#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_PACKET_DIR = ROOT / "devstream" / "work_packets" / "generated"
DEFAULT_ELO_PROOF = ROOT / "devstream" / "truth" / "latest-elo-proof.json"
DEFAULT_AUTORESEARCH = ROOT / "replay_analysis" / "autoresearch_latest.json"
DEFAULT_OUTPUT = ROOT / "devstream" / "truth" / "post-packet-eval.json"


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def read_json(path: Path) -> dict[str, Any]:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return {}
    return data if isinstance(data, dict) else {}


def read_bundle(path: str | None) -> dict[str, Any]:
    if not path:
        return {}
    text = sys.stdin.read() if path == "-" else Path(path).read_text(encoding="utf-8")
    try:
        data = json.loads(text)
    except Exception:
        return {}
    return data if isinstance(data, dict) else {}


def parse_timestamp(value: Any) -> datetime | None:
    if not isinstance(value, str) or not value.strip():
        return None
    text = value.strip().replace("Z", "+00:00")
    try:
        parsed = datetime.fromisoformat(text)
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def newer_than(left: Any, right: Any) -> bool | None:
    left_dt = parse_timestamp(left)
    right_dt = parse_timestamp(right)
    if left_dt is None or right_dt is None:
        return None
    return left_dt > right_dt


def string_list(value: Any) -> list[str]:
    if isinstance(value, str):
        value = [value]
    if not isinstance(value, list):
        return []
    return [text for item in value if (text := str(item).strip())]


def latest_packet(packet_dir: Path) -> dict[str, Any]:
    try:
        paths = sorted(packet_dir.glob("*.json"), key=lambda path: path.stat().st_mtime, reverse=True)
    except OSError:
        return {}
    for path in paths:
        packet = read_json(path)
        if packet.get("schemaVersion") == "devstream-work-packet/v1":
            packet["_path"] = str(path)
            return packet
    return {}


def latest_battle_from_elo(elo_proof: dict[str, Any]) -> dict[str, Any]:
    summary = elo_proof.get("summary") if isinstance(elo_proof.get("summary"), dict) else {}
    battle_id = summary.get("latestBattleId")
    battle_at = summary.get("latestBattleAt")
    games = elo_proof.get("games") if isinstance(elo_proof.get("games"), list) else []
    if (not battle_id or not battle_at) and games:
        latest = games[-1] if isinstance(games[-1], dict) else {}
        battle_id = battle_id or latest.get("battleId") or latest.get("battle_id")
        battle_at = battle_at or latest.get("timestamp")
    return {
        "id": battle_id,
        "at": battle_at,
        "learningVerified": summary.get("latestBattleLearningVerified") is True,
        "performanceImprovementVerified": summary.get("performanceImprovementVerified") is True,
        "performanceTrendStatus": summary.get("performanceTrendStatus") or "unknown",
        "ratingDelta": summary.get("ratingDelta"),
        "winRate": summary.get("winRate"),
        "finalRating": summary.get("finalRating"),
    }


def autoresearch_generated_at(autoresearch: dict[str, Any]) -> Any:
    return autoresearch.get("generated_at") or autoresearch.get("generatedAt")


def autoresearch_covers_battle(autoresearch: dict[str, Any], battle_id: Any) -> bool:
    battle_text = str(battle_id or "").strip()
    if not battle_text:
        return False
    batch = autoresearch.get("batch") if isinstance(autoresearch.get("batch"), dict) else {}
    if battle_text in {
        str(batch.get("start_battle_id") or ""),
        str(batch.get("end_battle_id") or ""),
    }:
        return True
    return battle_text in json.dumps(autoresearch, sort_keys=True)


def finding_key(packet: dict[str, Any]) -> str:
    return str(packet.get("finding_key") or packet.get("findingKey") or "").strip()


def matching_issues(autoresearch: dict[str, Any], key: str) -> list[dict[str, Any]]:
    if not key:
        return []
    matches: list[dict[str, Any]] = []
    top = autoresearch.get("top_issue") or autoresearch.get("topIssue")
    if isinstance(top, dict) and str(top.get("key") or "").strip() == key:
        matches.append(top)
    for item in autoresearch.get("issues") or []:
        if isinstance(item, dict) and str(item.get("key") or "").strip() == key:
            matches.append(item)
    return matches


