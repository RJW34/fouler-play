# fouler-play -- Mission

> **Intent doc: what this project is FOR. Read this first.** Then read the rules
> (`VENTURE_CONSTITUTION.md` on ubunztu, `~/devstream-claude/VENTURE_CONSTITUTION.md`),
> then the build map (`ARCHITECTURE.md` in this repo), then the running-state ledger
> (`~/devstream-claude/UNTETHER_COMPLETION_CAMPAIGN.md` on ubunztu). This file rarely
> changes. It exists to keep any LLM -- Codex, Claude, or otherwise -- from drifting
> off-mission. Where a "current status" claim here disagrees with a live gauge, the live
> gauge wins (constitution R1, R6); where intent disagrees, the lower-numbered source of
> truth wins (constitution s4).

---

## Mission (in human terms)

fouler-play is a Pokemon Showdown (gen9ou) battle agent that plays **any team it is handed**
at a 1700+ ELO level, so that strong players can use it as an **overnight team-testing and
data-gathering resource**: hand it a team, leave it laddering, and come back to ELO results
plus replay and loss data on that specific team. **Generality is the product** -- the value
is that it plays a *given* team well, not that it gets to pick teams it can win with. It is a
fork of pmariglia's `foul-play` (a Monte-Carlo-Tree-Search agent built on the Rust
`poke-engine`), and the upstream engine's real search strength is the foundation everything
else must protect.

## Current benchmark (a pilot -- NOT the ceiling)

Right now the project runs a narrow proving protocol: **three OWNER-LOCKED fat/stall gen9ou
teams** (`gen9/ou/fat-team-1-stall`, `fat-team-2-balance`, `fat-team-3-dondozo`) are driven
toward 1700 ELO via the learn-from-losses loop. This pilot exists to **prove the engine and
the loop** on a fixed, hard, well-understood set of teams.

It is **not** a license to only ever play these three teams, and it is **not** the mission
narrowing to "these teams." The three teams are a *benchmark harness*; the *product* is "any
team." Do not confuse the pilot for the goal, and never optimize toward "make these three
specific teams' number go up" at the expense of general strength.

## What we WANT

- **Strong GENERAL play (1700+ ELO) on ARBITRARY given teams**, grounded in the upstream
  engine's real strength (MCTS over Bayesian-sampled opponent sets -- see `ARCHITECTURE.md`
  section 2).
- **Honest ELO** from the live secure account (`thepeakmons`) -- age-honest gauges, no
  ratcheting, no fake green (constitution R6).
- **The learn-from-losses loop actually CLOSING**: replay analysis -> ranked recurring-loss
  issue -> ONE eval-gated engine improvement -> live divergence check -> keep or revert
  (`ARCHITECTURE.md` section 5).
- **The "any team" product path built out**: pokepaste / export-block intake ->
  `teams/team_converter.py` -> `accept_challenge` bot mode -> a **per-session data and replay
  report** handed back to the player. (`ARCHITECTURE.md` section 6 lists what exists vs. the
  gap: intake and the ad-hoc per-session report are the missing pieces.)
- **Play grounded in the upstream engine** -- every divergence from upstream `foul-play` is
  suspect until it is justified and eval-proven.

## What we DON'T want (anti-goals)

- **METRIC-GAMING.** Dropping, swapping, or re-weighting teams to farm ELO is **BANNED** by
  the owner. The mission is to play a *given* team well, not to pick easy teams. (Constitution
  section 6 anti-patterns; R10 -- report proven-vs-assumed honestly, never optimize the number
  instead of the mission.)
- **UNAUDITED engine changes.** Every decision-engine change must **beat the frozen baseline
  in the offline eval gate**, with upstream `foul-play` as the known-good anchor (constitution
  R5). When the offline gate cannot discriminate (e.g. a weak practice opponent), fall back to
  the live **played-vs-policy divergence gauge** or a monitored, reversible A/B -- never flip
  an engine change on a hunch.
- **UNBOUNDED ACCRETION.** The fork already ballooned upstream's compact (~150-line) decision
  core into several thousand lines of mostly-off / dead layers. **Prefer deleting to adding.**
  Most of the wrapping (penalty pipeline, loop-breaker, matchup-memory, strategic/archetype
  cluster) is currently OFF or dormant -- see the layer-status table in `ARCHITECTURE.md`
  section 4. Do not add another layer to fix what a deletion would fix. (Constitution R9.)
- **Resurfacing the retired / leaked account `LEBOTJAMESXD00N`** (or the older `npctypebeat`).
  The account is `thepeakmons`, and only `thepeakmons`.
- **Losing on the clock** (inactivity forfeits). The CRITICAL-CLOCK fast path and the
  side-clock-derived per-turn budget exist for exactly this (`ARCHITECTURE.md` section 2,
  steps 1-2); do not regress them.
- **"Deduping" the one ladder client by process count.** It is a venv-python parent + a
  system-python child + MCTS workers = **ONE** client. Killing on a raw process count kills
  the live client. Trust the per-account singleton lock in `run.py`. (Constitution R3.)

## Definition of Done

fouler-play **accepts an arbitrary team and sustains 1700+ ELO on the live ladder**, producing
a **per-session data / replay report** for the player; the **learn-from-losses loop is closed**,
and **every engine change is eval-gated** (constitution R5).

**Current honest state:** roughly the **1050-1200 ELO band** on the pilot teams -- **NOT done**,
and not yet climbing to 1700. The **biggest known lever is engine quality**, not more heuristic
layers. See `ARCHITECTURE.md` and the divergence findings: the decision loop-breaker was
trace-proven to cause 94% of played-vs-policy inversions (249/265 override turns) by demoting
correct repeated stall play, and is now OFF (`FOULER_LOOP_BREAK=0`); a separate re-baseline-to-
upstream effort (`rebaseline/upstream-anchor-20260704`) is testing whether anchoring back to
upstream removes the harmful layers by construction. Point engine work at those findings, not
at inventing new layers.

## Guardrails (owner-locked; cross-linked, not duplicated)

- **Upstream `foul-play` is the ANCHOR.** Justify every divergence against it (constitution R5,
  R9). Exact fork-vs-upstream counts and the ON/OFF/DORMANT layer table live in `ARCHITECTURE.md`.
- **Eval-gate before ANY engine change** (constitution R5). No unaudited autonomous change to a
  decision engine, ever.
- **OWNER-LOCKED, never change:** the 3 pilot teams; `max-concurrent-battles = 3`; MCTS
  `search-parallelism = 2`; the single-client posture (constitution R3); account = `thepeakmons`
  only. These are fixed by the owner and by the runtime lease
  (`devstream/truth/runtime-lease.json`); do not "tune" them to chase ELO.
- **Full rules live elsewhere -- read them, do not duplicate them here:** the standing rules
  R1-R10 and the anti-pattern catalog are in `VENTURE_CONSTITUTION.md` on ubunztu
  (`~/devstream-claude/VENTURE_CONSTITUTION.md`, read-only via `ssh ubunztu cat ...`); the
  verified build map is `ARCHITECTURE.md` in this repo; the append-only running state is
  `~/devstream-claude/UNTETHER_COMPLETION_CAMPAIGN.md` on ubunztu.
