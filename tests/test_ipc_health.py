"""A service that rejects a connection must not pass its health check."""
import io
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from phoneuse import ipc


class Connection:
    def __init__(self, reply):
        self.reply = reply

    def __enter__(self):
        return self

    def __exit__(self, *args):
        pass

    def sendall(self, data):
        pass

    def makefile(self, mode):
        return io.BytesIO(self.reply)


def test_rejected_connection_is_not_a_successful_request(monkeypatch):
    monkeypatch.setattr(ipc, "connect", lambda *args: Connection(b""))
    with pytest.raises(ConnectionResetError, match="without a reply"):
        ipc.request("hid", "status")


def test_successful_status_still_returns_reply(monkeypatch):
    monkeypatch.setattr(ipc, "connect", lambda *args: Connection(b"ok connected=True\n"))
    assert ipc.request("hid", "status") == ("ok connected=True", b"")
