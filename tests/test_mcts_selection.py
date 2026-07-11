from unittest.mock import MagicMock

import constants

from fp.search.main import DecisionProfile, select_move_from_eval_scores


def _mcts_selection_battle():
    battle = MagicMock()
    battle.force_switch = False
    battle.turn = 31
    battle.user.trapped = False
    battle.user.action_history = []
    battle.user.last_selected_move = None
    battle.user.active.name = "garganacl"
    battle.user.active.hp = 49
    battle.user.active.max_hp = 100
    battle.user.active.boosts = {}
    battle.user.reserve = []
    battle.opponent.active.name = "ogerponwellspringtera"
    battle.opponent.active.hp = 100
    battle.opponent.active.max_hp = 100
    battle.opponent.active.boosts = {}
    battle.opponent.reserve = []
    battle.request_json = {"active": [{}]}
    return battle


def _pokemon(name, hp, max_hp, *, moves=None, stats=None, types=None, ability=None, item=None):
    pokemon = MagicMock()
    pokemon.name = name
    pokemon.hp = hp
    pokemon.max_hp = max_hp
    pokemon.fainted = hp <= 0
    pokemon.moves = moves or []
    pokemon.stats = stats or {}
    pokemon.types = types or []
    pokemon.boosts = {}
    pokemon.ability = ability
    pokemon.item = item
    return pokemon


def _last_mon_conversion_battle(active, reserve, opponent, *, turn=70, history=None):
    battle = _mcts_selection_battle()
    battle.turn = turn
    battle.user.active = active
    battle.user.reserve = reserve
    battle.user.action_history = history or []
    battle.user.last_selected_move = None
    battle.opponent.active = opponent
    battle.opponent.reserve = []
    return battle


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


def test_mcts_only_deterministic_choice_respects_loop_breaker(monkeypatch):
    from fp.search.main import _position_fingerprint

    monkeypatch.setenv("FOULER_LOOP_BREAK", "1")
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


def test_mcts_only_honors_high_confidence_forced_switch_on_repeated_stall(monkeypatch):
    monkeypatch.setattr(
        "fp.search.main.detect_odd_move",
        lambda *_args, **_kwargs: ["waste_turn:repeat_status_move"],
    )
    battle = _mcts_selection_battle()
    trace = {}

    choice = select_move_from_eval_scores(
        {
            "protect": 0.4945,
            "switch slowkinggalar": 0.2290,
            "stealthrock": 0.1910,
        },
        battle=battle,
        ability_state=None,
        decision_profile=DecisionProfile.DEFAULT,
        trace=trace,
        policy_source="mcts",
        forced_line_bias={
            "move": "switch slowkinggalar",
            "confidence": 0.85,
            "reason": "Forced switch: opponent KOs us (151% vs 100%)",
            "line_type": "forced_switch",
            "applied": True,
        },
    )

    assert choice == "switch slowkinggalar"
    assert trace["mcts_only"]["selection"] == "forced_line_override"
    assert any(
        event.get("source") == "forced_line"
        and event.get("reason") == "high_confidence_forced_switch"
        for event in trace["mcts_only"]["events"]
    )


def test_mcts_only_preserves_non_repeated_protect_when_switch_policy_is_weak():
    trace = {}

    choice = select_move_from_eval_scores(
        {
            "protect": 0.5026,
            "switch gholdengo": 0.2188,
            "switch dondozo": 0.1533,
            "switch slowkinggalar": 0.0803,
        },
        battle=_mcts_selection_battle(),
        ability_state=None,
        decision_profile=DecisionProfile.DEFAULT,
        trace=trace,
        policy_source="mcts",
        forced_line_bias={
            "move": "switch slowkinggalar",
            "confidence": 0.85,
            "reason": "Forced switch: opponent KOs us (66% vs 49%), switching to slowkinggalar",
            "line_type": "forced_switch",
            "applied": True,
        },
    )

    assert choice == "protect"
    assert trace["mcts_only"]["selection"] == "deterministic_argmax"
    assert any(
        event.get("reason") == "top_stall_survival_line_preserved"
        for event in trace["mcts_only"]["events"]
    )


