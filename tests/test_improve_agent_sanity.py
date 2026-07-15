import hashlib
import json
import subprocess

import pytest

from infrastructure.improve_agent import (
    AUTO_IMPROVE_SENTINEL,
    AUTO_PUSH_SENTINEL,
    PUSH_BRANCH_ENV,
    PUSH_REMOTE_ENV,
    auto_improve_enabled,
    auto_push_enabled,
    battle_ids_from_evidence,
    committed_candidate_provenance,
    current_win_rate_snapshot,
    explicit_push_target,
    has_replay_protocol_evidence,
    has_request_legal_option_evidence,
    record_deploy,
    top_issue_evidence,
    validate_autoresearch_for_improvement,
    validate_diff_scope,
)


def _commit(repo, message):
    subprocess.run(["git", "-C", str(repo), "add", "-A"], check=True)
    subprocess.run(["git", "-C", str(repo), "commit", "-qm", message], check=True)
    return subprocess.check_output(["git", "-C", str(repo), "rev-parse", "HEAD"], text=True).strip()


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


def test_legacy_deploy_writer_is_retired():
    with pytest.raises(RuntimeError, match="activation receipt"):
        record_deploy("pre", "post")


def test_accepted_commit_receipt_is_immutable_and_hash_bound(monkeypatch, tmp_path):
    import infrastructure.improve_agent as improve_agent

    subprocess.run(["git", "init", "-q", str(tmp_path)], check=True)
    subprocess.run(["git", "-C", str(tmp_path), "config", "user.email", "proof@test.invalid"], check=True)
    subprocess.run(["git", "-C", str(tmp_path), "config", "user.name", "Proof Test"], check=True)
    target = tmp_path / "fp" / "search" / "main.py"
    target.parent.mkdir(parents=True)
    target.write_text("BASELINE = 1\n", encoding="utf-8")
    baseline = _commit(tmp_path, "baseline")
    target.write_text("BASELINE = 1\nCANDIDATE = 2\n", encoding="utf-8")
    candidate = _commit(tmp_path, "candidate")
    receipt_root = tmp_path / "receipts"
    monkeypatch.setattr(improve_agent, "PROJECT_ROOT", tmp_path)
    monkeypatch.setattr(improve_agent, "ACCEPTED_COMMIT_RECEIPT_ROOT", receipt_root)
    proof = {
        "candidatePatchSha256": "a" * 64,
        "lineage": {"changeId": "b" * 64},
    }
    artifact = {
        "runId": "run-proof",
        "resultRelativePath": "run-proof/result.json",
        "resultSha256": "c" * 64,
        "pointerSha256": "d" * 64,
        "autoresearchSha256": "e" * 64,
    }

    receipt = improve_agent.record_accepted_commit(
        issue_title="Fix exact policy branch",
        target_file="fp/search/main.py",
        pre_commit=baseline,
        post_commit=candidate,
        promotion_proof=proof,
        artifact_context=artifact,
        commit_provenance={"changedFiles": ["fp/search/main.py"]},
    )

    receipt_path = receipt_root / f"{candidate}.json"
    stored = json.loads(receipt_path.read_text(encoding="utf-8"))
    claimed_hash = stored.pop("receiptSha256")
    canonical = (json.dumps(stored, sort_keys=True, separators=(",", ":")) + "\n").encode("utf-8")
    assert claimed_hash == hashlib.sha256(canonical).hexdigest()
    assert receipt["candidate"]["postCommit"] == candidate
    assert receipt["proof"]["resultSha256"] == "c" * 64
    with pytest.raises(FileExistsError):
        improve_agent.record_accepted_commit(
            issue_title="Fix exact policy branch",
            target_file="fp/search/main.py",
            pre_commit=baseline,
            post_commit=candidate,
            promotion_proof=proof,
            artifact_context=artifact,
            commit_provenance={"changedFiles": ["fp/search/main.py"]},
        )


