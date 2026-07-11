#!/usr/bin/env python3
"""Build a history-aware promotion gate for Fouler engine changes.

This script is read-only with respect to runtime. It collects the current
engine-change lineage, recent battle window, autoresearch regression shifts,
decision-trace regret samples, post-packet proof, and offline-eval proof into
one artifact that can block candidate promotion before another narrow heuristic
is treated as accepted engine truth.
"""

from __future__ import annotations

import argparse
import json
import re
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable, Mapping

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
TRUTH_DIR = ROOT / "devstream" / "truth"
PACKET_DIR = ROOT / "devstream" / "work_packets" / "generated"
POST_PACKET_EVAL = TRUTH_DIR / "post-packet-eval.json"
AUTORESEARCH_JSON = ROOT / "replay_analysis" / "autoresearch_latest.json"
LATEST_ELO_PROOF = TRUTH_DIR / "latest-elo-proof.json"
BATTLE_STATS = ROOT / "battle_stats.json"
OUTPUT_JSON = TRUTH_DIR / "engine-promotion-gate.json"
OUTPUT_MD = TRUTH_DIR / "engine-promotion-gate.md"
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


def numeric(value: Any) -> float | None:
    if value is None or isinstance(value, bool):
        return None
    try:
        parsed = float(str(value).strip())
    except (TypeError, ValueError):
        return None
    rounded = round(parsed, 3)
    return int(rounded) if rounded.is_integer() else rounded


def string_list(value: Any) -> list[str]:
    if isinstance(value, str):
        value = [value]
    if not isinstance(value, list):
        return []
    return [str(item).strip() for item in value if str(item).strip()]


def git_state(root: Path) -> dict[str, Any]:
    def run_git(args: list[str]) -> str | None:
        try:
            completed = subprocess.run(
                ["git", "-C", str(root), *args],
                check=False,
                capture_output=True,
                text=True,
                timeout=10,
            )
        except Exception:
            return None
        if completed.returncode != 0:
            return None
        return completed.stdout.strip()

    commit = run_git(["rev-parse", "HEAD"])
    status = run_git(["status", "--short"]) or ""
    return {
        "commit": commit,
        "shortCommit": commit[:10] if commit else None,
        "dirty": bool(status),
        "dirtyPathCount": len([line for line in status.splitlines() if line.strip()]),
    }


def display_path(path: Path, root: Path) -> str:
    try:
        return str(path.relative_to(root))
    except ValueError:
        return str(path)


def packet_sort_key(packet: Mapping[str, Any]) -> tuple[datetime, str]:
    timestamp = (
        parse_timestamp(packet.get("implementedAt"))
        or parse_timestamp(packet.get("createdAt"))
        or datetime.fromtimestamp(0, timezone.utc)
    )
    return timestamp, str(packet.get("id") or "")


def load_packets(packet_dir: Path, *, root: Path = ROOT) -> list[dict[str, Any]]:
    packets: list[dict[str, Any]] = []
    for path in sorted(packet_dir.glob("*.json")):
        data = read_json(path, {})
        if not isinstance(data, dict) or data.get("schemaVersion") != "devstream-work-packet/v1":
            continue
        implementation = data.get("implementation") if isinstance(data.get("implementation"), dict) else {}
        integrity = data.get("evidence_integrity") if isinstance(data.get("evidence_integrity"), dict) else {}
        offline = implementation.get("offlineAcceptance") if isinstance(implementation.get("offlineAcceptance"), dict) else {}
        packets.append(
            {
                "id": data.get("id"),
                "path": display_path(path, root),
                "createdAt": data.get("createdAt"),
                "implementedAt": data.get("implementedAt"),
                "status": data.get("status"),
                "findingKey": data.get("finding_key") or data.get("findingKey"),
                "title": data.get("title"),
                "touchedPaths": string_list(implementation.get("touchedPaths")),
                "offlineAcceptance": {
                    "checkedAtUtc": offline.get("checkedAtUtc"),
                    "result": offline.get("result"),
                    "pending": str(offline.get("result") or "").strip().lower() in {"", "pending"},
                },
                "evidenceIntegrity": {
                    "ok": integrity.get("ok") is True,
                    "blockers": string_list(integrity.get("blockers")),
                    "groundTruthCount": len(string_list(integrity.get("groundTruth"))),
                },
                "risk": data.get("risk"),
                "recommendation": data.get("recommendation"),
            }
        )
    return sorted(packets, key=packet_sort_key)


