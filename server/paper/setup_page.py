"""/setup: a plain page for the settings a beginner needs (town, time zone, masthead, calendar) and the address to type
into the e-reader, with a QR code for it. Server-rendered forms; the only script fills in the computer's time zone.
Answers only this computer and the local network, and is switched off on the public demo, which has no settings."""
import ipaddress
import os
import re
import socket
import tomllib
from html import escape
from urllib.parse import quote, urlsplit
from zoneinfo import ZoneInfo

import segno
from fastapi import HTTPException, Request

from paper import config
from paper.config import SETTINGS as S

# Settings that can be changed here. An environment variable (PAPER_<NAME>) beats the file, so a field that has one
# is shown locked: saving it would do nothing.
EDITABLE = ("masthead", "place", "tz", "lat", "lon", "calendar_ics_url", "full_text")

STYLE = """
:root { color-scheme: light dark; --ink: #111; --mid: #666; --line: #ddd; --bg: #fff; --tint: #f3f3f3; --ok: #1d6b2f; --bad: #9b1c1c; }
@media (prefers-color-scheme: dark) { :root { --ink: #eee; --mid: #999; --line: #333; --bg: #111; --tint: #1c1c1c; --ok: #7fd392; --bad: #f19a9a; } }
body { font: 16px/1.4 system-ui, sans-serif; margin: 0 auto; max-width: 640px; padding: 16px; color: var(--ink); background: var(--bg); }
h1 { font: 700 28px Georgia, serif; margin: 0 0 4px; }
h2 { font: 700 20px Georgia, serif; margin: 28px 0 8px; border-bottom: 1px solid var(--line); padding-bottom: 4px; }
p.sub { color: var(--mid); margin: 0 0 12px; }
.note { padding: 10px 12px; border: 1px solid var(--line); border-radius: 8px; margin: 12px 0; background: var(--tint); }
.note.ok { color: var(--ok); } .note.bad { color: var(--bad); }
.address { font: 700 22px ui-monospace, Menlo, Consolas, monospace; word-break: break-all; margin: 8px 0; }
.qr { display: flex; gap: 16px; align-items: center; flex-wrap: wrap; }
.qr svg { width: 180px; height: 180px; background: #fff; padding: 6px; border-radius: 8px; }
label { display: block; font-weight: 600; margin: 14px 0 4px; }
label small { display: block; font-weight: 400; color: var(--mid); }
input[type=text], input[type=number] { width: 100%; box-sizing: border-box; font: inherit; padding: 10px; border: 1px solid var(--line); border-radius: 8px; background: var(--bg); color: var(--ink); }
input[disabled] { color: var(--mid); }
input[type=checkbox] { width: 20px; height: 20px; margin-right: 8px; vertical-align: middle; }
button, a.button { font: inherit; padding: 10px 14px; border: 1px solid var(--line); border-radius: 8px; background: var(--tint); color: var(--ink); text-decoration: none; display: inline-block; }
details { border: 1px solid var(--line); border-radius: 8px; padding: 8px 12px; margin: 8px 0; background: var(--tint); }
summary { font-weight: 600; cursor: pointer; }
details ol, details ul { padding-left: 20px; }
ul.plain { list-style: none; padding: 0; } ul.plain li { padding: 4px 0; }
.grid { display: grid; grid-template-columns: 1fr 1fr; gap: 0 12px; }
@media (max-width: 480px) { .grid { grid-template-columns: 1fr; } }
"""

# Common zones to show as a starting point for a beginner; any IANA name is accepted.
TZ_TIP = "Examples: Europe/London, America/New_York, Australia/Sydney."


