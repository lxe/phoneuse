using System.Numerics;
using System.Runtime.InteropServices.WindowsRuntime;
using Microsoft.UI.Composition;
using Microsoft.UI.Xaml;
using Microsoft.UI.Xaml.Hosting;
using Microsoft.UI.Xaml.Media.Imaging;
using Windows.UI;

namespace PhoneUseMirror;

/// <summary>
/// What makes the frame look like a real phone, beyond its outline (Layout):
///   the band, lit pixel by pixel as a rounded metal edge (light from the top left, brushed streaks
///     along it, the antenna lines across it), in the chosen finish (MainWindow.Finish.cs);
///   a soft shadow under the phone.
/// The band is drawn on the CPU into a bitmap at the screen's pixels, only when its size or finish
/// changes; if that fails the plain gradient band (Titanium) shows instead.
/// </summary>
public sealed partial class MainWindow
{
    FrameFinish _finish = FrameFinish.Get(null);
    (double pw, double ph, double k, double t, double r) _band;
    List<(double x, double y, double w, double h)> _bandButtons = new();
    (int w, int h, string id) _bandDrawn;

    // ---------------------------------------------------------------- the band

    /// <summary>Remember the band's shape and its side buttons (Surface DIPs) and draw them if anything changed.</summary>
    void PlaceBand(double pw, double ph, double k, double t, double r, List<(double x, double y, double w, double h)> buttons)
    {
        _band = (pw, ph, k, t, r);
        _bandButtons = buttons;
        if (!_watchingScale && Root.XamlRoot != null)
        {
            // moved to a screen of another scale: redraw at its pixels
            _watchingScale = true;
            Root.XamlRoot.Changed += (_, _) => DrawBand();
        }
        DrawBand();
    }

    bool _watchingScale;

    void DrawBand()
    {
        var (pw, ph, k, t, r) = _band;
        double s = Root.XamlRoot?.RasterizationScale ?? 1;
        int w = (int)Math.Round(pw * s), h = (int)Math.Round(ph * s);
        if (w <= 0 || h <= 0) return;
        // what's drawn: the finish, the metal, and which buttons where (by count and the first one's place)
        var b0 = _bandButtons.Count > 0 ? _bandButtons[0] : default;
        string key = $"{_finish.Id}|{_phone.Material}|{_bandButtons.Count}|{b0.x * s:F0},{b0.y * s:F0}|{_hdr}";
        if (_bandDrawn == (w, h, key) && BandImage.Source != null)
        {
            // only the DIP size can have moved
            BandImage.Width = w / s;
            BandImage.Height = h / s;
            return;
        }
        try
        {
            var buttons = _bandButtons.Select(b => (b.x * s, b.y * s, b.w * s, b.h * s)).ToList();
            var pixels = BandPixels(w, h, k * s, t * s, r * s, _finish, _phone.Material, buttons, _hdr);
            var bmp = new WriteableBitmap(w, h);
            using (var stream = bmp.PixelBuffer.AsStream()) stream.Write(pixels, 0, pixels.Length);
            bmp.Invalidate();
            BandImage.Source = bmp;
            BandImage.Width = w / s;
            BandImage.Height = h / s;
            _bandDrawn = (w, h, key);
            BandImage.Visibility = Visibility.Visible;
            Titanium.Visibility = Visibility.Collapsed;
            Buttons.Visibility = Visibility.Collapsed;  // drawn lit, with the band
        }
        catch (Exception e)
        {
            System.Diagnostics.Debug.WriteLine("band: " + e.Message);
            BandImage.Visibility = Visibility.Collapsed;
            Titanium.Visibility = Visibility.Visible;
            Buttons.Visibility = Visibility.Visible;
        }
    }

