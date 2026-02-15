# QUOTA MONITORING FAILURE - ROOT CAUSE ANALYSIS

**Date:** 2026-02-15  
**Severity:** CRITICAL  
**Status:** IDENTIFIED - COUNTING BUG CONFIRMED

---

## EXECUTIVE SUMMARY

The battle quota system is **completely bypassed** due to an off-by-one comparison bug in `run.py` line 566. All workers run with unlimited quotas instead of the intended 5 battles each.

---

## ACTUAL vs EXPECTED COUNTS

### Last 3 Completed Batches:

**BATCH 8** (2026-02-14 23:06 - 2026-02-15 03:03)
- **TOTAL:** 46 battles (expected 15) → **+31 overcount**
- stall: 16 (expected 5) → **+11**
- pivot: 16 (expected 5) → **+11**
- dondozo: 14 (expected 5) → **+9**

**BATCH 9** (2026-02-15 03:53 - 2026-02-15 17:34)  
- **TOTAL:** 151 battles (expected 15) → **+136 overcount** 🚨
- stall: 57 (expected 5) → **+52**
- pivot: 45 (expected 5) → **+40**
- dondozo: 49 (expected 5) → **+44**

**BATCH 10** (2026-02-15 18:06 - current)
- **TOTAL:** 5 battles so far (incomplete batch)

---

## ROOT CAUSE: OFF-BY-ONE COMPARISON BUG

**Location:** `/home/ryan/projects/fouler-play/run.py` **lines 566-574**

```python
# Compute per-worker quotas for even distribution
per_worker_quotas = []
if num_workers > 1 and FoulPlayConfig.run_count < 999999:  # ❌ BUG HERE
    base = FoulPlayConfig.run_count // num_workers
    remainder = FoulPlayConfig.run_count % num_workers
    for i in range(num_workers):
        per_worker_quotas.append(base + (1 if i < remainder else 0))
    logger.info(f"Per-worker quotas: {per_worker_quotas} (total={FoulPlayConfig.run_count})")
else:
    per_worker_quotas = [0] * num_workers  # ❌ 0 = no per-worker limit
```

**Environment Configuration (.env):**
```bash
PS_RUN_COUNT=999999
MAX_CONCURRENT_BATTLES=3
```

**The Bug:**
- Line 566 checks: `FoulPlayConfig.run_count < 999999`
- Actual value: `999999` (from .env `PS_RUN_COUNT`)
- Result: `999999 < 999999` evaluates to **FALSE**
- **Consequence:** The `else` branch executes, setting `per_worker_quotas = [0, 0, 0]`

**What per_worker_quota=0 means:**
From `run.py` line 352:
```python
if per_worker_quota > 0 and worker_battles >= per_worker_quota:
    logger.info(f"Worker {worker_id}: Per-worker quota reached, stopping")
    break
```
When `per_worker_quota=0`, the condition `per_worker_quota > 0` is **always False**, so the quota check **never triggers**. Workers run indefinitely.

---

## WHY THIS HAPPENED

1. **Original Intent:** Use `999999` as a sentinel value meaning "unlimited"
2. **Implementation Flaw:** The comparison operator is `<` instead of `<=` or `!=`
3. **Result:** The exact value `999999` falls into the "unlimited" branch instead of being treated as a finite quota

---

## EVIDENCE FROM LOGS

Expected behavior with 3 workers and quota=5 each:
- Total battles per batch: **15** (3 workers × 5 battles)
- Battles per team: **5** (each worker uses one team)

Actual behavior (Batch 9 example):
- Total battles: **151** (10.07× expected)
- Max concurrent: **3** (confirms 3 workers active)
- No quota enforcement logged

---

## FIX OPTIONS

### Option 1: Change Comparison Operator (Recommended)
```python
# Line 566
if num_workers > 1 and FoulPlayConfig.run_count <= 999999:  # ✅ Use <= instead of <
```

### Option 2: Use Different Sentinel Value
```python
# Line 566
if num_workers > 1 and FoulPlayConfig.run_count > 0 and FoulPlayConfig.run_count < 999999:
```

### Option 3: Explicit Check for Unlimited
```python
UNLIMITED_BATTLES = 999999
if num_workers > 1 and FoulPlayConfig.run_count != UNLIMITED_BATTLES:
```

---

## VERIFICATION STEPS

After fix:
1. Set `PS_RUN_COUNT=15` in .env
2. Start bot with 3 workers
3. Confirm logs show: `Per-worker quotas: [5, 5, 5] (total=15)`
4. Confirm each worker stops after exactly 5 battles
5. Confirm total battles = 15

---

## IMPACT ASSESSMENT

- **Severity:** CRITICAL - Core quota system completely bypassed
- **Affected Versions:** All versions with multi-worker quota system
- **Workaround:** Manually stop process after desired battle count
- **Data Loss:** None - battles recorded correctly, just ran too many

---

## RECOMMENDED IMMEDIATE ACTION

1. Stop current battle processes
2. Apply Option 1 fix (change `<` to `<=`)
3. Set explicit run_count in .env: `PS_RUN_COUNT=15`
4. Restart and monitor for exactly 15 battles
5. Verify per-worker quota logs appear

---

**Report Generated:** 2026-02-15 13:42 EST  
**Analyzed by:** DEKU Sub-Agent (monitoring-failure-audit)
