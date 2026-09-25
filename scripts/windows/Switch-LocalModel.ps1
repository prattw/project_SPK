# Pull a different chat model and point .env at it. Restart the app after.
#   powershell -ExecutionPolicy Bypass -File .\scripts\windows\Switch-LocalModel.ps1 qwen2.5:14b-instruct
#
# Do not change OPENAI_EMBEDDING_MODEL after the index is built. A different
# embedding model cannot search the existing vectors.

param(
    [Parameter(Mandatory = $true, Position = 0)]
    [string]$Model
)

$ErrorActionPreference = "Stop"
$Root = (Resolve-Path (Join-Path $PSScriptRoot "..\..")).Path
Set-Location $Root

if (-not (Test-Path ".env")) {
    Write-Error ".env not found. Run scripts\setup_windows.ps1 first."
}
if (-not (Get-Command ollama -ErrorAction SilentlyContinue)) {
    Write-Error "Ollama is not on PATH."
}

Write-Host "-- Pulling $Model (skips the download if it is already on disk)..."
& ollama pull $Model
if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }

$path = Join-Path $Root ".env"
$text = [System.IO.File]::ReadAllText($path)
$line = "OPENAI_MODEL=$Model"
if ([regex]::IsMatch($text, "(?m)^OPENAI_MODEL=.*$")) {
    $text = [regex]::Replace($text, "(?m)^OPENAI_MODEL=.*$", $line)
} else {
    if ($text.Length -gt 0 -and -not $text.EndsWith("`n")) { $text += "`r`n" }
    $text += "$line`r`n"
}
[System.IO.File]::WriteAllText($path, $text)
Write-Host "-- .env updated: OPENAI_MODEL=$Model"
Write-Host "-- Stop the app and start it again."