def test_mcts_only_keeps_best_mcts_switch_over_weaker_forced_target():
    trace = {}

    choice = select_move_from_eval_scores(
        {
            "switch dondozo": 0.6693,
            "switch garganacl": 0.2542,
            "switch gholdengo": 0.1115,
        },
        battle=_mcts_selection_battle(),
        ability_state=None,
        decision_profile=DecisionProfile.DEFAULT,
        trace=trace,
        policy_source="mcts",
        forced_line_bias={
            "move": "switch garganacl",
            "confidence": 0.85,
            "reason": "Forced switch: opponent KOs us (134% vs 100%), switching to garganacl",
            "line_type": "forced_switch",
            "applied": True,
        },
    )

    assert choice == "switch dondozo"
    assert trace["mcts_only"]["selection"] == "deterministic_argmax"


def test_mcts_only_preserves_recovery_anchor_in_won_endgame():
    battle = _mcts_selection_battle()
    battle.turn = 47
    battle.user.active.name = "gliscor"
    battle.user.active.hp = 45
    battle.user.active.max_hp = 100
    battle.user.active.moves = [MagicMock(name="swordsdance"), MagicMock(name="recover")]
    battle.user.reserve = [MagicMock(hp=100, max_hp=100), MagicMock(hp=100, max_hp=100)]
    battle.opponent.active.name = "corviknight"
    battle.opponent.active.hp = 100
    battle.opponent.active.max_hp = 100
    battle.opponent.reserve = []
    trace = {}

    choice = select_move_from_eval_scores(
        {"earthquake": 0.52, "recover": 0.40, "switch dondozo": 0.39},
        battle=battle,
        ability_state=None,
        decision_profile=DecisionProfile.DEFAULT,
        trace=trace,
        policy_source="mcts",
    )

    assert choice == "recover"
    assert any(
        event.get("source") == "endgame_preservation" and event.get("move") == "recover"
        for event in trace["mcts_only"]["events"]
    )


def test_mcts_only_endgame_preservation_keeps_immediate_ko_line():
    battle = _mcts_selection_battle()
    battle.turn = 47
    battle.user.active.name = "gliscor"
    battle.user.active.hp = 45
    battle.user.active.max_hp = 100
    battle.user.active.moves = [MagicMock(name="swordsdance"), MagicMock(name="recover")]
    battle.user.active.stats = {constants.ATTACK: 200, constants.SPECIAL_ATTACK: 80, constants.SPEED: 95}
    battle.user.active.types = ["ground"]
    battle.user.reserve = [MagicMock(hp=100, max_hp=100), MagicMock(hp=100, max_hp=100)]
    battle.opponent.active.name = "tinglu"
    battle.opponent.active.hp = 10
    battle.opponent.active.max_hp = 100
    battle.opponent.active.stats = {constants.DEFENSE: 80, constants.SPECIAL_DEFENSE: 80, constants.SPEED: 45}
    battle.opponent.active.types = ["dark", "ground"]
    battle.opponent.reserve = []
    trace = {}

    choice = select_move_from_eval_scores(
        {"earthquake": 0.52, "recover": 0.40, "switch dondozo": 0.39},
        battle=battle,
        ability_state=None,
        decision_profile=DecisionProfile.DEFAULT,
        trace=trace,
        policy_source="mcts",
    )

    assert choice == "earthquake"


