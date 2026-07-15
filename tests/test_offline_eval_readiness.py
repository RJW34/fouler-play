import json
import socket
from pathlib import Path

from infrastructure import offline_eval_readiness
from scripts import devstream_runtime_lease
from tests.runtime_authority_testkit import sign_test_runtime_lease


def _write_minimal_harness(root: Path) -> None:
    (root / "infrastructure").mkdir(parents=True, exist_ok=True)
    (root / "infrastructure" / "offline_eval.py").write_text("# eval harness\n", encoding="utf-8")
    (root / "infrastructure" / "_offline_baseline.py").write_text("# baseline\n", encoding="utf-8")
    (root / "infrastructure" / "requirements-eval.txt").write_text("poke-env\n", encoding="utf-8")
    team = root / "teams" / "gen9" / "ou" / "fat-team-1-stall"
    team.parent.mkdir(parents=True, exist_ok=True)
    team.write_text("Corviknight @ Leftovers\n", encoding="utf-8")


def _write_ready_eval_files(root: Path) -> None:
    _write_minimal_harness(root)
    venv_python = root / ".venv-eval" / "Scripts" / "python.exe"
    venv_python.parent.mkdir(parents=True, exist_ok=True)
    venv_python.write_text("# fake python for path proof\n", encoding="utf-8")
    frozen = root / "eval_results" / "offline" / "frozen.json"
    frozen.parent.mkdir(parents=True, exist_ok=True)
    frozen.write_text(
        json.dumps(
            {
                "label": "frozen",
                "battles": 200,
                "fouler_wins": 120,
                "fouler_win_rate": 0.6,
                "fouler_wilson_lcb": 0.53,
            }
        ),
        encoding="utf-8",
    )


def _write_result_artifacts(
    root: Path,
    *,
    battles: int = 200,
    accepted: bool = True,
    compare_battles: int | None = None,
) -> None:
    results = root / "eval_results" / "offline"
    results.mkdir(parents=True, exist_ok=True)
    candidate = {
        "label": "candidate",
        "team": "gen9/ou/fat-team-1-stall",
        "baseline": "simple",
        "battles": battles,
        "fouler_wins": 126,
        "baseline_wins": max(0, battles - 126),
        "fouler_win_rate": 0.63,
        "fouler_wilson_lcb": 0.56,
        "timestamp": "2026-06-14T00:00:00",
    }
    compare_candidate_battles = battles if compare_battles is None else compare_battles
    compare = {
        "frozen": {
            "label": "frozen",
            "fouler_wins": 100,
            "battles": 200,
            "fouler_win_rate": 0.5,
        },
        "candidate": {
            "label": "candidate",
            "fouler_wins": 126,
            "battles": compare_candidate_battles,
            "fouler_win_rate": 0.63,
        },
        "delta_win_rate": 0.13,
        "p_value": 0.02,
        "ACCEPT": accepted,
    }
    (results / "candidate.json").write_text(json.dumps(candidate), encoding="utf-8")
    (results / "compare-frozen-vs-candidate.json").write_text(json.dumps(compare), encoding="utf-8")


def _write_cleanup_lease(path: Path, purpose: str) -> Path:
    host_binding = devstream_runtime_lease.physical_host_binding()
    payload = {
        "schemaVersion": "fouler-play-runtime-lease/v3",
        "projectId": "fouler-play",
        "leaseId": "offline-cleanup-test",
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
        "account": "bot",
        "allowedPurposes": [purpose],
        "maxRunCount": 1,
        "maxCycles": 1,
        "maxConcurrentBattles": 1,
        "replayBehavior": "never",
        "battleScope": {
            **host_binding,
            "account": "bot",
            "runCount": 1,
            "maxRunCount": 1,
            "maxConcurrentBattles": 1,
            "replayBehavior": "never",
        },
        "cycleScope": {"maxCycles": 1},
        "proofWindow": {
            "startsAt": "2026-06-08T00:00:00+00:00",
            "expiresAt": "2099-01-01T00:00:00+00:00",
        },
    }
    payload = sign_test_runtime_lease(payload)
    path.write_text(json.dumps(payload), encoding="utf-8")
    return path


