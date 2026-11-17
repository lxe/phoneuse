"""Inbound ports Phone Use needs on the local network, and the one setup step that opens them.

The iPhone connects to this PC twice: Screen Mirroring (AirPlay, served by UxPlay with fixed
ports) and the companion app's API. Most desktops let both through as installed; a running
firewalld or ufw blocks them.

detect() never needs rights: it only learns which firewall runs. Reading a firewalld zone's
ports needs administrator rights on most systems (polkit asks for a password), so nothing here
reads the firewall until the user asks to open the ports: allow() asks once (polkit keeps the
answer a few minutes), finds the zone of the interface the LAN route uses, and opens exactly
these ports in it, now and permanently.
"""
import os
import shutil
import socket
import subprocess
import sys
import time

from .companion import PORT as COMPANION_PORT
from .config import CFG, MACOS, config_path

# UxPlay's fixed ports (`uxplay -p`) and the companion API.
PORTS = [(7000, "tcp"), (7001, "tcp"), (7100, "tcp"), (6000, "udp"), (6001, "udp"), (7011, "udp"),
         (COMPANION_PORT, "tcp")]
if CFG.get("airplay_port"):  # `uxplay -p N`: TCP and UDP N, N+1, N+2
    _n = CFG["airplay_port"]
    PORTS = [(p, proto) for proto in ("tcp", "udp") for p in (_n, _n + 1, _n + 2)] + [(COMPANION_PORT, "tcp")]

# macOS's Application Firewall filters by program, not by port: macOS asks once, for each program
# that listens, whether to accept incoming connections. Reading its state needs no rights.
SOCKETFILTERFW = "/usr/libexec/ApplicationFirewall/socketfilterfw"

FIREWALLD = "org.fedoraproject.FirewallD1"
# What this PC has shown about its firewall: the ports were opened here, or the iPhone's
# mirroring reached this PC (proof that they are open). Either ends the setup step.
STATE = os.path.join(os.path.dirname(config_path()), "network.json")


def confirmed():
    try:
        import json
        with open(STATE) as f:
            d = json.load(f)
        return bool(d.get("allowed") or d.get("mirrored"))
    except (OSError, ValueError):
        return False


def remember(**kw):
    import json
    d = {}
    try:
        with open(STATE) as f:
            d = json.load(f)
    except (OSError, ValueError):
        pass
    if all(d.get(k) == v for k, v in kw.items()):
        return
    d.update(kw)
    os.makedirs(os.path.dirname(STATE), exist_ok=True)
    with open(STATE, "w") as f:
        json.dump(d, f)


def _system_bus():
    from gi.repository import Gio
    return Gio.bus_get_sync(Gio.BusType.SYSTEM)


def _firewalld_running():
    try:
        from gi.repository import GLib
    except ImportError:  # no D-Bus bindings (Windows): no firewalld either
        return False
    try:
        r = _system_bus().call_sync("org.freedesktop.DBus", "/org/freedesktop/DBus", "org.freedesktop.DBus",
                                    "NameHasOwner", GLib.Variant("(s)", (FIREWALLD,)), GLib.VariantType("(b)"),
                                    0, 2000, None)
        return r.unpack()[0]
    except Exception:
        return False


def _ufw_enabled():
    try:
        with open("/etc/ufw/ufw.conf") as f:  # readable by everyone; `ufw status` needs root
            return any(l.strip() == "ENABLED=yes" for l in f)
    except OSError:
        return False


_windows_seen = (0.0, None)  # (when, answer): reading the rules runs PowerShell (about a second)


def _windows():
    """"windows" while Windows Firewall has no rule letting the iPhone reach UxPlay (or a Block
    rule wins), else None. Asked at most every 30 s; reading the rules needs no rights."""
    global _windows_seen
    at, answer = _windows_seen
    if time.time() - at < 30:
        return answer
    from . import doctor
    from .cli import uxplay_path
    ux = uxplay_path()
    answer = "windows" if ux and doctor.firewall_state(ux) in ("missing", "block") else None
    _windows_seen = (time.time(), answer)
    return answer


def _allow_windows():
    """The firewall rule for UxPlay (windows/scripts/firewall.ps1), elevated: one UAC prompt."""
    global _windows_seen
    from .cli import ROOT, uxplay_path
    script = os.path.join(ROOT, "windows", "scripts", "firewall.ps1")
    cmd = (f"$p = Start-Process powershell -Verb RunAs -Wait -PassThru -WindowStyle Hidden -ArgumentList "
           f"'-NoProfile -ExecutionPolicy Bypass -File \"{script}\" -Program \"{uxplay_path()}\"'; exit $p.ExitCode")
    r = subprocess.run(["powershell", "-NoProfile", "-NonInteractive", "-Command", cmd], capture_output=True,
                       text=True, timeout=300, creationflags=0x08000000)
    _windows_seen = (0.0, None)
    if r.returncode:
        err = (r.stderr or "").strip()
        if "cancel" in err.lower():  # the UAC prompt was dismissed
            raise RuntimeError("cancelled")
        raise RuntimeError(err.splitlines()[-1] if err else f"the firewall script failed (exit {r.returncode})")
    remember(allowed=True)
    return "Windows Firewall now lets the iPhone reach Phone Use."


