# fouler-play Devstream Contract

fouler-play is a bounded-cycle competitive Pokemon Showdown improvement lab. The project started from `foul-play`, but the devstream goal is much sharper: tune it for high-level Gen 9 OU until the bot can reach 1800+ ELO and play credible games against players in that band.

Live battles are not just content. They are the training and evaluation loop: collect decision traces, replay evidence, matchup failures, ladder rating movement, and DEKU-authored improvement notes, then feed those findings back into the bot.

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

## Improvement Loop

The intended loop is:

1. Run a bounded battle batch.
2. Capture battle stats, replays, and decision traces.
3. Have DEKU summarize losses, matchup failures, incorrect choices, and team/archetype drift.
4. Convert the report into constrained work packets.
5. Patch evaluation, prediction, team intent, or reporting logic.
6. Re-run regression tests and another bounded battle batch.

The stream should make that loop visible, not merely show random ladder games.

## Health Probe

Run:

```bash
cd /home/ryan/projects/fouler-play
python3 scripts/devstream_health.py
```

When the OBS server is running, `/health` returns the same structured payload via `streaming/serve_obs_page.py`.

## Next Work Packets

1. Define the 1800+ ELO measurement contract and rating-window proof files.
2. Add a canonical `scripts/devstream_start.sh` wrapper for bounded cycles that starts the OBS server and writes runtime truth.
3. Add drain-first `scripts/devstream_stop.sh` semantics around active battles.
4. Write `devstream/truth/completion.json` at bounded cycle end with battle counts, replay ids, report paths, rating deltas, and validation status.
5. Retire or clearly label legacy 6-slot text-source docs so the browser-source architecture is obvious.
