#!/usr/bin/env python3
"""
LoopMedic for Linux - guided setup. by Jacob Stiltner, (c) 2026
Start it with ./install.sh (as your normal user; it asks for sudo when needed).

Steps: tray dependency -> motherboard sensors (adds the nct6683 'force=1'
setting if the fans are hidden) -> pick the pump (live view) -> CPU limit ->
safe dry-run test -> install the service + tray.

  setup_loopmedic.py            guided setup + install
  setup_loopmedic.py --watch    live fan speeds only (find your pump)
  setup_loopmedic.py --uninstall
"""
import argparse
import getpass
import grp
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import time

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, HERE)
import loopmedic as pg  # noqa: E402

OPT = "/opt/loopmedic"
ETC_CFG = "/etc/loopmedic/config.json"
CONTROL = "/var/lib/loopmedic/control"
UNIT = "/etc/systemd/system/loopmedic.service"
MODPROBE_CONF = "/etc/modprobe.d/loopmedic-nct6683.conf"
MODLOAD_CONF = "/etc/modules-load.d/loopmedic.conf"
HOME = os.path.expanduser("~")
AUTOSTART = os.path.join(HOME, ".config", "autostart", "loopmedic-tray.desktop")
APPENTRY = os.path.join(HOME, ".local", "share", "applications", "loopmedic-tray.desktop")

C = {"cyan": "\033[96m", "green": "\033[92m", "yellow": "\033[93m", "red": "\033[91m", "dim": "\033[90m", "bold": "\033[1m", "x": "\033[0m"}


def say(text="", color=None):
    print(f"{C[color]}{text}{C['x']}" if color else text, flush=True)


def step(t):
    say()
    say(f"=== {t} ===", "cyan")


def yes_no(q):
    while True:
        a = input(f"{q} [y/n] ").strip().lower()
        if a in ("y", "yes"):
            return True
        if a in ("n", "no"):
            return False


def sudo(args, input_text=None, check=True):
    # already root (the tray's update runs this through pkexec): run directly
    r = subprocess.run(args if os.geteuid() == 0 else ["sudo"] + args, input=input_text, text=True)
    if check and r.returncode != 0:
        raise RuntimeError(f"'sudo {' '.join(args)}' failed (code {r.returncode})")
    return r.returncode


def sudo_write(path, text, mode="644"):
    sudo(["install", "-d", "-m", "755", os.path.dirname(path)])
    tmp = tempfile.NamedTemporaryFile("w", delete=False, suffix=".loopmedic")
    tmp.write(text)
    tmp.close()
    try:
        sudo(["install", "-m", mode, tmp.name, path])
    finally:
        os.unlink(tmp.name)


def board_fans():
    return pg.fan_sensors(pg.scan_sensors())


def module_loaded(name):
    return os.path.isdir(f"/sys/module/{name}")


# ------------------------------------------------------------------ live view
def watch(seconds=None):
    fans = board_fans()
    if not fans:
        say("No motherboard fan sensors found yet.", "yellow")
        return
    say("Live fan speeds - change your pump/fan speed now and watch which line moves.", "bold")
    say("Press Ctrl+C to stop." if seconds is None else f"(watching for {seconds} s, Ctrl+C to stop early)", "dim")
    first = {f.id: f.read() for f in fans}
    end = None if seconds is None else time.time() + seconds
    try:
        drawn = False
        while end is None or time.time() < end:
            if drawn:
                sys.stdout.write(f"\033[{len(fans)}F")
            for i, f in enumerate(fans, 1):
                v = f.read()
                d = (v or 0) - (first[f.id] or 0)
                mark = f"   {'+' if d > 0 else ''}{d:,.0f} since start" if abs(d) >= 80 else ""
                sys.stdout.write(f"\033[2K  {i:>2}. {f.label:<22} {v if v is not None else 0:>7,.0f} rpm{mark}\n")
            sys.stdout.flush()
            drawn = True
            time.sleep(1)
    except KeyboardInterrupt:
        print()


