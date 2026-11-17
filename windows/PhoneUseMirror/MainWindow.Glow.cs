using System.Runtime.InteropServices;
using Microsoft.UI.Windowing;
using Microsoft.UI.Xaml;
using Microsoft.UI.Xaml.Controls;
using Microsoft.UI.Xaml.Controls.Primitives;

namespace PhoneUseMirror;

/// <summary>
/// The agent glow (AgentGlow.cs) around the phone: kept on the phone's outline as the window
/// moves, resizes or turns, lit while an agent drives (amber while you took over), a ripple at each
/// of the agent's presses, and its look in Settings › Agent glow (previewed while the Agent Control tab is open).
/// </summary>
public sealed partial class MainWindow
{
    AgentGlow? _glow;
    bool _agentDown;
    // the phone in Surface's DIPs (Layout): its outer edge's inset and size and corner, the screen's size and corner
    (double inset, double w, double h, double r, double sw, double sh, double sr) _glowShape;

    void InitGlow()
    {
        try
        {
            _glow = new AgentGlow(WinRT.Interop.WindowNative.GetWindowHandle(this));
        }
        catch (Exception e)
        {
            System.Diagnostics.Debug.WriteLine("glow: " + e.Message);
            return;
        }
        _glow.Look = AppSettings.GlowLook;
        _glow.Enabled = AppSettings.GlowOn;
        _glow.AvailabilityChanged += () => _ui.TryEnqueue(ShowAgent);
        Surface.LayoutUpdated += (_, _) => PlaceGlow();
        AppWindow.Changed += (_, a) =>
        {
            if (a.DidPositionChange || a.DidSizeChange) PlaceGlow();
            if (a.DidVisibilityChange || a.DidPresenterChange || a.DidSizeChange) ShowGlowHost();
        };
        ShowGlowHost();
        Closed += (_, _) => _glow?.Dispose();
    }

    void ShowGlowHost() =>
        _glow?.SetHostVisible(AppWindow.IsVisible && AppWindow.Presenter is not OverlappedPresenter { State: OverlappedPresenterState.Minimized });

    /// <summary>The glow's lit and the plain ring isn't needed.</summary>
    bool GlowShowing => _glow is { Available: true, Enabled: true };

    /// <summary>The phone's outline in screen pixels, to the glow.</summary>
    void PlaceGlow()
    {
        if (_glow == null || Surface.ActualWidth <= 0 || Root.XamlRoot == null || _glowShape.w <= 0) return;
        double s = Root.XamlRoot.RasterizationScale;
        var at = Surface.TransformToVisual(Root).TransformPoint(new Windows.Foundation.Point(0, 0));
        var origin = new POINT();
        ClientToScreen(WinRT.Interop.WindowNative.GetWindowHandle(this), ref origin);
        var (inset, w, h, r, sw, sh, sr) = _glowShape;
        _glow.SetShape(origin.X + (at.X + inset) * s, origin.Y + (at.Y + inset) * s, w * s, h * s, r * s, sw * s, sh * s, sr * s);
    }

    /// <summary>An agent's pointer: a ripple where it presses.</summary>
    void GlowPointer(double x, double y, bool down)
    {
        if (down && !_agentDown) _glow?.Tap(x, y);
        _agentDown = down;
    }

    // ---------------------------------------------------------------- Settings › Agent glow

    bool _glowFilling = true;  // XAML value events can fire before the other controls exist.

    void FillGlowSettings()
    {
        _glowFilling = true;
        var look = AppSettings.GlowLook;
        GlowSwitch.IsOn = AppSettings.GlowOn;
        GlowIntensity.Value = look.Intensity;
        GlowSpeed.Value = look.Speed;
        GlowSize.Value = look.Size;
        GlowBleed.IsChecked = look.Bleed;
        GlowOptions.Visibility = GlowSwitch.IsOn ? Visibility.Visible : Visibility.Collapsed;
        GlowUnavailable.Visibility = _glow is { Available: true } ? Visibility.Collapsed : Visibility.Visible;
        _glowFilling = false;
        UpdateGlowPreview();
    }

    void SettingsTab_Changed(object sender, SelectionChangedEventArgs e) => UpdateGlowPreview();

    void UpdateGlowPreview()
    {
        if (_glow != null)
            _glow.Preview = SettingsVisible && ReferenceEquals(SettingsTabs.SelectedItem, AgentControlTab);
    }

    void Glow_Toggled(object sender, RoutedEventArgs e)
    {
        if (_glowFilling) return;
        AppSettings.GlowOn = GlowSwitch.IsOn;
        GlowOptions.Visibility = GlowSwitch.IsOn ? Visibility.Visible : Visibility.Collapsed;
        if (_glow != null) _glow.Enabled = GlowSwitch.IsOn;
        ShowAgent();
    }

    void GlowLook_Changed(object sender, object e)
    {
        if (_glowFilling) return;
        var look = new GlowLook((float)GlowIntensity.Value, (float)GlowSpeed.Value,
            (float)GlowSize.Value, GlowBleed.IsChecked == true);
        AppSettings.GlowLook = look;
        if (_glow != null) _glow.Look = look;
    }

    void GlowReset_Click(object sender, RoutedEventArgs e)
    {
        AppSettings.GlowLook = AppSettings.DefaultGlowLook;
        if (_glow != null) _glow.Look = AppSettings.DefaultGlowLook;
        FillGlowSettings();
    }

    [DllImport("user32.dll")]
    static extern bool ClientToScreen(IntPtr hwnd, ref POINT p);
}
