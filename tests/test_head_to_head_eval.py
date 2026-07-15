import hashlib
import json
import math
import sqlite3
import stat
import subprocess

import pytest

from infrastructure import head_to_head_authority as h2h_authority
from infrastructure import head_to_head_eval as h2h
from infrastructure import head_to_head_proof as h2h_proof
from infrastructure.head_to_head_proof import SCHEMA_VERSION, structure_blockers
from infrastructure.head_to_head_proof import load_latest_proof

BASELINE = "a" * 40
PATCH_SHA = "b" * 64
RUN_ID = "20260715T010203Z-deadbeef"
CHANGE_ID = "e" * 64
CANDIDATE_RUNTIME_DIGEST = "f" * 64
FROZEN_RUNTIME_DIGEST = "1" * 64


def expected_cell_provenance(
    cell,
    *,
    change_id=CHANGE_ID,
    candidate_runtime_digest=CANDIDATE_RUNTIME_DIGEST,
    frozen_runtime_digest=FROZEN_RUNTIME_DIGEST,
):
    candidate_account = f"candidate-{cell['id']}"
    frozen_account = f"frozen-{cell['id']}"
    frozen_role = "accepter" if cell["candidateRole"] == "challenger" else "challenger"
    common = {
        "format": "gen9ou",
        "source_commit": BASELINE,
        "h2h_run_id": RUN_ID,
        "h2h_cell_id": cell["id"],
        "h2h_baseline_commit": BASELINE,
        "h2h_candidate_patch_sha256": PATCH_SHA,
        "h2h_change_id": change_id,
    }
    return {
        "candidate": {
            **common,
            "account": candidate_account,
            "session_id": f"{RUN_ID}:{cell['id']}:candidate",
            "h2h_arm": "candidate",
            "h2h_role": cell["candidateRole"],
            "h2h_team": cell["candidateTeam"],
            "h2h_account": candidate_account,
            "h2h_opponent": frozen_account,
            "h2h_engine_digest": candidate_runtime_digest,
        },
        "frozen": {
            **common,
            "account": frozen_account,
            "session_id": f"{RUN_ID}:{cell['id']}:frozen",
            "h2h_arm": "frozen",
            "h2h_role": frozen_role,
            "h2h_team": cell["frozenTeam"],
            "h2h_account": frozen_account,
            "h2h_opponent": candidate_account,
            "h2h_engine_digest": frozen_runtime_digest,
        },
    }


def completed_cells(candidate_wins: int = 4, frozen_wins: int = 1):
    cells = h2h.build_evaluation_cells(h2h.DEFAULT_TEAMS, 60)
    return [
        {
            **cell,
            "completedBattles": cell["requestedBattles"],
            "candidateWins": candidate_wins,
            "frozenWins": frozen_wins,
            "ties": 0,
            "candidateReturncode": 0,
            "frozenReturncode": 0,
            "battleIds": [
                f"battle-gen9ou-{cell['id']}-{battle}"
                for battle in range(1, cell["requestedBattles"] + 1)
            ],
            "expectedProvenance": expected_cell_provenance(cell),
            "logEvidence": {
                "candidate": {"relativePath": f"{cell['id']}/candidate.log"},
                "frozen": {"relativePath": f"{cell['id']}/frozen.log"},
            },
            "error": "",
        }
        for cell in cells
    ]


def verdict(cells):
    return h2h.evaluate_matrix(
        cells,
        requested_battles=60,
        baseline_commit="a" * 40,
        candidate_patch_sha256="b" * 64,
    )


def promotable_proof(cells=None):
    cells = cells or completed_cells()
    report = verdict(cells)
    # Synthetic proof fixture for the independent validator. The in-process
    # harness itself is deliberately evidence-only and never emits this state.
    report["promotionAllowed"] = True
    report["blockers"] = []
    baseline = BASELINE
    patch_sha = PATCH_SHA
    candidate_file = "fp/search/main.py"
    run_id = RUN_ID
    runtime_family = "c" * 64
    protocol_digest = "d" * 64
    change_id = "e" * 64
    return {
        "schemaVersion": SCHEMA_VERSION,
        "status": "promotion-ready",
        "identicalSmoke": False,
        "baselineCommit": baseline,
        "candidatePatchSha256": patch_sha,
        "candidateFile": candidate_file,
        "runId": run_id,
        "runtimeFamilyId": runtime_family,
        "candidateRuntimeDigest": CANDIDATE_RUNTIME_DIGEST,
        "frozenRuntimeDigest": FROZEN_RUNTIME_DIGEST,
        "protocolDigest": protocol_digest,
        "runtimeEvidence": {
            "relativePath": "runtime-manifest.json",
            "sha256": "2" * 64,
            "byteLength": 100,
        },
        "lineage": {
            "changeId": change_id,
            "baselineCommit": baseline,
            "candidatePatchSha256": patch_sha,
            "candidateFile": candidate_file,
            "autoresearchSha256": "3" * 64,
        },
        "attemptBudget": {
            "registered": True,
            "schemaVersion": "fouler-head-to-head-attempt/v2",
            "ledgerId": "deku-test-ledger",
            "attemptId": "4" * 32,
            "registrationSequence": 1,
            "runId": run_id,
            "runtimeFamilyId": runtime_family,
            "protocolDigest": protocol_digest,
            "changeId": change_id,
            "baselineCommit": baseline,
            "candidatePatchSha256": patch_sha,
            "candidateFile": candidate_file,
            "attemptOrdinal": 1,
            "maximumAttempts": 5,
            "perAttemptAlpha": 0.01,
            "familyWiseAlpha": 0.05,
        },
        "configuration": {"battlesPerCell": 5},
        "cells": cells,
        **report,
    }


