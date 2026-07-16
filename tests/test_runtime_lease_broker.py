from __future__ import annotations

import ctypes
import json
import os
import sqlite3
import struct
import sys
import threading
import time
import uuid
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import pytest

from infrastructure import runtime_lease_client as lease_client
from infrastructure.runtime_lease_client import (
    MAX_FRAME_BYTES,
    PROTOCOL_VERSION,
    DuplicateJSONKeyError,
    ProtocolError,
    broker_request_payload,
    decode_frame_bytes,
    encode_frame,
    LeaseBrokerClient,
    request_with_retry,
    response_error_text,
)
from infrastructure.windows import fouler_lease_broker as broker


AUTHORIZATION_DIGEST = "a" * 64
LEASE_ID = "lease-test-0001"
CALLER = broker.CallerIdentity(4100, 133_801_234_567_890_000)
CHILD = broker.CallerIdentity(4200, 133_801_234_567_891_000)
SUPERVISOR_INSTANCE_ID = "supervisor-test-0001"


def lease_registration(
    *,
    authorization_digest: str = AUTHORIZATION_DIGEST,
    lease_id: str = LEASE_ID,
    max_run_count: int = 10,
    max_cycles: int = 10,
    max_concurrent_battles: int = 3,
    improve_authorized: bool = True,
) -> dict[str, object]:
    return {
        "schemaVersion": broker.REGISTRATION_SCHEMA_VERSION,
        "leaseId": lease_id,
        "authorizationDigest": authorization_digest,
        "sourceCommit": "1" * 40,
        "sourceTree": "2" * 40,
        "changeId": "change-test-0001",
        "deploymentId": "deployment-test-0001",
        "sessionId": "session-test-0001",
        "runtimeManifestDigest": "3" * 64,
        "deploymentReceiptSha256": "4" * 64,
        "account": "DekuFoulerLab",
        "hostName": "jigglypuff",
        "hostIdSha256": "5" * 64,
        "proofStartsAt": "2020-01-01T00:00:00+00:00",
        "proofExpiresAt": "2099-01-01T00:00:00+00:00",
        "maxRunCount": max_run_count,
        "maxCycles": max_cycles,
        "maxConcurrentBattles": max_concurrent_battles,
        "improveAuthorized": improve_authorized,
    }


def request(action: str, request_id: str, **extra: object) -> dict[str, object]:
    return {
        "schemaVersion": PROTOCOL_VERSION,
        "action": action,
        "requestId": request_id,
        "authorizationDigest": AUTHORIZATION_DIGEST,
        "leaseId": LEASE_ID,
        **extra,
    }


def runtime_reserve(request_id: str, *, battles: int = 1, cycles: int = 1):
    return request(
        "reserve-runtime",
        request_id,
        purpose=broker.RUNTIME_RESERVATION_PURPOSE,
        kind="runtime",
        battleCount=battles,
        cycleCount=cycles,
        maxConcurrentBattles=1,
        supervisorInstanceId=SUPERVISOR_INSTANCE_ID,
    )


def reservation_binding(response: dict[str, object]) -> dict[str, object]:
    result = response["result"]
    assert isinstance(result, dict)
    return {
        name: result[name]
        for name in (
            "reservationId",
            "kind",
            "purpose",
            "battleCount",
            "cycleCount",
            "maxConcurrentBattles",
            "supervisorProcessId",
            "supervisorProcessCreationFiletime",
            "supervisorInstanceId",
            "launchNonce",
        )
    }


def claim_request(
    reserved: dict[str, object], request_id: str, **overrides: object
) -> dict[str, object]:
    return request("claim", request_id, **{**reservation_binding(reserved), **overrides})


def complete_request(
    reserved: dict[str, object], request_id: str, outcome: str, **overrides: object
) -> dict[str, object]:
    return request(
        "complete",
        request_id,
        **{**reservation_binding(reserved), **overrides},
        outcome=outcome,
    )


def synthetic_binding(reservation_id: str = "res-" + "b" * 32) -> dict[str, object]:
    return {
        "reservationId": reservation_id,
        "kind": "runtime",
        "purpose": broker.RUNTIME_RESERVATION_PURPOSE,
        "battleCount": 3,
        "cycleCount": 1,
        "maxConcurrentBattles": 3,
        "supervisorProcessId": 4100,
        "supervisorProcessCreationFiletime": CALLER.process_creation_filetime,
        "supervisorInstanceId": SUPERVISOR_INSTANCE_ID,
        "launchNonce": "9" * 64,
    }


@pytest.fixture
def store(tmp_path: Path) -> broker.ConsumptionStore:
    result = broker.ConsumptionStore.initialize(
        tmp_path / "consumption.sqlite3",
        tmp_path / "consumption.sqlite3.initialized",
    )
    result.register_lease(lease_registration())
    return result


