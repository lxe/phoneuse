"""phoneuse settings: defaults, overridden by config.toml in the config directory
(~/.config/phoneuse on Linux, %APPDATA%\\phoneuse on Windows).

Example config.toml:

    name = "Phone Use"               # AirPlay receiver name (default: "Phone Use on <this computer>")
    screen_points = "auto"           # your iPhone's screen in points, such as [402, 874]; auto: from
                                     # the model of the phone that last started mirroring
    decoder = "auto"                 # Linux: GStreamer H.264 decoder; auto picks NVDEC if present
    encoder = "auto"                 # Windows: the hub's H.264 encoder (h264_nvenc, libx264, h264_mf)
    mirror_width = 498               # Linux: mirror window width in logical pixels
    web_port = 8777                  # browser mirror: http://localhost:8777
    bluetooth = "pc"                 # Linux: "pc": this PC's Bluetooth and the bridge (root daemon, phoneuse
                                     # install); "bridge": the bridge only, everything runs as the user (the app)
    uxplay = "auto"                  # Windows: path to uxplay.exe (auto: C:\\msys64\\ucrt64\\bin)
    audio = true                     # play the phone's audio on this PC
    app_window = true                # Windows: `phoneuse up` opens the mirror in its own window
    service_port = 47810             # Windows: first of the local ports phoneuse's services use
"""
import os
import shutil
import socket
import subprocess
import sys
import tempfile

try:
    import tomllib
except ModuleNotFoundError:  # Python < 3.11
    tomllib = None

IS_WINDOWS = sys.platform == "win32"

MACOS = sys.platform == "darwin"


def computer_name():
    """This computer's name as people see it: macOS's Computer Name, the pretty hostname
    (hostnamectl) on Linux, else the short hostname."""
    if sys.platform == "darwin":
        try:
            n = subprocess.run(["scutil", "--get", "ComputerName"], capture_output=True, text=True,
                               timeout=2).stdout.strip()
            if n:
                return n
        except (OSError, subprocess.SubprocessError):
            pass
    try:
        with open("/etc/machine-info") as f:
            for line in f:
                if line.startswith("PRETTY_HOSTNAME="):
                    n = line.split("=", 1)[1].strip().strip('"')
                    if n:
                        return n
    except OSError:
        pass
    return socket.gethostname().split(".")[0]


DEFAULTS = {
    # Each computer's receiver gets its own name, so two on one network don't show up in
    # Screen Mirroring as "Phone Use" and "Phone Use (2)".
    "name": f"Phone Use on {computer_name()}",
    "screen_points": "auto",
    "decoder": "auto",
    "encoder": "auto",
    "mirror_width": 498,
    "web_port": 8777,
    # How the PC pushes to the companion app (docs/PUSH-RELAY.md): "auto" uses this PC's own APNs key
    # (~/.config/phoneuse/apns.json) when it has one and the push relay otherwise; "direct", "relay" or "off".
    "push": "auto",
    # the app (a Flatpak) can only use the bridge; a checkout uses this PC's Bluetooth too
    # (a Mac has only the bridge: macOS can't make the Mac a Bluetooth keyboard and mouse)
    "bluetooth": "bridge" if os.path.exists("/.flatpak-info") or MACOS else "pc",
    # UxPlay's base port (TCP and UDP n, n+1, n+2); None: its legacy fixed ports (7000, 7001, 7100;
    # 6000, 6001, 7011). macOS's own AirPlay Receiver holds 7000, so the Mac uses 7200.
    "airplay_port": 7200 if MACOS else None,
    "uxplay": "auto",
    "audio": True,
    "app_window": True,
    "service_port": 47810,
}


def config_dir():
    if IS_WINDOWS:
        return os.path.join(os.environ.get("APPDATA") or os.path.expanduser("~"), "phoneuse")
    base = os.environ.get("XDG_CONFIG_HOME", os.path.expanduser("~/.config"))
    return os.path.join(base, "phoneuse")


def config_path():
    return os.path.join(config_dir(), "config.toml")


def _runtime_dir():
    """Logs, sockets and small state files that only matter while phoneuse runs."""
    if IS_WINDOWS:
        d = os.path.join(os.environ.get("LOCALAPPDATA") or os.path.expanduser("~"), "phoneuse", "run")
        os.makedirs(d, exist_ok=True)
        return d
    # macOS has no XDG_RUNTIME_DIR; its per-user temporary directory is private to the user like one
    d = os.environ.get("XDG_RUNTIME_DIR") or (tempfile.gettempdir() if MACOS else "/tmp")
    if os.environ.get("FLATPAK_ID"):
        # Each `flatpak run` gets its own private runtime directory; only app/<id> is shared between
        # instances, so the service's sockets go there for agents' `phoneuse` commands to reach.
        d = os.path.join(d, "app", os.environ["FLATPAK_ID"])
    return d


