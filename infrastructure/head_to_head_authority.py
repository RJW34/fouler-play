#!/usr/bin/env python3
"""Local H2H evidence ledger; production attempts require a DEKU-owned anchor."""

from __future__ import annotations

import hashlib
import json
import os
import sqlite3
import stat
import uuid
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Mapping


AUTHORITY_SCHEMA_VERSION = "fouler-head-to-head-ledger-authority/v1"
LEDGER_SCHEMA_VERSION = "fouler-head-to-head-ledger/v2"
DEFAULT_STATE_ROOT = Path.home() / ".deku" / "state"
DEFAULT_AUTHORITY_PATH = DEFAULT_STATE_ROOT / "fouler-h2h-ledger-authority.json"
DEFAULT_LEDGER_PATH = DEFAULT_STATE_ROOT / "fouler-h2h-attempts.sqlite3"
_AUTHORITY_FIELDS = {
    "schemaVersion",
    "authorityId",
    "authorityPath",
    "ledgerPath",
    "ledgerId",
    "createdAtUtc",
    "authorityDigest",
}
_AUTHORITY_METADATA_FIELDS = {
    "authorityId": "authorityId",
    "authorityPath": "authorityPath",
    "ledgerPath": "ledgerPath",
    "authorityDigest": "authorityDigest",
}
_REQUIRED_LEDGER_TABLES = {
    "metadata",
    "attempts",
    "improve_authorizations",
}
_REQUIRED_LEDGER_TRIGGERS = {
    "metadata_immutable",
    "metadata_no_delete",
    "attempts_insert_registered_only",
    "attempts_no_delete",
    "attempts_forward_only",
    "improve_authorizations_immutable",
    "improve_authorizations_no_delete",
}
_REQUIRED_LEDGER_INDEXES = {"attempts_family_ordinal"}
_LEDGER_SCHEMA_OBJECTS = (
    _REQUIRED_LEDGER_TABLES
    | _REQUIRED_LEDGER_INDEXES
    | _REQUIRED_LEDGER_TRIGGERS
)
_LEDGER_SCHEMA_DIGEST = "920916bd96d6b1733c4f936ca377cbfa8a283eec1f576f88dc2456c3381d7632"


@dataclass(frozen=True)
class LedgerAuthority:
    authority_path: Path
    ledger_path: Path
    ledger_id: str
    authority_id: str
    created_at_utc: str
    authority_digest: str

    def as_dict(self, *, created: bool | None = None) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "schemaVersion": AUTHORITY_SCHEMA_VERSION,
            "authorityId": self.authority_id,
            "authorityPath": str(self.authority_path),
            "ledgerPath": str(self.ledger_path),
            "ledgerId": self.ledger_id,
            "createdAtUtc": self.created_at_utc,
            "authorityDigest": self.authority_digest,
        }
        if created is not None:
            payload["created"] = created
        return payload


def durable_attempt_anchor_status(authority: LedgerAuthority | None) -> dict[str, Any]:
    """Describe the deliberately unimplemented external durability boundary.

    File mode bits and SQLite triggers protect against accidents, not replacement
    by the owning identity. Until DEKU supplies a separately owned reservation and
    consumption service, this module must never claim an alpha-budget reservation.
    """

    return {
        "schemaVersion": "fouler-h2h-durable-anchor-status/v1",
        "proven": False,
        "authorityId": authority.authority_id if authority is not None else None,
        "blocker": (
            "DEKU-owned durable attempt anchor is not integrated; the evaluator identity "
            "can replace both local authority and SQLite files"
        ),
    }


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def canonical_path(path: Path | str) -> Path:
    return Path(path).expanduser().resolve(strict=False)


def _validate_ledger_id(ledger_id: str) -> str:
    normalized = str(ledger_id).strip()
    if not normalized:
        raise ValueError("ledger_id is required")
    if len(normalized) > 128 or any(ord(character) < 33 for character in normalized):
        raise ValueError("ledger_id must be 1-128 printable non-whitespace characters")
    return normalized


