#!/usr/bin/env python3
"""
Stream Integration Module for Windows.

This module provides stub functions for the streaming integration that bot_monitor.py expects.
On Windows, we use OBS with browser sources instead of ffmpeg X11 capture, so these functions
primarily update status files that the OBS overlay reads.

For Linux/Twitch streaming, use stream_server.py instead.
"""

import os
import time
import aiohttp

from streaming.state_store import (
    read_active_battles,
    write_status,
    read_status,
    read_next_fix,
    write_next_fix,
)

# Global state
_state = read_status()
_state["next_fix"] = read_next_fix()

# Track active battles to emit start/end events with IDs
_last_active_ids: set[str] = set()


async def send_stream_event(event_type, payload):
    """Send a real-time event signal to the stream server."""
    try:
        url = "http://localhost:8777/event"
        async with aiohttp.ClientSession() as session:
            async with session.post(url, json={"type": event_type, "payload": payload}, timeout=3) as resp:
                return await resp.json()
    except Exception:
        # Silently fail if stream server isn't running
        pass


def _write_status():
    """Write current state to status file for OBS to read."""
    try:
        write_status(_state)
    except Exception as e:
        print(f"[STREAM] Warning: Could not write status file: {e}")


async def start_stream():
    """Start streaming (no-op on Windows, use OBS instead).
    
    On Windows, streaming is handled by OBS reading from the local HTTP server.
    This function just updates the status to indicate streaming should begin.
    """
    _state["streaming"] = True
    _write_status()
    
    # Send INIT event to refresh OBS data
    await send_stream_event("INIT", _state)
    
    print("[STREAM] Windows mode: Use OBS Browser Source at http://localhost:8777/obs")
    return {"ok": True, "msg": "Use OBS for Windows streaming"}


async def stop_stream():
    """Stop streaming (no-op on Windows).
    
    On Windows, streaming is controlled by OBS.
    This just updates the status file.
    """
    _state["streaming"] = False
    _write_status()
    print("[STREAM] Stream status set to stopped")
    return {"ok": True}


async def update_stream_status(
    wins=None,
    losses=None,
    elo=None,
    status=None,
    battle_info=None,
    next_fix=None,
):
    """Update stream overlay status.
    
    This writes to stream_status.json which the OBS overlay server can read.
    
    Args:
        wins: Win count
        losses: Loss count
        elo: Current ELO rating
        status: Status text (e.g., "Battling", "Searching", "Idle")
        battle_info: Current battle info string
    """
    if wins is not None:
        _state["wins"] = wins
    if losses is not None:
        _state["losses"] = losses
    if elo is not None:
        _state["elo"] = elo
    if status is not None:
        _state["status"] = status
    if battle_info is not None:
        _state["battle_info"] = battle_info
    if next_fix is not None:
        _state["next_fix"] = next_fix
        try:
            write_next_fix(next_fix)
        except Exception:
            pass
    else:
        # Refresh from disk if it changed
        disk_fix = read_next_fix()
        if disk_fix and disk_fix != _state.get("next_fix"):
            _state["next_fix"] = disk_fix
    
    _write_status()

    # Emit battle start/end events with IDs by diffing active_battles.json
    current_battles = read_active_battles().get("battles", [])
    current_ids = {b.get("id") for b in current_battles if b.get("id")}

    started = current_ids - _last_active_ids
    ended = _last_active_ids - current_ids

    for battle in current_battles:
        if battle.get("id") in started:
            await send_stream_event("BATTLE_START", {
                "id": battle.get("id"),
                "opponent": battle.get("opponent"),
                "slot": battle.get("slot"),
            })

    for bid in ended:
        await send_stream_event("BATTLE_END", {"id": bid})

    _last_active_ids.clear()
    _last_active_ids.update(current_ids)

    # Broadcast status update for stats
    await send_stream_event("STATS_UPDATE", _state)
    
    return {"ok": True}


# Initialize status file on import
_write_status()
