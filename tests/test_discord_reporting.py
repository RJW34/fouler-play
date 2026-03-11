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
    assert "**What happened:**\ncentral helper now builds the required message shape" in message
    assert "**Why it matters:**\nprevents ad hoc fragments from hitting the project channel" in message
    assert "**Proof:**\n- pytest target=test_contract_message_shape\n- source `unit-test`" in message
    assert "**Remaining:**\n- wire remaining call sites if new reporting surfaces appear" in message
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
    assert "**What happened:**\nrun_battle queued a result" in content
    assert "**Proof:**\n- source `unit-test`\n- battle `1`" in content
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
    assert "**What happened:**\nmonitor generated a review" in formatted
    assert "**Remaining:**\n- read saved report" in formatted


def test_payload_formatter_prioritizes_battle_subject_matter_in_battle_updates():
    payload = build_contract_payload(
        "PROOF",
        "battle result loss vs Gholdengo balance",
        "run_battle finalized battle-gen9ou-456 and queued the outcome for Discord delivery.",
        "Winning and losing updates should foreground the matchup lesson and next battle-relevant adjustment, not the reporting machinery.",
        "battle_id=battle-gen9ou-456; result=loss; team_file=fat-team-2-balance.txt; opponent=Gholdengo balance; turns=41",
        "Queue the next fix after checking the replay.",
        source="fp.run_battle",
        battle_id="battle-gen9ou-456",
        result="loss",
        team_file="fat-team-2-balance.txt",
        opponent="Gholdengo balance",
        turns=41,
        strategic_issue="Gholdengo kept blocking hazard control and forced Gliscor to absorb too much chip.",
        performance_change="The bot lost the long game once Ting-Lu and Gliscor were both chipped under 45%.",
        next_battle_action="Preserve Gliscor HP earlier.",
    )
    formatted = format_payload_or_message(payload)
    assert "battle finished loss vs Gholdengo balance using 2 balance in 41 turns" in formatted
    assert "Gholdengo kept blocking hazard control and forced Gliscor to absorb too much chip." in formatted
    assert "The bot lost the long game once Ting-Lu and Gliscor were both chipped under 45%." in formatted
    assert "next battle focus: Preserve Gliscor HP earlier." in formatted
    assert "Winning and losing updates should foreground the matchup lesson and next battle-relevant adjustment, not the reporting machinery." in formatted
    assert "queued the outcome for Discord delivery" not in formatted


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
    assert "**What happened:**\nbattle finished win vs Sample Opp using 1 stall in 37 turns" in formatted
    assert "**Why it matters:**\nBattle-result reporting should include machine-readable proof without leaving the poster to infer context from raw JSON blobs." in formatted
    assert "- battle `123`" in formatted
    assert "- win vs Sample Opp 37 turns" in formatted
    assert "- team `1 stall`" in formatted
    assert "- source `fp.run_battle`" in formatted
    assert "**Remaining:**\n- append replay or ladder delta if it becomes available" in formatted


def test_payload_formatter_prioritizes_loss_patterns_in_batch_updates():
    payload = build_contract_payload(
        "PROOF",
        "batch complete 1-2",
        "bot_monitor closed a live batch with 3 battle(s) and queued the concise Discord summary.",
        "Batch updates should explain the battle pattern that mattered, not the mechanics of posting the summary.",
        "replay https://replay.pokemonshowdown.com/battle-gen9ou-333",
        "Review the two losses before the next batch.",
        source="bot_monitor.batch_complete",
        batch_results=[
            ("Rain offense", "lost", "https://replay.pokemonshowdown.com/battle-gen9ou-333"),
            ("Rain offense", "lost", None),
            ("Ting-Lu balance", "won", None),
        ],
        analysis_count=2,
        loss_pattern="Rain offense forced repeated emergency sacks once hazards stayed up.",
        performance_change="The batch collapsed whenever the first rain cycle removed Ting-Lu.",
        next_battle_action="Value early weather pivots.",
    )
    formatted = format_payload_or_message(payload)
    assert "3-battle window finished at 1-2 (33% WR)" in formatted
    assert "top loss pattern: Rain offense caused 2 loss(es) in this window" in formatted
    assert "The batch collapsed whenever the first rain cycle removed Ting-Lu." in formatted
    assert "Rain offense forced repeated emergency sacks once hazards stayed up." in formatted
    assert "next battle focus: Value early weather pivots." in formatted
    assert "queued the concise Discord summary" not in formatted