def database_rows(store: broker.ConsumptionStore, statement: str):
    connection = sqlite3.connect(store.path)
    try:
        return connection.execute(statement).fetchall()
    finally:
        connection.close()


def test_fifty_concurrent_reservations_allow_only_one_unresolved(store):
    def reserve(index: int):
        caller = broker.CallerIdentity(5000 + index, CALLER.process_creation_filetime + index)
        return store.execute(runtime_reserve(f"reserve-concurrent-{index:03d}"), caller)

    with ThreadPoolExecutor(max_workers=50) as executor:
        responses = list(executor.map(reserve, range(50)))

    accepted = [response for response in responses if response["ok"]]
    rejected = [response for response in responses if not response["ok"]]
    assert len(accepted) == 1
    assert len(rejected) == 49
    assert {response["error"]["code"] for response in rejected} == {
        "outstanding_reservation"
    }
    assert database_rows(
        store,
        "SELECT count(*), sum(battle_count), sum(cycle_count) FROM reservations",
    ) == [(1, 1, 1)]


def test_request_ids_are_idempotent_and_bound_to_payload_and_caller(store):
    payload = runtime_reserve("reserve-idempotent-0001", battles=2, cycles=2)
    first = store.execute(payload, CALLER)
    retry = store.execute(payload, CALLER)

    assert retry == first
    assert database_rows(store, "SELECT count(*) FROM reservations") == [(1,)]

    changed = runtime_reserve("reserve-idempotent-0001", battles=1, cycles=1)
    conflict = store.execute(changed, CALLER)
    assert conflict["ok"] is False
    assert conflict["error"]["code"] == "idempotency_conflict"

    reused_pid = broker.CallerIdentity(
        CALLER.process_id, CALLER.process_creation_filetime + 1
    )
    identity_conflict = store.execute(payload, reused_pid)
    assert identity_conflict["ok"] is False
    assert identity_conflict["error"]["code"] == "idempotency_conflict"
    assert database_rows(store, "SELECT count(*) FROM reservations") == [(1,)]


def test_pid_reuse_creation_time_mismatch_cannot_complete_claim(store):
    reserved = store.execute(runtime_reserve("reserve-pid-reuse-0001"), CALLER)
    claimed = store.execute(
        claim_request(reserved, "claim-pid-reuse-0001"),
        CHILD,
    )
    assert claimed["ok"] is True

    reused_pid = broker.CallerIdentity(
        CHILD.process_id, CHILD.process_creation_filetime + 10_000
    )
    denied = store.execute(
        complete_request(reserved, "complete-pid-reuse-bad", "completed"),
        reused_pid,
    )
    assert denied["ok"] is False
    assert denied["error"]["code"] == "caller_identity_mismatch"

    completed = store.execute(
        complete_request(reserved, "complete-pid-reuse-good", "completed"),
        CHILD,
    )
    assert completed["ok"] is True
    assert completed["result"]["state"] == "completed"


def test_crash_like_retry_after_commit_returns_original_reservation(store):
    payload = runtime_reserve("reserve-crash-retry-0001")
    committed_response = store.execute(payload, CALLER)

    reopened = broker.ConsumptionStore(store.path, store.marker_path)
    retried_response = reopened.execute(payload, CALLER)

    assert retried_response == committed_response
    assert database_rows(store, "SELECT count(*) FROM reservations") == [(1,)]
    assert database_rows(store, "SELECT count(*) FROM request_journal") == [(1,)]


def test_minimal_reservation_cannot_be_claimed_as_larger_workload(store):
    reserved = store.execute(runtime_reserve("reserve-minimal-0001"), CALLER)

    rejected = store.execute(
        claim_request(reserved, "claim-larger-0001", battleCount=2), CHILD
    )

    assert rejected["ok"] is False
    assert rejected["error"]["code"] == "reservation_binding_mismatch"
    status = store.execute(
        request(
            "status",
            "status-minimal-0001",
            lookupType="reservation",
            lookupId=reserved["result"]["reservationId"],
        ),
        CHILD,
    )
    assert status["result"]["state"] == "reserved"


def test_claim_rejects_supervisor_creation_identity_mismatch(store):
    reserved = store.execute(runtime_reserve("reserve-supervisor-0001"), CALLER)

    rejected = store.execute(
        claim_request(
            reserved,
            "claim-supervisor-mismatch-0001",
            supervisorProcessCreationFiletime=CALLER.process_creation_filetime + 1,
        ),
        CHILD,
    )

    assert rejected["ok"] is False
    assert rejected["error"]["code"] == "supervisor_identity_mismatch"


