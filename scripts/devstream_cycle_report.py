#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import devstream_health

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
OUTPUT_JSON = ROOT / "devstream" / "truth" / "cycle-report.json"
OUTPUT_MD = ROOT / "devstream" / "truth" / "cycle-report.md"
OUTPUT_COMPLETION = ROOT / "devstream" / "truth" / "completion.json"
OUTPUT_PROOF_STATUS = ROOT / "devstream" / "truth" / "proof-status.json"
DISCORD_REPORTING = ROOT / "devstream" / "truth" / "discord-reporting.json"
DISCORD_DELIVERY = ROOT / "devstream" / "truth" / "discord-delivery.json"
IDLE_RUNTIME_BLOCKER = "fouler-play battle runner is idle; OBS HTTP alone is not active battle proof"
TERMINAL_BATTLE_RESULTS = {"win", "loss", "tie", "draw", "forfeit", "timeout", "ended", "error"}


def refresh_discord_proof_preview() -> dict[str, Any]:
    """Write a fresh local Discord proof preview without posting or draining."""
    queue_file = ROOT / "events_queue.json"
    try:
        from infrastructure import event_poster

        events = read_json(queue_file)
        if not isinstance(events, list):
            events = []
        pending = [event for event in events if isinstance(event, dict) and event.get("status") == "pending"]
        pending.sort(key=lambda event: _safe_count(event.get("timestamp")))
        if pending:
            event = pending[0]
            payload = event_poster.write_delivery_proof(
                status="dry-run",
                event=event,
                destination_alias=str(event.get("channel") or "unknown"),
                dry_run=True,
                blockers=[],
            )
            return {
                "refreshed": True,
                "status": "dry-run",
                "eventId": event.get("id"),
                "eventType": event.get("event_type"),
                "pendingBacklog": (payload.get("queue") or {}).get("pending"),
                "pendingBattleResults": (payload.get("queue") or {}).get("pendingBattleResults"),
                "deliveryProof": str(DISCORD_DELIVERY.relative_to(ROOT)),
                "reportingProof": str(DISCORD_REPORTING.relative_to(ROOT)),
                "secretValuesPrinted": bool(payload.get("secretValuesPrinted")),
                "note": "local proof preview only; queue events remain pending until approved transport or explicit archival",
            }
        payload = event_poster.write_delivery_proof(
            status="idle",
            event=None,
            destination_alias="unknown",
            dry_run=True,
            blockers=["no pending Discord events"],
            error_code="no_pending_events",
        )
        return {
            "refreshed": True,
            "status": "idle",
            "pendingBacklog": (payload.get("queue") or {}).get("pending"),
            "pendingBattleResults": (payload.get("queue") or {}).get("pendingBattleResults"),
            "deliveryProof": str(DISCORD_DELIVERY.relative_to(ROOT)),
            "reportingProof": str(DISCORD_REPORTING.relative_to(ROOT)),
            "secretValuesPrinted": bool(payload.get("secretValuesPrinted")),
            "note": "no pending Discord events were available for preview",
        }
    except Exception as exc:
        return {
            "refreshed": False,
            "status": "failed",
            "queueFile": str(queue_file),
            "error": f"{type(exc).__name__}: {exc}",
            "note": "cycle report continued without refreshing local Discord proof preview",
        }


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
    try:
        rel_path = str(path.relative_to(ROOT))
    except ValueError:
        rel_path = str(path)
    return {
        "path": rel_path,
        "exists": exists,
        "updatedAt": datetime.fromtimestamp(path.stat().st_mtime, tz=timezone.utc).isoformat() if exists else None,
        "ageSeconds": round(age, 3) if age is not None else None,
    }


def summarize_active_battles(payload: Any) -> dict[str, Any]:
    if not isinstance(payload, dict):
        return {
            "battleCount": 0,
            "battleIds": [],
            "classification": "missing-active-battle-telemetry",
            "isCompletedProof": False,
        }
    battles = payload.get("battles") if isinstance(payload.get("battles"), list) else []
    return {
        "battleCount": len(battles),
        "battleIds": [str(item.get("battle_id") or item.get("id") or "") for item in battles[:5] if isinstance(item, dict)],
        "classification": "active-battle-telemetry" if battles else "empty-active-battle-telemetry",
        "isCompletedProof": False,
        "proofNote": (
            "active battle telemetry shows battles in progress; it is not completed cycle proof"
            if battles
            else "no active battle telemetry is present"
        ),
    }


def _battle_id(payload: Any) -> str:
    if not isinstance(payload, dict):
        return ""
    return str(payload.get("battle_id") or payload.get("id") or "").strip()


def terminal_battle_ids(stats: Any) -> set[str]:
    battles = stats.get("battles") if isinstance(stats, dict) else stats
    if not isinstance(battles, list):
        return set()
    ids: set[str] = set()
    for item in battles:
        if not isinstance(item, dict):
            continue
        battle_id = _battle_id(item)
        result = str(item.get("result") or item.get("status") or item.get("outcome") or "").lower()
        if battle_id and result in TERMINAL_BATTLE_RESULTS:
            ids.add(battle_id)
    return ids


