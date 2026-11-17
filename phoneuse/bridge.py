"""The Bluetooth bridge: an ESP32 (LilyGO T-Display) that the iPhone pairs with as
its Bluetooth keyboard and mouse, driven by phoneuse over Wi-Fi.

Four parts, in the order a bridge meets them:

  1. Firmware over USB (developers): build bridge/ with ESP-IDF and flash it.
  2. Setup over BLE (everyone): find the bridge, exchange keys, compare the code on
     its screen, list the Wi-Fi networks it sees, send the chosen one's password
     encrypted. The bridge joins and phoneuse saves its id, address and key.
  3. Using a bridge another PC set up (firmware 0.3+): it answers on the network,
     so this PC sees it, asks to use it (ADOPT), and the person presses the
     bridge's buttons when its screen shows the same code. Up to four PCs
     can use one bridge, each with its own key.
  4. The control link over UDP: signed HID reports, answered one by one
     (bridge/main/link.c has the packet format).

Saved in ~/.config/phoneuse/bridge.json: {"id", "ip", "key" (hex), "ssid"}.
"""
import asyncio
import hashlib
import hmac
import json
import os
import platform
import socket
import struct
import subprocess
import sys
import threading
import weakref
import time

from .config import config_path

HERE = os.path.dirname(os.path.abspath(__file__))
FIRMWARE = os.path.join(os.path.dirname(HERE), "bridge")
SAVED = os.path.join(os.path.dirname(config_path()), "bridge.json")
PORT = 47700
IDF = os.environ.get("IDF_PATH") or os.path.expanduser("~/esp/esp-idf-v6.1")


def _uuid(n):
    return f"c0de00{n:02x}-7068-6f6e-6575-736562726467"


SVC, INFO, KEX, SCAN, JOIN = (_uuid(n) for n in (1, 2, 3, 4, 5))


# ---------- saved bridge ----------

def saved():
    try:
        with open(SAVED) as f:
            return json.load(f)
    except (OSError, ValueError):
        return None


def save(d):
    os.makedirs(os.path.dirname(SAVED), exist_ok=True)
    tmp = SAVED + ".tmp"
    with open(os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600), "w") as f:
        json.dump(d, f, indent=1)
    os.replace(tmp, SAVED)


def resolve(cfg=None):
    """The saved bridge with a current address: asks the network for its id when
    the address is missing or doesn't answer (DHCP may have moved it). Saves it."""
    cfg = cfg or saved()
    if not cfg:
        return None
    if cfg.get("ip"):
        link = None
        try:
            link = Link(cfg)
            link.hello()
            return cfg
        except Exception:
            pass
        finally:
            if link:
                link.close()
    hits = [b for b in find_lan() if b["id"] == cfg["id"]]
    if hits:
        cfg["ip"] = hits[0]["ip"]
        save(cfg)
        return cfg
    return None


def forget(reset=True):
    """Forget the saved bridge. With reset, first tell the bridge to stop trusting this PC;
    it resets fully when no other PC uses it. Returns None once it reset, "kept" when other
    PCs still use it (it keeps its Wi-Fi and iPhone), else why it couldn't be reached."""
    why = "not asked"
    cfg = saved() if reset else None
    if cfg:
        try:
            link = Link(resolve(cfg) or cfg)
            try:
                why = None if link.reset() else "kept"
            finally:
                link.close()
        except (RuntimeError, OSError) as e:
            why = str(e)
    try:
        os.remove(SAVED)
    except OSError:
        pass
    return why


# ---------- 1. firmware over USB ----------

USB_IDS = ((0x1A86, 0x55D4), (0x10C4, 0xEA60), (0x1A86, 0x7523))  # CH9102 (T-Display v1.1), CP210x, CH340


def usb_port():
    """The bridge's USB serial port, if one is plugged in (CH9102 / CP210x / CH340)."""
    import serial.tools.list_ports
    for p in serial.tools.list_ports.comports():
        if (p.vid, p.pid) in USB_IDS:
            return p.device
    return None


def usb_board_without_driver():
    """Windows: a board is plugged in but has no working serial driver, so it has no COM port
    and usb_port() can't see it. Windows may not have one for the T-Display's CH9102 (WCH's
    CH343 driver, from LilyGO's or WCH's site). False elsewhere, and when it can't tell."""
    if sys.platform != "win32":
        return False
    ids = "|".join(f"USB\\\\VID_{v:04X}&PID_{p:04X}" for v, p in USB_IDS)
    script = (f"Get-PnpDevice -PresentOnly | Where-Object {{ $_.InstanceId -match '{ids}' }} | "
              "ForEach-Object { \"$($_.Status) $($_.Class)\" }")
    try:
        out = subprocess.run(["powershell", "-NoProfile", "-NonInteractive", "-Command", script],
                             capture_output=True, text=True, timeout=10, creationflags=0x08000000).stdout
    except (OSError, subprocess.SubprocessError):
        return False
    found = [line.split() for line in out.splitlines() if line.strip()]
    return bool(found) and not any(f[:2] == ["OK", "Ports"] for f in found)


