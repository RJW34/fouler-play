import json

from scripts import fouler_cycle_digest as digest


def test_digest_ranks_start_gate_then_replay_issue(monkeypatch, tmp_path):
    root = tmp_path
    truth = root / "devstream" / "truth"
    truth.mkdir(parents=True)
    replay = root / "replay_analysis"
    reports = replay / "reports"
    reports.mkdir(parents=True)
    pids = root / ".pids"
    pids.mkdir()

    (truth / "mission-monitor.json").write_text(
        json.dumps(
            {
                "startGate": {
                    "ready": False,
                    "blockingIssueIds": ["fouler-rating-drawdown", "fouler-session-stop-loss-breached"],
                }
            }
        ),
        encoding="utf-8",
    )
    (replay / "autoresearch_latest.json").write_text(
        json.dumps(
            {
                "generated_at": "2026-07-05T00:00:00+00:00",
                "window_size": 60,
                "wins": 25,
                "losses": 35,
                "win_rate": 0.416,
                "top_issue": {
                    "key": "decision_instability",
                    "title": "Decision traces show unstable fallback behavior",
                    "recommendation": "Prioritize stability fixes around slow or failing decision branches.",
                    "proof": ["trace-backed loss"],
                },
            }
        ),
        encoding="utf-8",
    )
    (reports / "autoresearch_latest.md").write_text("# report\n", encoding="utf-8")
    (root / "events_queue.json").write_text(
        json.dumps([{"event_type": "fouler_cycle_digest", "status": "pending"}]),
        encoding="utf-8",
    )
    (root / "active_battles.json").write_text('{"count":0}', encoding="utf-8")
    (pids / "supervisor.stop").write_text("blocked\n", encoding="utf-8")

    monkeypatch.setattr(digest, "ROOT", root)
    monkeypatch.setattr(digest, "TRUTH_DIR", truth)
    monkeypatch.setattr(digest, "MISSION_MONITOR", truth / "mission-monitor.json")
    monkeypatch.setattr(digest, "AUTORESEARCH_JSON", replay / "autoresearch_latest.json")
    monkeypatch.setattr(digest, "AUTORESEARCH_MD", reports / "autoresearch_latest.md")
    monkeypatch.setattr(digest, "EVENT_QUEUE", root / "events_queue.json")
    monkeypatch.setattr(digest, "ACTIVE_BATTLES", root / "active_battles.json")
    monkeypatch.setattr(digest, "POST_PACKET_EVAL", truth / "post-packet-eval.json")

    payload = digest.build_payload()

    assert payload["status"] == "action-required"
    assert payload["runtime"]["activeBattleCount"] == 0
    assert payload["runtime"]["supervisorStopFilePresent"] is True
    assert payload["rankedBreakages"][0]["area"] == "start-gate"
    assert "fouler-rating-drawdown" in payload["rankedBreakages"][0]["evidence"]
    assert payload["rankedBreakages"][1]["area"] == "replay-review"
    assert payload["discord"]["pendingCount"] == 1
    assert payload["networkSendAllowed"] is False


