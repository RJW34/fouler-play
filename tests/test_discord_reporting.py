import json
import importlib

from infrastructure.discord_reporting import (
    build_contract_message,
    build_contract_payload,
    format_payload_or_message,
    is_contract_message,
)


def test_contract_message_shape():
    message = build_contract_message(
        "CODE_FIX",
        "reporting path standardized",
        "central helper now builds the required message shape",
        "prevents ad hoc fragments from hitting the project channel",
        "pytest target=test_contract_message_shape",
        "wire remaining call sites if new reporting surfaces appear",
    )
    assert message.startswith("[CODE_FIX] reporting path standardized")
    assert "What happened:" in message
    assert "Why it matters:" in message
    assert "Proof:" in message
    assert "Remaining:" in message
    assert is_contract_message(message)


def test_payload_is_formatted_before_queue(monkeypatch, tmp_path):
    queue_file = tmp_path / "events_queue.json"
    monkeypatch.setenv("EVENT_QUEUE_FILE", str(queue_file))
    queue_file.write_text("[]", encoding="utf-8")

    import infrastructure.event_queue_lib as event_queue_lib
    event_queue_lib = importlib.reload(event_queue_lib)

    payload = build_contract_payload(
        "PROOF",
        "battle result win vs sample",
        "run_battle queued a result",
        "poster should not have to decode raw json",
        "battle_id=battle-gen9ou-1; result=win",
        "append replay later if available",
        source="unit-test",
    )

    event_id = event_queue_lib.queue_event("battle_result", "battles", payload, dedup_window_sec=0)
    assert event_id is not None

    pending = event_queue_lib.get_pending_events()
    assert len(pending) == 1
    content = pending[0]["content"]
    assert content.startswith("[PROOF] battle result win vs sample")
    assert "What happened: run_battle queued a result" in content
    assert "Proof: battle_id=battle-gen9ou-1; result=win" in content
    queue_file.unlink(missing_ok=True)


def test_payload_formatter_passes_through_contract_messages():
    message = build_contract_message(
        "PROGRESSION",
        "already formatted",
        "message already matches contract",
        "double-formatting would be noisy",
        "unit proof",
        "none",
    )
    assert format_payload_or_message(message) == message


def test_payload_formatter_converts_json_payload():
    payload = build_contract_payload(
        "PROOF",
        "loss review ready",
        "monitor generated a review",
        "reviews need proof-backed summaries",
        "replay=https://example.test/replay",
        "read saved report",
    )
    formatted = format_payload_or_message(payload)
    assert formatted.startswith("[PROOF] loss review ready")
    assert "What happened: monitor generated a review" in formatted
    assert "Remaining: read saved report" in formatted
