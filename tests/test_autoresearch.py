import json
import hashlib
import os
import re
import subprocess
import sys
from datetime import datetime
from pathlib import Path

from replay_analysis.autoresearch import AutoResearcher


def write_json(path: Path, payload: dict):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload), encoding="utf-8")


def test_autoresearch_detects_hazard_pressure_and_trace_instability(tmp_path: Path):
    project = tmp_path
    replay_dir = project / "replay_analysis"
    trace_dir = project / "logs" / "decision_traces"

    battles = {
        "battles": [
            {
                "battle_id": "battle-gen9ou-1",
                "timestamp": "2026-03-10T10:00:00+00:00",
                "team_file": "fat-team-1-stall",
                "result": "loss",
                "replay_id": "battle-gen9ou-1",
            },
            {
                "battle_id": "battle-gen9ou-2",
                "timestamp": "2026-03-10T10:10:00+00:00",
                "team_file": "fat-team-1-stall",
                "result": "loss",
                "replay_id": "battle-gen9ou-2",
            },
            {
                "battle_id": "battle-gen9ou-3",
                "timestamp": "2026-03-10T10:20:00+00:00",
                "team_file": "fat-team-2-pivot",
                "result": "win",
                "replay_id": "battle-gen9ou-3",
            },
        ]
    }
    write_json(project / "battle_stats.json", battles)

    replay_payload = {
        "id": "gen9ou-1",
        "log": "\n".join([
            "|player|p1|ALL CHUNG|",
            "|player|p2|Opponent|",
            "|poke|p1|Gliscor, M",
            "|poke|p2|Gholdengo, M",
            "|turn|1",
            "|-sidestart|p1: ALL CHUNG|move: Stealth Rock",
            "|turn|2",
            "|faint|p1a: Gliscor",
            "|turn|4",
            "|faint|p1a: Blissey",
            "|win|Opponent",
        ])
    }
    replay_payload_2 = {
        "id": "gen9ou-2",
        "log": "\n".join([
            "|player|p1|ALL CHUNG|",
            "|player|p2|Opponent2|",
            "|poke|p1|Dondozo, M",
            "|poke|p2|Samurott-Hisui, M",
            "|turn|1",
            "|-sidestart|p1: ALL CHUNG|move: Stealth Rock",
            "|turn|3",
            "|faint|p1a: Dondozo",
            "|turn|6",
            "|faint|p1a: Alomomola",
            "|win|Opponent2",
        ])
    }
    write_json(replay_dir / "gen9ou-1.json", replay_payload)
    write_json(replay_dir / "gen9ou-2.json", replay_payload_2)

    trace_payload = {
        "battle_tag": "battle-gen9ou-1",
        "turn": 5,
        "reason": "timeout",
        "choice": "recover",
        "snapshot": {"user": {"active": {"moves": [{"id": "recover", "disabled": False}]}}},
    }
    write_json(trace_dir / "battle-gen9ou-1_turn5_1.json", trace_payload)
    write_json(trace_dir / "battle-gen9ou-1_turn6_2.json", trace_payload)
    write_json(trace_dir / "battle-gen9ou-1_turn7_3.json", trace_payload)

    researcher = AutoResearcher(project_root=project)
    report = researcher.analyze(last_n=10)

    assert report["losses"] == 2
    assert report["top_issue"]["key"] == "hazard_pressure"
    issue_keys = [issue["key"] for issue in report["issues"]]
    assert "decision_instability" in issue_keys
    markdown = researcher.render_markdown(report)
    assert "Top issue" in markdown
    assert "Next action" in markdown


def test_autoresearch_does_not_flag_missing_hazards_when_team_has_no_setter(tmp_path: Path):
    project = tmp_path
    replay_dir = project / "replay_analysis"
    team_dir = project / "teams" / "gen9" / "ou"
    team_dir.mkdir(parents=True)
    (team_dir / "no-setter-control").write_text(
        "\n".join(
            [
                "Corviknight @ Leftovers",
                "Ability: Pressure",
                "- Brave Bird",
                "- Defog",
                "- Roost",
                "- U-turn",
                "",
                "Cinderace @ Heavy-Duty Boots",
                "Ability: Libero",
                "- Pyro Ball",
                "- Court Change",
                "- U-turn",
                "- Will-O-Wisp",
                "",
            ]
        ),
        encoding="utf-8",
    )
    write_json(
        project / "battle_stats.json",
        {
            "battles": [
                {
                    "battle_id": "battle-gen9ou-no-setter-clean",
                    "timestamp": "2026-03-10T10:30:00+00:00",
                    "team_file": "no-setter-control",
                    "result": "loss",
                    "replay_id": "battle-gen9ou-no-setter-clean",
                }
            ]
        },
    )
    write_json(
        replay_dir / "gen9ou-no-setter-clean.json",
        {
            "log": "\n".join(
                [
                    "|player|p1|ALL CHUNG|",
                    "|player|p2|Opponent|",
                    "|turn|1",
                    "|move|p1a: Corviknight|U-turn|p2a: Great Tusk",
                    "|win|Opponent",
                ]
            )
        },
    )

    report = AutoResearcher(project_root=project).analyze(last_n=5)

    assert "hazard_pressure" not in [issue["key"] for issue in report["issues"]]


