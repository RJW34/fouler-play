#!/usr/bin/env python3
"""
Create a hybrid OBS scene collection derived from the existing fouler-play one.

By default this reads:
  %APPDATA%\\obs-studio\\basic\\scenes\\fouler_play_scenes.json

And writes:
  %APPDATA%\\obs-studio\\basic\\scenes\\fouler_play_hybrid_scenes.json

It keeps the same scene/source graph but updates key public browser source URLs
so the collection mirrors the updated hybrid overlay workflow.
"""

from __future__ import annotations

import argparse
import json
import os
from pathlib import Path


DEFAULT_INPUT = Path(os.path.expandvars(r"%APPDATA%\obs-studio\basic\scenes\fouler_play_scenes.json"))
DEFAULT_OUTPUT = Path(os.path.expandvars(r"%APPDATA%\obs-studio\basic\scenes\fouler_play_hybrid_scenes.json"))
PUBLIC_FORBIDDEN_SCENE_NAMES = {"Starting Soon", "Be Right Back", "Ending", "Vertical Scene"}
PUBLIC_FORBIDDEN_SOURCE_NAMES = {
    "Debug Overlay",
    "Window Capture",
    "Starting Soon BG",
    "Starting Soon Text",
    "BRB BG",
    "BRB Text",
    "Ending BG",
    "Ending Text",
}
BATTLE_SLOT_WIDTH = 1280
BATTLE_SLOT_HEIGHT = 720
BATTLE_SLOT_POSITIONS = {
    "1": {"x": 0.0, "y": 0.0},
    "2": {"x": 1280.0, "y": 0.0},
    "3": {"x": 640.0, "y": 720.0},
}
BATTLE_SLOT_POSITIONS_REL = {
    "1": {"x": -1.7777777910232544, "y": -1.0},
    "2": {"x": 0.0, "y": -1.0},
    "3": {"x": -0.8888888955116272, "y": 0.0},
}
PUBLIC_BATTLE_BROWSER_CSS = """
html, body {
  width: 100vw !important;
  height: 100vh !important;
  margin: 0 !important;
  padding: 0 !important;
  overflow: hidden !important;
  background: #05070a !important;
}
.header, .maintabbar, .mainmenu, .pm-window, .battle-log, .battle-log-add,
.message-log, .battle-log h2, .replay-controls, .replay-controls-2,
.battle-controls, .battle-timer, button { display: none !important; }
#onetrust-banner-sdk, #onetrust-consent-sdk, #ot-sdk-btn-floating,
.fc-ccpa-root, .fc-dialog-container, .fc-dialog, .fc-dns-dialog,
.fc-dns-link, .fc-button-background,
div[id*="onetrust" i], div[class*="onetrust" i],
div[class*="ccpa"], div[class*="fc-ccpa"],
div[id*="cookie" i], div[class*="cookie" i],
div[id*="privacy" i], div[class*="privacy" i],
button[aria-label*="Do Not Sell"],
button[aria-label*="do not sell"] {
  display: none !important;
  visibility: hidden !important;
  width: 0 !important;
  height: 0 !important;
  min-height: 0 !important;
  overflow: hidden !important;
  pointer-events: none !important;
}
.ps-room, .battle-room {
  top: 0 !important;
  left: 0 !important;
  right: 0 !important;
  bottom: 0 !important;
  width: 100vw !important;
  height: 100vh !important;
  overflow: visible !important;
}
.battle {
  margin: 0 !important;
  left: 0 !important;
  top: 0 !important;
  right: auto !important;
  min-width: 1280px !important;
  min-height: 720px !important;
  width: 100vw !important;
  height: 100vh !important;
  max-width: none !important;
  max-height: none !important;
  transform: none !important;
  transform-origin: top left !important;
}
.innerbattle { background-color: #05070a !important; }
""".strip()


def _battle_slot_number(name: str) -> str | None:
    if not name.startswith("Battle Slot "):
        return None
    return "".join(ch for ch in name if ch.isdigit()) or "1"


def _load_collection(path: Path) -> dict:
    with path.open("r", encoding="utf-8-sig") as f:
        return json.load(f)


