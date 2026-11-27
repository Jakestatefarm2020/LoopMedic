#!/usr/bin/env python3
"""
LoopMedic for Linux - by Jacob Stiltner, (c) 2026

Powers the PC off if the AIO pump stops or the CPU overheats.

Runs as root from systemd (loopmedic.service). Reads fan speeds and the CPU
temperature straight from the kernel (/sys/class/hwmon) - no extra driver.
Only the Python standard library is used, so protection never depends on the
tray app or any package.

Every poll it writes /run/loopmedic/status.json (read by the tray app) and reads
small request files from /var/lib/loopmedic/control (pause / test / cancel /
poweroff / tray heartbeat). Those are only ever parsed as dates - never run.

On a trip it arms a systemd timer (loopmedic-poweroff.timer) that forces a
power-off when the countdown ends. The timer is its own unit, so it fires even
if this program crashes. A countdown thread here is the backup for the timer.
systemd restarts this program within a second if it exits, and its watchdog
(WatchdogSec) restarts it if it stops checking in for 30 s.

Important events also go to the system journal with a priority
(journalctl -t loopmedic -p warning) - the Linux side of Windows' Event Viewer.

Test switches:
  --dry-run               never power off; log what would happen
  --once                  print the sensors once and exit
  --simulate-pump N       pretend the pump reads N rpm
  --simulate-healthy S    ...but read a healthy 2400 rpm for the first S seconds
  --simulate-stall N      freeze one check for N extra seconds
  --duration N            stop after N seconds
  --selftest-interval-hours H, --selftest-min-uptime S, --fake-selftest-fail FILE
"""
import argparse
import glob
import json
import os
import re
import shutil
import socket
import stat
import subprocess
import sys
import threading
import time
from datetime import datetime, timedelta

VERSION = "2.2.0"
GPU_CHIPS = {"amdgpu", "radeon", "nouveau", "nvidia", "i915", "xe"}
CPU_CHIPS = ("k10temp", "zenpower", "coretemp")
TIMER_UNIT = "loopmedic-poweroff"
# Flush what we can (max 5 s), then power off at once without stopping services:
# like holding the power button, minus most of the data-loss risk.
POWEROFF_CMD = "timeout 5 sync; systemctl poweroff -ff || echo o > /proc/sysrq-trigger"


# ----------------------------------------------------------------- helpers
def natural_key(s):
    return [int(t) if t.isdigit() else t for t in re.split(r"(\d+)", s)]


def read_text(path):
    try:
        with open(path, "r", encoding="utf-8", errors="replace") as f:
            return f.read().strip()
    except OSError:
        return None


def now_iso():
    return datetime.now().astimezone().isoformat()


def to_iso(dt):
    return dt.astimezone().isoformat() if dt else None


def parse_when(s):
    """ISO-8601 date -> naive local datetime, or None. Accepts .NET ('o') and Python styles."""
    if not isinstance(s, str):
        return None
    m = re.match(r"^\s*(\d{4}-\d{2}-\d{2}[T ]\d{2}:\d{2}:\d{2})(\.\d+)?(Z|[+-]\d{2}:?\d{2})?\s*$", s)
    if not m:
        return None
    base, frac, tz = m.groups()
    txt = base.replace(" ", "T")
    if frac:
        txt += "." + (frac[1:] + "000000")[:6]
    if tz:
        txt += "+00:00" if tz == "Z" else (tz if ":" in tz else tz[:3] + ":" + tz[3:])
    try:
        dt = datetime.fromisoformat(txt)
    except ValueError:
        return None
    if dt.tzinfo is not None:
        dt = dt.astimezone().replace(tzinfo=None)
    return dt


# ----------------------------------------------------------------- sensors
class Sensor:
    def __init__(self, sid, chip, kind, chan, label, path):
        self.id, self.chip, self.kind, self.chan, self.label, self.path = sid, chip, kind, chan, label, path

    def read(self):
        v = read_text(self.path)
        if v is None:
            return None
        try:
            x = float(v)
        except ValueError:
            return None
        if self.kind == "temp":
            x /= 1000.0
        return round(x, 1)


def scan_sensors(sysfs="/sys"):
    """Every fan and temperature the kernel exposes. IDs use the chip's device path,
    which stays the same across boots (the hwmonN numbers do not)."""
    out = []
    for d in sorted(glob.glob(os.path.join(sysfs, "class", "hwmon", "hwmon*")), key=natural_key):
        chip = read_text(os.path.join(d, "name")) or "unknown"
        real = os.path.realpath(d).replace("\\", "/")
        dev = re.sub(r"/(hwmon/)?hwmon\d+$", "", real)
        if not dev or dev == real or dev.endswith("/class"):
            dev = chip
        for kind, pattern in (("fan", "fan*_input"), ("temp", "temp*_input")):
            for p in sorted(glob.glob(os.path.join(d, pattern)), key=natural_key):
                chan = os.path.basename(p)[: -len("_input")]
                n = re.sub(r"\D", "", chan)
                label = read_text(os.path.join(d, chan + "_label"))
                if not label:
                    label = f"Fan #{n}" if kind == "fan" else f"{chip} temp{n}"
                out.append(Sensor(f"{dev}|{chan}", chip, kind, chan, label, p))
    return out


