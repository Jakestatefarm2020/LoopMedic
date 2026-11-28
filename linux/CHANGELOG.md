# Changelog

LoopMedic for Linux. The Windows version (../windows) keeps the same version numbers and rules.

## 2.3.2 — 2026-11-28

Fixes from an independent review.

- **A pump that's dead at boot can now trip.** A pump that has read "stopped" since boot isn't trusted on its
  own (a speed cable on the wrong header reads 0 too), so until now only a warning was given. Now LoopMedic
  powers off anyway if the CPU confirms the cooler is dead: at or above `NeverSpunTripC` (default: the limit
  minus 15 °C) while lightly loaded (`NeverSpunMaxLoad`, 25 %) for `CpuHotSeconds`. A busy CPU is left to the
  normal CPU trip. If the last power-off was for this, the next boot only warns (tray + log, no email) until
  the pump spins, so it can't power off on every boot. The "hasn't spun since boot" email is now important
  (retried).
- **Failed sensor reads no longer reset a stall.** Once the pump reads stopped, a failed read counts as still
  stopped; only a real reading above the stopped line clears it. A dying tach that alternates 0 and no
  reading now trips.
- **Safer updates.** The tray still checks the download, but the install now starts from the installed
  /opt/loopmedic/loopmedic.py (root-owned) through pkexec: it copies the package into a root-only staging
  folder, checks the signature again there, refuses a version that isn't newer, and runs the new setup from
  there. `--allow-downgrade` on `setup_loopmedic.py --update-root` for a deliberate rollback; install.sh from
  an older release still works. (This update itself still installs the old way.)
- **Mail password wording:** the docs now say plainly that it's obfuscated with a key on this PC (root can
  recover it), so use an app password.
- **Tray:** empty fan headers (0 rpm) are no longer listed; the byline reads "by Jake".
- **Docs:** honest headline (powers off instead of letting the CPU sit at its thermal limit unnoticed).

## 2.3.1 — 2026-11-27

- **Two new themes.** Tray right-click > Themes: **Neon Dark** (hot magenta pump, neon cyan CPU) and
  **Neon Light** (magenta pump, electric violet CPU, neon orange for the other fans, on white).
- **Atomic** now uses yellow for the pump and CPU readings, both graphs and the pump bar; the other fans are
  grey, so the pump stands out.
- The README now says plainly that LoopMedic works with AIO / water coolers and air coolers.

## 2.3.0 — 2026-11-27

- **Air coolers.** Setup asks what cools your CPU. With an air cooler, the CPU fan takes the pump's place:
  same rules, "CPU fan" instead of "pump" everywhere. A fan that stops at idle (zero RPM) is refused with advice.
- **Email alerts (optional).** Through your own SMTP server: powering off (as the countdown starts), back on
  after a power-off, a cancelled power-off, a warning starting or clearing, a failed self-test. Set up in
  setup (step 5/6) or later in `/etc/loopmedic/mail.json`; changes apply within seconds, no restart. The password is
  scrambled with a root-only key and the file is root-only. Sending runs in the background, so a dead mail server never delays a power-off.
  Port 587 (STARTTLS) or 25; 465 isn't supported. Tray: **Send a test email**.
- **Early warning - EXPERIMENTAL, off unless you turn it on in setup.** Learns the pump's normal speed over
  5 days (per CPU-load band), then warns once (a desktop notification, a log line, an email if set up) if it runs 12% slower
  over 3 days. Never a power-off. Only briefly tested so far. Tray: **Relearn normal pump speed**.
- **Themes.** Tray right-click > Themes: Stealth (default), Atomic, Dark Side, Ice. Status colours (green
  ARMED / Live, orange warnings, red alarms) are the same in every theme.
- **Quieter log.** One-reading pump glitches are counted in the hourly line instead of one line each.

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
