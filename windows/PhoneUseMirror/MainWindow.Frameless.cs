using System.Runtime.InteropServices;
using Microsoft.UI.Dispatching;
using Microsoft.UI.Input;
using Microsoft.UI.Windowing;
using Microsoft.UI.Xaml;
using Microsoft.UI.Xaml.Input;
using Microsoft.UI.Xaml.Media;
using Windows.Graphics;

namespace PhoneUseMirror;

/// <summary>
/// The window is only the phone: no border, no window background, everything around the
/// phone's frame see-through. Its bar (icon, mirroring status, About, Settings, minimize,
/// close) fades in above the phone while the pointer is in the strip where it sits. This window's own things
/// (notices, the agent's chip, setup, About) are drawn on the phone's screen (ScreenUi).
/// Ctrl + mouse wheel over the phone resizes the window.
/// </summary>
public sealed partial class MainWindow
{
    bool _barShown = true;
    DispatcherQueueTimer? _hoverTimer;
    static readonly Brush BarBrush = new SolidColorBrush(Windows.UI.Color.FromArgb(0xFF, 0x1C, 0x1C, 0x1E));

    /// <summary>Room above and below the phone (its shadow falls below it), in DIPs.</summary>
    const double PadAbove = 4, PadBelow = 16;

    /// <summary>Room beside the phone for its shadow, and above and below it with the bar (48), in DIPs.</summary>
    const double PadW = 24, PadH = 48 + PadAbove + PadBelow;

    /// <summary>The smallest window (DIPs) across and along the phone.</summary>
    const int MinShort = 300, MinLong = 500;

    void Frameless()
    {
        InitFrameGrips();
        BarBacking.Background = BarBrush;
        AppTitleBar.Background = new SolidColorBrush(Microsoft.UI.Colors.Transparent);
        SystemBackdrop = new ClearBackdrop();

        var hwnd = WinRT.Interop.WindowNative.GetWindowHandle(this);
        if (AppWindow.Presenter is OverlappedPresenter p)
        {
            p.SetBorderAndTitleBar(false, false);
            p.IsMaximizable = false;
            p.IsResizable = false;  // custom grips avoid the native resize frame
        }
        // the presenter leaves a dialog frame (a 1 px line around the window): off with it
        const int WS_CAPTION = 0x00C00000;  // WS_BORDER | WS_DLGFRAME
        SetWindowLong(hwnd, -16, GetWindowLong(hwnd, -16) & ~WS_CAPTION);
        SetWindowPos(hwnd, IntPtr.Zero, 0, 0, 0, 0, 0x0001 | 0x0002 | 0x0004 | 0x0010 | 0x0020);  // NOSIZE|NOMOVE|NOZORDER|NOACTIVATE|FRAMECHANGED

        int corners = 1;               // DWMWCP_DONOTROUND
        uint border = 0xFFFFFFFE;      // DWMWA_COLOR_NONE
        DwmSetWindowAttribute(hwnd, 33, ref corners, sizeof(int));
        DwmSetWindowAttribute(hwnd, 34, ref border, sizeof(uint));
        // DWM composes the window with its alpha (blur-behind over an empty region: no blur)
        var region = CreateRectRgn(-2, -2, -1, -1);
        var blur = new DWM_BLURBEHIND { dwFlags = 1 | 2, fEnable = true, hRgnBlur = region };  // ENABLE | BLURREGION
        DwmEnableBlurBehindWindow(hwnd, ref blur);
        DeleteObject(region);

        // presses, releases and the wheel on this window's own things stay with them, not the phone
        ScreenUi.PointerPressed += (_, e) => e.Handled = true;
        ScreenUi.PointerReleased += (_, e) => e.Handled = true;
        ScreenUi.PointerWheelChanged += (_, e) =>
        {
            if (!e.KeyModifiers.HasFlag(Windows.System.VirtualKeyModifiers.Control)) e.Handled = true;
        };

        Surface.LayoutUpdated += (_, _) =>
        {
            if (_glowShape.w > 0)
                PlaceFrameInteraction(Surface.ActualWidth, Surface.ActualHeight, _glowShape.inset,
                    Screen.Margin.Left, Screen.Width, Screen.Height);
        };

        _hoverTimer = _ui.CreateTimer();
        _hoverTimer.Interval = TimeSpan.FromMilliseconds(120);
        _hoverTimer.Tick += (_, _) => UpdateBar();
        _hoverTimer.Start();
        UpdateBar();
        _ui.TryEnqueue(AlignBarButtons);
    }

