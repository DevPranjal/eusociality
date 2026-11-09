from __future__ import annotations

import re
from typing import overload

SECRET_KEYS = frozenset(
    {
        "authorization",
        "cookie",
        "setcookie",
        "githubtoken",
        "ghtoken",
        "copilotgithubtoken",
        "accesstoken",
        "refreshtoken",
        "apikey",
        "clientsecret",
        "usageid",
        "quotasnapshots",
    }
)
TOKEN_PATTERN = re.compile(r"\b(?:gh[pousr]_[A-Za-z0-9_]+|github_pat_[A-Za-z0-9_]+)\b")


@overload
def redact(value: dict) -> dict: ...


@overload
def redact(value: list) -> list: ...


@overload
def redact(value: str) -> str: ...


@overload
def redact(value: object) -> object: ...


def redact(value: object) -> object:
    if isinstance(value, dict):
        return {
            key: "[REDACTED]"
            if str(key).lower().replace("_", "").replace("-", "") in SECRET_KEYS
            else redact(item)
            for key, item in value.items()
        }
    if isinstance(value, list):
        return [redact(item) for item in value]
    if isinstance(value, str):
        return TOKEN_PATTERN.sub("[REDACTED]", value)
    return value
