import sys
from pathlib import Path
from datetime import datetime, timezone

ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = ROOT / "scripts"
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))

from scripts import (  # noqa: E402
    devstream_health,
    devstream_runtime_lease,
    devstream_session,
)
from tests.runtime_authority_testkit import sign_test_runtime_lease  # noqa: E402


def test_health_groups_parent_child_battle_runner_as_one_logical_runner():
    groups = devstream_health.logical_battle_runner_groups(
        [
            {"pid": 100, "parentPid": 0, "pidFile": ".pids/devstream_battle_session.pid"},
            {"pid": 101, "parentPid": 100, "pidFile": ".bot.pid"},
        ]
    )

    assert len(groups) == 1
    assert groups[0]["rootPid"] == 100
    assert groups[0]["memberPids"] == [100, 101]


def test_health_keeps_independent_battle_runners_separate():
    groups = devstream_health.logical_battle_runner_groups(
        [
            {"pid": 100, "parentPid": 0, "pidFile": ".pids/devstream_battle_session.pid"},
            {"pid": 200, "parentPid": 0, "pidFile": ".bot.pid"},
        ]
    )

    assert len(groups) == 2
    assert [group["rootPid"] for group in groups] == [100, 200]


def test_session_groups_parent_child_battle_runner_as_one_logical_runner():
    groups = devstream_session._logical_battle_runner_groups(
        [
            {"pid": 100, "parentPid": 0, "pidFile": ".pids/devstream_battle_session.pid"},
            {"pid": 101, "parentPid": 100, "pidFile": ".bot.pid"},
        ]
    )

    assert len(groups) == 1
    assert groups[0]["rootPid"] == 100
    assert groups[0]["memberPids"] == [100, 101]


def test_supervisor_command_defaults_to_skip_improve():
    command = devstream_session.supervisor_command(
        run_count=30,
        max_concurrent=1,
        queue_timeout_seconds=180,
        sleep_seconds=20,
    )

    assert "--skip-improve" in command
    assert "--enable-auto-improve" not in command


def test_supervisor_command_explicit_auto_improve_opt_in():
    command = devstream_session.supervisor_command(
        run_count=30,
        max_concurrent=1,
        queue_timeout_seconds=180,
        sleep_seconds=20,
        enable_auto_improve=True,
    )

    assert "--enable-auto-improve" in command
    assert "--skip-improve" not in command


def test_idle_runner_recovery_candidate_blocks_when_battle_active(monkeypatch):
    monkeypatch.setattr(devstream_session, "read_active_battles", lambda: 1)
    monkeypatch.setattr(devstream_session, "battle_pid_files", lambda: [Path(".pids/devstream_battle_session.pid")])
    monkeypatch.setattr(devstream_session, "pid_alive", lambda path: (True, 123))
    monkeypatch.setattr(devstream_session, "process_age_seconds", lambda pid: 999)

    candidate = devstream_session.idle_battle_runner_recovery_candidate(stale_after_seconds=180)

    assert candidate["activeBattleCount"] == 1
    assert candidate["staleAliveCount"] == 1
    assert candidate["shouldRecover"] is False


def test_idle_runner_recovery_candidate_allows_stale_idle_runner(monkeypatch):
    monkeypatch.setattr(devstream_session, "read_active_battles", lambda: 0)
    monkeypatch.setattr(devstream_session, "battle_pid_files", lambda: [Path(".pids/devstream_battle_session.pid")])
    monkeypatch.setattr(devstream_session, "pid_alive", lambda path: (True, 123))
    monkeypatch.setattr(devstream_session, "process_age_seconds", lambda pid: 999)

    candidate = devstream_session.idle_battle_runner_recovery_candidate(stale_after_seconds=180)

    assert candidate["activeBattleCount"] == 0
    assert candidate["staleAliveCount"] == 1
    assert candidate["liveYoungCount"] == 0
    assert candidate["shouldRecover"] is True


def test_recovered_idle_runner_counts_as_completed_learning_cycle():
    assert devstream_session.learning_cycle_completed_after_cycle(
        battle_was_in_flight=True,
        pre_cycle_runtime={"inFlight": True},
        cycle={
            "proofRefreshed": True,
            "staleBattleRuntimeRecovery": {"recovered": True},
        },
    ) is True


def test_active_unrecovered_runner_does_not_count_as_completed_learning_cycle():
    assert devstream_session.learning_cycle_completed_after_cycle(
        battle_was_in_flight=True,
        pre_cycle_runtime={"inFlight": True},
        cycle={
            "proofRefreshed": False,
            "state": "battle-cycle-in-flight",
        },
    ) is False


def test_health_surface_expectation_uses_current_runtime_lease(tmp_path, monkeypatch):
    lease_path = tmp_path / "runtime-lease.json"
    now = datetime.now(timezone.utc)
    payload = devstream_runtime_lease.build_runtime_lease_artifact(
        purpose="devstream-supervise",
        machine="MIRAIDON",
        account="bot",
        run_count=1,
        max_cycles=1,
        max_concurrent_battles=1,
        replay_behavior="always",
        valid_minutes=10,
        source_commit="a" * 40,
        source_tree="b" * 40,
        change_id="change-health-test-0001",
        deployment_id="deployment-health-test-0001",
        runtime_manifest_digest="c" * 64,
        deployment_receipt_path=r"C:\ProgramData\HERMES\state\fouler\deployment-test.json",
        deployment_receipt_sha256="d" * 64,
        now=now,
    )
    lease_path.write_text(
        devstream_runtime_lease.json.dumps(sign_test_runtime_lease(payload)),
        encoding="utf-8",
    )
    monkeypatch.setattr(devstream_health, "RUNTIME_LEASE_PATH", lease_path)

    expectation = devstream_health.runtime_lease_surface_expectation()
    readiness = devstream_health.battle_surface_readiness(
        [
            {
                "relativePath": "active_battles.json",
                "summary": {"battleCount": 1, "maxSlots": 1},
            }
        ],
        {},
        check_http=False,
        http_open=False,
        expectation=expectation,
    )

    assert expectation["expected"] == 1
    assert expectation["source"] == "runtime-lease"
    assert readiness["expected"] == 1
    assert readiness["declaredMaxSlots"] == 1
    assert readiness["ready"] is True
