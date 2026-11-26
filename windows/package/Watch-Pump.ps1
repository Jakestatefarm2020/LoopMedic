# LoopMedic by Jacob Stiltner, (c) 2026
# Shows every motherboard fan + the CPU temperature every 2 s for 90 s. Read-only.
# Start it with Watch-Pump.bat. Change the pump speed in your cooler app while it
# runs: the line that moves is your pump.
param([int]$Seconds = 90)
$here = $PSScriptRoot
Get-ChildItem $here -Recurse -File | Unblock-File
Add-Type -Path (Join-Path $here 'lhm\LibreHardwareMonitorLib.dll')
$c = New-Object LibreHardwareMonitor.Hardware.Computer
$c.IsCpuEnabled = $true
$c.IsMotherboardEnabled = $true
$c.IsControllerEnabled = $false
$c.Open()
function Get-All { $l = @(); foreach ($h in $c.Hardware) { $h.Update(); $l += $h.Sensors; foreach ($s in $h.SubHardware) { $s.Update(); $l += $s.Sensors } }; return $l }
$end = (Get-Date).AddSeconds($Seconds)
while ((Get-Date) -lt $end) {
    $all = Get-All
    $fans = $all | Where-Object { $_.SensorType.ToString() -eq 'Fan' -and $_.Hardware.HardwareType.ToString() -ne 'Cpu' }
    $t = $all | Where-Object { $_.SensorType.ToString() -eq 'Temperature' -and $_.Hardware.HardwareType.ToString() -eq 'Cpu' -and $_.Name -match 'Tctl|Tdie|Package' } | Select-Object -First 1
    $parts = $fans | ForEach-Object { '{0}={1:N0}' -f $_.Name.Replace('Fan #', '#'), $_.Value }
    Write-Host ('{0}  CPU {1,5:N1} C   {2}' -f (Get-Date -Format 'HH:mm:ss'), $t.Value, ($parts -join '  '))
    Start-Sleep -Seconds 2
}
$c.Close()
Write-Host 'Finished.'
