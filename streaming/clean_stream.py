#!/usr/bin/env python3
"""
Clean Fouler Play Stream - No Desktop Clutter

Opens Pokemon Showdown battles in borderless Chrome windows,
captures ONLY those windows, streams clean to Twitch.

No OBS, no desktop mess, just clean battle views.
"""

import asyncio
import subprocess
import time
import re
import aiohttp
from pathlib import Path

TWITCH_STREAM_KEY_FILE = "/home/ryan/Desktop/twitchstreamingkey.txt"
TWITCH_RTMP = "rtmp://live.twitch.tv/app"
SHOWDOWN_BASE = "https://play.pokemonshowdown.com/"

class CleanStream:
    def __init__(self):
        self.chrome_windows = {}  # battle_id -> process
        self.ffmpeg_proc = None
        self.current_battles = []
        
    def get_stream_key(self):
        with open(TWITCH_STREAM_KEY_FILE) as f:
            return f.read().strip()
    
    def open_battle_window(self, battle_id, position_x=0):
        """Open Chrome in app mode (no UI) for a battle"""
        url = f"{SHOWDOWN_BASE}{battle_id}"
        
        cmd = [
            "google-chrome",
            f"--app={url}",
            f"--window-position={position_x},0",
            "--window-size=960,1080",
            "--user-data-dir=/tmp/showdown-stream",
            "--no-first-run",
            "--no-default-browser-check",
            "--disable-popup-blocking",
            "--disable-notifications",
            "--disable-infobars",
        ]
        
        print(f"[STREAM] Opening {battle_id} at x={position_x}")
        proc = subprocess.Popen(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, env={"DISPLAY": ":0"})
        return proc
    
    def find_showdown_windows(self):
        """Find all Showdown Chrome window IDs"""
        try:
            result = subprocess.run(
                ["xdotool", "search", "--name", "Showdown"],
                capture_output=True, text=True, timeout=5
            )
            if result.stdout.strip():
                return result.stdout.strip().split('\n')
        except:
            pass
        return []
    
    def get_window_geometry(self, window_id):
        """Get window position and size for ffmpeg capture"""
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
        except:
            return None
    
    def start_stream(self, window_ids):
        """Start ffmpeg stream capturing Showdown windows"""
        if self.ffmpeg_proc and self.ffmpeg_proc.poll() is None:
            print("[STREAM] Already streaming")
            return
        
        stream_key = self.get_stream_key()
        rtmp_url = f"{TWITCH_RTMP}/{stream_key}"
        
        # If we have 2 windows, capture both side-by-side
        if len(window_ids) >= 2:
            # Capture 1920x1080 region (2 windows at 960x1080 each)
            geo1 = self.get_window_geometry(window_ids[0])
            if geo1:
                x = geo1.get('X', 0)
                y = geo1.get('Y', 0)
                capture_input = [
                    "-f", "x11grab",
                    "-framerate", "30",
                    "-video_size", "1920x1080",
                    "-i", f":0+{x},{y}"
                ]
            else:
                # Fallback
                capture_input = [
                    "-f", "x11grab",
                    "-framerate", "30",
                    "-video_size", "1920x1080",
                    "-i", ":0+0,0"
                ]
        elif len(window_ids) == 1:
            # Single window, capture just that
            geo1 = self.get_window_geometry(window_ids[0])
            if geo1:
                w = geo1.get('WIDTH', 960)
                h = geo1.get('HEIGHT', 1080)
                x = geo1.get('X', 0)
                y = geo1.get('Y', 0)
                # Ensure even dimensions
                w = w - (w % 2)
                h = h - (h % 2)
                capture_input = [
                    "-f", "x11grab",
                    "-framerate", "30",
                    "-video_size", f"{w}x{h}",
                    "-i", f":0+{x},{y}"
                ]
            else:
                capture_input = [
                    "-f", "x11grab",
                    "-framerate", "30",
                    "-video_size", "960x1080",
                    "-i", ":0"
                ]
        else:
            print("[STREAM] No windows to capture")
            return
        
        cmd = [
            "ffmpeg", "-y",
            *capture_input,
            # Silent audio
            "-f", "lavfi", "-i", "anullsrc=r=44100:cl=stereo",
            # Video encoding
            "-c:v", "libx264",
            "-preset", "veryfast",
            "-maxrate", "3500k",
            "-bufsize", "7000k",
            "-pix_fmt", "yuv420p",
            "-g", "60",
            # Audio encoding
            "-c:a", "aac",
            "-b:a", "128k",
            "-ar", "44100",
            # Output
            "-f", "flv",
            rtmp_url,
        ]
        
        print(f"[STREAM] Starting ffmpeg stream...")
        self.ffmpeg_proc = subprocess.Popen(
            cmd,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
        )
        print(f"[STREAM] Streaming (PID {self.ffmpeg_proc.pid})")
    
    def stop_stream(self):
        """Stop ffmpeg stream"""
        if self.ffmpeg_proc and self.ffmpeg_proc.poll() is None:
            self.ffmpeg_proc.terminate()
            try:
                self.ffmpeg_proc.wait(timeout=5)
            except subprocess.TimeoutExpired:
                self.ffmpeg_proc.kill()
            print("[STREAM] Stream stopped")
        self.ffmpeg_proc = None
    
    async def update_battles(self, battle_ids):
        """Update which battles are displayed
        
        Args:
            battle_ids: List of battle IDs
        """
        print(f"[STREAM] Updating battles: {battle_ids}")
        
        # Close windows for battles that ended
        for battle_id in list(self.chrome_windows.keys()):
            if battle_id not in battle_ids:
                print(f"[STREAM] Closing {battle_id}")
                proc = self.chrome_windows[battle_id]
                proc.terminate()
                del self.chrome_windows[battle_id]
        
        # Open windows for new battles
        for i, battle_id in enumerate(battle_ids[:2]):  # Max 2 battles
            if battle_id not in self.chrome_windows:
                x_pos = i * 960
                proc = self.open_battle_window(battle_id, x_pos)
                self.chrome_windows[battle_id] = proc
                await asyncio.sleep(2)  # Let Chrome open
        
        self.current_battles = battle_ids
        
        # Update stream capture
        await asyncio.sleep(1)  # Let windows settle
        window_ids = self.find_showdown_windows()
        
        if battle_ids and window_ids:
            if not self.ffmpeg_proc or self.ffmpeg_proc.poll() is not None:
                self.start_stream(window_ids)
        else:
            self.stop_stream()


# HTTP server for bot integration
from aiohttp import web

stream = CleanStream()

async def handle_update(request):
    data = await request.json()
    battle_ids = data.get("battle_ids", [])
    await stream.update_battles(battle_ids)
    return web.json_response({"ok": True})

async def handle_stop(request):
    stream.stop_stream()
    return web.json_response({"ok": True})

def create_app():
    app = web.Application()
    app.router.add_post('/update-battles', handle_update)
    app.router.add_post('/stop', handle_stop)
    return app

if __name__ == '__main__':
    print("[Clean Stream] Starting on :8779")
    app = create_app()
    web.run_app(app, host='0.0.0.0', port=8779)
