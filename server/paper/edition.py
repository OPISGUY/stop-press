"""The daily edition: a PDF sized for the Kindle 4's 6" screen with every article the paper showed that day,
in full where the publisher's page could be read. The Kindle downloads it into its library each hour."""
import logging
import re
import threading
import time
from datetime import date, datetime
from pathlib import Path
from urllib.parse import urlparse

from fpdf import FPDF
from fpdf.enums import XPos, YPos

from paper import archive, sources
from paper.config import SETTINGS as S
from paper.draw import FONT_DIR

log = logging.getLogger(__name__)
PAGE_W, PAGE_H = 90, 120  # mm: the K4 screen is 600x800 px, so this fills it with no zoom
SECTIONS = [(k, S.sections[k]["title"]) for k in ("world", "home", "business", "tech")]
STATIC_DIR = FONT_DIR / "static"  # cut at image build time (see Dockerfile); recreated if missing

# (family, style) -> (variable file, weight)
STATIC = {
    ("Serif", ""): ("SourceSerif4.ttf", 400),
    ("Serif", "B"): ("SourceSerif4.ttf", 650),
    ("Serif", "I"): ("SourceSerif4-Italic.ttf", 400),
    ("Head", ""): ("PlayfairDisplay.ttf", 700),
    ("Head", "B"): ("PlayfairDisplay.ttf", 850),
    ("Sans", ""): ("Inter.ttf", 450),
    ("Sans", "B"): ("Inter.ttf", 700),
}


def _static_fonts() -> dict:
    """fpdf2 can't pick a weight from a variable font, so cut static instances once with fontTools."""
    from fontTools.ttLib import TTFont
    from fontTools.varLib import instancer

    STATIC_DIR.mkdir(parents=True, exist_ok=True)
    out = {}
    for (family, style), (fname, weight) in STATIC.items():
        path = STATIC_DIR / f"{family}{style or 'R'}.ttf"
        if not path.exists():
            vf = TTFont(str(FONT_DIR / fname))
            axes = {a.axisTag: a for a in vf["fvar"].axes}
            loc = {"wght": weight}
            if "opsz" in axes:
                loc["opsz"] = max(axes["opsz"].minValue, min(axes["opsz"].maxValue, 10))
            instancer.instantiateVariableFont(vf, loc, inplace=True)
            vf.save(str(path))
        out[(family, style)] = path
    return out


class Edition(FPDF):
    def __init__(self, day: date):
        super().__init__(unit="mm", format=(PAGE_W, PAGE_H))
        self.day = day
        self.set_margins(5, 6, 5)
        self.set_auto_page_break(True, margin=8)
        for (family, style), path in _static_fonts().items():
            self.add_font(family, style, str(path))
        self.add_font("Masthead", "", str(FONT_DIR / "UnifrakturMaguntia.ttf"))
        self.set_title(f"{S.masthead} — {day:%A %d %B %Y}")
        self.set_author(S.masthead)

    def footer(self):
        if self.page_no() == 1:
            return
        self.set_y(-6.5)
        self.set_font("Sans", "", 5.5)
        self.set_text_color(110)
        self.cell(0, 3, f"{S.masthead} · {self.day:%a %d %b}", align="L")
        self.set_x(self.l_margin)
        self.cell(0, 3, str(self.page_no()), align="R")
        self.set_text_color(0)

    def rule(self, w=0.3):
        self.set_line_width(w)
        self.line(self.l_margin, self.get_y(), PAGE_W - self.r_margin, self.get_y())


_JUNK = re.compile(
    r"^(-?\s*list (of )?\d+( of \d+)?|recommended stories|advertisement|sign up|follow .* on |related:|read more|"
    r"watch:|listen:|share this|copy link|image (source|caption)|getty images|skip past newsletter|"
    r"newsletter promotion|after newsletter promotion)",
    re.I,
)


def _paragraphs(text: str) -> list[str]:
    """Article paragraphs with the publishers' furniture (story lists, promos, captions) taken out."""
    out = []
    for p in (p.strip() for p in text.split("\n")):
        if p.startswith("[[img:"):
            out.append(p)  # a photo, kept in place (see archive._article_text)
            continue
        if not p or _JUNK.match(p) or (len(p) < 25 and not p.endswith((".", "?", "!", '"', "”"))):
            continue
        out.append(p)
    return out


