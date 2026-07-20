"""The self-learning and engine-change notifications the owner asked for."""

import json

from infrastructure import learn_loop_events


def _capture(monkeypatch):
    queued = []

    def fake_queue_event(event_type, channel, content, **kwargs):
        queued.append(
            {
                "event_type": event_type,
                "channel": channel,
                "content": content,
                "kwargs": kwargs,
            }
        )
        return f"id-{len(queued)}"

    import infrastructure.event_queue_lib as event_queue_lib

    monkeypatch.setattr(event_queue_lib, "queue_event", fake_queue_event)
    return queued


def _what(entry):
    return json.loads(entry["content"])["what_happened"]


def test_cycle_start_and_finish_are_reported(monkeypatch):
    queued = _capture(monkeypatch)

    learn_loop_events.emit_learn_cycle_started(cycle_index=7, target_issue="switch regret")
    learn_loop_events.emit_learn_cycle_finished(
        cycle_index=7, outcome="accepted", issue="switch regret", duration_sec=42.4
    )

    assert [entry["event_type"] for entry in queued] == [
        "learn_cycle_started",
        "learn_cycle_finished",
    ]
    assert "cycle 7" in _what(queued[0])
    assert "switch regret" in _what(queued[0])
    assert "accepted" in _what(queued[1])


def test_event_types_avoid_gen9_validation_and_local_only_routing():
    """Names are load-bearing.

    event_types containing 'report'/'summary'/'analysis'/'proof' are pulled into
    Gen9 content validation and can be rejected for lacking Pokemon proof text,
    and the names in ROUTINE_LOCAL_ONLY_EVENT_TYPES are never transported at
    all. Either mistake produces a notification that silently never arrives.
    """
    import infrastructure.event_poster as event_poster

    for event_type in ("learn_cycle_started", "learn_cycle_finished", "engine_change_explained"):
        assert not any(
            marker in event_type
            for marker in event_poster.GEN9_VALIDATED_EVENT_TYPE_MARKERS
        ), event_type
        assert event_type not in event_poster.ROUTINE_LOCAL_ONLY_EVENT_TYPES


def test_engine_change_reports_all_five_fields(monkeypatch):
    queued = _capture(monkeypatch)

    learn_loop_events.emit_engine_change_explained(
        change_id="fouler-change-abc",
        hypothesis="Switches are chosen without accounting for hazard chip.",
        change="Weight hazard damage in the switch evaluator.",
        predicted_effect="Fewer avoidable switch KOs; ladder win rate up.",
        measured_result=None,
        verdict="pending",
        target_file="fp/search/select.py",
    )

    what = _what(queued[0])
    assert "Hypothesis:" in what
    assert "Change:" in what
    assert "Predicted effect:" in what
    # Unmeasured must SAY unmeasured, never be omitted into looking like success.
    assert "Measured result: not yet measured" in what
    assert "Verdict: pending -> kept pending measurement" in what


def test_refuted_change_reports_as_reverted(monkeypatch):
    queued = _capture(monkeypatch)

    learn_loop_events.emit_engine_change_explained(
        hypothesis="h",
        change="c",
        predicted_effect="p",
        measured_result="win rate moved -2.1pts over 600 battles",
        verdict="refuted",
    )

    what = _what(queued[0])
    assert "Verdict: refuted -> reverted" in what
    assert "win rate moved -2.1pts" in what


def test_unknown_verdict_degrades_to_pending_not_to_success(monkeypatch):
    queued = _capture(monkeypatch)

    learn_loop_events.emit_engine_change_explained(
        hypothesis="h", change="c", predicted_effect="p", verdict="looks-great"
    )

    assert "Verdict: pending" in _what(queued[0])


def test_reporting_failure_never_breaks_the_learning_loop(monkeypatch):
    import infrastructure.event_queue_lib as event_queue_lib

    def boom(*args, **kwargs):
        raise RuntimeError("queue is down")

    monkeypatch.setattr(event_queue_lib, "queue_event", boom)

    # Returns None rather than propagating: a reporting outage must not stop the
    # bot from learning.
    assert learn_loop_events.emit_learn_cycle_started(cycle_index=1) is None
    assert learn_loop_events.emit_learn_cycle_finished(cycle_index=1, outcome="accepted") is None


def test_events_can_be_disabled(monkeypatch):
    queued = _capture(monkeypatch)
    monkeypatch.setattr(learn_loop_events, "LEARN_LOOP_EVENTS_ENABLED", False)

    assert learn_loop_events.emit_learn_cycle_started(cycle_index=1) is None
    assert queued == []
