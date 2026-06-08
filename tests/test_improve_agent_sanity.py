import pytest

from infrastructure.improve_agent import (
    AUTO_IMPROVE_SENTINEL,
    AUTO_PUSH_SENTINEL,
    PUSH_BRANCH_ENV,
    PUSH_REMOTE_ENV,
    auto_improve_enabled,
    auto_push_enabled,
    battle_ids_from_evidence,
    current_win_rate_snapshot,
    explicit_push_target,
    has_replay_protocol_evidence,
    has_request_legal_option_evidence,
    record_deploy,
    top_issue_evidence,
    validate_autoresearch_for_improvement,
    validate_diff_scope,
)


def _report(**overrides):
    report = {
        "generated_at": "2026-05-27T08:00:00+00:00",
        "batch": {"id": "batch-30-2618956577"},
        "grounded_context": {"source": "data/pokedex_oracle.py"},
        "top_issue": {
            "key": "decision_instability",
            "title": "Decision traces show unstable fallback behavior",
            "summary": "Repeated fallback choices appear in local decision traces.",
            "recommendation": "Patch the narrowest timeout/fallback branch.",
            "proof": ["battle-gen9ou-2618898402: repeated same action patterns: switch corviknight, bodypress"],
        },
    }
    report.update(overrides)
    return report


def test_auto_improve_requires_cli_flag_or_env_sentinel(monkeypatch):
    monkeypatch.delenv(AUTO_IMPROVE_SENTINEL, raising=False)

    assert not auto_improve_enabled(False)
    assert auto_improve_enabled(True)

    monkeypatch.setenv(AUTO_IMPROVE_SENTINEL, "1")

    assert auto_improve_enabled(False)


def test_auto_push_requires_cli_flag_or_env_sentinel(monkeypatch):
    monkeypatch.delenv(AUTO_PUSH_SENTINEL, raising=False)

    assert not auto_push_enabled(False)
    assert auto_push_enabled(True)

    monkeypatch.setenv(AUTO_PUSH_SENTINEL, "true")

    assert auto_push_enabled(False)


def test_push_target_requires_explicit_remote_and_branch(monkeypatch):
    monkeypatch.delenv(PUSH_REMOTE_ENV, raising=False)
    monkeypatch.delenv(PUSH_BRANCH_ENV, raising=False)

    with pytest.raises(ValueError, match="explicit"):
        explicit_push_target()


def test_push_target_refuses_origin_master():
    with pytest.raises(ValueError, match="origin master"):
        explicit_push_target("origin", "master")


def test_push_target_accepts_explicit_non_master_target():
    assert explicit_push_target("origin", "auto-improve/proof") == ("origin", "auto-improve/proof")


def test_deploy_record_includes_win_rate_snapshot(monkeypatch, tmp_path):
    import json
    import infrastructure.improve_agent as improve_agent

    battle_stats_path = tmp_path / "battle_stats.json"
    deploy_log_path = tmp_path / "deploy_log.json"
    battle_stats_path.write_text(
        json.dumps(
            [
                {"result": "win", "elo": 1201},
                {"result": "loss", "elo": 1190},
                {"result": "win", "elo": 1207},
                {"result": "tie", "elo": 1207},
            ]
        ),
        encoding="utf-8",
    )
    monkeypatch.setattr(improve_agent, "BATTLE_STATS_PATH", battle_stats_path)
    monkeypatch.setattr(improve_agent, "DEPLOY_LOG_PATH", deploy_log_path)

    record_deploy("pre", "post")

    deploy = json.loads(deploy_log_path.read_text(encoding="utf-8"))[-1]
    assert deploy["win_rate_at_deploy"] == pytest.approx(2 / 3)
    assert deploy["win_rate_sample_at_deploy"] == 3
    assert deploy["elo_at_deploy"] == 1207


def test_current_win_rate_snapshot_uses_recent_decisive_battles():
    battles = [
        {"result": "loss"},
        {"result": "win"},
        {"result": "tie"},
        {"result": "win"},
    ]

    assert current_win_rate_snapshot(battles, sample_size=2) == (1.0, 2)


