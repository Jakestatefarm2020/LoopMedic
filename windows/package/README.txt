==========================================================================
  LOOPMEDIC 2.2
  by Jacob Stiltner

  Powers your PC off if your AIO pump stops, before the CPU cooks.
==========================================================================

  PLEASE READ: NO WARRANTY
  LoopMedic is an EXTRA layer of protection, not a replacement for the
  ones your PC already has (see "LAYERS OF PROTECTION" below). It can
  miss a failure, and it can power your PC off when nothing is wrong.
  We aren't saving anyone's hardware - we're adding one more layer on top
  of the ones you should already have. Use it at your own risk; the
  authors accept no liability for damage, lost work or lost data
  (see LICENSE.md).

It runs quietly in the background from startup. Every 2 seconds it reads:
  - the pump speed, from the motherboard header the pump's speed cable
    plugs into
  - the CPU temperature

  When...                              for...   then...
  -----------------------------------  -------  --------------------------
  the pump has STOPPED                 10 sec   an alarm pops up with a
  (150 rpm or less)                             30 sec countdown; if you
                                                don't choose, the PC
                                                powers off
                                                (5 sec if the CPU is
                                                already over its limit)
  the CPU is at or above its limit     30 sec   same
                                       (60 sec
                                       on some
                                       chips)
  the pump is SLOWER than usual        10 sec   a warning only - the PC
  (under 500 rpm, but spinning)                 is NOT powered off for
                                                this; the CPU limit above
                                                still covers you

A shield icon in the system tray shows it's working, with live pump,
CPU and fan readings.

It doesn't touch your cooler's app (NZXT CAM, iCUE, etc.); that keeps
working as normal. LoopMedic only READS sensors; it never changes a fan
or pump speed.


--------------------------------------------------------------------------
BEFORE YOU START
--------------------------------------------------------------------------

* Windows 10 or 11. Everything else is in this folder.

* Your pump's speed cable should be plugged into the motherboard.
  Most AIOs have a small 3-pin lead for this, going to a header labelled
  PUMP, AIO_PUMP, W_PUMP or CPU_FAN.
  If your pump only reports its speed over USB to its own app, LoopMedic
  can only use the CPU temperature check (setup spots this and tells you).

* Unblock the zip BEFORE extracting it. Windows blocks downloaded files.
  Right-click the zip > Properties > tick "Unblock" > OK.
  Then extract it somewhere permanent (e.g. Documents\LoopMedic).
  If you skip this, Windows may show "Windows protected your PC" when you
  run Install.bat. Click "More info" > "Run anyway".


--------------------------------------------------------------------------
INSTALL (about 3 minutes)
--------------------------------------------------------------------------

1. Double-click Install.bat and click Yes on the admin prompt.

2. Follow the 5 steps on screen:

   Step 1 - Unblocks the files. Automatic.

   Step 2 - Checks the sensor driver. If PawnIO is missing, it offers to
            install it. Say Y.
            - PawnIO is a small, signed, open-source driver from
              https://pawnio.eu, the one LibreHardwareMonitor and
              FanControl use.
            - Its official installer is included in the pawnio folder,
              so no internet is needed.
            - Setup checks the installer's digital signature before
              running it, and uses winget if that check fails.
            - If Windows asks for a restart, restart and run Install.bat
              again.

   Step 3 - Asks you to pick your pump. You'll see a numbered list of fan
            sensors and their rpm. If one looks like the pump, it's
            marked "<- looks like the pump" and Enter picks it.
            The pump is usually the one at 1,500-4,000 rpm; the 0 rpm
            entries are empty headers.
            Pick 0 if your pump isn't on a motherboard header.

   Step 4 - Suggests a CPU temperature limit for YOUR chip (from a table
            of common AMD and Intel CPUs). Press Enter to take it, type
            your own, or press L to pick your CPU family from the list.
            - Most chips: 5 C under the maker's maximum, held 30 sec.
            - Ryzen 7000/9000 (non-X3D) and Intel 12th gen and newer are
              designed to sit AT their maximum under heavy load, so for
              those the limit is the maximum itself, held 60 sec.
            - A CPU that isn't in the table gets a safe 95 C.
            The temperature check can't be turned off: it's the backup
            for everything the pump check can't see.

   Step 5 - Runs a safe test, then installs. It pretends the pump reads
            0 rpm and checks that LoopMedic reacts; nothing powers off.
            Then answer Y to install.

