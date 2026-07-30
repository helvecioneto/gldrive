"""Google Drive client: path resolution, transfers and one-way sync."""

import hashlib
import io
import os
from dataclasses import dataclass
from pathlib import Path

from googleapiclient.discovery import build
from googleapiclient.http import MediaFileUpload, MediaIoBaseDownload
from tqdm import tqdm

FOLDER_MIME = "application/vnd.google-apps.folder"
GOOGLE_APPS_PREFIX = "application/vnd.google-apps."
FILE_FIELDS = "id,name,mimeType,size,md5Checksum,modifiedTime"
CHUNK_SIZE = 10 * 1024 * 1024


@dataclass
class RemotePath:
    """A Drive path written as gd:some/folder/file."""

    PREFIXES = ("gd:", "gdrive:")

    parts: tuple

    @classmethod
    def is_remote(cls, path: str) -> bool:
        return path.startswith(cls.PREFIXES)

    @classmethod
    def parse(cls, path: str) -> "RemotePath":
        for prefix in cls.PREFIXES:
            if path.startswith(prefix):
                rest = path[len(prefix):]
                break
        else:
            raise ValueError(f"Not a remote path (expected gd:...): {path}")
        parts = tuple(p for p in rest.strip("/").split("/") if p)
        return cls(parts)

    @property
    def name(self) -> str:
        return self.parts[-1] if self.parts else ""

    @property
    def parent(self) -> "RemotePath":
        return RemotePath(self.parts[:-1])

    def __str__(self) -> str:
        return "gd:" + "/".join(self.parts)


def _escape(name: str) -> str:
    return name.replace("\\", "\\\\").replace("'", "\\'")


def _local_md5(path: Path) -> str:
    h = hashlib.md5()
    with open(path, "rb") as f:
        for block in iter(lambda: f.read(1024 * 1024), b""):
            h.update(block)
    return h.hexdigest()


def is_folder(item: dict) -> bool:
    return item["mimeType"] == FOLDER_MIME


def is_google_doc(item: dict) -> bool:
    """Google-native files (Docs, Sheets...) have no binary content to download."""
    return item["mimeType"].startswith(GOOGLE_APPS_PREFIX) and not is_folder(item)


