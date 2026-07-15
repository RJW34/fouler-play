import copy
import json
import os

import pytest
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

from infrastructure import runtime_authorization as authority
from scripts import fouler_runtime_authority as authority_cli


def lease_payload():
    return {
        "schemaVersion": authority.RUNTIME_LEASE_SCHEMA_VERSION,
        "projectId": "fouler-play",
        "leaseId": "lease-test-0001",
        "approved": True,
        "allowedPurposes": ["devstream-start"],
        "proofWindow": {
            "startsAt": "2026-07-15T00:00:00+00:00",
            "expiresAt": "2026-07-15T01:00:00+00:00",
        },
        "battleScope": {"account": "DekuFoulerLab", "runCount": 1},
    }


@pytest.fixture
def signed_authority():
    private_key = Ed25519PrivateKey.generate()
    keyring = authority.build_keyring(
        private_key, "controller-key-01", "DEKU controller"
    )
    signed = authority.sign_runtime_lease(
        lease_payload(),
        private_key,
        "controller-key-01",
        "DEKU controller",
    )
    return private_key, keyring, signed


def blocker_codes(result):
    return set(result["blockerCodes"])


def test_valid_signature(signed_authority):
    _private_key, keyring, signed = signed_authority

    result = authority.verify_runtime_lease_authorization(signed, keyring)

    assert result["ok"] is True
    assert result["authorized"] is True
    assert result["blockers"] == []
    authorization = signed[authority.AUTHORIZATION_FIELD]
    assert set(authorization) == authority.AUTHORIZATION_FIELDS
    assert "=" not in authorization["signature"]
    assert result["publicKeyFingerprint"].startswith("sha256:")


@pytest.mark.parametrize(
    "mutation",
    [
        lambda payload: payload.__setitem__("leaseId", "lease-test-0002"),
        lambda payload: payload["proofWindow"].__setitem__(
            "expiresAt", "2026-07-15T02:00:00+00:00"
        ),
        lambda payload: payload["allowedPurposes"].append("run-py-battle-runner"),
        lambda payload: payload["battleScope"].__setitem__("runCount", 2),
        lambda payload: payload.__setitem__("approved", False),
    ],
)
def test_mutation_of_signed_fields_fails(signed_authority, mutation):
    _private_key, keyring, signed = signed_authority
    mutated = copy.deepcopy(signed)
    mutation(mutated)

    result = authority.verify_runtime_lease_authorization(mutated, keyring)

    assert result["ok"] is False
    assert "signature_invalid" in blocker_codes(result)


def test_unsigned_lease_fails(signed_authority):
    _private_key, keyring, _signed = signed_authority
    result = authority.verify_runtime_lease_authorization(lease_payload(), keyring)
    assert "authorization_missing" in blocker_codes(result)


def test_wrong_key_fails(signed_authority):
    _private_key, _keyring, signed = signed_authority
    wrong_keyring = authority.build_keyring(
        Ed25519PrivateKey.generate(), "controller-key-01", "DEKU controller"
    )
    result = authority.verify_runtime_lease_authorization(signed, wrong_keyring)
    assert "signature_invalid" in blocker_codes(result)


def test_revoked_key_fails(signed_authority):
    _private_key, keyring, signed = signed_authority
    keyring["keys"][0]["status"] = "revoked"
    result = authority.verify_runtime_lease_authorization(signed, keyring)
    assert "key_revoked" in blocker_codes(result)


@pytest.mark.parametrize(
    "change, expected",
    [
        (lambda auth: auth.__setitem__("signature", "not+base64"), "authorization_signature_malformed"),
        (lambda auth: auth.__setitem__("extra", True), "authorization_fields_invalid"),
        (lambda auth: auth.__setitem__("schemaVersion", "wrong/v1"), "authorization_schema_invalid"),
    ],
)
def test_malformed_authorization_fails(signed_authority, change, expected):
    _private_key, keyring, signed = signed_authority
    malformed = copy.deepcopy(signed)
    change(malformed[authority.AUTHORIZATION_FIELD])
    result = authority.verify_runtime_lease_authorization(malformed, keyring)
    assert expected in blocker_codes(result)


