#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import os
import re
import sys
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path, PureWindowsPath
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from infrastructure.deployment_lineage import (  # noqa: E402
    HOST_ID_HASH_RE,
    HostIdentityProvider,
    deployment_receipt_blockers,
    file_sha256,
    normalize_hostname,
    physical_host_binding,
    physical_host_binding_blockers,
)
from infrastructure.runtime_authorization import (  # noqa: E402
    RUNTIME_LEASE_SCHEMA_VERSION,
    atomic_write_exclusive,
    load_strict_json,
    runtime_lease_authorization_sha256,
    sign_runtime_lease,
    verify_runtime_lease_authorization,
)

RUNTIME_LEASE_PATH_ENV = "FOULER_RUNTIME_LEASE_PATH"


def _default_runtime_lease_path() -> Path:
    if os.name == "nt":
        return (
            Path(os.environ.get("PROGRAMDATA", r"C:\ProgramData"))
            / "HERMES"
            / "authority"
            / "fouler"
            / "runtime-lease.json"
        )
    return (
        Path.home()
        / ".config"
        / "deku-devstream"
        / "authority"
        / "fouler"
        / "runtime-lease.json"
    )


DEFAULT_RUNTIME_LEASE_PATH = _default_runtime_lease_path()
PROJECT_ID = "fouler-play"
LEASE_SCHEMA_VERSION = RUNTIME_LEASE_SCHEMA_VERSION
RUNTIME_ID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.:-]{7,127}$")
GIT_COMMIT_RE = re.compile(r"^[0-9a-f]{40,64}$")