def guard(request: Request, post: bool = False) -> None:
    """404 on the demo; 403 for anyone off this computer or the local network. A POST from another website's page
    is refused too, so a page open in the same browser can't change the settings."""
    if S.demo:
        raise HTTPException(status_code=404)
    try:
        ip = ipaddress.ip_address(request.client.host if request.client else "")
    except ValueError:
        raise HTTPException(status_code=403, detail="Setup works from this computer or your home network only.")
    if isinstance(ip, ipaddress.IPv6Address) and ip.ipv4_mapped:
        ip = ip.ipv4_mapped
    if not (ip.is_loopback or ip.is_private or ip.is_link_local):
        raise HTTPException(status_code=403, detail="Setup works from this computer or your home network only.")
    # Behind a reverse proxy or a cloud host every visitor arrives from the proxy's private address, so a forwarded
    # request is from somewhere else entirely: refuse it. Settings on a hosted server belong in its config file.
    if any(h in request.headers for h in ("x-forwarded-for", "forwarded", "x-real-ip")):
        raise HTTPException(status_code=403, detail="Setup works from this computer or your home network only.")
    origin = request.headers.get("origin")
    if post and origin is not None and urlsplit(origin).netloc != request.headers.get("host", ""):
        raise HTTPException(status_code=403, detail="Refused: this request came from another website.")


def lan_ip() -> str | None:
    """This computer's address on the local network. Connecting a UDP socket sends nothing; it only picks the route."""
    try:
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as s:
            s.connect(("192.0.2.1", 9))
            ip = s.getsockname()[0]
        return None if ip.startswith("127.") or ip == "0.0.0.0" else ip
    except OSError:
        return None


def _locked(key: str) -> bool:
    return bool(os.environ.get(f"PAPER_{key.upper()}", "").strip())


def _clean(form: dict) -> dict:
    """The values to save, checked. Raises ValueError with a sentence a beginner can act on."""
    out = {}
    if not _locked("masthead"):
        v = form.get("masthead", "").strip()
        if not 1 <= len(v) <= 60:
            raise ValueError("Give your paper a name (up to 60 letters).")
        out["masthead"] = v
    if not _locked("place"):
        v = form.get("place", "").strip()
        if not 1 <= len(v) <= 60:
            raise ValueError("Enter your town or city (up to 60 letters).")
        out["place"] = v
    if not _locked("tz"):
        v = form.get("tz", "").strip()
        try:
            ZoneInfo(v)
        except (ValueError, KeyError, OSError):
            raise ValueError(f"The time zone “{v}” isn't one I know. {TZ_TIP}")
        out["tz"] = v
    for key, lo, hi, label in (("lat", -90, 90, "Latitude"), ("lon", -180, 180, "Longitude")):
        if not _locked(key):
            try:
                v = float(form.get(key, "").strip())
            except ValueError:
                raise ValueError(f"{label} needs to be a number, for example {'51.5' if key == 'lat' else '-0.13'}.")
            if not lo <= v <= hi:
                raise ValueError(f"{label} must be between {lo} and {hi}.")
            out[key] = round(v, 4)
    if not _locked("calendar_ics_url"):
        v = form.get("calendar_ics_url", "").strip()
        if v.startswith("webcal://"):
            v = "https://" + v[len("webcal://"):]
        if (v and not v.startswith(("https://", "http://"))) or len(v) > 500:
            raise ValueError("The calendar address should start with https:// (or webcal://). Leave it empty for none.")
        out["calendar_ics_url"] = v
    if not _locked("full_text"):
        out["full_text"] = form.get("full_text") == "1"
    return out


def _toml(v) -> str:
    if isinstance(v, bool):
        return "true" if v else "false"
    if isinstance(v, float):
        return repr(v)
    return '"' + str(v).replace("\\", "\\\\").replace('"', '\\"') + '"'


