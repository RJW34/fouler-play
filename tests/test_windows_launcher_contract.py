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
    assert "set BATTLE_STATS_MAX_ENTRIES=5000" in text
    assert '$_.CommandLine -match "streaming[\\\\/]+serve_obs_page\\.py"' in text
    assert '$_.CommandLine -match "search_ladder" -and' in text
    assert '$_.CommandLine -match "devstream_session\\.py" -and $_.CommandLine -match "\\bsupervise\\b"' in text
    assert '$_.CommandLine -match $escapedRepo -and' in text
    assert "function Redact-CommandLine" in text
    assert "Redact-CommandLine -CommandLine $_.CommandLine" in text
    assert "function Get-LogicalProcessSummary" in text
    assert "battleSupervisor" in text
    assert "leafCount" in text
    assert "multiple Fouler OBS HTTP servers are running" in text
    assert "multiple Fouler battle supervisors are running" in text
    assert 'if ($Path -eq "/health")' in text
    assert '$rel -eq "battle_stats.json"' in text
    assert "battleCount = $battles.Count" in text
    assert "lastBattle = $lastBattle" in text
    assert '"scripts\\devstream_session.py"' in text
    assert '"supervise"' in text
    assert '"--run-count", "$RunCount"' in text
    assert '"--max-concurrent-battles", "$MaxConcurrentBattles"' in text
    assert "Start-BattleSession -RunCount $RunCount -MaxConcurrentBattles $MaxConcurrentBattles -MaxCycles $MaxCycles -AllowUnboundedSupervisor:$AllowUnboundedSupervisor -AutoImprove:$AutoImprove" in text
    assert "call start_one_touch.bat" not in text


def test_obs_server_task_runs_via_logged_cmd_wrapper():
    text = (ROOT / "scripts" / "install_obs_server_task.ps1").read_text(encoding="utf-8")

    assert "$TaskExecute" in text
    assert "cmd.exe" in text
    assert "start_obs_server_task.ps1" in text
    assert "-Foreground" not in text
    assert "jigglypuff-obs-server.log" in text
    assert "jigglypuff-obs-server.err.log" in text
    assert "$TaskArguments" in text
    assert "New-ScheduledTaskAction -Execute $TaskExecute -Argument $TaskArguments" in text
    assert '$_.Name -match "python|py"' in text
    assert "stderrTail" in text
    assert "lastTaskResult" in text
    assert "OBS_WS_PASSWORD" not in text
    assert "function Rotate-LogFile" in text
    assert "archive" in text


def test_obs_server_task_wrapper_loads_obs_secret_without_printing_it():
    text = (ROOT / "scripts" / "start_obs_server_task.ps1").read_text(encoding="utf-8")

    assert "hermes-devstream\\secrets.env" in text
    assert "OBS_WS_PASSWORD" in text
    assert "OBS_WEBSOCKET_PASSWORD" in text
    assert "HERMES_OBS_WEBSOCKET_PASSWORD" in text
    assert "FP_PARENT_PID" in text
    assert "[switch]$Foreground" in text
    assert "Start-Process" in text
    assert "Win32_Process" not in text
    assert "-WindowStyle Hidden" in text
    assert "RedirectStandardOutput" in text
    assert "SetEnvironmentVariable($_.Target, $value, \"Process\")" in text
    assert "Write-Output" not in text
    assert "Write-Host" not in text


def test_obs_server_keepalive_task_restarts_only_public_surface():
    keepalive = (ROOT / "scripts" / "fouler_obs_keepalive.ps1").read_text(encoding="utf-8")
    installer = (ROOT / "scripts" / "install_obs_server_keepalive_task.ps1").read_text(encoding="utf-8")

    assert "HERMES-FoulerObsServer" in keepalive
    assert "http://127.0.0.1:$Port/state" in keepalive
    assert "Start-ScheduledTask -TaskName $TaskName" in keepalive
    assert "Stop-ScheduledTask -TaskName $TaskName" in keepalive
    assert "stoppedStuckTask" in keepalive
    assert "Stop-Process" not in keepalive
    assert "PS_PASSWORD" not in keepalive
    assert "HERMES-FoulerObsKeepAlive" in installer
    assert "RepetitionInterval (New-TimeSpan -Minutes 1)" in installer
    assert "jigglypuff-obs-keepalive.log" in installer


