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
BATTLE_ID_RE = re.compile(r"\b(?:battle-)?gen\d+[a-z0-9]*-\d+(?:-[a-z0-9]+)?\b", re.IGNORECASE)
TRACE_ONLY_DECISION_RE = re.compile(
    r"\b(decision[_ -]?instability|decision trace|fallback|timeout|repeated same action|loop)\b",
    re.IGNORECASE,
)
MECHANICS_OR_MATCHUP_RE = re.compile(
    r"\b(type|ability|damage|weak|resist|immune|immunity|terrain|weather|tera|hazard pressure|speed tier|coverage)\b",
    re.IGNORECASE,
)
REQUEST_LEGAL_OPTION_RE = re.compile(
    r"\b(requestHash|legal options|legalMoves|legalSwitches|candidateSet|showdown request|showdown-request)\b",
    re.IGNORECASE,
)
REQUEST_HASH_RE = re.compile(r"\brequestHash=([a-f0-9]{64})\b", re.IGNORECASE)
LEGAL_COUNT_RE = re.compile(r"\blegal(?:Moves|Switches)=(\d+)\b")


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


def extract_battle_ids(texts: list[str]) -> list[str]:
    ids: list[str] = []
    for text in texts:
        ids.extend(match.group(0) for match in BATTLE_ID_RE.finditer(text))
    return list(dict.fromkeys(ids))


def _positive_int(value: Any) -> bool:
    try:
        return int(value) > 0
    except (TypeError, ValueError):
        return False


def _raw_showdown_request_has_legal_options(value: dict[str, Any]) -> bool:
    active = value.get("active") if isinstance(value.get("active"), list) else []
    legal_move_count = 0
    for request in active:
        if not isinstance(request, dict):
            continue
        moves = request.get("moves") if isinstance(request.get("moves"), list) else []
        legal_move_count += sum(1 for move in moves if isinstance(move, dict) and move.get("disabled") is not True)
    side = value.get("side") if isinstance(value.get("side"), dict) else {}
    side_pokemon = side.get("pokemon") if isinstance(side.get("pokemon"), list) else []
    legal_switch_count = sum(
        1
        for mon in side_pokemon
        if isinstance(mon, dict)
        and mon.get("active") is not True
        and not str(mon.get("condition") or "").startswith("0 fnt")
    )
    return bool(active or side_pokemon) and (
        legal_move_count > 0
        or legal_switch_count > 0
        or "forceSwitch" in value
        or "wait" in value
    )


def _structured_request_legal_option_evidence(value: Any) -> bool:
    if isinstance(value, dict):
        if _raw_showdown_request_has_legal_options(value):
            return True
        has_request_hash = isinstance(value.get("requestHash"), str) and bool(re.fullmatch(r"[a-f0-9]{64}", value["requestHash"], re.IGNORECASE))
        legal_moves = value.get("legalMoves") or value.get("legal_moves")
        legal_switches = value.get("legalSwitches") or value.get("legal_switches")
        candidate_bounded = value.get("candidateSetBounded") is True or value.get("candidate_set_bounded") is True
        if has_request_hash and candidate_bounded and (
            (isinstance(legal_moves, list) and bool(legal_moves))
            or (isinstance(legal_switches, list) and bool(legal_switches))
            or value.get("forceSwitch") is not None
            or value.get("wait") is not None
        ):
            return True
        return any(_structured_request_legal_option_evidence(child) for child in value.values())
    if isinstance(value, list):
        return any(_structured_request_legal_option_evidence(child) for child in value)
    return False


def _proof_text_has_request_legal_option_evidence(text: str) -> bool:
    if _text_has_showdown_request_protocol(text):
        return True
    if not REQUEST_LEGAL_OPTION_RE.search(text):
        return False
    if not REQUEST_HASH_RE.search(text):
        return False
    counts = [int(match.group(1)) for match in LEGAL_COUNT_RE.finditer(text)]
    return any(count > 0 for count in counts)


def _text_has_showdown_request_protocol(text: str) -> bool:
    for line in text.splitlines():
        if "|request|" not in line:
            continue
        raw = line.split("|request|", 1)[1].strip()
        try:
            payload = json.loads(raw)
        except json.JSONDecodeError:
            continue
        if isinstance(payload, dict) and _raw_showdown_request_has_legal_options(payload):
            return True
    return False


