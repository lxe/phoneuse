using System.Diagnostics;
using System.Text.RegularExpressions;
using Microsoft.UI.Xaml;
using Microsoft.UI.Xaml.Controls;

namespace PhoneUseMirror;

/// <summary>
/// Settings › Screen reading: the OCR that reads the phone's screen (RapidOCR on ONNX Runtime,
/// windows\scripts\setup-vision.ps1, in .venv-vision next to phoneuse). Agents need it to read text,
/// tap labels, type and open apps by name. Install runs the setup script in the background (its output
/// goes to phoneuse-vision-setup.log) with PHONEUSE_PROGRESS=raw, so each download and the unpacking
/// report their bytes and show as a progress bar. It keeps nothing in pip's cache, so .venv-vision is
/// all of it on the PC.
/// Cancel, while it installs, stops it and removes what it left: the half-made .venv-vision and the
/// wheels pip-progress.py downloaded to %TEMP%.
/// Remove stops the OCR service and deletes .venv-vision.
/// </summary>
public sealed partial class MainWindow
{
    enum VisionJob { None, Installing, Cancelling, Removing }
    VisionJob _visionJob;
    ProcessJob? _visionProcs;       // the running setup-vision.ps1 and all it starts, for Cancel
    bool _visionCancelled;
    string? _visionNote;            // after a job: "Install cancelled.", "Removed: freed 2.21 GB."
    string _visionStep = "";
    string? _visionFile;            // what's downloading now, else null (installing: indeterminate)
    long _visionDone, _visionTotal;  // its bytes
    string? _visionError;

    static string? VisionVenv => Backend.Root == null ? null : Path.Combine(Backend.Root, ".venv-vision");

    static bool VisionInstalled =>
        VisionVenv is string v && File.Exists(Path.Combine(v, "Scripts", "python.exe")) &&
        Directory.Exists(Path.Combine(v, "Lib", "site-packages", "rapidocr"));

    static string VisionLog => Path.Combine(Path.GetDirectoryName(Backend.LogPath)!, "phoneuse-vision-setup.log");

    void ShowVision()
    {
        bool busy = _visionJob != VisionJob.None;
        VisionRing.IsActive = busy;
        VisionRing.Visibility = busy ? Visibility.Visible : Visibility.Collapsed;
        bool installing = _visionJob == VisionJob.Installing;
        VisionButton.IsEnabled = (!busy || installing) && Backend.Root != null;  // installing: Cancel
        VisionProgress.Visibility = installing ? Visibility.Visible : Visibility.Collapsed;
        VisionDetail.Visibility = installing && _visionFile != null ? Visibility.Visible : Visibility.Collapsed;
        if (Backend.Root == null)
        {
            VisionText.Text = "phoneuse's folder wasn't found next to this app.";
            return;
        }
        if (_visionJob == VisionJob.Installing)
        {
            VisionText.Text = "Installing (up to 2 GB, can take a while)… " + _visionStep;
            bool known = _visionFile != null && _visionTotal > 0;
            VisionProgress.IsIndeterminate = !known;
            if (known) VisionProgress.Value = Math.Min(1, (double)_visionDone / _visionTotal);
            VisionDetail.Text = _visionFile == null ? ""
                : known ? $"{_visionFile}: {Size(_visionDone)} of {Size(_visionTotal)} ({(double)_visionDone / _visionTotal:P0})"
                : _visionFile;
            VisionButton.Content = "Cancel";
            return;
        }
        if (_visionJob == VisionJob.Cancelling)
        {
            VisionText.Text = "Cancelling, and removing what the install left…";
            VisionButton.Content = "Cancelling";
            return;
        }
        if (_visionJob == VisionJob.Removing)
        {
            VisionText.Text = "Removing…";
            VisionButton.Content = "Removing";
            return;
        }
        bool on = VisionInstalled;
        VisionText.Text = (_visionError != null ? _visionError + " " : "") + (_visionNote != null ? _visionNote + " " : "") + (on
            ? "Installed. Agents can read the screen, tap text, type and open apps by name."
            : "Not installed. Agents can only use screenshots: no reading text, typing or opening apps by name.");
        VisionButton.Content = on ? "Remove" : "Install";
    }

