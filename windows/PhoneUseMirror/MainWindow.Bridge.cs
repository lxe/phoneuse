using System.Net.Http;
using System.Text.Json.Nodes;
using Microsoft.UI.Xaml;
using Microsoft.UI.Xaml.Controls;

namespace PhoneUseMirror;

/// <summary>
/// Settings › Bluetooth bridge: "Use this PC's Bluetooth", on while this PC's (LE) keyboard
/// carries input, off while the bridge does (/api/bridge/use {on}; in bridge mode the HID service
/// closes this PC's LE keyboard), greyed out until a bridge is set up and answering; and
/// Bridge Settings, the bridge's page (BridgeView) over the phone, where About shows.
/// </summary>
public sealed partial class MainWindow
{
    BridgeView? _bridgeView;
    AboutView? _aboutView;

    async Task LoadBridgeSwitch() =>
        ShowBridgeSwitch(await _conn.CallAsync(HttpMethod.Get, "api/bridge", null, 15));

    void ShowBridgeSwitch(JsonObject? j)
    {
        var saved = j?["saved"] as JsonObject;
        var st = j?["status"] as JsonObject;
        bool failed = j == null || j["_failed"] != null;
        bool answering = saved != null && st != null && st["error"] == null;
        bool use = saved != null && ((bool?)saved["use"] ?? true);
        _settingUp = true;
        BridgeSwitch.IsOn = !(use && answering);  // on whenever this PC's Bluetooth carries input, greyed out or not
        BridgeSwitch.IsEnabled = answering;
        _settingUp = false;
        string daemon = (string?)j?["daemon"] ?? "";
        BridgeText.Text = failed ? "phoneuse isn't answering."
            : saved == null ? "No bridge set up. A LilyGO T-Display can be the iPhone's keyboard and mouse instead of this PC."
            : !answering ? "The bridge isn't answering, so this PC's Bluetooth carries input."
            : !use ? "This PC's Bluetooth carries input."
            : daemon.Contains(" le=closed") ? "The bridge carries input, and this PC's Bluetooth keyboard is closed."
            : "The bridge carries input.";
        BridgeButton.Content = saved == null ? "Set Up Bridge" : "Bridge Settings";
        BridgeButton.IsEnabled = !failed;
    }

    async void Bridge_Toggled(object sender, RoutedEventArgs e)
    {
        // IsOn="True" in the XAML toggles it while the window is built, before _conn exists;
        // greyed out, only code changes it
        if (_settingUp || _conn == null || !BridgeSwitch.IsEnabled) return;
        BridgeSwitch.IsEnabled = false;
        BridgeText.Text = BridgeSwitch.IsOn ? "Switching to this PC's Bluetooth…" : "Switching to the bridge…";
        var r = await _conn.PostAsync("api/bridge/use", new { on = !BridgeSwitch.IsOn });
        var daemon = (string?)r?["daemon"];
        await LoadBridgeSwitch();
        if (r?["error"] != null || (daemon != null && daemon.StartsWith("err")))
            BridgeText.Text = "Couldn't switch: " + ((string?)r?["error"] ?? daemon![4..]);
    }

    void BridgeSettings_Click(object sender, RoutedEventArgs e)
    {
        HideSettings();
        if (_bridgeView == null)
        {
            _bridgeView = new BridgeView(_conn);
            _bridgeView.Changed += () => _ = LoadBridgeSwitch();
        }
        ShowOverlay(_bridgeView);
        _bridgeView.Start();
    }

    /// <summary>A page over the phone, inside this window (About, Bridge Settings).</summary>
    void ShowOverlay(UIElement page)
    {
        ShowWindow();
        bool settings = ReferenceEquals(page, SettingsPage);
        if (_glow != null) _glow.Preview = false;
        SettingsPage.Visibility = settings ? Visibility.Visible : Visibility.Collapsed;
        AboutHost.Visibility = settings ? Visibility.Collapsed : Visibility.Visible;
        if (settings) _bridgeView?.Stop();
        if (!settings && !ReferenceEquals(AboutHost.Content, page))
        {
            if (ReferenceEquals(AboutHost.Content, _bridgeView)) _bridgeView?.Stop();
            AboutHost.Content = page;
        }
        AboutOverlay.Visibility = Visibility.Visible;
        _conn.Send(new { t = "blur" });  // let go of anything held on the phone
        AboutBack.Focus(FocusState.Programmatic);
    }
}
