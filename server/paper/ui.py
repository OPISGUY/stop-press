"""Interactive mode. The Kindle sends each button press (/ui.png?k=...) and gets back the screen to show; all
the state lives here, so the Kindle side stays a dumb button-forwarder. There is one reader, so one state.

With peek=True a press is worked out and drawn but not kept: the Kindle uses that to fetch the likely next
screens while you read, so a flip only has to draw. What you've read, where you got to in each article and
your text size are saved beside the archive, so they survive a restart of the app."""
import copy
from collections import OrderedDict
import json
import logging
import threading
import time
from datetime import datetime

from PIL import ImageDraw

from paper import archive, photos
from paper.config import SETTINGS as S
from paper.draw import DARK, INK, M, MID, READ, H, W, Page, ScaledDraw, current_device, font
from paper.pages import ORDER, PAGES, TITLES, ago, header, markets, markets_pages, now, page_for, stock

log = logging.getLogger(__name__)
VIEWS = ORDER + ["index"]
TITLES = {**TITLES, "index": "Today's Articles"}
INDEX_PER_PAGE = 8
# Article body sizes (font px, leading); the 5-way left/right steps through them
SIZES = [(16, 23), (18, 26), (21, 30), (24, 34)]

_lock = threading.Lock()
_FRESH = {"view": "front", "sel": 0, "ipage": 0, "link": None, "apage": 0, "back": "front"}
# One reading state per reader. The Kindle has no id (""); each browser on the public demo sends its own.
_sessions: "OrderedDict[str, dict]" = OrderedDict({"": dict(_FRESH)})
MAX_SESSIONS = 500
_state = _sessions[""]  # the state handle() is working on
_touch = False  # and whether its reader taps rather than presses keys
_mode = ""      # "live" or "paper" on a touch Kindle: drawn as a switch at the top of the screen
_fx: set = set()  # what the Kindle should do with this screen besides draw it: "fast" (only a highlight changed,
                  # so the quick black-and-white update will do) and "mode" (switch live/paper mode)
MODE_BOX = (258, 6, 342, 28)  # the live/paper switch, top centre (page units)
_reader = {"size": 1, "progress": {}, "last": None}  # saved; READ (in draw.py) is saved with it
_layouts: dict[tuple, tuple] = {}  # (link, size) -> (article, paginated layout)
_renders: dict[tuple, tuple] = {}  # (view, ipage, device) -> (time, Page) without the selection drawn
RENDER_TTL = 60
LIVE_TTL = 10  # how stale a page may be in live mode


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


# Touch screens get a navigation bar instead of the hint: an arrow at each end (previous / next page, always in
# the same place, so the Kindle can act on them without asking) and tabs between them.
NAV_H, NAV_ARROW = 40, 70
NAV_LABEL = {"front": "FRONT", "world": "WORLD", "markets": "MARKETS", "stats": "NUMBERS", "day": "DAY", "index": "ALL"}


def _nav_cells(s: dict) -> list[tuple[str, str, bool]]:
    """(label, key, current?) for the tabs of the screen in state s."""
    if s["view"] in ("article", "sheet"):
        return [("BACK", "back", False), ("FRONT PAGE", "home", False), ("ALL ARTICLES", "goto:index", False)]
    if s["view"] == "stock":
        return [("MARKETS", "back", False), ("CHART RANGE", "range", False), ("FRONT PAGE", "home", False)]
    return [(NAV_LABEL.get(v, TITLES.get(v, v).upper()), f"goto:{v}", v == s["view"]) for v in VIEWS]


def _nav_layout(s: dict) -> tuple[list[tuple[float, float, str, str, bool]], object]:
    """The tabs as (x0, x1, label, key, current?) and their font: each as wide as its label plus an equal share
    of the room left, in the largest size at which they all fit."""
    cells = _nav_cells(s)
    room = W - 2 * NAV_ARROW
    for size in (12, 11, 10, 9):
        f = font("sans", size, 700)
        widths = [f.getlength(label) for label, _, _ in cells]
        pad = (room - sum(widths)) / len(cells)
        if pad >= 10:
            break
    out, x = [], float(NAV_ARROW)
    for (label, key, current), w in zip(cells, widths):
        out.append((x, x + w + pad, label, key, current))
        x += w + pad
    return out, f


