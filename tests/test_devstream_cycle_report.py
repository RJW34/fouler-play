import io
import json
import os
import sys
import time
from datetime import datetime, timezone
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

import devstream_cycle_report
import fouler_mission_monitor
import devstream_runtime_lease
from tests.runtime_authority_testkit import sign_test_runtime_lease


def _active_lease() -> dict:
    return {
        "status": "active",
        "account": "LEBOTJAMESXD00N",
        "expiresAt": "2999-01-01T00:00:00+00:00",
    }


def _elo_battle(index: int, *, team: str, rating: int, result: str = "win") -> dict:
    return {
        "battle_id": f"battle-gen9ou-proof-{index}",
        "result": result,
        "ratingAfter": rating,
        "teamFile": team,
        "decisionTracePath": f"logs/decision_traces/gen9ou-proof-{index}.json",
        "timestamp": f"2026-05-25T12:{index:02d}:00+00:00",
    }


def _elo_cycle(generated_at: str | None = None) -> dict:
    return {
        "generatedAt": generated_at or datetime.now(timezone.utc).isoformat(),
        "autoresearch": {
            "json": {"exists": True, "path": "replay_analysis/autoresearch_latest.json"},
            "report": {"exists": True, "path": "replay_analysis/reports/autoresearch_latest.md"},
        },
    }


def test_elo_proof_resolves_account_from_runtime_lease(tmp_path, monkeypatch):
    truth_dir = tmp_path / "devstream" / "truth"
    truth_dir.mkdir(parents=True)
    runtime_lease = devstream_runtime_lease.build_runtime_lease_artifact(
        purpose="devstream-supervise",
        machine="MIRAIDON",
        account="thepeakmons",
        run_count=1,
        max_cycles=1,
        max_concurrent_battles=1,
        replay_behavior="always",
        valid_minutes=30,
        source_commit="a" * 40,
        source_tree="b" * 40,
        change_id="change-cycle-report-0001",
        deployment_id="deployment-cycle-report-0001",
        runtime_manifest_digest="c" * 64,
        deployment_receipt_path=r"C:\ProgramData\HERMES\state\fouler\deployment-test.json",
        deployment_receipt_sha256="d" * 64,
    )
    (truth_dir / "runtime-lease.json").write_text(
        json.dumps(sign_test_runtime_lease(runtime_lease)),
        encoding="utf-8",
    )
    monkeypatch.setattr(devstream_cycle_report, "ROOT", tmp_path)
    monkeypatch.delenv("FOULER_ACTIVE_ACCOUNT", raising=False)
    monkeypatch.delenv("PS_USERNAME", raising=False)
    monkeypatch.delenv("SHOWDOWN_ACCOUNTS", raising=False)

    proof = devstream_cycle_report.build_elo_proof_payload({"battles": []}, _elo_cycle())

    assert proof["account"]["showdownUserId"] == "thepeakmons"
    assert proof["account"]["authoritySource"] == "devstream/truth/runtime-lease.json"
    assert proof["account"]["authorityReady"] is True


def test_elo_proof_never_defaults_to_a_stale_account(tmp_path, monkeypatch):
    monkeypatch.setattr(devstream_cycle_report, "ROOT", tmp_path)
    monkeypatch.delenv("FOULER_ACTIVE_ACCOUNT", raising=False)
    monkeypatch.delenv("PS_USERNAME", raising=False)
    monkeypatch.delenv("SHOWDOWN_ACCOUNTS", raising=False)

    proof = devstream_cycle_report.build_elo_proof_payload({"battles": []}, _elo_cycle())

    assert proof["account"]["showdownUserId"] == "unknown"
    assert proof["account"]["authoritySource"] == "unresolved"
    assert proof["account"]["authorityReady"] is False


def test_elo_proof_records_live_profile_rating_without_sustain_credit(monkeypatch):
    monkeypatch.setattr(devstream_cycle_report, "current_source_commit", lambda: "abc1234")
    proof = devstream_cycle_report.build_elo_proof_payload(
        {
            "battles": [
                _elo_battle(0, team="fat-team-1-stall", rating=1153, result="win"),
            ]
        },
        _elo_cycle("2026-07-05T16:00:00+00:00"),
        account="thepeakmons",
        live_profile={
            "status": "fetched",
            "checkedAtUtc": "2026-07-05T16:00:01+00:00",
            "showdownUserId": "thepeakmons",
            "format": "gen9ou",
            "rating": 1197.25,
            "source": "https://pokemonshowdown.com/users/thepeakmons.json",
            "noRuntimeActions": True,
        },
    )

    assert proof["summary"]["finalRating"] == 1153
    assert proof["summary"]["currentRating"] == 1197.25
    assert proof["summary"]["currentRatingSource"] == "pokemonshowdown-user-api"
    assert proof["summary"]["liveProfileRating"] == 1197.25
    assert proof["liveProfile"]["rating"] == 1197.25
    assert proof["summary"]["sustainWindowGames"] == 0
    assert proof["summary"]["sustainProofComplete"] is False


def test_elo_proof_scopes_stats_to_active_account_season(tmp_path, monkeypatch):
    season_path = tmp_path / "account-season.json"
    season_path.write_text(
        json.dumps(
            {
                "account": "DekuFoulerLab",
                "seasonId": "dekufoulerlab-gen9ou-20260710",
            }
        ),
        encoding="utf-8",
    )
    monkeypatch.setattr(devstream_cycle_report, "ACCOUNT_SEASON_FILE", season_path)
    retired = _elo_battle(0, team="fat-team-1-stall", rating=1177, result="loss")
    retired.update({"account": "thepeakmons", "season_id": "thepeakmons-gen9ou-20260701"})
    prior_season = _elo_battle(1, team="fat-team-1-stall", rating=1000, result="loss")
    prior_season.update(
        {"account": "DekuFoulerLab", "season_id": "dekufoulerlab-gen9ou-20260709"}
    )
    current = _elo_battle(2, team="fat-team-2-balance", rating=1045, result="win")
    current.update(
        {"account": "DekuFoulerLab", "season_id": "dekufoulerlab-gen9ou-20260710"}
    )

    proof = devstream_cycle_report.build_elo_proof_payload(
        {"battles": [retired, prior_season, current]},
        _elo_cycle(),
        account="DekuFoulerLab",
    )

    assert [game["battleId"] for game in proof["games"]] == [
        "battle-gen9ou-proof-2"
    ]
    assert proof["summary"]["completedGames"] == 1
    assert proof["summary"]["wins"] == 1
    assert proof["summary"]["losses"] == 0
    assert proof["account"]["seasonId"] == "dekufoulerlab-gen9ou-20260710"


