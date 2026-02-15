#!/usr/bin/env python3
"""
Stability checker for heartbeat integration.
Returns infrastructure health recommendations for improvement plans.
"""

import json
import sys
from pathlib import Path
from typing import Dict, List, Optional


def load_stability_report(project_root: str = "/home/ryan/projects/fouler-play") -> Optional[Dict]:
    """Load the latest stability report."""
    report_path = Path(project_root) / "stability_report.json"
    
    if not report_path.exists():
        return None
    
    try:
        with open(report_path, 'r') as f:
            return json.load(f)
    except Exception as e:
        print(f"Error loading stability report: {e}", file=sys.stderr)
        return None


def get_infrastructure_recommendations(report: Dict) -> List[str]:
    """
    Analyze stability report and return infrastructure-related recommendations.
    These should be added to improvement plans when infrastructure is degraded.
    """
    if not report or 'stability' not in report:
        return []
    
    s = report['stability']
    recommendations = []
    
    # Timeout loss rate check
    timeout_rate = s.get('timeout_loss_rate', 0.0)
    if timeout_rate >= 20:
        recommendations.append(
            f"🔴 CRITICAL: {timeout_rate}% of losses are timeouts/crashes. "
            "Prioritize infrastructure stability before tuning strategy. "
            "Check service logs and resource usage."
        )
    elif timeout_rate >= 10:
        recommendations.append(
            f"⚠️ Infrastructure issue detected: {timeout_rate}% of losses are timeouts "
            f"({s.get('timeout_losses', 0)} out of {s.get('total_losses', 0)} losses). "
            "Not all losses are skill-related. Consider: 1) Reviewing service stability, "
            "2) Checking for memory leaks, 3) Investigating battle loop hangs."
        )
    
    # Service restart check
    restarts = s.get('service_restarts_24h', 0)
    if restarts > 5:
        recommendations.append(
            f"🔴 Service instability: {restarts} restarts in 24h. "
            "Investigate crash causes before implementing strategy changes."
        )
    elif restarts > 2:
        recommendations.append(
            f"⚠️ Multiple service restarts detected ({restarts} in 24h). "
            "Monitor for recurring crashes that may affect battle completion."
        )
    
    # Memory trend check
    memory_trend = s.get('memory_trend', 'unknown')
    if 'critical' in memory_trend.lower():
        recommendations.append(
            f"🔴 Memory usage critical ({s.get('max_memory_mb', 0)} MB). "
            "Possible memory leak. Investigate resource cleanup in battle loop."
        )
    elif 'high' in memory_trend.lower():
        recommendations.append(
            f"⚠️ Elevated memory usage ({s.get('max_memory_mb', 0)} MB). "
            "Monitor for memory leaks during extended runs."
        )
    
    # Battle gap analysis
    timeout_gaps = s.get('timeout_gaps_count', 0)
    avg_gap = s.get('avg_gap_seconds', 0)
    if timeout_gaps > 10:
        recommendations.append(
            f"⚠️ {timeout_gaps} battle gaps >5min detected (avg gap: {round(avg_gap, 1)}s). "
            "Indicates periodic hangs or slowdowns in battle loop."
        )
    
    return recommendations


def print_stability_summary(report: Dict):
    """Print a concise stability summary for heartbeat logs."""
    if not report or 'stability' not in report:
        print("⚠️ No stability data available")
        return
    
    s = report['stability']
    health = s.get('health', 'UNKNOWN')
    
    print(f"\n{'='*60}")
    print(f"STABILITY CHECK: {health}")
    print(f"{'='*60}")
    print(f"Last 100 battles: {s.get('wins', 0)}W - {s.get('total_losses', 0)}L")
    print(f"Timeout losses: {s.get('timeout_losses', 0)} ({s.get('timeout_loss_rate', 0)}%)")
    print(f"Service restarts (24h): {s.get('service_restarts_24h', 0)}")
    print(f"Memory: {s.get('avg_memory_mb', 0)}MB avg, {s.get('max_memory_mb', 0)}MB max ({s.get('memory_trend', 'unknown')})")
    
    recs = get_infrastructure_recommendations(report)
    if recs:
        print(f"\n⚠️ INFRASTRUCTURE RECOMMENDATIONS:")
        for i, rec in enumerate(recs, 1):
            print(f"  {i}. {rec}")
    else:
        print(f"\n✅ Infrastructure healthy - focus on strategy improvements")
    
    print(f"{'='*60}\n")


def main():
    """Main entry point for command-line usage."""
    report = load_stability_report()
    
    if not report:
        print("⚠️ No stability report found. Run stability_monitor.py first.", file=sys.stderr)
        sys.exit(1)
    
    # Print summary
    print_stability_summary(report)
    
    # Return recommendations as JSON for programmatic use
    recs = get_infrastructure_recommendations(report)
    output = {
        "health": report['stability'].get('health', 'UNKNOWN'),
        "timeout_rate": report['stability'].get('timeout_loss_rate', 0.0),
        "recommendations": recs,
        "needs_infrastructure_focus": len(recs) > 0
    }
    
    print("\nJSON OUTPUT:")
    print(json.dumps(output, indent=2))
    
    # Exit code based on health
    if '🔴' in output['health']:
        sys.exit(2)
    elif '⚠️' in output['health'] or recs:
        sys.exit(1)
    else:
        sys.exit(0)


if __name__ == "__main__":
    main()
