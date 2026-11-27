# LoopMedic by Jacob Stiltner, (c) 2026
# Run as admin.
#   Install-LoopMedic.ps1            install / update the watchdog + tray app and start them
#   Install-LoopMedic.ps1 -Uninstall stop and remove everything
# Looks for its files next to itself (release folder) or in bin\ (source folder, after build.ps1).
param([switch]$Uninstall, [string]$LhmSource = '')
$ErrorActionPreference = 'Stop'
$TaskName = 'LoopMedic'
$Dest = 'C:\ProgramData\LoopMedic'
$Src = $PSScriptRoot
$Exe = Join-Path $Dest 'LoopMedic.exe'
$TrayExe = Join-Path $Dest 'LoopMedicTray.exe'
$RunKey = 'HKLM:\SOFTWARE\Microsoft\Windows\CurrentVersion\Run'

$admin = ([Security.Principal.WindowsPrincipal][Security.Principal.WindowsIdentity]::GetCurrent()).IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)
if (-not $admin) { throw 'Run this as admin.' }

function Stop-LoopMedic {
    # The task, then anything still running from the install folder - matched by exact path, nothing else.
    if (Get-ScheduledTask -TaskName $TaskName -ErrorAction SilentlyContinue) {
        Stop-ScheduledTask -TaskName $TaskName -ErrorAction SilentlyContinue
        Unregister-ScheduledTask -TaskName $TaskName -Confirm:$false
        Write-Host "Removed the old $TaskName task."
    }
    Get-Process -Name LoopMedic, LoopMedicTray -ErrorAction SilentlyContinue |
        Where-Object { $_.Path -eq $Exe -or $_.Path -eq $TrayExe } | Stop-Process -Force
    Start-Sleep -Milliseconds 800
}

function Remove-PumpGuard {
    # LoopMedic was called PumpGuard up to v2.2. Remove that install (any version) so two watchdogs never
    # run side by side; keep its log and self-test/power-off records.
    $old = 'C:\ProgramData\PumpGuard'
    $task = Get-ScheduledTask -TaskName 'PumpGuard' -ErrorAction SilentlyContinue
    if (-not $task -and -not (Test-Path $old)) { return }
    if ($task) {
        Stop-ScheduledTask -TaskName 'PumpGuard' -ErrorAction SilentlyContinue
        Unregister-ScheduledTask -TaskName 'PumpGuard' -Confirm:$false
    }
    Get-Process -Name PumpGuard, PumpGuardTray -ErrorAction SilentlyContinue |
        Where-Object { $_.Path -like "$old\*" } | Stop-Process -Force
    Get-CimInstance Win32_Process -Filter "Name='powershell.exe'" -ErrorAction SilentlyContinue |
        Where-Object { $_.CommandLine -like "*$old\PumpGuard.ps1*" -or $_.CommandLine -like "*$old\PowerOff.ps1*" } |
        ForEach-Object { Stop-Process -Id $_.ProcessId -Force -ErrorAction SilentlyContinue }
    Remove-ItemProperty -Path $RunKey -Name 'PumpGuardTray' -ErrorAction SilentlyContinue
    Start-Sleep -Milliseconds 800
    if (Test-Path $old) {
        if (-not $Uninstall) {
            New-Item -ItemType Directory -Force $Dest | Out-Null
            foreach ($f in 'pumpguard.log', 'pumpguard.log.old') {
                if (Test-Path "$old\$f") { Copy-Item "$old\$f" (Join-Path $Dest ($f -replace '^pumpguard', 'pumpguard-before-rename')) -Force }
            }
            foreach ($f in 'selftest.json', 'last-poweroff.json') {
                if ((Test-Path "$old\$f") -and -not (Test-Path "$Dest\$f")) { Copy-Item "$old\$f" $Dest }
            }
        }
        Remove-Item $old -Recurse -Force -ErrorAction SilentlyContinue
    }
    try { if ([Diagnostics.EventLog]::SourceExists('PumpGuard')) { Remove-EventLog -Source 'PumpGuard' } } catch { }
    Write-Host 'Removed the old PumpGuard install (LoopMedic replaces it; its log is kept as pumpguard-before-rename.log).'
}

Stop-LoopMedic
Remove-PumpGuard

