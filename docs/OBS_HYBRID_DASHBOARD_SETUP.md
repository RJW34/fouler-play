# OBS Hybrid Dashboard Setup

## 1) Start the local server

For spectator-mode battle slots, use `serve_obs_page` (it updates OBS Browser
Source URLs via OBS WebSocket). `stream_server` can serve dashboard pages, but
does not drive battle-slot URL switching.

```bash
py -m streaming.serve_obs_page
```

Default port is `8777` unless `OBS_SERVER_PORT` is set.
`start_one_touch.bat` now auto-starts this helper server by default.

## 1.5) Install the Hybrid Scene Collection (local OBS)

Build a new scene collection derived from your existing `fouler_play_scenes.json`:

```bash
py -3 scripts/build_obs_hybrid_scene_collection.py
```

This writes:

- `%APPDATA%\obs-studio\basic\scenes\fouler_play_hybrid_scenes.json`
- `streaming/fouler_play_hybrid_scenes.json` (repo copy)

In OBS: `Scene Collection` -> select `Fouler Play Hybrid Battles`.

## 2) Browser dashboard (normal browser)

Open:

```text
http://localhost:8777/dashboard/hybrid
```

This is the full decision dashboard with timeline, latest decision details, and learning signals.

## 3) OBS Browser Source URL

Use this for the OBS-optimized overlay:

```text
http://localhost:8777/overlay/hybrid
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
   - `http://localhost:8777/overlay/hybrid`
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