class GDrive:
    """Thin wrapper over the Drive v3 API with path-based operations."""

    def __init__(self, credentials):
        self.service = build("drive", "v3", credentials=credentials)

    # ---------- navigation ----------

    def whoami(self) -> dict:
        return self.service.about().get(fields="user").execute()["user"]

    def find_child(self, parent_id: str, name: str) -> dict:
        query = (
            f"name = '{_escape(name)}' and '{parent_id}' in parents and trashed = false"
        )
        result = self.service.files().list(
            q=query, pageSize=2, fields=f"files({FILE_FIELDS})"
        ).execute()
        files = result.get("files", [])
        return files[0] if files else None

    def resolve(self, remote: RemotePath) -> dict:
        """Return the file/folder metadata at a remote path, or None."""
        item = {"id": "root", "name": "", "mimeType": FOLDER_MIME}
        for part in remote.parts:
            item = self.find_child(item["id"], part)
            if item is None:
                return None
        return item

    def list_children(self, folder_id: str) -> list:
        items, page_token = [], None
        while True:
            result = self.service.files().list(
                q=f"'{folder_id}' in parents and trashed = false",
                pageSize=1000,
                fields=f"nextPageToken, files({FILE_FIELDS})",
                pageToken=page_token,
            ).execute()
            items.extend(result.get("files", []))
            page_token = result.get("nextPageToken")
            if not page_token:
                return items

    def mkdirs(self, remote: RemotePath) -> str:
        """Create missing folders along the path; return the final folder id."""
        parent_id = "root"
        for part in remote.parts:
            child = self.find_child(parent_id, part)
            if child is None:
                child = self.service.files().create(
                    body={
                        "name": part,
                        "mimeType": FOLDER_MIME,
                        "parents": [parent_id],
                    },
                    fields="id,name,mimeType",
                ).execute()
            elif not is_folder(child):
                raise NotADirectoryError(f"gd:{'/'.join(remote.parts)}: '{part}' is a file")
            parent_id = child["id"]
        return parent_id

    # ---------- single-file transfers ----------

    def upload_file(self, local: Path, parent_id: str, name: str = None,
                    existing_id: str = None, quiet: bool = False) -> dict:
        """Upload one file; updates the existing file in place when existing_id is given."""
        name = name or local.name
        size = local.stat().st_size
        media = MediaFileUpload(str(local), chunksize=CHUNK_SIZE, resumable=size > 0)

        if existing_id:
            request = self.service.files().update(
                fileId=existing_id, media_body=media, fields=FILE_FIELDS
            )
        else:
            request = self.service.files().create(
                body={"name": name, "parents": [parent_id]},
                media_body=media,
                fields=FILE_FIELDS,
            )

        if size == 0:
            return request.execute()

        with tqdm(total=size, unit="B", unit_scale=True, unit_divisor=1024,
                  desc=name, disable=quiet) as pbar:
            response, sent = None, 0
            while response is None:
                status, response = request.next_chunk()
                current = int(status.progress() * size) if status else size
                pbar.update(current - sent)
                sent = current
            pbar.update(size - sent)
        return response

    def download_file(self, item: dict, local: Path, quiet: bool = False) -> None:
        if is_google_doc(item):
            tqdm.write(f"SKIP (Google-native file, use Drive to export): {item['name']}")
            return
        local.parent.mkdir(parents=True, exist_ok=True)
        size = int(item.get("size", 0))
        request = self.service.files().get_media(fileId=item["id"])
        with open(local, "wb") as fh:
            downloader = MediaIoBaseDownload(fh, request, chunksize=CHUNK_SIZE)
            with tqdm(total=size, unit="B", unit_scale=True, unit_divisor=1024,
                      desc=item["name"], disable=quiet) as pbar:
                done, received = False, 0
                while not done:
                    status, done = downloader.next_chunk()
                    current = int(status.progress() * size) if size else 0
                    pbar.update(current - received)
                    received = current

    # ---------- recursive transfers ----------

    def upload_tree(self, local_dir: Path, folder_id: str) -> None:
        """Upload the contents of local_dir into the remote folder (no diffing)."""
        self.sync_up(local_dir, folder_id)

    def download_tree(self, item: dict, local_dir: Path) -> None:
        local_dir.mkdir(parents=True, exist_ok=True)
        for child in self.list_children(item["id"]):
            target = local_dir / child["name"]
            if is_folder(child):
                self.download_tree(child, target)
            else:
                self.download_file(child, target)

    # ---------- sync (one-way, md5-based) ----------

    def sync_up(self, local_dir: Path, folder_id: str) -> dict:
        """Mirror local_dir into the remote folder: upload new/changed, skip identical."""
        stats = {"uploaded": 0, "updated": 0, "skipped": 0}
        remote_children = {c["name"]: c for c in self.list_children(folder_id)}

        for entry in sorted(local_dir.iterdir()):
            if entry.name.startswith("."):
                continue
            remote = remote_children.get(entry.name)
            if entry.is_dir():
                if remote is None:
                    remote = self.service.files().create(
                        body={"name": entry.name, "mimeType": FOLDER_MIME,
                              "parents": [folder_id]},
                        fields="id,name,mimeType",
                    ).execute()
                elif not is_folder(remote):
                    tqdm.write(f"CONFLICT (file on remote, folder locally): {entry.name}")
                    continue
                sub = self.sync_up(entry, remote["id"])
                for key in stats:
                    stats[key] += sub[key]
            elif entry.is_file():
                if remote is None:
                    self.upload_file(entry, folder_id)
                    stats["uploaded"] += 1
                elif is_folder(remote):
                    tqdm.write(f"CONFLICT (folder on remote, file locally): {entry.name}")
                elif remote.get("md5Checksum") == _local_md5(entry):
                    stats["skipped"] += 1
                else:
                    self.upload_file(entry, folder_id, existing_id=remote["id"])
                    stats["updated"] += 1
        return stats

    def sync_pair(self, src: str, dst: str) -> str:
        """Sync one src -> dst pair (exactly one side gd:...); return a summary."""
        src_remote = RemotePath.is_remote(src)
        dst_remote = RemotePath.is_remote(dst)
        if src_remote == dst_remote:
            raise ValueError("Exactly one of SRC/DST must be remote (gd:...)")

        if dst_remote:
            local = Path(src).expanduser()
            if not local.is_dir():
                raise ValueError(f"{src}: not a directory")
            folder_id = self.mkdirs(RemotePath.parse(dst))
            stats = self.sync_up(local, folder_id)
            return (f"{src} -> {dst}: {stats['uploaded']} uploaded, "
                    f"{stats['updated']} updated, {stats['skipped']} unchanged")

        remote = RemotePath.parse(src)
        item = self.resolve(remote)
        if item is None or not is_folder(item):
            raise ValueError(f"{src}: not a folder on Drive")
        stats = self.sync_down(item, Path(dst).expanduser())
        return (f"{src} -> {dst}: {stats['downloaded']} downloaded, "
                f"{stats['updated']} updated, {stats['skipped']} unchanged")

    def sync_down(self, item: dict, local_dir: Path) -> dict:
        """Mirror a remote folder into local_dir: download new/changed, skip identical."""
        stats = {"downloaded": 0, "updated": 0, "skipped": 0}
        local_dir.mkdir(parents=True, exist_ok=True)

        for child in self.list_children(item["id"]):
            target = local_dir / child["name"]
            if is_folder(child):
                sub = self.sync_down(child, target)
                for key in stats:
                    stats[key] += sub[key]
            elif is_google_doc(child):
                tqdm.write(f"SKIP (Google-native file): {child['name']}")
            elif not target.exists():
                self.download_file(child, target)
                stats["downloaded"] += 1
            elif child.get("md5Checksum") == _local_md5(target):
                stats["skipped"] += 1
            else:
                self.download_file(child, target)
                stats["updated"] += 1
        return stats
