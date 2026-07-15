import os
import sys
import builtins
import json
import tempfile
import time
from contextlib import contextmanager
from pathlib import Path
from types import SimpleNamespace

import pytest


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


def test_effective_default_mode_live_uri_fails_closed_without_cli_mode(monkeypatch):
    monkeypatch.setattr(process_lock.sys, "argv", ["python.exe", "run.py"])
    calls = []
    monkeypatch.setattr(
        process_lock,
        "validate_runtime_lease",
        lambda **kwargs: calls.append(kwargs)
        or {"ok": False, "blockers": ["runtime lease file is missing"]},
    )

    guard = process_lock._current_runtime_lease_guard(
        bot_mode="search_ladder",
        websocket_uri="wss://sim3.psim.us/showdown/websocket",
        username="bot",
        run_count=1,
        max_concurrent_battles=1,
    )

    assert guard["ok"] is False
    assert calls[0]["requested_run_count"] == 1
    assert calls[0]["requested_max_concurrent_battles"] == 1
    assert calls[0]["requested_replay_behavior"] is None
    assert calls[0]["require_deployment_receipt"] is True
    assert calls[0]["verify_deployment_checkout"] is True


def test_offline_env_cannot_bypass_live_websocket_authority(monkeypatch):
    monkeypatch.setenv("FOULER_OFFLINE_EVAL", "1")
    monkeypatch.setenv("FOULER_PROCESS_LOCK_FILE", "deceptive-offline.pid")
    monkeypatch.setattr(process_lock, "_EFFECTIVE_BOT_MODE", None)
    monkeypatch.setattr(process_lock, "_EFFECTIVE_WEBSOCKET_URI", None)
    monkeypatch.setattr(process_lock.sys, "argv", ["python.exe", "run.py"])
    calls = []
    monkeypatch.setattr(
        process_lock,
        "validate_runtime_lease",
        lambda **kwargs: calls.append(kwargs)
        or {"ok": False, "blockers": ["live authority required"]},
    )

    guard = process_lock._current_runtime_lease_guard(
        bot_mode="accept_challenge",
        websocket_uri="wss://sim3.psim.us/showdown/websocket",
        username="bot",
        run_count=1,
        max_concurrent_battles=1,
    )

    assert guard["ok"] is False
    assert len(calls) == 1
    assert process_lock._pid_file_path() == process_lock.PID_FILE


def test_runtime_lease_validator_exception_is_fatal(monkeypatch):
    monkeypatch.setattr(process_lock.sys, "argv", ["python.exe", "run.py"])
    monkeypatch.setattr(
        process_lock,
        "validate_runtime_lease",
        lambda **_kwargs: (_ for _ in ()).throw(RuntimeError("guard failed")),
    )

    with pytest.raises(RuntimeError, match="guard failed"):
        process_lock._current_runtime_lease_guard(
            bot_mode="search_ladder",
            websocket_uri="wss://sim3.psim.us/showdown/websocket",
            username="bot",
            run_count=1,
            max_concurrent_battles=1,
        )


