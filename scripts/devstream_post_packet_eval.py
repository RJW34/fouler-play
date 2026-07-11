#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import re
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_PACKET_DIR = ROOT / "devstream" / "work_packets" / "generated"
DEFAULT_ELO_PROOF = ROOT / "devstream" / "truth" / "latest-elo-proof.json"
DEFAULT_AUTORESEARCH = ROOT / "replay_analysis" / "autoresearch_latest.json"
DEFAULT_OUTPUT = ROOT / "devstream" / "truth" / "post-packet-eval.json"
BATTLE_ID_RE = re.compile(r"battle-gen9ou-[A-Za-z0-9-]+")


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
    final_rating = numeric(summary.get("finalRating"))
    current_rating = numeric(summary.get("currentRating"))
    rating_delta = numeric(summary.get("ratingDelta"))
    rating_delta_source = "summary.ratingDelta" if rating_delta is not None else None
    if rating_delta is None and final_rating is not None and current_rating is not None:
        rating_delta = round(current_rating - final_rating, 2)
        rating_delta_source = "summary.currentRating-minus-summary.finalRating"
    live_rating_improved = rating_delta is not None and rating_delta > 0
    improvement_verified = summary.get("performanceImprovementVerified") is True or live_rating_improved
    return {
        "id": battle_id,
        "at": battle_at,
        "learningVerified": summary.get("latestBattleLearningVerified") is True,
        "performanceImprovementVerified": improvement_verified,
        "performanceTrendStatus": summary.get("performanceTrendStatus") or ("improving" if live_rating_improved else "unknown"),
        "ratingDelta": rating_delta,
        "ratingDeltaSource": rating_delta_source,
        "winRate": summary.get("winRate"),
        "finalRating": final_rating,
        "currentRating": current_rating,
        "currentRatingSource": summary.get("currentRatingSource"),
        "liveProfileRating": summary.get("liveProfileRating"),
    }


def numeric(value: Any) -> float | None:
    if value is None or isinstance(value, bool):
        return None
    try:
        parsed = float(str(value).strip())
    except (TypeError, ValueError):
        return None
    rounded = round(parsed, 2)
    return int(rounded) if rounded.is_integer() else rounded


def post_packet_battles_from_elo(elo_proof: dict[str, Any], packet_created_at: Any) -> list[dict[str, Any]]:
    packet_dt = parse_timestamp(packet_created_at)
    if packet_dt is None:
        return []
    games = elo_proof.get("games") if isinstance(elo_proof.get("games"), list) else []
    post_packet: list[dict[str, Any]] = []
    for game in games:
        if not isinstance(game, dict):
            continue
        battle_at = parse_timestamp(game.get("timestamp"))
        if battle_at is not None and battle_at > packet_dt:
            post_packet.append(game)
    return post_packet


def game_battle_id(game: dict[str, Any]) -> str:
    return str(game.get("battleId") or game.get("battle_id") or "").strip()


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


def autoresearch_issue_candidates(autoresearch: dict[str, Any]) -> list[dict[str, Any]]:
    candidates: list[dict[str, Any]] = []
    top = autoresearch.get("top_issue") or autoresearch.get("topIssue")
    if isinstance(top, dict):
        candidates.append(top)
    for item in autoresearch.get("issues") or []:
        if isinstance(item, dict):
            candidates.append(item)
    unique: list[dict[str, Any]] = []
    seen: set[str] = set()
    for item in candidates:
        key = str(item.get("key") or item.get("title") or "").strip()
        if not key or key in seen:
            continue
        seen.add(key)
        unique.append(item)
    return unique


def next_issue_after_packet(autoresearch: dict[str, Any], packet_key: str) -> dict[str, Any]:
    for issue in autoresearch_issue_candidates(autoresearch):
        key = str(issue.get("key") or "").strip()
        if packet_key and key == packet_key:
            continue
        return {
            "key": key,
            "title": str(issue.get("title") or key or "autoresearch issue"),
            "recommendation": str(
                issue.get("recommendation")
                or "Implement one targeted fix from the latest autoresearch issue."
            ),
            "proof": string_list(issue.get("proof") or issue.get("evidence"))[:5],
        }
    return {}


def issue_shift(autoresearch: dict[str, Any], key: str) -> dict[str, Any]:
    regression = autoresearch.get("regression") if isinstance(autoresearch.get("regression"), dict) else {}
    compare = regression.get("issue_compare") if isinstance(regression.get("issue_compare"), dict) else {}
    shifts = compare.get("shifts") if isinstance(compare.get("shifts"), list) else []
    top_shift = compare.get("top_shift") if isinstance(compare.get("top_shift"), dict) else {}
    for item in [top_shift, *shifts]:
        if isinstance(item, dict) and str(item.get("key") or "").strip() == key:
            return item
    return {}


def evidence_for_battle(evidence: list[str], battle_id: Any) -> list[str]:
    battle_text = str(battle_id or "").strip()
    if not battle_text:
        return []
    return [item for item in evidence if battle_text in item]


def evidence_battle_ids(evidence: list[str]) -> list[str]:
    ids: list[str] = []
    for item in evidence:
        ids.extend(BATTLE_ID_RE.findall(item))
    return list(dict.fromkeys(ids))


