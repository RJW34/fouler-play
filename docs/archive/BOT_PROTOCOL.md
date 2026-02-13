# Fouler Play Bot Management Protocol

**MANDATORY PROCEDURES - NO EXCEPTIONS**

## Golden Rules

1. ✅ **ALWAYS verify** - Never assume, always check
2. ✅ **MAX 2 battles per account** - Hard limit, enforced
3. ✅ **Clean shutdown before restart** - Kill old processes, verify they're dead
4. ✅ **Pre-flight checks before start** - No existing processes, no active battles

## Using bot_manager.py (PREFERRED)

```bash
# Check status (ALWAYS run this first)
./bot_manager.py status

# Stop bot (with verification)
./bot_manager.py stop

# Start bot (with pre-flight checks)
./bot_manager.py start --battles 1

# Restart (stop + verify + start)
./bot_manager.py restart
```

## Manual Procedures (if bot_manager.py not working)

### ⚠️ BEFORE STARTING

**1. Check for existing processes:**
```bash
ps aux | grep "fp/main.py"
```
- If ANY processes exist → STOP THEM FIRST
- Never start if processes are already running

**2. Verify no active battles:**
- Check Pokemon Showdown account page
- Look for "In battle" status
- If battles active → wait or forfeit

**3. Count how many battles you're about to start:**
- Starting 1 battle? ✅ OK
- Starting 2 battles? ✅ OK (max)
- Starting 3+ battles? ❌ NEVER

### 🛑 STOPPING

**1. Find all processes:**
```bash
ps aux | grep "fp/main.py" | grep -v grep
```

**2. Kill each PID:**
```bash
kill -SIGTERM <PID>
```

**3. Wait 2 seconds:**
```bash
sleep 2
```

**4. VERIFY they're dead:**
```bash
ps aux | grep "fp/main.py"
```
- If still running → `kill -SIGKILL <PID>`
- If STILL running → investigate, don't proceed

**5. Never say "stopped" until verified with `ps aux`**

### 🚀 STARTING

**1. Run pre-flight checks (see above)**

**2. Start command:**
```bash
# TODO: Replace with actual start command once known
python fp/main.py --username dekubotbygoofy --battles 1
```

**3. Verify it started:**
```bash
ps aux | grep "fp/main.py"
```
- Should see exactly 1 process per battle you started
- If you see 0 → didn't start, check logs
- If you see more than expected → kill extras

## Battle Count Monitoring

**During operation, periodically check:**
```bash
./bot_manager.py status
```

**If you see 3+ battles active:**
1. 🚨 IMMEDIATE STOP - something is broken
2. Kill all processes: `./bot_manager.py stop --force`
3. Investigate what caused multiple spawns
4. Fix root cause before restarting

## Common Mistakes (DO NOT DO THESE)

❌ Starting without checking existing processes  
❌ Assuming a process stopped because you ran `kill`  
❌ Starting 3+ battles on one account  
❌ Saying "bot is stopped" without running `ps aux`  
❌ Restarting without waiting for clean shutdown  

## Verification Checklist

**Before claiming "bot started":**
- [ ] Ran pre-flight checks
- [ ] Verified no existing processes
- [ ] Verified no active battles
- [ ] Started with command
- [ ] Verified process is running with `ps aux`
- [ ] Counted battles: ≤ 2

**Before claiming "bot stopped":**
- [ ] Sent kill signal
- [ ] Waited 2+ seconds
- [ ] Verified with `ps aux` - NO matching processes
- [ ] If any remain, killed with SIGKILL
- [ ] Re-verified with `ps aux` - NONE remain

## Emergency Procedures

**If processes won't die:**
```bash
ps aux | grep "fp/main.py" | awk '{print $2}' | xargs kill -9
sleep 2
ps aux | grep "fp/main.py"  # Should be empty
```

**If battles stuck on account:**
- Log into Pokemon Showdown manually
- Forfeit all battles
- Wait 30 seconds
- Verify battles cleared before restarting bot

---

**REMEMBER: Trust is earned through verification. Every time.**
