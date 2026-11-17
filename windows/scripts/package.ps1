# Package Phone Use for Windows: the native window (WinUI 3) with phoneuse itself inside it, as
#   PhoneUse-<version>-win-x64-portable.zip   unzip anywhere and run PhoneUseMirror.exe; settings stay in data\
#   PhoneUse-<version>-win-x64-setup.exe      per-user installer (no admin), Start menu, uninstaller
#
#   powershell -ExecutionPolicy Bypass -File windows\scripts\package.ps1 [-Version 0.1.5.42] [-Out windows\build\release]
#     -NoZip: the installer only (no portable zip); -NoInstaller: the zip only.
#   To try a change to the window on an installed Phone Use, update-installed.ps1 is much quicker.
#
# Layout (both):
#   PhoneUseMirror.exe + the .NET and Windows App SDK runtime (self-contained)
#   core\              phoneuse as in the repository (phoneuse\, bin\, skill\, windows\scripts\), and
#     python\          a standalone CPython (python-build-standalone) with phoneuse's packages
#     uxplay\          the AirPlay receiver and the GStreamer it uses, from MSYS2 (UCRT64)
#     requirements-lock.txt   what python\ has, for the installer's "use the system's Python"
#
# Needs: the .NET 10 SDK; MSYS2 with UxPlay built (windows/scripts/build-uxplay.sh); Inno Setup 6
# (iscc) for the installer, else only the zip is made; the network for the Python download.
param(
    [string]$Version,
    [string]$Out,
    [string]$Msys = $(if ($env:PHONEUSE_MSYS2) { $env:PHONEUSE_MSYS2 } else { "C:\msys64" }),
    # The audio-only FFmpeg (windows/scripts/build-ffmpeg-audio.sh); without it, MSYS2's full one (~150 MB more)
    [string]$FFmpegAudio = $env:PHONEUSE_FFMPEG_AUDIO,
    [switch]$NoInstaller,
    [switch]$NoZip,
    # The GitHub repository whose releases update the app (Updater.cs): in CI, the one being built
    [string]$UpdateRepo = $(if ($env:GITHUB_REPOSITORY) { $env:GITHUB_REPOSITORY } else { "lxe/phoneuse" })
)
$ErrorActionPreference = "Stop"
$ProgressPreference = "SilentlyContinue"  # Invoke-WebRequest is many times slower with its progress bar
$Root = Split-Path -Parent (Split-Path -Parent $PSScriptRoot)
. "$PSScriptRoot\common.ps1"

# Pinned: a CPython the PyAV, numpy and WinRT wheels exist for. Bump both together.
$PyUrl = "https://github.com/astral-sh/python-build-standalone/releases/download/20261026/cpython-3.13.18%2B20261026-x86_64-pc-windows-msvc-install_only.tar.gz"
$PySha256 = $env:PHONEUSE_PY_SHA256  # optional: checked when set (CI sets it from the release's SHA256SUMS)
# The GStreamer plugins UxPlay loads with the arguments phoneuse gives it (cli.mirror_args): RTP out
# for the video, AAC/ALAC decoding (libav) to WASAPI for the audio. Measured with
# GST_DEBUG=GST_PLUGIN_LOADING:4; the smoke test below fails the build if one goes missing.
$Plugins = "app", "audioconvert", "audioresample", "coreelements", "level", "libav", "rtp", "udp",
           "videoparsersbad", "volume", "wasapi", "autodetect", "typefindfunctions", "playback"
# Not packaged: the bridge firmware and esptool, which only flashing a bridge needs. The app
# downloads them the first time someone flashes one (bridge.prepare_flashing).

if (-not $Version) {
    $base = (Select-String -Path "$Root\pyproject.toml" -Pattern '^version\s*=\s*"([^"]+)"').Matches[0].Groups[1].Value
    $Version = if ($env:GITHUB_RUN_NUMBER) { "$base.$env:GITHUB_RUN_NUMBER" } else { "$base.0" }
}
# As the Mac app's (mac/build/release): windows\build\, ignored by windows\.gitignore
if (-not $Out) { $Out = Join-Path $Root "windows\build\release" }
$Work = Join-Path $Root "windows\build\stage"
$Stage = Join-Path $Work "PhoneUse"
$Core = Join-Path $Stage "core"
$Cache = Join-Path $env:LOCALAPPDATA "phoneuse-build"
$Ucrt = Join-Path $Msys "ucrt64"
$Objdump = Join-Path $Ucrt "bin\objdump.exe"
foreach ($d in $Work, $Out, $Cache) { New-Item -ItemType Directory -Force $d | Out-Null }
if (Test-Path $Stage) {
    # A run that failed mid-test can leave the test UxPlay running from here, holding its DLLs
    Get-Process | Where-Object { $_.Path -like "$Work\*" } | Stop-Process -Force
    Start-Sleep 1
    Remove-Item -Recurse -Force $Stage
}
Write-Step "Phone Use $Version for Windows (x64)"

