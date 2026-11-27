// LoopMedic - powers the PC off if the AIO pump stops or the CPU overheats.
// by Jacob Stiltner, (c) 2026. See LICENSE.
//
// One program, three jobs (all run as SYSTEM from the "LoopMedic" scheduled task):
//   LoopMedic.exe                 supervisor: runs the watchdog and restarts it within seconds if it
//                                 exits or stops responding (the task restarts the supervisor itself)
//   LoopMedic.exe --worker        the watchdog: reads sensors every PollSeconds, decides, writes status.json
//   LoopMedic.exe --poweroff ...  countdown + forced power-off, started by the watchdog on a trip. It is its
//                                 own process so a stuck sensor read can't delay it.
//
// Sensors come from LibreHardwareMonitorLib (needs the PawnIO driver). USB cooler links are never
// touched - the cooler's own app (CAM, iCUE...) keeps them. The tray app (LoopMedicTray.exe, runs as the
// user) only reads status.json and drops small request files in control\ (pause / test / cancel /
// power off now / heartbeat). Those files are only ever parsed as dates - never run.
//
// Testing switches (worker): --dry-run --once --duration N --simulate-pump N --simulate-healthy S
//   --simulate-stall N --fake-sensors FILE --root DIR --no-eventlog
//   --selftest-interval-hours H --selftest-min-uptime S --crash-after S --hang-after S

using System;
using System.Collections.Generic;
using System.Diagnostics;
using System.Globalization;
using System.IO;
using System.Linq;
using System.Reflection;
using System.Runtime.InteropServices;
using System.Text;
using System.Text.RegularExpressions;
using System.Threading;
using System.Web.Script.Serialization;

[assembly: AssemblyTitle("LoopMedic")]
[assembly: AssemblyDescription("LoopMedic watchdog - powers the PC off if the AIO pump stops or the CPU overheats")]
[assembly: AssemblyCompany("Jacob Stiltner")]
[assembly: AssemblyCopyright("(c) 2026 Jacob Stiltner")]
[assembly: AssemblyVersion("2.2.0.0")]

namespace LoopMedic
{
    // ------------------------------------------------------------------ options
    class Options
    {
        public string Mode = "supervise";     // supervise | worker | poweroff
        public string Root;
        public bool DryRun, Once, NoEventLog, SelfTestRun;
        public int Duration, SimulateStall, SimulateHealthy, Delay = 30, CrashAfter, HangAfter, Parent;
        public double SimulatePump = -1, SelfTestIntervalHours = 24 * 30, SelfTestMinUptime = 600;
        public string FakeSensors, TripStarted, Reason = "", Kind = "pump";
        public List<string> PassThrough = new List<string>();   // what the supervisor hands to the worker

        public static Options Parse(string[] a)
        {
            var o = new Options();
            o.Root = Path.GetDirectoryName(Assembly.GetExecutingAssembly().Location);
            for (int i = 0; i < a.Length; i++)
            {
                string k = a[i].ToLowerInvariant();
                Func<string> next = delegate { i++; return i < a.Length ? a[i] : ""; };
                bool pass = true;
                switch (k)
                {
                    case "--worker": o.Mode = "worker"; pass = false; break;
                    case "--supervise": o.Mode = "supervise"; pass = false; break;
                    case "--poweroff": o.Mode = "poweroff"; pass = false; break;
                    case "--parent": o.Parent = ParseInt(next()); pass = false; break;
                    case "--root": o.Root = next(); o.PassThrough.Add(k); o.PassThrough.Add(o.Root); pass = false; break;
                    case "--dry-run": o.DryRun = true; break;
                    case "--once": o.Once = true; break;
                    case "--no-eventlog": o.NoEventLog = true; break;
                    case "--self-test": o.SelfTestRun = true; break;
                    case "--duration": o.Duration = ParseInt(next()); o.PassThrough.Add(k); o.PassThrough.Add(a[i]); pass = false; break;
                    case "--simulate-stall": o.SimulateStall = ParseInt(next()); o.PassThrough.Add(k); o.PassThrough.Add(a[i]); pass = false; break;
                    case "--simulate-healthy": o.SimulateHealthy = ParseInt(next()); o.PassThrough.Add(k); o.PassThrough.Add(a[i]); pass = false; break;
                    case "--simulate-pump": o.SimulatePump = ParseDouble(next(), -1); o.PassThrough.Add(k); o.PassThrough.Add(a[i]); pass = false; break;
                    case "--crash-after": o.CrashAfter = ParseInt(next()); o.PassThrough.Add(k); o.PassThrough.Add(a[i]); pass = false; break;
                    case "--hang-after": o.HangAfter = ParseInt(next()); o.PassThrough.Add(k); o.PassThrough.Add(a[i]); pass = false; break;
                    case "--fake-sensors": o.FakeSensors = next(); o.PassThrough.Add(k); o.PassThrough.Add(o.FakeSensors); pass = false; break;
                    case "--selftest-interval-hours": o.SelfTestIntervalHours = ParseDouble(next(), o.SelfTestIntervalHours); o.PassThrough.Add(k); o.PassThrough.Add(a[i]); pass = false; break;
                    case "--selftest-min-uptime": o.SelfTestMinUptime = ParseDouble(next(), o.SelfTestMinUptime); o.PassThrough.Add(k); o.PassThrough.Add(a[i]); pass = false; break;
                    case "--delay": o.Delay = ParseInt(next()); pass = false; break;
                    case "--trip-started": o.TripStarted = next(); pass = false; break;
                    case "--reason": o.Reason = next(); pass = false; break;
                    case "--kind": o.Kind = next(); pass = false; break;
                    default: pass = false; break;
                }
                if (pass) o.PassThrough.Add(k);
            }
            if (o.FakeSensors != null) o.NoEventLog = true;   // tests never write to the real Event Viewer
            return o;
        }
        public static int ParseInt(string s) { int v; return int.TryParse(s, NumberStyles.Integer, CultureInfo.InvariantCulture, out v) ? v : 0; }
        public static double ParseDouble(string s, double d) { double v; return double.TryParse(s, NumberStyles.Float, CultureInfo.InvariantCulture, out v) ? v : d; }
    }

    static class Program
    {
        public const string Version = "2.2.0";
        public static Options Opt;

        static int Main(string[] args)
        {
            Opt = Options.Parse(args);
            Paths.Init(Opt.Root);
            Events.Enabled = !Opt.NoEventLog;
            try
            {
                if (Opt.Mode == "poweroff") return PowerOff.Run(Opt);
                if (Opt.Mode == "worker" || Opt.Once) return Watchdog.Run(Opt);
                return Supervisor.Run(Opt);
            }
            catch (Exception e)
            {
                Log.Write("FATAL (" + Opt.Mode + "): " + e);
                return 1;
            }
        }
    }

    static class Paths
    {
        public static string Root, Log, Status, Control, Config, CpuLimits, SelfTest, LastPowerOff, Exe;
        public static void Init(string root)
        {
            Root = root;
            Log = Path.Combine(root, "loopmedic.log");
            Status = Path.Combine(root, "status.json");
            Control = Path.Combine(root, "control");
            Config = Path.Combine(root, "config.json");
            CpuLimits = Path.Combine(root, "cpu-limits.json");
            SelfTest = Path.Combine(root, "selftest.json");
            LastPowerOff = Path.Combine(root, "last-poweroff.json");
            Exe = Assembly.GetExecutingAssembly().Location;
        }
    }

    // ------------------------------------------------------------------ clock
    // Durations ("pump low for 10 s") use a monotonic clock: a clock change (daylight saving, internet time
    // sync, someone setting the time) must never delay a trip. Wall time is only used for display and for
    // dates written by the tray. Tests can shift the wall clock to prove that.
    static class Clock
    {
        static readonly Stopwatch sw = Stopwatch.StartNew();
        public static double OffsetSeconds;
        public static double Mono { get { return sw.Elapsed.TotalSeconds; } }
        public static DateTime Now { get { return DateTime.Now.AddSeconds(OffsetSeconds); } }
    }

