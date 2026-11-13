# Phone Use for macOS

This is the plan for a Mac app that does everything the Linux app does (docs/PACKAGING.md), and for keeping the
two, and a later Windows app, on one codebase. It records what the first look at macOS 27 on an Apple-silicon
Mac found, how the app is built, and the order of the work.

## One core, a native shell per platform

The Linux app already splits into two parts, and the Mac app keeps that split:

- **The core** is the Python package `phoneuse`, run as one supervisor (`phoneuse serve`). It owns everything
  about the phone: the AirPlay receiver (UxPlay), the video hub, the frames service, the browser mirror and its
  local API on port 8777, the companion API on 8778, the HID daemon in bridge-only mode, and the CLI. It is the same code on every platform; only a few calls into the system differ (below).
- **The shell** is a native app that starts and stops the core and shows its state. It holds no phone logic: it
  reads and changes everything through the browser mirror's local API, as the GTK app does. On Linux the shell
  is GTK and libadwaita; on the Mac it is SwiftUI; on Windows it will be WinUI.

The phone itself is always the browser mirror's web page, on every platform, so the mirror, the agent overlay
and the bridge setup flow stay one implementation.

### Shared UI model

The three shells use the same structure and the same words, taken from the iOS app:

- **Phone**: a hero that shows the next thing to do (start Phone Use, allow the network, set up the bridge,
  connect it to the iPhone, start Screen Mirroring) with one button, and once everything works, the iPhone and
  **Open iPhone**. Then Keep Awake, Start at Login, and the paired iPhone apps.
- **Agents**: Agent control (Always / After a delay / Ask every time), Delay, Unlock passcode, Claude Code.
- **Bridge**: the bridge, its signal, Input Through Bridge, Wi-Fi network, Set Up / Forget.

So that the shells don't each re-implement the hero's order of checks, the core should compute it: a new
`GET /api/app` returns the hero (`{"step": "bridge-offline", "title": …, "detail": …, "action": "open-bridge"}`)
and the settings' current values in one call. Each shell maps `action` to its own button. Until that exists,
the Mac shell ports `Window._show_hero` from app.py line for line.

On the Mac the shell is a **menu bar item** (the phone's state at a glance, Open iPhone, the hero's next step,
Settings…, Quit) plus a **settings window** with the three tabs as a toolbar tab view. Phone Use keeps running
with the window closed, like the Linux app's background mode, and leaves the Dock when no window is open.

## What differs on macOS, and the decision for each

**AirPlay port 7000 is taken.** macOS's own AirPlay Receiver (ControlCenter) listens on TCP 7000, and it is on
by default; on this Mac it is. UxPlay's `-p` takes a base port, and an iPhone finds the receiver's ports through
Bonjour, so UxPlay can run on other ports beside the system receiver (to be confirmed with the phone). The
plan: start UxPlay on 7200/7201/7202 (TCP) and 6200/6201/7211 (UDP) on macOS, so the user changes no system
setting. If iOS turns out to need 7000, setup gets a step that opens System Settings › General › AirDrop &
Handoff with an explanation, and checks port 7000 until it's free.

**Apple's iPhone Mirroring is a second way in, not a replacement.** iPhone Mirroring (macOS 15 and later) shows
and controls the iPhone with no bridge and no AirPlay. Its limits rule it out as the main path: the iPhone must
be locked and nearby while it's mirrored, so the owner can't use the phone at the same time; it needs the same
Apple ID on both and isn't available in the EU; an agent would read the screen with ScreenCaptureKit and drive
it with synthesized mouse events in the iPhone Mirroring window, which needs Screen Recording and Accessibility
permission and is easy to disturb. It stays a later option ("no bridge" mode for people without one), behind a
small input/screen backend interface in the core, after the bridge path works.

**Bonjour** comes with macOS. The companion API's `_phoneuse._tcp` record, published on Linux through Avahi's
D-Bus API, is published with `dns-sd -R` on macOS (a small change in companion.py). UxPlay builds against
macOS's own dns_sd.

**GStreamer** comes from Homebrew for development (`gstreamer` 1.28, with `pygobject3`). The hub decodes with
`vtdec` (VideoToolbox) or `avdec_h264` and encodes with `vtenc_h264_hw` (realtime, no frame reordering,
keyframes on request), in place of NVENC or x264. Audio plays through `osxaudiosink` in place of `pulsesink`.

