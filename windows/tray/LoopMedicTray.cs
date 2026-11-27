// LoopMedic tray app - by Jacob Stiltner, (c) 2026
// Shows LoopMedic's status (written by the SYSTEM watchdog to status.json) as a
// tray icon + panel, and a pop-up with a countdown when it trips.
// The watchdog does the actual shutdown, so protection never depends on this app.
// Build: ..\build.ps1 (the in-box .NET Framework compiler; it lists the references this file needs)

using System;
using System.Collections.Generic;
using System.Diagnostics;
using System.Drawing;
using System.Drawing.Drawing2D;
using System.Drawing.Text;
using System.Globalization;
using System.IO;
using System.Linq;
using System.Media;
using System.Net;
using System.Reflection;
using System.Security.Cryptography;
using System.Runtime.InteropServices;
using System.Threading;
using System.Web.Script.Serialization;
using System.Windows.Forms;

[assembly: System.Reflection.AssemblyTitle("LoopMedic tray")]
[assembly: System.Reflection.AssemblyCompany("Jacob Stiltner")]
[assembly: System.Reflection.AssemblyProduct("LoopMedic")]
[assembly: System.Reflection.AssemblyCopyright("(c) 2026 Jacob Stiltner")]
[assembly: System.Reflection.AssemblyVersion("2.2.0.0")]

namespace LoopMedicTray
{
    static class Program
    {
        [DllImport("user32.dll")] static extern bool SetProcessDPIAware();

        [STAThread]
        static void Main(string[] args)
        {
            bool created;
            string dir = AppDomain.CurrentDomain.BaseDirectory;
            for (int i = 0; i < args.Length - 1; i++)
            {
                if (args[i] == "--dir") dir = args[i + 1];
                if (args[i] == "--theme") Theme.Apply(args[i + 1]);   // screenshots: a theme for this run only (not saved)
            }
            // one tray per data folder (a test tray with --dir can run next to the real one)
            string mutexName = "Local\\LoopMedicTray" + (args.Contains("--dir") ? "-" + (Path.GetFullPath(dir).ToLowerInvariant().GetHashCode() & 0x7fffffff) : "");
            using (var mutex = new Mutex(true, mutexName, out created))
            {
                if (!created) return;
                bool open = args.Contains("--open");
                try { SetProcessDPIAware(); } catch { }
                Application.EnableVisualStyles();
                Application.SetCompatibleTextRenderingDefault(false);
                var ctx = new TrayContext(dir);
                if (open) ctx.OpenPanelSoon();
                Application.Run(ctx);
            }
        }
    }

    // ------------------------------------------------------------------ data
    class FanInfo { public string Name; public double? Rpm; public bool IsPump; }

    class Status
    {
        public DateTime At;
        public string State = "OFFLINE";
        public bool ReadOk;          // the file was read and parsed (false = missing, locked mid-save, or garbled)
        public bool DryRun;
        public bool Air;             // air cooler mode: the CPU fan is watched instead of a pump
        public string Cooler { get { return Air ? "CPU fan" : "Pump"; } }
        public string cooler { get { return Air ? "CPU fan" : "pump"; } }
        public double? PumpRpm, CpuTemp, PumpMinRpm, PumpStoppedRpm, CpuMaxTemp, DayMaxTemp, DayMinPump;
        public DateTime? LastPowerOffAt, SelfTestAt;
        public string LastPowerOffReason;
        public bool SelfTestOk;
        public string CpuName = "";
        public DateTime? PausedUntil, TripDeadline;
        public string TripKind, TripReason, Version = "";
        public List<FanInfo> Fans = new List<FanInfo>();
        public List<string> Degraded = new List<string>();

        static double? Num(Dictionary<string, object> d, string k)
        {
            object o;
            if (!d.TryGetValue(k, out o) || o == null) return null;
            try { return Convert.ToDouble(o, CultureInfo.InvariantCulture); } catch { return null; }
        }
        static string Str(Dictionary<string, object> d, string k)
        {
            object o;
            return d.TryGetValue(k, out o) && o != null ? Convert.ToString(o, CultureInfo.InvariantCulture) : null;
        }
        static DateTime? Date(Dictionary<string, object> d, string k)
        {
            string s = Str(d, k);
            DateTime t;
            if (s != null && DateTime.TryParse(s, CultureInfo.InvariantCulture, DateTimeStyles.RoundtripKind, out t)) return t.ToLocalTime();
            return null;
        }

        public static Status Load(string path)
        {
            var st = new Status();
            try
            {
                if (!File.Exists(path)) return st;
                string json;
                using (var fs = new FileStream(path, FileMode.Open, FileAccess.Read, FileShare.ReadWrite | FileShare.Delete))
                using (var sr = new StreamReader(fs)) json = sr.ReadToEnd();
                var d = new JavaScriptSerializer().DeserializeObject(json) as Dictionary<string, object>;
                if (d == null) return st;
                st.At = Date(d, "at") ?? DateTime.MinValue;
                st.State = Str(d, "state") ?? "OFFLINE";
                st.DryRun = (Str(d, "dryRun") ?? "").ToLowerInvariant() == "true";
                st.Air = (Str(d, "coolerType") ?? "") == "air";
                st.PumpRpm = Num(d, "pumpRpm"); st.CpuTemp = Num(d, "cpuTemp");
                st.PumpMinRpm = Num(d, "pumpMinRpm"); st.CpuMaxTemp = Num(d, "cpuMaxTemp");
                st.DayMaxTemp = Num(d, "dayMaxTemp"); st.DayMinPump = Num(d, "dayMinPump");
                st.CpuName = Str(d, "cpuName") ?? "";
                st.PausedUntil = Date(d, "pausedUntil"); st.TripDeadline = Date(d, "tripDeadline");
                st.TripKind = Str(d, "tripKind"); st.TripReason = Str(d, "tripReason");
                st.Version = Str(d, "version") ?? "";
                st.PumpStoppedRpm = Num(d, "pumpStoppedRpm");
                object sub;
                if (d.TryGetValue("lastPowerOff", out sub) && sub is Dictionary<string, object>)
                {
                    var lp = (Dictionary<string, object>)sub;
                    st.LastPowerOffAt = Date(lp, "at"); st.LastPowerOffReason = Str(lp, "reason");
                }
                if (d.TryGetValue("selfTest", out sub) && sub is Dictionary<string, object>)
                {
                    var stt = (Dictionary<string, object>)sub;
                    st.SelfTestAt = Date(stt, "at"); st.SelfTestOk = (Str(stt, "ok") ?? "").ToLowerInvariant() == "true";
                }
                object dg;
                if (d.TryGetValue("degraded", out dg))
                {
                    if (dg is object[]) foreach (var x in (object[])dg) { if (x != null) st.Degraded.Add(Convert.ToString(x)); }
                    else if (dg is string && ((string)dg).Length > 0) st.Degraded.Add((string)dg);
                }
                object fo;
                if (d.TryGetValue("fans", out fo) && fo is object[])
                {
                    foreach (var f in (object[])fo)
                    {
                        var fd = f as Dictionary<string, object>;
                        if (fd == null) continue;
                        st.Fans.Add(new FanInfo { Name = Str(fd, "name") ?? "Fan", Rpm = Num(fd, "rpm"), IsPump = (Str(fd, "pump") ?? "").ToLowerInvariant() == "true" });
                    }
                }
                st.ReadOk = true;
                if ((DateTime.Now - st.At).TotalSeconds > 20) st.State = "OFFLINE";   // 20 s without data = offline
            }
            catch { st.State = "OFFLINE"; }
            return st;
        }
    }

    // ------------------------------------------------------------------ look
    // Themes (tray right-click > Themes). Green = "all good" (graphs, pump bar, ARMED), Blue = the second accent
    // (fan bars, TEST), Amber / Red = warnings and alarms. Every theme keeps those last two clearly apart from
    // its accents, so a warning never blends in.
    static class Theme
    {
        public static readonly string[] Names = { "Stealth", "Atomic", "Dark Side", "Ice" };
        public static string Name = "Stealth";
        public static bool Dark = true;
        public static Color Bg, Card, CardHot, Line, Text, Dim, Green, Amber, Red, Blue, Link, Grey, Off;

        static Theme() { Apply(Load()); }

        static Color C(int r, int g, int b) { return Color.FromArgb(r, g, b); }