You're done. It now starts with Windows.

  NOT SURE WHICH SENSOR IS THE PUMP?
  Run Watch-Pump.bat (it shows every fan for 90 seconds). While it runs,
  change the pump speed in your cooler app: the number that moves is your
  pump. Then run Install.bat again and pick it.


--------------------------------------------------------------------------
THE TRAY ICON
--------------------------------------------------------------------------

Look for the shield in the system tray (bottom right; it may be hidden
under the ^ arrow - drag it onto the taskbar to keep it visible).

  Shield colour          Meaning
  ---------------------  ----------------------------------------------
  green, tick            ARMED - all good, protecting
  orange, !              WATCHING - pump stopped or CPU hot, counting
  red, !                 TRIPPED - power-off countdown running
  orange, ?              DEGRADED - protection is weaker, or the pump is
                         slower than usual. The panel says what; a
                         Windows notification pops up too.
  blue, T                TEST, or STARTING (first 20 sec after boot)
  grey, II               PAUSED - protection off (max 30 min, resumes
                         by itself)
  dark grey, ?           OFFLINE - no data for 20+ sec

  Hover        one-line summary
  Left-click   panel:
                 - pump + CPU with 10-minute graphs
                 - what trips (pump stopped / CPU limit) and what only
                   warns (slow pump), and your CPU headroom
                 - today's CPU peak and lowest pump speed
                 - the last self-test result
                 - every fan with a speed bar (pump in green)
                 - buttons: Pause 30 min / Run test / Open log
                 - bottom-left dot: Live (green), Waiting for sensors
                   (orange, 10-20 sec old), Offline (20+ sec)
  Right-click  the same actions + "Exit tray icon"
               (exiting only hides the icon - protection keeps running)

UPDATES: click the version number (e.g. "v2.2.0") at the top of the panel.
It turns blue when you point at it. LoopMedic asks GitHub for the newest
version and either says you're up to date or shows what's new with an
"Update now" button. Update now downloads it, checks it is genuinely
signed by the author (a changed or fake download is refused), then asks
for admin permission and installs it. Your settings are kept. Nothing
checks for updates by itself.

TRY IT: right-click the shield > Run test. A blue TEST alarm shows what a
real trip looks like. Nothing powers off.


--------------------------------------------------------------------------
CHECK IT'S WORKING
--------------------------------------------------------------------------

Easiest: the shield is green and the panel says "Live".

Or open PowerShell and run these two commands:

    Get-ScheduledTask LoopMedic | Select-Object TaskName, State
    Get-Content C:\ProgramData\LoopMedic\loopmedic.log -Tail 20

You want "State: Running" and a log that says "ARMED" and
"Start-up self-check: OK". After that it adds one "OK" line per hour with
the hottest CPU temperature and the slowest and fastest pump speed.

It checks itself, too:
  - At every start: the CPU temperature reads something sensible
    (10-100 C) and Windows lets it power the PC off.
  - Once a month: it runs its power-off helper in a "pretend" mode to
    prove the power-off path still works.
  If either fails, the shield turns orange (DEGRADED) and you get a
  notification. Run Install.bat again to fix it.

EVENT VIEWER: LoopMedic also writes important moments to Windows' own
log: Event Viewer > Windows Logs > Application, source "LoopMedic".

  Event ID   Meaning
  --------   ------------------------------------------------------------
  1000       Started
  1001       Protection reduced (DEGRADED) - the message says why
  1002       Protection back to normal
  1010       TRIPPED - power-off countdown started
  1011       Countdown cancelled
  1012       Powering off now
  1013       Test trip (from "Run test")
  1020/1021  Self-test OK / FAILED
  1030       The watchdog was restarted (it crashed or froze)
  1040       Pump slower than usual (warning only)
  1050       "LoopMedic powered off this PC" - read at the next start

  After a power-off, 1010 and 1012 are usually MISSING. Windows keeps the
  newest Event Viewer entries in memory and loses the last half minute or
  so when the power is cut - nothing a program can do about it (tested).
  1050 is the one to look for: it's written after the restart, from
  LoopMedic's own record, which is saved to disk before the power-off.
  loopmedic.log has every line too.

Other lines you may see in the log (none of them powers anything off):
  Pump jump: 2400 -> 4600 -> 2400 rpm (one reading, came straight back
             - ignored)
               a single bad reading (see PUMP READINGS below)
  Pump jump: 2400 -> 3600 rpm (confirmed by the next reading)
               the pump really did speed up (your cooler app changed it)
  Stalled: no check for 12 s
               LoopMedic fell behind (checks normally land every 2 sec).
               Once in a while is fine; lots of them = tell whoever set
               this up.


