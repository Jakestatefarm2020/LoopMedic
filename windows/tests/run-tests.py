"""LoopMedic (Windows) scenario tests - no hardware, nothing powers off.

Runs bin\\LoopMedic.exe against a fake sensor file (--fake-sensors) in throwaway folders, changes the
"readings" over time, and checks what the watchdog did (status.json, log, control files).
Every run is a dry run. Needs: Python 3.8+, a built bin\\LoopMedic.exe (run build.ps1 first).

    python tests\\run-tests.py            all scenarios (in parallel, ~2.5 min)
    python tests\\run-tests.py spike cancel   only the ones whose names contain these words
"""
import json, os, shutil, subprocess, sys, tempfile, threading, time
from datetime import datetime, timedelta

HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.dirname(HERE)
EXE = os.path.join(REPO, "bin", "LoopMedic.exe")
BASE = tempfile.mkdtemp(prefix="loopmedic-tests-")
PUMP_ID, TEMP_ID = "/lpc/fake/0/fan/0", "/amdcpu/0/temperature/2"

BASE_CONFIG = {
    "PumpSensorId": PUMP_ID, "CpuTempSensorId": TEMP_ID, "CpuName": "AMD Ryzen 7 5800X3D",
    "PumpMinRpm": 500, "PumpStoppedRpm": 150, "PumpFailSeconds": 10, "PumpFailSecondsCool": 60, "CoolCpuTempC": 60,
    "CpuMaxTempC": 85, "CpuHotSeconds": 30, "PollSeconds": 2, "StartupGraceSeconds": 4,
    "ShutdownDelaySeconds": 30, "HotShutdownDelaySeconds": 5, "RearmSeconds": 120, "HeartbeatMinutes": 60,
}


class Rig:
    """One throwaway install folder + fake sensors."""

    def __init__(self, name, cpu_name="AMD Ryzen 7 5800X3D", pump=2400.0, temp=50.0, **cfg):
        self.name = name
        self.root = os.path.join(BASE, name)
        os.makedirs(os.path.join(self.root, "control"))
        shutil.copy(os.path.join(REPO, "cpu-limits.json"), self.root)
        c = dict(BASE_CONFIG)
        c.update(cfg)
        self.write_json("config.json", c)
        self.fake = os.path.join(self.root, "fake-sensors.json")
        self.state = {"cpuName": cpu_name, "pump": pump, "temp": temp, "clockOffsetSeconds": 0, "privilegeFail": False,
                      "pumpPresent": True}
        self.push()
        self.proc = None

    def write_json(self, rel, obj):
        p = os.path.join(self.root, rel)
        tmp = p + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(obj, f)
        os.replace(tmp, p)

    def push(self, **changes):
        self.state.update(changes)
        s = self.state
        sensors = [{"id": TEMP_ID, "name": "Core (Tctl/Tdie)", "type": "Temperature", "hw": "Cpu", "value": s["temp"]},
                   {"id": "/lpc/fake/0/fan/1", "name": "Fan #2", "type": "Fan", "hw": "SuperIO", "value": 1200}]
        if s["pumpPresent"]:
            sensors.insert(0, {"id": PUMP_ID, "name": "Fan #1", "type": "Fan", "hw": "SuperIO", "value": s["pump"]})
        doc = {"cpuName": s["cpuName"], "sensors": sensors,
               "clockOffsetSeconds": s["clockOffsetSeconds"], "privilegeFail": s["privilegeFail"]}
        if s.get("oneShot"):
            doc["oneShot"], doc["oneShotNonce"] = s["oneShot"], s["oneShotNonce"]
        self.write_json("fake-sensors.json", doc)

    def glitch(self, pump_value):
        """The pump reads pump_value for exactly one check, then its normal value again."""
        self.push(oneShot={PUMP_ID: pump_value}, oneShotNonce=str(time.time()))

    def start(self, *extra, duration=40, supervise=False):
        args = [EXE, "--root", self.root, "--fake-sensors", self.fake, "--dry-run"]
        if not supervise:
            args.append("--worker")
        args += [str(x) for x in extra]
        if duration:
            args += ["--duration", str(duration)]
        self.proc = subprocess.Popen(args, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE, text=True)
        return self

    def wait(self, timeout=200):
        try:
            return self.proc.wait(timeout)
        except subprocess.TimeoutExpired:
            self.proc.kill()
            return "timeout"

    def status(self):
        try:
            with open(os.path.join(self.root, "status.json"), encoding="utf-8") as f:
                return json.load(f)
        except Exception as e:  # noqa: BLE001
            return {"state": f"<no status: {e}>"}

    def log(self):
        try:
            with open(os.path.join(self.root, "loopmedic.log"), encoding="utf-8") as f:
                return f.read()
        except OSError:
            return ""

    def control(self, name, field, when, mtime=None):
        p = os.path.join(self.root, "control", name)
        with open(p, "w", encoding="utf-8") as f:
            json.dump({field: when.astimezone().isoformat()}, f)
        if mtime is not None:
            os.utime(p, (mtime.timestamp(), mtime.timestamp()))


