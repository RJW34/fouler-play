#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_SOURCE = ROOT / "replay_analysis" / "autoresearch_latest.json"
DEFAULT_OUTPUT_DIR = ROOT / "devstream" / "work_packets" / "generated"


def slugify(text: str) -> str:
    text = re.sub(r"[^a-zA-Z0-9]+", "-", text.strip().lower()).strip("-")
    return text[:60] or "fouler-improvement"


def load_source(path: Path) -> dict[str, Any]:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else {}
    except Exception:
        return {}


def extract_findings(data: dict[str, Any]) -> list[dict[str, Any]]:
    candidates: list[Any] = []
    for key in ("findings", "recommendations", "issues", "failures", "action_items"):
        value = data.get(key)
        if isinstance(value, list):
            candidates.extend(value)
    if not candidates and data:
        summary = data.get("summary") or data.get("notes") or "Review latest autoresearch output and create a targeted improvement."
        candidates.append({"title": "Review autoresearch output", "summary": summary})
    findings: list[dict[str, Any]] = []
    for idx, item in enumerate(candidates[:10], start=1):
        if isinstance(item, str):
            title = item.split(".")[0][:80] or f"Finding {idx}"
            findings.append({"title": title, "summary": item, "evidence": []})
        elif isinstance(item, dict):
            title = str(item.get("title") or item.get("name") or item.get("summary") or f"Finding {idx}")[:100]
            summary = str(item.get("summary") or item.get("description") or title)
            evidence = item.get("evidence") if isinstance(item.get("evidence"), list) else []
            findings.append({"title": title, "summary": summary, "evidence": [str(e) for e in evidence]})
    return findings


def build_packet(finding: dict[str, Any], index: int, source: Path) -> dict[str, Any]:
    slug = slugify(finding["title"])
    return {
        "schemaVersion": "devstream-work-packet/v1",
        "project_id": "fouler-play",
        "target_repo": str(ROOT),
        "task_type": "visual-content",
        "stream_role": "visual-content",
        "id": f"fouler-auto-{index:03d}-{slug}",
        "title": finding["title"],
        "status": "draft",
        "objective": finding["summary"],
        "source_report": str(source.relative_to(ROOT)) if source.is_relative_to(ROOT) else str(source),
        "evidence": finding.get("evidence") or [],
        "proposed_change": ["Inspect replay/decision evidence", "Patch the narrowest evaluation, prediction, or reporting logic that explains the failure"],
        "allowed_paths": ["fp/", "replay_analysis/", "tests/", "devstream/"],
        "forbidden_paths": [".env", "config/secrets*"],
        "runtime_dependencies": ["Pokemon Showdown credentials", "bounded session truth files", "autoresearch report"],
        "acceptance_checks": ["python3 -m pytest tests/test_autoresearch.py tests/test_state_endpoint_status.py", "python3 scripts/devstream_session.py doctor"],
        "requires_human_approval": True,
        "risk": "medium",
        "done_when": ["A bounded batch can produce updated replay/report proof", "The related failure class is reduced or explicitly marked unresolved"],
        "owner": "DEKU",
        "createdAt": datetime.now(timezone.utc).isoformat()
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="Convert fouler-play autoresearch output into draft devstream work packets")
    parser.add_argument("--source", type=Path, default=DEFAULT_SOURCE)
    parser.add_argument("--output-dir", type=Path, default=DEFAULT_OUTPUT_DIR)
    parser.add_argument("--write", action="store_true", help="write generated packets; default prints JSON only")
    args = parser.parse_args()
    data = load_source(args.source)
    findings = extract_findings(data)
    packets = [build_packet(finding, idx, args.source) for idx, finding in enumerate(findings, start=1)]
    payload = {"schemaVersion": "fouler-play-packetizer/v1", "source": str(args.source), "packetCount": len(packets), "packets": packets}
    if args.write:
        args.output_dir.mkdir(parents=True, exist_ok=True)
        for packet in packets:
            path = args.output_dir / f"{packet['id']}.json"
            path.write_text(json.dumps(packet, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    print(json.dumps(payload, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
