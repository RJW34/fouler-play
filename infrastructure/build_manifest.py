"""
Build Manifest — Sidecar mapping deploy ranges to code versions.

Project-agnostic library. Tracks which git SHA was active during each range
of a project's progress metric (battle count, TAS lines, page deploys, etc.).

The caller provides the progress counter — this library only manages the
manifest lifecycle: deploy, commit, revert, query.

Written by: deploy scripts (on pull), autoresearch (on commit),
            watchdogs (on revert)
Read by:    Symphony pulse reporters (to annotate progress with build info)
"""

import copy
import json
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

_EMPTY_MANIFEST = {
    "schema_version": 1,
    "builds": [],
    "reverts": [],
    "metadata": {
        "repo": None,
        "branch": "master",
        "created": None,
        "last_updated": None,
    },
}


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _git(repo_dir: Path, *args, timeout: int = 10) -> Optional[str]:
    """Run a git command and return stdout, or None on failure."""
    try:
        result = subprocess.run(
            ["git", "-C", str(repo_dir), *args],
            capture_output=True,
            text=True,
            timeout=timeout,
        )
        if result.returncode == 0:
            return result.stdout.strip()
    except Exception:
        pass
    return None


def get_current_git_sha(repo_dir: Path, short: bool = True) -> str:
    """Return HEAD SHA of the repo. Falls back to 'unknown'."""
    args = ["rev-parse"]
    if short:
        args.append("--short=8")
    args.append("HEAD")
    return _git(repo_dir, *args) or "unknown"


def get_current_git_subject(repo_dir: Path) -> str:
    """Return the one-line subject of HEAD commit."""
    return _git(repo_dir, "log", "-1", "--format=%s") or ""


def get_repo_name(repo_dir: Path) -> Optional[str]:
    """Extract owner/repo from git remote origin URL."""
    url = _git(repo_dir, "remote", "get-url", "origin")
    if url:
        # Handle https://github.com/owner/repo.git and git@github.com:owner/repo.git
        url = url.rstrip("/").removesuffix(".git")
        if "/" in url:
            parts = url.replace(":", "/").split("/")
            if len(parts) >= 2:
                return f"{parts[-2]}/{parts[-1]}"
    return None


