# Phone Use for Windows

A native WinUI 3 window around the same core as the Linux and Mac apps (docs/WINDOWS.md): the mirror, a tray
icon, pairing and settings. It shows the browser mirror's stream and starts phoneuse in the background when
nothing answers on the mirror's port (`Backend.cs`); the Windows-only Python is in `phoneuse/windows/`.

- `PhoneUseMirror/`: the app (C#, .NET 10, Windows App SDK; unpackaged and self-contained)
- `scripts/`: setting up a checkout (`install.ps1`, `build-winui.ps1`, `build-uxplay.sh`, `firewall.ps1`,
  `setup-vision.ps1`, `update-installed.ps1`) and packaging the app (`package.ps1`, `PhoneUse.iss`, `build-ffmpeg-audio.sh`)

## Develop

```powershell
powershell -ExecutionPolicy Bypass -File windows\scripts\install.ps1   # .venv, MSYS2 and UxPlay, the window
windows\dist\PhoneUseMirror.exe
```

The development build runs the core from this checkout with its `.venv`, and UxPlay from MSYS2
(`C:\msys64\ucrt64\bin`). `windows\scripts\build-winui.ps1` rebuilds just the window into `windows\dist`. Logs
are in `%LOCALAPPDATA%\phoneuse\run`.

To try a change to the window on an installed Phone Use, `windows\scripts\update-installed.ps1` builds it and
copies it over the install in seconds (phoneuse in `core\` is left as it is, and keeps running).

## Package

```powershell
# in MSYS2 UCRT64, once (and after MSYS2 updates FFmpeg): bash windows/scripts/build-ffmpeg-audio.sh
powershell -ExecutionPolicy Bypass -File windows\scripts\package.ps1
```

This makes `windows\build\release\PhoneUse-<version>-win-x64-portable.zip` and `...-setup.exe` (with Inno Setup 6),
the same as `.github/workflows/windows-app.yml` does on every push to main. Both hold the window with the core in
`core\`: phoneuse, a standalone CPython with its packages, and UxPlay with the GStreamer plugins it uses and an
FFmpeg that only decodes audio. The installer is per-user (no admin except for the optional firewall rule) and
can use a compatible Python already on the PC instead of the bundled one. In the zip, a `portable` file next to
the exe keeps settings in `data\`. `-NoZip` makes only the installer, `-NoInstaller` only the zip.
