#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import os
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_RUNTIME_LEASE_PATH = ROOT / "devstream" / "truth" / "runtime-lease.json"
RUNTIME_LEASE_PATH_ENV = "FOULER_RUNTIME_LEASE_PATH"
PROJECT_ID = "fouler-play"

ACTIVE_STATUSES = {"active", "approved", "current", "open"}
PURPOSE_DELEGATIONS: dict[str, tuple[str, ...]] = {
    # A JIGGLYPUFF runtime start is a bounded session lease, not just
    # permission to invoke the outer SSH wrapper. The wrapper launches the
    # supervisor, and the supervisor launches the bounded battle session.
    "jigglypuff-runtime-start": (
        "devstream-start-continuous",
        "devstream-supervise",
        "devstream-start",
        "devstream-stale-truth-cleanup-dry-run",
        "devstream-stale-truth-cleanup",
        "run-py-battle-runner",
    ),
    "devstream-start-continuous": (
        "devstream-supervise",
        "devstream-start",
        "devstream-stale-truth-cleanup-dry-run",
        "devstream-stale-truth-cleanup",
        "run-py-battle-runner",
    ),
    "devstream-supervise": (
        "devstream-start",
        "devstream-stale-truth-cleanup-dry-run",
        "devstream-stale-truth-cleanup",
        "run-py-battle-runner",
    ),
    "devstream-start": ("run-py-battle-runner",),
}


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


def iso_now() -> str:
    return utc_now().isoformat()


def iso_timestamp(value: datetime) -> str:
    return value.astimezone(timezone.utc).isoformat()


def runtime_lease_path(path: str | os.PathLike[str] | None = None, env: dict[str, str] | None = None) -> Path:
    env = env if env is not None else os.environ
    configured = str(path or env.get(RUNTIME_LEASE_PATH_ENV) or "").strip()
    return Path(configured) if configured else DEFAULT_RUNTIME_LEASE_PATH


def read_json(path: Path) -> dict[str, Any]:
    try:
        parsed = json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return {}
    return parsed if isinstance(parsed, dict) else {}


def atomic_write_json(path: Path, payload: dict[str, Any]) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp_path = path.with_name(f".{path.name}.{os.getpid()}.{utc_now().strftime('%Y%m%dT%H%M%S%fZ')}.tmp")
    try:
        tmp_path.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")
        os.replace(tmp_path, path)
    finally:
        try:
            tmp_path.unlink()
        except FileNotFoundError:
            pass
        except OSError:
            pass
    return path


def expanded_allowed_purposes(purpose: str) -> list[str]:
    purpose = str(purpose or "").strip()
    if not purpose:
        return []
    expanded: list[str] = []
    seen: set[str] = set()
    stack = [purpose]
    while stack:
        current = stack.pop(0)
        if current in seen:
            continue
        seen.add(current)
        expanded.append(current)
        stack.extend(PURPOSE_DELEGATIONS.get(current, ()))
    return expanded


def build_runtime_lease_artifact(
    *,
    purpose: str,
    machine: str,
    account: str,
    run_count: int,
    max_cycles: int,
    max_concurrent_battles: int,
    replay_behavior: str,
    valid_minutes: int,
    lease_id: str | None = None,
    status: str = "active",
    approved: bool = True,
    now: datetime | None = None,
) -> dict[str, Any]:
    current = (now or utc_now()).astimezone(timezone.utc)
    if positive_int(run_count) is None:
        raise ValueError("run_count must be positive")
    if positive_int(max_cycles) is None:
        raise ValueError("max_cycles must be positive")
    if positive_int(max_concurrent_battles) is None:
        raise ValueError("max_concurrent_battles must be positive")
    if positive_int(valid_minutes) is None:
        raise ValueError("valid_minutes must be positive")
    purpose = str(purpose or "").strip()
    machine = str(machine or "").strip()
    account = str(account or "").strip()
    replay_behavior = str(replay_behavior or "").strip()
    if not purpose:
        raise ValueError("purpose is required")
    if not machine:
        raise ValueError("machine is required")
    if not account:
        raise ValueError("account is required")
    if not replay_behavior:
        raise ValueError("replay_behavior is required")

    expires_at = current + timedelta(minutes=int(valid_minutes))
    generated_id = lease_id or (
        "fouler-"
        + purpose.replace("_", "-").replace(" ", "-")
        + "-"
        + current.strftime("%Y%m%dT%H%M%SZ")
        + f"-run{int(run_count)}"
    )
    return {
        "schemaVersion": "fouler-play-runtime-lease/v1",
        "projectId": PROJECT_ID,
        "leaseId": generated_id,
        "status": status,
        "approved": approved,
        "createdAt": iso_timestamp(current),
        "machine": machine,
        "account": account,
        "allowedPurposes": expanded_allowed_purposes(purpose),
        "maxRunCount": int(run_count),
        "maxCycles": int(max_cycles),
        "maxConcurrentBattles": int(max_concurrent_battles),
        "replayBehavior": replay_behavior,
        "proofWindow": {
            "startsAt": iso_timestamp(current),
            "expiresAt": iso_timestamp(expires_at),
        },
        "battleScope": {
            "machine": machine,
            "account": account,
            "runCount": int(run_count),
            "maxRunCount": int(run_count),
            "maxConcurrentBattles": int(max_concurrent_battles),
            "replayBehavior": replay_behavior,
        },
        "cycleScope": {
            "maxCycles": int(max_cycles),
        },
        "notes": "Generated by devstream_runtime_lease.py without starting Showdown, Discord, battles, laddering, or auto-improvement.",
    }


