import hashlib
import copy
import json
import subprocess
from datetime import datetime, timedelta, timezone

import pytest

from infrastructure import deployment_lineage as lineage
from infrastructure import deployment_state as state
from infrastructure import elo_watchdog
from infrastructure import runtime_authorization
from scripts import fouler_deployment_state as state_cli
from tests.runtime_authority_testkit import sign_test_runtime_lease


def make_release(path):
    subprocess.run(["git", "init", "-q", str(path)], check=True)
    subprocess.run(["git", "-C", str(path), "config", "user.email", "proof@test.invalid"], check=True)
    subprocess.run(["git", "-C", str(path), "config", "user.name", "Proof Test"], check=True)
    (path / "fp" / "search").mkdir(parents=True)
    (path / "fp" / "search" / "main.py").write_text("VALUE = 1\n", encoding="utf-8")
    (path / "run.py").write_text("print('runtime')\n", encoding="utf-8")
    subprocess.run(["git", "-C", str(path), "add", "-A"], check=True)
    subprocess.run(["git", "-C", str(path), "commit", "-qm", "release"], check=True)
    return path


def make_bundle(tmp_path, *, battle_count=1, baseline=None, results=None):
    root = make_release(tmp_path / "release")
    deployment = lineage.build_deployment_receipt(
        root=root,
        machine="JIGGLYPUFF",
        change_id="owner-pilot-release-0001",
        authorization_type="owner-approved-release",
        approval_ref="codex-goal-owner-authorization",
    )
    deployment_path = tmp_path / "authority" / "deployment.json"
    lineage.write_immutable_receipt(deployment_path, deployment)
    deployment_sha = hashlib.sha256(deployment_path.read_bytes()).hexdigest()
    lease = {
        "schemaVersion": "fouler-play-runtime-lease/v3",
        "projectId": "fouler-play",
        "leaseId": "lease-pilot-0001",
        "sourceCommit": deployment["sourceCommit"],
        "sourceTree": deployment["sourceTree"],
        "runtimeManifestDigest": deployment["runtimeManifestDigest"],
        "changeId": deployment["changeId"],
        "deploymentId": deployment["deploymentId"],
        "deploymentReceiptPath": str(deployment_path.resolve()),
        "deploymentReceiptSha256": deployment_sha,
        "sessionId": "session-pilot-0001",
        "status": "active",
        "approved": True,
        "machine": "JIGGLYPUFF",
        "account": "DekuFoulerLab",
        "allowedPurposes": ["jigglypuff-runtime-start", "deployment-activation"],
        "maxRunCount": 100,
        "maxCycles": 10,
        "maxConcurrentBattles": 3,
        "replayBehavior": "save",
        "proofWindow": {
            "startsAt": "2026-01-01T00:00:00+00:00",
            "expiresAt": "2099-01-01T00:00:00+00:00",
        },
    }
    lease = sign_test_runtime_lease(lease)
    lease_path = tmp_path / "authority" / "runtime-lease.json"
    lease_path.write_text(json.dumps(lease), encoding="utf-8")
    lease_summary = state._lease_summary(lease)
    lease_summary["runtimeAuthorizationSha256"] = (
        runtime_authorization.runtime_lease_authorization_sha256(lease)
    )
    identity = state.expected_battle_identity(deployment, lease_summary, deployment_sha)
    started = datetime(2026, 7, 15, tzinfo=timezone.utc)
    pilot_teams = elo_watchdog.OWNER_LOCKED_PILOT_TEAMS
    rows = []
    for index in range(battle_count):
        row = {
            "battle_id": f"battle-gen9ou-{index + 1}",
            "timestamp": (started + timedelta(minutes=index)).isoformat(),
            "result": results[index] if results is not None else ("win" if index % 2 == 0 else "loss"),
            "account": identity["account"],
            "team_file": pilot_teams[index % len(pilot_teams)],
            "elo_after": 1000 + index,
            "rprd": 45,
        }
        for row_field, identity_field in state.BATTLE_IDENTITY_FIELDS.items():
            row[row_field] = identity[identity_field]
        rows.append(row)
    stats_path = tmp_path / "battle_stats.json"
    stats_path.write_text(json.dumps({"battles": rows}), encoding="utf-8")
    activation = state.build_activation_receipt(
        root=root,
        deployment_receipt_path=deployment_path,
        runtime_lease_path=lease_path,
        battle_stats_path=stats_path,
        baseline=baseline,
    )
    state_root = tmp_path / "deployment-state"
    activation_path = state.activation_receipt_path(activation["activationId"], state_root)
    state.write_immutable_receipt(activation_path, activation)
    state.write_current_activation(
        activation_path,
        state_root=state_root,
        battle_stats_path=stats_path,
    )
    return {
        "root": root,
        "deployment": deployment,
        "deploymentPath": deployment_path,
        "leasePath": lease_path,
        "statsPath": stats_path,
        "rows": rows,
        "activation": activation,
        "activationPath": activation_path,
        "stateRoot": state_root,
    }


