"""The browser mirror: the phone's screen, with mouse and keyboard, on a local
web page (http://localhost:PORT, `web_port` in config.toml).

The video comes from the hub (hub.py on Linux, as RTP over localhost; avhub.py on
Windows, as a subscription). When a tab joins, this server asks the hub for a
keyframe, so the tab shows the phone right away.

Each tab gets encoded frames over a WebSocket and decodes them itself
(WebCodecs). Mouse and keyboard events come back over the same WebSocket and
go to the phone through pointer.py, like the mirror window's. Input is only
accepted while the phone is mirroring and the tab is showing it.

Listens on 127.0.0.1 only: anyone who can open the page can control the phone.

Unlocking: the page's settings panel saves the phone's passcode in
the config directory (unlock.py). `phoneuse unlock` asks to use it (POST /api/unlock); the owner answers on
the page or in a desktop notification, and only then is it typed (unlock.py).
"""
import asyncio
import json
import os
import secrets
import shutil
import sys
import threading
import time

from aiohttp import web

from . import screen, unlock
from .agent_session import AgentSession, set_start_setting, start_setting
from .config import CFG, IS_WINDOWS, NAME, SCREEN_PT, config_path
from .hidclient import Hid
from .pointer import Keys, Pointer

NOT_PAIRED = ("No iPhone app is paired with this PC, so agents can't control the phone: nothing was sent. "
              "Stop and ask the user to pair the Phone Use app on the iPhone (Pair iPhone App in the Phone Use "
              "window on the PC).")


def paired():
    """Whether an iPhone app is paired. Unreadable pairings count as none, so agents stay out."""
    try:
        from . import companion
        return companion.paired() > 0
    except Exception:  # noqa: BLE001
        return False

# Where the pointer rests while nobody uses the phone (after Bluetooth reconnects,
# after an agent session, and during keep-awake nudges): the very bottom left corner,
# clear of the dock and the home bar, where it covers nothing and touches nothing.
PARK = (0.03, 0.995)

HERE = os.path.dirname(os.path.realpath(__file__))
PAGE = os.path.join(HERE, "web", "index.html")
PORT = int(CFG.get("web_port", 8777))
RTP_PORT = screen.WEB_RTP_PORT


def log(*a):
    print(time.strftime("%H:%M:%S"), "web:", *a, flush=True)


class QuietErrors:
    """Logs an error once while it keeps repeating (every input fails while the HID
    service restarts), then how many more there were."""

    def __init__(self):
        self.last, self.since, self.more = None, 0.0, 0

    def __call__(self, label, e):
        msg, now = f"{label} {e}", time.monotonic()
        if msg == self.last and now - self.since < 10:
            self.more += 1
            return
        if self.more:
            log(f"(the error before repeated {self.more} more times)")
        log(msg)
        self.last, self.since, self.more = msg, now, 0


input_error = QuietErrors()


class AvStream:
    """Windows: the hub's stream (avhub.py), as H.264 access units for the tabs."""

    def __init__(self, on_frame):
        from .avhub import subscribe
        self.subscribed = False
        threading.Thread(target=subscribe, args=(on_frame, self._state), daemon=True).start()

    def _state(self, on):
        self.subscribed = on

    @staticmethod
    def keyframe():
        """Ask the hub for a keyframe (a tab joined or its decoder started over)."""
        try:
            screen.hub_request("keyframe")
        except OSError as e:
            log("hub:", e)


class Stream:
    """Linux: the hub's stream (hub.py), as H.264 access units for the tabs."""

    def __init__(self, on_frame):
        import gi
        gi.require_version("Gst", "1.0")
        from gi.repository import Gst
        self.Gst = Gst
        self.on_frame = on_frame  # (bytes, is_keyframe), called from a GStreamer thread
        rtp = "application/x-rtp,media=video,encoding-name=H264,clock-rate=90000,payload=96"
        self.pipe = Gst.parse_launch(
            f'udpsrc reuse=false address=127.0.0.1 port={RTP_PORT} buffer-size=8388608 caps="{rtp}" '
            "! rtph264depay ! h264parse config-interval=-1 ! video/x-h264,stream-format=byte-stream,alignment=au "
            "! appsink name=enc sync=false async=false emit-signals=true")
        self.pipe.get_by_name("enc").connect("new-sample", self._frame)
        bus = self.pipe.get_bus()
        bus.add_signal_watch()
        bus.connect("message::error", self._error)
        self.pipe.set_state(Gst.State.PLAYING)

    def _frame(self, sink):
        Gst = self.Gst
        buf = sink.emit("pull-sample").get_buffer()
        ok, info = buf.map(Gst.MapFlags.READ)
        if ok:
            self.on_frame(bytes(info.data), not buf.has_flags(Gst.BufferFlags.DELTA_UNIT))
            buf.unmap(info)
        return Gst.FlowReturn.OK

    @staticmethod
    def keyframe():
        """Ask the hub for a keyframe (a tab joined or its decoder started over)."""
        try:
            screen.hub_request("keyframe")
        except OSError as e:
            log("hub:", e)

    def _error(self, _bus, msg):
        err, _ = msg.parse_error()
        log("stream error:", err.message)


