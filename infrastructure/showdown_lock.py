"""Verify the local Pokemon Showdown source used by Fouler eval gates."""

from __future__ import annotations

import json
import subprocess
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[1]
LOCK_PATH = PROJECT_ROOT / "infrastructure" / "showdown.lock.json"


def load_lock(path: Path = LOCK_PATH) -> dict:
    return json.loads(Path(path).read_text(encoding="utf-8"))


def _git(path: Path, args: list[str]) -> tuple[int, str, str]:
    proc = subprocess.run(
        ["git", "-C", str(path), *args],
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=30,
    )
    return proc.returncode, proc.stdout.strip(), proc.stderr.strip()


def verify_showdown_source(lock_path: Path = LOCK_PATH) -> dict:
    """Return a fail-closed verification payload for the locked Showdown repo."""

    try:
        lock = load_lock(lock_path)
    except Exception as exc:
        return {"ok": False, "reason": f"could not read showdown lock: {exc}"}

    source = Path(str(lock.get("path") or ""))
    expected_head = str(lock.get("expected_head") or "").strip()
    expected_branch = str(lock.get("expected_branch") or "").strip()
    allow_dirty = bool(lock.get("allow_dirty", False))
    result = {
        "ok": False,
        "path": str(source),
        "expected_head": expected_head,
        "expected_branch": expected_branch,
        "allow_dirty": allow_dirty,
    }
    if not source.exists():
        result["reason"] = f"showdown source path missing: {source}"
        return result

    code, actual_head, stderr = _git(source, ["rev-parse", "HEAD"])
    result["actual_head"] = actual_head
    if code != 0:
        result["reason"] = f"git rev-parse failed: {stderr or actual_head}"
        return result
    if expected_head and actual_head.lower() != expected_head.lower():
        result["reason"] = f"showdown HEAD mismatch: {actual_head} != {expected_head}"
        return result

    code, branch, stderr = _git(source, ["branch", "--show-current"])
    result["actual_branch"] = branch
    if code != 0:
        result["reason"] = f"git branch failed: {stderr or branch}"
        return result
    if expected_branch and branch != expected_branch:
        result["reason"] = f"showdown branch mismatch: {branch} != {expected_branch}"
        return result

    code, status, stderr = _git(source, ["status", "--porcelain"])
    result["dirty"] = bool(status)
    if code != 0:
        result["reason"] = f"git status failed: {stderr or status}"
        return result
    if status and not allow_dirty:
        result["reason"] = "showdown source is dirty"
        result["status"] = status.splitlines()[:20]
        return result

    result["ok"] = True
    result["reason"] = "showdown source lock verified"
    return result
