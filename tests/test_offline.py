"""Offline tests (no phone, no services): run with
    .venv\\Scripts\\python -m pytest tests\\test_offline.py      (or: python tests\\test_offline.py)
"""
import os
import struct
import sys

import numpy as np

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from phoneuse import calibrate, hidproto, rtp, touch  # noqa: E402

W, H = 804, 1748  # a screenshot: 2 px per point


def screen_with_pointer(x, y, noise=0):
    """A light screen with the AssistiveTouch pointer (a translucent grey disc) at (x, y)."""
    img = np.full((H, W, 3), 250, np.int16)
    yy, xx = np.mgrid[0:H, 0:W]
    disc = (xx - x * W) ** 2 + (yy - y * H) ** 2 <= (18 * 2) ** 2
    img[disc] = 140
    if noise:
        rng = np.random.default_rng(1)
        img += rng.integers(-noise, noise + 1, img.shape).astype(np.int16)
    return img


def test_calibration_finds_the_pointer_where_it_moved():
    start = calibrate.START
    at_start, back = screen_with_pointer(*start), screen_with_pointer(*start, noise=6)
    moved = screen_with_pointer(start[0], start[1] - 0.2, noise=6)
    spots = calibrate._pointer_spots(at_start, moved, back)
    far = max(spots, key=lambda s: (s[1] - start[0]) ** 2 + (s[2] - start[1]) ** 2)
    assert abs(far[1] - start[0]) < 0.01 and abs(far[2] - (start[1] - 0.2)) < 0.01


def test_calibration_ignores_what_changed_on_its_own():
    # Something that differs between the two frames at the start (an animation) isn't
    # the pointer, even where the pointer went.
    start = calibrate.START
    at_start, moved = screen_with_pointer(*start), screen_with_pointer(start[0], start[1] - 0.2)
    back = screen_with_pointer(*start)
    back[300:340, 100:140] = 0  # changed between at_start and back
    moved[300:340, 100:140] = 0
    spots = calibrate._pointer_spots(at_start, moved, back)
    assert all(not (0.12 < x < 0.18 and 0.16 < y < 0.2) for _, x, y in spots)


def test_calibration_tables_that_make_sense():
    # Measured: the 17 Pro over Classic, an iPhone 14 Pro Max over Bluetooth LE on a
    # blank note, and the same phone on its home screen (pointer snapping to icons).
    classic = [(1, 3.6), (2, 6.9), (4, 17.6), (8, 46.6), (12, 76.3), (20, 141.8), (28, 199.7)]
    le_note = [(1, 0.23), (2, 0.595), (4, 1.646), (8, 4.755), (12, 9.326), (20, 22.947), (28, 42.743)]
    home_screen = [(1, 16.333), (2, 27.638), (4, 47.79), (8, 84.8), (12, 114.279), (20, 173.1), (28, 174.873)]
    assert calibrate.plausible(classic) and calibrate.plausible(le_note)
    assert not calibrate.plausible(home_screen)


def rtp_packet(seq, ts, payload, marker=False, ssrc=1234):
    return struct.pack("!BBHII", 0x80, (0x80 if marker else 0) | 96, seq & 0xFFFF, ts, ssrc) + payload


def test_rtp_single_stap_and_fua():
    sps, pps = b"\x67\x64\x00\x1f\xaa", b"\x68\xee\x3c\x80"
    idr = b"\x65" + bytes(range(256)) * 20
    d = rtp.Depacketizer()
    stap = b"\x18" + struct.pack("!H", len(sps)) + sps + struct.pack("!H", len(pps)) + pps
    out = d.push(rtp_packet(1, 100, stap))
    # FU-A in three fragments
    body = idr[1:]
    frags = [body[:1000], body[1000:3000], body[3000:]]
    fu_ind = (idr[0] & 0xE0) | 28
    for i, f in enumerate(frags):
        header = (0x80 if i == 0 else 0) | (0x40 if i == 2 else 0) | (idr[0] & 0x1F)
        out += d.push(rtp_packet(2 + i, 100, bytes([fu_ind, header]) + f, marker=(i == 2)))
    assert len(out) == 1
    assert rtp.nal_types(out[0]) == [7, 8, 5]
    assert out[0].endswith(idr[-50:])
    # next frame: a single NAL unit
    p = b"\x41" + b"\x01" * 100
    out = d.push(rtp_packet(5, 200, p, marker=True))
    assert out == [rtp.START + p] and d.lost == 0


