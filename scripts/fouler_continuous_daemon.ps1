# Retired runtime-owner tombstone.
# The only battle owner is HERMES-FoulerBattleSupervisor from an immutable release.
$ErrorActionPreference = 'Stop'
Write-Error 'retired: the legacy continuous Fouler daemon cannot start a runtime'
exit 2
