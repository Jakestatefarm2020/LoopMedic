"""LoopMedic (Linux) scenario tests - no hardware, nothing powers off, runs on any OS with Python 3.8+.

Runs the real loopmedic/loopmedic.py against fake /sys + /proc trees in throwaway folders, changes the
"readings" over time, and checks what the service did (status.json, log). Every run is a dry run.

    python3 tests/run-tests.py               all scenarios (in parallel, ~1.5 min)
    python3 tests/run-tests.py spike cancel  only the ones whose names contain these words
"""
import json, os, subprocess, sys, tempfile, threading, time
from datetime import datetime, timedelta

HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.dirname(HERE)
SRC = os.path.join(REPO, "loopmedic", "loopmedic.py")
LIMITS = os.path.join(REPO, "loopmedic", "cpu-limits.json")
BASE = tempfile.mkdtemp(prefix="loopmedic-linux-tests-")
CPU = "AMD Ryzen 7 9800X3D 8-Core Processor"


def w(path, text):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    tmp = path + ".tmp"
    with open(tmp, "w") as f:
        f.write(str(text))
    os.replace(tmp, path)


class Rig:
    def __init__(self, name, cpu=CPU, temp=50.0, pump=2400, **cfg):
        self.root = os.path.join(BASE, name)
        self.h = os.path.join(self.root, "sys", "class", "hwmon")
        w(os.path.join(self.h, "hwmon0", "name"), "k10temp\n")
        w(os.path.join(self.h, "hwmon0", "temp1_label"), "Tctl\n")
        w(os.path.join(self.h, "hwmon1", "name"), "nct6683\n")
        for i, r in enumerate([1180, pump, 0, 0, 940, 0, 0], 1):
            w(os.path.join(self.h, "hwmon1", f"fan{i}_input"), r)
        self.set_temp(temp)
        w(os.path.join(self.root, "proc", "cpuinfo"), f"processor\t: 0\nmodel name\t: {cpu}\n")
        c = {"PumpSensorId": "nct6683|fan2", "CpuTempSensorId": "k10temp|temp1", "CpuName": CPU,
             "PumpStoppedRpm": 150, "PumpMinRpm": 500, "PumpFailSeconds": 6, "PumpFailSecondsCool": 60, "CoolCpuTempC": 60,
             "CpuMaxTempC": 90, "CpuHotSeconds": 30, "PollSeconds": 1, "StartupGraceSeconds": 3, "ShutdownDelaySeconds": 20,
             "HotShutdownDelaySeconds": 3, "RearmSeconds": 120, "HeartbeatMinutes": 60}
        c.update(cfg)
        w(os.path.join(self.root, "config.json"), json.dumps(c))
        os.makedirs(os.path.join(self.root, "control"), exist_ok=True)
        self.proc = None

    def set_pump(self, v):
        w(os.path.join(self.h, "hwmon1", "fan2_input"), int(v))

    def remove_pump(self):
        os.remove(os.path.join(self.h, "hwmon1", "fan2_input"))

    def set_temp(self, c):
        w(os.path.join(self.h, "hwmon0", "temp1_input"), int(c * 1000))

    def start(self, *extra, duration=30):
        args = [sys.executable, SRC, "--config", os.path.join(self.root, "config.json"), "--cpu-limits", LIMITS,
                "--state-dir", self.root, "--data-dir", self.root, "--control-dir", os.path.join(self.root, "control"),
                "--log", os.path.join(self.root, "pg.log"), "--sysfs", os.path.join(self.root, "sys"),
                "--proc", os.path.join(self.root, "proc"), "--dry-run", *[str(x) for x in extra]]
        if duration:
            args += ["--duration", str(duration)]
        self.proc = subprocess.Popen(args, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE, text=True)
        return self

    def wait(self):
        try:
            return self.proc.wait(200)
        except subprocess.TimeoutExpired:
            self.proc.kill()
            return "timeout"

    def status(self):
        try:
            with open(os.path.join(self.root, "status.json")) as f:
                return json.load(f)
        except Exception as e:  # noqa: BLE001
            return {"state": f"<no status: {e}>"}

    def next_status(self, timeout=5):
        """Wait until the service writes its next status (= it just finished one check)."""
        first = self.status().get("at")
        end = time.time() + timeout
        while time.time() < end:
            s = self.status()
            if s.get("at") != first:
                return s
            time.sleep(0.05)
        return self.status()

    def log(self):
        try:
            with open(os.path.join(self.root, "pg.log"), encoding="utf-8") as f:
                return f.read()
        except OSError:
            return ""

    def control(self, name, field, when, mtime=None):
        p = os.path.join(self.root, "control", name)
        with open(p, "w") as f:
            json.dump({field: when.astimezone().isoformat()}, f)
        if mtime is not None:
            os.utime(p, (mtime.timestamp(), mtime.timestamp()))


