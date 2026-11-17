using System.Runtime.InteropServices;
using Microsoft.UI.Windowing;
using Windows.Graphics;

namespace PhoneUseMirror;

/// <summary>
/// The window lives above its tray icon, as the clock's and the network's flyouts do, and stays
/// on top of other windows while it's there. Dragging it away by its bar takes it off the tray:
/// it becomes an ordinary window that stays where it's dropped. Dropped near the tray icon again,
/// it returns to one default spot: centred above the icon with an 8 DIP gap from the taskbar,
/// kept inside that monitor's work area. The preview, tray menu, Settings, and live resizing
/// all use the same destination.
/// </summary>
public sealed partial class MainWindow
{
    const double SnapGapDefault = 8;              // DIPs between the window and the taskbar
    const double ZoneGap = 200, ZoneAlong = 200;  // how far from the tray a drop still pins it

    enum Edge { Left, Top, Right, Bottom }

    /// <summary>Where the tray icon is: the taskbar's edge of the screen, the icon's centre along the
    /// taskbar, the taskbar's inner edge (Line), all in physical pixels.</summary>
    readonly record struct Anchor(Edge Edge, int Along, int Line, RectInt32 Work, double Scale);

    bool _snapped;
    bool _moving, _moved, _snapHint;
    SnapPreview? _snapPreview;
    Anchor? _dragAnchor;
    long _lastTrayClick;
    SubclassProc? _subclass;  // kept alive: the window calls it

    void InitSnap()
    {
        _snapPreview = new SnapPreview(WinRT.Interop.WindowNative.GetWindowHandle(this));
        Closed += (_, _) => _snapPreview?.Dispose();

        _subclass = WindowMessage;
        SetWindowSubclass(WinRT.Interop.WindowNative.GetWindowHandle(this), _subclass, 1, 0);

        // Floating is temporary: every app launch starts at the tray's default position.
        SetSnapped(true);
        // the icon moves when other icons come and go, and the taskbar can move: follow it
        _tick.Tick += (_, _) => { if (_snapped && !_moving && !_hidden) PlaceSnapped(); };
    }

    /// <summary>Pin the window above the tray icon (at its default spot), or let it go where it is.</summary>
    void SetSnapped(bool on)
    {
        _snapped = on;
        if (_tray != null) _tray.Snapped = on;
        if (SnapSwitch.IsOn != on)
        {
            bool filling = _settingUp;
            _settingUp = true;
            SnapSwitch.IsOn = on;
            _settingUp = filling;
        }
        if (on) PlaceSnapped();
        SetOnTop(on);
    }

    void SetOnTop(bool on)
    {
        if (AppWindow.Presenter is OverlappedPresenter p && p.IsAlwaysOnTop != on) p.IsAlwaysOnTop = on;
    }

    bool Minimized => AppWindow.Presenter is OverlappedPresenter { State: OverlappedPresenterState.Minimized };

    /// <summary>The tray icon's click: pinned and showing, it hides (as a flyout would); otherwise it shows.</summary>
    void TrayClicked()
    {
        long now = Environment.TickCount64;
        bool again = now - _lastTrayClick < GetDoubleClickTime();  // a double click is two clicks: don't hide on the second
        _lastTrayClick = now;
        if (_snapped && !_hidden && !Minimized && !again)
        {
            _toldAboutTray = true;  // they know where it went
            HideToTray();
        }
        else ShowFromTray();
    }

    /// <summary>Move the window to its pinned spot above the tray icon, if it isn't there.</summary>
    void PlaceSnapped()
    {
        if (!_snapped || _moving || Minimized || TrayAnchor() is not { } a) return;
        var to = SnappedPosition(a, AppWindow.Size);
        var at = AppWindow.Position;
        if (to.X != at.X || to.Y != at.Y) AppWindow.Move(to);
    }

    // ---------------------------------------------------------------- dragging

    IntPtr WindowMessage(IntPtr hwnd, uint msg, IntPtr w, IntPtr l, UIntPtr id, UIntPtr data)
    {
        switch (msg)
        {
            case WM_ENTERSIZEMOVE:
                SetSnapHint(false);
                _sizing = false;
                _resizeStart = AppWindow.Size;
                _moving = true;
                _moved = false;
                _dragAnchor = TrayAnchor();
                break;
            case WM_MOVING:
                if (_moving)
                {
                    if (!_moved) SetSnapped(false);
                    _moved = true;
                    Moving(Marshal.PtrToStructure<RECT>(l));
                }
                break;
            case WM_EXITSIZEMOVE:
                if (_moving)
                {
                    _moving = false;
                    if (_sizing)
                    {
                        SetSnapHint(false);
                        PlaceSnapped();
                    }
                    else if (_moved) Dropped();
                    else SetSnapHint(false);
                }
                break;
            case WM_DISPLAYCHANGE:
            case WM_SETTINGCHANGE:  // the work area changed: the taskbar moved or resized
                if (_snapped) _ui.TryEnqueue(PlaceSnapped);
                break;
        }
        return DefSubclassProc(hwnd, msg, w, l);
    }

