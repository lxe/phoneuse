"""Which way the phone faces, from the shape of its picture: the one place that knows.

The phone's screen size in config (SCREEN_PT) is portrait. When the phone turns sideways
the AirPlay frames become wider than tall; the frames service reports their size in its
status line, and everything that lays the phone out or plans a gesture in points asks
here, so a landscape picture is never squeezed into a portrait box.
"""
import re
import threading
import time

from . import ipc
from .config import SCREEN_PT

STALE = 2.0  # s: how long a frame size we asked for is trusted before asking again

_lock = threading.Lock()
_size = None  # (width, height) of the latest frame we know of, or None
_at = 0.0


def parse_size(line):
    """(w, h) from a status line's "size=WxH", or None when it has none ("size=-" too)."""
    m = re.search(r"\bsize=(\d+)x(\d+)", line or "")
    return (int(m[1]), int(m[2])) if m else None


def is_landscape(size):
    return bool(size) and size[0] > size[1]


def points_for(size, portrait=SCREEN_PT):
    """The screen in points as it currently faces: the portrait size, swapped when a frame
    of `size` is wider than tall. Unknown size (no picture yet): portrait."""
    return (portrait[1], portrait[0]) if is_landscape(size) else tuple(portrait)


def note_size(size):
    """Record a frame size learned elsewhere (the browser mirror's own polling)."""
    global _size, _at
    with _lock:
        _size, _at = size, time.time()


def refresh(timeout=0.5):
    """Ask the frames service for the frame size now. Keeps the old one if it can't answer."""
    global _at
    try:
        line, _ = ipc.request("frames", "status", timeout)
    except (OSError, RuntimeError, ValueError):  # not running, no token, a garbled reply
        _at = time.time()  # don't ask again on every call while it's down
        return _size
    size = parse_size(line)
    note_size(size)
    return size


def frame_size():
    if time.time() - _at > STALE:
        refresh()
    return _size


def current_points():
    """The screen in points for the way the phone faces now."""
    return points_for(frame_size())


def hid_fractions(fx, fy, landscape=False):
    """Screen fractions (of the picture as shown) to the fractions of the HID absolute
    pointer's 0..1 range.

    UNVERIFIED: we assume iOS maps the absolute pointer onto the screen as it currently
    faces, so a landscape picture's fractions go out unchanged. If it turns out to map onto
    the portrait panel, the rotation goes here (and only here), using `landscape`, and
    callers (hidproto.absolute) must then pass the real orientation, which the HID
    services don't know yet."""
    return fx, fy
