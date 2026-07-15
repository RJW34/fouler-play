import sys
from datetime import datetime, timezone

import pytest
from cryptography.hazmat.primitives import serialization

from infrastructure import deployment_lineage
from scripts import devstream_runtime_lease as lease
from tests.runtime_authority_testkit import (
    TEST_CONTROLLER_ISSUER,
    TEST_CONTROLLER_KEY_ID,
    TEST_PRIVATE_KEY,
    sign_test_runtime_lease,
)


NOW = datetime(2026, 6, 8, 12, 0, tzinfo=timezone.utc)
SOURCE_COMMIT = "a" * 40
HOST_A = {"hostname": "jigglypuff", "hostIdSha256": "1" * 64}
HOST_B = {"hostname": "copied-host", "hostIdSha256": "2" * 64}
LEASE_PROVENANCE = {
    "source_commit": SOURCE_COMMIT,
    "change_id": "change-test-0001",
    "deployment_id": "deployment-test-0001",
    "source_tree": "b" * 40,
    "runtime_manifest_digest": "c" * 64,
    "deployment_receipt_path": "C:\\ProgramData\\HERMES\\state\\fouler\\deployment-test.json",
    "deployment_receipt_sha256": "d" * 64,
    "session_id": "session-test-0001",
}


@pytest.fixture(autouse=True)
def stable_physical_host(monkeypatch):
    monkeypatch.setattr(
        deployment_lineage,
        "current_physical_host_identity",
        lambda: dict(HOST_A),
    )


def write_lease(path, **overrides):
    payload = {
        "schemaVersion": lease.LEASE_SCHEMA_VERSION,
        "projectId": "fouler-play",
        "leaseId": "lease-test",
        "sourceCommit": SOURCE_COMMIT,
        "changeId": LEASE_PROVENANCE["change_id"],
        "deploymentId": LEASE_PROVENANCE["deployment_id"],
        "sourceTree": LEASE_PROVENANCE["source_tree"],
        "runtimeManifestDigest": LEASE_PROVENANCE["runtime_manifest_digest"],
        "deploymentReceiptPath": LEASE_PROVENANCE["deployment_receipt_path"],
        "deploymentReceiptSha256": LEASE_PROVENANCE["deployment_receipt_sha256"],
        "sessionId": LEASE_PROVENANCE["session_id"],
        "status": "active",
        "approved": True,
        "allowedPurposes": [
            "devstream-start",
            "devstream-start-dry-run",
            "devstream-supervise",
            "jigglypuff-runtime-start",
            "run-py-battle-runner",
        ],
        "machine": HOST_A["hostname"],
        "hostName": HOST_A["hostname"],
        "hostIdSha256": HOST_A["hostIdSha256"],
        "account": "bot",
        "maxRunCount": 10,
        "maxCycles": 2,
        "maxConcurrentBattles": 3,
        "replayBehavior": "save",
        "proofWindow": {
            "startsAt": "2026-06-08T00:00:00+00:00",
            "expiresAt": "2026-06-09T00:00:00+00:00",
        },
        "battleScope": {
            "machine": HOST_A["hostname"],
            "hostName": HOST_A["hostname"],
            "hostIdSha256": HOST_A["hostIdSha256"],
        },
    }
    payload.update(overrides)
    payload = sign_test_runtime_lease(payload)
    path.write_text(lease.json.dumps(payload), encoding="utf-8")
    return path


def test_missing_runtime_lease_fails_closed(tmp_path):
    payload = lease.validate_runtime_lease(
        purpose="devstream-start",
        lease_path=tmp_path / "missing.json",
        requested_run_count=1,
        requested_max_concurrent_battles=1,
        require_run_count=True,
        require_max_concurrent_battles=True,
        require_replay_behavior=True,
        now=NOW,
    )

    assert payload["ok"] is False
    assert "missing" in payload["blockers"][0]


