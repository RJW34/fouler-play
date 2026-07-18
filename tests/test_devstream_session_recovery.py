import json
import argparse
import os
import subprocess
import sys
import time
from pathlib import Path
from types import SimpleNamespace

import pytest


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

import devstream_session  # noqa: E402
from scripts import devstream_runtime_lease as runtime_lease_impl  # noqa: E402
from tests.runtime_authority_testkit import sign_test_runtime_lease  # noqa: E402


@pytest.fixture(autouse=True)
def stable_runtime_source_commit(monkeypatch, tmp_path):
    identity_names = (*devstream_session.RUNTIME_PROVENANCE_ENV_NAMES, devstream_session.RUNTIME_LEASE_PATH_ENV)
    original_identity = {name: os.environ.get(name) for name in identity_names}
    monkeypatch.setattr(devstream_session, "current_source_commit", lambda: "a" * 40)
    monkeypatch.setattr(
        devstream_session,
        "RUNTIME_LEASE_CONSUMPTION_ROOT_OVERRIDE",
        tmp_path.parent / f"{tmp_path.name}-runtime-lease-consumption",
    )
    monkeypatch.setattr(
        runtime_lease_impl,
        "deployment_receipt_blockers",
        lambda *args, **kwargs: ({"schemaVersion": "fouler-deployment-receipt/v1"}, []),
    )
    yield
    for name, value in original_identity.items():
        if value is None:
            os.environ.pop(name, None)
        else:
            os.environ[name] = value


def apply_test_runtime_identity(env, _guard):
    updated = dict(env)
    updated.update(
        {
            "FOULER_SOURCE_COMMIT": "a" * 40,
            "FOULER_CHANGE_ID": "change-test-0001",
            "FOULER_DEPLOYMENT_ID": "deployment-test-0001",
            "FOULER_SESSION_ID": "session-test-0001",
            "FOULER_RUNTIME_LEASE_ID": "lease-test",
            "FOULER_RUNTIME_AUTHORIZATION_SHA256": "f" * 64,
            "FOULER_SOURCE_TREE": "b" * 40,
            "FOULER_RUNTIME_MANIFEST_DIGEST": "c" * 64,
            "FOULER_DEPLOYMENT_RECEIPT_SHA256": "d" * 64,
            "FOULER_DEPLOYMENT_RECEIPT_PATH": "C:\\ProgramData\\HERMES\\state\\fouler\\deployment-test.json",
        }
    )
    return updated, {
        "ok": True,
        "runtimeLeaseId": "lease-test",
        "blockers": [],
    }


def write_runtime_lease(
    path: Path,
    *,
    account: str = "bot",
    max_cycles: int = 2,
    max_run_count: int = 30,
    replay_behavior: str = "always",
    allowed_purposes: list[str] | None = None,
) -> Path:
    host_binding = runtime_lease_impl.physical_host_binding()
    purposes = allowed_purposes or [
        "devstream-start",
        "devstream-start-dry-run",
        "devstream-start-continuous",
        "devstream-supervise",
        devstream_session.STALE_TRUTH_CLEANUP_PURPOSE,
        devstream_session.STALE_TRUTH_CLEANUP_DRY_RUN_PURPOSE,
    ]
    payload = {
        "schemaVersion": "fouler-play-runtime-lease/v3",
        "projectId": "fouler-play",
        "leaseId": "lease-test",
        "sourceCommit": "a" * 40,
        "changeId": "change-test-0001",
        "deploymentId": "deployment-test-0001",
        "sourceTree": "b" * 40,
        "runtimeManifestDigest": "c" * 64,
        "deploymentReceiptPath": "C:\\ProgramData\\HERMES\\state\\fouler\\deployment-test.json",
        "deploymentReceiptSha256": "d" * 64,
        "sessionId": "session-test-0001",
        "status": "active",
        "approved": True,
        **host_binding,
        "machine": host_binding["hostName"],
        "account": account,
        "maxRunCount": max_run_count,
        "maxCycles": max_cycles,
        "maxConcurrentBattles": 3,
        "replayBehavior": replay_behavior,
        "allowedPurposes": purposes,
        "proofWindow": {
            "startsAt": "2026-06-08T00:00:00+00:00",
            "expiresAt": "2099-01-01T00:00:00+00:00",
        },
        "battleScope": {
            **host_binding,
            "machine": host_binding["hostName"],
            "account": account,
            "runCount": max_run_count,
            "maxRunCount": max_run_count,
            "maxConcurrentBattles": 3,
            "replayBehavior": replay_behavior,
        },
        "cycleScope": {"maxCycles": max_cycles},
    }
    payload = sign_test_runtime_lease(payload)
    path.write_text(json.dumps(payload), encoding="utf-8")
    return path


def successful_lifecycle_result(command: list[str]) -> dict:
    result = {"command": command, "returnCode": 0}
    if "scripts/fouler_deployment_state.py" in command:
        result["json"] = {"ok": True, "status": "active"}
    elif "infrastructure/elo_watchdog.py" in command:
        result["json"] = {"ok": True, "status": "passed"}
    return result


def run_authorized_supervisor_cycle(monkeypatch, args, cycle_index, **kwargs):
    lease_path = str((ROOT / "devstream" / "truth" / "runtime-lease.json").resolve())
    runtime_env = {
        "FOULER_SOURCE_COMMIT": "a" * 40,
        "FOULER_CHANGE_ID": "change-test-0001",
        "FOULER_DEPLOYMENT_ID": "deployment-test-0001",
        "FOULER_SESSION_ID": "session-test-0001",
        "FOULER_RUNTIME_LEASE_ID": "lease-test",
        "FOULER_RUNTIME_AUTHORIZATION_SHA256": "f" * 64,
        "FOULER_SOURCE_TREE": "b" * 40,
        "FOULER_RUNTIME_MANIFEST_DIGEST": "c" * 64,
        "FOULER_DEPLOYMENT_RECEIPT_SHA256": "d" * 64,
        "FOULER_DEPLOYMENT_RECEIPT_PATH": r"C:\ProgramData\HERMES\state\fouler\deployment-test.json",
        "FOULER_PHYSICAL_HOSTNAME": "test-host",
        "FOULER_PHYSICAL_HOST_ID_SHA256": "e" * 64,
        devstream_session.RUNTIME_LEASE_PATH_ENV: lease_path,
    }
    guard = {
        "ok": True,
        "path": lease_path,
        "lease": {
            "id": "lease-test",
            "deploymentReceiptPath": runtime_env["FOULER_DEPLOYMENT_RECEIPT_PATH"],
        },
    }
    monkeypatch.setattr(devstream_session, "lease_environment", lambda validation: dict(runtime_env))
    if (
        getattr(devstream_session.improvement_checkout_guard, "__module__", "")
        == devstream_session.__name__
    ):
        monkeypatch.setattr(
            devstream_session,
            "improvement_checkout_guard",
            lambda: {"ready": True, "blockers": []},
        )
    if (
        getattr(devstream_session.runtime_launch_preflight, "__module__", "")
        == devstream_session.__name__
    ):
        monkeypatch.setattr(
            devstream_session,
            "runtime_launch_preflight",
            lambda _args, *, lease_guard: {
                "ok": bool(lease_guard.get("ok")),
                "blockers": [],
                "secretValuesPrinted": False,
            },
        )
    if (
        getattr(devstream_session.reserve_runtime_lease_consumption, "__module__", "")
        == devstream_session.__name__
    ):
        monkeypatch.setattr(
            devstream_session,
            "reserve_runtime_lease_consumption",
            lambda lease_guard, **reserve_kwargs: {
                "ok": True,
                "reserved": True,
                "reservation": {
                    "reservationId": "res-" + "1" * 32,
                    "kind": "runtime",
                    "purpose": devstream_session.RUNTIME_RESERVATION_PURPOSE,
                    "battleCount": reserve_kwargs["run_count"],
                    "cycleCount": 1,
                    "maxConcurrentBattles": reserve_kwargs["max_concurrent_battles"],
                    "supervisorProcessId": os.getpid(),
                    "supervisorProcessCreationFiletime": 133_801_234_567_890_000,
                    "launchNonce": "9" * 64,
                    "runtimeLeaseId": "lease-test",
                    "supervisorInstanceId": reserve_kwargs["supervisor_instance_id"],
                    "runCount": reserve_kwargs["run_count"],
                },
            },
        )
    return devstream_session.run_supervisor_cycle(
        args,
        cycle_index,
        lease_guard=guard,
        supervisor_instance_id="test-supervisor-instance",
        **kwargs,
    )


def install_supervisor_authority_fixture(monkeypatch, tmp_path, *, run_count: int) -> Path:
    lease_path = write_runtime_lease(
        tmp_path / "runtime-lease.json",
        max_cycles=1,
        allowed_purposes=["devstream-supervise"],
    )
    guard = devstream_session.validate_runtime_lease(
        purpose="devstream-supervise",
        lease_path=lease_path,
        requested_run_count=run_count,
        requested_max_cycles=1,
        requested_max_concurrent_battles=3,
        requested_account="bot",
        requested_source_commit="a" * 40,
        require_deployment_receipt=True,
        verify_deployment_checkout=True,
        require_run_count=True,
        require_max_cycles=True,
        require_max_concurrent_battles=True,
        require_replay_behavior=True,
    )
    assert guard["ok"] is True, guard
    monkeypatch.setattr(devstream_session, "runtime_lease_guard", lambda **kwargs: guard)
    monkeypatch.setattr(
        devstream_session,
        "initialize_runtime_lease_consumption",
        lambda *args, **kwargs: {
            "ok": True,
            "exhausted": False,
            "consumed": {"reservedRunCount": 0, "reservedCycles": 0},
        },
    )
    monkeypatch.setattr(
        devstream_session,
        "runtime_lease_consumption_status",
        lambda lease_guard, *, run_count, max_concurrent_battles, max_cycles: {
            "ok": True,
            "blockers": [],
            "status": {
                "lookupType": "lease-runtime",
                "lookupId": "lease-test",
                "found": False,
                "reservationCount": 0,
                "reservedBattleCount": 0,
                "reservedCycleCount": 0,
                "successfulCycleCount": 0,
                "unresolvedReservationCount": 0,
                "remainingRunCount": 30,
                "remainingCycles": 1,
                "observedWorkload": None,
                "latestReservation": None,
            },
        },
    )
    monkeypatch.setattr(
        devstream_session,
        "complete_runtime_lease_consumption",
        lambda lease_guard, *, reservation, outcome, env=None: {
            "ok": bool(
                lease_guard.get("ok")
                and reservation.get("reservationId")
                and reservation.get("supervisorInstanceId")
            ),
            "completed": True,
            "state": "completed",
            "outcome": outcome,
            "reservation": {
                "reservationId": reservation.get("reservationId"),
                "supervisorInstanceId": reservation.get("supervisorInstanceId"),
            },
        },
    )
    monkeypatch.setattr(devstream_session, "any_battle_runner_alive", lambda: False)
    monkeypatch.setattr(devstream_session, "read_active_battles", lambda: 0)
    monkeypatch.setattr(devstream_session, "clear_dead_battle_pid_files", lambda **kwargs: [])
    monkeypatch.setattr(
        devstream_session,
        "runtime_launch_preflight",
        lambda *args, **kwargs: {
            "ok": True,
            "blockers": [],
            "secretValuesPrinted": False,
        },
    )
    monkeypatch.setattr(
        devstream_session,
        "account_season_authority_check",
        lambda *args, **kwargs: {
            "schemaVersion": "fouler-account-season-authority-check/v1",
            "ok": True,
            "blockers": [],
            "account": "bot",
            "seasonId": "test-season",
            "runtimeMirrorAuthoritative": False,
        },
    )
    return lease_path


def test_consumption_root_ignores_environment_redirect(monkeypatch, tmp_path):
    monkeypatch.setattr(
        devstream_session,
        "RUNTIME_LEASE_CONSUMPTION_ROOT_OVERRIDE",
        None,
    )

    resolved = devstream_session.runtime_lease_consumption_root(
        {"FOULER_RUNTIME_LEASE_CONSUMPTION_DIR": str(tmp_path / "attacker-selected")}
    )

    assert resolved != (tmp_path / "attacker-selected").resolve()
    assert "HERMES-LeaseBroker" in str(resolved)


def test_consumption_initialization_is_structural_and_never_creates_mutable_ledger(
    monkeypatch,
    tmp_path,
):
    monkeypatch.setattr(
        devstream_session,
        "RUNTIME_LEASE_CONSUMPTION_ROOT_OVERRIDE",
        tmp_path / "broker-store",
    )
    guard = {
        "ok": True,
        "lease": {
            "id": "lease-test-0001",
            "authorizationSha256": "f" * 64,
        },
    }

    result = devstream_session.initialize_runtime_lease_consumption(guard)

    assert result["ok"] is True
    assert result["authority"] == "windows-named-pipe-lease-broker"
    assert result["runtimeLeaseId"] == "lease-test-0001"
    assert not (tmp_path / "broker-store").exists()



def test_runtime_lease_status_requires_exact_public_workload(monkeypatch):
    guard = {
        "ok": True,
        "lease": {
            "id": "lease-test",
            "authorizationSha256": "f" * 64,
            "maxRunCount": 30,
            "maxCycles": 1,
        },
    }
    latest = {
        "reservationId": "res-" + "4" * 32,
        "kind": "runtime",
        "purpose": devstream_session.RUNTIME_RESERVATION_PURPOSE,
        "battleCount": 30,
        "cycleCount": 1,
        "maxConcurrentBattles": 3,
        "supervisorProcessId": 1234,
        "supervisorProcessCreationFiletime": 133_801_234_567_890_000,
        "supervisorInstanceId": "supervisor-1234-test",
        "state": "completed",
        "reservedAtFiletime": 133_801_234_567_890_001,
        "claimedProcessId": 2345,
        "claimedProcessCreationFiletime": 133_801_234_567_890_002,
        "claimedAtFiletime": 133_801_234_567_890_003,
        "outcome": "completed",
        "completionActor": "claimant",
        "completedAtFiletime": 133_801_234_567_890_004,
        "capacityReturned": False,
    }
    status = {
        "lookupType": "lease-runtime",
        "lookupId": "lease-test",
        "found": True,
        "reservationCount": 1,
        "reservedBattleCount": 30,
        "reservedCycleCount": 1,
        "successfulCycleCount": 1,
        "unresolvedReservationCount": 0,
        "remainingRunCount": 0,
        "remainingCycles": 0,
        "observedWorkload": {
            "minimumBattleCount": 30,
            "maximumBattleCount": 30,
            "minimumCycleCount": 1,
            "maximumCycleCount": 1,
            "minimumMaxConcurrentBattles": 3,
            "maximumMaxConcurrentBattles": 3,
        },
        "latestReservation": latest,
    }
    requests = []
    monkeypatch.setattr(
        devstream_session,
        "request_with_retry",
        lambda request: requests.append(request)
        or {"ok": True, "result": status},
    )

    result = devstream_session.runtime_lease_consumption_status(
        guard, run_count=30, max_concurrent_battles=3, max_cycles=1
    )

    assert result["ok"] is True
    assert requests[0]["action"] == "status"
    assert requests[0]["lookupType"] == "lease-runtime"
    assert "launchNonce" not in json.dumps(result)
    assert "authorizationDigest" not in json.dumps(result)

    mismatched = json.loads(json.dumps(status))
    mismatched["observedWorkload"]["minimumBattleCount"] = 29
    monkeypatch.setattr(
        devstream_session,
        "request_with_retry",
        lambda request: {"ok": True, "result": mismatched},
    )
    rejected = devstream_session.runtime_lease_consumption_status(
        guard, run_count=30, max_concurrent_battles=3, max_cycles=1
    )
    assert rejected["ok"] is False
    assert "workload binding does not match" in "; ".join(rejected["blockers"])

    secret_bearing = json.loads(json.dumps(status))
    secret_bearing["latestReservation"]["authorizationDigest"] = "f" * 64
    monkeypatch.setattr(
        devstream_session,
        "request_with_retry",
        lambda request: {"ok": True, "result": secret_bearing},
    )
    rejected = devstream_session.runtime_lease_consumption_status(
        guard, run_count=30, max_concurrent_battles=3, max_cycles=1
    )
    assert rejected["ok"] is False
    assert "forbidden secret field" in "; ".join(rejected["blockers"])

    ambiguous = json.loads(json.dumps(status))
    ambiguous["unresolvedReservationCount"] = 1
    monkeypatch.setattr(
        devstream_session,
        "request_with_retry",
        lambda request: {"ok": True, "result": ambiguous},
    )
    rejected = devstream_session.runtime_lease_consumption_status(
        guard, run_count=30, max_concurrent_battles=3, max_cycles=1
    )
    assert rejected["ok"] is False
    assert "ambiguous with a terminal" in "; ".join(rejected["blockers"])


def test_runtime_process_identity_comes_only_from_validated_lease(tmp_path, monkeypatch):
    path = write_runtime_lease(tmp_path / "runtime-lease.json")
    guard = devstream_session.validate_runtime_lease(
        purpose="devstream-start",
        lease_path=path,
        requested_run_count=1,
        requested_max_concurrent_battles=1,
        requested_account="bot",
        require_run_count=True,
        require_max_concurrent_battles=True,
        require_replay_behavior=True,
    )

    env, identity = devstream_session.apply_runtime_process_identity({"PS_USERNAME": "bot"}, guard)

    assert identity["ok"] is True
    assert env["FOULER_SOURCE_COMMIT"] == "a" * 40
    assert env["FOULER_DEPLOYMENT_ID"] == "deployment-test-0001"
    assert env["FOULER_RUNTIME_LEASE_ID"] == "lease-test"
    assert env["FOULER_RUNTIME_LEASE_PATH"] == str(path.resolve())

    _env, blocked = devstream_session.apply_runtime_process_identity(
        {"FOULER_DEPLOYMENT_ID": "different-deployment"},
        guard,
    )
    assert blocked["ok"] is False
    assert "FOULER_DEPLOYMENT_ID does not match" in "; ".join(blocked["blockers"])