def test_claim_rejects_launch_nonce_mismatch(store):
    reserved = store.execute(runtime_reserve("reserve-nonce-0001"), CALLER)

    rejected = store.execute(
        claim_request(reserved, "claim-nonce-mismatch-0001", launchNonce="0" * 64),
        CHILD,
    )

    assert rejected["ok"] is False
    assert rejected["error"]["code"] == "launch_nonce_mismatch"


def test_second_reservation_is_refused_until_first_is_terminal(store):
    first = store.execute(runtime_reserve("reserve-outstanding-0001"), CALLER)
    second = store.execute(runtime_reserve("reserve-outstanding-0002"), CALLER)

    assert first["ok"] is True
    assert second["ok"] is False
    assert second["error"]["code"] == "outstanding_reservation"
    assert store.execute(
        complete_request(first, "complete-outstanding-0001", "aborted"), CALLER
    )["ok"]
    third = store.execute(runtime_reserve("reserve-outstanding-0003"), CALLER)
    assert third["ok"] is True


def test_supervisor_can_terminally_abort_failed_launch_before_claim(store):
    reserved = store.execute(runtime_reserve("reserve-failed-launch-0001"), CALLER)

    completed = store.execute(
        complete_request(reserved, "complete-failed-launch-0001", "aborted"),
        CALLER,
    )

    assert completed["ok"] is True
    assert completed["result"]["state"] == "completed"
    assert completed["result"]["completionActor"] == "supervisor"
    assert completed["result"]["capacityReturned"] is False


def test_supervisor_can_reconcile_claim_after_child_process_has_exited(store):
    reserved = store.execute(runtime_reserve("reserve-dead-child-0001"), CALLER)
    assert store.execute(
        claim_request(reserved, "claim-dead-child-0001"), CHILD
    )["ok"]

    completed = store.execute(
        complete_request(reserved, "complete-dead-child-0001", "completed"),
        CALLER,
    )

    assert completed["ok"] is True
    assert completed["result"]["completionActor"] == "supervisor"
    assert completed["result"]["outcome"] == "completed"


def test_status_recovers_committed_request_and_terminal_reservation(store):
    reserve_request = runtime_reserve("reserve-status-recovery-0001")
    reserved = store.execute(reserve_request, CALLER)
    by_request = store.execute(
        request(
            "status",
            "status-request-recovery-0001",
            lookupType="request",
            lookupId=reserve_request["requestId"],
        ),
        CALLER,
    )
    assert by_request["result"]["response"] == reserved
    assert database_rows(store, "SELECT count(*) FROM request_journal") == [(1,)]

    store.execute(
        complete_request(reserved, "complete-status-recovery-0001", "aborted"),
        CALLER,
    )
    by_reservation = store.execute(
        request(
            "status",
            "status-reservation-recovery-0001",
            lookupType="reservation",
            lookupId=reserved["result"]["reservationId"],
        ),
        CHILD,
    )
    assert by_reservation["result"]["state"] == "completed"
    assert by_reservation["result"]["outcome"] == "aborted"
    assert "launchNonce" not in by_reservation["result"]


def test_request_status_denies_different_caller_but_allows_original(store):
    reserve_request = runtime_reserve("reserve-status-caller-bound-0001")
    reserved = store.execute(reserve_request, CALLER)
    intruder = broker.CallerIdentity(
        CALLER.process_id + 99, CALLER.process_creation_filetime + 99
    )

    denied = store.execute(
        request(
            "status",
            "status-cross-caller-denied-0001",
            lookupType="request",
            lookupId=reserve_request["requestId"],
        ),
        intruder,
    )
    recovered = store.execute(
        request(
            "status",
            "status-original-caller-allowed-0001",
            lookupType="request",
            lookupId=reserve_request["requestId"],
        ),
        CALLER,
    )

    assert denied["ok"] is False
    assert denied["error"]["code"] == "status_caller_mismatch"
    assert "result" not in denied
    assert recovered["result"]["response"] == reserved


def test_administrator_abandonment_is_not_a_runtime_pipe_action(store):
    reserved = store.execute(runtime_reserve("reserve-admin-abandon-0001"), CALLER)
    result = store.administratively_abandon(
        authorization_digest=AUTHORIZATION_DIGEST,
        lease_id=LEASE_ID,
        reservation_id=reserved["result"]["reservationId"],
        reason="verified supervisor and child are absent",
    )

    assert result["outcome"] == "abandoned"
    assert result["capacityReturned"] is False
    assert "admin-abandon-reservation" not in broker._ACTION_FIELDS