    /// <summary>While dragging, preview snapping without changing the proposed window position.</summary>
    void Moving(RECT r)
    {
        if (_dragAnchor is not { } a) return;
        var size = new SizeInt32(r.Right - r.Left, r.Bottom - r.Top);
        var (along, gap) = Measure(a, new PointInt32(r.Left, r.Top), size);
        bool zone = InsideWork(a.Work, new PointInt32(r.Left, r.Top), size) && InZone(a, size, along, gap);
        bool ready = zone;
        SetSnapHint(ready);
        if (ready)
        {
            _snapPreview?.Show(SnappedPosition(a, size), size, a.Scale);
        }
        // Do not rewrite WM_MOVING's rectangle. SnappedPosition clamps to the tray's
        // monitor, which traps a held drag when it crosses into a neighbouring display.
        // Positioning and work-area clamping happen only after the mouse is released.
    }

    /// <summary>A drop in the tray zone pins to its default destination; all other drops float.</summary>
    void Dropped()
    {
        SetSnapHint(false);
        var size = AppWindow.Size;
        if (_dragAnchor is { } a)
        {
            var (along, gap) = Measure(a, AppWindow.Position, size);
            // A nearby drop on a different monitor belongs to that monitor, not the tray.
            var at = AppWindow.Position;
            bool onTrayMonitor = InsideWork(a.Work, at, size);
            if (onTrayMonitor && InZone(a, size, along, gap))
            {
                SetSnapped(true);
                return;
            }
        }
        SetSnapped(false);
    }

    void SetSnapHint(bool on)
    {
        if (!on) _snapPreview?.Hide();
        if (on == _snapHint) return;
        _snapHint = on;
        SnapReady.Visibility = on ? Microsoft.UI.Xaml.Visibility.Visible : Microsoft.UI.Xaml.Visibility.Collapsed;
        ShowStatus();
    }

    // ---------------------------------------------------------------- geometry

    static bool InsideWork(RectInt32 work, PointInt32 at, SizeInt32 size)
    {
        int cx = at.X + size.Width / 2, cy = at.Y + size.Height / 2;
        return cx >= work.X && cx < work.X + work.Width && cy >= work.Y && cy < work.Y + work.Height;
    }

    /// <summary>Near enough the tray to pin: not far off the taskbar, and over or beside the icon.</summary>
    static bool InZone(Anchor a, SizeInt32 size, double along, double gap)
    {
        bool across = a.Edge is Edge.Top or Edge.Bottom;
        double span = (across ? size.Width : size.Height) / a.Scale;  // the window's size along the taskbar
        double depth = (across ? size.Height : size.Width) / a.Scale;
        return gap <= ZoneGap && gap >= -depth && Math.Abs(along) <= span / 2 + ZoneAlong;
    }

    /// <summary>The default pinned destination, kept inside the tray monitor's work area.</summary>
    static PointInt32 SnappedPosition(Anchor a, SizeInt32 size)
    {
        int w = size.Width, h = size.Height, off = 0, g = (int)Math.Round(SnapGapDefault * a.Scale);
        var (x, y) = a.Edge switch
        {
            Edge.Left => (a.Line + g, a.Along + off - h / 2),
            Edge.Top => (a.Along + off - w / 2, a.Line + g),
            Edge.Right => (a.Line - g - w, a.Along + off - h / 2),
            _ => (a.Along + off - w / 2, a.Line - g - h),
        };
        var work = a.Work;
        x = Math.Clamp(x, work.X, Math.Max(work.X, work.X + work.Width - w));
        y = Math.Clamp(y, work.Y, Math.Max(work.Y, work.Y + work.Height - h));
        return new PointInt32(x, y);
    }

    /// <summary>Where a window at this position is from the icon: (along, gap) in DIPs.</summary>
    static (double along, double gap) Measure(Anchor a, PointInt32 at, SizeInt32 size)
    {
        int x = at.X, y = at.Y, w = size.Width, h = size.Height;
        var (along, gap) = a.Edge switch
        {
            Edge.Left => (y + h / 2 - a.Along, x - a.Line),
            Edge.Top => (x + w / 2 - a.Along, y - a.Line),
            Edge.Right => (y + h / 2 - a.Along, a.Line - (x + w)),
            _ => (x + w / 2 - a.Along, a.Line - (y + h)),
        };
        return (along / a.Scale, gap / a.Scale);
    }

