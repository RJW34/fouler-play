#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import os
import re
import socket
import subprocess
import sys
import time
import urllib.error
import urllib.request
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import psutil

from devstream_runtime_checks import recent_showdown_credential_failure

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
HTTP_PORT = 8777
OBS_WS_PORT = 4455
IDLE_RUNNER_STALE_SECONDS = int(os.getenv("FP_IDLE_RUNNER_STALE_SECONDS", "180"))
PROOF_STATUS_MAX_AGE_SECONDS = int(os.getenv("FP_PROOF_STATUS_MAX_AGE_SECONDS", "1800"))
TERMINAL_BATTLE_RESULTS = {"win", "loss", "tie", "draw", "forfeit", "timeout", "ended", "error"}

SERVICES = [
    "fouler-play.service",
    "fouler-devstream.service",
    "fouler-pipeline.service",
    "fouler-play-watcher.service",
    "fouler-stability-monitor.service",
]

TRUTH_FILES = [
    {"label": "active battles", "path": "active_battles.json", "optional": True, "staleAfterSeconds": 1800},
    {"label": "stream status", "path": "stream_status.json", "optional": True, "staleAfterSeconds": 21600},
    {"label": "daily stats", "path": "daily_stats.json", "optional": True, "staleAfterSeconds": 21600},
    {"label": "battle stats", "path": "battle_stats.json", "optional": True},
    {
        "label": "autoresearch json",
        "path": "replay_analysis/autoresearch_latest.json",
        "optional": True,
        "staleAfterSeconds": 86400,
        "freshnessTimestampKeys": ["generated_at", "generatedAt"],
    },
    {"label": "autoresearch report", "path": "replay_analysis/reports/autoresearch_latest.md", "optional": True, "staleAfterSeconds": 86400},
    {
        "label": "stability report",
        "path": "stability_report.json",
        "optional": True,
        "staleAfterSeconds": 86400,
        "blocksAnalyticsFresh": False,
    },
]

ENDPOINTS = [
    "/state",
    "/status",
    "/battles",
    "/overlay/hybrid",
    "/dashboard/hybrid",
    "/slot/1",
    "/slot/1/state",
    "/slot/2/state",
    "/slot/3/state",
]

BATTLE_PID_FILES = [
    ".bot.pid",
    ".pids/devstream_battle_session.pid",
]


def iso_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def iso_from_epoch(value: float | None) -> str | None:
    if value is None:
        return None
    return datetime.fromtimestamp(value, tz=timezone.utc).isoformat()


def parse_payload_timestamp(value: Any) -> float | None:
    if not isinstance(value, str) or not value.strip():
        return None
    raw = value.strip()
    if raw.endswith("Z"):
        raw = raw[:-1] + "+00:00"
    try:
        parsed = datetime.fromisoformat(raw)
    except ValueError:
        return None
    return parsed.timestamp()


def run_command(command: list[str], *, timeout: int = 4) -> subprocess.CompletedProcess[str] | None:
    try:
        return subprocess.run(command, cwd=str(ROOT), capture_output=True, text=True, timeout=timeout, check=False)
    except Exception:
        return None


def read_pid_file(path: Path) -> int | None:
    try:
        raw = path.read_text(encoding="utf-8", errors="replace").strip()
    except OSError:
        return None
    if not raw:
        return None
    try:
        if raw.startswith("{"):
            parsed = json.loads(raw)
            return int(parsed.get("pid") or 0) or None
        return int(raw)
    except (ValueError, TypeError, json.JSONDecodeError):
        return None


def runtime_processes() -> list[dict[str, Any]]:
    processes: list[dict[str, Any]] = []
    seen: set[int] = set()
    root = os.path.abspath(ROOT)
    for rel in BATTLE_PID_FILES:
        path = ROOT / rel
        pid = read_pid_file(path)
        item: dict[str, Any] = {
            "pidFile": rel,
            "pid": pid,
            "pidFileExists": path.exists(),
            "alive": False,
            "isBattleRunner": False,
        }
        if not pid:
            processes.append(item)
            continue
        if pid in seen:
            continue
        seen.add(pid)
        try:
            proc = psutil.Process(pid)
            cmdline = proc.cmdline()
            cwd = proc.cwd()
            create_time = proc.create_time()
        except (psutil.NoSuchProcess, psutil.AccessDenied, OSError):
            processes.append(item)
            continue
        command = " ".join(cmdline).lower()
        cwd_matches = os.path.abspath(cwd) == root if cwd else False
        is_runner = cwd_matches and "run.py" in command and ("showdown" in command or "search_ladder" in command)
        item.update({
            "alive": proc.is_running(),
            "isBattleRunner": is_runner,
            "cwdMatchesRepo": cwd_matches,
            "ageSeconds": round(max(0.0, time.time() - create_time), 3),
            "commandSummary": " ".join(cmdline[:4]),
        })
        processes.append(item)
    return processes


