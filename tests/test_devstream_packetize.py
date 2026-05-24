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
