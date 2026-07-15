from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any, Mapping

from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

from infrastructure import runtime_authorization


TEST_CONTROLLER_KEY_ID = "deku-test-controller-0001"
TEST_CONTROLLER_ISSUER = "deku-test@controller"
TEST_PRIVATE_KEY = Ed25519PrivateKey.from_private_bytes(
    hashlib.sha256(b"fouler-play deterministic test controller key v1").digest()
)


def test_controller_keyring() -> dict[str, Any]:
    return runtime_authorization.build_keyring(
        TEST_PRIVATE_KEY,
        TEST_CONTROLLER_KEY_ID,
        TEST_CONTROLLER_ISSUER,
    )


def sign_test_runtime_lease(payload: Mapping[str, Any]) -> dict[str, Any]:
    unsigned = dict(payload)
    unsigned["schemaVersion"] = runtime_authorization.RUNTIME_LEASE_SCHEMA_VERSION
    return runtime_authorization.sign_runtime_lease(
        unsigned,
        key=TEST_PRIVATE_KEY,
        key_id=TEST_CONTROLLER_KEY_ID,
        issued_by=TEST_CONTROLLER_ISSUER,
    )


def write_test_controller_keyring(path: Path) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(test_controller_keyring(), indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    return path
