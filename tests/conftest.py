from __future__ import annotations

import pytest

from infrastructure import runtime_authorization
from tests.runtime_authority_testkit import write_test_controller_keyring


@pytest.fixture(autouse=True)
def fixed_controller_trust_store(tmp_path, monkeypatch):
    path = write_test_controller_keyring(tmp_path / "controller-keys.json")
    monkeypatch.setattr(runtime_authorization, "DEFAULT_TRUST_STORE_PATH", path)
    monkeypatch.setattr(
        runtime_authorization,
        "ALLOW_UNPROTECTED_TRUST_STORE_FOR_TESTS",
        True,
    )
    return path