--------------------------------------------------------------------------
PUMP READINGS: WHAT THE NUMBER REALLY MEANS
--------------------------------------------------------------------------

* THE RPM IS AN ESTIMATE. Your pump's speed wire sends a few electrical
  pulses per turn, and the motherboard converts that into rpm assuming
  the usual 2 pulses per turn. Some pumps send a different number, and
  some AIOs make up the signal in their own controller. So the rpm
  LoopMedic shows can be different from your cooler app (NZXT CAM,
  iCUE...) - both can be steady and still disagree. We can't correct
  this for every pump without measuring each one.

  That's why LoopMedic only POWERS OFF when the pump reads as STOPPED
  (150 rpm or less) - a stopped pump reads near zero whatever the
  conversion. "Slower than usual" (under 500 rpm) is only a warning, so
  a pump in a quiet / eco mode that really spins at 700 rpm but reads
  400 will not power your PC off. If a pump is spinning but not moving
  enough coolant, the CPU temperature limit is what catches it.

* CABLE ROUTING CAN CAUSE BAD READINGS. The pump's speed wire is thin
  and unshielded. Run next to power cables (the 24-pin, CPU 8-pin or GPU
  power cables) it can pick up electrical noise that looks like extra
  pulses. On one tester's PC, about 1 reading in 30 came back at roughly
  double the real speed under heavy load - the pump itself was fine.
  If your log shows lots of "Pump jump" lines, try routing the pump's
  speed wire away from power cables.

  How LoopMedic handles it: a sudden JUMP UP is only believed if the
  next reading agrees. A single odd reading is ignored and logged as
  "came straight back - ignored". A DROP always counts straight away,
  so noise can never hide a pump that has stopped. The panel shows what
  LoopMedic believes; the log keeps every jump so you can see them.


--------------------------------------------------------------------------
IF IT EVER FIRES
--------------------------------------------------------------------------

An alarm near the top of the screen says "PUMP FAILURE DETECTED" or
"CPU TEMPERATURE LIMIT REACHED" and counts down 30 seconds:

  [Shut down now]     cuts power immediately
  [Cancel shutdown]   false alarm? stops it; LoopMedic re-arms in 2 min
  (no choice)         the PC powers off when the timer runs out

HOTTER = FASTER: if the CPU is already over its limit when the pump
stops, the countdown is 5 seconds instead of 30. If the CPU gets that hot
DURING a countdown (5 C over the limit for a CPU alarm), LoopMedic
doesn't wait for the timer and powers off after 5 seconds.

The power-off is FORCED, like holding the power button: it does not wait
for apps, does not save open work, and skips "Working on updates" - the
point is to protect the hardware. Then CHECK YOUR PUMP before using the
PC hard again.

The power-off comes from the background watchdog, not the tray app - so
even if the tray icon was closed, the PC still powers off. Windows' own
shutdown timer is set as a backup too: that's the "You're about to be
signed out" box in the middle of the screen. Clicking its "Close" only
hides it - it doesn't cancel or speed anything up. [Cancel shutdown] in
LoopMedic's alarm cancels both.
If the tray app isn't running, a plain Windows message box with an OK
button shows the warning instead.

If the CPU is cool (under 60 C) when the pump stops, LoopMedic waits up
to 60 seconds instead of 10 (a cooler firmware update can stop the pump
briefly). As soon as the CPU warms past 60 C, the normal 10 seconds apply.

  "WHY DID MY PC TURN OFF?"
  If LoopMedic powered your PC off, it says so the next time you sign in:
  a notification "LoopMedic powered off this PC" with the time and the
  reason (it stays on screen until you dismiss it, then in the
  notification center), an orange line in the panel for 24 hours, event
  1050 in Event Viewer, and the full story in loopmedic.log.
  No notification = it wasn't LoopMedic (look in Event Viewer > System
  for event 6008 "unexpected shutdown" or "Kernel-Power" instead).


--------------------------------------------------------------------------
LAYERS OF PROTECTION (LoopMedic is only one)
--------------------------------------------------------------------------

