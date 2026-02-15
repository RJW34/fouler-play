# Fouler Play Stability Monitor - Quick Start

## What This Does

**Separates infrastructure failures from skill issues** so you don't waste time tuning strategy when the real problem is service crashes or timeouts.

### The Problem

You see 50 losses and think "I need to improve my strategy!" But 10 of those losses were actually because the service crashed or timed out. You're wasting effort tuning strategy when you should be fixing infrastructure.

### The Solution

This monitor tracks:
- **Timeout vs Skill Losses** - Which losses were infrastructure issues?
- **Battle Loop Health** - Is the bot hanging between battles?
- **Memory/CPU Trends** - Are resources leaking?
- **Service Stability** - How often is the service crashing?

### The Result

```
BEFORE:
"I lost 50 battles, let me tune my MCTS search time..."

AFTER:
"I lost 50 battles: 10 were timeouts (infrastructure), 40 were skill issues.
 → Fix service stability first, THEN tune strategy"
```

## Quick Commands

### Check Current Stability
```bash
cd /home/ryan/projects/fouler-play
./venv/bin/python stability_monitor.py
```

### Get Recommendations for Heartbeat
```bash
./venv/bin/python check_stability_for_heartbeat.py
```

### View Latest Report
```bash
cat stability_report.json | python -m json.tool
```

## What the Report Tells You

```json
{
  "stability": {
    "timeout_loss_rate": 6.5,  // ← If this is >10%, prioritize infrastructure
    "timeout_losses": 3,        // ← These aren't skill issues
    "skill_losses": 43,         // ← These are strategy problems
    "health": "✅ HEALTHY"      // ← Overall status
  }
}
```

### Decision Tree

```
timeout_loss_rate >= 20%  →  🔴 STOP strategy work, fix infrastructure
timeout_loss_rate >= 10%  →  ⚠️ Flag in improvement plan
timeout_loss_rate < 10%   →  ✅ Focus on strategy
```

## Automatic Monitoring

A systemd timer runs every 30 minutes to update the stability report:

```bash
# Check timer status
systemctl --user status fouler-stability-monitor.timer

# View recent runs
journalctl --user -u fouler-stability-monitor.service -n 20
```

## Integration Example

```python
# In your improvement pipeline:

def should_focus_on_infrastructure():
    """Check if infrastructure issues override strategy work."""
    result = subprocess.run(
        ["./venv/bin/python", "check_stability_for_heartbeat.py"],
        capture_output=True, text=True
    )
    
    # Parse JSON output
    data = json.loads(result.stdout.split("JSON OUTPUT:")[1])
    
    if data["timeout_rate"] >= 10:
        print(f"⚠️ {data['timeout_rate']}% timeout rate - infrastructure first!")
        return True
    
    return False

# Then in your improvement plan generation:
if should_focus_on_infrastructure():
    plan = "Fix infrastructure before tuning strategy..."
else:
    plan = "Strategy improvements: ..."
```

## Files

| File | Purpose |
|------|---------|
| `stability_monitor.py` | Main monitoring script |
| `check_stability_for_heartbeat.py` | Heartbeat integration |
| `stability_report.json` | Auto-generated data |
| `STABILITY_MONITORING.md` | Full documentation |
| `STABILITY_INTEGRATION.md` | Integration guide |
| `STABILITY_DEPLOYMENT.md` | Deployment summary |

## Current Status

As of deployment (2026-02-14):

```
✅ HEALTHY
Last 100 battles: 54W - 46L
Timeout losses: 3 (6.5%)
Skill losses: 43
Infrastructure: Stable (1 restart in 24h)
```

**Recommendation:** System is healthy. Focus on strategy improvements.

## Next Steps

1. ✅ Monitor is deployed and running
2. ✅ Timer is scheduled (every 30 min)
3. ✅ Report is auto-generated
4. 📋 **TODO:** Integrate into `pipeline.py` (see STABILITY_INTEGRATION.md)
5. 📋 **TODO:** Add to heartbeat checks
6. 📋 **TODO:** Test over 24h period

## Need Help?

- **Full docs:** `STABILITY_MONITORING.md`
- **Integration guide:** `STABILITY_INTEGRATION.md`
- **Check logs:** `journalctl --user -u fouler-stability-monitor.service`
- **Test manually:** `python stability_monitor.py`
