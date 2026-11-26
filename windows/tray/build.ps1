# Builds LoopMedicTray.exe with the C# compiler built into Windows (.NET Framework 4.x).
$csc = 'C:\Windows\Microsoft.NET\Framework64\v4.0.30319\csc.exe'
$src = Join-Path $PSScriptRoot 'LoopMedicTray.cs'
$ico = Join-Path $PSScriptRoot 'LoopMedic.ico'
$out = Join-Path $PSScriptRoot 'LoopMedicTray.exe'
$args = @('/nologo', '/target:winexe', '/optimize+', "/out:$out",
          '/r:System.dll', '/r:System.Core.dll', '/r:System.Drawing.dll', '/r:System.Windows.Forms.dll', '/r:System.Web.Extensions.dll')
if (Test-Path $ico) { $args += "/win32icon:$ico" }
& $csc @args $src
if ($LASTEXITCODE -eq 0) { Get-Item $out | Format-Table Name, Length, LastWriteTime }
exit $LASTEXITCODE
