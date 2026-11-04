"""HTTP front: the Kindle fetches /ui.png?t=<token>&k=<key> for each button press, /kindle.png?t=<token> for
whichever page is due while it sleeps, and /edition.mobi?t=<token> for the day's full articles."""
import hmac
import io
import json
import logging
import tarfile
import threading
import time
from contextlib import asynccontextmanager
from datetime import date
from pathlib import Path

from urllib.parse import parse_qs, quote

from fastapi import FastAPI, HTTPException, Query, Request
from fastapi.responses import HTMLResponse, RedirectResponse, Response

from paper import archive, edition, history, offline, pages, periodical, sources
from paper.config import SETTINGS as S
from paper.pages import PAGES, now, page_for

logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")
log = logging.getLogger(__name__)


def _refresher():
    """Keep the feeds (and so the day's archive) fresh even when nobody is fetching pages, e.g. overnight."""
    while True:
        try:
            n = sources.news() or {}
            sources.markets()
            history.snapshot()
            from paper import photos

            photos.warm(i.get("image") for sec in ("world", "home") for i in (n.get(sec) or [])[:3])
            time.sleep(90)  # let the full-text fetches for new articles land first
            edition.refresh()
            if periodical.available():
                periodical.refresh()
        except Exception:
            log.exception("background refresh failed")
        time.sleep(15 * 60)


@asynccontextmanager
async def lifespan(_app):
    from paper import ui

    ui.load_reader()
    threading.Thread(target=_refresher, daemon=True, name="refresher").start()
    yield


app = FastAPI(docs_url=None, redoc_url=None, openapi_url=None, lifespan=lifespan)


def _no_demo():
    """The heavy downloads aren't served on the public demo."""
    if S.demo:
        raise HTTPException(status_code=404)


def _check(t: str):
    if S.token and not hmac.compare_digest(t or "", S.token):
        raise HTTPException(status_code=404)


def _png(name: str) -> Response:
    img = PAGES[name]().img
    buf = io.BytesIO()
    img.save(buf, format="PNG", optimize=True)
    return Response(buf.getvalue(), media_type="image/png", headers={"Cache-Control": "no-store", "X-Page": name})


@app.get("/kindle.png")
def kindle(t: str = Query(""), b: int | None = Query(None, ge=0, le=100)):
    _check(t)
    if b is not None:
        pages.DEVICE["battery"] = b
    return _png(page_for(now()))


@app.get("/page/{name}.png")
def page(name: str, t: str = Query("")):
    _check(t)
    if name not in PAGES:
        raise HTTPException(status_code=404)
    return _png(name)


KEYS = {"next", "prev", "up", "down", "left", "right", "select", "back", "home", "menu", "none", "auto", "tap"}


@app.get("/ui.png")
def ui_png(t: str = Query(""), k: str = Query("none"), peek: int = Query(0), b: int | None = Query(None, ge=0, le=100),
           x: int = Query(0, ge=0, le=600), y: int = Query(0, ge=0, le=800), s: str = Query("", max_length=40),
           touch: int = Query(0)):
    """Interactive mode: apply one button press and return the screen to show. peek=1 draws the screen that press
    would give without making it (the Kindle prefetches with it). b is the Kindle's battery level. k=tap with x, y
    (in the 600x800 page) is a touch-screen tap. s identifies the reader when several share the server (the demo)."""
    _check(t)
    if k not in KEYS:
        raise HTTPException(status_code=400)
    from paper import ui

    if b is not None and not S.demo:
        pages.DEVICE["battery"] = b
    p, view = ui.handle(k, peek=bool(peek), x=x, y=y, sid=s if S.demo else "", touch=bool(touch))
    if not peek and not S.demo:  # remember what the Kindle now shows, for live mode and the smart flash
        new = p.img.convert("L")
        if _shown["img"] is not None:
            _clean.extend(_ghost_boxes(_shown["img"], new))
            _clean.extend(_ink_due(_shown["img"], new))
        _shown["img"] = new
    buf = io.BytesIO()
    p.img.save(buf, format="PNG", optimize=True)
    return Response(buf.getvalue(), media_type="image/png", headers={"Cache-Control": "no-store", "X-View": view})


