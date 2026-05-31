import pytest

from replay_analysis.batch_analyzer import BatchAnalyzer
from replay_analysis.loss_learning import (
    LocalMechanics,
    aggregate_loss_lessons,
    build_loss_artifact,
)


def _replay(log: str, replay_id: str = "gen9ou-test") -> dict:
    return {
        "id": replay_id,
        "format": "[Gen 9] OU",
        "formatid": "gen9ou",
        "players": ["Bot", "Opp"],
        "log": log.strip(),
    }


LOSS_WITH_EARTHQUAKE_KO = """
|player|p1|Bot|1|1000
|player|p2|Opp|2|1000
|gen|9
|tier|[Gen 9] OU
|clearpoke
|poke|p1|Torkoal, M|
|poke|p1|Corviknight, F|
|poke|p2|Gliscor, F|
|poke|p2|Darkrai|
|teampreview
|start
|switch|p1a: Torkoal|Torkoal, M|100/100
|switch|p2a: Gliscor|Gliscor, F|100/100
|turn|1
|move|p2a: Gliscor|Earthquake|p1a: Torkoal
|-supereffective|p1a: Torkoal
|-damage|p1a: Torkoal|0 fnt
|faint|p1a: Torkoal
|win|Opp
"""


LOSS_WITH_SPIKES = """
|player|p1|Bot|1|1000
|player|p2|Opp|2|1000
|gen|9
|tier|[Gen 9] OU
|clearpoke
|poke|p1|Torkoal, M|
|poke|p1|Corviknight, F|
|poke|p2|Gliscor, F|
|poke|p2|Darkrai|
|teampreview
|start
|switch|p1a: Torkoal|Torkoal, M|100/100
|switch|p2a: Gliscor|Gliscor, F|100/100
|turn|1
|move|p2a: Gliscor|Spikes|p1a: Torkoal
|-sidestart|p1: Bot|Spikes
|turn|2
|switch|p1a: Corviknight|Corviknight, F|100/100
|-damage|p1a: Corviknight|88/100|[from] Spikes
|move|p2a: Gliscor|Knock Off|p1a: Corviknight
|-damage|p1a: Corviknight|0 fnt
|faint|p1a: Corviknight
|win|Opp
"""


def test_source_backed_type_weakness_from_local_chart_and_log():
    artifact = build_loss_artifact(_replay(LOSS_WITH_EARTHQUAKE_KO), bot_username="Bot")

    assert artifact["result"] == "loss"
    assert artifact["faint_turns"] == [{"turn": 1, "pokemon": "Torkoal", "side": "p1"}]
    assert artifact["key_kos"][0]["move"] == "Earthquake"

    mechanics = LocalMechanics(format_id="gen9ou")
    validation = mechanics.validate_claim(
        {"kind": "type_effectiveness", "move": "Earthquake", "target": "Torkoal", "expected": "super_effective"}
    )

    assert validation.status == "source_backed"
    assert validation.claim["multiplier"] == 2.0
    assert "data/moves.json" in " ".join(validation.sources)
    assert any(c["status"] == "source_backed" for c in artifact["mechanics_claims"])


def test_unrevealed_sets_stay_unknown_not_fact():
    artifact = build_loss_artifact(_replay(LOSS_WITH_EARTHQUAKE_KO), bot_username="Bot")

    unknowns = artifact["unresolved_unknowns"]
    assert {"kind": "unrevealed_moves", "side": "p2", "pokemon": "Darkrai", "revealed_count": 0} in unknowns
    assert {"kind": "unrevealed_item", "side": "p2", "pokemon": "Darkrai"} in unknowns
    assert "Darkrai" in artifact["teams"]["p2"]


@pytest.mark.parametrize(
    "claim",
    [
        {"kind": "type_effectiveness", "move": "Earthquake", "target": "Corviknight", "expected": "super_effective"},
        {"kind": "ability", "pokemon": "Gholdengo", "ability": "Wonder Guard"},
        {"kind": "legal_move", "pokemon": "Gholdengo", "move": "Definitely Fake Beam"},
    ],
)
def test_impossible_or_hallucinated_claims_are_rejected(claim):
    mechanics = LocalMechanics(format_id="gen9ou")

    validation = mechanics.validate_claim(claim)

    assert validation.status == "rejected"
    assert validation.reason


def test_multi_loss_guidance_escalates_only_after_repeated_evidence():
    one = build_loss_artifact(_replay(LOSS_WITH_SPIKES, "gen9ou-one"), bot_username="Bot")
    two = build_loss_artifact(_replay(LOSS_WITH_SPIKES, "gen9ou-two"), bot_username="Bot")

    single_summary = aggregate_loss_lessons([one], min_repeats=2)
    assert single_summary["proven_lessons"] == []
    assert single_summary["hypotheses"]
    assert "single-loss findings remain hypotheses" in single_summary["must_not_conclude"]["overfit_guardrail"]

    repeated_summary = aggregate_loss_lessons([one, two], min_repeats=2)
    lesson_ids = {lesson["lesson_id"] for lesson in repeated_summary["proven_lessons"]}
    assert "bot_took_hazard_damage:spikes" in lesson_ids
    assert repeated_summary["hypotheses"] == []


def test_batch_stats_only_prompt_fails_closed_without_replay_evidence(monkeypatch, tmp_path):
    analyzer = BatchAnalyzer.__new__(BatchAnalyzer)
    analyzer.bot_username = "Bot"
    monkeypatch.setattr(analyzer, "get_battle_stats", lambda: [
        {
            "timestamp": "2026-05-20T00:00:00+00:00",
            "result": "loss",
            "team_file": "fat-team-1-stall",
            "opponent": "Opponent",
            "replay_id": "battle-gen9ou-missing",
        }
    ])
    monkeypatch.setattr(analyzer, "query_reasoning_agent", lambda prompt: prompt)
    monkeypatch.setattr("replay_analysis.batch_analyzer.REPORTS_DIR", tmp_path)

    report = analyzer.generate_stats_only_report(last_n=1, stats={"total": 1, "wins": 0, "losses": 1, "teams": {}})

    content = report.read_text(encoding="utf-8")
    assert "Aggregate statistics are not mechanics proof" in content
    assert "No local loss replay artifacts were available" in content
    assert "Do not write unsupported mechanics claims as fact" in content


def test_batch_prompt_does_not_smuggle_historical_meta_claims():
    analyzer = BatchAnalyzer.__new__(BatchAnalyzer)
    analyzer.bot_username = "Bot"

    prompt = analyzer.build_analysis_prompt(
        reviews=["--- Battle: battle-gen9ou-1 ---", "Turn 1: Bot chose Protect"],
        stats={"total": 1, "wins": 0, "losses": 1, "teams": {}},
        mechanics_summary="Proven lessons: none yet",
    )

    assert "76% of losses" not in prompt
    assert "No exceptions" not in prompt
    assert "advisory only" in prompt
    assert "label it unknown" in prompt
