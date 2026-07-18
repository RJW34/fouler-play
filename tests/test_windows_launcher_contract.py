import json
import shutil
import subprocess
from pathlib import Path

import pytest


ROOT = Path(__file__).resolve().parents[1]


def test_retired_one_touch_launchers_fail_closed_without_process_mutation():
    paths = [
        ROOT / "start_one_touch.bat",
        ROOT / "start_bot.bat",
        ROOT / "infrastructure" / "windows" / "install_task.bat",
    ]

    for path in paths:
        text = path.read_text(encoding="utf-8")
        assert "[RETIRED]" in text, path
        assert "exit /b 2" in text, path
        assert "run.py" not in text, path
        assert "Stop-Process" not in text, path
        assert "Get-CimInstance" not in text, path
        assert "schtasks /create" not in text, path


def test_retired_monitor_and_deploy_cluster_cannot_spawn_or_report():
    watchdog = (ROOT / "scripts" / "watchdog.ps1").read_text(encoding="utf-8")
    monitor = (ROOT / "bot_monitor.py").read_text(encoding="utf-8")
    restart = (ROOT / "scripts" / "safe_restart_monitor.sh").read_text(encoding="utf-8")
    deploy = (ROOT / "auto_deploy.sh").read_text(encoding="utf-8")

    assert "[RETIRED]" in watchdog and "exit 2" in watchdog
    assert "Start-Process" not in watchdog
    assert "Remove-Item" not in watchdog

    assert "[RETIRED]" in monitor and "return 2" in monitor
    assert "queue_event" not in monitor
    assert "aiohttp" not in monitor
    assert "subprocess" not in monitor

    for text in (restart, deploy):
        assert "[RETIRED]" in text
        assert "exit 2" in text
        assert "nohup" not in text
        assert "systemctl" not in text


def test_retired_direct_runtime_and_stream_launchers_are_inert():
    paths = (
        ROOT / "launch.py",
        ROOT / "launch_overnight.bat",
        ROOT / "start_batch.ps1",
        ROOT / "start_bot.sh",
        ROOT / "streaming" / "stream_controller.sh",
        ROOT / "streaming" / "start_stream_server.sh",
    )
    forbidden = (
        "run.py",
        "Start-Process",
        "subprocess",
        "openclaw",
        "ffmpeg",
        "rtmp://",
        "twitchstreamingkey",
        "DISCORD_BATTLES_WEBHOOK_URL",
    )

    for path in paths:
        text = path.read_text(encoding="utf-8")
        assert "retired" in text.lower(), path
        assert "exit 2" in text or "exit /b 2" in text or "return 2" in text, path
        for token in forbidden:
            assert token not in text, (path, token)


def test_retired_stream_output_controllers_cannot_start_obs_or_twitch():
    tombstones = (
        ROOT / "streaming" / "clean_stream.py",
        ROOT / "streaming" / "stream_server.py",
        ROOT / "streaming" / "obs_controller.py",
    )
    integrations = (
        ROOT / "streaming" / "clean_stream_integration.py",
        ROOT / "streaming" / "obs_integration.py",
        ROOT / "streaming" / "stream_integration.py",
    )

    for path in tombstones:
        text = path.read_text(encoding="utf-8")
        assert "retired" in text.lower(), path
        assert "return 2" in text, path
        assert "subprocess" not in text, path
        assert "rtmp://" not in text.lower(), path
        assert "TWITCH_STREAM_KEY" not in text, path
        assert "StartStream" not in text, path
        assert "password" not in text.lower(), path

    for path in integrations:
        text = path.read_text(encoding="utf-8")
        assert "retired" in text.lower(), path
        assert "aiohttp" not in text, path
        assert "http://" not in text, path

    watcher = (ROOT / "infrastructure" / "battle-stats-watcher.sh").read_text(encoding="utf-8")
    assert "retired" in watcher.lower()
    assert "exit 2" in watcher
    assert "webhook" not in watcher.lower()
    assert "curl" not in watcher

    websocket = (ROOT / "streaming" / "obs_websocket.py").read_text(encoding="utf-8")
    assert '"StartStream"' not in websocket
    assert '"StopStream"' not in websocket


def test_legacy_reporters_and_service_watchers_are_fail_closed():
    reporters = (
        ROOT / "infrastructure" / "battle-stats-watcher.sh",
        ROOT / "infrastructure" / "run_replay_analyzer.sh",
        ROOT / "infrastructure" / "replay_analyzer.py",
        ROOT / "replay_analysis" / "analysis_poster.py",
        ROOT / "replay_analysis" / "generate_weekly_report.py",
    )
    for path in reporters:
        text = path.read_text(encoding="utf-8")
        assert "retired" in text.lower(), path
        assert "exit 2" in text or "return 2" in text, path
        assert "requests.post" not in text, path
        assert "curl" not in text, path

    services = (
        ROOT / "fouler-pipeline.service",
        ROOT / "fouler-play-watcher.service",
        ROOT / "infrastructure" / "linux" / "fouler-play.service",
    )
    for path in services:
        text = path.read_text(encoding="utf-8")
        assert "RETIRED" in text, path
        assert "RefuseManualStart=yes" in text, path
        assert "ExecStart=/bin/false" in text, path
        assert "Restart=" not in text, path

    pipeline = (ROOT / "pipeline.py").read_text(encoding="utf-8")
    assert "pipeline.send_discord_notification(report)" not in pipeline
    assert "direct Discord delivery is retired" in pipeline


def test_retired_scheduler_competitors_are_inert():
    powershell_paths = (
        ROOT / "scripts" / "fouler_daemon_keepalive.ps1",
        ROOT / "scripts" / "fouler_lease_autorenew.ps1",
        ROOT / "scripts" / "fouler_keepalive.ps1",
        ROOT / "infrastructure" / "windows" / "fouler_boot_watchdog.ps1",
        ROOT / "infrastructure" / "windows" / "install_fouler_boot_watchdog_task.ps1",
        ROOT / "scripts" / "fouler_obs_keepalive.ps1",
        ROOT / "scripts" / "install_obs_server_keepalive_task.ps1",
        ROOT / "scripts" / "install_obs_server_task.ps1",
        ROOT / "scripts" / "start_obs_server_task.ps1",
        ROOT / "scripts" / "commit_hog_watchdog.ps1",
        ROOT / "scripts" / "install_commit_hog_watchdog_task.ps1",
        ROOT / "scripts" / "matchup_ab_report.ps1",
        ROOT / "scripts" / "install_matchup_ab_report_task.ps1",
    )
    shell_paths = (
        ROOT / "infrastructure" / "linux" / "stream_watchdog.sh",
        ROOT / "infrastructure" / "linux" / "install_service.sh",
    )
    forbidden = (
        "Start-Process",
        "Register-ScheduledTask",
        "New-ScheduledTaskAction",
        "StartStream",
        "run.py",
        "queue_event",
        "requests.post",
        "systemctl --user enable",
    )

    for path in (*powershell_paths, *shell_paths):
        text = path.read_text(encoding="utf-8")
        assert "retired" in text.lower(), path
        assert "exit 2" in text, path
        for token in forbidden:
            assert token not in text, (path, token)

    enqueue = (ROOT / "scripts" / "commit_hog_enqueue.py").read_text(encoding="utf-8")
    assert "retired" in enqueue.lower()
    assert "return 2" in enqueue
    assert "queue_event" not in enqueue


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
    assert '"--require-deployment-receipt"' in text
    assert '"--verify-deployment-checkout"' in text
    assert "$valid = [bool]($result.ok -and $null -ne $payload -and $payload.ok)" in text
    assert "Start-BattleSession -RunCount $RunCount -MaxConcurrentBattles $MaxConcurrentBattles -MaxCycles $MaxCycles -RuntimeLease $RuntimeLease -LeaseAccount $leaseAccount -AutoImprove:$AutoImprove" in text
    assert "$leaseAccount = [string]$lease.payload.lease.account" in text
    assert "function Get-RuntimeLeaseAccount" not in text
    assert '"PS_USERNAME", "SHOWDOWN_USER_ID", "SHOWDOWN_ACCOUNTS", "FOULER_ACTIVE_ACCOUNT"' in text
    assert "ConvertTo-CmdSetAssignment -Name $envName -Value $LeaseAccount" in text
    assert "call start_one_touch.bat" not in text