    /// <summary>The band and its side buttons, premultiplied BGRA, w x h pixels; k (the buttons' room),
    /// t (the band's width), r (its outer corner) and the buttons' rectangles in pixels.
    ///
    /// Lit so that metal facing you is exactly the finish's colour (FrameFinish.Base, AppleDB's):
    /// the light, the room's reflection and the highlight only change it where the surface turns
    /// toward or away from them.</summary>
    static byte[] BandPixels(int w, int h, double k, double t, double r, FrameFinish f, BandMaterial material,
        List<(double x, double y, double w, double h)> buttons, bool hdr)
    {
        // every colour written: on an HDR display, as gamma 2.2 would show it (ForDisplay)
        Vector3 Out(Vector3 c) => hdr ? new Vector3(Gamma22ToSrgb(c.X), Gamma22ToSrgb(c.Y), Gamma22ToSrgb(c.Z)) : c;
        // How the metal takes the light: highlight sharpness and strength, brushed lines, and how much the
        // highlight keeps the finish's colour (bare metal tints what it reflects; anodised aluminium's
        // clear oxide layer adds a little untinted sheen, the dusty look of its bands in Apple's photos).
        var (shine, specAmount, brushed, tinted) = material switch
        {
            BandMaterial.Steel => (160f, 0.9f, 0.012f, 0.85f),
            BandMaterial.Aluminium => (14f, 0.25f, 0.0f, 0.6f),
            _ => (40f, 0.5f, 0.07f, 0.8f),
        };
        var px = new byte[w * h * 4];
        var bo = new Vector2((float)(w / 2.0 - k), (float)(h / 2.0 - k));  // the band's outer half size
        var bi = bo - new Vector2((float)t);
        float ro = (float)r, ri = (float)Math.Max(0, r - t), tw = (float)Math.Max(1, t);
        float riExtent = ri * (float)ContinuousCorner.Extent;  // where the inner corner starts bending
        const float Under = 2;  // pixels the band reaches in under the black border

        // All lighting in linear light, from the band's own tone (FrameFinish.Band: measured from Apple's
        // photos of the phones, not the colour swatch, which isn't on the band's scale).
        var tone = SrgbToLinear(ToVec(f.Band));
        float toneMax = Math.Max(tone.X, Math.Max(tone.Y, tone.Z));
        var specColor = Vector3.Lerp(new Vector3(toneMax), tone, tinted);  // as bright as the tone, its hue kept by `tinted`
        var plastic = LinearToSrgb(tone * 0.35f);                           // the antenna lines: a darker shade of it

        // light from the top left and in front (y down, as the pixels)
        var L = Vector3.Normalize(new Vector3(-0.45f, -0.6f, 0.66f));
        var Hh = Vector3.Normalize(L + Vector3.UnitZ);

        // Facing you (normal (0, 0, 1)) the shading is exactly 1: the band's tone itself. Turning away from
        // the light it falls to about a quarter (Apple's Burgundy band: 28% at its outer edge), toward it
        // it rises to about 1.4x (theirs: 1.4x, evenly across red, green and blue).
        float flatLit = 0.2f + 0.8f * L.Z, flatSpec = MathF.Pow(Hh.Z, shine);
        // rim: how much a surface turning away from you darkens whatever side the light is on (1: not at all)
        Vector3 Shade(Vector3 n, out float diff, float rim = 1)
        {
            diff = Math.Max(0, Vector3.Dot(n, L));
            float lit = (0.2f + 0.8f * diff) / flatLit;
            float room = -0.1f * n.Y;  // facing up: a little of the room's light; down: its shade
            float spec = Math.Max(0, MathF.Pow(Math.Max(0, Vector3.Dot(n, Hh)), shine) - flatSpec);
            return LinearToSrgb(tone * (Math.Max(0, lit + room) * rim) + specColor * (specAmount * spec));
        }

        // the side buttons first, under the band: each a rounded bar, curving across its width
        foreach (var (bx, by, bw, bh) in buttons)
        {
            bool upright = bw <= bh;  // its narrow side runs across x, or (sideways) across y
            var half = new Vector2((float)(bw / 2), (float)(bh / 2));
            var centre = new Vector2((float)(bx + bw / 2), (float)(by + bh / 2));
            float corner = (float)(k * 0.9), across = upright ? half.X : half.Y;
            int x0 = Math.Max(0, (int)Math.Floor(bx)), x1 = Math.Min(w - 1, (int)Math.Ceiling(bx + bw));
            int y0 = Math.Max(0, (int)Math.Floor(by)), y1 = Math.Min(h - 1, (int)Math.Ceiling(by + bh));
            for (int y = y0; y <= y1; y++)
                for (int x = x0; x <= x1; x++)
                {
                    var p = new Vector2(x + 0.5f, y + 0.5f) - centre;
                    float cover = Math.Clamp(0.5f - ContinuousCorner.Sd(p, half, corner), 0, 1);
                    if (cover <= 0) continue;
                    // Lit by the same light as the band: rounded across its width, its surface turning the way
                    // the band's edge beside it does, so a button on the side away from the light is in the
                    // same shade as the band there (the light's side bright, the other side dim).
                    float s = Math.Clamp((upright ? p.X : p.Y) / Math.Max(across, 0.5f), -1, 1);
                    float tilt = s * 1.1f;
                    var n = upright
                        ? new Vector3(MathF.Sin(tilt), 0, MathF.Cos(tilt))
                        : new Vector3(0, MathF.Sin(tilt), MathF.Cos(tilt));
                    // a rounded button darkens toward both its edges, wherever the light is: the left ones,
                    // lit evenly across, would otherwise read flat against the band
                    float rim = 0.3f + 0.7f * n.Z;
                    // and where it meets the band, the shadowed gap between them (as on the side button,
                    // whose edge the light already leaves dark)
                    float toBand = ContinuousCorner.Sd(new Vector2(x + 0.5f - w / 2f, y + 0.5f - h / 2f), bo, ro);
                    if (toBand < 1.6f) rim *= 0.45f + 0.55f * Math.Clamp(toBand / 1.6f, 0, 1);
                    var col = Out(Vector3.Clamp(Shade(n, out _, rim), Vector3.Zero, Vector3.One)) * cover;
                    int i = (y * w + x) * 4;
                    px[i] = (byte)(col.Z * 255 + 0.5f);
                    px[i + 1] = (byte)(col.Y * 255 + 0.5f);
                    px[i + 2] = (byte)(col.X * 255 + 0.5f);
                    px[i + 3] = (byte)(cover * 255 + 0.5f);
                }
        }

        // antenna lines: across the long sides, just past where each corner's curve ends
        bool tall = h >= w;
        float longHalf = tall ? bo.Y : bo.X, shortHalf = tall ? bo.X : bo.Y;
        float antennaAt = longHalf - ro * (float)ContinuousCorner.Extent * 1.05f, antennaHalf = Math.Max(0.6f, tw * 0.2f);

        float cx = w / 2f, cy = h / 2f;
        for (int y = 0; y < h; y++)
        {
            float py = y + 0.5f - cy;
            for (int x = 0; x < w; x++)
            {
                var p = new Vector2(x + 0.5f - cx, py);
                // quick skip: well inside the band's inner edge, or outside its outer one
                var a = Vector2.Abs(p);
                if ((a.X < bi.X - riExtent - 1 - Under && a.Y < bi.Y - 1 - Under) ||
                    (a.Y < bi.Y - riExtent - 1 - Under && a.X < bi.X - 1 - Under)) continue;
                if (a.X > bo.X + 1 || a.Y > bo.Y + 1) continue;

                float d = SdSquircle(p, bo, ro);       // < 0 inside the outer edge
                float din = SdSquircle(p, bi, ri);     // > 0 outside the inner edge
                // inward the band runs on under the black border (drawn over it), so their two soft edges
                // never meet on the same pixel: there, they'd let the desktop through
                float cover = Math.Clamp(0.5f - d, 0, 1) * Math.Clamp(0.5f + din + Under, 0, 1);
                if (cover <= 0) continue;

                // the outward direction here, and how far across the band (0 outer edge, 1 inner)
                var g = new Vector2(
                    SdSquircle(p + Vector2.UnitX, bo, ro) - SdSquircle(p - Vector2.UnitX, bo, ro),
                    SdSquircle(p + Vector2.UnitY, bo, ro) - SdSquircle(p - Vector2.UnitY, bo, ro));
                g = g.LengthSquared() > 1e-8f ? Vector2.Normalize(g) : Vector2.Zero;
                float v = Math.Clamp(-d / tw, 0, 1);

                // a flat top that rolls off at both edges: outward at the outer edge, inward at the inner
                float across = 1 - 2 * v;
                float tilt = MathF.Sign(across) * MathF.Pow(MathF.Abs(across), 2.2f) * 1.2f;
                var n = new Vector3(g * MathF.Sin(tilt), MathF.Cos(tilt));

                var col = Shade(n, out float diff);

                // brushed metal: fine lines along the band, faint and slowly varying around it
                float along = MathF.Atan2(p.Y, p.X);
                float streak = (Hash(MathF.Floor(-d * 1.4f)) - 0.5f) * brushed + 0.3f * brushed * MathF.Sin(along * 9 + v * 3);
                col *= 1 + streak;

                // the antenna lines: matte, in a darker shade of the finish
                float alongLong = tall ? MathF.Abs(p.Y) : MathF.Abs(p.X);
                float acrossShort = tall ? MathF.Abs(p.X) : MathF.Abs(p.Y);
                if (acrossShort > shortHalf - tw - 1)
                {
                    float m = Math.Clamp(antennaHalf + 0.5f - MathF.Abs(alongLong - antennaAt), 0, 1);
                    if (m > 0) col = Vector3.Lerp(col, plastic * (0.7f + 0.4f * diff), m * 0.9f);
                }

                // over whatever is under it (a button's tucked-in end), premultiplied
                col = Out(Vector3.Clamp(col, Vector3.Zero, Vector3.One)) * cover;
                int i = (y * w + x) * 4;
                float keep = 1 - cover;
                px[i] = (byte)(col.Z * 255 + px[i] * keep + 0.5f);
                px[i + 1] = (byte)(col.Y * 255 + px[i + 1] * keep + 0.5f);
                px[i + 2] = (byte)(col.X * 255 + px[i + 2] * keep + 0.5f);
                px[i + 3] = (byte)(cover * 255 + px[i + 3] * keep + 0.5f);
            }
        }
        return px;
    }

