"""Command line: python -m snugbot <tick|send|stats|dashboard|preview|authorize|status|newkey>."""
from __future__ import annotations

import argparse
import json
import os
import random
import secrets as pysecrets
import sys
import time
import webbrowser
from datetime import date, datetime, timezone
from pathlib import Path

from . import config, pipeline, planner, render, rooms, tokens
from .config import ConfigError, load_config, load_dotenv, secret
from .gemini import Gemini, GeminiError
from .history import History
from .tiktok import TikTok, TikTokError

MANIFEST = config.ROOT / "built.json"


def log(msg: str) -> None:
    print(msg, flush=True)


def github_output(**values) -> None:
    path = os.environ.get("GITHUB_OUTPUT")
    if path:
        with open(path, "a", encoding="utf-8") as f:
            for k, v in values.items():
                f.write(f"{k}={v}\n")


def gemini(cfg: dict) -> Gemini:
    g = cfg["gemini"]
    return Gemini(secret("GEMINI_API_KEY"), g["image_model"], g["text_model"])


def queue_snapshot() -> set[tuple[str, int]]:
    return {(str(f.relative_to(config.QUEUE)), f.stat().st_size) for f in config.QUEUE.rglob("*") if f.is_file()} \
        if config.QUEUE.exists() else set()


def cmd_tick(args, cfg) -> int:
    """The hourly run: collect finished pictures, plan and order upcoming posts, list what's due."""
    github_output(send="false")
    if not config.TOKEN_FILE.exists():
        log("@TrySnug isn't logged in to TikTok yet (no state/tiktok_token.enc; see SETUP.md). Skipping.")
        MANIFEST.write_text("[]\n", encoding="utf-8")
        return 0
    before = queue_snapshot()
    ready = pipeline.tick(cfg, gemini(cfg), log=log)
    github_output(queue_changed=str(queue_snapshot() != before).lower())
    from . import dashboard
    dashboard.rebuild(cfg)              # the "posts being made" part stays current
    MANIFEST.write_text(json.dumps(ready) + "\n", encoding="utf-8")
    if ready:
        log(f"Due now: {', '.join(ready)}")
        github_output(send="true")
    return 0


def cmd_send(args, cfg) -> int:
    ids = json.loads(MANIFEST.read_text(encoding="utf-8")) if MANIFEST.exists() else []
    if not ids:
        log("Nothing to send.")
        return 0
    tt = TikTok(secret("TIKTOK_CLIENT_KEY"), secret("TIKTOK_CLIENT_SECRET"))
    key = secret("TOKEN_KEY")
    failed = 0
    for pid in ids:
        log(f"\n== Sending {pid} ==")
        try:
            pipeline.send(pid, cfg, tt, key, log=log)
        except (TikTokError, tokens.TokenError) as e:
            if "too_many_pending" in str(e):
                log("::warning::TikTok's inbox is full (5 drafts waiting). Post or delete some in TikTok; "
                    "the bot tries again in 2 hours.")
                break
            if "url_ownership_unverified" in str(e):
                log("::warning::TikTok hasn't verified https://tomovids.github.io/snug-tiktok-bot/ yet (SETUP.md "
                    "step 2). The bot tries again every hour.")
                break
            log(f"ERROR sending {pid}: {e}")
            failed += 1
    return 1 if failed else 0


def cmd_preview(args, cfg) -> int:
    """Makes posts on your PC into out/preview (nothing is saved or sent). Uses normal-price pictures."""
    ai = gemini(cfg)
    out = Path(args.out).resolve()
    hist = History.load()
    day = date.today()
    for n in range(args.n):
        fmt = args.format or cfg["schedule"]["formats"][n % len(cfg["schedule"]["formats"])]
        rng = random.Random(f"preview/{time.time()}/{n}")
        fmt, item, room = planner.choose(fmt, day, hist, cfg, rng)
        if args.room:
            room = args.room
        kind, problem = ("empty", None) if fmt == "makeover" else ("lived", item["id"])
        library = rooms.load()
        entry = rooms.pick(kind, room, problem, day, cfg, rng, library) if args.reuse else None
        if entry:
            before = rooms.image(entry)
        else:
            text, _, _ = rooms.prompt(kind, room, item.get("room_look"), rng)
            before = rooms.fit(ai.image(text))
        plan = planner.write(fmt, item, room, hist, cfg, ai, rng, log)
        log(f"\n== {fmt}: {plan.hook} ({room})")
        tmp_room = out / f"post-{n + 1}" / "before.jpg"
        tmp_room.parent.mkdir(parents=True, exist_ok=True)
        before.save(tmp_room, "JPEG", quality=90)
        p = {"id": f"post-{n + 1}", "plan": plan.to_dict(), "room_file": str(tmp_room),
             "keys": [f"{n}/{i}" for i in range(len(plan.slides))] + [f"{n}/cta"]}
        saved_queue = config.QUEUE
        config.QUEUE = out
        try:
            pipeline.finish(p, {}, ai, cfg, log)
        finally:
            config.QUEUE = saved_queue
        (out / p["id"] / "caption.txt").write_text(plan.caption, encoding="utf-8")
    log(f"\nGemini: {ai.usage} (about ${ai.usage['images'] * cfg['gemini']['price_now']:.2f}). Slides in {out}")
    return 0


