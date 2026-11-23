"""Measure how far one relative mouse report moves this phone's pointer, per
report size. iOS scales relative motion by AssistiveTouch's Tracking Speed, which
differs between phones (and settings), so drags need each phone's own table
(touch.REF_PTS is the iPhone 17 Pro's, at its setting). Saved per phone in
phones.json; touch.py uses the mirroring phone's table.

Each size: the pointer goes to a start point (absolute report, exact on every
phone), N relative reports of that size move it up (button 0: a hover, nothing is
tapped), then it goes back to the start. The pointer is drawn into the mirrored
picture, so the pixels that changed between "at start" and "moved" but match
between "at start" and "back" are the pointer at both places; animations on the
screen differ between the two "at start" frames and drop out.

Upright only: START and the sweeps assume a portrait screen (the pointer moves up it),
so a sideways phone is never measured. when_ready() waits for the phone to be turned
upright (and retries a measurement the screen spoiled); its progress goes to a status
file (status()) that the browser mirror and the Windows app show.
"""
import json
import os
import re
import time

import numpy as np

from . import orientation, phones, screen, touch
from .config import SCREEN_PT, runtime_path

SIZES = [28, 20, 12, 8, 4, 2, 1]  # largest first: each one's travel is aimed from the last
MAX_REPORTS = 150  # ~1.2 s at most per size
START = (0.5, 0.85)
TRAVEL = 0.3  # aim each measurement at about this share of the screen height
SETTLE = 0.35  # the mirror trails the phone by ~0.1-0.2 s
COLUMN = 0.06  # the pointer moves straight up: spots farther than this from START's x aren't it

STATUS_FILE = runtime_path("phoneuse-calibration.json")
LOCK_FILE = runtime_path("phoneuse-calibration.lock")
LOCK_STALE = 300  # s: a lock this old was left by a process that died mid-measurement
WAITING = ("waiting_upright", "waiting_link", "waiting_pointer", "waiting_unlock")
TRIES = 3  # measurements a spoiled screen may cost before giving up


class Sideways(RuntimeError):
    """The phone is (or turned) sideways: calibration measures an upright phone only."""

    def __init__(self, msg="calibration needs the phone upright (portrait); turn it upright and run it again"):
        super().__init__(msg)


def sideways():
    return orientation.is_landscape(orientation.frame_size())


def _check_upright():
    if sideways():
        raise Sideways()


# ---- status, for the pages and the Windows app ----

def set_status(state, phone=None, **extra):
    """Record where calibration stands: one of WAITING, "running" (with step/steps),
    "done", "failed" (with message), or None (nothing to show)."""
    d = {"state": state, "phone": phone, "at": time.time(), "pid": os.getpid(), **extra}
    try:
        tmp = STATUS_FILE + f".{os.getpid()}"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(d, f)
        os.replace(tmp, STATUS_FILE)
    except OSError:
        pass


def status(max_age=None):
    """The latest status (see set_status), or None. A waiting or running status that
    hasn't been renewed for a while was left by a process that stopped: dropped."""
    try:
        with open(STATUS_FILE, encoding="utf-8") as f:
            d = json.load(f)
    except (OSError, ValueError):
        return None
    if not isinstance(d, dict) or not d.get("state"):
        return None
    age = time.time() - float(d.get("at") or 0)
    live = d["state"] in WAITING or d["state"] == "running"
    if (live and age > 60) or (max_age is not None and age > max_age):
        return None
    return d


# ---- one measurement at a time ----

