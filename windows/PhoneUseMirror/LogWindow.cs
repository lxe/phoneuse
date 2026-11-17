using Microsoft.UI.Dispatching;
using Microsoft.UI.Xaml;
using Microsoft.UI.Xaml.Controls;
using Microsoft.UI.Xaml.Media;
using Windows.Graphics;

namespace PhoneUseMirror;

/// <summary>
/// phoneuse's log (phoneuse-up.log, written by the background phoneuse this app
/// starts), followed live: the last lines, kept scrolled to the end unless you
/// scroll up.
/// </summary>
public sealed class LogWindow : Window
{
    const int MaxBytes = 256 * 1024;
    readonly string _path;
    readonly TextBlock _text;
    readonly ScrollViewer _scroll;
    readonly DispatcherQueueTimer _timer;
    long _seenLength = -1;
    DateTime _seenWrite;

    public LogWindow(string path)
    {
        _path = path;
        Title = "phoneuse log";
        var icon = Path.Combine(AppContext.BaseDirectory, "Assets", "AppIcon.ico");
        if (File.Exists(icon)) AppWindow.SetIcon(icon);
        AppWindow.Resize(new SizeInt32(1000, 640));

        _text = new TextBlock
        {
            FontFamily = new FontFamily("Cascadia Mono, Consolas"),
            FontSize = 12,
            IsTextSelectionEnabled = true,
            TextWrapping = TextWrapping.Wrap,
            Margin = new Thickness(12, 8, 12, 8),
        };
        _scroll = new ScrollViewer { Content = _text, HorizontalScrollBarVisibility = ScrollBarVisibility.Disabled };

        var folder = new HyperlinkButton { Content = "Open the log folder", Margin = new Thickness(8, 4, 8, 4) };
        folder.Click += (_, _) =>
        {
            try { System.Diagnostics.Process.Start("explorer.exe", "/select,\"" + _path + "\""); } catch (Exception) { }
        };

        var root = new Grid { RequestedTheme = ElementTheme.Dark, Background = new SolidColorBrush(Windows.UI.Color.FromArgb(255, 15, 15, 17)) };
        root.RowDefinitions.Add(new RowDefinition { Height = new GridLength(1, GridUnitType.Star) });
        root.RowDefinitions.Add(new RowDefinition { Height = GridLength.Auto });
        root.Children.Add(_scroll);
        Grid.SetRow(folder, 1);
        root.Children.Add(folder);
        Content = root;

        _timer = DispatcherQueue.GetForCurrentThread().CreateTimer();
        _timer.Interval = TimeSpan.FromSeconds(1);
        _timer.Tick += (_, _) => Refresh();
        _timer.Start();
        Closed += (_, _) => _timer.Stop();
        Refresh();
    }

    void Refresh()
    {
        var info = new FileInfo(_path);
        if (!info.Exists)
        {
            if (_seenLength != 0) _text.Text = "No log yet. phoneuse writes here when this app starts it; " +
                                               "a `phoneuse up` in a terminal prints there instead.";
            _seenLength = 0;
            return;
        }
        if (info.Length == _seenLength && info.LastWriteTimeUtc == _seenWrite) return;
        _seenLength = info.Length;
        _seenWrite = info.LastWriteTimeUtc;

        string text;
        try
        {
            using var f = new FileStream(_path, FileMode.Open, FileAccess.Read, FileShare.ReadWrite | FileShare.Delete);
            long start = Math.Max(0, f.Length - MaxBytes);
            f.Seek(start, SeekOrigin.Begin);
            using var r = new StreamReader(f);
            text = r.ReadToEnd();
            if (start > 0)
            {
                int nl = text.IndexOf('\n');
                if (nl >= 0) text = text[(nl + 1)..];  // from a whole line
            }
        }
        catch (IOException e) { text = "Can't read " + _path + ": " + e.Message; }

        bool atEnd = _scroll.VerticalOffset >= _scroll.ScrollableHeight - 24;
        _text.Text = text.TrimEnd();
        if (atEnd)
        {
            _scroll.UpdateLayout();
            _scroll.ChangeView(null, _scroll.ScrollableHeight, null, true);
        }
    }
}
