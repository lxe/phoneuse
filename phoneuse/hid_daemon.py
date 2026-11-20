#!/usr/bin/python
"""Bluetooth HID mouse + keyboard for an iPhone, driven over a Unix socket.

Run as root. bluetoothd must be started with --noplugin=input so this process
can own the HID PSMs (17 control, 19 interrupt). See install.sh.

Commands on /run/phoneuse-hid.sock, one per line, reply "ok ..." or "err ...":
  move DX DY        relative pointer move (split into small steps)
  click [left|right|middle]
  down [btn] / up   hold / release a button (for drags)
  scroll N          wheel; positive scrolls up
  key NAME [MOD..]  e.g. "key h gui" (Cmd+H = home), "key enter", "key tab shift"
  type TEXT         US-layout text
  corner            slam pointer to the top-left corner
  connect           page the paired iPhone now (the daemon also reconnects by itself)
  release [IDLE]    drop the link while nobody uses it, so the iPhone shows its own keyboard
                    (iOS hides it while a keyboard is connected); only if nothing was sent
                    for IDLE seconds (default 0). The next real input reconnects first.
  pair [SECONDS]    discoverable/pairable for SECONDS (0 = always, the startup default)
  m DX DY [W [BTN]] raw mouse report (live input)
  k MODS [CODE..]   raw keyboard report: held modifiers + up to 6 HID usage codes
  status
"""
import fcntl
import os
import socket
import struct
import sys
import termios
import threading
import time
import unicodedata

from gi.repository import GLib

try:
    import dbus
    import dbus.mainloop.glib
    import dbus.service
except ImportError:  # the app (bridge-only) ships without dbus-python: BlueZ is never used there
    import types
    dbus = types.SimpleNamespace(service=types.SimpleNamespace(
        Object=object, method=lambda *a, **k: (lambda f: f)), DBusException=Exception)

HERE = os.path.dirname(os.path.abspath(__file__))
from .config import BRIDGE_ONLY, STATE_DIR, USER_DAEMON
from .config import HID_SOCKET as SOCK_PATH
DEVICE_NAME = os.environ.get("PHONEUSE_NAME", "Phone Use")  # what the phone sees in Bluetooth settings
PSM_CTRL, PSM_INTR = 0x11, 0x13
HID_UUID = "00001124-0000-1000-8000-00805f9b34fb"
INTR_UUID = "8e9a4f30-6b4c-4d2a-9b1e-70686f6e6575"  # our own: carries only the interrupt PSM (the app)
PROFILE_PATH = "/io/phoneuse/hid/profile"
AGENT_PATH = "/io/phoneuse/hid/agent"

from .hidproto import (  # noqa: E402
    BUTTONS, CHARS, CONSUMER_KEYS, MODS, NAMED, REPORT_MAP, AbsoluteNudge, absolute, passive,
)


def log(*a):
    print(time.strftime("%H:%M:%S"), *a, flush=True)


# While set, input goes through the ESP32 bridge (phoneuse/bridge.py) over Wi-Fi
# instead of this PC's Bluetooth: the iPhone pairs with the bridge.
bridge_link = [None]
bridge_phone = [False]
bridge_other = [None]  # the bridge's phone, when it isn't the phone that mirrors: no input goes to it


def no_bridge_phone():
    if bridge_other[0]:
        return RuntimeError(f"the bridge is connected to {bridge_other[0]}, not the mirroring {mirror_name[0]}: "
                            f"nothing was sent. Pair {mirror_name[0]} with Phone Use Bridge")
    return RuntimeError("no iPhone connected to the bridge (pair it with Phone Use Bridge)")


def through_bridge():
    """Whether input goes through the bridge: while it reaches the iPhone, or always when this
    PC's Bluetooth is off (bridge only). Otherwise this PC's own Bluetooth carries it, so with
    both paired the phone keeps working when the bridge is away or off."""
    link = bridge_link[0]
    return bool(link) and (link.phone or BRIDGE_ONLY)


