"""TikTok performance tracking (state/stats.json).

Twice a day: the account's followers / likes / post count, and every public post's views, likes,
comments and shares. Each post keeps daily snapshots for its first 14 days so it can be scored on
its views at 3 days old (a fair comparison between old and new posts). Posts are matched back to
the bot's drafts (state/history.json) by their caption, which gives each one its format, style or
problem, room and time slot.
"""
from __future__ import annotations

import json
import re
from datetime import datetime, timedelta, timezone
from pathlib import Path
from statistics import median

from . import config
from .history import History

STATS_FILE = "stats.json"
SCORE_AGE_H = 72
KEEP_SNAPSHOTS_H = 14 * 24


def _file() -> Path:
    return config.STATE / STATS_FILE


def load(path: Path | None = None) -> dict:
    path = path or _file()
    if path.exists():
        return json.loads(path.read_text(encoding="utf-8"))
    return {"account": [], "videos": {}, "updated": None}


def save(st: dict, path: Path | None = None) -> None:
    path = path or _file()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(st, ensure_ascii=False, indent=1) + "\n", encoding="utf-8")


def _norm(text: str) -> str:
    return re.sub(r"[^a-z0-9]+", "", (text or "").lower())


def post_key(p: dict) -> str:
    return p.get("publish_id") or f"{p.get('date')}|{p.get('hook')}"


def match_post(video: dict, posts: list[dict], taken: set[str]) -> int | None:
    """Index of the bot draft this TikTok post came from. A posted carousel's title becomes its
    caption, so the caption's intro line (or title, or hook) is the signature. Titles can repeat
    ("goodbye beige 👋"), so of the drafts sent before the post went up, the latest one wins."""
    text = _norm((video.get("title") or "") + " " + (video.get("video_description") or ""))
    created = datetime.fromtimestamp(int(video.get("create_time") or 0), timezone.utc)
    found = []
    for i, p in enumerate(posts):
        if post_key(p) in taken or not p.get("hook"):
            continue
        sent = p.get("sent_at")
        if sent and created < datetime.fromisoformat(sent) - timedelta(hours=1):
            continue                       # posted before this draft existed
        lines = [x for x in (p.get("caption") or "").split("\n") if x.strip() and not x.strip().startswith("#")]
        sigs = [_norm(x) for x in lines] + [_norm(p["hook"])]
        if any(len(s) >= 12 and s[:80] in text for s in sigs):
            found.append((sent or "", i))
    return max(found)[1] if found else None


def post_meta(p: dict) -> dict:
    return {"date": p.get("date"), "hook": p.get("hook"), "format": p.get("format"), "theme": p.get("theme"),
            "theme_name": p.get("theme_name"), "room": p.get("room"), "slot": p.get("slot"),
            "texts": p.get("texts") or [], "extra": bool(p.get("extra"))}


def update(st: dict, account: dict, videos: list[dict], hist: History, now: datetime | None = None) -> dict:
    now = now or datetime.now(timezone.utc)
    today = now.date().isoformat()
    total_views = sum(int(v.get("view_count") or 0) for v in videos)
    acc = {"date": today, "followers": account.get("follower_count"), "likes": account.get("likes_count"),
           "videos": account.get("video_count"), "total_views": total_views}
    st["account"] = [a for a in st.get("account", []) if a["date"] != today] + [acc]
    posts = sorted((p for p in hist.done() if p.get("hook")), key=lambda p: p.get("sent_at", ""))
    known = st.setdefault("videos", {})
    taken = {v["post_key"] for v in known.values() if v.get("post_key")}
    for v in sorted(videos, key=lambda v: int(v.get("create_time") or 0)):
        vid = str(v["id"])
        rec = known.setdefault(vid, {"id": vid, "snapshots": []})
        created = datetime.fromtimestamp(int(v.get("create_time") or 0), timezone.utc)
        rec.update({"posted": created.isoformat(timespec="minutes"), "title": v.get("title") or "",
                    "share_url": v.get("share_url"), "cover": v.get("cover_image_url")})
        age_h = (now - created).total_seconds() / 3600
        snap = {"at": now.isoformat(timespec="minutes"), "age_h": round(age_h, 1),
                "views": int(v.get("view_count") or 0), "likes": int(v.get("like_count") or 0),
                "comments": int(v.get("comment_count") or 0), "shares": int(v.get("share_count") or 0)}
        rec["latest"] = snap
        if age_h <= KEEP_SNAPSHOTS_H or not rec["snapshots"]:
            rec["snapshots"] = [s for s in rec["snapshots"] if s["at"][:10] != today] + [snap]
        if rec.get("post") is None:
            i = match_post(v, posts, taken)
            if i is not None:
                rec["post_key"] = post_key(posts[i])
                rec["post"] = post_meta(posts[i])
                taken.add(rec["post_key"])
    st["updated"] = now.isoformat(timespec="minutes")
    return st


def views_at(rec: dict, age_h: float = SCORE_AGE_H) -> float | None:
    """Views when the post was `age_h` hours old (interpolated); None if it's younger than that."""
    latest = rec.get("latest")
    if not latest or latest["age_h"] < age_h:
        return None
    snaps = sorted(rec.get("snapshots", []), key=lambda s: s["age_h"])
    before = [s for s in snaps if s["age_h"] <= age_h]
    after = [s for s in snaps if s["age_h"] >= age_h]
    if before and after:
        a, b = before[-1], after[0]
        if b["age_h"] == a["age_h"]:
            return float(a["views"])
        t = (age_h - a["age_h"]) / (b["age_h"] - a["age_h"])
        return a["views"] + t * (b["views"] - a["views"])
    return float((after or [latest])[0]["views"])


def engagement(snap: dict) -> float | None:
    v = snap.get("views") or 0
    return (snap.get("likes", 0) + snap.get("comments", 0) + snap.get("shares", 0)) / v if v else None


def scored_posts(st: dict) -> list[dict]:
    """Bot posts old enough to score, each with its ratio to the median of the last 30."""
    rows = []
    for rec in st.get("videos", {}).values():
        if not rec.get("post"):
            continue
        v72 = views_at(rec)
        if v72 is None:
            continue
        rows.append({**rec["post"], "id": rec["id"], "posted": rec["posted"], "views72": v72,
                     "views": rec["latest"]["views"], "engagement": engagement(rec["latest"]),
                     "share_url": rec.get("share_url"), "cover": rec.get("cover")})
    rows.sort(key=lambda r: r["posted"])
    recent = [r["views72"] for r in rows[-30:]]
    base = median(recent) if recent else 0
    for r in rows:
        r["ratio"] = (r["views72"] / base) if base else 1.0
    return rows
