# Builds LoopMedic.exe (watchdog) and LoopMedicTray.exe (tray app) with the C# compiler that ships
# inside Windows (.NET Framework 4.x) - no Visual Studio or SDK needed.
#   build.ps1                 -> bin\LoopMedic.exe, bin\LoopMedicTray.exe
#   build.ps1 -LhmDir <dir>   where LibreHardwareMonitorLib.dll is (default: .\lhm, then the dev tools copy)
param([string]$LhmDir = '', [string]$OutDir = '')
$ErrorActionPreference = 'Stop'
$here = $PSScriptRoot
if (-not $OutDir) { $OutDir = Join-Path $here 'bin' }
$csc = Join-Path $env:WINDIR 'Microsoft.NET\Framework64\v4.0.30319\csc.exe'
if (-not (Test-Path $csc)) { $csc = Join-Path $env:WINDIR 'Microsoft.NET\Framework\v4.0.30319\csc.exe' }
if (-not (Test-Path $csc)) { throw "The .NET Framework 4 C# compiler isn't on this PC ($csc)." }
if (-not $LhmDir) {
    $LhmDir = @((Join-Path $here 'lhm'), (Join-Path $here '..\tools\LibreHardwareMonitor-0.9.6')) |
        Where-Object { Test-Path (Join-Path $_ 'LibreHardwareMonitorLib.dll') } | Select-Object -First 1
}
if (-not $LhmDir) { throw 'LibreHardwareMonitorLib.dll not found (pass -LhmDir).' }
New-Item -ItemType Directory -Force $OutDir | Out-Null
$fw = Split-Path $csc
$common = @('/nologo', '/optimize+', '/r:System.dll', '/r:System.Core.dll', '/r:System.Web.Extensions.dll', '/r:System.Security.dll')

$out1 = Join-Path $OutDir 'LoopMedic.exe'
& $csc @common '/target:exe' "/out:$out1" "/r:$(Join-Path $LhmDir 'LibreHardwareMonitorLib.dll')" "/r:$(Join-Path $fw 'netstandard.dll')" (Join-Path $here 'src\LoopMedic.cs')
if ($LASTEXITCODE -ne 0) { throw 'LoopMedic.exe failed to build.' }
# Lets LoopMedic.exe find the sensor library in its lhm\ subfolder.
@'
<?xml version="1.0" encoding="utf-8"?>
<configuration>
  <startup><supportedRuntime version="v4.0" sku=".NETFramework,Version=v4.7.2" /></startup>
  <runtime>
    <assemblyBinding xmlns="urn:schemas-microsoft-com:asm.v1"><probing privatePath="lhm" /></assemblyBinding>
  </runtime>
</configuration>
'@ | Set-Content -Encoding UTF8 "$out1.config"
$binLhm = Join-Path $OutDir 'lhm'
if ((Resolve-Path $LhmDir).Path -ne (Resolve-Path -LiteralPath $binLhm -ErrorAction SilentlyContinue).Path) {
    New-Item -ItemType Directory -Force $binLhm | Out-Null
    Copy-Item (Join-Path $LhmDir '*.dll') $binLhm -Force
}

$out2 = Join-Path $OutDir 'LoopMedicTray.exe'
$trayArgs = @('/target:winexe', "/out:$out2", '/r:System.Drawing.dll', '/r:System.Windows.Forms.dll')
# Windows toast notifications (WinRT): the metadata ships with Windows 10/11.
$wm = Join-Path $env:WINDIR 'System32\WinMetadata'
$trayArgs += @("/r:$(Join-Path $wm 'Windows.UI.winmd')", "/r:$(Join-Path $wm 'Windows.Data.winmd')", "/r:$(Join-Path $wm 'Windows.Foundation.winmd')",
               "/r:$(Join-Path $fw 'System.Runtime.dll')", "/r:$(Join-Path $fw 'System.Runtime.WindowsRuntime.dll')",
               '/r:System.IO.Compression.dll', '/r:System.IO.Compression.FileSystem.dll')   # update download: unzip
$ico = Join-Path $here 'tray\LoopMedic.ico'
if (Test-Path $ico) { $trayArgs += "/win32icon:$ico" }
& $csc @common @trayArgs (Join-Path $here 'tray\LoopMedicTray.cs')
if ($LASTEXITCODE -ne 0) { throw 'LoopMedicTray.exe failed to build.' }
Get-Item $out1, $out2 | Format-Table Name, Length, LastWriteTime
