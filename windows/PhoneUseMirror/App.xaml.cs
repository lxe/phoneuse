using Microsoft.UI.Xaml;

namespace PhoneUseMirror;

/// <summary>
/// Phone Use's window. Start it on its own (it starts phoneuse if needed), or
/// `phoneuse up` starts it as PhoneUseMirror.exe --port 8777 (the browser mirror's
/// port, web_port in config.toml). One copy runs: a second start shows the first.
/// </summary>
public partial class App : Application
{
    const string Name = @"Local\PhoneUseMirror";
    MainWindow? _window;
    Mutex? _single;

    public App()
    {
        InitializeComponent();
    }

    protected override void OnLaunched(LaunchActivatedEventArgs args)
    {
        _single = new Mutex(true, Name, out bool first);
        if (!first)
        {
            // Already open (perhaps hidden in the tray): bring that one up instead.
            try { EventWaitHandle.OpenExisting(Name + ".Show").Set(); } catch (Exception) { }
            Exit();
            return;
        }
        int port = 8777;
        var argv = Environment.GetCommandLineArgs();
        for (int i = 1; i < argv.Length - 1; i++)
            if (argv[i] == "--port" && int.TryParse(argv[i + 1], out var p)) port = p;
        var window = _window = new MainWindow(port);
        Startup.Refresh();
        if (argv.Contains(Startup.TrayFlag)) window.StartInTray();  // at sign-in: tray only
        else window.Activate();
        if (argv.Contains("--about")) window.ShowAbout();

        var show = new EventWaitHandle(false, EventResetMode.AutoReset, Name + ".Show");
        var ui = window.DispatcherQueue;
        new Thread(() =>
        {
            while (show.WaitOne()) ui.TryEnqueue(window.ShowFromTaskbar);
        }) { IsBackground = true, Name = "show" }.Start();
    }
}
