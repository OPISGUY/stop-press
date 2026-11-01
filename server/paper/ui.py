"""Interactive mode. The Kindle sends each button press (/ui.png?k=...) and gets back the screen to show; all
the state lives here, so the Kindle side stays a dumb button-forwarder. There is one reader, so one state.

With peek=True a press is worked out and drawn but not kept: the Kindle uses that to fetch the likely next
screens while you read, so a flip only has to draw. What you've read, where you got to in each article and
your text size are saved beside the archive, so they survive a restart of the app."""
import copy
import json
import logging
import threading
import time
from datetime import datetime

from PIL import ImageDraw

from paper import archive, photos
from paper.config import SETTINGS as S
from paper.draw import DARK, INK, M, MID, READ, H, W, Page, font
from paper.pages import ORDER, PAGES, TITLES, ago, header, now, page_for

log = logging.getLogger(__name__)
VIEWS = ORDER + ["index"]
TITLES = {**TITLES, "index": "Today's Articles"}
INDEX_PER_PAGE = 8
# Article body sizes (font px, leading); the 5-way left/right steps through them
SIZES = [(16, 23), (18, 26), (21, 30), (24, 34)]

_lock = threading.Lock()
_state = {"view": "front", "sel": 0, "ipage": 0, "link": None, "apage": 0, "back": "front"}
_reader = {"size": 1, "progress": {}, "last": None}  # saved; READ (in draw.py) is saved with it
_layouts: dict[tuple, tuple] = {}  # (link, size) -> (article, paginated layout)
_renders: dict[tuple, tuple] = {}  # (view, ipage) -> (time, Page) without the selection drawn
RENDER_TTL = 60


# --- saved reader state -------------------------------------------------------------------------

def _local():
    return archive.LOCAL / "reader.json"


def load_reader():
    try:
        if not _local().exists():
            return
        data = json.loads(_local().read_text())
        _reader.update(size=data.get("size", 1), progress=data.get("progress", {}), last=data.get("last"))
        READ.update(data.get("read", []))
    except Exception:
        log.info("no saved reader state yet", exc_info=True)


_dirty = threading.Event()


def _saver():
    while True:
        _dirty.wait()
        time.sleep(5)  # gather a run of page turns into one write
        _dirty.clear()
        with _lock:
            body = json.dumps({**_reader, "read": sorted(READ)[-2000:]})
        try:
            archive.LOCAL.mkdir(parents=True, exist_ok=True)
            _local().write_text(body)
        except Exception:
            log.warning("saving reader state failed", exc_info=True)


threading.Thread(target=_saver, daemon=True, name="reader-saver").start()


# --- helpers ------------------------------------------------------------------------------------

def _hint(p: Page, text: str):
    """Replace the footer with the controls for this screen."""
    p.d.rectangle([0, H - 30, W, H], fill=255)
    p.rule(H - 28, w=1)
    p.text((W / 2, H - 20), text, font("sans", 11, 600), INK, anchor="ma")


def _copy(p: Page) -> Page:
    q = Page()
    q.img = p.img.copy()
    q.d = ImageDraw.Draw(q.img)
    q.hotspots = list(p.hotspots)
    return q


def _continue_item() -> tuple[dict, int, int] | None:
    """The last article left part-way through: (article, page reached, pages)."""
    link = _reader.get("last")
    if not link:
        return None
    a = archive.get(link)
    if a is None:
        return None
    pages = _layout(a)[1]
    at = _reader["progress"].get(link, 0)
    if at >= len(pages) - 1:
        return None
    return a, at, len(pages)


def _index_chunks(arts: list, cont: bool) -> list[list]:
    first = INDEX_PER_PAGE - 1 if cont else INDEX_PER_PAGE
    chunks = [arts[:first]]
    for i in range(first, len(arts), INDEX_PER_PAGE):
        chunks.append(arts[i:i + INDEX_PER_PAGE])
    return chunks


def _index_page(ipage: int) -> tuple[Page, int]:
    arts = archive.articles()
    cont = _continue_item()
    chunks = _index_chunks(arts, cont is not None)
    pages = len(chunks)
    ipage = max(0, min(ipage, pages - 1))
    p = Page()
    y = header(p, "Today's Articles")
    p.text((M, y), "Today's Articles", font("head", 30, 800))
    unread = sum(1 for a in arts if a["link"] not in READ)
    p.text((W - M, y + 14), f"{len(arts)} filed · {unread} unread · page {ipage + 1} of {pages}", font("italic", 14, 400), DARK, anchor="ra")
    p.rule(y + 42)
    y += 52
    if ipage == 0 and cont:
        a, at, n = cont
        y0 = y
        p.d.rectangle([M - 4, y - 4, W - M + 4, y + 50], fill=232)
        p.text((M + 4, y), f"CONTINUE READING · PAGE {at + 1} OF {n}", font("sans", 11, 700), INK)
        p.para(M + 4, y + 16, W - 2 * M - 8, a["title"], font("head", 17, 700), leading=21, max_lines=1)
        p.hot((M, y0, W - M, y + 46), a, tick=False)
        y += 66
    if not arts:
        p.text((M, y), "Nothing filed yet today.", font("italic", 16, 400), DARK)
    for a in chunks[ipage]:
        y0 = y
        when = datetime.fromisoformat(a["when"]) if a.get("when") else None
        read = a["link"] in READ
        p.text((M, y), f"{a['section'].upper()} · {a['source']}" + (f" · {ago(when)}" if when else "") + (" · READ" if read else ""),
               font("sans", 11, 700), MID)
        y = p.para(M, y + 15, W - 2 * M, a["title"], font("head", 17, 600 if read else 700), leading=21, max_lines=2,
                   fill=DARK if read else INK)
        p.hot((M, y0, W - M, y), a)
        y += 14
    return p, pages