ACTIVE_STATUSES = {"active", "approved", "current", "open"}
PURPOSE_DELEGATIONS: dict[str, tuple[str, ...]] = {
    # A JIGGLYPUFF runtime start is a bounded session lease, not just
    # permission to invoke the outer SSH wrapper. The wrapper launches the
    # supervisor, and the supervisor launches the bounded battle session.
    "jigglypuff-runtime-start": (
        "deployment-activation",
        "devstream-start-continuous-dry-run",
        "devstream-start-continuous",
        "devstream-supervise",
        "devstream-start-dry-run",
        "devstream-start",
        "devstream-stale-truth-cleanup-dry-run",
        "devstream-stale-truth-cleanup",
        "run-py-battle-runner",
    ),
    "devstream-start-continuous": (
        "deployment-activation",
        "devstream-start-continuous-dry-run",
        "devstream-supervise",
        "devstream-start-dry-run",
        "devstream-start",
        "devstream-stale-truth-cleanup-dry-run",
        "devstream-stale-truth-cleanup",
        "run-py-battle-runner",
    ),
    "devstream-supervise": (
        "deployment-activation",
        "devstream-start-dry-run",
        "devstream-start",
        "devstream-stale-truth-cleanup-dry-run",
        "devstream-stale-truth-cleanup",
        "run-py-battle-runner",
    ),
    "devstream-start": ("deployment-activation", "devstream-start-dry-run", "run-py-battle-runner"),
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
        parsed = load_strict_json(path)
    except Exception:
        return {}
    return parsed if isinstance(parsed, dict) else {}


def is_absolute_runtime_path(value: object) -> bool:
    text = str(value or "").strip()
    return bool(text) and (Path(text).is_absolute() or PureWindowsPath(text).is_absolute())


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
    source_commit: str,
    change_id: str,
    deployment_id: str,
    source_tree: str,
    runtime_manifest_digest: str,
    deployment_receipt_path: str,
    deployment_receipt_sha256: str,
    session_id: str | None = None,
    lease_id: str | None = None,
    status: str = "active",
    approved: bool = True,
    now: datetime | None = None,
    host_identity_provider: HostIdentityProvider | None = None,
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
    account = str(account or "").strip()
    replay_behavior = str(replay_behavior or "").strip()
    source_commit = str(source_commit or "").strip().lower()
    change_id = str(change_id or "").strip()
    deployment_id = str(deployment_id or "").strip()
    source_tree = str(source_tree or "").strip().lower()
    runtime_manifest_digest = str(runtime_manifest_digest or "").strip().lower()
    deployment_receipt_path = str(deployment_receipt_path or "").strip()
    deployment_receipt_sha256 = str(deployment_receipt_sha256 or "").strip().lower()
    session_id = str(session_id or uuid.uuid4()).strip()
    if not purpose:
        raise ValueError("purpose is required")
    # Preserve the legacy label for downstream state readers, but never use it
    # as physical-host authority.
    machine_label = str(machine or "").strip()
    host_binding = physical_host_binding(
        host_identity_provider=host_identity_provider
    )
    host_binding["machine"] = machine_label or host_binding["hostName"]
    if not account:
        raise ValueError("account is required")
    if not replay_behavior:
        raise ValueError("replay_behavior is required")
    if not GIT_COMMIT_RE.fullmatch(source_commit):
        raise ValueError("source_commit must be a full Git commit ID")
    if not RUNTIME_ID_RE.fullmatch(change_id):
        raise ValueError("change_id is required and malformed")
    if not RUNTIME_ID_RE.fullmatch(deployment_id):
        raise ValueError("deployment_id is required and malformed")
    if not GIT_COMMIT_RE.fullmatch(source_tree):
        raise ValueError("source_tree must be a full Git tree ID")
    if not re.fullmatch(r"[0-9a-f]{64}", runtime_manifest_digest):
        raise ValueError("runtime_manifest_digest must be a SHA-256")
    if not is_absolute_runtime_path(deployment_receipt_path):
        raise ValueError("deployment_receipt_path must be absolute")
    if not re.fullmatch(r"[0-9a-f]{64}", deployment_receipt_sha256):
        raise ValueError("deployment_receipt_sha256 must be a SHA-256")
    if not RUNTIME_ID_RE.fullmatch(session_id):
        raise ValueError("session_id is malformed")

    expires_at = current + timedelta(minutes=int(valid_minutes))
    generated_id = lease_id or (
        "fouler-"
        + purpose.replace("_", "-").replace(" ", "-")
        + "-"
        + uuid.uuid4().hex
    )
    return {
        "schemaVersion": LEASE_SCHEMA_VERSION,
        "projectId": PROJECT_ID,
        "leaseId": generated_id,
        "sourceCommit": source_commit,
        "changeId": change_id,
        "deploymentId": deployment_id,
        "sourceTree": source_tree,
        "runtimeManifestDigest": runtime_manifest_digest,
        "deploymentReceiptPath": deployment_receipt_path,
        "deploymentReceiptSha256": deployment_receipt_sha256,
        "sessionId": session_id,
        "status": status,
        "approved": approved,
        "createdAt": iso_timestamp(current),
        **host_binding,
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
            **host_binding,
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


def issue_runtime_lease_from_receipt(
    *,
    deployment_receipt_input: Path,
    deployment_receipt_path: str,
    output_path: Path,
    controller_private_key: Path,
    controller_trust_store: Path,
    controller_key_id: str,
    issued_by: str,
    purpose: str,
    account: str,
    run_count: int,
    max_cycles: int,
    max_concurrent_battles: int,
    replay_behavior: str,
    valid_minutes: int,
    expected_machine: str = "",
    expected_source_commit: str = "",
    expected_source_tree: str = "",
    expected_change_id: str = "",
    expected_deployment_id: str = "",
    expected_runtime_manifest_digest: str = "",
    expected_deployment_receipt_sha256: str = "",
    now: datetime | None = None,
) -> dict[str, Any]:
    """Issue one DEKU-signed lease from the exact target deployment receipt."""

    deployment_receipt_input = deployment_receipt_input.expanduser().resolve(strict=True)
    output_path = output_path.expanduser().absolute()
    if not is_absolute_runtime_path(deployment_receipt_path):
        raise ValueError("deployment_receipt_path must be an absolute target-host path")
    receipt = load_strict_json(deployment_receipt_input)
    if not isinstance(receipt, dict):
        raise ValueError("deployment receipt input must be a JSON object")
    receipt_host = {
        "hostname": str(receipt.get("hostName") or ""),
        "hostIdSha256": str(receipt.get("hostIdSha256") or ""),
    }
    _receipt, receipt_blockers = deployment_receipt_blockers(
        deployment_receipt_input,
        root=ROOT,
        verify_checkout=False,
        current_host=receipt_host,
    )
    if receipt_blockers:
        raise ValueError("deployment receipt input is invalid: " + "; ".join(receipt_blockers))

    receipt_sha256 = file_sha256(deployment_receipt_input)
    assertions = {
        "machine": expected_machine,
        "sourceCommit": expected_source_commit.lower(),
        "sourceTree": expected_source_tree.lower(),
        "changeId": expected_change_id,
        "deploymentId": expected_deployment_id,
        "runtimeManifestDigest": expected_runtime_manifest_digest.lower(),
        "deploymentReceiptSha256": expected_deployment_receipt_sha256.lower(),
    }
    actual = {
        "machine": str(receipt.get("machine") or ""),
        "sourceCommit": str(receipt.get("sourceCommit") or "").lower(),
        "sourceTree": str(receipt.get("sourceTree") or "").lower(),
        "changeId": str(receipt.get("changeId") or ""),
        "deploymentId": str(receipt.get("deploymentId") or ""),
        "runtimeManifestDigest": str(receipt.get("runtimeManifestDigest") or "").lower(),
        "deploymentReceiptSha256": receipt_sha256,
    }
    for field, expected in assertions.items():
        if expected and actual[field] != expected:
            raise ValueError(f"deployment receipt {field} does not match the issuance assertion")

    def host_provider() -> dict[str, str]:
        return dict(receipt_host)

    unsigned = build_runtime_lease_artifact(
        purpose=purpose,
        machine=actual["machine"],
        account=account,
        run_count=run_count,
        max_cycles=max_cycles,
        max_concurrent_battles=max_concurrent_battles,
        replay_behavior=replay_behavior,
        valid_minutes=valid_minutes,
        source_commit=actual["sourceCommit"],
        change_id=actual["changeId"],
        deployment_id=actual["deploymentId"],
        source_tree=actual["sourceTree"],
        runtime_manifest_digest=actual["runtimeManifestDigest"],
        deployment_receipt_path=deployment_receipt_path,
        deployment_receipt_sha256=receipt_sha256,
        now=now,
        host_identity_provider=host_provider,
    )
    signed = sign_runtime_lease(
        unsigned,
        controller_private_key,
        key_id=controller_key_id,
        issued_by=issued_by,
    )
    authorization = verify_runtime_lease_authorization(
        signed,
        controller_trust_store,
        env={},
    )
    if not authorization.get("ok"):
        messages = [
            str(item.get("message") or item.get("code") or "authorization failed")
            for item in authorization.get("blockers", [])
            if isinstance(item, dict)
        ]
        raise ValueError("issued runtime lease failed self-verification: " + "; ".join(messages))
    encoded = (json.dumps(signed, indent=2, sort_keys=True) + "\n").encode("utf-8")
    atomic_write_exclusive(output_path, encoded, mode=0o444)
    return signed


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
    controller_authorization = (
        lease.get("controllerAuthorization")
        if isinstance(lease.get("controllerAuthorization"), dict)
        else {}
    )
    return {
        "id": _first_text(lease, [("leaseId",), ("id",)]),
        "sourceCommit": _first_text(lease, [("sourceCommit",)]).lower(),
        "sourceTree": _first_text(lease, [("sourceTree",)]).lower(),
        "changeId": _first_text(lease, [("changeId",)]),
        "deploymentId": _first_text(lease, [("deploymentId",)]),
        "runtimeManifestDigest": _first_text(lease, [("runtimeManifestDigest",)]).lower(),
        "deploymentReceiptPath": _first_text(lease, [("deploymentReceiptPath",)]),
        "deploymentReceiptSha256": _first_text(lease, [("deploymentReceiptSha256",)]).lower(),
        "sessionId": _first_text(lease, [("sessionId",)]),
        "projectId": _first_text(lease, [("projectId",), ("project",)]),
        "machine": _first_text(lease, [("machine",), ("runtime", "machine"), ("battleScope", "machine")]),
        "hostName": _first_text(lease, [("hostName",), ("battleScope", "hostName")]).lower(),
        "hostIdSha256": _first_text(
            lease,
            [("hostIdSha256",), ("battleScope", "hostIdSha256")],
        ).lower(),
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
        "controllerKeyId": str(controller_authorization.get("keyId") or ""),
        "controllerIssuedBy": str(controller_authorization.get("issuedBy") or ""),
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
        return False
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
    requested_source_commit: str | None = None,
    requested_change_id: str | None = None,
    requested_deployment_id: str | None = None,
    requested_session_id: str | None = None,
    requested_replay_behavior: object | None = None,
    require_deployment_receipt: bool = False,
    verify_deployment_checkout: bool = False,
    require_run_count: bool = False,
    require_max_cycles: bool = False,
    require_max_concurrent_battles: bool = False,
    require_replay_behavior: bool = False,
    now: datetime | None = None,
    env: dict[str, str] | None = None,
    host_identity_provider: HostIdentityProvider | None = None,
    controller_trust_store: str | os.PathLike[str] | dict[str, Any] | None = None,
) -> dict[str, Any]:
    path = runtime_lease_path(lease_path, env)
    checked_at = iso_now()
    blockers: list[str] = []
    warnings: list[str] = []
    lease = read_json(path)
    summary = lease_summary(lease) if lease else {}
    authorization: dict[str, Any] = {
        "ok": False,
        "blockers": [{"code": "lease_unavailable", "message": "runtime lease is unavailable"}],
    }

    if not path.exists():
        blockers.append(f"runtime lease file is missing: {path}")
    elif not lease:
        blockers.append(f"runtime lease file is unreadable or not a JSON object: {path}")

    if lease:
        authorization = verify_runtime_lease_authorization(
            lease,
            controller_trust_store,
            env={},
            require_protected_trust_store=True,
        )
        if not authorization.get("ok"):
            for item in authorization.get("blockers", []):
                if isinstance(item, dict):
                    blockers.append(
                        "controller authorization: "
                        + str(item.get("message") or item.get("code") or "verification failed")
                    )
                else:
                    blockers.append(f"controller authorization: {item}")
        else:
            try:
                summary["authorizationSha256"] = runtime_lease_authorization_sha256(lease)
            except (TypeError, ValueError) as exc:
                blockers.append(f"controller authorization digest is unavailable: {exc}")
        if lease.get("schemaVersion") != LEASE_SCHEMA_VERSION:
            blockers.append(f"runtime lease schemaVersion must be {LEASE_SCHEMA_VERSION}")
        if summary.get("projectId") != PROJECT_ID:
            blockers.append(f"runtime lease projectId must be {PROJECT_ID}")
        if not summary.get("id"):
            blockers.append("runtime lease must include leaseId or id")
        if not summary.get("machine"):
            blockers.append("runtime lease must name the runtime machine")
        blockers.extend(
            physical_host_binding_blockers(
                lease,
                label="runtime lease",
                host_identity_provider=host_identity_provider,
            )
        )
        battle_scope = (
            lease.get("battleScope")
            if isinstance(lease.get("battleScope"), dict)
            else {}
        )
        for field in ("machine", "hostName", "hostIdSha256"):
            if battle_scope.get(field) != lease.get(field):
                blockers.append(
                    f"runtime lease battleScope {field} does not match the top-level host binding"
                )
        if not summary.get("account"):
            blockers.append("runtime lease must name the Showdown account")
        if not GIT_COMMIT_RE.fullmatch(str(summary.get("sourceCommit") or "")):
            blockers.append("runtime lease must include a full sourceCommit")
        if not GIT_COMMIT_RE.fullmatch(str(summary.get("sourceTree") or "")):
            blockers.append("runtime lease must include a full sourceTree")
        if not re.fullmatch(r"[0-9a-f]{64}", str(summary.get("runtimeManifestDigest") or "")):
            blockers.append("runtime lease must include a runtimeManifestDigest SHA-256")
        receipt_path_text = str(summary.get("deploymentReceiptPath") or "")
        if not is_absolute_runtime_path(receipt_path_text):
            blockers.append("runtime lease must include an absolute deploymentReceiptPath")
        if not re.fullmatch(r"[0-9a-f]{64}", str(summary.get("deploymentReceiptSha256") or "")):
            blockers.append("runtime lease must include a deploymentReceiptSha256")
        for field in ("changeId", "deploymentId", "sessionId"):
            if not RUNTIME_ID_RE.fullmatch(str(summary.get(field) or "")):
                blockers.append(f"runtime lease must include a valid {field}")
        if requested_account and summary.get("account") and str(summary["account"]).lower() != requested_account.lower():
            blockers.append(
                f"runtime lease account {summary['account']} does not match requested account {requested_account}"
            )
        requested_identity = {
            "sourceCommit": requested_source_commit,
            "changeId": requested_change_id,
            "deploymentId": requested_deployment_id,
            "sessionId": requested_session_id,
        }
        for field, requested_value in requested_identity.items():
            requested_text = str(requested_value or "").strip()
            if requested_text and str(summary.get(field) or "").strip() != requested_text:
                blockers.append(f"runtime lease {field} does not match requested {field}")
        if not _allowed_for_purpose(lease, purpose):
            blockers.append(f"runtime lease does not allow purpose {purpose}")
        status = str(summary.get("status") or "").strip().lower()
        if not status:
            blockers.append("runtime lease must include an explicit active status")
        elif status not in ACTIVE_STATUSES:
            blockers.append(f"runtime lease status is not active/approved: {status}")
        if lease.get("approved") is not True:
            blockers.append("runtime lease approved flag must be explicitly true")

        if require_deployment_receipt and is_absolute_runtime_path(receipt_path_text):
            _receipt, receipt_blockers = deployment_receipt_blockers(
                Path(receipt_path_text),
                root=ROOT,
                expected=summary,
                verify_checkout=verify_deployment_checkout,
                host_identity_provider=host_identity_provider,
            )
            blockers.extend(f"deployment receipt: {item}" for item in receipt_blockers)

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

        requested_replay = str(
            getattr(requested_replay_behavior, "name", requested_replay_behavior) or ""
        ).strip().lower()
        lease_replay = str(summary.get("replayBehavior") or "").strip().lower()
        if requested_replay and lease_replay != requested_replay:
            blockers.append(
                "runtime lease replayBehavior does not match requested replay behavior"
            )

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
            "sourceCommit": requested_source_commit,
            "changeId": requested_change_id,
            "deploymentId": requested_deployment_id,
            "sessionId": requested_session_id,
            "replayBehavior": str(
                getattr(requested_replay_behavior, "name", requested_replay_behavior) or ""
            ).strip(),
        },
        "lease": summary,
        "controllerAuthorization": authorization,
        "blockers": blockers,
        "warnings": warnings,
    }


