"""Daily snapshots of your [[stats]] numbers, so the paper can draw a trend for figures whose sources only give
today's total. One JSON document, {day: {metric: value}}, at <data_dir>/metrics.json."""
import json
import logging
import threading
from datetime import date, datetime, timedelta

from paper import archive
from paper.config import SETTINGS as S

log = logging.getLogger(__name__)
KEEP_DAYS = 400

_lock = threading.Lock()
_data: dict[str, dict] | None = None


def _local():
    return archive.LOCAL / "metrics.json"


def _load() -> dict:
    global _data
    if _data is not None:
        return _data
    _data = {}
    try:
        if _local().exists():
            _data = json.loads(_local().read_text())
    except Exception:
        log.info("no metrics history yet", exc_info=True)
    return _data


def record(metrics: dict):
    """Set today's values (the last reading of the day wins) and save."""
    metrics = {k: v for k, v in metrics.items() if v is not None}
    if not metrics:
        return
    with _lock:
        data = _load()
        today = datetime.now(S.tz).date()
        data.setdefault(today.isoformat(), {}).update(metrics)
        cutoff = (today - timedelta(days=KEEP_DAYS)).isoformat()
        for k in [k for k in data if k < cutoff]:
            del data[k]
        try:
            archive.LOCAL.mkdir(parents=True, exist_ok=True)
            _local().write_text(json.dumps(data, separators=(",", ":")))
        except Exception:
            log.warning("saving metrics history failed", exc_info=True)


def series(metric: str, days: int = 30) -> list[tuple[date, float]]:
    """(day, value) for the last `days` days that have a reading, oldest first."""
    with _lock:
        data = _load()
        today = datetime.now(S.tz).date()
        out = []
        for i in range(days - 1, -1, -1):
            d = today - timedelta(days=i)
            v = data.get(d.isoformat(), {}).get(metric)
            if v is not None:
                out.append((d, v))
        return out


def change(metric: str, days: int) -> float | None:
    """How much a metric has moved over the last `days` days (from the oldest reading in that window)."""
    s = series(metric, days + 1)
    if len(s) < 2:
        return None
    return s[-1][1] - s[0][1]


def snapshot():
    """Called by the background refresher: read the stats (cached) and file today's numbers."""
    from paper import sources

    try:
        record({s["key"]: s["value"] for s in sources.stats() or [] if isinstance(s["value"], (int, float))})
    except Exception:
        log.warning("stats snapshot failed", exc_info=True)
