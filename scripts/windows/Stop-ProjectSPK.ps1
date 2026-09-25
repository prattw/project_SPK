# Stop the Project SPK server started by Start-ProjectSPK.ps1.
# Leaves Ollama running so the next launch does not have to reload models.

$ErrorActionPreference = "Stop"

$PidFile = Join-Path $env:LOCALAPPDATA "ProjectSPK\uvicorn.pid"
if (-not (Test-Path $PidFile)) {
    Write-Host "Project SPK is not running (no pid file)."
    exit 0
}

$procId = 0
[void][int]::TryParse((Get-Content $PidFile -Raw).Trim(), [ref]$procId)
if ($procId -gt 0) {
    $proc = Get-Process -Id $procId -ErrorAction SilentlyContinue
    if ($proc) {
        Stop-Process -Id $procId -Force
        Write-Host "Stopped Project SPK (pid $procId)."
    } else {
        Write-Host "Project SPK was already stopped."
    }
}
Remove-Item -Path $PidFile -Force -ErrorAction SilentlyContinue
