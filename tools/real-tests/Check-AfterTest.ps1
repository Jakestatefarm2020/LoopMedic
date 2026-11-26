# Run as admin after each real test's restart: what happened before the power-off, and is the normal
# watchdog back. -Cleanup (after the last test) also deletes the test folders.
param([switch]$Cleanup)
$real = 'C:\ProgramData\LoopMedic'
Unregister-ScheduledTask -TaskName 'LoopMedic-RealTest' -Confirm:$false -ErrorAction SilentlyContinue
"== normal watchdog task: $((Get-ScheduledTask -TaskName LoopMedic).State)"
foreach ($root in @($real, 'C:\ProgramData\LoopMedic-realtest')) {
    $log = Join-Path $root 'loopmedic.log'
    if (-not (Test-Path $log)) { continue }
    "== $log (last 14 lines)"
    Get-Content $log -Tail 14
    $lp = Join-Path $root 'last-poweroff.json'
    if (Test-Path $lp) { "== $lp"; Get-Content $lp }
}
"== Event Viewer (LoopMedic, last 2 hours)"
Get-WinEvent -FilterHashtable @{ LogName = 'Application'; ProviderName = 'LoopMedic'; StartTime = (Get-Date).AddHours(-2) } -ErrorAction SilentlyContinue |
    Sort-Object TimeCreated | ForEach-Object { "{0:HH:mm:ss}  {1}  {2}" -f $_.TimeCreated, $_.Id, ($_.Message -replace '\s+', ' ') }
"== Windows: last shutdown events (41 = unexpected/forced, 1074 = requested)"
Get-WinEvent -FilterHashtable @{ LogName = 'System'; Id = 41, 1074, 6008; StartTime = (Get-Date).AddHours(-2) } -ErrorAction SilentlyContinue |
    Sort-Object TimeCreated | ForEach-Object { "{0:HH:mm:ss}  {1}  {2}" -f $_.TimeCreated, $_.Id, ($_.Message -split "`n")[0] }
if ($Cleanup) {
    Remove-Item 'C:\ProgramData\LoopMedic-realtest', 'C:\ProgramData\LoopMedic-rehearse' -Recurse -Force -ErrorAction SilentlyContinue
    "== test folders removed"
}