def test_activation_binds_clean_release_lease_session_and_observed_battle(tmp_path):
    bundle = make_bundle(tmp_path)

    activation, blockers = state.load_current_activation(
        state_root=bundle["stateRoot"],
        verify_checkout=True,
        battle_stats_path=bundle["statsPath"],
        verify_observation=True,
    )

    assert blockers == []
    assert activation["deploymentId"] == bundle["deployment"]["deploymentId"]
    assert activation["runtimeIdentity"]["runtimeLeaseId"] == "lease-pilot-0001"
    assert activation["observedBattle"]["battleId"] == "battle-gen9ou-1"


def test_activation_validator_rejects_writable_receipt(tmp_path):
    bundle = make_bundle(tmp_path)
    bundle["activationPath"].chmod(0o666)

    _activation, blockers = state.activation_receipt_blockers(
        bundle["activationPath"],
        verify_checkout=True,
    )

    assert "activation receipt is writable instead of immutable" in blockers


def test_activation_rejects_rehashed_but_signature_tampered_lease_snapshot(tmp_path):
    bundle = make_bundle(tmp_path)
    forged = copy.deepcopy(bundle["activation"])
    forged["runtimeLeaseSnapshot"]["maxRunCount"] = 999
    forged["runtimeLeaseSnapshotSha256"] = lineage.canonical_sha256(
        forged["runtimeLeaseSnapshot"]
    )
    forged["activationId"] = state._activation_identity(forged)
    forged["receiptSha256"] = lineage.canonical_sha256(forged)
    path = tmp_path / "forged-activation.json"
    state.write_immutable_receipt(path, forged)

    _activation, blockers = state.activation_receipt_blockers(
        path,
        verify_checkout=True,
        battle_stats_path=bundle["statsPath"],
        verify_observation=True,
    )

    assert any("runtime lease: controller authorization" in item for item in blockers)
    assert any("signature is invalid" in item for item in blockers)


def test_activation_rejects_swapped_or_recovered_battle_provenance(tmp_path):
    bundle = make_bundle(tmp_path)
    rows = bundle["rows"]
    rows[0]["source_commit"] = "f" * 40
    rows[0]["provenance_status"] = "recovered-unattributed"
    bundle["statsPath"].write_text(json.dumps({"battles": rows}), encoding="utf-8")

    with pytest.raises(ValueError, match="exact deployment/lease/session"):
        state.build_activation_receipt(
            root=bundle["root"],
            deployment_receipt_path=bundle["deploymentPath"],
            runtime_lease_path=bundle["leasePath"],
            battle_stats_path=bundle["statsPath"],
        )