# 1. The window, self-contained (the csproj says so). Built, not published, as build-winui.ps1 does:
# with EnableMsixTooling off, publish leaves out the compiled XAML (.xbf) and PhoneUseMirror.pri and
# the window dies at start. The trailing slash on -o keeps the .xbf files inside the folder.
Write-Step "Building the mirror window"
dotnet build "$Root\windows\PhoneUseMirror\PhoneUseMirror.csproj" -c Release -r win-x64 -p:Platform=x64 `
    -p:SatelliteResourceLanguages=en -p:DebugType=none -p:Version=$Version -p:UpdateRepo=$UpdateRepo -o "$Stage/" -nologo -v q
if ($LASTEXITCODE -ne 0) { throw "the mirror window didn't build" }
foreach ($f in "PhoneUseMirror.exe", "PhoneUseMirror.pri", "App.xbf", "MainWindow.xbf") {
    if (-not (Test-Path "$Stage\$f")) { throw "the mirror window's build has no $f" }
}

# 2. phoneuse itself, as laid out in the repository (cli.ROOT is core\)
Write-Step "Copying phoneuse"
New-Item -ItemType Directory -Force "$Core\bin", "$Core\windows\scripts" | Out-Null
robocopy "$Root\phoneuse" "$Core\phoneuse" /E /XD __pycache__ /XF *.pyc /NFL /NDL /NJH /NJS /NP | Out-Null
robocopy "$Root\skill" "$Core\skill" /E /NFL /NDL /NJH /NJS /NP | Out-Null
if ($LASTEXITCODE -ge 8) { throw "copying phoneuse failed" }
Copy-Item "$Root\bin\phoneuse" "$Core\bin\phoneuse"
Copy-Item "$PSScriptRoot\phoneuse.cmd" "$Core\bin\phoneuse.cmd"
foreach ($s in "common.ps1", "setup-vision.ps1", "pip-progress.py", "firewall.ps1") { Copy-Item "$PSScriptRoot\$s" "$Core\windows\scripts\" }
Copy-Item "$Root\pyproject.toml", "$Root\LICENSE", "$Root\README.md" $Core
# 3. Python
Write-Step "Python (python-build-standalone) and phoneuse's packages"
$tgz = Join-Path $Cache ([uri]::UnescapeDataString(($PyUrl -split "/")[-1]))
if (-not (Test-Path $tgz)) { Invoke-WebRequest $PyUrl -OutFile "$tgz.part"; Move-Item "$tgz.part" $tgz }
if ($PySha256 -and (Get-FileHash $tgz -Algorithm SHA256).Hash -ne $PySha256.ToUpper()) { throw "the Python download's SHA-256 doesn't match" }
tar -xzf $tgz -C $Core  # makes core\python
if ($LASTEXITCODE -ne 0) { throw "couldn't unpack Python" }
$Py = Join-Path $Core "python\python.exe"
$deps = & $Py -c "import tomllib; print('\n'.join(tomllib.load(open(r'$Root\pyproject.toml', 'rb'))['project']['dependencies']))"
& $Py -m pip install --quiet --disable-pip-version-check --no-warn-script-location --only-binary=:all: @($deps -split "`n" | Where-Object { $_ })
if ($LASTEXITCODE -ne 0) { throw "pip couldn't install phoneuse's packages" }
& $Py -m pip freeze --disable-pip-version-check | Where-Object { $_ -notmatch '^(pip|setuptools|wheel)==' } |
    Set-Content -Encoding ascii "$Core\requirements-lock.txt"
# What the app never runs: Tk/IDLE, the test suite, headers, and pip's caches
foreach ($p in "tcl", "include", "Lib\idlelib", "Lib\tkinter", "Lib\turtledemo", "Lib\test", "DLLs\_tkinter.pyd",
               "DLLs\tcl86t.dll", "DLLs\tk86t.dll", "DLLs\tcl9*.dll", "DLLs\tk9*.dll") {
    Get-Item (Join-Path "$Core\python" $p) -ErrorAction SilentlyContinue | Remove-Item -Recurse -Force
}
Get-ChildItem "$Core\python\Lib\site-packages" -Directory -Recurse -Filter tests -ErrorAction SilentlyContinue |
    Where-Object { $_.FullName -match '\\numpy\\' } | Remove-Item -Recurse -Force
