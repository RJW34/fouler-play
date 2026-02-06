#!/usr/bin/env python3
"""
OBS WebSocket Controller for Fouler Play Streaming

Controls OBS via WebSocket 5.x (obsws-python) to:
- Start/stop streaming
- Switch scenes (Starting Soon / Battle / BRB)
- Update browser sources with live battle URLs
- Handle concurrent battles (up to 2 side-by-side)
"""

import asyncio
import json
import subprocess
import time
import obsws_python as obs
from pathlib import Path

OBS_HOST = "localhost"
OBS_PORT = 4455
OBS_PASSWORD = "mfEsQOehf1gRyV34"  # From OBS config

# Scene names
SCENE_STARTING_SOON = "Starting Soon"
SCENE_BATTLE = "Battle"
SCENE_BRB = "BRB"

# Source names for battles
BATTLE_1_SOURCE = "Battle 1"
BATTLE_2_SOURCE = "Battle 2"

# URLs
SHOWDOWN_BASE = "https://play.pokemonshowdown.com/"


class OBSController:
    def __init__(self):
        self.ws = None
        self.connected = False
        self.obs_process = None
        
    def connect(self):
        """Connect to OBS WebSocket"""
        try:
            self.ws = obs.ReqClient(host=OBS_HOST, port=OBS_PORT, password=OBS_PASSWORD, timeout=5)
            self.connected = True
            print("[OBS] Connected to OBS WebSocket")
            return True
        except Exception as e:
            print(f"[OBS] Failed to connect: {e}")
            self.connected = False
            return False
    
    def disconnect(self):
        """Disconnect from OBS WebSocket"""
        if self.ws and self.connected:
            try:
                self.ws.disconnect()
            except:
                pass
            self.connected = False
            print("[OBS] Disconnected")
    
    def start_obs(self):
        """Start OBS in the background"""
        try:
            # Start OBS minimized with FoulerPlay profile/collection
            self.obs_process = subprocess.Popen([
                "obs",
                "--collection", "FoulerPlay",
                "--profile", "FoulerPlay",
                "--scene", SCENE_STARTING_SOON,
                "--minimize-to-tray",
            ], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            
            print(f"[OBS] Started OBS (PID {self.obs_process.pid})")
            
            # Wait for OBS to initialize and WebSocket to become available
            for i in range(15):
                time.sleep(1)
                if self.connect():
                    return True
                print(f"[OBS] Waiting for WebSocket... ({i+1}/15)")
            
            print("[OBS] Timeout waiting for OBS WebSocket")
            return False
        except Exception as e:
            print(f"[OBS] Error starting OBS: {e}")
            return False
    
    def stop_obs(self):
        """Stop OBS"""
        if self.obs_process:
            self.obs_process.terminate()
            try:
                self.obs_process.wait(timeout=5)
            except subprocess.TimeoutExpired:
                self.obs_process.kill()
    
    def start_streaming(self):
        """Start streaming to Twitch"""
        if not self.connected:
            print("[OBS] Not connected")
            return False
        
        try:
            self.ws.start_stream()
            print("[OBS] Streaming started")
            return True
        except Exception as e:
            print(f"[OBS] Error starting stream: {e}")
            return False
    
    def stop_streaming(self):
        """Stop streaming"""
        if not self.connected:
            return False
        
        try:
            self.ws.stop_stream()
            print("[OBS] Streaming stopped")
            return True
        except Exception as e:
            print(f"[OBS] Error stopping stream: {e}")
            return False
    
    def switch_scene(self, scene_name):
        """Switch to a specific scene"""
        if not self.connected:
            return False
        
        try:
            self.ws.set_current_program_scene(scene_name)
            print(f"[OBS] Switched to scene: {scene_name}")
            return True
        except Exception as e:
            print(f"[OBS] Error switching scene: {e}")
            return False
    
    def update_browser_source_url(self, source_name, url):
        """Update the URL of a browser source"""
        if not self.connected:
            return False
        
        try:
            # Get current settings
            response = self.ws.get_input_settings(source_name)
            settings = response.input_settings
            
            # Update URL
            settings['url'] = url
            
            # Apply updated settings
            self.ws.set_input_settings(source_name, settings, overlay=True)
            print(f"[OBS] Updated {source_name} URL: {url}")
            return True
        except Exception as e:
            print(f"[OBS] Error updating source URL: {e}")
            return False
    
    def set_source_visibility(self, scene_name, source_name, visible):
        """Show/hide a source in a scene"""
        if not self.connected:
            return False
        
        try:
            # Get scene item ID
            item_id = self.ws.get_scene_item_id(scene_name, source_name).scene_item_id
            
            # Set visibility
            self.ws.set_scene_item_enabled(scene_name, item_id, visible)
            return True
        except Exception as e:
            print(f"[OBS] Error setting visibility for {source_name}: {e}")
            return False
    
    def update_battles(self, battle_ids):
        """Update browser sources with current battle URLs
        
        Args:
            battle_ids: List of battle IDs (e.g. ["battle-gen9ou-123", "battle-gen9ou-456"])
        """
        if not self.connected:
            return
        
        print(f"[OBS] Updating battles: {battle_ids}")
        
        # Update Battle 1
        if len(battle_ids) >= 1:
            url1 = f"{SHOWDOWN_BASE}{battle_ids[0]}"
            self.update_browser_source_url(BATTLE_1_SOURCE, url1)
            self.set_source_visibility(SCENE_BATTLE, BATTLE_1_SOURCE, True)
        else:
            # Hide Battle 1 if no battles
            try:
                self.set_source_visibility(SCENE_BATTLE, BATTLE_1_SOURCE, False)
            except:
                pass
        
        # Update Battle 2
        if len(battle_ids) >= 2:
            url2 = f"{SHOWDOWN_BASE}{battle_ids[1]}"
            self.update_browser_source_url(BATTLE_2_SOURCE, url2)
            self.set_source_visibility(SCENE_BATTLE, BATTLE_2_SOURCE, True)
        else:
            # Hide Battle 2 if only 1 battle or less
            try:
                self.set_source_visibility(SCENE_BATTLE, BATTLE_2_SOURCE, False)
            except:
                pass


# Simple HTTP server for controlling OBS
from aiohttp import web

obs_controller = OBSController()


async def handle_start(request):
    """Start OBS and streaming"""
    if not obs_controller.connected:
        if not obs_controller.start_obs():
            return web.json_response({"ok": False, "error": "Failed to start OBS"})
    
    # Switch to Starting Soon scene
    obs_controller.switch_scene(SCENE_STARTING_SOON)
    
    # Start streaming
    obs_controller.start_streaming()
    
    return web.json_response({"ok": True})


async def handle_stop(request):
    """Stop streaming"""
    obs_controller.stop_streaming()
    return web.json_response({"ok": True})


async def handle_update_battles(request):
    """Update battle sources with new URLs"""
    data = await request.json()
    battle_ids = data.get("battle_ids", [])
    
    obs_controller.update_battles(battle_ids)
    
    # Switch to Battle scene if battles are active
    if battle_ids:
        obs_controller.switch_scene(SCENE_BATTLE)
    
    return web.json_response({"ok": True})


async def handle_scene(request):
    """Switch scenes"""
    data = await request.json()
    scene = data.get("scene", SCENE_STARTING_SOON)
    obs_controller.switch_scene(scene)
    return web.json_response({"ok": True})


def create_app():
    app = web.Application()
    app.router.add_post('/start', handle_start)
    app.router.add_post('/stop', handle_stop)
    app.router.add_post('/update-battles', handle_update_battles)
    app.router.add_post('/scene', handle_scene)
    return app


if __name__ == '__main__':
    print("[OBS Controller] Starting on :8778")
    app = create_app()
    web.run_app(app, host='0.0.0.0', port=8778)
