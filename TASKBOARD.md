# TASKBOARD.md - Fouler Play Coordination

**This is the single source of truth for fouler-play operations.** If anything contradicts this file, this file wins. `fouler-play-v2` has been archived — it was a duplicate clone DEKU created. Only `/home/ryan/projects/fouler-play` exists now.

**Purpose:** Overnight team-testing service for a competitive Pokemon player (fat/stall teams in gen9ou)
**Branch:** `master` is the live deployment/base branch and now contains the latest Codex-readiness/doc cleanup merged on 2026-03-09 (`a8b2d31`). Treat older branch-specific notes as historical unless a newer branch is explicitly called out here.
**Bot Account:** Use live `.env` / process truth, not hard-coded names. Current: `PS_USERNAME=claudechamp`. Windows machine hostname may vary (MAGNETON, MIRAIDON, etc.) — use OS detection.
**Updated:** 2026-05-02

---

## Standing Rules (check every session — fix drift immediately)

1. **DECISION ENGINE WORK ONLY.** Do not write infrastructure, reporting, Discord formatting, build manifests, or pipeline orchestration code. All of that is built (see "What's Already Built" below). Every commit must improve `fp/search/` or fix a documented bug in the decision engine. If you are about to create a new file outside `fp/search/` or `tests/`, STOP and reconsider.
2. **Do not hard-code stale concurrency targets or runtime hosts.** Live launcher truth plus a current proof window wins. The current devstream posture is no-autostart: DEKU on ubunztu may run status/dry-run checks, while any JIGGLYPUFF batch through `scripts/jigglypuff_devstream_control.py` -> `scripts/fouler_jigglypuff_runtime.ps1` -> `start_one_touch.bat` requires an explicit proof window and runtime lease.
3. **One source of truth.** This file plus current launcher/process evidence. If docs disagree with the running launcher or command line, fix the docs immediately.
4. **Bot account:** Use live `.env` / process truth, not hard-coded names. Current: `PS_USERNAME=claudechamp`.

---

## Current Status

### 2026-06-02 — SOTA rebuild toward 1700 ELO (branch resolution + P0/P1/P2 landed)

**Canonical branch = `opus48/multisample-mcts`** (NOT master/`claude/wincon-plan-bias`).

**Branch resolution (prerequisite):** ubunztu was on `claude/wincon-plan-bias`
(master + 3 commits); the JIGGLY runtime was on `opus48/multisample-mcts` (21
commits ahead, incl. the real search fixes: `e95683b9` restore multi-sample MCTS,
`724d21ae` authoritative |raw| rating, `82dee164` improve-loop unblock). The two
shared a base at `72a410be` then diverged. Resolution: JIGGLY's `opus48` line is
canonical (live runtime + real fixes); ubunztu's docs commit (`9d24a8b` AGENTS.md)
was cherry-picked on top (CLAUDE.md conflict resolved in favor of the mission
pointer). The `e4a5aab` win-condition-bias commit was DELIBERATELY NOT merged — it
is part of the heuristic penalty family the audit says to gate OFF, and it
conflicts with the MCTS-first selection. ubunztu now fast-forwarded to canonical.

**Work landed on `opus48/multisample-mcts` (4 commits, 2026-06-02):**
- **P0** `20d703bf` — restore opponent set-sampling for MCTS; gate penalty pipeline
  OFF (`FOULER_PENALTY_PIPELINE`, default 0); `MCTS_BLEND_MAX_SAMPLES` 2->8.
  Root cause: the fork's `bayesian_set_probabilities` built SmogonSets candidates
  from ONLY revealed moves, so unrevealed opponents were sampled with an EMPTY
  moveset and were INERT in MCTS. Proof (`scripts/probe_sampling.py`): opponent
  moves visible to the engine 15 -> 96 (+81); MCTS policy goes from degenerate
  93.7% scald-spam to a faithful stall hedge.
- **P1** `9fd9192b` — REAL offline win-rate eval gate (`infrastructure/offline_eval.py`
  + `_offline_baseline.py`): fouler `run.py` vs frozen poke-env baseline on a LOCAL
  pokemon-showdown `--no-security` server, Wilson LCB + two-proportion z-test.
  `improve_agent` now gates on this (pytest = pre-filter only) and sends the LLM the
  AST-extracted implicated functions, not a 500-line tail. `elo_watchdog` now uses
  win-rate over a >=30-battle window as the primary metric and only trusts ELO once
  Glicko deviation (rprd) < 50. ELO single-source: retired the regex-HTML scraper in
  `elo_tracker.py`; canonical path is the ladder JSON API (`_fetch_elo`/`_fetch_glicko`).
