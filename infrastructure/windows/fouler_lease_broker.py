#!/usr/bin/env python3
"""Append-only consumption broker for bounded Fouler runtime leases.

The broker is the only runtime writer of its protected SQLite database. Runtime
processes can reserve capacity, claim a reservation using their kernel-derived
process identity, and complete it. Capacity is never returned, including after
failed or abandoned work.
"""

from __future__ import annotations

import argparse
import ctypes
import hashlib
import hmac
import json
import os
import re
import secrets
import sqlite3
import stat
import sys
import threading
import time
import uuid
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Mapping


ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from infrastructure.runtime_lease_client import (  # noqa: E402
    MAX_FRAME_BYTES,
    PIPE_NAME,
    PROTOCOL_VERSION,
    RESPONSE_VERSION,
    IMPROVE_RESERVATION_PURPOSE,
    RUNTIME_RESERVATION_PURPOSE,
    DuplicateJSONKeyError,
    ProtocolError,
    canonical_json_bytes,
    encode_frame,
    read_framed,
    request_digest,
    strict_json_loads,
    _overlapped_transfer,
)


STORE_SCHEMA_VERSION = "fouler-lease-consumption-store/v2"
MARKER_SCHEMA_VERSION = "fouler-lease-consumption-marker/v1"
REGISTRATION_SCHEMA_VERSION = "fouler-lease-broker-registration/v1"
DEFAULT_ROOT = (
    Path(os.environ.get("PROGRAMDATA", r"C:\ProgramData"))
    / "HERMES-LeaseBroker"
    / "fouler"
)
DEFAULT_STORE_PATH = DEFAULT_ROOT / "consumption.sqlite3"
DEFAULT_MARKER_PATH = DEFAULT_ROOT / "consumption.sqlite3.initialized"

_HASH_RE = re.compile(r"^[0-9a-f]{64}$")
_GIT_HASH_RE = re.compile(r"^[0-9a-f]{40,64}$")
_ID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.:-]{7,127}$")
_REQUEST_ID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.:-]{7,191}$")
_RESERVATION_ID_RE = re.compile(r"^res-[0-9a-f]{32}$")
_LAUNCH_NONCE_RE = re.compile(r"^[0-9a-f]{64}$")
_SID_RE = re.compile(r"^S-\d+(?:-\d+)+$")
_FILETIME_EPOCH_OFFSET = 116_444_736_000_000_000
_RESERVATION_KINDS = frozenset({"runtime", "improve"})
_STATUS_LOOKUP_TYPES = frozenset({"request", "reservation"})

_REGISTRATION_FIELDS = frozenset(
    {
        "schemaVersion",
        "leaseId",
        "authorizationDigest",
        "sourceCommit",
        "sourceTree",
        "changeId",
        "deploymentId",
        "sessionId",
        "runtimeManifestDigest",
        "deploymentReceiptSha256",
        "account",
        "hostName",
        "hostIdSha256",
        "proofStartsAt",
        "proofExpiresAt",
        "maxRunCount",
        "maxCycles",
        "maxConcurrentBattles",
        "improveAuthorized",
    }
)
_COMMON_REQUEST_FIELDS = frozenset(
    {"schemaVersion", "action", "requestId", "authorizationDigest", "leaseId"}
)
_ACTION_FIELDS = {
    "reserve-runtime": _COMMON_REQUEST_FIELDS
    | {
        "purpose",
        "kind",
        "battleCount",
        "cycleCount",
        "maxConcurrentBattles",
        "supervisorInstanceId",
    },
    "reserve-improve": _COMMON_REQUEST_FIELDS
    | {
        "purpose",
        "kind",
        "battleCount",
        "cycleCount",
        "maxConcurrentBattles",
        "supervisorInstanceId",
    },
    "claim": _COMMON_REQUEST_FIELDS
    | {
        "reservationId",
        "purpose",
        "kind",
        "battleCount",
        "cycleCount",
        "maxConcurrentBattles",
        "supervisorProcessId",
        "supervisorProcessCreationFiletime",
        "supervisorInstanceId",
        "launchNonce",
    },
    "complete": _COMMON_REQUEST_FIELDS
    | {
        "reservationId",
        "purpose",
        "kind",
        "battleCount",
        "cycleCount",
        "maxConcurrentBattles",
        "supervisorProcessId",
        "supervisorProcessCreationFiletime",
        "supervisorInstanceId",
        "launchNonce",
        "outcome",
    },
    "status": _COMMON_REQUEST_FIELDS | {"lookupType", "lookupId"},
}
_RUNTIME_OUTCOMES = frozenset({"completed", "failed", "aborted"})
_IMPROVE_OUTCOMES = frozenset(
    {"accepted", "rejected", "failed", "no-change", "aborted"}
)


class BrokerError(RuntimeError):
    """A stable broker-domain error suitable for a protocol response."""

    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code


class StoreUnavailable(BrokerError):
    def __init__(self, message: str = "lease consumption store is unavailable") -> None:
        super().__init__("store_unavailable", message)


@dataclass(frozen=True)
class CallerIdentity:
    process_id: int
    process_creation_filetime: int

    def validated(self) -> "CallerIdentity":
        if type(self.process_id) is not int or self.process_id <= 0:
            raise BrokerError("caller_identity_invalid", "caller PID is invalid")
        if (
            type(self.process_creation_filetime) is not int
            or self.process_creation_filetime <= 0
        ):
            raise BrokerError(
                "caller_identity_invalid", "caller process creation FILETIME is invalid"
            )
        return self


def utc_filetime() -> int:
    return time.time_ns() // 100 + _FILETIME_EPOCH_OFFSET


def _timestamp_to_filetime(value: object, label: str) -> int:
    if not isinstance(value, str) or not value or value != value.strip():
        raise BrokerError("registration_invalid", f"{label} must be an RFC3339 timestamp")
    text = value[:-1] + "+00:00" if value.endswith("Z") else value
    try:
        parsed = datetime.fromisoformat(text)
    except ValueError as exc:
        raise BrokerError(
            "registration_invalid", f"{label} must be an RFC3339 timestamp"
        ) from exc
    if parsed.tzinfo is None:
        raise BrokerError("registration_invalid", f"{label} must include a timezone")
    utc = parsed.astimezone(timezone.utc)
    unix_seconds = int(utc.timestamp())
    remainder = utc.microsecond * 10
    result = unix_seconds * 10_000_000 + remainder + _FILETIME_EPOCH_OFFSET
    if result <= 0:
        raise BrokerError("registration_invalid", f"{label} is out of range")
    return result


def _required_text(
    value: object, label: str, *, maximum: int = 256, pattern: re.Pattern[str] | None = None
) -> str:
    if not isinstance(value, str) or value != value.strip() or not (1 <= len(value) <= maximum):
        raise BrokerError("registration_invalid", f"{label} is missing or malformed")
    if any(ord(character) < 32 or ord(character) == 127 for character in value):
        raise BrokerError("registration_invalid", f"{label} contains control characters")
    if pattern is not None and not pattern.fullmatch(value):
        raise BrokerError("registration_invalid", f"{label} is missing or malformed")
    return value


def _positive_integer(value: object, label: str, maximum: int) -> int:
    if type(value) is not int or value <= 0 or value > maximum:
        raise BrokerError(
            "registration_invalid", f"{label} must be an integer between 1 and {maximum}"
        )
    return value


@dataclass(frozen=True)
class LeaseRegistration:
    lease_id: str
    authorization_digest: str
    source_commit: str
    source_tree: str
    change_id: str
    deployment_id: str
    session_id: str
    runtime_manifest_digest: str
    deployment_receipt_sha256: str
    account: str
    host_name: str
    host_id_sha256: str
    proof_starts_at: str
    proof_expires_at: str
    proof_starts_filetime: int
    proof_expires_filetime: int
    max_run_count: int
    max_cycles: int
    max_concurrent_battles: int
    improve_authorized: bool
    canonical_json: str

    @classmethod
    def from_mapping(cls, payload: Mapping[str, Any]) -> "LeaseRegistration":
        if not isinstance(payload, Mapping):
            raise BrokerError("registration_invalid", "registration must be a JSON object")
        data = dict(payload)
        if set(data) != _REGISTRATION_FIELDS:
            raise BrokerError(
                "registration_invalid", "registration contains missing or unknown fields"
            )
        if data.get("schemaVersion") != REGISTRATION_SCHEMA_VERSION:
            raise BrokerError("registration_invalid", "registration schema is unsupported")
        authorization_digest = _required_text(
            data.get("authorizationDigest"),
            "authorizationDigest",
            maximum=64,
            pattern=_HASH_RE,
        )
        source_commit = _required_text(
            data.get("sourceCommit"), "sourceCommit", maximum=64, pattern=_GIT_HASH_RE
        )
        source_tree = _required_text(
            data.get("sourceTree"), "sourceTree", maximum=64, pattern=_GIT_HASH_RE
        )
        starts = _required_text(data.get("proofStartsAt"), "proofStartsAt", maximum=64)
        expires = _required_text(data.get("proofExpiresAt"), "proofExpiresAt", maximum=64)
        starts_filetime = _timestamp_to_filetime(starts, "proofStartsAt")
        expires_filetime = _timestamp_to_filetime(expires, "proofExpiresAt")
        if starts_filetime >= expires_filetime:
            raise BrokerError(
                "registration_invalid", "proofExpiresAt must be later than proofStartsAt"
            )
        if type(data.get("improveAuthorized")) is not bool:
            raise BrokerError(
                "registration_invalid", "improveAuthorized must be a JSON boolean"
            )
        normalized = {
            **data,
            "authorizationDigest": authorization_digest,
            "sourceCommit": source_commit,
            "sourceTree": source_tree,
        }
        return cls(
            lease_id=_required_text(
                data.get("leaseId"), "leaseId", maximum=128, pattern=_ID_RE
            ),
            authorization_digest=authorization_digest,
            source_commit=source_commit,
            source_tree=source_tree,
            change_id=_required_text(
                data.get("changeId"), "changeId", maximum=128, pattern=_ID_RE
            ),
            deployment_id=_required_text(
                data.get("deploymentId"), "deploymentId", maximum=128, pattern=_ID_RE
            ),
            session_id=_required_text(
                data.get("sessionId"), "sessionId", maximum=128, pattern=_ID_RE
            ),
            runtime_manifest_digest=_required_text(
                data.get("runtimeManifestDigest"),
                "runtimeManifestDigest",
                maximum=64,
                pattern=_HASH_RE,
            ),
            deployment_receipt_sha256=_required_text(
                data.get("deploymentReceiptSha256"),
                "deploymentReceiptSha256",
                maximum=64,
                pattern=_HASH_RE,
            ),
            account=_required_text(data.get("account"), "account", maximum=128),
            host_name=_required_text(data.get("hostName"), "hostName", maximum=255),
            host_id_sha256=_required_text(
                data.get("hostIdSha256"),
                "hostIdSha256",
                maximum=64,
                pattern=_HASH_RE,
            ),
            proof_starts_at=starts,
            proof_expires_at=expires,
            proof_starts_filetime=starts_filetime,
            proof_expires_filetime=expires_filetime,
            max_run_count=_positive_integer(
                data.get("maxRunCount"), "maxRunCount", 1_000_000
            ),
            max_cycles=_positive_integer(data.get("maxCycles"), "maxCycles", 100_000),
            max_concurrent_battles=_positive_integer(
                data.get("maxConcurrentBattles"), "maxConcurrentBattles", 3
            ),
            improve_authorized=bool(data["improveAuthorized"]),
            canonical_json=canonical_json_bytes(normalized).decode("ascii"),
        )