        public static void Apply(string name)
        {
            switch (name)
            {
                case "Atomic":       // deep navy, electric blue + yellow
                    Bg = C(13, 21, 43); Card = C(22, 35, 68); CardHot = C(34, 52, 96); Line = C(40, 58, 100);
                    Text = C(236, 242, 255); Dim = C(146, 163, 200);
                    Green = C(56, 189, 255); Blue = C(255, 214, 10); Amber = C(255, 138, 40); Red = C(255, 70, 85);
                    Link = C(255, 214, 10); Grey = C(140, 150, 170); Off = C(85, 95, 115); Dark = true;
                    break;
                case "Dark Side":    // near-black purple, blue + pink/magenta
                    Bg = C(18, 10, 24); Card = C(33, 19, 44); CardHot = C(50, 30, 66); Line = C(62, 38, 80);
                    Text = C(246, 236, 252); Dim = C(176, 152, 190);
                    Green = C(84, 150, 255); Blue = C(255, 77, 196); Amber = C(255, 170, 50); Red = C(255, 59, 59);
                    Link = C(255, 128, 214); Grey = C(150, 140, 160); Off = C(95, 85, 105); Dark = true;
                    break;
                case "Ice":          // white, blue graphs and text
                    Bg = C(247, 250, 255); Card = C(230, 239, 250); CardHot = C(214, 228, 246); Line = C(204, 217, 235);
                    Text = C(16, 48, 96); Dim = C(88, 112, 148);
                    Green = C(22, 112, 224); Blue = C(70, 160, 240); Amber = C(196, 118, 0); Red = C(206, 32, 48);
                    Link = C(0, 96, 200); Grey = C(118, 128, 144); Off = C(160, 168, 180); Dark = false;
                    break;
                default:             // Stealth: the original dark look
                    name = "Stealth";
                    Bg = C(28, 28, 33); Card = C(38, 38, 45); CardHot = C(58, 58, 68); Line = C(55, 55, 64);
                    Text = C(236, 236, 240); Dim = C(150, 150, 160);
                    Green = C(76, 194, 107); Blue = C(76, 154, 255); Amber = C(240, 168, 48); Red = C(229, 72, 77);
                    Link = C(122, 176, 255); Grey = C(138, 143, 152); Off = C(90, 90, 98); Dark = true;
                    break;
            }
            Name = name;
        }

        // Text that reads on top of a coloured button: black on light colours, white on dark ones.
        public static Color On(Color bg) { return bg.R * 0.299 + bg.G * 0.587 + bg.B * 0.114 > 150 ? Color.Black : Color.White; }

        // The choice is per user (each Windows account can pick its own).
        static string SettingsFile { get { return Path.Combine(Environment.GetFolderPath(Environment.SpecialFolder.LocalApplicationData), "LoopMedic", "theme.txt"); } }
        static string Load() { try { return File.Exists(SettingsFile) ? File.ReadAllText(SettingsFile).Trim() : "Stealth"; } catch { return "Stealth"; } }
        public static void Save() { try { Directory.CreateDirectory(Path.GetDirectoryName(SettingsFile)); File.WriteAllText(SettingsFile, Name); } catch { } }

        public static Color ForState(string s)
        {
            switch (s)
            {
                case "ARMED": return Green;
                case "WATCHING": return Amber;
                case "DEGRADED": return Amber;
                case "TRIPPED": return Red;
                case "TEST": case "STARTING": return Blue;
                case "PAUSED": return Grey;
                default: return Off;
            }
        }
        public static string Label(string s)
        {
            switch (s)
            {
                case "ARMED": return "ARMED";
                case "WATCHING": return "WATCHING";
                case "DEGRADED": return "DEGRADED";
                case "TRIPPED": return "TRIPPED";
                case "TEST": return "TEST";
                case "STARTING": return "STARTING";
                case "PAUSED": return "PAUSED";
                default: return "OFFLINE";
            }
        }
        public static float Scale()
        {
            using (var g = Graphics.FromHwnd(IntPtr.Zero)) return g.DpiX / 96f;
        }

        [DllImport("dwmapi.dll")] static extern int DwmSetWindowAttribute(IntPtr hwnd, int attr, ref int value, int size);
        public static void RoundCorners(Form f)
        {
            try { int pref = 2; DwmSetWindowAttribute(f.Handle, 33, ref pref, 4); } catch { }   // Win11 rounded corners
            try { int dark = Dark ? 1 : 0; DwmSetWindowAttribute(f.Handle, 20, ref dark, 4); } catch { }
        }

        public static GraphicsPath Rounded(RectangleF r, float rad)
        {
            var p = new GraphicsPath();
            float d = rad * 2;
            p.AddArc(r.X, r.Y, d, d, 180, 90);
            p.AddArc(r.Right - d, r.Y, d, d, 270, 90);
            p.AddArc(r.Right - d, r.Bottom - d, d, d, 0, 90);
            p.AddArc(r.X, r.Bottom - d, d, d, 90, 90);
            p.CloseFigure();
            return p;
        }

        public static void Spark(Graphics g, RectangleF r, List<double> v, Color c, double floor)
        {
            if (v.Count < 2) return;
            double min = v.Min(), max = v.Max();
            double pad = Math.Max((max - min) * 0.15, Math.Max(Math.Abs(max) * 0.03, floor));
            min -= pad; max += pad;
            var pts = new PointF[v.Count];
            for (int i = 0; i < v.Count; i++)
            {
                float x = r.X + r.Width * i / (float)(Math.Max(v.Count - 1, 1));
                float y = r.Bottom - (float)((v[i] - min) / (max - min)) * r.Height;
                pts[i] = new PointF(x, y);
            }
            using (var pen = new Pen(c, 1.6f)) g.DrawLines(pen, pts);
        }
    }

    class Btn { public RectangleF Rect; public string Text; public Action Click; public bool Accent; }

    // ------------------------------------------------------------------ tray
    class TrayContext : ApplicationContext
    {
        readonly string dir, statusPath, controlDir, logPath;
        readonly NotifyIcon ni = new NotifyIcon();
        readonly System.Windows.Forms.Timer timer = new System.Windows.Forms.Timer();
        readonly Dictionary<string, Icon> icons = new Dictionary<string, Icon>();
        readonly ContextMenuStrip menu = new ContextMenuStrip();
        readonly ToolStripMenuItem miPause, miTest, miThemes;
        public readonly List<double> PumpHist = new List<double>(), CpuHist = new List<double>();
        public Status Current = new Status();
        DateTime lastAt = DateTime.MinValue, lastHeartbeat = DateTime.MinValue;
        string iconState = null;
        PanelForm panel;
        TripForm popup;
        string dismissedTrip = null;

        public TrayContext(string dir)
        {
            this.dir = dir;
            statusPath = Path.Combine(dir, "status.json");
            controlDir = Path.Combine(dir, "control");
            logPath = Path.Combine(dir, "loopmedic.log");

            menu.Items.Add("Open panel", null, delegate { ShowPanel(); });
            miPause = new ToolStripMenuItem("Pause 30 min", null, delegate { TogglePause(); });
            miTest = new ToolStripMenuItem("Run test (no shutdown)", null, delegate { RunTest(); });
            menu.Items.Add(miPause);
            menu.Items.Add(miTest);
            menu.Items.Add("Open log", null, delegate { OpenLog(); });
            miThemes = new ToolStripMenuItem("Themes");
            foreach (string n in Theme.Names)
            {
                string name = n;
                miThemes.DropDownItems.Add(new ToolStripMenuItem(name, null, delegate { SetTheme(name); }));
            }
            menu.Items.Add(miThemes);
            menu.Items.Add(new ToolStripSeparator());
            menu.Items.Add("Exit tray icon (protection keeps running)", null, delegate { ExitApp(); });
            menu.Opening += delegate
            {
                miPause.Text = Current.State == "PAUSED" ? "Resume protection" : "Pause 30 min";
                foreach (ToolStripMenuItem t in miThemes.DropDownItems) t.Checked = t.Text == Theme.Name;
            };

            ni.ContextMenuStrip = menu;
            ni.MouseClick += (s, e) => { if (e.Button == MouseButtons.Left) TogglePanel(); };
            ni.Visible = true;

            timer.Interval = 1000;
            timer.Tick += delegate { Tick(); };
            timer.Start();
            Tick();
            NotifyIfUpdated();
        }

        void Tick()
        {
            // One failed read (the watchdog swapping the file in at that instant) must not count as "offline":
            // keep the last good status until it is genuinely 20 s old.
            var st = Status.Load(statusPath);
            if (st.ReadOk) lastGood = st;
            else if (lastGood != null && (DateTime.Now - lastGood.At).TotalSeconds <= 20) st = lastGood;
            Current = st;
            if (Current.At != lastAt && Current.State != "OFFLINE")
            {
                lastAt = Current.At;
                if (Current.PumpRpm.HasValue) Push(PumpHist, Current.PumpRpm.Value);
                if (Current.CpuTemp.HasValue) Push(CpuHist, Current.CpuTemp.Value);
            }
            UpdateIcon();
            Notify();

            if ((DateTime.Now - lastHeartbeat).TotalSeconds >= 5)
            {
                lastHeartbeat = DateTime.Now;
                WriteControl("tray.json", "at", DateTime.Now, false);
            }

            bool tripping = (Current.State == "TRIPPED" || Current.State == "TEST") && Current.TripDeadline.HasValue;
            string tripKey = tripping ? Current.TripDeadline.Value.ToString("o") : null;
            if (tripping && popup == null && tripKey != dismissedTrip)
            {
                popup = new TripForm(this, Current);
                popup.FormClosed += delegate { dismissedTrip = tripKey; popup = null; };
                popup.Show();
                popup.Activate();
            }
            if (!tripping && popup != null) popup.Close();
            if (panel != null && panel.Visible) panel.Invalidate();
        }