    // ------------------------------------------------------------------ log + event viewer
    static class Log
    {
        static readonly object gate = new object();
        public static void Write(string text) { Write(text, ""); }
        public static void Write(string text, string prefix)
        {
            string line = Clock.Now.ToString("yyyy-MM-dd HH:mm:ss", CultureInfo.InvariantCulture) + "  " + prefix + text;
            lock (gate)
            {
                for (int attempt = 0; attempt < 5; attempt++)
                {
                    try
                    {
                        var fi = new FileInfo(Paths.Log);
                        if (fi.Exists && fi.Length > 1024 * 1024)   // keeps at most ~2 MB: loopmedic.log + loopmedic.log.old
                        {
                            try { File.Copy(Paths.Log, Paths.Log + ".old", true); File.WriteAllText(Paths.Log, ""); } catch { }
                        }
                        using (var fs = new FileStream(Paths.Log, FileMode.Append, FileAccess.Write, FileShare.ReadWrite | FileShare.Delete))
                        using (var w = new StreamWriter(fs, new UTF8Encoding(false))) w.WriteLine(line);
                        break;
                    }
                    catch { Thread.Sleep(50); }
                }
            }
            try { Console.WriteLine(line); } catch { }
        }
    }

    // Windows Event Viewer > Windows Logs > Application, source "LoopMedic".
    static class Events
    {
        public static bool Enabled = true;
        public const int Started = 1000, Degraded = 1001, Normal = 1002, Tripped = 1010, Cancelled = 1011,
                         PoweringOff = 1012, TestTrip = 1013, SelfTestOk = 1020, SelfTestFailed = 1021,
                         Restarted = 1030, PumpSlow = 1040, LastPowerOff = 1050;
        public static void Info(int id, string msg) { Write(id, msg, EventLogEntryTypeShim.Information); }
        public static void Warn(int id, string msg) { Write(id, msg, EventLogEntryTypeShim.Warning); }
        public static void Error(int id, string msg) { Write(id, msg, EventLogEntryTypeShim.Error); }
        static void Write(int id, string msg, EventLogEntryTypeShim type)
        {
            if (!Enabled) return;
            try
            {
                if (msg.Length > 30000) msg = msg.Substring(0, 30000);
                EventLog.WriteEntry("LoopMedic", msg, (EventLogEntryType)(int)type, id);
            }
            catch { }   // source missing / no rights: the log file still has it
        }
        enum EventLogEntryTypeShim { Error = 1, Warning = 2, Information = 4 }
    }

    // ------------------------------------------------------------------ small helpers
    static class Json
    {
        public static readonly JavaScriptSerializer Ser = new JavaScriptSerializer();
        public static Dictionary<string, object> ReadFile(string path, int maxBytes)
        {
            try
            {
                var fi = new FileInfo(path);
                if (!fi.Exists || fi.Length > maxBytes) return null;
                string s;
                using (var fs = new FileStream(path, FileMode.Open, FileAccess.Read, FileShare.ReadWrite | FileShare.Delete))
                using (var r = new StreamReader(fs)) s = r.ReadToEnd();
                return Ser.DeserializeObject(s) as Dictionary<string, object>;
            }
            catch { return null; }
        }
        public static double? Num(Dictionary<string, object> d, string k)
        {
            object o;
            if (d == null || !d.TryGetValue(k, out o) || o == null || o is bool) return null;
            try { double v = Convert.ToDouble(o, CultureInfo.InvariantCulture); return double.IsNaN(v) ? (double?)null : v; } catch { return null; }
        }
        public static string Str(Dictionary<string, object> d, string k)
        {
            object o;
            return d != null && d.TryGetValue(k, out o) && o != null ? Convert.ToString(o, CultureInfo.InvariantCulture) : null;
        }
        public static bool Bool(Dictionary<string, object> d, string k)
        {
            object o;
            return d != null && d.TryGetValue(k, out o) && o is bool && (bool)o;
        }
        public static DateTime? Date(Dictionary<string, object> d, string k)
        {
            string s = Str(d, k);
            DateTime t;
            if (s != null && DateTime.TryParse(s, CultureInfo.InvariantCulture, DateTimeStyles.RoundtripKind, out t)) return t.ToLocalTime();
            return null;
        }
        public static void WriteAtomic(string path, object value)
        {
            string tmp = path + ".tmp";
            File.WriteAllText(tmp, Ser.Serialize(value));
            if (File.Exists(path)) File.Replace(tmp, path, null); else File.Move(tmp, path);
        }
        public static string Iso(DateTime t) { return t.ToString("o", CultureInfo.InvariantCulture); }
    }

    // Reads a date out of a small request file in control\. Anything odd -> null. Also returns the file's
    // write time (requests are checked against when they were made).
    static class Control
    {
        public static DateTime? Read(string name, string field, out DateTime written)
        {
            written = DateTime.MinValue;
            string p = Path.Combine(Paths.Control, name);
            try
            {
                var fi = new FileInfo(p);
                if (!fi.Exists || fi.Length > 4096 || (fi.Attributes & FileAttributes.ReparsePoint) != 0) return null;
                written = fi.LastWriteTime.AddSeconds(Clock.OffsetSeconds);
                return Json.Date(Json.ReadFile(p, 4096), field);
            }
            catch { return null; }
        }
        public static DateTime? Read(string name, string field) { DateTime w; return Read(name, field, out w); }
        // control\ is where the tray app checks in and drops requests. If it's missing (deleted, or a hand-made
        // install folder), recreate it with the installer's permission: Users may modify. Without it the tray
        // can't check in, and every trip would also show the plain msg.exe fallback box.
        public static void EnsureFolder()
        {
            try
            {
                if (Directory.Exists(Paths.Control)) return;
                Directory.CreateDirectory(Paths.Control);
                var sec = Directory.GetAccessControl(Paths.Control);
                sec.AddAccessRule(new System.Security.AccessControl.FileSystemAccessRule(
                    new System.Security.Principal.SecurityIdentifier(System.Security.Principal.WellKnownSidType.BuiltinUsersSid, null),
                    System.Security.AccessControl.FileSystemRights.Modify,
                    System.Security.AccessControl.InheritanceFlags.ContainerInherit | System.Security.AccessControl.InheritanceFlags.ObjectInherit,
                    System.Security.AccessControl.PropagationFlags.None, System.Security.AccessControl.AccessControlType.Allow));
                Directory.SetAccessControl(Paths.Control, sec);
                Log.Write("control folder was missing - recreated it (Users: modify).");
            }
            catch (Exception e) { Log.Write("control folder is missing and couldn't be recreated: " + e.Message); }
        }
        public static void Write(string name, string field, DateTime value)
        {
            try
            {
                Directory.CreateDirectory(Paths.Control);
                File.WriteAllText(Path.Combine(Paths.Control, name), "{\"" + field + "\":\"" + Json.Iso(value) + "\"}");
            }
            catch { }
        }
    }

    // ------------------------------------------------------------------ power-off privilege
    static class Native
    {
        [DllImport("ntdll.dll")] public static extern int RtlAdjustPrivilege(int privilege, bool enable, bool currentThread, out bool wasEnabled);
        [DllImport("ntdll.dll")] public static extern int NtShutdownSystem(int action);   // 2 = ShutdownPowerOff
        public const int SeShutdownPrivilege = 19;

        // Can this process get the power-off right? Turns it on, then puts it back as it was.
        public static int CheckShutdownPrivilege()
        {
            if (FakeSensors.Active) return FakeSensors.PrivilegeFail ? unchecked((int)0xC0000061) : 0;
            bool was;
            int st = RtlAdjustPrivilege(SeShutdownPrivilege, true, false, out was);
            if (st == 0 && !was) { bool x; RtlAdjustPrivilege(SeShutdownPrivilege, false, false, out x); }
            return st;
        }
    }