def test_offline_eval_gate_fails_closed_when_harness_missing(monkeypatch, tmp_path):
    import infrastructure.improve_agent as improve_agent

    monkeypatch.setattr(improve_agent, "PROJECT_ROOT", tmp_path)
    monkeypatch.setattr(improve_agent, "EVAL_GATE_ENABLED", True)

    accepted, detail = improve_agent.offline_eval_gate()

    assert accepted is False
    assert detail["error"] == "eval_harness_unavailable"
    assert detail["readiness_command"].endswith("infrastructure/offline_eval_readiness.py --require-ready")


def test_improve_agent_requires_battle_linked_evidence():
    report = _report(
        top_issue={
            "key": "hallucinated",
            "title": "Looks bad",
            "summary": "An LLM said the bot should change.",
            "recommendation": "Patch something.",
            "proof": ["no replay id here"],
        }
    )

    blockers = validate_autoresearch_for_improvement(report)

    assert "top_issue proof is not linked to Showdown battle ids" in blockers


def test_improve_agent_blocks_trace_only_policy_target_without_request_truth():
    blockers = validate_autoresearch_for_improvement(_report())

    assert any("trace-only decision issue cannot target fp/search/main.py" in blocker for blocker in blockers)


def test_improve_agent_allows_trace_only_policy_target_with_request_truth():
    report = _report(
        protocol_lines=[
            '|request|{"active":[{"moves":[{"id":"recover","disabled":false}]}]}',
            "|turn|5",
        ]
    )

    assert validate_autoresearch_for_improvement(report) == []


def test_improve_agent_blocks_trace_only_policy_target_with_replay_but_no_request_truth():
    report = _report(
        replay_json={
            "id": "gen9ou-2618898402",
            "log": "|turn|5\n|move|p1a: Corviknight|Body Press|p2a: Kingambit\n",
        }
    )

    blockers = validate_autoresearch_for_improvement(report)

    assert any("current Showdown request-backed legal-option evidence" in blocker for blocker in blockers)


def test_improve_agent_blocks_mechanics_issue_without_source_contract():
    report = _report(
        grounded_context={},
        top_issue={
            "key": "type_claim",
            "title": "Type matchup claim",
            "summary": "The bot mishandled a damage immunity.",
            "recommendation": "Change move weighting.",
            "proof": ["battle-gen9ou-2618898402: immunity was asserted by the model"],
        },
    )

    blockers = validate_autoresearch_for_improvement(report)

    assert "mechanics-adjacent issue lacks grounded_context.source" in blockers


def test_improve_agent_rejects_llm_grounding_source():
    report = _report(
        grounded_context={"source": "LLM memory and model prose"},
        top_issue={
            "key": "type_claim",
            "title": "Type matchup claim",
            "summary": "The bot mishandled a damage immunity.",
            "recommendation": "Change move weighting.",
            "proof": ["battle-gen9ou-2618898402: replay showed the action failed"],
        },
    )

    blockers = validate_autoresearch_for_improvement(report)

    assert "grounded_context.source is not a trusted non-LLM authority" in blockers


def test_improve_agent_rejects_evidence_integrity_claim_gaps():
    report = _report(
        top_issue={
            "key": "hazard_pressure",
            "title": "Hazard pressure matchup claim",
            "summary": "The bot mishandled hazard pressure without replay protocol proof.",
            "recommendation": "Change hazard pressure weighting.",
            "proof": ["battle-gen9ou-2618898402: hazard pressure was asserted without replay lines"],
        },
        evidence_integrity={
            "claims_without_evidence": [
                {"battle_id": "battle-gen9ou-2618898402", "claim": "hazard pressure without replay lines"}
            ]
        }
    )

    blockers = validate_autoresearch_for_improvement(report)

    assert "evidence_integrity reports claims without replay/trace evidence" in blockers


def test_improve_agent_blocks_mechanics_policy_issue_without_replay_protocol_truth():
    report = _report(
        grounded_context={"source": "data/pokedex_oracle.py"},
        top_issue={
            "key": "hazard_pressure",
            "title": "Hazard pressure is being lost",
            "summary": "Hazard pressure looked bad in the battle.",
            "recommendation": "Change hazard weighting.",
            "proof": ["battle-gen9ou-2618898402: hazard pressure looked bad"],
        },
    )

    blockers = validate_autoresearch_for_improvement(report)

    assert "mechanics/policy issue lacks replay/protocol evidence" in blockers


