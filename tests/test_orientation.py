"""Offline tests for landscape support (orientation.py and the pure functions that use it):
    python -m pytest tests/test_orientation.py
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from phoneuse import hidproto, orientation, screen  # noqa: E402

PORTRAIT = (402, 874)


def test_size_comes_from_a_status_line():
    assert orientation.parse_size("ok live=1 seq=4 size=1170x2532") == (1170, 2532)
    assert orientation.parse_size("ok live=1 mirroring=1 size=2532x1170 encoder=x") == (2532, 1170)
    assert orientation.parse_size("ok live=0 seq=0 size=-") is None
    assert orientation.parse_size("ok live=1 seq=3") is None  # an older service
    assert orientation.parse_size("") is None


def test_points_swap_only_for_a_wide_frame():
    assert orientation.points_for((1170, 2532), PORTRAIT) == PORTRAIT
    assert orientation.points_for((2532, 1170), PORTRAIT) == (874, 402)
    assert orientation.points_for(None, PORTRAIT) == PORTRAIT
    assert orientation.points_for((1000, 1000), PORTRAIT) == PORTRAIT  # square is not sideways


def test_window_and_picture_follow_the_shape():
    up = screen.window_size(500, PORTRAIT)
    side = screen.window_size(500, (874, 402))
    assert up[1] > up[0] and side == (up[1], up[0])  # the same phone, turned
    # a sideways picture in a sideways window fills it up to the bezel, never squeezed, and
    # the bezel is the upright one (a fraction of the short side)
    x, y, w, h = screen.picture_rect((0, 0), side, (874, 402))
    assert abs(w / h - 874 / 402) < 1e-6
    assert abs(x - screen.BEZEL * h) < 1 and abs(y - screen.BEZEL * h) < 1
    _, _, uw, _ = screen.picture_rect((0, 0), up, PORTRAIT)
    assert abs(h - uw) < 1  # the screen's short side is as long as when upright
    # an upright picture in the same window is aspect-fit with a wider bezel
    _, _, w2, h2 = screen.picture_rect((0, 0), side, PORTRAIT)
    assert abs(w2 / h2 - 402 / 874) < 1e-6 and h2 < side[1]


def test_hid_mapping_is_unchanged_in_landscape():
    # Unverified assumption (orientation.hid_fractions): fractions go out as they are.
    assert orientation.hid_fractions(0.25, 0.75, landscape=True) == (0.25, 0.75)
    assert hidproto.absolute(0.5, 1.0, 0)[1:] == [0x00, 0x40, 0xFF, 0x7F]
