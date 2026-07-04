# Re-baseline Phase A -- Offline Eval Verdict

Branch: `rebaseline/upstream-anchor-20260704`  (head `b79d136ce`)
Engine: upstream foul-play decision pipeline + poke-engine **0.0.47** (multithreaded MCTS)
Date: 2026-07-04
Scope: **non-destructive**. Branch-only. No live-lane swap. Phase B (the live client swap) stays owner-gated.

## What Phase A was

Re-baseline the fouler decision engine onto upstream `foul-play` + `poke-engine 0.0.47`,
then run the SAME offline acceptance gate the main tree uses to learn whether the
upstream-rebaselined engine matches/beats the frozen (old-engine) baseline before any
live swap. The worktree was built to Phase A (upstream merge, 0.0.47 bump, data refresh,
thin adapter, eval-login fix); this run produced the missing verdict.

Phase A commits (this branch, oldest first):
- `abb428f2d` engine: reset fp/search decision pipeline to upstream foul-play state
- `5eadeea28` engine: battle_modifier package -> upstream module + ported countdown clock fix
- `70010ce74` engine: apply upstream deltas onto fp/battle.py and fp/helpers.py
- `d57a195a7` engine: drop retired fork decision modules (dead in live path or measured harmful)
- `c89c3a562` adapter: thin fork-owned seam between upstream engine and the ops harness
- `ae1f8a46c` data: refresh Showdown data the upstream way + keep fork cache fixes
- `aae8abb8f` deps: poke-engine 0.0.46 -> 0.0.47 (multithreaded MCTS) + requests 2.33.0
- `b0affaee1` merge upstream/main (ancestry record for the engine re-baseline)
- `b79d136ce` fix(eval): no-security login survives a still-loading managed showdown server

## Import / readiness gate

`.venv-rebase` (the isolated rebaseline venv; live `.venv` untouched):
- poke-engine dist version **0.0.47** confirmed.
- Fouler runtime imports OK: aiohttp, requests, dotenv, dateutil, psutil, poke_engine.
- `import fp.search.main; import run` -> OK.
- Harness end-to-end proven in the worktree by the prior 3-battle smoke (fouler 3/3 at 50ms).

## Multithreaded MCTS confirmation (poke-engine 0.0.47 + --search-threads)

Engine wiring: `config.py` exposes `--search-threads` / `SEARCH_THREADS` (default 1) ->
`FoulPlayConfig.search_threads` -> `fp/search/main.py`:
`monte_carlo_tree_search(state, search_time_ms, threads=threads)`.

Direct micro-benchmark on a real gen9ou state (`.venv-rebase`, 1500 ms search window):

| threads | MCTS total_visits | vs 1-thread |
|--------:|------------------:|------------:|
| 1       | 53,000            | 1.00x       |
| 2       | 97,000            | 1.83x       |
| 4       | 467,000           | 8.81x       |

=> Multithreading **engages**: more threads => materially more MCTS iterations in the same
wall-clock window. (Ran fine even while the box sat at ~96% CPU from other lanes.)

## The gate run (apples-to-apples with the frozen baseline)

Frozen baseline (`eval_results/offline/frozen.json`, the OLD engine):
- team `gen9/ou/fat-team-1-stall`, baseline `simple`, n=**200**, WR **0.97** (194/200),
  Wilson LCB **0.9361**, search_time_ms **50**, extra_env `FOULER_FORCE_NO_SETSAMPLE=1`.

Candidate (this branch, `rebaseline-candidate`):
- team `gen9/ou/fat-team-1-stall`, baseline `simple`, search_time_ms **50**, threads **1**
  (isolates decision logic from raw compute; frozen was single-threaded 0.0.46 too),
  serial (concurrency 1), Normal priority.
- n=**4** (modest count). Why small: JIGGLYPUFF was concurrently running the LIVE fouler
  ladder client PLUS other devstream lanes (OBS ~120% of a core, a cobblemon JVM ~100%),
  and the `fat-team-1-stall` matchup is a STALL MIRROR (the baseline plays the same stall
  team), so each battle runs ~45 turns and, under that shared load, ~20 min. A 200-battle
  re-run was not feasible without starving the live lane; the gate cannot discriminate at
  this ceiling anyway (see below), so a small honest sample + corroboration was chosen over
  a live-lane-disrupting long run.
- `SEARCH_PARALLELISM=1` (frozen used the default 4). This is a THROUGHPUT accommodation:
  it cuts per-turn opponent-sample search breadth so battles finish ~3x faster on the loaded
  box. Against the weak SimpleHeuristics baseline it does not change the crush-the-baseline
  win-rate (only how many opponent-team samples are searched per move); documented here for
  full transparency.
