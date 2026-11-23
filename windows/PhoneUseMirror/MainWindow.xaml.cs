using System.Text.Json.Nodes;
using Microsoft.UI.Dispatching;
using Microsoft.UI.Input;
using Microsoft.UI.Windowing;
using Microsoft.UI.Xaml;
using Microsoft.UI.Xaml.Controls;
using Microsoft.UI.Xaml.Input;
using Microsoft.UI.Xaml.Media;
using Windows.Graphics;

namespace PhoneUseMirror;

/// <summary>
/// The mirror window: the phone's picture inside a phone-shaped bezel. Mouse and
/// keys over it go to the phone through phoneuse's browser-mirror server, exactly
/// as the browser page's do; positions are fractions of the screen, and outside
/// 0..1 is the bezel (a press there becomes a touch at the nearest edge).
/// </summary>
public sealed partial class MainWindow : Window
{
    readonly MirrorConnection _conn;
    readonly VideoPipeline _video;
    readonly DispatcherQueue _ui;
    readonly DispatcherQueueTimer _tick;

    // the server's last "state"
    bool _live, _mirroring, _stale, _awake;
    bool? _bluetooth;
    string _agent = "idle", _name = "Phone Use", _agentAction = "";
    double _screenW = 402, _screenH = 874;

    /// <summary>The screen's corner radius in units of its short side: the model's (PhoneModel.CornerPt),
    /// not the server's, which is the browser mirror's own approximation.</summary>
    double _radius => _phone.CornerPt / Math.Max(1, Math.Min(_screenW, _screenH));

    bool _sideways;         // the window is shaped for a phone in landscape
    bool? _frameSideways;   // the decoded picture is wider than tall; null before a picture
    bool _playing;          // a frame of the current stream is on screen
    bool _pressed;          // the left button went down over the phone
    long _troubleSince;     // when the current no-picture problem started (ticks), 0 = none

    public MainWindow(int port)
    {
        InitializeComponent();
        _ui = DispatcherQueue.GetForCurrentThread();
        ExtendsContentIntoTitleBar = true;
        SetTitleBar(AppTitleBar);
        AppTitleBar.SizeChanged += (_, _) => AlignBarButtons();
        AppWindow.TitleBar.PreferredHeightOption = TitleBarHeightOption.Tall;
        var icon = Path.Combine(AppContext.BaseDirectory, "Assets", "AppIcon.ico");
        if (File.Exists(icon)) AppWindow.SetIcon(icon);

        _video = new VideoPipeline(Video, _ui);
        Video.SizeChanged += (_, _) => ResizeVideo();
        Video.CompositionScaleChanged += (_, _) => ResizeVideo();
        Frameless();
        ApplyFinish(FrameFinish.Get(AppSettings.FrameFinish));
        WatchDisplay();  // HDR: the finish drawn for it
        InitGlow();
        SizeToPhone();
        // the minimum is in pixels: again when the window moves to a screen with other scaling
        Root.Loaded += (_, _) => Root.XamlRoot.Changed += (r, _) => LimitSize(r.RasterizationScale);

        _conn = new MirrorConnection(port);
        _video.KeyframeNeeded += () => _conn.Send(new { t = "keyframe" });
        _video.Playing += () => _ui.TryEnqueue(OnPlaying);
        _video.FrameSize += (w, h) => _ui.TryEnqueue(() => OnFrameSize(w, h));
        _conn.Frame += (key, data) => { if (!_hidden) _video.Push(key, data); };  // straight from the socket's thread
        _conn.Message += m => _ui.TryEnqueue(() => OnMessage(m));
        _conn.ConnectedChanged += on => _ui.TryEnqueue(() => OnConnected(on));

        // handledEventsToo: children (the swap chain panel, the bezel) must not swallow hover moves
        Surface.AddHandler(UIElement.PointerMovedEvent, new PointerEventHandler(Surface_PointerMoved), true);
        Surface.PointerPressed += Surface_PointerPressed;
        Surface.PointerReleased += Surface_PointerReleased;
        Surface.PointerCanceled += (_, _) => Lift();
        Surface.PointerCaptureLost += (_, _) => Lift();
        Surface.PointerWheelChanged += Surface_PointerWheelChanged;
        Surface.PreviewKeyDown += (_, e) => OnKey(e, true);
        Surface.PreviewKeyUp += (_, e) => OnKey(e, false);
        Surface.Loaded += (_, _) => Surface.Focus(FocusState.Programmatic);
        Activated += (_, e) =>
        {
            if (e.WindowActivationState == WindowActivationState.Deactivated)
                _conn.Send(new { t = "blur" });  // let go of held keys and buttons
            else
                Surface.Focus(FocusState.Programmatic);
        };
        Closed += (_, _) => { _conn.Dispose(); _video.Dispose(); };

        _tick = _ui.CreateTimer();
        _tick.Interval = TimeSpan.FromSeconds(1);
        _tick.Tick += (_, _) => UpdateNote();
        _tick.Start();
        InitTray(port);
        InitSnap();
        InitUpdates();
        StartSetup();

        UpdateNote();
        _conn.Start();
    }