# ------------------------------------------------------------------ steps
def ensure_tray_deps():
    try:
        import PySide6  # noqa: F401
        say("PySide6 is installed (needed for the tray icon only).", "green")
        return True
    except ImportError:
        pass
    say("The tray icon needs PySide6 (Qt for Python). The protection itself works without it.")
    if shutil.which("pacman"):
        if yes_no("Install it now with: sudo pacman -S --needed pyside6 ?"):
            if sudo(["pacman", "-S", "--needed", "--noconfirm", "pyside6"], check=False) == 0:
                say("PySide6 installed.", "green")
                return True
            say("pacman couldn't install it - the tray will be skipped (run install.sh again later).", "yellow")
    else:
        say("Install your distro's PySide6 package (e.g. python3-pyside6), then run install.sh again.", "yellow")
    return False


def ensure_sensors():
    """Returns the board fans; tries the MSI/Nuvoton 'force=1' fix if none show up."""
    fans = board_fans()
    if fans:
        say(f"Found {len(fans)} fan sensor(s) on the motherboard.", "green")
        return fans
    say("No motherboard fan sensors are visible yet.", "yellow")
    say("Newer MSI boards (and some ASRock/others) use a Nuvoton NCT6687 chip. Linux's nct6683")
    say("driver reads it, but only switches itself on for Intel-made boards unless told 'force=1'.")
    say("LoopMedic only READS from it (it never changes a fan speed).")
    if yes_no("Try enabling it (sudo modprobe nct6683 force=1)?"):
        if module_loaded("nct6683"):
            sudo(["modprobe", "-r", "nct6683"], check=False)
        sudo(["modprobe", "nct6683", "force=1"], check=False)
        time.sleep(2)
        fans = board_fans()
        if fans:
            say(f"That worked: {len(fans)} fan sensor(s). Making it permanent...", "green")
            sudo_write(MODPROBE_CONF, "# added by LoopMedic setup - lets nct6683 read MSI/Nuvoton fan sensors (read-only use)\noptions nct6683 force=1\n")
            sudo_write(MODLOAD_CONF, "# added by LoopMedic setup\nnct6683\n")
            return fans
        sudo(["modprobe", "-r", "nct6683"], check=False)
        say("Still no fans with nct6683 - undid it.", "yellow")
    say("Other things to try later: 'sudo sensors-detect' (lm_sensors package), or for Gigabyte")
    say("boards the it87 driver from the AUR (it87-dkms-git). Then run install.sh again.")
    return []


LIMITS = os.path.join(HERE, "cpu-limits.json")


def chip_table():
    try:
        with open(LIMITS, encoding="utf-8") as f:
            return json.load(f)
    except (OSError, ValueError):
        return {"fallbackTripC": 95, "chips": []}


def describe_chip(e):
    tj = float(e.get("tjmax", 95))
    if e.get("runsAtLimit"):
        hs = float(e.get("hotSeconds") or 30)
        return (tj, hs,
                f"{e['family']}: max {tj:g} C. These CPUs are DESIGNED to sit at {tj:g} C under heavy load, so the trip is "
                f"the limit itself held for {hs:g} s. The pump check is the main protection on this chip.")
    return (tj - 5, 30.0, f"{e['family']}: max {tj:g} C. Normal heavy load stays below that, so {tj - 5:g} C held for 30 s means cooling has failed.")


def suggest_limit(cpu):
    """(trip C, hot seconds, explanation) from cpu-limits.json - the same table the service uses."""
    fallback, table = pg.load_cpu_limits(LIMITS)
    for rx, e in table:
        if cpu and rx.search(cpu):
            return describe_chip(e)
    return (fallback, 30.0, f"This CPU isn't in LoopMedic's table, so it uses the safe default of {fallback:g} C. "
                            "If you know your CPU's max temperature (Tjmax), pick its family from the list or type a limit ~5 C below it.")


