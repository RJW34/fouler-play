from scripts import devstream_post_packet_eval as evaluator


def packet(*, created_at: str = "2026-05-07T07:39:32+00:00", status: str = "draft"):
    return {
        "schemaVersion": "devstream-work-packet/v1",
        "id": "fouler-auto-001-hazard-pressure-is-being-lost",
        "status": status,
        "createdAt": created_at,
        "finding_key": "hazard_pressure",
        "title": "Hazard pressure is being lost",
    }


def elo_proof(
    *,
    latest_at: str = "2026-05-05T17:37:27+00:00",
    latest_id: str = "battle-gen9ou-2602394852",
    improving: bool = False,
):
    return {
        "schemaVersion": "fouler-play-elo-proof/v1",
        "games": [
            {
                "battleId": latest_id,
                "timestamp": latest_at,
                "result": "win" if improving else "loss",
                "ratingAfter": 1100 if improving else 1038,
            }
        ],
        "summary": {
            "latestBattleId": latest_id,
            "latestBattleAt": latest_at,
            "latestBattleLearningVerified": True,
            "performanceImprovementVerified": improving,
            "performanceTrendStatus": "improving" if improving else "declining",
            "ratingDelta": 12 if improving else -98,
        },
    }


def autoresearch(
    *,
    latest_at: str = "2026-05-08T00:00:00+00:00",
    latest_id: str = "battle-gen9ou-post",
    issue_id: str | None = None,
    issue_present: bool = True,
    shift_direction: str = "worse",
):
    issue_id = issue_id or latest_id
    issue = {
        "key": "hazard_pressure",
        "title": "Hazard pressure is being lost",
        "proof": [f"{issue_id}: bot never established Stealth Rock"],
    }
    issues = [issue] if issue_present else []
    return {
        "generated_at": latest_at,
        "batch": {
            "id": f"batch-1-{latest_id}",
            "start_battle_id": latest_id,
            "end_battle_id": latest_id,
            "start_timestamp": latest_at,
            "end_timestamp": latest_at,
        },
        "top_issue": issue if issue_present else None,
        "issues": issues,
        "regression": {
            "issue_compare": {
                "shifts": [
                    {
                        "key": "hazard_pressure",
                        "title": "Hazard pressure is being lost",
                        "previous_count": 1,
                        "current_count": 0 if shift_direction == "better" else 1,
                        "delta": -1 if shift_direction == "better" else 1,
                        "direction": shift_direction,
                    }
                ]
            }
        },
    }


def test_awaits_battle_when_latest_proof_predates_packet():
    report = evaluator.build_report(
        packet=packet(),
        elo_proof=elo_proof(),
        autoresearch=autoresearch(latest_id="battle-gen9ou-2602394852"),
    )

    assert report["status"] == "awaiting-post-packet-battle-proof"
    assert report["actionablePostPacketEval"] is False
    assert report["proofWindow"]["latestBattleAfterPacket"] is False
    assert "latest ELO proof does not include a battle after the packet" in report["blockers"][0]


def test_post_packet_unresolved_issue_is_actionable_with_fresh_evidence():
    latest_at = "2026-05-08T00:00:00+00:00"
    latest_id = "battle-gen9ou-post"
    report = evaluator.build_report(
        packet=packet(status="implemented"),
        elo_proof=elo_proof(latest_at=latest_at, latest_id=latest_id),
        autoresearch=autoresearch(latest_at="2026-05-08T00:01:00+00:00", latest_id=latest_id),
    )

    assert report["status"] == "post-packet-eval-actionable-unresolved"
    assert report["actionablePostPacketEval"] is True
    assert report["failureClass"]["status"] == "unresolved-with-fresh-evidence"
    assert latest_id in report["failureClass"]["evidence"][0]


def test_post_packet_reduced_failure_class_counts_as_improving():
    latest_at = "2026-05-08T00:00:00+00:00"
    latest_id = "battle-gen9ou-post"
    report = evaluator.build_report(
        packet=packet(status="implemented"),
        elo_proof=elo_proof(latest_at=latest_at, latest_id=latest_id, improving=True),
        autoresearch=autoresearch(
            latest_at="2026-05-08T00:01:00+00:00",
            latest_id=latest_id,
            issue_present=False,
            shift_direction="better",
        ),
    )

    assert report["status"] == "post-packet-eval-improving"
    assert report["actionablePostPacketEval"] is True
    assert report["failureClass"]["status"] == "reduced"