def test_autoresearch_flags_no_setter_team_when_hazard_control_is_never_used(tmp_path: Path):
    project = tmp_path
    replay_dir = project / "replay_analysis"
    team_dir = project / "teams" / "gen9" / "ou"
    team_dir.mkdir(parents=True)
    (team_dir / "no-setter-control").write_text(
        "\n".join(
            [
                "Corviknight @ Leftovers",
                "Ability: Pressure",
                "- Brave Bird",
                "- Defog",
                "- Roost",
                "- U-turn",
                "",
                "Cinderace @ Heavy-Duty Boots",
                "Ability: Libero",
                "- Pyro Ball",
                "- Court Change",
                "- U-turn",
                "- Will-O-Wisp",
                "",
            ]
        ),
        encoding="utf-8",
    )
    write_json(
        project / "battle_stats.json",
        {
            "battles": [
                {
                    "battle_id": "battle-gen9ou-no-setter-hazards",
                    "timestamp": "2026-03-10T10:40:00+00:00",
                    "team_file": "no-setter-control",
                    "result": "loss",
                    "replay_id": "battle-gen9ou-no-setter-hazards",
                }
            ]
        },
    )
    write_json(
        replay_dir / "gen9ou-no-setter-hazards.json",
        {
            "log": "\n".join(
                [
                    "|player|p1|ALL CHUNG|",
                    "|player|p2|Opponent|",
                    "|turn|1",
                    "|-sidestart|p1: ALL CHUNG|move: Stealth Rock",
                    "|turn|2",
                    "|move|p1a: Corviknight|U-turn|p2a: Great Tusk",
                    "|win|Opponent",
                ]
            )
        },
    )

    report = AutoResearcher(project_root=project).analyze(last_n=5)

    hazard = next(issue for issue in report["issues"] if issue["key"] == "hazard_pressure")
    assert "no hazard setter" in hazard["proof"][0]
    assert "Defog" in hazard["proof"][0]
    assert "Court Change" in hazard["proof"][0]


def test_autoresearch_decision_trace_proves_request_legal_options(tmp_path: Path):
    project = tmp_path
    trace_dir = project / "logs" / "decision_traces"
    write_json(
        project / "battle_stats.json",
        {
            "battles": [
                {
                    "battle_id": "battle-gen9ou-legalproof",
                    "timestamp": "2026-03-10T11:00:00+00:00",
                    "team_file": "fat-team-1-stall",
                    "result": "loss",
                    "replay_id": "battle-gen9ou-legalproof",
                }
            ]
        },
    )

    trace_payload = {
        "battle_tag": "battle-gen9ou-legalproof",
        "turn": 7,
        "reason": "timeout",
        "choice": "recover",
        "legalOptions": {
            "source": "showdown-request",
            "requestHash": "a" * 64,
            "candidateSetBounded": True,
            "legalMoves": [{"activeSlot": 0, "id": "recover", "target": "self"}],
            "legalSwitches": [],
        },
    }
    write_json(trace_dir / "battle-gen9ou-legalproof_turn7_1.json", trace_payload)
    write_json(trace_dir / "battle-gen9ou-legalproof_turn8_2.json", trace_payload)
    write_json(trace_dir / "battle-gen9ou-legalproof_turn9_3.json", trace_payload)

    report = AutoResearcher(project_root=project).analyze(last_n=5)

    integrity = report["evidence_integrity"]
    assert integrity["losses_with_decision_trace"] == 1
    assert integrity["losses_with_request_legal_options"] == 1
    assert integrity["claims_without_evidence"] == []
    decision = next(issue for issue in report["issues"] if issue["key"] == "decision_instability")
    proof = "\n".join(decision["proof"])
    assert "requestHash=" in proof
    assert "legalMoves=1" in proof
    assert "traceSha256=" in proof
    assert datetime.fromisoformat(report["generated_at"]).tzinfo is not None
    trace_match = re.search(r"trace=(\S+)\s+traceSha256=([a-f0-9]{64})", proof)
    assert trace_match
    trace_path = project / trace_match.group(1)
    assert "replay_analysis/evidence_traces" in trace_match.group(1)
    assert trace_path.exists()
    assert hashlib.sha256(trace_path.read_bytes()).hexdigest() == trace_match.group(2)