def reconcile_active_battles(summary: dict[str, Any], stats: Any) -> dict[str, Any]:
    battle_ids = [str(item) for item in summary.get("battleIds") or [] if str(item)]
    ghosts = [battle_id for battle_id in battle_ids if battle_id in terminal_battle_ids(stats)]
    if not ghosts:
        return {
            **summary,
            "rawBattleCount": summary.get("battleCount", 0),
            "ghostBattleCount": 0,
            "ghostBattleIds": [],
        }
    ghost_set = set(ghosts)
    live_ids = [battle_id for battle_id in battle_ids if battle_id not in ghost_set]
    return {
        **summary,
        "rawBattleCount": summary.get("battleCount", len(battle_ids)),
        "battleCount": len(live_ids),
        "battleIds": live_ids,
        "ghostBattleCount": len(ghosts),
        "ghostBattleIds": ghosts,
        "classification": "ghost-active-battle-telemetry" if not live_ids else "mixed-active-and-ghost-battle-telemetry",
        "proofNote": "terminal battle_stats evidence exists for ghost active_battles id(s); ghosts are not live proof",
    }


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


def summarize_discord_delivery(payload: Any) -> dict[str, Any]:
    if not isinstance(payload, dict):
        return {
            "status": "missing",
            "pending": None,
            "pendingBattleResults": None,
            "pendingEventTypes": {},
            "pendingAgeBuckets": {},
            "pendingPlaceholderFieldCounts": {},
            "pendingBattleResultStructuredFields": {},
            "stalePendingBacklog": None,
            "stalePendingBattleResults": None,
            "freshPendingBacklog": None,
            "freshPendingBattleResults": None,
            "staleAfterSeconds": None,
            "oldestPendingAgeSeconds": None,
            "deliveryFailures": None,
            "failedEventTypes": {},
            "expiredEventTypes": {},
            "statusCounts": {},
            "dnsFailures": None,
            "webhookFailures": None,
            "healthStatus": "missing",
            "battle_id": None,
            "winner": None,
            "loser": None,
            "turns": None,
            "proof": None,
            "analysis": None,
            "currentBattleState": None,
            "whyItMatters": None,
            "nextHermesAction": None,
            "proofReadiness": None,
            "blockers": ["Discord delivery proof is missing"],
            "secretValuesPrinted": False,
        }
    queue = payload.get("queue") if isinstance(payload.get("queue"), dict) else {}
    return {
        "status": payload.get("status") or "unknown",
        "cycleId": payload.get("cycleId"),
        "destinationAlias": payload.get("destinationAlias"),
        "battleIds": payload.get("battleIds") if isinstance(payload.get("battleIds"), list) else [],
        "pending": queue.get("pending"),
        "pendingBattleResults": queue.get("pendingBattleResults"),
        "pendingEventTypes": queue.get("pendingEventTypes") if isinstance(queue.get("pendingEventTypes"), dict) else {},
        "pendingAgeBuckets": queue.get("pendingAgeBuckets") if isinstance(queue.get("pendingAgeBuckets"), dict) else {},
        "pendingPlaceholderFieldCounts": (
            queue.get("pendingPlaceholderFieldCounts")
            if isinstance(queue.get("pendingPlaceholderFieldCounts"), dict)
            else {}
        ),
        "pendingBattleResultStructuredFields": (
            queue.get("pendingBattleResultStructuredFields")
            if isinstance(queue.get("pendingBattleResultStructuredFields"), dict)
            else {}
        ),
        "stalePendingBacklog": queue.get("stalePendingBacklog"),
        "stalePendingBattleResults": queue.get("stalePendingBattleResults"),
        "freshPendingBacklog": queue.get("freshPendingBacklog"),
        "freshPendingBattleResults": queue.get("freshPendingBattleResults"),
        "staleAfterSeconds": queue.get("staleAfterSeconds"),
        "oldestPendingAgeSeconds": queue.get("oldestPendingAgeSeconds"),
        "deliveryFailures": queue.get("deliveryFailures"),
        "failedEventTypes": queue.get("failedEventTypes") if isinstance(queue.get("failedEventTypes"), dict) else {},
        "expiredEventTypes": queue.get("expiredEventTypes") if isinstance(queue.get("expiredEventTypes"), dict) else {},
        "statusCounts": queue.get("statusCounts") if isinstance(queue.get("statusCounts"), dict) else {},
        "dnsFailures": queue.get("dnsFailures"),
        "webhookFailures": queue.get("webhookFailures"),
        "failureTypes": queue.get("failureTypes") if isinstance(queue.get("failureTypes"), dict) else {},
        "healthStatus": queue.get("healthStatus") or (queue.get("health") or {}).get("status"),
        "errorCode": payload.get("errorCode"),
        "battle_id": payload.get("battle_id"),
        "winner": payload.get("winner"),
        "loser": payload.get("loser"),
        "turns": payload.get("turns"),
        "proof": payload.get("proof") if isinstance(payload.get("proof"), dict) else None,
        "analysis": payload.get("analysis") if isinstance(payload.get("analysis"), dict) else None,
        "currentBattleState": (
            (payload.get("analysis") or {}).get("currentBattleState")
            if isinstance(payload.get("analysis"), dict)
            else None
        ),
        "whyItMatters": (
            (payload.get("analysis") or {}).get("whyItMatters")
            if isinstance(payload.get("analysis"), dict)
            else None
        ),
        "nextHermesAction": (
            (payload.get("analysis") or {}).get("nextHermesAction")
            if isinstance(payload.get("analysis"), dict)
            else None
        ),
        "proofReadiness": (
            (payload.get("analysis") or {}).get("proofReadiness")
            if isinstance(payload.get("analysis"), dict)
            else None
        ),
        "reportSummary": payload.get("reportSummary") if isinstance(payload.get("reportSummary"), dict) else {},
        "blockers": [str(item) for item in payload.get("blockers") or []],
        "reportPaths": payload.get("reportPaths") if isinstance(payload.get("reportPaths"), dict) else {},
        "secretValuesPrinted": bool(payload.get("secretValuesPrinted")),
    }


