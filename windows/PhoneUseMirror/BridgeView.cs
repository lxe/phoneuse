using System.Net.Http;
using System.Text.Json.Nodes;
using Microsoft.UI.Dispatching;
using Microsoft.UI.Xaml;
using Microsoft.UI.Xaml.Controls;
using Microsoft.UI.Xaml.Media;

namespace PhoneUseMirror;

/// <summary>
/// The Bluetooth bridge (an ESP32, a LilyGO T-Display, as the iPhone's keyboard and mouse
/// over Bluetooth Classic; phoneuse/bridge.py), shown in the main window over the phone as
/// About is: its state, PC or bridge for input, its Wi-Fi network, flashing its firmware over
/// USB, forgetting it; with none set up, setting one up or using one another PC set up. The
/// same routes and steps as the Linux app (app.py BridgeSetup, FirmwareDialog):
///   GET /api/bridge, POST find, pair, networks, join, adopt, adopt/wait, cancel, use,
///   disconnect; DELETE /api/bridge; GET /api/bridge/firmware, POST firmware/install.
/// </summary>
public sealed class BridgeView : UserControl
{
    const string DriverUrl = "https://www.wch-ic.com/downloads/CH343SER_EXE.html";

    readonly MirrorConnection _conn;
    readonly StackPanel _body = new() { Spacing = 8, Padding = new Thickness(20, 8, 20, 28), MaxWidth = 640 };
    readonly DispatcherQueueTimer _poll;
    bool _loading, _dialogUp;
    public event Action? Changed;  // the bridge's setup changed: the window refreshes what it shows

    public BridgeView(MirrorConnection conn)
    {
        _conn = conn;
        Content = new ScrollViewer { Content = _body };
        _poll = DispatcherQueue.GetForCurrentThread().CreateTimer();
        _poll.Interval = TimeSpan.FromSeconds(3);
        _poll.Tick += async (_, _) => { if (!_dialogUp) await Refresh(); };
        Header(null);
        _body.Children.Add(Caption("Checking for a bridge…"));
    }

    public async void Start()
    {
        _poll.Start();
        await Refresh();
    }

    public void Stop() => _poll.Stop();

    /// <summary>Opens the setup dialog (the first-time setup's "A Bluetooth bridge"); with a bridge
    /// already set up, its page is enough.</summary>
    public async void BeginSetup()
    {
        var j = await _conn.CallAsync(HttpMethod.Get, "api/bridge", null, 15);
        if (j?["saved"] == null && !_dialogUp) await Setup();
    }

    // ------------------------------------------------------------------ the page

    async Task Refresh()
    {
        if (_loading) return;
        _loading = true;
        try { Show(await _conn.CallAsync(HttpMethod.Get, "api/bridge", null, 15)); }
        finally { _loading = false; }
    }

    void Header(string? sub)
    {
        _body.Children.Add(new TextBlock { Text = "Bluetooth Bridge", Style = Style("TitleTextBlockStyle") });
        _body.Children.Add(Caption(sub ?? "A LilyGO T-Display can be the iPhone's keyboard and mouse instead of this PC: " +
            "over Bluetooth Classic, as on Linux, so the pointer is quicker than over this PC's Bluetooth LE. " +
            "This PC sends it input over Wi-Fi."));
    }

