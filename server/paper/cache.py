"""Time-based caching for data sources. Once a value exists it is always served straight away: an expired one
is refreshed in the background (so a button press never waits on a slow feed), and if a refresh fails the
last good value stays, so one flaky feed never blanks the page."""
import functools
import logging
import threading
import time

log = logging.getLogger(__name__)


def cached(ttl_s: int):
    def deco(fn):
        lock = threading.Lock()
        state = {"at": 0.0, "value": None, "ok": False, "refreshing": False}

        def refresh():
            try:
                value = fn()
                with lock:
                    state.update(at=time.time(), value=value, ok=True)
            except Exception:
                log.exception("source %s failed", fn.__name__)
                with lock:
                    state["at"] = time.time() - ttl_s / 2  # retry sooner than a full TTL
            finally:
                with lock:
                    state["refreshing"] = False

        @functools.wraps(fn)
        def wrapper():
            with lock:
                fresh = state["ok"] and time.time() - state["at"] < ttl_s
                have = state["ok"]
                start_bg = have and not fresh and not state["refreshing"]
                if start_bg or not have:
                    state["refreshing"] = True
            if fresh:
                return state["value"]
            if have:
                if start_bg:
                    threading.Thread(target=refresh, daemon=True, name=f"refresh-{fn.__name__}").start()
                return state["value"]
            refresh()  # nothing cached yet: fetch in the foreground
            return state["value"]

        wrapper.clear = lambda: state.update(at=0.0, ok=False)
        return wrapper

    return deco
