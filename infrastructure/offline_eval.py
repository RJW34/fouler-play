#!/usr/bin/env python3
"""Legacy offline-eval compatibility entrypoint.

The recursive-improvement gate defaults to self-play. This module keeps the
shared eval statistics importable and provides comparison support for existing
offline result JSONs. It intentionally does not launch a live baseline eval; an
unsupported live invocation exits non-zero so callers fail closed.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

try:
    from infrastructure.eval_stats import two_proportion_z, wilson_lower_bound
except ModuleNotFoundError:  # pragma: no cover - direct script execution
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
    from infrastructure.eval_stats import two_proportion_z, wilson_lower_bound


PROJECT_ROOT = Path(__file__).resolve().parents[1]
RESULTS_DIR = PROJECT_ROOT / "eval_results" / "offline"


def _load_result(label: str) -> dict:
    path = RESULTS_DIR / f"{label}.json"
    if not path.exists():
        raise FileNotFoundError(f"offline eval result not found: {path}")
    return json.loads(path.read_text(encoding="utf-8"))


def _counts(result: dict) -> tuple[int, int]:
    if "fouler_wins" in result and "battles" in result:
        return int(result["fouler_wins"]), int(result["battles"])
    if "wins" in result and "battles" in result:
        return int(result["wins"]), int(result["battles"])
    if "fouler_win_rate" in result and "battles" in result:
        battles = int(result["battles"])
        return int(round(float(result["fouler_win_rate"]) * battles)), battles
    raise ValueError("offline eval result lacks win-count fields")


def compare_results(reference_label: str, candidate_label: str) -> dict:
    ref = _load_result(reference_label)
    cand = _load_result(candidate_label)
    ref_wins, ref_n = _counts(ref)
    cand_wins, cand_n = _counts(cand)
    z, p = two_proportion_z(cand_wins, cand_n, ref_wins, ref_n)
    cand_lcb = wilson_lower_bound(cand_wins, cand_n)
    accept = bool(
        cand_n
        and ref_n
        and cand_lcb > 0.50
        and (cand_wins / cand_n) > (ref_wins / ref_n)
    )
    return {
        "reference": reference_label,
        "candidate": candidate_label,
        "reference_wins": ref_wins,
        "reference_battles": ref_n,
        "candidate_wins": cand_wins,
        "candidate_battles": cand_n,
        "candidate_win_rate": round(cand_wins / cand_n, 4) if cand_n else 0.0,
        "candidate_wilson_lcb": round(cand_lcb, 4),
        "z": round(z, 3),
        "p_value": round(p, 4),
        "rule": "ACCEPT iff candidate Wilson LCB > 0.50 and candidate beats reference",
        "ACCEPT": accept,
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Fouler legacy offline eval shim")
    parser.add_argument("--compare", nargs=2, metavar=("REFERENCE", "CANDIDATE"))
    parser.add_argument("--label", default="candidate")
    parser.add_argument("--battles", type=int)
    parser.add_argument("--team")
    parser.add_argument("--baseline")
    args = parser.parse_args(argv)

    RESULTS_DIR.mkdir(parents=True, exist_ok=True)
    if args.compare:
        verdict = compare_results(args.compare[0], args.compare[1])
        out = RESULTS_DIR / f"compare-{args.compare[0]}-vs-{args.compare[1]}.json"
        out.write_text(json.dumps(verdict, indent=2), encoding="utf-8")
        print(json.dumps(verdict, indent=2))
        return 0

    print(
        "offline_eval.py no longer owns live baseline battles; use "
        "infrastructure/selfplay_eval.py or run_selfplay_burst.ps1. "
        "Failing closed without writing a candidate result.",
        file=sys.stderr,
    )
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
