"""The authority gate must not kill a season over one transient failure.

2026-07-22 live incident: 2h45m into a healthy 4-cycle unattended season on
release 590330b2, a single ``--verify-deployment-checkout`` git invocation
failed under battle load ("checkout tree is unavailable or malformed") and the
account-season gate hard-exited the supervisor on that one iteration, orphaning
three in-flight battles. The release tree verified byte-identical to the
deployment receipt minutes later — the checkout was never actually wrong.

``authority_gate_decision`` pins the replacement contract.
"""

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

import devstream_session  # noqa: E402


def decide(**kwargs) -> str:
    return devstream_session.authority_gate_decision(**kwargs)


def test_healthy_check_proceeds_and_ignores_history():
    assert decide(account_season_ok=True, battles_in_flight=False, failure_streak=0) == "proceed"
    assert decide(account_season_ok=True, battles_in_flight=True, failure_streak=99) == "proceed"


def test_single_transient_failure_retries_instead_of_exiting():
    # The 09:40Z incident: streak 1, no reason to die yet.
    assert decide(account_season_ok=False, battles_in_flight=False, failure_streak=1) == "retry"
    assert decide(account_season_ok=False, battles_in_flight=False, failure_streak=2) == "retry"


def test_persistent_failure_still_fails_closed_when_idle():
    assert decide(account_season_ok=False, battles_in_flight=False, failure_streak=3) == "exit"
    assert decide(account_season_ok=False, battles_in_flight=False, failure_streak=10) == "exit"


def test_in_flight_battles_are_never_orphaned_by_a_hard_exit():
    # Mirrors blocked-runtime-lease drain semantics: supervise until the
    # runtime drains, no matter how long the failure persists.
    for streak in (1, 3, 50):
        assert decide(account_season_ok=False, battles_in_flight=True, failure_streak=streak) == "retry"


def test_threshold_floor_is_at_least_one():
    # A misconfigured threshold of 0 must not turn every failure into an
    # instant exit bypassing the retry contract... nor loop forever: floor 1.
    assert (
        decide(
            account_season_ok=False,
            battles_in_flight=False,
            failure_streak=1,
            max_transient_failures=0,
        )
        == "exit"
    )


def test_supervise_loop_wires_the_gate():
    source = (ROOT / "scripts" / "devstream_session.py").read_text(encoding="utf-8")
    gate_call = source.find("decision = authority_gate_decision(")
    assert gate_call != -1, "cmd_supervise must consult authority_gate_decision"
    # The hard exit must be conditional on the gate's verdict, and a retry must
    # keep the loop alive.
    window = source[gate_call : gate_call + 900]
    assert 'if decision == "exit":' in window
    assert "continue" in window