        static void Push(List<double> l, double v) { l.Add(v); if (l.Count > 300) l.RemoveAt(0); }

        // Windows notifications when protection gets weaker or comes back.
        string lastProblem = null;          // what we last warned about ("" = all good)
        string pendingProblem = null;       // what we see right now, waiting to prove it isn't a blip
        DateTime pendingSince;
        Status lastGood;                    // last status that read cleanly
        readonly DateTime trayStarted = DateTime.Now;
        // "Why did my PC turn off?" - once per power-off, within a day of it, remembered per user.
        DateTime powerOffNoticeAt = DateTime.MinValue;
        readonly HashSet<string> shownPowerOffs = new HashSet<string>();
        void NotifyLastPowerOff()
        {
            if (!Current.LastPowerOffAt.HasValue || (DateTime.Now - Current.LastPowerOffAt.Value).TotalHours > 24) return;
            string key = Current.LastPowerOffAt.Value.ToString("o");
            if (shownPowerOffs.Contains(key)) return;       // this tray already showed it (even if the file below can't be written)
            shownPowerOffs.Add(key);
            // Remember every power-off already shown, not just the last one: two trays (e.g. a test tray) sharing
            // this file must not keep "un-seeing" each other's and re-showing the notice every second.
            string seenFile = Path.Combine(Environment.GetFolderPath(Environment.SpecialFolder.LocalApplicationData), "LoopMedic", "seen-poweroff.txt");
            try
            {
                var seen = File.Exists(seenFile) ? File.ReadAllLines(seenFile).Select(l => l.Trim()).Where(l => l.Length > 0).ToList() : new List<string>();
                if (seen.Contains(key)) return;
                seen.Add(key);
                Directory.CreateDirectory(Path.GetDirectoryName(seenFile));
                File.WriteAllLines(seenFile, seen.Skip(Math.Max(0, seen.Count - 20)).ToArray());
            }
            catch { }
            powerOffNoticeAt = DateTime.Now;
            Notice("LoopMedic powered off this PC",
                Current.LastPowerOffAt.Value.ToString("t") + ": " + (Current.LastPowerOffReason ?? "") + ". Check your cooler before heavy use. Details: Event Viewer > Application > LoopMedic.",
                true, ToolTipIcon.Warning);
        }

        void Notify()
        {
            NotifyLastPowerOff();
            if ((DateTime.Now - powerOffNoticeAt).TotalSeconds < 15) return;   // let that one be read first
            string s = Current.State;
            string problem;
            if (s == "OFFLINE") problem = "LoopMedic isn't sending data - your PC may NOT be protected. Check the LoopMedic task or reinstall.";
            else if (Current.Degraded.Count > 0) problem = string.Join("\n", Current.Degraded.ToArray());
            else problem = "";
            if (s == "OFFLINE" && (DateTime.Now - trayStarted).TotalSeconds < 45) return;   // give it a moment after logon
            // only notify once a change has held for 5 s, so a one-poll hiccup doesn't pop "off" then "back on"
            if (problem != pendingProblem) { pendingProblem = problem; pendingSince = DateTime.Now; }
            if ((DateTime.Now - pendingSince).TotalSeconds < 5) return;
            if (lastProblem == null) { lastProblem = problem; if (problem != "") Warn(problem); return; }
            if (problem == lastProblem) return;
            if (problem != "") Warn(problem);
            else Notice("LoopMedic", "Protection is back to normal.", false, ToolTipIcon.Info);
            lastProblem = problem;
        }
        void Warn(string text)
        {
            if (text.Length > 250) text = text.Substring(0, 247) + "...";
            Notice("LoopMedic - protection reduced", text, false, ToolTipIcon.Warning);
        }

        void SetTheme(string name)
        {
            Theme.Apply(name);
            Theme.Save();
            foreach (var ic in icons.Values) ic.Dispose();
            icons.Clear();
            iconState = null;
            UpdateIcon();
            if (panel != null) { panel.BackColor = Theme.Bg; Theme.RoundCorners(panel); panel.Invalidate(); }
        }

        void UpdateIcon()
        {
            string s = Current.State;
            string tip;
            if (s == "OFFLINE") tip = "LoopMedic: no data - is it running?";
            else
            {
                tip = "LoopMedic " + Theme.Label(s);
                if (Current.PumpRpm.HasValue) tip += " · " + Current.Cooler + " " + Current.PumpRpm.Value.ToString("N0") + " rpm";
                if (Current.CpuTemp.HasValue) tip += " · CPU " + Current.CpuTemp.Value.ToString("0") + "°C";
            }
            if (tip.Length > 63) tip = tip.Substring(0, 63);
            ni.Text = tip;
            if (s == iconState) return;
            iconState = s;
            Icon ic;
            if (!icons.TryGetValue(s, out ic)) { ic = MakeIcon(s); icons[s] = ic; }
            ni.Icon = ic;
        }

        [DllImport("user32.dll")] static extern bool DestroyIcon(IntPtr h);

        public static Icon MakeIcon(string state)
        {
            using (var bmp = Shield(Math.Max(SystemInformation.SmallIconSize.Width, 16), state))
            {
                IntPtr h = bmp.GetHicon();
                var ic = (Icon)Icon.FromHandle(h).Clone();
                DestroyIcon(h);
                return ic;
            }
        }

        public static Bitmap Shield(int sz, string state)
        {
            var bmp = new Bitmap(sz, sz);
            using (var g = Graphics.FromImage(bmp))
            {
                g.SmoothingMode = SmoothingMode.AntiAlias;
                g.TextRenderingHint = TextRenderingHint.AntiAliasGridFit;
                float w = sz, m = sz * 0.06f;
                var p = new GraphicsPath();
                p.AddLine(w / 2, m, w - m * 1.5f, w * 0.2f);
                p.AddLine(w - m * 1.5f, w * 0.2f, w - m * 1.5f, w * 0.5f);
                p.AddBezier(w - m * 1.5f, w * 0.5f, w - m * 2, w * 0.78f, w * 0.68f, w * 0.9f, w / 2, w - m);
                p.AddBezier(w / 2, w - m, w * 0.32f, w * 0.9f, m * 2, w * 0.78f, m * 1.5f, w * 0.5f);
                p.AddLine(m * 1.5f, w * 0.5f, m * 1.5f, w * 0.2f);
                p.CloseFigure();
                using (var b = new SolidBrush(Theme.ForState(state))) g.FillPath(b, p);
                using (var pen = new Pen(Color.FromArgb(160, 0, 0, 0), Math.Max(1f, sz / 16f))) g.DrawPath(pen, p);
                string glyph = state == "TRIPPED" || state == "WATCHING" ? "!" : state == "PAUSED" ? "II" : state == "OFFLINE" || state == "DEGRADED" ? "?" : state == "TEST" ? "T" : "";
                if (glyph == "")
                {
                    using (var pen = new Pen(Color.White, sz / 8f) { StartCap = LineCap.Round, EndCap = LineCap.Round, LineJoin = LineJoin.Round })
                        g.DrawLines(pen, new[] { new PointF(w * 0.32f, w * 0.5f), new PointF(w * 0.46f, w * 0.64f), new PointF(w * 0.7f, w * 0.36f) });
                }
                else
                {
                    using (var f = new Font("Segoe UI", sz * 0.42f, FontStyle.Bold, GraphicsUnit.Pixel))
                    using (var sf = new StringFormat { Alignment = StringAlignment.Center, LineAlignment = StringAlignment.Center })
                        g.DrawString(glyph, f, Brushes.White, new RectangleF(0, -sz * 0.02f, w, w), sf);
                }
            }
            return bmp;
        }

        // A notification: a Windows toast (stays in the notification center; important ones stay on screen until
        // dismissed), or the old tray balloon if toasts aren't available.
        void Notice(string title, string text, bool important, ToolTipIcon icon)
        {
            if (!Toast.Show(title, text, important)) ni.ShowBalloonTip(important ? 15000 : 10000, title, text, icon);
        }

