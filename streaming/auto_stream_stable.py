#!/usr/bin/env python3
"""
Fouler Play Auto-Stream - Stable Version
Focus: Reliable 2-battle display with Pokemon Showdown login

Key features:
- Uses single Chrome profile (stays logged in)
- Opens 2 battle windows side-by-side
- Monitors monitor.log for battle IDs
- Streams via ffmpeg to Twitch
"""

import subprocess
import time
import re
import os
import signal
import sys

# Configuration
MONITOR_LOG = "/home/ryan/projects/fouler-play/monitor.log"
STREAM_KEY_FILE = "/home/ryan/Desktop/twitchstreamingkey.txt"
CHROME = "/opt/google/chrome/chrome"
CHROME_PROFILE = "/tmp/fouler-stream-profile"  # Shared profile for login persistence
DISPLAY = os.environ.get("DISPLAY", ":0")

# Bot credentials for Showdown login
PS_USERNAME = "LEBOTJAMESXD005"
PS_PASSWORD = "LeBotPassword2026!"

# State tracking
chrome_pids = []
ffmpeg_proc = None
current_battles = []


def get_stream_key():
    with open(STREAM_KEY_FILE) as f:
        return f.read().strip()


def kill_chrome():
    """Kill Chrome windows we spawned"""
    global chrome_pids
    for pid in chrome_pids:
        try:
            os.kill(pid, signal.SIGTERM)
            time.sleep(0.5)
            os.kill(pid, signal.SIGKILL)
        except ProcessLookupError:
            pass
    chrome_pids = []


def stop_ffmpeg():
    """Stop ffmpeg"""
    global ffmpeg_proc
    if ffmpeg_proc and ffmpeg_proc.poll() is None:
        ffmpeg_proc.send_signal(signal.SIGINT)
        try:
            ffmpeg_proc.wait(timeout=5)
        except subprocess.TimeoutExpired:
            ffmpeg_proc.kill()
    ffmpeg_proc = None


def start_ffmpeg():
    """Start ffmpeg streaming to Twitch"""
    global ffmpeg_proc
    stop_ffmpeg()

    stream_key = get_stream_key()
    rtmp_url = f"rtmp://live.twitch.tv/app/{stream_key}"

    cmd = [
        "ffmpeg", "-y",
        "-f", "x11grab", "-framerate", "15",
        "-video_size", "1920x1080",
        "-i", f"{DISPLAY}+0,0",
        "-f", "lavfi", "-i", "anullsrc=r=44100:cl=stereo",
        "-c:v", "libx264",
        "-preset", "ultrafast",
        "-maxrate", "2500k",
        "-bufsize", "5000k",
        "-pix_fmt", "yuv420p",
        "-g", "60",
        "-c:a", "aac", "-b:a", "128k", "-ar", "44100",
        "-f", "flv", rtmp_url,
    ]

    print(f"[STREAM] Starting ffmpeg → Twitch...")
    ffmpeg_proc = subprocess.Popen(
        cmd,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.PIPE,
    )
    print(f"[STREAM] Streaming (PID {ffmpeg_proc.pid})")
    return ffmpeg_proc.pid


def open_showdown_login():
    """Open Pokemon Showdown homepage for manual login (one-time setup)"""
    print(f"\n[SETUP] Opening Pokemon Showdown for login...")
    print(f"[SETUP] Please log in as: {PS_USERNAME}")
    print(f"[SETUP] Password: {PS_PASSWORD}")
    print(f"[SETUP] Press ENTER when logged in...\n")
    
    proc = subprocess.Popen([
        CHROME,
        "--new-window", "https://play.pokemonshowdown.com/",
        f"--user-data-dir={CHROME_PROFILE}",
        "--window-position=0,0",
        "--window-size=960,1080",
    ], env={**os.environ, "DISPLAY": DISPLAY})
    
    input()  # Wait for user confirmation
    proc.terminate()
    time.sleep(2)
    print("[SETUP] Login complete! Chrome profile saved.")


