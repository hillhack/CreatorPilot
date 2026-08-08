"""Google OAuth2 Authentication handler for YouTube Data API v3.

Uses google_auth_oauthlib.flow.Flow (web client, no PKCE) with a persistent
state-file so the authorization state survives page reloads in Streamlit.
"""

import os
import json
import logging
from typing import Any, Optional, Tuple

from google.oauth2.credentials import Credentials
from google.auth.transport.requests import Request
from google_auth_oauthlib.flow import Flow
from googleapiclient.discovery import build

from youtube_uploader.config import settings

logger = logging.getLogger(__name__)

# Allow OAuth over plain HTTP for local development
os.environ["OAUTHLIB_INSECURE_TRANSPORT"] = "1"

# Path where we park the OAuth state between the redirect-away and redirect-back
_STATE_FILE = os.path.join(os.path.dirname(settings.token_file), ".oauth_state.json")


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------

def _build_flow(
    credentials_file: str,
    redirect_uri: str,
    state: Optional[str] = None,
) -> Flow:
    """Create a google_auth_oauthlib ``Flow`` (no PKCE) for a *web* client."""
    if not os.path.exists(credentials_file):
        raise FileNotFoundError(
            f"OAuth credentials file '{credentials_file}' not found. "
            "Download your OAuth 2.0 Client ID JSON from Google Cloud Console "
            f"and save it as '{credentials_file}'."
        )

    kwargs: dict = {}
    if state:
        kwargs["state"] = state

    flow = Flow.from_client_secrets_file(
        credentials_file,
        scopes=settings.scopes,
        redirect_uri=redirect_uri,
        **kwargs,
    )
    return flow


def _save_state(state: str, redirect_uri: str, code_verifier: Optional[str] = None) -> None:
    """Persist OAuth state token + redirect URI + PKCE code_verifier to disk."""
    os.makedirs(os.path.dirname(_STATE_FILE) or ".", exist_ok=True)
    with open(_STATE_FILE, "w", encoding="utf-8") as f:
        json.dump({"state": state, "redirect_uri": redirect_uri, "code_verifier": code_verifier}, f)


