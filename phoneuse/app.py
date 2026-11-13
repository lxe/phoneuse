"""Phone Use's control window (GTK 4, libadwaita).

The phone itself is a web page (the browser mirror, http://localhost:PORT); this window starts
and stops the services and walks through setup: the top of the window always shows the next
thing to do (open the firewall, set up the bridge, pair the iPhone with it, start Screen
Mirroring) with the button that does it, and once all is done, the iPhone and the way to it.
Below are the settings, in the iOS app's order and words: Agent, Unlock, Phone, Bridge, iPhone
App, Network. Everything goes through the browser mirror's local API, the same one the page
uses, so the two always agree.

When this window starts the services (`phoneuse serve`), closing it keeps them running for
agents; Quit stops them. Start at Login asks the desktop (the background portal) to start
Phone Use, without its window, when the user logs in. When the services were already running
(a terminal's `phoneuse serve` or `up`), the window only shows and changes their state.
"""
import json
import os
import socket
import sys
import threading
import time
import urllib.request

import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")
from gi.repository import Adw, Gio, GLib, Gtk  # noqa: E402

from . import claude_setup, overview, netcheck, screen  # noqa: E402
from .config import CFG, config_path  # noqa: E402

APP_ID = os.environ.get("FLATPAK_ID") or "co.lxe.PhoneUse"
PORT = CFG["web_port"]
BASE = f"http://127.0.0.1:{PORT}"
ORIGIN = f"http://localhost:{PORT}"  # the API takes changes only from its own origin
MODES = [("auto", "Always"), ("countdown", "After a delay"), ("ask", "Ask every time")]
DELAYS = [5, 10, 15, 30]
APP_STATE = os.path.join(os.path.dirname(config_path()), "app.json")  # {"login": bool}


def api(path, body=None, method=None, timeout=4):
    """The browser mirror's API. Returns the decoded JSON, or raises OSError."""
    data = None if body is None else json.dumps(body).encode()
    req = urllib.request.Request(BASE + path, data=data, method=method or ("GET" if body is None else "POST"),
                                 headers={"Content-Type": "application/json", "Origin": ORIGIN})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return json.loads(r.read() or b"{}")


def in_thread(work, done=None):
    """Run work() off the main loop; then done(result, error) on it."""
    def run():
        try:
            r, e = work(), None
        except Exception as ex:  # noqa: BLE001 - shown to the user
            r, e = None, ex
        if done:
            GLib.idle_add(lambda: (done(r, e), False)[1])
    threading.Thread(target=run, daemon=True).start()


def services_running():
    """Whether some phoneuse instance holds the video hub's port."""
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        s.bind(("127.0.0.1", screen.HUB_RTP_PORT))
        return False
    except OSError:
        return True
    finally:
        s.close()


def serve_command():
    if os.environ.get("FLATPAK_ID"):
        return ["phoneuse", "serve"]
    return [sys.executable, os.path.join(os.path.dirname(os.path.dirname(os.path.realpath(__file__))), "bin",
                                         "phoneuse"), "serve"]


def paired_iphones():
    try:
        from . import companion
        return len(companion.load().get("devices") or {})
    except Exception:  # noqa: BLE001
        return 0


def pc_bluetooth():
    from . import bluetooth_setup
    return bluetooth_setup.on()


def load_app_state():
    try:
        with open(APP_STATE) as f:
            return json.load(f)
    except (OSError, ValueError):
        return {}


def save_app_state(**kw):
    d = load_app_state()
    d.update(kw)
    os.makedirs(os.path.dirname(APP_STATE), exist_ok=True)
    with open(APP_STATE, "w") as f:
        json.dump(d, f)


def nav_row(title, subtitle=None, on=None):
    r = Adw.ActionRow(title=title, activatable=True)
    if subtitle:
        r.set_subtitle(subtitle)
    r.add_suffix(Gtk.Image(icon_name="go-next-symbolic"))
    if on:
        r.connect("activated", lambda *_: on())
    return r


def button(label, style=None, on=None):
    b = Gtk.Button(label=label, valign=Gtk.Align.CENTER)
    for c in (style or "").split():  # "pill suggested-action": each class on its own
        b.add_css_class(c)
    if on:
        b.connect("clicked", lambda *_: on())
    return b


def qr_texture(link):
    """A QR code for `link` (shown in a Gtk.Picture with the "qr" class: rounded corners)."""
    import io

    import qrcode
    from gi.repository import Gdk
    buf = io.BytesIO()
    qrcode.make(link, border=2, box_size=8).save(buf, format="PNG")
    return Gdk.Texture.new_from_bytes(GLib.Bytes.new(buf.getvalue()))


def confirm(parent, heading, body, action, on_yes):
    d = Adw.AlertDialog(heading=heading, body=body)
    d.add_response("cancel", "Cancel")
    d.add_response("yes", action)
    d.set_response_appearance("yes", Adw.ResponseAppearance.DESTRUCTIVE)
    d.connect("response", lambda _d, r: r == "yes" and on_yes())
    d.present(parent)


