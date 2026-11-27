"""LoopMedic (Linux) scenario tests - no hardware, nothing powers off, runs on any OS with Python 3.8+.

Runs the real loopmedic/loopmedic.py against fake /sys + /proc trees in throwaway folders, changes the
"readings" over time, and checks what the service did (status.json, log). Every run is a dry run.

    python3 tests/run-tests.py               all scenarios (in parallel, ~1.5 min)
    python3 tests/run-tests.py spike cancel  only the ones whose names contain these words
"""
import base64, json, os, socket, socketserver, subprocess, sys, tempfile, threading, time
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
                "--proc", os.path.join(self.root, "proc"), "--mail", os.path.join(self.root, "mail.json"),
                "--mail-check-seconds", "2", "--dry-run", *[str(x) for x in extra]]
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


def t_air_cooler_fan_stops():
    r = Rig("air_cooler", pump=900, temp=95, CoolerType="air", PumpMinRpm=250).start(duration=16)
    time.sleep(4); st = r.status(); r.set_pump(0)
    r.wait(); log = r.log(); p = []
    expect(st.get("coolerType") == "air", f"status says air cooler ({st.get('coolerType')})", p)
    expect("air cooler" in log and "CPU fan: Fan #2" in log, "start line names the CPU fan", p)
    expect("CPU fan stopped: 0 rpm" in log, "a stopped CPU fan is watched like a stopped pump", p)
    expect("CPU fan at 0 rpm" in log and "would power off in 3s" in log, "same trip rule (CPU hot -> short countdown)", p)
    return p


def health_history(rig, days, rpm_at, per_day=24, band="light"):
    """Pre-write `days` of health samples (newest last). rpm_at(days_ago) -> median rpm for that sample."""
    now = datetime.now()
    lines = []
    for i in range(days * per_day, 0, -1):
        ago = i / per_day
        at = (now - timedelta(days=ago)).astimezone().isoformat()
        lines.append(json.dumps({"at": at, "bands": {band: [round(rpm_at(ago), 1), 45.0, 60]}}))
    with open(os.path.join(rig.root, "health-samples.jsonl"), "w", encoding="utf-8") as f:
        f.write("\n".join(lines) + "\n")


class FakeSmtp:
    """A tiny local mail server (plain SMTP, accepts any login) that keeps what it receives."""

    def __init__(self):
        self.mails, self.logins = [], []
        box = self

        class H(socketserver.StreamRequestHandler):
            def handle(self):
                def say(t):
                    self.wfile.write((t + "\r\n").encode())
                say("220 fake ESMTP")
                data, in_data, auth = [], False, None
                while True:
                    raw = self.rfile.readline()
                    if not raw:
                        return
                    line = raw.decode(errors="replace").rstrip("\r\n")
                    if in_data:
                        if line == ".":
                            in_data = False; box.mails.append("\n".join(data)); data = []; say("250 queued")
                        else:
                            data.append(line[1:] if line.startswith("..") else line)
                        continue
                    if auth:
                        box.logins.append(line); auth -= 1
                        say("334 UGFzc3dvcmQ6" if auth else "235 ok")
                        continue
                    cmd = line.upper()
                    if cmd.startswith("EHLO"):
                        self.wfile.write(b"250-fake\r\n250 AUTH LOGIN\r\n")
                    elif cmd.startswith("AUTH LOGIN"):
                        parts = line.split()
                        if len(parts) > 2:      # user name sent with the command
                            box.logins.append(parts[2]); auth = 1; say("334 UGFzc3dvcmQ6")
                        else:
                            auth = 2; say("334 VXNlcm5hbWU6")
                    elif cmd == "DATA":
                        in_data = True; say("354 go")
                    elif cmd == "QUIT":
                        say("221 bye"); return
                    else:
                        say("250 ok")

        self.server = socketserver.ThreadingTCPServer(("127.0.0.1", 0), H)
        self.server.daemon_threads = True
        self.port = self.server.server_address[1]
        threading.Thread(target=self.server.serve_forever, daemon=True).start()

    def subjects(self):
        return [l for m in self.mails for l in m.split("\n") if l.lower().startswith("subject:")]


def free_port():
    s = socket.socket(); s.bind(("127.0.0.1", 0)); p = s.getsockname()[1]; s.close()
    return p


def mail_json(port, enabled=True):
    return {"Enabled": enabled, "Server": "127.0.0.1", "Port": port, "Security": "none", "Username": "alerts@example.com",
            "Password": "hunter2-test", "From": "alerts@example.com", "To": "me@example.com"}


def write_mail(rig, port, enabled=True):
    w(os.path.join(rig.root, "mail.json"), json.dumps(mail_json(port, enabled)))


def t_health_dying_pump():
    r = Rig("health_dying")
    health_history(r, 30, lambda ago: 2050 if ago <= 3 else 2400)
    r.start(duration=6)
    time.sleep(4); st = r.status()
    r.wait(); log = r.log(); p = []
    expect("EARLY WARNING: Your pump has run 15% slower" in log, "15% slower for 3 days -> early warning", p)
    expect("slower than its normal" in ((st.get("healthWarning") or {}).get("text") or ""), "warning handed to the tray", p)
    expect(st.get("state") in ("ARMED", "STARTING") and not st.get("degraded"), f"a warning only ({st.get('state')})", p)
    return p


