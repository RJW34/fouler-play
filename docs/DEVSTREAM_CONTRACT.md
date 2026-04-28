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
- `devstream/truth/elo-proof.schema.json`
- `devstream/truth/elo-proof.example.json`

`stream_status.json` and report files may be stale while the project is idle. The health probe reports that as `idle` or `degraded`; it does not start battles to refresh them.

## Bounded Session Commands

These commands are intentionally safe by default. They describe or verify a devstream run without queuing battles or stopping services unless a reviewed execute path is added later.

```bash
cd /home/ryan/projects/fouler-play
python3 scripts/devstream_session.py doctor
python3 scripts/devstream_session.py start --run-count 25 --max-concurrent-battles 2
python3 scripts/devstream_session.py stop
python3 scripts/devstream_packetize.py
```

`scripts/devstream_session.py start` and `stop` currently emit dry-run plans. Their `--execute` path is deliberately blocked until the wrapper can drain active battles, write completion truth, and prove rating/battle outcomes.

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

1. Generate live ELO proof files that conform to `devstream/truth/elo-proof.schema.json`.
2. Add the reviewed execute path for bounded sessions after drain-first stop semantics are in place.
3. Wire `scripts/devstream_packetize.py --write` into a human-reviewed DEKU packet flow.
4. Write `devstream/truth/completion.json` at bounded cycle end with battle counts, replay ids, report paths, rating deltas, and validation status.
5. Retire or clearly label legacy 6-slot text-source docs so the browser-source architecture is obvious.
