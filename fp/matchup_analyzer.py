"""
Matchup Analyzer - Pre-battle strategy generator for Fouler Play.

Generates conservative, deterministic gameplans by default. The historical
local qwen/Ollama path is opt-in only and must pass Gen 9 oracle validation
before a gameplan can be cached or exposed to battle code.
"""

import json
import hashlib
import os
import logging
from typing import Dict, List, Optional, Set
from dataclasses import dataclass, asdict
from pathlib import Path
import requests

from fp.team_analysis import analyze_team, TeamAnalysis
from fp.helpers import normalize_name
from constants_pkg.strategy import SETUP_MOVES, PRIORITY_MOVES
from fp.playstyle_config import HAZARD_MOVES, PIVOT_MOVES, RECOVERY_MOVES
from fp.theknower_competitive import build_competitive_meta_context, build_pokedex_oracle_context
from infrastructure.gen9_validation import Gen9Validator
from infrastructure.runtime_paths import resolve_runtime_paths

logger = logging.getLogger(__name__)

# Cache directory for matchup analysis results
PROJECT_ROOT = Path(__file__).resolve().parents[1]
LEGACY_CACHE_DIR = PROJECT_ROOT / "data" / "matchup_cache"
CACHE_DIR = resolve_runtime_paths(PROJECT_ROOT).cache_root / "matchup"

# Ollama API endpoint
OLLAMA_API_URL = os.getenv("OLLAMA_API_URL", "http://localhost:11434")
OLLAMA_MODEL = "qwen2.5-coder:3b"
ENABLE_LLM_GAMEPLAN = os.getenv("MATCHUP_ANALYZER_ENABLE_LLM", "").lower() in {"1", "true", "yes", "on"}

# Timeout for LLM requests (seconds)
OLLAMA_TIMEOUT = int(os.getenv("MATCHUP_ANALYZER_TIMEOUT", "30"))


@dataclass
class Gameplan:
    """Structured gameplan output from matchup analysis."""
    opponent_win_condition: str
    opponent_weaknesses: List[str]
    our_strategy: str
    key_pivot_triggers: List[str]
    win_condition: str
    lead_preference: Optional[str] = None
    backup_plan: Optional[str] = None
    
    def to_dict(self) -> Dict:
        return asdict(self)
    
    @classmethod
    def from_dict(cls, data: Dict) -> "Gameplan":
        return cls(**data)


def _hash_team(team_data: List[Dict]) -> str:
    """Generate a deterministic hash for a team composition."""
    # Sort team by species name for consistent hashing
    sorted_team = sorted(team_data, key=lambda p: p.get("species", ""))
    # Use species + moves + item + ability as hash input
    hash_input = []
    for pkmn in sorted_team:
        species = normalize_name(pkmn.get("species", ""))
        moves = sorted([normalize_name(m) for m in pkmn.get("moves", [])])
        item = normalize_name(pkmn.get("item", ""))
        ability = normalize_name(pkmn.get("ability", ""))
        hash_input.append(f"{species}|{','.join(moves)}|{item}|{ability}")
    
    combined = "||".join(hash_input)
    return hashlib.sha256(combined.encode()).hexdigest()[:16]


def _load_cached_gameplan(our_hash: str, opp_hash: str) -> Optional[Gameplan]:
    """Load a cached gameplan if it exists."""
    cache_file = CACHE_DIR / f"{our_hash}_vs_{opp_hash}.json"
    read_file = cache_file
    legacy_file = LEGACY_CACHE_DIR / cache_file.name
    if not read_file.exists() and legacy_file != read_file and legacy_file.exists():
        read_file = legacy_file
    if not read_file.exists():
        return None
    
    try:
        with open(read_file, "r") as f:
            data = json.load(f)
        return Gameplan.from_dict(data)
    except Exception as e:
        logger.warning(f"Failed to load cached gameplan: {e}")
        return None


def _save_gameplan_cache(our_hash: str, opp_hash: str, gameplan: Gameplan) -> None:
    """Save a gameplan to cache."""
    cache_file = CACHE_DIR / f"{our_hash}_vs_{opp_hash}.json"
    try:
        CACHE_DIR.mkdir(parents=True, exist_ok=True)
        with open(cache_file, "w") as f:
            json.dump(gameplan.to_dict(), f, indent=2)
    except Exception as e:
        logger.warning(f"Failed to save gameplan cache: {e}")


