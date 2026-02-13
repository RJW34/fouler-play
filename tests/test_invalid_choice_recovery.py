from types import SimpleNamespace

import constants
from fp.run_battle import _build_recovery_choice_from_request


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