def test_digest_ranks_abandoned_result_capture_before_elo(monkeypatch, tmp_path):
    root = tmp_path
    truth = root / "devstream" / "truth"
    truth.mkdir(parents=True)
    replay = root / "replay_analysis"
    reports = replay / "reports"
    reports.mkdir(parents=True)

    required_action = "Root-cause the runner exit before opening another proof window."
    (truth / "mission-monitor.json").write_text(
        json.dumps(
            {
                "classification": {
                    "abandonedBattleCleanup": {
                        "ready": False,
                        "status": "abandoned-active-battle-without-result",
                        "missingBattleIds": ["battle-gen9ou-abandoned"],
                        "sourceBackupPath": "devstream/truth/stale-active-battles-backups/active_battles-test.json",
                        "sourceBackupMtimeUtc": "2026-07-05T16:33:11+00:00",
                        "latestBattleStatsAtUtc": "2026-07-05T16:20:02+00:00",
                        "requiredAction": required_action,
                    },
                    "eloSustainProof": {
                        "ready": False,
                        "blockers": ["ELO proof never reaches 1700"],
                        "ratings": {"currentRating": 1144.38, "peakRating": 1560},
                        "target": {"proofRatingFloor": 1700},
                    },
                },
                "startGate": {
                    "ready": False,
                    "blockingIssueIds": ["fouler-abandoned-battle-without-result"],
                },
            }
        ),
        encoding="utf-8",
    )
    (replay / "autoresearch_latest.json").write_text('{"issues":[]}', encoding="utf-8")
    (reports / "autoresearch_latest.md").write_text("# report\n", encoding="utf-8")
    (root / "events_queue.json").write_text("[]", encoding="utf-8")
    (root / "active_battles.json").write_text('{"count":0}', encoding="utf-8")

    monkeypatch.setattr(digest, "ROOT", root)
    monkeypatch.setattr(digest, "TRUTH_DIR", truth)
    monkeypatch.setattr(digest, "MISSION_MONITOR", truth / "mission-monitor.json")
    monkeypatch.setattr(digest, "AUTORESEARCH_JSON", replay / "autoresearch_latest.json")
    monkeypatch.setattr(digest, "AUTORESEARCH_MD", reports / "autoresearch_latest.md")
    monkeypatch.setattr(digest, "EVENT_QUEUE", root / "events_queue.json")
    monkeypatch.setattr(digest, "ACTIVE_BATTLES", root / "active_battles.json")
    monkeypatch.setattr(digest, "POST_PACKET_EVAL", truth / "post-packet-eval.json")

    payload = digest.build_payload()

    assert payload["rankedBreakages"][0]["area"] == "runtime-result-capture"
    assert "missingResultBattle=battle-gen9ou-abandoned" in payload["rankedBreakages"][0]["evidence"]
    assert payload["singleNextAction"] == required_action
    assert payload["rankedBreakages"][1]["area"] == "start-gate"
    assert payload["rankedBreakages"][2]["area"] == "elo-sustain"


def test_digest_ranks_engine_promotion_gate_before_start_gate(monkeypatch, tmp_path):
    root = tmp_path
    truth = root / "devstream" / "truth"
    truth.mkdir(parents=True)
    replay = root / "replay_analysis"
    reports = replay / "reports"
    reports.mkdir(parents=True)
    pids = root / ".pids"
    pids.mkdir()

    (truth / "mission-monitor.json").write_text(
        json.dumps(
            {
                "startGate": {
                    "ready": False,
                    "blockingIssueIds": ["fouler-supervisor-stop-file-present"],
                }
            }
        ),
        encoding="utf-8",
    )
    (truth / "engine-promotion-gate.json").write_text(
        json.dumps(
            {
                "status": "promotion-blocked",
                "candidatePacketId": "fouler-auto-011-hazard-self-ko-switch-guard",
                "promotionAllowed": False,
                "blockers": ["post-packet preservation proof is not satisfied"],
                "singleNextAction": "Do not stack another engine heuristic; preserve or reject the current packet.",
            }
        ),
        encoding="utf-8",
    )
    (replay / "autoresearch_latest.json").write_text('{"issues":[]}', encoding="utf-8")
    (reports / "autoresearch_latest.md").write_text("# report\n", encoding="utf-8")
    (root / "events_queue.json").write_text("[]", encoding="utf-8")
    (root / "active_battles.json").write_text('{"count":0}', encoding="utf-8")
    (pids / "supervisor.stop").write_text("blocked\n", encoding="utf-8")

    monkeypatch.setattr(digest, "ROOT", root)
    monkeypatch.setattr(digest, "TRUTH_DIR", truth)
    monkeypatch.setattr(digest, "MISSION_MONITOR", truth / "mission-monitor.json")
    monkeypatch.setattr(digest, "AUTORESEARCH_JSON", replay / "autoresearch_latest.json")
    monkeypatch.setattr(digest, "AUTORESEARCH_MD", reports / "autoresearch_latest.md")
    monkeypatch.setattr(digest, "EVENT_QUEUE", root / "events_queue.json")
    monkeypatch.setattr(digest, "ACTIVE_BATTLES", root / "active_battles.json")
    monkeypatch.setattr(digest, "POST_PACKET_EVAL", truth / "post-packet-eval.json")

    payload = digest.build_payload()

    assert payload["rankedBreakages"][0]["area"] == "engine-promotion"
    assert payload["singleNextAction"].startswith("Do not stack another engine heuristic")
    assert payload["enginePromotionGate"]["status"] == "promotion-blocked"
    assert payload["enginePromotionGate"]["blockerCount"] == 1
    assert payload["rankedBreakages"][1]["area"] == "start-gate"


