# Stability Monitor - Pipeline Integration Guide

## Quick Integration for `pipeline.py`

Add this function to `pipeline.py` to check infrastructure health before generating improvement plans:

```python
def check_infrastructure_health(self) -> dict:
    """
    Check stability report for infrastructure issues.
    Returns recommendations if timeout rate > 10% or infrastructure problems detected.
    """
    try:
        result = subprocess.run(
            [sys.executable, str(PROJECT_ROOT / "check_stability_for_heartbeat.py")],
            cwd=PROJECT_ROOT,
            capture_output=True,
            text=True,
            timeout=30
        )
        
        # Parse JSON output from the script
        lines = result.stdout.split('\n')
        json_start = None
        for i, line in enumerate(lines):
            if line.strip() == "JSON OUTPUT:":
                json_start = i + 1
                break
        
        if json_start:
            json_text = '\n'.join(lines[json_start:])
            return json.loads(json_text)
        
        return {"needs_infrastructure_focus": False, "recommendations": []}
        
    except Exception as e:
        print(f"⚠️ Could not check stability: {e}")
        return {"needs_infrastructure_focus": False, "recommendations": []}
```

## Usage in Improvement Plans

### Before Strategy Analysis

```python
def analyze_batch(self):
    """Main batch analysis with infrastructure check."""
    
    # Check infrastructure FIRST
    infra_health = self.check_infrastructure_health()
    
    if infra_health.get("needs_infrastructure_focus"):
        print("⚠️ Infrastructure issues detected - flagging in improvement plan")
        timeout_rate = infra_health.get("timeout_rate", 0)
        
        # Prepend infrastructure warning to analysis
        improvement_plan = f"""
# INFRASTRUCTURE ALERT

⚠️ {timeout_rate}% of recent losses are timeouts/crashes, not skill issues.

## Infrastructure Recommendations (Priority: HIGH)

"""
        for i, rec in enumerate(infra_health.get("recommendations", []), 1):
            improvement_plan += f"{i}. {rec}\n"
        
        improvement_plan += "\n---\n\n# Strategy Analysis\n\n"
        
        # Then continue with normal strategy analysis...
        # ... existing code ...
    else:
        # No infrastructure issues - focus on strategy
        improvement_plan = "# Strategy Improvement Plan\n\n"
        # ... existing code ...
```

### In Notification Messages

```python
def send_notification(self, message: str):
    """Send notification with infrastructure health prefix."""
    
    # Check if infrastructure issues should be highlighted
    infra = self.check_infrastructure_health()
    
    if infra.get("timeout_rate", 0) >= 10:
        prefix = f"⚠️ **Infrastructure Alert**: {infra['timeout_rate']}% timeout loss rate\n\n"
        message = prefix + message
    
    # Send to Discord/OpenClaw
    # ... existing notification code ...
```

## Example: Full Pipeline Integration

