# Turn Review System - User Guide

**Purpose:** Get expert feedback on bot decisions without watching full replays

## How It Works

After each **lost battle**, the system automatically:

1. **Analyzes the replay** for mistakes
2. **Extracts 1-3 critical decision points**
3. **Posts them to Discord** with:
   - Turn number
   - Board state (active Pokemon, HP%, field conditions)
   - Bot's choice
   - Why it's critical
   - Alternative options to consider

## What You Do

When you see a turn review in Discord, **just reply with your feedback**:

### Example Turn Review:
```
📋 Turn 7 Review
🔗 Replay: https://replay.pokemonshowdown.com/...

Board State:
• Bot: Gliscor (78% HP)
• Opponent: Zamazenta (100% HP)
• Field: Stealth Rock (opponent's side)

Bot chose: Switched to Skarmory

Why critical: 🔄 Switched to Skarmory - was this the right matchup?

❓ Was this the right play?
• Alternative: Stay in and attack (Maintain momentum)
• Alternative: Switch to Gholdengo (Better special bulk vs Zamazenta)

Reply with your feedback on this turn!
```

### How to Reply:

**Option 1: Simple yes/no**
```
No, should have stayed in. Gliscor outspeeds and OHKOs with Earthquake.
```

**Option 2: Detailed feedback**
```
Bad switch. Zamazenta is likely banded/scarfed and will click Close Combat.
Gliscor outspeeds and threatens OHKO with Earthquake. Even if it's scarfed,
Earthquake does 80%+ and we force them out. Skarmory gets destroyed here.
```

**Option 3: It was correct**
```
Good switch. Zamazenta probably has Ice Fang for Gliscor, and Skarmory
walls it completely. Smart play.
```

## What Happens with Your Feedback

Your feedback is:
1. **Saved** to `feedback_log.jsonl`
2. **Analyzed** for patterns (e.g., "bot switches too much")
3. **Converted** into heuristic improvements
4. **Tracked** for bot accuracy over time

## Example Feedback Flow

```
Turn Review → You reply "No, should have stayed in and EQ'd"
              ↓
System logs: {
  "bot_choice": "Switched to Skarmory",
  "correct": false,
  "suggested": "Stay in and use Earthquake",
  "reasoning": "Gliscor outspeeds and OHKOs"
}
              ↓
Pattern detected: "Bot switching when it should attack"
              ↓
Heuristic added: "Reduce switch penalty when bot outspeeds and threatens KO"
              ↓
Bot improves!
```

## Feedback Stats

Check bot's decision-making accuracy:
```bash
cd /home/ryan/projects/fouler-play
python replay_analysis/feedback_tracker.py
```

Shows:
- Total reviews
- Correct vs incorrect decisions
- Accuracy percentage
- Most common mistakes
- Suggested improvements

## Tips for Good Feedback

**✅ Good:**
- "No, Gliscor outspeeds and OHKOs with EQ"
- "Yes, correct switch - Zamazenta has Ice Fang coverage"
- "Bad setup - opponent has 5 healthy mons including a phazer"

**❌ Less Helpful:**
- "Wrong"
- "Bad play"
- "Idk"

**The more specific you are, the better the bot can learn!**

## Manual Feedback Entry

If you want to give feedback on a turn that wasn't auto-posted:

```python
from replay_analysis.feedback_tracker import FeedbackTracker

tracker = FeedbackTracker()
tracker.record_feedback(
    turn_number=7,
    replay_url="https://replay.pokemonshowdown.com/...",
    bot_choice="Switched to Skarmory",
    expert_says_correct=False,
    expert_suggested="Stay in and use Earthquake",
    expert_reasoning="Gliscor outspeeds and threatens OHKO"
)
```

## Questions I'll Ask You

The system will focus on:
- **Switches:** "Should I have stayed in and attacked here?"
- **Setup:** "Was this the right time to set up?"
- **Pokemon faints:** "Should I have switched out earlier to save this mon?"
- **Hazards:** "Should I have prioritized setting up Stealth Rock here?"

---

**Goal:** Make your Pokemon expertise directly improve the bot's play!