_SCHEMA_SQL = """
CREATE TABLE broker_meta (
    key TEXT PRIMARY KEY,
    value TEXT NOT NULL
);

CREATE TABLE leases (
    authorization_digest TEXT PRIMARY KEY CHECK(length(authorization_digest) = 64),
    lease_id TEXT NOT NULL UNIQUE,
    source_commit TEXT NOT NULL,
    source_tree TEXT NOT NULL,
    change_id TEXT NOT NULL,
    deployment_id TEXT NOT NULL,
    session_id TEXT NOT NULL,
    runtime_manifest_digest TEXT NOT NULL,
    deployment_receipt_sha256 TEXT NOT NULL,
    account TEXT NOT NULL,
    host_name TEXT NOT NULL,
    host_id_sha256 TEXT NOT NULL,
    proof_starts_at TEXT NOT NULL,
    proof_expires_at TEXT NOT NULL,
    proof_starts_filetime INTEGER NOT NULL CHECK(proof_starts_filetime > 0),
    proof_expires_filetime INTEGER NOT NULL CHECK(proof_expires_filetime > proof_starts_filetime),
    max_run_count INTEGER NOT NULL CHECK(max_run_count > 0),
    max_cycles INTEGER NOT NULL CHECK(max_cycles > 0),
    max_concurrent_battles INTEGER NOT NULL CHECK(max_concurrent_battles BETWEEN 1 AND 3),
    improve_authorized INTEGER NOT NULL CHECK(improve_authorized IN (0, 1)),
    registration_json TEXT NOT NULL,
    registered_at_filetime INTEGER NOT NULL CHECK(registered_at_filetime > 0)
);

CREATE TABLE reservations (
    reservation_id TEXT PRIMARY KEY,
    authorization_digest TEXT NOT NULL REFERENCES leases(authorization_digest),
    reserve_request_id TEXT NOT NULL,
    kind TEXT NOT NULL CHECK(kind IN ('runtime', 'improve')),
    purpose TEXT NOT NULL,
    battle_count INTEGER NOT NULL CHECK(battle_count >= 0),
    cycle_count INTEGER NOT NULL CHECK(cycle_count >= 0),
    max_concurrent_battles INTEGER NOT NULL CHECK(max_concurrent_battles >= 0),
    supervisor_pid INTEGER NOT NULL CHECK(supervisor_pid > 0),
    supervisor_process_creation_filetime INTEGER NOT NULL
        CHECK(supervisor_process_creation_filetime > 0),
    supervisor_instance_id TEXT NOT NULL,
    launch_nonce_sha256 TEXT NOT NULL CHECK(length(launch_nonce_sha256) = 64),
    state TEXT NOT NULL CHECK(state IN ('reserved', 'claimed', 'completed')),
    reserved_at_filetime INTEGER NOT NULL CHECK(reserved_at_filetime > 0),
    claimed_pid INTEGER,
    claimed_process_creation_filetime INTEGER,
    claim_request_id TEXT,
    claimed_at_filetime INTEGER,
    completion_request_id TEXT,
    completed_at_filetime INTEGER,
    outcome TEXT,
    completion_actor TEXT CHECK(
        completion_actor IS NULL OR completion_actor IN ('claimant', 'supervisor', 'administrator')
    ),
    completion_actor_pid INTEGER,
    completion_actor_process_creation_filetime INTEGER,
    administrator_reason TEXT,
    UNIQUE(authorization_digest, reserve_request_id),
    CHECK(
        (kind = 'runtime' AND purpose = 'run-py-battle-runner'
         AND battle_count > 0 AND cycle_count > 0 AND max_concurrent_battles > 0)
        OR
        (kind = 'improve' AND purpose = 'deku-control-plane-improvement'
         AND battle_count = 0 AND cycle_count = 0 AND max_concurrent_battles = 0)
    ),
    CHECK(
        (state = 'reserved'
         AND claimed_pid IS NULL
         AND claimed_process_creation_filetime IS NULL
         AND claim_request_id IS NULL
         AND claimed_at_filetime IS NULL
         AND completion_request_id IS NULL
         AND completed_at_filetime IS NULL
         AND outcome IS NULL
         AND completion_actor IS NULL
         AND completion_actor_pid IS NULL
         AND completion_actor_process_creation_filetime IS NULL
         AND administrator_reason IS NULL)
        OR
        (state = 'claimed'
         AND claimed_pid > 0
         AND claimed_process_creation_filetime > 0
         AND claim_request_id IS NOT NULL
         AND claimed_at_filetime > reserved_at_filetime
         AND completion_request_id IS NULL
         AND completed_at_filetime IS NULL
         AND outcome IS NULL
         AND completion_actor IS NULL
         AND completion_actor_pid IS NULL
         AND completion_actor_process_creation_filetime IS NULL
         AND administrator_reason IS NULL)
        OR
        (state = 'completed'
         AND completion_request_id IS NOT NULL
         AND completed_at_filetime > reserved_at_filetime
         AND outcome IS NOT NULL
         AND completion_actor IS NOT NULL
         AND (
             (completion_actor = 'claimant'
              AND claimed_pid > 0
              AND claimed_process_creation_filetime > 0
              AND claim_request_id IS NOT NULL
              AND claimed_at_filetime > reserved_at_filetime
              AND completion_actor_pid = claimed_pid
              AND completion_actor_process_creation_filetime = claimed_process_creation_filetime
              AND administrator_reason IS NULL)
             OR
             (completion_actor = 'supervisor'
              AND completion_actor_pid = supervisor_pid
              AND completion_actor_process_creation_filetime = supervisor_process_creation_filetime
              AND administrator_reason IS NULL)
             OR
             (completion_actor = 'administrator'
              AND completion_actor_pid IS NULL
              AND completion_actor_process_creation_filetime IS NULL
              AND administrator_reason IS NOT NULL)
         ))
    )
);

CREATE UNIQUE INDEX one_improve_reservation_per_authorization
ON reservations(authorization_digest) WHERE kind = 'improve';

CREATE UNIQUE INDEX one_unresolved_reservation_per_authorization
ON reservations(authorization_digest) WHERE state IN ('reserved', 'claimed');

CREATE TABLE request_journal (
    authorization_digest TEXT NOT NULL REFERENCES leases(authorization_digest),
    request_id TEXT NOT NULL,
    action TEXT NOT NULL,
    request_sha256 TEXT NOT NULL CHECK(length(request_sha256) = 64),
    caller_pid INTEGER NOT NULL CHECK(caller_pid > 0),
    caller_process_creation_filetime INTEGER NOT NULL CHECK(caller_process_creation_filetime > 0),
    response_json TEXT NOT NULL,
    created_at_filetime INTEGER NOT NULL CHECK(created_at_filetime > 0),
    PRIMARY KEY(authorization_digest, request_id)
);

CREATE TRIGGER broker_meta_immutable
BEFORE UPDATE ON broker_meta BEGIN SELECT RAISE(ABORT, 'broker metadata is immutable'); END;
CREATE TRIGGER broker_meta_no_delete
BEFORE DELETE ON broker_meta BEGIN SELECT RAISE(ABORT, 'broker metadata cannot be deleted'); END;
CREATE TRIGGER leases_immutable
BEFORE UPDATE ON leases BEGIN SELECT RAISE(ABORT, 'lease identity and bounds are immutable'); END;
CREATE TRIGGER leases_no_delete
BEFORE DELETE ON leases BEGIN SELECT RAISE(ABORT, 'leases cannot be deleted'); END;
CREATE TRIGGER request_journal_immutable
BEFORE UPDATE ON request_journal BEGIN SELECT RAISE(ABORT, 'request journal is immutable'); END;
CREATE TRIGGER request_journal_no_delete
BEFORE DELETE ON request_journal BEGIN SELECT RAISE(ABORT, 'request journal cannot be deleted'); END;
CREATE TRIGGER reservations_insert_reserved_only
BEFORE INSERT ON reservations
WHEN NEW.state <> 'reserved'
BEGIN SELECT RAISE(ABORT, 'reservations must begin reserved'); END;
CREATE TRIGGER reservations_no_delete
BEFORE DELETE ON reservations BEGIN SELECT RAISE(ABORT, 'reservations cannot be deleted'); END;
CREATE TRIGGER reservations_forward_only
BEFORE UPDATE ON reservations
BEGIN
    SELECT CASE WHEN
        OLD.reservation_id IS NOT NEW.reservation_id OR
        OLD.authorization_digest IS NOT NEW.authorization_digest OR
        OLD.reserve_request_id IS NOT NEW.reserve_request_id OR
        OLD.kind IS NOT NEW.kind OR
        OLD.purpose IS NOT NEW.purpose OR
        OLD.battle_count IS NOT NEW.battle_count OR
        OLD.cycle_count IS NOT NEW.cycle_count OR
        OLD.max_concurrent_battles IS NOT NEW.max_concurrent_battles OR
        OLD.supervisor_pid IS NOT NEW.supervisor_pid OR
        OLD.supervisor_process_creation_filetime IS NOT NEW.supervisor_process_creation_filetime OR
        OLD.supervisor_instance_id IS NOT NEW.supervisor_instance_id OR
        OLD.launch_nonce_sha256 IS NOT NEW.launch_nonce_sha256 OR
        OLD.reserved_at_filetime IS NOT NEW.reserved_at_filetime
    THEN RAISE(ABORT, 'reservation identity and bounds are immutable') END;
    SELECT CASE WHEN NOT (
        (OLD.state = 'reserved' AND NEW.state = 'claimed'
         AND NEW.claimed_pid > 0
         AND NEW.claimed_process_creation_filetime > 0
         AND NEW.claim_request_id IS NOT NULL
         AND NEW.claimed_at_filetime > OLD.reserved_at_filetime
         AND NEW.completion_request_id IS NULL
         AND NEW.completed_at_filetime IS NULL
         AND NEW.outcome IS NULL
         AND NEW.completion_actor IS NULL
         AND NEW.completion_actor_pid IS NULL
         AND NEW.completion_actor_process_creation_filetime IS NULL
         AND NEW.administrator_reason IS NULL)
        OR
        (OLD.state = 'claimed' AND NEW.state = 'completed'
         AND NEW.claimed_pid IS OLD.claimed_pid
         AND NEW.claimed_process_creation_filetime IS OLD.claimed_process_creation_filetime
         AND NEW.claim_request_id IS OLD.claim_request_id
         AND NEW.claimed_at_filetime IS OLD.claimed_at_filetime
         AND NEW.completion_request_id IS NOT NULL
         AND NEW.completed_at_filetime > OLD.claimed_at_filetime
         AND NEW.outcome IS NOT NULL
         AND NEW.completion_actor IN ('claimant', 'supervisor', 'administrator'))
        OR
        (OLD.state = 'reserved' AND NEW.state = 'completed'
         AND NEW.claimed_pid IS NULL
         AND NEW.claimed_process_creation_filetime IS NULL
         AND NEW.claim_request_id IS NULL
         AND NEW.claimed_at_filetime IS NULL
         AND NEW.completion_request_id IS NOT NULL
         AND NEW.completed_at_filetime > OLD.reserved_at_filetime
         AND NEW.outcome IS NOT NULL
         AND NEW.completion_actor IN ('supervisor', 'administrator'))
    ) THEN RAISE(ABORT, 'reservation transition must move forward exactly once') END;
END;
"""