    // ------------------------------------------------------------------ CPU limits table (cpu-limits.json)
    class CpuLimit
    {
        public string Family; public double TjMax; public bool RunsAtLimit; public double? HotSeconds;
        public double Trip { get { return RunsAtLimit ? TjMax : TjMax - 5; } }
    }
    static class CpuLimits
    {
        public static double Fallback = 95;
        static readonly List<KeyValuePair<Regex, CpuLimit>> table = new List<KeyValuePair<Regex, CpuLimit>>();
        public static void Load(string path)
        {
            var d = Json.ReadFile(path, 256 * 1024);
            if (d == null) return;
            Fallback = Json.Num(d, "fallbackTripC") ?? 95;
            object chips;
            if (!d.TryGetValue("chips", out chips) || !(chips is object[])) return;
            foreach (var c in (object[])chips)
            {
                var e = c as Dictionary<string, object>;
                if (e == null) continue;
                try
                {
                    var re = new Regex(Json.Str(e, "match") ?? "^$", RegexOptions.IgnoreCase);
                    table.Add(new KeyValuePair<Regex, CpuLimit>(re, new CpuLimit
                    {
                        Family = Json.Str(e, "family") ?? "?", TjMax = Json.Num(e, "tjmax") ?? 95,
                        RunsAtLimit = Json.Bool(e, "runsAtLimit"), HotSeconds = Json.Num(e, "hotSeconds")
                    }));
                }
                catch { }
            }
        }
        public static CpuLimit Lookup(string cpuName)
        {
            if (string.IsNullOrEmpty(cpuName)) return null;
            foreach (var kv in table) if (kv.Key.IsMatch(cpuName)) return kv.Value;
            return null;
        }
    }

    // ------------------------------------------------------------------ sensors
    class SensorInfo
    {
        public string Id, Name, Type, HardwareType, HardwareName;
        public Func<double?> Read;
    }
    interface ISensorSource
    {
        void Update();
        List<SensorInfo> Sensors { get; }
        void Close();
    }

    // Test stand-in for the hardware: a JSON file the test rewrites while LoopMedic runs.
    //   { "cpuName": "...", "sensors": [ { "id", "name", "type": "Fan|Temperature", "hw": "SuperIO|Cpu", "value": 2400 } ],
    //     "clockOffsetSeconds": 0, "privilegeFail": false, "updateDelaySeconds": 0 }
    class FakeSensors : ISensorSource
    {
        public static bool Active, PrivilegeFail;
        readonly string path;
        readonly Dictionary<string, double?> values = new Dictionary<string, double?>();
        readonly List<SensorInfo> sensors = new List<SensorInfo>();
        public FakeSensors(string path) { this.path = path; Active = true; Load(true); }
        public List<SensorInfo> Sensors { get { return sensors; } }
        public void Close() { }
        public void Update()
        {
            Load(false);
        }
        void Load(bool first)
        {
            var d = Json.ReadFile(path, 1024 * 1024);
            if (d == null) return;   // mid-write: keep the last values
            Clock.OffsetSeconds = Json.Num(d, "clockOffsetSeconds") ?? 0;
            PrivilegeFail = Json.Bool(d, "privilegeFail");
            double delay = Json.Num(d, "updateDelaySeconds") ?? 0;
            if (!first && delay > 0) Thread.Sleep(TimeSpan.FromSeconds(delay));
            string cpu = Json.Str(d, "cpuName") ?? "";
            object arr;
            var seen = new HashSet<string>();
            if (d.TryGetValue("sensors", out arr) && arr is object[])
            {
                foreach (var o in (object[])arr)
                {
                    var s = o as Dictionary<string, object>;
                    if (s == null) continue;
                    string id = Json.Str(s, "id");
                    if (id == null) continue;
                    seen.Add(id);
                    values[id] = Json.Num(s, "value");
                    if (first)
                    {
                        string hw = Json.Str(s, "hw") ?? "SuperIO";
                        string sid = id;
                        sensors.Add(new SensorInfo
                        {
                            Id = id, Name = Json.Str(s, "name") ?? id, Type = Json.Str(s, "type") ?? "Fan",
                            HardwareType = hw, HardwareName = hw == "Cpu" ? cpu : "Fake board",
                            Read = delegate { double? v; return values.TryGetValue(sid, out v) ? v : null; }
                        });
                    }
                }
            }
            foreach (var k in values.Keys.ToList()) if (!seen.Contains(k)) values[k] = null;   // removed = not responding
            // "oneShot": { "<sensor id>": value } with a new "oneShotNonce" = that value for exactly ONE read
            // (a one-reading tach glitch), then back to the normal value.
            string nonce = Json.Str(d, "oneShotNonce");
            object os;
            if (!first && nonce != null && nonce != lastNonce && d.TryGetValue("oneShot", out os) && os is Dictionary<string, object>)
            {
                lastNonce = nonce;
                foreach (var kv in (Dictionary<string, object>)os)
                    if (values.ContainsKey(kv.Key)) values[kv.Key] = Json.Num((Dictionary<string, object>)os, kv.Key);
            }
        }
        string lastNonce;
    }

    // The real hardware, through LibreHardwareMonitorLib. Kept in its own class so the library is only
    // loaded when it's actually used (tests run without it).
    class LhmSensors : ISensorSource
    {
        readonly LibreHardwareMonitor.Hardware.Computer computer;
        readonly List<SensorInfo> sensors = new List<SensorInfo>();
        public LhmSensors()
        {
            computer = new LibreHardwareMonitor.Hardware.Computer();
            computer.IsCpuEnabled = true;
            computer.IsMotherboardEnabled = true;
            computer.IsControllerEnabled = false;   // never touch USB cooler links (their app owns them)
            computer.Open();
            Update();
            foreach (var hw in computer.Hardware)
            {
                Add(hw);
                foreach (var sub in hw.SubHardware) Add(sub);
            }
        }
        void Add(LibreHardwareMonitor.Hardware.IHardware hw)
        {
            foreach (var s in hw.Sensors)
            {
                var sensor = s;
                sensors.Add(new SensorInfo
                {
                    Id = s.Identifier.ToString(), Name = s.Name, Type = s.SensorType.ToString(),
                    HardwareType = s.Hardware.HardwareType.ToString(), HardwareName = s.Hardware.Name,
                    Read = delegate
                    {
                        float? v = sensor.Value;
                        if (!v.HasValue || float.IsNaN(v.Value)) return null;
                        return Math.Round((double)v.Value, 1);
                    }
                });
            }
        }
        public List<SensorInfo> Sensors { get { return sensors; } }
        public void Update()
        {
            foreach (var hw in computer.Hardware)
            {
                hw.Update();
                foreach (var sub in hw.SubHardware) sub.Update();
            }
        }
        public void Close() { try { computer.Close(); } catch { } }
    }

    // ------------------------------------------------------------------ the watchdog
    class Trip
    {
        public string Kind, Reason;
        public DateTime StartedWall;
        public double StartedMono, DeadlineMono;
        public int Delay;
        public double? HotSince;
        public bool HotFastSent;
    }

    static class Watchdog
    {
        static Options opt;
        static Dictionary<string, object> cfg;

        static double Cfg(string name, double def)
        {
            double? v = Json.Num(cfg, name);
            return v ?? def;
        }

        static void WriteStatus(Dictionary<string, object> st)
        {
            try { Json.WriteAtomic(Paths.Status, st); } catch { }
        }
        static void Fatal(string logText, string degradedText, int code)
        {
            Log.Write("ERROR: " + logText);
            Events.Error(Events.Degraded, "LoopMedic is NOT protecting this PC: " + degradedText);
            WriteStatus(new Dictionary<string, object> {
                { "at", Json.Iso(Clock.Now) }, { "state", "DEGRADED" }, { "version", Program.Version },
                { "degraded", new[] { degradedText } }, { "fans", new object[0] } });
        }

