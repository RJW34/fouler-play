# AGENTS.md -- Fouler Play (START HERE index for ANY LLM agent)

> **START HERE, whoever you are -- Codex, Claude, or any other agent.** Read in this order:
>
> 1. **`MISSION.md`** (this repo) -- what fouler-play is FOR: intent, the any-team product,
>    the anti-goals, and the definition of done. **Read it first.**
> 2. **`VENTURE_CONSTITUTION.md`** on ubunztu (`~/devstream-claude/VENTURE_CONSTITUTION.md`;
>    read-only via `ssh ubunztu cat ~/devstream-claude/VENTURE_CONSTITUTION.md`) -- the stable
>    venture rules **R1-R10** and the anti-pattern catalog. These govern the whole venture.
> 3. **`ARCHITECTURE.md`** (this repo) -- how the engine is ACTUALLY built today, verified
>    against source, with the ON/OFF/DORMANT layer table. This is the truthful engine map and
>    it **supersedes any older engine description** below, in `CLAUDE.md`, or in any root `.md`.
> 4. **The running-state ledger** `~/devstream-claude/UNTETHER_COMPLETION_CAMPAIGN.md` on
>    ubunztu (append-only) -- what happened and what is next. **Do NOT mint new "canonical" /
>    "read-this-first" docs** (spawning a new canonical doc every few days is a documented
>    failure mode -- constitution R9); append to the ledger instead.
>
> Nothing in this repo is Claude-specific. Read `CLAUDE.md` as **provider-agnostic** intent
> and history, not as a Claude-only manual. Everything below this block is older operational
> detail kept for reference; where it disagrees with MISSION.md / the constitution /
> ARCHITECTURE.md, **those three win**. In particular, any section below that defines DONE
> as "1700 on the three fat/stall pilot teams" is historical. The accepted mission is **any
> supplied team at 1700+ with per-session data/replay reporting**; the three teams are only
> the current benchmark harness.
>
> **The "2026-06-19 Codex Live Repair Hold" immediately below is VOID.** Per the constitution
> (section 3): "Codex exclusive control and all prior competing-owner locks are void." If you
> are the foreman agent now, you are cleared to act within the rules R1-R10 -- do not
> re-litigate governance or treat that hold as current.

---

# AGENTS.md — Fouler Play

## 2026-06-19 Codex Live Repair Hold

Codex thread `019ed784-dde9-7101-8804-3b3e850e45e2` is the sole
code-changing repair owner until HERMES proves lease ownership, mission checks,
proof gates, rollback, and escalation. Runtime evidence processes may run, but
recursive self-improvement/builders must not.

Current live posture:

- `HERMES` orchestrator service and Claude/HERMES repair tasks are disabled.
- Fouler ladder supervision and Discord event draining may run as runtime
  evidence only.
- The generated battle supervisor command must include `--skip-improve` unless
  an owner deliberately opens a new auto-improve proof window.
- Timeouts/disconnects are operational losses, not ties; see
  `tests/test_timeout_result_accounting.py`.
- Runtime leases must allow stale-truth cleanup so false `active_battles.json`
  state does not block recovery; see `tests/test_runtime_lease_purposes.py`.