    // ---------------------------------------------------------------- server events

    void OnConnected(bool on)
    {
        if (on)
        {
            _video.Reset();
            _ = LoadPaired();  // the pairing bar, once phoneuse answers
        }
        else
        {
            _video.Stop();
            _playing = false;
        }
        UpdateNote();
    }

    void OnPlaying()
    {
        if (_playing) return;
        _playing = true;
        _conn.Send(new { t = "playing" });
        UpdateNote();
    }

    void OnMessage(JsonObject m)
    {
        switch ((string?)m["type"])
        {
            case "state": OnState(m); break;
            case "agent-cursor":
                ShowAgentDot((double?)m["x"] ?? 0, (double?)m["y"] ?? 0, (bool?)m["down"] ?? false);
                break;
            case "agent-action":
                _agentAction = (string?)m["action"] ?? "";
                if (_agent == "driving") AgentWhat.Text = _agentAction;
                break;
            case "unlock-request":
                ShowWindow();
                _ = ShowAsk((string?)m["id"], "unlock", null, null);
                break;
            case "session-request":
                ShowWindow();
                _ = ShowAsk((string?)m["id"], "session", (string?)m["action"], (double?)m["deadline"]);
                break;
            case "unlock-done": HideAsk("unlock"); break;
            case "session-done": HideAsk("session"); break;
        }
    }

    void OnState(JsonObject m)
    {
        var wasLive = _live;
        _live = (bool?)m["live"] ?? false;
        _mirroring = (bool?)m["mirroring"] ?? false;
        _stale = (bool?)m["stale"] ?? false;
        _awake = (bool?)m["awake"] ?? false;
        _bluetooth = (bool?)m["bluetooth"];
        _agent = (string?)m["agent"] ?? "idle";
        _name = (string?)m["name"] ?? _name;
        if (m["screen"] is JsonArray s && s.Count == 2)
        {
            _screenW = (double?)s[0] ?? _screenW;
            _screenH = (double?)s[1] ?? _screenH;
        }
        if (m.ContainsKey("model")) SetModel((string?)m["model"]);
        // The decoder hears of a turn first (OnFrameSize); the server's screen catches up up to a
        // second later and must not turn the phone back meanwhile.
        if (_frameSideways is bool side) FaceScreen(side);
        Turned();

        if (!_live && wasLive)
        {
            _playing = false;
            _frameSideways = null;
            _video.Stop();  // never leave an old picture up
        }
        else if (_live && !wasLive && !_playing)
        {
            _video.Reset();
        }
        Video.Visibility = _live ? Visibility.Visible : Visibility.Collapsed;
        Video.Opacity = _stale ? 0.55 : 1;

        if (AwakeSwitch.IsOn != _awake)
        {
            _settingUp = true;
            AwakeSwitch.IsOn = _awake;
            _settingUp = false;
        }
        ShowStatus();
        Layout();
        ShowAgent();
        UpdateNote();
        OnCalibration(m);
    }

