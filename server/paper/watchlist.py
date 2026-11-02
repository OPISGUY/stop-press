"""The markets watchlist: which instruments the Markets pages list, in order. Edit it from a phone at /watchlist
(see app.py). It's saved in the data folder, so it survives a restart, and [markets] in stop-press.toml is only the
starting list."""
import json
import logging
import threading

from paper import archive
from paper.config import SETTINGS as S

log = logging.getLogger(__name__)
MAX_ITEMS = 60

# The starting list is [markets] instruments in stop-press.toml.
DEFAULT = [{"symbol": s, "name": n, "kind": k} for s, n, k in S.markets]

# Yahoo's quoteType -> our kind
KINDS = {"INDEX": "index", "CURRENCY": "fx", "CRYPTOCURRENCY": "crypto", "FUTURE": "commodity", "EQUITY": "stock",
         "ETF": "fund", "MUTUALFUND": "fund"}

_lock = threading.Lock()
_items: list[dict] | None = None


def _local():
    return archive.LOCAL / "watchlist.json"


def items() -> list[dict]:
    global _items
    with _lock:
        if _items is None:
            _items = _load()
        return [dict(i) for i in _items]


def _load() -> list[dict]:
    try:
        if _local().exists():
            return json.loads(_local().read_text())
    except Exception:
        log.info("no saved watchlist yet; using the default", exc_info=True)
    return [dict(i) for i in DEFAULT]


def _save(new: list[dict]):
    global _items
    body = json.dumps(new)
    archive.LOCAL.mkdir(parents=True, exist_ok=True)
    _local().write_text(body)
    _items = new
    from paper import sources

    sources.markets.clear()


def add(symbol: str, name: str, kind: str):
    with _lock:
        cur = _items if _items is not None else _load()
        if any(i["symbol"] == symbol for i in cur) or len(cur) >= MAX_ITEMS:
            return
        _save(cur + [{"symbol": symbol, "name": name[:40], "kind": kind}])


def remove(symbol: str):
    with _lock:
        cur = _items if _items is not None else _load()
        _save([i for i in cur if i["symbol"] != symbol])


def move(symbol: str, step: int):
    with _lock:
        cur = list(_items if _items is not None else _load())
        idx = next((n for n, i in enumerate(cur) if i["symbol"] == symbol), None)
        if idx is None:
            return
        to = max(0, min(len(cur) - 1, idx + step))
        cur.insert(to, cur.pop(idx))
        _save(cur)


def reset():
    with _lock:
        _save([dict(i) for i in DEFAULT])