def test_commit_records_acceptance_without_claiming_runtime_deploy(monkeypatch, tmp_path):
    import infrastructure.improve_agent as improve_agent

    subprocess.run(["git", "init", "-q", str(tmp_path)], check=True)
    subprocess.run(["git", "-C", str(tmp_path), "config", "user.email", "proof@test.invalid"], check=True)
    subprocess.run(["git", "-C", str(tmp_path), "config", "user.name", "Proof Test"], check=True)
    target = tmp_path / "fp" / "search" / "main.py"
    target.parent.mkdir(parents=True)
    target.write_text("BASELINE = 1\n", encoding="utf-8")
    baseline = _commit(tmp_path, "baseline")
    candidate_root = tmp_path / "candidate-worktree"
    subprocess.run(
        ["git", "-C", str(tmp_path), "worktree", "add", "--detach", str(candidate_root), baseline],
        check=True,
        capture_output=True,
    )
    candidate_target = candidate_root / "fp" / "search" / "main.py"
    candidate_target.write_text("BASELINE = 2\n", encoding="utf-8")
    unrelated = tmp_path / "docs" / "other-agent.md"
    unrelated.parent.mkdir(parents=True)
    unrelated.write_text("preserve staged work\n", encoding="utf-8")
    subprocess.run(["git", "-C", str(tmp_path), "add", "docs/other-agent.md"], check=True)
    proof = {
        "candidatePatchSha256": "a" * 64,
        "lineage": {"changeId": "b" * 64},
    }
    artifact = {
        "runId": "run-proof",
        "resultSha256": "c" * 64,
        "autoresearchSha256": "d" * 64,
    }
    recorded = []
    monkeypatch.setattr(improve_agent, "PROJECT_ROOT", tmp_path)
    monkeypatch.setattr(
        improve_agent,
        "validated_promotion_artifact_context",
        lambda _proof, **_kwargs: artifact,
    )
    monkeypatch.setattr(improve_agent, "promotion_artifact_unchanged", lambda _context: True)
    monkeypatch.setattr(
        improve_agent,
        "committed_candidate_provenance",
        lambda **_kwargs: (True, {"changedFiles": ["fp/search/main.py"]}),
    )
    monkeypatch.setattr(
        improve_agent,
        "record_accepted_commit",
        lambda **kwargs: recorded.append(kwargs) or {"receiptSha256": "e" * 64},
    )
    monkeypatch.setattr(
        improve_agent,
        "record_deploy",
        lambda *_args, **_kwargs: pytest.fail("a Git commit must not be recorded as a deployment"),
    )

    source_snapshot = improve_agent.capture_target_snapshot("fp/search/main.py")
    try:
        assert improve_agent.commit_and_push(
            "fp/search/main.py",
            "Fix exact policy branch",
            candidate_root=candidate_root,
            source_snapshot=source_snapshot,
            transaction_head=baseline,
            promotion_proof=proof,
        ) is True
    finally:
        subprocess.run(
            ["git", "-C", str(tmp_path), "worktree", "remove", "--force", str(candidate_root)],
            check=True,
            capture_output=True,
        )
    assert recorded and recorded[0]["pre_commit"] == baseline
    candidate_ref = "refs/heads/auto-improve/" + "b" * 64
    candidate_commit = subprocess.check_output(
        ["git", "-C", str(tmp_path), "rev-parse", candidate_ref], text=True
    ).strip()
    message = subprocess.check_output(
        ["git", "-C", str(tmp_path), "show", "-s", "--pretty=%B", candidate_commit], text=True
    )
    assert "Fouler-Change-Id: " + "b" * 64 in message
    assert "Fouler-H2H-Result-SHA256: " + "c" * 64 in message
    changed = subprocess.check_output(
        ["git", "-C", str(tmp_path), "diff-tree", "--no-commit-id", "--name-only", "-r", candidate_commit],
        text=True,
    ).splitlines()
    staged = subprocess.check_output(
        ["git", "-C", str(tmp_path), "diff", "--cached", "--name-only"],
        text=True,
    ).splitlines()
    assert changed == ["fp/search/main.py"]
    assert staged == ["docs/other-agent.md"]
    assert target.read_text(encoding="utf-8") == "BASELINE = 1\n"


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

    accepted, detail = improve_agent.offline_eval_gate("fp/search/main.py")

    assert accepted is False
    assert detail["error"] == "eval_harness_unavailable"
    assert detail["readiness_command"].endswith("infrastructure/head_to_head_eval.py --help")


def test_offline_eval_gate_cannot_be_bypassed_by_disabling_it(monkeypatch):
    import infrastructure.improve_agent as improve_agent

    monkeypatch.setattr(improve_agent, "EVAL_GATE_ENABLED", False)

    accepted, detail = improve_agent.offline_eval_gate("fp/search/main.py")

    assert accepted is False
    assert detail["error"] == "head_to_head_gate_disabled"


def test_offline_eval_gate_rejects_underfilled_matrix_configuration(monkeypatch):
    import infrastructure.improve_agent as improve_agent

    monkeypatch.setattr(improve_agent, "EVAL_GATE_ENABLED", True)
    monkeypatch.setattr(improve_agent, "EVAL_GATE_BATTLES", 5)

    accepted, detail = improve_agent.offline_eval_gate("fp/search/main.py")

    assert accepted is False
    assert detail["error"] == "invalid_head_to_head_matrix_size"


