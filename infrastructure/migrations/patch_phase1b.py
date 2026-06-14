#!/usr/bin/env python3
"""Phase 1b patcher: add the dense per-decision REGRET pre-gate to
infrastructure/improve_agent.py's offline_eval_gate().

The regret gate runs FIRST (cheap, deterministic, transfer-valid): it scores the
candidate's decisive-MCTS override rate on a frozen loss-position suite and
rejects a change that overrides the search MORE than the incumbent, BEFORE
spending hours on self-play. It degrades to SKIP (never blocks) when the suite
or candidate traces are absent. The self-play LCB gate remains as the secondary
outcome check. Idempotent + anchored.  Run on JIGGLY:  python patch_phase1b.py [--check]
"""
from __future__ import annotations
import ast, sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent
AGENT = ROOT / "infrastructure" / "improve_agent.py"

ANCHOR = '''    if not EVAL_GATE_ENABLED:
        return True, {"skipped": "IMPROVE_AGENT_EVAL_GATE disabled"}

    if EVAL_GATE_MODE == "selfplay":
        return selfplay_eval_gate()'''

REPLACE = '''    if not EVAL_GATE_ENABLED:
        return True, {"skipped": "IMPROVE_AGENT_EVAL_GATE disabled"}

    # PHASE 1b: dense per-decision REGRET pre-gate. Cheap, deterministic, and
    # transfer-valid -- it measures whether the candidate stops OVERRIDING a
    # decisive MCTS search (Root #1) on a frozen suite of real loss positions,
    # which the noisy mirror self-play cannot see. Runs BEFORE the heavy
    # self-play so an obviously-worse change is rejected in seconds. Skips (does
    # not block) when the suite/candidate traces are unavailable.
    if str(os.getenv("FOULER_REGRET_GATE", "1")).lower() not in {"0", "false", "no", "off"}:
        try:
            from infrastructure.decision_regret import regret_gate
            import time as _time
            _accept, _detail = regret_gate(since_epoch=_REGRET_GATE_SINCE)
            print(f"[AGENT] regret pre-gate: ACCEPT={_accept} :: {json.dumps(_detail)[:500]}")
            if not _accept:
                return False, {"regret_gate": _detail,
                               "rule": "rejected by decision-regret pre-gate"}
        except Exception as _rg_exc:
            print(f"[AGENT] regret pre-gate errored ({_rg_exc}); continuing to self-play.")

    if EVAL_GATE_MODE == "selfplay":
        return selfplay_eval_gate()'''

# We also need _REGRET_GATE_SINCE set at the start of the eval gate so "recent
# traces" means traces produced during THIS candidate's evaluation. Set it when
# the module imports (process start ~= candidate eval window) as a safe default.
ANCHOR2 = 'def offline_eval_gate() -> tuple[bool, dict]:'
REPLACE2 = '''# Epoch marking the start of the candidate evaluation window; traces written
# after this are attributable to the candidate. Set at import (process start).
import time as _t_for_regret
_REGRET_GATE_SINCE = _t_for_regret.time()


def offline_eval_gate() -> tuple[bool, dict]:'''


def main() -> int:
    check = "--check" in sys.argv
    src = AGENT.read_text(encoding="utf-8")
    if "_REGRET_GATE_SINCE" in src and "regret pre-gate" in src:
        print("[phase1b] already applied (idempotent no-op).")
        return 0
    if ANCHOR not in src:
        print("[phase1b] ERROR: eval-gate dispatch anchor not found.", file=sys.stderr)
        return 11
    if ANCHOR2 not in src:
        print("[phase1b] ERROR: offline_eval_gate def anchor not found.", file=sys.stderr)
        return 12
    src = src.replace(ANCHOR2, REPLACE2, 1)
    src = src.replace(ANCHOR, REPLACE, 1)
    try:
        ast.parse(src)
    except SyntaxError as e:
        print(f"[phase1b] ERROR: patched source does not parse: {e}", file=sys.stderr)
        return 13
    if check:
        print("[phase1b] CHECK ok: would add regret pre-gate + since-epoch.")
        return 0
    AGENT.write_text(src, encoding="utf-8")
    print("[phase1b] applied: regret pre-gate wired into offline_eval_gate.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
