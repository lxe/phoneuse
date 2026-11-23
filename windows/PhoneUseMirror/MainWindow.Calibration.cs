using System.Text.Json.Nodes;
using Microsoft.UI.Xaml;
using Microsoft.UI.Xaml.Controls;
using Microsoft.UI.Xaml.Media;
using Microsoft.UI.Xaml.Media.Animation;

namespace PhoneUseMirror;

/// <summary>
/// The pointer's calibration (calibrate.py): Phone Use measures how far a mouse report moves
/// this phone's pointer, and it measures an upright phone only. The server's "state" carries
/// where that stands ("calibration": waiting_upright, running, failed, done…). While it waits
/// or runs, a card over the phone says so (turn the phone upright; leave it alone); a hidden
/// window says it from the tray. The calibration flow (Settings › Calibrate the pointer, or the
/// setup checklist) walks through it: AssistiveTouch, upright, measure.
/// </summary>
public sealed partial class MainWindow
{
    JsonObject? _calibration;   // the server's "calibration", or null
    long _calibrationSeen;      // when it arrived (Environment.TickCount64): its "age" was as of then
    string? _calibrationHidden; // the card the owner dismissed ("Later"): hidden until the state changes
    string? _calibrationBalloon;// the state the tray last told about
    string? _calibrationError;  // the server refused to start (not mirroring, no Bluetooth)
    bool _calibrationStarting;
    Storyboard? _cardTurn, _flowTurn;

    string? CalState => (string?)_calibration?["state"];

    double CalAge => ((double?)_calibration?["age"] ?? 0) + (Environment.TickCount64 - _calibrationSeen) / 1000.0;

    string CalKey => $"{CalState}|{(string?)_calibration?["phone"]}|{(CalState == "failed" ? (string?)_calibration?["message"] : "")}";

    /// <summary>From OnState: the server's "calibration".</summary>
    void OnCalibration(JsonObject m)
    {
        var c = m["calibration"] as JsonObject;
        string before = $"{CalState}|{(int?)_calibration?["step"]}";
        _calibration = c?.DeepClone() as JsonObject;
        _calibrationSeen = Environment.TickCount64;
        if (before != $"{CalState}|{(int?)_calibration?["step"]}") _calibrationError = null;
        ShowCalibration();
    }

    /// <summary>The card over the phone, the flow (if open) and the tray's word, for the current state.</summary>
    void ShowCalibration()
    {
        if (CalibrationOverlay.Visibility == Visibility.Visible) DrawCalibrationFlow();
        string? st = CalState;
        // the picture turned upright before the server noticed
        if (st == "waiting_upright" && _frameSideways == false) st = "running";
        bool show = _calibration != null && _live && CalKey != _calibrationHidden && !Covered && (
            st is "waiting_upright" or "running" || (st == "failed" && CalAge < 600) || (st == "done" && CalAge < 8));
        CalibrationCard.Visibility = show ? Visibility.Visible : Visibility.Collapsed;
        TellTray();
        if (!show)
        {
            _cardTurn?.Stop();
            return;
        }
        string phone = (string?)_calibration?["phone"] ?? "the iPhone";
        (CalibrationTitle.Text, CalibrationText.Text) = st switch
        {
            "waiting_upright" => ("Turn the iPhone upright",
                $"Phone Use measures {phone}'s pointer only in portrait. It starts as soon as the phone is upright."),
            "running" => ("Measuring the pointer", "About 13 seconds. Keep the iPhone upright and leave it alone."),
            "failed" => ("Calibration didn't work", ((string?)_calibration?["message"] ?? "") + " Keep the iPhone upright and try again."),
            _ => ("Pointer calibrated", "Drags and swipes now go the right distance on this phone."),
        };
        CalibrationIcon.Glyph = st == "done" ? "" : st == "failed" ? "" : "";  // check, error, phone
        CalibrationProgress.Visibility = st == "running" ? Visibility.Visible : Visibility.Collapsed;
        CalibrationProgress.Maximum = (int?)_calibration?["steps"] ?? 7;
        CalibrationProgress.Value = CalState == "running" ? (int?)_calibration?["step"] ?? 0 : 0;
        CalibrationProgress.IsIndeterminate = CalState != "running";
        CalibrationRetry.Visibility = st == "failed" ? Visibility.Visible : Visibility.Collapsed;
        CalibrationLater.Visibility = st is "waiting_upright" or "failed" ? Visibility.Visible : Visibility.Collapsed;
        CalibrationLater.Content = st == "failed" ? "Dismiss" : "Later";
        _cardTurn ??= TurnAnimation(CalibrationTurn);
        if (st == "waiting_upright") _cardTurn.Begin();
        else { _cardTurn.Stop(); CalibrationTurn.Angle = 0; }
        if (CalState == "done")  // the card goes by itself
        {
            var t = _ui.CreateTimer();
            t.Interval = TimeSpan.FromSeconds(Math.Max(0.1, 8.1 - CalAge));
            t.IsRepeating = false;
            t.Tick += (_, _) => ShowCalibration();
            t.Start();
        }
    }