def test_improve_agent_blocks_trace_only_decision_instability_with_claim_gaps_without_request_truth():
    report = _report(
        evidence_integrity={
            "claims_without_evidence": [
                {"battle_id": "battle-gen9ou-2618898402", "claim": "hazard pressure without replay lines"}
            ]
        }
    )

    blockers = validate_autoresearch_for_improvement(report)

    assert any("trace-only decision issue cannot target fp/search/main.py" in blocker for blocker in blockers)


def test_improve_agent_rejects_narrative_request_hash_without_bounded_options():
    report = _report(
        evidence_integrity={
            "claims_without_evidence": [{"battle_id": "battle-gen9ou-2618898402"}],
            "losses_with_request_legal_options": 1,
        },
        top_issue={
            "key": "decision_instability",
            "title": "Decision traces show unstable fallback behavior",
            "summary": "Repeated fallback choices appear in local decision traces.",
            "recommendation": "Patch the narrowest timeout/fallback branch.",
            "proof": [
                "battle-gen9ou-2618898402: requestHash exists and legal options were available, but no bounded candidates are shown"
            ],
        },
    )

    blockers = validate_autoresearch_for_improvement(report)

    assert any("request-backed legal-option evidence" in blocker for blocker in blockers)


def test_improve_agent_detects_protocol_request_evidence():
    report = _report(request={"active": [{"moves": [{"id": "recover", "disabled": False}]}]})

    assert has_replay_protocol_evidence(report, top_issue_evidence(report["top_issue"]))
    assert has_request_legal_option_evidence(report, top_issue_evidence(report["top_issue"]))


def test_improve_agent_does_not_treat_turn_log_as_legal_option_evidence():
    report = _report(protocol_lines=["|turn|5", "|move|p1a: Corviknight|Body Press|p2a: Kingambit"])

    assert has_replay_protocol_evidence(report, top_issue_evidence(report["top_issue"]))
    assert not has_request_legal_option_evidence(report, top_issue_evidence(report["top_issue"]))


def test_improve_agent_derives_battle_count_from_proof_strings():
    evidence = top_issue_evidence(_report()["top_issue"])

    assert evidence == ["battle-gen9ou-2618898402: repeated same action patterns: switch corviknight, bodypress"]
    assert battle_ids_from_evidence(evidence) == ["battle-gen9ou-2618898402"]


def test_improve_agent_rejects_diff_that_touches_non_target_file():
    diff = """--- a/fp/search/eval.py
+++ b/fp/search/eval.py
@@ -1,1 +1,1 @@
-old
+new
--- a/fp/search/main.py
+++ b/fp/search/main.py
@@ -1,1 +1,1 @@
-old
+new
"""

    blockers = validate_diff_scope(diff, "fp/search/eval.py")

    assert any("outside target fp/search/eval.py" in blocker for blocker in blockers)


def test_improve_agent_rejects_file_creation_or_deletion():
    diff = """--- /dev/null
+++ b/fp/search/eval.py
@@ -0,0 +1,1 @@
+new
"""

    blockers = validate_diff_scope(diff, "fp/search/eval.py")

    assert any("creates or deletes files" in blocker for blocker in blockers)


def test_improve_agent_rejects_overlarge_diff():
    body = "\n".join(f"+line_{index}" for index in range(51))
    diff = f"""--- a/fp/search/eval.py
+++ b/fp/search/eval.py
@@ -1,1 +1,51 @@
{body}
"""

    blockers = validate_diff_scope(diff, "fp/search/eval.py")

    assert any("limit is 50" in blocker for blocker in blockers)


def test_improve_agent_accepts_small_target_only_diff():
    diff = """--- a/fp/search/eval.py
+++ b/fp/search/eval.py
@@ -1,1 +1,1 @@
-old
+new
"""

    assert validate_diff_scope(diff, "fp/search/eval.py") == []