    /// <summary>The tray icon's place: its own rectangle when it's on the taskbar, else (hidden in the
    /// overflow) the notification area's, else the taskbar's far end.</summary>
    Anchor? TrayAnchor()
    {
        var bar = new APPBARDATA { cbSize = Marshal.SizeOf<APPBARDATA>() };
        if (SHAppBarMessage(ABM_GETTASKBARPOS, ref bar) == IntPtr.Zero) return null;
        var tb = bar.rc;
        var edge = bar.uEdge switch { 0 => Edge.Left, 1 => Edge.Top, 2 => Edge.Right, _ => Edge.Bottom };
        bool across = edge is Edge.Top or Edge.Bottom;

        int cx, cy;
        if (_tray?.IconRect() is { } icon && Inside(tb, icon.X + icon.Width / 2, icon.Y + icon.Height / 2))
            (cx, cy) = (icon.X + icon.Width / 2, icon.Y + icon.Height / 2);
        else if (NotificationArea() is { } n)
            (cx, cy) = ((n.Left + n.Right) / 2, (n.Top + n.Bottom) / 2);
        else
            (cx, cy) = across ? (tb.Right - 1, (tb.Top + tb.Bottom) / 2) : ((tb.Left + tb.Right) / 2, tb.Bottom - 1);

        var work = DisplayArea.GetFromPoint(new PointInt32(cx, cy), DisplayAreaFallback.Primary).WorkArea;
        int line = edge switch
        {
            Edge.Left => Math.Max(work.X, tb.Right),
            Edge.Top => Math.Max(work.Y, tb.Bottom),
            Edge.Right => Math.Min(work.X + work.Width, tb.Left),
            _ => Math.Min(work.Y + work.Height, tb.Top),
        };
        return new Anchor(edge, across ? cx : cy, line, work, WindowScale());
    }

    static bool Inside(RECT r, int x, int y) => x >= r.Left && x < r.Right && y >= r.Top && y < r.Bottom;

    static RECT? NotificationArea()
    {
        var taskbar = FindWindow("Shell_TrayWnd", null);
        var area = taskbar == IntPtr.Zero ? IntPtr.Zero : FindWindowEx(taskbar, IntPtr.Zero, "TrayNotifyWnd", null);
        return area != IntPtr.Zero && GetWindowRect(area, out var r) && r.Right > r.Left ? r : null;
    }

    // ---------------------------------------------------------------- Win32

    const uint WM_SETTINGCHANGE = 0x001A, WM_DISPLAYCHANGE = 0x007E, WM_MOVING = 0x0216,
               WM_ENTERSIZEMOVE = 0x0231, WM_EXITSIZEMOVE = 0x0232;
    const uint ABM_GETTASKBARPOS = 5;

    delegate IntPtr SubclassProc(IntPtr hwnd, uint msg, IntPtr w, IntPtr l, UIntPtr id, UIntPtr data);

    [StructLayout(LayoutKind.Sequential)]
    struct RECT { public int Left, Top, Right, Bottom; }

    [StructLayout(LayoutKind.Sequential)]
    struct APPBARDATA
    {
        public int cbSize;
        public IntPtr hWnd;
        public uint uCallbackMessage, uEdge;
        public RECT rc;
        public IntPtr lParam;
    }

    [DllImport("comctl32.dll")]
    static extern bool SetWindowSubclass(IntPtr hwnd, SubclassProc proc, nuint id, nuint data);

    [DllImport("comctl32.dll")]
    static extern IntPtr DefSubclassProc(IntPtr hwnd, uint msg, IntPtr w, IntPtr l);

    [DllImport("shell32.dll")]
    static extern IntPtr SHAppBarMessage(uint msg, ref APPBARDATA data);

    [DllImport("user32.dll", CharSet = CharSet.Unicode)]
    static extern IntPtr FindWindow(string cls, string? name);

    [DllImport("user32.dll", CharSet = CharSet.Unicode)]
    static extern IntPtr FindWindowEx(IntPtr parent, IntPtr after, string cls, string? name);

    [DllImport("user32.dll")]
    static extern bool GetWindowRect(IntPtr hwnd, out RECT r);

    [DllImport("user32.dll")]
    static extern uint GetDoubleClickTime();
}
