# QUOTA & COUNT LOGIC AUDIT - run.py
**Date:** 2026-02-15  
**Scope:** ALL counting mechanisms, conditionals, thread-safety, off-by-one patterns

---

## 1. COUNTER INVENTORY

### Global Counters
- **`FoulPlayConfig.run_count`** - Global battle limit (config-driven)
- **`BattleStats.battles_run`** - Global count of completed battles (across all workers)
- **`BattleStats.wins`** - Global win count
- **`BattleStats.losses`** - Global loss count

### Per-Worker Counters
- **`worker_battles`** (local to each worker) - Counts battles completed by this worker

### Per-Worker Quota System
- **`per_worker_quota`** - Per-worker limit computed from `run_count / num_workers`
- **`per_worker_quotas[]`** - Array of quotas, one per worker

---

## 2. ALL COUNTER INCREMENTS/DECREMENTS

### BattleStats.battles_run
| Line | Location | Operation | Context |
|------|----------|-----------|---------|
| 132 | `BattleStats.__init__` | `self.battles_run = 0` | Initialization |
| 177 | `BattleStats.record_win` | `self.battles_run += 1` | ✅ Under `async with self._lock` |
| 185 | `BattleStats.record_loss` | `self.battles_run += 1` | ✅ Under `async with self._lock` |

### worker_battles (per-worker)
| Line | Location | Operation | Context |
|------|----------|-----------|---------|
| 288 | `battle_worker` | `worker_battles = 0` | Initialization |
| 407 | `battle_worker` | `worker_battles += 1` | After recording win |
| 416 | `battle_worker` | `worker_battles += 1` | After recording loss |

**✅ NO RACE CONDITIONS:** Each worker has its own `worker_battles` variable (local scope).

---

## 3. ALL CONDITIONAL CHECKS (< <= > >=)

### Line 295-296: Per-worker quota check
```python
if per_worker_quota > 0 and worker_battles >= per_worker_quota:
    logger.info(f"Worker {worker_id}: Per-worker quota reached ({worker_battles}/{per_worker_quota}), stopping")
    break
```
**Status:** ✅ CORRECT  
**Logic:** `>=` means worker stops AFTER reaching quota (e.g., quota=5 stops at 5, not 4)  
**Location:** Top of worker loop, BEFORE battle starts  
**Effect:** Worker will NOT start a new battle if quota already met

### Line 300-302: Global run_count safety net
```python
battles_run = await stats.get_battles_run()
if battles_run >= FoulPlayConfig.run_count:
    logger.info(f"Worker {worker_id}: Run count reached, stopping")
    break
```
**Status:** ✅ CORRECT  
**Logic:** `>=` checks global count BEFORE starting new battle  
**Thread-safety:** ✅ Uses `get_battles_run()` which locks  
**Purpose:** Safety net in case per-worker quotas drift due to timing

### Line 704: Per-worker quota computation gating
```python
if num_workers > 1 and FoulPlayConfig.run_count <= 999999:
    base = FoulPlayConfig.run_count // num_workers
    remainder = FoulPlayConfig.run_count % num_workers
    for i in range(num_workers):
        per_worker_quotas.append(base + (1 if i < remainder else 0))
```
**Status:** 🚨 **SUSPICIOUS - MAGIC NUMBER**  
**Issue:** Why is `999999` the cutoff?  
**Effect:** If `run_count > 999999`, all workers get quota=0 (unlimited)  
**Likely Intent:** Prevent quota system for "infinite run" configs (e.g., 1000000000)  
**Risk:** If someone sets `run_count=1000000`, they expect 1M battles, but quota system disables

### Line 287, 419: Logging conditions
```python
if per_worker_quota > 0 else ""
if per_worker_quota > 0:
```
**Status:** ✅ CORRECT (logging only)

---

## 4. PER_WORKER_QUOTA USAGE AUDIT

| Line | Usage | Purpose |
|------|-------|---------|
| 282 | Function parameter | Passed to `battle_worker()` |
| 287 | Log condition | Only log quota if > 0 |
| 295 | **EXIT CONDITION** | `worker_battles >= per_worker_quota` (if > 0) |
| 296 | Logging | Report quota reached |
| 419 | Log condition | Only log progress if quota active |
| 420 | Logging | `{worker_battles}/{per_worker_quota}` |
| 703 | List initialization | `per_worker_quotas = []` |
| 708 | Computation | Distribute `run_count` across workers |
| 709 | Logging | Report computed quotas |
| 711 | Fallback | Set all to 0 if conditions not met |
| 726 | Worker spawn | `per_worker_quota=per_worker_quotas[i]` |

**Key Insight:** `per_worker_quota=0` means "no limit" (worker checks `> 0` before enforcing)

---

## 5. BATTLESTATS THREAD-SAFETY ANALYSIS

### Lock Usage
✅ **PROPERLY LOCKED:**
- `record_win()` - `async with self._lock`
- `record_loss()` - `async with self._lock`
- `get_battles_run()` - `async with self._lock`

### Race Condition Check
✅ **NO RACES DETECTED:**
1. All mutations of `battles_run`, `wins`, `losses` are under lock
2. All reads of `battles_run` go through `get_battles_run()` which locks
3. `worker_battles` is local to each worker (no sharing)

