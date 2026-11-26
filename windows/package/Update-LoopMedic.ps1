# LoopMedic by Jacob Stiltner, (c) 2026
# Update in place, keeping your settings. The tray app starts this (as admin) after it has downloaded a new
# version and checked its signature; you can also run it yourself from an unzipped release folder.
#   1. builds the new version (build.ps1)
#   2. takes your current settings (C:\ProgramData\LoopMedic\config.json) and adds any setting the new
#      version introduced, with its default
#   3. installs (Install-LoopMedic.ps1): watchdog and tray restart on the new version
$ErrorActionPreference = 'Stop'
$here = $PSScriptRoot
$dest = 'C:\ProgramData\LoopMedic'
$Host.UI.RawUI.WindowTitle = 'Updating LoopMedic'
try {
    if (-not ([Security.Principal.WindowsPrincipal][Security.Principal.WindowsIdentity]::GetCurrent()).IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)) {
        throw 'This needs to run as administrator.'
    }
    $current = Join-Path $dest 'config.json'
    if (-not (Test-Path $current)) { throw "LoopMedic isn't installed on this PC - run Install.bat instead." }

    Write-Host 'Updating LoopMedic - your settings are kept.' -ForegroundColor Cyan
    Get-ChildItem $here -Recurse -File | Unblock-File

    Write-Host '1/3  Building...'
    & (Join-Path $here 'build.ps1') | Out-Null
    foreach ($f in 'bin\LoopMedic.exe', 'bin\LoopMedicTray.exe') {
        if (-not (Test-Path (Join-Path $here $f))) { throw "Build failed: $f is missing." }
    }

    Write-Host '2/3  Keeping your settings...'
    $template = Join-Path $here 'config.json'      # the new version's defaults
    $mine = Get-Content $current -Raw | ConvertFrom-Json
    $defaults = Get-Content $template -Raw | ConvertFrom-Json
    foreach ($p in $defaults.PSObject.Properties) {
        if (-not ($mine.PSObject.Properties.Name -contains $p.Name)) {
            $mine | Add-Member -NotePropertyName $p.Name -NotePropertyValue $p.Value
            Write-Host "     new setting: $($p.Name) = $($p.Value)"
        }
    }
    $mine | ConvertTo-Json | Set-Content $template -Encoding UTF8

    Write-Host '3/3  Installing...'
    & (Join-Path $here 'Install-LoopMedic.ps1')
    Write-Host ''
    Write-Host 'LoopMedic is updated and running.' -ForegroundColor Green
    Start-Sleep 4
}
catch {
    Write-Host ''
    Write-Host "UPDATE FAILED: $($_.Exception.Message)" -ForegroundColor Red
    Write-Host 'Your previous LoopMedic keeps running if the install step was not reached.'
    Write-Host 'To update by hand: download LoopMedic.zip from the GitHub Releases page and run Install.bat.'
    Read-Host 'Press Enter to close'
    exit 1
}