def parse_timestamp(value: object) -> datetime | None:
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


def positive_int(value: object) -> int | None:
    try:
        parsed = int(str(value).strip())
    except (TypeError, ValueError):
        return None
    return parsed if parsed > 0 else None


def _lookup(data: dict[str, Any], path: tuple[str, ...]) -> object:
    current: object = data
    for key in path:
        if not isinstance(current, dict):
            return None
        current = current.get(key)
    return current


def _first_text(data: dict[str, Any], paths: list[tuple[str, ...]]) -> str:
    for path in paths:
        value = _lookup(data, path)
        if isinstance(value, str) and value.strip():
            return value.strip()
    return ""


def _first_positive_int(data: dict[str, Any], paths: list[tuple[str, ...]]) -> int | None:
    for path in paths:
        parsed = positive_int(_lookup(data, path))
        if parsed is not None:
            return parsed
    return None


def lease_summary(lease: dict[str, Any]) -> dict[str, Any]:
    proof_window = lease.get("proofWindow") if isinstance(lease.get("proofWindow"), dict) else {}
    return {
        "id": _first_text(lease, [("leaseId",), ("id",)]),
        "projectId": _first_text(lease, [("projectId",), ("project",)]),
        "machine": _first_text(lease, [("machine",), ("runtime", "machine"), ("battleScope", "machine")]),
        "account": _first_text(
            lease,
            [
                ("account",),
                ("psUsername",),
                ("showdownAccount",),
                ("battleScope", "account"),
                ("battleScope", "psUsername"),
            ],
        ),
        "status": _first_text(lease, [("status",), ("proofWindow", "status")]),
        "proofWindow": {
            "startsAt": proof_window.get("startsAt") or proof_window.get("validFrom"),
            "expiresAt": proof_window.get("expiresAt") or proof_window.get("endsAt"),
        },
        "expiresAt": _first_text(lease, [("expiresAt",), ("proofWindow", "expiresAt"), ("proofWindow", "endsAt")]),
        "maxRunCount": _first_positive_int(
            lease,
            [
                ("maxRunCount",),
                ("runCount",),
                ("battleScope", "maxRunCount"),
                ("battleScope", "runCount"),
                ("bounds", "maxRunCount"),
                ("bounds", "runCount"),
            ],
        ),
        "maxCycles": _first_positive_int(
            lease,
            [
                ("maxCycles",),
                ("cycleScope", "maxCycles"),
                ("bounds", "maxCycles"),
            ],
        ),
        "maxConcurrentBattles": _first_positive_int(
            lease,
            [
                ("maxConcurrentBattles",),
                ("battleScope", "maxConcurrentBattles"),
                ("bounds", "maxConcurrentBattles"),
            ],
        ),
        "replayBehavior": _first_text(
            lease,
            [("replayBehavior",), ("battleScope", "replayBehavior"), ("saveReplay",), ("battleScope", "saveReplay")],
        ),
    }


