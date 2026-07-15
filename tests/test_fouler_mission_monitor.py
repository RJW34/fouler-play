import json
import os
import time
from datetime import datetime, timedelta, timezone

import pytest

from scripts import fouler_mission_monitor as monitor


@pytest.fixture(autouse=True)
def isolate_stale_active_battle_backup_dir(monkeypatch, tmp_path):
    monkeypatch.setattr(monitor, "STALE_ACTIVE_BATTLE_BACKUP_DIR", tmp_path / "stale-active-battles-backups")


def active_lease() -> dict:
    return {
        "status": "active",
        "account": "LEBOTJAMESXD00N",
        "expiresAt": (datetime.now(timezone.utc) + timedelta(hours=1)).isoformat(),
    }


def clean_rated_battles(count: int = 20, *, start_rating: int = 1300) -> list[dict]:
    return [
        {
            "battle_id": f"rated-truth-{index}",
            "rating": start_rating + (index % 6),
            "result": "win" if index % 2 else "loss",
            "timestamp": f"2026-07-05T00:{index:02d}:00+00:00",
        }
        for index in range(count)
    ]


def active_account_season(*, account: str = "LEBOTJAMESXD00N", created_at: datetime | None = None) -> dict:
    return {
        "schemaVersion": "fouler-play-account-season/v1",
        "seasonId": "test-season",
        "createdAtUtc": (created_at or datetime.now(timezone.utc)).isoformat(),
        "account": account,
        "baselineRating": 1000,
        "firstBattleStarted": False,
        "runtimeStatus": "staged-at-baseline",
    }


def test_abandoned_battle_cleanup_status_blocks_missing_result(tmp_path):
    backup_dir = tmp_path / "stale-active-battles-backups"
    backup_dir.mkdir()
    backup = backup_dir / "active_battles-20260705T120000Z.json"
    backup.write_text(
        json.dumps(
            {
                "battles": [
                    {
                        "id": "battle-gen9ou-abandoned",
                        "opponent": "staller",
                        "status": "active",
                    }
                ],
                "count": 1,
                "updated": "2026-07-05T12:00:00",
            }
        ),
        encoding="utf-8",
    )

    status = monitor.abandoned_battle_cleanup_status(
        [{"battle_id": "battle-gen9ou-previous", "timestamp": "2026-07-05T00:01:00+00:00"}],
        backup_dir=backup_dir,
    )

    assert status["ready"] is False
    assert status["status"] == "abandoned-active-battle-without-result"
    assert status["missingBattleIds"] == ["battle-gen9ou-abandoned"]
    assert status["sourceBackupPath"].endswith("active_battles-20260705T120000Z.json")


def test_abandoned_battle_cleanup_ignores_preseason_backup(tmp_path):
    backup_dir = tmp_path / "stale-active-battles-backups"
    backup_dir.mkdir()
    backup = backup_dir / "active_battles-old-season.json"
    backup.write_text(
        json.dumps({"battles": [{"id": "battle-gen9ou-old-account"}]}),
        encoding="utf-8",
    )
    season_started_at = datetime.now(timezone.utc) + timedelta(seconds=1)

    status = monitor.abandoned_battle_cleanup_status(
        [],
        backup_dir=backup_dir,
        season_started_at=season_started_at,
        season_account="DekuFoulerLab",
    )

    assert status["ready"] is True
    assert status["status"] == "clear"
    assert status["skippedPreSeasonBackups"] == 1
    assert status["seasonAccount"] == "DekuFoulerLab"


def test_abandoned_battle_cleanup_blocks_start_gate(monkeypatch, tmp_path):
    backup_dir = tmp_path / "stale-active-battles-backups"
    backup_dir.mkdir()
    backup = backup_dir / "active_battles-20260705T120000Z.json"
    backup.write_text(
        json.dumps(
            {
                "battles": [{"id": "battle-gen9ou-abandoned", "opponent": "staller"}],
                "count": 1,
            }
        ),
        encoding="utf-8",
    )
    monkeypatch.setattr(monitor, "STALE_ACTIVE_BATTLE_BACKUP_DIR", backup_dir)
    monkeypatch.setattr(monitor, "current_source_commit", lambda: "abc1234")
    monkeypatch.setattr(monitor, "offline_eval_resume_proof_status", lambda: {"ready": True})
    monkeypatch.setattr(monitor, "active_improvement_proof_status", lambda: {"ready": True})

    payload = monitor.classify_mission(
        health={
            "checkedAt": datetime.now(timezone.utc).isoformat(),
            "readiness": {"runtimeReady": True},
            "runtimeOwnership": {"battleRunnerProcessCount": 1, "duplicateBattleRunners": False},
            "activeBattleCount": 1,
            "discordQueue": {"backlogClassification": {"blocking": False}},
        },
        supervisor={},
        lease=active_lease(),
        battles=clean_rated_battles(20, start_rating=1710),
        max_health_age_seconds=60,
        loss_streak_threshold=5,
        low_win_rate_threshold=0.3,
        rating_drawdown_threshold=100,
        rating_drawdown_window=20,
        elo_proof=sustain_proof(),
        requested_run_count=1,
        requested_max_cycles=1,
    )

    issue_ids = {item["id"] for item in payload["issues"]}
    assert monitor.ABANDONED_BATTLE_ISSUE_ID in issue_ids
    assert monitor.ABANDONED_BATTLE_ISSUE_ID in payload["startGate"]["blockingIssueIds"]
    assert payload["startGate"]["ready"] is False


def test_runtime_python_uses_configured_override(monkeypatch):
    monkeypatch.setenv("FOULER_RUNTIME_PYTHON", r"C:\Tools\fouler-python.exe")

    assert monitor.runtime_python() == r"C:\Tools\fouler-python.exe"


def test_runtime_python_prefers_eval_venv_when_primary_missing(monkeypatch, tmp_path):
    monkeypatch.delenv("FOULER_RUNTIME_PYTHON", raising=False)
    monkeypatch.setattr(monitor, "ROOT", tmp_path)
    candidate = tmp_path / ".venv-eval" / "Scripts" / "python.exe"
    candidate.parent.mkdir(parents=True)
    candidate.write_text("", encoding="utf-8")

    assert monitor.runtime_python() == str(candidate)


def test_current_source_commit_reads_git_head(monkeypatch):
    def fake_run_command(command, *, timeout=60):
        assert command == ["git", "rev-parse", "HEAD"]
        assert timeout == 5
        return {"ok": True, "stdoutTail": "abc1234\n"}

    monkeypatch.setattr(monitor, "run_command", fake_run_command)

    assert monitor.current_source_commit() == "abc1234"


def proof_game(index: int, *, rating: int, team: str, result: str = "win") -> dict:
    return {
        "battleId": f"battle-gen9ou-sustain-{index}",
        "result": result,
        "replayUrl": f"https://replay.pokemonshowdown.com/gen9ou-sustain-{index}",
        "decisionTracePath": f"logs/decision_traces/gen9ou-sustain-{index}.json",
        "ratingAfter": rating,
        "teamFile": team,
        "timestamp": f"2026-05-25T12:{index:02d}:00+00:00",
    }


def sustain_proof(*, account: str = "LEBOTJAMESXD00N", checked_at: datetime | None = None) -> dict:
    checked_at = checked_at or datetime.now(timezone.utc)
    source_commit = monitor.current_source_commit() or "abc1234"
    games = []
    index = 0
    for team in monitor.SUSTAIN_REQUIRED_TEAMS:
        for _ in range(10):
            games.append(proof_game(index, rating=1710 + index, team=team, result="win" if index % 3 else "loss"))
            index += 1
    return {
        "schemaVersion": "fouler-play-elo-proof/v1",
        "format": "gen9ou",
        "sourceCommit": source_commit,
        "source": {"sourceCommit": source_commit, "generatedBy": "unit-test"},
        "checkedAtUtc": checked_at.isoformat(),
        "account": {"showdownUserId": account, "ratingSource": "pokemonshowdown-user-api"},
        "target": {
            "ratingFloor": 1700,
            "minimumCompletedGames": 30,
            "sustainMinimumGames": 30,
            "sustainMinimumGamesPerTeam": 10,
            "maximumSustainDrawdown": 75,
            "maximumPreTargetDrawdown": 75,
            "minimumSustainWinRate": 0.5,
            "requiredTeams": list(monitor.SUSTAIN_REQUIRED_TEAMS),
            "noCherryPicking": True,
            "uninterruptedPostTargetFloorRequired": True,
        },
        "session": {
            "startedAt": checked_at.isoformat(),
            "endedAt": checked_at.isoformat(),
            "runCountTarget": 30,
            "maxConcurrentBattles": 1,
        },
        "analysis": {
            "generatedAtUtc": checked_at.isoformat(),
            "autoresearchJsonPath": "replay_analysis/autoresearch_latest.json",
            "autoresearchReportPath": "replay_analysis/reports/autoresearch_latest.md",
            "decisionTraceReviewPath": "devstream/truth/decision-trace-review.json",
            "topIssue": "bounded sustain proof fixture",
            "reviewedBattleCount": len(games),
            "lossesAnalyzed": sum(1 for game in games if game["result"] == "loss"),
        },
        "games": games,
        "summary": {
            "completedGames": len(games),
            "wins": sum(1 for game in games if game["result"] == "win"),
            "losses": sum(1 for game in games if game["result"] == "loss"),
            "peakRating": max(game["ratingAfter"] for game in games),
            "finalRating": games[-1]["ratingAfter"],
            "passesTarget": True,
            "sustainedTarget": True,
            "sustainWindowGames": len(games),
            "gamesAtOrAboveFloor": len(games),
            "belowFloorAfterFirstTarget": 0,
            "maxSustainDrawdown": 0,
            "preTargetRatedGames": 0,
            "maxPreTargetDrawdown": None,
            "sustainReplayProofCount": len(games),
            "missingSustainReplayCount": 0,
            "mismatchedSustainReplayCount": 0,
            "missingSustainBattleIdCount": 0,
            "duplicateSustainBattleIdCount": 0,
            "duplicateSustainReplayIdCount": 0,
            "unknownSustainTeamCount": 0,
            "decisionTraceProofCount": len(games),
            "missingDecisionTraceCount": 0,
            "duplicateDecisionTraceProofCount": 0,
            "duplicateDecisionTraceProofs": [],
            "missingBattleTimestampCount": 0,
            "outOfOrderBattleTimestampCount": 0,
            "chronologicalBattleOrderComplete": True,
            "analysisEvidenceComplete": True,
            "sustainEvidenceShapeComplete": True,
            "sustainProofComplete": True,
            "teamCoverage": {team: 10 for team in monitor.SUSTAIN_REQUIRED_TEAMS},
        },
    }


def test_elo_sustain_proof_carries_live_profile_rating_without_satisfying_sustain(monkeypatch):
    monkeypatch.setattr(monitor, "current_source_commit", lambda: "abc1234")
    proof = sustain_proof(account="thepeakmons")
    proof["sourceCommit"] = "abc1234"
    proof["source"]["sourceCommit"] = "abc1234"
    proof["games"] = [proof_game(0, rating=1153, team="fat-team-1-stall", result="win")]
    proof["summary"].update(
        {
            "completedGames": 1,
            "wins": 1,
            "losses": 0,
            "peakRating": 1153,
            "finalRating": 1153,
            "currentRating": 1197.25,
            "currentRatingSource": "pokemonshowdown-user-api",
            "liveProfileRating": 1197.25,
            "passesTarget": False,
            "sustainedTarget": False,
            "sustainWindowGames": 0,
            "gamesAtOrAboveFloor": 0,
            "sustainProofComplete": False,
            "teamCoverage": {team: 0 for team in monitor.SUSTAIN_REQUIRED_TEAMS},
            "sustainReplayProofCount": 0,
            "decisionTraceProofCount": 0,
        }
    )
    proof["liveProfile"] = {
        "status": "fetched",
        "rating": 1197.25,
        "checkedAtUtc": proof["checkedAtUtc"],
    }

    status = monitor.elo_sustain_proof_status(
        proof,
        lease={"status": "active", "account": "thepeakmons", "expiresAt": "2999-01-01T00:00:00+00:00"},
        max_age_seconds=3600,
        current_checkout_commit="abc1234",
    )

    assert status["ready"] is False
    assert status["ratings"]["currentRating"] == 1197.25
    assert status["ratings"]["summaryFinalRating"] == 1153
    assert any("never reaches 1700" in blocker for blocker in status["blockers"])


def accepted_offline_eval_resume_proof() -> dict:
    return {
        "policy": monitor.OFFLINE_EVAL_RESUME_PROOF_POLICY,
        "ready": True,
        "status": "accepted",
        "blockers": [],
        "resultProof": {
            "schemaVersion": monitor.OFFLINE_EVAL_RESULT_PROOF_SCHEMA_VERSION,
            "ready": True,
            "accepted": True,
            "status": "accepted",
            "verdict": "accepted",
            "candidateBattles": 200,
            "compareCandidateBattles": 200,
            "requiredBattles": 200,
            "noRuntimeActions": True,
        },
        "noRuntimeActions": True,
    }


def accepted_active_improvement_proof() -> dict:
    return {
        "policy": monitor.ACTIVE_IMPROVEMENT_PROOF_POLICY,
        "ready": True,
        "status": "accepted",
        "blockers": [],
        "path": "devstream/truth/post-packet-eval.json",
        "checkedAtUtc": datetime.now(timezone.utc).isoformat(),
        "ageSeconds": 0,
        "maxAgeSeconds": monitor.ACTIVE_IMPROVEMENT_PROOF_MAX_AGE_SECONDS,
        "packet": {
            "id": "fouler-auto-001-hazard-pressure-is-being-lost",
            "status": "implemented",
            "findingKey": "hazard_pressure",
        },
        "latestBattle": {
            "id": "battle-gen9ou-post",
            "performanceImprovementVerified": True,
        },
        "proofWindow": {
            "latestBattleAfterPacket": True,
            "autoresearchCoversLatestBattle": True,
        },
        "failureClass": {"status": "reduced"},
        "evidenceIntegrity": {"ok": True},
        "noRuntimeActions": True,
    }


def missing_active_improvement_proof() -> dict:
    return {
        "policy": monitor.ACTIVE_IMPROVEMENT_PROOF_POLICY,
        "ready": False,
        "status": "missing",
        "blockers": ["missing devstream/truth/post-packet-eval.json"],
        "noRuntimeActions": True,
    }


def failed_active_improvement_proof() -> dict:
    return {
        "policy": monitor.ACTIVE_IMPROVEMENT_PROOF_POLICY,
        "ready": False,
        "status": "blocked",
        "blockers": [
            "active improvement proof status must be post-packet-eval-improving",
            "active improvement proof must show a positive aggregate performance signal",
            "active improvement proof must show the packet failure class is reduced",
        ],
        "proofWindow": {
            "latestBattleAfterPacket": True,
            "autoresearchCoversLatestBattle": True,
        },
        "latestBattle": {
            "id": "battle-gen9ou-failed-recovery",
            "performanceImprovementVerified": False,
        },
        "failureClass": {"status": "unresolved-with-fresh-evidence"},
        "noRuntimeActions": True,
    }


