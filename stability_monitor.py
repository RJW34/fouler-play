#!/usr/bin/env python3
"""
Fouler Play Stability Monitor
Tracks infrastructure health vs. skill issues for the Pokemon battle bot.
"""

import json
import os
import sys
import subprocess
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Dict, List, Optional
import re
import psutil


class StabilityMonitor:
    def __init__(self, project_root: str = "/home/ryan/projects/fouler-play"):
        self.project_root = Path(project_root)
        self.battle_stats_file = self.project_root / "battle_stats.json"
        self.stability_report_file = self.project_root / "stability_report.json"
        self.log_dir = self.project_root / "logs"
        self.service_name = "fouler-play.service"
        
    def load_battle_stats(self) -> List[Dict]:
        """Load battle statistics from JSON file."""
        if not self.battle_stats_file.exists():
            return []
        
        try:
            with open(self.battle_stats_file, 'r') as f:
                data = json.load(f)
                return data.get('battles', [])
        except Exception as e:
            print(f"Error loading battle stats: {e}")
            return []
    
    def analyze_battle_gaps(self, battles: List[Dict]) -> Dict:
        """
        Analyze time gaps between battles to detect hangs/timeouts.
        >5 minutes between battles suggests a timeout or hang.
        """
        if len(battles) < 2:
            return {
                "avg_gap_seconds": 0,
                "max_gap_seconds": 0,
                "timeout_gaps": 0,
                "gaps_over_5min": []
            }
        
        gaps = []
        timeout_gaps = []
        
        for i in range(1, len(battles)):
            prev_time = datetime.fromisoformat(battles[i-1]['timestamp'].replace('+00:00', '+00:00'))
            curr_time = datetime.fromisoformat(battles[i]['timestamp'].replace('+00:00', '+00:00'))
            gap_seconds = (curr_time - prev_time).total_seconds()
            
            gaps.append(gap_seconds)
            
            # Flag gaps >5 minutes as potential timeouts
            if gap_seconds > 300:
                timeout_gaps.append({
                    "before_battle": battles[i-1]['battle_id'],
                    "after_battle": battles[i]['battle_id'],
                    "gap_seconds": gap_seconds,
                    "gap_minutes": round(gap_seconds / 60, 1)
                })
        
        return {
            "avg_gap_seconds": round(sum(gaps) / len(gaps), 1) if gaps else 0,
            "max_gap_seconds": max(gaps) if gaps else 0,
            "timeout_gaps": len(timeout_gaps),
            "gaps_over_5min": timeout_gaps[:10]  # Keep last 10 for debugging
        }
    
    def detect_timeout_losses(self, battles: List[Dict]) -> Dict:
        """
        Analyze losses to separate timeout losses from skill losses.
        Uses multiple signals:
        1. Large battle time gaps near losses
        2. Log file analysis for timeout errors
        3. Battle duration analysis
        """
        timeout_losses = []
        skill_losses = []
        unknown_losses = []
        
        for i, battle in enumerate(battles):
            if battle['result'] != 'loss':
                continue
            
            # Check for timeout signals
            is_timeout = False
            timeout_reason = None
            
            # Signal 1: Large gap before this battle
            if i > 0:
                prev_time = datetime.fromisoformat(battles[i-1]['timestamp'].replace('+00:00', '+00:00'))
                curr_time = datetime.fromisoformat(battle['timestamp'].replace('+00:00', '+00:00'))
                gap = (curr_time - prev_time).total_seconds()
                
                if gap > 600:  # 10+ min gap likely indicates timeout/restart
                    is_timeout = True
                    timeout_reason = f"large_gap_before_{int(gap/60)}min"
            
            # Signal 2: Check if next battle also has large gap (suggests crash during this battle)
            if i < len(battles) - 1:
                curr_time = datetime.fromisoformat(battle['timestamp'].replace('+00:00', '+00:00'))
                next_time = datetime.fromisoformat(battles[i+1]['timestamp'].replace('+00:00', '+00:00'))
                gap = (next_time - curr_time).total_seconds()
                
                if gap > 600:
                    is_timeout = True
                    timeout_reason = f"large_gap_after_{int(gap/60)}min"
            
            # Signal 3: Check log files for error patterns
            log_pattern = self._check_battle_logs(battle['battle_id'])
            if log_pattern:
                is_timeout = True
                timeout_reason = log_pattern
            
            if is_timeout:
                timeout_losses.append({
                    "battle_id": battle['battle_id'],
                    "timestamp": battle['timestamp'],
                    "reason": timeout_reason
                })
            elif timeout_reason is None:
                # Could analyze further, but for now mark as skill loss
                skill_losses.append(battle['battle_id'])
            else:
                unknown_losses.append(battle['battle_id'])
        
        return {
            "timeout_losses": timeout_losses,
            "skill_losses": skill_losses,
            "unknown_losses": unknown_losses,
            "timeout_loss_count": len(timeout_losses),
            "skill_loss_count": len(skill_losses),
            "unknown_loss_count": len(unknown_losses)
        }
    
    def _check_battle_logs(self, battle_id: str) -> Optional[str]:
        """Check battle-specific logs for timeout/error patterns."""
        if not self.log_dir.exists():
            return None
        
        # Find log files for this battle
        log_files = list(self.log_dir.glob(f"{battle_id}*.log"))
        
        error_patterns = [
            (r"Unhandled exception", "unhandled_exception"),
            (r"Worker.*error", "worker_error"),
            (r"TimeoutError", "timeout_error"),
            (r"ConnectionError", "connection_error"),
            (r"'NoneType' object", "nonetype_error")
        ]
        
        for log_file in log_files:
            try:
                with open(log_file, 'r') as f:
                    content = f.read()
                    for pattern, reason in error_patterns:
                        if re.search(pattern, content, re.IGNORECASE):
                            return reason
            except Exception:
                continue
        
        return None
    
    def analyze_memory_cpu(self, battles: List[Dict]) -> Dict:
        """
        Analyze memory and CPU trends.
        For now, get current snapshot. In future, could track per-battle.
        """
        try:
            # Get current process info
            process_name = "python"
            memory_usage = []
            cpu_usage = []
            
            for proc in psutil.process_iter(['name', 'memory_info', 'cpu_percent']):
                if 'python' in proc.info['name'].lower():
                    try:
                        mem_mb = proc.info['memory_info'].rss / 1024 / 1024
                        cpu = proc.info['cpu_percent']
                        if mem_mb > 100:  # Filter out small processes
                            memory_usage.append(mem_mb)
                            cpu_usage.append(cpu)
                    except (psutil.NoSuchProcess, psutil.AccessDenied):
                        continue
            
            avg_memory = sum(memory_usage) / len(memory_usage) if memory_usage else 0
            max_memory = max(memory_usage) if memory_usage else 0
            avg_cpu = sum(cpu_usage) / len(cpu_usage) if cpu_usage else 0
            
            # Simple trend detection: if memory is unusually high, flag it
            memory_trend = "stable"
            if max_memory > 2000:
                memory_trend = "⚠️ high"
            elif max_memory > 4000:
                memory_trend = "🔴 critical"
            
            return {
                "avg_memory_mb": round(avg_memory, 1),
                "max_memory_mb": round(max_memory, 1),
                "avg_cpu_percent": round(avg_cpu, 1),
                "memory_trend": memory_trend,
                "sample_time": datetime.now(timezone.utc).isoformat()
            }
        except Exception as e:
            return {
                "error": str(e),
                "memory_trend": "unknown"
            }
    
    def count_service_restarts(self, hours: int = 24) -> int:
        """Count how many times the systemd service restarted in last N hours."""
        try:
            since_time = f"{hours} hours ago"
            result = subprocess.run(
                ['journalctl', '--user', '-u', self.service_name, 
                 '--since', since_time, '--no-pager'],
                capture_output=True,
                text=True,
                timeout=10
            )
            
            # Count restart messages
            restart_count = 0
            for line in result.stdout.split('\n'):
                if 'restart' in line.lower() and 'scheduled' in line.lower():
                    restart_count += 1
            
            return restart_count
        except Exception as e:
            print(f"Error checking service restarts: {e}")
            return -1
    
    def calculate_health_status(self, timeout_rate: float, restarts: int, memory_trend: str) -> str:
        """Calculate overall health status based on metrics."""
        issues = []
        
        if timeout_rate >= 20:
            issues.append("🔴 CRITICAL timeout rate")
        elif timeout_rate >= 10:
            issues.append("⚠️ WARNING timeout rate")
        
        if restarts > 5:
            issues.append("🔴 frequent restarts")
        elif restarts > 2:
            issues.append("⚠️ multiple restarts")
        
        if "critical" in memory_trend.lower():
            issues.append("🔴 memory critical")
        elif "high" in memory_trend.lower():
            issues.append("⚠️ memory high")
        
        if not issues:
            return "✅ HEALTHY"
        elif any("🔴" in i for i in issues):
            return "🔴 CRITICAL - " + ", ".join(issues)
        else:
            return "⚠️ WARNING - " + ", ".join(issues)
    
    def generate_report(self, recent_count: int = 100) -> Dict:
        """Generate comprehensive stability report."""
        battles = self.load_battle_stats()
        
        if not battles:
            return {
                "error": "No battle data found",
                "timestamp": datetime.now(timezone.utc).isoformat()
            }
        
        # Analyze recent battles
        recent_battles = battles[-recent_count:] if len(battles) > recent_count else battles
        total_battles = len(recent_battles)
        
        # Count wins/losses
        wins = sum(1 for b in recent_battles if b['result'] == 'win')
        losses = sum(1 for b in recent_battles if b['result'] == 'loss')
        
        # Analyze gaps
        gap_analysis = self.analyze_battle_gaps(recent_battles)
        
        # Detect timeout vs skill losses
        loss_analysis = self.detect_timeout_losses(recent_battles)
        
        # Memory/CPU analysis
        resource_analysis = self.analyze_memory_cpu(recent_battles)
        
        # Service restart count
        service_restarts = self.count_service_restarts(hours=24)
        
        # Calculate timeout loss rate
        timeout_loss_rate = 0.0
        if losses > 0:
            timeout_loss_rate = (loss_analysis['timeout_loss_count'] / losses) * 100
        
        # Overall health
        health_status = self.calculate_health_status(
            timeout_loss_rate,
            service_restarts,
            resource_analysis.get('memory_trend', 'unknown')
        )
        
        report = {
            "generated_at": datetime.now(timezone.utc).isoformat(),
            "stability": {
                "battles_last_100": total_battles,
                "wins": wins,
                "total_losses": losses,
                "timeout_losses": loss_analysis['timeout_loss_count'],
                "skill_losses": loss_analysis['skill_loss_count'],
                "unknown_losses": loss_analysis['unknown_loss_count'],
                "timeout_loss_rate": round(timeout_loss_rate, 1),
                "memory_trend": resource_analysis.get('memory_trend', 'unknown'),
                "avg_memory_mb": resource_analysis.get('avg_memory_mb', 0),
                "max_memory_mb": resource_analysis.get('max_memory_mb', 0),
                "avg_cpu_percent": resource_analysis.get('avg_cpu_percent', 0),
                "avg_gap_seconds": gap_analysis['avg_gap_seconds'],
                "max_gap_seconds": gap_analysis['max_gap_seconds'],
                "timeout_gaps_count": gap_analysis['timeout_gaps'],
                "service_restarts_24h": service_restarts,
                "health": health_status
            },
            "details": {
                "recent_timeout_losses": loss_analysis['timeout_losses'][-5:],
                "large_gaps": gap_analysis['gaps_over_5min']
            }
        }
        
        return report
    
    def save_report(self, report: Dict):
        """Save stability report to JSON file."""
        try:
            with open(self.stability_report_file, 'w') as f:
                json.dump(report, f, indent=2)
            print(f"✅ Stability report saved to {self.stability_report_file}")
        except Exception as e:
            print(f"❌ Error saving report: {e}")
            sys.exit(1)
    
    def print_summary(self, report: Dict):
        """Print human-readable summary."""
        if 'error' in report:
            print(f"❌ {report['error']}")
            return
        
        s = report['stability']
        print("\n" + "="*60)
        print("FOULER PLAY STABILITY REPORT")
        print("="*60)
        print(f"Status: {s['health']}")
        print(f"Analyzed: Last {s['battles_last_100']} battles")
        print(f"Record: {s['wins']}W - {s['total_losses']}L")
        print()
        print("LOSS BREAKDOWN:")
        print(f"  Timeout losses:  {s['timeout_losses']} ({s['timeout_loss_rate']}%)")
        print(f"  Skill losses:    {s['skill_losses']}")
        print(f"  Unknown losses:  {s['unknown_losses']}")
        print()
        print("INFRASTRUCTURE:")
        print(f"  Service restarts (24h): {s['service_restarts_24h']}")
        print(f"  Avg battle gap: {s['avg_gap_seconds']}s")
        print(f"  Max battle gap: {s['max_gap_seconds']}s ({round(s['max_gap_seconds']/60, 1)}min)")
        print(f"  Timeout gaps (>5min): {s['timeout_gaps_count']}")
        print()
        print("RESOURCES:")
        print(f"  Memory trend: {s['memory_trend']}")
        print(f"  Avg memory: {s['avg_memory_mb']} MB")
        print(f"  Max memory: {s['max_memory_mb']} MB")
        print(f"  Avg CPU: {s['avg_cpu_percent']}%")
        print()
        
        if s['timeout_loss_rate'] >= 10:
            print("⚠️  HIGH TIMEOUT RATE DETECTED")
            print(f"   {s['timeout_loss_rate']}% of losses are infrastructure issues, not skill issues")
            print("   Recommendation: Investigate service stability before tuning strategy")
        
        print("="*60 + "\n")


def main():
    monitor = StabilityMonitor()
    
    print("🔍 Generating stability report...")
    report = monitor.generate_report(recent_count=100)
    
    monitor.save_report(report)
    monitor.print_summary(report)
    
    # Exit with non-zero if critical
    if '🔴' in report['stability']['health']:
        sys.exit(2)
    elif '⚠️' in report['stability']['health']:
        sys.exit(1)
    else:
        sys.exit(0)


if __name__ == "__main__":
    main()