def choose_limit(cpu):
    trip, hot_s, why = suggest_limit(cpu)
    say(f"Suggested: power off if the CPU stays at {trip:g} C or more for {hot_s:g} s.", "bold")
    say(why)
    say("The temperature check can't be turned off - it's the backup if the pump reading ever fails.", "dim")
    while True:
        ans = input(f"Enter = {trip:g} C, a number (60-105), or 'l' to pick your CPU family from a list: ").strip().lower()
        if not ans:
            return trip, hot_s
        if ans.isdigit() and 60 <= int(ans) <= 105:
            return float(ans), hot_s
        if ans == "l":
            chips = chip_table().get("chips", [])
            for i, e in enumerate(chips, 1):
                t, hs, _ = describe_chip(e)
                say(f"  {i:>2}. {e['family']:<28} max {e['tjmax']:>3} C  -> trip {t:g} C for {hs:g} s")
            pick = input("Number (Enter = back): ").strip()
            if pick.isdigit() and 1 <= int(pick) <= len(chips):
                t, hs, why = describe_chip(chips[int(pick) - 1])
                say(why)
                return t, hs


def likely_pump(fans):
    """Best guess at the pump header: a 'pump' label, else the only header spinning at pump speeds."""
    readings = [(f, f.read() or 0) for f in fans]
    named = [f for f, v in readings if re.search(r"pump|aio", f.label, re.I) and v >= 500]
    if len(named) == 1:
        return named[0]
    in_range = [f for f, v in readings if 1500 <= v <= 4500]
    return in_range[0] if len(in_range) == 1 else None


def pick_pump(fans):
    if not fans:
        return None
    guess = likely_pump(fans)
    if not any(1000 <= (f.read() or 0) <= 5000 for f in fans):
        say("None of the motherboard headers is spinning at pump speeds (1,000-5,000 rpm).", "yellow")
        say("Some AIO coolers only report the pump speed over USB to their own app, with no speed wire")
        say("to the motherboard. If yours is one of them, choose 0: LoopMedic then protects with the CPU")
        say("temperature only (still a real safety net, just a slower one).")
    while True:
        say("Fan sensors on your motherboard right now:")
        for i, f in enumerate(fans, 1):
            v = f.read() or 0
            hint = f"   {C['green']}<- looks like the pump{C['x']}" if f is guess else ""
            say(f"  {i:>2}. {f.label:<22} {v:>7,.0f} rpm   {C['dim']}[{f.chip} {f.chan}]{C['x']}{hint}")
        say("   0. My pump is not on a motherboard header (temperature-only protection)")
        say("   w. Watch them live for 20 s (change the pump speed while watching)")
        say()
        say("How to spot the pump:", "bold")
        say(" - The AIO's pump-speed cable usually goes to a header labelled PUMP_FAN1 / AIO_PUMP / CPU_FAN.")
        say(" - Pumps usually spin at 1,500-4,000 rpm. Entries reading 0 are empty headers.")
        default = str(fans.index(guess) + 1) if guess else ""
        a = input(f"Which number is your pump?{' (Enter = ' + default + ')' if default else ''} ").strip().lower()
        if not a and default:
            a = default
        if a == "w":
            watch(20)
            continue
        if not a.isdigit() or int(a) > len(fans):
            continue
        n = int(a)
        if n == 0:
            return None
        cand = fans[n - 1]
        v = cand.read() or 0
        if v < 500:
            say("That sensor reads under 500 rpm right now. Pick the one your pump is on.", "yellow")
            continue
        if v < 1000:
            say("Note: that is slow for a pump. Double-check it is not a case fan.", "yellow")
        return cand