- **P2** `00a21908` — replay-grounded regret mining (`autoresearch._regret_issue`):
  flags turns where the chosen move's MCTS value << best legal line.
- proof/harness hardening `b3b314c9`.

**Tests:** 986 pass (1 pre-existing unrelated devstream-report flake).

**DEPLOY STEP (historical — do not use as launch permission):**
The JIGGLY runtime profile at `D:\Projects\fouler-play` was last documented on the OLD `opus48` tip
(`82dee164`), missing the docs commit + the 4 work commits. To deploy:
1. On JIGGLY: stash/commit the dirty runtime state files (battle_stats.json etc.),
   then `git pull`/fast-forward `opus48/multisample-mcts` to `b3b314c9`.
2. No poke-engine rebuild needed (poke-engine version unchanged; pure-Python edits).
3. Penalty pipeline is OFF by default — to A/B against the old behavior on ladder,
   set `FOULER_PENALTY_PIPELINE=1`.
4. To run the offline gate on JIGGLY, create `.venv-eval` (poke-env) and clone
   `pokemon-showdown` locally as on MIRAIDON.
**Honest scope note:** P0 search-quality restoration is PROVEN deterministically
(probe A/B). The offline harness is functional (smoke: fouler 2/2 vs
SimpleHeuristicsPlayer) but SimpleHeuristics is below fouler's discrimination floor,
so full-battle win-rate deltas vs that baseline are uninformative; live-ladder ELO
is still required to confirm the 1700 trajectory.

**2026-03-25 — Bot running, all critical bugs fixed, focus on Phase 2-3**

