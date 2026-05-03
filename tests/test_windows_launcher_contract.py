from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def test_start_one_touch_uses_bootstrapped_virtualenv_python():
    text = (ROOT / "start_one_touch.bat").read_text(encoding="utf-8")

    assert 'if exist ".venv\\Scripts\\python.exe" set "PY_EXE=.venv\\Scripts\\python.exe"' in text
    assert "echo [START] Python : %PY_EXE%" in text
    assert 'call "%PY_EXE%" run.py ^' in text
    assert "call python run.py ^" not in text
    assert "--ps-password" not in text
    assert "$_.CommandLine -match 'search_ladder' -and $_.CommandLine -match '--ps-username'" in text


def test_start_one_touch_preserves_explicit_control_env_over_dotenv():
    text = (ROOT / "start_one_touch.bat").read_text(encoding="utf-8")

    assert 'if not "%%~A"=="" if not defined %%~A set "%%~A=%%~B"' in text


def test_jigglypuff_wrapper_forces_actionable_logs_and_cleans_relative_obs_server():
    text = (ROOT / "scripts" / "fouler_jigglypuff_runtime.ps1").read_text(encoding="utf-8")

    assert "set BOT_LOG_TO_FILE=1" in text
    assert "set AUTO_START_OBS_SERVER=0" in text
    assert "set LOSS_TRIGGERED_DRAIN=0" in text
    assert '$_.CommandLine -match "streaming[\\\\/]+serve_obs_page\\.py"' in text
    assert '$_.CommandLine -match "search_ladder" -and' in text
    assert '$_.CommandLine -match $escapedRepo -and' in text
    assert "function Redact-CommandLine" in text
    assert "Redact-CommandLine -CommandLine $_.CommandLine" in text