def test_runtime_improvement_is_delegated_to_external_deku_control_plane(store):
    response = store.execute(
        request(
            "reserve-improve",
            "reserve-improve-disabled-0001",
            purpose=broker.IMPROVE_RESERVATION_PURPOSE,
            kind="improve",
            battleCount=0,
            cycleCount=0,
            maxConcurrentBattles=0,
            supervisorInstanceId=SUPERVISOR_INSTANCE_ID,
        ),
        CALLER,
    )

    assert response["ok"] is False
    assert response["error"]["code"] == "improve_control_plane_only"
    assert database_rows(store, "SELECT count(*) FROM reservations") == [(0,)]
    assert database_rows(store, "SELECT count(*) FROM request_journal") == [(0,)]


def test_failed_completed_and_abandoned_reservations_all_count(store):
    first = store.execute(
        runtime_reserve("reserve-all-count-0001", battles=10, cycles=10), CALLER
    )
    assert first["ok"] is True
    assert store.execute(
        claim_request(first, "claim-all-count-0001"),
        CHILD,
    )["ok"]
    assert store.execute(
        complete_request(first, "complete-all-count-0001", "failed"),
        CHILD,
    )["ok"]

    denied = store.execute(runtime_reserve("reserve-all-count-0002"), CALLER)
    assert denied["ok"] is False
    assert denied["error"]["code"] == "run_bound_exhausted"


def test_protocol_rejects_oversize_duplicate_and_nonfinite_json():
    oversized = struct.pack(">I", MAX_FRAME_BYTES + 1)
    with pytest.raises(ProtocolError, match="size limit"):
        decode_frame_bytes(oversized)

    duplicate = b'{"action":"claim","action":"complete"}'
    with pytest.raises(DuplicateJSONKeyError):
        decode_frame_bytes(struct.pack(">I", len(duplicate)) + duplicate)

    nonfinite = b'{"value":NaN}'
    with pytest.raises(ProtocolError, match="strict JSON"):
        decode_frame_bytes(struct.pack(">I", len(nonfinite)) + nonfinite)

    valid = {"schemaVersion": PROTOCOL_VERSION, "value": "ok"}
    assert decode_frame_bytes(encode_frame(valid)) == valid


def test_protocol_rejects_caller_supplied_pid_and_creation_time(store):
    forged = {
        **runtime_reserve("reserve-forged-identity-0001"),
        "callerPid": 4,
        "callerProcessCreationFiletime": 1,
    }
    with pytest.raises(broker.BrokerError) as error:
        store.execute(forged, CALLER)
    assert error.value.code == "request_fields_invalid"


def test_schema_prevents_skipping_transitions_and_deleting_history(store):
    reserved = store.execute(runtime_reserve("reserve-trigger-0001"), CALLER)
    reservation_id = reserved["result"]["reservationId"]
    connection = sqlite3.connect(store.path)
    try:
        with pytest.raises(sqlite3.IntegrityError):
            connection.execute(
                "UPDATE reservations SET state='completed', outcome='failed', "
                "completion_request_id='direct', completed_at_filetime=999999999999999999 "
                "WHERE reservation_id=?",
                (reservation_id,),
            )
        with pytest.raises(sqlite3.IntegrityError, match="cannot be deleted"):
            connection.execute(
                "DELETE FROM reservations WHERE reservation_id=?", (reservation_id,)
            )
    finally:
        connection.rollback()
        connection.close()

    assert set(broker._ACTION_FIELDS) == {
        "reserve-runtime",
        "reserve-improve",
        "claim",
        "complete",
        "status",
    }


def test_lease_identity_and_bounds_are_immutable(store):
    exact = store.register_lease(lease_registration())
    assert exact["idempotent"] is True

    changed = lease_registration(max_run_count=11)
    with pytest.raises(broker.BrokerError) as conflict:
        store.register_lease(changed)
    assert conflict.value.code == "lease_identity_conflict"

    connection = sqlite3.connect(store.path)
    try:
        with pytest.raises(sqlite3.IntegrityError, match="immutable"):
            connection.execute(
                "UPDATE leases SET max_run_count=999 WHERE authorization_digest=?",
                (AUTHORIZATION_DIGEST,),
            )
    finally:
        connection.rollback()
        connection.close()


def test_missing_or_corrupt_initialized_store_fails_closed(tmp_path):
    store = broker.ConsumptionStore.initialize(
        tmp_path / "consumption.sqlite3",
        tmp_path / "consumption.sqlite3.initialized",
    )
    store.path.unlink()
    with pytest.raises(broker.StoreUnavailable):
        store.validate()

    corrupt_root = tmp_path / "corrupt"
    corrupt_root.mkdir()
    corrupt = broker.ConsumptionStore.initialize(
        corrupt_root / "consumption.sqlite3",
        corrupt_root / "consumption.sqlite3.initialized",
    )
    corrupt.path.write_bytes(b"not a sqlite database")
    with pytest.raises(broker.StoreUnavailable):
        corrupt.validate()


