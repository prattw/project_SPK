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
# A *preferred* WSL Linux username to try first - purely an optimization so
# the common case skips straight to the right one. This does NOT need to be
# correct: if it's wrong (or blank), Resolve-MoliBeansTarget below falls
# back to probing WSL's own default user automatically, so a guessed or
# stale value here can never be a hard failure the way it used to be.
$WslUser     = "cyrus"
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
        "$Message`n`nDetails logged to:`n$LogFile",
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

# `wsl.exe -d <Distro> -- <cmd>` (no -u) is not guaranteed to log in as the
# same user as double-clicking the "Ubuntu" app / your Windows Terminal
# profile - it uses whatever default user is registered for the distro. A
# hardcoded -u guess can silently be wrong (that's exactly what happened
# here once already - see git history), so instead of trusting one guessed
# username, actually probe WSL for a user under which $ProjectDir has a
# working venv, trying the configured $WslUser first (fast path when it's
# right) and WSL's own default user as a fallback. Returns the resolved
# username, or $null if neither candidate has a working checkout.
function Test-MoliBeansCandidate {
    param([string]$Distro, [string]$User, [string]$ProjectDir)
    $probeCmd = "cd $ProjectDir 2>/dev/null && test -f start.sh && test -f .venv/bin/activate && whoami"
    $argList = @("-d", $Distro)
    if ($User) { $argList += @("-u", $User) }
    $argList += @("--", "bash", "-lc", $probeCmd)
    try {
        $output = & wsl.exe @argList 2>$null
    } catch {
        return $null
    }
    if ($LASTEXITCODE -eq 0 -and $output) {
        return (($output | Select-Object -Last 1) -as [string]).Trim()
    }
    return $null
}

function Resolve-MoliBeansTarget {
    param([string]$Distro, [string]$PreferredUser, [string]$ProjectDir)
    $candidates = @()
    if ($PreferredUser) { $candidates += $PreferredUser }
    $candidates += $null  # $null = let WSL pick its own default user
    $tried = @()
    foreach ($candidate in $candidates) {
        $label = if ($candidate) { $candidate } else { "(WSL default user)" }
        if ($tried -contains $label) { continue }
        $tried += $label
        $resolved = Test-MoliBeansCandidate -Distro $Distro -User $candidate -ProjectDir $ProjectDir
        if ($resolved) {
            return [PSCustomObject]@{ User = $resolved; Tried = $tried }
        }
    }
    return [PSCustomObject]@{ User = $null; Tried = $tried }
}

Write-Log "== Launch attempt starting =="

$ResolvedUser = $null

try {
    if (-not (Test-AppHealthy)) {
        Write-Log "Moli Beans isn't running yet - resolving which WSL user has $ProjectDir..."

        $target = Resolve-MoliBeansTarget -Distro $WslDistro -PreferredUser $WslUser -ProjectDir $ProjectDir
        if (-not $target.User) {
            Show-FailureAndExit (
                "Could not find a working checkout at '$ProjectDir' under any WSL user " +
                "(tried: $($target.Tried -join ', ')).`n`n" +
                "If you cloned the repo somewhere else, or use a different WSL username, " +
                "edit `$WslUser / `$ProjectDir at the top of Start-MoliBeans.ps1 to match - " +
                "run 'wsl -d $WslDistro', then 'whoami' and 'pwd' from inside your actual " +
                "project folder to find the right values."
            )
        }
        $ResolvedUser = $target.User
        Write-Log "Resolved WSL user: $ResolvedUser (tried: $($target.Tried -join ', '))"
        Write-Log "Starting it inside WSL2 ($WslDistro, user $ResolvedUser)..."

        # Start the backend inside WSL2. The cd/source steps run in the
        # foreground (with explicit || checks) so a failure there produces a
        # real non-zero exit code and an error message we can capture, rather
        # than being silently swallowed. Only the actual server process is
        # backgrounded with nohup so it keeps running after this wsl.exe
        # invocation returns. App logs go to /tmp/spk.log inside WSL.
        $startCmd = @"
echo "DIAG: whoami=`$(whoami) HOME=`$HOME"
cd $ProjectDir || { echo 'ERROR: cd to project dir failed - check `$ProjectDir in Start-MoliBeans.ps1'; exit 10; }
source .venv/bin/activate || { echo 'ERROR: could not activate .venv - did setup_local_prototype.sh finish successfully?'; echo "DIAG: pwd=`$(pwd)"; ls -la; exit 11; }
nohup ./start.sh > /tmp/spk.log 2>&1 &
disown
sleep 1
"@

        $wslStdout = Join-Path $env:TEMP "moli-beans-wsl-stdout.log"
        $wslStderr = Join-Path $env:TEMP "moli-beans-wsl-stderr.log"
        Remove-Item -ErrorAction SilentlyContinue $wslStdout, $wslStderr

        $proc = Start-Process -FilePath "wsl.exe" -ArgumentList @("-d", $WslDistro, "-u", $ResolvedUser, "--", "bash", "-lc", $startCmd) -WindowStyle Hidden -Wait -PassThru -RedirectStandardOutput $wslStdout -RedirectStandardError $wslStderr
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