def missing_offline_eval_resume_proof() -> dict:
    return {
        "policy": monitor.OFFLINE_EVAL_RESUME_PROOF_POLICY,
        "ready": False,
        "status": "blocked",
        "blockers": ["offline eval result proof ready must be true"],
        "resultProof": {
            "schemaVersion": monitor.OFFLINE_EVAL_RESULT_PROOF_SCHEMA_VERSION,
            "ready": False,
            "accepted": False,
            "status": "missing",
            "verdict": "missing",
            "candidateBattles": None,
            "compareCandidateBattles": None,
            "requiredBattles": 200,
            "noRuntimeActions": True,
        },
        "noRuntimeActions": True,
    }


def write_offline_eval_result_artifacts(root, *, battles: int = 200, accepted: bool = True) -> None:
    results = root / "eval_results" / "offline"
    results.mkdir(parents=True)
    candidate = {
        "label": "candidate",
        "battles": battles,
        "fouler_wins": 126,
        "fouler_win_rate": 0.63,
    }
    compare = {
        "candidate": {
            "label": "candidate",
            "battles": battles,
            "fouler_wins": 126,
            "fouler_win_rate": 0.63,
        },
        "ACCEPT": accepted,
    }
    (results / "candidate.json").write_text(json.dumps(candidate), encoding="utf-8")
    (results / "compare-frozen-vs-candidate.json").write_text(json.dumps(compare), encoding="utf-8")


def test_offline_eval_resume_proof_status_accepts_existing_result_artifacts(tmp_path):
    write_offline_eval_result_artifacts(tmp_path)

    status = monitor.offline_eval_resume_proof_status(root=tmp_path, env={})

    assert status["ready"] is True
    assert status["status"] == "accepted"
    assert status["blockers"] == []
    assert status["resultProof"]["candidateBattles"] == 200
    assert status["resultProof"]["compareCandidateBattles"] == 200
    assert status["noRuntimeActions"] is True


def test_offline_eval_resume_proof_status_rejects_missing_artifacts(tmp_path):
    status = monitor.offline_eval_resume_proof_status(root=tmp_path, env={})

    assert status["ready"] is False
    assert status["status"] == "blocked"
    assert "offline eval result proof ready must be true" in status["blockers"]
    assert status["resultProof"]["status"] == "missing"
    assert status["noRuntimeActions"] is True


def test_active_improvement_proof_status_accepts_current_improving_post_packet_eval():
    proof = {
        "schemaVersion": monitor.ACTIVE_IMPROVEMENT_PROOF_SCHEMA_VERSION,
        "checkedAtUtc": datetime.now(timezone.utc).isoformat(),
        "status": "post-packet-eval-improving",
        "actionablePostPacketEval": True,
        "runtimeMutationTouched": False,
        "networkSendAllowed": False,
        "packet": {
            "id": "fouler-auto-001-hazard-pressure-is-being-lost",
            "status": "implemented",
            "findingKey": "hazard_pressure",
            "path": "devstream/work_packets/generated/fouler-auto-001.json",
        },
        "latestBattle": {
            "id": "battle-gen9ou-post",
            "performanceImprovementVerified": True,
        },
        "proofWindow": {
            "latestBattleAfterPacket": True,
            "autoresearchCoversLatestBattle": True,
        },
        "failureClass": {"status": "reduced"},
        "evidenceIntegrity": {"ok": True},
        "blockers": [],
    }

    status = monitor.active_improvement_proof_status(proof)

    assert status["ready"] is True
    assert status["status"] == "accepted"
    assert status["packet"]["status"] == "implemented"
    assert status["noRuntimeActions"] is True


def test_active_improvement_proof_status_accepts_preserved_post_packet_eval():
    proof = {
        "schemaVersion": monitor.ACTIVE_IMPROVEMENT_PROOF_SCHEMA_VERSION,
        "checkedAtUtc": datetime.now(timezone.utc).isoformat(),
        "status": "post-packet-eval-accepted",
        "actionablePostPacketEval": True,
        "runtimeMutationTouched": False,
        "networkSendAllowed": False,
        "packet": {"status": "implemented"},
        "latestBattle": {"performanceImprovementVerified": True},
        "proofWindow": {
            "latestBattleAfterPacket": True,
            "autoresearchCoversLatestBattle": True,
            "preservationSatisfied": True,
        },
        "failureClass": {"status": "reduced"},
        "evidenceIntegrity": {"ok": True},
        "blockers": [],
    }

    status = monitor.active_improvement_proof_status(proof)

    assert status["ready"] is True
    assert status["status"] == "accepted"


def test_active_improvement_proof_status_rejects_unresolved_or_shallow_packet_eval():
    proof = {
        "schemaVersion": monitor.ACTIVE_IMPROVEMENT_PROOF_SCHEMA_VERSION,
        "checkedAtUtc": (datetime.now(timezone.utc) - timedelta(days=2)).isoformat(),
        "status": "post-packet-eval-actionable-unresolved",
        "actionablePostPacketEval": True,
        "runtimeMutationTouched": False,
        "networkSendAllowed": False,
        "packet": {"status": "implemented"},
        "latestBattle": {"performanceImprovementVerified": False},
        "proofWindow": {
            "latestBattleAfterPacket": True,
            "autoresearchCoversLatestBattle": True,
        },
        "failureClass": {"status": "unresolved-with-fresh-evidence"},
        "evidenceIntegrity": {"ok": False},
        "blockers": ["packet still needs follow-up"],
    }

    status = monitor.active_improvement_proof_status(proof)

    assert status["ready"] is False
    assert any("post-packet-eval-improving or post-packet-eval-accepted" in blocker for blocker in status["blockers"])
    assert any("evidenceIntegrity.ok" in blocker for blocker in status["blockers"])
    assert any("stale" in blocker for blocker in status["blockers"])


def test_active_improvement_proof_status_rejects_aggregate_gain_without_targeted_reduction():
    proof = {
        "schemaVersion": monitor.ACTIVE_IMPROVEMENT_PROOF_SCHEMA_VERSION,
        "checkedAtUtc": datetime.now(timezone.utc).isoformat(),
        "status": "post-packet-eval-improving",
        "actionablePostPacketEval": True,
        "runtimeMutationTouched": False,
        "networkSendAllowed": False,
        "packet": {"status": "implemented"},
        "latestBattle": {"performanceImprovementVerified": True},
        "proofWindow": {
            "latestBattleAfterPacket": True,
            "autoresearchCoversLatestBattle": True,
        },
        "failureClass": {"status": "unresolved-with-fresh-evidence"},
        "evidenceIntegrity": {"ok": True},
        "blockers": [],
    }

    status = monitor.active_improvement_proof_status(proof)

    assert status["ready"] is False
    assert any("failure class is reduced" in blocker for blocker in status["blockers"])


def test_classifies_completed_supervisor_idle_runtime_as_repairable(monkeypatch, tmp_path):
    monkeypatch.setattr(monitor, "SUPERVISOR_STOP_FILE", tmp_path / "supervisor.stop")
    health = {
        "checkedAt": datetime.now(timezone.utc).isoformat(),
        "running": False,
        "healthy": False,
        "status": "idle",
        "readiness": {"runtimeReady": False},
        "runtimeOwnership": {"battleRunnerProcessCount": 0, "duplicateBattleRunners": False},
    }
    supervisor = {
        "state": "completed-max-cycles",
        "completedLearningCycles": 12,
        "completedAt": datetime.now(timezone.utc).isoformat(),
    }

    payload = monitor.classify_mission(
        health=health,
        supervisor=supervisor,
        lease=active_lease(),
        battles=[],
        max_health_age_seconds=300,
        loss_streak_threshold=5,
        low_win_rate_threshold=0.45,
        rating_drawdown_threshold=75.0,
        rating_drawdown_window=60,
    )

    issue_ids = {item["id"] for item in payload["issues"]}
    assert "fouler-runtime-idle" in issue_ids
    assert "fouler-supervisor-max-cycles-complete" in issue_ids
    assert payload["runtimeIdle"] is True
    assert payload["runtimeLeaseActive"] is True
    assert payload["duplicateRunners"] is False


def test_elo_sustain_proof_blocks_stale_wrong_account_and_low_rating():
    old = datetime.now(timezone.utc) - timedelta(days=3)
    proof = sustain_proof(account="npctypebeat", checked_at=old)
    for game in proof["games"]:
        game["ratingAfter"] = 1490
    proof["summary"]["peakRating"] = 1490
    proof["summary"]["finalRating"] = 1490
    proof["summary"]["passesTarget"] = False

    status = monitor.elo_sustain_proof_status(
        proof,
        lease=active_lease(),
        max_age_seconds=3600,
    )

    assert status["ready"] is False
    assert status["status"] == "blocked"
    assert status["account"]["matched"] is False
    assert any("does not match expected account" in reason for reason in status["blockers"])
    assert any("stale" in reason for reason in status["blockers"])
    assert any("never reaches 1700" in reason for reason in status["blockers"])


def test_elo_sustain_proof_requires_all_three_fixed_teams():
    proof = sustain_proof()
    proof["games"] = [
        proof_game(index, rating=1710 + (index % 8), team="fat-team-1-stall")
        for index in range(30)
    ]

    status = monitor.elo_sustain_proof_status(
        proof,
        lease=active_lease(),
        max_age_seconds=3600,
    )

    assert status["ready"] is False
    assert status["teams"]["gamesAtOrAboveFloorByTeam"]["fat-team-1-stall"] == 30
    assert {item["team"] for item in status["teams"]["missingTeamMinimums"]} == {
        "fat-team-2-balance",
        "fat-team-3-dondozo",
    }


def test_elo_sustain_proof_accepts_clean_1700_sustain_window():
    status = monitor.elo_sustain_proof_status(
        sustain_proof(),
        lease=active_lease(),
        max_age_seconds=3600,
    )

    assert status["ready"] is True
    assert status["status"] == "passed"
    assert status["counts"]["gamesAtOrAboveFloor"] == 30
    assert status["ratings"]["finalRating"] >= 1700
    assert status["ratings"]["maxPreTargetDrawdown"] is None
    assert not status["teams"]["missingTeamMinimums"]
    assert status["counts"]["missingSustainReplayCount"] == 0
    assert status["counts"]["unknownSustainTeamCount"] == 0
    assert status["counts"]["missingDecisionTraceCount"] == 0
    assert status["analysis"]["ready"] is True
    assert status["targetContract"]["ready"] is True
    assert status["summaryConsistency"]["ready"] is True


def test_elo_sustain_proof_requires_declared_canonical_target_contract():
    proof = sustain_proof()
    proof["target"].pop("requiredTeams")
    proof["target"].pop("uninterruptedPostTargetFloorRequired")
    proof["target"]["minimumSustainWinRate"] = 0.25
    proof["target"]["maximumSustainDrawdown"] = 90

    status = monitor.elo_sustain_proof_status(
        proof,
        lease=active_lease(),
        max_age_seconds=3600,
    )

    assert status["ready"] is False
    assert status["targetContract"]["ready"] is False
    assert status["targetContract"]["missingDeclaredTeams"] == list(monitor.SUSTAIN_REQUIRED_TEAMS)
    assert any("target.requiredTeams" in blocker for blocker in status["blockers"])
    assert any("target.minimumSustainWinRate" in blocker for blocker in status["blockers"])
    assert any("target.maximumSustainDrawdown" in blocker for blocker in status["blockers"])
    assert any("target.uninterruptedPostTargetFloorRequired" in blocker for blocker in status["blockers"])


def test_elo_sustain_proof_rejects_summary_that_disagrees_with_games():
    proof = sustain_proof()
    proof["summary"]["finalRating"] = 1900
    proof["summary"]["teamCoverage"]["fat-team-1-stall"] = 0

    status = monitor.elo_sustain_proof_status(
        proof,
        lease=active_lease(),
        max_age_seconds=3600,
    )

    assert status["ready"] is False
    assert status["summaryConsistency"]["ready"] is False
    assert any("summary.finalRating" in blocker for blocker in status["blockers"])
    assert any("summary.teamCoverage.fat-team-1-stall" in blocker for blocker in status["blockers"])


def test_elo_sustain_proof_requires_source_commit_and_blocks_source_drift():
    proof = sustain_proof()
    proof.pop("sourceCommit", None)
    proof.pop("source", None)

    missing = monitor.elo_sustain_proof_status(
        proof,
        lease=active_lease(),
        max_age_seconds=3600,
        current_checkout_commit="current123",
    )

    assert missing["ready"] is False
    assert "ELO proof sourceCommit is missing or too short" in missing["blockers"]
    assert missing["source"]["sourceCommit"] is None

    proof = sustain_proof()
    proof["sourceCommit"] = "old1234567"
    proof["source"] = {"sourceCommit": "old1234567", "generatedBy": "unit-test"}

    drift = monitor.elo_sustain_proof_status(
        proof,
        lease=active_lease(),
        max_age_seconds=3600,
        current_checkout_commit="current123",
    )

    assert drift["ready"] is False
    assert "ELO proof sourceCommit does not match current checkout" in drift["blockers"]
    assert drift["source"]["sourceCommitMatchesCurrent"] is False


def test_elo_sustain_proof_freshness_ignores_analysis_timestamp_without_fresh_proof_anchor():
    old = datetime.now(timezone.utc) - timedelta(days=3)
    proof = sustain_proof(checked_at=old)
    proof.pop("checkedAtUtc")
    proof["analysis"]["generatedAtUtc"] = datetime.now(timezone.utc).isoformat()

    status = monitor.elo_sustain_proof_status(
        proof,
        lease=active_lease(),
        max_age_seconds=24 * 60 * 60,
        current_checkout_commit=monitor.current_source_commit(),
    )

    assert status["ready"] is False
    assert status["freshness"]["checkedAt"] == old.isoformat()
    assert status["freshness"]["analysisCheckedAt"] != status["freshness"]["checkedAt"]
    assert "replay-analysis timestamps are diagnostic" in status["freshness"]["freshnessAnchorPolicy"]
    assert any("ELO proof is stale" in blocker for blocker in status["blockers"])


def test_elo_sustain_proof_requires_replay_and_fixed_team_attribution():
    proof = sustain_proof()
    proof["games"][0]["replayUrl"] = ""
    proof["games"][1]["teamFile"] = "teams/gen9/ou/experimental-team"

    status = monitor.elo_sustain_proof_status(
        proof,
        lease=active_lease(),
        max_age_seconds=3600,
    )

    assert status["ready"] is False
    assert status["counts"]["missingSustainReplayCount"] == 1
    assert status["counts"]["unknownSustainTeamCount"] == 1
    assert any("without Pokemon Showdown replay proof" in blocker for blocker in status["blockers"])
    assert any("without fixed-team attribution" in blocker for blocker in status["blockers"])
    assert status["replays"]["missingSustainReplayBattleIds"] == ["battle-gen9ou-sustain-0"]


