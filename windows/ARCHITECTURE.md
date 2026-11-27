# LoopMedic: how it works

A safety add-on: if the AIO pump stops, or the CPU stays too hot, it forces a power-off. It is one layer
among several (the CPU's own thermal shutdown, BIOS fan-fail and temperature settings). It is not a
replacement for them.

## Processes (Windows)

```
Task Scheduler (SYSTEM, at startup + every 5 min "is it running?", restart after 1 min)
  └─ LoopMedic.exe                 supervisor: one per install folder (Global mutex)
       └─ LoopMedic.exe --worker   the watchdog: reads sensors every 2 s, decides, writes status.json
            └─ LoopMedic.exe --poweroff ...   power-off helper: countdown, then NtShutdownSystem(PowerOff)
LoopMedicTray.exe (the signed-in user, not elevated)   reads status.json, writes requests to control\
```

The tray only goes online when the user clicks the version number in the panel (see Updates).

- **Supervisor.** Restarts a worker that exits within 2 s. If status.json hasn't changed for 30 s (after a
  60 s start-up allowance), the worker is treated as frozen, killed and restarted. Exit codes 2–4 (config or
  sensor problems) and more than 5 restarts in 2 minutes back off to 30 s. Each restart logs event 1030.
- **Worker.** Exits if its supervisor dies (`--parent`), so there is never a worker without a supervisor.
- **Helper.** A separate process, so the countdown survives a worker crash. On a real trip it also sets
  `shutdown /s /f /t <delay+60>` as a backup timer. Cancelling aborts both.
- **Tray.** Purely a viewer and remote control. Protection never depends on it.

## Updates

Clicking the version number asks the GitHub API for the latest release of `Jakestatefarm2020/LoopMedic`.
If it is newer, the tray shows the release notes and, on **Update now**, downloads `LoopMedic.zip` and
`LoopMedic.zip.sig` to %TEMP% and checks the signature: RSA-3072, PKCS#1 v1.5 over SHA-256, against the
public key compiled into the tray. Only then does it unzip and start `Update-LoopMedic.ps1` elevated (UAC).
That builds the new version, keeps the current `config.json` (adding any new setting with its default)
and runs the installer. `tools/pack-release.py` signs the packages with the private key, which is never in
this repo. The Linux tray does the same with `LoopMedic-Linux.tar.gz(.sig)` and pkexec.

## Files

