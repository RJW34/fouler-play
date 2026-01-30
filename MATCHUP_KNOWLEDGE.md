# Matchup-Specific Knowledge

**Purpose:** Document specific Pokemon matchup facts that aren't obvious from stats alone

## Gliscor

### Move Pool Reality
**Gliscor is PURELY PHYSICAL** - never runs special attacks

**Standard moves:**
- Earthquake (physical Ground)
- Facade (physical Normal, 140 BP when statused)
- Knock Off (physical Dark, removes items)
- Protect
- Swords Dance
- Toxic
- U-turn

**Key insight:** Special bulk is IRRELEVANT vs Gliscor. Only physical defense matters.

### Switch Decisions vs Gliscor

**Bad reasoning:** "Switch to Gholdengo for better special bulk"
- ❌ Gliscor has NO special attacks
- ❌ Special bulk doesn't help at all

**Good reasoning:** "Switch Zamazenta out to avoid Toxic/status"
- ✅ Gliscor often runs Toxic
- ✅ Zamazenta hates being statused
- ✅ Preserving Zamazenta's health/status is valuable

**Correct switch targets:**
- **Skarmory** - Physical wall, immune to Ground (Flying type), resists Knock Off
- **Pecharunt** - Good physical bulk, status immunity via ability
- **NOT Gholdengo** - Special bulk irrelevant, weak to Ground

### Bot Implementation

```python
# Gliscor matchup knowledge
GLISCOR_MOVES = {
    "physical": ["earthquake", "facade", "knockoff", "uturn"],
    "status": ["toxic", "protect", "swordsdance"],
    "special": []  # NONE - purely physical
}

# When evaluating switch vs Gliscor
if opponent_active.name == "Gliscor":
    # Special bulk is irrelevant
    special_defense_bonus = 0
    
    # Physical bulk matters
    if switch_target.defense > current_mon.defense:
        switch_score += 30
    
    # Type immunity matters (Flying, Ghost immune to Ground/Normal)
    if switch_target.immune_to("Ground"):
        switch_score += 50
    
    # Status avoidance matters
    if current_mon.is_key_sweeper and not current_mon.statused:
        switch_score += 40  # Don't let Gliscor Toxic our win condition
```

---

## General Lesson: Movepool Matters More Than Stats

**The mistake:** Assuming Pokemon use all move types just because they CAN

**The reality:** Most Pokemon specialize

### Physical-Only Threats
- Gliscor
- Garchomp
- Landorus-Therian
- Great Tusk
- Kingambit

### Special-Only Threats
- Gholdengo (usually)
- Heatran
- Primarina
- Iron Valiant (usually)

### Mixed Threats (use both)
- Zamazenta (mostly physical, but can have Ice Fang)
- Dragapult
- Kyurem

**Bot must learn:** Track actual movepool, not theoretical movepool

---

## Status Avoidance Priority

**High-value targets to keep clean:**
- Sweepers (Zamazenta, Gliscor after SD)
- Setup mons before they set up
- Mons with status-triggered abilities

**Low-priority status protection:**
- Walls that can heal (Blissey Softboiled clears status via Natural Cure)
- Already-statused mons
- Mons with Guts/status-immune abilities

**Gliscor specifically:**
- Poison Heal makes it IMMUNE to Toxic
- Actually WANTS to be poisoned (via Toxic Orb)
- Other status (burn, paralysis) still bad

---

## Action Items

1. **Build movepool database** - Track what moves each Pokemon actually uses
2. **Update switch logic** - Don't value irrelevant defensive stats
3. **Status value tracking** - Prioritize keeping key mons clean
4. **Matchup knowledge base** - Document more specific matchups like this

---

**Lesson from Ryan's feedback:**
"Gholdengo special bulk doesn't matter vs Gliscor - Gliscor is purely physical. Switch to avoid Toxic, not for bulk matchup."

This is the kind of specific knowledge that separates good players from bots!
