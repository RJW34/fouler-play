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
import socket
from collections.abc import Mapping
from pathlib import Path
from urllib.parse import urlsplit, urlunsplit

try:
    from dotenv import load_dotenv
except ImportError:  # pragma: no cover - optional dependency fallback
    load_dotenv = None


if load_dotenv:
    load_dotenv()


DEFAULT_INPUT = Path(os.path.expandvars(r"%APPDATA%\obs-studio\basic\scenes\fouler_play_scenes.json"))
DEFAULT_OUTPUT = Path(os.path.expandvars(r"%APPDATA%\obs-studio\basic\scenes\fouler_play_hybrid_scenes.json"))


def _load_collection(path: Path) -> dict:
    with path.open("r", encoding="utf-8-sig") as f:
        return json.load(f)


def _normalize_base_url(raw_url: str) -> str:
    candidate = raw_url.strip()
    if not candidate:
        raise ValueError("OBS browser base URL cannot be empty")
    if "://" not in candidate:
        candidate = f"http://{candidate}"
    parsed = urlsplit(candidate)
    if not parsed.scheme or not parsed.netloc:
        raise ValueError(f"Invalid OBS browser base URL: {raw_url}")
    return urlunsplit((parsed.scheme, parsed.netloc.rstrip("/"), "", "", ""))


def _detect_lan_ip() -> str:
    try:
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as sock:
            sock.connect(("8.8.8.8", 80))
            host = sock.getsockname()[0]
        if host and not host.startswith("127."):
            return host
    except OSError:
        pass
    return "localhost"


def _resolve_base_url(
    port: int,
    explicit_base_url: str | None = None,
    env: Mapping[str, str] | None = None,
) -> str:
    env_map = os.environ if env is None else env

    if explicit_base_url:
        return _normalize_base_url(explicit_base_url)

    env_base_url = (env_map.get("OBS_BROWSER_BASE_URL") or "").strip()
    if env_base_url:
        return _normalize_base_url(env_base_url)

    legacy_idle_url = (env_map.get("OBS_IDLE_URL") or "").strip()
    if legacy_idle_url:
        return _normalize_base_url(legacy_idle_url)

    return _normalize_base_url(f"http://{_detect_lan_ip()}:{port}")


def _build_browser_url(base_url: str, path: str) -> str:
    return f"{base_url}/{path.lstrip('/')}"


def _update_browser_source(source: dict, base_url: str) -> None:
    name = str(source.get("name", ""))
    settings = source.setdefault("settings", {})

    if source.get("id") != "browser_source":
        return

    if name.startswith("Battle Slot "):
        settings["url"] = _build_browser_url(base_url, "idle")
        settings["reroute_audio"] = False
        return

    if name == "Stats Overlay":
        settings["url"] = _build_browser_url(base_url, "overlay/hybrid")
        settings["width"] = 2560
        settings["height"] = 1440
        settings["fps"] = 30
        return

    if name == "Debug Overlay":
        settings["url"] = _build_browser_url(base_url, "dashboard/hybrid")
        settings["width"] = 1280
        settings["height"] = 720
        settings["fps"] = 30
        return


def build_collection(data: dict, collection_name: str, base_url: str) -> dict:
    out = dict(data)
    out["name"] = collection_name

    for source in out.get("sources", []):
        _update_browser_source(source, base_url)
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
        "--base-url",
        default="",
        help="OBS-reachable base URL for browser sources (defaults to OBS_BROWSER_BASE_URL, then OBS_IDLE_URL, then detected LAN IP:port)",
    )
    parser.add_argument(
        "--repo-copy",
        type=Path,
        default=Path("streaming") / "fouler_play_hybrid_scenes.json",
        help="Optional repository copy for versioning",
    )
    args = parser.parse_args()

    if not args.input.exists():
        raise FileNotFoundError(f"Input scene collection not found: {args.input}")

    base_url = _resolve_base_url(args.port, args.base_url or None)
    data = _load_collection(args.input)
    out = build_collection(data, args.name, base_url)

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
    print(f"Browser base URL: {base_url}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