        // ---- actions
        public bool WriteControl(string file, string field, DateTime value, bool showError)
        {
            try
            {
                if (!Directory.Exists(controlDir)) { try { Directory.CreateDirectory(controlDir); } catch { } }   // the watchdog also recreates it
                File.WriteAllText(Path.Combine(controlDir, file), "{\"" + field + "\":\"" + value.ToString("o", CultureInfo.InvariantCulture) + "\"}");
                return true;
            }
            catch
            {
                if (showError) ni.ShowBalloonTip(4000, "LoopMedic", "Couldn't reach LoopMedic's control folder. Reinstall LoopMedic.", ToolTipIcon.Warning);
                return false;
            }
        }
        public void TogglePause()
        {
            if (Current.State == "PAUSED") { WriteControl("pause.json", "until", DateTime.Now.AddMinutes(-1), true); Balloon("Protection resumed."); }
            else if (WriteControl("pause.json", "until", DateTime.Now.AddMinutes(30), true)) Balloon("Protection paused for 30 minutes. It resumes on its own.");
        }
        public void RunTest()
        {
            if (WriteControl("test.json", "requested", DateTime.Now, true)) Balloon("Test requested - the trip pop-up should appear within a few seconds. Nothing will shut down.");
        }
        public void OpenLog() { try { Process.Start("notepad.exe", "\"" + logPath + "\""); } catch { } }
        public void Balloon(string text) { ni.ShowBalloonTip(4000, "LoopMedic", text, ToolTipIcon.Info); }

        // ---- updates (only when the user clicks the version in the panel; nothing checks in the background)
        readonly Control ui = new Control();   // hands results from the download thread back to the UI thread
        UpdateForm updateForm;
        public void CheckForUpdates()
        {
            if (Updater.Busy) return;
            if (updateForm != null && !updateForm.IsDisposed) { updateForm.Activate(); return; }
            if (!ui.IsHandleCreated) ui.CreateControl();
            string current = Current.Version.Length > 0 ? Current.Version : Updater.TrayVersion;
            if (panel != null) panel.Invalidate();
            Updater.Check(current, delegate (Updater.Release rel, string error)
            {
                ui.BeginInvoke((Action)delegate
                {
                    if (panel != null) panel.Invalidate();
                    if (error != null) Notice("LoopMedic - couldn't check for updates", error, false, ToolTipIcon.Warning);
                    else if (rel == null) Notice("LoopMedic is up to date", "You're on the latest version (" + current + ").", false, ToolTipIcon.Info);
                    else { updateForm = new UpdateForm(this, current, rel); updateForm.Show(); updateForm.Activate(); }
                });
            });
        }
        public void RunOnUi(Action a) { if (!ui.IsHandleCreated) ui.CreateControl(); ui.BeginInvoke(a); }

        // After an update the new tray says so once.
        void NotifyIfUpdated()
        {
            try
            {
                string f = Path.Combine(Environment.GetFolderPath(Environment.SpecialFolder.LocalApplicationData), "LoopMedic", "last-version.txt");
                string before = File.Exists(f) ? File.ReadAllText(f).Trim() : "";
                if (before == Updater.TrayVersion) return;
                Directory.CreateDirectory(Path.GetDirectoryName(f));
                File.WriteAllText(f, Updater.TrayVersion);
                if (before.Length > 0) Notice("LoopMedic updated", "Now running version " + Updater.TrayVersion + " (was " + before + "). Your settings were kept.", false, ToolTipIcon.Info);
            }
            catch { }
        }

        public void CancelShutdown(bool isTest)
        {
            if (!isTest) RunHidden("shutdown.exe", "/a");
            WriteControl("cancel.json", "at", DateTime.Now, true);
            Balloon(isTest ? "Test finished. In a real trip the PC would have shut down." : "Shutdown cancelled. Check your cooling! LoopMedic re-arms in 2 minutes.");
        }
        // Ask the watchdog's power-off helper to cut power now (forced, skips Windows Update).
        // If nothing happens within 5 s, fall back to a plain forced shutdown.
        public void ShutdownNow()
        {
            WriteControl("poweroff.json", "at", DateTime.Now, false);
            var fallback = new System.Windows.Forms.Timer { Interval = 5000 };
            fallback.Tick += delegate { fallback.Stop(); RunHidden("shutdown.exe", "/s /f /t 0"); };
            fallback.Start();
        }
        static void RunHidden(string exe, string args)
        {
            try { Process.Start(new ProcessStartInfo(exe, args) { CreateNoWindow = true, UseShellExecute = false }); } catch { }
        }

        DateTime panelHiddenAt = DateTime.MinValue;
        void TogglePanel()
        {
            if (panel != null && panel.Visible) { panel.Hide(); return; }
            if ((DateTime.Now - panelHiddenAt).TotalMilliseconds < 300) return;   // click on icon closed it
            ShowPanel();
        }
        void ShowPanel()
        {
            if (panel == null)
            {
                panel = new PanelForm(this);
                panel.Deactivate += delegate { panel.Hide(); panelHiddenAt = DateTime.Now; };
            }
            panel.PlaceNearTray();
            panel.Show();
            panel.Activate();
        }
        public void OpenPanelSoon()
        {
            var once = new System.Windows.Forms.Timer { Interval = 2500 };
            once.Tick += delegate { once.Stop(); once.Dispose(); ShowPanel(); };
            once.Start();
        }
        void ExitApp()
        {
            ni.Visible = false;
            timer.Stop();
            Application.Exit();
        }
    }

    // ------------------------------------------------------------------ panel
    class PanelForm : Form
    {
        readonly TrayContext app;
        readonly float S;
        readonly List<Btn> buttons = new List<Btn>();
        Btn hover;
        int measured;   // height the last paint actually needed

        public PanelForm(TrayContext app)
        {
            this.app = app;
            S = Theme.Scale();
            FormBorderStyle = FormBorderStyle.None;
            StartPosition = FormStartPosition.Manual;
            ShowInTaskbar = false;
            TopMost = true;
            BackColor = Theme.Bg;
            DoubleBuffered = true;
            Width = (int)(340 * S);
            Height = (int)(300 * S);
            KeyPreview = true;
            KeyDown += (s, e) => { if (e.KeyCode == Keys.Escape) Hide(); };
            MouseMove += (s, e) => { var h = buttons.FirstOrDefault(b => b.Rect.Contains(e.Location)); if (h != hover) { hover = h; Cursor = h != null ? Cursors.Hand : Cursors.Default; Invalidate(); } };
            MouseLeave += delegate { hover = null; Invalidate(); };
            MouseClick += (s, e) => { var b = buttons.FirstOrDefault(x => x.Rect.Contains(e.Location)); if (b != null) b.Click(); };
        }
        protected override void OnHandleCreated(EventArgs e) { base.OnHandleCreated(e); Theme.RoundCorners(this); }
        protected override CreateParams CreateParams { get { var cp = base.CreateParams; cp.ExStyle |= 0x80; return cp; } }   // tool window: no Alt+Tab

        int ContentHeight()
        {
            var st = app.Current;
            int fans = st.Fans.Count(f => f.Rpm.HasValue && f.Rpm.Value > 0);
            bool empties = st.Fans.Any(f => !f.Rpm.HasValue || f.Rpm.Value <= 0);
            if (measured > 0) return measured;
            return (int)((340 + fans * 22 + (empties ? 18 : 0) + (st.Fans.Count == 0 ? 18 : 0)) * S);
        }

        public void PlaceNearTray()
        {
            Height = ContentHeight();
            var scr = Screen.FromPoint(Cursor.Position);
            var wa = scr.WorkingArea;
            int m = (int)(12 * S);
            int x = Math.Min(Math.Max(Cursor.Position.X - Width / 2, wa.Left + m), wa.Right - Width - m);
            int y = wa.Bottom < scr.Bounds.Bottom ? wa.Bottom - Height - m : wa.Top + m;
            Location = new Point(x, y);
        }

