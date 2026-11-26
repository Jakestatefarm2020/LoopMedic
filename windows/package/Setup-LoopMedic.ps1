<#
LoopMedic by Jacob Stiltner, (c) 2026 - guided setup. Start it with Install.bat (that asks for admin).
Steps: unblock files + build (build.ps1) -> check PawnIO driver -> pick the pump sensor ->
choose the CPU temperature limit -> safe test (no shutdown) -> install.
#>
$ErrorActionPreference = 'Stop'
$here = $PSScriptRoot

function Show-Step([string]$t) { Write-Host ''; Write-Host "=== $t ===" -ForegroundColor Cyan }
function Exit-Setup([int]$code) { Write-Host ''; Read-Host 'Press Enter to close' | Out-Null; exit $code }
function Read-YesNo([string]$q) {
    while ($true) {
        $a = (Read-Host "$q [Y/N]").Trim().ToUpper()
        if ($a -eq 'Y') { return $true }
        if ($a -eq 'N') { return $false }
    }
}
function Get-PawnIO { Get-ItemProperty 'HKLM:\SOFTWARE\Microsoft\Windows\CurrentVersion\Uninstall\PawnIO' -ErrorAction SilentlyContinue }

try {
    $admin = ([Security.Principal.WindowsPrincipal][Security.Principal.WindowsIdentity]::GetCurrent()).IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)
    if (-not $admin) { Write-Host 'Please start setup with Install.bat (it asks for admin).' -ForegroundColor Red; Exit-Setup 1 }

    Write-Host 'LoopMedic setup: shuts the PC down if the AIO pump stops or the CPU overheats.' -ForegroundColor White
    Write-Host 'by Jacob Stiltner' -ForegroundColor DarkGray

    # ---------------------------------------------------------------- 1
    Show-Step '1/5  Preparing files'
    Get-ChildItem $here -Recurse -File | Unblock-File
    Write-Host 'Files unblocked (Windows marks downloaded files as untrusted).'
    # Built here, from the source in src\ and tray\, with the C# compiler that's part of Windows:
    # no ready-made .exe is downloaded, and anyone can read exactly what runs.
    Write-Host 'Building LoopMedic from its source code (uses the compiler built into Windows)...'
    & (Join-Path $here 'build.ps1') | Out-Null
    if (-not (Test-Path (Join-Path $here 'bin\LoopMedic.exe')) -or -not (Test-Path (Join-Path $here 'bin\LoopMedicTray.exe'))) {
        Write-Host 'The build failed - see the messages above. Send them to whoever gave you this.' -ForegroundColor Red
        Exit-Setup 1
    }
    Write-Host 'Built bin\LoopMedic.exe and bin\LoopMedicTray.exe.' -ForegroundColor Green

    # ---------------------------------------------------------------- 2
    Show-Step '2/5  Sensor driver (PawnIO)'
    $pawn = Get-PawnIO
    if ($pawn) {
        Write-Host "PawnIO $($pawn.DisplayVersion) is installed." -ForegroundColor Green
    } else {
        Write-Host 'PawnIO is not installed. LoopMedic needs it to read fan speeds and the CPU temperature.'
        Write-Host 'It is a small, signed, open-source driver (https://pawnio.eu), the same one'
        Write-Host 'LibreHardwareMonitor and FanControl use. Its official installer is included.'
        if (Read-YesNo 'Install PawnIO now?') {
            # 1) the bundled official installer, only if its signature is valid and from namazso
            $bundled = Join-Path $here 'pawnio\PawnIO_setup.exe'
            if (Test-Path $bundled) {
                $sig = Get-AuthenticodeSignature $bundled
                if ($sig.Status -eq 'Valid' -and $sig.SignerCertificate.Subject -match 'CN=namazso\.eu') {
                    Write-Host 'Running the included PawnIO installer (signature verified)...'
                    $p = Start-Process -FilePath $bundled -ArgumentList '-install', '-silent' -Wait -PassThru
                    Write-Host "Installer finished (code $($p.ExitCode))."
                    if ($p.ExitCode -eq 3010) {
                        Write-Host 'PawnIO is installed but Windows wants a restart. Restart the PC, then run Install.bat again.' -ForegroundColor Yellow
                        Exit-Setup 0
                    }
                    $pawn = Get-PawnIO
                } else {
                    Write-Host "The included installer failed its signature check ($($sig.Status)); not running it." -ForegroundColor Yellow
                }
            }
            # 2) fall back to winget (downloads the same official installer)
            if (-not $pawn -and (Get-Command winget -ErrorAction SilentlyContinue)) {
                Write-Host 'Trying winget instead...'
                & winget install --id namazso.PawnIO --exact --accept-source-agreements --accept-package-agreements
                $pawn = Get-PawnIO
            }
        }
        if (-not $pawn) {
            Write-Host 'Install PawnIO from https://pawnio.eu, then run Install.bat again.' -ForegroundColor Yellow
            Exit-Setup 1
        }
        Write-Host "PawnIO $($pawn.DisplayVersion) installed." -ForegroundColor Green
    }

    # ---------------------------------------------------------------- 3
    Show-Step '3/5  Finding your pump'
    Add-Type -Path (Join-Path $here 'lhm\LibreHardwareMonitorLib.dll')
    $c = New-Object LibreHardwareMonitor.Hardware.Computer
    $c.IsCpuEnabled = $true
    $c.IsMotherboardEnabled = $true
    $c.IsControllerEnabled = $false   # leave USB coolers to their own app
    $c.Open()
    function Get-All {
        $l = @()
        foreach ($h in $c.Hardware) { $h.Update(); $l += $h.Sensors; foreach ($s in $h.SubHardware) { $s.Update(); $l += $s.Sensors } }
        return $l
    }
    $null = Get-All
    Start-Sleep -Seconds 2
    $all = Get-All

    $board = $c.Hardware | Where-Object { $_.HardwareType.ToString() -eq 'Motherboard' } | Select-Object -First 1
    $cpuHw = $c.Hardware | Where-Object { $_.HardwareType.ToString() -eq 'Cpu' } | Select-Object -First 1
    Write-Host "Motherboard: $($board.Name)"
    Write-Host "CPU:         $($cpuHw.Name)"
    Write-Host ''

    $fans = @($all | Where-Object { $_.SensorType.ToString() -eq 'Fan' -and $_.Hardware.HardwareType.ToString() -ne 'Cpu' })
    $temps = @($all | Where-Object { $_.SensorType.ToString() -eq 'Temperature' -and $_.Hardware.HardwareType.ToString() -eq 'Cpu' })
    $pump = $null
    $cpuTemp = $null
    $maxTemp = -1

    # Already installed? Offer to keep those settings if they still match this PC.
    $keep = $false
    # PumpGuard = LoopMedic's old name: an upgrade from it can keep its settings too
    $oldPath = @('C:\ProgramData\LoopMedic\config.json', 'C:\ProgramData\PumpGuard\config.json') | Where-Object { Test-Path $_ } | Select-Object -First 1
    if (-not $oldPath) { $oldPath = 'C:\ProgramData\LoopMedic\config.json' }
    if (Test-Path $oldPath) {
        try {
            $old = Get-Content $oldPath -Raw | ConvertFrom-Json
            $oldPump = if ($old.PumpSensorId) { $fans | Where-Object { $_.Identifier.ToString() -eq $old.PumpSensorId } | Select-Object -First 1 } else { $null }
            $oldTemp = if ($old.CpuTempSensorId) { $temps | Where-Object { $_.Identifier.ToString() -eq $old.CpuTempSensorId } | Select-Object -First 1 } else { $null }
            $pumpOk = (-not $old.PumpSensorId) -or ($oldPump -and [double]$oldPump.Value -ge 500)
            $cpuOk = (-not $old.CpuName) -or ($old.CpuName -eq $cpuHw.Name)
            if ($pumpOk -and $cpuOk -and ($oldPump -or [double]$old.CpuMaxTempC -gt 0)) {
                Write-Host 'LoopMedic is already installed with these settings:' -ForegroundColor White
                Write-Host ("  Pump: {0}" -f $(if ($oldPump) { "$($oldPump.Name) (reading {0:N0} rpm now)" -f $oldPump.Value } else { 'none (temperature only)' }))
                Write-Host ("  CPU limit: {0}" -f $(if ([double]$old.CpuMaxTempC -gt 0) { "$($old.CpuMaxTempC) C" } else { 'off' }))
                if (Read-YesNo 'Keep these settings?') {
                    $keep = $true; $pump = $oldPump; $cpuTemp = $oldTemp; $maxTemp = [double]$old.CpuMaxTempC
                    if (-not $cpuTemp) { $maxTemp = 0 }
                }
            } elseif (-not $cpuOk) {
                Write-Host "Your CPU changed ($($old.CpuName) -> $($cpuHw.Name)), so let's pick the settings again." -ForegroundColor Yellow
            } else {
                Write-Host 'Your saved pump sensor is missing or not spinning, so let''s pick it again.' -ForegroundColor Yellow
            }
        } catch { }
    }

    if ($keep) {
        # settings carried over
    } elseif ($fans.Count -eq 0) {
        Write-Host 'No motherboard fan sensors were found, so only the temperature check can be used.' -ForegroundColor Yellow
    } else {
        # best guess: a header named like a pump, else the only one spinning at pump speeds
        $guess = -1
        $named = @(for ($i = 0; $i -lt $fans.Count; $i++) { if ($fans[$i].Name -match 'pump|aio' -and [double]$fans[$i].Value -ge 500) { $i } })
        $inRange = @(for ($i = 0; $i -lt $fans.Count; $i++) { $v = [double]$fans[$i].Value; if ($v -ge 1500 -and $v -le 4500) { $i } })
        if ($named.Count -eq 1) { $guess = $named[0] } elseif ($inRange.Count -eq 1) { $guess = $inRange[0] }
        if (-not ($fans | Where-Object { [double]$_.Value -ge 1000 -and [double]$_.Value -le 5000 })) {
            Write-Host 'None of the motherboard headers is spinning at pump speeds (1,000-5,000 rpm).' -ForegroundColor Yellow
            Write-Host 'Some AIO coolers only report the pump speed over USB to their own app, with no speed wire'
            Write-Host 'to the motherboard. If yours is one of them, choose 0: LoopMedic then protects with the'
            Write-Host 'CPU temperature only (still a real safety net, just a slower one).'
            Write-Host ''
        }
        Write-Host 'Fan sensors on your motherboard right now:'
        for ($i = 0; $i -lt $fans.Count; $i++) {
            $line = '  {0,2}. {1,-24} {2,7:N0} rpm' -f ($i + 1), $fans[$i].Name, $fans[$i].Value
            if ($i -eq $guess) { Write-Host $line -NoNewline; Write-Host '   <- looks like the pump' -ForegroundColor Green } else { Write-Host $line }
        }
        Write-Host '   0. My pump is not on a motherboard header (temperature-only protection)'
        Write-Host ''
        Write-Host 'How to spot the pump:' -ForegroundColor White
        Write-Host ' - Most AIOs have a small 3-pin pump-speed cable plugged into a header labelled'
        Write-Host '   PUMP, AIO_PUMP, W_PUMP or CPU_FAN on the motherboard.'
        Write-Host ' - Pumps usually spin at 1,500-4,000 rpm. Entries reading 0 are empty headers.'
        Write-Host ' - Not sure? Change the pump speed in your cooler app, run Install.bat again,'
        Write-Host '   and see which number moved (or use Watch-Pump.bat).'
        while ($true) {
            $n = -1
            $prompt = if ($guess -ge 0) { "Which number is your pump? (Enter = $($guess + 1))" } else { 'Which number is your pump?' }
            $ans = Read-Host $prompt
            if ([string]::IsNullOrWhiteSpace($ans) -and $guess -ge 0) { $ans = [string]($guess + 1) }
            [void][int]::TryParse($ans, [ref]$n)
            if ($n -lt 0 -or $n -gt $fans.Count) { continue }
            if ($n -eq 0) { break }
            $cand = $fans[$n - 1]
            if ([double]$cand.Value -lt 500) {
                Write-Host 'That sensor reads under 500 rpm right now - that is not a running pump. Pick the one your pump is on (or 0).' -ForegroundColor Yellow
                continue
            }
            $pump = $cand
            if ([double]$cand.Value -lt 1000) { Write-Host 'Note: that is slow for a pump. Double-check it is not a case fan.' -ForegroundColor Yellow }
            break
        }
    }

    # ---------------------------------------------------------------- 4
    Show-Step '4/5  CPU temperature limit'
    # The same table the watchdog uses (cpu-limits.json): trip = max temp - 5 C, or the max itself held
    # longer for chips designed to sit at their limit. The temperature check can't be switched off.
    $limits = Get-Content (Join-Path $here 'cpu-limits.json') -Raw | ConvertFrom-Json
    function Get-ChipAdvice($e) {
        $tj = [double]$e.tjmax
        if ($e.runsAtLimit) {
            $hs = if ($e.hotSeconds) { [double]$e.hotSeconds } else { 30 }
            return @{ Trip = $tj; Hot = $hs; Why = "$($e.family): max $tj C. These CPUs are DESIGNED to sit at $tj C under heavy load, so the trip is the limit itself held for $hs s. The pump check is the main protection on this chip." }
        }
        return @{ Trip = $tj - 5; Hot = 30; Why = "$($e.family): max $tj C. Normal heavy load stays below that, so $($tj - 5) C held for 30 s means cooling has failed." }
    }
    $hotSeconds = 0
    if (-not $cpuTemp) {
        $cpuTemp = $temps | Where-Object { $_.Name -match 'Tctl|Tdie' } | Select-Object -First 1
        if (-not $cpuTemp) { $cpuTemp = $temps | Where-Object { $_.Name -match 'Package' } | Select-Object -First 1 }
        if (-not $cpuTemp) { $cpuTemp = $temps | Where-Object { $_.Name -match 'Core Max' } | Select-Object -First 1 }
    }
    if (-not $cpuTemp) {
        Write-Host 'No CPU temperature sensor found - the temperature backup can''t work on this PC.' -ForegroundColor Yellow
        $maxTemp = 0
    } elseif ($keep -and $maxTemp -gt 0) {
        Write-Host "Keeping the CPU limit: $maxTemp C"
    } else {
        $match = $limits.chips | Where-Object { $cpuHw.Name -match $_.match } | Select-Object -First 1
        if ($match) { $adv = Get-ChipAdvice $match }
        else { $adv = @{ Trip = [double]$limits.fallbackTripC; Hot = 30; Why = "This CPU isn't in LoopMedic's table, so it uses the safe default of $($limits.fallbackTripC) C. If you know your CPU's max temperature (Tjmax), pick its family from the list or type a limit about 5 C below it." } }
        Write-Host ("CPU temperature now: {0:N1} C  ({1})" -f $cpuTemp.Value, $cpuTemp.Name)
        Write-Host "Suggested: power off if the CPU stays at $($adv.Trip) C or more for $($adv.Hot) s." -ForegroundColor White
        Write-Host $adv.Why
        Write-Host 'The temperature check can''t be turned off - it''s the backup if the pump reading ever fails.' -ForegroundColor DarkGray
        $maxTemp = -1
        while ($maxTemp -lt 0) {
            $a = Read-Host "Enter = $($adv.Trip) C, a number (60-105), or L to pick your CPU family from a list"
            if ([string]::IsNullOrWhiteSpace($a)) { $maxTemp = $adv.Trip; $hotSeconds = $adv.Hot; break }
            if ($a.Trim() -eq 'l') {
                $chips = @($limits.chips)
                for ($i = 0; $i -lt $chips.Count; $i++) {
                    $x = Get-ChipAdvice $chips[$i]
                    Write-Host ('  {0,2}. {1,-28} max {2,3} C  -> trip {3} C for {4} s' -f ($i + 1), $chips[$i].family, $chips[$i].tjmax, $x.Trip, $x.Hot)
                }
                $p = 0
                if ([int]::TryParse((Read-Host 'Number (Enter = back)'), [ref]$p) -and $p -ge 1 -and $p -le $chips.Count) {
                    $x = Get-ChipAdvice $chips[$p - 1]
                    Write-Host $x.Why
                    $maxTemp = $x.Trip; $hotSeconds = $x.Hot
                }
                continue
            }
            $v = -1; if ([int]::TryParse($a, [ref]$v) -and $v -ge 60 -and $v -le 105) { $maxTemp = $v; $hotSeconds = $adv.Hot }
        }
    }
    if (-not $pump -and -not $cpuTemp) {
        Write-Host 'No pump sensor and no CPU temperature sensor: there is nothing to watch. Setup stopped.' -ForegroundColor Red
        $c.Close(); Exit-Setup 1
    }
    $c.Close()

    $cfgPath = Join-Path $here 'config.json'
    $cfg = Get-Content $cfgPath -Raw | ConvertFrom-Json
    $cfg.PumpSensorId = if ($pump) { $pump.Identifier.ToString() } else { '' }
    $cfg.CpuTempSensorId = if ($cpuTemp) { $cpuTemp.Identifier.ToString() } else { '' }
    $cfg.CpuMaxTempC = $maxTemp
    if ($hotSeconds -gt 0) { $cfg | Add-Member -NotePropertyName CpuHotSeconds -NotePropertyValue $hotSeconds -Force }
    # remembered so LoopMedic notices a CPU swap (a new CPU needs its own limit)
    $cfg | Add-Member -NotePropertyName CpuName -NotePropertyValue ([string]$cpuHw.Name) -Force
    $cfg | ConvertTo-Json | Set-Content $cfgPath -Encoding UTF8
    Write-Host ''
    Write-Host "Saved: pump = $(if ($pump) { "$($pump.Name) [$($cfg.PumpSensorId)]" } else { 'none (temperature only)' }), CPU limit = $(if ($maxTemp -gt 0) { "$maxTemp C" } else { 'none' })" -ForegroundColor Green

    # ---------------------------------------------------------------- 5
    Show-Step '5/5  Safe test, then install'
    # The test runs in its own temp folder, so nothing is left behind here.
    $guard = Join-Path $here 'bin\LoopMedic.exe'
    $testRoot = Join-Path ([IO.Path]::GetTempPath()) ('LoopMedic-setup-test-' + [guid]::NewGuid().ToString('N').Substring(0, 8))
    New-Item -ItemType Directory -Force $testRoot | Out-Null
    Copy-Item $cfgPath, (Join-Path $here 'cpu-limits.json') $testRoot
    try {
        if ($pump) {
            Write-Host 'Pretending the pump spins, then stops (nothing will power off). This takes about 70 seconds...'
            $out = & $guard --worker --root $testRoot --dry-run --no-eventlog --simulate-pump 0 --simulate-healthy 22 --duration 70 2>&1 | ForEach-Object { "$_" }
            $out | ForEach-Object { Write-Host "  $_" }
            Start-Sleep -Seconds 2    # the power-off helper writes its last line a moment later
            $testLog = Get-Content (Join-Path $testRoot 'loopmedic.log') -Raw -ErrorAction SilentlyContinue
            if ($testLog -match 'DRY RUN - trip' -and $testLog -match 'WOULD POWER OFF NOW') {
                Write-Host 'TEST PASSED: LoopMedic reacted to a dead pump.' -ForegroundColor Green
            } else {
                Write-Host 'TEST FAILED: LoopMedic did not react. Not installing. Send the lines above to whoever gave you this.' -ForegroundColor Red
                Exit-Setup 1
            }
        } else {
            $out = & $guard --worker --root $testRoot --once --no-eventlog 2>&1 | ForEach-Object { "$_" }
            $out | ForEach-Object { Write-Host "  $_" }
        }
    } finally {
        Remove-Item $testRoot -Recurse -Force -ErrorAction SilentlyContinue
    }

    Write-Host ''
    if (Read-YesNo 'Install LoopMedic (and its tray icon) so it runs at every startup?') {
        & (Join-Path $here 'Install-LoopMedic.ps1')
        Write-Host ''
        Write-Host 'Done. LoopMedic is running.' -ForegroundColor Green
        Write-Host 'Look for the green shield in the system tray (it may be under the ^ arrow).'
        Write-Host 'Click it for live pump / CPU / fan readings; right-click > Run test to see the alarm pop-up.'
        Write-Host 'Log: C:\ProgramData\LoopMedic\loopmedic.log'
    } else {
        Write-Host 'Not installed. Run Install.bat again whenever you are ready.'
    }
    Exit-Setup 0
} catch {
    Write-Host ''
    Write-Host "ERROR: $($_.Exception.Message)" -ForegroundColor Red
    Write-Host $_.ScriptStackTrace
    Exit-Setup 1
}