class Window(Adw.ApplicationWindow):
    def __init__(self, app):
        w, h = (int(v) for v in os.environ.get("PHONEUSE_APP_SIZE", "620x720").split("x"))  # screenshots set it
        super().__init__(application=app, title="Phone Use", default_width=w, default_height=h)
        self.app = app
        self.busy = False
        self._quiet = False
        self.toasts = Adw.ToastOverlay()
        view = Adw.ToolbarView()
        header = Adw.HeaderBar()
        menu = Gio.Menu()
        sec = Gio.Menu()
        sec.append("Open iPhone in Browser", "app.open")
        menu.append_section(None, sec)
        sec = Gio.Menu()
        sec.append("About Phone Use", "app.about")
        sec.append("Quit", "app.quit")
        menu.append_section(None, sec)
        header.pack_end(Gtk.MenuButton(icon_name="open-menu-symbolic", menu_model=menu, primary=True,
                                       tooltip_text="Main Menu"))
        view.add_top_bar(header)
        # Three tabs: the phone (what to do next, or the iPhone), agents, and the bridge. The
        # switcher sits in the header bar, or at the bottom when the window is narrow.
        self.stack = Adw.ViewStack()
        self.phone_page, self.agents_page, self.bridge_page = (Adw.PreferencesPage() for _ in range(3))
        self.tabs = {}
        for key, page, title, icon in (("phone", self.phone_page, "Phone", "phone-symbolic"),
                                       ("agents", self.agents_page, "Agents", "co.lxe.PhoneUse-agent-symbolic"),
                                       ("bridge", self.bridge_page, "Bridge", "input-keyboard-symbolic")):
            self.tabs[key] = self.stack.add_titled_with_icon(page, key, title, icon)
        switcher = Adw.ViewSwitcher(stack=self.stack, policy=Adw.ViewSwitcherPolicy.WIDE)
        header.set_title_widget(switcher)
        bar = Adw.ViewSwitcherBar(stack=self.stack)
        view.add_bottom_bar(bar)
        view.set_content(self.stack)
        narrow = Adw.Breakpoint.new(Adw.BreakpointCondition.parse("max-width: 500sp"))
        narrow.add_setter(header, "title-widget", Adw.WindowTitle(title="Phone Use"))
        narrow.add_setter(bar, "reveal", True)
        self.add_breakpoint(narrow)
        self.toasts.set_child(view)
        self.set_content(self.toasts)
        self._build()
        if os.environ.get("PHONEUSE_APP_TAB"):  # screenshots
            self.stack.set_visible_child_name(os.environ["PHONEUSE_APP_TAB"])

    # ---- layout ----
    def _build(self):
        # The next thing to do, or the iPhone.
        hero = Adw.PreferencesGroup()
        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=6, margin_top=12, margin_bottom=6)
        self.hero_icon = Gtk.Image(icon_name="phone-symbolic", pixel_size=56)
        self.hero_title = Gtk.Label(css_classes=["title-2"], wrap=True, justify=Gtk.Justification.CENTER)
        self.hero_sub = Gtk.Label(css_classes=["dim-label"], wrap=True, justify=Gtk.Justification.CENTER,
                                  max_width_chars=42, use_markup=True)
        self.hero_btn = Gtk.Button(halign=Gtk.Align.CENTER, margin_top=12, css_classes=["pill", "suggested-action"])
        self.hero_btn.connect("clicked", lambda *_: self.hero_action and self.hero_action())
        self.hero_action = None
        # Numbered steps (overview's "steps"), under the title.
        self.hero_steps = Gtk.Grid(halign=Gtk.Align.CENTER, row_spacing=8, column_spacing=10, margin_top=10, margin_bottom=6,
                                   visible=False)
        self._steps_shown = None
        # The pairing QR code (overview's "qr"), made here and renewed before its link expires.
        self.hero_qr = Gtk.Picture(can_shrink=True, content_fit=Gtk.ContentFit.CONTAIN,
                                   overflow=Gtk.Overflow.HIDDEN, css_classes=["qr"])
        self.hero_qr_clamp = Adw.Clamp(maximum_size=220, child=self.hero_qr, margin_top=10, margin_bottom=4,
                                       visible=False)
        self._qr_made = 0.0
        for w in (self.hero_icon, self.hero_title, self.hero_qr_clamp, self.hero_steps, self.hero_sub, self.hero_btn):
            box.append(w)
        hero.add(box)
        self.phone_page.add(hero)

        # Agent
        g = Adw.PreferencesGroup()  # the tab names it
        self.mode_row = Adw.ComboRow(title="Agent control", subtitle="What happens when an agent wants the phone",
                                     model=Gtk.StringList.new([m[1] for m in MODES]))
        self.mode_row.connect("notify::selected", self._mode_changed)
        self.delay_row = Adw.ComboRow(title="Delay", subtitle="You can stop the agent until then",
                                      model=Gtk.StringList.new([f"{d} seconds" for d in DELAYS]))
        self.delay_row.connect("notify::selected", self._mode_changed)
        self.claude_row = Adw.ActionRow(title="Claude Code")
        self.claude_btn = button("Connect", on=self._claude_clicked)
        self.claude_row.add_suffix(self.claude_btn)
        for r in (self.mode_row, self.delay_row, self.claude_row):
            g.add(r)
        self.agents_page.add(g)

        # Unlock
        g = Adw.PreferencesGroup(title="Unlock",
                                 description="Phone Use types the passcode only after you allow each Unlock.")
        self.pass_row = Adw.ActionRow(title="Passcode")
        self.pass_state = Gtk.Label(css_classes=["dim-label"], valign=Gtk.Align.CENTER)
        self.pass_row.add_suffix(self.pass_state)
        self.pass_entry = Adw.PasswordEntryRow(title="New Passcode", show_apply_button=True)
        self.pass_entry.connect("apply", self._passcode_apply)
        # rare and destructive: a quiet button in the group's header, not a row among the settings
        self.pass_forget = button("Forget…", "flat", lambda: confirm(
            self, "Forget the passcode?", "Unlock stops working until you save it again.", "Forget",
            lambda: in_thread(lambda: api("/api/passcode", method="DELETE"),
                              lambda *_: (self.toast("Passcode forgotten"), self.refresh()))))
        self.pass_forget.add_css_class("error")
        self.pass_forget.set_tooltip_text("Forget the passcode")
        g.set_header_suffix(self.pass_forget)
        for r in (self.pass_row, self.pass_entry):
            g.add(r)
        self.unlock_group = g
        from . import unlock
        if unlock.ENABLED:  # unlocking with a saved passcode is turned off for now (unlock.py)
            self.agents_page.add(g)

        # Phone (the tab names it)
        g = Adw.PreferencesGroup()
        self.awake_row = Adw.SwitchRow(title="Keep Awake", subtitle="The screen stays on while the iPhone mirrors")
        self.awake_row.connect("notify::active", self._awake_changed)
        self.login_row = Adw.SwitchRow(title="Start at Login", subtitle="Phone Use runs in the background, so "
                                                                         "agents can use the phone")
        self.login_row.set_active(bool(load_app_state().get("login")))
        self.login_row.connect("notify::active", self._login_changed)
        g.add(self.awake_row)
        g.add(self.login_row)
        self.phone_page.add(g)

        # This PC's Bluetooth first: it needs no bridge
        g = Adw.PreferencesGroup(title="This PC's Bluetooth")
        self.pcbt_row = Adw.SwitchRow(title="Use This PC's Bluetooth", subtitle="Works with or without the bridge")
        self._pcbt_quiet = False  # set while the app moves the switch itself
        self.pcbt_row.connect("notify::active", self._pcbt_toggled)
        g.add(self.pcbt_row)
        self.bridge_page.add(g)

        # Bridge
        self.bridge_group = Adw.PreferencesGroup(title="Bridge")
        self.bridge_rows = []
        self.bridge_page.add(self.bridge_group)

        # iPhone App
        g = Adw.PreferencesGroup(title="iPhone")
        self.paired_row = Adw.ActionRow(title="Paired")
        self.paired_count = Gtk.Label(css_classes=["dim-label"], valign=Gtk.Align.CENTER)
        self.paired_row.add_suffix(self.paired_count)
        self.paired_row.add_suffix(button("Unpair", "flat", self._confirm_unpair))
        g.add(self.paired_row)
        self.pair_row = nav_row("Pair iPhone", "Allow and stop agents from the iPhone, and see what they do",
                                lambda: PairDialog(self).present(self))
        g.add(self.pair_row)
        self.phone_page.add(g)

        # Network: only until the ports are known to be open
        self.net_group = Adw.PreferencesGroup(title="Network", visible=False)
        self.fw_row = Adw.ActionRow(title="Firewall")
        self.fw_btn = button("Open Ports…", on=self._allow_network)
        self.fw_row.add_suffix(self.fw_btn)
        self.net_group.add(self.fw_row)
        self.bridge_page.add(self.net_group)



    # ---- refresh ----
    def refresh(self):
        if self.busy:
            return
        self.busy = True

        def work():
            if services_running():
                try:
                    return api("/api/app")
                except (OSError, ValueError):
                    pass  # starting up: the page doesn't answer yet
                return {"running": True, "picture": None, "claude": claude_setup.state(),
                        "firewall": netcheck.detect(), "confirmed": netcheck.confirmed(), "paired": paired_iphones(), "pc_bluetooth": pc_bluetooth()}
            return {"running": False, "claude": claude_setup.state(), "firewall": netcheck.detect(),
                    "confirmed": netcheck.confirmed(), "paired": paired_iphones(), "pc_bluetooth": pc_bluetooth()}
        in_thread(work, self._show)

    def _show(self, s, err):
        self.busy = False
        if err or not s:
            return
        self._show_hero(s)
        running = s["running"]
        self._quiet = True
        sess = s.get("session") or {}
        if sess:
            self.mode_row.set_selected(next((i for i, m in enumerate(MODES) if m[0] == sess.get("mode")), 0))
            secs = sess.get("seconds", 10)
            self.delay_row.set_selected(min(range(len(DELAYS)), key=lambda i: abs(DELAYS[i] - secs)))
        self.delay_row.set_visible(sess.get("mode") == "countdown")
        if s.get("awake") is not None:
            self.awake_row.set_active(bool(s["awake"].get("on")))
        self._quiet = False
        for w in (self.mode_row, self.delay_row, self.awake_row, self.unlock_group):
            w.set_sensitive(running)

        saved = bool((s.get("passcode") or {}).get("saved"))
        self.pass_state.set_label("Saved" if saved else "Not saved")
        self.pass_entry.set_title("Replace Passcode" if saved else "Save Passcode")
        self.pass_forget.set_visible(saved)

        c = s["claude"]
        ok = c["skill"] and c["command"]
        self.claude_row.set_subtitle("Claude Code can see and use the phone" if ok
                                     else "Claude Code isn't installed" if not c["claude"]
                                     else "Let Claude Code see and use the phone")
        self.claude_btn.set_label("Disconnect" if ok else "Connect")
        self.claude_btn.set_sensitive(c["claude"] or ok)

        self.paired_row.set_visible(s["paired"] > 0)
        self.paired_count.set_label(f"{s['paired']} iPhone" + ("s" if s["paired"] != 1 else ""))
        self.pair_row.set_title("Pair Another iPhone" if s["paired"] else "Pair iPhone")

        self._show_bridge(s.get("bridge"), running)
        st = (s.get("bridge") or {}).get("status") or {}
        self.tabs["bridge"].set_needs_attention(running and (not (s.get("bridge") or {}).get("saved")
                                                             or bool(st.get("error")) or not st.get("connected")))
        fw = s.get("firewall")
        self.net_group.set_visible(bool(fw) and not s["confirmed"])
        self.fw_row.set_subtitle(f"If {CFG['name']} is missing from Screen Mirroring on the iPhone, {fw} is "
                                 "blocking it. Open Ports lets the iPhone reach Phone Use on this network.")

    HERO_ICONS = {"phone": "phone-symbolic", "keyboard": "input-keyboard-symbolic",
                  "offline": "network-offline-symbolic", "bluetooth": "bluetooth-symbolic",
                  "mirroring": "co.lxe.PhoneUse-mirroring-symbolic"}
    HERO_BUTTONS = {"start": "Start Phone Use", "setup_bridge": "Set Up Bridge",
                    "pair_app": "Pair iPhone", "open_ports": "Open Ports…",
                    "retry": "Try Again", "open_phone": "Open iPhone"}

    def _show_hero(self, s):
        """The first thing left to do, with the button that does it; or the iPhone (overview.hero)."""
        h = (s.get("hero") if s["running"] else overview.stopped()) or overview.hero(s, CFG["name"])
        detail = GLib.markup_escape_text(h["detail"])
        if h.get("bold"):
            bold = GLib.markup_escape_text(h["bold"])
            detail = detail.replace(bold, f"<b>{bold}</b>", 1)
        actions = {"start": self.app.start_services, "setup_bridge": lambda: BridgeSetup(self).present(self),
                   "pair_app": lambda: PairDialog(self).present(self),
                   "open_ports": self._allow_network, "retry": self.refresh, "open_phone": self.app.open_phone}
        act = h.get("action")
        self._show_steps(h.get("steps"), h.get("bold"))
        self._show_qr(h.get("qr") == "pair")
        return self._hero(h["title"], detail, self.HERO_BUTTONS.get(act), actions.get(act),
                          icon=self.HERO_ICONS.get(h.get("icon"), "phone-symbolic"), good=h.get("good"),
                          quiet=h.get("quiet"))

    def _show_qr(self, on):
        self.hero_qr_clamp.set_visible(on)
        self.hero_icon.set_visible(not on)
        if not on:
            self._qr_made = 0.0
            return
        if time.time() - self._qr_made < 480:  # links last 10 minutes: renew after 8
            return
        self._qr_made = time.time()
        in_thread(lambda: api("/api/companion/pair", {}, timeout=10)["link"],
                  lambda link, err: err or self.hero_qr.set_paintable(qr_texture(link)))

    def _show_steps(self, steps, bold):
        self.hero_steps.set_visible(bool(steps))
        if not steps or (steps, bold) == self._steps_shown:
            return
        self._steps_shown = (steps, bold)
        while (c := self.hero_steps.get_first_child()):
            self.hero_steps.remove(c)

        def text(t):
            m = GLib.markup_escape_text(t)
            if bold:
                b = GLib.markup_escape_text(bold)
                m = m.replace(b, f"<b>{b}</b>")
            return Gtk.Label(label=m, use_markup=True, xalign=0)

        for i, step in enumerate(steps):
            self.hero_steps.attach(Gtk.Label(label=f"{i + 1}.", css_classes=["dim-label", "numeric"], xalign=1,
                                             valign=Gtk.Align.START), 0, i, 1, 1)
            line = Gtk.Box(spacing=5)
            for k, part in enumerate(step.split("{mirroring}")):
                if k:
                    line.append(Gtk.Image(icon_name="co.lxe.PhoneUse-mirroring-symbolic", pixel_size=16))
                if part.strip():
                    line.append(text(part.strip()))
            self.hero_steps.attach(line, 1, i, 1, 1)

    def _hero(self, title, sub, label, action, icon="phone-symbolic", good=False, quiet=False):
        self.hero_title.set_label(title)
        self.hero_sub.set_markup(sub)
        self.hero_sub.set_visible(bool(sub))
        self.hero_icon.set_from_icon_name(icon)
        self.hero_icon.set_css_classes(["success"] if good else ["dim-label"])
        self.hero_btn.set_visible(bool(label))
        if label:
            self.hero_btn.set_label(label)
            # quiet: a fallback, not the step itself (waiting for mirroring, the ports may be fine)
            self.hero_btn.set_css_classes(["pill"] if quiet else ["pill", "suggested-action"])
        self.hero_action = action

    def _show_bridge(self, b, running):
        for r in self.bridge_rows:
            self.bridge_group.remove(r)
        self.bridge_rows = []

        def add(r):
            self.bridge_group.add(r)
            self.bridge_rows.append(r)
        saved = (b or {}).get("saved")
        self.bridge_group.set_sensitive(running)
        self._pcbt_show(running)
        firmware = nav_row("Flash LilyGO T-Display", "Install the bridge firmware over USB",
                           lambda: FirmwareDialog(self).present(self))
        if not saved:
            # Bridges another PC set up answer on the network: one row each, to use it here too.
            for nb in (b or {}).get("nearby") or []:
                phone = nb.get("phone") or "No iPhone"
                r = Adw.ActionRow(title=f"Bridge {nb['id'][-4:]}",
                                  subtitle=f"{phone} · {nb.get('ssid') or nb.get('ip')}")
                r.add_suffix(button("Use…", None, lambda nb=nb: BridgeSetup(self, adopt=nb).present(self)))
                add(r)
            add(nav_row("Set Up Existing Bridge", None, lambda: BridgeSetup(self).present(self)))
            add(firmware)
            return
        st = b.get("status") or {}
        r = Adw.ActionRow(title=f"Bridge {saved['id'][-4:]}", subtitle=saved.get("ip") or "")
        state = Gtk.Label(valign=Gtk.Align.CENTER)
        if st.get("error"):
            state.set_label("Offline")
            state.add_css_class("error")
        elif st.get("connected"):  # the iPhone's name, after a phone icon, in plain text
            state.set_label(st.get("phone") or "iPhone")
            r.add_suffix(Gtk.Image(icon_name="phone-symbolic"))
        else:
            state.set_label("iPhone not connected")
            state.add_css_class("warning")
            r.set_subtitle("On the iPhone: Settings › Bluetooth › Phone Use Bridge")
        r.add_suffix(state)
        add(r)
        use = Adw.SwitchRow(title="Enable Input",
                            active=saved.get("use", True))
        use.connect("notify::active", lambda w, _p: in_thread(lambda: api("/api/bridge/use", {"on": w.get_active()}),
                                                              lambda *_: self.refresh()))
        add(use)
        net = Adw.ActionRow(title="Wi-Fi Network", activatable=True)
        net.add_suffix(Gtk.Label(label=saved.get("ssid") or "", css_classes=["dim-label"]))
        net.add_suffix(Gtk.Image(icon_name="go-next-symbolic"))
        net.connect("activated", lambda *_: BridgeSetup(self, change=True).present(self))
        add(net)
        add(firmware)
        # Forget: the last row, like Unpair (the Mac and iPhone apps do the same)
        others = (st.get("hosts") or 1) - 1  # other PCs the bridge lets in (firmware 0.3+)
        forget = Adw.ActionRow(title="Forget Bridge", subtitle=(
            f"Other computers keep using it ({others})" if others > 0 else
            "Resets the bridge and disconnects it from your iPhone"))
        forget.add_suffix(button("Forget…", None, self._confirm_forget_bridge))
        add(forget)

    # ---- this PC's Bluetooth ----
    def _pcbt_show(self, running):
        from . import bluetooth_setup
        self.pcbt_row.set_sensitive(running)
        if self.pcbt_row.get_active() != bluetooth_setup.on():
            self._pcbt_quiet = True
            self.pcbt_row.set_active(bluetooth_setup.on())
            self._pcbt_quiet = False

    def _pcbt_toggled(self, row, _p):
        if self._pcbt_quiet:
            return
        from . import bluetooth_setup
        if row.get_active():
            d = Adw.AlertDialog(heading="Use this PC's Bluetooth?",
                                body="Your iPhone can pair with this PC as its keyboard and mouse. While this is on, "
                                     "this PC can't use Bluetooth keyboards or mice of its own. Phone Use asks for "
                                     "your password once.")
            d.add_response("cancel", "Cancel")
            d.add_response("yes", "Use Bluetooth")
            d.set_response_appearance("yes", Adw.ResponseAppearance.SUGGESTED)
            d.connect("response", lambda _d, r: self._pcbt_apply(bluetooth_setup.enable) if r == "yes"
                      else self.refresh())
            d.present(self)
        else:
            self._pcbt_apply(bluetooth_setup.disable)

    def _pcbt_apply(self, change):
        self.pcbt_row.set_sensitive(False)
        turning_on = change.__name__ == "enable"

        def done(_r, e):
            if e:
                self.toast("Cancelled" if str(e) == "cancelled" else f"Couldn't change Bluetooth: {e}")
            elif turning_on:
                self.toast("On the iPhone: Settings › Bluetooth › Phone Use")
            self.refresh()
        in_thread(lambda: (change(), api("/api/input/restart", {}, timeout=20)), done)

    # ---- actions ----
    def toast(self, text, undo=None):
        t = Adw.Toast(title=text, timeout=4)
        if undo:
            t.set_button_label("Undo")
            t.connect("button-clicked", lambda *_: undo())
        self.toasts.add_toast(t)

    def _mode_changed(self, *_):
        if self._quiet:
            return
        mode = MODES[self.mode_row.get_selected()][0]
        secs = DELAYS[self.delay_row.get_selected()]
        self.delay_row.set_visible(mode == "countdown")
        in_thread(lambda: api("/api/session/ask", {"mode": mode, "seconds": secs}),
                  lambda _r, e: self.toast(f"Couldn't change Agent control: {e}") if e else None)

    def _awake_changed(self, *_):
        if self._quiet:
            return
        on = self.awake_row.get_active()
        in_thread(lambda: api("/api/awake", {"on": on}),
                  lambda _r, e: self.toast(f"Couldn't change Keep Awake: {e}") if e else None)

    def _login_changed(self, *_):
        on = self.login_row.get_active()
        save_app_state(login=on)
        self.app.request_background(on)

    def _passcode_apply(self, *_):
        code = self.pass_entry.get_text()
        self.pass_entry.set_text("")
        if code:
            in_thread(lambda: api("/api/passcode", {"code": code}),
                      lambda _r, e: (self.toast(f"Couldn't save the passcode: {e}" if e else "Passcode saved"),
                                     self.refresh()))

    def _claude_clicked(self):
        if claude_setup.connected():
            in_thread(claude_setup.disconnect, lambda _r, e: (
                self.toast(f"Couldn't disconnect: {e}" if e else "Disconnected from Claude Code"), self.refresh()))
        else:
            in_thread(claude_setup.connect, lambda _r, e: (
                self.toast(f"Couldn't connect: {e}" if e else "Connected. New Claude Code sessions can use the "
                           "phone."), self.refresh()))

    def _confirm_forget_bridge(self):
        confirm(self, "Forget the bridge?", "The bridge resets and disconnects from your iPhone. To use it again, "
                "hold its lower button for 3 seconds and set it up.", "Forget",
                lambda: in_thread(lambda: api("/api/bridge", method="DELETE"), self._bridge_forgotten))

    def _bridge_forgotten(self, j, err):
        if err:
            self.toast(f"Couldn't forget the bridge: {err}")
        elif j.get("reset"):
            self.toast("Bridge reset. Also forget Phone Use Bridge in the iPhone's Bluetooth settings")
        elif j.get("why") == "kept":
            self.toast("Bridge removed from this computer; other computers keep using it")
        else:  # forgotten here; the bridge itself didn't answer (offline, or older firmware)
            self.toast("Bridge removed, but it didn't respond to the reset")
        self.refresh()

    def _confirm_unpair(self):
        n = paired_iphones()
        confirm(self, "Unpair the iPhone app?" if n == 1 else f"Unpair {n} iPhone apps?",
                "Agents can't use the iPhone until you pair the app again.", "Unpair", self._unpair)

    def _unpair(self):
        from . import companion
        companion.forget()
        self.toast("iPhone app unpaired")
        self.refresh()

    def _allow_network(self):
        self.fw_btn.set_sensitive(False)
        self.hero_btn.set_sensitive(False)

        def done(msg, e):
            self.fw_btn.set_sensitive(True)
            self.hero_btn.set_sensitive(True)
            self.toast("Cancelled" if e and str(e) == "cancelled" else f"Couldn't open the ports: {e}" if e else msg)
            self.refresh()
        in_thread(netcheck.allow, done)


