"""A sandbox that blocks local connections (WinError 10013) is told apart from a stopped
service, so agents aren't sent to start a second backend (cli.can_reach_services)."""
from phoneuse import cli, ipc


def test_a_blocked_connection_means_sandboxed(monkeypatch):
    def blocked(port, timeout):
        raise PermissionError(10013, "An attempt was made to access a socket in a way forbidden by its access permissions")
    monkeypatch.setattr(ipc, "_tcp", blocked)
    assert not cli.can_reach_services()


def test_a_stopped_service_is_still_reachable(monkeypatch):
    def refused(port, timeout):
        raise ConnectionRefusedError(10061, "No connection could be made because the target machine actively refused it")
    monkeypatch.setattr(ipc, "_tcp", refused)
    assert cli.can_reach_services()


def test_the_message_says_not_to_start_it_again():
    assert "don't start it again" in cli.SANDBOXED