@pytest.mark.parametrize(
    "schema_version",
    ["fouler-play-runtime-lease/v2", lease.LEASE_SCHEMA_VERSION],
)
def test_unsigned_runtime_lease_versions_fail_closed(tmp_path, schema_version):
    path = tmp_path / "unsigned-runtime-lease.json"
    unsigned = {
        "schemaVersion": schema_version,
        "projectId": "fouler-play",
        "leaseId": "unsigned-lease-test",
        "status": "active",
        "approved": True,
        "allowedPurposes": ["devstream-start"],
    }
    path.write_text(lease.json.dumps(unsigned), encoding="utf-8")

    payload = lease.validate_runtime_lease(
        purpose="devstream-start",
        lease_path=path,
        now=NOW,
    )

    assert payload["ok"] is False
    assert "controller authorization" in " ".join(payload["blockers"])
    assert payload["controllerAuthorization"]["ok"] is False


def test_duplicate_key_runtime_lease_json_fails_closed(tmp_path):
    path = tmp_path / "duplicate-runtime-lease.json"
    path.write_text(
        '{"schemaVersion":"fouler-play-runtime-lease/v3",'
        '"schemaVersion":"fouler-play-runtime-lease/v3"}',
        encoding="utf-8",
    )

    payload = lease.validate_runtime_lease(
        purpose="devstream-start",
        lease_path=path,
        now=NOW,
    )

    assert payload["ok"] is False
    assert "unreadable or not a JSON object" in " ".join(payload["blockers"])


def test_valid_runtime_lease_allows_bounded_live_start(tmp_path):
    path = write_lease(tmp_path / "runtime-lease.json")

    payload = lease.validate_runtime_lease(
        purpose="devstream-start",
        lease_path=path,
        requested_run_count=2,
        requested_max_concurrent_battles=3,
        requested_account="bot",
        require_run_count=True,
        require_max_concurrent_battles=True,
        require_replay_behavior=True,
        now=NOW,
    )

    assert payload["ok"] is True
    assert payload["lease"]["maxRunCount"] == 10


@pytest.mark.parametrize(
    ("overrides", "expected"),
    [
        ({"allowedPurposes": []}, "does not allow purpose"),
        ({"status": ""}, "explicit active status"),
        ({"approved": None}, "approved flag must be explicitly true"),
    ],
)
def test_runtime_lease_requires_explicit_authority_fields(tmp_path, overrides, expected):
    path = write_lease(tmp_path / "runtime-lease.json", **overrides)

    payload = lease.validate_runtime_lease(
        purpose="devstream-start",
        lease_path=path,
        requested_run_count=2,
        requested_max_concurrent_battles=3,
        requested_account="bot",
        require_run_count=True,
        require_max_concurrent_battles=True,
        require_replay_behavior=True,
        now=NOW,
    )

    assert payload["ok"] is False
    assert expected in " ".join(payload["blockers"])


def test_runtime_lease_requires_exact_requested_replay_behavior(tmp_path):
    path = write_lease(tmp_path / "runtime-lease.json", replayBehavior="always")

    payload = lease.validate_runtime_lease(
        purpose="devstream-start",
        lease_path=path,
        requested_run_count=2,
        requested_max_concurrent_battles=3,
        requested_replay_behavior="never",
        require_run_count=True,
        require_max_concurrent_battles=True,
        require_replay_behavior=True,
        now=NOW,
    )

    assert payload["ok"] is False
    assert "does not match requested replay behavior" in " ".join(payload["blockers"])


def test_copied_runtime_lease_is_rejected_on_a_different_host(tmp_path):
    path = write_lease(tmp_path / "runtime-lease.json")

    payload = lease.validate_runtime_lease(
        purpose="devstream-start",
        lease_path=path,
        requested_run_count=1,
        requested_max_concurrent_battles=1,
        requested_account="bot",
        require_run_count=True,
        require_max_concurrent_battles=True,
        require_replay_behavior=True,
        now=NOW,
        host_identity_provider=lambda: dict(HOST_B),
    )

    assert payload["ok"] is False
    assert "runtime lease hostname does not match the executing physical host" in payload[
        "blockers"
    ]
    assert "runtime lease host ID does not match the executing physical host" in payload[
        "blockers"
    ]


