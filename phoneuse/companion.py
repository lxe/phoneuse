"""The companion app's API (docs/COMPANION.md), served by the browser mirror's
process on the local network (port 8778), so the app can show phoneuse's state,
change its settings, and get a Live Activity while an agent uses the phone.

Only paired apps can use it: every request is signed with the key that pairing
gave the app (HMAC-SHA256, with a time and a nonce, so a recorded request can't be
replayed), and secrets travel sealed with AES-GCM. The page's own routes on
localhost:8777 stay on localhost.
"""
import asyncio
import base64
import hashlib
import hmac
import json
import os
import secrets
import socket
import subprocess
import threading
import time
import urllib.parse

from aiohttp import web

from . import apns, bluetooth_setup, bridge, phones, relay, unlock
from .config import CFG, MACOS, NAME, RUNTIME_DIR, config_path

PORT = 8778
STORE = os.path.join(os.path.dirname(config_path()), "companion.json")
PHASE_HOLD = 4.0  # seconds a driving phase must last before the Live Activity's icon changes to it


def _key(want):
    """What a Live Activity push is for: the state, action, request and warning, without the
    step count and phase, which change with every action."""
    return None if not want else (want[0], want[2], want[4]) + tuple(want[5:]) + (want[1] == "warning",)
START_RETRY = 600  # seconds between Live Activity start pushes to a device that sent no activity token back
MIRROR_GONE = 90  # seconds mirroring must stay off (not a stall or a sleeping screen) before the activity ends
WARN_HOLD = 3  # bridge checks (5 s apart) a warning, or its all-clear, must hold before it shows
WARN_ALERT_EVERY = 1800  # seconds between sounded alerts for the same bridge warning
AUDIO_OFF = os.path.join(os.path.dirname(config_path()), "audio-off")  # exists while the mirror's audio is off
UXPLAY_PID = os.path.join(RUNTIME_DIR, "phoneuse-uxplay.pid")  # written by `phoneuse up`
PAIR_TTL = 600
SKEW = 60


def log(*a):
    print(time.strftime("%H:%M:%S"), "companion:", *a, flush=True)


# ---------- the paired apps ----------

def load():
    try:
        with open(STORE) as f:
            d = json.load(f)
    except (OSError, ValueError):
        d = {}
    if "pc_id" not in d:
        d["pc_id"] = secrets.token_hex(8)
        save(d)
    d.setdefault("devices", {})
    return d


def paired():
    """How many iPhone apps are paired. Agents may control the phone only while one is."""
    return len(load()["devices"])


def forget(dev_id=None):
    """Unpair one app, or every app when dev_id is None. Returns how many were removed."""
    d = load()
    gone = list(d["devices"]) if dev_id is None else [dev_id] if dev_id in d["devices"] else []
    for k in gone:
        dev = d["devices"].pop(k)
        if dev.get("relay"):  # the relay forgets this pairing's tokens too (best effort: they expire anyway)
            threading.Thread(target=relay.unregister, args=(bytes.fromhex(dev["key"]),), daemon=True).start()
    if gone:
        save(d)
        log("unpaired", "every app" if dev_id is None else f"an app: {dev_id}")
    return len(gone)


def save(d):
    os.makedirs(os.path.dirname(STORE), mode=0o700, exist_ok=True)
    tmp = STORE + ".tmp"
    fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w") as f:
        json.dump(d, f, indent=1)
    os.replace(tmp, STORE)


def lan_ip():
    """The address that the phone reaches this PC at: the source of the default route."""
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        s.connect(("192.0.2.1", 9))  # no packet is sent
        return s.getsockname()[0]
    finally:
        s.close()


def pc_name():
    from .config import computer_name
    return computer_name()


def new_pairing():
    """A pairing link that works once, for 10 minutes."""
    d = load()
    key = secrets.token_bytes(32)
    d["pending"] = {"key": key.hex(), "expires": time.time() + PAIR_TTL}
    save(d)
    q = urllib.parse.urlencode({"id": d["pc_id"], "name": pc_name(), "host": lan_ip(), "port": PORT,
                                "key": base64.urlsafe_b64encode(key).rstrip(b"=").decode()})
    return f"phoneuse://pair?{q}"


def _seal_key(key):
    from cryptography.hazmat.primitives import hashes
    from cryptography.hazmat.primitives.kdf.hkdf import HKDF
    return HKDF(algorithm=hashes.SHA256(), length=32, salt=None, info=b"phoneuse seal").derive(key)


def unseal(key, sealed, field):
    from cryptography.hazmat.primitives.ciphers.aead import AESGCM
    raw = base64.b64decode(sealed)
    return AESGCM(_seal_key(key)).decrypt(raw[:12], raw[12:], field.encode()).decode()


def seal(key, text, field):  # for tests and the mock: the app does this side
    from cryptography.hazmat.primitives.ciphers.aead import AESGCM
    nonce = secrets.token_bytes(12)
    return base64.b64encode(nonce + AESGCM(_seal_key(key)).encrypt(nonce, text.encode(), field.encode())).decode()


def signature(key, method, path, t, nonce, body):
    msg = f"{method}\n{path}\n{t}\n{nonce}\n{hashlib.sha256(body).hexdigest()}".encode()
    return hmac.new(key, msg, hashlib.sha256).hexdigest()


# ---------- the server ----------

def signal_level(kind, value):
    """good / fair / weak for a signal: kind "wifi" (dBm) or "phone" (Bluetooth, dB below
    the bridge's strong range; 0 is strong). None when unknown."""
    if value is None:
        return None
    if kind == "wifi":
        return "good" if value >= -65 else "fair" if value >= -76 else "weak"
    return "good" if value >= -8 else "fair" if value >= -24 else "weak"