def systemctl_state(unit: str) -> dict[str, Any]:
    result = run_command(["systemctl", "--user", "is-active", unit])
    state = (result.stdout.strip() if result and result.stdout.strip() else "unknown")
    if result and result.returncode != 0:
        state = state or "inactive"
    enabled = run_command(["systemctl", "--user", "is-enabled", unit])
    enabled_state = enabled.stdout.strip() if enabled and enabled.stdout.strip() else "unknown"
    return {"activeState": state, "enabledState": enabled_state, "active": state == "active"}


def port_open(port: int, host: str = "127.0.0.1") -> bool:
    try:
        with socket.create_connection((host, port), timeout=0.35):
            return True
    except OSError:
        return False


def fetch_endpoint(path: str, *, timeout: float = 1.25) -> dict[str, Any]:
    url = f"http://127.0.0.1:{HTTP_PORT}{path}"
    try:
        with urllib.request.urlopen(url, timeout=timeout) as response:
            content_type = response.headers.get("content-type", "")
            body = response.read(4096)
            parsed: Any = None
            if "json" in content_type:
                try:
                    parsed = json.loads(body.decode("utf-8", errors="replace"))
                except json.JSONDecodeError:
                    parsed = None
            return {"url": url, "ok": True, "statusCode": response.status, "contentType": content_type, "json": parsed}
    except urllib.error.HTTPError as exc:
        return {"url": url, "ok": False, "statusCode": exc.code, "error": str(exc)}
    except Exception as exc:
        return {"url": url, "ok": False, "statusCode": None, "error": str(exc)}


def classify_obs_task_stderr(stderr_tail: Any) -> dict[str, Any]:
    lines = [str(line) for line in stderr_tail] if isinstance(stderr_tail, list) else []
    text = "\n".join(lines).lower()
    classes: list[str] = []
    if "refusing duplicate start" in text or "existing fouler obs server process" in text:
        classes.append("duplicate_guard_false_positive")
    if "authentication failed" in text and "obs-ws" in text:
        classes.append("obs_ws_auth_failed")
    if not classes and text.strip():
        classes.append("stderr-present")
    if not classes:
        classes.append("clean")
    return {
        "classes": classes,
        "summary": ", ".join(classes),
    }


def obs_surface_task_status() -> dict[str, Any]:
    script = ROOT / "scripts" / "install_obs_server_task.ps1"
    if os.name != "nt":
        return {"available": False, "reason": "not-windows"}
    if not script.exists():
        return {"available": False, "reason": "task-script-missing", "script": str(script)}
    powershell = os.getenv("HERMES_POWERSHELL", "powershell.exe")
    result = run_command(
        [powershell, "-NoProfile", "-ExecutionPolicy", "Bypass", "-File", str(script), "-Status"],
        timeout=int(os.getenv("FP_OBS_TASK_STATUS_TIMEOUT_SECONDS", "8")),
    )
    if result is None:
        return {"available": False, "reason": "status-command-failed", "script": str(script)}
    if result.returncode != 0:
        return {
            "available": False,
            "reason": "status-command-nonzero",
            "returnCode": result.returncode,
            "stderrTail": result.stderr.splitlines()[-10:],
            "script": str(script),
        }
    try:
        payload = json.loads(result.stdout)
    except json.JSONDecodeError:
        return {
            "available": False,
            "reason": "status-json-unparseable",
            "stdoutTail": result.stdout.splitlines()[-10:],
            "stderrTail": result.stderr.splitlines()[-10:],
            "script": str(script),
        }
    if not isinstance(payload, dict):
        return {"available": False, "reason": "status-json-not-object", "script": str(script)}
    payload["available"] = True
    payload["stderrTailClass"] = classify_obs_task_stderr(payload.get("stderrTail"))
    return payload