def test_process_snapshot_recognizes_current_account_and_service_owned_obs_runtime():
    text = (ROOT / "scripts" / "fouler_process_snapshot.ps1").read_text(encoding="utf-8")

    assert "LEBOTJAMESXD00N" not in text
    assert '($Cmd -like "*--bot-mode*search_ladder*")' in text
    assert '($Cmd -like "*run_obs_server_service.py*")' in text
    assert "obsHttpLogicalCount" in text
    assert "obsHttpRootPids" in text


def retired_runtime_account_authority_reference():
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
    assert "devstream_session.py supervise" in continuous
    assert "account-season.json" in renew


def retired_keepalive_contract_reference():
    text = (ROOT / "scripts" / "fouler_keepalive.ps1").read_text(encoding="utf-8")

    assert "function Invoke-MissionMonitorRepair" in text
    assert "STOP-LOSS-RECOVERY-CHECK" in text
    assert "A monitor-owned stop marker is a tripwire, not a permanent operator hold." in text
    assert "BLOCKED: 0 clients and supervisor.stop is present" not in text


def retired_obs_server_task_contract_reference():
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
    assert "C:\\ProgramData\\HERMES-ObsServer\\bin\\nssm.exe" in text
    assert "ExpectedNssmSha256" in text
    assert "independently verified 64-character SHA-256" in text
    assert "Get-RegularFileSnapshot" in text
    assert "Protect-AdminDirectory" in text
    assert "NT SERVICE\\HERMES-FoulerObsServer" in text
    # The service account is pinned via sc.exe config and the final NSSM ObjectName
    # (no LocalSystem fallback). The obsolete passwordless empty-string arg is gone: a
    # virtual/service account takes no password and PowerShell 5.1 drops the empty
    # native arg, which is why the certified sc.exe path failed at runtime.
    assert '& $sc config $ServiceName "obj=" $ServiceAccount' in text
    assert '"ObjectName", $ServiceAccount' in text
    assert '"password=" ""' not in text
    assert '"ObjectName", "LocalSystem"' not in text
    # Backup precedes stop + delete of any existing service, which precedes clean
    # NSSM (re)installation -- closing the same legacy-service repair gap as the broker.
    obs_backup = text.index("$backup = Save-RollbackBackup")
    obs_stop = text.index("& $sc stop $ServiceName")
    obs_delete = text.index("& $sc delete $ServiceName")
    obs_install = text.index('Invoke-Nssm -Arguments @("install", $ServiceName')
    assert obs_backup < obs_stop < obs_delete < obs_install
    # Immediately after install: disabled start mode, unrestricted SID, intended account.
    obs_disabled = text.index('& $sc config $ServiceName "start=" "disabled"')
    obs_sidtype = text.index("& $sc sidtype $ServiceName unrestricted")
    obs_obj = text.index('& $sc config $ServiceName "obj=" $ServiceAccount')
    assert obs_install < obs_disabled < obs_sidtype < obs_obj
    # Post-install identity validation runs with ExpectedStartMode Disabled.
    assert 'Assert-ObsBaseServiceIdentity -ExpectedStartMode "Disabled"' in text
    assert obs_obj < text.index('Assert-ObsBaseServiceIdentity -ExpectedStartMode "Disabled"')
    assert "SERVICE_DISABLED" in text
    assert "TEMP=$RuntimeTempRoot" in text
    assert "TMP=$RuntimeTempRoot" in text
    assert "FOULER_RUNTIME_TEMP_ROOT=$RuntimeTempRoot" in text
    assert "nssm.exe.sha256" in text
    assert "nssm-acl.txt" in text
    backup = text.index("$backup = Save-RollbackBackup")
    publication = text.index("Write-AtomicBytes -Bytes $sourceSnapshot.Bytes -Destination $StableNssm")
    retire_tasks = text.index("Disable-LegacyObsTasks", backup)
    assert backup < publication
    assert backup < retire_tasks
    assert "Copy-Item -LiteralPath $sourceNssm" not in text
    assert "existing/PATH NSSM discovery is forbidden" in text
    assert "published NSSM hash changed immediately before execution" in text
    assert "rollback NSSM tool failed its hash pin immediately before execution" in text
    assert "@(& $StableNssm dump" not in text
    assert '"AppExit", "Default", "Restart"' in text
    assert '"SERVICE_AUTO_START"' in text
    assert '"SERVICE_DEMAND_START"' not in text
    assert "Start-Service" not in text
    assert "-Start is retired" in text
    assert '"AppNoConsole", "1"' in text
    assert "FOULER_OBS_LIFECYCLE_OWNER=windows-service" in text
    assert "streaming\\run_obs_server_service.py" in text
    assert 'Invoke-Nssm -Arguments @("set", $ServiceName, "Application", $Python)' in text
    assert '"AppParameters", $ObsServiceArguments' in text
    assert '"OBS_SERVER_HOST=127.0.0.1"' in text
    assert '"OBS_SERVER_PORT=8777"' in text
    assert "DisableLegacyTasks" in text
    assert "ProvisionIdentityOnly" in text
    assert "Apply requires an explicit target -ProjectDir from a trusted installer copy" in text
    assert "OBS installer path" in text
    assert 'schemaVersion = "fouler-obs-service-identity/v1"' in text
    assert 'status = "identity-provisioned-disabled"' in text
    assert "executesReleaseCode = $false" in text
    assert "OBS identity provisioning must leave the service stopped and Disabled" in text
    identity_gate = text.index("if ($ProvisionIdentityOnly) {")
    runtime_acl = text.index("Protect-RuntimeWriteDirectory -Path $RuntimeStateRoot", identity_gate)
    assert identity_gate < runtime_acl
    assert "stop_obs_server_tree.py" not in text
    assert '"HERMES-FoulerObsKeepAlive", "HERMES-FoulerObsServer"' in text
    assert 'lifecycleOwner = "windows-service"' in text
    assert "Save-RollbackBackup" in text
    assert "Apply requires an elevated administrator PowerShell session" in text
    assert "Assert-ManifestedImmutableRelease" in text
    assert "^D:\\\\Releases\\\\fouler-play\\\\[0-9a-f]{40}$" in text
    assert "fouler-bootstrap-manifest/v1" in text
    assert "GetFileInformationByHandle" in text
    assert "NumberOfLinks -ne 1" in text
    assert "SetAccessRuleProtection($true, $false)" in text
    assert "/grant:r" not in text
    assert "/inheritance:r" not in text
    assert "Get-ObsProcessChain" in text
    assert "Assert-InstalledObsServiceIdentity" in text
    assert "canonical OBS service Application is not the pinned release venv Python" in text
    assert "canonical OBS service environment changed" in text
    assert "SCM/NSSM does not own exactly one exact OBS venv launcher" in text
    assert "OBS venv launcher does not own exactly one exact base-Python runtime" in text
    assert "OBS PID creation time does not match the exact base-Python runtime" in text
    assert "port8777ListenerPids" in text
    assert "OBS installation must leave the canonical service stopped and Automatic without activating it" in text
    assert "startsProcesses = $false" in text
    assert "legacyTaskRetirementMandatory = $true" in text
    assert "transaction failed closed" in text
    assert "Start Streaming" not in text
    assert "TWITCH" not in text.upper()


