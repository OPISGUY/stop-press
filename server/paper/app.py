"""HTTP front: the Kindle fetches /ui.png?t=<token>&k=<key> for each button press, /kindle.png?t=<token> for
whichever page is due while it sleeps, and /edition.mobi?t=<token> for the day's full articles."""
import hmac
import io
import json
import logging
import threading
import time
from contextlib import asynccontextmanager
from datetime import date
from pathlib import Path

from fastapi import FastAPI, HTTPException, Query
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
    buf = io.BytesIO()
    p.img.save(buf, format="PNG", optimize=True)
    return Response(buf.getvalue(), media_type="image/png", headers={"Cache-Control": "no-store", "X-View": view})


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
