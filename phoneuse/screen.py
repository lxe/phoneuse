"""The phone's picture: whether it is showing, screenshots of it, and finding things
in them (switches, the orange pointer). On Linux also the mirror window on screen
(Hyprland IPC, grim).

Positions are fractions of the phone screen: (0, 0) top-left, (1, 1) bottom-right.
"""
import glob
import io
import json
import os
import socket
import subprocess
import sys

import numpy as np
from PIL import Image

from . import ipc, orientation
from .config import CFG, IS_WINDOWS

MIRROR_CLASS = "phoneuse-mirror"  # window class (Wayland app id) of the mirror window, window.py
HUB_RTP_PORT = 5590  # UxPlay sends the phone's video here, to the hub (hub.py)
RTP_PORT = 5600  # the hub sends it on here, for the mirror window (window.py)
WEB_RTP_PORT = 5602  # ...and here, for the browser mirror (web.py)
FRAMES_RTP_PORT = 5604  # ...and here, for the frames service (frames.py: settle, screenshots)
# The largest RTP packet sent between UxPlay, the hub and its viewers, all on this machine. macOS refuses UDP
# datagrams over 9216 bytes on a default socket ("Message too long", net.inet.udp.maxdgram), and UxPlay's udpsink
# doesn't raise its buffer: every large packet (each keyframe, each big change) vanished, the browser mirror got
# broken frames ("Decoding error"), never started playing, and so dropped the mouse. Linux takes 60000.
RTP_MTU = 9000 if sys.platform == "darwin" else 60000
# UxPlay's mirroring stream port (its first TCP port: 7100 with `-p`, N with `-p N`); a connection on it means
# the phone is mirroring
AIRPLAY_MIRROR_PORT = CFG.get("airplay_port") or 7100
# The window's title says whether it shows the phone: input is only forwarded
# to the phone while it does (mirror_input.py, cli.py).
TITLE_LIVE = "phoneuse mirror"
TITLE_WAITING = "phoneuse mirror (not mirroring)"

# Bezel around the picture in the mirror window, in units of the screen's width.
# Presses on the bezel start edge swipes (mirror_input.py).
BEZEL = 30 / 498
SCREEN_RADIUS = 60 / 498  # the screen's corner radius, ~48 pt on the phone


def _find_hyprland():
    # tmux servers started before a Hyprland restart keep a stale signature, so
    # hyprctl fails (exit 4). Use the saved one only if its socket answers;
    # otherwise the newest instance whose socket does.
    run = os.environ.get("XDG_RUNTIME_DIR", "/run/user/%d" % os.getuid())

    def alive(sock):
        s = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        try:
            s.connect(sock)
            return True
        except OSError:
            return False
        finally:
            s.close()

    sig = os.environ.get("HYPRLAND_INSTANCE_SIGNATURE")
    if sig and alive(os.path.join(run, "hypr", sig, ".socket.sock")):
        return
    socks = sorted(glob.glob(os.path.join(run, "hypr", "*", ".socket.sock")), key=os.path.getmtime, reverse=True)
    for sock in socks:
        if alive(sock):
            os.environ["HYPRLAND_INSTANCE_SIGNATURE"] = os.path.basename(os.path.dirname(sock))
            return


if not IS_WINDOWS:
    _find_hyprland()


def hypr(cmd):
    """One Hyprland IPC request ('j/cursorpos', 'j/clients', ...) as parsed JSON (~0.03 ms)."""
    path = os.path.join(os.environ.get("XDG_RUNTIME_DIR", f"/run/user/{os.getuid()}"),
                        "hypr", os.environ.get("HYPRLAND_INSTANCE_SIGNATURE", ""), ".socket.sock")
    s = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    s.connect(path)
    s.sendall(cmd.encode())
    data = b""
    while chunk := s.recv(65536):
        data += chunk
    s.close()
    return json.loads(data)


def hypr_eval(lua):
    """Run Lua in Hyprland (Lua-config builds). Returns False when unsupported."""
    r = subprocess.run(["hyprctl", "eval", lua], capture_output=True, text=True)
    return r.returncode == 0 and "unknown request" not in r.stdout and not r.stdout.startswith("error")