def test_showdown_profile_fetch_accepts_rating_json_from_http_error(monkeypatch):
    body = json.dumps(
        {
            "userid": "thepeakmons",
            "ratings": {
                "gen9ou": {
                    "elo": 1197.253638433582,
                    "gxe": 39,
                    "rpr": 1415.3215656670823,
                    "rprd": 25,
                    "w": 1057,
                    "l": 1229,
                }
            },
        }
    ).encode("utf-8")

    def fake_urlopen(request, *, timeout):
        raise devstream_cycle_report.urllib.error.HTTPError(
            request.full_url,
            404,
            "Not Found",
            hdrs=None,
            fp=io.BytesIO(body),
        )

    monkeypatch.setattr(devstream_cycle_report.urllib.request, "urlopen", fake_urlopen)

    profile = devstream_cycle_report.fetch_showdown_profile_rating("thepeakmons", "gen9ou")

    assert profile["status"] == "fetched"
    assert profile["rating"] == 1197.25
    assert profile["httpStatus"] == 404
    assert profile["noRuntimeActions"] is True


def test_cycle_report_completion_payload_marks_fresh_learning_proof():
    cycle = {
        "generatedAt": "2026-05-25T12:35:23+00:00",
        "activeBattles": {"battleCount": 0},
        "blockers": [],
        "warnings": [],
        "streamStatus": {"elo": "1200"},
        "autoresearch": {
            "json": {"exists": True, "path": "replay_analysis/autoresearch_latest.json"},
            "report": {"exists": True, "path": "replay_analysis/reports/autoresearch_latest.md"},
        },
    }
    autoresearch = {
        "batch": {
            "end_battle_id": "battle-gen9ou-1",
            "end_timestamp": "2026-05-25T12:00:00+00:00",
            "size": 30,
        },
        "win_rate": 0.53,
        "regression": {"status": "improving", "rating_delta": 18},
        "evidence_integrity": {
            "loss_count": 3,
            "losses_with_replay_json": 1,
            "losses_with_decision_trace": 3,
            "claims_without_evidence": [],
        },
    }

    completion = devstream_cycle_report.build_completion_payload(cycle, autoresearch)

    assert completion["schemaVersion"] == "fouler-play-devstream-completion/v1"
    assert completion["checkedAtUtc"] == "2026-05-25T12:35:23+00:00"
    assert completion["status"] == "cycle-proof-current"
    assert completion["latestBattleId"] == "battle-gen9ou-1"
    assert completion["latestBattleLearningVerified"] is True
    assert completion["performanceTrendStatus"] == "improving"
    assert completion["performanceImprovementVerified"] is True
    assert completion["activeImprovementVerified"] is True
    assert completion["blockers"] == []


def test_cycle_report_builds_1700_sustain_elo_proof_for_all_fixed_teams(monkeypatch):
    monkeypatch.setattr(devstream_cycle_report, "current_source_commit", lambda: "abc1234")
    battles = []
    index = 0
    for team in devstream_cycle_report.ELO_REQUIRED_TEAMS:
        for offset in range(10):
            battles.append(
                _elo_battle(
                    index,
                    team=team,
                    rating=1710 + ((index + offset) % 8),
                    result="loss" if index % 4 == 0 else "win",
                )
            )
            index += 1
    proof = devstream_cycle_report.build_elo_proof_payload(
        {"battles": battles},
        _elo_cycle(),
        account="LEBOTJAMESXD00N",
        autoresearch={
            "win_rate": 0.6,
            "regression": {"status": "improving", "rating_delta": 18, "win_rate_delta": 0.1},
            "evidence_integrity": {
                "loss_count": 2,
                "losses_with_replay_json": 2,
                "losses_with_decision_trace": 2,
                "claims_without_evidence": [],
            },
        },
    )
    status = fouler_mission_monitor.elo_sustain_proof_status(
        proof,
        lease=_active_lease(),
        max_age_seconds=3600,
    )

    assert proof["schemaVersion"] == "fouler-play-elo-proof/v1"
    assert proof["sourceCommit"] == "abc1234"
    assert proof["source"]["sourceCommit"] == "abc1234"
    assert proof["target"]["ratingFloor"] == 1700
    assert proof["target"]["noCherryPicking"] is True
    assert proof["target"]["uninterruptedPostTargetFloorRequired"] is True
    assert proof["summary"]["sustainedTarget"] is True
    assert proof["summary"]["gamesAtOrAboveFloor"] == 30
    assert proof["summary"]["sustainEvidenceShapeComplete"] is True
    assert proof["summary"]["sustainProofComplete"] is True
    assert proof["summary"]["analysisEvidenceComplete"] is True
    assert proof["summary"]["latestBattleId"] == "battle-gen9ou-proof-29"
    assert proof["summary"]["latestBattleAt"] == "2026-05-25T12:29:00+00:00"
    assert proof["summary"]["latestBattleLearningVerified"] is True
    assert proof["summary"]["performanceImprovementVerified"] is True
    assert proof["summary"]["performanceTrendStatus"] == "improving"
    assert proof["summary"]["improvementSignalStatus"] == "positive"
    assert proof["summary"]["ratingDelta"] == 18
    assert proof["summary"]["winRateDelta"] == 0.1
    assert proof["summary"]["winRate"] == 0.6
    assert proof["summary"]["preTargetDrawdownWithinLimit"] is True
    assert proof["summary"]["maxPreTargetDrawdown"] is None
    assert proof["summary"]["missingSustainReplayCount"] == 0
    assert proof["summary"]["unknownSustainTeamCount"] == 0
    assert proof["summary"]["missingDecisionTraceCount"] == 0
    assert proof["summary"]["duplicateDecisionTraceProofCount"] == 0
    assert proof["summary"]["duplicateDecisionTraceProofs"] == []
    assert proof["analysis"]["autoresearchJsonPath"] == "replay_analysis/autoresearch_latest.json"
    assert proof["analysis"]["autoresearchReportPath"] == "replay_analysis/reports/autoresearch_latest.md"
    assert proof["analysis"]["decisionTraceReviewPath"] == "devstream/truth/proof-status.json"
    assert proof["summary"]["teamCoverage"] == {
        "fat-team-1-stall": 10,
        "fat-team-2-balance": 10,
        "fat-team-3-dondozo": 10,
    }
    assert status["ready"] is True


