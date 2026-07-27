"""Tests for the learn-and-climb fixes:
  - fp/search/flatness.py            (Phase 0: flatness-gated blend)
  - infrastructure/whole_function_edit.py  (Phase 1a: AST splice generator)
  - infrastructure/decision_regret.py      (Phase 1b: per-decision regret gate)
"""
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from fp.search import flatness as fl
from infrastructure import decision_regret as dr
from infrastructure import improve_agent as ia
from infrastructure import whole_function_edit as wfe


# ---------------- Phase 0: flatness ----------------


def test_whole_function_prompt_uses_observed_ladder_snapshot(monkeypatch):
    monkeypatch.setattr(
        ia,
        "_ladder_snapshot",
        lambda: {
            "latest_battle_id": "battle-gen9ou-proof",
            "current_elo": 1512,
            "recent_sample": 20,
            "recent_wins": 12,
            "recent_losses": 8,
        },
    )
    line = ia._ladder_line()
    assert "ELO=1512" in line
    assert "recent=12-8" in line
    assert "battle-gen9ou-proof" in line

def test_mcts_top_mass_basic():
    assert fl.mcts_top_mass({"a": 0.9, "b": 0.1}) == 0.9
    assert abs(fl.mcts_top_mass({"a": 1, "b": 1, "c": 1, "d": 1}) - 0.25) < 1e-9
    assert fl.mcts_top_mass({}) == 0.0


def test_flatness_gate_decisive_makes_mcts_primary():
    # A sharply-peaked MCTS policy -> high alpha (MCTS leads).
    alpha, meta = fl.flatness_gated_alpha({"a": 0.95, "b": 0.03, "c": 0.02})
    assert meta["regime"] == "decisive"
    assert alpha >= 0.80


def test_flatness_gate_flat_keeps_eval_heavy():
    # The REAL captured override-on-flat case: MCTS 0.4917/0.5079 (top mass
    # 0.508). This is genuinely flat, so the gate must keep eval able to win
    # (low MCTS alpha) -- exactly where the recent KO-guard/eval work helps.
    alpha, meta = fl.flatness_gated_alpha({"foulplay": 0.4917, "recover": 0.5079,
                                           "x": 0.0002, "y": 0.0002})
    assert meta["regime"] == "flat"
    assert alpha == fl.FLATNESS_ALPHA_WHEN_FLAT


def _policy_with_top_mass(m):
    # Build a normalized policy whose single top move holds exactly mass m, the
    # remainder spread over two other moves (so 'a' is always the top).
    rest = (1.0 - m) / 2.0
    return {"a": m, "b": rest, "c": rest}


def test_flatness_gate_monotonic_between():
    # Alpha is non-decreasing as MCTS gets more decisive across the whole range.
    masses = [0.40, 0.52, 0.55, 0.60, 0.65, 0.95]
    alphas = [fl.flatness_gated_alpha(_policy_with_top_mass(m))[0] for m in masses]
    for lo, hi in zip(alphas, alphas[1:]):
        assert lo <= hi + 1e-9
    assert alphas[0] == fl.FLATNESS_ALPHA_WHEN_FLAT     # 0.40 top mass -> flat
    assert alphas[-1] == fl.FLATNESS_ALPHA_WHEN_DECISIVE  # 0.95 -> decisive


def test_flatness_entropy_bounds():
    assert fl.mcts_normalized_entropy({"a": 1, "b": 1}) > 0.99   # uniform -> ~1
    assert fl.mcts_normalized_entropy({"a": 1.0}) == 0.0         # single -> 0


# ---------------- Phase 1a: whole-function splice ----------------

SAMPLE = '''\
import os


def alpha(x):
    return x + 1


def beta(y):
    # original
    return y * 2


def gamma(z):
    return z
'''


def test_find_function_span_unique():
    span = wfe.find_function_span(SAMPLE, "beta")
    assert span is not None
    assert span.name == "beta"
    assert "return y * 2" in span.source


def test_find_function_span_ambiguous_returns_none():
    dup = SAMPLE + "\n\ndef beta(y):\n    return 0\n"
    assert wfe.find_function_span(dup, "beta") is None


def test_extract_replacement_function_from_fence():
    resp = "Here is the fix:\n```python\ndef beta(y):\n    return y * 3\n```\nDone."
    out = wfe.extract_replacement_function(resp)
    assert out is not None
    assert "return y * 3" in out


def test_splice_replaces_only_target():
    new_beta = "def beta(y):\n    # improved\n    return y * 3\n"
    new_src, msg = wfe.splice_function(SAMPLE, "beta", new_beta)
    assert new_src is not None, msg
    assert "return y * 3" in new_src
    assert "return y * 2" not in new_src
    # other functions untouched
    assert "def alpha(x):" in new_src and "def gamma(z):" in new_src
    # still parses and beta still unique
    assert wfe.find_function_span(new_src, "beta") is not None


