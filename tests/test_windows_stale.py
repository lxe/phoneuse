"""Which processes a new `phoneuse up` counts as old backends (phoneuse/windows/stale.py).
Only the command-line matching: it runs on any platform."""
from phoneuse.windows import stale

VENV = r'"C:\Users\me\AppData\Local\Programs\Phone Use\core\.venv\Scripts\python.exe"'
CHECKOUT = r"C:\Users\me\phoneuse\.venv\Scripts\python.exe"


def test_ups_match_from_any_install():
    assert stale.UP.search(f"{VENV} -u -m phoneuse.windows.background")
    assert stale.UP.search(f'{VENV}  "C:\\Users\\me\\AppData\\Local\\Programs\\Phone Use\\core\\bin\\phoneuse" up')
    assert stale.UP.search(f"{CHECKOUT} C:\\Users\\me\\phoneuse\\bin\\phoneuse serve")
    assert stale.UP.search(f"{CHECKOUT} -m phoneuse up")


def test_services_and_hid_match():
    for m in ("avhub", "frames", "web"):
        assert stale.SERVICES.search(f"{VENV} -u -m phoneuse.{m}")
    assert stale.HID.search(f"{CHECKOUT} -u -m phoneuse.windows.hid")


def test_agents_commands_and_shared_services_are_left_alone():
    for cmd in (f"{VENV} C:\\x\\bin\\phoneuse doctor", f"{VENV} C:\\x\\bin\\phoneuse status",
                f"{VENV} C:\\x\\bin\\phoneuse tap Settings", f"{CHECKOUT} -m phoneuse.vision",
                f"{CHECKOUT} -m phoneuse.mcp_server", f"{VENV} -m phoneuse.webhook"):
        assert not stale.UP.search(cmd), cmd
        assert not stale.SERVICES.search(cmd), cmd
        assert not stale.HID.search(cmd), cmd


def test_this_process_and_its_starters_are_mine(monkeypatch):
    procs = {10: {"ppid": 5}, 5: {"ppid": 1}, 1: {"ppid": 0}, 99: {"ppid": 5}}
    monkeypatch.setattr(stale.os, "getpid", lambda: 10)
    assert stale._mine(procs) == {10, 5, 1}
