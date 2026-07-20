import json
import hashlib
import os
import re
import subprocess
import sys
from datetime import datetime
from pathlib import Path

import pytest

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


def test_autoresearch_does_not_call_repeated_recovery_instability(tmp_path: Path):
    researcher = AutoResearcher(project_root=tmp_path)
    traces = [
        {"turn": turn, "choice": "roost", "reason": "mcts"}
        for turn in (13, 14, 15, 18, 19, 20)
    ]

    findings, _ = researcher._trace_issue(traces)

    assert findings == []


def test_autoresearch_flags_failed_consecutive_protect_from_replay(tmp_path: Path):
    project = tmp_path
    write_json(
        project / "battle_stats.json",
        {
            "battles": [
                {
                    "battle_id": "battle-gen9ou-protect-sequence",
                    "timestamp": "2026-07-14T12:00:00+00:00",
                    "team_file": "fat-team-2-balance",
                    "result": "loss",
                    "replay_id": "battle-gen9ou-protect-sequence",
                }
            ]
        },
    )
    write_json(
        project / "replay_analysis" / "gen9ou-protect-sequence.json",
        {
            "log": "\n".join(
                [
                    "|player|p1|ALL CHUNG|",
                    "|player|p2|Opponent|",
                    "|turn|16",
                    "|move|p1a: Garganacl|Protect|p1a: Garganacl",
                    "|turn|17",
                    "|move|p1a: Garganacl|Protect||[still]",
                    "|-fail|p1a: Garganacl",
                    "|turn|18",
                    "|move|p1a: Garganacl|Protect|p1a: Garganacl",
                    "|turn|19",
                    "|move|p1a: Garganacl|Protect||[still]",
                    "|-fail|p1a: Garganacl",
                    "|win|Opponent",
                ]
            )
        },
    )

    report = AutoResearcher(project_root=project).analyze(last_n=5)

    issue = next(item for item in report["issues"] if item["key"] == "protect_sequence_waste")
    assert issue["title"] == "Failed consecutive Protect attempts are wasting turns"
    assert "2 consecutive Protect-family attempt(s) failed" in issue["proof"][0]
    assert "turn 17" in issue["proof"][0]
    assert "turn 19" in issue["proof"][0]
    assert "decision_instability" not in [item["key"] for item in report["issues"]]


def test_autoresearch_does_not_bridge_protect_sequence_across_switch():
    lines = [
        "|turn|1",
        "|move|p1a: Garganacl|Protect|p1a: Garganacl",
        "|turn|2",
        "|switch|p1a: Blissey|Blissey, F|100/100",
        "|turn|3",
        "|switch|p1a: Garganacl|Garganacl|100/100",
        "|turn|4",
        "|move|p1a: Garganacl|Protect||[still]",
        "|-fail|p1a: Garganacl",
    ]

    assert AutoResearcher._protect_sequence_issue(lines, "p1") == (False, "")


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


def _preamble(bot_size: int = 6, opp_size: int = 6) -> list[str]:
    return [
        "|player|p1|ALL CHUNG|",
        "|player|p2|Endgamer|",
        f"|teamsize|p1|{bot_size}",
        f"|teamsize|p2|{opp_size}",
        "|start|",
        "|switch|p1a: Dondozo|Dondozo, M|100/100",
        "|switch|p2a: Gholdengo|Gholdengo|100/100",
    ]


def test_endgame_conversion_fires_when_a_material_lead_is_lost(tmp_path: Path):
    """The bot goes 6-4 up on Pokemon, then loses from there."""
    project = tmp_path
    write_json(
        project / "battle_stats.json",
        {
            "battles": [
                {
                    "battle_id": "battle-gen9ou-9",
                    "timestamp": "2026-03-10T12:00:00+00:00",
                    "team_file": "fat-team-3-dondozo",
                    "result": "loss",
                    "replay_id": "battle-gen9ou-9",
                }
            ]
        },
    )
    log = [
        *_preamble(),
        "|turn|1",
        "|-damage|p2a: Gholdengo|0 fnt",
        "|faint|p2a: Gholdengo",
        "|switch|p2a: Landorus|Landorus-Therian, M|100/100",
        "|turn|9",
        "|-damage|p2a: Landorus|0 fnt",
        "|faint|p2a: Landorus",
        "|switch|p2a: Kingambit|Kingambit, M|100/100",
        # Peak: bot 6 alive, opponent 4 alive.
        "|turn|18",
        # Collapse from a winning position.
        *[
            line
            for n, mon in enumerate(
                ["Dondozo", "Clodsire", "Corviknight", "Blissey", "Garganacl", "Alomomola"]
            )
            for line in (
                f"|switch|p1a: {mon}|{mon}, M|100/100",
                f"|-damage|p1a: {mon}|0 fnt",
                f"|faint|p1a: {mon}",
                f"|turn|{20 + n * 4}",
            )
        ],
        "|win|Endgamer",
    ]
    write_json(project / "replay_analysis" / "gen9ou-9.json", {"id": "gen9ou-9", "log": "\n".join(log)})

    report = AutoResearcher(project_root=project).analyze(last_n=5)

    issues = {issue["key"]: issue for issue in report["issues"]}
    assert "endgame_conversion" in issues
    proof = " ".join(issues["endgame_conversion"]["proof"])
    # Evidence must name the actual position, not a clock reading.
    assert "led 6-4 on Pokemon at turn 18" in proof
    assert "still lost" in proof


