import json
from pathlib import Path

from infrastructure import offline_eval_readiness


def _write_minimal_harness(root: Path) -> None:
    (root / "infrastructure").mkdir(parents=True, exist_ok=True)
    (root / "infrastructure" / "offline_eval.py").write_text("# eval harness\n", encoding="utf-8")
    (root / "infrastructure" / "_offline_baseline.py").write_text("# baseline\n", encoding="utf-8")
    (root / "infrastructure" / "requirements-eval.txt").write_text("poke-env\n", encoding="utf-8")
    team = root / "teams" / "gen9" / "ou" / "fat-team-1-stall"
    team.parent.mkdir(parents=True, exist_ok=True)
    team.write_text("Corviknight @ Leftovers\n", encoding="utf-8")


def test_readiness_payload_reports_actionable_missing_harness(tmp_path):
    payload = offline_eval_readiness.build_readiness_payload(
        root=tmp_path,
        env={},
        run_import_check=False,
    )

    assert payload["recursiveImprovementReady"] is False
    assert any("offline_eval.py" in blocker for blocker in payload["blockers"])
    assert any("eval venv python" in blocker for blocker in payload["blockers"])
    assert "py -3 -m venv .venv-eval" in payload["provisionCommands"]["windows"]
    assert ".venv-eval\\Scripts\\python.exe -m pip install -r infrastructure\\requirements-eval.txt" in payload["provisionCommands"]["windows"]
    assert "python3 -m venv .venv-eval" in payload["provisionCommands"]["posix"]
    assert ".venv-eval/bin/python -m pip install -r infrastructure/requirements-eval.txt" in payload["provisionCommands"]["posix"]
    assert "--label candidate" in payload["commands"]["candidateEval"]
    assert "--compare frozen candidate" in payload["commands"]["compareFrozenVsCandidate"]
    assert "eval_results/offline/frozen.json" in json.dumps(payload["proofRequired"])


def test_readiness_payload_ready_with_eval_proof(tmp_path):
    _write_minimal_harness(tmp_path)
    venv_python = tmp_path / ".venv-eval" / "Scripts" / "python.exe"
    venv_python.parent.mkdir(parents=True, exist_ok=True)
    venv_python.write_text("# fake python for path proof\n", encoding="utf-8")
    frozen = tmp_path / "eval_results" / "offline" / "frozen.json"
    frozen.parent.mkdir(parents=True, exist_ok=True)
    frozen.write_text(
        json.dumps(
            {
                "label": "frozen",
                "battles": 200,
                "fouler_wins": 120,
                "fouler_win_rate": 0.6,
                "fouler_wilson_lcb": 0.53,
            }
        ),
        encoding="utf-8",
    )

    payload = offline_eval_readiness.build_readiness_payload(
        root=tmp_path,
        env={},
        run_import_check=False,
    )

    assert payload["recursiveImprovementReady"] is True
    assert payload["blockers"] == []
    assert payload["paths"]["frozenBaseline"] == "eval_results\\offline\\frozen.json"
    assert "infrastructure\\offline_eval.py" in payload["commands"]["candidateEval"]
    assert "--battles 200" in payload["commands"]["candidateEval"]


def test_readiness_payload_honors_eval_env_overrides(tmp_path):
    _write_minimal_harness(tmp_path)
    env = {
        "IMPROVE_AGENT_EVAL_BATTLES": "40",
        "IMPROVE_AGENT_EVAL_TEAM": "gen9/ou/fat-team-1-stall",
        "IMPROVE_AGENT_EVAL_BASELINE": "maxbp",
        "EVAL_SHOWDOWN_PORT": "9876",
    }

    payload = offline_eval_readiness.build_readiness_payload(
        root=tmp_path,
        env=env,
        run_import_check=False,
    )

    assert payload["configuration"]["battles"] == 40
    assert payload["configuration"]["baseline"] == "maxbp"
    assert payload["configuration"]["showdownPort"] == 9876
    assert "--battles 40" in payload["commands"]["candidateEval"]
    assert "--baseline maxbp" in payload["commands"]["candidateEval"]
    assert payload["commands"]["showdownServer"].endswith("9876")
