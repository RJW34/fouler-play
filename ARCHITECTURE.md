# Fouler Play — Architecture (the truthful current engine map)

> **This is the front door.** It documents what the code ACTUALLY does today, verified
> against the source on `fix/clock-countdown-parse-79`. Where an older root `.md`
> disagrees with this file, this file wins. Historical snapshots have been moved to
> `docs/archive/` (see `docs/archive/README.md`).
>
> Last verified: 2026-07-04, against `fp/search/main.py` (7,344 lines),
> `fp/run_battle.py` (3,518 lines), `run.py` (1,015 lines).

Every claim below cites a call site so it can be re-checked. If you change the engine,
update this file in the same commit.

---

## 1. What this repo is (and its provenance)

Fouler Play is a **fork of [pmariglia/foul-play](https://github.com/pmariglia/foul-play)**,
a Monte-Carlo-Tree-Search (MCTS) battle agent for Pokemon Showdown built on the Rust
`poke-engine`. The fork is pinned to upstream base commit **`55fa9b477` ("Add a team-list
argument", 2026-01-25)** and has since diverged hard:

| | upstream `foul-play` (base 55fa9b4) | this fork (`fix/clock-countdown-parse-79`) |
|---|---|---|
| `fp/search/main.py` | **123 lines** | **7,344 lines** |
| `run.py` | 101 lines | 1,015 lines |
| commits ahead of base | — | **387** |
| files changed vs base | — | **992** |
| origin remote | `pmariglia/foul-play` (`upstream`) | `RJW34/fouler-play` (`origin`) |

Upstream's decision core is a ~120-line function that runs MCTS over sampled opponent
sets and picks the argmax. **The fork kept that MCTS core and wrapped it in a large amount
of accreted machinery** — a heuristic penalty pipeline, a loop-breaker, matchup-memory
bias, a strategic/archetype cluster, clock-safety fast paths, plus an entire ops harness
(worker pool, keepalive/lease, offline-eval gate, self-improvement loop). **Most of the
wrapping is currently gated OFF or dormant** (see the layer table in §4). The bot that runs
on the ladder today is, by deliberate configuration, close to *upstream MCTS + clock safety
+ hard-legality safety*.

Product framing (mission docs, `docs/PROJECT_MISSION.md`): ladder a top player's fat/stall
gen9ou teams and learn from loss replays. Runtime is bounded by proof windows + a runtime
lease; recursive self-improvement runs through an offline-eval gate before anything is
trusted live.

---

## 2. The real decision pipeline (as it executes)

The single decision chokepoint is **`find_best_move(battle)`** in
`fp/search/main.py:7496`, called once per turn from `fp/run_battle.py:1800`
(`loop.run_in_executor(None, find_best_move, battle_copy)`, guarded by an outer
inactivity timeout). It returns `(move_string, trace_dict)`. The stages run **in this
order**, each short-circuiting when it produces an answer:

1. **Decision-budget computation** (`:7511`) — derive a per-turn wall-clock deadline from
   the remaining SIDE clock (`battle.time_remaining`), not a static cap. Every stage below
   must finish inside this deadline.

2. **CRITICAL-CLOCK fast path** (`:7551`) — if `time_remaining <=
   CRITICAL_CLOCK_FAST_PATH_SECONDS`, skip ALL sampling/MCTS and return an instant
   heuristic move (`_get_fallback_move`). This is the inactivity-forfeit safety net; a pure
   heuristic move cannot be CPU-starved into a clock overrun. `decision_mode =
   "critical_clock_fast_path"`.

3. **Endgame solver** (`:7801`) — `is_endgame(...)` + `solve_endgame(...)`. If the position
   is a small, deterministically-solvable endgame (and the clock can afford it), return the
   solved best move. Skipped when `< 2s` budget remains. `decision_mode = "endgame"`.

4. **Forced-line short-circuit** (`:7833`) — `detect_forced_line(battle)`. If a forced line
   is found with **confidence >= 0.90** (obvious OHKO / forced switch), return it
   immediately. `decision_mode = "forced_line"`.

5. **MCTS policy pass** (`:7862`) — the PRIMARY chooser. Sample plausible opponent sets
   (`prepare_battles` / `prepare_random_battles`, which draw from `fp/bayesian_sets.py`),
   then `_run_mcts_policy_pass(...)` (`:8058`) produces a visit-count policy `mcts_policy`.
   A hard "MCTS floor" (`:7943`+) guarantees a minimum real search on a sampled state when
   the side clock is healthy, instead of collapsing to a 1-ply eval.

6. **Forced-line bias** (`:8067`) — if a forced line exists with confidence **>= 0.70**,
   multiplicatively boost that move inside `mcts_policy` (a nudge, not an override).

7. **matchup_memory bias** (`:8087`) — `matchup_memory.bias_policy(mcts_policy, battle)`.
   Loss-derived bounded reweight that prefers pivoting away from opponent species that
   historically beat us. **Currently a no-op** — see §4 (`MATCHUP_MEMORY_ENABLED=0`;
   `fp/matchup_memory.py:236` early-returns the policy unchanged when disabled).

8. **Selection — `_choose_mcts_only`** (`:8091` → `select_move_from_eval_scores(...,
   policy_source="mcts")` at `:6783`, which early-returns to `_choose_mcts_only` at
   `:6799` because the penalty pipeline is gated OFF). This is the default selector. It:
   - runs **`_apply_hard_legality_and_safety`** (`:6600`): trapped-switch removal,
     Magic-Bounce-reflected-move demote, full-HP-recovery demote, then the **decision
     loop-breaker** `break_repeated_decision` (`:6700` → `:6372`, currently a no-op — see
     §4);
   - applies the **immediate-survival switch override** (`:6741`) — a safety net (prefer a
     competitive switch if we are likely KOed before acting), not a heuristic bias;
   - picks the top line by **deterministic argmax** (`:6768`; `selection =
     "deterministic_argmax"`). It does NOT re-sample from the policy.
   `decision_mode = "mcts"`, `decision_mode_detail = "mcts_only"`.

9. **Eval-fallback path** (`:8111`, only if MCTS produced no policy) — 1-ply
   `evaluate_position` over the sampled states, then forced-line bias (`:8151`),
   matchup_memory bias (`:8164`), and `select_move_from_eval_scores` **with the full
   penalty pipeline** (`:6808`+: `apply_heuristic_bias`, `filter_blocked_moves`,
   `apply_ability_penalties`, `apply_team_intent_bias`, …). `decision_mode =
   "eval_fallback"`. This is the ONLY live path in which the penalty pipeline and
   team_intent bias actually affect selection today.

10. **Hard fallbacks** — empty eval → `_get_fallback_move` (`:8142`); any exception →
    `_get_fallback_move` (`:8187`). `decision_mode = "fallback"`.

**One-line summary:** clock-safety → endgame → forced-line → **MCTS** → (forced/matchup
bias) → hard-legality+survival safety → deterministic argmax. The heuristic penalty stack
only runs on the eval-fallback branch or under an explicit A/B flag.

---

## 3. Post-decision layers (do NOT change the move)

After `find_best_move` returns, `fp/run_battle.py` runs two more things that are commonly
mistaken for part of the engine:

- **StrategicDecisionLayer cluster** (`fp/run_battle.py:1851`,
  `fp/battle_decision.py`) — instantiated **after** `best_move` is already chosen. It calls
  `initialize_for_battle(...)`, **logs** `[STRATEGIC] Archetype=…/Win Condition=…`, and
  writes `trace["strategic"]`. It records `engine_choice = best_move` but never changes it.
  The source comment says so: *"Log archetype detection (full move selection integration in
  next phase)."* → **DORMANT / log-only.**

- **Hybrid LLM rerank** (`fp/run_battle.py:1918`) — only runs if
  `FoulPlayConfig.decision_policy == "hybrid"` (env `DECISION_POLICY=hybrid` +
  `OPENAI_API_KEY_PLAYER`). Off by default. → **DORMANT unless configured.**

---

## 4. Layer status table

Verified by grepping the actual call sites in `find_best_move` / `_choose_mcts_only` /
`run_battle.py`. "ON" = reached on the primary live path; "DORMANT" = code present and
constructed but not reached on the live decision path (log/trace only, or gated OFF by
default); "OFF" = explicitly disabled by an env flag in the deployed `.env`.

| Layer / module | Status | Env flag (deployed `.env` value) | Purpose (one line) |
|---|---|---|---|
| CRITICAL-CLOCK fast path (`main.py:7551`) | **ON** | `CRITICAL_CLOCK_FAST_PATH_SECONDS` (const) | Skip MCTS, play instant heuristic move when the side clock is critically low (inactivity-forfeit safety). |
| Endgame solver (`main.py:7801`, `fp/search/endgame.py`) | **ON** (conditional) | `ENDGAME_MAX_POKEMON` (const) | Deterministically solve small endgames before MCTS; skipped under tight clock. |
| Forced-line detection (`main.py:7833`, `fp/search/forced_lines.py`) | **ON** | — | Short-circuit obvious lines at confidence ≥ 0.90; bias MCTS at ≥ 0.70. |
| Bayesian opponent-set sampling (`fp/bayesian_sets.py` via `standard_battles.py`) | **ON** | — | Draw plausible opponent sets per mon to feed MCTS opponent modeling. |
| **MCTS policy pass** (`main.py:8058`, `_run_mcts_policy_pass`) | **ON (PRIMARY)** | `MCTS_FLOOR_MS`, `PS_SEARCH_TIME_MS`, `DECISION_SAMPLING_BUDGET_FRACTION` | Multi-sample MCTS over sampled sets = the real move chooser. |
| Forced-line bias (`main.py:8067`, `:8151`) | **ON** | — | Multiplicatively boost a high-confidence forced move inside the MCTS/eval policy. |
| **matchup_memory bias** (`main.py:8087`, `fp/matchup_memory.py:218`) | **OFF** | `MATCHUP_MEMORY_ENABLED=0` (code default `1`); `MATCHUP_MEMORY_AB=1` | Loss-derived switch-bias vs known-bad matchups. `bias_policy` early-returns unchanged when disabled; A/B harness kept intact. |
| `_choose_mcts_only` selection (`main.py:6708`) | **ON (default selector)** | `FOULER_PENALTY_PIPELINE=0` routes MCTS here | Deterministic argmax over hard-legality/safety-filtered MCTS policy. |
| Hard-legality + survival safety (`main.py:6600`) | **ON** | — | Trapped-switch removal, Magic-Bounce / full-HP-recovery demote, immediate-survival switch. |
| **Decision loop-breaker** `break_repeated_decision` (`main.py:6372`, applied `:6700`) | **OFF (2026-07-04)** | `FOULER_LOOP_BREAK=0` (code default `1`) | Demote a move repeated ≥3× on stagnation. **Trace-proven harmful:** it caused **94% of played-vs-policy inversions (249/265 override turns)** on stall lines where correct play IS repetition; selection-override rate ~35% of losses pre-fix → ~1% bounded-bias floor with it off. Disabled in `.env` after the offline-eval gate; the mission monitor's played-vs-policy divergence gauge guards against regression. |
| **Penalty pipeline** (~14 heuristic layers, `main.py:6808`+) | **OFF (default)** | `FOULER_PENALTY_PIPELINE=0` (unset ⇒ default 0) | `apply_heuristic_bias`, `filter_blocked_moves`, `apply_ability_penalties`, etc. Preserved for A/B; only runs on the eval-fallback branch or when set to `1`. |
| team_intent bias (`main.py:6908` `apply_team_intent_bias`; trace at `:7754`) | **DORMANT** (primary path) | (part of penalty pipeline) | Role/intent reweight. Only affects selection on the eval-fallback branch; on the MCTS path it contributes trace context only. |
| StrategicDecisionLayer cluster — `archetype_analyzer`, `gameplan_generator`, `strategic_filter`, `multi_turn_planner`, `theknower_competitive` (`run_battle.py:1851`, `fp/battle_decision.py`) | **DORMANT (log-only)** | — | Constructed AFTER the move is chosen; logs archetype/gameplan + writes `trace["strategic"]`; never changes `best_move`. |
| movepool_tracker (`fp/movepool_tracker.py`, `get_threat_category`) | **ON (narrow)** | — | Threat-category read used ONLY by the force-switch fallback scoring (`run_battle.py:2030`), not the MCTS decision. |
| Hybrid LLM rerank (`run_battle.py:1918`) | **DORMANT (default)** | `DECISION_POLICY=hybrid` + `OPENAI_API_KEY_PLAYER` | Optional post-engine LLM rerank of top candidates; off unless configured. |

**Net effect of the deployed config:** the live decision is MCTS argmax under clock-safety
and hard-legality/survival safety. The penalty pipeline, the loop-breaker, and
matchup-memory bias are all OFF; the strategic cluster and hybrid rerank are dormant.

---

## 5. The ops harness (the fork's real value-add)

Upstream is a single-process bot. The fork adds a runtime + self-improvement harness around
it. None of this changes how a move is chosen; it governs HOW the bot is run and improved.

- **Worker pool** — `run.py:383` `battle_worker(...)`; `run.py` spins N concurrent workers
  (up to `--max-concurrent-battles`, live lease = 3), each laddering until a per-worker
  quota or the global `--run-count` is hit, sharing one Showdown account via a singleton
  lock (`process_lock.py` → `.bot.pid`). Bot modes: `search_ladder` (live default, `.env`
  `PS_BOT_MODE`), `challenge_user`, `accept_challenge` (`run.py:508`+).
- **Keepalive / lease / monitor** — `scripts/fouler_keepalive.ps1`,
  `scripts/fouler_daemon_keepalive.ps1` (restart on stall); `scripts/devstream_runtime_lease.py`
  + `devstream/truth/runtime-lease.json` (bounded proof window: account, machine, run count,
  concurrency, expiry — nothing runs without it); `scripts/fouler_mission_monitor.py` +
  `devstream/truth/health.json`.
- **Offline-eval gate** — `infrastructure/offline_eval_readiness.py --require-ready`, then
  `infrastructure/offline_eval.py` runs frozen-vs-candidate battles against a local
  no-security Showdown server and `--compare`s them. No engine change is trusted live until
  it passes this gate (this is how `FOULER_LOOP_BREAK=0` was validated, via the
  `--no-loop-break` arm).
- **Self-improvement loop** — `pipeline.py` (batch → autoresearch) →
  `replay_analysis/autoresearch.py` (rank recurring-loss issues) →
  `infrastructure/improve_agent.py` (LLM proposes ONE targeted diff, runs the pytest gate,
  commits if green) → `infrastructure/elo_watchdog.py` (`git revert` if live ELO drops past
  the guardrail). NOTE: this loop is what *added* the harmful loop-breaker (it chased an
  "instability" label its own patch created) — hence the offline-eval gate + divergence
  monitor now sit in front of it.

---

## 6. The "any team" product gap (future scaffolding)

The long-term product is "point it at any team and have it pilot that team." The pieces that
exist vs. what's missing:

- **Exists:** `teams/team_converter.py` converts a Showdown **export block** (text) or JSON
  team into the engine's packed team format (`single_pokemon_export_to_dict`,
  `json_to_packed`); `run.py` already supports `accept_challenge` bot mode
  (`run.py:508`+, `fp/websocket_client.py` `accept_challenge`/`challenge_user`); teams are
  loaded per-worker via `teams/load_team.py` (`assigned_team` / cycling `team_iterator`).
