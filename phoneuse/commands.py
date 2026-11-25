"""Command metadata shared by discovery and MCP clients."""

from dataclasses import dataclass, field


@dataclass(frozen=True)
class Command:
    cli: str
    description: str
    positional: tuple[str, ...] = ()
    options: dict = field(default_factory=dict)
    properties: dict = field(default_factory=dict)
    output: str = "text"
    optional: tuple[str, ...] = ()

    def schema(self):
        return {"type": "object", "properties": self.properties, "required": list(self.positional),
                "additionalProperties": False}

    def arguments(self, values, artifact=None):
        args = [self.cli]
        for name, flag in self.options.items():
            value = values.get(name)
            if isinstance(value, bool):
                if value:
                    args.append(flag)
            elif value is not None:
                if self.properties[name]["type"] == "array":
                    value = ",".join(map(str, value))
                args.append(f"{flag}={value}")
        if self.output in {"json", "png"}:
            args.append("--json")
            if self.positional or self.optional or artifact is not None:
                args.append("--")
        for name in self.positional:
            value = str(values[name])
            if self.output == "text" and value.startswith("--"):
                raise ValueError(f"{name} must be a value, not a CLI flag")
            args.append(value)
        for name in self.optional:
            value = values.get(name)
            if value is None:
                continue
            value = str(value)
            if self.output == "text" and value.startswith("--"):
                raise ValueError(f"{name} must be a value, not a CLI flag")
            args.append(value)
        if artifact is not None:
            args.append(str(artifact))
        return args


STRING = {"type": "string", "minLength": 1}
FRACTION = {"type": "number", "minimum": 0, "maximum": 1}
REGION = {"type": "array", "items": FRACTION, "minItems": 4, "maxItems": 4,
          "description": "Normalized screen bounds in left, top, right, bottom order."}
TIMEOUT = {"type": "number", "exclusiveMinimum": 0, "maximum": 60}
UNLOCK_WAIT_DEFAULT = 30
UNLOCK_WAIT_MAX = 45
MAX_SCROLLS = 12
KEY_COMBO = {"type": "string", "minLength": 1,
             "pattern": r"^((cmd|command|ctrl|control|alt|option|opt|shift|gui)[+-])*"
                        r"([a-zA-Z0-9]|enter|return|esc|escape|backspace|tab|space|right|left|down|up|delete)$"}
