#!/usr/bin/env python3
"""
Opens active Pokemon Showdown battles in Chrome for streaming
Monitors bot output and keeps browser windows synced with active battles
"""

import asyncio
import subprocess
import time
import json
from pathlib import Path

STREAM_STATUS_URL = "http://localhost:8777/status"
CHROME_BIN = "/opt/google/chrome/chrome"
SHOWDOWN_BASE = "https://play.pokemonshowdown.com/"

# Track which battles we have windows open for
active_windows = {}  # battle_id -> process


def extract_battle_ids(battle_info):
    """Extract battle IDs from battle_info string"""
    import re
    # Pattern: (battle-gen9ou-1234567890)
    pattern = r'\(battle-[a-z0-9]+-\d+)\)'
    matches = re.findall(pattern, battle_info)
    return matches


async def get_active_battles():
    """Query stream server for current battle info"""
    try:
        proc = await asyncio.create_subprocess_exec(
            'curl', '-s', STREAM_STATUS_URL,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE
        )
        stdout, _ = await proc.communicate()
        data = json.loads(stdout.decode())
        battle_info = data.get('battle_info', '')
        return extract_battle_ids(battle_info)
    except Exception as e:
        print(f"Error getting battles: {e}")
        return []


def open_battle_window(battle_id, x_offset=0):
    """Open a Chrome window for a specific battle"""
    url = f"{SHOWDOWN_BASE}{battle_id}"
    
    # Open Chrome in app mode (no UI chrome) at specific position
    # Position windows side by side: first at x=0, second at x=960
    cmd = [
        CHROME_BIN,
        f"--app={url}",
        f"--window-position={x_offset},0",
        "--window-size=960,720",
        "--user-data-dir=/tmp/showdown-viewer",
    ]
    
    print(f"Opening {battle_id} at x={x_offset}")
    proc = subprocess.Popen(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    return proc


async def main():
    print("Battle Viewer starting...")
    print("Monitoring for active battles...")
    
    while True:
        try:
            battle_ids = await get_active_battles()
            
            # Close windows for battles that ended
            for battle_id in list(active_windows.keys()):
                if battle_id not in battle_ids:
                    print(f"Battle {battle_id} ended, closing window")
                    proc = active_windows[battle_id]
                    proc.terminate()
                    del active_windows[battle_id]
            
            # Open windows for new battles
            for i, battle_id in enumerate(battle_ids):
                if battle_id not in active_windows:
                    x_offset = i * 960  # Side by side
                    proc = open_battle_window(battle_id, x_offset)
                    active_windows[battle_id] = proc
                    # Give Chrome time to open
                    await asyncio.sleep(2)
            
            await asyncio.sleep(5)  # Check every 5 seconds
            
        except KeyboardInterrupt:
            print("\nShutting down...")
            for proc in active_windows.values():
                proc.terminate()
            break
        except Exception as e:
            print(f"Error: {e}")
            await asyncio.sleep(5)


if __name__ == '__main__':
    asyncio.run(main())
