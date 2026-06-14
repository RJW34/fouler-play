#!/usr/bin/env python3
"""Phase 0 patcher: wire flatness-gated blending into fp/search/main.py.

Idempotent + anchored on exact source text. Run ON JIGGLY from repo root:
    python patch_phase0.py            # apply
    python patch_phase0.py --check    # report only, no write
Exits non-zero if an anchor is missing (so a stale file never silently no-ops).
"""
from __future__ import annotations

import io
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent
MAIN = ROOT / "fp" / "search" / "main.py"

# Anchor 1: the import block region near the top — add flatness import + flag.
IMPORT_ANCHOR = "MCTS_EVAL_BLEND_ALPHA = max(\n    0.0,\n    min(1.0, float(os.getenv(\"MCTS_EVAL_BLEND_ALPHA\", \"0.35\"))),\n)"
IMPORT_ADD = IMPORT_ANCHOR + """

# PHASE 0 (flatness-gated blend): when enabled, the MCTS weight is chosen
# per-turn from the MCTS visit-policy flatness instead of a fixed 0.35. MCTS
# leads when its search is decisive; the eval only takes over when MCTS is
# genuinely flat. Env-gated so it is an instant, reversible A/B.
FOULER_FLATNESS_GATED_BLEND = str(
    os.getenv("FOULER_FLATNESS_GATED_BLEND", "1")
).lower() not in {"0", "false", "no", "off"}
try:
    from fp.search.flatness import flatness_gated_alpha as _flatness_gated_alpha
except Exception:  # pragma: no cover - defensive
    _flatness_gated_alpha = None"""

# Anchor 2: the blend call site — choose alpha per-turn.
BLEND_ANCHOR = """                if eval_blend_scores:
                    blended = _blend_eval_mcts_policy(
                        eval_blend_scores,
                        mcts_policy,
                        alpha=MCTS_EVAL_BLEND_ALPHA,
                    )
                    if blended:
                        decision_policy = blended
                        trace["eval_blend"] = {
                            "alpha_mcts": MCTS_EVAL_BLEND_ALPHA,"""
BLEND_REPLACE = """                if eval_blend_scores:
                    _alpha_mcts = MCTS_EVAL_BLEND_ALPHA
                    _flat_meta = None
                    if FOULER_FLATNESS_GATED_BLEND and _flatness_gated_alpha is not None:
                        try:
                            _alpha_mcts, _flat_meta = _flatness_gated_alpha(mcts_policy)
                        except Exception as _fl_exc:  # pragma: no cover
                            logger.warning("Flatness gate failed, using fixed alpha: %s", _fl_exc)
                            _alpha_mcts = MCTS_EVAL_BLEND_ALPHA
                    blended = _blend_eval_mcts_policy(
                        eval_blend_scores,
                        mcts_policy,
                        alpha=_alpha_mcts,
                    )
                    if blended:
                        decision_policy = blended
                        trace["eval_blend"] = {
                            "alpha_mcts": _alpha_mcts,
                            "flatness": _flat_meta,"""


def main() -> int:
    check = "--check" in sys.argv
    src = MAIN.read_text(encoding="utf-8")
    orig = src
    changed = []

    if "_flatness_gated_alpha" in src and "FOULER_FLATNESS_GATED_BLEND" in src and '"flatness": _flat_meta,' in src:
        print("[phase0] already applied (idempotent no-op).")
        return 0

    if IMPORT_ANCHOR not in src:
        print("[phase0] ERROR: import anchor not found.", file=sys.stderr)
        return 11
    if BLEND_ANCHOR not in src:
        print("[phase0] ERROR: blend-call anchor not found.", file=sys.stderr)
        return 12

    src = src.replace(IMPORT_ANCHOR, IMPORT_ADD, 1)
    changed.append("import+flag")
    src = src.replace(BLEND_ANCHOR, BLEND_REPLACE, 1)
    changed.append("blend-call")

    # sanity: result must still compile
    import ast
    try:
        ast.parse(src)
    except SyntaxError as e:
        print(f"[phase0] ERROR: patched source does not parse: {e}", file=sys.stderr)
        return 13

    if check:
        print(f"[phase0] CHECK ok: would apply {changed}.")
        return 0

    MAIN.write_text(src, encoding="utf-8")
    print(f"[phase0] applied {changed}; main.py {orig.count(chr(10))+1} -> {src.count(chr(10))+1} lines.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