- extra_env `{}` -- NOTE: the frozen run carried `FOULER_FORCE_NO_SETSAMPLE=1`, a handicap
  flag from the earlier set-sampling A/B. In the rebaselined tree that flag exists ONLY in
  the eval harness/probe scripts, NOT in `fp/` engine code (the fork's `_sample_pokemon`
  set-sampling was dropped in the upstream rebase), so it is a no-op on the candidate. The
  candidate therefore plays at natural strength; the frozen number carried a now-defunct
  handicap. This slightly favors the candidate but does not change the qualitative verdict.

Broader context (NOT pooled into the gate result): the prior in-worktree smoke went
fouler **3/3** at 50 ms on the same team/baseline. Adding this gate's 3/4, the rebaselined
engine is 6/7 (~86%) across all in-worktree battles -- mostly winning, but with one loss,
so it is NOT a clean 100% crush. See the honest interpretation below.

### Result

| metric              | frozen (reference)  | candidate (rebaselined) |
|---------------------|--------------------:|------------------------:|
| team                | gen9/ou/fat-team-1-stall | same               |
| baseline            | simple (SimpleHeuristics) | same              |
| search_time_ms      | 50                  | 50                      |
| battles (n)         | **200**             | **4**                   |
| fouler wins         | 194                 | 3                       |
| fouler losses       | 6                   | 1                       |
| win-rate            | **0.97**            | **0.75**                |
| Wilson LCB (95%)    | **0.9361**          | **0.3006**              |

`offline_eval.py --compare frozen rebaseline-candidate`:
- delta_win_rate = **-0.22**
- two-proportion z = **-2.393**, p = **0.0167**
- candidate LCB > 0.5 = **false**
- statistically_significant_improvement = **false**
- **ACCEPT = false  ->  REJECT**

The gate REJECTS: the candidate did not beat the frozen 0.97, and the z-test even flags a
nominal *regression* (p=0.017). **But read the interpretation before trusting that number.**

## Honest interpretation -- the REJECT is UNDERPOWERED, not a proven regression

**Bottom line: INCONCLUSIVE.** The n=4 REJECT does NOT establish that the rebaselined
engine is worse. Two things are true at once and both matter:

1. **The candidate lost 1 of 4 -- a real data point, honestly reported.** It is not a clean
   crush. If this loss rate were real it could mean the upstream engine dropped a fork
   behavior that helped in this stall matchup (the rebase deleted "retired fork decision
   modules", commit `d57a195a`). That possibility cannot be dismissed and is flagged for
   Phase B.

2. **n=4 cannot tell that apart from noise.** One loss in four is fully consistent with a
   true 0.97 win-rate: P(>=1 loss in 4 | p=0.97) ~ 11%. The "significant" z-test (p=0.017)
   is an ARTIFACT of comparing a 4-battle candidate against a 200-battle frozen -- the test
   is powered by frozen's huge n, not by evidence about the candidate. A single battle
   flipping (4/4) would have made WR=1.0 and flipped the verdict toward ACCEPT. The Wilson
   LCB of **0.30** says exactly this: the candidate's true win-rate is anywhere from ~0.30
   to ~1.0 on this evidence.

**Could the gate discriminate here?** No -- not reliably, for two compounding reasons:
(a) the SimpleHeuristics baseline is weak, so both engines sit near a crush ceiling with
little discriminating room (the same low-discrimination finding as the loop-breaker A/B);
and (b) the candidate n was forced tiny (4) by the loaded box, so the result is
statistically underpowered. The gate produced a verdict, but it is not trustworthy signal
about the re-baseline's real quality.

**What this run DID prove:** the rebaselined engine (upstream foul-play + poke-engine 0.0.47)
loads, imports, and plays the offline gate end-to-end in the isolated worktree, with the
new multithreaded MCTS confirmed engaging. What it did NOT prove: whether the re-baseline
matches, beats, or regresses the frozen engine's skill. That requires a properly powered,
discriminating gate (see Phase B).

## Phase B (live swap) -- owner-gated, NOT done here

Phase B would: (1) build `.venv`-equivalent runtime deps for 0.0.47 on the LIVE tree,
(2) swap the live ladder client (`thepeakmons`) from the frozen fork engine to the
rebaselined engine, (3) pick a `--search-threads` value for the live box (the multithread
win is real -- see table), (4) watch live ELO for regression with a rollback path. None of
that is done here; the live `.venv` client and `D:\Projects\fouler-play` were left untouched.

Phase B pre-reqs (this run does NOT clear the gate for a swap):
- **A properly powered gate.** Re-run this same offline gate at n ~ 100-200 (parallelism
  restored to 4) on a QUIET box -- ideally when the streaming/cobblemon lanes are idle so
  the eval is not starved and the live ladder is not disturbed. n=4 here was forced by box
  load and is not decision-grade.
- **A discriminating gate**, since even at full n the SimpleHeuristics baseline sits near a
  crush ceiling: prefer self-play vs the frozen fork engine head-to-head, or a stronger
  baseline (MaxDamage / short live-ladder A/B), which can actually separate the two engines.
- **Resolve the one loss**: at full n, confirm whether the ~stall-mirror loss rate is real
  (a dropped fork behavior) or variance before trusting the swap.

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>