def test_activation_keeps_immutable_lease_snapshot_across_renewal(tmp_path):
    bundle = make_bundle(tmp_path)
    renewed = json.loads(bundle["leasePath"].read_text(encoding="utf-8"))
    renewed["leaseId"] = "lease-pilot-0002"
    renewed["sessionId"] = "session-pilot-0002"
    renewed = sign_test_runtime_lease(renewed)
    bundle["leasePath"].write_text(json.dumps(renewed), encoding="utf-8")

    activation, blockers = state.load_current_activation(
        state_root=bundle["stateRoot"],
        verify_checkout=True,
    )
    assert blockers == []
    assert activation["runtimeIdentity"]["runtimeLeaseId"] == "lease-pilot-0001"

    context = state.current_deployment_context(
        battle_stats_path=bundle["statsPath"],
        state_root=bundle["stateRoot"],
        expected_runtime_identity={
            "runtimeLeaseId": "lease-pilot-0002",
            "sessionId": "session-pilot-0002",
        },
    )
    assert context["readyForImprovement"] is False
    assert any("does not match the running runtime identity" in item for item in context["blockers"])


def test_ensure_activation_waits_then_rotates_to_renewed_session(tmp_path, monkeypatch, capsys):
    bundle = make_bundle(tmp_path)
    renewed = json.loads(bundle["leasePath"].read_text(encoding="utf-8"))
    renewed["leaseId"] = "lease-pilot-0002"
    renewed["sessionId"] = "session-pilot-0002"
    renewed = sign_test_runtime_lease(renewed)
    bundle["leasePath"].write_text(json.dumps(renewed), encoding="utf-8")
    base_args = [
        "fouler_deployment_state.py",
        "--ensure-activation",
        "--root",
        str(bundle["root"]),
        "--state-root",
        str(bundle["stateRoot"]),
        "--deployment-receipt",
        str(bundle["deploymentPath"]),
        "--runtime-lease",
        str(bundle["leasePath"]),
        "--battle-stats",
        str(bundle["statsPath"]),
    ]
    monkeypatch.setattr(state_cli.sys, "argv", base_args)

    assert state_cli.main() == 0
    waiting = json.loads(capsys.readouterr().out)
    assert waiting["status"] == "waiting-for-first-battle"

    deployment_sha = hashlib.sha256(bundle["deploymentPath"].read_bytes()).hexdigest()
    renewed_summary = state._lease_summary(renewed)
    renewed_summary["runtimeAuthorizationSha256"] = (
        runtime_authorization.runtime_lease_authorization_sha256(renewed)
    )
    identity = state.expected_battle_identity(
        bundle["deployment"],
        renewed_summary,
        deployment_sha,
    )
    renewed_row = {
        "battle_id": "battle-gen9ou-renewed-session",
        "timestamp": "2026-07-15T01:00:00+00:00",
        "result": "win",
        "account": identity["account"],
    }
    for row_field, identity_field in state.BATTLE_IDENTITY_FIELDS.items():
        renewed_row[row_field] = identity[identity_field]
    rows = bundle["rows"] + [renewed_row]
    bundle["statsPath"].write_text(json.dumps({"battles": rows}), encoding="utf-8")

    assert state_cli.main() == 0
    active = json.loads(capsys.readouterr().out)
    assert active["status"] == "active"
    assert active["activation"]["runtimeIdentity"]["runtimeLeaseId"] == "lease-pilot-0002"


def test_judgment_uses_only_exact_identity_battles_and_is_immutable(tmp_path):
    bundle = make_bundle(tmp_path, battle_count=30)
    rows = list(bundle["rows"])
    foreign = dict(rows[0])
    foreign["battle_id"] = "battle-gen9ou-foreign"
    foreign["deployment_id"] = "fouler-deploy-foreign0000000000000000"
    rows.append(foreign)
    outside_lease = dict(rows[0])
    outside_lease["battle_id"] = "battle-gen9ou-after-expiry"
    outside_lease["timestamp"] = "2100-01-01T00:00:00+00:00"
    rows.append(outside_lease)

    judgment = state.build_judgment_receipt(
        activation=bundle["activation"],
        battle_rows=rows,
        min_battles=30,
    )
    path = state.judgment_receipt_path(bundle["activation"]["activationId"], bundle["stateRoot"])
    state.write_immutable_receipt(path, judgment)
    loaded, blockers = state.judgment_receipt_blockers(
        path,
        activation=bundle["activation"],
        battle_rows=rows,
    )

    assert blockers == []
    assert loaded["status"] == "passed-no-baseline"
    assert len(loaded["battleEvidence"]) == 30
    assert all(item["battleId"] != "battle-gen9ou-foreign" for item in loaded["battleEvidence"])
    assert all(item["battleId"] != "battle-gen9ou-after-expiry" for item in loaded["battleEvidence"])
    with pytest.raises(FileExistsError):
        state.write_immutable_receipt(path, judgment)