        public static int Run(Options o)
        {
            opt = o;
            // One watchdog per install folder.
            bool created;
            var mutex = new Mutex(false, "Global\\LoopMedicWorker-" + (Paths.Root.ToLowerInvariant().GetHashCode() & 0x7fffffff), out created);
            try { if (!mutex.WaitOne(TimeSpan.FromSeconds(10))) { Log.Write("Another watchdog is already running here - exiting."); return 0; } }
            catch (AbandonedMutexException) { }
            Control.EnsureFolder();

            // ---- config (a broken config must show up in the tray, not just crash) ----
            cfg = Json.ReadFile(Paths.Config, 256 * 1024);
            if (cfg == null) { Fatal("config.json can't be read. NOT PROTECTING. Fix it and reinstall.", "config.json is broken - NOT PROTECTING. Re-run setup.", 4); return 4; }
            CpuLimits.Load(Paths.CpuLimits);

            double pumpMinRpm = Cfg("PumpMinRpm", 500);              // below this = "slow" warning
            double pumpStoppedRpm = Cfg("PumpStoppedRpm", 150);      // at or below this = stopped -> trip
            double pumpFailSeconds = Cfg("PumpFailSeconds", 10);
            double pumpFailCool = Cfg("PumpFailSecondsCool", 60);   // pump stopped but CPU cool: allow this long (firmware flash, calibration)
            double coolCpuTempC = Cfg("CoolCpuTempC", 60);
            double cpuMaxTempC = Cfg("CpuMaxTempC", 0);
            double cpuHotSeconds = Cfg("CpuHotSeconds", 30);
            double pollSeconds = Math.Max(1, Cfg("PollSeconds", 2));
            double graceSeconds = Cfg("StartupGraceSeconds", 20);
            int delaySeconds = (int)Cfg("ShutdownDelaySeconds", 30);
            int hotDelaySeconds = (int)Cfg("HotShutdownDelaySeconds", 5);
            double rearmSeconds = Cfg("RearmSeconds", 120);
            double heartbeatMinutes = Cfg("HeartbeatMinutes", 60);
            if (pumpStoppedRpm >= pumpMinRpm) pumpStoppedRpm = Math.Max(0, pumpMinRpm - 1);
            // Air cooler mode: the CPU fan takes the pump's place. Same rules, only the wording changes.
            bool air = (Json.Str(cfg, "CoolerType") ?? "").Trim().ToLowerInvariant() == "air";
            string Pw = air ? "CPU fan" : "Pump", pw = air ? "CPU fan" : "pump";

            // ---- sensors ----
            ISensorSource src;
            try
            {
                if (opt.FakeSensors != null) src = new FakeSensors(opt.FakeSensors);
                else
                {
                    if (!File.Exists(Path.Combine(Path.GetDirectoryName(Paths.Exe), "lhm", "LibreHardwareMonitorLib.dll")))
                    { Fatal("lhm\\LibreHardwareMonitorLib.dll is missing. NOT PROTECTING.", "Sensor library missing - NOT PROTECTING. Reinstall.", 3); return 3; }
                    src = OpenLhm();
                }
            }
            catch (Exception e) { Fatal("sensors can't be opened (" + e.Message + "). NOT PROTECTING.", "Sensors can't be read - NOT PROTECTING. Is the PawnIO driver installed?", 3); return 3; }

            var startupProblems = new List<string>();
            string pumpId = Json.Str(cfg, "PumpSensorId") ?? "";
            SensorInfo pump = null;
            if (pumpId.Length > 0)
            {
                pump = src.Sensors.FirstOrDefault(s => s.Id == pumpId);
                if (pump == null)
                {
                    Log.Write("WARNING: " + pw + " sensor '" + pumpId + "' not found - " + pw + " check OFF.");
                    startupProblems.Add(Pw + " sensor not found (BIOS/driver/hardware change?) - " + pw + " NOT watched. Re-run setup.");
                }
            }
            else Log.Write("No " + pw + " sensor in config.json (no speed wire on a motherboard header) - temperature-only protection.");

            string tempId = Json.Str(cfg, "CpuTempSensorId") ?? "";
            SensorInfo cpuTemp = tempId.Length > 0 ? src.Sensors.FirstOrDefault(s => s.Id == tempId) : null;
            if (cpuTemp == null) cpuTemp = src.Sensors.FirstOrDefault(s => s.Type == "Temperature" && s.HardwareType == "Cpu" && Regex.IsMatch(s.Name, "Tctl|Tdie"));
            if (cpuTemp == null) cpuTemp = src.Sensors.FirstOrDefault(s => s.Type == "Temperature" && s.HardwareType == "Cpu" && s.Name.Contains("Package"));
            string cpuNow = cpuTemp != null ? cpuTemp.HardwareName : "";
            if (cpuTemp == null)
            {
                Log.Write("WARNING: no CPU temperature sensor found - temperature check OFF.");
                startupProblems.Add("CPU temperature sensor not found - temperature NOT watched.");
            }

            // ---- temperature limit: can't be switched off any more ----
            string cfgCpu = Json.Str(cfg, "CpuName") ?? "";
            CpuLimit known = CpuLimits.Lookup(cpuNow);
            string limitSource = "config.json";
            if (cfgCpu.Length > 0 && cpuNow.Length > 0 && cpuNow != cfgCpu)
            {
                // A different CPU than setup saw: use its own safe limit.
                cpuMaxTempC = known != null ? known.Trip : CpuLimits.Fallback;
                if (known != null && known.HotSeconds.HasValue) cpuHotSeconds = known.HotSeconds.Value;
                limitSource = known != null ? known.Family + " table" : "fallback";
                Log.Write("WARNING: CPU changed ('" + cfgCpu + "' -> '" + cpuNow + "') - temperature trip now " + F(cpuMaxTempC) + " C (" + limitSource + "). Re-run setup to confirm.");
                startupProblems.Add("CPU changed to " + cpuNow + " - temperature trip set to " + F(cpuMaxTempC) + " C (" + limitSource + "). Re-run setup to confirm.");
            }
            else if (cpuMaxTempC <= 0 || cpuMaxTempC > 110)
            {
                cpuMaxTempC = known != null ? known.Trip : CpuLimits.Fallback;
                if (known != null && known.HotSeconds.HasValue) cpuHotSeconds = known.HotSeconds.Value;
                limitSource = known != null ? known.Family + " table" : "fallback";
                Log.Write("CpuMaxTempC was off or out of range - the temperature trip can't be turned off; using " + F(cpuMaxTempC) + " C (" + limitSource + ").");
            }
            bool tempCheckOn = cpuTemp != null;

            var fanSensors = src.Sensors.Where(s => s.Type == "Fan" && s.HardwareType != "Cpu").ToList();
            bool simulating = opt.SimulatePump >= 0;

            if (pump == null && !tempCheckOn && !simulating)
            {
                Fatal("no " + pw + " sensor and no CPU temperature; nothing to guard. Exiting (it will retry).", "Nothing can be watched - NOT PROTECTING.", 2);
                src.Close();
                return 2;
            }

            // ---- boot self-test ----
            src.Update();
            double? t0 = cpuTemp != null ? cpuTemp.Read() : null;
            if (cpuTemp != null && (!t0.HasValue || t0.Value < 10 || t0.Value > 100))
                startupProblems.Add("CPU temperature reads " + (t0.HasValue ? F(t0.Value) + " C" : "nothing") + " - that looks wrong, the temperature trip may not work.");
            int priv = Native.CheckShutdownPrivilege();
            if (priv != 0)
                startupProblems.Add("No permission to power off (0x" + priv.ToString("X8") + ") - LoopMedic could NOT protect this PC. Reinstall it (it must run as SYSTEM).");
            Log.Write("Start-up self-check: " + (startupProblems.Count == 0 ? "OK (sensors present, CPU " + F(t0) + " C, power-off permission granted)." : string.Join(" | ", startupProblems)));

            string pumpDesc = pump != null ? pump.Name + " [" + pump.Id + "]" : "none";
            string tempDesc = cpuTemp != null ? cpuTemp.Name + " [" + cpuTemp.Id + "]" : "none";
            string mode = opt.DryRun ? "DRY RUN" : "ARMED";
            string startLine = "LoopMedic " + Program.Version + " started (" + mode + (air ? ", air cooler" : "") + "). " + Pw + ": " + pumpDesc + ", trips if stopped (<= " + F(pumpStoppedRpm) +
                " rpm) for " + F(pumpFailSeconds) + "s (" + F(pumpFailCool) + "s while CPU < " + F(coolCpuTempC) + " C), slow warning below " + F(pumpMinRpm) +
                " rpm. CPU: " + tempDesc + ", max " + F(cpuMaxTempC) + " C for " + F(cpuHotSeconds) + "s. Fans: " + fanSensors.Count + ". Power-off after " + delaySeconds +
                "s (" + hotDelaySeconds + "s if the CPU is already hot).";
            Log.Write(startLine);
            Events.Info(Events.Started, startLine);

            if (opt.Once)
            {
                src.Update();
                double? r = pump != null ? pump.Read() : null;
                if (simulating) r = opt.SimulatePump;
                Log.Write(Pw + " rpm: " + F(r) + "   CPU temp: " + F(cpuTemp != null ? cpuTemp.Read() : null) + " C");
                foreach (var f in fanSensors) Log.Write("  " + f.Name.PadRight(20) + " " + F(f.Read()) + " rpm   [" + f.Id + "]");
                src.Close();
                return 0;
            }

            // ---- why did the PC turn off last time? ----
            Dictionary<string, object> lastOff = Json.ReadFile(Paths.LastPowerOff, 16 * 1024);
            if (lastOff != null && !Json.Bool(lastOff, "reported"))
            {
                string msg = "Last power-off by LoopMedic: " + Json.Str(lastOff, "at") + " - " + Json.Str(lastOff, "reason");
                Log.Write(msg);
                Events.Warn(Events.LastPowerOff, msg);
                lastOff["reported"] = true;
                try { Json.WriteAtomic(Paths.LastPowerOff, lastOff); } catch { }
            }

            // ---- state ----
            double started = Clock.Mono;
            double? pumpBadSince = null, hotSince = null, pumpNullSince = null, tempNullSince = null, pumpSlowSince = null;
            bool pumpEverOk = false, pumpSlowReported = false;
            double lastHeartbeat = Clock.Mono, cooldownUntil = 0, lastCheck = Clock.Mono;
            DateTime lastCheckWall = Clock.Now;
            double? maxTemp = null, minRpm = null, maxRpm = null;
            double? accepted = null, pending = null;          // pump reading the watchdog believes / a jump waiting for confirmation
            int jumpHour = -1, jumpCount = 0, stallHour = -1, stallCount = 0, glitches = 0;
            DateTime day = Clock.Now.Date;
            double? dayMaxTemp = null, dayMinPump = null;
            Trip trip = null;
            DateTime lastTestHandled = Clock.Now, lastCancelHandled = Clock.Now;
            bool wasPaused = false, stallSimDone = false;
            double pauseEndMono = 0; DateTime pauseFileWritten = DateTime.MinValue; DateTime? pauseUntilWall = null;
            string lastDegradedText = "";
            // monthly self-test
            Process selfTestProc = null; double selfTestStarted = 0;
            Dictionary<string, object> selfTest = Json.ReadFile(Paths.SelfTest, 16 * 1024);
            string selfTestProblem = null;
            if (selfTest != null && Json.Str(selfTest, "ok") == "False") selfTestProblem = "Monthly self-test FAILED: " + Json.Str(selfTest, "detail");
            double nextSelfTestMono = started + SelfTestDue(selfTest, o);

            while (true)
            {
                Thread.Sleep(TimeSpan.FromSeconds(pollSeconds));
                double readStart = Clock.Mono;
                if (o.SimulateStall > 0 && !stallSimDone && readStart - started >= 4) { stallSimDone = true; Thread.Sleep(TimeSpan.FromSeconds(o.SimulateStall)); }
                if (o.CrashAfter > 0 && readStart - started >= o.CrashAfter) { Log.Write("TEST: simulated crash."); Environment.Exit(1); }
                if (o.HangAfter > 0 && readStart - started >= o.HangAfter) { Log.Write("TEST: simulated hang."); Thread.Sleep(Timeout.Infinite); }
                if (o.Parent > 0 && !ParentAlive(o.Parent)) { Log.Write("Supervisor is gone - exiting (it will be restarted)."); src.Close(); return 0; }
                try { src.Update(); }
                catch (Exception e) { Log.Write("Sensor update failed: " + e.Message); continue; }
                double now = Clock.Mono;
                DateTime wall = Clock.Now;

                // Stalls: a check should land every PollSeconds; log when 10 s or more go by without one.
                double gap = now - lastCheck;
                double wallGap = (wall - lastCheckWall).TotalSeconds;
                if (gap >= 10)
                {
                    if (stallHour != wall.Hour) { stallHour = wall.Hour; stallCount = 0; }
                    stallCount++;
                    // A quick sensor read inside a long gap means the whole PC paused (sleep), not the read.
                    string why = gap >= 300 || wallGap >= 300 || now - readStart < gap / 2 ? "PC probably asleep" :"the sensor read itself took " + (now - readStart).ToString("0.0", CultureInfo.InvariantCulture) + " s";
                    if (stallCount <= 20) Log.Write("Stalled: no check for " + gap.ToString("0", CultureInfo.InvariantCulture) + " s (" + why + ").");
                    else if (stallCount == 21) Log.Write("Stalled: more than 20 this hour - not logging the rest until the next hour.");
                }
                lastCheck = now; lastCheckWall = wall;
                double elapsed = now - started;

                // ---- pump reading + the jump rule ----
                double? raw = pump != null ? pump.Read() : null;
                if (simulating) raw = elapsed < o.SimulateHealthy ? 2400 : o.SimulatePump;
                double? temp = cpuTemp != null ? cpuTemp.Read() : null;
                double? rpm;
                if (!raw.HasValue) { rpm = null; pending = null; }
                else if (!accepted.HasValue) { rpm = raw; }
                else if (raw.Value > accepted.Value * 1.4 && raw.Value - accepted.Value > 100)
                {
                    // Sudden rise: a pump can't nearly double its speed in one check, so wait for the next
                    // reading to confirm. Rises are never dangerous, so holding one costs nothing.
                    if (pending.HasValue && Math.Abs(raw.Value - pending.Value) <= pending.Value * 0.25)
                    {
                        if (!simulating) JumpLog(Pw, ref jumpHour, ref jumpCount, wall, F(accepted) + " -> " + F(raw) + " rpm (confirmed by the next reading) (CPU " + F(temp) + " C)");
                        rpm = raw; pending = null;
                    }
                    else { pending = raw; rpm = accepted; }
                }
                else
                {
                    if (pending.HasValue)
                    {
                        // One wild reading that came straight back: tach noise. Counted in the hourly line, not logged one by one.
                        if (!simulating) glitches++;
                        pending = null;
                    }
                    else if (!simulating && accepted.Value > 0 && Math.Abs(raw.Value - accepted.Value) / accepted.Value > 0.15)
                        JumpLog(Pw, ref jumpHour, ref jumpCount, wall, F(accepted) + " -> " + F(raw) + " rpm (CPU " + F(temp) + " C)");
                    rpm = raw;   // drops always count, straight away
                }
                if (rpm.HasValue) accepted = rpm;

                if (temp.HasValue && (!maxTemp.HasValue || temp > maxTemp)) maxTemp = temp;
                if (rpm.HasValue && (!minRpm.HasValue || rpm < minRpm)) minRpm = rpm;
                if (rpm.HasValue && (!maxRpm.HasValue || rpm > maxRpm)) maxRpm = rpm;
                if (wall.Date != day) { day = wall.Date; dayMaxTemp = null; dayMinPump = null; }
                if (temp.HasValue && (!dayMaxTemp.HasValue || temp > dayMaxTemp)) dayMaxTemp = temp;
                if (rpm.HasValue && (!dayMinPump.HasValue || rpm < dayMinPump)) dayMinPump = rpm;
                if (rpm.HasValue && rpm.Value > pumpStoppedRpm) pumpEverOk = true;
                if (!rpm.HasValue && (pump != null || simulating)) { if (!pumpNullSince.HasValue) pumpNullSince = now; } else pumpNullSince = null;
                if (!temp.HasValue && cpuTemp != null) { if (!tempNullSince.HasValue) tempNullSince = now; } else tempNullSince = null;

                bool endNow = o.Duration > 0 && elapsed >= o.Duration;
                if (endNow || (now - lastHeartbeat) / 60 >= heartbeatMinutes)
                {
                    Log.Write("OK  " + pw + " " + F(rpm) + " rpm, CPU " + F(temp) + " C  (since last line: CPU max " + F(maxTemp) + " C, " + pw + " min " + F(minRpm) + " / max " + F(maxRpm) + " rpm" +
                        (glitches > 0 ? ", " + glitches + " one-reading " + pw + " glitch" + (glitches == 1 ? "" : "es") + " ignored" : "") + ")");
                    lastHeartbeat = now; maxTemp = null; minRpm = null; maxRpm = null; glitches = 0;
                }
                if (endNow) { Log.Write("Test duration reached - exiting."); src.Close(); return 0; }

                // ---- requests from the tray app ----
                // Pause: read once per new request and turned into a monotonic deadline, at most 2 h from when
                // the request was WRITTEN, never from a file dated in the future. Clock changes can't stretch it.
                DateTime pauseW;
                DateTime? pv = Control.Read("pause.json", "until", out pauseW);
                if (pv.HasValue && pauseW != pauseFileWritten)
                {
                    pauseFileWritten = pauseW;
                    if (pauseW <= wall.AddMinutes(5))
                    {
                        DateTime limit = pauseW.AddHours(2);
                        DateTime until = pv.Value < limit ? pv.Value : limit;
                        double remaining = (until - wall).TotalSeconds;
                        pauseEndMono = remaining > 0 ? now + remaining : 0;
                        pauseUntilWall = remaining > 0 ? (DateTime?)until : null;
                    }
                }
                bool paused = now < pauseEndMono;
                if (paused && !wasPaused) Log.Write("PAUSED by the tray app until " + pauseUntilWall.Value.ToString("HH:mm", CultureInfo.InvariantCulture) + ".");
                if (!paused && wasPaused) Log.Write("Pause ended - ARMED again.");
                wasPaused = paused;

                DateTime? testAt = Control.Read("test.json", "requested");
                if (testAt.HasValue && testAt.Value > lastTestHandled && (wall - testAt.Value).TotalSeconds < 60 && trip == null)
                {
                    lastTestHandled = testAt.Value;
                    trip = Protect("test", "TEST - simulated " + pw + " failure (nothing will power off)", delaySeconds, now, wall);
                }

                DateTime? cancelAt = Control.Read("cancel.json", "at");
                if (trip != null && cancelAt.HasValue && cancelAt.Value > lastCancelHandled && cancelAt.Value >= trip.StartedWall.AddSeconds(-1))
                {
                    lastCancelHandled = cancelAt.Value;
                    if (trip.Kind != "test" && !o.DryRun) Run("shutdown.exe", "/a");   // also stop the backup shutdown
                    Log.Write("Power-off CANCELLED from the tray pop-up (" + trip.Reason + "). Re-arming in " + F(rearmSeconds) + "s.");
                    if (trip.Kind != "test") Events.Info(Events.Cancelled, "Power-off cancelled by the user (" + trip.Reason + ").");
                    trip = null;
                    cooldownUntil = now + rearmSeconds;
                }
                if (trip != null && now > trip.DeadlineMono + 10)
                {
                    if (trip.Kind != "test" && !o.DryRun) Log.Write("Still running after the deadline (cancelled with shutdown /a?). Re-arming.");
                    trip = null;
                }

                // ---- hotter, faster: a countdown that's already running gets cut short if the CPU is hot ----
                if (trip != null && trip.Kind != "test" && !trip.HotFastSent && temp.HasValue && tempCheckOn)
                {
                    bool hotNow = trip.Kind == "pump" ? temp.Value >= cpuMaxTempC : temp.Value >= cpuMaxTempC + 5;
                    if (hotNow)
                    {
                        if (!trip.HotSince.HasValue) trip.HotSince = now;
                        else if (now - trip.HotSince.Value >= hotDelaySeconds && trip.DeadlineMono - now > hotDelaySeconds)
                        {
                            trip.HotFastSent = true;
                            Log.Write("CPU at " + F(temp) + " C during the countdown - powering off now (hotter, faster).");
                            Control.Write("poweroff.json", "at", wall);
                        }
                    }
                    else trip.HotSince = null;
                }

                // ---- health: things that make the protection weaker ----
                var degraded = new List<string>(startupProblems);
                if (selfTestProblem != null) degraded.Add(selfTestProblem);
                if (pumpNullSince.HasValue && now - pumpNullSince.Value >= 30) degraded.Add(Pw + " sensor not responding - " + pw + " NOT watched right now.");
                if (tempNullSince.HasValue && now - tempNullSince.Value >= 30) degraded.Add("CPU temperature not responding.");
                bool inGrace = elapsed < graceSeconds;
                bool pumpNeverSpun = (pump != null || simulating) && !pumpEverOk && !inGrace && rpm.HasValue;
                if (pumpNeverSpun) degraded.Add(Pw + " has read " + F(rpm) + " rpm since boot - cable moved? " + Pw + " trip held; CPU temperature still watched.");
                // Slow but spinning: warn, don't power off (eco modes, or a pump that reports a different pulse
                // count, can read low). The CPU temperature trip still covers a pump that's really failing.
                if (pumpEverOk && rpm.HasValue && rpm.Value > pumpStoppedRpm && rpm.Value < pumpMinRpm)
                {
                    if (!pumpSlowSince.HasValue) pumpSlowSince = now;
                    if (now - pumpSlowSince.Value >= pumpFailSeconds)
                    {
                        degraded.Add(Pw + " slower than usual: " + F(rpm) + " rpm (warning below " + F(pumpMinRpm) + "). Not powering off for this alone; the CPU temperature trip is still armed.");
                        if (!pumpSlowReported) { pumpSlowReported = true; Events.Warn(Events.PumpSlow, Pw + " slower than usual: " + F(rpm) + " rpm."); }
                    }
                }
                else { pumpSlowSince = null; pumpSlowReported = false; }
                string degradedText = string.Join(" | ", degraded);
                if (degradedText != lastDegradedText)
                {
                    if (degradedText.Length > 0) { Log.Write("DEGRADED: " + degradedText); Events.Warn(Events.Degraded, "Protection reduced: " + degradedText); }
                    else if (lastDegradedText.Length > 0) { Log.Write("Protection back to normal."); Events.Info(Events.Normal, "Protection back to normal."); }
                    lastDegradedText = degradedText;
                }

                // ---- checks ----
                if (!inGrace && !paused && trip == null && now >= cooldownUntil)
                {
                    if ((pump != null || simulating) && rpm.HasValue && pumpEverOk)
                    {
                        if (rpm.Value <= pumpStoppedRpm)
                        {
                            // CPU cool -> allow longer (a firmware flash or fan calibration stops the pump briefly).
                            // Once the CPU warms past CoolCpuTempC the normal short window applies.
                            bool cool = !simulating && temp.HasValue && temp.Value < coolCpuTempC;
                            double window = cool ? pumpFailCool : pumpFailSeconds;
                            if (!pumpBadSince.HasValue)
                            {
                                pumpBadSince = now;
                                Log.Write(Pw + " stopped: " + F(rpm) + " rpm - watching" + (cool ? " (CPU cool at " + F(temp) + " C: allowing " + F(pumpFailCool) + "s, e.g. for a firmware update)" : "") + ".");
                            }
                            else if (now - pumpBadSince.Value >= window)
                            {
                                bool hot = tempCheckOn && temp.HasValue && temp.Value >= cpuMaxTempC;
                                trip = Protect("pump", pw + " at " + F(rpm) + " rpm for " + (int)(now - pumpBadSince.Value) + "s (CPU " + F(temp) + " C)", hot ? hotDelaySeconds : delaySeconds, now, wall);
                                pumpBadSince = null; hotSince = null;
                                cooldownUntil = now + rearmSeconds;
                            }
                        }
                        else if (pumpBadSince.HasValue)
                        {
                            Log.Write(Pw + " recovered: " + F(rpm) + " rpm.");
                            pumpBadSince = null;
                        }
                    }
                    else pumpBadSince = null;

                    if (tempCheckOn && trip == null && temp.HasValue)
                    {
                        if (temp.Value >= cpuMaxTempC)
                        {
                            if (!hotSince.HasValue) { hotSince = now; Log.Write("CPU hot: " + F(temp) + " C - watching."); }
                            else if (now - hotSince.Value >= cpuHotSeconds)
                            {
                                trip = Protect("cpu", "CPU at " + F(temp) + " C for " + (int)(now - hotSince.Value) + "s (" + pw + " " + F(rpm) + " rpm)", delaySeconds, now, wall);
                                pumpBadSince = null; hotSince = null;
                                cooldownUntil = now + rearmSeconds;
                            }
                        }
                        else if (hotSince.HasValue)
                        {
                            Log.Write("CPU cooled: " + F(temp) + " C.");
                            hotSince = null;
                        }
                    }
                }

                // ---- monthly self-test: the whole power-off path in dry-run mode (nothing turns off) ----
                if (selfTestProc == null && now >= nextSelfTestMono && trip == null && !paused && now - started >= o.SelfTestMinUptime)
                {
                    selfTestProc = StartHelper("selftest", "monthly self-test", 2, wall, true, true);
                    selfTestStarted = now;
                    if (selfTestProc == null) { FinishSelfTest(false, "the power-off helper wouldn't start", out selfTestProblem); nextSelfTestMono = now + 24 * 3600; }
                }
                else if (selfTestProc != null)
                {
                    bool exited = false;
                    try { exited = selfTestProc.HasExited; } catch { exited = true; }
                    if (exited || now - selfTestStarted > 30)
                    {
                        int code = -1;
                        try { if (exited) code = selfTestProc.ExitCode; else selfTestProc.Kill(); } catch { }
                        bool ok = code == 0;
                        FinishSelfTest(ok, ok ? "power-off path works (permission granted)" : (code == 5 ? "no permission to power off" : (exited ? "helper exit code " + code : "helper didn't finish in 30 s")), out selfTestProblem);
                        nextSelfTestMono = now + (ok ? o.SelfTestIntervalHours * 3600 : Math.Min(24 * 3600, o.SelfTestIntervalHours * 3600));
                        selfTestProc = null;
                    }
                }

                // ---- status for the tray app ----
                string state = "ARMED";
                if (inGrace) state = "STARTING";
                if (degraded.Count > 0) state = "DEGRADED";
                if (pumpBadSince.HasValue || hotSince.HasValue) state = "WATCHING";
                if (paused) state = "PAUSED";
                if (trip != null) state = trip.Kind == "test" ? "TEST" : "TRIPPED";
                var fans = new List<object>();
                foreach (var f in fanSensors)
                {
                    bool isPump = pump != null && f.Id == pump.Id;
                    fans.Add(new Dictionary<string, object> { { "name", f.Name }, { "rpm", isPump ? rpm : f.Read() }, { "pump", isPump } });
                }
                Dictionary<string, object> lastOffStatus = null;
                if (lastOff != null)
                {
                    DateTime? lat = Json.Date(lastOff, "at");
                    if (lat.HasValue && (wall - lat.Value).TotalHours < 24)   // tray shows it for a day
                        lastOffStatus = new Dictionary<string, object> { { "at", Json.Iso(lat.Value) }, { "reason", Json.Str(lastOff, "reason") } };
                }
                WriteStatus(new Dictionary<string, object> {
                    { "at", Json.Iso(wall) }, { "version", Program.Version }, { "state", state }, { "dryRun", o.DryRun }, { "coolerType", air ? "air" : "aio" },
                    { "degraded", degraded.ToArray() }, { "pumpRpm", rpm }, { "cpuTemp", temp }, { "cpuName", cpuNow },
                    { "pumpMinRpm", pumpMinRpm }, { "pumpStoppedRpm", pumpStoppedRpm }, { "cpuMaxTemp", tempCheckOn ? cpuMaxTempC : 0 },
                    { "dayMaxTemp", dayMaxTemp }, { "dayMinPump", dayMinPump },
                    { "pausedUntil", paused && pauseUntilWall.HasValue ? Json.Iso(pauseUntilWall.Value) : null },
                    { "tripKind", trip != null ? trip.Kind : null }, { "tripReason", trip != null ? trip.Reason : null },
                    { "tripDeadline", trip != null ? Json.Iso(wall.AddSeconds(trip.DeadlineMono - now)) : null },
                    { "lastPowerOff", lastOffStatus },
                    { "selfTest", selfTest != null ? new Dictionary<string, object> { { "at", Json.Str(selfTest, "at") }, { "ok", Json.Str(selfTest, "ok") == "True" } } : null },
                    { "fans", fans.ToArray() } });
                if (File.Exists(Paths.SelfTest) && (selfTest == null || selfTestProc == null)) selfTest = Json.ReadFile(Paths.SelfTest, 16 * 1024) ?? selfTest;
            }
        }

