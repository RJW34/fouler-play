"""competitive_pokemon_art — strategic concept catalog grounded in
"The Art Of Competitive Pokemon" by Jamvad (2020).

The catalog is a DERIVATIVE work: paraphrased summaries + page citations.
No verbatim text from the book is stored here. Full original text lives
outside the repo at ~/.hermes/knowledge/competitive-pokemon-art/ (operator's
private cache) and is not committed.

Usage from autoresearch:

    from data.competitive_pokemon_art import concept, concepts_for_issue, citation

    # Cite a single concept by key
    c = concept("the_dance")
    # -> {"key": "the_dance", "title": "The Dance ...", "chapter": 2, ...}

    # Find concepts that ground a specific autoresearch issue
    for c in concepts_for_issue("hazard_pressure"):
        print(c["title"], "->", c["fouler_application"])

    # Get a one-line citation string for receipts / discord reports
    citation("the_dance")
    # -> "The Art of Competitive Pokemon: ch.2 'The Dance' (pp. 32-38)"
"""
from __future__ import annotations

import json
from functools import lru_cache
from pathlib import Path
from typing import Any

_HERE = Path(__file__).resolve().parent
_CATALOG_PATH = _HERE / "concepts.json"


@lru_cache(maxsize=1)
def _catalog() -> dict[str, Any]:
    return json.loads(_CATALOG_PATH.read_text(encoding="utf-8"))


def all_concepts() -> list[dict[str, Any]]:
    """Every concept entry in the catalog."""
    return list(_catalog()["concepts"])


@lru_cache(maxsize=64)
def concept(key: str) -> dict[str, Any] | None:
    """Look up one concept by key. Returns None if unknown."""
    for c in _catalog()["concepts"]:
        if c["key"] == key:
            return dict(c)
    return None


def concepts_for_issue(issue_key: str) -> list[dict[str, Any]]:
    """All concepts whose `related_issue_keys` include the autoresearch
    issue key. Used to attach strategic context to bot-side issues."""
    out = []
    for c in _catalog()["concepts"]:
        if issue_key in (c.get("related_issue_keys") or []):
            out.append(dict(c))
    return out


def citation(key: str) -> str | None:
    """Compact human-readable citation for a concept. Use in receipts +
    Discord reports so the user can look up the source themselves.

    Example: "The Art of Competitive Pokemon: ch.7 'Risk vs Reward' (pp. 103-109)"
    """
    c = concept(key)
    if not c:
        return None
    pages = c.get("source_pages") or []
    if not pages:
        page_str = ""
    elif len(pages) == 1:
        page_str = f"p. {pages[0]}"
    else:
        page_str = f"pp. {pages[0]}-{pages[-1]}"
    src = _catalog()["source"]
    return (f"{src['title']}: ch.{c['chapter']} '{c.get('section') or c['title']}'"
            f" ({page_str})").strip()


def source_info() -> dict[str, Any]:
    """The catalog's source metadata block."""
    return dict(_catalog()["source"])


def by_level(level: str) -> list[dict[str, Any]]:
    """Concepts filtered by skill level (beginner/intermediate/advanced/mastery)."""
    return [dict(c) for c in _catalog()["concepts"]
            if c.get("level") == level]
