# Running Phone Use from source

This page covers running Phone Use from a checkout on Linux, with the `phoneuse`
command instead of the app. For Windows, see [WINDOWS.md](WINDOWS.md).

| | Tested with |
|---|---|
| iPhone | iPhone 17 Pro, iOS 26 (Phone Use takes the screen size from the model) |
| Linux with BlueZ | Arch Linux, BlueZ 5.87 |
| Hyprland | 0.56 |
| UxPlay | 1.73.7 |
| GStreamer, GTK 4, `gtk4paintablesink` | 1.28, 4.24 |
| Python 3.11+ | `python-dbus`, `python-gobject`, `numpy`, `pillow`, `aiohttp` |
| `grim` | takes screenshots for agents |
| `uv` (optional) | runs the models that read the screen |
| For the bridge (optional) | [LilyGO T-Display](https://github.com/Xinyuan-LilyGO/TTGO-T-Display) (ESP32), ESP-IDF v6.1, `python-bleak`, `python-cryptography`, `python-pyserial` |

The phone and the PC must be on the same network. If you run a firewall, open TCP
ports 7000, 7001 and 7100 and UDP ports 6000, 6001 and 7011 for AirPlay, and UDP
port 47700 for the bridge. The iPhone app needs TCP port 8778.

1. **Install:**
   ```sh
   git clone https://github.com/lxe/phoneuse.git ~/phoneuse
   ln -s ~/phoneuse/bin/phoneuse ~/.local/bin/phoneuse
   phoneuse install   # needs sudo
   ```
   `phoneuse install` sets up the HID daemon as the `phoneuse-hid` service. It also
   turns off bluetoothd's input plugin, so the PC stops accepting Bluetooth keyboards
   and mice for itself. To undo the install, run `sudo scripts/install.sh --uninstall`.

2. **Start Phone Use:**
   ```sh
   phoneuse up
   ```
   Then pair the iPhone app (step 4): the AirPlay receiver appears only once an
   iPhone is paired. On the iPhone, open Control Center, tap Screen Mirroring and
   choose **Phone Use on <your computer>** (`phoneuse status` shows the name).

3. **Connect the mouse and keyboard**, in one of two ways:
   - **With this PC's Bluetooth:** on the iPhone, open Settings › Bluetooth and tap
     **Phone Use**.
   - **With the bridge:** follow [The Bluetooth bridge](#the-bluetooth-bridge) below.

4. **Pair the iPhone app:** run `phoneuse app pair` and open the link on the phone.
   Agents are refused until an app is paired.

5. **Turn on the pointer** (AssistiveTouch), in one of two ways:
   - Run `phoneuse cursor on`. Phone Use plays a spoken request through this PC's
     speakers and asks Siri to turn it on.
   - On the iPhone, open Settings › Accessibility › Touch › AssistiveTouch.

   Keep Dwell Control off: when it is on, iOS taps wherever the pointer stops. The
   first time you turn on a phone's pointer, Phone Use spends about 13 seconds
   measuring how far that phone moves the pointer for each mouse report, and it uses
   that measurement for drags. To measure again, run `phoneuse calibrate`.

## The Bluetooth bridge

The bridge is a small puck (a [LilyGO T-Display](https://github.com/Xinyuan-LilyGO/TTGO-T-Display),
an ESP32) that the iPhone pairs with as its Bluetooth keyboard and mouse. Phone Use sends commands to the bridge over Wi-Fi, so you can leave the bridge
next to the phone on a battery or a charger, and the PC's own Bluetooth adapter stays
unused.

1. **Flash the bridge** once, over USB, with the release's firmware (see
   [BRIDGE.md](BRIDGE.md#flashing)) or from source. To build it, install ESP-IDF, then:
   ```sh
   git clone -b v6.1 --recursive https://github.com/espressif/esp-idf.git ~/esp/esp-idf-v6.1
   ~/esp/esp-idf-v6.1/install.sh esp32
   phoneuse bridge flash
   ```
2. **Set it up** in the browser mirror: open Settings › Bluetooth bridge › Set Up
   Bridge. Check that the bridge's screen shows the same code as the page, then pick
   a 2.4 GHz Wi-Fi network and type its password. This step doesn't need the USB
   cable.
3. **Pair the iPhone** with the bridge: on the iPhone, open Settings › Bluetooth and
   tap **Phone Use Bridge**.

To use the same bridge from another computer, open Settings › Bluetooth bridge
there, click Use… next to the bridge, and press either of the bridge's buttons when
its screen shows the same code.

`phoneuse bridge status` shows the bridge's address, which phone is connected to it,
and how long a message takes to reach the bridge and come back. When the phone
disconnects, the bridge reconnects to it by itself. [BRIDGE.md](BRIDGE.md)
describes the protocol.

## Using it

**The mirror window:** the phone's pointer follows your mouse. Click to tap, drag to
swipe, and use the scroll wheel to scroll. To swipe in from an edge of the screen,
press on the bezel and drag; dragging up from the bottom bezel goes to the home
screen. While the window has focus, your keyboard types on the phone, and the Super
key works as Cmd.

**The browser mirror:** http://localhost:8777 shows the same mirror, and only this
computer can open it. In its settings you can set up the bridge and keep the phone's
screen awake.

**Unlocking:** Phone Use doesn't save or type your passcode. When the phone is locked,
an agent asks you to unlock it.

**Agents:** each action is a subcommand, and `phoneuse --help` lists them all:

```sh
phoneuse open Settings
phoneuse text                 # print the screen as text, with positions
phoneuse tap-on "Wi-Fi"       # tap the on-screen text that matches
phoneuse screenshot shot.png
phoneuse done                 # hand the phone back to you
```

[skill/SKILL.md](../skill/SKILL.md) is a skill for Claude Code that teaches agents these
commands. Agents use the phone through the `phoneuse` command; there is no MCP server. To
use the skill, link it into `~/.claude/skills/phoneuse`:

```sh
ln -s ~/phoneuse/skill ~/.claude/skills/phoneuse
```

If you have the Phone Use Flatpak installed, `scripts/dev-link.sh` runs it from this checkout
instead: the app, its services and the `phoneuse` command all use the code here, and the skill
links here ([PACKAGING.md](PACKAGING.md)).

## Configuration

These settings are optional and go in `~/.config/phoneuse/config.toml`
(on Windows, `%APPDATA%\phoneuse\config.toml`):

```toml
name = "Phone Use"            # the Bluetooth and AirPlay name; rerun phoneuse install after changing it
screen_points = "auto"        # the iPhone's screen size in points, such as [402, 874];
                              # auto: from the model of the phone that last mirrored
decoder = "auto"              # the GStreamer H.264 decoder
mirror_width = 498            # the mirror window's width
web_port = 8777               # the browser mirror's port
# Windows only:
encoder = "auto"              # the video hub's encoder: h264_nvenc, libx264 or h264_mf
uxplay = "auto"               # path to uxplay.exe (auto: C:\msys64\ucrt64\bin)
app_window = true             # phoneuse up opens the mirror in a window of its own
service_port = 47810          # the first of four local ports phoneuse's services use
```