def _build_analysis_prompt(our_team: TeamAnalysis, our_team_data: List[Dict], 
                           opp_team: TeamAnalysis, opp_team_data: List[Dict]) -> str:
    """Build a concise prompt for the LLM to generate a gameplan."""
    
    # Summarize our team
    our_summary = []
    for pkmn in our_team_data:
        species = pkmn.get("species", "")
        moves = ", ".join(pkmn.get("moves", [])[:4])
        item = pkmn.get("item", "")
        our_summary.append(f"  - {species} ({item}): {moves}")
    
    # Summarize opponent team
    opp_summary = []
    for pkmn in opp_team_data:
        species = pkmn.get("species", "")
        moves = ", ".join(pkmn.get("moves", [])[:4]) if pkmn.get("moves") else "unknown moves"
        item = pkmn.get("item", "") or "unknown item"
        opp_summary.append(f"  - {species} ({item}): {moves}")
    
    # Build strategic context
    our_context = []
    if our_team.hazard_setters:
        our_context.append(f"Hazard setters: {', '.join(our_team.hazard_setters)}")
    if our_team.wincons:
        our_context.append(f"Win conditions: {', '.join(our_team.wincons)}")
    if our_team.pivots:
        our_context.append(f"Pivots: {', '.join(our_team.pivots)}")
    
    opp_context = []
    if opp_team.hazard_setters:
        opp_context.append(f"Hazard setters: {', '.join(opp_team.hazard_setters)}")
    if opp_team.hazard_removers:
        opp_context.append(f"Hazard removal: {', '.join(opp_team.hazard_removers)}")
    if opp_team.wincons:
        opp_context.append(f"Win conditions: {', '.join(opp_team.wincons)}")
    
    competitive_oracle = build_competitive_meta_context(
        species=[pkmn.get("species", "") for pkmn in [*our_team_data, *opp_team_data]],
    )
    pokedex_oracle = build_pokedex_oracle_context(our_team_data, opp_team_data)

    prompt = f"""You are a competitive Pokemon battler analyzing a Gen 9 OU matchup.

**KNOWER COMPETITIVE ORACLE:**
{competitive_oracle}

**POKEDEX ORACLE:**
{pokedex_oracle}

**OUR TEAM ({our_team.playstyle.name}):**
{chr(10).join(our_summary)}

**OPPONENT TEAM ({opp_team.playstyle.name}):**
{chr(10).join(opp_summary)}

**OUR TEAM ROLES:**
{chr(10).join(our_context) if our_context else "Standard balanced team"}

**OPPONENT TEAM ROLES:**
{chr(10).join(opp_context) if opp_context else "Standard balanced team"}

Generate a concise battle gameplan in JSON format. Be specific but brief (2-3 sentences per field max).

Output ONLY valid JSON (no markdown, no explanation):
{{
  "opponent_win_condition": "How opponent tries to win (e.g., 'Hazard stack + Dondozo sweep', 'Speed control + wallbreakers')",
  "opponent_weaknesses": ["Specific exploitable weaknesses", "2-3 bullet points"],
  "our_strategy": "Our primary strategy to win this matchup (2-3 sentences)",
  "key_pivot_triggers": ["If X happens, switch to Y", "Specific situation-based pivots"],
  "win_condition": "Our most reliable path to victory (e.g., 'Kyurem sweep after removing Corviknight')",
  "lead_preference": "Best lead Pokemon and why (optional)",
  "backup_plan": "Fallback if primary strategy fails (optional)"
}}"""
    
    return prompt


def _call_ollama(prompt: str) -> Optional[str]:
    """Call Ollama API to generate gameplan."""
    try:
        response = requests.post(
            f"{OLLAMA_API_URL}/api/generate",
            json={
                "model": OLLAMA_MODEL,
                "prompt": prompt,
                "stream": False,
                "options": {
                    "temperature": 0.3,  # Lower temp for more focused strategic analysis
                    "num_predict": 512,  # Limit response length
                }
            },
            timeout=OLLAMA_TIMEOUT
        )
        
        if response.status_code != 200:
            logger.error(f"Ollama API returned status {response.status_code}")
            return None
        
        result = response.json()
        return result.get("response", "").strip()
    
    except requests.exceptions.Timeout:
        logger.warning(f"Ollama request timed out after {OLLAMA_TIMEOUT}s")
        return None
    except Exception as e:
        logger.error(f"Ollama API error: {e}")
        return None


