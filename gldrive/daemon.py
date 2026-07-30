"""Dropbox-like continuous sync for the saved sync pairs (gldrive sync watch).

Only folders registered with `gldrive sync add` are touched. Local changes in
upload pairs are detected instantly through filesystem events and synced after
a short debounce; Drive-side changes (and download pairs) are picked up by a
full pass every `interval` seconds. The registry is re-read continuously, so
`gldrive sync add/remove` take effect without restarting the daemon.
"""

import time
from pathlib import Path

from watchdog.events import FileSystemEventHandler
from watchdog.observers import Observer

from gldrive import auth, syncs
from gldrive.client import GDrive, RemotePath


def _log(message: str) -> None:
    print(f"[{time.strftime('%Y-%m-%d %H:%M:%S')}] {message}", flush=True)


class _MarkDirty(FileSystemEventHandler):
    """Flags the pair as needing a sync; the main loop debounces and runs it."""

    def __init__(self, state: dict):
        self.state = state

    def on_any_event(self, event):
        path = getattr(event, "dest_path", "") or getattr(event, "src_path", "")
        if Path(path).name.startswith("."):
            return
        self.state["dirty"] = True
        self.state["last_event"] = time.monotonic()


def _run_entry(drive: GDrive, entry: dict, entries: list) -> None:
    try:
        message = drive.sync_pair(entry["src"], entry["dst"])
        entry["last_run"] = time.strftime("%Y-%m-%d %H:%M:%S")
        syncs.save(entries)
        _log(f"{entry['name']}: {message}")
    except Exception as exc:  # keep the daemon alive through network/API errors
        _log(f"{entry['name']}: ERROR: {exc}")


def _reconcile_watchers(observer: Observer, watchers: dict, entries: list) -> None:
    """Keep one filesystem watcher per upload pair, following registry edits."""
    upload_pairs = {}
    for entry in entries:
        if RemotePath.is_remote(entry["dst"]):
            local = Path(entry["src"]).expanduser()
            if local.is_dir():
                upload_pairs[entry["name"]] = (entry, str(local))

    for name in list(watchers):
        current = upload_pairs.get(name)
        if current is None or current[1] != watchers[name]["path"]:
            observer.unschedule(watchers[name]["handle"])
            del watchers[name]
            _log(f"stopped watching '{name}'")

    for name, (entry, local) in upload_pairs.items():
        if name in watchers:
            watchers[name]["entry"] = entry
        else:
            state = {"dirty": False, "last_event": 0.0}
            handle = observer.schedule(_MarkDirty(state), local, recursive=True)
            watchers[name] = {"entry": entry, "state": state,
                              "handle": handle, "path": local}
            _log(f"watching '{name}': {local} -> {entry['dst']}")


def run_daemon(interval: int = 300, debounce: int = 3) -> None:
    """Run until interrupted. Raises auth.AuthError if not logged in."""
    drive = GDrive(auth.get_credentials())
    _log(f"gldrive watch started (full check every {interval}s, "
         f"local debounce {debounce}s)")

    observer = Observer()
    observer.start()
    watchers = {}
    next_full = 0.0  # immediate first pass

    try:
        while True:
            entries = syncs.load()
            _reconcile_watchers(observer, watchers, entries)

            now = time.monotonic()
            for info in watchers.values():
                state = info["state"]
                if state["dirty"] and now - state["last_event"] >= debounce:
                    state["dirty"] = False
                    _run_entry(drive, info["entry"], entries)

            if now >= next_full:
                if not entries:
                    _log("no saved syncs (add with: gldrive sync add SRC DST); waiting")
                for entry in entries:
                    _run_entry(drive, entry, entries)
                next_full = now + interval

            time.sleep(1)
    except KeyboardInterrupt:
        _log("stopping")
    finally:
        observer.stop()
        observer.join()
