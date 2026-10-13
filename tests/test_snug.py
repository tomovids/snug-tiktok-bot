import random
from datetime import date, datetime, timedelta, timezone

import pytest
from PIL import Image

from snugbot import config, pipeline, planner, render, rooms
from snugbot.history import SENT, History


@pytest.fixture
def cfg():
    return config.load_config()


@pytest.fixture
def scratch(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "PLAN_FILE", tmp_path / "plan.json")
    monkeypatch.setattr(config, "HISTORY_FILE", tmp_path / "history.json")
    monkeypatch.setattr(config, "QUEUE", tmp_path / "queue")
    monkeypatch.setattr(config, "SITE", tmp_path / "site")
    monkeypatch.setattr(config, "ROOMS_DIR", tmp_path / "rooms")
    return tmp_path


def test_styles_and_problems_rotate_without_repeats(cfg):
    hist = History()
    seen = []
    for d in range(10):
        day = date(2026, 10, 14) + timedelta(days=d)
        fmt, item, room = planner.choose("makeover", day, hist, cfg, random.Random(d))
        seen.append(item["id"])
        hist.add({"date": day.isoformat(), "format": fmt, "theme": item["id"], "room": room, "status": SENT})
    assert len(set(seen)) == 10


def test_problem_rooms_suit_the_problem(cfg):
    for d in range(20):
        fmt, item, room = planner.choose("problem", date(2026, 10, 14), History(), cfg, random.Random(d))
        assert room in item["rooms"]


GOOD_A = {"hook": "let's make this japandi ✍️",
          "slides": [{"text": f"{m} japandi", "look": " ".join(["word"] * 30)} for m in
                     ["warm minimal", "dark and moody", "soft neutral", "a little color", "cozy wabi-sabi", "gallery"]],
          "cta_question": "Have any Pinterest inspo you want to try? 📌", "title": "japandi transformation 📌",
          "caption_intro": "which one would you pick? 👇", "cta_look": "x"}


def test_rule_checks(cfg):
    item = {"id": "japandi", "name": "japandi"}
    assert planner.problems(GOOD_A, "makeover", item, [], cfg) == []
    bad = {**GOOD_A, "hook": "I did this all on the Spatial app ✍️"}
    found = " ".join(planner.problems(bad, "makeover", item, [], cfg))
    assert "spatial" in found and "i did this" in found
    wrong_label = {**GOOD_A, "slides": [{"text": "warm minimal", "look": "x " * 30}] + GOOD_A["slides"][1:]}
    assert planner.problems(wrong_label, "makeover", item, [], cfg)
    assert planner.problems(GOOD_A, "makeover", item, ["let's make this japandi ✍️"], cfg)   # repeat


def test_fallback_plan_passes_the_checks(cfg):
    data = planner.load_data()
    for item in data["styles"][:5]:
        d = planner.fallback("makeover", item, "living room", cfg, random.Random(1))
        assert planner.problems(d, "makeover", item, [], cfg) == []
    for item in data["problems"][:5]:
        d = planner.fallback("problem", item, item["rooms"][0], cfg, random.Random(1))
        assert planner.problems(d, "problem", item, [], cfg) == []


def test_room_library_reuse(cfg, scratch):
    lib = []
    day = date(2026, 10, 14)
    assert rooms.pick("empty", "bedroom", None, day, cfg, random.Random(1), lib) is None     # library empty
    for n in range(cfg["rotation"]["library_size"]):
        lib.append({"id": f"empty-bedroom-none-{n}", "kind": "empty", "room": "bedroom", "problem": None,
                    "file": "x.jpg", "used": [(day - timedelta(days=n)).isoformat()]})
    # all used in the last few days, library full -> reuse the one used longest ago
    assert rooms.pick("empty", "bedroom", None, day, cfg, random.Random(1), lib)["id"] == "empty-bedroom-none-3"
    lib[1]["used"] = [(day - timedelta(days=40)).isoformat()]
    assert rooms.pick("empty", "bedroom", None, day, cfg, random.Random(1), lib)["id"] == "empty-bedroom-none-1"
    assert rooms.pick("lived", "bedroom", "beige", day, cfg, random.Random(1), lib) is None


def test_upcoming_slots(cfg):
    now = datetime(2026, 10, 14, 9, 0, tzinfo=timezone.utc)      # 10:00 UK
    slots = pipeline.upcoming(cfg, now, 30)
    ids = [pipeline.post_id(d, i) for d, i, _ in slots if _ > now]
    assert ids[:3] == ["2026-10-14-1", "2026-10-14-2", "2026-10-15-1"]


