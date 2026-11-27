#!/usr/bin/env python3
"""
LoopMedic tray icon for Linux (made for KDE Plasma) - by Jacob Stiltner, (c) 2026

Shows the shield icon, a live panel (pump, CPU, every fan) and the trip pop-up
with a countdown. It only READS /run/loopmedic/status.json and drops small
request files into /var/lib/loopmedic/control. The power-off itself is done by
the root service, so closing this app never turns the protection off.

Needs PySide6 (CachyOS / Arch: sudo pacman -S pyside6).

Options (mostly for testing):
  --status FILE  --control DIR  --log FILE   other locations
  --open                                     open the panel at start
  --shot-panel PNG / --shot-trip PNG         render to a picture and exit
"""
import argparse
import base64
import getpass
import grp
import hashlib
import json
import os
import shutil
import subprocess
import sys
import tarfile
import tempfile
import threading
import urllib.error
import urllib.request
from datetime import datetime, timedelta

from PySide6.QtCore import QObject, QPointF, QRectF, Qt, QTimer, QUrl, Signal
from PySide6.QtGui import (QAction, QBrush, QColor, QCursor, QDesktopServices, QFont, QFontMetricsF, QGuiApplication,
                           QIcon, QLinearGradient, QPainter, QPainterPath, QPen, QPixmap)
from PySide6.QtWidgets import QApplication, QMenu, QMessageBox, QSystemTrayIcon, QWidget

AUTHOR = "by Jacob Stiltner"

try:
    from loopmedic import VERSION as TRAY_VERSION   # loopmedic.py sits next to this file (/opt/loopmedic)
except Exception:
    TRAY_VERSION = "0"

# ------------------------------------------------------------------ updates
# Only when the user clicks the version in the panel. Asks GitHub for the latest release, downloads
# LoopMedic-Linux.tar.gz + .sig and refuses anything the LoopMedic release key didn't sign.
REPO = "Jakestatefarm2020/LoopMedic"
# Public half of the release signing key (RSA-3072); the private half never leaves the author's PC.
KEY_N = int.from_bytes(base64.b64decode(
    "90PksaPHuTI9JIRU4CFL7dWDKaUDSGOrGCzGP8uwaAW8UqZsEfGJY9aeQY9cI1NcPOrwDWSBJZgkYC3brpgKLwzt1RCtwGS4+cuB/bEsYfFj7y5wUv58k/iXxSwJ"
    "hYZhrVBhnoU48g/FEL/oZyQrqJsLw139aAW54AxxFS4Q2fehI2nFuTiB6vRvOQILXvW756vnuccAILq1Duxfn+pZpfZq5111pfFopqaJaE1y9nEZ98trjDCgJuXI"
    "9WSIS2enYprBs3Wh34tkbjrOV/pPjzHxiuZUD7i+xfS2UhMmUwTQ5ifyL3kmNO7mVnoKxshSen5JhEhhnN5XQCNa3Y3PcGEV+CTrZn4hs1aNp4PX7HZyA1nu6XFO"
    "MVn8G5Sy+Meq4YxrvwZuQ1KPpReTIsyCv4gHhOfZJVgiNvHIvmH7L5TywQnYhV5EMc0rt/VJNw+fCOCjAeHdHWUpiaGb5WpAqziWP3ExDppYTjpGo7cmf38wHBxS"
    "NomRqOl6WXpjLZnF"), "big")
KEY_E = 65537
SHA256_PREFIX = bytes.fromhex("3031300d060960864801650304020105000420")


def verify_signature(data, sig):
    """RSA PKCS#1 v1.5 / SHA-256 check against the built-in release key (standard library only)."""
    k = (KEY_N.bit_length() + 7) // 8
    if len(sig) != k:
        return False
    di = SHA256_PREFIX + hashlib.sha256(data).digest()
    expected = b"\x00\x01" + b"\xff" * (k - len(di) - 3) + b"\x00" + di
    return pow(int.from_bytes(sig, "big"), KEY_E, KEY_N).to_bytes(k, "big") == expected


def version_tuple(v):
    try:
        return tuple(int(x) for x in v.strip().lstrip("vV").split("."))
    except ValueError:
        return None


def http_get(url, accept=None):
    headers = {"User-Agent": "LoopMedic-tray"}
    if accept:
        headers["Accept"] = accept
    with urllib.request.urlopen(urllib.request.Request(url, headers=headers), timeout=30) as r:
        return r.read()


def check_latest(current):
    """Returns (release dict or None if up to date, error text or None)."""
    try:
        d = json.loads(http_get(f"https://api.github.com/repos/{REPO}/releases/latest", "application/vnd.github+json"))
    except urllib.error.HTTPError as e:
        return None, "No release has been published on GitHub yet." if e.code == 404 else f"GitHub answered {e.code}."
    except (urllib.error.URLError, OSError, ValueError) as e:
        return None, f"Couldn't reach GitHub ({e})."
    rel = {"version": str(d.get("tag_name") or "").lstrip("vV"), "notes": d.get("body") or "",
           "page": d.get("html_url") or f"https://github.com/{REPO}/releases"}
    for a in d.get("assets") or []:
        if a.get("name") == "LoopMedic-Linux.tar.gz":
            rel["pkg"] = a.get("browser_download_url")
        if a.get("name") == "LoopMedic-Linux.tar.gz.sig":
            rel["sig"] = a.get("browser_download_url")
    new, now = version_tuple(rel["version"]), version_tuple(current)
    return (rel if new and now and new > now else None), None


def download_update(rel):
    """Download + signature check + unpack. Returns the new setup_loopmedic.py, or raises with a readable message."""
    if not rel.get("pkg") or not rel.get("sig"):
        raise RuntimeError("This release has no signed Linux package - download it from GitHub instead.")
    data = http_get(rel["pkg"])
    sig = base64.b64decode(http_get(rel["sig"]).strip())
    if not verify_signature(data, sig):
        raise RuntimeError("The download FAILED the signature check, so it was not installed. Please tell the author.")
    work = tempfile.mkdtemp(prefix="loopmedic-update-")
    pkg = os.path.join(work, "LoopMedic-Linux.tar.gz")
    with open(pkg, "wb") as f:
        f.write(data)
    with tarfile.open(pkg) as tar:
        for m in tar.getmembers():   # nothing outside the folder, no links
            if m.name.startswith("/") or ".." in m.name.split("/") or not (m.isfile() or m.isdir()):
                raise RuntimeError("The package has an unexpected file in it - not installing.")
        tar.extractall(work)
    setup = os.path.join(work, "LoopMedic-Linux", "loopmedic", "setup_loopmedic.py")
    if not os.path.exists(setup):
        raise RuntimeError("The package has no setup script - download it from GitHub and run install.sh.")
    return setup


