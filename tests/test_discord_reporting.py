import importlib
import json

from infrastructure.discord_reporting import (
    build_contract_message,
    build_contract_payload,
    canonical_replay_url,
    format_elo_delta,
    format_payload_or_message,
    is_contract_message,
    public_replay_id_candidate,
    summarize_recent_results,
    top_recurring_issue,
)


def test_event_poster_loads_env_chain_for_webhooks(monkeypatch, tmp_path):
    import infrastructure.event_poster as event_poster

    env_file = tmp_path / ".env"
    env_file.write_text("DISCORD_BATTLES_WEBHOOK_URL=https://discord.com/api/webhooks/example/token\n", encoding="utf-8")
    monkeypatch.setattr(event_poster, "ENV_FILES", (env_file,))
    monkeypatch.delenv("DISCORD_BATTLES_WEBHOOK_URL", raising=False)
    monkeypatch.delenv("DISCORD_WEBHOOK_URL", raising=False)

    loaded = event_poster.load_env_chain()
    url, source = event_poster.resolve_webhook_url("battles")

    assert loaded == [str(env_file)]
    assert source == "DISCORD_BATTLES_WEBHOOK_URL"
    assert url.endswith("/token")


def test_event_poster_doctor_reports_redacted_transport(monkeypatch, tmp_path):
    import infrastructure.event_poster as event_poster
    import infrastructure.event_queue_lib as event_queue_lib

    queue_file = tmp_path / "events_queue.json"
    queue_file.write_text("[]", encoding="utf-8")
    monkeypatch.setenv("EVENT_QUEUE_FILE", str(queue_file))
    monkeypatch.setattr(event_queue_lib, "QUEUE_FILE", queue_file)
    monkeypatch.setenv("DISCORD_WEBHOOK_URL", "https://discord.com/api/webhooks/example/token")

    payload = event_poster.build_doctor_payload()

    assert payload["ready"] is True
    assert payload["config"]["aliases"]["project"]["redactedUrl"].endswith("/api/webhooks/REDACTED")
    json.dumps(payload)


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
    assert "🔎 **Proof:**\n- battle `1`" in content
    assert "- source `unit-test`" in content
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


def test_payload_formatter_builds_operator_facing_battle_report():
    payload = build_contract_payload(
        "PROOF",
        "battle result loss vs GOATZILASPAMMER",
        "Battle battle-gen9ou-2555107042 ended loss against GOATZILASPAMMER.",
        "Operator-facing battle posts should immediately show whether the bot is climbing through repeatable play, variance, or an operational failure.",
        "battle_id=battle-gen9ou-2555107042; result=loss; team_file=fat-team-1-stall.txt; opponent=GOATZILASPAMMER; turns=47; replay=https://replay.pokemonshowdown.com/gen9ou-2555107042",
        "Append replay or ladder delta if more context lands after posting.",
        source="fp.run_battle",
        battle_id="battle-gen9ou-2555107042",
        result="loss",
        team_file="fat-team-1-stall.txt",
        opponent="GOATZILASPAMMER",
        turns=47,
        replay_url="https://replay.pokemonshowdown.com/gen9ou-2555107042",
        recent_record="last 5: 2-3 (40% WR)",
        decisive_reason="GOATZILASPAMMER closed the endgame before the bot stabilized the board.",
        next_battle_action="Review the replay before the next queue and tag whether this was policy, matchup, or ops.",
        elo_before=1223,
        elo_after=1203,
    )
    formatted = format_payload_or_message(payload)
    assert formatted.startswith("[PROOF] **battle result loss vs GOATZILASPAMMER**")
    assert "battle finished loss vs GOATZILASPAMMER using 1 stall in 47 turns" in formatted
    assert "last 5: 2-3 (40% WR)" in formatted
    assert "GOATZILASPAMMER closed the endgame before the bot stabilized the board." in formatted
    assert "next battle focus: Review the replay before the next queue and tag whether this was policy, matchup, or ops." in formatted
    assert "- replay `gen9ou-2555107042`: https://replay.pokemonshowdown.com/gen9ou-2555107042" in formatted
    assert "- ELO `lost 20 (1223 → 1203, -20)`" in formatted


def test_payload_formatter_does_not_render_contradictory_elo_delta():
    payload = build_contract_payload(
        "PROOF",
        "battle result loss vs MatronJames",
        "Battle battle-gen9ou-2555107042 ended loss against MatronJames.",
        "Operator-facing battle posts must not imply a loss improved ladder rating.",
        "battle_id=battle-gen9ou-2555107042; result=loss",
        "verify the account/rating source before claiming a ladder delta",
        source="unit-test",
        battle_id="battle-gen9ou-2555107042",
        result="loss",
        opponent="MatronJames",
        elo_before=1117,
        elo_after=1136,
    )
    formatted = format_payload_or_message(payload)

    assert "- ELO `check needed (cached 1117, fetched 1136, +19 contradicts loss)`" in formatted
    assert "1117 → 1136 ELO" not in formatted


def test_elo_delta_labels_match_result_direction():
    assert format_elo_delta(1136, 1117, "loss") == "ELO lost 19 (1136 → 1117, -19)"
    assert format_elo_delta(1117, 1136, "win") == "ELO gained 19 (1117 → 1136, +19)"
    assert "contradicts win" in format_elo_delta(1136, 1117, "win")
    assert "contradicts loss" in format_elo_delta(1117, 1136, "loss")