def bridge_health(s, use):
    """What the owner should know about the bridge, from its status (`s`, {} when it
    doesn't answer): {"online", "phone_connected", "standby", "wifi", "phone", "warning"}. warning is
    None or {"title", "detail"}."""
    if not use:
        return {"online": None, "phone_connected": None, "standby": None, "wifi": None, "phone": None,
                "warning": None}
    online = bool(s)
    wifi = signal_level("wifi", s.get("rssi") or None) if online else None
    fresh = online and 0 <= s.get("bt_rssi_age_ms", -1) < 10000
    phone = signal_level("phone", s.get("bt_rssi_delta")) if fresh and s.get("connected") else None
    warning = None
    if not online:
        warning = {"title": "Bridge is offline", "detail": "Check that it has power and is on Wi-Fi."}
    elif s.get("standby"):
        pass  # no phone mirrors, so the bridge stays away from it on purpose (firmware 0.4+)
    elif not s.get("connected"):
        warning = {"title": "Bridge can't reach your iPhone", "detail": "Move it closer, or check Bluetooth."}
    elif phone == "weak":
        warning = {"title": "Bridge is too far from your iPhone", "detail": "Move it closer."}
    elif wifi == "weak":
        warning = {"title": "Bridge's Wi-Fi is weak", "detail": "Move it closer to your router."}
    return {"online": online, "phone_connected": bool(s.get("connected")) if online else None,
            "standby": bool(s.get("standby")) if online else None, "wifi": wifi, "phone": phone, "warning": warning}


