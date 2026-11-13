# Packaging Phone Use for Linux

This is the plan for turning phoneuse into an app that someone installs, opens and uses without a terminal. It
records what the audit of 2026-11-08 found, what the package holds, and the order of the work.

## Format: a Flatpak

Phone Use ships as a Flatpak, app ID `co.lxe.PhoneUse`, on the GNOME 50 runtime. The runtime already carries
everything phoneuse is built on (GTK 4, libadwaita, GStreamer, PyGObject, PipeWire and PulseAudio access), and
Flathub reaches every distribution. An AppImage would have to rely on whatever GStreamer plugins, Avahi library
and GTK the host happens to have, which is the part most likely to break on someone else's machine. The first
builds go out as a `.flatpak` bundle; Flathub comes once the app is stable.

## What fits in the sandbox, and what doesn't

The audit went through every part that touches the host.

**Bridge mode fits completely.** The PC talks to the bridge over outgoing UDP, and the bridge's answers come back on
the same flow, so no inbound port is needed. Finding a bridge already copes with firewalls: a broadcast first,
then a direct question to every address on the /24. Setting up a bridge uses BLE through BlueZ over the system
bus, which a Flatpak may talk to (`--system-talk-name=org.bluez`). Without the PC's own Bluetooth, the HID
daemon needs no root, no raw HCI sockets and no change to bluetoothd.

**The PC's own Bluetooth does not fit.** That mode runs a root daemon and turns off bluetoothd's input plugin,
which also stops the user's own Bluetooth keyboards and mice from working with the PC. No consumer app should do
that, and a Flatpak can't. The app is bridge-only; the PC-Bluetooth mode stays in the repository as the developer
setup (`phoneuse install`).

**The browser mirror fits, and replaces the mirror window.** The page receives the phone's H.264 untouched and
decodes it with WebCodecs, so the PC decodes nothing for it. The mirror window depends on Hyprland's IPC and is
the one Wayland-compositor-specific part of phoneuse; it stays out of the app. A browser tab also suits the main
use better: an agent in Claude's browser pane watches the same page the owner does. Screenshots for agents come
from the frames service, which decodes the stream itself (`openh264dec` or `avdec_h264` from the codecs
extension), so `grim` and the window are never needed.

**UxPlay is built into the package**, with its fixed ports (`-p`). It advertises AirPlay through the host's Avahi
over the system bus (`--system-talk-name=org.freedesktop.Avahi`), as the companion API's Bonjour service already
does. UxPlay is GPL-3.0 and ships as a separate program inside the bundle.

**OCR fits on the CPU.** RapidOCR and onnxruntime install as Python modules in the build (about 1.2 s a screen
on the CPU against 0.18 s on an NVIDIA GPU). GPU OCR stays a developer option.

**The Siri request for the pointer fits**: it plays a WAV through PipeWire, which the sandbox reaches.

## Inbound ports, and the setup step for them

Two things on the LAN must reach the PC: the iPhone's AirPlay connection (TCP 7000, 7001, 7100 and UDP 6000,
6001, 7011) and the companion app (TCP 8778). Fedora Workstation, Ubuntu and Arch let these through as installed.
openSUSE, and any machine where someone turned on firewalld or ufw, block them; this PC is one, where the ports
were opened by hand.

Reading a firewalld zone's ports needs administrator rights on most systems (polkit's
`org.fedoraproject.FirewallD1.config.info` is `auth_admin_keep`; on Arch, asking without them opens a password
dialog), so the app never inspects the firewall on its own. It only learns whether one runs: firewalld's name
on the system bus, or ufw's `ENABLED=yes`, both readable without rights. Whether the ports are open shows in what
happens: the iPhone's AirPlay connection reaches UxPlay, and the companion app reaches its API.

When a firewall runs, setup has a step **Allow on this network** with one button. The button asks for the
administrator password once (polkit keeps the answer for a few minutes), reads the zone of the LAN interface,
and opens exactly these ports in it, now and permanently, over firewalld's D-Bus API; nothing else changes. For
ufw, which has no D-Bus API, the button runs `pkexec ufw allow` for the same ports on the host. When the iPhone
picks the receiver and no AirPlay connection arrives, the status page points back to that step, and for any
other firewall it lists the ports to open.

## The app

The GTK window (libadwaita, Python) is the control panel; the phone itself opens in the browser.

The first run walks through setup: the network check above, adding a bridge (the BLE code comparison and Wi-Fi
choice that the browser mirror already does), starting Screen Mirroring on the iPhone, turning on the pointer,
pairing the companion app, and connecting Claude Code. After that, the window opens on a status page: whether
the iPhone mirrors, which bridge it uses and how good its signal is, and who controls the phone. Its main button
opens the phone in the browser. Settings hold the receiver name, Agent control, keep-awake, sound, bridges, the
paired iPhones and the Claude Code connection. Services start with the app and keep running in the background
(the background portal), so agents can use the phone with the window closed.

**Connecting Claude Code** links `~/.claude/skills/phoneuse` to the installed app's skill, through Flatpak's
`active` path so the link follows updates, and puts a `phoneuse` command in `~/.local/bin` that runs the CLI
inside the sandbox. Agents use the phone through that command; there is no MCP server. The settings page shows
whether both are in place and can remove them. Each time the app starts it redoes the connection for someone
who connected before (claude_setup.refresh), so older setups catch up: a copied skill, or the MCP server
earlier versions added to `~/.claude.json`.

**Running the Flatpak from a checkout**: `scripts/dev-link.sh` sets a per-user Flatpak override that puts the
checkout first on the app's Python path, so the app, its services and the `phoneuse` command all run the
checkout's code, and links the skill to the checkout. Commands pick up edits at once; the app's services
when it restarts. `scripts/dev-link.sh --off` goes back to the installed code.

## Open problems

**The agent gate trusts a process's command line.** The HID daemon treats any process started as
`python -m phoneuse.web` as the owner. On a single-user desktop any program the user runs can claim that. The
packaged daemon should accept owner commands only from processes the app itself started.

**The companion app** has to reach the App Store before strangers can pair it.

## Order of work

1. A headless core: one supervisor (`phoneuse serve`) runs UxPlay, the hub, the frames service, the browser mirror
   and companion, and the HID daemon as the user in bridge-only mode, with no mirror window. It also runs
   from the repository, so the developer setup and the package share it.
2. The network check and the **Allow on this network** button, tested on this PC's firewalld.
3. The Flatpak manifest: UxPlay and its libraries, the Python modules, OCR, permissions. Builds and runs here.
4. The GTK app: first-run setup, status, settings, Claude Code connection, with a design pass before it ships.
5. The push relay ([PUSH-RELAY.md](PUSH-RELAY.md)), then Flathub.