def cmd_authorize(args, cfg) -> int:
    t = cfg["tiktok"]
    key = secret("TOKEN_KEY")
    tt = TikTok(secret("TIKTOK_CLIENT_KEY"), secret("TIKTOK_CLIENT_SECRET"))
    url = tt.authorize_url(t["redirect_uri"], t["scopes"], pysecrets.token_urlsafe(12))
    log("Opening TikTok in your browser. Log in as @TrySnug and approve access.")
    log("(If the browser is logged in to a different TikTok account, log out of it first.)")
    log(f"If nothing opens, go to:\n{url}\n")
    webbrowser.open(url)
    code = input("Paste the code shown on the page here: ").strip()
    login = tt.exchange_code(code, t["redirect_uri"])
    try:
        name = tt.display_name(login["access_token"])
        log(f"Connected as: {name}")
        if name and input("Is this @TrySnug? [y/n] ").strip().lower() not in ("y", "yes"):
            log("Not saved. Log out of TikTok in the browser, log in as @TrySnug and run authorize again.")
            return 1
    except TikTokError as e:
        log(f"(Couldn't read the account name: {e})")
    tokens.save(login, key)
    log("Saved the login to state/tiktok_token.enc (encrypted). Now commit and push it:")
    log('  git add state/tiktok_token.enc && git commit -m "TikTok login" && git push')
    return 0


def cmd_status(args, cfg) -> int:
    state = pipeline.load_plan()
    for p in sorted(state["posts"], key=lambda x: x["time"]):
        log(f"{p['time'][:16]}  {p['status']:8s} {p['plan']['hook']}  (~${p.get('cost', 0):.2f})")
    hist = History.load()
    for p in hist.posts[-args.n:]:
        log(f"sent {p['date']}  {p.get('status', '?'):20s} {p.get('hook', '')}")
    log(f"{len(rooms.load())} 'before' rooms in the library.")
    key = os.environ.get("TOKEN_KEY")
    if not config.TOKEN_FILE.exists():
        log("Not logged in to TikTok yet.")
    elif key:
        try:
            days = (tokens.load(key)["refresh_expires_at"] - time.time()) / 86400
            log(f"TikTok login valid for about {days:.0f} more days.")
        except tokens.TokenError as e:
            log(str(e))
    return 0


def cmd_stats(args, cfg) -> int:
    """Reads @TrySnug's TikTok numbers, updates the tuning and rebuilds the dashboard (twice a day)."""
    from . import dashboard, stats, tuning
    key = secret("TOKEN_KEY")
    tt = TikTok(secret("TIKTOK_CLIENT_KEY"), secret("TIKTOK_CLIENT_SECRET"))
    login = tt.refresh(tokens.load(key))
    tokens.save(login, key)
    account = tt.account_stats(login["access_token"])
    videos = tt.videos(login["access_token"], max_pages=cfg["tracking"]["video_pages"])
    st = stats.update(stats.load(), account, videos, History.load())
    stats.save(st)
    tune = tuning.compute(st, cfg)
    tuning.save(tune)
    dashboard.write(dashboard.build(st, tune, History.load(), pipeline.load_plan(), cfg), cfg)
    matched = sum(1 for v in st["videos"].values() if v.get("post"))
    log(f"Followers {account.get('follower_count')}, {len(videos)} posts read, {matched} matched to the bot's "
        f"drafts, {tune['scored_posts']} scored. Auto-tuning {'ON' if tune['active'] else 'not yet'} "
        f"(needs {tune['needed']} scored posts).")
    return 0


def cmd_dashboard(args, cfg) -> int:
    from . import dashboard
    dashboard.rebuild(cfg)
    log(f"Dashboard data written to {dashboard.folder(cfg) / 'data.json'}")
    return 0


def cmd_newkey(args, cfg) -> int:
    log(tokens.new_key())
    return 0


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(prog="snugbot", description="Snug TikTok slideshow bot")
    sub = p.add_subparsers(dest="cmd", required=True)
    sub.add_parser("tick", help="the hourly GitHub run: make posts ahead and list what's due")
    sub.add_parser("send", help="send what tick listed to TikTok drafts")
    pv = sub.add_parser("preview", help="make posts on your PC to look at (nothing is sent)")
    pv.add_argument("-n", type=int, default=2)
    pv.add_argument("--format", choices=["makeover", "problem"])
    pv.add_argument("--room", choices=["living room", "bedroom", "kitchen"])
    pv.add_argument("--reuse", action="store_true", help="use a library room if there's one")
    pv.add_argument("--out", default="out/preview")
    sub.add_parser("authorize", help="log @TrySnug in to TikTok once (run on your PC)")
    st = sub.add_parser("status", help="posts being made and recent drafts")
    st.add_argument("-n", type=int, default=10)
    sub.add_parser("newkey", help="print a new TOKEN_KEY")
    sub.add_parser("stats", help="read the TikTok numbers, update the tuning and the dashboard")
    sub.add_parser("dashboard", help="rebuild the dashboard data from the saved files")
    args = p.parse_args(argv)
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    load_dotenv()
    cfg = load_config()
    commands = {"tick": cmd_tick, "send": cmd_send, "preview": cmd_preview, "authorize": cmd_authorize,
                "status": cmd_status, "newkey": cmd_newkey, "stats": cmd_stats, "dashboard": cmd_dashboard}
    try:
        return commands[args.cmd](args, cfg)
    except (ConfigError, TikTokError, tokens.TokenError, GeminiError, FileNotFoundError) as e:
        log(f"\nERROR: {e}")
        return 1


if __name__ == "__main__":
    sys.exit(main())
