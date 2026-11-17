using Microsoft.UI.Xaml;
using Microsoft.UI.Xaml.Controls;
using Microsoft.UI.Xaml.Media;

namespace PhoneUseMirror;

/// <summary>
/// The phone's area: a focusable control (keys go to the focused element) with a
/// transparent background so the whole phone, bezel included, takes the mouse.
/// </summary>
public sealed partial class InputSurface : ContentControl
{
    public InputSurface()
    {
        IsTabStop = true;
        UseSystemFocusVisuals = false;
        TabFocusNavigation = Microsoft.UI.Xaml.Input.KeyboardNavigationMode.Once;
        Background = new SolidColorBrush(Microsoft.UI.Colors.Transparent);
        HorizontalContentAlignment = HorizontalAlignment.Stretch;
        VerticalContentAlignment = VerticalAlignment.Stretch;
    }
}
