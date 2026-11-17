"""The Windows HID service's bridge path (windows/hid.py), against a fake bridge on
127.0.0.1 (fake_bridge.py): no Bluetooth is published, no phone or bridge needed.
    .venv\\Scripts\\python -m pytest tests\\test_windows_bridge.py
"""
import asyncio
import os
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

pytestmark = pytest.mark.skipif(sys.platform != "win32", reason="the Windows HID service")

KEY = bytes(range(32))


def run(coro):
    return asyncio.run(coro)


def make_hid():
    """A BleHid whose LE publishing is only recorded: nothing reaches this PC's Bluetooth."""
    from phoneuse.windows import hid as whid
    h = whid.BleHid(asyncio.get_running_loop())
    h.published = []

    async def start():
        h.published.append("open")
    h.start = start
    h.mirror_name = "Test iPhone"
    return h


def test_input_goes_through_the_bridge_and_back():
    from fake_bridge import FakeBridge
    from phoneuse.hidproto import absolute
    from phoneuse.windows import hid as whid

    async def go(fake):
        h = make_hid()
        assert " link=ble" in await h.run("status")
        r = await h.run(f"bridge 127.0.0.1 {KEY.hex()}")
        assert r.startswith("ok bridge 127.0.0.1") and "connected=True" in r
        st = await h.run("status")
        assert " link=bridge" in st and "bridge=127.0.0.1" in st and "connected=True" in st
        assert " le=closed" in st  # bridge mode: this PC's own LE keyboard is closed

        assert await h.run("tap 0.5 0.25 0.05") == "ok"  # one tap, timed on the bridge
        assert fake.taps and fake.taps[-1][2] == 50
        assert await h.run("a 0.1 0.2 0 sure") == "ok"  # a lift: answered, never skipped
        assert (3, bytes(absolute(0.1, 0.2, 0))) in fake.reports
        assert await h.run("a 0.3 0.4 0") in ("ok", "ok dropped")  # a hover: the newest is kept
        assert await h.run("m 5 -3") == "ok"  # scripted motion, pipelined
        assert await h.run("md 2 2") in ("ok", "ok dropped")
        assert await h.run("k 0 4") == "ok"  # a key: report 1
        assert any(rid == 1 for rid, _ in fake.reports)
        assert any(rid == 2 for rid, _ in fake.reports)
        assert await h.run("connect") == "ok input goes through the bridge"

        assert await h.run("bridge off") == "ok bridge off"
        st = await h.run("status")
        assert " link=ble" in st and " le=open" in st
        await asyncio.sleep(0)
        assert h.published == ["open"]  # this PC's keyboard opened again

    with FakeBridge(KEY) as fake:
        run(go(fake))


def test_a_bridge_holding_another_phone_gets_nothing():
    from fake_bridge import FakeBridge
    from phoneuse.windows import hid as whid

    async def go(fake):
        h = make_hid()
        await h.run(f"bridge 127.0.0.1 {KEY.hex()}")
        await asyncio.sleep(2.6)  # watch_bridge's first look: its phone isn't the mirroring one
        assert h.bridge_other == "Someone Else's iPhone"
        assert " link=ble" in await h.run("status")
        before = len(fake.reports)
        # bridge mode closed this PC's LE keyboard: nothing can go, and it says why
        with pytest.raises(RuntimeError, match="connected to Someone Else's iPhone"):
            await h.run("k 0 4")
        assert len(fake.reports) == before  # nothing went to the other phone
        await h.run("bridge off")

    with FakeBridge(KEY, phone="Someone Else's iPhone") as fake:
        run(go(fake))


def test_this_pcs_keyboard_opens_when_the_bridge_goes_away():
    from fake_bridge import FakeBridge
    from phoneuse.windows import hid as whid

    async def go():
        h = make_hid()
        with FakeBridge(KEY):
            await h.run(f"bridge 127.0.0.1 {KEY.hex()}")
        assert not h.le_wanted and h.published == []
        old, whid.LE_FALLBACK = whid.LE_FALLBACK, 1.0
        try:
            for _ in range(300):  # the bridge stopped answering: after LE_FALLBACK, LE opens
                if h.le_wanted:
                    break
                await asyncio.sleep(0.1)
        finally:
            whid.LE_FALLBACK = old
        assert h.le_wanted and h.published == ["open"]
        assert " link=ble" in await h.run("status")
        await h.run("bridge off")

    run(go())


def test_a_bridge_that_doesnt_answer_is_refused():
    async def go():
        h = make_hid()
        with pytest.raises(RuntimeError, match="didn't answer"):
            await h.run(f"bridge 127.0.0.1 {KEY.hex()}")  # nothing listens
        assert h.bridge is None and " link=ble" in await h.run("status")

    run(go())


def test_calibration_tables_are_kept_per_link():
    from phoneuse import phones
    assert phones.drag_key("ble") == "drag_pts"  # what Windows saved before the bridge
    assert phones.drag_key("classic") == "drag_pts_classic"