def test_missing_trust_store_fails(signed_authority, tmp_path):
    _private_key, _keyring, signed = signed_authority
    result = authority.verify_runtime_lease_authorization(
        signed, tmp_path / "missing-keyring.json"
    )
    assert "trust_store_missing" in blocker_codes(result)


def test_duplicate_json_trust_store_fails(signed_authority, tmp_path):
    _private_key, _keyring, signed = signed_authority
    path = tmp_path / "keyring.json"
    path.write_text(
        '{"schemaVersion":"fouler-controller-keyring/v1",'
        '"schemaVersion":"fouler-controller-keyring/v1","keys":[]}',
        encoding="utf-8",
    )
    result = authority.verify_runtime_lease_authorization(signed, path)
    assert "trust_store_duplicate_json_key" in blocker_codes(result)


def test_protected_verification_rejects_in_memory_keyring(monkeypatch, signed_authority):
    _private_key, keyring, signed = signed_authority
    monkeypatch.setattr(authority, "ALLOW_UNPROTECTED_TRUST_STORE_FOR_TESTS", False)

    result = authority.verify_runtime_lease_authorization(
        signed,
        keyring,
        require_protected_trust_store=True,
    )

    assert result["ok"] is False
    assert "trust_store_mapping_forbidden" in blocker_codes(result)


def test_keygen_cli_outputs_only_safe_metadata(tmp_path, capsys):
    private_path = tmp_path / "controller-key.pem"
    keyring_path = tmp_path / "controller-keyring.json"

    exit_code = authority_cli.main(
        [
            "keygen",
            "--private-key",
            str(private_path),
            "--keyring",
            str(keyring_path),
            "--key-id",
            "controller-key-01",
            "--issued-by",
            "DEKU controller",
        ]
    )

    output_text = capsys.readouterr().out
    output = json.loads(output_text)
    assert exit_code == 0
    assert output["ok"] is True
    assert set(output) == {
        "schemaVersion",
        "ok",
        "privateKeyPath",
        "keyringPath",
        "keyId",
        "publicKeyFingerprint",
    }
    assert "PRIVATE KEY" not in output_text
    assert private_path.read_text(encoding="ascii") not in output_text
    assert authority.verify_runtime_lease_authorization(
        authority.sign_runtime_lease(
            lease_payload(),
            private_path,
            "controller-key-01",
            "DEKU controller",
        ),
        keyring_path,
    )["ok"] is True


def test_keygen_cli_never_overwrites(tmp_path, capsys):
    private_path = tmp_path / "controller-key.pem"
    keyring_path = tmp_path / "controller-keyring.json"
    private_path.write_text("sentinel", encoding="ascii")

    exit_code = authority_cli.main(
        [
            "keygen",
            "--private-key",
            str(private_path),
            "--keyring",
            str(keyring_path),
            "--key-id",
            "controller-key-01",
            "--issued-by",
            "DEKU controller",
        ]
    )

    output_text = capsys.readouterr().out
    output = json.loads(output_text)
    assert exit_code == 2
    assert output["blockers"][0]["code"] == "output_exists"
    assert private_path.read_text(encoding="ascii") == "sentinel"
    assert not keyring_path.exists()
    assert "PRIVATE KEY" not in output_text


def test_windows_trust_acl_allows_runtime_read_only(monkeypatch, tmp_path):
    monkeypatch.setattr(
        authority,
        "_windows_acl_snapshot",
        lambda _path: {
            "owner": r"BUILTIN\Administrators",
            "protected": True,
            "access": [
                {
                    "identity": r"NT AUTHORITY\SYSTEM",
                    "type": "Allow",
                    "rights": "FullControl",
                },
                {
                    "identity": r"JIGGLYPUFF\devstream-live",
                    "type": "Allow",
                    "rights": "ReadAndExecute, Synchronize",
                },
            ],
        },
    )

    assert authority._windows_acl_blockers(tmp_path, label="trust store") == []


