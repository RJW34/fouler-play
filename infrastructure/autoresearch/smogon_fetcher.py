"""
Smogon Data Fetcher — pulls competitive Pokemon usage data for research.

Sources:
- Smogon usage stats: https://www.smogon.com/stats/
- Pokemon sets: Smogon dex pages
- Usage-based move frequencies for opponent prediction

This data feeds into the autoresearch loop so DEKU can make
data-driven improvements to the penalty pipeline and eval weights.
"""

import json
import logging
import os
import time
import urllib.error
import urllib.request
from datetime import datetime
from pathlib import Path
from typing import Optional

logger = logging.getLogger(__name__)

PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
CACHE_DIR = PROJECT_ROOT / "data" / "smogon_stats_cache"
SETS_CACHE_DIR = PROJECT_ROOT / "data" / "pkmn_sets_cache"

SMOGON_STATS_BASE = "https://www.smogon.com/stats"
SMOGON_USAGE_FORMAT = "gen9ou"


def fetch_usage_stats(
    year_month: Optional[str] = None,
    rating: int = 1695,
) -> Optional[dict]:
    """Fetch Smogon usage stats for gen9ou.

    Args:
        year_month: e.g. "2026-02". Defaults to latest available.
        rating: Minimum rating cutoff (1500, 1695, 1825).

    Returns:
        Dict mapping Pokemon name to usage data, or None on failure.
    """
    CACHE_DIR.mkdir(parents=True, exist_ok=True)

    if year_month is None:
        year_month = datetime.now().strftime("%Y-%m")

    cache_file = CACHE_DIR / f"usage_{year_month}_{rating}.json"

    # Use cache if fresh (less than 24h old)
    if cache_file.exists():
        age_hours = (time.time() - cache_file.stat().st_mtime) / 3600
        if age_hours < 24:
            try:
                return json.loads(cache_file.read_text(encoding="utf-8"))
            except (json.JSONDecodeError, OSError):
                pass

    # Fetch from Smogon
    url = f"{SMOGON_STATS_BASE}/{year_month}/chaos/{SMOGON_USAGE_FORMAT}-{rating}.json"
    logger.info(f"Fetching Smogon stats: {url}")

    try:
        req = urllib.request.Request(url, headers={"User-Agent": "fouler-play/1.0"})
        with urllib.request.urlopen(req, timeout=30) as resp:
            data = json.loads(resp.read().decode("utf-8"))
    except (urllib.error.URLError, urllib.error.HTTPError, json.JSONDecodeError) as e:
        logger.warning(f"Failed to fetch Smogon stats: {e}")
        # Try previous month
        if year_month == datetime.now().strftime("%Y-%m"):
            prev = datetime.now().replace(day=1)
            prev = prev.replace(
                month=prev.month - 1 if prev.month > 1 else 12,
                year=prev.year if prev.month > 1 else prev.year - 1,
            )
            return fetch_usage_stats(prev.strftime("%Y-%m"), rating)
        return None

    # Cache the result
    try:
        cache_file.write_text(json.dumps(data, indent=2), encoding="utf-8")
    except OSError as e:
        logger.warning(f"Failed to cache stats: {e}")

    return data


def get_top_pokemon(n: int = 30, rating: int = 1695) -> list[dict]:
    """Get the top N most-used Pokemon in gen9ou.

    Returns list of dicts with: name, usage_pct, common_moves, common_items.
    """
    data = fetch_usage_stats(rating=rating)
    if not data or "data" not in data:
        return []

    pokemon_data = data["data"]
    ranked = []

    for name, info in pokemon_data.items():
        usage_pct = info.get("usage", 0)
        if usage_pct <= 0:
            continue

        # Extract top moves
        moves = info.get("Moves", {})
        top_moves = sorted(moves.items(), key=lambda x: x[1], reverse=True)[:6]

        # Extract top items
        items = info.get("Items", {})
        top_items = sorted(items.items(), key=lambda x: x[1], reverse=True)[:3]

        # Extract top abilities
        abilities = info.get("Abilities", {})
        top_abilities = sorted(
            abilities.items(), key=lambda x: x[1], reverse=True
        )[:3]

        ranked.append({
            "name": name,
            "usage_pct": round(usage_pct * 100, 2),
            "common_moves": [m[0] for m in top_moves],
            "common_items": [i[0] for i in top_items],
            "common_abilities": [a[0] for a in top_abilities],
        })

    ranked.sort(key=lambda x: x["usage_pct"], reverse=True)
    return ranked[:n]


def get_pokemon_counters(pokemon_name: str, rating: int = 1695) -> list[dict]:
    """Get the checks and counters for a specific Pokemon.

    Returns list of dicts with: name, ko_pct, switch_pct.
    """
    data = fetch_usage_stats(rating=rating)
    if not data or "data" not in data:
        return []

    poke_data = data["data"].get(pokemon_name, {})
    checks = poke_data.get("Checks and Counters", {})

    counters = []
    for name, info in checks.items():
        if isinstance(info, dict):
            counters.append({
                "name": name,
                "ko_pct": info.get("koed", 0),
                "switch_pct": info.get("switched", 0),
                "score": info.get("score", 0),
            })
        elif isinstance(info, list) and len(info) >= 2:
            counters.append({
                "name": name,
                "score": info[0],
                "ko_pct": info[1] if len(info) > 1 else 0,
                "switch_pct": info[2] if len(info) > 2 else 0,
            })

    counters.sort(key=lambda x: x.get("score", 0), reverse=True)
    return counters[:15]