    /// <summary>ScreenUi exactly over the screen; notices and buttons clear of the Dynamic Island
    /// and the home indicator.</summary>
    void PlaceScreenUi(double o, double sw, double sh)
    {
        ScreenUi.Margin = new Thickness(o, o, 0, 0);
        ScreenUi.Width = sw;
        ScreenUi.Height = sh;
        bool sideways = sw > sh;
        double top = sideways ? Math.Max(8, sh * 0.04) : sh * 0.07, bottom = sideways ? 10 : sh * 0.045;
        double side = sideways ? sw * 0.07 : 0;  // sideways, the island is at the left
        Bars.Margin = new Thickness(10 + side, top, 10 + side, 0);
        SetupResume.Margin = new Thickness(0, top, 0, 0);
        AgentPanel.Margin = new Thickness(0, 0, 0, bottom);
        SetupOverlay.Padding = new Thickness(side, top, side, 0);
        // App pages replace the video, so they do not need the phone's island safe area.
        AboutOverlay.Padding = new Thickness(0, 8, 0, 0);
    }

    /// <summary>Match the cap to the titanium frame and fill down to where its top corners become straight.</summary>
    void PlaceBar(double phoneWidth, double frameInset, double cornerRadius)
    {
        double width = phoneWidth - 2 * frameInset;
        AppTitleBar.Width = width;
        BarBacking.Width = width;
        double barHeight = AppTitleBar.ActualHeight > 0 ? AppTitleBar.ActualHeight : 48;
        BarBacking.Height = barHeight + Surface.Margin.Top + frameInset + cornerRadius;
    }

    /// <summary>The bar keeps room on its right for the system caption buttons the window doesn't
    /// have: pull its buttons over it, to the bar's right edge.</summary>
    void AlignBarButtons()
    {
        const double edge = 4;
        if (BarClose.ActualWidth <= 0 || AppTitleBar.ActualWidth <= 0) return;
        var right = BarClose.TransformToVisual(AppTitleBar)
            .TransformPoint(new Windows.Foundation.Point(BarClose.ActualWidth, 0)).X;
        double gap = AppTitleBar.ActualWidth - right;
        if (Math.Abs(gap - edge) < 0.5) return;
        var m = BarButtons.Margin;
        BarButtons.Margin = new Thickness(m.Left, m.Top, m.Right - (gap - edge), m.Bottom);
    }

    /// <summary>The bar's status, and who has the phone.</summary>
    void ShowStatus()
    {
        if (_snapHint)
        {
            StatusText.Text = "Let go to keep it above the tray";
            return;
        }
        var status = !_live ? (_mirroring ? "Starting" : "Not mirroring") : _stale ? "Picture is old" : "Live";
        if (_agent == "driving") status += " · Agent in control";
        else if (_agent == "paused") status += " · Agent paused";
        StatusText.Text = status;
    }

    // ---------------------------------------------------------------- size

    /// <summary>Ctrl + wheel: a notch makes the phone 8% bigger or smaller, its top centre staying put.</summary>
    void ResizeBy(int wheelDelta)
    {
        if (wheelDelta == 0) return;
        double scale = WindowScale();
        var area = DisplayArea.GetFromWindowId(AppWindow.Id, DisplayAreaFallback.Primary).WorkArea;
        var (_, _, dw, dh) = PhoneUnits();
        var now = AppWindow.Size;
        double phoneH = Math.Max(1, now.Height - PadH * scale) * Math.Pow(1.08, wheelDelta / 120.0);
        double minPhone = (_sideways ? MinShort : MinLong) * scale - PadH * scale;
        double maxPhone = area.Height - PadH * scale;
        maxPhone = Math.Min(maxPhone, (area.Width - PadW * scale) * dh / dw);
        phoneH = Math.Clamp(phoneH, Math.Min(minPhone, maxPhone), maxPhone);
        var size = new SizeInt32((int)(phoneH * dw / dh + PadW * scale), (int)(phoneH + PadH * scale));
        int cx = AppWindow.Position.X + now.Width / 2;
        int x = Math.Clamp(cx - size.Width / 2, area.X, Math.Max(area.X, area.X + area.Width - size.Width));
        int y = Math.Clamp(AppWindow.Position.Y, area.Y, Math.Max(area.Y, area.Y + area.Height - size.Height));
        AppWindow.MoveAndResize(new RectInt32(x, y, size.Width, size.Height));
        PlaceSnapped();  // pinned above the tray: it grows and shrinks from the taskbar
    }

    // ---------------------------------------------------------------- the bar on hover

