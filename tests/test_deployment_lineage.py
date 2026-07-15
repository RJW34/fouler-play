import hashlib
import json
import subprocess

import pytest

from infrastructure import deployment_lineage as lineage


HOST_A = {"hostname": "jigglypuff", "hostIdSha256": "1" * 64}
HOST_B = {"hostname": "copied-host", "hostIdSha256": "2" * 64}
RELEASE_FILES = {
    "config.py": "CONFIG = {}\n",
    "constants.py": "ROOT_CONSTANT = 1\n",
    "constants_pkg/core.py": "PACKAGE_CONSTANT = 1\n",
    "data/pokedex.json": "{}\n",
    "fp/search/main.py": "VALUE = 1\n",
    "infrastructure/runtime_authorization.py": "AUTHORITY = 1\n",
    "pipeline.py": "PIPELINE = 1\n",
    "process_lock.py": "LOCK = 1\n",
    "replay_analysis/autoresearch.py": "ANALYSIS = 1\n",
    "requirements-dev.txt": "pytest\n",
    "requirements.txt": "websockets\n",
    "run.py": "print('runtime')\n",
    "scripts/devstream_session.py": "SESSION = 1\n",
    "streaming/state_store.py": "STREAM_STATE = 1\n",
    "teams/gen9/ou/example.txt": "Pikachu\n",
}
AUTHORITY_CHAIN_FILES = (
    "process_lock.py",
    "pipeline.py",
    "constants_pkg/core.py",
    "infrastructure/runtime_authorization.py",
    "replay_analysis/autoresearch.py",
    "scripts/devstream_session.py",
    "streaming/state_store.py",
    "requirements.txt",
)


def host_a():
    return dict(HOST_A)


def host_b():
    return dict(HOST_B)


def test_git_reads_disable_optional_locks(monkeypatch, tmp_path):
    captured = {}

    def fake_run(command, **kwargs):
        captured["command"] = command
        captured["env"] = kwargs.get("env")
        return subprocess.CompletedProcess(command, 0, stdout="", stderr="")

    monkeypatch.setattr(lineage.subprocess, "run", fake_run)

    lineage._git(tmp_path, "rev-parse", "HEAD")

    assert captured["command"][:2] == ["git", "--no-optional-locks"]
    assert captured["env"]["GIT_OPTIONAL_LOCKS"] == "0"


def make_release(tmp_path):
    subprocess.run(["git", "init", "-q", str(tmp_path)], check=True)
    subprocess.run(["git", "-C", str(tmp_path), "config", "user.email", "proof@test.invalid"], check=True)
    subprocess.run(["git", "-C", str(tmp_path), "config", "user.name", "Proof Test"], check=True)
    for relative, content in RELEASE_FILES.items():
        path = tmp_path / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content, encoding="utf-8")
    subprocess.run(["git", "-C", str(tmp_path), "add", "-A"], check=True)
    subprocess.run(["git", "-C", str(tmp_path), "commit", "-qm", "release"], check=True)
    return tmp_path


def build_owner_receipt(root):
    return lineage.build_deployment_receipt(
        root=root,
        machine="JIGGLYPUFF",
        change_id="owner-pilot-release-0001",
        authorization_type="owner-approved-release",
        approval_ref="codex-goal-owner-authorization",
        host_identity_provider=host_a,
    )


def test_runtime_manifest_v2_covers_complete_authority_chain(tmp_path):
    root = make_release(tmp_path / "release")

    manifest = lineage.runtime_manifest(root)

    assert lineage.DEPLOYMENT_SCHEMA_VERSION == "fouler-deployment-receipt/v1"
    assert manifest["schemaVersion"] == "fouler-runtime-files/v2"
    assert set(manifest["files"]) == set(RELEASE_FILES)
    assert set(AUTHORITY_CHAIN_FILES) <= set(manifest["files"])


def test_owner_approved_deployment_receipt_binds_clean_checkout(tmp_path):
    root = make_release(tmp_path / "release")
    receipt = build_owner_receipt(root)
    receipt_path = tmp_path / "state" / f"{receipt['deploymentId']}.json"
    lineage.write_immutable_receipt(receipt_path, receipt)
    expected = {
        **receipt,
        "deploymentReceiptSha256": hashlib.sha256(receipt_path.read_bytes()).hexdigest(),
    }

    loaded, blockers = lineage.deployment_receipt_blockers(
        receipt_path,
        root=root,
        expected=expected,
        host_identity_provider=host_a,
    )

    assert blockers == []
    assert loaded["sourceCommit"] == receipt["sourceCommit"]
    assert loaded["runtimeManifestDigest"] == receipt["runtimeManifestDigest"]
    assert loaded["machine"] == "JIGGLYPUFF"
    assert loaded["hostName"] == HOST_A["hostname"]
    assert loaded["hostIdSha256"] == HOST_A["hostIdSha256"]


def test_deployment_receipt_rejects_dirty_or_different_runtime(tmp_path):
    root = make_release(tmp_path / "release")
    receipt = build_owner_receipt(root)
    receipt_path = tmp_path / "state" / "deployment.json"
    lineage.write_immutable_receipt(receipt_path, receipt)
    (root / "run.py").write_text("print('different runtime')\n", encoding="utf-8")

    _loaded, blockers = lineage.deployment_receipt_blockers(
        receipt_path,
        root=root,
        host_identity_provider=host_a,
    )

    assert "checkout contains tracked or untracked non-ignored changes" in blockers
    assert "runtime files differ from checkout HEAD: run.py" in blockers
    assert "deployment receipt runtimeManifestDigest does not match the current checkout" in blockers