def idf(*args, port=None):
    """Run idf.py in bridge/ with ESP-IDF's environment."""
    if not os.path.exists(os.path.join(IDF, "export.sh")):
        raise SystemExit(f"ESP-IDF not found at {IDF} (set IDF_PATH)")
    cmd = f". {IDF}/export.sh >/dev/null 2>&1 && idf.py " + (f"-p {port} -b 921600 " if port else "") + " ".join(args)
    return subprocess.call(["bash", "-c", cmd], cwd=FIRMWARE)


# Flashing a bridge needs esptool and the firmware, which most people never use: the
# Windows app doesn't ship them but downloads them here the first time someone flashes a
# bridge (prepare_flashing): esptool in a venv of its own, and the released image.
FIRMWARE_RELEASE = ("0.4.3", "b2a524b2600cc210b1b22facbefe3d1c5341443e2c865a66631f393f9463a18e")  # as the Flatpak's
FIRMWARE_URL = "https://github.com/lxe/phoneuse/releases/download/bridge-firmware-{0}/phoneuse-bridge-{0}.bin"
ESPTOOL = "esptool==5.6.0"


def tools_dir():
    """Where the downloaded flashing tools live: per user and not roaming (%LOCALAPPDATA% on
    Windows), or beside the portable Windows app's settings."""
    if os.environ.get("PHONEUSE_CONFIG_DIR"):
        return os.path.join(os.environ["PHONEUSE_CONFIG_DIR"], "bridge-tools")
    if sys.platform == "win32":
        base = os.environ.get("LOCALAPPDATA") or os.path.expanduser("~")
        return os.path.join(base, "phoneuse", "bridge-tools")
    return os.path.join(os.environ.get("XDG_DATA_HOME") or os.path.expanduser("~/.local/share"), "phoneuse", "bridge-tools")


def _tools_python():
    return os.path.join(tools_dir(), "venv", "Scripts" if sys.platform == "win32" else "bin",
                        "python.exe" if sys.platform == "win32" else "python")


# The released firmware (one merged image, flashed at 0x0): the Flatpak ships it, a checkout
# can drop one at bridge/build/phoneuse-bridge.bin, and prepare_flashing downloads one.
RELEASE_FIRMWARE = [os.path.join(p, "phoneuse-bridge.bin") for p in
                    ("/app/share/phoneuse/firmware", os.path.join(FIRMWARE, "build"),
                     os.path.join(tools_dir(), "firmware"))]


def esptool_python():
    """A Python that has esptool: this one (the Flatpak, a checkout's venv), or the tools
    venv prepare_flashing made. None: it needs preparing."""
    import importlib.util
    if importlib.util.find_spec("esptool"):
        return sys.executable
    return _tools_python() if os.path.exists(_tools_python()) else None


def flashing_ready():
    return bool(release_firmware()) and bool(esptool_python())


def prepare_flashing(step=None):
    """Download what flashing needs and this copy doesn't have: the released firmware
    (checked against its SHA-256) and esptool (into a venv of its own; pip builds the few of
    its packages published only as source, all pure Python). step(text) says what it does.
    Needs the network once; raises RuntimeError with the reason."""
    import shutil
    import urllib.request
    say = step or (lambda _t: None)
    if not release_firmware():
        version, sha = FIRMWARE_RELEASE
        say(f"Downloading bridge firmware {version}…")
        d = os.path.join(tools_dir(), "firmware")
        os.makedirs(d, exist_ok=True)
        try:
            with urllib.request.urlopen(FIRMWARE_URL.format(version), timeout=60) as r:
                data = r.read()
        except OSError as e:
            raise RuntimeError(f"couldn't download the bridge firmware: {e}") from None
        if hashlib.sha256(data).hexdigest() != sha:
            raise RuntimeError("the downloaded bridge firmware isn't the expected file (SHA-256 differs)")
        with open(os.path.join(d, "phoneuse-bridge.bin.part"), "wb") as f:
            f.write(data)
        os.replace(os.path.join(d, "phoneuse-bridge.bin.part"), os.path.join(d, "phoneuse-bridge.bin"))
        with open(os.path.join(d, "VERSION"), "w") as f:
            f.write(version)
    if not esptool_python():
        say("Installing esptool (first time only)…")
        venv = os.path.join(tools_dir(), "venv")
        shutil.rmtree(venv, ignore_errors=True)  # a half-made one from an earlier try
        flags = 0x08000000 if sys.platform == "win32" else 0  # no console window
        for cmd in ([sys.executable, "-m", "venv", venv],
                    [_tools_python(), "-m", "pip", "install", "--quiet", "--disable-pip-version-check",
                     "--only-binary=:all:", "--no-binary=esptool,reedsolo,intelhex", ESPTOOL]):
            r = subprocess.run(cmd, capture_output=True, text=True, creationflags=flags)
            if r.returncode:
                shutil.rmtree(venv, ignore_errors=True)
                why = (r.stderr or r.stdout).strip().splitlines()
                raise RuntimeError(f"couldn't install esptool: {why[-1] if why else r.returncode}")
    say("Ready")


def remove_flashing_tools():
    """Delete what prepare_flashing downloaded (the firmware and esptool's venv, ~50 MB), once
    a bridge is flashed: a bridge is flashed once, so they'd sit there unused. The next flash
    downloads them again. Firmware that came with this copy (the Flatpak's, a checkout's) stays."""
    import shutil
    shutil.rmtree(tools_dir(), ignore_errors=True)


