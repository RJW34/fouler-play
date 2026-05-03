import os
import sys
from types import SimpleNamespace


if "psutil" not in sys.modules:
    sys.modules["psutil"] = SimpleNamespace(
        AccessDenied=Exception,
        NoSuchProcess=Exception,
        Process=lambda pid: None,
        process_iter=lambda attrs: (),
    )

import process_lock


class FakeProcess:
    def __init__(self, pid, cmdline, cwd):
        self.pid = pid
        self.info = {
            "cmdline": cmdline,
            "cwd": cwd,
        }


def test_stale_reaper_protects_current_windows_venv_launch_chain():
    repo = os.path.abspath(process_lock.LOCK_DIR)
    proc = FakeProcess(
        42,
        ["D:\\Projects\\fouler-play\\.venv\\Scripts\\python.exe", "run.py", "--bot-mode", "search_ladder"],
        repo,
    )

    assert not process_lock._is_stale_bot_process(proc, repo, {42, 43})


def test_stale_reaper_targets_only_same_repo_ladder_bots():
    repo = os.path.abspath(process_lock.LOCK_DIR)
    proc = FakeProcess(
        99,
        ["python.exe", "run.py", "--bot-mode", "search_ladder"],
        repo,
    )

    assert process_lock._is_stale_bot_process(proc, repo, {42, 43})


def test_stale_reaper_ignores_other_fouler_repos():
    repo = os.path.abspath(process_lock.LOCK_DIR)
    proc = FakeProcess(
        99,
        ["python.exe", "run.py", "--bot-mode", "search_ladder"],
        "D:\\Other\\fouler-play",
    )

    assert not process_lock._is_stale_bot_process(proc, repo, {42, 43})
