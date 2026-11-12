"""Film of a gesture: frames of the phone taken while the gesture runs, in one image.

touch.gesture calls Film.mark at the end of each step (the end of a move, the end
of a hold with the finger down, just before a long press lifts) and once the
finger is up, for frames 0.3 and 0.8 s after the lift. A thread takes each frame
from the frames service ("shot") at its time and keeps the PNG bytes; decoding
waits until the gesture is over, so the drag's 8 ms reports aren't held up.

compose() tiles the frames into one image of about 750 tokens. Each frame carries
its step label and a ring where the finger is (orange with a centre dot while it
is down, white once it is up). A frame
identical to the one before it, with the finger in the same place, is left out.
The mirror shows the phone about 0.1 s late, so a frame shows the phone a moment
before its label's time.
"""
import io
import queue
import threading
import time

import numpy as np
from PIL import Image, ImageDraw, ImageFont

from . import screen
from .config import SCREEN_PT

AFTER = (0.3, 0.8)  # s after the lift
BUDGET = 560_000  # pixels in the composed image (~750 tokens)
MAX_H = 583  # frame height at most (2/3 of the screen in points)
MAX_FRAMES = 12


class Film:
    def __init__(self):
        self.jobs = queue.Queue()
        self.shots = []  # (label, finger (x, y, down), png bytes)
        self.thread = threading.Thread(target=self._run, daemon=True)
        self.thread.start()

    def mark(self, label, finger, down, delay=0.0):
        """Take a frame `delay` seconds from now, labelled, with the finger at `finger`
        (screen fractions) and down or up."""
        self.jobs.put((time.time() + delay, label, (finger[0], finger[1], down)))

    def lifted(self, finger):
        """The finger is up: frames AFTER seconds from now."""
        for a in AFTER:
            self.mark(f"up +{a:g}s", finger, False, a)

    def _run(self):
        while True:
            job = self.jobs.get()
            if job is None:
                return
            at, label, finger = job
            time.sleep(max(0.0, at - time.time()))
            try:
                line, png = screen.frames_request("shot")
            except OSError:
                continue
            if line.startswith("ok"):
                self.shots.append((label, finger, png))

    def finish(self):
        """Wait for the frames still due; returns [(label, finger, PIL image)]."""
        self.jobs.put(None)
        self.thread.join(timeout=5)
        return [(label, finger, Image.open(io.BytesIO(png)).convert("RGB")) for label, finger, png in self.shots]


def _small(img):
    return np.asarray(img.convert("L").resize((50, 109)), dtype=np.int16)


def keep_changed(frames):
    """Leave out frames that show the same picture, finger and finger state as the
    one kept before them: their labels join that frame's."""
    kept = []
    for label, finger, img in frames:
        if kept:
            plabel, pfinger, pimg = kept[-1]
            same_finger = pfinger[2] == finger[2] and abs(pfinger[0] - finger[0]) < 0.01 \
                and abs(pfinger[1] - finger[1]) < 0.01
            if same_finger and (np.abs(_small(img) - _small(pimg)) > 12).mean() <= 0.004:
                kept[-1] = (f"{plabel}, {label}", pfinger, pimg)
                continue
        kept.append((label, finger, img))
    if len(kept) > MAX_FRAMES:  # the first, the last and evenly between
        idx = np.linspace(0, len(kept) - 1, MAX_FRAMES).round().astype(int)
        kept = [kept[i] for i in sorted(set(idx))]
    return kept


def _font(size):
    try:
        return ImageFont.load_default(size=size)
    except TypeError:  # Pillow before 10.1
        return ImageFont.load_default()


def compose(frames):
    """One image of the frames left to right, and a legend. None when there are none."""
    if not frames:
        return None, "no frames (is the mirror showing the phone?)"
    n = len(frames)
    aspect = SCREEN_PT[0] / SCREEN_PT[1]
    h = int(min(MAX_H, (BUDGET / (n * aspect)) ** 0.5))
    w = int(h * aspect)
    gap = 6
    out = Image.new("RGB", (n * w + (n - 1) * gap, h), (40, 40, 40))
    draw = ImageDraw.Draw(out)
    font = _font(max(11, h // 28))
    r = max(6, h // 40)
    for i, (label, (fx, fy, down), img) in enumerate(frames):
        x0 = i * (w + gap)
        out.paste(img.resize((w, h), Image.LANCZOS), (x0, 0))
        cx, cy = x0 + fx * w, fy * h
        # an open ring, so what is under the finger stays visible: orange with a centre
        # dot while the finger is down, thin and white once it is up
        ring = (255, 120, 0) if down else (255, 255, 255)
        draw.ellipse((cx - r - 2, cy - r - 2, cx + r + 2, cy + r + 2), outline=(0, 0, 0), width=2)
        draw.ellipse((cx - r, cy - r, cx + r, cy + r), outline=ring, width=3 if down else 2)
        if down:
            draw.ellipse((cx - 2, cy - 2, cx + 2, cy + 2), fill=ring)
        text = f"{i + 1} {label}"
        tw = draw.textlength(text, font=font)
        draw.rectangle((x0, h - font.size - 8, x0 + tw + 8, h), fill=(0, 0, 0))
        draw.text((x0 + 4, h - font.size - 5), text, font=font, fill=(255, 255, 255))
    legend = (f"{n} frame{'s' if n != 1 else ''}, left to right: "
              + " · ".join(f"{i + 1} {label} (finger {fx:.3f} {fy:.3f}, {'down' if down else 'up'})"
                           for i, (label, (fx, fy, down), _) in enumerate(frames))
              + ". Ring: the finger (orange with a dot: down; white: up). The mirror shows the phone ~0.1 s late.")
    return out, legend