class HID:
    def __init__(self):
        self.ctrl = None
        self.intr = None
        self.peer = None
        self.buttons = 0
        self.consumer_bits = 0
        self.nudge = AbsoluteNudge()
        self.lock = threading.Lock()

    @property
    def connected(self):
        if through_bridge():
            return bridge_link[0].phone
        return self.intr is not None

    def _bridge(self, report_id, payload, skippable=False):
        # The bridge path needs the same guard as the local one (hidproto.AbsoluteNudge):
        # an agent's tap at the spot of the last absolute report, after the owner's mouse
        # had moved the pointer, was ignored and the tap landed nowhere (2026-11-10).
        if bridge_other[0]:  # the bridge holds a phone that isn't mirroring: nothing goes to it
            raise no_bridge_phone()
        # Drag motion still unanswered goes first (resent if lost): a lift or a press that
        # overtook it on Wi-Fi would land before the drag's end.
        bridge_link[0].drain()
        for extra in self.nudge.before(report_id, payload):
            bridge_link[0].report(3, extra, False)
        r = bridge_link[0].report(report_id, payload, skippable)
        bridge_phone[0] = r != "no phone"
        if r == "no phone":
            raise no_bridge_phone()
        return r == "sent"

    def send_skippable(self, report_id, payload):
        """Send a hover or motion report unless earlier ones still wait to go out
        (then the sender keeps the motion for its next report). True if sent."""
        if through_bridge():
            link = bridge_link[0]
            if not link.phone:
                raise no_bridge_phone()
            if report_id == 3:  # a hover: newest wins on the bridge, no answer
                for extra in self.nudge.before(report_id, payload):  # see _bridge
                    link.report(3, extra, False)
                link.send_latest(report_id, payload)
                return True
            if link.send_async(report_id, payload):  # motion: pipelined, counted
                self.nudge.before(report_id, payload)
                return True
            busy.note(0, link.waiting)
            return False
        if self.backlog() or inflight.n >= 2:
            busy.note(self.backlog(), inflight.n)
            return False
        self.send(report_id, payload)
        return True

    def send(self, report_id, payload):
        if through_bridge():
            self._bridge(report_id, payload)
            return
        with self.lock:
            if not self.intr:
                reconnect_now.set()
                raise RuntimeError("no iPhone connected, reconnecting")
            try:
                for extra in self.nudge.before(report_id, payload):  # see hidproto.AbsoluteNudge
                    self.intr.send(bytes([0xA1, report_id]) + bytes(extra))
                    time.sleep(0.008)
                self.intr.send(bytes([0xA1, report_id]) + bytes(payload))
            except OSError as e:
                self.drop(f"send failed: {e}")
                raise RuntimeError("connection lost")

    def backlog(self):
        """Bytes of earlier reports still waiting in the kernel to reach the phone
        (TIOCOUTQ on a Bluetooth socket is the free send space)."""
        try:
            free = struct.unpack("i", fcntl.ioctl(self.intr.fileno(), termios.TIOCOUTQ, b"\0" * 4))[0]
            return max(0, self.intr.getsockopt(socket.SOL_SOCKET, socket.SO_SNDBUF) - free)
        except (OSError, AttributeError):
            return 0

    def drop(self, why):
        log("disconnect:", why)
        # Detach first: closing a handed-over channel wakes its reader (HandedOver), and the
        # reader must then fail its "still our channel?" check, not drop a replacement too.
        socks, self.ctrl, self.intr = (self.intr, self.ctrl), None, None
        for s in socks:
            try:
                s and s.close()
            except OSError:
                pass
        reconnect_now.set()

    # mouse
    def mouse(self, dx=0, dy=0, wheel=0):
        b = lambda v: v & 0xFF
        payload = [self.buttons, b(dx), b(dy), b(wheel)]
        link = bridge_link[0]
        if link and link.phone and through_bridge():
            # Through the bridge, scripted swipes go pipelined: waiting for each
            # report's answer (14-35 ms over Wi-Fi) would stretch a 0.3 s flick
            # into a slow drag. Every report still goes out, in order: when 5 are
            # unanswered, wait for room instead of dropping one.
            end = time.time() + 0.3
            self.nudge.before(2, payload)  # relative motion: the next absolute report must differ (_bridge)
            while not link.send_async(2, payload):
                if time.time() > end:
                    self._bridge(2, payload)
                    return
                time.sleep(0.002)
            return
        self.send(2, payload)

    def move(self, dx, dy, step=8, delay=0.008):
        # Small, evenly paced steps keep iOS pointer acceleration predictable.
        while dx or dy:
            sx = max(-step, min(step, dx))
            sy = max(-step, min(step, dy))
            self.mouse(sx, sy)
            dx -= sx
            dy -= sy
            time.sleep(delay)

    def corner(self):
        for _ in range(60):
            self.mouse(-127, -127)
            time.sleep(0.008)

    def press(self, btn):
        self.buttons |= BUTTONS[btn]
        self.mouse()

    def release(self):
        self.buttons = 0
        self.mouse()

    def click(self, btn="left"):
        self.press(btn)
        time.sleep(0.05)
        self.release()

    def scroll(self, n):
        sign = 1 if n > 0 else -1
        for _ in range(abs(n)):
            self.mouse(wheel=sign)
            time.sleep(0.02)

    # keyboard
    def consumer(self, name, hold=0.05):
        """Press a consumer key (CONSUMER_KEYS) for `hold` seconds, then release it."""
        self.consumer_set(name, True)
        time.sleep(hold)
        self.consumer_set(name, False)

    def consumer_set(self, name, down):
        """Hold or let go of one consumer key (None lets go of all of them)."""
        bit = 1 << CONSUMER_KEYS[name] if name else 0xFF
        self.consumer_bits = self.consumer_bits | bit if down else self.consumer_bits & ~bit
        self.send(4, [self.consumer_bits])

    def eject(self):
        """Press and release Eject: toggles the iPhone's on-screen keyboard."""
        self.consumer("eject")

    def key(self, code, mods=0):
        self.send(1, [mods, 0, code, 0, 0, 0, 0, 0])
        time.sleep(0.012)
        self.send(1, [0] * 8)
        time.sleep(0.012)

    def type(self, text):
        for ch in text:
            if ch not in CHARS:
                raise ValueError(f"cannot type {ch!r}")
            self.key(*CHARS[ch])


hid = HID()


# ---------- link tuning ----------
# The iPhone is the master of the link and may poll us only every ~50 ms, which
# caps the pointer at ~20 updates/s (measured 2026-11-06: acks 51 ms apart).
# A QoS Setup asking for 8 ms latency brings that to ~2 ms, as real mice do.
HCIGETCONNINFO = 0x800448D5


def acl_handle(addr):
    import fcntl
    s = socket.socket(socket.AF_BLUETOOTH, socket.SOCK_RAW, socket.BTPROTO_HCI)
    try:
        s.bind((0,))
        ba = bytes(int(x, 16) for x in reversed(addr.split(":")))
        buf = bytearray(ba + bytes([1]) + bytes(24))  # hci_conn_info_req, type ACL
        fcntl.ioctl(s.fileno(), HCIGETCONNINFO, buf, True)
        return struct.unpack_from("<H", buf, 8)[0]
    finally:
        s.close()


def tune_link(addr):
    if USER_DAEMON:  # raw HCI needs root; the app keeps BlueZ's link settings
        return None
    try:
        h = acl_handle(addr)
        s = socket.socket(socket.AF_BLUETOOTH, socket.SOCK_RAW, socket.BTPROTO_HCI)
        s.bind((0,))

        def cmd(ogf, ocf, p):
            s.send(bytes([1]) + struct.pack("<HB", (ogf << 10) | ocf, len(p)) + p)
        cmd(0x02, 0x000D, struct.pack("<HH", h, 0x0001))  # link policy: role switch only, no sniff
        time.sleep(0.02)
        # QoS Setup: guaranteed, 800 B/s, latency 8000 us
        cmd(0x02, 0x0007, struct.pack("<HBBIIII", h, 0, 0x02, 800, 0, 8000, 0xFFFFFFFF))
        s.close()
        return h
    except OSError as e:
        log("tune_link failed:", e)
        return None