if ($Uninstall) {
    Remove-ItemProperty -Path $RunKey -Name 'LoopMedicTray' -ErrorAction SilentlyContinue
    if (Test-Path $Dest) { Remove-Item $Dest -Recurse -Force }
    try { if ([Diagnostics.EventLog]::SourceExists('LoopMedic')) { Remove-EventLog -Source 'LoopMedic' } } catch { }
    Write-Host 'LoopMedic uninstalled.'
    return
}

# ---- find the files ----
function Find-First([string[]]$candidates) { $candidates | Where-Object { $_ -and (Test-Path $_) } | Select-Object -First 1 }
$exeFrom  = Find-First @((Join-Path $Src 'LoopMedic.exe'), (Join-Path $Src 'bin\LoopMedic.exe'))
$trayFrom = Find-First @((Join-Path $Src 'LoopMedicTray.exe'), (Join-Path $Src 'bin\LoopMedicTray.exe'))
$lhmFrom  = Find-First @((Join-Path $Src 'lhm'), $LhmSource, (Join-Path $Src '..\tools\LibreHardwareMonitor-0.9.6'))
$limits   = Find-First @((Join-Path $Src 'cpu-limits.json'))
if (-not $exeFrom) { throw 'LoopMedic.exe not found (source folder: run build.ps1 first).' }
if (-not $lhmFrom -or -not (Test-Path (Join-Path $lhmFrom 'LibreHardwareMonitorLib.dll'))) { throw 'The lhm\ sensor library folder is missing.' }
if (-not $limits) { throw 'cpu-limits.json is missing.' }

# ---- check config.json before installing: a broken file would leave the PC unprotected ----
try { $cfg = Get-Content (Join-Path $Src 'config.json') -Raw | ConvertFrom-Json }
catch { throw "config.json is not valid JSON: $($_.Exception.Message)" }
$checks = @{ PumpMinRpm = @(50, 5000); PumpFailSeconds = @(2, 120); CpuMaxTempC = @(0, 110); CpuHotSeconds = @(2, 300);
             PollSeconds = @(1, 10); ShutdownDelaySeconds = @(5, 120); RearmSeconds = @(10, 3600) }
$optional = @{ PumpStoppedRpm = @(0, 1000); PumpFailSecondsCool = @(2, 600); CoolCpuTempC = @(20, 90); HotShutdownDelaySeconds = @(2, 60) }
foreach ($set in @(@{ c = $checks; req = $true }, @{ c = $optional; req = $false })) {
    foreach ($k in $set.c.Keys) {
        $v = $cfg.$k; $d = 0.0
        if ($null -eq $v -and -not $set.req) { continue }
        if ($null -eq $v -or -not [double]::TryParse([string]$v, [Globalization.NumberStyles]::Float, [Globalization.CultureInfo]::InvariantCulture, [ref]$d)) { throw "config.json: '$k' is missing or not a number." }
        if ($d -lt $set.c[$k][0] -or $d -gt $set.c[$k][1]) { throw "config.json: '$k' = $d is outside $($set.c[$k][0])..$($set.c[$k][1])." }
    }
}
if ($null -ne $cfg.PumpStoppedRpm -and [double]$cfg.PumpStoppedRpm -ge [double]$cfg.PumpMinRpm) { throw 'config.json: PumpStoppedRpm must be below PumpMinRpm.' }

# ---- program files go in an admin-only folder: a SYSTEM task must not run files normal users can edit ----
New-Item -ItemType Directory -Force "$Dest\lhm" | Out-Null
Copy-Item $exeFrom $Exe -Force
$exeConfig = "$exeFrom.config"
if (Test-Path $exeConfig) { Copy-Item $exeConfig "$Exe.config" -Force }
if ($trayFrom) { Copy-Item $trayFrom $TrayExe -Force }
Copy-Item (Join-Path $Src 'config.json') $Dest -Force
Copy-Item $limits $Dest -Force
Copy-Item (Join-Path $lhmFrom '*.dll') "$Dest\lhm" -Force
& icacls.exe $Dest /inheritance:r /grant:r 'SYSTEM:(OI)(CI)F' 'Administrators:(OI)(CI)F' 'Users:(OI)(CI)RX' | Out-Null

