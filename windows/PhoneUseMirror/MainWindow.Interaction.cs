using Microsoft.UI.Windowing;
using Microsoft.UI.Input;
using Microsoft.UI.Xaml;
using Microsoft.UI.Xaml.Controls;
using Microsoft.UI.Xaml.Input;
using Microsoft.UI.Xaml.Media;
using Windows.Graphics;

namespace PhoneUseMirror;

public sealed partial class MainWindow
{
    RectInt32 _frameBounds, _screenBounds;
    readonly Dictionary<int, FrameGrip> _frameGrips = new();
    int _manualResizeEdge;
    bool _manualMoving;
    RECT _manualResizeBounds;
    POINT _manualResizePointer;

    sealed class FrameGrip : UserControl
    {
        public FrameGrip(InputSystemCursorShape cursor)
        {
            Content = new Border { Background = new SolidColorBrush(Microsoft.UI.Colors.Transparent) };
            HorizontalContentAlignment = HorizontalAlignment.Stretch;
            VerticalContentAlignment = VerticalAlignment.Stretch;
            ProtectedCursor = InputSystemCursor.Create(cursor);
            IsTabStop = false;
            UseSystemFocusVisuals = false;
        }
    }

    void InitFrameGrips()
    {
        var canvas = new Canvas(); // empty canvas areas pass input through to the phone
        Grid.SetRowSpan(canvas, 2);
        Root.Children.Add(canvas);
        for (int edge = 0; edge <= 8; edge++)
        {
            var cursor = edge switch
            {
                0 => InputSystemCursorShape.SizeAll,
                1 or 2 => InputSystemCursorShape.SizeWestEast,
                3 or 6 => InputSystemCursorShape.SizeNorthSouth,
                4 or 8 => InputSystemCursorShape.SizeNorthwestSoutheast,
                _ => InputSystemCursorShape.SizeNortheastSouthwest,
            };
            var grip = new FrameGrip(cursor) { Tag = edge };
            string label = edge switch
            {
                0 => "Move phone window",
                1 => "Resize left edge", 2 => "Resize right edge", 3 => "Resize top edge",
                4 => "Resize top left corner", 5 => "Resize top right corner",
                6 => "Resize bottom edge", 7 => "Resize bottom left corner", _ => "Resize bottom right corner",
            };
            Microsoft.UI.Xaml.Automation.AutomationProperties.SetName(grip, label);
            grip.PointerPressed += FrameGrip_Pressed;
            grip.PointerMoved += FrameGrip_Moved;
            grip.PointerReleased += FrameGrip_Released;
            grip.PointerCanceled += FrameGrip_Released;
            grip.PointerCaptureLost += FrameGrip_Released;
            _frameGrips.Add(edge, grip);
            canvas.Children.Add(grip);
        }
    }

    void FrameGrip_Pressed(object sender, PointerRoutedEventArgs e)
    {
        var grip = (FrameGrip)sender;
        if (!e.GetCurrentPoint(grip).Properties.IsLeftButtonPressed) return;
        e.Handled = true;
        _conn.Send(new { t = "blur" });
        int edge = (int)grip.Tag;
        if (!GetCursorPos(out _manualResizePointer) || !grip.CapturePointer(e.Pointer)) return;
        _manualMoving = edge == 0;
        if (_manualMoving)
        {
            _moved = false;
            _dragAnchor = TrayAnchor();

        }
        _manualResizeEdge = edge;
        var at = AppWindow.Position;
        var size = AppWindow.Size;
        _manualResizeBounds = new RECT { Left = at.X, Top = at.Y, Right = at.X + size.Width, Bottom = at.Y + size.Height };
        _resizeStart = size;
        _moving = true;
        _sizing = !_manualMoving;
        SetSnapHint(false);
        ShowBar(true);
    }

    void FrameGrip_Moved(object sender, PointerRoutedEventArgs e)
    {
        if ((!_manualMoving && _manualResizeEdge == 0) || !GetCursorPos(out var cursor)) return;
        e.Handled = true;
        int dx = cursor.X - _manualResizePointer.X, dy = cursor.Y - _manualResizePointer.Y;
        var r = _manualResizeBounds;
        if (_manualMoving)
        {
            if (dx == 0 && dy == 0 && !_moved) return;
            if (!_moved) SetSnapped(false);
            _moved = true;
            r.Left += dx; r.Right += dx; r.Top += dy; r.Bottom += dy;
            AppWindow.Move(new PointInt32(r.Left, r.Top));
            Moving(r);
            return;
        }
        int edge = _manualResizeEdge;
        if (edge is 1 or 4 or 7) r.Left += dx;
        if (edge is 2 or 5 or 8) r.Right += dx;
        if (edge is 3 or 4 or 5) r.Top += dy;
        if (edge is 6 or 7 or 8) r.Bottom += dy;
        r = ResizedBounds(edge, r);
        var resized = new SizeInt32(r.Right - r.Left, r.Bottom - r.Top);
        if (_snapped && TrayAnchor() is { } anchor)
        {
            var pinned = SnappedPosition(anchor, resized);
            r.Left = pinned.X;
            r.Top = pinned.Y;
        }
        AppWindow.MoveAndResize(new RectInt32(r.Left, r.Top, resized.Width, resized.Height));
    }