    void Show(JsonObject? j)
    {
        _body.Children.Clear();
        Header(null);
        if (j == null || j["_failed"] != null)
        {
            _body.Children.Add(Caption("phoneuse isn't answering."));
            return;
        }
        var saved = j["saved"] as JsonObject;
        if (saved == null)
        {
            ShowNone(j["nearby"] as JsonArray);
            return;
        }
        var st = j["status"] as JsonObject ?? new JsonObject();
        string id = (string?)saved["id"] ?? "";
        string tail = id.Length > 4 ? id[^4..] : id;
        bool offline = st["error"] != null;
        string? other = (string?)j["other_phone"];

        // the bridge and its phone
        string state, sub = (string?)saved["ip"] ?? "";
        Brush? color = null;
        Button? disconnect = null;
        if (offline)
        {
            state = "Offline";
            sub = (string?)st["error"] ?? sub;
            color = Red;
        }
        else if (other != null)  // connected, but not to the phone that mirrors: nothing goes to it
        {
            state = other;
            color = Orange;
            sub = $"Not the mirroring {(string?)j["mirroring_phone"] ?? "iPhone"}";
            disconnect = Btn("Disconnect", async () => await Act("api/bridge/disconnect"));
        }
        else if ((bool?)st["connected"] == true)
        {
            state = (string?)st["phone"] ?? "iPhone";
            disconnect = Btn("Disconnect", async () => await Act("api/bridge/disconnect"));
        }
        else if ((bool?)st["standby"] == true)  // no phone mirrors: the bridge stays off the iPhone until one does
        {
            state = "Waiting for mirroring";
            sub = "Start Screen Mirroring to connect";
        }
        else
        {
            state = "iPhone not connected";
            color = Orange;
            sub = "On the iPhone: Settings › Bluetooth › Phone Use Bridge";
        }
        var stateText = new TextBlock { Text = state, VerticalAlignment = VerticalAlignment.Center, Opacity = color == null ? 0.75 : 1 };
        if (color != null) stateText.Foreground = color;
        var right = new StackPanel { Orientation = Orientation.Horizontal, Spacing = 12 };
        right.Children.Add(stateText);
        if (disconnect != null) right.Children.Add(disconnect);
        _body.Children.Add(Row($"Bridge {tail}", sub, right));

        // input: this PC's Bluetooth LE, or the bridge (this PC's LE keyboard closes)
        bool use = (bool?)saved["use"] ?? true;
        var sw = new ToggleSwitch
        {
            OnContent = "On", OffContent = "Off", IsOn = !use || offline, IsEnabled = !offline,
            MinWidth = 0,
        };
        sw.Toggled += async (_, _) =>
        {
            sw.IsEnabled = false;
            var r = await _conn.PostAsync("api/bridge/use", new { on = !sw.IsOn });
            var daemon = (string?)r?["daemon"];
            if (r?["error"] != null || (daemon != null && daemon.StartsWith("err")))
                await Message("Couldn't switch", (string?)r?["error"] ?? daemon![4..]);
            Changed?.Invoke();
            await Refresh();
        };
        string daemonStatus = (string?)j["daemon"] ?? "";
        _body.Children.Add(Row("Use this PC's Bluetooth",
            offline ? "The bridge isn't answering, so this PC's Bluetooth carries input."
            : use ? (daemonStatus.Contains(" le=closed") ? "This PC's Bluetooth keyboard is closed while the bridge carries input."
                                                         : "The bridge carries input; this PC's Bluetooth keyboard closes in a moment.")
            : "This PC's Bluetooth carries input. The bridge stays connected to the network.", sw));

        _body.Children.Add(Row("Wi-Fi Network", (string?)saved["ssid"] ?? "",
            Btn("Change…", () => _ = Setup(change: true), enabled: !offline)));
        FirmwareRow();
        int others = Math.Max(0, ((int?)(double?)st["hosts"] ?? 1) - 1);  // other PCs the bridge lets in (firmware 0.3+)
        _body.Children.Add(Row("Forget Bridge",
            others > 0 ? $"Other computers keep using it ({others})" : "Resets the bridge and disconnects it from your iPhone",
            Btn("Forget…", () => _ = Forget(others))));
    }

    void ShowNone(JsonArray? nearby)
    {
        _body.Children.Add(Caption("No bridge is set up on this PC."));
        foreach (var n in nearby?.OfType<JsonObject>() ?? [])
        {
            string id = (string?)n["id"] ?? "";
            string phone = (string?)n["phone"] ?? "No iPhone";
            _body.Children.Add(Row($"Bridge {(id.Length > 4 ? id[^4..] : id)}", $"{phone} · {(string?)n["ssid"] ?? (string?)n["ip"]}",
                Btn("Use…", () => _ = Setup(adopt: n))));
        }
        _body.Children.Add(Row("Set Up Bridge", "Find a bridge near this PC over Bluetooth and put it on your Wi-Fi",
            Btn("Set Up…", () => _ = Setup(), accent: true)));
        FirmwareRow();
    }

