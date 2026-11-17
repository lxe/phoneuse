using Microsoft.UI.Xaml;
using Microsoft.UI.Xaml.Controls;
using Microsoft.UI.Xaml.Media;
using Microsoft.UI.Xaml.Media.Imaging;

namespace PhoneUseMirror;

/// <summary>
/// About Phone Use, shown in the main window over the phone: how to use it, and
/// what's different on Windows (docs/WINDOWS.md). The PC's name in it is read from
/// Windows when it opens, so each PC shows its own.
/// </summary>
public sealed class AboutView : UserControl
{
    readonly StackPanel _body = new() { Spacing = 6, Padding = new Thickness(20, 8, 20, 28), MaxWidth = 640 };

    public AboutView()
    {
        string pc = Environment.MachineName;
        var version = typeof(AboutView).Assembly.GetName().Version?.ToString(3) ?? "";

        // header
        var head = new StackPanel { Orientation = Orientation.Horizontal, Spacing = 14, Margin = new Thickness(0, 0, 0, 6) };
        var png = Path.Combine(AppContext.BaseDirectory, "Assets", "AppIcon.png");
        if (File.Exists(png)) head.Children.Add(new Image { Source = new BitmapImage(new Uri(png)), Width = 56, Height = 56 });
        var titles = new StackPanel { VerticalAlignment = VerticalAlignment.Center };
        titles.Children.Add(new TextBlock { Text = "Phone Use", Style = Style("TitleTextBlockStyle") });
        titles.Children.Add(new TextBlock { Text = "Version " + version, Opacity = 0.6 });
        head.Children.Add(titles);
        _body.Children.Add(head);
        Para("See and use your iPhone from this PC. AirPlay brings the phone's screen here, and this PC " +
             "acts as a Bluetooth keyboard and mouse for the phone, so clicks and typing reach it. AI agents " +
             "can use the phone the same way, and you can watch and take over at any time.");

        Heading("Getting started");
        Step(1, "Mirror the screen",
             $"On the iPhone, open Control Center, tap Screen Mirroring and choose Phone Use on {pc}.");
        Step(2, "Pair Bluetooth, once",
             $"On the iPhone, open Settings › Bluetooth and tap {pc}. Confirm on the phone, and on Windows if it asks.");
        Step(3, "Turn on AssistiveTouch",
             "On the iPhone: Settings › Accessibility › Touch › AssistiveTouch on, and Dwell Control off. " +
             "iOS ignores a mouse without AssistiveTouch. The first time, Phone Use spends about 13 seconds " +
             "measuring how this phone's pointer moves.");

        Heading("Using the phone");
        Bullet("Click to tap, click and drag to swipe, and use the scroll wheel to scroll.");
        Bullet("Pressing on the bezel touches the nearest edge of the screen: drag from there for edge swipes.");
        Bullet("Typing goes to the phone. Ctrl works as the phone's Cmd (Ctrl+C, Ctrl+V, Ctrl+Space for " +
               "Spotlight), and the Windows key works as the phone's Ctrl.");
        Bullet("F1 Siri · F2 Home · F3 Search · F4 shows or hides the on-screen keyboard.");
        Bullet("When an agent drives the phone, a ring appears around the screen and a button at the bottom " +
               "lets you take over. Settings › Agent control chooses whether agents start right away, after " +
               "a delay, or only when you allow it.");

        Heading("The app and phoneuse");
        Bullet("Opening Phone Use starts phoneuse in the background when it isn't already running " +
               "(a phoneuse up in a terminal works too).");
        Bullet("Closing the window keeps Phone Use in the tray, so agents can keep using the phone. " +
               "Click the tray icon to open it again; Quit in its menu stops the phoneuse it started.");
        Bullet("The Bluetooth keyboard and mouse keep running after Quit, so the iPhone stays paired. " +
               "phoneuse down stops them.");
        Bullet("Settings › Start with Windows starts phoneuse when you sign in, with Phone Use in the tray.");
        Bullet("Logs: the tray menu's Show phoneuse log, or %LOCALAPPDATA%\\phoneuse\\run. " +
               "Configuration: %APPDATA%\\phoneuse\\config.toml.");

        Heading("Limitations on Windows");
        Bullet("Bluetooth LE only. Windows can't act as a Bluetooth Classic keyboard, so the phone takes a " +
               "report every 15 ms instead of every 8 ms, and the picture trails the phone by about 0.1 s. " +
               "By hand it feels a little slower than on Linux; agents don't notice.");
        Bullet($"The phone sees this PC's Windows name ({pc}) in Bluetooth: Windows doesn't let Phone Use choose its own.");
        Bullet("Each time the Bluetooth service restarts, the iPhone sees the keyboard and mouse disappear and " +
               "come back, and it may need pairing again. That's why it keeps running on its own.");
        Bullet("Pair only the phone Phone Use should control. Over Bluetooth LE iOS may call any phone just " +
               "\"iPhone\", so with two paired phones connected Phone Use can't tell which one is mirroring, " +
               "and sends nothing.");
        Bullet("A motherboard's Wi-Fi and Bluetooth card needs its antennas attached, even when the PC uses " +
               "Ethernet. Without them the link drops several times an hour, and keys and taps sent during a drop are lost.");
        Bullet("Windows keeps some Windows-key shortcuts (such as Win+L) for itself; those never reach the phone.");
        Bullet("Settings › Bluetooth bridge sets up a LilyGO T-Display as the keyboard and mouse instead " +
               "(Bluetooth Classic, as on Linux) and flashes its firmware. While the bridge carries input, this " +
               "PC's Bluetooth keyboard is closed; it opens again if the bridge stops answering for 15 seconds. " +
               "The bridge hasn't been tried with real hardware on Windows yet.");

        if (Backend.Root != null)
        {
            Heading("This copy");
            Para("phoneuse: " + Backend.Root, 0.6);
        }

        Content = new ScrollViewer { Content = _body };
    }

