# FOULER PLAY MAINTENANCE PROTOCOL
**Enforced:** 2026-02-15  
**Owner:** DEKU  
**Status:** MANDATORY — All batches must follow this protocol or they WILL fail.

---

## THE PROBLEM WE'RE SOLVING

Bot loses at 1100 ELO due to **systematic failures**:
1. **Quota bugs** (workers run 10x expected battles)
2. **Decision contradictions** (override ping-pong causes oscillation)
3. **Silent failures** (50+ bare except blocks, undetected ability misreads)
4. **Discord blackholes** (coordinator posts fail silently, no fallback)

These aren't balance/strategy problems. They're **architectural failures** that compound into ELO collapse.

---

## PROTOCOL: BEFORE EVERY BATCH

### 1. PRE-FLIGHT VERIFICATION (Non-Negotiable)
**Time:** 2-3 minutes  
**Owner:** automated script  
**Exit Condition:** Batch CANNOT start if any check fails

```bash
cd /home/ryan/projects/fouler-play
python3 pre_flight_check.py --run-count 15 --num-workers 3
```

**Must verify:**
- ✅ Quota math: `15 / 3 = [5, 5, 5]` (sum = 15)
- ✅ Team files exist and are parseable
- ✅ `PS_USERNAME`, `PS_PASSWORD` set
- ✅ `MAX_MCTS_BATTLES > 0`
- ✅ `battle_stats.json` valid
- ✅ Last batch worker counts don't exceed quotas

**If ANY check fails:**
- Print clear error message with fix
- Exit code 1
- launch.py refuses to start workers
- Human investigates before retry

### 2. CODE INTEGRATION (launch.py)
```python
from pre_flight_check import run_pre_flight_check

def main():
    # ... existing arg parsing ...
    
    # RUN PRE-FLIGHT CHECK
    print("[PRE-FLIGHT] Verifying configuration...")
    if not run_pre_flight_check(run_count=args.battles, num_workers=args.concurrent):
        print("\n[FATAL] Pre-flight check failed. Fix above and retry.")
        sys.exit(1)
    
    # Safe to proceed
    print("[PRE-FLIGHT] ✅ All checks passed. Launching batch.\n")
    # ... rest of launch ...
```

---

## PROTOCOL: DECISION LOGIC FIXES

### Phase 1: Remove Override Ping-Pong (CRITICAL)
**Affected file:** `/home/ryan/projects/fouler-play/fp/search/main.py`

**Issue:** `apply_threat_switch_bias` runs TWICE (lines 5396 and 5419), creating contradictory decisions.

**Fix:**
```python
# BEFORE (broken):
blended_policy = apply_heuristic_bias(...)
blended_policy = apply_threat_switch_bias(...)  # First pass
blended_policy = apply_team_strategy_bias(...)
blended_policy = apply_threat_switch_bias(...)  # Second pass (REDUNDANT!)
blended_policy = select_move_from_eval_scores(...)

# AFTER (fixed):
blended_policy = apply_heuristic_bias(...)
blended_policy = apply_switch_penalties(...)
blended_policy = apply_threat_switch_bias(...)  # ONCE ONLY
blended_policy = apply_hazard_maintenance_bias(...)
blended_policy = select_move_from_eval_scores(...)
```

**Deadline:** Before next batch  
**Verification:** Run 50 battle sample, check `mcts_meta` for oscillation patterns

### Phase 2: Add Logging to Silent Failures
**Affected file:** `/home/ryan/projects/fouler-play/fp/search/main.py:~1400-2500` (ability detection)

**Issue:** 50+ `except: pass` blocks swallow errors without logging.

**Fix:**
```python
# BEFORE:
try:
    threat_cat = get_threat_category(opp_name)
except Exception:
    pass  # BUG: Silent failure

# AFTER:
try:
    threat_cat = get_threat_category(opp_name)
except Exception as e:
    logger.warning(f"Failed to detect threat category for {opp_name}: {e}")
    threat_cat = THREAT_UNKNOWN
```

**Deadline:** Before next batch  
**Verification:** Run 10 battles, check logs for any "Failed to detect" warnings

### Phase 3: Hard-Zero Unaware Setup Moves
**Affected file:** `/home/ryan/projects/fouler-play/fp/search/eval.py`

**Issue:** Unaware abilities only get 25% penalty instead of being zeroed.

**Fix:**
```python
# BEFORE (weak):
if opponent_ability == "Unaware":
    setup_move_weight *= 0.75  # 25% reduction (still viable)

# AFTER (correct):
if opponent_ability == "Unaware":
    setup_move_weight = 0.0  # ZERO weight, move never selected
    logger.debug(f"Zeroed setup move {move} vs Unaware {opponent}")
```

**Deadline:** Before next batch  
**Verification:** Run 5 battles vs Unaware Dondozo, verify no Swords Dance usage

### Phase 4: Pre-Filter Illegal Moves Before MCTS
**Affected file:** `/home/ryan/projects/fouler-play/fp/search/main.py:5860`

**Issue:** MCTS samples include 0-PP moves and disabled moves, wastes 30% of search budget on illegal branches.

**Fix:**
```python
# Get candidate moves BEFORE MCTS
legal_moves = [m for m in battle.available_moves if m.current_pp > 0 and not m.disabled]
legal_switches = [s for s in battle.available_switches]

# MCTS searches only legal branches
mcts_result = monte_carlo_tree_search(legal_moves + legal_switches, ...)
```

**Deadline:** After Phase 1  
**Impact:** ~15-20% improvement in effective search depth

### Phase 5: Check force_switch BEFORE MCTS
**Affected file:** `/home/ryan/projects/fouler-play/fp/run_battle.py:1950`

