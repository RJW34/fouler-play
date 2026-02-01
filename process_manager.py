#!/usr/bin/env python3
"""
process_manager.py

Proper process tracking for Fouler Play.
Creates PID files for all spawned processes.
Ensures clean startup and shutdown.
"""

import os
import sys
import signal
import json
import time
import subprocess
from pathlib import Path

PID_DIR = Path(__file__).parent / ".pids"
PID_DIR.mkdir(exist_ok=True)

def write_pid(name, pid):
    """Write PID to tracking file"""
    pid_file = PID_DIR / f"{name}.pid"
    with open(pid_file, 'w') as f:
        json.dump({
            'pid': pid,
            'name': name,
            'started_at': time.time(),
            'command': ' '.join(sys.argv) if name == 'main' else 'spawned'
        }, f)
    print(f"Tracked {name} process: PID {pid}")

def read_pids():
    """Read all tracked PIDs"""
    pids = {}
    if not PID_DIR.exists():
        return pids
    
    for pid_file in PID_DIR.glob("*.pid"):
        try:
            with open(pid_file) as f:
                data = json.load(f)
                pids[pid_file.stem] = data
        except Exception as e:
            print(f"Error reading {pid_file}: {e}")
    
    return pids

def is_running(pid):
    """Check if process is running"""
    try:
        os.kill(pid, 0)
        return True
    except OSError:
        return False

def kill_process(name, pid, force=False):
    """Kill a tracked process"""
    if not is_running(pid):
        print(f"Process {name} (PID {pid}) already stopped")
        return True
    
    try:
        sig = signal.SIGKILL if force else signal.SIGTERM
        os.kill(pid, sig)
        print(f"Sent {'SIGKILL' if force else 'SIGTERM'} to {name} (PID {pid})")
        
        # Wait for process to die
        for _ in range(10):
            if not is_running(pid):
                print(f"Process {name} stopped")
                return True
            time.sleep(0.5)
        
        if force:
            print(f"Process {name} still running after SIGKILL")
            return False
        else:
            # Try force kill
            print(f"Process {name} didn't respond to SIGTERM, using SIGKILL")
            return kill_process(name, pid, force=True)
    
    except Exception as e:
        print(f"Error killing {name}: {e}")
        return False

def cleanup_pid_file(name):
    """Remove PID file"""
    pid_file = PID_DIR / f"{name}.pid"
    if pid_file.exists():
        pid_file.unlink()
        print(f"Cleaned up PID file for {name}")

def stop_all():
    """Stop all tracked processes"""
    pids = read_pids()
    
    if not pids:
        print("No tracked processes found")
        return True
    
    print(f"\nStopping {len(pids)} tracked processes...")
    
    success = True
    for name, data in pids.items():
        pid = data['pid']
        print(f"\nStopping {name} (PID {pid})...")
        
        if kill_process(name, pid):
            cleanup_pid_file(name)
        else:
            success = False
            print(f"⚠️  Failed to stop {name}")
    
    # Also check for orphaned Python processes
    print("\nChecking for orphaned bot processes...")
    try:
        result = subprocess.run(
            ['ps', 'aux'],
            capture_output=True,
            text=True
        )
        
        orphans = []
        for line in result.stdout.split('\n'):
            if 'fouler-play' in line and 'python' in line and 'run.py' in line:
                parts = line.split()
                if len(parts) > 1:
                    pid = int(parts[1])
                    # Check if this PID was tracked
                    if not any(d['pid'] == pid for d in pids.values()):
                        orphans.append(pid)
        
        if orphans:
            print(f"Found {len(orphans)} orphaned processes: {orphans}")
            for pid in orphans:
                print(f"Killing orphaned process {pid}...")
                try:
                    os.kill(pid, signal.SIGKILL)
                except Exception as e:
                    print(f"Failed to kill {pid}: {e}")
        else:
            print("No orphaned processes found")
    
    except Exception as e:
        print(f"Error checking for orphans: {e}")
    
    return success

def status():
    """Show status of all tracked processes"""
    pids = read_pids()
    
    if not pids:
        print("No tracked processes")
        return
    
    print(f"\nTracked processes ({len(pids)}):\n")
    
    running = 0
    dead = 0
    
    for name, data in pids.items():
        pid = data['pid']
        alive = is_running(pid)
        status = "✅ RUNNING" if alive else "❌ DEAD"
        
        if alive:
            running += 1
        else:
            dead += 1
        
        started = time.strftime('%Y-%m-%d %H:%M:%S', time.localtime(data['started_at']))
        print(f"{status} - {name} (PID {pid}, started {started})")
    
    print(f"\nSummary: {running} running, {dead} dead")
    
    # Clean up dead processes
    if dead > 0:
        print("\nCleaning up dead process files...")
        for name, data in pids.items():
            if not is_running(data['pid']):
                cleanup_pid_file(name)

if __name__ == '__main__':
    if len(sys.argv) < 2:
        print("Usage: process_manager.py [status|stop]")
        sys.exit(1)
    
    command = sys.argv[1]
    
    if command == 'status':
        status()
    elif command == 'stop':
        if stop_all():
            print("\n✅ All processes stopped successfully")
            sys.exit(0)
        else:
            print("\n⚠️  Some processes failed to stop")
            sys.exit(1)
    else:
        print(f"Unknown command: {command}")
        sys.exit(1)