# --- live mode ---------------------------------------------------------------------------------------
# The Kindle 4 can redraw one box of the screen without flashing the rest (FBInk). In live mode it ticks the
# countdowns itself from a set of digit images (/atlas.tar), and every few seconds asks /live.tar for
# whatever else changed on its screen, as small PNG patches. A ghost ledger counts the no-flash redraws on each
# 40px square; a patch over squares that have had too many is sent as a flash of just that box, which the change
# itself disguises, so the page as a whole never flashes.
_shown: dict = {"img": None}  # the screen the Kindle shows now (greyscale), as far as we know
DEBT_CELL, DEBT_LIMIT = 40, 8
_debt: dict[tuple[int, int], int] = {}
STRIP = 16  # rows per strip when looking for changes


def _png_bytes(img) -> bytes:
    buf = io.BytesIO()
    img.save(buf, format="PNG", optimize=True)
    return buf.getvalue()


def _tar(files: dict[str, bytes]) -> bytes:
    buf = io.BytesIO()
    with tarfile.open(fileobj=buf, mode="w", format=tarfile.USTAR_FORMAT) as tf:
        for name, data in files.items():
            info = tarfile.TarInfo(name)
            info.size = len(data)
            info.mtime = int(time.time())
            tf.addfile(info, io.BytesIO(data))
    return buf.getvalue()


def _changed_boxes(old, new) -> list[tuple[int, int, int, int]]:
    """Boxes (x0, y0, x1, y1, exclusive) covering every changed pixel: strips of rows, cropped to the changed
    columns, with neighbouring strips merged."""
    from PIL import ImageChops

    from paper.draw import H, W

    diff = ImageChops.difference(old, new)
    boxes: list[list[int]] = []
    for y in range(0, H, STRIP):
        bb = diff.crop((0, y, W, min(H, y + STRIP))).getbbox()
        if not bb:
            continue
        x0, y0, x1, y1 = bb[0], y + bb[1], bb[2], y + bb[3]
        if boxes and y0 - boxes[-1][3] <= 2:
            b = boxes[-1]
            boxes[-1] = [min(b[0], x0), b[1], max(b[2], x1), y1]
        else:
            boxes.append([x0, y0, x1, y1])
    return [tuple(b) for b in boxes]


_clean: list = []  # boxes a page turn left ghosts in, flashed by the next /live.tar


def _ghost_boxes(old, new) -> list[tuple[int, int, int, int]]:
    """Where a page turn is likely to have left a ghost: thick black strokes (the masthead, big headlines, filled
    bars) that are now white. Thin body text barely ghosts, so it is left alone and the rest of the page never
    flashes. A 5px minimum filter keeps only dark pixels whose whole neighbourhood is dark, i.e. thick strokes."""
    from PIL import Image, ImageChops, ImageFilter

    thick = old.point(lambda v: 255 if v < 110 else 0).filter(ImageFilter.MinFilter(5))
    light = new.point(lambda v: 255 if v > 200 else 0)
    risk = ImageChops.multiply(thick, light)
    boxes = []
    for x0, y0, x1, y1 in _changed_boxes(Image.new("L", risk.size, 0), risk):
        x0, y0, x1, y1 = max(0, x0 - 6), max(0, y0 - 6), min(risk.width, x1 + 6), min(risk.height, y1 + 6)
        boxes.append((x0, y0, x1, y1))
    return boxes[:10]


# Smart flash for page turns. Every no-flash page turn that turns ink white leaves a faint hollow outline of it.
# _ink adds up, per 40px square, how many dark pixels have been wiped that way since the square was last flashed.
# Once a square has had about six pages of text wiped through it, the area of all the well-worn squares flashes
# once (just that area), so busy text areas are cleaned when they need it and quiet ones never flash. The ink
# roller cleans the whole screen 45 s after the last turn, so a pause that long starts the count again.
INK_LIMIT = 1100  # wiped dark pixels per 1600-pixel square before it is flashed
INK_QUIET = 60    # seconds without a turn after which the roller has cleaned everything
_ink: dict[tuple[int, int], int] = {}
_last_turn = {"t": 0.0}