def truth_file_status(spec: dict[str, Any]) -> dict[str, Any]:
    rel = str(spec["path"])
    path = ROOT / rel
    exists = path.exists()
    mtime = path.stat().st_mtime if exists else None
    parsed: Any = None
    if exists and path.suffix.lower() == ".json":
        try:
            parsed = json.loads(path.read_text(encoding="utf-8", errors="replace"))
        except Exception:
            parsed = None
    freshness_time = mtime
    freshness_source = "mtime" if mtime else None
    if isinstance(parsed, dict):
        for key in spec.get("freshnessTimestampKeys", []):
            timestamp = parse_payload_timestamp(parsed.get(key))
            if timestamp is not None:
                freshness_time = timestamp
                freshness_source = key
                break
    age = time.time() - freshness_time if freshness_time else None
    stale_after = spec.get("staleAfterSeconds")
    summary = summarize_truth(rel, parsed)
    stale = bool(stale_after and age is not None and age > int(stale_after))
    freshness_note = None
    if rel == "active_battles.json" and isinstance(summary, dict) and int(summary.get("battleCount") or 0) == 0:
        stale = False
        freshness_note = "empty active battle truth is valid while the runner is idle or searching"
    return {
        "label": spec.get("label") or rel,
        "path": str(path),
        "relativePath": rel,
        "exists": exists,
        "optional": bool(spec.get("optional")),
        "mtime": iso_from_epoch(mtime),
        "freshnessSource": freshness_source,
        "ageSeconds": round(age, 3) if age is not None else None,
        "staleAfterSeconds": stale_after,
        "stale": stale,
        "blocksAnalyticsFresh": bool(spec.get("blocksAnalyticsFresh", True)),
        "summary": summary,
        **({"freshnessNote": freshness_note} if freshness_note else {}),
    }


def summarize_truth(rel: str, parsed: Any) -> dict[str, Any] | None:
    if not isinstance(parsed, dict):
        return None
    if rel == "active_battles.json":
        battles = parsed.get("battles") if isinstance(parsed.get("battles"), list) else []
        return {"battleCount": len(battles), "updated": parsed.get("updated") or parsed.get("updated_at")}
    if rel == "daily_stats.json":
        return {"wins": parsed.get("wins"), "losses": parsed.get("losses")}
    if rel == "stream_status.json":
        return {
            "status": parsed.get("status"),
            "elo": parsed.get("elo"),
            "updated": parsed.get("updated") or parsed.get("updated_at"),
            "runtimeBlocked": bool(parsed.get("runtime_blocked")),
            "blockerCode": parsed.get("blocker_code"),
            "blockerSummary": parsed.get("blocker_summary"),
            "nextFix": parsed.get("next_fix"),
        }
    return None


def active_battle_entries() -> list[dict[str, Any]]:
    path = ROOT / "active_battles.json"
    if not path.exists():
        return []
    try:
        parsed = json.loads(path.read_text(encoding="utf-8", errors="replace"))
    except Exception:
        return []
    battles = parsed.get("battles") if isinstance(parsed, dict) else []
    return [battle for battle in battles if isinstance(battle, dict)] if isinstance(battles, list) else []


def battle_entry_id(entry: dict[str, Any]) -> str:
    return str(entry.get("battle_id") or entry.get("id") or "").strip()


def terminal_battle_ids_from_stats() -> set[str]:
    path = ROOT / "battle_stats.json"
    parsed = read_json_file(path)
    battles = parsed.get("battles") if isinstance(parsed, dict) else parsed
    if not isinstance(battles, list):
        return set()
    terminal_ids: set[str] = set()
    for item in battles:
        if not isinstance(item, dict):
            continue
        battle_id = battle_entry_id(item)
        result = str(item.get("result") or item.get("status") or item.get("outcome") or "").lower()
        if battle_id and result in TERMINAL_BATTLE_RESULTS:
            terminal_ids.add(battle_id)
    return terminal_ids


def ghost_active_battle_ids() -> list[str]:
    terminal_ids = terminal_battle_ids_from_stats()
    if not terminal_ids:
        return []
    return [battle_id for battle_id in (battle_entry_id(item) for item in active_battle_entries()) if battle_id in terminal_ids]


def live_active_battle_entries() -> list[dict[str, Any]]:
    ghosts = set(ghost_active_battle_ids())
    return [entry for entry in active_battle_entries() if battle_entry_id(entry) not in ghosts]


def showdown_battle_url(battle_id: str) -> str:
    return f"https://play.pokemonshowdown.com/{battle_id}"


