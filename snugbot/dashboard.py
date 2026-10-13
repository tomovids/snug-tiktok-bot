"""Builds the dashboard's data (site/<dashboard_dir>/data.json): TikTok numbers, what performs best,
the posts being made, recent drafts and what the pictures cost."""
from __future__ import annotations

import json
from collections import defaultdict
from datetime import date, datetime, timedelta, timezone
from statistics import median
from zoneinfo import ZoneInfo

from . import config, planner, rooms, stats
from .history import History

STATUS = {"SEND_TO_USER_INBOX": "In drafts", "PUBLISH_COMPLETE": "Posted", "FAILED": "Failed"}
PLAN_STATUS = {"planned": "Planned", "waiting": "Making pictures", "ready": "Ready", "sent": "Sent",
               "failed": "Dropped"}


def folder(cfg: dict):
    return config.SITE / cfg["tracking"]["dashboard_dir"]


def _labels(cfg: dict) -> dict:
    data = planner.load_data()
    themes = {s["id"]: s["name"] for s in data["styles"]}
    themes.update({p["id"]: p["phrase"] for p in data["problems"]})
    slots = {i: t for i, t in enumerate(cfg["schedule"]["post_times"])}
    return {"theme": themes, "format": {"makeover": "Makeover (let's make this…)", "problem": "Problem (i'm done with…)"},
            "room": {}, "slot": slots}


def _table(rows: list[dict], key: str, labels: dict) -> list[dict]:
    groups: dict = defaultdict(list)
    for r in rows:
        if r.get(key) is not None:
            groups[r[key]].append(r)
    out = []
    for name, items in groups.items():
        eng = [i["engagement"] for i in items if i.get("engagement") is not None]
        best = max(items, key=lambda i: i["views72"])
        out.append({"name": name, "label": labels.get(key, {}).get(name, str(name)), "posts": len(items),
                    "median_views": round(median(i["views72"] for i in items)),
                    "avg_ratio": round(sum(i["ratio"] for i in items) / len(items), 2),
                    "engagement": round(sum(eng) / len(eng), 4) if eng else None,
                    "best_hook": best.get("hook"), "best_url": best.get("share_url")})
    return sorted(out, key=lambda r: (-r["avg_ratio"], -r["posts"]))