**Issue:** MCTS runs for 3000ms searching moves when game state requires a switch.

**Fix:**
```python
# BEFORE MCTS:
if battle.force_switch:
    best_move = _get_best_switch(...)  # Switch immediately
else:
    best_move = await async_pick_move(...)  # Normal MCTS
```

**Deadline:** After Phase 1  
**Impact:** ~100ms per turn saved on forced switches

---

## PROTOCOL: DISCORD FALLBACK AUDIT

**Current state:** Broken

### Issue 1: post_to_discord() uses wrong CLI syntax
```python
# BROKEN:
cmd = ["/home/ryan/.npm-global/bin/openclaw", "message", "send",
       "--channel", channel_id, "--message", message]
```

**Fix:** Use message tool correctly via python, or use webhook like post_via_mha()

### Issue 2: post_via_mha() has no error recovery
```python
# If webhook fails with 400/403, coordinator silently logs and continues
# Should retry with local qwen fallback instead
```

**Fix:** Catch webhook 400/403, fallback to local announcement

### Issue 3: DEKU_COORDINATOR_STATUS_CHANNEL hardcoded to workspace
```python
# Line 46: Points to same channel as human conversation
DEKU_COORDINATOR_STATUS_CHANNEL = "1466642788472066296"  # WRONG
```

**Fix:**
1. Create new Discord channel `#deku-coordinator-status`
2. Update config to point to that channel ID
3. All coordinator alerts go to status channel, not workspace

---

## MONITORING: During Batch Execution

### Real-Time Checks
After every 5 battles:
1. **Verify per-worker counts don't exceed quotas** (e.g., Worker 0 hasn't run >5)
2. **Check battle_stats.json format** (is JSON valid? counts updated?)
3. **Scan logs for CRITICAL errors** (exception logs, move failures, etc.)

### Post-Batch Analysis
```bash
# After batch completes:
python3 batch_analyzer.py --batch-number=LATEST

# Must verify:
# - Total battles = expected (e.g., 15, not 46)
# - Per-team distribution even (not 16/2/3)
# - No worker ran >quota battles
# - Decision trace shows no oscillation patterns
```

---

## EMERGENCY HALT TRIGGERS

**If ANY of these occur, HALT batch immediately:**
- Worker runs >1 battle beyond quota
- Team distribution uneven (e.g., 4/2/3 when expecting 5/5/5)
- 3+ consecutive losses at new ELO
- `apply_threat_switch_bias` appears >1 time in trace
- Silent failures in ability detection (5+ warnings in logs)

**Process:**
1. Kill running bot process
2. Post to #deku-workspace: `🛑 BATCH HALTED — [reason]`
3. Investigate root cause (don't retry)
4. Fix root cause
5. Run pre-flight check again
6. Restart batch

---

## SUCCESS CRITERIA

### Per Batch:
- ✅ Pre-flight check passes
- ✅ Total battles = expected count
- ✅ Per-worker counts within ±1 of quota
- ✅ No worker runs >quota
- ✅ Team distribution ±1 deviation
- ✅ Zero decision oscillation patterns
- ✅ Ability detection has 0 failures
- ✅ No silent exception swallowing

### Weekly (Every 5 batches):
- ✅ Average WR trending upward or stable
- ✅ No crashes lasting >5 minutes
- ✅ ELO climbing toward 1700 target
- ✅ Decision traces show correct MCTS usage

### Monthly (Infrastructure):
- ✅ 99%+ uptime (max 7 hours downtime)
- ✅ No quota bugs found in monthly audit
- ✅ Discord fallback never triggered (means posting works)
- ✅ All decision phases executing, no silent skips

---

## AUTOMATION: Heartbeat Integration

Every 6 hours, coordinator runs:
```python
def fouler_play_health_check():
    # 1. Pre-flight verification
    # 2. Parse last 10 batches from battle_stats.json
    # 3. Check for quota violations
    # 4. Check for decision oscillation patterns
    # 5. Verify Discord connectivity
    # 6. Post results to #deku-coordinator-status
```

**Purpose:** Catch problems within 6 hours, not after 3 weeks.

---

## WHO IS RESPONSIBLE

| Task | Owner |
|------|-------|
| Run pre-flight check | Automation (launch.py) |
| Fix decision bugs (Phases 1-5) | DEKU (autonomously) |
| Monitor batch execution | Automated coordinator + human (if alerted) |
| Monthly infrastructure audit | DEKU (weekly heartbeat) |
| Emergency halt response | Human + DEKU coordination |

---

## ROOT CAUSE: Why This Protocol Exists

**Symptom:** Bot loses at 1100 ELO, batches run 10x expected battles, silent failures accumulate.

**Root Cause:** Reactive debugging instead of proactive prevention.
- Batch fails → human reports → DEKU investigates → fixes symptom
- No automated checks before batch start
- No logging on silent errors
- No fallback when coordinator posts fail

**Fix:** Make prevention automatic and mandatory.
- Pre-flight check = gate before any batch starts
- Logging in all exceptions = visibility into failures
- Fallback chains = graceful degradation

**Result:** "Foolproof" means the system itself prevents foolishness, not that humans must never make mistakes.

---

## REVIEW CYCLE

- **After 10 batches:** Audit this protocol. Did pre-flight catch problems? Did decision fixes help? Update thresholds if needed.
- **After 50 batches:** Full decision-logic re-audit. MCTS is running? Override ping-pong gone? Silent failures fixed?
- **After 100 batches:** If ELO hasn't reached 1700, root cause analysis on remaining issues.

The protocol itself must evolve based on what actually fails.

---

**This protocol is mandatory and non-negotiable. No batch runs without it. No exceptions.**