def test_endgame_conversion_ignores_long_losses_that_were_never_winning(tmp_path: Path):
    """The regression that motivated this detector.

    Fouler runs fat-team-1-stall and fat-team-3-dondozo, which produce 40-90
    turn games by design. A turn-count threshold fired on essentially every
    stall loss and ranked #1 on frequency alone. A long loss the bot was behind
    in throughout is a matchup problem, not a conversion failure.
    """
    project = tmp_path
    write_json(
        project / "battle_stats.json",
        {
            "battles": [
                {
                    "battle_id": "battle-gen9ou-10",
                    "timestamp": "2026-03-10T12:00:00+00:00",
                    "team_file": "fat-team-1-stall",
                    "result": "loss",
                    "replay_id": "battle-gen9ou-10",
                }
            ]
        },
    )
    log = [*_preamble()]
    # Bot bleeds first and stays behind for a 60-turn grind.
    for n, mon in enumerate(["Dondozo", "Clodsire", "Corviknight", "Blissey"]):
        log += [
            f"|switch|p1a: {mon}|{mon}, M|100/100",
            f"|-damage|p1a: {mon}|0 fnt",
            f"|faint|p1a: {mon}",
            f"|turn|{10 + n * 12}",
        ]
    log += ["|turn|60", "|win|Endgamer"]
    write_json(project / "replay_analysis" / "gen9ou-10.json", {"id": "gen9ou-10", "log": "\n".join(log)})

    report = AutoResearcher(project_root=project).analyze(last_n=5)

    issue_keys = [issue["key"] for issue in report["issues"]]
    assert "endgame_conversion" not in issue_keys


def test_endgame_conversion_ignores_a_log_with_no_gameplay(tmp_path: Path):
    """A log of bare turn markers contains no Pokemon mechanics at all.

    The previous detector flagged this, which is the clearest demonstration
    that it was reading a clock rather than the game.
    """
    project = tmp_path
    write_json(
        project / "battle_stats.json",
        {
            "battles": [
                {
                    "battle_id": "battle-gen9ou-11",
                    "timestamp": "2026-03-10T12:00:00+00:00",
                    "team_file": "fat-team-3-dondozo",
                    "result": "loss",
                    "replay_id": "battle-gen9ou-11",
                }
            ]
        },
    )
    log = [
        "|player|p1|ALL CHUNG|",
        "|player|p2|Endgamer|",
        *[f"|turn|{n}" for n in range(1, 41)],
        "|win|Endgamer",
    ]
    write_json(project / "replay_analysis" / "gen9ou-11.json", {"id": "gen9ou-11", "log": "\n".join(log)})

    report = AutoResearcher(project_root=project).analyze(last_n=5)

    issue_keys = [issue["key"] for issue in report["issues"]]
    assert "endgame_conversion" not in issue_keys


def test_endgame_conversion_requires_a_loss(tmp_path: Path):
    battle = {"result": "win"}
    lines = [*_preamble(), "|turn|20", "|faint|p2a: Gholdengo", "|faint|p2a: Landorus"]
    fired, _ = AutoResearcher._endgame_conversion_issue(battle, lines, "p1")
    assert fired is False


def test_endgame_conversion_narrow_lead_needs_a_real_hp_edge():
    """One Pokemon up only counts as winning when backed by a team-HP edge."""
    base = [
        *_preamble(),
        "|switch|p2a: Kingambit|Kingambit, M|100/100",
        "|faint|p2a: Gholdengo",
    ]

    # 6-5 up, and the opponent's revealed mon is nearly dead -> real edge.
    strong = [*base, "|-damage|p2a: Kingambit|20/100", "|turn|20", "|win|Endgamer"]
    fired, detail = AutoResearcher._endgame_conversion_issue({"result": "loss"}, strong, "p1")
    assert fired is True
    assert "led 6-5 on Pokemon at turn 20" in detail

    # 6-5 up but everything else healthy -> not a decisive position.
    thin = [*base, "|-damage|p2a: Kingambit|98/100", "|turn|20", "|win|Endgamer"]
    fired, _ = AutoResearcher._endgame_conversion_issue({"result": "loss"}, thin, "p1")
    assert fired is False