_REQUIRED_TABLES = frozenset({"broker_meta", "leases", "reservations", "request_journal"})
_REQUIRED_TRIGGERS = frozenset(
    {
        "broker_meta_immutable",
        "broker_meta_no_delete",
        "leases_immutable",
        "leases_no_delete",
        "request_journal_immutable",
        "request_journal_no_delete",
        "reservations_insert_reserved_only",
        "reservations_no_delete",
        "reservations_forward_only",
    }
)


def _strict_file(path: Path, label: str, maximum: int) -> bytes:
    try:
        metadata = os.lstat(path)
    except OSError as exc:
        raise StoreUnavailable(f"{label} is missing or inaccessible") from exc
    if stat.S_ISLNK(metadata.st_mode) or not stat.S_ISREG(metadata.st_mode):
        raise StoreUnavailable(f"{label} is not a regular non-symlink file")
    if metadata.st_size > maximum:
        raise StoreUnavailable(f"{label} exceeds its size limit")
    try:
        content = path.read_bytes()
    except OSError as exc:
        raise StoreUnavailable(f"{label} is unreadable") from exc
    if len(content) > maximum:
        raise StoreUnavailable(f"{label} exceeds its size limit")
    return content


def _write_exclusive_durable(path: Path, content: bytes) -> None:
    flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_BINARY", 0)
    descriptor = os.open(path, flags, 0o600)
    try:
        with os.fdopen(descriptor, "wb", closefd=False) as handle:
            handle.write(content)
            handle.flush()
            os.fsync(descriptor)
    finally:
        os.close(descriptor)


