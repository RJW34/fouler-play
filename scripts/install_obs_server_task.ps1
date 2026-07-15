# Retired: the scheduled-task OBS owner was replaced by the NSSM service.
[Console]::Error.WriteLine(
    "[RETIRED] install_obs_server_task.ps1 is disabled; use install_obs_server_service.ps1."
)
exit 2
