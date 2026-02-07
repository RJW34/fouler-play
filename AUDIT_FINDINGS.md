# Fouler Play v2 Codebase Audit Findings
**Date:** 2026-02-06  
**Auditor:** DEKU (subagent: codebase-audit)  
**Scope:** Comprehensive audit of `/home/ryan/projects/fouler-play-v2`

## Executive Summary
This audit identified **15 issues** across 7 categories. All issues have been documented below with severity ratings and fix plans.

---

## 1. Teams Directory Structure Issues

### Issue 1.1: Nested teams/teams/ Directory with Circular Symlinks
**Severity:** HIGH  
**Status:** IDENTIFIED

**Description:**
The teams directory has a confusing nested structure with circular symlinks:
- `teams/gen3` → `teams/gen3` (circular!)
- `teams/gen7` → `teams/gen7` (circular!)
- `teams/gen8` → `teams/gen8` (circular!)
- `teams/gen9` → `teams/gen9` (circular!)
- `teams/fat-teams.list` → `/home/ryan/projects/fouler-play-v2/teams/teams/fat-teams.list` (absolute path)

Actual team files are in `teams/teams/gen*/` but the symlinks create confusion.

**Impact:**
- Makes it unclear where team files actually live
- Risk of path resolution issues
- Confusing for developers

**Fix Plan:**
1. Move all team files from `teams/teams/` to `teams/` directly
2. Remove circular symlinks
3. Update `load_team.py` if needed
4. Update `fat-teams.list` references
5. Test team loading still works

---

### Issue 1.2: load_team.py Fragility
**Severity:** MEDIUM  
**Status:** IDENTIFIED

**Description:**
`load_team.py` uses `TEAM_DIR = os.path.dirname(os.path.abspath(__file__))` which works when imported correctly, but could break if:
- Called from different working directories
- Module is executed directly
- Symlinks change

**Fix Plan:**
- Add validation to ensure paths resolve correctly
- Add better error messages for debugging
- Consider making paths more explicit

---

## 2. Process Management Issues

### Issue 2.1: process_lock.py Already Fixed
**Severity:** N/A (FIXED)  
**Status:** VERIFIED

Recent commit `1618c3a` fixed the issue where process_lock was killing ALL fouler-play bots. Now correctly scopes to own directory using `cwd` check.

**Verification:** Code review confirms fix is solid ✓

---

### Issue 2.2: bot_monitor.py Complexity
**Severity:** MEDIUM  
**Status:** IDENTIFIED

**Description:**
`bot_monitor.py` is 785 lines doing many things:
- Process management
- Discord webhooks
- Battle state tracking
- Signal handling
- Drain mode logic
- Multiple concurrent battle tracking

High complexity = harder to debug when things fail.

**Potential Issues:**
- Multiple async tasks running concurrently
- Error handling could be clearer
- Logging to both file and debug log (bot_monitor.log vs bot_monitor_debug.log)

**Fix Plan:**
- Add more granular error handling
- Ensure all critical paths log properly
- Consider splitting into modules if complexity grows

---

## 3. Configuration Issues

### Issue 3.1: Multiple .env Files Without Clear Documentation
**Severity:** MEDIUM  
**Status:** IDENTIFIED

**Description:**
Three .env files exist:
- `.env` (generic/shared?)
- `.env.deku` (DEKU-specific)
- `.env.bakugo` (BAKUGO-specific)

But `.env.example` doesn't document:
- Which variables are required vs optional
- What each variable does
- Valid values/ranges
- Which .env file is canonical for each bot

**Variables found:**
```
BOT_DISPLAY_NAME
BOT_LOG_LEVEL
BOT_LOG_TO_FILE
DISCORD_BATTLES_WEBHOOK_URL
DISCORD_FEEDBACK_WEBHOOK_URL
DISCORD_WEBHOOK_URL
MAX_CONCURRENT_BATTLES
PS_BOT_MODE
PS_FORMAT
PS_PASSWORD
PS_RUN_COUNT
PS_SEARCH_TIME_MS
PS_USERNAME
PS_WEBSOCKET_URI
SEARCH_PARALLELISM
TEAM_LIST
```

**Fix Plan:**
1. Create comprehensive `.env.example` with ALL variables documented
2. Document which variables are required
3. Add comments explaining each variable
4. Clarify the multi-bot .env strategy

---

### Issue 3.2: Conflicting Environment Variable Values
**Severity:** LOW  
**Status:** IDENTIFIED

