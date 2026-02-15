#!/usr/bin/env python3
"""
MAGNETON OBS Control Script
Automates scene switching, source updates, and recording control for PIE validation.

Usage:
    python magneton-obs-control.py setup_validation --battle-url "https://play.pokemonshowdown.com/battle-gen9ou-123456"
    python magneton-obs-control.py teardown
    python magneton-obs-control.py refresh_sources
    python magneton-obs-control.py start_recording
    python magneton-obs-control.py stop_recording
"""

import argparse
import time
import sys
from obswebsocket import obsws, requests as obs_requests
from obswebsocket.exceptions import ConnectionFailure

# Configuration
OBS_HOST = "192.168.1.181"
OBS_PORT = 4455
OBS_PASSWORD = "4Dswd1gtixEnGuK1"

class OBSController:
    def __init__(self, host=OBS_HOST, port=OBS_PORT, password=OBS_PASSWORD):
        self.host = host
        self.port = port
        self.password = password
        self.ws = None
        
    def connect(self):
        """Establish WebSocket connection to OBS"""
        try:
            self.ws = obsws(self.host, self.port, self.password)
            self.ws.connect()
            print(f"✅ Connected to OBS at {self.host}:{self.port}")
            return True
        except ConnectionFailure as e:
            print(f"❌ Failed to connect to OBS: {e}")
            return False
    
    def disconnect(self):
        """Close WebSocket connection"""
        if self.ws:
            self.ws.disconnect()
            print("🔌 Disconnected from OBS")
    
    def switch_scene(self, scene_name):
        """Switch to specified scene"""
        try:
            self.ws.call(obs_requests.SetCurrentProgramScene(sceneName=scene_name))
            print(f"🎬 Switched to scene: {scene_name}")
            return True
        except Exception as e:
            print(f"❌ Failed to switch scene: {e}")
            return False
    
    def update_browser_source_url(self, source_name, new_url):
        """Update browser source URL"""
        try:
            # Get current settings
            response = self.ws.call(obs_requests.GetInputSettings(inputName=source_name))
            settings = response.getInputSettings()
            
            # Update URL
            settings['url'] = new_url
            
            # Apply new settings
            self.ws.call(obs_requests.SetInputSettings(
                inputName=source_name,
                inputSettings=settings
            ))
            print(f"🔗 Updated {source_name} URL to: {new_url}")
            return True
        except Exception as e:
            print(f"❌ Failed to update browser source: {e}")
            return False
    
    def refresh_browser_source(self, source_name):
        """Refresh a browser source (reload page)"""
        try:
            # Trigger browser refresh
            self.ws.call(obs_requests.PressInputPropertiesButton(
                inputName=source_name,
                propertyName="refreshnocache"
            ))
            print(f"🔄 Refreshed browser source: {source_name}")
            time.sleep(0.5)  # Brief delay for reload
            return True
        except Exception as e:
            # Fallback: toggle visibility
            try:
                self.set_source_visibility(source_name, False)
                time.sleep(0.2)
                self.set_source_visibility(source_name, True)
                print(f"🔄 Refreshed {source_name} via visibility toggle")
                return True
            except:
                print(f"❌ Failed to refresh browser source: {e}")
                return False
    
    def set_source_visibility(self, source_name, visible):
        """Show or hide a source in current scene"""
        try:
            scene_name = self.get_current_scene()
            self.ws.call(obs_requests.SetSceneItemEnabled(
                sceneName=scene_name,
                sceneItemId=self.get_scene_item_id(scene_name, source_name),
                sceneItemEnabled=visible
            ))
            state = "visible" if visible else "hidden"
            print(f"👁️ Set {source_name} to {state}")
            return True
        except Exception as e:
            print(f"❌ Failed to set visibility: {e}")
            return False
    
    def get_current_scene(self):
        """Get current active scene name"""
        try:
            response = self.ws.call(obs_requests.GetCurrentProgramScene())
            return response.getCurrentProgramScene()
        except Exception as e:
            print(f"❌ Failed to get current scene: {e}")
            return None
    
    def get_scene_item_id(self, scene_name, source_name):
        """Get scene item ID for a source in a scene"""
        try:
            response = self.ws.call(obs_requests.GetSceneItemId(
                sceneName=scene_name,
                sourceName=source_name
            ))
            return response.getSceneItemId()
        except Exception as e:
            print(f"❌ Failed to get scene item ID: {e}")
            return None
    
    def start_recording(self):
        """Start OBS recording"""
        try:
            # Check if already recording
            status = self.ws.call(obs_requests.GetRecordStatus())
            if status.getOutputActive():
                print("⚠️ Recording already active")
                return True
            
            self.ws.call(obs_requests.StartRecord())
            print("🔴 Recording started")
            time.sleep(1)  # Wait for recording to initialize
            return True
        except Exception as e:
            print(f"❌ Failed to start recording: {e}")
            return False
    
    def stop_recording(self):
        """Stop OBS recording"""
        try:
            # Check if recording
            status = self.ws.call(obs_requests.GetRecordStatus())
            if not status.getOutputActive():
                print("⚠️ Recording not active")
                return True
            
            self.ws.call(obs_requests.StopRecord())
            print("⏹️ Recording stopped")
            
            # Wait for file to finalize
            time.sleep(2)
            
            # Get output path
            output_path = status.getOutputPath()
            print(f"💾 Recording saved: {output_path}")
            return True
        except Exception as e:
            print(f"❌ Failed to stop recording: {e}")
            return False
    
    def get_stats(self):
        """Get OBS performance statistics"""
        try:
            stats = self.ws.call(obs_requests.GetStats())
            output_stats = self.ws.call(obs_requests.GetRecordStatus())
            
            print("\n📊 OBS Statistics:")
            print(f"  FPS: {stats.getActiveFps():.2f}")
            print(f"  CPU: {stats.getCpuUsage():.2f}%")
            print(f"  Memory: {stats.getMemoryUsage():.2f} MB")
            print(f"  Render lag: {stats.getRenderSkippedFrames()} frames")
            print(f"  Encoding lag: {stats.getOutputSkippedFrames()} frames")
            
            if output_stats.getOutputActive():
                print(f"  🔴 Recording: {output_stats.getOutputTimecode()}")
            else:
                print("  ⚫ Recording: Inactive")
            
            return True
        except Exception as e:
            print(f"❌ Failed to get stats: {e}")
            return False

