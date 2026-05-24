#!/usr/bin/env python3
"""hypothesis_ledger — emit machine-readable hypothesis records from autoresearch.

A "hypothesis" is the formal closure-trackable form of an autoresearch issue:
    - id (stable across reruns; based on failure class + opened-at date)
    - failureClass (key from the issue, e.g. "hazard_pressure")
    - title, summary, recommendation (from the issue)
    - predictedChange ("the recommendation, if executed")
    - expectedEloDelta (operator-set or null; default null for now)
    - status: open | implemented | deployed | measured | kept | reverted
    - openedAt, closedAt
    - measurement: {batchAtClose, eloBefore, eloAfter, deltaELO, winRateDelta}

The HERMES-side watcher (`hermes-fouler-hypothesis-watcher`, future)
will read these and drive the lifecycle. For now this module just emits
hypothesis records so the lifecycle has *something to consume*.

Called automatically at the end of an autoresearch batch run via the
existing pipeline. Idempotent — same (failureClass, openedAt-date)
produces the same id, so a record is appended only once per failure class
per day.
"""
from __future__ import annotations
import hashlib, json, os
from datetime import datetime, timezone
from pathlib import Path

LEDGER_DIR = Path(os.environ.get("FOULER_HYPOTHESIS_LEDGER",
                                 os.path.expanduser("~/.hermes/operator/fouler-hypotheses")))


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def _today_utc() -> str:
    return datetime.now(timezone.utc).date().isoformat()


def hypothesis_id(failure_class: str, opened_date: str) -> str:
    """Stable per (class, date) so reruns the same day reuse the record."""
    h = hashlib.sha1(f"{failure_class}|{opened_date}".encode("utf-8")).hexdigest()[:10]
    return f"fouler-hypo-{opened_date}-{failure_class}-{h}"


def emit_from_issue(issue: dict, batch_meta: dict) -> Path | None:
    """Write a hypothesis record for one issue. Returns the path written or None
    if the record already exists (idempotent no-op)."""
    if not issue or not issue.get("key"):
        return None
    LEDGER_DIR.mkdir(parents=True, exist_ok=True)
    opened = _today_utc()
    hid = hypothesis_id(issue["key"], opened)
    path = LEDGER_DIR / f"{hid}.json"
    if path.exists():
        # Update the lastObservedAt timestamp + bump observation count, leave
        # everything else alone (lifecycle owned by the watcher).
        try:
            record = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            record = {}
        record["lastObservedAt"] = _now_iso()
        record["observationCount"] = (record.get("observationCount") or 1) + 1
        path.write_text(json.dumps(record, indent=2, sort_keys=True), encoding="utf-8")
        return path
    record = {
        "schemaVersion": "fouler-hypothesis/v1",
        "id": hid,
        "failureClass": issue["key"],
        "title": issue.get("title") or issue["key"],
        "summary": issue.get("summary") or "",
        "recommendation": issue.get("recommendation") or "",
        "evidence": issue.get("proof") or [],
        "predictedChange": issue.get("recommendation") or "",
        "expectedEloDelta": None,
        "status": "open",
        "openedAt": _now_iso(),
        "openedDate": opened,
        "lastObservedAt": _now_iso(),
        "observationCount": 1,
        "batchMeta": batch_meta or {},
        "measurement": {
            "batchAtClose": None,
            "eloBefore": None,
            "eloAfter": None,
            "deltaELO": None,
            "winRateDelta": None,
        },
        "closedAt": None,
    }
    path.write_text(json.dumps(record, indent=2, sort_keys=True), encoding="utf-8")
    return path


def emit_from_autoresearch_output(autoresearch_data: dict) -> list[str]:
    """Convenience: given the dict written to autoresearch_latest.json,
    emit hypothesis records for the top issue (and optionally all issues).
    Returns the list of paths written or touched."""
    written = []
    batch_meta = {
        "batch": autoresearch_data.get("batch"),
        "windowSize": autoresearch_data.get("window_size"),
        "winRate": autoresearch_data.get("win_rate"),
        "generatedAt": autoresearch_data.get("generated_at"),
    }
    top = autoresearch_data.get("top_issue") or {}
    if top:
        p = emit_from_issue(top, batch_meta)
        if p:
            written.append(str(p))
    # also emit any high-score issues (score >= 8) so multi-class failures
    # get tracked, not just the top one.
    for issue in autoresearch_data.get("issues", []):
        if (issue.get("score") or 0) >= 8 and issue.get("key") != top.get("key"):
            p = emit_from_issue(issue, batch_meta)
            if p:
                written.append(str(p))
    return written


if __name__ == "__main__":
    # standalone: read autoresearch_latest.json and emit
    import sys
    src = Path(sys.argv[1]) if len(sys.argv) > 1 else Path(__file__).parent / "autoresearch_latest.json"
    if not src.exists():
        print(f"missing: {src}")
        sys.exit(1)
    data = json.loads(src.read_text(encoding="utf-8"))
    paths = emit_from_autoresearch_output(data)
    print(json.dumps({"written": paths, "count": len(paths)}, indent=2))