def test_runtime_lease_blocks_expired_proof_window(tmp_path):
    path = write_lease(
        tmp_path / "runtime-lease.json",
        proofWindow={
            "startsAt": "2026-06-07T00:00:00+00:00",
            "expiresAt": "2026-06-08T00:00:00+00:00",
        },
    )

    payload = lease.validate_runtime_lease(
        purpose="devstream-start",
        lease_path=path,
        requested_run_count=1,
        requested_max_concurrent_battles=1,
        require_run_count=True,
        require_max_concurrent_battles=True,
        require_replay_behavior=True,
        now=NOW,
    )

    assert payload["ok"] is False
    assert "expired" in " ".join(payload["blockers"])


def test_runtime_lease_blocks_run_count_over_scope(tmp_path):
    path = write_lease(tmp_path / "runtime-lease.json", maxRunCount=1)

    payload = lease.validate_runtime_lease(
        purpose="devstream-start",
        lease_path=path,
        requested_run_count=2,
        requested_max_concurrent_battles=1,
        require_run_count=True,
        require_max_concurrent_battles=True,
        require_replay_behavior=True,
        now=NOW,
    )

    assert payload["ok"] is False
    assert "exceeds lease maxRunCount" in " ".join(payload["blockers"])


def test_jiggly_runtime_start_lease_delegates_to_supervisor_and_child_start(tmp_path):
    path = tmp_path / "runtime-lease.json"
    artifact = sign_test_runtime_lease(lease.build_runtime_lease_artifact(
        purpose="jigglypuff-runtime-start",
        machine="JIGGLYPUFF",
        account="bot",
        run_count=30,
        max_cycles=1,
        max_concurrent_battles=1,
        replay_behavior="always",
        valid_minutes=30,
        **LEASE_PROVENANCE,
        now=NOW,
    ))
    lease.atomic_write_json(path, artifact)

    supervise = lease.validate_runtime_lease(
        purpose="devstream-supervise",
        lease_path=path,
        requested_run_count=30,
        requested_max_cycles=1,
        requested_max_concurrent_battles=1,
        requested_account="bot",
        require_run_count=True,
        require_max_cycles=True,
        require_max_concurrent_battles=True,
        require_replay_behavior=True,
        now=NOW,
    )
    child_start = lease.validate_runtime_lease(
        purpose="devstream-start",
        lease_path=path,
        requested_run_count=30,
        requested_max_concurrent_battles=1,
        requested_account="bot",
        require_run_count=True,
        require_max_concurrent_battles=True,
        require_replay_behavior=True,
        now=NOW,
    )
    runner = lease.validate_runtime_lease(
        purpose="run-py-battle-runner",
        lease_path=path,
        requested_run_count=30,
        requested_max_concurrent_battles=1,
        requested_account="bot",
        require_run_count=True,
        require_max_concurrent_battles=True,
        require_replay_behavior=True,
        now=NOW,
    )
    dry_run = lease.validate_runtime_lease(
        purpose="devstream-start-dry-run",
        lease_path=path,
        requested_run_count=30,
        requested_max_concurrent_battles=1,
        requested_account="bot",
        require_run_count=True,
        require_max_concurrent_battles=True,
        require_replay_behavior=True,
        now=NOW,
    )

    assert artifact["allowedPurposes"] == [
        "jigglypuff-runtime-start",
        "deployment-activation",
        "devstream-start-continuous-dry-run",
        "devstream-start-continuous",
        "devstream-supervise",
        "devstream-start-dry-run",
        "devstream-start",
        "devstream-stale-truth-cleanup-dry-run",
        "devstream-stale-truth-cleanup",
        "run-py-battle-runner",
    ]
    assert artifact["machine"] == "JIGGLYPUFF"
    assert artifact["hostName"] == HOST_A["hostname"]
    assert artifact["hostIdSha256"] == HOST_A["hostIdSha256"]
    assert artifact["battleScope"]["hostIdSha256"] == HOST_A["hostIdSha256"]
    assert supervise["ok"] is True
    assert child_start["ok"] is True
    assert runner["ok"] is True
    assert dry_run["ok"] is True


