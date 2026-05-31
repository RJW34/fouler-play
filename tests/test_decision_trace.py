from types import SimpleNamespace

from fp.decision_trace import build_trace_base


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
