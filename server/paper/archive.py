"""The day's archive: every article the paper has shown, with its full text, so it can still be read after it
drops off the pages. One JSON document per day under <data_dir>/archive."""
import json
import logging
import os
import re
import threading
from concurrent.futures import ThreadPoolExecutor
from datetime import date, datetime
from html import unescape
from pathlib import Path

from paper.config import SETTINGS as S

log = logging.getLogger(__name__)
LOCAL = S.data_dir

_lock = threading.Lock()
_days: dict[str, dict] = {}  # "YYYY-MM-DD" -> {link: article}
_pool = ThreadPoolExecutor(max_workers=3)
_pending: set[str] = set()


def _key(day: date) -> str:
    return day.isoformat()


def _load(day: date) -> dict:
    k = _key(day)
    if k in _days:
        return _days[k]
    path = LOCAL / "archive" / f"{k}.json"
    data = json.loads(path.read_text("utf-8")) if path.exists() else {}
    _days[k] = data
    return data


def _save(day: date):
    k = _key(day)
    body = json.dumps(_days.get(k, {}), ensure_ascii=False, default=str)
    (LOCAL / "archive").mkdir(parents=True, exist_ok=True)
    (LOCAL / "archive" / f"{k}.json").write_text(body, "utf-8")


def record(section: str, items: list[dict]):
    """Add newly seen articles to today's archive and fetch their full text in the background."""
    today = datetime.now(S.tz).date()
    new = []
    with _lock:
        day = _load(today)
        for it in items:
            link = it.get("link")
            if not link or link in day:
                continue
            day[link] = {
                "title": it["title"], "summary": it.get("summary", ""), "source": it["source"], "section": section,
                "link": link, "when": it["when"].isoformat() if it.get("when") else None,
                "seen": datetime.now(S.tz).isoformat(), "text": None, "image": it.get("image"),
            }
            new.append(link)
        if new:
            _save(today)
    for link in new:
        if link not in _pending:
            _pending.add(link)
            _pool.submit(_fetch_text, today, link)


TEXT_VERSION = 2  # 2: photos kept in the text; older entries are re-fetched when opened
IMG = "[[img:"
_SKIP_IMG = re.compile(r"\.(svg|gif)(\?|$)|placeholder|logo|icon|avatar|sprite|1x1", re.I)


def _article_text(html: str, link: str) -> str:
    """The article as paragraphs, one per line, with its photos where they fall as '[[img:URL]]' lines."""
    from urllib.parse import urljoin

    import trafilatura
    from lxml import etree

    xml = trafilatura.extract(html, output_format="xml", include_images=True, include_comments=False,
                              include_tables=False, favor_precision=True)
    if not xml:
        return ""
    lines, photos = [], 0
    for el in etree.fromstring(xml.encode()).iter("p", "head", "item", "quote", "graphic"):
        if el.tag == "graphic":
            src = el.get("src") or ""
            if src and photos < 4 and not _SKIP_IMG.search(src):
                lines.append(f"{IMG}{urljoin(link, src)}]]")
                photos += 1
        elif not any(a.tag in ("p", "item", "quote") for a in el.iterancestors()):
            t = " ".join("".join(el.itertext()).split())
            if t:
                lines.append(t)
    return "\n".join(lines)


def same_photo(a: str | None, b: str | None) -> bool:
    """Publishers serve one photo at several sizes; compare by file name."""
    name = lambda u: (u or "").split("?")[0].rstrip("/").rsplit("/", 1)[-1]
    return bool(a and b) and name(a) == name(b)


def _fetch_text(day: date, link: str):
    try:
        import httpx
        import trafilatura

        html = httpx.get(link, timeout=20, follow_redirects=True, headers={"User-Agent": "Mozilla/5.0 (Stop Press e-ink newspaper)"}).text
        text = _article_text(html, link)
        m = re.search(r'<meta[^>]+property=["\']og:image["\'][^>]+content=["\']([^"\']+)', html)
        page_image = unescape(m.group(1)) if m else None
    except Exception:
        log.warning("full text failed for %s", link, exc_info=True)
        text, page_image = "", None
    with _lock:
        art = _load(day).get(link)
        if art is not None:
            art["text"] = text
            art["v"] = TEXT_VERSION
            if not art.get("image") and page_image:
                art["image"] = page_image
            _save(day)
    if art is not None and art.get("image"):
        from paper import photos

        photos.load(art["image"])  # so opening the article doesn't wait for it
    _pending.discard(link)


def get(link: str) -> dict | None:
    """One article by link (today, then the previous two days), fetching its full text now if still missing."""
    from datetime import timedelta

    today = datetime.now(S.tz).date()
    for back in range(3):
        day = today - timedelta(days=back)
        with _lock:
            art = _load(day).get(link)
        if art is not None:
            if not art.get("text") or art.get("v", 1) < TEXT_VERSION:
                _fetch_text(day, link)
                with _lock:
                    art = _load(day).get(link)
            return art
    return None


def articles(day: date | None = None) -> list[dict]:
    day = day or datetime.now(S.tz).date()
    with _lock:
        return sorted(_load(day).values(), key=lambda a: a.get("when") or a["seen"], reverse=True)


def count_today() -> int:
    with _lock:
        return len(_load(datetime.now(S.tz).date()))
