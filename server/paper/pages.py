"""The pages of the paper. Each takes no arguments, reads the cached sources, and returns a Page."""
from datetime import datetime, time as dtime, timedelta, timezone
from zoneinfo import ZoneInfo

from paper import history, sources
from paper.config import SETTINGS as S
from paper.draw import DARK, INK, LIGHT, M, MID, PAPER, H, W, Page, font

TITLES = {"front": "Front Page", "markets": "Markets", "world": "World", "stats": "Numbers", "day": "Your Day"}
# The pages in the order the page keys step through them (and the footer lists them).
ORDER = ["front", "world", "markets"] + (["stats"] if S.stats else []) + ["day"]
# Set by app.py from what the Kindle reports with each request.
DEVICE: dict = {"battery": None}


# --- small formatters ---------------------------------------------------------------------------

def now() -> datetime:
    return datetime.now(S.tz)


def ago(when: datetime | None) -> str:
    if not when:
        return ""
    mins = int((datetime.now(timezone.utc) - when).total_seconds() // 60)
    if mins < 60:
        return f"{max(mins, 1)}m ago"
    if mins < 48 * 60:
        return f"{mins // 60}h ago"
    return f"{mins // 1440}d ago"


CURRENCY = {"USD": "$", "GBP": "£", "EUR": "€", "JPY": "¥"}


def money(v: float | None, cur: str, kind: str = "stock") -> str:
    """A price in its own currency: London shares quote in pence (GBp)."""
    if v is None:
        return "—"
    if kind == "fx":
        return f"{v:.4f}"
    if kind == "index":  # points, not money
        return f"{v:,.0f}" if v >= 1000 else f"{v:,.2f}"
    if cur == "GBp":
        return f"{v:,.0f}p" if v >= 1000 else f"{v:,.1f}p"
    sym = CURRENCY.get(cur, "")
    digits = 0 if v >= 10000 else 2
    return f"{sym}{v:,.{digits}f}" + ("" if sym or kind == "index" or not cur else f" {cur}")


def price(x: dict) -> str:
    p = x["price"]
    if x["kind"] in ("stock", "fund") or (x["kind"] == "crypto" and x["symbol"] != "BTC-USD"):
        return money(p, x.get("currency", ""))
    if x["kind"] == "fx":
        return f"{p:.4f}"
    if x["symbol"] == "BTC-USD":
        return f"${p:,.0f}"
    if x["kind"] == "commodity":
        return f"${p:,.2f}"
    return f"{p:,.0f}" if p >= 1000 else f"{p:,.2f}"


def num(v, suffix="") -> str:
    if v is None:
        return "—"
    if isinstance(v, float):
        return f"{v:,.1f}{suffix}"
    return f"{v:,}{suffix}" if isinstance(v, int) else f"{v}{suffix}"


def change(p: Page, x_right: float, y_mid: float, pct: float | None, size=14):
    """'▲ 1.2%' right-aligned at x_right; down moves drawn in grey with a hollow-looking marker."""
    if pct is None:
        p.text((x_right, y_mid), "—", font("sans", size, 500), anchor="rm")
        return
    f = font("sans", size, 700 if abs(pct) >= 1 else 500)
    s = f"{abs(pct):.1f}%"
    p.text((x_right, y_mid), s, f, INK, anchor="rm")
    tx = x_right - p.width(s, f) - size * 0.55
    p.triangle(tx, y_mid, size * 0.62, pct >= 0, fill=INK if pct >= 0 else DARK)


# --- shared chrome ------------------------------------------------------------------------------

def battery_mark(p: Page, x_right: float, y: int):
    """The Kindle's battery, as last reported; a black warning tag when it needs charging."""
    pct = DEVICE.get("battery")
    if pct is None:
        return
    x = p.battery(x_right, y, pct)
    f = font("sans", 11, 700)
    if pct <= 15:
        label = f"CHARGE ME · {pct}%"
        w = p.width(label, f)
        p.d.rectangle([x - w - 8, y - 2, x + 1, y + 13], fill=INK)
        p.text((x - 4, y), label, f, PAPER, anchor="ra")
    else:
        p.text((x, y), f"{pct}%", font("sans", 11, 500), DARK, anchor="ra")


def header(p: Page, section: str) -> int:
    """Compact masthead for inside pages; returns the y where content starts."""
    p.text((M, 10), S.masthead, font("masthead", 30))
    f = font("sans", 12, 700)
    p.text((W - M, 14), section.upper(), f, anchor="ra")
    battery_mark(p, W - M - p.width(section.upper(), f) - 12, 15)
    p.text((W - M, 30), now().strftime("%a %d %b · %H:%M"), font("sans", 12, 400), DARK, anchor="ra")
    p.double_rule(50)
    return 64


def footer(p: Page, page_name: str):
    order = ORDER
    y = H - 20
    p.rule(y - 8, w=1)
    f, fb = font("sans", 11, 400), font("sans", 11, 700)
    x = M
    for i, name in enumerate(order):
        label = TITLES.get(name, name).upper()
        p.text((x, y), label, fb if name == page_name else f, INK if name == page_name else MID)
        x += p.width(label, fb) + 14
    try:
        from paper import archive

        n = archive.count_today()
    except Exception:
        n = 0
    right = f"{n} in today's edition · {now().strftime('%H:%M')}" if n else f"Printed {now().strftime('%H:%M')}"
    p.text((W - M, y), right, f, MID, anchor="ra")


def section_title(p: Page, y: int, title: str, sub: str = "") -> int:
    p.text((M, y), title, font("head", 30, 800))
    if sub:
        p.text((W - M, y + 14), sub, font("italic", 14, 400), DARK, anchor="ra")
    p.rule(y + 42)
    return y + 52


# --- Front page ---------------------------------------------------------------------------------

def front() -> Page:
    p = Page()
    t = now()
    w = sources.weather()
    n = sources.news() or {}
    mk = sources.markets() or []

    small = font("sans", 12, 500)
    p.text((M, 12), f"No. {t.timetuple().tm_yday} · {t.strftime('%A').upper()} EDITION", small, DARK)
    p.text((W - M, 12), f"{S.place.upper()} EDITION", small, DARK, anchor="ra")
    battery_mark(p, W - M - p.width(f"{S.place.upper()} EDITION", small) - 12, 13)
    p.text((W / 2, 28), S.masthead, font("masthead", 56), anchor="ma")
    p.double_rule(98)

    y = 110
    fb = font("sans", 12, 700)
    p.text((M, y), t.strftime("%A %d %B %Y").upper(), fb)
    if w:
        wx = f"{w['summary']} · {w['temp']}°C · H {w['days'][0]['hi']}° L {w['days'][0]['lo']}°"
        p.text((W - M, y), wx.upper(), fb, anchor="ra")
    p.rule(y + 20)

    # Editor's note
    y += 30
    note = sources.editors_note()
    if not note:
        top = (n.get("world") or [{}])[0].get("title")
        lead_mk = next((x for x in mk if x["symbol"] == S.lead_market), None)
        bits = [f"Leading the world pages: {top}." if top else ""]
        if lead_mk:
            bits.append(f"The {lead_mk['name']} is {'up' if lead_mk['change_pct'] >= 0 else 'down'} "
                        f"{abs(lead_mk['change_pct']):.1f}% at {price(lead_mk)}.")
        if w:
            bits.append(f"In {S.place}, {w['summary'].lower()}, {w['temp']}°C.")
        note = " ".join(b for b in bits if b)
    p.text((M, y + 1), "¶", font("head", 22, 700))
    y = p.justified(M + 18, y, W - 2 * M - 18, note, font("italic", 16, 400), leading=21, max_lines=5) + 6
    p.rule(y)

    # Lead story
    y += 10
    world = n.get("world") or []
    if world:
        lead = world[0]
        y0 = y
        y = p.kicker(M, y, f"World · {lead['source']} · {ago(lead['when'])}")
        tw, py = W - 2 * M, y
        if p.photo((W - M - 220, y + 4, W - M, y + 150), lead.get("image")):
            tw = W - 2 * M - 234
        y = p.para(M, y + 2, tw, lead["title"], font("head", 28 if tw > 400 else 24, 800), leading=32 if tw > 400 else 28,
                   max_lines=3 if tw > 400 else 4) + 4
        y = p.justified(M, y, tw, lead["summary"], font("body", 15, 400), leading=20, max_lines=3) + 8
        if tw < W - 2 * M:
            y = max(y, py + 162)
        p.hot((M, y0, W - M, y - 4), lead)
    p.rule(y)

    # Two columns: more news | markets
    top = y + 10
    bottom_band = 612
    col_l_w = 318
    gx = M + col_l_w + 12
    p.vrule(gx - 6, top, bottom_band - 10, fill=MID)

    yy = p.kicker(M, top, "Also today")
    hf = font("head", 16, 700)
    for item in (world[1:] + (n.get("home") or []))[:6]:
        lines = p.wrap(item["title"], hf, col_l_w)[:2]
        need = len(lines) * 20 + 18
        if yy + need > bottom_band - 12:
            break
        y0 = yy
        yy = p.para(M, yy + 2, col_l_w, item["title"], hf, leading=20, max_lines=2)
        p.text((M, yy + 1), f"{item['source']} · {ago(item['when'])}", font("sans", 11, 400), MID)
        yy += 20
        p.hot((M, y0, M + col_l_w, yy - 4), item)

    my = p.kicker(gx, top, "Markets")
    cw = W - M - gx
    for x in ([i for i in mk if i["symbol"] in S.front_markets] or mk)[:8]:
        if my + 32 > bottom_band - 10:
            break
        p.text((gx, my + 2), x["name"], font("sans", 13, 600))
        p.text((gx, my + 18), price(x), font("sans", 12, 400), DARK)
        p.sparkline((gx + 96, my + 6, gx + cw - 62, my + 26), x["series"][-20:], width=1, baseline=False)
        change(p, gx + cw, my + 16, x["change_pct"], size=13)
        my += 36

    # Bottom band: Your day | your numbers (or technology)
    y = bottom_band
    p.double_rule(y)
    y += 14
    half = (W - 2 * M - 16) // 2
    vx = M + half + 16
    p.vrule(vx - 8, y, H - 36, fill=MID)

    dy = p.kicker(M, y, "Your day")
    events = sources.calendar()
    if events is None:
        if w:
            for d in w["days"][:3]:
                p.weather_icon(M + 12, dy + 14, 12, d["code"])
                label = "Today" if d["date"] == t.date() else d["date"].strftime("%A")
                p.text((M + 32, dy + 4), label, font("sans", 13, 700))
                p.text((M + 32, dy + 20), f"{d['summary']} · {d['hi']}°/{d['lo']}° · rain {d['rain']}%", font("sans", 12, 400), DARK)
                dy += 40
    else:
        todays = [e for e in events if e["start"].date() == t.date()] or events[:4]
        if not todays:
            p.text((M, dy + 4), "Nothing in the diary.", font("italic", 15, 400), DARK)
        for e in todays[:4]:
            when = "All day" if e["all_day"] else e["start"].strftime("%H:%M")
            if e["start"].date() != t.date():
                when = e["start"].strftime("%a ") + when
            p.text((M, dy + 3), when, font("sans", 12, 700))
            p.text((M + 62, dy + 1), p.fit(e["title"], font("body", 15, 600), half - 62), font("body", 15, 600))
            dy += 26

    st = [x for x in sources.stats() or [] if x["value"] is not None]
    if st:
        ay = p.kicker(vx, y, "At a glance")
        for x in st[:3]:
            p.text((vx, ay + 1), p.fit(x["label"], font("sans", 12, 500), half - 110), font("sans", 12, 500), DARK)
            p.text((W - M, ay - 2), fmt_stat(x), font("head", 19, 800), anchor="ra")
            ay += 30
    else:
        ay = p.kicker(vx, y, S.sections["tech"]["title"])
        for item in ((sources.news() or {}).get("tech") or [])[:3]:
            if ay + 40 > H - 36:
                break
            y0 = ay
            ay = p.para(vx, ay + 2, W - M - vx, item["title"], font("head", 14, 700), leading=17, max_lines=2) + 6
            p.hot((vx, y0, W - M, ay - 2), item)

    footer(p, "front")
    return p


# --- Live tickers -------------------------------------------------------------------------------
# A countdown (or clock) is ticked by the Kindle itself in live mode: it holds the same two-digit images
# (/atlas.tar) and stamps them into these cells each second, so the page never has to be fetched to tick.
LIVE: dict = {}  # view -> {"cells": [(name, x, y)], "mode": ("countdown", epoch) | ("clock", 0)}, set as pages draw
DIGIT_FONT = ("sans", 26, 700)
CELL_W, CELL_H = 17, 30  # one digit's slot; a cell is two


def digit_cell(img, x: int, y: int, two: str):
    """Two digits in fixed slots on white, drawn the same on the page and in the Kindle's digit set."""
    from PIL import ImageDraw

    d = ImageDraw.Draw(img)
    d.rectangle([x, y, x + 2 * CELL_W - 1, y + CELL_H - 1], fill=PAPER)
    for i, ch in enumerate(two):
        d.text((x + i * CELL_W + CELL_W / 2, y + CELL_H / 2), ch, font=font(*DIGIT_FONT), fill=INK, anchor="mm")


def _cells(p: Page, view: str, x: int, yy: int, target: datetime | None) -> int:
    """HH:MM:SS from x: a countdown to target, or the local time. Returns x. The Kindle ticks it in live mode."""
    if target:
        secs = max(0, int((target - datetime.now(timezone.utc)).total_seconds()))
        parts, mode = (secs // 3600 % 100, secs // 60 % 60, secs % 60), ("countdown", int(target.timestamp()))
    else:
        n = now()
        parts, mode = (n.hour, n.minute, n.second), ("clock", 0)
    colon = 9
    x0 = x
    cells = []
    for i, v in enumerate(parts):
        digit_cell(p.img, x, yy, f"{v:02d}")
        cells.append(("hms"[i], x, yy))
        x += 2 * CELL_W
        if i < 2:
            p.text((x + colon / 2, yy + CELL_H / 2 - 2), ":", font(*DIGIT_FONT), INK, anchor="mm")
            x += colon
    LIVE[view] = {"cells": cells, "mode": mode}
    return x0


# Trading hours, local to each exchange. Weekdays only; holidays aren't known.
SESSIONS = {"NYSE": ("America/New_York", (9, 30), (16, 0)), "LONDON": ("Europe/London", (8, 0), (16, 30))}


def bell(exchange: str) -> tuple[str, datetime]:
    """("CLOSES", when) while the exchange is open, else ("OPENS", when)."""
    tz, o, c = SESSIONS[exchange]
    n = datetime.now(ZoneInfo(tz))
    for day in range(8):
        d = (n + timedelta(days=day)).date()
        if d.weekday() >= 5:
            continue
        op = datetime.combine(d, dtime(*o), ZoneInfo(tz))
        cl = datetime.combine(d, dtime(*c), ZoneInfo(tz))
        if n < op:
            return "OPENS", op
        if n < cl:
            return "CLOSES", cl
    return "OPENS", n


def _main_exchange() -> str:
    """The exchange whose bell matters now: one that's open (New York first), else the one opening soonest."""
    bells = {x: bell(x) for x in SESSIONS}
    open_ = [x for x in SESSIONS if bells[x][0] == "CLOSES"]
    return open_[0] if open_ else min(SESSIONS, key=lambda x: bells[x][1])


def bell_ticker(p: Page, view: str, y: int, exchange: str, sub: str = ""):
    """A live countdown to the exchange's next bell at the top right, labelled to its left."""
    what, when = bell(exchange)
    x0 = _cells(p, view, W - M - 6 * CELL_W - 18, y, when)
    p.text((x0 - 10, y + 1), f"{exchange} {what} IN", font("sans", 11, 700), INK, anchor="ra")
    if sub:
        p.text((x0 - 10, y + 16), sub, font("sans", 11, 400), DARK, anchor="ra")


# --- Markets ------------------------------------------------------------------------------------

MARKET_ROWS = 13
KIND_LABEL = {"index": "Index", "fx": "Currency", "crypto": "Crypto", "commodity": "Commodity", "stock": "Share",
              "fund": "Fund"}


def markets_pages() -> int:
    return max(1, -(-len(sources.markets() or []) // MARKET_ROWS))


def markets(ipage: int = 0) -> Page:
    """The watchlist, a page at a time. Each row opens that instrument's own screen."""
    p = Page()
    y = header(p, "Markets")
    mk = sources.markets() or []
    pages = markets_pages()
    ipage = max(0, min(ipage, pages - 1))
    state = next((x["market_state"] for x in mk if x["symbol"] == S.lead_market), "")
    sub = {"REGULAR": "Markets open", "CLOSED": "Closed · last prices", "PRE": "Pre-market"}.get(state, "Latest prices")
    LIVE.pop("markets", None)
    y = section_title(p, y, "Markets")
    bell_ticker(p, "markets", y - 50, _main_exchange(), f"{sub} · page {ipage + 1} of {pages}")

    hf = font("sans", 11, 700)
    p.text((M, y), "INSTRUMENT", hf, MID)
    p.text((330, y), "PRICE", hf, MID, anchor="ra")
    p.text((420, y), "DAY", hf, MID, anchor="ra")
    p.text((W - M, y), "30 DAYS", hf, MID, anchor="ra")
    y += 18
    row = 44
    for i, x in enumerate(mk[ipage * MARKET_ROWS:(ipage + 1) * MARKET_ROWS]):
        if i % 2 == 0:
            p.d.rectangle([M - 4, y, W - M + 4, y + row - 2], fill=242)
        mid = y + row / 2
        p.text((M, mid - 9), x["name"], font("sans", 16, 700), anchor="lm")
        sub = KIND_LABEL.get(x["kind"], "") + f" · {x['symbol']}"
        if x.get("month_pct") is not None:
            sub += f" · {x['month_pct']:+.1f}% 1m"
        p.text((M, mid + 10), sub, font("sans", 11, 400), DARK, anchor="lm")
        p.text((330, mid), price(x), font("sans", 17, 600), anchor="rm")
        change(p, 420, mid, x["change_pct"], size=15)
        p.sparkline((440, y + 8, W - M, y + row - 10), x["series"], width=2)
        p.hot((M, y, W - M, y + row - 2), {"link": f"stock:{x['symbol']}", "title": x["name"]}, tick=False)
        y += row

    # Room left on the last page: business headlines
    if ipage == pages - 1 and y < H - 120:
        p.rule(y + 4)
        y += 14
        y = p.kicker(M, y, "Business headlines")
        for item in (sources.news() or {}).get("business", [])[:4]:
            if y + 40 > H - 34:
                break
            y0 = y
            y = p.para(M, y + 2, W - 2 * M, item["title"], font("head", 16, 700), leading=20, max_lines=2)
            p.text((M, y + 1), f"{item['source']} · {ago(item['when'])}", font("sans", 11, 400), MID)
            y += 20
            p.hot((M, y0, W - M, y - 4), item)

    footer(p, "markets")
    return p


def _with_quote(symbol: str, d: dict | None) -> dict | None:
    """A stock screen's data with the price from the last 10 seconds (the chart itself refreshes each minute)."""
    try:
        q = (sources.quotes() or {}).get(symbol)
    except Exception:
        q = None
    return {**d, "price": q["price"]} if d and q else d


def stock(symbol: str, rng: str = "1M") -> Page:
    """One instrument in depth: price and move, a chart over the chosen range, the day's and 52-week ranges,
    volume, and recent headlines about it."""
    from paper import watchlist

    p = Page()
    y = header(p, "Markets")
    entry = next((i for i in watchlist.items() if i["symbol"] == symbol), {"name": symbol, "kind": "stock"})
    d = _with_quote(symbol, sources.stock(symbol, rng))
    nf = font("head", 30, 800)
    if p.width(entry["name"], nf) > W - 2 * M - 250:  # leave room for the bell countdown
        nf = font("head", 22, 800)
    p.text((M, y + (0 if nf.size == 30 else 6)), entry["name"], nf)
    if d is None:
        p.text((M, y + 48), "No data for this one just now.", font("italic", 16, 400), DARK)
        return p
    kind = entry.get("kind", "stock")
    cur = d["currency"]
    LIVE.pop("stock", None)
    exch = "LONDON" if cur in ("GBp", "GBP") and kind in ("stock", "fund") else "NYSE" if cur == "USD" and kind in ("stock", "fund") else None
    if exch:
        bell_ticker(p, "stock", y + 2, exch, f"{symbol} · {d['exchange']}")
    else:
        p.text((W - M, y + 12), f"{symbol} · {d['exchange']}", font("sans", 12, 400), DARK, anchor="ra")
    y += 46

    # Price and the day's move
    big = money(d["price"], cur, kind)
    bf = font("head", 40, 800)
    p.text((M, y), big, bf)
    x = M + p.width(big, bf) + 16
    # The day's move, as the Markets list shows it (the chart's own previous close depends on the range)
    row = next((x for x in sources.markets() or [] if x["symbol"] == symbol), None)
    if row and row.get("change_pct") is not None:
        d = {**d, "prev_close": d["price"] / (1 + row["change_pct"] / 100)}
    if d.get("prev_close"):
        pct = (d["price"] - d["prev_close"]) / d["prev_close"] * 100
        diff = d["price"] - d["prev_close"]
        change(p, x + 90, y + 24, pct, size=18)
        p.text((x + 100, y + 24), f"{diff:+,.2f} today", font("sans", 13, 400), DARK, anchor="lm")
    state = {"REGULAR": "Market open", "CLOSED": "Market closed", "PRE": "Pre-market", "POST": "After hours"}.get(d["market_state"], "")
    p.text((M, y + 54), state, font("sans", 12, 700), MID)
    y += 78

    # Chart with its range choices (the 5-way left/right moves between them)
    tx = M
    for t in sources.RANGES:
        on = t == rng
        f = font("sans", 13, 700 if on else 400)
        w = p.width(t, f) + 16
        if on:
            p.d.rectangle([tx, y - 2, tx + w, y + 20], fill=INK)
        p.text((tx + 8, y + 9), t, f, PAPER if on else DARK, anchor="lm")
        tx += w + 6
    if d.get("range_pct") is not None:
        p.text((W - M, y + 9), f"{d['range_pct']:+.1f}% over {rng}", font("sans", 13, 700), anchor="rm")
    y += 30
    s = d["series"]
    box = (M + 4, y + 8, W - M - 70, y + 220)
    p.sparkline(box, s, width=2)
    if s:
        p.text((W - M, box[1]), money(max(s), cur, kind), font("sans", 11, 400), DARK, anchor="ra")
        p.text((W - M, box[3]), money(min(s), cur, kind), font("sans", 11, 400), DARK, anchor="rd")
    p.rule(box[3] + 10, fill=MID)
    y = box[3] + 22

    # Where the price sits in the day's and the year's range
    def range_row(y, label, lo, hi):
        p.text((M, y), label, font("sans", 11, 700), MID)
        if lo is None or hi is None or hi <= lo:
            p.text((M + 120, y), "—", font("sans", 13, 400))
            return y + 26
        p.text((M + 120, y - 1), money(lo, cur, kind), font("sans", 12, 400), DARK)
        p.text((W - M, y - 1), money(hi, cur, kind), font("sans", 12, 400), DARK, anchor="ra")
        bx0, bx1 = M + 210, W - M - 90
        p.d.rectangle([bx0, y + 4, bx1, y + 8], fill=LIGHT)
        mx = bx0 + (bx1 - bx0) * max(0, min(1, (d["price"] - lo) / (hi - lo)))
        p.d.rectangle([mx - 2, y - 1, mx + 2, y + 13], fill=INK)
        return y + 26

    y = range_row(y, "TODAY", d.get("day_low"), d.get("day_high"))
    y = range_row(y, "52 WEEKS", d.get("low_52w"), d.get("high_52w"))
    facts = []
    if d.get("prev_close"):
        facts.append(("PREV CLOSE", money(d["prev_close"], cur, kind)))
    if d.get("volume"):
        v = d["volume"]
        facts.append(("VOLUME", f"{v / 1e6:,.1f}m" if v >= 1e6 else f"{v:,}"))
    for i, (label, val) in enumerate(facts):
        fx = M + i * 270
        p.text((fx, y), label, font("sans", 11, 700), MID)
        p.text((fx + 120, y - 1), val, font("sans", 13, 600))
    y += 30 if facts else 0

    # Headlines about it
    news = sources.stock_news(symbol)
    if news and y < H - 110:
        p.double_rule(y)
        y = p.kicker(M, y + 12, "In the news")
        for n in news:
            if y + 40 > H - 34:
                break
            y = p.para(M, y + 2, W - 2 * M, n["title"], font("head", 15, 700), leading=19, max_lines=2)
            p.text((M, y + 1), f"{n['source']} · {ago(n['when'])}", font("sans", 11, 400), MID)
            y += 20
    return p


# --- World --------------------------------------------------------------------------------------

def world() -> Page:
    p = Page()
    y = header(p, "World")
    n = sources.news() or {}
    items = n.get("world") or []
    y = section_title(p, y, "The World", "Geopolitics & foreign news")
    if not items:
        p.text((M, y), "The wires are quiet.", font("italic", 18, 400))
        footer(p, "world")
        return p

    lead = items[0]
    y0 = y
    y = p.kicker(M, y, f"{lead['source']} · {ago(lead['when'])}")
    if p.photo((M, y + 4, W - M, y + 214), lead.get("image")):
        y += 226
    y = p.para(M, y + 2, W - 2 * M, lead["title"], font("head", 26, 800), leading=30, max_lines=3) + 4
    y = p.justified(M, y, W - 2 * M, lead["summary"], font("body", 15, 400), leading=20, max_lines=3) + 8
    p.hot((M, y0, W - M, y - 4), lead)
    p.rule(y)
    y += 10

    # Remaining stories in two columns
    colw = (W - 2 * M - 20) // 2
    cols = [M, M + colw + 20]
    p.vrule(M + colw + 10, y, H - 36, fill=MID)
    ys = [y, y]
    for item in items[1:] + (n.get("home") or [])[:4]:
        c = 0 if ys[0] <= ys[1] else 1
        x, cy = cols[c], ys[c]
        hf, bf = font("head", 16, 700), font("body", 13, 400)
        h_lines = p.wrap(item["title"], hf, colw)[:3]
        need = len(h_lines) * 20 + 2 * 17 + 22
        if cy + need > H - 40:
            ys[c] = H
            if all(v >= H for v in ys):
                break
            continue
        y0 = cy
        cy = p.para(x, cy, colw, item["title"], hf, leading=20, max_lines=3) + 2
        if item["summary"]:
            cy = p.para(x, cy, colw, item["summary"], bf, leading=17, max_lines=2, fill=DARK)
        p.text((x, cy + 2), f"{item['source']} · {ago(item['when'])}", font("sans", 11, 400), MID)
        ys[c] = cy + 24
        p.hot((x, y0, x + colw, cy + 18), item)

    footer(p, "world")
    return p


# --- Numbers --------------------------------------------------------------------------------------

def _trend(p: Page, box, metric: str, days: int = 30):
    """Sparkline of a snapshotted metric, or a note while the history is still building."""
    pts = history.series(metric, days)
    if len(pts) >= 2:
        p.sparkline(box, [v for _, v in pts], width=2)
        f = font("sans", 10, 400)
        p.text((box[0], box[3] + 4), pts[0][0].strftime("%d %b"), f, MID)
        p.text((box[2], box[3] + 4), "today", f, MID, anchor="ra")
    else:
        p.para(box[0], box[1] + 8, box[2] - box[0], "Trend line starts filling in from today.", font("italic", 13, 400),
               leading=17, fill=MID)


def fmt_stat(x: dict) -> str:
    v = x["value"]
    if isinstance(v, float) and not v.is_integer():
        s = f"{v:,.2f}" if abs(v) < 100 else f"{v:,.0f}"
    else:
        s = f"{int(v):,}"
    return f"{s}{x.get('unit') or ''}"


def stats() -> Page:
    """Your own numbers ([[stats]] in the config): each with its 30-day trend from the daily snapshots."""
    p = Page()
    y = header(p, "Numbers")
    y = section_title(p, y, "Numbers", "Thirty days at a glance")
    rows = sources.stats() or []
    if not rows:
        y = p.para(M, y + 4, W - 2 * M, "No numbers configured. Add [[stats]] entries to stop-press.toml: any JSON URL "
                   "and the path to a figure in it (GitHub stars, app installs, a home sensor, your step count).",
                   font("italic", 15, 400), leading=20, fill=DARK)
    for x in rows[:5]:
        if y + 110 > H - 40:
            break
        p.kicker(M, y, x["label"])
        week = history.change(x["key"], 7)
        if week:
            p.text((W - M, y), f"{week:+,.0f} this week".upper(), font("sans", 11, 700), DARK, anchor="ra")
        p.text((M, y + 18), fmt_stat(x) if x["value"] is not None else "—", font("head", 40, 900))
        if x.get("note"):
            p.text((M, y + 68), p.fit(x["note"], font("sans", 12, 500), 220), font("sans", 12, 500), DARK)
        _trend(p, (250, y + 24, W - M, y + 66), x["key"])
        y += 100
        p.rule(y - 6, fill=MID)
        y += 8

    # Whatever room is left: technology headlines
    tech = (sources.news() or {}).get("tech") or []
    if tech and y < H - 110:
        p.double_rule(y)
        y = p.kicker(M, y + 12, S.sections["tech"]["title"])
        for item in tech:
            if y + 42 > H - 34:
                break
            y0 = y
            y = p.para(M, y + 2, W - 2 * M, item["title"], font("head", 16, 700), leading=20, max_lines=2)
            p.text((M, y + 1), f"{item['source']} · {ago(item['when'])}", font("sans", 11, 400), MID)
            y += 20
            p.hot((M, y0, W - M, y - 4), item)

    footer(p, "stats")
    return p


# --- Your Day -----------------------------------------------------------------------------------

def _hm(mins: int) -> str:
    return f"{mins // 60}h {mins % 60:02d}m"


def day() -> Page:
    p = Page()
    y = header(p, "Your Day")
    t = now()
    w = sources.weather()

    p.text((M, y), t.strftime("%A"), font("head", 40, 900))
    p.text((W - M, y + 6), t.strftime("%d %B %Y"), font("head", 22, 700), anchor="ra")
    p.text((W - M, y + 34), f"Day {t.timetuple().tm_yday} · week {t.isocalendar().week}", font("sans", 12, 400), DARK, anchor="ra")
    y += 56
    if w:
        diff = w["daylight_tomorrow_min"] - w["daylight_min"]
        p.text((M, y), f"Sunrise {w['sunrise']}  ·  sunset {w['sunset']}  ·  {_hm(w['daylight_min'])} of daylight "
               f"({'+' if diff >= 0 else '−'}{abs(diff)} min tomorrow)", font("sans", 12, 500), DARK)
    p.double_rule(y + 22)
    y += 34

    # Diary
    y = p.kicker(M, y, "The diary")
    events = sources.calendar()
    if events is None:
        y = p.para(M, y + 2, W - 2 * M, "No calendar connected. Put your calendar's private iCal address in stop-press.toml "
                   "(calendar_ics_url) and your appointments appear here.", font("italic", 15, 400), leading=20, fill=DARK) + 10
    elif not events:
        p.text((M, y + 2), "A clear week. Nothing in the diary.", font("italic", 15, 400), DARK)
        y += 30
    else:
        last_day = None
        for e in events:
            if y + 24 > 470:
                break
            d = e["start"].date()
            if d != last_day:
                label = "Today" if d == t.date() else ("Tomorrow" if (d - t.date()).days == 1 else e["start"].strftime("%A %d %b"))
                p.text((M, y + 4), label.upper(), font("sans", 11, 700), MID)
                y += 18
                last_day = d
            when = "All day" if e["all_day"] else e["start"].strftime("%H:%M")
            if e.get("end") and not e["all_day"]:
                when += "–" + e["end"].strftime("%H:%M")
            p.text((M, y + 3), when, font("sans", 12, 700))
            title = e["title"] + (f" · {e['where']}" if e["where"] else "")
            p.text((M + 96, y + 1), p.fit(title, font("body", 15, 500), W - 2 * M - 96), font("body", 15, 500))
            y += 24
        y += 6
    if not w:
        footer(p, "day")
        return p

    # Weather now + the next 24 hours
    p.double_rule(y)
    y += 12
    p.kicker(M, y, f"Weather · {S.place}")
    p.weather_icon(M + 30, y + 52, 26, w["code"])
    p.text((M + 66, y + 18), f"{w['temp']}°", font("head", 46, 800))
    p.text((M + 66, y + 76), w["summary"], font("sans", 13, 700))
    p.text((M + 66, y + 94), f"feels {w['feels']}° · wind {w['wind']} mph", font("sans", 12, 400), DARK)

    hrs = w.get("hours") or []
    if len(hrs) >= 2:
        cx0, cx1 = 250, W - M
        p.text((cx1, y), "NEXT 24 HOURS", font("sans", 10, 700), MID, anchor="ra")
        temps = [h[1] for h in hrs]
        p.sparkline((cx0, y + 22, cx1, y + 58), temps, width=2, baseline=False)
        p.text((cx0 - 6, y + 16), f"{round(max(temps))}°", font("sans", 10, 700), anchor="ra")
        p.text((cx0 - 6, y + 50), f"{round(min(temps))}°", font("sans", 10, 700), anchor="ra")
        p.bars((cx0, y + 68, cx1, y + 92), [h[2] for h in hrs], fill=DARK, gap=1)
        p.text((cx0 - 6, y + 74), "rain", font("sans", 10, 400), MID, anchor="ra")
        for i, (hh, _, _) in enumerate(hrs):
            if i % 4 == 0:
                hx = cx0 + (i + 0.5) * (cx1 - cx0) / len(hrs)
                p.text((hx, y + 96), hh.strftime("%H:%M"), font("sans", 10, 400), DARK, anchor="ma")
    y += 120

    # The week ahead, one column a day
    p.rule(y, fill=MID)
    y += 10
    days = w["days"][:7]
    cw = (W - 2 * M) / len(days)
    for i, d in enumerate(days):
        cx = M + cw * i + cw / 2
        p.text((cx, y), "Today" if i == 0 else d["date"].strftime("%a"), font("sans", 12, 700), anchor="ma")
        p.weather_icon(cx, y + 36, 13, d["code"])
        p.text((cx, y + 58), f"{d['hi']}°", font("sans", 14, 700), anchor="ma")
        p.text((cx, y + 76), f"{d['lo']}°", font("sans", 12, 400), DARK, anchor="ma")
        p.text((cx, y + 94), f"rain {d['rain']}%", font("sans", 10, 400), MID, anchor="ma")
    y += 116

    # Whatever room is left: home news
    home = (sources.news() or {}).get("home") or []
    if home and y < H - 100:
        p.double_rule(y)
        y = p.kicker(M, y + 12, S.sections["home"]["title"])
        for item in home:
            if y + 42 > H - 34:
                break
            y0 = y
            y = p.para(M, y + 2, W - 2 * M, item["title"], font("head", 16, 700), leading=20, max_lines=2)
            p.text((M, y + 1), f"{item['source']} · {ago(item['when'])}", font("sans", 11, 400), MID)
            y += 20
            p.hot((M, y0, W - M, y - 4), item)
    footer(p, "day")
    return p


PAGES = {"front": front, "markets": markets, "world": world, "stats": stats, "day": day}


def page_for(when: datetime) -> str:
    slot = (when.hour * 60 + when.minute) // max(S.rotate_minutes, 1)
    return S.rotation[slot % len(S.rotation)]