### Potential Issue: Lock Type
- Uses `asyncio.Lock()` (✅ correct for async/await)
- NOT using `threading.Lock()` (would be wrong for async)

---

## 6. OFF-BY-ONE PATTERN ANALYSIS

### Pattern 1: Loop exit conditions
```python
while not shutdown_event.is_set():
    if worker_battles >= per_worker_quota:  # Line 295
        break
```
**Analysis:**  
- Check happens BEFORE battle starts  
- If quota=5, worker stops at 5 (correct)  
- NOT an off-by-one ✅

### Pattern 2: Global count check
```python
battles_run = await stats.get_battles_run()
if battles_run >= FoulPlayConfig.run_count:  # Line 300
    break
```
**Analysis:**  
- Check happens BEFORE battle starts  
- If run_count=100, stops at 100 (correct)  
- NOT an off-by-one ✅

### Pattern 3: Quota distribution
```python
base = FoulPlayConfig.run_count // num_workers
remainder = FoulPlayConfig.run_count % num_workers
for i in range(num_workers):
    per_worker_quotas.append(base + (1 if i < remainder else 0))
```
**Example:** run_count=10, num_workers=3  
- Worker 0: 10//3 + (0 < 1) = 3 + 1 = **4**  
- Worker 1: 10//3 + (1 < 1) = 3 + 0 = **3**  
- Worker 2: 10//3 + (2 < 1) = 3 + 0 = **3**  
- Total: 4 + 3 + 3 = **10** ✅

**NOT an off-by-one** ✅

---

## 7. BUGS & SUSPICIOUS PATTERNS FOUND

### 🚨 BUG #1: Magic Number 999999 (Line 704)
```python
if num_workers > 1 and FoulPlayConfig.run_count <= 999999:
```
**Problem:** Arbitrary cutoff disables per-worker quotas for large run counts  
**Impact:** If user sets `run_count=1000000`, quota system silently disables  
**Expected:** Workers run indefinitely instead of 1M total  
**Fix:** Either:
1. Remove the check (always distribute quotas)
2. Use a larger threshold (e.g., `<= 10**9`)
3. Add explicit "infinite run" flag instead of magic number

### 🟡 EDGE CASE: worker_battles increment timing
**Lines 407, 416:**
```python
if winner == FoulPlayConfig.username:
    await stats.record_win(team_file_name, battle_tag)
    worker_battles += 1  # AFTER async call
```
**Scenario:**  
1. Worker completes battle #4 (quota=5)
2. Calls `record_win()` (async, takes time)
3. THEN increments `worker_battles` to 5
4. Loop continues, checks `worker_battles >= 5` → breaks
5. Worker never starts battle #6 ✅

**Status:** NOT a bug, but increment happens AFTER stats recording  
**Risk:** If `record_win()` fails, `worker_battles` might not increment  
**Current behavior:** If recording fails, worker still stops (because exception breaks loop)

### ✅ CORRECT: Quota check happens BEFORE battle
Worker loop structure:
```
while not shutdown:
    if quota_reached:  # Check FIRST
        break
    start_battle()     # Then start
    finish_battle()
    increment_counter()
```
This ensures workers never EXCEED quota, only REACH it.

---

## 8. VERIFICATION CHECKLIST

| Check | Status | Details |
|-------|--------|---------|
| All increments tracked? | ✅ | Lines 177, 185, 407, 416 |
| All conditionals flagged? | ✅ | Lines 295, 300, 704 |
| BattleStats thread-safe? | ✅ | All mutations under `asyncio.Lock` |
| Worker counters thread-safe? | ✅ | Local scope, no sharing |
| Off-by-one errors? | ✅ | None found |
| Magic numbers? | 🚨 | Line 704: `999999` |
| Race conditions? | ✅ | None found |

---

## 9. RECOMMENDATIONS

### CRITICAL: Fix magic number
**Before:**
```python
if num_workers > 1 and FoulPlayConfig.run_count <= 999999:
```

**Option A - Always distribute (simplest):**
```python
if num_workers > 1:
```

**Option B - Explicit infinite flag:**
```python
if num_workers > 1 and FoulPlayConfig.run_count < 10**9:
```

**Option C - Config flag:**
```python
if num_workers > 1 and not FoulPlayConfig.infinite_run:
```

### MONITORING: Add quota drift detection
Current system is correct, but add logging:
```python
if battles_run >= FoulPlayConfig.run_count:
    actual_total = sum(worker_battles_list)
    drift = abs(actual_total - battles_run)
    if drift > 0:
        logger.warning(f"Quota drift: expected={FoulPlayConfig.run_count}, actual={actual_total}, drift={drift}")
```

---

## 10. SUMMARY

**Total counters found:** 5 (battles_run, wins, losses, worker_battles, run_count)  
**Total increments:** 4 locations  
**Total conditionals:** 5 locations  
**Bugs found:** 1 (magic number 999999)  
**Race conditions:** 0  
**Off-by-one errors:** 0  
**Thread-safety issues:** 0  

**Overall assessment:** Logic is sound except for the magic number threshold. The recent fix to check quotas BEFORE battle start (line 295-302) is correct and prevents overruns.

