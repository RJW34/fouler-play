"""autoresearch_concept_hook — extend autoresearch issue recommendations
with strategic-concept citations from competitive_pokemon_art.

Imported by autoresearch.py. Tolerant — if competitive_pokemon_art fails
to import (e.g., catalog missing), autoresearch continues unchanged.
"""
from __future__ import annotations

from typing import Any


def attach_concept_citations(issue: dict[str, Any]) -> dict[str, Any]:
    """Augment a single autoresearch issue with concept citations.

    Mutates issue['recommendation'] to append a "[concept] ..." citation
    when the issue's key has matching concepts in the catalog. Returns
    the issue dict.

    No-op if the catalog is missing or the issue has no key.
    """
    try:
        from data.competitive_pokemon_art import concepts_for_issue, citation
    except Exception:
        return issue
    issue_key = issue.get("key")
    if not issue_key:
        return issue
    concepts = concepts_for_issue(issue_key)
    if not concepts:
        return issue
    cites = []
    for c in concepts:
        cite_str = citation(c["key"])
        if cite_str:
            cites.append(cite_str)
    if cites:
        rec = (issue.get("recommendation") or "").rstrip()
        suffix = " (see: " + "; ".join(cites) + ")"
        if suffix not in rec:
            issue["recommendation"] = rec + suffix
            issue["concepts_cited"] = [c["key"] for c in concepts]
    return issue


def attach_concept_citations_all(issues: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Bulk variant — extends every issue in the list. Returns the same list."""
    for i in issues:
        attach_concept_citations(i)
    return issues
