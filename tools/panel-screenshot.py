r"""Take docs/panel.png: the tray panel running on MADE-UP sensor values (no real hardware details).

    python tools/panel-screenshot.py        (after windows/build.ps1; Windows only; nothing powers off)

Runs windows\bin\LoopMedic.exe as a watchdog on fake sensors in a temp folder, opens a second tray panel for
that folder, feeds it ~70 s of gently varying readings so the graphs fill in, then captures the panel window.
Needs Pillow: the capture's 1-px Windows border and rounded corners (desktop shows through them) are
replaced with a clean, even border and transparent corners.
"""
import ctypes, ctypes.wintypes as wt, json, math, os, random, shutil, subprocess, sys, tempfile, time
from PIL import Image, ImageDraw

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
BIN = os.path.join(ROOT, "windows", "bin")
OUT = os.path.join(ROOT, "docs", "panel.png")
PUMP, TEMP = "/lpc/demo/0/fan/0", "/cpu/demo/0/temperature/0"
FANS = [("Fan #2", 1180), ("Fan #3", 940), ("Fan #4", 940), ("Fan #5", 0)]

work = tempfile.mkdtemp(prefix="loopmedic-shot-")
os.makedirs(os.path.join(work, "control"))
shutil.copy(os.path.join(ROOT, "windows", "cpu-limits.json"), work)
cfg = json.load(open(os.path.join(ROOT, "windows", "package", "config.template.json"), encoding="utf-8"))
cfg.update({"PumpSensorId": PUMP, "CpuTempSensorId": TEMP, "CpuName": "AMD Ryzen 7 7800X3D", "CpuMaxTempC": 84})
json.dump(cfg, open(os.path.join(work, "config.json"), "w"))
fake = os.path.join(work, "fake-sensors.json")


def push(t):
    pump = 2400 + 25 * math.sin(t / 9) + random.uniform(-12, 12)
    temp = 46 + 6 * math.sin(t / 17) + random.uniform(-0.6, 0.6)
    sensors = [{"id": PUMP, "name": "Fan #1", "type": "Fan", "hw": "SuperIO", "value": round(pump, 1)},
               {"id": TEMP, "name": "Core (Tctl/Tdie)", "type": "Temperature", "hw": "Cpu", "value": round(temp, 1)}]
    for i, (name, rpm) in enumerate(FANS):
        sensors.append({"id": f"/lpc/demo/0/fan/{i + 1}", "name": name, "type": "Fan", "hw": "SuperIO",
                        "value": rpm and round(rpm + 20 * math.sin(t / 7 + i), 1)})
    doc = {"cpuName": "AMD Ryzen 7 7800X3D", "sensors": sensors, "clockOffsetSeconds": 0, "privilegeFail": False}
    tmp = fake + ".tmp"
    json.dump(doc, open(tmp, "w"))
    os.replace(tmp, fake)


def clean_edges(path, radius=8, bg=(28, 28, 33), border=(58, 58, 64), ss=4):
    """Replace the captured window border and corner bleed with a uniform border and transparent corners."""
    im = Image.open(path).convert("RGB")
    w, h = im.size

    def rounded(inset, r):
        m = Image.new("L", (w * ss, h * ss), 0)
        ImageDraw.Draw(m).rounded_rectangle((inset * ss, inset * ss, (w - inset) * ss - 1, (h - inset) * ss - 1),
                                            r * ss, fill=255)
        return m.resize((w, h), Image.LANCZOS)

    inner = Image.composite(im, Image.new("RGB", (w, h), bg), rounded(1, radius - 1))
    out = Image.composite(inner, Image.new("RGB", (w, h), border), rounded(1, radius - 1)).convert("RGBA")
    out.putalpha(rounded(0, radius))
    out.save(path, optimize=True)


push(0)
worker = subprocess.Popen([os.path.join(BIN, "LoopMedic.exe"), "--worker", "--root", work, "--fake-sensors", fake,
                           "--duration", "150"])   # healthy made-up values can never trip; no --dry-run so the badge reads ARMED
time.sleep(3)
tray = subprocess.Popen([os.path.join(BIN, "LoopMedicTray.exe"), "--dir", work, "--open"])
try:
    t0 = time.time()
    while time.time() - t0 < 70:
        push(time.time() - t0)
        time.sleep(1)

    # find the tray's visible panel window and capture its rectangle
    user32 = ctypes.windll.user32
    user32.SetProcessDPIAware()
    found = []

    @ctypes.WINFUNCTYPE(wt.BOOL, wt.HWND, wt.LPARAM)
    def cb(h, _):
        pid = wt.DWORD()
        user32.GetWindowThreadProcessId(h, ctypes.byref(pid))
        if pid.value == tray.pid and user32.IsWindowVisible(h):
            r = wt.RECT()
            user32.GetWindowRect(h, ctypes.byref(r))
            if r.right - r.left > 200 and r.bottom - r.top > 200:
                found.append((r.left, r.top, r.right, r.bottom))
        return True

    user32.EnumWindows(cb, 0)
    if not found:
        sys.exit("panel window not found (did it close when it lost focus? run again and don't click elsewhere)")
    l, t, r, b = found[0]
    os.makedirs(os.path.dirname(OUT), exist_ok=True)
    ps = ("Add-Type -AssemblyName System.Drawing; "
          f"$b = New-Object System.Drawing.Bitmap {r - l},{b - t}; $g = [System.Drawing.Graphics]::FromImage($b); "
          f"$g.CopyFromScreen({l},{t},0,0,$b.Size); $b.Save('{OUT}', [System.Drawing.Imaging.ImageFormat]::Png)")
    subprocess.run(["powershell", "-NoProfile", "-Command", ps], check=True)
    clean_edges(OUT)
    print("saved", OUT, f"({r - l}x{b - t})")
finally:
    tray.kill()
    worker.kill()
    time.sleep(1)
    shutil.rmtree(work, ignore_errors=True)