    // ---------------------------------------------------------------- notes

    // Notes only appear when there's no picture: short gaps show nothing at all.
    void UpdateNote()
    {
        var connected = _conn?.Connected ?? false;
        if (connected && !_live && !_mirroring)
        {
            SetNote($"Screen Mirroring stopped, or the iPhone is locked.\nUnlock it, then open Control Center › Screen Mirroring › {_name}");
            return;
        }
        if (connected && _live && _stale)
        {
            SetNote("This picture is old.\nThe iPhone's screen is off, or mirroring stalled. Wake the phone, or reconnect Screen Mirroring.");
            return;
        }
        // it stays while the link is down (the setup checklist says it as a step instead)
        if (connected && _bluetooth == false && SetupOverlay.Visibility != Visibility.Visible)
        {
            SetNote("Mouse and keyboard not connected.\nClicks and typing can't reach the iPhone. Pair it once: iPhone Settings › Bluetooth › this PC's name.");
            return;
        }
        if (_playing) { _troubleSince = 0; SetNote(null); return; }
        if (_troubleSince == 0) _troubleSince = Environment.TickCount64;
        if (Environment.TickCount64 - _troubleSince < 5000) { SetNote(null); return; }
        if (!connected) { SetNote("Waiting for phoneuse up"); return; }
        if (_live) SetNote("The iPhone is mirroring, but no picture has arrived.\nStop and start Screen Mirroring once.");
        else SetNote(null);
    }

    void SetNote(string? text)
    {
        NoteBox.Visibility = text == null ? Visibility.Collapsed : Visibility.Visible;
        if (text != null) NoteText.Text = text;
    }

    // ---------------------------------------------------------------- layout

    // The phone around the screen, in units of the screen's short side (so a sideways phone keeps
    // them): a thin black border around the glass (the model's: PhoneModel.cs), the band, and how far
    // the side buttons stand out. Slimmer than the server's bezel, which the browser mirror uses.
    const double Band = 0.017, Knob = 0.009;
    PhoneModel _phone = PhoneModel.Default;
    double Glass => _phone.Glass;
    double Frame => Glass + Band + Knob;

    // Side buttons of an upright phone, for the models Apple has no drawings of (the iPhone 11 and
    // older): (left side?, top, bottom) as fractions of the band's height, estimated from photos.
    static readonly (bool left, double top, double bottom)[] EstimatedSideButtons =
    {
        (true, 0.155, 0.180),   // ring/silent switch
        (true, 0.245, 0.316),   // volume up
        (true, 0.340, 0.411),   // volume down
        (false, 0.265, 0.385),  // side button
    };

    /// <summary>This model's side buttons, where its drawing puts them (PhoneModel).</summary>
    (bool left, double top, double bottom)[] SideButtons =>
        _phone.Buttons is { } b ? b.Select(x => (x.Left, x.Top, x.Bottom)).ToArray() : EstimatedSideButtons;

    /// <summary>Camera Control: lower on the right, where this model has it (PhoneModel), a sapphire
    /// button barely standing out of the band.</summary>
    (bool left, double top, double bottom)[] CameraButton =>
        _phone.CameraControlAt is { } at ? new[] { (at.Left, at.Top, at.Bottom) } : Array.Empty<(bool, double, double)>();

    /// <summary>The phone that's mirroring changed: its frame from now on.</summary>
    void SetModel(string? identifier)
    {
        var phone = PhoneModel.For(identifier);
        if (phone == _phone) return;
        _phone = phone;
        _bandDrawn = default;  // its material may differ
        Layout();
        if (SettingsVisible) FillFinishSettings();
    }