def airplay_mirroring():
    """True while a phone is mirroring to UxPlay (an open connection on its
    mirroring port)."""
    if IS_WINDOWS:  # None: couldn't tell this time (the caller keeps what it knew)
        import psutil
        try:
            return any(c.laddr and c.laddr.port == AIRPLAY_MIRROR_PORT and c.status == psutil.CONN_ESTABLISHED
                       for c in psutil.net_connections(kind="tcp"))
        except (OSError, psutil.Error):
            return None
    if sys.platform == "darwin":
        # No /proc, and netstat no longer lists TCP connections on recent macOS (the phone often connects over
        # IPv6 link-local too), so ask lsof: our own UxPlay's sockets are visible without root.
        try:
            out = subprocess.run(["lsof", "-nP", f"-iTCP:{AIRPLAY_MIRROR_PORT}", "-sTCP:ESTABLISHED", "-t"],
                                 capture_output=True, text=True, timeout=3).stdout
        except (OSError, subprocess.SubprocessError):
            return False
        return bool(out.strip())
    port = f":{AIRPLAY_MIRROR_PORT:04X}"
    for path in ("/proc/net/tcp6", "/proc/net/tcp"):
        try:
            for line in open(path).readlines()[1:]:
                f = line.split()
                if f[1].endswith(port) and f[3] == "01":  # local port, ESTABLISHED
                    return True
        except OSError:
            pass
    return False


def mirror_live(client=None):
    """True when the phone's screen arrives. Asks the browser mirror, which runs under both
    `phoneuse up` and `serve` on every platform; only without it (or for a given window)
    does it read the mirror window's title, which needs Hyprland, or on Windows (no window
    of its own) asks the frames service."""
    if client is None:
        import json
        import urllib.request
        try:
            with urllib.request.urlopen(f"http://127.0.0.1:{CFG['web_port']}/api/picture", timeout=2) as r:
                return bool(json.load(r).get("live"))
        except (OSError, ValueError):
            pass
        if IS_WINDOWS:
            try:
                line, _ = frames_request("status", timeout=2)
                return "live=1" in line
            except OSError:
                return False
        try:
            client = mirror_client()
        except OSError:  # no Hyprland (the Flatpak, a Mac): no window to ask
            return False
    return bool(client) and client["title"] == TITLE_LIVE


def hub_request(cmd, timeout=3.0):
    """One control request to the video hub (hub.py / avhub.py); returns (reply line, payload)."""
    return ipc.request("hub", cmd, timeout)


def frames_request(cmd, timeout=3.0):
    """One request to the frames service (frames.py): status, settle, shot."""
    return ipc.request("frames", cmd, timeout)


def mirror_client():
    return next((c for c in hypr("j/clients") if c["class"] == MIRROR_CLASS), None)


def mirror_window():
    c = mirror_client()
    if c is None:
        raise RuntimeError("mirror window not found")
    return c["at"], c["size"]


def design_size(pts=None):
    """(width, height) of the window in units of the screen's short side (its width when
    upright): the bezel is a fraction of that, so a sideways phone keeps the same bezel.
    `pts`: the screen in points as it faces (orientation.py); default: as the frames say now."""
    pts = pts or orientation.current_points()
    short = min(pts)
    return pts[0] / short + 2 * BEZEL, pts[1] / short + 2 * BEZEL


def window_size(screen_width, pts=None):
    """The window for a phone whose screen is `screen_width` px across its short side."""
    dw, dh = design_size(pts)
    return round(dw * screen_width), round(dh * screen_width)


def picture_rect(at, size, pts=None):
    """Where the phone picture sits on screen, for a mirror window at `at` with
    `size`. The rest of the window is bezel. The picture is aspect-fit and
    centered, so a window of another shape gets a wider bezel on two sides, never
    a squished picture. A sideways phone's picture is wider than tall."""
    (x, y), (w, h) = at, size
    pts = pts or orientation.current_points()
    short = min(pts)
    dw, dh = design_size(pts)
    u = min(w / dw, h / dh)  # pixels per screen short side
    sw, sh = u * pts[0] / short, u * pts[1] / short
    return x + (w - sw) / 2, y + (h - sh) / 2, sw, sh


def grab_png():
    """PNG of the phone's screen. From the frames service when it runs (the
    current picture, so windows on top don't matter); on Linux otherwise a capture
    of the mirror window's area of the screen, which must not be covered."""
    try:
        line, png = frames_request("shot")
        if line.startswith("ok"):
            return png
    except OSError:
        pass
    if IS_WINDOWS:
        raise RuntimeError("no picture of the phone (is `phoneuse up` running, and the phone mirroring?)")
    x, y, w, h = picture_rect(*mirror_window())
    return subprocess.check_output(["grim", "-g", f"{round(x)},{round(y)} {round(w)}x{round(h)}", "-"])