def fan_sensors(sensors):
    return [s for s in sensors if s.kind == "fan" and s.chip not in GPU_CHIPS]


def find_cpu_temp(sensors):
    for chip, label in (("k10temp", "Tctl"), ("zenpower", "Tctl"), ("k10temp", "Tdie"), ("coretemp", "Package id 0")):
        for s in sensors:
            if s.kind == "temp" and s.chip == chip and s.label == label:
                return s
    for s in sensors:
        if s.kind == "temp" and s.chip in CPU_CHIPS:
            return s
    return None


def cpu_model(proc="/proc"):
    txt = read_text(os.path.join(proc, "cpuinfo")) or ""
    m = re.search(r"^model name\s*:\s*(.+)$", txt, re.M)
    return m.group(1).strip() if m else ""


def board_name(sysfs="/sys"):
    v = read_text(os.path.join(sysfs, "class", "dmi", "id", "board_vendor")) or ""
    n = read_text(os.path.join(sysfs, "class", "dmi", "id", "board_name")) or ""
    return (v + " " + n).strip()


# ----------------------------------------------------------------- plumbing
class Log:
    def __init__(self, path):
        self.path = path
        if path:
            try:
                os.makedirs(os.path.dirname(path), exist_ok=True)
            except OSError:
                pass

    def __call__(self, text, prio=None):
        """prio = syslog level for the journal (2 crit, 3 err, 4 warning, 6 info); None = normal line."""
        line = f"{datetime.now():%Y-%m-%d %H:%M:%S}  {text}"
        print((f"<{prio}>" if prio is not None else "") + line, flush=True)
        if not self.path:
            return
        try:
            if os.path.exists(self.path) and os.path.getsize(self.path) > 1_000_000:
                os.replace(self.path, self.path + ".old")
            with open(self.path, "a", encoding="utf-8") as f:
                f.write(line + "\n")
            os.chmod(self.path, 0o644)
        except OSError:
            pass


def write_status(path, st):
    tmp = path + ".tmp"
    try:
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(st, f, separators=(",", ":"))
        os.chmod(tmp, 0o644)
        os.replace(tmp, path)
    except OSError:
        pass


def read_control(ctl_dir, name, field):
    """(date, file write time) from a small JSON request file, or None.
    The control folder is user-writable: no symlinks, regular files only, max 4 KB, dates only."""
    flags = os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0) | getattr(os, "O_NONBLOCK", 0)
    try:
        fd = os.open(os.path.join(ctl_dir, name), flags)
    except OSError:
        return None
    try:
        st = os.fstat(fd)
        if not stat.S_ISREG(st.st_mode) or st.st_size > 4096:
            return None
        data = os.read(fd, 4096)
    except OSError:
        return None
    finally:
        os.close(fd)
    try:
        j = json.loads(data.decode("utf-8-sig"))
        val = parse_when(j.get(field)) if isinstance(j, dict) else None
    except (ValueError, UnicodeDecodeError):
        return None
    if val is None:
        return None
    return val, datetime.fromtimestamp(st.st_mtime)


def read_control_date(ctl_dir, name, field):
    r = read_control(ctl_dir, name, field)
    return r[0] if r else None


def load_cpu_limits(path):
    """cpu-limits.json -> (fallback trip C, [(compiled regex, entry dict)]). Missing file = fallback only."""
    try:
        with open(path, encoding="utf-8-sig") as f:
            d = json.load(f)
    except (OSError, ValueError):
        return 95.0, []
    table = []
    for e in d.get("chips") or []:
        try:
            table.append((re.compile(e["match"], re.I), e))
        except (KeyError, re.error, TypeError):
            pass
    return float(d.get("fallbackTripC") or 95), table


def cpu_limit_for(name, table):
    """(trip C, hot seconds or None, family) for a CPU name, or None if it isn't in the table."""
    for rx, e in table:
        if name and rx.search(name):
            tj = float(e.get("tjmax", 95))
            trip = tj if e.get("runsAtLimit") else tj - 5
            return trip, e.get("hotSeconds"), e.get("family", "?")
    return None


def sd_notify(msg):
    """Tell systemd we're alive (Type=notify + WatchdogSec). No-op outside systemd."""
    addr = os.environ.get("NOTIFY_SOCKET")
    if not addr:
        return
    if addr.startswith("@"):
        addr = "\0" + addr[1:]
    try:
        with socket.socket(socket.AF_UNIX, socket.SOCK_DGRAM) as s:
            s.connect(addr)
            s.sendall(msg.encode())
    except (OSError, AttributeError):
        pass


def write_json_durable(path, obj):
    """Small state file that must survive a hard power-off right after it's written."""
    tmp = path + ".tmp"
    try:
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(obj, f)
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp, path)
    except OSError:
        pass