def test_obs_server_service_entrypoint_loads_authority_and_runs_in_process():
    text = (ROOT / "streaming" / "run_obs_server_service.py").read_text(encoding="utf-8")

    assert "LEASE_PATH = runtime_lease_path()" in text
    assert 'purpose="jigglypuff-runtime-start"' in text
    assert "require_deployment_receipt=True" in text
    assert "verify_deployment_checkout=True" in text
    assert "lease_environment(validation)" in text
    assert '"runtime-authority-blocked"' in text
    assert '"FOULER_RUNTIME_STATE_ROOT"' in text
    assert '_install_service_console_signal_handlers()' in text
    assert '("SIGINT", "SIGBREAK")' in text
    assert 'publish_latest=False' in text
    assert 'target["FOULER_OBS_WS_DISABLED"] = "1"' in text
    assert 'target.setdefault("FOULER_OBS_WS_DISABLED"' not in text
    assert 'LOOPBACK_HOST = "127.0.0.1"' in text
    assert "LOOPBACK_PORT = 8777" in text
    assert "WINDOWS_RELEASE_RE" in text
    assert 'r"^D:\\\\Releases\\\\fouler-play\\\\[0-9a-f]{40}$"' in text
    assert "WINDOWS_EXTERNAL_PATHS" in text
    assert "must equal the canonical protected path" in text
    assert '"FP_PARENT_PID": "0"' in text
    assert '"FOULER_ACTIVE_ACCOUNT"' in text
    assert 'kwargs["host"] = LOOPBACK_HOST' in text
    assert 'kwargs["port"] = LOOPBACK_PORT' in text
    assert 'runpy.run_path(str(ROOT / "streaming" / "serve_obs_page.py"), run_name="__main__")' in text
    assert "_run_public_surface()" in text
    assert "_redact_sensitive_values" in text
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


def retired_obs_server_task_wrapper_contract_reference():
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


def retired_obs_server_keepalive_contract_reference():
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


def retired_boot_watchdog_contract_reference():
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

    assert "is retired" in producer
    assert "DEKU-managed relay owns delivery" in producer
    assert "exit 2" in producer
    assert "infrastructure\\event_poster.py" not in producer
    assert "Invoke-WebRequest" not in producer
    assert "discord.com" not in producer
    assert "ssh" not in producer.lower()


