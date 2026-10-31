"""Drawing toolkit for a 600x800, 16-grey e-ink page. Everything is black on white with a couple of greys;
fine detail below ~1px disappears on the Kindle 4, so rules are at least 1px and text at least 12px."""
from functools import lru_cache
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

W, H = 600, 800
M = 20  # outer margin
INK, DARK, MID, LIGHT, PAPER = 0, 68, 136, 204, 255

FONT_DIR = Path(__file__).resolve().parent.parent / "fonts"
FAMILIES = {
    "masthead": "UnifrakturMaguntia.ttf",
    "head": "PlayfairDisplay.ttf",
    "body": "SourceSerif4.ttf",
    "italic": "SourceSerif4-Italic.ttf",
    "sans": "Inter.ttf",
}


@lru_cache(maxsize=256)
def font(family: str, size: int, weight: int = 400) -> ImageFont.FreeTypeFont:
    f = ImageFont.truetype(str(FONT_DIR / FAMILIES[family]), size)
    try:
        axes = f.get_variation_axes()
    except OSError:
        return f  # static font
    values = []
    for a in axes:
        name = a["name"].decode() if isinstance(a["name"], bytes) else a["name"]
        if name.lower().startswith("weight"):
            values.append(max(a["minimum"], min(a["maximum"], weight)))
        elif name.lower().startswith("optical"):
            values.append(max(a["minimum"], min(a["maximum"], size)))
        else:
            values.append(a["default"])
    f.set_variation_by_axes(values)
    return f


READ: set[str] = set()  # links already opened on the Kindle; ui.py fills it, headlines get a tick