class _DeadPidPsutil:
    class NoSuchProcess(Exception):
        pass

    class AccessDenied(Exception):
        pass

    STATUS_ZOMBIE = "zombie"

    @staticmethod
    def Process(pid):
        raise _DeadPidPsutil.NoSuchProcess(pid)


class _RunningPidPsutil:
    class NoSuchProcess(Exception):
        pass

    class AccessDenied(Exception):
        pass

    STATUS_ZOMBIE = "zombie"

    class _Process:
        def __init__(self, pid):
            self.pid = pid

        def status(self):
            return "running"

        def is_running(self):
            return True

        def cwd(self):
            return "C:\\Users\\mtoli\\Documents\\Code\\fouler-play"

        def cmdline(self):
            return [".venv-eval\\Scripts\\python.exe", "infrastructure\\offline_eval.py", "--battles", "200"]

    @staticmethod
    def Process(pid):
        return _RunningPidPsutil._Process(pid)


def test_readiness_payload_reports_actionable_missing_harness(tmp_path):
    payload = offline_eval_readiness.build_readiness_payload(
        root=tmp_path,
        env={},
        run_import_check=False,
        run_prereq_check=False,
    )

    assert payload["recursiveImprovementReady"] is False
    assert any("offline_eval.py" in blocker for blocker in payload["blockers"])
    assert any("eval venv python" in blocker for blocker in payload["blockers"])
    assert "py -3 -m venv .venv-eval" in payload["provisionCommands"]["windows"]
    assert ".venv-eval\\Scripts\\python.exe -m pip install -r infrastructure\\requirements-eval.txt" in payload["provisionCommands"]["windows"]
    assert "python3 -m venv .venv-eval" in payload["provisionCommands"]["posix"]
    assert ".venv-eval/bin/python -m pip install -r infrastructure/requirements-eval.txt" in payload["provisionCommands"]["posix"]
    assert "--label candidate" in payload["commands"]["candidateEval"]
    assert "--compare frozen candidate" in payload["commands"]["compareFrozenVsCandidate"]
    assert "eval_results/offline/frozen.json" in json.dumps(payload["proofRequired"])


def test_readiness_payload_ready_with_eval_proof(tmp_path):
    _write_ready_eval_files(tmp_path)

    payload = offline_eval_readiness.build_readiness_payload(
        root=tmp_path,
        env={},
        run_import_check=False,
        run_server_check=False,
        run_prereq_check=False,
    )

    assert payload["recursiveImprovementReady"] is True
    assert payload["blockers"] == []
    assert payload["paths"]["frozenBaseline"] == "eval_results\\offline\\frozen.json"
    assert "infrastructure\\offline_eval.py" in payload["commands"]["candidateEval"]
    assert "--battles 200" in payload["commands"]["candidateEval"]
    assert "--search-time-ms 100" in payload["commands"]["candidateEval"]
    assert "--search-time-ms 100" in payload["commands"]["frozenBaseline"]
    assert "--manage-showdown-server" in payload["commands"]["candidateEval"]


def test_readiness_payload_honors_configured_eval_search_time(tmp_path):
    _write_ready_eval_files(tmp_path)

    payload = offline_eval_readiness.build_readiness_payload(
        root=tmp_path,
        env={"IMPROVE_AGENT_EVAL_SEARCH_TIME_MS": "50"},
        run_import_check=False,
        run_server_check=False,
        run_prereq_check=False,
    )

    assert payload["configuration"]["searchTimeMs"] == 50
    assert "--search-time-ms 50" in payload["commands"]["candidateEval"]
    assert "--search-time-ms 50" in payload["commands"]["frozenBaseline"]


