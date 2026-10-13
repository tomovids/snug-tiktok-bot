# Snug TikTok slideshow bot

Twice a day (12:00 and 19:00 UK time) a GitHub Action drops an 8-slide TikTok photo carousel into
@TrySnug's TikTok drafts. Open the notification, add a sound, post. It runs on GitHub's servers, so
your PC can be off.

Every post is ONE room restyled several ways (the same walls, window and camera on every slide), in
two formats that alternate:

- **Makeover** (12:00): "let's make this japandi ✍️ (inspired)" on an empty room, then 6 versions of
  that style, each with a style label and a callout on a standout piece that's really in the picture.
- **Problem** (19:00): "i'm done with beige everything! 😅 which would you choose?" on a lived-in room,
  then 6 colourful restyles, each with one opinionated line.
- The last slide invites people to try it ("Have any Pinterest inspo you want to try? 📌" / "try it on
  your own room with Snug").

Styles, problems and rooms rotate (`data/styles.toml`) so posts don't repeat; the voice is in
`data/brand.md`.

## Pictures and cost

All pictures are made with Google's Nano Banana (Gemini 2.5 Flash Image). Each post needs 7 restyles,
ordered about a day ahead from Google's **half-price batch service** (they usually come back within
minutes). The "before" rooms are kept in `state/rooms/` and reused for different styles (each rests
three weeks), so a new one is only made now and then. About 13-15p a post, roughly £8 a month.
If a batch is late, the post is made straight away at the normal price (about 31p) so it still goes
out. Set a monthly spending cap in Google AI Studio to be sure.

## Commands

```
python -m snugbot preview -n 2          # make posts into out\preview to look at (normal price, nothing sent)
python -m snugbot status                # posts being made, recent drafts, the TikTok login
python -m snugbot authorize             # log @TrySnug in to TikTok (once a year)
python -m snugbot tick / send           # what the GitHub Action runs
```

On Windows use `.\.venv\Scripts\python.exe` instead of `python`. Tests: `.\.venv\Scripts\python.exe -m pytest`.

Setup: [SETUP.md](SETUP.md). Fonts: TikTok Sans and Noto Color Emoji (SIL Open Font License, in `fonts/`).
