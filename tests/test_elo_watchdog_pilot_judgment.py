import json

from infrastructure import elo_watchdog


def pilot_rows(counts=(10, 10, 10)):
    rows = []
    battle_number = 0
    for team, count in zip(elo_watchdog.OWNER_LOCKED_PILOT_TEAMS, counts):
        for _index in range(count):
            battle_number += 1
            rows.append(
                {
                    "battle_id": f"battle-gen9ou-{battle_number}",
                    "result": "win" if battle_number % 2 else "loss",
                    "team_file": team,
                }
            )
    return rows


def test_pilot_sample_requires_exactly_thirty_and_ten_per_locked_team():
    balanced_blockers, balanced_counts = elo_watchdog.pilot_sample_blockers(
        pilot_rows(),
        require_complete=True,
    )
    imbalanced_blockers, _counts = elo_watchdog.pilot_sample_blockers(
        pilot_rows((11, 10, 9)),
        require_complete=True,
    )
    overflow_blockers, _counts = elo_watchdog.pilot_sample_blockers(
        [*pilot_rows(), {"battle_id": "battle-gen9ou-31", "result": "win", "team_file": "fat-team-1-stall"}],
        require_complete=True,
    )
    unknown_blockers, _counts = elo_watchdog.pilot_sample_blockers(
        [{**row, "team_file": "unreviewed-team"} if index == 0 else row for index, row in enumerate(pilot_rows())],
        require_complete=True,
    )

    assert balanced_blockers == []
    assert balanced_counts == {team: 10 for team in elo_watchdog.OWNER_LOCKED_PILOT_TEAMS}
    assert any("exactly 10" in blocker for blocker in imbalanced_blockers)
    assert any("exactly 30" in blocker for blocker in overflow_blockers)
    assert any("unknown or missing" in blocker for blocker in unknown_blockers)


def test_watchdog_writes_judgment_only_for_exact_balanced_sample(tmp_path, monkeypatch):
    rows = pilot_rows()
    activation = {
        "activationId": "activation-1",
        "deploymentId": "deployment-1",
    }
    receipt_path = tmp_path / "judgment.json"
    observed = {}
    monkeypatch.setattr(
        elo_watchdog,
        "current_deployment_context",
        lambda **_kwargs: {"activation": activation, "blockers": []},
    )
    monkeypatch.setattr(elo_watchdog, "read_battle_rows", lambda _path: rows)
    monkeypatch.setattr(
        elo_watchdog,
        "deployment_battles",
        lambda _rows, _activation, decisive_only: list(rows),
    )
    monkeypatch.setattr(
        elo_watchdog,
        "judgment_receipt_path",
        lambda _activation_id, _state_root: receipt_path,
    )

    def build_receipt(**kwargs):
        observed.update(kwargs)
        return {
            "status": "passed",
            "minimumBattles": 30,
            "battleEvidence": [{"battleId": row["battle_id"]} for row in rows],
        }

    monkeypatch.setattr(elo_watchdog, "build_judgment_receipt", build_receipt)
    monkeypatch.setattr(
        elo_watchdog,
        "write_immutable_receipt",
        lambda path, payload: path.write_text(json.dumps(payload), encoding="utf-8"),
    )
    monkeypatch.setattr(
        elo_watchdog,
        "judgment_receipt_blockers",
        lambda path, **_kwargs: (json.loads(path.read_text(encoding="utf-8")), []),
    )

    result = elo_watchdog.check_and_judge(
        battle_stats_path=tmp_path / "battle_stats.json",
        state_root=tmp_path / "state",
    )

    assert result["ok"] is True
    assert result["status"] == "passed"
    assert result["exactIdentityBattles"] == 30
    assert result["pilotTeamCounts"] == {team: 10 for team in elo_watchdog.OWNER_LOCKED_PILOT_TEAMS}
    assert observed["min_battles"] == 30