def test_watchdog_waits_then_writes_non_mutating_judgment(tmp_path, monkeypatch):
    bundle = make_bundle(tmp_path, battle_count=29)
    monkeypatch.setattr(elo_watchdog, "MIN_BATTLES_FOR_JUDGMENT", 30)

    waiting = elo_watchdog.check_and_judge(
        battle_stats_path=bundle["statsPath"],
        state_root=bundle["stateRoot"],
    )
    assert waiting["status"] == "waiting-for-sample"
    assert waiting["codeMutationPerformed"] is False

    rows = bundle["rows"]
    extra = dict(rows[-1])
    extra.update(
        {
            "battle_id": "battle-gen9ou-30",
            "timestamp": "2026-07-15T00:30:00+00:00",
            "result": "win",
            "team_file": elo_watchdog.OWNER_LOCKED_PILOT_TEAMS[2],
        }
    )
    rows.append(extra)
    bundle["statsPath"].write_text(json.dumps({"battles": rows}), encoding="utf-8")
    judged = elo_watchdog.check_and_judge(
        battle_stats_path=bundle["statsPath"],
        state_root=bundle["stateRoot"],
    )

    assert judged["ok"] is True
    assert judged["status"] == "passed-no-baseline"
    assert judged["codeMutationPerformed"] is False


def test_watchdog_marks_material_regression_without_mutating_release(tmp_path, monkeypatch):
    bundle = make_bundle(
        tmp_path,
        battle_count=30,
        baseline={"decisiveBattles": 30, "wins": 24, "losses": 6, "winRate": 0.8, "elo": 1200},
        results=["win"] * 6 + ["loss"] * 24,
    )
    monkeypatch.setattr(elo_watchdog, "MIN_BATTLES_FOR_JUDGMENT", 30)

    judged = elo_watchdog.check_and_judge(
        battle_stats_path=bundle["statsPath"],
        state_root=bundle["stateRoot"],
    )

    assert judged["ok"] is True
    assert judged["status"] == "regressed"
    assert judged["judgment"]["signals"]["winRateRegressed"] is True
    assert judged["codeMutationPerformed"] is False


def test_judgment_validator_recomputes_metrics_and_enforces_sample_floor(tmp_path):
    bundle = make_bundle(tmp_path, battle_count=30)
    with pytest.raises(ValueError, match="at least 30"):
        state.build_judgment_receipt(
            activation=bundle["activation"],
            battle_rows=bundle["rows"],
            min_battles=1,
        )

    judgment = state.build_judgment_receipt(
        activation=bundle["activation"],
        battle_rows=bundle["rows"],
        min_battles=30,
    )
    judgment["postActivation"]["winRate"] = 1.0
    judgment["judgmentId"] = state._judgment_identity(judgment)
    judgment["receiptSha256"] = state.canonical_sha256(state._payload_without_hash(judgment))
    path = state.judgment_receipt_path(bundle["activation"]["activationId"], bundle["stateRoot"])
    state.write_immutable_receipt(path, judgment)

    _loaded, blockers = state.judgment_receipt_blockers(
        path,
        activation=bundle["activation"],
        battle_rows=bundle["rows"],
    )
    assert "judgment post-activation metrics do not match battle evidence" in blockers


def test_current_context_fails_closed_without_judgment(tmp_path):
    bundle = make_bundle(tmp_path, battle_count=30)

    context = state.current_deployment_context(
        battle_stats_path=bundle["statsPath"],
        state_root=bundle["stateRoot"],
    )

    assert context["readyForImprovement"] is False
    assert context["gamesSinceActivation"] == 30
    assert any("judgment receipt" in blocker for blocker in context["blockers"])