        protected override void OnPaint(PaintEventArgs e)
        {
            var g = e.Graphics;
            g.SmoothingMode = SmoothingMode.AntiAlias;
            g.TextRenderingHint = TextRenderingHint.ClearTypeGridFit;
            var st = app.Current;
            buttons.Clear();
            float W = Width, pad = 16 * S, y = 14 * S;

            using (var fBig = new Font("Segoe UI Semibold", 22f * S, GraphicsUnit.Pixel))
            using (var fMid = new Font("Segoe UI", 13f * S, GraphicsUnit.Pixel))
            using (var fSmall = new Font("Segoe UI", 11.5f * S, GraphicsUnit.Pixel))
            using (var fTiny = new Font("Segoe UI Semibold", 10f * S, GraphicsUnit.Pixel))
            using (var bText = new SolidBrush(Theme.Text))
            using (var bDim = new SolidBrush(Theme.Dim))
            {
                using (var fHead = new Font("Segoe UI Semibold", 16f * S, GraphicsUnit.Pixel))
                {
                    g.DrawString("LoopMedic", fHead, bText, pad, y);
                    // The version doubles as "check for updates": soft blue + hand cursor on hover, click to check.
                    string ver = st.Version.Length > 0 ? st.Version : Updater.TrayVersion;
                    var hs = g.MeasureString("LoopMedic", fHead);
                    string vtext = Updater.Busy ? "v" + ver + "  checking..." : "v" + ver;
                    var vs = g.MeasureString(vtext, fTiny);
                    var vr = new RectangleF(pad + hs.Width, y + hs.Height - vs.Height - 3 * S, vs.Width, vs.Height);
                    var link = new Btn { Rect = RectangleF.Inflate(vr, 2 * S, 3 * S), Text = "#version", Click = delegate { app.CheckForUpdates(); } };
                    buttons.Add(link);
                    using (var vb = new SolidBrush(hover != null && hover.Text == "#version" ? Theme.Link : Theme.Dim))
                        g.DrawString(vtext, fTiny, vb, vr.X, vr.Y);
                }

                // state pill
                Color sc = Theme.ForState(st.State);
                string label = Theme.Label(st.State) + (st.DryRun ? " (DRY RUN)" : "");
                var ls = g.MeasureString(label, fTiny);
                var pill = new RectangleF(W - pad - ls.Width - 26 * S, y + 2 * S, ls.Width + 26 * S, 22 * S);
                using (var pb = new SolidBrush(Color.FromArgb(40, sc))) using (var path = Theme.Rounded(pill, 11 * S)) g.FillPath(pb, path);
                using (var db = new SolidBrush(sc)) g.FillEllipse(db, pill.X + 9 * S, pill.Y + 7.5f * S, 7 * S, 7 * S);
                using (var tb = new SolidBrush(sc)) g.DrawString(label, fTiny, tb, pill.X + 19 * S, pill.Y + (pill.Height - ls.Height) / 2);
                y += 38 * S;

                // what's weakening the protection, if anything
                if (st.Degraded.Count > 0)
                {
                    using (var ab = new SolidBrush(Theme.Amber))
                    {
                        foreach (var line in st.Degraded)
                        {
                            var box = new RectangleF(pad, y, W - pad * 2, 200 * S);
                            var sz = g.MeasureString("⚠ " + line, fSmall, (int)box.Width);
                            g.DrawString("⚠ " + line, fSmall, ab, box);
                            y += sz.Height + 2 * S;
                        }
                    }
                    y += 6 * S;
                }

                // big rows
                DrawMetric(g, ref y, pad, W, st.Cooler, st.PumpRpm.HasValue ? st.PumpRpm.Value.ToString("N0") : "—", "rpm",
                    // Pump graph gets headroom of 10% of the speed above and below, so the sensor's normal
                    // one-reading wiggles (a few %) look like small ticks instead of filling the graph.
                    PumpColor(st), app.PumpHist, Math.Max(50, (st.PumpRpm ?? 0) * 0.10), fBig, fMid, fSmall, bDim);
                DrawMetric(g, ref y, pad, W, "CPU", st.CpuTemp.HasValue ? st.CpuTemp.Value.ToString("0.0") : "—", "°C",
                    CpuColor(st), app.CpuHist, 1, fBig, fMid, fSmall, bDim);

                // limits + today
                string trips = (st.PumpStoppedRpm.HasValue ? "Trips: " + st.cooler + " stopped (≤ " + st.PumpStoppedRpm.Value.ToString("N0") + " rpm)" : "Trips: " + st.cooler + " < " + (st.PumpMinRpm ?? 0).ToString("N0") + " rpm") +
                    (st.CpuMaxTemp.HasValue && st.CpuMaxTemp.Value > 0 ? "  ·  CPU ≥ " + st.CpuMaxTemp.Value.ToString("0") + " °C" : "  ·  CPU trip off");
                g.DrawString(trips, fSmall, bDim, pad, y); y += 18 * S;
                if (st.PumpStoppedRpm.HasValue)
                {
                    string warns = "Warns: " + st.cooler + " < " + (st.PumpMinRpm ?? 0).ToString("N0") + " rpm";
                    if (st.CpuTemp.HasValue && st.CpuMaxTemp.HasValue && st.CpuMaxTemp.Value > 0)
                        warns += "  ·  " + Math.Max(0, st.CpuMaxTemp.Value - st.CpuTemp.Value).ToString("0") + "° headroom";
                    g.DrawString(warns, fSmall, bDim, pad, y); y += 18 * S;
                }
                string today = "Today: CPU peak " + (st.DayMaxTemp.HasValue ? st.DayMaxTemp.Value.ToString("0") + " °C" : "—") +
                               "  ·  " + st.cooler + " min " + (st.DayMinPump.HasValue ? st.DayMinPump.Value.ToString("N0") + " rpm" : "—");
                g.DrawString(today, fSmall, bDim, pad, y); y += 18 * S;
                if (st.SelfTestAt.HasValue)
                {
                    string stx = "Self-test: " + (st.SelfTestOk ? "OK" : "FAILED") + " (" + st.SelfTestAt.Value.ToString("MMM d") + ")";
                    using (var sb = new SolidBrush(st.SelfTestOk ? Theme.Dim : Theme.Red)) g.DrawString(stx, fSmall, sb, pad, y);
                    y += 18 * S;
                }
                if (st.LastPowerOffAt.HasValue)
                {
                    using (var ab = new SolidBrush(Theme.Amber))
                    {
                        string lpx = "⚡ LoopMedic powered off this PC " + st.LastPowerOffAt.Value.ToString("MMM d, h:mm tt") + ": " + (st.LastPowerOffReason ?? "");
                        var box = new RectangleF(pad, y, W - pad * 2, 200 * S);
                        var sz = g.MeasureString(lpx, fSmall, (int)box.Width);
                        g.DrawString(lpx, fSmall, ab, box);
                        y += sz.Height + 2 * S;
                    }
                }
                y += 4 * S;
                if (st.State == "PAUSED" && st.PausedUntil.HasValue)
                {
                    using (var ab = new SolidBrush(Theme.Amber)) g.DrawString("Paused until " + st.PausedUntil.Value.ToString("t") + " - not protecting!", fSmall, ab, pad, y - 4 * S);
                }

                // fans
                using (var lp = new Pen(Theme.Line)) g.DrawLine(lp, pad, y, W - pad, y);
                y += 8 * S;
                g.DrawString("FANS", fTiny, bDim, pad, y); y += 18 * S;
                var live = st.Fans.Where(f => f.Rpm.HasValue && f.Rpm.Value > 0).ToList();
                double maxRpm = Math.Max(1, live.Count > 0 ? live.Max(f => f.Rpm.Value) : 1);
                foreach (var f in live)
                {
                    string name = f.Name + (f.IsPump ? "  · " + st.Cooler : "");
                    using (var nb = new SolidBrush(f.IsPump ? Theme.Green : Theme.Text)) g.DrawString(name, fSmall, nb, pad, y);
                    string r = f.Rpm.Value.ToString("N0") + " rpm";
                    var rs = g.MeasureString(r, fSmall);
                    g.DrawString(r, fSmall, bText, W - pad - rs.Width, y);
                    var bar = new RectangleF(pad + 120 * S, y + 7 * S, W - pad * 2 - 120 * S - 80 * S, 5 * S);
                    using (var bb = new SolidBrush(Theme.Card)) using (var bp = Theme.Rounded(bar, 2.5f * S)) g.FillPath(bb, bp);
                    var fill = new RectangleF(bar.X, bar.Y, (float)(bar.Width * f.Rpm.Value / maxRpm), bar.Height);
                    if (fill.Width > 5 * S)
                        using (var fb = new SolidBrush(f.IsPump ? Theme.Green : Theme.Blue)) using (var fp = Theme.Rounded(fill, 2.5f * S)) g.FillPath(fb, fp);
                    y += 22 * S;
                }
                int empty = st.Fans.Count - live.Count;
                if (empty > 0) { g.DrawString(empty + " empty header" + (empty == 1 ? "" : "s") + " (0 rpm)", fSmall, bDim, pad, y); y += 18 * S; }
                if (st.Fans.Count == 0) { g.DrawString("No fan data yet", fSmall, bDim, pad, y); y += 18 * S; }

                // buttons
                y += 6 * S;
                using (var lp = new Pen(Theme.Line)) g.DrawLine(lp, pad, y, W - pad, y);
                y += 10 * S;
                float bw = (W - pad * 2 - 16 * S) / 3f, bh = 30 * S;
                string pauseText = st.State == "PAUSED" ? "Resume" : "Pause 30 min";
                AddButton(g, new RectangleF(pad, y, bw, bh), pauseText, delegate { app.TogglePause(); }, fSmall, st.State == "PAUSED");
                AddButton(g, new RectangleF(pad + bw + 8 * S, y, bw, bh), "Run test", delegate { app.RunTest(); }, fSmall, false);
                AddButton(g, new RectangleF(pad + (bw + 8 * S) * 2, y, bw, bh), "Open log", delegate { app.OpenLog(); }, fSmall, false);
                y += bh + 8 * S;

                // Live indicator: data up to 10 s old = Live; 10-20 s = waiting; older = offline
                int age = Math.Max(0, (int)(DateTime.Now - st.At).TotalSeconds);
                string upd; Color uc;
                if (st.State == "OFFLINE") { upd = "Offline - no data from LoopMedic"; uc = Theme.Dim; }
                else if (age <= 10) { upd = "Live"; uc = Theme.Green; }
                else { upd = "Waiting for sensors… (" + age + " s old)"; uc = Theme.Amber; }
                using (var ub = new SolidBrush(uc))
                {
                    float dot = 7 * S, th = g.MeasureString(upd, fTiny).Height;
                    g.FillEllipse(ub, pad, y + (th - dot) / 2, dot, dot);
                    g.DrawString(upd, fTiny, ub, pad + dot + 5 * S, y);
                }
                var by = g.MeasureString("by Jacob Stiltner", fTiny);
                g.DrawString("by Jacob Stiltner", fTiny, bDim, W - pad - by.Width, y);
                y += by.Height + 12 * S;
            }

            // grow/shrink to fit what was drawn, keeping the bottom edge anchored near the tray
            int need = (int)Math.Ceiling(y);
            if (Math.Abs(need - Height) > 2)
            {
                measured = need;
                bool anchoredBottom = Top > Screen.FromControl(this).WorkingArea.Top + Screen.FromControl(this).WorkingArea.Height / 2;
                int bottom = Bottom;
                Height = need;
                if (anchoredBottom) Top = bottom - Height;
                Invalidate();
            }
        }

