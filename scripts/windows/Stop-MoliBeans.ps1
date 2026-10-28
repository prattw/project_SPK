<#
.SYNOPSIS
  Stops Moli Beans (the Project SPK local prototype) running inside WSL2.

.DESCRIPTION
  Kills the uvicorn process inside WSL2. Does not shut down WSL2 itself or
  touch any other WSL processes. Safe to run even if it isn't currently
  running.
#>

$WslDistro = "Ubuntu"
# A *preferred* WSL Linux username to try first - see Start-MoliBeans.ps1
# for why this doesn't need to be correct. pkill is a no-op if there's no
# matching process for that user, so trying a user with nothing running is
# harmless; we also always try WSL's own default user as a fallback in case
# this value is stale or was never right in the first place.
$WslUser   = "cyrus"

Write-Host "Stopping Moli Beans inside WSL2 ($WslDistro)..."

$candidates = @()
if ($WslUser) { $candidates += $WslUser }
$candidates += $null  # $null = WSL's own default user

$tried = @()
foreach ($candidate in $candidates) {
    $label = if ($candidate) { $candidate } else { "(WSL default user)" }
    if ($tried -contains $label) { continue }
    $tried += $label
    $argList = @("-d", $WslDistro)
    if ($candidate) { $argList += @("-u", $candidate) }
    $argList += @("--", "bash", "-lc", "pkill -f 'uvicorn app.main' || true")
    Start-Process -FilePath "wsl.exe" -ArgumentList $argList -WindowStyle Hidden -Wait
}

Write-Host "Done (tried: $($tried -join ', '))."
