#!/usr/bin/env python3
"""
Create a hybrid OBS scene collection derived from the existing fouler-play one.

By default this reads:
  %APPDATA%\\obs-studio\\basic\\scenes\\fouler_play_scenes.json

And writes:
  %APPDATA%\\obs-studio\\basic\\scenes\\fouler_play_hybrid_scenes.json

It keeps the same scene/source graph but updates key browser source URLs so the
collection mirrors the updated hybrid dashboard/overlay workflow.
"""

from __future__ import annotations

import argparse
import json
import os
from pathlib import Path


DEFAULT_INPUT = Path(os.path.expandvars(r"%APPDATA%\obs-studio\basic\scenes\fouler_play_scenes.json"))
DEFAULT_OUTPUT = Path(os.path.expandvars(r"%APPDATA%\obs-studio\basic\scenes\fouler_play_hybrid_scenes.json"))


def _load_collection(path: Path) -> dict:
    with path.open("r", encoding="utf-8-sig") as f:
        return json.load(f)


def _update_browser_source(source: dict, port: int) -> None:
    name = str(source.get("name", ""))
    settings = source.setdefault("settings", {})

    if source.get("id") != "browser_source":
        return

    if name.startswith("Battle Slot "):
        settings["url"] = f"http://localhost:{port}/idle"
        settings["reroute_audio"] = False
        return

    if name == "Stats Overlay":
        settings["url"] = f"http://localhost:{port}/overlay/hybrid"
        settings["width"] = 2560
        settings["height"] = 1440
        settings["fps"] = 30
        return

    if name == "Debug Overlay":
        settings["url"] = f"http://localhost:{port}/dashboard/hybrid"
        settings["width"] = 1280
        settings["height"] = 720
        settings["fps"] = 30
        return


def build_collection(data: dict, collection_name: str, port: int) -> dict:
    out = dict(data)
    out["name"] = collection_name

    for source in out.get("sources", []):
        _update_browser_source(source, port)
        if source.get("id") == "scene":
            items = source.get("settings", {}).get("items", [])
            for item in items:
                if str(item.get("name", "")).strip().lower() == "window capture":
                    # The inherited capture is machine-specific and often points to
                    # a stale window title, which renders as a black full-screen
                    # layer. Disable it in the hybrid collection.
                    item["visible"] = False
        elif (
            source.get("id") == "window_capture"
            and str(source.get("name", "")).strip().lower() == "window capture"
        ):
            source["enabled"] = False

    return out


def main() -> int:
    parser = argparse.ArgumentParser(description="Build hybrid OBS scene collection")
    parser.add_argument("--input", type=Path, default=DEFAULT_INPUT, help="Input OBS scene collection JSON")
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT, help="Output OBS scene collection JSON")
    parser.add_argument(
        "--name",
        default="Fouler Play Hybrid Battles",
        help="Collection display name inside OBS",
    )
    parser.add_argument("--port", type=int, default=int(os.getenv("OBS_SERVER_PORT", "8777")))
    parser.add_argument(
        "--repo-copy",
        type=Path,
        default=Path("streaming") / "fouler_play_hybrid_scenes.json",
        help="Optional repository copy for versioning",
    )
    args = parser.parse_args()

    if not args.input.exists():
        raise FileNotFoundError(f"Input scene collection not found: {args.input}")

    data = _load_collection(args.input)
    out = build_collection(data, args.name, args.port)

    args.output.parent.mkdir(parents=True, exist_ok=True)
    with args.output.open("w", encoding="utf-8-sig", newline="\n") as f:
        json.dump(out, f, indent=4)
        f.write("\n")

    if args.repo_copy:
        args.repo_copy.parent.mkdir(parents=True, exist_ok=True)
        with args.repo_copy.open("w", encoding="utf-8", newline="\n") as f:
            json.dump(out, f, indent=4)
            f.write("\n")

    print(f"Wrote OBS collection: {args.output}")
    if args.repo_copy:
        print(f"Wrote repository copy: {args.repo_copy}")
    print(f"Collection name: {out.get('name')}")
    print(f"Server port: {args.port}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
