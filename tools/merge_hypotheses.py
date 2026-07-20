#!/usr/bin/env python3
"""merge_hypotheses.py — merge incoming JIGGLY hypothesis records into the
closer's ledger WITHOUT destroying lifecycle state.

WHY THIS EXISTS (2026-07-20)
    sync-fouler-from-jiggly.sh used to scp JIGGLY's hypothesis records straight
    over ~/.hermes/operator/fouler-hypotheses/. The producer (autoresearch on
    JIGGLY) only ever writes status "open". The consumer (the closer, on DEKU)
    writes "implemented"/"kept"/"reverted" into the SAME files. Since the sync
    timer runs every ~5 minutes and the closer every ~31, every transition the
    closer made was overwritten back to "open" within minutes.

    Observed directly: the closer's latest.json at 17:19 recorded all four
    records transitioning "open -> implemented"; at 17:35 the sync ran and all
    four files were back to status "open" with mtime 17:35. The learn loop could
    never advance past its first step, and the closer re-reported the same
    transition forever.

OWNERSHIP SPLIT
    producer-owned (JIGGLY, always taken from incoming):
        title, summary, recommendation, evidence, predictedChange, batchMeta,
        lastObservedAt, observationCount, mechanicalMetric
    closer-owned (DEKU, never overwritten once set):
        status, implementation, measurement, closedAt, closeNote

New records are taken wholesale.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

PRODUCER_FIELDS = {
    "title", "summary", "recommendation", "evidence", "predictedChange",
    "batchMeta", "lastObservedAt", "observationCount", "mechanicalMetric",
    "expectedEloDelta",
}
CLOSER_FIELDS = {
    "status", "implementation", "measurement", "closedAt", "closeNote",
}


def merge_one(incoming_path: Path, dest_path: Path) -> str:
    try:
        incoming = json.loads(incoming_path.read_text(encoding="utf-8"))
    except Exception as exc:
        return f"skip (unparseable incoming): {exc}"

    if not dest_path.exists():
        dest_path.write_text(json.dumps(incoming, indent=2, sort_keys=True),
                             encoding="utf-8")
        return "new"

    try:
        local = json.loads(dest_path.read_text(encoding="utf-8"))
    except Exception:
        dest_path.write_text(json.dumps(incoming, indent=2, sort_keys=True),
                             encoding="utf-8")
        return "replaced (local unparseable)"

    merged = dict(local)
    for key, value in incoming.items():
        if key in CLOSER_FIELDS:
            continue  # never let the producer reset lifecycle state
        if key in PRODUCER_FIELDS or key not in merged:
            merged[key] = value

    if merged == local:
        return "unchanged"
    dest_path.write_text(json.dumps(merged, indent=2, sort_keys=True),
                         encoding="utf-8")
    return f"merged (kept status={local.get('status')})"


def main() -> int:
    if len(sys.argv) < 3:
        print("usage: merge_hypotheses.py <incoming_dir> <dest_dir>")
        return 2
    incoming_dir, dest_dir = Path(sys.argv[1]), Path(sys.argv[2])
    dest_dir.mkdir(parents=True, exist_ok=True)
    counts: dict[str, int] = {}
    for path in sorted(incoming_dir.glob("fouler-hypo-*.json")):
        outcome = merge_one(path, dest_dir / path.name)
        key = outcome.split(" (")[0]
        counts[key] = counts.get(key, 0) + 1
    print(json.dumps(counts, sort_keys=True))
    return 0


if __name__ == "__main__":
    sys.exit(main())
