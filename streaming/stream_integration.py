"""Retired bot-monitor stream integration; overlay truth now comes from canonical state."""

from __future__ import annotations


async def start_stream() -> dict[str, object]:
    return {"ok": False, "error": "retired"}


async def stop_stream() -> dict[str, object]:
    return {"ok": False, "error": "retired"}


async def update_stream_status(**_updates: object) -> dict[str, object]:
    return {"ok": False, "error": "retired"}