def release_firmware():
    """(path, version) of the firmware this copy of Phone Use can install, or None."""
    for path in RELEASE_FIRMWARE:
        if os.path.exists(path):
            try:
                with open(os.path.join(os.path.dirname(path), "VERSION")) as f:
                    return path, f.read().strip()
            except OSError:
                return path, ""
    return None


def install_firmware(port, progress=None, keep_settings=False):
    """Write the released firmware to the board on `port` with esptool. By default the whole
    image goes on, which also erases the bridge's settings (its Wi-Fi, its key, its iPhone
    pairing): a fresh bridge. keep_settings writes only the bootloader, the partition table
    and the app, leaving nvs alone. progress(fraction) is called as it writes. Raises
    RuntimeError with esptool's reason."""
    import re
    import tempfile
    found = release_firmware()
    if not found:
        raise RuntimeError("this copy of Phone Use has no bridge firmware")
    with open(found[0], "rb") as f:
        image = f.read()
    regions = ([(0x1000, image[0x1000:0x9000]), (0x10000, image[0x10000:])] if keep_settings  # not nvs, phy
               else [(0x0, image)])  # the merged image's gaps are 0xFF: nvs is erased too
    with tempfile.TemporaryDirectory() as d:
        args = []
        for i, (at, data) in enumerate(regions):
            part = os.path.join(d, f"part{i}.bin")
            with open(part, "wb") as f:
                f.write(data)
            args += [hex(at), part]
        py = esptool_python()
        if not py:
            raise RuntimeError("esptool isn't installed: prepare_flashing() downloads it")
        cmd = [py, "-m", "esptool", "--chip", "esp32", "-p", port, "-b", "921600",
               "--before", "default-reset", "--after", "hard-reset", "write-flash"] + args
        proc = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True, bufsize=1,
                                creationflags=0x08000000 if sys.platform == "win32" else 0)
        sizes = [len(r[1]) for r in regions]
        written, last, lines = 0, 0.0, []  # written: regions finished ("Wrote ..." lines)
        for line in iter(proc.stdout.readline, ""):
            line = line.strip()
            lines.append(line)
            if line.startswith("Wrote"):
                written += 1
            m = re.search(r"(\d+(?:\.\d+)?)\s?%", line)
            if progress and m and line.startswith("Writing") and written < len(regions):
                frac = (sum(sizes[:written]) + float(m.group(1)) / 100 * sizes[written]) / sum(sizes)
                if frac > last:
                    last = frac
                    progress(frac)
        if proc.wait():
            why = next((x for x in reversed(lines) if x and ("rror" in x or "ailed" in x)), lines[-1] if lines else "")
            raise RuntimeError(why or f"esptool exited with {proc.returncode}")
    if progress:
        progress(1.0)


def flash(port=None):
    port = port or usb_port()
    if not port:
        raise SystemExit("No bridge on USB. Plug the T-Display into this PC (data cable, not charge-only).")
    print(f"building and flashing to {port}...", flush=True)
    rc = idf("build", "flash", port=port)
    if rc:
        raise SystemExit(f"flash failed ({rc})")
    print("flashed; the bridge restarted")


def log(port=None, seconds=None):
    """Print the bridge's serial log (Ctrl+C stops)."""
    import serial
    port = port or usb_port()
    if not port:
        raise SystemExit("No bridge on USB.")
    s = serial.Serial(port, 115200, timeout=0.2)
    s.dtr, s.rts = False, True  # restart it, so the log starts at boot
    time.sleep(0.1)
    s.rts = False
    end = time.time() + seconds if seconds else None
    try:
        while end is None or time.time() < end:
            b = s.read(4096)
            if b:
                sys.stdout.write(b.decode("utf-8", "replace"))
                sys.stdout.flush()
    except KeyboardInterrupt:
        pass


# ---------- 2. setup over BLE ----------

class Setup:
    """One setup conversation with a bridge over BLE. Methods are coroutines."""

    def __init__(self, address):
        from bleak import BleakClient
        self.client = BleakClient(address, timeout=20)
        self.code = None
        self.keys = None

    async def __aenter__(self):
        await self.client.connect()
        return self

    async def __aexit__(self, *exc):
        try:
            await self.client.disconnect()
        except Exception:
            pass

    async def info(self):
        return json.loads(bytes(await self.client.read_gatt_char(INFO)).decode() or "{}")

    async def exchange(self):
        """X25519 with the bridge: returns the 6-digit code it now shows."""
        from cryptography.hazmat.primitives import hashes, serialization
        from cryptography.hazmat.primitives.asymmetric.x25519 import X25519PrivateKey, X25519PublicKey
        from cryptography.hazmat.primitives.kdf.hkdf import HKDF
        ours = X25519PrivateKey.generate()
        pub = ours.public_key().public_bytes(serialization.Encoding.Raw, serialization.PublicFormat.Raw)
        theirs = bytes(await self.client.read_gatt_char(KEX))
        await self.client.write_gatt_char(KEX, pub, response=True)
        shared = ours.exchange(X25519PublicKey.from_public_bytes(theirs))

        def derive(info, n=32):
            return HKDF(hashes.SHA256(), n, theirs + pub, info.encode()).derive(shared)

        self.code = int.from_bytes(derive("phoneuse code", 4), "big") % 1000000
        self.keys = {"wifi": derive("phoneuse wifi"), "control": derive("phoneuse control")}
        return self.code

    async def networks(self):
        await self.client.write_gatt_char(SCAN, b"1", response=True)
        for _ in range(40):
            await asyncio.sleep(0.25)
            v = bytes(await self.client.read_gatt_char(SCAN)).decode()
            if v.startswith("["):
                return json.loads(v)
        return []

    async def join(self, ssid, password, wait=25):
        """Send the network; wait until the bridge is online (returns its info) or failed."""
        from cryptography.hazmat.primitives.ciphers.aead import AESGCM
        nonce = os.urandom(12)
        sealed = nonce + AESGCM(self.keys["wifi"]).encrypt(nonce, ssid.encode() + b"\0" + password.encode(), None)
        await self.client.write_gatt_char(JOIN, sealed, response=True)
        end = time.time() + wait
        info = {}
        while time.time() < end:
            await asyncio.sleep(0.5)
            try:
                info = await self.info()
            except Exception:
                # it stops BLE once online: the link closing means it joined; ask it on the network
                break
            if info.get("state") == "failed" or (info.get("state") == "online" and info.get("ip")):
                break
        return info