def tune_loop():
    while True:
        time.sleep(20)
        if hid.connected and hid.peer:
            tune_link(hid.peer)


# ---------- L2CAP ----------
def handle_ctrl(sock):
    # Answer HID control-channel requests (SET_PROTOCOL, SET_IDLE, ...) with HANDSHAKE OK.
    while True:
        try:
            data = sock.recv(64)
        except OSError:
            break
        if not data:
            break
        t = data[0] >> 4
        if t == 0x0:  # HANDSHAKE from host: ignore
            continue
        if t == 0x1 and data[0] & 0x0F in (0x3, 0x4, 0x5):  # HID_CONTROL suspend/exit/unplug
            log("control op", hex(data[0]))
            continue
        try:
            if t == 0x4:  # GET_REPORT -> empty report of the asked id
                rid = data[1] if len(data) > 1 else 1
                sock.send(bytes([0xA1, rid]) + bytes(8 if rid == 1 else 4))
            elif t == 0x6:  # GET_PROTOCOL -> report protocol
                sock.send(b"\xa0\x01")
            else:
                sock.send(b"\x00")
        except OSError:
            break
    if hid.ctrl is sock:
        hid.drop("control channel closed")


def listen(psm):
    s = socket.socket(socket.AF_BLUETOOTH, socket.SOCK_SEQPACKET, socket.BTPROTO_L2CAP)
    s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    s.bind((socket.BDADDR_ANY, psm))
    s.listen(1)
    return s


def accept_loop():
    sc, si = listen(PSM_CTRL), listen(PSM_INTR)
    log("listening on PSM 17/19")
    while True:
        c, addr = sc.accept()
        if released.is_set():
            log("declined HID connection from", addr[0], "(released until phoneuse is used)")
            c.close()
            continue
        if not wanted(addr[0]):
            log("declined HID connection from", addr[0], f"(not the mirroring phone, {mirror_name[0]!r})")
            c.close()
            continue
        log("control from", addr[0])
        i, _ = si.accept()
        log("interrupt from", addr[0])
        if hid.connected:
            hid.drop("replaced by new connection")
        hid.ctrl, hid.intr, hid.peer = c, i, addr[0]
        threading.Thread(target=handle_ctrl, args=(c,), daemon=True).start()
        log("link tuned, handle", tune_link(addr[0]))


connect_lock = threading.Lock()
reconnect_now = threading.Event()
class InFlight:
    """Reports handed to the Bluetooth controller that it hasn't sent yet. The
    controller buffers a few ACL packets beyond the kernel's socket queue (which
    backlog() sees); on a slow link (35 reports/s at times, when the phone's
    radio is busy with the AirPlay video) four of them are ~0.15 s of delay.
    Counted from the kernel's HCI monitor channel: ACL packets sent minus the
    controller's Number of Completed Packets events."""
    AF_BLUETOOTH, BTPROTO_HCI, HCI_CHANNEL_MONITOR = 31, 1, 2

    def __init__(self):
        self.n = 0
        self.lock = threading.Lock()
        if USER_DAEMON:  # needs a raw HCI socket (root); the app does without the in-flight count
            return
        try:
            import ctypes
            self.sock = socket.socket(self.AF_BLUETOOTH, socket.SOCK_RAW, self.BTPROTO_HCI)
            addr = struct.pack("<HHH", self.AF_BLUETOOTH, 0xFFFF, self.HCI_CHANNEL_MONITOR)
            libc = ctypes.CDLL(None, use_errno=True)
            if libc.bind(self.sock.fileno(), addr, len(addr)) != 0:
                raise OSError(ctypes.get_errno(), "bind HCI monitor")
            threading.Thread(target=self._read, daemon=True).start()
        except OSError as e:
            log("in-flight count off:", e)

    def _read(self):
        while True:
            try:
                b = self.sock.recv(4096)
            except OSError:
                return
            if len(b) < 7:
                continue
            op = struct.unpack_from("<H", b)[0]
            if op == 4:  # ACL data sent to the controller
                with self.lock:
                    self.n += 1
            elif op == 3 and b[6] == 0x13 and len(b) >= 9:  # Number of Completed Packets
                done = sum(struct.unpack_from("<H", b, 9 + 4 * i + 2)[0] for i in range(b[8])
                           if 9 + 4 * i + 4 <= len(b))
                with self.lock:
                    self.n = max(0, self.n - done)


inflight = InFlight()


class Busy:
    """Counts reports skipped because the link was backed up, and logs once a
    second while it happens (how often the radio falls behind, and how far)."""

    def __init__(self):
        self.lock = threading.Lock()
        self.skips, self.worst, self.since, self.most = 0, 0, 0.0, 0

    def note(self, queued, waiting=0):
        with self.lock:
            now = time.time()
            if not self.skips:
                self.since = now
            self.skips += 1
            self.worst = max(self.worst, queued)
            self.most = max(self.most, waiting)
            if now - self.since >= 1.0:
                log(f"link busy: skipped {self.skips} reports in {now - self.since:.1f} s, "
                    f"up to {self.worst} bytes queued, {self.most} in the controller")
                self.skips, self.worst, self.most = 0, 0, 0


busy = Busy()
released = threading.Event()  # link dropped on purpose: no reconnecting until real input
last_use = [0.0]  # when a command last sent real input (not a hover)
input_at = [0.0]  # when any input command (hovers too) last came in, from anyone
finger_down = [False]  # an absolute report with a button held, not yet lifted


