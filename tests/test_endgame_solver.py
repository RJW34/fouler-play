from types import SimpleNamespace

import constants

from fp.search.endgame import can_outspeed, get_speed, solve_1v1


def pokemon(
    *,
    name="mon",
    speed=100,
    hp=100,
    max_hp=100,
    status=None,
    moves=None,
):
    return SimpleNamespace(
        name=name,
        stats={constants.SPEED: speed},
        boosts={constants.SPEED: 0},
        status=status,
        hp=hp,
        max_hp=max_hp,
        moves=moves or [],
        types=[],
    )


def move(name, *, current_pp=8, disabled=False):
    return SimpleNamespace(name=name, current_pp=current_pp, disabled=disabled)


def test_paralyzed_speed_uses_project_constant_without_crashing():
    paralyzed = pokemon(speed=120, status=constants.PARALYZED)
    healthy = pokemon(speed=80)

    assert get_speed(paralyzed) == 60
    assert can_outspeed(paralyzed, healthy) is False


def test_solve_1v1_handles_paralysis_without_falling_back_to_exception():
    paralyzed = pokemon(
        name="slow-attacker",
        speed=120,
        status=constants.PARALYZED,
        hp=100,
        max_hp=100,
        moves=[move("earthquake")],
    )
    opponent = pokemon(
        name="faster-now",
        speed=80,
        hp=100,
        max_hp=100,
        moves=[move("tackle")],
    )

    result = solve_1v1(paralyzed, opponent)

    assert result.explanation
    assert result.best_move in {"earthquake", None}