def expect(cond, what, problems):
    if not cond:
        problems.append(what)


# ---------------------------------------------------------------- scenarios
# Each returns a list of problems (empty = pass).

def t_pump_stops_hot():
    r = Rig("pump_stops_hot", temp=88).start(duration=26)
    time.sleep(7); r.push(pump=0)
    time.sleep(5); s1 = r.status()["state"]
    r.wait(); log = r.log(); p = []
    expect(s1 == "WATCHING", f"6 s after stop: {s1}, want WATCHING", p)
    expect("would power off in 5s" in log, "CPU already hot -> 5 s countdown (hotter, faster)", p)
    expect("WOULD POWER OFF NOW" in log, "helper reached the power-off", p)
    return p


def t_pump_stops_cool_then_warms():
    r = Rig("pump_stops_cool", temp=45).start(duration=44)
    time.sleep(7); r.push(pump=0)
    time.sleep(16); s1 = r.status()["state"]
    r.push(temp=66)
    time.sleep(6); s2 = r.status()
    r.wait(); p = []
    expect(s1 == "WATCHING", f"16 s stopped while cool: {s1}, want WATCHING (60 s allowed)", p)
    expect(s2["state"] == "TRIPPED", f"after CPU warmed: {s2['state']}, want TRIPPED", p)
    expect("allowing 60s" in r.log(), "cool grace logged", p)
    return p


def t_spike_ignored():
    r = Rig("spike_ignored").start(duration=20)
    time.sleep(7); r.glitch(4600)             # exactly one bad reading, like the tach noise seen on one tester's PC
    seen = []
    for _ in range(12):
        time.sleep(0.5); seen.append(r.status().get("pumpRpm"))
    r.wait(); log = r.log(); p = []
    expect(4600 not in seen, f"one-reading spike must not reach status (saw {seen})", p)
    expect("1 one-reading pump glitch ignored" in log, "glitch counted in the hourly line", p)
    return p


def t_rise_confirmed():
    r = Rig("rise_confirmed").start(duration=20)
    time.sleep(7); r.push(pump=3600)
    time.sleep(6); s = r.status().get("pumpRpm")
    r.wait(); p = []
    expect(s == 3600, f"a real rise is accepted after one more reading (status {s})", p)
    expect("confirmed by the next reading" in r.log(), "confirmed jump logged", p)
    return p


def t_slow_pump_warns_only():
    r = Rig("slow_pump", temp=55).start(duration=40)
    time.sleep(7); r.push(pump=350)
    time.sleep(16); st = r.status()
    r.wait(); log = r.log(); p = []
    expect(st["state"] == "DEGRADED", f"slow pump: {st['state']}, want DEGRADED (warning)", p)
    expect(any("slower than usual" in d for d in st.get("degraded", [])), "slow-pump warning text", p)
    expect("trip:" not in log, "a slow (spinning) pump must NOT power off", p)
    return p


def t_clock_jump_back():
    r = Rig("clock_jump", temp=70).start(duration=30)
    time.sleep(7); r.push(pump=0)
    time.sleep(4); r.push(clockOffsetSeconds=-3600)   # clock goes back an hour mid-failure
    time.sleep(10); s = r.status()["state"]
    r.wait(); p = []
    expect(s == "TRIPPED", f"clock jumped back 1 h: {s}, want TRIPPED on time", p)
    return p


def t_cancel():
    r = Rig("cancel", temp=70).start(duration=34)
    time.sleep(7); r.push(pump=0)
    time.sleep(14); s1 = r.status()["state"]
    r.control("cancel.json", "at", datetime.now())
    time.sleep(4); s2 = r.status()["state"]
    r.wait(); log = r.log(); p = []
    expect(s1 == "TRIPPED", f"before cancel: {s1}", p)
    expect(s2 != "TRIPPED", f"after cancel: {s2}", p)
    expect("cancelled from the tray" in log, "helper saw the cancel", p)
    expect("WOULD POWER OFF NOW" not in log, "cancelled trip must not reach power-off", p)
    return p


def t_hotter_faster_during_countdown():
    r = Rig("hot_fast", temp=70).start(duration=36)
    time.sleep(7); r.push(pump=0)
    time.sleep(13); r.push(temp=90)   # trip started at CPU 70 (30 s countdown), then it heats up
    r.wait(); log = r.log(); p = []
    expect("hotter, faster" in log, "countdown cut short when the CPU got hot", p)
    expect("(power off now)" in log, "helper powered off early", p)
    return p


