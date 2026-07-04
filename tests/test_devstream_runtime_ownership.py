import sys
from pathlib import Path
from datetime import datetime, timedelta, timezone

ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = ROOT / "scripts"
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))

from scripts import devstream_health, devstream_session


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
    lease_path.write_text(
        """
{
  "projectId": "fouler-play",
  "leaseId": "test-lease",
  "status": "active",
  "maxConcurrentBattles": 1,
  "proofWindow": {
    "startsAt": "%s",
    "expiresAt": "%s"
  }
}
""".strip()
        % (
            now.isoformat(),
            (now + timedelta(minutes=10)).isoformat(),
        ),
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
