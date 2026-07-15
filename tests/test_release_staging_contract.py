from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
STAGER = ROOT / "infrastructure" / "windows" / "stage_fouler_release.ps1"


def test_release_stager_is_exact_commit_immutable_and_no_start():
    source = STAGER.read_text(encoding="utf-8")

    assert "ValidatePattern('^[0-9a-fA-F]{40}$')" in source
    assert '"fetch", "--no-tags", "--depth=1", "origin", $SourceCommit' in source
    assert '$fetched -ne $SourceCommit' in source
    assert '$head -ne $SourceCommit' in source
    assert "release destination already exists" in source
    assert "release manifest already exists" in source
    assert "Move-Item -LiteralPath $staging -Destination $destination" in source
    assert 'runtimeStarted = $false' in source
    assert "Start-Service" not in source
    assert "Start-ScheduledTask" not in source


def test_release_stager_runs_quality_gates_before_manifest_and_move():
    source = STAGER.read_text(encoding="utf-8")

    pip_check = source.index('"pip", "check"')
    fatal_ruff = source.index('"--select", "E9,F63,F7,F82"')
    strict_ruff = source.index('$strictRuffPaths = @(')
    pytest = source.index('"pytest", "-q", "--basetemp"')
    inventory = source.index("$files = Get-ReleaseFileInventory")
    move = source.index("Move-Item -LiteralPath $staging -Destination $destination")
    assert pip_check < fatal_ruff < strict_ruff < pytest < inventory < move
    assert '"git", "clean"' not in source
    assert '"clean", "-ffdx", "-e", ".venv/"' in source


def test_release_stager_uses_truthful_fatal_and_strict_lint_surfaces():
    source = STAGER.read_text(encoding="utf-8")

    assert '"--select", "E9,F63,F7,F82"' in source
    assert '"--exclude", "launch_integration_example.py"' in source
    assert 'Label "repository fatal lint gate"' in source
    assert 'Label "strict runtime lint gate"' in source
    assert 'repositoryFatalLintPassed = $true' in source
    assert 'strictRuntimeLintPassed = $true' in source
    assert 'ruffPassed = $true' not in source
    for required in (
        "run.py",
        "process_lock.py",
        "infrastructure/runtime_authorization.py",
        "infrastructure/windows/fouler_lease_broker.py",
        "infrastructure/head_to_head_authority.py",
        "infrastructure/head_to_head_eval.py",
        "scripts/devstream_session.py",
        "scripts/run_bounded_battle_session.py",
        "scripts/fouler_runtime_authority.py",
        "streaming/run_obs_server_service.py",
    ):
        assert f'"{required}"' in source


def test_release_manifest_covers_runtime_and_verifier_files():
    source = STAGER.read_text(encoding="utf-8")

    assert 'schemaVersion = "fouler-bootstrap-manifest/v1"' in source
    assert 'projectId = "fouler-play"' in source
    for required in (
        ".venv/Scripts/python.exe",
        "scripts/devstream_runtime_lease.py",
        "scripts/devstream_session.py",
        "scripts/run_bounded_battle_session.py",
        "run.py",
        "infrastructure/deployment_lineage.py",
        "infrastructure/head_to_head_authority.py",
        "infrastructure/head_to_head_eval.py",
        "infrastructure/runtime_authorization.py",
        "infrastructure/windows/fouler_lease_broker.py",
        "scripts/install_obs_server_service.ps1",
        "streaming/run_obs_server_service.py",
        "streaming/serve_obs_page.py",
    ):
        assert required in source


def test_release_stager_keeps_manifest_and_test_temp_outside_release():
    source = STAGER.read_text(encoding="utf-8")

    assert "Assert-NoPathOverlap -First $releaseRootPath -Second $manifestRootPath" in source
    assert "Assert-NoPathOverlap -First $stagingRootPath -Second $manifestRootPath" in source
    assert "$testTemp = Join-Path $stagingRootPath" in source
    assert "source bytecode" in source