    void FirmwareRow() => _body.Children.Add(Row("Flash LilyGO T-Display",
        "Install the bridge firmware over USB. The first time, Phone Use downloads it and the flashing tool (about 50 MB); " +
        "they're removed again once it's flashed.",
        Btn("Flash…", () => _ = Flash())));

    async Task Act(string path)
    {
        var r = await _conn.PostAsync(path, new { });
        if (r?["error"] != null) await Message("Bridge", (string?)r["error"] ?? "");
        await Refresh();
    }

    async Task Forget(int others)
    {
        var d = Dialog(others > 0 ? "Forget the bridge?" : "Forget and reset the bridge?");
        d.Content = new TextBlock
        {
            TextWrapping = TextWrapping.Wrap,
            Text = others > 0 ? "This PC stops using it; the other computers keep using it."
                              : "The bridge forgets its Wi-Fi network and your iPhone, and you set it up again to use it. " +
                                "On the iPhone, forget Phone Use Bridge in Bluetooth settings too.",
        };
        d.PrimaryButtonText = "Forget";
        d.CloseButtonText = "Cancel";
        d.DefaultButton = ContentDialogButton.Close;
        if (await ShowDialog(d) != ContentDialogResult.Primary) return;
        var r = await _conn.DeleteAsync("api/bridge");
        if (r?["error"] != null) await Message("Bridge", (string?)r["error"] ?? "");
        else if (r?["reset"] is JsonValue v && !(bool)v && r["why"] != null)
            await Message("Forgotten on this PC", $"The bridge didn't reset: {(string?)r["why"]}. Hold its lower button for 3 seconds to reset it.");
        Changed?.Invoke();
        await Refresh();
    }

    // ------------------------------------------------------------------ set up (BridgeSetup)

