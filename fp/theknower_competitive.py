from __future__ import annotations

from dataclasses import dataclass
import json
import logging
import os
from pathlib import Path
import subprocess
from typing import Sequence

logger = logging.getLogger(__name__)

_DEFAULT_THEKNOWER_ROOT = Path(os.environ.get("THEKNOWER_ROOT", "/home/ryan/projects/theknower"))
_DEFAULT_KB_QUERY_BIN = os.environ.get("KB_QUERY_BIN", "kb-query")
_TOPIC_DIR = "competitive-pokemon"
_KIND = "knower-competitive-pokemon"
_DEFAULT_QUERY = "gen9 ou current metagame anchors, role compression, hazard control, tera trends"


@dataclass(frozen=True)
class CompetitiveKnowledgeHit:
    id: str
    distance: float | None
    path: str
    text: str


@dataclass(frozen=True)
class CompetitiveTopicSnapshot:
    topic_root: Path
    query: str
    kind: str
    command: tuple[str, ...]
    hits: list[CompetitiveKnowledgeHit]
    highlights: list[str]
    fallback_reason: str | None = None



def load_competitive_topic(
    root: Path | None = None,
    query: str | None = None,
    *,
    species: Sequence[str] | None = None,
    kb_query_bin: str | None = None,
) -> CompetitiveTopicSnapshot:
    topic_root = (Path(root) if root is not None else _DEFAULT_THEKNOWER_ROOT) / _TOPIC_DIR
    effective_query = query or _build_query(species)
    binary = kb_query_bin or _DEFAULT_KB_QUERY_BIN
    command = (binary, "--kind", _KIND, "--json", effective_query)

    try:
        completed = subprocess.run(
            command,
            check=True,
            capture_output=True,
            text=True,
            timeout=20,
        )
        payload = json.loads(completed.stdout)
        hits = [_coerce_hit(item) for item in payload if isinstance(item, dict)]
        return CompetitiveTopicSnapshot(
            topic_root=topic_root,
            query=effective_query,
            kind=_KIND,
            command=command,
            hits=hits,
            highlights=_extract_highlights(hits),
        )
    except Exception as exc:
        logger.warning("kb-query competitive lookup failed, falling back to local topic parse: %s", exc)
        fallback_hits = _load_local_fallback(topic_root)
        return CompetitiveTopicSnapshot(
            topic_root=topic_root,
            query=effective_query,
            kind=_KIND,
            command=command,
            hits=fallback_hits,
            highlights=_extract_highlights(fallback_hits),
            fallback_reason=str(exc),
        )



def build_competitive_meta_context(
    root: Path | None = None,
    query: str | None = None,
    *,
    species: Sequence[str] | None = None,
    kb_query_bin: str | None = None,
) -> str:
    snapshot = load_competitive_topic(root, query, species=species, kb_query_bin=kb_query_bin)
    lines = [
        "Competitive Knowledge Oracle:",
        f"- Query kind: {snapshot.kind}",
        f"- Query text: {snapshot.query}",
        f"- Hits returned: {len(snapshot.hits)}",
    ]
    if snapshot.fallback_reason:
        lines.append(f"- Fallback: local topic parse used ({snapshot.fallback_reason})")
    if snapshot.highlights:
        lines.append("- Highlights:")
        lines.extend(f"  - {line}" for line in snapshot.highlights)
    return "\n".join(lines)



def build_pokedex_oracle_context(
    our_team_data: Sequence[dict] | None,
    opponent_team_data: Sequence[dict] | None,
    *,
    kb_query_bin: str | None = None,
) -> str:
    species = _collect_species(our_team_data, opponent_team_data)
    snapshot = load_competitive_topic(species=species, kb_query_bin=kb_query_bin)
    lines = [
        "Pokedex Oracle Augmentation:",
        f"- Query text: {snapshot.query}",
    ]
    if species:
        lines.append(f"- Team species focus: {', '.join(species[:12])}")
    if snapshot.highlights:
        lines.append("- Relevant knower notes:")
        lines.extend(f"  - {line}" for line in snapshot.highlights[:6])
    else:
        lines.append("- Relevant knower notes: none returned")
    return "\n".join(lines)



def _build_query(species: Sequence[str] | None) -> str:
    cleaned = [name for name in (species or []) if name]
    if not cleaned:
        return _DEFAULT_QUERY
    return (
        "gen9 ou current metagame, checks, tera trends, hazard control, role compression for: "
        + ", ".join(cleaned[:10])
    )



def _coerce_hit(item: dict) -> CompetitiveKnowledgeHit:
    meta = item.get("meta") or {}
    return CompetitiveKnowledgeHit(
        id=str(item.get("id", "unknown-hit")),
        distance=float(item["distance"]) if item.get("distance") is not None else None,
        path=str(meta.get("path", "")),
        text=str(item.get("text", "")).strip(),
    )



def _extract_highlights(hits: Sequence[CompetitiveKnowledgeHit]) -> list[str]:
    highlights: list[str] = []
    for hit in hits[:4]:
        lines = [line.strip() for line in hit.text.splitlines() if line.strip()]
        path_hint = Path(hit.path).name if hit.path else hit.id
        title = next((line.lstrip("# ") for line in lines if line.startswith("#")), None)
        if title:
            highlights.append(f"{path_hint}: {title}")
        detail = next(
            (
                line
                for line in lines
                if line.startswith("-")
                or line.startswith("|")
                or "Regulation" in line
                or "%" in line
            ),
            None,
        )
        if detail:
            highlights.append(f"{path_hint}: {detail}")
    return highlights[:8]



def _collect_species(*teams: Sequence[dict] | None) -> list[str]:
    seen: list[str] = []
    for team in teams:
        for pokemon in team or []:
            species = str(pokemon.get("species", "")).strip()
            if species and species not in seen:
                seen.append(species)
    return seen



def _load_local_fallback(topic_root: Path) -> list[CompetitiveKnowledgeHit]:
    docs = []
    for path in sorted(topic_root.glob("*.md")):
        try:
            text = path.read_text(encoding="utf-8").strip()
        except OSError:
            continue
        if text:
            docs.append(
                CompetitiveKnowledgeHit(
                    id=f"fallback:{path.name}",
                    distance=None,
                    path=str(path),
                    text=text[:4000],
                )
            )
    return docs