def test_recover_stale_battle_runtime_replaces_idle_singleton(tmp_path, monkeypatch):
    pid_dir = tmp_path / ".pids"
    bot_pid = tmp_path / ".bot.pid"
    session_pid = pid_dir / "devstream_battle_session.pid"
    calls = []

    monkeypatch.setattr(devstream_session, "PID_DIR", pid_dir)
    monkeypatch.setattr(devstream_session, "DRAIN_FILE", pid_dir / "drain.request")
    monkeypatch.setattr(devstream_session, "read_active_battles", lambda: 0)
    monkeypatch.setattr(devstream_session, "battle_pid_files", lambda: [bot_pid, session_pid])
    monkeypatch.setattr(
        devstream_session,
        "pid_alive",
        lambda path: (True, 1234) if path == bot_pid else (False, 36084),
    )
    monkeypatch.setattr(devstream_session, "process_age_seconds", lambda pid: 181.0)

    def fake_terminate(path, *, force=False):
        calls.append((path, force))
        was_running = path == bot_pid
        return {
            "pidFile": str(path),
            "pid": 1234 if was_running else 36084,
            "wasRunning": was_running,
            "stopped": was_running,
            "treeVerified": was_running,
        }

    monkeypatch.setattr(devstream_session, "terminate_pid_file", fake_terminate)
    monkeypatch.setattr(devstream_session, "read_pid", lambda path: None)

    payload = devstream_session.recover_stale_battle_runtime(execute=True, stale_after_seconds=180)

    assert payload["recovered"] is True
    assert payload["activeBattleCount"] == 0
    assert calls == [(bot_pid, True), (session_pid, True)]
    assert (pid_dir / "drain.request").exists()


def test_doctor_accepts_completed_proof_handoff_without_runtime_ready(monkeypatch):
    health = {
        "healthy": False,
        "readiness": {
            "runtimeReady": False,
            "proofHandoffReady": True,
        },
        "blockers": ["fouler-play battle runner is idle; OBS HTTP alone is not active battle proof"],
    }
    monkeypatch.setattr(devstream_session, "run_json", lambda command: (health, None))
    monkeypatch.setattr(devstream_session, "load_env_files", lambda: {})
    monkeypatch.setattr(
        devstream_session,
        "prepare_runtime_env",
        lambda env: {"PS_USERNAME": "bot", "PS_PASSWORD": "secret"},
    )
    monkeypatch.setattr(devstream_session, "recent_showdown_credential_failure", lambda root: {"found": False})
    monkeypatch.setattr(devstream_session, "secure_env_files", lambda execute=False: [{"ok": True}])
    monkeypatch.setattr(devstream_session, "shell_command_for_session", lambda *args, **kwargs: ["python", "run.py"])
    monkeypatch.setattr(devstream_session, "read_active_battles", lambda: 0)
    monkeypatch.setattr(devstream_session, "supervisor_alive", lambda: (True, 4321))
    monkeypatch.setattr(
        devstream_session,
        "python_module_available",
        lambda python, module: {"name": f"runtime_dependency_{module}", "ok": True},
    )
    monkeypatch.setattr(
        devstream_session,
        "showdown_account_authority_check",
        lambda env: {"name": "showdown_account_authority", "ok": True},
    )
    monkeypatch.setattr(
        devstream_session,
        "runtime_pid_file_check",
        lambda: {"name": "runtime_pid_files", "ok": True},
    )
    monkeypatch.setattr(
        devstream_session,
        "public_runtime_truth_check",
        lambda: {"name": "public_runtime_truth", "ok": True},
    )

    payload = devstream_session.build_doctor()
    health_check = next(check for check in payload["checks"] if check["name"] == "health_probe")

    assert payload["ready"] is True
    assert health_check["ok"] is True
    assert health_check["acceptedMode"] == "proof-handoff"
    assert "readiness gate" in health_check["runtimeRestoration"]


def test_doctor_blocks_stale_public_runtime_truth(monkeypatch):
    health = {
        "healthy": True,
        "readiness": {
            "runtimeReady": True,
            "proofHandoffReady": True,
        },
        "blockers": [],
    }
    stale_truth = {
        "name": "public_runtime_truth",
        "ok": False,
        "activeBattles": {"count": 0, "stale": True},
        "streamStatus": {"status": "Searching"},
        "battleRunnerAlive": False,
        "blockers": ["stream_status.json reports Searching without an expected Fouler battle runner"],
    }
    monkeypatch.setattr(devstream_session, "run_json", lambda command: (health, None))
    monkeypatch.setattr(devstream_session, "load_env_files", lambda: {})
    monkeypatch.setattr(
        devstream_session,
        "prepare_runtime_env",
        lambda env: {"PS_USERNAME": "bot", "PS_PASSWORD": "secret"},
    )
    monkeypatch.setattr(devstream_session, "recent_showdown_credential_failure", lambda root: {"found": False})
    monkeypatch.setattr(devstream_session, "secure_env_files", lambda execute=False: [{"ok": True}])
    monkeypatch.setattr(devstream_session, "shell_command_for_session", lambda *args, **kwargs: ["python", "run.py"])
    monkeypatch.setattr(devstream_session, "read_active_battles", lambda: 0)
    monkeypatch.setattr(devstream_session, "supervisor_alive", lambda: (True, 4321))
    monkeypatch.setattr(
        devstream_session,
        "python_module_available",
        lambda python, module: {"name": f"runtime_dependency_{module}", "ok": True},
    )
    monkeypatch.setattr(
        devstream_session,
        "showdown_account_authority_check",
        lambda env: {"name": "showdown_account_authority", "ok": True},
    )
    monkeypatch.setattr(
        devstream_session,
        "runtime_pid_file_check",
        lambda: {"name": "runtime_pid_files", "ok": True},
    )
    monkeypatch.setattr(devstream_session, "public_runtime_truth_check", lambda: stale_truth)

    payload = devstream_session.build_doctor()
    truth_check = next(check for check in payload["checks"] if check["name"] == "public_runtime_truth")

    assert payload["ready"] is False
    assert truth_check["ok"] is False
    assert "expected Fouler battle runner" in truth_check["blockers"][0]


def test_public_runtime_truth_classifies_archived_active_and_blocked_stream(tmp_path, monkeypatch):
    active = tmp_path / "active_battles.json"
    stream = tmp_path / "stream_status.json"
    active.write_text(
        json.dumps(
            {
                "battles": [],
                "count": 0,
                "clearedBy": "HERMES devstream_session start",
                "clearReason": "stale empty active battle truth had no live battle runner",
            }
        ),
        encoding="utf-8",
    )
    stream.write_text(json.dumps({"status": "Searching", "streaming": False}), encoding="utf-8")
    old = time.time() - 90000
    os.utime(active, (old, old))
    os.utime(stream, (old, old))

    monkeypatch.setattr(devstream_session, "ROOT", tmp_path)
    monkeypatch.setattr(devstream_session, "STREAM_STATUS_FILE", stream)
    monkeypatch.setattr(devstream_session, "any_battle_runner_alive", lambda: False)

    payload = devstream_session.public_runtime_truth_check(stale_after_seconds=180)

    assert payload["ok"] is False
    assert payload["activeBattles"]["disposition"]["state"] == "archived"
    assert payload["streamStatus"]["disposition"]["state"] == "blocked"
    assert "finite proof-window runtime lease" in payload["blockers"][0]
    assert not any("active_battles.json is stale" in blocker for blocker in payload["blockers"])


def test_cmd_cleanup_stale_truth_fails_closed_without_runtime_lease(tmp_path, monkeypatch, capsys):
    stream = tmp_path / "stream_status.json"
    stream.write_text(json.dumps({"status": "Searching", "streaming": False}), encoding="utf-8")
    old = time.time() - 90000
    os.utime(stream, (old, old))
    started = []

    monkeypatch.setattr(devstream_session, "ROOT", tmp_path)
    monkeypatch.setattr(devstream_session, "STREAM_STATUS_FILE", stream)
    monkeypatch.setattr(devstream_session, "STALE_STREAM_STATUS_BACKUP_DIR", tmp_path / "devstream" / "truth" / "stream-backups")
    monkeypatch.setattr(devstream_session, "load_env_files", lambda: {})
    monkeypatch.setattr(devstream_session, "prepare_runtime_env", lambda env: {"PS_USERNAME": "bot"})
    monkeypatch.setattr(devstream_session, "any_battle_runner_alive", lambda: False)
    monkeypatch.setattr(devstream_session, "start_process", lambda *args, **kwargs: started.append(args) or {"pid": 1})

    args = argparse.Namespace(
        runtime_lease=str(tmp_path / "missing-runtime-lease.json"),
        stale_after_seconds=180,
        stream_stale_after_seconds=180,
        execute=True,
    )

    assert devstream_session.cmd_cleanup_stale_truth(args) == 2

    payload = json.loads(capsys.readouterr().out)
    assert payload["status"] == "blocked-runtime-lease"
    assert "runtime lease file is missing" in " ".join(payload["runtimeLease"]["blockers"])
    assert "streamStatusCleanup" not in payload
    assert started == []
    assert json.loads(stream.read_text(encoding="utf-8"))["status"] == "Searching"


def test_cmd_cleanup_stale_truth_archives_stale_stream_status_without_starting(tmp_path, monkeypatch, capsys):
    stream = tmp_path / "stream_status.json"
    stream.write_text(json.dumps({"status": "Searching", "streaming": False, "stream_pid": None}), encoding="utf-8")
    old = time.time() - 90000
    os.utime(stream, (old, old))
    started = []

    monkeypatch.setattr(devstream_session, "ROOT", tmp_path)
    monkeypatch.setattr(devstream_session, "STREAM_STATUS_FILE", stream)
    monkeypatch.setattr(devstream_session, "STALE_STREAM_STATUS_BACKUP_DIR", tmp_path / "devstream" / "truth" / "stream-backups")
    monkeypatch.setattr(devstream_session, "load_env_files", lambda: {})
    monkeypatch.setattr(devstream_session, "prepare_runtime_env", lambda env: {"PS_USERNAME": "bot"})
    monkeypatch.setattr(devstream_session, "any_battle_runner_alive", lambda: False)
    monkeypatch.setattr(devstream_session, "start_process", lambda *args, **kwargs: started.append(args) or {"pid": 1})
    runtime_lease = write_runtime_lease(
        tmp_path / "runtime-lease.json",
        allowed_purposes=[devstream_session.STALE_TRUTH_CLEANUP_PURPOSE],
    )

    args = argparse.Namespace(
        runtime_lease=str(runtime_lease),
        stale_after_seconds=180,
        stream_stale_after_seconds=180,
        execute=True,
    )

    assert devstream_session.cmd_cleanup_stale_truth(args) == 0

    payload = json.loads(capsys.readouterr().out)
    cleanup = payload["streamStatusCleanup"]
    parsed = json.loads(stream.read_text(encoding="utf-8"))
    assert cleanup["archived"] is True
    assert cleanup["blocked"] is True
    assert Path(cleanup["backupPath"]).exists()
    assert parsed["runtime_blocked"] is True
    assert parsed["blocker_code"] == "stale_stream_status_archived"
    assert parsed["previousStatus"] == "Searching"
    assert payload["publicRuntimeTruthAfter"]["streamStatus"]["disposition"]["state"] == "blocked"
    assert started == []


def test_recover_abandoned_battle_results_from_backups_appends_operational_loss(tmp_path, monkeypatch):
    backup_dir = tmp_path / "devstream" / "truth" / "stale-active-battles-backups"
    backup_dir.mkdir(parents=True)
    stats = tmp_path / "battle_stats.json"
    stats.write_text(
        json.dumps(
            {
                "battles": [
                    {
                        "battle_id": "battle-gen9ou-1",
                        "timestamp": "2000-01-01T00:00:00+00:00",
                        "team_file": "fat-team-2-balance",
                        "result": "loss",
                        "replay_id": "battle-gen9ou-1",
                        "rating": None,
                    }
                ]
            }
        ),
        encoding="utf-8",
    )
    backup = backup_dir / "active_battles-20260705T163311Z.json"
    backup.write_text(
        json.dumps(
            {
                "battles": [
                    {
                        "id": "battle-gen9ou-2644396893",
                        "opponent": "drafalgus",
                        "url": "https://play.pokemonshowdown.com/battle-gen9ou-2644396893",
                        "started": "2026-07-05T12:20:47.795584",
                        "status": "active",
                    }
                ],
                "updated": "2026-07-05T12:30:31.652897",
            }
        ),
        encoding="utf-8",
    )
    new = time.time() + 60
    os.utime(backup, (new, new))

    monkeypatch.setattr(devstream_session, "any_battle_runner_alive", lambda: False)

    payload = devstream_session.recover_abandoned_battle_results_from_backups(
        execute=True,
        backup_dir=backup_dir,
        battle_stats_file=stats,
    )

    battles = json.loads(stats.read_text(encoding="utf-8"))["battles"]
    recovered = battles[-1]
    assert payload["recovered"] is True
    assert payload["rowsAdded"] == 1
    assert payload["battleIds"] == ["battle-gen9ou-2644396893"]
    assert recovered["battle_id"] == "battle-gen9ou-2644396893"
    assert recovered["result"] == "loss"
    assert recovered["operational_loss"] is True
    assert recovered["outcome_detail"] == "abandoned-active-battle-without-result"
    assert recovered["opponent"] == "drafalgus"
    assert recovered["battle_url"].endswith("battle-gen9ou-2644396893")

    second = devstream_session.recover_abandoned_battle_results_from_backups(
        execute=True,
        backup_dir=backup_dir,
        battle_stats_file=stats,
    )
    assert second["rowsAdded"] == 0
    assert len(json.loads(stats.read_text(encoding="utf-8"))["battles"]) == 2


def test_recover_completed_battle_results_from_logs_appends_authoritative_row(tmp_path):
    stats = tmp_path / "battle_stats.json"
    stats.write_text(
        json.dumps(
            {
                "battles": [
                    {
                        "battle_id": "battle-gen9ou-1",
                        "timestamp": "2000-01-01T00:00:00+00:00",
                        "team_file": "fat-team-2-balance",
                        "result": "loss",
                        "replay_id": "battle-gen9ou-1",
                        "rating": 1153,
                    }
                ]
            }
        ),
        encoding="utf-8",
    )
    log_dir = tmp_path / "logs"
    log_dir.mkdir()
    log = log_dir / "battle-gen9ou-2644412138_OUBot2BeepBoop.log"
    log.write_text(
        "\n".join(
            [
                "|player|p2|thepeakmons|1|1145",
                "INFO Battle finished: battle-gen9ou-2644412138 Winner: OUBot2BeepBoop",
                "INFO Captured authoritative rating transition for battle-gen9ou-2644412138: 1145 -> 1124 (-21)",
                "INFO Replay saved: https://replay.pokemonshowdown.com/gen9ou-2644412138",
            ]
        ),
        encoding="utf-8",
    )
    os.utime(log, (time.time() + 60, time.time() + 60))

    replay_dir = tmp_path / "replay_analysis"
    replay_dir.mkdir()
    replay = replay_dir / "gen9ou-2644412138.json"
    replay.write_text(
        json.dumps(
            {
                "log": [
                    "|player|p1|OUBot2BeepBoop|",
                    "|player|p2|thepeakmons|",
                    "|poke|p2|Gliscor, M|",
                    "|poke|p2|Gholdengo|",
                    "|poke|p2|Zamazenta|",
                    "|poke|p2|Blissey, F|",
                    "|poke|p2|Skarmory, F|",
                    "|poke|p2|Pecharunt|",
                    "|win|OUBot2BeepBoop",
                ]
            }
        ),
        encoding="utf-8",
    )
    teams_dir = tmp_path / "teams"
    team_path = teams_dir / "gen9" / "ou" / "fat-team-1-stall"
    team_path.parent.mkdir(parents=True)
    team_path.write_text(
        "\n".join(
            [
                "Gliscor @ Toxic Orb",
                "Gholdengo @ Air Balloon",
                "Zamazenta @ Leftovers",
                "Blissey @ Heavy-Duty Boots",
                "Skarmory @ Rocky Helmet",
                "Pecharunt @ Heavy-Duty Boots",
            ]
        ),
        encoding="utf-8",
    )

    payload = devstream_session.recover_completed_battle_results_from_logs(
        execute=True,
        log_dir=log_dir,
        battle_stats_file=stats,
        account="thepeakmons",
        replay_dir=replay_dir,
        teams_dir=teams_dir,
    )

    battles = json.loads(stats.read_text(encoding="utf-8"))["battles"]
    recovered = battles[-1]
    assert payload["recovered"] is True
    assert payload["rowsAdded"] == 1
    assert payload["battleIds"] == ["battle-gen9ou-2644412138"]
    assert recovered["battle_id"] == "battle-gen9ou-2644412138"
    assert recovered["result"] == "loss"
    assert recovered["team_file"] == "gen9/ou/fat-team-1-stall"
    assert recovered["opponent"] == "OUBot2BeepBoop"
    assert recovered["rating"] == 1124
    assert recovered["elo_before"] == 1145
    assert recovered["elo_after"] == 1124
    assert recovered["rating_delta"] == -21
    assert recovered["replay_url"] == "https://replay.pokemonshowdown.com/gen9ou-2644412138"
    assert recovered["account"] == "thepeakmons"

    second = devstream_session.recover_completed_battle_results_from_logs(
        execute=True,
        log_dir=log_dir,
        battle_stats_file=stats,
        account="thepeakmons",
        replay_dir=replay_dir,
        teams_dir=teams_dir,
    )
    assert second["rowsAdded"] == 0
    assert len(json.loads(stats.read_text(encoding="utf-8"))["battles"]) == 2


def test_recover_completed_battle_results_from_logs_enriches_existing_sparse_row(tmp_path):
    stats = tmp_path / "battle_stats.json"
    stats.write_text(
        json.dumps(
            {
                "battles": [
                    {
                        "battle_id": "battle-gen9ou-2644424166",
                        "timestamp": "2026-07-05T17:32:25.828231+00:00",
                        "team_file": "fat-team-2-balance",
                        "result": "loss",
                        "replay_id": "battle-gen9ou-2644424166",
                        "rating": None,
                    }
                ]
            }
        ),
        encoding="utf-8",
    )
    log_dir = tmp_path / "logs"
    log_dir.mkdir()
    log = log_dir / "battle-gen9ou-2644424166_jzjzjzjzjs.log"
    log.write_text(
        "\n".join(
            [
                "|player|p2|thepeakmons|1|1124",
                "INFO Battle finished: battle-gen9ou-2644424166 Winner: jzjzjzjzjs",
                "INFO Captured authoritative rating transition for battle-gen9ou-2644424166: 1124 -> 1097 (-27)",
                "INFO Replay saved: https://replay.pokemonshowdown.com/gen9ou-2644424166",
            ]
        ),
        encoding="utf-8",
    )

    payload = devstream_session.recover_completed_battle_results_from_logs(
        execute=True,
        log_dir=log_dir,
        battle_stats_file=stats,
        account="thepeakmons",
    )

    battles = json.loads(stats.read_text(encoding="utf-8"))["battles"]
    recovered = battles[0]
    assert payload["rowsAdded"] == 0
    assert payload["rowsUpdated"] == 1
    assert payload["updatedBattleIds"] == ["battle-gen9ou-2644424166"]
    assert len(battles) == 1
    assert recovered["rating"] == 1097
    assert recovered["elo_before"] == 1124
    assert recovered["elo_after"] == 1097
    assert recovered["rating_delta"] == -27
    assert recovered["replay_url"] == "https://replay.pokemonshowdown.com/gen9ou-2644424166"
    assert recovered["result_enriched_from_log"] is True