def issue_shift(autoresearch: dict[str, Any], key: str) -> dict[str, Any]:
    regression = autoresearch.get("regression") if isinstance(autoresearch.get("regression"), dict) else {}
    compare = regression.get("issue_compare") if isinstance(regression.get("issue_compare"), dict) else {}
    shifts = compare.get("shifts") if isinstance(compare.get("shifts"), list) else []
    top_shift = compare.get("top_shift") if isinstance(compare.get("top_shift"), dict) else {}
    for item in [top_shift, *shifts]:
        if isinstance(item, dict) and str(item.get("key") or "").strip() == key:
            return item
    return {}


def failure_class(packet: dict[str, Any], autoresearch: dict[str, Any], covers_latest: bool) -> dict[str, Any]:
    key = finding_key(packet)
    issues = matching_issues(autoresearch, key)
    shift = issue_shift(autoresearch, key)
    direction = str(shift.get("direction") or "").strip().lower()
    delta = shift.get("delta")
    try:
        numeric_delta = int(delta)
    except Exception:
        numeric_delta = None
    evidence: list[str] = []
    for issue in issues:
        evidence.extend(string_list(issue.get("proof") or issue.get("evidence")))
    evidence = list(dict.fromkeys(evidence))
    if direction == "better" or (numeric_delta is not None and numeric_delta < 0) or (covers_latest and key and not issues):
        status = "reduced"
    elif covers_latest and issues:
        status = "unresolved-with-fresh-evidence"
    elif direction == "worse" or (numeric_delta is not None and numeric_delta > 0):
        status = "worse"
    else:
        status = "unknown"
    return {
        "key": key,
        "status": status,
        "matchingIssueCount": len(issues),
        "evidence": evidence,
        "shift": shift,
    }


def acceptance_summary(acceptance: dict[str, Any], latest_battle_at: Any) -> dict[str, Any]:
    if not acceptance:
        return {"exists": False, "latestBattleAfterAcceptance": None}
    checked_at = acceptance.get("checkedAtUtc") or acceptance.get("checkedAt")
    packet = acceptance.get("packet") if isinstance(acceptance.get("packet"), dict) else {}
    return {
        "exists": True,
        "status": acceptance.get("status"),
        "checkedAtUtc": checked_at,
        "packetId": packet.get("id"),
        "packetFindingKey": packet.get("findingKey") or packet.get("finding_key"),
        "supportChecksPassed": acceptance.get("ok") is True or int((acceptance.get("summary") or {}).get("failedCommandCount") or 0) == 0,
        "latestBattleAfterAcceptance": newer_than(latest_battle_at, checked_at),
    }


def packet_evidence_integrity(packet: dict[str, Any]) -> dict[str, Any]:
    integrity = packet.get("evidence_integrity") or packet.get("evidenceIntegrity")
    return integrity if isinstance(integrity, dict) else {}


def build_report(
    *,
    packet: dict[str, Any],
    elo_proof: dict[str, Any],
    autoresearch: dict[str, Any],
    acceptance: dict[str, Any] | None = None,
) -> dict[str, Any]:
    acceptance = acceptance or {}
    latest_battle = latest_battle_from_elo(elo_proof)
    latest_battle_at = latest_battle.get("at")
    packet_created_at = packet.get("createdAt")
    latest_battle_after_packet = newer_than(latest_battle_at, packet_created_at)
    covers_latest = autoresearch_covers_battle(autoresearch, latest_battle.get("id"))
    generated_after_latest = newer_than(autoresearch_generated_at(autoresearch), latest_battle_at)
    failure = failure_class(packet, autoresearch, covers_latest)
    acceptance_info = acceptance_summary(acceptance, latest_battle_at)
    integrity = packet_evidence_integrity(packet)
    blockers: list[str] = []
    warnings: list[str] = []

    if not packet:
        status = "packet-missing"
        blockers.append("no devstream work packet is available for post-packet evaluation")
    elif integrity and integrity.get("ok") is not True:
        status = "packet-evidence-integrity-blocked"
        blockers.extend(string_list(integrity.get("blockers")) or ["packet evidence_integrity.ok is false"])
    elif not elo_proof:
        status = "elo-proof-missing"
        blockers.append("devstream/truth/latest-elo-proof.json is missing or unreadable")
    elif latest_battle_after_packet is not True:
        status = "awaiting-post-packet-battle-proof"
        blockers.append("latest ELO proof does not include a battle after the packet createdAt timestamp")
    elif covers_latest is not True or generated_after_latest is False:
        status = "awaiting-post-packet-autoresearch"
        blockers.append("latest post-packet battle has not been consumed by fresh autoresearch")
    elif latest_battle.get("performanceImprovementVerified") is True or failure["status"] == "reduced":
        status = "post-packet-eval-improving"
    elif failure["status"] == "unresolved-with-fresh-evidence":
        status = "post-packet-eval-actionable-unresolved"
        warnings.append("post-packet battle was evaluated, but the packet failure class is still present")
    else:
        status = "post-packet-eval-not-actionable"
        blockers.append("post-packet proof exists, but failure-class status is not actionable")

    if acceptance and acceptance_info.get("latestBattleAfterAcceptance") is False:
        warnings.append("latest battle is not newer than the support-only acceptance proof")
    if packet.get("status") == "draft":
        warnings.append("matching packet is still draft; mark implementation state before claiming patch closure")

    actionable = status in {"post-packet-eval-improving", "post-packet-eval-actionable-unresolved"}
    return {
        "schemaVersion": "fouler-play-post-packet-eval/v1",
        "checkedAtUtc": utc_now(),
        "status": status,
        "ok": True,
        "actionablePostPacketEval": actionable,
        "runtimeMutationTouched": False,
        "networkSendAllowed": False,
        "packet": {
            "exists": bool(packet),
            "path": packet.get("_path") or packet.get("path") or "",
            "id": packet.get("id"),
            "status": packet.get("status"),
            "createdAt": packet_created_at,
            "findingKey": finding_key(packet),
            "title": packet.get("title"),
        },
        "latestBattle": latest_battle,
        "proofWindow": {
            "latestBattleAfterPacket": latest_battle_after_packet,
            "autoresearchCoversLatestBattle": covers_latest,
            "autoresearchGeneratedAt": autoresearch_generated_at(autoresearch),
            "autoresearchGeneratedAfterLatestBattle": generated_after_latest,
            "latestBattleAfterAcceptance": acceptance_info.get("latestBattleAfterAcceptance"),
        },
        "acceptance": acceptance_info,
        "evidenceIntegrity": integrity or {"exists": False},
        "failureClass": failure,
        "blockers": blockers,
        "warnings": warnings,
        "nextActions": next_actions(status),
    }