- **Missing (the gap):** pokepaste-URL intake (no `pokepaste` fetch exists — team_converter
  only parses pasted export text), an "accept any challenger + auto-load their-vs-our team"
  wiring, and a **per-session report** for an ad-hoc team run. Today teams are the 3 fixed
  fat/stall lists, not arbitrary user input.

Treat this section as a roadmap, not current behavior.

---

## 7. Divergence from upstream (what the fork added on top of MCTS)

Relative to `pmariglia/foul-play` at base `55fa9b4`, the fork adds (all in this repo, most
gated OFF or dormant per §4):

- **Clock safety** — side-clock-derived per-turn budget, the CRITICAL-CLOCK fast path, and
  the MCTS floor. (Upstream has a fixed search-time and no inactivity-forfeit guard.) This
  is the single most load-bearing fork addition for ladder survival.
- **A heuristic penalty pipeline** (~14 layers) in `select_move_from_eval_scores` that
  mutated/overrode the MCTS policy. Upstream does NOT post-process the search policy.
  **Now gated OFF by default** (`FOULER_PENALTY_PIPELINE=0`); the source comment at
  `main.py:320` explains this reclaimed the search's strength.
- **A decision loop-breaker** (`break_repeated_decision`) added by the self-improvement loop
  to cure an autoresearch "decision_instability" label — which then demoted correct repeated
  stall play and drove losses. Upstream has no such layer (its selector is a pool ≥75%-of-
  best weighted pick, structurally immune to this inversion). **Now OFF**
  (`FOULER_LOOP_BREAK=0`).
