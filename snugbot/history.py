"""What was sent to TikTok (state/history.json)."""
from __future__ import annotations

import json
from datetime import date
from pathlib import Path

from . import config

SENT = "SEND_TO_USER_INBOX"
DONE_STATUSES = {SENT, "PUBLISH_COMPLETE"}


class History:
    def __init__(self, posts: list[dict] | None = None, path: Path | None = None):
        self.posts = posts or []
        self.path = path

    @classmethod
    def load(cls, path: Path | None = None) -> "History":
        path = path or config.HISTORY_FILE
        if path.exists():
            return cls(json.loads(path.read_text(encoding="utf-8")).get("posts", []), path)
        return cls([], path)

    def save(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        text = json.dumps({"posts": self.posts}, ensure_ascii=False, indent=1)
        self.path.write_text(text + "\n", encoding="utf-8")

    def done(self) -> list[dict]:
        return [p for p in self.posts if p.get("status") in DONE_STATUSES]

    def sent_slot(self, day: date, slot: int) -> bool:
        return any(p["date"] == day.isoformat() and p.get("slot") == slot and not p.get("extra")
                   for p in self.done())

    def add(self, post: dict) -> None:
        self.posts.append(post)

    def recent_hooks(self, n: int = 60) -> list[str]:
        posts = sorted(self.done(), key=lambda p: (p["date"], p.get("sent_at", "")))
        return [p["hook"] for p in posts[-n:] if p.get("hook")]
