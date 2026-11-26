LoopMedic 2.2 for Linux                               by Jacob Stiltner
==========================================================================

  Powers your PC off if your AIO pump stops, before the CPU cooks.
  Made for CachyOS / Arch with KDE Plasma; works on most systemd distros.

  pump STOPS ---> 10 s ---> pop-up with a 30 s countdown ---> power off
                                 |
                                 +--> [Cancel shutdown]  (false alarm)

  It also powers off if the CPU stays at its limit for 30 s (60 s on
  chips designed to run at their limit). A pump that is only SLOWER than
  usual gets a warning, not a power-off. If the CPU is already over its
  limit, the countdown is 5 s instead of 30 ("hotter = faster").

  PLEASE READ: NO WARRANTY
  LoopMedic is an EXTRA layer of protection, not a replacement for the
  ones your PC already has (see LAYERS OF PROTECTION). It can miss a
  failure, and it can power your PC off when nothing is wrong. We aren't
  saving anyone's hardware - we're adding one more layer on top of the
  ones you should already have. Use it at your own risk; the authors
  accept no liability for damage, lost work or lost data (LICENSE.md).


--------------------------------------------------------------------------
INSTALL (about 3 minutes)
--------------------------------------------------------------------------

  1. Extract this folder anywhere (e.g. ~/LoopMedic) and open a terminal
     in it.
  2. Run, as your normal user (NOT with sudo):

         bash install.sh

     It asks for your sudo password when it needs it, and walks you
     through five steps:

     1/5 Tray icon support   offers: sudo pacman -S --needed pyside6
                             (the tray only - protection works without it)
     2/5 Motherboard sensors if no fans show up, offers to enable the
                             nct6683 driver with force=1 (newer MSI boards
                             use this Nuvoton chip; it is only READ, no fan
                             speed is ever changed), and keeps that setting
     3/5 Find your pump      numbered fan list; the likely pump is marked
                             and Enter picks it; press w to watch them live
                             for 20 s while you change the pump speed.
                             No header at pump speed? Your AIO may report
                             over USB only - pick 0 (temperature only).
     4/5 CPU limit           suggested for YOUR chip from a table of
                             common AMD/Intel CPUs (9800X3D: 90 C).
                             Enter takes it, l picks a family from a list.
                             It can't be turned off.
     5/5 Safe test           pretends the pump dies, checks LoopMedic
                             reacts - nothing powers off - then installs

  Nothing else to download: Linux reads the sensors itself (the Windows
  version's PawnIO driver isn't needed here).