def _ink_due(old, new) -> list[tuple[int, int, int, int]]:
    from PIL import Image, ImageChops

    now = time.time()
    if now - _last_turn["t"] > INK_QUIET:
        _ink.clear()
    _last_turn["t"] = now
    wiped = ImageChops.multiply(old.point(lambda v: 255 if v < 128 else 0), new.point(lambda v: 255 if v > 200 else 0))
    if not wiped.getbbox():
        return []
    W, H = wiped.size
    cw, ch = (W + DEBT_CELL - 1) // DEBT_CELL, (H + DEBT_CELL - 1) // DEBT_CELL
    # pixel counts per square: shrink with a box filter (mean), then scale back up to counts
    small = wiped.resize((cw, ch), Image.BOX)
    for cy in range(ch):
        for cx in range(cw):
            n = small.getpixel((cx, cy)) * DEBT_CELL * DEBT_CELL // 255
            if n:
                _ink[(cx, cy)] = _ink.get((cx, cy), 0) + n
    if max(_ink.values(), default=0) < INK_LIMIT:
        return []
    hot = Image.new("L", (W, H), 0)
    for (cx, cy), n in list(_ink.items()):
        if n >= INK_LIMIT // 2:  # flash the well-worn neighbours too, so one flash does the job
            hot.paste(255, (cx * DEBT_CELL, cy * DEBT_CELL, min(W, (cx + 1) * DEBT_CELL), min(H, (cy + 1) * DEBT_CELL)))
            del _ink[(cx, cy)]
    return _changed_boxes(Image.new("L", (W, H), 0), hot)[:10]


