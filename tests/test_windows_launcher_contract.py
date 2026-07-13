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
    assert "set FOULER_PLAY_ENABLE_AUTO_IMPROVE=$autoImproveEnv" in text
    assert "set BATTLE_STATS_MAX_ENTRIES=5000" in text
    assert '$_.CommandLine -match "streaming[\\\\/]+serve_obs_page\\.py"' in text
    assert '$_.CommandLine -match "streaming[\\\\/]+run_obs_server_service\\.py"' in text
    assert '$_.CommandLine -match "search_ladder" -and' in text
    assert '$_.CommandLine -match "devstream_session\\.py" -and $_.CommandLine -match "\\bsupervise\\b"' in text
    assert '$_.CommandLine -match $escapedRepo -and' in text
    assert "function Redact-CommandLine" in text
    assert "Redact-CommandLine -CommandLine $_.CommandLine" in text
    assert "function Get-LogicalProcessSummary" in text
    assert "[switch]$NoWrite" in text
    assert "function Get-ProducerEvidence" in text
    assert "fouler-play-runtime-producer/v1" in text
    assert "expectedHostMatched" in text
    assert "JIGGLYPUFF runtime status was produced on unexpected host" in text
    assert "proofArtifact" in text
    assert "Get-Status -NoWrite:$NoWrite" in text
    assert "battleSupervisor" in text
    assert "leafCount" in text
    assert "multiple Fouler OBS HTTP servers are running" in text
    assert "multiple Fouler battle supervisors are running" in text
    assert 'if ($Path -eq "/health")' in text
    assert "function Invoke-CurlEndpoint" in text
    assert "probe = \"curl.exe\"" in text
    assert "invokeWebRequestError" in text
    assert '$rel -eq "battle_stats.json"' in text
    assert "battleCount = $battles.Count" in text
    assert "lastBattle = $lastBattle" in text
    assert '"scripts\\devstream_session.py"' in text
    assert '"supervise"' in text
    assert '"--run-count", "$RunCount"' in text
    assert '"--max-concurrent-battles", "$MaxConcurrentBattles"' in text
    assert '"--max-cycles", "$MaxCycles"' in text
    assert "Test-RuntimeLease -RunCount $RunCount -MaxConcurrentBattles $MaxConcurrentBattles -MaxCycles $MaxCycles" in text
    assert "Start-BattleSession -RunCount $RunCount -MaxConcurrentBattles $MaxConcurrentBattles -MaxCycles $MaxCycles -RuntimeLease $RuntimeLease -AutoImprove:$AutoImprove" in text
    assert "function Get-RuntimeLeaseAccount" in text
    assert '"PS_USERNAME", "SHOWDOWN_USER_ID", "SHOWDOWN_ACCOUNTS", "FOULER_ACTIVE_ACCOUNT"' in text
    assert "ConvertTo-CmdSetAssignment -Name $envName -Value $leaseAccount" in text
    assert "call start_one_touch.bat" not in text


def test_process_snapshot_recognizes_current_account_and_service_owned_obs_runtime():
    text = (ROOT / "scripts" / "fouler_process_snapshot.ps1").read_text(encoding="utf-8")

    assert "LEBOTJAMESXD00N" not in text
    assert '($Cmd -like "*--bot-mode*search_ladder*")' in text
    assert '($Cmd -like "*run_obs_server_service.py*")' in text
    assert "obsHttpLogicalCount" in text
    assert "obsHttpRootPids" in text


def test_runtime_account_authority_has_no_retired_operational_defaults():
    operational_paths = [
        ROOT / "scripts" / "devstream_session.py",
        ROOT / "scripts" / "fouler_continuous_daemon.ps1",
        ROOT / "scripts" / "fouler_keepalive.ps1",
        ROOT / "scripts" / "fouler_lease_autorenew.ps1",
        ROOT / "scripts" / "fouler_mission_monitor.py",
        ROOT / "scripts" / "run_improve_window.ps1",
        ROOT / "infrastructure" / "improve_agent.py",
    ]

    for path in operational_paths:
        text = path.read_text(encoding="utf-8")
        assert "LEBOTJAMESXD00N" not in text, path
        assert "npctypebeat" not in text, path
        assert "thepeakmons" not in text, path

    continuous = operational_paths[1].read_text(encoding="utf-8")
    renew = operational_paths[3].read_text(encoding="utf-8")
    assert "FOULER_ALLOW_LEGACY_CONTINUOUS_DAEMON" in continuous
    assert "FOULER_ENABLE_LEGACY_LEASE_AUTORENEW" in renew
    assert "account-season.json" in continuous
    assert "account-season.json" in renew