async def scan_ble(seconds=5.0):
    """Bridges advertising setup nearby: [(address, name, rssi)]. Another program
    can stop BlueZ's scan under us (BlueZ then refuses our stop): keep what came in."""
    from bleak import BleakScanner
    found = {}

    def seen(d, adv):
        if SVC in [u.lower() for u in adv.service_uuids] or (adv.local_name or "").startswith(("Phone Use Bridge", "PhoneUse Bridge")):  # also firmware < 0.2.6
            found[d.address] = (d.address, adv.local_name or d.name, adv.rssi)

    scanner = BleakScanner(seen)
    await scanner.start()
    await asyncio.sleep(seconds)
    try:
        await scanner.stop()
    except Exception:
        pass
    return sorted(found.values(), key=lambda x: -x[2])


def _local_subnet():
    """This PC's address on its default-route network, as a /24 prefix."""
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        s.connect(("192.0.2.1", 9))  # no packet is sent; picks the outgoing interface
        return s.getsockname()[0].rsplit(".", 1)[0]
    except OSError:
        return None
    finally:
        s.close()


def find_lan(timeout=1.0):
    """Bridges on the local network: [{"id","ip",...}]. Broadcast first; a host
    firewall drops answers to broadcasts (they match no outgoing packet), so then
    ask every address on this /24 directly."""
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    s.setsockopt(socket.SOL_SOCKET, socket.SO_BROADCAST, 1)
    s.settimeout(0.1)
    pkt = b"PU\x01\x10" + bytes(8)
    out = {}

    def listen(until):
        while time.time() < until:
            try:
                b, (ip, _) = s.recvfrom(512)
            except socket.timeout:
                continue
            if b[:4] == b"PU\x01\x90":
                try:
                    d = json.loads(b[12:])
                    d["ip"] = d.get("ip") or ip
                    out[d["id"]] = d
                except ValueError:
                    pass

    s.sendto(pkt, ("255.255.255.255", PORT))
    listen(time.time() + timeout / 2)
    net = _local_subnet()
    if not out and net:
        for i in range(1, 255):
            try:
                s.sendto(pkt, (f"{net}.{i}", PORT))
            except OSError:
                pass
        listen(time.time() + timeout)
    s.close()
    return list(out.values())


# ---------- 3. using a bridge another PC set up ----------

def nearby(timeout=1.0):
    """Bridges on this network that this PC doesn't use: [{"id","ip","ssid","phone",...}]."""
    mine = (saved() or {}).get("id")
    return [b for b in find_lan(timeout) if b.get("id") != mine]


def this_pc_name():
    return (platform.node() or "a computer")[:23]  # os.uname() doesn't exist on Windows