def test_payload_formatter_summarizes_batch_payload_without_blob_dump():
    payload = build_contract_payload(
        "PROOF",
        "batch complete 2-1",
        "bot_monitor closed a live batch with 3 battle(s) and queued the concise Discord summary.",
        "Routine batch updates should show the scoreline, replay coverage, and follow-up work without dumping the raw multiline summary blob into Proof.",
        "vs A https://replay.pokemonshowdown.com/battle-gen9ou-111\nvs B https://replay.pokemonshowdown.com/battle-gen9ou-222\nvs C",
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
    assert "**What happened:**\n3-battle window finished at 2-1 (67% WR); top loss pattern: losses were split across opponents (1 unique); replays 2/3; loss reviews queued 1; reviewed 1" in formatted
    assert "**Why it matters:**\nRoutine batch updates should show the scoreline, replay coverage, and follow-up work without dumping the raw multiline summary blob into Proof." in formatted
    assert "- replay `111`: https://replay.pokemonshowdown.com/battle-gen9ou-111" in formatted
    assert "- replay `222`: https://replay.pokemonshowdown.com/battle-gen9ou-222" in formatted
    assert "- batch `2-1`" in formatted
    assert "- coverage `replays 2/3`" in formatted
    assert "- loss reviews queued=`1`" in formatted
    assert "vs A https://replay.pokemonshowdown.com/battle-gen9ou-111" not in formatted


def test_remaining_and_proof_sections_render_as_compact_lists():
    message = build_contract_message(
        "REPORTING_CORRECTION",
        "format polish applied",
        "renderer now emits compact status-card spacing",
        "the project channel reads better when proof and next steps scan as bullets",
        "artifact batch_2026-03-10.md; report batch_2026-03-10.md; source=tests.discord_reporting",
        "verify live bot posts; monitor any awkward wrap cases",
    )
    assert "**Proof:**\n- artifact `batch_2026-03-10.md`\n- report `batch_2026-03-10.md`\n- source `tests.discord_reporting`" in message
    assert "**Remaining:**\n- verify live bot posts\n- monitor any awkward wrap cases" in message


def test_payload_formatter_summarizes_pipeline_report_with_lead_issue_only():
    payload = build_contract_payload(
        "PROOF",
        "batch analysis #42 ready",
        "pipeline analyzed the latest 30-battle batch and prepared the channel summary.",
        "Batch analysis only helps if the report callout is compact, proof-backed, and easy to scan before opening the full report.",
        "report=batch_0042_20260310.md; top_issues=1. Gholdengo structures still overload the Gliscor slot after Ting-Lu chip",
        "Open the report if the lead issue still needs a concrete fix after the summary.",
        source="pipeline.analyze",
        report="batch_0042_20260310.md",
        top_issues="1. Gholdengo structures still overload the Gliscor slot after Ting-Lu chip\n2. Booster Valiant forces too many emergency Tera lines",
    )
    formatted = format_payload_or_message(payload)
    assert formatted.startswith("[PROOF] **batch analysis #42 ready**")
    assert "**What happened:**\nlead issue: 1. Gholdengo structures still overload the Gliscor slot after Ting-Lu chip; full batch analysis: batch_0042_20260310.md" in formatted
    assert "**Why it matters:**\nBatch analysis only helps if the report callout is compact, proof-backed, and easy to scan before opening the full report." in formatted
    assert "- report `batch_0042_20260310.md`" in formatted
    assert "- top issue `1. Gholdengo structures still overload the Gliscor slot after Ting-Lu chip" in formatted
    assert "- source `pipeline.analyze`" in formatted
    assert "richer batch breakdown" not in formatted
    assert "**Remaining:**\n- Open the report if the lead issue still needs a concrete fix after the summary." in formatted