def test_digest_flags_discord_noise(monkeypatch, tmp_path):
    root = tmp_path
    truth = root / "devstream" / "truth"
    truth.mkdir(parents=True)
    (truth / "mission-monitor.json").write_text('{"startGate":{"ready":true}}', encoding="utf-8")
    replay = root / "replay_analysis"
    reports = replay / "reports"
    reports.mkdir(parents=True)
    (replay / "autoresearch_latest.json").write_text("{}", encoding="utf-8")
    (reports / "autoresearch_latest.md").write_text("# report\n", encoding="utf-8")
    (root / "active_battles.json").write_text('{"count":0}', encoding="utf-8")
    (root / "events_queue.json").write_text(
        json.dumps(
            [
                {"event_type": "autoresearch_summary", "status": "pending"},
                {"event_type": "autoresearch_deep_dive", "status": "pending"},
            ]
        ),
        encoding="utf-8",
    )

    monkeypatch.setattr(digest, "ROOT", root)
    monkeypatch.setattr(digest, "TRUTH_DIR", truth)
    monkeypatch.setattr(digest, "MISSION_MONITOR", truth / "mission-monitor.json")
    monkeypatch.setattr(digest, "AUTORESEARCH_JSON", replay / "autoresearch_latest.json")
    monkeypatch.setattr(digest, "AUTORESEARCH_MD", reports / "autoresearch_latest.md")
    monkeypatch.setattr(digest, "EVENT_QUEUE", root / "events_queue.json")
    monkeypatch.setattr(digest, "ACTIVE_BATTLES", root / "active_battles.json")
    monkeypatch.setattr(digest, "POST_PACKET_EVAL", truth / "post-packet-eval.json")

    payload = digest.build_payload()

    assert payload["rankedBreakages"][0]["area"] == "discord"
    assert payload["rankedBreakages"][0]["status"] == "noisy"
    assert payload["singleNextAction"].startswith("Retain routine analysis locally")


