"""Controlled tests for the ladder_supervisor REAL-PROGRESS watchdog.

Proves the hardening that the init.log liveness watchdog lacked: a churn/forfeit
loop that keeps init.log fresh while ZERO games complete IS caught, while a bot
that is genuinely completing games (or mid a single long MCTS game) is NEVER killed.

The watchdog decision is driven by two extracted, side-effect-free units:
  * _completed_battle_count()  -- reads the completed-game ledger, race-tolerant
  * _ProgressWatch             -- tracks wall-time since the last NEW completion

Run standalone (no pytest needed):  python test_progress_watchdog.py
"""
import json
import tempfile
from pathlib import Path

import ladder_supervisor as ls

LIMIT = ls.PROGRESS_LIMIT_SEC  # production threshold (default 1800s / 30 min)


def _write_stats(path: Path, n: int) -> None:
    path.write_text(json.dumps({"battles": [{"result": "win"} for _ in range(n)]}), encoding="utf-8")


def test_count_reads_valid_missing_and_corrupt(tmp: Path):
    stats = tmp / "battle_stats.json"
    orig = ls.BATTLE_STATS
    try:
        ls.BATTLE_STATS = stats
        # missing file -> None (no reading), not 0
        assert ls._completed_battle_count() is None
        # valid file -> exact record count
        _write_stats(stats, 5)
        assert ls._completed_battle_count() == 5
        # partial / corrupt write (write_text is not atomic) -> None, never a bogus count
        stats.write_text('{"battles": [{"result": "wi', encoding="utf-8")
        assert ls._completed_battle_count() is None
        # wrong shape -> None
        stats.write_text('{"battles": 7}', encoding="utf-8")
        assert ls._completed_battle_count() is None
    finally:
        ls.BATTLE_STATS = orig


def test_completions_reset_the_clock_no_kill():
    """A bot finishing games every few minutes is NEVER killed, even over a long run."""
    now = 1000.0
    w = ls._ProgressWatch(now, baseline_count=100)
    max_stall = 0.0
    # 20 games, one every 5 min (300s) -- well within LIMIT
    for i in range(1, 21):
        now += 300.0
        w.update(now, 100 + i)
        max_stall = max(max_stall, w.stalled_for(now))
    assert max_stall == 0.0, "a fresh completion must reset the stall timer to 0"
    assert w.stalled_for(now) < LIMIT


def test_single_long_mcts_game_not_killed():
    """One long game (no completion for its whole duration) stays UNDER the threshold."""
    now = 0.0
    w = ls._ProgressWatch(now, baseline_count=100)
    # a pathologically long single game: 20 min of no completion, then it finishes
    now += 20 * 60
    assert w.stalled_for(now) < LIMIT, "20-min single game must be below the 30-min threshold"
    w.update(now, 101)
    assert w.stalled_for(now) == 0.0


def test_churn_without_completions_is_caught():
    """The doom-loop: count FROZEN while wall time marches -> stall crosses the threshold."""
    now = 0.0
    w = ls._ProgressWatch(now, baseline_count=100)
    # simulate 40 min of polling; count never advances (mirrors init.log staying fresh
    # via buffer/purge/search churn while zero games complete)
    fired_at = None
    for _ in range(int(40 * 60 / 15)):  # 15s polls
        now += 15.0
        w.update(now, 100)  # SAME count every tick
        if w.stalled_for(now) > LIMIT and fired_at is None:
            fired_at = now
    assert fired_at is not None, "a frozen completion count must trip the watchdog"
    # must fire promptly after the threshold, not drift for hours
    assert LIMIT < fired_at <= LIMIT + 30


def test_none_readings_do_not_falsely_reset_or_trip():
    """Transient unreadable polls (partial writes) neither reset the clock nor add false progress."""
    now = 0.0
    w = ls._ProgressWatch(now, baseline_count=100)
    # a real completion at t=60
    now += 60.0
    w.update(now, 101)
    anchor = w.last_progress_wall
    # then a long run of unreadable polls -> clock keeps counting from the real completion
    for _ in range(200):
        now += 15.0
        w.update(now, None)
    assert w.last_progress_wall == anchor, "None readings must not reset the stall timer"
    assert w.stalled_for(now) > LIMIT, "with no real completion, stall still accrues past LIMIT"


def test_threshold_is_conservative_vs_observed_cadence():
    """Reason the threshold against the real cadence measured from battle_stats.json:
    healthy fix-era inter-completion gaps maxed ~15 min (p90 ~9 min). LIMIT must sit
    safely above that and comfortably below the old 6h MAX_BATCH backstop."""
    worst_healthy_gap_sec = 15 * 60
    assert LIMIT >= 2 * worst_healthy_gap_sec, "threshold should be >=2x worst healthy gap"
    assert LIMIT < ls.MAX_BATCH_SEC, "threshold must fire long before the 6h backstop"
    assert LIMIT <= 45 * 60, "but not so high it re-creates a multi-hour dead window"


def _run_standalone() -> int:
    tmpdir = Path(tempfile.mkdtemp(prefix="pw_test_"))
    tests = [
        ("count_reads_valid_missing_and_corrupt", lambda: test_count_reads_valid_missing_and_corrupt(tmpdir)),
        ("completions_reset_the_clock_no_kill", test_completions_reset_the_clock_no_kill),
        ("single_long_mcts_game_not_killed", test_single_long_mcts_game_not_killed),
        ("churn_without_completions_is_caught", test_churn_without_completions_is_caught),
        ("none_readings_do_not_falsely_reset_or_trip", test_none_readings_do_not_falsely_reset_or_trip),
        ("threshold_is_conservative_vs_observed_cadence", test_threshold_is_conservative_vs_observed_cadence),
    ]
    failed = 0
    for name, fn in tests:
        try:
            fn()
            print(f"PASS  {name}")
        except AssertionError as e:
            failed += 1
            print(f"FAIL  {name}: {e}")
        except Exception as e:  # noqa: BLE001
            failed += 1
            print(f"ERROR {name}: {type(e).__name__}: {e}")
    print(f"\n{len(tests) - failed}/{len(tests)} passed  (PROGRESS_LIMIT_SEC={LIMIT}s)")
    return 1 if failed else 0


if __name__ == "__main__":
    import sys
    sys.exit(_run_standalone())
