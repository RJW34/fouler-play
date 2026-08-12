# fouler-play Performance Loop

The product goal is a high-level Gen 9 OU bot, not merely a bot that can queue games. The target is sustained 1700+ ELO performance and credible games against humans around that rating.

## Metrics

- Current ladder ELO and peak session ELO.
- Win/loss record per bounded batch.
- Results against rating bands when opponent rating is known.
- Loss reasons grouped by matchup, decision error, team preview issue, endgame conversion, and invalid-choice recovery.
- Decision trace coverage for each completed game.
- Replay links for representative wins and losses.
- Machine-readable ELO proof conforming to `devstream/truth/elo-proof.schema.json`, including sourceCommit provenance for the checkout that produced the behavior, bounded pre-1700 approach drawdown, the 30-game post-1700 sustain window, 10 qualifying games on each fixed team, and unique per-game decision-trace proof. Treat `summary.sustainProofComplete=true` as a full 1700 sustain claim only; `summary.sustainEvidenceShapeComplete` is diagnostic and is not enough for readiness. The monitor derives target, rating, replay, team, analysis, decision-trace, and summary facts from the game list and rejects contradictory or hand-edited summary claims.
- Machine-readable ladder stage proof from `scripts/fouler_mission_monitor.py`: small proof batches below 1500, consecutive floor proof before promoting through 1500/1600, tighter batches through 1700, and the 1700 sustain proof is accumulated as a 30-game evidence window through repeated small runtime chunks that are re-gated between starts.

## DEKU Linkage

DEKU should consume the batch outputs and produce constrained work packets, not broad rewrites. A good packet names:

- evidence from replays or decision traces
- the suspected failure class
- the exact code area allowed to change
- the validation commands
- the next battle-batch proof expected after the patch

## Batch Lifecycle

1. Ask `scripts/fouler_mission_monitor.py --start-gate-only` to accept the next finite proof window.
2. Start a bounded batch only after the start gate accepts the declared run count, max cycles, concurrency, lease, and stop-loss state.
3. Keep OBS surfaces live for battles, stats, and current analysis state.
4. Write runtime truth while battles are active.
5. Drain active battles before stopping.
6. Write a batch completion file with counts, rating movement, replay ids, report paths, and ELO proof.
7. Generate DEKU work packets from the report.

## Launch Gaps

- `scripts/devstream_session.py` remains the low-level start/stop runner; HERMES start/recovery paths must enter through `scripts/fouler_mission_monitor.py --start-gate-only`.
- Drain-first stop exists through `scripts/devstream_session.py stop --execute`; `--force` is only for approved bounded rollback.
- `scripts/devstream_cycle_report.py --write` generates `devstream/truth/latest-elo-proof.json` at batch end. The remaining gap is operational: every bounded supervisor cycle must run that writer after drain, and the generated proof must pass the 1700 sustain contract before any readiness claim.
- The mission monitor now has a staged ladder proof-window gate and a separate runtime start gate. The repo default is one 5-game proof cycle; explicit `--run-count` and `--max-cycles` requests are multiplied and rejected when the total proof window exceeds the current stage max. Stage promotion is based on consecutive floor proof, so one-game 1500/1600 spikes do not unlock the next ladder stage. At 1700, the 30-game sustain proof target remains, but a single 30-game runtime chunk is not authorized; each small chunk must pass the start gate again so stop-loss can interrupt a skid. Missing 1700 sustain proof remains a mission issue, but does not by itself authorize a larger batch or block a small evidence-gathering proof window.
- Mission-monitor repair starts are proof-only unless `--auto-improve` is explicitly supplied. HERMES recovery commands should omit `--auto-improve` until offline eval and lease policy intentionally allow recursive live improvement.
- `.pids/supervisor.stop` is a first-class `fouler-supervisor-stop-file-present` blocker. Mission health, ticketing, and start eligibility must all agree that the lane is blocked until a deliberate resume path removes the stop file and re-runs the mission start gate.
- After any loss streak, low recent win rate, runtime rating drawdown, ELO-proof pre-target/sustain drawdown, or oversized batch, the start gate requires accepted offline-eval resume proof from `eval_results/offline/candidate.json` and `eval_results/offline/compare-frozen-vs-candidate.json`; otherwise `fouler-offline-eval-resume-proof-missing` blocks laddering.
- 2026-06-28 read-only monitor check: do not try another 1700 push from the current proof state. Health truth is stale/idle, the latest rated window shows a 104.61 current drawdown and 242.64 max drawdown from a 1274.39 peak to a 1031.75 trough, the default proof window fits `prove-1500` (`5 <= 10`) but laddering is still paused by drawdown stop-loss and missing offline-eval resume proof, and `latest-elo-proof.json` is stale, account-mismatched, below 30 games, and has zero post-target sustain games.
- Some legacy services/docs still duplicate pipeline intent.
- `scripts/devstream_packetize.py` can draft packets from autoresearch output; the write path still needs human review and orchestration policy.
