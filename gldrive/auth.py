"""OAuth authentication for gldrive.

Credentials live in a per-user config directory (override with GLDRIVE_CONFIG_DIR):
  - client_secrets.json: the OAuth client downloaded from Google Cloud Console
  - token.json: the access/refresh token saved after login
"""

import json
import os
import webbrowser
from pathlib import Path
from urllib.parse import parse_qs, urlparse

from google.auth.exceptions import RefreshError
from google.auth.transport.requests import Request
from google.oauth2.credentials import Credentials
from google_auth_oauthlib.flow import Flow, InstalledAppFlow

SCOPES = ["https://www.googleapis.com/auth/drive"]

# Redirect port for the manual (headless) flow. Must be outside the port range
# browsers refuse to connect to (port 1 and other low ports are blocked as
# "unsafe", which leaves the address bar stuck on the consent page).
MANUAL_PORT = 53682


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
    if "web" in data and "installed" not in data:
        raise AuthError(
            "This OAuth client is of type 'Web application', which requires "
            "pre-registered redirect URIs and will fail with "
            "redirect_uri_mismatch. Create a new client of type 'Desktop app' "
            "at https://console.cloud.google.com/apis/credentials and use that one."
        )
    if "installed" not in data:
        raise AuthError(
            "This does not look like an OAuth client secrets file "
            "(missing 'installed' key). Download it from "
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

    if open_browser:
        flow = InstalledAppFlow.from_client_secrets_file(str(secrets_path()), SCOPES)
        try:
            creds = flow.run_local_server(port=0, prompt="consent")
        except webbrowser.Error:
            print("\nNo local browser found — switching to manual login.")
            creds = _run_manual_flow()
    else:
        creds = _run_manual_flow()
    _save_token(creds)
    return creds


def _run_manual_flow() -> Credentials:
    """Headless login: user opens the URL anywhere, pastes the redirect back.

    Works on remote/SSH machines: the OAuth redirect goes to a localhost
    address that fails to load in the user's browser, but the authorization
    code is right there in the address bar for them to copy.
    """
    flow = Flow.from_client_secrets_file(
        str(secrets_path()), scopes=SCOPES,
        redirect_uri=f"http://localhost:{MANUAL_PORT}/",
    )
    auth_url, _ = flow.authorization_url(access_type="offline", prompt="consent")
    print("\nOpen this URL in any browser (on your own computer is fine):\n")
    print(auth_url)
    print("\nAuthorize the access. The browser will then land on a")
    print(f"http://localhost:{MANUAL_PORT}/... page that fails to load "
          '("site can\'t be reached")')
    print("— that is expected, and it means it worked. Copy the FULL address")
    print("from the address bar (it contains ?code=...) and paste it here.\n")

    while True:
        try:
            code = _extract_code(_read_reply())
            break
        except AuthError as exc:
            if "Aborted" in str(exc):
                raise
            print(f"  {exc}\n")

    try:
        flow.fetch_token(code=code)
    except Exception as exc:
        hint = ""
        if "deleted_client" in str(exc) or "invalid_client" in str(exc):
            hint = ("\nThe saved OAuth client no longer exists in Google Cloud "
                    "Console. Create a new one (type: Desktop app), then run "
                    "'gldrive logout --all' and 'gldrive login' again.")
        elif "invalid_grant" in str(exc):
            hint = ("\nAuthorization codes are single-use and expire in minutes. "
                    "Run 'gldrive login' again and paste the fresh URL right away.")
        raise AuthError(f"Token exchange failed: {exc}{hint}")
    return flow.credentials


def _read_reply() -> str:
    try:
        reply = input("Redirect URL (or just the code): ").strip().strip("'\"")
    except EOFError:
        reply = ""
    if not reply:
        raise AuthError("Aborted: no authorization code provided.")
    return reply


def _extract_code(reply: str) -> str:
    """Pull the authorization code out of a pasted redirect URL (or bare code)."""
    parsed = urlparse(reply)
    code = parse_qs(parsed.query).get("code", [None])[0]

    if code is None:
        if reply.startswith("GOCSPX-") or reply.endswith(".apps.googleusercontent.com"):
            raise AuthError(
                "That is the OAuth client secret/ID, not the authorization code. "
                "Open the URL above in a browser, click Allow, and then paste the "
                "FULL localhost address from the address bar (it contains ?code=...)."
            )
        if parsed.netloc.endswith("google.com"):
            raise AuthError(
                "That is still Google's consent page, not the redirect. Finish "
                "clicking Allow and wait until the address bar changes to "
                f"http://localhost:{MANUAL_PORT}/?...code=... (the page will show "
                "an error - that is fine), then paste that address."
            )
        if parsed.scheme or "?" in reply:
            raise AuthError("Could not find the ?code= parameter in the pasted URL.")
        code = reply  # a bare authorization code

    return code


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