def expect(cond, what, p):
    if not cond:
        p.append(what)


# ---------------------------------------------------------------- scenarios
def t_pump_stops_hot():
    r = Rig("pump_stops_hot", temp=92).start(duration=20)
    time.sleep(5); r.set_pump(0)
    time.sleep(3); s1 = r.status()["state"]
    r.wait(); log = r.log(); p = []
    expect(s1 == "WATCHING", f"after stop: {s1}", p)
    expect("would power off in 3s" in log, "CPU already hot -> short countdown (hotter, faster)", p)
    expect("WOULD POWER OFF NOW" in log, "countdown reached the power-off", p)
    return p


def t_pump_stops_cool_then_warms():
    r = Rig("pump_stops_cool", temp=45).start(duration=26)
    time.sleep(5); r.set_pump(0)
    time.sleep(10); s1 = r.status()["state"]
    r.set_temp(66)
    time.sleep(3); s2 = r.status()["state"]
    r.wait(); p = []
    expect(s1 == "WATCHING", f"stopped while cool: {s1}, want WATCHING (60 s allowed)", p)
    expect(s2 == "TRIPPED", f"after CPU warmed: {s2}", p)
    return p


def t_spike_ignored():
    r = Rig("spike_ignored").start(duration=14)
    time.sleep(5); r.next_status()
    r.set_pump(4600); s1 = r.next_status()      # the check that saw 4600
    r.set_pump(2400); s2 = r.next_status()      # back to normal on the next check
    r.wait(); p = []
    expect(s1.get("pumpRpm") == 2400 and s2.get("pumpRpm") == 2400, f"one-reading spike must not reach status ({s1.get('pumpRpm')}, {s2.get('pumpRpm')})", p)
    expect("1 one-reading pump glitch ignored" in r.log(), "glitch counted in the hourly line", p)
    return p


def t_rise_confirmed():
    r = Rig("rise_confirmed").start(duration=12)
    time.sleep(5); r.set_pump(3600)
    time.sleep(3); s = r.status().get("pumpRpm")
    r.wait(); p = []
    expect(s == 3600, f"real rise accepted after one more reading ({s})", p)
    expect("confirmed by the next reading" in r.log(), "logged", p)
    return p


def t_slow_pump_warns_only():
    r = Rig("slow_pump", temp=55).start(duration=22)
    time.sleep(5); r.set_pump(350)
    time.sleep(10); st = r.status()
    r.wait(); log = r.log(); p = []
    expect(st["state"] == "DEGRADED" and any("slower than usual" in d for d in st.get("degraded", [])), f"slow pump warning ({st['state']})", p)
    expect("trip:" not in log, "a slow, spinning pump must NOT power off", p)
    return p