def test_supervise_lease_delegates_to_child_start(tmp_path):
    path = tmp_path / "runtime-lease.json"
    artifact = sign_test_runtime_lease(lease.build_runtime_lease_artifact(
        purpose="devstream-supervise",
        machine="JIGGLYPUFF",
        account="bot",
        run_count=10,
        max_cycles=1,
        max_concurrent_battles=1,
        replay_behavior="always",
        valid_minutes=30,
        **LEASE_PROVENANCE,
        now=NOW,
    ))
    lease.atomic_write_json(path, artifact)

    payload = lease.validate_runtime_lease(
        purpose="devstream-start",
        lease_path=path,
        requested_run_count=10,
        requested_max_concurrent_battles=1,
        requested_account="bot",
        require_run_count=True,
        require_max_concurrent_battles=True,
        require_replay_behavior=True,
        now=NOW,
    )

    assert artifact["allowedPurposes"] == [
        "devstream-supervise",
        "deployment-activation",
        "devstream-start-dry-run",
        "devstream-start",
        "devstream-stale-truth-cleanup-dry-run",
        "devstream-stale-truth-cleanup",
        "run-py-battle-runner",
    ]
    assert payload["ok"] is True


def test_devstream_start_lease_delegates_to_run_py_battle_runner(tmp_path):
    path = tmp_path / "runtime-lease.json"
    artifact = sign_test_runtime_lease(lease.build_runtime_lease_artifact(
        purpose="devstream-start",
        machine="JIGGLYPUFF",
        account="bot",
        run_count=10,
        max_cycles=1,
        max_concurrent_battles=1,
        replay_behavior="always",
        valid_minutes=30,
        **LEASE_PROVENANCE,
        now=NOW,
    ))
    lease.atomic_write_json(path, artifact)

    payload = lease.validate_runtime_lease(
        purpose="run-py-battle-runner",
        lease_path=path,
        requested_run_count=10,
        requested_max_concurrent_battles=1,
        requested_account="bot",
        require_run_count=True,
        require_max_concurrent_battles=True,
        require_replay_behavior=True,
        now=NOW,
    )

    assert artifact["allowedPurposes"] == [
        "devstream-start",
        "deployment-activation",
        "devstream-start-dry-run",
        "run-py-battle-runner",
    ]
    assert payload["ok"] is True


def test_supervise_requires_explicit_max_cycles(tmp_path):
    path = write_lease(tmp_path / "runtime-lease.json")

    payload = lease.validate_runtime_lease(
        purpose="devstream-supervise",
        lease_path=path,
        requested_run_count=1,
        requested_max_cycles=0,
        requested_max_concurrent_battles=1,
        require_run_count=True,
        require_max_cycles=True,
        require_max_concurrent_battles=True,
        require_replay_behavior=True,
        now=NOW,
    )

    assert payload["ok"] is False
    assert "requested max cycles" in " ".join(payload["blockers"])


def test_generated_dry_run_runtime_lease_round_trips_without_execute_authority(tmp_path):
    path = tmp_path / "runtime-lease.json"
    artifact = sign_test_runtime_lease(lease.build_runtime_lease_artifact(
        purpose="devstream-start-dry-run",
        machine="JIGGLYPUFF",
        account="bot",
        run_count=1,
        max_cycles=1,
        max_concurrent_battles=1,
        replay_behavior="never",
        valid_minutes=30,
        **LEASE_PROVENANCE,
        now=NOW,
    ))
    lease.atomic_write_json(path, artifact)

    dry_run = lease.validate_runtime_lease(
        purpose="devstream-start-dry-run",
        lease_path=path,
        requested_run_count=1,
        requested_max_cycles=1,
        requested_max_concurrent_battles=1,
        requested_account="bot",
        require_run_count=True,
        require_max_cycles=True,
        require_max_concurrent_battles=True,
        require_replay_behavior=True,
        now=NOW,
    )
    execute = lease.validate_runtime_lease(
        purpose="devstream-start",
        lease_path=path,
        requested_run_count=1,
        requested_max_concurrent_battles=1,
        requested_account="bot",
        require_run_count=True,
        require_max_concurrent_battles=True,
        require_replay_behavior=True,
        now=NOW,
    )

    assert dry_run["ok"] is True
    assert dry_run["lease"]["maxRunCount"] == 1
    assert execute["ok"] is False
    assert "does not allow purpose devstream-start" in " ".join(execute["blockers"])


