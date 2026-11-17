"""The control window's first line: the one thing left to do, or the iPhone when nothing is.

Every shell (the GTK window, the Mac app) shows the same step, so the order of checks lives
here, and the shells read it from GET /api/app. A step is
  {"step": id, "title", "detail", "action": id or None, "icon": id, "good": bool, "quiet": bool}
where `detail` is plain text (a shell may bold `bold` inside it), `action` names what the
button does (start, setup_bridge, pair_app, open_ports, retry, open_phone), `icon` is one of phone,
keyboard, offline, bluetooth, `steps` (when present) is a numbered list to show under the title,
where "{mirroring}" stands for the Screen Mirroring icon inline and `bold` is bolded too,
`qr` "pair" asks for the pairing QR code (POST /api/companion/pair gives the link; it works
once, for 10 minutes), `good` marks a healthy state and `quiet` a button that is a
fallback rather than the step itself.
"""


def _setup_file():
    import os
    from .config import config_path
    return os.path.join(os.path.dirname(config_path()), "setup.json")


def input_choice():
    """What the owner chose in setup for the phone's mouse and keyboard: "pc" (this PC's own
    Bluetooth), "bridge", or None before choosing."""
    import json
    try:
        with open(_setup_file()) as f:
            v = json.load(f).get("input")
    except (OSError, ValueError):
        return None
    return v if v in ("pc", "bridge") else None


def set_input_choice(choice):
    import json
    import os
    if choice not in ("pc", "bridge", None):
        raise ValueError(f"no such choice: {choice!r}")
    path = _setup_file()
    try:
        with open(path) as f:
            d = json.load(f)
    except (OSError, ValueError):
        d = {}
    d["input"] = choice
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w") as f:
        json.dump(d, f)


def _step(step, title, detail="", action=None, icon="phone", good=False, quiet=False, bold=None, steps=None, qr=None):
    return {"step": step, "title": title, "detail": detail, "action": action, "icon": icon,
            "good": good, "quiet": quiet, "bold": bold, "steps": steps, "qr": qr}


def stopped():
    """For a shell that finds no services running (the API can't say this itself)."""
    return _step("stopped", "Phone Use is stopped", "Start it to see and use your iPhone here.", "start")


def hero(s, name):
    """s: {"picture", "bridge", "firewall", "confirmed", "paired", "pc_bluetooth"} as /api/app
    gathers them."""
    pic, b = s.get("picture"), s.get("bridge") or {}
    st, saved = b.get("status") or {}, b.get("saved")
    if not pic:
        return _step("starting", "Starting…")
    if not s.get("paired"):  # first: the app is where the owner allows and stops agents
        return _step("pair_app", "Pair your iPhone", "Open Phone Use on your iPhone, tap Scan QR Code and "
                     "scan this code.", qr="pair")
    if not saved and not s.get("pc_bluetooth"):
        return _step("setup_bridge", "Set up the bridge", "The bridge is a small puck that taps and types on your "
                     "iPhone over Bluetooth, and connects to this PC over Wi-Fi. Keep it plugged in near your iPhone.", "setup_bridge", icon="keyboard")
    if not pic.get("live"):
        fw = bool(s.get("firewall")) and not s.get("confirmed")
        return _step("start_mirroring", "Start Screen Mirroring",
                     f"If {name} isn't listed, open the ports." if fw else "",
                     "open_ports" if fw else None, quiet=True, bold=name,
                     steps=["On the iPhone, swipe down from the top-right corner.",
                            "Tap {mirroring} Screen Mirroring.", f"Choose {name}."])
    if saved and st.get("error"):
        return _step("bridge_offline", "The bridge is offline", "Check that it has power and is on the same "
                     "Wi-Fi as this PC.", "retry", icon="offline")
    phone = b.get("mirroring_phone")
    if saved and b.get("other_phone"):  # the bridge holds a phone that isn't mirroring: nothing goes to it
        return _step("other_phone", f"The bridge is on {b['other_phone']}",
                     f"{phone} is mirroring. To use it, open Settings › Bluetooth on {phone} and tap Phone Use Bridge.",
                     icon="bluetooth", bold=phone)
    if saved and not st.get("connected"):
        return _step("connect_bridge", "Connect the bridge to the iPhone", f"On {phone or 'the iPhone'}, open Settings › "
                     "Bluetooth and tap Phone Use Bridge. If it won't connect, forget it there and tap it again.",
                     icon="bluetooth", bold=phone)
    if pic.get("paused"):
        return _step("taken_over", "You're in control", "You took over from the agent.", "open_phone", good=True)
    if pic.get("stale"):
        return _step("stale", "The picture is old", "The iPhone's screen is off, or mirroring stalled.",
                     "open_phone")
    return _step("mirroring", phone or st.get("phone") or "iPhone", "Mirroring to this PC", "open_phone", good=True)


