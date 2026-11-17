using System.Diagnostics;
using System.Net;
using System.Net.Http.Headers;
using System.Reflection;
using System.Security.Cryptography;
using System.Text.Json.Nodes;

namespace PhoneUseMirror;

/// <summary>
/// Updates from the GitHub releases the windows-app workflow attaches its builds to
/// (.github/workflows/windows-app.yml). Asks GitHub for the latest release, which is never a
/// draft or a prerelease, so a release only reaches people once it's published.
///   installed  downloads PhoneUse-*-win-x64-setup.exe, checks its SHA-256 against the digest
///              GitHub keeps for the asset, and runs it silently over this install
///              (PhoneUse.iss: /UPDATE=1 starts the window again afterwards)
///   portable   only says so: Download opens the release's page
///   checkout   never checks (git is how a checkout updates)
/// The repository is the one the build came from (-p:UpdateRepo, package.ps1 passes
/// GITHUB_REPOSITORY in CI), so a fork's builds update from the fork.
/// </summary>
public static class Updater
{
    public enum Kind { None, Installed, Portable }

    public sealed record Release(Version Version, string Tag, string PageUrl, string? SetupUrl, string? SetupName,
                                 string? SetupSha256, long SetupSize);

    public static string Repo { get; } =
        typeof(Updater).Assembly.GetCustomAttributes<AssemblyMetadataAttribute>()
            .FirstOrDefault(a => a.Key == "UpdateRepo")?.Value is { Length: > 0 } r ? r : "lxe/phoneuse";

    public static Version Current { get; } = Normalize(typeof(Updater).Assembly.GetName().Version ?? new Version(0, 0));

    public static string CurrentText => Current.Revision > 0 ? Current.ToString(4) : Current.ToString(3);

    /// <summary>Installed: Inno Setup's uninstaller sits next to the exe.</summary>
    public static Kind Mode { get; } =
        !Backend.Bundled ? Kind.None
        : Backend.Portable ? Kind.Portable
        : File.Exists(Path.Combine(AppContext.BaseDirectory, "unins000.exe")) ? Kind.Installed
        : Kind.None;

    static readonly HttpClient Http = MakeClient();

    static HttpClient MakeClient()
    {
        var h = new HttpClient { Timeout = TimeSpan.FromMinutes(30) };  // the download is a few hundred MB
        h.DefaultRequestHeaders.UserAgent.Add(new ProductInfoHeaderValue("PhoneUse-Windows", CurrentText));
        return h;
    }

    static Version Normalize(Version v) => new(v.Major, v.Minor, Math.Max(v.Build, 0), Math.Max(v.Revision, 0));

    /// <summary>"v0.2.1" → 0.2.1.0; null for a tag that isn't a version.</summary>
    public static Version? ParseTag(string tag)
    {
        var s = tag.Trim().TrimStart('v', 'V');
        var cut = s.IndexOfAny(['-', '+']);
        if (cut >= 0) s = s[..cut];
        if (!s.Contains('.')) s += ".0";
        return Version.TryParse(s, out var v) ? Normalize(v) : null;
    }

    /// <summary>The newest published release with a Windows build, when it's newer than this one;
    /// else null (also when there's none yet). Throws when GitHub can't be asked.
    /// Not /releases/latest: the repository's releases also carry the Mac, Linux and bridge
    /// builds, and the latest one may have no Windows build at all.</summary>
    public static async Task<Release?> CheckAsync(CancellationToken ct = default)
    {
        using var req = new HttpRequestMessage(HttpMethod.Get, $"https://api.github.com/repos/{Repo}/releases?per_page=30");
        req.Headers.Accept.Add(new MediaTypeWithQualityHeaderValue("application/vnd.github+json"));
        req.Headers.Add("X-GitHub-Api-Version", "2022-11-28");
        using var res = await Http.SendAsync(req, ct);
        if (res.StatusCode == HttpStatusCode.NotFound) throw new HttpRequestException($"no GitHub repository {Repo}");
        if (res.StatusCode == HttpStatusCode.Forbidden || (int)res.StatusCode == 429)
            throw new HttpRequestException("GitHub is limiting requests from this network; try again later");
        res.EnsureSuccessStatusCode();
        var list = JsonNode.Parse(await res.Content.ReadAsStringAsync(ct)) as JsonArray
                   ?? throw new HttpRequestException("GitHub's answer wasn't a list of releases");
        return Newest(list);
    }

