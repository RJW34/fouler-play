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
    issue_present: bool = True,
    shift_direction: str = "worse",
):
    issue = {
        "key": "hazard_pressure",
        "title": "Hazard pressure is being lost",
        "proof": [f"{latest_id}: bot never established Stealth Rock"],
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
        elo_proof=elo_proof(latest_at=latest_at, latest_id=latest_id),
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


def test_latest_battle_uses_per_battle_rating_delta_not_summary_aggregate():
    proof = elo_proof(
        latest_at="2026-05-08T00:00:00+00:00",
        latest_id="battle-gen9ou-long",
        improving=True,
    )
    proof["summary"]["ratingDelta"] = -132
    proof["summary"]["performanceImprovementVerified"] = True
    proof["games"] = [
        {
            "battleId": "battle-gen9ou-other",
            "timestamp": "2026-05-08T00:00:01+00:00",
            "ratingBefore": 1201,
            "ratingAfter": 1218,
        },
        {
            "battleId": "battle-gen9ou-long",
            "timestamp": "2026-05-08T00:00:00+00:00",
            "ratingBefore": 1218,
            "ratingAfter": 1201,
        },
    ]

    latest = evaluator.latest_battle_from_elo(proof)

    assert latest["id"] == "battle-gen9ou-long"
    assert latest["ratingDelta"] == -17
    assert latest["ratingDeltaSource"] == "latest-game-before-after"
    assert latest["performanceImprovementVerified"] is False


def test_latest_battle_omits_rating_delta_without_per_battle_before_after():
    proof = elo_proof(
        latest_at="2026-05-08T00:00:00+00:00",
        latest_id="battle-gen9ou-long",
    )
    proof["summary"]["ratingDelta"] = -132

    latest = evaluator.latest_battle_from_elo(proof)

    assert latest["ratingDelta"] is None
    assert latest["ratingDeltaSource"] == "missing-per-battle-rating-proof"


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
