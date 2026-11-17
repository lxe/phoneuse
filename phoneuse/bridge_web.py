"""The browser mirror's routes for the Bluetooth bridge's setup (Settings > Bluetooth
bridge). The page walks through: find the bridge over BLE, compare the code on its
screen, pick a Wi-Fi network it sees, type that network's password, done. The BLE
conversation lives here between the page's requests.

A bridge another PC already set up answers on the network: GET /api/bridge lists it
under "nearby" while this PC has none, and POST /api/bridge/adopt asks it to let this
PC in (the person presses either of its buttons when its screen shows the same code)."""
import asyncio
import threading
import time

from aiohttp import web

from . import bridge
from .hidclient import Hid


def _daemon(cmd):
    try:
        return Hid().cmd(cmd)
    except Exception as e:
        return f"err {e}"


def use_bridge(cfg):
    """Tell the HID daemon to send input through the bridge (or not)."""
    if cfg and cfg.get("use", True):
        return _daemon(f"bridge {cfg['ip']} {cfg['key']}")
    return _daemon("bridge off")


class BridgeRoutes:
    def __init__(self, same_origin):
        self.same_origin = same_origin
        self.session = None  # bridge.Setup while a setup runs
        self.bridge_id = None
        self.lock = asyncio.Lock()
        self.adoption = None  # bridge.Adoption while this PC asks a bridge to let it in
        self._nearby, self._nearby_at, self._looking = [], 0.0, False
        # Flashing over USB (POST /api/bridge/firmware/install), for apps that aren't this
        # Python process (the Windows window; app.py calls bridge.install_firmware itself)
        self.flashing = {"busy": False, "step": None, "progress": 0.0, "error": None, "done": False}

    def nearby(self):
        """Bridges on the network that this PC doesn't use, looked up at most every 4 s in
        the background (a lookup can take a second): the last answer at once."""
        if time.time() - self._nearby_at > 4 and not self._looking:
            self._looking = True

            def look():
                try:
                    self._nearby = bridge.nearby(0.8)
                except OSError:
                    self._nearby = []
                self._nearby_at, self._looking = time.time(), False
            threading.Thread(target=look, daemon=True).start()
        return self._nearby

    async def _close(self):
        if self.session:
            s, self.session = self.session, None
            await s.__aexit__(None, None, None)

    def _guard(self, req):
        if not self.same_origin(req):
            raise web.HTTPForbidden()

    async def state(self, req):
        cfg = bridge.saved()
        if req.method == "DELETE":
            self._guard(req)
            loop = asyncio.get_running_loop()
            await loop.run_in_executor(None, _daemon, "bridge off")
            why = await loop.run_in_executor(None, bridge.forget)
            return web.json_response({"saved": None, "reset": why is None, "why": why})
        out = {"saved": None, "setup": bool(self.session)}
        if not cfg:
            out["nearby"] = [{k: b.get(k) for k in ("id", "ip", "fw", "ssid", "phone", "connected", "hosts")}
                             for b in self.nearby()]
        if cfg:
            out["saved"] = {"id": cfg["id"], "ip": cfg.get("ip") or "", "ssid": cfg.get("ssid"), "use": cfg.get("use", True)}

            def ask():
                try:
                    c = bridge.resolve(cfg)
                    if not c:
                        return {"error": "not answering on this network"}
                    if c["ip"] != cfg.get("ip"):
                        use_bridge(c)  # it moved: tell the daemon the new address
                    return bridge.Link(c).status()
                except Exception as e:
                    return {"error": str(e)}
            out["status"] = await asyncio.get_running_loop().run_in_executor(None, ask)
            from . import phones
            # the phone that screen mirrors is the target; a bridge holding another phone sends nothing to it
            out["mirroring_phone"] = phones.current()
            st = out["status"]
            out["other_phone"] = (phones.other_phone(st.get("phone"), out["mirroring_phone"])
                                  if st.get("connected") else None)
            out["daemon"] = await asyncio.get_running_loop().run_in_executor(None, _daemon, "status")
        return web.json_response(out)

    async def find(self, req):
        self._guard(req)
        async with self.lock:
            await self._close()
            try:
                found = await bridge.scan_ble(5)
            except Exception as e:
                return web.json_response({"error": f"Bluetooth on this PC: {e}"})
        near = await asyncio.get_running_loop().run_in_executor(None, bridge.nearby, 1.0)
        return web.json_response({"bridges": [{"address": a, "name": n, "rssi": r} for a, n, r in found],
                                  "nearby": [{k: b.get(k) for k in ("id", "ip", "fw", "ssid", "phone", "hosts")}
                                             for b in near]})

    async def pair(self, req):
        self._guard(req)
        address = (await req.json())["address"]
        async with self.lock:
            await self._close()
            s = bridge.Setup(address)
            try:
                await s.__aenter__()
                info = await s.info()
                code = await s.exchange()
            except Exception as e:
                await s.__aexit__(None, None, None)
                return web.json_response({"error": f"couldn't talk to the bridge: {e}"})
            self.session, self.bridge_id = s, info.get("id")
        return web.json_response({"code": f"{code // 1000:03d} {code % 1000:03d}", "id": self.bridge_id})

    async def networks(self, req):
        self._guard(req)
        if not self.session:  # a set-up bridge: ask it over the network
            cfg = bridge.saved()
            if not cfg:
                return web.json_response({"error": "setup ended; start again"})
            try:
                nets = await asyncio.get_running_loop().run_in_executor(
                    None, lambda: bridge.Link(bridge.resolve(cfg) or cfg).networks())
            except Exception as e:
                return web.json_response({"error": f"the bridge didn't answer: {e}"})
            return web.json_response({"networks": nets})
        async with self.lock:
            try:
                nets = await self.session.networks()
            except Exception as e:
                await self._close()
                return web.json_response({"error": f"the bridge stopped answering: {e}"})
        return web.json_response({"networks": nets})

    async def join(self, req):
        self._guard(req)
        body = await req.json()
        ssid, password = body["ssid"], body.get("password", "")
        if not self.session:
            return await self.change_network(ssid, password)
        loop = asyncio.get_running_loop()
        async with self.lock:
            s = self.session
            # Saved before the answer: the bridge keeps this key once it is on the
            # network, so the PC must never lose it (the address can be found later).
            previous = bridge.saved()
            cfg = {"id": self.bridge_id, "ip": None, "key": s.keys["control"].hex(), "ssid": ssid, "use": True,
                   "set_up": time.strftime("%Y-%m-%d %H:%M")}
            bridge.save(cfg)
            try:
                info = await s.join(ssid, password)
            except Exception as e:
                info = {"err": str(e)}
            if info.get("state") == "failed":
                bridge.save(previous) if previous else bridge.forget(reset=False)  # it kept its old key
                return web.json_response({"error": f"couldn't join {ssid}: {info.get('err')}", "retry": True})
            await self._close()
        cfg["ip"] = info.get("ip")
        bridge.save(cfg)
        found = None
        for _ in range(5):
            found = await loop.run_in_executor(None, bridge.resolve, cfg)
            if found:
                break
            await asyncio.sleep(1)
        if not found:
            return web.json_response({"error": "The bridge was set up but this PC can't reach it yet. Is this PC "
                                               "on the same network? phoneuse keeps looking for it."})
        cfg = found
        ip = cfg["ip"]
        answer = await loop.run_in_executor(None, use_bridge, cfg)
        return web.json_response({"ok": True, "ip": ip, "ssid": ssid, "daemon": answer})

    async def change_network(self, ssid, password):
        """A set-up bridge moves to another network over the control link; it goes
        back to the old one if the new one fails."""
        loop = asyncio.get_running_loop()
        cfg = bridge.resolve() or bridge.saved()
        if not cfg:
            return web.json_response({"error": "no bridge set up"})
        old = cfg.get("ssid")
        try:
            await loop.run_in_executor(None, lambda: bridge.Link(cfg).change_network(ssid, password))
        except Exception as e:
            return web.json_response({"error": f"the bridge didn't answer: {e}"})
        await asyncio.sleep(4)  # it leaves the old network and tries the new one
        deadline = time.time() + 30
        while time.time() < deadline:
            found = await loop.run_in_executor(None, bridge.resolve, dict(cfg, ip=None))
            if found:
                try:
                    st = await loop.run_in_executor(None, lambda: bridge.Link(found).status())
                except Exception:
                    st = {}
                if st.get("online") and st.get("ssid") == ssid:
                    found["ssid"] = ssid
                    bridge.save(found)
                    await loop.run_in_executor(None, use_bridge, found)
                    return web.json_response({"ok": True, "ip": found["ip"], "ssid": ssid})
                if st.get("online") and st.get("err"):
                    await loop.run_in_executor(None, use_bridge, found)
                    return web.json_response({"error": f"{st['err']}; it's back on {old}", "retry": True})
            await asyncio.sleep(1.5)
        return web.json_response({"error": f"The bridge left {old} and this PC can't find it. If {ssid} is "
                                           "another network, this PC must be on it too."})

    async def adopt(self, req):
        """Ask a bridge on the network to let this PC use it: {"id"} -> {"code"}."""
        self._guard(req)
        want = (await req.json())["id"]
        loop = asyncio.get_running_loop()
        found = [b for b in await loop.run_in_executor(None, bridge.nearby, 1.0) if b["id"] == want]
        if not found:
            return web.json_response({"error": "That bridge doesn't answer on this network any more."})
        a = bridge.Adoption(found[0])
        try:
            code = await loop.run_in_executor(None, a.start)
        except Exception as e:
            return web.json_response({"error": str(e)})
        self.adoption = a
        return web.json_response({"code": f"{code // 1000:03d} {code % 1000:03d}", "id": want,
                                  "name": bridge.this_pc_name()})

    async def adopt_wait(self, req):
        """Up to ~20 s: {"ok": true, ...} once the bridge allowed this PC, {"waiting": true}
        while it still asks, {"error"} once it declined or stopped asking."""
        self._guard(req)
        a = self.adoption
        if not a:
            return web.json_response({"error": "Start again."})
        loop = asyncio.get_running_loop()
        try:
            cfg = await loop.run_in_executor(None, a.wait, 20)
        except Exception as e:
            self.adoption = None
            return web.json_response({"error": str(e)})
        if not cfg:
            return web.json_response({"waiting": True})
        self.adoption = None
        self._nearby_at = 0
        answer = await loop.run_in_executor(None, use_bridge, cfg)
        return web.json_response({"ok": True, "ip": cfg["ip"], "ssid": cfg.get("ssid"), "daemon": answer})

    async def cancel(self, req):
        self._guard(req)
        async with self.lock:
            await self._close()
        return web.json_response({"ok": True})

    async def use(self, req):
        self._guard(req)
        cfg = bridge.saved()
        if not cfg:
            return web.json_response({"error": "no bridge set up"})
        cfg["use"] = bool((await req.json()).get("on"))
        bridge.save(cfg)
        answer = await asyncio.get_running_loop().run_in_executor(None, use_bridge, cfg)
        return web.json_response({"use": cfg["use"], "daemon": answer})

    async def disconnect(self, req):
        """Drop the bridge's phone; the bridge pages it again only once mirroring stops and
        starts again (firmware 0.4.2+)."""
        self._guard(req)
        cfg = bridge.saved()
        try:
            await asyncio.get_running_loop().run_in_executor(None, lambda: bridge.Link(bridge.resolve(cfg) or cfg).drop())
        except Exception as e:
            return web.json_response({"error": f"the bridge didn't answer: {e}"})
        return web.json_response({"ok": True})

    async def change(self, req):
        """Set the bridge up again over BLE (it keeps its network until a new one
        works). Changing networks doesn't need this: see change_network."""
        self._guard(req)
        cfg = bridge.saved()
        try:
            await asyncio.get_running_loop().run_in_executor(None, lambda: bridge.Link(cfg).open_setup())
        except Exception as e:
            return web.json_response({"error": f"the bridge didn't answer: {e}. Hold its lower button for 3 seconds, then try again."})
        return web.json_response({"ok": True})

    async def firmware(self, req):
        """The firmware this copy installs, whether its tools are here yet ("ready": false
        means flashing downloads the firmware and esptool first), the board on USB, and the
        flash in progress: {"version", "ready", "port", "driver_missing", "busy", "step",
        "progress", "error", "done"}."""
        loop = asyncio.get_running_loop()
        found = bridge.release_firmware()
        ready = await loop.run_in_executor(None, bridge.flashing_ready)

        def board():
            try:
                port = bridge.usb_port()
            except Exception:  # noqa: BLE001 - no serial support: nothing to show
                port = None
            return port, (not port and bridge.usb_board_without_driver())
        port, missing = await loop.run_in_executor(None, board)
        return web.json_response({"version": found[1] if found else bridge.FIRMWARE_RELEASE[0], "ready": ready,
                                  "port": port, "driver_missing": missing, **self.flashing})

    async def firmware_install(self, req):
        """Write the firmware to the board on USB, erasing it (its Wi-Fi, key and iPhone
        pairing), in the background: GET /api/bridge/firmware follows it ("step" says what
        it does). The first time, the firmware and esptool are downloaded first. Once it's
        written, the bridge this PC had set up is forgotten here: its key went with the erase."""
        self._guard(req)
        if self.flashing["busy"]:
            return web.json_response({"error": "already flashing"})
        loop = asyncio.get_running_loop()
        port = await loop.run_in_executor(None, bridge.usb_port)
        if not port:
            return web.json_response({"error": "No board on USB. Plug the T-Display in with a data cable."})
        self.flashing = {"busy": True, "step": "Starting…", "progress": 0.0, "error": None, "done": False}

        def progress(frac):
            self.flashing["progress"] = frac

        def step(text):
            self.flashing["step"] = text

        def work():
            try:
                if not bridge.flashing_ready():
                    bridge.prepare_flashing(step)
                found = bridge.release_firmware()
                step(f"Flashing bridge firmware {found[1]}…" if found and found[1] else "Flashing…")
                bridge.install_firmware(port, progress)
                if bridge.saved():
                    _daemon("bridge off")
                    bridge.forget(reset=False)  # it was erased: nothing to tell it
                # Flashed: the downloaded firmware and esptool go (after a failure they stay, so
                # Try Again doesn't download them again)
                bridge.remove_flashing_tools()
                self.flashing.update(done=True, progress=1.0, step="Flashed")
            except Exception as e:  # noqa: BLE001
                self.flashing["error"] = str(e)
            finally:
                self.flashing["busy"] = False
        threading.Thread(target=work, daemon=True).start()
        return web.json_response({"ok": True, "port": port})

    def add(self, app):
        r = app.router
        r.add_get("/api/bridge/firmware", self.firmware)
        r.add_post("/api/bridge/firmware/install", self.firmware_install)
        r.add_route("*", "/api/bridge", self.state)
        r.add_post("/api/bridge/find", self.find)
        r.add_post("/api/bridge/pair", self.pair)
        r.add_post("/api/bridge/networks", self.networks)
        r.add_post("/api/bridge/join", self.join)
        r.add_post("/api/bridge/cancel", self.cancel)
        r.add_post("/api/bridge/adopt", self.adopt)
        r.add_post("/api/bridge/adopt/wait", self.adopt_wait)
        r.add_post("/api/bridge/use", self.use)
        r.add_post("/api/bridge/change", self.change)
        r.add_post("/api/bridge/disconnect", self.disconnect)
