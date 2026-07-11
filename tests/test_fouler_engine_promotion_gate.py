import json
import subprocess
import sys
from pathlib import Path

from scripts import fouler_engine_promotion_gate as gate


def _packet(
    *,
    packet_id="fouler-auto-011-hazard-self-ko-switch-guard",
    status="implemented",
    created_at="2026-07-05T20:49:52+00:00",
    offline_result="18 passed; 189 passed",
):
    return {
        "schemaVersion": "devstream-work-packet/v1",
        "id": packet_id,
        "createdAt": created_at,
        "implementedAt": created_at,
        "status": status,
        "finding_key": "endgame_conversion",
        "title": "Block MCTS-only switches and pivots that faint to own-side entry hazards",
        "evidence_integrity": {
            "ok": True,
            "groundTruth": ["trace-backed battle"],
            "blockers": [],
        },
        "implementation": {
            "touchedPaths": ["fp/search/main.py", "tests/test_mcts_selection.py"],
            "offlineAcceptance": {
                "checkedAtUtc": "2026-07-05T20:52:36+00:00",
                "result": offline_result,
            },
        },
    }


def _battle_stats(*, rating_gap=False, post_packet_record=("loss", "win")):
    rows = [
        {
            "battle_id": "battle-gen9ou-before",
            "timestamp": "2026-07-05T20:40:00+00:00",
            "result": "win",
            "rating": 1271,
            "team_file": "fat-team-2-balance",
        }
    ]
    for idx, result in enumerate(post_packet_record, start=1):
        rows.append(
            {
                "battle_id": f"battle-gen9ou-post-{idx}",
                "timestamp": f"2026-07-05T21:0{idx}:00+00:00",
                "result": result,
                "rating": None if rating_gap else 1280 + idx,
                "team_file": "fat-team-1-stall",
            }
        )
    return {"battles": rows}


def _autoresearch(*, worse=True):
    return {
        "generated_at": "2026-07-05T21:10:00+00:00",
        "window_size": 30,
        "wins": 17,
        "losses": 13,
        "win_rate": 0.5667,
        "top_issue": {
            "key": "endgame_conversion",
            "title": "Long games are not being converted cleanly",
            "proof": ["battle-gen9ou-post-1: long game loss"],
        },
        "regression": {
            "issue_compare": {
                "shifts": [
                    {
                        "key": "decision_instability",
                        "title": "Decision traces show unstable fallback behavior",
                        "previous_count": 0,
                        "current_count": 2 if worse else 0,
                        "delta": 2 if worse else 0,
                        "direction": "worse" if worse else "flat",
                    }
                ]
            }
        },
    }


def _elo_proof(*, live_rating=1186.68):
    return {
        "schemaVersion": "fouler-play-elo-proof/v1",
        "summary": {
            "latestBattleId": "battle-gen9ou-post-2",
            "latestBattleAt": "2026-07-05T21:02:00+00:00",
            "finalRating": 1271,
            "currentRating": live_rating,
            "liveProfileRating": live_rating,
            "currentRatingSource": "pokemonshowdown-user-api",
            "performanceImprovementVerified": True,
            "performanceTrendStatus": "improving",
            "winRate": 0.5667,
        },
    }


def _post_packet(*, status="post-packet-eval-improving", preservation=False):
    return {
        "status": status,
        "packet": {"id": "fouler-auto-011-hazard-self-ko-switch-guard"},
        "failureClass": {"key": "endgame_conversion", "status": "reduced"},
        "proofWindow": {
            "preservationSatisfied": preservation,
            "postPacketFailureEvidenceBattleIds": [] if preservation else ["battle-gen9ou-post-1"],
        },
    }


def _trace(root, *, choice="switch slowkinggalar", best="icebeam", regret=True):
    traces = root / "replay_analysis" / "evidence_traces"
    traces.mkdir(parents=True)
    selected_weight = 0.2 if regret else 0.51
    trace = {
        "battle_tag": "battle-gen9ou-post-1",
        "turn": 12,
        "timestamp": "2026-07-05T21:01:00+00:00",
        "choice": choice,
        "mcts_only": {
            "selection": "deterministic_argmax",
            "top_moves": [
                {"move": best, "weight": 0.52},
                {"move": choice, "weight": selected_weight},
            ],
        },
    }
    (traces / "battle-gen9ou-post-1_turn12_test.json").write_text(json.dumps(trace), encoding="utf-8")


def test_gate_blocks_half_proven_packet_with_history_regressions(tmp_path):
    packet_dir = tmp_path / "devstream" / "work_packets" / "generated"
    packet_dir.mkdir(parents=True)
    (packet_dir / "fouler-auto-011-hazard-self-ko-switch-guard.json").write_text(
        json.dumps(_packet()),
        encoding="utf-8",
    )
    _trace(tmp_path, regret=True)

    history = gate.build_history(
        root=tmp_path,
        post_packet=_post_packet(),
        autoresearch=_autoresearch(worse=True),
        elo_proof=_elo_proof(live_rating=1186.68),
        battle_stats=_battle_stats(rating_gap=True),
        offline_eval={"accepted": True, "ready": True, "status": "accepted"},
        max_traces=20,
    )
    report = gate.build_gate(history)

    assert report["status"] == "promotion-blocked"
    assert report["promotionAllowed"] is False
    assert any("post-packet eval is post-packet-eval-improving" in item for item in report["blockers"])
    assert any("post-packet preservation proof is not satisfied" in item for item in report["blockers"])
    assert any("decision_instability" in item for item in report["blockers"])
    assert any("rating truth incoherent" in item for item in report["blockers"])
    assert any("high-regret" in item for item in report["blockers"])


def test_gate_allows_promotion_only_when_history_inputs_are_clean(tmp_path):
    packet_dir = tmp_path / "devstream" / "work_packets" / "generated"
    packet_dir.mkdir(parents=True)
    (packet_dir / "fouler-auto-011-hazard-self-ko-switch-guard.json").write_text(
        json.dumps(_packet()),
        encoding="utf-8",
    )
    _trace(tmp_path, choice="icebeam", best="icebeam", regret=False)

    history = gate.build_history(
        root=tmp_path,
        post_packet=_post_packet(status="post-packet-eval-accepted", preservation=True),
        autoresearch=_autoresearch(worse=False),
        elo_proof=_elo_proof(live_rating=1282),
        battle_stats=_battle_stats(rating_gap=False, post_packet_record=("win", "win")),
        offline_eval={"accepted": True, "ready": True, "status": "accepted"},
        max_traces=20,
    )
    report = gate.build_gate(history)

    assert report["status"] == "promotion-ready"
    assert report["promotionAllowed"] is True
    assert report["blockers"] == []
    assert report["history"]["lineage"]["latestPacketId"] == "fouler-auto-011-hazard-self-ko-switch-guard"
    assert report["history"]["decisionTraceHistory"]["traceFilesParsed"] == 1


def test_direct_script_resolves_offline_eval_proof_module_from_non_repo_cwd(tmp_path):
    script = Path(gate.__file__).resolve()
    completed = subprocess.run(
        [
            sys.executable,
            str(script),
            "--root",
            str(tmp_path),
            "--max-traces",
            "0",
        ],
        cwd=tmp_path,
        check=False,
        capture_output=True,
        text=True,
        timeout=30,
    )

    assert completed.returncode == 2
    payload = json.loads(completed.stdout)
    offline = payload["history"]["offlineEval"]
    reasons = "\n".join(str(item) for item in offline.get("reasons") or [])
    assert offline["status"] != "unavailable"
    assert "offline eval proof import/read failed" not in reasons
