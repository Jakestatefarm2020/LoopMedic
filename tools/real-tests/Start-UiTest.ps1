# LoopMedic pop-up / notice test - NOTHING powers off. Run as admin.
# Shows, in a throwaway folder with a second tray icon:
#   1. the "LoopMedic powered off this PC" notice (from a fake last-poweroff record)
#   2. ~30 s later the trip alarm (dry run), next to Windows' own "You're about to be signed out" box
#      from a real `shutdown /s /t 600` that this script cancels by itself after 75 s
#   3. the test folder starts WITHOUT control\ - the watchdog must recreate it (no plain msg.exe box)
$ErrorActionPreference = 'Stop'
$real = 'C:\ProgramData\LoopMedic'
$exe = Join-Path $real 'LoopMedic.exe'
$root = 'C:\ProgramData\LoopMedic-uitest'

Get-CimInstance Win32_Process -Filter "Name='LoopMedic.exe' or Name='LoopMedicTray.exe'" |
    Where-Object { $_.CommandLine -match 'LoopMedic-uitest' } | ForEach-Object { Stop-Process -Id $_.ProcessId -Force -ErrorAction SilentlyContinue }
Start-Sleep 1
Remove-Item $root -Recurse -Force -ErrorAction SilentlyContinue
New-Item -ItemType Directory $root | Out-Null
Copy-Item (Join-Path $real 'config.json'), (Join-Path $real 'cpu-limits.json') $root
if (Test-Path (Join-Path $real 'selftest.json')) { Copy-Item (Join-Path $real 'selftest.json') $root }
$at = (Get-Date).AddMinutes(-3).ToString('o')
'{"at":"' + $at + '","reason":"UI TEST - pump at 0 rpm for 10s (nothing really powered off)","kind":"pump","reported":false}' |
    Set-Content (Join-Path $root 'last-poweroff.json') -Encoding ascii

Start-Process $exe -ArgumentList "--worker --root `"$root`" --dry-run --no-eventlog --simulate-pump 0 --simulate-healthy 20 --duration 120" -WindowStyle Hidden
Start-Process (Join-Path $real 'LoopMedicTray.exe') -ArgumentList "--dir `"$root`""
Start-Sleep 28
& shutdown.exe /s /t 600 /c "LoopMedic UI TEST - this is the backup-shutdown box. It cancels itself in about a minute."
Start-Job { Start-Sleep 75; & shutdown.exe /a } | Out-Null
"UI test running: notice now, alarm in ~2 s, Windows box now; the Windows shutdown cancels itself in 75 s."
Start-Sleep 77
& shutdown.exe /a 2>$null   # belt and braces: no shutdown left pending
"after: control\ exists = $(Test-Path (Join-Path $root 'control'))"
Get-Content (Join-Path $root 'loopmedic.log')