def test_battle_supervisor_defaults_to_one_rated_battle():
    wrapper = (ROOT / "scripts" / "start_battle_supervisor_task.ps1").read_text(encoding="utf-8")
    installer = (ROOT / "scripts" / "install_battle_supervisor_task.ps1").read_text(encoding="utf-8")
    runtime = (ROOT / "scripts" / "fouler_jigglypuff_runtime.ps1").read_text(encoding="utf-8")

    assert "[switch]$AutoImprove" in wrapper
    assert "[switch]$AutoImprove" in installer
    assert "[switch]$AutoImprove" in runtime
    assert "[string]$RuntimeLease" in wrapper
    assert "[string]$RuntimeLease" in installer
    assert "[string]$RuntimeLease" in runtime
    assert "[switch]$AllowUnboundedSupervisor" in wrapper
    assert "[switch]$AllowUnboundedSupervisor" in installer
    assert "[switch]$AllowUnboundedSupervisor" in runtime
    assert "[int]$RunCount = 10" in wrapper
    assert "[int]$RunCount = 10" in installer
    assert "[int]$RunCount = 10" in runtime
    assert "[int]$MaxCycles = 1" in wrapper
    assert "[int]$MaxCycles = 1" in installer
    assert "[int]$MaxCycles = 1" in runtime
    assert "--enable-auto-improve" in wrapper
    assert "--enable-auto-improve" in runtime
    assert "--runtime-lease" in wrapper
    assert "--runtime-lease" in runtime
    assert "--max-cycles" in wrapper
    assert "--max-cycles" in runtime
    assert "--allow-unbounded-supervisor" in wrapper
    assert "--allow-unbounded-supervisor" in runtime
    assert "-RuntimeLease" in installer
    assert "-AutoImprove" in installer
    assert "-MaxCycles" in installer
    assert "-AllowUnboundedSupervisor" in installer
    assert "set \"PS_USERNAME=$RuntimeLeaseAccount\"" in wrapper
    assert "set PS_USERNAME=$runtimeLeaseAccount" in runtime
    assert "[switch]$AutoImprove" in runtime.split("function Start-BattleSession", 1)[1].split(")", 1)[0]
    assert "Rotate-LogFileIfLarge" in wrapper
    assert "Rotate-LogFileIfLarge" in runtime
    assert "[int]$MaxConcurrentBattles = 3" in wrapper
    assert "[int]$MaxConcurrentBattles = 3" in installer
    assert "[int]$MaxConcurrentBattles = 3" in runtime
    assert '$env:LOSS_TRIGGERED_DRAIN = "0"' in wrapper
    assert '$env:BATTLE_STATS_MAX_ENTRIES = "5000"' in wrapper
    assert '$env:BOT_LOG_TO_FILE = "1"' in wrapper


def test_jigglypuff_control_exposes_auto_improve_start_flag():
    text = (ROOT / "scripts" / "jigglypuff_devstream_control.py").read_text(encoding="utf-8")

    assert 'start.add_argument("--enable-auto-improve", action="store_true")' in text
    assert 'start.add_argument("--run-count", type=int, default=DEFAULT_RUN_COUNT)' in text
    assert 'start.add_argument("--max-cycles", type=int, default=DEFAULT_SUPERVISOR_MAX_CYCLES)' in text
    assert 'start.add_argument("--allow-unbounded-supervisor", action="store_true")' in text
    assert '"autoImprove": enable_auto_improve' in text
    assert '"maxCycles": max_cycles' in text
    assert '"allowUnboundedSupervisor": allow_unbounded_supervisor' in text
    assert 'powershell_args.append("-AutoImprove")' in text
    assert '"-MaxCycles", str(max_cycles)' in text
    assert 'powershell_args.append("-AllowUnboundedSupervisor")' in text


def test_legacy_windows_auto_pull_is_fail_closed():
    player_loop = (ROOT / "infrastructure" / "windows" / "player_loop.bat").read_text(encoding="utf-8")
    deploy_update = (ROOT / "infrastructure" / "windows" / "deploy_update.bat").read_text(encoding="utf-8")

    assert 'set "AUTO_PULL=%FOULER_PLAY_ENABLE_AUTO_PULL%"' in player_loop
    assert "FOULER_PLAY_ENABLE_DEPLOY_UPDATE=1" in player_loop
    assert "refusing to continue legacy player loop" in player_loop
    assert "FOULER_PLAY_ENABLE_DEPLOY_UPDATE" in deploy_update
    assert "FOULER_PLAY_PULL_REMOTE" in deploy_update
    assert "FOULER_PLAY_PULL_BRANCH" in deploy_update
    assert "git status --porcelain --untracked-files=no" in deploy_update
    assert 'git merge --ff-only "FETCH_HEAD"' in deploy_update
    assert "git pull origin %BRANCH%" not in deploy_update
    assert "set BRANCH=master" not in deploy_update


def test_legacy_onlogon_task_install_requires_explicit_sentinel():
    text = (ROOT / "infrastructure" / "windows" / "install_task.bat").read_text(encoding="utf-8")

    assert "FOULER_PLAY_ENABLE_LEGACY_ONLOGON_TASK" in text
    assert "legacy on-logon live battle task installation is disabled" in text