def test_digest_uses_post_packet_preservation_action_after_active_improvement(monkeypatch, tmp_path):
    root = tmp_path
    truth = root / "devstream" / "truth"
    truth.mkdir(parents=True)
    replay = root / "replay_analysis"
    reports = replay / "reports"
    reports.mkdir(parents=True)
    pids = root / ".pids"
    pids.mkdir()

    (truth / "mission-monitor.json").write_text(
        json.dumps(
            {
                "classification": {"activeImprovementProof": {"ready": True, "status": "accepted"}},
                "startGate": {"ready": False, "blockingIssueIds": ["fouler-supervisor-stop-file-present"]},
            }
        ),
        encoding="utf-8",
    )
    next_action = "Mark the packet evaluated only after the reduced failure class and positive performance signal are preserved in the next bounded proof window."
    (truth / "post-packet-eval.json").write_text(
        json.dumps(
            {
                "status": "post-packet-eval-improving",
                "nextActions": [next_action],
                "packet": {"id": "fouler-auto-006-forced-switch-regret-calibration"},
                "failureClass": {"key": "search_regret", "status": "reduced"},
                "latestBattle": {"id": "battle-gen9ou-2644332370"},
            }
        ),
        encoding="utf-8",
    )
    (replay / "autoresearch_latest.json").write_text("{}", encoding="utf-8")
    (reports / "autoresearch_latest.md").write_text("# report\n", encoding="utf-8")
    (root / "events_queue.json").write_text("[]", encoding="utf-8")
    (root / "active_battles.json").write_text('{"count":0}', encoding="utf-8")
    (pids / "supervisor.stop").write_text("blocked\n", encoding="utf-8")

    monkeypatch.setattr(digest, "ROOT", root)
    monkeypatch.setattr(digest, "TRUTH_DIR", truth)
    monkeypatch.setattr(digest, "MISSION_MONITOR", truth / "mission-monitor.json")
    monkeypatch.setattr(digest, "AUTORESEARCH_JSON", replay / "autoresearch_latest.json")
    monkeypatch.setattr(digest, "AUTORESEARCH_MD", reports / "autoresearch_latest.md")
    monkeypatch.setattr(digest, "EVENT_QUEUE", root / "events_queue.json")
    monkeypatch.setattr(digest, "ACTIVE_BATTLES", root / "active_battles.json")
    monkeypatch.setattr(digest, "POST_PACKET_EVAL", truth / "post-packet-eval.json")

    payload = digest.build_payload()

    assert payload["singleNextAction"] == next_action
    assert "code packet" not in payload["rankedBreakages"][0]["singleNextAction"]
    assert payload["rankedBreakages"][0]["whatIsBroken"].startswith("Fouler ladder start gate is parked")
    assert payload["postPacketEval"]["failureClassKey"] == "search_regret"


def test_digest_skips_accepted_packet_issue_when_evidence_is_preserved(monkeypatch, tmp_path):
    root = tmp_path
    truth = root / "devstream" / "truth"
    truth.mkdir(parents=True)
    replay = root / "replay_analysis"
    reports = replay / "reports"
    reports.mkdir(parents=True)
    pids = root / ".pids"
    pids.mkdir()

    (truth / "mission-monitor.json").write_text(
        json.dumps(
            {
                "classification": {"activeImprovementProof": {"ready": True, "status": "accepted"}},
                "startGate": {"ready": False, "blockingIssueIds": ["fouler-supervisor-stop-file-present"]},
            }
        ),
        encoding="utf-8",
    )
    (truth / "post-packet-eval.json").write_text(
        json.dumps(
            {
                "status": "post-packet-eval-accepted",
                "packet": {"id": "packet-007"},
                "failureClass": {"key": "endgame_conversion", "status": "reduced"},
                "proofWindow": {"preservationSatisfied": True, "postPacketFailureEvidenceBattleIds": []},
            }
        ),
        encoding="utf-8",
    )
    (replay / "autoresearch_latest.json").write_text(
        json.dumps(
            {
                "top_issue": {"key": "endgame_conversion", "title": "Accepted stale issue", "recommendation": "Do not rerun."},
                "issues": [
                    {"key": "endgame_conversion", "title": "Accepted stale issue", "recommendation": "Do not rerun."},
                    {"key": "decision_instability", "title": "Decision instability", "recommendation": "Build the instability packet.", "proof": ["fresh trace"]},
                ],
            }
        ),
        encoding="utf-8",
    )
    (reports / "autoresearch_latest.md").write_text("# report\n", encoding="utf-8")
    (root / "events_queue.json").write_text("[]", encoding="utf-8")
    (root / "active_battles.json").write_text('{"count":0}', encoding="utf-8")
    (pids / "supervisor.stop").write_text("blocked\n", encoding="utf-8")

    monkeypatch.setattr(digest, "ROOT", root)
    monkeypatch.setattr(digest, "TRUTH_DIR", truth)
    monkeypatch.setattr(digest, "MISSION_MONITOR", truth / "mission-monitor.json")
    monkeypatch.setattr(digest, "AUTORESEARCH_JSON", replay / "autoresearch_latest.json")
    monkeypatch.setattr(digest, "AUTORESEARCH_MD", reports / "autoresearch_latest.md")
    monkeypatch.setattr(digest, "EVENT_QUEUE", root / "events_queue.json")
    monkeypatch.setattr(digest, "ACTIVE_BATTLES", root / "active_battles.json")
    monkeypatch.setattr(digest, "POST_PACKET_EVAL", truth / "post-packet-eval.json")

    payload = digest.build_payload()

    assert payload["rankedBreakages"][0]["area"] == "replay-review"
    assert payload["rankedBreakages"][0]["whatIsBroken"] == "Decision instability"
    assert payload["singleNextAction"] == "Build the instability packet."


