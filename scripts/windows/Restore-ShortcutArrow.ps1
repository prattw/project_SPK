<#
.SYNOPSIS
  Undoes Remove-ShortcutArrow.ps1 - restores the normal Windows shortcut
  arrow badge on all desktop/Start Menu icons.

.NOTES
  Must be run as Administrator (it writes to HKEY_LOCAL_MACHINE).
#>

$ErrorActionPreference = "Stop"

$currentUser = [Security.Principal.WindowsIdentity]::GetCurrent()
$isAdmin = (New-Object Security.Principal.WindowsPrincipal($currentUser)).IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)
if (-not $isAdmin) {
    Write-Host "Relaunching as Administrator (a UAC prompt should appear)..."
    Start-Process -FilePath "powershell.exe" -ArgumentList @("-ExecutionPolicy", "Bypass", "-File", "`"$PSCommandPath`"") -Verb RunAs
    exit
}

$RegPath = "HKLM:\SOFTWARE\Microsoft\Windows\CurrentVersion\Explorer\Shell Icons"
if (Test-Path $RegPath) {
    Remove-ItemProperty -Path $RegPath -Name "29" -ErrorAction SilentlyContinue
}

Write-Host "Restarting Explorer..."
Stop-Process -Name explorer -Force -ErrorAction SilentlyContinue
Start-Sleep -Seconds 1
Start-Process explorer.exe

Write-Host ""
Write-Host "== Done =="
Write-Host "The default shortcut arrow should be back on all icons."