def test_offline_eval_gate_rejects_weakened_runtime_limits(monkeypatch):
    import infrastructure.improve_agent as improve_agent

    monkeypatch.setattr(improve_agent, "EVAL_GATE_ENABLED", True)
    monkeypatch.setattr(improve_agent, "EVAL_GATE_BATTLES", 60)
    monkeypatch.setattr(improve_agent, "EVAL_GATE_SEARCH_TIME_MS", 0)
    monkeypatch.setattr(improve_agent, "EVAL_GATE_PER_BATTLE_TIMEOUT", 1.0)

    accepted, detail = improve_agent.offline_eval_gate("fp/search/main.py")

    assert accepted is False
    assert detail["error"] == "weakened_head_to_head_runtime_limits"
    assert detail["promotionAllowed"] is False


def test_test_prefilter_rejects_timeout_instead_of_leaking_exception(monkeypatch):
    import infrastructure.improve_agent as improve_agent

    monkeypatch.setattr(
        improve_agent,
        "run_owned_command",
        lambda *args, **kwargs: {"timedOut": True, "timeout": 1, "returncode": None},
    )

    assert improve_agent.run_tests() is False


def test_candidate_restore_is_compare_and_swap(tmp_path, monkeypatch):
    import infrastructure.improve_agent as improve_agent

    target = tmp_path / "fp" / "search" / "main.py"
    target.parent.mkdir(parents=True)
    original = "VALUE = 1\n"
    candidate = "VALUE = 2\n"
    target.write_text(candidate, encoding="utf-8")
    monkeypatch.setattr(improve_agent, "PROJECT_ROOT", tmp_path)
    monkeypatch.setattr(
        improve_agent,
        "IMPROVE_RECOVERY_BLOCK_PATH",
        tmp_path / ".pids" / "improve-agent-recovery-block.json",
    )

    candidate_sha = hashlib.sha256(target.read_bytes()).hexdigest()
    assert improve_agent.restore_file_snapshot("fp/search/main.py", original, candidate_sha) is True
    assert target.read_text(encoding="utf-8") == original

    target.write_text(candidate, encoding="utf-8")
    candidate_sha = hashlib.sha256(target.read_bytes()).hexdigest()
    target.write_text("VALUE = 3\n", encoding="utf-8")

    assert improve_agent.restore_file_snapshot("fp/search/main.py", original, candidate_sha) is False
    assert target.read_text(encoding="utf-8") == "VALUE = 3\n"
    recovery = json.loads(improve_agent.IMPROVE_RECOVERY_BLOCK_PATH.read_text(encoding="utf-8"))
    assert recovery["reason"] == "candidate target changed outside the improve-agent transaction"


def test_authorized_generation_uses_exact_full_source_not_prompt_view(tmp_path, monkeypatch):
    import infrastructure.improve_agent as improve_agent

    subprocess.run(["git", "init", "-q", str(tmp_path)], check=True)
    subprocess.run(["git", "-C", str(tmp_path), "config", "user.email", "proof@test.invalid"], check=True)
    subprocess.run(["git", "-C", str(tmp_path), "config", "user.name", "Proof Test"], check=True)
    target = tmp_path / "fp" / "search" / "main.py"
    target.parent.mkdir(parents=True)
    target.write_text("".join(f"VALUE_{index} = {index}\n" for index in range(600)), encoding="utf-8")
    autoresearch = tmp_path / "replay_analysis" / "autoresearch_latest.json"
    autoresearch.parent.mkdir(parents=True)
    autoresearch.write_text('{"top_issue":{"title":"proof"}}\n', encoding="utf-8")
    head = _commit(tmp_path, "baseline")
    monkeypatch.setattr(improve_agent, "PROJECT_ROOT", tmp_path)
    monkeypatch.setattr(improve_agent, "AUTORESEARCH_PATH", autoresearch)

    snapshot = improve_agent.capture_target_snapshot("fp/search/main.py")
    prompt_view = improve_agent._prompt_code_view(
        "fp/search/main.py",
        snapshot["sourceText"],
        {"top_issue": {"key": "unmatched"}},
    )
    events = []
    result = improve_agent.generate_authorized_response(
        prompt=prompt_view,
        snapshot=snapshot,
        transaction_head=head,
        autoresearch_sha256=hashlib.sha256(autoresearch.read_bytes()).hexdigest(),
        consume_authorization=lambda: events.append("authorized") or {"consumed": True},
        model_call=lambda prompt: events.append(("model", prompt)) or "model-response",
    )

    assert prompt_view != snapshot["sourceText"]
    assert result["generated"] is True
    assert events == ["authorized", ("model", prompt_view)]

    target.write_text(snapshot["sourceText"] + "CONCURRENT = True\n", encoding="utf-8")
    blocked_events = []
    blocked = improve_agent.generate_authorized_response(
        prompt=prompt_view,
        snapshot=snapshot,
        transaction_head=head,
        autoresearch_sha256=hashlib.sha256(autoresearch.read_bytes()).hexdigest(),
        consume_authorization=lambda: blocked_events.append("authorized") or {"consumed": True},
        model_call=lambda _prompt: blocked_events.append("model") or "must-not-run",
    )

    assert blocked["generated"] is False
    assert blocked["authorizationConsumed"] is False
    assert blocked_events == []
    assert any("full-source" in item for item in blocked["blockers"])