    async void Vision_Click(object sender, RoutedEventArgs e)
    {
        if (_visionJob == VisionJob.Installing) { CancelVision(); return; }
        if (_visionJob != VisionJob.None || Backend.Root == null) return;
        _visionError = null;
        _visionNote = null;
        if (VisionInstalled) await RemoveVision();
        else await InstallVision();
    }

    async Task InstallVision()
    {
        _visionJob = VisionJob.Installing;
        _visionCancelled = false;
        _visionStep = "";
        _visionFile = null;
        ShowVision();
        int code;
        try
        {
            var psi = new ProcessStartInfo("powershell.exe",
                $"-NoProfile -ExecutionPolicy Bypass -File \"{Path.Combine(Backend.Root!, "windows", "scripts", "setup-vision.ps1")}\"")
            {
                WorkingDirectory = Backend.Root,
                UseShellExecute = false,
                CreateNoWindow = true,
                RedirectStandardOutput = true,
                RedirectStandardError = true,
            };
            psi.Environment["PHONEUSE_PROGRESS"] = "raw";  // setup-vision.ps1: Progress lines for each download
            psi.Environment["PYTHONUNBUFFERED"] = "1";
            using var log = new StreamWriter(VisionLog, append: true) { AutoFlush = true };
            log.WriteLine($"---- {DateTime.Now:yyyy-MM-dd HH:mm:ss} install");
            using var procs = _visionProcs = new ProcessJob();
            using var p = Process.Start(psi)!;
            procs.Add(p);  // before it has started anything: all of that joins the job
            if (_visionCancelled) KillVision();  // Cancel came while it was starting
            void Line(string? s)
            {
                if (string.IsNullOrWhiteSpace(s)) return;
                var progress = ProgressLine.Match(s);
                if (!progress.Success) lock (log) log.WriteLine(s);  // the log keeps what happened, not every tick
                _ui.TryEnqueue(() =>
                {
                    if (!VisionLine(s, progress)) return;
                    if (_visionJob == VisionJob.Installing) ShowVision();
                });
            }
            p.OutputDataReceived += (_, a) => Line(a.Data);
            p.ErrorDataReceived += (_, a) => Line(a.Data);
            p.BeginOutputReadLine();
            p.BeginErrorReadLine();
            await p.WaitForExitAsync();
            _visionProcs = null;
            code = p.ExitCode;
        }
        catch (Exception ex)
        {
            _visionProcs = null;
            code = -1;
            _visionStep = ex.Message;
        }
        _visionFile = null;
        if (_visionCancelled)
        {
            var left = await Task.Run(CleanUpCancelledInstall);
            try { File.AppendAllText(VisionLog, $"---- {DateTime.Now:yyyy-MM-dd HH:mm:ss} cancelled\n"); } catch (Exception) { }
            _visionJob = VisionJob.None;
            _visionNote = left == null ? "Install cancelled." : $"Install cancelled, but {left}";
            ShowVision();
            return;
        }
        _visionJob = VisionJob.None;
        if (code != 0 || !VisionInstalled)
            _visionError = "The install failed (the tray's log folder has phoneuse-vision-setup.log).";
        ShowVision();
        if (_visionError == null)
            _tray?.Balloon("Screen reading installed", "Agents can read the phone's screen, type and open apps by name.");
    }

    void CancelVision()
    {
        if (_visionJob != VisionJob.Installing) return;
        _visionCancelled = true;
        _visionJob = VisionJob.Cancelling;
        ShowVision();
        KillVision();
    }

    // powershell.exe, and under it pip-progress.py and its pip (ProcessJob: a tree kill misses the
    // interpreter a venv's python.exe launcher starts)
    void KillVision()
    {
        try { _visionProcs?.Kill(); } catch (Exception) { }
    }

    /// <summary>After Cancel: removes what the stopped install left. Null when it all went, else what didn't.</summary>
    static string? CleanUpCancelledInstall()
    {
        var failed = new List<string>();
        // pip-progress.py's downloads (its own cleanup never ran)
        foreach (var d in Directory.EnumerateDirectories(Path.GetTempPath(), "phoneuse-wheels-*"))
            if (!DeleteDirectory(d)) failed.Add(d);
        // the half-made venv: installing again starts it afresh
        if (VisionVenv is string v && !DeleteDirectory(v)) failed.Add(v);
        return failed.Count == 0 ? null : "couldn't remove " + string.Join(", ", failed) + ".";
    }

