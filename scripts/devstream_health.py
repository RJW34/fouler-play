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

try:
    import psutil  # type: ignore
except ModuleNotFoundError:  # pragma: no cover - exercised by doctor subprocess checks.
    psutil = None  # type: ignore[assignment]

from devstream_runtime_checks import recent_showdown_credential_failure
from devstream_session import DEFAULT_MAX_CONCURRENT as DEFAULT_DEVSTREAM_BATTLE_SURFACES

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
HTTP_PORT = 8777
OBS_WS_PORT = 4455
IDLE_RUNNER_STALE_SECONDS = int(os.getenv("FP_IDLE_RUNNER_STALE_SECONDS", "180"))
PROOF_STATUS_MAX_AGE_SECONDS = int(os.getenv("FP_PROOF_STATUS_MAX_AGE_SECONDS", "1800"))
TERMINAL_BATTLE_RESULTS = {"win", "loss", "tie", "draw", "forfeit", "timeout", "ended", "error"}
ACTIVE_STREAM_STATUSES = {"active", "battling", "running", "searching"}
FINITE_RUNTIME_LEASE_PRECONDITIONS = [
    "a current proof-window runtime lease validates for the requested HERMES action",
    "the lease names projectId=fouler-play, runtime machine, Showdown account, replay behavior, and expiry",
    "requested --run-count and --max-concurrent-battles are positive finite bounds within the lease",
    "archive/adopt/clear actions run only through devstream_session.py start/stop --execute after lease validation",
]


def positive_int_env(name: str, default: int) -> int:
    try:
        value = int(os.getenv(name, str(default)))
    except (TypeError, ValueError):
        return default
    return value if value > 0 else default


EXPECTED_DEVSTREAM_BATTLE_SURFACES = positive_int_env(
    "FP_EXPECTED_DEVSTREAM_BATTLE_SURFACES",
    DEFAULT_DEVSTREAM_BATTLE_SURFACES,
)

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

BASE_ENDPOINTS = [
    "/state",
    "/status",
    "/battles",
    "/overlay/hybrid",
    "/dashboard/hybrid",
]


def battle_slot_endpoints(expected: int = EXPECTED_DEVSTREAM_BATTLE_SURFACES) -> list[str]:
    return [
        endpoint
        for slot in range(1, expected + 1)
        for endpoint in (f"/slot/{slot}", f"/slot/{slot}/state")
    ]


ENDPOINTS = [*BASE_ENDPOINTS, *battle_slot_endpoints()]

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


def read_pid_payload(path: Path) -> dict[str, Any] | str | None:
    try:
        raw = path.read_text(encoding="utf-8", errors="replace").strip()
    except OSError:
        return None
    if not raw:
        return None
    try:
        if raw.startswith("{"):
            parsed = json.loads(raw)
            return parsed if isinstance(parsed, dict) else None
        return int(raw)
    except (ValueError, TypeError, json.JSONDecodeError):
        return None


def read_pid_file(path: Path) -> int | None:
    payload = read_pid_payload(path)
    if isinstance(payload, dict):
        try:
            return int(payload.get("pid") or 0) or None
        except (TypeError, ValueError):
            return None
    try:
        return int(str(payload or "").strip())
    except (TypeError, ValueError):
        return None


def runtime_processes() -> list[dict[str, Any]]:
    processes: list[dict[str, Any]] = []
    seen: set[int] = set()
    root = os.path.abspath(ROOT)
    if psutil is None:
        for rel in BATTLE_PID_FILES:
            path = ROOT / rel
            pid = read_pid_file(path)
            item: dict[str, Any] = {
                "pidFile": rel,
                "pid": pid,
                "pidFileExists": path.exists(),
                "processRunning": False,
                "alive": False,
                "isBattleRunner": False,
                "processInspectionAvailable": False,
            }
            if pid:
                item["stalePidReason"] = "psutil is not installed; PID ownership cannot be verified"
            processes.append(item)
        return processes
    for rel in BATTLE_PID_FILES:
        path = ROOT / rel
        pid_payload = read_pid_payload(path)
        pid = read_pid_file(path)
        item: dict[str, Any] = {
            "pidFile": rel,
            "pid": pid,
            "pidFileExists": path.exists(),
            "processRunning": False,
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
            status = proc.status() if hasattr(proc, "status") else ""
        except (psutil.NoSuchProcess, psutil.AccessDenied, OSError):
            processes.append(item)
            continue
        command = " ".join(cmdline).lower()
        cwd_matches = os.path.abspath(cwd) == root if cwd else False
        process_running = bool(proc.is_running()) and status != getattr(psutil, "STATUS_ZOMBIE", "zombie")
        is_runner = process_running and cwd_matches and "run.py" in command and ("showdown" in command or "search_ladder" in command)
        if isinstance(pid_payload, dict):
            started_at = parse_payload_timestamp(pid_payload.get("startedAt") or pid_payload.get("started_at"))
            if started_at is not None and create_time < started_at - 2:
                is_runner = False
        item.update({
            "processRunning": process_running,
            "alive": is_runner,
            "isBattleRunner": is_runner,
            "cwdMatchesRepo": cwd_matches,
            "ageSeconds": round(max(0.0, time.time() - create_time), 3),
            "commandSummary": " ".join(cmdline[:4]),
        })
        if process_running and not is_runner:
            item["stalePidReason"] = "pid belongs to unexpected command, cwd, or older process"
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
    disposition = truth_artifact_disposition(rel, parsed, summary, stale)
    blocks_analytics_fresh = bool(spec.get("blocksAnalyticsFresh", True))
    if disposition.get("state") == "archived":
        blocks_analytics_fresh = False
    freshness_note = None
    if rel == "active_battles.json" and isinstance(summary, dict) and int(summary.get("battleCount") or 0) == 0:
        freshness_note = "empty active battle truth is valid only while fresh runtime ownership exists"
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
        "blocksAnalyticsFresh": blocks_analytics_fresh,
        "summary": summary,
        "disposition": disposition,
        **({"freshnessNote": freshness_note} if freshness_note else {}),
    }


