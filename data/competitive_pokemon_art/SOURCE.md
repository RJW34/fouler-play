# competitive_pokemon_art — source + license note

## Source

- **Title**: The Art Of Competitive Pokemon
- **Author**: Jamvad (handle)
- **Foreword by**: Gr8astard (Smogon Premier League player)
- **Year**: 2020
- **Length**: 119 pages
- **Acquisition**: PDF downloaded by repo owner from fliphtml5; original sold to a verified individual purchaser.

## What this directory contains

- `concepts.json` — A DERIVATIVE structured catalog: each entry has a `concept_summary` paraphrased from the source and a `fouler_application` describing how fouler-play might detect / weight / respond to the concept. Page numbers cite the source for traceability.
- `__init__.py` — Python loader exposing `concept(key)`, `concepts_for_issue(issue_key)`, `citation(key)`, and `all_concepts()`.

## What this directory does NOT contain

- The full text of the book is NOT stored in this repo. It lives in the repo owner's private cache outside this repository.
- No verbatim quotations longer than the chapter title and Sun Tzu epigraphs the author himself quotes (which are themselves out-of-copyright public-domain).

## Why this is appropriate

The catalog is **transformative analytical reference material** — paraphrased summaries used internally to ground a Pokemon battle bot's decision-recommendation surface in established strategic vocabulary. The catalog cites chapter/section/page so any human consumer (Ryan, future maintainers, Discord viewers) can look up the source themselves.

This is the same pattern as analytic tools that cite Smogon strategy articles by URL: the tool relies on the source for grounding without redistributing it.

## How to extend

To add a new concept entry:

1. Pick a concept from the book that isn't yet in `concepts.json`.
2. Write a 1-2 sentence `concept_summary` in your own words (do not copy book sentences).
3. Write a `fouler_application` describing how the bot's autoresearch / decision engine could detect or respond to this concept.
4. Cite the chapter, section, and page range.
5. Add the concept key to `related_issue_keys` on any existing concept it pairs with.
6. Run the test suite (`pytest tests/test_competitive_concepts.py`).
7. Commit + push.

## How to refresh the source

The PDF is occasionally updated by the author. If a new edition lands:

1. Place the new PDF in the repo owner's private cache.
2. Re-read chapter intros to check for new concepts or renamed sections.
3. Update `concepts.json` with any new entries.
4. Bump the `ingested_at_utc` field.