def test_cycle_report_elo_proof_blocks_large_pre_target_skid_before_sustain():
    battles = [
        _elo_battle(0, team="fat-team-1-stall", rating=1600, result="win"),
        _elo_battle(1, team="fat-team-2-balance", rating=1688, result="win"),
        _elo_battle(2, team="fat-team-3-dondozo", rating=1570, result="loss"),
        _elo_battle(3, team="fat-team-1-stall", rating=1640, result="win"),
        _elo_battle(4, team="fat-team-2-balance", rating=1695, result="win"),
    ]
    index = len(battles)
    for team in devstream_cycle_report.ELO_REQUIRED_TEAMS:
        for _ in range(10):
            battles.append(_elo_battle(index, team=team, rating=1710 + (index % 6)))
            index += 1

    proof = devstream_cycle_report.build_elo_proof_payload(
        {"battles": battles},
        _elo_cycle(),
        account="LEBOTJAMESXD00N",
    )
    status = fouler_mission_monitor.elo_sustain_proof_status(
        proof,
        lease=_active_lease(),
        max_age_seconds=3600,
    )

    assert proof["summary"]["sustainedTarget"] is False
    assert proof["summary"]["sustainEvidenceShapeComplete"] is False
    assert proof["summary"]["sustainProofComplete"] is False
    assert proof["summary"]["preTargetRatedGames"] == 5
    assert proof["summary"]["maxPreTargetDrawdown"] == 118.0
    assert proof["summary"]["preTargetDrawdownPeakBattleId"] == "battle-gen9ou-proof-1"
    assert proof["summary"]["preTargetDrawdownTroughBattleId"] == "battle-gen9ou-proof-2"
    assert proof["summary"]["preTargetDrawdownWithinLimit"] is False
    assert status["ready"] is False
    assert status["ratings"]["maxPreTargetDrawdown"] == 118.0
    assert any("pre-target drawdown" in blocker for blocker in status["blockers"])


def test_cycle_report_elo_proof_blocks_when_rating_never_reaches_target():
    proof = devstream_cycle_report.build_elo_proof_payload(
        {
            "battles": [
                _elo_battle(index, team="fat-team-1-stall", rating=1490 + (index % 5))
                for index in range(30)
            ]
        },
        _elo_cycle("2026-05-25T12:35:23+00:00"),
        account="LEBOTJAMESXD00N",
    )
    status = fouler_mission_monitor.elo_sustain_proof_status(
        proof,
        lease=_active_lease(),
        max_age_seconds=3600,
    )

    assert proof["summary"]["sustainedTarget"] is False
    assert proof["summary"]["sustainEvidenceShapeComplete"] is False
    assert proof["summary"]["sustainProofComplete"] is False
    assert status["ready"] is False
    assert any("never reaches 1700" in blocker for blocker in status["blockers"])


def test_cycle_report_elo_proof_requires_replay_and_fixed_team_provenance():
    battles = []
    index = 0
    for team in devstream_cycle_report.ELO_REQUIRED_TEAMS:
        for offset in range(10):
            battles.append(_elo_battle(index, team=team, rating=1710 + (offset % 8)))
            index += 1
    battles[0].pop("battle_id")
    battles[1]["teamFile"] = "teams/gen9/ou/experimental-team"

    proof = devstream_cycle_report.build_elo_proof_payload(
        {"battles": battles},
        _elo_cycle("2026-05-25T12:35:23+00:00"),
        account="LEBOTJAMESXD00N",
    )

    assert proof["summary"]["sustainedTarget"] is False
    assert proof["summary"]["sustainEvidenceShapeComplete"] is False
    assert proof["summary"]["sustainProofComplete"] is False
    assert proof["summary"]["missingSustainReplayCount"] == 1
    assert proof["summary"]["unknownSustainTeamCount"] == 1
    assert proof["summary"]["missingSustainReplayBattleIds"] == ["unknown"]


def test_cycle_report_elo_proof_requires_decision_trace_and_analysis_evidence():
    battles = []
    index = 0
    for team in devstream_cycle_report.ELO_REQUIRED_TEAMS:
        for offset in range(10):
            battles.append(_elo_battle(index, team=team, rating=1710 + (offset % 8)))
            index += 1
    battles[0].pop("decisionTracePath")

    proof = devstream_cycle_report.build_elo_proof_payload(
        {"battles": battles},
        {
            "generatedAt": datetime.now(timezone.utc).isoformat(),
            "autoresearch": {
                "json": {"exists": False, "path": "replay_analysis/autoresearch_latest.json"},
                "report": {"exists": True, "path": "replay_analysis/reports/autoresearch_latest.md"},
            },
        },
        account="LEBOTJAMESXD00N",
    )
    status = fouler_mission_monitor.elo_sustain_proof_status(
        proof,
        lease=_active_lease(),
        max_age_seconds=3600,
    )

    assert proof["summary"]["sustainedTarget"] is False
    assert proof["summary"]["sustainEvidenceShapeComplete"] is False
    assert proof["summary"]["sustainProofComplete"] is False
    assert proof["summary"]["missingDecisionTraceCount"] == 1
    assert proof["summary"]["missingDecisionTraceBattleIds"] == ["battle-gen9ou-proof-0"]
    assert proof["analysis"]["autoresearchJsonPath"] == ""
    assert status["ready"] is False
    assert status["decisionTraces"]["missingDecisionTraceBattleIds"] == ["battle-gen9ou-proof-0"]
    assert "autoresearchJsonPath" in status["analysis"]["missingPathKeys"]


def test_cycle_report_elo_proof_rejects_duplicate_decision_trace_evidence():
    battles = []
    index = 0
    for team in devstream_cycle_report.ELO_REQUIRED_TEAMS:
        for offset in range(10):
            battles.append(_elo_battle(index, team=team, rating=1710 + (offset % 8)))
            index += 1
    battles[1]["decisionTracePath"] = battles[0]["decisionTracePath"]

    proof = devstream_cycle_report.build_elo_proof_payload(
        {"battles": battles},
        _elo_cycle(),
        account="LEBOTJAMESXD00N",
    )
    status = fouler_mission_monitor.elo_sustain_proof_status(
        proof,
        lease=_active_lease(),
        max_age_seconds=3600,
    )

    assert proof["summary"]["sustainedTarget"] is False
    assert proof["summary"]["sustainEvidenceShapeComplete"] is False
    assert proof["summary"]["sustainProofComplete"] is False
    assert proof["summary"]["duplicateDecisionTraceProofCount"] == 1
    assert proof["summary"]["duplicateDecisionTraceProofs"] == [
        "logs/decision_traces/gen9ou-proof-0.json"
    ]
    assert status["ready"] is False
    assert status["counts"]["duplicateDecisionTraceProofCount"] == 1
    assert any("duplicate sustain-window decision trace proof" in blocker for blocker in status["blockers"])


def test_cycle_report_elo_proof_sorts_source_battles_by_timestamp_before_sustain_derivation():
    low_after_first_target = _elo_battle(1, team="fat-team-1-stall", rating=1690, result="loss")
    low_after_first_target["timestamp"] = "2026-05-25T12:30:00+00:00"
    battles = [
        low_after_first_target,
        _elo_battle(0, team="fat-team-1-stall", rating=1710, result="win"),
    ]
    index = 2
    for team in devstream_cycle_report.ELO_REQUIRED_TEAMS:
        for _ in range(10):
            battles.append(_elo_battle(index, team=team, rating=1710 + (index % 6), result="win"))
            index += 1

    proof = devstream_cycle_report.build_elo_proof_payload(
        {"battles": battles},
        _elo_cycle(),
        account="LEBOTJAMESXD00N",
    )

    assert proof["games"][0]["battleId"] == "battle-gen9ou-proof-0"
    assert proof["summary"]["belowFloorAfterFirstTarget"] == 1
    assert proof["summary"]["chronologicalBattleOrderComplete"] is True
    assert proof["summary"]["sustainedTarget"] is False
    assert proof["summary"]["sustainProofComplete"] is False