class PairDialog(Adw.Dialog):
    """A one-time pairing link as a QR code for the iPhone's camera; closes once the iPhone
    has paired, and makes a new code when this one runs out."""

    def __init__(self, win):
        super().__init__(title="Pair iPhone", content_width=360)
        self.win = win
        self.before = paired_iphones()
        view = Adw.ToolbarView()
        view.add_top_bar(Adw.HeaderBar())
        self.box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=12, margin_top=12, margin_bottom=24,
                           margin_start=24, margin_end=24)
        view.set_content(self.box)
        self.set_child(view)
        self.timer = GLib.timeout_add_seconds(2, self._check)
        self.connect("closed", lambda *_: GLib.source_remove(self.timer))
        self.new_code()

    def _clear(self):
        while (c := self.box.get_first_child()):
            self.box.remove(c)

    def new_code(self):
        self._clear()
        self.box.append(Adw.Spinner(height_request=48))
        self.made = time.time()
        in_thread(lambda: api("/api/companion/pair", {}, timeout=10)["link"], self._show)

    def _show(self, link, err):
        self._clear()
        if err:
            self.box.append(Gtk.Label(label=f"Couldn't make a pairing code: {err}", wrap=True))
            self.box.append(button("Try Again", "pill", self.new_code))
            return
        tex = qr_texture(link)
        # A square the size of the code, clipped to rounded corners, so nothing shows around it.
        self.box.append(Gtk.Picture(paintable=tex, can_shrink=True, content_fit=Gtk.ContentFit.FILL,
                                    width_request=260, height_request=260, halign=Gtk.Align.CENTER,
                                    overflow=Gtk.Overflow.HIDDEN, css_classes=["qr"]))
        self.box.append(Gtk.Label(label="Scan this code with the Phone Use app on your iPhone.",
                                  wrap=True, justify=Gtk.Justification.CENTER, css_classes=["dim-label"]))

    def _check(self):
        if paired_iphones() > self.before:
            self.win.toast("iPhone paired")
            self.win.refresh()
            self.close()
            return False
        if time.time() - self.made > 590:  # the code lasts 10 minutes
            self.new_code()
        return True