> Codex-and-friends orientation. **Full agent manual is `CLAUDE.md`** in this directory — read that first; this file is the quick "where do I start" pointer.

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
python scripts/devstream_session.py doctor      # read-only readiness
python pipeline.py autoresearch -n 30 --no-discord  # local analysis only
```

Do not start live play from onboarding. Live play requires the exact pushed immutable
release, deployment receipt, finite DEKU-signed v3 lease, and
`HERMES-FoulerBattleSupervisor` path.

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
- **JIGGLYPUFF** (Windows) = the production battle/OBS worker only after an exact deployment receipt and DEKU-signed v3 lease
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

Historical source quoted below: `~/projects/the-abso-citadel/docs/hermes/DEVSTREAM_MISSIONS.md`
on ubunztu. This quote is **superseded for mission definition** by this repo's `MISSION.md`
and `ARCHITECTURE.md`. It remains useful as the origin of the three-team pilot protocol, not
as the product definition.

> "Prove a top player's fat/stall gen9ou teams by climbing to and sustaining 1700 ELO, faster than
> human testing, via a closed learn-from-loss-replays self-improvement loop. Do NOT redesign teams
> ('all solutions reside within the teams as built').
> DONE: reach + sustain 1700 ELO in gen9ou on the 3 provided fat/stall teams.
> MECHANISM: 30-battle batches (10/team) -> autoresearch -> ONE targeted fix -> test gate ->
> ELO-gated revert; 3 concurrent battles on ONE self-registered account; play like a human stall player.
> CONSTRAINTS: gen9ou only; account naming LEBOTJAMESXD00N; immutable Pokemon mechanics must never
> be hallucinated."

**Historical pilot done-bar:** reach and sustain 1700 ELO in gen9ou on the 3 provided
fat/stall teams, without redesigning the teams. This is **not** the full product done-bar.
The current product definition is in `MISSION.md`: accept an arbitrary supplied team, ladder
it at 1700+ level, and return per-session replay/data reporting without metric-gaming team
selection.

> Note: the mission text quotes the historical account name `LEBOTJAMESXD00N` (and `CLAUDE.md` /
> `devstream.yaml` mention older thresholds 1700/1800 and an even older name `npctypebeat`). Those
> account names are **RETIRED**. The current live account is declared by
> `devstream/truth/account-season.json` (currently `DekuFoulerLab`) and must match `.env`,
> `runtime-lease.json`, and live health. The canonical target on any conflict is **1700 ELO**.

---

## 2. START HERE

**Read first, in order:**
1. This file (`AGENTS.md`).
2. `CLAUDE.md` — operating manual (treat as provider-agnostic intent/history, not Claude-only).
3. `TASKBOARD.md` — current phase / action items.
4. `infrastructure/guardrails.json` — the hard allow/deny edit list. **Obey before editing.**
5. `git status --short` and `git log --oneline -10` — confirm branch reality (do NOT assume `master`).

**ubunztu = DEKU control/signing plane; JIGGLYPUFF = production executor.** Code may be
reviewed on a control checkout, but production runs only an exact pushed commit installed at
`D:\Releases\fouler-play\<commit>`. Keep runtime work status-only unless the matching
deployment receipt and a finite DEKU-signed v3 lease authorize a bounded Showdown batch.

### Runtime control path (status/dry-run by default)
The committed control path may inspect JIGGLYPUFF, but it must not launch from onboarding
instructions. Production activation is one immutable, receipt-bound transaction:

```
JIGGLYPUFF exact release + deployment receipt
   -> DEKU signs a finite v3 lease from that receipt
      -> scripts/install_runtime_authority.ps1 stages public keyring + lease (starts nothing)
         -> scripts/install_battle_supervisor_task.ps1 validates before task mutation
            -> scripts/start_battle_supervisor_task.ps1
               -> scripts/devstream_session.py supervise
                  -> python run.py --bot-mode search_ladder --pokemon-format gen9ou --max-concurrent-battles 3 ...
