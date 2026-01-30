# Fat Team Analysis - Top Player Request

**Goal:** Get bot to 1700+ ELO for team testing vs strong players

## Team Breakdown

### Team 1: "Gliscor Gholdengo Stall"
**Archetype:** Fat Balance / Semi-Stall
**Core Strategy:** Hazard stacking + status spreading + setup sweeping

**Key Pokemon:**
- **Gliscor** - Physical wall + SD sweeper (Poison Heal)
- **Gholdengo** - Special wall + NP sweeper + status (Good as Gold)
- **Zamazenta** - Fast breaker (Dauntless Shield)
- **Blissey** - Special wall + CM sweeper + hazards
- **Skarmory** - Physical wall + phazer + Spikes
- **Pecharunt** - Defensive pivot + Toxic spreader

**Win Conditions:**
1. Stack hazards (Spikes + Stealth Rock)
2. Spread status (Toxic, Thunder Wave)
3. Phaze setup sweepers (Whirlwind)
4. Set up late-game with Gliscor/Gholdengo/Blissey
5. Outlast opponent through recovery

---

### Team 2: "Pivot-Heavy Fat"
**Archetype:** Bulky Offense / Balance
**Core Strategy:** Pivot cycling + chip damage + hazards

**Key Pokemon:**
- **Gliscor** - Physical wall + Spikes + Toxic
- **Ogerpon** - Defiant sweeper + Encore + pivot
- **Walking Wake** - Special attacker + Sub staller
- **Blissey** - Special wall + CM + hazards
- **Corviknight** - Physical wall + pivot + Pressure stall
- **Pecharunt** - Defensive pivot + status

**Win Conditions:**
1. U-turn/Parting Shot cycling for chip + momentum
2. Hazard damage accumulation
3. Ogerpon Defiant punish + sweep
4. Walking Wake Sub stalling
5. Blissey CM late-game

---

### Team 3: "Dondozo Unaware Balance"
**Archetype:** Fat Balance
**Core Strategy:** Unaware + Regenerator cores + slow wear-down

**Key Pokemon:**
- **Corviknight** - Defog + pivot (Rocky Helmet chip)
- **Ting-Lu** - Special wall + RestTalk (Vessel of Ruin)
- **Kyurem** - Sub/Protect staller (Pressure)
- **Dondozo** - Unaware wall + Curse sweeper
- **Slowking-Galar** - Regenerator pivot + Future Sight
- **Cinderace** - Fast pivot + utility (Court Change)

**Win Conditions:**
1. Regenerator pivot cycling (Slowking-G, Corviknight passive healing)
2. Kyurem Sub/Protect PP stalling
3. Dondozo Curse setup + sweep
4. Future Sight + pivot damage
5. Court Change hazard control

---

## Common Themes Across All 3 Teams

### Defensive
- 4-5 defensive mons per team
- Heavy recovery (Roost, Recover, Soft-Boiled, Rest, Poison Heal, Regenerator)
- Thick physical AND special walls
- Heavy-Duty Boots on most mons (hazard immunity)

### Momentum Control
- Pivot moves everywhere (U-turn, Parting Shot, Chilly Reception)
- Switch-heavy playstyle
- Maintain favorable matchups constantly

### Chip Damage
- Hazards (Spikes, Stealth Rock)
- Status (Toxic, Thunder Wave, Will-O-Wisp)
- Rocky Helmet + contact punishment
- Future Sight delayed damage

### Late-Game Setup
- Multiple setup sweepers (Swords Dance, Nasty Plot, Calm Mind, Curse)
- Only set up when opponent is weakened
- Use phazers to prevent opponent setup

---

## Bot Heuristics Needed for Fat Teams

### 1. **Switch Aggression** (CRITICAL)
- **Current:** Bot probably switches too little
- **Needed:** Switch early and often to maintain favorable matchups
- **Example:** Don't stay in on bad matchups trying to "trade damage"

### 2. **Recovery Timing**
- **Current:** May undervalue recovery moves
- **Needed:** Recover aggressively to maintain defensive backbone
- **Example:** Roost at 60% HP, not 20%

### 3. **Hazard Priority**
- **Current:** Generic hazard value
- **Needed:** Prioritize hazards VERY highly on these teams
- **Example:** Getting Spikes up early is more valuable than dealing 30% damage

### 4. **Setup Patience**
- **Current:** May setup too early
- **Needed:** Wait for safe setup opportunities
- **Example:** Don't Swords Dance Gliscor when they have 4 healthy mons

### 5. **Pivot Intelligence**
- **Current:** May not understand pivot value
- **Needed:** Use U-turn/Parting Shot to scout and maintain momentum
- **Example:** U-turn even when you're in a good matchup if it gains info

### 6. **Unaware Awareness** (Team 3 specific)
- **Current:** Probably doesn't know Unaware ignores boosts
- **Needed:** Never waste setup vs Dondozo/Quagsire/etc.

### 7. **Regenerator Value**
- **Current:** May not factor passive healing into switches
- **Needed:** Switch Regenerator mons freely for 33% healing
- **Example:** Pivot Slowking-G even at 50% HP for free recovery

### 8. **Pressure Stalling** (Kyurem Sub/Protect)
- **Current:** Doesn't understand PP stalling
- **Needed:** Sub/Protect cycling to drain opponent PP
- **Example:** Kyurem vs low PP move = stall them out

---

## Implementation Priority

### Phase 1: Core Fat Heuristics (This Week)
- [ ] Reduce switch penalty for defensive teams
- [ ] Increase hazard value multiplier
- [ ] Boost recovery move scoring
- [ ] Add pivot move awareness
- [ ] Implement Unaware detection

### Phase 2: Advanced Strategy (Next Week)
- [ ] Setup opportunity detection
- [ ] Regenerator healing calculations
- [ ] Pressure stalling logic
- [ ] Phazing vs setup mons

### Phase 3: Team-Specific Tuning (Week 3)
- [ ] Test each team on ladder individually
- [ ] Track ELO by team archetype
- [ ] Fine-tune weights based on replay analysis
- [ ] Get feedback from top player

---

## Success Metrics

- **Target ELO:** 1700+
- **Win Rate:** 55%+ vs 1600+ players
- **Team Testing:** Accurate performance evaluation vs meta threats
- **Speed:** Faster iteration than human testing (2-3 games/hour)

---

**Next Steps:**
1. Convert teams to bot format
2. Implement Phase 1 heuristics
3. Start ladder testing
4. Collect loss replays for analysis
5. Iterate based on data + top player feedback