def latest_packet(packets: list[dict[str, Any]]) -> dict[str, Any]:
    return packets[-1] if packets else {}


def battle_rows(battle_stats: Any) -> list[dict[str, Any]]:
    if isinstance(battle_stats, dict):
        rows = battle_stats.get("battles", [])
    else:
        rows = battle_stats
    if not isinstance(rows, list):
        return []
    clean_rows = [row for row in rows if isinstance(row, dict)]
    return sorted(
        clean_rows,
        key=lambda row: parse_timestamp(row.get("timestamp")) or datetime.fromtimestamp(0, timezone.utc),
    )


def battle_id(row: Mapping[str, Any]) -> str:
    for key in ("battle_id", "battleId", "replay_id", "battle_tag"):
        value = str(row.get(key) or "").strip()
        if value:
            return value
    return ""


def rating_from_row(row: Mapping[str, Any]) -> float | None:
    for key in ("rating", "ratingAfter", "rating_after", "finalRating"):
        parsed = numeric(row.get(key))
        if parsed is not None:
            return parsed
    return None


def battle_window_summary(rows: list[dict[str, Any]], *, packet: Mapping[str, Any], window_size: int = 30) -> dict[str, Any]:
    current = rows[-window_size:] if len(rows) >= window_size else rows[:]
    previous = rows[-2 * window_size : -window_size] if len(rows) >= 2 * window_size else []
    packet_time = parse_timestamp(packet.get("createdAt"))
    post_packet = [
        row
        for row in rows
        if packet_time is not None and (parse_timestamp(row.get("timestamp")) or datetime.fromtimestamp(0, timezone.utc)) > packet_time
    ]

    def summarize(sample: list[dict[str, Any]]) -> dict[str, Any]:
        wins = sum(1 for row in sample if str(row.get("result") or "").lower() == "win")
        losses = sum(1 for row in sample if str(row.get("result") or "").lower() == "loss")
        total = wins + losses
        return {
            "battleCount": len(sample),
            "wins": wins,
            "losses": losses,
            "winRate": round(wins / total, 4) if total else None,
            "startBattleId": battle_id(sample[0]) if sample else None,
            "endBattleId": battle_id(sample[-1]) if sample else None,
            "startTimestamp": sample[0].get("timestamp") if sample else None,
            "endTimestamp": sample[-1].get("timestamp") if sample else None,
        }

    return {
        "totalBattles": len(rows),
        "currentWindow": summarize(current),
        "previousWindow": summarize(previous),
        "postLatestPacket": summarize(post_packet),
        "latestRows": [
            {
                "battleId": battle_id(row),
                "timestamp": row.get("timestamp"),
                "result": row.get("result"),
                "rating": rating_from_row(row),
                "team": row.get("team_file") or row.get("team"),
                "replayUrl": row.get("replay_url"),
            }
            for row in rows[-10:]
        ],
    }


def issue_shifts(autoresearch: Mapping[str, Any]) -> list[dict[str, Any]]:
    regression = autoresearch.get("regression") if isinstance(autoresearch.get("regression"), dict) else {}
    compare = regression.get("issue_compare") if isinstance(regression.get("issue_compare"), dict) else {}
    raw_shifts = compare.get("shifts") if isinstance(compare.get("shifts"), list) else []
    shifts: list[dict[str, Any]] = []
    for item in raw_shifts:
        if not isinstance(item, dict):
            continue
        shifts.append(
            {
                "key": item.get("key"),
                "title": item.get("title"),
                "previousCount": numeric(item.get("previous_count")),
                "currentCount": numeric(item.get("current_count")),
                "delta": numeric(item.get("delta")),
                "direction": item.get("direction"),
            }
        )
    return shifts


def autoresearch_summary(autoresearch: Mapping[str, Any]) -> dict[str, Any]:
    top = autoresearch.get("top_issue") if isinstance(autoresearch.get("top_issue"), dict) else {}
    shifts = issue_shifts(autoresearch)
    worse = [
        item
        for item in shifts
        if str(item.get("direction") or "").lower() == "worse" and numeric(item.get("currentCount")) != 0
    ]
    return {
        "generatedAt": autoresearch.get("generated_at") or autoresearch.get("generatedAt"),
        "windowSize": autoresearch.get("window_size"),
        "wins": autoresearch.get("wins"),
        "losses": autoresearch.get("losses"),
        "winRate": autoresearch.get("win_rate"),
        "topIssue": {
            "key": top.get("key"),
            "title": top.get("title"),
            "proof": string_list(top.get("proof"))[:5],
        }
        if top
        else {},
        "issueShifts": shifts,
        "worseningIssueShifts": worse,
    }


