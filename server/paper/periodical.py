"""The day's edition as a Kindle periodical (.mobi), the format Kindle newspapers use: on the Kindle 4 it gets
the section/article browser, the normal font-size menu and reflowing text, which the PDF can't.

We lay the day's archive out as an OEB book (OPF + NCX + one XHTML file per section and article, with the NCX
in the periodical > section > article shape the MOBI writer looks for) and let Calibre's ebook-convert write
the MOBI. Calibre is installed in the container image; without it this module just reports unavailable."""
import html
import logging
import shutil
import subprocess
import tempfile
import threading
import time
from datetime import date, datetime
from pathlib import Path

from PIL import Image, ImageDraw

from paper import archive, photos
from paper.config import SETTINGS as S
from paper.draw import font

log = logging.getLogger(__name__)
SECTIONS = [(k, S.sections[k]["title"]) for k in ("world", "home", "business", "tech")]
CONVERT = shutil.which("ebook-convert")

CSS = """
body { font-family: serif; }
h1 { font-size: 1.5em; line-height: 1.15; margin: 0 0 0.3em 0; }
h2 { font-size: 1.2em; margin: 0.8em 0 0.2em 0; }
.byline { font-family: sans-serif; font-size: 0.75em; text-transform: uppercase; color: #444; margin-bottom: 0.6em; }
.standfirst { font-style: italic; margin: 0.4em 0 0.8em 0; }
.photo { text-align: center; margin: 0.4em 0; }
.photo img { width: 100%; }
p { text-indent: 0; margin: 0 0 0.6em 0; text-align: justify; }
.toc p { text-align: left; margin-bottom: 0.4em; }
"""


def available() -> bool:
    return CONVERT is not None


def _e(s: str) -> str:
    return html.escape(s or "", quote=True)


def _xhtml(title: str, body: str) -> str:
    return (f'<?xml version="1.0" encoding="utf-8"?>\n<html xmlns="http://www.w3.org/1999/xhtml"><head>'
            f'<title>{_e(title)}</title><link rel="stylesheet" type="text/css" href="../style.css"/></head>'
            f"<body>{body}</body></html>")


def _masthead(path: Path):
    """The 600x100 nameplate Kindle shows at the top of a periodical."""
    img = Image.new("L", (600, 100), 255)
    d = ImageDraw.Draw(img)
    d.text((300, 50), S.masthead, font=font("masthead", 58), fill=0, anchor="mm")
    img.save(path, "GIF")


def _cover(path: Path):
    from paper.pages import front

    front().img.convert("RGB").save(path, "JPEG", quality=85)


def _photo(url: str | None, path: Path) -> bool:
    """Plain greyscale JPEG (the Kindle does its own dithering for book images)."""
    src = photos.load(url)
    if src is None:
        return False
    img = src.copy()
    img.thumbnail((600, 450))
    img.save(path, "JPEG", quality=80)
    return True


