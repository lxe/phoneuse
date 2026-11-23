using Windows.UI;

namespace PhoneUseMirror;

/// <summary>
/// The colour of the phone's band and side buttons (Settings › Appearance): real iPhone finishes.
/// Each base colour is AppleDB's for that finish (https://appledb.dev, deviceFiles/iPhone/&lt;identifier&gt;.json
/// in github.com/littlebyteorg/appledb); the band's light and shade are derived from it.
/// </summary>
public sealed record FrameFinish(string Id, string Name, string Group,
    Color Light, Color Dark, Color Mid, Color ButtonEdge, Color ButtonMid, Color Base)
{
    /// <summary>The colour shown on its swatch: the finish itself.</summary>
    public Color Swatch => Base;

    /// <summary>The band tone referenced from Apple's product photos. The AppleDB swatch can differ
    /// from the band, especially when the back is matte and the sides are polished steel.</summary>
    public Color? MeasuredBand { get; init; }

    /// <summary>The band's tone the renderer starts from: measured, or until then estimated from the
    /// swatch (about 40% of its light, the average of the measured ones).</summary>
    public Color Band => MeasuredBand ?? EstimateBand(Base);

    static Color EstimateBand(Color c)
    {
        static double Lin(byte v) { double s = v / 255.0; return s <= 0.04045 ? s / 12.92 : Math.Pow((s + 0.055) / 1.055, 2.4); }
        static byte Srgb(double l) { l = Math.Clamp(l, 0, 1); double s = l <= 0.0031308 ? l * 12.92 : 1.055 * Math.Pow(l, 1 / 2.4) - 0.055; return (byte)Math.Round(s * 255); }
        return Color.FromArgb(0xFF, Srgb(Lin(c.R) * 0.4), Srgb(Lin(c.G) * 0.4), Srgb(Lin(c.B) * 0.4));
    }

    public const string DefaultId = "graphite";

    /// <summary>A finish from one base colour. Base is exact: the lit band (MainWindow.Frame.cs) shows it
    /// wherever the metal faces you. The others are only for the plain band drawn if that can't be.</summary>
    static FrameFinish From(string id, string name, string group, uint rgb)
    {
        var c = Color.FromArgb(0xFF, (byte)(rgb >> 16), (byte)(rgb >> 8), (byte)rgb);
        var white = Color.FromArgb(0xFF, 0xFF, 0xFF, 0xFF);
        var black = Color.FromArgb(0xFF, 0, 0, 0);
        return new FrameFinish(id, name, group,
            Light: Lerp(c, white, 0.28),
            Dark: Lerp(c, black, 0.48),
            Mid: Lerp(c, black, 0.12),
            ButtonEdge: Lerp(c, black, 0.40),
            ButtonMid: Lerp(c, white, 0.18),
            Base: c);
    }

    static Color Hex(uint rgb) => Color.FromArgb(0xFF, (byte)(rgb >> 16), (byte)(rgb >> 8), (byte)rgb);

    internal static Color Lerp(Color a, Color b, double t) => Color.FromArgb(0xFF,
        (byte)Math.Round(a.R + (b.R - a.R) * t),
        (byte)Math.Round(a.G + (b.G - a.G) * t),
        (byte)Math.Round(a.B + (b.B - a.B) * t));

    public static readonly IReadOnlyList<FrameFinish> All = new[]
    {
        // the frame this window always had
        new FrameFinish(DefaultId, "Graphite", "Phone Use",
            Hex(0x6C6C72), Hex(0x2E2E33), Hex(0x55555B), Hex(0x3A3A3E), Hex(0x77777D), Hex(0x55555B)),

        // Bands measured in Apple's newsroom photos (apple.com/newsroom, "Apple debuts iPhone 18 Pro and
        // iPhone 18 Pro Max"): Burgundy on the phone facing the camera; the others from the side bands of
        // the colour lineup, scaled as Burgundy's side band is to its front one (0.8 of the light).
        From("18pro-burgundy", "Burgundy", "iPhone 18 Pro", 0x4D1822) with { MeasuredBand = Hex(0x3E252B) },
        From("18pro-glacier", "Glacier", "iPhone 18 Pro", 0xE8F2FE) with { MeasuredBand = Hex(0x737F92) },
        From("18pro-silver", "Silver", "iPhone 18 Pro", 0xF7F7F7) with { MeasuredBand = Hex(0xA9A9A7) },
        From("18pro-black", "Black", "iPhone 18 Pro", 0x3C3C3C) with { MeasuredBand = Hex(0x1A1A1C) },

        From("duo-night-sky", "Night Sky", "iPhone Duo", 0x394452),
        From("duo-star-white", "Star White", "iPhone Duo", 0xF7F6F5),

        From("17pro-cosmic-orange", "Cosmic Orange", "iPhone 17 Pro", 0xF8833E) with { MeasuredBand = Hex(0xC67643) },
        From("17pro-deep-blue", "Deep Blue", "iPhone 17 Pro", 0x4A5680) with { MeasuredBand = Hex(0x3A4053) },
        From("17pro-silver", "Silver", "iPhone 17 Pro", 0xE7E7E7) with { MeasuredBand = Hex(0xACACAA) },

        From("air-space-black", "Space Black", "iPhone Air", 0x0E0E0E) with { MeasuredBand = Hex(0x17171A) },
        From("air-cloud-white", "Cloud White", "iPhone Air", 0xFCFCFC) with { MeasuredBand = Hex(0xD0D0D0) },
        From("air-light-gold", "Light Gold", "iPhone Air", 0xFAF2E5) with { MeasuredBand = Hex(0xD4CFC4) },
        From("air-sky-blue", "Sky Blue", "iPhone Air", 0xE5F2FA) with { MeasuredBand = Hex(0xC7D2DC) },

        From("17-black", "Black", "iPhone 17", 0x464747) with { MeasuredBand = Hex(0x505153) },
        From("17-lavender", "Lavender", "iPhone 17", 0xE7D7F2) with { MeasuredBand = Hex(0xB9B3C0) },
        From("17-mist-blue", "Mist Blue", "iPhone 17", 0xA6BDDD) with { MeasuredBand = Hex(0x96A8C2) },
        From("17-sage", "Sage", "iPhone 17", 0xBAC79C) with { MeasuredBand = Hex(0x9AA887) },
        From("17-white", "White", "iPhone 17", 0xF7F7F5) with { MeasuredBand = Hex(0xCACAC8) },

        From("17e-black", "Black", "iPhone 17e", 0x404040),
        From("17e-soft-pink", "Soft Pink", "iPhone 17e", 0xFCE7E6),
        From("17e-white", "White", "iPhone 17e", 0xF6F6F4),

        From("16pro-black", "Black Titanium", "iPhone 16 Pro", 0x4F4F4F) with { MeasuredBand = Hex(0x232323) },
        From("16pro-white", "White Titanium", "iPhone 16 Pro", 0xECECEA) with { MeasuredBand = Hex(0xA6A29F) },
        From("16pro-natural", "Natural Titanium", "iPhone 16 Pro", 0xCBC7C0) with { MeasuredBand = Hex(0x8A857C) },
        From("16pro-desert", "Desert Titanium", "iPhone 16 Pro", 0xEBD8C8) with { MeasuredBand = Hex(0x685041) },

        From("16-black", "Black", "iPhone 16", 0x464747) with { MeasuredBand = Hex(0x2E2F33) },
        From("16-pink", "Pink", "iPhone 16", 0xF1A8D8) with { MeasuredBand = Hex(0xA9628D) },
        From("16-teal", "Teal", "iPhone 16", 0xAFD4D2) with { MeasuredBand = Hex(0x5A7B80) },
        From("16-ultramarine", "Ultramarine", "iPhone 16", 0x94A6F1) with { MeasuredBand = Hex(0x5D6EAD) },
        From("16-white", "White", "iPhone 16", 0xF7F7F5) with { MeasuredBand = Hex(0xC1C1BF) },

        From("16e-black", "Black", "iPhone 16e", 0x404040),
        From("16e-white", "White", "iPhone 16e", 0xF6F6F4),

        From("15pro-black", "Black Titanium", "iPhone 15 Pro", 0x464646) with { MeasuredBand = Hex(0x242529) },
        From("15pro-blue", "Blue Titanium", "iPhone 15 Pro", 0x545A66) with { MeasuredBand = Hex(0x20242E) },
        From("15pro-natural", "Natural Titanium", "iPhone 15 Pro", 0xA6A29B) with { MeasuredBand = Hex(0x706C64) },
        From("15pro-white", "White Titanium", "iPhone 15 Pro", 0xEEEAE6) with { MeasuredBand = Hex(0x93918C) },

        From("15-black", "Black", "iPhone 15", 0x4E4F4E) with { MeasuredBand = Hex(0x393D3F) },
        From("15-blue", "Blue", "iPhone 15", 0xF3F8F8) with { MeasuredBand = Hex(0xB7BEC1) },
        From("15-green", "Green", "iPhone 15", 0xF3F8E8) with { MeasuredBand = Hex(0xBAC4B5) },
        From("15-pink", "Pink", "iPhone 15", 0xFCE8E8) with { MeasuredBand = Hex(0xD2B8BB) },
        From("15-yellow", "Yellow", "iPhone 15", 0xFCFBE4) with { MeasuredBand = Hex(0xD0CCAF) },

        // Middle of the flat sides in Apple's iPhone 14 Pro Newsroom colour carousel.
        From("14pro-deep-purple", "Deep Purple", "iPhone 14 Pro", 0x595261) with { MeasuredBand = Hex(0x8F819B) },
        From("14pro-gold", "Gold", "iPhone 14 Pro", 0xF3E6CC) with { MeasuredBand = Hex(0xF4E6CB) },
        From("14pro-silver", "Silver", "iPhone 14 Pro", 0xF0F2F2) with { MeasuredBand = Hex(0xEBECEE) },
        From("14pro-space-black", "Space Black", "iPhone 14 Pro", 0x3B3937) with { MeasuredBand = Hex(0x2E2A27) },

        From("14-blue", "Blue", "iPhone 14", 0xA8B8C8),
        From("14-midnight", "Midnight", "iPhone 14", 0x192928),
        From("14-purple", "Purple", "iPhone 14", 0xE8D8E8),
        From("14-red", "(PRODUCT)RED", "iPhone 14", 0xF80628),
        From("14-starlight", "Starlight", "iPhone 14", 0xF8F8E7),
        From("14-yellow", "Yellow", "iPhone 14", 0xF8D868),

        // Band tones estimated from the Newsroom lineup images, accounting for reflections between phones.
        From("13pro-alpine-green", "Alpine Green", "iPhone 13 Pro", 0x546554) with { MeasuredBand = Hex(0x68786D) },
        From("13pro-gold", "Gold", "iPhone 13 Pro", 0xF6E4C7) with { MeasuredBand = Hex(0xC1A67E) },
        From("13pro-graphite", "Graphite", "iPhone 13 Pro", 0x4C4C4A) with { MeasuredBand = Hex(0x737470) },
        From("13pro-sierra-blue", "Sierra Blue", "iPhone 13 Pro", 0x93A9C0) with { MeasuredBand = Hex(0x859BB4) },
        From("13pro-silver", "Silver", "iPhone 13 Pro", 0xF1F1E7) with { MeasuredBand = Hex(0xDFE0DF) },

        From("13-blue", "Blue", "iPhone 13", 0x235D7B),
        From("13-green", "Green", "iPhone 13", 0x334434),
        From("13-midnight", "Midnight", "iPhone 13", 0x151D23),
        From("13-pink", "Pink", "iPhone 13", 0xFCDDD8),
        From("13-red", "(PRODUCT)RED", "iPhone 13", 0x96060D),
        From("13-starlight", "Starlight", "iPhone 13", 0xF9F4EE),

        // Estimates from the iPhone 12 Pro press images and finish swatches.
        From("12pro-gold", "Gold", "iPhone 12 Pro", 0xFCEFD9) with { MeasuredBand = Hex(0xBFA475) },
        From("12pro-graphite", "Graphite", "iPhone 12 Pro", 0x494846) with { MeasuredBand = Hex(0x666563) },
        From("12pro-pacific-blue", "Pacific Blue", "iPhone 12 Pro", 0x304B55) with { MeasuredBand = Hex(0x57788E) },
        From("12pro-silver", "Silver", "iPhone 12 Pro", 0xF3F4F0) with { MeasuredBand = Hex(0xDBDDDE) },

        From("12-black", "Black", "iPhone 12", 0x221C24),
        From("12-blue", "Blue", "iPhone 12", 0x033356),
        From("12-green", "Green", "iPhone 12", 0xDDF4D8),
        From("12-purple", "Purple", "iPhone 12", 0xAFA9DF),
        From("12-red", "(PRODUCT)RED", "iPhone 12", 0xDF3434),
        From("12-white", "White", "iPhone 12", 0xFDF4F4),

        // The curved iPhone 11 Pro bands catch several reflections in the Newsroom lineup image.
        From("11pro-gold", "Gold", "iPhone 11 Pro", 0xF4D2B9) with { MeasuredBand = Hex(0xDAB394) },
        From("11pro-midnight-green", "Midnight Green", "iPhone 11 Pro", 0x414A46) with { MeasuredBand = Hex(0x597069) },
        From("11pro-silver", "Silver", "iPhone 11 Pro", 0xE0E0D8) with { MeasuredBand = Hex(0xDCDDDC) },
        From("11pro-space-gray", "Space Gray", "iPhone 11 Pro", 0x3A3839) with { MeasuredBand = Hex(0x626062) },

        From("11-black", "Black", "iPhone 11", 0x1E1E1E),
        From("11-green", "Green", "iPhone 11", 0xABDDCA),
        From("11-purple", "Purple", "iPhone 11", 0xD7D1E0),
        From("11-red", "(PRODUCT)RED", "iPhone 11", 0xB6062A),
        From("11-white", "White", "iPhone 11", 0xFBF9F2),
        From("11-yellow", "Yellow", "iPhone 11", 0xFEE581),
    };

    public static FrameFinish Get(string? id) => All.FirstOrDefault(f => f.Id == id) ?? All[0];
}