        static Color PumpColor(Status st)
        {
            if (!st.PumpRpm.HasValue) return Theme.Dim;
            if (st.PumpStoppedRpm.HasValue)
            {
                if (st.PumpRpm.Value <= st.PumpStoppedRpm.Value) return Theme.Red;       // stopped
                if (st.PumpMinRpm.HasValue && st.PumpRpm.Value < st.PumpMinRpm.Value) return Theme.Amber;   // slow
                return Theme.Green;
            }
            if (st.PumpMinRpm.HasValue && st.PumpRpm.Value < st.PumpMinRpm.Value) return Theme.Red;
            if (st.PumpMinRpm.HasValue && st.PumpRpm.Value < st.PumpMinRpm.Value * 2) return Theme.Amber;
            return Theme.Green;
        }
        static Color CpuColor(Status st)
        {
            if (!st.CpuTemp.HasValue) return Theme.Dim;
            double lim = st.CpuMaxTemp.HasValue && st.CpuMaxTemp.Value > 0 ? st.CpuMaxTemp.Value : 95;
            if (st.CpuTemp.Value >= lim) return Theme.Red;
            if (st.CpuTemp.Value >= lim - 10) return Theme.Amber;
            return Theme.Green;
        }

        void DrawMetric(Graphics g, ref float y, float pad, float W, string label, string value, string unit, Color c,
                        List<double> hist, double floor, Font fBig, Font fMid, Font fSmall, Brush bDim)
        {
            var card = new RectangleF(pad, y, W - pad * 2, 56 * S);
            using (var cb = new SolidBrush(Theme.Card)) using (var cp = Theme.Rounded(card, 8 * S)) g.FillPath(cb, cp);
            g.DrawString(label, fSmall, bDim, card.X + 12 * S, card.Y + 6 * S);
            using (var vb = new SolidBrush(c)) g.DrawString(value, fBig, vb, card.X + 9 * S, card.Y + 19 * S);
            var vs = g.MeasureString(value, fBig);
            g.DrawString(unit, fMid, bDim, card.X + 9 * S + vs.Width - 4 * S, card.Y + 30 * S);
            Theme.Spark(g, new RectangleF(card.X + card.Width * 0.5f, card.Y + 12 * S, card.Width * 0.5f - 12 * S, card.Height - 24 * S), hist, c, floor);
            y += card.Height + 8 * S;
        }

        void AddButton(Graphics g, RectangleF r, string text, Action click, Font f, bool accent)
        {
            var b = new Btn { Rect = r, Text = text, Click = click, Accent = accent };
            buttons.Add(b);
            bool hot = hover != null && hover.Text == text;
            Color bg = accent ? Theme.Green : (hot ? Theme.CardHot : Theme.Card);
            using (var bb = new SolidBrush(bg)) using (var p = Theme.Rounded(r, 6 * S)) g.FillPath(bb, p);
            using (var sf = new StringFormat { Alignment = StringAlignment.Center, LineAlignment = StringAlignment.Center })
            using (var tb = new SolidBrush(accent ? Theme.On(Theme.Green) : Theme.Text)) g.DrawString(text, f, tb, r, sf);
        }
    }

    // ------------------------------------------------------------------ trip pop-up
    // Update check (only when the user clicks the version in the panel). Asks GitHub for the latest release,
    // downloads LoopMedic.zip + LoopMedic.zip.sig, and refuses anything the release key didn't sign.
    static class Updater
    {
        public const string Repo = "Jakestatefarm2020/LoopMedic";
        // Public half of the LoopMedic release signing key (RSA-3072). The private half never leaves the author's
        // PC; a release that wasn't signed with it is never installed, even if someone got into the GitHub account.
        const string KeyModulus = "90PksaPHuTI9JIRU4CFL7dWDKaUDSGOrGCzGP8uwaAW8UqZsEfGJY9aeQY9cI1NcPOrwDWSBJZgkYC3brpgKLwzt1RCtwGS4+cuB/bEsYfFj7y5wUv58k/iXxSwJhYZhrVBhnoU48g/FEL/oZyQrqJsLw139aAW54AxxFS4Q2fehI2nFuTiB6vRvOQILXvW756vnuccAILq1Duxfn+pZpfZq5111pfFopqaJaE1y9nEZ98trjDCgJuXI9WSIS2enYprBs3Wh34tkbjrOV/pPjzHxiuZUD7i+xfS2UhMmUwTQ5ifyL3kmNO7mVnoKxshSen5JhEhhnN5XQCNa3Y3PcGEV+CTrZn4hs1aNp4PX7HZyA1nu6XFOMVn8G5Sy+Meq4YxrvwZuQ1KPpReTIsyCv4gHhOfZJVgiNvHIvmH7L5TywQnYhV5EMc0rt/VJNw+fCOCjAeHdHWUpiaGb5WpAqziWP3ExDppYTjpGo7cmf38wHBxSNomRqOl6WXpjLZnF";
        const string KeyExponent = "AQAB";

        public class Release { public string Version, Notes, PageUrl, ZipUrl, SigUrl; }
        public static volatile bool Busy;
        public static string TrayVersion
        {
            get { var v = Assembly.GetExecutingAssembly().GetName().Version; return v.Major + "." + v.Minor + "." + v.Build; }
        }

        static WebClient Client()
        {
            ServicePointManager.SecurityProtocol |= (SecurityProtocolType)3072;   // TLS 1.2 (GitHub requires it)
            var wc = new WebClient();
            wc.Headers[HttpRequestHeader.UserAgent] = "LoopMedic-tray";
            return wc;
        }

        // done(release, error): release = null and error = null means "up to date".
        public static void Check(string current, Action<Release, string> done)
        {
            Busy = true;
            new Thread(delegate ()
            {
                Release found = null; string error = null;
                try
                {
                    string json;
                    using (var wc = Client())
                    {
                        wc.Headers[HttpRequestHeader.Accept] = "application/vnd.github+json";
                        json = wc.DownloadString("https://api.github.com/repos/" + Repo + "/releases/latest");
                    }
                    var d = new JavaScriptSerializer().Deserialize<Dictionary<string, object>>(json);
                    string tag = d.ContainsKey("tag_name") ? d["tag_name"] as string ?? "" : "";
                    var r = new Release
                    {
                        Version = tag.TrimStart('v', 'V'),
                        Notes = d.ContainsKey("body") ? d["body"] as string ?? "" : "",
                        PageUrl = d.ContainsKey("html_url") ? d["html_url"] as string : "https://github.com/" + Repo + "/releases"
                    };
                    var assets = d.ContainsKey("assets") ? d["assets"] as System.Collections.ArrayList : null;
                    if (assets != null)
                        foreach (var o in assets)
                        {
                            var a = o as Dictionary<string, object>;
                            if (a == null) continue;
                            string name = a["name"] as string, url = a["browser_download_url"] as string;
                            if (name == "LoopMedic.zip") r.ZipUrl = url;
                            if (name == "LoopMedic.zip.sig") r.SigUrl = url;
                        }
                    Version vNew, vNow;
                    if (Version.TryParse(r.Version, out vNew) && Version.TryParse(current, out vNow) && vNew > vNow) found = r;
                }
                catch (WebException e)
                {
                    var resp = e.Response as HttpWebResponse;
                    error = resp != null && resp.StatusCode == HttpStatusCode.NotFound ? "No release has been published on GitHub yet."
                          : "Couldn't reach GitHub (" + e.Message + ").";
                }
                catch (Exception e) { error = "Unexpected answer from GitHub (" + e.Message + ")."; }
                finally { Busy = false; }
                done(found, error);
            }) { IsBackground = true }.Start();
        }