def write_proof_bundle(tmp_path):
    results_root = tmp_path / "head_to_head"
    run_id = "20260715T010203Z-deadbeef"
    run_dir = results_root / run_id
    run_dir.mkdir(parents=True)
    cells = completed_cells()
    for cell in cells:
        candidate_rows = []
        frozen_rows = []
        for battle_number, battle_id in enumerate(cell["battleIds"], start=1):
            candidate_result = "win" if battle_number <= 4 else "loss"
            frozen_result = "loss" if candidate_result == "win" else "win"
            candidate_rows.append({"battle_id": battle_id, "result": candidate_result})
            frozen_rows.append({"battle_id": battle_id, "result": frozen_result})
        candidate_path = run_dir / cell["id"] / "arm-a" / "battle_stats.json"
        frozen_path = run_dir / cell["id"] / "arm-b" / "battle_stats.json"
        candidate_path.parent.mkdir(parents=True)
        frozen_path.parent.mkdir(parents=True)
        candidate_path.write_text(json.dumps({"battles": candidate_rows}), encoding="utf-8")
        frozen_path.write_text(json.dumps({"battles": frozen_rows}), encoding="utf-8")
        candidate_evidence = h2h.file_evidence(candidate_path, relative_to=run_dir)
        frozen_evidence = h2h.file_evidence(frozen_path, relative_to=run_dir)
        candidate_evidence["rowCount"] = 5
        frozen_evidence["rowCount"] = 5
        cell["rawEvidence"] = {"candidate": candidate_evidence, "frozen": frozen_evidence}

    def digested(payload):
        return {**payload, "digest": h2h.canonical_sha256(payload)}

    frozen_runtime = digested(
        {"schemaVersion": "fouler-runtime-files/v1", "files": {"fp/search/main.py": "1" * 64}}
    )
    candidate_runtime = digested(
        {"schemaVersion": "fouler-runtime-files/v1", "files": {"fp/search/main.py": "2" * 64}}
    )
    protocol = digested({"schemaVersion": "fouler-h2h-protocol/v1", "files": {}})
    python_runtime = digested({"executable": "python", "packagesSha256": "3" * 64})
    showdown_runtime = digested({"checkout": "showdown", "commit": "4" * 40})
    environment_values = {"FOULER_OFFLINE_EVAL": "1"}
    environment = {
        "keys": sorted(environment_values),
        "values": environment_values,
        "digest": h2h.canonical_sha256(environment_values),
    }
    runtime_family = h2h.canonical_sha256(
        {
            "frozenRuntimeDigest": frozen_runtime["digest"],
            "protocolDigest": protocol["digest"],
            "pythonRuntimeDigest": python_runtime["digest"],
            "showdownRuntimeDigest": showdown_runtime["digest"],
            "environmentPolicyDigest": environment["digest"],
        }
    )
    runtime_payload = {
        "schemaVersion": "fouler-head-to-head-runtime/v2",
        "runtimeFamilyId": runtime_family,
        "candidateRuntime": candidate_runtime,
        "frozenRuntime": frozen_runtime,
        "runtimeDifferences": ["fp/search/main.py"],
        "protocol": protocol,
        "python": python_runtime,
        "showdown": showdown_runtime,
        "environmentPolicy": environment,
    }
    runtime_path = run_dir / "runtime-manifest.json"
    h2h.write_json(runtime_path, runtime_payload)

    baseline = "a" * 40
    patch_sha = "b" * 64
    autoresearch_sha = "3" * 64
    change_id = h2h.canonical_sha256(
        {
            "runtimeFamilyId": runtime_family,
            "baselineCommit": baseline,
            "candidateFile": "fp/search/main.py",
            "candidatePatchSha256": patch_sha,
            "candidateRuntimeDigest": candidate_runtime["digest"],
            "autoresearchSha256": autoresearch_sha,
        }
    )
    for cell in cells:
        expected = expected_cell_provenance(
            cell,
            change_id=change_id,
            candidate_runtime_digest=candidate_runtime["digest"],
            frozen_runtime_digest=frozen_runtime["digest"],
        )
        cell["expectedProvenance"] = expected
        candidate_path = run_dir / cell["id"] / "arm-a" / "battle_stats.json"
        frozen_path = run_dir / cell["id"] / "arm-b" / "battle_stats.json"
        candidate_rows = []
        frozen_rows = []
        for battle_number, battle_id in enumerate(cell["battleIds"], start=1):
            candidate_result = "win" if battle_number <= 4 else "loss"
            frozen_result = "loss" if candidate_result == "win" else "win"
            candidate_rows.append({**expected["candidate"], "battle_id": battle_id, "result": candidate_result})
            frozen_rows.append({**expected["frozen"], "battle_id": battle_id, "result": frozen_result})
        candidate_path.write_text(json.dumps({"battles": candidate_rows}), encoding="utf-8")
        frozen_path.write_text(json.dumps({"battles": frozen_rows}), encoding="utf-8")
        candidate_log = candidate_path.parent / "agent.log"
        frozen_log = frozen_path.parent / "agent.log"
        candidate_log.write_text(f"candidate {cell['id']}\n", encoding="utf-8")
        frozen_log.write_text(f"frozen {cell['id']}\n", encoding="utf-8")
        candidate_evidence = h2h.file_evidence(candidate_path, relative_to=run_dir)
        frozen_evidence = h2h.file_evidence(frozen_path, relative_to=run_dir)
        candidate_evidence["rowCount"] = len(candidate_rows)
        frozen_evidence["rowCount"] = len(frozen_rows)
        cell["rawEvidence"] = {"candidate": candidate_evidence, "frozen": frozen_evidence}
        cell["logEvidence"] = {
            "candidate": h2h.file_evidence(candidate_log, relative_to=run_dir),
            "frozen": h2h.file_evidence(frozen_log, relative_to=run_dir),
        }
    authority_path = tmp_path / "authority.json"
    ledger_path = tmp_path / "attempts.sqlite3"
    ledger_id = "deku-test-ledger"
    ledger_authority = h2h_authority.initialize_ledger_authority(
        authority_path=authority_path,
        ledger_path=ledger_path,
        ledger_id=ledger_id,
    )
    attempt = h2h.register_evaluation_attempt(
        ledger_path=ledger_path,
        ledger_id=ledger_id,
        runtime_family_id=runtime_family,
        protocol_digest=protocol["digest"],
        change_id=change_id,
        baseline_commit=baseline,
        candidate_patch_sha256=patch_sha,
        candidate_file="fp/search/main.py",
        run_id=run_id,
        ledger_authority=ledger_authority,
        allow_unanchored_test_only=True,
    )
    proof = promotable_proof(cells)
    proof.update(
        {
            "runId": run_id,
            "runtimeFamilyId": runtime_family,
            "candidateRuntimeDigest": candidate_runtime["digest"],
            "frozenRuntimeDigest": frozen_runtime["digest"],
            "protocolDigest": protocol["digest"],
            "runtimeEvidence": h2h.file_evidence(runtime_path, relative_to=run_dir),
            "lineage": {
                "changeId": change_id,
                "baselineCommit": baseline,
                "candidatePatchSha256": patch_sha,
                "candidateFile": "fp/search/main.py",
                "autoresearchSha256": autoresearch_sha,
            },
            "attemptBudget": attempt,
        }
    )
    result_path = run_dir / "result.json"
    h2h.write_json(result_path, proof)
    result_sha = hashlib.sha256(result_path.read_bytes()).hexdigest()
    h2h.finalize_evaluation_attempt(
        ledger_path=ledger_path,
        ledger_id=ledger_id,
        attempt_id=attempt["attemptId"],
        result_sha256=result_sha,
        status="promotion-ready",
        ledger_authority=ledger_authority,
    )
    pointer_path = results_root / "latest.json"
    h2h.write_json(
        pointer_path,
        {
            "schemaVersion": "fouler-head-to-head-pointer/v2",
            "runId": run_id,
            "resultRelativePath": f"{run_id}/result.json",
            "resultSha256": result_sha,
            "completedAtUtc": "2026-07-15T01:02:03+00:00",
        },
    )
    return {
        "pointer": pointer_path,
        "result": result_path,
        "runDir": run_dir,
        "ledger": ledger_path,
        "ledgerId": ledger_id,
        "authority": authority_path,
        "ledgerAuthority": ledger_authority,
        "proof": proof,
    }