    void FrameGrip_Released(object sender, PointerRoutedEventArgs e)
    {
        if (!_manualMoving && _manualResizeEdge == 0) return;
        // Include the release position even if Windows coalesced the last pointer move.
        FrameGrip_Moved(sender, e);
        bool moved = _manualMoving;
        _manualMoving = false;
        _manualResizeEdge = 0;
        _moving = _sizing = false;
        ((FrameGrip)sender).ReleasePointerCaptures();
        e.Handled = true;
        if (moved)
        {
            if (_moved) Dropped();
            else SetSnapHint(false);
        }
        else
        {
            PlaceSnapped();
        }
        UpdateBar();
    }

    bool _sizing;
    SizeInt32 _resizeStart;

    // Cache client-pixel bounds after layout; hit testing never changes the XAML layout.
    void PlaceFrameInteraction(double pw, double ph, double inset, double screenInset, double sw, double sh)
    {
        if (Root.XamlRoot == null) return;
        double scale = Root.XamlRoot.RasterizationScale;
        var at = Surface.TransformToVisual(Root).TransformPoint(new Windows.Foundation.Point());
        _frameBounds = new RectInt32((int)((at.X + inset) * scale), (int)((at.Y + inset) * scale),
            (int)((pw - 2 * inset) * scale), (int)((ph - 2 * inset) * scale));
        _screenBounds = new RectInt32((int)((at.X + screenInset) * scale), (int)((at.Y + screenInset) * scale),
            (int)(sw * scale), (int)(sh * scale));
        if (_frameGrips.Count == 0) return;
        double x = _frameBounds.X / scale, y = _frameBounds.Y / scale;
        double right = (_frameBounds.X + _frameBounds.Width) / scale;
        double bottom = (_frameBounds.Y + _frameBounds.Height) / scale;
        double screenX = _screenBounds.X / scale, screenY = _screenBounds.Y / scale;
        double screenRight = (_screenBounds.X + _screenBounds.Width) / scale;
        double screenBottom = (_screenBounds.Y + _screenBounds.Height) / scale;
        const double corner = 18;
        void Place(int edge, double left, double top, double width, double height)
        {
            var grip = _frameGrips[edge];
            Canvas.SetLeft(grip, Math.Max(0, left));
            Canvas.SetTop(grip, Math.Max(0, top));
            grip.Width = Math.Max(1, width);
            grip.Height = Math.Max(1, height);
        }
        Place(0, x + corner, y - 4, right - x - 2 * corner, screenY - y + 4);
        Place(1, x - 4, y + corner, screenX - x + 4, bottom - y - 2 * corner);
        Place(2, screenRight, y + corner, right - screenRight + 4, bottom - y - 2 * corner);
        Place(3, x + corner, 0, right - x - 2 * corner, 5);
        Place(4, x - 4, y - 4, screenX - x + 4, corner + 4);
        Place(5, screenRight, y - 4, right - screenRight + 4, corner + 4);
        Place(6, x + corner, screenBottom, right - x - 2 * corner, bottom - screenBottom + 4);
        Place(7, x - 4, bottom - corner, screenX - x + 4, corner + 4);
        Place(8, screenRight, bottom - corner, right - screenRight + 4, corner + 4);
    }

    RECT ResizedBounds(int edge, RECT r)
    {
        var centre = new PointInt32((r.Left + r.Right) / 2, (r.Top + r.Bottom) / 2);
        var area = DisplayArea.GetFromPoint(centre, DisplayAreaFallback.Primary).WorkArea;
        var (_, _, dw, dh) = PhoneUnits();
        double scale = WindowScale(), ratio = dw / dh;
        double padW = PadW * scale, padH = PadH * scale;
        double minH = Math.Max((_sideways ? MinShort : MinLong) * scale,
            (((_sideways ? MinLong : MinShort) * scale - padW) / ratio) + padH);
        double maxH = Math.Min(area.Height, (area.Width - padW) / ratio + padH);
        r = AspectResize(r, edge, ratio, padW, padH, Math.Min(minH, maxH), maxH, _resizeStart.Width, _resizeStart.Height);
        return r;
    }

    // Keep the opposite edge/corner fixed, with equal space on either side for single-edge drags.
    static RECT AspectResize(RECT r, int edge, double ratio, double padW, double padH,
        double minH, double maxH, int startW, int startH)
    {
        double w = r.Right - r.Left, h = r.Bottom - r.Top;
        bool horizontal = edge is 1 or 2 || (edge is 4 or 5 or 7 or 8 && Math.Abs(w - startW) >= Math.Abs(h - startH) * ratio);
        h = Math.Clamp(horizontal ? (w - padW) / ratio + padH : h, minH, maxH);
        int height = (int)Math.Round(h), width = (int)Math.Round((h - padH) * ratio + padW);
        if (edge is 1 or 4 or 7) r.Left = r.Right - width;
        else if (edge is 2 or 5 or 8) r.Right = r.Left + width;
        else { int cx = (r.Left + r.Right) / 2; r.Left = cx - width / 2; r.Right = r.Left + width; }
        if (edge is 3 or 4 or 5) r.Top = r.Bottom - height;
        else if (edge is 6 or 7 or 8) r.Bottom = r.Top + height;
        else { int cy = (r.Top + r.Bottom) / 2; r.Top = cy - height / 2; r.Bottom = r.Top + height; }
        return r;
    }
}
