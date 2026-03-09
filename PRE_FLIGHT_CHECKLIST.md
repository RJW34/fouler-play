# Pre-Flight Verification - Quick Reference

## ⚠️ Scope note

This checklist is for batch-launch verification only. Before editing repo docs/code, agents should still follow the canonical startup path in `CLAUDE.md` and `TASKBOARD.md`, confirm branch reality, and inspect `git status` for unrelated local churn (including possible `fp/data/movepool_data.json` refresh noise).

## ✅ What Was Built

### Files Created
1. **`pre_flight_check.py`** - Main verification script (19KB, 500+ lines)
2. **`PRE_FLIGHT_README.md`** - Comprehensive documentation
3. **`launch_integration_example.py`** - Integration guide for launch.py
4. **`PRE_FLIGHT_CHECKLIST.md`** - This file

### Capabilities

#### 1️⃣ Quota Verification
- ✅ Validates quota math matches `run.py` exactly
- ✅ Tests known cases: 15/3, 20/3, 999999/3
- ✅ Verifies sum(quotas) == run_count
- ✅ Shows per-worker distribution

**Catches:** Math errors, quota logic bugs, worker starvation

#### 2️⃣ Team Rotation
- ✅ Parses TEAM_NAMES from .env
- ✅ Verifies file/directory existence
- ✅ Validates Pokemon Showdown format
- ✅ Simulates team assignment for test batch

**Catches:** Missing teams, corrupted files, invalid paths

#### 3️⃣ Config Sanity
- ✅ PS_USERNAME/PASSWORD presence
- ✅ BOT_LOG_LEVEL validity
- ✅ MAX_MCTS_BATTLES > 0
- ✅ PS_FORMAT correctness

**Catches:** Auth failures, invalid config values

#### 4️⃣ Battle Stats
- ✅ battle_stats.json parse check
- ✅ Win/loss totals and per-team breakdown
- ✅ Quota violation detection (historical)

**Catches:** Corrupted stats, quota enforcement bugs

---

## 🚀 Quick Start

### Run Standalone
```bash
# Use .env defaults
python3 pre_flight_check.py

# Override parameters
python3 pre_flight_check.py --run-count 15 --num-workers 3
```

### Integration (Recommended)
Add to `launch.py` before starting bot:

```python
from pre_flight_check import run_pre_flight_check

if not run_pre_flight_check(run_count=args.battles, num_workers=args.concurrent):
    print("[ERROR] Pre-flight check failed. Fix issues before launching.")
    sys.exit(1)
```

---

## 📋 Manual Checklist (if script unavailable)

Before every batch, verify:

- [ ] **Quota calculation:** `run_count // num_workers` distributes evenly
- [ ] **Team files exist:** All entries in TEAM_NAMES are valid paths
- [ ] **Credentials set:** PS_USERNAME and PS_PASSWORD in .env
- [ ] **Config valid:** MAX_MCTS_BATTLES > 0, BOT_LOG_LEVEL is DEBUG/INFO/WARNING/ERROR/CRITICAL
- [ ] **battle_stats.json:** Parses correctly (if it exists)

---

## 🧪 Verification Tests

### Test 1: Default Config
```bash
python3 pre_flight_check.py
# Expected: All checks pass ✅
```

### Test 2: Small Batch
```bash
python3 pre_flight_check.py --run-count 15 --num-workers 3
# Expected: Quotas = [5, 5, 5] ✅
```

### Test 3: Uneven Distribution
```bash
python3 pre_flight_check.py --run-count 20 --num-workers 3
# Expected: Quotas = [7, 7, 6] ✅
```

### Test 4: Error Detection
Create a broken config and verify script catches it:
- Remove PS_USERNAME from .env → Should fail ❌
- Set MAX_MCTS_BATTLES=0 → Should fail ❌
- Add nonexistent team to TEAM_NAMES → Should fail ❌

---

## 🎯 Success Criteria

Script returns exit code **0** and shows:
```
✅ ALL PRE-FLIGHT CHECKS PASSED
Ready to launch batch.
```

Any failures show:
```
❌ PRE-FLIGHT CHECK FAILED
Fix the issues above before running the batch.
```

Exit code **1** blocks batch from starting.

---

## 📊 Sample Output

```
============================================================
PRE-FLIGHT VERIFICATION
============================================================

1. QUOTA VERIFICATION
  ✅ 15 battles / 3 workers = [5, 5, 5] (sum=15)
  ✅ 20 battles / 3 workers = [7, 7, 6] (sum=20)
  ✅ 999999 battles / 3 workers = [333333, 333333, 333333] (sum=999999)

2. TEAM ROTATION
  ✅ Team 0: gen9/ou/fat-team-1-stall (file)
  ✅ Team 1: gen9/ou/fat-team-2-pivot (file)
  ✅ Team 2: gen9/ou/fat-team-3-dondozo (file)

3. CONFIG SANITY
  ✅ PS_USERNAME: BugInTheCode
  ✅ PS_PASSWORD: Her***************
  ✅ BOT_LOG_LEVEL: INFO
  ✅ MAX_MCTS_BATTLES: 1
  ✅ PS_FORMAT: gen9ou

4. BATTLE STATS
  ✅ battle_stats.json is valid JSON
  ✅ Found 321 recorded battles (58.3% win rate)

============================================================
SUMMARY
============================================================
✅ Quota Verification
✅ Team Rotation
✅ Config Sanity
✅ Battle Stats

✅ ALL PRE-FLIGHT CHECKS PASSED
Ready to launch batch.
```

---

## 🔧 Troubleshooting

### "Team file not found"
→ Verify TEAM_NAMES paths are relative to `teams/` directory
→ Example: `gen9/ou/fat-team-1-stall` should exist at `teams/gen9/ou/fat-team-1-stall`

### "Quota sum mismatch"
→ This is a critical bug in quota logic - do NOT proceed
→ Report to developer immediately

### "battle_stats.json is CORRUPTED"
→ Backup the file, then delete it (will be recreated)
→ Or fix JSON syntax errors manually

### "PS_USERNAME not set"
→ Add to `.env`: `PS_USERNAME=YourUsername`

---

## ✨ Features

- **Zero-dependency:** Uses only Python stdlib
- **Colored output:** Green ✅ for pass, Red ❌ for fail
- **Importable:** Can be called from other Python scripts
- **Standalone:** Works as CLI tool
- **Exit codes:** 0 = success, 1 = failure (scriptable)
- **Detailed errors:** Shows exactly what's wrong and how to fix it

---

## 📝 Integration Status

- [x] Script created and tested
- [x] Documentation written
- [ ] **TODO:** Integrate into `launch.py`
- [ ] **TODO:** Add to batch automation workflow
- [ ] **TODO:** Make mandatory in CI/CD (if applicable)

---

## 🎓 Design Principles

1. **Fail early:** Catch problems before batch starts
2. **Be explicit:** Show exactly what's wrong
3. **No surprises:** Test with known values first
4. **Trust but verify:** Don't assume config is correct
5. **Exit cleanly:** Return proper exit codes for automation

---

**Created:** 2026-02-15  
**Version:** 1.0  
**Status:** ✅ Production Ready
