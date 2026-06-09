# fouler-play Devstream Contract

fouler-play is a bounded-cycle competitive Pokemon Showdown improvement lab. The project started from `foul-play`, but the devstream goal is much sharper: tune it for high-level Gen 9 OU until the bot can reach 1800+ ELO and play credible games against players in that band.

Live battles are not just content. They are the training and evaluation loop: collect decision traces, replay evidence, matchup failures, ladder rating movement, and DEKU-authored improvement notes, then feed those findings back into the bot.

## Runtime Boundaries

- Runs on `ubunztu` only.
- Uses the repo-local `.venv` on ubunztu so `poke-engine` and the Showdown runtime dependencies are present without modifying system Python.
- Does not autostart from the contract exporter.
- The canonical OBS server is `streaming/serve_obs_page.py` on `127.0.0.1:8777`.
- The health probe is read-only by default and does not start games, restart services, or mutate battle state.
- Existing developer-loop and pipeline services are not treated as the same thing as the devstream runner.

## OBS Surfaces

- `http://127.0.0.1:8777/slot/1`
- `http://127.0.0.1:8777/slot/2`
- `http://127.0.0.1:8777/slot/3`
- `http://127.0.0.1:8777/overlay?mode=bottom&hide_recent=1`
- `http://127.0.0.1:8777/health`

The `/dashboard/hybrid` and `/overlay/hybrid` endpoints are operator-only decision review surfaces. Public OBS scenes must use slot or battle-lab overlay endpoints only, with health, model decisions, and proof details kept in reports rather than visible Twitch panels.

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

These commands are intentionally safe by default. They describe or verify a devstream run without queuing battles or stopping services unless `--execute` is present.

```bash
cd /home/ryan/projects/fouler-play
.venv/bin/python scripts/devstream_session.py doctor
.venv/bin/python scripts/showdown_login_check.py
.venv/bin/python scripts/showdown_login_check.py --execute
.venv/bin/python scripts/devstream_session.py start --run-count 25 --max-concurrent-battles 2
.venv/bin/python scripts/devstream_session.py start --run-count 25 --max-concurrent-battles 2 --execute
.venv/bin/python scripts/devstream_session.py stop
.venv/bin/python scripts/devstream_session.py stop --execute
.venv/bin/python scripts/devstream_packetize.py
```

`scripts/devstream_session.py start --execute` is the reviewed devstream runner. It loads `.env`/`.env.deku`, tightens those files to mode `600` on Linux, starts the OBS HTTP surface, then starts a bounded `run.py` batch with the required Showdown arguments.

`scripts/showdown_login_check.py --execute` is the credential proof gate. It logs into Pokemon Showdown, does not queue a battle, does not chat, and never prints the password. A bounded ladder cycle should not start until this probe passes.

`scripts/devstream_session.py stop --execute` is drain-first. It writes the drain request, waits for `active_battles.json` to clear, and then terminates the devstream-owned PIDs. Use `--force` only when forfeiting active battles is acceptable.

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
.venv/bin/python scripts/devstream_health.py
```

When the OBS server is running, `/health` returns the same structured payload via `streaming/serve_obs_page.py`.

`scripts/devstream_session.py doctor --require-ready` is also read-only. It must be able to import
`psutil` from the selected Python so PID files can be verified against real process command lines and
start times. If `psutil` is missing, stale `.bot.pid` or `.pids/*.pid` artifacts are treated as
untrusted blockers, not live runtime proof.

The doctor also fails closed when account authorities disagree. `.env` / `SHOWDOWN_USER_ID`, mission
docs, and any runtime lease must name the same Showdown account before an execute path is considered
ready. A dry-run lease authorizes only its dry-run purpose, and an expired proof window never authorizes
execute.

## Next Work Packets

1. Generate live ELO proof files that conform to `devstream/truth/elo-proof.schema.json`.
2. Generate a richer completion summary after each bounded session, including battle ids, replay ids, and rating deltas.
3. Wire `scripts/devstream_packetize.py --write` into a human-reviewed DEKU packet flow.
4. Write `devstream/truth/completion.json` at bounded cycle end with battle counts, replay ids, report paths, rating deltas, and validation status.
5. Retire or clearly label legacy 6-slot text-source docs so the browser-source architecture is obvious.
6. Promote `showdown_login_check.py --execute` into the standard DEKU certification step before any ladder batch.
