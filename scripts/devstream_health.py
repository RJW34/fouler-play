#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import socket
import subprocess
import time
import urllib.error
import urllib.request
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
HTTP_PORT = 8777
OBS_WS_PORT = 4455

SERVICES = [
    "fouler-play.service",
    "fouler-devstream.service",
    "fouler-pipeline.service",
    "fouler-play-watcher.service",
    "fouler-stability-monitor.service",
]

TRUTH_FILES = [
    {"label": "active battles", "path": "active_battles.json", "optional": True, "staleAfterSeconds": 1800},
    {"label": "stream status", "path": "stream_status.json", "optional": True, "staleAfterSeconds": 21600},
    {"label": "daily stats", "path": "daily_stats.json", "optional": True, "staleAfterSeconds": 21600},
    {"label": "battle stats", "path": "battle_stats.json", "optional": True},
    {"label": "autoresearch json", "path": "replay_analysis/autoresearch_latest.json", "optional": True, "staleAfterSeconds": 86400},
    {"label": "autoresearch report", "path": "replay_analysis/reports/autoresearch_latest.md", "optional": True, "staleAfterSeconds": 86400},
    {"label": "stability report", "path": "stability_report.json", "optional": True, "staleAfterSeconds": 86400},
]

ENDPOINTS = ["/state", "/status", "/battles", "/overlay/hybrid", "/dashboard/hybrid", "/slot/1"]


def iso_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def iso_from_epoch(value: float | None) -> str | None:
    if value is None:
        return None
    return datetime.fromtimestamp(value, tz=timezone.utc).isoformat()


def run_command(command: list[str], *, timeout: int = 4) -> subprocess.CompletedProcess[str] | None:
    try:
        return subprocess.run(command, capture_output=True, text=True, timeout=timeout, check=False)
    except Exception:
        return None


def systemctl_state(unit: str) -> dict[str, Any]:
    result = run_command(["systemctl", "--user", "is-active", unit])
    state = (result.stdout.strip() if result and result.stdout.strip() else "unknown")
    if result and result.returncode != 0:
        state = state or "inactive"
    enabled = run_command(["systemctl", "--user", "is-enabled", unit])
    enabled_state = enabled.stdout.strip() if enabled and enabled.stdout.strip() else "unknown"
    return {"activeState": state, "enabledState": enabled_state, "active": state == "active"}


def port_open(port: int, host: str = "127.0.0.1") -> bool:
    try:
        with socket.create_connection((host, port), timeout=0.35):
            return True
    except OSError:
        return False


def fetch_endpoint(path: str, *, timeout: float = 1.25) -> dict[str, Any]:
    url = f"http://127.0.0.1:{HTTP_PORT}{path}"
    try:
        with urllib.request.urlopen(url, timeout=timeout) as response:
            content_type = response.headers.get("content-type", "")
            body = response.read(4096)
            parsed: Any = None
            if "json" in content_type:
                try:
                    parsed = json.loads(body.decode("utf-8", errors="replace"))
                except json.JSONDecodeError:
                    parsed = None
            return {"url": url, "ok": True, "statusCode": response.status, "contentType": content_type, "json": parsed}
    except urllib.error.HTTPError as exc:
        return {"url": url, "ok": False, "statusCode": exc.code, "error": str(exc)}
    except Exception as exc:
        return {"url": url, "ok": False, "statusCode": None, "error": str(exc)}


def truth_file_status(spec: dict[str, Any]) -> dict[str, Any]:
    rel = str(spec["path"])
    path = ROOT / rel
    exists = path.exists()
    mtime = path.stat().st_mtime if exists else None
    age = time.time() - mtime if mtime else None
    stale_after = spec.get("staleAfterSeconds")
    stale = bool(stale_after and age is not None and age > int(stale_after))
    parsed: Any = None
    if exists and path.suffix.lower() == ".json":
        try:
            parsed = json.loads(path.read_text(encoding="utf-8", errors="replace"))
        except Exception:
            parsed = None
    return {
        "label": spec.get("label") or rel,
        "path": str(path),
        "relativePath": rel,
        "exists": exists,
        "optional": bool(spec.get("optional")),
        "mtime": iso_from_epoch(mtime),
        "ageSeconds": round(age, 3) if age is not None else None,
        "staleAfterSeconds": stale_after,
        "stale": stale,
        "summary": summarize_truth(rel, parsed),
    }