def plain_notes(md):
    lines = [l.lstrip("#").strip() if l.startswith("#") else l for l in (md or "").replace("\r\n", "\n").split("\n")]
    text = "\n".join(lines).replace("**", "").replace("`", "").strip()
    return text or "(no notes for this release)"


class Bridge(QObject):
    """Hands results from download threads back to the Qt thread."""
    done = Signal(object)


# ------------------------------------------------------------------ theme
class T:
    Bg = QColor(28, 28, 33)
    Card = QColor(38, 38, 45)
    Line = QColor(55, 55, 64)
    Text = QColor(236, 236, 240)
    Dim = QColor(150, 150, 160)
    Green = QColor(76, 194, 107)
    Amber = QColor(240, 168, 48)
    Red = QColor(229, 72, 77)
    Blue = QColor(76, 154, 255)
    Link = QColor(122, 176, 255)   # gentle link blue (version = update check)
    Grey = QColor(138, 143, 152)
    Off = QColor(90, 90, 98)
    Hot = QColor(58, 58, 68)

    @staticmethod
    def for_state(s):
        return {"ARMED": T.Green, "WATCHING": T.Amber, "DEGRADED": T.Amber, "TRIPPED": T.Red, "TEST": T.Blue,
                "STARTING": T.Blue, "PAUSED": T.Grey}.get(s, T.Off)

    @staticmethod
    def label(s):
        return {"ARMED": "ARMED", "WATCHING": "WATCHING", "DEGRADED": "DEGRADED", "TRIPPED": "TRIPPED", "TEST": "TEST",
                "STARTING": "STARTING", "PAUSED": "PAUSED"}.get(s, "OFFLINE")


FAMILY = None


def font(px, bold=False):
    f = QFont(FAMILY)
    f.setPixelSize(int(round(px)))
    f.setWeight(QFont.DemiBold if bold else QFont.Normal)
    return f


def rounded(r, rad):
    p = QPainterPath()
    p.addRoundedRect(r, rad, rad)
    return p


def text_w(f, s):
    return QFontMetricsF(f).horizontalAdvance(s)


def text_h(f):
    return QFontMetricsF(f).height()


def fmt_n(v):
    return f"{v:,.0f}"


def parse_when(s):
    if not isinstance(s, str) or not s:
        return None
    try:
        t = s.strip().replace("Z", "+00:00")
        # .NET writes 7 fraction digits; Python 3.10 wants 6
        if "." in t:
            head, rest = t.split(".", 1)
            digits = "".join(ch for ch in rest if ch.isdigit())
            tail = rest[len(digits):]
            t = head + "." + (digits + "000000")[:6] + tail
        dt = datetime.fromisoformat(t)
        return dt.astimezone().replace(tzinfo=None) if dt.tzinfo else dt
    except ValueError:
        return None


# ------------------------------------------------------------------ status
class Status:
    def __init__(self):
        self.at = datetime.min
        self.state = "OFFLINE"
        self.dry_run = False
        self.air = False        # air cooler mode: the CPU fan is watched instead of a pump
        self.version = ""
        self.degraded = []
        self.pump_rpm = self.cpu_temp = self.pump_min = self.cpu_max = self.pump_stopped = None
        self.last_off_at = self.last_off_reason = None
        self.selftest_at, self.selftest_ok = None, None
        self.day_max_temp = self.day_min_pump = None
        self.paused_until = self.trip_deadline = None
        self.trip_kind = self.trip_reason = None
        self.cpu_name = ""
        self.fans = []
        self.read_ok = False    # file read and parsed (False = missing, mid-write or garbled)

    @property
    def cooler_title(self):
        return "CPU fan" if self.air else "Pump"

    @property
    def cooler(self):
        return "CPU fan" if self.air else "pump"

    @staticmethod
    def load(path):
        st = Status()
        try:
            with open(path, encoding="utf-8-sig") as f:
                d = json.load(f)
        except (OSError, ValueError):
            return st
        num = lambda k: float(d[k]) if isinstance(d.get(k), (int, float)) and not isinstance(d.get(k), bool) else None
        st.at = parse_when(d.get("at")) or datetime.min
        st.state = str(d.get("state") or "OFFLINE")
        st.dry_run = bool(d.get("dryRun"))
        st.air = d.get("coolerType") == "air"
        st.version = str(d.get("version") or "")
        dg = d.get("degraded") or []
        st.degraded = [str(x) for x in (dg if isinstance(dg, list) else [dg]) if x]
        st.pump_rpm, st.cpu_temp = num("pumpRpm"), num("cpuTemp")
        st.pump_min, st.cpu_max = num("pumpMinRpm"), num("cpuMaxTemp")
        st.pump_stopped = num("pumpStoppedRpm")
        lo = d.get("lastPowerOff")
        if isinstance(lo, dict):
            st.last_off_at, st.last_off_reason = parse_when(lo.get("at")), str(lo.get("reason") or "")
        sti = d.get("selfTest")
        if isinstance(sti, dict):
            st.selftest_at, st.selftest_ok = parse_when(sti.get("at")), bool(sti.get("ok"))
        st.day_max_temp, st.day_min_pump = num("dayMaxTemp"), num("dayMinPump")
        st.paused_until = parse_when(d.get("pausedUntil"))
        st.trip_deadline = parse_when(d.get("tripDeadline"))
        st.trip_kind, st.trip_reason = d.get("tripKind"), d.get("tripReason")
        st.cpu_name = str(d.get("cpuName") or "")
        for f in d.get("fans") or []:
            if isinstance(f, dict):
                r = f.get("rpm")
                st.fans.append({"name": str(f.get("name") or "?"),
                                "rpm": float(r) if isinstance(r, (int, float)) and not isinstance(r, bool) else None,
                                "pump": bool(f.get("pump"))})
        st.read_ok = True
        if (datetime.now() - st.at).total_seconds() > 20:   # 20 s without data = offline
            st.state = "OFFLINE"
        return st