def test_matrix_balances_every_ordered_team_pair_and_role():
    cells = h2h.build_evaluation_cells(h2h.DEFAULT_TEAMS, 60)

    assert len(cells) == 12
    assert {cell["candidateRole"] for cell in cells} == {"challenger", "accepter"}
    assert all(cell["requestedBattles"] == 5 for cell in cells)
    pairs = {(cell["candidateTeam"], cell["frozenTeam"]) for cell in cells}
    assert len(pairs) == 6
    assert all(candidate != frozen for candidate, frozen in pairs)


def test_matrix_rejects_unbalanced_battle_count():
    try:
        h2h.build_evaluation_cells(h2h.DEFAULT_TEAMS, 61)
    except ValueError as exc:
        assert "multiple of 12" in str(exc)
    else:
        raise AssertionError("expected an unbalanced matrix to fail")


def test_authority_initialization_is_exclusive_and_immutable(tmp_path):
    authority_path = tmp_path / "state" / "authority.json"
    ledger_path = tmp_path / "state" / "attempts.sqlite3"
    initialized = h2h_authority.initialize_ledger_authority(
        authority_path=authority_path,
        ledger_path=ledger_path,
        ledger_id="fixed-ledger",
    )

    assert initialized.authority_path == authority_path.resolve()
    assert initialized.ledger_path == ledger_path.resolve()
    assert not authority_path.stat().st_mode & (stat.S_IWUSR | stat.S_IWGRP | stat.S_IWOTH)
    with pytest.raises(FileExistsError):
        h2h_authority.initialize_ledger_authority(
            authority_path=authority_path,
            ledger_path=ledger_path,
            ledger_id="replacement-ledger",
        )

    assert h2h_authority.load_ledger_authority(authority_path) == initialized


def test_authority_initialization_never_replaces_an_existing_database(tmp_path):
    authority_path = tmp_path / "authority.json"
    ledger_path = tmp_path / "attempts.sqlite3"
    original = b"existing-ledger-must-survive"
    ledger_path.write_bytes(original)

    with pytest.raises(FileExistsError):
        h2h_authority.initialize_ledger_authority(
            authority_path=authority_path,
            ledger_path=ledger_path,
            ledger_id="fixed-ledger",
        )

    assert ledger_path.read_bytes() == original
    assert not authority_path.exists()


def test_eval_authority_ignores_legacy_environment_repoint(tmp_path, monkeypatch):
    authority = h2h_authority.initialize_ledger_authority(
        authority_path=tmp_path / "authority.json",
        ledger_path=tmp_path / "fixed.sqlite3",
        ledger_id="fixed-ledger",
    )
    repointed_path = tmp_path / "fresh.sqlite3"
    h2h.initialize_evaluation_ledger(repointed_path, "fresh-ledger")
    monkeypatch.setenv("FOULER_H2H_LEDGER_PATH", str(repointed_path))
    monkeypatch.setenv("FOULER_H2H_LEDGER_ID", "fresh-ledger")
    monkeypatch.setattr(h2h, "DEFAULT_AUTHORITY_PATH", authority.authority_path)

    configured_path, configured_id = h2h.ledger_configuration()

    assert configured_path == authority.ledger_path
    assert configured_id == authority.ledger_id