def _ready_post(pid: str, t: datetime) -> dict:
    return {"id": pid, "date": t.date().isoformat(), "slot": 0, "time": t.isoformat(), "status": "ready",
            "plan": {}, "keys": [], "slides": ["01.jpg"]}


def test_due_posts_are_published(cfg, scratch):
    t = datetime(2026, 10, 14, 12, 0, tzinfo=timezone.utc)
    state = {"posts": [_ready_post("2026-10-14-1", t), _ready_post("2026-10-15-1", t + timedelta(days=1))]}
    for p in state["posts"]:
        (config.QUEUE / p["id"]).mkdir(parents=True)
        Image.new("RGB", (10, 10)).save(config.QUEUE / p["id"] / "01.jpg")
    assert pipeline.due(state, cfg, t + timedelta(minutes=5)) == ["2026-10-14-1"]
    assert (config.SITE / "p" / "2026-10-14-1" / "01.jpg").exists()
    state["posts"][0]["retry_after"] = (t + timedelta(hours=2)).isoformat()
    assert pipeline.due(state, cfg, t + timedelta(minutes=10)) == []        # inbox full: waits
    assert pipeline.due(state, cfg, t + timedelta(hours=30)) == ["2026-10-15-1"]   # the first is too late now
    assert state["posts"][0]["status"] == "failed"


def test_text_fits_on_a_slide(cfg):
    img = Image.new("RGB", (1080, 1920), (200, 190, 180))
    render.draw(img, "i'm done with my dorm room looking like a hostel! 😅", 540, 400, 66, cfg)
    style, lines = render.layout("i'm done with my dorm room looking like a hostel! 😅", cfg, 66, 1080 * 0.86)
    assert len(lines) <= 3
    assert not any(all(render.is_emoji(c) or c.isspace() for c in line) for line in lines)


def test_look_prompts_keep_the_room(cfg):
    d = planner.fallback("makeover", planner.load_data()["styles"][0], "kitchen", cfg, random.Random(1))
    p = planner.Plan(format="makeover", room="kitchen", theme="x", theme_name="x", hook=d["hook"], hook_sub="",
                     slides=[planner.Slide(**s) for s in d["slides"]], cta_look="x", cta_question="?", cta_line="",
                     title="", caption="")
    text = pipeline.look_prompt(p, "a look")
    assert "layout stays" in text and "same camera position" in text


def test_two_posts_a_day_use_different_rooms(cfg):
    hist = History([{"date": "2026-10-14", "format": "makeover", "theme": "midcentury", "room": "kitchen", "status": SENT}])
    for seed in range(15):
        fmt, item, room = planner.choose("problem", date(2026, 10, 14), hist, cfg, random.Random(seed))
        assert room != "kitchen"


def test_tiktok_posts_are_matched_to_drafts():
    from snugbot import stats
    sent = "2026-10-14T11:05:00+00:00"
    drafts = [{"date": "2026-10-13", "hook": "i'm done with beige everything! 😅", "status": SENT,
               "sent_at": "2026-10-13T18:05:00+00:00", "caption": "goodbye beige 👋\n\nwhich one is you?\n\n#snug"},
              {"date": "2026-10-14", "hook": "let's make this japandi ✍️", "status": SENT, "sent_at": sent,
               "caption": "japandi transformation 📌\n\nwhich one would you pick? 👇\n\n#interiordesign #snug"}]
    created = int(datetime.fromisoformat("2026-10-14T13:00:00+00:00").timestamp())
    video = {"id": "1", "title": "japandi transformation 📌 which one would you pick? 👇 #interiordesign", "create_time": created}
    assert stats.match_post(video, drafts, set()) == 1
    early = {**video, "create_time": int(datetime.fromisoformat("2026-10-14T08:00:00+00:00").timestamp())}
    assert stats.match_post(early, drafts, set()) is None            # posted before that draft existed
    assert stats.match_post({"id": "2", "title": "my cat", "create_time": created}, drafts, set()) is None


def test_dashboard_builds_with_no_stats_yet(cfg, scratch):
    from snugbot import dashboard
    d = dashboard.build({"account": [], "videos": {}}, {"active": False}, History(), {"posts": []}, cfg)
    assert d["kpis"]["followers"] is None and d["pipeline"] == [] and d["cost"]["per_post_usd"] is None