def test_windows_trust_acl_rejects_runtime_write_access(monkeypatch, tmp_path):
    monkeypatch.setattr(
        authority,
        "_windows_acl_snapshot",
        lambda _path: {
            "owner": r"JIGGLYPUFF\devstream-live",
            "protected": True,
            "access": [
                {
                    "identity": r"JIGGLYPUFF\devstream-live",
                    "type": "Allow",
                    "rights": "Modify, Synchronize",
                }
            ],
        },
    )

    blockers = authority._windows_acl_blockers(tmp_path, label="trust store")

    assert "trust store owner is not Administrators or SYSTEM" in blockers
    assert any("write-capable" in item for item in blockers)


@pytest.mark.skipif(os.name != "nt", reason="real ACL probe is Windows-specific")
def test_windows_acl_snapshot_uses_real_target_path(tmp_path):
    path = tmp_path / "keyring.json"
    path.write_text("{}", encoding="utf-8")

    snapshot = authority._windows_acl_snapshot(path)

    assert snapshot["owner"]
    assert isinstance(snapshot["access"], (dict, list))


def _protected_acl_snapshot():
    return {
        "owner": "S-1-5-18",
        "ownerSid": "S-1-5-18",
        "protected": True,
        "entriesValid": True,
        "access": [
            {
                "type": "Allow",
                "sid": "S-1-5-18",
                "accessMask": 0x1F01FF,
                "inherited": False,
            },
            {
                "type": "Allow",
                "sid": "S-1-5-21-100-200-300-400",
                "accessMask": 0x1200A9,
                "inherited": False,
            },
        ],
    }


class _FakeStableTrustStore:
    def __init__(self, identities):
        self.identity_values = list(identities)
        self.events = []

    def __enter__(self):
        return self

    def __exit__(self, *_error):
        self.events.append("close")

    def identities(self):
        self.events.append("identities")
        return self.identity_values.pop(0)

    def acl_snapshots(self):
        self.events.append("acl")
        return _protected_acl_snapshot(), _protected_acl_snapshot()

    def read(self, max_bytes):
        self.events.append("read")
        assert max_bytes == authority._MAX_TRUST_STORE_BYTES
        return b'{"schemaVersion":"fouler-controller-keyring/v1","keys":[]}'


def test_protected_windows_trust_store_reads_same_stable_object(monkeypatch, tmp_path):
    identity = ((1, 10, 0x10), (1, 20, 0))
    stable = _FakeStableTrustStore([identity, identity])
    monkeypatch.setattr(
        authority, "_open_stable_windows_trust_store", lambda _path: stable
    )

    content = authority._read_protected_windows_trust_store(tmp_path / "keyring.json")

    assert content.startswith(b'{"schemaVersion"')
    assert stable.events == ["identities", "acl", "read", "identities", "acl", "close"]


def test_protected_windows_trust_store_rejects_replacement(monkeypatch, tmp_path):
    before = ((1, 10, 0x10), (1, 20, 0))
    after = ((1, 10, 0x10), (1, 21, 0))
    stable = _FakeStableTrustStore([before, after])
    monkeypatch.setattr(
        authority, "_open_stable_windows_trust_store", lambda _path: stable
    )

    with pytest.raises(authority._AuthorityInputError) as error:
        authority._read_protected_windows_trust_store(tmp_path / "keyring.json")

    assert error.value.code == "trust_store_unstable"
    assert "identity" in str(error.value)


def test_protected_windows_trust_store_rejects_reparse_ancestry(
    monkeypatch, tmp_path
):
    def reject_reparse(_path):
        raise authority._AuthorityInputError(
            "trust_store_unstable",
            "trust store path ancestry contains a reparse point",
        )

    monkeypatch.setattr(authority, "_open_stable_windows_trust_store", reject_reparse)

    with pytest.raises(authority._AuthorityInputError) as error:
        authority._read_protected_windows_trust_store(tmp_path / "keyring.json")

    assert error.value.code == "trust_store_unstable"
    assert "reparse point" in str(error.value)