def summarize_queue_backlog() -> dict[str, Any]:
    try:
        from infrastructure import event_queue_lib

        queue_file = ROOT / "events_queue.json"
        if queue_file.exists():
            events = json.loads(queue_file.read_text(encoding="utf-8", errors="replace") or "[]")
            if not isinstance(events, list):
                raise ValueError("event queue root is not a list")
        else:
            events = []
    except Exception as exc:
        health = {
            "available": False,
            "ready": True,
            "status": "unavailable",
            "pendingBacklog": None,
            "pendingAgeBuckets": {},
            "pendingPlaceholderFieldCounts": {},
            "pendingBattleResultStructuredFields": {},
            "stalePendingBacklog": None,
            "stalePendingBattleResults": None,
            "freshPendingBacklog": None,
            "freshPendingBattleResults": None,
            "staleAfterSeconds": None,
            "oldestPendingAgeSeconds": None,
            "deliveryFailures": None,
            "failedEventTypes": {},
            "expiredEventTypes": {},
            "statusCounts": {},
            "dnsFailures": None,
            "webhookFailures": None,
            "failureTypes": {},
            "blockers": [],
        }
        return {
            "available": False,
            "pending": None,
            "pendingBattleResults": None,
            "pendingEventTypes": {},
            "pendingAgeBuckets": {},
            "pendingPlaceholderFieldCounts": {},
            "pendingBattleResultStructuredFields": {},
            "pendingBacklog": None,
            "failedEventTypes": {},
            "expiredEventTypes": {},
            "statusCounts": {},
            "oldestPendingAgeSeconds": None,
            "deliveryFailures": None,
            "dnsFailures": None,
            "webhookFailures": None,
            "failureTypes": {},
            "backlogClassification": {
                "status": "unavailable",
                "severity": "hard-blocker",
                "whyItMatters": "HERMES cannot inspect Discord backlog because the queue could not be read.",
                "nextHermesAction": "repair queue readability before proof handoff",
                "blocking": True,
            },
            "proofReadiness": {
                "status": "queue-unavailable",
                "readyForProofHandoff": False,
                "pendingBattleResults": None,
                "machineActionablePendingBattleResults": 0,
                "missingStructuredFieldCounts": {},
                "nextHermesAction": "repair queue readability before proof handoff",
                "blockers": [f"event queue could not be read: {exc}"],
            },
            "nextHermesAction": "repair queue readability before proof handoff",
            "healthStatus": "unavailable",
            "ready": True,
            "health": health,
            "blockers": [f"event queue could not be read: {exc}"],
        }

    pending = [event for event in events if isinstance(event, dict) and event.get("status") == "pending"]
    event_types: dict[str, int] = {}
    for event in pending:
        event_type = str(event.get("event_type") or "unknown")
        event_types[event_type] = event_types.get(event_type, 0) + 1
    health = event_queue_lib.queue_health_summary(events)
    return {
        "available": True,
        "total": len(events),
        "pending": len(pending),
        "pendingBattleResults": event_types.get("battle_result", 0),
        "pendingEventTypes": event_types,
        "pendingAgeBuckets": health.get("pendingAgeBuckets", {}),
        "pendingPlaceholderFieldCounts": health.get("pendingPlaceholderFieldCounts", {}),
        "pendingBattleResultStructuredFields": health.get("pendingBattleResultStructuredFields", {}),
        "stalePendingBacklog": health.get("stalePendingBacklog"),
        "stalePendingBattleResults": health.get("stalePendingBattleResults"),
        "freshPendingBacklog": health.get("freshPendingBacklog"),
        "freshPendingBattleResults": health.get("freshPendingBattleResults"),
        "staleAfterSeconds": health.get("staleAfterSeconds"),
        "pendingBacklog": health.get("pendingBacklog"),
        "failedEventTypes": health.get("failedEventTypes", {}),
        "expiredEventTypes": health.get("expiredEventTypes", {}),
        "statusCounts": health.get("statusCounts", {}),
        "oldestPendingAgeSeconds": health.get("oldestPendingAgeSeconds"),
        "oldestPendingEventId": health.get("oldestPendingEventId"),
        "deliveryFailures": health.get("deliveryFailures"),
        "retryingDeliveries": health.get("retryingDeliveries"),
        "expiredDeliveries": health.get("expiredDeliveries"),
        "dnsFailures": health.get("dnsFailures"),
        "webhookFailures": health.get("webhookFailures"),
        "failureTypes": health.get("failureTypes", {}),
        "backlogClassification": health.get("backlogClassification", {}),
        "proofReadiness": health.get("proofReadiness", {}),
        "nextHermesAction": health.get("nextHermesAction"),
        "healthStatus": health.get("status"),
        "ready": health.get("ready"),
        "health": health,
        "blockers": [],
    }


def _parse_time(value: object) -> datetime | None:
    if not value:
        return None
    try:
        text = str(value).replace("Z", "+00:00")
        parsed = datetime.fromisoformat(text)
        return parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)
    except Exception:
        return None