def dry_run_test(cfg_path, has_pump):
    guard = os.path.join(HERE, "loopmedic.py")
    with tempfile.TemporaryDirectory(prefix="loopmedic-test-") as tmp:
        ctl = os.path.join(tmp, "control")
        os.makedirs(ctl)
        logf = os.path.join(tmp, "test.log")
        base = [sys.executable, guard, "--config", cfg_path, "--cpu-limits", LIMITS, "--state-dir", tmp, "--data-dir", tmp,
                "--control-dir", ctl, "--log", logf]
        if not has_pump:
            subprocess.run(base + ["--once"])
            return True
        say("Pretending the pump spins, then stops (nothing will power off). About 70 seconds...")
        p = subprocess.Popen(base + ["--dry-run", "--simulate-pump", "0", "--simulate-healthy", "22", "--duration", "70"],
                             stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True)
        for line in p.stdout:
            say("  " + re.sub(r"^<[0-7]>", "", line.rstrip()), "dim")
        p.wait()
        text = open(logf, encoding="utf-8").read() if os.path.exists(logf) else ""
        return "DRY RUN - trip" in text and "WOULD POWER OFF NOW" in text


def unit_text():
    return f"""[Unit]
Description=LoopMedic - powers off if the AIO pump stops or the CPU overheats (by Jacob Stiltner)
After=systemd-modules-load.service

StartLimitIntervalSec=0

[Service]
# notify + WatchdogSec: systemd restarts the service if it stops checking in for 30 s (hung),
# and Restart=always brings it back 1 s after any exit.
Type=notify
NotifyAccess=main
WatchdogSec=30
ExecStart=/usr/bin/python3 {OPT}/loopmedic.py
Restart=always
RestartSec=1
# journal lines tagged "loopmedic"; a <N> prefix on a line sets its priority (journalctl -t loopmedic -p warning)
SyslogIdentifier=loopmedic
SyslogLevelPrefix=yes
RuntimeDirectory=loopmedic
RuntimeDirectoryMode=0755
LogsDirectory=loopmedic
LogsDirectoryMode=0755
Nice=-5
ProtectHome=yes
PrivateTmp=yes

[Install]
WantedBy=multi-user.target
"""


def desktop_text():
    return f"""[Desktop Entry]
Type=Application
Name=LoopMedic
Comment=AIO pump / CPU temperature guard (tray icon) - by Jacob Stiltner
Exec=/usr/bin/python3 {OPT}/loopmedic_tray.py
Icon=security-high
Terminal=false
Categories=System;Monitor;
X-KDE-autostart-after=panel
"""


def start_tray():
    subprocess.Popen(["/usr/bin/python3", f"{OPT}/loopmedic_tray.py"], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                     start_new_session=True)


def stop_tray():
    # only our own tray process, matched by its exact script path
    subprocess.run(["pkill", "-u", getpass.getuser(), "-f", f"{OPT}/loopmedic_tray.py"], stderr=subprocess.DEVNULL)


OLD_NAME_CFG = "/etc/pumpguard/config.json"   # LoopMedic was called PumpGuard up to v2.2