def test_rtp_loss_drops_fragment_and_counts():
    d = rtp.Depacketizer()
    idr = b"\x65" + b"\x02" * 3000
    fu_ind = (idr[0] & 0xE0) | 28
    d.push(rtp_packet(1, 100, bytes([fu_ind, 0x80 | 5]) + idr[1:1000]))
    # packet 2 lost; 3 is the end fragment
    out = d.push(rtp_packet(3, 100, bytes([fu_ind, 0x40 | 5]) + idr[2000:], marker=True))
    assert d.lost == 1 and out == []  # nothing usable in that frame
    out = d.push(rtp_packet(4, 200, b"\x41\x09", marker=True))
    assert out == [rtp.START + b"\x41\x09"]


def test_rtp_new_stream_resets():
    d = rtp.Depacketizer()
    d.push(rtp_packet(10, 100, b"\x41\x01", marker=True))
    out = d.push(rtp_packet(500, 9, b"\x41\x02", marker=True, ssrc=99))
    assert out == [rtp.START + b"\x41\x02"] and d.restarts == 1 and d.lost == 0


def test_hid_tables():
    assert hidproto.absolute(0, 1, 1) == [1, 0, 0, 0xFF, 0x7F]
    assert hidproto.key_code("h", ["gui"]) == (11, 0x08)
    assert hidproto.key_code("enter") == (40, 0)
    assert hidproto.passive("a", ["0.5", "0.5", "0"]) and not hidproto.passive("a", ["0.5", "0.5", "1"])
    assert len(hidproto.REPORT_MAP) > 100 and set(hidproto.REPORT_SIZES) == {1, 2, 3, 4}


def test_absolute_nudge_after_relative_motion():
    n = hidproto.AbsoluteNudge()
    park = hidproto.absolute(0.15, 0.3, 0)
    assert n.before(3, park) == []  # first report: nothing before it
    assert n.before(3, park) == []  # same spot, no motion between: nothing to fix
    assert n.before(2, [0, 8, 0, 0]) == []
    extra = n.before(3, park)  # same spot after a drag: a nudge first
    assert len(extra) == 1 and extra[0][1:] != park[1:] and extra[0][3:] == park[3:]
    assert abs((extra[0][1] | extra[0][2] << 8) - (park[1] | park[2] << 8)) == 8
    assert n.before(3, park) == []  # reset by the absolute report
    n.before(2, [0, 0, 0, 1])  # wheel only: the pointer didn't move
    assert n.before(3, park) == []
    n.before(2, [0, 0, 3, 0])
    assert n.before(3, hidproto.absolute(0.5, 0.5, 0)) == []  # a new spot moves it anyway
    edge = hidproto.absolute(0, 0.5, 0)
    n.before(3, edge)
    n.before(2, [0, 0xF8, 0, 0])
    nudged = n.before(3, edge)[0]
    assert (nudged[1] | nudged[2] << 8) == 8  # at x = 0 the nudge goes right


def test_screen_points_from_model():
    import tempfile
    from phoneuse import config
    saved = config.MODEL_FILE, config.CFG.get("screen_points")
    with tempfile.TemporaryDirectory() as d:
        config.MODEL_FILE = os.path.join(d, "mirroring-model")
        try:
            config.CFG["screen_points"] = "auto"
            assert config.screen_points() == (config.DEFAULT_POINTS, "no phone has mirrored yet")
            with open(config.MODEL_FILE, "w", encoding="utf-8") as f:
                f.write("iPhone15,3\n")  # iPhone 14 Pro Max, measured: a 1206 x 2614 picture
            assert config.screen_points() == ((430, 932), "iPhone15,3")
            with open(config.MODEL_FILE, "w", encoding="utf-8") as f:
                f.write("iPhone18,2")  # iPhone 17 Pro Max, measured: a 1206 x 2620 picture
            assert config.screen_points() == ((440, 956), "iPhone18,2")
            with open(config.MODEL_FILE, "w", encoding="utf-8") as f:
                f.write("iPhone99,9")
            assert config.screen_points() == (config.DEFAULT_POINTS, "unknown model iPhone99,9")
            config.CFG["screen_points"] = [375, 667]
            assert config.screen_points() == ((375, 667), "config.toml")
        finally:
            config.MODEL_FILE, config.CFG["screen_points"] = saved
    # Every model maps to one size, and the measured pictures have those shapes.
    assert len(config.MODEL_POINTS) == sum(len(m.split()) for m in config._SCREENS.values())
    for (w, h), (pw, ph) in (((1206, 2614), (430, 932)), ((1206, 2620), (440, 956))):
        assert abs((w / h) / (pw / ph) - 1) < 0.0005


