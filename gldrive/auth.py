"""OAuth authentication for gldrive.

Credentials live in a per-user config directory (override with GLDRIVE_CONFIG_DIR):
  - client_secrets.json: the OAuth client downloaded from Google Cloud Console
  - token.json: the access/refresh token saved after login
"""

import json
import os
from pathlib import Path

from google.auth.exceptions import RefreshError
from google.auth.transport.requests import Request
from google.oauth2.credentials import Credentials
from google_auth_oauthlib.flow import InstalledAppFlow

SCOPES = ["https://www.googleapis.com/auth/drive"]


class AuthError(Exception):
    """Raised when authentication cannot be completed."""


def config_dir() -> Path:
    return Path(os.environ.get("GLDRIVE_CONFIG_DIR", Path.home() / ".config" / "gldrive"))


def token_path() -> Path:
    return config_dir() / "token.json"


def secrets_path() -> Path:
    return config_dir() / "client_secrets.json"


def build_client_config(client_id: str, client_secret: str) -> dict:
    """Assemble a standard 'installed app' client config from an id/secret pair."""
    return {
        "installed": {
            "client_id": client_id,
            "client_secret": client_secret,
            "auth_uri": "https://accounts.google.com/o/oauth2/auth",
            "token_uri": "https://oauth2.googleapis.com/token",
            "auth_provider_x509_cert_url": "https://www.googleapis.com/oauth2/v1/certs",
            "redirect_uris": ["http://localhost"],
        }
    }


def save_client_secrets_data(data: dict) -> Path:
    """Validate and store OAuth client secrets (parsed JSON) in the config dir."""
    if "installed" not in data and "web" not in data:
        raise AuthError(
            "This does not look like an OAuth client secrets file "
            "(missing 'installed'/'web' key). Download it from "
            "https://console.cloud.google.com/apis/credentials"
        )
    dest = secrets_path()
    dest.parent.mkdir(parents=True, exist_ok=True)
    dest.write_text(json.dumps(data))
    dest.chmod(0o600)
    return dest


def save_client_secrets(source: str) -> Path:
    """Validate and copy an OAuth client secrets file into the config dir."""
    src = Path(source).expanduser()
    with open(src) as f:
        data = json.load(f)
    return save_client_secrets_data(data)


def _save_token(creds: Credentials) -> None:
    path = token_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(creds.to_json())
    path.chmod(0o600)


def get_credentials(interactive: bool = False, open_browser: bool = True) -> Credentials:
    """Return valid credentials, refreshing the saved token when possible.

    With interactive=True, runs the browser OAuth flow when there is no
    (refreshable) token; otherwise raises AuthError asking the user to log in.
    """
    creds = None
    if token_path().exists():
        creds = Credentials.from_authorized_user_file(str(token_path()), SCOPES)

    if creds and creds.valid:
        return creds

    if creds and creds.expired and creds.refresh_token:
        try:
            creds.refresh(Request())
            _save_token(creds)
            return creds
        except RefreshError:
            pass  # token revoked/expired for good; fall through to login

    if not interactive:
        raise AuthError("Not logged in. Run: gldrive login")

    if not secrets_path().exists():
        raise AuthError(
            "No OAuth client configured. Run: gldrive login --secrets <client_secrets.json>\n"
            "Create one (type: Desktop app) at https://console.cloud.google.com/apis/credentials"
        )

    flow = InstalledAppFlow.from_client_secrets_file(str(secrets_path()), SCOPES)
    if open_browser:
        creds = flow.run_local_server(port=0, prompt="consent")
    else:
        creds = flow.run_local_server(port=0, prompt="consent", open_browser=False)
    _save_token(creds)
    return creds


def login(secrets: str = None, open_browser: bool = True) -> Credentials:
    if secrets:
        save_client_secrets(secrets)
    return get_credentials(interactive=True, open_browser=open_browser)


def logout(purge: bool = False) -> dict:
    """Log out: revoke the token with Google and delete it locally.

    With purge=True also deletes the saved OAuth client (client_secrets.json).
    Returns which steps actually happened.
    """
    result = {"revoked": False, "token": False, "client": False}
    if token_path().exists():
        try:
            creds = Credentials.from_authorized_user_file(str(token_path()), SCOPES)
            token = creds.refresh_token or creds.token
            if token:
                import requests

                response = requests.post(
                    "https://oauth2.googleapis.com/revoke",
                    params={"token": token},
                    timeout=10,
                )
                result["revoked"] = response.status_code == 200
        except Exception:
            pass  # offline or already revoked; still remove the local token
        token_path().unlink()
        result["token"] = True
    if purge and secrets_path().exists():
        secrets_path().unlink()
        result["client"] = True
    return result
