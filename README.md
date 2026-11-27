# LoopMedic

**Powers your PC off if your CPU cooler stops, before the CPU cooks.** Works with AIO / water coolers
(it watches the pump) and air coolers (it watches the CPU fan). Setup asks which one you have.

<p align="center"><img src="docs/panel.png" alt="The LoopMedic tray panel: live pump speed and CPU temperature with graphs, trip limits, today's peaks and every fan header" width="340"></p>

LoopMedic runs quietly from startup and checks your pump (or CPU fan) and the CPU temperature every 2 seconds.

| When...                                                  | for...                    | then...                                                 |
|----------------------------------------------------------|---------------------------|---------------------------------------------------------|
| the pump / CPU fan has **stopped** (150 rpm or less)     | 10 s                      | an alarm with a 30 s countdown, then a forced power-off |
| the CPU is at or above its limit                         | 30 s (60 s on some chips) | same                                                    |
| the pump / CPU fan is **slower** than usual              | 10 s                      | a warning only                                          |

It only reads sensors: it never changes a fan or pump speed and leaves your cooler's app alone.

**Also:** optional email alerts through your own mail server · six tray themes · a self-check at every
boot and a self-test every month · an **experimental** early warning for a pump or fan that is slowly
slowing down (off unless you turn it on in setup).

> **No warranty.** LoopMedic is an *extra* layer of protection on top of the ones your PC already has (BIOS
> fan-fail and temperature shutdowns, the CPU's own thermal limits). It can miss a failure, and it can power
> your PC off when nothing is wrong. Use it at your own risk; see [LICENSE.md](LICENSE.md).

## Download

From the [Releases](../../releases) page:

- **Windows 10 / 11:** `LoopMedic.zip` - unzip, run `Install.bat`, follow the setup.
  Guide: [windows/package/README.txt](windows/package/README.txt)
- **Linux (systemd):** `LoopMedic-Linux.tar.gz` - unpack, run `bash install.sh` as your normal user.
  Guide: [linux/README.txt](linux/README.txt)

**Updating:** click the version number at the top of the tray panel. Nothing checks in the background.
Releases are signed, and the tray refuses a download whose signature doesn't match.

<details>
<summary><b>For developers</b>: repo layout, building, tests</summary>

```
windows/   C# watchdog (src/), tray app (tray/), installer + setup scripts, tests
linux/     Python (standard library) systemd service, PySide6 tray, setup, tests
tools/     pack-release.py (builds + signs both packages), panel-screenshot.py, real-tests/
```

- **Windows:** put the [LibreHardwareMonitorLib](https://github.com/LibreHardwareMonitor/LibreHardwareMonitor)
  0.9.6 DLLs in `windows/lhm/`, run `windows/build.ps1`, then `windows/tests/run-tests.py` (fake sensors,
  never powers off).
- **Linux:** nothing to build; `linux/tests/run-tests.py`.
- **Packages:** `python tools/pack-release.py [folder]`. Needs the signed [PawnIO](https://pawnio.eu)
  installer in `windows/package/pawnio/` and the author's release key, so packages you build yourself won't
  pass the trays' updater.

How it works, including the threat model: [windows/ARCHITECTURE.md](windows/ARCHITECTURE.md).
What changed: [windows/CHANGELOG.md](windows/CHANGELOG.md) · [linux/CHANGELOG.md](linux/CHANGELOG.md).

</details>

## License

[PolyForm Noncommercial 1.0.0](LICENSE.md): free to use, change and share for any noncommercial purpose.
Commercial use needs the author's permission. Third-party parts (LibreHardwareMonitorLib, PawnIO) keep their
own licenses: [THIRD-PARTY-NOTICES.txt](windows/package/THIRD-PARTY-NOTICES.txt).