    /// <summary>A hidden window tells from the tray once when the phone has to be turned upright.</summary>
    void TellTray()
    {
        string? st = CalState;
        if (st == _calibrationBalloon) return;
        _calibrationBalloon = st;
        if (!_hidden || _tray == null) return;
        if (st == "waiting_upright" && _frameSideways != false)
            _tray.Balloon("Turn the iPhone upright", "Phone Use measures the phone's pointer in portrait only. It starts as soon as the phone is upright.");
        else if (st == "failed")
            _tray.Balloon("Pointer calibration didn't work", "Open Phone Use to try again with the iPhone upright.");
    }

    void CalibrationLater_Click(object sender, RoutedEventArgs e)
    {
        _calibrationHidden = CalKey;
        ShowCalibration();
    }

    async void CalibrationRetry_Click(object sender, RoutedEventArgs e) => await StartCalibration();

    /// <summary>Settings › Calibrate the pointer.</summary>
    void CalibrateGuide_Click(object sender, RoutedEventArgs e)
    {
        HideSettings();
        ShowCalibrationFlow();
    }

    async Task StartCalibration()
    {
        _calibrationStarting = true;
        _calibrationError = null;
        _calibrationHidden = null;
        DrawCalibrationFlow();
        var r = await _conn.PostAsync("api/calibrate", new { });
        _calibrationStarting = false;
        if (r?["error"] != null) _calibrationError = (string?)r["error"];
        else if (r?["calibration"] is JsonObject c) OnCalibration(new JsonObject { ["calibration"] = c.DeepClone() });
        ShowCalibration();
        DrawCalibrationFlow();
    }

    // ------------------------------------------------------------------ the flow

    void ShowCalibrationFlow()
    {
        if (SetupOverlay.Visibility == Visibility.Visible) HideSetup(resume: true);
        CalibrationOverlay.Visibility = Visibility.Visible;
        _conn.Send(new { t = "blur" });
        _calibrationDrawn = null;
        DrawCalibrationFlow();
        ShowCalibration();
    }

    void HideCalibrationFlow()
    {
        CalibrationOverlay.Visibility = Visibility.Collapsed;
        _flowTurn?.Stop();
        Surface.Focus(FocusState.Programmatic);
        ShowCalibration();
        if (!AppSettings.SetupDone) _ = LoadSetup();
    }

    string? _calibrationDrawn;

