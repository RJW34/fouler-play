from fp.battle import Battle, Pokemon
from fp.search import standard_battles


def make_battle() -> Battle:
    battle = Battle("battle-gen9ou-test")
    battle.pokemon_format = "gen9ou"
    battle.generation = "gen9"
    battle.opponent.active = Pokemon("garchomp", 100)
    battle.opponent.reserve = [Pokemon("dragapult", 100), Pokemon("gliscor", 100)]
    battle.user.active = Pokemon("corviknight", 100)
    return battle


def test_standard_sampler_rehydrates_datasets_for_full_battle_roster(monkeypatch):
    battle = make_battle()
    calls = []

    def fake_team_initialize(mode, names, *args, **kwargs):
        calls.append(("team", mode, set(names)))
        standard_battles.TeamDatasets.pkmn_mode = mode
        standard_battles.TeamDatasets.pkmn_sets = {battle.opponent.active.name: [object()]}

    def fake_smogon_initialize(mode, names):
        calls.append(("smogon", mode, set(names)))
        standard_battles.SmogonSets.pkmn_mode = mode
        standard_battles.SmogonSets.pkmn_sets = {battle.opponent.active.name: [object()]}

    monkeypatch.setattr(standard_battles.FoulPlayConfig, "smogon_stats", None, raising=False)
    monkeypatch.setattr(standard_battles.TeamDatasets, "pkmn_mode", "uninitialized")
    monkeypatch.setattr(standard_battles.TeamDatasets, "pkmn_sets", {})
    monkeypatch.setattr(standard_battles.TeamDatasets, "initialize", fake_team_initialize)
    monkeypatch.setattr(standard_battles.SmogonSets, "pkmn_mode", "uninitialized")
    monkeypatch.setattr(standard_battles.SmogonSets, "pkmn_sets", {})
    monkeypatch.setattr(standard_battles.SmogonSets, "initialize", fake_smogon_initialize)

    standard_battles._ensure_standard_battle_sampling_datasets(
        battle.opponent.active,
        battle,
    )

    expected_names = {"garchomp", "dragapult", "gliscor", "corviknight"}
    assert ("team", "gen9ou", expected_names) in calls
    assert ("smogon", "gen9ou", expected_names) in calls


def test_prepare_battles_passes_battle_copy_to_each_sampled_pokemon(monkeypatch):
    battle = make_battle()
    sampled = []

    def fake_sample_pokemon(pkmn, battle_arg=None):
        sampled.append((pkmn.name, battle_arg))

    monkeypatch.setattr(standard_battles, "sample_pokemon", fake_sample_pokemon)

    standard_battles.prepare_battles(battle, num_battles=2)

    sampled_names = [name for name, _battle_arg in sampled]
    sampled_battles = [battle_arg for _name, battle_arg in sampled]
    assert sampled_names == [
        "garchomp",
        "dragapult",
        "gliscor",
        "garchomp",
        "dragapult",
        "gliscor",
    ]
    assert all(battle_arg is not None for battle_arg in sampled_battles)
    assert all(battle_arg is not battle for battle_arg in sampled_battles)