def summarize_unconsumed_battles(stats: Any, autoresearch: Any) -> dict[str, Any]:
    battles = stats.get("battles") if isinstance(stats, dict) and isinstance(stats.get("battles"), list) else []
    autoresearch = autoresearch if isinstance(autoresearch, dict) else {}
    batch = autoresearch.get("batch") if isinstance(autoresearch.get("batch"), dict) else {}
    end_battle_id = batch.get("end_battle_id") or batch.get("endBattleId")
    end_timestamp = _parse_time(batch.get("end_timestamp") or batch.get("endTimestamp"))
    unconsumed: list[dict[str, Any]] = []
    seen_end_id = not end_battle_id
    for battle in battles:
        if not isinstance(battle, dict):
            continue
        battle_id = battle.get("battle_id") or battle.get("id")
        battle_time = _parse_time(battle.get("timestamp") or battle.get("created_at"))
        if end_timestamp and battle_time and battle_time > end_timestamp:
            unconsumed.append(battle)
            continue
        if end_battle_id and battle_id == end_battle_id:
            seen_end_id = True
            continue
        if end_battle_id and seen_end_id:
            unconsumed.append(battle)
    losses = [battle for battle in unconsumed if str(battle.get("result") or "").lower() in {"loss", "lost"}]
    return {
        "latestAnalyzedBattleId": end_battle_id,
        "totalBattles": len(battles),
        "unconsumedCount": len(unconsumed),
        "unconsumedLosses": len(losses),
        "battleIds": [str(battle.get("battle_id") or battle.get("id") or "") for battle in unconsumed[:10]],
        "lossBattleIds": [str(battle.get("battle_id") or battle.get("id") or "") for battle in losses[:10]],
    }


def completed_cycle_evidence_available(
    *,
    active: dict[str, Any],
    unconsumed: dict[str, Any],
    report_exists: bool,
    autoresearch: Any | None = None,
) -> bool:
    return (
        _safe_count(active.get("battleCount")) == 0
        and bool(unconsumed.get("latestAnalyzedBattleId"))
        and _safe_count(unconsumed.get("totalBattles")) > 0
        and _safe_count(unconsumed.get("unconsumedCount")) == 0
        and report_exists
        and not autoresearch_has_unsupported_claims(autoresearch)
    )


def autoresearch_evidence_integrity(payload: Any) -> dict[str, Any]:
    payload = payload if isinstance(payload, dict) else {}
    integrity = payload.get("evidence_integrity") if isinstance(payload.get("evidence_integrity"), dict) else {}
    unsupported = integrity.get("claims_without_evidence") if isinstance(integrity.get("claims_without_evidence"), list) else []
    return {
        "present": bool(integrity),
        "lossCount": integrity.get("loss_count"),
        "lossesWithReplayJson": integrity.get("losses_with_replay_json"),
        "lossesWithDecisionTrace": integrity.get("losses_with_decision_trace"),
        "claimsWithoutEvidenceCount": len(unsupported),
        "claimsWithoutEvidence": unsupported[:10],
        "blocksCompletionProof": bool(unsupported),
    }


def autoresearch_has_unsupported_claims(payload: Any) -> bool:
    return bool(autoresearch_evidence_integrity(payload).get("blocksCompletionProof"))


def summarize_health(payload: Any) -> dict[str, Any]:
    if not isinstance(payload, dict):
        return {
            "healthy": False,
            "status": "unknown",
            "readiness": {},
            "blockers": ["devstream health probe did not return a payload"],
        }
    return {
        "healthy": bool(payload.get("healthy")),
        "status": payload.get("status"),
        "readyForLiveFocus": bool(payload.get("readyForLiveFocus")),
        "activeBattleCount": payload.get("activeBattleCount"),
        "readiness": payload.get("readiness") if isinstance(payload.get("readiness"), dict) else {},
        "devstreamReporting": payload.get("devstreamReporting") if isinstance(payload.get("devstreamReporting"), dict) else {},
        "blockers": [str(item) for item in payload.get("blockers") or []],
    }


def build_completion_payload(cycle: dict[str, Any], autoresearch: Any) -> dict[str, Any]:
    autoresearch = autoresearch if isinstance(autoresearch, dict) else {}
    batch = autoresearch.get("batch") if isinstance(autoresearch.get("batch"), dict) else {}
    regression = autoresearch.get("regression") if isinstance(autoresearch.get("regression"), dict) else {}
    trend = regression.get("status") or "unknown"
    report = cycle.get("autoresearch", {}).get("report", {}) if isinstance(cycle.get("autoresearch"), dict) else {}
    report_exists = bool(report.get("exists"))
    active_battles = int((cycle.get("activeBattles") or {}).get("battleCount") or 0)
    pending_delivery = int((cycle.get("queueBacklog") or {}).get("pending") or 0)
    local_discord_proof = bool(cycle.get("discordBacklogClassifiedForLocalHandoff"))
    unconsumed_count = int((cycle.get("unconsumedBattles") or {}).get("unconsumedCount") or 0)
    blockers = list(cycle.get("blockers") or [])
    warnings = list(cycle.get("warnings") or [])
    integrity = autoresearch_evidence_integrity(autoresearch)
    if active_battles:
        active_msg = "active battles are still present; completion proof is not final"
        if active_msg not in blockers:
            blockers.append(active_msg)
    if integrity["blocksCompletionProof"]:
        blockers.append(
            f"autoresearch has {integrity['claimsWithoutEvidenceCount']} unsupported mechanics/strategy claim(s); completion proof is not final"
        )
    if (
        pending_delivery
        and not local_discord_proof
        and not any(str(item).startswith("pending Discord delivery remains") for item in blockers)
    ):
        blockers.append(f"pending Discord delivery remains: {pending_delivery} event(s)")
    if unconsumed_count and not any(str(item).startswith("unconsumed battles remain") for item in blockers):
        blockers.append(f"unconsumed battles remain after latest autoresearch batch: {unconsumed_count} battle(s)")
    if not report_exists:
        warnings.append("autoresearch markdown report was not available for completion proof")
    return {
        "schemaVersion": "fouler-play-devstream-completion/v1",
        "projectId": "fouler-play",
        "proofKind": "completed-cycle-proof",
        "checkedAtUtc": cycle["generatedAt"],
        "status": "cycle-proof-current" if not blockers else "cycle-proof-blocked",
        "latestBattleId": batch.get("end_battle_id") or batch.get("endBattleId"),
        "latestBattleAt": batch.get("end_timestamp") or batch.get("endTimestamp"),
        "battleCount": batch.get("size") or autoresearch.get("window_size"),
        "latestBattleLearningVerified": bool(autoresearch and report_exists and not blockers),
        "evidenceIntegrity": integrity,
        "performanceImprovementVerified": trend == "improving",
        "performanceTrendStatus": trend,
        "winRate": autoresearch.get("win_rate"),
        "finalRating": (cycle.get("streamStatus") or {}).get("elo"),
        "ratingDelta": regression.get("rating_delta") or regression.get("ratingDelta"),
        "activeImprovementVerified": False,
        "activeBattleTelemetryPresent": active_battles > 0,
        "activeBattleTelemetryIsCompletionProof": False,
        "reportPaths": {
            "cycleReport": str(OUTPUT_JSON.relative_to(ROOT)),
            "cycleMarkdown": str(OUTPUT_MD.relative_to(ROOT)),
            "autoresearchJson": (cycle.get("autoresearch") or {}).get("json", {}).get("path"),
            "autoresearchMarkdown": report.get("path"),
        },
        "blockers": blockers,
        "warnings": warnings,
        "nextActions": [
            "Continue bounded battle batches and keep completion.json fresh after each cycle proof."
        ],
    }