    static long FolderSize(string dir)
    {
        try
        {
            return Directory.Exists(dir)
                ? new DirectoryInfo(dir).EnumerateFiles("*", SearchOption.AllDirectories).Sum(f => f.Length)
                : 0;
        }
        catch (Exception) { return 0; }
    }

    /// <summary>Deletes a folder, retrying while files that were just in use close. True when it's gone.</summary>
    static bool DeleteDirectory(string dir)
    {
        for (int i = 0; ; i++)
        {
            try
            {
                if (Directory.Exists(dir)) Directory.Delete(dir, true);
                return true;
            }
            catch (Exception) when (i < 5) { Thread.Sleep(1000); }
            catch (Exception) { return false; }
        }
    }

    // setup-vision.ps1's lines (PHONEUSE_PROGRESS=raw): "==> step" (Write-Step), "Downloading <url or name> (<size>)"
    // from pip, then "Progress <bytes> of <total>" (pip's --progress-bar raw)
    static readonly Regex ProgressLine = new(@"^\s*Progress (\d+) of (\d+)\s*$");
    static readonly Regex DownloadingLine = new(@"^\s*Downloading (\S+)");

    /// <summary>Takes in one line of the setup script's output; true when the display changed.</summary>
    bool VisionLine(string s, Match progress)
    {
        if (progress.Success)
        {
            if (_visionFile == null) return false;
            _visionDone = long.Parse(progress.Groups[1].Value);
            _visionTotal = long.Parse(progress.Groups[2].Value);
            return true;
        }
        var t = s.Trim();
        if (t.StartsWith("==>"))
        {
            _visionStep = t.TrimStart('=', '>', ' ');
            _visionFile = null;
            return true;
        }
        var d = DownloadingLine.Match(t);
        if (d.Success && !d.Groups[1].Value.EndsWith(".metadata", StringComparison.OrdinalIgnoreCase))  // a few kB first
        {
            // pip names the URL (…/onnxruntime_gpu-1.23.2-cp313-cp313-win_amd64.whl): its file name is enough
            var name = Uri.UnescapeDataString(d.Groups[1].Value.Split('/', '\\').Last().Split('#', '?')[0]);
            _visionFile = name;
            _visionDone = _visionTotal = 0;
            return true;
        }
        // pip-progress.py, once the wheels are downloaded: "Unpacking 10 packages (4.62 GB)", then Progress lines
        if (t.StartsWith("Unpacking "))
        {
            _visionFile = t;
            _visionDone = _visionTotal = 0;
            return true;
        }
        return false;
    }

    static string Size(long b) =>
        b >= 1_000_000_000 ? $"{b / 1e9:0.00} GB" : b >= 1_000_000 ? $"{b / 1e6:0.0} MB" : $"{b / 1e3:0} KB";

    async Task RemoveVision()
    {
        var confirm = new ContentDialog
        {
            XamlRoot = Root.XamlRoot,
            RequestedTheme = ElementTheme.Dark,
            Title = "Remove screen reading?",
            Content = "Agents won't be able to read the screen, type or open apps by name until you install it again " +
                      "(a download of up to 2 GB).",
            PrimaryButtonText = "Remove",
            CloseButtonText = "Cancel",
            DefaultButton = ContentDialogButton.Close,
        };
        HideSettings();
        if (_ask != null) return;
        try { if (await confirm.ShowAsync() != ContentDialogResult.Primary) return; }
        catch (Exception) { return; }

        _visionJob = VisionJob.Removing;
        ShowVision();
        var root = Backend.Root!;
        var venv = VisionVenv!;
        long freed = 0;
        string? error = await Task.Run(() =>
        {
            try
            {
                // The OCR service runs from .venv-vision: ask it to quit before deleting its files.
                var stop = Process.Start(new ProcessStartInfo(Backend.Python!,
                                                              "-c \"from phoneuse.cli import stop_vision; stop_vision()\"")
                {
                    WorkingDirectory = root, UseShellExecute = false, CreateNoWindow = true,
                });
                stop?.WaitForExit(10000);
                var size = FolderSize(venv);
                if (!DeleteDirectory(venv)) return "Couldn't remove it all (files in use).";
                freed = size;
                return null;
            }
            catch (Exception ex) { return "Couldn't remove it all (" + ex.Message + ")."; }
        });
        _visionJob = VisionJob.None;
        _visionError = error;
        if (freed > 0) _visionNote = $"Removed: freed {Size(freed)}.";
        ShowVision();
    }
}
