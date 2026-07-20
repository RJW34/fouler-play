#!/usr/bin/env python3
"""The unconverted-loss burndown -- fouler-play's finite work queue.

WHY THIS EXISTS
---------------
"The learn-from-losses loop is closed" was being inferred from the hypothesis
ledger count moving 0 -> 3. That is satisfiable without any learning happening,
and it is: all three entries are the same failure class, from the same templated
detector, none has ever been tested, and the losses they cite are a small
fraction of the losses that occurred.

This script counts what is actually unconverted, in two directions:

  losses with no hypothesis      signal that was recorded and then dropped
  hypotheses never tested/applied hypotheses that were raised and then dropped

Both go to zero. Zero means every loss either taught the bot something or was
explicitly triaged, and every hypothesis raised was carried through to a
kept/reverted verdict. It is the same shape as PokeCompletionist's
route_resolution_audit: a countable list derived from a measurement, not from
authored prose.

THE JOIN
--------
There is no first-class join field. `games[].failureClasses` exists in the ELO
proof and is the intended link, but it is empty for every game. The usable join
is the hypothesis evidence line, which `hypothesis_ledger.py` formats as
`f"{battle_id}: {detail}"` -- so split on the first ": " and match against
`battle_stats.json` `battles[].battle_id`.

That the join has to be recovered by string-parsing is itself a finding, and
`--json` output records it so it can be fixed at the source.

READ-ONLY. Reads battle records and hypothesis files. Touches no running batch.

Usage:
    python scripts/loss_hypothesis_burndown.py
    python scripts/loss_hypothesis_burndown.py --state-root /path/to/fouler/state
    python scripts/loss_hypothesis_burndown.py --json out.json
    python scripts/loss_hypothesis_burndown.py --strict
"""
from __future__ import annotations

import argparse
import json
import os
import sys
from collections import Counter
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]

SCHEMA_VERSION = "fouler-play-loss-hypothesis-burndown/v1"

# Terminal states from replay_analysis/hypothesis_ledger.py. A hypothesis that
# has not reached one of these has not been carried through to a verdict.
TERMINAL_STATES = ("kept", "reverted")
TESTED_STATES = ("measured", *TERMINAL_STATES)


def default_state_root() -> Path:
    env = os.environ.get("FOULER_STATE_ROOT")
    if env:
        return Path(env)
    if os.name == "nt":
        return Path(r"C:\ProgramData\HERMES\state\fouler")
    return Path.home() / ".hermes" / "state" / "fouler"


def load_battles(state_root: Path) -> tuple[list[dict[str, Any]], Path | None]:
    """Battle records as the ladder runner wrote them -- the external observation."""
    for candidate in (state_root / "battle_stats.json",
                      state_root / "truth" / "battle_stats.json"):
        if candidate.is_file():
            try:
                payload = json.loads(candidate.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError):
                continue
            battles = payload.get("battles") if isinstance(payload, dict) else payload
            if isinstance(battles, list):
                return [b for b in battles if isinstance(b, dict)], candidate
    return [], None


def load_hypotheses(state_root: Path, extra_dirs: list[Path]) -> list[dict[str, Any]]:
    directories = [state_root / "learning" / "hypotheses", *extra_dirs]
    seen: dict[str, dict[str, Any]] = {}
    for directory in directories:
        if not directory.is_dir():
            continue
        for path in sorted(directory.glob("*.json")):
            try:
                payload = json.loads(path.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError):
                continue
            if not isinstance(payload, dict) or not payload.get("id"):
                continue
            payload.setdefault("__source", str(path))
            # Later directories win; the consumer copy is the one the closer drives.
            seen[str(payload["id"])] = payload
    return list(seen.values())


def cited_battle_ids(hypothesis: dict[str, Any]) -> set[str]:
    """Recover battle ids from evidence lines formatted `<battle_id>: <detail>`."""
    out: set[str] = set()
    for line in hypothesis.get("evidence") or []:
        if not isinstance(line, str):
            continue
        head = line.split(": ", 1)[0].strip()
        if head and head != "unknown":
            out.add(head)
    for key in ("battleIds", "battle_ids", "citedBattleIds"):
        raw = hypothesis.get(key)
        if isinstance(raw, list):
            out.update(str(item) for item in raw if item)
    return out