# ---- mail.json: email alerts (optional). Setup may have made one next to this script; otherwise a switched-off
#      template is written once and then left alone (updates keep your mail settings). The watchdog encrypts a
#      typed-in password as soon as it sees it, and locks the file to SYSTEM + Administrators.
$mailDest = Join-Path $Dest 'mail.json'
$mailNew = Join-Path $Src 'mail.json'
if (Test-Path $mailNew) {
    Copy-Item $mailNew $mailDest -Force
    Remove-Item $mailNew -Force -ErrorAction SilentlyContinue
} elseif (-not (Test-Path $mailDest)) {
    @'
{
  "_read_me": "LoopMedic email alerts (optional). Fill in your mail server, set Enabled to true and save. Open this file in Notepad run as administrator. LoopMedic picks up the change within seconds - no restart. Type the password as plain text: LoopMedic encrypts it as soon as it sees it, and only this PC can read it back. Then right-click the tray icon > Send a test email.",
  "_security": "starttls = port 587 (most providers). none = port 25 (a local relay with no encryption). Port 465 (SSL) is not supported.",
  "_tip": "Gmail, Outlook and most big providers need an app password here, not your normal password. To = one address, or several separated by commas.",
  "Enabled": false,
  "Server": "smtp.example.com",
  "Port": 587,
  "Security": "starttls",
  "Username": "",
  "Password": "",
  "From": "",
  "To": ""
}
'@ | Set-Content $mailDest -Encoding UTF8
}
& icacls.exe $mailDest /inheritance:r /grant:r 'SYSTEM:F' 'Administrators:F' | Out-Null

# control\ is the one folder users may write: the tray app drops pause/test/cancel requests here.
# The watchdog only ever reads dates out of these files.
New-Item -ItemType Directory -Force "$Dest\control" | Out-Null
& icacls.exe "$Dest\control" /grant 'Users:(OI)(CI)M' | Out-Null

# ---- Event Viewer source (Windows Logs > Application > LoopMedic) ----
try { if (-not [Diagnostics.EventLog]::SourceExists('LoopMedic')) { New-EventLog -LogName Application -Source 'LoopMedic' } }
catch { Write-Host "Note: couldn't register the Event Viewer source ($($_.Exception.Message)); the log file still has everything." }

# ---- the task: starts at boot as SYSTEM. LoopMedic.exe supervises the watchdog itself (restart in
#      seconds); the task is the second line: restart after 1 min, and a 5-minute "is it running?" check.
$action = New-ScheduledTaskAction -Execute $Exe
$triggers = @(
    (New-ScheduledTaskTrigger -AtStartup),
    (New-ScheduledTaskTrigger -Once -At (Get-Date).AddMinutes(1) -RepetitionInterval (New-TimeSpan -Minutes 5)))
$principal = New-ScheduledTaskPrincipal -UserId 'SYSTEM' -LogonType ServiceAccount -RunLevel Highest
$settings = New-ScheduledTaskSettingsSet -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries `
    -StartWhenAvailable -ExecutionTimeLimit ([TimeSpan]::Zero) `
    -RestartCount 999 -RestartInterval (New-TimeSpan -Minutes 1) -MultipleInstances IgnoreNew
Register-ScheduledTask -TaskName $TaskName -Action $action -Trigger $triggers -Principal $principal -Settings $settings `
    -Description 'LoopMedic by Jacob Stiltner. Powers the PC off if the AIO pump stops or the CPU overheats. Log: C:\ProgramData\LoopMedic\loopmedic.log' | Out-Null
Start-ScheduledTask -TaskName $TaskName

if (Test-Path $TrayExe) {
    Set-ItemProperty -Path $RunKey -Name 'LoopMedicTray' -Value "`"$TrayExe`""
    # explorer.exe starts it as the signed-in user (not elevated), like a normal logon start.
    Start-Process explorer.exe -ArgumentList "`"$TrayExe`""
    Write-Host 'Tray app installed (starts at logon).'
}

Start-Sleep -Seconds 6
Write-Host 'LoopMedic installed and started. Latest log lines:'
Get-Content "$Dest\loopmedic.log" -Tail 4 -ErrorAction SilentlyContinue
