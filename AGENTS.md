# AGENTS.md — Fouler Play

> Codex-and-friends orientation. **Full agent manual is `CLAUDE.md`** in this directory — read that first; this file is the quick "where do I start" pointer.

## Current Devstream Runtime Truth - 2026-06-20

- Live runtime is on JIGGLYPUFF at `Ryanj@192.168.1.182`,
  `D:\Projects\fouler-play`.
- JIGGLY runs `HERMES-FoulerMissionMonitor` every 5 minutes through
  `scripts\fouler_mission_monitor_task.ps1`.
- Canonical HERMES health proof is
  `devstream\truth\mission-monitor.json`, with tickets under
  `devstream\tickets\fouler-play\`.
- The mission monitor treats timeouts, disconnects, inactivity, and forfeits
  as losses for safety-valve math; do not report them as ties.
- It raises tickets for stale health, duplicate ladder runners, idle runtime,
  completed finite supervisor cycles, Discord reporting defects, loss streaks,
  low recent win rate, recent rating drawdown from a rated-window peak, and
  session stop-loss governance. It also blocks readiness claims until
  `latest-elo-proof.json` proves the 1700 sustain contract across the three
  fixed teams.
- It may repair only through its rails: renew a finite runtime lease and start
  the bounded battle supervisor when there are no duplicate runners, no stop
  file, and no active session stop-loss breach. Do not start additional ladder
  clients manually.

## What this repo is

An overnight team-testing service for competitive Pokemon (gen9ou): load fat/stall teams, the bot ladders them while you sleep, you get a morning report on hard matchups and underperforming Pokemon. Forked from [pmariglia/foul-play](https://github.com/pmariglia/foul-play).

## Required reading order for a fresh session

1. `CLAUDE.md` — the full operating manual (machine roles, responsibilities, mission, never-modify files)
2. `TASKBOARD.md` — current phase + cross-machine action items
3. `infrastructure/guardrails.json` — protected files + safety thresholds
4. `git log --oneline -5` + `git status --short` — recent state
5. Confirm working branch matches what `TASKBOARD.md` says (not just `master`)

## Build / test

```bash
pip install -r requirements.txt           # poke-engine builds from source; needs Rust
python -m pytest tests/ -v                # full test suite
python -c "import ast; ast.parse(open('fp/search/main.py').read())"   # syntax check
python -c "from fp.search.main import find_best_move; print('OK')"    # import smoke
```

Runtime:
```bash
python run.py                             # play one session
python pipeline.py analyze -n 30          # batch analyze last 30 battles + autoresearch
python pipeline.py autoresearch -n 30     # autoresearch only
```

## DO NOT modify

Per `infrastructure/guardrails.json` and CLAUDE.md "NEVER MODIFY" lines:
- `run.py`, `config.py`, `.env`, `teams/**`
- `fp/data/movepool_data.json` is often locally dirty from refresh churn — treat as unrelated unless the task is specifically movepool data
- Files matching `never_modify` in guardrails.json

## Architectural conventions

- **Penalties, not blocks** — reduce move weights, never remove options entirely
- **Ground all Pokemon facts** via `data.pokedex_oracle.oracle` (types, abilities, moves from `data/pokedex.json` + `data/moves.json`). Never use LLM knowledge for game data
- **One improvement per cycle** — small correct changes beat ambitious broken ones
- **Tests must pass** before every push: `python -m pytest tests/ -v`

## Machine roles (proof-gated devstream as of 2026-06)

- **DEKU on ubunztu** (Linux) = brains: decision-making improvements, replay analysis, dev loop, tests, upstream merges
- **JIGGLYPUFF** (Windows) = optional remote runtime profile only after an explicit proof window and runtime lease
- Hostname detection is **OS-based not name-based** — see CLAUDE.md "How to identify your machine"
- No Fouler runtime is safe to autostart from onboarding docs. Treat status/dry-run commands as safe; `--execute`, scheduled tasks, Discord posting, and laddering require a current proof window plus a lease naming the machine and battle scope.

Note: `MAGNETON` mentioned in CLAUDE.md as a possible runtime hostname is RETIRED as of 2026-05-13 (see the-abso-citadel/docs/hermes/HERMES-BODY-2026-05-13.md). Treat MAGNETON references in this repo as historical.

## Pushing

`master` branch on `origin` (github.com/RJW34/fouler-play). Pull `upstream` (pmariglia/foul-play) for upstream merges.
# AGENTS.md — Fouler-Play Agentic Index

> Zero-context onboarding for any LLM agent. Read this top-to-bottom before touching anything.
> This file is the index. `CLAUDE.md` and `TASKBOARD.md` are the operational detail; this file
> tells you what they mean and where the real machinery lives.

---

## 1. MISSION & DEFINITION-OF-DONE  (verbatim canonical)

Canonical source of truth: `~/projects/the-abso-citadel/docs/hermes/DEVSTREAM_MISSIONS.md` on ubunztu.

> "Prove a top player's fat/stall gen9ou teams by climbing to and sustaining 1700 ELO, faster than
> human testing, via a closed learn-from-loss-replays self-improvement loop. Do NOT redesign teams
> ('all solutions reside within the teams as built').
> DONE: reach + sustain 1700 ELO in gen9ou on the 3 provided fat/stall teams.
> MECHANISM: 30-battle batches (10/team) -> autoresearch -> ONE targeted fix -> test gate ->
> ELO-gated revert; 3 concurrent battles on ONE self-registered account; play like a human stall player.
> CONSTRAINTS: gen9ou only; account naming LEBOTJAMESXD00N; immutable Pokemon mechanics must never
> be hallucinated."

**Definition of Done:** reach AND sustain 1700 ELO in gen9ou on the 3 provided fat/stall teams,
without redesigning the teams. ELO ~1164-1194 today; this is NOT done.

> Note: `CLAUDE.md` and `devstream.yaml` mention older thresholds (1700 / 1800) and an older account
> name (`npctypebeat`). The canonical mission above wins on any conflict: target is **1700**,
> account is **LEBOTJAMESXD00N**.

---

## 2. START HERE

**Read first, in order:**
1. This file (`AGENTS.md`).
2. `CLAUDE.md` — operating manual (treat as provider-agnostic intent/history, not Claude-only).
3. `TASKBOARD.md` — current phase / action items.
4. `infrastructure/guardrails.json` — the hard allow/deny edit list. **Obey before editing.**
5. `git status --short` and `git log --oneline -10` — confirm branch reality (do NOT assume `master`).

**ubunztu = code/dev home. No default live runtime is currently assigned.** You are almost
certainly on ubunztu (Linux). Write code and docs here, commit here, and keep runtime work
status-only unless a proof window and runtime lease explicitly authorize a bounded Showdown batch.
JIGGLYPUFF remains an optional Windows runtime profile, not a default place to launch laddering.

### Runtime control path (status/dry-run by default)
The committed control path can describe or inspect a remote Windows profile, but it must not launch
from onboarding instructions. `--execute` is allowed only when a current proof window and lease both
name Fouler, the machine, the account, run count, concurrency, replay/Discord behavior, and expiry.

```
devstream.yaml  runner.start
   -> scripts/jigglypuff_devstream_control.py start --run-count 10 --max-concurrent-battles 1   (dry-run plan on ubunztu)
      -> [Tailscale SSH to JIGGLYPUFF] D:\Projects\fouler-play\scripts\fouler_jigglypuff_runtime.ps1
         -> scripts/devstream_session.py start    (bounded session planner; doctor|start|stop)
            -> python run.py --bot-mode search_ladder --pokemon-format gen9ou --max-concurrent-battles N ...
```

`run.py` is the battle entry point. It acquires a singleton lock (`process_lock.py` -> `.bot.pid`),
opens the Showdown websocket, ladders, collects `battle_stats.json` + replays, then exits after
`--run-count` battles. The supervisor restarts batches.

> **Runtime/branch caveat (IMPORTANT):** the task that commissioned this index referred to a
> `supervise` subcommand and `start_battle_supervisor_task.ps1` driving
> `autoresearch -> improve_agent -> elo_watchdog`. Those names live on the **JIGGLYPUFF runtime
> branch** (`opus48/multisample-mcts`, fix commit `82dee164`), which is NOT synced to ubunztu.
> On ubunztu's checked-out branches the closed loop is driven by `pipeline.py` +
> `infrastructure/linux/developer_loop.sh` instead. The *concept* is identical (batch -> research ->
> one fix -> test gate -> ELO-gated revert); only the launcher names differ between code-home and
> runtime. When in doubt, trust `git log` on the machine you are on, and sync from JIGGLY before
> assuming the supervisor wiring is present here.

### How to run tests
```bash
python -m pytest tests/ -v           # full suite (this is the improve_agent gate)
python -m pytest tests/ -q --tb=short
# syntax/import smoke-checks used by the agent loop:
python -c "import ast; ast.parse(open('fp/search/main.py').read())"
python -c "from fp.search.main import find_best_move; print('OK')"
```

### Run the research loop by hand
```bash
python pipeline.py autoresearch -n 30          # research only -> autoresearch_latest.json + .md
python pipeline.py analyze -n 30               # batch report + autoresearch
python infrastructure/improve_agent.py --dry-run   # show the fix it WOULD make, apply nothing
```

---

## 3. ARCHITECTURE MAP

| Module / file | Purpose | Entry point |
|---|---|---|
| `run.py` | Battle entry point: websocket, laddering, batch loop, data collection. **Protected — do not edit.** | `python run.py ...` |
| `process_lock.py` | Singleton lock (`.bot.pid`) — one `run.py` per account. | `acquire_lock()` (called from run.py) |
| `fp/search/main.py` (7.3k lines) | **MCTS / decision engine.** `forced_lines -> eval -> 9-layer penalty pipeline`. The core "how the bot picks a move". | `find_best_move(...)` |
| `fp/search/eval.py` (1.7k) | 1-ply position evaluation (material, hazards, HP, status). | called by main.py |
| `fp/search/forced_lines.py` | Forced-sequence detection (OHKOs, forced switches). | called by main.py |
| `fp/search/endgame.py` | Endgame / conversion logic. | called by main.py |
| `fp/playstyle_config.py` | FAT/STALL tuning knobs — the "play it like a human stall player" config. | imported by search |
| `fp/battle_modifier.py` | Showdown protocol parser (server messages -> battle state). | called by run_battle |
| `fp/run_battle.py` | Per-battle loop + result/replay capture. | called by run.py |
| `teams/` | The 3 provided fat/stall teams. **Protected — never redesign.** `teams/gen9/ou/{fat-team-1-stall, fat-team-2-pivot, fat-team-3-dondozo}`; loaders in `teams/load_team.py`, `teams/team_converter.py`. | `--team-names` / `TEAM_NAMES` |
| `replay_analysis/autoresearch.py` (678) | **Autoresearch:** reads recent battle window + replays, ranks recurring-loss issues, writes report with grounded competitive context. | `run_autoresearch(last_n, queue_discord)` |
| `infrastructure/improve_agent.py` (399) | **The one-fix step.** Reads `autoresearch_latest.json` top issue, prompts an LLM (via `claude` CLI, Max OAuth) for ONE targeted diff, applies it, runs the test gate, commits if green. | `python infrastructure/improve_agent.py` |
| `infrastructure/elo_watchdog.py` (271) | **ELO-gated revert.** Watches post-deploy ELO; `git revert`s the last deploy if ELO drops past the guardrail threshold. | `check_and_revert()` |
| `pipeline.py` | Batch orchestrator (ubunztu): detect batch completion -> autoresearch -> Discord report. | `python pipeline.py watch\|analyze\|autoresearch` |
| `scripts/devstream_session.py` | Bounded session planner: `doctor` / `start` / `stop`. Builds the `run.py` command line from `.env`. | `python scripts/devstream_session.py start` |
| `scripts/jigglypuff_devstream_control.py` | Optional ubunztu->JIGGLY control profile (status/dry-run by default; execute requires proof window + lease). | `... control.py status` |
| `scripts/fouler_jigglypuff_runtime.ps1` | JIGGLY-side PowerShell worker invoked only by a proof-gated control plane. | status/proof-window runs only |
| `infrastructure/guardrails.json` | Allow/deny edit list + safety thresholds. | read by improve_agent & elo_watchdog |

### Files `improve_agent` is ALLOWED to edit
Hard-coded `ALLOWED_TARGETS` in `infrastructure/improve_agent.py` (a strict subset of guardrails' `allowed_modify`):
```
fp/search/main.py
fp/search/eval.py
fp/search/forced_lines.py
fp/search/endgame.py
fp/playstyle_config.py
fp/team_analysis.py
fp/opponent_model.py
```
Issue->file routing (`pick_target_file`): `hazard_pressure`/`early_bleeding` -> `eval.py`;
`endgame_conversion` -> `endgame.py`; everything else -> `main.py` (the penalty pipeline).
The full repo-wide edit policy is in `guardrails.json`. `run.py`, `config.py`, `.env`, `teams/**`
are **never_modify**.

---

## 4. CURRENT STATE & ACTIVE BLOCKERS  (as of 2026-06-02)

**What just got fixed (commit `82dee164`, branch `opus48/multisample-mcts`, on JIGGLY):**
The self-improvement loop — crashing/orphaned since February — now mechanically runs. Four bugs fixed:
1. `improve_agent` UTF-8 vs cp1252 crash on the `≈` character.
2. Corrupt-patch apply (bad diffs were poisoning the tree).
3. A permanently-red test gate that reverted *every* fix.
4. A missing supervisor singleton guard.
Result: `autoresearch -> improve_agent -> elo_watchdog` is wired into the runtime supervisor, and
bad fixes are now correctly reverted.

**Honest status — NOT done:**
- **No auto-fix has committed yet.** The loop runs but hasn't landed an accepted improvement.
- **ELO has not climbed.** Sitting ~1164-1194; target is 1700.
- **Top blocker: "decision instability".** The recurring top issue keeps **failing the test gate**,
  so every proposed fix gets rejected. Likely causes: the diff is sent too little code context
  (`MAX_CODE_LINES=500`, and for big files like `main.py` only the *last* 500 lines are sent), and/or
  the grounded issue set is too narrow. Fixing this probably means giving the agent more/targeted
  code context or broadening the issue catalog — not hacking the gate to pass.

**Dead artifact warning:** `replay_analysis/improvement_todo.json` (February) is a **dead legacy
artifact**. The live pipeline reads `replay_analysis/autoresearch_latest.json`. Do not drive work
off `improvement_todo.json`.

**Repo hygiene note:** the working tree carries unrelated local churn — `battle_stats.json`,
`fp/data/movepool_data.json`, `replay_analysis/autoresearch_latest.json`, and `*.predeploy-bak`
files. Treat these as runtime/data churn unless your task is specifically about them. Root has many
stale ops `.md`/`.log` files; `CLAUDE.md` + `TASKBOARD.md` + this file are the current intent.

---

## 5. HARD CONSTRAINTS / GUARDRAILS

1. **Do NOT redesign the teams.** "All solutions reside within the teams as built." `teams/**` is
   `never_modify`. The lever is decision-making, not team composition.
2. **gen9ou only.** Format is fixed.
3. **Single `run.py` per account (singleton lock).** `process_lock.py` enforces one bot instance;
   duplicate `run.py` processes are a known top failure mode. Verify single-process before launching.
4. **ELO-gated revert.** `elo_watchdog` reverts any deploy that drops ELO past
   `guardrails.json: safety.max_elo_drop_before_revert` (50). Improvements must survive on the ladder,
   not just in tests.
5. **Never hallucinate Pokemon mechanics.** Immutable mechanics (types, base power, abilities, speed
   tiers) must be grounded, not invented. autoresearch ships grounded competitive context for exactly
   this reason; use it.
6. **improve_agent gates on a GREEN test suite.** `require_test_pass: true` + `require_syntax_check:
   true`. A fix only commits if `pytest tests/` passes. Do not weaken or bypass the gate to force a
   commit — the broken-gate-that-reverts-everything was the February failure.
7. **Edit only allowed files.** Respect `guardrails.json allowed_modify` / `never_modify` and
   `improve_agent.ALLOWED_TARGETS`. Also: `min_games_between_deploys: 15`.
8. **Account name:** `LEBOTJAMESXD00N`. One self-registered account, up to 3 concurrent battles.

---

## 6. CONVENTIONS

- **Branch:** confirm the working branch from `git status` — do NOT assume `master`. ubunztu code
  home currently sits on `claude/wincon-plan-bias`; the live runtime fix is on JIGGLY's
  `opus48/multisample-mcts`. These diverge — sync deliberately, never `git reset --hard` shared data.
- **Commit discipline:** one focused, scoped change per commit. Run `python -m pytest tests/ -v`
  **before** committing. Prefer new commits over amends.
- **Do not push to a public remote** unless explicitly told. `origin` is the shared repo; pushing
  auto-deploys. The ELO watchdog also pushes its own reverts.
- **ubunztu vs JIGGLY split:** write/test/commit code on **ubunztu** (Linux, code home). The bot
  does not have a default live runtime right now. Do not ladder from any machine, including
  JIGGLYPUFF, unless a proof window and runtime lease explicitly authorize the bounded run.
- **Where the data lives (both machines, runtime-generated):**
  - `battle_stats.json` — rolling battle results + ELO (`rating` field per battle).
  - `replay_analysis/*.json` — saved replay logs; `gen9ou-*.json` per battle, `*_gameplan.json` per plan.
  - `replay_analysis/autoresearch_latest.json` — **live** research input for improve_agent.
  - `replay_analysis/reports/autoresearch_latest.md` — human-readable research report.
  - `logs/decision_traces/*.json` — per-turn choice/fallback evidence.
  - `infrastructure/.../deploy_log.json` — deploy + revert ledger (used by elo_watchdog).

---

## 7. GLOSSARY

- **Autoresearch** — the deterministic analysis step (`replay_analysis/autoresearch.py`) that mines
  the recent battle window for recurring-loss signals (hazard pressure, early material bleed, endgame
  conversion, decision instability) and emits a ranked `top_issue` with grounded context.
- **improve_agent** — the "ONE targeted fix" step. Takes the autoresearch `top_issue`, asks an LLM
  for a single small diff to an allowed file, applies it, runs the test gate, commits if green.
- **elo_watchdog** — the safety net. Reverts a deploy via `git revert` if post-deploy ELO drops past
  the guardrail threshold. This is the "ELO-gated revert".
- **Test gate** — `pytest tests/` must pass (plus syntax/import checks) before any fix commits.
  Currently the "decision instability" fix keeps failing this gate.
- **Batch** — 30 battles (10 per team across the 3 teams). One batch -> one autoresearch -> at most
  one fix.
- **Decision instability** — the current top recurring issue: the bot's move choice is unstable /
  loops / falls back. The improve loop keeps trying and failing to fix it through the test gate.
- **Penalty pipeline** — the 9-layer scoring in `fp/search/main.py` that ranks candidate moves after
  forced-line detection and 1-ply eval.
- **Fat / Stall** — the team archetypes under test: defensive, hazard-and-recover, PP-stall play.
  Must be played faithfully (see `fp/playstyle_config.py`), not as low-ELO cheese.
- **DEKU** — the HERMES control plane on ubunztu that orchestrates dev work and the JIGGLY runtime.
- **devstream** — the live-streamed autonomous run; `devstream.yaml` declares this project's runner,
  health probe, and "truth" files.
- **autoresearch_latest.json** — LIVE research artifact (use this). **improvement_todo.json** —
  DEAD February artifact (ignore).
