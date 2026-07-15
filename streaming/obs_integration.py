"""Retired client for the removed unauthenticated OBS output controller."""

from __future__ import annotations


async def start_stream() -> bool:
    return False


async def stop_stream() -> bool:
    return False


async def update_battles(_battle_ids: list[str]) -> bool:
    return False


async def switch_scene(_scene: str) -> bool:
    return False