def test_private_candidate_race_never_absorbs_or_overwrites_concurrent_writer(tmp_path, monkeypatch):
    import infrastructure.improve_agent as improve_agent

    subprocess.run(["git", "init", "-q", str(tmp_path)], check=True)
    subprocess.run(["git", "-C", str(tmp_path), "config", "user.email", "proof@test.invalid"], check=True)
    subprocess.run(["git", "-C", str(tmp_path), "config", "user.name", "Proof Test"], check=True)
    target = tmp_path / "fp" / "search" / "main.py"
    target.parent.mkdir(parents=True)
    target.write_text("VALUE = 1\n", encoding="utf-8")
    head = _commit(tmp_path, "baseline")
    monkeypatch.setattr(improve_agent, "PROJECT_ROOT", tmp_path)
    snapshot = improve_agent.capture_target_snapshot("fp/search/main.py")
    patch = """--- a/fp/search/main.py
+++ b/fp/search/main.py
@@ -1 +1 @@
-VALUE = 1
+VALUE = 2
"""

    with improve_agent.prepared_candidate_workspace(
        patch,
        "fp/search/main.py",
        source_snapshot=snapshot,
        transaction_head=head,
    ) as candidate:
        candidate_root = candidate["root"]
        assert (candidate_root / "fp/search/main.py").read_text(encoding="utf-8") == "VALUE = 2\n"
        target.write_text("VALUE = 99\n", encoding="utf-8")
        committed = improve_agent.commit_and_push(
            "fp/search/main.py",
            "must not absorb a race",
            candidate_root=candidate_root,
            source_snapshot=snapshot,
            transaction_head=head,
            promotion_proof={
                "candidatePatchSha256": "a" * 64,
                "lineage": {"changeId": "b" * 64},
            },
        )

    assert committed is False
    assert target.read_text(encoding="utf-8") == "VALUE = 99\n"
    assert subprocess.check_output(
        ["git", "-C", str(tmp_path), "show", f"{head}:fp/search/main.py"],
        text=True,
    ) == "VALUE = 1\n"
    assert not (tmp_path / ".agent_diff.patch").exists()


def test_improve_lock_is_exclusive_and_owner_verified(tmp_path, monkeypatch):
    import infrastructure.improve_agent as improve_agent

    lock_path = tmp_path / ".pids" / "improve-agent.lock"
    monkeypatch.setattr(improve_agent, "IMPROVE_LOCK_PATH", lock_path)
    monkeypatch.setattr(
        improve_agent,
        "IMPROVE_RECOVERY_BLOCK_PATH",
        tmp_path / ".pids" / "improve-agent-recovery-block.json",
    )

    token, detail = improve_agent.acquire_improve_lock("fp/search/main.py")
    second_token, second_detail = improve_agent.acquire_improve_lock("fp/search/main.py")

    assert token
    assert detail["token"] == token
    assert second_token is None
    assert second_detail["error"] == "improve_agent_lock_exists"
    assert improve_agent.release_improve_lock(token) is True