def setup_validation(controller, battle_url):
    """Complete setup for validation run"""
    print("\n🚀 Setting up OBS for PIE validation...")
    
    steps = [
        ("Switch to Active Battles scene", 
         lambda: controller.switch_scene("Active Battles")),
        
        ("Update Battle Slot 1 URL", 
         lambda: controller.update_browser_source_url("Battle Slot 1", battle_url)),
        
        ("Refresh Battle Slot 1", 
         lambda: controller.refresh_browser_source("Battle Slot 1")),
        
        ("Refresh Stats Overlay", 
         lambda: controller.refresh_browser_source("Stats Overlay")),
        
        ("Start recording", 
         lambda: controller.start_recording()),
    ]
    
    for step_name, step_func in steps:
        print(f"\n  ▶ {step_name}...")
        if not step_func():
            print(f"  ❌ Setup failed at: {step_name}")
            return False
        time.sleep(0.5)
    
    print("\n✅ OBS setup complete! Ready for validation.")
    controller.get_stats()
    return True

def teardown(controller):
    """Complete teardown after validation run"""
    print("\n🛑 Tearing down OBS after validation...")
    
    steps = [
        ("Stop recording", 
         lambda: controller.stop_recording()),
        
        ("Switch to Dashboard scene", 
         lambda: controller.switch_scene("Dashboard")),
    ]
    
    for step_name, step_func in steps:
        print(f"\n  ▶ {step_name}...")
        if not step_func():
            print(f"  ⚠️ Teardown warning at: {step_name}")
        time.sleep(0.5)
    
    print("\n✅ OBS teardown complete!")
    return True

def main():
    parser = argparse.ArgumentParser(description="MAGNETON OBS Control")
    parser.add_argument("command", choices=[
        "setup_validation",
        "teardown",
        "refresh_sources",
        "start_recording",
        "stop_recording",
        "stats",
        "switch_scene"
    ])
    parser.add_argument("--battle-url", help="Pokémon Showdown battle URL")
    parser.add_argument("--scene", help="Scene name to switch to")
    
    args = parser.parse_args()
    
    # Create controller
    controller = OBSController()
    
    # Connect to OBS
    if not controller.connect():
        sys.exit(1)
    
    try:
        # Execute command
        if args.command == "setup_validation":
            if not args.battle_url:
                print("❌ --battle-url required for setup_validation")
                sys.exit(1)
            success = setup_validation(controller, args.battle_url)
            
        elif args.command == "teardown":
            success = teardown(controller)
            
        elif args.command == "refresh_sources":
            controller.refresh_browser_source("Battle Slot 1")
            controller.refresh_browser_source("Stats Overlay")
            success = True
            
        elif args.command == "start_recording":
            success = controller.start_recording()
            
        elif args.command == "stop_recording":
            success = controller.stop_recording()
            
        elif args.command == "stats":
            success = controller.get_stats()
            
        elif args.command == "switch_scene":
            if not args.scene:
                print("❌ --scene required for switch_scene")
                sys.exit(1)
            success = controller.switch_scene(args.scene)
        
        sys.exit(0 if success else 1)
        
    finally:
        controller.disconnect()

if __name__ == "__main__":
    main()
