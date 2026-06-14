from types import SimpleNamespace

import constants
from fp.run_battle import _build_recovery_choice_from_request, _fallback_decision


def _active(types=None, hp=100, max_hp=100, moves=None, stats=None, name="active", index=1):
    return SimpleNamespace(
        name=name,
        index=index,
        types=types or [],
        hp=hp,
        max_hp=max_hp,
        moves=moves or [],
        stats=stats or {
            constants.DEFENSE: 100,
            constants.SPECIAL_DEFENSE: 100,
        },
    )


def test_fallback_scores_request_moves_instead_of_first_legal():
    battle = SimpleNamespace(
        force_switch=False,
        request_json={
            constants.ACTIVE: [
                {
                    constants.MOVES: [
                        {constants.ID: "earthquake", constants.DISABLED: False, constants.PP: 16},
                        {constants.ID: "icebeam", constants.DISABLED: False, constants.PP: 16},
                    ],
                }
            ],
        },
        user=SimpleNamespace(active=_active(types=["ice"]), reserve=[]),
        opponent=SimpleNamespace(active=_active(types=["ground", "flying"])),
    )

    assert _fallback_decision(battle) == "icebeam"


def test_fallback_ignores_disabled_and_empty_pp_request_moves():
    battle = SimpleNamespace(
        force_switch=False,
        request_json={
            constants.ACTIVE: [
                {
                    constants.MOVES: [
                        {constants.ID: "earthquake", constants.DISABLED: True, constants.PP: 16},
                        {constants.ID: "recover", constants.DISABLED: False, constants.PP: 0},
                        {constants.ID: "icebeam", constants.DISABLED: False, constants.PP: 16},
                    ],
                }
            ],
        },
        user=SimpleNamespace(active=_active(types=["ice"]), reserve=[]),
        opponent=SimpleNamespace(active=_active(types=["ground", "flying"])),
    )

    assert _fallback_decision(battle) == "icebeam"


def test_force_switch_fallback_scores_reserves_separately():
    frail_reserve = _active(
        name="weavile",
        types=["dark", "ice"],
        hp=25,
        max_hp=100,
        stats={constants.DEFENSE: 60, constants.SPECIAL_DEFENSE: 80},
    )
    bulky_reserve = _active(
        name="corviknight",
        types=["flying", "steel"],
        hp=100,
        max_hp=100,
        stats={constants.DEFENSE: 150, constants.SPECIAL_DEFENSE: 120},
    )
    battle = SimpleNamespace(
        force_switch=True,
        request_json={constants.FORCE_SWITCH: True},
        user=SimpleNamespace(active=_active(), reserve=[frail_reserve, bulky_reserve]),
        opponent=SimpleNamespace(active=_active(types=["normal"])),
    )

    assert _fallback_decision(battle) == "switch corviknight"


def test_force_switch_fallback_respects_request_legal_switch_slots():
    illegal_bulky = _active(
        name="corviknight",
        index=2,
        types=["flying", "steel"],
        hp=100,
        max_hp=100,
        stats={constants.DEFENSE: 150, constants.SPECIAL_DEFENSE: 120},
    )
    legal_frail = _active(
        name="weavile",
        index=3,
        types=["dark", "ice"],
        hp=25,
        max_hp=100,
        stats={constants.DEFENSE: 60, constants.SPECIAL_DEFENSE: 80},
    )
    battle = SimpleNamespace(
        force_switch=True,
        request_json={
            constants.FORCE_SWITCH: True,
            constants.SIDE: {
                constants.POKEMON: [
                    {constants.ACTIVE: True, constants.CONDITION: "0 fnt"},
                    {constants.ACTIVE: False, constants.CONDITION: "0 fnt"},
                    {constants.ACTIVE: False, constants.CONDITION: "25/100"},
                ]
            },
        },
        user=SimpleNamespace(active=_active(), reserve=[illegal_bulky, legal_frail]),
        opponent=SimpleNamespace(active=_active(types=["normal"])),
    )

    assert _fallback_decision(battle) == "switch weavile"


def test_invalid_switch_when_trapped_retries_with_move():
    battle = SimpleNamespace(
        rqid=33,
        force_switch=False,
        user=SimpleNamespace(trapped=True),
        request_json={
            constants.ACTIVE: [
                {
                    constants.TRAPPED: True,
                    constants.MOVES: [
                        {constants.ID: "closecombat", constants.DISABLED: False, constants.PP: 8},
                        {constants.ID: "crunch", constants.DISABLED: False, constants.PP: 24},
                    ],
                }
            ],
            constants.SIDE: {
                constants.POKEMON: [
                    {constants.ACTIVE: True, constants.CONDITION: "112/325"},
                    {constants.ACTIVE: False, constants.CONDITION: "652/652"},
                ]
            },
        },
    )

    choice = _build_recovery_choice_from_request(
        battle,
        error_message="|error|[Invalid choice] Can't switch: The active Pokemon is trapped",
    )
    assert choice == ["/choose move closecombat", "33"]


def test_force_switch_retries_with_switch_slot():
    battle = SimpleNamespace(
        rqid=17,
        force_switch=True,
        user=SimpleNamespace(trapped=False),
        request_json={
            constants.FORCE_SWITCH: True,
            constants.ACTIVE: [
                {
                    constants.MOVES: [
                        {constants.ID: "bodypress", constants.DISABLED: False, constants.PP: 16},
                    ],
                }
            ],
            constants.SIDE: {
                constants.POKEMON: [
                    {constants.ACTIVE: True, constants.CONDITION: "0 fnt"},
                    {constants.ACTIVE: False, constants.CONDITION: "334/334"},
                    {constants.ACTIVE: False, constants.CONDITION: "0 fnt"},
                ]
            },
        },
    )

    choice = _build_recovery_choice_from_request(
        battle,
        error_message="|error|[Invalid choice] Can't move: You need a switch response",
    )
    assert choice == ["/switch 2", "17"]
