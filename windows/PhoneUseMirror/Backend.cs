using System.Diagnostics;
using System.Net.Sockets;

namespace PhoneUseMirror;

/// <summary>
/// phoneuse itself stays what it is: the checkout's Python (bin\phoneuse, .venv),
/// started and stopped on its own. The window only starts it when nothing answers
/// on the mirror's port: `python -m phoneuse.windows.background`, which is
/// `phoneuse serve` with no console, logging to phoneuse-up.log. Quit stops that
/// background copy (by the pid it writes); a `phoneuse up` running in a terminal is
/// left alone.
/// </summary>
public static class Backend
{
    /// <summary>The phoneuse this window runs: the installed or portable app's core\ folder
    /// next to the exe (windows/scripts/package.ps1), else the checkout it was built in
    /// (windows\dist\ is two levels down).</summary>
    public static string? Root { get; } = FindRoot();

    /// <summary>The installed or portable app (a core\ folder next to the exe), not a checkout.</summary>
    public static bool Bundled => Root != null &&
        string.Equals(Root, Path.Combine(AppContext.BaseDirectory, "core"), StringComparison.OrdinalIgnoreCase);

    /// <summary>The portable zip: a "portable" file next to the exe keeps phoneuse's settings
    /// and this window's in data\ beside it instead of in the user's AppData.</summary>
    public static bool Portable => File.Exists(Path.Combine(AppContext.BaseDirectory, "portable"));

    public static string PortableData => Path.Combine(AppContext.BaseDirectory, "data");

    static string Runtime => Path.Combine(
        Environment.GetEnvironmentVariable("LOCALAPPDATA") ?? Environment.GetFolderPath(Environment.SpecialFolder.LocalApplicationData),
        "phoneuse", "run");

    public static string LogPath => Path.Combine(Runtime, "phoneuse-up.log");
    static string PidPath => Path.Combine(Runtime, "phoneuse-background.pid");

    /// <summary>A phoneuse's Python: its .venv (a checkout, or an install made with the
    /// system's Python), else the standalone Python the app ships.</summary>
    static string? PythonIn(string root)
    {
        foreach (var p in new[] { Path.Combine(root, ".venv", "Scripts", "python.exe"), Path.Combine(root, "python", "python.exe") })
            if (File.Exists(p)) return p;
        return null;
    }

    public static string? Python => Root == null ? null : PythonIn(Root);

    static string? FindRoot()
    {
        var core = Path.Combine(AppContext.BaseDirectory, "core");
        if (File.Exists(Path.Combine(core, "bin", "phoneuse")) && PythonIn(core) != null)
            return core;
        for (var d = new DirectoryInfo(AppContext.BaseDirectory); d != null; d = d.Parent)
            if (File.Exists(Path.Combine(d.FullName, "bin", "phoneuse")) &&
                File.Exists(Path.Combine(d.FullName, ".venv", "Scripts", "python.exe")))
                return d.FullName;
        return null;
    }

    public static bool Answers(int port)
    {
        try
        {
            using var c = new TcpClient();
            return c.ConnectAsync("127.0.0.1", port).Wait(400) && c.Connected;
        }
        catch (Exception) { return false; }
    }

    /// <summary>Start phoneuse in the background. Always, even when something holds the port: a
    /// port that takes connections can belong to an old backend that no longer answers. The
    /// background copy decides (cli.up_windows): it exits when a backend answers, and otherwise
    /// stops the old ones and takes over. Returns a line for the log/tooltip.</summary>
    public static string EnsureRunning(int port)
    {
        if (Root == null)
            return Answers(port) ? "phoneuse is already running" : "phoneuse not found next to this app: run `phoneuse up`";
        var python = Python;
        if (python == null) return "phoneuse's Python is missing: reinstall Phone Use";
        var psi = new ProcessStartInfo(python, "-u -m phoneuse.windows.background")
        {
            WorkingDirectory = Root,
            UseShellExecute = false,
            CreateNoWindow = true,
        };
        psi.Environment["PYTHONUTF8"] = "1";
        if (Portable) psi.Environment["PHONEUSE_CONFIG_DIR"] = PortableData;
        Process.Start(psi);
        return "starting phoneuse";
    }

    /// <summary>The background phoneuse this window (or an earlier one) started, if it's running.</summary>
    public static Process? Background()
    {
        try
        {
            var p = Process.GetProcessById(int.Parse(File.ReadAllText(PidPath).Trim()));
            return p.ProcessName.StartsWith("python", StringComparison.OrdinalIgnoreCase) ? p : null;
        }
        catch (Exception) { return null; }
    }

    public static bool OwnsRunning => Background() != null;

    /// <summary>Stop the background phoneuse. Its services are in its job object and stop
    /// with it; the Bluetooth HID service keeps running, as after `phoneuse up`.</summary>
    public static void Stop()
    {
        var p = Background();
        if (p == null) return;
        try
        {
            p.Kill();
            p.WaitForExit(3000);
        }
        catch (Exception) { }
        try { File.Delete(PidPath); } catch (Exception) { }
    }
}
