# Changelog

LoopMedic for Linux. The Windows version (../windows) keeps the same version numbers and rules.

## 2.3.0 — 2026-11-26

**Air coolers.** Setup asks what cools your CPU. With an air cooler the CPU fan takes the pump's place:
the same rules, with "CPU fan" in place of "pump" everywhere (log, tray, alarm). A fan that stops at idle
(zero RPM / fan stop) is refused with advice, since a stopped fan would look like a failure.

**Early warning for a pump slowing down.** Three times an hour the watchdog saves a health sample: the
median pump speed per CPU-load band (light / medium / heavy), from normal running only. The first 14 days
set the pump's normal speed (a fixed reference). After that, 12% or more slower over the last 3 days gives
one desktop notification (it stays in the notification history), an `EARLY WARNING` log line and an email
if mail is set up. Never a power-off, never DEGRADED, not in the panel. A clearly faster pump means its
curve was changed, so the normal is relearned; tray menu > **Relearn normal pump speed** does it by hand.
Settings: `HealthSampleMinutes` (20), `HealthLearnDays` (14), `HealthRecentDays` (3), `HealthWarnPercent` (12).

**Email alerts (optional).** Your own SMTP server, set up in setup step 5/6 or later in
`/etc/loopmedic/mail.json` (a switched-off template is installed once and never overwritten).
- Emails: powering off (as the countdown starts), back on after a forced power-off, a cancelled power-off,
  a warning starting and clearing (incl. the early warning), the self-test failing.
- Changes are picked up within ~2 s with no restart (inotify, plus a cheap date check once a minute).
  A broken save is logged and the previous settings stay in use.
- A typed password is scrambled at once with a random root-only key; the file is root-only.
- Sent from a background thread with retries: a slow or dead mail server never delays a power-off.
- STARTTLS (587) or none (25); 465 isn't supported. `loopmedic.py --test-mail` and tray
  **Send a test email** check it.

**Themes.** Tray right-click > Themes: Stealth (the old look, still the default), Atomic (blue and yellow),
Dark Side (blue and pink), Ice (white). Status colours are the same in every theme (green ARMED / Live,
orange warnings, red alarms, blue tests), so the state reads at a glance.

**Quieter log.** Single one-reading pump glitches are counted in the hourly OK line
("N one-reading pump glitches ignored") instead of one line each.

## 2.2.0 — 2026-11-25

**Renamed: PumpGuard is now LoopMedic.** Running install.sh removes an old PumpGuard install (service,
power-off timer, tray autostart, files) and carries over its settings, log and self-test / last power-off records.

**Trips**
- The pump trip fires only when the pump has **stopped** (≤ `PumpStoppedRpm`, 150 by default).
  A pump that is merely slow (below `PumpMinRpm`, 500) raises a warning (DEGRADED, plus a journal warning) and
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
- The service checks in with systemd (`Type=notify`, `WatchdogSec`): systemd restarts it within a second if it
  exits and after 30 s if it freezes.
- Start-up self-check: the CPU temperature is plausible (10–100 °C), the service runs as root, and
  `systemctl` / `systemd-run` are present.
- Monthly self-test: arms and removes a harmless systemd timer, proving the power-off path works. A failure
  shows DEGRADED with a notification and is retried daily.
- `last-poweroff.json` (in /var/lib/loopmedic) is written just before a forced power-off; the tray shows
  "LoopMedic powered off this PC" once at the next login.
- Important moments also go to the system journal with a priority: `journalctl -t loopmedic -p warning`.

**Setup**
- CPU limit suggested from `cpu-limits.json`, with a picker for the CPU family.
- The likely pump header is pre-selected, with a live view (press w). Setup warns when no header spins at pump
  speeds (a USB-only AIO → temperature-only protection).
- Offers the `nct6683 force=1` setting when newer MSI boards hide their fans.

**Tray**
- **Updates:** click the version number at the top of the panel to check GitHub for a newer release. If there is one,
  a window shows what's new; **Update now** downloads it, checks its RSA signature against the release key built into
  the tray (a changed download is refused), asks for your password and installs it with your settings kept.
  The new tray says "updated" once. Nothing checks in the background.
- The power-off notice and the amber "last power-off" line clear after 24 hours (was 7 days).
- The pump graph keeps headroom of 10% of the speed above and below the line, so the sensor's normal
  one-reading wiggles (a few %) show as small ticks instead of filling the graph. Every reading is still shown.

**Docs and tests**
- README: no-warranty notice, pump-reading accuracy and cable-routing warning, layers of protection and
  BIOS settings, journal commands, "why did my PC turn off?".
- `tests/run-tests.py`: 21 scenarios against fake sensor folders (no hardware, nothing powers off).
- License: PolyForm Noncommercial 1.0.0.

## 2.1.1 — 2026-11-25
- First Linux version: a port of the Windows 2.1.1 rules as a root systemd service (Python standard library only),
  a PySide6 tray app for KDE Plasma, and a guided setup.
