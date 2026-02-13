from types import SimpleNamespace

from fp.search.poke_engine_helpers import _resolve_side_active_and_reserve


def _mk_pkmn(name: str, hp: int):
    return SimpleNamespace(name=name, hp=hp)


def test_resolve_side_active_prefers_existing_active():
    active = _mk_pkmn("blissey", 100)
    reserve = [_mk_pkmn("corviknight", 200)]
    battler = SimpleNamespace(active=active, reserve=reserve)

    resolved_active, resolved_reserve, synthesized = _resolve_side_active_and_reserve(battler)

    assert synthesized is False
    assert resolved_active is active
    assert len(resolved_reserve) == 1
    assert resolved_reserve[0].name == "corviknight"


def test_resolve_side_active_uses_live_reserve_when_missing_active():
    reserve = [_mk_pkmn("fainted", 0), _mk_pkmn("dondozo", 320), _mk_pkmn("gliscor", 180)]
    battler = SimpleNamespace(active=None, reserve=reserve)

    resolved_active, resolved_reserve, synthesized = _resolve_side_active_and_reserve(battler)

    assert synthesized is True
    assert resolved_active.name == "dondozo"
    assert [p.name for p in resolved_reserve] == ["fainted", "gliscor"]


def test_resolve_side_active_handles_empty_party():
    battler = SimpleNamespace(active=None, reserve=[])

    resolved_active, resolved_reserve, synthesized = _resolve_side_active_and_reserve(battler)

    assert synthesized is False
    assert resolved_active is None
    assert resolved_reserve == []