def failure_class(
    packet: dict[str, Any],
    autoresearch: dict[str, Any],
    covers_latest: bool,
    latest_battle_id: Any = None,
) -> dict[str, Any]:
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
    fresh_evidence = evidence_for_battle(evidence, latest_battle_id)
    stale_evidence = [item for item in evidence if item not in set(fresh_evidence)]
    stale_battle_ids = [
        battle_id
        for battle_id in evidence_battle_ids(stale_evidence)
        if str(battle_id) != str(latest_battle_id or "")
    ]

    if (
        direction == "better"
        or (numeric_delta is not None and numeric_delta < 0)
        or (covers_latest and key and not issues)
        or (covers_latest and issues and latest_battle_id and not fresh_evidence)
    ):
        status = "reduced"
    elif covers_latest and issues and fresh_evidence:
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
        "freshEvidence": fresh_evidence,
        "staleEvidenceCount": len(stale_evidence),
        "staleEvidenceBattleIds": stale_battle_ids,
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
    post_packet_battles = post_packet_battles_from_elo(elo_proof, packet_created_at)
    post_packet_battle_ids = [battle_id for game in post_packet_battles if (battle_id := game_battle_id(game))]
    covers_latest = autoresearch_covers_battle(autoresearch, latest_battle.get("id"))
    generated_after_latest = newer_than(autoresearch_generated_at(autoresearch), latest_battle_at)
    failure = failure_class(
        packet,
        autoresearch,
        covers_latest,
        latest_battle_id=latest_battle.get("id"),
    )
    followup_issue = (
        next_issue_after_packet(autoresearch, finding_key(packet))
        if failure.get("status") == "reduced"
        else {}
    )
    post_packet_failure_battle_ids = [
        battle_id
        for battle_id in evidence_battle_ids(failure.get("evidence") or [])
        if battle_id in set(post_packet_battle_ids)
    ]
    preservation_satisfied = len(post_packet_battles) >= 2 and not post_packet_failure_battle_ids
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
    elif (
        latest_battle.get("performanceImprovementVerified") is True
        and failure["status"] == "reduced"
        and preservation_satisfied
    ):
        status = "post-packet-eval-accepted"
    elif latest_battle.get("performanceImprovementVerified") is True and failure["status"] == "reduced":
        status = "post-packet-eval-improving"
    elif failure["status"] == "reduced":
        status = "post-packet-eval-actionable-unresolved"
        warnings.append("target failure class was reduced, but the aggregate performance signal is not positive")
    elif latest_battle.get("performanceImprovementVerified") is True:
        status = "post-packet-eval-actionable-unresolved"
        warnings.append("aggregate performance improved, but the packet failure class is still present")
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
    if failure.get("staleEvidenceCount", 0) and not failure.get("freshEvidence"):
        warnings.append("rolling autoresearch still contains pre-packet evidence; it was not treated as fresh unresolved proof")

    actionable = status in {
        "post-packet-eval-accepted",
        "post-packet-eval-improving",
        "post-packet-eval-actionable-unresolved",
    }
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
            "postPacketBattleCount": len(post_packet_battles),
            "postPacketBattleIds": post_packet_battle_ids,
            "postPacketFailureEvidenceBattleIds": post_packet_failure_battle_ids,
            "preservationBattleCountRequired": 2,
            "preservationSatisfied": preservation_satisfied,
            "autoresearchCoversLatestBattle": covers_latest,
            "autoresearchGeneratedAt": autoresearch_generated_at(autoresearch),
            "autoresearchGeneratedAfterLatestBattle": generated_after_latest,
            "latestBattleAfterAcceptance": acceptance_info.get("latestBattleAfterAcceptance"),
        },
        "acceptance": acceptance_info,
        "evidenceIntegrity": integrity or {"exists": False},
        "failureClass": failure,
        "nextIssue": followup_issue,
        "blockers": blockers,
        "warnings": warnings,
        "nextActions": next_actions(status, failure=failure, next_issue=followup_issue),
    }


def next_actions(
    status: str,
    *,
    failure: dict[str, Any] | None = None,
    next_issue: dict[str, Any] | None = None,
) -> list[str]:
    if status == "awaiting-post-packet-battle-proof":
        return ["After HERMES focus/preflight allows it, run exactly one bounded JIGGLYPUFF battle proof and refresh ELO proof."]
    if status == "awaiting-post-packet-autoresearch":
        return ["Run replay_analysis/autoresearch.py --no-discord against the refreshed latest-elo-proof.json, then rerun this evaluator."]
    if status == "post-packet-eval-actionable-unresolved":
        issue = next_issue if isinstance(next_issue, dict) else {}
        if issue:
            title = str(issue.get("title") or issue.get("key") or "current top issue")
            key = str(issue.get("key") or "").strip()
            recommendation = str(
                issue.get("recommendation")
                or "Implement one targeted fix from the latest autoresearch issue."
            )
            label = f"{title} ({key})" if key else title
            return [f"Promote current top issue {label} into one constrained packet: {recommendation}"]
        failure_key = str((failure or {}).get("key") or "current packet failure class")
        return [f"Produce a narrower follow-up packet from fresh unresolved {failure_key} evidence instead of rerunning stale work."]
    if status == "post-packet-eval-accepted":
        return ["Promote the next highest-ranked fresh failure class into one constrained packet; do not rerun the accepted packet."]
    if status == "post-packet-eval-improving":
        return ["Mark the packet evaluated only after the reduced failure class and positive performance signal are preserved in the next bounded proof window."]
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
