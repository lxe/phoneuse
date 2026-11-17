# Phone Use on Windows

Windows can't act as a Bluetooth Classic keyboard, so on Windows Phone Use is a
Bluetooth LE keyboard and mouse instead (HID over GATT, through the Windows
Runtime). It uses the PC's own Bluetooth adapter, needs no driver and no admin
rights, and Windows keeps using the adapter for everything else. A
[Bluetooth bridge](#the-bluetooth-bridge) can be the keyboard and mouse instead, over
Bluetooth Classic as on Linux. The mirror window is a native WinUI 3 app
(`windows/PhoneUseMirror/`), or the browser mirror in an Edge or Chrome window when that
app isn't built.

| | Tested with |
|---|---|
| Windows | Windows 11 |
| iPhone | iPhone 18 Pro Max, iPhone 17 Pro Max and iPhone 14 Pro Max, iOS 26 |
| Python | 3.13 (the app ships python-build-standalone's; a checkout uses python.org's) |
| UxPlay | 1.74, built in MSYS2 (UCRT64) |
| NVIDIA GPU (optional) | NVENC for the video hub, CUDA for the OCR that reads the screen |

## Install the app

Each release, and each build of `main`, has two downloads (the GitHub workflow
`.github/workflows/windows-app.yml` makes them with `windows/scripts/package.ps1`):

- **`PhoneUse-<version>-win-x64-setup.exe`**, an installer for your account only (no admin
  rights), with a Start menu entry and an uninstaller. Its options:
  - **Use the Python already on this PC**, when it's a compatible python.org CPython
    (3.11–3.14): it makes `core\.venv` from it and downloads phoneuse's packages from PyPI,
    pinned to what the app was tested with. If anything fails, it installs the bundled
    Python instead.
  - **Let the iPhone reach Phone Use through Windows Firewall** (asks for admin once; the
    rule allows the local network only).
  - **Add the phoneuse command to PATH**, for terminals and AI agents.
  - A desktop icon, and starting Phone Use with Windows (in the tray).
- **`PhoneUse-<version>-win-x64-portable.zip`**: unzip it anywhere and run
  `PhoneUseMirror.exe`. Its settings stay in `data\` beside it, not in your profile. Run
  `core\bin\phoneuse.cmd install` once for the firewall rule.

Both hold the window with its .NET runtime, phoneuse in `core\` (with `core\python\`, a
standalone Python with phoneuse's packages), and UxPlay with the GStreamer and audio-only
FFmpeg it needs in `core\uxplay\`. Neither includes the OCR that reads the screen (about 2 GB
with an NVIDIA GPU, 250 MB without): the window's Settings › Screen reading installs it into
`core\.venv-vision`, with a progress bar for each download and for unpacking, and nothing
kept in pip's cache. Cancel stops it and removes what it left; Remove deletes the folder. Nor
do they include the bridge's
firmware and flashing tool: the app downloads those only if you flash a bridge.

## Install from a checkout

Needs the .NET 10 SDK for the window (without it, Phone Use opens Edge or Chrome). In
PowerShell:

```powershell
powershell -ExecutionPolicy Bypass -File windows\scripts\install.ps1
```

It makes `.venv`, installs MSYS2 to `C:\msys64` and builds UxPlay there, builds the Phone
Use window into `windows\dist` (`windows\scripts\build-winui.ps1` builds it again), adds
the firewall rule (one admin prompt), puts `bin` on your PATH and installs the OCR
that reads the screen (`-NoVision` skips it). It ends with `phoneuse doctor`, which checks
each part and says how to fix what is missing. `windows\scripts\package.ps1` builds the
installer and the zip (it needs Inno Setup 6 for the installer).

## First run

1. **Start Phone Use:** open the app (`windows\dist\PhoneUseMirror.exe` in a checkout).
   It starts phoneuse in the background when it isn't running. `phoneuse up` also opens it.
   Then, on the iPhone, open Control Center, tap Screen Mirroring and choose
   **Phone Use on <your computer>**.
2. **Pair**, once: on the iPhone, open Settings › Bluetooth and tap this PC's Windows
   name, such as `DESKTOP-…` (Windows doesn't let Phone Use choose its Bluetooth LE
   name). Confirm on the phone, and on Windows if it asks.
3. **Turn on the pointer** as on Linux ([SOURCE.md](SOURCE.md), step 5). The first time,
   Phone Use measures the phone's pointer: over Bluetooth LE every phone needs that
   measurement.
4. **For agents, pair the iPhone app** ([COMPANION.md](COMPANION.md)): in the Phone Use
   window, Settings › Pair iPhone shows a QR code to scan with the Phone Use app.
   Agents are refused until an app is paired.
5. **Connect Claude Code**, if you use it: `phoneuse claude connect` links the phoneuse
   skill into `~\.claude\skills` and puts the `phoneuse` command in `~\.local\bin`, where
   Claude Code's own `claude` is (an sh script for its Bash tool, and `phoneuse.cmd` for
   PowerShell and cmd). Both point at this copy of Phone Use. `phoneuse claude disconnect`
   removes them. It also removes the MCP server older versions of Phone Use added.

## The Phone Use window

The window is a client of the browser mirror's server (`phoneuse/web.py`): the same
video, input and settings, with lower latency (Media Foundation's hardware H.264
decoder straight to a Direct3D swap chain).

- **The window** is only the phone: no border, see-through around the frame. Point just
  above the phone for its bar (the mirroring status, About, Settings, minimize, close);
  drag the bar or the top phone frame to move the window. Drag an edge or corner
  to resize it with the phone's proportions preserved; Ctrl + wheel also resizes it. Setup,
  About and notices appear on the phone's screen.
- **Input:** click to tap, drag to swipe, scroll with the wheel; a press on the bezel
  touches the nearest screen edge. Ctrl works as the phone's Cmd (the Windows key
  belongs to the Start menu) and the Windows key as its Ctrl; F1 Siri, F2 Home,
  F3 Search, F4 the on-screen keyboard.
- **The tray:** closing the window hides it to the notification area, so agents keep
  the phone. The tray icon opens it again; its menu shows phoneuse's log and quits.
  Quit stops the phoneuse the window started (not a `phoneuse up` in a terminal); the
  Bluetooth keyboard and mouse keep running, as after `phoneuse up`.
- **Above the tray icon:** the window snaps to a default position centred above its tray
  icon with an 8 DIP gap from the taskbar, kept within that monitor. Drag it away to float;
  drop within the tray area to snap again. A cyan outline and a
  *Release to snap to tray* badge preview the default destination. Snapping happens only
  on release, so dragging across the tray area or onto another monitor stays free.
  The tray menu and Settings switch use the same default position, including while resizing.
  A full app restart always starts snapped. After closing to the tray, the tray icon
  reopens at the default snap position; the pinned taskbar icon restores the position
  and snap state from before closing. That position is retained only while the app runs.
- **Settings** (the gear) opens over the phone, like About; use Back or Escape to return.
  Keep the screen awake, start with Windows (phoneuse starts
  at sign-in with the window in the tray), agent control,
  the iPhone app's pairing, the Bluetooth bridge, screen reading (install or remove the
  models), and the passcode.
- **About** (the ⓘ button): how to use it and these differences from Linux.
- **Updates** (Settings › Updates): the installed and portable app look for a newer
  [GitHub release](https://github.com/lxe/phoneuse/releases) 20 s after starting and every
  6 hours (or on Check for updates). A newer one shows a notice on the phone's screen, and
  a tray notification while the window is hidden. Nothing installs by itself: **Install**
  downloads the release's `-setup.exe`, checks it against the SHA-256 GitHub keeps for it,
  and runs it silently over this install, which restarts Phone Use (and phoneuse) without
  asking for the firewall rule again. The portable app's **Download** opens the release's
  page. Only published releases count: the workflow's draft releases and prereleases are
  never offered. A build checks the repository it was built from (`package.ps1 -UpdateRepo`,
  `GITHUB_REPOSITORY` in CI); a checkout never checks.

The window's own settings are in `%LOCALAPPDATA%\phoneuse\PhoneUseMirror.json`.
`window = "browser"` in `config.toml` uses Edge or Chrome even when the window is built.

## The Bluetooth bridge

A LilyGO T-Display running the bridge firmware ([BRIDGE.md](BRIDGE.md)) can be the
iPhone's keyboard and mouse instead of this PC. The PC sends it input over Wi-Fi, and it
reaches the phone over Bluetooth Classic, as on Linux, so the pointer is quicker than
over this PC's Bluetooth LE.

- **Settings › Bluetooth bridge** shows **Use this PC's Bluetooth**. Turn it off to send
  input through the bridge. It's greyed out (and on) until a bridge is set up and
  answering. **Bridge Settings** opens the bridge's page: its iPhone, its Wi-Fi network,
  setting one up (or using one another PC set up), flashing, and forgetting it.
- **While the bridge carries input, this PC's Bluetooth keyboard is closed**, so the
  iPhone has one keyboard. If the bridge stops answering for 15 s
  (`PHONEUSE_LE_FALLBACK`), it opens again so the phone keeps working; it closes again
  once the bridge answers. Each close and open is a change the paired iPhone sees (see
  below). `phoneuse status` shows `link=bridge` or `link=ble`, and `le=open` or
  `le=closed`.
- **Flashing** works from Bridge Settings with the board on USB. The first time, Phone Use
  downloads the firmware (checked against its SHA-256) and esptool (about 50 MB) to
  `%LOCALAPPDATA%\phoneuse\bridge-tools` (the portable app: `data\bridge-tools`), and removes them once the board is flashed.
  Flashing erases the board: set the bridge up again after it.
- **The board's USB driver:** Windows may have no driver for the T-Display's USB chip (a
  WCH CH9102), so it gets no COM port. The flash dialog says so and links to WCH's CH343
  driver.
- Calibration is kept per link: the Classic tables for the bridge are separate from the
  LE ones (`drag_pts_classic`).

The bridge on Windows has been tested against a simulated bridge
(`tests/test_windows_bridge.py`) but not yet with real hardware.

## How it differs from Linux

- The Bluetooth keyboard and mouse run as a background service that keeps running
  when `phoneuse up` stops, and the next `phoneuse up` uses it again. Each time the
  service starts, the iPhone sees its Bluetooth services disappear and come back, and
  one iPhone stopped using them until it was paired again. `phoneuse down` stops it.
- Pair only the phone that Phone Use should control. Over Bluetooth LE, iOS may give
  its name as just "iPhone", so when two paired phones are connected Phone Use can't
  tell which one is mirroring, and it sends nothing.
- A motherboard's Wi-Fi and Bluetooth card needs its antennas, even if the PC uses
  Ethernet. Without them the phone was heard at about -90 dBm next to the PC, the
  link dropped several times an hour, and keys and taps sent during a drop were lost.
- The phone takes a report every 15 ms over Bluetooth LE instead of every 8 ms, and
  the mirror trails the phone by about 0.1 s, so by hand it feels slower than on
  Linux. Agents don't notice. The bridge doesn't have this limit.
- The PC isn't published over Bonjour (no Avahi), so the iPhone app finds it only at
  the address in its pairing link: pair again after the PC's address changes.

Logs are in `%LOCALAPPDATA%\phoneuse\run` (`phoneuse-up.log` when the window started
phoneuse). Agents use the `phoneuse` command (`bin\phoneuse.exe` in a checkout,
`core\bin\phoneuse.cmd` in the app), guided by [skill/SKILL.md](../skill/SKILL.md).

Configuration is the same as on Linux, in `%APPDATA%\phoneuse\config.toml`
([SOURCE.md](SOURCE.md#configuration)); the portable app keeps it in `data\`.