def remove_pumpguard(keep_records=True):
    """Remove an install made under the old name, so two watchdogs never run side by side."""
    if not (os.path.exists("/etc/systemd/system/pumpguard.service") or os.path.isdir("/opt/pumpguard")):
        return
    subprocess.run(["pkill", "-u", getpass.getuser(), "-f", "/opt/pumpguard/pumpguard_tray.py"], stderr=subprocess.DEVNULL)
    sudo(["systemctl", "disable", "--now", "pumpguard.service"], check=False)
    sudo(["systemctl", "stop", "pumpguard-poweroff.timer", "pumpguard-poweroff.service"], check=False)
    sudo(["rm", "-f", "/etc/systemd/system/pumpguard.service"], check=False)
    sudo(["systemctl", "daemon-reload"], check=False)
    if keep_records:
        sudo(["install", "-d", "-m", "755", "/var/lib/loopmedic", "/var/log/loopmedic"], check=False)
        for f in ("selftest.json", "last-poweroff.json"):
            if os.path.exists(f"/var/lib/pumpguard/{f}") and not os.path.exists(f"/var/lib/loopmedic/{f}"):
                sudo(["cp", f"/var/lib/pumpguard/{f}", "/var/lib/loopmedic/"], check=False)
        if os.path.exists("/var/log/pumpguard/pumpguard.log"):
            sudo(["cp", "/var/log/pumpguard/pumpguard.log", "/var/log/loopmedic/pumpguard-before-rename.log"], check=False)
    # the sensor setting keeps working; just give it the new name
    if os.path.exists("/etc/modprobe.d/pumpguard-nct6683.conf") and not os.path.exists(MODPROBE_CONF):
        sudo(["mv", "/etc/modprobe.d/pumpguard-nct6683.conf", MODPROBE_CONF], check=False)
        sudo(["mv", "/etc/modules-load.d/pumpguard.conf", MODLOAD_CONF], check=False)
    sudo(["rm", "-rf", "/opt/pumpguard", "/etc/pumpguard", "/var/lib/pumpguard", "/var/log/pumpguard"], check=False)
    for p in (os.path.join(HOME, ".config", "autostart", "pumpguard-tray.desktop"),
              os.path.join(HOME, ".local", "share", "applications", "pumpguard-tray.desktop")):
        if os.path.exists(p):
            os.remove(p)
    say("Removed the old PumpGuard install (LoopMedic replaces it).", "green")


def install(cfg_path, with_tray):
    user = getpass.getuser()
    group = grp.getgrgid(os.getgid()).gr_name
    stop_tray()
    remove_pumpguard()
    sudo(["systemctl", "stop", "loopmedic.service"], check=False)
    sudo(["install", "-d", "-m", "755", OPT, os.path.dirname(ETC_CFG), "/var/lib/loopmedic"])
    sudo(["install", "-m", "644", os.path.join(HERE, "loopmedic.py"), os.path.join(HERE, "loopmedic_tray.py"), LIMITS, OPT])
    sudo(["install", "-m", "644", cfg_path, ETC_CFG])
    # control/ is the one folder the tray (you) may write: pause/test/cancel requests. The root
    # service only reads dates out of it, refuses symlinks and anything over 4 KB.
    sudo(["install", "-d", "-m", "755", "-o", user, "-g", group, CONTROL])
    sudo_write(UNIT, unit_text())
    sudo(["systemctl", "daemon-reload"])
    sudo(["systemctl", "enable", "--now", "loopmedic.service"])
    if with_tray:
        for p in (AUTOSTART, APPENTRY):
            os.makedirs(os.path.dirname(p), exist_ok=True)
            with open(p, "w", encoding="utf-8") as f:
                f.write(desktop_text())
        start_tray()
    time.sleep(4)
    subprocess.run(["systemctl", "--no-pager", "--lines=0", "status", "loopmedic.service"])
    say()
    say("Latest log lines:", "bold")
    subprocess.run(["tail", "-n", "5", "/var/log/loopmedic/loopmedic.log"])


def update_as_root(user, group):
    """The tray's "update now": started through pkexec after the new version's signature checked out.
    Keeps the settings in /etc/loopmedic/config.json (adds any new setting with its default), installs the
    new files and restarts the service. The tray restarts itself afterwards."""
    if os.geteuid() != 0:
        raise RuntimeError("--update-root must run as root (the tray starts it through pkexec)")
    if not os.path.exists(ETC_CFG):
        raise RuntimeError("LoopMedic isn't installed here - run install.sh instead")
    mine = json.load(open(ETC_CFG, encoding="utf-8"))
    defaults = json.load(open(os.path.join(ROOT, "config.json"), encoding="utf-8"))
    for k, v in defaults.items():
        if k not in mine:
            mine[k] = v
            say(f"new setting: {k} = {v}")
    tmp = tempfile.NamedTemporaryFile("w", delete=False, suffix=".json")
    json.dump(mine, tmp, indent=2)
    tmp.close()
    sudo(["systemctl", "stop", "loopmedic.service"], check=False)
    sudo(["install", "-m", "644", os.path.join(HERE, "loopmedic.py"), os.path.join(HERE, "loopmedic_tray.py"), LIMITS, OPT])
    sudo(["install", "-m", "644", tmp.name, ETC_CFG])
    os.remove(tmp.name)
    sudo(["install", "-d", "-m", "755", "-o", user, "-g", group, CONTROL])
    sudo_write(UNIT, unit_text())
    sudo(["systemctl", "daemon-reload"])
    sudo(["systemctl", "enable", "--now", "loopmedic.service"])
    sudo(["systemctl", "restart", "loopmedic.service"])
    say(f"LoopMedic updated to {pg.VERSION}.", "green")


