#!/usr/bin/env python3
"""
Event Handler System for JARVIS Monolith
Listens for critical events (batch completion, crashes, threshold breaches)
Triggers immediate actions without time-based polling
"""

import json
import subprocess
import requests
from datetime import datetime
from pathlib import Path

from infrastructure.event_queue_lib import queue_event as _queue_event

DISCORD_WORKSPACE = "1466642788472066296"
BATTLE_STATS_PATH = Path("/home/ryan/projects/fouler-play/battle_stats.json")
UNIFIED_PERF_PATH = Path("/home/ryan/projects/UNIFIED_PERFORMANCE.json")


class EventHandler:
    """Central event dispatcher for system events"""
    
    @staticmethod
    def log_event(event_type: str, payload: dict):
        """Log event to local file + Discord"""
        timestamp = datetime.now().isoformat()
        event = {
            "type": event_type,
            "timestamp": timestamp,
            "payload": payload
        }
        
        # Local log
        log_file = Path("/home/ryan/projects/fouler-play/logs/events.log")
        with open(log_file, "a") as f:
            f.write(json.dumps(event) + "\n")
        
        print(f"[EVENT] {event_type}: {payload}")
    
    @staticmethod
    def on_batch_analysis_complete(batch_num: int, wr_pct: float, issues: list):
        """Fired when batch analysis finishes (from webhook or file watch)"""
        EventHandler.log_event("BATCH_ANALYSIS_COMPLETE", {
            "batch": batch_num,
            "wr": wr_pct,
            "issues": issues
        })
        
        # Immediately trigger unified performance update
        EventHandler.update_unified_performance()
        
        # Queue to Discord
        msg = f"✅ Batch {batch_num} analyzed: {wr_pct}% WR"
        if issues:
            msg += f" | Issues found: {', '.join(issues[:2])}"
        _queue_event("batch_analyzed", DISCORD_WORKSPACE, msg)
    
    @staticmethod
    def on_wr_drop(machine: str, new_wr: float, previous_wr: float, delta: float):
        """Fired when win rate drops >5% (threshold breach)"""
        EventHandler.log_event("WR_DROP", {
            "machine": machine,
            "from": previous_wr,
            "to": new_wr,
            "delta": delta
        })
        
        _queue_event("wr_drop", DISCORD_WORKSPACE,
                     f"⚠️ {machine} WR dropped {delta:.1f}% ({previous_wr:.1f}% → {new_wr:.1f}%)")
    
    @staticmethod
    def on_process_crash(process_name: str, pid: int, error_log: str = ""):
        """Fired when critical process dies"""
        EventHandler.log_event("PROCESS_CRASH", {
            "process": process_name,
            "pid": pid,
            "error": error_log[:200]  # First 200 chars
        })
        
        _queue_event("process_crash", DISCORD_WORKSPACE,
                     f"🚨 {process_name} crashed (PID {pid}). Systemd will auto-restart.",
                     precondition_check_fn="bot_is_dead",
                     dedup_window_sec=60)
    
    @staticmethod
    def on_ssh_failure(machine: str, error: str):
        """Fired when SSH to remote machine fails"""
        EventHandler.log_event("SSH_FAILURE", {
            "machine": machine,
            "error": error[:100]
        })
        
        _queue_event("ssh_failure", DISCORD_WORKSPACE,
                     f"⚠️ Cannot reach {machine} via SSH. Check network/gateway.")
    
    @staticmethod
    def update_unified_performance():
        """Immediately recalculate and update UNIFIED_PERFORMANCE.json"""
        try:
            with open(BATTLE_STATS_PATH) as f:
                stats = json.load(f)
            
            total_battles = stats.get("battles", 0)
            total_wins = stats.get("wins", 0)
            combined_wr = total_wins / total_battles if total_battles > 0 else 0
            
            perf_data = {
                "lastUpdated": datetime.now().isoformat(),
                "combined_stats": {
                    "total_battles": total_battles,
                    "total_wins": total_wins,
                    "combined_wr": combined_wr
                },
                "teams": stats.get("teams", {})
            }
            
            with open(UNIFIED_PERF_PATH, "w") as f:
                json.dump(perf_data, f, indent=2)
            
            EventHandler.log_event("UNIFIED_PERF_UPDATE", {
                "battles": total_battles,
                "wr": f"{combined_wr:.1%}"
            })
        except Exception as e:
            EventHandler.log_event("UNIFIED_PERF_UPDATE_FAILED", {"error": str(e)})
    
    @staticmethod
    def post_to_discord(message: str):
        """DISABLED: Events post to internal logs only, not workspace
        Will be routed through event queue system when implemented.
        """
        pass  # No-op: don't post workspace alerts


# Example usage (called from webhooks or file watchers)
if __name__ == "__main__":
    # Test event firing
    EventHandler.on_batch_analysis_complete(
        batch_num=7,
        wr_pct=58.5,
        issues=["Stall team -5% vs meta", "Hazard setup delayed"]
    )
    EventHandler.on_wr_drop("ubunztu", 0.56, 0.58, -0.02)