def test_digest_promotes_replay_issue_after_packet_acceptance(monkeypatch, tmp_path):
    root = tmp_path
    truth = root / "devstream" / "truth"
    truth.mkdir(parents=True)
    replay = root / "replay_analysis"
    reports = replay / "reports"
    reports.mkdir(parents=True)
    pids = root / ".pids"
    pids.mkdir()

    (truth / "mission-monitor.json").write_text(
        json.dumps(
            {
                "classification": {"activeImprovementProof": {"ready": True, "status": "accepted"}},
                "startGate": {"ready": False, "blockingIssueIds": ["fouler-supervisor-stop-file-present"]},
            }
        ),
        encoding="utf-8",
    )
    (truth / "post-packet-eval.json").write_text(
        json.dumps({"status": "post-packet-eval-accepted", "packet": {"id": "packet-1"}}),
        encoding="utf-8",
    )
    (replay / "autoresearch_latest.json").write_text(
        json.dumps(
            {
                "top_issue": {
                    "key": "endgame_conversion",
                    "title": "Long games are not being converted cleanly",
                    "recommendation": "Build the endgame packet.",
                    "proof": ["battle-gen9ou-loss: long-game loss"],
                }
            }
        ),
        encoding="utf-8",
    )
    (reports / "autoresearch_latest.md").write_text("# report\n", encoding="utf-8")
    (root / "events_queue.json").write_text("[]", encoding="utf-8")
    (root / "active_battles.json").write_text('{"count":0}', encoding="utf-8")
    (pids / "supervisor.stop").write_text("blocked\n", encoding="utf-8")

    monkeypatch.setattr(digest, "ROOT", root)
    monkeypatch.setattr(digest, "TRUTH_DIR", truth)
    monkeypatch.setattr(digest, "MISSION_MONITOR", truth / "mission-monitor.json")
    monkeypatch.setattr(digest, "AUTORESEARCH_JSON", replay / "autoresearch_latest.json")
    monkeypatch.setattr(digest, "AUTORESEARCH_MD", reports / "autoresearch_latest.md")
    monkeypatch.setattr(digest, "EVENT_QUEUE", root / "events_queue.json")
    monkeypatch.setattr(digest, "ACTIVE_BATTLES", root / "active_battles.json")
    monkeypatch.setattr(digest, "POST_PACKET_EVAL", truth / "post-packet-eval.json")

    payload = digest.build_payload()

    assert payload["rankedBreakages"][0]["area"] == "replay-review"
    assert payload["singleNextAction"] == "Build the endgame packet."


