# Fouler Play - Current Status (2026-02-01)

## Active Account
- **Username:** LEBOTJAMESXD005
- **Password:** LeBotPassword2026!
- **ELO:** ~1191 (gen9ou, 48.8% GXE)
- **Profile:** https://pokemonshowdown.com/users/lebotjamesxd005

## Bot Configuration
- **Concurrent battles:** 2 (MAX_CONCURRENT_BATTLES = 2 in run.py)
- **Team:** fat-team-1-stall (stall composition)
- **Format:** gen9ou
- **Websocket:** wss://sim3.psim.us/showdown/websocket
- **Goal:** Climb to 1700 ELO
- **Run count:** 999999 (indefinite)

## What's Working ✅
- Message buffering (race condition fixed)
- 2 concurrent battle architecture
- Discord notifications via bot_monitor.py webhooks
- Replay saving (--save-replay always)
- Stream server on port 8777 (/status JSON + /overlay HTML)
- Stream overlay built (overlay.html — esports-style HUD)

## Known Issues ⚠️
- **Team rotation bug:** --team-list parameter crashes with `ValueError: Could not find [pokemon] in team_dict`. Using single --team-name as workaround.
- **Websocket disconnects:** Bot occasionally loses websocket connection. bot_monitor.py handles restarts.
- **Twitch stream key:** May need refresh — ffmpeg encodes fine but Twitch shows offline. Key file: `/home/ryan/Desktop/twitchstreamingkey.txt`

## Architecture

### Processes
- `bot_monitor.py` — Supervisor: spawns/monitors run.py workers, sends Discord webhooks for results/ELO
- `run.py` — Battle worker (2 instances): connects to Showdown, plays games
- `streaming/stream_server.py` — HTTP server on port 8777: /status endpoint + serves overlay.html

### Key Files
- `run.py` — Main battle bot entry point
- `bot_monitor.py` — Process supervisor + Discord reporting
- `streaming/overlay.html` — Twitch stream overlay (polls /status)
- `streaming/stream_server.py` — Status API + overlay server
- `streaming/auto_stream_firefox.py` — Auto-stream script (opens battles in browser + ffmpeg to Twitch)
- `teams/gen9/ou/fat-team-1-stall` — Current team file

### Logs
- `monitor.log` — Main bot output (stdout from bot_monitor.py)
- `/tmp/ffmpeg-stream.log` — Stream encoder output (when streaming)

## Commands

**Start bot (via monitor):**
```bash
cd /home/ryan/projects/fouler-play
nohup venv/bin/python -u bot_monitor.py > monitor.log 2>&1 &
```

**Start bot (direct, single worker):**
```bash
cd /home/ryan/projects/fouler-play
source venv/bin/activate
python -u run.py \
  --websocket-uri wss://sim3.psim.us/showdown/websocket \
  --ps-username LEBOTJAMESXD005 \
  --ps-password "LeBotPassword2026!" \
  --bot-mode search_ladder \
  --pokemon-format gen9ou \
  --team-name gen9/ou/fat-team-1-stall \
  --search-time-ms 3000 \
  --run-count 999999 \
  --save-replay always \
  --log-level INFO
```

**Start stream server:**
```bash
cd /home/ryan/projects/fouler-play
source venv/bin/activate
python streaming/stream_server.py &
```

**Stop everything:**
```bash
pkill -9 -f "fouler\|bot_monitor\|run.py.*showdown"
```

**Check status:**
```bash
ps aux | grep -E "bot_monitor|run.py" | grep -v grep
curl -s http://localhost:8777/status
```

## Account History
- LEBOTJAMESXD001-002: Early test accounts
- LEBOTJAMESXD003: Previous main account (deprecated 2026-02-01)
- **LEBOTJAMESXD005: Current active account**
