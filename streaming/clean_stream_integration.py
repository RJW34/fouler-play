#!/usr/bin/env python3
"""
Integration module for clean stream
"""

import aiohttp

STREAM_URL = "http://localhost:8779"

async def start_stream():
    """Starts automatically when battles are added"""
    print("[STREAM] Stream will start when battles begin")
    return True

async def stop_stream():
    """Stop the stream"""
    try:
        async with aiohttp.ClientSession() as session:
            async with session.post(f"{STREAM_URL}/stop") as resp:
                await resp.json()
                return True
    except Exception as e:
        print(f"[STREAM] Error stopping: {e}")
        return False

async def update_battles(battle_ids):
    """Update which battles are shown"""
    try:
        async with aiohttp.ClientSession() as session:
            async with session.post(
                f"{STREAM_URL}/update-battles",
                json={"battle_ids": battle_ids}
            ) as resp:
                await resp.json()
                print(f"[STREAM] Updated battles: {battle_ids}")
                return True
    except Exception as e:
        print(f"[STREAM] Error updating battles: {e}")
        return False
