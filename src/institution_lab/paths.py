from pathlib import PurePosixPath


def relative_file(value: str) -> str:
    path = PurePosixPath(value)
    if path.is_absolute() or ".." in path.parts or any(char in value for char in ("\\", ":", "\0")):
        raise ValueError("invalid_file_path")
    return path.as_posix()