def test_digest_skips_historically_accepted_issue_without_new_evidence(monkeypatch, tmp_path):
    root = tmp_path
    truth = root / "devstream" / "truth"
    truth.mkdir(parents=True)
    replay = root / "replay_analysis"
    reports = replay / "reports"
    reports.mkdir(parents=True)

    (truth / "mission-monitor.json").write_text(
        json.dumps({"classification": {"activeImprovementProof": {"ready": True, "status": "accepted"}}, "startGate": {"ready": True}}),
        encoding="utf-8",
    )
    (truth / "post-packet-eval-20260705T144107Z.json").write_text(
        json.dumps(
            {
                "status": "post-packet-eval-accepted",
                "checkedAtUtc": "2026-07-05T14:41:07+00:00",
                "failureClass": {"key": "endgame_conversion", "status": "reduced"},
                "latestBattle": {"id": "battle-gen9ou-accepted", "at": "2026-07-05T14:40:11+00:00"},
                "proofWindow": {"preservationSatisfied": True},
            }
        ),
        encoding="utf-8",
    )
    (truth / "post-packet-eval.json").write_text(
        json.dumps(
            {
                "status": "post-packet-eval-accepted",
                "checkedAtUtc": "2026-07-05T15:28:56+00:00",
                "failureClass": {"key": "magic_bounce_reflected_hazard", "status": "reduced"},
                "latestBattle": {"id": "battle-gen9ou-current", "at": "2026-07-05T15:27:34+00:00"},
                "proofWindow": {"preservationSatisfied": True},
            }
        ),
        encoding="utf-8",
    )
    (root / "battle_stats.json").write_text(
        json.dumps(
            {
                "battles": [
                    {"battle_id": "battle-gen9ou-old-endgame", "timestamp": "2026-07-05T13:00:00+00:00"},
                    {"battle_id": "battle-gen9ou-new-decision", "timestamp": "2026-07-05T15:30:00+00:00"},
                ]
            }
        ),
        encoding="utf-8",
    )
    (replay / "autoresearch_latest.json").write_text(
        json.dumps(
            {
                "top_issue": {
                    "key": "endgame_conversion",
                    "title": "Accepted stale endgame issue",
                    "recommendation": "Do not rerun endgame.",
                    "proof": ["battle-gen9ou-old-endgame: old long-game loss"],
                },
                "issues": [
                    {
                        "key": "endgame_conversion",
                        "title": "Accepted stale endgame issue",
                        "recommendation": "Do not rerun endgame.",
                        "proof": ["battle-gen9ou-old-endgame: old long-game loss"],
                    },
                    {
                        "key": "decision_instability",
                        "title": "Decision instability",
                        "recommendation": "Build the fresh instability packet.",
                        "proof": ["battle-gen9ou-new-decision: fresh trace loop"],
                    },
                ],
            }
        ),
        encoding="utf-8",
    )
    (reports / "autoresearch_latest.md").write_text("# report\n", encoding="utf-8")
    (root / "events_queue.json").write_text("[]", encoding="utf-8")
    (root / "active_battles.json").write_text('{"count":0}', encoding="utf-8")

    monkeypatch.setattr(digest, "ROOT", root)
    monkeypatch.setattr(digest, "TRUTH_DIR", truth)
    monkeypatch.setattr(digest, "MISSION_MONITOR", truth / "mission-monitor.json")
    monkeypatch.setattr(digest, "AUTORESEARCH_JSON", replay / "autoresearch_latest.json")
    monkeypatch.setattr(digest, "AUTORESEARCH_MD", reports / "autoresearch_latest.md")
    monkeypatch.setattr(digest, "EVENT_QUEUE", root / "events_queue.json")
    monkeypatch.setattr(digest, "ACTIVE_BATTLES", root / "active_battles.json")
    monkeypatch.setattr(digest, "POST_PACKET_EVAL", truth / "post-packet-eval.json")

    payload = digest.build_payload()

    assert payload["rankedBreakages"][0]["whatIsBroken"] == "Decision instability"
    assert payload["singleNextAction"] == "Build the fresh instability packet."


