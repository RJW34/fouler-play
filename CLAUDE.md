# CLAUDE.md - Fouler-Play Autonomous Operations Manual

## Mission

Reach **1700 ELO in gen9ou** on Pokemon Showdown. Bot account: "ALL CHUNG". Current: ~1141. Do not stop until 1700.

## You Own Your Machine

This project runs on **two machines**. When you start a session, determine which machine you are on and act accordingly. You own everything on your machine — code, services, streaming, display, environment. If something is broken, fix it. Do not wait for the human or the other machine.

### How to identify your machine
- Run `uname -s` or check `$OSTYPE`. Linux = **DEKU**. Windows / `MSYS` / `MINGW` = **BAKUGO**.
- Or check the hostname: `hostname`.

---

## DEKU (Linux) — Brains

**You own:** Code analysis, strategy improvements, replay analysis, developer loop, test suite, upstream merges.

### Your loop: `infrastructure/linux/developer_loop.sh`
Runs continuously. Pulls battle data → analyzes performance → generates reports. Install as a persistent service:
```bash
bash infrastructure/linux/install_service.sh
systemctl --user start fouler-play
```

### Your responsibilities (in priority order)
1. **Phase 2-4 roadmap items** — see TASKBOARD.md. Bayesian set inference, switch prediction, archetype classification. Implement based on current ELO bracket. This is the main work to be done.
2. **Replay analysis improvements** — `replay_analysis/` has a working pipeline (`team_performance.py`, `analyzer.py`, `turn_review.py`). Improve the analysis quality, not rebuild it.
3. **Fix anything broken** — if tests fail, if imports break, if the developer loop crashes, diagnose and fix. Use sub-agents (`claude -p` or the Task tool) to parallelize research and implementation.
4. **Push to GitHub** — all code goes to `foulest-play` branch. Update TASKBOARD.md when completing items. Never modify files in the `never_modify` list in `infrastructure/guardrails.json`.

### Sub-agent pattern (DEKU)
When you have multiple independent tasks, spawn sub-agents to work in parallel. Use the Task tool or `claude -p "prompt" --allowedTools "Edit,Write,Read,Bash,Glob,Grep"` for headless sub-agents.

### Coordinating with BAKUGO
- Push code to `foulest-play`. BAKUGO pulls automatically in their player loop.
- If BAKUGO needs to do something specific (install a dependency, restart OBS, change .env), write instructions in `TASKBOARD.md` under "BAKUGO Action Items" and push.
- Read `battle_stats.json` (pushed by BAKUGO) for performance data.

---

## BAKUGO (Windows) — Brawn

**You own:** Bot operation, battle data collection, OBS streaming, Twitch display, poke-engine builds, ELO monitoring, and everything visual.

### Your loop: `infrastructure/windows/player_loop.bat`
Runs continuously. Pulls latest code → plays a batch of games → pushes stats/replays → checks for new deploys. Install as a persistent scheduled task:
```
Run as Administrator: infrastructure\windows\install_task.bat
schtasks /run /tn "FoulerPlayPlayerLoop"
```

### Your responsibilities (in priority order)
1. **Keep the bot playing** — the player loop must be running at all times. If it crashes, diagnose why and fix. Check logs in the repo root.
2. **OBS + Twitch streaming** — the streaming pipeline is BUILT and WORKING. Do NOT rebuild it. The architecture is:
   - `streaming/serve_obs_page.py` — Main HTTP + WebSocket server (port 8777). Controls OBS Browser Sources via obs-websocket. Start with: `python streaming/serve_obs_page.py`
   - `streaming/obs_websocket.py` — OBS WebSocket client that sets Browser Source URLs
   - `streaming/state_store.py` — Reads/writes `active_battles.json` and `stream_status.json`
   - `streaming/obs_controller.py` — Higher-level OBS control (source management)
   - `streaming/overlay.html` — ELO display, win/loss counter, team info overlay
   - `streaming/obs_battles.html` — Battle display page
   - `streaming/obs_idle.html` — Idle screen between battles
   - `fp/run_battle.py` writes to `active_battles.json` whenever battles start/end/update (~15 hook points)
   - **Data flow:** bot plays → `run_battle.py` writes `active_battles.json` → `serve_obs_page.py` reads it → OBS Browser Source displays it
   - OBS Browser Sources must be named to match `.env` → `OBS_BATTLE_SOURCES=Battle Slot 1,Battle Slot 2,Battle Slot 3`
   - Battle URLs use format: `https://play.pokemonshowdown.com/battle-gen9ou-XXXXXXX` — **never** use `~~showdown` in URLs
   - obs-websocket (port 4455) lets you programmatically set Browser Source URLs
   - If something in streaming is broken, **diagnose and fix it** — do not rewrite from scratch.