def _source_name(source: dict) -> str:
    return str(source.get("name", "")).strip()


def _is_forbidden_scene_source(source: dict) -> bool:
    return source.get("id") == "scene" and _source_name(source) in PUBLIC_FORBIDDEN_SCENE_NAMES


def _is_forbidden_source(source: dict) -> bool:
    name = _source_name(source)
    return (
        name in PUBLIC_FORBIDDEN_SOURCE_NAMES
        or _is_forbidden_scene_source(source)
        or source.get("id") == "window_capture"
    )


def _is_forbidden_scene_item(item: dict) -> bool:
    name = str(item.get("name", "")).strip()
    return name in PUBLIC_FORBIDDEN_SOURCE_NAMES or name in PUBLIC_FORBIDDEN_SCENE_NAMES


def _update_browser_source(source: dict, port: int) -> None:
    name = str(source.get("name", ""))
    settings = source.setdefault("settings", {})

    if source.get("id") != "browser_source":
        return

    slot = _battle_slot_number(name)
    if slot:
        settings["url"] = f"http://localhost:{port}/slot/{slot}?slot_idle=public"
        settings["width"] = BATTLE_SLOT_WIDTH
        settings["height"] = BATTLE_SLOT_HEIGHT
        settings["fps"] = 60
        settings["fps_custom"] = True
        settings["shutdown"] = False
        settings["restart_when_active"] = False
        settings["css"] = PUBLIC_BATTLE_BROWSER_CSS
        settings["reroute_audio"] = False
        return

    if name == "Stats Overlay":
        settings["url"] = f"http://localhost:{port}/overlay?mode=bottom&hide_recent=1"
        settings["width"] = 2560
        settings["height"] = 1440
        settings["fps"] = 30
        return


def _update_scene_item(item: dict) -> None:
    slot = _battle_slot_number(str(item.get("name", "")).strip())
    if not slot:
        return
    item["bounds_type"] = 2
    item["bounds_align"] = 0
    item["bounds_crop"] = False
    item["crop_left"] = 0
    item["crop_top"] = 0
    item["crop_right"] = 0
    item["crop_bottom"] = 0
    item["bounds"] = {"x": float(BATTLE_SLOT_WIDTH), "y": float(BATTLE_SLOT_HEIGHT)}
    item["bounds_rel"] = {"x": 1.7777777910232544, "y": 1.0}
    item["scale"] = {"x": 1.0, "y": 1.0}
    item["scale_rel"] = {"x": 1.0, "y": 1.0}
    if slot in BATTLE_SLOT_POSITIONS:
        item["pos"] = dict(BATTLE_SLOT_POSITIONS[slot])
    if slot in BATTLE_SLOT_POSITIONS_REL:
        item["pos_rel"] = dict(BATTLE_SLOT_POSITIONS_REL[slot])


def build_collection(data: dict, collection_name: str, port: int) -> dict:
    out = dict(data)
    out["name"] = collection_name
    out["scene_order"] = [
        item for item in out.get("scene_order", [])
        if str(item.get("name", "")).strip() not in PUBLIC_FORBIDDEN_SCENE_NAMES
    ]
    scene_names = [str(item.get("name", "")).strip() for item in out["scene_order"] if item.get("name")]
    if out.get("current_scene") in PUBLIC_FORBIDDEN_SCENE_NAMES and scene_names:
        out["current_scene"] = scene_names[0]
    if out.get("current_program_scene") in PUBLIC_FORBIDDEN_SCENE_NAMES and scene_names:
        out["current_program_scene"] = scene_names[0]
    out["sources"] = [
        source for source in out.get("sources", [])
        if not _is_forbidden_source(source)
    ]

    for source in out.get("sources", []):
        _update_browser_source(source, port)
        if source.get("id") == "scene":
            items = source.get("settings", {}).get("items", [])
            source.get("settings", {})["items"] = [
                item for item in items
                if not _is_forbidden_scene_item(item)
            ]
            items = source.get("settings", {}).get("items", [])
            for item in items:
                _update_scene_item(item)

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