def _parse_gameplan_json(response: str) -> Optional[Gameplan]:
    """Parse LLM response into a Gameplan object."""
    try:
        # Strip markdown code blocks if present
        response = response.strip()
        if response.startswith("```"):
            # Remove code block markers
            lines = response.split("\n")
            response = "\n".join(lines[1:-1] if lines[-1].strip() == "```" else lines[1:])
            response = response.replace("```json", "").replace("```", "").strip()
        
        data = json.loads(response)
        
        # Validate required fields
        required = ["opponent_win_condition", "opponent_weaknesses", "our_strategy", 
                   "key_pivot_triggers", "win_condition"]
        for field in required:
            if field not in data:
                logger.error(f"Missing required field in gameplan: {field}")
                return None
        
        return Gameplan(
            opponent_win_condition=data["opponent_win_condition"],
            opponent_weaknesses=data["opponent_weaknesses"],
            our_strategy=data["our_strategy"],
            key_pivot_triggers=data["key_pivot_triggers"],
            win_condition=data["win_condition"],
            lead_preference=data.get("lead_preference"),
            backup_plan=data.get("backup_plan")
        )
    
    except json.JSONDecodeError as e:
        logger.error(f"Failed to parse gameplan JSON: {e}\nResponse: {response}")
        return None
    except Exception as e:
        logger.error(f"Error parsing gameplan: {e}")
        return None


def _gameplan_validation_text(gameplan: Gameplan) -> str:
    """Flatten a gameplan for Gen 9 oracle validation."""
    parts = [
        gameplan.opponent_win_condition,
        *gameplan.opponent_weaknesses,
        gameplan.our_strategy,
        *gameplan.key_pivot_triggers,
        gameplan.win_condition,
        gameplan.lead_preference or "",
        gameplan.backup_plan or "",
    ]
    return "\n".join(str(part) for part in parts if part)


def _validate_gameplan(gameplan: Gameplan) -> list[str]:
    """Return blockers for gameplans containing unsupported Pokemon claims."""
    validator = Gen9Validator()
    is_valid, errors, warnings = validator.validate_analysis(_gameplan_validation_text(gameplan))
    if not is_valid:
        return errors
    # Warnings are not fatal for human-readable Discord text, but gameplans can
    # influence battle behavior later. Keep them out of the active cache.
    return warnings


def _create_fallback_gameplan(our_team: TeamAnalysis, opp_team: TeamAnalysis) -> Gameplan:
    """Create a basic fallback gameplan if LLM fails."""
    logger.warning("Using deterministic fallback gameplan")
    
    # Identify opponent threats
    opp_threats = list(opp_team.wincons) if opp_team.wincons else ["Unknown threats"]
    
    # Identify our win condition
    our_wincon = list(our_team.wincons)[0] if our_team.wincons else "Standard play"
    
    return Gameplan(
        opponent_win_condition=f"{opp_team.playstyle.name} strategy with {', '.join(opp_threats[:2])}",
        opponent_weaknesses=[
            "Require replay/protocol proof before making matchup-specific claims",
            "Use only current Showdown request and battle-state truth for tactical pivots",
        ],
        our_strategy=f"Execute the {our_team.playstyle.name} team plan while preserving {our_wincon} until battle evidence proves a different line.",
        key_pivot_triggers=[
            "Only pivot when the current Showdown request exposes the legal switch candidate",
            "Avoid model-only assumptions about opponent moves, abilities, or damage ranges",
        ],
        win_condition=f"Advance the deterministic engine plan around {our_wincon}; do not override the engine from this fallback text.",
        lead_preference=None,
        backup_plan="Collect replay/protocol evidence and keep unsupported strategy claims out of active policy."
    )