def test_authority_fails_closed_when_missing_or_malformed(tmp_path):
    missing = tmp_path / "missing-authority.json"
    with pytest.raises(RuntimeError, match="missing or linked"):
        h2h_authority.load_ledger_authority(missing)

    malformed = tmp_path / "malformed-authority.json"
    malformed.write_text("{}", encoding="utf-8")
    malformed.chmod(0o444)
    with pytest.raises(RuntimeError, match="missing or unexpected fields"):
        h2h_authority.load_ledger_authority(malformed)


def test_authority_fails_closed_after_it_is_moved(tmp_path):
    authority = h2h_authority.initialize_ledger_authority(
        authority_path=tmp_path / "authority.json",
        ledger_path=tmp_path / "attempts.sqlite3",
        ledger_id="fixed-ledger",
    )
    moved_path = tmp_path / "moved-authority.json"
    authority.authority_path.chmod(0o600)
    authority.authority_path.replace(moved_path)
    moved_path.chmod(0o444)

    with pytest.raises(RuntimeError, match="moved or copied"):
        h2h_authority.load_ledger_authority(moved_path)


def test_authority_replacement_cannot_select_a_fresh_ledger(tmp_path):
    primary = h2h_authority.initialize_ledger_authority(
        authority_path=tmp_path / "primary" / "authority.json",
        ledger_path=tmp_path / "primary" / "attempts.sqlite3",
        ledger_id="primary-ledger",
    )
    replacement = h2h_authority.initialize_ledger_authority(
        authority_path=tmp_path / "replacement" / "authority.json",
        ledger_path=tmp_path / "replacement" / "attempts.sqlite3",
        ledger_id="replacement-ledger",
    )
    replacement_payload = json.loads(replacement.authority_path.read_text(encoding="utf-8"))
    replacement_payload["authorityPath"] = str(primary.authority_path)
    replacement_payload["authorityDigest"] = h2h_authority.authority_digest(replacement_payload)
    primary.authority_path.chmod(0o600)
    primary.authority_path.write_text(json.dumps(replacement_payload), encoding="utf-8")
    primary.authority_path.chmod(0o444)

    with pytest.raises(RuntimeError, match="authority metadata mismatch"):
        h2h_authority.load_ledger_authority(primary.authority_path)


def test_replacing_authority_and_database_cannot_restart_production_attempt_one(tmp_path):
    authority_path = tmp_path / "authority.json"
    ledger_path = tmp_path / "attempts.sqlite3"
    authority = h2h_authority.initialize_ledger_authority(
        authority_path=authority_path,
        ledger_path=ledger_path,
        ledger_id="replaceable-local-ledger",
    )
    for ordinal in range(1, 6):
        recorded = h2h.register_evaluation_attempt(
            ledger_path=ledger_path,
            ledger_id=authority.ledger_id,
            runtime_family_id="f" * 64,
            protocol_digest="e" * 64,
            change_id=f"{ordinal:064x}",
            baseline_commit="a" * 40,
            candidate_patch_sha256=f"{ordinal:064x}",
            candidate_file="fp/search/main.py",
            run_id=f"test-only-{ordinal}",
            ledger_authority=authority,
            allow_unanchored_test_only=True,
        )
        assert recorded["attemptOrdinal"] == ordinal

    authority_path.chmod(0o600)
    authority_path.unlink()
    for candidate in (ledger_path, ledger_path.with_name(ledger_path.name + "-wal"), ledger_path.with_name(ledger_path.name + "-shm")):
        candidate.unlink(missing_ok=True)
    replacement = h2h_authority.initialize_ledger_authority(
        authority_path=authority_path,
        ledger_path=ledger_path,
        ledger_id="replaceable-local-ledger",
    )

    attempted_reset = h2h.register_evaluation_attempt(
        ledger_path=ledger_path,
        ledger_id=replacement.ledger_id,
        runtime_family_id="f" * 64,
        protocol_digest="e" * 64,
        change_id="9" * 64,
        baseline_commit="a" * 40,
        candidate_patch_sha256="9" * 64,
        candidate_file="fp/search/main.py",
        run_id="production-reset-attempt",
        ledger_authority=replacement,
    )

    assert attempted_reset["registered"] is False
    assert attempted_reset["externalAnchorProven"] is False
    assert "attemptOrdinal" not in attempted_reset
    assert "DEKU-owned durable attempt anchor" in attempted_reset["blocker"]


def test_authority_rejects_database_identity_mismatch(tmp_path):
    authority = h2h_authority.initialize_ledger_authority(
        authority_path=tmp_path / "authority.json",
        ledger_path=tmp_path / "attempts.sqlite3",
        ledger_id="fixed-ledger",
    )
    with sqlite3.connect(authority.ledger_path) as connection:
        with pytest.raises(sqlite3.IntegrityError, match="metadata is immutable"):
            connection.execute("UPDATE metadata SET value = 'different-ledger' WHERE key = 'ledgerId'")
        connection.rollback()
        connection.execute("DROP TRIGGER metadata_immutable")
        connection.execute("UPDATE metadata SET value = 'different-ledger' WHERE key = 'ledgerId'")
        connection.commit()

    with pytest.raises(RuntimeError, match="identity does not match"):
        h2h_authority.load_ledger_authority(authority.authority_path)


