#!/usr/bin/env python3
"""
Fouler Play Twitch Stream Server

Provides:
1. HTTP API for overlay status updates (port 8777)
2. Stream control (start/stop ffmpeg → Twitch RTMP)
3. Integration with bot_monitor.py for auto-streaming

Uses ffmpeg to capture X11 window + composite overlay.
"""

import asyncio
import json
import os
import signal
import subprocess
import sys
import time
from aiohttp import web
from pathlib import Path

STREAM_KEY_FILE = "/home/ryan/Desktop/twitchstreamingkey.txt"
TWITCH_RTMP = "rtmp://live.twitch.tv/app"
OVERLAY_HTML = str(Path(__file__).parent / "overlay.html")

# Global state
state = {
    "elo": "---",
    "wins": 0,
    "losses": 0,
    "status": "Idle",
    "battle_info": "Waiting for battle...",
    "streaming": False,
    "stream_pid": None,
    "active_battles": [],  # List of active battle IDs
}

import re

BATTLES_FILE = str(Path(__file__).parent.parent / "active_battles.json")

def get_active_battles_from_bot():
    """Read active battle IDs from shared active_battles.json file.
    
    Written by bot_monitor.py whenever battles start or end.
    """
    try:
        if not os.path.exists(BATTLES_FILE):
            return []
        with open(BATTLES_FILE) as f:
            data = json.load(f)
        return [b["id"] for b in data.get("battles", [])]
    except Exception as e:
        print(f"[BATTLES] Error reading battles file: {e}")
        return []

ffmpeg_proc = None


def get_stream_key():
    with open(STREAM_KEY_FILE) as f:
        return f.read().strip()


def find_showdown_window():
    """Find Pokemon Showdown browser window ID"""
    try:
        # Try "Showdown!" (most common for Chrome)
        result = subprocess.run(
            ["xdotool", "search", "--name", "Showdown!"],
            capture_output=True, text=True, timeout=5
        )
        if result.stdout.strip():
            # Return first window found
            return result.stdout.strip().split('\n')[0]
        
        # Fallback: try other patterns
        for pattern in ["Pokémon Showdown", "Pokemon Showdown", "play.pokemonshowdown.com"]:
            result = subprocess.run(
                ["xdotool", "search", "--name", pattern],
                capture_output=True, text=True, timeout=5
            )
            if result.stdout.strip():
                return result.stdout.strip().split('\n')[0]
    except Exception as e:
        print(f"[STREAM] Error finding window: {e}")
    return None


def get_window_geometry(window_id):
    """Get window position and size"""
    try:
        result = subprocess.run(
            ["xdotool", "getwindowgeometry", "--shell", window_id],
            capture_output=True, text=True, timeout=5
        )
        geo = {}
        for line in result.stdout.strip().split('\n'):
            if '=' in line:
                k, v = line.split('=', 1)
                geo[k] = int(v)
        return geo
    except Exception:
        return None


async def start_stream():
    """Start ffmpeg stream to Twitch"""
    global ffmpeg_proc
    
    if ffmpeg_proc and ffmpeg_proc.poll() is None:
        print("[STREAM] Already streaming")
        return {"ok": True, "msg": "Already streaming"}

    stream_key = get_stream_key()
    
    # For 2 concurrent battles: capture 1920x1080 (two 960-width windows side-by-side)
    # Battles are positioned at x=0 and x=960
    print("[STREAM] Capturing 1920x1080 for 2 concurrent battles")
    capture_input = ["-f", "x11grab", "-framerate", "30",
                    "-video_size", "1920x1080",
                    "-i", f"{os.environ.get('DISPLAY', ':0')}+0,0"]

    rtmp_url = f"{TWITCH_RTMP}/{stream_key}"

    cmd = [
        "ffmpeg", "-y",
        *capture_input,
        # No audio for now (bot doesn't need it)
        "-f", "lavfi", "-i", "anullsrc=r=44100:cl=stereo",
        # Video encoding
        "-c:v", "libx264",
        "-preset", "veryfast",
        "-maxrate", "2500k",
        "-bufsize", "5000k",
        "-pix_fmt", "yuv420p",
        "-g", "60",  # keyframe every 2s at 30fps
        # Audio encoding (silent)
        "-c:a", "aac",
        "-b:a", "128k",
        "-ar", "44100",
        # Output
        "-f", "flv",
        rtmp_url,
    ]

    print(f"[STREAM] Starting ffmpeg stream...")
    ffmpeg_proc = subprocess.Popen(
        cmd,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )
    
    state["streaming"] = True
    state["stream_pid"] = ffmpeg_proc.pid
    print(f"[STREAM] ffmpeg started with PID {ffmpeg_proc.pid}")
    return {"ok": True, "pid": ffmpeg_proc.pid}


