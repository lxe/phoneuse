"""`phoneuse serve` without a console window: what the Phone Use window
(windows/PhoneUseMirror) starts when phoneuse isn't running yet.

  python -m phoneuse.windows.background

Everything `phoneuse up` would print, its services' output included, goes to
phoneuse-up.log in the runtime folder. Its pid is in phoneuse-background.pid, so
the window's Quit stops it (and with it, through their job object, the services;
the Bluetooth HID service keeps running, as after `phoneuse up`). It is the same
backend as `phoneuse up` in a terminal, with no mirror window of its own: the
window that started it is the window.
"""

import ctypes
import msvcrt
import os
import sys

from ..config import runtime_path

LOG = runtime_path("phoneuse-up.log")
PID = runtime_path("phoneuse-background.pid")


def _to_log():
    """Point stdout and stderr, the C runtime's and Windows' (which the services
    started with stdout=None inherit), at the log."""
    try:
        if os.path.getsize(LOG) > 8 << 20:
            os.replace(LOG, LOG + ".old")
    except OSError:
        pass
    fd = os.open(LOG, os.O_WRONLY | os.O_APPEND | os.O_CREAT | os.O_BINARY)
    os.dup2(fd, 1)  # inheritable, so the services write here too
    os.dup2(fd, 2)
    os.close(fd)
    k = ctypes.WinDLL("kernel32", use_last_error=True)
    k.SetStdHandle.argtypes = (ctypes.c_uint32, ctypes.c_void_p)
    k.SetStdHandle(ctypes.c_uint32(-11 & 0xFFFFFFFF), msvcrt.get_osfhandle(1))
    k.SetStdHandle(ctypes.c_uint32(-12 & 0xFFFFFFFF), msvcrt.get_osfhandle(2))
    sys.stdout = open(1, "w", encoding="utf-8", errors="replace", buffering=1, closefd=False)
    sys.stderr = open(2, "w", encoding="utf-8", errors="replace", buffering=1, closefd=False)


def _own_pid():
    with open(PID, "w") as f:
        f.write(str(os.getpid()))


def main():
    os.makedirs(os.path.dirname(LOG), exist_ok=True)
    _to_log()
    try:
        from .. import cli
        # The pid is written once this copy is the backend: one that finds a backend
        # answering exits, and must not take the window's Quit away from that one.
        cli.up_windows(headless=True, started=_own_pid)
    finally:
        try:
            with open(PID) as f:
                if f.read().strip() == str(os.getpid()):
                    os.remove(PID)
        except OSError:
            pass


if __name__ == "__main__":
    main()
