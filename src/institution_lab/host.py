"""Temporary host availability lease; never changes persistent power settings."""

import ctypes
import os
import warnings


class AwakeLease:
    def __init__(self, enabled: bool) -> None:
        self.enabled = enabled
        self.previous: int | None = None

    def __enter__(self):
        if not self.enabled:
            return self
        if os.name != "nt":
            warnings.warn(
                "Host wake lease unavailable; keep the benchmark host awake.", stacklevel=2
            )
            return self
        function = ctypes.windll.kernel32.SetThreadExecutionState
        function.argtypes = [ctypes.c_uint]
        function.restype = ctypes.c_uint
        self.previous = function(0x80000001)
        if not self.previous:
            raise OSError("host_wake_lease_failed")
        return self

    def __exit__(self, exception_type, exception, traceback):
        if os.name == "nt" and self.previous is not None:
            if not ctypes.windll.kernel32.SetThreadExecutionState(self.previous):
                raise OSError("host_wake_lease_release_failed")