def test_recover_completed_battle_results_rejects_retired_account_log(tmp_path):
    stats = tmp_path / "battle_stats.json"
    stats.write_text('{"battles": []}', encoding="utf-8")
    log_dir = tmp_path / "logs"
    log_dir.mkdir()
    (log_dir / "battle-gen9ou-2644396893_drafalgus.log").write_text(
        "\n".join(
            [
                "|player|p1|thepeakmons|1|1177",
                "|player|p2|drafalgus|1|1204",
                "INFO Battle finished: battle-gen9ou-2644396893 Winner: drafalgus",
            ]
        ),
        encoding="utf-8",
    )

    payload = devstream_session.recover_completed_battle_results_from_logs(
        execute=True,
        log_dir=log_dir,
        battle_stats_file=stats,
        account="DekuFoulerLab",
        season_id="dekufoulerlab-gen9ou-20260710",
    )

    assert payload["rowsAdded"] == 0
    assert payload["rowsUpdated"] == 0
    assert json.loads(stats.read_text(encoding="utf-8"))["battles"] == []


def test_recover_completed_battle_results_repairs_self_attributed_opponent(tmp_path):
    stats = tmp_path / "battle_stats.json"
    stats.write_text(
        json.dumps(
            {
                "battles": [
                    {
                        "battle_id": "battle-gen9ou-2647386310-token",
                        "timestamp": "2026-07-11T02:55:38+00:00",
                        "team_file": "fat-team-2-balance",
                        "result": "win",
                        "winner": "DekuFoulerLab",
                        "opponent": "DekuFoulerLab",
                    }
                ]
            }
        ),
        encoding="utf-8",
    )
    log_dir = tmp_path / "logs"
    log_dir.mkdir()
    (log_dir / "battle-gen9ou-2647386310-token_dbdhkkb.log").write_text(
        "\n".join(
            [
                "INFO Battle finished: battle-gen9ou-2647386310-token Winner: DekuFoulerLab",
                "INFO Replay saved: https://replay.pokemonshowdown.com/gen9ou-2647386310-token",
            ]
        ),
        encoding="utf-8",
    )

    payload = devstream_session.recover_completed_battle_results_from_logs(
        execute=True,
        log_dir=log_dir,
        battle_stats_file=stats,
        account="DekuFoulerLab",
    )

    recovered = json.loads(stats.read_text(encoding="utf-8"))["battles"][0]
    assert payload["rowsUpdated"] == 1
    assert recovered["opponent"] == "dbdhkkb"
    assert recovered["result_enriched_from_log"] is True


def test_cmd_cleanup_stale_truth_recovers_abandoned_active_battle_result_without_starting(
    tmp_path, monkeypatch, capsys
):
    active = tmp_path / "active_battles.json"
    active.write_text(
        json.dumps(
            {
                "battles": [
                    {
                        "id": "battle-gen9ou-2644396893",
                        "opponent": "drafalgus",
                        "url": "https://play.pokemonshowdown.com/battle-gen9ou-2644396893",
                        "started": "2026-07-05T12:20:47.795584",
                        "status": "active",
                    }
                ],
                "count": 1,
                "updated": "2026-07-05T12:30:31.652897",
            }
        ),
        encoding="utf-8",
    )
    old = time.time() - 90000
    os.utime(active, (old, old))
    stats = tmp_path / "battle_stats.json"
    stats.write_text(
        json.dumps(
            {
                "battles": [
                    {
                        "battle_id": "battle-gen9ou-1",
                        "timestamp": "2000-01-01T00:00:00+00:00",
                        "team_file": "fat-team-2-balance",
                        "result": "loss",
                        "replay_id": "battle-gen9ou-1",
                        "rating": None,
                    }
                ]
            }
        ),
        encoding="utf-8",
    )
    started = []

    monkeypatch.setattr(devstream_session, "ROOT", tmp_path)
    monkeypatch.setattr(devstream_session, "STREAM_STATUS_FILE", tmp_path / "stream_status.json")
    monkeypatch.setattr(devstream_session, "BATTLE_STATS_FILE", stats)
    monkeypatch.setattr(
        devstream_session,
        "STALE_BATTLE_BACKUP_DIR",
        tmp_path / "devstream" / "truth" / "stale-active-battles-backups",
    )
    monkeypatch.setattr(
        devstream_session,
        "STALE_STREAM_STATUS_BACKUP_DIR",
        tmp_path / "devstream" / "truth" / "stream-backups",
    )
    monkeypatch.setattr(devstream_session, "load_env_files", lambda: {})
    monkeypatch.setattr(devstream_session, "prepare_runtime_env", lambda env: {"PS_USERNAME": "bot"})
    monkeypatch.setattr(devstream_session, "any_battle_runner_alive", lambda: False)
    monkeypatch.setattr(devstream_session, "start_process", lambda *args, **kwargs: started.append(args) or {"pid": 1})
    runtime_lease = write_runtime_lease(
        tmp_path / "runtime-lease.json",
        allowed_purposes=[devstream_session.STALE_TRUTH_CLEANUP_PURPOSE],
    )

    args = argparse.Namespace(
        runtime_lease=str(runtime_lease),
        stale_after_seconds=180,
        stream_stale_after_seconds=180,
        execute=True,
    )

    assert devstream_session.cmd_cleanup_stale_truth(args) == 0

    payload = json.loads(capsys.readouterr().out)
    recovered = payload["abandonedBattleResultRecovery"]
    battles = json.loads(stats.read_text(encoding="utf-8"))["battles"]
    assert payload["activeBattleCleanup"]["cleared"] is True
    assert recovered["rowsAdded"] == 1
    assert recovered["battleIds"] == ["battle-gen9ou-2644396893"]
    assert battles[-1]["battle_id"] == "battle-gen9ou-2644396893"
    assert battles[-1]["operational_loss"] is True
    assert json.loads(active.read_text(encoding="utf-8"))["battles"] == []
    assert started == []


def test_cmd_cleanup_stale_truth_dry_run_reports_stream_plan_without_mutation(tmp_path, monkeypatch, capsys):
    stream = tmp_path / "stream_status.json"
    original = {"status": "Searching", "streaming": False, "stream_pid": None}
    stream.write_text(json.dumps(original), encoding="utf-8")
    old = time.time() - 90000
    os.utime(stream, (old, old))
    backup_dir = tmp_path / "devstream" / "truth" / "stream-backups"

    monkeypatch.setattr(devstream_session, "ROOT", tmp_path)
    monkeypatch.setattr(devstream_session, "STREAM_STATUS_FILE", stream)
    monkeypatch.setattr(devstream_session, "STALE_STREAM_STATUS_BACKUP_DIR", backup_dir)
    monkeypatch.setattr(devstream_session, "load_env_files", lambda: {})
    monkeypatch.setattr(devstream_session, "prepare_runtime_env", lambda env: {"PS_USERNAME": "bot"})
    monkeypatch.setattr(devstream_session, "any_battle_runner_alive", lambda: False)
    runtime_lease = write_runtime_lease(
        tmp_path / "runtime-lease.json",
        allowed_purposes=[devstream_session.STALE_TRUTH_CLEANUP_DRY_RUN_PURPOSE],
    )

    args = argparse.Namespace(
        runtime_lease=str(runtime_lease),
        stale_after_seconds=180,
        stream_stale_after_seconds=180,
        execute=False,
    )

    assert devstream_session.cmd_cleanup_stale_truth(args) == 0

    payload = json.loads(capsys.readouterr().out)
    cleanup = payload["streamStatusCleanup"]
    assert payload["dryRun"] is True
    assert cleanup["reason"] == "dry run; stale stream status cleanup planned only"
    assert cleanup["plannedAction"] == "archive stale stream_status.json and publish runtime_blocked status"
    assert json.loads(stream.read_text(encoding="utf-8")) == original
    assert not backup_dir.exists()


def test_python_module_available_reports_missing_runtime_dependency(monkeypatch):
    monkeypatch.setattr(
        devstream_session.subprocess,
        "run",
        lambda *args, **kwargs: SimpleNamespace(returncode=3, stderr="", stdout=""),
    )

    check = devstream_session.python_module_available("python", "psutil")

    assert check["name"] == "runtime_dependency_psutil"
    assert check["ok"] is False
    assert check["returnCode"] == 3
    assert "pip install" in check["installHint"]


def test_runtime_pid_file_check_reports_stale_dead_pid(tmp_path, monkeypatch):
    bot_pid = tmp_path / ".bot.pid"
    session_pid = tmp_path / ".pids" / "devstream_battle_session.pid"
    supervisor_pid = tmp_path / ".pids" / "devstream_battle_supervisor.pid"
    bot_pid.write_text("42024", encoding="utf-8")
    session_pid.parent.mkdir(parents=True)
    session_pid.write_text(
        json.dumps({"pid": 36676, "command": ["python", "run.py"], "startedAt": devstream_session.iso_now()}),
        encoding="utf-8",
    )

    monkeypatch.setattr(devstream_session, "BOT_LOCK_PID_FILE", bot_pid)
    monkeypatch.setattr(devstream_session, "BATTLE_PID_FILE", session_pid)
    monkeypatch.setattr(devstream_session, "SUPERVISOR_PID_FILE", supervisor_pid)
    monkeypatch.setattr(devstream_session.os, "kill", lambda pid, sig: (_ for _ in ()).throw(OSError("dead pid")))
    monkeypatch.setattr(devstream_session, "_process_snapshot", lambda pid: None)

    check = devstream_session.runtime_pid_file_check()

    assert check["ok"] is False
    assert check["staleCount"] == 2
    assert [item["pid"] for item in check["details"] if item["stale"]] == [42024, 36676]
    assert all("not a live expected Fouler process" in item["reason"] for item in check["details"] if item["stale"])


def test_showdown_account_authority_check_reports_current_env_doc_mismatch(tmp_path, monkeypatch):
    agents = tmp_path / "AGENTS.md"
    taskboard = tmp_path / "TASKBOARD.md"
    agents.write_text("account is **LEBOTJAMESXD00N**\n", encoding="utf-8")
    taskboard.write_text("Current: `PS_USERNAME=npctypebeat`.\n", encoding="utf-8")

    monkeypatch.setattr(devstream_session, "ACCOUNT_AUTHORITY_FILES", [agents, taskboard])

    check = devstream_session.showdown_account_authority_check({"PS_USERNAME": "claudechamp"})

    assert check["ok"] is False
    assert check["runtimeAccount"] == "claudechamp"
    assert check["distinctAccounts"] == ["claudechamp", "npctypebeat"]
    assert [item["account"] for item in check["documentedAccounts"]] == ["npctypebeat"]


def test_showdown_account_authority_check_reports_current_live_account_prose(tmp_path, monkeypatch):
    claude = tmp_path / "CLAUDE.md"
    claude.write_text(
        'The live bot account on this machine is the `.env`-configured `PS_USERNAME` '
        '(currently **"claudechamp"**), playing gen9ou.\n',
        encoding="utf-8",
    )

    monkeypatch.setattr(devstream_session, "ACCOUNT_AUTHORITY_FILES", [claude])

    check = devstream_session.showdown_account_authority_check({"PS_USERNAME": "LEBOTJAMESXD00N"})

    assert check["ok"] is False
    assert check["runtimeAccount"] == "LEBOTJAMESXD00N"
    assert [item["account"] for item in check["documentedAccounts"]] == ["claudechamp"]
    assert check["distinctAccounts"] == ["claudechamp", "LEBOTJAMESXD00N"]


def test_showdown_account_authority_check_ignores_historical_mission_prose(tmp_path, monkeypatch):
    agents = tmp_path / "AGENTS.md"
    taskboard = tmp_path / "TASKBOARD.md"
    agents.write_text("account is **LEBOTJAMESXD00N**\naccount naming LEBOTJAMESXD00N\n", encoding="utf-8")
    taskboard.write_text("Historical account `npctypebeat` appears in archived notes.\n", encoding="utf-8")

    monkeypatch.setattr(devstream_session, "ACCOUNT_AUTHORITY_FILES", [agents, taskboard])

    check = devstream_session.showdown_account_authority_check({"PS_USERNAME": "claudechamp"})

    assert check["ok"] is True
    assert check["runtimeAccount"] == "claudechamp"
    assert check["documentedAccounts"] == []
    assert check["distinctAccounts"] == ["claudechamp"]


def test_runtime_lease_account_does_not_rewrite_stale_inherited_env(tmp_path):
    runtime_lease = write_runtime_lease(tmp_path / "runtime-lease.json", account="LEBOTJAMESXD00N")

    env = devstream_session.apply_runtime_lease_account(
        {"PS_USERNAME": "npctypebeat", "SHOWDOWN_USER_ID": "npctypebeat"},
        argparse.Namespace(runtime_lease=str(runtime_lease)),
    )

    assert env["PS_USERNAME"] == "npctypebeat"
    assert env["SHOWDOWN_USER_ID"] == "npctypebeat"
    assert "SHOWDOWN_ACCOUNTS" not in env
    assert "FOULER_ACTIVE_ACCOUNT" not in env
    assert env["FOULER_RUNTIME_LEASE_ACCOUNT"] == "LEBOTJAMESXD00N"


def test_current_repo_docs_do_not_publish_fixed_live_account():
    assert devstream_session.documented_showdown_accounts() == []


def test_battle_supervisor_contract_is_no_start_ready():
    contract = devstream_session.battle_supervisor_contract()

    assert contract["ok"] is True
    assert contract["statusPath"].endswith("supervisor-status.json")
    assert all(item["ok"] for item in contract["requirements"])
    assert all(item["ok"] for item in contract["checks"])


def test_existing_battle_runner_start_result_reuses_any_live_runner(tmp_path, monkeypatch):
    bot_pid = tmp_path / ".bot.pid"
    session_pid = tmp_path / ".pids" / "devstream_battle_session.pid"
    command = ["python", "run.py", "--bot-mode", "search_ladder"]

    monkeypatch.setattr(devstream_session, "BATTLE_PID_FILE", session_pid)
    monkeypatch.setattr(devstream_session, "battle_pid_files", lambda: [bot_pid, session_pid])
    monkeypatch.setattr(
        devstream_session,
        "pid_alive",
        lambda path: (True, 29852) if path == bot_pid else (False, 45896),
    )

    payload = devstream_session.existing_battle_runner_start_result(command)

    assert payload is not None
    assert payload["alreadyRunning"] is True
    assert payload["pid"] == 29852
    assert payload["pidFile"] == str(bot_pid)
    assert payload["knownRunners"] == [{"pidFile": str(bot_pid), "pid": 29852}]
    assert payload["adoptedPidFile"] == {
        "pidFile": str(session_pid),
        "pid": 29852,
        "adoptedFrom": str(bot_pid),
    }
    assert payload["command"] == command
    parsed_session_pid = json.loads(session_pid.read_text(encoding="utf-8"))
    assert parsed_session_pid["pid"] == 29852
    assert parsed_session_pid["adoptedExistingProcess"] is True


def test_existing_battle_runner_start_result_rejects_conflicting_live_owners(tmp_path, monkeypatch):
    bot_pid = tmp_path / ".bot.pid"
    session_pid = tmp_path / ".pids" / "devstream_battle_session.pid"
    command = ["python", "run.py", "--bot-mode", "search_ladder"]
    session_pid.parent.mkdir(parents=True, exist_ok=True)
    bot_pid.write_text("1111", encoding="utf-8")
    session_payload = {"pid": 2222, "command": command, "startedAt": devstream_session.iso_now()}
    session_pid.write_text(json.dumps(session_payload), encoding="utf-8")

    monkeypatch.setattr(devstream_session, "BATTLE_PID_FILE", session_pid)
    monkeypatch.setattr(devstream_session, "battle_pid_files", lambda: [bot_pid, session_pid])
    monkeypatch.setattr(
        devstream_session,
        "pid_alive",
        lambda path: (True, 1111) if path == bot_pid else (True, 2222),
    )

    payload = devstream_session.existing_battle_runner_start_result(command)

    assert payload is not None
    assert payload["runtimeOwnershipConflict"] is True
    assert payload["blocked"] is True
    assert payload["skipped"] is True
    assert payload["duplicateBattleRunners"] is True
    assert payload["battleRunnerCount"] == 2
    assert payload["distinctPids"] == [1111, 2222]
    assert payload["knownRunners"] == [
        {"pidFile": str(bot_pid), "pid": 1111},
        {"pidFile": str(session_pid), "pid": 2222},
    ]
    assert payload["adoptedPidFile"] is None
    assert json.loads(session_pid.read_text(encoding="utf-8")) == session_payload


def test_existing_battle_runner_start_result_accepts_same_pid_in_multiple_owner_files(tmp_path, monkeypatch):
    bot_pid = tmp_path / ".bot.pid"
    session_pid = tmp_path / ".pids" / "devstream_battle_session.pid"
    command = ["python", "run.py", "--bot-mode", "search_ladder"]

    monkeypatch.setattr(devstream_session, "BATTLE_PID_FILE", session_pid)
    monkeypatch.setattr(devstream_session, "battle_pid_files", lambda: [bot_pid, session_pid])
    monkeypatch.setattr(devstream_session, "pid_alive", lambda path: (True, 29852))

    payload = devstream_session.existing_battle_runner_start_result(command)

    assert payload is not None
    assert payload.get("runtimeOwnershipConflict") is None
    assert payload["alreadyRunning"] is True
    assert payload["pid"] == 29852
    assert payload["pidFile"] == str(session_pid)
    assert payload["knownRunners"] == [
        {"pidFile": str(bot_pid), "pid": 29852},
        {"pidFile": str(session_pid), "pid": 29852},
    ]
    assert payload["adoptedPidFile"] is None


