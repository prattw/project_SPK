# One-time setup: run Project SPK on this Windows laptop, with Ollama instead
# of the OpenAI API. See WINDOWS.md.
#
# Run once, while online, from the repo root or from anywhere:
#   powershell -ExecutionPolicy Bypass -File .\scripts\setup_windows.ps1
#   powershell -ExecutionPolicy Bypass -File .\scripts\setup_windows.ps1 -Model qwen2.5:14b-instruct
#
# Install Ollama for Windows and Python 3.12 first (both "Add to PATH").
# This script will not use the Linux installer.

param(
    [string]$Model = ""
)

$ErrorActionPreference = "Stop"

if ($env:OS -ne "Windows_NT") {
    Write-Error "This setup is for Windows. On the Mac mini, run ./scripts/setup_mac_mini.sh"
}

$Root = Split-Path -Parent $PSScriptRoot
Set-Location $Root

Write-Host "== Project SPK on this Windows laptop =="

$ramBytes = (Get-CimInstance Win32_ComputerSystem).TotalPhysicalMemory
$ramGb = [int]($ramBytes / 1GB)
Write-Host "-- $ramGb GB system RAM"

$vramGb = 0
$nvidia = Get-Command nvidia-smi -ErrorAction SilentlyContinue
if ($nvidia) {
    $mbText = (& nvidia-smi --query-gpu=memory.total --format=csv,noheader,nounits | Select-Object -First 1)
    if ($mbText -match '^\s*(\d+)') {
        $vramGb = [int]([int]$Matches[1] / 1024)
        Write-Host "-- NVIDIA GPU with about $vramGb GB VRAM. Ollama uses it on its own."
    }
} else {
    Write-Host "-- No nvidia-smi. Answers will use the CPU, or whatever GPU the Ollama app already sees."
}

$embedModel = "nomic-embed-text"
if ($Model) {
    $chatModel = $Model
    $visionModel = "qwen2.5vl:7b"
} elseif ($vramGb -ge 16 -or ($vramGb -eq 0 -and $ramGb -ge 32)) {
    $chatModel = "qwen2.5:14b-instruct"
    $visionModel = "qwen2.5vl:7b"
} elseif ($vramGb -ge 6 -or $ramGb -ge 16) {
    $chatModel = "qwen2.5:7b-instruct"
    $visionModel = "qwen2.5vl:3b"
} else {
    # Small RAM and no useful GPU. One small vision model does both jobs.
    $chatModel = "qwen2.5vl:3b"
    $visionModel = "qwen2.5vl:3b"
}

$contextChars = 40000
if ($ramGb -ge 32 -or $vramGb -ge 16) {
    $contextChars = 80000
}

Write-Host "-- Chat model:      $chatModel"
Write-Host "-- Vision model:    $visionModel"
Write-Host "-- Embedding model: $embedModel"
Write-Host ""

if (-not (Get-Command ollama -ErrorAction SilentlyContinue)) {
    Write-Error "Ollama is not on PATH. Install it from https://ollama.com/download/windows, open the app once, then run this script again."
}

function Test-OllamaUp {
    try {
        $null = Invoke-WebRequest -Uri "http://127.0.0.1:11434/api/tags" -UseBasicParsing -TimeoutSec 2
        return $true
    } catch {
        return $false
    }
}

Write-Host "-- Starting Ollama..."
if (-not (Test-OllamaUp)) {
    Start-Process -FilePath "ollama" -ArgumentList "serve" -WindowStyle Hidden
}
$ready = $false
foreach ($i in 1..20) {
    if (Test-OllamaUp) { $ready = $true; break }
    Start-Sleep -Seconds 1
}
if (-not $ready) {
    Write-Error "Ollama did not start. Open the Ollama app from the Start menu and run this script again."
}

Write-Host "-- Pulling chat model ($chatModel). The first download is several GB."
& ollama pull $chatModel
if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }
if ($visionModel -ne $chatModel) {
    Write-Host "-- Pulling vision model ($visionModel)..."
    & ollama pull $visionModel
    if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }
}
Write-Host "-- Pulling embedding model ($embedModel)..."
& ollama pull $embedModel
if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }

$python = Get-Command python -ErrorAction SilentlyContinue
if (-not $python) {
    Write-Error "python is not on PATH. Install Python 3.12 from https://www.python.org/downloads/windows/ and check 'Add python.exe to PATH'."
}

if (-not (Test-Path ".\.venv\Scripts\python.exe")) {
    Write-Host "-- Creating Python virtual environment..."
    & python -m venv .venv
    if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }
}

Write-Host "-- Installing Python dependencies..."
& .\.venv\Scripts\python.exe -m pip install --upgrade pip
if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }
& .\.venv\Scripts\python.exe -m pip install -r requirements.txt
if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }

function Set-EnvValue([string]$Key, [string]$Value) {
    $path = Join-Path $Root ".env"
    $text = [System.IO.File]::ReadAllText($path)
    $pattern = "(?m)^" + [regex]::Escape($Key) + "=.*$"
    $line = "$Key=$Value"
    if ([regex]::IsMatch($text, $pattern)) {
        $text = [regex]::Replace($text, $pattern, $line)
    } else {
        if ($text.Length -gt 0 -and -not $text.EndsWith("`n")) { $text += "`r`n" }
        $text += "$line`r`n"
    }
    [System.IO.File]::WriteAllText($path, $text)
}

if (-not (Test-Path ".env")) {
    Copy-Item ".env.windows.example" ".env"
    Set-EnvValue "OPENAI_MODEL" $chatModel
    Set-EnvValue "OPENAI_VISION_MODEL" $visionModel
    Set-EnvValue "OPENAI_EMBEDDING_MODEL" $embedModel
    Set-EnvValue "MAX_CONTEXT_CHARS" "$contextChars"
    $secret = & .\.venv\Scripts\python.exe -c "import secrets; print(secrets.token_hex(32))"
    Set-EnvValue "AUTH_SECRET" $secret.Trim()
    Write-Host "-- Wrote .env for this laptop."
} else {
    Write-Host "-- .env already exists, so it was left as-is."
    Write-Host "   If it still points at OpenAI, move it aside and run this script again."
}

Write-Host ""
Write-Host "== This laptop is ready =="
Write-Host ""
Write-Host "Chat, embeddings, and scanned-page reading all go to Ollama on this machine."
Write-Host "Nothing is sent to OpenAI. The app listens only on 127.0.0.1."
Write-Host ""
Write-Host "  1. powershell -ExecutionPolicy Bypass -File .\scripts\windows\Start-ProjectSPK.ps1"
Write-Host "  2. Sign in with your roster email. No message is sent; the check is local."
Write-Host "  3. Put the library on disk before you lose the network. Either copy the"
Write-Host "     Mac's finished index (WINDOWS.md) or, with the server stopped:"
Write-Host ""
Write-Host "       .\.venv\Scripts\python.exe scripts\bulk_ingest.py `"D:\path\to\documents`""
Write-Host ""
Write-Host "A desktop shortcut, once you want one:"
Write-Host "  powershell -ExecutionPolicy Bypass -File .\scripts\windows\Install-ProjectSPKShortcut.ps1"