def test_mcts_only_last_mon_conversion_caps_status_pivot_loop():
    battle = _last_mon_conversion_battle(
        _pokemon(
            "slowkinggalar",
            182,
            394,
            moves=[MagicMock(name="sludgebomb"), MagicMock(name="chillyreception")],
            stats={constants.SPECIAL_ATTACK: 100, constants.SPEED: 30},
            types=["poison", "psychic"],
        ),
        [
            _pokemon(
                "gholdengo",
                114,
                378,
                moves=[MagicMock(name="makeitrain"), MagicMock(name="shadowball")],
                stats={constants.SPECIAL_ATTACK: 133, constants.SPEED: 84},
                types=["steel", "ghost"],
            )
        ],
        _pokemon(
            "clefable",
            197,
            352,
            stats={constants.DEFENSE: 73, constants.SPECIAL_DEFENSE: 90, constants.SPEED: 60},
            types=["fairy"],
        ),
        turn=67,
        history=["switch slowkinggalar"],
    )
    trace = {}

    choice = select_move_from_eval_scores(
        {
            "chillyreception": 0.408,
            "sludgebomb": 0.317,
            "switch gholdengo": 0.124,
        },
        battle=battle,
        ability_state=None,
        decision_profile=DecisionProfile.LOW,
        trace=trace,
        policy_source="mcts",
    )

    assert choice == "sludgebomb"
    assert any(
        event.get("source") == "endgame_preservation"
        and event.get("move") == "chillyreception"
        and "avoid_status_pivot_loop" in event.get("reason", "")
        for event in trace["mcts_only"]["events"]
    )


def test_mcts_only_last_mon_conversion_caps_unforced_switch_loop():
    battle = _last_mon_conversion_battle(
        _pokemon(
            "gholdengo",
            138,
            378,
            moves=[MagicMock(name="nastyplot"), MagicMock(name="makeitrain")],
            stats={constants.SPECIAL_ATTACK: 133, constants.SPEED: 84},
            types=["steel", "ghost"],
        ),
        [
            _pokemon(
                "slowkinggalar",
                201,
                394,
                moves=[MagicMock(name="sludgebomb"), MagicMock(name="chillyreception")],
                stats={constants.SPECIAL_ATTACK: 100, constants.SPEED: 30},
                types=["poison", "psychic"],
            )
        ],
        _pokemon(
            "clefable",
            218,
            352,
            stats={constants.DEFENSE: 73, constants.SPECIAL_DEFENSE: 90, constants.SPEED: 60},
            types=["fairy"],
        ),
        turn=70,
    )
    trace = {}

    choice = select_move_from_eval_scores(
        {
            "switch slowkinggalar": 0.481,
            "makeitrain": 0.371,
            "shadowball": 0.185,
            "nastyplot": 0.153,
        },
        battle=battle,
        ability_state=None,
        decision_profile=DecisionProfile.LOW,
        trace=trace,
        policy_source="mcts",
    )

    assert choice == "makeitrain"
    assert any(
        event.get("source") == "endgame_preservation"
        and event.get("move") == "switch slowkinggalar"
        and "avoid_switch_loop" in event.get("reason", "")
        for event in trace["mcts_only"]["events"]
    )


def _kingambit_snowball_battle(*, pecharunt_hp=93):
    kingambit = _pokemon(
        "kingambit",
        362,
        362,
        moves=[MagicMock(name="kowtowcleave"), MagicMock(name="suckerpunch")],
        types=["dark", "steel"],
    )
    kingambit.ability = "supremeoverlord"
    kingambit.volatile_statuses = ["fallen3"]
    battle = _last_mon_conversion_battle(
        _pokemon(
            "blissey",
            652,
            652,
            moves=[
                MagicMock(name="softboiled"),
                MagicMock(name="calmmind"),
                MagicMock(name="shadowball"),
                MagicMock(name="stealthrock"),
            ],
            types=["normal"],
        ),
        [
            _pokemon(
                "gholdengo",
                378,
                378,
                moves=[
                    MagicMock(name="nastyplot"),
                    MagicMock(name="thunderwave"),
                    MagicMock(name="hex"),
                    MagicMock(name="recover"),
                ],
                types=["steel", "ghost"],
            ),
            _pokemon(
                "pecharunt",
                pecharunt_hp,
                380,
                moves=[
                    MagicMock(name="shadowball"),
                    MagicMock(name="toxic"),
                    MagicMock(name="partingshot"),
                    MagicMock(name="recover"),
                ],
                types=["poison", "ghost"],
            ),
        ],
        kingambit,
        turn=26,
    )
    battle.opponent.reserve = [
        _pokemon("corviknight", 358, 358, types=["flying", "steel"]),
        _pokemon("glimmora", 328, 328, types=["rock", "poison"]),
    ]
    return battle