def test_keepalive_rechecks_monitor_owned_stop_loss_instead_of_parking_forever():
    text = (ROOT / "scripts" / "fouler_keepalive.ps1").read_text(encoding="utf-8")

    assert "function Invoke-MissionMonitorRepair" in text
    assert "STOP-LOSS-RECOVERY-CHECK" in text
    assert "A monitor-owned stop marker is a tripwire, not a permanent operator hold." in text
    assert "BLOCKED: 0 clients and supervisor.stop is present" not in text


def test_obs_server_task_runs_via_scheduler_owned_powershell_wrapper():
    text = (ROOT / "scripts" / "install_obs_server_task.ps1").read_text(encoding="utf-8")

    assert "$TaskExecute" in text
    assert "$TaskExecute = $PowerShell" in text
    assert "start_obs_server_task.ps1" in text
    assert "-Foreground" in text
    assert "jigglypuff-obs-server.log" in text
    assert "jigglypuff-obs-server.err.log" in text
    assert "jigglypuff-obs-wrapper.log" not in text
    assert "jigglypuff-obs-wrapper.err.log" not in text
    assert "$TaskArguments" in text
    assert "New-ScheduledTaskAction -Execute $TaskExecute -Argument $TaskArguments" in text
    assert "New-ScheduledTaskTrigger -AtStartup" in text
    assert "LogonType S4U" in text
    assert "-AllowStartIfOnBatteries" in text
    assert "-DontStopIfGoingOnBatteries" in text
    assert "-DontStopOnIdleEnd" in text
    assert "schedulerOwnedForeground" in text
    assert "function Get-ManagedObsServerProcess" in text
    assert "managedPid" in text
    assert "healthEndpointOk" in text
    assert "[switch]$SkipHttpProbe" in text
    assert "healthProbeSkipped = [bool]$SkipHttpProbe" in text
    assert "Get-ObsTaskStatus -SkipHttpProbe:$SkipHttpProbe" in text
    assert "Get-CimInstance" not in text
    assert "Win32_Process" not in text
    assert "stderrTail" in text
    assert "lastTaskResult" in text
    assert "OBS_WS_PASSWORD" not in text
    assert "function Rotate-LogFile" in text
    assert "archive" in text


def test_obs_server_service_is_the_durable_lifecycle_owner():
    text = (ROOT / "scripts" / "install_obs_server_service.ps1").read_text(encoding="utf-8")

    assert "HERMES-FoulerObsServer" in text
    assert "C:\\ProgramData\\HERMES\\bin\\nssm.exe" in text
    assert '"AppExit", "Default", "Restart"' in text
    assert '"Start", "SERVICE_AUTO_START"' in text
    assert '"AppNoConsole", "1"' in text
    assert "FOULER_OBS_LIFECYCLE_OWNER=windows-service" in text
    assert "streaming\\run_obs_server_service.py" in text
    assert 'Invoke-Nssm -Arguments @("set", $ServiceName, "Application", $Python)' in text
    assert "DisableLegacyTasks" in text
    assert "stop_obs_server_tree.py" in text
    assert '"HERMES-FoulerObsKeepAlive", "HERMES-FoulerObsServer"' in text
    assert 'lifecycleOwner = "windows-service"' in text
    assert "Save-RollbackBackup" in text
    assert "Start Streaming" not in text
    assert "TWITCH" not in text.upper()


def test_obs_server_service_entrypoint_loads_authority_and_runs_in_process():
    text = (ROOT / "streaming" / "run_obs_server_service.py").read_text(encoding="utf-8")

    assert 'LEASE_PATH = TRUTH_DIR / "runtime-lease.json"' in text
    assert '_install_service_console_signal_handlers()' in text
    assert '("SIGINT", "SIGBREAK")' in text
    assert 'publish_latest=False' in text
    assert '"FOULER_OBS_WS_DISABLED": "1"' in text
    assert '"FP_PARENT_PID": "0"' in text
    assert '"FOULER_ACTIVE_ACCOUNT"' in text
    assert 'runpy.run_path(str(ROOT / "streaming" / "serve_obs_page.py"), run_name="__main__")' in text
    assert '"service-entrypoint-started"' in text
    assert '"python-exited"' in text
    assert "Start Streaming" not in text
    assert "TWITCH" not in text.upper()


