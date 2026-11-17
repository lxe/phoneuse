"""End a drag where it was sent: find the real pointer in the mirrored picture before
the lift, and slide the held finger the rest of the way.

While a finger is down, iOS takes only relative motion (an absolute report with a
new position ends the touch), and turns each report into a distance with its pointer
acceleration and the phone's Tracking Speed. Drags aim with the phone's calibration
table (touch.points_for), which leaves a few points of error, more on a phone with a
low Tracking Speed (measured: median 5 pt on an iPhone 17 Pro, 11 pt on an iPhone 15
Pro set slow). The picture shows where the pointer really is, so this looks before
lifting and corrects.

Finding the pointer: two frames, finger still down: "at", then the pointer slid a few
points sideways ("moved"). The pixels that changed are the pointer at both places, two
discs that overlap when the slide was short. The changed area's height is the pointer's
diameter, and its edge on the side it slid to is half a diameter from where the pointer
is now. (The slide's own length isn't known exactly, the very error being corrected, so
it isn't used.) Content that moves with the finger (a scroll, a dragged icon) changes far
more than a pointer-sized area: then nothing is found and the drag lifts uncorrected, as
before.
"""
import time

from . import screen, touch
from .calibrate import SETTLE

NUDGE = 14    # pt the pointer slides sideways to be seen at two places (overlapping is fine)
SEARCH = 45   # pt from where the drag aimed: a spot farther away isn't the pointer
CLOSE = 3     # pt: near enough, no correction
ROUNDS = 2    # look, correct, look again


def _frame():
    import numpy as np
    time.sleep(SETTLE)  # the mirror trails the phone
    return screen.grab_rgb().astype(np.int16)


def _slide(t, fx, fy, timeout=2.0):
    """Move the held finger to (fx, fy) by the shortest relative steps, slowly at the end."""
    t.target = (fx, fy)
    end = time.time() + timeout
    while time.time() < end:
        if t.step() < max(1.0, touch.DRAG_PTS[0][1]):
            return
        time.sleep(touch.TICK)


def locate(t):
    """Where the held finger's pointer really is (screen fractions), or None when the
    picture doesn't show it clearly. Slides the finger NUDGE points sideways to look."""
    import numpy as np
    gx, gy = t.pos
    pw, ph = t.pts
    side = 1 if gx < 0.5 else -1  # toward the middle
    at = _frame()
    _slide(t, gx + side * NUDGE / pw, gy)
    moved = _frame()
    if at.shape != moved.shape:
        return None
    h, w = at.shape[:2]
    px = w / pw  # pixels per point
    near = [(xs, ys) for c, xs, ys in screen._components(np.abs(moved - at).max(axis=2) > 40)
            if c >= 3 and abs(xs.mean() / px - t.pos[0] * pw) < SEARCH
            and abs(ys.mean() / px - gy * ph) < SEARCH]
    if not near:
        return None
    xs = np.concatenate([x for x, _ in near])
    ys = np.concatenate([y for _, y in near])
    cell = 4  # screen._components' grid
    top, bottom = ys.min() - cell / 2, ys.max() + cell / 2
    left, right = xs.min() - cell / 2, xs.max() + cell / 2
    d = bottom - top  # the pointer's diameter: the slide was sideways
    if not (8 * px <= d <= 30 * px) or right - left > d + (NUDGE * 3) * px:
        return None  # not pointer-sized: content moved with the finger, or something else changed
    cx = (right - d / 2) if side > 0 else (left + d / 2)
    return cx / w, (top + bottom) / 2 / h


def correct(t, target, log=None):
    """Before the lift: slide the held finger until the pointer sits on `target`. Each round
    looks (which slides the pointer NUDGE aside) and then slides to the target from where
    the pointer really was, so a round always ends with a correction. Returns the errors
    seen, in points (an empty list when the pointer wasn't found)."""
    errs = []
    for _ in range(ROUNDS):
        real = locate(t)
        if real is None:
            break
        ex, ey = (target[0] - real[0]) * t.pts[0], (target[1] - real[1]) * t.pts[1]
        errs.append(round((ex * ex + ey * ey) ** 0.5, 1))
        t.pos = real  # the truth, from the picture
        _slide(t, *target)
        if log:
            log(f"aim: {errs[-1]:.0f} pt off before the look's nudge was undone ({ex:+.0f}, {ey:+.0f})")
        # the look's own nudge is in this error; the first round's slide fixes the drag's error
        if len(errs) > 1 and abs(errs[-1] - NUDGE) <= CLOSE:
            break  # the pointer was on target before this look: done
    return errs