def build(st: dict, tune: dict, hist: History, plan_state: dict, cfg: dict, now: datetime | None = None) -> dict:
    now = now or datetime.now(timezone.utc)
    labels = _labels(cfg)
    rows = stats.scored_posts(st)
    account = st.get("account", [])
    daily = [{"date": c["date"], "views": max(0, c["total_views"] - p["total_views"])}
             for p, c in zip(account, account[1:]) if p.get("total_views") is not None and c.get("total_views") is not None]
    last7, prev7 = sum(d["views"] for d in daily[-7:]), sum(d["views"] for d in daily[-14:-7])

    def followers_ago(days: int):
        if not account:
            return None
        cutoff = (date.fromisoformat(account[-1]["date"]) - timedelta(days=days)).isoformat()
        older = [a for a in account if a["date"] <= cutoff]
        return (older[-1] if older else account[0]).get("followers")

    now_f = account[-1].get("followers") if account else None
    recent10 = rows[-10:]
    eng = [r["engagement"] for r in recent10 if r.get("engagement") is not None]
    videos = list(st.get("videos", {}).values())
    bot_videos = [v for v in videos if v.get("post")]

    def post_row(v: dict) -> dict:
        p, lt = v.get("post") or {}, v.get("latest") or {}
        v72 = stats.views_at(v)
        title = (v.get("title") or "").split("\n")[0][:110]
        return {"posted": v.get("posted"), "hook": p.get("hook") or title or "(no title)",
                "format": p.get("format"), "theme": labels["theme"].get(p.get("theme"), p.get("theme_name") or ""),
                "room": p.get("room") or "", "views": lt.get("views"), "likes": lt.get("likes"),
                "comments": lt.get("comments"), "shares": lt.get("shares"),
                "views72": round(v72) if v72 is not None else None,
                "engagement": stats.engagement(lt) if lt else None, "url": v.get("share_url"),
                "cover": v.get("cover"), "bot": bool(p), "new": v72 is None}

    by_views = sorted(bot_videos, key=lambda v: -(v.get("latest", {}).get("views") or 0))
    recent = sorted(videos, key=lambda v: v.get("posted") or "", reverse=True)[:24]
    tables = {k: _table(rows, k, labels) for k in ("format", "theme", "room", "slot")}

    tz = ZoneInfo(cfg["schedule"]["timezone"])
    upcoming = sorted((p for p in plan_state.get("posts", []) if p["status"] in ("planned", "waiting", "ready")),
                      key=lambda p: p["time"])
    pipeline = [{"time": p["time"], "local": datetime.fromisoformat(p["time"]).astimezone(tz).strftime("%a %d %b, %H:%M"),
                 "format": p["plan"]["format"], "theme": p["plan"]["theme_name"], "room": p["plan"]["room"],
                 "hook": p["plan"]["hook"], "status": PLAN_STATUS.get(p["status"], p["status"]),
                 "retry": bool(p.get("retry_after")), "cost": p.get("cost")} for p in upcoming]
    drafts = [{"sent_at": p.get("sent_at"), "hook": p.get("hook"), "format": p.get("format"),
               "theme": p.get("theme_name"), "room": p.get("room"), "cost": p.get("cost"),
               "status": STATUS.get(p.get("status"), p.get("status")), "failed": p.get("status") == "FAILED",
               "error": (p.get("error") or "").split(" (log id")[0][:140] or None}
              for p in sorted(hist.posts, key=lambda p: p.get("sent_at") or "", reverse=True)[:30]]
    month_ago = (now - timedelta(days=30)).isoformat()
    sent30 = [p for p in hist.done() if (p.get("sent_at") or "") >= month_ago]
    spent30 = sum(p.get("cost") or 0 for p in sent30)
    per_post = spent30 / len(sent30) if sent30 else None
    per_day = len(cfg["schedule"]["post_times"])

    def winners(table: list[dict]) -> list[dict]:
        return [t for t in table if t["posts"] >= 2 and t["avg_ratio"] >= 1.1][:2]

    return {
        "updated": st.get("updated") or now.isoformat(timespec="minutes"),
        "built": now.isoformat(timespec="minutes"),
        "kpis": {"followers": now_f,
                 "followers_7d": (now_f - followers_ago(7)) if now_f is not None and followers_ago(7) is not None else None,
                 "likes": account[-1].get("likes") if account else None, "views_7d": last7, "views_prev_7d": prev7,
                 "posts_tracked": len(bot_videos), "posts_scored": len(rows),
                 "median_views_10": round(median(r["views72"] for r in recent10)) if recent10 else None,
                 "engagement_10": round(sum(eng) / len(eng), 4) if eng else None},
        "account": account, "daily_views": daily,
        "top_posts": [post_row(v) for v in by_views[:12]], "recent": [post_row(v) for v in recent],
        "tables": tables, "double_down": {k: winners(t) for k, t in tables.items()},
        "tuning": {"active": tune.get("active", False), "scored": tune.get("scored_posts", 0),
                   "needed": tune.get("needed", cfg["tracking"]["min_posts"]), "explore": tune.get("explore", 0.25),
                   "multipliers": {k: [{"name": n, "label": labels.get(k, {}).get(n, n), **v}
                                       for n, v in sorted(m.items(), key=lambda kv: -kv[1]["mult"])]
                                   for k, m in tune.get("multipliers", {}).items()}},
        "pipeline": pipeline, "drafts": drafts,
        "cost": {"spent_30d_usd": round(spent30, 2), "posts_30d": len(sent30),
                 "per_post_usd": round(per_post, 3) if per_post else None,
                 "month_estimate_usd": round(per_post * per_day * 30, 2) if per_post else None,
                 "rooms_in_library": len(rooms.load())},
        "schedule": {"times": cfg["schedule"]["post_times"], "timezone": "UK"},
    }


def write(data: dict, cfg: dict) -> None:
    out = folder(cfg)
    out.mkdir(parents=True, exist_ok=True)
    (out / "data.json").write_text(json.dumps(data, ensure_ascii=False, indent=1) + "\n", encoding="utf-8")


def rebuild(cfg: dict) -> None:
    """From the saved files only (no TikTok calls)."""
    from . import pipeline as pl, tuning
    st = stats.load()
    write(build(st, tuning.compute(st, cfg), History.load(), pl.load_plan(), cfg), cfg)