def lock():
    """Take the calibration lock: True if taken, False if another measurement runs."""
    for _ in range(2):
        try:
            fd = os.open(LOCK_FILE, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
            os.write(fd, str(os.getpid()).encode())
            os.close(fd)
            return True
        except FileExistsError:
            try:
                if time.time() - os.path.getmtime(LOCK_FILE) < LOCK_STALE:
                    return False
                os.remove(LOCK_FILE)  # left by a process that died: take it over
            except OSError:
                return False
        except OSError:
            return False
    return False


def unlock():
    try:
        os.remove(LOCK_FILE)
    except OSError:
        pass


def busy():
    try:
        return time.time() - os.path.getmtime(LOCK_FILE) < LOCK_STALE
    except OSError:
        return False


def _settle(seconds=SETTLE):
    time.sleep(seconds)
    return screen.grab_rgb().astype(np.int16)


def _pointer_spots(at_start, moved, back):
    """Centres (fractions) of the pointer-shaped changes: changed between at_start
    and moved, unchanged between at_start and back."""
    if not (at_start.shape == moved.shape == back.shape):
        return []
    h, w = at_start.shape[:2]
    mask = (np.abs(moved - at_start).max(axis=2) > 40) & (np.abs(back - at_start).max(axis=2) < 20)
    spots = []
    for cells, xs, ys in screen._components(mask):
        if cells >= 3:  # a few 4-px cells at least: not noise
            spots.append((cells, float(xs.mean()) / w, float(ys.mean()) / h))
    return spots


def _to_start(h):
    """Put the pointer on START. An absolute report with the same position as the
    previous one is ignored after relative moves (iOS sees no change in the
    absolute axes; measured on an iPhone 15 Pro), so step beside it first."""
    h.cmd(f"a {START[0] + 0.03} {START[1]} 0")
    time.sleep(0.03)
    h.cmd(f"a {START[0]} {START[1]} 0")


def link_waits(h):
    """How many reports have waited for the Bluetooth link to come back (the Windows
    HID service; 0 elsewhere)."""
    m = re.search(r"waited_for_link=(\d+)", h.cmd("status"))
    return int(m[1]) if m else 0


def measure(h, units, reports, tries=3):
    """Points (fraction * SCREEN_PT) that `reports` relative reports of `units`
    moved the pointer straight up, or None if the pointer wasn't found. Measured
    again when the Bluetooth link dropped during the move (Windows: the HID service
    rides out drops, but the motion in reports lost at a drop is gone)."""
    for _ in range(tries):
        _to_start(h)
        at_start = _settle()
        drops = link_waits(h)
        for _ in range(reports):
            h.cmd(f"m 0 {-units} 0 0")
            time.sleep(touch.TICK)
        moved = _settle()
        if link_waits(h) == drops:
            break
    else:
        return None
    _to_start(h)
    back = _settle()
    if not (at_start.shape == moved.shape == back.shape) or orientation.is_landscape(back.shape[1::-1]):
        orientation.refresh()  # the picture changed shape: the phone turned mid-measurement?
        _check_upright()
        return None
    # The pointer moved straight up: only spots above the start, in its column. A
    # text cursor blinking in phase elsewhere (a new note's, top left) passes the
    # three-frame test, and when the pointer had moved too little to see, the
    # farthest spot was the cursor (measured over Bluetooth LE).
    spots = [s for s in _pointer_spots(at_start, moved, back)
             if abs(s[1] - START[0]) < COLUMN and s[2] < START[1]]
    if not spots:
        return None
    # The spot farthest from the start is where the pointer went.
    far = max(spots, key=lambda s: (s[1] - START[0]) ** 2 + (s[2] - START[1]) ** 2)
    dx, dy = (far[1] - START[0]) * SCREEN_PT[0], (far[2] - START[1]) * SCREEN_PT[1]
    if (dx * dx + dy * dy) ** 0.5 < 6:  # that's the start spot itself: it didn't move visibly
        return None
    return -dy


def _ref(units):
    """Points per report in the 17 Pro table (touch.REF_PTS), interpolated."""
    us = [u for u, _ in touch.REF_PTS]
    for (u0, p0), (u1, p1) in zip(touch.REF_PTS, touch.REF_PTS[1:]):
        if u0 <= units <= u1:
            return p0 + (p1 - p0) * (units - u0) / (u1 - u0)
    return touch.REF_PTS[-1][1] * units / us[-1]


def _default(units):
    """Points per report in the table drags use before calibration (touch.default_pts():
    the 17 Pro's over Classic, an LE table on Windows), interpolated."""
    pts = touch.default_pts()
    for (u0, p0), (u1, p1) in zip(pts, pts[1:]):
        if u0 <= units <= u1:
            return p0 + (p1 - p0) * (units - u0) / (u1 - u0)
    return pts[-1][1] * units / pts[-1][0]


def run(h, sizes=SIZES, log=print, alive=None, progress=None):
    """Measure points per report for each size; returns [(units, points)] sorted by
    size, or None if the measurement didn't hold together. Phones differ a lot (an
    iPhone 15 Pro moved 2-22% as far as the 17 Pro table, least for small reports),
    so each size's report count comes from the size measured before it. The first
    guess comes from this link's default table: over Bluetooth LE the 17 Pro table
    guessed 2 reports for 28 units, too few to measure, and the error carried on.
    Portrait only: START and the sweeps assume it (the pointer moves up the screen), so
    this raises Sideways when the phone is sideways, also when it turns partway through
    (nothing is saved then). progress(step, steps) runs before each size."""
    _check_upright()
    table, last = [], None
    for i, u in enumerate(sizes):
        if alive:
            alive()
        if progress:
            progress(i, len(sizes))
        if i:
            _check_upright()
        guess = last[1] * u / last[0] if last else _default(u)
        reports = min(MAX_REPORTS, max(2, round(TRAVEL * SCREEN_PT[1] / guess)))
        moved = measure(h, u, reports)
        if moved is None:
            log(f"  {u:2d} units: pointer not found")
            continue
        per = moved / reports
        last = (u, per)
        table.append((u, round(per, 3)))
        log(f"  {u:2d} units x {reports:3d}: {per:7.3f} pt per report ({per / _ref(u):.2f}x the 17 Pro)")
    _check_upright()  # turned during the last size: its measurement is wrong too
    table.sort()
    if len(table) < len(sizes) - 1 or any(b[1] <= a[1] for a, b in zip(table, table[1:])):
        log("  the sizes didn't grow steadily: something else moved on screen")
        return None
    if not plausible(table):
        log("  bigger reports moved less per unit: the pointer kept landing on the same spots "
            "(it snaps to icons and buttons); measure on a still, empty screen, such as a new note")
        return None
    return table


def plausible(table, slack=0.85):
    """iOS accelerates the pointer, so a bigger report moves at least about as far per
    unit as a smaller one (the 17 Pro table: 3.6 pt per unit at 1, 7.1 at 28). Measured
    on a home screen, where the pointer snaps to app icons, every size seemed to move
    the pointer one row of icons: 16 pt per unit at 1 down to 6 at 28, monotonic and
    wrong."""
    per = [p / u for u, p in table]
    return all(b >= a * slack for a, b in zip(per, per[1:]))


FAILED = "calibrate: failed (the screen kept changing, or the pointer is off); kept the old table"


def calibrate(h, name=None, log=print, alive=None, progress=None):
    """Measure the mirroring phone and save its table. Returns a one-line result.
    alive() runs before each size: it keeps the agent session open, so the browser
    mirror's keep-awake nudges stay off while the measurement runs. Raises Sideways
    when the phone is (or turns) sideways."""
    name = name or phones.current()
    if not name:
        return "calibrate: no phone is mirroring"
    _check_upright()
    if alive:
        alive()
        time.sleep(1.0)  # a keep-awake nudge sent just before the session began lands first
    try:
        table = run(h, log=log, alive=alive, progress=progress)
    finally:
        try:
            _to_start(h)
        except Exception:  # noqa: BLE001 - the link dropped: the result matters more
            pass
    if not table:
        return FAILED
    phones.save_calibration(name, table)
    touch.refresh(force=True)
    k = sorted(p / _ref(u) for u, p in table)
    return (f"calibrated {name}: a report moves the pointer {k[0]:.2f}x-{k[-1]:.2f}x as far as "
            f"on the iPhone 17 Pro; drags and swipes now use this phone's table")


def when_ready(name, get_hid, ready=None, alive=None, wait=180, tries=TRIES, still_wanted=None,
               poll=5.0, log=print, clock=time.time, sleep=time.sleep, upright_wait=None):
    """Measure `name` once it can be: upright, and ready() says nothing else is missing.
    Returns the one-line result, or None when it stopped waiting (gave up, or no
    longer wanted). Keeps the status file current for the pages and the Windows app.

    get_hid(): the link to send reports on. ready(): None when the phone can be
    measured, else the waiting state to show ("waiting_link", "waiting_pointer",
    "waiting_unlock"), or "stop" to give up (the pointer is off: nothing to measure).
    still_wanted(): False stops waiting (another phone mirrors, it got measured).
    `wait` bounds the wait for ready(); time spent sideways doesn't count, so a phone
    left sideways is measured whenever it's turned upright. A measurement the screen
    spoiled (it kept changing) is tried again, up to `tries` times. upright_wait, when
    set, bounds the time spent waiting for upright too (a command someone waits on)."""
    started = clock()
    deadline = started + wait
    failures = 0
    last_state = None

    def show(state, **extra):
        nonlocal last_state
        set_status(state, name, **extra)
        if state != last_state:
            last_state = state
            if state == "waiting_upright":
                log("calibration: waiting for the phone to be turned upright")

    while True:
        if still_wanted and not still_wanted():
            set_status(None, name)
            return None
        if sideways():
            if upright_wait is not None and clock() - started > upright_wait:
                set_status("failed", name, message="The phone stayed sideways. Turn it upright and try again.")
                return None
            show("waiting_upright")
            deadline = max(deadline, clock() + poll * 2)  # sideways time is free
            sleep(poll)
            continue
        why = ready() if ready else None
        if why == "stop":
            set_status(None, name)
            return None
        if why:
            if clock() > deadline:
                set_status("failed", name, message=_WHY.get(why, "the phone wasn't ready"))
                return None
            show(why)
            sleep(poll)
            continue
        if not lock():
            show("running")  # another process measures it now
            sleep(poll)
            continue
        try:
            show("running", step=0, steps=len(SIZES), attempt=failures + 1)
            result = calibrate(get_hid(), name, log=lambda *_: None, alive=alive,
                               progress=lambda i, n: show("running", step=i, steps=n, attempt=failures + 1))
        except Sideways:
            show("waiting_upright")
            continue  # turned mid-measurement: nothing saved; wait for upright again
        finally:
            unlock()
        if result.startswith("calibrated"):
            set_status("done", name, message=result)
            return result
        failures += 1
        if failures >= tries:
            set_status("failed", name, message="The screen kept changing, or the pointer is off. Open a still, "
                       "empty screen (a new note), keep the phone upright and still, and try again.")
            return result
        show("running", step=0, steps=len(SIZES), attempt=failures + 1, retrying=True)
        sleep(poll)


_WHY = {"waiting_link": "the phone's Bluetooth link didn't come up",
        "waiting_pointer": "the pointer didn't show (turn on AssistiveTouch)",
        "waiting_unlock": "the phone stayed locked"}
