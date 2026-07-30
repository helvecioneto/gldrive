"""gldrive command-line interface."""

import json
import sys
import time
from pathlib import Path

import click

from gldrive import auth, syncs
from gldrive.client import GDrive, RemotePath, is_folder, is_google_doc


def _connect() -> GDrive:
    try:
        return GDrive(auth.get_credentials())
    except auth.AuthError as exc:
        raise click.ClickException(str(exc))


def _fmt_size(item: dict) -> str:
    if is_folder(item):
        return "-"
    size = int(item.get("size", 0))
    for unit in ("B", "K", "M", "G", "T"):
        if size < 1024:
            return f"{size:.0f}{unit}"
        size /= 1024
    return f"{size:.0f}P"


@click.group()
@click.version_option(package_name="gldrive")
def main():
    """scp-like upload, download and sync for Google Drive.

    Remote paths are written with a gd: prefix, e.g. gd:backup/photos.

    \b
    Examples:
      gldrive login                        first-time setup (guided) and login
      gldrive ls gd:
      gldrive cp report.pdf gd:docs/       upload (download: swap the arguments)
      gldrive cp -r ./data gd:backup/data
      gldrive sync ./photos gd:photos      one-off sync
      gldrive sync add ./photos gd:photos  save a folder pair for continuous sync
      gldrive sync list                    show saved pairs
      gldrive sync watch                   Dropbox-like mode for the saved pairs
      gldrive service install              keep it running in background, forever
    """


CONSOLE_URL = "https://console.cloud.google.com/apis/credentials"


def _clean(value: str) -> str:
    return value.strip().strip("'\"")


def _prompt_for_secrets():
    """Guide the user to create an OAuth client and wait for the credentials.

    Accepts, in any order of discovery: the path to the downloaded JSON, the
    JSON content pasted directly, or a client ID / client secret pair.
    """
    click.echo("No OAuth client configured yet. To create one (only needed once):")
    click.echo()
    click.echo(f"  1. Open:  {CONSOLE_URL}")
    click.echo("  2. If asked, create a project and enable the Google Drive API")
    click.echo("     (APIs & Services > Library > Google Drive API > Enable).")
    click.echo("  3. Create Credentials > OAuth client ID > type 'Desktop app'.")
    click.echo("  4. Paste below the downloaded JSON file path, the JSON content,")
    click.echo("     or the Client ID / Client secret shown on screen.")
    click.echo()
    while True:
        value = _clean(click.prompt("Credentials (file path, JSON, ID or secret)"))
        try:
            if value.startswith("{"):
                auth.save_client_secrets_data(json.loads(value))
            elif Path(value).expanduser().is_file():
                auth.save_client_secrets(value)
            elif value.endswith(".apps.googleusercontent.com"):
                secret = _clean(click.prompt("Client secret"))
                auth.save_client_secrets_data(auth.build_client_config(value, secret))
            elif value.startswith("GOCSPX-"):
                client_id = _clean(click.prompt("Client ID (...apps.googleusercontent.com)"))
                auth.save_client_secrets_data(auth.build_client_config(client_id, value))
            elif value.endswith(".json"):
                raise OSError(f"file not found: {value}")
            else:
                raise ValueError(
                    "not recognized — paste the JSON file path, the JSON content, "
                    "a Client ID (...apps.googleusercontent.com) or a Client secret (GOCSPX-...)"
                )
            click.echo(f"OAuth client saved to {auth.secrets_path()}")
            return
        except (OSError, ValueError, auth.AuthError) as exc:
            click.echo(f"  Invalid credentials: {exc}")
            click.echo("  Try again (Ctrl+C to abort).")


@main.command()
@click.option("--secrets", type=click.Path(exists=True, dir_okay=False),
              help="OAuth client secrets JSON from Google Cloud Console "
                   "(only needed on the first login).")
@click.option("--no-browser", is_flag=True,
              help="Headless/SSH mode: print the auth URL to open on any "
                   "machine, then paste the redirect URL back.")
