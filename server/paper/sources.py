"""Every data source the paper reads. Each returns plain dicts/lists, or None when unavailable."""
import json
import logging
import re
from datetime import date, datetime, timedelta, timezone
from html import unescape

import feedparser
import httpx

from paper.cache import cached
from paper.config import SETTINGS as S

log = logging.getLogger(__name__)
UA = {"User-Agent": "Mozilla/5.0 (Stop Press e-ink newspaper)"}


def _get(url: str, **kw) -> httpx.Response:
    r = httpx.get(url, headers={**UA, **kw.pop("headers", {})}, timeout=15, follow_redirects=True, **kw)
    r.raise_for_status()
    return r


# --- Weather (Open-Meteo, no key) -------------------------------------------------------------

WMO = {
    0: "Clear", 1: "Mostly clear", 2: "Partly cloudy", 3: "Overcast", 45: "Fog", 48: "Freezing fog",
    51: "Light drizzle", 53: "Drizzle", 55: "Heavy drizzle", 56: "Freezing drizzle", 57: "Freezing drizzle",
    61: "Light rain", 63: "Rain", 65: "Heavy rain", 66: "Freezing rain", 67: "Freezing rain",
    71: "Light snow", 73: "Snow", 75: "Heavy snow", 77: "Snow grains", 80: "Showers", 81: "Showers",
    82: "Heavy showers", 85: "Snow showers", 86: "Snow showers", 95: "Thunderstorms", 96: "Thunder & hail", 99: "Thunder & hail",
}