class Companion:
    def __init__(self, mirror, unlocks):
        self.mirror, self.unlocks = mirror, unlocks
        self.nonces = {}  # nonce -> when seen
        self.audio_restarted = 0.0  # when UxPlay was last restarted for an audio change
        self.sent = None  # the Live Activity's last content-state sent: (state, action, steps)
        self.sent_at = 0.0
        self.activity_started = 0.0
        self.last_ts = 0  # the last Live Activity timestamp sent
        self.frame = 0
        self.health = None  # bridge_health() from watch_bridge
        self._phase_seen = self._phase_shown = None
        self._phase_since = 0.0
        self.start_tried = {}  # device id -> when we last pushed a Live Activity start to it
        self.had_activity = {}  # device id -> True once it had an activity in this mirroring session
        self._off_since = 0.0  # when mirroring stopped, while it is off
        self.warned_at = {}  # bridge warning title -> when its alert last sounded
        self._mirror_since = 0.0
        self.sent_to = frozenset()  # the apps the last activity push was for (serves)

    # ---- auth ----
    @web.middleware
    async def auth(self, req, handler):
        body = await req.read()
        t, nonce, sig = (req.headers.get(h, "") for h in ("X-PU-Time", "X-PU-Nonce", "X-PU-Sig"))
        try:
            if abs(time.time() - int(t)) > SKEW:
                raise ValueError("clock")
        except ValueError:
            return web.json_response({"error": "This phone's clock and the PC's differ; check both."}, status=401)
        now = time.time()
        self.nonces = {n: s for n, s in self.nonces.items() if now - s < 2 * SKEW}
        if not nonce or nonce in self.nonces:
            return web.json_response({"error": "Request was sent twice."}, status=401)
        d = load()
        keys = [(k, bytes.fromhex(dev["key"])) for k, dev in d["devices"].items()]
        pending = d.get("pending")
        if pending and pending["expires"] > now:
            keys.append((None, bytes.fromhex(pending["key"])))
        for dev_id, key in keys:
            if hmac.compare_digest(signature(key, req.method, req.path_qs, t, nonce, body), sig):
                break
        else:
            return web.json_response({"error": "This app isn't paired with the PC. Pair it again from Phone Use on the PC."},
                                     status=401)
        self.nonces[nonce] = now
        if dev_id is None:  # first request with a pairing key: the pairing is used up
            dev_id = hashlib.sha256(key).hexdigest()[:12]
            d["devices"][dev_id] = {"key": key.hex(), "added": time.strftime("%Y-%m-%d %H:%M")}
            d.pop("pending", None)
            save(d)
            log("paired an app:", dev_id)
        req["device"], req["key"] = dev_id, key
        req["json"] = json.loads(body) if body else {}
        return await handler(req)

    async def unpair(self, req):
        """The app unpairs itself: the PC forgets its key, so agents stop until an app pairs again."""
        forget(req["device"])
        return web.json_response({"ok": True})

    # ---- state ----
    def daemon(self, cmd):
        try:
            return self.mirror.get_hid().cmd(cmd)
        except Exception as e:
            self.mirror.hid = None
            return f"err {e}"

    async def run(self, fn, *a):
        return await asyncio.get_running_loop().run_in_executor(None, fn, *a)

    async def state(self, req):
        m = self.mirror
        if req.query.get("captured") in ("0", "1"):  # the app: whether iOS mirrors its own screen
            await self.run(self.note_captured, req["device"], req.query["captured"] == "1")
        st = await self.run(self.daemon, "status")
        b = bridge.saved()
        bridge_state = None
        if b:
            def ask():
                try:
                    c = bridge.resolve(b)
                    return bridge.Link(c).status() if c else {}
                except Exception:
                    return {}
            s = await self.run(ask)
            h = bridge_health(s, b.get("use", True))
            bridge_state = {"saved": True, "id": b.get("id"), "ip": b.get("ip"), "ssid": s.get("ssid") or b.get("ssid"),
                            "use": b.get("use", True), "online": bool(s.get("online", bool(s))),
                            "rtt_ms": s.get("rtt_ms"),
                            # whether the bridge reaches the iPhone, and that iPhone's name
                            "connected": bool(s.get("connected")), "phone": s.get("phone") or None,
                            # no phone mirrors, so the bridge stays off the iPhone on purpose (firmware 0.4+)
                            "standby": bool(s.get("standby")),
                            # signal levels: good / fair / weak, or null when unknown
                            "signal": {"wifi": h["wifi"], "phone": h["phone"]}, "warning": h["warning"]}
        p = self.unlocks.pending
        d = load()
        return web.json_response({
            "pc": {"id": d["pc_id"], "name": pc_name(), "receiver": NAME, "version": _version()},
            "mirroring": m.live(),
            "phone": phones.current(),
            # false: another phone mirrors, so this app gets no pushes and its actions are refused
            "serving": self.serves(req["device"]),
            "bluetooth": {"connected": "connected=True" in st, "via": "bridge" if "bridge=" in st else "pc"},
            "agent": {"state": m.agent(), "action": m.agent_action, "since": int(m.agent_started) or None,
                      "steps": m.agent_steps,
                      "phase": self._steady_phase(m.current_phase()) if m.agent() == "driving" else None},
            "awake": m.awake,
            "audio": {"on": not os.path.exists(AUDIO_OFF), "needs_remirror": self.needs_remirror()},
            "session": {**self._start(),
                        "pending": ({"id": sp["id"], "since": int(sp["since"]), "action": sp["action"],
                                     "mode": sp.get("mode"), "deadline": sp.get("deadline")}
                                    if (sp := self.mirror.sessions.pending) else None)},
            # enabled: false while unlocking with a saved passcode is turned off (unlock.py); apps hide it
            "unlock": {"enabled": unlock.ENABLED, "passcode_saved": await self.run(unlock.saved),
                       "pending": {"id": p["id"], "since": int(p.get("since", 0))} if p else None},
            "bridge": bridge_state,
            # this PC's own Bluetooth as a second way in (bluetooth_setup.py); switched on the PC only
            "pc_bluetooth": {"on": bluetooth_setup.on()},
        })

    def needs_remirror(self):
        return bool(self.audio_restarted) and not self.mirror.live()

    # ---- actions ----
    async def takeover(self, req):
        if no := self.refuse_other(req):  # only the mirroring phone's app acts on it
            return no
        self.mirror.take_over(True)
        return web.json_response({"agent": self.mirror.agent()})

    async def resume(self, req):
        if no := self.refuse_other(req):  # only the mirroring phone's app acts on it
            return no
        self.mirror.take_over(False)
        return web.json_response({"agent": self.mirror.agent()})

    async def unlock_answer(self, req):
        if no := self.refuse_other(req):  # only the mirroring phone's app acts on it
            return no
        p, b = self.unlocks.pending, req["json"]
        if not p or b.get("id") != p["id"]:
            return web.json_response({"error": "That unlock request is over."}, status=409)
        p["approved"] = bool(b.get("approve"))
        p["event"].set()
        return web.json_response({"ok": True})

    async def session_answer(self, req):
        if no := self.refuse_other(req):  # only the mirroring phone's app acts on it
            return no
        b = req["json"]
        if not self.mirror.sessions.answer_id(b.get("id"), b.get("approve")):
            return web.json_response({"error": "That request is over."}, status=409)
        return web.json_response({"ok": True})

    @staticmethod
    def _start():
        """When an agent starts: {"ask": bool (older apps), "mode", "seconds"}."""
        from .agent_session import start_setting
        st = start_setting()
        return {"ask": st["mode"] != "auto", **st}

    async def ask_setting(self, req):
        # Agent control: {"mode": auto | countdown | ask, "seconds": 5..30}
        from .agent_session import set_start_setting
        b = req["json"]
        set_start_setting(b.get("mode"), b.get("seconds"))
        await self.run(self.mirror.sessions.sync_gate)
        return web.json_response(self._start())

    async def passcode(self, req):
        if not unlock.ENABLED:
            return web.json_response({"error": unlock.OFF, "passcode_saved": False}, status=410)
        try:
            if req.method == "DELETE":
                await self.run(unlock.forget)
            else:
                try:
                    code = unseal(req["key"], req["json"]["sealed"], "passcode")
                except Exception:
                    raise ValueError("Couldn't read the passcode.")
                await self.run(unlock.save, code)
        except (KeyError, ValueError) as e:
            return web.json_response({"error": str(e) or "Couldn't read the passcode."}, status=400)
        return web.json_response({"passcode_saved": await self.run(unlock.saved)})

    async def awake(self, req):
        self.mirror.set_awake(bool(req["json"].get("on")))
        return web.json_response({"awake": self.mirror.awake})

    async def keyboard(self, req):
        if no := self.refuse_other(req):  # only the mirroring phone's app acts on it
            return no
        r = await self.run(self.daemon, "eject")
        return web.json_response({"ok": r.startswith("ok")} if r.startswith("ok") else {"error": r}, status=200)

    async def cursor(self, req):
        """The pointer (AssistiveTouch). GET: whether it works now ("on", "off" or
        "unknown"; the pointer hops between two spots for about 2 s to find out).
        POST {"on": bool}: turn it on or off by asking Siri out loud through the PC's
        speakers (up to ~15 s; the phone must be unlocked and in earshot). Runs here,
        as the owner's own action: an agent session or approval isn't involved."""
        if no := self.refuse_other(req):  # only the mirroring phone's app acts on it
            return no
        from . import cli
        h = self.mirror.get_hid()

        def run(fn):
            try:
                return fn()
            except SystemExit as e:  # cli helpers exit with a message on failure
                return f"error: {e}"
            except Exception as e:
                return f"error: {e}"
        if req.method == "GET":
            state = await self.run(lambda: run(lambda: cli.cursor_state(h)))
            return web.json_response({"pointer": state})
        on = bool(req["json"].get("on"))
        result = await self.run(lambda: run(lambda: cli.cursor_set(h, on)))
        ok = not str(result).startswith("error")
        return web.json_response({"result": str(result)}, status=200 if ok else 502)

    async def bridge_use(self, req):
        from .bridge_web import use_bridge
        cfg = bridge.saved()
        if not cfg:
            return web.json_response({"error": "No bridge is set up."}, status=409)
        cfg["use"] = bool(req["json"].get("on"))
        bridge.save(cfg)
        await self.run(use_bridge, cfg)
        return web.json_response({"use": cfg["use"]})

    async def bridge_forget(self, req):
        # Forget Bridge…: the same as the PC's DELETE /api/bridge (the bridge resets itself)
        from .bridge_web import _daemon
        if not bridge.saved():
            return web.json_response({"error": "No bridge is set up."}, status=409)
        await self.run(_daemon, "bridge off")
        why = await self.run(bridge.forget)
        return web.json_response({"saved": None, "reset": why is None, "why": why})

    async def bridge_networks(self, req):
        cfg = bridge.saved()
        if not cfg:
            return web.json_response({"error": "No bridge is set up."}, status=409)
        try:
            nets = await self.run(lambda: bridge.Link(bridge.resolve(cfg) or cfg).networks())
        except Exception as e:
            return web.json_response({"error": f"The bridge didn't answer: {e}"}, status=502)
        # The bridge's short keys (s, r, l) become the doc's names.
        nets = [{"ssid": n.get("s", n.get("ssid")), "rssi": n.get("r", n.get("rssi")),
                 "secure": bool(n.get("l", n.get("secure", True)))} for n in nets]
        return web.json_response({"networks": nets})

    async def bridge_network(self, req):
        from .bridge_web import BridgeRoutes
        b = req["json"]
        try:
            password = unseal(req["key"], b["sealed"], "password") if b.get("sealed") else ""
        except Exception:
            return web.json_response({"error": "Couldn't read the password."}, status=400)
        r = await BridgeRoutes(lambda _r: True).change_network(b.get("ssid", ""), password)
        out = json.loads(r.body)
        return web.json_response(out, status=502 if "error" in out else 200)

    async def audio(self, req):
        on = bool(req["json"].get("on"))
        if on == (not os.path.exists(AUDIO_OFF)):
            return web.json_response({"on": on, "needs_remirror": self.needs_remirror()})
        if on:
            os.remove(AUDIO_OFF)
        else:
            open(AUDIO_OFF, "w").close()
        try:
            with open(UXPLAY_PID) as f:
                os.kill(int(f.read()), 15)  # `phoneuse up` starts it again with the new setting
        except (OSError, ValueError):
            return web.json_response({"error": "phoneuse up isn't running UxPlay; the setting applies when it starts."},
                                     status=409)
        self.audio_restarted = time.time()
        log("mirror audio", "on" if on else "off", "- UxPlay restarted; the phone has to mirror again")
        return web.json_response({"on": on, "needs_remirror": True})

    async def push(self, req):
        b, d = req["json"], load()
        dev = d["devices"][req["device"]]
        if b.get("env") and b["env"] != dev.get("env"):
            dev.pop("token_env", None)  # another build: every token's environment is the app's again
        if "relay" in b and bool(b["relay"]) != bool(dev.get("relay")):  # the app registered with the push relay
            dev["relay"] = 1 if b["relay"] else 0
        for k in ("bundle_id", "env", "device", "push_to_start", "activity"):
            if b.get(k) and b[k] != dev.get(k):
                dev[k] = b[k]
                if k == "push_to_start":
                    self.start_tried.pop(req["device"], None)  # a new token may start an activity at once
                if k == "activity":  # a new activity (the app may have started it itself): send it the
                    self.sent = None  # current state now, not what the app guessed
                dev.get("token_env", {}).pop(k, None)  # a new token: its environment is the app's
        save(d)
        return web.json_response({"ok": True})

    # ---- pushes ----
    @staticmethod
    def push_mode(dev):
        """How to push to one paired app: "direct" (this PC's APNs key), "relay", or None."""
        want = CFG.get("push", "auto")
        if want in ("auto", "direct") and apns.configured():
            return "direct"
        if want in ("auto", "relay") and dev.get("relay"):
            return "relay"
        return None

    # ---- which app: only the one on the phone that screen mirrors ----
    def mirroring_phone(self):
        """The phone that screen mirrors to this PC now (its AirPlay name), or None."""
        return phones.current() if self.mirror.live() else None

    def serves(self, dev_id):
        """Whether the app `dev_id` gets this PC's state and pushes. While a phone mirrors, only
        the app on that phone does: each app says whether iOS mirrors its own screen
        (note_captured), which ties it to the phone mirroring at that moment, so another phone's
        app stays out even in the background. An app that never said (older builds) is served."""
        phone = self.mirroring_phone()
        if not phone:
            return True
        dev = load()["devices"].get(dev_id) or {}
        if dev.get("phone"):
            return phones.same(dev["phone"], phone)
        return not phones.same(dev.get("not_phone"), phone)

    def note_captured(self, dev_id, captured):
        """The app says whether iOS mirrors its screen now (UIScreen.isCaptured): while a phone
        mirrors, that tells which phone the app runs on, or that it isn't that phone."""
        phone = self.mirroring_phone()
        if not phone:
            return
        d = load()
        dev = d["devices"].get(dev_id)
        if dev is None:
            return
        if captured and not phones.same(dev.get("phone"), phone):
            dev["phone"] = phone
            dev.pop("not_phone", None)
            log(f"app {dev_id} runs on {phone}")
        elif not captured and (phones.same(dev.get("phone"), phone) or not phones.same(dev.get("not_phone"), phone)):
            dev.pop("phone", None)  # its screen isn't the one mirroring: whatever it was tied to, it isn't this
            dev["not_phone"] = phone
            log(f"app {dev_id} isn't on {phone}: it gets nothing while {phone} mirrors")
        else:
            return
        save(d)

    def refuse_other(self, req):
        """A response refusing an action from an app on a phone that doesn't mirror, or None."""
        if self.serves(req["device"]):
            return None
        return web.json_response({"error": f"{self.mirroring_phone()} is mirroring, not this iPhone."}, status=409)

    def _devices(self):
        return [dev for i, dev in load()["devices"].items()
                if dev.get("bundle_id") and self.push_mode(dev) and self.serves(i)]

    def has_devices(self):
        """Whether a paired phone can get pushes (an agent's countdown waits for one to take it)."""
        return any(dev.get("device") or (dev.get("activity") or {}).get("token") for dev in self._devices())

    def _told(self, rid, r, at):
        """A push about request `rid` went out at `at` with result `r`: once Apple took one,
        the owner has the request, and its countdown runs from then."""
        if r and r[0] == 200 and rid and self.mirror.sessions:
            self.mirror.sessions.countdown_started(rid, at)

    def _send(self, dev, field, token, payload, topic, push_type, priority=10):
        """Send to one of a device's tokens. A token from another build can belong to the
        other APNs environment (a TestFlight token on a development build): on
        BadDeviceToken, try the other one and remember it for that token."""
        if self.push_mode(dev) == "relay":  # the relay picks the token and its environment itself
            act = (dev.get("activity") or {}).get("id") if field == "activity" else None
            return relay.send(bytes.fromhex(dev["key"]), payload, push_type, field, priority, act)
        envs = dev.get("token_env", {})
        env = envs.get(field) or dev.get("env", "production")
        r = apns.send(token, payload, topic, push_type, env, priority)
        if r[0] == 400 and "BadDeviceToken" in r[1]:
            other = "production" if env == "sandbox" else "sandbox"
            r2 = apns.send(token, payload, topic, push_type, other, priority)
            if r2[0] == 200:
                d = load()
                for v in d["devices"].values():
                    if v.get(field) == token or (v.get(field) or {}).get("token") == token:
                        v.setdefault("token_env", {})[field] = other
                save(d)
                return r2
        return r

    def push_request_over(self, kind, rid):
        """Tell the app a session or unlock request is over, so it removes its notification."""
        for dev in self._devices():
            if dev.get("device"):
                r = self._send(dev, "device", dev["device"],
                               {"aps": {"content-available": 1}, "request_over": {"kind": kind, "id": rid}},
                               dev["bundle_id"], "background", priority=5)
                log(f"{kind} request over push:", r)

    def push_session(self, rid, action):
        """The notification is the fallback: a phone with a live activity gets the
        request there (state "asking"), not twice."""
        for dev in self._devices():
            if dev.get("device") and not (dev.get("activity") or {}).get("token"):
                p = self.mirror.sessions.pending or {}
                title = (f"Agent taking control in {p['seconds']} s" if p.get("mode") == "countdown"
                         else "Agent requests control")
                at = time.time()
                r = self._send(dev, "device", dev["device"], {"aps": {"alert": {"title": title,
                                                                 "body": action or "Using the phone"},
                                                       "category": "SESSION", "sound": "default",
                                                       "interruption-level": "time-sensitive"},
                                              "session_id": rid},
                              dev["bundle_id"], "alert")
                log("session request push:", r)
                self._told(rid, r, at)

    def push_unlock(self, rid):
        for dev in self._devices():
            if dev.get("device"):
                r = self._send(dev, "device", dev["device"], {"aps": {"alert": {"title": "Unlock the iPhone?",
                                                                 "body": "Agent asks to enter your saved passcode."},
                                                       "category": "UNLOCK", "sound": "default"},
                                              "unlock_id": rid},
                              dev["bundle_id"], "alert")
                log("unlock push:", r)

    async def activities(self):
        """Keep one Live Activity on the phone the whole time the phone mirrors to this PC:
        "idle" (the owner is in control), "asking" (an agent waits for the owner's
        Allow, with Allow and Deny buttons), "driving" (with the agent's phase: acting,
        reading or thinking) or "paused" (the owner took over). It ends when mirroring
        stops. An activity the app or iOS ended is started again by push."""
        while True:
            await asyncio.sleep(0.5)
            if not self._devices_by_id(every=True):
                continue
            want = self._activity_state()
            served = frozenset(self._devices_by_id())  # another phone started mirroring: some apps drop out
            # one push per change the owner should see: a new state, request, action or
            # warning. The ring and the robot animate on the phone by themselves, and the
            # step count and phase ride along with the next push.
            if _key(want) == _key(self.sent) and served == self.sent_to and not self._needs_start(want):
                continue
            self.sent_to = served
            await self.run(self._send_activity, want)

    def _activity_state(self):
        """(state, phase, action, steps, request id), or None while the phone doesn't mirror."""
        m = self.mirror
        if not m.live():
            now = time.time()
            self._off_since = self._off_since or now
            if self.sent and now - self._off_since < MIRROR_GONE:
                return self.sent  # a stall or the screen sleeping: leave the activity as it is
            self.had_activity.clear()  # mirroring ended: the next session starts a new activity
            return None
        self._off_since = 0
        p = m.sessions.pending if m.sessions else None
        if p:  # a countdown's length: its deadline is set when the phone takes the push
            return ("asking", "asking", p["action"] or "Using the phone", 0, p["id"],
                    p.get("seconds") if p.get("mode") == "countdown" else None)
        a = m.agent()
        if a == "driving":
            return ("driving", self._steady_phase(m.current_phase()), m.agent_action or "Working", m.agent_steps, "")
        if a == "paused":
            return ("paused", "paused", m.agent_action, m.agent_steps, "")
        w = (self.health or {}).get("warning")
        if w:  # the owner should fix the bridge before an agent can use the phone
            return ("idle", "warning", w["title"], 0, "", None, w["detail"])
        return ("idle", "idle", "", 0, "")

    async def watch_bridge(self):
        """Check the bridge every 5 s for the Live Activity's warning. A warning shows, and
        clears, only after it has held for WARN_HOLD checks in a row, so a signal hovering
        near a threshold doesn't flip the activity back and forth."""
        seen, count = None, 0
        link = [None, None]  # one Link (one bridge session) for all checks, and the bridge it is for
        while True:
            await asyncio.sleep(5)
            b = bridge.saved()
            if not b:
                self.health = None
                continue

            def ask():
                # a new Link per check cost the bridge a key exchange every 5 s, and a session
                # slot (it keeps four); keep one until the bridge changes or stops answering
                try:
                    if link[0] is None or link[1] != (b.get("id"), b.get("ip")):
                        c = bridge.resolve(b)
                        if not c:
                            return {}
                        link[:] = [bridge.Link(c), (b.get("id"), b.get("ip"))]
                    return link[0].status()
                except Exception:
                    if link[0]:
                        try:
                            link[0].close()
                        except Exception:
                            pass
                    link[:] = [None, None]
                    return {}
            h = bridge_health(await self.run(ask), b.get("use", True))
            w, shown = h["warning"], (self.health or {}).get("warning")
            if (w or {}).get("title") == (shown or {}).get("title"):
                seen, count = None, 0
                self.health = h
                continue
            key = (w or {}).get("title")
            seen, count = key, (count + 1 if seen == key else 1)
            if count < WARN_HOLD and self.health is not None:  # the first check shows at once
                if self.health:  # keep the signal bars fresh; only the warning waits
                    self.health = dict(h, warning=shown)
                continue
            log("bridge warning:", w["title"]) if w else log("bridge warning cleared")
            seen, count = None, 0
            self.health = h

    def _steady_phase(self, phase):
        """The phase the Live Activity shows: a quick acting/reading flicker would swap its
        icon every second, so a new phase shows only once it has lasted PHASE_HOLD seconds
        (thinking shows at once: it is the long one)."""
        now = time.time()
        if phase != self._phase_seen:
            self._phase_seen, self._phase_since = phase, now
        if phase != self._phase_shown and (phase == "thinking" or now - self._phase_since >= PHASE_HOLD
                                           or self._phase_shown is None):
            self._phase_shown = phase
        return self._phase_shown

    def _needs_start(self, want):
        """A device without a live activity, which we haven't asked to start lately."""
        if not want:
            return False
        now = time.time()
        return any(not (dev.get("activity") or {}).get("token") and not self.had_activity.get(i)
                   and now - self.start_tried.get(i, 0) > START_RETRY
                   for i, dev in self._devices_by_id().items() if dev.get("push_to_start"))

    def _devices_by_id(self, every=False):
        """Apps that can get pushes: those served now (serves), or every one (to end an activity)."""
        return {i: dev for i, dev in load()["devices"].items()
                if dev.get("bundle_id") and self.push_mode(dev) and (every or self.serves(i))}

    def _send_activity(self, want):
        m, before = self.mirror, self.sent
        self.sent, self.sent_at = want, time.time()
        sent_at = self.sent_at
        # an asking countdown runs from this push: the phone and the PC count to the same deadline
        req = (self.mirror.sessions.pending or {}) if want and want[0] == "asking" and self.mirror.sessions else {}
        deadline = req.get("deadline") or (sent_at + want[5] if want and want[0] == "asking" and want[5] else None)
        self.frame = (self.frame + 1) % 1000

        def content(st):
            return {"state": st[0], "phase": st[1], "action": st[2], "steps": st[3], "requestId": st[4],
                    # asking with a countdown: when the agent starts unless the owner stops it
                    "deadline": round(deadline) if st[0] == "asking" and deadline else None,
                    # idle with phase "warning": action is the title, detail the second line
                    "detail": st[6] if len(st) > 6 else None,
                    "updatedAt": round(time.time(), 2),  # every refresh differs, and restarts the ring
                    "frame": self.frame,  # counts up with each refresh: the app draws one animation frame per value
                    "startedAt": int(m.agent_started if st[0] in ("driving", "paused") else self.mirror_since())}

        alert = None
        if want and want[0] == "asking" and (not before or before[0] != "asking"):
            alert = {"title": f"Agent taking control in {want[5]} s" if want[5] else "Agent requests control",
                     "body": want[2], "sound": "default"}
        elif (want and want[1] == "warning" and (not before or before[1] != "warning")
              and time.time() - self.warned_at.get(want[2], 0) > WARN_ALERT_EVERY):
            self.warned_at[want[2]] = time.time()
            alert = {"title": want[2], "body": want[6], "sound": "default"}  # the bridge needs the owner
        everyone_want = want
        for i, dev in self._devices_by_id(every=True).items():
            want = everyone_want if self.serves(i) else None  # another phone's app: its activity ends
            topic = dev["bundle_id"] + ".push-type.liveactivity"
            # iOS ignores an update whose timestamp isn't newer than the last one it applied
            self.last_ts = max(int(time.time()), self.last_ts + 1)
            # The same high relevance every time: with several Live Activities on the phone
            # (Screen Mirroring, a screen recording), iOS picks which one the Dynamic Island
            # shows by relevance; a constant top score keeps ours in place instead of being
            # swapped in and out on every 1 s frame.
            aps = {"timestamp": self.last_ts, "relevance-score": 100}
            act = dev.get("activity") or {}
            if want and not act.get("token"):
                # one start, then wait for the app to send the new activity's token: starting
                # again sooner could leave two activities on the phone
                # and never again in this mirroring session once the phone had one: Apple makes
                # every start an alert, and the owner may have swiped the activity away
                if (not dev.get("push_to_start") or self.had_activity.get(i)
                        or time.time() - self.start_tried.get(i, 0) < START_RETRY):
                    continue
                self.start_tried[i] = time.time()
                aps.update({"event": "start", "content-state": content(want),
                            "attributes-type": "AgentActivityAttributes", "attributes": {"pcName": pc_name()},
                            "alert": alert or {"title": "Phone Use", "body": "Mirroring to " + pc_name()}})
                r = self._send(dev, "push_to_start", dev["push_to_start"], {"aps": aps}, topic, "liveactivity")
            elif act.get("token"):
                self.had_activity[i] = True
                if want:
                    aps.update({"event": "update", "content-state": content(want)})
                    if alert:
                        aps["alert"] = alert
                else:
                    aps.update({"event": "end", "content-state": content(before or ("idle", "idle", "", 0, "")),
                                "dismissal-date": int(time.time())})
                r = self._send(dev, "activity", act["token"], {"aps": aps}, topic, "liveactivity",
                               priority=10)  # every frame at high priority: iOS held back priority-5 frames
                # that activity is gone, or we just ended it: forget its token, so the next
                # mirroring session starts a new one (APNs accepts updates to an ended
                # activity without an error, and the phone would show nothing)
                if not want or r[0] == 410 or (r[0] == 400 and "BadDeviceToken" in r[1]):
                    d = load()
                    for v in d["devices"].values():
                        if (v.get("activity") or {}).get("token") == act["token"]:
                            v.pop("activity", None)
                    save(d)
            else:
                continue
            log(f"live activity {aps.get('event')} ({want[0] if want else 'off'}):", r)
            if want and want[0] == "asking":
                self._told(want[4], r, sent_at)
        want = everyone_want

    def mirror_since(self):
        if self.mirror.live():
            if not self._mirror_since:
                self._mirror_since = time.time()
        else:
            self._mirror_since = 0
        return self._mirror_since or time.time()

    # ---- Bonjour ----
    async def advertise(self):
        """Keep `_phoneuse._tcp` published through Avahi's D-Bus API (works inside the Flatpak,
        which has no avahi-utils). Checks every 30 s and publishes again if Avahi restarted.
        On macOS, mDNSResponder publishes it for as long as `dns-sd -R` runs."""
        if MACOS:
            return await self._advertise_dns_sd()
        if os.name == "nt":
            # No Avahi (gi) on Windows: say so once instead of every 30 s. Pairing links carry the
            # PC's address; Bonjour is only the app's fallback when that address changes.
            return log("not published over Bonjour (Windows): after this PC's address changes, "
                       "pair the app again (phoneuse app pair)")
        group = [None]
        conn = [None]  # held for as long as we publish: Avahi frees a group when its connection closes

        def publish():
            from gi.repository import Gio, GLib
            if conn[0] is None or conn[0].is_closed():
                conn[0] = Gio.bus_get_sync(Gio.BusType.SYSTEM)
            bus = conn[0]

            def call(path, iface, method, args=None, rtype=None):
                r = bus.call_sync("org.freedesktop.Avahi", path, iface, method, args,
                                  GLib.VariantType(rtype) if rtype else None, Gio.DBusCallFlags.NONE, 5000, None)
                return r.unpack() if rtype else None
            why = "first time"
            if group[0]:
                try:
                    st = call(group[0], "org.freedesktop.Avahi.EntryGroup", "GetState", None, "(i)")[0]
                    if st in (1, 2):
                        return None  # still published (1: registering, 2: established)
                    why = f"Avahi state {st}"
                except GLib.Error as e:
                    why = Gio.DBusError.get_remote_error(e) or e.message
                try:
                    call(group[0], "org.freedesktop.Avahi.EntryGroup", "Free")
                except GLib.Error:
                    pass
            group[0] = call("/", "org.freedesktop.Avahi.Server", "EntryGroupNew", None, "(o)")[0]
            txt = [f"id={load()['pc_id']}".encode(), f"name={pc_name()}".encode()]
            call(group[0], "org.freedesktop.Avahi.EntryGroup", "AddService",
                 GLib.Variant("(iiussssqaay)", (-1, -1, 0, f"Phone Use on {pc_name()}", "_phoneuse._tcp", "", "",
                                                 PORT, txt)))
            call(group[0], "org.freedesktop.Avahi.EntryGroup", "Commit")
            return why
        while True:
            try:
                why = await self.run(publish)
                if why:
                    log("published over Bonjour as", f"Phone Use on {pc_name()} ({why})")
            except Exception as e:
                group[0] = None
                log("can't publish over Bonjour:", e)
            await asyncio.sleep(30)

    async def _advertise_dns_sd(self):
        """macOS: register with mDNSResponder through the system's dns_sd library. The registration belongs
        to this process's connection to the daemon, so it ends with the process, however it ends."""
        import ctypes
        lib = ctypes.CDLL("/usr/lib/libSystem.B.dylib")  # dns_sd is part of libSystem
        lib.DNSServiceRegister.argtypes = [ctypes.POINTER(ctypes.c_void_p), ctypes.c_uint32, ctypes.c_uint32,
                                           ctypes.c_char_p, ctypes.c_char_p, ctypes.c_char_p, ctypes.c_char_p,
                                           ctypes.c_uint16, ctypes.c_uint16, ctypes.c_char_p, ctypes.c_void_p,
                                           ctypes.c_void_p]
        reply = ctypes.CFUNCTYPE(None, ctypes.c_void_p, ctypes.c_uint32, ctypes.c_int32, ctypes.c_char_p,
                                 ctypes.c_char_p, ctypes.c_char_p, ctypes.c_void_p)
        lib.DNSServiceRegister.argtypes[10] = reply
        lib.DNSServiceProcessResult.argtypes = [ctypes.c_void_p]

        loop = asyncio.get_running_loop()

        def registered(_ref, _flags, err, name, _type, _domain, _ctx):
            if err == -65570:  # PolicyDenied: no Local Network permission (Python run from a terminal)
                asyncio.run_coroutine_threadsafe(self._advertise_dns_sd_tool(), loop)
            elif err:
                log("can't publish over Bonjour: dns_sd error", err)
            else:
                log("published over Bonjour as", name.decode())
        self._dns_sd_reply = reply(registered)  # both kept for the life of the process
        self._dns_sd = ctypes.c_void_p()
        txt = b"".join(bytes([len(t)]) + t for t in (f"id={load()['pc_id']}".encode(), f"name={pc_name()}".encode()))
        err = lib.DNSServiceRegister(ctypes.byref(self._dns_sd), 0, 0, f"Phone Use on {pc_name()}".encode(),
                                     b"_phoneuse._tcp", None, None, socket.htons(PORT), len(txt), txt,
                                     self._dns_sd_reply, None)
        if err:
            return log("can't publish over Bonjour: dns_sd error", err)
        # the daemon's answers arrive on this connection; reading them completes the registration
        import threading
        threading.Thread(target=lambda: [lib.DNSServiceProcessResult(self._dns_sd) for _ in iter(int, 1)],
                         daemon=True).start()

    async def _advertise_dns_sd_tool(self):
        """The `dns-sd` tool registers under its own permission. A shell loop ends it once this process is gone,
        so no registration outlives the companion."""
        name, txt = f"Phone Use on {pc_name()}", [f"id={load()['pc_id']}", f"name={pc_name()}"]
        script = ('dns-sd -R "$1" _phoneuse._tcp local "$2" "$3" "$4" >/dev/null & child=$!; '
                  f'while kill -0 {os.getpid()} 2>/dev/null; do sleep 2; done; kill $child')
        await asyncio.create_subprocess_exec("/bin/sh", "-c", script, "sh", name, str(PORT), *txt,
                                             stdout=asyncio.subprocess.DEVNULL, stderr=asyncio.subprocess.DEVNULL)
        log("published over Bonjour (dns-sd) as", name)

    def app(self):
        a = web.Application(middlewares=[self.auth], client_max_size=64 * 1024)
        r = a.router
        r.add_get("/v1/state", self.state)
        r.add_post("/v1/agent/takeover", self.takeover)
        r.add_post("/v1/agent/resume", self.resume)
        r.add_post("/v1/unlock/answer", self.unlock_answer)
        r.add_post("/v1/session/answer", self.session_answer)
        r.add_post("/v1/session/ask", self.ask_setting)
        r.add_route("POST", "/v1/passcode", self.passcode)
        r.add_route("DELETE", "/v1/passcode", self.passcode)
        r.add_post("/v1/awake", self.awake)
        r.add_post("/v1/keyboard", self.keyboard)
        r.add_route("*", "/v1/cursor", self.cursor)
        r.add_post("/v1/bridge/use", self.bridge_use)
        r.add_delete("/v1/bridge", self.bridge_forget)
        r.add_get("/v1/bridge/networks", self.bridge_networks)
        r.add_post("/v1/bridge/network", self.bridge_network)
        r.add_post("/v1/mirror/audio", self.audio)
        r.add_post("/v1/push", self.push)
        r.add_delete("/v1/pairing", self.unpair)
        return a

    async def start(self):
        runner = web.AppRunner(self.app(), access_log=None)
        await runner.setup()
        await web.TCPSite(runner, "0.0.0.0", PORT).start()
        log(f"companion API on port {PORT}")
        asyncio.ensure_future(self.advertise())
        asyncio.ensure_future(self.activities())
        asyncio.ensure_future(self.watch_bridge())


def _root():
    return os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def _version():
    try:
        return subprocess.run(["git", "-C", _root(), "rev-parse", "--short", "HEAD"], capture_output=True,
                              text=True, timeout=2).stdout.strip()
    except (OSError, subprocess.SubprocessError):
        return ""
