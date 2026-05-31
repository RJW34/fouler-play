from fp.websocket_client import _public_replay_id_from_ref, _replay_ref_matches_battle


def test_public_replay_id_normalizes_battle_tags_private_hashes_and_urls():
    assert _public_replay_id_from_ref("battle-gen9ou-2620946620-stxlkin") == "gen9ou-2620946620"
    assert _public_replay_id_from_ref("https://replay.pokemonshowdown.com/gen9ou-2620946620") == "gen9ou-2620946620"
    assert _public_replay_id_from_ref("https://replay.pokemonshowdown.com/gen9ou-2620946620.json") == "gen9ou-2620946620"


def test_replay_guard_rejects_cross_linked_active_battle_replays():
    assert _replay_ref_matches_battle("gen9ou-2620946620", "battle-gen9ou-2620946620-stxlkin")
    assert not _replay_ref_matches_battle("gen9ou-2620945433", "battle-gen9ou-2620946620-stxlkin")