def test_attempt_budget_caps_each_frozen_baseline_at_five_trials(tmp_path, monkeypatch):
    ledger_path = tmp_path / "attempts.sqlite3"
    ledger_id = "deku-test-ledger"
    h2h.initialize_evaluation_ledger(ledger_path, ledger_id)
    baseline = "a" * 40
    runtime_family = "f" * 64

    registrations = [
        h2h.register_evaluation_attempt(
            ledger_path=ledger_path,
            ledger_id=ledger_id,
            runtime_family_id=runtime_family,
            protocol_digest="e" * 64,
            change_id=f"{index:064x}",
            baseline_commit=baseline,
            candidate_patch_sha256=f"{index:064x}",
            candidate_file="fp/search/main.py",
            run_id=f"run-{index}",
            allow_unanchored_test_only=True,
        )
        for index in range(1, 7)
    ]

    assert [entry.get("attemptOrdinal") for entry in registrations[:5]] == [1, 2, 3, 4, 5]
    assert all(entry["registered"] is True for entry in registrations[:5])
    assert registrations[5]["registered"] is False
    assert "exhausted" in registrations[5]["blocker"]

    next_baseline = h2h.register_evaluation_attempt(
        ledger_path=ledger_path,
        ledger_id=ledger_id,
        runtime_family_id=runtime_family,
        protocol_digest="e" * 64,
        change_id="c" * 64,
        baseline_commit="c" * 40,
        candidate_patch_sha256="d" * 64,
        candidate_file="fp/search/main.py",
        run_id="run-next-baseline",
        allow_unanchored_test_only=True,
    )
    assert next_baseline["registered"] is False

    next_runtime_family = h2h.register_evaluation_attempt(
        ledger_path=ledger_path,
        ledger_id=ledger_id,
        runtime_family_id="9" * 64,
        protocol_digest="e" * 64,
        change_id="d" * 64,
        baseline_commit="c" * 40,
        candidate_patch_sha256="d" * 64,
        candidate_file="fp/search/main.py",
        run_id="run-next-runtime-family",
        allow_unanchored_test_only=True,
    )
    assert next_runtime_family["registered"] is True
    assert next_runtime_family["attemptOrdinal"] == 1


def test_attempt_history_is_forward_only_and_cannot_be_deleted(tmp_path):
    ledger_path = tmp_path / "attempts.sqlite3"
    ledger_id = "deku-test-ledger"
    h2h.initialize_evaluation_ledger(ledger_path, ledger_id)
    attempt = h2h.register_evaluation_attempt(
        ledger_path=ledger_path,
        ledger_id=ledger_id,
        runtime_family_id="f" * 64,
        protocol_digest="e" * 64,
        change_id="d" * 64,
        baseline_commit="a" * 40,
        candidate_patch_sha256="b" * 64,
        candidate_file="fp/search/main.py",
        run_id="run-forward-only",
        allow_unanchored_test_only=True,
    )

    with sqlite3.connect(ledger_path) as connection:
        with pytest.raises(sqlite3.IntegrityError, match="identity and family budget are immutable"):
            connection.execute(
                "UPDATE attempts SET baseline_commit = ? WHERE attempt_id = ?",
                ("c" * 40, attempt["attemptId"]),
            )
        connection.rollback()
        with pytest.raises(sqlite3.IntegrityError, match="cannot be deleted"):
            connection.execute("DELETE FROM attempts WHERE attempt_id = ?", (attempt["attemptId"],))

    h2h.finalize_evaluation_attempt(
        ledger_path=ledger_path,
        ledger_id=ledger_id,
        attempt_id=attempt["attemptId"],
        result_sha256="1" * 64,
        status="promotion-blocked",
    )
    with pytest.raises(RuntimeError, match="exactly one registered attempt"):
        h2h.finalize_evaluation_attempt(
            ledger_path=ledger_path,
            ledger_id=ledger_id,
            attempt_id=attempt["attemptId"],
            result_sha256="2" * 64,
            status="promotion-ready",
        )


def test_ledger_rejects_same_named_noop_trigger_replacement(tmp_path):
    ledger_path = tmp_path / "attempts.sqlite3"
    ledger_id = "deku-test-ledger"
    h2h.initialize_evaluation_ledger(ledger_path, ledger_id)
    with sqlite3.connect(ledger_path) as connection:
        connection.executescript(
            """
            DROP TRIGGER attempts_no_delete;
            CREATE TRIGGER attempts_no_delete
            BEFORE DELETE ON attempts WHEN 0
            BEGIN SELECT 1; END;
            """
        )

    with pytest.raises(RuntimeError, match="schema fingerprint"):
        h2h_authority.open_evaluation_ledger(
            ledger_path,
            ledger_id,
            writable=False,
        )


def test_improve_authorization_is_durably_single_use(tmp_path):
    ledger_path = tmp_path / "attempts.sqlite3"
    ledger_id = "deku-test-ledger"
    h2h.initialize_evaluation_ledger(ledger_path, ledger_id)
    checkout = tmp_path / "control-checkout"
    checkout.mkdir()
    arguments = {
        "ledger_path": ledger_path,
        "ledger_id": ledger_id,
        "authorization_digest": "a" * 64,
        "lease_id": "deku-improve-lease-1",
        "source_commit": "b" * 40,
        "source_tree": "c" * 40,
        "change_id": "change-1",
        "deployment_id": "deployment-1",
        "session_id": "session-1",
        "account": "DekuFoulerLab",
        "control_checkout": checkout,
        "control_head": "b" * 40,
        "control_tree": "c" * 40,
        "max_cycles": 1,
    }

    first = h2h_authority.consume_improve_authorization(**arguments)
    repeated = h2h_authority.consume_improve_authorization(**arguments)
    resigned_same_lease = h2h_authority.consume_improve_authorization(
        **{**arguments, "authorization_digest": "d" * 64}
    )
    with pytest.raises(ValueError, match="bind the control checkout HEAD"):
        h2h_authority.consume_improve_authorization(
            **{
                **arguments,
                "authorization_digest": "e" * 64,
                "lease_id": "deku-improve-lease-2",
                "control_head": "f" * 40,
            }
        )

    assert first["consumed"] is True
    assert repeated["consumed"] is False
    assert resigned_same_lease["consumed"] is False
    with sqlite3.connect(ledger_path) as connection:
        with pytest.raises(sqlite3.IntegrityError, match="history is immutable"):
            connection.execute("UPDATE improve_authorizations SET account = 'other'")
        connection.rollback()
        with pytest.raises(sqlite3.IntegrityError, match="cannot be deleted"):
            connection.execute("DELETE FROM improve_authorizations")