- **matchup_memory** loss-derived bias + A/B harness. **OFF** (`MATCHUP_MEMORY_ENABLED=0`).
- **A strategic/archetype cluster** (`fp/battle_decision.py` + `archetype_analyzer`,
  `gameplan_generator`, `strategic_filter`, `multi_turn_planner`, `theknower_competitive`).
  **DORMANT / log-only.**
- **The entire ops harness** (§5): worker pool, keepalive/lease/monitor, offline-eval gate,
  self-improvement loop, replay analysis + reporting, OBS/devstream integration.

There is an active **re-baseline** effort (`rebaseline/upstream-anchor-20260704` branch, and
a separate worktree) whose thesis is that anchoring back to upstream removes the harmful
layers "by construction." This document describes the CURRENT `fix/clock-countdown-parse-79`
tree, not that in-progress re-baseline.

---

## 8. Files that matter (quick index)

| File | Role |
|---|---|
| `fp/search/main.py` | The decision engine. `find_best_move` (`:7496`) is the chokepoint; `_choose_mcts_only` (`:6708`) is the default selector. |
| `fp/run_battle.py` | Per-battle loop; calls `find_best_move` (`:1800`); post-decision strategic-log (`:1851`) + hybrid rerank (`:1918`). |
| `run.py` | Entry point + worker pool (`battle_worker` `:383`), singleton lock, bot modes, `--run-count`. **Protected.** |
| `fp/matchup_memory.py` | Loss-derived matchup bias (`bias_policy` `:218`); OFF via `MATCHUP_MEMORY_ENABLED`. |
| `fp/battle_decision.py` | StrategicDecisionLayer cluster (dormant/log-only). |
| `fp/search/{eval,forced_lines,endgame,standard_battles,random_battles}.py`, `fp/bayesian_sets.py` | Eval, forced-line, endgame, opponent-set sampling used by `find_best_move`. |
| `infrastructure/{offline_eval,offline_eval_readiness,improve_agent,elo_watchdog}.py`, `pipeline.py` | Offline-eval gate + self-improvement loop. |
| `scripts/{devstream_runtime_lease,devstream_session,fouler_mission_monitor}.py`, `scripts/fouler_keepalive.ps1` | Runtime lease, session planner, monitor, keepalive. |
| `.env` | Deployed config; source of the flag values in §4. **Protected.** |
| `docs/archive/` | Frozen Feb-2026 snapshots and superseded reports. |
