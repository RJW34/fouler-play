# Smoothness Readiness Gates

Updated: 2026-07-06

This file defines what "live-ready" means for the fouler-play lane beyond "the
bot is queued on Pokemon Showdown." The bot must make timely, coherent battle
decisions and must improve through a history-aware promotion loop instead of
cycling through half-baked engine edits that help one replay and damage overall
ladder performance.

## Mission Scope

- Run an autonomous Pokemon Showdown battle lab lane that can ladder without
  manual babysitting.
- Build on accepted engine improvements. Do not repeatedly make, undo, or
  reimplement the same idea without checking the engine history.
- Use losses, replays, offline evaluation, regret suites, and ladder ELO as one
  closed improvement loop.
- Replace noisy Discord reporting with one actionable digest per cycle.

## Smoothness Definition

A viewer should see the bot choose moves and switches before the clock becomes
the main opponent. A developer should see one current engine hypothesis, one
promotion gate, and one result, not a pile of contradictory reports.

The lane is not smooth if any of these are true:

- The ladder client times out, misses turns, or stalls because analysis is too
  slow.
- The engine changes after every loss without historical regression checks.
- A replay reviewer writes analysis but no future decision path reads it.
- ELO drops in multi-hour skids without triggering a stop-loss, rollback, or
  quarantine gate.
- Discord reports many observations but no single next action.
- The promotion gate accepts an improvement that only fixes one narrow replay
  while losing against the historical suite.

## Engine History Gate

Every engine change must attach to an accepted packet or be rejected.

Required record for each proposed playmaking change:

- The exact replay, turn, and state that motivated the change.
- The previous engine decision and why it was wrong.
- The proposed engine decision and why it is better.
- The known risk: what common matchup, archetype, or endgame pattern could get
  worse.
- The offline evaluation result against the historical regression suite.
- The live or shadow ladder result if promoted.
- The rollback path and the previous accepted engine fingerprint.

The autoresearch loop may propose candidates. It may not promote candidates
unless the candidate clears the promotion gate.

## Decision Cadence Gate

The battle engine must remain fast enough for live laddering.

Required behavior:

- Team preview decisions are deterministic or cached once the matchup is known.
- Forced switches use a bounded search or heuristic fallback.
- Endgames use the dedicated endgame solver when the state qualifies.
- MCTS, sampling, and matchup research have strict time budgets.
- If a search budget expires, the engine emits the best safe fallback and logs
  the budget miss as a candidate improvement.
- The websocket client lifecycle must reconnect without duplicate battle writers.

## Evaluation Gate

Promotion requires evidence from more than one source:

- Unit tests for the touched mechanic.
- Offline replay/regret suite for historical failures.
- Self-play or deterministic simulation where available.
- Live ELO trend after promotion, with sample size and confidence caveats.
- Stop-loss if the promoted engine underperforms the baseline.

The target is not "never lose." The target is that the bot does not forget prior
lessons while attempting to fix a new one.

## Reporting Gate

Discord output should be one digest per cycle:

- Current ELO and recent win rate.
- One accepted packet, blocked packet, or rollback.
- One specific next action.
- One proof link or artifact path.
- No multi-message dumps unless the operator explicitly asks for details.

## Proof Commands

Repository checks on the active JIGGLYPUFF repo:

```cmd
pytest tests\test_fouler_engine_promotion_gate.py tests\test_fouler_cycle_digest.py -q
python scripts\fouler_engine_promotion_gate.py
python scripts\fouler_cycle_digest.py --write
```

Runtime checks on JIGGLYPUFF:

```cmd
type devstream\truth\health.json
type devstream\truth\fouler-cycle-digest.json
type devstream\truth\runtime-lease.json
```

Pass/fail is based on ladder state, replay evidence, accepted engine history, and
future-play effect. A replay analysis file that nobody reads is not proof of
learning.