    void DrawCalibrationFlow()
    {
        if (CalibrationOverlay.Visibility != Visibility.Visible) return;
        string? st = CalState;
        bool upright = _frameSideways != true;
        int step = st == "running" ? (int?)_calibration?["step"] ?? 0 : -1;
        string key = $"{st}|{step}|{upright}|{_calibrationStarting}|{_calibrationError}|{AppSettings.AssistiveTouchDone}|{_live}|{(string?)_calibration?["message"]}";
        if (key == _calibrationDrawn) return;
        _calibrationDrawn = key;
        var body = CalibrationBody;
        body.Children.Clear();

        var head = new Grid { Margin = new Thickness(0, 4, 0, 4) };
        head.ColumnDefinitions.Add(new ColumnDefinition { Width = new GridLength(1, GridUnitType.Star) });
        head.ColumnDefinitions.Add(new ColumnDefinition { Width = GridLength.Auto });
        var titles = new StackPanel { Spacing = 2 };
        titles.Children.Add(new TextBlock { Text = "Calibrate the pointer", Style = Res<Style>("SubtitleTextBlockStyle") });
        titles.Children.Add(new TextBlock
        {
            Text = "So drags and swipes go the right distance on this phone.",
            Opacity = 0.7, Style = Res<Style>("CaptionTextBlockStyle"), TextWrapping = TextWrapping.Wrap,
        });
        head.Children.Add(titles);
        var back = new Button { Content = st == "done" ? "Done" : "Show the phone", VerticalAlignment = VerticalAlignment.Top };
        if (st == "done") back.Style = Res<Style>("AccentButtonStyle");
        back.Click += (_, _) => HideCalibrationFlow();
        Grid.SetColumn(back, 1);
        head.Children.Add(back);
        body.Children.Add(head);

        bool measuring = st is "running" or "waiting_upright" or "waiting_link" or "waiting_pointer" or "waiting_unlock";
        bool atOn = AppSettings.AssistiveTouchDone || measuring || st is "done" or "failed";
        bool done = st == "done";

        // 1. AssistiveTouch
        var at = new StackPanel { Spacing = 6 };
        at.Children.Add(Para("On the iPhone: Settings › Accessibility › Touch › AssistiveTouch on, and Dwell Control off. "
                             + "iOS ignores a mouse without it.", 0.85));
        if (!atOn)
        {
            var ok = new Button { Content = "It's on", Style = Res<Style>("AccentButtonStyle") };
            ok.Click += (_, _) => { AppSettings.AssistiveTouchDone = true; _calibrationDrawn = null; DrawCalibrationFlow(); };
            at.Children.Add(ok);
        }
        body.Children.Add(FlowCard(1, "Turn on AssistiveTouch", at, atOn, !atOn));

        // 2. upright
        var up = new StackPanel { Spacing = 10 };
        var turn = new RotateTransform { Angle = upright ? 0 : 90 };
        var icon = new FontIcon
        {
            Glyph = "", FontSize = 44, HorizontalAlignment = HorizontalAlignment.Left,
            RenderTransform = turn, RenderTransformOrigin = new Windows.Foundation.Point(0.5, 0.5),
            Foreground = upright ? SetupGreen : Res<Brush>("AccentFillColorDefaultBrush"),
        };
        up.Children.Add(icon);
        up.Children.Add(Para(upright
            ? "The iPhone is upright. Keep it that way, on a still screen (a new note is good), until the measurement is done."
            : "The iPhone is sideways. Turn it upright (portrait): Phone Use measures the pointer only that way, and starts by itself once it is.",
            0.85));
        _flowTurn?.Stop();
        if (!upright && atOn)
        {
            _flowTurn = TurnAnimation(turn);
            _flowTurn.Begin();
        }
        bool upCurrent = atOn && !done && (!upright || !measuring);
        body.Children.Add(FlowCard(2, "Hold the iPhone upright", up, upright && (measuring || done), upCurrent && !upright));

        // 3. measure
        var me = new StackPanel { Spacing = 8 };
        if (done)
            me.Children.Add(Para($"{(string?)_calibration?["phone"] ?? "The iPhone"}'s pointer is calibrated. "
                                 + "Drags and swipes now go the right distance on it.", 0.85));
        else if (st == "running")
        {
            int steps = (int?)_calibration?["steps"] ?? 7;
            me.Children.Add(Para($"Measuring… {Math.Min(step + 1, steps)} of {steps}. Leave the phone alone (about 13 seconds).", 0.85));
            me.Children.Add(new ProgressBar { Maximum = steps, Value = step });
        }
        else if (st == "waiting_upright")
            me.Children.Add(Para(upright ? "Starting…" : "Waiting for the iPhone to be upright.", 0.85));
        else if (st is "waiting_link" or "waiting_pointer" or "waiting_unlock")
        {
            me.Children.Add(Para(st switch
            {
                "waiting_link" => "Waiting for the iPhone's Bluetooth link.",
                "waiting_pointer" => "Waiting for the pointer to show on the iPhone (AssistiveTouch on).",
                _ => "Unlock the iPhone: the measurement starts once it's unlocked.",
            }, 0.85));
            me.Children.Add(new ProgressBar { IsIndeterminate = true });
        }
        else
        {
            if (st == "failed")
                me.Children.Add(ErrorText("Calibration didn't work. " + ((string?)_calibration?["message"] ?? "")));
            me.Children.Add(Para("Phone Use moves the pointer up the screen a few times, hovering only: nothing is tapped.", 0.85));
            var row = new StackPanel { Orientation = Orientation.Horizontal, Spacing = 10 };
            var go = new Button
            {
                Content = _calibrationStarting ? "Starting…" : st == "failed" ? "Try again" : "Calibrate",
                Style = Res<Style>("AccentButtonStyle"), IsEnabled = atOn && !_calibrationStarting && _live,
            };
            go.Click += async (_, _) => await StartCalibration();
            row.Children.Add(go);
            if (_calibrationStarting) row.Children.Add(new ProgressRing { IsActive = true, Width = 18, Height = 18 });
            me.Children.Add(row);
            if (!_live) me.Children.Add(Para("Start Screen Mirroring first: the iPhone isn't mirroring to this PC.", 0.7));
        }
        if (_calibrationError != null) me.Children.Add(ErrorText(_calibrationError));
        body.Children.Add(FlowCard(3, "Measure", me, done, atOn && !done && (upright || !measuring)));
    }