def test_readiness_payload_honors_configured_eval_concurrency(tmp_path):
    _write_ready_eval_files(tmp_path)

    payload = offline_eval_readiness.build_readiness_payload(
        root=tmp_path,
        env={"IMPROVE_AGENT_EVAL_CONCURRENCY": "3"},
        run_import_check=False,
        run_server_check=False,
        run_prereq_check=False,
    )

    assert payload["configuration"]["concurrency"] == 3
    assert "--concurrency 3" in payload["commands"]["candidateEval"]
    assert "--concurrency 3" in payload["commands"]["frozenBaseline"]


def test_readiness_payload_can_disable_managed_showdown_command(tmp_path):
    _write_ready_eval_files(tmp_path)

    payload = offline_eval_readiness.build_readiness_payload(
        root=tmp_path,
        env={"IMPROVE_AGENT_EVAL_MANAGE_SHOWDOWN": "0"},
        run_import_check=False,
        run_server_check=False,
        run_prereq_check=False,
    )

    assert payload["configuration"]["manageShowdownServer"] is False
    assert "--manage-showdown-server" not in payload["commands"]["candidateEval"]


def test_offline_eval_result_proof_treats_legacy_acceptance_as_smoke_only(tmp_path):
    _write_result_artifacts(tmp_path, accepted=True)

    payload = offline_eval_readiness.offline_eval_result_proof(root=tmp_path, env={})

    assert payload["status"] == "smoke-passed"
    assert payload["ready"] is True
    assert payload["smokePassed"] is True
    assert payload["accepted"] is False
    assert payload["promotionEligible"] is False
    assert payload["verdict"] == "passed"
    assert payload["candidateBattles"] == 200
    assert payload["requiredBattles"] == 200
    assert payload["missingPaths"] == []
    assert payload["artifacts"]["compare"]["accepted"] is False
    assert payload["artifacts"]["compare"]["legacyReportedAcceptance"] is True


def test_offline_eval_result_proof_reports_failed_smoke_artifacts(tmp_path):
    _write_result_artifacts(tmp_path, accepted=False)

    payload = offline_eval_readiness.offline_eval_result_proof(root=tmp_path, env={})

    assert payload["status"] == "smoke-failed"
    assert payload["ready"] is False
    assert payload["accepted"] is False
    assert payload["verdict"] == "failed"
    assert "weak-baseline transport or gross-regression smoke failed" in payload["reasons"]


def test_offline_eval_result_proof_prefers_explicit_smoke_over_forced_accept_false(tmp_path):
    _write_result_artifacts(tmp_path, accepted=False)
    compare_path = tmp_path / "eval_results" / "offline" / "compare-frozen-vs-candidate.json"
    compare = json.loads(compare_path.read_text(encoding="utf-8"))
    compare["smoke_passed"] = True
    compare["promotion_eligible"] = False
    compare_path.write_text(json.dumps(compare), encoding="utf-8")

    payload = offline_eval_readiness.offline_eval_result_proof(root=tmp_path, env={})

    assert payload["status"] == "smoke-passed"
    assert payload["ready"] is True
    assert payload["accepted"] is False
    assert payload["artifacts"]["compare"]["verdictSource"] == "smoke_passed"


def test_offline_eval_result_proof_reports_missing_artifacts(tmp_path):
    payload = offline_eval_readiness.offline_eval_result_proof(root=tmp_path, env={})

    assert payload["status"] == "missing"
    assert payload["ready"] is False
    assert payload["accepted"] is False
    missing_paths = [path.replace("\\", "/") for path in payload["missingPaths"]]
    assert "eval_results/offline/candidate.json" in missing_paths
    assert "eval_results/offline/compare-frozen-vs-candidate.json" in missing_paths


def test_offline_eval_result_proof_reports_insufficient_candidate_battles(tmp_path):
    _write_result_artifacts(tmp_path, battles=20, accepted=True)

    payload = offline_eval_readiness.offline_eval_result_proof(root=tmp_path, env={})

    assert payload["status"] == "insufficient"
    assert payload["ready"] is False
    assert payload["accepted"] is False
    assert payload["candidateBattles"] == 20
    assert payload["requiredBattles"] == 200
    assert any("below required 200" in reason for reason in payload["reasons"])


