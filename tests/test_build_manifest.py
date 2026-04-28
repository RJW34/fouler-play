"""Tests for the build manifest sidecar system."""

import json
import tempfile
from pathlib import Path
from unittest.mock import patch

import pytest

from infrastructure.build_manifest import BuildManifest, get_current_git_sha


@pytest.fixture
def tmp_repo(tmp_path):
    """Create a minimal repo structure with a manifest."""
    (tmp_path / ".git").mkdir()
    (tmp_path / "data").mkdir()
    return tmp_path


@pytest.fixture
def manifest(tmp_repo):
    """Return a BuildManifest pointing at the temp repo."""
    return BuildManifest(repo_dir=tmp_repo)


def _mock_git_sha(sha="abcd1234"):
    return patch(
        "infrastructure.build_manifest.get_current_git_sha",
        return_value=sha,
    )


def _mock_git_subject(subject="test commit"):
    return patch(
        "infrastructure.build_manifest.get_current_git_subject",
        return_value=subject,
    )


class TestRecordDeploy:
    def test_creates_first_build(self, manifest):
        with _mock_git_sha("aaa11111"), _mock_git_subject("first deploy"):
            entry = manifest.record_deploy(progress_count=0, source="test")

        assert entry["sha"] == "aaa11111"
        assert entry["subject"] == "first deploy"
        assert entry["source"] == "test"
        assert entry["progress_at_deploy"] == 0
        assert entry["progress_end"] is None
        assert entry["reverted"] is False

    def test_closes_previous_build_range(self, manifest):
        with _mock_git_sha("aaa11111"), _mock_git_subject("first"):
            manifest.record_deploy(progress_count=0, source="test")

        with _mock_git_sha("bbb22222"), _mock_git_subject("second"):
            manifest.record_deploy(progress_count=30, source="test")

        data = manifest._load()
        assert len(data["builds"]) == 2
        assert data["builds"][0]["progress_end"] == 30
        assert data["builds"][1]["progress_at_deploy"] == 30
        assert data["builds"][1]["progress_end"] is None

    def test_skips_duplicate_sha(self, manifest):
        with _mock_git_sha("aaa11111"), _mock_git_subject("same"):
            manifest.record_deploy(progress_count=0)
            entry = manifest.record_deploy(progress_count=10)

        data = manifest._load()
        assert len(data["builds"]) == 1
        assert entry["sha"] == "aaa11111"

    def test_stores_extra_metadata(self, manifest):
        with _mock_git_sha("eee55555"), _mock_git_subject("with extra"):
            entry = manifest.record_deploy(
                progress_count=50,
                source="test",
                extra={"elo": 1350, "win_rate": 0.52},
            )

        assert entry["extra"]["elo"] == 1350
        assert entry["extra"]["win_rate"] == 0.52


class TestRecordCommit:
    def test_records_with_files_changed(self, manifest):
        with _mock_git_sha("aaa11111"), _mock_git_subject("seed"):
            manifest.record_deploy(progress_count=0)

        with _mock_git_sha("ccc33333"), _mock_git_subject("autoresearch: fix switches"):
            entry = manifest.record_commit(
                progress_count=30,
                files_changed=["fp/search/main.py"],
                source="autoresearch",
            )

        assert entry["sha"] == "ccc33333"
        assert entry["files_changed"] == ["fp/search/main.py"]
        assert entry["source"] == "autoresearch"

        data = manifest._load()
        assert data["builds"][0]["progress_end"] == 30


class TestRecordRevert:
    def test_marks_reverted_build_and_creates_new(self, manifest):
        with _mock_git_sha("aaa11111"), _mock_git_subject("good"):
            manifest.record_deploy(progress_count=0)

        with _mock_git_sha("bbb22222"), _mock_git_subject("bad"):
            manifest.record_deploy(progress_count=30)

        with _mock_git_sha("aaa11111"), _mock_git_subject("good"):
            revert = manifest.record_revert(
                reverted_sha="bbb22222",
                reason="ELO drop: 55",
                progress_count=45,
            )

        assert revert["reverted_sha"] == "bbb22222"
        assert revert["reverted_to_sha"] == "aaa11111"

        data = manifest._load()
        assert len(data["builds"]) == 3
        assert data["builds"][1]["reverted"] is True
        assert data["builds"][1]["progress_end"] == 45
        assert data["builds"][2]["sha"] == "aaa11111"
        assert data["builds"][2]["source"] == "revert:bbb22222"
        assert len(data["reverts"]) == 1


class TestQueryBuilds:
    def _seed_three_builds(self, manifest):
        with _mock_git_sha("aaa11111"), _mock_git_subject("first"):
            manifest.record_deploy(progress_count=0)
        with _mock_git_sha("bbb22222"), _mock_git_subject("second"):
            manifest.record_deploy(progress_count=30)
        with _mock_git_sha("ccc33333"), _mock_git_subject("third"):
            manifest.record_deploy(progress_count=60)

    def test_get_current_build(self, manifest):
        self._seed_three_builds(manifest)
        current = manifest.get_current_build()
        assert current["sha"] == "ccc33333"

    def test_get_build_for_progress(self, manifest):
        self._seed_three_builds(manifest)

        assert manifest.get_build_for_progress(0)["sha"] == "aaa11111"
        assert manifest.get_build_for_progress(15)["sha"] == "aaa11111"
        assert manifest.get_build_for_progress(29)["sha"] == "aaa11111"
        assert manifest.get_build_for_progress(30)["sha"] == "bbb22222"
        assert manifest.get_build_for_progress(59)["sha"] == "bbb22222"
        assert manifest.get_build_for_progress(60)["sha"] == "ccc33333"
        assert manifest.get_build_for_progress(999)["sha"] == "ccc33333"

    def test_get_builds_in_range(self, manifest):
        self._seed_three_builds(manifest)

        # Range within first build
        result = manifest.get_builds_in_range(10, 20)
        assert len(result) == 1
        assert result[0]["sha"] == "aaa11111"

        # Range spanning two builds
        result = manifest.get_builds_in_range(25, 35)
        assert len(result) == 2
        shas = {b["sha"] for b in result}
        assert shas == {"aaa11111", "bbb22222"}

        # Range spanning all builds
        result = manifest.get_builds_in_range(0, 100)
        assert len(result) == 3

    def test_get_summary(self, manifest):
        self._seed_three_builds(manifest)
        summary = manifest.get_summary()
        assert summary["total_builds"] == 3
        assert summary["total_reverts"] == 0
        assert summary["current_sha"] == "ccc33333"


class TestPersistence:
    def test_round_trip(self, manifest):
        with _mock_git_sha("aaa11111"), _mock_git_subject("persisted"):
            manifest.record_deploy(progress_count=42, source="round-trip-test")

        # Create a new instance reading from the same file
        m2 = BuildManifest(repo_dir=manifest.repo_dir, manifest_path=manifest.manifest_path)
        current = m2.get_current_build()
        assert current["sha"] == "aaa11111"
        assert current["progress_at_deploy"] == 42

    def test_handles_missing_file(self, manifest):
        assert manifest.get_current_build() is None
        assert manifest.get_builds_in_range(0, 100) == []

    def test_handles_corrupt_file(self, manifest):
        manifest.manifest_path.parent.mkdir(parents=True, exist_ok=True)
        manifest.manifest_path.write_text("not valid json{{{")
        assert manifest.get_current_build() is None


class TestGitSha:
    def test_returns_string(self):
        # This tests against the actual repo
        sha = get_current_git_sha(Path(__file__).resolve().parents[1])
        assert isinstance(sha, str)
        assert len(sha) >= 7 or sha == "unknown"