def test_elo_sustain_proof_requires_decision_trace_and_analysis_artifacts():
    proof = sustain_proof()
    proof["games"][0].pop("decisionTracePath")
    proof.pop("analysis")

    status = monitor.elo_sustain_proof_status(
        proof,
        lease=active_lease(),
        max_age_seconds=3600,
    )

    assert status["ready"] is False
    assert status["counts"]["missingDecisionTraceCount"] == 1
    assert status["decisionTraces"]["missingDecisionTraceBattleIds"] == ["battle-gen9ou-sustain-0"]
    assert status["analysis"]["ready"] is False
    assert set(status["analysis"]["missingPathKeys"]) == {
        "autoresearchJsonPath",
        "autoresearchReportPath",
        "decisionTraceReviewPath",
    }
    assert any("without decision trace proof" in blocker for blocker in status["blockers"])
    assert any("post-window analysis artifact path" in blocker for blocker in status["blockers"])


def test_elo_sustain_proof_rejects_duplicate_decision_trace_evidence():
    proof = sustain_proof()
    proof["games"][1]["decisionTracePath"] = proof["games"][0]["decisionTracePath"]

    status = monitor.elo_sustain_proof_status(
        proof,
        lease=active_lease(),
        max_age_seconds=3600,
    )

    assert status["ready"] is False
    assert status["counts"]["duplicateDecisionTraceProofCount"] == 1
    assert status["decisionTraces"]["duplicateDecisionTraceProofs"] == [
        "logs/decision_traces/gen9ou-sustain-0.json"
    ]
    assert any("duplicate sustain-window decision trace proof" in blocker for blocker in status["blockers"])


def test_elo_sustain_proof_rejects_shallow_replay_and_duplicate_battle_evidence():
    proof = sustain_proof()
    proof["games"][0]["battleId"] = "battle-gen9ou-sustain-1"
    proof["games"][0]["replayUrl"] = "https://replay.pokemonshowdown.com/gen9ou-sustain-1"
    proof["games"][3]["replayUrl"] = "https://replay.pokemonshowdown.com/gen9ou-other"
    proof["games"][4]["replayUrl"] = "https://replay.pokemonshowdown.com/unknown"

    status = monitor.elo_sustain_proof_status(
        proof,
        lease=active_lease(),
        max_age_seconds=3600,
    )

    assert status["ready"] is False
    assert status["counts"]["duplicateSustainBattleIdCount"] == 1
    assert status["counts"]["duplicateSustainReplayIdCount"] == 1
    assert status["counts"]["mismatchedSustainReplayCount"] == 1
    assert status["counts"]["missingSustainReplayCount"] == 1
    assert "gen9ou-sustain-1" in status["replays"]["duplicateSustainBattleIds"]
    assert "gen9ou-sustain-1" in status["replays"]["duplicateSustainReplayIds"]
    assert status["replays"]["mismatchedSustainReplayBattleIds"] == ["battle-gen9ou-sustain-3"]
    assert status["replays"]["missingSustainReplayBattleIds"] == ["battle-gen9ou-sustain-4"]
    assert any("duplicate sustain-window battle id" in blocker for blocker in status["blockers"])
    assert any("duplicate sustain-window replay id" in blocker for blocker in status["blockers"])
    assert any("replay URL(s) that do not match" in blocker for blocker in status["blockers"])


def test_elo_sustain_proof_requires_parseable_timestamps_for_all_games():
    proof = sustain_proof()
    proof["games"][0].pop("timestamp")

    status = monitor.elo_sustain_proof_status(
        proof,
        lease=active_lease(),
        max_age_seconds=3600,
    )

    assert status["ready"] is False
    assert status["counts"]["missingBattleTimestampCount"] == 1
    assert status["battleOrder"]["chronological"] is False
    assert status["battleOrder"]["missingBattleTimestampBattleIds"] == ["battle-gen9ou-sustain-0"]
    assert any("without parseable battle timestamp proof" in blocker for blocker in status["blockers"])


def test_elo_sustain_proof_rejects_out_of_order_game_timestamps():
    proof = sustain_proof()
    proof["games"][1]["timestamp"] = "2026-05-25T11:59:00+00:00"

    status = monitor.elo_sustain_proof_status(
        proof,
        lease=active_lease(),
        max_age_seconds=3600,
    )

    assert status["ready"] is False
    assert status["counts"]["outOfOrderBattleTimestampCount"] == 1
    assert status["battleOrder"]["chronological"] is False
    assert status["battleOrder"]["outOfOrderBattleTimestampBattleIds"] == ["battle-gen9ou-sustain-1"]
    assert any("out of chronological order" in blocker for blocker in status["blockers"])


def test_failed_elo_sustain_proof_opens_mission_issue_without_stop_loss(monkeypatch, tmp_path):
    monkeypatch.setattr(monitor, "SUPERVISOR_STOP_FILE", tmp_path / "supervisor.stop")
    health = {
        "checkedAt": datetime.now(timezone.utc).isoformat(),
        "running": True,
        "readiness": {"runtimeReady": True},
        "runtimeOwnership": {
            "battleRunnerProcessCount": 1,
            "duplicateBattleRunners": False,
            "showdownAccount": "LEBOTJAMESXD00N",
        },
    }
    proof = sustain_proof(checked_at=datetime.now(timezone.utc) - timedelta(hours=2))

    payload = monitor.classify_mission(
        health=health,
        supervisor={"state": "battle-cycle-in-flight"},
        lease=active_lease(),
        battles=clean_rated_battles(),
        max_health_age_seconds=300,
        loss_streak_threshold=5,
        low_win_rate_threshold=0.45,
        rating_drawdown_threshold=75.0,
        rating_drawdown_window=60,
        elo_proof=proof,
        max_elo_proof_age_seconds=60,
    )

    issue_ids = {item["id"] for item in payload["issues"]}
    assert "fouler-elo-sustain-proof-missing-or-failing" in issue_ids
    assert payload["sessionGovernance"]["allowLaddering"] is True
    assert payload["startGate"]["ready"] is True
    assert payload["startGate"]["decision"] == "allow-next-proof-window"
    assert "fouler-elo-sustain-proof-missing-or-failing" in payload["startGate"]["allowedOpenIssueIds"]
    assert payload["repairQueue"]["status"] == "actionable"
    assert payload["repairQueue"]["packetCount"] == 1
    assert payload["repairQueue"]["nextPacketId"] == "fouler-1700-sustain-proof"
    packet = payload["repairQueue"]["packets"][0]
    assert packet["id"] == "fouler-1700-sustain-proof"
    assert packet["status"] == "ready-for-bounded-proof-window"
    assert packet["authority"]["runtimeMutationAllowed"] is False
    assert packet["authority"]["streamKeyRequired"] is False
    assert "open-only-the-next-finite-proof-window-if-start-gate-allows" in packet["nextActions"]


def test_repair_queue_empty_when_sustain_proof_and_start_gate_are_clean(monkeypatch, tmp_path):
    monkeypatch.setattr(monitor, "SUPERVISOR_STOP_FILE", tmp_path / "supervisor.stop")
    monkeypatch.setattr(monitor, "offline_eval_resume_proof_status", accepted_offline_eval_resume_proof)
    monkeypatch.setattr(monitor, "active_improvement_proof_status", accepted_active_improvement_proof)
    health = {
        "checkedAt": datetime.now(timezone.utc).isoformat(),
        "running": True,
        "readiness": {"runtimeReady": True},
        "runtimeOwnership": {
            "battleRunnerProcessCount": 1,
            "duplicateBattleRunners": False,
            "showdownAccount": "LEBOTJAMESXD00N",
        },
        "accountAuthority": {
            "expectedAccount": "LEBOTJAMESXD00N",
            "claims": [{"source": "runtime-env", "account": "LEBOTJAMESXD00N"}],
        },
    }

    payload = monitor.classify_mission(
        health=health,
        supervisor={"state": "battle-cycle-in-flight"},
        lease=active_lease(),
        battles=clean_rated_battles(),
        max_health_age_seconds=300,
        loss_streak_threshold=5,
        low_win_rate_threshold=0.45,
        rating_drawdown_threshold=75.0,
        rating_drawdown_window=60,
        elo_proof=sustain_proof(),
    )

    assert payload["startGate"]["ready"] is True
    queue = payload["repairQueue"]
    assert queue["schemaVersion"] == monitor.REPAIR_QUEUE_SCHEMA_VERSION
    assert queue["projectId"] == "fouler-play"
    assert queue["status"] == "ready"
    assert queue["packetCount"] == 0
    assert queue["blockedIssueIds"] == []
    assert queue["triggerIssueIds"] == []
    assert queue["nextPacketId"] is None
    assert queue["packets"] == []
    assert queue["authority"]["runtimeMutationAllowed"] is False
    assert queue["authority"]["streamKeyRequired"] is False
    assert queue["noRuntimeActions"] is True


def test_runtime_account_authority_mismatch_blocks_start_gate(monkeypatch, tmp_path):
    monkeypatch.setattr(monitor, "SUPERVISOR_STOP_FILE", tmp_path / "supervisor.stop")
    health = {
        "checkedAt": datetime.now(timezone.utc).isoformat(),
        "running": True,
        "readiness": {"runtimeReady": True},
        "runtimeOwnership": {
            "battleRunnerProcessCount": 1,
            "duplicateBattleRunners": False,
            "showdownAccount": "npctypebeat",
        },
        "accountAuthority": {
            "claims": [{"source": "runtime-env", "account": "npctypebeat"}],
            "expectedAccount": "npctypebeat",
        },
    }

    payload = monitor.classify_mission(
        health=health,
        supervisor={"state": "battle-cycle-in-flight"},
        lease=active_lease(),
        battles=clean_rated_battles(),
        max_health_age_seconds=300,
        loss_streak_threshold=5,
        low_win_rate_threshold=0.45,
        rating_drawdown_threshold=75.0,
        rating_drawdown_window=60,
        elo_proof=sustain_proof(),
        max_elo_proof_age_seconds=3600,
    )

    issue_ids = {item["id"] for item in payload["issues"]}
    assert monitor.ACCOUNT_AUTHORITY_MISMATCH_ISSUE_ID in issue_ids
    assert payload["accountAuthority"]["ready"] is False
    assert any(
        "does not match expected account LEBOTJAMESXD00N" in blocker
        for blocker in payload["accountAuthority"]["blockers"]
    )
    assert payload["sessionGovernance"]["allowLaddering"] is True
    assert payload["startGate"]["ready"] is False
    assert monitor.ACCOUNT_AUTHORITY_MISMATCH_ISSUE_ID in payload["startGate"]["blockingIssueIds"]


def test_missing_runtime_account_telemetry_is_visible_without_deadlocking_start_gate(monkeypatch, tmp_path):
    monkeypatch.setattr(monitor, "SUPERVISOR_STOP_FILE", tmp_path / "supervisor.stop")
    health = {
        "checkedAt": datetime.now(timezone.utc).isoformat(),
        "running": True,
        "readiness": {"runtimeReady": True},
        "runtimeOwnership": {"battleRunnerProcessCount": 1, "duplicateBattleRunners": False},
    }

    payload = monitor.classify_mission(
        health=health,
        supervisor={"state": "battle-cycle-in-flight"},
        lease=active_lease(),
        battles=clean_rated_battles(),
        max_health_age_seconds=300,
        loss_streak_threshold=5,
        low_win_rate_threshold=0.45,
        rating_drawdown_threshold=75.0,
        rating_drawdown_window=60,
        elo_proof=sustain_proof(),
        max_elo_proof_age_seconds=3600,
    )

    issue_ids = {item["id"] for item in payload["issues"]}
    assert monitor.ACCOUNT_TELEMETRY_MISSING_ISSUE_ID in issue_ids
    assert payload["accountAuthority"]["observable"] is False
    assert payload["sessionGovernance"]["allowLaddering"] is True
    assert payload["startGate"]["ready"] is True
    assert monitor.ACCOUNT_TELEMETRY_MISSING_ISSUE_ID in payload["startGate"]["allowedOpenIssueIds"]


def test_supervisor_stop_file_is_first_class_start_blocker(monkeypatch, tmp_path):
    stop_file = tmp_path / "supervisor.stop"
    stop_file.write_text(datetime.now(timezone.utc).isoformat() + "\n", encoding="utf-8")
    monkeypatch.setattr(monitor, "SUPERVISOR_STOP_FILE", stop_file)
    health = {
        "checkedAt": datetime.now(timezone.utc).isoformat(),
        "running": True,
        "readiness": {"runtimeReady": True},
        "runtimeOwnership": {
            "battleRunnerProcessCount": 1,
            "duplicateBattleRunners": False,
            "showdownAccount": "LEBOTJAMESXD00N",
        },
    }

    payload = monitor.classify_mission(
        health=health,
        supervisor={"state": "battle-cycle-in-flight"},
        lease=active_lease(),
        battles=clean_rated_battles(),
        max_health_age_seconds=300,
        loss_streak_threshold=5,
        low_win_rate_threshold=0.45,
        rating_drawdown_threshold=75.0,
        rating_drawdown_window=60,
        elo_proof=sustain_proof(),
        max_elo_proof_age_seconds=3600,
        requested_run_count=5,
        requested_max_cycles=1,
    )

    issue_ids = {item["id"] for item in payload["issues"]}
    assert monitor.SUPERVISOR_STOP_FILE_ISSUE_ID in issue_ids
    assert payload["stopFilePresent"] is True
    assert payload["sessionGovernance"]["allowLaddering"] is True
    assert payload["startGate"]["ready"] is False
    assert payload["startGate"]["blockingIssueIds"] == [monitor.SUPERVISOR_STOP_FILE_ISSUE_ID]


def test_elo_sustain_proof_drawdown_blocks_next_ladder_start(monkeypatch, tmp_path):
    monkeypatch.setattr(monitor, "SUPERVISOR_STOP_FILE", tmp_path / "supervisor.stop")
    monkeypatch.setattr(monitor, "offline_eval_resume_proof_status", missing_offline_eval_resume_proof)
    health = {
        "checkedAt": datetime.now(timezone.utc).isoformat(),
        "running": True,
        "readiness": {"runtimeReady": True},
        "runtimeOwnership": {"battleRunnerProcessCount": 1, "duplicateBattleRunners": False},
    }
    proof = sustain_proof()
    proof["games"][0]["ratingAfter"] = 1800
    for game in proof["games"][1:]:
        game["ratingAfter"] = 1710
    proof["summary"]["peakRating"] = 1800
    proof["summary"]["finalRating"] = 1710

    payload = monitor.classify_mission(
        health=health,
        supervisor={"state": "battle-cycle-in-flight"},
        lease=active_lease(),
        battles=[
            {"battle_id": "safe-a", "rating": 1300, "result": "loss"},
            {"battle_id": "safe-b", "rating": 1325, "result": "win"},
            {"battle_id": "safe-c", "rating": 1320, "result": "win"},
        ],
        max_health_age_seconds=300,
        loss_streak_threshold=5,
        low_win_rate_threshold=0.45,
        rating_drawdown_threshold=75.0,
        rating_drawdown_window=60,
        elo_proof=proof,
        max_elo_proof_age_seconds=3600,
    )

    issue_ids = {item["id"] for item in payload["issues"]}
    drawdown_issue = next(item for item in payload["issues"] if item["id"] == "fouler-rating-drawdown")
    assert drawdown_issue["evidence"]["source"].replace("\\", "/") == "devstream/truth/latest-elo-proof.json"
    assert drawdown_issue["evidence"]["maxSustainDrawdown"] == 90
    assert payload["eloSustainProof"]["ratings"]["maxSustainDrawdown"] == 90
    assert payload["sessionGovernance"]["allowLaddering"] is False
    assert payload["startGate"]["ready"] is False
    assert "fouler-rating-drawdown" in payload["startGate"]["blockingIssueIds"]
    assert monitor.OFFLINE_EVAL_RESUME_ISSUE_ID in payload["startGate"]["blockingIssueIds"]
    assert "fouler-session-stop-loss-breached" in payload["startGate"]["blockingIssueIds"]
    assert "fouler-elo-sustain-proof-missing-or-failing" in issue_ids