def test_offline_eval_result_proof_reports_stale_compare_artifact(tmp_path):
    _write_result_artifacts(tmp_path, battles=200, accepted=True, compare_battles=199)

    payload = offline_eval_readiness.offline_eval_result_proof(root=tmp_path, env={})

    assert payload["status"] == "stale"
    assert payload["ready"] is False
    assert payload["accepted"] is False
    assert payload["candidateBattles"] == 200
    assert any("does not match candidate.json" in reason for reason in payload["staleReasons"])


def test_readiness_payload_classifies_stale_bot_pid_as_cleanup_safe(tmp_path, monkeypatch):
    _write_ready_eval_files(tmp_path)
    (tmp_path / ".bot.pid").write_text("999999", encoding="utf-8")
    monkeypatch.setattr(offline_eval_readiness, "psutil", _DeadPidPsutil)

    payload = offline_eval_readiness.build_readiness_payload(
        root=tmp_path,
        env={},
        run_import_check=False,
        run_server_check=False,
        run_prereq_check=False,
    )

    pid_check = next(check for check in payload["checks"] if check["name"] == "fouler bot pid lock")
    assert payload["recursiveImprovementReady"] is True
    assert pid_check["ok"] is True
    assert pid_check["detail"]["state"] == "stale-dead-pid"
    assert pid_check["detail"]["cleanupRecommended"] is True


def test_readiness_payload_blocks_stale_running_eval_status_with_dead_server_pid(tmp_path, monkeypatch):
    _write_ready_eval_files(tmp_path)
    status = tmp_path / "eval_results" / "offline" / "frozen-200-status.json"
    status.write_text(
        json.dumps(
            {
                "serverPid": 29192,
                "serverStopped": False,
                "stage": "running-frozen-eval",
                "updatedAt": "2026-06-08T05:18:44-04:00",
            }
        ),
        encoding="utf-16",
    )
    monkeypatch.setattr(offline_eval_readiness, "psutil", _DeadPidPsutil)

    payload = offline_eval_readiness.build_readiness_payload(
        root=tmp_path,
        env={},
        run_import_check=False,
        run_server_check=False,
        run_prereq_check=False,
    )

    status_check = next(check for check in payload["checks"] if check["name"] == "offline eval status artifacts")
    assert payload["recursiveImprovementReady"] is False
    assert status_check["ok"] is False
    assert status_check["detail"]["staleRunning"][0]["name"] == "frozen-200-status.json"
    assert status_check["detail"]["staleRunning"][0]["serverProcess"]["status"] == "dead"
    assert status_check["detail"]["staleRunning"][0]["disposition"]["state"] == "blocked"
    assert status_check["detail"]["staleRunning"][0]["disposition"]["classification"] == "blocked-stale-running-offline-eval-status"
    assert "explicit positive --battles bound" in status_check["detail"]["finiteLeasePreconditions"][0]
    assert any("offline eval status artifacts" in blocker for blocker in payload["blockers"])


def test_dead_offline_eval_status_cleanup_fails_closed_without_runtime_lease(tmp_path, monkeypatch):
    _write_ready_eval_files(tmp_path)
    status = tmp_path / "eval_results" / "offline" / "candidate-status.json"
    status.write_text(
        json.dumps({"serverPid": 29192, "serverStopped": False, "stage": "running-candidate-eval"}),
        encoding="utf-8",
    )
    monkeypatch.setattr(offline_eval_readiness, "psutil", _DeadPidPsutil)

    payload = offline_eval_readiness.archive_dead_offline_eval_status_artifacts(
        root=tmp_path,
        env={},
        execute=True,
        runtime_lease=tmp_path / "missing-runtime-lease.json",
    )

    assert payload["status"] == "blocked-runtime-lease"
    assert "runtime lease file is missing" in " ".join(payload["runtimeLease"]["blockers"])
    assert status.exists()
    assert payload["archivedCount"] == 0


