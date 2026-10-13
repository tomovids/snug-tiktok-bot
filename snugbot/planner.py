"""Plans a post: picks the format, room, style or problem, and writes every slide's words and look.

The writer (Gemini) gets the content guide (data/brand.md), the post's style or problem, the room and
the account's recent hooks, and returns the hook, 6 restyles (a label or line + what the room looks
like) and the last slide. Rule checks reject anything too long, off-brand or repeated; after three
tries a plain, safe plan is used so a post always goes out.
"""
from __future__ import annotations

import random
import re
import tomllib
from dataclasses import asdict, dataclass, field
from datetime import date

from . import config
from .gemini import Gemini, GeminiError
from .history import History

BANNED = ["spatial", "i did this", "i made this", "made with snug", "made on snug", "did this on snug",
          "cheap", "dupe", "ikea", "amazon", "wayfair", "h&m", "zara home", "west elm", "target"]


def load_data() -> dict:
    with open(config.DATA / "styles.toml", "rb") as f:
        return tomllib.load(f)


def guide() -> str:
    return (config.DATA / "brand.md").read_text(encoding="utf-8")


@dataclass
class Slide:
    text: str              # format A: the style label; format B: the opinion line
    look: str              # what the room looks like (for the picture)
    callout: str = ""      # format A: written after the picture is made, about a piece that's really in it


@dataclass
class Plan:
    format: str            # "makeover" or "problem"
    room: str              # "living room", "bedroom", "kitchen"
    theme: str             # style id or problem id
    theme_name: str
    hook: str
    hook_sub: str
    slides: list[Slide]
    cta_look: str
    cta_question: str
    cta_line: str
    title: str
    caption: str
    source: str = "ai"
    room_id: str = ""      # which "before" room from the library
    extra: dict = field(default_factory=dict)

    def to_dict(self) -> dict:
        return asdict(self)

    @classmethod
    def from_dict(cls, d: dict) -> "Plan":
        d = dict(d)
        d["slides"] = [Slide(**s) for s in d["slides"]]
        return cls(**d)


# ---- what the post is about ------------------------------------------------------------------------

def _last_used(hist: History, key: str) -> dict[str, date]:
    out: dict[str, date] = {}
    for p in hist.done():
        v = p.get(key)
        if v:
            d = date.fromisoformat(p["date"])
            out[v] = max(out.get(v, d), d)
    return out


def choose(fmt: str, day: date, hist: History, cfg: dict, rng: random.Random,
           taken: set[str] = frozenset()) -> tuple[str, dict, str]:
    """(format, style or problem, room): least recently used, resting `*_rest_days` if possible, and
    not the same room type as another post that day if there's a choice.
    `taken`: themes already planned for other posts in this run."""
    same_day = {p.get("room") for p in hist.done() if p.get("date") == day.isoformat()}
    data = load_data()
    rot = cfg["rotation"]
    if fmt == "any":
        last = _last_used(hist, "format")
        fmt = min(("makeover", "problem"), key=lambda f: last.get(f, date.min))
    items = data["styles"] if fmt == "makeover" else data["problems"]
    rest = rot["style_rest_days"] if fmt == "makeover" else rot["problem_rest_days"]
    last = _last_used(hist, "theme")
    pool = [i for i in items if i["id"] not in taken] or items
    pool = [i for i in pool if set(i["rooms"]) - same_day] or pool
    rested = [i for i in pool if i["id"] not in last or (day - last[i["id"]]).days >= rest]
    pool = rested or pool
    oldest = min(last.get(i["id"], date.min) for i in pool)
    item = rng.choice([i for i in pool if last.get(i["id"], date.min) == oldest])
    room_last = _last_used(hist, "room")
    rooms = [r for r in item["rooms"] if r not in same_day] or item["rooms"]
    room = min(rooms, key=lambda r: (room_last.get(r, date.min), rng.random()))
    return fmt, item, room


# ---- writing ---------------------------------------------------------------------------------------

MOVABLE = ("Every look describes ONLY movable things (furniture, rugs, textiles, cushions, lamps and light fittings, "
           "art, mirrors, plants, books, decor): never walls, wall color, brick, concrete, panelling, beams, floors, "
           "ceilings, windows or curtains, because the room itself stays exactly the same in every picture.")


KITCHEN = (" In a kitchen the cabinet color, handles, worktops, splashback, lights, stools and open shelves CAN "
           "change and each look should make a big, visible change to them (the layout stays).")


def movable(room: str) -> str:
    return MOVABLE + (KITCHEN if room == "kitchen" else "")


