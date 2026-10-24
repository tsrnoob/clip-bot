#!/usr/bin/env python3
"""ClipBot v1: find trending Twitch + Kick clips, render vertical shorts, post to Discord."""
import json
import os
import shutil
import re
import subprocess
import sys
import textwrap
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path

import requests
import yaml

ROOT = Path(__file__).resolve().parent
STATE = ROOT / "state" / "seen.json"
HTTP_TIMEOUT = 20
YTDLP = [sys.executable, "-m", "yt_dlp"]  # works even when pip's Scripts dir is not on PATH


def log(msg):
    print(f"[clipbot] {msg}", flush=True)


def parse_ts(s):
    s = s.replace("Z", "+00:00")
    # Kick uses nanosecond-ish fractions; trim to microseconds
    s = re.sub(r"(\.\d{6})\d+", r"\1", s)
    return datetime.fromisoformat(s)


# ---------------------------------------------------------------- state

def load_seen():
    if STATE.exists():
        return set(json.loads(STATE.read_text()))
    return set()


def save_seen(seen):
    STATE.parent.mkdir(parents=True, exist_ok=True)
    STATE.write_text(json.dumps(sorted(seen), indent=1), encoding="utf-8")


# ---------------------------------------------------------------- Twitch

class Twitch:
    API = "https://api.twitch.tv/helix"

    def __init__(self, client_id, client_secret):
        r = requests.post("https://id.twitch.tv/oauth2/token", data={
            "client_id": client_id, "client_secret": client_secret,
            "grant_type": "client_credentials"}, timeout=HTTP_TIMEOUT)
        r.raise_for_status()
        self.headers = {"Client-Id": client_id,
                        "Authorization": f"Bearer {r.json()['access_token']}"}

    def get(self, path, **params):
        r = requests.get(f"{self.API}/{path}", headers=self.headers,
                         params=params, timeout=HTTP_TIMEOUT)
        r.raise_for_status()
        return r.json()["data"]

    def clips(self, cfg, since):
        tcfg = cfg["twitch"]
        query = []
        if tcfg.get("streamers"):
            for u in self.get("users", login=tcfg["streamers"]):
                query.append({"broadcaster_id": u["id"]})
        for name in tcfg.get("categories", []):
            for g in self.get("games", name=name):
                query.append({"game_id": g["id"]})
        out = []
        for q in query:
            for c in self.get("clips", first=50, started_at=since.isoformat(), **q):
                if tcfg.get("language") and c.get("language") != tcfg["language"]:
                    continue
                out.append({
                    "id": f"twitch:{c['id']}",
                    "platform": "twitch",
                    "streamer": c["broadcaster_name"],
                    "streamer_slug": c["broadcaster_name"].lower(),
                    "title": c["title"],
                    "views": c["view_count"],
                    "duration": float(c["duration"]),
                    "created_at": parse_ts(c["created_at"]),
                    "url": c["url"],
                    "media_url": None,
                })
        return out


# ---------------------------------------------------------------- Kick

def kick_clips(cfg, since):
    kcfg = cfg["kick"]
    urls = [f"https://kick.com/api/v2/channels/{s}/clips" for s in kcfg.get("streamers", [])]
    if kcfg.get("sitewide_trending"):
        urls.append("https://kick.com/api/v2/clips")
    out = []
    for url in urls:
        try:
            r = requests.get(url, params={"sort": "view", "time": "day"}, timeout=HTTP_TIMEOUT)
            r.raise_for_status()
            clips = r.json().get("clips", [])
        except (requests.RequestException, ValueError) as e:
            # Kick may refuse cloud IPs (Cloudflare). No circumvention: skip this run.
            log(f"kick: skipping {url} ({e})")
            continue
        for c in clips:
            created = parse_ts(c["created_at"])
            if created < since or c.get("privacy") != "public":
                continue
            ch = c["channel"]
            out.append({
                "id": f"kick:{c['id']}",
                "platform": "kick",
                "streamer": ch["username"],
                "streamer_slug": ch["slug"],
                "title": c["title"],
                "views": c.get("views") or c.get("view_count") or 0,
                "duration": float(c["duration"]),
                "created_at": created,
                "url": f"https://kick.com/{ch['slug']}/clips/{c['id']}",
                "media_url": c.get("video_url") or c.get("clip_url"),
            })
    return out


# ---------------------------------------------------------------- filter + rank