    /// <summary>The screen and the whole phone (buttons included) in units of the screen's short side.</summary>
    (double w, double h, double dw, double dh) PhoneUnits()
    {
        double s = Math.Max(1, Math.Min(_screenW, _screenH));
        double w = _screenW / s, h = _screenH / s;
        return (w, h, w + 2 * Frame, h + 2 * Frame);
    }

    /// <summary>A w x h rectangle at (x, y) with iOS's continuous corners of radius r: the corners of the
    /// phone and its screen (ContinuousCorner.cs).</summary>
    static PathGeometry Squircle(double x, double y, double w, double h, double r) => ContinuousCorner.Rect(x, y, w, h, r);

    void Stage_SizeChanged(object sender, SizeChangedEventArgs e) => Layout();

    /// <summary>The decoder's picture changed size: when the phone turned, turn the window now,
    /// with the first frame of the new shape, not when the server notices.</summary>
    void OnFrameSize(int w, int h)
    {
        if (w <= 0 || h <= 0) return;
        _frameSideways = w > h;
        FaceScreen(w > h);
        Turned();
        Layout();
        ShowCalibration();  // upright now: the calibration starts (it measures an upright phone only)
    }

    /// <summary>The screen's size in points, upright or sideways.</summary>
    void FaceScreen(bool sideways)
    {
        double s = Math.Min(_screenW, _screenH), l = Math.Max(_screenW, _screenH);
        (_screenW, _screenH) = sideways ? (l, s) : (s, l);
    }

    /// <summary>Reshape the window only when the phone turns, not on every state (the user may have sized it).</summary>
    void Turned()
    {
        if ((_screenW > _screenH) == _sideways) return;
        _sideways = _screenW > _screenH;
        ReshapeWindow();
    }

    // the screen's corner radius in DIPs (Layout), which the video cuts its picture to
    double _screenCorner;

    void ResizeVideo()
    {
        // the panel reaches past the screen by its margin on every side: its corners are that much wider
        if (Video.ActualWidth > 0 && Video.ActualHeight > 0)
            _video.Resize(Video.ActualWidth, Video.ActualHeight, Video.CompositionScaleX, Video.CompositionScaleY,
                _screenCorner - Video.Margin.Left);
    }

    /// <summary>The phone body fits the window; the screen keeps the phone's shape.</summary>
    void Layout()
    {
        double W = Stage.ActualWidth, H = Stage.ActualHeight;
        if (W <= 0 || H <= 0) return;
        var (w, h, dw, dh) = PhoneUnits();
        const double below = PadBelow, above = PadAbove;
        double u = Math.Min((W - PadW) / dw, (H - below - above) / dh);  // px per screen short side
        if (u <= 10) return;

        double pw = dw * u, ph = dh * u, sw = w * u, sh = h * u, sr = _radius * u;
        double k = Knob * u, t = Band * u, g = Glass * u, o = k + t + g;  // o: the screen's offset
        Surface.Width = pw;
        Surface.Height = ph;
        Surface.Margin = new Thickness(0, Math.Max(above, (H - below - ph) / 2), 0, 0);

        Screen.Margin = new Thickness(o, o, 0, 0);
        Screen.Width = sw;
        Screen.Height = sh;
        DotLayer.Width = sw;
        DotLayer.Height = sh;
        PlaceScreenUi(o, sw, sh);
        ClipScreen(sw, sh, sr);
        if (Math.Abs(sr - _screenCorner) > 0.01)
        {
            _screenCorner = sr;
            ResizeVideo();  // the same size can come with another corner (another model)
        }

        // Drawn over the screen's square corners; each ring and the one inside it share an outline,
        // so the edges always meet.
        var outer = Squircle(k, k, pw - 2 * k, ph - 2 * k, sr + g + t);
        Titanium.Data = new GeometryGroup
        {
            FillRule = FillRule.EvenOdd,
            Children = { outer, Squircle(k + t, k + t, pw - 2 * (k + t), ph - 2 * (k + t), sr + g) },
        };
        Back.Data = Squircle(k + t, k + t, pw - 2 * (k + t), ph - 2 * (k + t), sr + g);
        Body.Data = new GeometryGroup
        {
            FillRule = FillRule.EvenOdd,
            Children = { Squircle(k + t, k + t, pw - 2 * (k + t), ph - 2 * (k + t), sr + g), Squircle(o, o, sw, sh, sr) },
        };
        Buttons.Data = ButtonGeometry(SideButtons, pw, ph, k, t, 1);
        CameraControl.Data = ButtonGeometry(CameraButton, pw, ph, k, t, 0.45);
        // the glass catching the light where it meets the band
        double e = k + t + 0.4;
        GlassEdge.Data = Squircle(e, e, pw - 2 * e, ph - 2 * e, sr + g - 0.4);
        ShadowShape.Width = pw;
        ShadowShape.Height = ph;
        ShadowShape.Data = Squircle(k, k, pw - 2 * k, ph - 2 * k, sr + g + t);
        PlaceBand(pw, ph, k, t, sr + g + t, ButtonRects(SideButtons, pw, ph, k, t, 1));
        PlaceShadow(pw, ph);
        Rim.Data = Squircle(o - 1, o - 1, sw + 2, sh + 2, sr + 1);  // the 2 px stroke sits just outside the screen
        _glowShape = (k, pw - 2 * k, ph - 2 * k, sr + g + t, sw, sh, sr);  // the titanium band's outline, and the screen
        PlaceBar(pw, k, sr + g + t);
        PlaceFrameInteraction(pw, ph, k, o, sw, sh);
        PlaceGlow();
    }