def analyze_matchup(our_team_data: List[Dict], opponent_team_data: List[Dict], 
                    use_cache: bool = True) -> Gameplan:
    """
    Analyze a team matchup and generate a strategic gameplan.
    
    Args:
        our_team_data: List of dicts with our Pokemon (species, moves, item, ability, evs)
        opponent_team_data: List of dicts with opponent Pokemon (same format)
        use_cache: Whether to use cached results if available
    
    Returns:
        Gameplan object with strategic recommendations
    """
    # Generate team hashes for caching
    our_hash = _hash_team(our_team_data)
    opp_hash = _hash_team(opponent_team_data)
    
    # Check cache first
    if use_cache:
        cached = _load_cached_gameplan(our_hash, opp_hash)
        if cached:
            logger.info(f"Loaded cached gameplan for matchup {our_hash[:8]} vs {opp_hash[:8]}")
            return cached
    
    # Analyze both teams
    our_team = analyze_team(our_team_data)
    opp_team = analyze_team(opponent_team_data)
    
    logger.info(f"Analyzing matchup: {our_team.playstyle.name} vs {opp_team.playstyle.name}")
    
    if ENABLE_LLM_GAMEPLAN:
        prompt = _build_analysis_prompt(our_team, our_team_data, opp_team, opponent_team_data)
        response = _call_ollama(prompt)

        if response:
            gameplan = _parse_gameplan_json(response)
            if gameplan:
                blockers = _validate_gameplan(gameplan)
                if not blockers:
                    logger.info(f"Generated validated gameplan: {gameplan.win_condition}")
                    if use_cache:
                        _save_gameplan_cache(our_hash, opp_hash, gameplan)
                    return gameplan
                logger.warning("Rejected LLM gameplan with Gen 9 validation blocker(s): %s", "; ".join(blockers))
    else:
        logger.info("MATCHUP_ANALYZER_ENABLE_LLM is not enabled; skipping local LLM gameplan generation")
    
    # Fallback if LLM fails
    gameplan = _create_fallback_gameplan(our_team, opp_team)
    if use_cache:
        _save_gameplan_cache(our_hash, opp_hash, gameplan)
    return gameplan


def analyze_matchup_from_battle(battle) -> Optional[Gameplan]:
    """
    Convenience function to analyze a matchup from a Battle object.
    
    Args:
        battle: Battle object with user and opponent teams
    
    Returns:
        Gameplan object, or None if team data unavailable
    """
    # Extract our team data
    our_team_data = getattr(battle.user, "team_dict", None)
    if not our_team_data:
        logger.warning("Our team data not available for matchup analysis")
        return None
    
    # Extract opponent team data
    # In team preview, opponent Pokemon are in battle.opponent.reserve
    opp_team_data = []
    if hasattr(battle, "opponent") and hasattr(battle.opponent, "reserve"):
        for pkmn in battle.opponent.reserve:
            if pkmn is None:
                continue
            # Build opponent Pokemon dict from revealed info
            pkmn_dict = {
                "species": pkmn.name,
                "moves": [m.name for m in pkmn.moves if m.name],
                "item": getattr(pkmn, "item", "") or "",
                "ability": getattr(pkmn, "ability", "") or "",
            }
            opp_team_data.append(pkmn_dict)
    
    if not opp_team_data:
        logger.warning("Opponent team data not available for matchup analysis")
        return None
    
    return analyze_matchup(our_team_data, opp_team_data)


if __name__ == "__main__":
    # Test with sample teams
    our_team = [
        {"species": "Corviknight", "moves": ["Brave Bird", "Defog", "Roost", "U-turn"], 
         "item": "Rocky Helmet", "ability": "Pressure"},
        {"species": "Dondozo", "moves": ["Wave Crash", "Earthquake", "Order Up", "Rest"],
         "item": "Leftovers", "ability": "Unaware"},
        {"species": "Kyurem", "moves": ["Freeze-Dry", "Earth Power", "Substitute", "Protect"],
         "item": "Leftovers", "ability": "Pressure"},
    ]
    
    opp_team = [
        {"species": "Landorus-Therian", "moves": ["Stealth Rock", "Earthquake", "U-turn", "Taunt"],
         "item": "Rocky Helmet", "ability": "Intimidate"},
        {"species": "Great Tusk", "moves": ["Earthquake", "Ice Spinner", "Rapid Spin", "Knock Off"],
         "item": "Booster Energy", "ability": "Protosynthesis"},
        {"species": "Iron Crown", "moves": ["Tachyon Cutter", "Volt Switch", "Psyshock", "Focus Blast"],
         "item": "Booster Energy", "ability": "Quark Drive"},
    ]
    
    gameplan = analyze_matchup(our_team, opp_team, use_cache=False)
    print(json.dumps(gameplan.to_dict(), indent=2))
