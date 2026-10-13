"""Makes Snug posts ahead of time and sends them when they're due.

Every hourly run ("tick"):
1. collect: picks up finished batch jobs, writes the callouts, draws the slides into queue/<post>/.
2. prepare: plans the posts due in the next `prepare_hours` (format, style or problem, room, words),
   takes a "before" room from the library (or makes one) and orders the restyles from Google's
   half-price batch service, one job for all of them.
3. rescue: a post whose pictures still aren't back `batch_grace_hours` after its time is made straight
   away at the normal price.
4. due: lists the ready posts whose time has come (built.json) and copies their slides into site/p/
   for GitHub Pages; `send` then sends them to TikTok drafts.

state/plan.json holds every post being made (planned -> waiting -> ready -> sent).
"""
from __future__ import annotations

import json
import random
import shutil
import time
from datetime import date, datetime, timedelta, timezone
from zoneinfo import ZoneInfo

from PIL import Image

from . import config, planner, render, rooms, tokens
from .gemini import Gemini, GeminiError, image_from, image_request
from .history import SENT, History
from .planner import Plan
from .tiktok import TikTok, TikTokError, wait_for_urls

KEEP = ("This is a restyle of the SAME room photo, not a new room. Keep the architecture pixel-for-pixel: the same "
        "walls in the same place and the same wall color and finish, the same window(s) with the same position, "
        "size and frame, the same floor, ceiling, cornicing, doors and fireplace, and the same camera position, "
        "angle and lens. Do NOT add, remove or move any window, wall, beam, column, archway, brick or panelling, "
        "do not repaint the walls and do not add curtains or blinds.")
FINISH = ("Lighting to suit the look (moody looks darker with lamps on, airy looks in bright daylight). "
          "Photorealistic interior photography, rich believable colors, like a high-end design magazine. "
          "No text, no watermarks, no people.")
STALE_HOURS = 18     # a post this late is dropped instead of sent


def look_prompt(plan: Plan, look: str) -> str:
    if plan.format == "makeover":
        what = (f"Furnish this empty {plan.room} fully and richly like a real, loved home styled for a design "
                f"magazine: the furniture fills the room and comes close to the camera, with layered rugs, "
                f"textiles, books, lamps, plants, art on the walls and a styled table. The look: {look}.")
        if plan.room == "kitchen":
            what = (f"Restyle this kitchen fully, like a real, loved kitchen styled for a design magazine: cabinet "
                    f"colors, worktops, lights, stools, open shelves, plants and decor can change, the layout "
                    f"stays. The look: {look}.")
    elif plan.room == "kitchen":
        what = (f"Restyle this kitchen completely in this look: {look}. Repaint the cabinets, change the handles, "
                f"worktops, splashback, lights, stools and decor so it looks totally different, keeping the layout, "
                f"the walls and the window.")
    else:
        what = (f"Restyle this {plan.room} completely in this look: {look}. Replace the furniture, textiles, rugs, "
                f"art, lamps and decor so it feels full of character and totally different, keeping the wall color "
                f"and the layout.")
    return f"{KEEP} {what} {FINISH}"


# ---- the plan file ------------------------------------------------------------------------------

def load_plan() -> dict:
    if config.PLAN_FILE.exists():
        return json.loads(config.PLAN_FILE.read_text(encoding="utf-8"))
    return {"posts": []}


def save_plan(state: dict) -> None:
    config.PLAN_FILE.parent.mkdir(parents=True, exist_ok=True)
    config.PLAN_FILE.write_text(json.dumps(state, ensure_ascii=False, indent=1) + "\n", encoding="utf-8")


def tz(cfg: dict) -> ZoneInfo:
    return ZoneInfo(cfg["schedule"]["timezone"])


def slot_time(cfg: dict, day: date, index: int) -> datetime:
    h, m = map(int, cfg["schedule"]["post_times"][index].split(":"))
    return datetime(day.year, day.month, day.day, h, m, tzinfo=tz(cfg))


