# fouler-play Self-Sufficient Livestream TODO - 2026-06-29

Status: NO-GO.

This repo owns the competitive Showdown lane. The livestream claim is not "the bot battled"; it is "the bot can improve and sustain credible Gen 9 OU ladder performance without unmanaged ELO crashes."

## Current Snapshot - 2026-06-30

Local launch safety is stricter than the old one-touch model: the boot watchdog and battle-supervisor path route through bounded run counts, max cycles, runtime lease validation, and `scripts/fouler_mission_monitor.py --start-gate-only`. The current start gate correctly blocks ladder starts under stop-loss instead of grinding into further ELO skids.

Stop-loss recovery now has two machine gates: accepted offline eval result proof and fresh active improvement proof from `devstream/truth/post-packet-eval.json`. Do not treat accepted candidate/compare artifacts alone as recovery after a skid; the packet must be implemented, followed by a bounded post-packet battle, consumed by autoresearch, and classified as `post-packet-eval-improving`.

The mission monitor now also emits a read-only `repairQueue` using `schemaVersion=fouler-play-repair-queue/v1`. Under rating drawdown, low win rate, floor regression, rating-truth gaps, or ELO-proof stop-loss, the next packet is `fouler-stop-loss-recovery`; under a clean gate with only missing 1700 sustain proof, the next packet is `fouler-1700-sustain-proof`. These packets are DEKU work instructions only: `runtimeMutationAllowed=false`, `networkSendAllowed=false`, `discordPostAllowed=false`, `teamEditsAllowed=false`, and `streamKeyRequired=false`.

The lane remains NO-GO because the mission proof is not produced: the current monitor reports missing accepted offline-eval resume proof, stale/failing ELO sustain proof, insufficient 1700-floor evidence, and stop-loss blockers. Do not treat a future process restart, active Showdown tab, or short battle batch as readiness unless the start gate returns `allow-next-proof-window` and the sustain proof contract passes.

## Static Process Deployment Alignment

Canonical static-process deployment docs live in `C:\Users\mtoli\Documents\Code\deku-hermes-live-build-orchestrator\docs\STATIC_PROCESS_DEPLOYMENT_MASTER_PLAN_20260630.md` and `C:\Users\mtoli\Documents\Code\deku-hermes-live-build-orchestrator\data\static-process-conversion\accepted-truth-latest.json`.

fouler-play's target role in that plan is a static competitive runtime and improvement loop: start gates, stop-loss classification, offline eval acceptance, post-packet proof, chronological ELO proof, and 1700 sustain windows should run from deterministic code and typed artifacts. Routine ladder decisions should not depend on an LLM heartbeat. Any advisory LLM rerank should be disabled for proof runs or restricted to explicitly bounded exception cases after the static scorer has produced a legal choice set and a close-score ambiguity. Source improvement work should become a static issue-to-work-packet flow first, with LLM or human diffs only after the packet is accepted and bounded.

## HARD BLOCKERS

- `start-gate-blocked`: `scripts/fouler_mission_monitor.py --start-gate-only` must report `startGate.ready=true` and `decision=allow-next-proof-window`.
- `stop-loss-active`: Clear rating-truth and drawdown stop-loss through accepted offline eval resume proof plus fresh active improvement proof, not prose.
- `active-improvement-proof-missing`: `devstream/truth/post-packet-eval.json` must prove an implemented packet, post-packet battle, autoresearch coverage, `evidenceIntegrity.ok=true`, and `status=post-packet-eval-improving`.
- `latest-elo-proof-stale-or-invalid`: Produce a fresh `devstream/truth/latest-elo-proof.json` from `scripts/devstream_cycle_report.py --write`.
- `uninterrupted-1700-floor`: The proof target must declare `uninterruptedPostTargetFloorRequired=true`, and no post-target game may dip below 1700.

## RELIABILITY BLOCKERS

- `rating-truth-incomplete`: Maintain at least 20 recent rated decisive battles with zero missing rating rows and coverage 1.0.
- `single-runtime-owner`: Prove exactly one ladder runner, one Showdown account, and one active runtime lease. Duplicate runners or stale PIDs block starts.
- `offline-eval-resume`: After loss streak, low win rate, drawdown, floor regression, or target-floor breach, require accepted `eval_results/offline/candidate.json` and `eval_results/offline/compare-frozen-vs-candidate.json`.
- `post-packet-improvement-resume`: After accepted offline eval, require `scripts/devstream_post_packet_eval.py --write` to produce a fresh improving proof before another ladder proof window opens.
- `bounded-proof-windows`: Keep staged ladder windows in front of every start path. A 1500/1600/1700 spike cannot authorize a long unattended grind.
- `closed-learning-loop`: Every sustain-window game must have replay proof, battle ID, decision trace, and post-window autoresearch/report proof.
- `chronological-proof-window`: Every completed proof game must include a parseable battle timestamp, and `latest-elo-proof.json` must be ordered chronologically before first-target, drawdown, and uninterrupted-floor derivation.
- `repair-queue-consumed`: DEKU should consume `repairQueue.nextPacketId` from `scripts/fouler_mission_monitor.py` instead of retrying ladder starts from prose. A blocked `fouler-stop-loss-recovery` packet requires the offline-eval and post-packet proof sequence before the start gate can reopen.

## 1700 Sustain Proof Requirements

`latest-elo-proof.json` must prove:

- `schemaVersion=fouler-play-elo-proof/v1`.
- `sourceCommit` from the behavior-producing checkout.
- `target.ratingFloor >= 1700`.
- `target.sustainMinimumGames >= 30`.
- `target.sustainMinimumGamesPerTeam >= 10`.
- `target.maximumSustainDrawdown <= 75`.
- `target.maximumPreTargetDrawdown <= 75`.
- `target.minimumSustainWinRate >= 0.5`.
- `target.noCherryPicking=true`.
- `target.uninterruptedPostTargetFloorRequired=true`.
- At least 30 post-target rated games at or above 1700.
- Zero games below 1700 after first reaching target.
- Final rating and peak rating at least 1700.
- At least 10 sustain-window games for each fixed team.
- Unique concrete Showdown battle IDs and replay IDs, with replay URL matching battle ID.
- Unique per-game decision trace evidence.
- Parseable per-game battle timestamps, zero out-of-order completed games, and `summary.chronologicalBattleOrderComplete=true`.
- Fresh analysis artifacts: autoresearch JSON, autoresearch report, and decision trace review.

## Verification Commands

Read-only/local:

```powershell
cd C:\Users\mtoli\Documents\Code\fouler-play
py -3 scripts\fouler_mission_monitor.py --start-gate-only
py -3 -m pytest -q -p no:cacheprovider tests\test_fouler_mission_monitor.py tests\test_devstream_cycle_report.py
```

Do not edit `run.py`, `config.py`, `.env`, or `teams/**` as part of this TODO without a separate approved competitive-bot repair slice.
