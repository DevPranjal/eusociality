"""Read-only checkpoint capture run as the coding user in one owned container."""

import json
import os
from pathlib import Path

ROOT = Path("/workspace")
EXCLUDED = {".git", ".home", ".tmp", "__pycache__", "node_modules"}
TOTAL_LIMIT = 2_097_152
FILE_LIMIT = 262_144


def capture() -> dict:
    files = {}
    errors = []
    total = 0

    def traversal_error(error: OSError) -> None:
        errors.append({"path": str(error.filename), "reason": type(error).__name__})

    for directory, folders, names in os.walk(ROOT, followlinks=False, onerror=traversal_error):
        base = Path(directory)
        folders[:] = sorted(
            name for name in folders if name not in EXCLUDED and not (base / name).is_symlink()
        )
        for name in sorted(names):
            path = base / name
            relative = path.relative_to(ROOT)
            if path.name.startswith(".env"):
                continue
            if path.is_symlink() or not path.resolve().is_relative_to(ROOT.resolve()):
                errors.append({"path": relative.as_posix(), "reason": "symlink_excluded"})
                continue
            try:
                size = path.stat().st_size
                if size > FILE_LIMIT or total + size > TOTAL_LIMIT:
                    errors.append({"path": relative.as_posix(), "reason": "capture_size_limit"})
                    continue
                files[relative.as_posix()] = path.read_bytes().decode("utf-8")
                total += size
            except (OSError, UnicodeError) as error:
                errors.append({"path": relative.as_posix(), "reason": type(error).__name__})
    return {"files": files, "errors": errors}


if __name__ == "__main__":
    print(json.dumps(capture()))