def _toc(pdf: Edition, outline):
    pdf.set_x(pdf.l_margin)
    pdf.set_font("Sans", "B", 6.5)
    pdf.cell(0, 4, "IN THIS EDITION", new_x=XPos.LMARGIN, new_y=YPos.NEXT)
    pdf.rule(0.2)
    pdf.ln(1.2)
    for entry in outline:
        if entry.level == 0:
            pdf.ln(0.8)
            pdf.set_font("Sans", "B", 7)
            pdf.cell(64, 4, entry.name.upper(), link=pdf.add_link(page=entry.page_number))
            pdf.cell(0, 4, str(entry.page_number), align="R", new_x=XPos.LMARGIN, new_y=YPos.NEXT)
        else:
            pdf.set_font("Serif", "", 7.5)
            title = entry.name
            while pdf.get_string_width(title) > 70 and len(title) > 4:
                title = title[:-2].rstrip() + "…"
                title = title.replace("……", "…")
            pdf.cell(72, 3.9, title, link=pdf.add_link(page=entry.page_number))
            pdf.set_font("Sans", "", 6.5)
            pdf.cell(0, 3.9, str(entry.page_number), align="R", new_x=XPos.LMARGIN, new_y=YPos.NEXT)


def build(day: date | None = None) -> bytes:
    """Two passes: the first measures how many pages the contents take, the second reserves exactly that many
    (fpdf2 needs the count up front, and a wrong guess leaves blank pages)."""
    day = day or datetime.now(S.tz).date()
    data, toc_pages = _build(day, None)
    if toc_pages:
        # The measured span can include fpdf2's stray blank page, so try the neighbours too.
        for n in (toc_pages - 1, toc_pages, toc_pages + 1):
            if n < 1:
                continue
            try:
                return _build(day, n)[0]
            except Exception:
                continue
        log.warning("no exact contents length fitted; using the first pass")
    return data


