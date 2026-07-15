# Fouler-Play Infrastructure: Two-Machine Autonomous Development Loop

## Architecture Overview

The fouler-play bot operates across two machines in a continuous improvement loop,
coordinated through GitHub (branch: `master`).

```
+---------------------------+          GitHub           +---------------------------+
|     WINDOWS MACHINE       |  <-- master -->     |      LINUX MACHINE        |
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
- Runs only a clean release named by a deployment receipt and finite runtime lease
- Creates exact-identity activation/judgment proof before another engine change is eligible

Key scripts:
- `scripts/devstream_session.py` -- finite leased supervisor/runtime lifecycle
- `scripts/fouler_deployment_receipt.py` -- clean immutable release authorization
- `scripts/fouler_deployment_state.py` -- activation/current-state/judgment operator
- `infrastructure/elo_watchdog.py` -- exact-identity immutable judgment writer

### Linux Machine (Developer / Analyst)

Responsibilities:
- Pulls latest battle data from `master`
- Runs `replay_analysis/team_performance.py` to generate performance reports
- Invokes the current coding-agent/runtime with the analysis prompt and team report
- If the agent produces changes that pass syntax checks and tests, commits and pushes
- Sleeps, then repeats

Key scripts:
- `infrastructure/linux/developer_loop.sh` -- main loop
- `infrastructure/linux/analysis_prompt.md` -- prompt template for the coding agent / reasoning runtime

### GitHub as Coordination Layer

- Git carries reviewed code and durable source history; a push is not a deployment.
- JIGGLYPUFF runs an immutable release directory for one exact pushed commit.
- Runtime authority lives under `%PROGRAMDATA%\HERMES\state\fouler\deployments` as
  deployment, activation, and judgment receipts rather than `deploy_log.json`.

---

## How Each Loop Works

### Windows Player Loop

```
1. Validate the clean release receipt and finite DEKU-signed v3 runtime lease.
2. Run one bounded three-slot battle batch under the lease identity.
3. Ensure the first exact-identity row activates the release/session.
4. Refresh replay/autoresearch proof without posting per-battle Discord noise.
5. Judge after 30 exact-identity decisive battles; continue sampling while pending.
6. Permit one candidate only after a passing immutable judgment.
7. Deploy an accepted candidate or rollback as a new separately authorized release.
```

### Linux Developer Loop

```
1. git pull origin master
2. Check if new entries exist in battle_stats.json since last analysis
3. If yes: run team_performance.py to generate report
4. Invoke the current coding agent with analysis_prompt.md + report
5. If the agent's changes pass syntax check + tests: commit and push
6. Sleep for configured interval (default 30 minutes)
7. Go to step 1
```

---

## How to Start Each Machine

### Windows Machine

Do not run `infrastructure\windows\player_loop.bat`; it is a legacy mutable-checkout loop.
Provision a clean release, deployment receipt, and finite `jigglypuff-runtime-start` lease, then
invoke `scripts\devstream_session.py start --continuous --execute --runtime-lease <path>` through
the validated supervisor task. OBS remains a separately verified output gate.

### Linux Machine

The old `infrastructure/linux/developer_loop.sh` is not runtime authority. HERMES may prepare one
bounded candidate only after the current JIGGLY activation has a passing judgment and the external
H2H attempt authority validates. Accepted source still requires a new immutable JIGGLY deployment.

---

## Reporting / proof contract

This infrastructure should be operated under `D:\deku-workspace\docs\TEAM_REPORTING_CONTRACT.md`.

Implications for Fouler Play ops:
- Normal project reporting stays in Discord channel `1466691161363054840`; do not tag `@Ryan` unless a true `ESCALATION` condition is met.
- Process-up/log-up is not enough to call the service healthy; useful proof should include battle_stats movement, replay output, deploy/test evidence, or clear before/after recovery evidence.
- If the overnight loop is alive but produces no new battle/reporting outcome for an abnormal interval, emit `STAGNATION` instead of healthy.
- Auto-revert / recovery paths should report `RECOVERY_ATTEMPT` and `RECOVERY_RESULT`, and use `ESCALATION` only when autonomous recovery has actually failed or a policy boundary is hit.

## Safety Guardrails

All guardrails are defined in `infrastructure/guardrails.json`:

| Guardrail | Value | Description |
|---|---|---|
| `max_elo_drop_before_revert` | 50 | Regression threshold recorded in immutable judgment proof |
| `min_games_between_deploys` | 30 | Exact-identity decisive games required before another candidate |
| `require_test_pass` | true | All tests must pass before a commit is pushed |
| `require_syntax_check` | true | Syntax check (`python -m py_compile`) must pass |

File-level guardrails:
- `allowed_modify`: Files the coding agent is permitted to change
- `never_modify`: Files that must never be touched (credentials, config, teams)

The ELO watchdog (`infrastructure/elo_watchdog.py`) never edits the live release. A
regressed judgment blocks continuation until a separately authorized rollback or replacement
release is activated.

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