**UxPlay** isn't in Homebrew; it builds from source with CMake against Homebrew's GStreamer, libplist and OpenSSL.
For the app, build it with `-DCMAKE_OSX_DEPLOYMENT_TARGET=15.0`. Homebrew's own libraries are built for the
macOS the build Mac runs, so `bundle_core.py` copies each one from Homebrew's macOS 15 bottle (`arm64_sequoia`)
instead, and stops if anything in the bundle needs a macOS newer than 15.

**Notifications**: the core calls `notify-send` for agent requests and the unlock prompt. On macOS the shell
shows them as native notifications with Allow / Deny actions and answers through the local API, so the core
only needs to skip `notify-send` where it doesn't exist.

**Firewall and Local Network.** macOS's Application Firewall is per program, not per port: the first time
UxPlay and the core listen, macOS asks once whether to allow incoming connections, and the app's setup explains
that prompt before it appears. `socketfilterfw --getglobalstate` and `--getappblocked` read its state without
rights. macOS 15 and later also ask for **Local Network** access, which the bridge (UDP on the LAN) and Bonjour
need; the app declares `NSLocalNetworkUsageDescription` and `NSBonjourServices`, and the child processes it
starts are attributed to it.

**Bluetooth (bridge setup over BLE)**: bleak uses CoreBluetooth on macOS. CoreBluetooth hides a device's
address and gives each a per-Mac UUID instead, so the bridge setup must treat the "address" as opaque (it
already only passes it back). The app declares `NSBluetoothAlwaysUsageDescription`.

**Start at Login** uses `SMAppService.mainApp`. **Keep Awake** is the phone's screen, nudged through the
bridge, so it's the core's and needs nothing here; the Mac itself is kept from sleeping while the phone mirrors
with an IOKit power assertion held by the shell.

**OCR**: macOS has Apple's Vision text recognition built in. A small Swift helper that answers the vision
socket's protocol replaces RapidOCR on the Mac, with no model download and faster than the CPU model.

**Connect Claude Code** stays in the core (claude_setup.py). On the Mac, the skill link and the `phoneuse`
wrapper point into the app bundle (`Phone Use.app/Contents/Resources/core/…`).

**Runtime directory**: there's no `XDG_RUNTIME_DIR`, so sockets go to a per-user directory under
`$TMPDIR` instead of `/tmp`.

## Packaging

`Phone Use.app`, bundle ID `co.lxe.phoneuse.mac` (Apple treats bundle IDs without regard to case, so the Linux
ID `co.lxe.PhoneUse` would collide with the iOS app's `co.lxe.phoneuse`), Apple silicon only to start, signed
with Developer ID and notarized, and shipped as a DMG; the Mac App Store is out because the app runs a Python
interpreter and helper programs and listens on the network without the sandbox.

Inside: the SwiftUI shell, a relocatable Python (python-build-standalone) with the core and its modules
(aiohttp, numpy, pillow, bleak, cryptography, PyGObject), GStreamer (the official relocatable macOS framework),
UxPlay as a separate GPL-3.0 program, and the OCR helper. Every binary and library is signed with the hardened
runtime; Python needs the `allow-unsigned-executable-memory` and `disable-library-validation` exceptions for its
extension modules.

## Order of work

1. **The core runs on the Mac from the repository** with Homebrew dependencies: config, UxPlay args and ports,
   hub encoder and audio sink, Bonjour, notifications, runtime directory. `phoneuse serve` starts, the browser
   mirror loads, the bridge is found on the LAN. Small, cross-platform changes, reviewed by the Linux side.
2. **The SwiftUI shell** (`mac/`, XcodeGen like `ios/`): menu bar item, settings window with the three tabs,
   the hero, every setting through the local API, pairing QR, bridge setup sheet, Claude Code, Start at Login,
   native notifications for agent requests. Runs the core from the repository in development.
3. **Mac-only helpers**: the Vision OCR helper, the power assertion, the firewall and Local Network steps.
4. **The bundle**: relocatable Python and GStreamer, UxPlay built in, signing and notarization, a DMG.
5. **With the phone**: AirPlay on moved ports beside the system receiver, the bridge end to end, the companion
   app over Bonjour. Then iPhone Mirroring as a "no bridge" option.
