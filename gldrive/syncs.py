"""Persistent registry of sync pairs (stored in the config dir as syncs.json)."""

import json

from gldrive.auth import config_dir


def syncs_path():
    return config_dir() / "syncs.json"


def load() -> list:
    path = syncs_path()
    if not path.exists():
        return []
    return json.loads(path.read_text())


def save(entries: list) -> None:
    path = syncs_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(entries, indent=2, ensure_ascii=False))


def find(entries: list, ref: str) -> dict:
    """Look an entry up by name or 1-based index."""
    for index, entry in enumerate(entries, 1):
        if entry["name"] == ref or str(index) == ref:
            return entry
    return None


def unique_name(entries: list, base: str) -> str:
    names = {entry["name"] for entry in entries}
    if base not in names:
        return base
    counter = 2
    while f"{base}-{counter}" in names:
        counter += 1
    return f"{base}-{counter}"