def summarize_truth(rel: str, parsed: Any) -> dict[str, Any] | None:
    if not isinstance(parsed, dict):
        return None
    if rel == "active_battles.json":
        battles = parsed.get("battles") if isinstance(parsed.get("battles"), list) else []
        return {"battleCount": len(battles), "updated": parsed.get("updated") or parsed.get("updated_at")}
    if rel == "daily_stats.json":
        return {"wins": parsed.get("wins"), "losses": parsed.get("losses")}
    if rel == "stream_status.json":
        return {"status": parsed.get("status"), "elo": parsed.get("elo"), "updated": parsed.get("updated") or parsed.get("updated_at")}
    return None


def active_battle_count(truth: list[dict[str, Any]]) -> int:
    for item in truth:
        if item["relativePath"] == "active_battles.json" and isinstance(item.get("summary"), dict):
            value = item["summary"].get("battleCount")
            return int(value or 0)
    return 0


def git_status() -> dict[str, Any]:
    commit = run_command(["git", "rev-parse", "--short", "HEAD"], timeout=3)
    dirty = run_command(["git", "status", "--short"], timeout=3)
    return {
        "commit": commit.stdout.strip() if commit and commit.returncode == 0 else None,
        "dirty": bool(dirty and dirty.stdout.strip()),
    }


def build_payload(*, check_http: bool = True) -> dict[str, Any]:
    services = {unit: systemctl_state(unit) for unit in SERVICES}
    truth = [truth_file_status(spec) for spec in TRUTH_FILES]
    active_services = [name for name, state in services.items() if state["active"]]
    http_open = port_open(HTTP_PORT)
    obs_open = port_open(OBS_WS_PORT)
    endpoints = {path: fetch_endpoint(path) for path in ENDPOINTS} if check_http and http_open else {}
    stale_truth = [item for item in truth if item["exists"] and item["stale"]]
    missing_required = [item for item in truth if not item["optional"] and not item["exists"]]
    battle_count = active_battle_count(truth)

    blockers: list[str] = []
    if not active_services and not http_open and battle_count == 0:
        blockers.append("fouler-play runner and OBS HTTP server are idle")
    if http_open and check_http:
        failed = [path for path, result in endpoints.items() if not result.get("ok")]
        blockers.extend(f"OBS endpoint failed: {path}" for path in failed)
    if missing_required:
        blockers.extend(f"missing truth file: {item['relativePath']}" for item in missing_required)
    if stale_truth:
        blockers.extend(f"stale truth file: {item['relativePath']}" for item in stale_truth)

    running = bool(active_services or http_open or battle_count > 0)
    healthy = running and not missing_required and (not check_http or not http_open or all(result.get("ok") for result in endpoints.values()))
    if healthy and stale_truth:
        healthy = False
    if healthy:
        status = "running"
    elif running:
        status = "degraded"
    else:
        status = "idle"

    return {
        "schemaVersion": "devstream-health/v1",
        "projectId": "fouler-play",
        "checkedAt": iso_now(),
        "healthy": healthy,
        "running": running,
        "status": status,
        "blockers": blockers,
        "services": services,
        "ports": {
            "obsHttp": {"port": HTTP_PORT, "open": http_open},
            "obsWebSocket": {"port": OBS_WS_PORT, "open": obs_open},
        },
        "endpoints": endpoints,
        "activeBattleCount": battle_count,
        "truth": truth,
        "git": git_status(),
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="Read-only fouler-play devstream health probe")
    parser.add_argument("--skip-http", action="store_true", help="skip local HTTP endpoint checks")
    parser.add_argument("--write", action="store_true", help="also write devstream/truth/health.json")
    args = parser.parse_args()
    payload = build_payload(check_http=not args.skip_http)
    print(json.dumps(payload, indent=2, sort_keys=True))
    if args.write:
        output = ROOT / "devstream" / "truth" / "health.json"
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