class ConsumptionStore:
    def __init__(
        self,
        path: str | os.PathLike[str] = DEFAULT_STORE_PATH,
        marker_path: str | os.PathLike[str] = DEFAULT_MARKER_PATH,
        *,
        busy_timeout_ms: int = 30_000,
    ) -> None:
        self.path = Path(path).absolute()
        self.marker_path = Path(marker_path).absolute()
        self.busy_timeout_ms = int(busy_timeout_ms)
        if self.path == self.marker_path:
            raise ValueError("store and initialization marker paths must differ")
        if not (1 <= self.busy_timeout_ms <= 120_000):
            raise ValueError("busy_timeout_ms must be between 1 and 120000")

    @classmethod
    def initialize(
        cls,
        path: str | os.PathLike[str] = DEFAULT_STORE_PATH,
        marker_path: str | os.PathLike[str] = DEFAULT_MARKER_PATH,
    ) -> "ConsumptionStore":
        store = cls(path, marker_path)
        store.path.parent.mkdir(parents=True, exist_ok=True)
        if store.marker_path.parent != store.path.parent:
            store.marker_path.parent.mkdir(parents=True, exist_ok=True)
        database_exists = os.path.lexists(store.path)
        marker_exists = os.path.lexists(store.marker_path)
        if database_exists or marker_exists:
            if not (database_exists and marker_exists):
                raise StoreUnavailable(
                    "store and initialization marker must either both exist or both be absent"
                )
            store.validate()
            return store

        store_id = str(uuid.uuid4())
        connection: sqlite3.Connection | None = None
        try:
            connection = sqlite3.connect(str(store.path), isolation_level=None)
            store._configure(connection, allow_initialize=True)
            connection.execute("BEGIN IMMEDIATE")
            connection.executescript(_SCHEMA_SQL)
            connection.execute(
                "INSERT INTO broker_meta(key, value) VALUES (?, ?)",
                ("schema_version", STORE_SCHEMA_VERSION),
            )
            connection.execute(
                "INSERT INTO broker_meta(key, value) VALUES (?, ?)", ("store_id", store_id)
            )
            connection.commit()
            connection.execute("PRAGMA wal_checkpoint(FULL)")
        except (OSError, sqlite3.Error) as exc:
            if connection is not None:
                try:
                    connection.rollback()
                except sqlite3.Error:
                    pass
            raise StoreUnavailable("store initialization failed closed") from exc
        finally:
            if connection is not None:
                connection.close()

        marker = {
            "schemaVersion": MARKER_SCHEMA_VERSION,
            "storeId": store_id,
            "storeSchemaVersion": STORE_SCHEMA_VERSION,
        }
        try:
            _write_exclusive_durable(
                store.marker_path, canonical_json_bytes(marker) + b"\n"
            )
        except OSError as exc:
            raise StoreUnavailable(
                "initialization marker publication failed; store requires administrator recovery"
            ) from exc
        store.validate()
        return store

    def _marker(self) -> dict[str, str]:
        content = _strict_file(self.marker_path, "initialization marker", 16 * 1024)
        try:
            payload = strict_json_loads(content)
        except (DuplicateJSONKeyError, ProtocolError, ValueError) as exc:
            raise StoreUnavailable("initialization marker is corrupt") from exc
        expected_fields = {"schemaVersion", "storeId", "storeSchemaVersion"}
        if not isinstance(payload, dict) or set(payload) != expected_fields:
            raise StoreUnavailable("initialization marker is malformed")
        if (
            payload.get("schemaVersion") != MARKER_SCHEMA_VERSION
            or payload.get("storeSchemaVersion") != STORE_SCHEMA_VERSION
            or not isinstance(payload.get("storeId"), str)
        ):
            raise StoreUnavailable("initialization marker is incompatible")
        return payload

    def _configure(self, connection: sqlite3.Connection, *, allow_initialize: bool = False) -> None:
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA foreign_keys=ON")
        connection.execute(f"PRAGMA busy_timeout={self.busy_timeout_ms}")
        mode = str(connection.execute("PRAGMA journal_mode=WAL").fetchone()[0]).lower()
        if mode != "wal":
            raise StoreUnavailable("SQLite WAL mode is required")
        connection.execute("PRAGMA synchronous=FULL")
        synchronous = int(connection.execute("PRAGMA synchronous").fetchone()[0])
        if synchronous != 2:
            raise StoreUnavailable("SQLite synchronous=FULL is required")
        connection.execute("PRAGMA trusted_schema=OFF")
        if not allow_initialize:
            check = connection.execute("PRAGMA quick_check(1)").fetchone()
            if check is None or str(check[0]).lower() != "ok":
                raise StoreUnavailable("SQLite integrity check failed")

    def _connect(self) -> sqlite3.Connection:
        marker = self._marker()
        _strict_file(self.path, "consumption database", 8 * 1024 * 1024 * 1024)
        try:
            uri = self.path.resolve(strict=True).as_uri() + "?mode=rw"
            connection = sqlite3.connect(uri, uri=True, isolation_level=None)
            self._configure(connection)
            self._verify_schema(connection, marker)
            return connection
        except StoreUnavailable:
            raise
        except (OSError, sqlite3.Error, ValueError) as exc:
            raise StoreUnavailable("consumption database failed closed") from exc

    @staticmethod
    def _verify_schema(connection: sqlite3.Connection, marker: Mapping[str, Any]) -> None:
        try:
            metadata = dict(connection.execute("SELECT key, value FROM broker_meta"))
            tables = {
                str(row[0])
                for row in connection.execute(
                    "SELECT name FROM sqlite_master WHERE type = 'table'"
                )
            }
            triggers = {
                str(row[0])
                for row in connection.execute(
                    "SELECT name FROM sqlite_master WHERE type = 'trigger'"
                )
            }
        except sqlite3.Error as exc:
            raise StoreUnavailable("consumption database schema is unreadable") from exc
        if metadata.get("schema_version") != STORE_SCHEMA_VERSION:
            raise StoreUnavailable("consumption database schema version is incompatible")
        if metadata.get("store_id") != marker.get("storeId"):
            raise StoreUnavailable("database identity does not match its initialization marker")
        if not _REQUIRED_TABLES.issubset(tables) or not _REQUIRED_TRIGGERS.issubset(triggers):
            raise StoreUnavailable("consumption database schema is incomplete")

    def validate(self) -> dict[str, Any]:
        connection = self._connect()
        try:
            lease_count = int(connection.execute("SELECT count(*) FROM leases").fetchone()[0])
            reservation_count = int(
                connection.execute("SELECT count(*) FROM reservations").fetchone()[0]
            )
        finally:
            connection.close()
        return {
            "schemaVersion": "fouler-lease-consumption-store-check/v1",
            "ok": True,
            "path": str(self.path),
            "markerPath": str(self.marker_path),
            "leaseCount": lease_count,
            "reservationCount": reservation_count,
            "journalMode": "wal",
            "synchronous": "full",
        }

    def register_lease(self, payload: Mapping[str, Any]) -> dict[str, Any]:
        registration = LeaseRegistration.from_mapping(payload)
        connection = self._connect()
        try:
            connection.execute("BEGIN IMMEDIATE")
            existing = connection.execute(
                "SELECT lease_id, registration_json FROM leases WHERE authorization_digest = ?",
                (registration.authorization_digest,),
            ).fetchone()
            if existing is not None:
                if (
                    existing["lease_id"] == registration.lease_id
                    and existing["registration_json"] == registration.canonical_json
                ):
                    connection.commit()
                    return {
                        "ok": True,
                        "registered": False,
                        "idempotent": True,
                        "leaseId": registration.lease_id,
                        "authorizationDigest": registration.authorization_digest,
                    }
                raise BrokerError(
                    "lease_identity_conflict",
                    "authorization digest is already bound to different immutable identity or bounds",
                )
            try:
                connection.execute(
                    """
                    INSERT INTO leases(
                        authorization_digest, lease_id, source_commit, source_tree,
                        change_id, deployment_id, session_id, runtime_manifest_digest,
                        deployment_receipt_sha256, account, host_name, host_id_sha256,
                        proof_starts_at, proof_expires_at, proof_starts_filetime,
                        proof_expires_filetime, max_run_count, max_cycles,
                        max_concurrent_battles, improve_authorized, registration_json,
                        registered_at_filetime
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        registration.authorization_digest,
                        registration.lease_id,
                        registration.source_commit,
                        registration.source_tree,
                        registration.change_id,
                        registration.deployment_id,
                        registration.session_id,
                        registration.runtime_manifest_digest,
                        registration.deployment_receipt_sha256,
                        registration.account,
                        registration.host_name,
                        registration.host_id_sha256,
                        registration.proof_starts_at,
                        registration.proof_expires_at,
                        registration.proof_starts_filetime,
                        registration.proof_expires_filetime,
                        registration.max_run_count,
                        registration.max_cycles,
                        registration.max_concurrent_battles,
                        int(registration.improve_authorized),
                        registration.canonical_json,
                        utc_filetime(),
                    ),
                )
            except sqlite3.IntegrityError as exc:
                raise BrokerError(
                    "lease_identity_conflict",
                    "lease identity is already bound to another authorization digest",
                ) from exc
            connection.commit()
            return {
                "ok": True,
                "registered": True,
                "idempotent": False,
                "leaseId": registration.lease_id,
                "authorizationDigest": registration.authorization_digest,
            }
        except Exception:
            try:
                connection.rollback()
            except sqlite3.Error:
                pass
            raise
        finally:
            connection.close()

    def execute(
        self, payload: Mapping[str, Any], caller: CallerIdentity
    ) -> dict[str, Any]:
        request = _validated_request(payload)
        caller = caller.validated()
        authorization_digest = request["authorizationDigest"]
        request_id = request["requestId"]
        action = request["action"]
        digest = request_digest(request)
        connection = self._connect()
        try:
            connection.execute("BEGIN IMMEDIATE")
            lease = connection.execute(
                "SELECT * FROM leases WHERE authorization_digest = ?",
                (authorization_digest,),
            ).fetchone()
            if lease is None:
                connection.rollback()
                return _error_response(
                    request, "authorization_unknown", "authorization digest is not registered"
                )
            if lease["lease_id"] != request["leaseId"]:
                connection.rollback()
                return _error_response(
                    request,
                    "lease_identity_mismatch",
                    "leaseId does not match the registered authorization digest",
                )

            # Status is a bounded read and is deliberately not journaled. This
            # keeps malformed/repeated recovery traffic from growing the
            # append-only mutation journal.
            if action == "status":
                try:
                    result = self._status(connection, lease, request, caller)
                except BrokerError as exc:
                    connection.rollback()
                    return _error_response(request, exc.code, str(exc))
                connection.commit()
                return _success_response(request, result)

            previous = connection.execute(
                """
                SELECT action, request_sha256, caller_pid,
                       caller_process_creation_filetime, response_json
                FROM request_journal
                WHERE authorization_digest = ? AND request_id = ?
                """,
                (authorization_digest, request_id),
            ).fetchone()
            if previous is not None:
                if (
                    previous["action"] != action
                    or previous["request_sha256"] != digest
                    or int(previous["caller_pid"]) != caller.process_id
                    or int(previous["caller_process_creation_filetime"])
                    != caller.process_creation_filetime
                ):
                    connection.rollback()
                    return _error_response(
                        request,
                        "idempotency_conflict",
                        "requestId is already bound to a different request or caller process identity",
                    )
                response = strict_json_loads(previous["response_json"])
                if not isinstance(response, dict):
                    raise StoreUnavailable("idempotency journal contains malformed response data")
                connection.commit()
                return response

            try:
                result = self._perform(connection, lease, request, caller)
                response = _success_response(request, result)
            except BrokerError as exc:
                # Rejected requests do not mutate reservation state and are not
                # journaled. Only successful bounded transitions consume an
                # append-only request row.
                connection.rollback()
                return _error_response(request, exc.code, str(exc))

            connection.execute(
                """
                INSERT INTO request_journal(
                    authorization_digest, request_id, action, request_sha256,
                    caller_pid, caller_process_creation_filetime, response_json,
                    created_at_filetime
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    authorization_digest,
                    request_id,
                    action,
                    digest,
                    caller.process_id,
                    caller.process_creation_filetime,
                    canonical_json_bytes(response).decode("ascii"),
                    utc_filetime(),
                ),
            )
            connection.commit()
            return response
        except StoreUnavailable:
            try:
                connection.rollback()
            except sqlite3.Error:
                pass
            raise
        except sqlite3.Error as exc:
            try:
                connection.rollback()
            except sqlite3.Error:
                pass
            raise StoreUnavailable("SQLite transaction failed closed") from exc
        finally:
            connection.close()

    def administratively_abandon(
        self,
        *,
        authorization_digest: str,
        lease_id: str,
        reservation_id: str,
        reason: str,
    ) -> dict[str, Any]:
        """Resolve an orphan without exposing abandonment on the runtime pipe."""

        authorization_digest = _required_text(
            authorization_digest,
            "authorizationDigest",
            maximum=64,
            pattern=_HASH_RE,
        )
        lease_id = _required_text(lease_id, "leaseId", maximum=128, pattern=_ID_RE)
        reservation_id = _required_text(
            reservation_id,
            "reservationId",
            maximum=36,
            pattern=_RESERVATION_ID_RE,
        )
        reason = _required_text(reason, "reason", maximum=512)
        connection = self._connect()
        try:
            connection.execute("BEGIN IMMEDIATE")
            lease = connection.execute(
                "SELECT * FROM leases WHERE authorization_digest = ? AND lease_id = ?",
                (authorization_digest, lease_id),
            ).fetchone()
            if lease is None:
                raise BrokerError(
                    "authorization_unknown", "authorization digest and leaseId are not registered"
                )
            row = connection.execute(
                """
                SELECT * FROM reservations
                WHERE authorization_digest = ? AND reservation_id = ?
                """,
                (authorization_digest, reservation_id),
            ).fetchone()
            if row is None:
                raise BrokerError(
                    "reservation_unknown", "reservation does not exist for this lease"
                )
            if row["state"] == "completed":
                if (
                    row["outcome"] == "abandoned"
                    and row["completion_actor"] == "administrator"
                    and row["administrator_reason"] == reason
                ):
                    connection.commit()
                    return {
                        "ok": True,
                        "idempotent": True,
                        "reservationId": reservation_id,
                        "state": "completed",
                        "outcome": "abandoned",
                        "capacityReturned": False,
                    }
                raise BrokerError(
                    "reservation_terminal", "reservation already has a different terminal outcome"
                )
            prior_time = int(row["claimed_at_filetime"] or row["reserved_at_filetime"])
            now = max(utc_filetime(), prior_time + 1)
            connection.execute(
                """
                UPDATE reservations
                SET state = 'completed', completion_request_id = ?,
                    completed_at_filetime = ?, outcome = 'abandoned',
                    completion_actor = 'administrator', administrator_reason = ?
                WHERE reservation_id = ? AND state IN ('reserved', 'claimed')
                """,
                ("admin-abandon-" + uuid.uuid4().hex, now, reason, reservation_id),
            )
            connection.commit()
            return {
                "ok": True,
                "idempotent": False,
                "reservationId": reservation_id,
                "state": "completed",
                "outcome": "abandoned",
                "completedAtFiletime": now,
                "capacityReturned": False,
            }
        except Exception:
            try:
                connection.rollback()
            except sqlite3.Error:
                pass
            raise
        finally:
            connection.close()

    def _perform(
        self,
        connection: sqlite3.Connection,
        lease: sqlite3.Row,
        request: Mapping[str, Any],
        caller: CallerIdentity,
    ) -> dict[str, Any]:
        action = request["action"]
        if action in {"reserve-runtime", "reserve-improve", "claim"}:
            _require_active_proof_window(lease)
        if action == "reserve-runtime":
            return self._reserve_runtime(connection, lease, request, caller)
        if action == "reserve-improve":
            raise BrokerError(
                "improve_control_plane_only",
                "runtime improvement is disabled and delegated to the external DEKU control plane",
            )
        if action == "claim":
            return self._claim(connection, lease, request, caller)
        if action == "complete":
            return self._complete(connection, lease, request, caller)
        raise BrokerError("unsupported_action", "request action is not supported")

    @staticmethod
    def _reserve_runtime(
        connection: sqlite3.Connection,
        lease: sqlite3.Row,
        request: Mapping[str, Any],
        caller: CallerIdentity,
    ) -> dict[str, Any]:
        battle_count = int(request["battleCount"])
        cycle_count = int(request["cycleCount"])
        concurrency = int(request["maxConcurrentBattles"])
        outstanding = connection.execute(
            """
            SELECT reservation_id, state FROM reservations
            WHERE authorization_digest = ? AND state IN ('reserved', 'claimed')
            """,
            (lease["authorization_digest"],),
        ).fetchone()
        if outstanding is not None:
            raise BrokerError(
                "outstanding_reservation",
                "an earlier reservation remains unresolved",
            )
        used = connection.execute(
            """
            SELECT COALESCE(SUM(battle_count), 0) AS battles,
                   COALESCE(SUM(cycle_count), 0) AS cycles
            FROM reservations
            WHERE authorization_digest = ? AND kind = 'runtime'
            """,
            (lease["authorization_digest"],),
        ).fetchone()
        # No state filter is intentional: every reservation permanently counts.
        if int(used["battles"]) + battle_count > int(lease["max_run_count"]):
            raise BrokerError("run_bound_exhausted", "runtime battle reservation exceeds lease bound")
        if int(used["cycles"]) + cycle_count > int(lease["max_cycles"]):
            raise BrokerError("cycle_bound_exhausted", "runtime cycle reservation exceeds lease bound")
        if concurrency > int(lease["max_concurrent_battles"]):
            raise BrokerError(
                "concurrency_bound_exceeded",
                "requested concurrency exceeds the immutable lease bound",
            )
        reservation_id = "res-" + uuid.uuid4().hex
        launch_nonce = secrets.token_hex(32)
        launch_nonce_sha256 = hashlib.sha256(launch_nonce.encode("ascii")).hexdigest()
        now = utc_filetime()
        connection.execute(
            """
            INSERT INTO reservations(
                reservation_id, authorization_digest, reserve_request_id, kind,
                purpose, battle_count, cycle_count, max_concurrent_battles,
                supervisor_pid, supervisor_process_creation_filetime,
                supervisor_instance_id, launch_nonce_sha256, state,
                reserved_at_filetime
            ) VALUES (?, ?, ?, 'runtime', ?, ?, ?, ?, ?, ?, ?, ?, 'reserved', ?)
            """,
            (
                reservation_id,
                lease["authorization_digest"],
                request["requestId"],
                request["purpose"],
                battle_count,
                cycle_count,
                concurrency,
                caller.process_id,
                caller.process_creation_filetime,
                request["supervisorInstanceId"],
                launch_nonce_sha256,
                now,
            ),
        )
        return {
            "reservationId": reservation_id,
            "kind": "runtime",
            "purpose": request["purpose"],
            "state": "reserved",
            "battleCount": battle_count,
            "cycleCount": cycle_count,
            "maxConcurrentBattles": concurrency,
            "supervisorProcessId": caller.process_id,
            "supervisorProcessCreationFiletime": caller.process_creation_filetime,
            "supervisorInstanceId": request["supervisorInstanceId"],
            "launchNonce": launch_nonce,
            "reservedAtFiletime": now,
            "remainingRunCount": int(lease["max_run_count"])
            - int(used["battles"])
            - battle_count,
            "remainingCycles": int(lease["max_cycles"])
            - int(used["cycles"])
            - cycle_count,
        }

    @staticmethod
    def _reservation(
        connection: sqlite3.Connection,
        lease: sqlite3.Row,
        request: Mapping[str, Any],
    ) -> sqlite3.Row:
        row = connection.execute(
            """
            SELECT * FROM reservations
            WHERE reservation_id = ? AND authorization_digest = ?
            """,
            (request["reservationId"], lease["authorization_digest"]),
        ).fetchone()
        if row is None:
            raise BrokerError("reservation_unknown", "reservation does not exist for this lease")
        return row

    @staticmethod
    def _binding_result(
        row: sqlite3.Row, *, launch_nonce: str | None = None
    ) -> dict[str, Any]:
        result: dict[str, Any] = {
            "reservationId": row["reservation_id"],
            "kind": row["kind"],
            "purpose": row["purpose"],
            "battleCount": int(row["battle_count"]),
            "cycleCount": int(row["cycle_count"]),
            "maxConcurrentBattles": int(row["max_concurrent_battles"]),
            "supervisorProcessId": int(row["supervisor_pid"]),
            "supervisorProcessCreationFiletime": int(
                row["supervisor_process_creation_filetime"]
            ),
            "supervisorInstanceId": row["supervisor_instance_id"],
        }
        if launch_nonce is not None:
            result["launchNonce"] = launch_nonce
        return result

    @classmethod
    def _validate_binding(
        cls, row: sqlite3.Row, request: Mapping[str, Any]
    ) -> str:
        structural = {
            "reservationId": row["reservation_id"],
            "kind": row["kind"],
            "purpose": row["purpose"],
            "battleCount": int(row["battle_count"]),
            "cycleCount": int(row["cycle_count"]),
            "maxConcurrentBattles": int(row["max_concurrent_battles"]),
            "supervisorInstanceId": row["supervisor_instance_id"],
        }
        if any(request.get(name) != expected for name, expected in structural.items()):
            raise BrokerError(
                "reservation_binding_mismatch",
                "claim or completion does not match the immutable reservation workload",
            )
        if (
            request.get("supervisorProcessId") != int(row["supervisor_pid"])
            or request.get("supervisorProcessCreationFiletime")
            != int(row["supervisor_process_creation_filetime"])
        ):
            raise BrokerError(
                "supervisor_identity_mismatch",
                "authorized supervisor PID or creation identity does not match",
            )
        launch_nonce = str(request.get("launchNonce") or "")
        supplied_digest = hashlib.sha256(launch_nonce.encode("ascii")).hexdigest()
        if not hmac.compare_digest(supplied_digest, str(row["launch_nonce_sha256"])):
            raise BrokerError(
                "launch_nonce_mismatch",
                "broker-issued launch nonce does not match the reservation",
            )
        return launch_nonce

    @classmethod
    def _claim(
        cls,
        connection: sqlite3.Connection,
        lease: sqlite3.Row,
        request: Mapping[str, Any],
        caller: CallerIdentity,
    ) -> dict[str, Any]:
        row = cls._reservation(connection, lease, request)
        if row["state"] != "reserved":
            raise BrokerError("reservation_not_reserved", "reservation is no longer reservable")
        launch_nonce = cls._validate_binding(row, request)
        now = max(utc_filetime(), int(row["reserved_at_filetime"]) + 1)
        connection.execute(
            """
            UPDATE reservations
            SET state = 'claimed', claimed_pid = ?,
                claimed_process_creation_filetime = ?, claim_request_id = ?,
                claimed_at_filetime = ?
            WHERE reservation_id = ? AND state = 'reserved'
            """,
            (
                caller.process_id,
                caller.process_creation_filetime,
                request["requestId"],
                now,
                row["reservation_id"],
            ),
        )
        return {
            **cls._binding_result(row, launch_nonce=launch_nonce),
            "state": "claimed",
            "claimedProcessId": caller.process_id,
            "claimedProcessCreationFiletime": caller.process_creation_filetime,
            "claimedAtFiletime": now,
        }

    @classmethod
    def _complete(
        cls,
        connection: sqlite3.Connection,
        lease: sqlite3.Row,
        request: Mapping[str, Any],
        caller: CallerIdentity,
    ) -> dict[str, Any]:
        row = cls._reservation(connection, lease, request)
        if row["state"] not in {"reserved", "claimed"}:
            raise BrokerError("reservation_terminal", "reservation is already terminal")
        launch_nonce = cls._validate_binding(row, request)
        caller_is_supervisor = bool(
            int(row["supervisor_pid"]) == caller.process_id
            and int(row["supervisor_process_creation_filetime"])
            == caller.process_creation_filetime
        )
        caller_is_claimant = bool(
            row["state"] == "claimed"
            and int(row["claimed_pid"]) == caller.process_id
            and int(row["claimed_process_creation_filetime"])
            == caller.process_creation_filetime
        )
        if not (caller_is_supervisor or caller_is_claimant):
            raise BrokerError(
                "caller_identity_mismatch",
                "caller is neither the exact claimant nor the authorized supervisor",
            )
        outcome = str(request["outcome"])
        allowed = _RUNTIME_OUTCOMES if row["kind"] == "runtime" else _IMPROVE_OUTCOMES
        if outcome not in allowed:
            raise BrokerError(
                "outcome_invalid", f"outcome is not valid for a {row['kind']} reservation"
            )
        if row["state"] == "reserved" and (not caller_is_supervisor or outcome == "completed"):
            raise BrokerError(
                "reservation_not_claimed",
                "only the authorized supervisor may fail or abort an unclaimed launch",
            )
        prior_time = int(row["claimed_at_filetime"] or row["reserved_at_filetime"])
        now = max(utc_filetime(), prior_time + 1)
        actor = "supervisor" if caller_is_supervisor else "claimant"
        connection.execute(
            """
            UPDATE reservations
            SET state = 'completed', completion_request_id = ?,
                completed_at_filetime = ?, outcome = ?, completion_actor = ?,
                completion_actor_pid = ?,
                completion_actor_process_creation_filetime = ?
            WHERE reservation_id = ? AND state IN ('reserved', 'claimed')
            """,
            (
                request["requestId"],
                now,
                outcome,
                actor,
                caller.process_id,
                caller.process_creation_filetime,
                row["reservation_id"],
            ),
        )
        return {
            **cls._binding_result(row, launch_nonce=launch_nonce),
            "state": "completed",
            "outcome": outcome,
            "completionActor": actor,
            "completedAtFiletime": now,
            "capacityReturned": False,
        }

    @classmethod
    def _status(
        cls,
        connection: sqlite3.Connection,
        lease: sqlite3.Row,
        request: Mapping[str, Any],
        caller: CallerIdentity,
    ) -> dict[str, Any]:
        lookup_type = str(request["lookupType"])
        lookup_id = str(request["lookupId"])
        if lookup_type == "request":
            row = connection.execute(
                """
                SELECT action, caller_pid, caller_process_creation_filetime,
                       response_json
                FROM request_journal
                WHERE authorization_digest = ? AND request_id = ?
                """,
                (lease["authorization_digest"], lookup_id),
            ).fetchone()
            if row is None:
                return {
                    "lookupType": lookup_type,
                    "lookupId": lookup_id,
                    "found": False,
                }
            if (
                int(row["caller_pid"]) != caller.process_id
                or int(row["caller_process_creation_filetime"])
                != caller.process_creation_filetime
            ):
                raise BrokerError(
                    "status_caller_mismatch",
                    "request status is bound to the original caller process identity",
                )
            response = strict_json_loads(row["response_json"])
            if not isinstance(response, dict):
                raise StoreUnavailable("request journal contains malformed response data")
            return {
                "lookupType": lookup_type,
                "lookupId": lookup_id,
                "found": True,
                "originalAction": row["action"],
                "response": response,
            }

        row = connection.execute(
            """
            SELECT * FROM reservations
            WHERE authorization_digest = ? AND reservation_id = ?
            """,
            (lease["authorization_digest"], lookup_id),
        ).fetchone()
        if row is None:
            return {
                "lookupType": lookup_type,
                "lookupId": lookup_id,
                "found": False,
            }
        result = {
            "lookupType": lookup_type,
            "lookupId": lookup_id,
            "found": True,
            **cls._binding_result(row),
            "state": row["state"],
            "reservedAtFiletime": int(row["reserved_at_filetime"]),
            "capacityReturned": False,
        }
        if row["claimed_pid"] is not None:
            result.update(
                {
                    "claimedProcessId": int(row["claimed_pid"]),
                    "claimedProcessCreationFiletime": int(
                        row["claimed_process_creation_filetime"]
                    ),
                    "claimedAtFiletime": int(row["claimed_at_filetime"]),
                }
            )
        if row["state"] == "completed":
            result.update(
                {
                    "outcome": row["outcome"],
                    "completionActor": row["completion_actor"],
                    "completedAtFiletime": int(row["completed_at_filetime"]),
                }
            )
        return result