```python
class Pipeline:
    def __init__(self):
        self.analyzer = BatchAnalyzer()
        # ... existing init ...
    
    def check_infrastructure_health(self) -> dict:
        """Check stability monitor report."""
        try:
            result = subprocess.run(
                [sys.executable, str(PROJECT_ROOT / "check_stability_for_heartbeat.py")],
                cwd=PROJECT_ROOT,
                capture_output=True,
                text=True,
                timeout=30
            )
            
            lines = result.stdout.split('\n')
            json_start = None
            for i, line in enumerate(lines):
                if line.strip() == "JSON OUTPUT:":
                    json_start = i + 1
                    break
            
            if json_start:
                json_text = '\n'.join(lines[json_start:])
                return json.loads(json_text)
            
            return {"needs_infrastructure_focus": False, "recommendations": []}
            
        except Exception as e:
            print(f"⚠️ Could not check stability: {e}")
            return {"needs_infrastructure_focus": False, "recommendations": []}
    
    def run_analysis(self, battle_count: int = 30):
        """Run batch analysis with infrastructure health check."""
        
        print(f"📊 Analyzing last {battle_count} battles...")
        
        # 1. Check infrastructure health FIRST
        print("🔍 Checking infrastructure stability...")
        infra = self.check_infrastructure_health()
        
        # 2. Run batch analysis
        results = self.analyzer.analyze_recent_battles(battle_count)
        
        # 3. Generate improvement plan
        plan = self.generate_improvement_plan(results, infra)
        
        # 4. Send notification
        self.send_notification(plan, infra)
        
        return plan
    
    def generate_improvement_plan(self, results: dict, infra: dict) -> str:
        """Generate improvement plan with infrastructure context."""
        
        plan = []
        
        # Header
        plan.append("# Fouler Play Improvement Plan")
        plan.append(f"*Generated: {datetime.now().strftime('%Y-%m-%d %H:%M')}*\n")
        
        # INFRASTRUCTURE SECTION (if issues detected)
        if infra.get("needs_infrastructure_focus"):
            timeout_rate = infra.get("timeout_rate", 0)
            plan.append("## 🔴 Infrastructure Issues Detected\n")
            plan.append(f"**Timeout Loss Rate: {timeout_rate}%**")
            plan.append(f"*{int(timeout_rate * results['total_losses'] / 100)} out of {results['total_losses']} losses are infrastructure-related*\n")
            
            plan.append("### Action Items (Priority: HIGH)\n")
            for i, rec in enumerate(infra.get("recommendations", []), 1):
                plan.append(f"{i}. {rec}")
            
            plan.append("\n**⚠️ Recommendation: Address infrastructure stability before strategy tuning**\n")
            plan.append("---\n")
        
        # STRATEGY SECTION (existing logic)
        plan.append("## Strategy Analysis\n")
        plan.append(f"**Record:** {results['wins']}W - {results['losses']}L")
        plan.append(f"**Win Rate:** {results['win_rate']:.1f}%\n")
        
        # ... rest of existing improvement plan generation ...
        
        return "\n".join(plan)
    
    def send_notification(self, plan: str, infra: dict):
        """Send notification with infrastructure health context."""
        
        # Build message
        message = plan
        
        # Add health status emoji
        health = infra.get("health", "✅ HEALTHY")
        if "🔴" in health:
            emoji = "🔴"
        elif "⚠️" in health:
            emoji = "⚠️"
        else:
            emoji = "✅"
        
        # Send via existing notification system
        print(f"\n{emoji} Sending notification...")
        # ... existing Discord/OpenClaw notification code ...
```

## Command-Line Testing

Test the integration before deploying:

```bash
# 1. Generate fresh stability report
cd /home/ryan/projects/fouler-play
./venv/bin/python stability_monitor.py

# 2. Check what heartbeat would see
./venv/bin/python check_stability_for_heartbeat.py

# 3. Test pipeline with stability check
./venv/bin/python pipeline.py analyze
```

## Automated Workflow

The systemd timer runs every 30 minutes:
1. `fouler-stability-monitor.timer` triggers `fouler-stability-monitor.service`
2. Service runs `stability_monitor.py`
3. Updates `stability_report.json`
4. Pipeline reads report via `check_stability_for_heartbeat.py`

## Decision Logic

```
IF timeout_rate >= 20%:
    → 🔴 CRITICAL: Stop strategy work, fix infrastructure
    
ELSE IF timeout_rate >= 10%:
    → ⚠️ WARNING: Flag in improvement plan
    → "X% of losses are timeouts, not skill issues"
    
ELSE IF timeout_rate < 10%:
    → ✅ HEALTHY: Focus on strategy improvements
```

## Example Output

### With Infrastructure Issues (>10% timeout rate)

```
# Fouler Play Improvement Plan
*Generated: 2026-02-14 21:40*

## 🔴 Infrastructure Issues Detected

**Timeout Loss Rate: 15.2%**
*7 out of 46 losses are infrastructure-related*

### Action Items (Priority: HIGH)

1. ⚠️ Infrastructure issue detected: 15.2% of losses are timeouts
   (7 out of 46 losses). Not all losses are skill-related. Consider:
   1) Reviewing service stability
   2) Checking for memory leaks
   3) Investigating battle loop hangs.

2. ⚠️ 32 battle gaps >5min detected (avg gap: 892.3s).
   Indicates periodic hangs or slowdowns in battle loop.

**⚠️ Recommendation: Address infrastructure stability before strategy tuning**

---

## Strategy Analysis

**Record:** 54W - 46L
**Win Rate:** 54.0%

...
```

### Without Infrastructure Issues (<10% timeout rate)

```
# Fouler Play Improvement Plan
*Generated: 2026-02-14 21:40*

## Strategy Analysis

**Record:** 54W - 46L
**Win Rate:** 54.0%
**Infrastructure Health:** ✅ HEALTHY

...
```

## Files Modified

- `pipeline.py` - Add `check_infrastructure_health()` method
- `pipeline.py` - Modify `generate_improvement_plan()` to include infra context
- `pipeline.py` - Update `send_notification()` to highlight infra issues

## Next Steps

1. Copy the integration code into `pipeline.py`
2. Test with: `python pipeline.py analyze`
3. Verify infrastructure warnings appear when timeout rate >10%
4. Deploy and let systemd timer maintain fresh stability data