def test_elo_sustain_proof_pre_target_skid_blocks_next_ladder_start(monkeypatch, tmp_path):
    monkeypatch.setattr(monitor, "SUPERVISOR_STOP_FILE", tmp_path / "supervisor.stop")
    monkeypatch.setattr(monitor, "offline_eval_resume_proof_status", missing_offline_eval_resume_proof)
    health = {
        "checkedAt": datetime.now(timezone.utc).isoformat(),
        "running": True,
        "readiness": {"runtimeReady": True},
        "runtimeOwnership": {"battleRunnerProcessCount": 1, "duplicateBattleRunners": False},
    }
    proof = sustain_proof()
    proof["games"] = [
        proof_game(100, rating=1600, team="fat-team-1-stall", result="win"),
        proof_game(101, rating=1688, team="fat-team-2-balance", result="win"),
        proof_game(102, rating=1570, team="fat-team-3-dondozo", result="loss"),
    ]
    proof["summary"]["completedGames"] = len(proof["games"])
    proof["summary"]["peakRating"] = max(game["ratingAfter"] for game in proof["games"])
    proof["summary"]["finalRating"] = proof["games"][-1]["ratingAfter"]

    payload = monitor.classify_mission(
        health=health,
        supervisor={"state": "battle-cycle-in-flight"},
        lease=active_lease(),
        battles=[
            {"battle_id": "safe-a", "rating": 1300, "result": "loss"},
            {"battle_id": "safe-b", "rating": 1325, "result": "win"},
            {"battle_id": "safe-c", "rating": 1320, "result": "win"},
        ],
        max_health_age_seconds=300,
        loss_streak_threshold=5,
        low_win_rate_threshold=0.45,
        rating_drawdown_threshold=75.0,
        rating_drawdown_window=60,
        elo_proof=proof,
        max_elo_proof_age_seconds=3600,
    )

    issue_ids = {item["id"] for item in payload["issues"]}
    drawdown_issue = next(item for item in payload["issues"] if item["id"] == "fouler-rating-drawdown")
    assert drawdown_issue["evidence"]["maxPreTargetDrawdown"] == 118.0
    assert drawdown_issue["evidence"]["activePreTargetDrawdown"] == 118.0
    assert drawdown_issue["evidence"]["preTargetBreach"] is True
    assert drawdown_issue["evidence"]["sustainBreach"] is False
    assert payload["eloSustainProof"]["counts"]["preTargetRatedGames"] == 3
    assert payload["eloSustainProof"]["ratings"]["maxPreTargetDrawdown"] == 118.0
    assert any("pre-target drawdown" in blocker for blocker in payload["eloSustainProof"]["blockers"])
    assert payload["sessionGovernance"]["allowLaddering"] is False
    assert payload["startGate"]["ready"] is False
    assert "fouler-rating-drawdown" in payload["startGate"]["blockingIssueIds"]
    assert monitor.OFFLINE_EVAL_RESUME_ISSUE_ID in payload["startGate"]["blockingIssueIds"]
    assert "fouler-session-stop-loss-breached" in payload["startGate"]["blockingIssueIds"]
    assert "fouler-elo-sustain-proof-missing-or-failing" in issue_ids


def test_historical_pre_target_skid_does_not_permanently_stop_recovered_lane(monkeypatch, tmp_path):
    monkeypatch.setattr(monitor, "SUPERVISOR_STOP_FILE", tmp_path / "supervisor.stop")
    health = {
        "checkedAt": datetime.now(timezone.utc).isoformat(),
        "running": False,
        "readiness": {"runtimeReady": False},
        "runtimeOwnership": {"battleRunnerProcessCount": 0, "duplicateBattleRunners": False},
    }
    proof = sustain_proof()
    proof["games"] = [
        proof_game(200, rating=1000, team="fat-team-1-stall", result="win"),
        proof_game(201, rating=1397, team="fat-team-2-balance", result="win"),
        proof_game(202, rating=1311, team="fat-team-3-dondozo", result="loss"),
        proof_game(203, rating=1333, team="fat-team-1-stall", result="win"),
    ]
    proof["summary"]["completedGames"] = len(proof["games"])
    proof["summary"]["peakRating"] = 1397
    proof["summary"]["finalRating"] = 1333

    payload = monitor.classify_mission(
        health=health,
        supervisor={"state": "completed-max-cycles"},
        lease=active_lease(),
        battles=clean_rated_battles(17) + [
            {"battle_id": "recovery-a", "rating": 1311, "result": "loss"},
            {"battle_id": "recovery-b", "rating": 1334, "result": "win"},
            {"battle_id": "recovery-c", "rating": 1333, "result": "win"},
        ],
        max_health_age_seconds=300,
        loss_streak_threshold=5,
        low_win_rate_threshold=0.45,
        rating_drawdown_threshold=75.0,
        rating_drawdown_window=60,
        elo_proof=proof,
        max_elo_proof_age_seconds=3600,
        requested_run_count=5,
        requested_max_cycles=1,
    )

    issue_ids = {item["id"] for item in payload["issues"]}
    assert payload["eloSustainProof"]["ratings"]["maxPreTargetDrawdown"] == 86.0
    assert "fouler-rating-drawdown" not in issue_ids
    assert payload["ratingDrawdown"]["currentDrawdown"] == 1.0
    assert payload["sessionGovernance"]["allowLaddering"] is True
    assert payload["startGate"]["ready"] is True


def test_elo_sustain_proof_post_target_floor_breach_blocks_next_ladder_start(monkeypatch, tmp_path):
    monkeypatch.setattr(monitor, "SUPERVISOR_STOP_FILE", tmp_path / "supervisor.stop")
    monkeypatch.setattr(monitor, "offline_eval_resume_proof_status", missing_offline_eval_resume_proof)
    health = {
        "checkedAt": datetime.now(timezone.utc).isoformat(),
        "running": True,
        "readiness": {"runtimeReady": True},
        "runtimeOwnership": {"battleRunnerProcessCount": 1, "duplicateBattleRunners": False},
    }
    proof = sustain_proof()
    proof["games"][3]["ratingAfter"] = 1698
    proof["summary"]["sustainedTarget"] = False
    proof["summary"]["sustainEvidenceShapeComplete"] = False
    proof["summary"]["sustainProofComplete"] = False
    proof["summary"]["gamesAtOrAboveFloor"] = 29
    proof["summary"]["belowFloorAfterFirstTarget"] = 1
    proof["summary"]["maxSustainDrawdown"] = 14.0
    proof["summary"]["sustainReplayProofCount"] = 29
    proof["summary"]["decisionTraceProofCount"] = 29
    proof["summary"]["teamCoverage"]["fat-team-1-stall"] = 9

    payload = monitor.classify_mission(
        health=health,
        supervisor={"state": "battle-cycle-in-flight"},
        lease=active_lease(),
        battles=clean_rated_battles(),
        max_health_age_seconds=300,
        loss_streak_threshold=5,
        low_win_rate_threshold=0.45,
        rating_drawdown_threshold=75.0,
        rating_drawdown_window=60,
        elo_proof=proof,
        max_elo_proof_age_seconds=3600,
    )

    issue_ids = {item["id"] for item in payload["issues"]}
    breach_issue = next(item for item in payload["issues"] if item["id"] == "fouler-elo-target-floor-breach")
    assert breach_issue["evidence"]["belowFloorAfterFirstTarget"] == 1
    assert payload["eloSustainProof"]["counts"]["belowFloorAfterFirstTarget"] == 1
    assert any("dips below 1700" in blocker for blocker in payload["eloSustainProof"]["blockers"])
    assert payload["sessionGovernance"]["allowLaddering"] is False
    assert payload["startGate"]["ready"] is False
    assert "fouler-elo-target-floor-breach" in payload["startGate"]["blockingIssueIds"]
    assert monitor.OFFLINE_EVAL_RESUME_ISSUE_ID in payload["startGate"]["blockingIssueIds"]
    assert "fouler-session-stop-loss-breached" in payload["startGate"]["blockingIssueIds"]
    assert "fouler-elo-sustain-proof-missing-or-failing" in issue_ids


def test_duplicate_ladder_runners_block_auto_repair(monkeypatch, tmp_path):
    monkeypatch.setattr(monitor, "SUPERVISOR_STOP_FILE", tmp_path / "supervisor.stop")
    health = {
        "checkedAt": datetime.now(timezone.utc).isoformat(),
        "running": True,
        "readiness": {"runtimeReady": True},
        "runtimeOwnership": {"battleRunnerProcessCount": 2, "duplicateBattleRunners": True},
    }

    payload = monitor.classify_mission(
        health=health,
        supervisor={"state": "running-cycle"},
        lease=active_lease(),
        battles=[],
        max_health_age_seconds=300,
        loss_streak_threshold=5,
        low_win_rate_threshold=0.45,
        rating_drawdown_threshold=75.0,
        rating_drawdown_window=60,
    )

    assert any(item["id"] == "fouler-duplicate-ladder-runners" for item in payload["issues"])
    assert payload["duplicateRunners"] is True
    assert payload["runtimeIdle"] is False


def test_stale_running_flag_without_runner_is_idle(monkeypatch, tmp_path):
    monkeypatch.setattr(monitor, "SUPERVISOR_STOP_FILE", tmp_path / "supervisor.stop")
    health = {
        "checkedAt": datetime.now(timezone.utc).isoformat(),
        "running": True,
        "healthy": False,
        "status": "blocked",
        "readiness": {"runtimeReady": False},
        "runtimeOwnership": {"battleRunnerProcessCount": 0, "duplicateBattleRunners": False},
        "activeBattleCount": 0,
    }

    payload = monitor.classify_mission(
        health=health,
        supervisor={"state": "completed-max-cycles"},
        lease=active_lease(),
        battles=[],
        max_health_age_seconds=300,
        loss_streak_threshold=5,
        low_win_rate_threshold=0.45,
        rating_drawdown_threshold=75.0,
        rating_drawdown_window=60,
    )

    issue_ids = {item["id"] for item in payload["issues"]}
    assert "fouler-runtime-idle" in issue_ids
    assert payload["runtimeIdle"] is True
    assert payload["runtimeReady"] is False


def test_reporting_placeholders_become_mission_issue(monkeypatch, tmp_path):
    monkeypatch.setattr(monitor, "SUPERVISOR_STOP_FILE", tmp_path / "supervisor.stop")
    health = {
        "checkedAt": datetime.now(timezone.utc).isoformat(),
        "running": True,
        "readiness": {"runtimeReady": True},
        "runtimeOwnership": {"battleRunnerProcessCount": 1, "duplicateBattleRunners": False},
        "discordQueue": {
            "status": "ready",
            "pendingPlaceholderFieldCounts": {"falseTurns": 1},
            "backlogClassification": {"blocking": False},
        },
    }

    payload = monitor.classify_mission(
        health=health,
        supervisor={"state": "battle-cycle-in-flight"},
        lease=active_lease(),
        battles=[],
        max_health_age_seconds=300,
        loss_streak_threshold=5,
        low_win_rate_threshold=0.45,
        rating_drawdown_threshold=75.0,
        rating_drawdown_window=60,
    )

    assert any(item["id"] == "fouler-discord-reporting-unhealthy" for item in payload["issues"])


def test_redacted_local_discord_backlog_does_not_block_mission(monkeypatch, tmp_path):
    monkeypatch.setattr(monitor, "SUPERVISOR_STOP_FILE", tmp_path / "supervisor.stop")
    health = {
        "checkedAt": datetime.now(timezone.utc).isoformat(),
        "running": True,
        "readiness": {"runtimeReady": True},
        "runtimeOwnership": {"battleRunnerProcessCount": 1, "duplicateBattleRunners": False},
        "discordQueue": {
            "status": "backlogged",
            "deliveryFailures": 0,
            "pendingPlaceholderFieldCounts": {},
            "backlogClassification": {"blocking": True},
            "proofReadiness": {
                "readyForLocalProofHandoff": True,
                "localProofClassified": True,
                "missingStructuredFieldCounts": {},
            },
        },
    }

    payload = monitor.classify_mission(
        health=health,
        supervisor={"state": "battle-cycle-in-flight"},
        lease=active_lease(),
        battles=clean_rated_battles(),
        max_health_age_seconds=300,
        loss_streak_threshold=5,
        low_win_rate_threshold=0.45,
        rating_drawdown_threshold=75.0,
        rating_drawdown_window=60,
    )

    assert all(item["id"] != "fouler-discord-reporting-unhealthy" for item in payload["issues"])


def test_timeout_and_disconnect_results_count_as_losses(monkeypatch, tmp_path):
    monkeypatch.setattr(monitor, "SUPERVISOR_STOP_FILE", tmp_path / "supervisor.stop")
    battles = [
        {"result": "win"},
        {"result": "timeout"},
        {"result": "disconnect after reconnect"},
        {"result": "timed out"},
        {"result": "won"},
    ]

    summary = monitor.recent_result_summary(battles, window=5)

    assert summary["wins"] == 2
    assert summary["losses"] == 3
    assert summary["record"] == "last 5: 2-3"


def test_rating_drawdown_summary_uses_recent_rated_window():
    battles = [
        {"battle_id": "old-peak", "rating": 1600, "result": "win"},
        {"battle_id": "outside-window", "rating": 1200, "result": "loss"},
        {"battle_id": "recent-a", "rating": 1300, "result": "win"},
        {"battle_id": "recent-b", "rating": None, "result": "loss"},
        {"battle_id": "recent-c", "rating": 1375, "result": "win"},
        {"battle_id": "recent-d", "rating": 1290, "result": "loss"},
        {"battle_id": "recent-e", "rating": 1310, "result": "win"},
    ]

    summary = monitor.rating_drawdown_summary(battles, window=4)

    assert summary["ratedBattles"] == 4
    assert summary["peakRating"] == 1375
    assert summary["troughRating"] == 1290
    assert summary["maxDrawdown"] == 85
    assert summary["currentDrawdown"] == 65
    assert summary["peakBattleId"] == "recent-c"
    assert summary["troughBattleId"] == "recent-d"