def connect_out():
    """Page each paired iPhone once and open the HID channels."""
    if through_bridge():
        bridge_link[0].connect_phone()  # the bridge pages the phone it is paired with
        return "input goes through the bridge; asked it to page the phone"
    if BRIDGE_ONLY:
        raise RuntimeError("no bridge is set up: add one in Phone Use")
    with connect_lock:
        released.clear()
        if hid.connected:
            return "already connected"
        errors = []
        for addr in [a for a in paired_devices() if wanted(a)]:
            socks = []
            try:
                for psm in (PSM_CTRL, PSM_INTR):
                    s = socket.socket(socket.AF_BLUETOOTH, socket.SOCK_SEQPACKET, socket.BTPROTO_L2CAP)
                    socks.append(s)
                    s.settimeout(8)
                    s.connect((addr, psm))
                    s.settimeout(None)
            except OSError as e:
                for s in socks:
                    s.close()
                errors.append(f"{addr}: {e}")
                continue
            if hid.connected:  # the iPhone connected in by itself meanwhile
                for s in socks:
                    s.close()
                return "already connected"
            hid.ctrl, hid.intr = socks
            hid.peer = addr
            log("connected out to", addr, "" if USER_DAEMON else f"- link tuned, handle {tune_link(addr)}")
            threading.Thread(target=handle_ctrl, args=(hid.ctrl,), daemon=True).start()
            return f"connected to {addr}"
        raise RuntimeError("; ".join(errors) or "no paired iPhone")


def reconnect_loop():
    # While disconnected, page the iPhone with a growing pause between tries
    # (5 s up to 30 s). Paging without pauses keeps the radio busy and makes it
    # miss the iPhone's own connection attempts. A disconnect, a command sent
    # while disconnected, or the iPhone coming into range (BlueZ sees it
    # connect) cuts the pause short.
    pause = 0
    while True:
        reconnect_now.wait(30 if hid.connected else pause)
        woke = reconnect_now.is_set()
        reconnect_now.clear()
        if hid.connected or released.is_set():
            pause = 0
            continue
        if woke:
            time.sleep(3)  # give the iPhone a moment to reconnect by itself
            if hid.connected or released.is_set():
                continue
        try:
            log("reconnect:", connect_out())
            pause = 0
        except Exception as e:
            pause = min(30, max(5, pause * 2))
            log(f"reconnect failed ({e}); next try in {pause}s")


def on_device_changed(iface, changed, invalidated, path=None):
    if iface == "org.bluez.Device1" and changed.get("Connected") and not hid.connected and not released.is_set():
        reconnect_now.set()


# ---------- D-Bus: profile, agent, adapter ----------
bus = None


def adapter():
    return dbus.Interface(bus.get_object("org.bluez", "/org/bluez/hci0"), "org.freedesktop.DBus.Properties")


def device_name(addr):
    try:
        props = dbus.Interface(bus.get_object("org.bluez", "/org/bluez/hci0/dev_" + addr.upper().replace(":", "_")),
                               "org.freedesktop.DBus.Properties")
        return str(props.Get("org.bluez.Device1", "Alias"))
    except dbus.DBusException:
        return None


def same_name(a, b):
    n = lambda x: unicodedata.normalize("NFKC", x or "").replace("\u2019", "'").casefold().strip()
    return bool(a and b) and n(a) == n(b)


# The phone that is screen mirroring, by device name: `phoneuse up` reads it from UxPlay
# and sends "mirror NAME". AirPlay and Bluetooth both carry the name set in Settings ->
# General -> About -> Name, so HID goes to the phone whose screen we see.
mirror_name = [None]


def wanted(addr):
    """May this device use the HID channels? Only the mirroring phone, when it is paired;
    any paired device while no phone mirrors or the mirroring one isn't paired yet."""
    if not mirror_name[0] or not any(same_name(device_name(a), mirror_name[0]) for a in paired_devices()):
        return True
    return same_name(device_name(addr), mirror_name[0])


def set_mirror(name):
    mirror_name[0] = name.strip() or None
    save_state()
    log("mirroring phone:", mirror_name[0])
    if hid.connected and hid.peer and not wanted(hid.peer):  # no peer: input goes through the bridge
        peer = hid.peer
        hid.drop(f"{peer} is not the mirroring phone")  # also wakes the reconnect loop
        # The Bluetooth link itself too: while it stays up, an iPhone refuses new HID
        # connections on it ("Connection refused") until something disconnects it.
        disconnect(peer)
    else:
        reconnect_now.set()
    return f"mirror={mirror_name[0]!r} connected={hid.connected} peer={hid.peer}"


def disconnect(addr):
    """Close this PC's Bluetooth connection to one device (BlueZ Device1), not only its HID channels."""
    try:
        dbus.Interface(bus.get_object("org.bluez", "/org/bluez/hci0/dev_" + addr.upper().replace(":", "_")),
                       "org.bluez.Device1").Disconnect()
        log("disconnected", addr)
    except dbus.DBusException as e:
        log("couldn't disconnect", addr, e)


def disconnect_all():
    """Close this PC's Bluetooth connections to the paired phones (BlueZ Device1)."""
    if BRIDGE_ONLY:
        return
    om = dbus.Interface(bus.get_object("org.bluez", "/"), "org.freedesktop.DBus.ObjectManager")
    for path, ifaces in om.GetManagedObjects().items():
        d = ifaces.get("org.bluez.Device1")
        if d and d.get("Paired") and d.get("Connected"):
            try:
                dbus.Interface(bus.get_object("org.bluez", path), "org.bluez.Device1").Disconnect()
                log("disconnected", str(d["Address"]), "(input goes through the bridge)")
            except dbus.DBusException as e:
                log("couldn't disconnect", str(d["Address"]), e)


def paired_devices():
    if BRIDGE_ONLY:
        return []
    om = dbus.Interface(bus.get_object("org.bluez", "/"), "org.freedesktop.DBus.ObjectManager")
    out = []
    for path, ifaces in om.GetManagedObjects().items():
        d = ifaces.get("org.bluez.Device1")
        if d and d.get("Paired"):
            out.append(str(d["Address"]))
    return out


pending_ctrl = {}  # device address -> its control channel, until the interrupt channel follows