def _safe_count(value: object) -> int:
    try:
        return int(value or 0)
    except (TypeError, ValueError):
        return 0


def _limited_strings(items: object, limit: int = 8) -> list[str]:
    if not isinstance(items, list):
        return []
    return [str(item) for item in items[:limit]]


def build_proof_status_payload(cycle: dict[str, Any], completion: dict[str, Any]) -> dict[str, Any]:
    active = cycle.get("activeBattles") if isinstance(cycle.get("activeBattles"), dict) else {}
    queue = cycle.get("queueBacklog") if isinstance(cycle.get("queueBacklog"), dict) else {}
    delivery = cycle.get("discordDelivery") if isinstance(cycle.get("discordDelivery"), dict) else {}
    active_count = _safe_count(active.get("battleCount"))
    pending = _safe_count(queue.get("pending"))
    completion_ready = completion.get("status") == "cycle-proof-current"
    local_discord_proof = bool(cycle.get("discordBacklogClassifiedForLocalHandoff"))

    if active_count:
        status = "active-telemetry-not-final-proof"
    elif pending and not local_discord_proof:
        status = "discord-backlog-blocked"
    elif pending and local_discord_proof and completion_ready:
        status = "local-discord-proof-classified"
    elif completion_ready:
        status = "proof-ready"
    else:
        status = "blocked"

    return {
        "schemaVersion": "fouler-play-proof-status/v1",
        "projectId": "fouler-play",
        "generatedAt": cycle["generatedAt"],
        "status": status,
        "readyForProofHandoff": status in {"proof-ready", "local-discord-proof-classified"},
        "secretValuesPrinted": False,
        "activeBattleTelemetry": {
            "classification": active.get("classification") or "unknown",
            "battleCount": active_count,
            "rawBattleCount": _safe_count(active.get("rawBattleCount")),
            "ghostBattleCount": _safe_count(active.get("ghostBattleCount")),
            "battleIds": _limited_strings(active.get("battleIds"), 5),
            "ghostBattleIds": _limited_strings(active.get("ghostBattleIds"), 5),
            "isCompletedProof": False,
            "note": active.get("proofNote") or "active battle telemetry is separate from completed proof",
        },
        "completedCycleProof": {
            "classification": completion.get("proofKind") or "completed-cycle-proof",
            "status": completion.get("status"),
            "latestBattleId": completion.get("latestBattleId"),
            "latestBattleAt": completion.get("latestBattleAt"),
            "latestBattleLearningVerified": bool(completion.get("latestBattleLearningVerified")),
            "performanceTrendStatus": completion.get("performanceTrendStatus"),
            "isCurrent": completion_ready,
        },
        "discordBacklog": {
            "healthStatus": queue.get("healthStatus"),
            "pending": pending,
            "pendingBattleResults": queue.get("pendingBattleResults"),
            "backlogClassification": (
                queue.get("backlogClassification")
                if isinstance(queue.get("backlogClassification"), dict)
                else {}
            ),
            "pendingEventTypes": queue.get("pendingEventTypes") if isinstance(queue.get("pendingEventTypes"), dict) else {},
            "pendingAgeBuckets": queue.get("pendingAgeBuckets") if isinstance(queue.get("pendingAgeBuckets"), dict) else {},
            "pendingPlaceholderFieldCounts": (
                queue.get("pendingPlaceholderFieldCounts")
                if isinstance(queue.get("pendingPlaceholderFieldCounts"), dict)
                else {}
            ),
            "pendingBattleResultStructuredFields": (
                queue.get("pendingBattleResultStructuredFields")
                if isinstance(queue.get("pendingBattleResultStructuredFields"), dict)
                else {}
            ),
            "stalePendingBacklog": queue.get("stalePendingBacklog"),
            "stalePendingBattleResults": queue.get("stalePendingBattleResults"),
            "freshPendingBacklog": queue.get("freshPendingBacklog"),
            "freshPendingBattleResults": queue.get("freshPendingBattleResults"),
            "staleAfterSeconds": queue.get("staleAfterSeconds"),
            "oldestPendingAgeSeconds": queue.get("oldestPendingAgeSeconds"),
            "deliveryFailures": queue.get("deliveryFailures"),
            "dnsFailures": queue.get("dnsFailures"),
            "webhookFailures": queue.get("webhookFailures"),
            "proofReadiness": (
                queue.get("proofReadiness")
                if isinstance(queue.get("proofReadiness"), dict)
                else {}
            ),
            "nextHermesAction": queue.get("nextHermesAction"),
        },
        "discordDeliveryProof": {
            "status": delivery.get("status"),
            "cycleId": delivery.get("cycleId"),
            "battle_id": delivery.get("battle_id"),
            "winner": delivery.get("winner"),
            "loser": delivery.get("loser"),
            "turns": delivery.get("turns"),
            "proof": delivery.get("proof") if isinstance(delivery.get("proof"), dict) else None,
            "analysis": delivery.get("analysis") if isinstance(delivery.get("analysis"), dict) else None,
            "currentBattleState": delivery.get("currentBattleState"),
            "whyItMatters": delivery.get("whyItMatters"),
            "nextHermesAction": delivery.get("nextHermesAction"),
            "proofReadiness": delivery.get("proofReadiness"),
            "secretValuesPrinted": bool(delivery.get("secretValuesPrinted")),
        },
        "nextHermesAction": cycle.get("nextHermesAction"),
        "blockers": _limited_strings(cycle.get("blockers"), 12),
        "warnings": _limited_strings(cycle.get("warnings"), 12),
        "artifactPaths": {
            "proofStatus": str(OUTPUT_PROOF_STATUS.relative_to(ROOT)),
            "cycleReport": str(OUTPUT_JSON.relative_to(ROOT)),
            "cycleMarkdown": str(OUTPUT_MD.relative_to(ROOT)),
            "completion": str(OUTPUT_COMPLETION.relative_to(ROOT)),
            "discordReporting": str(DISCORD_REPORTING.relative_to(ROOT)),
            "discordDelivery": str(DISCORD_DELIVERY.relative_to(ROOT)),
            "eventQueue": "events_queue.json",
        },
    }