class FirmwareDialog(Adw.Dialog):
    """Flash a LilyGO T-Display over USB with the bridge firmware this copy of Phone Use ships:
    wait for the board, confirm (flashing erases it: Wi-Fi, key, iPhone pairing), write it
    (bridge.install_firmware) with progress, forget the old bridge here, then set it up."""

    def __init__(self, win):
        super().__init__(title="Flash LilyGO T-Display", content_width=380, content_height=340)
        self.win, self.poll = win, None
        tv = Adw.ToolbarView()
        tv.add_top_bar(Adw.HeaderBar())
        self.box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=14, margin_top=24, margin_bottom=24,
                           margin_start=24, margin_end=24, valign=Gtk.Align.CENTER, vexpand=True)
        tv.set_content(self.box)
        self.set_child(tv)
        self.connect("closed", lambda *_: self.poll and GLib.source_remove(self.poll))
        from . import bridge
        self.firmware = bridge.release_firmware()
        if not self.firmware:
            self._show("This copy of Phone Use has no bridge firmware. From a checkout, run "
                       "phoneuse bridge flash.")
            return
        self.wait_for_usb()

    def _show(self, text, spinner=False, bar=None, actions=()):
        while (c := self.box.get_first_child()):
            self.box.remove(c)
        if spinner:
            self.box.append(Adw.Spinner(height_request=32))
        if bar is not None:
            self.box.append(bar)
        self.box.append(Gtk.Label(label=text, wrap=True, justify=Gtk.Justification.CENTER,
                                  css_classes=["dim-label"]))
        for label, style, fn in actions:
            b = button(label, style, fn)
            b.set_halign(Gtk.Align.CENTER)
            b.set_size_request(220, -1)  # equal buttons
            self.box.append(b)

    def wait_for_usb(self):
        self._show("Plug the T-Display into this PC with a USB cable.", spinner=True)

        def look():
            from . import bridge
            try:
                port = bridge.usb_port()
            except Exception:  # noqa: BLE001 - no serial support: keep waiting, nothing to show
                port = None
            if port:
                self.poll = None
                self.confirm(port)
                return False
            return True
        self.poll = GLib.timeout_add(1000, look)

    def confirm(self, port):
        version = self.firmware[1]
        self._show(f"Found a board on {port}. Flashing installs bridge firmware {version} and erases the "
                   "board: the bridge it was loses its Wi-Fi and its iPhone pairing, and you set it up again.",
                   actions=[("Erase and Flash", "pill destructive-action", lambda: self.install(port)),
                            ("Cancel", "pill", self.close)])

    def install(self, port):
        from . import bridge
        bar = Gtk.ProgressBar(margin_start=12, margin_end=12)
        version = self.firmware[1]
        self._show(f"Flashing bridge firmware {version}…" if version else "Flashing…", bar=bar)

        def progress(frac):
            GLib.idle_add(lambda: (bar.set_fraction(frac), False)[1])

        def done(_r, err):
            if err:
                return self._show(f"Couldn't install the firmware: {err}",
                                  actions=[("Try Again", "pill", self.wait_for_usb)])
            if bridge.saved():  # its key is gone with the erase
                api_quiet = lambda: api("/api/bridge", method="DELETE")
                in_thread(api_quiet)
            self._show("Flashed. Set the bridge up to use it. On the iPhone, forget Phone Use Bridge in "
                       "Bluetooth settings first if it's listed.",
                       actions=[("Set Up Bridge", "pill suggested-action", self._setup)])
        in_thread(lambda: bridge.install_firmware(port, progress), done)

    def _setup(self):
        self.close()
        BridgeSetup(self.win).present(self.win)