def test_registration_file_is_strict_json_and_hash_pinned(tmp_path):
    path = tmp_path / "registration.json"
    content = json.dumps(lease_registration(), sort_keys=True).encode("utf-8")
    path.write_bytes(content)
    digest = __import__("hashlib").sha256(content).hexdigest()
    assert broker._load_registration(path, digest)["leaseId"] == LEASE_ID
    with pytest.raises(broker.BrokerError) as mismatch:
        broker._load_registration(path, "0" * 64)
    assert mismatch.value.code == "registration_hash_mismatch"


def test_client_retry_reuses_exact_request_after_possible_committed_response_loss():
    payload = broker_request_payload(
        "reserve-runtime",
        authorization_digest=AUTHORIZATION_DIGEST,
        lease_id=LEASE_ID,
        purpose=broker.RUNTIME_RESERVATION_PURPOSE,
        kind="runtime",
        battleCount=3,
        cycleCount=1,
        maxConcurrentBattles=3,
        supervisorInstanceId=SUPERVISOR_INSTANCE_ID,
    )

    class LostFirstResponseClient:
        def __init__(self):
            self.requests = []

        def request(self, request_payload):
            self.requests.append(json.loads(json.dumps(request_payload)))
            if len(self.requests) == 1:
                raise OSError("response lost after commit")
            return {
                "schemaVersion": "fouler-lease-broker-response/v1",
                "ok": True,
                "requestId": request_payload["requestId"],
                "action": request_payload["action"],
                "result": {"reservationId": "res-" + "a" * 32},
            }

    client = LostFirstResponseClient()
    response = request_with_retry(payload, client=client)

    assert response["ok"] is True
    assert client.requests == [payload, payload]
    assert client.requests[0]["requestId"] == client.requests[1]["requestId"]


def test_client_recovers_lost_committed_response_through_status_lookup():
    payload = broker_request_payload(
        "reserve-runtime",
        authorization_digest=AUTHORIZATION_DIGEST,
        lease_id=LEASE_ID,
        purpose=broker.RUNTIME_RESERVATION_PURPOSE,
        kind="runtime",
        battleCount=3,
        cycleCount=1,
        maxConcurrentBattles=3,
        supervisorInstanceId=SUPERVISOR_INSTANCE_ID,
    )
    committed = {
        "schemaVersion": "fouler-lease-broker-response/v1",
        "ok": True,
        "requestId": payload["requestId"],
        "action": payload["action"],
        "result": synthetic_binding("res-" + "c" * 32),
    }

    class LostResponsesThenStatusClient:
        def __init__(self):
            self.requests = []

        def request(self, request_payload):
            self.requests.append(json.loads(json.dumps(request_payload)))
            if request_payload["action"] != "status":
                raise OSError("committed response was lost")
            return {
                "schemaVersion": "fouler-lease-broker-response/v1",
                "ok": True,
                "requestId": request_payload["requestId"],
                "action": "status",
                "result": {
                    "lookupType": "request",
                    "lookupId": payload["requestId"],
                    "found": True,
                    "originalAction": payload["action"],
                    "response": committed,
                },
            }

    client = LostResponsesThenStatusClient()
    recovered = request_with_retry(payload, client=client, attempts=2)

    assert recovered == committed
    assert [item["action"] for item in client.requests] == [
        "reserve-runtime",
        "reserve-runtime",
        "status",
    ]


@pytest.mark.parametrize("field", ["requestId", "action"])
def test_client_rejects_response_not_bound_to_exact_request(field):
    payload = broker_request_payload(
        "claim",
        authorization_digest=AUTHORIZATION_DIGEST,
        lease_id=LEASE_ID,
        **synthetic_binding(),
    )

    class MismatchedResponseClient:
        def request(self, request_payload):
            response = {
                "schemaVersion": "fouler-lease-broker-response/v1",
                "ok": True,
                "requestId": request_payload["requestId"],
                "action": request_payload["action"],
                "result": {},
            }
            response[field] = "wrong"
            return response

    with pytest.raises(ProtocolError, match=field):
        request_with_retry(payload, client=MismatchedResponseClient())


def test_broker_error_text_is_bounded_to_structured_error_fields():
    assert response_error_text(
        {"ok": False, "error": {"code": "run_bound_exhausted", "message": "bound reached"}}
    ) == "run_bound_exhausted: bound reached"


