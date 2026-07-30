# gldrive

scp-like upload, download and sync of files and folders for Google Drive,
straight from the terminal. OAuth login through the browser (a few clicks),
token saved locally — you only log in once.

Remote paths use the `gd:` prefix, like scp's `remote:`:

```
gldrive cp report.pdf gd:docs/
```

## Installation

Straight from GitHub:

```bash
pip install git+https://github.com/helvecioneto/gldrive.git
```

To upgrade later:

```bash
pip install -U --force-reinstall git+https://github.com/helvecioneto/gldrive.git
```

Or, for development:

```bash
git clone https://github.com/helvecioneto/gldrive.git
cd gldrive
pip install -e .
```

### On shared machines / conda environments

On clusters or machines with a shared Anaconda, avoid `pip install --user`:
mixing new Google libraries with older ones already present (oauth2client,
pyOpenSSL, pydrive2...) can break imports. Install into a dedicated
environment and expose just the executable:

```bash
python3 -m venv ~/.gldrive-venv
~/.gldrive-venv/bin/pip install git+https://github.com/helvecioneto/gldrive.git
ln -s ~/.gldrive-venv/bin/gldrive ~/.local/bin/gldrive
```

(or use [pipx](https://pipx.pypa.io): `pipx install git+https://github.com/helvecioneto/gldrive.git`)

## First-time setup (once)

Just run:

```bash
gldrive login
```

On the first run, the command shows the Google Cloud Console link with the
step-by-step to create the OAuth client (type **Desktop app**) and waits for
the credentials. It automatically detects whatever you paste:

- the **path** of the downloaded JSON;
- the JSON **content** pasted directly into the terminal;
- the **Client ID** or the **Client secret** shown on the console screen —
  paste one and it asks for the other, assembling the configuration itself.

If you prefer, point to the file directly:

```bash
gldrive login --secrets ~/Downloads/client_secret_XXXX.json
```

The browser then opens, you authorize with a few clicks and that's it. The
token is stored in `~/.config/gldrive/` and refreshed automatically — the
next commands never ask for login. On servers without a browser, use
`gldrive login --no-browser`.

## Usage

```bash
# List
gldrive ls gd:                     # Drive root
gldrive ls -l gd:backup            # long listing (size, date)

# Upload (local -> Drive)
gldrive cp report.pdf gd:docs/
gldrive cp -r ./data gd:backup/data

# Download (Drive -> local)
gldrive cp gd:docs/report.pdf .
gldrive cp -r gd:backup/data ./data

# Sync (one-way, incremental by md5; never deletes anything)
gldrive sync ./photos gd:photos            # one-off: local -> Drive
gldrive sync gd:photos ./photos            # one-off: Drive -> local
gldrive sync ./photos gd:photos --watch    # keeps syncing (every 60s)

# Permanent syncs (saved in ~/.config/gldrive/syncs.json)
gldrive sync add ./photos gd:photos        # register the pair (auto name: "photos")
gldrive sync add ./data gd:backup --name data-backup
gldrive sync list                          # list saved pairs and their last run
gldrive sync run                           # run every saved pair
gldrive sync run photos                    # run just one (by name or number)
gldrive sync run --watch --interval 300    # run all in a loop
gldrive sync remove photos                 # unregister (by name or number)

# Dropbox-like mode: continuous sync of the saved pairs (and ONLY them)
gldrive sync watch                         # runs in the terminal until Ctrl+C
gldrive sync watch --interval 60           # check the Drive side every 60s
gldrive service install                    # install as a service: starts at login
gldrive service status                     #   and runs forever in the background
gldrive service uninstall                  # stop and remove the service

# Other
gldrive mkdir gd:backup/2026
gldrive whoami
gldrive logout           # revokes access with Google and deletes the token
gldrive logout --all     # same, and also deletes the saved OAuth client
```

In continuous mode (`sync watch` / service), changes in the **local** folders
are detected instantly (filesystem events, via watchdog) and uploaded after a
few quiet seconds; **Drive-side** changes are picked up by the periodic check
(`--interval`, default 300s). Only the folders registered with `sync add` are
watched — never your whole Drive nor your whole computer. `sync add`/`remove`
take effect immediately, without restarting the service. Works on macOS
(launchd), Linux (systemd) and Windows (Task Scheduler).

Folders are created automatically at the destination when missing. Files with
identical content (md5) are skipped; changed files are updated in place (no
duplicates on Drive). Google-native files (Docs, Sheets...) have no binary
content and are skipped on download.

## Library usage

```python
from pathlib import Path
from gldrive import GDrive, RemotePath, get_credentials

drive = GDrive(get_credentials())
folder_id = drive.mkdirs(RemotePath.parse("gd:backup/data"))
drive.sync_up(Path("./data"), folder_id)
```

## Notes

- OAuth scope: `https://www.googleapis.com/auth/drive` (read and write).
- Config lives in `~/.config/gldrive/` (override with the `GLDRIVE_CONFIG_DIR`
  environment variable).
- `sync` is one-way and never deletes files, neither at the destination nor
  at the source.