    /// <summary>Side buttons, standing out from the band by `stand` of k and tucked under it. Sideways, the
    /// phone is drawn turned to the left (its top at the left), so the volume buttons are below.</summary>
    GeometryGroup ButtonGeometry((bool left, double top, double bottom)[] buttons, double pw, double ph, double k, double t, double stand)
    {
        var group = new GeometryGroup();
        foreach (var (x, y, w, h) in ButtonRects(buttons, pw, ph, k, t, stand))
            group.Children.Add(Squircle(x, y, w, h, k * 0.9));
        return group;
    }

    /// <summary>Where those buttons are (DIPs in the phone), as drawn: also for the lit band (MainWindow.Frame.cs).</summary>
    List<(double x, double y, double w, double h)> ButtonRects((bool left, double top, double bottom)[] buttons,
        double pw, double ph, double k, double t, double stand)
    {
        bool sideways = _screenW > _screenH;
        double uw = sideways ? ph : pw, uh = sideways ? pw : ph;  // the upright phone's size
        double bandH = uh - 2 * k;
        double room = k * (1 - stand);  // between the phone's edge and the button
        var rects = new List<(double, double, double, double)>();
        foreach (var (left, top, bottom) in buttons)
        {
            double bw = k * stand + t, x = left ? room : uw - room - bw, y = k + top * bandH, bh = (bottom - top) * bandH;
            // upright (x, y) turned to the left: the top goes left, the left side goes down
            rects.Add(sideways ? (y, uw - x - bw, bh, bw) : (x, y, bw, bh));
        }
        return rects;
    }

    /// <summary>The window's size for the phone's shape: about 85% of the work area's height,
    /// as wide as the phone needs, and never beyond 95% of the work area either way.</summary>
    SizeInt32 PhoneWindowSize(RectInt32 area, double scale)
    {
        var (_, _, dw, dh) = PhoneUnits();
        double chrome = PadH * scale;  // the bar + room above and below the phone
        double h = area.Height * 0.85;
        double w = (h - chrome) * dw / dh + PadW * scale;
        double fit = Math.Min(1.0, Math.Min(area.Width * 0.95 / w, area.Height * 0.95 / h));  // landscape is wide
        return new SizeInt32((int)(w * fit), (int)(h * fit));
    }

