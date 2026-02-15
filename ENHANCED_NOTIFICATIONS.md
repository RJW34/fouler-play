# Enhanced Watcher Notifications — Actionable Intelligence

## Overview

The Fouler Play watcher now posts **actionable intelligence** instead of raw analysis dumps. Each notification includes impact metrics, effort badges, team breakdowns, example battles, and auto-apply recommendations.

## Notification Structure

### 📬 Embed 1: Summary (Primary)
- **Batch number and record** — e.g., "Batch #5: 18-12 (60% WR)"
- **Team performance breakdown** — Win rates for all 3 teams:
  ```
  Stall: 8-2 (80% WR)
  Pivot: 5-5 (50% WR)
  Dondozo: 5-5 (50% WR)
  ```
- **Color-coded** — Green if winning batch (>50% WR), red if losing

### 📬 Embeds 2-4: Issues (Top 3 by Impact)

Each issue embed contains:

#### Title
```
1. 🟢 Easy/High — Add hazard removal to fat-team-2-pivot
```

#### Impact Metrics
```
Impact: Affects 60% of losses this batch (8 battles)
Teams affected: Pivot: 5 losses, Dondozo: 3 losses
Examples: [2539805111] • [2539810279] • [2539815551]
```
- **Percentage** — How many losses this issue contributed to
- **Battle count** — Absolute number of affected games
- **Team breakdown** — Which teams suffer most (sorted by loss count)
- **Replay links** — Up to 3 clickable battle examples

#### Code Diff (If Applicable)
```diff
💻 Suggested Fix
# In teams/fat-team-2-pivot.txt
- Ability: Regenerator
+ Ability: Heavy Duty Boots

OR add dedicated remover:
+ Corviknight @ Leftovers
+ Ability: Pressure
+ - Defog
```

#### Recommendation
```
✅ Will auto-apply next cycle (react 🛑 to block)
```
or
```
⚠️ Needs manual review before applying
```

### 📬 Embed 5: Footer (Metadata)
- Full report filename
- Analysis model (Ollama qwen2.5-coder:7b)
- Report location on disk
- Timestamp

## Effort/Impact Classification

### 🟢 Easy/High Impact (Auto-Apply Eligible)
**Criteria:**
- Team composition tweaks (add/remove Pokemon)
- Item changes (Heavy Duty Boots, Leftovers, etc.)
- Ability swaps (Regenerator → Pressure)
- Moveset adjustments (add Defog, remove redundant move)

**Why auto-apply?**
- Low risk (no code logic changes)
- High confidence (clear fixes from analysis)
- Easily reversible (revert team file)

**Examples:**
- "Add hazard removal to fat-team-2-pivot"
- "Give Corviknight Heavy Duty Boots"
- "Replace Toxic with Defog on Skarmory"

### 🟡 Medium Effort (Manual Review)
**Criteria:**
- Logic tweaks (threshold adjustments)
- Heuristic changes (switch scoring, threat assessment)
- Config updates (search depth, time limits)

**Why manual review?**
- Medium risk (could affect multiple battles)
- Requires testing (not obviously safe)
- May have side effects

**Examples:**
- "Increase switch penalty when health > 70%"
- "Prioritize super-effective coverage in endgame"
- "Adjust speed tie handling"

### 🔴 Hard/Low Impact (Never Auto-Apply)
**Criteria:**
- Major refactors (new systems, algorithms)
- Architecture changes (eval engine redesign)
- Complex feature additions (momentum tracking, opponent modeling)

**Why never auto-apply?**
- High risk (could break existing logic)
- Requires deep testing (multi-batch validation)
- Speculative impact (not proven from data)

**Examples:**
- "Implement momentum-based aggression logic"
- "Add long-term position evaluation"
- "Refactor battle state management"

## How It Works (Technical)

### 1. Parse Analysis Sections
```python
sections = self._split_into_sections(analysis_text)
# Splits on numbered items (1., 2., 3.), ### headings, or "TOP IMPROVEMENTS"
```

### 2. Extract Issue Metadata
For each section:
- **Title** — First line (cleaned of markers)
- **Description** — Remaining text
- **Effort badge** — Classified by keywords in title/description
- **Example battles** — Extract battle IDs mentioned in text
- **Team impact** — Count losses per team from examples

### 3. Cross-Reference Battle Data
```python
battles = self._get_recent_battles(BATCH_SIZE)
examples = self._find_example_battles(title, description, battles)
team_impact = self._calculate_team_impact(examples, battles)
```

Matches battle IDs mentioned in AI analysis (e.g., "battle-gen9ou-2539805111") against actual `battle_stats.json` entries.

### 4. Calculate Impact Percentage
```python
losses_affected = len([b for b in examples if b['result'] == 'loss'])
total_losses = len([b for b in all_battles if b['result'] == 'loss'])
impact_pct = (losses_affected / total_losses * 100)
```

### 5. Build Structured Embeds
- **Sort issues** by impact (losses affected DESC, effort score ASC)
- **Generate embeds** for top 3 issues
- **Add metadata** (code diffs, auto-apply flags, replay links)

### 6. Send to Discord
```python
response = requests.post(DISCORD_WEBHOOK_URL, json={"embeds": embeds[:10]})
```

## Example Notification

