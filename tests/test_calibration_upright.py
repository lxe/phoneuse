"""Calibration measures an upright phone only (calibrate.py): it waits while the phone is
sideways, stops when the phone turns mid-measurement, and retries a spoiled measurement.
    .venv\\Scripts\\python -m pytest tests\\test_calibration_upright.py
"""
import os
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from phoneuse import calibrate, overview  # noqa: E402


class Clock:
    """A fake clock: sleep() moves it on instead of waiting."""

    def __init__(self):
        self.t = 1000.0

    def __call__(self):
        return self.t

    def sleep(self, s):
        self.t += s


@pytest.fixture
def files(tmp_path, monkeypatch):
    monkeypatch.setattr(calibrate, "STATUS_FILE", str(tmp_path / "status.json"))
    monkeypatch.setattr(calibrate, "LOCK_FILE", str(tmp_path / "calibration.lock"))
    return tmp_path


def test_run_refuses_a_sideways_phone(monkeypatch):
    monkeypatch.setattr(calibrate.orientation, "frame_size", lambda: (2622, 1206))
    with pytest.raises(calibrate.Sideways):
        calibrate.run(object(), log=lambda *_: None)


def test_run_stops_when_the_phone_turns_partway(monkeypatch):
    sizes = iter([(1206, 2622)] * 3 + [(2622, 1206)] * 10)  # upright for the first checks, then sideways
    monkeypatch.setattr(calibrate.orientation, "frame_size", lambda: next(sizes))
    measured = []
    monkeypatch.setattr(calibrate, "measure", lambda h, u, n: measured.append(u) or 10.0 * u)
    with pytest.raises(calibrate.Sideways):
        calibrate.run(object(), log=lambda *_: None)
    assert len(measured) < len(calibrate.SIZES)  # it didn't go on measuring a sideways phone


def test_it_waits_while_sideways_then_measures_once_upright(files, monkeypatch):
    clock = Clock()
    sideways = {"on": True}
    monkeypatch.setattr(calibrate, "sideways", lambda: sideways["on"])
    seen = []

    def sleep(s):
        clock.sleep(s)
        seen.append((calibrate.status() or {}).get("state"))
        if clock() - 1000 > 600:  # ten minutes sideways: far past `wait`, still waiting
            sideways["on"] = False

    monkeypatch.setattr(calibrate, "calibrate", lambda h, name, **k: f"calibrated {name}: ok")
    r = calibrate.when_ready("Cam's iPhone", lambda: None, wait=30, clock=clock, sleep=sleep, log=lambda *_: None)
    assert r == "calibrated Cam's iPhone: ok"
    assert seen and set(seen) == {"waiting_upright"}
    assert calibrate.status()["state"] == "done"
    assert not os.path.exists(calibrate.LOCK_FILE)


def test_turning_mid_measurement_waits_for_upright_again(files, monkeypatch):
    clock = Clock()
    calls = []

    def measure(h, name, **k):
        calls.append(1)
        if len(calls) == 1:
            raise calibrate.Sideways()
        return f"calibrated {name}: ok"

    monkeypatch.setattr(calibrate, "sideways", lambda: False)
    monkeypatch.setattr(calibrate, "calibrate", measure)
    r = calibrate.when_ready("p", lambda: None, clock=clock, sleep=clock.sleep, log=lambda *_: None)
    assert r.startswith("calibrated") and len(calls) == 2


def test_a_spoiled_measurement_is_tried_again_then_given_up(files, monkeypatch):
    clock = Clock()
    calls = []
    monkeypatch.setattr(calibrate, "sideways", lambda: False)
    monkeypatch.setattr(calibrate, "calibrate", lambda h, name, **k: calls.append(1) or calibrate.FAILED)
    r = calibrate.when_ready("p", lambda: None, tries=3, clock=clock, sleep=clock.sleep, log=lambda *_: None)
    assert r == calibrate.FAILED and len(calls) == 3
    assert calibrate.status()["state"] == "failed"


def test_an_upright_wait_bound_gives_up(files, monkeypatch):
    clock = Clock()
    monkeypatch.setattr(calibrate, "sideways", lambda: True)
    r = calibrate.when_ready("p", lambda: None, upright_wait=60, poll=5, clock=clock, sleep=clock.sleep,
                             log=lambda *_: None)
    assert r is None and calibrate.status()["state"] == "failed"


def test_it_stops_when_no_longer_wanted(files, monkeypatch):
    clock = Clock()
    monkeypatch.setattr(calibrate, "sideways", lambda: True)
    wanted = iter([True, True, False])
    r = calibrate.when_ready("p", lambda: None, still_wanted=lambda: next(wanted), clock=clock, sleep=clock.sleep,
                             log=lambda *_: None)
    assert r is None and calibrate.status() is None


def test_one_measurement_at_a_time(files):
    assert calibrate.lock()
    assert not calibrate.lock() and calibrate.busy()
    calibrate.unlock()
    assert calibrate.lock()
    calibrate.unlock()


def test_the_setup_step_says_to_turn_the_phone_upright():
    s = {"picture": {"live": True}, "bridge": {}, "calibrated": False, "bluetooth": True, "input_choice": "pc",
         "calibration": {"state": "waiting_upright"}}
    pointer = next(i for i in overview.checklist(s, "Phone Use") if i["id"] == "pointer")
    assert "upright" in pointer["detail"] and pointer["calibration"] == "waiting_upright"
    assert pointer["action"] == "calibrate"
    s["calibration"] = {"state": "running", "step": 2, "steps": 7}
    pointer = next(i for i in overview.checklist(s, "Phone Use") if i["id"] == "pointer")
    assert "3 of 7" in pointer["detail"]
