# Retired: the NSSM Windows service is the only OBS HTTP lifecycle owner.
[Console]::Error.WriteLine(
    "[RETIRED] fouler_obs_keepalive.ps1 is disabled; use HERMES-FoulerObsServer service."
)
exit 2
