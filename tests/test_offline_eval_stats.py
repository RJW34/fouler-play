"""Unit tests for the offline-eval acceptance statistics (the real gate's math)."""
import importlib.util
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
spec = importlib.util.spec_from_file_location(
    "offline_eval", ROOT / "infrastructure" / "offline_eval.py"
)
offline_eval = importlib.util.module_from_spec(spec)
spec.loader.exec_module(offline_eval)


def test_wilson_lower_bound_basic():
    # 100% over a tiny sample is NOT confidently > 0.5
    assert offline_eval.wilson_lower_bound(2, 2) < 0.5
    # 100% over a large sample IS confidently > 0.5
    assert offline_eval.wilson_lower_bound(200, 200) > 0.5
    # coin flip centers below 0.5 on the lower bound
    assert offline_eval.wilson_lower_bound(100, 200) < 0.5
    # empty sample -> 0
    assert offline_eval.wilson_lower_bound(0, 0) == 0.0


def test_wilson_monotonic_in_n():
    # Same win-rate, larger n -> tighter (higher) lower bound
    lo_small = offline_eval.wilson_lower_bound(15, 20)
    lo_big = offline_eval.wilson_lower_bound(150, 200)
    assert lo_big > lo_small


def test_two_proportion_z_significant():
    # 80% vs 50% over 200 each should be significant (p < 0.05)
    z, p = offline_eval.two_proportion_z(160, 200, 100, 200)
    assert z > 0
    assert p < 0.05


def test_two_proportion_z_not_significant():
    # 52% vs 50% over 40 each: not significant
    z, p = offline_eval.two_proportion_z(21, 40, 20, 40)
    assert p > 0.05


def test_two_proportion_z_empty():
    z, p = offline_eval.two_proportion_z(0, 0, 0, 0)
    assert p == 1.0