    /// <summary>Set up a bridge (find it over Bluetooth, compare codes, pick Wi-Fi, wait for the
    /// iPhone), use one another PC set up (adopt), or move this one to another network (change).</summary>
    async Task Setup(bool change = false, JsonObject? adopt = null)
    {
        var w = new Wizard(this, change ? "Wi-Fi Network" : adopt != null ? "Use Bridge" : "Set Up Bridge");

        async Task Find()
        {
            w.Wait("Looking for the bridge…");
            var j = await _conn.CallAsync(HttpMethod.Post, "api/bridge/find", new { }, 30);
            if (w.Ended) return;
            if (j?["error"] != null) { w.Error(((string?)j["error"] ?? ""), () => _ = Find()); return; }
            var ble = (j?["bridges"] as JsonArray)?.OfType<JsonObject>().ToList() ?? [];
            var near = (j?["nearby"] as JsonArray)?.OfType<JsonObject>().ToList() ?? [];
            if (ble.Count == 0 && near.Count == 0)
            {
                w.Show("No bridge found. Hold the bridge's lower button for 3 seconds, then try again.",
                    ("Try Again", 1, () => _ = Find()),
                    ("Flash LilyGO T-Display", 0, () => { w.Close(); _ = Flash(); }));
                return;
            }
            var rows = new List<(string, string?, bool, Action)>();
            foreach (var nb in near)  // set up already, on this network: ask it to let this PC in
            {
                string id = (string?)nb["id"] ?? "";
                rows.Add(($"Bridge {(id.Length > 4 ? id[^4..] : id)}",
                    $"{(string?)nb["phone"] ?? "No iPhone"} · {(string?)nb["ssid"] ?? (string?)nb["ip"]}", false, () => _ = Adopt(nb)));
            }
            foreach (var b in ble)
            {
                string address = (string?)b["address"] ?? "";
                rows.Add(((string?)b["name"] ?? "Phone Use Bridge", address, false, () => _ = Pair(address)));
            }
            w.List("Choose your bridge.", rows);
        }

        async Task Adopt(JsonObject nb)
        {
            w.Wait("Asking the bridge…");
            var j = await _conn.CallAsync(HttpMethod.Post, "api/bridge/adopt", new { id = (string?)nb["id"] }, 15);
            if (w.Ended) return;
            if (j?["error"] != null || j?["code"] == null) { w.Error((string?)j?["error"] ?? "No answer", () => _ = Adopt(nb)); return; }
            w.Show($"If the bridge shows {(string?)j["name"]} and the same code, press either of its buttons.",
                big: (string?)j["code"], spinner: true);
            while (!w.Ended)
            {
                var r = await _conn.CallAsync(HttpMethod.Post, "api/bridge/adopt/wait", new { }, 40);
                if (w.Ended) return;
                if (r?["error"] != null) { w.Error(((string?)r["error"] ?? ""), () => _ = Adopt(nb)); return; }
                if ((bool?)r?["waiting"] == true) continue;
                w.Show("This PC now uses the bridge.", ("Done", 1, w.Close));
                return;
            }
        }

        async Task Pair(string address)
        {
            w.Wait("Connecting…");
            var j = await _conn.CallAsync(HttpMethod.Post, "api/bridge/pair", new { address }, 40);
            if (w.Ended) return;
            if (j?["error"] != null || j?["code"] == null) { w.Error((string?)j?["error"] ?? "No answer", () => _ = Find()); return; }
            w.Show("Does the bridge show the same code?", big: (string?)j["code"],
                actions: [("Codes Match", 1, () => _ = Networks()), ("Don't Match", 2, () => _ = Find())]);
        }

        async Task Networks()
        {
            w.Wait("Looking for Wi-Fi…");
            var j = await _conn.CallAsync(HttpMethod.Post, "api/bridge/networks", new { }, 30);
            if (!w.Ended && j?["error"] == null && (j?["networks"] as JsonArray)?.Count is null or 0)  // a scan can come back empty: once more
                j = await _conn.CallAsync(HttpMethod.Post, "api/bridge/networks", new { }, 30);
            if (w.Ended) return;
            if (j?["error"] != null) { w.Error(((string?)j["error"] ?? ""), () => _ = Networks()); return; }
            var rows = new List<(string, string?, bool, Action)>();
            foreach (var n in (j?["networks"] as JsonArray)?.OfType<JsonObject>() ?? [])
            {
                string ssid = (string?)n["s"] ?? "";
                if (ssid.Length == 0) continue;
                bool locked = (bool?)n["l"] ?? false;
                rows.Add((ssid, null, locked, () => { if (locked) Password(ssid); else _ = Join(ssid, ""); }));
            }
            rows.Add(("Other Network…", null, false, Other));
            if (rows.Count > 1) w.List("Choose the same 2.4 GHz network as this PC.", rows);
            else w.List("No networks found.", rows, ("Try Again", 0, () => _ = Networks()));
        }

        void Password(string ssid)
        {
            var pw = new PasswordBox { Header = $"Password for {ssid}" };
            w.Form([pw], ("Join", 1, () => _ = Join(ssid, pw.Password)));
            pw.KeyDown += (_, e) => { if (e.Key == Windows.System.VirtualKey.Enter) _ = Join(ssid, pw.Password); };
            pw.Loaded += (_, _) => pw.Focus(FocusState.Programmatic);
        }

        void Other()
        {
            var name = new TextBox { Header = "Network Name" };
            var pw = new PasswordBox { Header = "Password" };
            w.Form([name, pw], ("Join", 1, () => { if (name.Text.Length > 0) _ = Join(name.Text, pw.Password); }));
            name.Loaded += (_, _) => name.Focus(FocusState.Programmatic);
        }

        async Task Join(string ssid, string password)
        {
            w.Wait($"Joining {ssid}…");
            var j = await _conn.CallAsync(HttpMethod.Post, "api/bridge/join", new { ssid, password }, 60);
            if (w.Ended) return;
            if (j?["error"] != null) { w.Error(((string?)j["error"] ?? ""), () => _ = Networks()); return; }
            if (change)
            {
                w.Show($"The bridge is on {ssid}.", ("Done", 1, w.Close));
                return;
            }
            w.Wait("On the iPhone, open Settings › Bluetooth and tap Phone Use Bridge. If it won't connect, forget it there and tap it again.");
            while (!w.Ended)
            {
                await Task.Delay(2000);
                if (w.Ended) return;
                var b = await _conn.CallAsync(HttpMethod.Get, "api/bridge", null, 15);
                var st = b?["status"] as JsonObject;
                if ((bool?)st?["connected"] == true)
                {
                    w.Title = "Bridge Ready";
                    w.Show($"Connected to {(string?)st!["phone"] ?? "the iPhone"}.", ("Done", 1, w.Close));
                    return;
                }
            }
        }

        _ = change ? Networks() : adopt != null ? Adopt(adopt) : Find();
        _dialogUp = true;
        await ShowDialog(w.Dialog);
        _dialogUp = false;
        w.Ended = true;
        if (!change) await _conn.PostAsync("api/bridge/cancel", new { });
        Changed?.Invoke();
        await Refresh();
    }

