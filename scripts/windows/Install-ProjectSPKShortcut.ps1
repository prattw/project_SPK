# Create a Desktop and Start menu shortcut that launches the local app.
# Run once from PowerShell:
#   powershell -ExecutionPolicy Bypass -File .\scripts\windows\Install-ProjectSPKShortcut.ps1

$ErrorActionPreference = "Stop"

$StartScript = (Resolve-Path (Join-Path $PSScriptRoot "Start-ProjectSPK.ps1")).Path
$Repo = (Resolve-Path (Join-Path $PSScriptRoot "..\..")).Path

function New-ProjectSPKShortcut([string]$ShortcutPath) {
    $shell = New-Object -ComObject WScript.Shell
    $shortcut = $shell.CreateShortcut($ShortcutPath)
    $shortcut.TargetPath = "powershell.exe"
    $shortcut.Arguments = "-NoLogo -NoProfile -WindowStyle Hidden -ExecutionPolicy Bypass -File `"$StartScript`""
    $shortcut.WorkingDirectory = $Repo
    $shortcut.Description = "Project SPK (this laptop, offline)"
    $shortcut.Save()
}

$DesktopShortcut = Join-Path ([Environment]::GetFolderPath("Desktop")) "Project SPK.lnk"
New-ProjectSPKShortcut $DesktopShortcut
Write-Host "Created desktop shortcut: $DesktopShortcut"

$StartMenuDir = Join-Path $env:APPDATA "Microsoft\Windows\Start Menu\Programs"
$StartMenuShortcut = Join-Path $StartMenuDir "Project SPK.lnk"
New-ProjectSPKShortcut $StartMenuShortcut
Write-Host "Created Start menu shortcut: $StartMenuShortcut"
Write-Host ""
Write-Host "Double-click Project SPK on the desktop. It starts Ollama if needed,"
Write-Host "starts the app on this laptop only, and opens it in a window."
