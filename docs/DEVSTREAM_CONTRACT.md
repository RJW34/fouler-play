# fouler-play Devstream Contract

fouler-play is a bounded-cycle competitive Pokemon Showdown runner. The devstream should show live battles, current ladder/session state, and proof-backed reports about team quality and failure modes.

## Runtime Boundaries

- Runs on `ubunztu` only.
- Does not autostart from the contract exporter.
- The canonical OBS server is `streaming/serve_obs_page.py` on `127.0.0.1:8777`.
- The health probe is read-only by default and does not start games, restart services, or mutate battle state.
- Existing developer-loop and pipeline services are not treated as the same thing as the devstream runner.

## OBS Surfaces

- `http://127.0.0.1:8777/slot/1`
- `http://127.0.0.1:8777/slot/2`
- `http://127.0.0.1:8777/slot/3`
- `http://127.0.0.1:8777/overlay/hybrid`
- `http://127.0.0.1:8777/dashboard/hybrid`
- `http://127.0.0.1:8777/health`

The next OBS pass should improve scene composition around these endpoints, but this pass only establishes the contract and health truth.

## Truth Files

- `active_battles.json`
- `stream_status.json`
- `daily_stats.json`
- `battle_stats.json`
- `replay_analysis/autoresearch_latest.json`
- `replay_analysis/reports/autoresearch_latest.md`
- `stability_report.json`

`stream_status.json` and report files may be stale while the project is idle. The health probe reports that as `idle` or `degraded`; it does not start battles to refresh them.

## Health Probe

Run:

```bash
cd /home/ryan/projects/fouler-play
python3 scripts/devstream_health.py
```

When the OBS server is running, `/health` returns the same structured payload via `streaming/serve_obs_page.py`.

## Next Work Packets

1. Add a canonical `scripts/devstream_start.sh` wrapper for bounded cycles that starts the OBS server and writes runtime truth.
2. Add drain-first `scripts/devstream_stop.sh` semantics around active battles.
3. Write `devstream/truth/completion.json` at bounded cycle end with battle counts, replay ids, report paths, and validation status.
4. Retire or clearly label legacy 6-slot text-source docs so the browser-source architecture is obvious.