def test_post_packet_reduced_failure_class_accepts_after_preservation_battle():
    first_at = "2026-05-08T00:00:00+00:00"
    latest_at = "2026-05-08T00:10:00+00:00"
    latest_id = "battle-gen9ou-post-2"
    proof = elo_proof(latest_at=latest_at, latest_id=latest_id, improving=True)
    proof["games"].insert(
        0,
        {
            "battleId": "battle-gen9ou-post-1",
            "timestamp": first_at,
            "result": "win",
            "ratingAfter": 1090,
        },
    )

    report = evaluator.build_report(
        packet=packet(status="implemented"),
        elo_proof=proof,
        autoresearch=autoresearch(
            latest_at="2026-05-08T00:11:00+00:00",
            latest_id=latest_id,
            issue_present=False,
            shift_direction="better",
        ),
    )

    assert report["status"] == "post-packet-eval-accepted"
    assert report["actionablePostPacketEval"] is True
    assert report["proofWindow"]["postPacketBattleCount"] == 2
    assert report["proofWindow"]["preservationSatisfied"] is True
    assert "next highest-ranked" in report["nextActions"][0]


def test_post_packet_accepts_live_profile_rating_gain_when_battle_rows_lack_delta():
    first_at = "2026-05-08T00:00:00+00:00"
    latest_at = "2026-05-08T00:10:00+00:00"
    latest_id = "battle-gen9ou-post-2"
    proof = elo_proof(latest_at=latest_at, latest_id=latest_id, improving=False)
    proof["summary"].update(
        {
            "performanceImprovementVerified": False,
            "performanceTrendStatus": "flat",
            "ratingDelta": None,
            "finalRating": 1153,
            "currentRating": 1197.25,
            "currentRatingSource": "pokemonshowdown-user-api",
            "liveProfileRating": 1197.25,
        }
    )
    proof["games"].insert(
        0,
        {
            "battleId": "battle-gen9ou-post-1",
            "timestamp": first_at,
            "result": "win",
            "ratingAfter": 1153,
        },
    )

    report = evaluator.build_report(
        packet=packet(status="implemented"),
        elo_proof=proof,
        autoresearch=autoresearch(
            latest_at="2026-05-08T00:11:00+00:00",
            latest_id=latest_id,
            issue_present=False,
            shift_direction="better",
        ),
    )

    assert report["status"] == "post-packet-eval-accepted"
    assert report["latestBattle"]["performanceImprovementVerified"] is True
    assert report["latestBattle"]["ratingDelta"] == 44.25
    assert report["latestBattle"]["ratingDeltaSource"] == "summary.currentRating-minus-summary.finalRating"
    assert report["latestBattle"]["currentRating"] == 1197.25


def test_post_packet_preservation_rejects_target_evidence_from_any_post_packet_battle():
    first_id = "battle-gen9ou-post-1"
    latest_id = "battle-gen9ou-post-2"
    proof = elo_proof(latest_at="2026-05-08T00:10:00+00:00", latest_id=latest_id, improving=True)
    proof["games"].insert(
        0,
        {
            "battleId": first_id,
            "timestamp": "2026-05-08T00:00:00+00:00",
            "result": "win",
            "ratingAfter": 1090,
        },
    )

    report = evaluator.build_report(
        packet=packet(status="implemented"),
        elo_proof=proof,
        autoresearch=autoresearch(
            latest_at="2026-05-08T00:11:00+00:00",
            latest_id=latest_id,
            issue_id=first_id,
            issue_present=True,
            shift_direction="worse",
        ),
    )

    assert report["status"] == "post-packet-eval-improving"
    assert report["failureClass"]["status"] == "reduced"
    assert report["proofWindow"]["postPacketFailureEvidenceBattleIds"] == [first_id]
    assert report["proofWindow"]["preservationSatisfied"] is False