def test_cycle_report_completion_payload_blocks_unsupported_autoresearch_claims():
    cycle = {
        "generatedAt": "2026-05-25T12:35:23+00:00",
        "activeBattles": {"battleCount": 0},
        "blockers": [],
        "warnings": [],
        "streamStatus": {},
        "autoresearch": {
            "json": {"exists": True, "path": "replay_analysis/autoresearch_latest.json"},
            "report": {"exists": True, "path": "replay_analysis/reports/autoresearch_latest.md"},
        },
    }
    autoresearch = {
        "batch": {
            "end_battle_id": "battle-gen9ou-missing-proof",
            "end_timestamp": "2026-05-25T12:00:00+00:00",
            "size": 1,
        },
        "evidence_integrity": {
            "loss_count": 1,
            "losses_with_replay_json": 0,
            "losses_with_decision_trace": 0,
            "claims_without_evidence": [
                {
                    "battle_id": "battle-gen9ou-missing-proof",
                    "claim_class": "mechanics_or_strategy",
                    "reason": "no replay JSON or Showdown protocol log lines",
                }
            ],
        },
    }

    completion = devstream_cycle_report.build_completion_payload(cycle, autoresearch)

    assert completion["status"] == "cycle-proof-blocked"
    assert completion["latestBattleLearningVerified"] is False
    assert completion["evidenceIntegrity"]["blocksCompletionProof"] is True
    assert "unsupported mechanics/strategy claim" in " ".join(completion["blockers"])


def test_cycle_report_blocks_when_health_probe_is_not_ready(tmp_path, monkeypatch):
    monkeypatch.setattr(devstream_cycle_report, "ROOT", tmp_path)
    truth_dir = tmp_path / "devstream" / "truth"
    truth_dir.mkdir(parents=True)
    monkeypatch.setattr(devstream_cycle_report, "DISCORD_REPORTING", truth_dir / "discord-reporting.json")
    monkeypatch.setattr(devstream_cycle_report, "DISCORD_DELIVERY", truth_dir / "discord-delivery.json")
    (truth_dir / "discord-delivery.json").write_text('{"status":"idle","secretValuesPrinted":false}', encoding="utf-8")
    monkeypatch.setattr(
        devstream_cycle_report.devstream_health,
        "build_payload",
        lambda check_http=True: {
            "healthy": False,
            "status": "degraded",
            "readyForLiveFocus": False,
            "activeBattleCount": 0,
            "readiness": {"runtimeReady": False, "streamReady": True, "analyticsFresh": True},
            "blockers": ["fouler-play battle runner has no active battle proof after 181s (limit 180s)"],
        },
    )

    payload = devstream_cycle_report.build_payload()

    assert payload["readyForHandoff"] is False
    assert payload["blockers"] == ["fouler-play battle runner has no active battle proof after 181s (limit 180s)"]
    assert payload["health"]["healthy"] is False


def test_cycle_report_blocks_stale_empty_active_battles_without_runner(tmp_path, monkeypatch):
    monkeypatch.setattr(devstream_cycle_report, "ROOT", tmp_path)
    truth_dir = tmp_path / "devstream" / "truth"
    truth_dir.mkdir(parents=True)
    monkeypatch.setattr(devstream_cycle_report, "OUTPUT_JSON", truth_dir / "cycle-report.json")
    monkeypatch.setattr(devstream_cycle_report, "OUTPUT_MD", truth_dir / "cycle-report.md")
    monkeypatch.setattr(devstream_cycle_report, "OUTPUT_COMPLETION", truth_dir / "completion.json")
    monkeypatch.setattr(devstream_cycle_report, "OUTPUT_PROOF_STATUS", truth_dir / "proof-status.json")
    monkeypatch.setattr(devstream_cycle_report, "OUTPUT_ELO_PROOF", truth_dir / "latest-elo-proof.json")
    monkeypatch.setattr(devstream_cycle_report, "DISCORD_REPORTING", truth_dir / "discord-reporting.json")
    monkeypatch.setattr(devstream_cycle_report, "DISCORD_DELIVERY", truth_dir / "discord-delivery.json")
    monkeypatch.setattr(
        devstream_cycle_report.devstream_health,
        "build_payload",
        lambda check_http=True: {
            "healthy": True,
            "status": "ready",
            "running": False,
            "readyForLiveFocus": False,
            "activeBattleCount": 0,
            "readiness": {"runtimeReady": False, "streamReady": True, "analyticsFresh": False},
            "runtimeOwnership": {"battleRunnerCount": 0, "duplicateBattleRunners": False},
            "blockers": [],
        },
    )

    active = tmp_path / "active_battles.json"
    active.write_text('{"battles":[],"count":0}', encoding="utf-8")
    old = time.time() - 3600
    os.utime(active, (old, old))
    (tmp_path / "battle_stats.json").write_text('{"battles":[]}', encoding="utf-8")
    (tmp_path / "stream_status.json").write_text('{"status":"Ready"}', encoding="utf-8")
    (tmp_path / "daily_stats.json").write_text('{"wins":0,"losses":0}', encoding="utf-8")
    (truth_dir / "discord-delivery.json").write_text('{"status":"idle","secretValuesPrinted":false}', encoding="utf-8")
    (truth_dir / "discord-reporting.json").write_text('{"status":"idle","secretValuesPrinted":false}', encoding="utf-8")

    payload = devstream_cycle_report.build_payload()

    assert payload["readyForHandoff"] is False
    assert payload["activeBattles"]["stale"] is True
    assert any("active_battles.json is stale and no battle runner owns the runtime" in blocker for blocker in payload["blockers"])