class Adoption:
    """Asks a set-up bridge to let this PC use it. start() returns the 6-digit code the
    bridge now shows with this PC's name; wait() returns once the person pressed the
    bridge's buttons (the bridge then answers HELLO signed with the new key) and
    saves the bridge, or raises when they declined or nobody answered."""

    def __init__(self, found):
        self.found = found  # a find_lan() entry
        self.code = None
        self.key = None

    def start(self):
        from cryptography.hazmat.primitives import hashes, serialization
        from cryptography.hazmat.primitives.asymmetric.x25519 import X25519PrivateKey, X25519PublicKey
        from cryptography.hazmat.primitives.kdf.hkdf import HKDF
        if _fw(self.found) < (0, 3):
            raise RuntimeError(f"this bridge has firmware {self.found.get('fw')}; using it from a second PC "
                               "needs 0.3 or later (flash it once over USB, keeping its settings)")
        ours = X25519PrivateKey.generate()
        pub = ours.public_key().public_bytes(serialization.Encoding.Raw, serialization.PublicFormat.Raw)
        pkt = b"PU\x01\x11" + bytes(8) + pub + this_pc_name().encode()
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        s.settimeout(0.4)
        theirs = None
        try:
            for _ in range(6):
                s.sendto(pkt, (self.found["ip"], PORT))
                try:
                    b = s.recv(256)
                except socket.timeout:
                    continue
                if b[:4] == b"PU\x01\x91" and len(b) >= 44:
                    theirs = b[12:44]
                    break
        finally:
            s.close()
        if not theirs:
            raise RuntimeError("the bridge didn't answer. If its screen asks about another computer, wait "
                               "a minute and try again")
        shared = ours.exchange(X25519PublicKey.from_public_bytes(theirs))

        def derive(info, n=32):
            return HKDF(hashes.SHA256(), n, theirs + pub, info.encode()).derive(shared)

        self.code = int.from_bytes(derive("phoneuse code", 4), "big") % 1000000
        self.key = derive("phoneuse control")
        return self.code

    def wait(self, seconds=90):
        """Saves and returns the bridge once it allows this PC; None while still asking
        after `seconds`; raises once it stopped asking without allowing."""
        cfg = {"id": self.found["id"], "ip": self.found["ip"], "key": self.key.hex(),
               "ssid": self.found.get("ssid"), "use": True, "set_up": time.strftime("%Y-%m-%d %H:%M"),
               "shared": True}
        end = time.time() + seconds
        while time.time() < end:
            link = Link(cfg)
            try:
                if link.hello_once():
                    save(cfg)
                    return cfg
            finally:
                link.close()
            asking = [b for b in find_lan(0.5) if b["id"] == cfg["id"]]
            if asking and not asking[0].get("asking"):
                # It stopped asking: allowed just now, declined, or timed out. One more try tells.
                link = Link(cfg)
                try:
                    if link.hello_once():
                        save(cfg)
                        return cfg
                finally:
                    link.close()
                raise RuntimeError("the bridge didn't allow this computer (declined, or no answer in time)")
        return None


def _fw(found):
    try:
        return tuple(int(x) for x in str(found.get("fw", "0")).split(".")[:2])
    except ValueError:
        return (0, 0)


# ---------- 4. the control link ----------

