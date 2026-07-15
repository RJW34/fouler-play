#!/usr/bin/env python3
"""Activation and judgment state for one immutable Fouler deployment."""

from __future__ import annotations

import json
import os
import re
import stat
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Mapping, Sequence

from infrastructure.deployment_lineage import (
    COMMIT_RE,
    ID_RE,
    SHA256_RE,
    canonical_sha256,
    deployment_receipt_blockers,
    file_sha256,
    write_immutable_receipt,
)
from infrastructure.runtime_authorization import (
    RUNTIME_LEASE_SCHEMA_VERSION,
    load_strict_json,
    runtime_lease_authorization_sha256,
    verify_runtime_lease_authorization,
)


PROJECT_ROOT = Path(__file__).resolve().parent.parent
ACTIVATION_SCHEMA_VERSION = "fouler-deployment-activation/v1"
ACTIVATION_POINTER_SCHEMA_VERSION = "fouler-current-activation/v1"
JUDGMENT_SCHEMA_VERSION = "fouler-deployment-judgment/v1"
LEASE_SCHEMA_VERSION = RUNTIME_LEASE_SCHEMA_VERSION
PROJECT_ID = "fouler-play"
FINAL_PASS_STATUSES = {"passed", "passed-no-baseline"}
MINIMUM_JUDGMENT_BATTLES = 30
MINIMUM_NO_BASELINE_WIN_RATE = 0.35
RUNTIME_ID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.:-]{7,127}$")

BATTLE_IDENTITY_FIELDS = {
    "source_commit": "sourceCommit",
    "source_tree": "sourceTree",
    "runtime_manifest_digest": "runtimeManifestDigest",
    "change_id": "changeId",
    "deployment_id": "deploymentId",
    "deployment_receipt_sha256": "deploymentReceiptSha256",
    "runtime_lease_id": "runtimeLeaseId",
    "runtime_authorization_sha256": "runtimeAuthorizationSha256",
    "session_id": "sessionId",
}


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


