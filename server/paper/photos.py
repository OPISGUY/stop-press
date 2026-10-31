"""News photos for e-ink. Each is fetched once, cropped to its slot, toned for the Kindle 4 (its panel prints
dark and flat, so we lift the mid-tones and stretch the contrast) and dithered to its 16 greys, which reads
like a newsprint halftone instead of banding."""
import io
import logging
import threading
import time
from collections import OrderedDict

import httpx
from PIL import Image, ImageEnhance, ImageOps

log = logging.getLogger(__name__)
UA = {"User-Agent": "Mozilla/5.0 (Stop Press e-ink newspaper)"}
MAX_SOURCES = 160
FAIL_RETRY_S = 1800

_lock = threading.Lock()
_sources: "OrderedDict[str, Image.Image | None]" = OrderedDict()  # url -> greyscale original (None: failed)
_failed_at: dict[str, float] = {}
_fitted: "OrderedDict[tuple, Image.Image]" = OrderedDict()

_PALETTE = Image.new("P", (1, 1))
_PALETTE.putpalette([v for i in range(16) for v in (i * 17,) * 3] + [0] * (768 - 48))


def best_url(url: str) -> str:
    """Ask the publishers' image servers for a size worth printing (the BBC feed links a 240px thumbnail)."""
    return url.replace("/ace/standard/240/", "/ace/standard/800/")


def load(url: str | None) -> Image.Image | None:
    if not url:
        return None
    with _lock:
        if url in _sources:
            _sources.move_to_end(url)
            if _sources[url] is not None or time.time() - _failed_at.get(url, 0) < FAIL_RETRY_S:
                return _sources[url]
    img = None
    try:
        r = httpx.get(url, headers=UA, timeout=10, follow_redirects=True)
        r.raise_for_status()
        img = Image.open(io.BytesIO(r.content))
        img.draft("L", (1000, 1000))  # JPEGs decode straight to greyscale at about the size we need
        img = ImageOps.exif_transpose(img).convert("L")
        img.thumbnail((1000, 1000))
    except Exception:
        log.info("photo failed: %s", url, exc_info=True)
    with _lock:
        _sources[url] = img
        if img is None:
            _failed_at[url] = time.time()
        while len(_sources) > MAX_SOURCES:
            _sources.popitem(last=False)
    return img


def available(url: str | None) -> bool:
    return load(url) is not None


def fit(url: str | None, w: int, h: int, dither: bool = True) -> Image.Image | None:
    """The photo cropped to fill w×h (centred, a little above middle where faces usually are), ready to paste."""
    key = (url, w, h, dither)
    with _lock:
        if key in _fitted:
            return _fitted[key]
    src = load(url)
    if src is None:
        return None
    img = ImageOps.fit(src, (w, h), Image.LANCZOS, centering=(0.5, 0.4))
    img = ImageOps.autocontrast(img, cutoff=1)
    img = img.point(lambda v: round(255 * (v / 255) ** 0.8))  # lift the mid-tones
    img = ImageEnhance.Sharpness(img).enhance(1.4)
    if dither:
        img = img.convert("RGB").quantize(palette=_PALETTE, dither=Image.Dither.FLOYDSTEINBERG).convert("L")
    with _lock:
        _fitted[key] = img
        while len(_fitted) > 120:
            _fitted.popitem(last=False)
    return img


def warm(urls):
    """Fetch photos ahead of the pages that need them (called from the background refresher)."""
    for u in urls:
        if u:
            load(u)