def test_cli_issue_mode_derives_and_signs_finite_lease(
    tmp_path,
    monkeypatch,
    capsys,
    fixed_controller_trust_store,
):
    path = tmp_path / "runtime-lease.json"
    receipt_path = tmp_path / "deployment-receipt.json"
    receipt = {
        "schemaVersion": deployment_lineage.DEPLOYMENT_SCHEMA_VERSION,
        "createdAt": NOW.isoformat(),
        "machine": "JIGGLYPUFF",
        "hostName": HOST_A["hostname"],
        "hostIdSha256": HOST_A["hostIdSha256"],
        "releasePath": str(tmp_path),
        "sourceCommit": SOURCE_COMMIT,
        "sourceTree": LEASE_PROVENANCE["source_tree"],
        "runtimeManifestDigest": LEASE_PROVENANCE["runtime_manifest_digest"],
        "changeId": LEASE_PROVENANCE["change_id"],
        "authorization": {
            "type": "owner-approved-release",
            "ownerApproved": True,
            "approvalRef": "test-owner-approval",
        },
    }
    receipt["deploymentId"] = deployment_lineage.deployment_identity(receipt)
    receipt["receiptSha256"] = deployment_lineage.canonical_sha256(receipt)
    deployment_lineage.write_immutable_receipt(receipt_path, receipt)
    private_key_path = tmp_path / "controller-private.pem"
    private_key_path.write_bytes(
        TEST_PRIVATE_KEY.private_bytes(
            serialization.Encoding.PEM,
            serialization.PrivateFormat.PKCS8,
            serialization.NoEncryption(),
        )
    )
    private_key_path.chmod(0o600)
    monkeypatch.setattr(
        sys,
        "argv",
        [
            "devstream_runtime_lease.py",
            "--issue",
            "--runtime-lease",
            str(path),
            "--deployment-receipt-input",
            str(receipt_path),
            "--controller-private-key",
            str(private_key_path),
            "--controller-trust-store",
            str(fixed_controller_trust_store),
            "--controller-key-id",
            TEST_CONTROLLER_KEY_ID,
            "--issued-by",
            TEST_CONTROLLER_ISSUER,
            "--purpose",
            "devstream-start-dry-run",
            "--machine",
            "JIGGLYPUFF",
            "--account",
            "bot",
            "--run-count",
            "1",
            "--max-cycles",
            "1",
            "--max-concurrent-battles",
            "1",
            "--replay-behavior",
            "never",
            "--valid-minutes",
            "30",
            "--source-commit",
            SOURCE_COMMIT,
            "--source-tree",
            LEASE_PROVENANCE["source_tree"],
            "--change-id",
            LEASE_PROVENANCE["change_id"],
            "--deployment-id",
            receipt["deploymentId"],
            "--runtime-manifest-digest",
            LEASE_PROVENANCE["runtime_manifest_digest"],
            "--deployment-receipt-path",
            LEASE_PROVENANCE["deployment_receipt_path"],
        ],
    )

    assert lease.main() == 0

    payload = lease.json.loads(capsys.readouterr().out)
    written = lease.json.loads(path.read_text(encoding="utf-8"))
    assert payload["ok"] is True
    assert payload["issuedLease"]["noRuntimeActions"] is True
    assert payload["issuedLease"]["controllerKeyId"] == TEST_CONTROLLER_KEY_ID
    assert written["allowedPurposes"] == ["devstream-start-dry-run"]
    assert written["controllerAuthorization"]["issuedBy"] == TEST_CONTROLLER_ISSUER
