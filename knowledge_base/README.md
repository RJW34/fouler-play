# Fouler Play Knowledge Base

Structured domain knowledge separated from decision logic, inspired by Melee Replay Analyzer's architecture.

## Purpose

Separate **what we know about Pokemon** (facts, data, patterns) from **how we make decisions** (algorithms, scoring).

## Benefits

1. **Domain Expert Editable** - Non-programmers can update move effects, matchup knowledge
2. **Version Controlled** - Track changes to battle knowledge over time
3. **Clear Separation** - Facts vs logic, data vs code
4. **Extensible** - Easy to add new knowledge types

## Structure

```
knowledge_base/
├── moves/          # Move data, effects, interactions
├── abilities/      # Ability mechanics, immunities, triggers
├── items/          # Item effects, common holders
├── matchups/       # Type matchup patterns, common counters
├── strategies/     # Setup sweepers, stall patterns, hyper offense
└── situations/     # Battle phases, positions, conditions
```

## Usage Pattern

```python
from knowledge_base import moves, matchups

# Load move data
move_data = moves.get("psychic")
# { power: 90, type: "psychic", effects: [{spdef_drop: 0.1}] }

# Load matchup knowledge
matchup = matchups.get("psychic_vs_fighting")
# { effectiveness: 2.0, common_switches: ["dark", "psychic"] }
```

## Implementation Status

- [x] Directory structure created
- [ ] YAML schema definitions
- [ ] Move database
- [ ] Ability database
- [ ] Matchup patterns
- [ ] Python loader utilities

## Inspiration

Based on Melee Replay Analyzer's knowledge base architecture:
- Frame data → Move data
- Punish trees → Matchup patterns  
- Situation taxonomy → Battle phases