```
🎯 Fouler Play Analysis — Batch 5
Record: 18-12 (60.0% WR)
Battles: 30

Team Performance:
Stall: 8-2 (80% WR)
Pivot: 5-5 (50% WR)
Dondozo: 5-5 (50% WR)

───────────────────────────────

1. 🟢 Easy/High — Add hazard removal to fat-team-2-pivot

Impact: Affects 60% of losses this batch (8 battles)
Teams affected: Pivot: 5 losses, Dondozo: 3 losses
Examples: [2539805111] • [2539810279] • [2539815551]

💻 Suggested Fix
# In teams/fat-team-2-pivot.txt
- Ability: Regenerator
+ Ability: Heavy Duty Boots

✅ Will auto-apply next cycle (react 🛑 to block)

───────────────────────────────

2. 🔴 Hard/Low — Add steel-type counterplay to fat-team-1-stall

Impact: Affects 30% of losses this batch (4 battles)
Teams affected: Stall: 4 losses
Examples: [2539822342] • [2539829275] • [2539836046]

💻 Suggested Fix
# In battle_logic.py, add steel-type threat detection
def should_prioritize_counterplay(self, threat_pokemon):
    if threat_pokemon.type1 == 'Steel' and threat_pokemon.has_boost('defense'):
        return self.get_special_attackers() or self.get_phasers()

⚠️ Needs manual review before applying

───────────────────────────────

📊 Full Report: batch_0005_20260214_204500.md
🤖 Analysis: qwen2.5-coder:7b on MAGNETON
📍 Location: /home/ryan/projects/fouler-play/replay_analysis/reports/
```

## Configuration

### Batch Size
```bash
# .env
FOULER_BATCH_SIZE=30  # Trigger analysis every 30 battles
```

### Discord Channel
```python
# pipeline.py
DISCORD_CHANNEL_ID = "1466642788472066296"  # #deku-workspace
```

### Auto-Apply Keywords
```python
# pipeline.py - _classify_fix()
# Add keywords to enable auto-apply for new fix types
if any(keyword in title_lower for keyword in [
    'team composition', 'hazard removal', 'add', 'item change'
]):
    return ("🟢 Easy/High", 1, True)  # auto_apply=True
```

## Testing

### Run Enhanced Test
```bash
cd /home/ryan/projects/fouler-play
python test_watcher_notification.py
```

**Expected output:**
```
🧪 Testing Fouler Play Watcher Enhanced Notification System

✅ Created 30 test battles (14 losses, 16 wins)
✅ Test report created: batch_0004_20260214_204752_TEST.md
📤 Testing enhanced Discord notification...
✅ Discord notification sent to #deku-workspace (4 issues)

✅ Test complete! Check #deku-workspace for enhanced notification.
   Expected: 3 issue embeds with impact metrics, team breakdown, and example links
```

### Verify in Discord
Check #deku-workspace for:
- ✅ Summary embed with team WRs
- ✅ 3 issue embeds (hazard removal, steel-type, over-switching)
- ✅ Impact percentages (60%, 30%, 15%)
- ✅ Team breakdowns (Pivot: X losses, Stall: Y losses)
- ✅ Example replay links (clickable)
- ✅ Code diffs in collapsed fields
- ✅ Auto-apply recommendations

## Troubleshooting

### "No issues parsed from analysis"
- **Cause:** AI analysis doesn't use numbered sections or clear headings
- **Fix:** Adjust `_split_into_sections()` to recognize different patterns
- **Debug:** Add `print(sections)` to see what was extracted

### "Impact percentage always 0%"
- **Cause:** Battle IDs in analysis don't match battle_stats.json
- **Fix:** Check replay ID format (should be "battle-gen9ou-XXXXXXXX")
- **Debug:** Print `examples` to see which battles were matched

### "Auto-apply flag incorrect"
- **Cause:** Keywords in `_classify_fix()` don't match issue title
- **Fix:** Add more keywords or adjust classification logic
- **Debug:** Print `title_lower` and `desc_lower` to see what's being matched

### "Team breakdown shows wrong teams"
- **Cause:** Example battles don't specify correct team_file
- **Fix:** Verify battle_stats.json has accurate team assignments
- **Debug:** Print `team_impact` dict to see counts

## Future Enhancements

### 1. Historical Context
Track previous batches and show:
- "Similar issue in Batch #3: +12% WR after fix"
- "Previously auto-applied: 3/5 successes"
- "ELO delta: +25 after implementing this fix"

### 2. Replay Embedding
Instead of just links, embed replay viewer:
- Show key turns that demonstrate the issue
- Highlight decision points where bot failed
- Compare bot choice vs. optimal move

### 3. Live Testing Results
After auto-apply, track next batch:
- "Hazard removal fix: 8→2 losses (75% reduction)"
- "Auto-rollback if WR decreases by >5%"

### 4. Competitive Context
Pull ladder data:
- "Steel-types used in 45% of top 500 teams"
- "Hazard stacking meta increased 20% this week"
- "Priority threat: Kingambit (15% usage, 60% WR vs. Stall)"

### 5. Multi-Batch Trends
Analyze across multiple batches:
- "Recurring issue: Hazard weakness appears in 4/5 recent batches"
- "Team performance trending: Stall +5% WR, Pivot -8% WR"
- "Meta adaptation needed: Steel-type usage increasing"

## Files

- **pipeline.py** — Enhanced notification logic (~250 lines added)
- **test_watcher_notification.py** — Test suite with battle data generation
- **ENHANCED_NOTIFICATIONS.md** — This documentation
- **WATCHER_SETUP.md** — Original setup guide (still valid)

## Summary

The enhanced notification system transforms raw AI analysis into **actionable intelligence**:

✅ **Impact metrics** — Know which fixes matter most
✅ **Effort badges** — Prioritize easy wins
✅ **Team breakdown** — Fix the weakest teams first
✅ **Example battles** — Verify the issue with replays
✅ **Auto-apply** — Safe fixes applied automatically
✅ **Scannable** — Make decisions in <30 seconds

**Goal:** Fast, confident decision-making backed by data.
