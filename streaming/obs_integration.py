#!/usr/bin/env python3
"""
OBS Integration for Bot Monitor

Simple HTTP client to control OBS via obs_controller.py
"""

import aiohttp

OBS_CONTROLLER_URL = "http://localhost:8778"


async def start_stream():
    """Start OBS and streaming"""
    try:
        async with aiohttp.ClientSession() as session:
            async with session.post(f"{OBS_CONTROLLER_URL}/start") as resp:
                result = await resp.json()
                if result.get("ok"):
                    print("[STREAM] OBS streaming started")
                    return True
                else:
                    print(f"[STREAM] Error starting OBS: {result.get('error')}")
                    return False
    except Exception as e:
        print(f"[STREAM] Failed to start stream: {e}")
        return False


async def stop_stream():
    """Stop streaming (keep OBS running)"""
    try:
        async with aiohttp.ClientSession() as session:
            async with session.post(f"{OBS_CONTROLLER_URL}/stop") as resp:
                await resp.json()
                print("[STREAM] OBS streaming stopped")
                return True
    except Exception as e:
        print(f"[STREAM] Failed to stop stream: {e}")
        return False


async def update_battles(battle_ids):
    """Update which battles are shown
    
    Args:
        battle_ids: List of battle IDs (e.g. ["battle-gen9ou-123", "battle-gen9ou-456"])
    """
    try:
        async with aiohttp.ClientSession() as session:
            async with session.post(
                f"{OBS_CONTROLLER_URL}/update-battles",
                json={"battle_ids": battle_ids}
            ) as resp:
                await resp.json()
                print(f"[STREAM] Updated battles: {battle_ids}")
                return True
    except Exception as e:
        print(f"[STREAM] Failed to update battles: {e}")
        return False


async def switch_scene(scene):
    """Switch OBS scene
    
    Args:
        scene: "Starting Soon", "Battle", or "BRB"
    """
    try:
        async with aiohttp.ClientSession() as session:
            async with session.post(
                f"{OBS_CONTROLLER_URL}/scene",
                json={"scene": scene}
            ) as resp:
                await resp.json()
                print(f"[STREAM] Switched to scene: {scene}")
                return True
    except Exception as e:
        print(f"[STREAM] Failed to switch scene: {e}")
        return False