def latest_elo_summary(elo_proof: Mapping[str, Any]) -> dict[str, Any]:
    summary = elo_proof.get("summary") if isinstance(elo_proof.get("summary"), dict) else {}
    return {
        "latestBattleId": summary.get("latestBattleId"),
        "latestBattleAt": summary.get("latestBattleAt"),
        "finalRating": numeric(summary.get("finalRating")),
        "currentRating": numeric(summary.get("currentRating")),
        "liveProfileRating": numeric(summary.get("liveProfileRating")),
        "currentRatingSource": summary.get("currentRatingSource"),
        "performanceImprovementVerified": summary.get("performanceImprovementVerified") is True,
        "performanceTrendStatus": summary.get("performanceTrendStatus"),
        "winRate": numeric(summary.get("winRate")),
        "peakRating": numeric(summary.get("peakRating") or summary.get("summaryPeakRating")),
    }


def rating_truth(rows: list[dict[str, Any]], elo_proof: Mapping[str, Any], latest: Mapping[str, Any]) -> dict[str, Any]:
    packet_time = parse_timestamp(latest.get("createdAt"))
    post_rows = [
        row
        for row in rows
        if packet_time is not None and (parse_timestamp(row.get("timestamp")) or datetime.fromtimestamp(0, timezone.utc)) > packet_time
    ]
    recent = post_rows[-10:] if post_rows else rows[-10:]
    missing_recent_rating = [battle_id(row) for row in recent if rating_from_row(row) is None]
    last_rated = next((row for row in reversed(rows) if rating_from_row(row) is not None), None)
    elo = latest_elo_summary(elo_proof)
    live_rating = elo.get("liveProfileRating") or elo.get("currentRating")
    last_rated_value = rating_from_row(last_rated) if last_rated else None
    live_delta = None
    if live_rating is not None and last_rated_value is not None:
        live_delta = round(float(live_rating) - float(last_rated_value), 2)
    blockers: list[str] = []
    if post_rows and len(missing_recent_rating) == len(recent):
        blockers.append("all recent post-packet battle rows lack per-battle ratings")
    elif len(missing_recent_rating) >= 3:
        blockers.append(f"{len(missing_recent_rating)} recent battle rows lack per-battle ratings")
    if live_delta is not None and abs(live_delta) > 75:
        blockers.append(f"live profile rating differs from last rated battle row by {live_delta}")
    return {
        "coherent": not blockers,
        "blockers": blockers,
        "recentMissingRatingBattleIds": missing_recent_rating[:10],
        "lastRatedBattleId": battle_id(last_rated) if last_rated else None,
        "lastRatedRating": last_rated_value,
        "liveProfileOrCurrentRating": live_rating,
        "liveMinusLastRated": live_delta,
        "latestEloSummary": elo,
    }


def trace_battle_id(path: Path, trace: Mapping[str, Any]) -> str:
    for key in ("battle_tag", "battle_id", "battleId"):
        value = str(trace.get(key) or "").strip()
        if value:
            return value
    match = BATTLE_ID_RE.search(path.name)
    return match.group(0) if match else ""


def top_moves(trace: Mapping[str, Any]) -> list[dict[str, Any]]:
    mcts = trace.get("mcts_only") if isinstance(trace.get("mcts_only"), dict) else {}
    moves = mcts.get("top_moves") if isinstance(mcts.get("top_moves"), list) else []
    normalized: list[dict[str, Any]] = []
    for item in moves:
        if not isinstance(item, dict):
            continue
        move = str(item.get("move") or "").strip()
        weight = numeric(item.get("weight"))
        if move:
            normalized.append({"move": move, "weight": weight})
    if normalized:
        return normalized
    raw = trace.get("mcts_policy_raw") if isinstance(trace.get("mcts_policy_raw"), dict) else {}
    for move, weight in raw.items():
        normalized.append({"move": str(move), "weight": numeric(weight)})
    return sorted(normalized, key=lambda item: item.get("weight") or 0, reverse=True)[:8]