def login(secrets, no_browser):
    """Log in to Google Drive via OAuth (opens your browser).

    Without --secrets on the first login, prints the link to create the
    OAuth client and waits for you to provide the downloaded credentials.

    On machines without a browser (clusters, SSH sessions), use
    --no-browser: open the printed URL on any computer, authorize, and
    paste the resulting redirect URL back into the terminal. If no
    browser is found, this mode is used automatically.
    """
    try:
        if secrets:
            auth.save_client_secrets(secrets)
        elif not auth.secrets_path().exists():
            _prompt_for_secrets()
        creds = auth.get_credentials(interactive=True, open_browser=not no_browser)
    except auth.AuthError as exc:
        raise click.ClickException(str(exc))
    user = GDrive(creds).whoami()
    click.echo(f"Logged in as {user.get('displayName')} <{user.get('emailAddress')}>")
    click.echo(f"Token saved to {auth.token_path()}")


@main.command()
@click.option("--all", "purge", is_flag=True,
              help="Also delete the saved OAuth client (client_secrets.json).")
def logout(purge):
    """Log out: revoke access with Google and delete the saved token."""
    result = auth.logout(purge=purge)
    if result["token"]:
        revoked = "access revoked with Google" if result["revoked"] else \
                  "could not reach Google to revoke (token deleted locally)"
        click.echo(f"Logged out: {revoked}.")
    else:
        click.echo("No saved session.")
    if result["client"]:
        click.echo("OAuth client credentials deleted.")
    elif purge:
        click.echo("No OAuth client credentials to delete.")


@main.command()
def whoami():
    """Show the logged-in account."""
    user = _connect().whoami()
    click.echo(f"{user.get('displayName')} <{user.get('emailAddress')}>")


@main.command()
@click.argument("path", default="gd:")
@click.option("-l", "long", is_flag=True, help="Long listing (size, modified, type).")
def ls(path, long):
    """List a remote folder (default: Drive root)."""
    if not RemotePath.is_remote(path):
        raise click.ClickException("ls expects a remote path, e.g. gd:folder")
    drive = _connect()
    remote = RemotePath.parse(path)
    item = drive.resolve(remote)
    if item is None:
        raise click.ClickException(f"{path}: not found")
    if not is_folder(item):
        click.echo(item["name"])
        return
    children = sorted(drive.list_children(item["id"]),
                      key=lambda c: (not is_folder(c), c["name"].lower()))
    for child in children:
        name = child["name"] + ("/" if is_folder(child) else "")
        if long:
            modified = child.get("modifiedTime", "")[:16].replace("T", " ")
            click.echo(f"{_fmt_size(child):>8}  {modified}  {name}")
        else:
            click.echo(name)


@main.command()
@click.argument("path")
def mkdir(path):
    """Create a remote folder, with parents.

    Example: gldrive mkdir gd:backup/2026
    """
    if not RemotePath.is_remote(path):
        raise click.ClickException("mkdir expects a remote path, e.g. gd:folder")
    _connect().mkdirs(RemotePath.parse(path))
    click.echo(f"Created {path}")


@main.command()
@click.argument("src")
@click.argument("dst")
@click.option("-r", "--recursive", is_flag=True,
              help="Copy folders recursively (also inferred automatically).")
def cp(src, dst, recursive):
    """Copy files/folders to or from Drive, scp-style.

    \b
      gldrive cp report.pdf gd:docs/         upload a file
      gldrive cp -r ./data gd:backup/data    upload a folder
      gldrive cp gd:docs/report.pdf .        download a file
      gldrive cp -r gd:backup/data ./data    download a folder
    """
    src_remote = RemotePath.is_remote(src)
    dst_remote = RemotePath.is_remote(dst)
    if src_remote == dst_remote:
        raise click.ClickException(
            "Exactly one of SRC/DST must be remote (gd:...) — "
            "remote-to-remote and local-to-local copies are not supported."
        )
    drive = _connect()
    if dst_remote:
        _upload(drive, Path(src).expanduser(), RemotePath.parse(dst))
    else:
        _download(drive, RemotePath.parse(src), Path(dst).expanduser())