def test_watchdog_blocks_overfilled_or_unbalanced_sample_before_receipt(tmp_path, monkeypatch):
    activation = {"activationId": "activation-1", "deploymentId": "deployment-1"}
    monkeypatch.setattr(
        elo_watchdog,
        "current_deployment_context",
        lambda **_kwargs: {"activation": activation, "blockers": []},
    )
    monkeypatch.setattr(elo_watchdog, "read_battle_rows", lambda _path: [])
    monkeypatch.setattr(
        elo_watchdog,
        "judgment_receipt_path",
        lambda _activation_id, _state_root: tmp_path / "missing-judgment.json",
    )
    built = []
    monkeypatch.setattr(elo_watchdog, "build_judgment_receipt", lambda **kwargs: built.append(kwargs))

    for rows in (pilot_rows((11, 10, 9)), [*pilot_rows(), {"battle_id": "battle-gen9ou-31", "result": "win", "team_file": "fat-team-1-stall"}]):
        monkeypatch.setattr(
            elo_watchdog,
            "deployment_battles",
            lambda _all_rows, _activation, decisive_only, rows=rows: list(rows),
        )
        result = elo_watchdog.check_and_judge(
            battle_stats_path=tmp_path / "battle_stats.json",
            state_root=tmp_path / "state",
        )
        assert result["ok"] is False
        assert result["status"] == "blocked-pilot-sample"

    assert built == []


def test_watchdog_validates_complete_matching_sample_before_decisive_filter(tmp_path, monkeypatch):
    identity = {
        "sourceCommit": "a" * 40,
        "sourceTree": "b" * 40,
        "runtimeManifestDigest": "c" * 64,
        "changeId": "d" * 64,
        "deploymentId": "deployment-1",
        "deploymentReceiptSha256": "e" * 64,
        "runtimeLeaseId": "lease-1",
        "runtimeAuthorizationSha256": "f" * 64,
        "sessionId": "session-1",
        "account": "DekuFoulerLab",
    }
    activation = {
        "activationId": "activation-1",
        "deploymentId": "deployment-1",
        "runtimeIdentity": identity,
        "runtimeLeaseSnapshot": {
            "proofWindow": {
                "startsAt": "2026-07-15T00:00:00+00:00",
                "expiresAt": "2026-07-16T00:00:00+00:00",
            }
        },
    }
    field_map = {
        "source_commit": "sourceCommit",
        "source_tree": "sourceTree",
        "runtime_manifest_digest": "runtimeManifestDigest",
        "change_id": "changeId",
        "deployment_id": "deploymentId",
        "deployment_receipt_sha256": "deploymentReceiptSha256",
        "runtime_lease_id": "runtimeLeaseId",
        "runtime_authorization_sha256": "runtimeAuthorizationSha256",
        "session_id": "sessionId",
    }
    rows = []
    for index, row in enumerate(
        [
            *pilot_rows(),
            {
                "battle_id": "battle-gen9ou-31",
                "result": "tie",
                "team_file": "fat-team-1-stall",
            },
        ],
        start=1,
    ):
        rows.append(
            {
                **row,
                **{row_field: identity[identity_field] for row_field, identity_field in field_map.items()},
                "account": identity["account"],
                "timestamp": f"2026-07-15T00:{index:02d}:00+00:00",
            }
        )

    monkeypatch.setattr(
        elo_watchdog,
        "current_deployment_context",
        lambda **_kwargs: {"activation": activation, "blockers": []},
    )
    monkeypatch.setattr(elo_watchdog, "read_battle_rows", lambda _path: rows)
    monkeypatch.setattr(
        elo_watchdog,
        "judgment_receipt_path",
        lambda _activation_id, _state_root: tmp_path / "missing-judgment.json",
    )
    built = []
    monkeypatch.setattr(elo_watchdog, "build_judgment_receipt", lambda **kwargs: built.append(kwargs))

    result = elo_watchdog.check_and_judge(
        battle_stats_path=tmp_path / "battle_stats.json",
        state_root=tmp_path / "state",
    )

    assert result["ok"] is False
    assert result["status"] == "blocked-pilot-sample"
    assert result["exactIdentityBattles"] == 31
    assert any("not decisive" in blocker for blocker in result["blockers"])
    assert any("exceeded 10" in blocker for blocker in result["blockers"])
    assert built == []