def _flash_due(box) -> bool:
    """Charge each 40px square under the box one no-flash redraw; if any is over the limit, flash the box instead
    and clear the squares."""
    x0, y0, x1, y1 = box
    keys = [(cx, cy) for cx in range(x0 // DEBT_CELL, (x1 - 1) // DEBT_CELL + 1)
            for cy in range(y0 // DEBT_CELL, (y1 - 1) // DEBT_CELL + 1)]
    if any(_debt.get(k, 0) + 1 >= DEBT_LIMIT for k in keys):
        for k in keys:
            _debt[k] = 0
        return True
    for k in keys:
        _debt[k] = _debt.get(k, 0) + 1
    return False


@app.get("/atlas.tar")
def atlas_tar(t: str = Query("")):
    """The live countdown's digits: d00.png ... d99.png, each one two-digit cell exactly as the page draws it."""
    _check(t)
    _no_demo()
    from PIL import Image

    files = {}
    for n in range(100):
        im = Image.new("L", (2 * pages.CELL_W, pages.CELL_H), 255)
        pages.digit_cell(im, 0, 0, f"{n:02d}")
        files[f"d{n:02d}.png"] = _png_bytes(im)
    return Response(_tar(files), media_type="application/x-tar", headers={"Cache-Control": "no-store"})


@app.get("/live.tar")
def live_tar(t: str = Query("")):
    """manifest.txt says what the screen ticks and what to redraw:
         view <name>
         mode countdown <unix time> | mode clock 0 | mode none 0
         cell <h|m|s> <x> <y>        (where the Kindle stamps its digit images)
         patch <file> <x> <y> <0|1>  (1 = flash just that box)
       plus the patch PNGs."""
    _check(t)
    _no_demo()
    from PIL import ImageDraw

    from paper import ui

    p, view = ui.live_frame()
    new = p.img.convert("L")
    lv = pages.LIVE.get(view) or {}
    cells = lv.get("cells") or []
    mode = lv.get("mode") or ("none", 0)
    lines = [f"view {view}", f"mode {mode[0]} {mode[1]}"] + [f"cell {n} {x} {y}" for n, x, y in cells]
    files = {}
    old = _shown["img"]
    while _clean:  # ghosts from the last page turn: flash just those boxes, with what's there now
        box = _clean.pop(0)
        name = f"g{len(files)}.png"
        files[name] = _png_bytes(new.crop(box))
        lines.append(f"patch {name} {box[0]} {box[1]} 1")
        for cx in range(box[0] // DEBT_CELL, (box[2] - 1) // DEBT_CELL + 1):
            for cy in range(box[1] // DEBT_CELL, (box[3] - 1) // DEBT_CELL + 1):
                _debt[(cx, cy)] = 0
                _ink.pop((cx, cy), None)
    if old is not None:
        a, b = old.copy(), new.copy()
        for _, x, y in cells:  # the Kindle draws these itself
            for im in (a, b):
                ImageDraw.Draw(im).rectangle([x, y, x + 2 * pages.CELL_W - 1, y + pages.CELL_H - 1], fill=255)
        for i, box in enumerate(_changed_boxes(a, b)[:12]):
            name = f"p{i}.png"
            files[name] = _png_bytes(new.crop(box))
            lines.append(f"patch {name} {box[0]} {box[1]} {1 if _flash_due(box) else 0}")
    _shown["img"] = new
    files = {"manifest.txt": ("\n".join(lines) + "\n").encode(), **files}
    return Response(_tar(files), media_type="application/x-tar", headers={"Cache-Control": "no-store"})


@app.get("/edition.pdf")
def edition_pdf(t: str = Query(""), day: str = Query("")):
    _check(t)
    _no_demo()
    try:
        d = date.fromisoformat(day) if day else None
    except ValueError:
        raise HTTPException(status_code=400)
    data = edition.pdf_for(d)
    return Response(data, media_type="application/pdf", headers={"Cache-Control": "no-store"})


@app.get("/edition.mobi")
def edition_mobi(t: str = Query(""), day: str = Query("")):
    """The same edition as a Kindle periodical (sections, articles, reflowing text)."""
    _check(t)
    _no_demo()
    try:
        d = date.fromisoformat(day) if day else None
    except ValueError:
        raise HTTPException(status_code=400)
    if not periodical.available():
        raise HTTPException(status_code=503, detail="ebook-convert not installed")
    try:
        data = periodical.mobi_for(d)
    except Exception as e:
        log.exception("periodical build failed")
        raise HTTPException(status_code=503, detail=str(e)[:1500])
    return Response(data, media_type="application/x-mobipocket-ebook", headers={"Cache-Control": "no-store"})


VIEW_HTML = (Path(__file__).parent / "view.html").read_text(encoding="utf-8")


@app.get("/")
def root():
    return RedirectResponse("view")


@app.get("/view")
def view(t: str = Query("")):
    """The paper in any web browser (an e-reader's built-in browser, an old phone or tablet): tap or use the keys."""
    _check(t)
    html = VIEW_HTML.replace('"__TOKEN__"', json.dumps(t).replace("</", "<\\/"))
    return HTMLResponse(html, headers={"Cache-Control": "no-store"})


@app.get("/watchlist")
def watchlist_page(t: str = Query(""), q: str = Query("", max_length=60)):
    """Choose the Markets pages' instruments from a phone (see watchlist_page.py)."""
    _check(t)
    _no_demo()
    from paper import watchlist_page as wp

    return HTMLResponse(wp.render(t, q.strip()), headers={"Cache-Control": "no-store"})


@app.post("/watchlist/{action}")
async def watchlist_change(action: str, request: Request, t: str = Query("")):
    _check(t)
    _no_demo()
    from paper import watchlist

    form = {k: v[0] for k, v in parse_qs((await request.body()).decode()).items()}
    sym = form.get("symbol", "")[:30]
    if action == "add" and sym:
        watchlist.add(sym, form.get("name", sym), form.get("kind", "stock"))
    elif action == "remove":
        watchlist.remove(sym)
    elif action == "move":
        watchlist.move(sym, 1 if form.get("step") == "1" else -1)
    elif action == "reset":
        watchlist.reset()
    else:
        raise HTTPException(status_code=404)
    from paper import ui

    ui._renders.clear()
    return RedirectResponse(f"../watchlist?t={quote(t)}", status_code=303)


@app.get("/setup")
def setup(request: Request, saved: int = Query(0)):
    """Settings for a beginner: town, time zone, masthead, calendar, and the e-reader's address (see setup_page.py)."""
    from paper import setup_page as sp

    sp.guard(request)
    return HTMLResponse(sp.render(request, saved=bool(saved)), headers={"Cache-Control": "no-store"})


@app.post("/setup/save")
async def setup_save(request: Request):
    from paper import setup_page as sp

    sp.guard(request, post=True)
    form = {k: v[0] for k, v in parse_qs((await request.body()).decode(), keep_blank_values=True).items()}
    error = sp.save(form)
    if error:
        return HTMLResponse(sp.render(request, error=error, form=form), status_code=400,
                            headers={"Cache-Control": "no-store"})
    return RedirectResponse("/setup?saved=1", status_code=303)


@app.get("/offline.tar")
def offline_tar(t: str = Query("")):
    """The paper as a book of screens, for reading when the Kindle can't reach us (see offline.py)."""
    _check(t)
    _no_demo()
    return Response(offline.tar(), media_type="application/x-tar", headers={"Cache-Control": "no-store"})


@app.get("/bookpos")
def bookpos(t: str = Query("")):
    """Which page of the offline copy matches the screen the reader is on now."""
    _check(t)
    _no_demo()
    from paper import ui

    return Response(f"{offline.position(ui.state_for(""))}\n", media_type="text/plain", headers={"Cache-Control": "no-store"})


@app.get("/healthz")
def healthz():
    return {"ok": True, "articles_today": archive.count_today(), "battery": pages.DEVICE.get("battery")}