def grab_rgb():
    return np.asarray(Image.open(io.BytesIO(grab_png())).convert("RGB"))


def _components(mask, cell=4):
    """Connected components of a boolean mask on a coarse grid: (cells, xs, ys) each."""
    h, w = mask.shape
    g = mask[: h // cell * cell, : w // cell * cell].reshape(h // cell, cell, w // cell, cell).any(axis=(1, 3))
    lab = np.zeros(g.shape, np.int32)
    out = []
    for y0, x0 in zip(*np.nonzero(g)):
        if lab[y0, x0]:
            continue
        lab[y0, x0] = len(out) + 1
        stack, pts = [(y0, x0)], []
        while stack:
            cy, cx = stack.pop()
            pts.append((cy, cx))
            for ny in (cy - 1, cy, cy + 1):
                for nx in (cx - 1, cx, cx + 1):
                    if 0 <= ny < g.shape[0] and 0 <= nx < g.shape[1] and g[ny, nx] and not lab[ny, nx]:
                        lab[ny, nx] = lab[y0, x0]
                        stack.append((ny, nx))
        ys, xs = (np.array(v) for v in zip(*pts))
        out.append((len(pts), (xs + 0.5) * cell, (ys + 0.5) * cell))
    return out


def find_switches(frame_rgb):
    """On/off switches (iOS toggles) in a screenshot: [(x, y, on)], positions as
    fractions. A toggle is a pill about 61 x 31 pt: green when on, grey when off
    (dark-mode grey; light-mode toggles aren't detected). OCR can't read them."""
    a = frame_rgb.astype(np.int16)
    h, w = a.shape[:2]
    pt = w / 402  # pixels per phone point (portrait)
    r, g, b = a[..., 0], a[..., 1], a[..., 2]
    masks = {
        True: (g > 170) & (r < 110) & (b < 140) & (g - r > 90),  # green track
        False: (abs(r - 90) < 14) & (abs(g - r) < 6) & (abs(b - r) < 10),  # grey track
    }
    found = []
    for on, mask in masks.items():
        for n, xs, ys in _components(mask, cell=2):
            bw, bh = xs.max() - xs.min(), ys.max() - ys.min()
            if 48 * pt <= bw <= 72 * pt and 22 * pt <= bh <= 36 * pt and xs.mean() > 0.6 * w:  # toggles sit at the right
                found.append((float(xs.mean() / w), float(ys.mean() / h), on))
    return sorted(found, key=lambda t: t[1])


def orange_mask(frame_rgb):
    r, g, b = (frame_rgb[..., i].astype(np.int16) for i in range(3))
    return (r > 200) & (g > 100) & (g < 185) & (b < 90) & (r - g > 60)


def find_orange(frame_rgb, baseline_rgb=None):
    """Centre (fractions) of the orange Pointer Control ring, or None.

    Needs Pointer Control: Color = Orange, large size. The ring is the orange
    component that is big (about 22% of the screen width across) and hollow,
    unlike orange app icons, which are small and solid.
    """
    mask = orange_mask(frame_rgb)
    if baseline_rgb is not None:  # drop orange that was already there (icons etc.)
        mask &= ~orange_mask(baseline_rgb)
    h, w = mask.shape
    best = None
    for n, xs, ys in _components(mask):
        bw, bh = np.ptp(xs) + 4, np.ptp(ys) + 4
        size = max(bw, bh) / w
        fill = n * 16 / (bw * bh)
        if 0.12 < size < 0.35 and fill < 0.5 and (best is None or n > best[0]):
            # a ring cut off by a screen edge: centre from the full-size side
            cx = (xs.min() + xs.max()) / 2
            cy = (ys.min() + ys.max()) / 2
            d = max(bw, bh)
            if bw < d * 0.9:
                cx = xs.max() - d / 2 if xs.min() <= 4 else xs.min() + d / 2
            if bh < d * 0.9:
                cy = ys.max() - d / 2 if ys.min() <= 4 else ys.min() + d / 2
            best = (n, cx / w, cy / h)
    return best[1:] if best else None
