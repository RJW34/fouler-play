# fp_flatness.py — shared helpers for flatness-gated MCTS/eval blending.
#
# ROOT-CAUSE FIX (play-strength). The live decision path blended the homegrown
# 1-ply static eval at 65% over the Rust MCTS visit policy at 35%
# (MCTS_EVAL_BLEND_ALPHA=0.35 is the *MCTS* weight). That fixed blend was
# justified by the in-code premise "the Rust MCTS returns a near-FLAT leaf
# value on EVERY turn, so its visit policy cannot separate moves." Runtime
# traces falsify the universality of that premise: MCTS is only *measurably*
# flat on a small minority of turns. On the majority of turns where MCTS DOES
# produce a decisive visit policy, letting a crude 1-ply eval outvote it is a
# strict strength regression — the signature of the bot's flat ~50% win-rate.
#
# Fix: measure MCTS flatness per turn from its own normalized visit policy and
# scale the blend so MCTS LEADS when it is decisive and the eval only takes
# over when MCTS is genuinely flat (the regime the original premise actually
# describes). This preserves the recent eval/KO-guard wins on flat turns while
# stopping the eval from sabotaging strong searches on the other ~95%.
from __future__ import annotations

import math
import os


def _f(name: str, default: float) -> float:
    try:
        return float(os.getenv(name, str(default)))
    except (TypeError, ValueError):
        return default


# Tunables (env-overridable for instant A/B + rollback).
# A normalized visit policy is "decisive" when its top move holds at least
# FLATNESS_TOP_DECISIVE of the mass; "flat" when the top move holds at most
# FLATNESS_TOP_FLAT. Between those we interpolate alpha linearly.
FLATNESS_TOP_FLAT = _f("FOULER_FLATNESS_TOP_FLAT", 0.52)
FLATNESS_TOP_DECISIVE = _f("FOULER_FLATNESS_TOP_DECISIVE", 0.65)
# MCTS weight (alpha) at the two ends. When flat, fall back to the historical
# eval-heavy blend (alpha low) so the eval/KO signal wins. When decisive, make
# MCTS primary (alpha high) so the strong search is not outvoted.
FLATNESS_ALPHA_WHEN_FLAT = _f("FOULER_FLATNESS_ALPHA_FLAT", 0.35)
FLATNESS_ALPHA_WHEN_DECISIVE = _f("FOULER_FLATNESS_ALPHA_DECISIVE", 0.85)


def mcts_top_mass(mcts_policy: dict) -> float:
    """Fraction of normalized visit mass on the single most-visited move.
    1.0 => fully decisive, ~1/N => fully flat (uniform)."""
    if not mcts_policy:
        return 0.0
    vals = []
    for v in mcts_policy.values():
        try:
            fv = float(v)
        except (TypeError, ValueError):
            continue
        if fv > 0:
            vals.append(fv)
    total = sum(vals)
    if total <= 0:
        return 0.0
    return max(vals) / total


def mcts_normalized_entropy(mcts_policy: dict) -> float:
    """Shannon entropy of the visit policy normalized to [0,1] (0=peaked,
    1=uniform). Provided for diagnostics / alternative flatness signals."""
    vals = []
    for v in (mcts_policy or {}).values():
        try:
            fv = float(v)
        except (TypeError, ValueError):
            continue
        if fv > 0:
            vals.append(fv)
    n = len(vals)
    if n <= 1:
        return 0.0
    total = sum(vals)
    if total <= 0:
        return 0.0
    h = 0.0
    for v in vals:
        p = v / total
        h -= p * math.log(p)
    return h / math.log(n)


def flatness_gated_alpha(mcts_policy: dict) -> tuple[float, dict]:
    """Return (alpha_mcts, telemetry).

    alpha_mcts is the MCTS weight to use in _blend_eval_mcts_policy:
      - MCTS decisive (top mass >= FLATNESS_TOP_DECISIVE) -> ALPHA_WHEN_DECISIVE
        (MCTS leads; the strong search is trusted).
      - MCTS flat (top mass <= FLATNESS_TOP_FLAT) -> ALPHA_WHEN_FLAT (eval leads;
        the eval/KO signal wins where the Rust policy is genuinely flat).
      - In between -> linear interpolation.
    """
    top = mcts_top_mass(mcts_policy)
    lo, hi = FLATNESS_TOP_FLAT, FLATNESS_TOP_DECISIVE
    a_flat, a_dec = FLATNESS_ALPHA_WHEN_FLAT, FLATNESS_ALPHA_WHEN_DECISIVE
    if hi <= lo:
        alpha = a_dec
        regime = "degenerate_thresholds"
    elif top <= lo:
        alpha = a_flat
        regime = "flat"
    elif top >= hi:
        alpha = a_dec
        regime = "decisive"
    else:
        frac = (top - lo) / (hi - lo)
        alpha = a_flat + frac * (a_dec - a_flat)
        regime = "mixed"
    alpha = max(0.0, min(1.0, alpha))
    return alpha, {
        "mcts_top_mass": round(top, 4),
        "regime": regime,
        "alpha_mcts": round(alpha, 4),
        "thresholds": {"flat": lo, "decisive": hi},
    }