    /// <summary>Shown while the pointer is in the strip above the phone where the bar sits (the cursor,
    /// not XAML's pointer events: over the drag region those go to the non-client area), or while
    /// dragging the window.</summary>
    void UpdateBar()
    {
        bool over = false;
        if (GetCursorPos(out var c))
        {
            var pos = AppWindow.Position;
            var size = AppWindow.Size;
            double strip = AppTitleBar.ActualHeight > 0 ? AppTitleBar.ActualHeight : 48;
            int bottom = pos.Y + (int)(strip * WindowScale());
            over = c.X >= pos.X && c.X < pos.X + size.Width && c.Y >= pos.Y && c.Y < bottom;
        }
        ShowBar(over || _moving);
    }

    void ShowBar(bool on)
    {
        if (on == _barShown) return;
        _barShown = on;
        AppTitleBar.OpacityTransition ??= new ScalarTransition { Duration = TimeSpan.FromMilliseconds(150) };
        BarBacking.OpacityTransition ??= new ScalarTransition { Duration = TimeSpan.FromMilliseconds(150) };
        AppTitleBar.Opacity = on ? 1 : 0;
        BarBacking.Opacity = on ? 1 : 0;
        AppTitleBar.IsHitTestVisible = on;
    }

    void BarMinimize_Click(object sender, RoutedEventArgs e)
    {
        if (AppWindow.Presenter is OverlappedPresenter p) p.Minimize();
    }

    void BarClose_Click(object sender, RoutedEventArgs e) => HideToTray();  // as a system close would

    // ---------------------------------------------------------------- see-through

    /// <summary>A backdrop of a fully transparent colour: the desktop shows through wherever
    /// the window draws nothing.</summary>
    sealed class ClearBackdrop : SystemBackdrop
    {
        Windows.UI.Composition.Compositor? _compositor;

        protected override void OnTargetConnected(Microsoft.UI.Composition.ICompositionSupportsSystemBackdrop target, XamlRoot xamlRoot)
        {
            base.OnTargetConnected(target, xamlRoot);
            EnsureSystemDispatcherQueue();
            _compositor ??= new Windows.UI.Composition.Compositor();
            target.SystemBackdrop = _compositor.CreateColorBrush(Windows.UI.Color.FromArgb(0, 0, 0, 0));
        }

        protected override void OnTargetDisconnected(Microsoft.UI.Composition.ICompositionSupportsSystemBackdrop target)
        {
            base.OnTargetDisconnected(target);
            target.SystemBackdrop = null;
        }
    }

    // Windows.UI.Composition needs a Windows.System dispatcher queue on this thread.
    static object? _systemQueue;

    static void EnsureSystemDispatcherQueue()
    {
        if (_systemQueue != null || Windows.System.DispatcherQueue.GetForCurrentThread() != null) return;
        var options = new DispatcherQueueOptions { dwSize = Marshal.SizeOf<DispatcherQueueOptions>(), threadType = 2, apartmentType = 2 };
        CreateDispatcherQueueController(options, out var controller);
        _systemQueue = controller;
    }

    [StructLayout(LayoutKind.Sequential)]
    struct DispatcherQueueOptions
    {
        public int dwSize, threadType, apartmentType;
    }

    [StructLayout(LayoutKind.Sequential)]
    struct POINT { public int X, Y; }

    [StructLayout(LayoutKind.Sequential)]
    struct DWM_BLURBEHIND
    {
        public uint dwFlags;
        [MarshalAs(UnmanagedType.Bool)] public bool fEnable;
        public IntPtr hRgnBlur;
        [MarshalAs(UnmanagedType.Bool)] public bool fTransitionOnMaximized;
    }

    [DllImport("CoreMessaging.dll")]
    static extern int CreateDispatcherQueueController(DispatcherQueueOptions options,
        [MarshalAs(UnmanagedType.IUnknown)] out object controller);

    [DllImport("user32.dll")]
    static extern bool GetCursorPos(out POINT p);

    [DllImport("user32.dll")]
    static extern int GetWindowLong(IntPtr hwnd, int index);

    [DllImport("user32.dll")]
    static extern int SetWindowLong(IntPtr hwnd, int index, int value);

    [DllImport("user32.dll")]
    static extern bool SetWindowPos(IntPtr hwnd, IntPtr after, int x, int y, int cx, int cy, uint flags);

    [DllImport("dwmapi.dll")]
    static extern int DwmEnableBlurBehindWindow(IntPtr hwnd, ref DWM_BLURBEHIND blur);

    [DllImport("gdi32.dll")]
    static extern IntPtr CreateRectRgn(int left, int top, int right, int bottom);

    [DllImport("gdi32.dll")]
    static extern bool DeleteObject(IntPtr obj);

    [DllImport("dwmapi.dll")]
    static extern int DwmSetWindowAttribute(IntPtr hwnd, int attr, ref int value, int size);

    [DllImport("dwmapi.dll")]
    static extern int DwmSetWindowAttribute(IntPtr hwnd, int attr, ref uint value, int size);
}