def _write(values: dict) -> None:
    """Replace the managed keys inside the [paper] table and leave everything else in the file (other tables,
    comments) as it is. The new text is checked as TOML before it replaces the file."""
    path = config.CONFIG_PATH
    lines = path.read_text("utf-8").splitlines() if path.exists() else []
    new = [f"{k} = {_toml(v)}" for k, v in values.items()]
    out, in_paper, done = [], False, False
    for line in lines:
        head = line.strip()
        if head.startswith("["):
            if in_paper:
                trailing = []
                while out and not out[-1].strip():
                    trailing.insert(0, out.pop())
                out.extend(new + trailing)
                in_paper, done = False, True
            in_paper = re.match(r"\[paper\]\s*(#.*)?$", head) is not None
        else:
            m = re.match(r"\s*([A-Za-z_]\w*)\s*=", line)
            if in_paper and m and m.group(1) in values:
                continue
        out.append(line)
    if in_paper:
        out.extend(new)
        done = True
    if not done:
        out += ([""] if out else []) + ["[paper]"] + new
    text = "\n".join(out) + "\n"
    try:
        tomllib.loads(text)
    except tomllib.TOMLDecodeError as e:
        raise ValueError(f"Couldn't write the settings file ({e}).")
    path.parent.mkdir(parents=True, exist_ok=True)
    try:
        path.write_text(text, "utf-8")
    except OSError:
        raise ValueError("Couldn't save the settings file. If you run the server in Docker, edit stop-press.toml "
                         "on your computer and restart the container.")


def save(form: dict) -> str:
    """Save the form. Returns an error sentence, or "" when saved and reloaded."""
    try:
        values = _clean(form)
        _write(values)
    except ValueError as e:
        return str(e)
    config.reload()
    from paper import ui

    ui._renders.clear()
    return ""


def _current() -> dict:
    return {"masthead": S.masthead, "place": S.place, "tz": S.tz.key, "lat": S.lat, "lon": S.lon,
            "calendar_ics_url": S.calendar_ics_url, "full_text": S.full_text}


def _field(form: dict, key: str, label: str, hint: str = "", kind: str = "text") -> str:
    val = escape(str(form.get(key, "")), quote=True)
    dis = " disabled" if _locked(key) else ""
    lock = (f'<small>Set by the environment variable PAPER_{key.upper()}, so it can\'t be changed here.</small>'
            if _locked(key) else "")
    if kind == "bool":
        checked = " checked" if form.get(key) in (True, "1", "on") else ""
        return (f'<label><input type="checkbox" name="{key}" value="1"{checked}{dis}>{escape(label)}'
                f'<small>{escape(hint)}</small>{lock}</label>')
    return (f'<label for="{key}">{escape(label)}<small>{escape(hint)}</small></label>'
            f'<input type="text" id="{key}" name="{key}" value="{val}"{dis}>{lock}')


def _address(request: Request) -> tuple[str, str]:
    """The e-reader address (on the local network if we can find it) and the QR code for it."""
    port = request.url.port or 8000
    ip = lan_ip()
    host = f"{ip}:{port}" if ip else f"localhost:{port}"
    url = f"http://{host}/view" + (f"?t={quote(S.token)}" if S.token else "")
    qr = segno.make(url, error="m").svg_inline(scale=5, dark="#000", light="#fff", border=1)
    return url, qr


