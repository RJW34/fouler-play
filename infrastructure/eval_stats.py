"""Shared statistics for Fouler eval gates.

The self-play gate and legacy offline-eval comparison use the same math so a
candidate is judged consistently no matter which harness produced the counts.
"""

from __future__ import annotations

import math
from statistics import NormalDist


def _validate_counts(successes: int, trials: int, *, label: str) -> tuple[int, int]:
    try:
        successes = int(successes)
        trials = int(trials)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"{label} counts must be integers") from exc
    if successes < 0 or trials < 0:
        raise ValueError(f"{label} counts must be non-negative")
    if successes > trials:
        raise ValueError(f"{label} successes cannot exceed trials")
    return successes, trials


def _z_for_confidence(confidence: float) -> float:
    try:
        confidence = float(confidence)
    except (TypeError, ValueError) as exc:
        raise ValueError("confidence must be numeric") from exc
    if not 0.0 < confidence < 1.0:
        raise ValueError("confidence must be between 0 and 1")
    return NormalDist().inv_cdf(0.5 + confidence / 2.0)


def wilson_lower_bound(wins: int, trials: int, confidence: float = 0.95) -> float:
    """Return the Wilson score lower bound for a binomial win rate.

    Empty samples have no evidence and therefore return 0.0. Invalid samples
    raise ValueError instead of silently clipping because eval gates must fail
    closed when their inputs are malformed.
    """

    wins, trials = _validate_counts(wins, trials, label="wilson")
    if trials == 0:
        return 0.0

    z = _z_for_confidence(confidence)
    p_hat = wins / trials
    denom = 1.0 + (z * z / trials)
    centre = p_hat + (z * z / (2.0 * trials))
    spread = z * math.sqrt(
        (p_hat * (1.0 - p_hat) + (z * z / (4.0 * trials))) / trials
    )
    return max(0.0, (centre - spread) / denom)


def two_proportion_z(
    successes_a: int,
    trials_a: int,
    successes_b: int,
    trials_b: int,
) -> tuple[float, float]:
    """Return (z, two-sided p-value) for two independent proportions."""

    successes_a, trials_a = _validate_counts(
        successes_a, trials_a, label="sample A"
    )
    successes_b, trials_b = _validate_counts(
        successes_b, trials_b, label="sample B"
    )
    if trials_a == 0 or trials_b == 0:
        return 0.0, 1.0

    p_a = successes_a / trials_a
    p_b = successes_b / trials_b
    pooled = (successes_a + successes_b) / (trials_a + trials_b)
    variance = pooled * (1.0 - pooled) * (1.0 / trials_a + 1.0 / trials_b)
    if variance <= 0.0:
        if p_a == p_b:
            return 0.0, 1.0
        z = math.copysign(math.inf, p_a - p_b)
        return z, 0.0

    z = (p_a - p_b) / math.sqrt(variance)
    p_value = math.erfc(abs(z) / math.sqrt(2.0))
    return z, p_value
