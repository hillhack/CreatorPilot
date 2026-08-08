"""Standalone OAuth login script for YouTube Uploader."""

import os
import sys
import urllib.parse
import webbrowser
import wsgiref.simple_server
from google_auth_oauthlib.flow import InstalledAppFlow, _RedirectWSGIApp, _WSGIRequestHandler
from youtube_uploader.config import settings

# Allow HTTP for local OAuth testing
os.environ["OAUTHLIB_INSECURE_TRANSPORT"] = "1"

CREDENTIALS_FILE = settings.credentials_file
TOKEN_FILE = settings.token_file
SCOPES = settings.scopes
REDIRECT_URI = settings.redirect_uri


def main():
    print("=" * 60)
    print("  YouTube Uploader - Google OAuth Login")
    print("=" * 60)

    if not os.path.exists(CREDENTIALS_FILE):
        print(f"\n❌ {CREDENTIALS_FILE} not found!")
        sys.exit(1)

    print(f"\n✓ Using credentials: {CREDENTIALS_FILE}")
    print(f"✓ Using Redirect URI: {REDIRECT_URI}\n")

    # Create flow
    flow = InstalledAppFlow.from_client_secrets_file(CREDENTIALS_FILE, SCOPES)
    flow.redirect_uri = REDIRECT_URI

    # Create WSGI local server listening on port 8501
    wsgi_app = _RedirectWSGIApp(
        "<h1 style='font-family:sans-serif;color:green;text-align:center;margin-top:100px;'>"
        "✓ Authentication Successful! You can close this browser tab.</h1>"
    )

    try:
        wsgiref.simple_server.WSGIServer.allow_reuse_address = True
        local_server = wsgiref.simple_server.make_server(
            "localhost", 8501, wsgi_app, handler_class=_WSGIRequestHandler
        )
    except OSError as err:
        print(f"❌ Error binding to port 8501: {err}")
        print("Please stop any running Streamlit server (Ctrl+C) and try again.")
        sys.exit(1)

    auth_url, _ = flow.authorization_url(access_type="offline", include_granted_scopes="true")

    print("Opening Google OAuth sign-in page in your browser...")
    print(f"URL: {auth_url}\n")
    webbrowser.open(auth_url, new=1, autoraise=True)

    print("Waiting for Google OAuth response...")

    # Loop until the authorization callback containing 'code=' is received
    while True:
        local_server.handle_request()
        raw_uri = getattr(wsgi_app, "last_request_uri", "")
        if raw_uri and "code=" in raw_uri:
            break

    try:
        raw_uri = wsgi_app.last_request_uri
        parsed_url = urllib.parse.urlparse(raw_uri)
        params = urllib.parse.parse_qs(parsed_url.query)

        if "code" not in params or not params["code"]:
            raise ValueError(f"No authorization code returned in response URL: {raw_uri}")

        auth_code = params["code"][0]

        # Exchange code for token directly
        flow.fetch_token(code=auth_code)
        creds = flow.credentials

        with open(TOKEN_FILE, "w", encoding="utf-8") as f:
            f.write(creds.to_json())

        print("\n" + "=" * 60)
        print(" SUCCESS! Saved token.json")
        print("=" * 60)
        print("\nYou can now start Streamlit and upload videos cleanly:")
        print("   streamlit run app.py\n")
    except Exception as e:
        print(f"\n❌ Error during token exchange: {e}")
    finally:
        local_server.server_close()


if __name__ == "__main__":
    main()