class BridgeSetup(Adw.Dialog):
    """Set up a bridge (find it over Bluetooth, compare the code on its screen, pick a Wi-Fi
    network, type its password, then wait for the iPhone to connect to it), or move a set-up
    bridge to another network."""

    def __init__(self, win, change=False, adopt=None):
        super().__init__(title="Wi-Fi Network" if change else "Use Bridge" if adopt else "Set Up Bridge",
                         content_width=420, content_height=540)
        self.win, self.change, self.waiting, self.ended = win, change, None, False
        self.nav = Adw.NavigationView()
        self.set_child(self.nav)
        self.connect("closed", self._closed)
        self.networks() if change else self.adopt(adopt) if adopt else self.find()

    def _closed(self, *_):
        self.ended = True
        if self.waiting:
            GLib.source_remove(self.waiting)
        if not self.change:
            in_thread(lambda: api("/api/bridge/cancel", {}))
        self.win.refresh()

    def _page(self, title, *groups, actions=()):
        tv = Adw.ToolbarView()
        tv.add_top_bar(Adw.HeaderBar())
        page = Adw.PreferencesPage()
        for g in groups:
            page.add(g)
        if actions:
            g = Adw.PreferencesGroup()
            # One column of equal buttons.
            box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=12, halign=Gtk.Align.CENTER, margin_top=6,
                          homogeneous=True, width_request=240)
            for label, style, fn in actions:
                box.append(button(label, style, fn))
            g.add(box)
            page.add(g)
        tv.set_content(page)
        p = Adw.NavigationPage(title=title, child=tv)
        self.nav.push(p) if self.nav.get_visible_page() else self.nav.add(p)
        return p

    def _text(self, text, big=False):
        g = Adw.PreferencesGroup()
        g.add(Gtk.Label(label=text, wrap=True, justify=Gtk.Justification.CENTER, margin_top=12,
                        css_classes=["title-1", "numeric"] if big else ["dim-label"]))
        return g

    def _wait(self, title, text):
        g = Adw.PreferencesGroup()
        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=12, margin_top=24)
        box.append(Adw.Spinner(height_request=32))
        box.append(Gtk.Label(label=text, css_classes=["dim-label"], wrap=True, justify=Gtk.Justification.CENTER))
        g.add(box)
        self._page(title, g)

    def _error(self, title, text, retry):
        self._page(title, self._text(text), actions=[("Try Again", "pill", retry)])

    def _firmware(self):
        self.close()
        FirmwareDialog(self.win).present(self.win)

    def find(self):
        self._wait("Find the Bridge", "Looking for the bridge…")
        in_thread(lambda: api("/api/bridge/find", {}, timeout=30), self._found)

    def _found(self, j, err):
        if err or (j or {}).get("error"):
            return self._error("Find the Bridge", str(err or j["error"]), self.find)
        near = j.get("nearby") or []
        if not j["bridges"] and not near:
            return self._page("Find the Bridge", self._text("No bridge found. Hold the bridge's lower button for 3 "
                                                            "seconds, then try again."),
                              actions=[("Try Again", "pill", self.find),
                                       ("Flash LilyGO T-Display", "pill", self._firmware)])
        g = Adw.PreferencesGroup(description="Choose your bridge.")
        for nb in near:  # set up already, on this network: ask it to let this computer in
            g.add(nav_row(f"Bridge {nb['id'][-4:]}", f"{nb.get('phone') or 'No iPhone'} · {nb.get('ssid') or nb['ip']}",
                          lambda nb=nb: self.adopt(nb)))
        for b in j["bridges"]:
            g.add(nav_row(b.get("name") or "Phone Use Bridge", b["address"], lambda a=b["address"]: self.pair(a)))
        self._page("Find the Bridge", g)

    # ---- a bridge another computer set up: it shows this one's name and a code ----
    def adopt(self, nb):
        self.adopting = nb
        self._wait("Check the Code", "Asking the bridge…")
        in_thread(lambda: api("/api/bridge/adopt", {"id": nb["id"]}, timeout=15), self._asked)

    def _asked(self, j, err):
        if err or (j or {}).get("error"):
            return self._error("Check the Code", str(err or j["error"]), lambda: self.adopt(self.adopting))
        g = Adw.PreferencesGroup()
        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=12, margin_top=12)
        box.append(Gtk.Label(label=j["code"], css_classes=["title-1", "numeric"]))
        box.append(Gtk.Label(label=f"If the bridge shows {j['name']} and the same code, press either of its buttons.",
                             wrap=True, justify=Gtk.Justification.CENTER, css_classes=["dim-label"]))
        box.append(Adw.Spinner(height_request=24))
        g.add(box)
        self._page("Check the Code", g)
        self._adopt_wait()

    def _adopt_wait(self):
        def done(j, err):
            if self.ended:
                return
            if err or (j or {}).get("error"):
                return self._error("Check the Code", str(err or j["error"]), lambda: self.adopt(self.adopting))
            if j.get("waiting"):
                return self._adopt_wait()
            self.win.toast("This computer now uses the bridge")
            self.close()
        in_thread(lambda: api("/api/bridge/adopt/wait", {}, timeout=40), done)

    def pair(self, address):
        self._wait("Check the Code", "Connecting…")
        in_thread(lambda: api("/api/bridge/pair", {"address": address}, timeout=40), self._paired)

    def _paired(self, j, err):
        if err or (j or {}).get("error"):
            return self._error("Check the Code", str(err or j["error"]), self.find)
        self._page("Check the Code", self._text(j["code"], big=True),
                   self._text("Does the bridge show the same code?"),
                   actions=[("Codes Match", "pill suggested-action", self.networks),
                            ("Don't Match", "pill destructive-action", self.find)])

    def networks(self):
        self._wait("Choose a Network", "Looking for Wi-Fi…")
        def ask():
            j = api("/api/bridge/networks", {}, timeout=30)
            if not j.get("error") and not j.get("networks"):  # a scan can come back empty: once more
                j = api("/api/bridge/networks", {}, timeout=30)
            return j
        in_thread(ask, self._networks)

    def _networks(self, j, err):
        if err or (j or {}).get("error"):
            return self._error("Choose a Network", str(err or j["error"]), self.networks)
        if not any(n.get("s") for n in j["networks"]):
            g = Adw.PreferencesGroup()
            g.add(nav_row("Other Network…", None, self.other))
            return self._page("Choose a Network", self._text("No networks found."), g,
                              actions=[("Try Again", "pill", self.networks)])
        g = Adw.PreferencesGroup(description="Choose the same 2.4 GHz network as this PC.")
        for n in j["networks"]:
            if not n.get("s"):
                continue
            r = nav_row(n["s"], None, lambda n=n: self.password(n["s"], n.get("l")))
            if n.get("l"):
                r.add_prefix(Gtk.Image(icon_name="channel-secure-symbolic"))
            g.add(r)
        g.add(nav_row("Other Network…", None, self.other))
        self._page("Choose a Network", g)

    def other(self):
        g = Adw.PreferencesGroup()
        name = Adw.EntryRow(title="Network Name")
        pw = Adw.PasswordEntryRow(title="Password")
        g.add(name)
        g.add(pw)
        self._page("Other Network", g, actions=[("Join", "suggested-action pill",
                                                 lambda: name.get_text() and self.join(name.get_text(), pw.get_text()))])

    def password(self, ssid, locked):
        if not locked:
            return self.join(ssid, "")
        g = Adw.PreferencesGroup()
        e = Adw.PasswordEntryRow(title=f"Password for {ssid}", show_apply_button=True)
        e.connect("apply", lambda *_: self.join(ssid, e.get_text()))
        g.add(e)
        self._page("Wi-Fi Password", g)
        e.grab_focus()

    def join(self, ssid, password):
        self._wait("Joining", f"Joining {ssid}…")
        in_thread(lambda: api("/api/bridge/join", {"ssid": ssid, "password": password}, timeout=60),
                  lambda j, err: self._joined(j, err, ssid))

    def _joined(self, j, err, ssid):
        if err or (j or {}).get("error"):
            return self._error("Joining", str(err or j["error"]), self.networks)
        if self.change:
            self.win.toast(f"Bridge connected to {ssid}")
            return self.close()
        self._wait("Connect the iPhone", "On the iPhone, open Settings › Bluetooth and tap Phone Use Bridge. If it "
                   "won't connect, forget it there and tap it again.")
        self.waiting = GLib.timeout_add_seconds(2, self._phone_check)

    def _phone_check(self):
        def done(b, _e):
            st = (b or {}).get("status") or {}
            if st.get("connected") and self.waiting:
                GLib.source_remove(self.waiting)
                self.waiting = None
                self._page("Bridge Ready", self._text(f"Connected to {st.get('phone') or 'the iPhone'}."),
                           actions=[("Done", "suggested-action pill", self.close)])
        in_thread(lambda: api("/api/bridge"), done)
        return True