        // Download + signature check + unzip. Returns the new version's Update-LoopMedic.ps1, or throws a readable message.
        public static string Download(Release r, Action<string> progress)
        {
            if (r.ZipUrl == null || r.SigUrl == null) throw new Exception("This release has no signed Windows package - download it from GitHub instead.");
            string dir = Path.Combine(Path.GetTempPath(), "LoopMedic-update-" + r.Version);
            if (Directory.Exists(dir)) Directory.Delete(dir, true);
            Directory.CreateDirectory(dir);
            byte[] zip, sig;
            using (var wc = Client())
            {
                progress("Downloading LoopMedic " + r.Version + "...");
                zip = wc.DownloadData(r.ZipUrl);
                sig = Convert.FromBase64String(wc.DownloadString(r.SigUrl).Trim());
            }
            progress("Checking the signature...");
            if (!Verify(zip, sig))
                throw new Exception("The download FAILED the signature check, so it was not installed. Please tell the author.");
            string zp = Path.Combine(dir, "LoopMedic.zip");
            File.WriteAllBytes(zp, zip);
            progress("Unpacking...");
            System.IO.Compression.ZipFile.ExtractToDirectory(zp, Path.Combine(dir, "x"));
            string script = Path.Combine(dir, "x", "LoopMedic", "Update-LoopMedic.ps1");
            if (!File.Exists(script)) throw new Exception("The package has no updater - download it from GitHub and run Install.bat.");
            return script;
        }

        public static bool Verify(byte[] data, byte[] sig)
        {
            try
            {
                using (var rsa = new RSACryptoServiceProvider())
                using (var sha = SHA256.Create())
                {
                    rsa.ImportParameters(new RSAParameters { Modulus = Convert.FromBase64String(KeyModulus), Exponent = Convert.FromBase64String(KeyExponent) });
                    return rsa.VerifyHash(sha.ComputeHash(data), CryptoConfig.MapNameToOID("SHA256"), sig);
                }
            }
            catch { return false; }
        }
    }

    // "LoopMedic x.y is available" window: what's new, Update now / Later, link to the full changelog.
    class UpdateForm : Form
    {
        readonly TrayContext app;
        readonly Updater.Release rel;
        readonly Label status;
        readonly Button bUpdate, bLater;

        public UpdateForm(TrayContext app, string current, Updater.Release rel)
        {
            this.app = app;
            this.rel = rel;
            float S = Theme.Scale();
            Text = "LoopMedic update";
            Icon = TrayContext.MakeIcon("ARMED");
            FormBorderStyle = FormBorderStyle.FixedDialog;
            MaximizeBox = MinimizeBox = false;
            StartPosition = FormStartPosition.CenterScreen;
            TopMost = true;
            BackColor = Theme.Bg;
            ForeColor = Theme.Text;
            Font = new Font("Segoe UI", 11f * S, GraphicsUnit.Pixel);
            ClientSize = new Size((int)(460 * S), (int)(340 * S));
            int pad = (int)(18 * S), w = ClientSize.Width - pad * 2;

            var title = new Label { Text = "LoopMedic " + rel.Version + " is available", Font = new Font("Segoe UI Semibold", 16f * S, GraphicsUnit.Pixel),
                                    Location = new Point(pad, pad), Size = new Size(w, (int)(26 * S)) };
            var sub = new Label { Text = "You have " + current + ". Updating keeps your settings.", ForeColor = Theme.Dim,
                                  Location = new Point(pad, pad + (int)(30 * S)), Size = new Size(w, (int)(18 * S)) };
            var head = new Label { Text = "WHAT'S NEW", Font = new Font("Segoe UI Semibold", 10f * S, GraphicsUnit.Pixel), ForeColor = Theme.Dim,
                                   Location = new Point(pad, pad + (int)(60 * S)), Size = new Size(w, (int)(16 * S)) };
            var notes = new TextBox { Multiline = true, ReadOnly = true, ScrollBars = ScrollBars.Vertical, BorderStyle = BorderStyle.None,
                                      BackColor = Theme.Card, ForeColor = Theme.Text, Text = Clean(rel.Notes), TabStop = false,
                                      Location = new Point(pad, pad + (int)(80 * S)), Size = new Size(w, (int)(150 * S)) };
            var link = new LinkLabel { Text = "Full changelog on GitHub", LinkColor = Theme.Link, ActiveLinkColor = Theme.Blue, VisitedLinkColor = Theme.Link,
                                       LinkBehavior = LinkBehavior.HoverUnderline, AutoSize = true, Location = new Point(pad, pad + (int)(238 * S)) };
            link.LinkClicked += delegate { try { Process.Start(rel.PageUrl); } catch { } };
            status = new Label { ForeColor = Theme.Dim, Location = new Point(pad, pad + (int)(258 * S)), Size = new Size(w, (int)(18 * S)) };
            int bh = (int)(32 * S), bw = (int)(120 * S), by = ClientSize.Height - pad - bh;
            bLater = Flat("Later", Theme.Card, new Rectangle(ClientSize.Width - pad - bw, by, bw, bh));
            bUpdate = Flat("Update now", Theme.Blue, new Rectangle(ClientSize.Width - pad - bw * 2 - (int)(8 * S), by, bw, bh));
            bLater.Click += delegate { Close(); };
            bUpdate.Click += delegate { StartUpdate(); };
            Controls.AddRange(new Control[] { title, sub, head, notes, link, status, bUpdate, bLater });
            AcceptButton = bUpdate;
            CancelButton = bLater;
        }
        protected override void OnHandleCreated(EventArgs e) { base.OnHandleCreated(e); Theme.RoundCorners(this); }

        static Button Flat(string text, Color bg, Rectangle r)
        {
            var b = new Button { Text = text, FlatStyle = FlatStyle.Flat, BackColor = bg, ForeColor = bg == Theme.Card ? Theme.Text : Theme.On(bg), Bounds = r, Cursor = Cursors.Hand };
            b.FlatAppearance.BorderSize = 0;
            return b;
        }

        // Release notes are Markdown; show them as plain text.
        static string Clean(string md)
        {
            if (string.IsNullOrEmpty(md)) return "(no notes for this release)";
            var lines = md.Replace("\r\n", "\n").Split('\n')
                          .Select(l => System.Text.RegularExpressions.Regex.Replace(l, @"^#+\s*", "").Replace("**", "").Replace("`", ""));
            return string.Join("\r\n", lines.ToArray()).Trim();
        }

        void StartUpdate()
        {
            bUpdate.Enabled = bLater.Enabled = false;
            status.ForeColor = Theme.Dim;
            new Thread(delegate ()
            {
                try
                {
                    string script = Updater.Download(rel, s => app.RunOnUi(delegate { status.Text = s; }));
                    app.RunOnUi(delegate { Launch(script); });
                }
                catch (Exception ex)
                {
                    app.RunOnUi(delegate { status.Text = ex.Message; status.ForeColor = Theme.Amber; bUpdate.Enabled = bLater.Enabled = true; });
                }
            }) { IsBackground = true }.Start();
        }

        void Launch(string script)
        {
            try
            {
                Process.Start(new ProcessStartInfo("powershell.exe", "-NoProfile -ExecutionPolicy Bypass -File \"" + script + "\"") { Verb = "runas", UseShellExecute = true });
                status.Text = "Installing - LoopMedic restarts in a moment. You can close this.";
                bLater.Text = "Close";
                bLater.Enabled = true;
            }
            catch (System.ComponentModel.Win32Exception)
            {
                status.Text = "Update cancelled - Windows didn't get admin permission.";
                status.ForeColor = Theme.Amber;
                bUpdate.Enabled = bLater.Enabled = true;
            }
        }
    }

    // Windows toast notifications. A desktop app needs an app ID registered under HKCU for them to show with a
    // name + icon and stay in the notification center. Any failure (older Windows, policy) returns false and the
    // caller falls back to a tray balloon.
    static class Toast
    {
        const string AppId = "LoopMedic.Tray";
        static bool registered, broken;