        static LhmSensors OpenLhm() { return new LhmSensors(); }

        static double SelfTestDue(Dictionary<string, object> st, Options o)
        {
            if (st == null) return o.SelfTestMinUptime;              // never run: first one soon after install
            DateTime? at = Json.Date(st, "at");
            if (!at.HasValue) return o.SelfTestMinUptime;
            bool ok = Json.Str(st, "ok") == "True";
            double interval = ok ? o.SelfTestIntervalHours * 3600 : Math.Min(24 * 3600, o.SelfTestIntervalHours * 3600);
            double left = interval - (Clock.Now - at.Value).TotalSeconds;
            return Math.Max(o.SelfTestMinUptime, left);
        }

        static void FinishSelfTest(bool ok, string detail, out string problem)
        {
            try
            {
                Json.WriteAtomic(Paths.SelfTest, new Dictionary<string, object> { { "at", Json.Iso(Clock.Now) }, { "ok", ok ? "True" : "False" }, { "detail", detail } });
            }
            catch { }
            if (ok) { Log.Write("Monthly self-test OK: " + detail + "."); Events.Info(Events.SelfTestOk, "Monthly self-test OK: " + detail + "."); problem = null; }
            else { Log.Write("Monthly self-test FAILED: " + detail + "."); Events.Error(Events.SelfTestFailed, "Monthly self-test FAILED: " + detail + ". LoopMedic may not be able to power off this PC."); problem = "Monthly self-test FAILED: " + detail + ". LoopMedic may not be able to power off. Reinstall."; }
        }

