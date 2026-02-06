#!/usr/bin/env python3
"""
Auto-streaming system that monitors bot_monitor and updates the stream
"""

import subprocess
import time
import re
import os

MONITOR_LOG = "/home/ryan/projects/fouler-play/monitor.log"
STREAM_KEY_FILE = "/home/ryan/Desktop/twitchstreamingkey.txt"
WAITING_HTML = "/home/ryan/projects/fouler-play/streaming/waiting.html"

current_battles = []
chrome_procs = []
ffmpeg_proc = None

def get_stream_key():
    with open(STREAM_KEY_FILE) as f:
        return f.read().strip()

def kill_all():
    """Kill all Chrome and ffmpeg processes"""
    subprocess.run(["pkill", "-9", "chrome"], stderr=subprocess.DEVNULL)
    subprocess.run(["pkill", "-9", "chromium"], stderr=subprocess.DEVNULL)
    subprocess.run(["pkill", "-9", "ffmpeg"], stderr=subprocess.DEVNULL)
    time.sleep(1)

def show_waiting_screen():
    """Show 'Waiting for battles...' screen"""
    global chrome_procs, ffmpeg_proc
    
    kill_all()
    
    # Open waiting screen in fullscreen
    proc = subprocess.Popen([
        "chromium-browser",
        f"--app=file://{WAITING_HTML}",
        "--window-position=0,0",
        "--window-size=1920,1080",
        "--kiosk",
    ], env={"DISPLAY": ":0"}, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    chrome_procs = [proc]
    
    time.sleep(3)
    
    # Start ffmpeg stream
    stream_key = get_stream_key()
    ffmpeg_proc = subprocess.Popen([
        "ffmpeg", "-f", "x11grab", "-framerate", "30",
        "-video_size", "1920x1080", "-i", ":0+0,0",
        "-f", "lavfi", "-i", "anullsrc=r=44100:cl=stereo",
        "-c:v", "libx264", "-preset", "veryfast",
        "-maxrate", "3500k", "-bufsize", "7000k",
        "-pix_fmt", "yuv420p", "-g", "60",
        "-c:a", "aac", "-b:a", "128k", "-ar", "44100",
        "-f", "flv", f"rtmp://live.twitch.tv/app/{stream_key}"
    ], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    
    print("[STREAM] Showing waiting screen")

def show_battles(battle_ids):
    """Show 2 battles side-by-side"""
    global chrome_procs, ffmpeg_proc
    
    kill_all()
    
    # Open up to 2 battles
    procs = []
    for i, battle_id in enumerate(battle_ids[:2]):
        x_pos = i * 960
        proc = subprocess.Popen([
            "/opt/google/chrome/chrome",
            "--new-window",
            f"https://play.pokemonshowdown.com/{battle_id}",
            f"--window-position={x_pos},0",
            "--window-size=960,1080",
            f"--user-data-dir=/tmp/showdown-{i}",
            "--no-first-run",
            "--disable-notifications",
            "--disable-popup-blocking",
            "--disable-session-crashed-bubble",
            "--disable-infobars",
            "--hide-crash-restore-bubble",
        ], env={"DISPLAY": ":0"}, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        procs.append(proc)
        time.sleep(2)
    
    chrome_procs = procs
    time.sleep(3)
    
    # Start ffmpeg stream
    stream_key = get_stream_key()
    ffmpeg_proc = subprocess.Popen([
        "ffmpeg", "-f", "x11grab", "-framerate", "30",
        "-video_size", "1920x1080", "-i", ":0+0,0",
        "-f", "lavfi", "-i", "anullsrc=r=44100:cl=stereo",
        "-c:v", "libx264", "-preset", "veryfast",
        "-maxrate", "3500k", "-bufsize", "7000k",
        "-pix_fmt", "yuv420p", "-g", "60",
        "-c:a", "aac", "-b:a", "128k", "-ar", "44100",
        "-f", "flv", f"rtmp://live.twitch.tv/app/{stream_key}"
    ], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    
    print(f"[STREAM] Showing {len(battle_ids)} battles: {battle_ids}")

def get_active_battles():
    """Parse monitor.log for ONLY active (not finished) battles"""
    try:
        with open(MONITOR_LOG) as f:
            lines = f.readlines()[-2000:]
        
        active = {}
        finished = set()
        
        for i, line in enumerate(lines):
            if "Battle started vs" in line or "Initialized" in line:
                match = re.search(r"(battle-gen9ou-\d+)", line)
                if match:
                    active[match.group(1)] = i
            
            if any(word in line for word in ["Won vs", "Lost vs", "Winner:", "forfeited"]):
                match = re.search(r"(battle-gen9ou-\d+)", line)
                if match:
                    bid = match.group(1)
                    finished.add(bid)
        
        for bid in finished:
            active.pop(bid, None)
        
        sorted_active = sorted(active.items(), key=lambda x: x[1], reverse=True)
        return [bid for bid, _ in sorted_active[:2]]
    except Exception as e:
        print(f"[ERROR] {e}")
        return []


# Initialize with waiting screen
show_waiting_screen()

print("[AUTO STREAM] Monitoring battles...")

# Monitor loop
last_battles = []
while True:
    try:
        battles = get_active_battles()
        
        if battles != last_battles:
            if not battles:
                show_waiting_screen()
            else:
                show_battles(battles)
            last_battles = battles
        
        time.sleep(10)  # Check every 10 seconds
        
    except KeyboardInterrupt:
        print("\n[AUTO STREAM] Shutting down...")
        kill_all()
        break
    except Exception as e:
        print(f"[AUTO STREAM] Error: {e}")
        time.sleep(10)