def test_digest_reports_elo_blocker_after_packets_are_accepted(monkeypatch, tmp_path):
    root = tmp_path
    truth = root / "devstream" / "truth"
    truth.mkdir(parents=True)
    replay = root / "replay_analysis"
    reports = replay / "reports"
    reports.mkdir(parents=True)
    pids = root / ".pids"
    pids.mkdir()

    (truth / "mission-monitor.json").write_text(
        json.dumps(
            {
                "classification": {
                    "activeImprovementProof": {"ready": True, "status": "accepted"},
                    "eloSustainProof": {
                        "ready": False,
                        "status": "blocked",
                        "blockers": ["ELO proof never reaches 1700"],
                        "ratings": {
                            "summaryFinalRating": 1153,
                            "currentRating": 1197.25,
                            "summaryPeakRating": 1560,
                        },
                        "target": {"proofRatingFloor": 1700},
                    },
                    "ladderStage": {"nextMilestone": "reach and hold 1500 before promoting"},
                },
                "startGate": {"ready": False, "blockingIssueIds": ["fouler-supervisor-stop-file-present"]},
            }
        ),
        encoding="utf-8",
    )
    (truth / "post-packet-eval.json").write_text(
        json.dumps(
            {
                "status": "post-packet-eval-accepted",
                "failureClass": {"key": "magic_bounce_reflected_hazard", "status": "reduced"},
                "proofWindow": {"preservationSatisfied": True, "postPacketFailureEvidenceBattleIds": []},
            }
        ),
        encoding="utf-8",
    )
    (replay / "autoresearch_latest.json").write_text(json.dumps({"issues": []}), encoding="utf-8")
    (reports / "autoresearch_latest.md").write_text("# report\n", encoding="utf-8")
    (root / "events_queue.json").write_text("[]", encoding="utf-8")
    (root / "active_battles.json").write_text('{"count":0}', encoding="utf-8")
    (pids / "supervisor.stop").write_text("blocked\n", encoding="utf-8")

    monkeypatch.setattr(digest, "ROOT", root)
    monkeypatch.setattr(digest, "TRUTH_DIR", truth)
    monkeypatch.setattr(digest, "MISSION_MONITOR", truth / "mission-monitor.json")
    monkeypatch.setattr(digest, "AUTORESEARCH_JSON", replay / "autoresearch_latest.json")
    monkeypatch.setattr(digest, "AUTORESEARCH_MD", reports / "autoresearch_latest.md")
    monkeypatch.setattr(digest, "EVENT_QUEUE", root / "events_queue.json")
    monkeypatch.setattr(digest, "ACTIVE_BATTLES", root / "active_battles.json")
    monkeypatch.setattr(digest, "POST_PACKET_EVAL", truth / "post-packet-eval.json")

    payload = digest.build_payload()

    assert payload["status"] == "action-required"
    assert payload["rankedBreakages"][0]["area"] == "elo-sustain"
    assert "currentRating=1197.25" in payload["rankedBreakages"][0]["evidence"]
    assert payload["singleNextAction"].startswith("Open only the next bounded ladder proof batch")


def test_digest_falls_back_to_battle_stats_final_rating_when_live_profile_missing(monkeypatch, tmp_path):
    root = tmp_path
    truth = root / "devstream" / "truth"
    truth.mkdir(parents=True)
    replay = root / "replay_analysis"
    reports = replay / "reports"
    reports.mkdir(parents=True)

    (truth / "mission-monitor.json").write_text(
        json.dumps(
            {
                "classification": {
                    "activeImprovementProof": {"ready": True, "status": "accepted"},
                    "eloSustainProof": {
                        "ready": False,
                        "status": "blocked",
                        "blockers": ["ELO proof never reaches 1700"],
                        "ratings": {"summaryFinalRating": 1153, "summaryPeakRating": 1560},
                        "target": {"proofRatingFloor": 1700},
                    },
                    "ladderStage": {"nextMilestone": "reach and hold 1500 before promoting"},
                },
                "startGate": {"ready": True},
            }
        ),
        encoding="utf-8",
    )
    (truth / "post-packet-eval.json").write_text(
        json.dumps({"status": "post-packet-eval-accepted", "failureClass": {"status": "reduced"}}),
        encoding="utf-8",
    )
    (replay / "autoresearch_latest.json").write_text(json.dumps({"issues": []}), encoding="utf-8")
    (reports / "autoresearch_latest.md").write_text("# report\n", encoding="utf-8")
    (root / "events_queue.json").write_text("[]", encoding="utf-8")
    (root / "active_battles.json").write_text('{"count":0}', encoding="utf-8")

    monkeypatch.setattr(digest, "ROOT", root)
    monkeypatch.setattr(digest, "TRUTH_DIR", truth)
    monkeypatch.setattr(digest, "MISSION_MONITOR", truth / "mission-monitor.json")
    monkeypatch.setattr(digest, "AUTORESEARCH_JSON", replay / "autoresearch_latest.json")
    monkeypatch.setattr(digest, "AUTORESEARCH_MD", reports / "autoresearch_latest.md")
    monkeypatch.setattr(digest, "EVENT_QUEUE", root / "events_queue.json")
    monkeypatch.setattr(digest, "ACTIVE_BATTLES", root / "active_battles.json")
    monkeypatch.setattr(digest, "POST_PACKET_EVAL", truth / "post-packet-eval.json")

    payload = digest.build_payload()

    assert "currentRating=1153" in payload["rankedBreakages"][0]["evidence"]