def test_mcts_only_caps_low_hp_kingambit_sack_switch():
    trace = {}

    choice = select_move_from_eval_scores(
        {
            "switch pecharunt": 0.3552,
            "stealthrock": 0.277837,
            "switch gholdengo": 0.177324,
            "shadowball": 0.126004,
            "calmmind": 0.091105,
            "softboiled": 0.091602,
        },
        battle=_kingambit_snowball_battle(pecharunt_hp=93),
        ability_state=None,
        decision_profile=DecisionProfile.LOW,
        trace=trace,
        policy_source="mcts",
    )

    assert choice == "stealthrock"
    assert any(
        event.get("source") == "mcts_hard_safety"
        and event.get("move") == "switch pecharunt"
        and "late_kingambit_sack_guard" in event.get("reason", "")
        for event in trace["mcts_only"]["events"]
    )


def test_mcts_only_allows_healthy_kingambit_switch_target():
    trace = {}

    choice = select_move_from_eval_scores(
        {
            "switch pecharunt": 0.3552,
            "stealthrock": 0.277837,
            "switch gholdengo": 0.177324,
            "shadowball": 0.126004,
        },
        battle=_kingambit_snowball_battle(pecharunt_hp=360),
        ability_state=None,
        decision_profile=DecisionProfile.LOW,
        trace=trace,
        policy_source="mcts",
    )

    assert choice == "switch pecharunt"
    assert not any(
        event.get("reason", "").startswith("late_kingambit_sack_guard")
        for event in trace["mcts_only"]["events"]
    )


def _hazard_self_ko_battle(*, reserve=None, force_switch=False, turn=60):
    battle = _last_mon_conversion_battle(
        _pokemon(
            "slowkinggalar",
            173,
            394,
            moves=[MagicMock(name="icebeam"), MagicMock(name="sludgebomb"), MagicMock(name="chillyreception")],
            stats={constants.SPECIAL_ATTACK: 100, constants.SPEED: 30},
            types=["poison", "psychic"],
            ability="regenerator",
            item="heavydutyboots",
        ),
        reserve
        or [
            _pokemon("dondozo", 153, 503, types=["water"], ability="unaware"),
            _pokemon("garganacl", 122, 404, types=["rock"], ability="purifyingsalt", item="leftovers"),
            _pokemon("gholdengo", 78, 378, types=["steel", "ghost"], ability="goodasgold"),
        ],
        _pokemon(
            "ogerponwellspring",
            259,
            322,
            moves=[MagicMock(name="ivycudgel"), MagicMock(name="knockoff")],
            stats={constants.ATTACK: 120, constants.SPEED: 110},
            types=["grass", "water"],
        ),
        turn=turn,
    )
    battle.force_switch = force_switch
    battle.user.side_conditions = {constants.STEALTH_ROCK: 1, constants.SPIKES: 3}
    battle.opponent.side_conditions = {}
    return battle


def test_mcts_only_caps_live_loss_hazard_self_ko_switches():
    trace = {}

    choice = select_move_from_eval_scores(
        {
            "switch dondozo": 0.3467,
            "switch garganacl": 0.3098,
            "icebeam": 0.1288,
            "sludgebomb": 0.1211,
            "chillyreception": 0.1100,
        },
        battle=_hazard_self_ko_battle(),
        ability_state=None,
        decision_profile=DecisionProfile.LOW,
        trace=trace,
        policy_source="mcts",
    )

    assert choice == "icebeam"
    assert any(
        event.get("source") == "mcts_hard_safety"
        and event.get("move") == "switch dondozo"
        and "hazard_self_ko_switch_guard" in event.get("reason", "")
        for event in trace["mcts_only"]["events"]
    )


