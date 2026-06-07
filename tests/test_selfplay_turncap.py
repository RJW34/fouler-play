"""selfplay_eval turn-cap plumbing + fast-team default (ROOT 3 gate viability)."""
import argparse
import importlib.util
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
spec = importlib.util.spec_from_file_location(
    "selfplay_eval", ROOT / "infrastructure" / "selfplay_eval.py"
)
sp = importlib.util.module_from_spec(spec)
spec.loader.exec_module(sp)


def test_build_env_sets_turn_cap(tmp_path):
    env = sp._build_env(None, 1200, tmp_path / "stats.json", turn_cap=42)
    assert env["FOULER_BATTLE_TURN_CAP"] == "42"
    # the eval must never pollute the live ladder stats file
    assert env["BATTLE_STATS_FILE"] == str(tmp_path / "stats.json")


def test_build_env_default_turn_cap_is_positive(tmp_path):
    env = sp._build_env(None, 1200, tmp_path / "s.json")
    assert int(env["FOULER_BATTLE_TURN_CAP"]) == sp.DEFAULT_TURN_CAP
    assert sp.DEFAULT_TURN_CAP > 0  # the gate ALWAYS caps


def test_default_teams_prefer_fast_non_stall_set():
    # _load_teams with no explicit args should resolve to the fast eval set,
    # which must NOT contain the slow pure-stall mirror as the sole team.
    args = argparse.Namespace(teams=None, teams_from=None)
    teams = sp._load_teams(args)
    assert teams, "fast default team set must be non-empty"
    assert "gen9/ou/fat-team-1-stall" not in teams


def test_default_teams_file_exists_on_disk():
    assert (ROOT / "teams" / "eval-fast-teams.list").exists()


def test_burst_runner_forwards_turn_cap_to_harness_and_env():
    text = (ROOT / "infrastructure" / "run_selfplay_burst.ps1").read_text(encoding="utf-8")

    assert "FOULER_BATTLE_TURN_CAP" in text
    assert "--turn-cap" in text
    assert "FOULER_MAX_TURNS" not in text
