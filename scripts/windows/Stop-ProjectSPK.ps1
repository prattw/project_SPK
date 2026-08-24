<#
.SYNOPSIS
  Stops the Project SPK local prototype running inside WSL2.

.DESCRIPTION
  Kills the uvicorn process inside WSL2. Does not shut down WSL2 itself or
  touch any other WSL processes. Safe to run even if Project SPK isn't
  currently running.
#>

$WslDistro = "Ubuntu"

Write-Host "Stopping Project SPK inside WSL2 ($WslDistro)..."
Start-Process -FilePath "wsl.exe" `
    -ArgumentList @("-d", $WslDistro, "--", "bash", "-lc", "pkill -f 'uvicorn app.main' || true") `
    -WindowStyle Hidden -Wait

Write-Host "Done."