def build_handoff_action(
    *,
    active: dict[str, Any],
    queue: dict[str, Any],
    delivery: dict[str, Any],
    health: dict[str, Any],
    unconsumed: dict[str, Any],
) -> dict[str, Any]:
    active_count = _safe_count(active.get("battleCount"))
    delivery_action = delivery.get("nextHermesAction")
    queue_action = queue.get("nextHermesAction")
    health_reporting = health.get("devstreamReporting") if isinstance(health.get("devstreamReporting"), dict) else {}
    if active_count:
        state = f"{active_count} active battle(s) in flight; telemetry is useful but not completed proof"
    elif unconsumed.get("unconsumedCount"):
        state = f"{unconsumed.get('unconsumedCount')} completed battle(s) still need autoresearch consumption"
    else:
        state = "no active battle telemetry; rely on latest completed cycle proof"
    backlog = queue.get("backlogClassification") if isinstance(queue.get("backlogClassification"), dict) else {}
    local_discord_ready = local_discord_proof_classified(queue, delivery)
    if backlog.get("blocking") and queue_action and not local_discord_ready:
        next_action = str(queue_action)
    elif unconsumed.get("unconsumedLosses"):
        next_action = "run loss analysis/autoresearch on unconsumed losses before claiming learning progress"
    elif queue_action:
        next_action = (
            "transport Discord backlog when approved; local redacted proof is classified for rehearsal handoff"
            if local_discord_ready
            else str(queue_action)
        )
    elif delivery_action:
        next_action = str(delivery_action)
    else:
        next_action = str(health_reporting.get("nextHermesAction") or "run one bounded battle cycle, drain proof, and refresh reports")
    why = (
        "Discord delivery remains pending, but every queued battle_result has redacted local proof fields for HERMES handoff."
        if local_discord_ready
        else backlog.get("whyItMatters")
        or health_reporting.get("whyItMatters")
        or "HERMES needs clean battle, analysis, and Discord proof before fouler-play can be stream-ready."
    )
    return {
        "currentBattleState": state,
        "whyItMatters": str(why),
        "nextHermesAction": next_action,
        "backlogClassification": backlog,
        "proofReadiness": queue.get("proofReadiness") if isinstance(queue.get("proofReadiness"), dict) else {},
    }


def _delivery_proof_ready(delivery: dict[str, Any]) -> bool:
    proof_readiness = delivery.get("proofReadiness") if isinstance(delivery.get("proofReadiness"), dict) else {}
    return bool(proof_readiness.get("readyForHermes") or proof_readiness.get("status") == "proof-ready")


def local_discord_proof_classified(queue: dict[str, Any], delivery: dict[str, Any]) -> bool:
    proof_readiness = queue.get("proofReadiness") if isinstance(queue.get("proofReadiness"), dict) else {}
    return (
        _safe_count(queue.get("pending")) > 0
        and bool(proof_readiness.get("readyForLocalProofHandoff"))
        and delivery.get("status") == "dry-run"
        and _delivery_proof_ready(delivery)
        and not bool(delivery.get("secretValuesPrinted"))
        and not _safe_count(queue.get("deliveryFailures"))
        and not _safe_count(queue.get("dnsFailures"))
        and not _safe_count(queue.get("webhookFailures"))
    )