def summarize_truth(rel: str, parsed: Any) -> dict[str, Any] | None:
    if not isinstance(parsed, dict):
        return None
    if rel == "active_battles.json":
        battles = parsed.get("battles") if isinstance(parsed.get("battles"), list) else []
        max_slots = parsed.get("max_slots") or parsed.get("maxSlots")
        try:
            max_slots = int(max_slots)
        except (TypeError, ValueError):
            max_slots = max(len(battles), EXPECTED_DEVSTREAM_BATTLE_SURFACES)
        return {
            "battleCount": len(battles),
            "maxSlots": max_slots,
            "updated": parsed.get("updated") or parsed.get("updated_at"),
            "clearedBy": parsed.get("clearedBy"),
            "clearReason": parsed.get("clearReason"),
            "runtimeBlocked": bool(parsed.get("runtime_blocked")),
            "blockerCode": parsed.get("blocker_code"),
            "blockerSummary": parsed.get("blocker_summary"),
            "previousBattleCount": parsed.get("previousBattleCount"),
            "previousTruthWasEmpty": parsed.get("previousTruthWasEmpty"),
        }
    if rel == "daily_stats.json":
        return {"wins": parsed.get("wins"), "losses": parsed.get("losses")}
    if rel == "stream_status.json":
        return {
            "status": parsed.get("status"),
            "elo": parsed.get("elo"),
            "updated": parsed.get("updated") or parsed.get("updated_at"),
            "streaming": parsed.get("streaming"),
            "streamPid": parsed.get("stream_pid"),
            "runtimeBlocked": bool(parsed.get("runtime_blocked")),
            "blockerCode": parsed.get("blocker_code"),
            "blockerSummary": parsed.get("blocker_summary"),
            "nextFix": parsed.get("next_fix"),
        }
    return None


def finite_runtime_lease_preconditions() -> list[str]:
    return list(FINITE_RUNTIME_LEASE_PRECONDITIONS)


def active_truth_is_archived(summary: dict[str, Any] | None) -> bool:
    if not isinstance(summary, dict):
        return False
    if int(summary.get("battleCount") or 0) != 0:
        return False
    return bool(summary.get("clearedBy") or summary.get("clearReason") or summary.get("runtimeBlocked"))


def truth_artifact_disposition(
    rel: str,
    parsed: Any,
    summary: dict[str, Any] | None,
    stale: bool,
) -> dict[str, Any]:
    if rel == "active_battles.json":
        battle_count = int((summary or {}).get("battleCount") or 0)
        if active_truth_is_archived(summary):
            return {
                "state": "archived",
                "classification": "archived-active-battle-truth",
                "proofUse": "not-live-runtime-proof",
                "reason": (summary or {}).get("clearReason") or (summary or {}).get("blockerSummary"),
                "clearedBy": (summary or {}).get("clearedBy"),
                "finiteLeasePreconditions": finite_runtime_lease_preconditions(),
            }
        if battle_count > 0:
            return {
                "state": "candidate-active",
                "classification": "active-battle-telemetry",
                "proofUse": "requires-live-runner-adoption",
                "finiteLeasePreconditions": finite_runtime_lease_preconditions(),
            }
        return {
            "state": "idle-stale" if stale else "idle",
            "classification": "empty-active-battle-truth",
            "proofUse": "not-live-runtime-proof",
            "finiteLeasePreconditions": finite_runtime_lease_preconditions(),
        }
    if rel == "stream_status.json":
        status = str((summary or {}).get("status") or "").strip()
        normalized = status.lower()
        if (summary or {}).get("runtimeBlocked"):
            return {
                "state": "blocked",
                "classification": "runtime-blocked-stream-status",
                "proofUse": "not-live-runtime-proof",
                "reason": (summary or {}).get("blockerSummary") or status or "runtime blocked",
                "finiteLeasePreconditions": finite_runtime_lease_preconditions(),
            }
        if normalized in ACTIVE_STREAM_STATUSES or (summary or {}).get("streaming"):
            return {
                "state": "candidate-active-stale" if stale else "candidate-active",
                "classification": "active-stream-status",
                "proofUse": "requires-live-runner-adoption",
                "finiteLeasePreconditions": finite_runtime_lease_preconditions(),
            }
        return {
            "state": "idle-stale" if stale else "idle",
            "classification": "stream-status",
            "proofUse": "not-live-runtime-proof",
            "finiteLeasePreconditions": finite_runtime_lease_preconditions(),
        }
    return {
        "state": "stale" if stale else "fresh",
        "classification": "generated-truth-file",
    }


