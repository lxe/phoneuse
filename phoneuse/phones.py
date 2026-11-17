"""The phones phoneuse has driven, and which one is screen mirroring now.

~/.config/phoneuse/phones.json keeps one entry per phone, keyed by the name it
mirrors under (UxPlay prints it on every connection): its model, when it was
first and last seen, and its pointer calibration (calibrate.py), which drags use
in place of the iPhone 17 Pro table in touch.py. `phoneuse up` writes the
mirroring phone's name to a runtime file, so every process (the CLI, the browser
mirror's server) reads the same answer.
"""
import json
import os
import time

from .config import RUNTIME_DIR, config_path

PHONES = os.path.join(os.path.dirname(config_path()), "phones.json")
CURRENT = os.path.join(RUNTIME_DIR, "phoneuse-phone")


def load():
    try:
        with open(PHONES) as f:
            return json.load(f)
    except (OSError, ValueError):
        return {}


def _write(phones):
    os.makedirs(os.path.dirname(PHONES), exist_ok=True)
    tmp = PHONES + ".tmp"
    with open(tmp, "w") as f:
        json.dump(phones, f, indent=1, ensure_ascii=False)
    os.replace(tmp, PHONES)


def seen(name, model):
    """A phone started mirroring: record it, and make it the current phone.
    Returns True the first time this phone is seen."""
    phones = load()
    now = time.strftime("%Y-%m-%d %H:%M")
    new = name not in phones
    entry = phones.setdefault(name, {"first_seen": now})
    entry["model"] = model
    entry["last_seen"] = now
    _write(phones)
    with open(CURRENT, "w") as f:
        f.write(name)
    return new


def same(a, b):
    """Whether two phone names are the same phone's (AirPlay and Bluetooth both carry the
    name from Settings > General > About; one may write ' where the other writes ’)."""
    import unicodedata
    n = lambda x: unicodedata.normalize("NFKC", x or "").replace("\u2019", "'").casefold().strip()
    return bool(a and b) and n(a) == n(b)


def other_phone(bridge_phone, mirroring):
    """The bridge's connected phone, when it isn't the phone that mirrors; else None. The
    bridge reports a Bluetooth address until it has read the phone's name: until then the
    phone isn't known to be the mirroring one either."""
    import re
    if not mirroring:
        return None
    if not bridge_phone or re.fullmatch(r"[0-9a-f]{2}(:[0-9a-f]{2}){5}", bridge_phone):
        return "an iPhone it hasn't named yet"
    return None if same(bridge_phone, mirroring) else bridge_phone


def current():
    """Name of the phone that is screen mirroring (or last mirrored), or None."""
    try:
        with open(CURRENT) as f:
            return f.read().strip() or None
    except OSError:
        return None


def entry(name=None):
    name = name or current()
    return load().get(name, {}) if name else {}


def drag_key(link):
    """Where a phone's drag table for this link is kept. A table measured over one link is
    wrong for the other (Bluetooth LE moves ~10x less per report than Classic). Linux is
    always Classic, and Windows was always LE until the bridge, so "drag_pts" stays what
    each wrote before; Windows' tables over the bridge (Classic) go in drag_pts_classic."""
    from .config import IS_WINDOWS
    return "drag_pts_classic" if IS_WINDOWS and link == "classic" else "drag_pts"


def needs_calibration(name=None):
    """Whether this phone should be measured on connect: it has no table of its own for
    the link in use and isn't the model that touch.REF_PTS was measured on. Over
    Bluetooth LE (Windows) no phone is: that table was measured over Classic."""
    from .touch import REF_MODEL, link
    e, now = entry(name), link()
    return not e.get(drag_key(now)) and (now == "ble" or e.get("model") != REF_MODEL)


def save_calibration(name, table):
    from .touch import link
    phones = load()
    e = phones.setdefault(name, {"first_seen": time.strftime("%Y-%m-%d %H:%M")})
    e[drag_key(link())] = [[u, p] for u, p in table]
    e["calibrated"] = time.strftime("%Y-%m-%d %H:%M")
    _write(phones)


def stamp():
    """Changes whenever the current phone or the phone list changes."""
    out = []
    for p in (CURRENT, PHONES):
        try:
            out.append(os.stat(p).st_mtime_ns)
        except OSError:
            out.append(0)
    return tuple(out)
