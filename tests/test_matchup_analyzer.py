from fp import matchup_analyzer
from fp.matchup_analyzer import Gameplan, analyze_matchup


def test_matchup_analyzer_rejects_unvalidated_llm_gameplan():
    gameplan = Gameplan(
        opponent_win_condition="Gholdengo has Levitate and walls Ground coverage.",
        opponent_weaknesses=["Earthquake hits Corviknight super effectively."],
        our_strategy="Use those mechanics claims to force progress.",
        key_pivot_triggers=["Switch on assumed immunity."],
        win_condition="Win from unsupported matchup facts.",
    )

    blockers = matchup_analyzer._validate_gameplan(gameplan)

    lowered = "\n".join(blockers).lower()
    assert "gholdengo" in lowered
    assert "levitate" in lowered
    assert "earthquake" in lowered
    assert "corviknight" in lowered


def test_matchup_analyzer_skips_local_llm_by_default(monkeypatch):
    monkeypatch.setattr(matchup_analyzer, "ENABLE_LLM_GAMEPLAN", False)

    def fail_call(_prompt):
        raise AssertionError("local LLM must not be called unless explicitly enabled")

    monkeypatch.setattr(matchup_analyzer, "_call_ollama", fail_call)
    our_team = [
        {"species": "Corviknight", "moves": ["Brave Bird", "Roost"], "item": "Rocky Helmet", "ability": "Pressure"},
        {"species": "Gholdengo", "moves": ["Shadow Ball"], "item": "Air Balloon", "ability": "Good as Gold"},
    ]
    opp_team = [
        {"species": "Great Tusk", "moves": ["Earthquake", "Rapid Spin"], "item": "Booster Energy", "ability": "Protosynthesis"},
        {"species": "Corviknight", "moves": ["Brave Bird", "Roost"], "item": "Leftovers", "ability": "Pressure"},
    ]

    gameplan = analyze_matchup(our_team, opp_team, use_cache=False)

    assert "unsupported strategy claims" in gameplan.backup_plan
    assert "model-only assumptions" in " ".join(gameplan.key_pivot_triggers)