def test_dead_offline_eval_status_cleanup_dry_run_keeps_status_file(tmp_path, monkeypatch):
    _write_ready_eval_files(tmp_path)
    status = tmp_path / "eval_results" / "offline" / "candidate-status.json"
    status.write_text(
        json.dumps({"serverPid": 29192, "serverStopped": False, "stage": "running-candidate-eval"}),
        encoding="utf-8",
    )
    monkeypatch.setattr(offline_eval_readiness, "psutil", _DeadPidPsutil)
    runtime_lease = _write_cleanup_lease(
        tmp_path / "runtime-lease.json",
        offline_eval_readiness.OFFLINE_STATUS_CLEANUP_DRY_RUN_PURPOSE,
    )

    payload = offline_eval_readiness.archive_dead_offline_eval_status_artifacts(
        root=tmp_path,
        env={},
        execute=False,
        runtime_lease=runtime_lease,
    )

    assert payload["dryRun"] is True
    assert payload["runtimeLease"]["ok"] is True
    assert payload["reason"] == "dry run; dead offline eval status cleanup planned only"
    assert payload["deadStatusArtifacts"][0]["name"] == "candidate-status.json"
    assert status.exists()
    assert payload["archivedCount"] == 0


def test_dead_offline_eval_status_cleanup_archives_after_valid_lease(tmp_path, monkeypatch):
    _write_ready_eval_files(tmp_path)
    status = tmp_path / "eval_results" / "offline" / "candidate-status.json"
    status.write_text(
        json.dumps({"serverPid": 29192, "serverStopped": False, "stage": "running-candidate-eval"}),
        encoding="utf-8",
    )
    monkeypatch.setattr(offline_eval_readiness, "psutil", _DeadPidPsutil)
    runtime_lease = _write_cleanup_lease(
        tmp_path / "runtime-lease.json",
        offline_eval_readiness.OFFLINE_STATUS_CLEANUP_PURPOSE,
    )

    payload = offline_eval_readiness.archive_dead_offline_eval_status_artifacts(
        root=tmp_path,
        env={},
        execute=True,
        runtime_lease=runtime_lease,
    )

    archived = payload["archived"][0]
    assert payload["runtimeLease"]["ok"] is True
    assert payload["archivedCount"] == 1
    assert archived["archived"] is True
    assert not status.exists()
    assert Path(archived["archivePath"]).exists()

    readiness = offline_eval_readiness.build_readiness_payload(
        root=tmp_path,
        env={},
        run_import_check=False,
        run_server_check=False,
        run_prereq_check=False,
    )
    status_check = next(check for check in readiness["checks"] if check["name"] == "offline eval status artifacts")
    assert status_check["ok"] is True


def test_readiness_payload_adopts_live_running_eval_status_without_starting_another(tmp_path, monkeypatch):
    _write_ready_eval_files(tmp_path)
    status = tmp_path / "eval_results" / "offline" / "candidate-status.json"
    status.write_text(
        json.dumps(
            {
                "serverPid": 29192,
                "serverStopped": False,
                "stage": "running-candidate-eval",
                "updatedAt": "2026-06-08T05:18:44-04:00",
            }
        ),
        encoding="utf-8",
    )
    monkeypatch.setattr(offline_eval_readiness, "psutil", _RunningPidPsutil)

    payload = offline_eval_readiness.build_readiness_payload(
        root=tmp_path,
        env={},
        run_import_check=False,
        run_server_check=False,
        run_prereq_check=False,
    )

    status_check = next(check for check in payload["checks"] if check["name"] == "offline eval status artifacts")
    assert payload["recursiveImprovementReady"] is False
    assert status_check["ok"] is False
    assert status_check["detail"]["adoptedRunning"][0]["name"] == "candidate-status.json"
    assert status_check["detail"]["adoptedRunning"][0]["disposition"]["state"] == "adopted"
    assert status_check["detail"]["adoptedRunning"][0]["disposition"]["classification"] == "adopted-running-offline-eval"
    assert "read-only" in status_check["detail"]["finiteLeasePreconditions"][-1]