# ------------------------------------------------------------------ icon
def make_icon(state, sz=64):
    pm = QPixmap(sz, sz)
    pm.fill(Qt.transparent)
    g = QPainter(pm)
    g.setRenderHint(QPainter.Antialiasing)
    w, m = float(sz), sz * 0.06
    p = QPainterPath()
    p.moveTo(w / 2, m)
    p.lineTo(w - m * 1.5, w * 0.2)
    p.lineTo(w - m * 1.5, w * 0.5)
    p.cubicTo(w - m * 2, w * 0.78, w * 0.68, w * 0.9, w / 2, w - m)
    p.cubicTo(w * 0.32, w * 0.9, m * 2, w * 0.78, m * 1.5, w * 0.5)
    p.lineTo(m * 1.5, w * 0.2)
    p.closeSubpath()
    g.fillPath(p, T.for_state(state))
    g.setPen(QPen(QColor(0, 0, 0, 160), max(1.0, sz / 16)))
    g.drawPath(p)
    glyph = {"TRIPPED": "!", "WATCHING": "!", "PAUSED": "II", "OFFLINE": "?", "DEGRADED": "?", "TEST": "T"}.get(state, "")
    if not glyph:
        pen = QPen(Qt.white, sz / 8)
        pen.setCapStyle(Qt.RoundCap)
        pen.setJoinStyle(Qt.RoundJoin)
        g.setPen(pen)
        g.drawPolyline([QPointF(w * 0.32, w * 0.5), QPointF(w * 0.46, w * 0.64), QPointF(w * 0.7, w * 0.36)])
    else:
        f = QFont(FAMILY)
        f.setPixelSize(int(sz * 0.42))
        f.setBold(True)
        g.setFont(f)
        g.setPen(Qt.white)
        g.drawText(QRectF(0, -sz * 0.02, w, w), Qt.AlignCenter, glyph)
    g.end()
    return QIcon(pm)


def sparkline(g, r, hist, c, floor):
    if len(hist) < 2:
        return
    lo, hi = min(hist), max(hist)
    pad = max((hi - lo) * 0.15, abs(hi) * 0.03, floor)   # same scaling as the Windows tray
    lo, hi = lo - pad, hi + pad
    n = len(hist)
    pts = [QPointF(r.x() + r.width() * i / (n - 1), r.bottom() - r.height() * (v - lo) / (hi - lo)) for i, v in enumerate(hist)]
    area = QPainterPath(QPointF(pts[0].x(), r.bottom()))
    for p in pts:
        area.lineTo(p)
    area.lineTo(pts[-1].x(), r.bottom())
    area.closeSubpath()
    grad = QLinearGradient(0, r.top(), 0, r.bottom())
    top = QColor(c)
    top.setAlpha(70)
    bot = QColor(c)
    bot.setAlpha(0)
    grad.setColorAt(0, top)
    grad.setColorAt(1, bot)
    g.fillPath(area, QBrush(grad))
    pen = QPen(c, 1.6)
    pen.setJoinStyle(Qt.RoundJoin)
    g.setPen(pen)
    g.drawPolyline(pts)


class Clickable:
    def __init__(self):
        self.buttons = []   # (QRectF, text, callback)
        self.hover = None

    def hit(self, pos):
        for r, t, cb in self.buttons:
            if r.contains(pos):
                return t, cb
        return None, None


