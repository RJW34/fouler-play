#!/usr/bin/env python3
"""Write one operator digest for the Fouler devstream lane.

This is deliberately read-only with respect to runtime: it reads the current
mission monitor, autoresearch, Discord queue, and runtime files, then writes a
single concise digest artifact. It does not post to Discord or start battles.
"""

from __future__ import annotations

import argparse
import json
import re
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
TRUTH_DIR = ROOT / "devstream" / "truth"
MISSION_MONITOR = TRUTH_DIR / "mission-monitor.json"
AUTORESEARCH_JSON = ROOT / "replay_analysis" / "autoresearch_latest.json"
AUTORESEARCH_MD = ROOT / "replay_analysis" / "reports" / "autoresearch_latest.md"
EVENT_QUEUE = ROOT / "events_queue.json"
ACTIVE_BATTLES = ROOT / "active_battles.json"
POST_PACKET_EVAL = TRUTH_DIR / "post-packet-eval.json"
OUTPUT_JSON = TRUTH_DIR / "fouler-cycle-digest.json"
OUTPUT_MD = TRUTH_DIR / "fouler-cycle-digest.md"
BATTLE_ID_RE = re.compile(r"battle-gen9ou-[A-Za-z0-9-]+")


def iso_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def read_json(path: Path, default: Any) -> Any:
    try:
        if not path.exists():
            return default
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return default


