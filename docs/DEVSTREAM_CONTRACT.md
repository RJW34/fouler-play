# fouler-play Devstream Contract

fouler-play is a bounded-cycle competitive Pokemon Showdown improvement lab. The project started from `foul-play`, but the devstream goal is much sharper: tune it for high-level Gen 9 OU until the bot can reach 1700+ ELO and play credible games against players in that band.

Live battles are not just content. They are the training and evaluation loop: collect decision traces, replay evidence, matchup failures, ladder rating movement, and DEKU-authored improvement notes, then feed those findings back into the bot.

The ladder loop is staged. Fouler must prove the current rating band before it
is allowed to take larger proof windows: small proof windows below 1500, tighter
windows through 1600 and 1700, and then a 30-game 1700 sustain proof assembled
from repeated small runtime chunks that are re-gated between starts.
`scripts/fouler_mission_monitor.py` enforces this as
`fouler-ladder-stage-gate/v1` plus `fouler-ladder-floor-proof/v1`: one rating
spike above 1500 or 1600 is not enough to promote the next stage; the latest
rated games must show five-game consecutive floor proof with a non-losing
floor-window record. Oversized repair/start batches are mission issues and
block automatic ladder starts.

## Runtime Boundaries

- `ubunztu` is the control-plane and development home for status, dry-run, analysis, and HERMES proof work.
- `JIGGLYPUFF` is an optional Windows runtime profile only after a current proof-window runtime lease names Fouler, the machine, account, run count, concurrency, replay behavior, and expiry.
- Uses repo-local virtualenvs so `poke-engine` and Showdown runtime dependencies are present without modifying system Python.
- Does not autostart from the contract exporter.
- The canonical OBS HTTP surface is `streaming/serve_obs_page.py` on `127.0.0.1:8777` of the active runtime host.
- The health probe is read-only by default and does not start games, restart services, or mutate battle state.
- Existing developer-loop and pipeline services are not treated as the same thing as the devstream runner.
- Controller probes that use `scripts/jigglypuff_devstream_control.py status --read-only` must not write local mirror files or remote `devstream\truth\jigglypuff-runtime.json`; scheduled/resident proof production uses the normal status path so the artifact is produced on JIGGLYPUFF with `producer.expectedHostMatched=true`.

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
.venv/bin/python scripts/fouler_mission_monitor.py --run-count 5 --max-cycles 1 --max-concurrent-battles 1 --start-gate-only
.venv/bin/python scripts/fouler_mission_monitor.py --write --repair-runtime --renew-lease --run-count 5 --max-cycles 1 --max-concurrent-battles 1 --start-gate-only
.venv/bin/python scripts/devstream_session.py stop
.venv/bin/python scripts/devstream_session.py stop --execute
.venv/bin/python scripts/devstream_packetize.py
```

`scripts/fouler_mission_monitor.py --start-gate-only` is the HERMES-facing start gate. It allows only the next finite proof window when health is fresh, duplicate runners are absent, account authority agrees with the active runtime lease, no supervisor stop file is present, stop-loss governance is clear, the requested `run_count * max_cycles` fits the ladder stage and floor-proof state, and a runtime lease can authorize the exact machine/account/concurrency/replay scope. The 1700 sustain proof still requires 30 qualifying games, but the monitor caps each live runtime chunk to a small re-gated window so a single 1700 spike cannot authorize a long uninspected skid. If loss streak, low win-rate, runtime rating drawdown, regression below a previously proven 1500/1600/1700 ladder floor, ELO-proof pre-target/sustain drawdown, account-authority mismatch, supervisor stop-file presence, or oversized batch stop-loss trips, the gate also requires the blocking issue to be cleared; stop-loss recovery additionally requires accepted offline-eval resume proof from `eval_results/offline/candidate.json` and `eval_results/offline/compare-frozen-vs-candidate.json`, then fresh active-improvement proof from `devstream/truth/post-packet-eval.json`, before another ladder proof window may open. The post-packet proof must show an implemented packet, a bounded battle after the packet, autoresearch coverage of that battle, `evidenceIntegrity.ok=true`, no runtime/network mutation from the evaluator, and `status=post-packet-eval-improving`. Missing final 1700 sustain proof remains a mission issue, but it does not by itself authorize a larger batch or block a small proof window needed to gather evidence.

The same monitor payload exposes `repairQueue` (`fouler-play-repair-queue/v1`) for DEKU/HERMES. When stop-loss is active, `repairQueue.nextPacketId` is `fouler-stop-loss-recovery` and the packet enumerates the required sequence: freeze ladder starts, analyze the failing rated window, select one constrained work packet, implement one allowed code fix with tests, produce accepted offline-eval proof, produce fresh post-packet improvement proof, then re-run the start gate for one bounded proof window. When stop-loss is clear but final 1700 sustain proof is missing, `repairQueue.nextPacketId` is `fouler-1700-sustain-proof`. Repair packets are operator/work instructions only; they set `runtimeMutationAllowed=false`, `networkSendAllowed=false`, `discordPostAllowed=false`, `teamEditsAllowed=false`, and `streamKeyRequired=false`.

`--repair-runtime` starts proof-only bounded sessions by default. `--auto-improve` is an explicit opt-in and must not be added to HERMES repair/start commands unless a separate offline-eval acceptance and lease policy deliberately allows recursive live improvement.

`scripts/devstream_session.py start --execute` is a lower-level runner behind the gate. It must not be called directly by HERMES start/recovery paths unless the mission monitor has first accepted the requested proof window. `scripts/devstream_session.py supervise` also checks the mission start gate for its full `run-count * max-cycles` proof window before writing a supervisor PID, so a multi-cycle supervisor cannot bypass the staged ladder cap one child batch at a time. Each idle supervisor cycle may refresh proof, but it must recheck the mission gate before auto-improve or the next ladder batch; if stop-loss, drawdown, floor regression, stale truth, or missing offline-eval resume proof closes the gate, the supervisor exits blocked instead of polling or grinding.

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

For controller-side inspection, prefer:

```bash
cd /home/ryan/projects/fouler-play
.venv/bin/python scripts/jigglypuff_devstream_control.py status --read-only
```

This is a no-write probe. It reports JIGGLYPUFF status without creating a controller-produced mirror or refreshing the remote runtime proof file. A proof artifact refreshed by the normal status path must include `proofArtifact.written=true` and `producer.expectedHostMatched=true`.

`scripts/devstream_session.py doctor --require-ready` is also read-only. It must be able to import
`psutil` from the selected Python so PID files can be verified against real process command lines and
start times. If `psutil` is missing, stale `.bot.pid` or `.pids/*.pid` artifacts are treated as
untrusted blockers, not live runtime proof.

The doctor and mission monitor also fail closed when account authorities disagree. `.env` /
`SHOWDOWN_USER_ID`, mission docs, health accountAuthority telemetry, and any runtime lease must name
the same Showdown account before an execute path is considered ready. A dry-run lease authorizes only
its dry-run purpose, and an expired proof window never authorizes execute.

## Next Work Packets

1. Keep live ELO proof files fresh through `scripts/devstream_cycle_report.py --write`, conforming to `devstream/truth/elo-proof.schema.json`, including the bounded pre-1700 approach drawdown, the 1700 sustain window, and per-team coverage fields. `summary.sustainProofComplete` is a mission-ready 1700 sustain claim, not just an evidence-shape flag; use `summary.sustainEvidenceShapeComplete` only to diagnose malformed or missing proof fields.
   The sustain proof must also carry a `sourceCommit` from the checkout that produced the behavior, and the mission monitor rejects source drift when the current checkout is known. The sustain window must contain concrete, unique battle ids and unique Pokemon Showdown replay ids; each replay URL must match its battle id. `unknown`, duplicated, or merely prefix-shaped replay URLs are not proof. Every sustain-window game must also link unique per-game decision-trace evidence; duplicated decision-trace paths or URLs are not proof. Every completed proof game must carry a parseable battle timestamp, and the game list must be chronological before first-target, drawdown, and uninterrupted-floor derivation. The proof target must declare `uninterruptedPostTargetFloorRequired=true`, and any dip below 1700 after the first target hit is a stop-loss breach, not merely an incomplete completion claim. The proof must link the post-window autoresearch JSON, human-readable autoresearch report, and decision-trace review artifact. Replay-analysis timestamps are diagnostic only; they cannot refresh an old sustain proof without a fresh proof/session/latest-battle timestamp. The declared target contract and every summary counter/rating must match values derived from the game list; a stale, hand-edited, reordered, or internally contradictory summary is not readiness evidence. A 1700 rating window without source provenance, replay-analysis, decision-trace evidence, and chronological battle proof is not a closed learn-from-loss loop.
2. Generate a richer completion summary after each bounded session, including battle ids, replay ids, and rating deltas.
3. Wire `scripts/devstream_packetize.py --write` into a human-reviewed DEKU packet flow.
4. Write `devstream/truth/completion.json` at bounded cycle end with battle counts, replay ids, report paths, rating deltas, and validation status.
5. Retire or clearly label legacy 6-slot text-source docs so the browser-source architecture is obvious.
6. Promote `showdown_login_check.py --execute` into the standard DEKU certification step before any ladder batch.
7. Keep the mission monitor's staged ladder gate in front of every repair/start path, so a sub-1500 bot or one-game 1500/1600 spike cannot launch a full 30-game batch and skid before HERMES can inspect the failing window.
8. Treat `fouler-offline-eval-resume-proof-missing` as a start blocker after any stop-loss breach; do not clear it with prose or stale reports.
9. Treat `fouler-active-improvement-proof-missing` as the second stop-loss recovery blocker after offline eval acceptance; do not reopen laddering until `scripts/devstream_post_packet_eval.py --write` produces fresh improving proof for the implemented packet.
10. Consume `repairQueue.nextPacketId` from the monitor payload for DEKU work dispatch. Do not retry ladder starts from a generic blocked status; use the packet's ordered `nextActions` and acceptance gates.
11. Treat `.pids/supervisor.stop` as `fouler-supervisor-stop-file-present`, a hard start blocker and mission issue. Do not remove it from automation just to make health green; resume only through a fresh mission start gate.