def build_burndown(state_root: Path, extra_hypothesis_dirs: list[Path]) -> dict[str, Any]:
    battles, battles_path = load_battles(state_root)
    hypotheses = load_hypotheses(state_root, extra_hypothesis_dirs)

    losses = [b for b in battles if str(b.get("result")).lower() == "loss"]
    loss_ids = {str(b.get("battle_id")) for b in losses if b.get("battle_id")}

    cited: set[str] = set()
    citation_owner: dict[str, list[str]] = {}
    for hypothesis in hypotheses:
        for battle_id in cited_battle_ids(hypothesis):
            cited.add(battle_id)
            citation_owner.setdefault(battle_id, []).append(str(hypothesis.get("id")))

    uncited = sorted(loss_ids - cited)
    cited_not_in_store = sorted(cited - loss_ids)

    unconverted_hypotheses = []
    for hypothesis in hypotheses:
        status = str(hypothesis.get("status") or "").lower()
        measurement = hypothesis.get("measurement") or {}
        measured = any(v is not None for v in measurement.values()) if isinstance(
            measurement, dict) else False
        if status in TERMINAL_STATES and measured:
            continue
        reasons = []
        if status not in TERMINAL_STATES:
            reasons.append(f"status={status or 'unset'} is not terminal "
                           f"({'/'.join(TERMINAL_STATES)})")
        if not measured:
            reasons.append("measurement block is entirely null — never tested")
        if hypothesis.get("closedAt") is None:
            reasons.append("closedAt is null — never closed")
        unconverted_hypotheses.append({
            "id": hypothesis.get("id"),
            "failureClass": hypothesis.get("failureClass"),
            "status": status or None,
            "openedAt": hypothesis.get("openedAt"),
            "citedLosses": len(cited_battle_ids(hypothesis)),
            "reasons": reasons,
            "source": hypothesis.get("__source"),
        })

    by_class = Counter(str(h.get("failureClass")) for h in hypotheses)

    return {
        "schemaVersion": SCHEMA_VERSION,
        "stateRoot": str(state_root),
        "battleStore": str(battles_path) if battles_path else None,
        "joinMethod": (
            "string-parsed from hypothesis.evidence[] prefix before ': '. "
            "games[].failureClasses is the intended first-class join field and is "
            "empty for every game; populating it would remove this parse."
        ),
        "counts": {
            "battles": len(battles),
            "losses": len(losses),
            "lossesWithHypothesis": len(loss_ids & cited),
            "lossesWithoutHypothesis": len(uncited),
            "hypotheses": len(hypotheses),
            "hypothesesUnconverted": len(unconverted_hypotheses),
            "citedButNotInBattleStore": len(cited_not_in_store),
            "totalBurndown": len(uncited) + len(unconverted_hypotheses),
        },
        "failureClassHistogram": dict(by_class),
        "lossesWithoutHypothesis": uncited,
        "hypothesesUnconverted": unconverted_hypotheses,
        "citedButNotInBattleStore": cited_not_in_store,
    }


def main() -> int:
    ap = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--state-root", default=str(default_state_root()),
                    help="fouler runtime state root (rule 5: check the runtime, not the repo)")
    ap.add_argument("--hypothesis-dir", action="append", default=[],
                    help="extra hypothesis ledger directory (repeatable)")
    ap.add_argument("--json", dest="json_out")
    ap.add_argument("--strict", action="store_true", help="exit 1 if the burndown is non-zero")
    args = ap.parse_args()

    report = build_burndown(Path(args.state_root),
                            [Path(p) for p in args.hypothesis_dir])
    counts = report["counts"]

    print(f"\n=== FOULER LOSS-HYPOTHESIS BURNDOWN ===")
    print(f"  state root   : {report['stateRoot']}")
    print(f"  battle store : {report['battleStore'] or 'NOT FOUND'}")
    print(f"\n  battles                     : {counts['battles']}")
    print(f"  losses                      : {counts['losses']}")
    print(f"  losses WITH a hypothesis    : {counts['lossesWithHypothesis']}")
    print(f"  losses WITHOUT a hypothesis : {counts['lossesWithoutHypothesis']}")
    print(f"  hypotheses                  : {counts['hypotheses']}")
    print(f"  hypotheses NEVER tested     : {counts['hypothesesUnconverted']}")

    if report["failureClassHistogram"]:
        print("\n  hypotheses by failure class:")
        for name, count in sorted(report["failureClassHistogram"].items()):
            print(f"    {name:<40} {count}")

    if report["hypothesesUnconverted"]:
        print("\n  --- unconverted hypotheses ---")
        for row in report["hypothesesUnconverted"]:
            print(f"    {row['id']}")
            for reason in row["reasons"]:
                print(f"        {reason}")

    if counts["citedButNotInBattleStore"]:
        print(f"\n  WARNING {counts['citedButNotInBattleStore']} cited battle id(s) "
              f"are not in the battle store — the join may be degrading")

    print(f"\nTOTAL UNCONVERTED: {counts['totalBurndown']}")

    if args.json_out:
        Path(args.json_out).write_text(json.dumps(report, indent=2), encoding="utf-8")
        print(f"wrote {args.json_out}")

    if args.strict and counts["totalBurndown"]:
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
