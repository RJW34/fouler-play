"""Tests for the competitive_pokemon_art catalog + loader.

Verifies catalog integrity (each entry has required fields, no duplicate
keys, pages are sensible), loader correctness, and that the
autoresearch_concept_hook tolerates missing / present catalog.
"""
from __future__ import annotations

import pytest

from data import competitive_pokemon_art as cpa
from replay_analysis import autoresearch_concept_hook as hook


# ---------------------------------------------------------------------------
# Catalog integrity
# ---------------------------------------------------------------------------

REQUIRED_FIELDS = {"key", "title", "level", "chapter", "source_pages",
                   "concept_summary", "fouler_application"}
ALLOWED_LEVELS = {"beginner", "intermediate", "advanced", "mastery"}


def test_catalog_loads_and_is_nonempty():
    items = cpa.all_concepts()
    assert len(items) >= 15, "expected at least 15 concept entries"


def test_every_concept_has_required_fields():
    for c in cpa.all_concepts():
        missing = REQUIRED_FIELDS - set(c.keys())
        assert not missing, f"concept {c.get('key')!r} missing {missing}"


def test_concept_keys_are_unique_and_snake_case():
    keys = [c["key"] for c in cpa.all_concepts()]
    assert len(keys) == len(set(keys)), "duplicate concept keys"
    for k in keys:
        assert k.islower() and " " not in k, f"non-snake-case key: {k!r}"


def test_levels_are_valid():
    for c in cpa.all_concepts():
        assert c["level"] in ALLOWED_LEVELS, \
            f"concept {c['key']!r} has invalid level {c['level']!r}"


def test_pages_are_within_book_range():
    for c in cpa.all_concepts():
        for p in c["source_pages"]:
            assert 1 <= p <= 119, \
                f"concept {c['key']!r} cites page {p} outside [1, 119]"


def test_no_verbatim_book_text_smell():
    """Spot-check: summaries should NOT contain phrases that look like
    direct quotes from the foreword (`extraordinary information-management`)
    or chapter intros (`battle for information`). We're paranoid here
    because copyright-cleanliness is the whole reason this catalog exists."""
    forbidden_strings = [
        "extraordinary information-management",
        "battle for information",
        "well-read student of life",
        "marveled at his unique teambuilding",
        "strike while the iron is hot",  # author's exact phrasing
    ]
    for c in cpa.all_concepts():
        text = (c["concept_summary"] + " " + c["fouler_application"]).lower()
        for forbidden in forbidden_strings:
            assert forbidden.lower() not in text, \
                f"concept {c['key']!r} contains forbidden verbatim: {forbidden!r}"


# ---------------------------------------------------------------------------
# Loader API
# ---------------------------------------------------------------------------


def test_concept_lookup_by_key():
    c = cpa.concept("the_dance")
    assert c is not None
    assert c["key"] == "the_dance"
    assert "tempo" in c["concept_summary"].lower()


def test_unknown_concept_returns_none():
    assert cpa.concept("definitely_not_a_real_concept_xyz") is None


def test_citation_format():
    cite = cpa.citation("the_dance")
    assert cite is not None
    assert "The Art of Competitive Pokemon" in cite or "Art Of Competitive Pokemon" in cite
    assert "ch.2" in cite
    assert "pp." in cite


def test_citation_for_unknown_concept_returns_none():
    assert cpa.citation("nonexistent_concept_key") is None


def test_concepts_for_issue_returns_matches():
    # `hazard_pressure` is an existing autoresearch issue; multiple
    # concepts cite it in related_issue_keys
    matches = cpa.concepts_for_issue("hazard_pressure")
    assert len(matches) >= 1
    keys = [c["key"] for c in matches]
    assert "the_dance" in keys or "positioning" in keys


def test_concepts_for_issue_empty_for_unknown():
    assert cpa.concepts_for_issue("unknown_issue_xyz") == []


def test_by_level_filtering():
    beginners = cpa.by_level("beginner")
    masters = cpa.by_level("mastery")
    assert len(beginners) > 0
    assert len(masters) > 0
    assert all(c["level"] == "beginner" for c in beginners)


# ---------------------------------------------------------------------------
# Hook
# ---------------------------------------------------------------------------


def test_hook_attaches_citation_to_known_issue():
    issue = {
        "key": "hazard_pressure",
        "title": "Hazard pressure is being lost",
        "recommendation": "Raise hazard-setting urgency earlier.",
    }
    out = hook.attach_concept_citations(issue)
    assert "see:" in out["recommendation"]
    assert "Art" in out["recommendation"]
    assert "concepts_cited" in out
    assert len(out["concepts_cited"]) >= 1


def test_hook_noop_for_unknown_issue():
    issue = {"key": "unknown_issue", "recommendation": "Do thing."}
    out = hook.attach_concept_citations(issue)
    assert out["recommendation"] == "Do thing."
    assert "concepts_cited" not in out


def test_hook_idempotent():
    issue = {"key": "hazard_pressure", "recommendation": "Raise urgency."}
    a = hook.attach_concept_citations(dict(issue))
    b = hook.attach_concept_citations(dict(a))
    assert a["recommendation"] == b["recommendation"]


def test_hook_handles_missing_recommendation():
    issue = {"key": "hazard_pressure"}
    out = hook.attach_concept_citations(issue)
    assert "see:" in out["recommendation"]