def _prompt(fmt: str, item: dict, room: str, recent: list[str], cfg: dict) -> str:
    n = cfg["slides"]["restyles"]
    s = cfg["slides"]
    rec = "\n".join(f"- {h}" for h in recent[-30:]) or "- (none yet)"
    if fmt == "makeover":
        what = f"""Format A for an empty {room}. Style: {item['name']} ({item['notes']}).
- "hook": exactly "let's make this {item['name']} ✍️" or a close variant ("let's make this {room} {item['name']} ✍️",
  "turning this into a {item['name']} {room} ✍️"); at most {s['max_hook_chars']} characters, lowercase.
- {n} "slides", each {{"text": a 2-5 word lowercase style label that ends with "{item['name']}", in the spirit
  of "warm nostalgic mid-century" or "retro glam mid-century" but with your OWN words that fit {item['name']}
  (don't reuse "warm nostalgic", "moody and refined" or "retro glam"); {n} clearly different "look": 25-45 words describing how the {room} is furnished and decorated in that version: the main
  furniture, materials, colors, lighting mood, art and decor}}. {movable(room)}
- "cta_look": one more version of the room (25-45 words) for the last slide.
- "cta_question": "Have any Pinterest inspo you want to try? 📌" or a close variant.
- "title": "{item['name']} transformation 📌" or a close variant (max 40 characters)."""
    else:
        what = f"""Format B for a lived-in {room}. Problem: {item['phrase']} ({item['room_look']}).
- "hook": exactly "i'm done with {item['phrase']}! 😅" or a close variant in the same voice; at most
  {s['max_hook_chars']} characters, lowercase.
- {n} "slides", each {{"text": ONE personal, opinionated lowercase line, 3-9 words, about that version, in the
  spirit of "the deep blue makes pink feel grown up", "could I commit this much to color? 🫢" or "a person well
  travelled" but your OWN words about what's in that version (never copy these examples), at most {s['max_line_chars']} characters, "look": 25-45 words
  describing a colorful, characterful restyle of the same {room}: furniture, colors, textiles, art, lighting}}. {movable(room)}
  Make the {n} versions really different from each other (palettes and moods).
- "cta_look": one more restyle (25-45 words) for the last slide.
- "cta_question": "{item['cta']}" or a close variant.
- "title": a short TikTok title like "goodbye {item['id']} 👋" (max 40 characters)."""
    return f"""Write one TikTok photo carousel for Snug.

{what}
- "caption_intro": one short lowercase caption line that asks people to comment their pick (max 90 characters).

Never repeat or closely copy these recent hooks:
{rec}

Use only these emoji: {s['emoji']}. No hashtags (added later).
Return JSON: {{"hook": "...", "slides": [{{"text": "...", "look": "..."}}, ...], "cta_look": "...",
"cta_question": "...", "title": "...", "caption_intro": "..."}}"""


def problems(d: dict, fmt: str, item: dict, recent: list[str], cfg: dict) -> list[str]:
    s = cfg["slides"]
    out = []
    hook = str(d.get("hook", "")).strip()
    if not hook:
        return ["no hook"]
    if len(hook) > s["max_hook_chars"]:
        out.append(f"the hook is {len(hook)} characters; max {s['max_hook_chars']}")
    slides = d.get("slides") or []
    if len(slides) != s["restyles"]:
        out.append(f"need exactly {s['restyles']} slides")
    texts = [str(x.get("text", "")).strip() for x in slides]
    for t in texts:
        if not t or len(t) > s["max_line_chars"]:
            out.append(f"slide text '{t}' must be 1-{s['max_line_chars']} characters")
        if fmt == "makeover" and not t.lower().rstrip(" .!").endswith(item["name"].lower()):
            out.append(f"label '{t}' must end with '{item['name']}'")
    if len({t.lower() for t in texts}) < len(texts):
        out.append("two slides have the same text")
    for x in slides:
        if len(str(x.get("look", "")).split()) < 12:
            out.append("every slide needs a 25-45 word look")
            break
    allowed = set(s["emoji"])
    every = " ".join([hook, *texts, str(d.get("cta_question", "")), str(d.get("caption_intro", "")),
                      str(d.get("title", ""))])
    if any(_is_emoji(c) and c not in allowed and c not in "️‍" for c in every):
        out.append("use only these emoji: " + s["emoji"])
    low = every.lower()
    bad = [b for b in BANNED if re.search(rf"(?<![a-z]){re.escape(b)}(?![a-z])", low)]
    if bad:
        out.append("don't say: " + ", ".join(bad))
    if _norm(hook) in {_norm(h) for h in recent}:
        out.append("that hook was used recently")
    return out


