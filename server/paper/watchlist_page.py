"""/watchlist: a plain phone page for choosing what the Markets pages list. Server-rendered forms, no JavaScript."""
from html import escape
from urllib.parse import quote

from paper import sources, watchlist

STYLE = """
:root { color-scheme: light dark; --ink: #111; --mid: #666; --line: #ddd; --bg: #fff; --tint: #f3f3f3; }
@media (prefers-color-scheme: dark) { :root { --ink: #eee; --mid: #999; --line: #333; --bg: #111; --tint: #1c1c1c; } }
body { font: 16px/1.4 system-ui, sans-serif; margin: 0 auto; max-width: 640px; padding: 16px; color: var(--ink); background: var(--bg); }
h1 { font: 700 26px Georgia, serif; margin: 0 0 4px; }
p.sub { color: var(--mid); margin: 0 0 16px; }
form.search { display: flex; gap: 8px; margin-bottom: 16px; }
input[type=text] { flex: 1; font: inherit; padding: 10px; border: 1px solid var(--line); border-radius: 8px; background: var(--bg); color: var(--ink); }
button { font: inherit; padding: 8px 12px; border: 1px solid var(--line); border-radius: 8px; background: var(--tint); color: var(--ink); }
ul { list-style: none; padding: 0; margin: 0 0 24px; }
li { display: flex; align-items: center; gap: 8px; padding: 10px 0; border-bottom: 1px solid var(--line); }
li .what { flex: 1; min-width: 0; }
li .what small { display: block; color: var(--mid); }
li form { margin: 0; }
h2 { font-size: 14px; letter-spacing: .08em; text-transform: uppercase; color: var(--mid); margin: 24px 0 4px; }
"""


def _form(t: str, action: str, fields: dict, label: str) -> str:
    hidden = "".join(f'<input type="hidden" name="{k}" value="{escape(str(v))}">' for k, v in fields.items())
    return f'<form method="post" action="watchlist/{action}?t={quote(t)}">{hidden}<button>{label}</button></form>'


def render(t: str, q: str = "", note: str = "") -> str:
    items = watchlist.items()
    rows = []
    for i, x in enumerate(items):
        rows.append(
            f'<li><span class="what">{escape(x["name"])}<small>{escape(x["symbol"])} · {escape(x["kind"])}</small></span>'
            + (_form(t, "move", {"symbol": x["symbol"], "step": -1}, "↑") if i else "")
            + (_form(t, "move", {"symbol": x["symbol"], "step": 1}, "↓") if i < len(items) - 1 else "")
            + _form(t, "remove", {"symbol": x["symbol"]}, "Remove") + "</li>")
    found = ""
    if q:
        try:
            results = sources.search_symbols(q)
        except Exception:
            results = None
        have = {x["symbol"] for x in items}
        if results is None:
            found = "<p>The search didn't work just now. Try again in a minute.</p>"
        elif not results:
            found = f"<p>Nothing found for “{escape(q)}”.</p>"
        else:
            found = "<h2>Results</h2><ul>" + "".join(
                f'<li><span class="what">{escape(r["name"])}<small>{escape(r["symbol"])} · {escape(r["kind"])}'
                f'{" · " + escape(r["exchange"]) if r["exchange"] else ""}</small></span>'
                + ("<small>On the list</small>" if r["symbol"] in have else
                   _form(t, "add", {"symbol": r["symbol"], "name": r["name"], "kind": r["kind"]}, "Add"))
                + "</li>" for r in results) + "</ul>"
    return f"""<!DOCTYPE html><html><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1"><title>Watchlist</title><style>{STYLE}</style></head>
<body><h1>Markets watchlist</h1>
<p class="sub">What the Markets pages list, in this order ({len(items)} of {watchlist.MAX_ITEMS}). Changes show on the next page turn.</p>
{f'<p><b>{escape(note)}</b></p>' if note else ''}
<form class="search" method="get" action="watchlist"><input type="hidden" name="t" value="{escape(t)}">
<input type="text" name="q" value="{escape(q)}" placeholder="Search: a company, index, currency or crypto"><button>Search</button></form>
{found}
<h2>On the list</h2><ul>{''.join(rows)}</ul>
{_form(t, "reset", {}, "Reset to the default list")}
</body></html>"""