SYSTEM_GESTURES = ("home", "switcher", "control-center", "notifications", "dismiss")
TOOLS = {
    "status": Command("status", "Check the mirror, Bluetooth connection, and owner takeover. "
                      "Use this after a dropped connection and follow the recovery steps in the result."),
    "show_mirror": Command("view", "Show the owner the Phone Use mirror before operating the phone. "
                           "On Windows this opens or restores the native WinUI window when available."),
    "reconnect_bluetooth": Command("reconnect", "Try to reconnect an already paired iPhone after status shows "
                                   "Bluetooth disconnected. Does not send iPhone input or change pairing."),
    "request_unlock": Command("unlock", "Request owner approval to unlock the iPhone if saved-passcode "
                              "unlock is supported. If it is disabled or denied, ask the owner to unlock "
                              "the iPhone directly, then call wait_for_unlock. Never ask for or enter the passcode."),
    "wait_for_unlock": Command(
        "wait-for-unlock",
        "After asking the owner to unlock the iPhone manually, call this once and wait for the result. "
        "Stop issuing tool calls while it runs. Phone Use watches the live mirror without sending input. "
        "Resume only on unlocked. On timeout, wait for the owner before retrying. Defaults to 30 seconds.",
        options={"timeout": "--timeout"}, properties={"timeout": {**TIMEOUT, "maximum": UNLOCK_WAIT_MAX}},
        output="json",
    ),
    "look": Command("text", "Read visible iPhone text and its normalized positions."),
    "open_app": Command("open", "Open an installed iPhone app by its visible name.",
                        ("app",), properties={"app": STRING}),
    "tap_on": Command("tap-on", "Wait for a unique label and tap once. Use near for a label on the same row "
                      "or region for a screen area. Optionally verify a new label anywhere on screen. "
                      "If a result says input was sent or may have been sent, inspect before retrying.",
                      ("label",), {"timeout": "--timeout", "expect": "--expect", "near": "--near",
                                   "region": "--region"},
                      {"label": STRING, "timeout": TIMEOUT, "expect": STRING, "near": STRING, "region": REGION},
                      "json"),
    "wait_for": Command("wait-for", "Wait for a visible text label without sending input.",
                        ("label",), {"timeout": "--timeout"}, {"label": STRING, "timeout": TIMEOUT}, "json"),
    "find_in_scroll": Command("find-in-scroll", "Check the visible list, then scroll in the given direction "
                              "until a label is found or the search limit is reached. Returns full-screen "
                              "positions and stops on ambiguity. Defaults to down, 6 scrolls, and 30 seconds.",
                              ("label",), {"direction": "--direction", "max_scrolls": "--max-scrolls",
                                           "timeout": "--timeout"},
                              {"label": STRING, "direction": {"type": "string", "enum": ["up", "down"]},
                               "max_scrolls": {"type": "integer", "minimum": 0, "maximum": MAX_SCROLLS},
                               "timeout": TIMEOUT}, "json"),
    "inspect_region": Command("inspect-region", "Return a phone screen crop and nearby OCR with full-screen "
                              "positions. Coordinates are normalized bounds from 0 to 1.",
                              ("left", "top", "right", "bottom"), properties={
                                  name: FRACTION for name in ("left", "top", "right", "bottom")}, output="png"),
    "tap": Command("tap", "Tap a position from a recent look or crop. Coordinates range from 0 to 1.",
                   ("x", "y"), properties={"x": FRACTION, "y": FRACTION}),
    "scroll_by": Command("scroll-by", "Scroll by screen heights. Positive reveals what is below.",
                         ("dy",), properties={"dy": {"type": "number", "minimum": -1, "maximum": 1}}),
    "type_text": Command("type", "Type into the focused iPhone field, replacing its current contents.",
                         ("text",), properties={"text": STRING}),
    "back": Command("back", "Go back on the iPhone."),
    "long_press": Command("longpress", "Press and hold a position from a recent look or crop, for "
                          "context menus. Coordinates range from 0 to 1; hold is in seconds (default 0.8).",
                          ("x", "y"), optional=("hold",), properties={
                              "x": FRACTION, "y": FRACTION,
                              "hold": {"type": "number", "minimum": 0.3, "maximum": 5}}),
    "swipe": Command("swipe", "Swipe from one position to another, for carousels, sliders and dismissing "
                     "sheets. Coordinates range from 0 to 1; duration is in seconds (default 0.3).",
                     ("x0", "y0", "x1", "y1"), optional=("duration",), properties={
                         **{n: FRACTION for n in ("x0", "y0", "x1", "y1")},
                         "duration": {"type": "number", "minimum": 0.1, "maximum": 2}}),
    "navigate": Command("recipe", "Use a system gesture: home, switcher (the app switcher), "
                        "control-center, notifications, or dismiss (close an overlay, or go home from an app).",
                        ("name",), properties={"name": {"type": "string", "enum": list(SYSTEM_GESTURES)}}),
    "press_key": Command("key", "Press a key or a shortcut on the iPhone's keyboard, such as return, "
                         "esc, tab, cmd+a or cmd+space.",
                         ("combo",), properties={"combo": KEY_COMBO}),
    "toggle_keyboard": Command("keyboard", "Show or hide the iPhone's on-screen keyboard."),
    "siriai": Command(
        "siriai",
        "Ask Apple Intelligence on the iPhone and return the reply text. Always pass model: "
        "siri-plus-context is Siri, the only one with the owner's personal context (messages, mail, "
        "calendar, the screen) and app intents, so it can act in apps; get the owner's OK before anything it "
        "would send, buy or change. on-device (small, private, quickest), cloud (Private Cloud Compute, larger, "
        "may use the web) and cloud-pro (largest, slowest, tight daily limits) run a PhoneUse shortcut and see "
        "only the prompt. If a model's shortcut is missing, the result gives its iCloud link to add it.",
        ("prompt",), {"model": "--model", "timeout": "--timeout"},
        {"prompt": STRING, "model": {"type": "string", "enum": [
            "siri-plus-context", "on-device", "cloud", "cloud-pro"]}, "timeout": TIMEOUT}),
    "end_session": Command("done", "Give control of the iPhone back to the owner."),
}


def check_region(bounds):
    bounds = tuple(bounds)
    if len(bounds) != 4:
        raise ValueError("Supply four region bounds in left, top, right, bottom order.")
    left, top, right, bottom = bounds
    if not (0 <= left < right <= 1 and 0 <= top < bottom <= 1):
        raise ValueError("Use normalized bounds with left < right and top < bottom.")
    return left, top, right, bottom


def manifest():
    return [{"name": name, "description": command.description, "inputSchema": command.schema(),
             "cli": command.cli, "positional": command.positional, "optional": command.optional, "options": command.options,
             "output": command.output} for name, command in TOOLS.items()]