def _nav(p: Page, s: dict):
    top = H - NAV_H
    p.d.rectangle([0, top - 2, W, H], fill=255)
    p.rule(top, 0, W, w=2)
    mid = top + NAV_H / 2 + 1
    p.triangle_h(NAV_ARROW / 2, mid, 14, left=True)
    p.triangle_h(W - NAV_ARROW / 2, mid, 14, left=False)
    cells, f = _nav_layout(s)
    for x0, x1, label, _, current in cells:
        if current:
            p.d.rectangle([x0 + 2, top + 5, x1 - 2, H - 4], fill=INK)
        else:
            p.vrule(x0, top + 8, H - 8, MID)
        p.text(((x0 + x1) / 2, mid), label, f, 255 if current else INK, anchor="mm")
    p.vrule(W - NAV_ARROW, top + 8, H - 8, MID)


def _nav_key(s: dict, x: int) -> str:
    """The key for a tap in the navigation bar at x."""
    if x < NAV_ARROW:
        return "prev"
    if x >= W - NAV_ARROW:
        return "next"
    cells, _ = _nav_layout(s)
    for x0, x1, _, key, _ in cells:
        if x < x1:
            return key
    return cells[-1][3]


def _foot(p: Page, s: dict, keys_hint: str):
    """The foot of the screen: the navigation bar on a touch screen, else the keys that work here."""
    if _touch:
        _nav(p, s)
        if _mode:  # the live/paper switch
            x0, y0, x1, y1 = MODE_BOX
            live = _mode == "live"
            p.d.rectangle([x0, y0, x1, y1], fill=INK if live else 255, outline=INK, width=1)
            p.text(((x0 + x1) / 2, (y0 + y1) / 2), "LIVE" if live else "PAPER", font("sans", 11, 700),
                   255 if live else INK, anchor="mm")
    else:
        _hint(p, keys_hint)


