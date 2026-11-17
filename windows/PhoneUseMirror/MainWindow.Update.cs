using Microsoft.UI.Dispatching;
using Microsoft.UI.Xaml;
using Microsoft.UI.Xaml.Controls;

namespace PhoneUseMirror;

/// <summary>
/// Updates (Updater): a look at GitHub's latest release 20 s after start and every 6 hours
/// (Settings › Updates › Check automatically), and Check for updates by hand. A newer release
/// shows a notice on the phone's screen (and a tray notification while the window is hidden).
/// Install downloads it and runs its installer, which replaces this copy and starts it again;
/// the portable app's Download opens the release's page instead. Nothing installs by itself:
/// an agent may be using the phone.
/// </summary>
public sealed partial class MainWindow
{
    DispatcherQueueTimer? _updateTimer;
    Updater.Release? _update;
    bool _updateChecking, _updateInstalling, _updateChecked;
    double _updateProgress;
    string? _updateError, _updateToldTray;

    void InitUpdates()
    {
        if (Updater.Mode == Updater.Kind.None)
        {
            UpdateSection.Visibility = Visibility.Collapsed;
            return;
        }
        _updateTimer = _ui.CreateTimer();
        _updateTimer.Interval = TimeSpan.FromSeconds(20);  // not while phoneuse is starting
        _updateTimer.Tick += async (t, _) =>
        {
            t.Interval = TimeSpan.FromHours(6);
            if (AppSettings.AutoUpdate) await CheckUpdates(manual: false);
        };
        _updateTimer.Start();
        ShowUpdate();
    }

    async Task CheckUpdates(bool manual)
    {
        if (_updateChecking || _updateInstalling) return;
        _updateChecking = true;
        _updateError = null;
        ShowUpdate();
        try
        {
            _update = await Updater.CheckAsync();
            _updateChecked = true;
        }
        catch (Exception ex)
        {
            // only said when asked: an offline PC shouldn't hear about it every 6 hours
            if (manual) _updateError = "Couldn't check for updates: " + ex.Message;
        }
        _updateChecking = false;
        ShowUpdate();
        if (_update == null) return;

        var tag = _update.Tag;
        if (manual || AppSettings.UpdateDismissed != tag) UpdateNotice.IsOpen = true;
        if (_hidden && _updateToldTray != tag && AppSettings.UpdateDismissed != tag)
        {
            _updateToldTray = tag;
            _tray?.Balloon($"Phone Use {VersionText(_update)} is available",
                Updater.CanInstall(_update) ? "Open Phone Use to install it." : "Open Phone Use to download it.");
        }
    }

    static string VersionText(Updater.Release r) => r.Version.Revision > 0 ? r.Version.ToString(4) : r.Version.ToString(3);

    void ShowUpdate()
    {
        if (Updater.Mode == Updater.Kind.None) return;
        bool busy = _updateChecking || _updateInstalling;
        UpdateRing.IsActive = busy;
        UpdateRing.Visibility = busy ? Visibility.Visible : Visibility.Collapsed;
        UpdateButton.IsEnabled = !busy;
        UpdateNoticeButton.IsEnabled = !busy;

        var current = "This is version " + Updater.CurrentText + ".";
        if (_updateInstalling)
        {
            UpdateText.Text = _updateProgress < 1
                ? $"Downloading Phone Use {VersionText(_update!)}… {_updateProgress:P0}"
                : "Starting the installer…";
            UpdateButton.Content = "Installing";
        }
        else if (_update != null)
        {
            bool install = Updater.CanInstall(_update);
            UpdateText.Text = $"Phone Use {VersionText(_update)} is available. {current}" +
                (install ? " Installing it restarts Phone Use; agents lose the phone for a moment." : "");
            UpdateButton.Content = install ? "Install and restart" : "Download";
            UpdateNoticeButton.Content = install ? "Install" : "Download";
            UpdateNotice.Title = $"Phone Use {VersionText(_update)} is available";
            UpdateNotice.Message = install ? "Installing it restarts Phone Use." : "Download it from the release page.";
        }
        else
        {
            UpdateText.Text = _updateError ?? (_updateChecking ? "Checking for updates…"
                : _updateChecked ? current + " It's the latest." : current);
            UpdateButton.Content = "Check for updates";
        }
        if (_updateError != null && _update != null) UpdateText.Text = _updateError;
    }

    async void UpdateButton_Click(object sender, RoutedEventArgs e)
    {
        if (_update == null) { await CheckUpdates(manual: true); return; }
        if (!Updater.CanInstall(_update))
        {
            await Windows.System.Launcher.LaunchUriAsync(new Uri(_update.PageUrl));
            return;
        }
        await InstallUpdate(_update);
    }

    async Task InstallUpdate(Updater.Release r)
    {
        if (_updateInstalling) return;
        _updateInstalling = true;
        _updateError = null;
        _updateProgress = 0;
        ShowUpdate();
        try
        {
            var progress = new Progress<double>(p => { _updateProgress = p; ShowUpdate(); });
            var setup = await Updater.DownloadAsync(r, progress);
            _updateProgress = 1;
            ShowUpdate();
            Updater.LaunchInstaller(setup, tray: _hidden);
        }
        catch (Exception ex)
        {
            _updateInstalling = false;
            _updateError = "Couldn't install the update: " + ex.Message;
            UpdateNotice.Message = _updateError;
            UpdateNotice.Severity = InfoBarSeverity.Error;
            ShowUpdate();
            return;
        }
        // The installer closes anything left running from this folder and starts the window again.
        QuitApp();
    }

    void UpdateAuto_Click(object sender, RoutedEventArgs e) => AppSettings.AutoUpdate = UpdateAuto.IsChecked == true;

    void UpdateNotice_Closed(InfoBar sender, InfoBarClosedEventArgs e)
    {
        if (e.Reason == InfoBarCloseReason.CloseButton && _update != null) AppSettings.UpdateDismissed = _update.Tag;
    }
}
