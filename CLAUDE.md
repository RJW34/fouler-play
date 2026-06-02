# CLAUDE.md - Fouler-Play Autonomous Operations Manual

> **NEW AGENT? START AT `AGENTS.md`** (zero-context index: mission, launch path, architecture map,
> current blockers, guardrails). Canonical mission: climb to and SUSTAIN **1700 ELO** in gen9ou on the
> 3 provided fat/stall teams via the closed learn-from-loss self-improvement loop — **do NOT redesign
> the teams**. Account `LEBOTJAMESXD00N`. Source of truth: `~/projects/the-abso-citadel/docs/hermes/DEVSTREAM_MISSIONS.md`.

## Mission

Build an **overnight team-testing service** for a competitive Pokemon player. The player loads fat/stall teams, the bot plays them on ladder while they sleep, and in the morning they get a report: which matchups were hard, which Pokemon underperformed, which replays to study. The live bot account on this machine is the `.env`-configured `PS_USERNAME` (currently **"npctypebeat"**), playing **gen9ou**. The Windows bot machine hostname may be MAGNETON, MIRAIDON, or similar — use OS detection, not hostname.

**Why 1700 ELO matters:** At 1200 (where we are now), opponents play poorly and the data is meaningless for team evaluation. The bot must reach **1700+** so that matchup data reflects how the team performs against competent opponents. 1700 is not the goal — it's the minimum quality threshold for useful test data.

**Current devstream deployment:** DEKU runs on ubunztu as the control plane. The live Windows bot runtime is JIGGLYPUFF at `D:\Projects\fouler-play`, controlled through Tailscale SSH by `scripts/jigglypuff_devstream_control.py` and the remote PowerShell worker `scripts/fouler_jigglypuff_runtime.ps1`.

**What the player actually consumes:**
- Per-team win rates and trends across overnight sessions
- Worst matchups: "You lose 70% of games when the opponent has Gholdengo"
- Per-Pokemon performance: "Gliscor fainted in 80% of your losses"
- Replay links for the most instructive wins and losses
- Actionable team-building suggestions: "This team needs a Gholdengo answer"

**Critical constraint — play the team faithfully.** The bot must play fat/stall teams *as a human would play them*: pivot for matchup advantage, preserve HP, chip with hazards, recover, stall PP. Do NOT take shortcuts that win at low ELO but wouldn't generalize (cheese strategies, hyper-aggressive plays with stall teams, etc.). The `playstyle_config.py` FAT/STALL tuning exists for this reason — respect it.

## You Own Your Machine

This project runs on **multiple machines**. When you start a session, determine which machine you are on and act accordingly.

### How to identify your machine
- Run `uname -s` or check `$OSTYPE`. Linux = **DEKU**. Windows / `MSYS` / `MINGW` = Windows bot machine.
- Or check the hostname: `hostname`. Known live devstream hosts: ubunztu/DEKU (Linux control plane), JIGGLYPUFF (Windows bot/runtime worker), MIRAIDON (human workstation).
- The Windows machine hostname may change. Use OS detection, not hostname matching.

---

## DEKU (Linux) — Brains

**You own:** Decision-making improvements, replay analysis, morning report quality, developer loop, test suite, upstream merges.

### Your loop: `infrastructure/linux/developer_loop.sh`
Runs continuously. Pulls battle data -> analyzes performance -> invokes a coding agent to make one improvement -> runs tests -> pushes if passing. Install as a persistent service:
```bash
bash infrastructure/linux/install_service.sh
systemctl --user start fouler-play
```

**Codex migration note (2026-03):** treat this file as provider-agnostic intent/history, not Claude-only workflow. For automated coding sessions, the required startup remains: read `CLAUDE.md`, then `TASKBOARD.md`, then inspect `git status`/`git log`, then obey `infrastructure/guardrails.json` before editing.

### Your responsibilities (in priority order)
1. **Improve the bot's decision-making** — see TASKBOARD.md for the current phase. Check `battle_stats.json` for current ELO and win rate. The bot needs to play fat/stall correctly at a higher level. Focus on improvements that help the bot play these archetypes faithfully: better switching, hazard management, recovery timing, PP awareness, matchup-based pivoting. Run `python -m pytest tests/ -v` after each change.
2. **Improve the morning report** — `replay_analysis/team_performance.py` generates per-team analysis. Make this output more useful for a competitive player studying their teams. The report should answer: "Is this team viable? What does it lose to? Which Pokemon are the weak links? Which replays should I watch?" This is the primary deliverable the player consumes.
3. **Fix anything broken** — if tests fail, if imports break, if the developer loop crashes, diagnose and fix.
4. **Push to GitHub** — all code goes to `master` branch. Update TASKBOARD.md when completing items. Never modify files in the `never_modify` list in `infrastructure/guardrails.json`.