def _sheet_page(sh: dict) -> Page:
    """A detail sheet: a kicker, a title and a text, on a screen of its own."""
    p = Page()
    y = header(p, sh.get("section") or "Detail")
    y = p.kicker(M, y + 4, sh.get("kicker") or "")
    if sh.get("title"):
        y = p.para(M, y + 2, W - 2 * M, sh["title"], font("head", 24, 800), leading=29, max_lines=4) + 8
    p.rule(y, fill=MID)
    y += 12
    f = font("body", 16, 400)
    for para in (sh.get("body") or "").split("\n"):
        if not para.strip():
            continue
        room = int((H - 52 - y) // 23)
        if room < 1:
            break
        y = p.para(M, y, W - 2 * M, para.strip(), f, leading=23, max_lines=room) + 9
    return p


def _copy(p: Page) -> Page:
    q = Page(p.dev)
    q.img = p.img.copy()
    q.d = ImageDraw.Draw(q.img) if isinstance(p.d, ImageDraw.ImageDraw) else ScaledDraw(q.img, p.dev)
    q.hotspots = list(p.hotspots)
    q.photos = list(p.photos)
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


PAGED = ("index", "markets")


def _pages_of(view: str) -> int:
    return _index_page(10_000)[1] if view == "index" else markets_pages() if view == "markets" else 1


def _paper_view(view: str, ipage: int) -> Page:
    """The page as drawn for this view, from a short-lived cache (moving the selection only redraws the frame)."""
    key = (view, ipage if view in PAGED else 0, current_device().name)  # each screen size its own render
    hit = _renders.get(key)
    if hit and time.time() - hit[0] < RENDER_TTL:
        return hit[1]
    p = _index_page(ipage)[0] if view == "index" else markets(ipage) if view == "markets" else PAGES[view]()
    _renders[key] = (time.time(), p)
    return p


def _mark_read(link: str):
    if link not in READ:
        READ.add(link)
        _renders.clear()  # headlines carry a tick once read


# --- the state machine --------------------------------------------------------------------------

def _tap_key(x: int, y: int) -> str:
    """A touch screen has no keys, so a tap becomes one. The navigation bar's arrows and tabs do what they say; a
    headline or a market row opens; the top strip goes to the front page (or leaves an article or a stock). In an
    article the left third turns back and the rest turns on, as in a Kindle book. Elsewhere a stray tap does
    nothing, as on a tablet."""
    s = _state
    armed = s.pop("armed", None)
    if _mode and MODE_BOX[0] - 10 <= x <= MODE_BOX[2] + 10 and y <= MODE_BOX[3] + 12:
        _fx.add("mode")
        return "none"
    if y >= H - NAV_H - 4:
        key = _nav_key(s, x)
        if key.startswith("goto:"):
            if key[5:] in VIEWS:
                s.update(view=key[5:], sel=0, ipage=0)
            return "none"
        return key
    if s["view"] == "article":
        return "back" if y < 60 else "prev" if x < W / 3 else "next"
    if s["view"] == "sheet":
        return "back" if y < 60 else "none"
    page = stock(s["symbol"], s.get("range", "1M")) if s["view"] == "stock" else _paper_view(s["view"], s["ipage"])
    if s["view"] != "stock":
        if s["sel"] < 0:
            s["sel"] = 0
        # The first tap on a headline or a row frames it; a second tap on the same one opens it. E-ink turns
        # the page too slowly to see what a finger hit otherwise.
        for i, (box, _) in enumerate(page.hotspots):
            if box[0] <= x <= box[2] and box[1] - 6 <= y <= box[3] + 6:
                s["sel"] = i
                if armed == ["hot", i]:
                    return "select"
                s["armed"] = ["hot", i]
                _fx.add("fast")
                return "none"
    for box, action in page.taps:  # section titles, front-page market rows, the masthead, chart tabs
        if box[0] <= x <= box[2] and box[1] <= y <= box[3]:
            kind, _, what = action.partition(":")
            if kind != "range" and action != "view:front" and armed != ["tap", action]:
                s["armed"] = ["tap", action]  # (chart tabs and the masthead are plain buttons: one tap)
                _fx.add("fast")
                return "none"
            if kind == "view" and what in VIEWS:
                s.update(view=what, sel=0, ipage=0)
            elif kind == "stock":
                s.update(view="stock", symbol=what, range=s.get("range") or "1M")
            elif kind == "range":
                s["range"] = what
            return "none"
    if s["view"] == "stock":
        return "back" if y < 60 else "range" if 180 <= y <= 460 else "none"
    return "home" if y < 60 else "none"


def state_for(sid: str = "") -> dict:
    """The reading state for one reader, starting at the front page; the least recently used are forgotten."""
    if sid not in _sessions:
        _sessions[sid] = dict(_FRESH)
        while len(_sessions) > MAX_SESSIONS:
            _sessions.popitem(last=False)
    _sessions.move_to_end(sid)
    return _sessions[sid]


def handle(key: str, peek: bool = False, x: int = 0, y: int = 0, sid: str = "", touch: bool = False,
           mode: str = "") -> tuple[Page, str]:
    """Apply one button press (or a tap at x, y) for reader sid and return (screen, view name). With peek, the
    state is left as it was. touch gives the screen a navigation bar and touch targets; mode ("live"/"paper")
    draws the mode switch. The returned page's fx says what else the Kindle should do (see _fx)."""
    global _state, _touch, _mode
    with _lock:
        _state = state_for(sid)
        _touch, _mode = touch, mode
        _fx.clear()
        saved = copy.deepcopy(_state) if peek else None
        try:
            if key == "tap":
                key = _tap_key(x, y)
            elif key != "none":
                _state.pop("armed", None)  # a highlight waits only for the next tap
            p, view = _handle(key, peek)
            p.fx = set(_fx)
            return p, view
        finally:
            if peek:
                _state.clear()
                _state.update(saved)


def live_frame(touch: bool = False, mode: str = "") -> tuple[Page, str]:
    """The Kindle's screen as it stands now (for /live.tar): its page, redrawn if more than 10 seconds old."""
    with _lock:
        view = _sessions[""].get("view")
        for key, (at, _) in list(_renders.items()):
            if key[0] == view and time.time() - at > LIVE_TTL:
                _renders.pop(key, None)
    return handle("none", peek=True, touch=touch, mode=mode)


def _handle(key: str, peek: bool) -> tuple[Page, str]:
    s = _state
    if key == "auto":
        s.update(view=page_for(now()), sel=-1, ipage=0)
        p = PAGES[s["view"]]()
        if _touch:  # the sleeping page wakes straight into taps, so it carries the bar too
            p = _copy(p)
            _foot(p, s, "")
        return p, s["view"]

    if s["view"] == "stock":
        from paper import sources, watchlist

        syms = [i["symbol"] for i in watchlist.items()]
        ranges = list(sources.RANGES)
        if key == "range":  # a tap on the chart: the next range, round again after the last
            at = ranges.index(s.get("range", "1M")) if s.get("range") in ranges else 0
            s["range"] = ranges[(at + 1) % len(ranges)]
        elif key in ("next", "down", "prev", "up") and s["symbol"] in syms:
            s["symbol"] = syms[(syms.index(s["symbol"]) + (1 if key in ("next", "down") else -1)) % len(syms)]
        elif key in ("left", "right"):
            at = ranges.index(s.get("range", "1M")) if s.get("range") in ranges else 1
            s["range"] = ranges[max(0, min(len(ranges) - 1, at + (1 if key == "right" else -1)))]
        elif key in ("back", "select"):
            s["view"] = "markets"
        elif key == "home":
            s.update(view="front", sel=0)
        elif key == "menu":
            s.update(view="index", sel=0, ipage=0)
    elif s["view"] == "sheet":
        if key in ("back", "select", "prev", "up", "left"):
            s["view"] = s.get("back") or "front"
        elif key == "home":
            s.update(view="front", sel=0)
        elif key == "menu":
            s.update(view="index", sel=0, ipage=0)
    elif s["view"] == "article":
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
        elif key in ("left", "right") and not peek and not S.demo:  # text size is shared, so fixed on the demo
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
            for k in [k for k in _renders if k[0] == "index"]:  # its 'continue reading' line has moved on
                _renders.pop(k, None)
            _dirty.set()
    else:
        if s["sel"] < 0:
            s["sel"] = 0
        if key in ("next", "right"):
            if s["view"] in PAGED and s["ipage"] < _pages_of(s["view"]) - 1:
                s.update(ipage=s["ipage"] + 1, sel=0)
            else:
                s.update(view=VIEWS[(VIEWS.index(s["view"]) + 1) % len(VIEWS)], sel=0, ipage=0)
        elif key in ("prev", "left"):
            if s["view"] in PAGED and s["ipage"] > 0:
                s.update(ipage=s["ipage"] - 1, sel=0)
            else:
                nv = VIEWS[(VIEWS.index(s["view"]) - 1) % len(VIEWS)]
                s.update(view=nv, sel=0, ipage=0)
                if nv in PAGED:
                    s["ipage"] = max(0, _pages_of(nv) - 1)
        elif key in ("up", "down"):
            n = len(_paper_view(s["view"], s["ipage"]).hotspots)
            if n:
                s["sel"] = (s["sel"] + (1 if key == "down" else -1)) % n
        elif key == "select":
            spots = _paper_view(s["view"], s["ipage"]).hotspots
            if spots:
                item = spots[min(s["sel"], len(spots) - 1)][1]
                if item["link"].startswith("stock:"):
                    s.update(view="stock", symbol=item["link"][6:], range=s.get("range") or "1M")
                    item = None
                elif item["link"].startswith("sheet:"):
                    sheet = _paper_view(s["view"], s["ipage"]).sheets.get(item["link"][6:])
                    if sheet:
                        s.update(back=s["view"], view="sheet", sheet=sheet)
                    item = None
            if spots and item is not None:
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
    if s["view"] == "stock":
        p = stock(s["symbol"], s.get("range", "1M"))
        _foot(p, s, "PAGE KEYS NEXT/PREVIOUS  ·  LEFT/RIGHT CHART RANGE  ·  BACK TO MARKETS")
        return p, "stock"
    if s["view"] == "sheet":
        p = _sheet_page(s.get("sheet") or {})
        _foot(p, s, "BACK RETURNS  ·  HOME FRONT")
        return p, "sheet"
    if s["view"] == "article":
        p, n = _article_page(s["link"], s["apage"])
        _foot(p, s, "PAGE KEYS TURN  ·  LEFT/RIGHT TEXT SIZE  ·  BACK RETURNS  ·  HOME FRONT")
        return p, "article"
    p = _copy(_paper_view(s["view"], s["ipage"]))
    if p.hotspots:
        s["sel"] = min(s["sel"], len(p.hotspots) - 1)
        if not _touch:
            p.highlight(p.hotspots[s["sel"]][0])
    if _touch:  # a finger's selection: whatever its first tap framed
        armed = s.get("armed") or []
        if armed[:1] == ["hot"] and armed[1] < len(p.hotspots):
            p.highlight(p.hotspots[armed[1]][0])
        elif armed[:1] == ["tap"]:
            for box, action in _paper_view(s["view"], s["ipage"]).taps:
                if action == armed[1]:
                    p.highlight(box, tab=False)
                    break
    _foot(p, s, "PAGE KEYS FLIP  ·  5-WAY CHOOSE  ·  PRESS READS  ·  MENU ALL ARTICLES")
    return p, s["view"]