class App(Adw.Application):
    def __init__(self):
        flags = Gio.ApplicationFlags.HANDLES_COMMAND_LINE
        if os.environ.get("PHONEUSE_APP_SIZE"):  # a screenshot run: never activate the real window instead
            flags |= Gio.ApplicationFlags.NON_UNIQUE
        super().__init__(application_id=APP_ID, flags=flags)
        self.proc = None  # the `phoneuse serve` this window started
        self.win = None
        for name, fn in (("open", lambda *_: self.open_phone()), ("about", lambda *_: self.about()),
                         ("quit", lambda *_: self.quit_all())):
            a = Gio.SimpleAction.new(name, None)
            a.connect("activate", fn)
            self.add_action(a)
        self.set_accels_for_action("app.quit", ["<Control>q"])

    def do_command_line(self, cmd):
        # --background (Start at Login): start the services without the window
        if "--background" in cmd.get_arguments()[1:]:
            if not services_running():
                self.start_services()
            elif not self.proc:
                return 0
            self.hold()
            self.release()
            return 0
        self.activate()
        return 0

    def do_activate(self):
        from gi.repository import Gdk
        css = Gtk.CssProvider()
        css.load_from_string(".qr { border-radius: 12px; }")  # QR codes: rounded corners
        Gtk.StyleContext.add_provider_for_display(Gdk.Display.get_default(), css,
                                                  Gtk.STYLE_PROVIDER_PRIORITY_APPLICATION)
        if not os.environ.get("FLATPAK_ID"):  # a checkout: the app's own icons live in packaging/icons
            from gi.repository import Gdk
            Gtk.IconTheme.get_for_display(Gdk.Display.get_default()).add_search_path(
                os.path.join(os.path.dirname(os.path.dirname(os.path.realpath(__file__))), "packaging", "icons"))
        if not self.win:
            self.win = Window(self)
            self.win.connect("close-request", self._on_close)
            self._tick = GLib.timeout_add_seconds(2, lambda: (self.win.refresh(), True)[1])
            if not services_running():
                self.start_services()
        self.win.present()
        self.win.refresh()

    def _on_close(self, *_):
        # The window closes for good (a hidden GTK window doesn't map again on some compositors,
        # Hyprland among them); the services keep running for agents, and the next launch builds
        # a new window.
        GLib.source_remove(self._tick)
        self.win = None
        return False

    def start_services(self):
        # self.proc: started already and still starting up (its ports aren't open yet, so
        # services_running() can't tell): don't start a second one.
        if self.proc or services_running():
            return self.win and self.win.refresh()
        self.proc = Gio.Subprocess.new(serve_command(), Gio.SubprocessFlags.NONE)
        self.hold()
        self.proc.wait_async(None, self._services_ended)

    def _services_ended(self, proc, _res):
        if proc is self.proc:
            self.proc = None
            self.release()
            if self.win:
                self.win.refresh()

    def quit_all(self):
        """Stop the services, whoever started them, and quit once they're gone, so that the next
        launch starts fresh rather than finding services that are still shutting down."""
        if self.win:
            self.win.set_visible(False)
        if self.proc:
            self.proc.send_signal(15)
        elif services_running():
            try:
                api("/api/quit", {})
            except OSError:
                pass
        deadline = time.time() + 15

        def check():
            if services_running() and time.time() < deadline:
                return True
            self.quit()
            return False
        GLib.timeout_add(200, check)

    def open_phone(self):
        Gtk.UriLauncher.new(f"http://localhost:{PORT}").launch(self.win, None, None)

    def about(self):
        Adw.AboutDialog(application_name="Phone Use", application_icon=APP_ID, developer_name="Aleksey Smolenchuk",
                        version="0.1.3", website="https://github.com/lxe/phoneuse",
                        license_type=Gtk.License.MIT_X11).present(self.win)

    def request_background(self, on):
        """Start at Login through the desktop's background portal (works in and out of the Flatpak)."""
        try:
            bus = Gio.bus_get_sync(Gio.BusType.SESSION)
            opts = {"reason": GLib.Variant("s", "Phone Use keeps running so agents can use the iPhone"),
                    "autostart": GLib.Variant("b", on),
                    "commandline": GLib.Variant("as", ["phoneuse-app", "--background"]),
                    "dbus-activatable": GLib.Variant("b", False)}
            bus.call_sync("org.freedesktop.portal.Desktop", "/org/freedesktop/portal/desktop",
                          "org.freedesktop.portal.Background", "RequestBackground",
                          GLib.Variant("(sa{sv})", ("", opts)), None, Gio.DBusCallFlags.NONE, 10000, None)
        except GLib.Error as e:
            self.win.toast(f"Couldn't change Start at Login: {e.message}")


def main():
    Adw.init()
    sys.exit(App().run(sys.argv))


if __name__ == "__main__":
    main()