    double WindowScale()
    {
        double scale = GetDpiForWindow(WinRT.Interop.WindowNative.GetWindowHandle(this)) / 96.0;
        return scale <= 0 ? 1 : scale;
    }

    /// <summary>A phone-shaped window, centered on the work area.</summary>
    void SizeToPhone()
    {
        var area = DisplayArea.GetFromWindowId(AppWindow.Id, DisplayAreaFallback.Primary).WorkArea;
        double scale = WindowScale();
        var size = PhoneWindowSize(area, scale);
        AppWindow.Resize(size);
        AppWindow.Move(new PointInt32(area.X + (area.Width - size.Width) / 2, area.Y + (area.Height - size.Height) / 2));
        LimitSize(scale);
    }

    /// <summary>The phone turned: turn the window with it, so the phone keeps its size on screen
    /// (the room the phone had across becomes the room it has down, and the other way round),
    /// centered where it was, shrunk to fit the work area of the display it is on.</summary>
    void ReshapeWindow()
    {
        if (AppWindow.Presenter is OverlappedPresenter { State: OverlappedPresenterState.Maximized }) return;
        var area = DisplayArea.GetFromWindowId(AppWindow.Id, DisplayAreaFallback.Primary).WorkArea;
        double scale = WindowScale();
        LimitSize(scale);  // the minimum changes shape too, before resizing under it
        double padW = PadW * scale, padH = PadH * scale;  // beside the phone; title bar + room above and below it
        var now = AppWindow.Size;
        double w = Math.Max(1, now.Height - padH) + padW, h = Math.Max(1, now.Width - padW) + padH;
        double fit = Math.Min(1.0, Math.Min(area.Width * 0.95 / w, area.Height * 0.95 / h));
        var size = new SizeInt32((int)(w * fit), (int)(h * fit));
        int cx = AppWindow.Position.X + now.Width / 2, cy = AppWindow.Position.Y + now.Height / 2;
        int x = Math.Clamp(cx - size.Width / 2, area.X, Math.Max(area.X, area.X + area.Width - size.Width));
        int y = Math.Clamp(cy - size.Height / 2, area.Y, Math.Max(area.Y, area.Y + area.Height - size.Height));
        AppWindow.MoveAndResize(new RectInt32(x, y, size.Width, size.Height));
        PlaceSnapped();
    }

    /// <summary>No smaller than a usable phone, turned sideways
    /// with the phone, enough for the title bar's buttons, Settings,
    /// the setup checklist and a phone you can still tap on; never more than the screen allows.</summary>
    void LimitSize(double scale)
    {
        if (AppWindow.Presenter is not OverlappedPresenter p) return;
        var area = DisplayArea.GetFromWindowId(AppWindow.Id, DisplayAreaFallback.Primary).WorkArea;
        int w = _sideways ? MinLong : MinShort, h = _sideways ? MinShort : MinLong;
        p.PreferredMinimumWidth = Math.Min((int)(w * scale), area.Width);
        p.PreferredMinimumHeight = Math.Min((int)(h * scale), area.Height);
    }

    [System.Runtime.InteropServices.DllImport("user32.dll")]
    static extern uint GetDpiForWindow(IntPtr hwnd);

    // ---------------------------------------------------------------- mouse

    // While an agent drives the phone, this window sends no input: mixing in clicks
    // and keys would confuse it. "Take over" stops the agent.
    bool Active => _live && _playing && _agent != "driving" && !Covered;

    /// <summary>The setup checklist or About is on the phone's screen: input goes to it, not the phone.</summary>
    bool Covered => SetupOverlay.Visibility == Visibility.Visible || AboutOverlay.Visibility == Visibility.Visible
                    || CalibrationOverlay.Visibility == Visibility.Visible;