def test_improvement_checkout_must_be_mutable_deku_control_plane(tmp_path, monkeypatch):
    import infrastructure.improve_agent as improve_agent

    repo = tmp_path / "control"
    target = repo / "fp" / "search" / "main.py"
    target.parent.mkdir(parents=True)
    target.write_text("VALUE = 1\n", encoding="utf-8")
    subprocess.run(["git", "init", "-q", str(repo)], check=True)
    subprocess.run(["git", "-C", str(repo), "config", "user.email", "proof@test.invalid"], check=True)
    subprocess.run(["git", "-C", str(repo), "config", "user.name", "Proof Test"], check=True)
    head = _commit(repo, "control baseline")
    tree = subprocess.check_output(
        ["git", "-C", str(repo), "rev-parse", "HEAD^{tree}"], text=True
    ).strip()
    monkeypatch.setattr(improve_agent, "PROJECT_ROOT", repo)
    lease_guard = {
        "ok": True,
        "lease": {
            "id": "deku-improve-1",
            "authorizationSha256": "a" * 64,
            "machine": "ubunztu",
            "hostName": "ubunztu",
            "maxCycles": 1,
            "sourceCommit": head,
            "sourceTree": tree,
        },
    }

    accepted = improve_agent.improvement_control_checkout_guard(
        lease_guard,
        requested_max_cycles=1,
    )
    jiggly = improve_agent.improvement_control_checkout_guard(
        {**lease_guard, "lease": {**lease_guard["lease"], "machine": "JIGGLYPUFF", "hostName": "JIGGLYPUFF"}},
        requested_max_cycles=1,
    )
    lookalike = improve_agent.improvement_control_checkout_guard(
        {**lease_guard, "lease": {**lease_guard["lease"], "machine": "dekuevil", "hostName": "ubunztu-copy"}},
        requested_max_cycles=1,
    )

    assert accepted["ready"] is True
    assert jiggly["ready"] is False
    assert lookalike["ready"] is False
    assert any("DEKU/ubunztu" in blocker for blocker in jiggly["blockers"])


def test_mutation_policy_rejects_weakened_gates_and_immutable_runtime(tmp_path, monkeypatch):
    import infrastructure.improve_agent as improve_agent

    guardrails = tmp_path / "guardrails.json"
    guardrails.write_text(
        json.dumps(
            {
                "allowed_modify": ["fp/search/main.py"],
                "never_modify": [],
                "safety": {
                    "require_test_pass": False,
                    "require_syntax_check": False,
                    "min_games_between_deploys": 1,
                },
            }
        ),
        encoding="utf-8",
    )
    monkeypatch.setattr(improve_agent, "GUARDRAILS_PATH", guardrails)
    monkeypatch.setattr(
        improve_agent,
        "PROJECT_ROOT",
        tmp_path / "Releases" / "fouler-play" / ("a" * 40),
    )

    blockers = improve_agent.mutation_policy_blockers("fp/search/main.py")

    assert "guardrail test gate must remain explicitly enabled" in blockers
    assert "guardrail syntax gate must remain explicitly enabled" in blockers
    assert "guardrail deployment spacing must remain at least 30 decisive battles" in blockers
    assert any("never_modify policy is weakened" in blocker for blocker in blockers)
    assert "guardrail ELO stop-loss must remain within (0, 50]" in blockers
    assert "immutable JIGGLYPUFF release checkout cannot be mutated" in blockers


def test_committed_candidate_must_match_exact_promotion_patch(monkeypatch, tmp_path):
    import infrastructure.improve_agent as improve_agent

    subprocess.run(["git", "init", "-q", str(tmp_path)], check=True)
    subprocess.run(["git", "-C", str(tmp_path), "config", "user.email", "proof@test.invalid"], check=True)
    subprocess.run(["git", "-C", str(tmp_path), "config", "user.name", "Proof Test"], check=True)
    target = tmp_path / "fp" / "search" / "main.py"
    target.parent.mkdir(parents=True)
    target.write_text("BASELINE = 1\n", encoding="utf-8")
    baseline = _commit(tmp_path, "baseline")
    target.write_text("BASELINE = 1\nCANDIDATE = 2\n", encoding="utf-8")
    patch = subprocess.check_output(
        ["git", "-C", str(tmp_path), "diff", "--binary", "--", "fp/search/main.py"]
    )
    candidate = _commit(tmp_path, "candidate")
    proof = {
        "baselineCommit": baseline,
        "candidateFile": "fp/search/main.py",
        "candidatePatchSha256": hashlib.sha256(patch).hexdigest(),
    }
    monkeypatch.setattr(improve_agent, "PROJECT_ROOT", tmp_path)

    ok, detail = committed_candidate_provenance(
        target_file="fp/search/main.py",
        pre_commit=baseline,
        post_commit=candidate,
        promotion_proof=proof,
    )

    assert ok is True
    assert detail["changedFiles"] == ["fp/search/main.py"]
    proof["candidatePatchSha256"] = "0" * 64
    ok, detail = committed_candidate_provenance(
        target_file="fp/search/main.py",
        pre_commit=baseline,
        post_commit=candidate,
        promotion_proof=proof,
    )
    assert ok is False
    assert any("SHA-256" in blocker for blocker in detail["blockers"])


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