def _require_active_proof_window(lease: sqlite3.Row) -> None:
    now = utc_filetime()
    if now < int(lease["proof_starts_filetime"]):
        raise BrokerError("proof_window_not_started", "lease proof window has not started")
    if now >= int(lease["proof_expires_filetime"]):
        raise BrokerError("proof_window_expired", "lease proof window is expired")


def _validated_request(payload: Mapping[str, Any]) -> dict[str, Any]:
    if not isinstance(payload, Mapping):
        raise BrokerError("request_invalid", "request must be a JSON object")
    request = dict(payload)
    action = request.get("action")
    if not isinstance(action, str) or action not in _ACTION_FIELDS:
        raise BrokerError("unsupported_action", "request action is not supported")
    if set(request) != _ACTION_FIELDS[action]:
        raise BrokerError("request_fields_invalid", "request contains missing or unknown fields")
    if request.get("schemaVersion") != PROTOCOL_VERSION:
        raise BrokerError("request_schema_invalid", "request schema is unsupported")
    _required_text(
        request.get("requestId"), "requestId", maximum=192, pattern=_REQUEST_ID_RE
    )
    _required_text(
        request.get("authorizationDigest"),
        "authorizationDigest",
        maximum=64,
        pattern=_HASH_RE,
    )
    _required_text(request.get("leaseId"), "leaseId", maximum=128, pattern=_ID_RE)
    if action in {"reserve-runtime", "reserve-improve"}:
        _required_text(
            request.get("supervisorInstanceId"),
            "supervisorInstanceId",
            maximum=128,
            pattern=_ID_RE,
        )
        _required_text(request.get("purpose"), "purpose", maximum=128)
        _required_text(request.get("kind"), "kind", maximum=32)
    if action == "reserve-runtime":
        if request.get("purpose") != RUNTIME_RESERVATION_PURPOSE:
            raise BrokerError("purpose_invalid", "runtime reservation purpose is invalid")
        if request.get("kind") != "runtime":
            raise BrokerError("kind_invalid", "runtime reservation kind is invalid")
        _positive_integer(request.get("battleCount"), "battleCount", 1_000_000)
        _positive_integer(request.get("cycleCount"), "cycleCount", 100_000)
        _positive_integer(
            request.get("maxConcurrentBattles"), "maxConcurrentBattles", 3
        )
    if action == "reserve-improve":
        if request.get("purpose") != IMPROVE_RESERVATION_PURPOSE:
            raise BrokerError("purpose_invalid", "improvement reservation purpose is invalid")
        if request.get("kind") != "improve":
            raise BrokerError("kind_invalid", "improvement reservation kind is invalid")
        if any(
            type(request.get(name)) is not int or request.get(name) != 0
            for name in ("battleCount", "cycleCount", "maxConcurrentBattles")
        ):
            raise BrokerError(
                "improve_bounds_invalid",
                "runtime improvement reservations must carry zero runtime workload bounds",
            )
    if action in {"claim", "complete"}:
        _required_text(
            request.get("reservationId"),
            "reservationId",
            maximum=36,
            pattern=_RESERVATION_ID_RE,
        )
        _required_text(request.get("purpose"), "purpose", maximum=128)
        kind = _required_text(request.get("kind"), "kind", maximum=32)
        if kind not in _RESERVATION_KINDS:
            raise BrokerError("kind_invalid", "reservation kind is invalid")
        for name, maximum in (
            ("battleCount", 1_000_000),
            ("cycleCount", 100_000),
            ("maxConcurrentBattles", 3),
        ):
            value = request.get(name)
            if type(value) is not int or value < 0 or value > maximum:
                raise BrokerError(
                    "reservation_binding_invalid", f"{name} is outside the protocol bound"
                )
        _positive_integer(
            request.get("supervisorProcessId"),
            "supervisorProcessId",
            0xFFFFFFFF,
        )
        _positive_integer(
            request.get("supervisorProcessCreationFiletime"),
            "supervisorProcessCreationFiletime",
            0x7FFFFFFFFFFFFFFF,
        )
        _required_text(
            request.get("supervisorInstanceId"),
            "supervisorInstanceId",
            maximum=128,
            pattern=_ID_RE,
        )
        _required_text(
            request.get("launchNonce"),
            "launchNonce",
            maximum=64,
            pattern=_LAUNCH_NONCE_RE,
        )
    if action == "complete":
        _required_text(request.get("outcome"), "outcome", maximum=32)
    if action == "status":
        lookup_type = _required_text(
            request.get("lookupType"), "lookupType", maximum=32
        )
        if lookup_type not in _STATUS_LOOKUP_TYPES:
            raise BrokerError("status_lookup_invalid", "status lookup type is unsupported")
        pattern = _REQUEST_ID_RE if lookup_type == "request" else _RESERVATION_ID_RE
        maximum = 192 if lookup_type == "request" else 36
        _required_text(
            request.get("lookupId"), "lookupId", maximum=maximum, pattern=pattern
        )
    return request