def test_spotlight_tap():
    from phoneuse import cli
    H = 932  # iPhone 14 Pro Max, points
    head = (0.116, 0.088, "Top Hit")
    # Read from the phone (iOS 26.5): the top hit's icon above its name, sometimes with
    # text on the icon itself; a tap on the name didn't open Weather, one on the icon did.
    weather = [head, (0.173, 0.216, "Weather"), (0.391, 0.216, "Q weather"), (0.170, 0.279, "Suggestions")]
    clock = [head, (0.172, 0.145, "12"), (0.172, 0.216, "Clock"), (0.391, 0.214, "Set Timer")]
    calendar = [head, (0.171, 0.144, "Mon"), (0.170, 0.173, "9"), (0.172, 0.216, "Calendar")]
    camera = [head, (0.391, 0.163, "日"), (0.172, 0.216, "Camera"), (0.391, 0.214, "Selfie")]
    for lines, name in ((weather, "Weather"), (clock, "Clock"), (calendar, "Calendar"), (camera, "Camera")):
        x, y = cli.spotlight_tap(lines, name, H)
        assert abs(x - 0.172) < 0.002 and abs(y - (0.216 - 47 / H)) < 0.002, (name, x, y)
    # A list row right under the title, and an app in the Apps section below another
    # top hit: their names are tapped.
    row = [head, (0.30, 0.13, "Weather"), (0.30, 0.19, "Weather Radar")]
    assert cli.spotlight_tap(row, "Weather", H) == (0.30, 0.13)
    apps = [head, (0.172, 0.216, "Mail"), (0.08, 0.30, "Apps"), (0.172, 0.40, "Gmail")]
    assert cli.spotlight_tap(apps, "Gmail", H) == (0.172, 0.40)
    # Exact names only; no Spotlight titles on the screen: not Spotlight (the home
    # screen's own icon labels matched once).
    assert cli.spotlight_tap(weather, "Weath", H) == "missing"
    assert cli.spotlight_tap([(0.27, 0.75, "Weather"), (0.5, 0.9, "Search")], "Weather", H) == "closed"
    assert cli.spotlight_tap([head, (0.172, 0.216, "Google Ma…")], "Google Maps", H)[0] == 0.172


def test_drag_table_round_trip():
    for units in (1, 3, 7.5, 16, 28):
        assert abs(touch.units_for(touch.points_for(units)) - units) < 1e-6
    ux, uy = touch.whole_units(100.0, 0.0)
    assert ux > 0 and uy == 0 and touch.points_for(ux) <= 100.0


def test_gesture_steps():
    from phoneuse import touch
    steps = touch.parse_gesture("down 0.2 0.3, hold 1; move 0.7 0.6 1.2 hold 0.5")
    assert steps == [("down", 0.2, 0.3), ("hold", 1.0), ("move", 0.7, 0.6, 1.2), ("hold", 0.5), ("up",)]
    assert touch.parse_gesture("down 0.5 0.5 move 0.5 0.1 up")[1] == ("move", 0.5, 0.1, touch.MOVE_SECONDS)
    for bad in ("move 0.5 0.5", "down 0.5", "down 0.1 0.1 down 0.2 0.2", "up", "hold 1", "down 0.5 0.5 jiggle"):
        try:
            touch.parse_gesture(bad)
        except touch.GestureError:
            continue
        raise AssertionError(f"accepted {bad!r}")


def test_gesture_still_press_is_one_timed_tap():
    from phoneuse import touch
    sent = []

    class Hid:
        def cmd(self, line):
            sent.append(line)
            return "ok"
    touch.gesture(Hid(), touch.parse_gesture("down 0.5 0.4 hold 0.8 up"))
    assert sent == ["a 0.50000 0.40000 0 sure", "tap 0.50000 0.40000 0.800"]