def extract_title(html: str) -> str:
    match = re.search(r"<title>(.*?)</title>", html or "", re.IGNORECASE | re.DOTALL)
    if not match:
        return ""
    return re.sub(r"\s+", " ", match.group(1)).strip()


def fetch_showdown_battle_title(battle_id: str, *, timeout: float = 4.0) -> dict[str, Any]:
    url = showdown_battle_url(battle_id)
    try:
        request = urllib.request.Request(url, headers={"User-Agent": "FoulerPlayHealth/1.0"})
        with urllib.request.urlopen(request, timeout=timeout) as response:
            body = response.read(32768).decode("utf-8", errors="replace")
            title = extract_title(body)
            return {
                "url": url,
                "ok": response.status == 200 and " vs. " in title and title.endswith(" - Showdown!"),
                "statusCode": response.status,
                "title": title,
            }
    except Exception as exc:
        return {"url": url, "ok": False, "statusCode": None, "error": str(exc), "title": ""}


def slot_readiness(endpoints: dict[str, Any], *, check_battle_pages: bool = True) -> dict[str, Any]:
    battles = live_active_battle_entries()
    checks: list[dict[str, Any]] = []
    for index, battle in enumerate(battles, start=1):
        battle_id = str(battle.get("id") or "")
        if not battle_id:
            continue
        try:
            slot = int(battle.get("slot") or index)
        except (TypeError, ValueError):
            slot = index
        endpoint = endpoints.get(f"/slot/{slot}/state") or {}
        endpoint_json = endpoint.get("json") if isinstance(endpoint.get("json"), dict) else {}
        local_ok = bool(endpoint.get("ok")) and endpoint_json.get("battle_id") == battle_id
        showdown = fetch_showdown_battle_title(battle_id) if check_battle_pages else {"checked": False, "ok": True}
        showdown["checked"] = bool(check_battle_pages)
        checks.append({
            "slot": slot,
            "battleId": battle_id,
            "opponent": battle.get("opponent"),
            "localStateOk": local_ok,
            "localState": endpoint_json,
            "showdownPage": showdown,
            "ready": local_ok,
        })
    return {"ready": all(item["ready"] for item in checks), "checks": checks}


def stream_status_summary(truth: list[dict[str, Any]]) -> dict[str, Any]:
    for item in truth:
        if item["relativePath"] == "stream_status.json" and isinstance(item.get("summary"), dict):
            return item["summary"]
    return {}


def latest_successful_login(summary: dict[str, Any]) -> bool:
    proof = summary.get("latestSuccessfulProof") if isinstance(summary, dict) else {}
    return bool(isinstance(proof, dict) and proof.get("found") and proof.get("ok"))


def active_battle_count(truth: list[dict[str, Any]]) -> int:
    for item in truth:
        if item["relativePath"] == "active_battles.json" and isinstance(item.get("summary"), dict):
            value = item["summary"].get("battleCount")
            return int(value or 0)
    return 0


def active_battle_truth_status(truth: list[dict[str, Any]]) -> dict[str, Any]:
    for item in truth:
        if item["relativePath"] == "active_battles.json":
            return item
    return {}


def stream_truth_status(truth: list[dict[str, Any]]) -> dict[str, Any]:
    for item in truth:
        if item["relativePath"] == "stream_status.json":
            return item
    return {}


def read_json_file(path: Path) -> Any:
    try:
        return json.loads(path.read_text(encoding="utf-8", errors="replace"))
    except Exception:
        return None