def _success_response(request: Mapping[str, Any], result: Mapping[str, Any]) -> dict[str, Any]:
    return {
        "schemaVersion": RESPONSE_VERSION,
        "ok": True,
        "requestId": request.get("requestId"),
        "action": request.get("action"),
        "result": dict(result),
    }


def _error_response(
    request: Mapping[str, Any] | None, code: str, message: str
) -> dict[str, Any]:
    return {
        "schemaVersion": RESPONSE_VERSION,
        "ok": False,
        "requestId": request.get("requestId") if request else None,
        "action": request.get("action") if request else None,
        "error": {"code": code, "message": message},
    }


if os.name == "nt":
    from ctypes import wintypes

    class _SECURITY_ATTRIBUTES(ctypes.Structure):
        _fields_ = [
            ("nLength", wintypes.DWORD),
            ("lpSecurityDescriptor", wintypes.LPVOID),
            ("bInheritHandle", wintypes.BOOL),
        ]


    class _FILETIME(ctypes.Structure):
        _fields_ = [
            ("dwLowDateTime", wintypes.DWORD),
            ("dwHighDateTime", wintypes.DWORD),
        ]


    class _OVERLAPPED(ctypes.Structure):
        _fields_ = [
            ("Internal", ctypes.c_size_t),
            ("InternalHigh", ctypes.c_size_t),
            ("Offset", wintypes.DWORD),
            ("OffsetHigh", wintypes.DWORD),
            ("hEvent", wintypes.HANDLE),
        ]


    class _TRUSTEE_W(ctypes.Structure):
        _fields_ = [
            ("pMultipleTrustee", wintypes.LPVOID),
            ("MultipleTrusteeOperation", wintypes.DWORD),
            ("TrusteeForm", wintypes.DWORD),
            ("TrusteeType", wintypes.DWORD),
            ("ptstrName", wintypes.LPVOID),
        ]


    class _EXPLICIT_ACCESS_W(ctypes.Structure):
        _fields_ = [
            ("grfAccessPermissions", wintypes.DWORD),
            ("grfAccessMode", wintypes.DWORD),
            ("grfInheritance", wintypes.DWORD),
            ("Trustee", _TRUSTEE_W),
        ]


def _pipe_security_sddl(runtime_sid: str, broker_sid: str) -> str:
    if not _SID_RE.fullmatch(runtime_sid) or not _SID_RE.fullmatch(broker_sid):
        raise ValueError("pipe security SID is malformed")
    # CreateNamedPipe's duplex open maps to SYNCHRONIZE plus all pipe-specific
    # read/write bits (0x19f), including FILE_CREATE_PIPE_INSTANCE (0x4).
    # A data-only CreateFile open needs SYNCHRONIZE, FILE_READ_ATTRIBUTES, and
    # FILE_READ_DATA | FILE_WRITE_DATA (0x100083), but no create-instance bit.
    return (
        f"D:P(A;;0x0010019f;;;{broker_sid})"
        f"(A;;0x00100083;;;{runtime_sid})"
    )