def test_cycle_report_allows_completed_cycle_with_classified_local_discord_proof(tmp_path, monkeypatch):
    monkeypatch.setattr(devstream_cycle_report, "ROOT", tmp_path)
    truth_dir = tmp_path / "devstream" / "truth"
    monkeypatch.setattr(devstream_cycle_report, "OUTPUT_JSON", truth_dir / "cycle-report.json")
    monkeypatch.setattr(devstream_cycle_report, "OUTPUT_MD", truth_dir / "cycle-report.md")
    monkeypatch.setattr(devstream_cycle_report, "OUTPUT_COMPLETION", truth_dir / "completion.json")
    monkeypatch.setattr(devstream_cycle_report, "OUTPUT_PROOF_STATUS", truth_dir / "proof-status.json")
    monkeypatch.setattr(devstream_cycle_report, "OUTPUT_ELO_PROOF", truth_dir / "latest-elo-proof.json")
    monkeypatch.setattr(devstream_cycle_report, "DISCORD_REPORTING", truth_dir / "discord-reporting.json")
    monkeypatch.setattr(devstream_cycle_report, "DISCORD_DELIVERY", truth_dir / "discord-delivery.json")
    monkeypatch.setattr(
        devstream_cycle_report.devstream_health,
        "build_payload",
        lambda check_http=True: {
            "healthy": False,
            "status": "degraded",
            "readyForLiveFocus": False,
            "activeBattleCount": 0,
            "readiness": {"runtimeReady": False, "streamReady": True, "analyticsFresh": True},
            "blockers": ["fouler-play battle runner is idle; OBS HTTP alone is not active battle proof"],
        },
    )

    queue_file = tmp_path / "events_queue.json"
    queue_file.write_text(
        json.dumps(
            [
                {
                    "id": "pending-1",
                    "timestamp": 90,
                    "event_type": "battle_result",
                    "channel": "battles",
                    "status": "pending",
                    "retry_count": 0,
                    "content": "battle finished loss vs Example in 31 turns battle-gen9ou-123",
                    "battle_id": "battle-gen9ou-123",
                    "winner": "Example",
                    "loser": "fouler-play",
                    "turns": 31,
                    "proof": {"battleIds": ["gen9ou-123"], "items": ["battle `123`"]},
                    "analysis": {
                        "currentBattleState": "battle loss; vs Example; 31 turns; id 123",
                        "whyItMatters": "loss proof should be visible locally",
                        "nextHermesAction": "review the replay",
                    },
                    "current_battle_state": "battle loss; vs Example; 31 turns; id 123",
                    "why_it_matters": "loss proof should be visible locally",
                    "next_hermes_action": "review the replay",
                    "proof_readiness": {"status": "proof-ready"},
                }
            ]
        ),
        encoding="utf-8",
    )
    monkeypatch.setenv("EVENT_QUEUE_FILE", str(queue_file))
    from infrastructure import event_queue_lib

    monkeypatch.setattr(event_queue_lib, "QUEUE_FILE", queue_file)

    (tmp_path / "active_battles.json").write_text('{"battles":[],"count":0}', encoding="utf-8")
    (tmp_path / "battle_stats.json").write_text(
        '{"battles":[{"battle_id":"battle-gen9ou-123","timestamp":"2026-05-25T12:00:00+00:00","result":"loss"}]}',
        encoding="utf-8",
    )
    autoresearch_dir = tmp_path / "replay_analysis"
    reports_dir = autoresearch_dir / "reports"
    reports_dir.mkdir(parents=True)
    (autoresearch_dir / "autoresearch_latest.json").write_text(
        json.dumps(
            {
                "batch": {
                    "end_battle_id": "battle-gen9ou-123",
                    "end_timestamp": "2026-05-25T12:00:00+00:00",
                    "size": 1,
                },
                "regression": {"status": "improving", "rating_delta": 12},
                "evidence_integrity": {
                    "loss_count": 1,
                    "losses_with_replay_json": 0,
                    "losses_with_decision_trace": 1,
                    "claims_without_evidence": [],
                },
            }
        ),
        encoding="utf-8",
    )
    (reports_dir / "autoresearch_latest.md").write_text("# report\n", encoding="utf-8")
    truth_dir.mkdir(parents=True)
    (truth_dir / "discord-delivery.json").write_text(
        json.dumps(
            {
                "schemaVersion": "fouler-play-discord-delivery/v1",
                "status": "dry-run",
                "queue": {"pending": 1, "pendingBattleResults": 1},
                "battle_id": "battle-gen9ou-123",
                "analysis": {"proofReadiness": {"status": "proof-ready", "readyForHermes": True}},
                "secretValuesPrinted": False,
            }
        ),
        encoding="utf-8",
    )
    (truth_dir / "discord-reporting.json").write_text(
        '{"schemaVersion":"fouler-play-discord-reporting/v1","status":"dry-run","secretValuesPrinted":false}',
        encoding="utf-8",
    )

    payload = devstream_cycle_report.build_payload()
    completion = devstream_cycle_report.build_completion_payload(
        payload,
        devstream_cycle_report.read_json(autoresearch_dir / "autoresearch_latest.json"),
    )
    proof_status = devstream_cycle_report.build_proof_status_payload(payload, completion)

    assert payload["readyForHandoff"] is True
    assert payload["completedCycleEvidenceAvailable"] is True
    assert payload["discordBacklogClassifiedForLocalHandoff"] is True
    assert payload["blockers"] == []
    assert any("runtime is idle after completed cycle proof" in warning for warning in payload["warnings"])
    assert any("pending Discord delivery remains locally classified" in warning for warning in payload["warnings"])
    assert payload["nextHermesAction"] == "transport Discord backlog when approved; local redacted proof is classified for rehearsal handoff"
    assert completion["status"] == "cycle-proof-current"
    assert proof_status["status"] == "local-discord-proof-classified"
    assert proof_status["readyForProofHandoff"] is True


def test_cycle_report_completion_payload_blocks_with_active_battles():
    cycle = {
        "generatedAt": "2026-05-25T12:35:23+00:00",
        "activeBattles": {"battleCount": 1},
        "blockers": [],
        "warnings": [],
        "streamStatus": {},
        "autoresearch": {
            "json": {"exists": True, "path": "replay_analysis/autoresearch_latest.json"},
            "report": {"exists": True, "path": "replay_analysis/reports/autoresearch_latest.md"},
        },
    }

    completion = devstream_cycle_report.build_completion_payload(
        cycle,
        {
            "regression": {"status": "improving", "rating_delta": 5},
            "evidence_integrity": {
                "loss_count": 0,
                "losses_with_replay_json": 0,
                "losses_with_decision_trace": 0,
                "claims_without_evidence": [],
            },
        },
    )

    assert completion["status"] == "cycle-proof-blocked"
    assert completion["latestBattleLearningVerified"] is False
    assert completion["activeImprovementVerified"] is False
    assert completion["blockers"] == ["active battles are still present; completion proof is not final"]
    assert completion["activeBattleTelemetryPresent"] is True
    assert completion["activeBattleTelemetryIsCompletionProof"] is False