def test_recipe_values():
    import tempfile
    from phoneuse import recipes, touch
    saved_file = recipes.FILE
    recipes.FILE = os.path.join(tempfile.mkdtemp(), "recipes.json")
    assert recipes.expand("down $1 $2 hold $3=0.8 up", ["0.3", "0.4"]) == "down 0.3 0.4 hold 0.8 up"
    recipes.save("move-icon", "down $1 $2 hold 1 move $3 $4 1 up", "drag an icon")
    assert recipes.get("move-icon")[0] == "down $1 $2 hold 1 move $3 $4 1 up"
    for args in (["0.1"], ["0.1", "0.2", "0.3", "0.4", "0.5"], ["a", "b", "c", "d"]):
        try:
            recipes.expand(recipes.get("move-icon")[0], args)
        except touch.GestureError:
            continue
        raise AssertionError(f"accepted {args}")
    recipes.forget("move-icon")
    assert "move-icon" not in recipes.every()
    recipes.FILE = saved_file


def test_film_merges_still_frames_and_fits_budget():
    from PIL import Image
    from phoneuse import film
    a, b = Image.new("RGB", (804, 1748), (20, 20, 20)), Image.new("RGB", (804, 1748), (200, 200, 200))
    frames = [("hold 0.4", (0.2, 0.3, True), a), ("move", (0.7, 0.6, True), a), ("hold 0.5", (0.7, 0.6, True), a),
              ("up +0.3s", (0.7, 0.6, False), b), ("up +0.8s", (0.7, 0.6, False), b)]
    kept = film.keep_changed(frames)
    # the hold after the move shows the same picture with the finger in the same place
    assert [k[0] for k in kept] == ["hold 0.4", "move, hold 0.5", "up +0.3s, up +0.8s"]
    img, legend = film.compose(kept)
    assert img.width * img.height <= film.BUDGET * 1.05 and img.height <= film.MAX_H
    assert legend.startswith("3 frames")
    many = [(f"move {i}", (i / 20, 0.5, True), a if i % 2 else b) for i in range(20)]
    assert len(film.keep_changed(many)) == film.MAX_FRAMES


def test_reconnect_loop_waits_while_released():
    try:
        from phoneuse import hid_daemon as d
    except ImportError:  # Linux only (GLib)
        return

    class Stop(Exception):
        pass

    class Event:  # records the first wait's timeout, then ends the loop
        def wait(self, timeout):
            waits.append(timeout)
            raise Stop

    real = d.reconnect_now
    d.reconnect_now = Event()
    try:
        for released, expected in ((True, 30), (False, 0)):  # released waits; disconnected dials at once
            waits = []
            (d.released.set if released else d.released.clear)()
            try:
                d.reconnect_loop()
            except Stop:
                pass
            assert waits == [expected], (released, waits)
    finally:
        d.reconnect_now = real
        d.released.clear()


if __name__ == "__main__":
    fails = 0
    for name, fn in list(globals().items()):
        if name.startswith("test_"):
            try:
                fn()
                print("PASS", name)
            except AssertionError as e:
                fails += 1
                print("FAIL", name, e)
    sys.exit(1 if fails else 0)


def test_companion_serves_only_the_mirroring_phones_app(monkeypatch):
    from phoneuse import companion, phones
    store = {"devices": {"mine": {"key": "00"}, "hers": {"key": "01"}, "old": {"key": "02"}}}
    monkeypatch.setattr(companion, "load", lambda: store)
    monkeypatch.setattr(companion, "save", lambda d: None)
    mirroring = ["LXEPhone17Pro"]
    monkeypatch.setattr(phones, "current", lambda: mirroring[0])

    class Mirror:
        def live(self):
            return mirroring[0] is not None

    c = companion.Companion.__new__(companion.Companion)
    c.mirror = Mirror()
    c.note_captured("mine", True)       # my app, while my phone mirrors
    mirroring[0] = "Meng\u2019s iPhone"
    c.note_captured("hers", True)       # her app, while hers mirrors
    assert not c.serves("mine")         # my app is tied to my phone: left out, even without reporting again
    assert c.serves("hers")
    assert c.serves("old")              # never reported: served as before
    c.note_captured("old", False)       # it says its screen isn't the mirroring one
    assert not c.serves("old")
    mirroring[0] = "LXEPhone17Pro"
    assert c.serves("mine") and not c.serves("hers")
    assert c.serves("old")              # it only said it isn't Meng's iPhone
    mirroring[0] = None
    assert c.serves("mine") and c.serves("hers")  # nothing mirrors: every app sees the PC