def lease_environment(validation: dict[str, Any]) -> dict[str, str]:
    """Return the exact non-secret process identity approved by a lease check."""
    if not validation.get("ok"):
        raise ValueError("cannot derive an environment from an invalid runtime lease")
    summary = validation.get("lease") if isinstance(validation.get("lease"), dict) else {}
    path = Path(str(validation.get("path") or "")).expanduser().resolve(strict=True)
    mapping = {
        "FOULER_SOURCE_COMMIT": str(summary.get("sourceCommit") or ""),
        "FOULER_CHANGE_ID": str(summary.get("changeId") or ""),
        "FOULER_DEPLOYMENT_ID": str(summary.get("deploymentId") or ""),
        "FOULER_SESSION_ID": str(summary.get("sessionId") or ""),
        "FOULER_RUNTIME_LEASE_ID": str(summary.get("id") or ""),
        "FOULER_RUNTIME_AUTHORIZATION_SHA256": str(summary.get("authorizationSha256") or ""),
        "FOULER_SOURCE_TREE": str(summary.get("sourceTree") or ""),
        "FOULER_RUNTIME_MANIFEST_DIGEST": str(summary.get("runtimeManifestDigest") or ""),
        "FOULER_DEPLOYMENT_RECEIPT_SHA256": str(summary.get("deploymentReceiptSha256") or ""),
        "FOULER_DEPLOYMENT_RECEIPT_PATH": str(summary.get("deploymentReceiptPath") or ""),
        "FOULER_PHYSICAL_HOSTNAME": str(summary.get("hostName") or ""),
        "FOULER_PHYSICAL_HOST_ID_SHA256": str(summary.get("hostIdSha256") or ""),
        RUNTIME_LEASE_PATH_ENV: str(path),
    }
    if not GIT_COMMIT_RE.fullmatch(mapping["FOULER_SOURCE_COMMIT"]):
        raise ValueError("validated runtime lease sourceCommit is malformed")
    for name in (
        "FOULER_CHANGE_ID",
        "FOULER_DEPLOYMENT_ID",
        "FOULER_SESSION_ID",
        "FOULER_RUNTIME_LEASE_ID",
        "FOULER_RUNTIME_AUTHORIZATION_SHA256",
        "FOULER_SOURCE_TREE",
        "FOULER_RUNTIME_MANIFEST_DIGEST",
        "FOULER_DEPLOYMENT_RECEIPT_SHA256",
    ):
        if not RUNTIME_ID_RE.fullmatch(mapping[name]):
            raise ValueError(f"validated runtime lease identity is malformed: {name}")
    try:
        if normalize_hostname(mapping["FOULER_PHYSICAL_HOSTNAME"]) != mapping[
            "FOULER_PHYSICAL_HOSTNAME"
        ]:
            raise ValueError
    except ValueError:
        raise ValueError(
            "validated runtime lease identity is malformed: FOULER_PHYSICAL_HOSTNAME"
        ) from None
    if not HOST_ID_HASH_RE.fullmatch(mapping["FOULER_PHYSICAL_HOST_ID_SHA256"]):
        raise ValueError(
            "validated runtime lease identity is malformed: FOULER_PHYSICAL_HOST_ID_SHA256"
        )
    return mapping


