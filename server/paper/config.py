"""Settings. Everything has a sensible default; set what you like in stop-press.toml (path in STOP_PRESS_CONFIG,
default ./stop-press.toml) and/or environment variables, which win. See stop-press.example.toml for every option."""
import os
import tomllib
from dataclasses import dataclass, field, fields
from pathlib import Path
from zoneinfo import ZoneInfo

_path = Path(os.environ.get("STOP_PRESS_CONFIG", "stop-press.toml"))
CONFIG_PATH = _path  # the /setup page writes here
FILE: dict = tomllib.loads(_path.read_text("utf-8")) if _path.exists() else {}


def _flag(key: str, default: bool) -> bool:
    v = _get(key, default)
    return v if isinstance(v, bool) else str(v).lower() in ("1", "true", "yes", "on")


def _get(key: str, default=""):
    """PAPER_<KEY> from the environment, else `key` in the [paper] table of the config file, else the default."""
    env = os.environ.get(f"PAPER_{key.upper()}")
    if env is not None and env.strip():
        return env.strip()
    return FILE.get("paper", {}).get(key, default)


# The four news sections every page draws on. Rename them and point them at any RSS/Atom feeds in [sections.*].
DEFAULT_SECTIONS = {
    "world": {"title": "The World", "feeds": [["BBC", "https://feeds.bbci.co.uk/news/world/rss.xml"],
                                              ["Guardian", "https://www.theguardian.com/world/rss"],
                                              ["Al Jazeera", "https://www.aljazeera.com/xml/rss/all.xml"]]},
    "home": {"title": "Britain", "feeds": [["BBC", "https://feeds.bbci.co.uk/news/uk/rss.xml"]]},
    "business": {"title": "Business", "feeds": [["BBC", "https://feeds.bbci.co.uk/news/business/rss.xml"],
                                                ["Guardian", "https://www.theguardian.com/uk/business/rss"]]},
    "tech": {"title": "Technology", "feeds": [["BBC", "https://feeds.bbci.co.uk/news/technology/rss.xml"]]},
}

# Yahoo Finance symbols: (symbol, name, kind), kind one of index, fx, crypto, commodity.
DEFAULT_MARKETS = [
    ["^FTSE", "FTSE 100", "index"], ["^FTMC", "FTSE 250", "index"], ["^GSPC", "S&P 500", "index"],
    ["^IXIC", "Nasdaq", "index"], ["^N225", "Nikkei 225", "index"], ["^GDAXI", "DAX", "index"],
    ["GBPUSD=X", "GBP/USD", "fx"], ["GBPEUR=X", "GBP/EUR", "fx"], ["BTC-USD", "Bitcoin", "crypto"],
    ["GC=F", "Gold", "commodity"], ["BZ=F", "Brent crude", "commodity"], ["^VIX", "VIX (fear)", "index"],
]


def _sections() -> dict:
    out = {k: dict(v) for k, v in DEFAULT_SECTIONS.items()}
    for k, v in FILE.get("sections", {}).items():
        if k in out:
            out[k].update(v)
    return out


@dataclass(frozen=True)
class Settings:
    masthead: str = field(default_factory=lambda: _get("masthead", "Stop Press"))
    tz: ZoneInfo = field(default_factory=lambda: ZoneInfo(_get("tz", "Europe/London")))
    place: str = field(default_factory=lambda: _get("place", "London"))
    lat: float = field(default_factory=lambda: float(_get("lat", 51.507)))
    lon: float = field(default_factory=lambda: float(_get("lon", -0.128)))
    # Where the day's archive, reading progress and stat history live. Mount a volume here.
    data_dir: Path = field(default_factory=lambda: Path(_get("data_dir", "/data")))

    # Shared secret the Kindle sends as ?t=. Empty means no check (fine on a home network, not on the internet).
    token: str = field(default_factory=lambda: _get("token"))

    # A private iCal address (Google Calendar: Settings > your calendar > "Secret address in iCal format"). Optional.
    calendar_ics_url: str = field(default_factory=lambda: _get("calendar_ics_url"))

    # Fetch each article's full text from the publisher's page. Off shows the feed's summary instead.
    full_text: bool = field(default_factory=lambda: _flag("full_text", True))
    # Public demo: a reading state per browser, and no offline copy, edition or battery reports.
    demo: bool = field(default_factory=lambda: _flag("demo", False))

    sections: dict = field(default_factory=_sections)
    markets: list = field(default_factory=lambda: FILE.get("markets", {}).get("instruments", DEFAULT_MARKETS))
    # Market quoted in the front-page note and whose open/closed state the Markets page reports.
    lead_market: str = field(default_factory=lambda: FILE.get("markets", {}).get("lead", "^FTSE"))
    # The (up to eight) shown in the front page's markets column.
    front_markets: list = field(default_factory=lambda: FILE.get("markets", {}).get(
        "front", ["^FTSE", "^FTMC", "^GSPC", "^IXIC", "GBPUSD=X", "BTC-USD", "GC=F", "BZ=F"]))

    # Your own numbers (see [[stats]] in the example config): each is a JSON URL and a path to the figure in it.
    stats: list = field(default_factory=lambda: FILE.get("stats", []))

    # Optional AI editor's note: any OpenAI-compatible chat endpoint (OpenAI, Ollama, LM Studio, OpenRouter...).
    # Empty base URL means no AI at all: the note is a plain sentence built from the headlines.
    ai_base_url: str = field(default_factory=lambda: os.environ.get("AI_BASE_URL") or FILE.get("ai", {}).get("base_url", ""))
    ai_api_key: str = field(default_factory=lambda: os.environ.get("AI_API_KEY") or FILE.get("ai", {}).get("api_key", ""))
    ai_model: str = field(default_factory=lambda: os.environ.get("AI_MODEL") or FILE.get("ai", {}).get("model", ""))

    # Pages the Kindle steps through while asleep, one per refresh. The front page comes round every other time.
    rotation: tuple = field(default_factory=lambda: tuple(str(_get("rotation", "front")).split(",")))
    rotate_minutes: int = field(default_factory=lambda: int(_get("rotate_minutes", 60)))  # matches the Kindle's refresh


SETTINGS = Settings()


def reload() -> None:
    """Re-read the config file and environment into the SETTINGS object the rest of the server already holds, so
    the /setup page takes effect without a restart. Settings is frozen, so each field is set directly."""
    global FILE
    FILE = tomllib.loads(CONFIG_PATH.read_text("utf-8")) if CONFIG_PATH.exists() else {}
    fresh = Settings()
    for f in fields(Settings):
        object.__setattr__(SETTINGS, f.name, getattr(fresh, f.name))