def upcoming(cfg: dict, now: datetime, hours: float) -> list[tuple[date, int, datetime]]:
    """(day, slot index, time) for every slot from STALE_HOURS ago to `hours` ahead."""
    local = now.astimezone(tz(cfg))
    out = []
    for d in range(-1, int(hours // 24) + 2):
        day = local.date() + timedelta(days=d)
        for i in range(len(cfg["schedule"]["post_times"])):
            t = slot_time(cfg, day, i)
            if now - timedelta(hours=STALE_HOURS) <= t <= now + timedelta(hours=hours):
                out.append((day, i, t))
    return out


def post_id(day: date, index: int) -> str:
    return f"{day.isoformat()}-{index + 1}"


def queue_dir(pid: str):
    return config.QUEUE / pid


# ---- 1. collect -----------------------------------------------------------------------------------

def collect(state: dict, ai: Gemini, cfg: dict, log=print) -> bool:
    changed = False
    jobs = {p["batch"] for p in state["posts"] if p["status"] == "waiting" and p.get("batch")}
    for job in jobs:
        try:
            st, results = ai.batch_get(job)
        except GeminiError as e:
            log(f"Couldn't check batch {job}: {e}")
            continue
        if st not in ("BATCH_STATE_SUCCEEDED", "BATCH_STATE_FAILED", "BATCH_STATE_CANCELLED", "BATCH_STATE_EXPIRED"):
            log(f"Batch {job}: {st.replace('BATCH_STATE_', '').lower() or 'waiting'}")
            continue
        log(f"Batch {job}: {st.replace('BATCH_STATE_', '').lower()}")
        for p in state["posts"]:
            if p.get("batch") != job or p["status"] != "waiting":
                continue
            images = {}
            for k in p["keys"]:
                try:
                    images[k] = image_from(results[k]) if k in results and "error" not in results[k] else None
                except GeminiError:
                    images[k] = None
            missing = [k for k, v in images.items() if v is None]
            if missing:
                log(f"  {p['id']}: {len(missing)} picture(s) missing from the batch; making them now.")
            finish(p, images, ai, cfg, log)
            changed = True
    return changed


def finish(p: dict, images: dict[str, Image.Image | None], ai: Gemini, cfg: dict, log=print) -> None:
    """Fills in missing pictures (now, normal price), writes callouts, draws the slides into the queue."""
    plan = Plan.from_dict(p["plan"])
    before = rooms.image({"file": p["room_file"]})
    looks = [s.look for s in plan.slides] + [plan.cta_look]
    for k, look in zip(p["keys"], looks):
        if images.get(k) is None:
            images[k] = ai.image(look_prompt(plan, look), base=before)
            p["cost"] = round(p.get("cost", 0) + cfg["gemini"]["price_now"], 4)
    pics = [rooms.fit(images[k]) for k in p["keys"]]
    boxes = [None] * len(plan.slides)
    if plan.format == "makeover":
        for i, (slide, img) in enumerate(zip(plan.slides, pics)):
            slide.callout, boxes[i] = write_callout(ai, img, plan, slide.text, log)
    slides = render.slides_for(plan, before, pics[:-1], pics[-1], boxes, cfg)
    out = queue_dir(p["id"])
    if out.exists():
        shutil.rmtree(out)
    out.mkdir(parents=True)
    for n, img in enumerate(slides, 1):
        img.save(out / f"{n:02d}.jpg", "JPEG", quality=90)
    p["slides"] = [f"{n:02d}.jpg" for n in range(1, len(slides) + 1)]
    p["plan"] = plan.to_dict()
    p["status"] = "ready"
    p["ready_at"] = datetime.now(timezone.utc).isoformat(timespec="seconds")
    log(f"  {p['id']} is ready: {plan.hook}")


def write_callout(ai: Gemini, img: Image.Image, plan: Plan, label: str, log=print) -> tuple[str, tuple | None]:
    """A callout about a piece that's really in the picture, and where it is."""
    prompt = (f"This photo shows a {plan.room} styled as \"{label}\". Pick the ONE most eye-catching piece that "
              f"is clearly visible (a sofa, chair, lamp, table, sideboard, rug, mirror or artwork). Write a short "
              f"lowercase TikTok callout about it, 3-7 words, said like a person who wants it, naming its color or "
              f"material: start with \"need this\", \"love this\", \"obsessed with this\" or \"this ... !\" "
              f"(like \"need this chrome coffee table\", \"love this rosewood credenza\", \"this couch and lamp!\"). "
              f"No brands, no emoji. Return JSON {{\"callout\": \"...\", \"box_2d\": [ymin, xmin, "
              f"ymax, xmax]}} with the box around that piece, values 0-1000.")
    try:
        d = ai.text_json(prompt, images=[img])
        text = str(d.get("callout", "")).strip().strip('"')
        y0, x0, y1, x1 = [float(v) / 1000 for v in d["box_2d"]]
        if not text or len(text) > 40 or any(b in text.lower() for b in planner.BANNED):
            return "", None
        return text, (x0, y0, x1, y1)
    except (GeminiError, KeyError, ValueError, TypeError) as e:
        log(f"  (no callout: {e})")
        return "", None


# ---- 2. prepare -----------------------------------------------------------------------------------

def prepare(state: dict, hist: History, ai: Gemini, cfg: dict, now: datetime, log=print) -> bool:
    known = {p["id"] for p in state["posts"]}
    todo = [(d, i, t) for d, i, t in upcoming(cfg, now, cfg["schedule"]["prepare_hours"])
            if post_id(d, i) not in known and not hist.sent_slot(d, i) and t > now - timedelta(hours=2)]
    if not todo:
        return False
    library = rooms.load()
    taken_themes, taken_rooms, requests_by_key, new = set(), set(), {}, []
    planned_hist = History(hist.posts + [{**p["plan"], "date": p["date"], "status": SENT} for p in state["posts"]
                                         if p["status"] != "failed"])
    for day, index, t in todo:
        pid = post_id(day, index)
        rng = random.Random(f"snug/{pid}")
        fmt = cfg["schedule"]["formats"][index % len(cfg["schedule"]["formats"])]
        fmt, item, room = planner.choose(fmt, day, planned_hist, cfg, rng, taken_themes)
        taken_themes.add(item["id"])
        kind, problem = ("empty", None) if fmt == "makeover" else ("lived", item["id"])
        entry = rooms.pick(kind, room, problem, day, cfg, rng, library, taken_rooms)
        cost = 0.0
        if entry is None:
            entry = rooms.add(ai, kind, room, problem, item.get("room_look"), day, rng, library, log)
            cost += cfg["gemini"]["price_now"]
        taken_rooms.add(entry["id"])
        rooms.mark_used(entry["id"], day, library)
        plan = planner.write(fmt, item, room, planned_hist, cfg, ai, rng, log)
        plan.room_id = entry["id"]
        before = rooms.image(entry)
        keys = [f"{pid}/{n}" for n in range(len(plan.slides))] + [f"{pid}/cta"]
        for k, look in zip(keys, [s.look for s in plan.slides] + [plan.cta_look]):
            requests_by_key[k] = image_request(look_prompt(plan, look), base=before)
        cost += len(keys) * cfg["gemini"]["price_batch"]
        post = {"id": pid, "date": day.isoformat(), "slot": index, "time": t.isoformat(), "status": "planned",
                "plan": plan.to_dict(), "room_file": entry["file"], "keys": keys, "cost": round(cost, 4)}
        state["posts"].append(post)
        planned_hist.add({**plan.to_dict(), "date": day.isoformat(), "status": SENT})
        new.append(post)
        log(f"Planned {pid} ({t:%a %H:%M}): {fmt}, {plan.theme_name}, {room} -> {plan.hook}")
    try:
        job = ai.batch_submit(requests_by_key, f"snug-{new[0]['id']}")
        for p in new:
            p["batch"], p["status"] = job, "waiting"
        log(f"Ordered {len(requests_by_key)} pictures from the batch service ({job}).")
    except GeminiError as e:
        log(f"The batch service refused the order ({e}); these posts will be made at the normal price.")
        for p in new:
            p["status"] = "waiting"
            p["batch"] = None
    return True


# ---- 3. rescue ------------------------------------------------------------------------------------

def rescue(state: dict, ai: Gemini, cfg: dict, now: datetime, log=print) -> bool:
    changed = False
    grace = timedelta(hours=cfg["schedule"]["batch_grace_hours"])
    for p in state["posts"]:
        if p["status"] != "waiting":
            continue
        t = datetime.fromisoformat(p["time"])
        if p.get("batch") and now < t + grace:
            continue
        log(f"{p['id']}: pictures not back in time; making them now at the normal price.")
        try:
            finish(p, {}, ai, cfg, log)
        except GeminiError as e:
            log(f"  couldn't make {p['id']}: {e}")
            continue
        changed = True
    return changed


# ---- 4. due ---------------------------------------------------------------------------------------

def due(state: dict, cfg: dict, now: datetime, log=print) -> list[str]:
    out = []
    for p in state["posts"]:
        t = datetime.fromisoformat(p["time"])
        if p.get("retry_after") and datetime.fromisoformat(p["retry_after"]) > now:
            continue
        if p["status"] == "ready" and t <= now:
            if now - t > timedelta(hours=STALE_HOURS):
                p["status"] = "failed"
                p["error"] = "too late to send"
                continue
            if not queue_dir(p["id"]).exists():
                log(f"{p['id']}: its slides are missing from the queue; making it again.")
                p["status"] = "waiting"
                p["batch"] = None
                continue
            dest = config.SITE / "p" / p["id"]
            if dest.exists():
                shutil.rmtree(dest)
            shutil.copytree(queue_dir(p["id"]), dest)
            out.append(p["id"])
    return out


def prune(state: dict, now: datetime) -> None:
    """Forgets finished posts after a week and clears their queued slides."""
    keep = []
    for p in state["posts"]:
        if p["status"] in ("sent", "failed"):
            if queue_dir(p["id"]).exists():
                shutil.rmtree(queue_dir(p["id"]))
            if now - datetime.fromisoformat(p["time"]) > timedelta(days=7):
                continue
        keep.append(p)
    state["posts"] = keep


def tick(cfg: dict, ai: Gemini, now: datetime | None = None, log=print) -> list[str]:
    now = now or datetime.now(timezone.utc)
    state = load_plan()
    hist = History.load()
    prune(state, now)
    collect(state, ai, cfg, log)
    prepare(state, hist, ai, cfg, now, log)
    save_plan(state)
    rescue(state, ai, cfg, now, log)
    ready = due(state, cfg, now, log)
    save_plan(state)
    log(f"Gemini this run: {ai.usage}")
    return ready


# ---- sending --------------------------------------------------------------------------------------

def send(pid: str, cfg: dict, tt: TikTok, token_key: str, log=print, wait_pages=wait_for_urls) -> str:
    state = load_plan()
    p = next(x for x in state["posts"] if x["id"] == pid)
    plan = Plan.from_dict(p["plan"])
    slides = p["slides"]
    urls = [cfg["tiktok"]["pages_base_url"].rstrip("/") + f"/p/{pid}/{s}" for s in slides]
    hist = History.load()
    record = {"date": p["date"], "slot": p["slot"], "id": pid, "format": plan.format, "theme": plan.theme,
              "theme_name": plan.theme_name, "room": plan.room, "room_id": plan.room_id, "hook": plan.hook,
              "texts": [s.text for s in plan.slides], "caption": plan.caption, "source": plan.source,
              "cost": p.get("cost"), "urls": urls}
    try:
        wait_pages(urls, log=log)
        login = tokens.load(token_key)
        login = tt.refresh(login)
        tokens.save(login, token_key)
        days_left = (login["refresh_expires_at"] - time.time()) / 86400
        if days_left < 30:
            log(f"Heads up: the TikTok login runs out in {days_left:.0f} days. Run `python -m snugbot authorize` soon.")
        record["publish_id"] = tt.send_photo_draft(login["access_token"], urls, plan.title or plan.hook,
                                                   plan.caption, cfg["tiktok"].get("ai_generated", True), log)
        record["status"] = tt.wait_until_sent(login["access_token"], record["publish_id"], log=log)
        p["status"] = "sent"
    except (TikTokError, tokens.TokenError) as e:
        record["status"], record["error"] = "FAILED", str(e)
        if "too_many_pending" in str(e):       # 5 drafts already waiting: try again in 2 hours
            p["retry_after"] = (datetime.now(timezone.utc) + timedelta(hours=2)).isoformat(timespec="seconds")
        else:
            p["status"] = "failed"
        raise
    finally:
        record["sent_at"] = datetime.now(timezone.utc).isoformat(timespec="seconds")
        hist.add(record)
        hist.save()
        save_plan(state)
    log("Done: the slideshow is in @TrySnug's TikTok inbox. Open the notification, add a sound and post.")
    return record["status"]
