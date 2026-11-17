"""This PC's Bluetooth as the iPhone's keyboard and mouse, for the app (no root daemon).

The input service runs as the user and gets the HID connections from bluetoothd (hid_daemon.py,
setup_dbus). For that, bluetoothd needs two changes once, as root:
  - without its input plugin, which holds the HID channels (PSMs 17/19) for keyboards and mice
    that connect TO this PC; with it off, this PC can't use its own Bluetooth keyboards and mice;
    without its hostname plugin, which would reset the device class;
  - the device class of a keyboard and pointing device, so the iPhone lists this PC as one.
enable() makes both changes with one administrator password (pkexec, on the host when inside
the Flatpak) and turns the feature on; disable() undoes them. The drop-in is the same file
`phoneuse install` writes, so the two setups never stack.
"""
import os
import subprocess

from .config import PC_BLUETOOTH_FLAG

DROPIN = "/etc/systemd/system/bluetooth.service.d/phoneuse.conf"
MAIN_CONF = "/etc/bluetooth/main.conf"
CLASS = "0x0025C0"

ENABLE = r"""
set -e
BIN=$(for p in /usr/libexec/bluetooth/bluetoothd /usr/lib/bluetooth/bluetoothd /usr/sbin/bluetoothd; do
        [ -x "$p" ] && echo "$p" && break; done)
[ -n "$BIN" ] || { echo "bluetoothd not found" >&2; exit 3; }
mkdir -p "$(dirname "@DROPIN@")"
printf '[Service]\nExecStart=\nExecStart=%s --noplugin=input,hostname\n' "$BIN" > "@DROPIN@"
if [ -f "@CONF@" ] && ! grep -q '^# phoneuse$' "@CONF@"; then
  if grep -q '^\[General\]' "@CONF@"; then
    sed -i '/^\[General\]/a # phoneuse\nClass = @CLASS@' "@CONF@"
  else
    printf '[General]\n# phoneuse\nClass = @CLASS@\n' >> "@CONF@"
  fi
elif [ ! -f "@CONF@" ]; then
  mkdir -p "$(dirname "@CONF@")"; printf '[General]\n# phoneuse\nClass = @CLASS@\n' > "@CONF@"
fi
systemctl daemon-reload
systemctl restart bluetooth
"""

DISABLE = r"""
set -e
rm -f "@DROPIN@"
[ -f "@CONF@" ] && sed -i '/^# phoneuse$/,+1d' "@CONF@"
systemctl daemon-reload
systemctl restart bluetooth
"""


def _fill(script):
    return script.replace("@DROPIN@", DROPIN).replace("@CONF@", MAIN_CONF).replace("@CLASS@", CLASS)


def _host(cmd):
    if os.path.exists("/.flatpak-info"):
        cmd = ["flatpak-spawn", "--host"] + cmd
    return subprocess.run(cmd, capture_output=True, text=True, timeout=180)


def prepared():
    """Whether bluetoothd runs without its input plugin (our drop-in). Needs no rights."""
    r = _host(["cat", DROPIN])
    return r.returncode == 0 and "--noplugin=input" in r.stdout


def on():
    """Whether this PC's own Bluetooth can be the iPhone's keyboard and mouse. On Windows always:
    the LE service (windows/hid.py) needs no setup and no rights."""
    import sys
    return sys.platform == "win32" or os.path.exists(PC_BLUETOOTH_FLAG)


def _run_as_admin(script):
    r = _host(["pkexec", "sh", "-c", _fill(script)])
    if r.returncode in (126, 127):  # the password dialog was dismissed, or pkexec refused
        raise RuntimeError("cancelled")
    if r.returncode:
        raise RuntimeError(r.stderr.strip() or f"setup failed ({r.returncode})")


def enable():
    """Prepare bluetoothd (asks for the administrator password once) and turn the feature on.
    The input service must restart to pick it up."""
    if not prepared():
        _run_as_admin(ENABLE)
    os.makedirs(os.path.dirname(PC_BLUETOOTH_FLAG), exist_ok=True)
    open(PC_BLUETOOTH_FLAG, "w").close()


def disable(undo_system=True):
    """Turn the feature off and, unless asked not to, give bluetoothd its input plugin back
    (asks for the password), so this PC can use Bluetooth keyboards and mice again."""
    if os.path.exists(PC_BLUETOOTH_FLAG):
        os.remove(PC_BLUETOOTH_FLAG)
    if undo_system and prepared():
        _run_as_admin(DISABLE)