def test_pipe_sddl_separates_create_instance_from_runtime_data_rights():
    runtime_sid = "S-1-5-21-100-200-300-400"
    broker_sid = "S-1-5-19"

    assert broker._pipe_security_sddl(runtime_sid, broker_sid) == (
        "D:P(A;;0x0010019f;;;S-1-5-19)"
        "(A;;0x00100083;;;S-1-5-21-100-200-300-400)"
    )
    assert 0x00100083 & 0x4 == 0
    assert broker.WindowsPipeServer.MAX_PIPE_INSTANCES == (
        broker.WindowsPipeServer.MAX_ACTIVE_WORKERS + 2
    )


def test_broker_command_line_must_name_exact_script_and_serve(tmp_path):
    executable = str(tmp_path / "python.exe")
    script = str(tmp_path / "fouler_lease_broker.py")

    assert lease_client._is_expected_broker_command(
        [executable, script, "--store-path", "store.db", "serve"], executable, script
    )
    assert not lease_client._is_expected_broker_command(
        [str(tmp_path / "other-python.exe"), script, "serve"], executable, script
    )
    assert not lease_client._is_expected_broker_command(
        [executable, str(tmp_path / "other.py"), "serve"], executable, script
    )
    assert not lease_client._is_expected_broker_command(
        [executable, script, "check-store"], executable, script
    )


def test_prepare_broker_client_identity_grants_only_resolved_service_sid(monkeypatch):
    calls = []

    class FakeApi:
        def resolve_account_sid(self, account):
            calls.append(("resolve", account))
            return "S-1-5-80-1234"

        def grant_current_process_query_to_sid(self, sid):
            calls.append(("grant", sid))

    monkeypatch.setattr(lease_client, "_WINDOWS_API", FakeApi())

    assert lease_client.prepare_broker_client_identity(
        service_name="HERMES-FoulerLeaseBroker",
        expected_service_sid="S-1-5-80-1234",
    ) == "S-1-5-80-1234"
    assert calls == [
        ("resolve", r"NT SERVICE\HERMES-FoulerLeaseBroker"),
        ("grant", "S-1-5-80-1234"),
    ]


def test_prepare_broker_client_identity_fails_before_grant_on_sid_mismatch(monkeypatch):
    granted = []

    class FakeApi:
        @staticmethod
        def resolve_account_sid(_account):
            return "S-1-5-80-1234"

        @staticmethod
        def grant_current_process_query_to_sid(sid):
            granted.append(sid)

    monkeypatch.setattr(lease_client, "_WINDOWS_API", FakeApi())

    with pytest.raises(lease_client.ServerIdentityError, match="service SID"):
        lease_client.prepare_broker_client_identity(
            expected_service_sid="S-1-5-80-9999"
        )
    assert granted == []


def test_default_client_prepares_identity_before_waiting_for_pipe(monkeypatch):
    calls = []

    class FakeKernel:
        @staticmethod
        def WaitNamedPipeW(_pipe_name, _timeout_ms):
            calls.append("wait")
            return False

    class FakeApi:
        kernel32 = FakeKernel()

        @staticmethod
        def last_error(operation):
            return OSError(operation)

    monkeypatch.setattr(lease_client, "_WINDOWS_API", FakeApi())
    monkeypatch.setattr(
        lease_client,
        "prepare_broker_client_identity",
        lambda **_kwargs: calls.append("prepare"),
    )

    with pytest.raises(OSError, match="WaitNamedPipeW"):
        LeaseBrokerClient().request(
            {
                "schemaVersion": PROTOCOL_VERSION,
                "action": "status",
                "requestId": "prepare-before-pipe-0001",
            }
        )

    assert calls == ["prepare", "wait"]


def test_venv_base_executable_comes_from_one_bounded_manifested_value(tmp_path):
    scripts = tmp_path / ".venv" / "Scripts"
    scripts.mkdir(parents=True)
    launcher = scripts / "python.exe"
    launcher.write_bytes(b"launcher")
    expected_base = Path(sys.executable).resolve()
    (scripts.parent / "pyvenv.cfg").write_text(
        f"home = {expected_base.parent}\nexecutable = {expected_base}\n",
        encoding="utf-8",
    )

    assert lease_client._venv_base_executable(str(launcher)) == str(expected_base)

    (scripts.parent / "pyvenv.cfg").write_text(
        f"executable = {expected_base}\nexecutable = {expected_base}\n",
        encoding="utf-8",
    )
    with pytest.raises(
        lease_client.ServerIdentityError,
        match="must name one base executable",
    ):
        lease_client._venv_base_executable(str(launcher))


def _current_windows_user_sid() -> str:
    account = os.environ.get("USERNAME") or os.getlogin()
    return lease_client._windows_api().resolve_account_sid(account)


