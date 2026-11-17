using Microsoft.Win32;

namespace PhoneUseMirror;

/// <summary>
/// "Start with Windows": a per-user Run entry (HKCU\...\Run, no admin) that starts
/// this app at sign-in with --tray: phoneuse starts in the background and the
/// window waits in the tray.
/// </summary>
public static class Startup
{
    const string RunKey = @"Software\Microsoft\Windows\CurrentVersion\Run";
    const string ValueName = "Phone Use";
    public const string TrayFlag = "--tray";

    static string Command => $"\"{Environment.ProcessPath}\" {TrayFlag}";

    public static bool Enabled
    {
        get
        {
            using var k = Registry.CurrentUser.OpenSubKey(RunKey);
            return k?.GetValue(ValueName) is string s && s.Length > 0;
        }
        set
        {
            using var k = Registry.CurrentUser.CreateSubKey(RunKey);
            if (value) k.SetValue(ValueName, Command);
            else k.DeleteValue(ValueName, false);
        }
    }

    /// <summary>Keep the entry pointing at this exe if it moved (a rebuilt checkout elsewhere).</summary>
    public static void Refresh()
    {
        try
        {
            using var k = Registry.CurrentUser.OpenSubKey(RunKey, true);
            if (k?.GetValue(ValueName) is string s && s != Command) k.SetValue(ValueName, Command);
        }
        catch (Exception) { }
    }
}