def test_battle_supervisor_uses_the_fixed_live_pilot_authority_contract():
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
    assert "ClearStopFile" not in installer
    assert "ClearDrainRequest" not in installer
    assert "blocked-runtime-sentinel" in installer
    assert "blocked-runtime-sentinel-race" in installer
    assert "start does not clear operator sentinels" in installer
    assert "function Assert-BattleSupervisorRuntimeIdle" in installer
    assert "Wait-BattleSupervisorProcess" in installer
    assert "Start-ForegroundWrapperProcess" not in installer
    assert "installer-token fallback is forbidden" in installer
    assert "Protect-AdminDirectory -Path $BackupRoot" in installer
    assert "$env:TEMP = $RuntimeTempRoot" in wrapper
    assert "$env:TMP = $RuntimeTempRoot" in wrapper
    assert "New-ScheduledTaskTrigger -AtStartup" not in installer
    assert "New-ScheduledTaskTrigger -AtLogOn" not in installer
    # --- Bounded S4U task registered via the Task Scheduler COM API ---
    # Cross-user passwordless Register-ScheduledTask cannot register S4U for a
    # different account (Access Denied), so registration uses the COM API with an
    # ephemeral credential. Every security property below is preserved and, for the
    # installed task, independently revalidated.
    assert "-UserId $RuntimeAccount -LogonType S4U" not in installer
    # No autonomous triggers of any kind: no cmdlet triggers, no COM trigger creation.
    assert ".Triggers.Create(" not in installer
    # Principal is the runtime account, S4U logon (2), Limited run level (0).
    assert "$definition.Principal.UserId = $RuntimeAccount" in installer
    assert "$definition.Principal.LogonType = 2" in installer       # TASK_LOGON_S4U
    assert "$definition.Principal.RunLevel = 0" in installer        # TASK_RUNLEVEL_LUA (Limited)
    # RegisterTaskDefinition uses the runtime account and logon type 2 (S4U).
    assert "$root.RegisterTaskDefinition($TaskName, $definition, 6, $RuntimeAccount, $s4uSecret, 2, $null)" in installer
    # The AutoAdminLogon identity must exactly match the runtime account BEFORE the
    # ephemeral credential is read.
    assert "does not match the canonical runtime account" in installer
    identity_guard = installer.index("does not match the canonical runtime account")
    credential_read = installer.index("$s4uSecret = [string]$winlogon.DefaultPassword")
    assert identity_guard < credential_read
    # The credential is nonempty, used only to register, cleared in a finally, and
    # never printed, logged, or persisted.
    assert "if ([string]::IsNullOrEmpty($s4uSecret)) { throw" in installer
    finally_index = installer.index("finally {", credential_read)
    clear_index = installer.index("$s4uSecret = $null")
    assert credential_read < finally_index < clear_index
    for cred_line in installer.splitlines():
        if "$s4uSecret" in cred_line and any(
            sink in cred_line
            for sink in ("Write-", "Out-File", "Out-Host", "Set-Content", "Add-Content",
                         "ConvertTo-Json", "Export-", "Tee-Object", "echo ")
        ):
            raise AssertionError(f"registration credential must never be emitted to a sink: {cred_line!r}")
    # The installed-task validator checks principal SID equality, S4U logon, absence of
    # real triggers, and the exact action (executable, arguments, working directory).
    assert "function Assert-InstalledTaskIdentity" in installer
    validator_body = installer[installer.index("function Assert-InstalledTaskIdentity"):]
    assert "$expectedPrincipalSid" in validator_body and "$actualPrincipalSid" in validator_body
    assert "canonical Fouler supervisor task runtime principal changed" in validator_body
    assert '$task.Principal.LogonType, "S4U"' in validator_body
    assert "@($task.Triggers | Where-Object { $_ }).Count -ne 0" in validator_body
    assert "canonical Fouler supervisor task must not have autonomous triggers" in validator_body
    assert "canonical Fouler supervisor task must contain exactly one action" in validator_body
    assert "$action.Execute" in validator_body and "$TaskExecute" in validator_body
    assert "$action.Arguments" in validator_body and "$TaskArguments" in validator_body
    assert "$action.WorkingDirectory" in validator_body and "$ProjectDir" in validator_body
    assert "$null = Assert-InstalledTaskIdentity" in installer
    assert '"C:\\ProgramData\\HERMES\\authority\\fouler\\runtime-lease.json"' in installer
    assert '"C:\\ProgramData\\HERMES\\state\\fouler"' in wrapper
    assert '"C:\\ProgramData\\HERMES\\logs\\fouler"' in wrapper
    assert '$env:FOULER_ENV_FILE = [System.IO.Path]::GetFullPath($SecretEnvFile)' in wrapper
    assert "blocked-supervisor-launch" in installer
    assert "battle supervisor launch did not produce a live devstream_session.py supervise process" in installer
    assert "Rotate-LogFileIfLarge" not in wrapper
    assert "Rotate-LogFileIfLarge" in runtime
    assert "function Get-RuntimeLeaseAccount" not in wrapper
    assert "function Resolve-RuntimeLeasePath" not in wrapper
    assert '$leaseAccount = "$($leaseValidation.lease.account)".Trim()' in wrapper
    assert "ConvertTo-CmdSetAssignment" not in wrapper
    assert '$env:FOULER_ACCOUNT_SEASON_PATH = $AccountSeasonPath' in wrapper
    assert '$env:SEARCH_PARALLELISM = "2"' in wrapper
    assert '"C:\\ProgramData\\HERMES\\authority\\fouler\\account-season.json"' in wrapper
    assert "[int]$MaxConcurrentBattles = 3" in wrapper
    assert "[int]$MaxConcurrentBattles = 3" in installer
    assert "[int]$MaxConcurrentBattles = 3" in runtime
    assert "[int]$SearchParallelism = 2" in wrapper
    assert "[int]$SearchParallelism = 2" in installer
    assert "owner-locked live pilot MaxConcurrentBattles must equal 3" in wrapper
    assert "owner-locked live pilot SearchParallelism must equal 2" in wrapper
    assert '$env:LOSS_TRIGGERED_DRAIN = "0"' in wrapper
    assert '$env:BATTLE_STATS_MAX_ENTRIES = "5000"' in wrapper
    assert '$env:BOT_LOG_TO_FILE = "1"' in wrapper
    assert '$env:FOULER_PLAY_ENABLE_AUTO_IMPROVE = if ($AutoImprove) { "1" } else { "0" }' in wrapper
    assert '[string]$LoopBreak = "0"' in wrapper
    assert '[string]$LoopBreak = "0"' in installer
    assert "-LoopBreak {8}" in installer
    assert '"-LoopBreak"' in installer
    assert '$env:FOULER_LOOP_BREAK = $LoopBreak' in wrapper
    assert "function Test-AndApplyRuntimeLease" in wrapper
    assert "devstream_runtime_lease.py" in wrapper
    assert '"--source-commit", $sourceCommit.ToLowerInvariant()' in wrapper
    assert "$leaseValidation = Test-AndApplyRuntimeLease" in wrapper
    assert wrapper.index("$leaseValidation = Test-AndApplyRuntimeLease") < wrapper.index("# --- SINGLETON GUARD")
    assert "refusing incidental replacement" in wrapper
    assert "Stop-Process" not in wrapper
    assert "Start-Process" not in wrapper
    assert "may launch only in the canonical scheduled-task foreground mode" in wrapper
    assert "function Test-RuntimeLeaseForStart" in installer
    assert '"--source-commit", $sourceCommit.ToLowerInvariant()' in installer
    assert '"--deployment-id", $env:FOULER_DEPLOYMENT_ID' in installer
    assert installer.index("$startLeaseCheck = Test-RuntimeLeaseForStart") < installer.index("$backup = Save-TaskBackup")
    assert "ProjectDir must be an immutable D:\\Releases\\fouler-play\\<commit> release" in installer
    assert "supervisor mutation requires an explicit target -ProjectDir from a trusted installer copy" in installer
    assert "supervisor installer path" in installer
    assert "Assert-ManifestedImmutableRelease" in installer
    assert "^D:\\\\Releases\\\\fouler-play\\\\[0-9a-f]{40}$" in installer
    assert "^D:\\\\Releases\\\\fouler-play\\\\[0-9a-f]{40}$" in wrapper
    assert "fouler-bootstrap-manifest/v1" in installer
    assert "GetFileInformationByHandle" in installer
    assert "NumberOfLinks -ne 1" in installer
    assert "SetAccessRuleProtection($true, $false)" in installer
    assert "/grant:r" not in installer
    assert "/inheritance:r" not in installer
    assert "Assert-NoPathOverlap" in installer
    assert 'Join-Path $ProjectDir ".pids"' not in installer
    assert 'Get-Command "py"' not in installer
    assert "git.exe" not in installer
    assert "& $Py -I -B @validatorArgs" in installer
    assert "RuntimeCacheRoot" in installer
    assert "battle supervisor mutation requires an elevated administrator PowerShell session" in installer
    assert 'ObserverAccount = "NT SERVICE\\HERMES-FoulerObsServer"' in installer
    assert "RuntimeCacheRoot" in wrapper
    assert '$env:FOULER_RUNTIME_TEMP_ROOT = $RuntimeTempRoot' in wrapper
    assert '$CanonicalDekuEventQueueRoot = "D:\\DekuEvents"' in wrapper
    assert '$env:DEKU_EVENT_QUEUE_ROOT = $CanonicalDekuEventQueueRoot' in wrapper
    assert 'Get-Command "py"' not in wrapper
    assert "git.exe" not in wrapper
    assert "& $Py -I -B @validatorArgs" in wrapper
    assert "& $Py -I -B @supervisorArgs" in wrapper
    assert "Quote-BatchArg" not in wrapper
    assert "$SupervisorScript = [System.IO.Path]::GetFullPath" in installer
    assert "function Test-BattleSupervisorProcessOwnership" in installer
    assert "function Test-BattleSupervisorLauncherOwnership" in installer
    assert "function Get-RuntimeLeaseAccount" not in installer
    ownership_parser = installer[
        installer.index("function Resolve-CommandPathToken") : installer.index("function Test-RuntimeLeaseForStart")
    ]
    assert "ConvertFrom-Json" not in ownership_parser
    assert "function Invoke-VerifiedProcessTreeTermination" in installer
    assert "Get-ProcessRecordById -ProcessId $processId" in installer
    assert '& $TaskKill /PID "$processId" /T /F' in installer
    assert "Stop-Process" not in installer
    validation_gate = installer.index("if ($Start) {\n    $startLeaseCheck")
    preflight_idle = installer.index("$null = Assert-BattleSupervisorRuntimeIdle", validation_gate)
    registration = installer.index("$backup = Save-TaskBackup", validation_gate)
    launch_block = installer.index("if ($Start) {", registration)
    launch_idle = installer.index("$null = Assert-BattleSupervisorRuntimeIdle", launch_block)
    launch = installer.index("Start-ScheduledTask -TaskName $TaskName", launch_idle)
    assert validation_gate < preflight_idle < registration < launch_idle < launch