def completed_cycle_proof_status() -> dict[str, Any]:
    path = ROOT / "devstream" / "truth" / "proof-status.json"
    parsed = read_json_file(path)
    generated_at = parse_payload_timestamp(parsed.get("generatedAt")) if isinstance(parsed, dict) else None
    age = time.time() - generated_at if generated_at is not None else None
    active = parsed.get("activeBattleTelemetry") if isinstance(parsed, dict) and isinstance(parsed.get("activeBattleTelemetry"), dict) else {}
    completed = parsed.get("completedCycleProof") if isinstance(parsed, dict) and isinstance(parsed.get("completedCycleProof"), dict) else {}
    blockers = parsed.get("blockers") if isinstance(parsed, dict) and isinstance(parsed.get("blockers"), list) else []
    status = str(parsed.get("status") or "") if isinstance(parsed, dict) else ""
    ready = bool(
        isinstance(parsed, dict)
        and parsed.get("readyForProofHandoff") is True
        and status in {"proof-ready", "local-discord-proof-classified"}
        and parsed.get("secretValuesPrinted") is not True
        and int(active.get("battleCount") or 0) == 0
        and completed.get("isCurrent") is True
        and not blockers
        and age is not None
        and age <= PROOF_STATUS_MAX_AGE_SECONDS
    )
    return {
        "exists": path.exists(),
        "path": str(path),
        "readyForProofHandoff": ready,
        "status": status or None,
        "ageSeconds": round(age, 3) if age is not None else None,
        "staleAfterSeconds": PROOF_STATUS_MAX_AGE_SECONDS,
        "latestBattleId": completed.get("latestBattleId"),
        "completedCycleCurrent": bool(completed.get("isCurrent")),
        "activeBattleCount": int(active.get("battleCount") or 0),
        "secretValuesPrinted": bool(parsed.get("secretValuesPrinted")) if isinstance(parsed, dict) else False,
        "blockers": [str(item) for item in blockers],
    }


def git_status() -> dict[str, Any]:
    commit = run_command(["git", "rev-parse", "--short", "HEAD"], timeout=3)
    dirty = run_command(["git", "status", "--short"], timeout=3)
    return {
        "commit": commit.stdout.strip() if commit and commit.returncode == 0 else None,
        "dirty": bool(dirty and dirty.stdout.strip()),
    }


def discord_queue_health() -> dict[str, Any]:
    queue_file = ROOT / "events_queue.json"
    if not queue_file.exists():
        return {
            "available": False,
            "ready": True,
            "status": "missing",
            "queueFile": str(queue_file),
            "pendingBacklog": 0,
            "pendingBattleResults": 0,
            "pendingEventTypes": {},
            "pendingAgeBuckets": {},
            "pendingPlaceholderFieldCounts": {},
            "pendingBattleResultStructuredFields": {},
            "stalePendingBacklog": 0,
            "stalePendingBattleResults": 0,
            "freshPendingBacklog": 0,
            "freshPendingBattleResults": 0,
            "staleAfterSeconds": None,
            "failedEventTypes": {},
            "expiredEventTypes": {},
            "statusCounts": {},
            "oldestPendingAgeSeconds": None,
            "deliveryFailures": 0,
            "retryingDeliveries": 0,
            "expiredDeliveries": 0,
            "dnsFailures": 0,
            "webhookFailures": 0,
            "failureTypes": {},
            "backlogClassification": {
                "status": "missing",
                "severity": "clear",
                "whyItMatters": "No Discord event queue exists yet.",
                "nextHermesAction": "create the first bounded battle proof event before claiming Discord handoff proof",
                "blocking": False,
            },
            "proofReadiness": {
                "status": "no-queue-yet",
                "readyForProofHandoff": True,
                "pendingBattleResults": 0,
                "machineActionablePendingBattleResults": 0,
                "missingStructuredFieldCounts": {},
                "nextHermesAction": "create the first bounded battle proof event before claiming Discord handoff proof",
                "blockers": [],
            },
            "nextHermesAction": "create the first bounded battle proof event before claiming Discord handoff proof",
            "blockers": [],
        }
    try:
        events = json.loads(queue_file.read_text(encoding="utf-8", errors="replace") or "[]")
        if not isinstance(events, list):
            raise ValueError("event queue root is not a list")
        from infrastructure.event_queue_lib import queue_health_summary

        payload = queue_health_summary(events, available=True)
        payload["queueFile"] = str(queue_file)
        return payload
    except Exception as exc:
        return {
            "available": False,
            "ready": False,
            "status": "unreadable",
            "queueFile": str(queue_file),
            "pendingBacklog": None,
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
            "failedEventTypes": {},
            "expiredEventTypes": {},
            "statusCounts": {},
            "oldestPendingAgeSeconds": None,
            "deliveryFailures": None,
            "retryingDeliveries": None,
            "expiredDeliveries": None,
            "dnsFailures": None,
            "webhookFailures": None,
            "failureTypes": {},
            "backlogClassification": {
                "status": "unreadable",
                "severity": "hard-blocker",
                "whyItMatters": "HERMES cannot classify or drain Discord proof because the queue is unreadable.",
                "nextHermesAction": "repair events_queue.json readability before resuming Discord proof handoff",
                "blocking": True,
            },
            "proofReadiness": {
                "status": "queue-unreadable",
                "readyForProofHandoff": False,
                "pendingBattleResults": None,
                "machineActionablePendingBattleResults": 0,
                "missingStructuredFieldCounts": {},
                "nextHermesAction": "repair events_queue.json readability before resuming Discord proof handoff",
                "blockers": [f"Discord event queue could not be read: {exc}"],
            },
            "nextHermesAction": "repair events_queue.json readability before resuming Discord proof handoff",
            "blockers": [f"Discord event queue could not be read: {exc}"],
        }


