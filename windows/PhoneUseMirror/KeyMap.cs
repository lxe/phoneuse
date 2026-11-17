namespace PhoneUseMirror;

/// <summary>
/// Physical keys (PC/AT set-1 scan codes, as Windows reports them) to USB HID
/// usages, the same table the browser mirror builds from KeyboardEvent.code, so
/// the phone gets the key at that position whatever the PC's keyboard layout.
///
/// As in the browser mirror on Windows, Ctrl is the phone's Cmd (Ctrl+C, Ctrl+V,
/// Ctrl+Space for Spotlight) and the Windows key is the phone's Ctrl.
/// F1-F4 press the phone's own buttons for as long as they're held.
/// </summary>
public static class KeyMap
{
    // HID modifier bits: LCtrl 0x01, LShift 0x02, LAlt 0x04, LGUI(Cmd) 0x08, then right-hand x16.
    public static int? Modifier(uint scan, bool extended) => (scan, extended) switch
    {
        (0x1D, false) => 0x08, // Left Ctrl  -> Left Cmd
        (0x1D, true) => 0x80,  // Right Ctrl -> Right Cmd
        (0x2A, _) => 0x02,     // Left Shift
        (0x36, _) => 0x20,     // Right Shift
        (0x38, false) => 0x04, // Left Alt   -> Left Option
        (0x38, true) => 0x40,  // Right Alt  -> Right Option
        (0x5B, _) => 0x01,     // Left Win   -> Left Ctrl
        (0x5C, _) => 0x10,     // Right Win  -> Right Ctrl
        _ => null,
    };

    public static string? Consumer(uint scan, bool extended) => extended ? null : scan switch
    {
        0x3B => "siri",   // F1
        0x3C => "home",   // F2
        0x3D => "search", // F3
        0x3E => "eject",  // F4: the on-screen keyboard
        _ => null,
    };

    static readonly Dictionary<uint, int> Plain = new()
    {
        [0x01] = 41, // Escape
        [0x02] = 30, [0x03] = 31, [0x04] = 32, [0x05] = 33, [0x06] = 34,
        [0x07] = 35, [0x08] = 36, [0x09] = 37, [0x0A] = 38, [0x0B] = 39, // 1..9, 0
        [0x0C] = 45, [0x0D] = 46, // - =
        [0x0E] = 42, // Backspace
        [0x0F] = 43, // Tab
        [0x10] = 20, [0x11] = 26, [0x12] = 8, [0x13] = 21, [0x14] = 23,
        [0x15] = 28, [0x16] = 24, [0x17] = 12, [0x18] = 18, [0x19] = 19, // QWERTYUIOP
        [0x1A] = 47, [0x1B] = 48, // [ ]
        [0x1C] = 40, // Enter
        [0x1E] = 4, [0x1F] = 22, [0x20] = 7, [0x21] = 9, [0x22] = 10,
        [0x23] = 11, [0x24] = 13, [0x25] = 14, [0x26] = 15, // ASDFGHJKL
        [0x27] = 51, [0x28] = 52, [0x29] = 53, // ; ' `
        [0x2B] = 49, // backslash
        [0x2C] = 29, [0x2D] = 27, [0x2E] = 6, [0x2F] = 25, [0x30] = 5,
        [0x31] = 17, [0x32] = 16, // ZXCVBNM
        [0x33] = 54, [0x34] = 55, [0x35] = 56, // , . /
        [0x39] = 44, // Space
        [0x3A] = 57, // Caps Lock
        [0x3F] = 62, [0x40] = 63, [0x41] = 64, [0x42] = 65, [0x43] = 66, // F5..F9
        [0x44] = 67, [0x57] = 68, [0x58] = 69, // F10..F12
        [0x56] = 100, // the ISO key next to Left Shift
    };

    static readonly Dictionary<uint, int> Extended = new()
    {
        [0x1C] = 40, // keypad Enter
        [0x47] = 74, // Home
        [0x48] = 82, // Up
        [0x49] = 75, // Page Up
        [0x4B] = 80, // Left
        [0x4D] = 79, // Right
        [0x4F] = 77, // End
        [0x50] = 81, // Down
        [0x51] = 78, // Page Down
        [0x52] = 73, // Insert
        [0x53] = 76, // Delete
    };

    public static int? Usage(uint scan, bool extended) =>
        (extended ? Extended : Plain).TryGetValue(scan, out var u) ? u : null;
}
