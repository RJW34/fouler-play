#!/usr/bin/env python3
from __future__ import annotations

import json
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_MAX_AGE_SECONDS = 24 * 60 * 60
TAIL_BYTES = 96 * 1024

CREDENTIAL_FAILURE_MARKERS = (
    ("wrong password", "showdown_credential_rejected"),
    ("login unsuccessful", "showdown_login_unsuccessful"),
    ("loginerror", "showdown_login_error"),
)

RUNTIME_LOGS = (
    "logs/devstream_battle_session.log",
    "logs/init.log",
)
SUCCESS_PROOF = "devstream/truth/showdown-login-proof.json"


def iso_from_epoch(value: float | None) -> str | None:
    if value is None:
        return None
    return datetime.fromtimestamp(value, tz=timezone.utc).isoformat()


def _tail_text(path: Path, max_bytes: int = TAIL_BYTES) -> str:
    with path.open("rb") as handle:
        try:
            handle.seek(0, 2)
            size = handle.tell()
            handle.seek(max(0, size - max_bytes))
        except OSError:
            handle.seek(0)
        return handle.read(max_bytes).decode("utf-8", errors="replace")


def _redact_line(line: str) -> str:
    lowered = line.lower()
    if "password" in lowered:
        return "Showdown login failed; credential was rejected."
    return line.strip()[:240]


def _parse_iso(value: Any) -> datetime | None:
    if not isinstance(value, str) or not value.strip():
        return None
    text = value.strip().replace("Z", "+00:00")
    try:
        parsed = datetime.fromisoformat(text)
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed


def latest_successful_login_proof(root: Path = ROOT) -> dict[str, Any]:
    path = root / SUCCESS_PROOF
    if not path.exists():
        return {"found": False, "path": SUCCESS_PROOF}
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except Exception as exc:
        return {"found": False, "path": SUCCESS_PROOF, "error": str(exc)}
    if not isinstance(payload, dict):
        return {"found": False, "path": SUCCESS_PROOF, "error": "proof is not a JSON object"}
    checked_at = _parse_iso(payload.get("checkedAt"))
    ok = bool(payload.get("ok") and payload.get("loginOk") and not payload.get("secretValuesPrinted"))
    return {
        "found": ok,
        "path": SUCCESS_PROOF,
        "checkedAt": checked_at.isoformat() if checked_at else None,
        "mtime": iso_from_epoch(path.stat().st_mtime),
        "ok": ok,
    }


def recent_showdown_credential_failure(
    root: Path = ROOT,
    *,
    max_age_seconds: int = DEFAULT_MAX_AGE_SECONDS,
) -> dict[str, Any]:
    """Return a small, secret-free summary of recent Showdown login failures."""
    now = time.time()
    scanned: list[dict[str, Any]] = []
    success_proof = latest_successful_login_proof(root)
    success_checked_at = _parse_iso(success_proof.get("checkedAt"))
    for rel in RUNTIME_LOGS:
        path = root / rel
        if not path.exists():
            scanned.append({"path": rel, "exists": False})
            continue
        mtime = path.stat().st_mtime
        age = now - mtime
        scanned.append({
            "path": rel,
            "exists": True,
            "mtime": iso_from_epoch(mtime),
            "ageSeconds": round(age, 3),
            "scanned": age <= max_age_seconds,
        })
        if age > max_age_seconds:
            continue
        tail = _tail_text(path)
        lowered = tail.lower()
        for marker, code in CREDENTIAL_FAILURE_MARKERS:
            if marker not in lowered:
                continue
            matching_lines = [line for line in tail.splitlines() if marker in line.lower()]
            line = matching_lines[-1] if matching_lines else ""
            if success_checked_at and success_checked_at.timestamp() >= mtime:
                return {
                    "found": False,
                    "clearedBy": success_proof,
                    "staleFailure": {
                        "code": code,
                        "path": rel,
                        "mtime": iso_from_epoch(mtime),
                        "ageSeconds": round(age, 3),
                    },
                    "scanned": scanned,
                }
            return {
                "found": True,
                "code": code,
                "path": rel,
                "mtime": iso_from_epoch(mtime),
                "ageSeconds": round(age, 3),
                "summary": _redact_line(line),
                "latestSuccessfulProof": success_proof,
                "scanned": scanned,
            }
    return {"found": False, "latestSuccessfulProof": success_proof, "scanned": scanned}