        static void JumpLog(string who, ref int hour, ref int count, DateTime wall, string text)
        {
            if (hour != wall.Hour) { hour = wall.Hour; count = 0; }
            count++;
            if (count <= 20) Log.Write(who + " jump: " + text);
            else if (count == 21) Log.Write(who + " jump: more than 20 this hour - not logging the rest until the next hour.");
        }

        static bool ParentAlive(int pid)
        {
            try { var p = Process.GetProcessById(pid); return !p.HasExited; } catch { return false; }
        }

        static Process StartHelper(string kind, string reason, int delay, DateTime startedWall, bool dry, bool selfTest)
        {
            var args = new List<string> { "--poweroff", "--kind", kind, "--delay", delay.ToString(CultureInfo.InvariantCulture),
                                          "--trip-started", Json.Iso(startedWall), "--reason", reason, "--root", Paths.Root };
            if (dry) args.Add("--dry-run");
            if (selfTest) args.Add("--self-test");
            if (opt.FakeSensors != null) { args.Add("--fake-sensors"); args.Add(opt.FakeSensors); }
            if (opt.NoEventLog) args.Add("--no-eventlog");
            try
            {
                var psi = new ProcessStartInfo(Paths.Exe, string.Join(" ", args.Select(Quote))) { UseShellExecute = false, CreateNoWindow = true };
                return Process.Start(psi);
            }
            catch (Exception e) { Log.Write("Couldn't start the power-off helper: " + e.Message); return null; }
        }

