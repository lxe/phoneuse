using Microsoft.UI.Xaml;
using Microsoft.UI.Xaml.Controls;
using Microsoft.UI.Xaml.Media;
using Microsoft.UI.Xaml.Shapes;
using Windows.UI;

namespace PhoneUseMirror;

/// <summary>
/// The phone frame's colour (FrameFinish.cs): the band and side buttons drawn in a real iPhone
/// finish, chosen from swatches in Settings › Appearance.
/// </summary>
public sealed partial class MainWindow
{
    void ApplyFinish(FrameFinish f)
    {
        _finish = f;
        if (_band.pw > 0) DrawBand();
        // the plain band, while the lit one (MainWindow.Frame.cs) can't be drawn: lit from the top left, in shade across the middle, catching light again at the bottom right
        Titanium.Fill = new LinearGradientBrush
        {
            StartPoint = new Windows.Foundation.Point(0, 0),
            EndPoint = new Windows.Foundation.Point(1, 1),
            GradientStops =
            {
                new GradientStop { Color = ForDisplay(f.Light), Offset = 0 },
                new GradientStop { Color = ForDisplay(f.Dark), Offset = 0.45 },
                new GradientStop { Color = ForDisplay(f.Mid), Offset = 1 },
            },
        };
        // the buttons: rounded across their width, brightest down the middle
        Buttons.Fill = new LinearGradientBrush
        {
            StartPoint = new Windows.Foundation.Point(0, 0),
            EndPoint = new Windows.Foundation.Point(1, 0),
            GradientStops =
            {
                new GradientStop { Color = ForDisplay(f.ButtonEdge), Offset = 0 },
                new GradientStop { Color = ForDisplay(f.ButtonMid), Offset = 0.5 },
                new GradientStop { Color = ForDisplay(f.ButtonEdge), Offset = 1 },
            },
        };
        ApplyCameraControlFinish(f);
    }

    /// <summary>
    /// Camera Control's sapphire. On a dark band it's dark glass, as it looks on the phone. On a light
    /// band (Silver, White, Cloud White, Natural...) dark glass is only a dark sliver beside a light
    /// band, lost against a dark desktop; there it's drawn as it looks on those phones instead: the
    /// glass showing the light metal under it, brightest down the middle, with a dark gap around it
    /// so it still has an edge against a light desktop.
    /// </summary>
    void ApplyCameraControlFinish(FrameFinish f)
    {
        var band = f.Band;
        double luma = (0.299 * band.R + 0.587 * band.G + 0.114 * band.B) / 255;
        Color edge, mid;
        if (luma < LightBand)
        {
            edge = Color.FromArgb(0xFF, 0x14, 0x14, 0x16);
            mid = Color.FromArgb(0xFF, 0x4C, 0x4C, 0x54);
            CameraControl.Stroke = null;
        }
        else
        {
            var glass = Color.FromArgb(0xFF, 0x1C, 0x1C, 0x22);  // sapphire's own slight blue-grey
            edge = FrameFinish.Lerp(band, glass, 0.5);
            mid = FrameFinish.Lerp(band, Color.FromArgb(0xFF, 0xFF, 0xFF, 0xFF), 0.4);
            CameraControl.Stroke = new SolidColorBrush(Color.FromArgb(0xA0, 0, 0, 0));
            CameraControl.StrokeThickness = 0.8;
        }
        CameraControl.Fill = new LinearGradientBrush
        {
            StartPoint = new Windows.Foundation.Point(0, 0),
            EndPoint = new Windows.Foundation.Point(1, 0),
            GradientStops =
            {
                new GradientStop { Color = ForDisplay(edge), Offset = 0 },
                new GradientStop { Color = ForDisplay(mid), Offset = 0.5 },
                new GradientStop { Color = ForDisplay(edge), Offset = 1 },
            },
        };
    }

    /// <summary>A band at least this light (luma, 0-1) gets the light Camera Control. Natural Titanium
    /// (0.52) and Cosmic Orange (0.53) are just over it; Teal (0.44) stays dark.</summary>
    const double LightBand = 0.45;

    // ---------------------------------------------------------------- Settings › Appearance

    void FillFinishSettings()
    {
        var chosen = FrameFinish.Get(AppSettings.FrameFinish);
        FinishName.Text = chosen.Group == "Phone Use" ? chosen.Name : $"{chosen.Name} · {PhoneModel.FinishGroupLabel(chosen.Group)}";
        FinishGroups.Children.Clear();
        // the colours of the phone that's mirroring come first
        var groups = FrameFinish.All.GroupBy(f => f.Group)
            .OrderBy(g => g.Key == _phone.FinishGroup ? 0 : 1);
        foreach (var group in groups)
        {
            var label = PhoneModel.FinishGroupLabel(group.Key);
            FinishGroups.Children.Add(new TextBlock
            {
                Text = group.Key == _phone.FinishGroup ? $"{label} · your iPhone" : label,
                Margin = new Thickness(0, 10, 0, 2),
                Opacity = 0.7,
                Style = (Style)Application.Current.Resources["CaptionTextBlockStyle"],
                TextWrapping = TextWrapping.Wrap,
            });
            var swatches = new VariableSizedWrapGrid { Orientation = Orientation.Horizontal, ItemWidth = 42, ItemHeight = 42 };
            foreach (var f in group) swatches.Children.Add(Swatch(f, label, f.Id == chosen.Id));
            FinishGroups.Children.Add(swatches);
        }
    }

    /// <summary>A round chip of the finish, ringed while it's the one chosen.</summary>
    Button Swatch(FrameFinish f, string label, bool selected)
    {
        var chip = new Ellipse
        {
            Width = 26,
            Height = 26,
            Stroke = new SolidColorBrush(Color.FromArgb(0x50, 0xFF, 0xFF, 0xFF)),
            StrokeThickness = 1,
            Fill = new LinearGradientBrush
            {
                StartPoint = new Windows.Foundation.Point(0, 0),
                EndPoint = new Windows.Foundation.Point(1, 1),
                GradientStops =
                {
                    new GradientStop { Color = ForDisplay(f.Light), Offset = 0 },
                    new GradientStop { Color = ForDisplay(f.Base), Offset = 0.5 },
                    new GradientStop { Color = ForDisplay(f.Dark), Offset = 1 },
                },
            },
        };
        var button = new Button
        {
            Content = chip,
            Width = 36,
            Height = 36,
            Padding = new Thickness(0),
            CornerRadius = new CornerRadius(18),
            Background = new SolidColorBrush(Microsoft.UI.Colors.Transparent),
            BorderThickness = new Thickness(2),
            BorderBrush = selected
                ? (Brush)Application.Current.Resources["AccentFillColorDefaultBrush"]
                : new SolidColorBrush(Microsoft.UI.Colors.Transparent),
            Tag = f.Id,
        };
        ToolTipService.SetToolTip(button, f.Name);
        Microsoft.UI.Xaml.Automation.AutomationProperties.SetName(button, $"{f.Name}, {label}");
        button.Click += (_, _) =>
        {
            AppSettings.FrameFinish = f.Id;
            ApplyFinish(f);
            FillFinishSettings();
        };
        return button;
    }
}
