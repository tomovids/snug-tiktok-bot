"""The "before" rooms (state/rooms/): made once, reused for different styles.

A room rests `room_rest_days` between uses. A new one is only made while there are fewer than
`library_size` rooms of that kind, or when every one of them was used recently.
"""
from __future__ import annotations

import json
import random
import tomllib
from datetime import date
from pathlib import Path

from PIL import Image

from . import config
from .gemini import Gemini

W, H = 1080, 1920
LIBRARY = "rooms.json"


def _file() -> Path:
    return config.ROOMS_DIR / LIBRARY


def load() -> list[dict]:
    return json.loads(_file().read_text(encoding="utf-8")) if _file().exists() else []


def save(rooms: list[dict]) -> None:
    config.ROOMS_DIR.mkdir(parents=True, exist_ok=True)
    _file().write_text(json.dumps(rooms, ensure_ascii=False, indent=1) + "\n", encoding="utf-8")


def image(entry: dict) -> Image.Image:
    return Image.open(config.ROOMS_DIR / entry["file"]).convert("RGB")


def _arch() -> dict:
    with open(config.DATA / "styles.toml", "rb") as f:
        return tomllib.load(f)["architecture"]


def matches(e: dict, kind: str, room: str, problem: str | None) -> bool:
    return e["kind"] == kind and e["room"] == room and (kind == "empty" or e.get("problem") == problem)


def pick(kind: str, room: str, problem: str | None, day: date, cfg: dict, rng: random.Random,
         rooms: list[dict] | None = None, taken: set[str] = frozenset()) -> dict | None:
    """A library room to reuse, or None when a new one should be made."""
    rot = cfg["rotation"]
    rooms = load() if rooms is None else rooms
    found = [e for e in rooms if matches(e, kind, room, problem) and e["id"] not in taken]

    def last(e):
        return max((date.fromisoformat(d) for d in e.get("used", [])), default=date.min)

    rested = [e for e in found if (day - last(e)).days >= rot["room_rest_days"]]
    if rested:
        return min(rested, key=lambda e: (last(e), rng.random()))
    if len(found) < rot["library_size"]:
        return None
    return min(found, key=lambda e: (last(e), rng.random()))


def prompt(kind: str, room: str, problem_look: str | None, rng: random.Random) -> tuple[str, str, str]:
    a = _arch()
    arch = rng.choice(a[room])
    floors = a["floors"] if room == "kitchen" else [f for f in a["floors"] if "tile" not in f]
    floor = rng.choice(floors)
    camera = ("camera at eye level inside the room, about 3 metres from the back wall and looking straight at "
              "it, so the back wall fills most of the frame and only some floor shows; no door frames or "
              "walls cut off at the edges; tall vertical framing")
    if kind == "empty":
        contents = ("Completely empty: no furniture, no decor, no rugs, no curtains, no people." if room != "kitchen"
                    else "A bare, newly fitted kitchen with plain white units and empty worktops: no decor, "
                         "no stools, no people.")
        text = (f"Photorealistic interior photo of an empty, freshly painted {room} in a city apartment, {camera}. "
                f"The room has {arch}, CLEARLY VISIBLE in the picture, {floor} floor, warm white walls, soft afternoon sunlight making "
                f"patches of light on the floor and walls. {contents} Bright and calm, real-estate photography.")
    else:
        text = (f"Photorealistic smartphone photo of a real, lived-in {room}, {camera}. The room has {arch}, clearly "
                f"visible in the picture, and a {floor} floor. The room: {problem_look}. Furnished and tidy but boring. Soft natural daylight. "
                f"No people, no text.")
    return text, arch, floor


def add(ai: Gemini, kind: str, room: str, problem: str | None, problem_look: str | None, day: date,
        rng: random.Random, rooms: list[dict] | None = None, log=print) -> dict:
    """Makes a new "before" room (one image at the normal price) and adds it to the library."""
    rooms = load() if rooms is None else rooms
    text, arch, floor = prompt(kind, room, problem_look, rng)
    img = fit(ai.image(text))
    n = 1 + max([int(e["id"].rsplit("-", 1)[-1]) for e in rooms if e["id"].rsplit("-", 1)[-1].isdigit()] or [0])
    rid = f"{kind}-{room.replace(' ', '')}-{problem or 'none'}-{n}"
    config.ROOMS_DIR.mkdir(parents=True, exist_ok=True)
    img.save(config.ROOMS_DIR / f"{rid}.jpg", "JPEG", quality=88)
    entry = {"id": rid, "kind": kind, "room": room, "problem": problem, "arch": arch, "floor": floor,
             "file": f"{rid}.jpg", "created": day.isoformat(), "used": []}
    rooms.append(entry)
    save(rooms)
    log(f"New 'before' room for the library: {rid}")
    return entry


def mark_used(room_id: str, day: date, rooms: list[dict] | None = None) -> None:
    rooms = load() if rooms is None else rooms
    for e in rooms:
        if e["id"] == room_id and day.isoformat() not in e["used"]:
            e["used"].append(day.isoformat())
    save(rooms)


def fit(img: Image.Image) -> Image.Image:
    """Crop to 9:16 and scale to 1080x1920."""
    w, h = img.size
    t = W / H
    if w / h > t:
        nw = round(h * t)
        img = img.crop(((w - nw) // 2, 0, (w - nw) // 2 + nw, h))
    elif w / h < t:
        nh = round(w / t)
        img = img.crop((0, (h - nh) // 2, w, (h - nh) // 2 + nh))
    return img.resize((W, H), Image.LANCZOS)