    Border FlowCard(int number, string title, FrameworkElement content, bool done, bool current)
    {
        var g = new Grid { ColumnSpacing = 12 };
        g.ColumnDefinitions.Add(new ColumnDefinition { Width = GridLength.Auto });
        g.ColumnDefinitions.Add(new ColumnDefinition { Width = new GridLength(1, GridUnitType.Star) });
        g.Children.Add(Badge(number, done, current));
        var col = new StackPanel { Spacing = 6 };
        col.Children.Add(new TextBlock { Text = title, Style = Res<Style>("BodyStrongTextBlockStyle"), TextWrapping = TextWrapping.Wrap });
        col.Children.Add(content);
        Grid.SetColumn(col, 1);
        g.Children.Add(col);
        return new Border
        {
            Child = g, Padding = new Thickness(14, 12, 14, 12), CornerRadius = new CornerRadius(8),
            Background = Res<Brush>(current ? "CardBackgroundFillColorSecondaryBrush" : "CardBackgroundFillColorDefaultBrush"),
            BorderBrush = current ? Res<Brush>("AccentFillColorDefaultBrush") : Res<Brush>("CardStrokeColorDefaultBrush"),
            BorderThickness = new Thickness(current ? 1.5 : 1),
        };
    }

    static TextBlock ErrorText(string text) => new()
    {
        Text = text, TextWrapping = TextWrapping.Wrap,
        Foreground = new SolidColorBrush(Windows.UI.Color.FromArgb(255, 0xFF, 0x45, 0x3A)),
    };

    /// <summary>A phone turning from sideways to upright, over and over.</summary>
    static Storyboard TurnAnimation(RotateTransform t)
    {
        var a = new DoubleAnimationUsingKeyFrames { RepeatBehavior = RepeatBehavior.Forever, EnableDependentAnimation = true };
        a.KeyFrames.Add(new DiscreteDoubleKeyFrame { KeyTime = TimeSpan.Zero, Value = 90 });
        a.KeyFrames.Add(new DiscreteDoubleKeyFrame { KeyTime = TimeSpan.FromSeconds(0.4), Value = 90 });
        a.KeyFrames.Add(new EasingDoubleKeyFrame
        {
            KeyTime = TimeSpan.FromSeconds(1.1), Value = 0,
            EasingFunction = new CubicEase { EasingMode = EasingMode.EaseInOut },
        });
        a.KeyFrames.Add(new DiscreteDoubleKeyFrame { KeyTime = TimeSpan.FromSeconds(2.4), Value = 0 });
        Storyboard.SetTarget(a, t);
        Storyboard.SetTargetProperty(a, "Angle");
        var s = new Storyboard();
        s.Children.Add(a);
        return s;
    }
}