def test_recent_rating_truth_missing_opens_start_gate_blocker(monkeypatch, tmp_path):
    monkeypatch.setattr(monitor, "SUPERVISOR_STOP_FILE", tmp_path / "supervisor.stop")
    monkeypatch.setattr(monitor, "offline_eval_resume_proof_status", missing_offline_eval_resume_proof)
    health = {
        "checkedAt": datetime.now(timezone.utc).isoformat(),
        "running": True,
        "readiness": {"runtimeReady": True},
        "runtimeOwnership": {"battleRunnerProcessCount": 1, "duplicateBattleRunners": False},
    }
    battles = [
        {"battle_id": "a", "rating": 1300, "result": "win"},
        {"battle_id": "b", "rating": None, "result": "loss"},
        {"battle_id": "c", "result": "win"},
        {"battle_id": "d", "rating": 1315, "result": "loss"},
        {"battle_id": "e", "rating": 1320, "result": "win"},
    ]

    payload = monitor.classify_mission(
        health=health,
        supervisor={"state": "battle-cycle-in-flight"},
        lease=active_lease(),
        battles=battles,
        max_health_age_seconds=300,
        loss_streak_threshold=5,
        low_win_rate_threshold=0.45,
        rating_drawdown_threshold=75.0,
        rating_drawdown_window=60,
    )

    issue = next(item for item in payload["issues"] if item["id"] == "fouler-rating-truth-insufficient")
    assert issue["severity"] == "RELIABILITY_BLOCKER"
    assert issue["evidence"]["decisiveBattles"] == 5
    assert issue["evidence"]["ratedDecisiveBattles"] == 3
    assert issue["evidence"]["missingRatingBattles"] == 2
    assert issue["evidence"]["missingBattleIds"] == ["b", "c"]
    assert payload["ratingTruth"]["ratingCoverage"] == 0.6
    assert "fouler-rating-truth-insufficient" in payload["startGate"]["blockingIssueIds"]


def test_current_account_season_builds_twenty_rated_battles_without_stop_loss(monkeypatch, tmp_path):
    monkeypatch.setattr(monitor, "SUPERVISOR_STOP_FILE", tmp_path / "supervisor.stop")
    monkeypatch.setattr(monitor, "offline_eval_resume_proof_status", missing_offline_eval_resume_proof)
    health = {
        "checkedAt": datetime.now(timezone.utc).isoformat(),
        "running": True,
        "readiness": {"runtimeReady": True},
        "runtimeOwnership": {"battleRunnerProcessCount": 1, "duplicateBattleRunners": False},
    }

    payload = monitor.classify_mission(
        health=health,
        supervisor={"state": "battle-cycle-in-flight"},
        lease=active_lease(),
        battles=clean_rated_battles(19),
        max_health_age_seconds=300,
        loss_streak_threshold=5,
        low_win_rate_threshold=0.45,
        rating_drawdown_threshold=75.0,
        rating_drawdown_window=60,
        account_season=active_account_season(),
    )

    issue = next(item for item in payload["issues"] if item["id"] == monitor.RATING_TRUTH_BUILDING_ISSUE_ID)
    assert issue["evidence"]["ratedDecisiveBattles"] == 19
    assert issue["evidence"]["minimumRatedDecisiveBattles"] == 20
    assert issue["evidence"]["ratingCoverage"] == 1.0
    assert issue["evidence"]["ratingTruthReady"] is False
    assert issue["evidence"]["remainingRatedDecisiveBattles"] == 1
    assert payload["sessionGovernance"]["allowLaddering"] is True
    assert monitor.RATING_TRUTH_BUILDING_ISSUE_ID in payload["startGate"]["allowedOpenIssueIds"]
    assert payload["startGate"]["ready"] is True


def test_fresh_account_season_zero_battles_does_not_write_stop_loss(monkeypatch, tmp_path):
    stop_file = tmp_path / "supervisor.stop"
    monkeypatch.setattr(monitor, "SUPERVISOR_STOP_FILE", stop_file)
    monkeypatch.setattr(monitor, "offline_eval_resume_proof_status", missing_offline_eval_resume_proof)

    payload = monitor.classify_mission(
        health={
            "checkedAt": datetime.now(timezone.utc).isoformat(),
            "running": False,
            "readiness": {"runtimeReady": False},
            "runtimeOwnership": {"battleRunnerProcessCount": 0, "duplicateBattleRunners": False},
        },
        supervisor={},
        lease=active_lease(),
        battles=[],
        max_health_age_seconds=300,
        loss_streak_threshold=5,
        low_win_rate_threshold=0.45,
        rating_drawdown_threshold=75.0,
        rating_drawdown_window=60,
        account_season=active_account_season(),
    )

    action = monitor.enforce_stop_loss_tripwire(payload, write=True)
    issue_ids = {item["id"] for item in payload["issues"]}
    assert monitor.RATING_TRUTH_BUILDING_ISSUE_ID in issue_ids
    assert "fouler-rating-truth-insufficient" not in issue_ids
    assert payload["sessionGovernance"]["stopLossBreached"] is False
    assert action is None
    assert not stop_file.exists()


def test_no_refresh_flags_allow_read_only_classification():
    args = monitor.parse_args(["--no-refresh-health", "--no-refresh-health-after-repair"])

    assert args.refresh_health is False
    assert args.refresh_health_after_repair is False


def test_monitor_defaults_to_one_small_ladder_proof_window():
    args = monitor.parse_args([])

    assert args.run_count == monitor.DEFAULT_MONITOR_RUN_COUNT == 5
    assert args.max_cycles == monitor.DEFAULT_MONITOR_MAX_CYCLES == 1


def test_start_gate_only_flag_is_explicit():
    args = monitor.parse_args(["--start-gate-only"])

    assert args.start_gate_only is True


def test_auto_improve_is_explicit_opt_in_for_repair_starts():
    default_args = monitor.parse_args([])
    opted_in_args = monitor.parse_args(["--auto-improve"])

    assert default_args.auto_improve is False
    assert opted_in_args.auto_improve is True


def test_refresh_health_is_read_only_without_write(monkeypatch):
    captured = {}

    def fake_run_command(command, *, timeout=60):
        captured["command"] = command
        captured["timeout"] = timeout
        return {"ok": True}

    monkeypatch.setattr(monitor, "run_command", fake_run_command)

    result = monitor.refresh_health(skip_http=True, write=False)

    assert result["ok"] is True
    assert captured["timeout"] == 90
    assert "scripts/devstream_health.py" in captured["command"]
    assert "--skip-http" in captured["command"]
    assert "--write" not in captured["command"]


def test_refresh_health_writes_only_when_monitor_write_requested(monkeypatch):
    captured = {}

    def fake_run_command(command, *, timeout=60):
        captured["command"] = command
        captured["timeout"] = timeout
        return {"ok": True}

    monkeypatch.setattr(monitor, "run_command", fake_run_command)

    result = monitor.refresh_health(write=True)

    assert result["ok"] is True
    assert captured["timeout"] == 90
    assert "--write" in captured["command"]


def test_recent_rating_drawdown_opens_mission_issue(monkeypatch, tmp_path):
    monkeypatch.setattr(monitor, "SUPERVISOR_STOP_FILE", tmp_path / "supervisor.stop")
    monkeypatch.setattr(monitor, "offline_eval_resume_proof_status", missing_offline_eval_resume_proof)
    monkeypatch.setattr(monitor, "active_improvement_proof_status", missing_active_improvement_proof)
    health = {
        "checkedAt": datetime.now(timezone.utc).isoformat(),
        "running": True,
        "readiness": {"runtimeReady": True},
        "runtimeOwnership": {"battleRunnerProcessCount": 1, "duplicateBattleRunners": False},
    }
    battles = clean_rated_battles(17) + [
        {"battle_id": "peak", "rating": 1512, "result": "win"},
        {"battle_id": "loss-a", "rating": 1478, "result": "loss"},
        {"battle_id": "loss-b", "rating": 1431, "result": "loss"},
    ]

    payload = monitor.classify_mission(
        health=health,
        supervisor={"state": "battle-cycle-in-flight"},
        lease=active_lease(),
        battles=battles,
        max_health_age_seconds=300,
        loss_streak_threshold=5,
        low_win_rate_threshold=0.45,
        rating_drawdown_threshold=75.0,
        rating_drawdown_window=60,
    )

    drawdown_issue = next(item for item in payload["issues"] if item["id"] == "fouler-rating-drawdown")
    assert drawdown_issue["severity"] == "RELIABILITY_BLOCKER"
    assert drawdown_issue["evidence"]["maxDrawdown"] == 81
    assert drawdown_issue["evidence"]["threshold"] == 75.0
    assert payload["ratingDrawdown"]["currentRating"] == 1431
    assert payload["sessionGovernance"]["allowLaddering"] is False
    assert payload["sessionGovernance"]["blockingIssueIds"] == ["fouler-rating-drawdown"]
    assert payload["startGate"]["ready"] is False
    assert "fouler-rating-drawdown" in payload["startGate"]["blockingIssueIds"]
    assert monitor.OFFLINE_EVAL_RESUME_ISSUE_ID in payload["startGate"]["blockingIssueIds"]
    assert "fouler-session-stop-loss-breached" in payload["startGate"]["blockingIssueIds"]
    assert any(item["id"] == monitor.OFFLINE_EVAL_RESUME_ISSUE_ID for item in payload["issues"])
    assert any(item["id"] == "fouler-session-stop-loss-breached" for item in payload["issues"])

    queue = payload["repairQueue"]
    packet = queue["packets"][0]
    assert queue["schemaVersion"] == monitor.REPAIR_QUEUE_SCHEMA_VERSION
    assert queue["status"] == "blocked"
    assert queue["packetCount"] == 1
    assert queue["nextPacketId"] == "fouler-stop-loss-recovery"
    assert packet["schemaVersion"] == monitor.REPAIR_PACKET_SCHEMA_VERSION
    assert packet["id"] == "fouler-stop-loss-recovery"
    assert packet["status"] == "blocked"
    assert "fouler-rating-drawdown" in packet["issueIds"]
    assert monitor.OFFLINE_EVAL_RESUME_ISSUE_ID in packet["blockedBy"]
    assert "produce-accepted-offline-eval-resume-proof" in packet["nextActions"]
    assert "produce-fresh-post-packet-active-improvement-proof" in packet["nextActions"]
    assert packet["authority"]["runtimeMutationAllowed"] is False
    assert packet["authority"]["streamKeyRequired"] is False
    assert packet["evidence"]["offlineEvalResumeProof"]["ready"] is False
    assert queue["noRuntimeActions"] is True


def test_stop_loss_tripwire_writes_live_runner_and_supervisor_blocks(monkeypatch, tmp_path):
    drain_file = tmp_path / "drain.request"
    stop_file = tmp_path / "supervisor.stop"
    marker_file = tmp_path / "recovery-proof-window.json"
    monkeypatch.setattr(monitor, "DRAIN_FILE", drain_file)
    monkeypatch.setattr(monitor, "SUPERVISOR_STOP_FILE", stop_file)
    monkeypatch.setattr(monitor, "RECOVERY_PROOF_WINDOW_FILE", marker_file)
    classification = {
        "sessionGovernance": {
            "stopLossBreached": True,
            "blockingIssueIds": ["fouler-rating-drawdown"],
        }
    }

    dry_run = monitor.enforce_stop_loss_tripwire(classification, write=False)

    assert dry_run is not None
    assert dry_run["dryRun"] is True
    assert dry_run["written"] is False
    assert dry_run["triggerIssueIds"] == ["fouler-rating-drawdown"]
    assert not drain_file.exists()
    assert not stop_file.exists()

    written = monitor.enforce_stop_loss_tripwire(classification, write=True)

    assert written is not None
    assert written["dryRun"] is False
    assert written["written"] is True
    assert drain_file.exists()
    assert stop_file.exists()
    assert "fouler-rating-drawdown" in drain_file.read_text(encoding="utf-8")
    assert "fouler-rating-drawdown" in stop_file.read_text(encoding="utf-8")


def test_recovered_active_drawdown_clears_only_monitor_owned_tripwire(monkeypatch, tmp_path):
    drain_file = tmp_path / "drain.request"
    stop_file = tmp_path / "supervisor.stop"
    monkeypatch.setattr(monitor, "DRAIN_FILE", drain_file)
    monkeypatch.setattr(monitor, "SUPERVISOR_STOP_FILE", stop_file)
    marker = "2026-07-13T01:17:50+00:00 stop-loss breached: fouler-rating-drawdown\n"
    drain_file.write_text(marker, encoding="utf-8")
    stop_file.write_text(marker, encoding="utf-8")
    classification = {
        "sessionGovernance": {
            "stopLossBreached": False,
            "blockingIssueIds": [],
        }
    }

    dry_run = monitor.reconcile_recovered_stop_loss_tripwire(classification, write=False)
    assert dry_run is not None
    assert dry_run["action"] == "clear-recovered-stop-loss-tripwire"
    assert dry_run["written"] is False
    assert stop_file.exists()
    assert drain_file.exists()

    written = monitor.reconcile_recovered_stop_loss_tripwire(classification, write=True)
    assert written is not None
    assert written["written"] is True
    assert written["drainCleared"] is True
    assert not stop_file.exists()
    assert not drain_file.exists()


def test_recovered_drawdown_does_not_clear_manual_stop(monkeypatch, tmp_path):
    stop_file = tmp_path / "supervisor.stop"
    monkeypatch.setattr(monitor, "SUPERVISOR_STOP_FILE", stop_file)
    stop_file.write_text("manual operator hold\n", encoding="utf-8")

    action = monitor.reconcile_recovered_stop_loss_tripwire(
        {"sessionGovernance": {"stopLossBreached": False}}, write=True
    )

    assert action is not None
    assert action["action"] == "retain-non-stop-loss-supervisor-stop"
    assert action["written"] is False
    assert stop_file.exists()