def uninstall():
    step("Removing LoopMedic")
    stop_tray()
    remove_pumpguard(keep_records=False)
    sudo(["systemctl", "disable", "--now", "loopmedic.service"], check=False)
    sudo(["systemctl", "stop", f"{pg.TIMER_UNIT}.timer", f"{pg.TIMER_UNIT}.service"], check=False)
    sudo(["rm", "-f", UNIT], check=False)
    sudo(["systemctl", "daemon-reload"], check=False)
    sudo(["rm", "-rf", OPT, os.path.dirname(ETC_CFG), "/var/lib/loopmedic", "/var/log/loopmedic"], check=False)
    for p in (AUTOSTART, APPENTRY):
        if os.path.exists(p):
            os.remove(p)
    if os.path.exists(MODPROBE_CONF):
        if yes_no("Also remove the 'nct6683 force=1' sensor setting LoopMedic added? (other monitor apps may use it)"):
            sudo(["rm", "-f", MODPROBE_CONF, MODLOAD_CONF], check=False)
    say("LoopMedic removed.", "green")


# ------------------------------------------------------------------ main
def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--watch", action="store_true")
    ap.add_argument("--uninstall", action="store_true")
    ap.add_argument("--update-root", action="store_true", help=argparse.SUPPRESS)   # the tray's updater (via pkexec)
    ap.add_argument("--user", help=argparse.SUPPRESS)
    ap.add_argument("--group", help=argparse.SUPPRESS)
    a = ap.parse_args()

    if a.update_root:
        if not (a.user and a.group and re.fullmatch(r"[A-Za-z0-9._-]+", a.user) and re.fullmatch(r"[A-Za-z0-9._-]+", a.group)):
            say("--update-root needs --user and --group", "red")
            return 2
        update_as_root(a.user, a.group)
        return 0

    if a.watch:
        watch()
        return 0
    if os.geteuid() == 0:
        say("Please run ./install.sh as your normal user (not with sudo) - it asks for sudo itself.", "red")
        return 1
    if a.uninstall:
        uninstall()
        return 0

    say("LoopMedic setup: powers the PC off if the AIO pump stops or the CPU overheats.", "bold")
    say("by Jacob Stiltner", "dim")
    if not shutil.which("systemctl"):
        say("This needs systemd (systemctl). Setup stopped.", "red")
        return 1

    step("1/5  Tray icon support")
    with_tray = ensure_tray_deps()

    step("2/5  Motherboard sensors")
    say(f"Motherboard: {pg.board_name() or 'unknown'}")
    cpu = pg.cpu_model()
    say(f"CPU:         {cpu or 'unknown'}")
    fans = ensure_sensors()
    sensors = pg.scan_sensors()
    temp = pg.find_cpu_temp(sensors)

    # Already installed with settings that still fit this PC? Offer to keep them.
    keep, pump, max_temp = False, None, -1
    old_cfg = ETC_CFG if os.path.exists(ETC_CFG) else OLD_NAME_CFG
    if os.path.exists(old_cfg):
        try:
            old = json.load(open(old_cfg, encoding="utf-8"))
            old_pump = next((f for f in fans if f.id == old.get("PumpSensorId")), None) if old.get("PumpSensorId") else None
            pump_ok = not old.get("PumpSensorId") or (old_pump and (old_pump.read() or 0) >= 500)
            cpu_ok = not old.get("CpuName") or old.get("CpuName") == cpu
            if pump_ok and cpu_ok and (old_pump or float(old.get("CpuMaxTempC") or 0) > 0):
                say("LoopMedic is already installed with these settings:", "bold")
                say(f"  Pump: {old_pump.label + ' (reading %s rpm now)' % format(old_pump.read() or 0, ',.0f') if old_pump else 'none (temperature only)'}")
                old_limit = float(old.get("CpuMaxTempC") or 0)
                say(f"  CPU limit: {'%g C' % old_limit if old_limit > 0 else 'off (no longer allowed - you will pick a limit)'}")
                if yes_no("Keep these settings?"):
                    keep, pump, max_temp = True, old_pump, old_limit
            elif not cpu_ok:
                say(f"Your CPU changed ({old.get('CpuName')} -> {cpu}), so let's pick the settings again.", "yellow")
            else:
                say("Your saved pump sensor is missing or not spinning, so let's pick it again.", "yellow")
        except (OSError, ValueError):
            pass

    step("3/5  Finding your pump")
    if keep:
        say(f"Keeping: {pump.label if pump else 'no pump (temperature only)'}")
    else:
        pump = pick_pump(fans)

    step("4/5  CPU temperature limit")
    hot_s = None
    if not temp:
        say("No CPU temperature sensor found (k10temp/coretemp) - the temperature backup can't work.", "yellow")
        max_temp = 0
    elif keep and max_temp > 0:
        say(f"Keeping the CPU limit: {max_temp:g} C")
    else:
        say(f"CPU temperature now: {temp.read()} C  ({temp.label})")
        max_temp, hot_s = choose_limit(cpu)
    if not pump and not temp:
        say("No pump sensor and no CPU temperature sensor: there is nothing to watch. Setup stopped.", "red")
        return 1

    cfg_path = os.path.join(ROOT, "config.json")
    cfg = json.load(open(cfg_path, encoding="utf-8"))
    cfg["PumpSensorId"] = pump.id if pump else ""
    cfg["CpuTempSensorId"] = temp.id if temp else ""
    cfg["CpuName"] = cpu           # remembered so LoopMedic notices a CPU swap
    cfg["CpuMaxTempC"] = max_temp
    if hot_s:
        cfg["CpuHotSeconds"] = hot_s
    with open(cfg_path, "w", encoding="utf-8") as f:
        json.dump(cfg, f, indent=2)
    say(f"Saved: pump = {pump.label + ' [' + pump.id + ']' if pump else 'none (temperature only)'}, CPU limit = {'%g C' % max_temp if max_temp > 0 else 'none'}", "green")

    step("5/5  Safe test, then install")
    if not dry_run_test(cfg_path, pump is not None):
        say("TEST FAILED: LoopMedic did not react. Not installing. Send the lines above to whoever gave you this.", "red")
        return 1
    if pump:
        say("TEST PASSED: LoopMedic reacted to a dead pump (and would have powered off).", "green")
    say()
    if not yes_no("Install LoopMedic (service + tray icon) so it runs at every boot?"):
        say("Not installed. Run ./install.sh again whenever you are ready.")
        return 0
    install(cfg_path, with_tray)
    say()
    say("Done. LoopMedic is running.", "green")
    if with_tray:
        say("Look for the green shield in the system tray (it may be in the ^ overflow area).")
        say("Click it for live pump / CPU / fan readings; right-click > Run test to see the alarm pop-up.")
    say("Terminal:  systemctl status loopmedic    journalctl -u loopmedic -f")
    say("Log file:  /var/log/loopmedic/loopmedic.log")
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except KeyboardInterrupt:
        print()
        sys.exit(130)
    except RuntimeError as e:
        say(f"ERROR: {e}", "red")
        sys.exit(1)