def test_readiness_payload_honors_eval_env_overrides(tmp_path):
    _write_minimal_harness(tmp_path)
    env = {
        "IMPROVE_AGENT_EVAL_BATTLES": "40",
        "IMPROVE_AGENT_EVAL_TEAM": "gen9/ou/fat-team-1-stall",
        "IMPROVE_AGENT_EVAL_BASELINE": "maxbp",
        "IMPROVE_AGENT_EVAL_CONCURRENCY": "2",
        "EVAL_SHOWDOWN_PORT": "9876",
    }

    payload = offline_eval_readiness.build_readiness_payload(
        root=tmp_path,
        env=env,
        run_import_check=False,
        run_server_check=False,
        run_prereq_check=False,
    )

    assert payload["configuration"]["battles"] == 40
    assert payload["configuration"]["baseline"] == "maxbp"
    assert payload["configuration"]["concurrency"] == 2
    assert payload["configuration"]["showdownPort"] == 9876
    assert "--battles 40" in payload["commands"]["candidateEval"]
    assert "--baseline maxbp" in payload["commands"]["candidateEval"]
    assert "--concurrency 2" in payload["commands"]["candidateEval"]
    assert payload["commands"]["showdownServer"].endswith("9876")


def test_readiness_payload_reports_closed_showdown_eval_port(tmp_path):
    _write_minimal_harness(tmp_path)
    venv_python = tmp_path / ".venv-eval" / "Scripts" / "python.exe"
    venv_python.parent.mkdir(parents=True, exist_ok=True)
    venv_python.write_text("# fake python for path proof\n", encoding="utf-8")
    frozen = tmp_path / "eval_results" / "offline" / "frozen.json"
    frozen.parent.mkdir(parents=True, exist_ok=True)
    frozen.write_text(
        json.dumps(
            {
                "label": "frozen",
                "battles": 200,
                "fouler_wins": 120,
                "fouler_win_rate": 0.6,
                "fouler_wilson_lcb": 0.53,
            }
        ),
        encoding="utf-8",
    )
    probe = socket.socket()
    probe.bind(("127.0.0.1", 0))
    port = probe.getsockname()[1]
    probe.close()

    payload = offline_eval_readiness.build_readiness_payload(
        root=tmp_path,
        env={"EVAL_SHOWDOWN_PORT": str(port), "IMPROVE_AGENT_EVAL_MANAGE_SHOWDOWN": "0"},
        run_import_check=False,
        run_prereq_check=False,
    )

    assert payload["recursiveImprovementReady"] is False
    server_check = next(check for check in payload["checks"] if check["name"] == "local showdown eval server")
    assert server_check["ok"] is False
    assert server_check["detail"]["port"] == port
    assert f"node pokemon-showdown --no-security start {port}" in server_check["remediation"]


def test_readiness_payload_accepts_managed_showdown_when_port_closed(tmp_path):
    _write_ready_eval_files(tmp_path)
    probe = socket.socket()
    probe.bind(("127.0.0.1", 0))
    port = probe.getsockname()[1]
    probe.close()

    payload = offline_eval_readiness.build_readiness_payload(
        root=tmp_path,
        env={"EVAL_SHOWDOWN_PORT": str(port)},
        run_import_check=False,
        run_prereq_check=False,
    )

    server_check = next(check for check in payload["checks"] if check["name"] == "local showdown eval server")
    assert payload["recursiveImprovementReady"] is True
    assert server_check["ok"] is True
    assert server_check["detail"]["status"] == "managed-start-on-demand"


