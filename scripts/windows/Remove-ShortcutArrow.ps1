<#
.SYNOPSIS
  Removes the little "shortcut arrow" badge from ALL desktop/Start Menu
  shortcut icons on this Windows machine (including the Moli Beans icon).

.DESCRIPTION
  Windows draws that arrow on every .lnk shortcut as a built-in shell
  overlay - it is not a property of any single icon, so there is no way to
  remove it from just one shortcut while keeping it on the rest. This
  script uses the standard, widely-documented approach: it generates a
  fully transparent 32x32 icon and tells Explorer to use that as the
  system-wide "this is a shortcut" overlay instead of the default arrow.
  It does NOT change the "IsShortcut" registry key or anything about how
  shortcuts behave/open - it only swaps the little glyph Explorer draws in
  the bottom-left corner.

  This is entirely reversible - see Restore-ShortcutArrow.ps1.

.NOTES
  Must be run as Administrator (it writes to HKEY_LOCAL_MACHINE).
  Run from an elevated PowerShell window:

    powershell -ExecutionPolicy Bypass -File .\Remove-ShortcutArrow.ps1

  If PowerShell wasn't already elevated, this script will automatically
  relaunch itself with an admin prompt (UAC).
#>

$ErrorActionPreference = "Stop"

$currentUser = [Security.Principal.WindowsIdentity]::GetCurrent()
$isAdmin = (New-Object Security.Principal.WindowsPrincipal($currentUser)).IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)
if (-not $isAdmin) {
    Write-Host "Relaunching as Administrator (a UAC prompt should appear)..."
    Start-Process -FilePath "powershell.exe" -ArgumentList @("-ExecutionPolicy", "Bypass", "-File", "`"$PSCommandPath`"") -Verb RunAs
    exit
}

$IconDir  = "$env:LOCALAPPDATA\MoliBeans"
New-Item -ItemType Directory -Force -Path $IconDir | Out-Null
$BlankIco = Join-Path $IconDir "blank-overlay.ico"

Write-Host "Generating a transparent overlay icon..."
Add-Type -AssemblyName System.Drawing
$bitmap = New-Object System.Drawing.Bitmap 32, 32
$graphics = [System.Drawing.Graphics]::FromImage($bitmap)
$graphics.Clear([System.Drawing.Color]::Transparent)
$graphics.Dispose()
$hIcon = $bitmap.GetHicon()
$icon = [System.Drawing.Icon]::FromHandle($hIcon)
$stream = New-Object System.IO.FileStream($BlankIco, [System.IO.FileMode]::Create)
$icon.Save($stream)
$stream.Close()
$icon.Dispose()
$bitmap.Dispose()

Write-Host "Pointing Explorer's shortcut overlay at it..."
$RegPath = "HKLM:\SOFTWARE\Microsoft\Windows\CurrentVersion\Explorer\Shell Icons"
if (-not (Test-Path $RegPath)) {
    New-Item -Path $RegPath -Force | Out-Null
}
Set-ItemProperty -Path $RegPath -Name "29" -Value "$BlankIco,0" -Type String

Write-Host "Clearing the icon cache and restarting Explorer..."
Stop-Process -Name explorer -Force -ErrorAction SilentlyContinue
Start-Sleep -Seconds 1
Start-Process explorer.exe

Write-Host ""
Write-Host "== Done =="
Write-Host "The shortcut arrow should be gone from every desktop/Start Menu icon,"
Write-Host "including Moli Beans, once Explorer finishes restarting (a few seconds)."
Write-Host "If icons still look wrong, sign out and back in once."
Write-Host ""
Write-Host "To undo this later, run Restore-ShortcutArrow.ps1."
