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
from streaming import state_store

STREAM_KEY_FILE = os.environ.get("STREAM_KEY_PATH", "twitch_key.txt")
TWITCH_RTMP = "rtmp://live.twitch.tv/app"
OVERLAY_HTML = str(Path(__file__).parent / "overlay.html")
OBS_BATTLES_HTML = str(Path(__file__).parent / "obs_battles.html")

# Track connected WebSocket clients for real-time broadcast
ws_clients = set()
async_lock = asyncio.Lock()

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

def get_active_battles_from_bot():
    """Read active battle IDs from shared active_battles.json file."""
    try:
        data = state_store.read_active_battles()
        return [b["id"] for b in data.get("battles", [])]
    except Exception as e:
        print(f"[BATTLES] Error reading battles file: {e}")
        return []

def _merge_status_from_files() -> dict:
    """Merge persisted stream_status.json with in-memory state."""
    file_status = state_store.read_status()
    merged = dict(state)
    defaults = state_store.DEFAULT_STATUS
    for key, value in file_status.items():
        if value is None:
            continue
        default_val = defaults.get(key)
        if value != default_val or merged.get(key) in (None, default_val):
            merged[key] = value
    return merged


def _build_status_payload() -> tuple[dict, dict]:
    status = _merge_status_from_files()
    battles_data = state_store.read_active_battles()
    battles = battles_data.get("battles", [])
    status["active_battles"] = [b.get("id") for b in battles if b.get("id")]
    if battles:
        status["battle_info"] = ", ".join(
            f"vs {b.get('opponent', 'Unknown')}" for b in battles
        )
    daily = state_store.read_daily_stats()
    status["today_wins"] = daily.get("wins", 0)
    status["today_losses"] = daily.get("losses", 0)
    return status, battles_data

ffmpeg_proc = None


def get_stream_key():
    if os.path.exists(STREAM_KEY_FILE):
        with open(STREAM_KEY_FILE) as f:
            return f.read().strip()
    return os.environ.get("TWITCH_STREAM_KEY", "")


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


# --- Real-time Event Hub ---

async def broadcast_event(event_type, data):
    """Push event to all connected OBS WebSocket clients."""
    message = json.dumps({
        "type": event_type,
        "payload": data,
        "timestamp": time.time()
    })
    
    if not ws_clients:
        return

    print(f"[EVENT] Broadcasting {event_type} to {len(ws_clients)} clients")
    
    # Broadcast to all connected clients
    disconnected = set()
    for ws in ws_clients:
        try:
            await ws.send_str(message)
        except Exception:
            disconnected.add(ws)
            
    for ws in disconnected:
        ws_clients.discard(ws)

async def handle_ws(request):
    """WebSocket handler for OBS overlay."""
    ws = web.WebSocketResponse()
    await ws.prepare(request)
    
    ws_clients.add(ws)
    print(f"[WS] New client connected. Total: {len(ws_clients)}")
    
    # Send initial state
    await ws.send_str(json.dumps({
        "type": "INIT",
        "payload": state,
        "timestamp": time.time()
    }))

    try:
        async for msg in ws:
            pass # We don't expect messages FROM OBS
    finally:
        ws_clients.discard(ws)
        print(f"[WS] Client disconnected. Total: {len(ws_clients)}")
        
    return ws

async def handle_event(request):
    """Entry point for bot processes to signal events (start, end, move)."""
    try:
        data = await request.json()
        event_type = data.get("type", "UNKNOWN")
        payload = data.get("payload", {})
        
        # Internal state update if applicable
        if event_type == "BATTLE_START":
            bid = payload.get("id")
            if bid and bid not in state["active_battles"]:
                state["active_battles"].append(bid)
        elif event_type == "BATTLE_END":
            bid = payload.get("id")
            if bid and bid in state["active_battles"]:
                state["active_battles"].remove(bid)
        
        # Broadcast to all OBS instances
        await broadcast_event(event_type, payload)
        
        return web.json_response({"ok": True})
    except Exception as e:
        print(f"[EVENT] Error processing event: {e}")
        return web.json_response({"ok": False, "error": str(e)}, status=400)

# --- HTTP API ---

async def handle_battles(request):
    """Return active battle IDs and their Showdown URLs.
    
    BAKUGO polls this from Windows to know which battles to show in OBS.
    """
    try:
        data = state_store.read_active_battles()
        state["active_battles"] = [b["id"] for b in data.get("battles", [])]
        return web.json_response(data)
    except Exception as e:
        return web.json_response({"battles": [], "count": 0, "error": str(e)})


async def handle_status(request):
    status, _ = _build_status_payload()
    return web.json_response(status)


async def handle_state(request):
    status, battles_data = _build_status_payload()
    battles = battles_data.get("battles", [])
    return web.json_response({
        "status": status,
        "battles": battles,
        "count": battles_data.get("count", len(battles)),
        "max_slots": battles_data.get("max_slots"),
        "updated": battles_data.get("updated"),
    })


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


async def handle_obs_battles(request):
    """Serve the OBS browser source page with auto-updating battle iframes."""
    return web.FileResponse(OBS_BATTLES_HTML)


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
    app.router.add_get('/ws', handle_ws)
    app.router.add_post('/event', handle_event)
    app.router.add_get('/battles', handle_battles)
    app.router.add_get('/status', handle_status)
    app.router.add_get('/state', handle_state)
    app.router.add_post('/update', handle_update)
    app.router.add_post('/stream/start', handle_stream_start)
    app.router.add_post('/stream/stop', handle_stream_stop)
    app.router.add_get('/overlay', handle_overlay)
    app.router.add_get('/obs', handle_obs_battles)
    app.on_startup.append(start_background_tasks)
    app.on_cleanup.append(cleanup_background_tasks)
    return app


if __name__ == '__main__':
    print("[STREAM] Starting Fouler Play stream server on :8777")
    app = create_app()
    web.run_app(app, host='0.0.0.0', port=8777)