class Link:
    """Signed UDP session with a set-up bridge.

    Two ways to send a report:
      report(): waits for the bridge's answer and resends if it got lost; for
          keys, presses and releases, which must arrive exactly once.
      send_async(): sends without waiting, at most CREDIT unanswered at a time;
          for hovers and motion. False means "not now" (the sender keeps the
          motion for its next report), so a slow Wi-Fi moment costs one report
          instead of stalling everything behind it.
    One thread reads every answer; callers never read the socket themselves.
    """

    TIMEOUT = 0.06   # first wait for an answer; doubles on each retry
    CREDIT = 5       # unanswered async reports allowed at once (measured: 6 -> 107/s)
    RESEND = 0.12    # an async report unanswered this long goes again, with the same seq (firmware 0.4.1+)
    TRIES = 12       # sends of one async report before it counts as lost: rides out ~1.4 s of Wi-Fi gap
    EXPIRE = 0.25    # firmware before 0.4.1 can't take a resend: unanswered this long, it counts as lost

    def __init__(self, cfg=None):
        self.cfg = cfg or saved()
        if not self.cfg:
            raise RuntimeError("no bridge set up")
        if not self.cfg.get("ip"):
            raise RuntimeError("the bridge's address isn't known yet")
        self.key = bytes.fromhex(self.cfg["key"])
        self.addr = (self.cfg["ip"], PORT)
        self.sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        self.sock.settimeout(0.5)
        self.lock = threading.Lock()   # seq, session and sends stay in order
        self.session = 0
        self.seq = 0
        self.waiting = 0               # reports the bridge still has for Bluetooth
        self.phone = True              # the last answer said a phone is connected
        self.rtt = []
        self.waiters = {}              # seq or ("hello", nonce) -> [event, answer body]
        self.inflight = {}             # async seq -> [first send, last send, packet, sends]
        self.counts = {"async": 0, "resent": 0, "lost": 0}  # async reports since the link opened
        self.closed = False
        # the receiver holds the Link weakly: a Link dropped without close() (a one-off
        # status()) closes its socket and ends its thread within 0.5 s
        threading.Thread(target=Link._rx, args=(weakref.ref(self), self.sock), daemon=True).start()

    def close(self):
        self.closed = True
        self.sock.close()

    def __del__(self):
        try:
            self.sock.close()
        except Exception:
            pass

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        self.close()

    def _pack(self, type_, seq, body):
        head = b"PU\x01" + bytes([type_]) + struct.pack("<II", self.session, seq)
        return head + body + hmac.new(self.key, head + body, hashlib.sha256).digest()[:8]

    def _ok(self, b):
        return len(b) >= 20 and hmac.compare_digest(
            b[-8:], hmac.new(self.key, b[:-8], hashlib.sha256).digest()[:8])

    @staticmethod
    def _rx(ref, sock):
        while True:
            link = ref()
            if link is None or link.closed:
                sock.close()
                return
            del link
            try:
                b = sock.recv(1024)
            except socket.timeout:
                continue
            except OSError:
                return
            link = ref()
            if link is None:
                sock.close()
                return
            link._received(b)
            del link

    def _received(self, b):
        if not self._ok(b):
            return
        type_, seq, body = b[3], struct.unpack_from("<I", b, 8)[0], b[12:-8]
        if type_ == 0x81:
            w = self.waiters.get(("hello", body[:8]))
            if w:
                w[1] = struct.unpack_from("<I", b, 4)[0]
                w[0].set()
            return
        if type_ == 0x82 and len(body) >= 2:
            self.waiting, self.phone = body[1], body[0] != 2
        sent = self.inflight.pop(seq, None)
        if sent:
            self.rtt = (self.rtt + [time.time() - sent[1]])[-200:]
        w = self.waiters.get(seq)
        if w and not w[0].is_set():
            w[1] = body
            w[0].set()

    def hello(self):
        nonce = os.urandom(8)
        w = self.waiters[("hello", nonce)] = [threading.Event(), None]
        try:
            for _ in range(5):
                with self.lock:
                    self.session, self.seq = 0, 0
                    self.sock.sendto(self._pack(0x01, 0, nonce), self.addr)
                if w[0].wait(0.3):
                    with self.lock:
                        self.session = w[1]
                        self.inflight.clear()
                    return True
            raise RuntimeError(f"the bridge at {self.addr[0]} didn't answer")
        finally:
            self.waiters.pop(("hello", nonce), None)

    def hello_once(self, wait=0.4):
        """One HELLO: True if the bridge answered (it knows this PC's key)."""
        nonce = os.urandom(8)
        w = self.waiters[("hello", nonce)] = [threading.Event(), None]
        try:
            with self.lock:
                self.session, self.seq = 0, 0
                self.sock.sendto(self._pack(0x01, 0, nonce), self.addr)
            if w[0].wait(wait):
                with self.lock:
                    self.session = w[1]
                return True
            return False
        finally:
            self.waiters.pop(("hello", nonce), None)

    def _call(self, type_, body=b"", tries=4, wait=None):
        if not self.session:
            self.hello()
        for attempt in range(2):
            with self.lock:
                self.seq += 1
                seq = self.seq
                pkt = self._pack(type_, seq, body)
            w = self.waiters[seq] = [threading.Event(), None]
            try:
                t0 = time.time()
                for i in range(tries):
                    self.sock.sendto(pkt, self.addr)  # the same seq: the bridge answers again, does nothing twice
                    if w[0].wait(wait or self.TIMEOUT * (2 ** i)):
                        self.rtt = (self.rtt + [time.time() - t0])[-200:]
                        return w[1]
            finally:
                self.waiters.pop(seq, None)
            if attempt == 0:
                self.hello()  # the bridge restarted (its session is gone): once more
        raise RuntimeError("the bridge didn't answer")

    def send_async(self, report_id, payload):
        """Send a hover or motion report without waiting. False: not now."""
        if not self.session:
            self.hello()
        resend = self.can_resend()
        with self.lock:
            self._resend_due(resend)
            if len(self.inflight) >= self.CREDIT or self.waiting >= self.CREDIT:
                return False
            self.seq += 1
            pkt = self._pack(0x02, self.seq, bytes([0, report_id]) + bytes(payload))
            self.inflight[self.seq] = [time.time(), time.time(), pkt, 1]
            self.counts["async"] += 1
            self.sock.sendto(pkt, self.addr)
        if getattr(self, "lost", 0) >= 8:  # many lost in a row: maybe a new session is needed
            self.lost = 0
            threading.Thread(target=self.hello, daemon=True).start()
        return True

    def _resend_due(self, resend):
        """(lock held) Send again, with the same seq, each async report unanswered for
        RESEND: the bridge does a report it hasn't done yet once, and only answers a repeat
        (0.4.1+). One unanswered after TRIES sends, or EXPIRE on older firmware, is lost."""
        now = time.time()
        for seq, f in list(self.inflight.items()):
            if resend and f[3] < self.TRIES:
                if now - f[1] > self.RESEND:
                    f[1], f[3] = now, f[3] + 1
                    self.counts["resent"] += 1
                    self.sock.sendto(f[2], self.addr)
            elif now - f[0] > (self.RESEND * self.TRIES if resend else self.EXPIRE):
                del self.inflight[seq]
                self.counts["lost"] += 1
                self.lost = getattr(self, "lost", 0) + 1

    def drain(self, timeout=1.0):
        """Wait until every async report is answered (resending as needed), so a report
        sent next can't overtake drag motion: a lift must come after the drag. True when
        none is left unanswered."""
        resend, end = self.can_resend(), time.time() + timeout
        while True:
            with self.lock:
                self._resend_due(resend)
                if not self.inflight:
                    return True
            if time.time() > end:
                return False
            time.sleep(0.004)

    def can_resend(self):
        """Whether the bridge's firmware takes resent async reports (0.4.1 and later)."""
        if getattr(self, "_can_resend", None) is None:
            self._can_resend = False  # until the status below says otherwise
            try:
                fw = self.status().get("fw", "0")
                self._can_resend = tuple(int(p) for p in fw.split(".")[:3]) >= (0, 4, 1)
            except (RuntimeError, ValueError, OSError):
                self._can_resend = None
                return False
        return self._can_resend

    def send_latest(self, report_id, payload):
        """A hover: sent without waiting and without an answer. The bridge keeps
        only the newest and sends it once Bluetooth is free."""
        if not self.session:
            self.hello()
        with self.lock:
            self.seq += 1
            self.sock.sendto(self._pack(0x02, self.seq, bytes([3, report_id]) + bytes(payload)), self.addr)

    def report(self, report_id, payload, skippable=False):
        r = self._call(0x02, bytes([1 if skippable else 0, report_id]) + bytes(payload))
        self.waiting, self.phone = r[1], r[0] != 2
        return ("sent", "skipped", "no phone")[min(r[0], 2)]

    def can_tap(self):
        """Whether the bridge's firmware does whole taps (0.2.0 and later)."""
        if getattr(self, "_can_tap", None) is None:
            try:
                fw = self.status().get("fw", "0")
                self._can_tap = tuple(int(p) for p in fw.split(".")[:2]) >= (0, 2)
            except (RuntimeError, ValueError, OSError):
                return False
        return self._can_tap

    def tap(self, fx, fy, hold):
        """A whole tap on the bridge: press at (fx, fy) (screen fractions), hold `hold`
        seconds, release. The bridge times it, so Wi-Fi can't stretch the hold."""
        x, y = (max(0, min(32767, round(float(v) * 32767))) for v in (fx, fy))
        ms = max(0, min(3000, round(hold * 1000)))
        r = self._call(0x0C, bytes([x & 0xFF, x >> 8, y & 0xFF, y >> 8, ms & 0xFF, ms >> 8]))
        if not r or r[0] != 0:
            raise RuntimeError("the bridge couldn't queue the tap")

    def status(self):
        return json.loads(self._call(0x03).decode())

    def connect_phone(self):
        self._call(0x04)

    def can_stand_by(self):
        """Whether the bridge's firmware connects only while told a phone mirrors (0.4 and later)."""
        if getattr(self, "_can_stand_by", None) is None:
            try:
                fw = self.status().get("fw", "0")
                self._can_stand_by = tuple(int(p) for p in fw.split(".")[:2]) >= (0, 4)
            except (RuntimeError, ValueError, OSError):
                return False
        return self._can_stand_by

    def want(self, mirroring, name=None):
        """Tell the bridge whether a phone is screen mirroring to this PC, and which one (its
        AirPlay name): while one is (said again within 30 s), the bridge pages and keeps only
        the paired phone by that name (0.4.2+); otherwise it drops its phone and turns
        invisible, so the phone keeps its own keyboard. Returns whether a phone is connected."""
        body = bytes([1]) + (name or "").encode()[:39] if mirroring else bytes([0])
        r = self._call(0x0E, body)
        return bool(r and r[0])

    def drop(self):
        """Disconnect the bridge's phone; the bridge doesn't page it again until mirroring
        stops and starts again (firmware 0.4.2+)."""
        self._call(0x0F)

    def open_setup(self):
        self._call(0x05)

    def reset(self):
        """Have the bridge stop trusting this PC (firmware 0.2.3+). If no other PC uses it
        (always, before 0.3), it forgets everything: its iPhone pairings, its Wi-Fi and
        the key, and restarts invisible over Bluetooth until its button is held.
        Returns True when it reset, False when other PCs keep it."""
        body = self._call(0x0D, tries=3)
        return not body or body[:1] == b"\x01"

    def networks(self):
        """Wi-Fi networks the bridge sees (it scans while staying online, ~2 s)."""
        return json.loads(self._call(0x06, tries=2, wait=6).decode() or "[]")

    def change_network(self, ssid, password):
        """Move the bridge to another network. It answers first, then switches, and
        goes back to the current network if the new one fails (status "err")."""
        from cryptography.hazmat.primitives.ciphers.aead import AESGCM
        k = hmac.new(self.key, b"phoneuse wifi change", hashlib.sha256).digest()
        nonce = os.urandom(12)
        sealed = nonce + AESGCM(k).encrypt(nonce, ssid.encode() + b"\0" + password.encode(), None)
        if self._call(0x07, sealed)[:1] != b"\x01":
            raise RuntimeError("the bridge couldn't read the network details")


