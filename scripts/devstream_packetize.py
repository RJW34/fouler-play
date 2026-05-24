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
DEFAULT_ALLOWED_PATHS = [
    "fp/search/",
    "fp/helpers.py",
    "fp/battle.py",
    "replay_analysis/",
    "tests/test_autoresearch.py",
    "tests/test_eval.py",
    "tests/test_state_endpoint_status.py",
    "devstream/work_packets/",
]
DEFAULT_ACCEPTANCE_CHECKS = [
    "python3 -m pytest tests/test_autoresearch.py tests/test_eval.py tests/test_state_endpoint_status.py -q",
    "python3 scripts/devstream_session.py doctor",
]


def slugify(text: str) -> str:
    text = re.sub(r"[^a-zA-Z0-9]+", "-", text.strip().lower()).strip("-")
    return text[:60] or "fouler-improvement"


def load_source(path: Path) -> dict[str, Any]:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else {}
    except Exception:
        return {}


def _text_items(value: Any) -> list[str]:
    if isinstance(value, str):
        value = [value]
    if not isinstance(value, list):
        return []
    return [text for item in value if (text := str(item).strip())]


def extract_evidence(item: dict[str, Any]) -> list[str]:
    evidence: list[str] = []
    for key in ("evidence", "proof", "examples"):
        evidence.extend(_text_items(item.get(key)))
    return list(dict.fromkeys(evidence))


def extract_findings(data: dict[str, Any]) -> list[dict[str, Any]]:
    candidates: list[Any] = []
    for key in ("top_issue", "topIssue"):
        value = data.get(key)
        if isinstance(value, dict):
            candidates.append(value)
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
            finding = {"title": title, "summary": summary, "evidence": extract_evidence(item)}
            for optional_key in ("key", "recommendation"):
                if text := str(item.get(optional_key) or "").strip():
                    finding[optional_key] = text
            findings.append(finding)
    return dedupe_findings(findings)


def dedupe_findings(findings: list[dict[str, Any]]) -> list[dict[str, Any]]:
    seen: set[tuple[str, str]] = set()
    out: list[dict[str, Any]] = []
    for finding in findings:
        key = str(finding.get("key") or "").strip().lower()
        title = str(finding.get("title") or "").strip().lower()
        dedupe_key = (key, title)
        if dedupe_key in seen:
            continue
        seen.add(dedupe_key)
        out.append(finding)
    return out


def post_patch_eval_plan(finding: dict[str, Any]) -> dict[str, Any]:
    key = str(finding.get("key") or slugify(finding["title"]))
    return {
        "offline_checks": list(DEFAULT_ACCEPTANCE_CHECKS),
        "runtime_gate": (
            "After patch acceptance, HERMES must refresh fouler proof and approve exactly one bounded "
            "JIGGLYPUFF battle proof only when focus/preflight allows it."
        ),
        "expected_runtime_proof": [
            "devstream/truth/latest-elo-proof.json includes a battle after this packet's createdAt timestamp",
            "replay_analysis/autoresearch_latest.json consumes that new battle",
            f"the '{key}' failure class is reduced or explicitly marked unresolved with fresh evidence",
        ],
        "eval_command": "python3 scripts/devstream_post_packet_eval.py --write",
        "proof_artifact": "devstream/truth/post-packet-eval.json",
    }


def source_report_path(source: Path) -> str:
    absolute = source if source.is_absolute() else ROOT / source
    try:
        return absolute.resolve().relative_to(ROOT).as_posix()
    except ValueError:
        return str(source).replace("\\", "/")


def build_packet(finding: dict[str, Any], index: int, source: Path) -> dict[str, Any]:
    slug = slugify(finding["title"])
    recommendation = str(finding.get("recommendation") or "").strip()
    proposed_change = ["Inspect replay/decision evidence named in evidence[]"]
    if recommendation:
        proposed_change.append(recommendation)
    else:
        proposed_change.append("Patch the narrowest evaluation, prediction, or reporting logic that explains the failure")
    return {
        "schemaVersion": "devstream-work-packet/v1",
        "project_id": "fouler-play",
        "target_repo": str(ROOT),
        "task_type": "battle-policy-improvement",
        "stream_role": "code-eval-work-packet",
        "id": f"fouler-auto-{index:03d}-{slug}",
        "title": finding["title"],
        "status": "draft",
        "objective": finding["summary"],
        "finding_key": finding.get("key") or slug,
        "recommendation": recommendation,
        "source_report": source_report_path(source),
        "evidence": finding.get("evidence") or [],
        "proposed_change": proposed_change,
        "allowed_paths": DEFAULT_ALLOWED_PATHS,
        "forbidden_paths": [".env", "config/secrets*"],
        "runtime_dependencies": ["Pokemon Showdown credentials", "bounded session truth files", "autoresearch report"],
        "acceptance_checks": DEFAULT_ACCEPTANCE_CHECKS,
        "post_patch_eval_plan": post_patch_eval_plan(finding),
        "authority": {
            "source_of_truth": "HERMES",
            "worker_role": "JIGGLYPUFF may run bounded battles only after HERMES focus/preflight approval",
            "runtime_mutation_allowed_by_packet": False,
        },
        "requires_human_approval": True,
        "requires_human_approval_for_runtime": True,
        "runtime_mutation_allowed": False,
        "risk": "medium",
        "done_when": [
            "Offline acceptance checks pass after the patch",
            "A bounded post-packet battle proof is produced only through the HERMES approval gate",
            "The related failure class is reduced or explicitly marked unresolved with fresh replay evidence",
        ],
        "owner": "HERMES",
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