def open_battles(battle_ids):
    """Open 2 battle windows side-by-side using logged-in profile"""
    global chrome_pids, current_battles
    
    # Don't restart if same battles
    if battle_ids == current_battles:
        return
    
    kill_chrome()
    
    num = min(len(battle_ids), 2)
    width = 960  # Fixed width for 2 windows
    
    pids = []
    for i, bid in enumerate(battle_ids[:num]):
        x_pos = i * width
        url = f"https://play.pokemonshowdown.com/{bid}"
        
        proc = subprocess.Popen([
            CHROME,
            "--new-window", url,
            f"--user-data-dir={CHROME_PROFILE}",  # Reuse logged-in profile
            f"--window-position={x_pos},0",
            f"--window-size={width},1080",
            "--no-first-run",
            "--disable-notifications",
            "--disable-session-crashed-bubble",
            "--hide-crash-restore-bubble",
        ], env={**os.environ, "DISPLAY": DISPLAY},
           stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        
        pids.append(proc.pid)
        time.sleep(2)
    
    chrome_pids = pids
    current_battles = battle_ids[:num]
    print(f"[STREAM] Displaying battles: {battle_ids[:num]}")


def get_active_battles():
    """Parse monitor log for active battles"""
    try:
        if not os.path.exists(MONITOR_LOG):
            return []
        
        with open(MONITOR_LOG) as f:
            lines = f.readlines()[-2000:]
        
        active = {}
        finished = set()
        
        for i, line in enumerate(lines):
            match = re.search(r'(battle-gen\d+\w+-\d+)', line)
            if match:
                bid = match.group(1)
                # Track when battles start
                if any(w in line for w in ["Initialized", "Matched", "Battle started"]):
                    active[bid] = i
                # Track when they finish
                if any(w in line for w in ["Won vs", "Lost vs", "Winner:", "forfeited", "finished"]):
                    finished.add(bid)
        
        # Remove finished battles
        for bid in finished:
            active.pop(bid, None)
        
        # Return 2 most recent active battles
        sorted_battles = sorted(active.items(), key=lambda x: x[1], reverse=True)
        return [bid for bid, _ in sorted_battles[:2]]
    
    except Exception as e:
        print(f"[ERROR] Reading battles: {e}")
        return []


def cleanup(signum=None, frame=None):
    print("\n[STREAM] Shutting down...")
    kill_chrome()
    stop_ffmpeg()
    sys.exit(0)


signal.signal(signal.SIGINT, cleanup)
signal.signal(signal.SIGTERM, cleanup)


def main():
    print("=" * 60)
    print("FOULER PLAY AUTO-STREAM - Stable Version")
    print("=" * 60)
    
    # Check if we need to log in
    if not os.path.exists(f"{CHROME_PROFILE}/Default"):
        print("\n[SETUP] First-time setup: Pokemon Showdown login required")
        open_showdown_login()
    else:
        print(f"[INFO] Using existing Chrome profile (logged in as {PS_USERNAME})")
    
    # Start streaming
    print("\n[STREAM] Starting Twitch stream...")
    start_ffmpeg()
    time.sleep(2)
    
    # Verify ffmpeg started
    if ffmpeg_proc and ffmpeg_proc.poll() is not None:
        print("[ERROR] ffmpeg failed to start!")
        cleanup()
    
    print("\n[STREAM] 🔴 LIVE on Twitch!")
    print("[STREAM] Monitoring for battles...\n")
    
    last_battles = []
    check_count = 0
    
    while True:
        try:
            battles = get_active_battles()
            
            # Update display when battles change
            if battles != last_battles:
                if len(battles) == 0:
                    print(f"[STREAM] No active battles (waiting...)")
                    kill_chrome()
                else:
                    print(f"[STREAM] Battle update: {battles}")
                    open_battles(battles)
                
                last_battles = battles
            
            # Periodic health checks
            check_count += 1
            if check_count >= 6:  # Every ~60s
                check_count = 0
                
                # Check ffmpeg health
                if ffmpeg_proc and ffmpeg_proc.poll() is not None:
                    print("[STREAM] ⚠️  ffmpeg died! Restarting...")
                    start_ffmpeg()
                
                # Status update
                status = f"{len(battles)} battle(s)" if battles else "waiting"
                print(f"[STREAM] Status: {status}")
            
            time.sleep(10)
        
        except KeyboardInterrupt:
            cleanup()
        except Exception as e:
            print(f"[ERROR] {e}")
            time.sleep(10)


if __name__ == "__main__":
    main()
