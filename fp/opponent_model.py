import time
from collections import defaultdict

import constants
from data import all_move_json


class OpponentModel:
    """Lightweight opponent pattern memory for switches and passive turns."""

    def __init__(self):
        self._by_name = defaultdict(lambda: {
            "turns": 0,
            "switch_after_hazards": 0,
            "passive_turns": 0,
            "sacks": 0,
            "last_seen": 0.0,
        })
        self._by_battle = {}

    def observe(self, battle):
        opponent_name = getattr(battle.opponent, "account_name", None) or getattr(battle.opponent, "name", None)
        if not opponent_name:
            return

        rec = self._by_name[opponent_name]
        rec["turns"] += 1
        rec["last_seen"] = time.time()

        state = self._by_battle.get(battle.battle_tag, {})
        prev_active = state.get("active_name")
        curr_active = battle.opponent.active.name if battle.opponent.active else None
        curr_hp_ratio = None
        if battle.opponent.active and battle.opponent.active.max_hp > 0:
            curr_hp_ratio = battle.opponent.active.hp / battle.opponent.active.max_hp

        # Switch-after-hazards heuristic
        if prev_active and curr_active and curr_active != prev_active:
            hazards = (
                int(battle.opponent.side_conditions.get(constants.STEALTH_ROCK, 0) > 0)
                + battle.opponent.side_conditions.get(constants.SPIKES, 0)
            )
            if hazards > 0:
                rec["switch_after_hazards"] += 1

        # Passive turn heuristic
        last_move = getattr(battle.opponent.last_used_move, "move", "")
        if last_move:
            move_data = all_move_json.get(last_move, {})
            if move_data.get(constants.CATEGORY) == constants.STATUS:
                rec["passive_turns"] += 1

        # Sack heuristic (stays in at very low HP and faints)
        prev_hp_ratio = state.get("active_hp_ratio")
        if prev_active and prev_hp_ratio is not None and prev_hp_ratio < 0.2:
            if curr_active == prev_active and curr_hp_ratio is not None and curr_hp_ratio <= 0:
                rec["sacks"] += 1

        state["active_name"] = curr_active
        state["active_hp_ratio"] = curr_hp_ratio
        self._by_battle[battle.battle_tag] = state

    def get_switch_tendency(self, opponent_name: str) -> float:
        rec = self._by_name.get(opponent_name)
        if not rec or rec["turns"] <= 0:
            return 0.0
        return min(rec["switch_after_hazards"] / rec["turns"], 1.0)

    def get_passive_tendency(self, opponent_name: str) -> float:
        rec = self._by_name.get(opponent_name)
        if not rec or rec["turns"] <= 0:
            return 0.0
        return min(rec["passive_turns"] / rec["turns"], 1.0)


OPPONENT_MODEL = OpponentModel()