def move_key(value: Any) -> str:
    text = str(value or "").strip().lower()
    if text.startswith("/choose "):
        text = text.split("/choose ", 1)[1]
    if text.startswith("move "):
        text = text.split("move ", 1)[1]
    return re.sub(r"[^a-z0-9]+", "", text)


def trace_summary(path: Path, *, root: Path = ROOT) -> dict[str, Any] | None:
    trace = read_json(path, {})
    if not isinstance(trace, dict):
        return None
    moves = top_moves(trace)
    if not moves:
        return None
    choice = trace.get("choice") or trace.get("formatted_choice") or trace.get("selectedMove")
    choice_key = move_key(choice)
    best = moves[0]
    selected = next((item for item in moves if move_key(item.get("move")) == choice_key), None)
    best_weight = numeric(best.get("weight"))
    selected_weight = numeric(selected.get("weight")) if selected else None
    regret_ratio = None
    if best_weight and selected_weight is not None and best_weight > 0:
        regret_ratio = round(max(0.0, (float(best_weight) - float(selected_weight)) / float(best_weight)), 4)
    return {
        "path": display_path(path, root),
        "battleId": trace_battle_id(path, trace),
        "timestamp": trace.get("timestamp"),
        "turn": trace.get("turn"),
        "choice": choice,
        "bestMove": best.get("move"),
        "bestWeight": best_weight,
        "selectedWeight": selected_weight,
        "regretRatio": regret_ratio,
        "selection": (trace.get("mcts_only") or {}).get("selection") if isinstance(trace.get("mcts_only"), dict) else None,
        "eventCount": len((trace.get("mcts_only") or {}).get("events") or []) if isinstance(trace.get("mcts_only"), dict) else 0,
    }


def decision_trace_summary(root: Path, *, max_traces: int = 500) -> dict[str, Any]:
    paths: list[Path] = []
    for directory in (root / "replay_analysis" / "evidence_traces", root / "logs" / "decision_traces"):
        if directory.exists():
            paths.extend(directory.glob("*.json"))
    paths = sorted(set(paths), key=lambda path: path.stat().st_mtime, reverse=True)[:max_traces]
    traces = [item for path in paths if (item := trace_summary(path, root=root)) is not None]
    high_regret = [
        item
        for item in traces
        if item.get("regretRatio") is not None and float(item["regretRatio"]) >= 0.35
    ]
    return {
        "traceFilesScanned": len(paths),
        "traceFilesParsed": len(traces),
        "highRegretCount": len(high_regret),
        "highRegretExamples": high_regret[:10],
        "latestTrace": traces[0] if traces else None,
    }


def offline_eval_result(root: Path) -> dict[str, Any]:
    try:
        from infrastructure.offline_eval_readiness import offline_eval_result_proof

        proof = offline_eval_result_proof(root=root)
        return proof if isinstance(proof, dict) else {}
    except Exception as exc:
        return {
            "ready": False,
            "accepted": False,
            "status": "unavailable",
            "reasons": [f"offline eval proof import/read failed: {exc}"],
        }


