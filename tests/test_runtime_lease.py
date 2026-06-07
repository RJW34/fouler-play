import json
import os

import pytest

from infrastructure.runtime_lease import (
    LEASE_NAME_ENV,
    LEASE_TOKEN_ENV,
    RuntimeLease,
    RuntimeLeaseBusy,
    acquire_runtime_lease,
    lease_status,
)


def test_runtime_lease_acquires_and_releases_atomically(tmp_path, monkeypatch):
    monkeypatch.delenv(LEASE_TOKEN_ENV, raising=False)
    monkeypatch.delenv(LEASE_NAME_ENV, raising=False)

    lease = acquire_runtime_lease(holder="test", lease_dir=tmp_path)
    path = tmp_path / "fouler-runtime-lane.lease.json"

    assert path.exists()
    assert os.environ[LEASE_TOKEN_ENV] == lease.token
    assert lease_status(lease_dir=tmp_path)["alive"] is True

    lease.release()

    assert not path.exists()
    assert LEASE_TOKEN_ENV not in os.environ


def test_runtime_lease_refuses_live_other_holder(tmp_path, monkeypatch):
    monkeypatch.delenv(LEASE_TOKEN_ENV, raising=False)
    path = tmp_path / "fouler-runtime-lane.lease.json"
    path.write_text(json.dumps({"pid": os.getpid(), "token": "other", "holder": "already-running"}), encoding="utf-8")

    with pytest.raises(RuntimeLeaseBusy):
        acquire_runtime_lease(holder="second", lease_dir=tmp_path)


def test_runtime_lease_reclaims_dead_holder(tmp_path, monkeypatch):
    monkeypatch.delenv(LEASE_TOKEN_ENV, raising=False)
    path = tmp_path / "fouler-runtime-lane.lease.json"
    path.write_text(json.dumps({"pid": 999999999, "token": "dead", "holder": "old"}), encoding="utf-8")

    lease = acquire_runtime_lease(holder="new", lease_dir=tmp_path)

    assert lease.acquired is True
    assert json.loads(path.read_text(encoding="utf-8"))["holder"] == "new"
    lease.release()


def test_runtime_lease_allows_child_reentry_with_inherited_token(tmp_path, monkeypatch):
    first = acquire_runtime_lease(holder="supervisor", lease_dir=tmp_path)
    monkeypatch.setenv(LEASE_TOKEN_ENV, first.token)

    child = RuntimeLease(holder="improve_agent", lease_dir=tmp_path).acquire()

    assert child.reentrant is True
    assert child.acquired is False
    child.release()
    assert (tmp_path / "fouler-runtime-lane.lease.json").exists()
    first.release()
