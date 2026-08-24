<#
.SYNOPSIS
  Launches Project SPK (local prototype) and opens it in an app-like window.

.DESCRIPTION
  Starts the FastAPI backend inside WSL2 if it isn't already running, waits
  for it to become healthy, then opens it in a chromeless browser window
  (Edge's "app mode") so it feels like a standalone desktop app rather than
  a browser tab. Falls back to your default browser if Edge isn't found.

  This does not touch production Project SPK (Railway/OpenAI) in any way —
  it only starts the local prototype described in LOCAL_PROTOTYPE.md.

.NOTES
  This script is meant to be launched via the desktop shortcut created by
  Install-ProjectSPKShortcut.ps1. You can also run it directly.
#>

$ErrorActionPreference = "Stop"

$WslDistro   = "Ubuntu"
$ProjectDir  = "~/project_SPK"
$AppUrl      = "http://127.0.0.1:8000"
$HealthUrl   = "$AppUrl/health"
$MaxWaitSecs = 45

function Test-AppHealthy {
    try {
        $response = Invoke-WebRequest -Uri $HealthUrl -TimeoutSec 2 -UseBasicParsing
        return $response.StatusCode -eq 200
    } catch {
        return $false
    }
}

if (-not (Test-AppHealthy)) {
    Write-Host "Project SPK isn't running yet — starting it inside WSL2 ($WslDistro)..."

    # Start the backend inside WSL2, backgrounded with nohup so it keeps
    # running after this wsl.exe invocation returns. Logs go to /tmp/spk.log
    # inside the WSL filesystem for troubleshooting.
    $startCmd = "cd $ProjectDir && source .venv/bin/activate && nohup ./start.sh > /tmp/spk.log 2>&1 & disown; sleep 1"
    Start-Process -FilePath "wsl.exe" -ArgumentList @("-d", $WslDistro, "--", "bash", "-lc", $startCmd) -WindowStyle Hidden -Wait

    $waited = 0
    while (-not (Test-AppHealthy) -and $waited -lt $MaxWaitSecs) {
        Start-Sleep -Seconds 1
        $waited++
    }

    if (-not (Test-AppHealthy)) {
        Write-Warning "Project SPK did not become healthy within $MaxWaitSecs seconds."
        Write-Warning "Check the log inside WSL2: wsl.exe -d $WslDistro -- cat /tmp/spk.log"
        Read-Host "Press Enter to close"
        exit 1
    }
    Write-Host "Project SPK is up."
} else {
    Write-Host "Project SPK is already running."
}

# Open in an app-like (chromeless) window if Edge is available, else fall
# back to whatever the default browser is.
$edgePaths = @(
    "${env:ProgramFiles(x86)}\Microsoft\Edge\Application\msedge.exe",
    "$env:ProgramFiles\Microsoft\Edge\Application\msedge.exe"
)
$edge = $edgePaths | Where-Object { Test-Path $_ } | Select-Object -First 1

if ($edge) {
    Start-Process -FilePath $edge -ArgumentList @("--app=$AppUrl", "--window-size=1440,900")
} else {
    Start-Process $AppUrl
}
