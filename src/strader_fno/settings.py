from __future__ import annotations

import os
import tempfile
from pathlib import Path

KEYS = {"UPSTOX_ACCESS_TOKEN", "UPSTOX_CLIENT_ID", "UPSTOX_CLIENT_SECRET", "UPSTOX_REDIRECT_URI",
        "UPSTOX_API_KEY", "UPSTOX_API_SECRET", "TELEGRAM_BOT_TOKEN", "TELEGRAM_CHAT_ID"}


def load_settings(path: Path = Path(".env")) -> dict[str, str]:
    result = {}
    if path.exists():
        for line in path.read_text().splitlines():
            line = line.strip()
            if not line or line.startswith("#"):
                continue
            key, separator, value = line.partition("=")
            key = key.strip()
            if not separator or key not in KEYS:
                raise ValueError("Unsupported setting in .env")
            value = value.strip()
            if len(value) >= 2 and value[0] == value[-1] and value[0] in "\"'":
                value = value[1:-1]
            result[key] = value
    for key in KEYS:
        if os.environ.get(key):
            result[key] = os.environ[key]
    return result


def validate_settings(settings: dict[str, str]) -> None:
    for key, value in settings.items():
        if key not in KEYS or not isinstance(value, str) or any(char in value for char in ("\n", "\r", "\x00")):
            raise ValueError("Invalid setting; credentials must be single-line values")


def write_settings(path: Path, settings: dict[str, str]) -> None:
    validate_settings(settings)
    path = path.resolve()
    descriptor, temporary = tempfile.mkstemp(prefix=".env-", dir=path.parent)
    try:
        os.fchmod(descriptor, 0o600)
        with os.fdopen(descriptor, "w") as handle:
            for key in sorted(settings):
                handle.write(key + "=" + settings[key] + "\n")
        os.replace(temporary, path)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)