def file_meta(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {"exists": False, "path": str(path.relative_to(ROOT))}
    stat = path.stat()
    return {
        "exists": True,
        "path": str(path.relative_to(ROOT)),
        "sizeBytes": stat.st_size,
        "mtimeUtc": datetime.fromtimestamp(stat.st_mtime, timezone.utc).isoformat(),
    }


def parse_timestamp(value: Any) -> datetime | None:
    if not isinstance(value, str) or not value.strip():
        return None
    try:
        parsed = datetime.fromisoformat(value.strip().replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def battle_time_index() -> dict[str, datetime]:
    data = read_json(ROOT / "battle_stats.json", {})
    battles = data.get("battles") if isinstance(data, dict) else data if isinstance(data, list) else []
    index: dict[str, datetime] = {}
    if not isinstance(battles, list):
        return index
    for battle in battles:
        if not isinstance(battle, dict):
            continue
        timestamp = parse_timestamp(battle.get("timestamp"))
        if timestamp is None:
            continue
        for key in ("battle_id", "battleId", "replay_id", "battle_tag"):
            battle_id = str(battle.get(key) or "").strip()
            if battle_id:
                index[battle_id] = timestamp
    return index


def issue_battle_ids(issue: dict[str, Any]) -> list[str]:
    proof = issue.get("proof") if isinstance(issue.get("proof"), list) else []
    ids: list[str] = []
    for item in proof:
        ids.extend(BATTLE_ID_RE.findall(str(item)))
    return list(dict.fromkeys(ids))


def current_acceptance_cutoff(post_packet: dict[str, Any]) -> datetime | None:
    if not post_packet_accepted(post_packet):
        return None
    proof_window = post_packet.get("proofWindow") if isinstance(post_packet.get("proofWindow"), dict) else {}
    if proof_window.get("preservationSatisfied") is not True:
        return None
    latest = post_packet.get("latestBattle") if isinstance(post_packet.get("latestBattle"), dict) else {}
    return parse_timestamp(latest.get("at")) or parse_timestamp(post_packet.get("checkedAtUtc"))


def accepted_issue_cutoffs() -> dict[str, datetime]:
    cutoffs: dict[str, datetime] = {}
    for path in TRUTH_DIR.glob("post-packet-eval*.json"):
        report = read_json(path, {})
        if not isinstance(report, dict) or report.get("status") != "post-packet-eval-accepted":
            continue
        proof_window = report.get("proofWindow") if isinstance(report.get("proofWindow"), dict) else {}
        failure_class = report.get("failureClass") if isinstance(report.get("failureClass"), dict) else {}
        if proof_window.get("preservationSatisfied") is not True or failure_class.get("status") != "reduced":
            continue
        key = str(failure_class.get("key") or "").strip()
        if not key:
            continue
        latest = report.get("latestBattle") if isinstance(report.get("latestBattle"), dict) else {}
        cutoff = parse_timestamp(latest.get("at")) or parse_timestamp(report.get("checkedAtUtc"))
        if cutoff is None:
            continue
        if key not in cutoffs or cutoff > cutoffs[key]:
            cutoffs[key] = cutoff
    return cutoffs


def issue_has_evidence_after_cutoff(issue: dict[str, Any], cutoff: datetime, battle_times: dict[str, datetime]) -> bool:
    ids = issue_battle_ids(issue)
    if not ids:
        return True
    for battle_id in ids:
        timestamp = battle_times.get(battle_id)
        if timestamp is None:
            return True
        if timestamp > cutoff:
            return True
    return False


def event_queue_summary(events: list[dict[str, Any]]) -> dict[str, Any]:
    status_counts = Counter(str(event.get("status") or "unknown") for event in events if isinstance(event, dict))
    type_counts = Counter(str(event.get("event_type") or event.get("type") or "unknown") for event in events if isinstance(event, dict))
    pending = [event for event in events if isinstance(event, dict) and event.get("status") == "pending"]
    pending_battle_results = [event for event in pending if event.get("event_type") == "battle_result"]
    pending_other = [event for event in pending if event.get("event_type") != "battle_result"]
    return {
        "eventCount": len(events),
        "pendingCount": len(pending),
        "statusCounts": dict(sorted(status_counts.items())),
        "eventTypeCounts": dict(type_counts.most_common(12)),
        "pendingBattleResultCount": len(pending_battle_results),
        "pendingNonBattleCount": len(pending_other),
        "noisePolicy": "one DEKU observation per completed battle; routine analysis remains local",
    }


def active_improvement_ready(mission: dict[str, Any]) -> bool:
    classification = mission.get("classification") if isinstance(mission.get("classification"), dict) else {}
    proof = classification.get("activeImprovementProof") if isinstance(classification.get("activeImprovementProof"), dict) else {}
    return proof.get("ready") is True or proof.get("status") == "accepted"


def post_packet_accepted(post_packet: dict[str, Any]) -> bool:
    return isinstance(post_packet, dict) and post_packet.get("status") == "post-packet-eval-accepted"


def accepted_packet_finding_key(post_packet: dict[str, Any]) -> str:
    if not post_packet_accepted(post_packet):
        return ""
    proof_window = post_packet.get("proofWindow") if isinstance(post_packet.get("proofWindow"), dict) else {}
    failure_class = post_packet.get("failureClass") if isinstance(post_packet.get("failureClass"), dict) else {}
    if proof_window.get("preservationSatisfied") is not True:
        return ""
    if failure_class.get("status") != "reduced":
        return ""
    return str(failure_class.get("key") or "").strip()


def autoresearch_issue_candidates(autoresearch: dict[str, Any]) -> list[dict[str, Any]]:
    candidates: list[dict[str, Any]] = []
    top = autoresearch.get("top_issue") if isinstance(autoresearch.get("top_issue"), dict) else {}
    if top:
        candidates.append(top)
    for item in autoresearch.get("issues") or []:
        if isinstance(item, dict):
            candidates.append(item)
    unique: list[dict[str, Any]] = []
    seen: set[str] = set()
    for issue in candidates:
        key = str(issue.get("key") or issue.get("title") or "").strip()
        if not key or key in seen:
            continue
        seen.add(key)
        unique.append(issue)
    return unique


def post_packet_next_action(post_packet: dict[str, Any]) -> str | None:
    if not isinstance(post_packet, dict):
        return None
    actions = post_packet.get("nextActions") if isinstance(post_packet.get("nextActions"), list) else []
    first_action = str(actions[0]) if actions else ""
    status = str(post_packet.get("status") or "")
    if status == "post-packet-eval-improving":
        return first_action or (
            "Run one additional bounded preservation proof window, then refresh autoresearch, post-packet eval, and mission monitor."
        )
    if status == "awaiting-post-packet-battle-proof":
        return first_action or "Run one bounded post-packet proof window, then refresh autoresearch and post-packet eval."
    if status == "post-packet-eval-accepted":
        return first_action or "Refresh the ladder stage gate and open only the next bounded proof batch it permits."
    return first_action or None


def start_gate_next_action(mission: dict[str, Any], post_packet: dict[str, Any]) -> str:
    packet_action = post_packet_next_action(post_packet)
    if active_improvement_ready(mission):
        return packet_action or (
            "Run one additional bounded preservation proof window, then refresh autoresearch, post-packet eval, and mission monitor."
        )
    if packet_action:
        return packet_action
    return "Keep supervisor.stop in place until one targeted code packet is implemented, eval-accepted, and followed by one bounded proof window."


def elo_sustain_breakage(mission: dict[str, Any]) -> dict[str, Any] | None:
    classification = mission.get("classification") if isinstance(mission.get("classification"), dict) else {}
    proof = classification.get("eloSustainProof") if isinstance(classification.get("eloSustainProof"), dict) else {}
    if not proof or proof.get("ready") is True:
        return None
    blockers = [str(item) for item in proof.get("blockers") or []]
    ratings = proof.get("ratings") if isinstance(proof.get("ratings"), dict) else {}
    target = proof.get("target") if isinstance(proof.get("target"), dict) else {}
    current_rating = next(
        (
            ratings.get(key)
            for key in ("currentRating", "summaryCurrentRating", "liveProfileRating", "summaryFinalRating", "finalRating")
            if ratings.get(key) is not None
        ),
        None,
    )
    peak_rating = next((ratings.get(key) for key in ("summaryPeakRating", "peakRating") if ratings.get(key) is not None), None)
    floor = target.get("proofRatingFloor") or target.get("canonicalRatingFloor") or 1700
    evidence = blockers[:5]
    if current_rating is not None:
        evidence.append(f"currentRating={current_rating}")
    if peak_rating is not None:
        evidence.append(f"peakRating={peak_rating}")
    stage = classification.get("ladderStage") if isinstance(classification.get("ladderStage"), dict) else {}
    next_milestone = str(stage.get("nextMilestone") or "reach and hold the next ladder floor with bounded proof batches")
    return {
        "rank": 0,
        "area": "elo-sustain",
        "status": "blocked",
        "whatIsBroken": f"Fouler ELO sustain proof is not live-ready for the {floor} target.",
        "evidence": evidence,
        "singleNextAction": (
            f"Open only the next bounded ladder proof batch allowed by the recovery gate ({next_milestone}), "
            "then refresh latest-elo-proof, autoresearch, post-packet eval, mission monitor, and this digest."
        ),
    }


def abandoned_battle_breakage(mission: dict[str, Any]) -> dict[str, Any] | None:
    classification = mission.get("classification") if isinstance(mission.get("classification"), dict) else {}
    cleanup = classification.get("abandonedBattleCleanup") if isinstance(classification.get("abandonedBattleCleanup"), dict) else {}
    if not cleanup or cleanup.get("ready") is True:
        return None
    evidence: list[str] = []
    for battle_id in cleanup.get("missingBattleIds") or []:
        evidence.append(f"missingResultBattle={battle_id}")
    source = cleanup.get("sourceBackupPath")
    if source:
        evidence.append(f"staleActiveBackup={source}")
    if cleanup.get("latestBattleStatsAtUtc"):
        evidence.append(f"latestBattleStatsAtUtc={cleanup['latestBattleStatsAtUtc']}")
    if cleanup.get("sourceBackupMtimeUtc"):
        evidence.append(f"sourceBackupMtimeUtc={cleanup['sourceBackupMtimeUtc']}")
    return {
        "rank": 0,
        "area": "runtime-result-capture",
        "status": "blocked",
        "whatIsBroken": "Fouler abandoned an active ladder battle without writing a completed result row.",
        "evidence": evidence,
        "singleNextAction": str(
            cleanup.get("requiredAction")
            or "Root-cause the runner exit/result-capture path, then prove one bounded battle writes a completed battle_stats row."
        ),
    }


def engine_promotion_breakage(engine_gate: dict[str, Any]) -> dict[str, Any] | None:
    if not isinstance(engine_gate, dict) or not engine_gate:
        return None
    status = str(engine_gate.get("status") or "").strip()
    if status in {"", "promotion-ready"}:
        return None
    blockers = [str(item) for item in engine_gate.get("blockers") or []]
    evidence = [f"enginePromotionGate={status}"]
    packet_id = engine_gate.get("candidatePacketId")
    if packet_id:
        evidence.append(f"candidatePacket={packet_id}")
    evidence.extend(blockers[:6])
    return {
        "rank": 0,
        "area": "engine-promotion",
        "status": "blocked",
        "whatIsBroken": "Fouler has a candidate engine change that is not safe to promote against historical proof.",
        "evidence": evidence,
        "singleNextAction": str(
            engine_gate.get("singleNextAction")
            or "Run scripts/fouler_engine_promotion_gate.py --write, then repair the first blocker it reports."
        ),
    }


def ranked_breakages(
    mission: dict[str, Any],
    autoresearch: dict[str, Any],
    queue: dict[str, Any],
    post_packet: dict[str, Any],
    engine_gate: dict[str, Any] | None = None,
) -> list[dict[str, Any]]:
    items: list[dict[str, Any]] = []
    abandoned_item = abandoned_battle_breakage(mission)
    if abandoned_item is not None:
        abandoned_item["rank"] = len(items) + 1
        items.append(abandoned_item)

    engine_item = engine_promotion_breakage(engine_gate or {})
    if engine_item is not None:
        engine_item["rank"] = len(items) + 1
        items.append(engine_item)

    start_gate = mission.get("startGate") if isinstance(mission.get("startGate"), dict) else {}
    blockers = [str(item) for item in start_gate.get("blockingIssueIds") or []]
    if blockers and not (post_packet_accepted(post_packet) and blockers == ["fouler-supervisor-stop-file-present"]):
        items.append(
            {
                "rank": 1,
                "area": "start-gate",
                "status": "blocked",
                "whatIsBroken": (
                    "Fouler ladder start gate is parked by supervisor.stop while the next proof window is prepared."
                    if active_improvement_ready(mission) and blockers == ["fouler-supervisor-stop-file-present"]
                    else "Fouler ladder start gate is blocked."
                ),
                "evidence": blockers[:8]
                + ([f"postPacketEval={post_packet.get('status')}"] if post_packet.get("status") else []),
                "singleNextAction": start_gate_next_action(mission, post_packet),
            }
        )

    accepted_key = accepted_packet_finding_key(post_packet)
    accepted_cutoffs = accepted_issue_cutoffs()
    acceptance_cutoff = current_acceptance_cutoff(post_packet)
    battle_times = battle_time_index()
    for issue in autoresearch_issue_candidates(autoresearch):
        issue_key = str(issue.get("key") or "").strip()
        if accepted_key and issue_key == accepted_key:
            continue
        accepted_cutoff = accepted_cutoffs.get(issue_key)
        if accepted_cutoff and not issue_has_evidence_after_cutoff(issue, accepted_cutoff, battle_times):
            continue
        if acceptance_cutoff and not issue_has_evidence_after_cutoff(issue, acceptance_cutoff, battle_times):
            continue
        items.append(
            {
                "rank": len(items) + 1,
                "area": "replay-review",
                "status": "action-required",
                "whatIsBroken": str(issue.get("title") or "autoresearch top issue"),
                "evidence": list(issue.get("proof") or [])[:5],
                "singleNextAction": str(issue.get("recommendation") or "Implement one targeted fix from the latest autoresearch issue."),
            }
        )
        break

    elo_item = elo_sustain_breakage(mission)
    if elo_item is not None:
        elo_item["rank"] = len(items) + 1
        items.append(elo_item)

    if queue.get("pendingNonBattleCount", 0) > 0:
        items.append(
            {
                "rank": len(items) + 1,
                "area": "discord",
                "status": "noisy",
                "whatIsBroken": "The DEKU journal contains pending non-battle chatter outside the reporting policy.",
                "evidence": [f"pendingNonBattleCount={queue.get('pendingNonBattleCount')}", f"eventCount={queue.get('eventCount')}"],
                "singleNextAction": "Retain routine analysis locally and advance only battle results or edge-triggered operational alerts.",
            }
        )
    elif queue.get("pendingBattleResultCount", 0) > 0:
        items.append(
            {
                "rank": len(items) + 1,
                "area": "discord",
                "status": "relay-pending",
                "whatIsBroken": "Completed battle observations are waiting for the DEKU relay.",
                "evidence": [f"pendingBattleResultCount={queue.get('pendingBattleResultCount')}", "cycle digest remains local proof"],
                "singleNextAction": "Verify the singleton HERMES relay advances every pending battle observation exactly once.",
            }
        )

    for index, item in enumerate(items, start=1):
        item["rank"] = index
    return items


def build_payload() -> dict[str, Any]:
    mission = read_json(MISSION_MONITOR, {})
    autoresearch = read_json(AUTORESEARCH_JSON, {})
    events = read_json(EVENT_QUEUE, [])
    if not isinstance(events, list):
        events = []
    active = read_json(ACTIVE_BATTLES, {})
    post_packet = read_json(POST_PACKET_EVAL, {})
    engine_gate_path = TRUTH_DIR / "engine-promotion-gate.json"
    engine_gate = read_json(engine_gate_path, {})
    queue = event_queue_summary(events)
    breakages = ranked_breakages(mission, autoresearch, queue, post_packet, engine_gate)
    first = breakages[0] if breakages else {}
    return {
        "schemaVersion": "fouler-cycle-digest/v1",
        "checkedAt": iso_now(),
        "projectId": "fouler-play",
        "status": "action-required" if breakages else "clear",
        "singleNextAction": first.get("singleNextAction") or "Run one bounded proof cycle and refresh this digest.",
        "rankedBreakages": breakages,
        "runtime": {
            "activeBattleCount": active.get("count") if isinstance(active, dict) else None,
            "supervisorStopFilePresent": (ROOT / ".pids" / "supervisor.stop").exists(),
            "drainRequestPresent": (ROOT / ".pids" / "drain.request").exists(),
        },
        "autoresearch": {
            "generatedAt": autoresearch.get("generated_at"),
            "windowSize": autoresearch.get("window_size"),
            "wins": autoresearch.get("wins"),
            "losses": autoresearch.get("losses"),
            "winRate": autoresearch.get("win_rate"),
            "topIssueKey": (autoresearch.get("top_issue") or {}).get("key") if isinstance(autoresearch.get("top_issue"), dict) else None,
        },
        "postPacketEval": {
            "status": post_packet.get("status") if isinstance(post_packet, dict) else None,
            "packetId": (post_packet.get("packet") or {}).get("id") if isinstance(post_packet.get("packet"), dict) else None,
            "failureClassKey": (post_packet.get("failureClass") or {}).get("key") if isinstance(post_packet.get("failureClass"), dict) else None,
            "failureClassStatus": (post_packet.get("failureClass") or {}).get("status") if isinstance(post_packet.get("failureClass"), dict) else None,
            "latestBattleId": (post_packet.get("latestBattle") or {}).get("id") if isinstance(post_packet.get("latestBattle"), dict) else None,
            "preservationSatisfied": (post_packet.get("proofWindow") or {}).get("preservationSatisfied") if isinstance(post_packet.get("proofWindow"), dict) else None,
            "postPacketFailureEvidenceBattleIds": (post_packet.get("proofWindow") or {}).get("postPacketFailureEvidenceBattleIds") if isinstance(post_packet.get("proofWindow"), dict) else None,
        },
        "enginePromotionGate": {
            "status": engine_gate.get("status") if isinstance(engine_gate, dict) else None,
            "candidatePacketId": engine_gate.get("candidatePacketId") if isinstance(engine_gate, dict) else None,
            "promotionAllowed": engine_gate.get("promotionAllowed") if isinstance(engine_gate, dict) else None,
            "blockerCount": len(engine_gate.get("blockers") or []) if isinstance(engine_gate, dict) else None,
        },
        "discord": queue,
        "sources": {
            "missionMonitor": file_meta(MISSION_MONITOR),
            "autoresearchJson": file_meta(AUTORESEARCH_JSON),
            "autoresearchMarkdown": file_meta(AUTORESEARCH_MD),
            "eventQueue": file_meta(EVENT_QUEUE),
            "activeBattles": file_meta(ACTIVE_BATTLES),
            "postPacketEval": file_meta(POST_PACKET_EVAL),
            "enginePromotionGate": file_meta(engine_gate_path),
        },
        "secretValuesPrinted": False,
        "runtimeMutationAllowed": False,
        "networkSendAllowed": False,
    }


def render_markdown(payload: dict[str, Any]) -> str:
    lines = [
        "# Fouler Cycle Digest",
        "",
        f"Checked: {payload['checkedAt']}",
        f"Status: {payload['status']}",
        "",
        "## Single Next Action",
        payload["singleNextAction"],
        "",
        "## Ranked Breakages",
    ]
    for item in payload.get("rankedBreakages") or []:
        lines.extend(
            [
                f"### {item['rank']}. {item['area']} - {item['status']}",
                f"- Broken: {item['whatIsBroken']}",
                f"- Next: {item['singleNextAction']}",
            ]
        )
        for evidence in item.get("evidence") or []:
            lines.append(f"- Evidence: {evidence}")
        lines.append("")
    lines.extend(
        [
            "## Runtime",
            f"- Active battles: {payload['runtime']['activeBattleCount']}",
            f"- Supervisor stop file: {payload['runtime']['supervisorStopFilePresent']}",
            f"- Drain request file: {payload['runtime']['drainRequestPresent']}",
            "",
            "## Discord",
            f"- Pending events: {payload['discord']['pendingCount']}",
            f"- Policy: {payload['discord']['noisePolicy']}",
            "",
        ]
    )
    return "\n".join(lines).strip() + "\n"


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Write one Fouler operator digest.")
    parser.add_argument("--write", action="store_true")
    args = parser.parse_args(argv)

    payload = build_payload()
    if args.write:
        TRUTH_DIR.mkdir(parents=True, exist_ok=True)
        OUTPUT_JSON.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")
        OUTPUT_MD.write_text(render_markdown(payload), encoding="utf-8")
    print(json.dumps(payload, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