class BuildManifest:
    """
    Manages a build manifest sidecar file for any project.

    Usage:
        manifest = BuildManifest(repo_dir=Path("/path/to/repo"))
        manifest.record_deploy(progress_count=118, source="deploy_update.bat")
        manifest.record_commit(progress_count=118, files_changed=["fp/search/main.py"])
        build = manifest.get_build_for_progress(130)
    """

    def __init__(self, repo_dir: Path, manifest_path: Path = None):
        self.repo_dir = Path(repo_dir).resolve()
        self.manifest_path = (
            Path(manifest_path) if manifest_path
            else self.repo_dir / "data" / "build_manifest.json"
        )

    def _load(self) -> dict:
        if not self.manifest_path.exists():
            return copy.deepcopy(_EMPTY_MANIFEST)
        try:
            with open(self.manifest_path, "r", encoding="utf-8") as f:
                data = json.load(f)
            if not isinstance(data, dict) or "builds" not in data:
                return copy.deepcopy(_EMPTY_MANIFEST)
            return data
        except (json.JSONDecodeError, OSError):
            return copy.deepcopy(_EMPTY_MANIFEST)

    def _save(self, manifest: dict) -> None:
        manifest["metadata"]["last_updated"] = _now_iso()
        if manifest["metadata"].get("created") is None:
            manifest["metadata"]["created"] = manifest["metadata"]["last_updated"]
        if manifest["metadata"].get("repo") is None:
            manifest["metadata"]["repo"] = get_repo_name(self.repo_dir)

        self.manifest_path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self.manifest_path.with_suffix(".tmp")
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(manifest, f, indent=2, ensure_ascii=False)
            f.write("\n")
        tmp.replace(self.manifest_path)

    def _close_previous_build(self, manifest: dict, progress_count: int) -> None:
        """Close the previous build's range if open."""
        if manifest["builds"]:
            prev = manifest["builds"][-1]
            if prev.get("progress_end") is None:
                prev["progress_end"] = progress_count
                prev["timestamp_end"] = _now_iso()

    def record_deploy(
        self,
        progress_count: int = 0,
        source: str = "manual",
        extra: dict = None,
    ) -> dict:
        """
        Record a new deploy (code pull) in the manifest.

        Args:
            progress_count: Current progress metric (battles played, TAS lines, etc.)
            source: What triggered the deploy (deploy_update.bat, autoresearch, etc.)
            extra: Optional dict of project-specific metadata to store

        Returns the new build entry.
        """
        manifest = self._load()
        sha = get_current_git_sha(self.repo_dir)

        # Don't create duplicate if SHA hasn't changed
        if manifest["builds"] and manifest["builds"][-1].get("sha") == sha:
            return manifest["builds"][-1]

        self._close_previous_build(manifest, progress_count)

        entry = {
            "sha": sha,
            "subject": get_current_git_subject(self.repo_dir),
            "timestamp": _now_iso(),
            "source": source,
            "progress_at_deploy": progress_count,
            "progress_end": None,
            "timestamp_end": None,
            "files_changed": None,
            "reverted": False,
        }
        if extra:
            entry["extra"] = extra

        manifest["builds"].append(entry)
        self._save(manifest)
        return entry

    def record_commit(
        self,
        progress_count: int = 0,
        files_changed: list = None,
        source: str = "autoresearch",
        extra: dict = None,
    ) -> dict:
        """
        Record a new commit (autoresearch/builder improvement).

        Returns the new build entry.
        """
        manifest = self._load()
        sha = get_current_git_sha(self.repo_dir)

        self._close_previous_build(manifest, progress_count)

        entry = {
            "sha": sha,
            "subject": get_current_git_subject(self.repo_dir),
            "timestamp": _now_iso(),
            "source": source,
            "progress_at_deploy": progress_count,
            "progress_end": None,
            "timestamp_end": None,
            "files_changed": files_changed,
            "reverted": False,
        }
        if extra:
            entry["extra"] = extra

        manifest["builds"].append(entry)
        self._save(manifest)
        return entry

    def record_revert(self, reverted_sha: str, reason: str, progress_count: int = 0) -> dict:
        """
        Record an auto-revert event.

        Marks the reverted build, creates a new entry for the reverted-to code.
        Returns the revert entry.
        """
        manifest = self._load()
        sha = get_current_git_sha(self.repo_dir)

        # Close and mark the reverted build
        self._close_previous_build(manifest, progress_count)
        if manifest["builds"]:
            manifest["builds"][-1]["reverted"] = True

        revert_entry = {
            "timestamp": _now_iso(),
            "reverted_sha": reverted_sha,
            "reverted_to_sha": sha,
            "reason": reason,
            "progress_at_revert": progress_count,
        }
        manifest["reverts"].append(revert_entry)

        # New build entry for the code we reverted to
        build_entry = {
            "sha": sha,
            "subject": get_current_git_subject(self.repo_dir),
            "timestamp": _now_iso(),
            "source": f"revert:{reverted_sha[:8]}",
            "progress_at_deploy": progress_count,
            "progress_end": None,
            "timestamp_end": None,
            "files_changed": None,
            "reverted": False,
        }
        manifest["builds"].append(build_entry)

        self._save(manifest)
        return revert_entry

    def get_current_build(self) -> Optional[dict]:
        """Return the most recent build entry, or None."""
        manifest = self._load()
        return manifest["builds"][-1] if manifest["builds"] else None

    def get_build_for_progress(self, index: int) -> Optional[dict]:
        """Find which build was active at a given progress index."""
        manifest = self._load()
        for b in reversed(manifest.get("builds", [])):
            start = b.get("progress_at_deploy", 0)
            end = b.get("progress_end") if b.get("progress_end") is not None else float("inf")
            if start <= index < end:
                return b
        return None

    def get_builds_in_range(self, start: int, end: int) -> list:
        """Return all builds overlapping a progress range."""
        manifest = self._load()
        result = []
        for b in manifest.get("builds", []):
            b_start = b.get("progress_at_deploy", 0)
            b_end = b.get("progress_end") if b.get("progress_end") is not None else float("inf")
            if b_start < end and b_end > start:
                result.append(b)
        return result

    def get_summary(self) -> dict:
        """Return a diagnostic summary of the manifest."""
        manifest = self._load()
        builds = manifest.get("builds", [])
        current = builds[-1] if builds else None
        return {
            "total_builds": len(builds),
            "total_reverts": len(manifest.get("reverts", [])),
            "current_sha": current.get("sha") if current else None,
            "current_source": current.get("source") if current else None,
            "current_deploy_progress": current.get("progress_at_deploy") if current else None,
            "last_updated": manifest.get("metadata", {}).get("last_updated"),
        }


# ---------------------------------------------------------------------------
# Convenience: auto-detect repo from script location
# ---------------------------------------------------------------------------
def _default_repo_dir() -> Path:
    """Walk up from this file to find the repo root (has .git/)."""
    d = Path(__file__).resolve().parent
    for _ in range(5):
        if (d / ".git").exists():
            return d
        d = d.parent
    return Path(__file__).resolve().parent.parent


def get_manifest(repo_dir: Path = None) -> BuildManifest:
    """Get a BuildManifest for the given or auto-detected repo."""
    return BuildManifest(repo_dir or _default_repo_dir())


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------
if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser(description="Build manifest management")
    parser.add_argument("action", choices=["seed", "deploy", "commit", "show"],
                        help="Action to perform")
    parser.add_argument("--source", default="cli", help="Source label for the entry")
    parser.add_argument("--progress", type=int, default=0,
                        help="Current progress count (battles, TAS lines, etc.)")
    parser.add_argument("--repo", type=str, default=None, help="Repo directory path")
    args = parser.parse_args()

    m = get_manifest(Path(args.repo) if args.repo else None)

    if args.action == "seed":
        entry = m.record_deploy(progress_count=args.progress, source="initial-seed")
        print(f"Seeded: {entry['sha']} at progress={entry['progress_at_deploy']}")
    elif args.action == "deploy":
        entry = m.record_deploy(progress_count=args.progress, source=args.source)
        print(f"Deploy: {entry['sha']} at progress={entry['progress_at_deploy']}")
    elif args.action == "commit":
        entry = m.record_commit(progress_count=args.progress, source=args.source)
        print(f"Commit: {entry['sha']} at progress={entry['progress_at_deploy']}")
    elif args.action == "show":
        print(json.dumps(m.get_summary(), indent=2))