def test_start_process_rejects_conflicting_battle_runner_owners_without_spawning(tmp_path, monkeypatch):
    bot_pid = tmp_path / ".bot.pid"
    session_pid = tmp_path / ".pids" / "devstream_battle_session.pid"
    command = ["python", "run.py", "--bot-mode", "search_ladder"]

    monkeypatch.setattr(devstream_session, "BATTLE_PID_FILE", session_pid)
    monkeypatch.setattr(devstream_session, "battle_pid_files", lambda: [bot_pid, session_pid])
    monkeypatch.setattr(
        devstream_session,
        "pid_alive",
        lambda path: (True, 1111) if path == bot_pid else (True, 2222),
    )

    def fail_spawn(*args, **kwargs):
        raise AssertionError("should not spawn when battle runner ownership conflicts")

    monkeypatch.setattr(devstream_session.subprocess, "Popen", fail_spawn)

    payload = devstream_session.start_process(command, session_pid, {})

    assert payload["runtimeOwnershipConflict"] is True
    assert payload["distinctPids"] == [1111, 2222]


def test_bounded_session_command_wraps_run_py_for_truthful_idle_completion(monkeypatch):
    monkeypatch.setattr(devstream_session, "runtime_python", lambda: "python")
    command = devstream_session.shell_command_for_session(
        1,
        1,
        {
            "PS_USERNAME": "DekuFoulerLab",
            "PS_BOT_MODE": "search_ladder",
            "PS_FORMAT": "gen9ou",
            "TEAM_NAMES": "gen9/ou/fat-team-2-balance",
        },
    )

    assert command[:3] == ["python", "scripts/run_bounded_battle_session.py", "--"]
    assert command[3:5] == ["python", "run.py"]
    assert command[command.index("--run-count") + 1] == "1"
    assert command[command.index("--max-concurrent-battles") + 1] == "1"
    assert command[command.index("--search-parallelism") + 1] == "2"
    assert "DekuFoulerLab" in command


def test_terminate_battle_runners_covers_all_known_pid_files(tmp_path, monkeypatch):
    bot_pid = tmp_path / ".bot.pid"
    session_pid = tmp_path / ".pids" / "devstream_battle_session.pid"
    calls = []

    monkeypatch.setattr(devstream_session, "battle_pid_files", lambda: [bot_pid, session_pid])

    def fake_terminate(path, *, force=False):
        calls.append((path, force))
        return {"pidFile": str(path), "wasRunning": path == bot_pid}

    monkeypatch.setattr(devstream_session, "terminate_pid_file", fake_terminate)

    payload = devstream_session.terminate_battle_runners(force=True)

    assert calls == [(bot_pid, True), (session_pid, True)]
    assert payload == {
        ".bot.pid": {"pidFile": str(bot_pid), "wasRunning": True},
        "devstream_battle_session.pid": {"pidFile": str(session_pid), "wasRunning": False},
    }


def test_start_process_adopts_existing_matching_process_without_spawning(tmp_path, monkeypatch):
    pid_file = tmp_path / ".pids" / "devstream_obs_http.pid"
    command = ["python", "streaming/serve_obs_page.py"]

    monkeypatch.setattr(devstream_session, "PID_DIR", pid_file.parent)
    monkeypatch.setattr(devstream_session, "pid_alive", lambda path: (False, 32900))
    monkeypatch.setattr(devstream_session, "_find_existing_process", lambda cmd: 42208)
    monkeypatch.setattr(devstream_session, "obs_http_ready", lambda: True)

    def fail_spawn(*args, **kwargs):
        raise AssertionError("should not spawn when matching process already exists")

    monkeypatch.setattr(devstream_session.subprocess, "Popen", fail_spawn)

    payload = devstream_session.start_process(command, pid_file, {})

    assert payload["alreadyRunning"] is True
    assert payload["pid"] == 42208
    assert payload["previousPid"] == 32900
    assert payload["adoptedExistingProcess"] is True
    parsed = json.loads(pid_file.read_text(encoding="utf-8"))
    assert parsed["pid"] == 42208
    assert parsed["previousPid"] == 32900


def test_start_process_adopts_healthy_obs_endpoint_owned_by_external_service(tmp_path, monkeypatch):
    pid_file = tmp_path / ".pids" / "devstream_obs_http.pid"
    pid_file.parent.mkdir(parents=True)
    pid_file.write_text('{"pid": 6288}\n', encoding="utf-8")
    command = ["python", "streaming/serve_obs_page.py"]

    monkeypatch.setattr(devstream_session, "PID_DIR", pid_file.parent)
    monkeypatch.setattr(devstream_session, "pid_alive", lambda path: (False, 6288))
    monkeypatch.setattr(devstream_session, "_find_existing_process", lambda cmd: None)
    monkeypatch.setattr(devstream_session, "obs_http_ready", lambda: True)

    def fail_spawn(*args, **kwargs):
        raise AssertionError("should not spawn over a healthy service-owned OBS endpoint")

    monkeypatch.setattr(devstream_session.subprocess, "Popen", fail_spawn)

    payload = devstream_session.start_process(command, pid_file, {})

    assert payload["alreadyRunning"] is True
    assert payload["pid"] is None
    assert payload["previousPid"] == 6288
    assert payload["adoptedHealthyEndpoint"] is True
    assert payload["externalLifecycleOwner"] is True
    assert payload["removedStalePidFile"] is True
    assert not pid_file.exists()


def test_start_process_refuses_stale_obs_http_adoption(tmp_path, monkeypatch):
    pid_file = tmp_path / ".pids" / "devstream_obs_http.pid"
    command = ["python", "streaming/serve_obs_page.py"]
    spawned = {}
    terminated = []

    monkeypatch.setattr(devstream_session, "PID_DIR", tmp_path / ".pids")
    monkeypatch.setattr(devstream_session, "OBS_PID_FILE", pid_file)
    monkeypatch.setattr(devstream_session, "pid_alive", lambda path: (False, 32900))
    monkeypatch.setattr(devstream_session, "_find_existing_process", lambda cmd: 42208)
    monkeypatch.setattr(devstream_session, "obs_http_ready", lambda: False)
    monkeypatch.setattr(
        devstream_session,
        "terminate_process_pid",
        lambda pid, **kwargs: terminated.append((pid, kwargs)) or {"pid": pid, "wasRunning": True, "sent": "SIGTERM"},
    )

    class FakeProc:
        pid = 50001

    def fake_spawn(*args, **kwargs):
        spawned["args"] = args
        spawned["kwargs"] = kwargs
        return FakeProc()

    monkeypatch.setattr(devstream_session.subprocess, "Popen", fake_spawn)

    payload = devstream_session.start_process(command, pid_file, {})

    assert payload["pid"] == 50001
    assert payload.get("adoptedExistingProcess") is None
    assert payload["staleExistingProcess"]["pid"] == 42208
    assert terminated == [
        (
            42208,
            {
                "force": True,
                "reason": "OBS HTTP process matched command but /health was unavailable before restart",
            },
        )
    ]
    assert spawned


def test_recover_stale_battle_runtime_never_interrupts_active_battles(tmp_path, monkeypatch):
    monkeypatch.setattr(devstream_session, "read_active_battles", lambda: 1)

    payload = devstream_session.recover_stale_battle_runtime(execute=True, stale_after_seconds=180)

    assert payload["recovered"] is False
    assert payload["reason"] == "active battles are present; not replacing runner"


def test_drain_command_writes_request_without_terminating_active_battle(tmp_path, monkeypatch, capsys):
    pid_dir = tmp_path / ".pids"
    drain_file = pid_dir / "drain.request"
    args = argparse.Namespace(execute=True, reason="deploy refreshed legal-option trace proof")

    monkeypatch.setattr(devstream_session, "PID_DIR", pid_dir)
    monkeypatch.setattr(devstream_session, "DRAIN_FILE", drain_file)
    monkeypatch.setattr(devstream_session, "read_active_battles", lambda: 1)
    monkeypatch.setattr(devstream_session, "any_battle_runner_alive", lambda: True)
    monkeypatch.setattr(
        devstream_session,
        "live_battle_runner_owners",
        lambda: [{"pidFile": str(tmp_path / ".bot.pid"), "pid": 1234}],
    )

    assert devstream_session.cmd_drain(args) == 0

    payload = json.loads(capsys.readouterr().out)
    assert payload["schemaVersion"] == "fouler-play-devstream-drain-plan/v1"
    assert payload["activeBattleCount"] == 1
    assert payload["battleRunnerAlive"] is True
    assert payload["runtimeOwnership"]["duplicateBattleRunners"] is False
    assert payload["written"] is True
    assert "deploy refreshed legal-option trace proof" in drain_file.read_text(encoding="utf-8")


def test_drain_command_reports_duplicate_runner_owners_without_terminating(tmp_path, monkeypatch, capsys):
    pid_dir = tmp_path / ".pids"
    drain_file = pid_dir / "drain.request"
    bot_pid = tmp_path / ".bot.pid"
    session_pid = pid_dir / "devstream_battle_session.pid"
    args = argparse.Namespace(execute=True, reason="resolve duplicate runtime owners")
    terminate_calls = []

    monkeypatch.setattr(devstream_session, "PID_DIR", pid_dir)
    monkeypatch.setattr(devstream_session, "DRAIN_FILE", drain_file)
    monkeypatch.setattr(devstream_session, "read_active_battles", lambda: 2)
    monkeypatch.setattr(devstream_session, "any_battle_runner_alive", lambda: True)
    monkeypatch.setattr(
        devstream_session,
        "live_battle_runner_owners",
        lambda: [
            {"pidFile": str(bot_pid), "pid": 1111},
            {"pidFile": str(session_pid), "pid": 2222},
        ],
    )
    monkeypatch.setattr(
        devstream_session,
        "terminate_pid_file",
        lambda *args, **kwargs: terminate_calls.append((args, kwargs)),
    )

    assert devstream_session.cmd_drain(args) == 0

    payload = json.loads(capsys.readouterr().out)
    assert payload["runtimeOwnership"]["duplicateBattleRunners"] is True
    assert payload["runtimeOwnership"]["distinctPids"] == [1111, 2222]
    assert "drain/adopt exactly one live battle runner" in payload["runtimeOwnership"]["requiredHermesAction"]
    assert payload["written"] is True
    assert terminate_calls == []


def test_env_loader_strips_unquoted_inline_comments(tmp_path, monkeypatch):
    env_file = tmp_path / ".env"
    env_file.write_text(
        "\n".join(
            [
                "LOSS_TRIGGERED_DRAIN=0  # disable early-stop for devstream runs",
                "FOO='quoted # value'",
                'BAR="also # quoted"',
                "URL=https://example.test/path#anchor",
            ]
        ),
        encoding="utf-8",
    )

    monkeypatch.setattr(devstream_session, "ENV_FILES", [env_file])
    monkeypatch.delenv("LOSS_TRIGGERED_DRAIN", raising=False)
    monkeypatch.delenv("FOO", raising=False)
    monkeypatch.delenv("BAR", raising=False)
    monkeypatch.delenv("URL", raising=False)

    env = devstream_session.load_env_files()

    assert env["LOSS_TRIGGERED_DRAIN"] == "0"
    assert env["FOO"] == "quoted # value"
    assert env["BAR"] == "also # quoted"
    assert env["URL"] == "https://example.test/path#anchor"


def test_clear_stale_active_battles_backs_up_and_resets_dead_runner_truth(tmp_path, monkeypatch):
    active = tmp_path / "active_battles.json"
    active.write_text(
        json.dumps({"battles": [{"id": "battle-gen9ou-1"}], "count": 1, "max_slots": 3}),
        encoding="utf-8",
    )
    old = time.time() - 300
    os.utime(active, (old, old))
    backup_dir = tmp_path / "backups"

    monkeypatch.setattr(devstream_session, "ROOT", tmp_path)
    monkeypatch.setattr(devstream_session, "STALE_BATTLE_BACKUP_DIR", backup_dir)
    monkeypatch.setattr(devstream_session, "any_battle_runner_alive", lambda: False)

    payload = devstream_session.clear_stale_active_battles(execute=True, stale_after_seconds=180)
    parsed = json.loads(active.read_text(encoding="utf-8"))

    assert payload["cleared"] is True
    assert payload["activeBattleCount"] == 1
    assert parsed["battles"] == []
    assert parsed["count"] == 0
    assert parsed["clearReason"] == "stale active battle truth had no live battle runner"
    assert Path(payload["backupPath"]).exists()


def test_clear_stale_active_battles_refreshes_stale_empty_truth_without_runner(tmp_path, monkeypatch):
    active = tmp_path / "active_battles.json"
    active.write_text(json.dumps({"battles": [], "count": 0, "max_slots": 3}), encoding="utf-8")
    old = time.time() - 300
    os.utime(active, (old, old))
    backup_dir = tmp_path / "backups"

    monkeypatch.setattr(devstream_session, "ROOT", tmp_path)
    monkeypatch.setattr(devstream_session, "STALE_BATTLE_BACKUP_DIR", backup_dir)
    monkeypatch.setattr(devstream_session, "any_battle_runner_alive", lambda: False)

    payload = devstream_session.clear_stale_active_battles(execute=True, stale_after_seconds=180)
    parsed = json.loads(active.read_text(encoding="utf-8"))

    assert payload["cleared"] is True
    assert payload["activeBattleCount"] == 0
    assert payload["activeBattleTruthExists"] is True
    assert payload["stale"] is True
    assert payload["reason"] == "stale empty active battle truth refreshed before bounded session start"
    assert parsed["battles"] == []
    assert parsed["count"] == 0
    assert parsed["previousTruthWasEmpty"] is True
    assert parsed["clearReason"] == "stale active battle truth had no live battle runner"
    assert Path(payload["backupPath"]).exists()
    assert active.stat().st_mtime > old


def test_clear_stale_active_battles_preserves_live_runner_truth(tmp_path, monkeypatch):
    active = tmp_path / "active_battles.json"
    active.write_text(json.dumps({"battles": [{"id": "battle-gen9ou-1"}], "count": 1}), encoding="utf-8")
    old = time.time() - 300
    os.utime(active, (old, old))

    monkeypatch.setattr(devstream_session, "ROOT", tmp_path)
    monkeypatch.setattr(devstream_session, "any_battle_runner_alive", lambda: True)

    payload = devstream_session.clear_stale_active_battles(execute=True, stale_after_seconds=180)

    assert payload["cleared"] is False
    assert payload["reason"] == "battle runner is alive; preserving active battle truth"
    assert json.loads(active.read_text(encoding="utf-8"))["count"] == 1


def test_cmd_start_refreshes_stale_empty_active_truth_before_spawning(tmp_path, monkeypatch, capsys):
    active = tmp_path / "active_battles.json"
    active.write_text(json.dumps({"battles": [], "count": 0, "max_slots": 3}), encoding="utf-8")
    old = time.time() - 300
    os.utime(active, (old, old))
    started = []

    monkeypatch.setattr(devstream_session, "ROOT", tmp_path)
    monkeypatch.setattr(devstream_session, "PID_DIR", tmp_path / ".pids")
    monkeypatch.setattr(devstream_session, "OBS_PID_FILE", tmp_path / ".pids" / "obs.pid")
    monkeypatch.setattr(devstream_session, "BATTLE_PID_FILE", tmp_path / ".pids" / "battle.pid")
    monkeypatch.setattr(devstream_session, "STALE_BATTLE_BACKUP_DIR", tmp_path / "backups")
    monkeypatch.setattr(devstream_session, "load_env_files", lambda: {})
    monkeypatch.setattr(
        devstream_session,
        "prepare_runtime_env",
        lambda env: {"PS_USERNAME": "bot", "PS_PASSWORD": "secret"},
    )
    monkeypatch.setattr(devstream_session, "recent_showdown_credential_failure", lambda root: {"found": False})
    monkeypatch.setattr(devstream_session, "secure_env_files", lambda execute=False: [{"ok": True}])
    monkeypatch.setattr(devstream_session, "any_battle_runner_alive", lambda: False)
    monkeypatch.setattr(
        devstream_session,
        "recover_stale_battle_runtime",
        lambda **kwargs: {"recovered": False, "reason": "no stale live battle runner found"},
    )
    monkeypatch.setattr(devstream_session, "existing_battle_runner_start_result", lambda command: None)
    monkeypatch.setattr(devstream_session, "run_json", lambda command: ({"healthy": True}, None))
    monkeypatch.setattr(devstream_session.time, "sleep", lambda seconds: None)
    monkeypatch.setattr(
        devstream_session,
        "improvement_checkout_guard",
        lambda: {"ready": True, "blockers": []},
    )
    monkeypatch.setattr(
        devstream_session,
        "runtime_launch_preflight",
        lambda *args, **kwargs: {"ok": True, "blockers": []},
    )
    monkeypatch.setattr(
        devstream_session,
        "validate_runtime_lease_reservation",
        lambda *args, **kwargs: {
            "ok": True,
            "valid": True,
            "reservation": {
                "reservationId": "lease-test-reservation-1",
                "runtimeLeaseId": "lease-test",
                "supervisorInstanceId": "test-supervisor-instance",
                "runCount": 25,
            },
        },
    )

    def fake_start(command, pid_file, env):
        started.append((command, pid_file))
        return {"pid": 1000 + len(started), "pidFile": str(pid_file), "command": command}

    monkeypatch.setattr(devstream_session, "start_process", fake_start)
    runtime_lease = write_runtime_lease(tmp_path / "runtime-lease.json")

    args = argparse.Namespace(
        run_count=25,
        max_concurrent_battles=3,
        max_runtime_minutes=180,
        queue_timeout_seconds=180,
        turn_timeout_seconds=90,
        supervisor_sleep_seconds=15,
        replace_stale_runner=True,
        continuous=False,
        execute=True,
        enable_auto_improve=False,
        max_cycles=0,
        runtime_lease=str(runtime_lease),
    )

    assert devstream_session.cmd_start(args) == 0

    payload = json.loads(capsys.readouterr().out)
    cleanup = payload["staleActiveBattleCleanup"]
    parsed = json.loads(active.read_text(encoding="utf-8"))
    assert cleanup["cleared"] is True
    assert cleanup["reason"] == "stale empty active battle truth refreshed before bounded session start"
    assert parsed["previousTruthWasEmpty"] is True
    assert active.stat().st_mtime > old
    assert any(pid_file == devstream_session.BATTLE_PID_FILE for _, pid_file in started)


