<#
.SYNOPSIS
  Stops Moli Beans (the Project SPK local prototype) running inside WSL2.

.DESCRIPTION
  Kills the uvicorn process inside WSL2. Does not shut down WSL2 itself or
  touch any other WSL processes. Safe to run even if it isn't currently
  running.
#>

$WslDistro = "Ubuntu"
# Must match the user Start-MoliBeans.ps1 runs as (see $WslUser there),
# otherwise pkill won't have permission to signal the uvicorn process.
$WslUser   = "cyrus"

Write-Host "Stopping Moli Beans inside WSL2 ($WslDistro)..."
Start-Process -FilePath "wsl.exe" `
    -ArgumentList @("-d", $WslDistro, "-u", $WslUser, "--", "bash", "-lc", "pkill -f 'uvicorn app.main' || true") `
    -WindowStyle Hidden -Wait

Write-Host "Done."