def read_json(path):
    try:
        with open(path, encoding="utf-8-sig") as f:
            d = json.load(f)
        return d if isinstance(d, dict) else None
    except (OSError, ValueError):
        return None


def run_quiet(args):
    try:
        return subprocess.run(args, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE, timeout=10, text=True)
    except (OSError, subprocess.SubprocessError) as e:
        return subprocess.CompletedProcess(args, 1, "", str(e))


class PowerOff:
    """Countdown + forced power-off. Real trips: a systemd timer (survives a crash of this
    program) plus a countdown thread here as a backup. Dry runs and tests: the thread only logs."""

    def __init__(self, log):
        self.log = log
        self.ev = None
        self.dry = True

    def clear_timer(self):
        run_quiet(["systemctl", "stop", f"{TIMER_UNIT}.timer", f"{TIMER_UNIT}.service"])
        run_quiet(["systemctl", "reset-failed", f"{TIMER_UNIT}.timer", f"{TIMER_UNIT}.service"])

    def arm(self, delay, dry):
        if self.ev:
            self.ev.set()
        self.ev = threading.Event()
        self.dry = dry
        deadline = time.monotonic() + delay
        if not dry:
            self.clear_timer()
            r = run_quiet(["systemd-run", f"--unit={TIMER_UNIT}", f"--on-active={int(delay)}",
                           "--timer-property=AccuracySec=100ms", "--description=LoopMedic forced power-off",
                           "/bin/sh", "-c", POWEROFF_CMD])
            if r.returncode == 0:
                self.log(f"Power-off timer armed ({TIMER_UNIT}.timer, {int(delay)} s).")
            else:
                self.log(f"Couldn't arm the systemd power-off timer ({(r.stderr or '').strip()}) - the watchdog will power off itself.")
        else:
            self.log(f"[power-off] armed (DRY RUN): would power off in {int(delay)} s")
        threading.Thread(target=self._countdown, args=(self.ev, deadline, dry), daemon=True).start()

    def _countdown(self, ev, deadline, dry):
        wait = deadline - time.monotonic() + (0 if dry else 5)   # real trips: the timer goes first
        if ev.wait(max(0.0, wait)):
            return
        if dry:
            self.log("[power-off] DRY RUN - WOULD POWER OFF NOW (forced: sync, then immediate power-off).")
        else:
            self.now("the countdown ended and the PC is still on")

    def cancel(self):
        if self.ev:
            self.ev.set()
        if not self.dry:
            self.clear_timer()

    def now(self, why):
        if self.ev:
            self.ev.set()
        if self.dry:
            self.log(f"[power-off] DRY RUN - would power off now ({why}).")
            return
        self.log(f"POWERING OFF NOW ({why}).")
        try:
            subprocess.run(["/bin/sh", "-c", POWEROFF_CMD], timeout=30)
        except (OSError, subprocess.SubprocessError) as e:
            self.log(f"Power-off command failed: {e}")