def test_cmd_start_execute_fails_closed_without_runtime_lease(tmp_path, monkeypatch, capsys):
    started = []

    monkeypatch.setattr(devstream_session, "load_env_files", lambda: {})
    monkeypatch.setattr(
        devstream_session,
        "prepare_runtime_env",
        lambda env: {"PS_USERNAME": "bot", "PS_PASSWORD": "secret"},
    )
    monkeypatch.setattr(devstream_session, "secure_env_files", lambda execute=False: [{"ok": True}])
    monkeypatch.setattr(
        devstream_session,
        "start_process",
        lambda *args, **kwargs: started.append(args) or {"pid": 1},
    )

    args = argparse.Namespace(
        run_count=1,
        max_concurrent_battles=1,
        max_runtime_minutes=180,
        queue_timeout_seconds=180,
        turn_timeout_seconds=90,
        supervisor_sleep_seconds=15,
        replace_stale_runner=True,
        continuous=False,
        execute=True,
        enable_auto_improve=False,
        max_cycles=0,
        runtime_lease=str(tmp_path / "missing-runtime-lease.json"),
    )

    assert devstream_session.cmd_start(args) == 2

    payload = json.loads(capsys.readouterr().out)
    assert payload["runtimeLease"]["ok"] is False
    assert "started" not in payload
    assert started == []


def test_cmd_start_fails_closed_on_interrupted_improvement_checkout(tmp_path, monkeypatch, capsys):
    started = []

    monkeypatch.setattr(devstream_session, "load_env_files", lambda: {})
    monkeypatch.setattr(
        devstream_session,
        "prepare_runtime_env",
        lambda env: {"PS_USERNAME": "bot", "PS_PASSWORD": "secret"},
    )
    monkeypatch.setattr(devstream_session, "secure_env_files", lambda execute=False: [{"ok": True}])
    monkeypatch.setattr(
        devstream_session,
        "improvement_checkout_guard",
        lambda: {
            "ready": False,
            "blockers": ["improve-agent lock exists", "engine checkout has uncommitted changes"],
        },
    )
    monkeypatch.setattr(
        devstream_session,
        "start_process",
        lambda *args, **kwargs: started.append(args) or {"pid": 1},
    )
    runtime_lease = write_runtime_lease(tmp_path / "runtime-lease.json")
    args = argparse.Namespace(
        run_count=1,
        max_concurrent_battles=1,
        max_runtime_minutes=180,
        queue_timeout_seconds=180,
        turn_timeout_seconds=90,
        supervisor_sleep_seconds=15,
        replace_stale_runner=True,
        continuous=False,
        execute=True,
        enable_auto_improve=False,
        max_cycles=0,
        runtime_lease=str(runtime_lease),
    )

    assert devstream_session.cmd_start(args) == 2

    payload = json.loads(capsys.readouterr().out)
    assert payload["status"] == "blocked-improvement-checkout"
    assert payload["improvementCheckoutGuard"]["ready"] is False
    assert started == []


def test_cmd_start_dry_run_reports_runtime_lease_blocker(tmp_path, monkeypatch, capsys):
    started = []

    monkeypatch.setattr(devstream_session, "load_env_files", lambda: {})
    monkeypatch.setattr(
        devstream_session,
        "prepare_runtime_env",
        lambda env: {"PS_USERNAME": "bot", "PS_PASSWORD": "secret"},
    )
    monkeypatch.setattr(devstream_session, "secure_env_files", lambda execute=False: [{"ok": True}])
    monkeypatch.setattr(
        devstream_session,
        "start_process",
        lambda *args, **kwargs: started.append(args) or {"pid": 1},
    )

    args = argparse.Namespace(
        run_count=1,
        max_concurrent_battles=1,
        max_runtime_minutes=180,
        queue_timeout_seconds=180,
        turn_timeout_seconds=90,
        supervisor_sleep_seconds=15,
        replace_stale_runner=True,
        continuous=False,
        execute=False,
        enable_auto_improve=False,
        max_cycles=0,
        runtime_lease=str(tmp_path / "missing-runtime-lease.json"),
    )

    assert devstream_session.cmd_start(args) == 2

    payload = json.loads(capsys.readouterr().out)
    assert payload["dryRun"] is True
    assert payload["status"] == "blocked-runtime-lease"
    assert payload["runtimeLease"]["ok"] is False
    assert "runtime lease file is missing" in " ".join(payload["runtimeLease"]["blockers"])
    assert "started" not in payload
    assert started == []


def test_cmd_start_dry_run_includes_valid_runtime_lease_preflight(tmp_path, monkeypatch, capsys):
    started = []
    monkeypatch.setattr(
        devstream_session,
        "improvement_checkout_guard",
        lambda: {"ready": True, "blockers": []},
    )

    monkeypatch.setattr(devstream_session, "load_env_files", lambda: {})
    monkeypatch.setattr(
        devstream_session,
        "prepare_runtime_env",
        lambda env: {"PS_USERNAME": "bot", "PS_PASSWORD": "secret"},
    )
    monkeypatch.setattr(devstream_session, "secure_env_files", lambda execute=False: [{"ok": True}])
    monkeypatch.setattr(
        devstream_session,
        "start_process",
        lambda *args, **kwargs: started.append(args) or {"pid": 1},
    )
    runtime_lease = write_runtime_lease(tmp_path / "runtime-lease.json")

    args = argparse.Namespace(
        run_count=1,
        max_concurrent_battles=1,
        max_runtime_minutes=180,
        queue_timeout_seconds=180,
        turn_timeout_seconds=90,
        supervisor_sleep_seconds=15,
        replace_stale_runner=True,
        continuous=False,
        execute=False,
        enable_auto_improve=False,
        max_cycles=0,
        runtime_lease=str(runtime_lease),
    )

    assert devstream_session.cmd_start(args) == 0

    payload = json.loads(capsys.readouterr().out)
    assert payload["dryRun"] is True
    assert payload["runtimeLease"]["ok"] is True
    assert payload["runtimeLease"]["requested"]["runCount"] == 1
    assert "started" not in payload
    assert started == []


def test_cmd_start_dry_run_only_lease_does_not_authorize_execute(tmp_path, monkeypatch, capsys):
    started = []
    monkeypatch.setattr(
        devstream_session,
        "improvement_checkout_guard",
        lambda: {"ready": True, "blockers": []},
    )

    monkeypatch.setattr(devstream_session, "load_env_files", lambda: {})
    monkeypatch.setattr(
        devstream_session,
        "prepare_runtime_env",
        lambda env: {"PS_USERNAME": "bot", "PS_PASSWORD": "secret"},
    )
    monkeypatch.setattr(devstream_session, "secure_env_files", lambda execute=False: [{"ok": True}])
    monkeypatch.setattr(
        devstream_session,
        "start_process",
        lambda *args, **kwargs: started.append(args) or {"pid": 1},
    )
    runtime_lease = write_runtime_lease(
        tmp_path / "runtime-lease.json",
        allowed_purposes=["devstream-start-dry-run"],
    )
    base_args = dict(
        run_count=1,
        max_concurrent_battles=1,
        max_runtime_minutes=180,
        queue_timeout_seconds=180,
        turn_timeout_seconds=90,
        supervisor_sleep_seconds=15,
        replace_stale_runner=True,
        continuous=False,
        enable_auto_improve=False,
        max_cycles=0,
        runtime_lease=str(runtime_lease),
    )

    assert devstream_session.cmd_start(argparse.Namespace(**base_args, execute=False)) == 0
    dry_run = json.loads(capsys.readouterr().out)

    assert dry_run["dryRun"] is True
    assert dry_run["runtimeLease"]["ok"] is True
    assert dry_run["runtimeLease"]["purpose"] == "devstream-start-dry-run"

    assert devstream_session.cmd_start(argparse.Namespace(**base_args, execute=True)) == 2
    execute = json.loads(capsys.readouterr().out)

    assert execute["dryRun"] is False
    assert execute["status"] == "blocked-runtime-lease"
    assert "does not allow purpose devstream-start" in " ".join(execute["runtimeLease"]["blockers"])
    assert "started" not in execute
    assert started == []


def test_forced_clear_active_battles_overrides_live_runner_truth(tmp_path, monkeypatch):
    active = tmp_path / "active_battles.json"
    active.write_text(json.dumps({"battles": [{"id": "battle-gen9ou-1"}], "count": 1}), encoding="utf-8")
    backup_dir = tmp_path / "backups"

    monkeypatch.setattr(devstream_session, "ROOT", tmp_path)
    monkeypatch.setattr(devstream_session, "STALE_BATTLE_BACKUP_DIR", backup_dir)
    monkeypatch.setattr(devstream_session, "any_battle_runner_alive", lambda: True)

    payload = devstream_session.clear_stale_active_battles(
        execute=True,
        stale_after_seconds=0,
        force=True,
        clear_reason="forced devstream stop terminated the battle runner; stale active battle truth must not stay public",
    )
    parsed = json.loads(active.read_text(encoding="utf-8"))

    assert payload["cleared"] is True
    assert payload["reason"] == "active battle truth cleared after forced stop"
    assert parsed["battles"] == []
    assert parsed["clearReason"] == "forced devstream stop terminated the battle runner; stale active battle truth must not stay public"


def test_pid_alive_rejects_reused_pid_with_wrong_command(tmp_path, monkeypatch):
    pid_file = tmp_path / "devstream_battle_session.pid"
    pid_file.write_text(
        json.dumps({"pid": 1234, "command": ["python", "run.py"], "startedAt": devstream_session.iso_now()}),
        encoding="utf-8",
    )

    monkeypatch.setattr(devstream_session.os, "kill", lambda pid, sig: None)
    monkeypatch.setattr(
        devstream_session,
        "_process_snapshot",
        lambda pid: {
            "running": True,
            "cmdline": ["python", "not_the_bot.py"],
            "cwd": str(devstream_session.ROOT),
            "createTime": time.time(),
        },
    )

    alive, pid = devstream_session.pid_alive(pid_file)

    assert pid == 1234
    assert alive is False


def test_pid_alive_rejects_process_created_before_pid_file_start(tmp_path, monkeypatch):
    pid_file = tmp_path / "devstream_battle_session.pid"
    pid_file.write_text(
        json.dumps({"pid": 1234, "command": ["python", "run.py"], "startedAt": devstream_session.iso_now()}),
        encoding="utf-8",
    )

    monkeypatch.setattr(devstream_session.os, "kill", lambda pid, sig: None)
    monkeypatch.setattr(
        devstream_session,
        "_process_snapshot",
        lambda pid: {
            "running": True,
            "cmdline": ["python", "run.py", "--bot-mode", "search_ladder"],
            "cwd": str(devstream_session.ROOT),
            "createTime": time.time() - 60,
        },
    )

    alive, pid = devstream_session.pid_alive(pid_file)

    assert pid == 1234
    assert alive is False


def test_pid_alive_accepts_matching_repo_process(tmp_path, monkeypatch):
    pid_file = tmp_path / "devstream_battle_session.pid"
    started = time.time() - 5
    command = ["python", "run.py", "--bot-mode", "search_ladder"]
    pid_file.write_text(
        json.dumps({"pid": 1234, "command": command, "started_at": started}),
        encoding="utf-8",
    )

    monkeypatch.setattr(devstream_session.os, "kill", lambda pid, sig: None)
    monkeypatch.setattr(
        devstream_session,
        "_process_snapshot",
        lambda pid: {
            "running": True,
            "cmdline": command,
            "cwd": str(devstream_session.ROOT),
            "createTime": started + 1,
        },
    )

    alive, pid = devstream_session.pid_alive(pid_file)

    assert pid == 1234
    assert alive is True


def test_pid_alive_rejects_legacy_bare_pid_when_signal_zero_fails(tmp_path, monkeypatch):
    pid_file = tmp_path / ".bot.pid"
    pid_file.write_text("29852", encoding="utf-8")

    monkeypatch.setattr(devstream_session, "BOT_LOCK_PID_FILE", pid_file)
    monkeypatch.setattr(devstream_session.os, "kill", lambda pid, sig: (_ for _ in ()).throw(OSError("signal 0 unsupported")))
    monkeypatch.setattr(
        devstream_session,
        "_process_snapshot",
        lambda pid: {
            "running": True,
            "cmdline": ["python", "run.py", "--bot-mode", "search_ladder"],
            "cwd": str(devstream_session.ROOT),
            "createTime": time.time(),
        },
    )

    alive, pid = devstream_session.pid_alive(pid_file)

    assert pid == 29852
    assert alive is False


def test_continuous_start_spawns_supervisor_not_direct_battle_runner(monkeypatch, capsys, tmp_path):
    calls = []
    supervisor_calls = []
    monkeypatch.setattr(
        devstream_session,
        "improvement_checkout_guard",
        lambda: {"ready": True, "blockers": []},
    )
    monkeypatch.setattr(
        devstream_session,
        "runtime_launch_preflight",
        lambda *args, **kwargs: {"ok": True, "blockers": []},
    )

    monkeypatch.setattr(devstream_session, "PID_DIR", tmp_path / ".pids")
    monkeypatch.setattr(devstream_session, "SUPERVISOR_STOP_FILE", tmp_path / ".pids" / "supervisor.stop")
    monkeypatch.setattr(devstream_session, "OBS_PID_FILE", tmp_path / ".pids" / "obs.pid")
    monkeypatch.setattr(devstream_session, "SUPERVISOR_PID_FILE", tmp_path / ".pids" / "supervisor.pid")
    monkeypatch.setattr(devstream_session, "BATTLE_PID_FILE", tmp_path / ".pids" / "battle.pid")
    monkeypatch.setattr(devstream_session, "load_env_files", lambda: {})
    monkeypatch.setattr(
        devstream_session,
        "prepare_runtime_env",
        lambda env: {"PS_USERNAME": "bot", "PS_PASSWORD": "secret"},
    )
    monkeypatch.setattr(devstream_session, "recent_showdown_credential_failure", lambda root: {"found": False})
    monkeypatch.setattr(devstream_session, "secure_env_files", lambda execute=False: [{"ok": True}])
    monkeypatch.setattr(devstream_session, "run_json", lambda command: ({"healthy": True}, None))
    monkeypatch.setattr(devstream_session.time, "sleep", lambda seconds: None)

    def fake_start(command, pid_file, env):
        calls.append((command, pid_file))
        return {"pid": 100 + len(calls), "pidFile": str(pid_file), "command": command}

    monkeypatch.setattr(devstream_session, "start_process", fake_start)
    monkeypatch.setattr(
        devstream_session,
        "start_supervisor_runtime",
        lambda args, command, env: supervisor_calls.append(command) or {"ok": True, "taskStatus": {"taskPresent": True}},
    )
    runtime_lease = write_runtime_lease(tmp_path / "runtime-lease.json")

    args = argparse.Namespace(
        run_count=25,
        max_concurrent_battles=3,
        max_runtime_minutes=180,
        queue_timeout_seconds=180,
        turn_timeout_seconds=90,
        supervisor_sleep_seconds=15,
        replace_stale_runner=True,
        continuous=True,
        execute=True,
        enable_auto_improve=False,
        max_cycles=1,
        runtime_lease=str(runtime_lease),
    )

    assert devstream_session.cmd_start(args) == 0

    assert supervisor_calls
    assert not any(pid_file == devstream_session.BATTLE_PID_FILE for _, pid_file in calls)
    payload = json.loads(capsys.readouterr().out)
    assert payload["started"]["battleSession"]["reason"] == "persistent supervisor owns bounded battle session starts"


def test_supervisor_cycle_refreshes_proof_then_starts_when_idle(monkeypatch):
    commands = []

    monkeypatch.setattr(devstream_session, "read_active_battles", lambda: 0)
    monkeypatch.setattr(devstream_session, "any_battle_runner_alive", lambda: False)
    monkeypatch.setattr(devstream_session, "supervisor_child_python", lambda: "python")
    recovered = {"rowsUpdated": 2, "recovered": True}
    monkeypatch.setattr(
        devstream_session,
        "recover_completed_battle_results_from_logs",
        lambda **kwargs: recovered,
    )

    def fake_run(command, *, timeout, env_overrides=None):
        commands.append(command)
        return successful_lifecycle_result(command)

    monkeypatch.setattr(devstream_session, "run_supervisor_command", fake_run)

    args = argparse.Namespace(
        run_count=25,
        max_concurrent_battles=3,
        queue_timeout_seconds=180,
        autoresearch_count=30,
        proof_timeout_seconds=300,
        start_timeout_seconds=60,
        improve_timeout_seconds=devstream_session.DEFAULT_IMPROVE_TIMEOUT_SECONDS,
        skip_improve=True,
    )

    payload = run_authorized_supervisor_cycle(monkeypatch, args, 1)

    assert payload["state"] == "idle-restoring-runtime"
    assert payload["completedBattleLogResultRecovery"] == recovered
    pipeline_command = next(command for command in commands if "pipeline.py" in command)
    report_command = next(command for command in commands if "scripts/devstream_cycle_report.py" in command)
    start_command = next(
        command
        for command in commands
        if command[:3] == ["python", "scripts/devstream_session.py", "start"]
    )
    assert pipeline_command[:4] == ["python", "pipeline.py", "autoresearch", "-n"]
    assert report_command == ["python", "scripts/devstream_cycle_report.py", "--write"]
    assert "--continuous" not in start_command


def test_supervisor_command_timeout_terminates_process_tree(monkeypatch):
    class FakeProcess:
        pid = 4321
        returncode = None

        def __init__(self):
            self.communicate_calls = 0

        def communicate(self, timeout):
            self.communicate_calls += 1
            if self.communicate_calls == 1:
                raise subprocess.TimeoutExpired(["python", "slow.py"], timeout, output="partial")
            return "after-cleanup", ""

        def poll(self):
            return self.returncode

    process = FakeProcess()
    monkeypatch.setattr(devstream_session, "load_env_files", lambda: {})
    monkeypatch.setattr(devstream_session, "prepare_runtime_env", lambda env: env)
    monkeypatch.setattr(devstream_session.subprocess, "Popen", lambda *args, **kwargs: process)
    monkeypatch.setattr(
        devstream_session,
        "terminate_supervisor_process_tree",
        lambda observed: {"pid": observed.pid, "stopped": True},
    )

    result = devstream_session.run_supervisor_command(["python", "slow.py"], timeout=1)

    assert result["timedOut"] is True
    assert result["processTreeCleanup"]["stopped"] is True
    assert "partial" in result["stdoutTail"]
    assert "after-cleanup" in result["stdoutTail"]