        static Trip Protect(string kind, string reason, int delay, double now, DateTime wall)
        {
            var t = new Trip { Kind = kind, Reason = reason, StartedWall = wall, StartedMono = now, DeadlineMono = now + delay, Delay = delay };
            bool dry = opt.DryRun || kind == "test";
            if (dry) Log.Write((kind == "test" ? "TEST" : "DRY RUN") + " - trip: " + reason + " - would power off in " + delay + "s (power-off helper runs in dry-run mode)");
            else Log.Write("TRIPPED: " + reason + " - powering off in " + delay + "s unless cancelled.");
            if (kind == "test") Events.Info(Events.TestTrip, "Test trip from the tray (nothing powers off).");
            else Events.Warn(Events.Tripped, (dry ? "DRY RUN - " : "") + "LoopMedic tripped: " + reason + ". Powering off in " + delay + " s unless cancelled.");

            // The countdown + forced power-off run in their own process (keeps going even if this loop stalls).
            StartHelper(kind, reason, delay, wall, dry, false);

            if (!dry)
            {
                // Backup in case the helper dies: a normal forced shutdown 60 s after the deadline.
                string msg = "LoopMedic: " + reason + ". The PC will power off to protect the hardware.";
                Run("shutdown.exe", "/s /f /t " + (delay + 60) + " /c " + Quote(msg));
                // The tray app shows its own pop-up; fall back to msg.exe (Pro/Enterprise only) when it isn't running.
                DateTime? tray = Control.Read("tray.json", "at");
                if (!(tray.HasValue && (wall - tray.Value).TotalSeconds < 15))
                    Run("msg.exe", "* /TIME:" + delay + " " + Quote(msg + " Powering off in " + delay + " seconds. To cancel the backup: shutdown /a"));
            }
            return t;
        }