def build_reporting_action(
    *,
    battle_count: int,
    discord_queue: dict[str, Any],
    runtime_blocked: bool,
    credential_failure: dict[str, Any],
) -> dict[str, Any]:
    classification = discord_queue.get("backlogClassification") if isinstance(discord_queue.get("backlogClassification"), dict) else {}
    proof_readiness = discord_queue.get("proofReadiness") if isinstance(discord_queue.get("proofReadiness"), dict) else {}
    if runtime_blocked:
        current_state = "fouler-play runtime is blocked before battle proof can advance"
        next_action = "clear the runtime blocker, then run one bounded battle cycle and refresh devstream proof"
    elif credential_failure.get("found"):
        current_state = "Showdown credential failure was detected"
        next_action = "repair Showdown login/session handling before running more battles"
    elif battle_count:
        current_state = f"{battle_count} active Showdown battle(s) are in flight"
        next_action = str(classification.get("nextHermesAction") or "let active battles finish, drain Discord proof, then analyze losses")
    else:
        current_state = "no active Showdown battle proof is present"
        next_action = str(classification.get("nextHermesAction") or "start a bounded HERMES-managed battle cycle and refresh proof")
    return {
        "currentBattleState": current_state,
        "whyItMatters": str(
            classification.get("whyItMatters")
            or "HERMES needs live battle truth plus clean Discord proof before fouler-play can be considered stream-ready."
        ),
        "nextHermesAction": next_action,
        "backlogClassification": classification,
        "proofReadiness": proof_readiness,
    }


def local_discord_proof_classified(discord_queue: dict[str, Any]) -> bool:
    proof_readiness = discord_queue.get("proofReadiness") if isinstance(discord_queue.get("proofReadiness"), dict) else {}
    return (
        bool(proof_readiness.get("readyForLocalProofHandoff"))
        and int(discord_queue.get("deliveryFailures") or 0) == 0
        and int(discord_queue.get("dnsFailures") or 0) == 0
        and int(discord_queue.get("webhookFailures") or 0) == 0
    )


