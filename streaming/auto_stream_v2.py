#!/usr/bin/env python3
"""
Fouler Play Auto-Stream v2
- Monitors bot_monitor.out for active battles
- Opens Chrome windows showing battles side-by-side
- Captures screen via ffmpeg → Twitch RTMP
- Shows waiting screen between battles
- Overlay composited via ffmpeg drawtext
"""

import subprocess
import time
import re
import os
import signal
import sys
import json
import urllib.request

MONITOR_LOG = "/home/ryan/projects/fouler-play/monitor.log"
STREAM_KEY_FILE = "/home/ryan/Desktop/twitchstreamingkey.txt"
WAITING_HTML = os.path.join(os.path.dirname(__file__), "waiting.html")
OVERLAY_HTML = os.path.join(os.path.dirname(__file__), "overlay.html")
STREAM_API = "http://localhost:8777"
FIREFOX = "/usr/bin/firefox"
DISPLAY = os.environ.get("DISPLAY", ":0")

# Track our own PIDs only
firefox_pids = []
ffmpeg_proc = None
current_mode = None  # "waiting" | "battles"
current_battles = []


def get_stream_key():
    with open(STREAM_KEY_FILE) as f:
        return f.read().strip()


def kill_our_firefox():
    """Kill only Firefox windows we spawned"""
    global firefox_pids
    for pid in firefox_pids:
        try:
            os.kill(pid, signal.SIGTERM)
        except ProcessLookupError:
            pass
    time.sleep(1)
    for pid in firefox_pids:
        try:
            os.kill(pid, signal.SIGKILL)
        except ProcessLookupError:
            pass
    firefox_pids = []


def stop_ffmpeg():
    """Stop our ffmpeg process"""
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
        # Screen capture (720p to save resources)
        "-f", "x11grab", "-framerate", "15",
        "-video_size", "1280x720",
        "-i", f"{DISPLAY}+0,0",
        # Silent audio
        "-f", "lavfi", "-i", "anullsrc=r=44100:cl=stereo",
        # Video encoding
        "-c:v", "libx264",
        "-preset", "ultrafast",
        "-maxrate", "2000k",
        "-bufsize", "4000k",
        "-pix_fmt", "yuv420p",
        "-g", "60",
        # Overlay text (top bar - adjusted for 720p)
        "-vf",
        "drawbox=x=0:y=0:w=1280:h=32:color=black@0.85:t=fill,"
        "drawtext=fontfile=/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf:"
        "text='FOULER PLAY':fontcolor=0xef4444:fontsize=18:x=12:y=8,"
        "drawtext=fontfile=/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf:"
        "text='Gen 9 OU Ladder':fontcolor=0xaaaaaa:fontsize=12:x=1120:y=12,"
        "drawbox=x=0:y=696:w=1280:h=24:color=black@0.8:t=fill,"
        "drawtext=fontfile=/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf:"
        "text='twitch.tv/dekubotbygoofy - Pokemon Showdown Battle Bot':"
        "fontcolor=0x999999:fontsize=11:x=12:y=700",
        # Audio encoding
        "-c:a", "aac", "-b:a", "128k", "-ar", "44100",
        "-shortest",
        # Output
        "-f", "flv", rtmp_url,
    ]

    print(f"[STREAM] Starting ffmpeg → Twitch...")
    ffmpeg_proc = subprocess.Popen(
        cmd,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.PIPE,
        env={**os.environ, "DISPLAY": DISPLAY},
    )
    print(f"[STREAM] ffmpeg PID: {ffmpeg_proc.pid}")
    return ffmpeg_proc.pid


def open_waiting_screen():
    """Open waiting screen in fullscreen Firefox"""
    global firefox_pids, current_mode
    kill_our_firefox()

    proc = subprocess.Popen([
        FIREFOX,
        "-new-window", f"file://{WAITING_HTML}",
        "-width", "1920",
        "-height", "1080",
    ], env={**os.environ, "DISPLAY": DISPLAY},
       stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)

    firefox_pids = [proc.pid]
    current_mode = "waiting"
    print("[STREAM] Showing waiting screen")