def test_improvement_checkout_guard_detects_dirty_engine_and_recovery_ownership(tmp_path, monkeypatch):
    subprocess.run(["git", "init", "-q", str(tmp_path)], check=True)
    subprocess.run(["git", "-C", str(tmp_path), "config", "user.email", "guard@test.invalid"], check=True)
    subprocess.run(["git", "-C", str(tmp_path), "config", "user.name", "Guard Test"], check=True)
    target = tmp_path / "fp" / "search" / "main.py"
    target.parent.mkdir(parents=True)
    target.write_text("VALUE = 1\n", encoding="utf-8")
    subprocess.run(["git", "-C", str(tmp_path), "add", "fp/search/main.py"], check=True)
    subprocess.run(["git", "-C", str(tmp_path), "commit", "-qm", "baseline"], check=True)
    lock = tmp_path / ".pids" / "improve-agent.lock"
    recovery = tmp_path / ".pids" / "improve-agent-recovery-block.json"
    monkeypatch.setattr(devstream_session, "ROOT", tmp_path)
    monkeypatch.setattr(devstream_session, "IMPROVE_AGENT_LOCK_FILE", lock)
    monkeypatch.setattr(devstream_session, "IMPROVE_AGENT_RECOVERY_BLOCK_FILE", recovery)

    assert devstream_session.improvement_checkout_guard()["ready"] is True

    lock.parent.mkdir(parents=True)
    lock.write_text("{}\n", encoding="utf-8")
    guarded = devstream_session.improvement_checkout_guard()
    assert guarded["ready"] is False
    assert "improve-agent lock exists" in guarded["blockers"]

    lock.unlink()
    target.write_text("VALUE = 2\n", encoding="utf-8")
    guarded = devstream_session.improvement_checkout_guard()
    assert guarded["ready"] is False
    assert "engine checkout has uncommitted or untracked changes" in guarded["blockers"]


def test_run_supervisor_cycle_waits_for_result_persistence_grace(monkeypatch):
    monkeypatch.setattr(devstream_session, "read_active_battles", lambda: 0)
    monkeypatch.setattr(devstream_session, "any_battle_runner_alive", lambda: True)
    monkeypatch.setattr(
        devstream_session,
        "idle_battle_runner_recovery_candidate",
        lambda stale_after_seconds: {"shouldRecover": True, "staleAfterSeconds": stale_after_seconds},
    )
    monkeypatch.setattr(devstream_session, "active_battles_age_seconds", lambda: 5.0)
    monkeypatch.setattr(devstream_session, "RESULT_PERSISTENCE_GRACE_SECONDS", 90)

    def fail_recovery(*args, **kwargs):
        raise AssertionError("stale runtime recovery must wait for result persistence grace")

    monkeypatch.setattr(devstream_session, "recover_stale_battle_runtime", fail_recovery)

    args = argparse.Namespace(
        run_count=5,
        max_concurrent_battles=1,
        queue_timeout_seconds=180,
    )

    payload = devstream_session.run_supervisor_cycle(args, 1, start_next=False)

    assert payload["state"] == "result-persistence-grace"
    assert payload["resultPersistenceGrace"]["activeBattleTruthAgeSeconds"] == 5.0
    assert payload["startNextBattleSession"] is False


def test_supervisor_cycle_caps_legacy_unbounded_run_count(monkeypatch):
    commands = []

    monkeypatch.setenv(devstream_session.RUN_COUNT_CAP_ENV, "7")
    monkeypatch.setattr(devstream_session, "read_active_battles", lambda: 0)
    monkeypatch.setattr(devstream_session, "any_battle_runner_alive", lambda: False)
    monkeypatch.setattr(devstream_session, "supervisor_child_python", lambda: "python")

    def fake_run(command, *, timeout, env_overrides=None):
        commands.append(command)
        return successful_lifecycle_result(command)

    monkeypatch.setattr(devstream_session, "run_supervisor_command", fake_run)

    args = argparse.Namespace(
        run_count=1000000,
        max_concurrent_battles=3,
        queue_timeout_seconds=180,
        autoresearch_count=30,
        proof_timeout_seconds=300,
        start_timeout_seconds=60,
        improve_timeout_seconds=240,
        skip_improve=True,
    )

    payload = run_authorized_supervisor_cycle(monkeypatch, args, 1)
    start_command = next(
        command
        for command in commands
        if command[:3] == ["python", "scripts/devstream_session.py", "start"]
    )

    assert payload["requestedRunCount"] == 1000000
    assert payload["effectiveRunCount"] == 7
    assert start_command[start_command.index("--run-count") + 1] == "7"


def test_supervisor_launch_preflight_blocks_before_reservation(monkeypatch):
    commands = []
    preflight_guards = []
    reserve_calls = []
    monkeypatch.setattr(devstream_session, "read_active_battles", lambda: 0)
    monkeypatch.setattr(devstream_session, "any_battle_runner_alive", lambda: False)
    monkeypatch.setattr(devstream_session, "supervisor_child_python", lambda: "python")
    monkeypatch.setattr(
        devstream_session,
        "runtime_launch_preflight",
        lambda _args, *, lease_guard: preflight_guards.append(lease_guard)
        or {
            "ok": False,
            "blockers": ["recent Showdown credential failure is unresolved"],
            "secretValuesPrinted": False,
        },
    )
    monkeypatch.setattr(
        devstream_session,
        "reserve_runtime_lease_consumption",
        lambda *args, **kwargs: reserve_calls.append((args, kwargs))
        or (_ for _ in ()).throw(AssertionError("reserve must not run")),
    )
    monkeypatch.setattr(
        devstream_session,
        "run_supervisor_command",
        lambda command, *, timeout, env_overrides=None: commands.append(command)
        or successful_lifecycle_result(command),
    )
    args = argparse.Namespace(
        run_count=3,
        max_concurrent_battles=3,
        queue_timeout_seconds=180,
        autoresearch_count=30,
        proof_timeout_seconds=300,
        start_timeout_seconds=60,
        skip_improve=True,
        enable_auto_improve=False,
    )

    payload = run_authorized_supervisor_cycle(monkeypatch, args, 1)

    assert payload["state"] == "blocked-runtime-launch-preflight"
    assert preflight_guards == [
        {
            "ok": True,
            "path": str((ROOT / "devstream" / "truth" / "runtime-lease.json").resolve()),
            "lease": {
                "id": "lease-test",
                "deploymentReceiptPath": r"C:\ProgramData\HERMES\state\fouler\deployment-test.json",
            },
        }
    ]
    assert reserve_calls == []
    assert "leaseConsumptionReservation" not in payload


def test_failed_launch_is_durably_aborted_and_does_not_leak_nonce(monkeypatch):
    reservation_state = {}
    completion_calls = []
    binding = {
        "reservationId": "res-" + "2" * 32,
        "kind": "runtime",
        "purpose": devstream_session.RUNTIME_RESERVATION_PURPOSE,
        "battleCount": 3,
        "cycleCount": 1,
        "maxConcurrentBattles": 3,
        "supervisorProcessId": os.getpid(),
        "supervisorProcessCreationFiletime": 133_801_234_567_890_000,
        "supervisorInstanceId": "test-supervisor-instance",
        "launchNonce": "8" * 64,
    }
    monkeypatch.setattr(devstream_session, "read_active_battles", lambda: 0)
    monkeypatch.setattr(devstream_session, "any_battle_runner_alive", lambda: False)
    monkeypatch.setattr(devstream_session, "supervisor_child_python", lambda: "python")
    monkeypatch.setattr(
        devstream_session,
        "reserve_runtime_lease_consumption",
        lambda *args, **kwargs: {
            "ok": True,
            "reserved": True,
            "reservation": dict(binding),
        },
    )

    def fake_run(command, *, timeout, env_overrides=None):
        if command[:3] == ["python", "scripts/devstream_session.py", "start"]:
            assert env_overrides[devstream_session.RUNTIME_LAUNCH_NONCE_ENV] == "8" * 64
            return {"command": command, "returnCode": 2, "error": "launch failed"}
        return successful_lifecycle_result(command)

    monkeypatch.setattr(devstream_session, "run_supervisor_command", fake_run)
    monkeypatch.setattr(
        devstream_session,
        "complete_runtime_lease_consumption",
        lambda lease_guard, *, reservation, outcome, env=None: completion_calls.append(
            (dict(reservation), outcome)
        )
        or {
            "ok": True,
            "completed": True,
            "state": "completed",
            "reservationId": reservation["reservationId"],
            "outcome": outcome,
            "capacityReturned": False,
        },
    )
    args = argparse.Namespace(
        run_count=3,
        max_concurrent_battles=3,
        queue_timeout_seconds=180,
        autoresearch_count=30,
        proof_timeout_seconds=300,
        start_timeout_seconds=60,
        skip_improve=True,
        enable_auto_improve=False,
    )

    payload = run_authorized_supervisor_cycle(
        monkeypatch, args, 1, reservation_state=reservation_state
    )

    assert payload["state"] == "blocked-battle-launch"
    assert completion_calls == [(binding, "aborted")]
    assert reservation_state == {}
    assert "launchNonce" not in payload["leaseConsumptionReservation"]["reservation"]
    assert payload["leaseConsumptionReservation"]["reservation"]["launchNoncePresent"] is True


def test_outstanding_reservation_is_reconciled_before_any_new_reserve(monkeypatch):
    reservation_state = {
        "reservationId": "res-" + "3" * 32,
        "kind": "runtime",
        "purpose": devstream_session.RUNTIME_RESERVATION_PURPOSE,
        "battleCount": 3,
        "cycleCount": 1,
        "maxConcurrentBattles": 3,
        "supervisorProcessId": os.getpid(),
        "supervisorProcessCreationFiletime": 133_801_234_567_890_000,
        "supervisorInstanceId": "test-supervisor-instance",
        "launchNonce": "7" * 64,
    }
    monkeypatch.setattr(devstream_session, "read_active_battles", lambda: 0)
    monkeypatch.setattr(devstream_session, "any_battle_runner_alive", lambda: False)
    monkeypatch.setattr(
        devstream_session,
        "runtime_reservation_status",
        lambda lease_guard, reservation: {
            "ok": True,
            "status": {"state": "claimed", "reservationId": reservation["reservationId"]},
        },
    )
    monkeypatch.setattr(
        devstream_session,
        "complete_runtime_lease_consumption",
        lambda lease_guard, *, reservation, outcome, env=None: {
            "ok": True,
            "completed": True,
            "state": "completed",
            "reservationId": reservation["reservationId"],
            "outcome": outcome,
        },
    )
    monkeypatch.setattr(
        devstream_session,
        "reserve_runtime_lease_consumption",
        lambda *args, **kwargs: (_ for _ in ()).throw(
            AssertionError("new reserve must not run while reconciling")
        ),
    )
    args = argparse.Namespace(
        run_count=3,
        max_concurrent_battles=3,
        queue_timeout_seconds=180,
    )

    payload = devstream_session.run_supervisor_cycle(
        args,
        2,
        lease_guard={"ok": True},
        supervisor_instance_id="test-supervisor-instance",
        reservation_state=reservation_state,
    )

    assert payload["state"] == "reconciled-orphaned-runtime"
    assert payload["leaseConsumptionTerminal"]["outcome"] == "failed"
    assert reservation_state == {}


def test_supervisor_cycle_skips_improve_without_explicit_opt_in(monkeypatch):
    commands = []

    monkeypatch.setattr(devstream_session, "read_active_battles", lambda: 0)
    monkeypatch.setattr(devstream_session, "any_battle_runner_alive", lambda: False)
    monkeypatch.setattr(devstream_session, "supervisor_child_python", lambda: "python")
    monkeypatch.setattr(devstream_session, "load_env_files", lambda: {})

    def fake_run(command, *, timeout, env_overrides=None):
        commands.append(command)
        return successful_lifecycle_result(command)

    monkeypatch.setattr(devstream_session, "run_supervisor_command", fake_run)

    args = argparse.Namespace(
        run_count=25,
        max_concurrent_battles=3,
        queue_timeout_seconds=180,
        autoresearch_count=30,
        proof_timeout_seconds=300,
        start_timeout_seconds=60,
        improve_timeout_seconds=240,
        skip_improve=False,
        enable_auto_improve=False,
    )

    payload = run_authorized_supervisor_cycle(monkeypatch, args, 1)

    assert payload["autoImprove"]["enabled"] is False
    assert "DEKU control plane" in payload["autoImprove"]["reason"]
    assert payload["autoImprove"]["delegatedTo"] == "DEKU external control plane"
    assert any(command[:4] == ["python", "pipeline.py", "autoresearch", "-n"] for command in commands)
    assert ["python", "scripts/devstream_cycle_report.py", "--write"] in commands
    assert any(
        command[:3] == ["python", "scripts/devstream_session.py", "start"]
        for command in commands
    )
    assert not any("infrastructure/improve_agent.py" in command for command in commands)
    assert sum("infrastructure/elo_watchdog.py" in command for command in commands) == 1


def test_supervisor_cycle_invalid_parent_authority_cannot_mint_improve_lease(monkeypatch):
    commands = []
    monkeypatch.setattr(devstream_session, "read_active_battles", lambda: 0)
    monkeypatch.setattr(devstream_session, "any_battle_runner_alive", lambda: False)
    monkeypatch.setattr(
        devstream_session,
        "run_supervisor_command",
        lambda command, *, timeout, env_overrides=None: commands.append(command)
        or {"command": command, "returnCode": 0},
    )
    args = argparse.Namespace(
        run_count=25,
        max_concurrent_battles=3,
        queue_timeout_seconds=180,
        enable_auto_improve=True,
        skip_improve=False,
    )

    payload = devstream_session.run_supervisor_cycle(
        args,
        2,
        start_next=False,
        authority_ok=False,
    )

    assert payload["state"] == "blocked-runtime-lease"
    assert payload["parentRuntimeAuthorityValid"] is False
    assert payload["autoImprove"]["enabled"] is False
    assert commands == []


def test_supervisor_cycle_waits_for_first_identity_row_without_deadlocking_start(monkeypatch):
    commands = []
    monkeypatch.setenv("FOULER_DEPLOYMENT_RECEIPT_PATH", r"C:\ProgramData\HERMES\state\deployment.json")
    monkeypatch.setattr(devstream_session, "read_active_battles", lambda: 0)
    monkeypatch.setattr(devstream_session, "any_battle_runner_alive", lambda: False)
    monkeypatch.setattr(devstream_session, "supervisor_child_python", lambda: "python")
    monkeypatch.setattr(
        devstream_session,
        "improvement_checkout_guard",
        lambda: {"ready": True, "blockers": []},
    )

    def fake_run(command, *, timeout, env_overrides=None):
        commands.append(command)
        if "scripts/fouler_deployment_state.py" in command:
            return {
                "command": command,
                "returnCode": 0,
                "json": {"ok": True, "status": "waiting-for-first-battle"},
            }
        return successful_lifecycle_result(command)

    monkeypatch.setattr(devstream_session, "run_supervisor_command", fake_run)
    args = argparse.Namespace(
        run_count=25,
        max_concurrent_battles=3,
        queue_timeout_seconds=180,
        autoresearch_count=30,
        proof_timeout_seconds=300,
        start_timeout_seconds=60,
        improve_timeout_seconds=devstream_session.DEFAULT_IMPROVE_TIMEOUT_SECONDS,
        skip_improve=False,
        enable_auto_improve=True,
        runtime_lease="devstream/truth/runtime-lease.json",
    )

    payload = run_authorized_supervisor_cycle(monkeypatch, args, 1)

    assert payload["deploymentActivation"]["json"]["status"] == "waiting-for-first-battle"
    assert payload["autoImprove"]["enabled"] is False
    assert any(command[:3] == ["python", "scripts/devstream_session.py", "start"] for command in commands)
    assert not any("infrastructure/improve_agent.py" in command for command in commands)
    assert not any("infrastructure/elo_watchdog.py" in command for command in commands)


def test_supervisor_cycle_blocks_next_batch_when_activation_proof_fails(monkeypatch):
    commands = []
    monkeypatch.setenv("FOULER_DEPLOYMENT_RECEIPT_PATH", r"C:\ProgramData\HERMES\state\deployment.json")
    monkeypatch.setattr(devstream_session, "read_active_battles", lambda: 0)
    monkeypatch.setattr(devstream_session, "any_battle_runner_alive", lambda: False)
    monkeypatch.setattr(devstream_session, "supervisor_child_python", lambda: "python")
    monkeypatch.setattr(
        devstream_session,
        "improvement_checkout_guard",
        lambda: {"ready": True, "blockers": []},
    )

    def fake_run(command, *, timeout, env_overrides=None):
        commands.append(command)
        if "scripts/fouler_deployment_state.py" in command:
            return {
                "command": command,
                "returnCode": 2,
                "json": {"ok": False, "status": "blocked", "blockers": ["identity mismatch"]},
            }
        return successful_lifecycle_result(command)

    monkeypatch.setattr(devstream_session, "run_supervisor_command", fake_run)
    args = argparse.Namespace(
        run_count=25,
        max_concurrent_battles=3,
        queue_timeout_seconds=180,
        autoresearch_count=30,
        proof_timeout_seconds=300,
        start_timeout_seconds=60,
        improve_timeout_seconds=devstream_session.DEFAULT_IMPROVE_TIMEOUT_SECONDS,
        skip_improve=True,
        enable_auto_improve=False,
        runtime_lease="devstream/truth/runtime-lease.json",
    )

    payload = run_authorized_supervisor_cycle(monkeypatch, args, 2)

    assert payload["state"] == "blocked-improvement-checkout"
    assert "deployment activation proof failed" in payload["startSkipped"]["blockers"]
    assert not any(command[:3] == ["python", "scripts/devstream_session.py", "start"] for command in commands)


def test_supervisor_cycle_delegates_explicit_improve_request_to_deku(monkeypatch, tmp_path):
    commands = []
    improve_lease = write_runtime_lease(
        tmp_path / "improve-runtime-lease.json",
        max_cycles=1,
        max_run_count=60,
        replay_behavior="never",
        allowed_purposes=["improve-agent"],
    )

    monkeypatch.setattr(devstream_session, "read_active_battles", lambda: 0)
    monkeypatch.setattr(devstream_session, "any_battle_runner_alive", lambda: False)
    monkeypatch.setattr(devstream_session, "supervisor_child_python", lambda: "python")

    monkeypatch.setattr(
        devstream_session,
        "load_env_files",
        lambda: {
            devstream_session.IMPROVE_RUNTIME_LEASE_PATH_ENV: str(improve_lease),
            "IMPROVE_AGENT_ACCOUNT": "bot",
        },
    )

    def fake_run(command, *, timeout, env_overrides=None):
        commands.append(command)
        return successful_lifecycle_result(command)

    monkeypatch.setattr(devstream_session, "run_supervisor_command", fake_run)

    args = argparse.Namespace(
        run_count=25,
        max_concurrent_battles=3,
        queue_timeout_seconds=180,
        autoresearch_count=30,
        proof_timeout_seconds=300,
        start_timeout_seconds=60,
        improve_timeout_seconds=devstream_session.DEFAULT_IMPROVE_TIMEOUT_SECONDS,
        skip_improve=False,
        enable_auto_improve=True,
    )

    payload = run_authorized_supervisor_cycle(monkeypatch, args, 1)

    assert payload["autoImprove"]["enabled"] is False
    assert payload["autoImprove"]["requested"] is True
    assert "DEKU control plane" in payload["autoImprove"]["reason"]
    assert payload["autoImprove"]["sentinel"] == devstream_session.AUTO_IMPROVE_SENTINEL
    start_command = next(
        command
        for command in commands
        if command[:3] == ["python", "scripts/devstream_session.py", "start"]
    )
    assert "improveLease" not in payload
    assert not any("infrastructure/improve_agent.py" in command for command in commands)
    assert sum(command == ["python", "infrastructure/elo_watchdog.py"] for command in commands) == 1
    assert start_command[:3] == ["python", "scripts/devstream_session.py", "start"]