3. **Environment setup** — your `.env` file (not tracked by git) must have all variables from `.env.example`. Critical streaming vars:
   ```
   OBS_WS_HOST=localhost
   OBS_WS_PORT=4455
   OBS_BATTLE_SOURCES=Battle Slot 1,Battle Slot 2,Battle Slot 3
   ```
4. **poke-engine builds** — if DEKU pushes code that updates poke-engine version, you need Rust toolchain installed to rebuild. `pip install -e .` or `pip install poke-engine==X.X.X`.
5. **ELO monitoring** — `infrastructure/elo_watchdog.py` runs after deploys. If ELO drops >50 from a deploy, it auto-reverts. Make sure this is working.
6. **Push battle data** — after each batch, `battle_stats.json` and `replays/` get committed and pushed to `foulest-play`.

### Sub-agent pattern (BAKUGO)
When you have multiple independent tasks, spawn sub-agents to work in parallel.

### Coordinating with DEKU
- Push battle data (stats, replays) to `foulest-play`. DEKU pulls in their developer loop.
- Check `TASKBOARD.md` for any "BAKUGO Action Items" that DEKU has written.
- If you find a bug in the bot's decision-making, note it in TASKBOARD.md under "Bug Reports" and push. DEKU will fix it.

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
├── constants.py              # Shim → re-exports from constants_pkg/
├── constants_pkg/            # Penalty/boost values, ability sets, move flags
├── fp/
│   ├── battle.py             # Battle/Battler/Pokemon state
│   ├── battle_modifier.py    # Parses PS protocol, updates battle state
│   ├── run_battle.py         # Main battle loop + streaming hooks
│   ├── websocket_client.py   # PS websocket + multi-battle routing
│   ├── search/
│   │   ├── main.py           # MCTS + ability penalty system (core logic)
│   │   ├── endgame.py        # Endgame solver
│   │   ├── standard_battles.py  # Battle sampling + weighted selection
│   │   └── move_validators.py   # Move validation
│   ├── team_analysis.py      # Win condition identification
│   ├── opponent_model.py     # Opponent tendency tracking
│   ├── playstyle_config.py   # Per-team playstyle tuning
│   └── decision_trace.py     # Decision logging
├── infrastructure/
│   ├── guardrails.json       # File permissions + safety thresholds
│   ├── elo_watchdog.py       # Auto-revert bad deploys
│   ├── linux/                # DEKU's scripts + service files
│   └── windows/              # BAKUGO's scripts + task installer
├── streaming/                # OBS/Twitch integration (BUILT — do not rebuild)
│   ├── serve_obs_page.py     # Main server (port 8777) — the entry point
│   ├── obs_websocket.py      # OBS WebSocket client
│   ├── state_store.py        # active_battles.json / stream_status.json I/O
│   ├── obs_controller.py     # OBS source management
│   ├── overlay.html          # Stats overlay for OBS
│   ├── obs_battles.html      # Battle display page
│   └── obs_idle.html         # Idle screen
├── replay_analysis/          # Performance analysis (BUILT — do not rebuild)
│   ├── team_performance.py   # Per-team win rates and weakness analysis
│   ├── analyzer.py           # Battle replay analyzer
│   ├── turn_review.py        # Turn-by-turn analysis
│   └── reports/              # Generated analysis reports
├── data/                     # Smogon data, pokedex, moves
├── teams/                    # Team files (NEVER MODIFY)
└── tests/                    # Test suite
```

## Key Design Principles

1. **Penalties, not blocks** — reduce move weights, never remove options entirely
2. **Known > Inferred** — trust revealed abilities over Pokemon-commonly-has lists
3. **Severe for game-losing** — 0.1 weight for moves that actively help opponent
4. **One improvement per cycle** — small correct changes beat ambitious broken ones
5. **Tests must pass** — `python -m pytest tests/ -v` before every push
6. **Never modify protected files** — see `infrastructure/guardrails.json`

## On Every Fresh Session

1. Read this file (CLAUDE.md)
2. Read TASKBOARD.md for current status and action items
3. Determine which machine you're on (DEKU or BAKUGO)
4. Check `git log --oneline -5` and `git status` for recent changes
5. Act on your highest-priority responsibility
6. Update TASKBOARD.md with what you did
7. Push your changes

## Upstream Base

Forked from [pmariglia/foul-play](https://github.com/pmariglia/foul-play) at commit `55fa9b4`.
- Upstream: `upstream` → https://github.com/pmariglia/foul-play.git
- Origin: `origin` → https://github.com/RJW34/fouler-play.git
- Branch: `foulest-play`

## Testing

```bash
python -m pytest tests/ -v                                              # Full suite
python -c "import ast; ast.parse(open('fp/search/main.py').read())"     # Syntax check
python -c "from fp.search.main import find_best_move; print('OK')"      # Import check
```