def t_cancel():
    r = Rig("cancel", temp=70).start(duration=26)
    time.sleep(5); r.set_pump(0)
    time.sleep(9); s1 = r.status()["state"]
    r.control("cancel.json", "at", datetime.now())
    time.sleep(3); s2 = r.status()["state"]
    r.wait(); log = r.log(); p = []
    expect(s1 == "TRIPPED", f"before cancel: {s1}", p)
    expect(s2 != "TRIPPED", f"after cancel: {s2}", p)
    expect("WOULD POWER OFF NOW" not in log, "cancelled trip must not reach power-off", p)
    return p


def t_poweroff_now():
    r = Rig("poweroff_now", temp=70).start(duration=20)
    time.sleep(5); r.set_pump(0)
    time.sleep(9); r.control("poweroff.json", "at", datetime.now())
    r.wait(); p = []
    expect('would power off now ("Shut down now" pressed)' in r.log(), "power off now honoured", p)
    return p


def t_hotter_faster_during_countdown():
    r = Rig("hot_fast", temp=70).start(duration=24)
    time.sleep(5); r.set_pump(0)
    time.sleep(9); r.set_temp(93)
    r.wait(); log = r.log(); p = []
    expect("hotter, faster" in log, "countdown cut short when the CPU got hot", p)
    return p


def t_cpu_trip():
    r = Rig("cpu_trip", CpuHotSeconds=4).start(duration=16)
    time.sleep(5); r.set_temp(91)
    time.sleep(7); s = r.status()
    r.wait(); p = []
    expect(s["state"] == "TRIPPED" and s.get("tripKind") == "cpu", f"CPU trip ({s['state']}/{s.get('tripKind')})", p)
    return p


def t_pause_capped():
    r = Rig("pause").start(duration=14)
    time.sleep(4); r.control("pause.json", "until", datetime(2099, 1, 1))
    time.sleep(2); pu = r.status().get("pausedUntil")
    r.control("pause.json", "until", datetime(2099, 1, 1), mtime=datetime.now() + timedelta(hours=3))
    time.sleep(3); s2 = r.status()["state"]
    r.wait(); p = []
    want = datetime.now() + timedelta(hours=2)
    ok = pu and abs((datetime.fromisoformat(pu[:19]) - want).total_seconds()) < 120
    expect(ok, f"2099 pause capped to ~2 h ({pu})", p)
    expect(s2 == "PAUSED", f"future-dated file ignored, earlier pause kept ({s2})", p)
    return p


def t_sensor_vanishes():
    r = Rig("sensor_gone").start(duration=42)
    time.sleep(5); r.remove_pump()
    time.sleep(34); st = r.status()
    r.wait(); p = []
    expect(st["state"] == "DEGRADED" and any("not responding" in d for d in st.get("degraded", [])), f"pump sensor gone ({st.get('degraded')})", p)
    return p


def t_cpu_changed_uses_table():
    r = Rig("cpu_changed", cpu="AMD Ryzen 9 9950X 16-Core Processor").start(duration=6)
    time.sleep(5); st = r.status()
    r.wait(); p = []
    expect(st.get("cpuMaxTemp") == 95, f"9950X runs at its limit -> 95 C ({st.get('cpuMaxTemp')})", p)
    expect(any("CPU changed" in d for d in st.get("degraded", [])), "CPU change flagged", p)
    return p


def t_limit_zero_uses_table():
    r = Rig("limit_zero", CpuMaxTempC=0).start(duration=6)
    time.sleep(5); st = r.status()
    r.wait(); p = []
    expect(st.get("cpuMaxTemp") == 90, f"0 = off not allowed; 9800X3D -> 90 ({st.get('cpuMaxTemp')})", p)
    return p


def t_unknown_cpu_fallback():
    r = Rig("unknown_cpu", cpu="Mystery CPU 3000", CpuName="", CpuMaxTempC=0).start(duration=6)
    time.sleep(5); st = r.status()
    r.wait(); p = []
    expect(st.get("cpuMaxTemp") == 95, f"unknown CPU -> 95 C ({st.get('cpuMaxTemp')})", p)
    return p


