import json

from infrastructure import ladder_trajectory


def test_trajectory_from_battles_uses_authoritative_elo_after_fields():
    battles = [
        {"battle_tag": "battle-gen9ou-1", "result": "loss", "elo_after": 1200, "elo_delta": -20},
        {"battle_tag": "battle-gen9ou-2", "result": "win", "rating": 1230, "elo_delta": 30},
        {"battle_tag": "battle-gen9ou-3", "result": "win", "elo_after": 1260, "elo_delta": 30},
    ]

    traj = ladder_trajectory.trajectory_from_battles(battles, recent_window=3)

    assert traj["current_elo"] == 1260
    assert traj["peak_elo"] == 1260
    assert traj["rated_games"] == 3
    assert traj["authoritative_elo_games"] == 2
    assert traj["fallback_rating_games"] == 1
    assert traj["remaining_to_target"] == 440
    assert traj["recent_slope_per_game"] == 30
    assert traj["games_to_target_at_rate"] == 15
    assert 0 < traj["progress_fraction_1000_to_target"] < 1
    assert traj["recent_points"][-1]["battle_id"] == "battle-gen9ou-3"


def test_trajectory_decline_has_no_games_to_target_estimate():
    battles = [
        {"battle_tag": "battle-gen9ou-1", "elo_after": 1260},
        {"battle_tag": "battle-gen9ou-2", "elo_after": 1240},
    ]

    traj = ladder_trajectory.trajectory_from_battles(battles)

    assert traj["recent_slope_per_game"] == -20
    assert traj["games_to_target_at_rate"] is None


def test_trajectory_loads_battle_stats_dict_shape(tmp_path):
    path = tmp_path / "battle_stats.json"
    path.write_text(
        json.dumps({"battles": [{"battle_id": "b1", "rating_after": 1100}]}),
        encoding="utf-8",
    )

    assert ladder_trajectory.trajectory(path)["current_elo"] == 1100
    assert ladder_trajectory.proof(path)["rated_games"] == 1
    assert ladder_trajectory.proof(path)["fallback_rating_games"] == 1


def test_rated_points_marks_authoritative_and_fallback_sources_separately():
    points = ladder_trajectory.rated_points(
        [
            {"battle_id": "battle-gen9ou-1", "elo_after": 1200, "rating": 1100},
            {"battle_id": "battle-gen9ou-2", "rating": 1210},
        ]
    )

    assert points == [
        {
            "index": 0,
            "battle_id": "battle-gen9ou-1",
            "timestamp": "",
            "result": "",
            "elo": 1200.0,
            "elo_source": "authoritative_elo",
            "elo_delta": None,
        },
        {
            "index": 1,
            "battle_id": "battle-gen9ou-2",
            "timestamp": "",
            "result": "",
            "elo": 1210.0,
            "elo_source": "fallback_rating",
            "elo_delta": None,
        },
    ]
