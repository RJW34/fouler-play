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
        [
            "D:\\Projects\\fouler-play\\.venv\\Scripts\\python.exe",
            "run.py",
            "--websocket-uri",
            "wss://sim3.psim.us/showdown/websocket",
            "--bot-mode",
            "search_ladder",
        ],
        repo,
    )

    assert not process_lock._is_stale_bot_process(proc, repo, {42, 43})


def test_stale_reaper_targets_only_same_repo_ladder_bots():
    repo = os.path.abspath(process_lock.LOCK_DIR)
    proc = FakeProcess(
        99,
        [
            "python.exe",
            "run.py",
            "--websocket-uri",
            "wss://sim3.psim.us/showdown/websocket",
            "--bot-mode",
            "search_ladder",
        ],
        repo,
    )

    assert process_lock._is_stale_bot_process(proc, repo, {42, 43})


def test_stale_reaper_ignores_other_fouler_repos():
    repo = os.path.abspath(process_lock.LOCK_DIR)
    proc = FakeProcess(
        99,
        [
            "python.exe",
            "run.py",
            "--websocket-uri",
            "wss://sim3.psim.us/showdown/websocket",
            "--bot-mode",
            "search_ladder",
        ],
        "D:\\Other\\fouler-play",
    )

    assert not process_lock._is_stale_bot_process(proc, repo, {42, 43})


def test_stale_reaper_ignores_local_eval_search_ladder_runtime():
    repo = os.path.abspath(process_lock.LOCK_DIR)
    proc = FakeProcess(
        99,
        [
            "python.exe",
            "run.py",
            "--websocket-uri",
            "ws://127.0.0.1:8000/showdown/websocket",
            "--bot-mode",
            "search_ladder",
        ],
        repo,
    )

    assert not process_lock._is_stale_bot_process(proc, repo, {42, 43})


def test_lock_pid_file_rejects_run_py_from_other_repo(monkeypatch):
    class ProcessFromOtherRepo:
        def cmdline(self):
            return [
                "python.exe",
                "run.py",
                "--websocket-uri",
                "wss://sim3.psim.us/showdown/websocket",
                "--bot-mode",
                "search_ladder",
            ]

        def cwd(self):
            return "D:\\Other\\fouler-play"

    monkeypatch.setattr(process_lock.psutil, "Process", lambda pid: ProcessFromOtherRepo())

    assert process_lock.is_bot_process(1234) is False


def test_lock_pid_file_accepts_same_repo_run_py(monkeypatch):
    repo = os.path.abspath(process_lock.LOCK_DIR)

    class ProcessFromThisRepo:
        def cmdline(self):
            return [
                "python.exe",
                "run.py",
                "--websocket-uri",
                "wss://sim3.psim.us/showdown/websocket",
                "--bot-mode",
                "search_ladder",
            ]

        def cwd(self):
            return repo

    monkeypatch.setattr(process_lock.psutil, "Process", lambda pid: ProcessFromThisRepo())

    assert process_lock.is_bot_process(1234) is True


def test_acquire_lock_refuses_orphaned_same_repo_ladder_process_without_killing(monkeypatch, tmp_path):
    repo = os.path.abspath(process_lock.LOCK_DIR)
    killed = []

    class LiveProcess(FakeProcess):
        def kill(self):
            killed.append(self.pid)

    live = LiveProcess(
        4242,
        [
            "python.exe",
            "run.py",
            "--websocket-uri",
            "wss://sim3.psim.us/showdown/websocket",
            "--bot-mode",
            "search_ladder",
        ],
        repo,
    )
    pid_file = tmp_path / ".bot.pid"

    monkeypatch.setattr(process_lock, "PID_FILE", str(pid_file))
    monkeypatch.setattr(process_lock.psutil, "process_iter", lambda attrs: [live])
    monkeypatch.setattr(process_lock, "_protected_process_ids", lambda: {os.getpid()})

    assert process_lock.acquire_lock("npctypebeat") is False
    assert not pid_file.exists()
    assert killed == []

def test_lock_pid_file_rejects_same_repo_local_eval_runtime(monkeypatch):
    repo = os.path.abspath(process_lock.LOCK_DIR)

    class LocalEvalProcess:
        def cmdline(self):
            return [
                "python.exe",
                "run.py",
                "--websocket-uri",
                "ws://127.0.0.1:8000/showdown/websocket",
                "--bot-mode",
                "search_ladder",
            ]

        def cwd(self):
            return repo

    monkeypatch.setattr(process_lock.psutil, "Process", lambda pid: LocalEvalProcess())

    assert process_lock.is_bot_process(1234) is False