def test_battle_supervisor_start_requires_idle_runtime_without_disruption():
    installer = (ROOT / "scripts" / "install_battle_supervisor_task.ps1").read_text(
        encoding="utf-8"
    )

    idle_start = installer.index("function Assert-BattleSupervisorRuntimeIdle")
    idle_end = installer.index("function Get-ProcessRecordById", idle_start)
    idle = installer[idle_start:idle_end]
    assert "Get-CimInstance Win32_Process -ErrorAction Stop" in idle
    assert "Assert-NoAlternateBattleOwnerProcesses -Processes $processes" in idle
    assert "Test-BattleSupervisorLauncherOwnership" in idle
    assert "Test-BattleSupervisorProcessOwnership" in idle
    assert "Test-BattleLadderProcessOwnership" in idle
    assert "Get-ScheduledTask -ErrorAction Stop" in idle
    assert '$_ -notin @("Ready", "Disabled")' in idle
    assert "battle supervisor start requires complete runtime idleness" in idle
    assert "Stop-BattleSupervisorProcesses" not in idle
    assert "Remove-Item" not in idle

    launch_start = installer.index(
        "if ($Start) {", installer.index("$null = Assert-InstalledTaskIdentity")
    )
    launch_end = installer.index("$statusPayload = Get-BattleSupervisorStatus", launch_start)
    launch = installer[launch_start:launch_end]
    assert "Stop-BattleSupervisorProcesses" not in launch
    assert "Remove-Item" not in launch
    assert "Set-Content -LiteralPath $StopFile" not in launch

    stop_start = installer.index("if ($Stop -or $Uninstall) {")
    stop_end = installer.index("if (-not $Apply) {", stop_start)
    stop = installer[stop_start:stop_end]
    assert "Stop-BattleSupervisorProcesses | Out-Null" in stop
    assert installer.count("Stop-BattleSupervisorProcesses | Out-Null") == 1


def test_legacy_jigglypuff_runtime_mutations_are_fail_closed():
    text = (ROOT / "scripts" / "fouler_jigglypuff_runtime.ps1").read_text(
        encoding="utf-8"
    )

    gate = 'if ($Command -ne "status")'
    assert gate in text
    assert 'schemaVersion = "fouler-play-retired-launcher/v1"' in text
    assert "startsProcesses = $false" in text
    assert "mutatesProcesses = $false" in text
    assert "$NoWrite = $true" in text
    assert text.index(gate) < text.index("function Start-ObsServer")