@pytest.mark.parametrize("relative", AUTHORITY_CHAIN_FILES)
def test_authority_chain_mutation_changes_manifest_and_invalidates_receipt(
    tmp_path,
    relative,
):
    root = make_release(tmp_path / "release")
    original_manifest = lineage.runtime_manifest(root)
    receipt = build_owner_receipt(root)
    receipt_path = tmp_path / "state" / "deployment.json"
    lineage.write_immutable_receipt(receipt_path, receipt)

    path = root / relative
    path.write_text(path.read_text(encoding="utf-8") + "MUTATED = 1\n", encoding="utf-8")

    changed_manifest = lineage.runtime_manifest(root)
    _loaded, blockers = lineage.deployment_receipt_blockers(
        receipt_path,
        root=root,
        host_identity_provider=host_a,
    )

    assert changed_manifest["files"][relative] != original_manifest["files"][relative]
    assert changed_manifest["digest"] != original_manifest["digest"]
    assert f"runtime files differ from checkout HEAD: {relative}" in blockers
    assert "deployment receipt runtimeManifestDigest does not match the current checkout" in blockers


@pytest.mark.parametrize(
    ("index_flag", "relative"),
    (
        ("--skip-worktree", "scripts/devstream_session.py"),
        ("--assume-unchanged", "infrastructure/runtime_authorization.py"),
    ),
)
def test_index_hidden_authority_mutation_cannot_bypass_immutable_checkout(
    tmp_path,
    index_flag,
    relative,
):
    root = make_release(tmp_path / "release")
    receipt = build_owner_receipt(root)
    original_digest = receipt["runtimeManifestDigest"]
    receipt_path = tmp_path / "state" / "deployment.json"
    lineage.write_immutable_receipt(receipt_path, receipt)
    flag_result = subprocess.run(
        ["git", "-C", str(root), "update-index", index_flag, "--", relative],
        capture_output=True,
        text=True,
        check=False,
    )
    if flag_result.returncode:
        pytest.skip(f"git index flag is unavailable: {flag_result.stderr.strip()}")

    path = root / relative
    path.write_text(path.read_text(encoding="utf-8") + "HIDDEN_MUTATION = 1\n", encoding="utf-8")
    status = subprocess.run(
        ["git", "-C", str(root), "status", "--porcelain=v1", "--untracked-files=all"],
        capture_output=True,
        text=True,
        check=True,
    )

    assert status.stdout == ""
    assert lineage.runtime_manifest(root)["digest"] != original_digest
    _loaded, blockers = lineage.deployment_receipt_blockers(
        receipt_path,
        root=root,
        host_identity_provider=host_a,
    )
    assert "checkout contains tracked or untracked non-ignored changes" not in blockers
    assert f"runtime files differ from checkout HEAD: {relative}" in blockers
    assert "deployment receipt runtimeManifestDigest does not match the current checkout" in blockers
    with pytest.raises(ValueError, match="runtime files differ from checkout HEAD"):
        build_owner_receipt(root)


def test_deployment_receipt_cannot_be_replaced(tmp_path):
    root = make_release(tmp_path / "release")
    receipt = build_owner_receipt(root)
    receipt_path = tmp_path / "state" / "deployment.json"
    lineage.write_immutable_receipt(receipt_path, receipt)

    with pytest.raises(FileExistsError):
        lineage.write_immutable_receipt(receipt_path, receipt)


def test_deployment_receipt_validator_rejects_writable_file(tmp_path):
    root = make_release(tmp_path / "release")
    receipt = build_owner_receipt(root)
    receipt_path = tmp_path / "state" / "deployment.json"
    lineage.write_immutable_receipt(receipt_path, receipt)
    receipt_path.chmod(0o666)

    _loaded, blockers = lineage.deployment_receipt_blockers(
        receipt_path,
        root=root,
        host_identity_provider=host_a,
    )

    assert "deployment receipt is writable instead of immutable" in blockers


def test_physical_host_identity_hashes_os_identifier_without_exposing_it():
    raw_machine_id = "secret-machine-guid-12345678"

    identity = lineage.current_physical_host_identity(
        hostname_provider=lambda: "JIGGLYPUFF.",
        stable_id_provider=lambda: raw_machine_id,
    )

    assert identity["hostname"] == "jigglypuff"
    assert lineage.HOST_ID_HASH_RE.fullmatch(identity["hostIdSha256"])
    assert raw_machine_id not in json.dumps(identity)


def test_copied_deployment_receipt_is_rejected_on_a_different_host(tmp_path):
    root = make_release(tmp_path / "release")
    receipt = lineage.build_deployment_receipt(
        root=root,
        machine="caller-controlled-name",
        change_id="owner-pilot-release-0001",
        authorization_type="owner-approved-release",
        approval_ref="codex-goal-owner-authorization",
        host_identity_provider=host_a,
    )
    receipt_path = tmp_path / "state" / "deployment.json"
    lineage.write_immutable_receipt(receipt_path, receipt)

    _loaded, blockers = lineage.deployment_receipt_blockers(
        receipt_path,
        root=root,
        host_identity_provider=host_b,
    )

    assert receipt["machine"] == "caller-controlled-name"
    assert receipt["hostName"] == HOST_A["hostname"]
    assert "deployment receipt hostname does not match the executing physical host" in blockers
    assert "deployment receipt host ID does not match the executing physical host" in blockers