def _upload(drive: GDrive, src: Path, dst: RemotePath) -> None:
    if not src.exists():
        raise click.ClickException(f"{src}: no such file or directory")

    target = drive.resolve(dst)

    if src.is_dir():
        if target is not None and is_folder(target):
            # existing destination folder: create src.name inside it (scp behavior)
            folder_id = drive.mkdirs(RemotePath(dst.parts + (src.name,)))
        elif target is None:
            folder_id = drive.mkdirs(dst)
        else:
            raise click.ClickException(f"{dst}: exists and is a file")
        stats = drive.sync_up(src, folder_id)
        click.echo(f"Done: {stats['uploaded']} uploaded, {stats['updated']} updated, "
                   f"{stats['skipped']} unchanged")
        return

    if target is not None and is_folder(target):
        parent_id, name = target["id"], src.name
    else:
        parent_id, name = drive.mkdirs(dst.parent), dst.name
    existing = drive.find_child(parent_id, name)
    if existing is not None and is_folder(existing):
        raise click.ClickException(f"gd:{'/'.join(dst.parts)}/{name}: is a folder")
    drive.upload_file(src, parent_id, name=name,
                      existing_id=existing["id"] if existing else None)


def _download(drive: GDrive, src: RemotePath, dst: Path) -> None:
    item = drive.resolve(src)
    if item is None:
        raise click.ClickException(f"{src}: not found on Drive")

    if is_folder(item):
        target = dst / (src.name or "drive") if dst.is_dir() else dst
        stats = drive.sync_down(item, target)
        click.echo(f"Done: {stats['downloaded']} downloaded, {stats['updated']} updated, "
                   f"{stats['skipped']} unchanged")
        return

    if is_google_doc(item):
        raise click.ClickException(
            f"{item['name']} is a Google-native file (Docs/Sheets/...); "
            "export it from the Drive UI instead."
        )
    target = dst / item["name"] if dst.is_dir() else dst
    drive.download_file(item, target)


class DefaultGroup(click.Group):
    """Group that routes unknown first arguments to a default subcommand,
    so `gldrive sync SRC DST` still works alongside `gldrive sync add ...`."""

    def __init__(self, *args, **kwargs):
        self.default_cmd = kwargs.pop("default_cmd", None)
        super().__init__(*args, **kwargs)

    def resolve_command(self, ctx, args):
        try:
            return super().resolve_command(ctx, args)
        except click.UsageError:
            if self.default_cmd is None:
                raise
            cmd = self.get_command(ctx, self.default_cmd)
            return cmd.name, cmd, args


def _sync_pair(drive: GDrive, src: str, dst: str) -> str:
    """Run one sync in the direction given by the arguments; return a summary."""
    try:
        return drive.sync_pair(src, dst)
    except (ValueError, NotADirectoryError) as exc:
        raise click.ClickException(str(exc))


@main.group(cls=DefaultGroup, default_cmd="once")
def sync():
    """One-way sync between a local folder and a Drive folder.

    Direction follows the arguments: `sync ./photos gd:photos` uploads,
    `sync gd:photos ./photos` downloads. Identical files (md5) are
    skipped; nothing is ever deleted.

    \b
    One-off:            gldrive sync ./photos gd:photos
    Save permanently:   gldrive sync add ./photos gd:photos
    List saved:         gldrive sync list
    Run all saved:      gldrive sync run
    Continuous (live):  gldrive sync watch      (as a service: gldrive service install)
    """


@sync.command()
@click.argument("src")
@click.argument("dst")
@click.option("--watch", is_flag=True, help="Keep syncing forever.")
@click.option("--interval", default=60, show_default=True,
              help="Seconds between runs with --watch.")
def once(src, dst, watch, interval):
    """Run a one-off sync without saving it (default for `sync SRC DST`)."""
    drive = _connect()
    while True:
        click.echo(f"[{time.strftime('%H:%M:%S')}] {_sync_pair(drive, src, dst)}")
        if not watch:
            break
        try:
            time.sleep(interval)
        except KeyboardInterrupt:
            break


@sync.command()
@click.argument("src")
@click.argument("dst")
@click.option("--name", help="Name for this sync (default: taken from the remote path).")
def add(src, dst, name):
    """Save a sync pair permanently.

    Example: gldrive sync add ./photos gd:photos
    """
    if RemotePath.is_remote(src) == RemotePath.is_remote(dst):
        raise click.ClickException("Exactly one of SRC/DST must be remote (gd:...)")
    entries = syncs.load()
    remote = RemotePath.parse(dst if RemotePath.is_remote(dst) else src)
    base = name or remote.name or "root"
    entry = {
        "name": syncs.unique_name(entries, base),
        "src": src,
        "dst": dst,
        "added": time.strftime("%Y-%m-%d %H:%M:%S"),
    }
    entries.append(entry)
    syncs.save(entries)
    click.echo(f"Saved sync '{entry['name']}': {src} -> {dst}")
    click.echo("Run it with: gldrive sync run" + (f" {entry['name']}" if len(entries) > 1 else ""))


