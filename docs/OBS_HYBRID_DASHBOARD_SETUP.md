# OBS Hybrid Dashboard Setup

## 1) Verify the managed local server

For spectator-mode battle slots, `HERMES-FoulerObsServer` runs
`streaming/run_obs_server_service.py`, which hosts the overlays and updates OBS
Browser Source URLs. The retired `stream_server.py` cannot start or serve output.

```powershell
Get-Service HERMES-FoulerObsServer
Invoke-WebRequest -UseBasicParsing http://127.0.0.1:8777/health
```

Default port is `8777` unless `OBS_SERVER_PORT` is set.
Battle launchers never start a second helper server.

## 1.5) Install the Hybrid Scene Collection (local OBS)

Build a new scene collection derived from your existing `fouler_play_scenes.json`:

```bash
py -3 scripts/build_obs_hybrid_scene_collection.py
```

This writes:

- `%APPDATA%\obs-studio\basic\scenes\fouler_play_hybrid_scenes.json`
- `streaming/fouler_play_hybrid_scenes.json` (repo copy)

In OBS: `Scene Collection` -> select `Fouler Play Hybrid Battles`.

## 2) Operator dashboard (normal browser only)

Open:

```text
http://localhost:8777/dashboard/hybrid
```

This is the full decision dashboard with timeline, latest decision details, and learning signals. It is for local/operator review only. Do not add it to public OBS scenes.

## 3) Public OBS Browser Source URL

Use this for the OBS-optimized overlay:

```text
http://localhost:8777/overlay?mode=bottom&hide_recent=1
```

Recommended Browser Source settings:

- Width: `1920`
- Height: `1080`
- FPS: `30`
- Custom frame rate enabled: `30` (optional but recommended)
- Shutdown source when not visible: `Off` (recommended for stable cache)
- Refresh browser when scene becomes active: `On`

For 720p scenes:

- Width: `1280`
- Height: `720`
- FPS: `30`

## 4) Refresh behavior

- The overlay polls `/api/dashboard/state` every ~`1.5s`.
- Data is cached server-side; trace ingestion uses incremental file change checks.
- No mouse/keyboard interaction is required.

## 5) API endpoints

- `GET /api/dashboard/state`
- `GET /api/dashboard/turns?limit=50`
- `GET /api/dashboard/battles`

## 6) Troubleshooting

### Blank page in OBS

1. Verify server is running on the same machine/port:
   - `http://localhost:8777/obs-debug`
2. Confirm Browser Source URL exactly matches:
   - `http://localhost:8777/overlay?mode=bottom&hide_recent=1`
3. In OBS Browser Source, click `Refresh cache of current page`.
4. If battle slots stay idle, verify OBS WebSocket is connected in debug JSON:
   - `obs.client_status` should be `connected`
   - `obs.sources` should include `Battle Slot 1/2/3`

### Spectator not seeing battles

1. Set `SPECTATOR_USERNAME` in `.env`.
2. Keep `ENABLE_SPECTATOR_INVITES=1` (or leave unset; invites auto-enable when
   a spectator username is provided).
3. In OBS, open each `Battle Slot` Browser Source and log in to Pokemon
   Showdown with the spectator account once. Cookies persist per source.
4. Start the bot and check logs for:
   - `Inviting spectator: <username>`
5. Confirm `obs_server.log` contains:
   - `Setting to battle ...`
   - `Successfully updated to https://play.pokemonshowdown.com/battle-...`

### Data looks stale

1. Check trace files exist in `logs/decision_traces`.
2. Confirm `active_battles.json`, `stream_status.json`, and `daily_stats.json` are updating.
3. Verify API directly:
   - `http://localhost:8777/api/dashboard/state`
4. If needed, reload OBS source and restart the local server.