def _is_idle_runtime_blocker(value: object) -> bool:
    return IDLE_RUNTIME_BLOCKER in str(value)


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
    discord_reporting = read_json(DISCORD_REPORTING)
    discord_delivery = read_json(DISCORD_DELIVERY)
    queue_backlog = summarize_queue_backlog()
    delivery = summarize_discord_delivery(discord_delivery)
    unconsumed = summarize_unconsumed_battles(stats, read_json(autoresearch_json))
    report_exists = autoresearch_md.exists()
    active_summary = reconcile_active_battles(summarize_active_battles(active), stats)
    completed_cycle_available = completed_cycle_evidence_available(
        active=active_summary,
        unconsumed=unconsumed,
        report_exists=report_exists,
        autoresearch=read_json(autoresearch_json),
    )
    discord_backlog_classified = local_discord_proof_classified(queue_backlog, delivery)
    blockers: list[str] = []
    warnings: list[str] = []
    try:
        health = summarize_health(devstream_health.build_payload(check_http=True))
    except Exception as exc:
        health = summarize_health(None)
        health["blockers"] = [f"devstream health probe failed: {exc}"]
    if active_summary.get("ghostBattleCount"):
        warnings.append(
            "active_battles.json contains terminal battle id(s) already present in battle_stats.json; "
            f"not counting ghost battle telemetry as live proof: {', '.join(active_summary.get('ghostBattleIds', [])[:5])}"
        )
    if active_summary["battleCount"]:
        warnings.append("active battles are still present; cycle report is not a final handoff yet")
    if queue_backlog["blockers"] and not discord_backlog_classified:
        blockers.extend(queue_backlog["blockers"])
    elif queue_backlog["blockers"] and discord_backlog_classified:
        warnings.append("Discord queue backlog is locally classified with redacted dry-run proof; transport remains pending.")
    if queue_backlog.get("pending") and not discord_backlog_classified:
        blockers.append(
            f"pending Discord delivery remains: {queue_backlog['pending']} event(s), "
            f"{queue_backlog.get('pendingBattleResults') or 0} battle_result event(s)"
        )
    elif queue_backlog.get("pending") and discord_backlog_classified:
        warnings.append(
            f"pending Discord delivery remains locally classified: {queue_backlog['pending']} event(s), "
            f"{queue_backlog.get('pendingBattleResults') or 0} battle_result event(s)"
        )
    if queue_backlog.get("deliveryFailures"):
        blockers.append(f"Discord queue has {queue_backlog['deliveryFailures']} failed delivery event(s)")
    if queue_backlog.get("dnsFailures"):
        blockers.append(f"Discord queue has {queue_backlog['dnsFailures']} DNS failure(s)")
    if queue_backlog.get("webhookFailures"):
        blockers.append(f"Discord queue has {queue_backlog['webhookFailures']} webhook failure(s)")
    if delivery["status"] in {"missing", "failed", "rate-limited", "blocked"}:
        blockers.append(f"Discord delivery proof status is {delivery['status']}")
    if delivery.get("dnsFailures"):
        blockers.append(f"Discord delivery proof reports {delivery['dnsFailures']} DNS failure(s)")
    if delivery.get("webhookFailures"):
        blockers.append(f"Discord delivery proof reports {delivery['webhookFailures']} webhook failure(s)")
    if delivery.get("secretValuesPrinted"):
        blockers.append("Discord proof reports secretValuesPrinted=true")
    if unconsumed["unconsumedCount"]:
        blockers.append(f"unconsumed battles remain after latest autoresearch batch: {unconsumed['unconsumedCount']} battle(s)")
    if unconsumed["unconsumedLosses"]:
        blockers.append(f"loss-learning is blocked until {unconsumed['unconsumedLosses']} unconsumed loss battle(s) are analyzed")
    if not health["healthy"]:
        health_blockers = health["blockers"] or ["devstream health is not ready"]
        for blocker in health_blockers:
            if _is_idle_runtime_blocker(blocker) and completed_cycle_available:
                warnings.append(
                    "runtime is idle after completed cycle proof; plan restoration only after readiness gate allows project starts"
                )
            else:
                blockers.append(blocker)
    if not stream_path.exists() and not daily_path.exists() and not stats_path.exists():
        warnings.append("no battle/stat truth files exist yet; run a bounded session before treating this as performance proof")
    if not report_exists:
        warnings.append("autoresearch report is missing; DEKU should run replay analysis before claiming learning progress")
    handoff_action = build_handoff_action(
        active=active_summary,
        queue=queue_backlog,
        delivery=delivery,
        health=health,
        unconsumed=unconsumed,
    )
    return {
        "schemaVersion": "fouler-play-cycle-report/v1",
        "projectId": "fouler-play",
        "generatedAt": iso_now(),
        "readyForHandoff": (
            not blockers
            and active_summary["battleCount"] == 0
            and (not queue_backlog.get("pending") or discord_backlog_classified)
        ),
        "completedCycleEvidenceAvailable": completed_cycle_available,
        "discordBacklogClassifiedForLocalHandoff": discord_backlog_classified,
        "currentBattleState": handoff_action["currentBattleState"],
        "whyItMatters": handoff_action["whyItMatters"],
        "nextHermesAction": handoff_action["nextHermesAction"],
        "backlogClassification": handoff_action["backlogClassification"],
        "proofReadiness": handoff_action["proofReadiness"],
        "blockers": blockers,
        "warnings": warnings,
        "health": health,
        "activeBattles": active_summary,
        "queueBacklog": queue_backlog,
        "discordDelivery": delivery,
        "discordReporting": {
            "json": file_meta(DISCORD_REPORTING),
            "status": discord_reporting.get("status") if isinstance(discord_reporting, dict) else "missing",
            "secretValuesPrinted": bool(discord_reporting.get("secretValuesPrinted")) if isinstance(discord_reporting, dict) else False,
        },
        "unconsumedBattles": unconsumed,
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
            "discordReporting": file_meta(DISCORD_REPORTING),
            "discordDelivery": file_meta(DISCORD_DELIVERY),
            "proofStatus": file_meta(OUTPUT_PROOF_STATUS),
        },
        "operatorNote": "A fouler-play devstream cycle should run a bounded battle batch, stop cleanly, write this report, then let DEKU analyze replay/decision evidence before the next batch.",
    }