@pytest.mark.skipif(os.name != "nt", reason="kernel-object DACL test is Windows-specific")
def test_actual_attestation_acl_grants_are_accepted_for_process_and_token():
    sid = _current_windows_user_sid()
    server = broker.WindowsPipeServer(_ProbeStore(), sid, broker_sid=sid)

    server._grant_runtime_process_query(os.getpid())
    server._grant_runtime_token_query(os.getpid())


class _ProbeStore:
    def validate(self):
        return {"ok": True}

    def execute(self, payload, caller):
        return {
            "schemaVersion": lease_client.RESPONSE_VERSION,
            "ok": True,
            "requestId": payload.get("requestId"),
            "action": payload.get("action"),
            "result": {"callerProcessId": caller.process_id},
        }


@pytest.mark.skipif(os.name != "nt", reason="named-pipe DACL test is Windows-specific")
def test_actual_pipe_dacl_allows_first_request_without_write_attributes(
    monkeypatch,
):
    pipe_name = rf"\\.\pipe\FoulerLeaseBroker.test.{uuid.uuid4().hex}"
    monkeypatch.setattr(broker, "PIPE_NAME", pipe_name)
    monkeypatch.setattr(
        lease_client, "verify_broker_server_identity", lambda *_args, **_kwargs: None
    )
    sid = _current_windows_user_sid()
    server = broker.WindowsPipeServer(
        _ProbeStore(), sid, pipe_name=pipe_name, broker_sid=sid
    )
    handle = server._create_pipe(first_instance=True)
    errors = []

    def serve_one() -> None:
        try:
            server._connect(handle)
            server._serve_connection(handle)
        except Exception as exc:  # pragma: no cover - asserted below
            errors.append(exc)

    worker = threading.Thread(target=serve_one, daemon=True)
    worker.start()
    client = LeaseBrokerClient(pipe_name=pipe_name, timeout_ms=2_000)
    response = client.request(
        {
            "schemaVersion": PROTOCOL_VERSION,
            "action": "probe",
            "requestId": "probe-first-dacl-0001",
        }
    )
    worker.join(timeout=3)

    assert response["ok"] is True
    assert not worker.is_alive()
    assert errors == []


@pytest.mark.skipif(os.name != "nt", reason="named-pipe DACL test is Windows-specific")
def test_actual_pipe_dacl_allows_first_and_second_listener_creation(monkeypatch):
    pipe_name = rf"\\.\pipe\FoulerLeaseBroker.test.{uuid.uuid4().hex}"
    monkeypatch.setattr(broker, "PIPE_NAME", pipe_name)
    sid = _current_windows_user_sid()
    server = broker.WindowsPipeServer(
        _ProbeStore(), sid, pipe_name=pipe_name, broker_sid=sid
    )
    first = server._create_pipe(first_instance=True)
    second = None
    try:
        second = server._create_pipe(first_instance=False)
        assert first != server.INVALID_HANDLE_VALUE
        assert second != server.INVALID_HANDLE_VALUE
    finally:
        server.kernel32.CloseHandle(first)
        if second is not None:
            server.kernel32.CloseHandle(second)


@pytest.mark.skipif(os.name != "nt", reason="named-pipe deadline test is Windows-specific")
def test_stalled_reader_expires_and_releases_server_worker_slot(monkeypatch):
    pipe_name = rf"\\.\pipe\FoulerLeaseBroker.test.{uuid.uuid4().hex}"
    monkeypatch.setattr(broker, "PIPE_NAME", pipe_name)
    sid = _current_windows_user_sid()
    server = broker.WindowsPipeServer(
        _ProbeStore(), sid, pipe_name=pipe_name, broker_sid=sid
    )
    server.CONNECTION_DEADLINE_SECONDS = 0.05
    server._worker_slots = threading.BoundedSemaphore(1)
    assert server._worker_slots.acquire(blocking=False)
    handle = server._create_pipe(first_instance=True)
    errors = []

    def serve_stalled() -> None:
        try:
            server._connect(handle)
            server._serve_connection_bounded(handle)
        except Exception as exc:
            errors.append(exc)

    worker = threading.Thread(target=serve_stalled, daemon=True)
    started = time.monotonic()
    worker.start()
    api = lease_client._windows_api()
    assert api.kernel32.WaitNamedPipeW(pipe_name, 2_000)
    client_handle = api.kernel32.CreateFileW(
        pipe_name,
        api.FILE_READ_DATA | api.FILE_WRITE_DATA,
        0,
        None,
        api.OPEN_EXISTING,
        api.SECURITY_SQOS_PRESENT | api.SECURITY_IDENTIFICATION,
        None,
    )
    assert client_handle != api.INVALID_HANDLE_VALUE
    try:
        worker.join(timeout=3)
        assert not worker.is_alive()
        assert time.monotonic() - started < 2.0
        if errors:
            assert isinstance(errors[0], ProtocolError)
            assert errors[0].code == "pipe_timeout"
        assert server._worker_slots.acquire(blocking=False)
        server._worker_slots.release()
    finally:
        api.close(client_handle)


