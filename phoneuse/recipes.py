"""Recipes: named gestures (touch.py's steps: down X Y, move X Y [S], hold S, up).

A recipe's gesture can take arguments: $1, $2, ... are replaced by the values given
when it runs, and $3=0.8 gives $3 a default. Positions are screen fractions (0..1),
or phone points when a value is above 1, as everywhere else.

The built-in recipes were verified on the device (iPhone 17 Pro, iOS 26). Agents
save their own with `phoneuse recipe save` (MCP: save_recipe) once a gesture works,
into ~/.config/phoneuse/recipes.json; a saved recipe with a built-in's name replaces
it on this PC (to tune one for a phone). "home" is the Home key, not a gesture.
"""
import json
import os
import re

from . import touch
from .config import config_path

FILE = os.path.join(os.path.dirname(config_path()), "recipes.json")

BUILTIN = {
    "home": ("key", "the Home key (Cmd+H); works from any app"),
    "back": ("down 0 0.5 move 0.8 0.5 0.35 up",
             "a finger sliding in from the very left edge"),
    "switcher": ("down 0.5 1 move 0.5 0.62 0.45 hold 0.6 up",
                 "the app switcher: slow swipe up from the bottom, then hold"),
    "control-center": ("down 0.88 0 move 0.88 0.45 0.6 up",
                       "slow swipe down from the top-right corner (fast ones are ignored)"),
    "notifications": ("down 0.15 0 move 0.15 0.7 0.6 up",
                      "Notification Center: slow swipe down from the top-left"),
    "dismiss": ("down 0.5 1 move 0.5 0.15 0.12 up",
                "close Control Center, Notification Center or the switcher; from an app, go home"),
    "long-press": ("down $1 $2 hold $3=0.8 up",
                   "press and hold at ($1, $2) for $3 seconds: context menus"),
}

_ARG = re.compile(r"\$(\d+)(?:=(\S+))?")
NAME = re.compile(r"^[a-z0-9][a-z0-9-]{0,39}$")


def saved():
    try:
        with open(FILE) as f:
            d = json.load(f)
        return d if isinstance(d, dict) else {}
    except (OSError, ValueError):
        return {}


def every():
    """{name: (gesture, about, "built-in" | "saved")}, saved ones replacing built-ins."""
    out = {n: (g, a, "built-in") for n, (g, a) in BUILTIN.items()}
    for n, r in saved().items():
        if isinstance(r, dict) and r.get("gesture"):
            out[n] = (r["gesture"], r.get("about", ""), "saved")
    return out


def arity(gesture):
    """(arguments it needs, arguments it takes)."""
    found = [(int(m.group(1)), m.group(2)) for m in _ARG.finditer(gesture)]
    takes = max((n for n, _ in found), default=0)
    optional = {n for n, d in found if d is not None}
    needs = max((n for n, _ in found if n not in optional), default=0)
    return needs, takes


def expand(gesture, args):
    """The gesture with $N replaced by args[N-1] (or its default)."""
    needs, takes = arity(gesture)
    if len(args) < needs or len(args) > takes:
        want = f"{needs}" if needs == takes else f"{needs} to {takes}"
        raise touch.GestureError(f"this recipe takes {want} values, not {len(args)}: {gesture}")
    for a in args:
        try:
            float(a)
        except ValueError:
            raise touch.GestureError(f"recipe values are numbers, not {a!r}") from None

    def sub(m):
        n = int(m.group(1))
        return str(args[n - 1]) if n <= len(args) else m.group(2)
    return _ARG.sub(sub, gesture)


def get(name):
    r = every().get(name)
    if r is None:
        raise touch.GestureError(f"no recipe {name!r}; `phoneuse recipe list` shows them")
    return r


def save(name, gesture, about=""):
    """Check the gesture parses (with sample values for its $N) and keep it."""
    name = name.strip().lower()
    if not NAME.match(name):
        raise touch.GestureError("recipe names are lowercase letters, digits and dashes (up to 40)")
    if name in ("list", "save", "forget"):
        raise touch.GestureError(f"{name!r} is a command; choose another name")
    if name == "home":
        raise touch.GestureError("home is the Home key; choose another name")
    _, takes = arity(gesture)
    touch.parse_gesture(expand(gesture, ["0.5"] * takes))
    d = saved()
    d[name] = {"gesture": " ".join(gesture.split()), "about": about.strip()}
    _write(d)
    return name


def forget(name):
    d = saved()
    if d.pop(name, None) is None:
        raise touch.GestureError(f"no saved recipe {name!r}" + (" (built-in recipes stay)" if name in BUILTIN else ""))
    _write(d)


def _write(d):
    os.makedirs(os.path.dirname(FILE), exist_ok=True)
    tmp = FILE + ".tmp"
    with open(tmp, "w") as f:
        json.dump(d, f, indent=2, sort_keys=True)
    os.replace(tmp, FILE)


def listing():
    lines = []
    for n, (g, a, kind) in sorted(every().items()):
        shown = "(Home key)" if g == "key" else g
        lines.append(f"{n}: {shown}" + (f"  # {a}" if a else "") + ("  [saved]" if kind == "saved" else ""))
    return "\n".join(lines)