def test_readiness_payload_reports_showdown_dependency_gap(tmp_path, monkeypatch):
    _write_minimal_harness(tmp_path)
    venv_python = tmp_path / ".venv-eval" / "Scripts" / "python.exe"
    venv_python.parent.mkdir(parents=True, exist_ok=True)
    venv_python.write_text("# fake python for path proof\n", encoding="utf-8")
    frozen = tmp_path / "eval_results" / "offline" / "frozen.json"
    frozen.parent.mkdir(parents=True, exist_ok=True)
    frozen.write_text(
        json.dumps(
            {
                "label": "frozen",
                "battles": 200,
                "fouler_wins": 120,
                "fouler_win_rate": 0.6,
                "fouler_wilson_lcb": 0.53,
            }
        ),
        encoding="utf-8",
    )
    showdown = tmp_path / "pokemon-showdown"
    showdown.mkdir()
    (showdown / "pokemon-showdown").write_text("# launcher\n", encoding="utf-8")
    (showdown / "package.json").write_text(
        json.dumps({"name": "pokemon-showdown", "version": "0.0.0-test", "engines": {"node": ">=16.0.0"}}),
        encoding="utf-8",
    )

    def fake_which(command):
        return f"C:\\tools\\{command}.exe"

    class FakeProbe:
        returncode = 0
        stdout = "v20.0.0\n"
        stderr = ""

    def fake_run(*args, **kwargs):
        return FakeProbe()

    monkeypatch.setattr(offline_eval_readiness.shutil, "which", fake_which)
    monkeypatch.setattr(offline_eval_readiness.subprocess, "run", fake_run)

    payload = offline_eval_readiness.build_readiness_payload(
        root=tmp_path,
        env={"POKEMON_SHOWDOWN_DIR": str(showdown)},
        run_import_check=False,
        run_server_check=False,
    )

    deps_check = next(check for check in payload["checks"] if check["name"] == "pokemon-showdown dependencies")
    assert deps_check["ok"] is False
    assert deps_check["detail"]["nodeModulesExists"] is False
    assert "npm ci" in deps_check["remediation"]
    assert any("pokemon-showdown dependencies" in blocker for blocker in payload["blockers"])
    assert str(showdown) in payload["commands"]["showdownServerCwd"]


def test_readiness_payload_reports_fouler_runtime_gap(tmp_path, monkeypatch):
    _write_minimal_harness(tmp_path)
    venv_python = tmp_path / ".venv-eval" / "Scripts" / "python.exe"
    venv_python.parent.mkdir(parents=True, exist_ok=True)
    venv_python.write_text("# fake python for path proof\n", encoding="utf-8")
    frozen = tmp_path / "eval_results" / "offline" / "frozen.json"
    frozen.parent.mkdir(parents=True, exist_ok=True)
    frozen.write_text(
        json.dumps(
            {
                "label": "frozen",
                "battles": 200,
                "fouler_wins": 120,
                "fouler_win_rate": 0.6,
                "fouler_wilson_lcb": 0.53,
            }
        ),
        encoding="utf-8",
    )

    monkeypatch.setattr(
        offline_eval_readiness,
        "_check_imports",
        lambda _venv_python: (True, {"poke_env": "test", "websockets": "test"}),
    )
    monkeypatch.setattr(
        offline_eval_readiness,
        "_check_fouler_runtime_imports",
        lambda _root, _env: (
            False,
            {
                "requiredImports": ["aiohttp"],
                "failures": [{"command": "python", "stderr": "No module named aiohttp"}],
            },
        ),
    )

    payload = offline_eval_readiness.build_readiness_payload(
        root=tmp_path,
        env={},
        run_server_check=False,
        run_prereq_check=False,
    )

    runtime_check = next(check for check in payload["checks"] if check["name"] == "fouler runtime imports")
    assert runtime_check["ok"] is False
    assert any("fouler runtime imports" in blocker for blocker in payload["blockers"])


def test_windows_executable_probe_does_not_invoke_version_by_default(monkeypatch):
    calls = []

    monkeypatch.setattr(offline_eval_readiness.os, "name", "nt")
    monkeypatch.delenv("FOULER_OFFLINE_READINESS_VERSION_PROBE", raising=False)
    monkeypatch.setattr(offline_eval_readiness.shutil, "which", lambda command: f"C:\\tools\\{command}.exe")
    monkeypatch.setattr(
        offline_eval_readiness.subprocess,
        "run",
        lambda *args, **kwargs: calls.append((args, kwargs)),
    )

    ok, detail = offline_eval_readiness._check_executable("node")

    assert ok is True
    assert detail["found"] is True
    assert detail["versionProbeSkipped"] is True
    assert calls == []
