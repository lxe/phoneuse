using Microsoft.UI.Windowing;
using Microsoft.UI.Xaml;
using Microsoft.UI.Xaml.Input;

namespace PhoneUseMirror;

/// <summary>
/// The window and phoneuse's lifetime: opening the window starts phoneuse when it
/// isn't running (Backend); closing the window only hides it to the tray, so
/// agents keep the phone; the tray's Quit stops the phoneuse this window started
/// and exits. An unlock or agent-start request brings the window back.
/// </summary>
public sealed partial class MainWindow
{
    TrayIcon? _tray;
    volatile bool _hidden;
    bool _quitting, _toldAboutTray;

    void InitTray(int port)
    {
        _tray = new TrayIcon(Path.Combine(AppContext.BaseDirectory, "Assets", "AppIcon.ico"));
        _tray.Click += () => _ui.TryEnqueue(TrayClicked);
        _tray.Open += () => _ui.TryEnqueue(ShowFromTray);
        _tray.SnapChanged += on => _ui.TryEnqueue(() => { SetSnapped(on); ShowWindow(); });
        _tray.Logs += () => _ui.TryEnqueue(ShowLog);
        _tray.About += () => _ui.TryEnqueue(() => { ShowFromTray(); ShowAbout(); });
        _tray.Quit += () => _ui.TryEnqueue(QuitApp);
        _tick.Tick += (_, _) =>
            _tray?.SetTip("Phone Use" + (string.IsNullOrEmpty(StatusText.Text) ? "" : " · " + StatusText.Text));

        AppWindow.Closing += (_, e) =>
        {
            if (_quitting) return;
            e.Cancel = true;
            HideToTray();
        };

        _ = Task.Run(() => Backend.EnsureRunning(port));
    }

    /// <summary>Started at sign-in (--tray): phoneuse runs, the window waits in the tray unshown.</summary>
    public void StartInTray()
    {
        _hidden = true;
        _toldAboutTray = true;
    }

    void HideToTray()
    {
        if (_hidden) return;
        SetSnapHint(false);
        _hidden = true;
        _conn.Send(new { t = "blur" });  // let go of anything held
        AppWindow.Hide();
        _video.Stop();                    // no decoding while nobody looks
        _playing = false;
        if (!_toldAboutTray)
        {
            _toldAboutTray = true;
            _tray?.Balloon("Phone Use is still running",
                "Agents can keep using the phone. Open it again from the tray icon; Quit there stops phoneuse.");
        }
    }

    // Only a tray-icon reopen resets a hidden window to the default snap position.
    public void ShowFromTray()
    {
        if (_quitting) return;
        if (_hidden) SetSnapped(true);
        ShowWindow();
    }

    // A second launch (including the pinned taskbar icon) restores this process's position.
    public void ShowFromTaskbar() => ShowWindow();

    void ShowWindow()
    {
        if (_quitting) return;
        if (AppWindow.Presenter is OverlappedPresenter p && p.State == OverlappedPresenterState.Minimized) p.Restore();
        PlaceSnapped();  // above the icon, wherever it is now
        AppWindow.Show(true);
        Activate();
        if (_hidden)
        {
            _hidden = false;
            _video.Reset();  // waits for, and asks for, a keyframe
        }
    }

    LogWindow? _log;
    public void ShowAbout()
    {
        ShowOverlay(_aboutView ??= new AboutView());
    }

    void HideAbout()
    {
        _bridgeView?.Stop();
        if (_glow != null) _glow.Preview = false;
        AboutOverlay.Visibility = Visibility.Collapsed;
        Surface.Focus(FocusState.Programmatic);
    }

    void AboutBack_Click(object sender, RoutedEventArgs e) => HideAbout();

    void AboutOverlay_KeyDown(object sender, KeyRoutedEventArgs e)
    {
        if (e.Key != Windows.System.VirtualKey.Escape) return;
        e.Handled = true;
        HideAbout();
    }

    void ShowLog()
    {
        if (_log == null)
        {
            _log = new LogWindow(Backend.LogPath);
            _log.Closed += (_, _) => _log = null;
        }
        _log.Activate();
    }

    void QuitApp()
    {
        _quitting = true;
        _log?.Close();
        _tray?.Dispose();
        _tray = null;
        Backend.Stop();
        Close();
        Microsoft.UI.Xaml.Application.Current.Exit();
    }
}