def runtime_truth_disposition(
    *,
    active_truth: dict[str, Any],
    stream_truth: dict[str, Any],
    runner_active: bool,
    battle_count: int,
) -> dict[str, Any]:
    active = dict(active_truth.get("disposition") if isinstance(active_truth.get("disposition"), dict) else {})
    stream = dict(stream_truth.get("disposition") if isinstance(stream_truth.get("disposition"), dict) else {})
    stream_summary = stream_truth.get("summary") if isinstance(stream_truth.get("summary"), dict) else {}
    blockers: list[str] = []

    if runner_active:
        if active.get("state") in {"candidate-active", "candidate-active-stale"}:
            active["state"] = "adopted"
            active["classification"] = "adopted-active-battle-truth"
        if stream.get("state") in {"candidate-active", "candidate-active-stale"}:
            stream["state"] = "adopted"
            stream["classification"] = "adopted-stream-status"
    else:
        active_is_archived = active.get("state") == "archived"
        if battle_count > 0 or (active_truth.get("stale") and not active_is_archived):
            active["state"] = "blocked"
            active["classification"] = "blocked-stale-active-battle-truth"
            active["proofUse"] = "not-live-runtime-proof"
            blockers.append(
                "active_battles.json is stale/unowned runtime truth; archive or adopt only through a finite proof-window runtime lease"
            )
        stream_active = str(stream_summary.get("status") or "").strip().lower() in ACTIVE_STREAM_STATUSES
        stream_claims_runtime = stream_active or bool(stream_summary.get("streaming")) or bool(stream_summary.get("streamPid"))
        if stream_claims_runtime and stream_truth.get("stale"):
            stream["state"] = "blocked"
            stream["classification"] = "blocked-stale-stream-status"
            stream["proofUse"] = "not-live-runtime-proof"
            blockers.append(
                "stream_status.json is stale/unowned active runtime truth; archive or adopt only through a finite proof-window runtime lease"
            )

    artifact_states = [active.get("state"), stream.get("state")]
    if any(state == "blocked" for state in artifact_states):
        state = "blocked"
    elif any(state == "adopted" for state in artifact_states):
        state = "adopted"
    elif any(state == "archived" for state in artifact_states):
        state = "archived"
    else:
        state = "idle"
    return {
        "state": state,
        "blockers": blockers,
        "finiteLeasePreconditions": finite_runtime_lease_preconditions(),
        "artifacts": {
            "activeBattles": active,
            "streamStatus": stream,
        },
    }


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


def active_battle_max_slots(truth: list[dict[str, Any]]) -> int:
    for item in truth:
        if item["relativePath"] == "active_battles.json" and isinstance(item.get("summary"), dict):
            value = item["summary"].get("maxSlots")
            try:
                return int(value)
            except (TypeError, ValueError):
                return EXPECTED_DEVSTREAM_BATTLE_SURFACES
    return EXPECTED_DEVSTREAM_BATTLE_SURFACES


def active_battle_truth_status(truth: list[dict[str, Any]]) -> dict[str, Any]:
    for item in truth:
        if item["relativePath"] == "active_battles.json":
            return item
    return {}


