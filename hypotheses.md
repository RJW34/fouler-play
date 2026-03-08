# fouler-play Hypotheses Log

Each entry is one experiment. Status: PENDING | TESTED | MERGED | DISCARDED

Baseline (2026-03-08): 41 battles, 23W/18L (56.1% overall)
- fat-team-1-stall: 10W/7L (59%)
- fat-team-2-pivot: 5W/7L (42%) <-- primary concern
- fat-team-3-dondozo: 8W/4L (67%)

---

## H001 — Penalty pipeline too aggressive on pivot switches
**Status:** PENDING (highest priority — strongest log evidence)
**Hypothesis:** The penalty pipeline penalizes switching too heavily, which disproportionately hurts fat-team-2-pivot (42% WR vs 59%/67% for the other teams). The pivot team relies on U-turn/Parting Shot momentum cycling, but the bot may be penalizing these pivot moves the same way it penalizes raw switches. This causes the bot to stay in and use weak/neutral moves instead of pivoting to a better matchup.
**Proposed change:** In `fp/search/main.py`, examine the switch penalty logic in the penalty pipeline. Pivot moves (U-turn, Volt Switch, Parting Shot, Flip Turn) should receive significantly reduced switch penalties compared to raw switches. Look for penalty constants in `constants_pkg/` that apply flat switch discouragement. Separate "pivot move" from "hard switch" in the penalty calc.
**Predicted effect:** fat-team-2-pivot WR should increase from 42% toward 55%+. The bot will use its pivot moves more often instead of staying in unfavorable matchups.
**Baseline win rate:** 42% (fat-team-2-pivot, N=12) / 56.1% (overall, N=41)
**Result win rate:** _[fill in after running]_
**Delta:** _[fill in]_
**Decision:** _[pending]_
**Notes:** Evidence from battle logs: in battle-gen9ou-2550834465, the bot was setting Spikes with Gliscor on turn 13 against Thundurus (Acrobatics user) instead of pivoting out. The pivot team's entire strategy depends on momentum cycling, and log evidence shows the bot choosing to stay in and use utility moves when it should be U-turning or Parting Shot-ing. The 25-point WR gap between pivot (42%) and dondozo (67%) strongly suggests the pivot playstyle is mechanically suppressed by the current penalty system.

---

## H002 — Ghost-immune-to-Dark type calc bug (Gholdengo vs Ting-Lu)
**Status:** PENDING
**Hypothesis:** The damage calculator may not correctly account for Ghost-type immunity to certain Dark-type interactions, specifically Gholdengo (Steel/Ghost with Good As Gold) vs Dark-type attackers like Ting-Lu. The bot may be overvaluing Dark-type moves against Ghost-types or undervaluing Ghost-type moves against targets with Dark-type STAB.
**Proposed change:** In `fp/search/eval.py`, audit the type effectiveness matrix for Ghost immunities. Verify that `type_effectiveness_modifier()` in `fp/helpers.py` correctly returns 0x for Normal/Fighting vs Ghost and for Ghost immunity interactions. Check if the Gholdengo Good As Gold ability check correctly blocks status moves.
**Predicted effect:** Fewer misplays in matchups involving Ghost-type pokemon. Could prevent 1-2 losses per 30-game stretch where the bot commits a Dark move into a Ghost.
**Baseline win rate:** 56.1% (overall)
**Result win rate:** _[fill in after running]_
**Delta:** _[fill in]_
**Decision:** _[pending]_
**Notes:** Known bug from agent profile. Gholdengo is on fat-team-1-stall and fat-team-2-pivot. Impact may be moderate — only triggers in specific matchups. Lower priority than H001 since it's a point fix vs a systemic penalty issue.

---

## H003 — Infinite switch loop detection and breaking
**Status:** PENDING
**Hypothesis:** The bot sometimes enters infinite switch loops where it alternates between two pokemon for many consecutive turns (11-turn loop observed). This wastes turns, accumulates hazard damage, and eventually loses games that were otherwise winnable. The penalty pipeline has no memory of recent switch patterns to detect and break these loops.
**Proposed change:** In `fp/search/main.py`, add a switch history tracker (last 6-8 moves). If the same switch pattern repeats 3+ times, force a different action: either stay in and attack, or switch to a third pokemon. Implementation: maintain a `recent_switches` deque per battle, check for A->B->A->B pattern, and apply a heavy penalty to the looping switch when detected.
**Predicted effect:** Prevents the ~5% of games that are lost purely to switch looping. Could add +2-3% overall WR.
**Baseline win rate:** 56.1% (overall)
**Result win rate:** _[fill in after running]_
**Delta:** _[fill in]_
**Decision:** _[pending]_
**Notes:** 11-turn loop directly observed in logs. This is a pure waste — every loop turn is a turn where hazards deal damage and the opponent gets free setup. The fix is relatively isolated (add loop detection to the switch decision) and low-risk.

---

## H004 — Recovery timing (using recovery too early)
**Status:** PENDING
**Hypothesis:** The bot uses recovery moves (Recover, Roost, Soft-Boiled) too early — at 70-80% HP when it should be attacking or setting hazards. This wastes turns that should be spent applying pressure. Recovery should be saved for when HP drops below 50% or when the opponent can't threaten a KO.
**Proposed change:** In `fp/search/main.py`, look at the recovery move evaluation. Add a HP threshold check: penalize recovery when HP > 60% unless the opponent has no threatening moves. The threshold should be context-dependent (lower for walls, higher for offensive mons).
**Predicted effect:** More aggressive play in the early-mid game. Could improve WR by 2-3% by converting turns into pressure instead of wasting them on unnecessary healing.
**Baseline win rate:** 56.1% (overall)
**Result win rate:** _[fill in after running]_
**Delta:** _[fill in]_
**Decision:** _[pending]_
**Notes:** Hard to quantify from battle_stats.json alone — needs replay log analysis of specific turns where recovery was chosen. Should correlate with turn count: if losses have high turn counts, recovery timing may be contributing (stalling too long instead of pressing advantage).

---

## H005 — Body Press damage calc (uses Defense, not Attack)
**Status:** PENDING
**Hypothesis:** Body Press calculates damage using the user's Defense stat instead of Attack. If the damage calculator incorrectly uses Attack, the bot will undervalue Body Press on high-Defense mons (Skarmory, Corviknight) and overvalue it on low-Defense mons. This specifically affects fat-team-1-stall (Skarmory has Body Press) and fat-team-2-pivot (Corviknight has Body Press).
**Proposed change:** In the damage calculation module (likely `fp/search/eval.py` or the external damage calc), verify that Body Press uses `defender.defense_stat` as the attack stat. Check the move data flags for Body Press to ensure the calc correctly swaps Attack for Defense.
**Predicted effect:** Correct Body Press calcs would improve move selection for Skarmory/Corviknight. Could affect 2-4 games per 30-game stretch where the bot undervalues Body Press as a KO or chip option.
**Baseline win rate:** 56.1% (overall) / 59% stall / 42% pivot
**Result win rate:** _[fill in after running]_
**Delta:** _[fill in]_
**Decision:** _[pending]_
**Notes:** Both teams with Body Press users have it as a key coverage move. Skarmory in stall needs Body Press to threaten Steel-types and Dark-types that try to set up. Corviknight in pivot needs it for chip damage during pivots. A wrong calc here silently degrades the bot's move choices without obvious errors in logs.