def t_cpu_trip():
    r = Rig("cpu_trip", temp=50, CpuHotSeconds=6).start(duration=24)
    time.sleep(7); r.push(temp=87)
    time.sleep(12); s = r.status()
    r.wait(); p = []
    expect(s["state"] == "TRIPPED" and s.get("tripKind") == "cpu", f"CPU trip: {s['state']}/{s.get('tripKind')}", p)
    return p


def t_pause_capped():
    r = Rig("pause").start(duration=22)
    time.sleep(6); r.control("pause.json", "until", datetime(2099, 1, 1))
    time.sleep(4); pu = r.status().get("pausedUntil")
    r.control("pause.json", "until", datetime(2099, 1, 1), mtime=datetime.now() + timedelta(hours=3))   # future-dated file
    time.sleep(4); s2 = r.status()["state"]
    r.wait(); p = []
    want = datetime.now() + timedelta(hours=2)
    ok = pu and abs((datetime.fromisoformat(pu[:19]) - want).total_seconds()) < 120
    expect(ok, f"2099 pause capped to ~2 h (got {pu})", p)
    expect(s2 == "PAUSED", f"future-dated file ignored, earlier pause kept: {s2}", p)
    return p


def t_sensor_vanishes():
    r = Rig("sensor_gone").start(duration=46)
    time.sleep(7); r.push(pumpPresent=False)
    time.sleep(36); st = r.status()
    r.wait(); p = []
    expect(st["state"] == "DEGRADED" and any("not responding" in d for d in st.get("degraded", [])), f"pump sensor gone: {st.get('degraded')}", p)
    return p


def t_cpu_changed_uses_table():
    r = Rig("cpu_changed", cpu_name="AMD Ryzen 9 9950X3D").start(duration=10)
    time.sleep(8); st = r.status()
    r.wait(); p = []
    expect(st.get("cpuMaxTemp") == 90, f"9950X3D -> 90 C from the table (got {st.get('cpuMaxTemp')})", p)
    expect(any("CPU changed" in d for d in st.get("degraded", [])), "CPU change flagged", p)
    return p


def t_limit_zero_uses_table():
    r = Rig("limit_zero", CpuMaxTempC=0).start(duration=10)
    time.sleep(8); st = r.status()
    r.wait(); p = []
    expect(st.get("cpuMaxTemp") == 85, f"0 = off is not allowed any more; 5800X3D -> 85 (got {st.get('cpuMaxTemp')})", p)
    return p


def t_unknown_cpu_fallback():
    r = Rig("unknown_cpu", cpu_name="Mystery CPU 3000", CpuName="", CpuMaxTempC=0).start(duration=10)
    time.sleep(8); st = r.status()
    r.wait(); p = []
    expect(st.get("cpuMaxTemp") == 95, f"unknown CPU -> 95 C fallback (got {st.get('cpuMaxTemp')})", p)
    return p


def t_broken_config():
    r = Rig("broken_config")
    with open(os.path.join(r.root, "config.json"), "w") as f:
        f.write("{ not json")
    r.start(duration=5)
    code = r.wait(); st = r.status(); p = []
    expect(code == 4, f"exit code {code}, want 4", p)
    expect(st["state"] == "DEGRADED", f"status {st['state']}", p)
    return p


def t_test_request():
    r = Rig("test_request").start(duration=22)
    time.sleep(7); r.control("test.json", "requested", datetime.now())
    time.sleep(4); st = r.status()
    r.wait(); p = []
    expect(st["state"] == "TEST", f"state {st['state']}, want TEST", p)
    return p


def t_no_power_permission():
    r = Rig("no_permission")
    r.push(privilegeFail=True)
    r.start(duration=10)
    time.sleep(8); st = r.status()
    r.wait(); p = []
    expect(any("No permission to power off" in d for d in st.get("degraded", [])), f"boot self-test: {st.get('degraded')}", p)
    return p


def t_bad_temp_reading():
    r = Rig("bad_temp", temp=3).start(duration=10)
    time.sleep(8); st = r.status()
    r.wait(); p = []
    expect(any("looks wrong" in d for d in st.get("degraded", [])), f"implausible CPU temp flagged: {st.get('degraded')}", p)
    return p


def t_stall_logged():
    r = Rig("stall").start("--simulate-stall", 9, duration=22)
    r.wait(); p = []
    expect("Stalled: no check for" in r.log(), "stall line", p)
    return p


