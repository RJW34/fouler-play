"""Retired integration for the removed ffmpeg Twitch server."""

from __future__ import annotations


async def start_stream() -> bool:
    return False


async def stop_stream() -> bool:
    return False


async def update_battles(_battle_ids: list[str]) -> bool:
    return False