def t_broken_config():
    r = Rig("broken_config")
    w(os.path.join(r.root, "config.json"), "{ not json")
    r.start(duration=4)
    code = r.wait(); st = r.status(); p = []
    expect(code == 4 and st["state"] == "DEGRADED", f"exit {code}, status {st['state']}", p)
    return p


def t_test_request():
    r = Rig("test_request").start(duration=12)
    time.sleep(5); r.control("test.json", "requested", datetime.now())
    time.sleep(2); st = r.status()
    r.wait(); p = []
    expect(st["state"] == "TEST", f"state {st['state']}", p)
    return p


def t_bad_temp_reading():
    r = Rig("bad_temp", temp=3).start(duration=6)
    time.sleep(5); st = r.status()
    r.wait(); p = []
    expect(any("looks wrong" in d for d in st.get("degraded", [])), f"implausible temp flagged ({st.get('degraded')})", p)
    return p


def t_stall_logged():
    r = Rig("stall").start("--simulate-stall", 9, duration=18)
    r.wait(); p = []
    expect("Stalled: no check for" in r.log(), "stall line", p)
    return p


def t_monthly_selftest():
    r = Rig("selftest")
    fail_flag = os.path.join(r.root, "fail-selftest")
    r.start("--selftest-min-uptime", 4, "--selftest-interval-hours", 0.002, "--fake-selftest-fail", fail_flag, duration=24)
    time.sleep(8); open(fail_flag, "w").close()
    time.sleep(10); st = r.status()
    r.wait(); log = r.log(); p = []
    expect("Monthly self-test OK" in log, "first self-test OK", p)
    expect("Monthly self-test FAILED" in log, "second self-test FAILED", p)
    expect(any("self-test FAILED" in d for d in st.get("degraded", [])), "failure shows as DEGRADED", p)
    return p


def t_last_poweroff_notice():
    r = Rig("last_poweroff")
    w(os.path.join(r.root, "last-poweroff.json"), json.dumps({"at": datetime.now().astimezone().isoformat(), "reason": "pump at 0 rpm for 10s",
                                                               "kind": "pump", "armed": True, "reported": False}))
    r.start(duration=5)
    time.sleep(4); st = r.status()
    r.wait(); p = []
    with open(os.path.join(r.root, "last-poweroff.json")) as f:
        lp = json.load(f)
    expect("Last power-off by LoopMedic" in r.log(), "logged at start", p)
    expect((st.get("lastPowerOff") or {}).get("reason") == "pump at 0 rpm for 10s", f"status carries it ({st.get('lastPowerOff')})", p)
    expect(lp.get("reported") is True, "marked as reported", p)
    return p


def t_temperature_only():
    r = Rig("temp_only", PumpSensorId="").start(duration=6)
    time.sleep(5); st = r.status()
    r.wait(); p = []
    expect(st["state"] in ("ARMED", "STARTING"), f"temperature-only still armed ({st['state']})", p)
    expect("temperature-only protection" in r.log(), "logged", p)
    return p


def main():
    tests = [v for k, v in sorted(globals().items()) if k.startswith("t_")]
    if len(sys.argv) > 1:
        tests = [t for t in tests if any(x in t.__name__ for x in sys.argv[1:])]
    results = {}

    def run(t):
        try:
            results[t.__name__] = t()
        except Exception as e:  # noqa: BLE001
            results[t.__name__] = [f"EXCEPTION {e!r}"]

    th = [threading.Thread(target=run, args=(t,)) for t in tests]
    for t in th:
        t.start()
    for t in th:
        t.join()
    failed = 0
    for name in sorted(results):
        print(("PASS  " if not results[name] else "FAIL  ") + name[2:])
        for x in results[name]:
            print("        - " + x)
        failed += bool(results[name])
    print(f"\n{len(results) - failed}/{len(results)} passed   (run folders: {BASE})")
    sys.exit(1 if failed else 0)


if __name__ == "__main__":
    main()