@cached(ttl_s=900)
def weather():
    r = _get(
        "https://api.open-meteo.com/v1/forecast",
        params={
            "latitude": S.lat, "longitude": S.lon, "timezone": str(S.tz),
            "current": "temperature_2m,weather_code,wind_speed_10m,apparent_temperature",
            "hourly": "temperature_2m,precipitation_probability",
            "daily": "weather_code,temperature_2m_max,temperature_2m_min,precipitation_probability_max,sunrise,sunset",
            "forecast_days": 7, "wind_speed_unit": "mph",
        },
    ).json()
    cur, d = r["current"], r["daily"]
    days = [
        {
            "date": date.fromisoformat(d["time"][i]),
            "summary": WMO.get(d["weather_code"][i], "—"),
            "code": d["weather_code"][i],
            "hi": round(d["temperature_2m_max"][i]),
            "lo": round(d["temperature_2m_min"][i]),
            "rain": d["precipitation_probability_max"][i] or 0,
        }
        for i in range(len(d["time"]))
    ]
    now_hour = datetime.fromisoformat(cur["time"]).replace(minute=0)
    hours = [
        (datetime.fromisoformat(t), temp, p or 0)
        for t, temp, p in zip(r["hourly"]["time"], r["hourly"]["temperature_2m"], r["hourly"]["precipitation_probability"])
        if datetime.fromisoformat(t) >= now_hour
    ][:24]
    return {
        "temp": round(cur["temperature_2m"]),
        "feels": round(cur["apparent_temperature"]),
        "wind": round(cur["wind_speed_10m"]),
        "code": cur["weather_code"],
        "summary": WMO.get(cur["weather_code"], "—"),
        "sunrise": datetime.fromisoformat(d["sunrise"][0]).strftime("%H:%M"),
        "sunset": datetime.fromisoformat(d["sunset"][0]).strftime("%H:%M"),
        "daylight_min": int((datetime.fromisoformat(d["sunset"][0]) - datetime.fromisoformat(d["sunrise"][0])).total_seconds() // 60),
        "daylight_tomorrow_min": int((datetime.fromisoformat(d["sunset"][1]) - datetime.fromisoformat(d["sunrise"][1])).total_seconds() // 60),
        "days": days,
        "hours": hours,
    }


# --- Markets (Yahoo Finance chart endpoint, no key) -------------------------------------------

INSTRUMENTS = [tuple(i) for i in S.markets]


def _chart(symbol: str) -> dict:
    r = _get(f"https://query1.finance.yahoo.com/v8/finance/chart/{symbol}", params={"range": "3mo", "interval": "1d"}).json()
    res = r["chart"]["result"][0]
    closes = [c for c in res["indicators"]["quote"][0]["close"] if c is not None]
    price = res["meta"].get("regularMarketPrice") or closes[-1]
    # The last daily bar is the latest session (it tracks the live price), so the day's move is against the bar before.
    # If the live price has moved off that bar (a new session not yet in the series), compare with the bar itself.
    prev = closes[-2] if len(closes) >= 2 else price
    if abs(closes[-1] - price) / price > 0.002:
        prev = closes[-1]
    return {
        "price": price,
        "change_pct": (price - prev) / prev * 100 if prev else 0.0,
        "month_pct": (price - closes[-22]) / closes[-22] * 100 if len(closes) >= 22 else None,
        "series": closes[-30:],
        "currency": res["meta"].get("currency", ""),
        "market_state": res["meta"].get("marketState") or "",
    }


@cached(ttl_s=600)
def markets():
    out = []
    for sym, name, kind in INSTRUMENTS:
        try:
            out.append({"symbol": sym, "name": name, "kind": kind, **_chart(sym)})
        except Exception:
            log.warning("market %s failed", sym, exc_info=True)
    if not out:
        raise RuntimeError("no market data")
    return out


# --- News (RSS) --------------------------------------------------------------------------------

FEEDS = {key: [tuple(f) for f in sec["feeds"]] for key, sec in S.sections.items()}

_TAG = re.compile(r"<[^>]+>")


def _clean(s: str) -> str:
    return re.sub(r"\s+", " ", unescape(_TAG.sub(" ", s or ""))).strip()


def _norm(title: str) -> str:
    return re.sub(r"[^a-z0-9 ]", "", title.lower())


def _image(e) -> str | None:
    """The story's photo from the feed: the widest media:content (Guardian) or the thumbnail (BBC, upsized)."""
    from paper.photos import best_url

    content = [c for c in e.get("media_content", []) if c.get("url") and c.get("medium", "image") == "image"]
    if content:
        return max(content, key=lambda c: int(c.get("width") or 0))["url"]
    thumbs = e.get("media_thumbnail") or []
    return best_url(thumbs[0]["url"]) if thumbs and thumbs[0].get("url") else None


def _section(name: str, limit: int = 12) -> list[dict]:
    items, seen = [], set()
    for source, url in FEEDS[name]:
        try:
            feed = feedparser.parse(_get(url).content)
        except Exception:
            log.warning("feed %s failed", url, exc_info=True)
            continue
        for e in feed.entries[:20]:
            title = _clean(e.get("title", ""))
            key = " ".join(_norm(title).split()[:6])
            if not title or key in seen or "/sport" in e.get("link", ""):
                continue
            seen.add(key)
            ts = e.get("published_parsed") or e.get("updated_parsed")
            when = datetime(*ts[:6], tzinfo=timezone.utc) if ts else None
            summary = _clean(e.get("summary", ""))[:400]
            if _norm(summary).startswith(_norm(title)[:40]):
                summary = ""  # some feeds repeat the headline as the summary
            items.append({"title": title, "summary": summary, "source": source, "when": when, "link": e.get("link", ""),
                          "image": _image(e)})
    # Interleave sources by recency so one outlet doesn't swamp the page.
    items.sort(key=lambda i: i["when"] or datetime.min.replace(tzinfo=timezone.utc), reverse=True)
    return items[:limit]


@cached(ttl_s=900)
def news():
    out = {name: _section(name) for name in FEEDS}
    if not any(out.values()):
        raise RuntimeError("no news")
    from paper import archive

    for name, items in out.items():
        try:
            archive.record(name, items)
        except Exception:
            log.warning("archive record failed", exc_info=True)
    return out


# --- Calendar (private iCal link) --------------------------------------------------------------


@cached(ttl_s=600)
def calendar():
    if not S.calendar_ics_url:
        return None
    import icalendar
    import recurring_ical_events

    cal = icalendar.Calendar.from_ical(_get(S.calendar_ics_url).content)
    now = datetime.now(S.tz)
    start = now.replace(hour=0, minute=0, second=0, microsecond=0)
    events = []
    for ev in recurring_ical_events.of(cal).between(start, start + timedelta(days=7)):
        dtstart = ev["DTSTART"].dt
        all_day = not isinstance(dtstart, datetime)
        begin = datetime.combine(dtstart, datetime.min.time(), S.tz) if all_day else dtstart.astimezone(S.tz)
        dtend = ev.get("DTEND")
        end = None
        if dtend is not None and not all_day:
            end = dtend.dt.astimezone(S.tz)
        if not all_day and end and end < now:
            continue
        events.append({"title": str(ev.get("SUMMARY", "(no title)")), "start": begin, "end": end, "all_day": all_day,
                       "where": str(ev.get("LOCATION", "") or "").split(",")[0]})
    events.sort(key=lambda e: (e["start"], not e["all_day"]))
    return events


# --- Your own numbers ([[stats]] in the config) -----------------------------------------------------


def _dig(data, path: str):
    """'a.b.0.c' into nested JSON."""
    for part in [p for p in path.split(".") if p]:
        data = data[int(part)] if isinstance(data, list) else data[part]
    return data


@cached(ttl_s=900)
def stats():
    """[{label, value, note, key}] for each configured stat that could be read. None when none are configured.

    Each stat is a URL that returns JSON and a dotted path to the number in it, e.g.
        [[stats]]
        label = "GitHub stars"
        url = "https://api.github.com/repos/you/project"
        path = "stargazers_count"
    """
    if not S.stats:
        return None
    out = []
    for i, st in enumerate(S.stats):
        if not st.get("url"):
            continue
        key = st.get("key") or f"stat{i}"
        try:
            r = _get(st["url"], headers=st.get("headers", {}))
            value = _dig(r.json(), st.get("path", ""))
            value = float(value) if isinstance(value, str) else value
        except Exception:
            log.warning("stat %s failed", st.get("label"), exc_info=True)
            value = None
        out.append({"key": key, "label": st.get("label", key), "value": value, "note": st.get("note", ""),
                    "unit": st.get("unit", "")})
    return out


# --- Editor's note (optional AI) -------------------------------------------------------------------


@cached(ttl_s=3600)
def editors_note():
    """Two or three sentences tying the day together, from any OpenAI-compatible endpoint. None when AI is off
    (the front page then builds a plain sentence from the top headline, the lead market and the weather)."""
    if not (S.ai_base_url and S.ai_model):
        return None
    n = news() or {}
    m = markets() or []
    w = weather() or {}
    facts = {
        "date": datetime.now(S.tz).strftime("%A %d %B %Y"),
        "place": S.place,
        "weather": f"{w.get('summary')} {w.get('temp')}C, high {w['days'][0]['hi']}C" if w.get("days") else None,
        "world_headlines": [i["title"] for i in n.get("world", [])[:6]],
        "business_headlines": [i["title"] for i in n.get("business", [])[:4]],
        "markets": {x["name"]: f"{x['change_pct']:+.1f}%" for x in m[:8]},
    }
    headers = {"Authorization": f"Bearer {S.ai_api_key}"} if S.ai_api_key else {}
    r = httpx.post(
        S.ai_base_url.rstrip("/") + "/chat/completions",
        headers=headers, timeout=60,
        json={"model": S.ai_model, "max_tokens": 2000, "messages": [
            {"role": "system", "content": (
                "You write the two-to-three sentence 'editor's note' at the top of a one-reader morning newspaper. "
                "Plain, confident English, like a broadsheet's front-page summary. Only use the facts given; "
                "no invented numbers, no advice, no emojis, no greeting. Max 45 words.")},
            {"role": "user", "content": json.dumps(facts, default=str)},
        ]},
    )
    r.raise_for_status()
    text = (r.json()["choices"][0]["message"].get("content") or "").strip()
    return text or None