def open_battles(battle_ids):
    """Open battle windows side-by-side"""
    global firefox_pids, current_mode, current_battles
    kill_our_firefox()

    pids = []
    num = min(len(battle_ids), 2)
    width = 1920 // num

    for i, bid in enumerate(battle_ids[:num]):
        x_pos = i * width
        url = f"https://play.pokemonshowdown.com/{bid}"

        proc = subprocess.Popen([
            FIREFOX,
            "-new-window", url,
            "-width", str(width),
            "-height", "1080",
        ], env={**os.environ, "DISPLAY": DISPLAY},
           stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)

        pids.append(proc.pid)
        time.sleep(2)

    firefox_pids = pids
    current_mode = "battles"
    current_battles = battle_ids[:num]
    print(f"[STREAM] Showing {num} battle(s): {battle_ids[:num]}")


def update_stream_server(battle_ids):
    """Update the stream server API with current status"""
    try:
        data = json.dumps({"battle_ids": battle_ids}).encode()
        req = urllib.request.Request(
            f"{STREAM_API}/update",
            data=json.dumps({
                "status": "In Battle" if battle_ids else "Searching",
                "battle_info": ", ".join(battle_ids) if battle_ids else "Waiting for battle...",
            }).encode(),
            headers={"Content-Type": "application/json"},
        )
        urllib.request.urlopen(req, timeout=3)
    except Exception:
        pass


def get_active_battles():
    """Parse monitor log for active (not finished) battles"""
    try:
        with open(MONITOR_LOG) as f:
            lines = f.readlines()[-3000:]

        active = {}
        finished = set()

        for i, line in enumerate(lines):
            # Detect battle start
            match = re.search(r"(battle-gen\d+\w+-\d+)", line)
            if match:
                bid = match.group(1)
                if "Initialized" in line or "Battle started" in line or "Matched" in line:
                    active[bid] = i
                if any(w in line for w in ["finished", "Won vs", "Lost vs", "Winner:", "forfeited", "Unregistered"]):
                    finished.add(bid)

        for bid in finished:
            active.pop(bid, None)

        sorted_active = sorted(active.items(), key=lambda x: x[1], reverse=True)
        return [bid for bid, _ in sorted_active[:2]]
    except Exception as e:
        print(f"[ERROR] Reading battles: {e}")
        return []


def cleanup(signum=None, frame=None):
    print("\n[STREAM] Shutting down...")
    kill_our_firefox()
    stop_ffmpeg()
    sys.exit(0)


signal.signal(signal.SIGINT, cleanup)
signal.signal(signal.SIGTERM, cleanup)

# --- MAIN ---
if __name__ == "__main__":
    print("[AUTO STREAM v2] Starting Fouler Play Twitch stream")
    print(f"[AUTO STREAM v2] Display: {DISPLAY}, Resolution: 1920x1080")

    # Start with waiting screen
    open_waiting_screen()
    time.sleep(3)

    # Start ffmpeg
    start_ffmpeg()
    time.sleep(2)

    # Check ffmpeg started OK
    if ffmpeg_proc and ffmpeg_proc.poll() is not None:
        stderr = ffmpeg_proc.stderr.read().decode() if ffmpeg_proc.stderr else ""
        print(f"[ERROR] ffmpeg failed to start: {stderr[-500:]}")
        cleanup()

    print("[AUTO STREAM v2] Stream is LIVE! Monitoring battles...")

    last_battles = []
    ffmpeg_check_counter = 0

    while True:
        try:
            battles = get_active_battles()

            if battles != last_battles:
                print(f"[STREAM] Battle change: {last_battles} → {battles}")
                if not battles:
                    open_waiting_screen()
                else:
                    open_battles(battles)
                update_stream_server(battles)
                last_battles = battles

            # Periodic ffmpeg health check
            ffmpeg_check_counter += 1
            if ffmpeg_check_counter >= 6:  # Every ~60s
                ffmpeg_check_counter = 0
                if ffmpeg_proc and ffmpeg_proc.poll() is not None:
                    print("[STREAM] ffmpeg died, restarting...")
                    start_ffmpeg()

            time.sleep(10)

        except KeyboardInterrupt:
            cleanup()
        except Exception as e:
            print(f"[AUTO STREAM v2] Error: {e}")
            time.sleep(10)