def incoming(addr, kind, sock):
    """A connection BlueZ accepted for us (the app: bluetoothd owns PSMs 17/19 and hands each
    one over). The same rules as accept_loop: control first, then interrupt."""
    if kind == "ctrl":
        if released.is_set() or not wanted(addr):
            log("declined HID connection from", addr, "(released, or not the mirroring phone)")
            sock.close()
            return
        log("control from", addr)
        old = pending_ctrl.pop(addr, None)
        if old:
            old.close()
        pending_ctrl[addr] = sock
        return
    c = pending_ctrl.pop(addr, None)
    if not c:
        sock.close()
        return
    log("interrupt from", addr)
    if hid.connected:
        hid.drop("replaced by new connection")
    hid.ctrl, hid.intr, hid.peer = c, sock, addr
    threading.Thread(target=handle_ctrl, args=(c,), daemon=True).start()


class HandedOver(socket.socket):
    """A connection bluetoothd accepted and handed over (NewConnection). bluetoothd makes it
    non-blocking (btio's server_cb), so the control reader's first recv() would fail at once
    and drop the link; and it keeps its own copy of the socket, so close() alone would leave
    the channel open and the iPhone would still count the keyboard as connected."""

    def __init__(self, *a, **k):
        super().__init__(*a, **k)
        self.setblocking(True)

    def close(self):
        try:
            self.shutdown(socket.SHUT_RDWR)
        except OSError:
            pass
        super().close()


class Profile(dbus.service.Object):
    def __init__(self, bus, path, kind=None):
        super().__init__(bus, path)
        self.kind = kind  # None: SDP only (the root daemon owns the PSMs); "ctrl" / "intr": BlueZ owns them

    @dbus.service.method("org.bluez.Profile1", in_signature="", out_signature="")
    def Release(self):
        pass

    @dbus.service.method("org.bluez.Profile1", in_signature="oha{sv}", out_signature="")
    def NewConnection(self, path, fd, props):
        fd = fd.take()
        if not self.kind:
            os.close(fd)  # the root daemon owns the PSMs directly
            return
        addr = str(path).rsplit("dev_", 1)[-1].replace("_", ":")
        incoming(addr, self.kind, HandedOver(fileno=fd))

    @dbus.service.method("org.bluez.Profile1", in_signature="o", out_signature="")
    def RequestDisconnection(self, path):
        pass


class Agent(dbus.service.Object):
    # NoInputNoOutput: accept every pairing ("Just Works"). Pairing is only possible while
    # the adapter is discoverable, which the "pair" command opens for a limited time.
    @dbus.service.method("org.bluez.Agent1", in_signature="", out_signature="")
    def Release(self):
        pass

    @dbus.service.method("org.bluez.Agent1", in_signature="os", out_signature="")
    def AuthorizeService(self, device, uuid):
        pass

    @dbus.service.method("org.bluez.Agent1", in_signature="ou", out_signature="")
    def RequestConfirmation(self, device, passkey):
        log("pairing confirm", device, passkey)

    @dbus.service.method("org.bluez.Agent1", in_signature="o", out_signature="")
    def RequestAuthorization(self, device):
        pass

    @dbus.service.method("org.bluez.Agent1", in_signature="o", out_signature="s")
    def RequestPinCode(self, device):
        return "0000"

    @dbus.service.method("org.bluez.Agent1", in_signature="o", out_signature="u")
    def RequestPasskey(self, device):
        return dbus.UInt32(0)

    @dbus.service.method("org.bluez.Agent1", in_signature="ouq", out_signature="")
    def DisplayPasskey(self, device, passkey, entered):
        log("passkey", passkey)

    @dbus.service.method("org.bluez.Agent1", in_signature="os", out_signature="")
    def DisplayPinCode(self, device, pin):
        log("pin", pin)

    @dbus.service.method("org.bluez.Agent1", in_signature="", out_signature="")
    def Cancel(self):
        pass




def open_pairing(seconds):
    a = adapter()
    a.Set("org.bluez.Adapter1", "PairableTimeout", dbus.UInt32(seconds))
    a.Set("org.bluez.Adapter1", "DiscoverableTimeout", dbus.UInt32(seconds))
    a.Set("org.bluez.Adapter1", "Pairable", True)
    a.Set("org.bluez.Adapter1", "Discoverable", True)


def setup_dbus():
    global bus
    dbus.mainloop.glib.DBusGMainLoop(set_as_default=True)
    bus = dbus.SystemBus()
    record = (open(os.path.join(HERE, "sdp_record.xml")).read()
              .replace("@REPORT_MAP@", REPORT_MAP.hex()).replace("@NAME@", DEVICE_NAME))
    pm = dbus.Interface(bus.get_object("org.bluez", "/org/bluez"), "org.bluez.ProfileManager1")
    opts = {"ServiceRecord": record, "Role": "server", "RequireAuthentication": False, "RequireAuthorization": False}
    if USER_DAEMON:
        # No root: bluetoothd listens on the HID PSMs and passes each connection over
        # (NewConnection). The interrupt channel needs a profile of its own.
        Profile(bus, PROFILE_PATH, "ctrl")
        pm.RegisterProfile(PROFILE_PATH, HID_UUID, dict(opts, PSM=dbus.UInt16(PSM_CTRL)))
        Profile(bus, PROFILE_PATH + "_intr", "intr")
        pm.RegisterProfile(PROFILE_PATH + "_intr", INTR_UUID, {
            "Role": "server", "PSM": dbus.UInt16(PSM_INTR),
            "RequireAuthentication": False, "RequireAuthorization": False})
    else:
        Profile(bus, PROFILE_PATH)
        pm.RegisterProfile(PROFILE_PATH, HID_UUID, opts)
    Agent(bus, AGENT_PATH)
    am = dbus.Interface(bus.get_object("org.bluez", "/org/bluez"), "org.bluez.AgentManager1")
    am.RegisterAgent(AGENT_PATH, "NoInputNoOutput")
    am.RequestDefaultAgent(AGENT_PATH)
    a = adapter()
    a.Set("org.bluez.Adapter1", "Powered", True)
    a.Set("org.bluez.Adapter1", "Alias", DEVICE_NAME)
    bus.add_signal_receiver(on_device_changed, "PropertiesChanged",
                            "org.freedesktop.DBus.Properties", "org.bluez", path_keyword="path")
    log("profile + agent registered")
    # Always discoverable and pairable, so any iPhone can (re)pair any time. Any
    # paired device may connect; a new connection replaces the current one.
    open_pairing(0)


