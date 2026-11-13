# Phone Use on Windows

Windows can't act as a Bluetooth Classic keyboard, so on Windows Phone Use is a
Bluetooth LE keyboard and mouse instead (HID over GATT, through the Windows
Runtime). It uses the PC's own Bluetooth adapter, needs no driver and no admin
rights, and Windows keeps using the adapter for everything else. The mirror window is
the browser mirror in a window of its own.

| | Tested with |
|---|---|
| Windows | Windows 11 |
| iPhone | iPhone 17 Pro Max and iPhone 14 Pro Max, iOS 26 |
| Python | 3.13, from python.org |
| UxPlay | 1.74, built in MSYS2 by the installer |
| Edge or Chrome | the mirror window |
| NVIDIA GPU (optional) | NVENC for the video hub, CUDA for the models that read the screen |

1. **Install**, from the checkout, in PowerShell:
   ```powershell
   powershell -ExecutionPolicy Bypass -File scripts\windows\install.ps1
   ```
   The installer makes `.venv`, installs MSYS2 to `C:\msys64` and builds UxPlay there,
   adds a Windows Firewall rule for UxPlay (it asks for admin rights once; the rule
   allows the local network only), puts `bin` on your PATH and downloads the models
   that read the screen (`-NoVision` skips them). It ends with `phoneuse doctor`,
   which checks each part and says how to fix what is missing.
2. **Start Phone Use:** run `phoneuse up`. The mirror window opens. Then, on the
   iPhone, open Control Center, tap Screen Mirroring and choose **Phone Use on <your computer>**.
3. **Pair**, once: on the iPhone, open Settings › Bluetooth and tap this PC's Windows
   name, such as `DESKTOP-…` (Windows doesn't let Phone Use choose its Bluetooth LE
   name). Confirm on the phone, and on Windows if it asks.
4. **Turn on the pointer** as on Linux ([SOURCE.md](SOURCE.md), step 5). The first time, Phone Use measures the phone's
   pointer: over Bluetooth LE every phone needs that measurement.

How it differs from Linux:

- In the mirror window, Ctrl works as Cmd: the Windows key belongs to the Start menu.
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
  Linux. Agents don't notice.
- The bridge hasn't been tried on Windows.

Logs are in `%LOCALAPPDATA%\phoneuse\run`. Agents use `bin\phoneuse.exe` (on your PATH after
the install), guided by [skill/SKILL.md](../skill/SKILL.md).

Configuration is the same as on Linux, in `%APPDATA%\phoneuse\config.toml` ([SOURCE.md](SOURCE.md#configuration)).