def _layout(a: dict) -> tuple[dict, list]:
    key = (a["link"], _reader["size"])
    if key not in _layouts:
        _layouts[key] = (a, _article_layout(a, *SIZES[_reader["size"]]))
    return _layouts[key]


def _article_layout(a: dict, size: int = 18, leading: int = 26) -> list:
    """Split an article into 600x800 screens: a list of pages, each a list of (kind, text, font, leading, colour)."""
    text = (a.get("text") or "").strip()
    from paper.edition import _paragraphs

    paras = _paragraphs(text) or [a.get("summary") or "The full text of this one couldn't be fetched."]
    if paras and paras[0].startswith(archive.IMG) and not photos.available(a.get("image")):
        a = {**a, "image": paras.pop(0)[len(archive.IMG):-2]}  # no feed photo: the article's first one leads
    scratch = Page()
    bw = W - 2 * M
    blocks = []
    blocks.append(("kicker", f"{a['section'].upper()} · {a['source'].upper()}", None, 18, MID))
    hf = font("head", 26, 800)
    for line in scratch.wrap(a["title"], hf, bw):
        blocks.append(("line", line, hf, 31, INK))
    blocks.append(("gap", "", None, 8, INK))
    if photos.available(a.get("image")):
        blocks.append(("image", a["image"], None, 330, INK))
    if a.get("summary") and not paras[0].startswith(a["summary"][:40]):
        sf = font("italic", 17, 400)
        for line in scratch.wrap(a["summary"], sf, bw):
            blocks.append(("line", line, sf, 23, DARK))
        blocks.append(("gap", "", None, 10, INK))
    blocks.append(("rule", "", None, 12, INK))
    bf = font("body", size, 400)
    for para in paras:
        if para.startswith(archive.IMG):
            url = para[len(archive.IMG):-2]
            src = photos.load(url)
            if src is None or archive.same_photo(url, a.get("image")) or src.width < 300:
                continue
            h = max(160, min(420, round(bw * src.height / src.width)))
            blocks.append(("image", url, None, h + 16, INK))
            continue
        for line in scratch.wrap(para, bf, bw):
            blocks.append(("body", line, bf, leading, INK))
        blocks.append(("gap", "", None, round(leading * 0.4), INK))

    top, bottom = 70, H - 38
    pages, cur, y = [], [], top
    for b in blocks:
        if y + b[3] > bottom and cur:
            pages.append(cur)
            cur, y = [], top
            if b[0] == "gap":
                continue
        cur.append(b)
        y += b[3]
    if cur:
        pages.append(cur)
    return pages


def _article_page(link: str, apage: int) -> tuple[Page, int]:
    a = archive.get(link)
    if a is None:
        p = Page()
        header(p, "Article")
        p.text((M, 90), "That article has gone from the archive.", font("italic", 18, 400))
        return p, 1
    a, pages = _layout(a)
    apage = max(0, min(apage, len(pages) - 1))
    p = Page()
    header(p, f"{a['source']} · {apage + 1} of {len(pages)}")
    # reading progress along the masthead rule
    p.d.rectangle([M, 56, M + (W - 2 * M) * (apage + 1) / len(pages), 58], fill=INK)
    y = 70
    for kind, text, f, lead, colour in pages[apage]:
        if kind == "kicker":
            p.kicker(M, y, text, fill=colour)
        elif kind == "rule":
            p.rule(y + 2)
        elif kind in ("body", "line"):
            p.text((M, y), text, f, colour)
        elif kind == "image":
            p.photo((M, y, W - M, y + lead - 16), text)
        y += lead
    if apage == len(pages) - 1:
        p.text((W / 2, min(y + 16, H - 52)), "· END ·", font("sans", 11, 700), MID, anchor="ma")
    return p, len(pages)


def _paper_view(view: str, ipage: int) -> Page:
    """The page as drawn for this view, from a short-lived cache (moving the selection only redraws the frame)."""
    key = (view, ipage if view == "index" else 0)
    hit = _renders.get(key)
    if hit and time.time() - hit[0] < RENDER_TTL:
        return hit[1]
    p = _index_page(ipage)[0] if view == "index" else PAGES[view]()
    _renders[key] = (time.time(), p)
    return p


def _mark_read(link: str):
    if link not in READ:
        READ.add(link)
        _renders.clear()  # headlines carry a tick once read


# --- the state machine --------------------------------------------------------------------------

