from infrastructure.discord_reporting import structured_report_fields


def test_pending_public_replay_is_not_proof_ready():
    content = """
[PROOF] **battle result win vs Tonyfloyd**

What happened:
battle finished win vs Tonyfloyd using gen9ou in 12 turns

Why it matters:
battle updates should confirm the win condition that worked.

Proof:
- replay `gen9ou-2626011252`: replay pending public upload gen9ou-2626011252
- battle `2626011252`
- win vs Tonyfloyd 12 turns

Remaining:
Append replay or ladder delta if more context lands after posting.
""".strip()

    fields = structured_report_fields(content, event_type="battle_result")

    assert fields["proof"]["replay"]["status"] == "pending-public-upload"
    assert fields["proof_readiness"]["readyForHermes"] is False
    assert fields["proof_readiness"]["status"] == "proof-needs-fields"
    assert "replay.url" in fields["proof_readiness"]["missingFields"]
    assert "replay pending public upload" in fields["proof_readiness"]["blockers"]
