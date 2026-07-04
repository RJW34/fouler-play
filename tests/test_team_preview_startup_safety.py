from types import SimpleNamespace

from fp import run_battle


def _pokemon(index, hp=100, name=None):
    return SimpleNamespace(index=index, hp=hp, name=name or f"mon{index}")


def test_first_alive_team_preview_slot_skips_fainted_leads():
    battle = SimpleNamespace(
        user=SimpleNamespace(
            reserve=[
                _pokemon(1, hp=0),
                _pokemon(2, hp=1),
                _pokemon(3, hp=100),
            ]
        )
    )

    assert run_battle._first_alive_team_preview_slot(battle) == 2


def test_team_preview_message_places_selected_lead_first():
    battle = SimpleNamespace(
        rqid=17,
        user=SimpleNamespace(
            reserve=[
                _pokemon(1),
                _pokemon(2),
                _pokemon(3),
                _pokemon(4),
                _pokemon(5),
                _pokemon(6),
            ]
        ),
    )

    assert run_battle._team_preview_message_for_slot(battle, 3) == ["/team 312456|17"]