def _tap_key(x: int, y: int) -> str:
    """A touch screen has no keys, so a tap becomes one: a headline opens, the left third goes back a page, the
    top strip opens the article list (or leaves an article), and anywhere else turns the page."""
    s = _state
    if s["view"] == "article":
        return "back" if y < 60 else "prev" if x < W / 3 else "next"
    if s["sel"] < 0:
        s["sel"] = 0
    for i, (box, _) in enumerate(_paper_view(s["view"], s["ipage"]).hotspots):
        if box[0] <= x <= box[2] and box[1] - 6 <= y <= box[3] + 6:
            s["sel"] = i
            return "select"
    return "menu" if y < 60 else "prev" if x < W / 3 else "next"


def handle(key: str, peek: bool = False, x: int = 0, y: int = 0) -> tuple[Page, str]:
    """Apply one button press (or a tap at x, y) and return (screen, view name). With peek, the state is left as
    it was."""
    with _lock:
        saved = copy.deepcopy(_state) if peek else None
        try:
            if key == "tap":
                key = _tap_key(x, y)
            return _handle(key, peek)
        finally:
            if peek:
                _state.clear()
                _state.update(saved)


def _handle(key: str, peek: bool) -> tuple[Page, str]:
    s = _state
    if key == "auto":
        s.update(view=page_for(now()), sel=-1, ipage=0)
        return PAGES[s["view"]](), s["view"]

    if s["view"] == "article":
        _, n = _article_page(s["link"], s["apage"])
        if key in ("next", "down"):
            if s["apage"] < n - 1:
                s["apage"] += 1
            else:
                s["view"] = s["back"]
        elif key in ("prev", "up"):
            if s["apage"] > 0:
                s["apage"] -= 1
            else:
                s["view"] = s["back"]
        elif key in ("left", "right") and not peek:
            # text size: keep roughly the same place in the article
            frac = s["apage"] / max(n - 1, 1)
            _reader["size"] = max(0, min(len(SIZES) - 1, _reader["size"] + (1 if key == "right" else -1)))
            _, n2 = _article_page(s["link"], 0)
            s["apage"] = round(frac * (n2 - 1))
        elif key in ("back", "select"):
            s["view"] = s["back"]
        elif key == "home":
            s.update(view="front", sel=0)
        elif key == "menu":
            s.update(view="index", sel=0, ipage=0)
        if not peek:
            _reader["progress"][s["link"]] = s["apage"]
            _reader["last"] = s["link"]
            _renders.pop(("index", 0), None)  # its 'continue reading' line has moved on
            _dirty.set()
    else:
        if s["sel"] < 0:
            s["sel"] = 0
        if key in ("next", "right"):
            if s["view"] == "index":
                _, pages = _index_page(s["ipage"])
                if s["ipage"] < pages - 1:
                    s.update(ipage=s["ipage"] + 1, sel=0)
                else:
                    s.update(view="front", sel=0, ipage=0)
            else:
                s.update(view=VIEWS[(VIEWS.index(s["view"]) + 1) % len(VIEWS)], sel=0, ipage=0)
        elif key in ("prev", "left"):
            if s["view"] == "index" and s["ipage"] > 0:
                s.update(ipage=s["ipage"] - 1, sel=0)
            else:
                nv = VIEWS[(VIEWS.index(s["view"]) - 1) % len(VIEWS)]
                s.update(view=nv, sel=0)
                if nv == "index":
                    s["ipage"] = max(0, _index_page(10_000)[1] - 1)
        elif key in ("up", "down"):
            n = len(_paper_view(s["view"], s["ipage"]).hotspots)
            if n:
                s["sel"] = (s["sel"] + (1 if key == "down" else -1)) % n
        elif key == "select":
            spots = _paper_view(s["view"], s["ipage"]).hotspots
            if spots:
                item = spots[min(s["sel"], len(spots) - 1)][1]
                a = archive.get(item["link"])
                start = 0
                if a is not None:
                    n = len(_layout(a)[1])
                    at = _reader["progress"].get(item["link"], 0)
                    start = at if at < n - 1 else 0  # resume where you left off, unless you'd finished it
                s.update(back=s["view"], view="article", link=item["link"], apage=start)
                if not peek:
                    _mark_read(item["link"])
                    _reader["last"] = item["link"]
                    _dirty.set()
        elif key == "home":
            s.update(view="front", sel=0)
        elif key in ("menu", "back"):
            s.update(view="index", sel=0, ipage=0)

    # Render the resulting screen
    if s["view"] == "article":
        p, n = _article_page(s["link"], s["apage"])
        _hint(p, "PAGE KEYS TURN  ·  LEFT/RIGHT TEXT SIZE  ·  BACK RETURNS  ·  HOME FRONT")
        return p, "article"
    p = _copy(_paper_view(s["view"], s["ipage"]))
    if p.hotspots:
        s["sel"] = min(s["sel"], len(p.hotspots) - 1)
        p.highlight(p.hotspots[s["sel"]][0])
    _hint(p, "PAGE KEYS FLIP  ·  5-WAY CHOOSE  ·  PRESS READS  ·  MENU ALL ARTICLES")
    return p, s["view"]