# ------------------------------------------------------------------ panel
class Panel(QWidget, Clickable):
    W = 340

    def __init__(self, app):
        QWidget.__init__(self, None, Qt.Tool | Qt.FramelessWindowHint | Qt.WindowStaysOnTopHint)
        Clickable.__init__(self)
        self.app = app
        self.setAttribute(Qt.WA_TranslucentBackground)
        self.setMouseTracking(True)
        self.setWindowTitle("LoopMedic")
        self.resize(self.W, 420)
        self.need = 420

    def changeEvent(self, e):
        if e.type() == e.Type.ActivationChange and not self.isActiveWindow() and self.isVisible():
            self.hide()
            self.app.panel_hidden_at = datetime.now()
        super().changeEvent(e)

    def keyPressEvent(self, e):
        if e.key() == Qt.Key_Escape:
            self.hide()

    def mouseMoveEvent(self, e):
        t, _ = self.hit(e.position())
        if t != self.hover:
            self.hover = t
            self.setCursor(Qt.PointingHandCursor if t else Qt.ArrowCursor)
            self.update()

    def leaveEvent(self, e):
        self.hover = None
        self.update()

    def mousePressEvent(self, e):
        _, cb = self.hit(e.position())
        if cb:
            cb()

    def place(self):
        scr = QGuiApplication.screenAt(QCursor.pos()) or QGuiApplication.primaryScreen()
        wa = scr.availableGeometry()
        geo = self.app.tray.geometry()
        anchor = geo.center() if geo.isValid() and not geo.isEmpty() else QCursor.pos()
        m = 12
        x = min(max(anchor.x() - self.width() // 2, wa.left() + m), wa.right() - self.width() - m)
        y = wa.bottom() - self.height() - m if anchor.y() > wa.center().y() else wa.top() + m
        self.move(x, y)   # Wayland may ignore this and let KWin place the window

    def paintEvent(self, e):
        g = QPainter(self)
        g.setRenderHint(QPainter.Antialiasing)
        g.setRenderHint(QPainter.TextAntialiasing)
        st = self.app.current
        self.buttons = []
        W, pad, y = float(self.width()), 16.0, 14.0
        g.fillPath(rounded(QRectF(0, 0, W, self.height()), 10), T.Bg)

        fBig, fMid, fSmall, fTiny, fHead = font(22, True), font(13), font(11.5), font(10, True), font(16, True)
        # header
        g.setFont(fHead)
        g.setPen(T.Text)
        g.drawText(QPointF(pad, y + QFontMetricsF(fHead).ascent()), "LoopMedic")
        # the version doubles as "check for updates": soft blue + hand cursor on hover, click to check
        vtext = "v" + (st.version or TRAY_VERSION) + ("  checking..." if self.app.update_busy else "")
        g.setFont(fTiny)
        g.setPen(T.Link if self.hover == "#version" else T.Dim)
        vx, vbase = pad + text_w(fHead, "LoopMedic") + 5, y + QFontMetricsF(fHead).ascent()
        g.drawText(QPointF(vx, vbase), vtext)
        fm = QFontMetricsF(fTiny)
        self.buttons.append((QRectF(vx - 2, vbase - fm.ascent() - 3, text_w(fTiny, vtext) + 4, fm.height() + 6), "#version",
                             self.app.check_updates))
        sc = T.for_state(st.state)
        label = T.label(st.state) + (" (DRY RUN)" if st.dry_run else "")
        lw = text_w(fTiny, label)
        pill = QRectF(W - pad - lw - 28, y + 2, lw + 28, 22)
        bg = QColor(sc)
        bg.setAlpha(40)
        g.fillPath(rounded(pill, 11), bg)
        g.setBrush(sc)
        g.setPen(Qt.NoPen)
        g.drawEllipse(QRectF(pill.x() + 9, pill.y() + 7.5, 7, 7))
        g.setFont(fTiny)
        g.setPen(sc)
        g.drawText(QRectF(pill.x() + 20, pill.y(), lw + 4, pill.height()), Qt.AlignVCenter | Qt.AlignLeft, label)
        y += 38

        # what's weakening the protection
        if st.degraded:
            g.setFont(fSmall)
            g.setPen(T.Amber)
            for line in st.degraded:
                box = QRectF(pad, y, W - pad * 2, 200)
                br = g.boundingRect(box, Qt.TextWordWrap, "⚠ " + line)
                g.drawText(box, Qt.TextWordWrap, "⚠ " + line)
                y += br.height() + 2
            y += 6

        y = self.draw_metric(g, y, pad, W, st.cooler_title, fmt_n(st.pump_rpm) if st.pump_rpm is not None else "—", "rpm",
                        self.pump_color(st), self.app.pump_hist,
                        # headroom of 10% of the speed above and below, so normal one-reading wiggles look small
                        max(50, (st.pump_rpm or 0) * 0.10), fBig, fMid, fSmall)
        y = self.draw_metric(g, y, pad, W, "CPU", f"{st.cpu_temp:.1f}" if st.cpu_temp is not None else "—", "°C",
                        self.cpu_color(st), self.app.cpu_hist, 1, fBig, fMid, fSmall)

        g.setFont(fSmall)
        g.setPen(T.Dim)
        asc = QFontMetricsF(fSmall).ascent()
        if st.pump_stopped is not None:     # v2.2+: trips only on a stopped pump; slow = warning
            trips = f"Trips: {st.cooler} stopped (≤ {fmt_n(st.pump_stopped)} rpm)" + (f"  ·  CPU ≥ {st.cpu_max:.0f} °C" if st.cpu_max else "")
            warns = f"Warns: {st.cooler} < {fmt_n(st.pump_min or 0)} rpm"
            if st.cpu_temp is not None and st.cpu_max:
                warns += f"  ·  {max(0, st.cpu_max - st.cpu_temp):.0f}° headroom"
            g.drawText(QPointF(pad, y + asc), trips)
            y += 18
            g.drawText(QPointF(pad, y + asc), warns)
            y += 18
        else:
            trips = f"Trips at < {fmt_n(st.pump_min or 0)} rpm" + (f"  ·  ≥ {st.cpu_max:.0f} °C" if st.cpu_max else "  ·  CPU trip off")
            if st.cpu_temp is not None and st.cpu_max:
                trips += f"  ·  {max(0, st.cpu_max - st.cpu_temp):.0f}° headroom"
            g.drawText(QPointF(pad, y + asc), trips)
            y += 18
        today = "Today: CPU peak " + (f"{st.day_max_temp:.0f} °C" if st.day_max_temp is not None else "—") + \
                f"  ·  {st.cooler} min " + (f"{fmt_n(st.day_min_pump)} rpm" if st.day_min_pump is not None else "—")
        g.drawText(QPointF(pad, y + asc), today)
        y += 18
        if st.selftest_at:
            g.setPen(T.Dim if st.selftest_ok else T.Amber)
            g.drawText(QPointF(pad, y + asc), f"Self-test: {'OK' if st.selftest_ok else 'FAILED'} ({st.selftest_at:%d %b %Y})")
            y += 18
        if st.last_off_at:
            g.setPen(T.Amber)
            box = QRectF(pad, y, W - pad * 2, 200)
            txt = f"⚡ LoopMedic powered off this PC on {st.last_off_at:%d %b at %H:%M}: {st.last_off_reason}"
            br = g.boundingRect(box, Qt.TextWordWrap, txt)
            g.drawText(box, Qt.TextWordWrap, txt)
            y += br.height() + 2
            g.setPen(T.Dim)
        y += 4
        if st.state == "PAUSED" and st.paused_until:
            g.setPen(T.Amber)
            g.drawText(QPointF(pad, y - 4 + asc), f"Paused until {st.paused_until:%H:%M} - not protecting!")
            y += 16

        # fans
        g.setPen(QPen(T.Line, 1))
        g.drawLine(QPointF(pad, y), QPointF(W - pad, y))
        y += 8
        g.setFont(fTiny)
        g.setPen(T.Dim)
        g.drawText(QPointF(pad, y + QFontMetricsF(fTiny).ascent()), "FANS")
        y += 18
        live = [f for f in st.fans if f["rpm"] and f["rpm"] > 0]
        top = max([f["rpm"] for f in live] + [1])
        g.setFont(fSmall)
        for f in live:
            g.setPen(T.Green if f["pump"] else T.Text)
            g.drawText(QPointF(pad, y + asc), f["name"] + (f"  · {st.cooler_title}" if f["pump"] else ""))
            r = fmt_n(f["rpm"]) + " rpm"
            g.setPen(T.Text)
            g.drawText(QPointF(W - pad - text_w(fSmall, r), y + asc), r)
            bar = QRectF(pad + 120, y + 7, W - pad * 2 - 120 - 80, 5)
            g.fillPath(rounded(bar, 2.5), T.Card)
            fw = bar.width() * f["rpm"] / top
            if fw > 5:
                g.fillPath(rounded(QRectF(bar.x(), bar.y(), fw, bar.height()), 2.5), T.Green if f["pump"] else T.Blue)
            y += 22
        empty = len(st.fans) - len(live)
        g.setPen(T.Dim)
        if empty > 0:
            g.drawText(QPointF(pad, y + asc), f"{empty} empty header{'' if empty == 1 else 's'} (0 rpm)")
            y += 18
        if not st.fans:
            g.drawText(QPointF(pad, y + asc), "No fan data yet")
            y += 18

        # buttons
        y += 6
        g.setPen(QPen(T.Line, 1))
        g.drawLine(QPointF(pad, y), QPointF(W - pad, y))
        y += 10
        bw, bh = (W - pad * 2 - 16) / 3, 30
        paused = st.state == "PAUSED"
        self.button(g, QRectF(pad, y, bw, bh), "Resume" if paused else "Pause 30 min", self.app.toggle_pause, fSmall, paused)
        self.button(g, QRectF(pad + bw + 8, y, bw, bh), "Run test", self.app.run_test, fSmall, False)
        self.button(g, QRectF(pad + (bw + 8) * 2, y, bw, bh), "Open log", self.app.open_log, fSmall, False)
        y += bh + 8

        # Live indicator: up to 10 s old = Live; 10-20 s = waiting; older = offline
        age = max(0, int((datetime.now() - st.at).total_seconds())) if st.at != datetime.min else 999
        if st.state == "OFFLINE":
            upd, uc = "Offline - no data from LoopMedic", T.Dim
        elif age <= 10:
            upd, uc = "Live", T.Green
        else:
            upd, uc = f"Waiting for sensors… ({age} s old)", T.Amber
        th = text_h(fTiny)
        g.setBrush(uc)
        g.setPen(Qt.NoPen)
        g.drawEllipse(QRectF(pad, y + (th - 7) / 2, 7, 7))
        g.setFont(fTiny)
        g.setPen(uc)
        g.drawText(QPointF(pad + 12, y + QFontMetricsF(fTiny).ascent()), upd)
        g.setPen(T.Dim)
        g.drawText(QPointF(W - pad - text_w(fTiny, AUTHOR), y + QFontMetricsF(fTiny).ascent()), AUTHOR)
        y += th + 12
        g.end()

        need = int(y + 0.999)
        if abs(need - self.height()) > 2:
            self.need = need
            bottom = self.y() + self.height()
            anchored_bottom = self.y() > (self.screen().availableGeometry().center().y() if self.screen() else 0)
            self.resize(self.W, need)
            if anchored_bottom and self.isVisible():
                self.move(self.x(), bottom - need)

    @staticmethod
    def pump_color(st):
        if st.pump_rpm is None:
            return T.Dim
        if st.pump_stopped is not None:
            if st.pump_rpm <= st.pump_stopped:
                return T.Red
            if st.pump_min and st.pump_rpm < st.pump_min:
                return T.Amber
            return T.Green
        if st.pump_min and st.pump_rpm < st.pump_min:
            return T.Red
        if st.pump_min and st.pump_rpm < st.pump_min * 2:
            return T.Amber
        return T.Green

    @staticmethod
    def cpu_color(st):
        if st.cpu_temp is None:
            return T.Dim
        lim = st.cpu_max if st.cpu_max else 95
        if st.cpu_temp >= lim:
            return T.Red
        if st.cpu_temp >= lim - 10:
            return T.Amber
        return T.Green

    def draw_metric(self, g, y, pad, W, label, value, unit, c, hist, floor, fBig, fMid, fSmall):
        card = QRectF(pad, y, W - pad * 2, 56)
        g.fillPath(rounded(card, 8), T.Card)
        g.setFont(fSmall)
        g.setPen(T.Dim)
        g.drawText(QPointF(card.x() + 12, card.y() + 6 + QFontMetricsF(fSmall).ascent()), label)
        g.setFont(fBig)
        g.setPen(c)
        base = card.y() + 19 + QFontMetricsF(fBig).ascent()
        g.drawText(QPointF(card.x() + 11, base), value)
        g.setFont(fMid)
        g.setPen(T.Dim)
        g.drawText(QPointF(card.x() + 11 + text_w(fBig, value) + 4, base), unit)
        sparkline(g, QRectF(card.x() + card.width() * 0.5, card.y() + 12, card.width() * 0.5 - 12, card.height() - 24), hist, c, floor)
        return y + card.height() + 8

    def button(self, g, r, text, cb, f, accent):
        self.buttons.append((r, text, cb))
        hot = self.hover == text
        g.fillPath(rounded(r, 6), T.Green if accent else (T.Hot if hot else T.Card))
        g.setFont(f)
        g.setPen(QColor(0, 0, 0) if accent else T.Text)
        g.drawText(r, Qt.AlignCenter, text)


# ------------------------------------------------------------------ trip pop-up
class TripPopup(QWidget, Clickable):
    def __init__(self, app, st):
        QWidget.__init__(self, None, Qt.Window | Qt.FramelessWindowHint | Qt.WindowStaysOnTopHint)
        Clickable.__init__(self)
        self.app = app
        self.is_test = st.trip_kind == "test" or st.state == "TEST"
        what = st.cooler_title.upper()
        self.title = f"TEST: SIMULATED {what} FAILURE" if self.is_test else (
            "CPU TEMPERATURE LIMIT REACHED" if st.trip_kind == "cpu" else f"{what} FAILURE DETECTED")
        self.reason = st.trip_reason or ""
        self.deadline = st.trip_deadline or datetime.now() + timedelta(seconds=30)
        self.total = max(1.0, (self.deadline - datetime.now()).total_seconds())
        self.acted = False
        self.setWindowTitle("LoopMedic - " + self.title)
        self.setAttribute(Qt.WA_TranslucentBackground)
        self.setMouseTracking(True)
        self.resize(500, 242)
        scr = QGuiApplication.primaryScreen().availableGeometry()
        self.move(scr.center().x() - 250, scr.center().y() - 121)
        self.t = QTimer(self)
        self.t.timeout.connect(self.tick)
        self.t.start(200)

    def tick(self):
        self.update()
        if self.is_test and datetime.now() > self.deadline + timedelta(seconds=3):
            self.close()

    def mouseMoveEvent(self, e):
        t, _ = self.hit(e.position())
        if t != self.hover:
            self.hover = t
            self.setCursor(Qt.PointingHandCursor if t else Qt.ArrowCursor)
            self.update()

    def mousePressEvent(self, e):
        _, cb = self.hit(e.position())
        if cb and not self.acted:
            cb()

    def paintEvent(self, e):
        g = QPainter(self)
        g.setRenderHint(QPainter.Antialiasing)
        self.buttons = []
        W, pad = float(self.width()), 22.0
        accent = T.Blue if self.is_test else T.Red
        left = max(0.0, (self.deadline - datetime.now()).total_seconds())
        g.fillPath(rounded(QRectF(0, 0, W, self.height()), 10), T.Bg)
        g.save()
        g.setClipPath(rounded(QRectF(0, 0, W, self.height()), 10))
        g.fillRect(QRectF(0, 0, W, 6), accent)
        g.restore()

        fT, fR, fC, fB, fS = font(20, True), font(13), font(15, True), font(13, True), font(11.5)
        y = 24.0
        g.setFont(fT)
        g.setPen(accent)
        g.drawText(QPointF(pad, y + QFontMetricsF(fT).ascent()), "⚠  " + self.title)
        y += 36
        g.setFont(fR)
        g.setPen(T.Text)
        g.drawText(QRectF(pad, y, W - pad * 2, 40), Qt.TextWordWrap, self.reason)
        y += 40
        if self.is_test:
            cd = f"A real trip would power off in {int(left + 0.999)} s" if left > 0 else "Test finished - nothing powered off."
        elif self.acted:
            cd = "OK"
        else:
            cd = f"Powering off in {int(left + 0.999)} s" if left > 0 else "Powering off…"
        g.setFont(fC)
        g.drawText(QPointF(pad, y + QFontMetricsF(fC).ascent()), cd)
        y += 30
        bar = QRectF(pad, y, W - pad * 2, 6)
        g.fillPath(rounded(bar, 3), T.Card)
        fw = bar.width() * left / self.total
        if fw > 6:
            g.fillPath(rounded(QRectF(bar.x(), bar.y(), fw, bar.height()), 3), accent)
        y += 22
        bh = 38
        if self.is_test:
            self.button(g, QRectF(W - pad - 160, y, 160, bh), "Close", self.close_test, fB, T.Blue, Qt.white)
        else:
            self.button(g, QRectF(pad, y, 170, bh), "Shut down now", self.shutdown_now, fB, T.Red, Qt.white)
            self.button(g, QRectF(W - pad - 190, y, 190, bh), "Cancel shutdown", self.cancel, fB, T.Card, T.Text)
        y += bh + 10
        g.setFont(fS)
        g.setPen(T.Dim)
        g.drawText(QPointF(pad, y + QFontMetricsF(fS).ascent()),
                   "This was only a test of the pop-up." if self.is_test else "No choice = the PC powers off to protect the hardware.")
        g.end()

    def button(self, g, r, text, cb, f, bg, fg):
        self.buttons.append((r, text, cb))
        c = QColor(bg).lighter(118) if self.hover == text else QColor(bg)
        g.fillPath(rounded(r, 7), c)
        g.setFont(f)
        g.setPen(QColor(fg))
        g.drawText(r, Qt.AlignCenter, text)

    def close_test(self):
        self.acted = True
        self.app.cancel(True)
        self.close()

    def shutdown_now(self):
        self.acted = True
        self.app.shutdown_now()
        self.update()

    def cancel(self):
        self.acted = True
        self.app.cancel(False)
        self.close()


# ------------------------------------------------------------------ app
class TrayApp:
    def __init__(self, a):
        self.a = a
        self.current = Status.load(a.status)
        self.pump_hist, self.cpu_hist = [], []
        self.icons = {}
        self.icon_state = None
        self.panel = None
        self.popup = None
        self.popup_deadline = None
        self.panel_hidden_at = datetime.min
        self.last_heartbeat = datetime.min
        self.last_problem = None
        self.pending_problem = None     # what we see now, waiting to prove it isn't a blip
        self.pending_since = datetime.now()
        self.last_good = None           # last status that read cleanly
        self.last_at = None
        self.started = datetime.now()
        self.seen_off_path = os.path.join(os.path.expanduser("~"), ".local", "state", "loopmedic", "seen-poweroff.txt")
        self.seen_off_checked = False
        self.update_busy = False
        self.bridge = Bridge()
        self.bridge.done.connect(lambda f: f())   # run a callable on the Qt thread

        self.tray = QSystemTrayIcon()
        self.tray.setIcon(self.icon_for(self.current.state))
        menu = QMenu()
        self.act_open = QAction("Open panel")
        self.act_pause = QAction("Pause 30 min")
        self.act_test = QAction("Run test")
        self.act_log = QAction("Open log")
        self.act_exit = QAction("Exit tray icon (protection keeps running)")
        self.act_open.triggered.connect(self.show_panel)
        self.act_pause.triggered.connect(self.toggle_pause)
        self.act_test.triggered.connect(self.run_test)
        self.act_log.triggered.connect(self.open_log)
        self.act_exit.triggered.connect(QApplication.quit)
        for x in (self.act_open, self.act_pause, self.act_test, self.act_log):
            menu.addAction(x)
        menu.addSeparator()
        menu.addAction(self.act_exit)
        self.menu = menu
        self.tray.setContextMenu(menu)
        self.tray.activated.connect(self.on_activated)
        self.tray.show()

        self.timer = QTimer()
        self.timer.timeout.connect(self.tick)
        self.timer.start(1000)
        self.tick()
        self.notify_if_updated()

    # ---- updates (only when the version in the panel is clicked)
    def later(self, f):
        self.bridge.done.emit(f)

    def check_updates(self):
        if self.update_busy:
            return
        self.update_busy = True
        current = self.current.version or TRAY_VERSION
        if self.panel:
            self.panel.update()

        def work():
            rel, err = check_latest(current)

            def show():
                self.update_busy = False
                if self.panel:
                    self.panel.update()
                if err:
                    self.tray.showMessage("LoopMedic - couldn't check for updates", err, QSystemTrayIcon.Warning, 8000)
                elif not rel:
                    self.tray.showMessage("LoopMedic is up to date", f"You're on the latest version ({current}).",
                                          QSystemTrayIcon.Information, 6000)
                else:
                    self.offer_update(current, rel)
            self.later(show)
        threading.Thread(target=work, daemon=True).start()

    def offer_update(self, current, rel):
        box = QMessageBox()
        box.setWindowTitle("LoopMedic update")
        box.setIcon(QMessageBox.Information)
        box.setText(f"LoopMedic {rel['version']} is available (you have {current}).\nUpdating keeps your settings.")
        box.setInformativeText("What's new:\n\n" + plain_notes(rel["notes"]))
        b_update = box.addButton("Update now", QMessageBox.AcceptRole)
        box.addButton("Later", QMessageBox.RejectRole)
        b_log = box.addButton("Full changelog", QMessageBox.HelpRole)
        box.setDefaultButton(b_update)
        box.exec()
        if box.clickedButton() == b_log:
            QDesktopServices.openUrl(QUrl(rel["page"]))
        elif box.clickedButton() == b_update:
            self.run_update(rel)

    def run_update(self, rel):
        self.tray.showMessage("LoopMedic", f"Downloading LoopMedic {rel['version']}...", QSystemTrayIcon.Information, 4000)
        user, group = getpass.getuser(), grp.getgrgid(os.getgid()).gr_name

        def work():
            try:
                setup = download_update(rel)
            except (RuntimeError, OSError, urllib.error.URLError, ValueError, tarfile.TarError) as e:
                msg = str(e)
                self.later(lambda: self.tray.showMessage("LoopMedic update failed", msg, QSystemTrayIcon.Warning, 15000))
                return
            # pkexec asks for your password, then the new setup installs as root (settings kept)
            r = subprocess.run(["pkexec", "/usr/bin/python3", setup, "--update-root", "--user", user, "--group", group],
                               capture_output=True, text=True)

            def after():
                if r.returncode == 0:
                    subprocess.Popen(["/usr/bin/python3", "/opt/loopmedic/loopmedic_tray.py"], start_new_session=True,
                                     stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
                    QApplication.quit()   # the new tray takes over (it says "updated")
                elif r.returncode in (126, 127):
                    self.tray.showMessage("LoopMedic", "Update cancelled - no administrator permission.", QSystemTrayIcon.Warning, 8000)
                else:
                    tail = (r.stdout + r.stderr).strip().splitlines()[-1:] or ["unknown error"]
                    self.tray.showMessage("LoopMedic update failed", tail[0][:200], QSystemTrayIcon.Warning, 15000)
            self.later(after)
        threading.Thread(target=work, daemon=True).start()

    def notify_if_updated(self):
        f = os.path.join(os.path.expanduser("~"), ".local", "state", "loopmedic", "last-version.txt")
        try:
            before = open(f, encoding="utf-8").read().strip() if os.path.exists(f) else ""
            if before == TRAY_VERSION:
                return
            os.makedirs(os.path.dirname(f), exist_ok=True)
            with open(f, "w", encoding="utf-8") as fh:
                fh.write(TRAY_VERSION)
            if before:
                self.tray.showMessage("LoopMedic updated", f"Now running version {TRAY_VERSION} (was {before}). Your settings were kept.",
                                      QSystemTrayIcon.Information, 8000)
        except OSError:
            pass

    # ---- loop
    def tick(self):
        st = Status.load(self.a.status)
        # one failed read must not count as "offline": keep the last good status until it is 20 s old
        if st.read_ok:
            self.last_good = st
        elif self.last_good and (datetime.now() - self.last_good.at).total_seconds() <= 20:
            st = self.last_good
        self.current = st
        if st.state != "OFFLINE" and st.at != self.last_at:
            self.last_at = st.at
            if st.pump_rpm is not None:
                self.push(self.pump_hist, st.pump_rpm)
            if st.cpu_temp is not None:
                self.push(self.cpu_hist, st.cpu_temp)
        self.update_icon()
        self.notify()
        self.notify_last_poweroff()
        if (datetime.now() - self.last_heartbeat).total_seconds() >= 5:
            if self.write_control("tray.json", "at", datetime.now(), False):
                self.last_heartbeat = datetime.now()
        self.act_pause.setText("Resume protection" if st.state == "PAUSED" else "Pause 30 min")

        tripping = st.state in ("TRIPPED", "TEST") and st.trip_deadline is not None
        if tripping and st.trip_deadline != self.popup_deadline:
            self.popup_deadline = st.trip_deadline
            self.show_popup(st)
        if not tripping and self.popup and self.popup.isVisible() and not self.popup.is_test:
            self.popup.close()   # cancelled elsewhere (or test over)
        if self.panel and self.panel.isVisible():
            self.panel.update()

    @staticmethod
    def push(lst, v):
        lst.append(v)
        if len(lst) > 300:
            del lst[0]

    def icon_for(self, s):
        if s not in self.icons:
            self.icons[s] = make_icon(s)
        return self.icons[s]

    def update_icon(self):
        st = self.current
        tip = f"LoopMedic - {T.label(st.state)}"
        if st.state != "OFFLINE":
            tip += f"\n{st.cooler_title} {fmt_n(st.pump_rpm) + ' rpm' if st.pump_rpm is not None else '—'} · CPU " + \
                   (f"{st.cpu_temp:.1f} °C" if st.cpu_temp is not None else "—")
        self.tray.setToolTip(tip)
        if st.state != self.icon_state:
            self.icon_state = st.state
            self.tray.setIcon(self.icon_for(st.state))

    # ---- notifications when protection gets weaker or comes back
    def notify(self):
        st = self.current
        if st.state == "OFFLINE":
            if (datetime.now() - self.started).total_seconds() < 45:
                return   # give the service a moment after logon
            problem = "LoopMedic isn't sending data - your PC may NOT be protected. Check: systemctl status loopmedic"
        elif st.degraded:
            problem = "\n".join(st.degraded)
        else:
            problem = ""
        # only notify once a change has held for 5 s, so a one-poll hiccup doesn't pop "off" then "back on"
        if problem != self.pending_problem:
            self.pending_problem, self.pending_since = problem, datetime.now()
        if (datetime.now() - self.pending_since).total_seconds() < 5:
            return
        if self.last_problem is None:
            self.last_problem = problem
            if problem:
                self.warn(problem)
            return
        if problem == self.last_problem:
            return
        if problem:
            self.warn(problem)
        else:
            self.tray.showMessage("LoopMedic", "Protection is back to normal.", QSystemTrayIcon.Information, 4000)
        self.last_problem = problem

    def notify_last_poweroff(self):
        """Once per power-off (if it was within 24 h): tell the user why their PC turned off."""
        st = self.current
        if self.seen_off_checked or not st.read_ok or st.state == "OFFLINE":
            return
        self.seen_off_checked = True
        if not st.last_off_at or (datetime.now() - st.last_off_at).total_seconds() > 24 * 3600:
            return
        key = st.last_off_at.isoformat()
        try:
            with open(self.seen_off_path, encoding="utf-8") as f:
                if f.read().strip() == key:
                    return
        except OSError:
            pass
        try:
            os.makedirs(os.path.dirname(self.seen_off_path), exist_ok=True)
            with open(self.seen_off_path, "w", encoding="utf-8") as f:
                f.write(key)
        except OSError:
            pass
        self.tray.showMessage("LoopMedic powered off this PC",
                              f"{st.last_off_at:%d %b at %H:%M}: {st.last_off_reason}\n"
                              "Check the pump/cooler before heavy use. Details: journalctl -t loopmedic",
                              QSystemTrayIcon.Critical, 20000)

    def warn(self, text):
        if len(text) > 250:
            text = text[:247] + "..."
        self.tray.showMessage("LoopMedic - protection reduced", text, QSystemTrayIcon.Warning, 10000)

    def balloon(self, text):
        self.tray.showMessage("LoopMedic", text, QSystemTrayIcon.Information, 4000)

    # ---- trip pop-up (+ a critical notification that stays on screen, in case a window rule hides the pop-up)
    def show_popup(self, st):
        if self.popup:
            self.popup.close()
        self.popup = TripPopup(self, st)
        self.popup.show()
        self.popup.raise_()
        self.popup.activateWindow()
        if st.trip_kind != "test" and st.state != "TEST":
            title = "CPU TEMPERATURE LIMIT REACHED" if st.trip_kind == "cpu" else f"{st.cooler_title.upper()} FAILURE DETECTED"
            body = f"{st.trip_reason or ''}\nThe PC powers off in about 30 s. Open the LoopMedic pop-up to cancel."
            if shutil.which("notify-send"):
                subprocess.Popen(["notify-send", "-u", "critical", "-a", "LoopMedic", "-i", "dialog-warning", title, body])
            else:
                self.tray.showMessage(title, body, QSystemTrayIcon.Critical, 30000)
        self.play_alert()

    @staticmethod
    def play_alert():
        snd = "/usr/share/sounds/freedesktop/stereo/dialog-warning.oga"
        for cmd in (["canberra-gtk-play", "-i", "dialog-warning"], ["paplay", snd], ["pw-play", snd]):
            if shutil.which(cmd[0]) and (cmd[0] == "canberra-gtk-play" or os.path.exists(snd)):
                try:
                    subprocess.Popen(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
                    return
                except OSError:
                    pass
        QApplication.beep()

    # ---- actions
    def write_control(self, name, field, value, show_error):
        try:
            path = os.path.join(self.a.control, name)
            tmp = path + ".tmp"
            with open(tmp, "w", encoding="utf-8") as f:
                json.dump({field: value.astimezone().isoformat()}, f)
            os.replace(tmp, path)
            return True
        except OSError:
            if show_error:
                self.tray.showMessage("LoopMedic", "Couldn't reach LoopMedic's control folder. Run install.sh again.",
                                      QSystemTrayIcon.Warning, 4000)
            return False

    def toggle_pause(self):
        if self.current.state == "PAUSED":
            self.write_control("pause.json", "until", datetime.now() - timedelta(minutes=1), True)
            self.balloon("Protection resumed.")
        elif self.write_control("pause.json", "until", datetime.now() + timedelta(minutes=30), True):
            self.balloon("Protection paused for 30 minutes. It resumes on its own.")

    def run_test(self):
        if self.write_control("test.json", "requested", datetime.now(), True):
            self.balloon("Test requested - the trip pop-up should appear within a few seconds. Nothing will power off.")

    def open_log(self):
        try:
            if sys.platform.startswith("win"):
                os.startfile(self.a.log)  # noqa - preview on Windows only
            else:
                subprocess.Popen(["xdg-open", self.a.log], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        except OSError:
            pass

    def cancel(self, is_test):
        self.write_control("cancel.json", "at", datetime.now(), True)
        self.balloon("Test finished. In a real trip the PC would have powered off." if is_test else
                     "Power-off cancelled. Check your cooling! LoopMedic re-arms in 2 minutes.")

    def shutdown_now(self):
        """Ask the service to cut power now; if nothing happens within 5 s, ask systemd ourselves."""
        self.write_control("poweroff.json", "at", datetime.now(), False)
        QTimer.singleShot(5000, lambda: subprocess.Popen(["systemctl", "poweroff", "-i"]) if not sys.platform.startswith("win") else None)

    # ---- panel
    def on_activated(self, reason):
        if reason in (QSystemTrayIcon.Trigger, QSystemTrayIcon.DoubleClick):
            self.toggle_panel()

    def toggle_panel(self):
        if self.panel and self.panel.isVisible():
            self.panel.hide()
            return
        if (datetime.now() - self.panel_hidden_at).total_seconds() < 0.3:
            return   # the click on the icon is what closed it
        self.show_panel()

    def show_panel(self):
        if not self.panel:
            self.panel = Panel(self)
        self.panel.resize(Panel.W, self.panel.need)
        self.panel.place()
        self.panel.show()
        self.panel.raise_()
        self.panel.activateWindow()


def render(widget, path):
    widget.resize(widget.width(), widget.height())
    widget.grab()                      # first pass measures the height
    if isinstance(widget, Panel):
        widget.resize(Panel.W, widget.need)
    widget.grab().save(path)


def main():
    ap = argparse.ArgumentParser(description="LoopMedic tray icon")
    ap.add_argument("--status", default=os.environ.get("LOOPMEDIC_STATUS", "/run/loopmedic/status.json"))
    ap.add_argument("--control", default=os.environ.get("LOOPMEDIC_CONTROL", "/var/lib/loopmedic/control"))
    ap.add_argument("--log", default=os.environ.get("LOOPMEDIC_LOG", "/var/log/loopmedic/loopmedic.log"))
    ap.add_argument("--open", action="store_true")
    ap.add_argument("--shot-panel")
    ap.add_argument("--shot-trip")
    ap.add_argument("--history", type=int, default=0, help="(screenshots) seconds of history to sample first")
    a = ap.parse_args()

    app = QApplication(sys.argv)
    app.setApplicationName("LoopMedic")
    app.setDesktopFileName("loopmedic-tray")
    app.setQuitOnLastWindowClosed(False)
    global FAMILY
    FAMILY = app.font().family()

    if a.shot_panel or a.shot_trip:
        tray = TrayApp(a)
        for _ in range(a.history):     # fake a little history for the sparklines
            st = Status.load(a.status)
            if st.pump_rpm is not None:
                tray.push(tray.pump_hist, st.pump_rpm)
            if st.cpu_temp is not None:
                tray.push(tray.cpu_hist, st.cpu_temp)
            import time
            time.sleep(1)
        tray.current = Status.load(a.status)
        if a.shot_panel:
            p = Panel(tray)
            render(p, a.shot_panel)
        if a.shot_trip:
            render(TripPopup(tray, tray.current), a.shot_trip)
        tray.tray.hide()
        return 0

    if not QSystemTrayIcon.isSystemTrayAvailable():
        print("No system tray found (on GNOME install the AppIndicator extension).", file=sys.stderr)
    tray = TrayApp(a)
    if a.open:
        QTimer.singleShot(800, tray.show_panel)
    return app.exec()


if __name__ == "__main__":
    sys.exit(main())
