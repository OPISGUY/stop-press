"""The offline copy. The Kindle's Wi-Fi drops as soon as it leaves the room, so each hour it downloads the paper
as a "book": every section page and the full articles on them, one PNG per screen, in reading order. When it
can't reach us it flips through that instead.

/offline.tar holds p001.png, p002.png, ... plus chapters.txt, the page numbers where each section or article
starts (the 5-way up/down jumps between them). /bookpos says which book page matches the screen the reader is on
now, so losing Wi-Fi mid-article carries on from the same page."""
import io
import logging
import tarfile
import threading

from paper import archive
from paper.draw import H, W, Page, font, INK
from paper.pages import ORDER, PAGES, now

log = logging.getLogger(__name__)
MAX_ARTICLES = 24

_lock = threading.Lock()
_tar: bytes = b""
_starts: dict[str, int] = {}  # section name or article link -> first book page (1-based)
_lengths: dict[str, int] = {}  # article link -> pages


def _footer(p: Page, stamp: str):
    p.d.rectangle([0, H - 30, W, H], fill=255)
    p.rule(H - 28, w=1)
    p.text((W / 2, H - 20), f"SAVED COPY FROM {stamp}  ·  UP/DOWN NEXT STORY  ·  HOME RECONNECTS",
           font("sans", 11, 700), INK, anchor="ma")


def _png(p: Page) -> bytes:
    buf = io.BytesIO()
    p.img.save(buf, format="PNG", optimize=True)
    return buf.getvalue()


def build():
    """Render the book (about 15 seconds) and keep it for tar() and position()."""
    from paper import ui

    global _tar, _starts, _lengths
    stamp = now().strftime("%H:%M")
    shots: list[bytes] = []
    chapters: list[int] = []
    starts: dict[str, int] = {}
    lengths: dict[str, int] = {}
    taken = 0
    for name in ORDER:
        try:
            p = PAGES[name]()
        except Exception:
            log.warning("offline copy: %s page failed", name, exc_info=True)
            continue
        links = [item["link"] for _, item in p.hotspots]
        _footer(p, stamp)
        starts[name] = len(shots) + 1
        chapters.append(len(shots) + 1)
        shots.append(_png(p))
        # Then the stories on that page, so a section is followed by its own articles.
        for link in links:
            if link in starts or taken >= MAX_ARTICLES or archive.get(link) is None:
                continue
            first = len(shots) + 1
            n = 1
            i = 0
            while i < n:
                page, n = ui._article_page(link, i)
                _footer(page, stamp)
                shots.append(_png(page))
                i += 1
            starts[link], lengths[link] = first, n
            chapters.append(first)
            taken += 1

    buf = io.BytesIO()
    with tarfile.open(fileobj=buf, mode="w", format=tarfile.USTAR_FORMAT) as tar:
        def add(name: str, data: bytes):
            info = tarfile.TarInfo(name)
            info.size = len(data)
            tar.addfile(info, io.BytesIO(data))

        for i, data in enumerate(shots, 1):
            add(f"p{i:03d}.png", data)
        add("chapters.txt", ("\n".join(map(str, chapters)) + "\n").encode())
        add("pages.txt", f"{len(shots)}\n".encode())
    with _lock:
        _tar, _starts, _lengths = buf.getvalue(), starts, lengths
    log.info("offline copy: %d pages, %d articles, %d KB", len(shots), taken, len(_tar) // 1024)


def tar() -> bytes:
    """A fresh copy. Built on request rather than in the background, so /bookpos always describes the copy the
    Kindle actually has."""
    build()
    with _lock:
        return _tar


def position(state: dict) -> int:
    """The book page for the screen the reader is on (1 = the front page)."""
    with _lock:
        if state.get("view") == "article" and state.get("link") in _starts:
            link = state["link"]
            return _starts[link] + min(state.get("apage", 0), _lengths[link] - 1)
        return _starts.get(state.get("view"), 1)