def test_battle_supervisor_process_ownership_rejects_adversarial_command_lines(tmp_path):
    powershell = shutil.which("powershell.exe") or shutil.which("pwsh")
    if not powershell:
        pytest.skip("PowerShell is required to exercise the Windows ownership predicate")
    installer = (ROOT / "scripts" / "install_battle_supervisor_task.ps1").read_text(encoding="utf-8")
    function_start = installer.index("function Split-WindowsCommandLine")
    function_end = installer.index("function Test-RuntimeLeaseForStart")
    ownership_functions = installer[function_start:function_end]
    harness = r'''
$ProjectDir = 'C:\Exact Fouler Release'
$Py = [System.IO.Path]::GetFullPath((Join-Path $ProjectDir '.venv\Scripts\python.exe'))
$PowerShell = 'C:\Windows\System32\WindowsPowerShell\v1.0\powershell.exe'
$CanonicalAccountSeasonPath = 'C:\ProgramData\HERMES\authority\fouler\account-season.json'
$RuntimeStateRoot = 'C:\ProgramData\HERMES\state\fouler'
$RuntimeLogRoot = 'C:\ProgramData\HERMES\logs\fouler'
$RuntimeCacheRoot = 'C:\ProgramData\HERMES\cache\fouler'
$SecretEnvFile = 'C:\ProgramData\HERMES\secrets\fouler.env'
$SupervisorScript = [System.IO.Path]::GetFullPath((Join-Path $ProjectDir 'scripts\devstream_session.py'))
$TaskWrapperPath = [System.IO.Path]::GetFullPath((Join-Path $ProjectDir 'scripts\start_battle_supervisor_task.ps1'))
$BoundedSessionScript = [System.IO.Path]::GetFullPath((Join-Path $ProjectDir 'scripts\run_bounded_battle_session.py'))
$LadderScript = [System.IO.Path]::GetFullPath((Join-Path $ProjectDir 'run.py'))
$ResolvedRuntimeLease = 'C:\Exact Fouler Authority\runtime-lease.json'
''' + ownership_functions + r'''
$cases = [ordered]@{
    ownedSupervisor = Test-BattleSupervisorProcessOwnership -Process ([pscustomobject]@{
        ProcessId = 2147483001
        Name = 'python.exe'
        ExecutablePath = 'C:\Exact Fouler Release\.venv\Scripts\python.exe'
        CommandLine = '"C:\Exact Fouler Release\.venv\Scripts\python.exe" -I -B "C:\Exact Fouler Release\scripts\devstream_session.py" supervise --run-count 5 --max-concurrent-battles 3 --queue-timeout-seconds 180 --sleep-seconds 15 --max-cycles 6 --runtime-lease "C:\Exact Fouler Authority\runtime-lease.json" --skip-improve'
    })
    relativeOwnedSupervisor = Test-BattleSupervisorProcessOwnership -Process ([pscustomobject]@{
        ProcessId = 2147483002
        Name = 'python.exe'
        ExecutablePath = 'C:\Exact Fouler Release\.venv\Scripts\python.exe'
        CommandLine = '.venv\Scripts\python.exe -I -B scripts\devstream_session.py supervise --run-count 5 --max-concurrent-battles 3 --queue-timeout-seconds 180 --sleep-seconds 15 --max-cycles 6 --runtime-lease "C:\Exact Fouler Authority\runtime-lease.json" --enable-auto-improve'
    })
    otherRelease = Test-BattleSupervisorProcessOwnership -Process ([pscustomobject]@{
        ProcessId = 2147483003
        Name = 'python.exe'
        ExecutablePath = 'C:\Exact Fouler Release\.venv\Scripts\python.exe'
        CommandLine = '"C:\Exact Fouler Release\.venv\Scripts\python.exe" -I -B "C:\Exact Fouler Release-old\scripts\devstream_session.py" supervise --run-count 5 --max-concurrent-battles 3 --queue-timeout-seconds 180 --sleep-seconds 15 --max-cycles 6 --runtime-lease "C:\Exact Fouler Authority\runtime-lease.json" --skip-improve'
    })
    pathPrefix = Test-BattleSupervisorProcessOwnership -Process ([pscustomobject]@{
        ProcessId = 2147483004
        Name = 'python.exe'
        ExecutablePath = 'C:\Exact Fouler Release\.venv\Scripts\python.exe'
        CommandLine = '"C:\Exact Fouler Release\.venv\Scripts\python.exe" -I -B "C:\Exact Fouler Release\scripts\devstream_session.py.bak" supervise'
    })
    pathOnlyInData = Test-BattleSupervisorProcessOwnership -Process ([pscustomobject]@{
        ProcessId = 2147483005
        Name = 'python.exe'
        ExecutablePath = 'C:\Exact Fouler Release\.venv\Scripts\python.exe'
        CommandLine = '"C:\Exact Fouler Release\.venv\Scripts\python.exe" -c "C:\Exact Fouler Release\scripts\devstream_session.py supervise"'
    })
    wrongSubcommand = Test-BattleSupervisorProcessOwnership -Process ([pscustomobject]@{
        ProcessId = 2147483006
        Name = 'python.exe'
        ExecutablePath = 'C:\Exact Fouler Release\.venv\Scripts\python.exe'
        CommandLine = '"C:\Exact Fouler Release\.venv\Scripts\python.exe" -I -B "C:\Exact Fouler Release\scripts\devstream_session.py" doctor'
    })
    misleadingProcessName = Test-BattleSupervisorProcessOwnership -Process ([pscustomobject]@{
        ProcessId = 2147483007
        Name = 'python-helper.exe'
        ExecutablePath = 'C:\Exact Fouler Release\.venv\Scripts\python.exe'
        CommandLine = '"C:\Exact Fouler Release\.venv\Scripts\python.exe" -I -B "C:\Exact Fouler Release\scripts\devstream_session.py" supervise'
    })
    ownedFallbackLauncher = Test-BattleSupervisorLauncherOwnership -Process ([pscustomobject]@{
        ProcessId = 2147483008
        Name = 'powershell.exe'
        ExecutablePath = 'C:\Windows\System32\WindowsPowerShell\v1.0\powershell.exe'
        CommandLine = '"C:\Windows\System32\WindowsPowerShell\v1.0\powershell.exe" -NoProfile -ExecutionPolicy Bypass -File "C:\Exact Fouler Release\scripts\start_battle_supervisor_task.ps1" -RunCount 5 -MaxConcurrentBattles 3 -SearchParallelism 2 -MaxCycles 6 -QueueTimeoutSeconds 180 -SleepSeconds 15 -LoopBreak 0 -AccountSeasonPath "C:\ProgramData\HERMES\authority\fouler\account-season.json" -RuntimeLease "C:\Exact Fouler Authority\runtime-lease.json" -RuntimeStateRoot "C:\ProgramData\HERMES\state\fouler" -RuntimeLogRoot "C:\ProgramData\HERMES\logs\fouler" -RuntimeCacheRoot "C:\ProgramData\HERMES\cache\fouler" -SecretEnvFile "C:\ProgramData\HERMES\secrets\fouler.env" -Foreground'
    })
    otherFallbackLauncher = Test-BattleSupervisorLauncherOwnership -Process ([pscustomobject]@{
        ProcessId = 2147483009
        Name = 'powershell.exe'
        ExecutablePath = 'C:\Windows\System32\WindowsPowerShell\v1.0\powershell.exe'
        CommandLine = '"C:\Windows\System32\WindowsPowerShell\v1.0\powershell.exe" -NoProfile -ExecutionPolicy Bypass -File "C:\Other Release\scripts\start_battle_supervisor_task.ps1" -RunCount 5 -MaxConcurrentBattles 3 -SearchParallelism 2 -MaxCycles 6 -QueueTimeoutSeconds 180 -SleepSeconds 15 -LoopBreak 0 -AccountSeasonPath "C:\ProgramData\HERMES\authority\fouler\account-season.json" -RuntimeLease "C:\Exact Fouler Authority\runtime-lease.json" -RuntimeStateRoot "C:\ProgramData\HERMES\state\fouler" -RuntimeLogRoot "C:\ProgramData\HERMES\logs\fouler" -RuntimeCacheRoot "C:\ProgramData\HERMES\cache\fouler" -SecretEnvFile "C:\ProgramData\HERMES\secrets\fouler.env" -Foreground'
    })
    ownedLadder = Test-BattleLadderProcessOwnership -Process ([pscustomobject]@{
        ProcessId = 2147483010
        Name = 'python.exe'
        ExecutablePath = 'C:\Exact Fouler Release\.venv\Scripts\python.exe'
        CommandLine = '"C:\Exact Fouler Release\.venv\Scripts\python.exe" "C:\Exact Fouler Release\run.py" --websocket-uri wss://sim3.psim.us/showdown/websocket --ps-username DekuFoulerLab --bot-mode search_ladder --pokemon-format gen9ou --run-count 5 --max-concurrent-battles 3 --search-parallelism 2 --save-replay always --log-to-file'
    })
    partialLadder = Test-BattleLadderProcessOwnership -Process ([pscustomobject]@{
        ProcessId = 2147483011
        Name = 'python.exe'
        ExecutablePath = 'C:\Exact Fouler Release\.venv\Scripts\python.exe'
        CommandLine = '"C:\Exact Fouler Release\.venv\Scripts\python.exe" "C:\Exact Fouler Release\run.py" --ps-username DekuFoulerLab --bot-mode search_ladder --pokemon-format gen9ou --run-count 5 --max-concurrent-battles 3'
    })
    otherReleaseLadder = Test-BattleLadderProcessOwnership -Process ([pscustomobject]@{
        ProcessId = 2147483012
        Name = 'python.exe'
        ExecutablePath = 'C:\Exact Fouler Release\.venv\Scripts\python.exe'
        CommandLine = '"C:\Exact Fouler Release\.venv\Scripts\python.exe" "C:\Other Release\run.py" --websocket-uri wss://sim3.psim.us/showdown/websocket --ps-username DekuFoulerLab --bot-mode search_ladder --pokemon-format gen9ou --run-count 5 --max-concurrent-battles 3 --search-parallelism 2 --save-replay always --log-to-file'
    })
}
$cases | ConvertTo-Json -Compress
'''
    harness_path = tmp_path / "ownership-harness.ps1"
    harness_path.write_text(harness, encoding="utf-8")

    result = subprocess.run(
        [powershell, "-NoProfile", "-NonInteractive", "-ExecutionPolicy", "Bypass", "-File", str(harness_path)],
        capture_output=True,
        text=True,
        timeout=30,
        check=False,
    )

    assert result.returncode == 0, result.stderr
    cases = json.loads(result.stdout)
    assert cases == {
        "ownedSupervisor": True,
        "relativeOwnedSupervisor": True,
        "otherRelease": False,
        "pathPrefix": False,
        "pathOnlyInData": False,
        "wrongSubcommand": False,
        "misleadingProcessName": False,
        "ownedFallbackLauncher": True,
        "otherFallbackLauncher": False,
        "ownedLadder": True,
        "partialLadder": False,
        "otherReleaseLadder": False,
    }