def build(day: date | None = None) -> bytes:
    from paper.edition import _paragraphs

    if not available():
        raise RuntimeError("ebook-convert not installed")
    day = day or datetime.now(S.tz).date()
    arts = archive.articles(day)
    with tempfile.TemporaryDirectory(prefix="periodical-") as tmp:
        root = Path(tmp)
        (root / "style.css").write_text(CSS, encoding="utf-8")
        _masthead(root / "masthead.gif")
        _cover(root / "cover.jpg")
        manifest = ['<item id="css" href="style.css" media-type="text/css"/>',
                    '<item id="masthead" href="masthead.gif" media-type="image/gif"/>',
                    '<item id="cover" href="cover.jpg" media-type="image/jpeg"/>',
                    '<item id="ncx" href="toc.ncx" media-type="application/x-dtbncx+xml"/>']
        spine, nav = [], []
        order = 1
        for si, (key, name) in enumerate(SECTIONS):
            items = [a for a in arts if a["section"] == key and (a.get("text") or a.get("summary"))]
            if not items:
                continue
            sdir = root / f"s{si}"
            sdir.mkdir()
            links, art_nav = [], []
            for ai, a in enumerate(items):
                fname = f"a{ai}.html"
                body = [f'<p class="byline">{_e(a["source"])}'
                        + (f" · {datetime.fromisoformat(a['when']).astimezone(S.tz):%H:%M}" if a.get("when") else "") + "</p>",
                        f"<h1>{_e(a['title'])}</h1>"]
                if _photo(a.get("image"), sdir / f"a{ai}.jpg"):
                    manifest.append(f'<item id="s{si}a{ai}img" href="s{si}/a{ai}.jpg" media-type="image/jpeg"/>')
                    body.append(f'<div class="photo"><img src="a{ai}.jpg" alt=""/></div>')
                paras = _paragraphs(a.get("text") or "")
                if a.get("summary") and not (paras and paras[0].startswith(a["summary"][:40])):
                    body.append(f'<p class="standfirst">{_e(a["summary"])}</p>')
                for pi, para in enumerate(paras):
                    if para.startswith(archive.IMG):
                        url = para[len(archive.IMG):-2]
                        if not archive.same_photo(url, a.get("image")) and _photo(url, sdir / f"a{ai}_{pi}.jpg"):
                            manifest.append(f'<item id="s{si}a{ai}i{pi}" href="s{si}/a{ai}_{pi}.jpg" media-type="image/jpeg"/>')
                            body.append(f'<div class="photo"><img src="a{ai}_{pi}.jpg" alt=""/></div>')
                    else:
                        body.append(f"<p>{_e(para)}</p>")
                (sdir / fname).write_text(_xhtml(a["title"], "".join(body)), encoding="utf-8")
                manifest.append(f'<item id="s{si}a{ai}" href="s{si}/{fname}" media-type="application/xhtml+xml"/>')
                links.append(f'<p><a href="{fname}">{_e(a["title"])}</a></p>')
                order += 1
                desc = _e((a.get("summary") or "")[:200])
                art_nav.append(
                    f'<navPoint class="article" id="n{si}_{ai}" playOrder="{order}"><navLabel><text>{_e(a["title"])}</text></navLabel>'
                    f'<content src="s{si}/{fname}"/><mbp:meta name="description">{desc}</mbp:meta>'
                    f'<mbp:meta name="author">{_e(a["source"])}</mbp:meta></navPoint>')
            (sdir / "index.html").write_text(_xhtml(name, f'<h2>{_e(name)}</h2><div class="toc">{"".join(links)}</div>'), encoding="utf-8")
            manifest.append(f'<item id="s{si}" href="s{si}/index.html" media-type="application/xhtml+xml"/>')
            spine.append(f'<itemref idref="s{si}"/>')
            spine += [f'<itemref idref="s{si}a{ai}"/>' for ai in range(len(items))]
            order += 1
            nav.append(f'<navPoint class="section" id="sec{si}" playOrder="{order}"><navLabel><text>{_e(name)}</text></navLabel>'
                       f'<content src="s{si}/index.html"/>{"".join(art_nav)}</navPoint>')
        if not spine:
            raise RuntimeError("nothing filed yet")
        title = f"{S.masthead} {day:%a %d %b %Y}"
        first = spine[0].split('"')[1]
        (root / "toc.ncx").write_text(
            '<?xml version="1.0" encoding="utf-8"?>\n'
            '<ncx xmlns="http://www.daisy.org/z3986/2005/ncx/" xmlns:mbp="http://mobipocket.com/ns/mbp" version="2005-1" xml:lang="en">'
            f'<head><meta name="dtb:uid" content="stop-press-{day}"/></head><docTitle><text>{_e(title)}</text></docTitle>'
            f'<navMap><navPoint class="periodical" id="periodical" playOrder="0"><navLabel><text>{_e(title)}</text></navLabel>'
            f'<content src="s{first[1:]}/index.html"/>{"".join(nav)}</navPoint></navMap></ncx>', encoding="utf-8")
        (root / "edition.opf").write_text(
            '<?xml version="1.0" encoding="utf-8"?>\n'
            '<package xmlns="http://www.idpf.org/2007/opf" version="2.0" unique-identifier="uid">'
            '<metadata xmlns:dc="http://purl.org/dc/elements/1.1/" xmlns:opf="http://www.idpf.org/2007/opf">'
            f'<dc:title>{_e(title)}</dc:title><dc:creator>{_e(S.masthead)}</dc:creator><dc:publisher>{_e(S.masthead)}</dc:publisher>'
            f'<dc:language>en-GB</dc:language><dc:date>{day.isoformat()}</dc:date><dc:identifier id="uid">stop-press-{day}</dc:identifier>'
            f'<meta name="calibre:publication_type" content="periodical:newspaper:{_e(S.masthead)}"/>'
            '<meta name="cover" content="cover"/></metadata>'
            f'<manifest>{"".join(manifest)}</manifest><spine toc="ncx">{"".join(spine)}</spine>'
            '<guide><reference type="masthead" title="Masthead" href="masthead.gif"/>'
            '<reference type="cover" title="Cover" href="cover.jpg"/></guide></package>', encoding="utf-8")
        out = root / "edition.mobi"
        r = subprocess.run([CONVERT, str(root / "edition.opf"), str(out), "--output-profile", "kindle",
                            "--mobi-file-type", "old", "--no-inline-toc", "--mobi-ignore-margins"],
                           capture_output=True, text=True, timeout=600, env={"QT_QPA_PLATFORM": "offscreen", "HOME": tmp, "PATH": "/usr/bin:/bin"})
        if r.returncode != 0 or not out.exists():
            raise RuntimeError(f"ebook-convert failed: {r.stdout[-800:]} {r.stderr[-800:]}")
        periodical = "periodical" in r.stdout.lower()
        log.info("edition.mobi built: %d articles, %d KB, periodical=%s", order, out.stat().st_size // 1024, periodical)
        return out.read_bytes()


_cache: dict[str, tuple[float, bytes]] = {}
_lock = threading.Lock()


def refresh(day: date | None = None) -> bytes:
    day = day or datetime.now(S.tz).date()
    with _lock:
        data = build(day)
        _cache[day.isoformat()] = (time.time(), data)
        return data


def mobi_for(day: date | None = None, max_age_s: int = 900) -> bytes:
    """Serve the cached edition straight away (rebuilding behind it if stale); build only if there is none."""
    day = day or datetime.now(S.tz).date()
    hit = _cache.get(day.isoformat())
    if hit is None:
        return refresh(day)
    if time.time() - hit[0] > max_age_s and not _lock.locked():
        threading.Thread(target=refresh, args=(day,), daemon=True).start()
    return hit[1]
