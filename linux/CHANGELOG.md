# Changelog

LoopMedic for Linux. The Windows version (../windows) keeps the same version numbers and rules.

## 2.2.0 — 2026-11-25

**Renamed: PumpGuard is now LoopMedic.** Installing 2.2 removes an old PumpGuard install (task or service, tray, event source) and keeps its log, settings and self-test records.

**Trips**
- The pump trip fires only when the pump has **stopped** (≤ `PumpStoppedRpm`, 150 by default).
  A pump that is merely slow (below `PumpMinRpm`, 500) raises a warning (DEGRADED, event 1040) and
  never powers off by itself. Tach readings are estimates (pulses-per-turn varies by pump), so a slow
  reading isn't trusted enough to cut power. The CPU temperature trip covers a pump that is really failing.
- **Jump rule:** a one-reading upward jump (more than 1.4× and more than 100 rpm) is held until the
  next reading confirms it. Drops always count at once. Every jump is still logged.
- **The CPU temperature trip can no longer be switched off.** `CpuMaxTempC` ≤ 0 or > 110 uses
  `cpu-limits.json` (a table of common AMD/Intel chips), and unknown CPUs fall back to 95 °C. A CPU swap
  switches to the table's value and shows DEGRADED until setup is re-run.
- **Hotter = faster:** if the CPU is already over its limit when the pump stops, the countdown is
  `HotShutdownDelaySeconds` (5). If the CPU gets that hot during a countdown (the limit + 5 °C for a CPU trip),
  the PC powers off after 5 s.

**Robustness**
- The watchdog is now a compiled exe (`LoopMedic.exe`, .NET Framework 4, no install needed) in place of the
  PowerShell scripts. A supervisor process restarts a crashed worker within 2 s and a frozen one within
  30 s. The scheduled task (restart after 1 min plus a 5-minute "is it running?" trigger) is the second line.
- Start-up self-check: the CPU temperature is plausible (10–100 °C) and the power-off privilege can be
  enabled.
- Monthly self-test: the power-off helper runs in dry-run mode and proves the privilege path. A failure
  shows DEGRADED with a notification and is retried daily.
- `last-poweroff.json` is written (write-through) just before a forced power-off. The next start logs it
  and raises event 1050, and the tray shows "LoopMedic powered off this PC" once.
- Event Viewer: Application log, source `LoopMedic`, IDs 1000–1050 (see README).
- Memory use dropped from about 82 MB to about 53 MB (watchdog and tray together).

**Setup**
- CPU limit suggested from `cpu-limits.json`, with a picker for the CPU family.
- The likely pump header is pre-selected. Setup warns when no header spins at pump speeds (a USB-only AIO
  → temperature-only protection).
- The safe test runs the exe in a temporary folder.

**Tray**
- The pump graph keeps headroom of 10% of the speed above and below the line, so the sensor's normal
  one-reading wiggles (a few %) show as small ticks instead of filling the graph. Every reading is still shown.

**Docs and tests**
- README: no-warranty notice, pump-reading accuracy and cable-routing warning, layers of protection and
  BIOS settings, Event Viewer IDs, "why did my PC turn off?".
- `tests/run-tests.py`: 24 scenarios against fake sensors (no hardware, nothing powers off).
- License: PolyForm Noncommercial 1.0.0.

## 2.1.1 — 2026-11-24
- Tray: one failed status read no longer flashes "protection off" then "on". It keeps the last good status
  and only notifies once a change has lasted 5 s.
- Tray "Live" indicator: Live ≤ 10 s, Waiting 10–20 s, Offline > 20 s.
- Log: "Pump jump" lines (> 15 % change between checks) and "Stalled" lines (no check for ≥ 10 s), each capped
  at 20 per hour.

## 2.1 — 2026-11-24
- DEGRADED state, cool-CPU grace period (60 s while the CPU is below 60 °C), tray app with panel and pop-up,
  pause capped at 2 hours, monotonic clock for all durations.