def test_battle_supervisor_tree_kill_revalidates_pid_and_requests_descendants(tmp_path):
    powershell = shutil.which("powershell.exe") or shutil.which("pwsh")
    if not powershell:
        pytest.skip("PowerShell is required to exercise the Windows termination contract")
    installer = (ROOT / "scripts" / "install_battle_supervisor_task.ps1").read_text(encoding="utf-8")
    function_start = installer.index("function Split-WindowsCommandLine")
    function_end = installer.index("function Test-RuntimeLeaseForStart")
    ownership_functions = installer[function_start:function_end]
    taskkill_args = tmp_path / "taskkill-args.txt"
    taskkill_stub = tmp_path / "taskkill-stub.cmd"
    taskkill_stub.write_text(
        f'@echo off\r\necho %* > "{taskkill_args}"\r\nexit /b 0\r\n',
        encoding="ascii",
    )
    quoted_stub = str(taskkill_stub).replace("'", "''")
    harness = rf'''
$ProjectDir = 'C:\Exact Fouler Release'
$Py = [System.IO.Path]::GetFullPath((Join-Path $ProjectDir '.venv\Scripts\python.exe'))
$SupervisorScript = [System.IO.Path]::GetFullPath((Join-Path $ProjectDir 'scripts\devstream_session.py'))
$TaskWrapperPath = [System.IO.Path]::GetFullPath((Join-Path $ProjectDir 'scripts\start_battle_supervisor_task.ps1'))
$BoundedSessionScript = [System.IO.Path]::GetFullPath((Join-Path $ProjectDir 'scripts\run_bounded_battle_session.py'))
$LadderScript = [System.IO.Path]::GetFullPath((Join-Path $ProjectDir 'run.py'))
$ResolvedRuntimeLease = 'C:\Exact Fouler Authority\runtime-lease.json'
$TaskKill = '{quoted_stub}'
''' + ownership_functions + r'''
$script:currentRecord = [pscustomobject]@{
    ProcessId = 424242
    ParentProcessId = 1
    Name = 'python.exe'
    ExecutablePath = 'C:\Exact Fouler Release\.venv\Scripts\python.exe'
    CreationDate = '20260715080000.000000-000'
    CommandLine = '"C:\Exact Fouler Release\.venv\Scripts\python.exe" -I -B "C:\Exact Fouler Release\scripts\devstream_session.py" supervise --run-count 5 --max-concurrent-battles 3 --queue-timeout-seconds 180 --sleep-seconds 15 --max-cycles 6 --runtime-lease "C:\Exact Fouler Authority\runtime-lease.json" --skip-improve'
}
$script:childRecord = [pscustomobject]@{
    ProcessId = 424243
    ParentProcessId = 424242
    Name = 'python.exe'
    ExecutablePath = 'C:\Exact Fouler Release\.venv\Scripts\python.exe'
    CreationDate = '20260715080001.000000-000'
    CommandLine = '"C:\Exact Fouler Release\.venv\Scripts\python.exe" "C:\Exact Fouler Release\run.py" --websocket-uri wss://sim3.psim.us/showdown/websocket --ps-username DekuFoulerLab --bot-mode search_ladder --pokemon-format gen9ou --run-count 5 --max-concurrent-battles 3 --search-parallelism 2 --save-replay always --log-to-file'
}
$script:lookupCount = 0
function Get-ProcessRecordById {
    param([int64]$ProcessId)
    $script:lookupCount += 1
    if ($script:lookupCount -eq 1) { return $script:currentRecord }
    return $null
}
function Get-ProcessTreeRecords {
    param([int64]$RootProcessId)
    return @($script:currentRecord, $script:childRecord)
}
$owned = Invoke-VerifiedProcessTreeTermination -Process $script:currentRecord -OwnershipType 'supervisor'
$script:currentRecord = [pscustomobject]@{
    ProcessId = 424242
    Name = 'python.exe'
    ExecutablePath = 'C:\Exact Fouler Release\.venv\Scripts\python.exe'
    CommandLine = '"C:\Exact Fouler Release\.venv\Scripts\python.exe" -I -B "C:\Other Release\scripts\devstream_session.py" supervise --run-count 5 --max-concurrent-battles 3 --queue-timeout-seconds 180 --sleep-seconds 15 --max-cycles 6 --runtime-lease "C:\Exact Fouler Authority\runtime-lease.json" --skip-improve'
}
$script:lookupCount = 0
$reused = Invoke-VerifiedProcessTreeTermination -Process ([pscustomobject]@{ ProcessId = 424242 }) -OwnershipType 'supervisor'
[ordered]@{ owned = $owned; reused = $reused } | ConvertTo-Json -Compress -Depth 5
'''
    harness_path = tmp_path / "termination-harness.ps1"
    harness_path.write_text(harness, encoding="utf-8")

    result = subprocess.run(
        [powershell, "-NoProfile", "-NonInteractive", "-ExecutionPolicy", "Bypass", "-File", str(harness_path)],
        capture_output=True,
        text=True,
        timeout=30,
        check=False,
    )

    assert result.returncode == 0, result.stderr
    payload = json.loads(result.stdout)
    assert payload["owned"]["terminated"] is True
    assert payload["owned"]["skipped"] is False
    assert payload["owned"]["treeVerified"] is True
    assert payload["owned"]["ownedTreePids"] == [424242, 424243]
    assert [pid for pid in payload["owned"]["survivorPids"] if pid is not None] == []
    assert payload["reused"]["terminated"] is False
    assert payload["reused"]["skipped"] is True
    assert taskkill_args.read_text(encoding="ascii").strip() == "/PID 424242 /T /F"


def test_scheduled_mission_monitor_repairs_without_renewing_owner_authority():
    task_wrapper = (ROOT / "scripts" / "fouler_mission_monitor_task.ps1").read_text(encoding="utf-8")

    assert '"scripts\\fouler_mission_monitor.py"' in task_wrapper
    assert '$argsList += "--repair-runtime"' in task_wrapper
    assert "--renew-lease" not in task_wrapper
    assert "& $Py @argsList" in task_wrapper
    assert "exit $code" in task_wrapper