def test_replay_url_canonicalization_rejects_private_unresolved_links():
    assert (
        canonical_replay_url("https://replay.pokemonshowdown.com/battle-gen9ou-111.json")
        == "https://replay.pokemonshowdown.com/gen9ou-111"
    )
    assert (
        canonical_replay_url("https://replay.pokemonshowdown.com/gen9ou-111")
        == "https://replay.pokemonshowdown.com/gen9ou-111"
    )
    assert canonical_replay_url("https://replay.pokemonshowdown.com/battle-gen9ou-111-privatehash") == ""
    assert public_replay_id_candidate("battle-gen9ou-111-privatehash") == "gen9ou-111"


def test_payload_formatter_marks_private_replay_as_pending_not_public_link():
    payload = build_contract_payload(
        "PROOF",
        "battle result loss vs PrivateReplay",
        "Battle battle-gen9ou-111-privatehash ended loss against PrivateReplay.",
        "Private room ids should not be shown as confident public replay URLs.",
        "replay=https://replay.pokemonshowdown.com/battle-gen9ou-111-privatehash",
        "wait for public upload or use local replay JSON",
        result="loss",
        battle_id="battle-gen9ou-111-privatehash",
        replay_url="https://replay.pokemonshowdown.com/battle-gen9ou-111-privatehash",
    )
    formatted = format_payload_or_message(payload)

    assert "- replay pending public upload `gen9ou-111`" in formatted
    assert "https://replay.pokemonshowdown.com/gen9ou-111" not in formatted


def test_payload_formatter_flags_operational_losses():
    payload = build_contract_payload(
        "PROOF",
        "battle result loss vs oTheus03",
        "Battle battle-gen9ou-2556331152 ended loss against oTheus03.",
        "Operator-facing battle posts should immediately show whether the bot is climbing through repeatable play, variance, or an operational failure.",
        "battle_id=battle-gen9ou-2556331152; result=loss; team_file=fat-team-1-stall.txt; opponent=oTheus03; turns=54; replay=https://replay.pokemonshowdown.com/gen9ou-2556331152",
        "Append replay or ladder delta if more context lands after posting.",
        source="fp.run_battle",
        battle_id="battle-gen9ou-2556331152",
        result="loss",
        team_file="fat-team-1-stall.txt",
        opponent="oTheus03",
        turns=54,
        replay_url="https://replay.pokemonshowdown.com/gen9ou-2556331152",
        recent_record="last 5: 2-3 (40% WR)",
        decisive_reason="Loss came from inactivity/disconnect behavior, so this looks operational before it looks strategic.",
        next_battle_action="Review reconnect / timer handling before blaming the team.",
        performance_change="Two recent losses include disconnect or inactivity endings.",
    )
    formatted = format_payload_or_message(payload)
    assert "inactivity/disconnect behavior" in formatted
    assert "operator reports should flag ladder-invisible runtime failures immediately" in formatted


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
        recent_record="last 10: 6-4 (60% WR)",
        trend="improving",
    )
    formatted = format_payload_or_message(payload)
    assert formatted.startswith("[PROOF] **batch complete 2-1**")
    assert "3-battle window finished at 2-1 (67% WR)" in formatted
    assert "top loss pattern: losses were split across opponents (1 unique)" in formatted
    assert "last 10: 6-4 (60% WR)" in formatted
    assert "improving" in formatted
    assert "public replays 2/3; loss reviews queued 1; reviewed 1" in formatted
    assert "- replay `gen9ou-111`: https://replay.pokemonshowdown.com/gen9ou-111" in formatted
    assert "- replay `gen9ou-222`: https://replay.pokemonshowdown.com/gen9ou-222" in formatted
    assert "vs A https://replay.pokemonshowdown.com/battle-gen9ou-111" not in formatted


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
        recent_record="last 30: 11-19 (37% WR)",
        trend="slipping",
        next_battle_action="Preserve the Gliscor slot until Gholdengo is actually pinned.",
    )
    formatted = format_payload_or_message(payload)
    assert formatted.startswith("[PROOF] **batch analysis #42 ready**")
    assert "lead issue: 1. Gholdengo structures still overload the Gliscor slot after Ting-Lu chip" in formatted
    assert "last 30: 11-19 (37% WR)" in formatted
    assert "slipping" in formatted
    assert "full batch analysis: batch_0042_20260310.md" in formatted
    assert "- report `batch_0042_20260310.md`" in formatted
    assert "- top issue `1. Gholdengo structures still overload the Gliscor slot after Ting-Lu chip`" in formatted


def test_recent_results_summary_and_recurring_issue_helpers():
    battles = [
        {"result": "loss"},
        {"result": "win"},
        {"result": "loss"},
        {"result": "loss"},
        {"result": "loss"},
    ]
    summary = summarize_recent_results(battles, window=5)
    assert summary["record"] == "last 5: 1-4 (20% WR)"
    assert summary["streak"] == "loss x3"

    issue = top_recurring_issue([
        "disconnect on timer",
        "disconnect after reconnect",
        "hazards got away",
    ])
    assert issue.startswith("battle ended through inactivity/disconnect behavior")
