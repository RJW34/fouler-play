# Fouler-Play Improvement Cycle

## Your Role

You are improving the **fouler-play** Pokemon Showdown battle bot. Your goal is to
make one targeted, high-impact improvement per cycle that increases the bot's win rate
in Gen 9 OU ladder play.

**Before doing anything else**, read the file `CLAUDE.md` in the repository root for
full project context, architecture details, and coding conventions.

## Current State

- **Current ELO**: {{CURRENT_ELO}}
- **New battles since last analysis**: {{NEW_BATTLES}}

## Team Performance Report

```
{{TEAM_REPORT}}
```

## Rules

1. **One targeted improvement per cycle.** Do not attempt sweeping refactors. Make a
   single focused change that addresses the highest-priority issue identified in the
   team report.

2. **Run tests before finishing.** Execute `python -m pytest tests/ --tb=short -q` and
   ensure all tests pass. If your change breaks a test, fix it or revert.

3. **Run syntax checks.** Every modified `.py` file must pass `python -m py_compile`.

4. **Never modify protected files.** Do not touch: `config.py`, `run.py`, `.env`,
   `CREDENTIALS.md`, or anything in `teams/`. Check `infrastructure/guardrails.json`
   for the full list.

5. **Explain your reasoning in the commit message.** The commit message should describe
   what you changed, why you changed it, and what improvement you expect.

6. **Be conservative.** A small correct improvement is better than an ambitious broken
   one. When in doubt, do less.

## Priority Order (4-Phase Roadmap)

Prioritize improvements in this order based on current ELO:

### Phase 1: Analytics + Tuning (target: 1350 -> 1450)
- Fix data quality issues (see technical hints below)
- Tune search parameters and playstyle weights
- Improve the replay analysis pipeline

### Phase 2: Bayesian Set Inference (target: 1450 -> 1550)
- Use speed_range to narrow opponent sets
- Implement Bayesian updating on revealed moves/items/abilities
- Weight MCTS simulations by set likelihood

### Phase 3: Switch Prediction (target: 1550 -> 1650)
- Use OpponentModel passive/sack tendencies
- Build switch prediction from type matchups + HP thresholds
- Incorporate predictions into search tree

### Phase 4: Archetype + Adaptive (target: 1650 -> 1700+)
- Classify opponent teams (HO/Balance/Stall)
- Adjust search weights by archetype
- Implement game-phase awareness

## Technical Hints (Known Issues)

These are known problems in the codebase that are ripe for fixing:

1. **`speed_range` exists but is unused.** The code calculates speed ranges for opponent
   Pokemon but never uses them to filter or weight possible sets. This is low-hanging
   fruit for Phase 2.

2. **`OpponentModel` passive/sack tendencies are unused.** The model tracks whether
   opponents tend to play passively or sack Pokemon, but the search does not use this
   information for switch prediction. This is key for Phase 3.

3. **25% TeamDatasets skip dilutes accuracy.** There is a 25% chance of skipping
   TeamDatasets lookup, which means 1 in 4 simulations uses generic/random sets instead
   of real competitive data. This directly hurts decision quality.

4. **Default EVs of 85 do not match real sets.** When EV spreads are unknown, the code
   defaults to 85 across the board. Real competitive sets use specific spreads (e.g.,
   252/252/4). This distorts damage calculations.

5. **Dummy Pikachu fill distorts MCTS.** When the opponent's team is incomplete, a dummy
   Pikachu is used to fill empty slots. This causes MCTS to evaluate against an
   unrealistic team composition, wasting simulation budget on impossible scenarios.

## Process

1. Read `CLAUDE.md` for full project context
2. Read `infrastructure/guardrails.json` to understand file permissions
3. Analyze the team performance report above
4. Identify the single highest-impact improvement given the current ELO and phase
5. Implement the change in the appropriate file(s)
6. Run tests and syntax checks
7. Provide a clear summary of what you changed and why