def test_live_runner_requires_exact_inherited_lease_identity(monkeypatch, tmp_path):
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
            "3",
            "--search-parallelism",
            "2",
        ],
    )
    lease_path = tmp_path / "lease.json"
    monkeypatch.setenv("FOULER_RUNTIME_LEASE_PATH", str(lease_path))
    expected = {
        "FOULER_SOURCE_COMMIT": "a" * 40,
        "FOULER_CHANGE_ID": "change-test-0001",
        "FOULER_DEPLOYMENT_ID": "deployment-test-0001",
        "FOULER_SESSION_ID": "session-test-0001",
        "FOULER_RUNTIME_LEASE_ID": "lease-test",
        "FOULER_RUNTIME_LEASE_PATH": str(lease_path),
    }
    calls = []
    monkeypatch.setattr(
        process_lock,
        "validate_runtime_lease",
        lambda **kwargs: calls.append(kwargs) or {"ok": True, "blockers": []},
    )
    monkeypatch.setattr(process_lock, "lease_environment", lambda _guard: expected)
    reservation_calls = []
    monkeypatch.setattr(
        process_lock,
        "_current_runtime_reservation_guard",
        lambda guard, **kwargs: reservation_calls.append((guard, kwargs))
        or {"ok": True, "valid": True},
    )
    monkeypatch.setattr(
        process_lock.subprocess,
        "run",
        lambda *args, **kwargs: SimpleNamespace(returncode=0, stdout="a" * 40 + "\n"),
    )
    for name, value in expected.items():
        monkeypatch.setenv(name, value)
    monkeypatch.setenv("FOULER_DEPLOYMENT_ID", "wrong-deployment")

    blocked = process_lock._current_runtime_lease_guard()

    assert blocked["ok"] is False
    assert any("FOULER_DEPLOYMENT_ID" in blocker for blocker in blocked["blockers"])
    assert calls[0]["lease_path"] == str(lease_path)

    monkeypatch.setenv("FOULER_DEPLOYMENT_ID", expected["FOULER_DEPLOYMENT_ID"])
    assert process_lock._current_runtime_lease_guard()["ok"] is True
    assert reservation_calls[-1][1]["run_count"] == 1
    assert reservation_calls[-1][1]["max_concurrent_battles"] == 3


def test_live_runner_requires_matching_supervisor_reservation(monkeypatch):
    monkeypatch.setattr(process_lock.sys, "argv", ["python.exe", "run.py"])
    expected = {
        "FOULER_SOURCE_COMMIT": "a" * 40,
        "FOULER_CHANGE_ID": "change-test-0001",
        "FOULER_DEPLOYMENT_ID": "deployment-test-0001",
        "FOULER_SESSION_ID": "session-test-0001",
        "FOULER_RUNTIME_LEASE_ID": "lease-test",
        "FOULER_RUNTIME_LEASE_PATH": "C:\\runtime-lease.json",
    }
    monkeypatch.setattr(
        process_lock,
        "validate_runtime_lease",
        lambda **_kwargs: {"ok": True, "blockers": []},
    )
    monkeypatch.setattr(process_lock, "lease_environment", lambda _guard: expected)
    monkeypatch.setattr(
        process_lock,
        "_current_runtime_reservation_guard",
        lambda *_args, **_kwargs: {
            "ok": False,
            "valid": False,
            "blockers": ["no matching in-flight reservation"],
        },
    )
    monkeypatch.setattr(
        process_lock.subprocess,
        "run",
        lambda *args, **kwargs: SimpleNamespace(
            returncode=0,
            stdout="a" * 40 + "\n",
        ),
    )
    for name, value in expected.items():
        monkeypatch.setenv(name, value)

    guard = process_lock._current_runtime_lease_guard(
        bot_mode="search_ladder",
        websocket_uri="wss://sim3.psim.us/showdown/websocket",
        username="bot",
        run_count=30,
        max_concurrent_battles=3,
        search_parallelism=2,
        replay_behavior="always",
    )

    assert guard["ok"] is False
    assert "runtime reservation: no matching in-flight reservation" in guard["blockers"]


@pytest.mark.parametrize(
    ("max_concurrent_battles", "search_parallelism", "expected_blocker"),
    [
        (2, 2, "max-concurrent-battles must equal"),
        (3, 4, "search-parallelism must equal"),
    ],
)
def test_live_runner_requires_owner_locked_pilot_shape(
    monkeypatch,
    max_concurrent_battles,
    search_parallelism,
    expected_blocker,
):
    monkeypatch.setattr(process_lock.sys, "argv", ["python.exe", "run.py"])
    monkeypatch.setattr(
        process_lock,
        "validate_runtime_lease",
        lambda **_kwargs: {"ok": True, "blockers": []},
    )

    guard = process_lock._current_runtime_lease_guard(
        bot_mode="search_ladder",
        websocket_uri="wss://sim3.psim.us/showdown/websocket",
        username="bot",
        run_count=30,
        max_concurrent_battles=max_concurrent_battles,
        search_parallelism=search_parallelism,
        replay_behavior="always",
    )

    assert guard["ok"] is False
    assert any(expected_blocker in blocker for blocker in guard["blockers"])


