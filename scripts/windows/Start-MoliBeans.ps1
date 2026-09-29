<#
.SYNOPSIS
  Launches Moli Beans (Project SPK's local prototype) and opens it in an
  app-like window.

.DESCRIPTION
  Starts the FastAPI backend inside WSL2 if it isn't already running, waits
  for it to become healthy, then opens it in a chromeless browser window
  (Edge's "app mode") so it feels like a standalone desktop app rather than
  a browser tab.  Falls back to your default browser if Edge isn't found.

  This does not touch production Project SPK (Railway/OpenAI) in any way -
  it only starts the local prototype described in LOCAL_PROTOTYPE.md. The
  app is renamed "Moli Beans" for this laptop instance only (see
  APP_DISPLAY_NAME/APP_ICON_PATH in .env); production keeps the Project SPK
  name and branding.

  The desktop shortcut launches this with a hidden window, so anything
  printed with Write-Host/Write-Warning is invisible in normal use. Every
  step is also logged to moli-beans-launcher.log next to this script, and
  failures pop up a message box (rather than a hidden, silently-hanging
  Read-Host prompt) so a problem is never just "nothing happened."

.NOTES
  This script is meant to be launched via the desktop shortcut created by
  Install-MoliBeansShortcut.ps1. You can also run it directly (e.g. from a
  normal PowerShell window) to see everything live instead of via the log.
#>

$ErrorActionPreference = "Stop"

$WslDistro   = "Ubuntu"
# Edit this if you cloned the repo somewhere else inside WSL2 (check with
# `pwd` from inside the Ubuntu terminal where you ran setup_local_prototype.sh).
$ProjectDir  = "~/Deployment-Laptop"
$AppUrl      = "http://127.0.0.1:8000"
$HealthUrl   = "$AppUrl/health"
$MaxWaitSecs = 45
$LogFile     = Join-Path $PSScriptRoot "moli-beans-launcher.log"

function Write-Log {
    param([string]$Message)
    $line = "$(Get-Date -Format 'yyyy-MM-dd HH:mm:ss')  $Message"
    Add-Content -Path $LogFile -Value $line
    Write-Host $Message
}

function Show-FailureAndExit {
    param([string]$Message)
    Write-Log "FAILED: $Message"
    Add-Type -AssemblyName System.Windows.Forms
    [System.Windows.Forms.MessageBox]::Show(
        "$Message`n`nDetails logged to:`n$LogFile`n`nWSL-side app log (if it got that far):`nwsl.exe -d $WslDistro -- cat /tmp/spk.log",
        "Moli Beans didn't start",
        [System.Windows.Forms.MessageBoxButtons]::OK,
        [System.Windows.Forms.MessageBoxIcon]::Error
    ) | Out-Null
    exit 1
}

function Test-AppHealthy {
    try {
        $response = Invoke-WebRequest -Uri $HealthUrl -TimeoutSec 2 -UseBasicParsing
        return $response.StatusCode -eq 200
    } catch {
        return $false
    }
}

Write-Log "== Launch attempt starting =="

try {
    if (-not (Test-AppHealthy)) {
        Write-Log "Moli Beans isn't running yet - starting it inside WSL2 ($WslDistro)..."

        # Start the backend inside WSL2. The cd/source steps run in the
        # foreground (with explicit || checks) so a failure there produces a
        # real non-zero exit code and an error message we can capture, rather
        # than being silently swallowed. Only the actual server process is
        # backgrounded with nohup so it keeps running after this wsl.exe
        # invocation returns. App logs go to /tmp/spk.log inside WSL.
        $startCmd = "cd $ProjectDir || { echo 'ERROR: cd to project dir failed - check `$ProjectDir in Start-MoliBeans.ps1'; exit 10; }; source .venv/bin/activate || { echo 'ERROR: could not activate .venv - did setup_local_prototype.sh finish successfully?'; exit 11; }; nohup ./start.sh > /tmp/spk.log 2>&1 & disown; sleep 1"

        $wslStdout = Join-Path $env:TEMP "moli-beans-wsl-stdout.log"
        $wslStderr = Join-Path $env:TEMP "moli-beans-wsl-stderr.log"
        Remove-Item -ErrorAction SilentlyContinue $wslStdout, $wslStderr

        $proc = Start-Process -FilePath "wsl.exe" -ArgumentList @("-d", $WslDistro, "--", "bash", "-lc", $startCmd) -WindowStyle Hidden -Wait -PassThru -RedirectStandardOutput $wslStdout -RedirectStandardError $wslStderr
        Write-Log "wsl.exe exited with code $($proc.ExitCode)"

        $wslOutput = ""
        foreach ($f in @($wslStdout, $wslStderr)) {
            if (Test-Path $f) {
                $content = (Get-Content $f -Raw -ErrorAction SilentlyContinue)
                if ($content) {
                    Write-Log "wsl.exe output ($f):`n$content"
                    $wslOutput += "$content`n"
                }
            }
        }

        if ($proc.ExitCode -ne 0) {
            Show-FailureAndExit "Starting the backend inside WSL2 failed (exit code $($proc.ExitCode)).`n`n$wslOutput"
        }

        $waited = 0
        while (-not (Test-AppHealthy) -and $waited -lt $MaxWaitSecs) {
            Start-Sleep -Seconds 1
            $waited++
        }

        if (-not (Test-AppHealthy)) {
            Show-FailureAndExit "Moli Beans did not become healthy within $MaxWaitSecs seconds (wsl.exe exit code: $($proc.ExitCode)).`n`n$wslOutput"
        }
        Write-Log "Moli Beans is up after $waited second(s)."
    } else {
        Write-Log "Moli Beans is already running."
    }
} catch {
    Show-FailureAndExit "Error while starting the backend: $($_.Exception.Message)"
}

# Open in an app-like (chromeless) window if Edge is available, else fall
# back to whatever the default browser is.
try {
    $edgePaths = @(
        "${env:ProgramFiles(x86)}\Microsoft\Edge\Application\msedge.exe",
        "$env:ProgramFiles\Microsoft\Edge\Application\msedge.exe"
    )
    $edge = $edgePaths | Where-Object { Test-Path $_ } | Select-Object -First 1

    if ($edge) {
        Write-Log "Opening in Edge app mode: $edge"
        Start-Process -FilePath $edge -ArgumentList @("--app=$AppUrl", "--window-size=1440,900")
    } else {
        Write-Log "Edge not found at the usual paths - opening default browser instead."
        Start-Process $AppUrl
    }
    Write-Log "== Launch attempt finished OK =="
} catch {
    Show-FailureAndExit "Backend is healthy, but couldn't open a browser window: $($_.Exception.Message)"
}
