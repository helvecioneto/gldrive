"""gldrive - scp-like upload, download and sync for Google Drive."""

from gldrive.auth import get_credentials, login, logout
from gldrive.client import GDrive, RemotePath

__version__ = "0.1.0"

__all__ = ["GDrive", "RemotePath", "get_credentials", "login", "logout", "__version__"]
