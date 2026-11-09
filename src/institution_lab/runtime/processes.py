"""Trusted checkpoint probe, executed in isolated Python mode inside one owned container."""

import json
import os
import signal
import time
from pathlib import Path


def active_coding_pids() -> list[int]:
    result = []
    for path in Path("/proc").iterdir():
        if not path.name.isdigit():
            continue
        try:
            if path.stat().st_uid == 1001 and int(path.name) != os.getpid():
                state = (path / "stat").read_text().rsplit(")", 1)[1].split()[0]
                if state != "Z":
                    result.append(int(path.name))
        except FileNotFoundError:
            continue
    return result


def stop_coding_processes() -> list[int]:
    kill_signal = getattr(signal, "SIGKILL", None)
    if kill_signal is None:
        raise RuntimeError("linux_process_helper_required")
    targets = active_coding_pids()
    for pid in targets:
        try:
            os.kill(pid, signal.SIGTERM)
        except ProcessLookupError:
            continue
    deadline = time.monotonic() + 2
    while active_coding_pids() and time.monotonic() < deadline:
        time.sleep(0.05)
    for pid in active_coding_pids():
        try:
            os.kill(pid, kill_signal)
        except ProcessLookupError:
            continue
    return targets


if __name__ == "__main__":
    print(json.dumps(active_coding_pids()))