def test_promotion_accepts_significant_balanced_candidate_win():
    report = verdict(completed_cells())

    assert report["candidateWins"] == 48
    assert report["frozenWins"] == 12
    assert report["oneSidedExactP"] < 0.05
    assert report["statisticalPromotionCandidate"] is True
    assert report["statisticalBlockers"] == []
    assert report["promotionAllowed"] is False
    assert report["blockers"] == [h2h.LOCAL_ISOLATION_BLOCKER]


def test_independent_validator_recomputes_every_promotion_statistic():
    proof = promotable_proof()

    assert structure_blockers(proof) == []

    proof["effectOverFrozen"] = 0.49
    proof["oneSidedExactP"] = 0.000001
    proof["roleSummary"]["challenger"]["wins"] += 1
    proof["candidateTeamSummary"]["fat-team-1-stall"]["winRate"] = 1.0

    blockers = structure_blockers(proof)

    assert "reported candidate effect does not match cell results" in blockers
    assert "reported role summary does not match cells for challenger" in blockers
    assert "reported team summary does not match cells for fat-team-1-stall" in blockers


def test_independent_validator_rejects_non_finite_statistics():
    proof = promotable_proof()
    proof["effectOverFrozen"] = math.nan
    proof["oneSidedExactP"] = math.inf

    blockers = structure_blockers(proof)

    assert "candidate effect is missing or non-finite" in blockers
    assert "one-sided exact-binomial p-value is missing or non-finite" in blockers


def test_independent_validator_rejects_unbudgeted_legacy_promotion_proof():
    proof = promotable_proof()
    del proof["attemptBudget"]

    blockers = structure_blockers(proof)

    assert "promotion attempt was not durably pre-registered" in blockers


def test_promotion_rejects_small_nondiscriminating_edge():
    report = verdict(completed_cells(candidate_wins=3, frozen_wins=2))

    assert report["candidateWinRate"] == 0.6
    assert report["promotionAllowed"] is False
    assert any("exact binomial" in blocker for blocker in report["blockers"])


def test_promotion_rejects_challenge_role_regression():
    cells = completed_cells(candidate_wins=5, frozen_wins=0)
    for cell in cells:
        if cell["candidateRole"] == "challenger":
            cell["candidateWins"] = 2
            cell["frozenWins"] = 3

    report = verdict(cells)

    assert report["candidateWins"] == 42
    assert report["promotionAllowed"] is False
    assert any("regressed as challenger" in blocker for blocker in report["blockers"])


def test_promotion_rejects_team_regression():
    cells = completed_cells(candidate_wins=5, frozen_wins=0)
    weak_team = h2h.DEFAULT_TEAMS[0]
    for cell in cells:
        if cell["candidateTeam"] == weak_team:
            cell["candidateWins"] = 2
            cell["frozenWins"] = 3

    report = verdict(cells)

    assert report["promotionAllowed"] is False
    assert any("regressed on fat-team-1-stall" in blocker for blocker in report["blockers"])


def test_promotion_rejects_tie_or_disconnect_truth():
    cells = completed_cells()
    cells[0]["candidateWins"] = 3
    cells[0]["ties"] = 1

    report = verdict(cells)

    assert report["promotionAllowed"] is False
    assert any("tie/disconnect" in blocker for blocker in report["blockers"])


def test_promotion_rejects_a_matrix_that_substitutes_benchmark_teams():
    cells = completed_cells()
    for cell in cells:
        if cell["candidateTeam"] == h2h.DEFAULT_TEAMS[0]:
            cell["candidateTeam"] = "gen9/ou/unreviewed-team"

    report = verdict(cells)

    assert report["promotionAllowed"] is False
    assert any("mission benchmark teams" in blocker for blocker in report["blockers"])


def test_agent_command_runs_each_worktree_runner():
    root = h2h.PROJECT_ROOT / "frozen-worktree"
    command = h2h.build_agent_command(
        root,
        ["python"],
        username="frozenEval",
        mode="challenge_user",
        opponent="candidateEval",
        team=h2h.DEFAULT_TEAMS[0],
        battles=5,
        ws_uri="ws://127.0.0.1:8791/showdown/websocket",
        search_time_ms=1200,
    )

    assert command[1] == str(root / "infrastructure" / "offline_eval_runner.py")
    assert command[command.index("--bot-mode") + 1] == "challenge_user"
    assert command[command.index("--user-to-challenge") + 1] == "candidateEval"
    assert command[command.index("--run-count") + 1] == "5"


def test_agent_environment_disables_live_transports_and_strategy_experiments(tmp_path, monkeypatch):
    monkeypatch.setenv("OPENAI_API_KEY", "secret")
    monkeypatch.setenv("TWITCH_STREAM_KEY", "secret")
    monkeypatch.setenv("DISCORD_WEBHOOK_URL", "secret")
    monkeypatch.setenv("DEKU_BOT_TOKEN", "secret")
    monkeypatch.setenv("OBS_WEBSOCKET_PASSWORD", "secret")

    env = h2h.build_agent_env("candidate", tmp_path, 8791, 1200)

    assert "OPENAI_API_KEY" not in env
    assert "TWITCH_STREAM_KEY" not in env
    assert "DISCORD_WEBHOOK_URL" not in env
    assert "DEKU_BOT_TOKEN" not in env
    assert "OBS_WEBSOCKET_PASSWORD" not in env
    assert env["FOULER_BATTLE_RESULT_QUEUE"] == "0"
    assert env["FOULER_STREAM_EVENTS"] == "0"
    assert env["FOULER_LOOP_BREAK"] == "0"
    assert env["FOULER_PENALTY_PIPELINE"] == "0"
    assert env["MATCHUP_MEMORY_ENABLED"] == "0"
    assert env["SEARCH_TIME_MS"] == "1200"
    assert env["MIN_SEARCH_TIME_MS"] == "1200"
    assert env["PYTHONNOUSERSITE"] == "1"
    assert "FOULER_OFFLINE_EVAL" not in env
    assert "FOULER_OFFLINE_EVAL_LABEL" not in env
    for key in ("HOME", "USERPROFILE", "APPDATA", "LOCALAPPDATA", "TEMP", "TMP", "TMPDIR"):
        assert env[key].startswith(str(tmp_path))
    assert not (set(env) & h2h.FORBIDDEN_ARM_ENV_KEYS)

    with pytest.raises(ValueError, match="at least 1200ms"):
        h2h.build_agent_env("candidate", tmp_path / "weak", 8791, 1199)


