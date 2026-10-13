"""Settings (config.toml), paths and secrets (.env locally, environment variables on GitHub)."""
from __future__ import annotations

import os
import tomllib
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DATA = ROOT / "data"
STATE = ROOT / "state"
SITE = ROOT / "site"
QUEUE = ROOT / "queue"            # finished slides waiting for their time (kept in the GitHub cache)
ROOMS_DIR = STATE / "rooms"       # the "before" room library (reused across posts)
HISTORY_FILE = STATE / "history.json"
PLAN_FILE = STATE / "plan.json"   # posts being made: planned, waiting for Gemini, ready, sent
TOKEN_FILE = STATE / "tiktok_token.enc"


class ConfigError(Exception):
    pass


def load_config(path: Path | None = None) -> dict:
    with open(path or ROOT / "config.toml", "rb") as f:
        return tomllib.load(f)


def load_dotenv(path: Path | None = None) -> None:
    """Reads KEY=value lines from .env into os.environ (never overrides real environment variables)."""
    path = path or ROOT / ".env"
    if not path.exists():
        return
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        key, value = key.strip(), value.strip().strip('"').strip("'")
        if key and value and key not in os.environ:
            os.environ[key] = value


def secret(name: str, required: bool = True) -> str | None:
    value = os.environ.get(name) or None
    if required and not value:
        raise ConfigError(f"{name} is not set. On GitHub add it with `gh secret set {name}`; "
                          f"on your PC put it in the .env file.")
    return value