### Sub-agent pattern (DEKU)
When you have multiple independent tasks, spawn sub-agents to work in parallel:
- Agent 1: Implement a decision-making improvement (from TASKBOARD.md phase items)
- Agent 2: Analyze replay data for team weakness patterns
- Agent 3: Improve team_performance.py report output

Use your current coding-agent runtime for headless sub-agents. The exact harness can change (Codex, Claude Code, etc.), but the repo workflow should not: read intent docs first, make one focused improvement, run validations, and push only clean, scoped changes.

### Coordinating with JIGGLYPUFF
- Push code to `master`; the devstream control path deploys and starts JIGGLYPUFF through Tailscale SSH.
- If JIGGLYPUFF needs something specific, write instructions in `TASKBOARD.md` under "JIGGLYPUFF Action Items" and push.
- Read `battle_stats.json` and remote runtime truth for performance data.

---

## JIGGLYPUFF (Windows) — Brawn

**You own:** Bot operation, battle data collection, environment, poke-engine builds, ELO monitoring. Streaming is secondary.

### Your loop: DEKU-controlled bounded sessions
DEKU starts and stops bounded sessions through:
```
python3 /home/ryan/projects/fouler-play/scripts/jigglypuff_devstream_control.py status
python3 /home/ryan/projects/fouler-play/scripts/jigglypuff_devstream_control.py start --run-count 10 --max-concurrent-battles 1 --execute
python3 /home/ryan/projects/fouler-play/scripts/jigglypuff_devstream_control.py stop --execute
```

The direct Windows fallback is `infrastructure/windows/player_loop.bat` or `start_one_touch.bat`. The current Windows runtime is intentionally **single-worker by default** unless explicitly overridden via environment or launcher args. Install as a persistent scheduled task only if DEKU's remote runner is not being used:
```
Run as Administrator: infrastructure\windows\install_task.bat
schtasks /run /tn "FoulerPlayOneTouch"
```

### Your responsibilities (in priority order)
1. **Keep the bot playing** — the player loop must be running at all times. If it crashes, diagnose why and fix. The bot should be playing games continuously so there's fresh data every morning. Check logs in the repo root.
2. **Push battle data** — after each batch, `battle_stats.json` and `replays/` get committed and pushed to `master`. This is the raw material DEKU and the player need.
3. **ELO monitoring** — `infrastructure/elo_watchdog.py` runs after deploys. If ELO drops >50 from a deploy, it auto-reverts. Make sure this is working.
4. **poke-engine builds** — if DEKU pushes code that updates poke-engine version, you need Rust toolchain installed to rebuild. `pip install -e .` or `pip install poke-engine==X.X.X`.
5. **Streaming (low priority)** — the streaming pipeline in `streaming/` is built and functional but is NOT critical to the mission. Only work on it if everything above is running smoothly.
   - `serve_obs_page.py`: HTTP + WebSocket server on port 8777
   - `state_store.py`: Reads/writes `active_battles.json` and `stream_status.json`
   - Start with: `python streaming/serve_obs_page.py`

### Coordinating with DEKU
- Push battle data (stats, replays) to `master`. DEKU pulls in their developer loop.
- Check `TASKBOARD.md` for any "JIGGLYPUFF Action Items" that DEKU has written.
- If you find a bug in the bot's decision-making, note it in TASKBOARD.md under "Bug Reports" and push.

---

## Repository Structure

