from types import SimpleNamespace

import json

from fp.decision_trace import PUBLIC_BATTLE_VIEW_FILENAME, build_trace_base, write_decision_trace


def test_decision_trace_records_redacted_showdown_request_legal_options():
    battle = SimpleNamespace(
        battle_tag="battle-gen9ou-1",
        worker_id=1,
        turn=5,
        pokemon_format="gen9ou",
        rqid=42,
        wait=False,
        force_switch=False,
        user=SimpleNamespace(trapped=False),
        request_json={
            "rqid": 42,
            "active": [
                {
                    "moves": [
                        {"id": "recover", "disabled": False},
                        {"id": "toxic", "disabled": True},
                    ],
                    "trapped": False,
                }
            ],
            "side": {
                "pokemon": [
                    {"ident": "p1a: Blissey", "details": "Blissey, F", "condition": "100/100", "active": True},
                    {"ident": "p1b: Corviknight", "details": "Corviknight, M", "condition": "88/100", "active": False},
                    {"ident": "p1c: Clodsire", "details": "Clodsire, F", "condition": "0 fnt", "active": False},
                ]
            },
        },
        snapshot=lambda: {"turn": 5},
    )

    trace = build_trace_base(battle, reason="timeout")

    assert trace["showdownRequest"]["legalOptionsSource"] == "showdown-request"
    assert trace["showdownRequest"]["candidateSetBounded"] is True
    assert trace["showdownRequest"]["legalMoves"] == [{"activeSlot": 0, "id": "recover", "target": None}]
    assert trace["showdownRequest"]["legalSwitches"] == [{"slot": 1, "details": "Corviknight, M", "condition": "88/100"}]
    assert len(trace["showdownRequest"]["requestHash"]) == 64
    assert trace["legalOptions"]["requestHash"] == trace["showdownRequest"]["requestHash"]
    assert "npctypebeat" not in str(trace)


def test_decision_trace_suppresses_switches_while_trapped():
    battle = SimpleNamespace(
        battle_tag="battle-gen9ou-trapped",
        worker_id=1,
        turn=6,
        pokemon_format="gen9ou",
        rqid=43,
        wait=False,
        force_switch=False,
        user=SimpleNamespace(trapped=True),
        request_json={
            "rqid": 43,
            "active": [
                {
                    "moves": [{"id": "recover", "disabled": False}],
                    "trapped": True,
                }
            ],
            "side": {
                "pokemon": [
                    {"ident": "p1a: Blissey", "details": "Blissey, F", "condition": "100/100", "active": True},
                    {"ident": "p1b: Corviknight", "details": "Corviknight, M", "condition": "88/100", "active": False},
                    {"ident": "p1c: Clodsire", "details": "Clodsire, F", "condition": "70/100", "active": False},
                ]
            },
        },
        snapshot=lambda: {"turn": 6},
    )

    trace = build_trace_base(battle, reason="timeout")

    assert trace["legalOptions"]["trapped"] is True
    assert trace["legalOptions"]["legalMoves"] == [{"activeSlot": 0, "id": "recover", "target": None}]
    assert trace["legalOptions"]["legalSwitches"] == []
    assert trace["legalOptions"]["candidateSetBounded"] is True


def test_decision_trace_publishes_sanitized_live_battle_view(tmp_path):
    pokemon = {
        "name": "corviknight",
        "hp": 248,
        "max_hp": 399,
        "fainted": False,
        "status": "brn",
        "types": ["flying", "steel"],
        "ability": "pressure",
        "item": "leftovers",
        "moves": ["roost", "uturn"],
        "tera": {"active": False, "type": "dragon"},
        "boosts": {"def": 1, "atk": 0},
    }
    trace = {
        "battle_tag": "battle-gen9ou-123-private-room-token",
        "turn": 9,
        "timestamp": "2026-07-13T18:00:00Z",
        "format": "gen9ou",
        "choice": "move roost",
        "snapshot": {
            "turn": 9,
            "weather": "raindance",
            "user": {
                "account": "DekuFoulerLab",
                "active": pokemon,
                "reserve": [],
                "side_conditions": {"spikes": 2, "reflect": 0},
            },
            "opponent": {
                "account": "Opponent",
                "active": {**pokemon, "name": "weezinggalar", "status": None},
                "reserve": [{**pokemon, "name": "gliscor", "fainted": True, "hp": 0}],
                "side_conditions": {"stealthrock": 1},
            },
        },
    }

    trace_path = write_decision_trace(trace, base_dir=str(tmp_path))
    public = json.loads((tmp_path / PUBLIC_BATTLE_VIEW_FILENAME).read_text(encoding="utf-8"))

    assert trace_path is not None
    assert public["battle_id"] == trace["battle_tag"]
    assert public["turn"] == 9
    assert public["user"]["active"]["name"] == "corviknight"
    assert public["user"]["active"]["hp_percent"] == 62.2
    assert "hp" not in public["user"]["active"]
    assert "max_hp" not in public["user"]["active"]
    assert public["user"]["active"]["tera"]["type"] is None
    assert public["opponent"]["reserve"][0]["fainted"] is True
    assert public["user"]["side_conditions"] == {"spikes": 2}
    serialized = json.dumps(public)
    assert "leftovers" not in serialized
    assert "pressure" not in serialized
    assert "roost" not in serialized
    assert "move roost" not in serialized
    assert list(tmp_path.glob(f"{PUBLIC_BATTLE_VIEW_FILENAME}.*.tmp")) == []