def t_monthly_selftest():
    r = Rig("selftest").start("--selftest-min-uptime", 6, "--selftest-interval-hours", 0.003, duration=40)
    time.sleep(16)
    r.push(privilegeFail=True)   # second run (~11 s later) must fail
    time.sleep(20); st = r.status()
    r.wait(); log = r.log(); p = []
    expect("Monthly self-test OK" in log, "first self-test OK", p)
    expect("Monthly self-test FAILED" in log, "second self-test FAILED once permission is gone", p)
    expect(any("self-test FAILED" in d for d in st.get("degraded", [])), "failed self-test shows as DEGRADED", p)
    return p


def t_last_poweroff_notice():
    r = Rig("last_poweroff")
    r.write_json("last-poweroff.json", {"at": datetime.now().astimezone().isoformat(), "reason": "pump at 0 rpm for 10s", "kind": "pump", "reported": False})
    r.start(duration=8)
    time.sleep(6); st = r.status()
    r.wait(); p = []
    with open(os.path.join(r.root, "last-poweroff.json")) as f:
        lp = json.load(f)
    expect("Last power-off by LoopMedic" in r.log(), "logged once at start", p)
    expect((st.get("lastPowerOff") or {}).get("reason") == "pump at 0 rpm for 10s", f"status carries it for the tray: {st.get('lastPowerOff')}", p)
    expect(lp.get("reported") is True, "marked as reported", p)
    return p


def t_air_cooler_fan_stops():
    r = Rig("air_cooler", pump=900, temp=88, CoolerType="air", PumpMinRpm=250).start(duration=26)
    time.sleep(7); st = r.status(); r.push(pump=0)
    r.wait(); log = r.log(); p = []
    expect(st.get("coolerType") == "air", f"status says air cooler ({st.get('coolerType')})", p)
    expect("air cooler" in log and "CPU fan: Fan #1" in log, "start line names the CPU fan", p)
    expect("CPU fan stopped: 0 rpm" in log, "a stopped CPU fan is watched like a stopped pump", p)
    expect("CPU fan at 0 rpm" in log and "would power off in 5s" in log, "same trip rule (CPU hot -> 5 s)", p)
    return p


def t_temperature_only():
    r = Rig("temp_only", PumpSensorId="").start(duration=10)
    time.sleep(8); st = r.status()
    r.wait(); p = []
    expect(st["state"] in ("ARMED", "STARTING"), f"no pump wire = temperature-only, still armed ({st['state']})", p)
    expect("temperature-only protection" in r.log(), "logged", p)
    return p


def t_supervisor_restarts_crash():
    r = Rig("sup_crash").start("--crash-after", 6, duration=0, supervise=True)
    time.sleep(16)
    r.proc.kill()
    log = r.log(); p = []
    expect("watchdog exited (code 1) - restarting in 2 s" in log, "supervisor restarted the crashed watchdog", p)
    expect(log.count("started (DRY RUN)") >= 2, "watchdog came back", p)
    return p


def t_supervisor_restarts_hang():
    r = Rig("sup_hang").start("--hang-after", 6, duration=0, supervise=True)
    time.sleep(100)
    r.proc.kill()
    log = r.log(); p = []
    expect("stopped responding for 30 s - restarting it" in log, "supervisor restarted the hung watchdog", p)
    return p


# ---------------------------------------------------------------- runner
def main():
    if not os.path.exists(EXE):
        sys.exit(f"{EXE} not found - run build.ps1 first")
    tests = [v for k, v in sorted(globals().items()) if k.startswith("t_")]
    if len(sys.argv) > 1:
        tests = [t for t in tests if any(w in t.__name__ for w in sys.argv[1:])]
    results = {}

    def run(t):
        try:
            results[t.__name__] = t()
        except Exception as e:  # noqa: BLE001
            results[t.__name__] = [f"EXCEPTION {e!r}"]

    threads = [threading.Thread(target=run, args=(t,)) for t in tests]
    for th in threads:
        th.start()
    for th in threads:
        th.join()
    failed = 0
    for name in sorted(results):
        probs = results[name]
        print(("PASS  " if not probs else "FAIL  ") + name[2:])
        for x in probs:
            print("        - " + x)
        failed += bool(probs)
    print(f"\n{len(results) - failed}/{len(results)} passed   (run folders: {BASE})")
    # stop any supervisor/worker/helper still running from these folders
    subprocess.run(["powershell", "-NoProfile", "-Command",
                    "Get-CimInstance Win32_Process -Filter \"Name='LoopMedic.exe'\" | Where-Object { $_.CommandLine -like '*" + BASE + "*' } | ForEach-Object { Stop-Process -Id $_.ProcessId -Force }"],
                   capture_output=True)
    sys.exit(1 if failed else 0)


if __name__ == "__main__":
    main()
