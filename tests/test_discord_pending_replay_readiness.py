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


def test_replay_public_verified_false_overrides_public_status_claim():
    content = """
{"event_class":"PROOF","headline":"battle result win vs Pending","what_happened":"Battle battle-gen9ou-777-private ended win.","why_it_matters":"Replay upload is still pending.","proof":"replay=https://replay.pokemonshowdown.com/gen9ou-777","remaining":"wait for upload","battle_id":"battle-gen9ou-777-private","result":"win","replay_url":"https://replay.pokemonshowdown.com/gen9ou-777","replay_status":"public","replay_public_verified":false,"next_battle_action":"wait for upload"}
""".strip()

    fields = structured_report_fields(content, event_type="battle_result")

    assert fields["proof"]["replay"] == {
        "status": "pending-public-upload",
        "id": "gen9ou-777",
        "url": "",
    }
    assert fields["proof_readiness"]["readyForHermes"] is False
