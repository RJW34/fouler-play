import json

import pytest

from infrastructure import eval_stats, offline_eval


def test_wilson_lower_bound_empty_sample_is_fail_closed_zero():
    assert eval_stats.wilson_lower_bound(0, 0) == 0.0


def test_wilson_lower_bound_matches_selfplay_gate_expectations():
    assert eval_stats.wilson_lower_bound(25, 50) < 0.50
    assert eval_stats.wilson_lower_bound(34, 50) > 0.50
    assert eval_stats.wilson_lower_bound(4, 4) > 0.50


def test_two_proportion_z_returns_two_sided_p_value():
    z, p = eval_stats.two_proportion_z(34, 50, 25, 50)
    assert z > 0
    assert 0 < p < 0.10

    z0, p0 = eval_stats.two_proportion_z(25, 50, 25, 50)
    assert z0 == 0.0
    assert p0 == 1.0


def test_eval_stats_rejects_malformed_counts():
    with pytest.raises(ValueError):
        eval_stats.wilson_lower_bound(5, 4)
    with pytest.raises(ValueError):
        eval_stats.two_proportion_z(-1, 4, 1, 4)


def test_offline_eval_reexports_shared_statistics():
    assert offline_eval.wilson_lower_bound is eval_stats.wilson_lower_bound
    assert offline_eval.two_proportion_z is eval_stats.two_proportion_z


def test_offline_eval_compare_writes_fail_closed_verdict(tmp_path, monkeypatch):
    results = tmp_path / "offline"
    results.mkdir()
    (results / "frozen.json").write_text(
        json.dumps({"fouler_wins": 25, "battles": 50}),
        encoding="utf-8",
    )
    (results / "candidate.json").write_text(
        json.dumps({"fouler_wins": 34, "battles": 50}),
        encoding="utf-8",
    )
    monkeypatch.setattr(offline_eval, "RESULTS_DIR", results)

    verdict = offline_eval.compare_results("frozen", "candidate")

    assert verdict["candidate_wilson_lcb"] > 0.50
    assert verdict["ACCEPT"] is True