    /// <summary>Signed distance to the band's outlines, with the corners Squircle draws; negative inside.</summary>
    static float SdSquircle(Vector2 p, Vector2 b, float r) => ContinuousCorner.Sd(p, b, r);

    static float Hash(float n)
    {
        float s = MathF.Sin(n * 12.9898f) * 43758.547f;
        return s - MathF.Floor(s);
    }

    static Vector3 ToVec(Color c) => new(c.R / 255f, c.G / 255f, c.B / 255f);

    static Vector3 SrgbToLinear(Vector3 c)
    {
        static float F(float v) => v <= 0.04045f ? v / 12.92f : MathF.Pow((v + 0.055f) / 1.055f, 2.4f);
        return new Vector3(F(c.X), F(c.Y), F(c.Z));
    }

    static Vector3 LinearToSrgb(Vector3 c)
    {
        static float F(float v) { v = Math.Clamp(v, 0, 1); return v <= 0.0031308f ? v * 12.92f : 1.055f * MathF.Pow(v, 1 / 2.4f) - 0.055f; }
        return new Vector3(F(c.X), F(c.Y), F(c.Z));
    }

    // ---------------------------------------------------------------- HDR displays

    // On a display in HDR mode, Windows shows ordinary (SDR) colours by the exact sRGB curve, where an
    // ordinary monitor uses gamma 2.2: dark tones come out lighter (a deep red's dark channels by
    // 40-65%), so the finishes look washed out. There, the frame's colours are drawn so that they
    // come out as on an ordinary monitor. (Windows' SDR brightness is the person's own setting: left alone.)