class Page:
    def __init__(self):
        self.img = Image.new("L", (W, H), PAPER)
        self.d = ImageDraw.Draw(self.img)
        self.hotspots: list[tuple[tuple, dict]] = []  # (box, article) the 5-way can select, in reading order

    def hot(self, box, item: dict, tick: bool = True):
        if item.get("link"):
            self.hotspots.append((box, item))
            if tick and item["link"] in READ:
                self.tick(box[0] - 14, box[1] + 4)

    def highlight(self, box):
        """Selection marker: a heavy frame and a tab in the margin, both survive a partial e-ink refresh."""
        x0, y0, x1, y1 = box
        self.d.rectangle([x0 - 6, y0 - 4, x1 + 6, y1 + 4], outline=INK, width=3)
        self.d.rectangle([0, y0 - 4, 7, y1 + 4], fill=INK)

    # -- text -----------------------------------------------------------------------------------

    def text(self, xy, s, f, fill=INK, anchor="la"):
        self.d.text(xy, s, font=f, fill=fill, anchor=anchor)

    def width(self, s, f) -> float:
        return self.d.textlength(s, font=f)

    def fit(self, s: str, f, max_w: float) -> str:
        """Truncate to max_w with an ellipsis."""
        if self.width(s, f) <= max_w:
            return s
        while s and self.width(s + "…", f) > max_w:
            s = s[:-1]
        return s.rstrip(" ,;:-") + "…"

    def wrap(self, s: str, f, max_w: float) -> list[str]:
        lines, line = [], ""
        for word in s.split():
            trial = f"{line} {word}".strip()
            if self.width(trial, f) <= max_w:
                line = trial
            else:
                if line:
                    lines.append(line)
                line = word if self.width(word, f) <= max_w else self.fit(word, f, max_w)
        if line:
            lines.append(line)
        return lines

    def para(self, x, y, w, s, f, leading=None, max_lines=None, fill=INK, align="left") -> int:
        """Draw wrapped text; returns the y just below it."""
        leading = leading or int(f.size * 1.3)
        lines = self.wrap(s, f, w)
        if max_lines and len(lines) > max_lines:
            lines = lines[:max_lines]
            lines[-1] = self.fit(lines[-1] + " …", f, w) if not lines[-1].endswith("…") else lines[-1]
            if not lines[-1].endswith("…"):
                lines[-1] = self.fit(lines[-1] + "…", f, w)
        for i, line in enumerate(lines):
            if align == "center":
                self.text((x + w / 2, y + i * leading), line, f, fill, anchor="ma")
            else:
                self.text((x, y + i * leading), line, f, fill)
        return y + len(lines) * leading

    def justified(self, x, y, w, s, f, leading=None, max_lines=None, fill=INK) -> int:
        """Newspaper-style justified paragraph (last line ragged)."""
        leading = leading or int(f.size * 1.3)
        lines = self.wrap(s, f, w)
        if max_lines and len(lines) > max_lines:
            lines = lines[:max_lines]
            lines[-1] = self.fit(lines[-1] + "…", f, w)
        for i, line in enumerate(lines):
            words = line.split()
            last = i == len(lines) - 1
            if last or len(words) == 1:
                self.text((x, y + i * leading), line, f, fill)
                continue
            gap = (w - sum(self.width(wd, f) for wd in words)) / (len(words) - 1)
            if gap > self.width(" ", f) * 3:  # too loose: leave ragged
                self.text((x, y + i * leading), line, f, fill)
                continue
            cx = x
            for wd in words:
                self.text((cx, y + i * leading), wd, f, fill)
                cx += self.width(wd, f) + gap
        return y + len(lines) * leading

    def kicker(self, x, y, s, fill=INK) -> int:
        """Small spaced capitals used as section labels."""
        f = font("sans", 12, 700)
        cx = x
        for ch in s.upper():
            self.text((cx, y), ch, f, fill)
            cx += self.width(ch, f) + 1.6
        return y + 16

    # -- rules and shapes ----------------------------------------------------------------------

    def rule(self, y, x0=M, x1=W - M, w=1, fill=INK):
        self.d.rectangle([x0, y, x1, y + w - 1], fill=fill)

    def double_rule(self, y, x0=M, x1=W - M):
        self.rule(y, x0, x1, 3)
        self.rule(y + 5, x0, x1, 1)

    def vrule(self, x, y0, y1, fill=INK):
        self.d.rectangle([x, y0, x, y1], fill=fill)

    def box(self, xy, outline=INK, fill=None, w=1):
        self.d.rectangle(xy, outline=outline, fill=fill, width=w)

    def triangle(self, x, y, size, up: bool, fill=INK):
        """Up/down change marker, x,y = centre."""
        h = size * 0.85
        if up:
            pts = [(x - size / 2, y + h / 2), (x + size / 2, y + h / 2), (x, y - h / 2)]
        else:
            pts = [(x - size / 2, y - h / 2), (x + size / 2, y - h / 2), (x, y + h / 2)]
        self.d.polygon(pts, fill=fill)

    def sparkline(self, box, series, width=2, baseline=True):
        x0, y0, x1, y1 = box
        s = [v for v in series if v is not None]
        if len(s) < 2:
            return
        lo, hi = min(s), max(s)
        span = (hi - lo) or 1
        pts = [(x0 + i * (x1 - x0) / (len(s) - 1), y1 - (v - lo) / span * (y1 - y0)) for i, v in enumerate(s)]
        if baseline:
            # dotted line at the first value, so the eye sees up/down over the period
            by = y1 - (s[0] - lo) / span * (y1 - y0)
            for x in range(int(x0), int(x1), 4):
                self.d.point((x, by), fill=MID)
        self.d.line(pts, fill=INK, width=width, joint="curve")
        lx, ly = pts[-1]
        self.d.ellipse([lx - 2.5, ly - 2.5, lx + 2.5, ly + 2.5], fill=INK)

    def bar(self, box, frac, fill=INK, bg=LIGHT):
        x0, y0, x1, y1 = box
        self.d.rectangle(box, fill=bg)
        self.d.rectangle([x0, y0, x0 + (x1 - x0) * max(0.0, min(1.0, frac)), y1], fill=fill)

    def bars(self, box, values, fill=INK, gap=2, labels: list[str] | None = None, f=None):
        """Column chart for daily counts; zero days get a hairline so the gaps read as 'none', not 'missing'."""
        x0, y0, x1, y1 = box
        if not values:
            return
        hi = max(max(values), 1)
        bw = (x1 - x0) / len(values)
        for i, v in enumerate(values):
            bx = x0 + i * bw
            top = y1 - (v / hi) * (y1 - y0)
            self.d.rectangle([bx + gap / 2, min(top, y1 - 1), bx + bw - gap / 2, y1], fill=fill if v else MID)
            if labels and labels[i] and f is not None:
                self.text((bx + bw / 2, y1 + 3), labels[i], f, DARK, anchor="ma")

    def battery(self, x_right, y, pct: int):
        """Small battery gauge ending at x_right, with the percentage to its left."""
        bw, bh = 22, 11
        x0 = x_right - bw - 2
        self.d.rectangle([x0, y, x0 + bw, y + bh], outline=INK, width=1)
        self.d.rectangle([x0 + bw + 1, y + 3, x0 + bw + 2, y + bh - 3], fill=INK)
        self.d.rectangle([x0 + 2, y + 2, x0 + 2 + (bw - 4) * max(0, min(100, pct)) / 100, y + bh - 2], fill=INK)
        return x0 - 4  # where the text can end

    def photo(self, box, url: str | None) -> bool:
        """A dithered news photo filling box, with a hairline frame. False (nothing drawn) if it can't be had."""
        from paper import photos

        x0, y0, x1, y1 = (int(v) for v in box)
        img = photos.fit(url, x1 - x0, y1 - y0)
        if img is None:
            return False
        self.img.paste(img, (x0, y0))
        self.d.rectangle([x0, y0, x1 - 1, y1 - 1], outline=INK, width=1)
        return True

    def tick(self, x, y, size=9, fill=MID):
        """'Already read' mark."""
        self.d.line([(x, y + size * 0.55), (x + size * 0.38, y + size), (x + size, y)], fill=fill, width=2)

    # -- weather icons (simple line drawings that survive e-ink) --------------------------------

    def weather_icon(self, cx, cy, r, code: int):
        d = self.d
        def sun(x, y, rr):
            d.ellipse([x - rr * 0.45, y - rr * 0.45, x + rr * 0.45, y + rr * 0.45], outline=INK, width=2)
            import math
            for k in range(8):
                a = k * math.pi / 4
                d.line([(x + math.cos(a) * rr * 0.65, y + math.sin(a) * rr * 0.65),
                        (x + math.cos(a) * rr * 0.9, y + math.sin(a) * rr * 0.9)], fill=INK, width=2)
        def cloud(x, y, rr, fill=PAPER):
            parts = [(x - rr * 0.45, y + rr * 0.05, rr * 0.38), (x + rr * 0.05, y - rr * 0.2, rr * 0.5), (x + rr * 0.5, y + rr * 0.1, rr * 0.33)]
            for px, py, pr in parts:
                d.ellipse([px - pr, py - pr, px + pr, py + pr], fill=INK)
            d.rectangle([x - rr * 0.45, y + rr * 0.05, x + rr * 0.5, y + rr * 0.43], fill=INK)
            for px, py, pr in parts:
                d.ellipse([px - pr + 2, py - pr + 2, px + pr - 2, py + pr - 2], fill=fill)
            d.rectangle([x - rr * 0.45, y + rr * 0.05, x + rr * 0.5, y + rr * 0.43 - 2], fill=fill)
        def drops(x, y, rr, snow=False):
            for k in range(3):
                px = x - rr * 0.35 + k * rr * 0.35
                if snow:
                    d.text((px, y + rr * 0.75), "*", font=font("sans", int(rr * 0.6), 700), fill=INK, anchor="mm")
                else:
                    d.line([(px, y + rr * 0.55), (px - rr * 0.12, y + rr * 0.9)], fill=INK, width=2)
        if code in (0, 1):
            sun(cx, cy, r)
        elif code == 2:
            sun(cx - r * 0.3, cy - r * 0.3, r * 0.75)
            cloud(cx + r * 0.1, cy + r * 0.1, r * 0.8)
        elif code in (45, 48):
            for k in range(4):
                d.line([(cx - r * 0.7, cy - r * 0.45 + k * r * 0.3), (cx + r * 0.7, cy - r * 0.45 + k * r * 0.3)], fill=INK, width=2)
        elif code in (71, 73, 75, 77, 85, 86):
            cloud(cx, cy - r * 0.2, r)
            drops(cx, cy - r * 0.2, r, snow=True)
        elif code >= 95:
            cloud(cx, cy - r * 0.25, r)
            d.polygon([(cx, cy + r * 0.25), (cx - r * 0.2, cy + r * 0.6), (cx, cy + r * 0.6), (cx - r * 0.15, cy + r * 0.95),
                       (cx + r * 0.25, cy + r * 0.5), (cx + r * 0.05, cy + r * 0.5), (cx + r * 0.15, cy + r * 0.25)], fill=INK)
        elif code >= 51:
            cloud(cx, cy - r * 0.2, r)
            drops(cx, cy - r * 0.2, r)
        else:
            cloud(cx, cy, r)
