using System.Runtime.InteropServices;

namespace PhoneUseMirror;

/// <summary>
/// A notification-area icon (WinUI 3 has none of its own): Shell_NotifyIcon on a
/// hidden window of the UI thread, whose messages WinUI's loop dispatches.
/// Click: Click (the window shows, or hides when it's pinned above the icon). Right-click: a menu.
/// </summary>
public sealed class TrayIcon : IDisposable
{
    const int WM_APP_TRAY = 0x8000 + 1;
    const int WM_LBUTTONUP = 0x0202, WM_LBUTTONDBLCLK = 0x0203, WM_RBUTTONUP = 0x0205, WM_CONTEXTMENU = 0x007B;
    const int NIN_BALLOONUSERCLICK = 0x0400 + 5;  // the notification was clicked
    const int NIM_ADD = 0, NIM_MODIFY = 1, NIM_DELETE = 2;
    const int NIF_MESSAGE = 1, NIF_ICON = 2, NIF_TIP = 4, NIF_INFO = 0x10;
    const int CmdOpen = 1, CmdLogs = 2, CmdQuit = 3, CmdAbout = 4, CmdSnap = 5;

    readonly IntPtr _hwnd, _icon;
    readonly WndProc _proc;   // kept alive: the window calls it
    readonly uint _taskbarCreated;
    readonly string _class = "PhoneUseTray" + Environment.ProcessId;
    string _tip = "Phone Use";

    public event Action? Click;
    public event Action? Open;
    public event Action<bool>? SnapChanged;

    /// <summary>The window is pinned above the icon: the menu's "Keep above the tray icon" is ticked.</summary>
    public bool Snapped { get; set; }
    public event Action? Logs;
    public event Action? About;
    public event Action? Quit;

    public TrayIcon(string iconPath)
    {
        _proc = Proc;
        var wc = new WNDCLASSEX
        {
            cbSize = Marshal.SizeOf<WNDCLASSEX>(),
            lpfnWndProc = Marshal.GetFunctionPointerForDelegate(_proc),
            hInstance = GetModuleHandle(null),
            lpszClassName = _class,
        };
        RegisterClassEx(ref wc);
        _hwnd = CreateWindowEx(0, _class, "Phone Use tray", 0, 0, 0, 0, 0, IntPtr.Zero, IntPtr.Zero, wc.hInstance, IntPtr.Zero);
        _taskbarCreated = RegisterWindowMessage("TaskbarCreated");
        _icon = LoadImage(IntPtr.Zero, iconPath, 1 /*IMAGE_ICON*/, GetSystemMetrics(49), GetSystemMetrics(50), 0x10 /*LR_LOADFROMFILE*/);
        Add();
    }

    void Add()
    {
        var d = Data(NIF_MESSAGE | NIF_ICON | NIF_TIP);
        Shell_NotifyIcon(NIM_ADD, ref d);
    }

    NOTIFYICONDATA Data(int flags) => new()
    {
        cbSize = Marshal.SizeOf<NOTIFYICONDATA>(),
        hWnd = _hwnd,
        uID = 1,
        uFlags = flags,
        uCallbackMessage = WM_APP_TRAY,
        hIcon = _icon,
        szTip = _tip,
        szInfo = "",
        szInfoTitle = "",
    };

    public void SetTip(string tip)
    {
        tip = tip.Length > 120 ? tip[..120] : tip;
        if (tip == _tip) return;
        _tip = tip;
        var d = Data(NIF_TIP);
        Shell_NotifyIcon(NIM_MODIFY, ref d);
    }

    /// <summary>A notification from the icon.</summary>
    public void Balloon(string title, string text)
    {
        var d = Data(NIF_INFO);
        d.szInfoTitle = title;
        d.szInfo = text;
        d.dwInfoFlags = 4 | 0x20; // NIIF_USER (the app's icon) | NIIF_LARGE_ICON
        d.hBalloonIcon = _icon;
        Shell_NotifyIcon(NIM_MODIFY, ref d);
    }

    IntPtr Proc(IntPtr hwnd, uint msg, IntPtr w, IntPtr l)
    {
        if (msg == WM_APP_TRAY)
        {
            switch ((int)l & 0xFFFF)
            {
                case WM_LBUTTONUP: Click?.Invoke(); break;
                case NIN_BALLOONUSERCLICK: Open?.Invoke(); break;
                case WM_RBUTTONUP:
                case WM_CONTEXTMENU: Menu(); break;
            }
            return IntPtr.Zero;
        }
        if (msg == _taskbarCreated && msg != 0) Add();  // Explorer restarted
        return DefWindowProc(hwnd, msg, w, l);
    }

    void Menu()
    {
        var m = CreatePopupMenu();
        AppendMenu(m, 0, CmdOpen, "Open Phone Use");
        AppendMenu(m, 0, CmdLogs, "Show phoneuse log");
        AppendMenu(m, 0, CmdAbout, "About Phone Use");
        AppendMenu(m, Snapped ? 0x8 /*MF_CHECKED*/ : 0, CmdSnap, "Keep above the tray icon");
        AppendMenu(m, 0x800 /*MF_SEPARATOR*/, 0, null);
        AppendMenu(m, 0, CmdQuit, "Quit");
        SetMenuDefaultItem(m, CmdOpen, 0);
        GetCursorPos(out var pt);
        SetForegroundWindow(_hwnd);  // so the menu closes when you click elsewhere
        int cmd = TrackPopupMenuEx(m, 0x100 /*TPM_RETURNCMD*/ | 0x20 /*TPM_BOTTOMALIGN*/, pt.X, pt.Y, _hwnd, IntPtr.Zero);
        DestroyMenu(m);
        switch (cmd)
        {
            case CmdOpen: Open?.Invoke(); break;
            case CmdLogs: Logs?.Invoke(); break;
            case CmdAbout: About?.Invoke(); break;
            case CmdSnap: SnapChanged?.Invoke(!Snapped); break;
            case CmdQuit: Quit?.Invoke(); break;
        }
    }