| Path (`C:\ProgramData\LoopMedic`) | Who writes | What |
|---|---|---|
| `LoopMedic.exe`, `lhm\`, `config.json`, `cpu-limits.json` | installer (admin-only ACL) | program and settings |
| `status.json` | worker (atomic replace) | live state for the tray |
| `loopmedic.log` (+ `.old`) | worker, helper | 1 MB rotation, one old file |
| `selftest.json` | worker | last monthly self-test |
| `last-poweroff.json` | helper (write-through, before power-off) | "why did my PC turn off" |
| `health-samples.jsonl`, `health.json` | worker | early-warning samples (60 days) and the learned normal |
| `mail.json` | installer (template), you, worker (encrypts the password) | email alert settings; SYSTEM + Administrators only |
| `control\` (Users: Modify) | tray | `pause.json` (until), `test.json` (requested), `cancel.json` (at), `poweroff.json` (at), `tray.json` (at), `health-reset.json` (at), `mail-test.json` (at) |

The worker reads only **dates** from `control\` files and ignores everything else in them.

## Decision rules (every 2 s)

1. **Pump reading → accepted value (jump rule).** A rise to more than 1.4× and more than 100 rpm is held.
   It is accepted if the next reading is within 25 % of it, otherwise dropped ("came straight back").
   Drops are accepted at once.
2. **Pump stopped** (accepted ≤ `PumpStoppedRpm`, and the pump has read above that since start): trip after
   `PumpFailSeconds` (10), or `PumpFailSecondsCool` (60) while the CPU is below `CoolCpuTempC` (60).
3. **Pump slow** (stopped < accepted < `PumpMinRpm`) for 10 s: DEGRADED warning only (event 1040).
4. **CPU ≥ limit** for `CpuHotSeconds`: trip. The limit comes from config if it is in 1–110 °C, otherwise
   from `cpu-limits.json` (trip = Tjmax − 5, or Tjmax held 60 s for chips that run at their limit), or a
   95 °C fallback.
5. **Countdown:** `ShutdownDelaySeconds` (30), or `HotShutdownDelaySeconds` (5) if a pump trip starts with
   the CPU already over its limit. During a countdown, CPU ≥ limit (pump trip) or ≥ limit + 5 (CPU trip),
   held 5 s, cuts the countdown short.
6. Grace: no trips in the first `StartupGraceSeconds` (20), or for `RearmSeconds` (120) after a cancel.

7. **Air cooler mode** (`CoolerType` = `air`): the CPU fan is watched with exactly the same rules; only the
   wording changes.

All durations use a monotonic clock (Stopwatch), so a clock change can't delay a trip. A pause request is
turned into a monotonic deadline when the file is first seen. The deadline is capped at 2 h from the file's
write time, and files dated more than 5 min in the future are ignored.

## Early warning (a pump slowing down)

Accepted readings go into the current 20-minute window (`HealthSampleMinutes`), but only while running
normally: pump known good and above the stopped line, no pause or trip, and at least 5 min after start or
after a 10 s+ stall (sleep). Each reading is filed under the CPU load band at that moment (LHM "CPU Total":
light < 25 %, medium < 60 %, heavy; "all" without a load sensor). At the end of the window, each band with
≥ 60 readings becomes one sample (median rpm, median °C, count) appended to `health-samples.jsonl`.

The first `HealthLearnDays` (14) of samples set the **normal** per band (median of ≥ 36 samples), kept in
`health.json`. It is fixed so that a slow decline can't drag its own baseline down. Each new sample compares
the median of the last `HealthRecentDays` (3, ≥ 18 samples) with the normal:
- ≥ `HealthWarnPercent` (12 %) slower in any band: `EARLY WARNING` log line, `healthWarning` {id, text} in
  status.json (the tray shows it once per id as a notification, never in the panel), and an email.
  Repeats after 7 days or 5 points worse; clears below half the threshold.
- ≥ 12 % faster with nothing slower: the curve was changed, so the samples and normal are reset and
  learning starts again. `control\health-reset.json` (tray: Relearn) does the same on request.

## Email alerts

Optional, off until `mail.json` says `"Enabled": true`. The worker loads it at start, then reloads it ~2 s
after a FileSystemWatcher change notice (no polling), with a last-write-time check once a minute as a
backstop. A bad file is logged and the previous settings stay in use; protection is never restarted.
A plain-text `Password` is replaced at once by `PasswordProtected` (DPAPI, LocalMachine scope plus a fixed
entropy, so only this PC can decrypt it), and the file's ACL is reset to SYSTEM + Administrators.
Messages go into a queue served by a background thread (`SmtpClient`, 20 s timeout, STARTTLS or none;
important ones retry for ~15 min, others are capped at 6 an hour). A trip never waits on mail.

## Self-checks

- **Start-up:** CPU temperature in 10–100 °C, and `RtlAdjustPrivilege(SeShutdownPrivilege)` succeeds
  (then restored). A failure → DEGRADED, plus a notification from the tray.
- **Monthly:** runs the helper with `--self-test --dry-run` (exit 0 OK, 5 no privilege). The first run comes
  10 min after the first start, and a failure retries in 24 h.

## Threat model (what we defend against, and what we don't)

| Risk | Handling |
|---|---|
| Worker crash or hang | supervisor (seconds), then the task (1 min / 5 min) |
| Tray closed or crashed | protection unaffected; the helper uses msg.exe if the tray isn't alive |
| Clock changes (DST, NTP, user) | monotonic durations |
| A non-admin user stopping protection | can only write to `control\`. **Pause is the known lever:** any local user can pause for up to 2 h at a time and re-write it. Accepted: the tool assumes the users of the PC aren't hostile to it. It is a safety tool, not a security boundary. |
| A malicious `control\` file | only a date is parsed. Size and type limits apply, and symlinks are refused on Linux. |
| Tampering with the program files | admin-only ACL. The SYSTEM task never runs anything a normal user can edit. |
| A fake or altered update (hijacked account, swapped download) | refused unless signed with the release key, which never leaves the author's PC. The first download from the Releases page is trusted as-is (HTTPS from GitHub). |
| Sensor noise / bad tach | jump rule; only a *stopped* pump trips |
| The mail password on disk | encrypted with DPAPI (this PC only) as soon as it is seen; admin-only file. An admin on the PC can still recover it - use an app password. |
| A dead or slow mail server | sends run on their own thread with timeouts; trips and power-offs never wait for them |
| Pulse-count mismatch (rpm wrong by 2×) | only a *stopped* pump trips. Slow = warning. |
| A pump spinning without flow | CPU temperature trip only |
| A frozen OS, sleep, boot | out of scope: the CPU's own THERMTRIP and BIOS settings |
| A false trip powering off a healthy PC | 30 s countdown with Cancel, cool-CPU grace, jump rule, chip-aware limits. Still possible: no warranty. |

## Tests

`tests\run-tests.py` runs the real exe against fake sensors (`--fake-sensors` JSON), in dry-run mode, with the
Event Log off. It covers 34 scenarios: trips, the jump rule, slow-pump warnings, clock jumps, cancel, hotter =
faster, pause limits, a sensor vanishing, CPU changes, broken config, missing privilege, self-tests, last
power-off, temperature-only mode, air cooler mode, supervisor restarts after a crash or a hang, the early
warning (a dying pump, a slow decline, a raised curve, a steady pump, still learning, sampling and relearn)
and email alerts against a local fake mail server (trip email, encrypted password, live reload, a broken
file, test emails, and a dead server that must not delay the power-off).

Tested on the author's own PC (November 2026, real hardware, real forced power-offs; scripts in `tools/real-tests/`):

- **Sleep / resume:** protection carries on after waking. This test found a bug: the supervisor took sleep for a hang. Fixed in 2.2.0.
- **Pump stopped:** 30 s alarm, forced power-off, and the "why your PC turned off" notice after boot.
- **CPU too hot:** forced power-off, including the hotter = faster cut about 8 s in.
- **Pump stopped while the CPU is already hot:** 5 s power-off.

The Linux version has passed its automated tests but hasn't yet run on a real Linux PC.
