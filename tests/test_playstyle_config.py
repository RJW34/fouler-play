from fp.playstyle_config import Playstyle, PlaystyleConfig


def test_current_benchmark_balance_team_uses_fat_policy():
    assert PlaystyleConfig.get_team_playstyle("fat-team-2-balance") is Playstyle.FAT


def test_retired_pivot_name_is_not_a_current_team_override():
    assert PlaystyleConfig.get_team_playstyle("fat-team-2-pivot") is Playstyle.BALANCE