def _install_live_broker_lock_fixture(monkeypatch, events):
    monkeypatch.setattr(process_lock, "_BROKER_RESERVATION", None)
    monkeypatch.setattr(process_lock, "_BROKER_COMPLETED", False)
    monkeypatch.setattr(process_lock, "_BROKER_OUTCOME", "failed")
    binding = {
        "reservationId": "res-" + "a" * 32,
        "kind": "runtime",
        "purpose": process_lock.RUNTIME_RESERVATION_PURPOSE,
        "battleCount": 30,
        "cycleCount": 1,
        "maxConcurrentBattles": 3,
        "supervisorProcessId": 1234,
        "supervisorProcessCreationFiletime": 133_801_234_567_890_000,
        "supervisorInstanceId": "supervisor-test",
        "launchNonce": "9" * 64,
    }
    monkeypatch.setattr(
        process_lock,
        "_current_runtime_lease_guard",
        lambda **_kwargs: {
            "ok": True,
            "lease": {
                "id": "lease-test-0001",
                "authorizationSha256": "f" * 64,
            },
            "leaseConsumptionReservation": {
                "ok": True,
                "valid": True,
                "reservation": dict(binding),
            },
        },
    )

    def broker_request(payload):
        events.append(("broker", payload["action"], payload.get("outcome")))
        return {
            "ok": True,
            "requestId": payload["requestId"],
            "action": payload["action"],
            "result": {
                **binding,
                "state": "completed" if payload["action"] == "complete" else "claimed",
            },
        }

    monkeypatch.setattr(process_lock, "request_with_retry", broker_request)
    monkeypatch.setattr(process_lock.atexit, "register", lambda _func: None)
    monkeypatch.setattr(process_lock.signal, "signal", lambda *_args, **_kwargs: None)
    monkeypatch.setenv(process_lock.RUNTIME_RESERVATION_ID_ENV, binding["reservationId"])
    monkeypatch.setenv(process_lock.RUNTIME_SUPERVISOR_INSTANCE_ID_ENV, "supervisor-test")


def test_live_lock_claims_broker_before_pid_and_stale_cleanup(monkeypatch):
    events = []
    _install_live_broker_lock_fixture(monkeypatch, events)
    monkeypatch.setattr(
        process_lock,
        "_claim_pid_file_atomically",
        lambda: events.append(("pid", "claim", None)),
    )
    monkeypatch.setattr(
        process_lock,
        "kill_stale_processes",
        lambda: events.append(("process", "cleanup", None)) or 0,
    )

    assert process_lock.acquire_lock(username="bot", run_count=30) is True
    assert events[:3] == [
        ("broker", "claim", None),
        ("pid", "claim", None),
        ("process", "cleanup", None),
    ]


def test_claim_response_binding_mismatch_is_terminally_aborted(monkeypatch):
    events = []
    _install_live_broker_lock_fixture(monkeypatch, events)
    monkeypatch.setattr(
        process_lock,
        "_claim_pid_file_atomically",
        lambda: (_ for _ in ()).throw(AssertionError("PID claim must not run")),
    )
    original_request = process_lock.request_with_retry

    def mismatched_claim(payload):
        response = original_request(payload)
        if payload["action"] == "claim":
            response["result"] = {**response["result"], "battleCount": 31}
        return response

    monkeypatch.setattr(process_lock, "request_with_retry", mismatched_claim)

    assert process_lock.acquire_lock(username="bot", run_count=30) is False
    assert events == [
        ("broker", "claim", None),
        ("broker", "complete", "aborted"),
    ]
    assert process_lock._BROKER_RESERVATION is None


def test_post_claim_cleanup_failure_completes_broker_as_aborted(monkeypatch):
    events = []
    _install_live_broker_lock_fixture(monkeypatch, events)
    monkeypatch.setattr(process_lock, "_claim_pid_file_atomically", lambda: None)
    monkeypatch.setattr(
        process_lock,
        "kill_stale_processes",
        lambda: (_ for _ in ()).throw(RuntimeError("inspection failed")),
    )
    monkeypatch.setattr(process_lock, "_pid_file_path", lambda: "missing-test-pid")

    assert process_lock.acquire_lock(username="bot", run_count=30) is False
    assert events == [
        ("broker", "claim", None),
        ("broker", "complete", "aborted"),
    ]
    assert process_lock._BROKER_RESERVATION is None