    static Style Style(string key) => (Style)Application.Current.Resources[key];

    void Heading(string text) => _body.Children.Add(new TextBlock
    {
        Text = text,
        Style = Style("SubtitleTextBlockStyle"),
        Margin = new Thickness(0, 18, 0, 2),
    });

    void Para(string text, double opacity = 0.85) => _body.Children.Add(new TextBlock
    {
        Text = text,
        TextWrapping = TextWrapping.Wrap,
        Opacity = opacity,
        IsTextSelectionEnabled = true,
    });

    void Bullet(string text)
    {
        var g = new Grid { ColumnSpacing = 10 };
        g.ColumnDefinitions.Add(new ColumnDefinition { Width = GridLength.Auto });
        g.ColumnDefinitions.Add(new ColumnDefinition { Width = new GridLength(1, GridUnitType.Star) });
        g.Children.Add(new TextBlock { Text = "•", Opacity = 0.6 });
        var t = new TextBlock { Text = text, TextWrapping = TextWrapping.Wrap, Opacity = 0.85, IsTextSelectionEnabled = true };
        Grid.SetColumn(t, 1);
        g.Children.Add(t);
        _body.Children.Add(g);
    }

    void Step(int n, string title, string text)
    {
        var g = new Grid { ColumnSpacing = 12, Margin = new Thickness(0, 4, 0, 4) };
        g.ColumnDefinitions.Add(new ColumnDefinition { Width = GridLength.Auto });
        g.ColumnDefinitions.Add(new ColumnDefinition { Width = new GridLength(1, GridUnitType.Star) });
        var badge = new Border
        {
            Width = 24, Height = 24, CornerRadius = new CornerRadius(12), VerticalAlignment = VerticalAlignment.Top,
            Background = (Brush)Application.Current.Resources["AccentFillColorDefaultBrush"],
            Child = new TextBlock
            {
                Text = n.ToString(), FontWeight = Microsoft.UI.Text.FontWeights.SemiBold,
                HorizontalAlignment = HorizontalAlignment.Center, VerticalAlignment = VerticalAlignment.Center,
                Foreground = new SolidColorBrush(Microsoft.UI.Colors.Black),
            },
        };
        g.Children.Add(badge);
        var col = new StackPanel { Spacing = 2 };
        col.Children.Add(new TextBlock { Text = title, Style = Style("BodyStrongTextBlockStyle") });
        col.Children.Add(new TextBlock { Text = text, TextWrapping = TextWrapping.Wrap, Opacity = 0.85, IsTextSelectionEnabled = true });
        Grid.SetColumn(col, 1);
        g.Children.Add(col);
        _body.Children.Add(g);
    }
}
