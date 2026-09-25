# Start Project SPK on this Windows laptop and open it in an app window.
# Ollama and the app both stay on 127.0.0.1. See WINDOWS.md.

$ErrorActionPreference = "Stop"

$Repo = (Resolve-Path (Join-Path $PSScriptRoot "..\..")).Path
Set-Location $Repo

$HostAddr = "127.0.0.1"
$PortNum = "8000"
$envFile = Join-Path $Repo ".env"
if (Test-Path $envFile) {
    foreach ($line in Get-Content $envFile) {
        if ($line -match '^HOST=(.+)$') { $HostAddr = $Matches[1].Trim().Trim('"') }
        if ($line -match '^PORT=(.+)$') { $PortNum = $Matches[1].Trim().Trim('"') }
    }
}
if ($HostAddr -eq "0.0.0.0") {
    # The field copy is single-user. Binding every interface was the Mac's
    # setting so a second person on the home LAN could connect.
    Write-Warning "HOST=0.0.0.0 exposes the login page to whatever network this laptop is on. Austere use should stay on 127.0.0.1."
}

$AppUrl = "http://127.0.0.1:${PortNum}"
$HealthUrl = "$AppUrl/health"
$Python = Join-Path $Repo ".venv\Scripts\python.exe"
$LogPath = Join-Path $Repo "spk-windows.log"
$ErrPath = Join-Path $Repo "spk-windows.err.log"
$StateDir = Join-Path $env:LOCALAPPDATA "ProjectSPK"
$PidFile = Join-Path $StateDir "uvicorn.pid"
New-Item -ItemType Directory -Force -Path $StateDir | Out-Null

function Test-AppHealthy {
    try {
        $response = Invoke-WebRequest -Uri $HealthUrl -UseBasicParsing -TimeoutSec 2
        return $response.StatusCode -eq 200
    } catch {
        return $false
    }
}

function Test-OllamaUp {
    try {
        $null = Invoke-WebRequest -Uri "http://127.0.0.1:11434/api/tags" -UseBasicParsing -TimeoutSec 2
        return $true
    } catch {
        return $false
    }
}

if (-not (Test-Path $Python)) {
    Write-Error "Python venv not found at $Python. Run scripts\setup_windows.ps1 while you still have a network."
}
if (-not (Test-Path $envFile)) {
    Write-Error ".env not found. Run scripts\setup_windows.ps1 first."
}

if (-not (Test-OllamaUp)) {
    Write-Host "Starting Ollama..."
    if (-not (Get-Command ollama -ErrorAction SilentlyContinue)) {
        Write-Error "Ollama is not on PATH. Open the Ollama app, or reinstall it from https://ollama.com/download/windows"
    }
    Start-Process -FilePath "ollama" -ArgumentList "serve" -WindowStyle Hidden
    $ready = $false
    foreach ($i in 1..20) {
        if (Test-OllamaUp) { $ready = $true; break }
        Start-Sleep -Seconds 1
    }
    if (-not $ready) {
        Write-Error "Ollama did not start. Open the Ollama app from the Start menu and try again. Models have to already be on disk; this does not download them."
    }
}

if (-not (Test-AppHealthy)) {
    Write-Host "Starting Project SPK..."
    $uvicornArgs = @(
        "-m", "uvicorn", "app.main:app",
        "--host", $HostAddr,
        "--port", $PortNum
    )
    $proc = Start-Process -FilePath $Python -ArgumentList $uvicornArgs -WorkingDirectory $Repo `
        -WindowStyle Hidden -RedirectStandardOutput $LogPath -RedirectStandardError $ErrPath -PassThru
    Set-Content -Path $PidFile -Value $proc.Id -Encoding ascii

    $waited = 0
    while (-not (Test-AppHealthy) -and $waited -lt 45) {
        Start-Sleep -Seconds 1
        $waited++
        if ($proc.HasExited) { break }
    }
    if (-not (Test-AppHealthy)) {
        Write-Warning "Project SPK did not become healthy. Logs: $LogPath and $ErrPath"
        Read-Host "Press Enter to close"
        exit 1
    }
    Write-Host "Project SPK is up at $AppUrl"
} else {
    Write-Host "Project SPK is already running at $AppUrl"
}

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