def test_autoresearch_does_not_treat_legal_option_trace_as_instability(tmp_path: Path):
    project = tmp_path
    trace_dir = project / "logs" / "decision_traces"
    write_json(
        project / "battle_stats.json",
        {
            "battles": [
                {
                    "battle_id": "battle-gen9ou-grounded-normal",
                    "timestamp": "2026-03-10T11:05:00+00:00",
                    "team_file": "fat-team-1-stall",
                    "result": "loss",
                    "replay_id": "battle-gen9ou-grounded-normal",
                }
            ]
        },
    )
    trace_payload = {
        "battle_tag": "battle-gen9ou-grounded-normal",
        "turn": 34,
        "choice": "icebeam",
        "legalOptions": {
            "source": "showdown-request",
            "requestHash": "f" * 64,
            "candidateSetBounded": True,
            "legalMoves": [
                {"activeSlot": 0, "id": "futuresight", "target": "normal"},
                {"activeSlot": 0, "id": "sludgebomb", "target": "normal"},
                {"activeSlot": 0, "id": "icebeam", "target": "normal"},
                {"activeSlot": 0, "id": "chillyreception", "target": "self"},
            ],
            "legalSwitches": [],
        },
        "mcts_only": {
            "events": [
                {
                    "type": "skip",
                    "source": "decision_loop_break",
                    "move": "icebeam",
                    "reason": "icebeam_repeated_0_position_not_stagnant",
                }
            ],
            "top_moves": [{"move": "icebeam", "weight": 0.264}],
        },
    }
    write_json(trace_dir / "battle-gen9ou-grounded-normal_turn34_1.json", trace_payload)

    report = AutoResearcher(project_root=project).analyze(last_n=5)

    assert report["evidence_integrity"]["losses_with_request_legal_options"] == 1
    assert report["evidence_integrity"]["claims_without_evidence"] == []
    assert "decision_instability" not in [issue["key"] for issue in report["issues"]]


def test_autoresearch_flags_grounded_loop_breaker_override(tmp_path: Path):
    project = tmp_path
    trace_dir = project / "logs" / "decision_traces"
    write_json(
        project / "battle_stats.json",
        {
            "battles": [
                {
                    "battle_id": "battle-gen9ou-loop-break",
                    "timestamp": "2026-03-10T11:07:00+00:00",
                    "team_file": "fat-team-1-stall",
                    "result": "loss",
                    "replay_id": "battle-gen9ou-loop-break",
                }
            ]
        },
    )
    write_json(
        trace_dir / "battle-gen9ou-loop-break_turn21_1.json",
        {
            "battle_tag": "battle-gen9ou-loop-break",
            "turn": 21,
            "choice": "softboiled",
            "legalOptions": {
                "source": "showdown-request",
                "requestHash": "e" * 64,
                "candidateSetBounded": True,
                "legalMoves": [
                    {"activeSlot": 0, "id": "seismictoss", "target": "normal"},
                    {"activeSlot": 0, "id": "softboiled", "target": "self"},
                ],
                "legalSwitches": [],
            },
            "mcts_only": {
                "events": [
                    {
                        "type": "override",
                        "source": "decision_loop_break",
                        "move": "softboiled",
                        "reason": "softboiled_repeated_3_in_last_6_forcing_distinct",
                    }
                ]
            },
        },
    )

    report = AutoResearcher(project_root=project).analyze(last_n=5)

    decision = next(issue for issue in report["issues"] if issue["key"] == "decision_instability")
    proof = "\n".join(decision["proof"])
    assert "loop-breaker intervened" in proof
    assert "requestHash=" in proof