def select(clips, cfg, seen, now):
    f = cfg["filter"]
    uniq = {}
    for c in clips:
        if c["id"] in seen or c["views"] < f["min_views"]:
            continue
        if not f["min_duration"] <= c["duration"] <= f["max_duration"]:
            continue
        hours = max((now - c["created_at"]).total_seconds() / 3600, 0.25)
        c["score"] = c["views"] / hours
        uniq[c["id"]] = c
    picked, per_streamer = [], {}
    for c in sorted(uniq.values(), key=lambda c: c["score"], reverse=True):
        key = (c["platform"], c["streamer_slug"])
        if per_streamer.get(key, 0) >= f["max_per_streamer"]:
            continue
        per_streamer[key] = per_streamer.get(key, 0) + 1
        picked.append(c)
        if len(picked) >= f["max_per_run"]:
            break
    return picked


# ---------------------------------------------------------------- download

def download(clip, workdir):
    dest = workdir / f"{clip['id'].replace(':', '_')}.mp4"
    cmd = YTDLP + ["-q", "--no-warnings", "-f", "best[ext=mp4]/best",
           "--merge-output-format", "mp4", "-o", str(dest), clip["url"]]
    if subprocess.run(cmd).returncode == 0 and dest.exists():
        return dest
    if clip.get("media_url"):
        log(f"yt-dlp failed, falling back to direct media for {clip['id']}")
        cmd = ["ffmpeg", "-y", "-loglevel", "error", "-i", clip["media_url"],
               "-c", "copy", "-bsf:a", "aac_adtstoasc", str(dest)]
        if subprocess.run(cmd).returncode == 0 and dest.exists():
            return dest
    return None


# ---------------------------------------------------------------- edit

EMOJI_RE = re.compile("[\U0001F000-\U0001FFFF☀-➿️‍]+")


def title_lines(title, e):
    t = EMOJI_RE.sub("", title).strip()
    t = re.sub(r"\s+", " ", t)
    if len(t) > e["title_max_chars"]:
        t = t[:e["title_max_chars"] - 1].rstrip() + "…"
    lines = textwrap.wrap(t, e["title_line_chars"])[:e["title_max_lines"]]
    return lines or [""]


def ff_escape(s):
    # escaping for drawtext text= inside a filtergraph
    return (s.replace("\\", "\\\\").replace("'", "’")
             .replace(":", "\\:").replace("%", "\\%").replace(",", "\\,"))


def render(src, clip, cfg, dest):
    e = cfg["edit"]
    W, H = e["width"], e["height"]
    credit = f"{'twitch.tv' if clip['platform'] == 'twitch' else 'kick.com'}/{clip['streamer_slug']}"
    font = e["font"]
    fs = e["title_font_size"]
    draw = []
    for i, line in enumerate(title_lines(clip["title"], e)):
        y = 140 + i * int(fs * 1.25)
        draw.append(f"drawtext=fontfile={font}:text='{ff_escape(line)}':fontsize={fs}:"
                    f"fontcolor=white:borderw=6:bordercolor=black:x=(w-text_w)/2:y={y}")
    draw.append(f"drawtext=fontfile={font}:text='{ff_escape(credit)}':fontsize={e['credit_font_size']}:"
                f"fontcolor=white:borderw=4:bordercolor=black:x=(w-text_w)/2:y=h-220")
    fg = (f"[0:v]split[a][b];"
          f"[a]scale={W}:{H}:force_original_aspect_ratio=increase,crop={W}:{H},"
          f"boxblur={e['blur']}:2,eq=brightness={e['darken'] - 1:.2f}[bg];"
          f"[b]scale={W}:-2[fg];"
          f"[bg][fg]overlay=(W-w)/2:(H-h)/2,{','.join(draw)},fps={e['fps']},format=yuv420p[v]")
    cmd = ["ffmpeg", "-y", "-loglevel", "error", "-i", str(src), "-filter_complex", fg,
           "-map", "[v]", "-map", "0:a?", "-c:v", "libx264", "-preset", "medium", "-crf", "20",
           "-c:a", "aac", "-b:a", "128k", "-movflags", "+faststart", str(dest)]
    subprocess.run(cmd, check=True, cwd=ROOT)
    return dest


