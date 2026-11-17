using System.Runtime.InteropServices.WindowsRuntime;
using Microsoft.UI.Xaml;
using Microsoft.UI.Xaml.Controls;
using Microsoft.UI.Xaml.Media;
using Microsoft.UI.Xaml.Media.Imaging;
using QRCoder;
using Windows.Storage.Streams;

namespace PhoneUseMirror;

/// <summary>
/// The Phone Use iPhone app (docs/COMPANION.md), where the owner allows and stops
/// agents: agents are refused until one is paired. Settings › iPhone app shows how
/// many are paired, pairs one with a QR code (a one-time link from
/// /api/companion/pair that works for 10 minutes; a new code before it runs out),
/// and unpairs (/api/companion/unpair), as the Linux and Mac apps do.
/// </summary>
public sealed partial class MainWindow
{
    int _paired = -1;
    // Shown only while no iPhone app is paired; once you close it, never again (AppSettings).
    void PairingBar_Closed(InfoBar sender, InfoBarClosedEventArgs args)
    {
        if (args.Reason == InfoBarCloseReason.CloseButton) AppSettings.PairingBarDismissed = true;
    }

    async Task<int> PairedCount()
    {
        var j = await _conn.GetAsync("api/app");
        return j != null && j["_failed"] == null ? (int?)(double?)j["paired"] ?? 0 : -1;
    }

    async Task LoadPaired() => ShowPaired(await PairedCount());

    void ShowPaired(int n)
    {
        _paired = n;
        PairedText.Text = n switch
        {
            < 0 => "phoneuse isn't answering.",
            0 => "Not paired. Agents can't use the phone until you pair the Phone Use app (App Store) on the iPhone: " +
                 "it's where you allow and stop them.",
            1 => "1 iPhone paired. Agents can use the phone; you allow and stop them in the app.",
            _ => $"{n} iPhones paired. Agents can use the phone; you allow and stop them in the app.",
        };
        PairButton.Content = n > 0 ? "Pair another" : "Pair iPhone";
        PairButton.IsEnabled = n >= 0;
        UnpairButton.Visibility = n > 0 ? Visibility.Visible : Visibility.Collapsed;
        PairingBar.IsOpen = n == 0 && !AppSettings.PairingBarDismissed && SetupOverlay.Visibility != Visibility.Visible;
    }

    async void Pair_Click(object sender, RoutedEventArgs e)
    {
        HideSettings();
        if (_ask != null) return;  // an unlock or agent request is up; one dialog at a time
        int before = Math.Max(0, await PairedCount());

        var image = new Image { Width = 260, Height = 260, Stretch = Stretch.Uniform };
        var status = new TextBlock
        {
            Text = "Making a code…", TextWrapping = TextWrapping.Wrap, TextAlignment = TextAlignment.Center,
            Opacity = 0.75, HorizontalAlignment = HorizontalAlignment.Center,
        };
        var body = new StackPanel { Spacing = 14, Width = 300 };
        body.Children.Add(new Border
        {
            Background = new SolidColorBrush(Microsoft.UI.Colors.White), CornerRadius = new CornerRadius(12),
            Padding = new Thickness(10), HorizontalAlignment = HorizontalAlignment.Center, Child = image,
        });
        body.Children.Add(status);
        var dialog = new ContentDialog
        {
            XamlRoot = Root.XamlRoot,
            RequestedTheme = ElementTheme.Dark,
            Title = "Pair iPhone",
            Content = body,
            CloseButtonText = "Cancel",
        };

        var made = DateTime.MinValue;
        bool open = true, paired = false;
        async Task NewCode()
        {
            made = DateTime.UtcNow;
            var r = await _conn.PostAsync("api/companion/pair", new { });
            var link = (string?)r?["link"];
            if (link == null || !link.StartsWith("phoneuse://pair"))
            {
                status.Text = "Couldn't make a pairing code: " + ((string?)r?["error"] ?? "no link") + ". Close and try again.";
                return;
            }
            image.Source = await QrImage(link);
            status.Text = "In the Phone Use app on your iPhone, scan this code (or scan it with the Camera app " +
                          "and open it in Phone Use). It works once, for 10 minutes.";
        }

        var timer = _ui.CreateTimer();
        timer.Interval = TimeSpan.FromSeconds(2);
        timer.Tick += async (_, _) =>
        {
            if (!open) return;
            int n = await PairedCount();
            if (n > before)
            {
                paired = true;
                dialog.Hide();
            }
            else if (DateTime.UtcNow - made > TimeSpan.FromSeconds(590))
                await NewCode();  // the code lasts 10 minutes
        };

        _ = NewCode();
        timer.Start();
        try { await dialog.ShowAsync(); }
        catch (Exception) { }  // another dialog won the race
        open = false;
        timer.Stop();
        await LoadPaired();
        if (paired) _tray?.Balloon("iPhone paired", "Agents can use the phone now. You allow and stop them in the Phone Use app.");
    }

    static async Task<ImageSource> QrImage(string link)
    {
        using var gen = new QRCodeGenerator();
        using var data = gen.CreateQrCode(link, QRCodeGenerator.ECCLevel.M);
        var png = new PngByteQRCode(data).GetGraphic(10, drawQuietZones: false);
        var bmp = new BitmapImage();
        using var stream = new InMemoryRandomAccessStream();
        await stream.WriteAsync(png.AsBuffer());
        stream.Seek(0);
        await bmp.SetSourceAsync(stream);
        return bmp;
    }

    async void Unpair_Click(object sender, RoutedEventArgs e)
    {
        HideSettings();
        if (_ask != null) return;
        var confirm = new ContentDialog
        {
            XamlRoot = Root.XamlRoot,
            RequestedTheme = ElementTheme.Dark,
            Title = _paired == 1 ? "Unpair the iPhone app?" : $"Unpair {_paired} iPhone apps?",
            Content = "Agents can't use the iPhone until you pair the app again.",
            PrimaryButtonText = "Unpair",
            CloseButtonText = "Cancel",
            DefaultButton = ContentDialogButton.Close,
        };
        ContentDialogResult result;
        try { result = await confirm.ShowAsync(); }
        catch (Exception) { return; }
        if (result != ContentDialogResult.Primary) return;
        await _conn.PostAsync("api/companion/unpair", new { });
        await LoadPaired();
    }
}
