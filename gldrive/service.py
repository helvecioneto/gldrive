"""Install `gldrive sync watch` as a background service that starts at login.

macOS: launchd user agent. Linux: systemd user unit. Windows: scheduled task.
"""

import platform
import subprocess
import sys
from pathlib import Path

from gldrive.auth import config_dir

LABEL = "com.gldrive.watch"
UNIT = "gldrive-watch"


def log_path() -> Path:
    return config_dir() / "watch.log"


def plist_path() -> Path:
    return Path.home() / "Library" / "LaunchAgents" / f"{LABEL}.plist"


def unit_path() -> Path:
    return Path.home() / ".config" / "systemd" / "user" / f"{UNIT}.service"


def watch_command(interval: int) -> list:
    return [sys.executable, "-m", "gldrive", "sync", "watch",
            "--interval", str(interval)]


def plist_content(interval: int) -> str:
    args = "\n".join(f"        <string>{arg}</string>"
                     for arg in watch_command(interval))
    return f"""<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN"
  "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
    <key>Label</key>
    <string>{LABEL}</string>
    <key>ProgramArguments</key>
    <array>
{args}
    </array>
    <key>RunAtLoad</key>
    <true/>
    <key>KeepAlive</key>
    <true/>
    <key>StandardOutPath</key>
    <string>{log_path()}</string>
    <key>StandardErrorPath</key>
    <string>{log_path()}</string>
</dict>
</plist>
"""


def unit_content(interval: int) -> str:
    return f"""[Unit]
Description=gldrive continuous sync
After=network-online.target

[Service]
ExecStart={' '.join(watch_command(interval))}
Restart=always
RestartSec=10

[Install]
WantedBy=default.target
"""


def install(interval: int = 300) -> str:
    system = platform.system()
    log_path().parent.mkdir(parents=True, exist_ok=True)

    if system == "Darwin":
        path = plist_path()
        path.parent.mkdir(parents=True, exist_ok=True)
        subprocess.run(["launchctl", "unload", str(path)], capture_output=True)
        path.write_text(plist_content(interval))
        subprocess.run(["launchctl", "load", "-w", str(path)], check=True)
        return (f"launchd service installed and started ({path}).\n"
                f"Logs: {log_path()}")

    if system == "Linux":
        path = unit_path()
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(unit_content(interval))
        subprocess.run(["systemctl", "--user", "daemon-reload"], check=True)
        subprocess.run(["systemctl", "--user", "enable", "--now", UNIT], check=True)
        return (f"systemd user service installed and started ({path}).\n"
                f"Logs: journalctl --user -u {UNIT} -f")

    if system == "Windows":
        command = " ".join(watch_command(interval))
        subprocess.run(["schtasks", "/Create", "/F", "/TN", UNIT,
                        "/SC", "ONLOGON", "/TR", command], check=True)
        return (f"Scheduled task '{UNIT}' created; it starts at the next logon.\n"
                "Start it now with: schtasks /Run /TN " + UNIT)

    raise RuntimeError(f"Unsupported platform: {system}")


def uninstall() -> str:
    system = platform.system()
    if system == "Darwin":
        path = plist_path()
        subprocess.run(["launchctl", "unload", "-w", str(path)], capture_output=True)
        if path.exists():
            path.unlink()
            return f"launchd service stopped and removed ({path})"
        return "Service was not installed."

    if system == "Linux":
        subprocess.run(["systemctl", "--user", "disable", "--now", UNIT],
                       capture_output=True)
        path = unit_path()
        if path.exists():
            path.unlink()
            subprocess.run(["systemctl", "--user", "daemon-reload"], capture_output=True)
            return f"systemd service stopped and removed ({path})"
        return "Service was not installed."

    if system == "Windows":
        result = subprocess.run(["schtasks", "/Delete", "/F", "/TN", UNIT],
                                capture_output=True)
        return ("Scheduled task removed." if result.returncode == 0
                else "Service was not installed.")

    raise RuntimeError(f"Unsupported platform: {system}")


def status() -> str:
    system = platform.system()
    if system == "Darwin":
        result = subprocess.run(["launchctl", "list", LABEL],
                                capture_output=True, text=True)
        if result.returncode != 0:
            return "Not installed (see: gldrive service install)"
        pid = next((line.split('"PID" = ')[1].rstrip(";").strip()
                    for line in result.stdout.splitlines() if '"PID"' in line), None)
        state = f"running (pid {pid})" if pid else "installed but not running"
        return f"{state}. Logs: {log_path()}"

    if system == "Linux":
        result = subprocess.run(["systemctl", "--user", "is-active", UNIT],
                                capture_output=True, text=True)
        state = result.stdout.strip() or "not installed"
        return f"{state}. Logs: journalctl --user -u {UNIT} -f"

    if system == "Windows":
        result = subprocess.run(["schtasks", "/Query", "/TN", UNIT],
                                capture_output=True, text=True)
        return (result.stdout.strip() if result.returncode == 0
                else "Not installed (see: gldrive service install)")

    raise RuntimeError(f"Unsupported platform: {system}")