def test_obs_server_tree_stop_is_project_scoped_and_explicit():
    text = (ROOT / "scripts" / "stop_obs_server_tree.py").read_text(encoding="utf-8")

    assert "serve_obs_page.py" in text
    assert "--project-dir" in text
    assert '"--execute"' in text
    assert "process.cwd()" in text
    assert '"nssm.exe", "services.exe", "svchost.exe"' in text
    assert "psutil.process_iter" in text
    assert "sys.modules.setdefault(\"_wmi\", None)" in text
    assert "subprocess" not in text


def test_obs_server_task_wrapper_loads_obs_secret_without_printing_it():
    text = (ROOT / "scripts" / "start_obs_server_task.ps1").read_text(encoding="utf-8")

    assert "hermes-devstream\\secrets.env" in text
    assert "OBS_WS_PASSWORD" in text
    assert "OBS_WEBSOCKET_PASSWORD" in text
    assert "HERMES_OBS_WEBSOCKET_PASSWORD" in text
    assert "FOULER_OBS_WS_DISABLED" in text
    assert "function Resolve-RuntimeLeasePath" in text
    assert "function Get-RuntimeLeaseAccount" in text
    assert "FOULER_RUNTIME_LEASE_PATH" in text
    assert "[Environment]::SetEnvironmentVariable(\"PS_USERNAME\", $leaseAccount, \"Process\")" in text
    assert "[Environment]::SetEnvironmentVariable(\"SHOWDOWN_USER_ID\", $leaseAccount, \"Process\")" in text
    assert "[Environment]::SetEnvironmentVariable(\"SHOWDOWN_ACCOUNTS\", $leaseAccount, \"Process\")" in text
    assert "LEBOTJAMESXD00N" not in text
    assert "FP_PARENT_PID" in text
    assert "[switch]$Foreground" in text
    assert "lifecycle-manager-owned and must run with -Foreground" in text
    assert "fouler-obs-launch/v1" in text
    assert "obs-server-launch.jsonl" in text
    assert "AppendAllText" in text
    assert "FOULER_OBS_LIFECYCLE_OWNER" in text
    assert 'Write-LaunchPhase -Phase "starting-python"' in text
    assert 'Write-LaunchPhase -Phase "python-exited"' in text
    assert '& $python -u "streaming\\serve_obs_page.py" 1>> $stdoutLog 2>> $stderrLog' in text
    assert '$ErrorActionPreference = "Continue"' in text
    assert "$commandInterpreter" not in text
    assert "$pythonCommand" not in text
    assert "Invoke-CimMethod" not in text
    assert "Get-CimInstance" not in text
    assert "Win32_Process" not in text
    assert "start_obs_server.cmd" not in text
    assert "SetEnvironmentVariable($_.Target, $value, \"Process\")" in text
    assert "Write-Output" not in text
    assert "Write-Host" not in text


