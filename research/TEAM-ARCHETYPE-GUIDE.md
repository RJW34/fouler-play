# Team Archetype Classification Guide

## Why This Matters

Not all high-elo strategies apply to all team styles. A hyper-offense team plays completely differently than a stall/fat team. Learning from mismatched archetypes teaches the bot strategies it can't execute.

**Solution:** Filter observed games by team archetype. Only learn from games where at least one player uses a team similar to ours.

## Archetype Definitions

### Fat / Bulky Teams
**Core Philosophy:** Outlast, wear down, punish mistakes

**Common Pokemon:**
- Blissey, Chansey (special walls)
- Corviknight, Skarmory (physical walls)
- Toxapex, Clodsire (hazard setters + status spreaders)
- Ferrothorn, Gastrodon (defensive pivots)
- Ting-Lu, Garganacl (bulky hazard control)
- Dondozo, Alomomola (physical tanks)
- Gliscor, Hippowdon (ground-type walls)

**Win conditions:**
- Residual damage (Toxic, Stealth Rock, Spikes)
- PP stalling
- Safe switches and chip damage
- Late-game sweeps after opponent is weakened

### Hyper Offense
**Core Philosophy:** Fast, hit hard, sweep early

**Common Pokemon:**
- Dragapult, Iron Valiant (fast sweepers)
- Great Tusk, Roaring Moon (offensive powerhouses)
- Volcarona, Gholdengo (setup sweepers)
- Kingambit, Baxcalibur (late-game finishers)
- Iron Moth, Greninja (special attackers)

**Win conditions:**
- Early momentum
- Setup sweepers (Swords Dance, Nasty Plot)
- Revenge killing with priority
- No defensive backbone - all-in pressure

### Balanced / Semi-Stall
**Middle ground:** Some bulk, some offense

**Mix of:** 
- 2-3 defensive cores
- 2-3 offensive threats
- Flexible playstyle

## How Classification Works

**Fat Team Detection:**
```javascript
// Team is classified as "fat" if:
fatRatio >= 0.5  (50% or more fat Pokemon)

// "Semi-fat" if:
fatRatio >= 0.33  (33-49% fat Pokemon)
```

**Example:**
```
Team: Corviknight, Blissey, Toxapex, Landorus, Great Tusk, Gholdengo
Fat Pokemon: Corviknight, Blissey, Toxapex = 3/6 = 50%
Classification: FAT ✅
```

```
Team: Dragapult, Iron Valiant, Volcarona, Kingambit, Roaring Moon, Great Tusk
Fat Pokemon: 0/6 = 0%
Classification: HYPER OFFENSE ❌ (not relevant to our bot)
```

## Learning Value Tags

Every saved game gets tagged with its learning value:

### `WIN_WITH_OUR_ARCHETYPE` 
- A fat team won the game
- **Learn:** What moves/switches led to victory
- **Apply:** Prioritize these patterns in bot

### `LOSS_WITH_OUR_ARCHETYPE`
- A fat team lost the game
- **Learn:** What mistakes were made
- **Apply:** AVOID these patterns in bot

### `IRRELEVANT_ARCHETYPE`
- Neither team was fat
- **Learn:** Nothing (different playstyle)
- **Apply:** Ignore this game

## Why Learn from Losses?

**Traditional approach:** Only study wins
**Problem:** Misses critical mistakes that fat teams make

**Enhanced approach:** Study both wins AND losses

**Example insights from losses:**
- "Corviknight using Brave Bird early-game appears in 5 losses vs 2 wins" → Avoid
- "Blissey leading with Seismic Toss loses 75% of games" → Don't do this
- "Early switches correlate with losses for fat teams" → Stay in more

**The bot can now learn:**
1. What works (winning patterns)
2. What doesn't work (losing patterns)
3. Common traps (mistakes that lose games)

## Implementation

### Observer
```javascript
// Classifies teams before saving
const teams = TeamClassifier.extractTeams(battleData);
const p1MatchesArchetype = TeamClassifier.matchesArchetype(teams.p1Team, 'fat');

if (p1MatchesArchetype && winner === p1) {
  battleData.learningValue = 'WIN_WITH_OUR_ARCHETYPE';
} else if (p1MatchesArchetype && winner !== p1) {
  battleData.learningValue = 'LOSS_WITH_OUR_ARCHETYPE';
}
```

### Analyzer
```javascript
// Separate win and loss patterns
for (const game of relevantGames) {
  const isWin = game.learningValue === 'WIN_WITH_OUR_ARCHETYPE';
  const isLoss = game.learningValue === 'LOSS_WITH_OUR_ARCHETYPE';
  
  if (isWin) {
    winningPatterns[move]++;
  } else if (isLoss) {
    losingPatterns[move]++;
  }
}

// Compare: moves with high loss rate = avoid
```

## Adjusting the Archetype

Our bot's archetype is set in `high-elo-observer.js`:

```javascript
const BOT_ARCHETYPE = 'fat'; // Change to 'offense' or 'balanced' if needed
```

If the bot's team changes, update this constant and restart the observer.

## Future Enhancements

- [ ] Sub-archetype detection (stall vs balance-fat vs offense-fat)
- [ ] Detect team cores (Corviknight + Toxapex + Blissey = fat core)
- [ ] Identify team synergies (which combos win most?)
- [ ] Track meta shifts (which fat mons are winning this month?)
- [ ] Auto-adjust archetype based on bot's current team

---

**Key Insight:** Don't just learn from winners. Learn from losers playing YOUR style. That's where the real lessons are.