@pytest.mark.skipif(os.name != "nt", reason="overlapped-I/O test is Windows-specific")
def test_stalled_writer_is_cancelled_and_releases_server_worker_slot(monkeypatch):
    class StalledKernel:
        def __init__(self):
            self.waits = 0
            self.cancelled = 0
            self.closed = 0

        def CreateEventW(self, *_args):
            return 100

        def WriteFile(self, *_args):
            ctypes.set_last_error(lease_client._WindowsApi.ERROR_IO_PENDING)
            return False

        def WaitForSingleObject(self, _event, _timeout):
            self.waits += 1
            if self.waits == 1:
                return lease_client._WindowsApi.WAIT_TIMEOUT
            return lease_client._WindowsApi.WAIT_OBJECT_0

        def CancelIoEx(self, *_args):
            self.cancelled += 1
            return True

        def CloseHandle(self, _handle):
            self.closed += 1
            return True

    class StalledApi:
        ERROR_IO_PENDING = lease_client._WindowsApi.ERROR_IO_PENDING
        ERROR_OPERATION_ABORTED = lease_client._WindowsApi.ERROR_OPERATION_ABORTED
        ERROR_NOT_FOUND = lease_client._WindowsApi.ERROR_NOT_FOUND
        WAIT_OBJECT_0 = lease_client._WindowsApi.WAIT_OBJECT_0
        WAIT_TIMEOUT = lease_client._WindowsApi.WAIT_TIMEOUT
        CANCELLATION_GRACE_MS = 10
        INFINITE = lease_client._WindowsApi.INFINITE

        def __init__(self):
            self.kernel32 = StalledKernel()

        def close(self, handle):
            self.kernel32.CloseHandle(handle)

        @staticmethod
        def last_error(operation):
            return OSError(operation)

        @staticmethod
        def error_from_code(operation, code):
            return OSError(code, operation)

    fake_api = StalledApi()
    monkeypatch.setattr(lease_client, "_WINDOWS_API", fake_api)
    server = object.__new__(broker.WindowsPipeServer)
    server._worker_slots = threading.BoundedSemaphore(1)
    assert server._worker_slots.acquire(blocking=False)
    content = ctypes.create_string_buffer(b"response")
    server._serve_connection = lambda _handle, deadline=None: lease_client._overlapped_transfer(
        42,
        content,
        len(b"response"),
        write=True,
        deadline=time.monotonic() + 1,
    )

    with pytest.raises(ProtocolError) as timeout:
        server._serve_connection_bounded(42)

    assert timeout.value.code == "pipe_timeout"
    assert fake_api.kernel32.cancelled == 1
    assert server._worker_slots.acquire(blocking=False)
    server._worker_slots.release()


@pytest.mark.skipif(os.name != "nt", reason="named-pipe deadline test is Windows-specific")
def test_client_read_deadline_expires_when_server_never_responds(monkeypatch):
    pipe_name = rf"\\.\pipe\FoulerLeaseBroker.test.{uuid.uuid4().hex}"
    monkeypatch.setattr(broker, "PIPE_NAME", pipe_name)
    monkeypatch.setattr(
        lease_client, "verify_broker_server_identity", lambda *_args, **_kwargs: None
    )
    sid = _current_windows_user_sid()
    server = broker.WindowsPipeServer(
        _ProbeStore(), sid, pipe_name=pipe_name, broker_sid=sid
    )
    handle = server._create_pipe(first_instance=True)

    def accept_without_response() -> None:
        server._connect(handle)
        time.sleep(0.5)
        server.kernel32.CloseHandle(handle)

    worker = threading.Thread(target=accept_without_response, daemon=True)
    worker.start()
    client = LeaseBrokerClient(pipe_name=pipe_name, timeout_ms=75)
    started = time.monotonic()
    with pytest.raises(ProtocolError) as timeout:
        client.request(
            {
                "schemaVersion": PROTOCOL_VERSION,
                "action": "probe",
                "requestId": "probe-client-deadline-0001",
            }
        )
    elapsed = time.monotonic() - started
    worker.join(timeout=2)

    assert timeout.value.code == "pipe_timeout"
    assert elapsed < 1.0
    assert not worker.is_alive()