# ---------- command socket ----------
def release(idle):
    with connect_lock:
        if time.time() - last_use[0] < idle:
            return "in use"
        released.set()
        if hid.connected:
            hid.drop("released: nobody is using phoneuse, so the iPhone shows its own keyboard")
        return "released"


# survives restarts: bridge and mirroring phone
STATE = os.path.join(STATE_DIR, "daemon.json") if USER_DAEMON else "/var/lib/phoneuse/daemon.json"


def save_state():
    import json
    link = bridge_link[0]
    d = {"mirror": mirror_name[0], "bridge": [link.addr[0], link.key.hex()] if link else None}
    try:
        os.makedirs(os.path.dirname(STATE), exist_ok=True)
        fd = os.open(STATE + ".tmp", os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
        with os.fdopen(fd, "w") as f:
            json.dump(d, f)
        os.replace(STATE + ".tmp", STATE)
    except OSError as e:
        log("couldn't save state:", e)


def restore_state():
    import json
    try:
        with open(STATE) as f:
            d = json.load(f)
    except (OSError, ValueError):
        return
    if d.get("mirror"):
        mirror_name[0] = d["mirror"]
    if d.get("bridge"):
        for _ in range(10):  # the network may still be coming up
            if bridge_link[0]:  # the supervisor set the bridge first: keep its link
                return
            try:
                log("restoring bridge:", set_bridge(d["bridge"]))
                return
            except Exception as e:
                log("bridge not answering yet:", e)
                time.sleep(3)


def set_bridge(args):
    """Switch input to the bridge (its address and control key) or back to this PC."""
    try:
        return _set_bridge(args)
    finally:
        save_state()


def _set_bridge(args):
    if args[:1] == ["off"] or not args:
        if bridge_link[0]:
            bridge_link[0].close()
        bridge_link[0] = None
        released.clear()
        reconnect_now.set()
        log("bridge off: input goes through this PC's Bluetooth again")
        return "bridge off"
    from .bridge import Link
    link = Link({"ip": args[0], "key": args[1]})
    try:
        link.hello()  # raises if it doesn't answer or the key is wrong
        st = link.status()
    except Exception:
        link.close()  # or every retry leaves a socket and its reader thread behind
        raise
    if bridge_link[0]:
        bridge_link[0].close()
    bridge_link[0] = link
    bridge_phone[0] = link.phone = bool(st.get("connected"))
    released.set()  # this PC's own link to the phone would be a second keyboard: drop it
    if hid.intr:
        hid.drop("input goes through the bridge now")
    disconnect_all()  # the Bluetooth link itself too, or the phone still lists Phone Use as connected
    log(f"bridge on: {args[0]}, phone {st.get('phone') or 'not paired'}")
    threading.Thread(target=watch_bridge, args=(link,), daemon=True).start()
    return f"bridge {args[0]} phone={st.get('phone')!r} connected={st.get('connected')}"


def mirroring_now():
    """True or False: a phone is screen mirroring to this PC's UxPlay right now. None:
    can't tell (screen.py needs numpy and Pillow; Windows may not answer)."""
    try:
        from .screen import airplay_mirroring
    except ImportError:
        return None
    try:
        return airplay_mirroring()
    except (OSError, ValueError):
        return None


def watch_bridge(link):
    """Keep the bridge's phone state current, and tell the bridge whether a phone is
    screen mirroring: it connects to the phone only while one is (firmware 0.4+;
    older firmware is asked to page the phone whenever it isn't connected)."""
    failing = None  # the last error, logged once until the bridge answers again
    mirroring = None  # the last answer mirroring_now() gave
    while True:
        time.sleep(2)
        if bridge_link[0] is not link:  # replaced or turned off while this slept: its socket is closed
            return
        try:
            was = link.phone
            st = link.status()
            from .phones import other_phone
            # Input goes to the bridge's phone only when it is the one that mirrors (the
            # bridge may still hold another phone it was paired with)
            other = other_phone(st.get("phone"), mirror_name[0]) if st.get("connected") else None
            if other != bridge_other[0]:
                log(f"bridge: connected to {other}, not the mirroring {mirror_name[0]}: input off" if other
                    else "bridge: its phone is the mirroring one")
            bridge_other[0] = other
            bridge_phone[0] = link.phone = bool(st.get("connected")) and not other
            if link.phone and not was and hid.intr:  # back with the phone: one keyboard is enough
                released.set()
                hid.drop("the bridge reaches the iPhone again")
            if failing:
                log("bridge: answering again")
                failing = None
            now = mirroring_now()
            if now is not None and now != mirroring:
                log("bridge:", "a phone mirrors: connecting" if now else "no phone mirrors: the bridge stands by")
            mirroring = mirroring if now is None else now
            if link.can_stand_by():
                if mirroring is not None:
                    bridge_phone[0] = link.phone = link.want(mirroring, mirror_name[0]) and not bridge_other[0]
            elif not link.phone and mirror_name[0]:
                link.connect_phone()
        except Exception as e:
            if bridge_link[0] is not link:
                return
            bridge_phone[0] = link.phone = False  # not answering: this PC's Bluetooth takes over, if on
            if failing is None:
                log("bridge: no answer:", e)
            failing = str(e)


def nudge(args):
    """Keep-awake (web.py) moves the pointer a little about once a second so Auto-Lock
    never fires, during an agent's session too: an agent that reads or thinks for a
    minute between actions sends nothing, and a phone with a 30 s Auto-Lock locks.
    The nudge goes only while no finger is down and no input came in for QUIET seconds,
    so it never lifts a drag or moves the pointer between a hover and its press."""
    quiet = float(args[2]) if len(args) > 2 else 2.0
    if finger_down[0] or hid.buttons or time.time() - input_at[0] < quiet:
        return "ok busy"
    if released.is_set() and not hid.connected:
        return "ok released"
    return "ok" if hid.send_skippable(3, absolute(args[0], args[1], 0)) else "ok dropped"


def run_command(line):
    parts = line.strip().split(" ", 1)
    cmd, rest = parts[0].lower(), (parts[1] if len(parts) > 1 else "")
    args = rest.split()
    if cmd == "status":
        link = bridge_link[0]
        # motion reports sent to the bridge since it was set, how many went again, how many were lost
        via = (f" bridge={link.addr[0]} bridge_moves={link.counts['async']} bridge_resent={link.counts['resent']}"
               f" bridge_lost={link.counts['lost']}") if link else ""
        return (f"ok connected={hid.connected} released={released.is_set()} peer={hid.peer} "
                f"paired={paired_devices()} mirror={mirror_name[0]!r}{via}")
    if cmd == "bridge":  # bridge IP KEYHEX | bridge off: send input through the ESP32 bridge
        return "ok " + set_bridge(args)
    if cmd == "connect":
        return "ok " + connect_out()
    if cmd == "mirror":  # mirror NAME: the phone that is screen mirroring
        return "ok " + set_mirror(rest)
    if cmd == "release":
        return "ok " + release(float(args[0]) if args else 0.0)
    if cmd == "nudge":  # nudge FX FY QUIET: keep-awake's hover, only while nobody touches the phone
        return nudge(args)
    if cmd in INPUT_COMMANDS:
        input_at[0] = time.time()
    if cmd == "pair":
        pass
    elif passive(cmd, args):
        if released.is_set() and not hid.connected:
            return "ok released"
    else:
        last_use[0] = time.time()
        if released.is_set() and not hid.connected and not through_bridge():
            try:
                log("reconnect on use:", connect_out())
            except Exception:
                released.set()  # still released: no paging until the next try
                raise
            if cmd == "a":  # put the pointer there before the press (a press that moves ends the touch)
                hid.send(3, absolute(args[0], args[1], 0))
                time.sleep(0.05)
    if cmd == "eject":
        hid.eject()
        return "ok"
    if cmd == "consumer":  # consumer NAME [HOLD_SECONDS | down | up]: siri, home, search, eject
        if args == ["up"]:  # let go of every consumer key
            hid.consumer_set(None, False)
            return "ok"
        if not args or args[0] not in CONSUMER_KEYS:
            return "err consumer keys: " + ", ".join(CONSUMER_KEYS)
        how = args[1] if len(args) > 1 else "0.05"
        if how in ("down", "up"):
            hid.consumer_set(args[0], how == "down")
        else:
            hid.consumer(args[0], float(how))
        return "ok"
    if cmd == "pair":
        if BRIDGE_ONLY:
            return "ok the iPhone pairs with the bridge: Settings > Bluetooth > Phone Use Bridge"
        secs = int(args[0]) if args else 120
        GLib.idle_add(open_pairing, secs)
        return f"ok discoverable for {secs}s as '{DEVICE_NAME}'"
    if cmd == "tap":  # tap FX FY HOLD: press, hold HOLD seconds, release, as one unit
        fx, fy, hold = float(args[0]), float(args[1]), float(args[2]) if len(args) > 2 else 0.06
        if through_bridge() and bridge_link[0].can_tap():
            if not bridge_link[0].phone:
                raise no_bridge_phone()
            for extra in hid.nudge.before(3, absolute(fx, fy, 1)):  # the tap is an absolute report (_bridge)
                bridge_link[0].report(3, extra, False)
            bridge_link[0].tap(fx, fy, hold)  # timed on the bridge
            time.sleep(hold + 0.03)  # answer once the release is out, as the local path does
        else:
            hid.send(3, absolute(args[0], args[1], 1))
            time.sleep(hold)
            hid.send(3, absolute(args[0], args[1], 0))
        return "ok"
    if cmd == "a":  # absolute pointer: a FX FY [BUTTONS [sure]], FX/FY are 0..1 screen fractions
        buttons = int(args[2]) if len(args) > 2 else 0
        # sure: a gesture's lift or its hover before a press. It must arrive, so it goes
        # answered (resent until the bridge confirms it), never as a skippable hover: over
        # weak Wi-Fi a lost lift left iOS's last absolute report at the press point, and the
        # next press at that point was ignored as a repeat, so the touch started wherever
        # the pointer already was (edge swipes became drags of the content).
        sure = len(args) > 3 and args[3] == "sure"
        finger_down[0] = bool(buttons)
        if not buttons and not hid.buttons and not sure:
            # A hover while earlier reports still wait to go out (in the socket, the
            # controller, or the bridge): skip it, so the pointer jumps to the newest
            # position instead of trailing a queue. The sender sends its newest
            # position again on its next tick.
            return "ok" if hid.send_skippable(3, absolute(args[0], args[1], 0)) else "ok dropped"
        hid.send(3, absolute(args[0], args[1], buttons))
        return "ok"
    if cmd in ("m", "md"):  # raw mouse report: m DX DY [WHEEL [BUTTONS]], for live input
        # md: the same, but skipped while earlier reports still wait to go out
        # (a slow radio); the sender keeps the motion and sends it in its next
        # report, so the touch jumps to the newest position instead of trailing.
        dx, dy = (max(-127, min(127, int(v))) for v in args[:2])
        wheel = max(-127, min(127, int(args[2]))) if len(args) > 2 else 0
        if len(args) > 3:
            hid.buttons = int(args[3]) & 0x07
        if cmd == "md":
            sent = hid.send_skippable(2, [hid.buttons, dx & 0xFF, dy & 0xFF, wheel & 0xFF])
            return "ok" if sent else "ok dropped"
        hid.mouse(dx, dy, wheel)
        return "ok"
    if cmd == "k":  # raw keyboard report: k MODS [CODE...], up to 6 held keys
        codes = [int(c) & 0xFF for c in args[1:7]]
        hid.send(1, [int(args[0]) & 0xFF, 0] + codes + [0] * (6 - len(codes)))
        return "ok"
    if cmd == "move":
        hid.move(int(args[0]), int(args[1]))
    elif cmd == "corner":
        hid.corner()
    elif cmd == "click":
        hid.click(args[0] if args else "left")
    elif cmd == "down":
        hid.press(args[0] if args else "left")
    elif cmd == "up":
        hid.release()
    elif cmd == "scroll":
        hid.scroll(int(args[0]))
    elif cmd == "key":
        name, mods = args[0].lower(), 0
        for m in args[1:]:
            mods |= MODS[m.lower()]
        if name in NAMED:
            code = NAMED[name]
        else:
            code, shift = CHARS[name]
            mods |= shift
        hid.key(code, mods)
    elif cmd == "type":
        hid.type(rest)
    else:
        return f"err unknown command {cmd!r}"
    return "ok"


# ---- the agent gate ----
# When Agent control waits for the owner (a delay or an Allow), an agent may send input
# only while its session is allowed. The gate is kept here, in the root daemon every tap and key
# passes through, so no agent code (however old or buggy) can go around it. The
# owner's own processes (the browser mirror and the mirror window, started by
# `phoneuse up`) always pass, and only they may move the gate: the browser mirror
# sends "gate open" / "gate closed" / "gate until <unix time>".
OWNER_MODULES = ("phoneuse.web", "phoneuse.window")
INPUT_COMMANDS = {"a", "nudge", "tap", "m", "md", "k", "consumer", "eject", "move", "corner", "click", "down", "up", "scroll",
                  "key", "type"}
gate_until = [0.0]  # agents may send input while time.time() < this; closed until the browser mirror opens it


def _peer_args(conn):
    """The connected process's command line, as a list of bytes."""
    if sys.platform == "darwin":  # no /proc: the peer's pid from LOCAL_PEERPID, its arguments from ps
        import subprocess
        pid = struct.unpack("i", conn.getsockopt(0, 2, 4))[0]  # SOL_LOCAL, LOCAL_PEERPID
        out = subprocess.run(["ps", "-ww", "-o", "command=", "-p", str(pid)], capture_output=True, timeout=2).stdout
        return out.split()
    pid = struct.unpack("3i", conn.getsockopt(socket.SOL_SOCKET, socket.SO_PEERCRED, struct.calcsize("3i")))[0]
    with open(f"/proc/{pid}/cmdline", "rb") as f:
        return f.read().split(b"\0")


def peer_is_owner(conn):
    try:
        args = _peer_args(conn)
        return any(args[i:i + 2] == [b"-m", m.encode()] for m in OWNER_MODULES for i in range(len(args)))
    except (OSError, ValueError, struct.error):
        return False


def gated(line, owner):
    """None if the command may run; else the refusal."""
    parts = line.strip().split(" ", 1)
    cmd, rest = parts[0].lower(), (parts[1] if len(parts) > 1 else "")
    if cmd == "restart":  # the app switched this PC's Bluetooth on or off: start again with it
        if not owner:
            return "err only the browser mirror may restart the input service"
        log("restarting for a new input setting")
        threading.Timer(0.3, os._exit, (0,)).start()  # `phoneuse serve` starts it again
        return "ok restarting"
    if cmd == "gate":
        if not owner:
            return "err only the browser mirror may change the agent gate"
        a = rest.split()
        if a[:1] == ["open"]:
            gate_until[0] = float("inf")
        elif a[:1] == ["closed"]:
            gate_until[0] = 0.0
        elif a[:1] == ["until"] and len(a) == 2:
            gate_until[0] = float(a[1])
        else:
            return "err gate open | closed | until UNIX_TIME"
        return "ok"
    if cmd in INPUT_COMMANDS and not owner and time.time() >= gate_until[0]:
        return ("err the owner hasn't allowed an agent session: nothing was sent to the phone. "
                "Stop and ask the user")
    return None


def serve_client(conn):
    owner = peer_is_owner(conn)
    with conn, conn.makefile("rw") as f:
        for line in f:
            if not line.strip():
                continue
            try:
                reply = gated(line, owner) or run_command(line)
            except Exception as e:
                reply = f"err {e}"
            f.write(reply + "\n")
            f.flush()


def command_server():
    try:
        os.unlink(SOCK_PATH)
    except FileNotFoundError:
        pass
    s = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    s.bind(SOCK_PATH)
    if USER_DAEMON:  # the user's own daemon: only the user's processes may connect
        os.chmod(SOCK_PATH, 0o600)
    else:
        os.chmod(SOCK_PATH, 0o660)
        os.chown(SOCK_PATH, 0, int(os.environ.get("HID_GID", "0")))
    s.listen(4)
    log("commands on", SOCK_PATH)
    while True:
        conn, _ = s.accept()
        threading.Thread(target=serve_client, args=(conn,), daemon=True).start()


def main():
    if USER_DAEMON and not BRIDGE_ONLY:
        # The app with this PC's Bluetooth on: BlueZ owns the HID PSMs (setup_dbus), so no root;
        # no link tuning either (it needs raw HCI). Outgoing connections work as any user.
        log("this PC's Bluetooth and the bridge, as the user")
        setup_dbus()
        threading.Thread(target=command_server, daemon=True).start()
        threading.Thread(target=restore_state, daemon=True).start()
        threading.Thread(target=reconnect_loop, daemon=True).start()
        reconnect_now.set()
        GLib.MainLoop().run()
        return
    if BRIDGE_ONLY:
        # Input goes through the bridge alone: no BlueZ, no HID sockets on this PC's
        # adapter, no root. Commands, the gate and the bridge link work as usual.
        log("bridge-only mode: this PC's Bluetooth stays untouched")
        threading.Thread(target=command_server, daemon=True).start()
        threading.Thread(target=restore_state, daemon=True).start()
        GLib.MainLoop().run()
        return
    setup_dbus()
    threading.Thread(target=accept_loop, daemon=True).start()
    threading.Thread(target=command_server, daemon=True).start()
    threading.Thread(target=tune_loop, daemon=True).start()
    threading.Thread(target=restore_state, daemon=True).start()
    threading.Thread(target=reconnect_loop, daemon=True).start()
    reconnect_now.set()
    GLib.MainLoop().run()


if __name__ == "__main__":
    main()
