"""Critical-clock fast path in find_best_move (inactivity-forfeit safety net).

When the side clock is critically low (<= CRITICAL_CLOCK_FAST_PATH_SECONDS), the
move search must skip ALL sampling/MCTS and return an instant heuristic move so a
CPU-starved, un-interruptible MCTS sample cannot overrun the wall clock and cause
a "lost due to inactivity" forfeit. Above that threshold (or when the clock is
unknown) the normal search path must still run.

These tests drive the real ``find_best_move`` but stub the cheap pre-search
helpers and install a *sentinel* in place of the first expensive post-fast-path
call (``detect_opponent_abilities``). If control ever reaches that sentinel the
fast path was NOT taken, which is exactly how we prove the two branches apart
without spinning up real MCTS.
"""

from types import SimpleNamespace

import pytest

from fp.search import main as fp_main


class _ExpensivePathReached(Exception):
    """Raised by the sentinel that stands in for the MCTS/search region."""


def _make_move(name="tackle", disabled=False, current_pp=35):
    return SimpleNamespace(name=name, disabled=disabled, current_pp=current_pp)


def _make_battle(time_remaining, *, force_switch=False, reserve=None):
    active = SimpleNamespace(
        moves=[_make_move()],
        types=["normal"],
        has_type=lambda t: t == "normal",
    )
    opponent_active = SimpleNamespace(types=["normal"], boosts={})
    return SimpleNamespace(
        _isolation_copy=True,  # skip the internal deepcopy
        battle_type=SimpleNamespace(name="singles"),
        team_preview=False,
        force_switch=force_switch,
        time_remaining=time_remaining,
        user=SimpleNamespace(active=active, reserve=reserve or []),
        opponent=SimpleNamespace(active=opponent_active),
    )


@pytest.fixture
def patched_search(monkeypatch):
    """Stub the cheap pre-search helpers and make the search region fatal."""
    monkeypatch.setattr(fp_main, "_maybe_hot_reload", lambda: None)
    monkeypatch.setattr(fp_main, "build_trace_base", lambda battle: {})
    monkeypatch.setattr(
        fp_main,
        "_compute_decision_budget_seconds",
        lambda battle: (
            2.0,
            {
                "clock_known": battle.time_remaining is not None,
                "remaining_clock_s": battle.time_remaining or 45.0,
                "est_turns_left": 18,
                "budget_s": 2.0,
            },
        ),
    )

    def _sentinel(_battle):
        raise _ExpensivePathReached("MCTS/search region was entered")

    # First expensive call after the fast path: if hit, the fast path was skipped.
    monkeypatch.setattr(fp_main, "detect_opponent_abilities", _sentinel)
    return monkeypatch


def test_critical_clock_takes_fast_path_without_mcts(patched_search):
    battle = _make_battle(time_remaining=5)

    move, trace = fp_main.find_best_move(battle)

    # A valid, non-empty heuristic move was produced...
    assert isinstance(move, str) and move
    assert move == "tackle"
    # ...via the fast path (proven by the trace flag) and WITHOUT touching MCTS
    # (the detect_opponent_abilities sentinel would have raised otherwise).
    assert trace.get("critical_clock_fast_path") is True
    assert trace.get("decision_mode") == "critical_clock_fast_path"
    assert trace.get("choice") == move


def test_critical_clock_force_switch_returns_valid_switch(patched_search):
    reserve = [SimpleNamespace(name="corviknight", hp=100)]
    battle = _make_battle(time_remaining=3, force_switch=True, reserve=reserve)

    move, trace = fp_main.find_best_move(battle)

    assert trace.get("critical_clock_fast_path") is True
    assert move == "switch corviknight"


def test_healthy_clock_does_not_take_fast_path(patched_search):
    battle = _make_battle(time_remaining=120)

    # Above the threshold the normal path runs and reaches the sentinel.
    with pytest.raises(_ExpensivePathReached):
        fp_main.find_best_move(battle)


def test_unknown_clock_does_not_take_fast_path(patched_search):
    battle = _make_battle(time_remaining=None)

    # Unknown clock must NOT trigger the fast path.
    with pytest.raises(_ExpensivePathReached):
        fp_main.find_best_move(battle)


def test_threshold_is_env_tunable_and_conservative():
    # Default is a small, conservative band -- not so large it disables search.
    assert isinstance(fp_main.CRITICAL_CLOCK_FAST_PATH_SECONDS, int)
    assert 0 < fp_main.CRITICAL_CLOCK_FAST_PATH_SECONDS <= 15