# ---------- CLI ----------

def _ask(prompt):
    try:
        return input(prompt)
    except EOFError:
        raise SystemExit(1)


def setup_cli():
    """Interactive setup in the terminal (the browser mirror's settings do the same)."""
    async def run():
        print("Looking for bridges in setup mode (5 s)...")
        found = await scan_ble()
        if not found:
            raise SystemExit("No bridge found. Hold the bridge's lower button for 3 seconds, then try again.")
        for i, (addr, name, rssi) in enumerate(found):
            print(f"  {i + 1}. {name}  ({addr}, {rssi} dBm)")
        pick = found[int(_ask("Which one? [1] ") or 1) - 1]
        async with Setup(pick[0]) as s:
            info = await s.info()
            code = await s.exchange()
            print(f"\nThe bridge's screen should show {code // 1000:03d} {code % 1000:03d}.")
            if _ask("Same code? [y/N] ").strip().lower() != "y":
                raise SystemExit("Stopped: a different code means something else answered.")
            print("Wi-Fi networks the bridge sees:")
            nets = await s.networks()
            for i, n in enumerate(nets):
                print(f"  {i + 1}. {n['s']}  ({n['r']} dBm{', open' if not n['l'] else ''})")
            choice = _ask("Network number (or a name): ").strip()
            ssid = nets[int(choice) - 1]["s"] if choice.isdigit() else choice
            import getpass
            password = getpass.getpass(f"Password for {ssid}: ")
            print("Joining...")
            info2 = await s.join(ssid, password)
        if info2.get("state") == "failed":
            raise SystemExit(f"The bridge couldn't join {ssid}: {info2.get('err')}")
        bridge_id = info.get("id")
        ip = info2.get("ip")
        if not ip:
            for _ in range(10):
                hits = [b for b in find_lan() if b["id"] == bridge_id]
                if hits:
                    ip = hits[0]["ip"]
                    break
        if not ip:
            raise SystemExit("The bridge joined but isn't answering on this network (is this PC on the same one?)")
        save({"id": bridge_id, "ip": ip, "key": s.keys["control"].hex(), "ssid": ssid})
        st = Link().status()
        print(f"Set up: bridge {bridge_id} at {ip} on {ssid}. Phone: {st.get('phone') or 'not paired yet'}")
    asyncio.run(run())