def build_history(
    *,
    root: Path = ROOT,
    packet_dir: Path | None = None,
    post_packet: dict[str, Any] | None = None,
    autoresearch: dict[str, Any] | None = None,
    elo_proof: dict[str, Any] | None = None,
    battle_stats: Any | None = None,
    offline_eval: dict[str, Any] | None = None,
    max_traces: int = 500,
) -> dict[str, Any]:
    packet_dir = packet_dir or root / "devstream" / "work_packets" / "generated"
    packets = load_packets(packet_dir, root=root)
    latest = latest_packet(packets)
    post_packet = post_packet if post_packet is not None else read_json(root / "devstream" / "truth" / "post-packet-eval.json", {})
    autoresearch = autoresearch if autoresearch is not None else read_json(root / "replay_analysis" / "autoresearch_latest.json", {})
    elo_proof = elo_proof if elo_proof is not None else read_json(root / "devstream" / "truth" / "latest-elo-proof.json", {})
    battle_stats = battle_stats if battle_stats is not None else read_json(root / "battle_stats.json", {})
    offline_eval = offline_eval if offline_eval is not None else offline_eval_result(root)
    rows = battle_rows(battle_stats)
    return {
        "schemaVersion": "fouler-engine-history/v1",
        "checkedAt": iso_now(),
        "projectId": "fouler-play",
        "git": git_state(root),
        "lineage": {
            "packetCount": len(packets),
            "latestPacketId": latest.get("id"),
            "packets": packets,
        },
        "battleHistory": battle_window_summary(rows, packet=latest),
        "autoresearch": autoresearch_summary(autoresearch if isinstance(autoresearch, dict) else {}),
        "postPacketEval": {
            "status": post_packet.get("status") if isinstance(post_packet, dict) else None,
            "packetId": (post_packet.get("packet") or {}).get("id") if isinstance(post_packet.get("packet"), dict) else None,
            "failureClassStatus": (post_packet.get("failureClass") or {}).get("status") if isinstance(post_packet.get("failureClass"), dict) else None,
            "preservationSatisfied": (post_packet.get("proofWindow") or {}).get("preservationSatisfied") if isinstance(post_packet.get("proofWindow"), dict) else None,
            "postPacketFailureEvidenceBattleIds": (post_packet.get("proofWindow") or {}).get("postPacketFailureEvidenceBattleIds") if isinstance(post_packet.get("proofWindow"), dict) else None,
        },
        "ratingTruth": rating_truth(rows, elo_proof if isinstance(elo_proof, dict) else {}, latest),
        "decisionTraceHistory": decision_trace_summary(root, max_traces=max_traces),
        "offlineEval": offline_eval if isinstance(offline_eval, dict) else {},
    }


def promotion_blockers(history: Mapping[str, Any]) -> list[str]:
    blockers: list[str] = []
    lineage = history.get("lineage") if isinstance(history.get("lineage"), dict) else {}
    packets = lineage.get("packets") if isinstance(lineage.get("packets"), list) else []
    latest = packets[-1] if packets and isinstance(packets[-1], dict) else {}
    post_packet = history.get("postPacketEval") if isinstance(history.get("postPacketEval"), dict) else {}
    autoresearch = history.get("autoresearch") if isinstance(history.get("autoresearch"), dict) else {}
    rating = history.get("ratingTruth") if isinstance(history.get("ratingTruth"), dict) else {}
    offline = history.get("offlineEval") if isinstance(history.get("offlineEval"), dict) else {}
    traces = history.get("decisionTraceHistory") if isinstance(history.get("decisionTraceHistory"), dict) else {}

    if not latest:
        blockers.append("no engine work packet exists")
    else:
        if str(latest.get("status") or "").lower() not in {"implemented", "accepted"}:
            blockers.append(f"latest packet {latest.get('id')} is not implemented")
        evidence = latest.get("evidenceIntegrity") if isinstance(latest.get("evidenceIntegrity"), dict) else {}
        if evidence.get("ok") is not True:
            blockers.append(f"latest packet {latest.get('id')} does not have evidence_integrity.ok=true")
        offline_acceptance = latest.get("offlineAcceptance") if isinstance(latest.get("offlineAcceptance"), dict) else {}
        if offline_acceptance.get("pending") is True:
            blockers.append(f"latest packet {latest.get('id')} offline acceptance is pending")

    if post_packet.get("status") != "post-packet-eval-accepted":
        blockers.append(f"post-packet eval is {post_packet.get('status') or 'missing'}, not accepted")
    if post_packet.get("preservationSatisfied") is not True:
        blockers.append("post-packet preservation proof is not satisfied")

    if offline.get("accepted") is not True and offline.get("ready") is not True:
        blockers.append(f"offline eval result proof is {offline.get('status') or 'not accepted'}")

    for shift in autoresearch.get("worseningIssueShifts") or []:
        if not isinstance(shift, dict):
            continue
        key = shift.get("key") or "unknown"
        blockers.append(f"autoresearch issue shift worsened: {key} delta={shift.get('delta')}")

    if rating.get("coherent") is not True:
        blockers.extend([f"rating truth incoherent: {item}" for item in rating.get("blockers") or []])

    if numeric(traces.get("traceFilesParsed")) in (None, 0):
        blockers.append("decision trace history is empty; candidate has no turn-level regression corpus")
    high_regret = numeric(traces.get("highRegretCount")) or 0
    if high_regret > 0:
        blockers.append(f"decision trace history contains {int(high_regret)} high-regret selected moves")

    return blockers


