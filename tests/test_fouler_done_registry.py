"""Tests for the fouler done registry, its scorer, and the loss-hypothesis burndown.

The load-bearing test is `test_a_self_generated_elo_proof_does_not_move_the_score`:
truth/latest-elo-proof.json is a report this project writes about its own
performance. The scorer must recompute from battle records instead.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
for candidate in (str(ROOT), str(ROOT / "scripts")):
    if candidate not in sys.path:
        sys.path.insert(0, candidate)

import build_done_registry as B  # noqa: E402
import done_registry_score as S  # noqa: E402
import loss_hypothesis_burndown as BD  # noqa: E402


def _battles(tmp_path: Path, battles: list[dict]) -> Path:
    tmp_path.mkdir(parents=True, exist_ok=True)
    (tmp_path / "battle_stats.json").write_text(
        json.dumps({"battles": battles}), encoding="utf-8")
    return tmp_path


def _hypothesis(tmp_path: Path, **fields) -> Path:
    directory = tmp_path / "learning" / "hypotheses"
    directory.mkdir(parents=True, exist_ok=True)
    payload = {"schemaVersion": "fouler-hypothesis/v1", "status": "open",
               "closedAt": None, "evidence": [],
               "measurement": {"deltaELO": None, "eloAfter": None, "eloBefore": None},
               **fields}
    (directory / f"{payload['id']}.json").write_text(json.dumps(payload), encoding="utf-8")
    return directory


def _sustain_battles(n: int, rating: float, team: str = "fat-team-1-stall",
                     result: str = "win") -> list[dict]:
    return [{"battle_id": f"b{i}", "rating": rating, "result": result,
             "team_file": team} for i in range(n)]


# ---------------------------------------------------------------- registry

def test_registry_is_in_sync_with_the_ladder_contract():
    committed = json.loads(B.REGISTRY_PATH.read_text(encoding="utf-8"))
    assert committed == B.build_registry(), (
        "data/completion/done_registry.json is stale — "
        "run `python scripts/build_done_registry.py`"
    )


def test_registry_target_is_the_canonical_1700():
    import fouler_mission_monitor as M

    registry = B.build_registry()
    assert registry["contract"]["targetRating"] == M.CANONICAL_TARGET_RATING == 1700


def test_registry_covers_both_halves_of_the_owner_goal():
    """1700 sustained AND the learn loop closed. Neither half may be dropped."""
    registry = B.build_registry()
    groups = {e["group"] for e in registry["entries"]}
    assert groups == {"elo", "learn-loop"}
    assert sum(1 for e in registry["entries"] if e["group"] == "learn-loop") >= 5


def test_registry_records_the_sources_it_was_derived_from():
    registry = B.build_registry()
    assert registry["generatedFrom"]
    for rel, digest in registry["generatedFrom"].items():
        assert (ROOT / rel).exists()
        assert len(digest) == 64


# ------------------------------------------------------------------ scorer

def test_no_battle_records_makes_elo_entries_unverifiable(tmp_path):
    registry = B.build_registry()
    report = S.score(registry, tmp_path, [])
    elo_rows = [r for r in report["entries"] if r["group"] == "elo"]
    assert all(r["state"] == S.UNVERIFIABLE for r in elo_rows)
    assert report["counts"][S.COMPLETE] == 0


def test_counts_always_partition_the_entries(tmp_path):
    _battles(tmp_path, _sustain_battles(5, 1400.0))
    registry = B.build_registry()
    report = S.score(registry, tmp_path, [])
    assert sum(report["counts"].values()) == report["total"] == len(report["entries"])


def test_peak_below_the_floor_scores_the_stage_incomplete(tmp_path):
    _battles(tmp_path, _sustain_battles(40, 1591.0))
    registry = B.build_registry()
    report = S.score(registry, tmp_path, [])
    by_id = {r["id"]: r for r in report["entries"]}
    assert by_id["elo/prove-1600"]["state"] == S.INCOMPLETE
    assert by_id["elo/prove-1700"]["state"] == S.INCOMPLETE
    assert by_id["elo/sustain-window-games"]["state"] == S.INCOMPLETE


def test_a_full_sustain_window_scores_the_elo_entries_complete(tmp_path):
    """The gate must be passable — a permanently-red light trains everyone to ignore it."""
    battles = []
    for team in ("fat-team-1-stall", "fat-team-2-pivot", "fat-team-3-dondozo"):
        battles.extend(_sustain_battles(12, 1750.0, team=team, result="win"))
    _battles(tmp_path, battles)
    registry = B.build_registry()
    report = S.score(registry, tmp_path, [])
    by_id = {r["id"]: r for r in report["entries"]}
    for entry_id in ("elo/prove-1600", "elo/prove-1700", "elo/sustain-1700",
                     "elo/sustain-window-games", "elo/sustain-win-rate",
                     "elo/sustain-team-coverage", "elo/sustain-drawdown"):
        assert by_id[entry_id]["state"] == S.COMPLETE, entry_id


def test_a_losing_sustain_window_fails_the_win_rate(tmp_path):
    battles = []
    for team in ("fat-team-1-stall", "fat-team-2-pivot", "fat-team-3-dondozo"):
        battles.extend(_sustain_battles(12, 1750.0, team=team, result="loss"))
    _battles(tmp_path, battles)
    registry = B.build_registry()
    report = S.score(registry, tmp_path, [])
    by_id = {r["id"]: r for r in report["entries"]}
    assert by_id["elo/sustain-win-rate"]["state"] == S.INCOMPLETE


def test_a_self_generated_elo_proof_does_not_move_the_score(tmp_path):
    """A process must never grade its own homework (venture operating rule 9).

    truth/latest-elo-proof.json is written by this project about its own
    performance. Here it claims the 1700 target passes and is sustained, while
    the battle records show a peak of 1400. The score must follow the records.
    """
    _battles(tmp_path, _sustain_battles(50, 1400.0))
    truth = tmp_path / "truth"
    truth.mkdir(parents=True, exist_ok=True)
    (truth / "latest-elo-proof.json").write_text(json.dumps({
        "summary": {"passesTarget": True, "sustainedTarget": True,
                    "gamesAtOrAboveFloor": 99, "peakRating": 1900,
                    "currentRating": 1850},
    }), encoding="utf-8")

    registry = B.build_registry()
    report = S.score(registry, tmp_path, [])
    by_id = {r["id"]: r for r in report["entries"]}
    assert by_id["elo/prove-1700"]["state"] == S.INCOMPLETE, (
        "the scorer believed a report the project wrote about itself"
    )
    assert by_id["elo/sustain-window-games"]["state"] == S.INCOMPLETE
    assert "1400" in by_id["elo/prove-1700"]["reason"]


# ---------------------------------------------------------------- burndown

def test_join_recovers_battle_ids_from_evidence_lines():
    hypothesis = {"evidence": ["battle-abc: loss lasted 45 turns",
                               "battle-def: loss lasted 46 turns",
                               "unknown: nothing"]}
    assert BD.cited_battle_ids(hypothesis) == {"battle-abc", "battle-def"}


def test_losses_without_a_hypothesis_are_counted(tmp_path):
    _battles(tmp_path, [
        {"battle_id": "b1", "result": "loss", "rating": 1400},
        {"battle_id": "b2", "result": "loss", "rating": 1400},
        {"battle_id": "b3", "result": "win", "rating": 1400},
    ])
    _hypothesis(tmp_path, id="h1", failureClass="endgame_conversion",
                evidence=["b1: loss lasted 45 turns"])
    report = BD.build_burndown(tmp_path, [])
    assert report["counts"]["losses"] == 2
    assert report["counts"]["lossesWithHypothesis"] == 1
    assert report["counts"]["lossesWithoutHypothesis"] == 1
    assert report["lossesWithoutHypothesis"] == ["b2"]


def test_an_open_hypothesis_is_unconverted(tmp_path):
    _battles(tmp_path, [{"battle_id": "b1", "result": "loss", "rating": 1400}])
    _hypothesis(tmp_path, id="h1", failureClass="endgame_conversion", status="open")
    report = BD.build_burndown(tmp_path, [])
    assert report["counts"]["hypothesesUnconverted"] == 1
    reasons = report["hypothesesUnconverted"][0]["reasons"]
    assert any("not terminal" in r for r in reasons)
    assert any("never tested" in r for r in reasons)


def test_an_implemented_but_unmeasured_hypothesis_is_still_unconverted(tmp_path):
    """'implemented' is not 'tested'. Only a measured verdict closes the loop."""
    _battles(tmp_path, [{"battle_id": "b1", "result": "loss", "rating": 1400}])
    _hypothesis(tmp_path, id="h1", failureClass="endgame_conversion",
                status="implemented")
    report = BD.build_burndown(tmp_path, [])
    assert report["counts"]["hypothesesUnconverted"] == 1


def test_a_kept_and_measured_hypothesis_is_converted(tmp_path):
    _battles(tmp_path, [{"battle_id": "b1", "result": "loss", "rating": 1400}])
    _hypothesis(tmp_path, id="h1", failureClass="search_regret", status="kept",
                closedAt="2026-07-20T00:00:00Z",
                evidence=["b1: x"],
                measurement={"deltaELO": 12.0, "eloAfter": 1412, "eloBefore": 1400})
    report = BD.build_burndown(tmp_path, [])
    assert report["counts"]["hypothesesUnconverted"] == 0
    assert report["counts"]["totalBurndown"] == 0


def test_detector_diversity_fails_when_only_templated_classes_appear(tmp_path):
    _battles(tmp_path, [{"battle_id": "b1", "result": "loss", "rating": 1400}])
    _hypothesis(tmp_path, id="h1", failureClass="endgame_conversion")
    registry = B.build_registry()
    report = S.score(registry, tmp_path, [])
    by_id = {r["id"]: r for r in report["entries"]}
    assert by_id["learn-loop/detector-diversity"]["state"] == S.INCOMPLETE


def test_detector_diversity_passes_when_a_grounded_class_appears(tmp_path):
    _battles(tmp_path, [{"battle_id": "b1", "result": "loss", "rating": 1400}])
    _hypothesis(tmp_path, id="h1", failureClass="search_regret")
    registry = B.build_registry()
    report = S.score(registry, tmp_path, [])
    by_id = {r["id"]: r for r in report["entries"]}
    assert by_id["learn-loop/detector-diversity"]["state"] == S.COMPLETE


def test_an_implementing_commit_predating_its_hypothesis_is_caught(tmp_path):
    """A fix cannot predate the hypothesis it fixes.

    The closer's git --grep deliberately has no --since filter, so a detector's
    own birth commit can certify every hypothesis that detector will ever emit.
    """
    _battles(tmp_path, [{"battle_id": "b1", "result": "loss", "rating": 1400}])
    _hypothesis(tmp_path, id="h1", failureClass="endgame_conversion",
                status="implemented", openedAt="2026-07-20T04:08:31Z",
                implementation={"commit": "c4621284", "committedAt": "2026-05-20 21:02:22 -0400"})
    registry = B.build_registry()
    report = S.score(registry, tmp_path, [])
    by_id = {r["id"]: r for r in report["entries"]}
    row = by_id["learn-loop/implementation-after-hypothesis"]
    assert row["state"] == S.INCOMPLETE
    assert "predate" in row["reason"]


def test_a_missing_battle_store_makes_terminal_states_unreachable(tmp_path):
    registry = B.build_registry()
    report = S.score(registry, tmp_path, [])
    by_id = {r["id"]: r for r in report["entries"]}
    row = by_id["learn-loop/measurement-store-resolvable"]
    assert row["state"] == S.INCOMPLETE
    assert "unreachable" in row["reason"]