        public static void Run(string exe, string args)
        {
            try { var p = Process.Start(new ProcessStartInfo(exe, args) { UseShellExecute = false, CreateNoWindow = true }); if (p != null) p.WaitForExit(5000); }
            catch { }
        }

        public static string Quote(string s)
        {
            if (s.Length > 0 && s.IndexOfAny(new[] { ' ', '"', '\t' }) < 0) return s;
            return "\"" + s.Replace("\\\"", "\\\\\"").Replace("\"", "'") + "\"";
        }

        public static string F(double? v)
        {
            if (!v.HasValue) return "-";
            return Math.Round(v.Value, 1).ToString("0.#", CultureInfo.InvariantCulture);
        }
    }

    // ------------------------------------------------------------------ power-off helper
    static class PowerOff
    {
        public static int Run(Options o)
        {
            if (o.FakeSensors != null) new FakeSensors(o.FakeSensors);   // tests: fake privilege + clock
            string p = "[power-off] ";
            DateTime started = Clock.Now;
            DateTime t;
            if (!string.IsNullOrEmpty(o.TripStarted) && DateTime.TryParse(o.TripStarted, CultureInfo.InvariantCulture, DateTimeStyles.RoundtripKind, out t)) started = t.ToLocalTime();
            var sw = Stopwatch.StartNew();
            string tag = o.SelfTestRun ? " (SELF-TEST, nothing turns off)" : (o.DryRun ? " (DRY RUN)" : "");
            Log.Write("armed: powering off in " + o.Delay + " s" + tag, p);

            bool nowPressed = false;
            while (sw.Elapsed.TotalSeconds < o.Delay && !o.SelfTestRun)
            {
                DateTime? c = Control.Read("cancel.json", "at");
                if (c.HasValue && c.Value >= started.AddSeconds(-1)) { Log.Write("cancelled from the tray - not powering off.", p); return 0; }
                DateTime? n = Control.Read("poweroff.json", "at");
                if (n.HasValue && n.Value >= started.AddSeconds(-1)) { Log.Write("\"Power off now\" requested.", p); nowPressed = true; break; }
                Thread.Sleep(250);
            }
            if (o.SelfTestRun) Thread.Sleep(TimeSpan.FromSeconds(Math.Min(o.Delay, 5)));

            bool was = false;
            int priv;
            if (FakeSensors.Active) priv = FakeSensors.PrivilegeFail ? unchecked((int)0xC0000061) : 0;
            else priv = Native.RtlAdjustPrivilege(Native.SeShutdownPrivilege, true, false, out was);
            if (o.DryRun || o.SelfTestRun)
            {
                Log.Write((o.SelfTestRun ? "SELF-TEST" : "DRY RUN") + " - WOULD POWER OFF NOW" + (nowPressed ? " (power off now)" : "") +
                          " (forced, skips Windows Update). Shutdown privilege status 0x" + priv.ToString("X8") + ".", p);
                return priv == 0 ? 0 : 5;
            }

            // Record why, for the "why did my PC turn off?" notice after the restart and for Event Viewer.
            try
            {
                string json = Json.Ser.Serialize(new Dictionary<string, object> { { "at", Json.Iso(Clock.Now) }, { "reason", o.Reason }, { "kind", o.Kind }, { "reported", false } });
                using (var fs = new FileStream(Paths.LastPowerOff, FileMode.Create, FileAccess.Write, FileShare.None, 4096, FileOptions.WriteThrough))
                using (var w = new StreamWriter(fs)) { w.Write(json); w.Flush(); fs.Flush(true); }
            }
            catch { }
            Events.Error(Events.PoweringOff, "LoopMedic is powering off this PC now to protect the hardware: " + o.Reason + ".");
            Log.Write("POWERING OFF NOW (forced, skips Windows Update): " + o.Reason, p);
            // Give the log file a moment to reach the disk. Event Viewer keeps its newest entries in the event log
            // service's own memory, so the trip / power-off entries written in the last ~30-60 s are usually lost
            // in the cut - flushing the drive doesn't help (tested 25 Nov 2026). The record that survives is
            // last-poweroff.json -> event 1050 + the tray notice after the restart.
            Thread.Sleep(1000);
            int r = Native.NtShutdownSystem(2);
            // Only reached if the forced power-off failed.
            Log.Write("NtShutdownSystem failed (0x" + r.ToString("X8") + ", privilege 0x" + priv.ToString("X8") + ") - falling back to shutdown /p /f.", p);
            Watchdog.Run("shutdown.exe", "/p /f");
            return 1;
        }
    }

    // ------------------------------------------------------------------ supervisor
    // Keeps the watchdog running: restarts it 2 s after it exits, and kills + restarts it if status.json
    // stops changing for 30 s (hung). The scheduled task restarts the supervisor itself (second line).
    static class Supervisor
    {
        public static int Run(Options o)
        {
            bool created;
            var mutex = new Mutex(false, "Global\\LoopMedicSupervisor-" + (Paths.Root.ToLowerInvariant().GetHashCode() & 0x7fffffff), out created);
            try { if (!mutex.WaitOne(TimeSpan.FromSeconds(5))) return 0; } catch (AbandonedMutexException) { }

            var recent = new Queue<double>();
            int myPid = Process.GetCurrentProcess().Id;
            while (true)
            {
                var args = new List<string>(o.PassThrough) { "--worker", "--parent", myPid.ToString(CultureInfo.InvariantCulture) };
                Process w;
                try { w = Process.Start(new ProcessStartInfo(Paths.Exe, string.Join(" ", args.Select(Watchdog.Quote))) { UseShellExecute = false, CreateNoWindow = true }); }
                catch (Exception e) { Log.Write("Supervisor: couldn't start the watchdog (" + e.Message + ") - retrying in 10 s."); Thread.Sleep(10000); continue; }
                double startedAt = Clock.Mono, lastChange = Clock.Mono, lastTick = Clock.Mono;
                DateTime lastStamp = StatusStamp();
                bool hung = false;
                while (!w.WaitForExit(1000))
                {
                    // This loop ticks every second. A much longer gap means the PC was asleep (the clock
                    // keeps counting through sleep): give the watchdog a fresh 30 s instead of calling it hung.
                    double tick = Clock.Mono;
                    if (tick - lastTick > 10)
                    {
                        Log.Write("Supervisor: PC was asleep for about " + (tick - lastTick).ToString("0", CultureInfo.InvariantCulture) + " s - giving the watchdog a fresh 30 s.");
                        lastChange = tick;
                    }
                    lastTick = tick;
                    DateTime s = StatusStamp();
                    if (s != lastStamp) { lastStamp = s; lastChange = Clock.Mono; }
                    if (Clock.Mono - startedAt > 60 && Clock.Mono - lastChange > 30)
                    {
                        hung = true;
                        Log.Write("Supervisor: the watchdog stopped responding for 30 s - restarting it.");
                        Events.Warn(Events.Restarted, "The LoopMedic watchdog stopped responding for 30 s and was restarted.");
                        try { w.Kill(); w.WaitForExit(5000); } catch { }
                        break;
                    }
                }
                int code = -1;
                try { code = w.ExitCode; } catch { }
                if (!hung && code == 0 && o.Duration > 0) return 0;   // test run finished
                double wait = (code >= 2 && code <= 4) ? 30 : 2;      // config/sensors broken: don't spin
                recent.Enqueue(Clock.Mono);
                while (recent.Count > 0 && Clock.Mono - recent.Peek() > 120) recent.Dequeue();
                if (recent.Count > 5) wait = 30;
                if (!hung)
                {
                    Log.Write("Supervisor: the watchdog exited (code " + code + ") - restarting in " + wait + " s.");
                    if (code != 2 && code != 3 && code != 4) Events.Warn(Events.Restarted, "The LoopMedic watchdog exited (code " + code + ") and was restarted.");
                }
                Thread.Sleep(TimeSpan.FromSeconds(hung ? 1 : wait));
            }
        }

        static DateTime StatusStamp()
        {
            try { return File.GetLastWriteTimeUtc(Paths.Status); } catch { return DateTime.MinValue; }
        }
    }
}