def next_actions(status: str) -> list[str]:
    if status == "awaiting-post-packet-battle-proof":
        return ["After HERMES focus/preflight allows it, run exactly one bounded JIGGLYPUFF battle proof and refresh ELO proof."]
    if status == "awaiting-post-packet-autoresearch":
        return ["Run replay_analysis/autoresearch.py --no-discord against the refreshed latest-elo-proof.json, then rerun this evaluator."]
    if status == "post-packet-eval-actionable-unresolved":
        return ["Produce a narrower follow-up packet from the fresh unresolved evidence instead of rerunning stale work."]
    if status == "post-packet-eval-improving":
        return ["Mark the packet evaluated and keep the next bounded batch small enough to preserve attribution."]
    return ["Inspect packet, latest ELO proof, and autoresearch timestamps before requesting another runtime proof."]


def write_outputs(report: dict[str, Any], output: Path) -> None:
    output.parent.mkdir(parents=True, exist_ok=True)
    text = json.dumps(report, indent=2, sort_keys=True) + "\n"
    output.write_text(text, encoding="utf-8")
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    (output.parent / f"post-packet-eval-{stamp}.json").write_text(text, encoding="utf-8")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Classify fouler-play post-packet battle/eval proof without runtime mutation.")
    parser.add_argument("--packet", type=Path)
    parser.add_argument("--packet-dir", type=Path, default=DEFAULT_PACKET_DIR)
    parser.add_argument("--elo-proof", type=Path, default=DEFAULT_ELO_PROOF)
    parser.add_argument("--autoresearch", type=Path, default=DEFAULT_AUTORESEARCH)
    parser.add_argument("--acceptance", type=Path)
    parser.add_argument("--bundle", help="JSON bundle path, or '-' for stdin, with packet/eloProof/autoresearch/acceptance keys")
    parser.add_argument("--write", action="store_true")
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    bundle = read_bundle(args.bundle)
    packet = bundle.get("packet") if isinstance(bundle.get("packet"), dict) else {}
    if not packet:
        packet = read_json(args.packet) if args.packet else latest_packet(args.packet_dir)
    elo_proof = bundle.get("eloProof") if isinstance(bundle.get("eloProof"), dict) else read_json(args.elo_proof)
    autoresearch = bundle.get("autoresearch") if isinstance(bundle.get("autoresearch"), dict) else read_json(args.autoresearch)
    acceptance = bundle.get("acceptance") if isinstance(bundle.get("acceptance"), dict) else (read_json(args.acceptance) if args.acceptance else {})
    report = build_report(packet=packet, elo_proof=elo_proof, autoresearch=autoresearch, acceptance=acceptance)
    print(json.dumps(report, indent=2, sort_keys=True))
    if args.write:
        write_outputs(report, args.output)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