def test_evaluation_runtime_limits_cannot_be_weakened():
    assert h2h.evaluation_runtime_limit_blockers(1200, 240.0) == []
    assert len(h2h.evaluation_runtime_limit_blockers(1199, 239.0)) == 2


def test_controller_attests_arm_identity_only_after_raw_behavior():
    cell = h2h.build_evaluation_cells(h2h.DEFAULT_TEAMS, 60)[0]
    expected = expected_cell_provenance(cell)["candidate"]
    raw = {
        "battle_id": "battle-gen9ou-123",
        "result": "win",
        "account": expected["account"],
        "format": "gen9ou",
        "team_file": h2h.normalize_team(cell["candidateTeam"]),
        "source_commit": "unknown",
        "session_id": "opaque-agent-session",
    }

    attested, blockers = h2h.externally_attest_rows([raw], expected, label="candidate")

    assert blockers == []
    assert not any(key.startswith("h2h_") for key in raw)
    assert attested[0]["h2h_arm"] == "candidate"
    assert attested[0]["h2h_candidate_patch_sha256"] == PATCH_SHA
    assert attested[0]["source_commit"] == BASELINE
    assert attested[0]["session_id"] == expected["session_id"]

    _attested, leaked = h2h.externally_attest_rows(
        [{**raw, "h2h_arm": "candidate"}],
        expected,
        label="candidate",
    )
    assert any("controller-only provenance" in blocker for blocker in leaked)


def test_prepared_arms_hide_git_metadata_and_use_opaque_roots(tmp_path, monkeypatch):
    repo = tmp_path / "repo"
    target = repo / "fp" / "search" / "main.py"
    target.parent.mkdir(parents=True)
    target.write_text("VALUE = 1\n", encoding="utf-8")
    subprocess_args = {"check": True, "capture_output": True, "text": True}

    subprocess.run(["git", "init", "-q", str(repo)], **subprocess_args)
    subprocess.run(["git", "-C", str(repo), "config", "user.email", "proof@test.invalid"], **subprocess_args)
    subprocess.run(["git", "-C", str(repo), "config", "user.name", "Proof Test"], **subprocess_args)
    subprocess.run(["git", "-C", str(repo), "add", "-A"], **subprocess_args)
    subprocess.run(["git", "-C", str(repo), "commit", "-qm", "baseline"], **subprocess_args)
    target.write_text("VALUE = 2\n", encoding="utf-8")
    monkeypatch.setattr(h2h, "PROJECT_ROOT", repo)

    with h2h.prepared_worktrees("fp/search/main.py") as prepared:
        candidate_root = prepared["candidateRoot"]
        frozen_root = prepared["frozenRoot"]
        assert candidate_root.name.startswith("fouler-arm-")
        assert frozen_root.name.startswith("fouler-arm-")
        assert not (candidate_root / ".git").exists()
        assert not (frozen_root / ".git").exists()
        assert (candidate_root / "fp/search/main.py").read_text(encoding="utf-8") == "VALUE = 2\n"
        assert (frozen_root / "fp/search/main.py").read_text(encoding="utf-8") == "VALUE = 1\n"

    assert not candidate_root.exists()
    assert not frozen_root.exists()


def test_evaluation_arm_labels_are_semantically_opaque():
    source = h2h.run_cell.__code__.co_consts
    flattened = " ".join(value for value in source if isinstance(value, str)).lower()

    assert "candeval" not in flattened
    assert "frozeval" not in flattened


def test_cell_summary_rejects_per_battle_result_mismatch(tmp_path):
    candidate_path = tmp_path / "candidate.json"
    frozen_path = tmp_path / "frozen.json"
    candidate_path.write_text(
        json.dumps(
            {
                "battles": [
                    {"battle_id": "battle-a", "result": "win"},
                    {"battle_id": "battle-b", "result": "loss"},
                ]
            }
        ),
        encoding="utf-8",
    )
    frozen_path.write_text(
        json.dumps(
            {
                "battles": [
                    {"battle_id": "battle-a", "result": "win"},
                    {"battle_id": "battle-b", "result": "loss"},
                ]
            }
        ),
        encoding="utf-8",
    )

    result = h2h.summarize_cell(
        {"id": "cell-01", "requestedBattles": 2},
        candidate_stats_path=candidate_path,
        frozen_stats_path=frozen_path,
        candidate_returncode=0,
        frozen_returncode=0,
        candidate_expected_provenance={},
        frozen_expected_provenance={},
    )

    assert "per-battle result perspectives disagree" in result["error"]
    candidate_evidence = tmp_path / result["rawEvidence"]["candidate"]["relativePath"]
    assert candidate_evidence.exists() is False
    assert result["_deferredEvidence"]

    h2h.materialize_deferred_evidence(tmp_path, [result])

    assert candidate_evidence.is_file()
    assert "_deferredEvidence" not in result


def test_canonical_bundle_recomputes_from_raw_files_and_external_ledger(tmp_path):
    bundle = write_proof_bundle(tmp_path)

    proof, blockers = load_latest_proof(
        bundle["pointer"],
        project_root=tmp_path,
        ledger_path=bundle["ledger"],
        ledger_id=bundle["ledgerId"],
        verify_host_runtime=False,
    )

    assert blockers == []
    assert proof["promotionAllowed"] is True


