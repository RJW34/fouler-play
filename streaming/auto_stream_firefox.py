#!/usr/bin/env python3
"""
Fouler Play Auto-Stream - Firefox Version
Uses Firefox instead of Chrome per Ryan's setup
"""

import subprocess
import time
import signal
import sys
import os
import re

# Configuration
FIREFOX = "/usr/bin/firefox"
FIREFOX_PROFILE = "/tmp/fouler-stream-firefox"
DISPLAY = os.environ.get("DISPLAY", ":0")

# Bot credentials
PS_USERNAME = os.getenv("PS_USERNAME", "LEBOTJAMESXD005")
PS_PASSWORD = os.getenv("PS_PASSWORD", "LeBotPassword2026!")

# Twitch streaming
TWITCH_KEY_FILE = "/home/ryan/Desktop/twitchstreamingkey.txt"
TWITCH_RTMP = "rtmp://live.twitch.tv/app"

# Bot output log
BOT_LOG = "/home/ryan/projects/fouler-play/monitor.log"

# Process tracking
firefox_pids = []
ffmpeg_proc = None
current_battles = []


def read_twitch_key():
    """Read Twitch stream key"""
    with open(TWITCH_KEY_FILE) as f:
        return f.read().strip()


def start_ffmpeg():
    """Start ffmpeg capture → Twitch stream"""
    global ffmpeg_proc
    
    if ffmpeg_proc and ffmpeg_proc.poll() is None:
        print("[STREAM] ffmpeg already running")
        return
    
    stream_key = read_twitch_key()
    rtmp_url = f"{TWITCH_RTMP}/{stream_key}"
    
    cmd = [
        "ffmpeg",
        "-f", "x11grab",
        "-video_size", "1920x1080",
        "-framerate", "15",
        "-i", f"{DISPLAY}",
        "-c:v", "libx264",
        "-preset", "veryfast",
        "-b:v", "2500k",
        "-maxrate", "2500k",
        "-bufsize", "5000k",
        "-pix_fmt", "yuv420p",
        "-g", "30",
        "-f", "flv",
        rtmp_url
    ]
    
    ffmpeg_proc = subprocess.Popen(
        cmd,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL
    )
    print(f"[STREAM] Started ffmpeg (PID {ffmpeg_proc.pid})")


def stop_ffmpeg():
    """Stop ffmpeg"""
    global ffmpeg_proc
    if ffmpeg_proc:
        ffmpeg_proc.terminate()
        ffmpeg_proc.wait()
        ffmpeg_proc = None
        print("[STREAM] Stopped ffmpeg")


def kill_firefox():
    """Close all battle windows"""
    global firefox_pids
    for pid in firefox_pids:
        try:
            os.kill(pid, signal.SIGTERM)
        except ProcessLookupError:
            pass
    firefox_pids = []


def open_battles(battle_ids):
    """Open 2 battle windows side-by-side"""
    global firefox_pids, current_battles
    
    # Don't restart if same battles
    if battle_ids == current_battles:
        return
    
    kill_firefox()
    
    num = min(len(battle_ids), 2)
    
    # Firefox opens all URLs in one command
    urls = [f"https://play.pokemonshowdown.com/{bid}" for bid in battle_ids[:num]]
    
    if urls:
        proc = subprocess.Popen([
            FIREFOX,
            "--new-window",
            *urls,
            "--profile", FIREFOX_PROFILE,
        ], env={**os.environ, "DISPLAY": DISPLAY},
           stdout=subprocess.DEVNULL,
           stderr=subprocess.DEVNULL)
        
        firefox_pids = [proc.pid]
        current_battles = battle_ids
        print(f"[STREAM] Opened {num} battle(s) in Firefox: {battle_ids}")


def get_active_battles():
    """Read active battles from bot output log"""
    if not os.path.exists(BOT_LOG):
        return []
    
    # Read last 100 lines
    with open(BOT_LOG) as f:
        lines = f.readlines()[-100:]
    
    # Extract battle IDs
    battle_pattern = re.compile(r'battle-gen9ou-\d+')
    battles = set()
    
    for line in reversed(lines):
        # Look for "Initialized battle-..." or "Battle started"
        if "Initialized" in line or "Battle started" in line:
            match = battle_pattern.search(line)
            if match:
                battles.add(match.group(0))
        
        # Stop at battle end markers
        if ("Won with team" in line or "Lost with team" in line) and len(battles) >= 2:
            break
    
    return list(battles)[:2]  # Max 2 battles


def cleanup(signum=None, frame=None):
    """Cleanup on exit"""
    print("\n[STREAM] Shutting down...")
    kill_firefox()
    stop_ffmpeg()
    sys.exit(0)


signal.signal(signal.SIGINT, cleanup)
signal.signal(signal.SIGTERM, cleanup)


def main():
    print("=" * 60)
    print("FOULER PLAY AUTO-STREAM - Firefox Version")
    print("=" * 60)
    print(f"[INFO] Firefox profile: {FIREFOX_PROFILE}")
    print(f"[INFO] Account: {PS_USERNAME}")
    print(f"[INFO] Display: {DISPLAY}")
    
    # Create Firefox profile if doesn't exist
    os.makedirs(FIREFOX_PROFILE, exist_ok=True)
    
    # Start streaming
    print("\n[STREAM] Starting Twitch stream...")
    start_ffmpeg()
    time.sleep(3)
    
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
                    kill_firefox()
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
            import traceback
            traceback.print_exc()
            time.sleep(10)


if __name__ == "__main__":
    main()