def _is_emoji(ch: str) -> bool:
    cp = ord(ch)
    return cp >= 0x1F000 or 0x2600 <= cp <= 0x27BF or 0x2300 <= cp <= 0x23FF or 0x2B00 <= cp <= 0x2BFF


def _norm(t: str) -> str:
    return re.sub(r"[^a-z0-9]+", "", t.lower())


def write(fmt: str, item: dict, room: str, hist: History, cfg: dict, ai: Gemini | None,
          rng: random.Random, log=print) -> Plan:
    recent = hist.recent_hooks(60)
    d = None
    if ai is not None:
        prompt = _prompt(fmt, item, room, recent, cfg)
        feedback = ""
        for attempt in range(3):
            try:
                d = ai.text_json(prompt + feedback, system=guide())
            except (GeminiError, ValueError) as e:
                log(f"Writer unavailable ({e}); using a plain plan.")
                d = None
                break
            found = problems(d, fmt, item, recent, cfg)
            if not found:
                break
            log(f"  writer attempt {attempt + 1}: {'; '.join(found)}")
            feedback = f"\n\nYour last answer broke these rules: {'; '.join(found)}. Write it again."
            d = None
    source = "ai" if d else "fallback"
    d = d or fallback(fmt, item, room, cfg, rng)
    tags = list(dict.fromkeys(cfg["caption"]["hashtags"] + ([item["tag"]] if item.get("tag") else [])
                              + [cfg["caption"]["brand_tag"]]))[: cfg["caption"]["max_hashtags"]]
    title = str(d.get("title") or "").strip()[:60]
    caption = "\n\n".join(x for x in [title, str(d.get("caption_intro") or "").strip(), " ".join(tags)] if x)
    return Plan(
        format=fmt, room=room, theme=item["id"], theme_name=item.get("name") or item.get("phrase"),
        hook=d["hook"].strip(), hook_sub="(inspired)" if fmt == "makeover" else "which would you choose?",
        slides=[Slide(text=str(x["text"]).strip(), look=str(x["look"]).strip()) for x in d["slides"]],
        cta_look=str(d.get("cta_look") or d["slides"][0]["look"]).strip(),
        cta_question=str(d.get("cta_question") or "Have any Pinterest inspo you want to try? 📌").strip(),
        cta_line=rng.choice(cfg["slides"]["cta_lines"]), title=title, caption=caption, source=source)


MOODS_A = ["warm nostalgic", "moody and refined", "soft and airy", "bold and playful", "cozy eclectic", "classic"]
LOOKS_B = [
    ("rust and cream is the easiest switch", "rust and cream textiles, warm wood, a vintage rug, amber glass lamps, plants"),
    ("could I commit this much to color? 🫢", "bold pink and red, patterned cushions, a gallery wall of colorful prints, a floral rug"),
    ("a little moody, a lot cozy", "deep navy and forest green, velvet, brass lamps, a dark vintage rug, an oil painting"),
    ("sage green is always the answer", "sage green and white, light oak, botanical prints, a soft rug, ceramic lamps, plants"),
    ("building the whole room around one rug", "a statement vintage rug in deep red and blue, textiles picking up its colors, brass"),
    ("someone who collects things from every trip", "global eclectic textiles, rattan, souvenir ceramics, a woven wall hanging, a kilim rug"),
]


def fallback(fmt: str, item: dict, room: str, cfg: dict, rng: random.Random) -> dict:
    n = cfg["slides"]["restyles"]
    if fmt == "makeover":
        name = item["name"]
        slides = [{"text": f"{m} {name}", "look": f"a {m} {name} {room}: {item['notes']}, fully furnished and styled"}
                  for m in MOODS_A[:n]]
        return {"hook": f"let's make this {name} ✍️", "slides": slides,
                "cta_look": f"a {name} {room} with {item['notes']}", "cta_question": "Have any Pinterest inspo you want to try? 📌",
                "title": f"{name} transformation 📌", "caption_intro": "which one would you pick? 👇"}
    looks = rng.sample(LOOKS_B, n)
    return {"hook": f"i'm done with {item['phrase']}! 😅",
            "slides": [{"text": t, "look": f"the same {room} restyled: {l}"} for t, l in looks],
            "cta_look": f"the same {room} restyled with terracotta, olive and warm wood", "cta_question": item["cta"],
            "title": f"goodbye {item['id']} 👋", "caption_intro": "which would you choose? 👇"}
