# Fouler-Play Infrastructure: Two-Machine Autonomous Development Loop

## Architecture Overview

The fouler-play bot operates across two machines in a continuous improvement loop,
coordinated through GitHub (branch: `foulest-play`).

```
+---------------------------+          GitHub           +---------------------------+
|     WINDOWS MACHINE       |  <-- foulest-play -->     |      LINUX MACHINE        |
|                           |        branch             |                           |
|  - Plays ladder matches   |                           |  - Analyzes replays       |
|  - Streams to Twitch      |  battle_stats.json --->   |  - Identifies weaknesses  |
|  - Pushes replays + stats |                           |  - Generates code fixes   |
|  - Deploys code updates   |  <--- code changes        |  - Runs tests             |
|  - Monitors ELO           |                           |  - Commits improvements   |
+---------------------------+                           +---------------------------+
```

### Windows Machine (Player / Streamer)

Responsibilities:
- Runs the bot against the Pokemon Showdown ladder (`gen9ou`)
- Streams gameplay to Twitch via OBS
- After each batch of games, pushes `battle_stats.json` and replay files to GitHub
- Periodically checks for new code on `foulest-play` and deploys updates
- Runs the ELO watchdog to revert bad deploys

Key scripts:
- `infrastructure/windows/player_loop.bat` -- main loop
- `infrastructure/windows/deploy_update.bat` -- pulls and logs deploys
- `infrastructure/elo_watchdog.py` -- reverts if ELO drops too far

### Linux Machine (Developer / Analyst)

Responsibilities:
- Pulls latest battle data from `foulest-play`
- Runs `replay_analysis/team_performance.py` to generate performance reports
- Invokes Claude Code CLI with the analysis prompt and team report
- If Claude produces changes that pass syntax checks and tests, commits and pushes
- Sleeps, then repeats

Key scripts:
- `infrastructure/linux/developer_loop.sh` -- main loop
- `infrastructure/linux/analysis_prompt.md` -- prompt template for Claude Code

### GitHub as Coordination Layer

- Branch `foulest-play` is the live code branch. Both machines push to and pull from it.
- The Windows machine pushes data (stats, replays). The Linux machine pushes code.
- Merge conflicts are avoided because each machine writes to different files.
- `infrastructure/deploy_log.json` tracks every deploy event for audit and rollback.

---

## How Each Loop Works

### Windows Player Loop

```
1. git pull origin foulest-play
2. Run bot for {batch_size} games (default 10)
3. git add battle_stats.json replays/ && git commit && git push
4. git fetch origin foulest-play
5. If new commits from Linux machine: run deploy_update.bat
6. Run elo_watchdog.py to check for regressions
7. Go to step 1
```

### Linux Developer Loop

```
1. git pull origin foulest-play
2. Check if new entries exist in battle_stats.json since last analysis
3. If yes: run team_performance.py to generate report
4. Invoke Claude Code with analysis_prompt.md + report
5. If Claude's changes pass syntax check + tests: commit and push
6. Sleep for configured interval (default 30 minutes)
7. Go to step 1
```

---

## How to Start Each Machine

### Windows Machine

1. Open a terminal in the repo root (`C:\Users\mtoli\Documents\Code\fouler-play`)
2. Ensure `.env` has valid Pokemon Showdown credentials
3. Run:
   ```
   infrastructure\windows\player_loop.bat
   ```
4. (Optional) Start OBS for Twitch streaming separately

### Linux Machine

1. Clone the repo and checkout `foulest-play`
2. Ensure Claude Code CLI is installed and authenticated
3. Ensure Python dependencies are installed (`pip install -r requirements.txt`)
4. Run:
   ```bash
   chmod +x infrastructure/linux/developer_loop.sh
   ./infrastructure/linux/developer_loop.sh
   ```

---

## Safety Guardrails

All guardrails are defined in `infrastructure/guardrails.json`:

| Guardrail | Value | Description |
|---|---|---|
| `max_elo_drop_before_revert` | 50 | If ELO drops more than this after a deploy, auto-revert |
| `min_games_between_deploys` | 15 | Must play at least 15 games before accepting another deploy |
| `require_test_pass` | true | All tests must pass before a commit is pushed |
| `require_syntax_check` | true | Syntax check (`python -m py_compile`) must pass |

File-level guardrails:
- `allowed_modify`: Files Claude Code is permitted to change
- `never_modify`: Files that must never be touched (credentials, config, teams)

The ELO watchdog (`infrastructure/elo_watchdog.py`) runs after each deploy and can
automatically revert bad changes using `git revert`.

---

## 4-Phase Roadmap to 1700 ELO

### Phase 1: Analytics + Tuning (1350 -> 1450)

Focus: Fix data quality and tune existing search parameters.

Key tasks:
- Fix the 25% TeamDatasets skip that dilutes accuracy
- Replace default 85 EVs with real competitive spreads
- Remove dummy Pikachu fill that distorts MCTS evaluations
- Tune `playstyle_config.py` weights based on replay analysis
- Build the replay analysis pipeline (`replay_analysis/team_performance.py`)

### Phase 2: Bayesian Set Inference (1450 -> 1550)

Focus: Predict opponent sets more accurately using revealed information.

Key tasks:
- Use `speed_range` (exists but currently unused) to narrow down opponent sets
- Implement Bayesian updating: as moves/items/abilities are revealed, update set probabilities
- Weight MCTS simulations by set likelihood instead of uniform sampling
- Track and use common sets from Smogon usage stats

### Phase 3: Switch Prediction (1550 -> 1650)

Focus: Predict when and what the opponent will switch to.

Key tasks:
- Use `OpponentModel` passive/sack tendencies (exist but unused)
- Build switch prediction based on type matchups, HP thresholds, and opponent tendencies
- Incorporate switch predictions into search tree (double-weight predicted switches)
- Punish predicted switches with coverage moves or hazard setters

### Phase 4: Archetype + Adaptive Play (1650 -> 1700+)

Focus: Recognize team archetypes and adapt strategy mid-game.

Key tasks:
- Classify opponent teams as HO/Balance/Stall within first 2-3 turns
- Adjust search weights based on archetype (e.g., against stall: prioritize wallbreaking)
- Implement game-phase awareness (early/mid/endgame strategy shifts)
- Dynamic team selection based on recent opponent distribution