def main() -> int:
    parser = argparse.ArgumentParser(description="Validate the Fouler proof-window runtime lease.")
    parser.add_argument("--purpose", required=True)
    parser.add_argument("--runtime-lease")
    parser.add_argument(
        "--issue",
        action="store_true",
        help="Issue one DEKU-signed v3 lease from an exact target deployment receipt.",
    )
    parser.add_argument("--deployment-receipt-input")
    parser.add_argument("--controller-private-key")
    parser.add_argument("--controller-trust-store")
    parser.add_argument("--controller-key-id")
    parser.add_argument("--issued-by")
    parser.add_argument(
        "--machine",
        help="Compatibility hint only; physical host identity is read from the executing OS.",
    )
    parser.add_argument("--run-count", type=int)
    parser.add_argument("--max-cycles", type=int)
    parser.add_argument("--max-concurrent-battles", type=int)
    parser.add_argument("--account")
    parser.add_argument("--replay-behavior")
    parser.add_argument("--valid-minutes", type=int, default=45)
    parser.add_argument("--source-commit", default=os.getenv("FOULER_SOURCE_COMMIT", ""))
    parser.add_argument("--source-tree", default=os.getenv("FOULER_SOURCE_TREE", ""))
    parser.add_argument("--change-id", default=os.getenv("FOULER_CHANGE_ID", ""))
    parser.add_argument("--deployment-id", default=os.getenv("FOULER_DEPLOYMENT_ID", ""))
    parser.add_argument(
        "--runtime-manifest-digest",
        default=os.getenv("FOULER_RUNTIME_MANIFEST_DIGEST", ""),
    )
    parser.add_argument("--deployment-receipt-path", default=os.getenv("FOULER_DEPLOYMENT_RECEIPT_PATH", ""))
    parser.add_argument(
        "--deployment-receipt-sha256",
        default=os.getenv("FOULER_DEPLOYMENT_RECEIPT_SHA256", ""),
    )
    parser.add_argument("--session-id", default=os.getenv("FOULER_SESSION_ID", ""))
    parser.add_argument("--require-run-count", action="store_true")
    parser.add_argument("--require-max-cycles", action="store_true")
    parser.add_argument("--require-max-concurrent-battles", action="store_true")
    parser.add_argument("--require-replay-behavior", action="store_true")
    parser.add_argument("--require-deployment-receipt", action="store_true")
    parser.add_argument("--verify-deployment-checkout", action="store_true")
    args = parser.parse_args()
    if args.issue:
        required = {
            "--runtime-lease": args.runtime_lease,
            "--deployment-receipt-input": args.deployment_receipt_input,
            "--deployment-receipt-path": args.deployment_receipt_path,
            "--controller-private-key": args.controller_private_key,
            "--controller-trust-store": args.controller_trust_store,
            "--controller-key-id": args.controller_key_id,
            "--issued-by": args.issued_by,
            "--account": args.account,
            "--run-count": args.run_count,
            "--max-cycles": args.max_cycles,
            "--max-concurrent-battles": args.max_concurrent_battles,
            "--replay-behavior": args.replay_behavior,
        }
        missing = [name for name, value in required.items() if value in {None, ""}]
        if missing:
            parser.error("--issue requires " + ", ".join(missing))
        try:
            issued = issue_runtime_lease_from_receipt(
                deployment_receipt_input=Path(args.deployment_receipt_input),
                deployment_receipt_path=args.deployment_receipt_path,
                output_path=runtime_lease_path(args.runtime_lease),
                controller_private_key=Path(args.controller_private_key),
                controller_trust_store=Path(args.controller_trust_store),
                controller_key_id=args.controller_key_id,
                issued_by=args.issued_by,
                purpose=args.purpose,
                account=args.account or "",
                run_count=args.run_count,
                max_cycles=args.max_cycles,
                max_concurrent_battles=args.max_concurrent_battles,
                replay_behavior=args.replay_behavior,
                valid_minutes=args.valid_minutes,
                expected_machine=args.machine or "",
                expected_source_commit=args.source_commit,
                expected_source_tree=args.source_tree,
                expected_change_id=args.change_id,
                expected_deployment_id=args.deployment_id,
                expected_runtime_manifest_digest=args.runtime_manifest_digest,
                expected_deployment_receipt_sha256=args.deployment_receipt_sha256,
            )
        except (FileExistsError, OSError, TypeError, ValueError) as exc:
            parser.error(str(exc))
        payload = {
            "schemaVersion": "fouler-play-runtime-lease-issue-check/v1",
            "ok": True,
            "issuedLease": {
                "path": str(runtime_lease_path(args.runtime_lease).absolute()),
                "leaseId": issued["leaseId"],
                "proofWindow": issued["proofWindow"],
                "allowedPurposes": issued["allowedPurposes"],
                "controllerKeyId": issued["controllerAuthorization"]["keyId"],
                "authorizationSha256": runtime_lease_authorization_sha256(issued),
                "noRuntimeActions": True,
            },
        }
        print(json.dumps(payload, indent=2, sort_keys=True))
        return 0
    payload = validate_runtime_lease(
        purpose=args.purpose,
        lease_path=args.runtime_lease,
        requested_run_count=args.run_count,
        requested_max_cycles=args.max_cycles,
        requested_max_concurrent_battles=args.max_concurrent_battles,
        requested_account=args.account,
        requested_source_commit=args.source_commit or None,
        requested_change_id=args.change_id or None,
        requested_deployment_id=args.deployment_id or None,
        requested_session_id=args.session_id or None,
        requested_replay_behavior=args.replay_behavior,
        require_run_count=args.require_run_count,
        require_max_cycles=args.require_max_cycles,
        require_max_concurrent_battles=args.require_max_concurrent_battles,
        require_replay_behavior=args.require_replay_behavior,
        require_deployment_receipt=args.require_deployment_receipt,
        verify_deployment_checkout=args.verify_deployment_checkout,
        controller_trust_store=args.controller_trust_store,
    )
    if payload["ok"]:
        payload["environment"] = lease_environment(payload)
    print(json.dumps(payload, indent=2, sort_keys=True))
    return 0 if payload["ok"] else 2


if __name__ == "__main__":
    raise SystemExit(main())
