"""
Gameplan integration module for run_battle.py.

Stores and retrieves gameplans during battles for decision layer reference.
"""

import logging
from typing import Optional, Dict
from fp.matchup_analyzer import Gameplan, analyze_matchup_from_battle

logger = logging.getLogger(__name__)

# In-memory storage for active gameplans
# battle_tag -> Gameplan
_active_gameplans: Dict[str, Gameplan] = {}


def store_gameplan(battle_tag: str, gameplan: Gameplan) -> None:
    """Store a gameplan for an active battle."""
    _active_gameplans[battle_tag] = gameplan
    logger.info(f"Stored gameplan for {battle_tag}: {gameplan.win_condition}")


def get_gameplan(battle_tag: str) -> Optional[Gameplan]:
    """Retrieve the gameplan for an active battle."""
    return _active_gameplans.get(battle_tag)


def clear_gameplan(battle_tag: str) -> None:
    """Clear the gameplan for a finished battle."""
    if battle_tag in _active_gameplans:
        del _active_gameplans[battle_tag]
        logger.debug(f"Cleared gameplan for {battle_tag}")


def generate_and_store_gameplan(battle_tag: str, battle) -> Optional[Gameplan]:
    """
    Generate a gameplan from a battle object and store it.
    
    Args:
        battle_tag: Battle identifier
        battle: Battle object with team data
    
    Returns:
        Generated Gameplan or None if generation failed
    """
    try:
        gameplan = analyze_matchup_from_battle(battle)
        if gameplan:
            store_gameplan(battle_tag, gameplan)
            return gameplan
        else:
            logger.warning(f"Failed to generate gameplan for {battle_tag}")
            return None
    except Exception as e:
        logger.error(f"Error generating gameplan for {battle_tag}: {e}")
        return None


def get_active_gameplan_count() -> int:
    """Get the number of active gameplans."""
    return len(_active_gameplans)


# ============================================================================
# Integration snippet for run_battle.py
# ============================================================================
"""
INTEGRATION INSTRUCTIONS FOR run_battle.py:

1. Import at the top:
   from fp.gameplan_integration import generate_and_store_gameplan, get_gameplan, clear_gameplan

2. After team preview completes (around line 1450 in run_async_battle):
   # Generate gameplan after team preview
   if battle.team_preview and hasattr(battle.opponent, 'reserve') and battle.opponent.reserve:
       gameplan = generate_and_store_gameplan(battle_tag, battle)
       if gameplan:
           logger.info(f"Gameplan: {gameplan.our_strategy}")
           logger.info(f"Win condition: {gameplan.win_condition}")

3. At battle end (cleanup section around line 1640):
   # Clear gameplan when battle ends
   clear_gameplan(battle_tag)

4. Optional - In async_pick_move (line 563) for decision layer reference:
   # Get gameplan for strategic alignment
   gameplan = get_gameplan(battle_tag)
   if gameplan and TRACE_DECISIONS:
       if trace is None:
           trace = build_trace_base(battle_copy, reason="gameplan_check")
       trace["gameplan"] = {
           "win_condition": gameplan.win_condition,
           "strategy": gameplan.our_strategy
       }

EXACT CODE SNIPPET FOR run_battle.py:
-------------------------------------------

# At top of file (around line 86, after other fp imports):
from fp.gameplan_integration import generate_and_store_gameplan, get_gameplan, clear_gameplan

# In run_async_battle, after team preview (around line 1450):
        # Generate strategic gameplan after seeing opponent team
        if not battle.started and hasattr(battle.opponent, 'reserve') and battle.opponent.reserve:
            try:
                gameplan = generate_and_store_gameplan(battle_tag, battle)
                if gameplan:
                    logger.info(f"[GAMEPLAN] Strategy: {gameplan.our_strategy}")
                    logger.info(f"[GAMEPLAN] Win condition: {gameplan.win_condition}")
                    logger.info(f"[GAMEPLAN] Opponent weaknesses: {', '.join(gameplan.opponent_weaknesses)}")
            except Exception as e:
                logger.warning(f"Failed to generate gameplan: {e}")

# In battle cleanup (around line 1640):
            # Clear gameplan when battle ends
            clear_gameplan(battle_tag)

# Optional - In async_pick_move for decision tracing (around line 608):
        # Reference gameplan in decision trace
        try:
            gameplan = get_gameplan(getattr(battle, 'battle_tag', ''))
            if gameplan and TRACE_DECISIONS and trace is not None:
                trace["gameplan_alignment"] = {
                    "win_condition": gameplan.win_condition,
                    "strategy": gameplan.our_strategy,
                    "key_triggers": gameplan.key_pivot_triggers
                }
        except Exception:
            pass  # Don't fail decision if gameplan lookup fails
"""
