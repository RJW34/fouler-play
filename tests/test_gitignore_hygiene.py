import shutil
import subprocess
from pathlib import Path

import pytest


ROOT = Path(__file__).resolve().parents[1]


def ignored_paths(paths: list[str]) -> set[str]:
    if shutil.which("git") is None:
        pytest.skip("git is required for .gitignore contract checks")

    result = subprocess.run(
        ["git", "check-ignore", "--stdin", "--no-index"],
        cwd=ROOT,
        input="\n".join(paths) + "\n",
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        check=False,
    )
    if result.returncode not in {0, 1}:
        pytest.fail(f"git check-ignore failed: {result.stderr}")
    return set(result.stdout.splitlines())


def test_generated_pytest_proof_and_runtime_artifacts_are_ignored():
    generated = [
        ".tmp-codex-pytest/event-poster-slice/events_queue.json",
        ".tmp-codex-process-lock/.bot.pid",
        ".tmp-pytest-codex/cache/nodeids",
        ".codex-pytest-review-fouler/cache/nodeids",
        "devstream/backups/events_queue-before-compact-20260606T174927Z.json",
        "devstream/truth/discord-backlog-archive.json",
        "devstream/truth/discord-delivery.json",
        "devstream/truth/discord-reporting.json",
        "devstream/truth/discord-reporting-doctor.json",
        "devstream/truth/proof-status.json",
        "devstream/truth/stale-active-battles-backups/active_battles-20260606T215004Z.json",
        "eval_results/offline/frozen.json",
        "eval_results/offline/frozen-200-status.json",
        "eval_results/offline/showdown-server-frozen.stdout.log",
    ]

    assert ignored_paths(generated) == set(generated)


def test_source_truth_contract_files_remain_visible_to_git():
    source_truth = [
        ".gitignore",
        "devstream/truth/completion.json",
        "devstream/truth/elo-proof.example.json",
        "devstream/truth/elo-proof.schema.json",
        "devstream/truth/latest-elo-proof.json",
        "scripts/devstream_cycle_report.py",
        "tests/test_devstream_cycle_report.py",
    ]

    assert ignored_paths(source_truth) == set()
