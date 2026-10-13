"""Leaning into what works (state/tuning.json), worked out from state/stats.json.

Each scored post (views at 3 days old vs the median of the last 30) feeds a multiplier for its style
or problem and its room: the average score, pulled towards 1.0 by `prior_posts` imaginary average
posts so one lucky post can't swing it, clamped to `min_mult`-`max_mult`. Nothing changes until
`min_posts` posts are scored, and `explore` of posts ignore the tuning so new ideas keep being tried.
"""
from __future__ import annotations

import json
import random
from collections import defaultdict

from . import config, stats

DIMENSIONS = ("theme", "room", "format")
FILE = "tuning.json"


def compute(st: dict, cfg: dict) -> dict:
    t = cfg["tracking"]
    rows = stats.scored_posts(st)
    out = {"active": len(rows) >= t["min_posts"], "scored_posts": len(rows), "needed": t["min_posts"],
           "explore": t["explore"], "multipliers": {}}
    for key in DIMENSIONS:
        groups: dict[str, list[float]] = defaultdict(list)
        for r in rows:
            if r.get(key):
                groups[r[key]].append(r["ratio"])
        out["multipliers"][key] = {
            name: {"mult": round(min(t["max_mult"], max(t["min_mult"], (sum(rs) + t["prior_posts"]) /
                                                        (len(rs) + t["prior_posts"]))), 2),
                   "posts": len(rs), "avg_ratio": round(sum(rs) / len(rs), 2)}
            for name, rs in groups.items()}
    return out


def load() -> dict:
    path = config.STATE / FILE
    return json.loads(path.read_text(encoding="utf-8")) if path.exists() else {"active": False, "multipliers": {}}


def save(t: dict) -> None:
    (config.STATE / FILE).write_text(json.dumps(t, ensure_ascii=False, indent=1) + "\n", encoding="utf-8")


def for_post(rng: random.Random, t: dict | None = None) -> dict:
    """The multipliers this post uses ({} when tuning is off or this post explores)."""
    t = t if t is not None else load()
    if not t.get("active") or rng.random() < t.get("explore", 0.25):
        return {}
    return {k: {n: v["mult"] for n, v in m.items()} for k, m in t.get("multipliers", {}).items()}