    // ------------------------------------------------------------------ flash (FirmwareDialog)

    /// <summary>Wait for the board on USB, confirm (flashing erases it), flash with progress;
    /// the first time the server downloads the firmware and esptool, and removes them after.</summary>
    async Task Flash()
    {
        var w = new Wizard(this, "Flash LilyGO T-Display");
        bool setUp = false;

        async Task Follow()
        {
            var bar = new ProgressBar { Minimum = 0, Maximum = 1, Margin = new Thickness(12, 0, 12, 0) };
            while (!w.Ended)
            {
                var f = await _conn.GetAsync("api/bridge/firmware");
                if (w.Ended) return;
                if (f?["error"] is JsonValue err && (bool?)f["busy"] != true)
                {
                    w.Show($"Couldn't install the firmware: {(string?)err}", ("Try Again", 0, () => _ = WaitForUsb()));
                    return;
                }
                if ((bool?)f?["done"] == true && (bool?)f?["busy"] != true)
                {
                    w.Show("Flashed. Set the bridge up to use it. On the iPhone, forget Phone Use Bridge in Bluetooth " +
                           "settings first if it's listed.", ("Set Up Bridge", 1, () => { setUp = true; w.Close(); }));
                    return;
                }
                double p = (double?)f?["progress"] ?? 0;
                bar.IsIndeterminate = p <= 0;
                bar.Value = p;
                w.Show((string?)f?["step"] ?? "Flashing…", bar: bar);
                await Task.Delay(500);
            }
        }

        async Task WaitForUsb()
        {
            while (!w.Ended)
            {
                var f = await _conn.GetAsync("api/bridge/firmware");
                if (w.Ended) return;
                if ((bool?)f?["busy"] == true) { await Follow(); return; }  // already flashing (from before)
                var port = (string?)f?["port"];
                if (port != null)
                {
                    string version = (string?)f!["version"] ?? "";
                    bool ready = (bool?)f["ready"] ?? false;
                    w.Show($"Found a board on {port}. Flashing installs bridge firmware {version} and erases the board: " +
                           "the bridge it was loses its Wi-Fi and its iPhone pairing, and you set it up again." +
                           (ready ? "" : " Phone Use downloads the firmware and the flashing tool first (about 50 MB)."),
                        ("Erase and Flash", 2, () => _ = Install()), ("Cancel", 0, w.Close));
                    return;
                }
                if ((bool?)f?["driver_missing"] == true)
                    w.Show("The T-Display is plugged in but Windows has no driver for its USB chip (a WCH CH9102), so it " +
                           "has no COM port. Install WCH's CH343 driver, then unplug the board and plug it in again.",
                        spinner: true, link: ("Download the driver from WCH", DriverUrl));
                else
                    w.Show("Plug the T-Display into this PC with a USB cable (one that carries data, not only power).", spinner: true);
                await Task.Delay(1000);
            }
        }

        async Task Install()
        {
            w.Dialog.CloseButtonText = "Close";
            w.Wait("Starting…");
            var r = await _conn.PostAsync("api/bridge/firmware/install", new { });
            if (w.Ended) return;
            if (r?["error"] != null) { w.Show((string?)r["error"] ?? "", ("Try Again", 0, () => _ = WaitForUsb())); return; }
            await Follow();
        }

        _ = WaitForUsb();
        _dialogUp = true;
        await ShowDialog(w.Dialog);
        _dialogUp = false;
        w.Ended = true;
        Changed?.Invoke();
        await Refresh();
        if (setUp) await Setup();
    }