def _load_state() -> Optional[dict]:
    """Load persisted OAuth state from disk (returns None if missing)."""
    if not os.path.exists(_STATE_FILE):
        return None
    try:
        with open(_STATE_FILE, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return None


def _clear_state() -> None:
    """Remove the persisted OAuth state file."""
    if os.path.exists(_STATE_FILE):
        os.remove(_STATE_FILE)


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def get_authorization_url(
    credentials_file: str = settings.credentials_file,
    redirect_uri: str = settings.redirect_uri,
) -> Tuple[str, str]:
    """Generate the Google OAuth authorization URL and persist the CSRF state.

    Returns:
        (auth_url, state) — pass ``state`` back to ``exchange_code_for_credentials``.
    """
    flow = _build_flow(credentials_file=credentials_file, redirect_uri=redirect_uri)
    auth_url, state = flow.authorization_url(
        access_type="offline",
        include_granted_scopes="true",
        prompt="consent",
    )
    # Save the PKCE code_verifier so we can restore it during token exchange.
    # Flow generates a fresh verifier on every authorization_url() call; if we
    # reconstruct the flow later with a different verifier Google returns
    # (invalid_grant) Missing code verifier.
    code_verifier = getattr(flow, "code_verifier", None)
    _save_state(state=state, redirect_uri=redirect_uri, code_verifier=code_verifier)
    logger.info("Authorization URL generated. State + code_verifier saved to %s", _STATE_FILE)
    return auth_url, state


def exchange_code_for_credentials(
    code: str,
    state: Optional[str] = None,
    credentials_file: str = settings.credentials_file,
    redirect_uri: str = settings.redirect_uri,
    token_file: str = settings.token_file,
) -> Credentials:
    """Exchange an authorization code for OAuth credentials and save token.json.

    Loads the persisted CSRF state so we reconstruct an identical ``Flow``
    instance and Google's server accepts our token request.
    """
    # Load persisted state (redirect_uri may differ from default if user changed it)
    saved = _load_state()
    effective_redirect_uri = redirect_uri
    effective_state = state

    if saved:
        effective_redirect_uri = saved.get("redirect_uri", redirect_uri)
        effective_state = saved.get("state", state)
        logger.info("Loaded saved OAuth state for token exchange.")
    else:
        logger.warning("No saved OAuth state found; proceeding without CSRF state.")

    # Restore the PKCE code_verifier from the saved state so Google accepts our exchange
    effective_code_verifier = saved.get("code_verifier") if saved else None

    flow = _build_flow(
        credentials_file=credentials_file,
        redirect_uri=effective_redirect_uri,
        state=effective_state,
    )

    # Restore PKCE verifier onto the flow before fetching the token
    if effective_code_verifier:
        flow.code_verifier = effective_code_verifier
        logger.info("Restored PKCE code_verifier onto exchange flow.")

    flow.oauth2session._state = effective_state
    flow.fetch_token(code=code.strip())

    creds = flow.credentials

    # Load client info from credentials file to ensure complete token.json schema
    with open(credentials_file, "r", encoding="utf-8") as f:
        client_data = json.load(f)
    client_type = "web" if "web" in client_data else "installed"
    c_info = client_data[client_type]

    existing_refresh_token = None
    if os.path.exists(token_file):
        try:
            with open(token_file, "r", encoding="utf-8") as tf:
                existing_refresh_token = json.load(tf).get("refresh_token")
        except Exception:
            pass

    token_payload = {
        "token": creds.token,
        "refresh_token": creds.refresh_token or existing_refresh_token,
        "token_uri": creds.token_uri or c_info.get("token_uri", "https://oauth2.googleapis.com/token"),
        "client_id": creds.client_id or c_info.get("client_id"),
        "client_secret": creds.client_secret or c_info.get("client_secret"),
        "scopes": creds.scopes or settings.scopes,
    }

    os.makedirs(os.path.dirname(token_file) or ".", exist_ok=True)
    with open(token_file, "w", encoding="utf-8") as token_out:
        json.dump(token_payload, token_out, indent=2)

    _clear_state()
    logger.info("Credentials saved to %s", token_file)
    return creds


def get_credentials(
    credentials_file: str = settings.credentials_file,
    token_file: str = settings.token_file,
    scopes: list = settings.scopes,
) -> Credentials:
    """Load, auto-refresh, and return valid Google OAuth2 credentials."""
    creds: Optional[Credentials] = None

    if os.path.exists(token_file):
        try:
            creds = Credentials.from_authorized_user_file(token_file, scopes)
            logger.info("Loaded credentials from %s", token_file)
        except Exception as err:
            logger.warning("Failed to load %s: %s. Cleaning up invalid token file.", token_file, err)
            creds = None
            try:
                os.remove(token_file)
            except Exception:
                pass

    if creds and creds.valid:
        return creds

    if creds and creds.expired and creds.refresh_token:
        logger.info("Refreshing expired OAuth token…")
        try:
            creds.refresh(Request())
            with open(token_file, "w", encoding="utf-8") as f:
                f.write(creds.to_json())
            return creds
        except Exception as err:
            logger.warning("Token refresh failed: %s", err)
            creds = None

    raise RuntimeError(
        "Google OAuth credentials missing or expired. "
        "Please click 'Sign in with Google' to authenticate."
    )


def login_cli(
    credentials_file: str = settings.credentials_file,
    token_file: str = settings.token_file,
) -> Credentials:
    """Interactive login flow for CLI / terminal users (runs a local server)."""
    from google_auth_oauthlib.flow import InstalledAppFlow  # only used here

    os.environ["OAUTHLIB_INSECURE_TRANSPORT"] = "1"
    installed_flow = InstalledAppFlow.from_client_secrets_file(
        credentials_file, scopes=settings.scopes
    )
    creds = installed_flow.run_local_server(host="localhost", port=0)
    os.makedirs(os.path.dirname(token_file) or ".", exist_ok=True)
    with open(token_file, "w", encoding="utf-8") as f:
        f.write(creds.to_json())
    logger.info("Saved credentials to %s", token_file)
    return creds


def get_youtube_client(
    credentials_file: str = settings.credentials_file,
    token_file: str = settings.token_file,
) -> Any:
    """Build an authorized YouTube Data API v3 client (OAuth2)."""
    creds = get_credentials(credentials_file=credentials_file, token_file=token_file)
    return build("youtube", "v3", credentials=creds)


def get_youtube_api_key_client(api_key: Optional[str] = None) -> Any:
    """Build a YouTube Data API v3 client using an API Key."""
    key = api_key or settings.youtube_api_key
    if not key:
        raise ValueError("YOUTUBE_API_KEY is not configured.")
    return build("youtube", "v3", developerKey=key)


def get_analytics_client(
    credentials_file: str = settings.credentials_file,
    token_file: str = settings.token_file,
) -> Any:
    """Build an authorized YouTube Analytics API v2 client (OAuth2)."""
    creds = get_credentials(credentials_file=credentials_file, token_file=token_file)
    return build("youtubeAnalytics", "v2", credentials=creds)

