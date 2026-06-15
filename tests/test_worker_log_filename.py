import os

from fp.run_battle import _safe_log_filename_part, _worker_battle_log_path


def test_safe_log_filename_part_removes_windows_invalid_chars():
    safe = _safe_log_filename_part('bad/name:with*chars?<>|"\\')

    assert safe == "bad_name_with_chars"
    assert all(char not in safe for char in '<>:"/\\|?*')


def test_worker_battle_log_path_sanitizes_live_jiggly_opponent_name():
    path = _worker_battle_log_path(
        1,
        "battle-gen9ou-2632283833",
        "12bucklemy...?",
    )

    assert os.path.dirname(path) == "logs"
    assert os.path.basename(path) == "battle-gen9ou-2632283833_12bucklemy.log"


def test_worker_battle_log_path_uses_fallbacks_for_blank_parts():
    path = _worker_battle_log_path(2, "", "...???")

    assert os.path.basename(path) == "worker_2_unknown.log"