def test_cycle_report_excludes_terminal_ghost_active_battles(tmp_path, monkeypatch):
    monkeypatch.setattr(devstream_cycle_report, "ROOT", tmp_path)
    truth_dir = tmp_path / "devstream" / "truth"
    truth_dir.mkdir(parents=True)
    monkeypatch.setattr(devstream_cycle_report, "OUTPUT_JSON", truth_dir / "cycle-report.json")
    monkeypatch.setattr(devstream_cycle_report, "OUTPUT_MD", truth_dir / "cycle-report.md")
    monkeypatch.setattr(devstream_cycle_report, "OUTPUT_COMPLETION", truth_dir / "completion.json")
    monkeypatch.setattr(devstream_cycle_report, "OUTPUT_PROOF_STATUS", truth_dir / "proof-status.json")
    monkeypatch.setattr(devstream_cycle_report, "OUTPUT_ELO_PROOF", truth_dir / "latest-elo-proof.json")
    monkeypatch.setattr(devstream_cycle_report, "DISCORD_REPORTING", truth_dir / "discord-reporting.json")
    monkeypatch.setattr(devstream_cycle_report, "DISCORD_DELIVERY", truth_dir / "discord-delivery.json")
    monkeypatch.setattr(
        devstream_cycle_report.devstream_health,
        "build_payload",
        lambda check_http=True: {
            "healthy": True,
            "status": "ready",
            "readyForLiveFocus": True,
            "activeBattleCount": 0,
            "readiness": {},
            "devstreamReporting": {},
            "blockers": [],
        },
    )
    (tmp_path / "active_battles.json").write_text(
        json.dumps({"battles": [{"id": "battle-gen9ou-2618904557"}], "count": 1}),
        encoding="utf-8",
    )
    (tmp_path / "battle_stats.json").write_text(
        json.dumps({"battles": [{"battle_id": "battle-gen9ou-2618904557", "result": "loss"}]}),
        encoding="utf-8",
    )
    (tmp_path / "stream_status.json").write_text('{"status":"Ready"}', encoding="utf-8")
    (tmp_path / "daily_stats.json").write_text('{"wins":0,"losses":1}', encoding="utf-8")
    (truth_dir / "discord-delivery.json").write_text('{"status":"idle","secretValuesPrinted":false}', encoding="utf-8")
    (truth_dir / "discord-reporting.json").write_text('{"status":"idle","secretValuesPrinted":false}', encoding="utf-8")

    payload = devstream_cycle_report.build_payload()
    completion = devstream_cycle_report.build_completion_payload(payload, {})
    proof_status = devstream_cycle_report.build_proof_status_payload(payload, completion)

    assert payload["activeBattles"]["battleCount"] == 0
    assert payload["activeBattles"]["rawBattleCount"] == 1
    assert payload["activeBattles"]["ghostBattleIds"] == ["battle-gen9ou-2618904557"]
    assert any("not counting ghost battle telemetry as live proof" in warning for warning in payload["warnings"])
    assert "active battles are still present; completion proof is not final" not in completion["blockers"]
    assert proof_status["activeBattleTelemetry"]["ghostBattleCount"] == 1


def test_proof_status_labels_active_telemetry_as_not_final_proof():
    cycle = {
        "generatedAt": "2026-05-25T12:35:23+00:00",
        "activeBattles": {
            "battleCount": 2,
            "battleIds": ["battle-gen9ou-1", "battle-gen9ou-2"],
            "classification": "active-battle-telemetry",
            "proofNote": "active battle telemetry shows battles in progress; it is not completed cycle proof",
        },
        "queueBacklog": {
            "pending": 3,
            "pendingBattleResults": 3,
            "pendingEventTypes": {"battle_result": 3},
            "pendingAgeBuckets": {"lt5m": 1, "m5to60": 2, "h1to24": 0, "d1to3": 0, "gt3d": 0},
            "pendingPlaceholderFieldCounts": {"falseTurns": 1},
            "backlogClassification": {
                "status": "backlogged",
                "severity": "reliability-blocker",
                "whyItMatters": "3 battle reports are waiting.",
                "nextHermesAction": "drain Discord queue",
                "blocking": True,
            },
            "proofReadiness": {
                "status": "queue-backlogged",
                "readyForProofHandoff": False,
                "pendingBattleResults": 3,
                "machineActionablePendingBattleResults": 3,
                "missingStructuredFieldCounts": {},
                "nextHermesAction": "drain Discord queue",
                "blockers": ["3 battle reports are waiting."],
            },
            "nextHermesAction": "drain Discord queue",
            "oldestPendingAgeSeconds": 1200,
            "deliveryFailures": 0,
            "dnsFailures": 0,
            "webhookFailures": 0,
            "healthStatus": "backlogged",
        },
        "discordDelivery": {
            "status": "dry-run",
            "cycleId": "discord-test",
            "currentBattleState": "battle loss; vs Example",
            "whyItMatters": "losses need analysis",
            "nextHermesAction": "analyze latest loss",
            "proofReadiness": {"status": "proof-ready"},
            "secretValuesPrinted": False,
        },
        "nextHermesAction": "drain Discord queue",
        "blockers": ["active battles are still present; completion proof is not final"],
        "warnings": [],
    }
    completion = {
        "proofKind": "completed-cycle-proof",
        "status": "cycle-proof-blocked",
        "latestBattleId": "battle-gen9ou-0",
        "latestBattleAt": "2026-05-25T12:00:00+00:00",
        "latestBattleLearningVerified": False,
        "performanceTrendStatus": "flat",
    }

    proof_status = devstream_cycle_report.build_proof_status_payload(cycle, completion)

    assert proof_status["status"] == "active-telemetry-not-final-proof"
    assert proof_status["readyForProofHandoff"] is False
    assert proof_status["activeBattleTelemetry"]["isCompletedProof"] is False
    assert proof_status["completedCycleProof"]["classification"] == "completed-cycle-proof"
    assert proof_status["discordBacklog"]["pendingEventTypes"] == {"battle_result": 3}
    assert proof_status["discordBacklog"]["pendingAgeBuckets"]["m5to60"] == 2
    assert proof_status["discordBacklog"]["pendingPlaceholderFieldCounts"] == {"falseTurns": 1}
    assert proof_status["discordBacklog"]["backlogClassification"]["status"] == "backlogged"
    assert proof_status["discordBacklog"]["proofReadiness"]["status"] == "queue-backlogged"
    assert proof_status["discordBacklog"]["nextHermesAction"] == "drain Discord queue"
    assert proof_status["discordDeliveryProof"]["currentBattleState"] == "battle loss; vs Example"
    assert proof_status["discordDeliveryProof"]["nextHermesAction"] == "analyze latest loss"
    assert proof_status["nextHermesAction"] == "drain Discord queue"
    assert proof_status["secretValuesPrinted"] is False


