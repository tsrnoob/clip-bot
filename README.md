# ClipBot

Finds trending Twitch + Kick clips, renders them as 1080x1920 shorts
(blurred background, centered clip, title on top, streamer credit at bottom)
and posts them to Discord for manual reposting.

## Setup
1. Repo secrets: `TWITCH_CLIENT_ID`, `TWITCH_CLIENT_SECRET` (dev.twitch.tv app),
   `DISCORD_WEBHOOK_URL`.
2. Edit `config.yaml` (streamers, categories, filters).
3. Actions → clips → Run workflow (also runs every 6h).

Over-10 MB renders go to Discord as a 720p copy; full 1080p is in the run's
artifacts (14 days).

## Local run
    pip install -r requirements.txt   # plus ffmpeg
    python clipbot.py

Only repost clips from streamers who allow clipping, and keep the credit visible.