def test_autoresearch_does_not_count_wait_or_empty_force_switch_as_legal_options(tmp_path: Path):
    project = tmp_path
    trace_dir = project / "logs" / "decision_traces"
    write_json(
        project / "battle_stats.json",
        {
            "battles": [
                {
                    "battle_id": "battle-gen9ou-wait",
                    "timestamp": "2026-03-10T11:10:00+00:00",
                    "team_file": "fat-team-1-stall",
                    "result": "loss",
                    "replay_id": "battle-gen9ou-wait",
                },
                {
                    "battle_id": "battle-gen9ou-empty-force-switch",
                    "timestamp": "2026-03-10T11:20:00+00:00",
                    "team_file": "fat-team-1-stall",
                    "result": "loss",
                    "replay_id": "battle-gen9ou-empty-force-switch",
                },
            ]
        },
    )
    wait_trace = {
        "battle_tag": "battle-gen9ou-wait",
        "turn": 2,
        "reason": "wait",
        "choice": "wait",
        "legalOptions": {
            "source": "showdown-request",
            "requestHash": "b" * 64,
            "candidateSetBounded": True,
            "legalMoves": [],
            "legalSwitches": [],
            "wait": True,
        },
    }
    force_switch_trace = {
        "battle_tag": "battle-gen9ou-empty-force-switch",
        "turn": 3,
        "reason": "force switch",
        "choice": "switch",
        "legalOptions": {
            "source": "showdown-request",
            "requestHash": "c" * 64,
            "candidateSetBounded": True,
            "legalMoves": [],
            "legalSwitches": [],
            "forceSwitch": True,
        },
    }
    write_json(trace_dir / "battle-gen9ou-wait_turn2_1.json", wait_trace)
    write_json(trace_dir / "battle-gen9ou-empty-force-switch_turn3_1.json", force_switch_trace)

    report = AutoResearcher(project_root=project).analyze(last_n=5)

    assert report["evidence_integrity"]["losses_with_decision_trace"] == 2
    assert report["evidence_integrity"]["losses_with_request_legal_options"] == 0


def test_autoresearch_detects_magic_bounce_reflected_hazard_from_trace(tmp_path: Path):
    project = tmp_path
    trace_dir = project / "logs" / "decision_traces"
    write_json(
        project / "battle_stats.json",
        {
            "battles": [
                {
                    "battle_id": "battle-gen9ou-magic-bounce",
                    "timestamp": "2026-03-10T11:30:00+00:00",
                    "team_file": "fat-team-2-balance",
                    "result": "loss",
                    "replay_id": "battle-gen9ou-magic-bounce",
                }
            ]
        },
    )
    trace_payload = {
        "battle_tag": "battle-gen9ou-magic-bounce",
        "turn": 7,
        "choice": "stealthrock",
        "legalOptions": {
            "source": "showdown-request",
            "requestHash": "d" * 64,
            "candidateSetBounded": True,
            "legalMoves": [
                {"activeSlot": 0, "id": "earthquake", "target": "allAdjacent"},
                {"activeSlot": 0, "id": "stealthrock", "target": "foeSide"},
            ],
            "legalSwitches": [],
        },
        "mcts_only": {
            "selection": "deterministic_argmax",
            "top_moves": [
                {"move": "stealthrock", "weight": 0.133557},
                {"move": "earthquake", "weight": 0.032795},
            ],
            "events": [
                {
                    "type": "penalty",
                    "source": "mcts_hard_safety",
                    "move": "stealthrock",
                    "reason": "magic_bounce_reflects_status",
                    "before": 1.335569,
                    "after": 0.133557,
                }
            ],
        },
    }
    write_json(trace_dir / "battle-gen9ou-magic-bounce_turn7_1.json", trace_payload)

    report = AutoResearcher(project_root=project).analyze(last_n=5)

    issue = next(issue for issue in report["issues"] if issue["key"] == "magic_bounce_reflected_hazard")
    proof = "\n".join(issue["proof"])
    assert "selected stealthrock into Magic Bounce" in proof
    assert "best_non_reflected=earthquake" in proof
    assert "traceSha256=" in proof


def test_autoresearch_detects_long_game_conversion_issue(tmp_path: Path):
    project = tmp_path
    replay_dir = project / "replay_analysis"
    battles = {
        "battles": [
            {
                "battle_id": "battle-gen9ou-9",
                "timestamp": "2026-03-10T12:00:00+00:00",
                "team_file": "fat-team-3-dondozo",
                "result": "loss",
                "replay_id": "battle-gen9ou-9",
            }
        ]
    }
    write_json(project / "battle_stats.json", battles)
    replay_payload = {
        "id": "gen9ou-9",
        "log": "\n".join([
            "|player|p1|ALL CHUNG|",
            "|player|p2|Endgamer|",
            *[f"|turn|{n}" for n in range(1, 41)],
            "|win|Endgamer",
        ])
    }
    write_json(replay_dir / "gen9ou-9.json", replay_payload)

    researcher = AutoResearcher(project_root=project)
    report = researcher.analyze(last_n=5)

    issue_keys = [issue["key"] for issue in report["issues"]]
    assert "endgame_conversion" in issue_keys


