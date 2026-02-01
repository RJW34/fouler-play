# Starting the Fouler Play Twitch Stream

## Quick Start

```bash
cd /home/ryan/projects/fouler-play
python3 streaming/auto_stream_firefox.py
```

## Available Stream Scripts

| Script | Browser | Notes |
|--------|---------|-------|
| `auto_stream_firefox.py` | Firefox | Recommended for Linux |
| `auto_stream_stable.py` | Chrome | Alternative (needs `--ozone-platform=x11 --no-sandbox` on Ubuntu) |
| `auto_stream_headless.py` | None | ffmpeg-only, no battle windows |

## Prerequisites

1. **Bot running:** `./start.sh` or `nohup venv/bin/python -u bot_monitor.py > monitor.log 2>&1 &`
2. **Stream server:** `python streaming/stream_server.py &` (port 8777)
3. **Twitch stream key:** `/home/ryan/Desktop/twitchstreamingkey.txt`
4. **Display:** X11 session on :0

## What It Does

1. Reads `monitor.log` for active battle IDs (pattern: `battle-gen9ou-NNNN`)
2. Opens battles in browser windows on the desktop
3. Captures entire X11 display via ffmpeg (1920x1080, 15fps, 2.5Mbps)
4. Pushes RTMP stream to Twitch

## Stream Overlay

The overlay is served at `http://localhost:8777/overlay` and shows:
- ELO, W/L record, win rate
- Battle status (searching/battling/idle)
- Recent results strip
- Streak indicator

To composite: Add as Browser Source in OBS pointing to `http://localhost:8777/overlay`

For direct streaming (no OBS), the overlay is part of the desktop capture.

## Manual Stream (without auto_stream script)

```bash
# 1. Open Showdown in Chrome
DISPLAY=:0 google-chrome --no-sandbox --ozone-platform=x11 "https://play.pokemonshowdown.com/" &

# 2. Start ffmpeg
STREAM_KEY=$(cat /home/ryan/Desktop/twitchstreamingkey.txt)
DISPLAY=:0 ffmpeg -y \
  -f x11grab -video_size 1920x1080 -framerate 15 -i :0 \
  -f lavfi -i anullsrc=r=44100:cl=stereo \
  -c:v libx264 -preset veryfast -b:v 2500k -maxrate 2500k -bufsize 5000k \
  -pix_fmt yuv420p -g 30 -c:a aac -b:a 128k \
  -f flv "rtmp://live.twitch.tv/app/${STREAM_KEY}"
```

## Troubleshooting

**Stream not showing on Twitch:**
- Verify stream key is current (get new one at https://dashboard.twitch.tv/settings/stream)
- Check ffmpeg output for errors: `tail /tmp/ffmpeg-stream.log`
- ffmpeg encoding without Twitch showing = expired stream key

**Chrome won't open / no window visible:**
- Use `--ozone-platform=x11 --no-sandbox` flags
- Firefox snap is broken on this system — use google-chrome
- Verify with `DISPLAY=:0 wmctrl -l`

**Battles not appearing in browser:**
- Check `monitor.log` has recent battle IDs: `grep "battle-gen9ou" monitor.log | tail -5`
- Bot websocket may have closed — check bot status

## Twitch Channel
- URL: https://twitch.tv/dekubotbygoofy
- Account: dekubotbygoofy (Google OAuth via Dekubot6967@gmail.com)
