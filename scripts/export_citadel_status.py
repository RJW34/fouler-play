#!/usr/bin/env python3
from __future__ import annotations

import json
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
OUTPUT = ROOT / "state" / "citadel_status.json"


def run_json(command: list[str]) -> dict[str, Any]:
    result = subprocess.run(command, cwd=str(ROOT), capture_output=True, text=True, timeout=30, check=False)
    if result.stdout.strip():
        try:
            return json.loads(result.stdout)
        except json.JSONDecodeError:
            pass
    return {"error": result.stderr.strip() or result.stdout.strip(), "returnCode": result.returncode}


def signal(name: str, label: str, value: Any, status: str = "neutral") -> dict[str, str]:
    return {"name": name, "label": label, "value": str(value), "status": status}


def main() -> int:
    health = run_json([sys.executable, "scripts/devstream_health.py"])
    active_battles = int(health.get("activeBattleCount") or 0)
    ports = health.get("ports") if isinstance(health.get("ports"), dict) else {}
    stale_truth = [
        item.get("relativePath")
        for item in health.get("truth", [])
        if isinstance(item, dict) and item.get("stale")
    ]
    running = bool(health.get("running"))
    healthy = bool(health.get("healthy"))
    payload = {
        "schema_version": 1,
        "project_id": "fouler-play",
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "status": "active" if running else "idle",
        "health": "healthy" if healthy else ("warning" if running else "idle"),
        "headline": f"fouler-play {health.get('status', 'unknown')}; {active_battles} active battles.",
        "signals": [
            signal("active_battles", "Active battles", active_battles, "good" if active_battles else "neutral"),
            signal("obs_http", "OBS HTTP 8777", "open" if (ports.get("obsHttp") or {}).get("open") else "closed", "good" if (ports.get("obsHttp") or {}).get("open") else "warn"),
            signal("stale_truth", "Stale truth files", len(stale_truth), "good" if not stale_truth else "warn"),
        ],
        "artifacts": [
            {"label": "Devstream contract", "path": "devstream.yaml", "kind": "contract"},
            {"label": "Devstream health", "path": "scripts/devstream_health.py", "kind": "probe"},
            {"label": "ELO proof schema", "path": "devstream/truth/elo-proof.schema.json", "kind": "proof"},
        ],
        "details": {
            "devstream_health": health,
            "stale_truth": stale_truth,
        },
    }
    OUTPUT.parent.mkdir(parents=True, exist_ok=True)
    OUTPUT.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    print(json.dumps(payload, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