```

`run.py` is the battle entry point. It acquires a singleton lock (`process_lock.py` -> `.bot.pid`),
opens the Showdown websocket, ladders, collects `battle_stats.json` + replays, then exits after
`--run-count` battles. The supervisor restarts batches.

> **Runtime identity caveat (IMPORTANT):** a Git commit is not a deployment. JIGGLYPUFF must run a
> clean immutable release with a deployment receipt and finite DEKU-signed v3 lease. The first completed battle
> from that exact commit/tree/manifest/lease/session creates an activation receipt; only matching
> rows can create the 30-battle judgment receipt. Never revive the old mutable-checkout player loop
> or treat `infrastructure/deploy_log.json` as authority.

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
| `fp/search/main.py` (7.3k lines) | **MCTS-first decision engine.** Real order: clock-safety -> endgame -> forced-line -> **MCTS** -> forced/matchup bias -> hard-legality+survival safety -> deterministic argmax (`_choose_mcts_only`). The heuristic **penalty pipeline is default-OFF** (`FOULER_PENALTY_PIPELINE=0`). **See [ARCHITECTURE.md](ARCHITECTURE.md) for the verified pipeline + layer status table.** | `find_best_move(...)` |
| `fp/search/eval.py` (1.7k) | 1-ply position evaluation (material, hazards, HP, status). | called by main.py |
| `fp/search/forced_lines.py` | Forced-sequence detection (OHKOs, forced switches). | called by main.py |
| `fp/search/endgame.py` | Endgame / conversion logic. | called by main.py |
| `fp/playstyle_config.py` | FAT/STALL tuning knobs — the "play it like a human stall player" config. | imported by search |
| `fp/battle_modifier.py` | Showdown protocol parser (server messages -> battle state). | called by run_battle |
| `fp/run_battle.py` | Per-battle loop + result/replay capture. | called by run.py |
| `teams/` | The 3 provided fat/stall teams. **Protected — never redesign.** `teams/gen9/ou/{fat-team-1-stall, fat-team-2-balance, fat-team-3-dondozo}`; loaders in `teams/load_team.py`, `teams/team_converter.py`. | `--team-names` / `TEAM_NAMES` |
| `replay_analysis/autoresearch.py` (678) | **Autoresearch:** reads recent battle window + replays, ranks recurring-loss issues, writes report with grounded competitive context. | `run_autoresearch(last_n, queue_discord)` |
| `infrastructure/improve_agent.py` (399) | **The one-fix step.** Reads `autoresearch_latest.json` top issue, prompts an LLM (via `claude` CLI, Max OAuth) for ONE targeted diff, applies it, runs the test gate, commits if green. | `python infrastructure/improve_agent.py` |
| `infrastructure/deployment_lineage.py` | Builds and validates immutable clean-release deployment receipts. | `deployment_receipt_blockers()` |
| `infrastructure/deployment_state.py` | Binds activation and judgment to exact runtime battle provenance. | `current_deployment_context()` |
| `infrastructure/elo_watchdog.py` | Writes or validates an immutable exact-identity judgment; never mutates the live release. | `check_and_judge()` |
| `pipeline.py` | Batch analysis orchestrator; managed runs use `autoresearch --no-discord`, while DEKU owns delivery. | `python pipeline.py autoresearch -n 30 --no-discord` |
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

## 4. CURRENT STATE & ACTIVE BLOCKERS  (as of 2026-07-04)

**Working branch: `fix/clock-countdown-parse-79`** (pushed to `origin/fix/clock-countdown-parse-79`).
This is a **long-lived local fork line that has NEVER been merged back to `origin/master`** — do
not assume `master` is current; trust `git status` on the box you are on. The fork is 387 commits /
992 files ahead of the upstream base `55fa9b4`. The self-improvement loop remains explicit opt-in.
Engine promotion requires the balanced candidate-vs-frozen head-to-head gate plus the
played-vs-policy divergence monitor; the older simple-opponent evaluator is smoke only.
Each evaluated runtime family has five attempts pre-registered in the external
DEKU-owned SQLite ledger at `p < 0.01`, which bounds family-wise error to 0.05.
Unrelated Git commits do not reset this budget.

**Engine identity (the thing most stale docs get wrong):** the bot is **MCTS-first**. The heuristic
penalty pipeline, the decision loop-breaker, and matchup-memory bias are all currently **OFF**; the
strategic/archetype cluster is dormant/log-only. See **[ARCHITECTURE.md](ARCHITECTURE.md)** for the
verified pipeline and the ON/OFF/DORMANT layer table (this supersedes the old
`forced_lines -> eval -> 9 penalty layers` description everywhere).

**Honest status — NOT done:**
- **ELO band ~1050-1200** (recent live), not climbing to the 1700 target yet.
- **2026-07-04 finding:** the decision loop-breaker (`break_repeated_decision`) was **trace-proven
  harmful** — it caused 94% of played-vs-policy inversions (249/265 override turns) by demoting
  correct repeated stall play, and was disabled via `.env` (`FOULER_LOOP_BREAK=0`) after the offline
  eval gate. This is also the root of the old "decision_instability" label: the self-improvement loop
  had added the breaker to cure a label its own patch created.
- A separate **re-baseline effort** (`rebaseline/upstream-anchor-20260704` + worktree) is testing
  whether anchoring to upstream removes the harmful layers by construction.

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
4. **Receipt-gated judgment and rollback.** `elo_watchdog` judges only rows matching the current
   deployment/lease/session. A regressed judgment blocks the next batch and becomes input to a
   separately authorized rollback deployment; it never dirties the immutable release with `git revert`.
5. **Never hallucinate Pokemon mechanics.** Immutable mechanics (types, base power, abilities, speed
   tiers) must be grounded, not invented. autoresearch ships grounded competitive context for exactly
   this reason; use it.
6. **improve_agent gates on a GREEN test suite.** `require_test_pass: true` + `require_syntax_check:
   true`. A fix only commits if `pytest tests/` passes. Do not weaken or bypass the gate to force a
   commit — the broken-gate-that-reverts-everything was the February failure.
7. **Edit only allowed files.** Respect `guardrails.json allowed_modify` / `never_modify` and
   `improve_agent.ALLOWED_TARGETS`. Also: `min_games_between_deploys: 30`.
8. **Account name:** `devstream/truth/account-season.json` owns the current account; `.env`,
   `runtime-lease.json`, and health must agree. Historical account names are retired and must
   not be treated as current. One self-registered account, up to 3 concurrent battles.

---

## 6. CONVENTIONS

- **Branch:** confirm the working branch from `git status` — do NOT assume `master`. The live
  runtime + docs branch is `fix/clock-countdown-parse-79` (a long-lived fork line never merged to
  `origin/master`). Older branch names in these docs (`claude/wincon-plan-bias`,
  `opus48/multisample-mcts`) are historical — trust `git branch` / `git log` on the box you are on,
  never `git reset --hard` shared data.
- **Commit discipline:** one focused, scoped change per commit. Run `python -m pytest tests/ -v`
  **before** committing. Prefer new commits over amends.
- **Do not push to a public remote** unless explicitly told. `origin` is the shared repo. Pushing
  does not itself prove activation or authorize a live runtime.
- **ubunztu vs JIGGLY split:** write/test/commit code on **ubunztu** (Linux, code home). The bot
  does not have a default live runtime right now. Do not ladder from any machine, including
  JIGGLYPUFF, unless a proof window and runtime lease explicitly authorize the bounded run.
- **Where the data lives (both machines, runtime-generated):**
  - `battle_stats.json` — rolling battle results + ELO (`rating` field per battle).
  - `replay_analysis/*.json` — saved replay logs; `gen9ou-*.json` per battle, `*_gameplan.json` per plan.
  - `replay_analysis/autoresearch_latest.json` — **live** research input for improve_agent.
  - `replay_analysis/reports/autoresearch_latest.md` — human-readable research report.
  - `logs/decision_traces/*.json` — per-turn choice/fallback evidence.
  - `%PROGRAMDATA%\HERMES\state\fouler\deployments\` — current activation pointer plus immutable
    activation/judgment receipts. This is runtime authority, not a repo-local deploy log.

---

## 7. GLOSSARY

- **Autoresearch** — the deterministic analysis step (`replay_analysis/autoresearch.py`) that mines
  the recent battle window for recurring-loss signals (hazard pressure, early material bleed, endgame
  conversion, decision instability) and emits a ranked `top_issue` with grounded context.
- **improve_agent** — the "ONE targeted fix" step. Takes the autoresearch `top_issue`, asks an LLM
  for a single small diff to an allowed file, applies it, runs the test gate, commits if green.
- **elo_watchdog** — the judgment writer. It filters to exact deployment/lease/session battle rows,
  writes one immutable result after 30 decisive battles, and blocks on regression without editing Git.
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