def test_legacy_player_loop_is_clearly_quarantined():
    text = (ROOT / "infrastructure" / "windows" / "player_loop.bat").read_text(encoding="utf-8")

    assert "retired" in text.lower()
    assert "receipt-gated devstream supervisor" in text
    assert "exit /b 2" in text
    assert "goto loop_start" not in text
    assert "start_one_touch" not in text


def test_legacy_deploy_and_developer_loops_are_fail_closed_tombstones():
    paths = (
        ROOT / "infrastructure" / "windows" / "deploy_update.bat",
        ROOT / "infrastructure" / "linux" / "deploy_update.sh",
        ROOT / "infrastructure" / "linux" / "developer_loop.sh",
    )
    for path in paths:
        text = path.read_text(encoding="utf-8")
        assert "retired" in text.lower()
        assert "git pull" not in text
        assert "git merge" not in text
        assert "record_deploy" not in text
        assert "exit 2" in text or "exit /b 2" in text


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


def test_runtime_authority_installer_is_acl_protected_and_never_starts_runtime():
    text = (ROOT / "scripts" / "install_runtime_authority.ps1").read_text(encoding="utf-8")

    assert "D:\\Releases\\fouler-play\\<commit>" in text
    assert "C:\\ProgramData\\HERMES\\authority\\fouler" in text
    assert "[string]$AccountSeasonSource" in text
    assert "[string]$ExpectedAccountSeasonSha256" in text
    assert "ConvertFrom-StrictAccountSeasonSnapshot" in text
    assert "account-season source SHA-256 does not match the owner-pinned digest" in text
    assert '"C:\\ProgramData\\HERMES\\authority\\fouler\\account-season.json"' in text
    assert "Protect-AccountSeasonFile -Path $accountSeasonDestination" in text
    assert "[System.IO.FileAttributes]::ReadOnly" in text
    assert "[string]$ExpectedKeyringSha256" in text
    assert "keyring source SHA-256 does not match the trusted bootstrap digest" in text
    assert "installed keyring differs from the pinned keyring" in text
    assert "keyRotationAllowed = $false" in text
    assert "Get-RegularFileSnapshot -Path $KeyringSource" in text
    assert "Get-RegularFileSnapshot -Path $LeaseSource" in text
    assert "Write-ExactAtomic -Content $keyringSnapshot.Bytes" in text
    assert "Write-ExactAtomic -Content $leaseSnapshot.Bytes" in text
    assert "Write-ReplaceAtomic -Content $keyringSnapshot.Bytes" not in text
    assert "Write-ReplaceAtomic -Content $leaseSnapshot.Bytes -Destination $currentLeaseDestination" in text
    assert '"S-1-5-18"' in text
    assert '"S-1-5-32-544"' in text
    assert "$script:RuntimeSid" in text
    assert "$script:BrokerSid" in text
    assert '"NT SERVICE\\HERMES-FoulerLeaseBroker"' in text
    assert "$script:ObserverSid" in text
    assert '"NT SERVICE\\HERMES-FoulerObsServer"' in text
    assert "SetAccessRuleProtection($true, $false)" in text
    assert "[System.IO.Directory]::SetAccessControl" in text
    assert "[System.IO.File]::SetAccessControl" in text
    assert "/grant:r" not in text
    assert "/inheritance:r" not in text
    assert "GetFileInformationByHandle" in text
    assert "NumberOfLinks -ne 1" in text
    assert "Assert-NoReparsePathChain" in text
    assert "Assert-NoPathOverlap" in text
    assert "Protect-ImmutableReleaseTree -Path $resolvedProject" in text
    assert "releaseDaclEnforced = [bool]$Apply" in text
    assert "release-acl.txt" in text
    assert '"--require-deployment-receipt"' in text
    assert '"--verify-deployment-checkout"' in text
    assert "refusing to overwrite immutable authority file" in text
    assert "$leaseCreated = $false" in text
    assert "$keyringInstalled = $false" in text
    assert "foreach ($createdAuthorityFile in @(\n" in text
    assert "if (-not $createdAuthorityFile.Created)" in text
    assert "$accountSeasonRotationAttempted = $false" in text
    assert "account-season rollback backup failed its pre-mutation SHA-256 verification" in text
    assert "rolled-back account-season authority differs from its verified backup" in text
    backup_verified = text.index("account-season rollback backup failed its pre-mutation SHA-256 verification")
    rotate = text.index("Clear-AccountSeasonReadOnlyAttribute -Path $accountSeasonDestination", backup_verified)
    assert backup_verified < rotate
    assert "startsProcesses = $false" in text
    assert "mutatesScheduledTasks = $false" in text
    assert '[switch]$ImproveAuthorized' in text
    assert 'improveAuthorized = [bool]$ImproveAuthorized' in text
    assert 'register-lease --registration $registrationPath' in text
    assert "Initialize-BrokerStore" in text
    assert "Assert-NoCompetingBrokerProcess" in text
    assert "Assert-NoCompetingObsProcess" in text
    assert "OBS service identity must be provisioned, stopped, and Disabled" in text
    preflight = text.index("$brokerService = Get-Service -Name $BrokerServiceName", text.index("try {", text.index("$previousCurrentLease")))
    assert 'fouler-lease-broker-activation/v1' in text
    assert "ReleaseManifestSource" in text
    assert "ExpectedReleaseManifestSha256" in text
    assert "authority installer path" in text
    assert "Apply requires an elevated administrator PowerShell session" in text
    harden = text.index("Protect-ImmutableReleaseTree -Path $resolvedProject")
    manifest = text.index("$manifest = Assert-ReleaseMatchesBootstrapManifest", harden)
    validate = text.index("$leaseValidation = Invoke-AuthorityValidation", manifest)
    initialize = text.index("$brokerInitialization = Initialize-BrokerStore", validate)
    register = text.index("Register-BrokerLease -Validation $leaseValidation", initialize)
    assert preflight < harden < manifest < validate < initialize < register
    assert "& $Python -I -B" in text
    assert "& $python -I -B" in text
    assert "Start-ScheduledTask" not in text
    assert "Start-Process" not in text


def test_windows_atomic_replacement_uses_a_concrete_same_directory_backup():
    installers = (
        ROOT / "scripts" / "install_runtime_authority.ps1",
        ROOT / "scripts" / "install_obs_server_service.ps1",
    )

    for installer in installers:
        text = installer.read_text(encoding="utf-8")
        assert "::Replace($temporary, $Destination, $null, $true)" not in text
        assert '".replace.bak"' in text
        assert "::Replace($temporary, $Destination, $replacementBackup, $true)" in text
        assert (
            "Remove-Item -LiteralPath $replacementBackup -Force "
            "-ErrorAction SilentlyContinue"
        ) in text