    /// <summary>The newest release in GitHub's list that is published (not a draft or a prerelease),
    /// tagged with a version, has the Windows installer, and is newer than this build.</summary>
    public static Release? Newest(JsonArray releases)
    {
        Release? best = null;
        foreach (var j in releases.OfType<JsonObject>())
        {
            if ((bool?)j["draft"] == true || (bool?)j["prerelease"] == true) continue;
            var tag = (string?)j["tag_name"] ?? "";
            var version = ParseTag(tag);
            if (version == null || version <= Current || (best != null && version <= best.Version)) continue;
            foreach (var a in j["assets"]?.AsArray() ?? [])
            {
                var n = (string?)a?["name"] ?? "";
                if (!n.EndsWith("-win-x64-setup.exe", StringComparison.OrdinalIgnoreCase)) continue;
                var digest = (string?)a!["digest"];  // "sha256:<hex>", kept by GitHub for every uploaded asset
                var sha = digest != null && digest.StartsWith("sha256:", StringComparison.OrdinalIgnoreCase)
                    ? digest[7..].ToLowerInvariant() : null;
                var page = (string?)j["html_url"] ?? $"https://github.com/{Repo}/releases/tag/{tag}";
                long.TryParse(a["size"]?.ToString(), out var size);
                best = new Release(version, tag, page, (string?)a["browser_download_url"], n, sha, size);
                break;
            }
        }
        return best;
    }

    /// <summary>Whether this copy can install the release itself (else Download opens its page).</summary>
    public static bool CanInstall(Release r) => Mode == Kind.Installed && r.SetupUrl != null && r.SetupSha256 != null;

    static string Folder => Path.Combine(Path.GetTempPath(), "phoneuse-update");

    /// <summary>Downloads the release's installer and checks its SHA-256; progress is 0..1.</summary>
    public static async Task<string> DownloadAsync(Release r, IProgress<double>? progress, CancellationToken ct = default)
    {
        if (!CanInstall(r)) throw new InvalidOperationException("this release has no installer to download");
        try { if (Directory.Exists(Folder)) Directory.Delete(Folder, true); } catch (Exception) { }
        Directory.CreateDirectory(Folder);
        var path = Path.Combine(Folder, Path.GetFileName(r.SetupName!));

        using (var res = await Http.GetAsync(r.SetupUrl, HttpCompletionOption.ResponseHeadersRead, ct))
        {
            res.EnsureSuccessStatusCode();
            long total = res.Content.Headers.ContentLength ?? r.SetupSize;
            await using var src = await res.Content.ReadAsStreamAsync(ct);
            await using var dst = File.Create(path + ".part");
            using var sha = IncrementalHash.CreateHash(HashAlgorithmName.SHA256);
            var buf = new byte[1 << 16];
            long got = 0;
            int n;
            while ((n = await src.ReadAsync(buf, ct)) > 0)
            {
                await dst.WriteAsync(buf.AsMemory(0, n), ct);
                sha.AppendData(buf, 0, n);
                got += n;
                if (total > 0) progress?.Report((double)got / total);
            }
            var hex = Convert.ToHexStringLower(sha.GetHashAndReset());
            if (hex != r.SetupSha256)
            {
                dst.Close();
                File.Delete(path + ".part");
                throw new InvalidDataException("the download doesn't match the release's SHA-256");
            }
        }
        File.Move(path + ".part", path, true);
        return path;
    }

    /// <summary>Runs the downloaded installer silently over this install. The caller quits right
    /// after: the installer closes what's left of Phone Use (CloseApplications=force) and starts the
    /// window again when it's done, in the tray when <paramref name="tray"/>.</summary>
    public static void LaunchInstaller(string setup, bool tray)
    {
        var dir = AppContext.BaseDirectory.TrimEnd('\\');
        var log = Path.Combine(Folder, "install.log");
        var args = $"/SILENT /SUPPRESSMSGBOXES /NORESTART /SP- /FORCECLOSEAPPLICATIONS " +
                   $"/UPDATE={(tray ? "tray" : "1")} /DIR=\"{dir}\" /LOG=\"{log}\"";
        Process.Start(new ProcessStartInfo(setup, args) { UseShellExecute = true, WorkingDirectory = Folder });
    }
}