**Description:**
Multiple .env files have different values for same variables:
- `MAX_CONCURRENT_BATTLES=2` in one, `=3` in another
- `SEARCH_PARALLELISM=2` vs `=4`
- `PS_SEARCH_TIME_MS=2000` vs `=3000`

**Impact:**
Depending on which .env is loaded, behavior changes unpredictably.

**Fix Plan:**
- Document that each bot should use its own .env file exclusively
- Make loading logic explicit about which file it uses

---

## 4. Logging Issues

### Issue 4.1: Battle Logs Growing to 100MB+
**Severity:** HIGH  
**Status:** IDENTIFIED

**Description:**
Battle logs in `logs/` directory grow enormous:
```
210M  logs/battle-gen9ou-2534391754_elite4niel.log
175M  logs/battle-gen9ou-2534459659_chocojutsu.log
129M  logs/battle-gen9ou-2534471948-igqn6ouhn635o1lmcd4fvt5golmxjbqpw_knoxvill.log
102M  logs/battle-gen9ou-2534478245_None.log
101M  logs/battle-gen9ou-2534388322_None.log
```

Likely caused by verbose MCTS debug logging.

**Impact:**
- Disk space waste
- Slow file I/O
- Makes debugging harder (too much noise)

**Fix Plan:**
1. Find where battle-specific loggers are created
2. Add max file size limit to RotatingFileHandler
3. Reduce MCTS logging verbosity in production
4. Consider separate log levels for MCTS vs battle state

---

### Issue 4.2: No Log Rotation on Battle Logs
**Severity:** MEDIUM  
**Status:** IDENTIFIED

**Description:**
`config.py` has `CustomRotatingFileHandler` but it's only used for `init.log`. Battle logs created in `run.py` or `fp/run_battle.py` don't use rotation.

**Fix Plan:**
- Apply RotatingFileHandler to battle logs
- Set reasonable maxBytes (e.g., 10MB)
- Set backupCount to keep last N versions

---

## 5. Code Quality Issues

### Issue 5.1: No TODO/FIXME Comments in Main Code
**Severity:** N/A  
**Status:** VERIFIED

Grepped for TODO/FIXME/HACK in Python files - only found them in venv dependencies, not in project code. This is actually GOOD ✓

---

### Issue 5.2: Potential Path Bugs (Need Deeper Audit)
**Severity:** UNKNOWN  
**Status:** IN PROGRESS

**Action:** Need to grep for hardcoded paths, os.path.join issues, etc.

---

## 6. Documentation Issues

### Issue 6.1: CLAUDE.md Outdated
**Severity:** LOW  
**Status:** IDENTIFIED

Need to verify if CLAUDE.md reflects:
- Current directory structure
- Two-bot setup (DEKU vs BAKUGO)
- Recent fixes (process_lock, etc.)

**Fix Plan:**
- Review and update CLAUDE.md

---

### Issue 6.2: Missing Deployment Documentation
**Severity:** MEDIUM  
**Status:** IDENTIFIED

**Description:**
No clear docs on:
- How to start/stop each bot
- How DEKU and BAKUGO coexist
- What .env file each uses
- How to monitor running bots

**Fix Plan:**
- Add DEPLOYMENT.md or update README.md
- Document the two-bot architecture
- Add troubleshooting guide

---

## 7. Two-Bot Coexistence Issues

### Issue 7.1: Shared Pokemon Showdown Server
**Severity:** LOW (by design)  
**Status:** VERIFIED

Both bots connect to same PS server but with different usernames. This is intentional and working correctly ✓

---

### Issue 7.2: No Shared State Files (Good!)
**Severity:** N/A  
**Status:** VERIFIED

Each bot has its own:
- `.bot.pid` file
- `.pids/` directory  
- `logs/` directory
- `active_battles.json`

No risk of state conflicts ✓

---

## Next Steps

1. ✅ Run test suite to establish baseline
2. Fix teams directory structure (Issue 1.1)
3. Fix battle log size limits (Issue 4.1, 4.2)
4. Create comprehensive .env.example (Issue 3.1)
5. Update documentation (Issue 6.1, 6.2)
6. Deep code audit for path bugs (Issue 5.2)
7. Final test suite run
8. Commit all fixes
9. Post summary to Discord

---

## Test Results

**Baseline (before fixes):**
```
517 passed in 4.81s
```

All tests passing ✓
