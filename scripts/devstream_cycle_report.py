#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
OUTPUT_JSON = ROOT / "devstream" / "truth" / "cycle-report.json"
OUTPUT_MD = ROOT / "devstream" / "truth" / "cycle-report.md"


def iso_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def read_json(path: Path) -> Any:
    try:
        return json.loads(path.read_text(encoding="utf-8", errors="replace"))
    except Exception:
        return None


def file_meta(path: Path) -> dict[str, Any]:
    exists = path.exists()
    age = time.time() - path.stat().st_mtime if exists else None
    return {
        "path": str(path.relative_to(ROOT)),
        "exists": exists,
        "updatedAt": datetime.fromtimestamp(path.stat().st_mtime, tz=timezone.utc).isoformat() if exists else None,
        "ageSeconds": round(age, 3) if age is not None else None,
    }


def summarize_active_battles(payload: Any) -> dict[str, Any]:
    if not isinstance(payload, dict):
        return {"battleCount": 0}
    battles = payload.get("battles") if isinstance(payload.get("battles"), list) else []
    return {"battleCount": len(battles), "battleIds": [str(item.get("battle_id") or item.get("id") or "") for item in battles[:5] if isinstance(item, dict)]}


def summarize_record(payload: Any) -> dict[str, Any]:
    if not isinstance(payload, dict):
        return {}
    wins = payload.get("wins")
    losses = payload.get("losses")
    total = None
    if isinstance(wins, int) and isinstance(losses, int):
        total = wins + losses
    return {
        "status": payload.get("status"),
        "elo": payload.get("elo") or payload.get("rating"),
        "wins": wins,
        "losses": losses,
        "games": total,
        "updated": payload.get("updated") or payload.get("updated_at"),
    }


def build_payload() -> dict[str, Any]:
    active_path = ROOT / "active_battles.json"
    stream_path = ROOT / "stream_status.json"
    daily_path = ROOT / "daily_stats.json"
    stats_path = ROOT / "battle_stats.json"
    autoresearch_json = ROOT / "replay_analysis" / "autoresearch_latest.json"
    autoresearch_md = ROOT / "replay_analysis" / "reports" / "autoresearch_latest.md"
    active = read_json(active_path)
    stream = read_json(stream_path)
    daily = read_json(daily_path)
    stats = read_json(stats_path)
    report_exists = autoresearch_md.exists()
    blockers: list[str] = []
    warnings: list[str] = []
    if summarize_active_battles(active)["battleCount"]:
        warnings.append("active battles are still present; cycle report is not a final handoff yet")
    if not stream_path.exists() and not daily_path.exists() and not stats_path.exists():
        warnings.append("no battle/stat truth files exist yet; run a bounded session before treating this as performance proof")
    if not report_exists:
        warnings.append("autoresearch report is missing; DEKU should run replay analysis before claiming learning progress")
    return {
        "schemaVersion": "fouler-play-cycle-report/v1",
        "projectId": "fouler-play",
        "generatedAt": iso_now(),
        "readyForHandoff": not blockers and summarize_active_battles(active)["battleCount"] == 0,
        "blockers": blockers,
        "warnings": warnings,
        "activeBattles": summarize_active_battles(active),
        "streamStatus": summarize_record(stream),
        "dailyStats": summarize_record(daily),
        "battleStatsShape": list(stats.keys())[:20] if isinstance(stats, dict) else [],
        "autoresearch": {
            "json": file_meta(autoresearch_json),
            "report": file_meta(autoresearch_md),
        },
        "truthFiles": {
            "activeBattles": file_meta(active_path),
            "streamStatus": file_meta(stream_path),
            "dailyStats": file_meta(daily_path),
            "battleStats": file_meta(stats_path),
        },
        "operatorNote": "A fouler-play devstream cycle should run a bounded battle batch, stop cleanly, write this report, then let DEKU analyze replay/decision evidence before the next batch.",
    }


def write_markdown(payload: dict[str, Any]) -> None:
    lines = [
        "# fouler-play Cycle Report",
        "",
        f"- Generated: `{payload['generatedAt']}`",
        f"- Ready for handoff: `{payload['readyForHandoff']}`",
        f"- Active battles: `{payload['activeBattles']['battleCount']}`",
        f"- Stream ELO: `{payload['streamStatus'].get('elo') or 'unknown'}`",
        f"- Daily record: `{payload['dailyStats'].get('wins') or 0}-{payload['dailyStats'].get('losses') or 0}`",
        "",
        "## Warnings",
        "",
    ]
    warnings = payload.get("warnings") or []
    lines.extend(f"- {item}" for item in warnings) if warnings else lines.append("- none")
    lines.extend(["", payload["operatorNote"], ""])
    OUTPUT_MD.write_text("\n".join(lines), encoding="utf-8")


def main() -> int:
    parser = argparse.ArgumentParser(description="Write fouler-play bounded devstream cycle handoff report.")
    parser.add_argument("--write", action="store_true")
    args = parser.parse_args()
    payload = build_payload()
    if args.write:
        OUTPUT_JSON.parent.mkdir(parents=True, exist_ok=True)
        OUTPUT_JSON.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")
        write_markdown(payload)
        payload["written"] = [str(OUTPUT_JSON), str(OUTPUT_MD)]
    print(json.dumps(payload, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
