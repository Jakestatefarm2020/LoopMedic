r"""Build both LoopMedic release packages from this repo, with a privacy scan.

    python tools/pack-release.py [output folder]      (default: your Desktop)

Windows: LoopMedic\ + LoopMedic.zip  (source + build.ps1; setup compiles on the user's PC; CRLF text)
Linux:   LoopMedic-Linux\ + LoopMedic-Linux.tar.gz (LF only, scripts executable)

Needs, besides the repo:
  windows/lhm/                      LibreHardwareMonitorLib 0.9.6 DLLs (or set LOOPMEDIC_LHM to their folder)
  windows/package/pawnio/PawnIO_setup.exe   the official signed PawnIO installer (git-ignored)
Optional: tools/private-patterns.txt (git-ignored), one regex per line - extra words that must never
ship (your name, email, hardware). Generic checks for personal paths always run.
"""
import os, re, shutil, sys, tarfile, zipfile

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
WIN = os.path.join(ROOT, "windows")
LNX = os.path.join(ROOT, "linux")
OUT = sys.argv[1] if len(sys.argv) > 1 else os.path.join(os.path.expanduser("~"), "Desktop")
LHM = os.environ.get("LOOPMEDIC_LHM") or os.path.join(WIN, "lhm")
PAWNIO = os.path.join(WIN, "package", "pawnio")
LICENSE = os.path.join(ROOT, "LICENSE.md")

PRIVATE = [rb"[A-Za-z]:\\+Users\\+", rb"/home/[A-Za-z]", rb"@gmail\.com"]
extra = os.path.join(ROOT, "tools", "private-patterns.txt")
if os.path.exists(extra):
    PRIVATE += [l.strip().encode() for l in open(extra, encoding="utf-8") if l.strip() and not l.startswith("#")]
else:
    print("note: tools/private-patterns.txt not found - only the generic privacy checks run")
problems = []


def scan(name, data):
    for pat in PRIVATE:
        if re.search(pat, data, re.I):
            problems.append(f"{name}: private text matching {pat.decode()}")


for need in (os.path.join(LHM, "LibreHardwareMonitorLib.dll"), os.path.join(PAWNIO, "PawnIO_setup.exe")):
    if not os.path.exists(need):
        sys.exit("missing: " + need)

# ------------------------------------------------------------------ Windows
WIN_FILES = {  # release path: path in windows/
    "Install.bat": "package/Install.bat", "Uninstall.bat": "package/Uninstall.bat",
    "Watch-Pump.bat": "package/Watch-Pump.bat", "Watch-Pump.ps1": "package/Watch-Pump.ps1",
    "Setup-LoopMedic.ps1": "package/Setup-LoopMedic.ps1", "README.txt": "package/README.txt",
    "THIRD-PARTY-NOTICES.txt": "package/THIRD-PARTY-NOTICES.txt", "config.json": "package/config.template.json",
    "Install-LoopMedic.ps1": "Install-LoopMedic.ps1", "build.ps1": "build.ps1", "cpu-limits.json": "cpu-limits.json",
    "src/LoopMedic.cs": "src/LoopMedic.cs", "tray/LoopMedicTray.cs": "tray/LoopMedicTray.cs",
    "LICENSE.md": LICENSE, "CHANGELOG.md": "CHANGELOG.md",
}
if os.path.exists(os.path.join(WIN, "tray", "LoopMedic.ico")):
    WIN_FILES["tray/LoopMedic.ico"] = "tray/LoopMedic.ico"
wdst = os.path.join(OUT, "LoopMedic")
shutil.rmtree(wdst, ignore_errors=True)
for rel, src in WIN_FILES.items():
    data = open(os.path.join(WIN, src), "rb").read()
    scan(rel, data)
    if not rel.endswith(".ico"):
        data = data.replace(b"\r\n", b"\n").replace(b"\n", b"\r\n")   # Windows line endings (cmd needs them for .bat)
    p = os.path.join(wdst, rel)
    os.makedirs(os.path.dirname(p), exist_ok=True)
    open(p, "wb").write(data)
shutil.copytree(PAWNIO, os.path.join(wdst, "pawnio"))
os.makedirs(os.path.join(wdst, "lhm"))
for f in os.listdir(LHM):
    if f.lower().endswith(".dll"):
        shutil.copy2(os.path.join(LHM, f), os.path.join(wdst, "lhm", f))
wzip = os.path.join(OUT, "LoopMedic.zip")
if os.path.exists(wzip):
    os.remove(wzip)
with zipfile.ZipFile(wzip, "w", zipfile.ZIP_DEFLATED) as z:
    for root, _, files in os.walk(wdst):
        for f in files:
            full = os.path.join(root, f)
            z.write(full, "LoopMedic/" + os.path.relpath(full, wdst).replace("\\", "/"))

# ------------------------------------------------------------------ Linux
LNX_FILES = {f: f for f in ["install.sh", "uninstall.sh", "watch-fans.sh", "config.json", "README.txt", "CHANGELOG.md",
                            "loopmedic/loopmedic.py", "loopmedic/loopmedic_tray.py", "loopmedic/setup_loopmedic.py",
                            "loopmedic/cpu-limits.json", "tests/run-tests.py"]}
LNX_FILES["LICENSE.md"] = LICENSE
EXEC = {"install.sh", "uninstall.sh", "watch-fans.sh", "loopmedic/loopmedic.py", "loopmedic/loopmedic_tray.py",
        "loopmedic/setup_loopmedic.py", "tests/run-tests.py"}
ldst = os.path.join(OUT, "LoopMedic-Linux")
shutil.rmtree(ldst, ignore_errors=True)
for rel, src in LNX_FILES.items():
    data = open(os.path.join(LNX, src), "rb").read().replace(b"\r\n", b"\n")
    scan("linux " + rel, data)
    p = os.path.join(ldst, rel)
    os.makedirs(os.path.dirname(p), exist_ok=True)
    open(p, "wb").write(data)
ltgz = os.path.join(OUT, "LoopMedic-Linux.tar.gz")
with tarfile.open(ltgz, "w:gz") as tar:
    for d in ("LoopMedic-Linux", "LoopMedic-Linux/loopmedic", "LoopMedic-Linux/tests"):
        ti = tarfile.TarInfo(d); ti.type = tarfile.DIRTYPE; ti.mode = 0o755
        tar.addfile(ti)
    for rel in LNX_FILES:
        p = os.path.join(ldst, rel)
        ti = tar.gettarinfo(p, arcname="LoopMedic-Linux/" + rel)
        ti.mode = 0o755 if rel in EXEC else 0o644
        ti.uid = ti.gid = 1000; ti.uname = ti.gname = "user"
        with open(p, "rb") as fh:
            tar.addfile(ti, fh)

print("Windows zip:", os.path.getsize(wzip), "bytes,", len(zipfile.ZipFile(wzip).namelist()), "files")
print("Linux tar.gz:", os.path.getsize(ltgz), "bytes")
print("problems:", problems or "none")
sys.exit(1 if problems else 0)
