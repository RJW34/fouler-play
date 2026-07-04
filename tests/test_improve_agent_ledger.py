import json

from infrastructure import improve_agent


def test_append_improve_ledger_records_machine_readable_no_change(tmp_path, monkeypatch):
    ledger = tmp_path / "improve_ledger.jsonl"
    monkeypatch.setattr(improve_agent, "IMPROVE_LEDGER_PATH", ledger)
    monkeypatch.setattr(
        improve_agent,
        "_load_battles",
        lambda: [
            {
                "battle_id": "battle-gen9ou-1",
                "result": "loss",
                "elo_after": 1390,
            },
            {
                "battle_id": "battle-gen9ou-2",
                "result": "win",
                "elo_after": 1409,
            },
        ],
    )

    improve_agent.append_improve_ledger(
        "no_change",
        issue="No promotable issue",
        target_file="fp/search/main.py",
        detail={"reason": "autoresearch_not_promotable", "blockers": ["missing replay proof"]},
        returncode=0,
    )

    rows = [json.loads(line) for line in ledger.read_text(encoding="utf-8").splitlines()]
    assert len(rows) == 1
    row = rows[0]
    assert row["outcome"] == "no_change"
    assert row["issue"] == "No promotable issue"
    assert row["target_file"] == "fp/search/main.py"
    assert row["returncode"] == 0
    assert row["detail"]["reason"] == "autoresearch_not_promotable"
    assert row["ladder"]["latest_battle_id"] == "battle-gen9ou-2"
    assert row["ladder"]["current_elo"] == 1409
