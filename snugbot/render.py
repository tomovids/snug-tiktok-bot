"""Draws the slide text like TikTok's own text: plain white, no box, a soft shadow, colour emoji."""
from __future__ import annotations

from functools import lru_cache
from itertools import combinations

from PIL import Image, ImageDraw, ImageFilter, ImageFont

from . import config

W, H = 1080, 1920
SKIP = {0xFE0F, 0x200D}


def is_emoji(ch: str) -> bool:
    cp = ord(ch)
    return cp >= 0x1F000 or 0x2600 <= cp <= 0x27BF or 0x2300 <= cp <= 0x23FF or 0x2B00 <= cp <= 0x2BFF


@lru_cache(maxsize=32)
def _font(path: str, size: int) -> ImageFont.FreeTypeFont:
    return ImageFont.truetype(str(config.ROOT / path), size)


@lru_cache(maxsize=64)
def _emoji(path: str, ch: str, height: int) -> Image.Image:
    font = ImageFont.truetype(str(config.ROOT / path), 109)  # the only size the bitmap font has
    box = font.getbbox(ch)
    glyph = Image.new("RGBA", (max(1, box[2] - box[0]), max(1, box[3] - box[1])), (0, 0, 0, 0))
    ImageDraw.Draw(glyph).text((-box[0], -box[1]), ch, font=font, embedded_color=True)
    scale = height / glyph.height
    return glyph.resize((max(1, round(glyph.width * scale)), height), Image.LANCZOS)


def runs(text: str) -> list[tuple[str, str]]:
    out: list[tuple[str, str]] = []
    for ch in text:
        if ord(ch) in SKIP:
            continue
        if is_emoji(ch):
            out.append(("emoji", ch))
        elif out and out[-1][0] == "text":
            out[-1] = ("text", out[-1][1] + ch)
        else:
            out.append(("text", ch))
    return out


class Style:
    def __init__(self, cfg: dict, size: int):
        self.font, self.emoji_font, self.size = cfg["slides"]["font"], cfg["slides"]["emoji_font"], size

    @property
    def emoji_h(self) -> int:
        return round(self.size * 1.06)

    def width(self, text: str) -> float:
        f = _font(self.font, self.size)
        return sum(_emoji(self.emoji_font, v, self.emoji_h).width + self.size * 0.06 if k == "emoji"
                   else f.getlength(v) for k, v in runs(text))


def wrap(text: str, style: Style, max_w: float) -> list[str]:
    lines, line = [], ""
    for word in text.split():
        trial = f"{line} {word}".strip()
        if not line or style.width(trial) <= max_w:
            line = trial
        else:
            lines.append(line)
            line = word
    return lines + ([line] if line else [])


def balance(lines: list[str], style: Style, max_w: float) -> list[str]:
    """Same number of lines, split as evenly as possible (never an emoji alone on a line)."""
    words = " ".join(lines).split()
    n = len(lines)
    if n < 2 or len(words) > 16:
        return lines
    best, best_w = lines, max(style.width(x) for x in lines)
    for cuts in combinations(range(1, len(words)), n - 1):
        b = (0, *cuts, len(words))
        trial = [" ".join(words[i:j]) for i, j in zip(b, b[1:])]
        if any(all(is_emoji(c) or c.isspace() or ord(c) in SKIP for c in x) for x in trial):
            continue
        widest = max(style.width(x) for x in trial)
        if widest <= max_w and widest < best_w - 1:
            best, best_w = trial, widest
    return best


def layout(text: str, cfg: dict, size: int, max_w: float, max_lines: int = 3) -> tuple[Style, list[str]]:
    while True:
        style = Style(cfg, size)
        lines = balance(wrap(text, style, max_w), style, max_w)
        if (len(lines) <= max_lines and max(style.width(x) for x in lines) <= max_w) or size <= 28:
            return style, lines
        size -= 2


def draw(img: Image.Image, text: str, cx: float, cy: float, size: int, cfg: dict, max_w: float = 0.86) -> None:
    """Centred text block around (cx, cy), in place."""
    if not text:
        return
    style, lines = layout(text, cfg, size, W * max_w)
    lh = style.size * 1.2
    top = cy - lh * len(lines) / 2
    shadow = Image.new("RGBA", img.size, (0, 0, 0, 0))
    layer = Image.new("RGBA", img.size, (0, 0, 0, 0))
    f = _font(style.font, style.size)
    for target, colour in ((shadow, (0, 0, 0, 140)), (layer, (255, 255, 255, 255))):
        d = ImageDraw.Draw(target)
        for i, line in enumerate(lines):
            x = cx - style.width(line) / 2
            y = top + i * lh
            for kind, val in runs(line):
                if kind == "emoji":
                    e = _emoji(style.emoji_font, val, style.emoji_h)
                    if target is layer:
                        target.alpha_composite(e, (round(x), round(y + (lh - e.height) / 2 - style.size * 0.05)))
                    x += e.width + style.size * 0.06
                else:
                    d.text((x, y + lh / 2), val, font=f, fill=colour, anchor="lm")
                    x += f.getlength(val)
    base = img.convert("RGBA")
    base.alpha_composite(shadow.filter(ImageFilter.GaussianBlur(4)), (1, 2))
    base.alpha_composite(layer)
    img.paste(base.convert("RGB"))


def callout_spot(box: tuple[float, float, float, float] | None) -> tuple[float, float]:
    """Where a callout goes: just above the piece it's about (box = x0, y0, x1, y1 as 0-1)."""
    if not box:
        return W * 0.5, H * 0.5
    x0, y0, x1, y1 = box
    cx = min(max((x0 + x1) / 2 * W, W * 0.3), W * 0.7)
    cy = y0 * H - 34 if y0 * H > 330 else (y0 + y1) / 2 * H
    return cx, min(max(cy, H * 0.18), H * 0.76)


def slides_for(plan, before: Image.Image, looks: list[Image.Image], cta: Image.Image, boxes: list, cfg: dict) -> list[Image.Image]:
    """All 8 finished slides for a plan (the pictures in the plan's order)."""
    s = cfg["slides"]
    out = []
    first = before.copy()
    draw(first, plan.hook, W / 2, H * (0.30 if plan.format == "makeover" else 0.22), s["hook_size"], cfg)
    draw(first, plan.hook_sub, W / 2, H * 0.86, s["label_size"], cfg)
    out.append(first)
    for slide, img, box in zip(plan.slides, looks, boxes):
        img = img.copy()
        draw(img, slide.text, W / 2, H * 0.87, s["label_size"], cfg)
        if slide.callout:
            cx, cy = callout_spot(box)
            draw(img, slide.callout, cx, cy, s["callout_size"], cfg, max_w=0.6)
        out.append(img)
    last = cta.copy()
    draw(last, plan.cta_question, W / 2, H * 0.22, s["cta_size"], cfg)
    draw(last, plan.cta_line, W / 2, H * 0.84, s["cta_size"], cfg)
    out.append(last)
    return out