def test_splice_rejects_name_mismatch():
    new_src, msg = wfe.splice_function(SAMPLE, "beta", "def betax(y):\n    return 0\n")
    assert new_src is None
    assert "expected 'beta'" in msg


def test_splice_rejects_unparseable():
    new_src, msg = wfe.splice_function(SAMPLE, "beta", "def beta(y):\n    return (\n")
    assert new_src is None
    assert "parse" in msg


def test_splice_preserves_method_indentation():
    src = "class C:\n    def m(self):\n        return 1\n\n    def n(self):\n        return 2\n"
    new_src, msg = wfe.splice_function(src, "m", "def m(self):\n    return 99\n")
    assert new_src is not None, msg
    # re-indented to method level (8 spaces of body indentation)
    assert "        return 99" in new_src
    # module still parses
    import ast
    ast.parse(new_src)


# ---------------- Phase 1b: decision regret ----------------

def _case(top_mass, top="foulplay", second=0.05, second_move="recover", choice=None):
    pol = {top: top_mass, second_move: second}
    # pad remaining mass
    rest = 1.0 - top_mass - second
    if rest > 0:
        pol["pad"] = rest
    return {
        "battle_tag": "battle-x", "turn": 1,
        "mcts_policy": pol, "legal_moves": list(pol.keys()),
        "choice": choice or top, "decisive": top_mass >= 0.6,
        "mcts_top": top, "mcts_top_mass": top_mass,
    }


def test_regret_zero_when_choice_is_mcts_top():
    c = _case(0.8, choice="foulplay")
    assert dr.regret_of_choice(c, "foulplay") == 0.0


def test_regret_positive_when_overriding_decisive_mcts():
    c = _case(0.8, choice="recover")
    r = dr.regret_of_choice(c, "recover")
    assert r > 0.0
    # regret == top_mass - mass(recover) = 0.8 - 0.05
    assert abs(r - 0.75) < 1e-9


def test_score_suite_lower_regret_for_aligned_policy():
    suite = [_case(0.85, choice="recover"), _case(0.9, choice="recover")]
    # baseline (live chose 'recover', overriding the 'foulplay' MCTS top)
    base = dr.baseline_regret_from_suite(suite)
    # a candidate that picks the MCTS top move on both
    aligned = {f"{c['battle_tag']}#{c['turn']}": "foulplay" for c in suite}
    cand = dr.score_choices_against_suite(aligned, suite)
    assert cand["mean_regret"] < base["mean_regret"]
    assert cand["match_rate"] == 1.0
    assert base["override_rate"] == 1.0


def test_case_from_trace_detects_decisive():
    trace = {
        "battle_tag": "battle-y", "turn": 3, "choice": "foulplay",
        "mcts_policy_raw": {"foulplay": 0.82, "recover": 0.10, "x": 0.08},
        "legalOptions": {"legalMoves": [{"id": "foulplay"}, {"id": "recover"}, {"id": "x"}]},
    }
    c = dr.case_from_trace(trace)
    assert c is not None
    assert c.decisive is True
    assert c.mcts_top == "foulplay"


def test_case_from_trace_flat_not_decisive():
    trace = {
        "battle_tag": "battle-z", "turn": 4, "choice": "foulplay",
        "mcts_policy_raw": {"foulplay": 0.4917, "recover": 0.5079, "x": 0.0004},
        "legalOptions": {"legalMoves": [{"id": "foulplay"}, {"id": "recover"}]},
    }
    c = dr.case_from_trace(trace)
    assert c is not None
    assert c.decisive is False  # the real override-on-flat case must NOT be decisive


def test_regret_gate_skips_without_suite(tmp_path, monkeypatch):
    # Point the suite at a nonexistent file -> gate skips (never blocks).
    # regret_gate reads dr.SUITE_PATH dynamically, so patching it takes effect.
    monkeypatch.setattr(dr, "SUITE_PATH", tmp_path / "nope.jsonl")
    accept, detail = dr.regret_gate(since_epoch=0.0)
    assert accept is True
    assert "skipped" in detail


def test_regret_gate_accepts_lower_override(tmp_path, monkeypatch):
    # Frozen suite: 2 decisive cases the incumbent OVERRODE (chose 'recover').
    suite = [_case(0.85, choice="recover"), _case(0.9, choice="recover")]
    sp = tmp_path / "suite.jsonl"
    monkeypatch.setattr(dr, "SUITE_PATH", sp)
    dr.write_suite([dr.RegretCase(**{
        "battle_tag": c["battle_tag"], "turn": i, "mcts_policy": c["mcts_policy"],
        "legal_moves": c["legal_moves"], "choice": c["choice"],
        "decisive": c["decisive"], "mcts_top": c["mcts_top"],
        "mcts_top_mass": c["mcts_top_mass"],
    }) for i, c in enumerate(suite)], path=sp)
    loaded = dr.load_suite(sp)
    base = dr.baseline_regret_from_suite(loaded)
    assert base["override_rate"] == 1.0  # incumbent overrode every decisive turn
