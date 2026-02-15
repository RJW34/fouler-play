# Fouler Play Stability Monitoring System

## Overview

The stability monitoring system separates **infrastructure issues** from **skill issues** to ensure improvement efforts target the right problems.

### Key Metrics Tracked

1. **Battle Loop Gaps** - Detects hangs/timeouts (>5min gaps)
2. **Memory Usage** - Identifies memory leaks
3. **CPU Spikes** - Tracks resource consumption
4. **Service Restarts** - Counts crash frequency
5. **Timeout vs Skill Losses** - Separates infrastructure from strategy losses

## Components

### 1. `stability_monitor.py`
Main monitoring script that analyzes battle history and system resources.

**Run manually:**
```bash
cd /home/ryan/projects/fouler-play
./venv/bin/python stability_monitor.py
```

**Output:** `/home/ryan/projects/fouler-play/stability_report.json`

**Exit codes:**
- 0: Healthy
- 1: Warning (10%+ timeout rate or infrastructure issues)
- 2: Critical (20%+ timeout rate or severe issues)

### 2. `check_stability_for_heartbeat.py`
Heartbeat integration helper that reads stability report and provides recommendations.

**Usage in heartbeat:**
```bash
./venv/bin/python check_stability_for_heartbeat.py
```

**Returns:**
- Human-readable stability summary
- JSON output with recommendations
- Infrastructure-focused suggestions when timeout rate >10%

### 3. Systemd Timer
Automatically runs stability monitor every 30 minutes.

**Status:**
```bash
systemctl --user status fouler-stability-monitor.timer
```

**View logs:**
```bash
journalctl --user -u fouler-stability-monitor.service -f
```

**Manual trigger:**
```bash
systemctl --user start fouler-stability-monitor.service
```

## Stability Report Format

```json
{
  "generated_at": "2026-02-15T02:33:22.567285+00:00",
  "stability": {
    "battles_last_100": 100,
    "wins": 54,
    "total_losses": 46,
    "timeout_losses": 3,
    "skill_losses": 43,
    "unknown_losses": 0,
    "timeout_loss_rate": 6.5,
    "memory_trend": "stable",
    "avg_memory_mb": 227.4,
    "max_memory_mb": 227.4,
    "avg_cpu_percent": 0.0,
    "avg_gap_seconds": 819.8,
    "max_gap_seconds": 50772.5,
    "timeout_gaps_count": 28,
    "service_restarts_24h": 1,
    "health": "✅ HEALTHY"
  },
  "details": {
    "recent_timeout_losses": [...],
    "large_gaps": [...]
  }
}
```

## Health Status Levels

### ✅ HEALTHY
- Timeout loss rate < 10%
- Service restarts < 3 in 24h
- Memory usage stable

### ⚠️ WARNING
- Timeout loss rate 10-20%
- Service restarts 3-5 in 24h
- Memory usage elevated

### 🔴 CRITICAL
- Timeout loss rate > 20%
- Service restarts > 5 in 24h
- Memory usage critical

## Heartbeat Integration

### Example Heartbeat Code

```python
import subprocess
import json

def check_infrastructure_health():
    """Check if infrastructure issues should override strategy improvements."""
    try:
        result = subprocess.run(
            ["./venv/bin/python", "check_stability_for_heartbeat.py"],
            cwd="/home/ryan/projects/fouler-play",
            capture_output=True,
            text=True,
            timeout=30
        )
        
        # Parse JSON output
        lines = result.stdout.split('\n')
        json_start = None
        for i, line in enumerate(lines):
            if line.strip() == "JSON OUTPUT:":
                json_start = i + 1
                break
        
        if json_start:
            json_text = '\n'.join(lines[json_start:])
            data = json.loads(json_text)
            
            if data['needs_infrastructure_focus']:
                print("⚠️ Infrastructure issues detected - adding to improvement plan")
                return data['recommendations']
        
        return []
        
    except Exception as e:
        print(f"Could not check stability: {e}")
        return []

# In improvement plan generation:
improvements = []

# Check infrastructure first
infra_issues = check_infrastructure_health()
if infra_issues:
    improvements.append({
        "category": "Infrastructure",
        "priority": "HIGH",
        "issues": infra_issues
    })
    print(f"⚠️ Flagging {len(infra_issues)} infrastructure issues before strategy changes")

# Then add strategy improvements...
```

## Timeout Loss Detection Logic

A loss is classified as "timeout" if:

1. **Large gap before** (>10min) - Service likely restarted/hung before this battle
2. **Large gap after** (>10min) - Service likely crashed during this battle
3. **Log file errors** - Battle logs contain:
   - `Unhandled exception`
   - `Worker.*error`
   - `TimeoutError`
   - `ConnectionError`
   - `NoneType object` errors

Otherwise classified as "skill loss" (strategy/decision issue).

## Monitoring Best Practices

1. **Check before major strategy changes**
   ```bash
   ./venv/bin/python check_stability_for_heartbeat.py
   ```

2. **Review after service restarts**
   - Check if restart was due to infrastructure or user action
   - Examine timeout losses around restart time

3. **Weekly stability review**
   - Look for memory trend over time
   - Identify recurring timeout patterns
   - Correlate crashes with specific opponents/teams

4. **Alert thresholds**
   - 10% timeout rate: Investigate
   - 20% timeout rate: Stop strategy tuning, fix infrastructure
   - 3+ restarts/day: Critical stability issue

## Troubleshooting

### High Timeout Rate

1. Check systemd logs for crashes:
   ```bash
   journalctl --user -u fouler-play.service -n 100
   ```

2. Review battle logs for errors:
   ```bash
   ls -lt logs/ | head -20
   grep -i "error\|exception" logs/battle-gen9ou-*.log | tail -50
   ```

3. Monitor memory during battles:
   ```bash
   watch -n 5 'ps aux | grep python | grep fouler'
   ```

### Large Battle Gaps

1. Check if MCTS search is hanging:
   - Look for CPU spikes that last >2min
   - Review search time budget in config

2. Verify network connectivity:
   - Pokemon Showdown websocket stability
   - Firewall/connection issues

### Service Restarts

1. Check crash patterns:
   ```bash
   journalctl --user -u fouler-play.service | grep -i "restart\|exit\|killed"
   ```

2. Review exit codes:
   - OOM killed (code 137) → Memory leak
   - Segfault (code 139) → Code bug
   - Normal exit → Intentional restart

## Files

- `stability_monitor.py` - Main monitoring script
- `check_stability_for_heartbeat.py` - Heartbeat integration helper  
- `stability_report.json` - Latest stability data (auto-generated)
- `~/.config/systemd/user/fouler-stability-monitor.service` - Systemd service
- `~/.config/systemd/user/fouler-stability-monitor.timer` - Systemd timer

## Future Enhancements

- [ ] Per-battle memory tracking (log RSS before/after each battle)
- [ ] CPU spike correlation with specific move searches
- [ ] Timeout pattern analysis (time-of-day, opponent-based)
- [ ] Automatic log archival when timeout detected
- [ ] Discord notification on critical health status
- [ ] Historical trend visualization