    // ------------------------------------------------------------------ pieces

    sealed class Wizard
    {
        readonly StackPanel _box = new() { Spacing = 14, Width = 360 };
        public readonly ContentDialog Dialog;
        public bool Ended;

        public Wizard(BridgeView view, string title)
        {
            Dialog = view.Dialog(title);
            Dialog.CloseButtonText = "Cancel";
            Dialog.Content = new ScrollViewer { Content = _box, MaxHeight = 460 };
            Dialog.Closed += (_, _) => Ended = true;
        }

        public string Title { set => Dialog.Title = value; }

        public void Close() => Dialog.Hide();

        void Clear() => _box.Children.Clear();

        static TextBlock Text(string text) => new()
        {
            Text = text, TextWrapping = TextWrapping.Wrap, TextAlignment = TextAlignment.Center, Opacity = 0.8,
            HorizontalAlignment = HorizontalAlignment.Center, IsTextSelectionEnabled = true,
        };

        // (label, kind, action): kind 1 accent, 2 destructive, 0 plain; one column of equal buttons
        void Actions((string, int, Action)[] actions)
        {
            foreach (var (label, kind, fn) in actions)
            {
                var b = new Button { Content = label, Width = 240, HorizontalAlignment = HorizontalAlignment.Center };
                if (kind == 1) b.Style = BridgeView.Style("AccentButtonStyle");
                if (kind == 2) b.Foreground = Red;
                b.Click += (_, _) => fn();
                _box.Children.Add(b);
            }
        }

        public void Wait(string text) => Show(text, spinner: true);

        public void Error(string text, Action retry) => Show(text, ("Try Again", 0, retry));

        public void Show(string text, params (string, int, Action)[] actions) => Show(text, null, false, null, null, actions);

        public void Show(string text, string? big = null, bool spinner = false, ProgressBar? bar = null,
                         (string, string)? link = null, params (string, int, Action)[] actions)
        {
            Clear();
            if (big != null)
                _box.Children.Add(new TextBlock
                {
                    Text = big, Style = BridgeView.Style("TitleLargeTextBlockStyle"), HorizontalAlignment = HorizontalAlignment.Center,
                });
            if (spinner) _box.Children.Add(new ProgressRing { IsActive = true, Width = 32, Height = 32 });
            if (bar != null) _box.Children.Add(bar);
            _box.Children.Add(Text(text));
            if (link is { } l)
                _box.Children.Add(new HyperlinkButton { Content = l.Item1, NavigateUri = new Uri(l.Item2), HorizontalAlignment = HorizontalAlignment.Center });
            Actions(actions);
        }