class WindowsPipeServer:
    PIPE_ACCESS_DUPLEX = 0x00000003
    FILE_FLAG_OVERLAPPED = 0x40000000
    FILE_FLAG_FIRST_PIPE_INSTANCE = 0x00080000
    PIPE_TYPE_BYTE = 0x00000000
    PIPE_READMODE_BYTE = 0x00000000
    PIPE_WAIT = 0x00000000
    PIPE_REJECT_REMOTE_CLIENTS = 0x00000008
    MAX_ACTIVE_WORKERS = 8
    # Active workers, one bounded connected staging instance, and one listener.
    MAX_PIPE_INSTANCES = MAX_ACTIVE_WORKERS + 2
    CONNECTION_DEADLINE_SECONDS = 8.0
    SDDL_REVISION_1 = 1
    ERROR_PIPE_CONNECTED = 535
    ERROR_IO_PENDING = 997
    WAIT_OBJECT_0 = 0x00000000
    INFINITE = 0xFFFFFFFF
    PROCESS_QUERY_LIMITED_INFORMATION = 0x1000
    TOKEN_QUERY = 0x0008
    READ_CONTROL = 0x00020000
    WRITE_DAC = 0x00040000
    SE_KERNEL_OBJECT = 6
    DACL_SECURITY_INFORMATION = 0x00000004
    GRANT_ACCESS = 1
    NO_INHERITANCE = 0
    NO_MULTIPLE_TRUSTEE = 0
    TRUSTEE_IS_SID = 0
    TRUSTEE_IS_USER = 1
    ERROR_SUCCESS = 0
    LOCAL_SERVICE_SID = "S-1-5-19"
    INVALID_HANDLE_VALUE = ctypes.c_void_p(-1).value

    def __init__(
        self,
        store: ConsumptionStore,
        runtime_sid: str,
        *,
        pipe_name: str = PIPE_NAME,
        broker_sid: str = LOCAL_SERVICE_SID,
    ) -> None:
        if os.name != "nt":
            raise OSError("Windows named pipes are unavailable on this platform")
        if not _SID_RE.fullmatch(runtime_sid):
            raise ValueError("runtime SID is malformed")
        if not _SID_RE.fullmatch(broker_sid):
            raise ValueError("broker SID is malformed")
        if pipe_name != PIPE_NAME:
            raise ValueError("production broker pipe name is fixed")
        self.store = store
        self.runtime_sid = runtime_sid
        self.broker_sid = broker_sid
        self.pipe_name = pipe_name
        self.kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
        self.advapi32 = ctypes.WinDLL("advapi32", use_last_error=True)
        self._worker_slots = threading.BoundedSemaphore(self.MAX_ACTIVE_WORKERS)
        self._configure_api()

    def _configure_api(self) -> None:
        self.kernel32.CreateNamedPipeW.argtypes = [
            wintypes.LPCWSTR,
            wintypes.DWORD,
            wintypes.DWORD,
            wintypes.DWORD,
            wintypes.DWORD,
            wintypes.DWORD,
            wintypes.DWORD,
            ctypes.POINTER(_SECURITY_ATTRIBUTES),
        ]
        self.kernel32.CreateNamedPipeW.restype = wintypes.HANDLE
        self.kernel32.ConnectNamedPipe.argtypes = [wintypes.HANDLE, wintypes.LPVOID]
        self.kernel32.ConnectNamedPipe.restype = wintypes.BOOL
        self.kernel32.CloseHandle.argtypes = [wintypes.HANDLE]
        self.kernel32.CloseHandle.restype = wintypes.BOOL
        self.kernel32.CreateEventW.argtypes = [
            wintypes.LPVOID,
            wintypes.BOOL,
            wintypes.BOOL,
            wintypes.LPCWSTR,
        ]
        self.kernel32.CreateEventW.restype = wintypes.HANDLE
        self.kernel32.WaitForSingleObject.argtypes = [wintypes.HANDLE, wintypes.DWORD]
        self.kernel32.WaitForSingleObject.restype = wintypes.DWORD
        self.kernel32.GetOverlappedResult.argtypes = [
            wintypes.HANDLE,
            ctypes.POINTER(_OVERLAPPED),
            ctypes.POINTER(wintypes.DWORD),
            wintypes.BOOL,
        ]
        self.kernel32.GetOverlappedResult.restype = wintypes.BOOL
        self.kernel32.ReadFile.argtypes = [
            wintypes.HANDLE,
            wintypes.LPVOID,
            wintypes.DWORD,
            ctypes.POINTER(wintypes.DWORD),
            wintypes.LPVOID,
        ]
        self.kernel32.ReadFile.restype = wintypes.BOOL
        self.kernel32.WriteFile.argtypes = self.kernel32.ReadFile.argtypes
        self.kernel32.WriteFile.restype = wintypes.BOOL
        self.kernel32.GetNamedPipeClientProcessId.argtypes = [
            wintypes.HANDLE,
            ctypes.POINTER(wintypes.DWORD),
        ]
        self.kernel32.GetNamedPipeClientProcessId.restype = wintypes.BOOL
        self.kernel32.OpenProcess.argtypes = [
            wintypes.DWORD,
            wintypes.BOOL,
            wintypes.DWORD,
        ]
        self.kernel32.OpenProcess.restype = wintypes.HANDLE
        self.kernel32.GetProcessTimes.argtypes = [
            wintypes.HANDLE,
            ctypes.POINTER(_FILETIME),
            ctypes.POINTER(_FILETIME),
            ctypes.POINTER(_FILETIME),
            ctypes.POINTER(_FILETIME),
        ]
        self.kernel32.GetProcessTimes.restype = wintypes.BOOL
        self.kernel32.LocalFree.argtypes = [wintypes.HLOCAL]
        self.kernel32.LocalFree.restype = wintypes.HLOCAL
        self.advapi32.ConvertStringSecurityDescriptorToSecurityDescriptorW.argtypes = [
            wintypes.LPCWSTR,
            wintypes.DWORD,
            ctypes.POINTER(wintypes.LPVOID),
            ctypes.POINTER(wintypes.DWORD),
        ]
        self.advapi32.ConvertStringSecurityDescriptorToSecurityDescriptorW.restype = wintypes.BOOL
        self.advapi32.ConvertStringSidToSidW.argtypes = [
            wintypes.LPCWSTR,
            ctypes.POINTER(wintypes.LPVOID),
        ]
        self.advapi32.ConvertStringSidToSidW.restype = wintypes.BOOL
        self.advapi32.OpenProcessToken.argtypes = [
            wintypes.HANDLE,
            wintypes.DWORD,
            ctypes.POINTER(wintypes.HANDLE),
        ]
        self.advapi32.OpenProcessToken.restype = wintypes.BOOL
        self.advapi32.GetSecurityInfo.argtypes = [
            wintypes.HANDLE,
            ctypes.c_int,
            wintypes.DWORD,
            ctypes.POINTER(wintypes.LPVOID),
            ctypes.POINTER(wintypes.LPVOID),
            ctypes.POINTER(wintypes.LPVOID),
            ctypes.POINTER(wintypes.LPVOID),
            ctypes.POINTER(wintypes.LPVOID),
        ]
        self.advapi32.GetSecurityInfo.restype = wintypes.DWORD
        self.advapi32.SetEntriesInAclW.argtypes = [
            wintypes.ULONG,
            ctypes.POINTER(_EXPLICIT_ACCESS_W),
            wintypes.LPVOID,
            ctypes.POINTER(wintypes.LPVOID),
        ]
        self.advapi32.SetEntriesInAclW.restype = wintypes.DWORD
        self.advapi32.SetSecurityInfo.argtypes = [
            wintypes.HANDLE,
            ctypes.c_int,
            wintypes.DWORD,
            wintypes.LPVOID,
            wintypes.LPVOID,
            wintypes.LPVOID,
            wintypes.LPVOID,
        ]
        self.advapi32.SetSecurityInfo.restype = wintypes.DWORD

    @staticmethod
    def _filetime(value: "_FILETIME") -> int:
        return (int(value.dwHighDateTime) << 32) | int(value.dwLowDateTime)

    @staticmethod
    def _error(operation: str) -> OSError:
        code = ctypes.get_last_error()
        return OSError(code, f"{operation} failed: {ctypes.FormatError(code).strip()}")

    @staticmethod
    def _status_error(operation: str, status: int) -> OSError:
        return OSError(status, f"{operation} failed: {ctypes.FormatError(status).strip()}")

    def _grant_runtime_object_access(
        self, handle: int, *, access_mask: int, label: str
    ) -> None:
        runtime_sid = wintypes.LPVOID()
        security_descriptor = wintypes.LPVOID()
        existing_dacl = wintypes.LPVOID()
        updated_dacl = wintypes.LPVOID()
        try:
            if not self.advapi32.ConvertStringSidToSidW(
                self.runtime_sid, ctypes.byref(runtime_sid)
            ):
                raise self._error("ConvertStringSidToSidW(runtime)")
            status = int(
                self.advapi32.GetSecurityInfo(
                    handle,
                    self.SE_KERNEL_OBJECT,
                    self.DACL_SECURITY_INFORMATION,
                    None,
                    None,
                    ctypes.byref(existing_dacl),
                    None,
                    ctypes.byref(security_descriptor),
                )
            )
            if status != self.ERROR_SUCCESS:
                raise self._status_error(f"GetSecurityInfo({label}-DACL)", status)
            trustee = _TRUSTEE_W(
                None,
                self.NO_MULTIPLE_TRUSTEE,
                self.TRUSTEE_IS_SID,
                self.TRUSTEE_IS_USER,
                runtime_sid,
            )
            access = _EXPLICIT_ACCESS_W(
                access_mask,
                self.GRANT_ACCESS,
                self.NO_INHERITANCE,
                trustee,
            )
            status = int(
                self.advapi32.SetEntriesInAclW(
                    1,
                    ctypes.byref(access),
                    existing_dacl,
                    ctypes.byref(updated_dacl),
                )
            )
            if status != self.ERROR_SUCCESS:
                raise self._status_error(f"SetEntriesInAclW({label}-DACL)", status)
            status = int(
                self.advapi32.SetSecurityInfo(
                    handle,
                    self.SE_KERNEL_OBJECT,
                    self.DACL_SECURITY_INFORMATION,
                    None,
                    None,
                    updated_dacl,
                    None,
                )
            )
            if status != self.ERROR_SUCCESS:
                raise self._status_error(f"SetSecurityInfo({label}-DACL)", status)
        finally:
            if updated_dacl:
                self.kernel32.LocalFree(updated_dacl)
            if security_descriptor:
                self.kernel32.LocalFree(security_descriptor)
            if runtime_sid:
                self.kernel32.LocalFree(runtime_sid)

    def _grant_runtime_process_query(self, process_id: int) -> None:
        process = self.kernel32.OpenProcess(
            self.READ_CONTROL | self.WRITE_DAC, False, process_id
        )
        if not process:
            raise self._error("OpenProcess(process-DACL)")
        try:
            self._grant_runtime_object_access(
                process,
                access_mask=self.PROCESS_QUERY_LIMITED_INFORMATION,
                label="process",
            )
        finally:
            self.kernel32.CloseHandle(process)

    def _grant_runtime_token_query(self, process_id: int) -> None:
        process = self.kernel32.OpenProcess(
            self.PROCESS_QUERY_LIMITED_INFORMATION, False, process_id
        )
        if not process:
            raise self._error("OpenProcess(token-DACL)")
        token = wintypes.HANDLE()
        try:
            if not self.advapi32.OpenProcessToken(
                process,
                self.READ_CONTROL | self.WRITE_DAC,
                ctypes.byref(token),
            ):
                raise self._error("OpenProcessToken(token-DACL)")
            self._grant_runtime_object_access(
                token.value,
                access_mask=self.TOKEN_QUERY,
                label="token",
            )
        finally:
            if token.value:
                self.kernel32.CloseHandle(token.value)
            self.kernel32.CloseHandle(process)

    def _grant_runtime_attestation_access(self) -> None:
        # The client verifies both the base-Python pipe owner and the Windows
        # venv redirector that remains between it and NSSM.
        for process_id in {os.getpid(), os.getppid()}:
            self._grant_runtime_process_query(process_id)
            self._grant_runtime_token_query(process_id)

    def _create_pipe(self, *, first_instance: bool) -> int:
        sddl = _pipe_security_sddl(self.runtime_sid, self.broker_sid)
        descriptor = wintypes.LPVOID()
        descriptor_size = wintypes.DWORD(0)
        if not self.advapi32.ConvertStringSecurityDescriptorToSecurityDescriptorW(
            sddl,
            self.SDDL_REVISION_1,
            ctypes.byref(descriptor),
            ctypes.byref(descriptor_size),
        ):
            raise self._error("ConvertStringSecurityDescriptorToSecurityDescriptorW")
        attributes = _SECURITY_ATTRIBUTES(
            ctypes.sizeof(_SECURITY_ATTRIBUTES), descriptor, False
        )
        try:
            open_mode = self.PIPE_ACCESS_DUPLEX | self.FILE_FLAG_OVERLAPPED
            if first_instance:
                open_mode |= self.FILE_FLAG_FIRST_PIPE_INSTANCE
            pipe_mode = (
                self.PIPE_TYPE_BYTE
                | self.PIPE_READMODE_BYTE
                | self.PIPE_WAIT
                | self.PIPE_REJECT_REMOTE_CLIENTS
            )
            handle = self.kernel32.CreateNamedPipeW(
                self.pipe_name,
                open_mode,
                pipe_mode,
                self.MAX_PIPE_INSTANCES,
                MAX_FRAME_BYTES + 4,
                MAX_FRAME_BYTES + 4,
                5_000,
                ctypes.byref(attributes),
            )
        finally:
            self.kernel32.LocalFree(descriptor)
        if handle == self.INVALID_HANDLE_VALUE:
            raise self._error("CreateNamedPipeW")
        return handle

    def _connect(self, handle: int) -> None:
        event = self.kernel32.CreateEventW(None, True, False, None)
        if not event:
            raise self._error("CreateEventW(connect)")
        overlapped = _OVERLAPPED()
        overlapped.hEvent = event
        try:
            if self.kernel32.ConnectNamedPipe(handle, ctypes.byref(overlapped)):
                return
            code = ctypes.get_last_error()
            if code == self.ERROR_PIPE_CONNECTED:
                return
            if code != self.ERROR_IO_PENDING:
                raise self._error("ConnectNamedPipe")
            if (
                self.kernel32.WaitForSingleObject(event, self.INFINITE)
                != self.WAIT_OBJECT_0
            ):
                raise self._error("WaitForSingleObject(connect)")
            transferred = wintypes.DWORD(0)
            if not self.kernel32.GetOverlappedResult(
                handle,
                ctypes.byref(overlapped),
                ctypes.byref(transferred),
                False,
            ):
                raise self._error("GetOverlappedResult(connect)")
        finally:
            self.kernel32.CloseHandle(event)

    def _caller_identity(self, handle: int) -> CallerIdentity:
        pid = wintypes.DWORD(0)
        if not self.kernel32.GetNamedPipeClientProcessId(handle, ctypes.byref(pid)):
            raise self._error("GetNamedPipeClientProcessId")
        process = self.kernel32.OpenProcess(
            self.PROCESS_QUERY_LIMITED_INFORMATION, False, pid.value
        )
        if not process:
            raise self._error("OpenProcess(client)")
        try:
            creation = _FILETIME()
            exit_time = _FILETIME()
            kernel = _FILETIME()
            user = _FILETIME()
            if not self.kernel32.GetProcessTimes(
                process,
                ctypes.byref(creation),
                ctypes.byref(exit_time),
                ctypes.byref(kernel),
                ctypes.byref(user),
            ):
                raise self._error("GetProcessTimes(client)")
            return CallerIdentity(int(pid.value), self._filetime(creation)).validated()
        finally:
            self.kernel32.CloseHandle(process)

    def _read_exact(self, handle: int, length: int, *, deadline: float) -> bytes:
        parts: list[bytes] = []
        remaining = length
        while remaining:
            amount = min(remaining, 16 * 1024)
            buffer = ctypes.create_string_buffer(amount)
            received = _overlapped_transfer(
                handle, buffer, amount, write=False, deadline=deadline
            )
            if received == 0:
                raise ProtocolError("truncated_frame", "pipe closed during request")
            parts.append(buffer.raw[:received])
            remaining -= received
        return b"".join(parts)

    def _write_all(self, handle: int, content: bytes, *, deadline: float) -> None:
        offset = 0
        while offset < len(content):
            chunk = content[offset : offset + 16 * 1024]
            buffer = ctypes.create_string_buffer(chunk)
            written = _overlapped_transfer(
                handle, buffer, len(chunk), write=True, deadline=deadline
            )
            if written == 0:
                raise OSError("WriteFile completed without progress")
            offset += written

    def _serve_connection(self, handle: int, *, deadline: float | None = None) -> None:
        request: dict[str, Any] | None = None
        if deadline is None:
            deadline = time.monotonic() + self.CONNECTION_DEADLINE_SECONDS
        try:
            caller = self._caller_identity(handle)
            parsed = read_framed(
                lambda count: self._read_exact(handle, count, deadline=deadline)
            )
            if not isinstance(parsed, dict):
                raise BrokerError("request_invalid", "request must be a JSON object")
            request = parsed
            response = self.store.execute(request, caller)
        except DuplicateJSONKeyError:
            response = _error_response(None, "duplicate_json_key", "duplicate JSON keys are forbidden")
        except ProtocolError as exc:
            response = _error_response(None, exc.code, str(exc))
        except BrokerError as exc:
            response = _error_response(request, exc.code, str(exc))
        except Exception:
            response = _error_response(None, "broker_failure", "broker failed closed")
        try:
            self._write_all(handle, encode_frame(response), deadline=deadline)
        finally:
            self.kernel32.CloseHandle(handle)

    def _serve_connection_bounded(
        self, handle: int, *, deadline: float | None = None
    ) -> None:
        try:
            self._serve_connection(handle, deadline=deadline)
        finally:
            self._worker_slots.release()

    def serve_forever(self) -> None:
        self.store.validate()
        self._grant_runtime_attestation_access()
        listener = self._create_pipe(first_instance=True)
        while True:
            next_listener: int | None = None
            slot_acquired = False
            try:
                self._connect(listener)
                deadline = time.monotonic() + self.CONNECTION_DEADLINE_SECONDS
                # Keep ownership of the pipe name continuously after the
                # FIRST_PIPE_INSTANCE anti-squatting check succeeds.
                next_listener = self._create_pipe(first_instance=False)
                slot_acquired = self._worker_slots.acquire(
                    timeout=max(0.0, deadline - time.monotonic())
                )
                if not slot_acquired:
                    self.kernel32.CloseHandle(listener)
                    listener = next_listener
                    continue
            except Exception:
                if slot_acquired:
                    self._worker_slots.release()
                self.kernel32.CloseHandle(listener)
                if next_listener is not None:
                    self.kernel32.CloseHandle(next_listener)
                raise
            worker = threading.Thread(
                target=self._serve_connection_bounded,
                args=(listener,),
                kwargs={"deadline": deadline},
                name="fouler-lease-broker-client",
                daemon=True,
            )
            try:
                worker.start()
            except Exception:
                self._worker_slots.release()
                self.kernel32.CloseHandle(listener)
                self.kernel32.CloseHandle(next_listener)
                raise
            listener = next_listener