Get-ChildItem "$Core\python" -Recurse -File -Filter *.pdb | Remove-Item -Force  # debug symbols, ~90 MB
# pip only installed the packages; a venv made from this Python (setup-vision.ps1) gets its own from ensurepip
& $Py -m pip uninstall --quiet --yes pip
Remove-Item "$Core\python\Scripts" -Recurse -Force -ErrorAction SilentlyContinue
& $Py -m compileall -q -j 0 "$Core\phoneuse" "$Core\python\Lib" | Out-Null
& $Py -c "import av, numpy, PIL, aiohttp, psutil, cryptography, bleak, serial, winrt.windows.devices.bluetooth; import sys; sys.path.insert(0, r'$Core'); import phoneuse.cli, phoneuse.windows.background"
if ($LASTEXITCODE -ne 0) { throw "the bundled Python can't import phoneuse" }

# 4. UxPlay and its GStreamer, with every DLL they load from MSYS2
Write-Step "UxPlay and GStreamer from $Ucrt"
if (-not (Test-Path "$Ucrt\bin\uxplay.exe")) { throw "no UxPlay in $Ucrt\bin: run windows/scripts/build-uxplay.sh in MSYS2 UCRT64 first" }
$Ux = Join-Path $Core "uxplay"
New-Item -ItemType Directory -Force "$Ux\bin", "$Ux\lib\gstreamer-1.0", "$Ux\libexec\gstreamer-1.0", "$Ux\licenses" | Out-Null
if (-not $FFmpegAudio) { $FFmpegAudio = Join-Path $Msys "home\$env:USERNAME\ffmpeg-audio" }
$FFbin = Join-Path $FFmpegAudio "bin"
if (Test-Path "$FFbin\avcodec-*.dll") { Write-Host "    FFmpeg: audio only, from $FFmpegAudio" }
else { Write-Warning "no audio-only FFmpeg in $FFmpegAudio (build-ffmpeg-audio.sh): bundling MSYS2's full one"; $FFbin = $null }
$seen = @{}
function Add-Closure($file) {
    foreach ($line in (& $Objdump -p $file | Select-String "DLL Name: (.+)$")) {
        $dll = $line.Matches[0].Groups[1].Value.Trim()
        $key = $dll.ToLower()
        if ($seen.ContainsKey($key)) { continue }
        $seen[$key] = $true
        # FFmpeg's DLLs from the audio-only build (same names and ABI), everything else from MSYS2
        $src = if ($FFbin -and (Test-Path (Join-Path $FFbin $dll))) { Join-Path $FFbin $dll } else { Join-Path "$Ucrt\bin" $dll }
        if (-not (Test-Path $src)) { continue }  # a Windows DLL (kernel32, ws2_32, ...)
        Copy-Item $src "$Ux\bin\"
        Add-Closure $src
    }
}
Copy-Item "$Ucrt\bin\uxplay.exe" "$Ux\bin\"
Add-Closure "$Ucrt\bin\uxplay.exe"
foreach ($p in $Plugins) {
    $dll = "$Ucrt\lib\gstreamer-1.0\libgst$p.dll"
    if (-not (Test-Path $dll)) { throw "GStreamer plugin $p isn't installed in MSYS2" }
    Copy-Item $dll "$Ux\lib\gstreamer-1.0\"
    Add-Closure $dll
}
Copy-Item "$Ucrt\libexec\gstreamer-1.0\gst-plugin-scanner.exe" "$Ux\libexec\gstreamer-1.0\"
Add-Closure "$Ucrt\libexec\gstreamer-1.0\gst-plugin-scanner.exe"
@"
UxPlay (https://github.com/FDH2/UxPlay) is GPL-3.0; built from the commit pinned in
windows/scripts/build-uxplay.sh of phoneuse (https://github.com/lxe/phoneuse).
GStreamer is LGPL-2.1 (https://gstreamer.freedesktop.org). FFmpeg (avcodec, avformat, avfilter, avutil,
swresample) is LGPL-2.1, built from the release MSYS2 ships with only its AAC and ALAC decoders
(windows/scripts/build-ffmpeg-audio.sh). The other DLLs here are MSYS2's UCRT64 packages
(https://packages.msys2.org), each under its own license.
"@ | Set-Content "$Ux\licenses\README.txt"
Get-ChildItem "$Ucrt\share\licenses" -Directory -ErrorAction SilentlyContinue |
    Where-Object { $_.Name -match '^(gstreamer|gst-plugins-base|gst-plugins-good|gst-plugins-bad|gst-libav|ffmpeg|libplist|openssl|glib2)$' } |
    ForEach-Object { Copy-Item -Recurse $_.FullName "$Ux\licenses\" }

# UxPlay from the bundle alone (MSYS2 off the PATH): its pipelines must build, which loads every
# plugin it needs; a missing one is "no element" in its output.
Write-Step "Checking that the bundled UxPlay starts"
$uxLog = Join-Path $Work "uxplay-smoke.log"
$env:GST_REGISTRY_1_0 = Join-Path $Work "gst-registry.bin"
Remove-Item $env:GST_REGISTRY_1_0 -ErrorAction SilentlyContinue
$savedPath = $env:PATH
$env:PATH = ($env:PATH -split ";" | Where-Object { $_ -notlike "$Msys*" }) -join ";"
try {
    # $uxProc, not $ux: PowerShell's names are case-insensitive, and $Ux is the folder
    $uxProc = Start-Process "$Ux\bin\uxplay.exe" -PassThru -WindowStyle Hidden -RedirectStandardOutput $uxLog -RedirectStandardError "$uxLog.err" `
        -ArgumentList '-p 47999 -n phoneuse-package-test -nh -vrtp "config-interval=1 pt=96 ! udpsink host=127.0.0.1 port=47998 sync=false" -vsync no -as "wasapisink low-latency=true" -al 0.1'
    Start-Sleep 4
    $alive = -not $uxProc.HasExited
    if ($alive) { Stop-Process $uxProc -Force }
} finally { $env:PATH = $savedPath; Remove-Item Env:GST_REGISTRY_1_0 }
$said = (Get-Content $uxLog, "$uxLog.err" -ErrorAction SilentlyContinue) -join "`n"
if (-not $alive -or $said -match "no element|could not|failed") { Write-Host $said; throw "the bundled UxPlay didn't start" }
# The elements themselves, the audio decoders above all: UxPlay only builds its audio pipeline when the
# phone plays sound, so starting isn't proof (doctor.py checks the same list on users' PCs).
$inspect = Join-Path $Ux "bin\gst-inspect-1.0.exe"
Copy-Item "$Ucrt\bin\gst-inspect-1.0.exe" $inspect
$env:PATH = ($env:PATH -split ";" | Where-Object { $_ -notlike "$Msys*" }) -join ";"
$env:GST_REGISTRY_1_0 = Join-Path $Work "gst-registry.bin"
try {
    foreach ($el in "avdec_aac", "avdec_alac", "wasapisink", "rtph264pay", "rtph265pay", "udpsink", "h264parse", "appsrc", "audioconvert", "volume") {
        & $inspect --exists $el
        if ($LASTEXITCODE -ne 0) { throw "the bundled GStreamer has no $el" }
    }
} finally { $env:PATH = $savedPath; Remove-Item Env:GST_REGISTRY_1_0; Remove-Item $inspect }

$mb = [math]::Round(((Get-ChildItem $Stage -Recurse -File | Measure-Object Length -Sum).Sum / 1MB))
Write-Step "Staged $Stage ($mb MB)"

# 5. Portable zip: the same files plus the "portable" marker
$name = "PhoneUse-$Version-win-x64"
if ($NoZip) { Write-Host "    No zip (-NoZip)" }
else {
    $Portable = Join-Path $Work "portable\PhoneUse"
    if (Test-Path (Split-Path $Portable)) { Remove-Item -Recurse -Force (Split-Path $Portable) }
    New-Item -ItemType Directory -Force (Split-Path $Portable) | Out-Null
    Copy-Item -Recurse $Stage $Portable
    "Phone Use keeps its settings in data\ next to this file instead of in AppData. Delete this file to use AppData." |
        Set-Content "$Portable\portable"
    New-Item -ItemType Directory -Force "$Portable\data" | Out-Null
    $zip = Join-Path $Out "$name-portable.zip"
    Remove-Item $zip -ErrorAction SilentlyContinue
    tar -a -cf $zip -C (Split-Path $Portable) PhoneUse  # Windows' bsdtar: much faster than Compress-Archive
    if ($LASTEXITCODE -ne 0) { throw "couldn't make the zip" }
    Write-Step "Built $zip"
}

# 6. Installer
if ($NoInstaller) { return }
$iscc = (Get-Command iscc -ErrorAction SilentlyContinue).Source
if (-not $iscc) {
    $iscc = @("${env:ProgramFiles(x86)}\Inno Setup 6\ISCC.exe", "$env:ProgramFiles\Inno Setup 6\ISCC.exe",
              "$env:LOCALAPPDATA\Programs\Inno Setup 6\ISCC.exe") | Where-Object { Test-Path $_ } | Select-Object -First 1
}
if (-not $iscc) {
    $made = if ($NoZip) { "made nothing" } else { "made the zip only" }
    Write-Warning "Inno Setup 6 isn't installed: $made (winget install JRSoftware.InnoSetup)"
    return
}
& $iscc /Q "/DAppVersion=$Version" "/DStage=$Stage" "/DOutDir=$Out" "/DOutName=$name-setup" "$PSScriptRoot\PhoneUse.iss"
if ($LASTEXITCODE -ne 0) { throw "Inno Setup failed" }
Write-Step "Built $(Join-Path $Out "$name-setup.exe")"
