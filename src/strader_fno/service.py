from __future__ import annotations

import os
import plistlib
import subprocess
import sys
from pathlib import Path

LABEL = "com.strader-fno.scanner"


def install_service(root: Path, *, count=200, mode="ltp") -> Path:
    if sys.platform != "darwin":
        raise ValueError("Automatic launch-agent installation is available on macOS")
    root = root.resolve()
    if not 1 <= count <= 500 or mode not in ("ltp", "candle_close"):
        raise ValueError("Invalid scanner count or signal mode")
    from .brokers import Upstox
    from .settings import load_settings
    from .telegram import Telegram
    settings = load_settings(root / ".env")
    Upstox(settings.get("UPSTOX_ACCESS_TOKEN", "")).verify()
    Telegram(settings.get("TELEGRAM_BOT_TOKEN", ""), settings.get("TELEGRAM_CHAT_ID", "")).verify()
    state = root / "state"
    state.mkdir(exist_ok=True)
    target = Path.home() / "Library" / "LaunchAgents" / (LABEL + ".plist")
    target.parent.mkdir(parents=True, exist_ok=True)
    arguments = [sys.executable, "-B", "-m", "strader_fno", "scan", "--count", str(count),
                 "--mode", mode, "--state-dir", str(state)]
    definition = {"Label": LABEL, "ProgramArguments": arguments, "WorkingDirectory": str(root),
                  "EnvironmentVariables": {"PYTHONPATH": str(root / "src"), "PYTHONUNBUFFERED": "1"},
                  "RunAtLoad": True, "KeepAlive": True, "ThrottleInterval": 60,
                  "StandardOutPath": str(state / "scanner.log"), "StandardErrorPath": str(state / "scanner-error.log")}
    with open(target, "wb") as handle:
        plistlib.dump(definition, handle)
    os.chmod(target, 0o600)
    domain = f"gui/{os.getuid()}"
    subprocess.run(["launchctl", "bootout", domain + "/" + LABEL], capture_output=True)
    result = subprocess.run(["launchctl", "bootstrap", domain, str(target)], capture_output=True, text=True)
    if result.returncode:
        raise ValueError("Launch-agent installation failed; inspect launchctl for the current user's session")
    return target


def stop_service() -> None:
    if sys.platform != "darwin":
        raise ValueError("Launch-agent control is available on macOS")
    result = subprocess.run(["launchctl", "bootout", f"gui/{os.getuid()}/{LABEL}"], capture_output=True)
    if result.returncode:
        raise ValueError("Scanner launch agent is not running")