def test_proof_authority_ignores_legacy_environment_repoint(tmp_path, monkeypatch):
    bundle = write_proof_bundle(tmp_path)
    repointed_path = tmp_path / "fresh.sqlite3"
    h2h.initialize_evaluation_ledger(repointed_path, "fresh-ledger")
    monkeypatch.setenv("FOULER_H2H_LEDGER_PATH", str(repointed_path))
    monkeypatch.setenv("FOULER_H2H_LEDGER_ID", "fresh-ledger")
    monkeypatch.setattr(h2h_proof, "DEFAULT_AUTHORITY_PATH", bundle["authority"])

    proof, blockers = load_latest_proof(
        bundle["pointer"],
        project_root=tmp_path,
        verify_host_runtime=False,
    )

    assert blockers == []
    assert proof["attemptBudget"]["ledgerId"] == bundle["ledgerId"]


def test_proof_fails_closed_after_authority_replacement(tmp_path):
    bundle = write_proof_bundle(tmp_path)
    authority_path = bundle["authority"]
    payload = json.loads(authority_path.read_text(encoding="utf-8"))
    payload["ledgerId"] = "replacement-ledger"
    payload["authorityDigest"] = h2h_authority.authority_digest(payload)
    authority_path.chmod(0o600)
    authority_path.write_text(json.dumps(payload), encoding="utf-8")
    authority_path.chmod(0o444)

    _proof, blockers = load_latest_proof(
        bundle["pointer"],
        project_root=tmp_path,
        authority_path=authority_path,
        verify_host_runtime=False,
    )

    assert any("external H2H ledger authority is invalid" in blocker for blocker in blockers)
    assert any("identity does not match" in blocker for blocker in blockers)


def test_canonical_bundle_rejects_raw_battle_tampering(tmp_path):
    bundle = write_proof_bundle(tmp_path)
    raw_path = bundle["runDir"] / "cell-01" / "arm-a" / "battle_stats.json"
    payload = json.loads(raw_path.read_text(encoding="utf-8"))
    payload["battles"][0]["result"] = "loss"
    raw_path.write_text(json.dumps(payload), encoding="utf-8")

    _proof, blockers = load_latest_proof(
        bundle["pointer"],
        project_root=tmp_path,
        ledger_path=bundle["ledger"],
        ledger_id=bundle["ledgerId"],
        verify_host_runtime=False,
    )

    assert any("raw" in blocker and ("SHA-256" in blocker or "perspectives disagree" in blocker) for blocker in blockers)


def test_canonical_bundle_rejects_swapped_or_unbound_arm_rows(tmp_path):
    bundle = write_proof_bundle(tmp_path)
    raw_path = bundle["runDir"] / "cell-01" / "arm-a" / "battle_stats.json"
    payload = json.loads(raw_path.read_text(encoding="utf-8"))
    payload["battles"][0]["h2h_arm"] = "frozen"
    raw_path.write_text(json.dumps(payload), encoding="utf-8")

    _proof, blockers = load_latest_proof(
        bundle["pointer"],
        project_root=tmp_path,
        ledger_path=bundle["ledger"],
        ledger_id=bundle["ledgerId"],
        verify_host_runtime=False,
    )

    assert any("candidate raw row 1 provenance mismatch: h2h_arm" in blocker for blocker in blockers)


def test_canonical_bundle_rejects_latest_pointer_swapping(tmp_path):
    bundle = write_proof_bundle(tmp_path)
    pointer = json.loads(bundle["pointer"].read_text(encoding="utf-8"))
    pointer["resultSha256"] = "0" * 64
    bundle["pointer"].write_text(json.dumps(pointer), encoding="utf-8")

    _proof, blockers = load_latest_proof(
        bundle["pointer"],
        project_root=tmp_path,
        ledger_path=bundle["ledger"],
        ledger_id=bundle["ledgerId"],
        verify_host_runtime=False,
    )

    assert "latest pointer SHA-256 does not match canonical result.json" in blockers


def test_canonical_bundle_rejects_forged_attempt_ordinal(tmp_path):
    bundle = write_proof_bundle(tmp_path)
    proof = json.loads(bundle["result"].read_text(encoding="utf-8"))
    proof["attemptBudget"]["attemptOrdinal"] = 5
    h2h.write_json(bundle["result"], proof)
    result_sha = hashlib.sha256(bundle["result"].read_bytes()).hexdigest()
    pointer = json.loads(bundle["pointer"].read_text(encoding="utf-8"))
    pointer["resultSha256"] = result_sha
    h2h.write_json(bundle["pointer"], pointer)

    _proof, blockers = load_latest_proof(
        bundle["pointer"],
        project_root=tmp_path,
        ledger_path=bundle["ledger"],
        ledger_id=bundle["ledgerId"],
        verify_host_runtime=False,
    )

    assert any("attempt_ordinal" in blocker or "result_sha256" in blocker for blocker in blockers)


def test_validator_rejects_skewed_49_1_cell_allocation():
    cells = completed_cells(candidate_wins=5, frozen_wins=0)
    allocations = [49, *([1] * 11)]
    for cell, requested in zip(cells, allocations):
        cell["requestedBattles"] = requested
        cell["completedBattles"] = requested
        cell["candidateWins"] = requested
        cell["frozenWins"] = 0
        cell["battleIds"] = [f"battle-{cell['id']}-{index}" for index in range(requested)]
    proof = promotable_proof(cells)
    proof["candidateWins"] = 60
    proof["frozenWins"] = 0
    proof["candidateWinRate"] = 1.0
    proof["effectOverFrozen"] = 0.5
    proof["oneSidedExactP"] = 0.0

    blockers = structure_blockers(proof)

    assert any("allocated exactly 5 battles" in blocker for blocker in blockers)