```
fouler-play/
├── run.py                    # Entry point (NEVER MODIFY)
├── config.py                 # FoulPlayConfig + env loading (NEVER MODIFY)
├── .env                      # Local credentials (NEVER MODIFY, not tracked)
├── .env.example              # Template for .env (tracked)
├── CLAUDE.md                 # THIS FILE — read on every session
├── TASKBOARD.md              # Cross-machine coordination — read and update
├── start_one_touch.bat       # Battle launcher (non-interactive when PS_RUN_COUNT set)
├── pipeline.py               # Analysis orchestrator (BUILT, do not modify)
├── constants.py              # Shim -> re-exports from constants_pkg/
├── constants_pkg/            # Penalty/boost values, ability sets, move flags
├── fp/
│   ├── battle.py             # Battle/Battler/Pokemon state
│   ├── battle_modifier.py    # Parses PS protocol, updates battle state
│   ├── run_battle.py         # Main battle loop + data collection hooks
│   ├── websocket_client.py   # PS websocket + multi-battle routing
│   ├── search/
│   │   ├── main.py           # 1-ply eval + penalty pipeline (core logic)
│   │   ├── eval.py           # Position evaluation (damage, recovery, hazards, switches)
│   │   ├── forced_lines.py   # Forced sequence detection (OHKOs, forced switches, phazing)
│   │   ├── speed_order.py    # Speed comparison utilities
│   │   ├── endgame.py        # Endgame solver for 1v1/2v1 scenarios
│   │   ├── standard_battles.py  # Battle sampling + weighted selection
│   │   ├── move_validators.py   # Move validation
│   │   ├── opponent_predict.py  # Opponent set/move prediction
│   │   └── helpers.py           # Search helper utilities
│   ├── team_analysis.py      # Win condition identification
│   ├── opponent_model.py     # Opponent tendency tracking
│   ├── playstyle_config.py   # Per-team playstyle tuning (FAT/STALL configs)
│   └── decision_trace.py     # Decision logging
├── infrastructure/
│   ├── guardrails.json       # File permissions + safety thresholds
│   ├── elo_watchdog.py       # Auto-revert bad deploys
│   ├── improve_agent.py      # Automated coding agent (reads autoresearch, writes fixes)
│   ├── linux/                # Linux scripts + service files
│   └── windows/              # Windows scripts + task installer
├── streaming/                # OBS/Twitch integration (BUILT, low priority)
├── replay_analysis/          # Team performance analysis (BUILT — primary player-facing output)
│   └── team_performance.py   # Main report generator — this is what the player reads
├── data/                     # Smogon data, pokedex, moves
├── teams/                    # Team files (NEVER MODIFY)
└── tests/                    # Test suite
```

## Pokemon Data Grounding (MANDATORY)

**Never use LLM knowledge for Pokemon facts.** Type matchups, abilities, move effects, stats, and common sets MUST come from the project's data files. Use the `PokedexOracle`:

```python
from data.pokedex_oracle import oracle
oracle.pokemon("gholdengo")                    # types, stats, abilities from pokedex.json
oracle.move("shadowball")                      # type, power, category from moves.json
oracle.effectiveness("ghost", ["dark"])         # 0.5 — from the actual type chart
oracle.common_sets("gholdengo")                # top moves/items from Smogon stats
oracle.matchup_summary("gholdengo", "gen9/ou/fat-team-1-stall")  # vs our team
oracle.validate_ability_claim("gholdengo", "Good as Gold")        # True
oracle.validate_type_claim("ghost", ["dark"], 0.0)                # (False, 0.5)
```

**Data files (authoritative, never override with LLM knowledge):**
- `data/pokedex.json` — Pokemon types, stats, abilities
- `data/moves.json` — move type, power, category, effects
- `fp/helpers.py` — type effectiveness chart (DAMAGE_MULTIPICATION_ARRAY)
- `data/smogon_stats_cache/gen9ou-0.json` — Smogon usage: common moves, items, tera types
- `teams/gen9/ou/` — our actual team files with Pokemon, moves, items, EVs

**Autoresearch reports include grounding blocks** — when opponent Pokemon are flagged, the report embeds their full profile (types, abilities, common moves, matchups against our teams) from the oracle. Agents reading the report should use this grounded data, not generate their own Pokemon knowledge.

## Key Design Principles

1. **Faithful play** — the bot must play each team according to its archetype, not just "to win"
2. **Penalties, not blocks** — reduce move weights, never remove options entirely
3. **Known > Inferred** — trust revealed abilities over Pokemon-commonly-has lists
4. **Severe for game-losing** — 0.1 weight for moves that actively help opponent
5. **One improvement per cycle** — small correct changes beat ambitious broken ones
6. **Tests must pass** — `python -m pytest tests/ -v` before every push
7. **Never modify protected files** — see `infrastructure/guardrails.json`
8. **Report quality matters** — every improvement should eventually make the morning report more useful
9. **Ground all Pokemon facts** — use `PokedexOracle` or data files, never LLM knowledge (see above)

## On Every Fresh Session

1. Read this file (CLAUDE.md)
2. Read TASKBOARD.md for current status and action items
3. Determine which machine you're on (DEKU or JIGGLYPUFF)
4. Check `git log --oneline -5` and `git status` for recent changes
5. Act on your highest-priority responsibility
6. Update TASKBOARD.md with what you did
7. Push your changes

## Upstream Base

Forked from [pmariglia/foul-play](https://github.com/pmariglia/foul-play) at commit `55fa9b4`.
- Upstream: `upstream` -> https://github.com/pmariglia/foul-play.git
- Origin: `origin` -> https://github.com/RJW34/fouler-play.git
- Branch: `master`

## Testing

```bash
python -m pytest tests/ -v                                              # Full suite
python -c "import ast; ast.parse(open('fp/search/main.py').read())"     # Syntax check
python -c "from fp.search.main import find_best_move; print('OK')"      # Import check
```
