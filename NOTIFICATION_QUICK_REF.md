# Watcher Notification Quick Reference

## Reading a Notification (30-Second Guide)

### 1️⃣ Check Summary (Top Embed)
```
Record: 18-12 (60% WR)
Team Performance:
  Stall: 8-2 (80%)   ← crushing it
  Pivot: 5-5 (50%)   ← weak link
  Dondozo: 5-5 (50%)
```
**Action:** Identify weakest team (focus fixes there)

### 2️⃣ Scan Issues (By Badge)
```
🟢 = Free wins (auto-apply)
🟡 = Medium effort (review)
🔴 = Long-term project (defer)
```
**Action:** Read 🟢 issues first (highest ROI)

### 3️⃣ Verify Impact
```
Impact: Affects 60% of losses (8/12 battles)
Teams: Pivot: 5 losses, Dondozo: 3 losses
Examples: [805111] • [810279] • [815551]
```
**Action:** Click 1-2 example replays to confirm

### 4️⃣ Review Fix
```
💻 Suggested Fix
- Ability: Regenerator
+ Ability: Heavy Duty Boots
```
**Action:** Sanity check (does this make sense?)

### 5️⃣ Decide
```
✅ Will auto-apply next cycle (react 🛑 to block)
⚠️ Needs manual review before applying
```
**Action:** 
- Auto-apply → Let it run (or 🛑 to veto)
- Manual → Assign to work queue or DEKU

---

## Badge Meanings

### 🟢 Easy/High Impact
**What:** Team composition, items, abilities, movesets
**Risk:** Low (no code changes)
**Action:** Let auto-apply (veto if wrong)
**Examples:**
- Add hazard removal
- Swap item to Heavy Duty Boots
- Include Defog in moveset

### 🟡 Medium Effort
**What:** Logic tweaks, thresholds, heuristics
**Risk:** Medium (could affect multiple battles)
**Action:** Review code diff, test manually
**Examples:**
- Adjust switch penalty threshold
- Prioritize coverage moves in endgame
- Increase/decrease aggression scoring

### 🔴 Hard/Low Impact
**What:** Major refactors, new systems, algorithms
**Risk:** High (could break existing logic)
**Action:** Add to long-term roadmap
**Examples:**
- Implement momentum tracking
- Refactor eval engine
- Add opponent modeling system

---

## When to Veto Auto-Apply

React 🛑 to an auto-apply if:

1. **Meta knowledge** — You know this team matchup is uncommon
2. **Sample size** — Only 2-3 examples (might be variance)
3. **Alternative fix** — You have a better solution in mind
4. **Team identity** — Fix breaks the archetype (e.g., stall → offense)
5. **Testing needed** — Want to A/B test before committing

**Default:** Trust the data (most 🟢 fixes are valid)

---

## Impact Percentage Guidelines

- **>50%** — Critical issue (affects majority of losses)
- **30-50%** — Significant weakness (high priority)
- **15-30%** — Moderate impact (medium priority)
- **<15%** — Minor issue (low priority or variance)

**Note:** Percentages relative to THIS BATCH (30 games), not all-time

---

## Team Performance Interpretation

```
Stall: 8-2 (80%)   ← Working great, minor tweaks
Pivot: 5-5 (50%)   ← Needs help, focus fixes here
Dondozo: 3-7 (30%) ← Major problems, consider rebuild
```