async def stop_stream():
    """Stop ffmpeg stream"""
    global ffmpeg_proc
    
    if ffmpeg_proc and ffmpeg_proc.poll() is None:
        ffmpeg_proc.send_signal(signal.SIGINT)
        try:
            ffmpeg_proc.wait(timeout=5)
        except subprocess.TimeoutExpired:
            ffmpeg_proc.kill()
        print("[STREAM] Stream stopped")
    
    ffmpeg_proc = None
    state["streaming"] = False
    state["stream_pid"] = None
    return {"ok": True}


# --- HTTP API ---

async def handle_battles(request):
    """Return active battle IDs and their Showdown URLs.
    
    BAKUGO polls this from Windows to know which battles to show in OBS.
    """
    try:
        if os.path.exists(BATTLES_FILE):
            with open(BATTLES_FILE) as f:
                data = json.load(f)
            state["active_battles"] = [b["id"] for b in data.get("battles", [])]
            return web.json_response(data)
        else:
            return web.json_response({"battles": [], "count": 0, "updated": None})
    except Exception as e:
        return web.json_response({"battles": [], "count": 0, "error": str(e)})


async def handle_status(request):
    # Also refresh active battles on status check
    state["active_battles"] = get_active_battles_from_bot()
    return web.json_response(state)


async def handle_update(request):
    data = await request.json()
    for key in ["elo", "wins", "losses", "status", "battle_info"]:
        if key in data:
            state[key] = data[key]
    return web.json_response({"ok": True})


async def handle_stream_start(request):
    result = await start_stream()
    return web.json_response(result)


async def handle_stream_stop(request):
    result = await stop_stream()
    return web.json_response(result)


async def handle_overlay(request):
    return web.FileResponse(OVERLAY_HTML)


async def check_ffmpeg_health(app):
    """Periodically check if ffmpeg is still running"""
    while True:
        await asyncio.sleep(10)
        if ffmpeg_proc and ffmpeg_proc.poll() is not None:
            print(f"[STREAM] ffmpeg exited with code {ffmpeg_proc.returncode}")
            state["streaming"] = False
            state["stream_pid"] = None


async def start_background_tasks(app):
    app['health_check'] = asyncio.create_task(check_ffmpeg_health(app))


async def cleanup_background_tasks(app):
    app['health_check'].cancel()
    await stop_stream()


def create_app():
    app = web.Application()
    app.router.add_get('/battles', handle_battles)
    app.router.add_get('/status', handle_status)
    app.router.add_post('/update', handle_update)
    app.router.add_post('/stream/start', handle_stream_start)
    app.router.add_post('/stream/stop', handle_stream_stop)
    app.router.add_get('/overlay', handle_overlay)
    app.on_startup.append(start_background_tasks)
    app.on_cleanup.append(cleanup_background_tasks)
    return app


if __name__ == '__main__':
    print("[STREAM] Starting Fouler Play stream server on :8777")
    app = create_app()
    web.run_app(app, host='0.0.0.0', port=8777)