def test_stop_loss_tripwire_suppressed_during_approved_recovery_proof_window(monkeypatch, tmp_path):
    drain_file = tmp_path / "drain.request"
    stop_file = tmp_path / "supervisor.stop"
    marker_file = tmp_path / "recovery-proof-window.json"
    monkeypatch.setattr(monitor, "DRAIN_FILE", drain_file)
    monkeypatch.setattr(monitor, "SUPERVISOR_STOP_FILE", stop_file)
    monkeypatch.setattr(monitor, "RECOVERY_PROOF_WINDOW_FILE", marker_file)
    marker_file.write_text(
        json.dumps(
            {
                "schemaVersion": "fouler-play-recovery-proof-window/v1",
                "approved": True,
                "purpose": "stop-loss-recovery-proof-window",
                "launchedAtUtc": datetime.now(timezone.utc).isoformat(),
                "expiresAtUtc": (datetime.now(timezone.utc) + timedelta(minutes=20)).isoformat(),
                "runCount": 5,
                "maxCycles": 1,
                "maxConcurrentBattles": 1,
                "loopBreak": "0",
                "noStreamStart": True,
            }
        ),
        encoding="utf-8",
    )
    classification = {
        "sessionGovernance": {
            "stopLossBreached": True,
            "blockingIssueIds": ["fouler-rating-drawdown"],
        }
    }

    action = monitor.enforce_stop_loss_tripwire(classification, write=True)

    assert action is not None
    assert action["action"] == "stop-loss-tripwire-suppressed-for-recovery-proof-window"
    assert action["written"] is False
    assert action["recoveryProofWindow"]["active"] is True
    assert action["triggerIssueIds"] == ["fouler-rating-drawdown"]
    assert not drain_file.exists()
    assert not stop_file.exists()


def test_recovery_proof_window_accepts_powershell_utf8_bom_marker(monkeypatch, tmp_path):
    marker_file = tmp_path / "recovery-proof-window.json"
    monkeypatch.setattr(monitor, "RECOVERY_PROOF_WINDOW_FILE", marker_file)
    marker = {
        "schemaVersion": "fouler-play-recovery-proof-window/v1",
        "approved": True,
        "purpose": "stop-loss-recovery-proof-window",
        "launchedAtUtc": datetime.now(timezone.utc).isoformat(),
        "expiresAtUtc": (datetime.now(timezone.utc) + timedelta(minutes=20)).isoformat(),
        "runCount": 5,
        "maxCycles": 1,
        "maxConcurrentBattles": 1,
        "loopBreak": "0",
        "noStreamStart": True,
    }
    marker_file.write_bytes(("\ufeff" + json.dumps(marker)).encode("utf-8"))

    status = monitor.recovery_proof_window_status()

    assert status["active"] is True
    assert status["blockers"] == []


def test_stop_loss_tripwire_ignores_expired_recovery_proof_window(monkeypatch, tmp_path):
    drain_file = tmp_path / "drain.request"
    stop_file = tmp_path / "supervisor.stop"
    marker_file = tmp_path / "recovery-proof-window.json"
    monkeypatch.setattr(monitor, "DRAIN_FILE", drain_file)
    monkeypatch.setattr(monitor, "SUPERVISOR_STOP_FILE", stop_file)
    monkeypatch.setattr(monitor, "RECOVERY_PROOF_WINDOW_FILE", marker_file)
    marker_file.write_text(
        json.dumps(
            {
                "schemaVersion": "fouler-play-recovery-proof-window/v1",
                "approved": True,
                "purpose": "stop-loss-recovery-proof-window",
                "launchedAtUtc": (datetime.now(timezone.utc) - timedelta(hours=1)).isoformat(),
                "expiresAtUtc": (datetime.now(timezone.utc) - timedelta(minutes=1)).isoformat(),
                "runCount": 5,
                "maxCycles": 1,
                "maxConcurrentBattles": 1,
                "loopBreak": "0",
                "noStreamStart": True,
            }
        ),
        encoding="utf-8",
    )
    classification = {
        "sessionGovernance": {
            "stopLossBreached": True,
            "blockingIssueIds": ["fouler-rating-drawdown"],
        }
    }

    action = monitor.enforce_stop_loss_tripwire(classification, write=True)

    assert action is not None
    assert action["action"] == "enforce-stop-loss-tripwire"
    assert action["written"] is True
    assert drain_file.exists()
    assert stop_file.exists()


def test_build_payload_enforces_stop_loss_tripwire_before_runtime_repair(monkeypatch, tmp_path):
    drain_file = tmp_path / ".pids" / "drain.request"
    stop_file = tmp_path / ".pids" / "supervisor.stop"
    marker_file = tmp_path / ".pids" / "recovery-proof-window.json"
    monitor_file = tmp_path / "mission-monitor.json"
    monkeypatch.setattr(monitor, "DRAIN_FILE", drain_file)
    monkeypatch.setattr(monitor, "SUPERVISOR_STOP_FILE", stop_file)
    monkeypatch.setattr(monitor, "RECOVERY_PROOF_WINDOW_FILE", marker_file)
    monkeypatch.setattr(monitor, "MISSION_MONITOR_FILE", monitor_file)
    monkeypatch.setattr(monitor, "refresh_health", lambda *, skip_http, write: {"ok": True})
    monkeypatch.setattr(monitor, "supervisor_task_status", lambda: {"ok": True})
    monkeypatch.setattr(monitor, "load_json", lambda path, default=None: default if default is not None else {})
    monkeypatch.setattr(monitor, "read_battles", lambda: [])
    monkeypatch.setattr(monitor, "write_tickets", lambda issues, *, source: [])
    monkeypatch.setattr(monitor, "reconcile_cleared_tickets", lambda active_issue_ids, *, classification, source: [])
    monkeypatch.setattr(monitor, "current_source_commit", lambda: "test-commit")
    monkeypatch.setattr(monitor, "signal_freshness_status", lambda: {})
    monkeypatch.setattr(monitor, "decision_divergence_status", lambda: {"status": "test"})

    def fake_classify_mission(**kwargs):
        stop_present = stop_file.exists()
        issues = [{"id": "fouler-rating-drawdown"}]
        if stop_present:
            issues.append({"id": monitor.SUPERVISOR_STOP_FILE_ISSUE_ID})
        return {
            "issues": issues,
            "runtimeIdle": True,
            "runtimeLeaseActive": True,
            "duplicateRunners": False,
            "stopFilePresent": stop_present,
            "sessionGovernance": {
                "allowLaddering": False,
                "decision": "pause-laddering",
                "stopLossBreached": True,
                "blockingIssueIds": ["fouler-rating-drawdown"],
            },
            "startGate": {
                "ready": False,
                "blockingIssueIds": ["fouler-rating-drawdown"],
            },
            "repairQueue": {"packetCount": 1},
        }

    monkeypatch.setattr(monitor, "classify_mission", fake_classify_mission)
    args = monitor.parse_args(["--write", "--repair-runtime", "--no-refresh-health", "--no-refresh-health-after-repair"])

    payload = monitor.build_payload(args)

    assert drain_file.exists()
    assert stop_file.exists()
    assert payload["classification"]["stopFilePresent"] is True
    assert any(action["action"] == "enforce-stop-loss-tripwire" and action["written"] is True for action in payload["actions"])
    assert any(action["action"] == "repair-skipped" and action["reason"] == "supervisor stop file is present" for action in payload["actions"])


def test_build_payload_clears_recovered_tripwire_then_repairs_runtime(monkeypatch, tmp_path):
    drain_file = tmp_path / ".pids" / "drain.request"
    stop_file = tmp_path / ".pids" / "supervisor.stop"
    monitor_file = tmp_path / "mission-monitor.json"
    drain_file.parent.mkdir(parents=True)
    marker = "2026-07-13T01:17:50+00:00 stop-loss breached: fouler-rating-drawdown\n"
    drain_file.write_text(marker, encoding="utf-8")
    stop_file.write_text(marker, encoding="utf-8")
    monkeypatch.setattr(monitor, "DRAIN_FILE", drain_file)
    monkeypatch.setattr(monitor, "SUPERVISOR_STOP_FILE", stop_file)
    monkeypatch.setattr(monitor, "MISSION_MONITOR_FILE", monitor_file)
    monkeypatch.setattr(monitor, "supervisor_task_status", lambda: {"ok": True})
    monkeypatch.setattr(
        monitor,
        "load_json",
        lambda path, default=None: active_lease() if path == monitor.RUNTIME_LEASE_FILE else (default or {}),
    )
    monkeypatch.setattr(monitor, "read_battles", lambda: [])
    monkeypatch.setattr(monitor, "write_tickets", lambda issues, *, source: [])
    monkeypatch.setattr(monitor, "reconcile_cleared_tickets", lambda active_issue_ids, *, classification, source: [])
    monkeypatch.setattr(monitor, "current_source_commit", lambda: "test-commit")
    monkeypatch.setattr(monitor, "signal_freshness_status", lambda: {})
    monkeypatch.setattr(monitor, "decision_divergence_status", lambda: {"status": "test"})
    started: list[bool] = []
    monkeypatch.setattr(
        monitor,
        "start_supervisor",
        lambda args: started.append(True) or {"ok": True, "returnCode": 0},
    )

    def fake_classify_mission(**kwargs):
        stop_present = stop_file.exists()
        return {
            "issues": ([{"id": monitor.SUPERVISOR_STOP_FILE_ISSUE_ID}] if stop_present else []),
            "runtimeIdle": True,
            "runtimeLeaseActive": True,
            "duplicateRunners": False,
            "stopFilePresent": stop_present,
            "sessionGovernance": {
                "allowLaddering": True,
                "decision": "allow-laddering",
                "stopLossBreached": False,
                "blockingIssueIds": [],
            },
            "startGate": {
                "ready": not stop_present,
                "decision": "block-ladder-start" if stop_present else "allow-next-proof-window",
                "blockingIssueIds": ([monitor.SUPERVISOR_STOP_FILE_ISSUE_ID] if stop_present else []),
            },
            "repairQueue": {"packetCount": 0},
        }

    monkeypatch.setattr(monitor, "classify_mission", fake_classify_mission)
    args = monitor.parse_args(["--write", "--repair-runtime", "--no-refresh-health", "--no-refresh-health-after-repair"])

    payload = monitor.build_payload(args)

    assert not stop_file.exists()
    assert not drain_file.exists()
    assert started == [True]
    assert any(action["action"] == "clear-recovered-stop-loss-tripwire" for action in payload["actions"])
    assert any(action["action"] == "start-battle-supervisor" for action in payload["actions"])


def test_stop_loss_offline_eval_issue_clears_but_requires_active_improvement_proof(monkeypatch, tmp_path):
    monkeypatch.setattr(monitor, "SUPERVISOR_STOP_FILE", tmp_path / "supervisor.stop")
    monkeypatch.setattr(monitor, "offline_eval_resume_proof_status", accepted_offline_eval_resume_proof)
    monkeypatch.setattr(monitor, "active_improvement_proof_status", missing_active_improvement_proof)
    health = {
        "checkedAt": datetime.now(timezone.utc).isoformat(),
        "running": True,
        "readiness": {"runtimeReady": True},
        "runtimeOwnership": {"battleRunnerProcessCount": 1, "duplicateBattleRunners": False},
    }
    battles = clean_rated_battles(17) + [
        {"battle_id": "peak", "rating": 1512, "result": "win"},
        {"battle_id": "loss-a", "rating": 1478, "result": "loss"},
        {"battle_id": "loss-b", "rating": 1431, "result": "loss"},
    ]

    payload = monitor.classify_mission(
        health=health,
        supervisor={"state": "battle-cycle-in-flight"},
        lease=active_lease(),
        battles=battles,
        max_health_age_seconds=300,
        loss_streak_threshold=5,
        low_win_rate_threshold=0.45,
        rating_drawdown_threshold=75.0,
        rating_drawdown_window=60,
    )

    issue_ids = {item["id"] for item in payload["issues"]}
    assert monitor.OFFLINE_EVAL_RESUME_ISSUE_ID not in issue_ids
    assert monitor.OFFLINE_EVAL_RESUME_ISSUE_ID not in payload["startGate"]["blockingIssueIds"]
    assert payload["offlineEvalResumeProof"]["ready"] is True
    assert monitor.ACTIVE_IMPROVEMENT_ISSUE_ID in issue_ids
    assert monitor.ACTIVE_IMPROVEMENT_ISSUE_ID in payload["startGate"]["recoveryValidationSuppressedIssueIds"]
    assert "fouler-rating-drawdown" in payload["startGate"]["recoveryValidationSuppressedIssueIds"]
    assert payload["startGate"]["blockingIssueIds"] == []
    assert payload["startGate"]["ready"] is True
    assert payload["startGate"]["decision"] == "allow-stop-loss-recovery-proof-window"
    assert payload["stopLossRecoveryValidation"]["ready"] is True


def test_stop_loss_recovery_window_closes_after_failed_post_packet_proof(monkeypatch, tmp_path):
    monkeypatch.setattr(monitor, "SUPERVISOR_STOP_FILE", tmp_path / "supervisor.stop")
    monkeypatch.setattr(monitor, "offline_eval_resume_proof_status", accepted_offline_eval_resume_proof)
    monkeypatch.setattr(monitor, "active_improvement_proof_status", failed_active_improvement_proof)
    health = {
        "checkedAt": datetime.now(timezone.utc).isoformat(),
        "running": True,
        "readiness": {"runtimeReady": True},
        "runtimeOwnership": {"battleRunnerProcessCount": 1, "duplicateBattleRunners": False},
    }
    battles = clean_rated_battles(17) + [
        {"battle_id": "peak", "rating": 1512, "result": "win"},
        {"battle_id": "loss-a", "rating": 1478, "result": "loss"},
        {"battle_id": "loss-b", "rating": 1431, "result": "loss"},
    ]

    payload = monitor.classify_mission(
        health=health,
        supervisor={"state": "battle-cycle-in-flight"},
        lease=active_lease(),
        battles=battles,
        max_health_age_seconds=300,
        loss_streak_threshold=5,
        low_win_rate_threshold=0.45,
        rating_drawdown_threshold=75.0,
        rating_drawdown_window=60,
        requested_run_count=5,
        requested_max_cycles=1,
    )

    assert payload["stopLossRecoveryValidation"]["ready"] is False
    assert "completed recovery proof window" in payload["stopLossRecoveryValidation"]["blockers"][0]
    assert monitor.ACTIVE_IMPROVEMENT_ISSUE_ID in payload["startGate"]["blockingIssueIds"]
    assert payload["startGate"]["ready"] is False
    assert payload["startGate"]["decision"] == "block-ladder-start"


def test_stop_loss_active_improvement_issue_clears_with_post_packet_proof(monkeypatch, tmp_path):
    monkeypatch.setattr(monitor, "SUPERVISOR_STOP_FILE", tmp_path / "supervisor.stop")
    monkeypatch.setattr(monitor, "offline_eval_resume_proof_status", accepted_offline_eval_resume_proof)
    monkeypatch.setattr(monitor, "active_improvement_proof_status", accepted_active_improvement_proof)
    health = {
        "checkedAt": datetime.now(timezone.utc).isoformat(),
        "running": True,
        "readiness": {"runtimeReady": True},
        "runtimeOwnership": {"battleRunnerProcessCount": 1, "duplicateBattleRunners": False},
    }
    battles = clean_rated_battles(17) + [
        {"battle_id": "peak", "rating": 1512, "result": "win"},
        {"battle_id": "loss-a", "rating": 1478, "result": "loss"},
        {"battle_id": "loss-b", "rating": 1431, "result": "loss"},
    ]

    payload = monitor.classify_mission(
        health=health,
        supervisor={"state": "battle-cycle-in-flight"},
        lease=active_lease(),
        battles=battles,
        max_health_age_seconds=300,
        loss_streak_threshold=5,
        low_win_rate_threshold=0.45,
        rating_drawdown_threshold=75.0,
        rating_drawdown_window=60,
    )

    issue_ids = {item["id"] for item in payload["issues"]}
    assert monitor.OFFLINE_EVAL_RESUME_ISSUE_ID not in issue_ids
    assert monitor.ACTIVE_IMPROVEMENT_ISSUE_ID not in issue_ids
    assert payload["activeImprovementProof"]["ready"] is True
    assert payload["startGate"]["ready"] is True
    assert payload["startGate"]["blockingIssueIds"] == []
    assert payload["startGate"]["recoveryValidationSuppressedIssueIds"] == [
        "fouler-rating-drawdown",
        "fouler-session-stop-loss-breached",
    ]
    assert payload["startGate"]["decision"] == "allow-stop-loss-recovery-proof-window"