def has_request_legal_option_evidence(source_data: dict[str, Any], finding: dict[str, Any], evidence: list[str]) -> bool:
    if _structured_request_legal_option_evidence(source_data) or _structured_request_legal_option_evidence(finding):
        return True
    for value in (source_data.get("protocol_lines"), source_data.get("protocolLines"), source_data.get("showdown_protocol"), source_data.get("showdownProtocol")):
        if isinstance(value, list) and any(_text_has_showdown_request_protocol(str(item)) for item in value):
            return True
        if isinstance(value, str) and _text_has_showdown_request_protocol(value):
            return True
    integrity = source_data.get("evidence_integrity") if isinstance(source_data.get("evidence_integrity"), dict) else {}
    if not _positive_int(integrity.get("losses_with_request_legal_options")):
        return False
    return any(_proof_text_has_request_legal_option_evidence(text) for text in evidence)


def evidence_integrity(source_data: dict[str, Any], finding: dict[str, Any]) -> dict[str, Any]:
    evidence = list(finding.get("evidence") or [])
    battle_ids = extract_battle_ids(evidence)
    batch = source_data.get("batch") if isinstance(source_data.get("batch"), dict) else {}
    grounded = source_data.get("grounded_context") if isinstance(source_data.get("grounded_context"), dict) else {}
    source_contract = str(grounded.get("source") or "")
    unsupported_claims = [
        str(item)
        for item in source_data.get("unsupported_mechanics_claims", [])
        if str(item).strip()
    ] if isinstance(source_data.get("unsupported_mechanics_claims"), list) else []
    claims_without_evidence = [
        item
        for item in source_data.get("evidence_integrity", {}).get("claims_without_evidence", [])
        if item
    ] if isinstance(source_data.get("evidence_integrity"), dict) else []
    rejected_claims = [
        item
        for item in source_data.get("mechanics_claims", [])
        if isinstance(item, dict) and str(item.get("status") or "").lower() == "rejected"
    ] if isinstance(source_data.get("mechanics_claims"), list) else []
    blockers: list[str] = []
    warnings: list[str] = []
    finding_text = "\n".join(
        str(finding.get(key) or "")
        for key in ("key", "title", "summary", "recommendation")
    ) + "\n" + "\n".join(evidence)
    trace_only_issue = bool(TRACE_ONLY_DECISION_RE.search(finding_text)) and not bool(MECHANICS_OR_MATCHUP_RE.search(finding_text))
    request_legal_option_evidence = has_request_legal_option_evidence(source_data, finding, evidence)
    if not evidence:
        blockers.append("finding has no replay, trace, or battle proof strings")
    if evidence and not battle_ids:
        blockers.append("finding evidence is not linked to any Showdown battle id")
    if not source_data.get("generated_at") and not source_data.get("generatedAt"):
        blockers.append("source report has no generated_at timestamp")
    if not batch.get("id"):
        blockers.append("source report has no batch id")
    if not source_contract:
        warnings.append("source report did not expose grounded_context.source")
    if unsupported_claims:
        blockers.append("source report contains unsupported mechanics claims")
    if claims_without_evidence and not trace_only_issue:
        blockers.append("source report evidence_integrity contains claims without replay/trace evidence")
    elif claims_without_evidence and not request_legal_option_evidence:
        blockers.append("trace-only finding lacks request-backed legal-option evidence")
    elif claims_without_evidence:
        warnings.append("source report has mechanics/strategy claim gaps; this packet is only promotable for trace-only fallback/runtime fixes")
    if rejected_claims:
        blockers.append("source report contains rejected mechanics claims")
    return {
        "ok": not blockers,
        "battleIds": battle_ids,
        "battleIdCount": len(battle_ids),
        "allEvidenceBattleLinked": bool(evidence) and bool(battle_ids),
        "sourceGeneratedAt": source_data.get("generated_at") or source_data.get("generatedAt"),
        "sourceBatchId": batch.get("id"),
        "mechanicsClaimsValidated": not unsupported_claims and not rejected_claims,
        "requestLegalOptionEvidence": request_legal_option_evidence,
        "unsupportedMechanicsClaimCount": len(unsupported_claims) + len(rejected_claims),
        "claimsWithoutEvidenceCount": len(claims_without_evidence),
        "sourceContract": source_contract,
        "blockers": blockers,
        "warnings": warnings,
    }


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


def build_packet(finding: dict[str, Any], index: int, source: Path, source_data: dict[str, Any] | None = None) -> dict[str, Any]:
    slug = slugify(finding["title"])
    recommendation = str(finding.get("recommendation") or "").strip()
    integrity = evidence_integrity(source_data if source_data is not None else load_source(source), finding)
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
        "evidence_integrity": integrity,
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
            "evidence_integrity.ok is true with battle-linked proof before any source patch is promoted",
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
    packets = [build_packet(finding, idx, args.source, data) for idx, finding in enumerate(findings, start=1)]
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
