# Retired mutable matchup-weight refresher tombstone.
# Engine changes must pass the candidate-vs-frozen promotion transaction.
$ErrorActionPreference = 'Stop'
Write-Error 'retired: standalone matchup-weight refresh cannot mutate live policy'
exit 2
