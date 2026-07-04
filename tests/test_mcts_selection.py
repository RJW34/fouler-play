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
    from fp.search.main import _position_fingerprint

    battle = MagicMock()
    battle.force_switch = False
    battle.turn = 12
    battle.user.trapped = False
    battle.user.action_history = ["recover", "recover", "recover"]
    battle.user.last_selected_move = None
    battle.user.active.name = "blissey"
    battle.user.active.hp = 50
    battle.user.active.max_hp = 100
    battle.user.reserve = []
    battle.opponent.active.name = "gholdengo"
    battle.opponent.active.hp = 80
    battle.opponent.reserve = []
    battle.request_json = {"active": [{}]}
    # Loop-breaker guards (2026-07-04): only a non-decisive policy over a provably
    # stagnant position may be broken -- seed identical board fingerprints.
    fingerprint = _position_fingerprint(battle)
    assert fingerprint is not None
    battle._loop_break_fp_history = [(9, fingerprint), (10, fingerprint), (11, fingerprint)]
    trace = {}

    choice = select_move_from_eval_scores(
        {"recover": 0.40, "shadowball": 0.34, "toxic": 0.26},
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


def test_mcts_only_loop_breaker_skips_decisive_search():
    # gen9ou-2643766855 t20 shape: the search is decisive (75% on the repeated
    # move); the loop-breaker must not override it (2026-07-04 guard).
    battle = MagicMock()
    battle.force_switch = False
    battle.turn = 20
    battle.user.trapped = False
    battle.user.action_history = ["protect", "earthquake", "protect", "swordsdance", "protect"]
    battle.user.last_selected_move = None
    battle.user.active.name = "gliscor"
    battle.user.active.hp = 211
    battle.user.active.max_hp = 352
    battle.user.reserve = []
    battle.opponent.active.name = "corviknight"
    battle.opponent.active.hp = 329
    battle.opponent.reserve = []
    battle.request_json = {"active": [{}]}
    trace = {}

    choice = select_move_from_eval_scores(
        {"protect": 0.751, "earthquake": 0.078, "knockoff": 0.077, "swordsdance": 0.069},
        battle=battle,
        ability_state=None,
        decision_profile=DecisionProfile.DEFAULT,
        trace=trace,
        policy_source="mcts",
    )

    assert choice == "protect"


def test_mcts_only_loop_breaker_kill_switch(monkeypatch):
    monkeypatch.setenv("FOULER_LOOP_BREAK", "0")
    battle = MagicMock()
    battle.force_switch = False
    battle.turn = 12
    battle.user.trapped = False
    battle.user.action_history = ["recover", "recover", "recover"]
    battle.user.last_selected_move = None
    battle.user.active.hp = 50
    battle.user.active.max_hp = 100
    battle.request_json = {"active": [{}]}
    trace = {}

    choice = select_move_from_eval_scores(
        {"recover": 0.40, "shadowball": 0.34, "toxic": 0.26},
        battle=battle,
        ability_state=None,
        decision_profile=DecisionProfile.DEFAULT,
        trace=trace,
        policy_source="mcts",
    )

    assert choice == "recover"
    assert not any(
        event.get("source") == "decision_loop_break"
        for event in trace["mcts_only"]["events"]
    )
