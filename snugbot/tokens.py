"""The TikTok login, stored encrypted in the repo (state/tiktok_token.enc).

The repo is public, so the tokens are encrypted with Fernet; the key is the TOKEN_KEY secret.
"""
from __future__ import annotations

import json
from pathlib import Path

from cryptography.fernet import Fernet, InvalidToken

from . import config


class TokenError(Exception):
    pass


def new_key() -> str:
    return Fernet.generate_key().decode()


def save(tokens: dict, key: str, path: Path | None = None) -> None:
    path = path or config.TOKEN_FILE
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(Fernet(key.encode()).encrypt(json.dumps(tokens).encode()) + b"\n")


def load(key: str, path: Path | None = None) -> dict:
    path = path or config.TOKEN_FILE
    if not path.exists():
        raise TokenError("No TikTok login saved yet. Run `python -m snugbot authorize` on your PC "
                         "(see SETUP.md), then commit state/tiktok_token.enc.")
    try:
        return json.loads(Fernet(key.encode()).decrypt(path.read_bytes().strip()))
    except (InvalidToken, ValueError) as e:
        raise TokenError("Can't unlock state/tiktok_token.enc: TOKEN_KEY doesn't match the key used "
                         "when you authorised. Set the same TOKEN_KEY, or run authorize again.") from e
