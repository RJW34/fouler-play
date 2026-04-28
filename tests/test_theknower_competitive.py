from pathlib import Path

from fp.theknower_competitive import (
    build_competitive_meta_context,
    build_pokedex_oracle_context,
    load_competitive_topic,
)


THEKNOWER_ROOT = Path("/home/ryan/projects/theknower")


def test_load_competitive_topic_prefers_kb_query_runtime():
    snapshot = load_competitive_topic(THEKNOWER_ROOT, species=["Great Tusk", "Kingambit"])

    assert snapshot.command[0].endswith("kb-query") or snapshot.command[0] == "kb-query"
    assert snapshot.kind == "knower-competitive-pokemon"
    assert snapshot.hits
    assert any("Great Tusk" in hit.text for hit in snapshot.hits)


def test_build_competitive_meta_context_surfaces_runtime_meta_summary():
    context = build_competitive_meta_context(THEKNOWER_ROOT, species=["Great Tusk", "Gliscor"])

    assert "Competitive Knowledge Oracle:" in context
    assert "Query kind: knower-competitive-pokemon" in context
    assert "Great Tusk" in context or "gen9-ou-meta" in context


def test_build_pokedex_oracle_context_augments_team_species_notes():
    context = build_pokedex_oracle_context(
        [{"species": "Great Tusk"}, {"species": "Kyurem"}],
        [{"species": "Kingambit"}, {"species": "Gliscor"}],
    )

    assert "Pokedex Oracle Augmentation:" in context
    assert "Great Tusk" in context
    assert "Kingambit" in context
