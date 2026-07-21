"""Regression tests for the two defects that made fouler unable to hold a stream.

Defect 1 -- a served stop request latched the runtime off forever.
``scripts/start_battle_supervisor_task.ps1`` refuses to launch while
``supervisor.stop`` exists and exits **0** while doing so, so once the supervisor
honored a stop request and left the file behind, every later
``Start-ScheduledTask`` reported ``LastTaskResult=0``, dropped back to ``Ready``,
and never reached Python. The supervisor now consumes the request as it serves
it, which keeps the refusal meaningful for an *unserved* stop while letting an
authorized start inside a live lease proceed.

Defect 2 -- ``completedLearningCycles`` under-reported a cycle the broker had
already banked. The in-process credit lands one poll iteration after the broker
records the reservation terminal, because the boundary iteration returns early
as ``result-persistence-grace`` without setting ``proofRefreshed``. Exiting
inside that window reported 0 for a lease that had just completed a 30-battle
cycle.
"""

import argparse
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

import devstream_session  # noqa: E402


def test_reconcile_never_lowers_the_reported_count(monkeypatch):
    payload: dict = {}
    monkeypatch.setattr(
        devstream_session,
        "runtime_lease_consumption_status",
        lambda lease_guard, *, run_count, max_concurrent_battles, max_cycles: {
            "ok": True,
            "status": {"successfulCycleCount": 1},
        },
    )
    result = devstream_session.reconcile_completed_learning_cycles(
        payload,
        lease_guard={"ok": True},
        args=argparse.Namespace(max_concurrent_battles=3),
        run_count=30,
        effective_max_cycles=4,
        in_process_count=3,
    )
    assert result == 3
    assert payload["completedLearningCyclesReconciliation"]["reconciled"] is False


def test_reconcile_survives_an_unavailable_broker(monkeypatch):
    payload: dict = {}

    def exploding_status(*a, **k):
        raise OSError("named pipe unavailable")

    monkeypatch.setattr(devstream_session, "runtime_lease_consumption_status", exploding_status)
    result = devstream_session.reconcile_completed_learning_cycles(
        payload,
        lease_guard={"ok": True},
        args=argparse.Namespace(max_concurrent_battles=3),
        run_count=30,
        effective_max_cycles=4,
        in_process_count=2,
    )
    assert result == 2
    assert (
        "named pipe unavailable"
        in payload["completedLearningCyclesReconciliation"]["error"]
    )


def test_reconcile_requires_a_validated_lease(monkeypatch):
    """Without a validated lease there is no authority to reconcile against."""

    payload: dict = {}

    def must_not_be_called(*a, **k):
        raise AssertionError("must not query the broker without a validated lease")

    monkeypatch.setattr(devstream_session, "runtime_lease_consumption_status", must_not_be_called)
    result = devstream_session.reconcile_completed_learning_cycles(
        payload,
        lease_guard={"ok": False},
        args=argparse.Namespace(max_concurrent_battles=3),
        run_count=30,
        effective_max_cycles=4,
        in_process_count=1,
    )
    assert result == 1
    assert payload["completedLearningCyclesReconciliation"]["reconciled"] is False