def test_ladder_stage_blocks_oversized_batch_below_1500(monkeypatch, tmp_path):
    monkeypatch.setattr(monitor, "SUPERVISOR_STOP_FILE", tmp_path / "supervisor.stop")
    monkeypatch.setattr(monitor, "offline_eval_resume_proof_status", missing_offline_eval_resume_proof)
    health = {
        "checkedAt": datetime.now(timezone.utc).isoformat(),
        "running": True,
        "readiness": {"runtimeReady": True},
        "runtimeOwnership": {"battleRunnerProcessCount": 1, "duplicateBattleRunners": False},
    }
    battles = clean_rated_battles()

    payload = monitor.classify_mission(
        health=health,
        supervisor={"state": "battle-cycle-in-flight"},
        lease=active_lease(),
        battles=battles,
        max_health_age_seconds=300,
        loss_streak_threshold=5,
        low_win_rate_threshold=0.45,
        rating_drawdown_threshold=75.0,
        rating_drawdown_window=60,
        requested_run_count=30,
    )

    stage = payload["ladderStage"]
    assert stage["stageId"] == "prove-1500"
    assert stage["maxBatchGames"] == 10
    assert stage["requestedRunCount"] == 30
    assert stage["requestedProofWindowGames"] == 30
    assert stage["batchSizeOk"] is False
    assert any(item["id"] == "fouler-ladder-batch-too-large-for-stage" for item in payload["issues"])
    assert payload["sessionGovernance"]["allowLaddering"] is False
    assert payload["sessionGovernance"]["blockingIssueIds"] == ["fouler-ladder-batch-too-large-for-stage"]
    assert payload["startGate"]["ready"] is False
    assert payload["startGate"]["blockingIssueIds"] == [
        "fouler-ladder-batch-too-large-for-stage",
        monitor.OFFLINE_EVAL_RESUME_ISSUE_ID,
        "fouler-session-stop-loss-breached",
    ]


def test_stop_loss_recovery_validation_does_not_allow_oversized_batch(monkeypatch, tmp_path):
    monkeypatch.setattr(monitor, "SUPERVISOR_STOP_FILE", tmp_path / "supervisor.stop")
    monkeypatch.setattr(monitor, "offline_eval_resume_proof_status", accepted_offline_eval_resume_proof)
    monkeypatch.setattr(monitor, "active_improvement_proof_status", missing_active_improvement_proof)
    health = {
        "checkedAt": datetime.now(timezone.utc).isoformat(),
        "running": True,
        "readiness": {"runtimeReady": True},
        "runtimeOwnership": {"battleRunnerProcessCount": 1, "duplicateBattleRunners": False},
    }
    battles = clean_rated_battles(17) + [
        {"battle_id": "peak", "rating": 1512, "result": "win"},
        {"battle_id": "loss-a", "rating": 1478, "result": "loss"},
        {"battle_id": "loss-b", "rating": 1431, "result": "loss"},
    ]

    payload = monitor.classify_mission(
        health=health,
        supervisor={"state": "battle-cycle-in-flight"},
        lease=active_lease(),
        battles=battles,
        max_health_age_seconds=300,
        loss_streak_threshold=5,
        low_win_rate_threshold=0.45,
        rating_drawdown_threshold=75.0,
        rating_drawdown_window=60,
        requested_run_count=30,
    )

    assert payload["stopLossRecoveryValidation"]["ready"] is False
    assert any("requested run count" in item for item in payload["stopLossRecoveryValidation"]["blockers"])
    assert payload["startGate"]["ready"] is False
    assert "fouler-ladder-batch-too-large-for-stage" in payload["startGate"]["blockingIssueIds"]


def test_ladder_stage_blocks_many_small_cycles_below_1500(monkeypatch, tmp_path):
    monkeypatch.setattr(monitor, "SUPERVISOR_STOP_FILE", tmp_path / "supervisor.stop")
    monkeypatch.setattr(monitor, "offline_eval_resume_proof_status", missing_offline_eval_resume_proof)
    health = {
        "checkedAt": datetime.now(timezone.utc).isoformat(),
        "running": True,
        "readiness": {"runtimeReady": True},
        "runtimeOwnership": {"battleRunnerProcessCount": 1, "duplicateBattleRunners": False},
    }
    battles = clean_rated_battles()

    payload = monitor.classify_mission(
        health=health,
        supervisor={"state": "battle-cycle-in-flight"},
        lease=active_lease(),
        battles=battles,
        max_health_age_seconds=300,
        loss_streak_threshold=5,
        low_win_rate_threshold=0.45,
        rating_drawdown_threshold=75.0,
        rating_drawdown_window=60,
        requested_run_count=5,
        requested_max_cycles=12,
    )

    stage = payload["ladderStage"]
    assert stage["stageId"] == "prove-1500"
    assert stage["maxBatchGames"] == 10
    assert stage["requestedRunCount"] == 5
    assert stage["requestedMaxCycles"] == 12
    assert stage["requestedProofWindowGames"] == 60
    assert stage["batchSizeOk"] is False
    assert any(item["id"] == "fouler-ladder-batch-too-large-for-stage" for item in payload["issues"])


def test_ladder_stage_does_not_promote_on_single_1500_spike():
    stage = monitor.ladder_stage_status(
        [
            {"battle_id": "a", "rating": 1478, "result": "win"},
            {"battle_id": "b", "rating": 1492, "result": "win"},
            {"battle_id": "c", "rating": 1504, "result": "win"},
        ],
        requested_run_count=8,
    )

    assert stage["stageId"] == "prove-1500"
    assert stage["stageGateReason"] == "rating crossed 1500 but lacks consecutive 1500-floor proof"
    assert stage["floorProofs"][1500]["ready"] is False
    assert stage["floorProofs"][1500]["consecutiveGamesAtOrAboveFloor"] == 1


def test_ladder_stage_does_not_promote_on_losing_floor_window():
    stage = monitor.ladder_stage_status(
        [
            {"battle_id": "a", "rating": 1502, "result": "loss"},
            {"battle_id": "b", "rating": 1514, "result": "loss"},
            {"battle_id": "c", "rating": 1507, "result": "loss"},
            {"battle_id": "d", "rating": 1510, "result": "loss"},
            {"battle_id": "e", "rating": 1503, "result": "loss"},
        ],
        requested_run_count=8,
    )

    floor = stage["floorProofs"][1500]
    assert stage["stageId"] == "prove-1500"
    assert floor["consecutiveGamesAtOrAboveFloor"] == 5
    assert floor["floorWindowRecord"] == {
        "wins": 0,
        "losses": 5,
        "decisive": 5,
        "winRate": 0.0,
        "recordReady": False,
    }
    assert floor["ready"] is False


def test_ladder_stage_does_not_promote_to_1600_on_three_game_1500_spike():
    stage = monitor.ladder_stage_status(
        [
            {"battle_id": "a", "rating": 1502, "result": "win"},
            {"battle_id": "b", "rating": 1514, "result": "win"},
            {"battle_id": "c", "rating": 1507, "result": "loss"},
        ],
        requested_run_count=8,
    )

    assert stage["stageId"] == "prove-1500"
    assert stage["floorProofs"][1500]["ready"] is False
    assert stage["floorProofs"][1500]["consecutiveGamesAtOrAboveFloor"] == 3


def test_ladder_stage_promotes_to_1600_only_after_consecutive_1500_floor_proof():
    stage = monitor.ladder_stage_status(
        [
            {"battle_id": "a", "rating": 1502, "result": "win"},
            {"battle_id": "b", "rating": 1514, "result": "win"},
            {"battle_id": "c", "rating": 1507, "result": "loss"},
            {"battle_id": "d", "rating": 1520, "result": "win"},
            {"battle_id": "e", "rating": 1511, "result": "loss"},
        ],
        requested_run_count=8,
    )

    assert stage["stageId"] == "prove-1600"
    assert stage["maxBatchGames"] == 8
    assert stage["floorProofs"][1500]["ready"] is True
    assert stage["floorProofs"][1500]["consecutiveGamesAtOrAboveFloor"] == 5
    assert stage["batchSizeOk"] is True


def test_ladder_stage_does_not_promote_to_1700_on_single_1600_spike():
    stage = monitor.ladder_stage_status(
        [
            {"battle_id": "a", "rating": 1502, "result": "win"},
            {"battle_id": "b", "rating": 1514, "result": "win"},
            {"battle_id": "c", "rating": 1507, "result": "loss"},
            {"battle_id": "d", "rating": 1520, "result": "win"},
            {"battle_id": "e", "rating": 1511, "result": "loss"},
            {"battle_id": "f", "rating": 1604, "result": "win"},
        ],
        requested_run_count=8,
    )

    assert stage["stageId"] == "prove-1600"
    assert stage["floorProofs"][1600]["ready"] is False
    assert stage["floorProofs"][1600]["consecutiveGamesAtOrAboveFloor"] == 1


def test_ladder_stage_promotes_to_1700_only_after_consecutive_1600_floor_proof():
    stage = monitor.ladder_stage_status(
        [
            {"battle_id": "a", "rating": 1602, "result": "win"},
            {"battle_id": "b", "rating": 1614, "result": "win"},
            {"battle_id": "c", "rating": 1607, "result": "loss"},
            {"battle_id": "d", "rating": 1620, "result": "win"},
            {"battle_id": "e", "rating": 1611, "result": "loss"},
        ],
        requested_run_count=5,
    )

    assert stage["stageId"] == "prove-1700"
    assert stage["maxBatchGames"] == 5
    assert stage["floorProofs"][1600]["ready"] is True
    assert stage["floorProofs"][1600]["consecutiveGamesAtOrAboveFloor"] == 5
    assert stage["batchSizeOk"] is True


def test_sustain_1700_blocks_single_thirty_game_runtime_chunk():
    stage = monitor.ladder_stage_status(
        [
            {"battle_id": "a", "rating": 1602, "result": "win"},
            {"battle_id": "b", "rating": 1614, "result": "win"},
            {"battle_id": "c", "rating": 1607, "result": "loss"},
            {"battle_id": "d", "rating": 1620, "result": "win"},
            {"battle_id": "e", "rating": 1702, "result": "win"},
        ],
        requested_run_count=30,
        requested_max_cycles=1,
    )

    assert stage["stageId"] == "sustain-1700"
    assert stage["sustainProofTargetGames"] == 30
    assert stage["runtimeChunkedProofRequired"] is True
    assert stage["maxBatchGames"] == monitor.SUSTAIN_RUNTIME_CHUNK_MAX_GAMES == 5
    assert stage["requestedProofWindowGames"] == 30
    assert stage["batchSizeOk"] is False


def test_ladder_stage_reports_regression_below_previously_proven_floor():
    stage = monitor.ladder_stage_status(
        [
            {"battle_id": "floor-a", "rating": 1502, "result": "win"},
            {"battle_id": "floor-b", "rating": 1514, "result": "win"},
            {"battle_id": "floor-c", "rating": 1507, "result": "loss"},
            {"battle_id": "floor-d", "rating": 1520, "result": "win"},
            {"battle_id": "floor-e", "rating": 1511, "result": "loss"},
            {"battle_id": "drop-a", "rating": 1491, "result": "loss"},
        ],
        requested_run_count=5,
    )

    regression = stage["floorRegression"]
    assert stage["stageId"] == "prove-1500"
    assert regression["policy"] == "fouler-ladder-floor-regression-stop-loss/v1"
    assert regression["regressed"] is True
    assert regression["highestRegressedFloor"] == 1500
    assert regression["historicalProofs"][1500]["historicallyReady"] is True
    assert regression["historicalProofs"][1500]["lastProofBattleIds"] == [
        "floor-a",
        "floor-b",
        "floor-c",
        "floor-d",
        "floor-e",
    ]


def test_losing_floor_window_does_not_become_historical_regression_proof():
    stage = monitor.ladder_stage_status(
        [
            {"battle_id": "floor-a", "rating": 1502, "result": "loss"},
            {"battle_id": "floor-b", "rating": 1514, "result": "loss"},
            {"battle_id": "floor-c", "rating": 1507, "result": "loss"},
            {"battle_id": "floor-d", "rating": 1520, "result": "loss"},
            {"battle_id": "floor-e", "rating": 1511, "result": "loss"},
            {"battle_id": "drop-a", "rating": 1491, "result": "loss"},
        ],
        requested_run_count=5,
    )

    proof = stage["floorRegression"]["historicalProofs"][1500]
    assert proof["historicallyReady"] is False
    assert proof["lastProofBattleIds"] == []
    assert stage["floorRegression"]["regressed"] is False


def test_floor_regression_blocks_next_ladder_start(monkeypatch, tmp_path):
    monkeypatch.setattr(monitor, "SUPERVISOR_STOP_FILE", tmp_path / "supervisor.stop")
    monkeypatch.setattr(monitor, "offline_eval_resume_proof_status", missing_offline_eval_resume_proof)
    health = {
        "checkedAt": datetime.now(timezone.utc).isoformat(),
        "running": True,
        "readiness": {"runtimeReady": True},
        "runtimeOwnership": {"battleRunnerProcessCount": 1, "duplicateBattleRunners": False},
    }
    battles = clean_rated_battles(14) + [
        {"battle_id": "floor-a", "rating": 1502, "result": "win"},
        {"battle_id": "floor-b", "rating": 1514, "result": "win"},
        {"battle_id": "floor-c", "rating": 1507, "result": "loss"},
        {"battle_id": "floor-d", "rating": 1520, "result": "win"},
        {"battle_id": "floor-e", "rating": 1511, "result": "loss"},
        {"battle_id": "drop-a", "rating": 1491, "result": "loss"},
    ]

    payload = monitor.classify_mission(
        health=health,
        supervisor={"state": "battle-cycle-in-flight"},
        lease=active_lease(),
        battles=battles,
        max_health_age_seconds=300,
        loss_streak_threshold=5,
        low_win_rate_threshold=0.45,
        rating_drawdown_threshold=75.0,
        rating_drawdown_window=60,
        requested_run_count=5,
        requested_max_cycles=1,
    )

    issue_ids = {item["id"] for item in payload["issues"]}
    floor_issue = next(item for item in payload["issues"] if item["id"] == "fouler-ladder-floor-regression")
    assert floor_issue["evidence"]["highestRegressedFloor"] == 1500
    assert payload["ratingDrawdown"]["maxDrawdown"] == 29.0
    assert payload["sessionGovernance"]["allowLaddering"] is False
    assert payload["sessionGovernance"]["blockingIssueIds"] == ["fouler-ladder-floor-regression"]
    assert payload["startGate"]["ready"] is False
    assert "fouler-ladder-floor-regression" in payload["startGate"]["blockingIssueIds"]
    assert monitor.OFFLINE_EVAL_RESUME_ISSUE_ID in payload["startGate"]["blockingIssueIds"]
    assert "fouler-session-stop-loss-breached" in payload["startGate"]["blockingIssueIds"]
    assert "fouler-rating-drawdown" not in issue_ids