    bool _hdr;
    Microsoft.Graphics.Display.DisplayInformation? _display;

    void WatchDisplay()
    {
        try
        {
            _display = Microsoft.Graphics.Display.DisplayInformation.CreateForWindowId(AppWindow.Id);
            _display.AdvancedColorInfoChanged += (_, _) => _ui.TryEnqueue(UpdateHdr);  // also on moving to another display
            UpdateHdr();
        }
        catch (Exception e)
        {
            System.Diagnostics.Debug.WriteLine("display: " + e.Message);
        }
    }

    void UpdateHdr()
    {
        bool hdr;
        try
        {
            hdr = _display?.GetAdvancedColorInfo().CurrentAdvancedColorKind
                  == Microsoft.Graphics.Display.DisplayAdvancedColorKind.HighDynamicRange;
        }
        catch (Exception) { hdr = false; }
        if (hdr == _hdr) return;
        _hdr = hdr;
        ApplyFinish(_finish);  // redraws the band, with the buttons
        if (SettingsVisible) FillFinishSettings();
    }

    /// <summary>A colour as this display should be given it: as is, or on HDR, so it shows as gamma 2.2 would.</summary>
    Color ForDisplay(Color c) => _hdr
        ? Color.FromArgb(c.A, ToByte(Gamma22ToSrgb(c.R / 255f)), ToByte(Gamma22ToSrgb(c.G / 255f)), ToByte(Gamma22ToSrgb(c.B / 255f)))
        : c;

