#!/usr/bin/env python3
"""
Fouler Play Bot Manager - Rigorous Process Control

Enforces:
- Only one instance per account at a time
- Clean shutdown verification
- Pre-flight checks before starting
- Battle count monitoring (MAX 2 per account)
"""

import os
import sys
import subprocess
import time
import json
import requests
from pathlib import Path

MAX_BATTLES_PER_ACCOUNT = 2
PROCESS_CHECK_TIMEOUT = 5  # seconds

class BotManager:
    def __init__(self, username, password=None):
        self.username = username
        self.password = password or os.getenv('PS_PASSWORD')
        self.project_dir = Path(__file__).parent
        
    def get_running_processes(self):
        """Get all running Fouler Play bot processes"""
        try:
            result = subprocess.run(
                ['ps', 'aux'],
                capture_output=True,
                text=True,
                timeout=PROCESS_CHECK_TIMEOUT
            )
            
            processes = []
            for line in result.stdout.split('\n'):
                if 'fp/main.py' in line and 'grep' not in line:
                    processes.append(line)
            
            return processes
        except subprocess.TimeoutExpired:
            print("⚠️  Process check timed out")
            return []
    
    def get_active_battles_count(self):
        """
        Query Pokemon Showdown to see how many battles this account has active.
        Returns: int (number of active battles)
        """
        # TODO: Implement PS API check or scrape user page
        # For now, return based on process count as proxy
        processes = self.get_running_processes()
        return len(processes)
    
    def pre_flight_checks(self):
        """
        Mandatory checks before starting bot.
        Returns: (bool, str) - (can_start, reason)
        """
        print("🔍 Running pre-flight checks...")
        
        # Check 1: No existing processes
        processes = self.get_running_processes()
        if processes:
            return False, f"❌ Found {len(processes)} existing bot process(es). Clean shutdown first."
        
        print("✅ No existing bot processes")
        
        # Check 2: Battle count on account
        battle_count = self.get_active_battles_count()
        if battle_count > 0:
            return False, f"❌ Account has {battle_count} active battle(s). Wait for completion or forfeit."
        
        print("✅ No active battles on account")
        
        # Check 3: Required files exist
        main_script = self.project_dir / 'fp' / 'main.py'
        if not main_script.exists():
            return False, f"❌ Main script not found: {main_script}"
        
        print("✅ Main script exists")
        
        # Check 4: Credentials available
        if not self.password:
            return False, "❌ No password provided (set PS_PASSWORD env var or pass to constructor)"
        
        print("✅ Credentials configured")
        
        return True, "All checks passed"
    
    def start(self, battles=1, **kwargs):
        """
        Start bot with rigorous verification.
        
        Args:
            battles: Number of battles to run (default 1, max 2 per account limit)
            **kwargs: Additional arguments to pass to bot
        """
        # Enforce hard limit
        if battles > MAX_BATTLES_PER_ACCOUNT:
            print(f"❌ Cannot start {battles} battles - MAX {MAX_BATTLES_PER_ACCOUNT} per account")
            return False
        
        # Pre-flight checks
        can_start, reason = self.pre_flight_checks()
        if not can_start:
            print(f"\n❌ PRE-FLIGHT CHECK FAILED\n{reason}\n")
            return False
        
        print(f"\n✅ PRE-FLIGHT CHECKS PASSED\n")
        print(f"🚀 Starting bot: {self.username}, {battles} battle(s)")
        
        # TODO: Implement actual bot start command
        # Example:
        # cmd = [
        #     'python', 'fp/main.py',
        #     '--username', self.username,
        #     '--password', self.password,
        #     '--battles', str(battles),
        # ]
        # subprocess.Popen(cmd)
        
        print("⚠️  Bot start command not yet implemented")
        return True
    
    def stop(self, force=False):
        """
        Stop bot with verification.
        
        Args:
            force: If True, use SIGKILL instead of SIGTERM
        """
        print("🛑 Stopping bot...")
        
        processes = self.get_running_processes()
        if not processes:
            print("✅ No processes to stop")
            return True
        
        print(f"Found {len(processes)} process(es) to stop")
        
        # Extract PIDs
        pids = []
        for proc_line in processes:
            parts = proc_line.split()
            if len(parts) > 1:
                pids.append(parts[1])  # PID is second column
        
        # Kill processes
        signal = 'SIGKILL' if force else 'SIGTERM'
        for pid in pids:
            print(f"Sending {signal} to PID {pid}")
            try:
                subprocess.run(['kill', f'-{signal}', pid], timeout=2)
            except subprocess.TimeoutExpired:
                print(f"⚠️  Kill command timed out for PID {pid}")
        
        # Wait and verify
        time.sleep(2)
        remaining = self.get_running_processes()
        
        if remaining:
            if not force:
                print(f"⚠️  {len(remaining)} process(es) still running. Retrying with force...")
                return self.stop(force=True)
            else:
                print(f"❌ FAILED TO STOP {len(remaining)} PROCESS(ES) EVEN WITH SIGKILL")
                for proc in remaining:
                    print(f"  {proc}")
                return False
        
        print("✅ All processes stopped and verified")
        return True
    
    def status(self):
        """Show current status"""
        processes = self.get_running_processes()
        battle_count = self.get_active_battles_count()
        
        print(f"\n{'='*60}")
        print(f"FOULER PLAY BOT STATUS")
        print(f"{'='*60}")
        print(f"Account: {self.username}")
        print(f"Running processes: {len(processes)}")
        print(f"Active battles: {battle_count}/{MAX_BATTLES_PER_ACCOUNT}")
        
        if processes:
            print(f"\nProcesses:")
            for proc in processes:
                print(f"  {proc}")
        
        if battle_count >= MAX_BATTLES_PER_ACCOUNT:
            print(f"\n⚠️  WARNING: At battle limit ({MAX_BATTLES_PER_ACCOUNT})")
        
        print(f"{'='*60}\n")


def main():
    import argparse
    
    parser = argparse.ArgumentParser(description='Fouler Play Bot Manager')
    parser.add_argument('action', choices=['start', 'stop', 'status', 'restart'])
    parser.add_argument('--username', default='dekubotbygoofy', help='PS username')
    parser.add_argument('--password', help='PS password (or use PS_PASSWORD env var)')
    parser.add_argument('--battles', type=int, default=1, help='Number of battles to run')
    parser.add_argument('--force', action='store_true', help='Force kill on stop')
    
    args = parser.parse_args()
    
    manager = BotManager(args.username, args.password)
    
    if args.action == 'status':
        manager.status()
    elif args.action == 'stop':
        manager.stop(force=args.force)
    elif args.action == 'start':
        manager.start(battles=args.battles)
    elif args.action == 'restart':
        manager.stop()
        time.sleep(1)
        manager.start(battles=args.battles)


if __name__ == '__main__':
    main()