def render(request: Request, error: str = "", saved: bool = False, form: dict | None = None) -> str:
    from paper import archive, pages, periodical

    values = _current()
    if form is not None:
        values.update({k: form.get(k, "") for k in EDITABLE if k != "full_text"})
        values["full_text"] = form.get("full_text") == "1"
    url, qr = _address(request)
    picture_url = url.split("?")[0].replace("/view", "/kindle.png")
    count = archive.count_today()
    battery = pages.DEVICE.get("battery")
    calibre = "Calibre is installed, so the Kindle daily edition (.mobi) works." if periodical.available() else \
        "Calibre isn't installed, so the Kindle daily edition (.mobi) is off. Everything else works."
    token_note = "" if not S.token else " The address already includes your private token."
    sections = "".join(f"<li>{escape(s['title'])}</li>" for s in S.sections.values())
    notes = ""
    if saved:
        notes += '<p class="note ok">Saved. The paper is using the new settings now.</p>'
    if error:
        notes += f'<p class="note bad">{escape(error)}</p>'
    return f"""<!DOCTYPE html><html lang="en-GB"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1"><title>Set up your paper</title><style>{STYLE}</style></head>
<body><h1>Set up your paper</h1>
<p class="sub">Stop Press makes a small newspaper for old e-readers, phones and tablets. This page is where you
set it up. Changes take effect straight away.</p>
{notes}

<h2>1. Your e-reader's address</h2>
<p>Type this into the device's web browser, or open it on a phone or computer. Your computer and the device need to be
on the same Wi-Fi.</p>
<div class="qr">{qr}<div><div class="address">{escape(url)}</div>
<p>Scan the code with a phone camera to open it on that device.{escape(token_note)}</p>
<p><a class="button" href="/view" target="_blank">Open the paper here</a></p></div></div>

<h2>2. Connect a device</h2>
<details><summary>Kindle 4 (jailbroken, with KUAL)</summary>
<p>The full version: it sleeps between refreshes and lasts weeks on a charge.</p>
<ol><li>Jailbreak the Kindle and install KUAL, following the
<a href="https://github.com/OPISGUY/stop-press#2-set-up-the-kindle-4">README</a>.</li>
<li>Copy the Kindle files from the Stop Press <code>kindle</code> folder over USB, as the README describes.</li>
<li>In the Kindle's <code>env.sh</code>, set the picture address to <code>{escape(picture_url)}</code>
(with <code>?t=…</code> added if you set a token).</li>
<li>Eject the Kindle, then open KUAL, choose Stop Press and Start the paper.</li></ol></details>
<details><summary>Any other browser (Kobo, Boox, other Kindles, phones, tablets)</summary>
<ol><li>Open the address above in the device's browser.</li>
<li>Bookmark it, or add it to the home screen on Android.</li>
<li>Keep the device on a charger: its browser stays awake, so it uses more battery than the Kindle 4.</li></ol></details>

<h2>3. Your paper</h2>
<form method="post" action="/setup/save">
{_field(values, "masthead", "Name of your paper", "Set in blackletter across the front page.")}
{_field(values, "place", "Your town or city", "Used for the weather and the front page's “edition” line.")}
<label for="tz">Time zone<small>{escape(TZ_TIP)}</small></label>
<input type="text" id="tz" name="tz" value="{escape(str(values['tz']), quote=True)}"{' disabled' if _locked('tz') else ''}>
{'<small>Set by the environment variable PAPER_TZ, so it can\'t be changed here.</small>' if _locked('tz') else ''}
<p id="tzhint" class="sub" hidden><button type="button" id="use-tz">Use this computer's time zone</button></p>
<div class="grid">
<div>{_field(values, "lat", "Latitude", "For the weather and sunrise, e.g. 51.5 for London.")}</div>
<div>{_field(values, "lon", "Longitude", "e.g. -0.13 for London. Right-click a place in a map to copy both.")}</div>
</div>
{_field(values, "calendar_ics_url", "Your calendar (optional)", "A private iCal address from Google Calendar or similar, to show your diary on the paper. Leave empty for none.")}
{_field(values, "full_text", "Fetch the full text of articles", "Off shows only each article's summary, and the server does less work.", kind="bool")}
<p><button>Save settings</button></p>
</form>
<p class="sub">News sections on the paper:</p>
<ul class="plain">{sections}</ul>
<p class="sub">Feeds and section names are set in <code>stop-press.toml</code> in the Stop Press data folder.</p>
<p><a class="button" href="/watchlist?t={escape(quote(S.token))}">Edit the markets watchlist</a></p>

<h2>4. Status</h2>
<ul class="plain">
<li>Articles today: {count}{' (the first batch arrives a few minutes after start)' if not count else ''}</li>
<li>Kindle battery: {f'{battery}%' if battery is not None else 'not reported yet'}</li>
<li>{escape(calibre)}</li>
</ul>
<script>
(function () {{
  var tz = (window.Intl && Intl.DateTimeFormat().resolvedOptions().timeZone) || "";
  var box = document.getElementById("tz"), hint = document.getElementById("tzhint"), btn = document.getElementById("use-tz");
  if (!tz || !box || !hint || !btn || box.disabled || box.value === tz) return;
  hint.hidden = false;
  btn.onclick = function () {{ box.value = tz; hint.hidden = true; }};
}})();
</script>
</body></html>"""