def _inline_counts(value: object) -> str:
    if not isinstance(value, dict) or not value:
        return "none"
    return ", ".join(f"{key}={value[key]}" for key in sorted(value))


def write_markdown(payload: dict[str, Any]) -> None:
    lines = [
        "# fouler-play Cycle Report",
        "",
        f"- Generated: `{payload['generatedAt']}`",
        f"- Ready for handoff: `{payload['readyForHandoff']}`",
        f"- Active battles: `{payload['activeBattles']['battleCount']}`",
        f"- Active telemetry class: `{payload['activeBattles'].get('classification')}`",
        f"- Pending Discord delivery: `{payload['queueBacklog'].get('pending')}`",
        f"- Pending battle_result events: `{payload['queueBacklog'].get('pendingBattleResults')}`",
        f"- Pending event classes: `{_inline_counts(payload['queueBacklog'].get('pendingEventTypes'))}`",
        f"- Pending age buckets: `{_inline_counts(payload['queueBacklog'].get('pendingAgeBuckets'))}`",
        f"- Pending placeholder fields: `{_inline_counts(payload['queueBacklog'].get('pendingPlaceholderFieldCounts'))}`",
        f"- Pending battle_result structured fields: `{_inline_counts(payload['queueBacklog'].get('pendingBattleResultStructuredFields'))}`",
        f"- Oldest pending Discord age seconds: `{payload['queueBacklog'].get('oldestPendingAgeSeconds')}`",
        f"- Discord queue health: `{payload['queueBacklog'].get('healthStatus')}`",
        f"- Discord delivery failures: `{payload['queueBacklog'].get('deliveryFailures')}`",
        f"- Discord DNS/webhook failures: `{payload['queueBacklog'].get('dnsFailures')}` / `{payload['queueBacklog'].get('webhookFailures')}`",
        f"- Discord delivery proof: `{payload['discordDelivery'].get('status')}`",
        f"- Current battle state: `{payload.get('currentBattleState')}`",
        f"- Why it matters: `{payload.get('whyItMatters')}`",
        f"- Next HERMES action: `{payload.get('nextHermesAction')}`",
        f"- Proof readiness: `{(payload.get('proofReadiness') or {}).get('status')}`",
        f"- Unconsumed battles: `{payload['unconsumedBattles'].get('unconsumedCount')}`",
        f"- Unconsumed losses: `{payload['unconsumedBattles'].get('unconsumedLosses')}`",
        f"- Stream ELO: `{payload['streamStatus'].get('elo') or 'unknown'}`",
        f"- Daily record: `{payload['dailyStats'].get('wins') or 0}-{payload['dailyStats'].get('losses') or 0}`",
        "",
        "## Blockers",
        "",
    ]
    blockers = payload.get("blockers") or []
    lines.extend(f"- {item}" for item in blockers) if blockers else lines.append("- none")
    lines.extend([
        "",
        "## Warnings",
        "",
    ])
    warnings = payload.get("warnings") or []
    lines.extend(f"- {item}" for item in warnings) if warnings else lines.append("- none")
    lines.extend(["", payload["operatorNote"], ""])
    OUTPUT_MD.write_text("\n".join(lines), encoding="utf-8")


def write_completion(payload: dict[str, Any], autoresearch: Any) -> dict[str, Any]:
    completion = build_completion_payload(payload, autoresearch)
    OUTPUT_COMPLETION.write_text(json.dumps(completion, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return completion


def write_proof_status(payload: dict[str, Any], completion: dict[str, Any]) -> dict[str, Any]:
    proof_status = build_proof_status_payload(payload, completion)
    OUTPUT_PROOF_STATUS.write_text(json.dumps(proof_status, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return proof_status


def main() -> int:
    parser = argparse.ArgumentParser(description="Write fouler-play bounded devstream cycle handoff report.")
    parser.add_argument("--write", action="store_true")
    args = parser.parse_args()
    discord_proof_refresh = refresh_discord_proof_preview() if args.write else None
    payload = build_payload()
    if discord_proof_refresh:
        payload["discordProofRefresh"] = discord_proof_refresh
    if args.write:
        OUTPUT_JSON.parent.mkdir(parents=True, exist_ok=True)
        completion = write_completion(payload, read_json(ROOT / "replay_analysis" / "autoresearch_latest.json"))
        write_proof_status(payload, completion)
        payload.setdefault("truthFiles", {})
        payload["truthFiles"]["completion"] = file_meta(OUTPUT_COMPLETION)
        payload["truthFiles"]["proofStatus"] = file_meta(OUTPUT_PROOF_STATUS)
        payload["written"] = [str(OUTPUT_JSON), str(OUTPUT_MD), str(OUTPUT_COMPLETION), str(OUTPUT_PROOF_STATUS)]
        OUTPUT_JSON.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")
        write_markdown(payload)
    print(json.dumps(payload, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
