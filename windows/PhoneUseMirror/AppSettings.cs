using System.Text.Json.Nodes;

namespace PhoneUseMirror;

/// <summary>
/// This window's own settings (not phoneuse's, which the server keeps):
/// %LOCALAPPDATA%\phoneuse\PhoneUseMirror.json.
/// </summary>
public static class AppSettings
{
    static readonly string FilePath = Backend.Portable
        ? Path.Combine(Backend.PortableData, "PhoneUseMirror.json")
        : Path.Combine(Environment.GetFolderPath(Environment.SpecialFolder.LocalApplicationData), "phoneuse", "PhoneUseMirror.json");

    static JsonObject Load()
    {
        try { return JsonNode.Parse(File.ReadAllText(FilePath)) as JsonObject ?? new JsonObject(); }
        catch (Exception) { return new JsonObject(); }
    }

    static void Save(JsonObject o)
    {
        try
        {
            Directory.CreateDirectory(Path.GetDirectoryName(FilePath)!);
            File.WriteAllText(FilePath, o.ToJsonString());
        }
        catch (Exception) { }
    }

    /// <summary>"Agent control requires pairing" was closed: it stays gone for good.</summary>
    public static bool PairingBarDismissed
    {
        get => (bool?)Load()["pairingBarDismissed"] ?? false;
        set
        {
            var o = Load();
            o["pairingBarDismissed"] = value;
            Save(o);
        }
    }

    /// <summary>The first-time setup checklist was finished or skipped: it no longer opens by itself.</summary>
    public static bool SetupDone
    {
        get => (bool?)Load()["setupDone"] ?? false;
        set
        {
            var o = Load();
            o["setupDone"] = value;
            Save(o);
        }
    }

    /// <summary>Look for a newer release on GitHub now and then (Updater).</summary>
    public static bool AutoUpdate
    {
        get => (bool?)Load()["autoUpdate"] ?? true;
        set
        {
            var o = Load();
            o["autoUpdate"] = value;
            Save(o);
        }
    }

    /// <summary>The release whose "update available" notice was closed: it isn't shown again for that one.</summary>
    public static string? UpdateDismissed
    {
        get => (string?)Load()["updateDismissed"];
        set
        {
            var o = Load();
            o["updateDismissed"] = value;
            Save(o);
        }
    }

    /// <summary>The window is pinned above the tray icon (MainWindow.Snap.cs); on by default.</summary>
    public static bool Snapped
    {
        get => (bool?)Load()["snapped"] ?? true;
        set => Set("snapped", value);
    }

    /// <summary>Where it's pinned, in DIPs: along the taskbar from the icon's centre.</summary>
    public static double SnapAlong
    {
        get => Number("snapAlong") ?? 0;
        set => Set("snapAlong", value);
    }

    /// <summary>Where it's pinned, in DIPs: away from the taskbar. None yet: the default.</summary>
    public static double? SnapGap
    {
        get => Number("snapGap");
        set => Set("snapGap", value);
    }

    /// <summary>Where it was last left when not pinned (physical pixels).</summary>
    public static Windows.Graphics.PointInt32? FloatPosition
    {
        get => Number("floatX") is { } x && Number("floatY") is { } y ? new((int)x, (int)y) : null;
        set
        {
            var o = Load();
            o["floatX"] = value?.X;
            o["floatY"] = value?.Y;
            Save(o);
        }
    }

    static double? Number(string key)
    {
        try { return (double?)Load()[key]; }
        catch (Exception) { return null; }
    }

    static void Set(string key, JsonNode? value)
    {
        var o = Load();
        o[key] = value;
        Save(o);
    }

    /// <summary>The agent glow around the phone (AgentGlow); on by default.</summary>
    public static bool GlowOn
    {
        get => (bool?)Load()["glowOn"] ?? true;
        set => Set("glowOn", value);
    }

    public static readonly GlowLook DefaultGlowLook = new(1f, 1f, 0.4f, true);

    /// <summary>The Aurora glow's tuning (Settings › Agent glow).</summary>
    public static GlowLook GlowLook
    {
        get
        {
            var d = DefaultGlowLook;
            if (Load()["glow"] is not JsonObject g) return d;
            try
            {
                return new GlowLook(
                    Math.Clamp((float?)g["intensity"] ?? d.Intensity, 0.2f, 1.6f),
                    Math.Clamp((float?)g["speed"] ?? d.Speed, 0.2f, 3f),
                    Math.Clamp((float?)g["size"] ?? d.Size, 0.3f, 2.5f),
                    (bool?)g["bleed"] ?? d.Bleed);
            }
            catch (Exception) { return d; }
        }
        set => Set("glow", new JsonObject
        {
            ["intensity"] = Math.Round(value.Intensity, 2),
            ["speed"] = Math.Round(value.Speed, 2),
            ["size"] = Math.Round(value.Size, 2),
            ["bleed"] = value.Bleed,
        });
    }

    /// <summary>The phone frame's colour (Settings › Appearance): a FrameFinish id.</summary>
    public static string FrameFinish
    {
        get => (string?)Load()["frameFinish"] ?? PhoneUseMirror.FrameFinish.DefaultId;
        set => Set("frameFinish", value);
    }

    /// <summary>The setup checklist's "Turn on AssistiveTouch" step was ticked off by hand.</summary>
    public static bool AssistiveTouchDone
    {
        get => (bool?)Load()["assistiveTouchDone"] ?? false;
        set
        {
            var o = Load();
            o["assistiveTouchDone"] = value;
            Save(o);
        }
    }
}
