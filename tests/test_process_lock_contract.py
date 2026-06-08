import os
import sys
import builtins
import json
import tempfile
import time
from contextlib import contextmanager
from pathlib import Path
from types import SimpleNamespace


if "psutil" not in sys.modules:
    sys.modules["psutil"] = SimpleNamespace(
        AccessDenied=Exception,
        NoSuchProcess=Exception,
        Process=lambda pid: None,
        process_iter=lambda attrs: (),
    )

import process_lock


@contextmanager
def temporary_pid_file():
    with tempfile.TemporaryDirectory(
        prefix="process-lock-test-",
        dir=Path(__file__).resolve().parents[1],
    ) as directory:
        yield Path(directory) / ".bot.pid"


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


def test_stale_reaper_targets_only_same_repo_showdown_bots():
    repo = os.path.abspath(process_lock.LOCK_DIR)
    proc = FakeProcess(
        99,
        ["python.exe", "run.py", "--bot-mode", "accept_challenge"],
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


def test_lock_pid_file_rejects_run_py_from_other_repo(monkeypatch):
    class ProcessFromOtherRepo:
        def cmdline(self):
            return ["python.exe", "run.py", "--bot-mode", "search_ladder"]

        def cwd(self):
            return "D:\\Other\\fouler-play"

    monkeypatch.setattr(process_lock.psutil, "Process", lambda pid: ProcessFromOtherRepo())

    assert process_lock.is_bot_process(1234) is False


def test_lock_pid_file_accepts_same_repo_run_py(monkeypatch):
    repo = os.path.abspath(process_lock.LOCK_DIR)

    class ProcessFromThisRepo:
        def cmdline(self):
            return ["python.exe", "run.py", "--bot-mode", "search_ladder"]

        def cwd(self):
            return repo

    monkeypatch.setattr(process_lock.psutil, "Process", lambda pid: ProcessFromThisRepo())

    assert process_lock.is_bot_process(1234) is True


def test_lock_pid_file_accepts_same_repo_devstream_supervisor(monkeypatch):
    repo = os.path.abspath(process_lock.LOCK_DIR)

    class SupervisorFromThisRepo:
        def cmdline(self):
            return ["python.exe", "scripts/devstream_session.py", "supervise"]

        def cwd(self):
            return repo

    monkeypatch.setattr(process_lock.psutil, "Process", lambda pid: SupervisorFromThisRepo())

    assert process_lock.is_bot_process(1234) is True


def test_lock_pid_file_treats_access_denied_as_held(monkeypatch):
    class AccessDenied(Exception):
        pass

    def raise_access_denied(pid):
        raise AccessDenied(pid)

    monkeypatch.setattr(process_lock.psutil, "AccessDenied", AccessDenied)
    monkeypatch.setattr(process_lock.psutil, "Process", raise_access_denied)

    assert process_lock.is_bot_process(1234) is True


def test_acquire_lock_claims_pid_file_atomically(monkeypatch):
    open_flags = []
    real_open = os.open

    def capture_open(path, flags, mode=0o777, *args, **kwargs):
        open_flags.append(flags)
        return real_open(path, flags, mode, *args, **kwargs)

    with temporary_pid_file() as pid_file:
        monkeypatch.setattr(process_lock, "PID_FILE", str(pid_file))
        monkeypatch.setattr(process_lock.os, "open", capture_open)
        monkeypatch.setattr(process_lock.os, "getpid", lambda: 4321)
        monkeypatch.setattr(
            process_lock.psutil,
            "Process",
            lambda pid: SimpleNamespace(create_time=lambda: 123.5),
        )
        monkeypatch.setattr(process_lock, "kill_stale_processes", lambda: 0)
        monkeypatch.setattr(process_lock.atexit, "register", lambda func: None)
        monkeypatch.setattr(process_lock.signal, "signal", lambda *args, **kwargs: None)

        assert process_lock.acquire_lock(username="test-user") is True
        payload = json.loads(pid_file.read_text())
        assert payload["pid"] == 4321
        assert payload["createTime"] == 123.5
        assert open_flags
        assert open_flags[0] & os.O_CREAT
        assert open_flags[0] & os.O_EXCL


def test_acquire_lock_blocks_live_runner_without_runtime_lease(monkeypatch):
    monkeypatch.setattr(
        process_lock.sys,
        "argv",
        [
            "python.exe",
            "run.py",
            "--bot-mode",
            "search_ladder",
            "--ps-username",
            "bot",
            "--run-count",
            "1",
            "--max-concurrent-battles",
            "1",
        ],
    )
    monkeypatch.setattr(
        process_lock,
        "validate_runtime_lease",
        lambda **kwargs: {"ok": False, "blockers": ["runtime lease file is missing"]},
    )
    monkeypatch.setattr(
        process_lock,
        "_claim_pid_file_atomically",
        lambda: (_ for _ in ()).throw(AssertionError("pid file must not be claimed")),
    )

    assert process_lock.acquire_lock(username="bot") is False


def test_acquire_lock_retries_after_clearly_stale_pid_file(monkeypatch):
    with temporary_pid_file() as pid_file:
        pid_file.write_text("987654321")

        def remove_stale_pid_file():
            pid_file.unlink()
            return True

        monkeypatch.setattr(process_lock, "PID_FILE", str(pid_file))
        monkeypatch.setattr(process_lock.os, "getpid", lambda: 4321)
        monkeypatch.setattr(process_lock, "_remove_stale_pid_file", remove_stale_pid_file)
        monkeypatch.setattr(process_lock, "is_bot_process", lambda pid: False)
        monkeypatch.setattr(process_lock, "kill_stale_processes", lambda: 0)
        monkeypatch.setattr(process_lock.atexit, "register", lambda func: None)
        monkeypatch.setattr(process_lock.signal, "signal", lambda *args, **kwargs: None)

        assert process_lock.acquire_lock(username="test-user") is True
        assert json.loads(pid_file.read_text())["pid"] == 4321


def test_acquire_lock_treats_unreadable_pid_file_as_held(monkeypatch):
    real_open = builtins.open

    with temporary_pid_file() as pid_file:
        pid_file.write_text("9999")

        def raise_permission_error(path, *args, **kwargs):
            if os.fspath(path) == str(pid_file):
                raise PermissionError("denied")
            return real_open(path, *args, **kwargs)

        monkeypatch.setattr(process_lock, "PID_FILE", str(pid_file))
        monkeypatch.setattr(builtins, "open", raise_permission_error)
        monkeypatch.setattr(process_lock, "kill_stale_processes", lambda: 0)

        assert process_lock.acquire_lock(username="test-user") is False
        assert pid_file.read_text() == "9999"


def test_acquire_lock_rejects_live_pid_with_matching_create_time(monkeypatch):
    repo = os.path.abspath(process_lock.LOCK_DIR)
    create_time = time.time() - 10

    class CurrentRunner:
        def cmdline(self):
            return ["python.exe", "run.py", "--bot-mode", "search_ladder"]

        def cwd(self):
            return repo

        def create_time(self):
            return create_time

    with temporary_pid_file() as pid_file:
        pid_file.write_text(json.dumps({"pid": 1234, "createTime": create_time}))

        monkeypatch.setattr(process_lock, "PID_FILE", str(pid_file))
        monkeypatch.setattr(process_lock.psutil, "Process", lambda pid: CurrentRunner())
        monkeypatch.setattr(process_lock, "kill_stale_processes", lambda: 0)

        assert process_lock.acquire_lock(username="test-user") is False
        assert json.loads(pid_file.read_text())["pid"] == 1234


def test_acquire_lock_reclaims_reused_pid_with_mismatched_create_time(monkeypatch):
    repo = os.path.abspath(process_lock.LOCK_DIR)
    old_create_time = time.time() - 100
    reused_create_time = time.time() - 5

    class ReusedPidRunner:
        def cmdline(self):
            return ["python.exe", "run.py", "--bot-mode", "search_ladder"]

        def cwd(self):
            return repo

        def create_time(self):
            return reused_create_time

    with temporary_pid_file() as pid_file:
        pid_file.write_text(json.dumps({"pid": 1234, "createTime": old_create_time}))

        monkeypatch.setattr(process_lock, "PID_FILE", str(pid_file))
        monkeypatch.setattr(process_lock.os, "getpid", lambda: 4321)
        monkeypatch.setattr(
            process_lock.psutil,
            "Process",
            lambda pid: SimpleNamespace(create_time=lambda: 123.5) if pid == 4321 else ReusedPidRunner(),
        )
        monkeypatch.setattr(process_lock, "kill_stale_processes", lambda: 0)
        monkeypatch.setattr(process_lock.atexit, "register", lambda func: None)
        monkeypatch.setattr(process_lock.signal, "signal", lambda *args, **kwargs: None)

        assert process_lock.acquire_lock(username="test-user") is True
        payload = json.loads(pid_file.read_text())
        assert payload["pid"] == 4321
        assert payload["createTime"] == 123.5
