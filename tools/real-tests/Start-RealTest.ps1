# LoopMedic REAL power-off tests. Run as admin:  Start-RealTest.ps1 -Test 1|2|3
#   1  pump stopped, CPU normal  -> alarm + 30 s countdown -> power off   (real install folder: the
#                                   "why did my PC turn off?" notice shows after the restart)
#   2  CPU over its limit         -> CPU trip, then "hotter, faster" cuts the countdown to ~5 s
#   3  pump stopped + CPU hot     -> pump trip with the 5 s countdown
# Tests 2 and 3 use a separate folder whose config has a CPU limit of 20 C (below idle), so the real
# config.json is never changed and the normal watchdog can't trip after the restart.
# The test watchdog runs as SYSTEM (like the real one) from a one-off task with no trigger, and exits by
# itself after 3 minutes if nothing powers off. Nothing here runs at boot.
# -Rehearse: same test in a throwaway folder with --dry-run - nothing powers off, no tray pop-up.
param([Parameter(Mandatory)][ValidateSet(1, 2, 3)][int]$Test, [switch]$Rehearse)
$ErrorActionPreference = 'Stop'
$real = 'C:\ProgramData\LoopMedic'
$exe = Join-Path $real 'LoopMedic.exe'
$testRoot = if ($Rehearse) { 'C:\ProgramData\LoopMedic-rehearse' } else { 'C:\ProgramData\LoopMedic-realtest' }
$taskName = 'LoopMedic-RealTest'
$note = Join-Path $PSScriptRoot 'realtest-notes.txt'

Unregister-ScheduledTask -TaskName $taskName -Confirm:$false -ErrorAction SilentlyContinue
# An earlier test/rehearsal watchdog that's still running: stop it by PID (only ones using a test folder).
Get-CimInstance Win32_Process -Filter "Name='LoopMedic.exe'" |
    Where-Object { $_.CommandLine -match 'LoopMedic-(rehearse|realtest)' -or ($_.CommandLine -match '--simulate-pump' -and $_.CommandLine -notmatch '--root') } |
    ForEach-Object { Stop-Process -Id $_.ProcessId -Force -ErrorAction SilentlyContinue }
Start-Sleep 1

if ($Rehearse) {
    Remove-Item $testRoot -Recurse -Force -ErrorAction SilentlyContinue
    New-Item -ItemType Directory $testRoot, (Join-Path $testRoot 'control') | Out-Null   # control = where the tray says it's running (the installer makes it)
    $cfg = Get-Content (Join-Path $real 'config.json') -Raw | ConvertFrom-Json
    if ($Test -ne 1) { $cfg.CpuMaxTempC = 20 }
    $cfg | ConvertTo-Json | Set-Content (Join-Path $testRoot 'config.json') -Encoding UTF8
    Copy-Item (Join-Path $real 'cpu-limits.json') $testRoot
    if (Test-Path (Join-Path $real 'selftest.json')) { Copy-Item (Join-Path $real 'selftest.json') $testRoot }
    $root = $testRoot
    $taskArgs = "--worker --root `"$testRoot`" --dry-run --no-eventlog --duration 90"
    if ($Test -ne 2) { $taskArgs += ' --simulate-pump 0 --simulate-healthy 20' }
} elseif ($Test -eq 1) {
    # The test watchdog takes the real folder, so the normal one steps aside until the restart.
    Stop-ScheduledTask -TaskName 'LoopMedic'
    Start-Sleep 2
    Get-Process LoopMedic -ErrorAction SilentlyContinue | Where-Object { $_.Path -eq $exe } | ForEach-Object { Stop-Process -Id $_.Id -Force }
    $root = $real
    $taskArgs = '--worker --simulate-pump 0 --simulate-healthy 20 --duration 180'
} else {
    # Separate folder: the normal watchdog keeps protecting the PC the whole time.
    Remove-Item $testRoot -Recurse -Force -ErrorAction SilentlyContinue
    New-Item -ItemType Directory $testRoot, (Join-Path $testRoot 'control') | Out-Null   # control = where the tray says it's running (the installer makes it)
    $cfg = Get-Content (Join-Path $real 'config.json') -Raw | ConvertFrom-Json
    $cfg.CpuMaxTempC = 20
    $cfg | ConvertTo-Json | Set-Content (Join-Path $testRoot 'config.json') -Encoding UTF8
    Copy-Item (Join-Path $real 'cpu-limits.json') $testRoot
    if (Test-Path (Join-Path $real 'selftest.json')) { Copy-Item (Join-Path $real 'selftest.json') $testRoot }   # no monthly self-test mid-run
    $root = $testRoot
    $taskArgs = "--worker --root `"$testRoot`" --duration 180"
    if ($Test -eq 3) { $taskArgs += ' --simulate-pump 0 --simulate-healthy 20' }
    # A second tray icon for the test folder, so the alarm pops up like the real one.
    Start-Process (Join-Path $real 'LoopMedicTray.exe') -ArgumentList "--dir `"$testRoot`""
}

$action = New-ScheduledTaskAction -Execute $exe -Argument $taskArgs
$principal = New-ScheduledTaskPrincipal -UserId 'SYSTEM' -LogonType ServiceAccount -RunLevel Highest
Register-ScheduledTask -TaskName $taskName -Action $action -Principal $principal | Out-Null
"$(Get-Date -Format s)  $(if ($Rehearse) { "REHEARSAL" } else { "REAL TEST" }) $Test started (root $root, args: $taskArgs)" | Add-Content $note
Start-ScheduledTask -TaskName $taskName
if ($Rehearse) { "REHEARSAL $Test running (dry run, 90 s)."; return }
"REAL TEST $Test running. Expected power-off in about " + @{1 = '60'; 2 = '55'; 3 = '35'}[$Test] + " seconds."