def build_gate(history: Mapping[str, Any]) -> dict[str, Any]:
    blockers = promotion_blockers(history)
    latest_packet_id = ((history.get("lineage") or {}).get("latestPacketId") if isinstance(history.get("lineage"), dict) else None)
    status = "promotion-ready" if not blockers else "promotion-blocked"
    if status == "promotion-ready":
        action = "Promote the candidate only after writing the accepted gate artifact into the next operator digest."
    elif any("post-packet eval" in item or "preservation" in item for item in blockers):
        action = "Do not stack another engine heuristic; preserve or reject the current packet with the next bounded proof window and this gate refreshed."
    elif any("worsened" in item or "high-regret" in item for item in blockers):
        action = "Open a regression-reduction packet against the worsened historical issue before promoting the latest engine change."
    else:
        action = "Repair the missing proof inputs, then rerun scripts/fouler_engine_promotion_gate.py --write."
    return {
        "schemaVersion": "fouler-engine-promotion-gate/v1",
        "checkedAt": iso_now(),
        "projectId": "fouler-play",
        "status": status,
        "candidatePacketId": latest_packet_id,
        "promotionAllowed": not blockers,
        "blockers": blockers,
        "singleNextAction": action,
        "history": history,
        "secretValuesPrinted": False,
        "runtimeMutationAllowed": False,
        "networkSendAllowed": False,
    }


def render_markdown(gate: Mapping[str, Any]) -> str:
    history = gate.get("history") if isinstance(gate.get("history"), dict) else {}
    autoresearch = history.get("autoresearch") if isinstance(history.get("autoresearch"), dict) else {}
    rating = history.get("ratingTruth") if isinstance(history.get("ratingTruth"), dict) else {}
    traces = history.get("decisionTraceHistory") if isinstance(history.get("decisionTraceHistory"), dict) else {}
    lines = [
        "# Fouler Engine Promotion Gate",
        "",
        f"Checked: {gate.get('checkedAt')}",
        f"Status: {gate.get('status')}",
        f"Candidate packet: {gate.get('candidatePacketId')}",
        "",
        "## Single Next Action",
        str(gate.get("singleNextAction") or ""),
        "",
        "## Blockers",
    ]
    blockers = gate.get("blockers") if isinstance(gate.get("blockers"), list) else []
    if blockers:
        lines.extend(f"- {item}" for item in blockers)
    else:
        lines.append("- none")
    lines.extend(
        [
            "",
            "## Evidence Summary",
            f"- Autoresearch window: {autoresearch.get('wins')}-{autoresearch.get('losses')} winRate={autoresearch.get('winRate')}",
            f"- Worsening issue shifts: {len(autoresearch.get('worseningIssueShifts') or [])}",
            f"- Rating truth coherent: {rating.get('coherent')}",
            f"- Decision traces parsed: {traces.get('traceFilesParsed')}",
            f"- High-regret traces: {traces.get('highRegretCount')}",
        ]
    )
    return "\n".join(lines) + "\n"


def write_outputs(gate: Mapping[str, Any], output_json: Path, output_md: Path) -> None:
    output_json.parent.mkdir(parents=True, exist_ok=True)
    output_json.write_text(json.dumps(gate, indent=2, sort_keys=True), encoding="utf-8")
    output_md.write_text(render_markdown(gate), encoding="utf-8")


def build_from_root(root: Path, *, max_traces: int) -> dict[str, Any]:
    history = build_history(root=root, max_traces=max_traces)
    return build_gate(history)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, default=ROOT)
    parser.add_argument("--write", action="store_true")
    parser.add_argument("--output-json", type=Path, default=OUTPUT_JSON)
    parser.add_argument("--output-md", type=Path, default=OUTPUT_MD)
    parser.add_argument("--max-traces", type=int, default=500)
    args = parser.parse_args(argv)

    root = args.root.resolve()
    gate = build_from_root(root, max_traces=max(0, args.max_traces))
    if args.write:
        output_json = args.output_json if args.output_json.is_absolute() else root / args.output_json
        output_md = args.output_md if args.output_md.is_absolute() else root / args.output_md
        write_outputs(gate, output_json, output_md)
    json.dump(gate, sys.stdout, indent=2, sort_keys=True)
    sys.stdout.write("\n")
    return 0 if gate.get("status") == "promotion-ready" else 2


if __name__ == "__main__":
    raise SystemExit(main())