class Mirror:
    def __init__(self):
        self.loop = None
        self.tabs = set()
        self.stream = (AvStream if IS_WINDOWS else Stream)(self._frame)
        self.mirroring = False
        self.hub_live = False
        self.hid = None
        # Who controls the phone (agent_session.py): the one record the gate, the pages,
        # the companion app and the notifications derive from.
        self.session = AgentSession()
        self.session.listeners.append(self.on_session)
        self.bluetooth = True  # the phone's Bluetooth link (mouse and keyboard)
        self.last_frame_at = 0.0  # when the phone's picture last changed
        self.stale = False  # the picture stopped following the phone (see check_stale)
        self.last_input_at = 0.0  # when the page or an agent last sent input
        self.awake = os.path.exists(AWAKE_FILE)  # keep the screen awake (see keep_awake)
        self.screen_dark = False  # a keep-awake nudge drew no frame: the screen is off
        self.hover_at = PARK  # where the page's pointer last was (screen fractions)
        self.unlocks = None
        self.sessions = None

    def get_hid(self):
        if self.hid is None:
            self.hid = Hid()
        return self.hid

    def live(self):
        return self.mirroring and self.hub_live

    # ---- keep awake: stop Auto-Lock while nobody uses the phone ----
    AWAKE_EVERY = 1.0  # seconds between nudges
    AWAKE_QUIET = 2.0  # skip nudges this long after real input

    def set_awake(self, on):
        self.awake = on
        if on:
            os.makedirs(os.path.dirname(AWAKE_FILE), exist_ok=True)
            open(AWAKE_FILE, "w").close()
        elif os.path.exists(AWAKE_FILE):
            os.remove(AWAKE_FILE)
        self.screen_dark = False
        log("keep awake", "on" if on else "off")
        self.broadcast_state()

    async def keep_awake(self):
        """While on, move the pointer a few pixels and back about once a second so
        Auto-Lock never fires. Real input (the page, an agent) takes over; a nudge
        that draws no new frame means the screen is off (the pointer always shows
        when it moves), so nudging pauses until the picture changes again rather
        than waking a phone that was just locked."""
        # Absolute moves: they land exactly on every phone, while a small relative
        # move can shift the pointer less than a pixel at a slow tracking speed.
        sign, misses = 1, 0
        loop = asyncio.get_running_loop()
        while True:
            await asyncio.sleep(self.AWAKE_EVERY)
            if (not self.awake or self.screen_dark or not self.live() or self.agent() != "idle"
                    or time.time() - self.last_input_at < self.AWAKE_QUIET):
                continue
            before = self.last_frame_at
            try:
                x, y = PARK
                x += 0.01 * sign
                r = await loop.run_in_executor(None, self.get_hid().cmd, f"a {x:.4f} {min(max(y, 0.0), 1.0):.4f} 0")
            except Exception:
                self.hid = None
                continue
            sign = -sign
            if "dropped" in r:  # the link was busy; this nudge never left
                continue
            await asyncio.sleep(0.8)
            if self.last_frame_at != before or time.time() - self.last_input_at < self.AWAKE_QUIET:
                misses = 0
                continue
            misses += 1
            if misses >= 3:  # three nudges in a row drew nothing: the screen is off
                misses = 0
                self.screen_dark = True
                log("keep awake: no picture after three nudges; the screen is off, pausing")

    # Releasing the link while idle (so iOS shows its own keyboard) is off: after
    # phoneuse drops the link, iOS refuses phoneuse's reconnects ("Connection
    # refused", measured 2026-11-07) until the owner taps Phone Use in Bluetooth
    # settings, so the next input could never reach the phone. The daemon keeps
    # its release command for a better trigger.
    RELEASE_IDLE = None

    def bluetooth_up(self):
        """Whether the link works: connected, or released on purpose."""
        try:
            st = self.get_hid().cmd("status")
            return "connected=True" in st or "released=True" in st
        except Exception:
            self.hid = None
            return False

    # ---- agent sessions: who is driving the phone (agent_session.py holds the state) ----
    @property
    def taken_over(self):
        return self.session.current() == "paused"

    @property
    def agent_action(self):
        return self.session.action

    @property
    def agent_steps(self):
        return self.session.steps

    @property
    def agent_started(self):
        return self.session.started

    def agent(self):
        """"driving" while an agent acts (the page shows it and takes no input),
        "paused" after the user took over, else "idle"."""
        return self.session.agent()

    def current_phase(self):
        return self.session.current_phase()

    def agent_acts(self, action="", phase="acting"):
        """Called before every agent action (CLI or MCP), once access allows it. False
        while the user has taken over: the agent must stop."""
        if not self.session.act(action, phase):
            return False
        msg = json.dumps({"type": "agent-action", "action": self.agent_action, "steps": self.agent_steps})
        for tab in list(self.tabs):
            asyncio.ensure_future(tab.ws.send_str(msg))
        self.last_input_at = time.time()
        return True

    def on_session(self, session, old, why):
        """Every change of who controls the phone: tell the pages, and rest the pointer
        when an agent lets go."""
        if old != session.state:
            log(f"agent session: {old} -> {session.state} ({why})")
        if old == "driving" and session.state != "driving":
            self.park()
        if self.loop:
            self.loop.call_soon_threadsafe(self.broadcast_state)

    def park(self):
        """Move the pointer to PARK, out of the way (in a thread: the link can be slow)."""
        def go():
            try:
                self.get_hid().cmd(f"a {PARK[0]} {PARK[1]} 0")
            except Exception:
                self.hid = None
        if self.live():
            threading.Thread(target=go, daemon=True).start()

    def take_over(self, take):
        if take:
            self.session.take_over()
        else:
            self.session.resume()

    STALE_INPUT = 1.5  # s: input that should change the picture, with no new frame since
    STALE_CLOCK = 4.0  # s past a minute boundary (the status-bar clock changes) with no new frame

    def check_stale(self):
        """Whether the mirror shows an old picture: input went to the phone and no frame
        followed, or a minute rolled over (the phone's clock changed) and no frame
        arrived. Either means the phone's screen is off, or mirroring stalled or lags.
        Agents get a warning with every screen read; the pages show it."""
        now = time.time()
        if not self.live() or not self.last_frame_at:
            stale = False
        else:
            unanswered = self.last_input_at > self.last_frame_at and now - self.last_input_at > self.STALE_INPUT
            clock = now > (self.last_frame_at // 60 + 1) * 60 + self.STALE_CLOCK
            stale = unanswered or clock
        if stale != self.stale:
            self.stale = stale
            log("the picture " + ("stopped updating: the phone's screen is off, or mirroring stalled"
                                  if stale else "is updating again"))
            self.broadcast_state()
        return stale

    def broadcast_state(self):
        for tab in list(self.tabs):
            tab.state()

    def _frame(self, data, key):
        self.last_frame_at = time.time()
        if self.stale:
            self.loop.call_soon_threadsafe(self.check_stale)
        if self.screen_dark:
            self.screen_dark = False
        if self.loop is None:  # the hub's first keyframe can beat the server's start; tabs ask again
            return
        self.loop.call_soon_threadsafe(self._send_frame, data, key)

    def _send_frame(self, data, key):
        for tab in list(self.tabs):
            if key:
                tab.synced = True
            if tab.synced:
                tab.send(bytes([1 if key else 0]) + data)

    async def watch(self):
        # Stay subscribed to the hub, and follow whether the phone is mirroring and
        # the hub has a picture of it.
        loop = asyncio.get_running_loop()
        joined = False
        while True:
            mirroring = False
            try:
                if not IS_WINDOWS:  # Linux viewers renew their UDP subscription; Windows ones stay connected
                    line, _ = await loop.run_in_executor(None, screen.hub_request, f"{'keep' if joined else 'join'} {RTP_PORT}")
                    joined = line.startswith("ok")
                line, _ = await loop.run_in_executor(None, screen.hub_request, "status")
                self.hub_live = "live=1" in line
                mirroring = "mirroring=1" in line
            except OSError:
                joined, self.hub_live = False, False  # the hub is (re)starting
            if not IS_WINDOWS:  # Windows: the hub's status said it above
                mirroring = await loop.run_in_executor(None, screen.airplay_mirroring)  # lsof on macOS: not on the loop
            bt = await loop.run_in_executor(None, self.bluetooth_up)
            changed = mirroring != self.mirroring or bt != self.bluetooth
            if bt and not self.bluetooth:  # (re)connected: rest the pointer where it touches nothing
                self.hover_at = PARK
                try:
                    await asyncio.get_running_loop().run_in_executor(
                        None, self.get_hid().cmd, f"a {PARK[0]} {PARK[1]} 0")
                    log("Bluetooth back: pointer parked at the bottom middle")
                except Exception:
                    self.hid = None
            self.mirroring, self.bluetooth = mirroring, bt
            for tab in list(self.tabs):
                if changed or tab.told_live != self.live() or tab.told_agent != self.agent():
                    tab.state()
            await asyncio.sleep(0.5)


class Tab:
    """One browser tab: its video feed and its input."""

    def __init__(self, mirror, ws):
        self.mirror, self.ws = mirror, ws
        self.synced = False  # has had a keyframe
        self.playing = False  # has shown a frame (the page says so)
        self.told_live = False
        self.told_agent = None
        self.pointer = Pointer(mirror.get_hid, log)
        self.keys = Keys(mirror.get_hid)
        self.queue = asyncio.Queue(maxsize=8)

    def send(self, data):
        # Drop frames for a tab that can't keep up rather than queueing delay;
        # it gets a keyframe to start again from.
        if self.queue.full():
            self.synced = False
            while not self.queue.empty():
                self.queue.get_nowait()
            self.mirror.stream.keyframe()
            return
        self.queue.put_nowait(data)

    def state(self):
        live = self.mirror.live()
        self.told_live = live
        self.told_agent = self.mirror.agent()
        msg = json.dumps({"type": "state", "live": live, "mirroring": self.mirror.mirroring,
                          "agent": self.told_agent, "bluetooth": self.mirror.bluetooth,
                          "awake": self.mirror.awake, "stale": self.mirror.stale,
                          "name": NAME, "screen": SCREEN_PT,
                          "bezel": screen.BEZEL, "radius": screen.SCREEN_RADIUS})
        asyncio.ensure_future(self.ws.send_str(msg))

    async def pump(self):
        while True:
            await self.ws.send_bytes(await self.queue.get())

    def handle(self, msg):
        t = msg.get("t")
        if t in ("move", "down", "up", "scroll", "mod", "key", "consumer"):
            self.mirror.last_input_at = time.time()
            if "x" in msg:
                self.mirror.hover_at = (float(msg["x"]), float(msg["y"]))
        if t in ("move", "down", "up", "scroll", "mod", "key", "consumer", "blur") and os.path.exists(INPUT_TRACE + ".on"):
            # Debugging: `touch $XDG_RUNTIME_DIR/phoneuse-input.jsonl.on` logs the page's input.
            with open(INPUT_TRACE, "a") as f:
                f.write(json.dumps({"at": time.time(), **msg}) + "\n")
        if t == "playing":
            self.playing = True
            return
        if t == "keyframe":  # the tab's decoder failed and starts over
            self.synced = False
            self.mirror.stream.keyframe()
            return
        # While an agent drives, the page's mouse and keyboard don't reach the phone
        # (mixing in input would confuse the agent); the user takes over first.
        if t == "blur" or not (self.playing and self.mirror.live()) or self.mirror.agent() == "driving":
            self.pointer.release()
            self.keys.release_all()
            return
        if t == "move":
            self.pointer.hover(msg["x"], msg["y"])
        elif t == "down":
            self.pointer.press(msg["x"], msg["y"])
        elif t == "up":
            self.pointer.release()
        elif t == "scroll":  # fractions of the screen to drag the content by
            self.pointer.hover(msg["x"], msg["y"])
            self.pointer.scroll(msg["dx"] * SCREEN_PT[0], msg["dy"] * SCREEN_PT[1])
        elif t == "mod":
            self.keys.modifier(int(msg["bit"]), bool(msg["down"]))
        elif t == "key":
            self.keys.key(int(msg["usage"]), bool(msg["down"]))
        elif t == "consumer" and msg.get("key") in ("siri", "home", "search", "eject"):
            self.keys.consumer(msg["key"], bool(msg["down"]))

    def close(self):
        try:
            self.pointer.close()
            self.keys.release_all()
        except Exception as e:
            log("closing tab:", e)


AWAKE_FILE = os.path.join(os.path.dirname(config_path()), "keep-awake")  # exists while "keep the screen awake" is on
INPUT_TRACE = os.path.join(os.environ.get("XDG_RUNTIME_DIR", "/tmp"), "phoneuse-input.jsonl")


def same_origin(req):
    """Requests from the page itself, not from other websites (which could send
    the browser here): their Origin must be this server."""
    return req.headers.get("Origin") in (f"http://localhost:{PORT}", f"http://127.0.0.1:{PORT}")


class UnlockRequests:
    """`phoneuse unlock` asks; the owner answers on the page or in a desktop
    notification; only then is the saved passcode typed (unlock.py)."""

    def __init__(self, mirror):
        self.mirror = mirror
        self.pending = None  # {"id", "event", "approved", "notice", "since"}
        self.companion = None

    def broadcast(self, msg):
        for tab in list(self.mirror.tabs):
            asyncio.ensure_future(tab.ws.send_str(json.dumps(msg)))

    async def ask(self, req):
        if not unlock.ENABLED:
            return web.json_response({"result": unlock.OFF}, status=409)
        if self.pending:
            return web.json_response({"result": "another unlock request is waiting for the owner"}, status=409)
        if not await asyncio.get_running_loop().run_in_executor(None, unlock.saved):
            return web.json_response({"result": "no passcode saved: the owner can save one in the browser "
                                                "mirror's settings, or unlock the phone themselves"}, status=409)
        rid = secrets.token_hex(8)
        p = self.pending = {"id": rid, "event": asyncio.Event(), "approved": False, "notice": None,
                            "since": time.time()}
        self.broadcast({"type": "unlock-request", "id": rid})
        if self.companion:  # the companion app's notification, with Unlock and Deny
            asyncio.get_running_loop().run_in_executor(None, self.companion.push_unlock, rid)
        asyncio.ensure_future(self._notify(p))
        log("unlock requested; waiting for the owner")
        try:
            await asyncio.wait_for(p["event"].wait(), 120)
        except asyncio.TimeoutError:
            pass
        finally:
            self.pending = None
            self.broadcast({"type": "unlock-done"})
            if self.companion:
                asyncio.get_running_loop().run_in_executor(None, self.companion.push_request_over, "unlock", rid)
            if p["notice"] and p["notice"].returncode is None:
                p["notice"].kill()
        if not p["event"].is_set():
            return web.json_response({"result": "no answer from the owner within 2 minutes"})
        if not p["approved"]:
            log("unlock denied")
            return web.json_response({"result": "denied by the owner"})
        result = await asyncio.get_running_loop().run_in_executor(None, self._unlock)
        log("unlock:", result)
        return web.json_response({"result": result})

    @staticmethod
    def _unlock():
        try:
            from . import cli
            return unlock.unlock(cli.screen_text)
        except BaseException as e:  # cli helpers exit on errors; never take the server down
            return f"failed: {e}"

    async def _notify(self, p):
        # A desktop notification with Unlock / Deny, where the notification daemon supports actions.
        # Windows: a notification that opens the page, where the owner answers.
        if IS_WINDOWS:
            from .windows import notify
            await asyncio.get_running_loop().run_in_executor(
                None, notify.show, "Unlock the iPhone?",
                "An agent asks to enter your saved passcode. Answer in the Phone Use window.",
                f"http://localhost:{PORT}/")
            return
        # (Where there's no notify-send, as on macOS, the app shows its own from the API.)
        if not shutil.which("notify-send"):
            return
        try:
            p["notice"] = await asyncio.create_subprocess_exec(
                "notify-send", "-a", "phoneuse", "-u", "critical", "--wait", "-A", "unlock=Unlock", "-A", "deny=Deny",
                "Unlock the iPhone?", f"Agent asks to enter your saved passcode. Answer here or at "
                f"http://localhost:{PORT}", stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.DEVNULL)
            out, _ = await p["notice"].communicate()
            answer = out.decode().strip()
            if answer in ("unlock", "deny") and self.pending is p:
                p["approved"] = answer == "unlock"
                p["event"].set()
        except (OSError, asyncio.CancelledError):
            pass

    async def answer(self, req):
        if not same_origin(req):
            return web.Response(status=403)
        body = await req.json()
        p = self.pending
        if not p or body.get("id") != p["id"]:
            return web.json_response({"ok": False})
        p["approved"] = bool(body.get("approve"))
        p["event"].set()
        return web.json_response({"ok": True})


class SessionRequests:
    """The owner's side of agent access ("Agent access": Always / After a notice / Ask
    every time). The state lives in the agent session (agent_session.py); this turns its
    changes into what the owner and the agent see: the browser mirror's prompt, a
    desktop notification, the companion app's notification, the HID daemon's gate,
    and the agent's answer."""

    def __init__(self, mirror):
        self.mirror = mirror
        self.session = mirror.session
        self.companion = None
        self.notices = {}  # request id -> its desktop notification process
        self._waiters = {}  # request id -> agents waiting on it
        self.session.listeners.append(self._on_change)

    @property
    def pending(self):
        """The waiting request: {"id", "action", "mode", "since", "deadline"}, or None."""
        return self.session.request if self.session.current() == "requested" else None

    def needed(self):
        """Whether an agent's action must wait for the owner first."""
        return start_setting()["mode"] != "auto" and self.session.current() not in ("driving", "paused")

    def acted(self):
        self.sync_gate()

    def _on_change(self, session, old, why):
        loop = self.mirror.loop
        if old == "requested" and session.state != "requested":
            rid = next(reversed(session.outcome))  # the request that just ended
            log(f"agent session request over: {session.outcome[rid]} ({why})")
            if loop:
                loop.call_soon_threadsafe(self._request_over, rid)
        if session.state == "requested" and old != "requested" and loop:
            loop.call_soon_threadsafe(self._request_started, dict(session.request))
        threading.Thread(target=self.sync_gate, daemon=True).start()

    def _request_started(self, r):
        self._broadcast_request(r)
        phone = bool(self.companion and self.companion.has_devices())
        if phone:
            asyncio.get_running_loop().run_in_executor(None, self.companion.push_session, r["id"], r["action"])
        asyncio.ensure_future(self._notify(r))
        if r["mode"] != "countdown":
            log("an agent wants the phone; waiting for the owner")
        elif phone:
            log(f"an agent wants the phone; its {r['seconds']} s countdown starts once the phone has the request")
        else:
            self.countdown_started(r["id"])

    def _broadcast_request(self, r):
        self.broadcast({"type": "session-request", "id": r["id"], "action": r["action"], "mode": r["mode"],
                        "deadline": r["deadline"]})

    def countdown_started(self, rid, at=None):
        """The owner has been told about request `rid` (the phone took the push at `at`, or
        there is no phone): its countdown runs from then. Safe from any thread."""
        if not self.session.start_countdown(rid, at):
            return
        r = dict(self.session.request)
        log(f"the owner has the request; the agent takes control in {round(r['deadline'] - time.time())} s "
            "unless the owner cancels")
        if self.mirror.loop:
            self.mirror.loop.call_soon_threadsafe(self._broadcast_request, r)

    def _request_over(self, rid):
        self.broadcast({"type": "session-done"})
        notice = self.notices.pop(rid, None)
        if notice and notice.returncode is None:
            notice.kill()
        if self.companion:
            asyncio.get_running_loop().run_in_executor(None, self.companion.push_request_over, "session", rid)

    # The HID daemon enforces access (its agent gate): agents' input is refused there
    # unless the session says it may pass. Its own connection, so a slow input
    # command on the shared one never delays the gate.
    _gate_hid = None
    _gate_sent = None

    def gate_value(self):
        if not paired():  # agents need the iPhone app, where the owner approves and stops them
            return "closed"
        return self.session.gate(start_setting()["mode"])

    def sync_gate(self, force=False):
        v = self.gate_value()
        if v == self._gate_sent and not force:
            return
        try:
            if self._gate_hid is None:
                self._gate_hid = Hid()
            self._gate_hid.cmd("gate " + v)
            self._gate_sent = v
        except Exception as e:
            self._gate_hid = None
            log("couldn't set the agent gate in the HID daemon:", e)

    async def keep_gate(self):
        """Resend the gate now and then: the daemon starts closed after a restart."""
        while True:
            await asyncio.sleep(2.0)
            await asyncio.get_running_loop().run_in_executor(None, self.sync_gate, True)

    def broadcast(self, msg):
        for tab in list(self.mirror.tabs):
            asyncio.ensure_future(tab.ws.send_str(json.dumps(msg)))

    async def ask(self, action):
        """True once the session may start; else the reason it can't. Concurrent first
        actions share one request. If every waiting agent gives up, the request is
        cancelled everywhere."""
        st = start_setting()
        r = self.session.ask(action, st["mode"], st["seconds"])
        rid = r["id"]
        self._waiters[rid] = self._waiters.get(rid, 0) + 1
        try:
            while rid not in self.session.outcome:
                await asyncio.sleep(0.1)
                self.session.tick()  # a countdown ending, or no answer
        finally:
            self._waiters[rid] -= 1
            if self._waiters[rid] == 0:
                del self._waiters[rid]
                if rid not in self.session.outcome:
                    self.session.cancel("the agent stopped waiting")
        outcome = self.session.outcome[rid]
        if outcome == "allowed":
            await asyncio.get_running_loop().run_in_executor(None, self.sync_gate)  # open before the agent hears yes
            return True
        if outcome == "declined":
            return "The owner declined the agent session. Stop; don't retry unless the user asks you to."
        if outcome == "timed out":
            return "The owner didn't allow the agent session within 2 minutes. Stop and tell the user."
        return "The agent session request was cancelled. Stop and tell the user."

    def answer_id(self, rid, approve):
        return self.session.answer(rid, bool(approve))


    async def answer(self, req):
        if not same_origin(req):
            return web.Response(status=403)
        body = await req.json()
        return web.json_response({"ok": self.answer_id(body.get("id"), body.get("approve"))})

    async def setting(self, req):
        # The browser mirror's settings: Agent control ({"mode", "seconds"}).
        if req.method == "POST":
            if not same_origin(req):
                return web.Response(status=403)
            b = await req.json()
            set_start_setting(b.get("mode"), b.get("seconds"))
            await asyncio.get_running_loop().run_in_executor(None, self.sync_gate)
        st = start_setting()
        return web.json_response({"on": st["mode"] != "auto", **st})

    async def _notify(self, p):
        if not shutil.which("notify-send"):  # macOS: the app shows its own notification from the API
            return
        try:
            self.notices[p["id"]] = notice = await asyncio.create_subprocess_exec(
                *(["notify-send", "-a", "phoneuse", "-u", "critical", "--wait", "-A", "deny=Cancel",
                   "-A", "allow=Allow now", f"Agent taking control in {p['seconds']} s",
                   f"{p['action'] or 'Using the phone'}. Cancel here or at http://localhost:{PORT}"]
                  if p["mode"] == "countdown" else
                  ["notify-send", "-a", "phoneuse", "-u", "critical", "--wait", "-A", "allow=Allow", "-A", "deny=Deny",
                   "Agent requests control",
                   f"{p['action'] or 'Using the phone'}. Answer here or at http://localhost:{PORT}"]),
                stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.DEVNULL)
            out, _ = await notice.communicate()
            choice = out.decode().strip()
            if choice in ("allow", "deny"):
                self.answer_id(p["id"], choice == "allow")
        except OSError:
            pass


def app(mirror):
    unlocks = mirror.unlocks = UnlockRequests(mirror)
    sessions = mirror.sessions = SessionRequests(mirror)

    async def page(_req):
        return web.FileResponse(PAGE, headers={"Cache-Control": "no-cache"})

    async def passcode(req):
        # The page's settings panel: whether a passcode is saved, save one, or remove
        # it. The passcode itself is never sent back.
        loop = asyncio.get_running_loop()
        if req.method != "GET" and not same_origin(req):
            return web.Response(status=403)
        if not unlock.ENABLED:
            return web.json_response({"saved": False, "enabled": False, "error": unlock.OFF},
                                     status=200 if req.method == "GET" else 410)
        try:
            if req.method == "POST":
                await loop.run_in_executor(None, unlock.save, (await req.json()).get("code", ""))
            elif req.method == "DELETE":
                await loop.run_in_executor(None, unlock.forget)
        except (ValueError, OSError) as e:
            return web.json_response({"error": str(e)}, status=400)
        return web.json_response({"saved": await loop.run_in_executor(None, unlock.saved), "enabled": True})

    async def awake(req):
        # Keep the screen awake: the page's setting and `phoneuse awake on|off`.
        # Other websites can't switch it (a browser always sends their Origin).
        if req.headers.get("Origin") and not same_origin(req):
            return web.Response(status=403)
        if req.method == "POST":
            mirror.set_awake(bool((await req.json()).get("on")))
        return web.json_response({"on": mirror.awake})

    async def ws(req):
        sock = web.WebSocketResponse(max_msg_size=1 << 16, heartbeat=20)
        await sock.prepare(req)
        tab = Tab(mirror, sock)
        mirror.tabs.add(tab)
        tab.state()
        mirror.stream.keyframe()
        pump = asyncio.ensure_future(tab.pump())
        log("tab joined;", len(mirror.tabs), "open")
        try:
            async for m in sock:
                if m.type == web.WSMsgType.TEXT:
                    try:
                        tab.handle(json.loads(m.data))
                    except Exception as e:
                        input_error("input error:", e)
                        mirror.hid = None
        finally:
            pump.cancel()
            mirror.tabs.discard(tab)
            tab.close()
            log("tab left;", len(mirror.tabs), "open")
        return sock

    async def agent_act(req):
        # The CLI and MCP server call this before every agent action.
        try:
            action = str((await req.json()).get("action", ""))[:80]
        except ValueError:
            action = ""
        if not paired():
            return web.json_response({"ok": False, "result": NOT_PAIRED}, status=409)
        if mirror.sessions.needed():
            allowed = await mirror.sessions.ask(action)
            if allowed is not True:
                return web.json_response({"ok": False, "result": allowed}, status=409)
        if mirror.agent_acts(action):
            mirror.sessions.acted()
            return web.json_response({"ok": True})
        return web.json_response({"ok": False, "result": "The user took over the phone. Stop and wait; "
                                  "continue only when they tell you to (they resume you on the page)."},
                                 status=409)

    async def agent_phase(req):
        # The CLI and MCP server: "reading" while the agent reads the screen, "thinking" once an
        # action returns (its model works out the next step).
        try:
            phase = (await req.json()).get("phase")
        except ValueError:
            phase = None
        mirror.session.set_phase(phase)
        return web.json_response({"ok": True})

    async def agent_cursor(req):
        # Where the agent points or touches, for the page's glowing dot.
        b = await req.json()
        msg = json.dumps({"type": "agent-cursor", "x": float(b["x"]), "y": float(b["y"]), "down": bool(b.get("down"))})
        for tab in list(mirror.tabs):
            asyncio.ensure_future(tab.ws.send_str(msg))
        return web.json_response({"ok": True})

    async def agent_done(_req):
        # The agent's session is over: control goes back to the page at once.
        mirror.session.done()
        return web.json_response({"ok": True})

    async def agent_takeover(req):
        if not same_origin(req):
            return web.Response(status=403)
        mirror.take_over(bool((await req.json()).get("take")))
        return web.json_response({"agent": mirror.agent()})

    a = web.Application()
    a.router.add_get("/", page)
    a.router.add_get("/ws", ws)
    a.router.add_route("*", "/api/passcode", passcode)
    a.router.add_post("/api/unlock", unlocks.ask)
    async def quit_services(req):
        # The app's Quit, when the services it shows aren't its own child (another launch
        # started them): stop `phoneuse serve`, this process's parent, which stops the rest.
        if not same_origin(req):
            return web.Response(status=403)
        import signal
        parent = os.getppid()
        with open(f"/proc/{parent}/cmdline", "rb") as f:
            if b"serve" not in f.read().split(b"\0"):
                return web.json_response({"ok": False, "error": "not started by phoneuse serve"}, status=409)
        asyncio.get_running_loop().call_later(0.2, os.kill, parent, signal.SIGTERM)
        return web.json_response({"ok": True})

    for icon in ("favicon-32.png", "favicon-64.png", "apple-touch-icon.png"):  # scripts/make-icons.py
        a.router.add_get("/" + icon, lambda _r, f=icon: web.FileResponse(os.path.join(os.path.dirname(PAGE), f)))
    a.router.add_post("/api/quit", quit_services)
    a.router.add_post("/api/agent/act", agent_act)
    a.router.add_post("/api/agent/phase", agent_phase)
    def picture_state():
        return {"live": mirror.live(), "stale": mirror.stale, "paused": mirror.taken_over,
                "frame_age": round(time.time() - mirror.last_frame_at, 1) if mirror.last_frame_at else None}
    a.router.add_get("/api/picture", lambda _r: web.json_response(picture_state()))
    a.router.add_post("/api/agent/takeover", agent_takeover)
    a.router.add_post("/api/agent/cursor", agent_cursor)
    a.router.add_post("/api/agent/done", agent_done)
    a.router.add_route("*", "/api/awake", awake)

    async def input_state(req):
        # This PC's Bluetooth as a second way in (bluetooth_setup.py); the app's Bridge tab.
        from . import bluetooth_setup
        loop = asyncio.get_running_loop()
        if req.method == "POST":
            if not same_origin(req):
                return web.Response(status=403)
            try:  # the daemon reads the setting when it starts; `phoneuse serve` restarts it
                await loop.run_in_executor(None, mirror.get_hid().cmd, "restart")
            except (OSError, RuntimeError) as e:
                return web.json_response({"error": str(e)}, status=500)
        return web.json_response({"pc_bluetooth": bluetooth_setup.on(),
                                  "prepared": await loop.run_in_executor(None, bluetooth_setup.prepared)})
    a.router.add_route("*", "/api/input", input_state)
    a.router.add_post("/api/input/restart", input_state)
    from .bridge_web import BridgeRoutes
    bridges = BridgeRoutes(same_origin)
    bridges.add(a)
    a.router.add_post("/api/unlock/answer", unlocks.answer)
    a.router.add_post("/api/session/answer", sessions.answer)
    a.router.add_route("*", "/api/session/ask", sessions.setting)

    async def companion_pair(req):
        # `phoneuse app pair`: a one-time link that pairs the companion app.
        if req.headers.get("Origin") and not same_origin(req):
            return web.Response(status=403)
        from . import companion
        return web.json_response({"link": await asyncio.get_running_loop().run_in_executor(None, companion.new_pairing)})
    a.router.add_post("/api/companion/pair", companion_pair)

    async def app_state(req):
        # Everything a control window shows, in one call: the next step (overview.hero) and
        # the settings. Read-only; each setting still changes through its own route.
        if req.headers.get("Origin") and not same_origin(req):
            return web.Response(status=403)
        from . import claude_setup, companion, netcheck, overview

        async def get(handler):
            try:
                return json.loads((await handler(req)).text)
            except Exception:  # noqa: BLE001 - one part failing leaves the rest
                return None

        def host():
            try:
                paired = len(companion.load().get("devices") or {})
            except Exception:  # noqa: BLE001
                paired = 0
            from . import bluetooth_setup
            return {"claude": claude_setup.state(), "firewall": netcheck.detect(),
                    "confirmed": netcheck.confirmed(), "paired": paired, "pc_bluetooth": bluetooth_setup.on()}
        out = {"running": True, "picture": picture_state(), "name": NAME}
        out["bridge"], out["session"], out["awake"], out["passcode"], h = await asyncio.gather(
            get(bridges.state), get(sessions.setting), get(awake), get(passcode),
            asyncio.get_running_loop().run_in_executor(None, host))
        out.update(h)
        if out["picture"]["live"]:
            netcheck.remember(mirrored=True)  # the iPhone reached this PC: the ports are open
            out["confirmed"] = True
        out["hero"] = overview.hero(out, NAME)
        # What waits for the owner, for shells that notify natively (the Mac app); each is answered through
        # /api/session/answer or /api/unlock/answer with its id
        sp, up = mirror.sessions.pending, mirror.unlocks.pending
        out["requests"] = {
            "session": {k: sp.get(k) for k in ("id", "action", "mode", "deadline")} if sp else None,
            "unlock": {"id": up["id"]} if up else None}
        return web.json_response(out)
    a.router.add_get("/api/app", app_state)
    return a


def main():
    unlock.purge()  # unlocking is off: a passcode saved earlier doesn't stay on disk
    if IS_WINDOWS:
        from .windows import precise_timers
        precise_timers()
    else:
        import gi
        gi.require_version("Gst", "1.0")
        from gi.repository import GLib, Gst
        Gst.init(None)
        # GStreamer's bus signals need a GLib main loop; run one beside asyncio.
        threading.Thread(target=GLib.MainLoop().run, daemon=True).start()
    mirror = Mirror()

    async def start():
        mirror.loop = asyncio.get_running_loop()
        runner = web.AppRunner(app(mirror), access_log=None)
        await runner.setup()
        await web.TCPSite(runner, "127.0.0.1", PORT).start()
        log(f"browser mirror on http://localhost:{PORT}")
        asyncio.ensure_future(mirror.keep_awake())
        asyncio.ensure_future(mirror.sessions.keep_gate())

        async def stale_loop():
            while True:
                await asyncio.sleep(0.5)
                mirror.session.tick()  # the session running out, a countdown ending
                mirror.check_stale()
        asyncio.ensure_future(stale_loop())
        try:  # the companion app's API on the local network (companion.py)
            from .companion import Companion
            c = mirror.unlocks.companion = mirror.sessions.companion = Companion(mirror, mirror.unlocks)
            await c.start()
        except OSError as e:
            log("companion API off:", e)
        await mirror.watch()

    try:
        asyncio.run(start())
    except OSError as e:
        log("can't serve:", e)
        sys.exit(1)


if __name__ == "__main__":
    main()