    /// <summary>The icon's rectangle on screen (physical pixels); in the overflow, where it is there.</summary>
    public Windows.Graphics.RectInt32? IconRect()
    {
        var id = new NOTIFYICONIDENTIFIER { cbSize = Marshal.SizeOf<NOTIFYICONIDENTIFIER>(), hWnd = _hwnd, uID = 1 };
        if (Shell_NotifyIconGetRect(ref id, out var r) != 0 || r.Right <= r.Left) return null;
        return new Windows.Graphics.RectInt32(r.Left, r.Top, r.Right - r.Left, r.Bottom - r.Top);
    }

    public void Dispose()
    {
        var d = Data(0);
        Shell_NotifyIcon(NIM_DELETE, ref d);
        DestroyWindow(_hwnd);
        if (_icon != IntPtr.Zero) DestroyIcon(_icon);
    }

    // ---------------------------------------------------------------- Win32

    delegate IntPtr WndProc(IntPtr hwnd, uint msg, IntPtr w, IntPtr l);

    [StructLayout(LayoutKind.Sequential, CharSet = CharSet.Unicode)]
    struct WNDCLASSEX
    {
        public int cbSize, style;
        public IntPtr lpfnWndProc;
        public int cbClsExtra, cbWndExtra;
        public IntPtr hInstance, hIcon, hCursor, hbrBackground;
        public string? lpszMenuName;
        public string lpszClassName;
        public IntPtr hIconSm;
    }

    [StructLayout(LayoutKind.Sequential, CharSet = CharSet.Unicode)]
    struct NOTIFYICONDATA
    {
        public int cbSize;
        public IntPtr hWnd;
        public int uID, uFlags, uCallbackMessage;
        public IntPtr hIcon;
        [MarshalAs(UnmanagedType.ByValTStr, SizeConst = 128)] public string szTip;
        public int dwState, dwStateMask;
        [MarshalAs(UnmanagedType.ByValTStr, SizeConst = 256)] public string szInfo;
        public int uVersion;
        [MarshalAs(UnmanagedType.ByValTStr, SizeConst = 64)] public string szInfoTitle;
        public int dwInfoFlags;
        public Guid guidItem;
        public IntPtr hBalloonIcon;
    }

    struct POINT { public int X, Y; }

    [StructLayout(LayoutKind.Sequential)]
    struct NOTIFYICONIDENTIFIER
    {
        public int cbSize;
        public IntPtr hWnd;
        public int uID;
        public Guid guidItem;
    }

    [StructLayout(LayoutKind.Sequential)]
    struct RECT { public int Left, Top, Right, Bottom; }

    [DllImport("shell32.dll")] static extern int Shell_NotifyIconGetRect(ref NOTIFYICONIDENTIFIER id, out RECT r);

    [DllImport("shell32.dll", CharSet = CharSet.Unicode)] static extern bool Shell_NotifyIcon(int msg, ref NOTIFYICONDATA d);
    [DllImport("user32.dll", CharSet = CharSet.Unicode)] static extern ushort RegisterClassEx(ref WNDCLASSEX wc);
    [DllImport("user32.dll", CharSet = CharSet.Unicode)]
    static extern IntPtr CreateWindowEx(int ex, string cls, string name, int style, int x, int y, int w, int h,
                                        IntPtr parent, IntPtr menu, IntPtr inst, IntPtr param);
    [DllImport("user32.dll")] static extern bool DestroyWindow(IntPtr h);
    [DllImport("user32.dll", CharSet = CharSet.Unicode)] static extern IntPtr DefWindowProc(IntPtr h, uint m, IntPtr w, IntPtr l);
    [DllImport("user32.dll", CharSet = CharSet.Unicode)] static extern uint RegisterWindowMessage(string s);
    [DllImport("user32.dll", CharSet = CharSet.Unicode)]
    static extern IntPtr LoadImage(IntPtr inst, string name, int type, int cx, int cy, int flags);
    [DllImport("user32.dll")] static extern bool DestroyIcon(IntPtr h);
    [DllImport("user32.dll")] static extern int GetSystemMetrics(int i);
    [DllImport("user32.dll")] static extern IntPtr CreatePopupMenu();
    [DllImport("user32.dll", CharSet = CharSet.Unicode)] static extern bool AppendMenu(IntPtr m, int flags, int id, string? text);
    [DllImport("user32.dll")] static extern bool SetMenuDefaultItem(IntPtr m, int item, int byPos);
    [DllImport("user32.dll")] static extern int TrackPopupMenuEx(IntPtr m, int flags, int x, int y, IntPtr hwnd, IntPtr p);
    [DllImport("user32.dll")] static extern bool DestroyMenu(IntPtr m);
    [DllImport("user32.dll")] static extern bool GetCursorPos(out POINT p);
    [DllImport("user32.dll")] static extern bool SetForegroundWindow(IntPtr h);
    [DllImport("kernel32.dll", CharSet = CharSet.Unicode)] static extern IntPtr GetModuleHandle(string? name);
}
