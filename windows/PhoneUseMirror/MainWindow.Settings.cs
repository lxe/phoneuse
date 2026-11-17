using System.Text.Json.Nodes;
using Microsoft.UI.Xaml;
using Microsoft.UI.Xaml.Controls;
using Microsoft.UI.Xaml.Input;
using Microsoft.UI.Xaml.Media;

namespace PhoneUseMirror;

/// <summary>
/// The settings overlay: the browser mirror's settings panel, natively. Every setting
/// goes through the same server routes the page uses:
///   keep awake     /api/awake        {on}
///   agent control  /api/session/ask  {mode | seconds}
///   passcode       /api/passcode     GET / POST {code} / DELETE (never sent back)
///   bridge         /api/bridge/...   (MainWindow.Bridge.cs)
/// </summary>
public sealed partial class MainWindow
{
    bool _settingUp;  // filling the controls from the server: their change events send nothing

    async void OpenSettings_Click(object sender, RoutedEventArgs e)
    {
        if (SettingsVisible)
        {
            HideAbout();
            return;
        }
        ShowOverlay(SettingsPage);
        _settingUp = true;
        AwakeSwitch.IsOn = _awake;
        StartupSwitch.IsOn = Startup.Enabled;
        SnapSwitch.IsOn = _snapped;
        UpdateAuto.IsChecked = AppSettings.AutoUpdate;
        _settingUp = false;
        FillGlowSettings();
        FillFinishSettings();
        ShowUpdate();
        ShowVision();
        PasscodeBox.Password = "";
        await Task.WhenAll(LoadStartMode(), LoadPasscode(), LoadPaired(), LoadBridgeSwitch());
    }

    bool SettingsVisible => AboutOverlay.Visibility == Visibility.Visible && SettingsPage.Visibility == Visibility.Visible;

    void HideSettings()
    {
        if (SettingsVisible) HideAbout();
    }

    // The (i) button next to Settings: opens About, or closes it again.
    void OpenAbout_Click(object sender, RoutedEventArgs e)
    {
        if (AboutOverlay.Visibility == Visibility.Visible && !SettingsVisible && ReferenceEquals(AboutHost.Content, _aboutView)) HideAbout();
        else ShowAbout();
    }

    async void OpenBrowser_Click(object sender, RoutedEventArgs e) =>
        await Windows.System.Launcher.LaunchUriAsync(_conn.PageUri);

    void Snap_Toggled(object sender, RoutedEventArgs e)
    {
        if (_settingUp || SnapSwitch.IsOn == _snapped) return;
        SetSnapped(SnapSwitch.IsOn);
    }

    // ---------------------------------------------------------------- keep awake

    async void Awake_Toggled(object sender, RoutedEventArgs e)
    {
        if (_settingUp) return;
        await _conn.PostAsync("api/awake", new { on = AwakeSwitch.IsOn });
    }

    // ---------------------------------------------------------------- start with Windows (this app's own)

    void Startup_Toggled(object sender, RoutedEventArgs e)
    {
        if (_settingUp) return;
        try { Startup.Enabled = StartupSwitch.IsOn; }
        catch (Exception)
        {
            _settingUp = true;
            StartupSwitch.IsOn = Startup.Enabled;
            _settingUp = false;
        }
    }

    // ---------------------------------------------------------------- agent control

    async Task LoadStartMode()
    {
        var st = await _conn.GetAsync("api/session/ask");
        if (st != null && st["mode"] != null) ShowStartMode(st);
    }

    void ShowStartMode(JsonObject st)
    {
        _settingUp = true;
        var mode = (string?)st["mode"] ?? "auto";
        var secs = (int?)(double?)st["seconds"] ?? 10;
        StartMode.SelectedItem = StartMode.Items.OfType<ComboBoxItem>().FirstOrDefault(i => (string)i.Tag == mode);
        if (secs is not (5 or 10 or 15 or 30)) secs = 10;
        StartSeconds.SelectedItem = StartSeconds.Items.OfType<ComboBoxItem>().FirstOrDefault(i => (string)i.Tag == secs.ToString());
        StartSeconds.Visibility = mode == "countdown" ? Visibility.Visible : Visibility.Collapsed;
        _settingUp = false;
    }

    async void StartMode_Changed(object sender, SelectionChangedEventArgs e)
    {
        if (_settingUp || StartMode.SelectedItem is not ComboBoxItem item) return;
        var r = await _conn.PostAsync("api/session/ask", new { mode = (string)item.Tag });
        if (r != null && r["mode"] != null) ShowStartMode(r);
    }

    async void StartSeconds_Changed(object sender, SelectionChangedEventArgs e)
    {
        if (_settingUp || StartSeconds.SelectedItem is not ComboBoxItem item) return;
        var r = await _conn.PostAsync("api/session/ask", new { seconds = int.Parse((string)item.Tag) });
        if (r != null && r["mode"] != null) ShowStartMode(r);
    }

    // ---------------------------------------------------------------- passcode

    async Task LoadPasscode() => ShowPasscode(await _conn.GetAsync("api/passcode"));

    void ShowPasscode(JsonObject? j)
    {
        // Turned off (or phoneuse unreachable): no passcode settings at all.
        bool on = j != null && j["_failed"] == null && (bool?)j["enabled"] != false;
        PasscodeSection.Visibility = on ? Visibility.Visible : Visibility.Collapsed;
        if (!on) return;
        var error = (string?)j!["error"];
        PasscodeError.Text = error ?? "";
        PasscodeError.Visibility = error != null ? Visibility.Visible : Visibility.Collapsed;
        if (error == null)
        {
            bool saved = (bool?)j["saved"] ?? false;
            PasscodeRemove.Visibility = saved ? Visibility.Visible : Visibility.Collapsed;
            PasscodeBox.PlaceholderText = saved ? "Replace passcode" : "Enter passcode";
        }
    }

    void Passcode_Changed(object sender, RoutedEventArgs e) => PasscodeSave.IsEnabled = PasscodeBox.Password.Length > 0;

    void Passcode_KeyDown(object sender, KeyRoutedEventArgs e)
    {
        if (e.Key == Windows.System.VirtualKey.Enter && PasscodeBox.Password.Length > 0)
        {
            e.Handled = true;
            PasscodeSave_Click(sender, e);
        }
    }

    async void PasscodeSave_Click(object sender, RoutedEventArgs e)
    {
        var code = PasscodeBox.Password;
        PasscodeBox.Password = "";
        PasscodeSave.IsEnabled = false;
        if (code.Length > 0) ShowPasscode(await _conn.PostAsync("api/passcode", new { code }));
    }

    async void PasscodeRemove_Click(object sender, RoutedEventArgs e) =>
        ShowPasscode(await _conn.DeleteAsync("api/passcode"));
}
