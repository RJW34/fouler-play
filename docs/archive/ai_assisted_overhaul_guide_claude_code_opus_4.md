# Fouler-Play Codebase Overhaul Guide

This guide is written to help you **systematically overhaul fouler-play** using **Claude Code** and **Opus 4.6**, with the explicit goal of producing a **Gen9 OU–capable evaluation bot** that can play at **1700+ ELO** with a *high skill floor*.

This is **not** about adding more search or randomness. It is about restoring **competent competitive fundamentals**, isolating regressions, and then layering controlled intelligence on top.

---

## 0. Ground Truth (Read This First)

Your intuition is correct:

- The bot **should not sack incorrectly**
- The bot **should not be creative at the expense of fundamentals**
- The bot’s **floor matters more than its ceiling**

For team testing, variance is noise. The bot’s job is to be:

> *A boring, consistent, mechanically sound ladder player*

If the bot loses, it should lose **for the same reasons a good human would**.

Right now, fouler-play feels worse than foul-play because:

1. **Decision authority is fragmented across branches**
2. Experimental logic leaked into core loops
3. Search and heuristics are fighting each other
4. You optimized for “clever” before locking “correct”

We will fix that.

---

## 1. Immediate Repo Hygiene (Do This Before Any AI Work)

### 1.1 Squash Strategy (Strongly Recommended)

You should **not** preserve foulest-play as an independent lineage.

**Action:**

- Create a new branch: `gen9ou-baseline`
- Cherry-pick ONLY commits that:
  - Fix bugs
  - Improve state parsing
  - Improve legality checks
  - Improve speed / stability

Do **not** cherry-pick:
- Multi-game concurrency logic
- MCTS expansion changes
- Experimental evaluation functions

Once done:

```bash
git checkout master
git merge --squash gen9ou-baseline
git commit -m "Establish Gen9 OU baseline"
```

This gives Claude a **single coherent target**.

---

## 2. How to Use Claude Code (Correctly)

Claude Code is most effective when you **constrain its mandate**.

### 2.1 NEVER Say

> “Improve the bot”

### 2.2 ALWAYS Say

> “Make this module match high-level Gen9 OU fundamentals”

Claude must be told **what good looks like**.

---

## 3. Phase 1: Restore a High Skill Floor

### 3.1 Lock These Behaviors First

These are *non-negotiable* for 1700+ ladder:

- Never sack unless forced
- Never ignore obvious KO ranges
- Never click low-value setup when under threat
- Never mis-handle choice lock
- Never mis-handle Terastallization rules

#### Claude Prompt (Module-Level)

Use this **verbatim** pattern:

```
You are modifying a competitive Pokémon Gen9 OU battle bot.

Your task:
- Remove any logic that encourages speculative sacks
- Enforce conservative switching and survival-first heuristics
- Assume the opponent is competent

This bot is used to evaluate teams, not to explore strategies.

Here is the module:
<PASTE FILE>
```

Do this **file by file**, not repo-wide.

---

## 4. Phase 2: Strip Search Back to Sanity

Your realization about MCTS + concurrency is correct.

### 4.1 What To Remove

- Parallel game rollouts
- Deep playouts with weak evaluation
- Branching that ignores matchup context

### 4.2 What To Keep

- 1-ply tactical lookahead
- Forced-line detection (kills, saves, traps)
- Deterministic pruning

#### Rule of Thumb

If the bot can’t explain *why* a move is good in English, it shouldn’t search it.

---

## 5. Phase 3: Human-Like Evaluation (Critical)

High-level players do **not** optimize damage; they optimize **positions**.

### 5.1 Evaluation Must Weight:

- Win conditions alive
- Defensive backbone integrity
- Speed control status
- Hazard asymmetry
- Tera information advantage

### 5.2 Explicitly Devalue

- Raw damage if it weakens structure
- Early Tera without payoff
- Greedy prediction lines

#### Claude Prompt (Eval Rewrite)

```
Rewrite this evaluation function to reflect how a high-level Gen9 OU player
assesses a position.

Prioritize long-term win conditions over immediate damage.
Assume both players minimize risk.

Here is the function:
<PASTE FUNCTION>
```

---

## 6. Phase 4: Make It a Team Tester (Not a Player)

This is the most important conceptual shift.

### 6.1 The Bot Is NOT Trying to Win

It is trying to answer:

- Does this team consistently reach its win condition?
- Where does it fold under pressure?
- What patterns repeat across losses?

### 6.2 Required Outputs

Add logging for:

- Turn where win condition becomes unreachable
- First forced sack
- Tera usage turn + justification
- Endgame matchup summary

Claude is excellent at adding **structured logging** if told explicitly.

---

## 7. Validation Protocol (Do Not Skip)

Before trusting results:

1. Run 50 games with a **known ladder team**
2. Compare replays vs a human 1700+ player using the same team
3. Loss patterns should *match*, even if execution differs

If the bot loses in *new* ways → regression.

---

## 8. Final Reality Check

> Are you on the way to the mission statement?

Yes — **architecturally**.

But you only get there if you:

- Freeze experimentation
- Enforce fundamentals
- Treat AI as a junior engineer, not a strategist

Once the floor is elite, *then* you can let it think.

If you want, next we can:
- Define a "1700+ competency checklist"
- Write Claude prompts for each subsystem
- Design replay analysis schemas for players

Just say the word.

