"""Unit tests for the self-play TURN CAP / score-on-cap (ROOT 3 gate viability).

The self-play gate could never reach decisive>=N because a stall mirror runs
~1 turn/70s and is killed by per_battle_timeout before any natural |win|. The
fix: a hard per-battle turn cap that FORCE-DECIDES via an HP-fraction "score on
cap" -- the lower-HP side forfeits, Showdown emits a real |win|, and the battle
counts as DECISIVE (not discarded). These tests pin that decision logic without
a live Showdown server.
"""
import importlib.util
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent

# --- load fp.run_battle's pure helpers WITHOUT importing the heavy package ----
# run_battle imports config/data at module import; that is fine offline.
import sys
sys.path.insert(0, str(ROOT))
import fp.run_battle as rb  # noqa: E402


class _FakePkmn:
    def __init__(self, hp, max_hp):
        self.hp = hp
        self.max_hp = max_hp


class _FakeBattler:
    def __init__(self, name, active, reserve):
        self.name = name
        self.active = active
        self.reserve = reserve


class _FakeBattle:
    def __init__(self, user, opponent):
        self.user = user
        self.opponent = opponent


def _battler(name, mons):
    pk = [_FakePkmn(hp, mx) for hp, mx in mons]
    return _FakeBattler(name, pk[0] if pk else None, pk[1:])


# --- _hp_fraction_sum ---------------------------------------------------------

def test_hp_fraction_sum_counts_active_and_reserve_fainted_as_zero():
    b = _battler("p1", [(100, 100), (50, 100), (0, 100)])
    # 1.0 + 0.5 + 0.0
    assert abs(rb._hp_fraction_sum(b) - 1.5) < 1e-9


def test_hp_fraction_sum_handles_none_active_and_missing_fields():
    b = _FakeBattler("p1", None, [_FakePkmn(30, 60)])
    assert abs(rb._hp_fraction_sum(b) - 0.5) < 1e-9
    b2 = _FakeBattler("p1", None, [])
    assert rb._hp_fraction_sum(b2) == 0.0


# --- score_on_cap: the side with lower HP forfeits ----------------------------

def test_score_on_cap_lower_hp_side_forfeits():
    # we are clearly behind -> we forfeit
    battle = _FakeBattle(
        user=_battler("p1", [(10, 100), (0, 100)]),       # 0.1
        opponent=_battler("p2", [(100, 100), (100, 100)]),  # 2.0
    )
    d = rb.score_on_cap(battle)
    assert d["we_forfeit"] is True
    assert d["my_hp_sum"] < d["opp_hp_sum"]


def test_score_on_cap_higher_hp_side_holds():
    battle = _FakeBattle(
        user=_battler("p1", [(100, 100), (100, 100)]),  # 2.0
        opponent=_battler("p2", [(10, 100)]),            # 0.1
    )
    d = rb.score_on_cap(battle)
    assert d["we_forfeit"] is False


# --- the critical property: EXACTLY ONE side forfeits on an exact tie ---------
# Both self-play engines run the SAME code; if both stayed (or both forfeited)
# on a tie, the battle would never resolve. The slot-id tiebreak must make the
# two symmetric perspectives disagree.

def test_score_on_cap_exact_tie_breaks_so_exactly_one_forfeits():
    # p1's perspective: user=p1, opponent=p2, equal HP
    p1_view = _FakeBattle(
        user=_battler("p1", [(100, 100)]),
        opponent=_battler("p2", [(100, 100)]),
    )
    # p2's perspective is the mirror: user=p2, opponent=p1, equal HP
    p2_view = _FakeBattle(
        user=_battler("p2", [(100, 100)]),
        opponent=_battler("p1", [(100, 100)]),
    )
    d1 = rb.score_on_cap(p1_view)["we_forfeit"]
    d2 = rb.score_on_cap(p2_view)["we_forfeit"]
    # exactly one of the two engines forfeits -> battle resolves decisively
    assert d1 != d2
    # by the documented rule, the later slot id (p2) is the one that forfeits
    assert d2 is True and d1 is False
