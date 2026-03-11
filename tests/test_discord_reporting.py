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
        "pytest target=test_contract_message_shape; source=unit-test",
        "wire remaining call sites if new reporting surfaces appear",
    )
    assert message.startswith("[CODE_FIX] **reporting path standardized**")
    assert "📝 **What happened:**\ncentral helper now builds the required message shape" in message
    assert "🎯 **Why it matters:**\nprevents ad hoc fragments from hitting the project channel" in message
    assert "🔎 **Proof:**\n- pytest target=test_contract_message_shape\n- source `unit-test`" in message
    assert "⏭️ **Remaining:**\n- wire remaining call sites if new reporting surfaces appear" in message
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
    assert content.startswith("[PROOF] **battle result win vs sample**")
    assert "📝 **What happened:**\nrun_battle queued a result" in content
    assert "🔎 **Proof:**\n- source `unit-test`\n- battle `1`" in content
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
    assert formatted.startswith("[PROOF] **loss review ready**")
    assert "📝 **What happened:**\nmonitor generated a review" in formatted
    assert "⏭️ **Remaining:**\n- read saved report" in formatted


def test_payload_formatter_summarizes_battle_result_payload():
    payload = build_contract_payload(
        "PROOF",
        "battle result win vs Sample Opp",
        "run_battle finalized battle-gen9ou-123 and queued the outcome for Discord delivery.",
        "Battle-result reporting should include machine-readable proof without leaving the poster to infer context from raw JSON blobs.",
        "battle_id=battle-gen9ou-123; result=win; team_file=fat-team-1-stall.txt; opponent=Sample Opp; turns=37",
        "Poster can append replay/ELO context if available before or after posting this result.",
        source="fp.run_battle",
        battle_id="battle-gen9ou-123",
        result="win",
        team_file="fat-team-1-stall.txt",
        opponent="Sample Opp",
        turns=37,
    )
    formatted = format_payload_or_message(payload)
    assert formatted.startswith("[PROOF] **battle result win vs Sample Opp**")
    assert "📝 **What happened:**\nbattle finished win vs Sample Opp using 1 stall in 37 turns" in formatted
    assert "🎯 **Why it matters:**\nbattle outcomes are only useful in Discord if the proof is scannable without decoding raw payloads" in formatted
    assert "- battle `123`" in formatted
    assert "- win vs Sample Opp 37 turns" in formatted
    assert "- team `1 stall`" in formatted
    assert "- source `fp.run_battle`" in formatted
    assert "⏭️ **Remaining:**\n- append replay or ladder delta if it becomes available" in formatted


def test_payload_formatter_summarizes_batch_payload_without_blob_dump():
    payload = build_contract_payload(
        "PROOF",
        "batch complete 2-1",
        "bot_monitor closed a live batch with 3 battle(s) and queued the concise Discord summary.",
        "Routine batch updates should show the scoreline, replay coverage, and follow-up work without dumping the raw multiline summary blob into Proof.",
        "✅ vs A https://replay.pokemonshowdown.com/battle-gen9ou-111\n❌ vs B https://replay.pokemonshowdown.com/battle-gen9ou-222\n✅ vs C",
        "Loss review queue: 1 replay(s) pending deeper analysis.",
        source="bot_monitor.batch_complete",
        batch_results=[
            ("A", "won", "https://replay.pokemonshowdown.com/battle-gen9ou-111"),
            ("B", "lost", "https://replay.pokemonshowdown.com/battle-gen9ou-222"),
            ("C", "won", None),
        ],
        analysis_count=1,
    )
    formatted = format_payload_or_message(payload)
    assert formatted.startswith("[PROOF] **batch complete 2-1**")
    assert "📝 **What happened:**\nbot completed a battle batch at 2-1 and queued the live summary" in formatted
    assert "- replay `111`: https://replay.pokemonshowdown.com/battle-gen9ou-111" in formatted
    assert "- replay `222`: https://replay.pokemonshowdown.com/battle-gen9ou-222" in formatted
    assert "- batch `2-1`" in formatted
    assert "- `2` replay link(s)" in formatted
    assert "- …" in formatted
    assert "✅ vs A" not in formatted


def test_remaining_and_proof_sections_render_as_compact_lists():
    message = build_contract_message(
        "REPORTING_CORRECTION",
        "format polish applied",
        "renderer now emits compact status-card spacing",
        "the project channel reads better when proof and next steps scan as bullets",
        "artifact batch_2026-03-10.md; report batch_2026-03-10.md; source=tests.discord_reporting",
        "verify live bot posts; monitor any awkward wrap cases",
    )
    assert "🔎 **Proof:**\n- artifact `batch_2026-03-10.md`\n- report `batch_2026-03-10.md`\n- source `tests.discord_reporting`" in message
    assert "⏭️ **Remaining:**\n- verify live bot posts\n- monitor any awkward wrap cases" in message