    /// <summary>The value that Windows' sRGB decoding turns into the light gamma 2.2 would give v.</summary>
    static float Gamma22ToSrgb(float v)
    {
        float linear = MathF.Pow(Math.Clamp(v, 0, 1), 2.2f);
        return linear <= 0.0031308f ? linear * 12.92f : 1.055f * MathF.Pow(linear, 1 / 2.4f) - 0.055f;
    }

    static byte ToByte(float v) => (byte)Math.Clamp(v * 255 + 0.5f, 0, 255);

    // ---------------------------------------------------------------- the screen's shape

    /// <summary>The screen (video) and this window's pages on it are rectangles; with the phone's
    /// corners as round as they are, their square corners reach past the band. Clip them to the
    /// screen, rounded: a circular corner of the same radius holds the continuous one (they meet
    /// at 45 degrees), and the body drawn over the screen gives it the exact shape.</summary>
    void ClipScreen(double sw, double sh, double sr)
    {
        try
        {
            // (A clip doesn't reach the video's swap chain, on Screen or on the panel itself: the video cuts
            // its own corners, VideoPipeline.CutCorners.)
            foreach (var element in new UIElement[] { Screen, ScreenUi })
            {
                var visual = ElementCompositionPreview.GetElementVisual(element);
                var compositor = visual.Compositor;
                var shape = compositor.CreateRoundedRectangleGeometry();
                shape.Size = new Vector2((float)sw, (float)sh);
                shape.CornerRadius = new Vector2((float)Math.Min(sr, Math.Min(sw, sh) / 2));
                visual.Clip = compositor.CreateGeometricClip(shape);
            }
        }
        catch (Exception e)
        {
            System.Diagnostics.Debug.WriteLine("screen clip: " + e.Message);
        }
    }

    // ---------------------------------------------------------------- the shadow

    SpriteVisual? _shadow;

    /// <summary>A soft shadow the phone's shape casts, a little below it. ShadowShape, under the
    /// phone, is the shape; its alpha is the shadow's mask.</summary>
    void PlaceShadow(double pw, double ph)
    {
        try
        {
            if (_shadow == null)
            {
                var compositor = ElementCompositionPreview.GetElementVisual(ShadowHost).Compositor;
                var drop = compositor.CreateDropShadow();
                drop.BlurRadius = 16;
                drop.Offset = new Vector3(0, 5, 0);
                drop.Color = Color.FromArgb(0xFF, 0, 0, 0);
                drop.Opacity = 0.5f;
                drop.Mask = ShadowShape.GetAlphaMask();
                _shadow = compositor.CreateSpriteVisual();
                _shadow.Shadow = drop;
                ElementCompositionPreview.SetElementChildVisual(ShadowHost, _shadow);
            }
            _shadow.Size = new Vector2((float)pw, (float)ph);
        }
        catch (Exception e)
        {
            System.Diagnostics.Debug.WriteLine("shadow: " + e.Message);
        }
    }
}