def test_cycle_report_blocks_on_pending_discord_delivery_and_unconsumed_losses(tmp_path, monkeypatch):
    monkeypatch.setattr(devstream_cycle_report, "ROOT", tmp_path)
    monkeypatch.setattr(devstream_cycle_report, "DISCORD_REPORTING", tmp_path / "devstream" / "truth" / "discord-reporting.json")
    monkeypatch.setattr(devstream_cycle_report, "DISCORD_DELIVERY", tmp_path / "devstream" / "truth" / "discord-delivery.json")
    monkeypatch.setattr(
        devstream_cycle_report.devstream_health,
        "build_payload",
        lambda check_http=True: {
            "healthy": True,
            "status": "healthy",
            "readyForLiveFocus": True,
            "activeBattleCount": 0,
            "readiness": {"runtimeReady": True, "streamReady": True, "analyticsFresh": True},
            "blockers": [],
        },
    )

    queue_file = tmp_path / "events_queue.json"
    queue_file.write_text(
        """
[
  {"id":"1","timestamp":90,"event_type":"battle_result","status":"pending","retry_count":0,"content":"battle finished loss in False turns"},
  {"id":"2","timestamp":80,"event_type":"battle_result","status":"failed","last_error":"dns_failure","retry_count":3}
]
""",
        encoding="utf-8",
    )
    monkeypatch.setenv("EVENT_QUEUE_FILE", str(queue_file))
    from infrastructure import event_queue_lib

    monkeypatch.setattr(event_queue_lib, "QUEUE_FILE", queue_file)

    stats_path = tmp_path / "battle_stats.json"
    stats_path.write_text(
        """
{
  "battles": [
    {"battle_id": "battle-gen9ou-1", "timestamp": "2026-05-25T12:00:00+00:00", "result": "win"},
    {"battle_id": "battle-gen9ou-2", "timestamp": "2026-05-25T12:10:00+00:00", "result": "loss"}
  ]
}
""",
        encoding="utf-8",
    )
    autoresearch_dir = tmp_path / "replay_analysis"
    autoresearch_dir.mkdir()
    (autoresearch_dir / "autoresearch_latest.json").write_text(
        """
{
  "batch": {
    "end_battle_id": "battle-gen9ou-1",
    "end_timestamp": "2026-05-25T12:00:00+00:00",
    "size": 1
  }
}
""",
        encoding="utf-8",
    )
    truth_dir = tmp_path / "devstream" / "truth"
    truth_dir.mkdir(parents=True)
    (truth_dir / "discord-delivery.json").write_text(
        '{"schemaVersion":"fouler-play-discord-delivery/v1","status":"dry-run","queue":{"pending":1,"pendingBattleResults":1},"secretValuesPrinted":false}',
        encoding="utf-8",
    )
    (truth_dir / "discord-reporting.json").write_text(
        '{"schemaVersion":"fouler-play-discord-reporting/v1","status":"dry-run","secretValuesPrinted":false}',
        encoding="utf-8",
    )

    payload = devstream_cycle_report.build_payload()

    assert payload["readyForHandoff"] is False
    assert payload["queueBacklog"]["pending"] == 1
    assert payload["queueBacklog"]["pendingEventTypes"] == {"battle_result": 1}
    assert payload["queueBacklog"]["pendingPlaceholderFieldCounts"] == {"falseTurns": 1}
    assert payload["queueBacklog"]["oldestPendingAgeSeconds"] is not None
    assert payload["queueBacklog"]["deliveryFailures"] == 1
    assert payload["queueBacklog"]["dnsFailures"] == 1
    assert payload["queueBacklog"]["webhookFailures"] == 0
    assert payload["queueBacklog"]["healthStatus"] == "dns-failed"
    assert payload["discordDelivery"]["status"] == "dry-run"
    assert payload["backlogClassification"]["status"] == "dns-failed"
    assert payload["proofReadiness"]["status"] == "delivery-failed"
    assert "repair DNS" in payload["nextHermesAction"]
    assert payload["unconsumedBattles"]["unconsumedCount"] == 1
    assert payload["unconsumedBattles"]["unconsumedLosses"] == 1
    assert any("pending Discord delivery remains: 1 event(s), 1 battle_result event(s)" in item for item in payload["blockers"])
    assert any("Discord queue has 1 DNS failure(s)" in item for item in payload["blockers"])
    assert any("loss-learning is blocked until 1 unconsumed loss battle(s) are analyzed" in item for item in payload["blockers"])


def test_cycle_report_write_refreshes_redacted_discord_preview(tmp_path, monkeypatch):
    truth_dir = tmp_path / "devstream" / "truth"
    truth_dir.mkdir(parents=True)
    monkeypatch.setattr(devstream_cycle_report, "ROOT", tmp_path)
    monkeypatch.setattr(devstream_cycle_report, "OUTPUT_JSON", truth_dir / "cycle-report.json")
    monkeypatch.setattr(devstream_cycle_report, "OUTPUT_MD", truth_dir / "cycle-report.md")
    monkeypatch.setattr(devstream_cycle_report, "OUTPUT_COMPLETION", truth_dir / "completion.json")
    monkeypatch.setattr(devstream_cycle_report, "OUTPUT_PROOF_STATUS", truth_dir / "proof-status.json")
    monkeypatch.setattr(devstream_cycle_report, "OUTPUT_ELO_PROOF", truth_dir / "latest-elo-proof.json")
    monkeypatch.setattr(devstream_cycle_report, "DISCORD_REPORTING", truth_dir / "discord-reporting.json")
    monkeypatch.setattr(devstream_cycle_report, "DISCORD_DELIVERY", truth_dir / "discord-delivery.json")
    monkeypatch.setattr(
        devstream_cycle_report.devstream_health,
        "build_payload",
        lambda check_http=True: {
            "healthy": True,
            "status": "healthy",
            "readyForLiveFocus": True,
            "activeBattleCount": 0,
            "readiness": {"runtimeReady": True, "streamReady": True, "analyticsFresh": True},
            "blockers": [],
        },
    )

    queue_file = tmp_path / "events_queue.json"
    queue_file.write_text(
        json.dumps(
            [
                {
                    "id": "pending-1",
                    "timestamp": 90,
                    "event_type": "battle_result",
                    "channel": "battles",
                    "status": "pending",
                    "retry_count": 0,
                    "content": "battle finished loss vs Example in 31 turns battle-gen9ou-123",
                    "battle_id": "battle-gen9ou-123",
                    "winner": "Example",
                    "loser": "fouler-play",
                    "turns": 31,
                    "proof": {"battleIds": ["gen9ou-123"], "items": ["battle `123`"]},
                    "analysis": {
                        "currentBattleState": "battle loss; vs Example; 31 turns; id 123",
                        "whyItMatters": "loss proof should be visible locally",
                        "nextHermesAction": "review the replay",
                    },
                }
            ]
        ),
        encoding="utf-8",
    )
    monkeypatch.setenv("EVENT_QUEUE_FILE", str(queue_file))

    from infrastructure import event_poster, event_queue_lib

    monkeypatch.setattr(event_queue_lib, "QUEUE_FILE", queue_file)
    monkeypatch.setattr(event_poster, "PROJECT_ROOT", tmp_path)
    monkeypatch.setattr(event_poster, "TRUTH_DIR", truth_dir)
    monkeypatch.setattr(event_poster, "DISCORD_REPORTING_PROOF", truth_dir / "discord-reporting.json")
    monkeypatch.setattr(event_poster, "DISCORD_DELIVERY_PROOF", truth_dir / "discord-delivery.json")
    monkeypatch.setattr(event_poster, "DISCORD_DOCTOR_PROOF", truth_dir / "discord-reporting-doctor.json")
    monkeypatch.setattr(event_poster, "LOG_FILE", tmp_path / "logs" / "event_poster.log")

    (truth_dir / "discord-delivery.json").write_text(
        '{"schemaVersion":"fouler-play-discord-delivery/v1","status":"dry-run","eventId":"stale"}',
        encoding="utf-8",
    )
    monkeypatch.setattr(
        devstream_cycle_report,
        "fetch_showdown_profile_rating",
        lambda account, fmt="gen9ou": {
            "status": "unresolved-account",
            "showdownUserId": "unknown",
            "format": fmt,
            "rating": None,
            "noRuntimeActions": True,
        },
    )
    monkeypatch.setattr(sys, "argv", ["devstream_cycle_report.py", "--write"])

    assert devstream_cycle_report.main() == 0

    cycle = json.loads((truth_dir / "cycle-report.json").read_text(encoding="utf-8"))
    delivery = json.loads((truth_dir / "discord-delivery.json").read_text(encoding="utf-8"))
    queue = json.loads(queue_file.read_text(encoding="utf-8"))

    assert cycle["discordProofRefresh"]["refreshed"] is True
    assert cycle["discordProofRefresh"]["eventId"] == "pending-1"
    assert cycle["discordDelivery"]["battle_id"] == "battle-gen9ou-123"
    assert delivery["eventId"] == "pending-1"
    assert delivery["status"] == "dry-run"
    assert delivery["queue"]["pendingBacklog"] == 1
    assert delivery["secretValuesPrinted"] is False
    assert queue[0]["status"] == "pending"