        public void List(string caption, List<(string title, string? sub, bool locked, Action fn)> rows,
                         params (string, int, Action)[] actions)
        {
            Clear();
            _box.Children.Add(new TextBlock { Text = caption, TextWrapping = TextWrapping.Wrap, Opacity = 0.8 });
            var list = new StackPanel { Spacing = 4 };
            foreach (var (title, sub, locked, fn) in rows)
            {
                var col = new StackPanel();
                var head = new StackPanel { Orientation = Orientation.Horizontal, Spacing = 8 };
                if (locked) head.Children.Add(new FontIcon { Glyph = "", FontSize = 12, Opacity = 0.7 });  // a lock
                head.Children.Add(new TextBlock { Text = title });
                col.Children.Add(head);
                if (sub != null) col.Children.Add(new TextBlock { Text = sub, Opacity = 0.6, FontSize = 12 });
                var b = new Button
                {
                    Content = col, HorizontalAlignment = HorizontalAlignment.Stretch, HorizontalContentAlignment = HorizontalAlignment.Left,
                    Padding = new Thickness(12, 8, 12, 8),
                };
                b.Click += (_, _) => fn();
                list.Children.Add(b);
            }
            _box.Children.Add(list);
            Actions(actions);
        }

        public void Form(Control[] fields, params (string, int, Action)[] actions)
        {
            Clear();
            foreach (var f in fields) _box.Children.Add(f);
            Actions(actions);
        }
    }

    static readonly Brush Red = new SolidColorBrush(Windows.UI.Color.FromArgb(255, 0xFF, 0x45, 0x3A));
    static readonly Brush Orange = new SolidColorBrush(Windows.UI.Color.FromArgb(255, 0xFF, 0x9F, 0x0A));

    static Style Style(string key) => (Style)Application.Current.Resources[key];

    static TextBlock Caption(string text) => new()
    {
        Text = text, TextWrapping = TextWrapping.Wrap, Opacity = 0.7, Style = Style("CaptionTextBlockStyle"),
    };

    static Button Btn(string label, Action fn, bool accent = false, bool enabled = true)
    {
        var b = new Button { Content = label, IsEnabled = enabled, VerticalAlignment = VerticalAlignment.Center };
        if (accent) b.Style = Style("AccentButtonStyle");
        b.Click += (_, _) => fn();
        return b;
    }

    /// <summary>A settings row: a title and a line under it, and a control on the right.</summary>
    static Border Row(string title, string sub, FrameworkElement right)
    {
        var g = new Grid { ColumnSpacing = 16 };
        g.ColumnDefinitions.Add(new ColumnDefinition { Width = new GridLength(1, GridUnitType.Star) });
        g.ColumnDefinitions.Add(new ColumnDefinition { Width = GridLength.Auto });
        var col = new StackPanel { Spacing = 2, VerticalAlignment = VerticalAlignment.Center };
        col.Children.Add(new TextBlock { Text = title, Style = Style("BodyStrongTextBlockStyle") });
        if (sub.Length > 0)
            col.Children.Add(new TextBlock { Text = sub, TextWrapping = TextWrapping.Wrap, Opacity = 0.7, Style = Style("CaptionTextBlockStyle") });
        g.Children.Add(col);
        right.VerticalAlignment = VerticalAlignment.Center;
        Grid.SetColumn(right, 1);
        g.Children.Add(right);
        return new Border
        {
            Child = g, Padding = new Thickness(16, 12, 16, 12), CornerRadius = new CornerRadius(8),
            Background = (Brush)Application.Current.Resources["CardBackgroundFillColorDefaultBrush"],
            BorderBrush = (Brush)Application.Current.Resources["CardStrokeColorDefaultBrush"],
            BorderThickness = new Thickness(1),
        };
    }

    ContentDialog Dialog(string title) => new()
    {
        XamlRoot = XamlRoot,
        RequestedTheme = ElementTheme.Dark,
        Title = title,
    };

    async Task<ContentDialogResult> ShowDialog(ContentDialog d)
    {
        try { return await d.ShowAsync(); }
        catch (Exception) { return ContentDialogResult.None; }  // another dialog is up
    }

    async Task Message(string title, string text)
    {
        var d = Dialog(title);
        d.Content = new TextBlock { Text = text, TextWrapping = TextWrapping.Wrap, IsTextSelectionEnabled = true };
        d.CloseButtonText = "OK";
        await ShowDialog(d);
    }
}