def test_endgame_conversion_analyzes_the_side_that_actually_lost():
    """A mis-resolved slot must never become a fabricated "we were winning" claim.

    _parse_bot_slot falls back to "p1" when the account lookup fails, and the
    bot plays p1 and p2 at roughly even rates. Reading the wrong side turns the
    OPPONENT's winning position into a confident, exactly-backwards finding, so
    the detector trusts the replay's own |win| line over the passed-in slot.
    """
    # p1 crushes p2; the bot is p2 and lost. Caller wrongly says "p1".
    lines = [
        "|player|p1|Opponent|",
        "|player|p2|DekuFoulerFresh|",
        "|teamsize|p1|6",
        "|teamsize|p2|6",
        "|switch|p1a: Kingambit|Kingambit, M|100/100",
        "|switch|p2a: Dondozo|Dondozo, M|100/100",
        "|faint|p2a: Dondozo",
        "|faint|p2a: Clodsire",
        "|turn|20",
        "|win|Opponent",
    ]
    fired, detail = AutoResearcher._endgame_conversion_issue({"result": "loss"}, lines, "p1")
    assert fired is False, f"claimed a blown lead for the winning side: {detail}"

    assert AutoResearcher._winning_slot(lines) == "p1"
    assert AutoResearcher._losing_slot(lines, "p1") == "p2"


def test_endgame_conversion_declines_when_the_replay_has_no_verdict():
    """No |win| line means the losing side cannot be verified -- make no claim."""
    lines = [
        "|player|p1|ALL CHUNG|",
        "|player|p2|Endgamer|",
        "|teamsize|p1|6",
        "|teamsize|p2|6",
        "|faint|p2a: Gholdengo",
        "|faint|p2a: Landorus",
        "|turn|20",
    ]
    assert AutoResearcher._losing_slot(lines, "p1") is None
    fired, _ = AutoResearcher._endgame_conversion_issue({"result": "loss"}, lines, "p1")
    assert fired is False


def test_endgame_conversion_requires_hp_edge_not_just_bodies():
    """Three near-dead Pokemon do not beat one healthy one."""
    # Real shape (mirrors replay gen9ou-2651205354): deep into a game both
    # sides are mostly revealed. The bot has three Pokemon left but all are
    # nearly dead; the opponent has one at full health.
    lines = [
        *_preamble(),
        "|switch|p2a: Kingambit|Kingambit, M|100/100",
        # Opponent is down to a single healthy Pokemon.
        *[f"|faint|p2a: {mon}" for mon in ("Gholdengo", "Landorus", "Dragapult", "Zamazenta", "Ogerpon")],
        # Bot has three left, all chipped to nothing.
        *[f"|faint|p1a: {mon}" for mon in ("Dondozo", "Clodsire", "Corviknight")],
        *[
            line
            for mon in ("Blissey", "Garganacl", "Alomomola")
            for line in (
                f"|switch|p1a: {mon}|{mon}, F|100/100",
                f"|-damage|p1a: {mon}|8/100",
            )
        ],
        "|turn|20",
        "|win|Endgamer",
    ]
    timeline = AutoResearcher._material_timeline(lines, "p1")
    row = [r for r in timeline if r["turn"] == 20][0]
    # Bodies favour the bot; HP does not.
    assert row["our_alive"] > row["opp_alive"]
    assert row["our_hp"] < row["opp_hp"]

    fired, _ = AutoResearcher._endgame_conversion_issue({"result": "loss"}, lines, "p1")
    assert fired is False


def test_material_timeline_tracks_alive_counts_and_hp_from_protocol():
    lines = [
        *_preamble(),
        "|turn|1",
        "|-damage|p2a: Gholdengo|50/100",
        "|turn|2",
        "|faint|p2a: Gholdengo",
        "|switch|p2a: Landorus|Landorus-Therian, M|100/100",
        "|turn|3",
    ]
    timeline = AutoResearcher._material_timeline(lines, "p1")
    by_turn = {row["turn"]: row for row in timeline}

    assert by_turn[1]["our_alive"] == 6 and by_turn[1]["opp_alive"] == 6
    # Opponent's revealed mon at 50% costs half a slot of a 6-mon team.
    assert by_turn[2]["opp_hp"] == pytest.approx((5 * 1.0 + 0.5) / 6)
    assert by_turn[3]["opp_alive"] == 5
    assert by_turn[3]["our_alive"] == 6
    assert by_turn[3]["our_hp"] == pytest.approx(1.0)


@pytest.mark.parametrize(
    "token,expected",
    [
        ("100/100", 1.0),
        ("43/100", 0.43),
        ("0 fnt", 0.0),
        ("0", 0.0),
        ("100/100 brn", 1.0),
        ("250\\/300", pytest.approx(250 / 300)),
        ("", None),
        ("garbage", None),
        ("50/0", None),
    ],
)
def test_parse_hp_fraction(token, expected):
    assert AutoResearcher._parse_hp_fraction(token) == expected


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