# ----------------------------------------------------------------- main
def main(argv=None):
    ap = argparse.ArgumentParser(description="LoopMedic for Linux - powers off if the AIO pump stops or the CPU overheats.")
    ap.add_argument("--config", default="/etc/loopmedic/config.json")
    ap.add_argument("--cpu-limits", default=os.path.join(os.path.dirname(os.path.abspath(__file__)), "cpu-limits.json"))
    ap.add_argument("--state-dir", default="/run/loopmedic")
    ap.add_argument("--data-dir", default="/var/lib/loopmedic")
    ap.add_argument("--control-dir", default="/var/lib/loopmedic/control")
    ap.add_argument("--log", default="/var/log/loopmedic/loopmedic.log")
    ap.add_argument("--sysfs", default="/sys")
    ap.add_argument("--proc", default="/proc")
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--once", action="store_true")
    ap.add_argument("--simulate-pump", type=float, default=-1)
    ap.add_argument("--simulate-healthy", type=int, default=0)
    ap.add_argument("--duration", type=int, default=0)
    ap.add_argument("--simulate-stall", type=int, default=0, help="freeze one check for N extra seconds (tests the Stalled log line)")
    ap.add_argument("--selftest-interval-hours", type=float, default=24 * 30)
    ap.add_argument("--selftest-min-uptime", type=float, default=600)
    ap.add_argument("--fake-selftest-fail", default="", help="tests: if this file exists, the self-test fails")
    a = ap.parse_args(argv)

    log = Log(a.log)
    for d in (a.state_dir, a.data_dir):
        try:
            os.makedirs(d, exist_ok=True)
        except OSError:
            pass
    status_path = os.path.join(a.state_dir, "status.json")
    last_off_path = os.path.join(a.data_dir, "last-poweroff.json")
    selftest_path = os.path.join(a.data_dir, "selftest.json")
    test_mode = a.duration > 0 or a.once

    def hold_degraded(msgs, code):
        """Nothing can be watched: keep telling the tray why (it would otherwise just say Offline)."""
        log("NOT PROTECTING: " + " | ".join(msgs), 3)
        if test_mode:
            write_status(status_path, {"at": now_iso(), "state": "DEGRADED", "version": VERSION, "degraded": msgs, "fans": []})
            return code
        sd_notify("READY=1")
        while True:
            write_status(status_path, {"at": now_iso(), "state": "DEGRADED", "version": VERSION, "degraded": msgs, "fans": []})
            sd_notify("WATCHDOG=1")
            time.sleep(2)

    # ---- config
    try:
        with open(a.config, encoding="utf-8-sig") as f:
            cfg = json.load(f)
        if not isinstance(cfg, dict):
            raise ValueError("not a JSON object")
    except Exception as e:  # noqa: BLE001 - any problem here means "not protecting"
        log(f"ERROR: {a.config} can't be read ({e}). NOT PROTECTING. Re-run setup.")
        return hold_degraded(["config.json is broken - NOT PROTECTING. Re-run setup."], 4)

    def num(name, default):
        v = cfg.get(name)
        if v is None or isinstance(v, bool):
            return default
        try:
            return float(v)
        except (TypeError, ValueError):
            return default

    pump_min = num("PumpMinRpm", 500)                # below this = "slow" warning
    pump_stopped = num("PumpStoppedRpm", 150)        # at or below this = stopped -> trip
    pump_fail_s = num("PumpFailSeconds", 10)
    pump_fail_cool_s = num("PumpFailSecondsCool", 60)
    cool_cpu_c = num("CoolCpuTempC", 60)
    cpu_max = num("CpuMaxTempC", 0)
    cpu_hot_s = num("CpuHotSeconds", 30)
    poll_s = max(1.0, num("PollSeconds", 2))
    grace_s = num("StartupGraceSeconds", 20)
    delay_s = max(5, int(num("ShutdownDelaySeconds", 30)))
    hot_delay_s = max(2, int(num("HotShutdownDelaySeconds", 5)))
    rearm_s = num("RearmSeconds", 120)
    heartbeat_min = num("HeartbeatMinutes", 60)
    if pump_stopped >= pump_min:
        pump_stopped = max(0.0, pump_min - 1)
    # Air cooler mode: the CPU fan takes the pump's place. Same rules, only the wording changes.
    air = str(cfg.get("CoolerType") or "").strip().lower() == "air"
    Pw, pw = ("CPU fan", "CPU fan") if air else ("Pump", "pump")
    fallback_c, limits = load_cpu_limits(a.cpu_limits)

    # ---- sensors
    sensors = scan_sensors(a.sysfs)
    problems = []
    pump = None
    pump_id = str(cfg.get("PumpSensorId") or "")
    if pump_id:
        pump = next((s for s in sensors if s.id == pump_id), None)
        if not pump:
            log(f"WARNING: {pw} sensor '{pump_id}' not found - {pw} check OFF.")
            problems.append(f"{Pw} sensor not found (BIOS/driver/kernel change?) - {pw} NOT watched. Re-run setup.")
    else:
        log(f"No {pw} sensor in config.json (no speed wire on a motherboard header) - temperature-only protection.")

    cpu_temp = None
    temp_id = str(cfg.get("CpuTempSensorId") or "")
    if temp_id:
        cpu_temp = next((s for s in sensors if s.id == temp_id), None)
    if not cpu_temp:
        cpu_temp = find_cpu_temp(sensors)
    cpu_now = cpu_model(a.proc)
    if not cpu_temp:
        log("WARNING: no CPU temperature sensor found - temperature check OFF.")
        problems.append("CPU temperature sensor not found - temperature NOT watched.")

    # ---- temperature limit: can't be switched off any more
    known = cpu_limit_for(cpu_now, limits)
    cfg_cpu = str(cfg.get("CpuName") or "")
    if cfg_cpu and cpu_now and cpu_now != cfg_cpu:
        cpu_max = known[0] if known else fallback_c
        if known and known[1]:
            cpu_hot_s = float(known[1])
        src = f"{known[2]} table" if known else "fallback"
        log(f"WARNING: CPU changed ('{cfg_cpu}' -> '{cpu_now}') - temperature trip now {cpu_max:g} C ({src}). Re-run setup to confirm.")
        problems.append(f"CPU changed to {cpu_now} - temperature trip set to {cpu_max:g} C ({src}). Re-run setup to confirm.")
    elif cpu_max <= 0 or cpu_max > 110:
        cpu_max = known[0] if known else fallback_c
        if known and known[1]:
            cpu_hot_s = float(known[1])
        log(f"CpuMaxTempC was off or out of range - the temperature trip can't be turned off; using {cpu_max:g} C "
            f"({known[2] + ' table' if known else 'fallback'}).")
    temp_check = cpu_temp is not None

    fans = fan_sensors(sensors)
    simulating = a.simulate_pump >= 0

    if not pump and not temp_check and not simulating:
        log(f"ERROR: no {pw} sensor and no CPU temperature; nothing to guard.")
        return hold_degraded(problems + ["Nothing can be watched - NOT PROTECTING."], 2)

    # ---- start-up self-check
    t0 = cpu_temp.read() if cpu_temp else None
    if cpu_temp and (t0 is None or t0 < 10 or t0 > 100):
        problems.append(f"CPU temperature reads {t0 if t0 is not None else 'nothing'} - that looks wrong, the temperature trip may not work.")
    if not a.dry_run and not test_mode:
        if hasattr(os, "geteuid") and os.geteuid() != 0:
            problems.append("Not running as root - LoopMedic could NOT power off this PC. Reinstall it.")
        if not shutil.which("systemctl") or not shutil.which("systemd-run"):
            problems.append("systemctl / systemd-run not found - LoopMedic could NOT power off this PC.")
    log("Start-up self-check: " + ("OK (sensors present, CPU " + str(t0) + " C)." if not problems else " | ".join(problems)))

    def desc(s):
        return f"{s.label} [{s.id}]" if s else "none"

    mode = "DRY RUN" if a.dry_run else "ARMED"
    start_line = (f"LoopMedic {VERSION} (Linux) started ({mode}{', air cooler' if air else ''}). {Pw}: {desc(pump)}, trips if stopped (<= {pump_stopped:g} rpm) for "
                  f"{pump_fail_s:g}s ({pump_fail_cool_s:g}s while CPU < {cool_cpu_c:g} C), slow warning below {pump_min:g} rpm. "
                  f"CPU: {desc(cpu_temp)}, max {cpu_max:g} C for {cpu_hot_s:g}s. Fans: {len(fans)}. "
                  f"Power-off after {delay_s}s ({hot_delay_s}s if the CPU is already hot).")
    log(start_line, 6)

    if a.once:
        r = a.simulate_pump if simulating else (pump.read() if pump else None)
        log(f"{Pw} rpm: {r}   CPU temp: {cpu_temp.read() if cpu_temp else None} C")
        for f in fans:
            log(f"  {f.label:<20} {f.read()} rpm   [{f.id}]")
        return 0

    # ---- why did the PC turn off last time?
    last_off = read_json(last_off_path)
    if last_off and last_off.get("armed") and not last_off.get("reported"):
        log(f"Last power-off by LoopMedic: {last_off.get('at')} - {last_off.get('reason')}", 4)
        last_off["reported"] = True
        write_json_durable(last_off_path, last_off)
    elif last_off and not last_off.get("armed"):
        last_off = None

    sd_notify("READY=1")
    power = PowerOff(log)
    started = time.monotonic()
    pump_bad_since = hot_since = pump_null_since = temp_null_since = pump_slow_since = None
    pump_ever_ok = pump_slow_reported = False
    last_heartbeat = time.monotonic()
    cooldown_until = 0.0
    max_temp = min_rpm = max_rpm = None
    accepted = pending = None        # the pump reading the watchdog believes / a jump waiting for confirmation
    jump_hour, jump_count, glitches = -1, 0, 0
    stall_hour, stall_count, stall_sim_done = -1, 0, False
    last_check = time.monotonic()
    day = datetime.now().date()
    day_max_temp = day_min_pump = None
    trip = None                       # dict: kind, reason, started (wall), deadline_mono, hot_since, hot_sent
    last_test = last_cancel = last_poweroff = datetime.now()
    was_paused = False
    pause_end_mono, pause_mtime, pause_until_wall = 0.0, None, None
    last_degraded = ""
    selftest = read_json(selftest_path)
    selftest_problem = None
    if selftest and selftest.get("ok") is False:
        selftest_problem = f"Monthly self-test FAILED: {selftest.get('detail')}"

    def selftest_due(st):
        if not st or not parse_when(st.get("at")):
            return a.selftest_min_uptime
        interval = a.selftest_interval_hours * 3600 if st.get("ok") else min(24 * 3600, a.selftest_interval_hours * 3600)
        left = interval - (datetime.now() - parse_when(st.get("at"))).total_seconds()
        return max(a.selftest_min_uptime, left)

    next_selftest = started + selftest_due(selftest)

    def run_selftest():
        """The power-off path, minus the power-off: systemd can create our transient timer, and we're root."""
        if a.fake_selftest_fail and os.path.exists(a.fake_selftest_fail):
            return False, "systemd-run couldn't arm a timer (test)"
        if a.dry_run or test_mode:
            return True, "dry run - power-off path checks skipped"
        if hasattr(os, "geteuid") and os.geteuid() != 0:
            return False, "not running as root"
        r = run_quiet(["systemd-run", "--unit=loopmedic-selftest", "--on-active=1h", "--description=LoopMedic self-test (never fires)", "/bin/true"])
        run_quiet(["systemctl", "stop", "loopmedic-selftest.timer"])
        run_quiet(["systemctl", "reset-failed", "loopmedic-selftest.timer", "loopmedic-selftest.service"])
        if r.returncode != 0:
            return False, f"systemd-run couldn't arm a timer ({(r.stderr or '').strip()[:120]})"
        return True, "power-off path works (root, systemd timer armed and removed)"

    def protect(kind, reason, delay):
        nonlocal trip
        trip = {"kind": kind, "reason": reason, "started": datetime.now(), "deadline_mono": time.monotonic() + delay,
                "hot_since": None, "hot_sent": False}
        dry = a.dry_run or kind == "test"
        if kind == "test":
            log(f"TEST - trip: {reason} - would power off in {delay}s (power-off runs in dry-run mode)", 6)
        elif dry:
            log(f"DRY RUN - trip: {reason} - would power off in {delay}s (power-off runs in dry-run mode)", 4)
        else:
            log(f"TRIPPED: {reason} - powering off in {delay}s unless cancelled.", 2)
            # written now (the systemd timer does the power-off, not us): read after the restart
            write_json_durable(last_off_path, {"at": now_iso(), "reason": reason, "kind": kind, "armed": True, "reported": False})
        power.arm(delay, dry)

    def jump_log(text, now):
        nonlocal jump_hour, jump_count
        if jump_hour != now.hour:
            jump_hour, jump_count = now.hour, 0
        jump_count += 1
        if jump_count <= 20:
            log(f"{Pw} jump: " + text)
        elif jump_count == 21:
            log(f"{Pw} jump: more than 20 this hour - not logging the rest until the next hour.")

    def fmt(v):
        return "-" if v is None else f"{v:g}"

    while True:
        time.sleep(poll_s)
        try:
            read_start = time.monotonic()
            if a.simulate_stall > 0 and not stall_sim_done and read_start - started >= 4:
                stall_sim_done = True
                time.sleep(a.simulate_stall)
            raw = pump.read() if pump else None
            temp = cpu_temp.read() if cpu_temp else None
            mono = time.monotonic()
            now = datetime.now()
            elapsed = mono - started
            if simulating:
                raw = 2400.0 if elapsed < a.simulate_healthy else a.simulate_pump

            # Stalls: a check should land every PollSeconds; log when 10 s or more go by without one
            # (up to 20 lines an hour). The monotonic clock stops during suspend, so sleep doesn't count.
            gap = mono - last_check
            last_check = mono
            if gap >= 10:
                if stall_hour != now.hour:
                    stall_hour, stall_count = now.hour, 0
                stall_count += 1
                if stall_count <= 20:
                    log(f"Stalled: no check for {gap:.0f} s (the sensor read itself took {mono - read_start:.1f} s).")
                elif stall_count == 21:
                    log("Stalled: more than 20 this hour - not logging the rest until the next hour.")

            # ---- the jump rule: a sudden rise must be confirmed by the next reading; drops count at once
            if raw is None:
                rpm, pending = None, None
            elif accepted is None:
                rpm = raw
            elif raw > accepted * 1.4 and raw - accepted > 100:
                if pending is not None and abs(raw - pending) <= pending * 0.25:
                    if not simulating:
                        jump_log(f"{fmt(accepted)} -> {fmt(raw)} rpm (confirmed by the next reading) (CPU {fmt(temp)} C)", now)
                    rpm, pending = raw, None
                else:
                    pending, rpm = raw, accepted
            else:
                if pending is not None:
                    # One wild reading that came straight back: tach noise. Counted in the hourly line, not logged one by one.
                    if not simulating:
                        glitches += 1
                    pending = None
                elif not simulating and accepted > 0 and abs(raw - accepted) / accepted > 0.15:
                    jump_log(f"{fmt(accepted)} -> {fmt(raw)} rpm (CPU {fmt(temp)} C)", now)
                rpm = raw
            if rpm is not None:
                accepted = rpm

            if temp is not None and (max_temp is None or temp > max_temp):
                max_temp = temp
            if rpm is not None and (min_rpm is None or rpm < min_rpm):
                min_rpm = rpm
            if rpm is not None and (max_rpm is None or rpm > max_rpm):
                max_rpm = rpm
            if now.date() != day:
                day, day_max_temp, day_min_pump = now.date(), None, None
            if temp is not None and (day_max_temp is None or temp > day_max_temp):
                day_max_temp = temp
            if rpm is not None and (day_min_pump is None or rpm < day_min_pump):
                day_min_pump = rpm
            if rpm is not None and rpm > pump_stopped:
                pump_ever_ok = True
            if rpm is None and (pump or simulating):
                pump_null_since = pump_null_since or mono
            else:
                pump_null_since = None
            if temp is None and cpu_temp:
                temp_null_since = temp_null_since or mono
            else:
                temp_null_since = None

            end_now = a.duration > 0 and elapsed >= a.duration
            if end_now or (mono - last_heartbeat) / 60 >= heartbeat_min:
                g = f", {glitches} one-reading {pw} glitch{'' if glitches == 1 else 'es'} ignored" if glitches else ""
                log(f"OK  {pw} {fmt(rpm)} rpm, CPU {fmt(temp)} C  (since last line: CPU max {fmt(max_temp)} C, {pw} min {fmt(min_rpm)} / max {fmt(max_rpm)} rpm{g})")
                last_heartbeat, max_temp, min_rpm, max_rpm, glitches = mono, None, None, None, 0
            if end_now:
                log("Test duration reached - exiting.")
                return 0

            # --- requests from the tray app
            # Pause: read once per new request, turned into a monotonic deadline: at most 2 h from when the
            # request was WRITTEN, never from a future-dated file. Clock changes can't stretch it.
            pr = read_control(a.control_dir, "pause.json", "until")
            if pr and pr[1] != pause_mtime:
                pause_mtime = pr[1]
                if pr[1] <= now + timedelta(minutes=5):
                    until = min(pr[0], pr[1] + timedelta(hours=2))
                    remaining = (until - now).total_seconds()
                    pause_end_mono = mono + remaining if remaining > 0 else 0.0
                    pause_until_wall = until if remaining > 0 else None
            paused = mono < pause_end_mono
            if paused and not was_paused:
                log(f"PAUSED by the tray app until {pause_until_wall:%H:%M}.", 4)
            if not paused and was_paused:
                log("Pause ended - ARMED again.")
            was_paused = paused

            t = read_control_date(a.control_dir, "test.json", "requested")
            if t and t > last_test and (now - t).total_seconds() < 60 and not trip:
                last_test = t
                protect("test", f"TEST - simulated {pw} failure (nothing will power off)", delay_s)

            c = read_control_date(a.control_dir, "cancel.json", "at")
            if trip and c and c > last_cancel and c >= trip["started"] - timedelta(seconds=1):
                last_cancel = c
                power.cancel()
                log(f"Power-off CANCELLED from the tray pop-up ({trip['reason']}). Re-arming in {rearm_s:g}s.", 5 if trip["kind"] != "test" else None)
                if trip["kind"] != "test" and not a.dry_run:
                    try:
                        os.remove(last_off_path)
                    except OSError:
                        pass
                trip = None
                cooldown_until = mono + rearm_s

            p = read_control_date(a.control_dir, "poweroff.json", "at")
            if trip and p and p > last_poweroff and p >= trip["started"] - timedelta(seconds=1):
                last_poweroff = p
                if trip["kind"] == "test":
                    log("Test pop-up: 'Shut down now' pressed - test only, nothing happens.")
                else:
                    power.now('"Shut down now" pressed')

            # --- hotter, faster: a countdown that's already running is cut short if the CPU is hot
            if trip and trip["kind"] != "test" and not trip["hot_sent"] and temp is not None and temp_check:
                hot_now = temp >= cpu_max if trip["kind"] == "pump" else temp >= cpu_max + 5
                if hot_now:
                    if trip["hot_since"] is None:
                        trip["hot_since"] = mono
                    elif mono - trip["hot_since"] >= hot_delay_s and trip["deadline_mono"] - mono > hot_delay_s:
                        trip["hot_sent"] = True
                        log(f"CPU at {fmt(temp)} C during the countdown - powering off now (hotter, faster).", 2)
                        power.now("hotter, faster")
                else:
                    trip["hot_since"] = None

            if trip and mono > trip["deadline_mono"] + 15:
                if trip["kind"] != "test" and not a.dry_run:
                    log("Still running after the deadline (timer stopped by hand?). Re-arming.", 4)
                    try:
                        os.remove(last_off_path)
                    except OSError:
                        pass
                trip = None

            # --- health: what makes the protection weaker
            degraded = list(problems)
            if selftest_problem:
                degraded.append(selftest_problem)
            if pump_null_since and mono - pump_null_since >= 30:
                degraded.append(f"{Pw} sensor not responding - {pw} NOT watched right now.")
            if temp_null_since and mono - temp_null_since >= 30:
                degraded.append("CPU temperature not responding.")
            in_grace = elapsed < grace_s
            if (pump or simulating) and not pump_ever_ok and not in_grace and rpm is not None:
                degraded.append(f"{Pw} has read {rpm:g} rpm since boot - cable moved? {Pw} trip held; CPU temperature still watched.")
            # Slow but spinning: warn, don't power off (eco modes, or a pump that reports a different pulse
            # count, can read low). The CPU temperature trip still covers a pump that's really failing.
            if pump_ever_ok and rpm is not None and pump_stopped < rpm < pump_min:
                pump_slow_since = pump_slow_since or mono
                if mono - pump_slow_since >= pump_fail_s:
                    degraded.append(f"{Pw} slower than usual: {rpm:g} rpm (warning below {pump_min:g}). Not powering off for this alone; "
                                    "the CPU temperature trip is still armed.")
                    if not pump_slow_reported:
                        pump_slow_reported = True
            else:
                pump_slow_since, pump_slow_reported = None, False
            dtext = " | ".join(degraded)
            if dtext != last_degraded:
                if dtext:
                    log(f"DEGRADED: {dtext}", 4)
                elif last_degraded:
                    log("Protection back to normal.", 5)
                last_degraded = dtext

            # --- checks
            if not in_grace and not paused and not trip and mono >= cooldown_until:
                if (pump or simulating) and rpm is not None and pump_ever_ok:
                    if rpm <= pump_stopped:
                        # CPU cool -> allow longer (a firmware flash or fan calibration stops the pump briefly).
                        cool = (not simulating) and temp is not None and temp < cool_cpu_c
                        window = pump_fail_cool_s if cool else pump_fail_s
                        if pump_bad_since is None:
                            pump_bad_since = mono
                            log(f"{Pw} stopped: {rpm:g} rpm - watching" +
                                (f" (CPU cool at {temp:g} C: allowing {pump_fail_cool_s:g}s, e.g. for a firmware update)." if cool else "."), 4)
                        elif mono - pump_bad_since >= window:
                            hot = temp_check and temp is not None and temp >= cpu_max
                            protect("pump", f"{pw} at {rpm:g} rpm for {int(mono - pump_bad_since)}s (CPU {fmt(temp)} C)",
                                    hot_delay_s if hot else delay_s)
                            pump_bad_since = hot_since = None
                            cooldown_until = mono + rearm_s
                    elif pump_bad_since is not None:
                        log(f"{Pw} recovered: {rpm:g} rpm.")
                        pump_bad_since = None
                else:
                    pump_bad_since = None

                if temp_check and not trip and temp is not None:
                    if temp >= cpu_max:
                        if hot_since is None:
                            hot_since = mono
                            log(f"CPU hot: {temp:g} C - watching.", 4)
                        elif mono - hot_since >= cpu_hot_s:
                            protect("cpu", f"CPU at {temp:g} C for {int(mono - hot_since)}s ({pw} {fmt(rpm)} rpm)", delay_s)
                            pump_bad_since = hot_since = None
                            cooldown_until = mono + rearm_s
                    elif hot_since is not None:
                        log(f"CPU cooled: {temp:g} C.")
                        hot_since = None

            # --- monthly self-test (the power-off path, minus the power-off)
            if mono >= next_selftest and not trip and not paused and elapsed >= a.selftest_min_uptime:
                ok, detail = run_selftest()
                selftest = {"at": now_iso(), "ok": ok, "detail": detail}
                write_json_durable(selftest_path, selftest)
                if ok:
                    log(f"Monthly self-test OK: {detail}.", 6)
                    selftest_problem = None
                    next_selftest = mono + a.selftest_interval_hours * 3600
                else:
                    log(f"Monthly self-test FAILED: {detail}.", 3)
                    selftest_problem = f"Monthly self-test FAILED: {detail}. LoopMedic may not be able to power off. Reinstall."
                    next_selftest = mono + min(24 * 3600, a.selftest_interval_hours * 3600)

            # --- status for the tray app
            state = "ARMED"
            if in_grace:
                state = "STARTING"
            if degraded:
                state = "DEGRADED"
            if pump_bad_since is not None or hot_since is not None:
                state = "WATCHING"
            if paused:
                state = "PAUSED"
            if trip:
                state = "TEST" if trip["kind"] == "test" else "TRIPPED"
            lo = None
            if last_off and parse_when(last_off.get("at")) and (now - parse_when(last_off.get("at"))).total_seconds() < 24 * 3600:   # tray shows it for a day
                lo = {"at": last_off.get("at"), "reason": last_off.get("reason")}
            write_status(status_path, {
                "at": now_iso(),
                "version": VERSION,
                "state": state,
                "dryRun": bool(a.dry_run),
                "coolerType": "air" if air else "aio",
                "degraded": degraded,
                "pumpRpm": rpm,
                "cpuTemp": temp,
                "cpuName": cpu_now,
                "pumpMinRpm": pump_min,
                "pumpStoppedRpm": pump_stopped,
                "cpuMaxTemp": cpu_max if temp_check else 0,
                "dayMaxTemp": day_max_temp,
                "dayMinPump": day_min_pump,
                "pausedUntil": to_iso(pause_until_wall) if paused and pause_until_wall else None,
                "tripKind": trip["kind"] if trip else None,
                "tripReason": trip["reason"] if trip else None,
                "tripDeadline": to_iso(now + timedelta(seconds=trip["deadline_mono"] - mono)) if trip else None,
                "lastPowerOff": lo,
                "selfTest": {"at": selftest.get("at"), "ok": bool(selftest.get("ok"))} if selftest else None,
                "fans": [{"name": f.label, "rpm": rpm if (pump and f.id == pump.id) else f.read(), "pump": bool(pump and f.id == pump.id)} for f in fans],
            })
            sd_notify("WATCHDOG=1")
        except Exception as e:  # noqa: BLE001 - the watchdog must keep running
            log(f"Loop error (continuing): {e!r}", 3)


if __name__ == "__main__":
    sys.exit(main())