def use_cli(args):
    """Use a bridge another PC set up: phoneuse bridge use [ID]."""
    found = nearby(1.5)
    if args:
        found = [b for b in found if b["id"].endswith(args[0].lower())]
    if not found:
        raise SystemExit("No other bridge answers on this network." if not args else f"No bridge {args[0]} here.")
    if len(found) > 1:
        for b in found:
            print(f"  {b['id'][-4:]}  on {b.get('ssid') or '?'}, {b.get('phone') or 'no iPhone'}")
        raise SystemExit("Several bridges: phoneuse bridge use ID (the last 4 characters)")
    b = found[0]
    if saved():
        print(f"This PC uses bridge {saved()['id'][-4:]} now; it will use {b['id'][-4:]} instead.")
    a = Adoption(b)
    code = a.start()
    print(f"Bridge {b['id'][-4:]} shows {this_pc_name()} and a code. If it's {code // 1000:03d} {code % 1000:03d}, "
          "press either button on the bridge.")
    try:
        cfg = a.wait()
    except RuntimeError as e:
        raise SystemExit(str(e))
    if not cfg:
        raise SystemExit("No answer from the bridge.")
    from .hidclient import Hid
    try:
        Hid().cmd(f"bridge {cfg['ip']} {cfg['key']}")
    except Exception:
        pass
    print(f"This PC now uses bridge {b['id'][-4:]} on {b.get('ssid')}. Phone: {b.get('phone') or 'not paired yet'}")


def status_cli():
    cfg = saved()
    if not cfg:
        print("No bridge set up (phoneuse bridge setup).")
        return
    try:
        link = Link(cfg)
        st = link.status()
    except Exception as e:
        lan = [b for b in find_lan() if b["id"] == cfg["id"]]
        if lan and lan[0]["ip"] != cfg["ip"]:
            cfg["ip"] = lan[0]["ip"]
            save(cfg)
            st = Link(cfg).status()
        else:
            print(f"Bridge {cfg['id']} at {cfg['ip']}: no answer ({e})")
            return
    rtt = sorted(link.rtt)[len(link.rtt) // 2] * 1000 if link.rtt else 0
    print(f"Bridge {st['id']} (firmware {st['fw']}) at {cfg['ip']} on {st['ssid']}, Wi-Fi {st['rssi']} dBm, "
          f"round trip {rtt:.1f} ms")
    print(f"Phone: {st['phone'] + (' (connected)' if st['connected'] else ' (not connected)') if st['phone'] else 'not paired'}")
    if st.get("battery_mv"):
        print(f"Battery: {st['battery_mv'] / 1000:.2f} V")


def main(args):
    sub = args[0] if args else "status"
    if sub == "flash":
        return flash(args[1] if len(args) > 1 else None)
    if sub == "build":
        return idf("build")
    if sub == "log":
        return log(seconds=float(args[1]) if len(args) > 1 else None)
    if sub == "setup":
        return setup_cli()
    if sub == "status":
        return status_cli()
    if sub == "find":
        mine = (saved() or {}).get("id")
        lan = find_lan(1.5)
        print("On the network:" if lan else "On the network: none")
        for b in lan:
            who = "this PC uses it" if b["id"] == mine else (
                f"used by {b['hosts']} other PC{'s' if b['hosts'] != 1 else ''}" if b.get("hosts") else
                "firmware before 0.3" if _fw(b) < (0, 3) else "not set up")
            phone = b.get("phone") and f"{b['phone']}{'' if b.get('connected') else ' (not connected)'}"
            print(f"  {b['id'][-4:]}  {b['ip']}  firmware {b.get('fw')}  {b.get('ssid') or ''}  "
                  f"{phone or 'no iPhone'}  ({who})")
        print("In setup mode nearby:", asyncio.run(scan_ble()) or "none")
        return
    if sub == "use":
        return use_cli(args[1:])
    if sub == "forget":
        why = forget()
        return print("Forgot the bridge, and it reset: no Wi-Fi, no iPhone pairing, hidden until its button is held "
                     "3 s." if why is None else "Forgot the bridge here. Other PCs still use it, so it keeps its "
                     "Wi-Fi and iPhone." if why == "kept" else f"Forgot the bridge here. It didn't reset ({why}); "
                     "it keeps its pairing and Wi-Fi until it's set up again or flashed.")
    raise SystemExit("usage: phoneuse bridge [status|setup|find|use [ID]|flash|build|log [SECONDS]|forget]")