def parse_timestamp(value: object) -> datetime | None:
    if not isinstance(value, str) or not value.strip():
        return None
    try:
        parsed = datetime.fromisoformat(value.strip().replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def default_state_root() -> Path:
    if os.name == "nt":
        program_data = str(os.environ.get("PROGRAMDATA") or r"C:\ProgramData").strip()
        return Path(program_data) / "HERMES" / "state" / "fouler" / "deployments"
    return Path.home() / ".deku" / "state" / "fouler" / "deployments"


def current_activation_pointer_path(state_root: Path | None = None) -> Path:
    return (state_root or default_state_root()) / "current-activation.json"


def activation_receipt_path(activation_id: str, state_root: Path | None = None) -> Path:
    return (state_root or default_state_root()) / "activations" / f"{activation_id}.json"


def judgment_receipt_path(activation_id: str, state_root: Path | None = None) -> Path:
    return (state_root or default_state_root()) / "judgments" / f"{activation_id}.json"


def _canonical_row_sha256(row: Mapping[str, Any]) -> str:
    return canonical_sha256(dict(row))


def _load_regular_json(path: Path, label: str, blockers: list[str]) -> dict[str, Any]:
    if not path.is_file() or path.is_symlink():
        blockers.append(f"{label} is missing or linked")
        return {}
    try:
        payload = load_strict_json(path)
    except Exception as exc:
        blockers.append(f"{label} is malformed: {exc}")
        return {}
    if not isinstance(payload, dict):
        blockers.append(f"{label} is not a JSON object")
        return {}
    return payload


def _require_readonly(path: Path, label: str, blockers: list[str]) -> None:
    if path.is_file() and path.stat().st_mode & (stat.S_IWUSR | stat.S_IWGRP | stat.S_IWOTH):
        blockers.append(f"{label} is writable instead of immutable")


def _payload_without_hash(payload: Mapping[str, Any]) -> dict[str, Any]:
    return {key: value for key, value in payload.items() if key != "receiptSha256"}


def _normalize_account(value: object) -> str:
    return re.sub(r"[^a-z0-9]", "", str(value or "").lower())


def read_battle_rows(path: Path) -> list[dict[str, Any]]:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return []
    if isinstance(payload, dict):
        payload = payload.get("battles", [])
    if not isinstance(payload, list):
        return []
    return [row for row in payload if isinstance(row, dict)]


def _lease_summary(lease: Mapping[str, Any]) -> dict[str, Any]:
    return {
        "projectId": lease.get("projectId"),
        "runtimeLeaseId": lease.get("leaseId") or lease.get("id"),
        "sourceCommit": lease.get("sourceCommit"),
        "sourceTree": lease.get("sourceTree"),
        "runtimeManifestDigest": lease.get("runtimeManifestDigest"),
        "changeId": lease.get("changeId"),
        "deploymentId": lease.get("deploymentId"),
        "deploymentReceiptPath": lease.get("deploymentReceiptPath"),
        "deploymentReceiptSha256": lease.get("deploymentReceiptSha256"),
        "sessionId": lease.get("sessionId"),
        "machine": lease.get("machine"),
        "account": lease.get("account"),
    }


def _lease_blockers(
    lease_path: Path,
    *,
    deployment: Mapping[str, Any],
    deployment_receipt_path: Path,
    deployment_receipt_sha256: str,
    now: datetime | None = None,
    lease_payload: Mapping[str, Any] | None = None,
) -> tuple[dict[str, Any], list[str]]:
    blockers: list[str] = []
    lease = (
        dict(lease_payload)
        if isinstance(lease_payload, Mapping)
        else _load_regular_json(lease_path, "runtime lease", blockers)
    )
    if not lease:
        return {}, blockers
    summary = _lease_summary(lease)
    authorization = verify_runtime_lease_authorization(
        lease,
        env={},
        require_protected_trust_store=True,
    )
    if not authorization.get("ok"):
        blockers.extend(
            "controller authorization: "
            + str(item.get("message") or item.get("code") or "verification failed")
            for item in authorization.get("blockers", [])
            if isinstance(item, dict)
        )
    else:
        try:
            summary["runtimeAuthorizationSha256"] = runtime_lease_authorization_sha256(lease)
        except (TypeError, ValueError) as exc:
            blockers.append(f"controller authorization digest is unavailable: {exc}")
    if lease.get("schemaVersion") != LEASE_SCHEMA_VERSION:
        blockers.append("runtime lease schema is unsupported")
    if summary["projectId"] != PROJECT_ID:
        blockers.append("runtime lease projectId is not fouler-play")
    if lease.get("approved") is not True:
        blockers.append("runtime lease approved flag is not explicitly true")
    if str(lease.get("status") or "").lower() not in {"active", "approved", "current", "open"}:
        blockers.append("runtime lease is not active")
    if not RUNTIME_ID_RE.fullmatch(str(summary["runtimeLeaseId"] or "")):
        blockers.append("runtime lease id is malformed")
    if not RUNTIME_ID_RE.fullmatch(str(summary["sessionId"] or "")):
        blockers.append("runtime lease sessionId is malformed")
    if not str(summary["machine"] or "").strip():
        blockers.append("runtime lease machine is missing")
    if not _normalize_account(summary["account"]):
        blockers.append("runtime lease account is missing")

    proof_window = lease.get("proofWindow") if isinstance(lease.get("proofWindow"), dict) else {}
    starts_at = parse_timestamp(proof_window.get("startsAt"))
    expires_at = parse_timestamp(proof_window.get("expiresAt"))
    current = (now or utc_now()).astimezone(timezone.utc)
    if starts_at is None or starts_at > current:
        blockers.append("runtime lease proof window has not started")
    if expires_at is None or expires_at <= current:
        blockers.append("runtime lease proof window is expired")

    allowed = lease.get("allowedPurposes")
    if isinstance(allowed, str):
        allowed = [allowed]
    normalized = (
        {str(item).strip().lower() for item in allowed if str(item).strip()}
        if isinstance(allowed, list)
        else set()
    )
    if not normalized:
        blockers.append("runtime lease has no explicit allowedPurposes")
    elif "*" not in normalized and "deployment-activation" not in normalized:
        blockers.append("runtime lease does not allow deployment-activation")

    expected = {
        "sourceCommit": deployment.get("sourceCommit"),
        "sourceTree": deployment.get("sourceTree"),
        "runtimeManifestDigest": deployment.get("runtimeManifestDigest"),
        "changeId": deployment.get("changeId"),
        "deploymentId": deployment.get("deploymentId"),
        "deploymentReceiptPath": str(deployment_receipt_path.resolve()),
        "deploymentReceiptSha256": deployment_receipt_sha256,
        "machine": deployment.get("machine"),
    }
    for field, value in expected.items():
        actual = summary.get(field)
        if field == "deploymentReceiptPath":
            try:
                matches = Path(str(actual or "")).resolve() == Path(str(value)).resolve()
            except OSError:
                matches = False
        else:
            matches = actual == value
        if not matches:
            blockers.append(f"runtime lease {field} does not match deployment receipt")
    return summary, list(dict.fromkeys(blockers))


def expected_battle_identity(
    deployment: Mapping[str, Any],
    lease: Mapping[str, Any],
    deployment_receipt_sha256: str,
) -> dict[str, str]:
    return {
        "sourceCommit": str(deployment.get("sourceCommit") or ""),
        "sourceTree": str(deployment.get("sourceTree") or ""),
        "runtimeManifestDigest": str(deployment.get("runtimeManifestDigest") or ""),
        "changeId": str(deployment.get("changeId") or ""),
        "deploymentId": str(deployment.get("deploymentId") or ""),
        "deploymentReceiptSha256": deployment_receipt_sha256,
        "runtimeLeaseId": str(lease.get("runtimeLeaseId") or ""),
        "runtimeAuthorizationSha256": str(lease.get("runtimeAuthorizationSha256") or ""),
        "sessionId": str(lease.get("sessionId") or ""),
        "account": str(lease.get("account") or ""),
    }


def battle_row_matches_identity(row: Mapping[str, Any], identity: Mapping[str, Any]) -> bool:
    if str(row.get("provenance_status") or "").strip().lower() == "recovered-unattributed":
        return False
    for row_field, identity_field in BATTLE_IDENTITY_FIELDS.items():
        if str(row.get(row_field) or "") != str(identity.get(identity_field) or ""):
            return False
    return _normalize_account(row.get("account")) == _normalize_account(identity.get("account"))


def deployment_battles(
    battle_rows: Sequence[Mapping[str, Any]],
    activation: Mapping[str, Any],
    *,
    decisive_only: bool = False,
) -> list[dict[str, Any]]:
    identity = activation.get("runtimeIdentity")
    if not isinstance(identity, dict):
        return []
    lease = activation.get("runtimeLeaseSnapshot")
    proof_window = lease.get("proofWindow") if isinstance(lease, dict) and isinstance(lease.get("proofWindow"), dict) else {}
    starts_at = parse_timestamp(proof_window.get("startsAt"))
    expires_at = parse_timestamp(proof_window.get("expiresAt"))
    rows: list[dict[str, Any]] = []
    for row in battle_rows:
        if not battle_row_matches_identity(row, identity):
            continue
        timestamp = parse_timestamp(row.get("timestamp") or row.get("time"))
        if timestamp is None:
            continue
        if starts_at is not None and timestamp < starts_at:
            continue
        if expires_at is not None and timestamp > expires_at:
            continue
        rows.append(dict(row))
    rows.sort(
        key=lambda row: (
            parse_timestamp(row.get("timestamp") or row.get("time")) or datetime.min.replace(tzinfo=timezone.utc),
            str(row.get("battle_id") or row.get("battle_tag") or row.get("replay_id") or ""),
        )
    )
    if decisive_only:
        rows = [row for row in rows if row.get("result") in {"win", "loss"}]
    return rows


def performance_snapshot(rows: Sequence[Mapping[str, Any]], sample_size: int = 30) -> dict[str, Any]:
    decisive = [dict(row) for row in rows if row.get("result") in {"win", "loss"}]
    if sample_size > 0:
        decisive = decisive[-sample_size:]
    wins = sum(1 for row in decisive if row.get("result") == "win")
    latest = decisive[-1] if decisive else {}
    elo = latest.get("elo_after", latest.get("elo", latest.get("rating")))
    rd = latest.get("rprd", latest.get("deviation"))
    return {
        "decisiveBattles": len(decisive),
        "wins": wins,
        "losses": len(decisive) - wins,
        "winRate": (wins / len(decisive)) if decisive else None,
        "elo": elo,
        "glickoDeviation": rd,
    }


def _activation_identity(payload: Mapping[str, Any]) -> str:
    stable = {
        key: value
        for key, value in payload.items()
        if key not in {"activationId", "receiptSha256", "activatedAt"}
    }
    return "fouler-activation-" + canonical_sha256(stable)[:32]


def build_activation_receipt(
    *,
    root: Path,
    deployment_receipt_path: Path,
    runtime_lease_path: Path,
    battle_stats_path: Path,
    baseline: Mapping[str, Any] | None = None,
    now: datetime | None = None,
) -> dict[str, Any]:
    deployment_receipt_path = deployment_receipt_path.resolve()
    runtime_lease_path = runtime_lease_path.resolve()
    deployment, deployment_blockers = deployment_receipt_blockers(
        deployment_receipt_path,
        root=root,
        verify_checkout=True,
    )
    if deployment_blockers:
        raise ValueError("deployment receipt is not activatable: " + "; ".join(deployment_blockers))
    deployment_file_sha = file_sha256(deployment_receipt_path)
    lease, lease_blockers = _lease_blockers(
        runtime_lease_path,
        deployment=deployment,
        deployment_receipt_path=deployment_receipt_path,
        deployment_receipt_sha256=deployment_file_sha,
        now=now,
    )
    if lease_blockers:
        raise ValueError("runtime lease is not activatable: " + "; ".join(lease_blockers))
    lease_snapshot_blockers: list[str] = []
    lease_snapshot = _load_regular_json(runtime_lease_path, "runtime lease", lease_snapshot_blockers)
    if lease_snapshot_blockers:
        raise ValueError("runtime lease snapshot is unavailable: " + "; ".join(lease_snapshot_blockers))
    identity = expected_battle_identity(deployment, lease, deployment_file_sha)
    matching = [row for row in read_battle_rows(battle_stats_path) if battle_row_matches_identity(row, identity)]
    proof_window = lease_snapshot.get("proofWindow") if isinstance(lease_snapshot.get("proofWindow"), dict) else {}
    starts_at = parse_timestamp(proof_window.get("startsAt"))
    expires_at = parse_timestamp(proof_window.get("expiresAt"))
    completed = []
    for row in matching:
        timestamp = parse_timestamp(row.get("timestamp") or row.get("time"))
        if row.get("result") not in {"win", "loss", "tie"} or timestamp is None:
            continue
        if starts_at is not None and timestamp < starts_at:
            continue
        if expires_at is not None and timestamp > expires_at:
            continue
        completed.append(row)
    if not completed:
        raise ValueError("no completed battle row proves the exact deployment/lease/session identity")
    observed = completed[-1]
    battle_id = str(
        observed.get("battle_id")
        or observed.get("battle_tag")
        or observed.get("replay_id")
        or ""
    ).strip()
    if not battle_id:
        raise ValueError("activation battle row has no battle id")
    if parse_timestamp(observed.get("timestamp") or observed.get("time")) is None:
        raise ValueError("activation battle row has no valid timestamp")
    payload: dict[str, Any] = {
        "schemaVersion": ACTIVATION_SCHEMA_VERSION,
        "activatedAt": (now or utc_now()).astimezone(timezone.utc).isoformat(),
        "deploymentId": deployment["deploymentId"],
        "changeId": deployment["changeId"],
        "sourceCommit": deployment["sourceCommit"],
        "sourceTree": deployment["sourceTree"],
        "runtimeManifestDigest": deployment["runtimeManifestDigest"],
        "machine": deployment["machine"],
        "releasePath": deployment["releasePath"],
        "deploymentReceiptPath": str(deployment_receipt_path),
        "deploymentReceiptSha256": deployment_file_sha,
        "runtimeLeasePath": str(runtime_lease_path),
        "runtimeLeaseSha256": file_sha256(runtime_lease_path),
        "runtimeLeaseSnapshot": lease_snapshot,
        "runtimeLeaseSnapshotSha256": canonical_sha256(lease_snapshot),
        "runtimeIdentity": identity,
        "observedBattle": {
            "battleId": battle_id,
            "timestamp": observed.get("timestamp") or observed.get("time"),
            "result": observed.get("result"),
            "rowSha256": _canonical_row_sha256(observed),
        },
        "baseline": dict(baseline or {}),
    }
    payload["activationId"] = _activation_identity(payload)
    payload["receiptSha256"] = canonical_sha256(payload)
    return payload


def activation_receipt_blockers(
    path: Path,
    *,
    verify_checkout: bool = True,
    battle_stats_path: Path | None = None,
    verify_observation: bool = False,
    now: datetime | None = None,
) -> tuple[dict[str, Any], list[str]]:
    blockers: list[str] = []
    activation = _load_regular_json(path, "activation receipt", blockers)
    if not activation:
        return {}, blockers
    _require_readonly(path, "activation receipt", blockers)
    if activation.get("schemaVersion") != ACTIVATION_SCHEMA_VERSION:
        blockers.append("activation receipt schema is unsupported")
    if activation.get("receiptSha256") != canonical_sha256(_payload_without_hash(activation)):
        blockers.append("activation receipt self-hash is invalid")
    if activation.get("activationId") != _activation_identity(activation):
        blockers.append("activationId does not match activation content")
    for field in ("sourceCommit", "sourceTree"):
        if not COMMIT_RE.fullmatch(str(activation.get(field) or "")):
            blockers.append(f"activation receipt {field} is malformed")
    if not SHA256_RE.fullmatch(str(activation.get("runtimeManifestDigest") or "")):
        blockers.append("activation receipt runtimeManifestDigest is malformed")
    for field in ("activationId", "deploymentId", "changeId"):
        if not ID_RE.fullmatch(str(activation.get(field) or "")):
            blockers.append(f"activation receipt {field} is malformed")

    deployment_path_text = str(activation.get("deploymentReceiptPath") or "")
    deployment_path = Path(deployment_path_text) if deployment_path_text else Path()
    if not deployment_path.is_absolute():
        blockers.append("activation receipt deploymentReceiptPath is not absolute")
        deployment = {}
    else:
        release_path = Path(str(activation.get("releasePath") or ""))
        deployment, deployment_blockers = deployment_receipt_blockers(
            deployment_path,
            root=release_path,
            verify_checkout=verify_checkout,
        )
        blockers.extend(f"deployment receipt: {item}" for item in deployment_blockers)
        if deployment_path.is_file() and not deployment_path.is_symlink():
            if file_sha256(deployment_path) != activation.get("deploymentReceiptSha256"):
                blockers.append("activation deployment receipt file SHA-256 does not match")

    lease_path_text = str(activation.get("runtimeLeasePath") or "")
    lease_path = Path(lease_path_text) if lease_path_text else Path()
    if not lease_path.is_absolute():
        blockers.append("activation runtimeLeasePath is not absolute")
        lease = {}
    elif deployment:
        lease_snapshot = (
            activation.get("runtimeLeaseSnapshot")
            if isinstance(activation.get("runtimeLeaseSnapshot"), dict)
            else {}
        )
        if not lease_snapshot:
            blockers.append("activation runtime lease snapshot is missing")
        elif activation.get("runtimeLeaseSnapshotSha256") != canonical_sha256(lease_snapshot):
            blockers.append("activation runtime lease snapshot hash is invalid")
        lease, lease_blockers = _lease_blockers(
            lease_path,
            deployment=deployment,
            deployment_receipt_path=deployment_path,
            deployment_receipt_sha256=str(activation.get("deploymentReceiptSha256") or ""),
            now=parse_timestamp(activation.get("activatedAt")) or now,
            lease_payload=lease_snapshot,
        )
        blockers.extend(f"runtime lease: {item}" for item in lease_blockers)
    else:
        lease = {}

    if deployment and lease:
        identity = expected_battle_identity(
            deployment,
            lease,
            str(activation.get("deploymentReceiptSha256") or ""),
        )
        if activation.get("runtimeIdentity") != identity:
            blockers.append("activation runtime identity does not match deployment and lease")
    observed = activation.get("observedBattle") if isinstance(activation.get("observedBattle"), dict) else {}
    if not str(observed.get("battleId") or "").strip():
        blockers.append("activation observed battle id is missing")
    if parse_timestamp(observed.get("timestamp")) is None:
        blockers.append("activation observed battle timestamp is malformed")
    if observed.get("result") not in {"win", "loss", "tie"}:
        blockers.append("activation observed battle result is not completed")
    if not SHA256_RE.fullmatch(str(observed.get("rowSha256") or "")):
        blockers.append("activation observed battle row SHA-256 is malformed")
    if verify_observation:
        rows = read_battle_rows(battle_stats_path or Path())
        hashes = {_canonical_row_sha256(row) for row in rows if battle_row_matches_identity(row, activation.get("runtimeIdentity", {}))}
        if observed.get("rowSha256") not in hashes:
            blockers.append("activation observed battle row is not present with exact identity")
    return activation, list(dict.fromkeys(blockers))


def _pointer_payload_without_hash(pointer: Mapping[str, Any]) -> dict[str, Any]:
    return {key: value for key, value in pointer.items() if key != "pointerSha256"}


def write_current_activation(
    activation_path: Path,
    *,
    state_root: Path | None = None,
    verify_checkout: bool = True,
    battle_stats_path: Path | None = None,
) -> Path:
    activation_path = activation_path.resolve()
    activation, blockers = activation_receipt_blockers(
        activation_path,
        verify_checkout=verify_checkout,
        battle_stats_path=battle_stats_path,
        verify_observation=battle_stats_path is not None,
    )
    if blockers:
        raise ValueError("activation cannot become current: " + "; ".join(blockers))
    expected_path = activation_receipt_path(activation["activationId"], state_root).resolve()
    if activation_path != expected_path:
        raise ValueError("activation receipt is outside the fixed activation authority directory")
    pointer = {
        "schemaVersion": ACTIVATION_POINTER_SCHEMA_VERSION,
        "updatedAt": utc_now().isoformat(),
        "activationId": activation["activationId"],
        "deploymentId": activation["deploymentId"],
        "activationReceiptPath": str(activation_path),
        "activationReceiptSha256": file_sha256(activation_path),
    }
    pointer["pointerSha256"] = canonical_sha256(pointer)
    target = current_activation_pointer_path(state_root)
    target.parent.mkdir(parents=True, exist_ok=True)
    temporary = target.with_name(f".{target.name}.{uuid.uuid4().hex}.tmp")
    try:
        temporary.write_text(json.dumps(pointer, indent=2, sort_keys=True) + "\n", encoding="utf-8")
        os.replace(temporary, target)
    finally:
        temporary.unlink(missing_ok=True)
    return target


def load_current_activation(
    *,
    state_root: Path | None = None,
    verify_checkout: bool = True,
    battle_stats_path: Path | None = None,
    verify_observation: bool = False,
) -> tuple[dict[str, Any], list[str]]:
    blockers: list[str] = []
    pointer_path = current_activation_pointer_path(state_root)
    pointer = _load_regular_json(pointer_path, "current activation pointer", blockers)
    if not pointer:
        return {}, blockers
    if pointer.get("schemaVersion") != ACTIVATION_POINTER_SCHEMA_VERSION:
        blockers.append("current activation pointer schema is unsupported")
    if pointer.get("pointerSha256") != canonical_sha256(_pointer_payload_without_hash(pointer)):
        blockers.append("current activation pointer self-hash is invalid")
    receipt_path_text = str(pointer.get("activationReceiptPath") or "")
    receipt_path = Path(receipt_path_text) if receipt_path_text else Path()
    if not receipt_path.is_absolute():
        blockers.append("current activation receipt path is not absolute")
        return {}, list(dict.fromkeys(blockers))
    activation, activation_blockers = activation_receipt_blockers(
        receipt_path,
        verify_checkout=verify_checkout,
        battle_stats_path=battle_stats_path,
        verify_observation=verify_observation,
    )
    blockers.extend(f"activation receipt: {item}" for item in activation_blockers)
    if activation:
        expected_path = activation_receipt_path(str(activation.get("activationId") or ""), state_root).resolve()
        if receipt_path.resolve() != expected_path:
            blockers.append("current activation receipt is outside the fixed activation authority directory")
    if receipt_path.is_file() and not receipt_path.is_symlink():
        if file_sha256(receipt_path) != pointer.get("activationReceiptSha256"):
            blockers.append("current activation receipt file SHA-256 does not match pointer")
    if activation:
        if pointer.get("activationId") != activation.get("activationId"):
            blockers.append("current activation id does not match pointer")
        if pointer.get("deploymentId") != activation.get("deploymentId"):
            blockers.append("current deployment id does not match pointer")
    return activation, list(dict.fromkeys(blockers))


def _judgment_identity(payload: Mapping[str, Any]) -> str:
    stable = {
        key: value
        for key, value in payload.items()
        if key not in {"judgmentId", "receiptSha256", "judgedAt"}
    }
    return "fouler-judgment-" + canonical_sha256(stable)[:32]


def _judgment_outcome(
    baseline: Mapping[str, Any],
    after: Mapping[str, Any],
    *,
    max_elo_drop: float,
    max_glicko_deviation: float,
) -> tuple[str, dict[str, Any]]:
    baseline_rate = baseline.get("winRate")
    baseline_elo = baseline.get("elo")
    after_rate = after.get("winRate")
    after_elo = after.get("elo")
    rd = after.get("glickoDeviation")
    try:
        win_rate_regressed = (
            baseline_rate is not None
            and after_rate is not None
            and float(baseline_rate) - float(after_rate) > 0.08
            and float(after_rate) <= 0.50
        )
    except (TypeError, ValueError):
        win_rate_regressed = False
    try:
        elo_regressed = (
            baseline_elo is not None
            and after_elo is not None
            and rd is not None
            and float(rd) < max_glicko_deviation
            and float(baseline_elo) - float(after_elo) > max_elo_drop
            and float(after_rate) <= 0.50
        )
    except (TypeError, ValueError):
        elo_regressed = False
    try:
        absolute_floor_failed = after_rate is None or float(after_rate) < MINIMUM_NO_BASELINE_WIN_RATE
    except (TypeError, ValueError):
        absolute_floor_failed = True
    if win_rate_regressed or elo_regressed or absolute_floor_failed:
        status = "regressed"
    elif baseline_rate is None and baseline_elo is None:
        status = "passed-no-baseline"
    else:
        status = "passed"
    return status, {
        "winRateRegressed": win_rate_regressed,
        "eloRegressed": elo_regressed,
        "absoluteWinRateFloorFailed": absolute_floor_failed,
        "minimumNoBaselineWinRate": MINIMUM_NO_BASELINE_WIN_RATE,
        "maxEloDrop": max_elo_drop,
        "maxGlickoDeviation": max_glicko_deviation,
    }


def build_judgment_receipt(
    *,
    activation: Mapping[str, Any],
    battle_rows: Sequence[Mapping[str, Any]],
    min_battles: int = 30,
    max_elo_drop: float = 50.0,
    max_glicko_deviation: float = 50.0,
    now: datetime | None = None,
) -> dict[str, Any]:
    if min_battles < MINIMUM_JUDGMENT_BATTLES:
        raise ValueError(f"min_battles must be at least {MINIMUM_JUDGMENT_BATTLES}")
    rows = deployment_battles(battle_rows, activation, decisive_only=True)
    battle_ids = [
        str(row.get("battle_id") or row.get("battle_tag") or row.get("replay_id") or "").strip()
        for row in rows
    ]
    if any(not battle_id for battle_id in battle_ids) or len(battle_ids) != len(set(battle_ids)):
        raise ValueError("exact-identity judgment battles contain missing or duplicate battle ids")
    if len(rows) < min_battles:
        raise ValueError(f"only {len(rows)}/{min_battles} exact-identity decisive battles are available")
    judged_rows = rows[:min_battles]
    after = performance_snapshot(judged_rows, sample_size=min_battles)
    baseline = activation.get("baseline") if isinstance(activation.get("baseline"), dict) else {}
    status, signals = _judgment_outcome(
        baseline,
        after,
        max_elo_drop=max_elo_drop,
        max_glicko_deviation=max_glicko_deviation,
    )
    evidence = [
        {
            "battleId": str(row.get("battle_id") or row.get("battle_tag") or row.get("replay_id") or ""),
            "rowSha256": _canonical_row_sha256(row),
        }
        for row in judged_rows
    ]
    payload: dict[str, Any] = {
        "schemaVersion": JUDGMENT_SCHEMA_VERSION,
        "judgedAt": (now or utc_now()).astimezone(timezone.utc).isoformat(),
        "activationId": activation.get("activationId"),
        "deploymentId": activation.get("deploymentId"),
        "changeId": activation.get("changeId"),
        "sourceCommit": activation.get("sourceCommit"),
        "status": status,
        "minimumBattles": min_battles,
        "baseline": dict(baseline),
        "postActivation": after,
        "signals": signals,
        "battleEvidence": evidence,
        "battleEvidenceSha256": canonical_sha256({"rows": evidence}),
    }
    payload["judgmentId"] = _judgment_identity(payload)
    payload["receiptSha256"] = canonical_sha256(payload)
    return payload


def judgment_receipt_blockers(
    path: Path,
    *,
    activation: Mapping[str, Any],
    battle_rows: Sequence[Mapping[str, Any]] | None = None,
) -> tuple[dict[str, Any], list[str]]:
    blockers: list[str] = []
    judgment = _load_regular_json(path, "judgment receipt", blockers)
    if not judgment:
        return {}, blockers
    _require_readonly(path, "judgment receipt", blockers)
    if judgment.get("schemaVersion") != JUDGMENT_SCHEMA_VERSION:
        blockers.append("judgment receipt schema is unsupported")
    if judgment.get("receiptSha256") != canonical_sha256(_payload_without_hash(judgment)):
        blockers.append("judgment receipt self-hash is invalid")
    if judgment.get("judgmentId") != _judgment_identity(judgment):
        blockers.append("judgmentId does not match judgment content")
    for field in ("activationId", "deploymentId", "changeId", "sourceCommit"):
        if judgment.get(field) != activation.get(field):
            blockers.append(f"judgment {field} does not match activation")
    if judgment.get("status") not in FINAL_PASS_STATUSES | {"regressed"}:
        blockers.append("judgment status is unsupported")
    baseline = activation.get("baseline") if isinstance(activation.get("baseline"), dict) else {}
    if judgment.get("baseline") != baseline:
        blockers.append("judgment baseline does not match activation")
    signals = judgment.get("signals") if isinstance(judgment.get("signals"), dict) else {}
    try:
        max_elo_drop = float(signals.get("maxEloDrop"))
        max_glicko_deviation = float(signals.get("maxGlickoDeviation"))
    except (TypeError, ValueError):
        max_elo_drop = -1.0
        max_glicko_deviation = -1.0
    if not 0 < max_elo_drop <= 50.0:
        blockers.append("judgment maxEloDrop is outside policy")
    if not 0 < max_glicko_deviation <= 50.0:
        blockers.append("judgment maxGlickoDeviation is outside policy")
    if signals.get("minimumNoBaselineWinRate") != MINIMUM_NO_BASELINE_WIN_RATE:
        blockers.append("judgment no-baseline win-rate floor is outside policy")
    evidence = judgment.get("battleEvidence")
    if not isinstance(evidence, list) or not evidence:
        blockers.append("judgment battle evidence is missing")
        evidence = []
    evidence_ids = [
        str(item.get("battleId") or "")
        for item in evidence
        if isinstance(item, dict)
    ]
    evidence_hashes = [
        str(item.get("rowSha256") or "")
        for item in evidence
        if isinstance(item, dict)
    ]
    if (
        len(evidence_ids) != len(evidence)
        or any(not value for value in evidence_ids)
        or len(evidence_ids) != len(set(evidence_ids))
        or len(evidence_hashes) != len(set(evidence_hashes))
    ):
        blockers.append("judgment battle evidence contains missing or duplicate identities")
    if judgment.get("battleEvidenceSha256") != canonical_sha256({"rows": evidence}):
        blockers.append("judgment battle evidence hash is invalid")
    minimum = judgment.get("minimumBattles")
    if (
        not isinstance(minimum, int)
        or minimum < MINIMUM_JUDGMENT_BATTLES
        or len(evidence) != minimum
    ):
        blockers.append("judgment battle evidence count does not match minimumBattles")
    if battle_rows is not None:
        exact = deployment_battles(battle_rows, activation, decisive_only=True)
        available = {_canonical_row_sha256(row): row for row in exact}
        missing = [
            item
            for item in evidence
            if not isinstance(item, dict) or item.get("rowSha256") not in available
        ]
        if missing:
            blockers.append("judgment includes battle evidence outside the exact deployment identity")
        else:
            evidence_rows = [available[str(item["rowSha256"])] for item in evidence]
            for item, row in zip(evidence, evidence_rows):
                actual_id = str(
                    row.get("battle_id") or row.get("battle_tag") or row.get("replay_id") or ""
                )
                if item.get("battleId") != actual_id:
                    blockers.append("judgment battle id does not match its row evidence")
            recomputed_after = performance_snapshot(evidence_rows, sample_size=len(evidence_rows))
            recomputed_status, recomputed_signals = _judgment_outcome(
                baseline,
                recomputed_after,
                max_elo_drop=max_elo_drop,
                max_glicko_deviation=max_glicko_deviation,
            )
            if judgment.get("postActivation") != recomputed_after:
                blockers.append("judgment post-activation metrics do not match battle evidence")
            if judgment.get("status") != recomputed_status:
                blockers.append("judgment status does not match battle evidence")
            if signals != recomputed_signals:
                blockers.append("judgment signals do not match battle evidence")
    return judgment, list(dict.fromkeys(blockers))


def current_deployment_context(
    *,
    battle_stats_path: Path,
    state_root: Path | None = None,
    verify_checkout: bool = True,
    expected_runtime_identity: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    activation, blockers = load_current_activation(
        state_root=state_root,
        verify_checkout=verify_checkout,
        battle_stats_path=battle_stats_path,
        verify_observation=True,
    )
    rows = read_battle_rows(battle_stats_path)
    exact = deployment_battles(rows, activation, decisive_only=True) if activation else []
    judgment: dict[str, Any] = {}
    judgment_blockers: list[str] = []
    judgment_path: Path | None = None
    if activation:
        current_identity = (
            activation.get("runtimeIdentity")
            if isinstance(activation.get("runtimeIdentity"), dict)
            else {}
        )
        for field, value in (expected_runtime_identity or {}).items():
            expected = str(value or "").strip()
            if expected and str(current_identity.get(field) or "") != expected:
                blockers.append(f"current activation {field} does not match the running runtime identity")
        judgment_path = judgment_receipt_path(str(activation.get("activationId") or ""), state_root)
        judgment, judgment_blockers = judgment_receipt_blockers(
            judgment_path,
            activation=activation,
            battle_rows=rows,
        )
        blockers.extend(f"judgment receipt: {item}" for item in judgment_blockers)
    status = str(judgment.get("status") or "missing")
    return {
        "ok": bool(activation) and not blockers,
        "readyForImprovement": bool(activation) and not blockers and status in FINAL_PASS_STATUSES,
        "activation": activation,
        "judgment": judgment,
        "judgmentStatus": status,
        "judgmentPath": str(judgment_path) if judgment_path else None,
        "gamesSinceActivation": len(exact),
        "blockers": list(dict.fromkeys(blockers)),
    }


__all__ = [
    "ACTIVATION_SCHEMA_VERSION",
    "FINAL_PASS_STATUSES",
    "JUDGMENT_SCHEMA_VERSION",
    "activation_receipt_blockers",
    "activation_receipt_path",
    "battle_row_matches_identity",
    "build_activation_receipt",
    "build_judgment_receipt",
    "current_activation_pointer_path",
    "current_deployment_context",
    "default_state_root",
    "deployment_battles",
    "judgment_receipt_blockers",
    "judgment_receipt_path",
    "load_current_activation",
    "performance_snapshot",
    "read_battle_rows",
    "write_current_activation",
    "write_immutable_receipt",
]