def load():
    cfg = dict(DEFAULTS)
    if tomllib is None:
        return cfg
    try:
        with open(config_path(), "rb") as f:
            cfg.update(tomllib.load(f))
    except FileNotFoundError:
        pass
    return cfg


CFG = load()
RUNTIME_DIR = _runtime_dir()
# Bridge only: the HID daemon runs as the user and never touches this PC's Bluetooth.
USER_DAEMON = CFG["bluetooth"] == "bridge"  # the app: the HID daemon runs as the user, never as root
# The app can use this PC's Bluetooth too, once bluetoothd is prepared for it (one admin password;
# see bluetooth_setup.py): this file exists while that is on.
PC_BLUETOOTH_FLAG = os.path.join(os.path.dirname(config_path()), "pc-bluetooth")
BRIDGE_ONLY = USER_DAEMON and not os.path.exists(PC_BLUETOOTH_FLAG)
# The HID daemon's command socket: the root service's, or the user's own in bridge-only mode.
HID_SOCKET = os.path.join(RUNTIME_DIR, "phoneuse-hid.sock") if USER_DAEMON else "/run/phoneuse-hid.sock"
STATE_DIR = os.path.join(os.environ.get("XDG_STATE_HOME", os.path.expanduser("~/.local/state")), "phoneuse")
NAME = CFG["name"]


def runtime_path(name):
    return os.path.join(RUNTIME_DIR, name)


# The screen in points (Display Zoom off) by model identifier, as AirPlay reports it
# ("iPhone15,3"). Gestures are planned in points and screenshots are scaled to them, so
# a wrong size makes every drag and swipe off by the same factor (taps aren't affected).
_SCREENS = {
    (320, 568): "8,4",
    (375, 667): "8,1 9,1 9,3 10,1 10,4 12,8 14,6",
    (414, 736): "8,2 9,2 9,4 10,2 10,5",
    (375, 812): "10,3 10,6 11,2 12,3 13,1 14,4",
    (414, 896): "11,4 11,6 11,8 12,1 12,5",
    (390, 844): "13,2 13,3 14,2 14,5 14,7 17,5",
    (428, 926): "13,4 14,3 14,8",
    (393, 852): "15,2 15,4 16,1 17,3",
    (430, 932): "15,3 15,5 16,2 17,4",
    (402, 874): "17,1 18,1 18,3",
    (440, 956): "17,2 18,2",
    (420, 912): "18,4",
}
MODEL_POINTS = {f"iPhone{m}": pts for pts, models in _SCREENS.items() for m in models.split()}
DEFAULT_POINTS = (402, 874)  # iPhone 17 Pro
MODEL_FILE = runtime_path("mirroring-model")  # written by cli.follow_sender


def mirroring_model():
    """The model identifier of the iPhone that last started mirroring, or None."""
    try:
        with open(MODEL_FILE, encoding="utf-8-sig") as f:
            return f.read().strip() or None
    except OSError:
        return None


def screen_points():
    """The phone's screen in points, and where that came from: config.toml's
    screen_points, else the model of the phone that last started mirroring, else
    an iPhone 17 Pro's."""
    configured = CFG.get("screen_points", "auto")
    if isinstance(configured, (list, tuple)) and len(configured) == 2 and min(configured) > 0:
        return (int(configured[0]), int(configured[1])), "config.toml"
    model = mirroring_model()
    if model in MODEL_POINTS:
        return MODEL_POINTS[model], model
    return DEFAULT_POINTS, (f"unknown model {model}" if model else "no phone has mirrored yet")


SCREEN_PT, SCREEN_SOURCE = screen_points()
PHONE_ASPECT = SCREEN_PT[0] / SCREEN_PT[1]


# Local services. On Linux these are unix sockets (the root HID daemon's is root-owned and
# group-writable; HID_SOCKET above); on Windows, TCP ports on 127.0.0.1 guarded by a per-user
# token (ipc.py).
VISION_SOCKET = runtime_path("phoneuse-vision.sock")
HUB_SOCKET = runtime_path("phoneuse-hub.sock")
FRAMES_SOCKET = runtime_path("phoneuse-frames.sock")
SERVICE_PORTS = {name: int(CFG["service_port"]) + i for i, name in enumerate(("hid", "hub", "frames", "vision"))}


def decoder():
    """H.264 decoder for the mirror (Linux, GStreamer). Frames must leave the decoder as
    soon as they are decoded: iOS only sends a frame when the screen changes and doesn't
    declare 'no reordering', so a default decoder holds each frame until the next arrives
    (measured 200 ms). nvh264dec with compliance=flexible releases them in ~1 ms;
    the software decoder releases them immediately."""
    d = CFG["decoder"]
    if d != "auto":
        return d
    if shutil.which("gst-inspect-1.0"):
        r = subprocess.run(["gst-inspect-1.0", "nvh264dec"], capture_output=True)
        if r.returncode == 0:
            return "nvh264dec max-display-delay=0 compliance=flexible"
    return "avdec_h264"