    (double x, double y) At(PointerRoutedEventArgs e)
    {
        var p = e.GetCurrentPoint(Screen).Position;
        return (p.X / Math.Max(1, Screen.ActualWidth), p.Y / Math.Max(1, Screen.ActualHeight));
    }

    void Surface_PointerMoved(object sender, PointerRoutedEventArgs e)
    {
        if (!Active) return;
        var (x, y) = At(e);
        _conn.Send(new { t = "move", x, y });
    }

    void Surface_PointerPressed(object sender, PointerRoutedEventArgs e)
    {
        Surface.Focus(FocusState.Pointer);
        var props = e.GetCurrentPoint(Surface).Properties;
        if (!props.IsLeftButtonPressed || !Active) return;
        Surface.CapturePointer(e.Pointer);
        _pressed = true;
        var (x, y) = At(e);
        // The server presses where the phone's pointer already hovers: put it here first,
        // or the press lands at the last hover spot and drags over to this one.
        _conn.Send(new { t = "move", x, y });
        _conn.Send(new { t = "down", x, y });
        e.Handled = true;
    }

    void Surface_PointerReleased(object sender, PointerRoutedEventArgs e)
    {
        if (!_pressed) return;
        Lift();
        Surface.ReleasePointerCapture(e.Pointer);
        e.Handled = true;
    }

    void Lift()
    {
        if (!_pressed) return;
        _pressed = false;
        _conn.Send(new { t = "up" });
    }

    // Wheel and touchpad scrolling: the server turns it into a drag (iOS ignores the
    // wheel). One notch is 100 px, as in the browser.
    void Surface_PointerWheelChanged(object sender, PointerRoutedEventArgs e)
    {
        if (e.KeyModifiers.HasFlag(Windows.System.VirtualKeyModifiers.Control))
        {
            e.Handled = true;  // Ctrl + wheel: the window's size, not the phone's scrolling
            ResizeBy(e.GetCurrentPoint(Surface).Properties.MouseWheelDelta);
            return;
        }
        if (Covered) return;  // the checklist or About scrolls
        e.Handled = true;
        if (!Active) return;
        var props = e.GetCurrentPoint(Screen).Properties;
        double px = props.MouseWheelDelta / 120.0 * 100;
        double w = Math.Max(1, Screen.ActualWidth), h = Math.Max(1, Screen.ActualHeight);
        var (x, y) = At(e);
        double dx = props.IsHorizontalMouseWheel ? -px / w : 0;
        double dy = props.IsHorizontalMouseWheel ? 0 : px / h;
        _conn.Send(new { t = "scroll", x, y, dx, dy });
    }

    // ---------------------------------------------------------------- keyboard

    void OnKey(KeyRoutedEventArgs e, bool down)
    {
        if (!Active) return;
        var st = e.KeyStatus;
        uint scan = st.ScanCode;
        bool ext = st.IsExtendedKey;
        if (KeyMap.Modifier(scan, ext) is int bit)
        {
            e.Handled = true;
            if (down && st.WasKeyDown) return;
            _conn.Send(new { t = "mod", bit, down });
            return;
        }
        if (KeyMap.Consumer(scan, ext) is string key)
        {
            e.Handled = true;
            if (!(down && st.WasKeyDown)) _conn.Send(new { t = "consumer", key, down });
            return;
        }
        if (KeyMap.Usage(scan, ext) is not int usage) return;
        e.Handled = true;
        if (down && st.WasKeyDown) return;  // the phone repeats keys itself
        _conn.Send(new { t = "key", usage, down });
    }

    // ---------------------------------------------------------------- agents

