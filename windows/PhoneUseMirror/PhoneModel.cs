namespace PhoneUseMirror;

/// <summary>What the band is made of: how it takes the light (MainWindow.Frame.cs).</summary>
public enum BandMaterial
{
    /// <summary>Brushed: fine lines along the band, a soft highlight.</summary>
    Titanium,
    /// <summary>Bead-blasted: satin, a broad dim highlight, no lines.</summary>
    Aluminium,
    /// <summary>Polished: mirror-like, a sharp bright highlight (stainless steel; also the Air's polished titanium).</summary>
    Steel,
}

/// <summary>One button on an upright phone's side: which side, and its top and bottom as fractions
/// of the phone's length.</summary>
public readonly record struct SideButton(bool Left, double Top, double Bottom);

/// <summary>
/// The frame of one iPhone model, by its model identifier as AirPlay reports it (phoneuse's
/// state message: "model").
///   Glass: the black border between the screen and the band, in units of the screen's width:
///     1.2 mm on the 16 Pro and 1.71 mm on the 15 Pro (reported before launch); the others are
///     estimates from photos.
///   CornerPt: the screen's corner radius in points, iOS's own (UIScreen._displayCornerRadius, as
///     listed by github.com/kylebshr/ScreenCorners), drawn as a continuous corner (ContinuousCorner).
///     The 18 Pro isn't listed there; it has the 17 Pro's body, so its 62 is assumed.
///   FinishGroup: its colours in FrameFinish, if listed.
///   Buttons: the side buttons (Action button or ring/silent switch, volume up, volume down, side
///     button), or null where there's no drawing to take them from (MainWindow's estimate instead).
///   CameraControlAt: Camera Control on the right side, or null for none.
///
/// Where the buttons are comes from Apple's dimensional drawings (developer.apple.com/accessories,
/// "Accessory Design Guidelines"): each button's centre in mm from the phone's top, and its half
/// length (the side views' "2X" figures). Apple places them by distance from the top, not by
/// proportion, so on the bigger phones (Plus, Pro Max, Air) they sit proportionally higher, and
/// Camera Control proportionally lower. Apple has no drawings online for the iPhone 11 and older.
/// </summary>
public sealed record PhoneModel(string Name, double Glass, BandMaterial Material,
    bool ActionButton, SideButton[]? Buttons, SideButton? CameraControlAt, string? FinishGroup, double CornerPt)
{
    /// <summary>One side's buttons in mm: (left side?, centre from the top, half length).</summary>
    sealed record Layout((bool Left, double Centre, double Half)[] Side);

    /// <summary>The buttons of a layout on a phone `length` mm long, and its Camera Control
    /// (centre in mm; always 8.55 mm either side of it), if any.</summary>
    static (SideButton[] Buttons, SideButton? Camera) Place(Layout layout, double length, double? camera)
    {
        SideButton At((bool left, double centre, double half) b) =>
            new(b.left, (b.centre - b.half) / length, (b.centre + b.half) / length);
        return (layout.Side.Select(At).ToArray(), camera is { } c ? At((false, c, 8.55)) : null);
    }

    // The layouts in mm, each from the drawings named (the same on every size of that year unless
    // noted). Left: Action button or ring/silent switch, volume up, volume down; right: side button.
    static Layout Buttons4(double first, double firstHalf, double up, double down, double volumeHalf, double side, double sideHalf) =>
        new(new[] { (true, first, firstHalf), (true, up, volumeHalf), (true, down, volumeHalf), (false, side, sideHalf) });

    static readonly Layout
        Pro17 = Buttons4(34.28, 3.45, 48.43, 62.63, 5.60, 55.53, 8.85),  // 17 Pro, 18 Pro
        Air = Buttons4(34.08, 3.45, 48.23, 62.43, 5.60, 55.33, 8.90),
        Iphone16 = Buttons4(34.08, 3.45, 48.23, 62.43, 5.60, 55.33, 8.85),  // 16, 16 Pro, 17
        Iphone16e = Buttons4(31.28, 3.77, 45.43, 59.63, 5.92, 52.53, 9.17),
        Pro15 = Buttons4(31.67, 3.02, 45.22, 59.42, 5.60, 56.17, 8.85),
        Iphone15 = Buttons4(32.83, 2.96, 46.27, 60.47, 5.60, 53.37, 8.85),
        Plus15 = Buttons4(31.94, 2.96, 45.39, 59.59, 5.60, 52.49, 8.85),
        Pro14 = Buttons4(31.14, 2.96, 44.59, 58.79, 5.90, 55.54, 9.15),
        Iphone14 = Buttons4(31.98, 2.96, 45.43, 59.63, 5.92, 52.53, 9.17),
        Iphone13 = Buttons4(32.22, 2.90, 45.53, 59.53, 5.82, 52.53, 9.07),  // 13, 13 Pro, 13 Pro Max
        Mini13 = Buttons4(23.37, 2.90, 36.68, 50.68, 5.82, 43.68, 9.07),
        Iphone12 = Buttons4(25.99, 2.85, 39.31, 53.31, 5.65, 46.31, 8.90),  // 12, 12 Pro
        ProMax12 = Buttons4(33.34, 2.85, 46.65, 60.65, 5.65, 53.65, 8.90),
        Mini12 = Buttons4(23.37, 2.85, 36.68, 50.68, 5.65, 43.68, 8.90);

    /// <summary>A model with its buttons placed: its layout on a phone `length` mm long, and
    /// Camera Control's centre in mm, if it has one.</summary>
    static PhoneModel Make(string name, double glass, BandMaterial material, bool action, string? finish, double corner,
        Layout layout, double length, double? camera = null)
    {
        var (buttons, cc) = Place(layout, length, camera);
        return new(name, glass, material, action, buttons, cc, finish, corner);
    }

    /// <summary>No phone has mirrored yet, or one this table doesn't know: a current iPhone (the 17 Pro's buttons).</summary>
    public static readonly PhoneModel Default =
        Make("iPhone", 0.018, BandMaterial.Titanium, true, null, 62, Pro17, 150.01, 98.40);

    static readonly Dictionary<string, PhoneModel> Models = Build();

    public static PhoneModel For(string? identifier) =>
        identifier != null && Models.TryGetValue(identifier, out var m) ? m : Default;

    static Dictionary<string, PhoneModel> Build()
    {
        var d = new Dictionary<string, PhoneModel>();
        void Add(string ids, PhoneModel m) { foreach (var id in ids.Split(' ')) d["iPhone" + id] = m; }

        const BandMaterial Ti = BandMaterial.Titanium, Al = BandMaterial.Aluminium, St = BandMaterial.Steel;
        // (name, glass, band, Action button?, finishes, corner, button layout, length mm, Camera Control's centre mm)
        Add("19,2", Make("iPhone 18 Pro", 0.018, Al, true, "iPhone 18 Pro", 62, Pro17, 150.01, 98.40));
        Add("19,3 19,7", Make("iPhone 18 Pro Max", 0.018, Al, true, "iPhone 18 Pro", 62, Pro17, 163.43, 111.82));
        Add("18,1", Make("iPhone 17 Pro", 0.018, Al, true, "iPhone 17 Pro", 62, Pro17, 150.01, 98.40));
        Add("18,2", Make("iPhone 17 Pro Max", 0.018, Al, true, "iPhone 17 Pro", 62, Pro17, 163.43, 111.82));
        Add("18,3", Make("iPhone 17", 0.020, Al, true, "iPhone 17", 62, Iphone16, 149.61, 98.20));
        Add("18,4", Make("iPhone Air", 0.020, St, true, "iPhone Air", 62, Air, 156.18, 104.77));  // titanium, but mirror-polished
        Add("17,1", Make("iPhone 16 Pro", 0.018, Ti, true, "iPhone 16 Pro", 62, Iphone16, 149.61, 98.19));
        Add("17,2", Make("iPhone 16 Pro Max", 0.018, Ti, true, "iPhone 16 Pro", 62, Iphone16, 163.03, 111.62));
        Add("17,3", Make("iPhone 16", 0.027, Al, true, "iPhone 16", 55, Iphone16, 147.64, 96.23));
        Add("17,4", Make("iPhone 16 Plus", 0.027, Al, true, "iPhone 16", 55, Iphone16, 160.89, 109.48));
        Add("17,5", Make("iPhone 16e", 0.030, Al, true, "iPhone 16e", 47.33, Iphone16e, 146.71));
        Add("16,1", Make("iPhone 15 Pro", 0.026, Ti, true, "iPhone 15 Pro", 55, Pro15, 146.61));
        Add("16,2", Make("iPhone 15 Pro Max", 0.026, Ti, true, "iPhone 15 Pro", 55, Pro15, 159.86));
        Add("15,4", Make("iPhone 15", 0.028, Al, false, "iPhone 15", 55, Iphone15, 147.64));
        Add("15,5", Make("iPhone 15 Plus", 0.028, Al, false, "iPhone 15", 55, Plus15, 160.89));
        Add("15,2", Make("iPhone 14 Pro", 0.029, St, false, "iPhone 14 Pro", 55, Pro14, 147.46));
        Add("15,3", Make("iPhone 14 Pro Max", 0.029, St, false, "iPhone 14 Pro", 55, Pro14, 160.71));
        Add("14,7", Make("iPhone 14", 0.031, Al, false, null, 47.33, Iphone14, 146.71));
        Add("14,8", Make("iPhone 14 Plus", 0.031, Al, false, null, 53.33, Iphone14, 160.84));
        Add("14,2", Make("iPhone 13 Pro", 0.031, St, false, null, 47.33, Iphone13, 146.71));
        Add("14,3", Make("iPhone 13 Pro Max", 0.031, St, false, null, 53.33, Iphone13, 160.84));
        Add("14,5", Make("iPhone 13", 0.031, Al, false, null, 47.33, Iphone13, 146.70));
        Add("14,4", Make("iPhone 13 mini", 0.031, Al, false, null, 44, Mini13, 131.50));
        Add("13,3", Make("iPhone 12 Pro", 0.032, St, false, null, 47.33, Iphone12, 146.71));
        Add("13,4", Make("iPhone 12 Pro Max", 0.032, St, false, null, 53.33, ProMax12, 160.84));
        Add("13,2", Make("iPhone 12", 0.032, Al, false, null, 47.33, Iphone12, 146.71));
        Add("13,1", Make("iPhone 12 mini", 0.032, Al, false, null, 44, Mini12, 131.50));
        // no drawings online: MainWindow's estimated buttons
        Add("12,3 12,5", new("iPhone 11 Pro", 0.032, St, false, null, null, null, 39));
        Add("12,1", new("iPhone 11", 0.060, Al, false, null, null, null, 41.5));
        Add("11,8", new("iPhone XR", 0.060, Al, false, null, null, null, 41.5));
        Add("11,2 11,4 11,6", new("iPhone XS", 0.032, St, false, null, null, null, 39));
        Add("10,3 10,6", new("iPhone X", 0.032, St, false, null, null, null, 39));
        return d;
    }
}