        public static bool Show(string title, string text, bool stayUntilDismissed)
        {
            if (broken) return false;
            try { Register(); ShowCore(title, text, stayUntilDismissed); return true; }
            catch { broken = true; return false; }
        }

        static void Register()
        {
            if (registered) return;
            registered = true;
            try
            {
                string dir = Path.Combine(Environment.GetFolderPath(Environment.SpecialFolder.LocalApplicationData), "LoopMedic");
                Directory.CreateDirectory(dir);
                string png = Path.Combine(dir, "loopmedic-icon.png");
                using (var bmp = TrayContext.Shield(64, "ARMED")) bmp.Save(png, System.Drawing.Imaging.ImageFormat.Png);
                using (var k = Microsoft.Win32.Registry.CurrentUser.CreateSubKey(@"Software\Classes\AppUserModelId\" + AppId))
                {
                    k.SetValue("DisplayName", "LoopMedic");
                    k.SetValue("IconUri", png);
                }
            }
            catch { }
        }

        // Kept separate so a missing WinRT (very old Windows) fails here, inside Show's try, not at startup.
        [System.Runtime.CompilerServices.MethodImpl(System.Runtime.CompilerServices.MethodImplOptions.NoInlining)]
        static void ShowCore(string title, string text, bool stay)
        {
            string x = "<toast" + (stay ? " scenario='reminder'" : "") + "><visual><binding template='ToastGeneric'>" +
                       "<text>" + System.Security.SecurityElement.Escape(title) + "</text>" +
                       "<text>" + System.Security.SecurityElement.Escape(text) + "</text></binding></visual>" +
                       (stay ? "<actions><action activationType='system' arguments='dismiss' content=''/></actions>" : "") + "</toast>";
            var xml = new Windows.Data.Xml.Dom.XmlDocument();
            xml.LoadXml(x);
            Windows.UI.Notifications.ToastNotificationManager.CreateToastNotifier(AppId).Show(new Windows.UI.Notifications.ToastNotification(xml));
        }
    }

    class TripForm : Form
    {
        readonly TrayContext app;
        readonly float S;
        readonly bool isTest;
        readonly string title, reason;
        readonly DateTime deadline;
        readonly double total;
        readonly System.Windows.Forms.Timer t = new System.Windows.Forms.Timer();
        readonly List<Btn> buttons = new List<Btn>();
        Btn hover;
        bool acted;

        public TripForm(TrayContext app, Status st)
        {
            this.app = app;
            S = Theme.Scale();
            isTest = st.TripKind == "test" || st.State == "TEST";
            string what = st.Cooler.ToUpperInvariant();
            title = isTest ? "TEST: SIMULATED " + what + " FAILURE" : st.TripKind == "cpu" ? "CPU TEMPERATURE LIMIT REACHED" : what + " FAILURE DETECTED";
            reason = st.TripReason ?? "";
            deadline = st.TripDeadline ?? DateTime.Now.AddSeconds(15);
            total = Math.Max(1, (deadline - DateTime.Now).TotalSeconds);
            FormBorderStyle = FormBorderStyle.None;
            TopMost = true;
            ShowInTaskbar = true;
            Text = "LoopMedic - " + title;
            BackColor = Theme.Bg;
            DoubleBuffered = true;
            Width = (int)(500 * S);
            Height = (int)(242 * S);
            // Near the top of the main screen, not the middle: Windows' own "You're about to be signed out" box
            // (from the backup shutdown) always sits in the middle and would cover this one.
            StartPosition = FormStartPosition.Manual;
            var wa = Screen.PrimaryScreen.WorkingArea;
            Location = new Point(wa.Left + (wa.Width - Width) / 2, wa.Top + Math.Max((int)(24 * S), wa.Height / 12));
            t.Interval = 200;
            t.Tick += delegate { Invalidate(); if (DateTime.Now > deadline.AddSeconds(3) && isTest) Close(); };
            t.Start();
            MouseMove += (s, e) => { var h = buttons.FirstOrDefault(b => b.Rect.Contains(e.Location)); if (h != hover) { hover = h; Cursor = h != null ? Cursors.Hand : Cursors.Default; Invalidate(); } };
            MouseClick += (s, e) => { var b = buttons.FirstOrDefault(x => x.Rect.Contains(e.Location)); if (b != null && !acted) b.Click(); };
            Shown += delegate { SystemSounds.Hand.Play(); };
        }
        protected override void OnHandleCreated(EventArgs e) { base.OnHandleCreated(e); Theme.RoundCorners(this); }
        protected override void OnFormClosed(FormClosedEventArgs e) { t.Stop(); base.OnFormClosed(e); }

        protected override void OnPaint(PaintEventArgs e)
        {
            var g = e.Graphics;
            g.SmoothingMode = SmoothingMode.AntiAlias;
            g.TextRenderingHint = TextRenderingHint.ClearTypeGridFit;
            buttons.Clear();
            float W = Width, pad = 22 * S;
            Color accent = isTest ? Theme.Blue : Theme.Red;
            double left = Math.Max(0, (deadline - DateTime.Now).TotalSeconds);

            using (var band = new SolidBrush(accent)) g.FillRectangle(band, 0, 0, W, 6 * S);
            using (var fT = new Font("Segoe UI Semibold", 20f * S, GraphicsUnit.Pixel))
            using (var fR = new Font("Segoe UI", 13f * S, GraphicsUnit.Pixel))
            using (var fC = new Font("Segoe UI Semibold", 15f * S, GraphicsUnit.Pixel))
            using (var fB = new Font("Segoe UI Semibold", 13f * S, GraphicsUnit.Pixel))
            using (var fS = new Font("Segoe UI", 11.5f * S, GraphicsUnit.Pixel))
            using (var bA = new SolidBrush(accent))
            using (var bText = new SolidBrush(Theme.Text))
            using (var bDim = new SolidBrush(Theme.Dim))
            {
                float y = 24 * S;
                g.DrawString("⚠  " + title, fT, bA, pad, y); y += 36 * S;
                g.DrawString(reason, fR, bText, new RectangleF(pad, y, W - pad * 2, 40 * S)); y += 40 * S;

                string cd;
                if (isTest) cd = left > 0 ? "A real trip would shut down in " + Math.Ceiling(left) + " s" : "Test finished - nothing shut down.";
                else if (acted) cd = "OK";
                else cd = left > 0 ? "Powering off in " + Math.Ceiling(left) + " s" : "Powering off…";
                g.DrawString(cd, fC, bText, pad, y); y += 30 * S;

                var bar = new RectangleF(pad, y, W - pad * 2, 6 * S);
                using (var bb = new SolidBrush(Theme.Card)) using (var bp = Theme.Rounded(bar, 3 * S)) g.FillPath(bb, bp);
                var fill = new RectangleF(bar.X, bar.Y, (float)(bar.Width * left / total), bar.Height);
                if (fill.Width > 6 * S) using (var fp = Theme.Rounded(fill, 3 * S)) g.FillPath(bA, fp);
                y += 22 * S;

                float bh = 38 * S;
                if (isTest)
                {
                    AddButton(g, new RectangleF(W - pad - 160 * S, y, 160 * S, bh), "Close", delegate { acted = true; app.CancelShutdown(true); Close(); }, fB, Theme.Blue, Theme.On(Theme.Blue));
                }
                else
                {
                    AddButton(g, new RectangleF(pad, y, 170 * S, bh), "Shut down now", delegate { acted = true; app.ShutdownNow(); }, fB, Theme.Red, Theme.On(Theme.Red));
                    AddButton(g, new RectangleF(W - pad - 190 * S, y, 190 * S, bh), "Cancel shutdown", delegate { acted = true; app.CancelShutdown(false); Close(); }, fB, Theme.Card, Theme.Text);
                }
                y += bh + 10 * S;
                g.DrawString(isTest ? "This was only a test of the pop-up." : "No choice = the PC powers off to protect the hardware.", fS, bDim, pad, y);
            }
        }

        void AddButton(Graphics g, RectangleF r, string text, Action click, Font f, Color bg, Color fg)
        {
            buttons.Add(new Btn { Rect = r, Text = text, Click = click });
            bool hot = hover != null && hover.Text == text;
            using (var bb = new SolidBrush(hot ? ControlPaint.Light(bg, 0.15f) : bg)) using (var p = Theme.Rounded(r, 7 * S)) g.FillPath(bb, p);
            using (var sf = new StringFormat { Alignment = StringAlignment.Center, LineAlignment = StringAlignment.Center })
            using (var tb = new SolidBrush(fg)) g.DrawString(text, f, tb, r, sf);
        }
    }
}