def shrink(src, dest, max_mb):
    """2-pass 720p encode targeting max_mb (with headroom)."""
    dur = float(subprocess.check_output(
        ["ffprobe", "-v", "error", "-show_entries", "format=duration", "-of", "csv=p=0", str(src)]))
    total_kbps = max_mb * 8 * 1024 * 0.92 / dur
    v_kbps = max(int(total_kbps - 96), 200)
    passlog = str(dest.with_suffix(""))
    base = ["ffmpeg", "-y", "-loglevel", "error", "-i", str(src), "-vf", "scale=720:1280",
            "-c:v", "libx264", "-b:v", f"{v_kbps}k", "-passlogfile", passlog]
    subprocess.run(base + ["-pass", "1", "-an", "-f", "mp4", os.devnull], check=True)
    subprocess.run(base + ["-pass", "2", "-c:a", "aac", "-b:a", "96k",
                           "-movflags", "+faststart", str(dest)], check=True)
    return dest


# ---------------------------------------------------------------- output

def caption(clip, cfg):
    tags = list(cfg["output"]["hashtags"])
    tags.append("#" + re.sub(r"\W", "", clip["streamer_slug"]))
    return f"{EMOJI_RE.sub('', clip['title']).strip()} (via @{clip['streamer']})\n{' '.join(tags)}"


def post_discord(webhook, clip, cfg, video):
    msg = (f"**{clip['title']}**\n{clip['platform']} · {clip['streamer']} · "
           f"{clip['views']:,} views · {clip['duration']:.0f}s · {clip['score']:.0f} views/h\n"
           f"<{clip['url']}>\n```{caption(clip, cfg)}```")
    with open(video, "rb") as fh:
        r = requests.post(webhook, data={"content": msg[:2000]},
                          files={"file": (video.name, fh, "video/mp4")}, timeout=120)
    r.raise_for_status()


# ---------------------------------------------------------------- main

def find_ffmpeg():
    """Put a winget-installed ffmpeg on PATH if the shell hasn't picked it up yet."""
    if shutil.which("ffmpeg") or os.name != "nt":
        return
    pkgs = Path(os.getenv("LOCALAPPDATA", "")) / "Microsoft" / "WinGet" / "Packages"
    for exe in pkgs.glob("*FFmpeg*/*/bin/ffmpeg.exe"):
        os.environ["PATH"] = f"{exe.parent}{os.pathsep}{os.environ['PATH']}"
        return


def main():
    cfg = yaml.safe_load((ROOT / "config.yaml").read_text(encoding="utf-8"))
    find_ffmpeg()
    missing = [t for t in ("ffmpeg", "ffprobe") if not shutil.which(t)]
    if missing:
        sys.exit(f"[clipbot] not found on PATH: {', '.join(missing)} (install ffmpeg)")
    now = datetime.now(timezone.utc)
    since = now - timedelta(hours=cfg["filter"]["lookback_hours"])
    seen = load_seen()

    clips = []
    cid, csec = os.getenv("TWITCH_CLIENT_ID"), os.getenv("TWITCH_CLIENT_SECRET")
    if cid and csec:
        try:
            clips += Twitch(cid, csec).clips(cfg, since)
        except requests.RequestException as e:
            log(f"twitch: failed ({e})")
    else:
        log("twitch: no credentials, skipping")
    if cfg["kick"].get("enabled"):
        clips += kick_clips(cfg, since)
    log(f"found {len(clips)} candidate clips")

    picked = select(clips, cfg, seen, now)
    log(f"selected {len(picked)}")

    out = ROOT / cfg["output"]["dir"]
    work = out / "raw"
    work.mkdir(parents=True, exist_ok=True)
    webhook = os.getenv("DISCORD_WEBHOOK_URL")
    max_mb = cfg["output"]["discord_max_mb"]

    for clip in picked:
        log(f"processing {clip['id']} — {clip['title']!r}")
        src = download(clip, work)
        if not src:
            log("download failed, skipping")
            continue
        name = clip["id"].replace(":", "_")
        final = render(src, clip, cfg, out / f"{name}.mp4")
        (out / f"{name}.txt").write_text(caption(clip, cfg) + f"\n\nSource: {clip['url']}\n", encoding="utf-8")
        seen.add(clip["id"])
        if webhook:
            send = final
            if final.stat().st_size > max_mb * 1024 * 1024:
                send = shrink(final, work / f"{name}_720.mp4", max_mb)
            try:
                post_discord(webhook, clip, cfg, send)
            except requests.RequestException as e:
                log(f"discord post failed ({e})")
            time.sleep(2)
    save_seen(seen)


if __name__ == "__main__":
    sys.exit(main())