def test_ladder_stage_allows_small_bounded_batch_below_1500(monkeypatch, tmp_path):
    monkeypatch.setattr(monitor, "SUPERVISOR_STOP_FILE", tmp_path / "supervisor.stop")
    health = {
        "checkedAt": datetime.now(timezone.utc).isoformat(),
        "running": True,
        "readiness": {"runtimeReady": True},
        "runtimeOwnership": {"battleRunnerProcessCount": 1, "duplicateBattleRunners": False},
    }
    battles = clean_rated_battles()

    payload = monitor.classify_mission(
        health=health,
        supervisor={"state": "battle-cycle-in-flight"},
        lease=active_lease(),
        battles=battles,
        max_health_age_seconds=300,
        loss_streak_threshold=5,
        low_win_rate_threshold=0.45,
        rating_drawdown_threshold=75.0,
        rating_drawdown_window=60,
        requested_run_count=5,
        requested_max_cycles=1,
    )

    issue_ids = {item["id"] for item in payload["issues"]}
    assert payload["ladderStage"]["batchSizeOk"] is True
    assert payload["ladderStage"]["requestedProofWindowGames"] == 5
    assert payload["sessionGovernance"]["allowLaddering"] is True
    assert payload["startGate"]["ready"] is True
    assert "fouler-ladder-batch-too-large-for-stage" not in issue_ids


def test_session_stop_loss_blocks_runtime_repair_start(monkeypatch, tmp_path):
    monkeypatch.setattr(monitor, "SUPERVISOR_STOP_FILE", tmp_path / "supervisor.stop")
    monkeypatch.setattr(monitor, "offline_eval_resume_proof_status", missing_offline_eval_resume_proof)
    health = {
        "checkedAt": datetime.now(timezone.utc).isoformat(),
        "running": False,
        "readiness": {"runtimeReady": False},
        "runtimeOwnership": {"battleRunnerProcessCount": 0, "duplicateBattleRunners": False},
    }
    battles = clean_rated_battles(17) + [
        {"battle_id": "peak", "rating": 1510, "result": "win"},
        {"battle_id": "loss-a", "rating": 1450, "result": "loss"},
        {"battle_id": "loss-b", "rating": 1410, "result": "loss"},
    ]

    payload = monitor.classify_mission(
        health=health,
        supervisor={"state": "completed-max-cycles"},
        lease=active_lease(),
        battles=battles,
        max_health_age_seconds=300,
        loss_streak_threshold=5,
        low_win_rate_threshold=0.45,
        rating_drawdown_threshold=75.0,
        rating_drawdown_window=60,
    )
    args = monitor.parse_args(["--repair-runtime", "--no-refresh-health", "--no-refresh-health-after-repair"])

    actions = monitor.maybe_repair_runtime(args, payload, active_lease())

    assert payload["runtimeIdle"] is True
    assert payload["sessionGovernance"]["decision"] == "pause-laddering"
    assert actions == [
        {
            "action": "repair-skipped",
            "reason": "runtime start gate blocks ladder start",
            "startGate": payload["startGate"],
        }
    ]


def test_stop_loss_recovery_window_starts_bounded_supervisor(monkeypatch, tmp_path):
    monkeypatch.setattr(monitor, "SUPERVISOR_STOP_FILE", tmp_path / "supervisor.stop")
    monkeypatch.setattr(monitor, "offline_eval_resume_proof_status", accepted_offline_eval_resume_proof)
    monkeypatch.setattr(monitor, "active_improvement_proof_status", missing_active_improvement_proof)
    started: list[dict] = []

    def fake_renew_runtime_lease(args, lease):
        return {"ok": True, "returnCode": 0, "lease": {"status": "active"}}

    def fake_start_supervisor(args):
        started.append({"runCount": args.run_count, "maxCycles": args.max_cycles, "maxConcurrentBattles": args.max_concurrent_battles})
        return {"ok": True, "returnCode": 0}

    monkeypatch.setattr(monitor, "renew_runtime_lease", fake_renew_runtime_lease)
    monkeypatch.setattr(monitor, "start_supervisor", fake_start_supervisor)
    health = {
        "checkedAt": datetime.now(timezone.utc).isoformat(),
        "running": False,
        "readiness": {"runtimeReady": False},
        "runtimeOwnership": {"battleRunnerProcessCount": 0, "duplicateBattleRunners": False},
    }
    battles = clean_rated_battles(17) + [
        {"battle_id": "peak", "rating": 1512, "result": "win"},
        {"battle_id": "loss-a", "rating": 1478, "result": "loss"},
        {"battle_id": "loss-b", "rating": 1431, "result": "loss"},
    ]

    payload = monitor.classify_mission(
        health=health,
        supervisor={"state": "completed-max-cycles"},
        lease=active_lease(),
        battles=battles,
        max_health_age_seconds=300,
        loss_streak_threshold=5,
        low_win_rate_threshold=0.45,
        rating_drawdown_threshold=75.0,
        rating_drawdown_window=60,
        requested_run_count=5,
        requested_max_cycles=1,
    )
    args = monitor.parse_args(
        [
            "--repair-runtime",
            "--renew-lease",
            "--run-count",
            "5",
            "--max-cycles",
            "1",
            "--max-concurrent-battles",
            "1",
            "--no-refresh-health",
            "--no-refresh-health-after-repair",
        ]
    )

    actions = monitor.maybe_repair_runtime(args, payload, active_lease())

    assert payload["runtimeIdle"] is True
    assert payload["sessionGovernance"]["decision"] == "pause-laddering"
    assert payload["startGate"]["ready"] is True
    assert payload["startGate"]["decision"] == "allow-stop-loss-recovery-proof-window"
    assert [item["action"] for item in actions] == ["renew-runtime-lease", "start-battle-supervisor"]
    assert started == [{"runCount": 5, "maxCycles": 1, "maxConcurrentBattles": 1}]


def test_repair_runtime_exit_accepts_authorized_start_with_open_issues(monkeypatch):
    payload = {
        "healthy": False,
        "startGate": {"ready": True, "repairActionsOk": True},
        "classification": {"runtimeIdle": True},
        "actions": [{"action": "start-battle-supervisor", "ok": True, "returnCode": 0}],
    }
    monkeypatch.setattr(monitor, "build_payload", lambda args: payload)

    assert monitor.main(["--repair-runtime"]) == 0


def test_repair_runtime_exit_rejects_ready_gate_without_start_or_active_runtime(monkeypatch):
    payload = {
        "healthy": False,
        "startGate": {"ready": True, "repairActionsOk": True},
        "classification": {"runtimeIdle": True},
        "actions": [],
    }
    monkeypatch.setattr(monitor, "build_payload", lambda args: payload)

    assert monitor.main(["--repair-runtime"]) == 2


def test_session_governance_allows_clean_recent_risk(monkeypatch, tmp_path):
    monkeypatch.setattr(monitor, "SUPERVISOR_STOP_FILE", tmp_path / "supervisor.stop")
    health = {
        "checkedAt": datetime.now(timezone.utc).isoformat(),
        "running": False,
        "readiness": {"runtimeReady": False},
        "runtimeOwnership": {"battleRunnerProcessCount": 0, "duplicateBattleRunners": False},
    }
    battles = clean_rated_battles()

    payload = monitor.classify_mission(
        health=health,
        supervisor={"state": "completed-max-cycles"},
        lease=active_lease(),
        battles=battles,
        max_health_age_seconds=300,
        loss_streak_threshold=5,
        low_win_rate_threshold=0.45,
        rating_drawdown_threshold=75.0,
        rating_drawdown_window=60,
    )

    issue_ids = {item["id"] for item in payload["issues"]}
    assert payload["sessionGovernance"]["allowLaddering"] is True
    assert payload["sessionGovernance"]["blockingIssueIds"] == []
    assert "fouler-session-stop-loss-breached" not in issue_ids


def test_reconcile_clears_inactive_open_ticket(monkeypatch, tmp_path):
    monkeypatch.setattr(monitor, "TICKET_DIR", tmp_path)
    path = tmp_path / "fouler-runtime-idle.json"
    monitor.write_json(
        path,
        {
            "schemaVersion": "hermes-devstream-ticket/v1",
            "projectId": "fouler-play",
            "ticketId": "fouler-runtime-idle",
            "status": "open",
            "openedAt": datetime.now(timezone.utc).isoformat(),
            "updatedAt": datetime.now(timezone.utc).isoformat(),
        },
    )

    cleared = monitor.reconcile_cleared_tickets(
        set(),
        classification={
            "runtimeReady": True,
            "runtimeIdle": False,
            "runtimeLeaseActive": True,
            "duplicateRunners": False,
            "stopFilePresent": False,
            "recentResults": {"record": "last 20: 10-10"},
        },
        source="unit-test",
    )
    ticket = monitor.load_json(path, {})

    assert cleared == [monitor.display_path(path)]
    assert ticket["status"] == "cleared"
    assert ticket["clearedBy"] == "unit-test"
    assert ticket["clearanceEvidence"]["runtimeReady"] is True


def ladder_proc(pid: int, ppid: int, *, name: str = "python.exe", search_ladder: bool = True) -> dict:
    cmdline = ["C:\\fake\\python.exe", "run.py", "--bot-mode"]
    cmdline.append("search_ladder" if search_ladder else "accept_challenge")
    return {"pid": pid, "ppid": ppid, "name": name, "cmdline": cmdline}


def test_ladder_client_top_level_pids_collapses_shim_child_and_workers_to_one():
    processes = [
        ladder_proc(10, 1),  # venv launcher shim (top-level)
        ladder_proc(20, 10),  # system-python child re-exec'd by the shim
        ladder_proc(30, 20),  # MCTS search worker
        ladder_proc(31, 20),  # MCTS search worker
        {"pid": 40, "ppid": 1, "name": "python.exe", "cmdline": ["python.exe", "scripts/devstream_health.py"]},
        {"pid": 50, "ppid": 1, "name": "notepad.exe", "cmdline": ["notepad.exe", "run.py", "search_ladder"]},
    ]

    assert monitor.ladder_client_top_level_pids(processes) == [10]


def test_ladder_client_top_level_pids_empty_without_search_ladder_client():
    processes = [
        ladder_proc(10, 1, search_ladder=False),
        {"pid": 20, "ppid": 1, "name": "python.exe", "cmdline": ["python.exe", "bot_monitor.py"]},
    ]

    assert monitor.ladder_client_top_level_pids(processes) == []


def test_newest_battle_log_ignores_keepalive_noise(tmp_path):
    logs = tmp_path / "logs"
    logs.mkdir()
    now = time.time()
    battle = logs / "battle-gen9ou-123_opponent.log"
    battle.write_text("battle", encoding="utf-8")
    os.utime(battle, (now - 5000, now - 5000))
    session = logs / "init.log"
    session.write_text("session", encoding="utf-8")
    os.utime(session, (now - 30, now - 30))
    keepalive = logs / "fouler_keepalive.log"
    keepalive.write_text("keepalive noise", encoding="utf-8")
    os.utime(keepalive, (now, now))

    result = monitor.newest_battle_log(logs)

    assert result["path"].endswith("init.log")
    assert 0 <= result["ageSeconds"] < 300


def test_newest_battle_log_reports_none_when_no_play_logs(tmp_path):
    logs = tmp_path / "logs"
    logs.mkdir()
    (logs / "fouler_keepalive.log").write_text("keepalive noise", encoding="utf-8")

    assert monitor.newest_battle_log(logs) == {"path": None, "ageSeconds": None}


def signal_logs(tmp_path, *, age_seconds: float) -> object:
    logs = tmp_path / "logs"
    logs.mkdir()
    battle = logs / "battle-gen9ou-999_opponent.log"
    battle.write_text("battle", encoding="utf-8")
    stamp = time.time() - age_seconds
    os.utime(battle, (stamp, stamp))
    return logs


def test_signal_state_live_when_client_alive_and_log_fresh(monkeypatch, tmp_path):
    monkeypatch.setattr(monitor, "ladder_client_status", lambda: {"clientAlive": True, "topLevelPids": [123]})
    logs = signal_logs(tmp_path, age_seconds=60)

    fields = monitor.signal_freshness_status(logs_dir=logs)

    assert fields["client_alive"] is True
    assert 0 <= fields["battle_log_age_s"] < monitor.LIVE_SIGNAL_MAX_BATTLE_LOG_AGE_SECONDS
    assert fields["signal_state"] == monitor.SIGNAL_STATE_LIVE


def test_signal_state_stale_when_client_dead_even_with_fresh_logs(monkeypatch, tmp_path):
    monkeypatch.setattr(monitor, "ladder_client_status", lambda: {"clientAlive": False, "topLevelPids": []})
    logs = signal_logs(tmp_path, age_seconds=60)

    fields = monitor.signal_freshness_status(logs_dir=logs)

    assert fields["client_alive"] is False
    assert fields["signal_state"] == monitor.SIGNAL_STATE_STALE
    assert fields["signal_state"].startswith("STALE")


def test_signal_state_stale_when_logs_older_than_an_hour(monkeypatch, tmp_path):
    monkeypatch.setattr(monitor, "ladder_client_status", lambda: {"clientAlive": True, "topLevelPids": [123]})
    logs = signal_logs(tmp_path, age_seconds=monitor.LIVE_SIGNAL_MAX_BATTLE_LOG_AGE_SECONDS + 600)

    fields = monitor.signal_freshness_status(logs_dir=logs)

    assert fields["client_alive"] is True
    assert fields["battle_log_age_s"] >= monitor.LIVE_SIGNAL_MAX_BATTLE_LOG_AGE_SECONDS
    assert fields["signal_state"] == monitor.SIGNAL_STATE_STALE


def test_signal_state_stale_when_no_play_logs_exist(monkeypatch, tmp_path):
    monkeypatch.setattr(monitor, "ladder_client_status", lambda: {"clientAlive": True, "topLevelPids": [123]})
    logs = tmp_path / "logs"
    logs.mkdir()

    fields = monitor.signal_freshness_status(logs_dir=logs)

    assert fields["battle_log_age_s"] is None
    assert fields["signal_state"] == monitor.SIGNAL_STATE_STALE