def _allowed_for_purpose(lease: dict[str, Any], purpose: str) -> bool:
    allowed = lease.get("allowedPurposes") or lease.get("purposes")
    if not allowed:
        return True
    if isinstance(allowed, str):
        allowed = [allowed]
    if not isinstance(allowed, list):
        return False
    normalized = {str(item).strip().lower() for item in allowed if str(item).strip()}
    return "*" in normalized or purpose.strip().lower() in normalized


def validate_runtime_lease(
    *,
    purpose: str,
    lease_path: str | os.PathLike[str] | None = None,
    requested_run_count: int | None = None,
    requested_max_cycles: int | None = None,
    requested_max_concurrent_battles: int | None = None,
    requested_account: str | None = None,
    require_run_count: bool = False,
    require_max_cycles: bool = False,
    require_max_concurrent_battles: bool = False,
    require_replay_behavior: bool = False,
    now: datetime | None = None,
    env: dict[str, str] | None = None,
) -> dict[str, Any]:
    path = runtime_lease_path(lease_path, env)
    checked_at = iso_now()
    blockers: list[str] = []
    warnings: list[str] = []
    lease = read_json(path)
    summary = lease_summary(lease) if lease else {}

    if not path.exists():
        blockers.append(f"runtime lease file is missing: {path}")
    elif not lease:
        blockers.append(f"runtime lease file is unreadable or not a JSON object: {path}")

    if lease:
        if summary.get("projectId") != PROJECT_ID:
            blockers.append(f"runtime lease projectId must be {PROJECT_ID}")
        if not summary.get("id"):
            blockers.append("runtime lease must include leaseId or id")
        if not summary.get("machine"):
            blockers.append("runtime lease must name the runtime machine")
        if not summary.get("account"):
            blockers.append("runtime lease must name the Showdown account")
        if requested_account and summary.get("account") and str(summary["account"]).lower() != requested_account.lower():
            blockers.append(
                f"runtime lease account {summary['account']} does not match requested account {requested_account}"
            )
        if not _allowed_for_purpose(lease, purpose):
            blockers.append(f"runtime lease does not allow purpose {purpose}")
        status = str(summary.get("status") or "").strip().lower()
        if status and status not in ACTIVE_STATUSES:
            blockers.append(f"runtime lease status is not active/approved: {status}")
        if lease.get("approved") is False:
            blockers.append("runtime lease approved flag is false")

        proof_window = lease.get("proofWindow") if isinstance(lease.get("proofWindow"), dict) else None
        if proof_window is None:
            blockers.append("runtime lease must include proofWindow")
        else:
            starts_at = parse_timestamp(proof_window.get("startsAt") or proof_window.get("validFrom"))
            expires_at = parse_timestamp(proof_window.get("expiresAt") or proof_window.get("endsAt"))
            current = (now or utc_now()).astimezone(timezone.utc)
            if starts_at is None:
                blockers.append("proofWindow must include startsAt or validFrom")
            elif starts_at > current:
                blockers.append("proofWindow has not started")
            if expires_at is None:
                blockers.append("proofWindow must include expiresAt or endsAt")
            elif expires_at <= current:
                blockers.append("proofWindow is expired")

        if require_replay_behavior and not summary.get("replayBehavior"):
            blockers.append("runtime lease must name replay behavior")

    lease_run_count = positive_int(summary.get("maxRunCount")) if summary else None
    requested_run = positive_int(requested_run_count)
    if require_run_count and requested_run is None:
        blockers.append("requested run count must be a positive explicit bound")
    if require_run_count and lease_run_count is None:
        blockers.append("runtime lease must include maxRunCount or battleScope.runCount")
    if requested_run is not None and lease_run_count is not None and requested_run > lease_run_count:
        blockers.append(f"requested run count {requested_run} exceeds lease maxRunCount {lease_run_count}")

    lease_cycles = positive_int(summary.get("maxCycles")) if summary else None
    requested_cycles = positive_int(requested_max_cycles)
    if require_max_cycles and requested_cycles is None:
        blockers.append("requested max cycles must be a positive explicit bound")
    if require_max_cycles and lease_cycles is None:
        blockers.append("runtime lease must include maxCycles")
    if requested_cycles is not None and lease_cycles is not None and requested_cycles > lease_cycles:
        blockers.append(f"requested max cycles {requested_cycles} exceeds lease maxCycles {lease_cycles}")

    lease_concurrent = positive_int(summary.get("maxConcurrentBattles")) if summary else None
    requested_concurrent = positive_int(requested_max_concurrent_battles)
    if require_max_concurrent_battles and requested_concurrent is None:
        blockers.append("requested max concurrent battles must be a positive explicit bound")
    if require_max_concurrent_battles and lease_concurrent is None:
        blockers.append("runtime lease must include maxConcurrentBattles")
    if requested_concurrent is not None and lease_concurrent is not None and requested_concurrent > lease_concurrent:
        blockers.append(
            f"requested max concurrent battles {requested_concurrent} exceeds lease maxConcurrentBattles {lease_concurrent}"
        )

    return {
        "schemaVersion": "fouler-play-runtime-lease-check/v1",
        "checkedAt": checked_at,
        "ok": not blockers,
        "required": True,
        "purpose": purpose,
        "path": str(path),
        "requested": {
            "runCount": requested_run_count,
            "maxCycles": requested_max_cycles,
            "maxConcurrentBattles": requested_max_concurrent_battles,
            "account": requested_account,
        },
        "lease": summary,
        "blockers": blockers,
        "warnings": warnings,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="Validate the Fouler proof-window runtime lease.")
    parser.add_argument("--purpose", required=True)
    parser.add_argument("--runtime-lease")
    parser.add_argument(
        "--write",
        action="store_true",
        help="Write a finite runtime lease artifact before validating it. This only writes JSON.",
    )
    parser.add_argument("--machine", help="Runtime machine to write into a generated lease.")
    parser.add_argument("--run-count", type=int)
    parser.add_argument("--max-cycles", type=int)
    parser.add_argument("--max-concurrent-battles", type=int)
    parser.add_argument("--account")
    parser.add_argument("--replay-behavior", default="never")
    parser.add_argument("--valid-minutes", type=int, default=45)
    parser.add_argument("--lease-id")
    parser.add_argument("--status", default="active")
    parser.add_argument("--approved", action=argparse.BooleanOptionalAction, default=True)
    parser.add_argument("--require-run-count", action="store_true")
    parser.add_argument("--require-max-cycles", action="store_true")
    parser.add_argument("--require-max-concurrent-battles", action="store_true")
    parser.add_argument("--require-replay-behavior", action="store_true")
    args = parser.parse_args()
    written: dict[str, Any] | None = None
    if args.write:
        if not args.runtime_lease:
            parser.error("--write requires --runtime-lease so generated proof-window leases are never written implicitly")
        try:
            lease = build_runtime_lease_artifact(
                purpose=args.purpose,
                machine=args.machine or "",
                account=args.account or "",
                run_count=args.run_count,
                max_cycles=args.max_cycles,
                max_concurrent_battles=args.max_concurrent_battles,
                replay_behavior=args.replay_behavior,
                valid_minutes=args.valid_minutes,
                lease_id=args.lease_id,
                status=args.status,
                approved=args.approved,
            )
        except ValueError as exc:
            parser.error(str(exc))
        path = atomic_write_json(runtime_lease_path(args.runtime_lease), lease)
        written = {
            "path": str(path),
            "leaseId": lease["leaseId"],
            "proofWindow": lease["proofWindow"],
            "allowedPurposes": lease["allowedPurposes"],
            "noRuntimeActions": True,
        }
    payload = validate_runtime_lease(
        purpose=args.purpose,
        lease_path=args.runtime_lease,
        requested_run_count=args.run_count,
        requested_max_cycles=args.max_cycles,
        requested_max_concurrent_battles=args.max_concurrent_battles,
        requested_account=args.account,
        require_run_count=args.require_run_count,
        require_max_cycles=args.require_max_cycles,
        require_max_concurrent_battles=args.require_max_concurrent_battles,
        require_replay_behavior=args.require_replay_behavior,
    )
    if written is not None:
        payload = {
            "schemaVersion": "fouler-play-runtime-lease-write-check/v1",
            "ok": payload["ok"],
            "writtenLease": written,
            "validation": payload,
        }
    print(json.dumps(payload, indent=2, sort_keys=True))
    return 0 if payload["ok"] else 2


if __name__ == "__main__":
    raise SystemExit(main())
