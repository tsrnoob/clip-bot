# ClipBot — project notes for Claude

Automated pipeline: find trending Twitch + Kick clips → edit into vertical
shorts → save them so the owner reposts manually on TikTok/Shorts/Reels.
Owner often reads on mobile: keep replies short and practical.

## Environment
- Runs locally on the owner's Windows PC at `D:\clip bot` (PowerShell; quote
  the path because of the space). Python 3.13, ffmpeg via winget
  (`Gyan.FFmpeg`; `clipbot.py` finds it even if PATH isn't refreshed).
- yt-dlp is invoked as `python -m yt_dlp` (pip's user Scripts dir isn't on PATH).
- Font is bundled: `fonts/DejaVuSans-Bold.ttf`; ffmpeg runs with cwd = repo.
- Also has a GitHub Actions workflow (`.github/workflows/clips.yml`, every 6h)
  — not deployed yet (needs secrets TWITCH_CLIENT_ID, TWITCH_CLIENT_SECRET,
  DISCORD_WEBHOOK_URL).

## v1 (built, `clipbot.py` + `config.yaml`)
- FIND: Twitch Helix API (clips per streamer/category, last 24h);
  Kick public JSON `kick.com/api/v2/channels/{slug}/clips` and
  `kick.com/api/v2/clips`, `sort=view&time=day`. Kick verified working.
- FILTER: ≥500 views, 6–90s, Twitch language en, not in `state/seen.json`.
  Rank by views/hour; max 2 per streamer, 5 per run.
- EDIT: 1080x1920, blurred+darkened fill background, clip centered,
  title on top, `kick.com/<slug>` / `twitch.tv/<slug>` credit at bottom.
- OUTPUT: `out/*.mp4` + `out/*.txt` caption; Discord webhook if set
  (720p 2-pass copy when >10 MB).

## Rules
- No bot-check circumvention: no browser impersonation, no fake browser
  user-agents, no Cloudflare workarounds. If a site refuses, skip it.
- Sample shorts downloaded for style analysis are never reposted.
- Prefer royalty-free sound effects (e.g. Pixabay); famous meme sounds can
  get copyright-flagged.
- Only clips from streamers who allow clipping / clipping campaigns
  (e.g. Whop Content Rewards); keep the credit visible.

## v2 plan (in order)
1. Output to GitHub Releases (full quality) instead of Discord; optional
   short Discord message with the link.
2. Learn style from 10–20 sample shorts the owner names (TikTok / Shorts /
   Reels links, or uploaded files if a site blocks): caption look (OCR with
   tesseract/opencv), word swaps (faster-whisper word timestamps vs on-screen
   captions), sound effects (audio spikes vs speech). Output a written
   STYLE GUIDE for the owner to approve before building.
3. Pop-term captions (hybrid): owner's dictionary of word-groups → meme terms;
   an AI step picks 1–3 fitting swaps per clip; log swaps per clip.
4. Animated burned-in captions from Whisper word timestamps (1–3 words at a time).
5. Sound effects: ~20–30 tagged sounds; triggers = loudness spikes + AI-marked
   moments; max 2–4 per clip; duck stream audio under each.
6. Split layout (owner asked for this early): facecam crop on top
   (1080x608), gameplay crop below (1080x1312), title on the seam. Crop, don't
   stretch; at most ~15% horizontal squeeze on gameplay. Facecam box set per
   streamer in config (suggest it from a frame); keep facecam out of the
   gameplay crop; clips without a facecam overlay get a single crop centered on the streamer.
   Prototype rendered on a Kick clip (kaysan, facecam at 0,0 530x298 in 1920x1080).
   Pending owner choice: crop-only vs 15% squeeze; title on seam vs top.
7. Later: trim dead air at the start.

## Open decisions
- Where v2 runs: GitHub Actions (needs ANTHROPIC_API_KEY for the AI step) vs
  locally / Claude Code.
- Which sample accounts to learn from (owner will provide).
