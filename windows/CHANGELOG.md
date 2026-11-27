# Changelog

LoopMedic for Windows. The Linux port (../linux) keeps the same version numbers and rules.

## 2.3.1 — 2026-11-27

- **Two new themes.** Tray right-click > Themes: **Neon Dark** (hot magenta pump, neon cyan CPU) and
  **Neon Light** (magenta pump, electric violet CPU, neon orange for the other fans, on white).
- **Atomic** now uses yellow for the pump and CPU readings, both graphs and the pump bar; the other fans are
  grey, so the pump stands out. The "Update now" button follows the theme.
- The README now says plainly that LoopMedic works with AIO / water coolers and air coolers.

## 2.3.0 — 2026-11-27

- **Air coolers.** Setup asks what cools your CPU. With an air cooler, the CPU fan takes the pump's place:
  same rules, "CPU fan" instead of "pump" everywhere. A fan that stops at idle (zero RPM) is refused with advice.
- **Email alerts (optional).** Through your own SMTP server: powering off (as the countdown starts), back on
  after a power-off, a cancelled power-off, a warning starting or clearing, a failed self-test. Set up in
  setup (step 5/6) or later in `C:\ProgramData\LoopMedic\mail.json`; changes apply within seconds, no restart. The password is
  encrypted on the PC (Windows DPAPI) and the file is admin-only. Sending runs in the background, so a dead mail server never delays a power-off.
  Port 587 (STARTTLS) or 25; 465 isn't supported. Tray: **Send a test email**.
- **Early warning - EXPERIMENTAL, off unless you turn it on in setup.** Learns the pump's normal speed over
  5 days (per CPU-load band), then warns once (a Windows notification, a log line, an email if set up) if it runs 12% slower
  over 3 days. Never a power-off. Only briefly tested so far. Tray: **Relearn normal pump speed**.
- **Themes.** Tray right-click > Themes: Stealth (default), Atomic, Dark Side, Ice. Status colours (green
  ARMED / Live, orange warnings, red alarms) are the same in every theme.
- **Quieter log.** One-reading pump glitches are counted in the hourly line instead of one line each.

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
- The watchdog recreates a missing `control\` folder (Users: modify) at start, so the tray can always check in.
- Sleep no longer looks like a hang: the supervisor notices the PC was asleep and gives the watchdog a
  fresh 30 s instead of restarting it (found in the real sleep/resume test).
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
- **Updates:** click the version number at the top of the panel to check GitHub for a newer release. If there is one,
  a window shows what's new; **Update now** downloads it, checks its RSA signature against the release key built into
  the tray (a changed download is refused), and installs it with your settings kept. The new tray says "updated" once.
  Nothing checks in the background.
- Notifications are real Windows notifications now: they stay in the notification center, and the
  "LoopMedic powered off this PC" one stays on screen until dismissed (falls back to a tray balloon).
- The trip alarm opens near the top of the screen, so Windows' own "You're about to be signed out" box
  (the backup shutdown, always in the middle) doesn't cover it.
- The power-off notice and the amber "last power-off" line clear after 24 hours (was 7 days).
- Each power-off is announced once, even with a second (test) tray running.
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