--------------------------------------------------------------------------
THE TRAY ICON
--------------------------------------------------------------------------

  Shield colour   Meaning
  -------------   ------------------------------------------------------
  green, tick     ARMED - all good, protecting
  orange, !       WATCHING - pump stopped or CPU hot, counting
  red, !          TRIPPED - power-off countdown running
  orange, ?       DEGRADED - protection weaker, or the pump is slower
                  than usual. The panel says what; a notification pops
                  up too.
  blue, T         TEST, or STARTING (first 20 s after boot)
  grey, II        PAUSED (max 30 min, resumes by itself)
  dark grey, ?    OFFLINE - no data from the service for 20+ s
                  (the panel's Live dot turns orange "Waiting" at 10 s)

  Left-click   panel: pump + CPU with 10-minute graphs, what trips and
               what only warns, CPU headroom, today's CPU peak / lowest
               pump speed, the last self-test, every fan with a speed bar,
               Pause 30 min / Run test / Open log, and a Live indicator
  Right-click  same actions + "Exit tray icon" (protection keeps running)

  UPDATES: click the version number (e.g. "v2.2.0") at the top of the
  panel; it turns blue when you point at it. LoopMedic asks GitHub for
  the newest version, shows what's new, and on "Update now" downloads it,
  checks it is genuinely signed by the author (a changed or fake download
  is refused), asks for your password and installs it, keeping your
  settings. Nothing checks for updates by itself.

  On Wayland, KWin decides where the panel opens (it may not sit right
  above the icon). The tray starts at login (~/.config/autostart) and is
  also in the app menu as "LoopMedic".

  TRY IT: right-click the shield > Run test. A blue TEST pop-up shows what
  a real trip looks like. Nothing powers off.


--------------------------------------------------------------------------
FROM THE TERMINAL
--------------------------------------------------------------------------

  systemctl status loopmedic               is it running?
  journalctl -u loopmedic -f               live log
  journalctl -t loopmedic -p warning       only the important moments
                                           (trips, warnings, self-test
                                           failures, power-offs)
  cat /run/loopmedic/status.json           the live readings (JSON)
  less /var/log/loopmedic/loopmedic.log    log (1 "OK" line per hour)
  bash watch-fans.sh                       live fan speeds

  It checks itself:
    - at every start: the CPU temperature is sensible (10-100 C), it runs
      as root, and systemctl / systemd-run are there
    - once a month: it arms and removes a test systemd timer, proving the
      power-off path still works
  A failure shows as DEGRADED with a notification. systemd restarts the
  service 1 s after any crash, and its watchdog restarts it if it freezes
  for 30 s.

  Other log lines (none of them powers anything off):
    Pump jump: ... (one reading, came straight back - ignored)
                               a single bad reading (see PUMP READINGS)
    Pump jump: ... (confirmed by the next reading)
                               the pump really did speed up
    Stalled: no check for N s  the service fell behind (checks normally land
                               every 2 s). Rare = fine; lots = worth a look.

  Files:  /opt/loopmedic/            program + cpu-limits.json (root-owned)
          /etc/loopmedic/config.json settings
          /etc/systemd/system/loopmedic.service
          /var/lib/loopmedic/        self-test + last power-off records
          /var/lib/loopmedic/control the only folder your user can write
                                     (the tray's pause/test/cancel
                                     requests; the root service only ever
                                     reads a date out of these files,
                                     refuses symlinks and files > 4 KB)


--------------------------------------------------------------------------
PUMP READINGS: WHAT THE NUMBER REALLY MEANS
--------------------------------------------------------------------------

  * THE RPM IS AN ESTIMATE. The pump's speed wire sends a few pulses per
    turn and the motherboard converts them to rpm assuming the usual 2
    per turn. Some pumps send a different number, and some AIOs make up
    the signal in their own controller, so LoopMedic's rpm can differ from
    the cooler's own app. Both can be steady and still disagree.

    That's why LoopMedic only POWERS OFF when the pump reads as STOPPED
    (150 rpm or less) - a stopped pump reads near zero whatever the
    conversion. "Slower than usual" (under 500 rpm) is a warning only, so
    a quiet/eco pump mode that reads low won't power your PC off. A pump
    that spins but moves no coolant is caught by the CPU limit.

  * CABLE ROUTING CAN CAUSE BAD READINGS. The speed wire is thin and
    unshielded; next to power cables (24-pin, CPU 8-pin, GPU power) it
    can pick up noise that looks like extra pulses. On one tester's PC,
    about 1 reading in 30 came back at roughly double the real speed under
    heavy load - the pump was fine. Lots of "Pump jump" lines = try
    routing the speed wire away from power cables.

    How LoopMedic handles it: a sudden JUMP UP is only believed if the
    next reading agrees; a single odd reading is ignored (and logged).
    A DROP always counts straight away, so noise can never hide a stopped
    pump. The panel shows what LoopMedic believes; the log keeps every
    jump.


--------------------------------------------------------------------------
IF IT EVER FIRES
--------------------------------------------------------------------------

  A red pop-up ("PUMP FAILURE DETECTED" / "CPU TEMPERATURE LIMIT
  REACHED") plus a critical notification count down 30 seconds:

    [Shut down now]     power off immediately
    [Cancel shutdown]   false alarm? stops it; re-arms in 2 minutes
    (no choice)         the PC powers off when the timer runs out

  HOTTER = FASTER: already over the CPU limit when the pump stops = 5 s
  countdown. Getting that hot DURING a countdown (5 C over the limit for
  a CPU alarm) = power off after 5 s without waiting for the timer.

  The power-off is FORCED on purpose: sync (max 5 s), then
  'systemctl poweroff -ff' - like holding the power button, minus most of
  the data-loss risk. It protects the hardware, not open work.

  The countdown is a systemd timer (loopmedic-poweroff.timer), so it fires
  even if the service or the tray crashes. Without the tray, cancel with:
      sudo systemctl stop loopmedic-poweroff.timer

  If the CPU is cool (< 60 C) when the pump stops, LoopMedic waits 60 s
  instead of 10 (a cooler firmware update or fan calibration can stop the
  pump briefly). Once the CPU passes 60 C the normal 10 s apply.

  "WHY DID MY PC TURN OFF?"
  If LoopMedic powered it off, the tray says so at your next login
  ("LoopMedic powered off this PC", with the time and reason), the panel
  shows it for 24 hours, and journalctl -t loopmedic -p warning -b -1 shows
  the trip from the previous boot. No notification = it wasn't LoopMedic.


--------------------------------------------------------------------------
LAYERS OF PROTECTION (LoopMedic is only one)
--------------------------------------------------------------------------

  1. THE CPU ITSELF throttles at its maximum temperature and cuts its own
     power far past it - even if Linux has frozen. The last line of
     defence, not something to lean on every day.
  2. YOUR MOTHERBOARD (BIOS) - worth five minutes (names vary by brand;
     look in "Hardware Monitor" / "Fan"):
       - set the pump header to full speed / "Pump" mode
       - turn on the fan-fail warning for the pump header
       - turn on any "shutdown temperature" / "CPU over temperature
         protection" setting
     These work before Linux starts and while it's frozen.
  3. LOOPMEDIC - watches from inside Linux: warns, counts down, lets you
     cancel, explains what happened, keeps a log. Only while Linux runs.
  4. YOU - glance at the shield now and then and listen to your pump.

  What LoopMedic can't do: help while the PC is asleep, booting or frozen
  solid (layers 1 + 2 cover that); see a pump that spins but moves no
  coolant (only the CPU limit catches that); see a pump that reports
  only over USB (temperature only).


--------------------------------------------------------------------------
BEFORE YOU UPDATE OR TINKER
--------------------------------------------------------------------------

  Click the shield > Pause 30 min first if you are about to:
    * flash the BIOS, or change fan curves in it
    * run fan calibration (fancontrol's pwmconfig, CoolerControl...) -
      these can spin headers down to 0 on purpose
    * update the cooler's firmware (on Windows / another tool)

  After kernel updates (CachyOS ships them often), BIOS updates or
  installing other sensor drivers: check the shield is GREEN and the
  panel says "Live". Orange "?" or a notification = run install.sh again
  (it offers to keep your settings).


--------------------------------------------------------------------------
CHANGE SETTINGS / REMOVE
--------------------------------------------------------------------------

  * Different pump or limit: bash install.sh again.
  * Fine-tuning: edit /etc/loopmedic/config.json (sudo), then
        sudo systemctl restart loopmedic
      PumpStoppedRpm (150)   at or below = stopped = power off
      PumpMinRpm (500)       below = "slower than usual" warning
      PumpFailSeconds, PumpFailSecondsCool, CoolCpuTempC
      CpuMaxTempC            0 or a silly value doesn't turn it off - the
                             table's value for your CPU (or 95 C) is used
      CpuHotSeconds
      ShutdownDelaySeconds (30), HotShutdownDelaySeconds (5)
  * Remove completely: bash uninstall.sh

  !! MOVING THE PUMP CABLE TO ANOTHER HEADER?
  !! Run install.sh again afterwards. Until then LoopMedic sees 0 rpm,
  !! shows DEGRADED and only watches the CPU temperature.

  !! NEW CPU? LoopMedic switches to the table's limit for it and shows
  !! DEGRADED until you run install.sh again to confirm.


--------------------------------------------------------------------------
WHAT'S IN THIS FOLDER
--------------------------------------------------------------------------

  install.sh                  guided setup + install (run this)
  uninstall.sh                removes everything
  watch-fans.sh               live fan speeds, to find your pump
  config.json                 your settings (written by setup)
  loopmedic/loopmedic.py      the root service (Python standard library)
  loopmedic/loopmedic_tray.py the tray icon + pop-up (PySide6)
  loopmedic/setup_loopmedic.py  the guided setup
  loopmedic/cpu-limits.json   the CPU temperature table
  tests/run-tests.py          scenario tests (fake sensors, nothing
                              powers off): python3 tests/run-tests.py
  LICENSE.md                  free for personal, non-commercial use
  README.txt                  this file
