import json
from pathlib import Path

from scripts import devstream_packetize as packetize


def test_packetizer_preserves_autoresearch_proof_and_recommendation(tmp_path: Path):
    source = tmp_path / "autoresearch_latest.json"
    proof = [
        "battle-gen9ou-1: bot never established Stealth Rock",
        "battle-gen9ou-2: opponent won the hazard race",
    ]
    source.write_text(
        json.dumps(
            {
                "generated_at": "2026-05-27T08:00:00+00:00",
                "batch": {"id": "batch-2", "start_battle_id": "battle-gen9ou-1", "end_battle_id": "battle-gen9ou-2"},
                "grounded_context": {"source": "data/pokedex_oracle.py"},
                "issues": [
                    {
                        "key": "hazard_pressure",
                        "title": "Hazard pressure is being lost",
                        "summary": "Losses repeatedly come from losing the hazard race.",
                        "recommendation": "Raise hazard-setting urgency earlier in neutral matchups.",
                        "proof": proof,
                    }
                ]
            }
        ),
        encoding="utf-8",
    )

    findings = packetize.extract_findings(packetize.load_source(source))
    packet = packetize.build_packet(findings[0], 1, source)

    assert packet["finding_key"] == "hazard_pressure"
    assert packet["task_type"] == "battle-policy-improvement"
    assert packet["stream_role"] == "code-eval-work-packet"
    assert packet["evidence"] == proof
    assert packet["recommendation"] == "Raise hazard-setting urgency earlier in neutral matchups."
    assert packet["source_report"] == str(source).replace("\\", "/")
    assert packet["proposed_change"] == [
        "Inspect replay/decision evidence named in evidence[]",
        "Raise hazard-setting urgency earlier in neutral matchups.",
    ]
    assert packet["runtime_mutation_allowed"] is False
    assert packet["requires_human_approval_for_runtime"] is True
    assert packet["authority"]["source_of_truth"] == "HERMES"
    assert packet["authority"]["runtime_mutation_allowed_by_packet"] is False
    assert packet["evidence_integrity"]["ok"] is True
    assert packet["evidence_integrity"]["battleIds"] == ["battle-gen9ou-1", "battle-gen9ou-2"]
    assert packet["evidence_integrity"]["mechanicsClaimsValidated"] is True
    assert packet["allowed_paths"] == packetize.DEFAULT_ALLOWED_PATHS
    assert packet["acceptance_checks"] == packetize.DEFAULT_ACCEPTANCE_CHECKS
    assert "post_patch_eval_plan" in packet
    assert "runtime_gate" in packet["post_patch_eval_plan"]
    assert "battle after this packet's createdAt" in packet["post_patch_eval_plan"]["expected_runtime_proof"][0]
    assert packet["post_patch_eval_plan"]["eval_command"] == "python3 scripts/devstream_post_packet_eval.py --write"
    assert packet["post_patch_eval_plan"]["proof_artifact"] == "devstream/truth/post-packet-eval.json"


def test_packetizer_normalizes_relative_source_report_path():
    packet = packetize.build_packet(
        {
            "title": "Hazard pressure",
            "summary": "Hazard issue",
            "evidence": [],
        },
        1,
        Path("replay_analysis/autoresearch_latest.json"),
    )

    assert packet["source_report"] == "replay_analysis/autoresearch_latest.json"


def test_packetizer_deduplicates_evidence_sources():
    findings = packetize.extract_findings(
        {
            "issues": [
                {
                    "summary": "Fallback loops",
                    "evidence": ["battle-1: timeout loop", "battle-2: fallback"],
                    "proof": ["battle-1: timeout loop"],
                    "examples": "battle-3: repeated protect",
                }
            ]
        }
    )

    assert findings[0]["evidence"] == [
        "battle-1: timeout loop",
        "battle-2: fallback",
        "battle-3: repeated protect",
    ]


def test_packetizer_extracts_single_top_issue_from_live_autoresearch_shape():
    findings = packetize.extract_findings(
        {
            "top_issue": {
                "key": "hazard_pressure",
                "title": "Hazard pressure is being lost",
                "summary": "Losses repeatedly come from losing the hazard race.",
                "recommendation": "Raise hazard urgency earlier.",
                "proof": ["battle-gen9ou-2602394852: no hazards"],
            }
        }
    )

    assert len(findings) == 1
    assert findings[0]["key"] == "hazard_pressure"
    assert findings[0]["title"] == "Hazard pressure is being lost"
    assert findings[0]["recommendation"] == "Raise hazard urgency earlier."
    assert findings[0]["evidence"] == ["battle-gen9ou-2602394852: no hazards"]


def test_packetizer_deduplicates_top_issue_and_issue_list_entry():
    issue = {
        "key": "hazard_pressure",
        "title": "Hazard pressure is being lost",
        "summary": "Losses repeatedly come from losing the hazard race.",
        "recommendation": "Raise hazard urgency earlier.",
        "proof": ["battle-gen9ou-2602394852: no hazards"],
    }

    findings = packetize.extract_findings({"top_issue": issue, "issues": [issue]})

    assert len(findings) == 1
    assert findings[0]["key"] == "hazard_pressure"