def build_payload(*, check_http: bool = True) -> dict[str, Any]:
    services = {unit: systemctl_state(unit) for unit in SERVICES}
    truth = [truth_file_status(spec) for spec in TRUTH_FILES]
    active_services = [name for name, state in services.items() if state["active"]]
    obs_surface = obs_surface_task_status()
    raw_http_open = port_open(HTTP_PORT)
    task_reports_http = bool(obs_surface.get("available") and obs_surface.get("port8777Listening"))
    http_open = bool(raw_http_open or task_reports_http)
    obs_surface_ready = http_open
    obs_open = port_open(OBS_WS_PORT)
    endpoints = {path: fetch_endpoint(path) for path in ENDPOINTS} if check_http and http_open else {}
    stale_truth = [item for item in truth if item["exists"] and item["stale"]]
    missing_required = [item for item in truth if not item["optional"] and not item["exists"]]
    raw_battle_count = active_battle_count(truth)
    ghost_battles = ghost_active_battle_ids()
    battle_count = max(0, raw_battle_count - len(ghost_battles))
    active_battle_truth = active_battle_truth_status(truth)
    stream_truth = stream_truth_status(truth)
    stream_summary = stream_status_summary(truth)
    slots = slot_readiness(endpoints, check_battle_pages=True) if check_http and http_open else {"ready": True, "checks": []}
    runtime_blocked = bool(stream_summary.get("runtimeBlocked"))
    credential_failure = recent_showdown_credential_failure(ROOT)
    processes = runtime_processes()
    battle_runners = [proc for proc in processes if proc.get("alive") and proc.get("isBattleRunner")]
    duplicate_battle_runner_blocked = len(battle_runners) > 1
    runner_active = bool(active_services or battle_runners)
    runner_proof_missing = False
    runner_has_fresh_idle_truth = (
        bool(active_battle_truth.get("exists"))
        and not bool(active_battle_truth.get("stale"))
        and bool(stream_truth.get("exists"))
        and not bool(stream_truth.get("stale"))
        and str(stream_summary.get("status") or "").lower() in {"active", "battling", "ready", "searching"}
    )
    discord_queue = discord_queue_health()
    local_discord_proof = local_discord_proof_classified(discord_queue)
    completed_proof = completed_cycle_proof_status()
    discord_reporting_ready = bool(discord_queue.get("ready", True) or local_discord_proof)
    proof_blockers = [] if local_discord_proof else [str(item) for item in discord_queue.get("blockers") or []]
    if battle_count:
        proof_blockers.append(
            f"active battle cycle has not drained to analysis yet ({battle_count} active battle"
            f"{'s' if battle_count != 1 else ''})"
        )
    reporting_action = build_reporting_action(
        battle_count=battle_count,
        discord_queue=discord_queue,
        runtime_blocked=runtime_blocked,
        credential_failure=credential_failure,
    )
    if duplicate_battle_runner_blocked:
        reporting_action["currentBattleState"] = (
            f"{len(battle_runners)} live fouler-play battle runners are competing for runtime ownership"
        )
        reporting_action["nextHermesAction"] = (
            "drain/adopt exactly one live battle runner before starting or certifying another cycle"
        )
        reporting_action["whyItMatters"] = (
            "Two battle runners can make active battle truth and control-plane status disagree."
        )

    blockers: list[str] = []
    warnings: list[str] = []
    if ghost_battles:
        warnings.append(
            "active_battles.json contains terminal battle id(s) already present in battle_stats.json; "
            f"not counting ghost battle telemetry as live proof: {', '.join(ghost_battles[:5])}"
        )
    if duplicate_battle_runner_blocked:
        pids = ", ".join(str(proc.get("pid")) for proc in battle_runners[:5])
        blockers.append(
            "duplicate fouler-play battle runners are alive; HERMES must drain/adopt one runtime owner "
            f"before claiming ready (pids: {pids})"
        )
    if obs_surface.get("available"):
        stderr_tail_class = obs_surface.get("stderrTailClass") if isinstance(obs_surface.get("stderrTailClass"), dict) else {}
        raw_stderr_classes = stderr_tail_class.get("classes") if isinstance(stderr_tail_class, dict) else []
        stderr_classes = set(str(item) for item in raw_stderr_classes) if isinstance(raw_stderr_classes, list) else set()
        if "duplicate_guard_false_positive" in stderr_classes and not obs_surface_ready:
            blockers.append("fouler-play OBS HTTP task is blocked by duplicate-guard false positive")
        if "obs_ws_auth_failed" in stderr_classes:
            warnings.append("fouler-play OBS WebSocket source sync has authentication failures in task stderr")
    if not runner_active and battle_count == 0:
        runner_proof_missing = True
        blockers.append("fouler-play battle runner is idle; OBS HTTP alone is not active battle proof")
    elif not runner_active and battle_count > 0 and active_battle_truth.get("stale"):
        runner_proof_missing = True
        blockers.append(
            "fouler-play active battle truth is stale and no battle runner is alive; "
            "clear stale battle state through HERMES devstream_session start"
        )
    elif battle_count == 0 and battle_runners:
        oldest_runner_age = max(float(proc.get("ageSeconds") or 0) for proc in battle_runners)
        if oldest_runner_age > IDLE_RUNNER_STALE_SECONDS and not runner_has_fresh_idle_truth:
            runner_proof_missing = True
            blockers.append(
                "fouler-play battle runner has no active battle proof "
                f"after {int(oldest_runner_age)}s (limit {IDLE_RUNNER_STALE_SECONDS}s)"
            )
    if runtime_blocked:
        summary = stream_summary.get("blockerSummary") or stream_summary.get("status") or "runtime blocked"
        code = stream_summary.get("blockerCode")
        suffix = f" ({code})" if code else ""
        blockers.append(f"{summary}{suffix}")
    if credential_failure.get("found"):
        blockers.append(f"recent Showdown credential failure: {credential_failure.get('code')}")
    if http_open and check_http:
        failed = [path for path, result in endpoints.items() if not result.get("ok")]
        blockers.extend(f"OBS endpoint failed: {path}" for path in failed)
    if missing_required:
        blockers.extend(f"missing truth file: {item['relativePath']}" for item in missing_required)
    if not slots["ready"]:
        for item in slots["checks"]:
            if not item["ready"]:
                title = (item.get("showdownPage") or {}).get("title") or (item.get("showdownPage") or {}).get("error") or "unknown"
                blockers.append(f"slot {item['slot']} is not battle-ready for {item['battleId']}: {title}")
    if stale_truth:
        warnings.extend(f"stale truth file: {item['relativePath']}" for item in stale_truth)

    running = bool(runner_active or obs_surface_ready or battle_count > 0)
    runtime_ready = (
        running
        and (runner_active or battle_count > 0)
        and not runner_proof_missing
        and not duplicate_battle_runner_blocked
        and not runtime_blocked
        and not credential_failure.get("found")
        and not missing_required
        and slots["ready"]
        and (not check_http or not http_open or all(result.get("ok") for result in endpoints.values()))
    )
    analytics_blockers = [item for item in stale_truth if item.get("blocksAnalyticsFresh", True)]
    analytics_fresh = not analytics_blockers
    proof_handoff_ready = (
        (runtime_ready or completed_proof["readyForProofHandoff"])
        and discord_reporting_ready
        and not proof_blockers
        and analytics_fresh
    )
    ready_for_live_focus = (
        runtime_ready
        and not blockers
        and analytics_fresh
        and (
            latest_successful_login(credential_failure)
            or stream_summary.get("status") == "Ready"
            or battle_count > 0
        )
    )
    healthy = runtime_ready
    if healthy:
        status = "ready" if ready_for_live_focus and battle_count == 0 else "running"
    elif runtime_blocked or duplicate_battle_runner_blocked:
        status = "blocked"
    elif running:
        status = "degraded"
    else:
        status = "idle"

    return {
        "schemaVersion": "devstream-health/v1",
        "projectId": "fouler-play",
        "checkedAt": iso_now(),
        "healthy": healthy,
        "running": running,
        "readyForLiveFocus": ready_for_live_focus,
        "readiness": {
            "runtimeReady": runtime_ready,
            "streamReady": slots["ready"] and (obs_surface_ready or not check_http),
            "analyticsFresh": analytics_fresh,
            "discordReportingReady": discord_reporting_ready,
            "proofHandoffReady": proof_handoff_ready,
        },
        "slotReadiness": slots,
        "devstreamReporting": reporting_action,
        "completedCycleProof": completed_proof,
        "status": status,
        "blockers": blockers,
        "proofBlockers": proof_blockers,
        "proofWarnings": (
            [
                "Discord delivery remains pending, but queued battle reports are classified as redacted local proof for HERMES rehearsal handoff."
            ]
            if local_discord_proof and (discord_queue.get("pendingBacklog") or 0)
            else []
        ),
        "warnings": warnings,
        "services": services,
        "ports": {
            "obsHttp": {"port": HTTP_PORT, "open": http_open, "rawSocketOpen": raw_http_open, "taskReportsListening": task_reports_http},
            "obsWebSocket": {"port": OBS_WS_PORT, "open": obs_open},
        },
        "obsSurface": obs_surface,
        "runtimeProcesses": processes,
        "runtimeOwnership": {
            "battleRunnerCount": len(battle_runners),
            "duplicateBattleRunners": duplicate_battle_runner_blocked,
            "requiredHermesAction": (
                "drain/adopt exactly one live battle runner; do not kill processes from this health probe"
                if duplicate_battle_runner_blocked
                else None
            ),
        },
        "discordQueue": discord_queue,
        "endpoints": endpoints,
        "activeBattleCount": battle_count,
        "rawActiveBattleCount": raw_battle_count,
        "ghostActiveBattles": {
            "battleCount": len(ghost_battles),
            "battleIds": ghost_battles[:10],
            "classification": "terminal-battle-stale-active-file" if ghost_battles else "none",
            "proofNote": "Ghost battles are excluded from live proof because terminal battle_stats evidence exists.",
        },
        "truth": truth,
        "credentials": {
            "recentShowdownFailure": credential_failure,
        },
        "git": git_status(),
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="Read-only fouler-play devstream health probe")
    parser.add_argument("--skip-http", action="store_true", help="skip local HTTP endpoint checks")
    parser.add_argument("--write", action="store_true", help="also write devstream/truth/health.json")
    args = parser.parse_args()
    payload = build_payload(check_http=not args.skip_http)
    print(json.dumps(payload, indent=2, sort_keys=True))
    if args.write:
        output = ROOT / "devstream" / "truth" / "health.json"
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