def t_health_slow_decline_and_raise():
    a = Rig("health_decline"); health_history(a, 37, lambda ago: 2400 if ago >= 23 else 2400 - (23 - ago) * 20)
    b = Rig("health_raised"); health_history(b, 30, lambda ago: 2800 if ago <= 3 else 2400)
    c = Rig("health_steady"); health_history(c, 30, lambda ago: 2400 + (ago % 1) * 20)
    d = Rig("health_learning"); health_history(d, 5, lambda ago: 2000 if ago <= 2 else 2400)
    for x in (a, b, c, d):
        x.start(duration=5)
    for x in (a, b, c, d):
        x.wait()
    p = []
    expect("Learned the normal pump speed: 2,400 rpm" in a.log() and "EARLY WARNING" in a.log(), "slow decline caught against the fixed normal", p)
    expect("Learning the new normal" in b.log() and "EARLY WARNING" not in b.log(), "faster = setting changed -> relearn", p)
    expect("EARLY WARNING" not in c.log(), "steady pump never warns", p)
    expect("EARLY WARNING" not in d.log(), "no warning before 14 days", p)
    return p


def t_health_samples_and_relearn():
    r = Rig("health_sampling", HealthSampleMinutes=0.1, HealthMinReadings=3, HealthWarmupSeconds=0).start(duration=22)
    time.sleep(14)
    r.control("health-reset.json", "at", datetime.now())
    r.wait(); p = []
    path = os.path.join(r.root, "health-samples.jsonl")
    lines = open(path, encoding="utf-8").read().split("\n") if os.path.exists(path) else []
    lines = [l for l in lines if l.strip()]
    expect("Relearn requested from the tray" in r.log(), "relearn request handled", p)
    expect(len(lines) >= 1, f"samples written after the relearn ({len(lines)})", p)
    if lines:
        s = json.loads(lines[-1])
        expect("all" in s["bands"] and abs(s["bands"]["all"][0] - 2400) < 1, f"sample = median rpm of the window ({s})", p)
    return p


def t_mail_on_trip_password_scrambled():
    smtp = FakeSmtp()
    r = Rig("mail_trip", temp=92)
    write_mail(r, smtp.port)
    r.start(duration=18)
    time.sleep(5); r.set_pump(0)
    r.wait(); log = r.log(); p = []
    text = open(os.path.join(r.root, "mail.json"), encoding="utf-8").read()
    expect("hunter2-test" not in text and '"PasswordProtected"' in text, "password scrambled in mail.json", p)
    expect(any("POWERING OFF" in s for s in smtp.subjects()), f"power-off email received ({smtp.subjects()})", p)
    expect(any(base64.b64decode(l).decode(errors="replace") == "hunter2-test" for l in smtp.logins if l), "server got the real password back", p)
    expect("WOULD POWER OFF NOW" in log, "the power-off still happened", p)
    return p


def t_mail_reload_bad_json_and_warning():
    smtp = FakeSmtp()
    r = Rig("mail_reload")
    write_mail(r, smtp.port, enabled=False)
    r.start(duration=30)
    time.sleep(4); write_mail(r, smtp.port)
    time.sleep(6)
    with open(os.path.join(r.root, "mail.json"), "w", encoding="utf-8") as f:
        f.write('{ "Enabled": true, "Server": ')
    time.sleep(5); r.set_pump(400)
    r.wait(); log = r.log(); p = []
    expect("Email alerts on" in log, "change picked up without a restart", p)
    expect("has a mistake" in log, "broken mail.json logged", p)
    expect(any("Warning" in s and "slower" in s for s in smtp.subjects()), f"warning email sent with the old settings ({smtp.subjects()})", p)
    return p


def t_mail_test_email_and_server_down():
    smtp = FakeSmtp()
    a = Rig("mail_test"); write_mail(a, smtp.port)
    out = subprocess.run([sys.executable, SRC, "--test-mail", "--mail", os.path.join(a.root, "mail.json"), "--data-dir", a.root,
                          "--log", os.path.join(a.root, "pg.log")], capture_output=True, text=True, timeout=60)
    b = Rig("mail_tray_test"); write_mail(b, smtp.port)
    b.start(duration=10)
    time.sleep(4); b.control("mail-test.json", "at", datetime.now())
    c = Rig("mail_down", temp=92); write_mail(c, free_port())
    c.start(duration=16)
    time.sleep(5); c.set_pump(0)
    b.wait(); c.wait(); p = []
    expect(out.returncode == 0 and "Test email sent" in out.stdout, f"--test-mail ({out.returncode}: {out.stdout.strip()} {out.stderr[-200:]})", p)
    expect(sum("test email" in s for s in smtp.subjects()) == 2, f"two test emails ({smtp.subjects()})", p)
    log = c.log()

    def at(text):
        line = next((l for l in log.splitlines() if text in l), None)
        return datetime.strptime(line[:19], "%Y-%m-%d %H:%M:%S") if line else None
    t0, t1 = at("would power off in 3s"), at("WOULD POWER OFF NOW")
    took = (t1 - t0).total_seconds() if t0 and t1 else 999
    expect(took <= 4, f"3 s countdown kept with the mail server down ({took} s)", p)
    expect("Mail: not sent" in log, "failed send logged", p)
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