def test_post_packet_aggregate_improvement_without_targeted_reduction_is_not_closure():
    latest_at = "2026-05-08T00:00:00+00:00"
    latest_id = "battle-gen9ou-post"
    report = evaluator.build_report(
        packet=packet(status="implemented"),
        elo_proof=elo_proof(latest_at=latest_at, latest_id=latest_id, improving=True),
        autoresearch=autoresearch(
            latest_at="2026-05-08T00:01:00+00:00",
            latest_id=latest_id,
            issue_present=True,
            shift_direction="worse",
        ),
    )

    assert report["status"] == "post-packet-eval-actionable-unresolved"
    assert report["actionablePostPacketEval"] is True
    assert report["latestBattle"]["performanceImprovementVerified"] is True
    assert report["failureClass"]["status"] == "unresolved-with-fresh-evidence"
    assert "packet failure class is still present" in report["warnings"][0]


def test_stale_window_issue_evidence_does_not_block_fresh_improving_battle():
    latest_at = "2026-05-08T00:00:00+00:00"
    latest_id = "battle-gen9ou-post"
    old_issue_id = "battle-gen9ou-old"
    report = evaluator.build_report(
        packet=packet(status="implemented"),
        elo_proof=elo_proof(latest_at=latest_at, latest_id=latest_id, improving=True),
        autoresearch=autoresearch(
            latest_at="2026-05-08T00:01:00+00:00",
            latest_id=latest_id,
            issue_id=old_issue_id,
            issue_present=True,
            shift_direction="worse",
        ),
    )

    assert report["status"] == "post-packet-eval-improving"
    assert report["actionablePostPacketEval"] is True
    assert report["failureClass"]["status"] == "reduced"
    assert report["failureClass"]["freshEvidence"] == []
    assert old_issue_id in report["failureClass"]["staleEvidenceBattleIds"]
    assert "pre-packet evidence" in report["warnings"][0]


def test_reduced_target_without_performance_gain_points_to_current_top_issue():
    latest_at = "2026-05-08T00:00:00+00:00"
    latest_id = "battle-gen9ou-post"
    current_autoresearch = autoresearch(
        latest_at="2026-05-08T00:01:00+00:00",
        latest_id=latest_id,
        issue_present=False,
        shift_direction="better",
    )
    current_autoresearch["top_issue"] = {
        "key": "endgame_conversion",
        "title": "Long games are not being converted cleanly",
        "recommendation": "Build the endgame conversion packet.",
        "proof": ["battle-gen9ou-long: loss lasted 65 turns"],
    }
    current_autoresearch["issues"] = [current_autoresearch["top_issue"]]

    report = evaluator.build_report(
        packet=packet(status="implemented"),
        elo_proof=elo_proof(latest_at=latest_at, latest_id=latest_id, improving=False),
        autoresearch=current_autoresearch,
    )

    assert report["status"] == "post-packet-eval-actionable-unresolved"
    assert report["failureClass"]["status"] == "reduced"
    assert report["nextIssue"]["key"] == "endgame_conversion"
    assert "Build the endgame conversion packet" in report["nextActions"][0]


def test_waits_for_autoresearch_when_post_packet_battle_is_not_consumed():
    latest_at = "2026-05-08T00:00:00+00:00"
    report = evaluator.build_report(
        packet=packet(status="implemented"),
        elo_proof=elo_proof(latest_at=latest_at, latest_id="battle-gen9ou-post"),
        autoresearch=autoresearch(
            latest_at="2026-05-07T00:00:00+00:00",
            latest_id="battle-gen9ou-old",
        ),
    )

    assert report["status"] == "awaiting-post-packet-autoresearch"
    assert report["actionablePostPacketEval"] is False
    assert report["proofWindow"]["autoresearchCoversLatestBattle"] is False


def test_packet_evidence_integrity_blocks_post_packet_success():
    bad_packet = packet(status="implemented")
    bad_packet["evidence_integrity"] = {
        "ok": False,
        "blockers": ["finding evidence is not linked to any Showdown battle id"],
    }

    report = evaluator.build_report(
        packet=bad_packet,
        elo_proof=elo_proof(
            latest_at="2026-05-08T00:00:00+00:00",
            latest_id="battle-gen9ou-post",
            improving=True,
        ),
        autoresearch=autoresearch(
            latest_at="2026-05-08T00:01:00+00:00",
            latest_id="battle-gen9ou-post",
            issue_present=False,
            shift_direction="better",
        ),
    )

    assert report["status"] == "packet-evidence-integrity-blocked"
    assert report["actionablePostPacketEval"] is False
    assert "not linked to any Showdown battle id" in report["blockers"][0]
