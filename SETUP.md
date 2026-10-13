# Setting up the Snug TikTok bot

The bot uses the same TikTok developer app as the Measy bot, and its own GitHub repo
(tomovids/snug-tiktok-bot), GitHub Pages address and secrets.

## 1. Keys (on your PC)

`.env` in this folder holds `GEMINI_API_KEY`, `TIKTOK_CLIENT_KEY`, `TIKTOK_CLIENT_SECRET` and
`TOKEN_KEY` (the key that locks the TikTok login). It is never uploaded. The same values are in the
repo's GitHub secrets (Settings → Secrets and variables → Actions).

In Google AI Studio (https://aistudio.google.com) set a monthly spending cap for the key.

## 2. Let TikTok trust the slide address

TikTok only pulls pictures from addresses the app has verified.

1. developer.tiktok.com → your app → **URL properties** (or Content Posting API → Verify domains)
   → **Add URL prefix**: `https://tomovids.github.io/snug-tiktok-bot/`
2. TikTok gives you a file to download (`tiktok....txt`). Put it in this repo's `site/` folder,
   commit and push; wait a minute for GitHub Pages, then click **Verify** in TikTok.

## 3. @TrySnug as a test user

@TrySnug must be a **Target user** in the app's Sandbox (already done).

## 4. Log in once

With the browser logged in to TikTok as @TrySnug (log out of other accounts first):

```
.\.venv\Scripts\python.exe -m snugbot authorize
git add state/tiktok_token.enc; git commit -m "TikTok login"; git push
```

The login lasts about a year; the bot warns in its log a month before it runs out.

## 5. Running

GitHub → Actions → **Snug drafts** runs every hour by itself (or press **Run workflow**). The first run
plans the next posts and orders their pictures; a later run sends each one at its time.
`python -m snugbot status` shows what's planned.