@sync.command("list")
def sync_list():
    """List the saved sync pairs."""
    entries = syncs.load()
    if not entries:
        click.echo("No saved syncs. Add one with: gldrive sync add SRC DST")
        return
    for index, entry in enumerate(entries, 1):
        direction = "up  " if RemotePath.is_remote(entry["dst"]) else "down"
        last = entry.get("last_run", "never")
        click.echo(f"{index}. {entry['name']:<20} [{direction}] "
                   f"{entry['src']} -> {entry['dst']}  (last run: {last})")


@sync.command()
@click.argument("ref")
def remove(ref):
    """Remove a saved sync by name or number (see `gldrive sync list`)."""
    entries = syncs.load()
    entry = syncs.find(entries, ref)
    if entry is None:
        raise click.ClickException(f"No sync named '{ref}' (see: gldrive sync list)")
    entries.remove(entry)
    syncs.save(entries)
    click.echo(f"Removed sync '{entry['name']}' ({entry['src']} -> {entry['dst']})")


@sync.command()
@click.option("--interval", default=300, show_default=True,
              help="Seconds between full checks (picks up Drive-side changes).")
@click.option("--debounce", default=3, show_default=True,
              help="Seconds of quiet after a local change before syncing.")
def watch(interval, debounce):
    """Dropbox-like mode: continuously sync the saved pairs.

    Watches only the folders registered with `gldrive sync add`. Local
    changes are detected instantly (filesystem events) and uploaded after
    a short debounce; Drive-side changes are picked up every --interval
    seconds. Runs until interrupted (Ctrl+C) — to keep it running
    permanently in the background, use `gldrive service install`.
    """
    from gldrive.daemon import run_daemon

    try:
        run_daemon(interval=interval, debounce=debounce)
    except auth.AuthError as exc:
        raise click.ClickException(str(exc))


@sync.command()
@click.argument("refs", nargs=-1)
@click.option("--watch", is_flag=True, help="Keep syncing forever.")
@click.option("--interval", default=60, show_default=True,
              help="Seconds between rounds with --watch.")
def run(refs, watch, interval):
    """Run saved syncs: all of them, or only the ones named (by name or number)."""
    entries = syncs.load()
    if not entries:
        raise click.ClickException("No saved syncs. Add one with: gldrive sync add SRC DST")
    if refs:
        selected = []
        for ref in refs:
            entry = syncs.find(entries, ref)
            if entry is None:
                raise click.ClickException(f"No sync named '{ref}' (see: gldrive sync list)")
            selected.append(entry)
    else:
        selected = entries

    drive = _connect()
    while True:
        for entry in selected:
            message = _sync_pair(drive, entry["src"], entry["dst"])
            entry["last_run"] = time.strftime("%Y-%m-%d %H:%M:%S")
            click.echo(f"[{time.strftime('%H:%M:%S')}] {entry['name']}: {message}")
        syncs.save(entries)
        if not watch:
            break
        try:
            time.sleep(interval)
        except KeyboardInterrupt:
            break


@main.group()
def service():
    """Run the continuous sync (sync watch) as a background service.

    Installs a user service that starts at login and keeps running:
    launchd on macOS, systemd on Linux, Task Scheduler on Windows.
    """


@service.command("install")
@click.option("--interval", default=300, show_default=True,
              help="Seconds between full checks (picks up Drive-side changes).")
def service_install(interval):
    """Install and start the background sync service."""
    from gldrive import service as svc

    try:
        auth.get_credentials()  # fail early with a clear message
    except auth.AuthError as exc:
        raise click.ClickException(f"{exc} (the service needs a saved login)")
    try:
        click.echo(svc.install(interval))
    except Exception as exc:
        raise click.ClickException(str(exc))


@service.command("uninstall")
def service_uninstall():
    """Stop and remove the background sync service."""
    from gldrive import service as svc

    try:
        click.echo(svc.uninstall())
    except Exception as exc:
        raise click.ClickException(str(exc))


@service.command("status")
def service_status():
    """Show whether the background sync service is running."""
    from gldrive import service as svc

    try:
        click.echo(svc.status())
    except Exception as exc:
        raise click.ClickException(str(exc))


if __name__ == "__main__":
    sys.exit(main())