def test_obs_server_keepalive_task_restarts_only_public_surface():
    keepalive = (ROOT / "scripts" / "fouler_obs_keepalive.ps1").read_text(encoding="utf-8")
    installer = (ROOT / "scripts" / "install_obs_server_keepalive_task.ps1").read_text(encoding="utf-8")

    assert "HERMES-FoulerObsServer" in keepalive
    assert "http://127.0.0.1:$Port/health" in keepalive
    assert "healthEndpointOk" in keepalive
    assert "schtasks.exe" in keepalive
    assert "/Run /TN $TaskName" in keepalive
    assert "/End /TN $TaskName" in keepalive
    assert "Get-ScheduledTask" not in keepalive
    assert "Start-ScheduledTask" not in keepalive
    assert "Stop-ScheduledTask" not in keepalive
    assert "stoppedStuckTask" in keepalive
    assert "stoppedStuckProcess" in keepalive
    assert ".pids\\obs_server.pid" in keepalive
    assert "function Get-ManagedObsServerStatus" in keepalive
    assert "startupGrace" in keepalive
    assert "[int]$HealthProbeAttempts = 6" in keepalive
    assert "[int]$HealthProbeIntervalSeconds = 8" in keepalive
    assert "[int]$ClosedPortProbeAttempts = 2" in keepalive
    assert "TimeoutSec $TimeoutSeconds" in keepalive
    assert "$probeAttemptsUsed -ge $ClosedPortProbeAttempts" in keepalive
    assert "probeAttemptsUsed = $probeAttemptsUsed" in keepalive
    assert "$beforeLifecycleHealthy" in keepalive
    assert "if (-not $beforeLifecycleHealthy)" in keepalive
    assert '"scheduler-not-owning-process"' in keepalive
    assert "lifecycleHealthy = $afterLifecycleHealthy" in keepalive
    assert "if (-not ($beforePort -and $beforeState))" not in keepalive
    assert "Stop-Process -Id $managed.processId -Force" in keepalive
    assert "Get-CimInstance" not in keepalive
    assert "Get-ServerOutputAgeSeconds" not in keepalive
    assert "PORT-BLOCKED-BUT-ALIVE" not in keepalive
    assert "netstat.exe" in keepalive
    assert "PS_PASSWORD" not in keepalive
    assert "HERMES-FoulerObsKeepAlive" in installer
    assert "New-ScheduledTaskTrigger -AtStartup" in installer
    assert "LogonType S4U" in installer
    assert "-AllowStartIfOnBatteries" in installer
    assert "-DontStopIfGoingOnBatteries" in installer
    assert "-DontStopOnIdleEnd" in installer
    assert "RepetitionInterval (New-TimeSpan -Minutes 1)" in installer
    assert "schedulerRunObserved" in installer
    assert "fallbackUsed" in installer
    assert "Get-ScheduledTaskInfo" not in installer
    assert "$statusPayload = Get-KeepaliveTaskStatus" in installer
    assert "$status = Get-KeepaliveTaskStatus" not in installer
    assert "/V /FO CSV" in installer
    assert "$TaskExecute = $PowerShell" in installer
    assert "jigglypuff-obs-keepalive.log" not in installer


def test_obs_listener_self_check_uses_a_valid_http_probe():
    text = (ROOT / "streaming" / "serve_obs_page.py").read_text(encoding="utf-8")

    assert 'psutil.process_iter(["pid", "ppid", "cmdline"])' in text
    assert "Get-CimInstance Win32_Process" not in text
    assert 'sys.modules.setdefault("_wmi", None)' in text
    assert 'b"GET /health HTTP/1.1\\r\\n"' in text
    assert 'b"Connection: close\\r\\n\\r\\n"' in text
    assert "unexpected health response" in text
    assert "HTTP self-probe" in text


def test_boot_watchdog_installs_startup_service_account_task():
    installer = (ROOT / "infrastructure" / "windows" / "install_fouler_boot_watchdog_task.ps1").read_text(encoding="utf-8")
    watchdog = (ROOT / "infrastructure" / "windows" / "fouler_boot_watchdog.ps1").read_text(encoding="utf-8")

    assert "New-ScheduledTaskTrigger -AtStartup" in installer
    assert 'New-ScheduledTaskPrincipal -UserId "SYSTEM" -LogonType ServiceAccount -RunLevel Highest' in installer
    assert "StartWhenAvailable" in installer
    assert "starting the Fouler boot watchdog requires explicit positive -RunCount and -MaxCycles" in installer
    assert "HERMES-FoulerBootWatchdog" in installer
    assert "ServiceAccount" in installer
    assert "Interactive" not in installer
    assert "Start Streaming" not in installer
    assert "TWITCH" not in installer.upper()

    assert "scripts\\fouler_jigglypuff_runtime.ps1" in watchdog
    assert "scripts\\fouler_mission_monitor.py" in watchdog
    assert "--start-gate-only" in watchdog
    assert "--run-count" in watchdog
    assert "--max-cycles" in watchdog
    assert "--max-concurrent-battles" in watchdog
    assert "checking Fouler mission start gate before launch" in watchdog
    assert "blocked: Fouler mission start gate" in watchdog
    assert '"-Command", "start"' in watchdog
    assert '"-Execute"' in watchdog
    assert "supervisor.stop" in watchdog
    assert "FOULER_PLAY_ENABLE_AUTO_IMPROVE" not in watchdog
    assert "git push" not in watchdog
    assert "Start Streaming" not in watchdog


def test_fouler_deku_event_producer_has_no_network_transport():
    producer = (ROOT / "scripts" / "fouler_deku_event_producer.ps1").read_text(encoding="utf-8")

    assert "infrastructure\\event_poster.py" in producer
    assert '"--once"' in producer
    assert "fouler-deku-event-producer.lock" in producer
    assert "Invoke-WebRequest" not in producer
    assert "discord.com" not in producer
    assert "ssh" not in producer.lower()