def detect():
    """"firewalld", "ufw", "macos", "windows" or None: which firewall may block the iPhone.
    Needs no rights."""
    if sys.platform == "win32":
        return _windows()
    if MACOS:
        try:
            out = subprocess.run([SOCKETFILTERFW, "--getglobalstate"], capture_output=True, text=True,
                                 timeout=5).stdout
        except (OSError, subprocess.SubprocessError):
            return None
        return "macos" if "enabled" in out.lower() else None
    if _firewalld_running():
        return "firewalld"
    if _ufw_enabled():
        return "ufw"
    return None


def lan_interface():
    """The interface this PC reaches the local network on: the default route's, read from
    /proc/net/route (no `ip` command needed, which the Flatpak doesn't have)."""
    best = None
    try:
        with open("/proc/net/route") as f:
            for line in f.readlines()[1:]:
                p = line.split()
                if len(p) > 6 and p[1] == "00000000" and int(p[3], 16) & 2:  # default route, gateway flag
                    if best is None or int(p[6]) < best[1]:
                        best = (p[0], int(p[6]))
    except OSError:
        pass
    return best[0] if best else None


def _host(cmd):
    """Run a command on the host: through flatpak-spawn inside the Flatpak, directly otherwise."""
    if os.path.exists("/.flatpak-info"):
        cmd = ["flatpak-spawn", "--host"] + cmd
    return subprocess.run(cmd, capture_output=True, text=True, timeout=120)


def allow():
    """Open PORTS in the active firewall, asking for the administrator password once.
    Returns a sentence for the user. Raises RuntimeError when it couldn't."""
    fw = detect()
    if fw == "windows":
        return _allow_windows()
    if fw == "macos":  # no ports to open: macOS asks about each program the first time it listens
        return ("macOS asks whether Phone Use may accept incoming connections the first time it listens. "
                "Choose Allow, or turn it on in System Settings › Network › Firewall › Options.")
    if fw == "firewalld":
        return _allow_firewalld()
    if fw == "ufw":
        r = None
        for port, proto in PORTS:
            r = _host(["pkexec", "ufw", "allow", f"{port}/{proto}", "comment", "Phone Use"])
            if r.returncode == 126:
                raise RuntimeError("cancelled")
            if r.returncode:
                raise RuntimeError(r.stderr.strip() or "ufw refused")
        remember(allowed=True)
        return "ufw now lets the iPhone reach Phone Use."
    return "No firewall is running, so nothing needs opening."


def _allow_firewalld():
    from gi.repository import Gio, GLib
    bus = _system_bus()
    flags = Gio.DBusCallFlags.ALLOW_INTERACTIVE_AUTHORIZATION  # polkit asks for the password

    def call(path, iface, method, args, rtype=None):
        r = bus.call_sync(FIREWALLD, path, iface, method, args, GLib.VariantType(rtype) if rtype else None,
                          flags, 120000, None)
        return r.unpack() if rtype else None

    root, zone_if = "/org/fedoraproject/FirewallD1", FIREWALLD + ".zone"
    try:
        iface = lan_interface()
        zone = call(root, zone_if, "getZoneOfInterface", GLib.Variant("(s)", (iface,)), "(s)")[0] if iface else ""
        if not zone:
            zone = call(root, zone_if, "getDefaultZone", None, "(s)")[0]
        cfg_path = call(root + "/config", FIREWALLD + ".config", "getZoneByName", GLib.Variant("(s)", (zone,)), "(o)")[0]
        for port, proto in PORTS:
            p = (str(port), proto)
            if not call(root, zone_if, "queryPort", GLib.Variant("(sss)", (zone,) + p), "(b)")[0]:
                call(root, zone_if, "addPort", GLib.Variant("(sssi)", (zone,) + p + (0,)), "(s)")
            if not call(cfg_path, FIREWALLD + ".config.zone", "queryPort", GLib.Variant("(ss)", p), "(b)")[0]:
                call(cfg_path, FIREWALLD + ".config.zone", "addPort", GLib.Variant("(ss)", p))
    except GLib.Error as e:
        if "NotAuthorized" in str(e) or "AccessDenied" in str(e) or "Timeout" in str(e):
            raise RuntimeError("cancelled") from e
        Gio.DBusError.strip_remote_error(e)  # "GDBus.Error:org.fedoraproject...: X" -> "X"
        raise RuntimeError(f"firewalld said {e.message}") from e
    remember(allowed=True)
    return f"The firewall now lets the iPhone reach Phone Use (zone {zone}, interface {iface})."


def main():
    import sys
    if sys.argv[1:] == ["--allow"]:
        try:
            print(allow())
        except RuntimeError as e:
            sys.exit(f"couldn't open the ports: {e}")
        return
    fw = detect()
    ports = ", ".join(f"{p}/{proto}" for p, proto in PORTS)
    print(f"firewall: {fw or 'none'}; Phone Use needs {ports} open to the local network")


if __name__ == "__main__":
    main()