def _build(day: date, toc_pages: int | None) -> tuple[bytes, int | None]:
    arts = archive.articles(day)
    pdf = Edition(day)

    # Cover
    pdf.add_page()
    pdf.set_font("Masthead", "", 25)
    pdf.cell(0, 12, S.masthead, align="C", new_x=XPos.LMARGIN, new_y=YPos.NEXT)
    pdf.rule(0.8)
    pdf.ln(0.8)
    pdf.rule(0.2)
    pdf.ln(1.2)
    pdf.set_font("Sans", "B", 6.5)
    printed = datetime.now(S.tz)
    stamp = f"{day:%A %d %B %Y}".upper()
    if day == printed.date():
        stamp += f" · PRINTED {printed:%H:%M}"
    pdf.cell(0, 3.5, stamp, align="C", new_x=XPos.LMARGIN, new_y=YPos.NEXT)
    pdf.cell(0, 3.5, f"{len(arts)} ARTICLES", align="C", new_x=XPos.LMARGIN, new_y=YPos.NEXT)
    pdf.ln(2)
    if day == printed.date():
        note = sources.editors_note()
        if note:
            pdf.set_font("Serif", "I", 8.5)
            pdf.multi_cell(0, 4.2, note, align="J", new_x=XPos.LMARGIN, new_y=YPos.NEXT)
            pdf.ln(2)

    if not arts:
        pdf.set_font("Serif", "I", 9)
        pdf.multi_cell(0, 5, "Nothing filed yet today. The archive fills as the paper refreshes.")
        return bytes(pdf.output()), None

    by_section = {key: [a for a in arts if a["section"] == key] for key, _ in SECTIONS}
    pdf.add_page()
    toc_start = pdf.page
    # 'pages' counts the current page. Pass 1 guesses 1 and lets fpdf2 add more.
    pdf.insert_toc_placeholder(_toc, pages=toc_pages or 1, allow_extra_pages=toc_pages is None)

    for key, title in SECTIONS:
        items = by_section[key]
        if not items:
            continue
        if pdf.get_y() > pdf.t_margin + 1:  # the contents placeholder already leaves us on a fresh page
            pdf.add_page()
        pdf.start_section(title, level=0)
        pdf.set_font("Head", "B", 17)
        pdf.cell(0, 9, title, new_x=XPos.LMARGIN, new_y=YPos.NEXT)
        pdf.rule(0.6)
        pdf.ln(2)
        for i, a in enumerate(items):
            if i and pdf.get_y() > PAGE_H - 40:
                pdf.add_page()
            elif i:
                pdf.ln(2)
                pdf.rule(0.15)
                pdf.ln(2)
            pdf.start_section(a["title"], level=1)
            when = ""
            if a.get("when"):
                when = datetime.fromisoformat(a["when"]).astimezone(S.tz).strftime(" · %H:%M")
            pdf.set_font("Sans", "B", 5.8)
            pdf.cell(0, 3, f"{a['source'].upper()}{when}", new_x=XPos.LMARGIN, new_y=YPos.NEXT)
            pdf.set_font("Head", "", 12)
            pdf.multi_cell(0, 5.2, a["title"], align="L", new_x=XPos.LMARGIN, new_y=YPos.NEXT)
            pdf.ln(0.8)
            paras = [x for x in _paragraphs(a.get("text") or "") if not x.startswith("[[img:")]
            if a.get("summary") and paras and not paras[0].startswith(a["summary"][:40]):
                pdf.set_font("Serif", "I", 8.5)
                pdf.multi_cell(0, 4, a["summary"], align="L", new_x=XPos.LMARGIN, new_y=YPos.NEXT)
                pdf.ln(0.8)
            pdf.set_font("Serif", "", 8.5)
            if paras:
                for para in paras:
                    pdf.multi_cell(0, 4, para, align="J", new_x=XPos.LMARGIN, new_y=YPos.NEXT)
                    pdf.ln(1.2)
            else:
                pdf.multi_cell(0, 4, a.get("summary") or "", align="L", new_x=XPos.LMARGIN, new_y=YPos.NEXT)
                pdf.ln(1)
                pdf.set_font("Sans", "", 6)
                pdf.set_text_color(90)
                pending = a.get("text") is None
                pdf.multi_cell(0, 3, "Full text still being fetched." if pending else
                               f"Full text not available here; it's at {urlparse(a['link']).netloc}.",
                               new_x=XPos.LMARGIN, new_y=YPos.NEXT)
                pdf.set_text_color(0)

    _markets_page(pdf)
    data = bytes(pdf.output())
    # After output the outline's page numbers include any contents pages fpdf2 inserted.
    first = min((e.page_number for e in pdf._outline if e.level == 0), default=None)
    return data, (first - toc_start) if first else None


def _markets_page(pdf: Edition):
    mk = sources.markets() or []
    if not mk:
        return
    pdf.add_page()
    pdf.start_section("Markets at press time", level=0)
    pdf.set_font("Head", "B", 17)
    pdf.cell(0, 9, "Markets", new_x=XPos.LMARGIN, new_y=YPos.NEXT)
    pdf.rule(0.6)
    pdf.ln(2)
    for x in mk:
        pdf.set_font("Sans", "B", 7.5)
        pdf.cell(34, 5.5, x["name"])
        pdf.set_font("Sans", "", 7.5)
        from paper.pages import price

        pdf.cell(24, 5.5, price(x), align="R")
        pdf.set_font("Sans", "B", 7.5)
        pdf.cell(0, 5.5, f"{x['change_pct']:+.1f}%", align="R", new_x=XPos.LMARGIN, new_y=YPos.NEXT)
        pdf.rule(0.1)


_cache: dict[str, tuple[float, bytes]] = {}
_build_lock = threading.Lock()


def refresh(day: date | None = None) -> bytes:
    """Build now and cache. Called by the background refresher so the Kindle never waits for a build."""
    day = day or datetime.now(S.tz).date()
    with _build_lock:
        data = build(day)
        _cache[day.isoformat()] = (time.time(), data)
        return data


def pdf_for(day: date | None = None, max_age_s: int = 600) -> bytes:
    """Serve the cached edition straight away (rebuilding behind it if stale); build only if there is none."""
    day = day or datetime.now(S.tz).date()
    hit = _cache.get(day.isoformat())
    if hit is None:
        return refresh(day)
    if time.time() - hit[0] > max_age_s and not _build_lock.locked():
        threading.Thread(target=refresh, args=(day,), daemon=True).start()
    return hit[1]