def test_autoresearch_blocks_hazard_claims_without_replay_or_trace_evidence(tmp_path: Path):
    project = tmp_path
    battles = {
        "battles": [
            {
                "battle_id": "battle-gen9ou-missing-proof",
                "timestamp": "2026-03-10T12:10:00+00:00",
                "team_file": "fat-team-1-stall",
                "result": "loss",
                "replay_id": "battle-gen9ou-missing-proof",
            }
        ]
    }
    write_json(project / "battle_stats.json", battles)

    researcher = AutoResearcher(project_root=project)
    report = researcher.analyze(last_n=5)

    issue_keys = [issue["key"] for issue in report["issues"]]
    assert "hazard_pressure" not in issue_keys
    integrity = report["evidence_integrity"]
    assert integrity["loss_count"] == 1
    assert integrity["losses_with_replay_json"] == 0
    assert integrity["claims_without_evidence"][0]["battle_id"] == "battle-gen9ou-missing-proof"
    markdown = researcher.render_markdown(report)
    assert "Unsupported mechanics/strategy claims are blocked" in markdown


def test_autoresearch_prefers_fresher_devstream_elo_proof(tmp_path: Path):
    project = tmp_path
    write_json(
        project / "battle_stats.json",
        {
            "battles": [
                {
                    "battle_id": "battle-gen9ou-old",
                    "timestamp": "2026-03-25T14:45:21+00:00",
                    "team_file": "fat-team-1-stall",
                    "result": "win",
                    "replay_id": "battle-gen9ou-old",
                }
            ]
        },
    )
    write_json(
        project / "devstream" / "truth" / "latest-elo-proof.json",
        {
            "games": [
                {
                    "battleId": "battle-gen9ou-2602394852",
                    "timestamp": "2026-05-05T17:37:27+00:00",
                    "teamFile": "fat-team-1-stall",
                    "result": "loss",
                    "replayUrl": "https://replay.pokemonshowdown.com/gen9ou-2602394852",
                    "ratingAfter": 1038,
                }
            ]
        },
    )

    researcher = AutoResearcher(project_root=project)
    report = researcher.analyze(last_n=30)

    assert report["battle_source"] == "devstream/truth/latest-elo-proof.json"
    assert report["batch"]["end_battle_id"] == "battle-gen9ou-2602394852"
    assert report["window_size"] == 1
    assert report["losses"] == 1


def test_autoresearch_filters_marked_offline_eval_battles(tmp_path: Path):
    project = tmp_path
    write_json(
        project / "battle_stats.json",
        {
            "battles": [
                {
                    "battle_id": "battle-gen9ou-offline",
                    "timestamp": "2026-06-08T09:00:00+00:00",
                    "team_file": "fat-team-1-stall",
                    "result": "loss",
                    "replay_id": "battle-gen9ou-offline",
                    "offline_eval": True,
                },
                {
                    "battle_id": "battle-gen9ou-live",
                    "timestamp": "2026-06-08T09:10:00+00:00",
                    "team_file": "fat-team-1-stall",
                    "result": "win",
                    "replay_id": "battle-gen9ou-live",
                },
            ]
        },
    )

    researcher = AutoResearcher(project_root=project)
    battles = researcher.load_battles()
    report = researcher.analyze(last_n=30, battles=battles)

    assert [battle["battle_id"] for battle in battles] == ["battle-gen9ou-live"]
    assert report["window_size"] == 1
    assert report["losses"] == 0
    assert report["battle_source"] == "battle_stats.json"


def test_pipeline_autoresearch_accepts_no_discord_flag():
    repo = Path(__file__).resolve().parent.parent
    result = subprocess.run(
        [sys.executable, "pipeline.py", "autoresearch", "-n", "1", "--no-discord"],
        cwd=repo,
        capture_output=True,
        text=True,
        check=False,
    )

    assert result.returncode == 0, result.stderr
    payload = json.loads(result.stdout)
    assert "window_size" in payload


def test_pipeline_autoresearch_accepts_inline_comment_batch_size():
    repo = Path(__file__).resolve().parent.parent
    result = subprocess.run(
        [sys.executable, "pipeline.py", "autoresearch", "-n", "1", "--no-discord"],
        cwd=repo,
        env={**os.environ, "FOULER_BATCH_SIZE": "10  # safe smaller live cycle"},
        capture_output=True,
        text=True,
        check=False,
    )

    assert result.returncode == 0, result.stderr
    payload = json.loads(result.stdout)
    assert "window_size" in payload