def test_cycle_report_write_records_fresh_generated_artifact_metadata(tmp_path, monkeypatch):
    truth_dir = tmp_path / "devstream" / "truth"
    truth_dir.mkdir(parents=True)
    monkeypatch.setattr(devstream_cycle_report, "ROOT", tmp_path)
    monkeypatch.setattr(devstream_cycle_report, "OUTPUT_JSON", truth_dir / "cycle-report.json")
    monkeypatch.setattr(devstream_cycle_report, "OUTPUT_MD", truth_dir / "cycle-report.md")
    monkeypatch.setattr(devstream_cycle_report, "OUTPUT_COMPLETION", truth_dir / "completion.json")
    monkeypatch.setattr(devstream_cycle_report, "OUTPUT_PROOF_STATUS", truth_dir / "proof-status.json")
    monkeypatch.setattr(devstream_cycle_report, "OUTPUT_ELO_PROOF", truth_dir / "latest-elo-proof.json")
    monkeypatch.setattr(devstream_cycle_report, "DISCORD_REPORTING", truth_dir / "discord-reporting.json")
    monkeypatch.setattr(devstream_cycle_report, "DISCORD_DELIVERY", truth_dir / "discord-delivery.json")
    monkeypatch.setattr(devstream_cycle_report, "refresh_discord_proof_preview", lambda: {"refreshed": True})
    monkeypatch.setattr(
        devstream_cycle_report.devstream_health,
        "build_payload",
        lambda check_http=True: {
            "healthy": True,
            "status": "healthy",
            "readyForLiveFocus": True,
            "activeBattleCount": 0,
            "readiness": {"runtimeReady": True, "streamReady": True, "analyticsFresh": True},
            "blockers": [],
        },
    )

    (tmp_path / "active_battles.json").write_text('{"battles":[],"count":0}', encoding="utf-8")
    (tmp_path / "stream_status.json").write_text('{"status":"Ready"}', encoding="utf-8")
    (tmp_path / "daily_stats.json").write_text('{"wins":1,"losses":0}', encoding="utf-8")
    (tmp_path / "battle_stats.json").write_text(
        '{"battles":[{"battle_id":"battle-gen9ou-123","timestamp":"2026-05-25T12:00:00+00:00","result":"win"}]}',
        encoding="utf-8",
    )
    autoresearch_dir = tmp_path / "replay_analysis"
    reports_dir = autoresearch_dir / "reports"
    reports_dir.mkdir(parents=True)
    (autoresearch_dir / "autoresearch_latest.json").write_text(
        '{"batch":{"end_battle_id":"battle-gen9ou-123","end_timestamp":"2026-05-25T12:00:00+00:00","size":1}}',
        encoding="utf-8",
    )
    (reports_dir / "autoresearch_latest.md").write_text("# report\n", encoding="utf-8")
    (truth_dir / "discord-delivery.json").write_text(
        '{"schemaVersion":"fouler-play-discord-delivery/v1","status":"idle","queue":{"pending":0,"pendingBattleResults":0},"secretValuesPrinted":false}',
        encoding="utf-8",
    )
    monkeypatch.setattr(
        devstream_cycle_report,
        "fetch_showdown_profile_rating",
        lambda account, fmt="gen9ou": {
            "status": "unresolved-account",
            "showdownUserId": "unknown",
            "format": fmt,
            "rating": None,
            "noRuntimeActions": True,
        },
    )
    monkeypatch.setattr(sys, "argv", ["devstream_cycle_report.py", "--write"])

    assert devstream_cycle_report.main() == 0

    cycle = json.loads((truth_dir / "cycle-report.json").read_text(encoding="utf-8"))
    proof_status = json.loads((truth_dir / "proof-status.json").read_text(encoding="utf-8"))
    elo_proof = json.loads((truth_dir / "latest-elo-proof.json").read_text(encoding="utf-8"))

    assert cycle["written"] == [str(truth_dir / "cycle-report.json"), str(truth_dir / "cycle-report.md"), str(truth_dir / "completion.json"), str(truth_dir / "proof-status.json"), str(truth_dir / "latest-elo-proof.json")]
    assert cycle["truthFiles"]["completion"]["exists"] is True
    assert cycle["truthFiles"]["proofStatus"]["exists"] is True
    assert cycle["truthFiles"]["latestEloProof"]["exists"] is True
    assert cycle["truthFiles"]["proofStatus"]["updatedAt"] is not None
    assert cycle["truthFiles"]["proofStatus"]["ageSeconds"] < 2
    assert cycle["truthFiles"]["latestEloProof"]["updatedAt"] is not None
    assert cycle["truthFiles"]["latestEloProof"]["ageSeconds"] < 2
    assert proof_status["generatedAt"] == cycle["generatedAt"]
    assert elo_proof["schemaVersion"] == "fouler-play-elo-proof/v1"
    assert elo_proof["source"]["generatedBy"] == "scripts/devstream_cycle_report.py"