- **Bot is live** on `npctypebeat`, playing gen9ou with 3 concurrent battles across all 3 teams.
- **3413 battles played.** 1735W-1671L (50.9% WR overall, ~48% last 100).
- **All 3 critical bugs fixed** (#3 resisted move spam, #5 recovery loops, #7 switch oscillation) — see Decision Engine Bugs section below.
- **Recent engine commits** (March 23-25): stagnation switch boost for resisted moves, recovery loop detection, switch oscillation detection, out-healing pattern detection, passive penalty tuning, hazard suppression at critical HP, type-disadvantage switch penalties.
- **Infrastructure is DONE.** No further infra/reporting/pipeline work needed.

Decision pipeline: forced_lines -> eval -> penalty pipeline (9 layers). Bot needs to reach 1700+ ELO for matchup data to be meaningful. WR is flat at ~50% — next gains come from Phase 2-3 decision improvements.

---

## NEXT ACTION (read this first)

**STOP BUILDING INFRASTRUCTURE. START FIXING THE BOT.**

The following systems are complete and must NOT receive further development unless broken:
- `replay_analysis/autoresearch.py` (585 lines) — done
- `infrastructure/build_manifest.py` (340 lines) — done
- `infrastructure/discord_reporting.py` (762 lines) — done
- `infrastructure/event_queue_lib.py` (273 lines) — done
- `pipeline.py` (746 lines) — done
- `infrastructure/autoresearch_post_commit.py` (81 lines) — done

**What agents must do (in this order):**
1. Implement Phase 2-3 items — PP tracking, switch prediction, recovery timing, hazard awareness, Bayesian set updating
2. Run `python -m pytest tests/ -v` after every change
3. Do NOT create new infrastructure files, reporting pipelines, or meta-tooling

**Operational prerequisite:** Fresh `battle_stats.json` only matters after an authorized proof-window batch. If the bot is not authorized to run, keep the runtime stopped and work on offline decision-engine proof instead.

**All critical bugs are fixed.** See Decision Engine Bugs section below for details.

---

## What's Already Built (DO NOT REBUILD OR POLISH)

These systems are **complete and working**. Do not recreate, extend, refactor, or polish them:
- `streaming/` — OBS integration. `serve_obs_page.py` is the main server (port 8777). Low priority.
- `replay_analysis/autoresearch.py` — Automated loss pattern detection (585 lines). DONE.
- `replay_analysis/team_performance.py` — Player-facing report generator. DONE.
- `replay_analysis/` — `analyzer.py`, `turn_review.py`, `batch_analyzer.py`, replay JSONs. DONE.
- `infrastructure/build_manifest.py` — Build-to-battle tracking (340 lines). DONE.
- `infrastructure/discord_reporting.py` — Discord report formatting (762 lines). DONE.
- `infrastructure/event_queue_lib.py` — Discord event queue (273 lines). DONE.
- `infrastructure/autoresearch_post_commit.py` — Post-commit manifest hook. DONE.
- `pipeline.py` — Analysis orchestrator (746 lines). DONE.
- `infrastructure/linux/` — developer loop, analyze_performance.sh, systemd service. DONE.
- `infrastructure/windows/` — player_loop.bat, deploy_update.bat, install_task.bat. DONE.
- `infrastructure/elo_watchdog.py` — auto-revert on ELO drop. DONE.
- `fp/playstyle_config.py` — FAT/STALL playstyle tuning with switch/pivot/recovery/chip multipliers.
- `fp/search/endgame.py` — endgame solver for 1v1/2v1 scenarios.
- `fp/team_analysis.py` — win condition identification.

---

## Machine Ownership

### DEKU (Linux) owns:
- Decision-making improvements (make the bot play fat/stall correctly)
- Replay analysis quality (make the morning report useful for the player)
- Developer loop (`infrastructure/linux/developer_loop.sh`)
- Test suite maintenance
- Upstream merge management

### JIGGLYPUFF (Windows) owns:
- Optional runtime readiness proof (`D:\Projects\fouler-play`, controlled by DEKU over Tailscale SSH only under proof-window/lease authorization)
- Battle data preservation after an authorized bounded batch (`battle_stats.json`, replays)
- poke-engine builds (Rust toolchain)
- ELO monitoring + watchdog
- Environment/credentials setup
- Streaming (low priority — only if everything else is running)

---

## DEKU Action Items

### Phase 2: Better Opponent Modeling (target: 1200 -> 1400)
- [x] Weighted sampling by set count
- [ ] Speed range narrowing (infrastructure exists, needs to be used)
- [ ] Bayesian updating as moves/items are revealed
- [ ] Track revealed information to update set probabilities

### Phase 3: Correct Fat/Stall Play (target: 1400 -> 1550)
- [x] Win condition awareness
- [x] Momentum tracking
- [ ] PP tracking (infrastructure built, needs battle_modifier integration)
- [ ] Switch prediction from type matchups
- [ ] Recovery timing — when to Recover vs when to attack
- [ ] Hazard awareness — prioritize Stealth Rock early, Defog/Rapid Spin when needed

### Phase 4: Advanced (target: 1550 -> 1700)
- [x] Endgame solver
- [ ] Team archetype classification of opponent's team
- [ ] Game-phase awareness (early/mid/late game strategy shifts)
- [ ] Matchup-aware lead selection

### Morning Report Improvements
- [ ] Add "key replays to watch" (closest losses, biggest upsets) to report output
- [ ] Add per-session summaries (not just all-time) — "last night: 15 games, 9-6"
- [ ] Add move-level analysis: "Gliscor used Earthquake into Corviknight 4 times" (misplays)
- [ ] Discord webhook delivery of morning summary (optional, uses DISCORD_WEBHOOK_URL from .env)

---

## JIGGLYPUFF Action Items

### Keep Runtime Readiness Verifiable
1. Ensure DEKU can run `python3 /home/ryan/projects/fouler-play/scripts/jigglypuff_devstream_control.py status` from ubunztu.
2. Keep scheduled tasks disabled unless a proof window and lease authorize a bounded batch.
3. After an authorized batch, verify `battle_stats.json` has new entries and preserve replay evidence.
4. If the bot disconnects or crashes during an authorized batch, collect logs and stop cleanly; do not install a persistent restarter from stale docs.

### Verified Setup
- [x] Bot connects to Showdown and plays games (current live account: `npctypebeat` on 2026-03-10)
- [x] battle_stats.json is being written (check file for current count)
- [x] Replays saved to replay_analysis/
- [x] Player loop runs unattended / looping on Windows
- [x] Push battle_stats.json after each batch
- [ ] JIGGLYPUFF deployment is current, `.env` is present, `.venv` is bootstrapped, DEKU's Tailscale control wrapper reports ready, and a proof-window/lease document exists before any `--execute`.

---

**Streaming:** See `docs/STREAMING.md` for overlay fix tasks (low priority).

---

## Bug Reports
- 2026-03-10: Drift audit found stale hard-coded account/runtime references (`ALL CHUNG`, `BugInTheCode`, `LEBOTJAMESXD*`, old concurrency targets, old task name/channel prose). Repo should prefer `.env` / current process truth over fixed names wherever practical.

### Decision Engine Bugs (from 2026-02-14 battle analysis)

**Fixed this session:**
- [x] **Destiny Bond awareness** — Bot KO'd opponent's Ceruledge with its win-condition Gliscor when Destiny Bond was revealed and Ceruledge was at low HP. Fixed: `detect_odd_move()` now checks for revealed Destiny Bond + ≤40% HP and applies 85% penalty to damaging attacks.
- [x] **Toxic suppressed vs boosted threats when no offensive answer** — Blissey had Seismic Toss (immune to Ghost) as its only damaging move vs Gholdengo (+2 SpA). `apply_threat_switch_bias()` suppressed Toxic as "passive" even though it was the only progress line. Fixed: Added `no_offensive_answer` exemption for status moves when all attacks are weight-0.
- [x] **Calm Mind + fixed damage** — Blissey used Calm Mind to boost SpA when its only damaging move was Seismic Toss (fixed 100 damage, ignores SpA). Fixed: `detect_odd_move()` now flags setup moves when no non-fixed-damage attack uses the boosted stat.

**Documented for later:**
- [x] **#3: Resisted move spam (mislabeled as "Ghost-immune-to-Dark")** — Gholdengo used Hex into Ting-Lu (Dark/Ground) for 8 turns. Investigation found Ghost vs Dark is 0.5x (resisted), not immune — the type system was correct. Root cause was the bot repeating a resisted move without switching. Fixed 2026-03-23: `apply_repetition_penalty` now detects when a repeated move has ≤0.5x effectiveness and boosts switch options by 1.6x (stagnation switch boost). Test: `test_resisted_move_spam_boosts_switches`.
- [x] **#5: Recover loop detection** — Blissey entered a 4-turn Recover loop vs Drain Punch Conkeldurr. Fixed 2026-03-23: `apply_repetition_penalty` now detects when recovery moves are repeated 3+ times and boosts switch options by 1.6x, pushing the bot to switch to a Pokemon that can actually progress. Test: `test_recovery_loop_boosts_switches`.
- [x] **#6: Body Press vs Waterfall type matchup** — Fixed and regression-proofed. `f4ab71e test: fix Body Press regression proof` documents the proof path so future agents should not keep treating this as an open mystery.
- [x] **#7: Infinite switch loop detection** — Corviknight and Blissey alternated switches for 11 turns vs Tera Normal Dragonite without ever using Toxic. Fixed 2026-03-23: `apply_repetition_penalty` now detects switch oscillation (3+ switches with 2+ distinct targets) and boosts attack moves by 1.5x, forcing the bot to attack instead of cycling. Test: `test_switch_oscillation_boosts_attacks`.

---

## Communication Protocol

- Push code/data to the branch that matches current repo reality. As of `a8b2d31`, `master` is again the deployment/base branch and the repo-guidance baseline.
- Update this TASKBOARD.md when completing items (check the box: `[x]`)
- DEKU pushes code changes, JIGGLYPUFF pushes battle data
- Check `battle_stats.json` for performance tracking
- If you need the other machine to act, write it under their Action Items section and push

## Completed Phases
- [x] Phase 1: Penalty system, timeout protection, Focus Sash detection, setup/phazer awareness, substitute awareness, contact move penalties
- [x] All porting work from upstream fork
- [x] Streaming pipeline (built, low priority)
- [x] Replay analysis pipeline (built, needs quality improvements)
- [x] Win-rate diagnosis (2026-02-07): 76% of losses had no Stealth Rock, recovery underused, excessive switching. Fixed with early-game hazard urgency + FAT/STALL recovery boost.
- [x] MCTS-to-Eval overhaul (2026-02-09): Replaced MCTS with 1-ply eval engine + forced line detection. Created `fp/search/eval.py`, `fp/search/forced_lines.py`. Pipeline: forced_lines -> eval -> penalty pipeline. All 9 penalty layers preserved.
- [x] Round system + disconnect tracking (2026-03-02): All battle outcomes count toward quota. Round-complete summary with per-team stats. Dead battle timeout (DISCONNECT_STRIKES). Branch consolidation (foulest-play -> master).
