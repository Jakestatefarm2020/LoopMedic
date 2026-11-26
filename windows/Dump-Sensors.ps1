# Run as admin. Writes every CPU / motherboard fan and temperature sensor to sensors.txt
# so the pump's header can be picked for config.json. Read-only; changes nothing.
param([string]$Lhm = (Join-Path $PSScriptRoot 'lhm'))   # folder with LibreHardwareMonitorLib.dll
$out = Join-Path $PSScriptRoot 'sensors.txt'
Add-Type -Path (Join-Path $Lhm 'LibreHardwareMonitorLib.dll')
$c = New-Object LibreHardwareMonitor.Hardware.Computer
$c.IsCpuEnabled = $true
$c.IsMotherboardEnabled = $true
$c.IsControllerEnabled = $false   # leave AIO coolers' USB links to their own app
$c.Open()
$lines = New-Object System.Collections.Generic.List[string]
$elev = ([Security.Principal.WindowsPrincipal][Security.Principal.WindowsIdentity]::GetCurrent()).IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)
$lines.Add("elevated=$elev  $(Get-Date -Format s)")
function Walk($hw, $depth) {
    $hw.Update()
    $lines.Add(('  ' * $depth) + "[$($hw.HardwareType)] $($hw.Name)")
    foreach ($s in $hw.Sensors) {
        if ('Fan', 'Temperature', 'Control' -contains $s.SensorType.ToString()) {
            $lines.Add(('  ' * $depth) + "    $($s.SensorType) | $($s.Name) | $($s.Value) | $($s.Identifier)")
        }
    }
    foreach ($sub in $hw.SubHardware) { Walk $sub ($depth + 1) }
}
foreach ($h in $c.Hardware) { Walk $h 0 }
$c.Close()
$lines | Set-Content -Path $out -Encoding UTF8
$lines
Write-Host "`nSaved to $out"