def test_digest_skips_any_replay_issue_without_evidence_after_current_acceptance(monkeypatch, tmp_path):
    root = tmp_path
    truth = root / "devstream" / "truth"
    truth.mkdir(parents=True)
    replay = root / "replay_analysis"
    reports = replay / "reports"
    reports.mkdir(parents=True)

    (truth / "mission-monitor.json").write_text(
        json.dumps(
            {
                "classification": {
                    "activeImprovementProof": {"ready": True, "status": "accepted"},
                    "eloSustainProof": {
                        "ready": False,
                        "status": "blocked",
                        "blockers": ["ELO proof never reaches 1700"],
                        "ratings": {"summaryFinalRating": 1153},
                        "target": {"proofRatingFloor": 1700},
                    },
                    "ladderStage": {"nextMilestone": "reach and hold 1500 before promoting"},
                },
                "startGate": {"ready": True},
            }
        ),
        encoding="utf-8",
    )
    (truth / "post-packet-eval.json").write_text(
        json.dumps(
            {
                "status": "post-packet-eval-accepted",
                "checkedAtUtc": "2026-07-05T15:47:07+00:00",
                "failureClass": {"key": "magic_bounce_reflected_hazard", "status": "reduced"},
                "latestBattle": {"id": "battle-gen9ou-current-win", "at": "2026-07-05T15:46:11+00:00"},
                "proofWindow": {"preservationSatisfied": True},
            }
        ),
        encoding="utf-8",
    )
    (root / "battle_stats.json").write_text(
        json.dumps(
            {
                "battles": [
                    {"battle_id": "battle-gen9ou-old-decision", "timestamp": "2026-07-05T15:00:00+00:00"},
                    {"battle_id": "battle-gen9ou-current-win", "timestamp": "2026-07-05T15:46:11+00:00"},
                ]
            }
        ),
        encoding="utf-8",
    )
    (replay / "autoresearch_latest.json").write_text(
        json.dumps(
            {
                "top_issue": {
                    "key": "decision_instability",
                    "title": "Decision traces show unstable fallback behavior",
                    "recommendation": "Build stale instability packet.",
                    "proof": ["battle-gen9ou-old-decision: old repeated pattern"],
                }
            }
        ),
        encoding="utf-8",
    )
    (reports / "autoresearch_latest.md").write_text("# report\n", encoding="utf-8")
    (root / "events_queue.json").write_text("[]", encoding="utf-8")
    (root / "active_battles.json").write_text('{"count":0}', encoding="utf-8")

    monkeypatch.setattr(digest, "ROOT", root)
    monkeypatch.setattr(digest, "TRUTH_DIR", truth)
    monkeypatch.setattr(digest, "MISSION_MONITOR", truth / "mission-monitor.json")
    monkeypatch.setattr(digest, "AUTORESEARCH_JSON", replay / "autoresearch_latest.json")
    monkeypatch.setattr(digest, "AUTORESEARCH_MD", reports / "autoresearch_latest.md")
    monkeypatch.setattr(digest, "EVENT_QUEUE", root / "events_queue.json")
    monkeypatch.setattr(digest, "ACTIVE_BATTLES", root / "active_battles.json")
    monkeypatch.setattr(digest, "POST_PACKET_EVAL", truth / "post-packet-eval.json")

    payload = digest.build_payload()

    assert payload["rankedBreakages"][0]["area"] == "elo-sustain"
    assert "stale instability" not in payload["singleNextAction"]
