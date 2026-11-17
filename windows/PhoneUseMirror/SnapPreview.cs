using System.Runtime.InteropServices;
using Windows.Graphics;

namespace PhoneUseMirror;

/// <summary>A click-through outline of the drop destination. Never activates or moves the mirror.</summary>
sealed class SnapPreview : IDisposable
{
    static readonly WndProc Proc = (h, m, w, l) => m switch
    {
        0x84 => new IntPtr(-1), // WM_NCHITTEST: HTTRANSPARENT
        0x21 => new IntPtr(3),  // WM_MOUSEACTIVATE: MA_NOACTIVATE
        _ => DefWindowProc(h, m, w, l),
    };
    static readonly string ClassName = "PhoneUseSnapPreview" + Environment.ProcessId;
    static bool _registered;
    readonly IntPtr _hwnd;
    RectInt32? _bounds;

    public SnapPreview(IntPtr owner)
    {
        var instance = GetModuleHandle(null);
        if (!_registered)
        {
            var wc = new WNDCLASSEX
            {
                cbSize = (uint)Marshal.SizeOf<WNDCLASSEX>(),
                lpfnWndProc = Marshal.GetFunctionPointerForDelegate(Proc),
                hInstance = instance,
                hbrBackground = CreateSolidBrush(0x00FFD060), // RGB(96, 208, 255)
                lpszClassName = ClassName,
            };
            _registered = RegisterClassEx(ref wc) != 0;
            if (!_registered) { DeleteObject(wc.hbrBackground); return; }
        }
        // TOOLWINDOW | TRANSPARENT | LAYERED | NOACTIVATE, WS_POPUP
        _hwnd = CreateWindowEx(0x080800A0, ClassName, "", 0x80000000,
            0, 0, 1, 1, owner, IntPtr.Zero, instance, IntPtr.Zero);
        if (_hwnd != IntPtr.Zero) SetLayeredWindowAttributes(_hwnd, 0, 230, 2);
    }

    public void Show(PointInt32 at, SizeInt32 size, double scale)
    {
        if (_hwnd == IntPtr.Zero) return;
        int stroke = Math.Max(2, (int)Math.Round(3 * scale));
        int pad = stroke + 2;
        var bounds = new RectInt32(at.X - pad, at.Y - pad, size.Width + 2 * pad, size.Height + 2 * pad);
        if (_bounds != bounds)
        {
            int radius = (int)Math.Round(24 * scale);
            var outer = CreateRoundRectRgn(0, 0, bounds.Width + 1, bounds.Height + 1, radius, radius);
            var inner = CreateRoundRectRgn(stroke, stroke, bounds.Width + 1 - stroke, bounds.Height + 1 - stroke,
                Math.Max(1, radius - 2 * stroke), Math.Max(1, radius - 2 * stroke));
            CombineRgn(outer, outer, inner, 4); // RGN_DIFF: only the outline is visible
            DeleteObject(inner);
            if (SetWindowRgn(_hwnd, outer, true) == 0) DeleteObject(outer); // Windows owns a successful region
            _bounds = bounds;
        }
        SetWindowPos(_hwnd, new IntPtr(-1), bounds.X, bounds.Y, bounds.Width, bounds.Height,
            0x0010 | 0x0040); // NOACTIVATE | SHOWWINDOW
    }

    public void Hide()
    {
        if (_hwnd != IntPtr.Zero) ShowWindow(_hwnd, 0);
    }

    public void Dispose()
    {
        if (_hwnd != IntPtr.Zero) DestroyWindow(_hwnd);
    }

    delegate IntPtr WndProc(IntPtr hwnd, uint msg, IntPtr w, IntPtr l);
    [StructLayout(LayoutKind.Sequential, CharSet = CharSet.Unicode)]
    struct WNDCLASSEX
    {
        public uint cbSize, style;
        public IntPtr lpfnWndProc;
        public int cbClsExtra, cbWndExtra;
        public IntPtr hInstance, hIcon, hCursor, hbrBackground;
        public string? lpszMenuName, lpszClassName;
        public IntPtr hIconSm;
    }
    [DllImport("user32.dll", CharSet = CharSet.Unicode)] static extern ushort RegisterClassEx(ref WNDCLASSEX wc);
    [DllImport("user32.dll", CharSet = CharSet.Unicode)] static extern IntPtr CreateWindowEx(uint ex, string cls, string name,
        uint style, int x, int y, int w, int h, IntPtr owner, IntPtr menu, IntPtr instance, IntPtr param);
    [DllImport("user32.dll")] static extern IntPtr DefWindowProc(IntPtr hwnd, uint msg, IntPtr w, IntPtr l);
    [DllImport("user32.dll")] static extern bool SetWindowPos(IntPtr hwnd, IntPtr after, int x, int y, int w, int h, uint flags);
    [DllImport("user32.dll")] static extern bool SetLayeredWindowAttributes(IntPtr hwnd, uint key, byte alpha, uint flags);
    [DllImport("user32.dll")] static extern int SetWindowRgn(IntPtr hwnd, IntPtr region, bool redraw);
    [DllImport("user32.dll")] static extern bool ShowWindow(IntPtr hwnd, int command);
    [DllImport("user32.dll")] static extern bool DestroyWindow(IntPtr hwnd);
    [DllImport("kernel32.dll", CharSet = CharSet.Unicode)] static extern IntPtr GetModuleHandle(string? name);
    [DllImport("gdi32.dll")] static extern IntPtr CreateSolidBrush(uint color);
    [DllImport("gdi32.dll")] static extern IntPtr CreateRoundRectRgn(int left, int top, int right, int bottom, int ellipseWidth, int ellipseHeight);
    [DllImport("gdi32.dll")] static extern int CombineRgn(IntPtr dest, IntPtr a, IntPtr b, int mode);
    [DllImport("gdi32.dll")] static extern bool DeleteObject(IntPtr obj);
}
