<#
.SYNOPSIS
  One-time installer: creates a "Moli Beans" desktop icon (and Start Menu
  entry) that launches the local prototype and opens it in an app-like
  window.

.DESCRIPTION
  Copies the launcher scripts and app icon to a local folder under
  %LOCALAPPDATA%\MoliBeans (so the shortcut doesn't depend on the WSL2
  network path staying available), then creates:
    - A Desktop shortcut: "Moli Beans"
    - A Start Menu shortcut: "Moli Beans"
  Both point at Start-MoliBeans.ps1, which starts the backend inside WSL2
  if needed and opens the app in a chromeless browser window.

  This is a laptop-only rebrand of Project SPK's local prototype — the icon
  and name only affect this desktop shortcut and the browser tab it opens
  (via APP_DISPLAY_NAME/APP_ICON_PATH in .env). Production on Railway is
  untouched and keeps the Project SPK name.

  Run this ONCE. Re-running it is safe (it just overwrites the copied
  files and recreates the shortcuts).

.NOTES
  Run from PowerShell, not by double-clicking (Windows opens .ps1 files in
  Notepad by default):

    powershell -ExecutionPolicy Bypass -File .\Install-MoliBeansShortcut.ps1
#>

$ErrorActionPreference = "Stop"

$InstallDir = "$env:LOCALAPPDATA\MoliBeans"
New-Item -ItemType Directory -Force -Path $InstallDir | Out-Null

$SourceDir = $PSScriptRoot
Copy-Item -Path (Join-Path $SourceDir "Start-MoliBeans.ps1") -Destination $InstallDir -Force
Copy-Item -Path (Join-Path $SourceDir "Stop-MoliBeans.ps1")  -Destination $InstallDir -Force
Copy-Item -Path (Join-Path $SourceDir "app-icon.ico")        -Destination $InstallDir -Force

$StartScript = Join-Path $InstallDir "Start-MoliBeans.ps1"
$IconPath    = Join-Path $InstallDir "app-icon.ico"

function New-MoliBeansShortcut($ShortcutPath) {
    $shell = New-Object -ComObject WScript.Shell
    $shortcut = $shell.CreateShortcut($ShortcutPath)
    $shortcut.TargetPath = "powershell.exe"
    $shortcut.Arguments = "-NoLogo -NoProfile -WindowStyle Hidden -ExecutionPolicy Bypass -File `"$StartScript`""
    $shortcut.WorkingDirectory = $InstallDir
    $shortcut.IconLocation = "$IconPath,0"
    $shortcut.Description = "Moli Beans (Project SPK local prototype)"
    $shortcut.Save()
}

$DesktopShortcut = Join-Path ([Environment]::GetFolderPath("Desktop")) "Moli Beans.lnk"
New-MoliBeansShortcut $DesktopShortcut
Write-Host "Created desktop shortcut: $DesktopShortcut"

$StartMenuDir = "$env:APPDATA\Microsoft\Windows\Start Menu\Programs"
$StartMenuShortcut = Join-Path $StartMenuDir "Moli Beans.lnk"
New-MoliBeansShortcut $StartMenuShortcut
Write-Host "Created Start Menu shortcut: $StartMenuShortcut"

Write-Host ""
Write-Host "== Done =="
Write-Host "Double-click 'Moli Beans' on your Desktop (or search for it in the"
Write-Host "Start Menu) to launch the app. First launch may take a few seconds"
Write-Host "while the backend starts inside WSL2."
Write-Host ""
Write-Host "To pin it to the taskbar: right-click the new Desktop shortcut and"
Write-Host "choose 'Pin to taskbar' (or pin it from the Start Menu entry after"
Write-Host "running it once, whichever your Windows build offers)."