def test_battle_supervisor_defaults_to_one_rated_battle():
    wrapper = (ROOT / "scripts" / "start_battle_supervisor_task.ps1").read_text(encoding="utf-8")
    installer = (ROOT / "scripts" / "install_battle_supervisor_task.ps1").read_text(encoding="utf-8")
    runtime = (ROOT / "scripts" / "fouler_jigglypuff_runtime.ps1").read_text(encoding="utf-8")

    assert "[switch]$AutoImprove" in wrapper
    assert "[switch]$AutoImprove" in installer
    assert "[switch]$AutoImprove" in runtime
    assert "--enable-auto-improve" in wrapper
    assert "--enable-auto-improve" in runtime
    assert "--max-cycles" in wrapper
    assert "--max-cycles" in runtime
    assert "-MaxCycles" in installer
    assert "-AutoImprove" in installer
    assert "-Foreground" in installer
    assert "Wait-BattleSupervisorProcess" in installer
    assert "Start-ForegroundWrapperProcess" in installer
    assert "blocked-supervisor-launch" in installer
    assert "battle supervisor launch did not produce a live devstream_session.py supervise process" in installer
    assert "Rotate-LogFileIfLarge" in wrapper
    assert "Rotate-LogFileIfLarge" in runtime
    assert "function Get-RuntimeLeaseAccount" in wrapper
    assert "ConvertTo-CmdSetAssignment -Name $envName -Value $leaseAccount" in wrapper
    assert '"PS_USERNAME", "SHOWDOWN_USER_ID", "SHOWDOWN_ACCOUNTS", "FOULER_ACTIVE_ACCOUNT"' in wrapper
    assert "[int]$MaxConcurrentBattles = 3" in wrapper
    assert "[int]$MaxConcurrentBattles = 3" in installer
    assert "[int]$MaxConcurrentBattles = 3" in runtime
    assert '$env:LOSS_TRIGGERED_DRAIN = "0"' in wrapper
    assert '$env:BATTLE_STATS_MAX_ENTRIES = "5000"' in wrapper
    assert '$env:BOT_LOG_TO_FILE = "1"' in wrapper
    assert '$env:FOULER_PLAY_ENABLE_AUTO_IMPROVE = if ($AutoImprove) { "1" } else { "0" }' in wrapper
    assert '[string]$LoopBreak = "0"' in wrapper
    assert '[string]$LoopBreak = "0"' in installer
    assert "-LoopBreak {7}" in installer
    assert '"-LoopBreak"' in installer
    assert '$env:FOULER_LOOP_BREAK = $LoopBreak' in wrapper


def test_legacy_player_loop_is_clearly_quarantined():
    text = (ROOT / "infrastructure" / "windows" / "player_loop.bat").read_text(encoding="utf-8")

    assert "LEGACY FALLBACK ONLY" in text
    assert "This wrapper intentionally loops forever" in text
    assert "devstream_session.py supervise" in text
    assert "goto loop_start" in text


def test_linux_player_loop_delegates_to_bounded_supervisor():
    text = (ROOT / "infrastructure" / "linux" / "player_loop.sh").read_text(encoding="utf-8")

    assert "scripts/devstream_session.py" in text
    assert '"supervise"' in text
    assert '"--max-cycles" "$MAX_CYCLES"' in text
    assert "RUN_COUNT or BATCH_SIZE must be a positive bounded value" in text
    assert "FOULER_PLAY_ENABLE_AUTO_IMPROVE" in text
    assert "python run.py" not in text
    assert '"run.py"' not in text
    assert "while true" not in text
    assert "git push" not in text
    assert "git commit" not in text


def test_jigglypuff_control_exposes_auto_improve_start_flag():
    text = (ROOT / "scripts" / "jigglypuff_devstream_control.py").read_text(encoding="utf-8")

    assert 'start.add_argument("--enable-auto-improve", action="store_true")' in text
    assert '"autoImprove": enable_auto_improve' in text
    assert 'start.add_argument("--max-cycles", type=int, default=0)' in text
    assert 'powershell_args.append("-AutoImprove")' in text


def test_jigglypuff_control_read_only_status_is_remote_no_write():
    text = (ROOT / "scripts" / "jigglypuff_devstream_control.py").read_text(encoding="utf-8")

    assert "no_remote_write: bool = False" in text
    assert 'if no_remote_write and action == "status"' in text
    assert 'powershell_args.append("-NoWrite")' in text
    assert "remoteStatusWriteSkipped" in text
    assert "skippedNoWrite" in text