def test_packetizer_marks_unlinked_or_unsupported_evidence_as_not_promotable(tmp_path: Path):
    source = tmp_path / "autoresearch_latest.json"
    source.write_text(
        json.dumps(
            {
                "generated_at": "2026-05-27T08:00:00+00:00",
                "batch": {"id": "batch-no-proof"},
                "unsupported_mechanics_claims": ["Gholdengo has Wonder Guard"],
                "evidence_integrity": {
                    "claims_without_evidence": [
                        {
                            "battle_id": "battle-gen9ou-2618898402",
                            "claim_class": "mechanics_or_strategy",
                            "reason": "no replay JSON or Showdown protocol log lines",
                        }
                    ]
                },
                "top_issue": {
                    "key": "hallucinated_mechanics",
                    "title": "Unsupported mechanic claim",
                    "summary": "The model asserted a mechanic without replay proof.",
                    "recommendation": "Do not promote this.",
                    "proof": ["trust me, bro"],
                },
            }
        ),
        encoding="utf-8",
    )

    finding = packetize.extract_findings(packetize.load_source(source))[0]
    packet = packetize.build_packet(finding, 1, source)

    assert packet["evidence_integrity"]["ok"] is False
    assert "not linked to any Showdown battle id" in "\n".join(packet["evidence_integrity"]["blockers"])
    assert "claims without replay/trace evidence" in "\n".join(packet["evidence_integrity"]["blockers"])
    assert packet["evidence_integrity"]["unsupportedMechanicsClaimCount"] == 1
    assert packet["evidence_integrity"]["claimsWithoutEvidenceCount"] == 1


def test_packetizer_blocks_trace_only_decision_instability_without_request_legal_options(tmp_path: Path):
    source = tmp_path / "autoresearch_latest.json"
    source.write_text(
        json.dumps(
            {
                "generated_at": "2026-05-27T08:00:00+00:00",
                "batch": {"id": "batch-trace-only"},
                "grounded_context": {"source": "data/pokedex_oracle.py"},
                "evidence_integrity": {
                    "claims_without_evidence": [
                        {
                            "battle_id": "battle-gen9ou-2618898402",
                            "claim_class": "mechanics_or_strategy",
                            "reason": "no replay JSON or Showdown protocol log lines",
                        }
                    ]
                },
                "top_issue": {
                    "key": "decision_instability",
                    "title": "Decision traces show unstable fallback behavior",
                    "summary": "Decision traces show repeated fallback loops.",
                    "recommendation": "Patch the narrowest timeout branch.",
                    "proof": ["battle-gen9ou-2618898402: repeated same action patterns in decision trace"],
                },
            }
        ),
        encoding="utf-8",
    )

    finding = packetize.extract_findings(packetize.load_source(source))[0]
    packet = packetize.build_packet(finding, 1, source)

    assert packet["evidence_integrity"]["ok"] is False
    assert packet["evidence_integrity"]["claimsWithoutEvidenceCount"] == 1
    assert packet["evidence_integrity"]["requestLegalOptionEvidence"] is False
    assert "request-backed legal-option evidence" in "\n".join(packet["evidence_integrity"]["blockers"])


def test_packetizer_allows_trace_only_decision_instability_with_request_legal_options(tmp_path: Path):
    source = tmp_path / "autoresearch_latest.json"
    source.write_text(
        json.dumps(
            {
                "generated_at": "2026-05-27T08:00:00+00:00",
                "batch": {"id": "batch-trace-only"},
                "grounded_context": {"source": "data/pokedex_oracle.py"},
                "evidence_integrity": {
                    "claims_without_evidence": [
                        {
                            "battle_id": "battle-gen9ou-2618898402",
                            "claim_class": "mechanics_or_strategy",
                            "reason": "no replay JSON or Showdown protocol log lines",
                        }
                    ],
                    "losses_with_request_legal_options": 1,
                },
                "top_issue": {
                    "key": "decision_instability",
                    "title": "Decision traces show unstable fallback behavior",
                    "summary": "Decision traces show repeated fallback loops.",
                    "recommendation": "Patch the narrowest timeout branch.",
                    "proof": [
                        (
                            "battle-gen9ou-2618898402: repeated same action patterns in decision trace; "
                            "request-backed legal options: "
                            "requestHash=aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa "
                            "legalMoves=4 legalSwitches=2"
                        )
                    ],
                },
            }
        ),
        encoding="utf-8",
    )

    finding = packetize.extract_findings(packetize.load_source(source))[0]
    packet = packetize.build_packet(finding, 1, source)

    assert packet["evidence_integrity"]["ok"] is True
    assert packet["evidence_integrity"]["claimsWithoutEvidenceCount"] == 1
    assert packet["evidence_integrity"]["requestLegalOptionEvidence"] is True
    assert "trace-only fallback/runtime fixes" in "\n".join(packet["evidence_integrity"]["warnings"])


def test_packetizer_rejects_narrative_request_hash_without_structured_legal_counts(tmp_path: Path):
    source = tmp_path / "autoresearch_latest.json"
    source.write_text(
        json.dumps(
            {
                "generated_at": "2026-05-27T08:00:00+00:00",
                "batch": {"id": "batch-trace-only"},
                "grounded_context": {"source": "data/pokedex_oracle.py"},
                "evidence_integrity": {
                    "claims_without_evidence": [
                        {"battle_id": "battle-gen9ou-2618898402", "claim_class": "mechanics_or_strategy"}
                    ],
                    "losses_with_request_legal_options": 1,
                },
                "top_issue": {
                    "key": "decision_instability",
                    "title": "Decision traces show unstable fallback behavior",
                    "summary": "Decision traces show repeated fallback loops.",
                    "recommendation": "Patch the narrowest timeout branch.",
                    "proof": [
                        (
                            "battle-gen9ou-2618898402: a report says requestHash exists and legal options were available, "
                            "but it does not provide bounded legal move or switch counts"
                        )
                    ],
                },
            }
        ),
        encoding="utf-8",
    )

    finding = packetize.extract_findings(packetize.load_source(source))[0]
    packet = packetize.build_packet(finding, 1, source)

    assert packet["evidence_integrity"]["ok"] is False
    assert packet["evidence_integrity"]["requestLegalOptionEvidence"] is False
    assert "request-backed legal-option evidence" in "\n".join(packet["evidence_integrity"]["blockers"])