def test_runtime_never_launches_improve_agent_even_when_requested(monkeypatch, tmp_path):
    commands = []
    improve_lease = write_runtime_lease(
        tmp_path / "improve-runtime-lease.json",
        max_cycles=1,
        max_run_count=60,
        replay_behavior="never",
        allowed_purposes=["improve-agent"],
    )

    monkeypatch.setattr(devstream_session, "read_active_battles", lambda: 0)
    monkeypatch.setattr(devstream_session, "any_battle_runner_alive", lambda: False)
    monkeypatch.setattr(devstream_session, "supervisor_child_python", lambda: "python")
    monkeypatch.setattr(
        devstream_session,
        "load_env_files",
        lambda: {
            devstream_session.IMPROVE_RUNTIME_LEASE_PATH_ENV: str(improve_lease),
            "IMPROVE_AGENT_ACCOUNT": "bot",
        },
    )
    monkeypatch.setattr(
        devstream_session,
        "improvement_checkout_guard",
        lambda: {"ready": True, "blockers": []},
    )

    def fake_run(command, *, timeout, env_overrides=None):
        commands.append(command)
        if "infrastructure/improve_agent.py" in command:
            return {"command": command, "returnCode": 2, "error": "candidate recovery blocked"}
        return successful_lifecycle_result(command)

    monkeypatch.setattr(devstream_session, "run_supervisor_command", fake_run)
    args = argparse.Namespace(
        run_count=25,
        max_concurrent_battles=3,
        queue_timeout_seconds=180,
        autoresearch_count=30,
        proof_timeout_seconds=300,
        start_timeout_seconds=60,
        improve_timeout_seconds=devstream_session.DEFAULT_IMPROVE_TIMEOUT_SECONDS,
        skip_improve=False,
        enable_auto_improve=True,
    )

    payload = run_authorized_supervisor_cycle(monkeypatch, args, 1)

    assert payload["autoImprove"]["enabled"] is False
    assert payload["autoImprove"]["requested"] is True
    assert not any("infrastructure/improve_agent.py" in command for command in commands)
    assert any(command[:3] == ["python", "scripts/devstream_session.py", "start"] for command in commands)


def test_supervisor_cycle_blocks_live_start_on_stale_improve_ownership(monkeypatch):
    commands = []

    monkeypatch.setattr(devstream_session, "read_active_battles", lambda: 0)
    monkeypatch.setattr(devstream_session, "any_battle_runner_alive", lambda: False)
    monkeypatch.setattr(devstream_session, "supervisor_child_python", lambda: "python")
    monkeypatch.setattr(
        devstream_session,
        "improvement_checkout_guard",
        lambda: {"ready": False, "blockers": ["improve-agent recovery block exists"]},
    )
    def fake_run(command, *, timeout, env_overrides=None):
        commands.append(command)
        return successful_lifecycle_result(command)

    monkeypatch.setattr(devstream_session, "run_supervisor_command", fake_run)
    args = argparse.Namespace(
        run_count=25,
        max_concurrent_battles=3,
        queue_timeout_seconds=180,
        autoresearch_count=30,
        proof_timeout_seconds=300,
        start_timeout_seconds=60,
        improve_timeout_seconds=devstream_session.DEFAULT_IMPROVE_TIMEOUT_SECONDS,
        skip_improve=True,
    )

    payload = run_authorized_supervisor_cycle(monkeypatch, args, 1)

    assert payload["state"] == "blocked-improvement-checkout"
    assert payload["startSkipped"]["blockers"] == ["improve-agent recovery block exists"]
    assert not any(command[:3] == ["python", "scripts/devstream_session.py", "start"] for command in commands)


def test_supervisor_cycle_delegates_env_improve_sentinel_to_deku(monkeypatch, tmp_path):
    commands = []
    improve_lease = write_runtime_lease(
        tmp_path / "improve-runtime-lease.json",
        max_cycles=1,
        max_run_count=60,
        replay_behavior="never",
        allowed_purposes=["improve-agent"],
    )

    monkeypatch.setattr(devstream_session, "read_active_battles", lambda: 0)
    monkeypatch.setattr(devstream_session, "any_battle_runner_alive", lambda: False)
    monkeypatch.setattr(devstream_session, "supervisor_child_python", lambda: "python")
    monkeypatch.setattr(
        devstream_session,
        "load_env_files",
        lambda: {
            devstream_session.AUTO_IMPROVE_SENTINEL: "1",
            devstream_session.IMPROVE_RUNTIME_LEASE_PATH_ENV: str(improve_lease),
            "IMPROVE_AGENT_ACCOUNT": "bot",
        },
    )

    def fake_run(command, *, timeout, env_overrides=None):
        commands.append(command)
        return successful_lifecycle_result(command)

    monkeypatch.setattr(devstream_session, "run_supervisor_command", fake_run)

    args = argparse.Namespace(
        run_count=25,
        max_concurrent_battles=3,
        queue_timeout_seconds=180,
        autoresearch_count=30,
        proof_timeout_seconds=300,
        start_timeout_seconds=60,
        improve_timeout_seconds=devstream_session.DEFAULT_IMPROVE_TIMEOUT_SECONDS,
        skip_improve=False,
        enable_auto_improve=False,
    )

    payload = run_authorized_supervisor_cycle(monkeypatch, args, 1)

    assert payload["autoImprove"]["enabled"] is False
    assert payload["autoImprove"]["requested"] is True
    assert "DEKU control plane" in payload["autoImprove"]["reason"]
    assert not any("infrastructure/improve_agent.py" in command for command in commands)
    assert sum(command == ["python", "infrastructure/elo_watchdog.py"] for command in commands) == 1


def test_supervisor_auto_improve_rejects_env_sentinel_in_runtime():
    args = argparse.Namespace(skip_improve=False, enable_auto_improve=False)

    enabled, reason = devstream_session.supervisor_auto_improve_enabled(
        args,
        {devstream_session.AUTO_IMPROVE_SENTINEL: "1"},
    )

    assert enabled is False
    assert "DEKU control plane" in reason


def test_improve_authority_environment_does_not_replace_battle_identity(monkeypatch):
    monkeypatch.setattr(
        devstream_session,
        "lease_environment",
        lambda _guard: {
            "FOULER_RUNTIME_LEASE_ID": "improve-lease-test",
            "FOULER_RUNTIME_AUTHORIZATION_SHA256": "i" * 64,
            "FOULER_SOURCE_COMMIT": "b" * 40,
            devstream_session.RUNTIME_LEASE_PATH_ENV: r"C:\authority\improve.json",
        },
    )

    namespaced = devstream_session.improve_authority_environment({"ok": True})
    merged = {
        "FOULER_RUNTIME_LEASE_ID": "battle-lease-test",
        "FOULER_RUNTIME_AUTHORIZATION_SHA256": "a" * 64,
        **namespaced,
    }

    assert merged["FOULER_RUNTIME_LEASE_ID"] == "battle-lease-test"
    assert merged["FOULER_RUNTIME_AUTHORIZATION_SHA256"] == "a" * 64
    assert merged["FOULER_IMPROVE_RUNTIME_LEASE_ID"] == "improve-lease-test"
    assert merged["FOULER_IMPROVE_RUNTIME_AUTHORIZATION_SHA256"] == "i" * 64
    assert merged[devstream_session.IMPROVE_RUNTIME_LEASE_PATH_ENV] == r"C:\authority\improve.json"


def test_improve_eval_battles_cannot_collapse_to_live_batch_size():
    assert devstream_session.improve_eval_battle_count({}) == 60
    assert devstream_session.improve_eval_battle_count({"IMPROVE_AGENT_EVAL_BATTLES": "5"}) == 60
    assert devstream_session.improve_eval_battle_count({"IMPROVE_AGENT_EVAL_BATTLES": "72"}) == 72
    assert devstream_session.improve_eval_battle_count({"IMPROVE_AGENT_EVAL_BATTLES": "61"}) == 60


def test_auto_improve_request_does_not_mint_runtime_cycle_lease():
    args = argparse.Namespace(skip_improve=False, enable_auto_improve=True, max_cycles=0)

    limit, reason = devstream_session.supervisor_cycle_limit(args, {})

    assert limit == 0
    assert reason == "unbounded supervisor without auto-improve"


def test_auto_improve_environment_cannot_mint_runtime_cycle_lease():
    args = argparse.Namespace(skip_improve=False, enable_auto_improve=True, max_cycles=0)

    limit, reason = devstream_session.supervisor_cycle_limit(
        args,
        {devstream_session.AUTO_IMPROVE_MAX_CYCLES_ENV: "2"},
    )

    assert limit == 0
    assert reason == "unbounded supervisor without auto-improve"


def test_supervisor_explicit_max_cycles_wins_over_auto_improve_lease():
    args = argparse.Namespace(skip_improve=False, enable_auto_improve=True, max_cycles=4)

    limit, reason = devstream_session.supervisor_cycle_limit(args, {})

    assert limit == 4
    assert reason == "--max-cycles"


def test_cmd_supervise_fails_closed_without_runtime_lease_or_cycle_bound(tmp_path, monkeypatch, capsys):
    monkeypatch.setattr(devstream_session, "SUPERVISOR_STATUS_FILE", tmp_path / "supervisor-status.json")
    monkeypatch.setattr(devstream_session, "load_env_files", lambda: {"PS_USERNAME": "bot"})
    monkeypatch.setattr(devstream_session, "prepare_runtime_env", lambda env: env)
    monkeypatch.setattr(
        devstream_session,
        "write_pid_value",
        lambda *args, **kwargs: (_ for _ in ()).throw(AssertionError("supervisor PID must not be written")),
    )

    args = argparse.Namespace(
        run_count=1,
        max_concurrent_battles=1,
        queue_timeout_seconds=180,
        sleep_seconds=15,
        max_cycles=0,
        autoresearch_count=30,
        proof_timeout_seconds=300,
        start_timeout_seconds=60,
        improve_timeout_seconds=240,
        skip_improve=True,
        enable_auto_improve=False,
        runtime_lease=str(tmp_path / "missing-runtime-lease.json"),
    )

    assert devstream_session.cmd_supervise(args) == 2

    payload = json.loads(capsys.readouterr().out)
    assert payload["state"] == "blocked-runtime-lease"
    assert payload["runtimeLease"]["ok"] is False
    assert "requested max cycles" in " ".join(payload["runtimeLease"]["blockers"])


def test_cmd_supervise_preserves_stale_env_for_lease_mismatch_detection(tmp_path, monkeypatch):
    captured = {}
    runtime_lease = write_runtime_lease(tmp_path / "runtime-lease.json", account="LEBOTJAMESXD00N")

    monkeypatch.setattr(devstream_session, "SUPERVISOR_STATUS_FILE", tmp_path / "supervisor-status.json")
    monkeypatch.setattr(devstream_session, "load_env_files", lambda: {"PS_USERNAME": "npctypebeat"})
    monkeypatch.setattr(devstream_session, "prepare_runtime_env", lambda env: env)
    monkeypatch.setattr(devstream_session, "write_supervisor_status", lambda payload: None)
    monkeypatch.setattr(
        devstream_session,
        "write_pid_value",
        lambda *args, **kwargs: (_ for _ in ()).throw(AssertionError("blocked supervisor must not write pid")),
    )

    def fake_guard(**kwargs):
        captured["env"] = dict(kwargs["env"])
        return {"ok": False, "blockers": ["intentional stop after env capture"]}

    monkeypatch.setattr(devstream_session, "runtime_lease_guard", fake_guard)

    args = argparse.Namespace(
        run_count=30,
        max_concurrent_battles=3,
        queue_timeout_seconds=180,
        sleep_seconds=15,
        max_cycles=1,
        autoresearch_count=30,
        proof_timeout_seconds=300,
        start_timeout_seconds=60,
        improve_timeout_seconds=240,
        skip_improve=True,
        enable_auto_improve=False,
        runtime_lease=str(runtime_lease),
    )

    assert devstream_session.cmd_supervise(args) == 2
    assert captured["env"]["PS_USERNAME"] == "npctypebeat"
    assert "FOULER_RUNTIME_LEASE_ACCOUNT" not in captured["env"]


def test_supervisor_commands_propagate_auto_improve_to_task_installer():
    args = argparse.Namespace(
        run_count=25,
        max_concurrent_battles=3,
        queue_timeout_seconds=180,
        supervisor_sleep_seconds=15,
        max_cycles=1,
        runtime_lease="devstream/truth/runtime-lease.json",
        enable_auto_improve=True,
    )

    supervisor_command = devstream_session.supervisor_command(
        25,
        3,
        180,
        15,
        enable_auto_improve=True,
        max_cycles=1,
        runtime_lease="devstream/truth/runtime-lease.json",
    )
    task_command = devstream_session.battle_supervisor_task_command(args)

    assert supervisor_command[-1] == "--enable-auto-improve"
    assert "--max-cycles" in supervisor_command
    assert "--runtime-lease" in supervisor_command
    assert "-MaxCycles" in task_command
    assert "-RuntimeLease" in task_command
    assert "-AutoImprove" in task_command


def test_supervisor_cycle_clears_stale_active_truth_when_runner_is_dead(monkeypatch):
    commands = []
    counts = [1, 0, 0]

    monkeypatch.setattr(devstream_session, "read_active_battles", lambda: counts.pop(0) if counts else 0)
    monkeypatch.setattr(devstream_session, "any_battle_runner_alive", lambda: False)
    monkeypatch.setattr(devstream_session, "supervisor_child_python", lambda: "python")

    def fake_clear(**kwargs):
        return {
            "cleared": True,
            "execute": kwargs["execute"],
            "staleAfterSeconds": kwargs["stale_after_seconds"],
            "clearReason": kwargs["clear_reason"],
        }

    def fake_run(command, *, timeout, env_overrides=None):
        commands.append(command)
        return successful_lifecycle_result(command)

    monkeypatch.setattr(devstream_session, "clear_stale_active_battles", fake_clear)
    monkeypatch.setattr(devstream_session, "run_supervisor_command", fake_run)

    args = argparse.Namespace(
        run_count=25,
        max_concurrent_battles=3,
        queue_timeout_seconds=180,
        autoresearch_count=30,
        proof_timeout_seconds=300,
        start_timeout_seconds=60,
        improve_timeout_seconds=240,
        skip_improve=True,
    )

    payload = run_authorized_supervisor_cycle(monkeypatch, args, 2)

    assert payload["state"] == "idle-restoring-runtime"
    assert payload["staleActiveBattleClear"]["cleared"] is True
    assert payload["staleActiveBattleClear"]["staleAfterSeconds"] == 180
    assert payload["activeBattleCountAfterClear"] == 0
    start_command = next(
        command
        for command in commands
        if command[:3] == ["python", "scripts/devstream_session.py", "start"]
    )
    assert start_command[start_command.index("--max-concurrent-battles") + 1] == "3"


def test_supervisor_cycle_waits_when_battle_runner_alive(monkeypatch):
    monkeypatch.setattr(devstream_session, "read_active_battles", lambda: 0)
    monkeypatch.setattr(devstream_session, "any_battle_runner_alive", lambda: True)

    payload = devstream_session.run_supervisor_cycle(argparse.Namespace(), 7)

    assert payload["state"] == "battle-cycle-in-flight"
    assert payload["cycleIndex"] == 7
    assert payload["actions"] == []


def test_supervisor_cycle_can_refresh_proof_without_starting_next_batch(monkeypatch):
    commands = []

    monkeypatch.setattr(devstream_session, "read_active_battles", lambda: 0)
    monkeypatch.setattr(devstream_session, "any_battle_runner_alive", lambda: False)
    monkeypatch.setattr(devstream_session, "supervisor_child_python", lambda: "python")

    def fake_run(command, *, timeout, env_overrides=None):
        commands.append(command)
        return {"command": command, "returnCode": 0}

    monkeypatch.setattr(devstream_session, "run_supervisor_command", fake_run)

    args = argparse.Namespace(
        run_count=25,
        max_concurrent_battles=3,
        queue_timeout_seconds=180,
        autoresearch_count=30,
        proof_timeout_seconds=300,
        start_timeout_seconds=60,
        improve_timeout_seconds=240,
        skip_improve=True,
    )

    payload = devstream_session.run_supervisor_cycle(args, 8, start_next=False)

    assert payload["proofRefreshed"] is True
    assert payload["startNextBattleSession"] is False
    assert payload["startSkipped"]["reason"].startswith("bounded learning-cycle limit")
    assert payload["nextAction"] == "bounded learning cycle complete; supervisor may stop"
    assert commands == [
        ["python", "pipeline.py", "autoresearch", "-n", "30", "--no-discord"],
        ["python", "scripts/devstream_cycle_report.py", "--write"],
    ]