**Action Priority:**
1. Fix teams with WR 40-60% (salvageable with tweaks)
2. Preserve teams with WR >70% (don't break what works)
3. Rebuild teams with WR <40% (fundamental issues)

---

## Example Replay Usage

**Click the replay links to:**

1. **Verify the issue** — Does hazard damage actually kill us?
2. **Spot the turn** — When did the game become unwinnable?
3. **Check alternatives** — Could we have switched/attacked instead?
4. **Meta insights** — Is this a common strategy we'll face?

**Pro tip:** Watch replays at 2x speed, focus on critical turns

---

## Code Diff Reading

### Team Composition (Safe)
```diff
# teams/fat-team-2-pivot.txt
- Corviknight @ Leftovers
+ Corviknight @ Heavy Duty Boots
```
**Risk:** None (just item swap)

### Moveset (Low Risk)
```diff
- Toxic
+ Defog
```
**Risk:** Low (trades coverage for utility)

### Battle Logic (Medium Risk)
```python
+ if threat.has_boost('defense'):
+     return self.get_special_attackers()
```
**Risk:** Medium (could affect multiple matchups)

### Algorithm (High Risk)
```python
- score = eval_position(state)
+ score = eval_with_momentum(state, history)
```
**Risk:** High (changes core decision-making)

---

## Common Fix Types

### Hazard Management
```
Issue: Team has no hazard removal
Fix: Add Defog/Rapid Spin or Heavy Duty Boots
Badge: 🟢 Easy/High
Impact: Usually 40-60% of losses
```

### Coverage Gaps
```
Issue: Team loses to Steel/Fairy/Dragon types
Fix: Add coverage move (Earthquake, Flamethrower, etc.)
Badge: 🟢 Easy/High
Impact: Usually 20-40% of losses
```

### Over-Switching
```
Issue: Bot switches when it should attack
Fix: Adjust switch penalty or momentum tracking
Badge: 🟡 Medium or 🔴 Hard
Impact: Usually 15-30% of losses
```

### Speed Ties
```
Issue: Bot loses 50/50 speed ties consistently
Fix: Adjust speed calculation or tie-break logic
Badge: 🟡 Medium
Impact: Usually 10-20% of losses
```

---

## Workflow

### Immediate (During Notification)
1. Read summary (30 sec)
2. Check 🟢 issues (1 min)
3. Click 1-2 example replays (2 min)
4. Decide: auto-apply or veto (10 sec)

**Total time:** ~4 minutes per batch

### Follow-Up (Next Day)
1. Review 🟡 issues (5-10 min)
2. Test proposed fixes manually (15-30 min)
3. Implement or assign to DEKU (5 min)

### Long-Term (Weekly/Monthly)
1. Review 🔴 issues (accumulate ideas)
2. Prioritize by frequency (recurring patterns)
3. Plan refactor sprints (when ready)

---

## Red Flags (When to Ignore)

- **Contradictory fixes** — "Add hazards" AND "Remove hazards"
- **Over-specific** — "Only happens with Kingambit on turn 7"
- **Meta blind** — "Add Heatran" (banned in Gen 9 OU)
- **Sample size** — Based on 1-2 battles only
- **Regression** — Suggests reverting a previous fix

**Action:** Mark as false positive, improve analysis prompt

---

## Quick Decisions

| Scenario | Action |
|----------|--------|
| 🟢 fix, 60% impact, 8 examples | ✅ Auto-apply |
| 🟢 fix, 20% impact, 2 examples | 🛑 Veto (small sample) |
| 🟡 fix, 40% impact, clear code diff | ✅ Review and test |
| 🔴 fix, 50% impact, major refactor | 📝 Add to roadmap |
| Fix contradicts team archetype | 🛑 Veto (preserve identity) |
| Fix addresses rare matchup | 🛑 Veto (not worth it) |

---

## Metrics to Track (Over Time)

1. **Auto-apply success rate** — Do 🟢 fixes actually work?
2. **Impact accuracy** — Do high-impact issues move WR?
3. **Team WR trends** — Which team improving/declining?
4. **Fix frequency** — Same issue recurring?
5. **ELO delta** — Ladder rating change per batch

**Goal:** Build trust in the system through data validation

---

## TL;DR

1. **Scan summary** → identify weak team
2. **Read 🟢 issues** → free wins
3. **Click examples** → verify
4. **Let auto-apply run** → unless red flag
5. **Review 🟡/🔴** → later

**Time commitment:** ~4 min per batch (~every 30 games)