def _load_registration(path: Path, expected_sha256: str) -> dict[str, Any]:
    if not _HASH_RE.fullmatch(expected_sha256.lower()):
        raise BrokerError("registration_hash_invalid", "expected registration SHA-256 is malformed")
    content = _strict_file(path, "registration file", MAX_FRAME_BYTES)
    if hashlib.sha256(content).hexdigest() != expected_sha256.lower():
        raise BrokerError("registration_hash_mismatch", "registration file SHA-256 does not match")
    try:
        payload = strict_json_loads(content)
    except (DuplicateJSONKeyError, ProtocolError, ValueError) as exc:
        raise BrokerError("registration_invalid", "registration file is not strict JSON") from exc
    if not isinstance(payload, dict):
        raise BrokerError("registration_invalid", "registration file must contain an object")
    return payload


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Fouler Windows runtime lease consumption broker.")
    parser.add_argument("--store-path", default=str(DEFAULT_STORE_PATH))
    parser.add_argument("--marker-path", default=str(DEFAULT_MARKER_PATH))
    subparsers = parser.add_subparsers(dest="command", required=True)
    subparsers.add_parser("initialize-store")
    subparsers.add_parser("check-store")
    register = subparsers.add_parser("register-lease")
    register.add_argument("--registration", required=True)
    register.add_argument("--expected-registration-sha256", required=True)
    abandon = subparsers.add_parser(
        "admin-abandon-reservation",
        help="Administrator-only orphan reconciliation; never exposed on the runtime pipe.",
    )
    abandon.add_argument("--authorization-digest", required=True)
    abandon.add_argument("--lease-id", required=True)
    abandon.add_argument("--reservation-id", required=True)
    abandon.add_argument("--reason", required=True)
    serve = subparsers.add_parser("serve")
    serve.add_argument("--runtime-sid", required=True)
    args = parser.parse_args(argv)

    store = ConsumptionStore(args.store_path, args.marker_path)
    try:
        if args.command == "initialize-store":
            store = ConsumptionStore.initialize(args.store_path, args.marker_path)
            payload = store.validate()
        elif args.command == "check-store":
            payload = store.validate()
        elif args.command == "register-lease":
            registration = _load_registration(
                Path(args.registration).absolute(), args.expected_registration_sha256
            )
            payload = store.register_lease(registration)
        elif args.command == "admin-abandon-reservation":
            payload = store.administratively_abandon(
                authorization_digest=args.authorization_digest,
                lease_id=args.lease_id,
                reservation_id=args.reservation_id,
                reason=args.reason,
            )
        elif args.command == "serve":
            WindowsPipeServer(store, args.runtime_sid).serve_forever()
            return 0
        else:
            parser.error("unsupported command")
    except (BrokerError, OSError, sqlite3.Error, ValueError) as exc:
        print(
            json.dumps(
                {
                    "schemaVersion": "fouler-lease-broker-command/v1",
                    "ok": False,
                    "error": {
                        "code": getattr(exc, "code", "command_failed"),
                        "message": str(exc),
                    },
                },
                sort_keys=True,
            )
        )
        return 2
    print(json.dumps(payload, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