def test_cmd_supervise_carries_terminal_binding_across_live_poll_race(tmp_path, monkeypatch):
    statuses = []
    start_next_flags = []
    runtime_lease = install_supervisor_authority_fixture(monkeypatch, tmp_path, run_count=30)
    runtime_states = iter(
        [
            {"activeBattleCount": 0, "battleRunnerAlive": False, "inFlight": False},
            {"activeBattleCount": 1, "battleRunnerAlive": True, "inFlight": True},
            {"activeBattleCount": 0, "battleRunnerAlive": False, "inFlight": False},
        ]
    )

    monkeypatch.setattr(devstream_session, "SUPERVISOR_STOP_FILE", tmp_path / "supervisor.stop")
    monkeypatch.setattr(devstream_session, "SUPERVISOR_PID_FILE", tmp_path / "supervisor.pid")
    monkeypatch.setattr(devstream_session, "SUPERVISOR_STATUS_FILE", tmp_path / "supervisor-status.json")
    monkeypatch.setattr(devstream_session, "load_env_files", lambda: {"PS_USERNAME": "bot"})
    monkeypatch.setattr(devstream_session, "prepare_runtime_env", lambda env: env)
    monkeypatch.setattr(devstream_session, "write_pid_value", lambda *args, **kwargs: None)
    monkeypatch.setattr(devstream_session.time, "sleep", lambda seconds: None)
    monkeypatch.setattr(devstream_session, "supervisor_runtime_state", lambda: next(runtime_states))
    monkeypatch.setattr(
        devstream_session,
        "idle_battle_runner_recovery_candidate",
        lambda stale_after_seconds: {"shouldRecover": False},
    )

    def fake_write_status(payload):
        statuses.append(json.loads(json.dumps(payload)))

    def fake_cycle(
        args,
        cycle_index,
        *,
        start_next=True,
        authority_ok=True,
        lease_guard=None,
        supervisor_instance_id=None,
        reservation_state=None,
    ):
        assert authority_ok is True
        assert lease_guard["lease"]["id"] == "lease-test"
        assert supervisor_instance_id
        start_next_flags.append(start_next)
        if cycle_index == 1:
            reservation_state.update(
                {
                    "reservationId": "res-" + "1" * 32,
                    "kind": "runtime",
                    "purpose": devstream_session.RUNTIME_RESERVATION_PURPOSE,
                    "battleCount": 30,
                    "cycleCount": 1,
                    "maxConcurrentBattles": 3,
                    "supervisorProcessId": os.getpid(),
                    "supervisorProcessCreationFiletime": 133_801_234_567_890_000,
                    "supervisorInstanceId": supervisor_instance_id,
                    "launchNonce": "9" * 64,
                }
            )
            return {
                "state": "idle-restoring-runtime",
                "proofRefreshed": True,
                "battleRunnerAliveAfter": True,
                "activeBattleCountAfter": 0,
                "leaseConsumptionReservation": {
                    "ok": True,
                    "reserved": True,
                    "reservation": {
                        "reservationId": "lease-test-reservation-1",
                        "runtimeLeaseId": "lease-test",
                        "supervisorInstanceId": supervisor_instance_id,
                        "runCount": 30,
                    },
                },
            }
        if cycle_index == 2:
            reservation_state.clear()
            return {
                "state": "reconciled-completed-runtime",
                "proofRefreshed": False,
                "battleRunnerAliveAfter": False,
                "activeBattleCountAfter": 0,
                "leaseConsumptionTerminal": {
                    "state": "completed",
                    "outcome": "completed",
                    "reservationId": "res-" + "1" * 32,
                },
            }
        if cycle_index == 3:
            assert reservation_state == {}
            return {
                "state": "idle-restoring-runtime",
                "proofRefreshed": True,
                "battleRunnerAliveAfter": False,
                "activeBattleCountAfter": 0,
            }
        raise AssertionError("supervisor should stop after one completed learning cycle")

    monkeypatch.setattr(devstream_session, "write_supervisor_status", fake_write_status)
    monkeypatch.setattr(devstream_session, "run_supervisor_cycle", fake_cycle)

    args = argparse.Namespace(
        run_count=30,
        max_concurrent_battles=3,
        queue_timeout_seconds=180,
        sleep_seconds=15,
        max_cycles=1,
        autoresearch_count=30,
        proof_timeout_seconds=300,
        start_timeout_seconds=60,
        improve_timeout_seconds=240,
        skip_improve=True,
        enable_auto_improve=False,
        runtime_lease=str(runtime_lease),
    )

    assert devstream_session.cmd_supervise(args) == 0

    assert start_next_flags == [True, True, False]
    final = statuses[-1]
    assert final["state"] == "completed-max-cycles"
    assert final["completedLearningCycles"] == 1
    assert final["bounds"]["maxCyclesSemantics"] == "completed bounded learning cycles, not supervisor polling heartbeats"
    assert final["lastCycle"]["learningCycleCompleted"] is True
    assert final["lastCycle"]["completedLearningCycles"] == 1

    assert final["lastCycle"]["leaseConsumptionTerminal"]["reservationId"] == (
        "res-" + "1" * 32
    )



def test_cmd_supervise_restart_accepts_broker_completed_batch(
    tmp_path, monkeypatch
):
    statuses = []
    runtime_lease = install_supervisor_authority_fixture(
        monkeypatch, tmp_path, run_count=30
    )
    terminal = {
        "reservationId": "res-" + "6" * 32,
        "kind": "runtime",
        "purpose": devstream_session.RUNTIME_RESERVATION_PURPOSE,
        "battleCount": 30,
        "cycleCount": 1,
        "maxConcurrentBattles": 3,
        "supervisorProcessId": 2222,
        "supervisorProcessCreationFiletime": 133_801_234_567_890_000,
        "supervisorInstanceId": "supervisor-2222-prior",
        "state": "completed",
        "outcome": "completed",
        "completionActor": "claimant",
        "capacityReturned": False,
    }
    monkeypatch.setattr(
        devstream_session,
        "runtime_lease_consumption_status",
        lambda *args, **kwargs: {
            "ok": True,
            "blockers": [],
            "status": {
                "successfulCycleCount": 1,
                "remainingRunCount": 0,
                "remainingCycles": 0,
                "latestReservation": dict(terminal),
            },
        },
    )
    monkeypatch.setattr(
        devstream_session, "SUPERVISOR_STOP_FILE", tmp_path / "supervisor.stop"
    )
    monkeypatch.setattr(
        devstream_session, "SUPERVISOR_PID_FILE", tmp_path / "supervisor.pid"
    )
    monkeypatch.setattr(
        devstream_session,
        "SUPERVISOR_STATUS_FILE",
        tmp_path / "supervisor-status.json",
    )
    monkeypatch.setattr(devstream_session, "load_env_files", lambda: {"PS_USERNAME": "bot"})
    monkeypatch.setattr(devstream_session, "prepare_runtime_env", lambda env: env)
    monkeypatch.setattr(
        devstream_session,
        "write_pid_value",
        lambda *args, **kwargs: (_ for _ in ()).throw(
            AssertionError("completed restart must not write a supervisor PID")
        ),
    )
    monkeypatch.setattr(
        devstream_session, "write_supervisor_status",
        lambda payload: statuses.append(json.loads(json.dumps(payload))),
    )
    args = argparse.Namespace(
        run_count=30,
        max_concurrent_battles=3,
        queue_timeout_seconds=180,
        sleep_seconds=15,
        max_cycles=1,
        autoresearch_count=30,
        proof_timeout_seconds=300,
        start_timeout_seconds=60,
        improve_timeout_seconds=240,
        skip_improve=True,
        enable_auto_improve=False,
        runtime_lease=str(runtime_lease),
    )

    assert devstream_session.cmd_supervise(args) == 0

    assert statuses[-1]["state"] == "completed-max-cycles"
    assert statuses[-1]["completedLearningCycles"] == 1
    assert statuses[-1]["latestBrokerReservation"]["reservationId"] == (
        terminal["reservationId"]
    )
    assert "launchNonce" not in json.dumps(statuses[-1]["latestBrokerReservation"])
    assert "authorizationDigest" not in json.dumps(
        statuses[-1]["latestBrokerReservation"]
    )


def test_cmd_supervise_restart_recovers_exact_dead_claim_without_capacity(
    tmp_path, monkeypatch
):
    statuses = []
    recovery_calls = []
    runtime_lease = install_supervisor_authority_fixture(
        monkeypatch, tmp_path, run_count=30
    )
    claimed = {
        "reservationId": "res-" + "7" * 32,
        "kind": "runtime",
        "purpose": devstream_session.RUNTIME_RESERVATION_PURPOSE,
        "battleCount": 30,
        "cycleCount": 1,
        "maxConcurrentBattles": 3,
        "supervisorProcessId": 2222,
        "supervisorProcessCreationFiletime": 133_801_234_567_890_000,
        "supervisorInstanceId": "supervisor-2222-prior",
        "state": "claimed",
        "claimedProcessId": 3333,
        "claimedProcessCreationFiletime": 133_801_234_567_891_000,
        "capacityReturned": False,
    }
    monkeypatch.setattr(
        devstream_session,
        "runtime_lease_consumption_status",
        lambda *args, **kwargs: {
            "ok": True,
            "blockers": [],
            "status": {
                "successfulCycleCount": 0,
                "remainingRunCount": 0,
                "remainingCycles": 0,
                "latestReservation": dict(claimed),
            },
        },
    )
    monkeypatch.setattr(
        devstream_session,
        "runtime_reservation_status",
        lambda lease_guard, reservation: {
            "ok": reservation["reservationId"] == claimed["reservationId"],
            "blockers": [],
            "status": dict(claimed),
        },
    )

    def recover(lease_guard, reservation):
        recovery_calls.append((lease_guard, dict(reservation)))
        return {
            "ok": True,
            "completed": True,
            "status": {
                **claimed,
                "state": "completed",
                "outcome": "abandoned",
                "completionActor": "administrator",
                "capacityReturned": False,
                "recovered": True,
            },
            "outcome": "abandoned",
            "capacityReturned": False,
        }

    monkeypatch.setattr(
        devstream_session, "recover_runtime_lease_consumption", recover
    )
    monkeypatch.setattr(
        devstream_session,
        "reserve_runtime_lease_consumption",
        lambda *args, **kwargs: (_ for _ in ()).throw(
            AssertionError("orphan recovery must not reserve more capacity")
        ),
    )
    monkeypatch.setattr(
        devstream_session, "SUPERVISOR_STOP_FILE", tmp_path / "supervisor.stop"
    )
    monkeypatch.setattr(
        devstream_session, "SUPERVISOR_PID_FILE", tmp_path / "supervisor.pid"
    )
    monkeypatch.setattr(
        devstream_session,
        "SUPERVISOR_STATUS_FILE",
        tmp_path / "supervisor-status.json",
    )
    monkeypatch.setattr(devstream_session, "load_env_files", lambda: {"PS_USERNAME": "bot"})
    monkeypatch.setattr(devstream_session, "prepare_runtime_env", lambda env: env)
    monkeypatch.setattr(devstream_session, "write_pid_value", lambda *args, **kwargs: None)
    monkeypatch.setattr(
        devstream_session,
        "supervisor_runtime_state",
        lambda: {
            "activeBattleCount": 0,
            "battleRunnerAlive": False,
            "inFlight": False,
        },
    )
    monkeypatch.setattr(
        devstream_session,
        "idle_battle_runner_recovery_candidate",
        lambda stale_after_seconds: {"shouldRecover": False},
    )
    monkeypatch.setattr(
        devstream_session, "write_supervisor_status",
        lambda payload: statuses.append(json.loads(json.dumps(payload))),
    )
    args = argparse.Namespace(
        run_count=30,
        max_concurrent_battles=3,
        queue_timeout_seconds=180,
        sleep_seconds=15,
        max_cycles=1,
        autoresearch_count=30,
        proof_timeout_seconds=300,
        start_timeout_seconds=60,
        improve_timeout_seconds=240,
        skip_improve=True,
        enable_auto_improve=False,
        runtime_lease=str(runtime_lease),
    )

    assert devstream_session.cmd_supervise(args) == 0

    assert len(recovery_calls) == 1
    recovered_binding = recovery_calls[0][1]
    assert recovered_binding["reservationId"] == claimed["reservationId"]
    assert recovered_binding["supervisorInstanceId"] == (
        claimed["supervisorInstanceId"]
    )
    assert recovered_binding["battleCount"] == 30
    assert "launchNonce" not in recovered_binding
    assert "authorizationDigest" not in recovered_binding
    assert statuses[-1]["state"] == "completed-lease-consumption"
    assert statuses[-1]["lastCycle"]["state"] == "reconciled-orphaned-runtime"
    assert statuses[-1]["lastCycle"]["leaseConsumptionTerminal"]["outcome"] == (
        "abandoned"
    )


def test_cmd_supervise_restart_fails_closed_on_ambiguous_broker_state(
    tmp_path, monkeypatch
):
    statuses = []
    runtime_lease = install_supervisor_authority_fixture(
        monkeypatch, tmp_path, run_count=30
    )
    monkeypatch.setattr(
        devstream_session,
        "runtime_lease_consumption_status",
        lambda *args, **kwargs: {
            "ok": False,
            "blockers": ["lease broker runtime workload binding does not match"],
        },
    )
    monkeypatch.setattr(
        devstream_session, "SUPERVISOR_STATUS_FILE",
        tmp_path / "supervisor-status.json",
    )
    monkeypatch.setattr(devstream_session, "load_env_files", lambda: {"PS_USERNAME": "bot"})
    monkeypatch.setattr(devstream_session, "prepare_runtime_env", lambda env: env)
    monkeypatch.setattr(
        devstream_session,
        "write_pid_value",
        lambda *args, **kwargs: (_ for _ in ()).throw(
            AssertionError("ambiguous broker state must block before PID ownership")
        ),
    )
    monkeypatch.setattr(
        devstream_session, "write_supervisor_status",
        lambda payload: statuses.append(json.loads(json.dumps(payload))),
    )
    args = argparse.Namespace(
        run_count=30,
        max_concurrent_battles=3,
        queue_timeout_seconds=180,
        sleep_seconds=15,
        max_cycles=1,
        autoresearch_count=30,
        proof_timeout_seconds=300,
        start_timeout_seconds=60,
        improve_timeout_seconds=240,
        skip_improve=True,
        enable_auto_improve=False,
        runtime_lease=str(runtime_lease),
    )

    assert devstream_session.cmd_supervise(args) == 2
    assert statuses[-1]["state"] == "blocked-lease-consumption-reconciliation"
    assert "workload binding does not match" in statuses[-1]["error"]
def test_cmd_supervise_does_not_start_next_after_final_stale_idle_recovery(tmp_path, monkeypatch):
    statuses = []
    start_next_flags = []
    runtime_lease = install_supervisor_authority_fixture(monkeypatch, tmp_path, run_count=1)
    runtime_states = iter(
        [
            {"activeBattleCount": 0, "battleRunnerAlive": False, "inFlight": False},
            {"activeBattleCount": 1, "battleRunnerAlive": True, "inFlight": True},
            {"activeBattleCount": 0, "battleRunnerAlive": True, "inFlight": True},
        ]
    )
    idle_recovery_states = iter(
        [
            {"shouldRecover": False},
            {"shouldRecover": False},
            {"shouldRecover": True},
        ]
    )

    monkeypatch.setattr(devstream_session, "SUPERVISOR_STOP_FILE", tmp_path / "supervisor.stop")
    monkeypatch.setattr(devstream_session, "SUPERVISOR_PID_FILE", tmp_path / "supervisor.pid")
    monkeypatch.setattr(devstream_session, "SUPERVISOR_STATUS_FILE", tmp_path / "supervisor-status.json")
    monkeypatch.setattr(devstream_session, "load_env_files", lambda: {"PS_USERNAME": "bot"})
    monkeypatch.setattr(devstream_session, "prepare_runtime_env", lambda env: env)
    monkeypatch.setattr(devstream_session, "write_pid_value", lambda *args, **kwargs: None)
    monkeypatch.setattr(devstream_session.time, "sleep", lambda seconds: None)
    monkeypatch.setattr(devstream_session, "supervisor_runtime_state", lambda: next(runtime_states))
    monkeypatch.setattr(
        devstream_session,
        "idle_battle_runner_recovery_candidate",
        lambda stale_after_seconds: next(idle_recovery_states),
    )

    def fake_write_status(payload):
        statuses.append(json.loads(json.dumps(payload)))

    def fake_cycle(
        args,
        cycle_index,
        *,
        start_next=True,
        authority_ok=True,
        lease_guard=None,
        supervisor_instance_id=None,
        reservation_state=None,
    ):
        assert authority_ok is True
        assert lease_guard["lease"]["id"] == "lease-test"
        assert supervisor_instance_id
        start_next_flags.append(start_next)
        if cycle_index == 1:
            reservation_state.update(
                {
                    "reservationId": "res-" + "1" * 32,
                    "kind": "runtime",
                    "purpose": devstream_session.RUNTIME_RESERVATION_PURPOSE,
                    "battleCount": 1,
                    "cycleCount": 1,
                    "maxConcurrentBattles": 3,
                    "supervisorProcessId": os.getpid(),
                    "supervisorProcessCreationFiletime": 133_801_234_567_890_000,
                    "supervisorInstanceId": supervisor_instance_id,
                    "launchNonce": "9" * 64,
                }
            )
            return {
                "state": "idle-restoring-runtime",
                "proofRefreshed": True,
                "battleRunnerAliveAfter": True,
                "activeBattleCountAfter": 0,
                "leaseConsumptionReservation": {
                    "ok": True,
                    "reserved": True,
                    "reservation": {
                        "reservationId": "lease-test-reservation-1",
                        "runtimeLeaseId": "lease-test",
                        "supervisorInstanceId": supervisor_instance_id,
                        "runCount": 1,
                    },
                },
            }
        if cycle_index == 2:
            return {
                "state": "battle-cycle-in-flight",
                "proofRefreshed": False,
            }
        if cycle_index == 3:
            reservation_state.clear()
            return {
                "state": "idle-restoring-runtime",
                "proofRefreshed": True,
                "staleBattleRuntimeRecovery": {"recovered": True},
                "battleRunnerAliveAfter": False,
                "activeBattleCountAfter": 0,
                "leaseConsumptionTerminal": {
                    "state": "completed",
                    "outcome": "completed",
                    "reservationId": "res-" + "1" * 32,
                },
            }
        raise AssertionError("supervisor should stop after stale idle runner completes the final cycle")

    monkeypatch.setattr(devstream_session, "write_supervisor_status", fake_write_status)
    monkeypatch.setattr(devstream_session, "run_supervisor_cycle", fake_cycle)

    args = argparse.Namespace(
        run_count=1,
        max_concurrent_battles=3,
        queue_timeout_seconds=180,
        sleep_seconds=15,
        max_cycles=1,
        autoresearch_count=30,
        proof_timeout_seconds=300,
        start_timeout_seconds=60,
        improve_timeout_seconds=240,
        skip_improve=True,
        enable_auto_improve=False,
        runtime_lease=str(runtime_lease),
    )

    assert devstream_session.cmd_supervise(args) == 0

    assert start_next_flags == [True, True, False]
    final = statuses[-1]
    assert final["state"] == "completed-max-cycles"
    assert final["completedLearningCycles"] == 1
    assert final["lastCycle"]["learningCycleCompleted"] is True
    assert final["lastCycle"]["staleBattleRuntimeRecovery"]["recovered"] is True


def test_supervisor_process_identity_requires_supervise_subcommand():
    tokens = devstream_session._command_expected_tokens(
        ["python", "scripts/devstream_session.py", "supervise", "--run-count", "25"]
    )

    assert "devstream_session.py" in tokens
    assert "supervise" in tokens
