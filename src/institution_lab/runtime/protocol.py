import hashlib
import importlib.metadata
import json
from pathlib import Path

VERSION = 1


def fingerprint() -> str:
    root = Path(__file__).parent
    digest = hashlib.sha256()
    for name in ("worker.py", "processes.py", "protocol.py"):
        digest.update(name.encode())
        digest.update((root / name).read_bytes())
    return digest.hexdigest()


def identity() -> dict:
    return {
        "protocol_version": VERSION,
        "runtime_fingerprint": fingerprint(),
        "sdk_version": importlib.metadata.version("github-copilot-sdk"),
    }


if __name__ == "__main__":
    print(json.dumps(identity()))