def battle_surface_readiness(
    truth: list[dict[str, Any]],
    endpoints: dict[str, Any],
    *,
    check_http: bool,
    http_open: bool,
) -> dict[str, Any]:
    expected = EXPECTED_DEVSTREAM_BATTLE_SURFACES
    declared_max_slots = active_battle_max_slots(truth)
    slot_checks: list[dict[str, Any]] = []
    for slot in range(1, expected + 1):
        page = endpoints.get(f"/slot/{slot}") or {}
        state = endpoints.get(f"/slot/{slot}/state") or {}
        page_ok = True if not check_http or not http_open else bool(page.get("ok"))
        state_ok = True if not check_http or not http_open else bool(state.get("ok"))
        slot_checks.append({
            "slot": slot,
            "pageOk": page_ok,
            "stateOk": state_ok,
            "ready": page_ok and state_ok,
        })
    return {
        "expected": expected,
        "declaredMaxSlots": declared_max_slots,
        "maxSlotsOk": declared_max_slots >= expected,
        "checks": slot_checks,
        "ready": declared_max_slots >= expected and all(item["ready"] for item in slot_checks),
    }


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
    trend = str(completed.get("performanceTrendStatus") or "").strip().lower()
    improvement_signal_ok = (
        completed.get("performanceImprovementVerified") is True
        or completed.get("improvementSignalStatus") == "positive"
        or trend in {"improving", "better", "reduced"}
    )
    ready = bool(
        isinstance(parsed, dict)
        and parsed.get("readyForProofHandoff") is True
        and status in {"proof-ready", "local-discord-proof-classified"}
        and parsed.get("secretValuesPrinted") is not True
        and int(active.get("battleCount") or 0) == 0
        and completed.get("isCurrent") is True
        and improvement_signal_ok
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
        "performanceImprovementVerified": bool(completed.get("performanceImprovementVerified")),
        "performanceTrendStatus": completed.get("performanceTrendStatus"),
        "improvementSignalOk": improvement_signal_ok,
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
    battle_surfaces = battle_surface_readiness(truth, endpoints, check_http=check_http, http_open=http_open)
    runtime_blocked = bool(stream_summary.get("runtimeBlocked"))
    credential_failure = recent_showdown_credential_failure(ROOT)
    processes = runtime_processes()
    process_inspection_ready = all(bool(proc.get("processInspectionAvailable", True)) for proc in processes)
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
    runtime_truth = runtime_truth_disposition(
        active_truth=active_battle_truth,
        stream_truth=stream_truth,
        runner_active=runner_active,
        battle_count=battle_count,
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
    if not process_inspection_ready:
        blockers.append(
            "python dependency psutil is missing; cannot verify Fouler PID files or prove runtime ownership"
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
        if runtime_truth["artifacts"]["activeBattles"].get("state") == "blocked":
            blockers.append(
                "fouler-play active_battles.json is stale and no battle runner is alive; "
                "archive/adopt runtime state only through HERMES-owned devstream_session with a finite proof-window runtime lease"
            )
        elif runtime_truth["artifacts"]["streamStatus"].get("state") == "blocked":
            blockers.append(
                "fouler-play stream_status.json is stale and no battle runner is alive; "
                "archive/adopt runtime state only through HERMES-owned devstream_session with a finite proof-window runtime lease"
            )
        else:
            if obs_surface_ready:
                blockers.append("fouler-play battle runner is idle; OBS HTTP alone is not active battle proof")
    elif not runner_active and battle_count > 0 and active_battle_truth.get("stale"):
        runner_proof_missing = True
        blockers.append(
            "fouler-play active battle truth is stale and no battle runner is alive; "
            "archive/adopt stale battle state through HERMES devstream_session start with a finite proof-window runtime lease"
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
    if not battle_surfaces["ready"]:
        if not battle_surfaces["maxSlotsOk"]:
            blockers.append(
                f"devstream mode expects {battle_surfaces['expected']} concurrent battle surfaces; active_battles.json reports "
                f"max_slots={battle_surfaces['declaredMaxSlots']}"
            )
        failed_slots = [
            item for item in battle_surfaces["checks"]
            if not item["ready"]
        ]
        if failed_slots:
            failed_labels = ", ".join(
                f"slot {item['slot']} page={item['pageOk']} state={item['stateOk']}"
                for item in failed_slots
            )
            blockers.append(f"devstream public battle slot surface check failed: {failed_labels}")
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
        and battle_surfaces["ready"]
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
    elif runtime_truth.get("state") == "blocked":
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
            "streamReady": slots["ready"] and battle_surfaces["ready"] and (obs_surface_ready or not check_http),
            "analyticsFresh": analytics_fresh,
            "discordReportingReady": discord_reporting_ready,
            "proofHandoffReady": proof_handoff_ready,
        },
        "battleSurfaceReadiness": battle_surfaces,
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
            "processInspectionReady": process_inspection_ready,
            "battleRunnerCount": len(battle_runners),
            "duplicateBattleRunners": duplicate_battle_runner_blocked,
            "requiredHermesAction": (
                "drain/adopt exactly one live battle runner; do not kill processes from this health probe"
                if duplicate_battle_runner_blocked
                else None
            ),
        },
        "runtimeTruthDisposition": runtime_truth,
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
