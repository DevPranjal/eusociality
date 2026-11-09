from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from institution_lab import host


def test_disabled_lease_does_not_access_native_api(monkeypatch):
    monkeypatch.setattr(host, "ctypes", SimpleNamespace())
    with host.AwakeLease(enabled=False) as lease:
        assert lease.previous is None


def test_non_windows_lease_warns_without_native_api(monkeypatch):
    monkeypatch.setattr(host, "os", SimpleNamespace(name="posix"))
    monkeypatch.setattr(host, "ctypes", SimpleNamespace())
    with pytest.warns(UserWarning, match="Host wake lease unavailable"):
        with host.AwakeLease(enabled=True) as lease:
            assert lease.previous is None


@pytest.mark.parametrize(
    ("returns", "error"),
    [
        ([0x80000000, 0x80000000], None),
        ([0], "host_wake_lease_failed"),
        ([0x80000000, 0], "host_wake_lease_release_failed"),
    ],
)
def test_windows_lease_restores_state_and_reports_errors(monkeypatch, returns, error):
    function = Mock(side_effect=returns)
    monkeypatch.setattr(host, "os", SimpleNamespace(name="nt"))
    monkeypatch.setattr(
        host,
        "ctypes",
        SimpleNamespace(
            c_uint=int,
            windll=SimpleNamespace(kernel32=SimpleNamespace(SetThreadExecutionState=function)),
        ),
    )

    def exercise():
        with host.AwakeLease(enabled=True) as lease:
            assert lease.previous == 0x80000000

    if error is None:
        exercise()
    else:
        with pytest.raises(OSError, match=error):
            exercise()
    assert function.call_args_list[0].args == (0x80000001,)
    if len(returns) > 1:
        assert function.call_args_list[1].args == (0x80000000,)