def checklist(s, name):
    """First-time setup as a list the Windows window shows and ticks off as each step is seen
    done: [{"id", "title", "detail", "done", "optional", "action", "steps", "bold"}], in the
    order to do them. s: what /api/app gathers, plus "bluetooth" (the phone's input link is
    up), "calibrated" (the mirroring phone's pointer is measured; None: no phone yet) and
    "pc" (this PC's Bluetooth name), "input_choice" (input_choice()). Actions: choose_input
    (the "input" step also says "choice": "pc", "bridge" or None), allow_firewall, setup_bridge,
    pair_app, claude."""
    pic, b = s.get("picture") or {}, s.get("bridge") or {}
    saved = b.get("saved")
    live = bool(pic.get("live"))
    items = []

    def add(id, title, detail="", done=False, optional=False, action=None, steps=None, bold=None):
        items.append({"id": id, "title": title, "detail": detail, "done": bool(done), "optional": optional,
                      "action": action, "steps": steps, "bold": bold})

    # How the phone gets its mouse and keyboard: this PC's Bluetooth LE, or a bridge (Bluetooth
    # Classic). Asked until the owner picks one; a bridge already set up counts as chosen.
    choice = s.get("input_choice")
    if saved:
        choice = "bridge"
    add("input", "Choose how Phone Use controls the phone",
        "Bridge chosen: set it up next (Bluetooth Settings has the steps)." if choice == "bridge" and not saved
        else "", done=choice == "pc" or (choice == "bridge" and saved), action="choose_input")
    items[-1]["choice"] = choice
    if s.get("firewall") and not s.get("confirmed"):  # Windows Firewall has no rule for UxPlay yet
        add("firewall", "Let the iPhone reach this PC",
            "Windows Firewall blocks Screen Mirroring until Phone Use has a rule for it (local network "
            "only). Windows asks for permission once.", action="allow_firewall")
    add("mirroring", "Start Screen Mirroring", done=live, bold=name,
        steps=["On the iPhone, swipe down from the top-right corner.", "Tap {mirroring} Screen Mirroring.",
               f"Choose {name}."])
    if saved:
        add("bluetooth", "Connect the bridge to the iPhone",
            "On the iPhone, open Settings › Bluetooth and tap Phone Use Bridge. If it won't connect, "
            "forget it there and tap it again.", done=s.get("bluetooth"), bold="Phone Use Bridge")
    else:
        pc = s.get("pc") or "this PC"
        add("bluetooth", "Pair Bluetooth",
            f"On the iPhone, open Settings › Bluetooth and tap {pc}, then confirm on both. Phone Use is the "
            "phone's mouse and keyboard this way. A bridge can do it instead.",
            done=s.get("bluetooth"), action="setup_bridge", bold=pc)
    add("pointer", "Turn on AssistiveTouch",
        "On the iPhone: Settings › Accessibility › Touch › AssistiveTouch on, and Dwell Control off. iOS ignores "
        "a mouse without it. Then Phone Use measures this phone's pointer, for about 13 seconds: leave the "
        "phone alone until it's done.", done=s.get("calibrated"))
    add("app", "Pair the Phone Use app",
        "Agents can use the phone only once the Phone Use app (App Store) is paired: it's where you allow "
        "and stop them.", done=s.get("paired"), action="pair_app")
    claude = s.get("claude") or {}
    if claude.get("claude"):  # Claude Code is installed
        add("claude", "Connect Claude Code",
            "Lets Claude Code use the phone: links the phoneuse skill and puts the phoneuse command where "
            "Claude Code finds it.", done=claude.get("skill") and claude.get("command"), optional=True,
            action="claude")
    return items