def test_aim_finds_the_pointer_from_a_short_sideways_slide(monkeypatch):
    from phoneuse import aim
    from phoneuse.config import SCREEN_PT
    px = W / SCREEN_PT[0]

    def frame(x, y):  # a dark screen with an 18 pt grey pointer at (x, y) fractions
        img = np.zeros((H, W, 3), np.int16)
        yy, xx = np.mgrid[0:H, 0:W]
        img[(xx - x * W) ** 2 + (yy - y * H) ** 2 <= (9 * px) ** 2] = 130
        return img

    real = [0.30, 0.20]  # where the pointer is; the drag thinks it's at 0.32
    frames = iter([frame(*real), frame(real[0] + 10 / SCREEN_PT[0], real[1])])  # slides 10 pt, not 14
    monkeypatch.setattr(aim, "_frame", lambda: next(frames))

    class T:
        pos = (0.32, 0.20)
    t = T()
    monkeypatch.setattr(aim, "_slide", lambda t, fx, fy: setattr(t, "pos", (fx, fy)))
    x, y = aim.locate(t)
    assert abs((x - real[0]) * SCREEN_PT[0] - 10) < 2.5 and abs(y - real[1]) * SCREEN_PT[1] < 2.5


def test_exact_gesture_corrects_drags_only(monkeypatch):
    from phoneuse import aim, touch
    calls = []
    monkeypatch.setattr(aim, "correct", lambda t, target, log=None: calls.append(target))
    monkeypatch.setattr(touch, "_move", lambda t, x, y, s: setattr(t, "target", (x, y)))

    class Hid:
        def cmd(self, line):
            return "ok"
    monkeypatch.setattr(touch.Touch, "press", lambda t, x, y: (setattr(t, "pos", (x, y)), setattr(t, "down", True)))
    monkeypatch.setattr(touch.Touch, "release", lambda t: setattr(t, "down", False))
    touch.gesture(Hid(), touch.parse_gesture("down 0.5 0.4 hold 0.3 up"), exact=True)
    assert calls == []
    touch.gesture(Hid(), touch.parse_gesture("down 0.2 0.3 move 0.7 0.6 0.5"), exact=True)
    assert calls == [(0.7, 0.6)]


def test_scroll_by_half_screens_never_reads_as_points(monkeypatch):
    from phoneuse import cli, touch
    drags = []
    monkeypatch.setattr(touch, "scroll_drag", lambda h, x0, y0, x1, y1: drags.append(y1 - y0))
    for n in (1, 2, 4, 6):
        drags.clear()
        cli.scroll(None, "up", n)
        assert abs(sum(drags) - 0.45 * n) < 1e-6, (n, drags)


def test_status_bar_hides_its_own_items_but_not_buttons():
    from phoneuse.cli import status_bar_item
    assert status_bar_item(0.03, "4:43") and status_bar_item(0.03, "100%") and status_bar_item(0.03, "5G")
    assert not status_bar_item(0.035, "Done") and not status_bar_item(0.035, "Edit")
    assert not status_bar_item(0.2, "5G")


def test_moves_to_an_edge_push_past_it(monkeypatch):
    from phoneuse import touch
    sent = []

    class Hid:
        def cmd(self, line):
            sent.append(line)
            return "ok"
    t = touch.Touch(Hid())
    t.pos = t.target = (0.5, 0.3)
    t.down = True
    touch._move(t, 0.6, 0.3, 0.05)
    inner = len(sent)
    touch._move(t, 1.0, 0.3, 0.05)  # to the right edge: more reports than the distance alone
    assert t.pos == (1.0, 0.3)
    xs = [int(l.split()[1]) for l in sent[inner:] if l.startswith("m ")]
    assert sum(touch.points_for(abs(x)) for x in xs) >= 0.4 * 393 + touch.EDGE_PUSH * 0.8