Your PC already has safety layers. LoopMedic sits on top of them:

  1. THE CPU ITSELF. Every modern CPU slows down (throttles) when it
     reaches its maximum temperature, and cuts its own power if it goes
     far past it. This works even if Windows has frozen. It's the last
     line of defence - it stops the chip dying, but running hot over and
     over isn't good for it.

  2. YOUR MOTHERBOARD (BIOS). Worth five minutes in the BIOS (names vary
     by brand - look in the "Hardware Monitor", "Fan" or "Smart Fan"
     section):
       - Set the pump's header to full speed / "Pump" mode, so it never
         slows to a stop on its own.
       - Turn on the fan-fail warning for the pump header ("CPU Fan Fail
         Warning", "Fan Speed Low Limit"...). It beeps or warns at boot
         if the pump isn't spinning.
       - If your board has a "shutdown temperature" or "CPU over
         temperature protection" setting, turn it on.
     These work before Windows starts and while it's frozen.

  3. LOOPMEDIC. Watches from inside Windows, so it can warn you, count
     down, let you cancel, explain what happened, and keep a log. It
     only works while Windows is running.

  4. YOU. Glance at the shield now and then, listen to your pump, and
     keep your cooler maker's software and firmware up to date.

What LoopMedic can't do:
  - help while the PC is asleep, booting, or frozen solid (layers 1 + 2
    cover that)
  - see a pump that spins but moves no coolant (air lock, blockage,
    leak) - only the CPU temperature limit catches that
  - see a pump that reports its speed only over USB (temperature only)


--------------------------------------------------------------------------
BEFORE YOU UPDATE OR TINKER
--------------------------------------------------------------------------

Click the shield > Pause 30 min first if you are about to:
  * update your cooler's firmware (NZXT CAM, iCUE, Lian Li L-Connect...)
  * run fan calibration / auto-tuning (ASUS Fan Xpert, FanControl,
    MSI Center...) - these spin headers down to 0 on purpose
  * update the BIOS, or change fan curves in it

After any of these, plus big Windows feature updates, or installing /
removing other hardware-monitor apps (FanControl, HWiNFO, OpenRGB,
Armoury Crate...): check the shield is GREEN and the panel says "Live".
Orange "?" or a notification = run Install.bat again.

Updating LoopMedic itself: click the version number in the panel (see
THE TRAY ICON). By hand: unzip the new LoopMedic.zip and run Install.bat.


--------------------------------------------------------------------------
CHANGE SETTINGS / REMOVE
--------------------------------------------------------------------------

* Different pump or limit: run Install.bat again. (If nothing changed on
  the PC it offers to keep your current settings - handy after an update
  of LoopMedic itself.)

* Fine-tuning: edit config.json in this folder, then run Install.bat.
  The settings are:
    PumpStoppedRpm (150)     at or below this = stopped = power off
    PumpMinRpm (500)         below this = "slower than usual" warning
    PumpFailSeconds (10)     how long the pump must be stopped
    CpuMaxTempC              your CPU limit. 0 or a silly value doesn't
                             turn it off - LoopMedic uses the table's
                             value for your CPU (or 95 C) instead.
    CpuHotSeconds (30)       how long the CPU must stay at the limit
    ShutdownDelaySeconds (30)      the alarm's countdown
    HotShutdownDelaySeconds (5)    the countdown when already too hot

* Remove completely: double-click Uninstall.bat.

  !! MOVING THE PUMP CABLE TO ANOTHER HEADER?
  !! Run Install.bat again afterwards. Until then LoopMedic sees 0 rpm,
  !! shows DEGRADED and only watches the CPU temperature.

  !! NEW CPU?
  !! LoopMedic notices, switches to the table's limit for the new chip,
  !! and shows DEGRADED until you run Install.bat again to confirm.


--------------------------------------------------------------------------
WHAT'S IN THIS FOLDER
--------------------------------------------------------------------------

  Install.bat              Guided setup + install (run this)
  Uninstall.bat            Removes LoopMedic (and the tray icon)
  Watch-Pump.bat           Live fan speeds, to find your pump
  LoopMedic.exe            The watchdog + its power-off helper
  LoopMedic.exe.config     Tells it where the sensor library is
  LoopMedicTray.exe        The tray icon app (installed for you)
  Setup-LoopMedic.ps1      \
  Install-LoopMedic.ps1     > The scripts the .bat files run
  Watch-Pump.ps1           /
  config.json              Your settings (written by setup)
  cpu-limits.json          The CPU temperature table
  lhm\                     LibreHardwareMonitor sensor library
                           (see THIRD-PARTY-NOTICES.txt)
  pawnio\                  Official PawnIO driver installer + its license
  LICENSE.md               LoopMedic's license (free for personal,
                           non-commercial use)
  README.txt               This file

Once installed, the running copy lives in C:\ProgramData\LoopMedic
(admin-only), and you can delete or move this folder. Keep it if you
want to change settings or uninstall later.