def test_mcts_only_caps_live_loss_second_hazard_self_ko_switch():
    trace = {}

    choice = select_move_from_eval_scores(
        {
            "switch garganacl": 0.3522,
            "icebeam": 0.1632,
            "sludgebomb": 0.1581,
            "chillyreception": 0.1112,
            "switch gholdengo": 0.0990,
        },
        battle=_hazard_self_ko_battle(
            reserve=[
                _pokemon("garganacl", 122, 404, types=["rock"], ability="purifyingsalt", item="leftovers"),
                _pokemon("gholdengo", 78, 378, types=["steel", "ghost"], ability="goodasgold"),
            ],
            turn=61,
        ),
        ability_state=None,
        decision_profile=DecisionProfile.LOW,
        trace=trace,
        policy_source="mcts",
    )

    assert choice == "icebeam"
    assert any(
        event.get("source") == "mcts_hard_safety"
        and event.get("move") == "switch garganacl"
        and "hazard_self_ko_switch_guard" in event.get("reason", "")
        for event in trace["mcts_only"]["events"]
    )


def test_mcts_only_caps_status_pivot_when_all_targets_faint_to_hazards():
    trace = {}

    choice = select_move_from_eval_scores(
        {
            "chillyreception": 0.3416,
            "icebeam": 0.1948,
            "sludgebomb": 0.1849,
        },
        battle=_hazard_self_ko_battle(
            reserve=[
                _pokemon("gholdengo", 78, 378, types=["steel", "ghost"], ability="goodasgold"),
            ],
            turn=62,
        ),
        ability_state=None,
        decision_profile=DecisionProfile.LOW,
        trace=trace,
        policy_source="mcts",
    )

    assert choice == "icebeam"
    assert any(
        event.get("source") == "mcts_hard_safety"
        and event.get("move") == "chillyreception"
        and "hazard_self_ko_pivot_guard" in event.get("reason", "")
        for event in trace["mcts_only"]["events"]
    )


def test_mcts_only_forced_switch_prefers_hazard_survivor():
    trace = {}

    choice = select_move_from_eval_scores(
        {
            "switch dondozo": 0.6693,
            "switch slowkinggalar": 0.2542,
            "switch gholdengo": 0.1115,
        },
        battle=_hazard_self_ko_battle(
            reserve=[
                _pokemon("dondozo", 153, 503, types=["water"], ability="unaware"),
                _pokemon(
                    "slowkinggalar",
                    394,
                    394,
                    types=["poison", "psychic"],
                    ability="regenerator",
                    item="heavydutyboots",
                ),
                _pokemon("gholdengo", 78, 378, types=["steel", "ghost"], ability="goodasgold"),
            ],
            force_switch=True,
            turn=60,
        ),
        ability_state=None,
        decision_profile=DecisionProfile.LOW,
        trace=trace,
        policy_source="mcts",
    )

    assert choice == "switch slowkinggalar"
    assert any(
        event.get("source") == "mcts_hard_safety"
        and event.get("move") == "switch dondozo"
        and "hazard_self_ko_forced_switch_guard" in event.get("reason", "")
        for event in trace["mcts_only"]["events"]
    )


def test_mcts_only_keeps_low_confidence_forced_bias_soft():
    trace = {}

    choice = select_move_from_eval_scores(
        {"protect": 0.4945, "switch slowkinggalar": 0.2290},
        battle=None,
        ability_state=None,
        decision_profile=DecisionProfile.DEFAULT,
        trace=trace,
        policy_source="mcts",
        forced_line_bias={
            "move": "switch slowkinggalar",
            "confidence": 0.70,
            "reason": "Predicted switch only",
            "line_type": "forced_switch",
            "applied": True,
        },
    )

    assert choice == "protect"
    assert trace["mcts_only"]["selection"] == "deterministic_argmax"


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