def authority_digest(payload: Mapping[str, Any]) -> str:
    digest_input = {
        key: payload.get(key)
        for key in sorted(_AUTHORITY_FIELDS - {"authorityDigest"})
    }
    encoded = json.dumps(
        digest_input,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=True,
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def _authority_payload(
    *,
    authority_path: Path,
    ledger_path: Path,
    ledger_id: str,
    authority_id: str,
    created_at_utc: str,
) -> dict[str, Any]:
    payload = {
        "schemaVersion": AUTHORITY_SCHEMA_VERSION,
        "authorityId": authority_id,
        "authorityPath": str(authority_path),
        "ledgerPath": str(ledger_path),
        "ledgerId": ledger_id,
        "createdAtUtc": created_at_utc,
    }
    payload["authorityDigest"] = authority_digest(payload)
    return payload


def _authority_from_payload(payload: Mapping[str, Any], actual_path: Path) -> LedgerAuthority:
    if set(payload) != _AUTHORITY_FIELDS:
        raise RuntimeError("H2H ledger authority has missing or unexpected fields")
    if payload.get("schemaVersion") != AUTHORITY_SCHEMA_VERSION:
        raise RuntimeError(f"H2H ledger authority schema must be {AUTHORITY_SCHEMA_VERSION}")
    authority_path_value = str(payload.get("authorityPath") or "")
    if not Path(authority_path_value).expanduser().is_absolute():
        raise RuntimeError("H2H ledger authority authorityPath must be absolute")
    configured_authority_path = canonical_path(authority_path_value)
    if configured_authority_path != actual_path:
        raise RuntimeError("H2H ledger authority was moved or copied from its initialized path")
    ledger_path_value = str(payload.get("ledgerPath") or "")
    if not Path(ledger_path_value).expanduser().is_absolute():
        raise RuntimeError("H2H ledger authority ledgerPath must be absolute")
    ledger_path = canonical_path(ledger_path_value)
    if str(ledger_path) != ledger_path_value:
        raise RuntimeError("H2H ledger authority ledgerPath is not canonical")
    ledger_id = _validate_ledger_id(str(payload.get("ledgerId") or ""))
    authority_id = str(payload.get("authorityId") or "").strip()
    if len(authority_id) != 32 or any(character not in "0123456789abcdef" for character in authority_id):
        raise RuntimeError("H2H ledger authority ID is malformed")
    created_at_utc = str(payload.get("createdAtUtc") or "").strip()
    try:
        created_at = datetime.fromisoformat(created_at_utc)
    except ValueError as exc:
        raise RuntimeError("H2H ledger authority creation time is malformed") from exc
    if created_at.tzinfo is None:
        raise RuntimeError("H2H ledger authority creation time must include a timezone")
    expected_digest = authority_digest(payload)
    if payload.get("authorityDigest") != expected_digest:
        raise RuntimeError("H2H ledger authority digest does not match its contents")
    return LedgerAuthority(
        authority_path=actual_path,
        ledger_path=ledger_path,
        ledger_id=ledger_id,
        authority_id=authority_id,
        created_at_utc=created_at_utc,
        authority_digest=expected_digest,
    )


def _read_authority_file(authority_path: Path | str) -> LedgerAuthority:
    path = canonical_path(authority_path)
    if not path.is_file() or path.is_symlink():
        raise RuntimeError(f"H2H ledger authority is missing or linked: {path}")
    if path.stat().st_mode & (stat.S_IWUSR | stat.S_IWGRP | stat.S_IWOTH):
        raise RuntimeError(f"H2H ledger authority is writable instead of immutable: {path}")
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except Exception as exc:
        raise RuntimeError(f"H2H ledger authority is malformed: {exc}") from exc
    if not isinstance(payload, dict):
        raise RuntimeError("H2H ledger authority must contain one JSON object")
    return _authority_from_payload(payload, path)


def _metadata_for_authority(authority: LedgerAuthority | None) -> tuple[tuple[str, str], ...]:
    if authority is None:
        return ()
    payload = authority.as_dict()
    return tuple(
        (metadata_key, str(payload[payload_key]))
        for metadata_key, payload_key in _AUTHORITY_METADATA_FIELDS.items()
    )


def _validate_ledger_metadata(
    connection: sqlite3.Connection,
    *,
    ledger_path: Path,
    ledger_id: str,
    authority: LedgerAuthority | None,
) -> None:
    integrity = connection.execute("PRAGMA integrity_check").fetchone()
    if integrity is None or str(integrity[0]).lower() != "ok":
        raise RuntimeError("attempt ledger failed SQLite integrity_check")
    try:
        metadata = dict(connection.execute("SELECT key, value FROM metadata").fetchall())
    except sqlite3.Error as exc:
        raise RuntimeError(f"attempt ledger metadata is malformed: {exc}") from exc
    if metadata.get("schemaVersion") != LEDGER_SCHEMA_VERSION:
        raise RuntimeError(f"attempt ledger schema is not {LEDGER_SCHEMA_VERSION}")
    if metadata.get("ledgerId") != ledger_id:
        raise RuntimeError("attempt ledger identity does not match the configured authority")
    if authority is None:
        expected = {}
    else:
        expected = dict(_metadata_for_authority(authority))
        for key, value in expected.items():
            if metadata.get(key) != value:
                raise RuntimeError(f"attempt ledger authority metadata mismatch: {key}")
        if ledger_path != authority.ledger_path:
            raise RuntimeError("attempt ledger path does not match the configured authority")

    schema_rows = connection.execute(
        "SELECT type, name, sql FROM sqlite_master "
        "WHERE type IN ('table', 'index', 'trigger') ORDER BY type, name"
    ).fetchall()
    tables = {str(row[1]) for row in schema_rows if row[0] == "table"}
    indexes = {str(row[1]) for row in schema_rows if row[0] == "index"}
    triggers = {str(row[1]) for row in schema_rows if row[0] == "trigger"}
    missing_tables = sorted(_REQUIRED_LEDGER_TABLES - tables)
    missing_indexes = sorted(_REQUIRED_LEDGER_INDEXES - indexes)
    missing_triggers = sorted(_REQUIRED_LEDGER_TRIGGERS - triggers)
    if missing_tables:
        raise RuntimeError("attempt ledger is missing protected table(s): " + ", ".join(missing_tables))
    if missing_indexes:
        raise RuntimeError("attempt ledger is missing protected index(es): " + ", ".join(missing_indexes))
    if missing_triggers:
        raise RuntimeError("attempt ledger is missing append-only trigger(s): " + ", ".join(missing_triggers))
    protected_schema = [
        (str(row[0]), str(row[1]), str(row[2]))
        for row in schema_rows
        if str(row[1]) in _LEDGER_SCHEMA_OBJECTS
    ]
    encoded_schema = json.dumps(
        protected_schema,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=True,
    ).encode("utf-8")
    if hashlib.sha256(encoded_schema).hexdigest() != _LEDGER_SCHEMA_DIGEST:
        raise RuntimeError("attempt ledger protected schema fingerprint is invalid")


def open_evaluation_ledger(
    path: Path | str,
    ledger_id: str,
    *,
    authority: LedgerAuthority | None = None,
    writable: bool,
    verify_authority_file: bool = True,
) -> sqlite3.Connection:
    ledger_path = canonical_path(path)
    normalized_id = _validate_ledger_id(ledger_id)
    if authority is not None:
        if verify_authority_file and _read_authority_file(authority.authority_path) != authority:
            raise RuntimeError("H2H ledger authority was replaced after it was loaded")
        if ledger_path != authority.ledger_path or normalized_id != authority.ledger_id:
            raise RuntimeError("attempt ledger target does not match the configured authority")
    if not ledger_path.is_file() or ledger_path.is_symlink():
        raise RuntimeError(f"pre-provisioned attempt ledger is missing or linked: {ledger_path}")
    mode = "rw" if writable else "ro"
    connection = sqlite3.connect(f"file:{ledger_path.as_posix()}?mode={mode}", uri=True, timeout=30)
    connection.row_factory = sqlite3.Row
    try:
        _validate_ledger_metadata(
            connection,
            ledger_path=ledger_path,
            ledger_id=normalized_id,
            authority=authority,
        )
    except Exception:
        connection.close()
        raise
    return connection


def load_ledger_authority(authority_path: Path | str | None = None) -> LedgerAuthority:
    authority = _read_authority_file(authority_path or DEFAULT_AUTHORITY_PATH)
    connection = open_evaluation_ledger(
        authority.ledger_path,
        authority.ledger_id,
        authority=authority,
        writable=False,
        verify_authority_file=False,
    )
    connection.close()
    return authority


def _reserve_file(path: Path, mode: int) -> None:
    descriptor = os.open(path, os.O_CREAT | os.O_EXCL | os.O_RDWR, mode)
    os.close(descriptor)


def initialize_evaluation_ledger(
    path: Path | str,
    ledger_id: str,
    *,
    authority: LedgerAuthority | None = None,
) -> dict[str, Any]:
    """Create a ledger exclusively; direct use is reserved for explicit test fixtures."""
    ledger_path = canonical_path(path)
    normalized_id = _validate_ledger_id(ledger_id)
    if authority is not None and (
        ledger_path != authority.ledger_path or normalized_id != authority.ledger_id
    ):
        raise ValueError("ledger path and ID must match the authority")
    ledger_path.parent.mkdir(parents=True, exist_ok=True)
    _reserve_file(ledger_path, 0o600)
    connection: sqlite3.Connection | None = None
    try:
        connection = sqlite3.connect(ledger_path)
        connection.executescript(
            """
            PRAGMA journal_mode=WAL;
            PRAGMA synchronous=FULL;
            CREATE TABLE metadata (key TEXT PRIMARY KEY, value TEXT NOT NULL);
            CREATE TABLE attempts (
                sequence INTEGER PRIMARY KEY AUTOINCREMENT,
                attempt_id TEXT NOT NULL UNIQUE,
                ledger_id TEXT NOT NULL,
                run_id TEXT NOT NULL UNIQUE,
                registered_at_utc TEXT NOT NULL,
                runtime_family_id TEXT NOT NULL,
                protocol_digest TEXT NOT NULL,
                change_id TEXT NOT NULL,
                baseline_commit TEXT NOT NULL,
                candidate_patch_sha256 TEXT NOT NULL,
                candidate_file TEXT NOT NULL,
                attempt_ordinal INTEGER NOT NULL,
                status TEXT NOT NULL CHECK(
                    status IN ('registered', 'promotion-ready', 'promotion-blocked')
                ),
                result_sha256 TEXT,
                CHECK(
                    (status = 'registered' AND result_sha256 IS NULL)
                    OR
                    (status IN ('promotion-ready', 'promotion-blocked')
                     AND length(result_sha256) = 64)
                )
            );
            CREATE UNIQUE INDEX attempts_family_ordinal
                ON attempts(runtime_family_id, attempt_ordinal);

            CREATE TABLE improve_authorizations (
                sequence INTEGER PRIMARY KEY AUTOINCREMENT,
                consumption_id TEXT NOT NULL UNIQUE,
                authorization_digest TEXT NOT NULL UNIQUE CHECK(length(authorization_digest) = 64),
                lease_id TEXT NOT NULL UNIQUE,
                consumed_at_utc TEXT NOT NULL,
                purpose TEXT NOT NULL CHECK(purpose = 'improve-agent'),
                max_cycles INTEGER NOT NULL CHECK(max_cycles = 1),
                source_commit TEXT NOT NULL,
                source_tree TEXT NOT NULL,
                change_id TEXT NOT NULL,
                deployment_id TEXT NOT NULL,
                session_id TEXT NOT NULL,
                account TEXT NOT NULL,
                control_checkout TEXT NOT NULL,
                control_head TEXT NOT NULL,
                control_tree TEXT NOT NULL
            );

            CREATE TRIGGER metadata_immutable
            BEFORE UPDATE ON metadata
            BEGIN SELECT RAISE(ABORT, 'ledger metadata is immutable'); END;
            CREATE TRIGGER metadata_no_delete
            BEFORE DELETE ON metadata
            BEGIN SELECT RAISE(ABORT, 'ledger metadata cannot be deleted'); END;
            CREATE TRIGGER attempts_insert_registered_only
            BEFORE INSERT ON attempts
            WHEN NEW.status <> 'registered' OR NEW.result_sha256 IS NOT NULL
            BEGIN SELECT RAISE(ABORT, 'attempts must begin registered'); END;
            CREATE TRIGGER attempts_no_delete
            BEFORE DELETE ON attempts
            BEGIN SELECT RAISE(ABORT, 'attempt history cannot be deleted'); END;
            CREATE TRIGGER attempts_forward_only
            BEFORE UPDATE ON attempts
            BEGIN
                SELECT CASE WHEN
                    OLD.sequence IS NOT NEW.sequence OR
                    OLD.attempt_id IS NOT NEW.attempt_id OR
                    OLD.ledger_id IS NOT NEW.ledger_id OR
                    OLD.run_id IS NOT NEW.run_id OR
                    OLD.registered_at_utc IS NOT NEW.registered_at_utc OR
                    OLD.runtime_family_id IS NOT NEW.runtime_family_id OR
                    OLD.protocol_digest IS NOT NEW.protocol_digest OR
                    OLD.change_id IS NOT NEW.change_id OR
                    OLD.baseline_commit IS NOT NEW.baseline_commit OR
                    OLD.candidate_patch_sha256 IS NOT NEW.candidate_patch_sha256 OR
                    OLD.candidate_file IS NOT NEW.candidate_file OR
                    OLD.attempt_ordinal IS NOT NEW.attempt_ordinal
                THEN RAISE(ABORT, 'attempt identity and family budget are immutable') END;
                SELECT CASE WHEN NOT (
                    OLD.status = 'registered' AND
                    OLD.result_sha256 IS NULL AND
                    NEW.status IN ('promotion-ready', 'promotion-blocked') AND
                    length(NEW.result_sha256) = 64
                ) THEN RAISE(ABORT, 'attempt finalization must move forward exactly once') END;
            END;
            CREATE TRIGGER improve_authorizations_immutable
            BEFORE UPDATE ON improve_authorizations
            BEGIN SELECT RAISE(ABORT, 'improve authorization history is immutable'); END;
            CREATE TRIGGER improve_authorizations_no_delete
            BEFORE DELETE ON improve_authorizations
            BEGIN SELECT RAISE(ABORT, 'improve authorization history cannot be deleted'); END;
            """
        )
        connection.executemany(
            "INSERT INTO metadata(key, value) VALUES (?, ?)",
            (
                ("schemaVersion", LEDGER_SCHEMA_VERSION),
                ("ledgerId", normalized_id),
                ("createdAtUtc", _utc_now()),
                *_metadata_for_authority(authority),
            ),
        )
        connection.commit()
        connection.close()
        connection = None
        validation = open_evaluation_ledger(
            ledger_path,
            normalized_id,
            authority=authority,
            writable=True,
            verify_authority_file=False,
        )
        validation.close()
    except Exception:
        if connection is not None:
            connection.close()
        for candidate in (ledger_path, Path(f"{ledger_path}-wal"), Path(f"{ledger_path}-shm")):
            try:
                candidate.unlink()
            except FileNotFoundError:
                pass
        raise
    return {"ledgerPath": str(ledger_path), "ledgerId": normalized_id, "created": True}


def consume_improve_authorization(
    *,
    ledger_path: Path | str,
    ledger_id: str,
    authorization_digest: str,
    lease_id: str,
    source_commit: str,
    source_tree: str,
    change_id: str,
    deployment_id: str,
    session_id: str,
    account: str,
    control_checkout: Path | str,
    control_head: str,
    control_tree: str,
    max_cycles: int,
    authority: LedgerAuthority | None = None,
) -> dict[str, Any]:
    """Durably consume one DEKU-signed improve lease before candidate behavior."""

    digest = str(authorization_digest or "").strip().lower()
    normalized_lease_id = str(lease_id or "").strip()
    if len(digest) != 64 or any(character not in "0123456789abcdef" for character in digest):
        raise ValueError("improve authorization digest must be a SHA-256")
    if not normalized_lease_id:
        raise ValueError("improve authorization lease ID is required")
    if int(max_cycles) != 1:
        raise ValueError("an improve authorization must cover exactly one cycle")

    identity = {
        "sourceCommit": str(source_commit or "").strip().lower(),
        "sourceTree": str(source_tree or "").strip().lower(),
        "changeId": str(change_id or "").strip(),
        "deploymentId": str(deployment_id or "").strip(),
        "sessionId": str(session_id or "").strip(),
        "account": str(account or "").strip(),
        "controlHead": str(control_head or "").strip().lower(),
        "controlTree": str(control_tree or "").strip().lower(),
    }
    missing = [key for key, value in identity.items() if not value]
    if missing:
        raise ValueError("improve authorization identity is incomplete: " + ", ".join(missing))
    for key in ("sourceCommit", "sourceTree", "controlHead", "controlTree"):
        value = identity[key]
        if len(value) not in {40, 64} or any(character not in "0123456789abcdef" for character in value):
            raise ValueError(f"improve authorization {key} is not a Git object ID")
    if identity["sourceCommit"] != identity["controlHead"]:
        raise ValueError("improve authorization sourceCommit does not bind the control checkout HEAD")
    if identity["sourceTree"] != identity["controlTree"]:
        raise ValueError("improve authorization sourceTree does not bind the control checkout tree")
    checkout = canonical_path(control_checkout)
    if not checkout.is_dir():
        raise ValueError("improve control checkout is unavailable")

    connection = open_evaluation_ledger(
        ledger_path,
        ledger_id,
        authority=authority,
        writable=True,
    )
    consumption_id = "improve-" + uuid.uuid4().hex
    consumed_at = _utc_now()
    try:
        connection.execute("BEGIN IMMEDIATE")
        cursor = connection.execute(
            """
            INSERT INTO improve_authorizations(
                consumption_id, authorization_digest, lease_id, consumed_at_utc,
                purpose, max_cycles, source_commit, source_tree, change_id,
                deployment_id, session_id, account, control_checkout,
                control_head, control_tree
            ) VALUES (?, ?, ?, ?, 'improve-agent', 1, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                consumption_id,
                digest,
                normalized_lease_id,
                consumed_at,
                identity["sourceCommit"],
                identity["sourceTree"],
                identity["changeId"],
                identity["deploymentId"],
                identity["sessionId"],
                identity["account"],
                str(checkout),
                identity["controlHead"],
                identity["controlTree"],
            ),
        )
        sequence = int(cursor.lastrowid)
        connection.commit()
        return {
            "schemaVersion": "fouler-improve-authorization-consumption/v1",
            "consumed": True,
            "consumptionId": consumption_id,
            "sequence": sequence,
            "authorizationDigest": digest,
            "leaseId": normalized_lease_id,
            "consumedAtUtc": consumed_at,
            "purpose": "improve-agent",
            "maxCycles": 1,
            "controlCheckout": str(checkout),
            "controlHead": identity["controlHead"],
            "controlTree": identity["controlTree"],
        }
    except sqlite3.IntegrityError as exc:
        connection.rollback()
        existing = connection.execute(
            """
            SELECT sequence, consumption_id, authorization_digest, lease_id, consumed_at_utc
            FROM improve_authorizations
            WHERE authorization_digest = ? OR lease_id = ?
            ORDER BY sequence LIMIT 1
            """,
            (digest, normalized_lease_id),
        ).fetchone()
        return {
            "schemaVersion": "fouler-improve-authorization-consumption/v1",
            "consumed": False,
            "blocker": "signed improve authorization was already consumed",
            "authorizationDigest": digest,
            "leaseId": normalized_lease_id,
            "existingSequence": int(existing["sequence"]) if existing is not None else None,
            "existingConsumptionId": existing["consumption_id"] if existing is not None else None,
            "existingConsumedAtUtc": existing["consumed_at_utc"] if existing is not None else None,
            "detail": str(exc),
        }
    except Exception:
        connection.rollback()
        raise
    finally:
        connection.close()


def initialize_ledger_authority(
    *,
    ledger_id: str,
    ledger_path: Path | str | None = None,
    authority_path: Path | str | None = None,
) -> LedgerAuthority:
    """Provision a read-only local evidence pointer and SQLite mirror exactly once."""
    configured_authority_path = canonical_path(authority_path or DEFAULT_AUTHORITY_PATH)
    configured_ledger_path = canonical_path(ledger_path or DEFAULT_LEDGER_PATH)
    normalized_id = _validate_ledger_id(ledger_id)
    if configured_authority_path == configured_ledger_path:
        raise ValueError("authority and ledger paths must be different")
    configured_authority_path.parent.mkdir(parents=True, exist_ok=True)
    descriptor = os.open(
        configured_authority_path,
        os.O_CREAT | os.O_EXCL | os.O_WRONLY,
        0o400,
    )
    authority = LedgerAuthority(
        authority_path=configured_authority_path,
        ledger_path=configured_ledger_path,
        ledger_id=normalized_id,
        authority_id=uuid.uuid4().hex,
        created_at_utc=_utc_now(),
        authority_digest="",
    )
    payload = _authority_payload(
        authority_path=authority.authority_path,
        ledger_path=authority.ledger_path,
        ledger_id=authority.ledger_id,
        authority_id=authority.authority_id,
        created_at_utc=authority.created_at_utc,
    )
    authority = LedgerAuthority(
        authority_path=authority.authority_path,
        ledger_path=authority.ledger_path,
        ledger_id=authority.ledger_id,
        authority_id=authority.authority_id,
        created_at_utc=authority.created_at_utc,
        authority_digest=str(payload["authorityDigest"]),
    )
    ledger_created = False
    try:
        initialize_evaluation_ledger(
            configured_ledger_path,
            normalized_id,
            authority=authority,
        )
        ledger_created = True
        encoded = (json.dumps(payload, indent=2, ensure_ascii=True) + "\n").encode("utf-8")
        written = 0
        while written < len(encoded):
            written += os.write(descriptor, encoded[written:])
        os.fsync(descriptor)
        os.close(descriptor)
        descriptor = -1
        os.chmod(configured_authority_path, 0o444)
        return load_ledger_authority(configured_authority_path)
    except Exception:
        if descriptor >= 0:
            os.close(descriptor)
        if ledger_created:
            for candidate in (
                configured_ledger_path,
                Path(f"{configured_ledger_path}-wal"),
                Path(f"{configured_ledger_path}-shm"),
            ):
                try:
                    candidate.unlink()
                except FileNotFoundError:
                    pass
        try:
            os.chmod(configured_authority_path, 0o600)
        except FileNotFoundError:
            pass
        try:
            configured_authority_path.unlink()
        except FileNotFoundError:
            pass
        raise