def test_acquire_lock_allows_offline_eval_with_isolated_lock(monkeypatch):
    with temporary_pid_file() as pid_file:
        monkeypatch.setenv("FOULER_OFFLINE_EVAL", "1")
        monkeypatch.setenv("FOULER_NO_SECURITY_LOGIN", "1")
        monkeypatch.delenv("PS_PASSWORD", raising=False)
        monkeypatch.delenv("SHOWDOWN_PASSWORD", raising=False)
        monkeypatch.delenv("FOULER_SHOWDOWN_PASSWORD", raising=False)
        monkeypatch.setenv("FOULER_PROCESS_LOCK_FILE", str(pid_file))
        monkeypatch.setattr(
            process_lock.sys,
            "argv",
            [
                "python.exe",
                "run.py",
                "--ps-username",
                "foulerEvalBot",
                "--run-count",
                "1",
                "--max-concurrent-battles",
                "1",
            ],
        )
        monkeypatch.setattr(
            process_lock,
            "validate_runtime_lease",
            lambda **kwargs: (_ for _ in ()).throw(AssertionError("live lease guard must be skipped")),
        )
        monkeypatch.setattr(process_lock.os, "getpid", lambda: 4321)
        monkeypatch.setattr(
            process_lock.psutil,
            "Process",
            lambda pid: SimpleNamespace(create_time=lambda: 123.5),
        )
        monkeypatch.setattr(process_lock.atexit, "register", lambda func: None)
        monkeypatch.setattr(process_lock.signal, "signal", lambda *args, **kwargs: None)

        assert process_lock.acquire_lock(
            username="foulerEvalBot",
            bot_mode="accept_challenge",
            websocket_uri="ws://127.0.0.1:8000/showdown/websocket",
            run_count=1,
            max_concurrent_battles=1,
            offline_eval_authority=process_lock._OFFLINE_EVAL_AUTHORITY,
        ) is True
        payload = json.loads(pid_file.read_text(encoding="utf-8"))
        assert payload["pid"] == 4321
        assert payload["lockFile"] == os.path.abspath(pid_file)


def test_offline_env_and_loopback_do_not_bypass_without_runner_authority(monkeypatch):
    monkeypatch.setenv("FOULER_OFFLINE_EVAL", "1")
    monkeypatch.setenv("FOULER_NO_SECURITY_LOGIN", "1")
    calls = []
    monkeypatch.setattr(
        process_lock,
        "validate_runtime_lease",
        lambda **kwargs: calls.append(kwargs)
        or {"ok": False, "blockers": ["live authority required"]},
    )

    guard = process_lock._current_runtime_lease_guard(
        bot_mode="accept_challenge",
        websocket_uri="ws://127.0.0.1:8000/showdown/websocket",
        username="foulerEvalBot",
        run_count=1,
        max_concurrent_battles=1,
    )

    assert guard["ok"] is False
    assert len(calls) == 1


def test_offline_runner_authority_rejects_inherited_live_password(monkeypatch):
    monkeypatch.setenv("FOULER_OFFLINE_EVAL", "1")
    monkeypatch.setenv("FOULER_NO_SECURITY_LOGIN", "1")
    monkeypatch.setenv("PS_PASSWORD", "must-not-cross-offline-boundary")
    calls = []
    monkeypatch.setattr(
        process_lock,
        "validate_runtime_lease",
        lambda **kwargs: calls.append(kwargs)
        or {"ok": False, "blockers": ["live authority required"]},
    )

    guard = process_lock._current_runtime_lease_guard(
        bot_mode="accept_challenge",
        websocket_uri="ws://127.0.0.1:8000/showdown/websocket",
        username="foulerEvalBot",
        run_count=1,
        max_concurrent_battles=1,
        offline_eval_authority=process_lock._OFFLINE_EVAL_AUTHORITY,
    )

    assert guard["ok"] is False
    assert len(calls) == 1


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