    void ShowAgent()
    {
        bool driving = _agent == "driving";
        if (!driving) AgentDot.Visibility = Visibility.Collapsed;
        _glow?.SetAgent(_agent);
        // the glow says it (AgentGlow); the plain ring only when the glow is off or can't run
        Rim.Visibility = driving && !GlowShowing ? Visibility.Visible : Visibility.Collapsed;
        AgentPanel.Visibility = _agent == "idle" ? Visibility.Collapsed : Visibility.Visible;
        TakeoverLabel.Text = driving ? "Agent in control" : _agent == "paused" ? "You took over" : "You're in control";
        TakeoverAction.Text = driving ? "Take over" : _agent == "paused" ? "Resume agent" : "";
        TakeoverAction.Visibility = _agent == "idle" ? Visibility.Collapsed : Visibility.Visible;
        AgentLight.Fill = new SolidColorBrush(driving
            ? Windows.UI.Color.FromArgb(255, 10, 132, 255)
            : _agent == "paused" ? Windows.UI.Color.FromArgb(255, 255, 159, 10) : Windows.UI.Color.FromArgb(255, 48, 209, 88));
        AgentWhat.Text = driving ? _agentAction : "";
    }

    void ShowAgentDot(double x, double y, bool down)
    {
        Canvas.SetLeft(AgentDot, x * Screen.ActualWidth - AgentDot.Width / 2);
        Canvas.SetTop(AgentDot, y * Screen.ActualHeight - AgentDot.Height / 2);
        AgentDot.Fill = new SolidColorBrush(down
            ? Windows.UI.Color.FromArgb(200, 10, 132, 255) : Windows.UI.Color.FromArgb(100, 255, 255, 255));
        AgentDot.Visibility = Visibility.Visible;
        GlowPointer(x, y, down);
    }

    async void Takeover_Click(object sender, RoutedEventArgs e)
    {
        if (_agent == "idle") return;
        await _conn.PostAsync("api/agent/takeover", new { take = _agent != "paused" });
    }

    // ---------------------------------------------------------------- requests from agents

    ContentDialog? _ask;
    string? _askId;
    string _askKind = "unlock";
    DispatcherQueueTimer? _askTimer;

    async Task ShowAsk(string? id, string kind, string? action, double? deadline)
    {
        if (id == null || id == _askId) return;
        HideAsk(_askKind);
        _askId = id;
        _askKind = kind;
        var dialog = new ContentDialog
        {
            XamlRoot = Root.XamlRoot,
            RequestedTheme = ElementTheme.Dark,
            DefaultButton = ContentDialogButton.Primary,
        };
        if (kind == "session" && deadline is double dl)
        {
            dialog.PrimaryButtonText = "Allow now";
            dialog.CloseButtonText = "Cancel";
            dialog.Content = action ?? "";
            void Tick()
            {
                var left = Math.Max(0, Math.Ceiling(dl - DateTimeOffset.UtcNow.ToUnixTimeMilliseconds() / 1000.0));
                dialog.Title = $"Agent taking control in {left} s";
            }
            Tick();
            _askTimer = _ui.CreateTimer();
            _askTimer.Interval = TimeSpan.FromMilliseconds(250);
            _askTimer.Tick += (_, _) => Tick();
            _askTimer.Start();
        }
        else
        {
            dialog.Title = kind == "session" ? "Agent requests control" : "Agent requests an unlock";
            dialog.Content = kind == "session" ? action ?? "" : "It types your saved passcode once.";
            dialog.PrimaryButtonText = kind == "session" ? "Allow" : "Unlock";
            dialog.CloseButtonText = "Deny";
        }
        _ask = dialog;
        bool answered = false;
        dialog.PrimaryButtonClick += (_, _) => answered = true;
        dialog.CloseButtonClick += (_, _) => answered = true;
        var result = await dialog.ShowAsync();
        if (_ask == dialog)
        {
            _askTimer?.Stop();
            _ask = null;
            if (answered)
                await _conn.PostAsync($"api/{kind}/answer", new { id, approve = result == ContentDialogResult.Primary });
            _askId = null;
        }
    }

    void HideAsk(string kind)
    {
        if (_ask == null || kind != _askKind) return;
        var dialog = _ask;
        _ask = null;
        _askId = null;
        _askTimer?.Stop();
        dialog.Hide();
    }
}
