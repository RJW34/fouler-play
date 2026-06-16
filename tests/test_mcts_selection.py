from unittest.mock import MagicMock

from fp.search.main import DecisionProfile, select_move_from_eval_scores


def test_mcts_only_selection_does_not_sample_from_close_policy(monkeypatch):
    def fail_random_choices(*_args, **_kwargs):
        raise AssertionError("MCTS-only selection must not re-sample searched policy")

    monkeypatch.setattr("fp.search.main.random.choices", fail_random_choices)
    trace = {}

    choice = select_move_from_eval_scores(
        {"recover": 0.51, "shadowball": 0.49},
        battle=None,
        ability_state=None,
        decision_profile=DecisionProfile.DEFAULT,
        trace=trace,
        policy_source="mcts",
    )

    assert choice == "recover"
    assert trace["mcts_only"]["selection"] == "deterministic_argmax"


def test_mcts_only_deterministic_choice_respects_loop_breaker():
    battle = MagicMock()
    battle.force_switch = False
    battle.user.trapped = False
    battle.user.action_history = ["recover", "recover", "recover"]
    battle.user.last_selected_move = None
    battle.user.active.hp = 50
    battle.user.active.max_hp = 100
    battle.request_json = {"active": [{}]}
    trace = {}

    choice = select_move_from_eval_scores(
        {"recover": 0.90, "shadowball": 0.30},
        battle=battle,
        ability_state=None,
        decision_profile=DecisionProfile.DEFAULT,
        trace=trace,
        policy_source="mcts",
    )

    assert choice == "shadowball"
    assert any(
        event.get("source") == "decision_loop_break"
        for event in trace["mcts_only"]["events"]
    )
