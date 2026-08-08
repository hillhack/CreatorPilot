"""YouTube Publishing & Creator Studio — Streamlit Web UI.

OAuth strategy:
  Streamlit 1.35+ intercepts /oauth2callback internally, so we cannot rely on
  st.query_params to receive Google's authorization code. Instead we spin up a
  tiny stdlib HTTP server on port 8080 (already registered in Google Cloud
  Console as an Authorised Redirect URI: http://localhost:8080/) in a daemon
  thread. When Google redirects the browser there we exchange the code and
  save token.json — completely bypassing Streamlit's routing.
"""

import os
import random
import tempfile
import threading
import urllib.parse
from typing import Any
from http.server import BaseHTTPRequestHandler, HTTPServer


import streamlit as st
import plotly.graph_objects as go

os.environ["OAUTHLIB_INSECURE_TRANSPORT"] = "1"

from youtube_uploader.config import settings
from youtube_uploader.auth import (
    get_authorization_url,
    exchange_code_for_credentials,
    get_credentials,
    get_youtube_client,
)
from youtube_uploader.upload import upload_video
from youtube_uploader.analytics import (
    get_own_channel_stats,
    get_channel_videos,
    get_random_video,
    get_analytics_timeseries,
    get_public_channel_stats,
)
from youtube_uploader.gemini_client import (
    generate_video_ideas,
    generate_video_script,
    analyse_video_performance,
    compare_channels_ai,
    transcribe_and_generate_metadata,
)

# ── Constants ────────────────────────────────────────────────────────────────
OAUTH_PORT = 8080
OAUTH_REDIRECT_URI = f"http://localhost:{OAUTH_PORT}/"

CATEGORIES = {
    "22": "People & Blogs",
    "28": "Science & Technology",
    "20": "Gaming",
    "27": "Education",
    "24": "Entertainment",
    "10": "Music",
}

# ── Mock data (used when not authenticated or API disabled) ─────────────────
_MOCK_CHANNEL: dict = {
    "channel_id": "UC_MOCK_CHANNEL",
    "title": "CreatorPilot Lab",
    "description": "AI & Developer Studio — building next-gen creative tools for YouTube creators.",
    "custom_url": "creatorpilotlab",
    "subscriber_count": 14250,
    "view_count": 512000,
    "video_count": 38,
    "thumbnail": "https://picsum.photos/100/100",
    "uploads_playlist_id": "MOCK_UPLOADS",
}
_MOCK_VIDEOS: list = [
    {"video_id": "vid_101", "title": "Build Autonomous AI Agents in 10 Minutes", "view_count": 18400, "like_count": 1220, "comment_count": 89, "thumbnail": "https://picsum.photos/320/180?random=11", "published_at": "2024-03-15", "youtube_url": "https://youtu.be/vid_101"},
    {"video_id": "vid_102", "title": "Streamlit vs Next.js Developer Guide", "view_count": 9300, "like_count": 640, "comment_count": 42, "thumbnail": "https://picsum.photos/320/180?random=22", "published_at": "2024-02-28", "youtube_url": "https://youtu.be/vid_102"},
    {"video_id": "vid_103", "title": "Python FastAPI Complete Tutorial 2024", "view_count": 14200, "like_count": 980, "comment_count": 73, "thumbnail": "https://picsum.photos/320/180?random=33", "published_at": "2024-01-20", "youtube_url": "https://youtu.be/vid_103"},
    {"video_id": "vid_104", "title": "RAG with LangChain — Zero to Hero", "view_count": 11500, "like_count": 820, "comment_count": 58, "thumbnail": "https://picsum.photos/320/180?random=44", "published_at": "2023-12-10", "youtube_url": "https://youtu.be/vid_104"},
    {"video_id": "vid_105", "title": "Docker Compose in 15 Minutes", "view_count": 7800, "like_count": 510, "comment_count": 35, "thumbnail": "https://picsum.photos/320/180?random=55", "published_at": "2023-11-05", "youtube_url": "https://youtu.be/vid_105"},
]

# ── Helper for clean single-line error messages ──────────────────────────────
def format_error_one_liner(err: Any, prefix: str = "") -> str:
    """Format exception into a clean single-line string without dumping multi-line JSON or raw traces."""
    msg = str(err).strip()
    if "429" in msg or "RESOURCE_EXHAUSTED" in msg or "quota" in msg.lower():
        clean = "Rate limit / quota exceeded (429). Please wait ~15 seconds and try again."
    elif "403" in msg or "accessNotConfigured" in msg:
        clean = "Access denied (403). API service disabled in Google Cloud Console."
    elif "401" in msg or "invalid_grant" in msg:
        clean = "Authentication expired. Please disconnect and sign in again."
    else:
        clean = msg.split("\n")[0].strip()
        if len(clean) > 120:
            clean = clean[:117] + "..."
    return f"{prefix}{clean}" if prefix else clean


# ── Page config ──────────────────────────────────────────────────────────────
st.set_page_config(
    page_title="CreatorPilot Studio",
    page_icon="⚡",
    layout="wide",
    initial_sidebar_state="expanded",
)

# ── Design System CSS (additive polish on top of dark base theme) ─────────────
st.markdown("""
<style>
@import url('https://fonts.googleapis.com/css2?family=Inter:wght@300;400;500;600;700;800&display=swap');

html, body, [class*="css"] {
    font-family: 'Inter', -apple-system, BlinkMacSystemFont, sans-serif !important;
}

/* Radial glow background accent */
.stAppViewContainer {
    background: radial-gradient(ellipse 100% 60% at 50% -10%, rgba(255,46,76,0.09) 0%, transparent 70%) !important;
}

/* Sidebar edge */
section[data-testid="stSidebar"] {
    border-right: 1px solid rgba(255,255,255,0.06) !important;
}

/* Hero Section */
.hero-badge {
    display: inline-block;
    background: rgba(255,46,76,0.1);
    border: 1px solid rgba(255,46,76,0.3);
    color: #ff6680;
    font-size: 0.72rem;
    font-weight: 700;
    padding: 3px 12px;
    border-radius: 9999px;
    letter-spacing: 0.08em;
    text-transform: uppercase;
    margin-bottom: 0.6rem;
}
.hero-title {
    font-size: 2.6rem;
    font-weight: 800;
    letter-spacing: -0.03em;
    background: linear-gradient(160deg, #ffffff 30%, #909090 100%);
    -webkit-background-clip: text;
    -webkit-text-fill-color: transparent;
    background-clip: text;
    line-height: 1.1;
    margin-bottom: 0.5rem;
}
.hero-sub {
    color: #71717a;
    font-size: 0.95rem;
    font-weight: 400;
}

/* Glass Cards (st.container border=True) */
div[data-testid="stVerticalBlockBorderWrapper"] {
    background: rgba(255,255,255,0.025) !important;
    border: 1px solid rgba(255,255,255,0.08) !important;
    border-radius: 16px !important;
    backdrop-filter: blur(12px) !important;
    transition: border-color 0.2s ease !important;
}
div[data-testid="stVerticalBlockBorderWrapper"]:hover {
    border-color: rgba(255,46,76,0.25) !important;
}

/* Buttons */
.stButton > button {
    background: rgba(255,255,255,0.05) !important;
    color: #e4e4e7 !important;
    border: 1px solid rgba(255,255,255,0.1) !important;
    border-radius: 9px !important;
    font-weight: 600 !important;
    font-size: 0.875rem !important;
    transition: all 0.18s ease !important;
}
.stButton > button:hover {
    background: rgba(255,46,76,0.18) !important;
    border-color: rgba(255,46,76,0.45) !important;
    color: #ff8096 !important;
    transform: translateY(-1px) !important;
    box-shadow: 0 0 16px rgba(255,46,76,0.25) !important;
}
button[kind="primary"], .stButton > button[kind="primary"] {
    background: linear-gradient(135deg, #ff2e4c, #c0112c) !important;
    color: #ffffff !important;
    border-color: rgba(255,255,255,0.15) !important;
    box-shadow: 0 4px 18px rgba(255,46,76,0.4) !important;
}
button[kind="primary"]:hover {
    background: linear-gradient(135deg, #ff4d66, #d91b38) !important;
    box-shadow: 0 6px 24px rgba(255,46,76,0.5) !important;
}

/* Tabs pill nav */
.stTabs [data-baseweb="tab-list"] {
    gap: 4px !important;
    background: rgba(255,255,255,0.04) !important;
    padding: 5px !important;
    border-radius: 12px !important;
    border: 1px solid rgba(255,255,255,0.07) !important;
    margin-bottom: 1.5rem !important;
}
.stTabs [data-baseweb="tab"] {
    height: 40px !important;
    border-radius: 8px !important;
    padding: 0 18px !important;
    font-weight: 500 !important;
    font-size: 0.88rem !important;
    transition: all 0.15s ease !important;
    background: transparent !important;
    border: none !important;
}
.stTabs [aria-selected="true"] {
    background: rgba(255,46,76,0.12) !important;
    color: #ff6680 !important;
    font-weight: 700 !important;
    border: 1px solid rgba(255,46,76,0.3) !important;
}

/* Metric tiles */
[data-testid="stMetric"] {
    background: rgba(255,255,255,0.02) !important;
    border: 1px solid rgba(255,255,255,0.06) !important;
    border-radius: 12px !important;
    padding: 1rem 1.2rem !important;
}
[data-testid="stMetricValue"] {
    font-size: 1.55rem !important;
    font-weight: 700 !important;
    letter-spacing: -0.02em !important;
}

/* Connection status pills */
.status-pill-ok {
    display: inline-flex; align-items: center; gap: 5px;
    background: rgba(34,197,94,0.08); border: 1px solid rgba(34,197,94,0.3);
    color: #4ade80; padding: 4px 12px; border-radius: 9999px; font-size: 0.8rem; font-weight: 600;
}
.status-pill-off {
    display: inline-flex; align-items: center; gap: 5px;
    background: rgba(239,68,68,0.08); border: 1px solid rgba(239,68,68,0.3);
    color: #f87171; padding: 4px 12px; border-radius: 9999px; font-size: 0.8rem; font-weight: 600;
}
.dot-green { width: 6px; height: 6px; background: #22c55e; border-radius: 50%; box-shadow: 0 0 7px #22c55e; flex-shrink: 0; }
.dot-red   { width: 6px; height: 6px; background: #ef4444; border-radius: 50%; flex-shrink: 0; }
@keyframes pulse-glow {
    0% { opacity: 0.3; transform: scale(0.9); }
    50% { opacity: 1; transform: scale(1.1); }
    100% { opacity: 0.3; transform: scale(0.9); }
}
.dot-pulse {
    width: 6px; height: 6px; background: #ff2e4c; border-radius: 50%; box-shadow: 0 0 6px #ff2e4c; display: inline-block;
    animation: pulse-glow 1.4s infinite ease-in-out;
}

/* Google Auth link button */
.auth-google-btn {
    display: block !important;
    width: 100% !important;
    text-align: center !important;
    background: linear-gradient(135deg, #ff2e4c, #c0112c) !important;
    color: #ffffff !important;
    font-weight: 600 !important;
    font-size: 0.88rem !important;
    padding: 10px 16px !important;
    border-radius: 9px !important;
    text-decoration: none !important;
    box-shadow: 0 4px 18px rgba(255,46,76,0.35) !important;
    transition: all 0.18s ease !important;
    box-sizing: border-box !important;
    border: 1px solid rgba(255,255,255,0.15) !important;
    margin-bottom: 0.6rem !important;
}
.auth-google-btn:hover {
    background: linear-gradient(135deg, #ff4d66, #d91b38) !important;
    transform: translateY(-1px) !important;
    box-shadow: 0 6px 24px rgba(255,46,76,0.5) !important;
    color: #ffffff !important;
}
/* ── Custom HTML Sidebar Navigation ── */
section[data-testid="stSidebar"] {
    background: #0b0a0e !important;
    border-right: 1px solid rgba(255, 255, 255, 0.05) !important;
}
section[data-testid="stSidebar"] [data-testid="stSidebarUserContent"] {
    padding: 0 !important;
}
/* Hide the Streamlit sidebar padding & element spacing */
section[data-testid="stSidebar"] [data-testid="stVerticalBlock"] > [data-testid="element-container"] {
    margin: 0 !important;
    padding: 0 !important;
}

/* Status pill */
.status-pill-ok {
    display: inline-flex; align-items: center; gap: 5px;
    background: rgba(74,222,128,0.1); border: 1px solid rgba(74,222,128,0.3);
    color: #4ade80; font-size: 0.72rem; font-weight: 600;
    padding: 2px 10px; border-radius: 9999px; margin-left: 8px;
}


</style>
""", unsafe_allow_html=True)


# ── Background OAuth callback server ─────────────────────────────────────────

class _OAuthCallbackHandler(BaseHTTPRequestHandler):
    """Minimal HTTP handler that catches Google's redirect and exchanges the code."""

    def do_GET(self):
        parsed = urllib.parse.urlparse(self.path)
        params = urllib.parse.parse_qs(parsed.query)

        if "code" in params:
            code  = params["code"][0]
            state = params.get("state", [None])[0]
            try:
                exchange_code_for_credentials(
                    code=code,
                    state=state,
                    redirect_uri=OAUTH_REDIRECT_URI,
                )
                html = (
                    b"<html><body style='font-family:sans-serif;text-align:center;background:#09090b;color:#fff;margin-top:120px;'>"
                    b"<h1 style='color:#4ade80;'>&#10003; YouTube Connected!</h1>"
                    b"<p style='color:#a1a1aa;'>Redirecting you back to CreatorPilot...</p>"
                    b"<script>setTimeout(()=>window.close(),1500);</script>"
                    b"</body></html>"
                )
            except Exception as exc:
                html = (
                    f"<html><body style='font-family:sans-serif;text-align:center;background:#09090b;color:#fff;margin-top:80px;'>"
                    f"<h1 style='color:#f87171;'>&#10007; Authentication Failed</h1>"
                    f"<p>{exc}</p></body></html>"
                ).encode()
        else:
            html = b"<html><body style='background:#09090b;color:#fff;'><p>No code received.</p></body></html>"

        self.send_response(200)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.end_headers()
        self.wfile.write(html)

        threading.Thread(target=self.server.shutdown, daemon=True).start()

    def log_message(self, *args):
        pass


def _start_oauth_server() -> bool:
    try:
        server = HTTPServer(("localhost", OAUTH_PORT), _OAuthCallbackHandler)
    except OSError:
        return False

    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    st.session_state["_oauth_server"] = server
    return True


# ── Auth state helpers ────────────────────────────────────────────────────────

def _is_authenticated() -> bool:
    if not os.path.exists(settings.token_file):
        return False
    try:
        creds = get_credentials()
        return creds is not None and creds.valid
    except Exception:
        return False


def _start_signin() -> str:
    _start_oauth_server()
    auth_url, _ = get_authorization_url(redirect_uri=OAUTH_REDIRECT_URI)
    return auth_url


# ── Session state init ────────────────────────────────────────────────────────
st.session_state.setdefault("signing_in", False)
st.session_state.setdefault("auth_url", None)
st.session_state.setdefault("script_title_preset", "")
st.session_state.setdefault("video_inspector_idx", 0)

IS_AUTH = _is_authenticated()


# ── Auto-Redirect Check ───────────────────────────────────────────────────────
# If the user completed Google auth in browser window, automatically transition to main app
if st.session_state.signing_in:
    if IS_AUTH:
        st.session_state.signing_in = False
        st.session_state.auth_url = None
        st.rerun()


# ── Routing: read nav page from query params ─────────────────────────────────
_PAGES = ["Dashboard", "Analyse", "Generate", "Automate"]
nav_option = st.query_params.get("nav", "Dashboard")
if nav_option not in _PAGES:
    nav_option = "Dashboard"

mock_mode = settings.mock_youtube_api

# ── Custom HTML Sidebar ───────────────────────────────────────────────────────
@st.cache_data(ttl=120, show_spinner=False)
def _sidebar_channel_info(is_auth: bool, is_mock: bool) -> dict:
    if is_auth and not is_mock:
        try:
            info = get_own_channel_stats(get_youtube_client())
            if info:
                return info
        except Exception:
            pass
    return {"title": "CreatorPilot Lab", "thumbnail": "https://picsum.photos/100/100"}

_ch    = _sidebar_channel_info(IS_AUTH, mock_mode)
_thumb = _ch.get("thumbnail", "https://picsum.photos/100/100")
_name  = _ch.get("title", "Channel")

_NAV_ITEMS = [
    ("Dashboard", "Dashboard", '<path d="m3 9 9-7 9 7v11a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2z"/><polyline points="9 22 9 12 15 12 15 22"/>'),
    ("Analyse",   "Analyse",   '<line x1="18" y1="20" x2="18" y2="10"/><line x1="12" y1="20" x2="12" y2="4"/><line x1="6" y1="20" x2="6" y2="14"/>'),
    ("Generate",  "Generate",  '<path d="m12 3-1.912 5.813a2 2 0 0 1-1.275 1.275L3 12l5.813 1.912a2 2 0 0 1 1.275 1.275L12 21l1.912-5.813a2 2 0 0 1 1.275-1.275L21 12l-5.813-1.912a2 2 0 0 1-1.275-1.275L12 3z"/>'),
    ("Automate",  "Automate",  '<path d="M4.5 16.5c-1.5 1.26-2 5-2 5s3.74-.5 5-2c.71-.71.79-1.81.2-2.55L4.5 16.5z"/><path d="M12 15l-3-3a22 22 0 0 1 2-3.95A12.88 12.88 0 0 1 22 2c0 2.72-.78 7.5-3.05 11a22.35 22.35 0 0 1-3.95 2z"/>'),
]

def _nav_item_html(key: str, label: str, icon_path: str, active: bool) -> str:
    if active:
        style = (
            "display:flex;align-items:center;gap:16px;padding:16px 22px;"
            "border-radius:0 20px 20px 0;"
            "border-left:6px solid #ff4d73;"
            "background:rgba(255,255,255,0.06);"
            "backdrop-filter:blur(18px);"
            "box-shadow:inset 0 0 0 1px rgba(255,255,255,0.05),0 8px 30px rgba(0,0,0,0.25);"
            "margin:4px 0;cursor:pointer;text-decoration:none;"
            "transition:all 0.25s cubic-bezier(0.4,0,0.2,1);"
        )
        icon_color = "#ff4d73"
        text_style = "color:#ff4d73;font-size:1.05rem;font-weight:600;letter-spacing:0.01em;"
    else:
        style = (
            "display:flex;align-items:center;gap:16px;padding:16px 22px;"
            "border-radius:20px;border-left:6px solid transparent;"
            "background:transparent;margin:4px 0;cursor:pointer;text-decoration:none;"
            "transition:all 0.25s cubic-bezier(0.4,0,0.2,1);"
        )
        icon_color = "#9494a3"
        text_style = "color:#9494a3;font-size:1.05rem;font-weight:500;letter-spacing:0.01em;"

    return (
        f'<a href="?nav={key}" style="{style}" '
        f'onmouseover="this.style.background=\'rgba(255,255,255,0.04)\';'
        f'this.querySelector(\'span\').style.color=\'#fff\';"'
        f'onmouseout="this.style.background=\'{"rgba(255,255,255,0.06)" if active else "transparent"}\';'
        f'this.querySelector(\'span\').style.color=\'{"#ff4d73" if active else "#9494a3"}\';">'
        f'<svg width="22" height="22" viewBox="0 0 24 24" fill="none" stroke="{icon_color}" '
        f'stroke-width="2.2" stroke-linecap="round" stroke-linejoin="round" style="flex-shrink:0;">'
        f'{icon_path}</svg>'
        f'<span style="{text_style}">{label}</span>'
        f'</a>'
    )

_nav_html_items = "".join(
    _nav_item_html(k, lbl, ico, nav_option == k)
    for k, lbl, ico in _NAV_ITEMS
)

_disconnect_btn = ""
if IS_AUTH:
    _disconnect_btn = (
        '<a href="?nav=_disconnect" style="display:flex;align-items:center;gap:12px;'
        'padding:12px 22px;border-radius:14px;background:rgba(255,77,115,0.06);'
        'border:1px solid rgba(255,77,115,0.15);text-decoration:none;margin-top:8px;'
        'transition:all 0.2s ease;" '
        'onmouseover="this.style.background=\'rgba(255,77,115,0.12)\'"'
        'onmouseout="this.style.background=\'rgba(255,77,115,0.06)\'">'
        '<svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="#ff4d73" stroke-width="2" stroke-linecap="round" stroke-linejoin="round">'
        '<path d="M9 21H5a2 2 0 0 1-2-2V5a2 2 0 0 1 2-2h4"/>'
        '<polyline points="16 17 21 12 16 7"/><line x1="21" y1="12" x2="9" y2="12"/></svg>'
        '<span style="color:#ff4d73;font-size:0.9rem;font-weight:500;">Disconnect Channel</span>'
        '</a>'
    )

_sidebar_html = f"""
<div style="
    font-family: Inter, -apple-system, BlinkMacSystemFont, sans-serif;
    padding: 36px 16px 24px 0;
    min-height: 100vh;
    display: flex;
    flex-direction: column;
">
    <!-- Logo -->
    <div style="display:flex;align-items:center;gap:12px;padding:0 22px;margin-bottom:40px;">
        <div style="width:36px;height:36px;background:linear-gradient(135deg,#ff4d73,#c0112c);
            border-radius:10px;display:flex;align-items:center;justify-content:center;
            box-shadow:0 4px 14px rgba(255,77,115,0.4);flex-shrink:0;">
            <svg width="20" height="20" viewBox="0 0 24 24" fill="none" stroke="#fff"
                stroke-width="2.5" stroke-linecap="round" stroke-linejoin="round">
                <polygon points="13 2 3 14 12 14 11 22 21 10 12 10 13 2"/>
            </svg>
        </div>
        <div>
            <div style="font-size:1.45rem;font-weight:800;letter-spacing:-0.03em;line-height:1.1;">
                <span style="color:#fff;">Creator</span><span style="color:#ff4d73;">Pilot</span>
            </div>
            <div style="color:#71717a;font-size:0.74rem;font-weight:500;margin-top:2px;">Studio &amp; AI Copilot</div>
        </div>
    </div>

    <!-- Nav label -->
    <div style="font-size:0.7rem;font-weight:700;letter-spacing:0.12em;text-transform:uppercase;
        color:#71717a;margin-bottom:12px;padding-left:22px;">NAVIGATION</div>

    <!-- Nav items -->
    <div style="display:flex;flex-direction:column;gap:2px;">
        {_nav_html_items}
    </div>

    <div style="flex:1;"></div>

    <!-- Disconnect -->
    <div style="padding:0 6px;">
        {_disconnect_btn}
    </div>

    <!-- Channel card -->
    <div style="margin-top:16px;padding:0 6px;">
        <div style="display:flex;align-items:center;gap:12px;
            background:rgba(255,255,255,0.03);backdrop-filter:blur(18px);
            border:1px solid rgba(255,255,255,0.05);box-shadow:0 8px 30px rgba(0,0,0,0.25);
            border-radius:16px;padding:12px 14px;">
            <img src="{_thumb}" style="width:40px;height:40px;border-radius:50%;
                border:2px solid #ff4d73;flex-shrink:0;object-fit:cover;">
            <div style="overflow:hidden;">
                <div style="font-weight:600;font-size:0.9rem;color:#fff;
                    white-space:nowrap;overflow:hidden;text-overflow:ellipsis;">{_name}</div>
                <div style="color:#4ade80;font-size:0.75rem;font-weight:600;
                    display:flex;align-items:center;gap:5px;margin-top:2px;">
                    <span style="width:6px;height:6px;background:#4ade80;border-radius:50%;
                        display:inline-block;box-shadow:0 0 6px #4ade80;"></span> Connected
                </div>
            </div>
        </div>
    </div>
</div>
"""

with st.sidebar:
    st.markdown(_sidebar_html, unsafe_allow_html=True)

# Handle disconnect via query param
if nav_option == "_disconnect":
    if os.path.exists(settings.token_file):
        os.remove(settings.token_file)
    st.session_state.signing_in = False
    st.session_state.auth_url = None
    st.query_params.clear()
    st.rerun()




# ── Main Header ──────────────────────────────────────────────────────────────
st.markdown("""
<div style="text-align: center; padding: 1.5rem 0 1rem 0;">
    <div class="hero-badge">Creator Studio</div>
    <div class="hero-title" style="font-size:2.8rem;"><span style="color:#ffffff;">Creator</span><span style="color:#ff2e4c;">Pilot</span></div>
    <div class="hero-sub">AI Analytics • Content Generation • Direct Video Publishing</div>
</div>
""", unsafe_allow_html=True)


# ── View A — Not authenticated & Not mock mode ────────────────────────────────
if not IS_AUTH and not mock_mode:
    if not os.path.exists(settings.credentials_file):
        st.error(
            f"`{settings.credentials_file}` not found. "
            "Please download your OAuth 2.0 Client ID JSON from Google Cloud Console."
        )
        st.stop()

    try:
        auth_url = _start_signin()
    except Exception as exc:
        st.error(format_error_one_liner(exc, "Authentication initialization error: "))
        st.stop()

    col_center = st.columns([1, 2, 1])[1]
    with col_center:
        with st.container(border=True):
            st.markdown(
                "<h3 style='font-weight:700;margin:0 0 0.3rem 0;font-size:1.3rem;text-align:center;'>Connect YouTube Channel</h3>",
                unsafe_allow_html=True,
            )
            st.markdown(
                "<p style='color:#a1a1aa;font-size:0.85rem;line-height:1.4;text-align:center;margin-bottom:0.75rem;'>"
                "Authorize CreatorPilot once to access channel analytics, generate AI content scripts, and publish videos directly."
                "</p>",
                unsafe_allow_html=True,
            )
            st.markdown(
                f'<a href="{auth_url}" target="_blank" class="auth-google-btn">🔑 Sign in with Google</a>',
                unsafe_allow_html=True,
            )

            @st.fragment(run_every=1)
            def _auto_auth_detector():
                if _is_authenticated():
                    st.rerun()
                else:
                    st.markdown(
                        "<div style='text-align:center;margin-top:0.2rem;margin-bottom:0.3rem;'>"
                        "<span style='color:#71717a;font-size:0.75rem;display:inline-flex;align-items:center;gap:6px;'>"
                        "<span class='dot-pulse'></span> Waiting for sign-in completion..."
                        "</span></div>",
                        unsafe_allow_html=True,
                    )

            _auto_auth_detector()

    st.stop()


# ── View B — Authenticated / Dry-Run Mode ────────────────────────────────────

# ── Shared data loaders (cached in st.session_state to save API calls) ───────

def _load_channel_data() -> None:
    """Fetch & cache channel_info + yt_client in session_state (runs once)."""
    if "channel_info" not in st.session_state:
        if IS_AUTH and not mock_mode:
            try:
                yt_c = get_youtube_client()
                st.session_state["yt_client"] = yt_c
                st.session_state["channel_info"] = get_own_channel_stats(yt_c)
            except Exception as _e:
                st.session_state["channel_info"] = _MOCK_CHANNEL
        else:
            st.session_state["channel_info"] = _MOCK_CHANNEL


def _load_videos() -> None:
    """Fetch & cache recent_videos in session_state (runs once)."""
    if "recent_videos" not in st.session_state:
        if IS_AUTH and not mock_mode:
            try:
                yt_c = st.session_state.get("yt_client") or get_youtube_client()
                ch = st.session_state.get("channel_info", {})
                vids = get_channel_videos(yt_c, ch.get("uploads_playlist_id"))
                st.session_state["recent_videos"] = vids if vids else _MOCK_VIDEOS
            except Exception:
                st.session_state["recent_videos"] = _MOCK_VIDEOS
        else:
            st.session_state["recent_videos"] = _MOCK_VIDEOS


def _load_timeseries(channel_id: str | None = None) -> None:
    """Fetch & cache 30-day timeseries in session_state (runs once)."""
    if "timeseries_data" not in st.session_state:
        st.session_state["timeseries_data"] = get_analytics_timeseries(
            channel_id=channel_id, days=30
        )


# Run loaders — each only fetches once per session
_load_channel_data()
channel_info = st.session_state["channel_info"]
_ch_id       = channel_info.get("channel_id")
_subs        = channel_info.get("subscriber_count", 0)
_views       = channel_info.get("view_count", 0)
_vids        = channel_info.get("video_count", 0)
_avg         = int(_views / max(1, _vids))
_ch_thumb    = channel_info.get("thumbnail", "https://picsum.photos/100/100")
_ch_name     = channel_info.get("title", "Channel")

_load_timeseries(_ch_id)
timeseries_data = st.session_state["timeseries_data"]
ts_dates = [d["date"]                    for d in timeseries_data]
ts_views = [d["views"]                   for d in timeseries_data]
ts_watch = [d["watch_time_mins"]          for d in timeseries_data]
ts_subs  = [d.get("subs_gained", 0)      for d in timeseries_data]

_load_videos()
recent_videos: list = st.session_state.get("recent_videos") or _MOCK_VIDEOS


# =============================================================================
# DASHBOARD
# =============================================================================
if nav_option == "Dashboard":

    # ── Channel Profile Banner ─────────────────────────────────────────────
    with st.container(border=True):
        b1, b2 = st.columns([1, 6])
        with b1:
            st.markdown(
                f'<img src="{_ch_thumb}" style="width:76px;height:76px;border-radius:50%;'
                f'border:3px solid #ff2e4c;display:block;margin:0.3rem auto;">',
                unsafe_allow_html=True,
            )
        with b2:
            handle    = channel_info.get("custom_url", "")
            handle_md = f"<span style='color:#71717a;font-size:0.8rem;'>@{handle.lstrip('@')}</span>&nbsp;" if handle else ""
            pill      = ('<span class="status-pill-ok"><span class="dot-green"></span> Connected</span>'
                         if IS_AUTH else "")
            desc      = channel_info.get("description", "")
            desc_md   = (f"<div style='color:#a1a1aa;font-size:0.82rem;margin-top:5px;line-height:1.45;'>"
                         f"{desc[:160]}{'…' if len(desc)>160 else ''}</div>" if desc else "")
            st.markdown(
                f"<h2 style='margin:0 0 3px;font-weight:800;font-size:1.45rem;'>{_ch_name}</h2>"
                f"<div>{handle_md}{pill}</div>{desc_md}",
                unsafe_allow_html=True,
            )

    st.write("")

    # ── Key Metrics ────────────────────────────────────────────────────────
    dm1, dm2, dm3, dm4 = st.columns(4)
    with dm1: st.metric("Subscribers",      f"{_subs:,}")
    with dm2: st.metric("Total Views",       f"{_views:,}")
    with dm3: st.metric("Videos Published",  f"{_vids:,}")
    with dm4: st.metric("Avg Views / Video", f"{_avg:,}")

    st.write("")

    chart_col, top_col = st.columns([3, 2])

    # ── 30-Day Performance Chart ───────────────────────────────────────────
    with chart_col:
        with st.container(border=True):
            st.markdown(
                "<h4 style='margin-bottom:0.6rem;font-weight:700;'>📈 30-Day Performance</h4>",
                unsafe_allow_html=True,
            )
            dfig = go.Figure()
            dfig.add_trace(go.Scatter(
                x=ts_dates, y=ts_views, mode="lines", name="Daily Views",
                line=dict(color="#ff2e4c", width=2.5),
                fill="tozeroy", fillcolor="rgba(255,46,76,0.07)"
            ))
            dfig.add_trace(go.Scatter(
                x=ts_dates, y=ts_watch, mode="lines", name="Watch Mins",
                line=dict(color="#a855f7", width=2, dash="dot")
            ))
            if any(s > 0 for s in ts_subs):
                dfig.add_trace(go.Scatter(
                    x=ts_dates, y=ts_subs, mode="lines", name="Subs Gained",
                    line=dict(color="#4ade80", width=1.5, dash="dashdot")
                ))
            dfig.update_layout(
                margin=dict(l=10, r=10, t=10, b=10), height=260,
                legend=dict(orientation="h", yanchor="bottom", y=1.02, xanchor="right", x=1),
                paper_bgcolor="rgba(0,0,0,0)", plot_bgcolor="rgba(0,0,0,0)",
                xaxis=dict(showgrid=True, gridcolor="rgba(255,255,255,0.05)", tickfont=dict(size=10)),
                yaxis=dict(showgrid=True, gridcolor="rgba(255,255,255,0.05)"),
                font=dict(color="#a1a1aa")
            )
            st.plotly_chart(dfig, use_container_width=True)

    # ── Top 5 Videos Leaderboard ───────────────────────────────────────────
    with top_col:
        with st.container(border=True):
            st.markdown(
                "<h4 style='margin-bottom:0.6rem;font-weight:700;'>🏆 Top Videos</h4>",
                unsafe_allow_html=True,
            )
            top5 = sorted(recent_videos, key=lambda v: v.get("view_count", 0), reverse=True)[:5]
            for i, v in enumerate(top5):
                t_url = v.get("thumbnail", "https://picsum.photos/60/34")
                v_cnt = v.get("view_count", 0)
                v_ttl = v.get("title", "Untitled")
                st.markdown(
                    f'<div style="display:flex;align-items:center;gap:10px;padding:7px 0;'
                    f'border-bottom:1px solid rgba(255,255,255,0.05);">'  
                    f'<div style="font-weight:700;color:#ff6680;font-size:0.8rem;min-width:20px;text-align:center;">#{i+1}</div>'
                    f'<img src="{t_url}" style="width:62px;height:35px;border-radius:5px;object-fit:cover;flex-shrink:0;">'
                    f'<div style="overflow:hidden;flex:1;">'
                    f'<div style="font-size:0.78rem;font-weight:600;color:#e4e4e7;'
                    f'white-space:nowrap;overflow:hidden;text-overflow:ellipsis;">{v_ttl}</div>'
                    f'<div style="font-size:0.7rem;color:#71717a;">{v_cnt:,} views</div>'
                    f'</div></div>',
                    unsafe_allow_html=True,
                )

    st.write("")

    # ── Channel Health ─────────────────────────────────────────────────────
    with st.container(border=True):
        st.markdown(
            "<h4 style='margin-bottom:0.8rem;font-weight:700;'>💡 Channel Health & Engagement</h4>",
            unsafe_allow_html=True,
        )
        total_likes    = sum(v.get("like_count", 0)    for v in recent_videos)
        total_comments = sum(v.get("comment_count", 0) for v in recent_videos)
        total_rv       = sum(v.get("view_count", 0)    for v in recent_videos)
        eng_rate       = round((total_likes + total_comments) / max(1, total_rv) * 100, 2)
        like_rate      = round(total_likes / max(1, total_rv) * 100, 2)
        comment_rate   = round(total_comments / max(1, total_rv) * 100, 3)
        views_per_sub  = round(_views / max(1, _subs), 1)

        hc1, hc2, hc3, hc4 = st.columns(4)
        with hc1: st.metric("Engagement Rate",   f"{eng_rate}%",      help="(Likes + Comments) / Views × 100")
        with hc2: st.metric("Like Rate",           f"{like_rate}%",     help="Likes / Views × 100")
        with hc3: st.metric("Comment Rate",        f"{comment_rate}%",  help="Comments / Views × 100")
        with hc4: st.metric("Views / Subscriber", f"{views_per_sub}×", help="Total Views ÷ Subscriber Count")


# =============================================================================
# ANALYSE
# =============================================================================
elif nav_option == "Analyse":

    # Metrics row
    am1, am2, am3, am4 = st.columns(4)
    with am1: st.metric("Subscribers",      f"{_subs:,}")
    with am2: st.metric("Total Views",       f"{_views:,}")
    with am3: st.metric("Videos Published",  f"{_vids:,}")
    with am4: st.metric("Avg Views / Video", f"{_avg:,}")

    st.write("")

    c_chart, c_inspect = st.columns([3, 2])

    # ── 30-Day Chart ───────────────────────────────────────────────────────
    with c_chart:
        with st.container(border=True):
            st.markdown(
                "<h4 style='margin-bottom:0.6rem;font-weight:700;'>30-Day Channel Reach</h4>",
                unsafe_allow_html=True,
            )
            afig = go.Figure()
            afig.add_trace(go.Scatter(
                x=ts_dates, y=ts_views, mode="lines", name="Daily Views",
                line=dict(color="#ff2e4c", width=3),
                fill="tozeroy", fillcolor="rgba(255,46,76,0.07)"
            ))
            afig.add_trace(go.Scatter(
                x=ts_dates, y=ts_watch, mode="lines", name="Watch Time (Mins)",
                line=dict(color="#a855f7", width=2, dash="dot")
            ))
            if any(s > 0 for s in ts_subs):
                afig.add_trace(go.Scatter(
                    x=ts_dates, y=ts_subs, mode="lines", name="Subs Gained",
                    line=dict(color="#4ade80", width=1.5, dash="dashdot")
                ))
            afig.update_layout(
                margin=dict(l=10, r=10, t=10, b=10), height=290,
                legend=dict(orientation="h", yanchor="bottom", y=1.02, xanchor="right", x=1),
                paper_bgcolor="rgba(0,0,0,0)", plot_bgcolor="rgba(0,0,0,0)",
                xaxis=dict(showgrid=True, gridcolor="rgba(255,255,255,0.05)"),
                yaxis=dict(showgrid=True, gridcolor="rgba(255,255,255,0.05)"),
                font=dict(color="#a1a1aa")
            )
            st.plotly_chart(afig, use_container_width=True)

    # ── Video Inspector ────────────────────────────────────────────────────
    with c_inspect:
        with st.container(border=True):
            st.markdown(
                "<h4 style='margin-bottom:0.6rem;font-weight:700;'>🎬 Video Inspector</h4>",
                unsafe_allow_html=True,
            )

            video_titles = [v["title"] for v in recent_videos]

            sel1, sel2 = st.columns([5, 1])
            with sel1:
                sel_title = st.selectbox(
                    "Video",
                    video_titles,
                    index=min(st.session_state["video_inspector_idx"], len(video_titles) - 1),
                    label_visibility="collapsed",
                    key="video_inspector_select",
                )
                # Sync idx if user changed selectbox
                new_idx = video_titles.index(sel_title) if sel_title in video_titles else 0
                if new_idx != st.session_state["video_inspector_idx"]:
                    st.session_state["video_inspector_idx"] = new_idx
            with sel2:
                if st.button("🎲", key="rand_vid_btn", use_container_width=True):
                    st.session_state["video_inspector_idx"] = random.randint(0, len(recent_videos) - 1)
                    st.rerun()

            sel_video  = recent_videos[st.session_state["video_inspector_idx"]]
            vid_id     = sel_video.get("video_id", "")
            thumb_url  = sel_video.get("thumbnail", "")
            yt_url     = sel_video.get("youtube_url", f"https://youtu.be/{vid_id}")
            pub_date   = str(sel_video.get("published_at", ""))[:10]
            v_views    = sel_video.get("view_count", 0)
            v_likes    = sel_video.get("like_count", 0)
            v_comments = sel_video.get("comment_count", 0)
            v_eng      = round((v_likes + v_comments) / max(1, v_views) * 100, 2)

            # Thumbnail
            if thumb_url:
                st.markdown(
                    f'<img src="{thumb_url}" style="width:100%;border-radius:10px;'
                    f'margin-bottom:8px;border:1px solid rgba(255,255,255,0.08);">',
                    unsafe_allow_html=True,
                )

            # Stat grid
            st.markdown(
                f'<div style="display:grid;grid-template-columns:1fr 1fr 1fr;gap:6px;margin-bottom:8px;">'
                f'<div style="background:rgba(255,255,255,0.04);border-radius:8px;padding:8px 4px;text-align:center;">'
                f'<div style="font-size:0.62rem;color:#71717a;font-weight:700;text-transform:uppercase;margin-bottom:2px;">Views</div>'
                f'<div style="font-size:0.88rem;font-weight:700;color:#fff;">{v_views:,}</div></div>'
                f'<div style="background:rgba(255,255,255,0.04);border-radius:8px;padding:8px 4px;text-align:center;">'
                f'<div style="font-size:0.62rem;color:#71717a;font-weight:700;text-transform:uppercase;margin-bottom:2px;">Likes</div>'
                f'<div style="font-size:0.88rem;font-weight:700;color:#4ade80;">{v_likes:,}</div></div>'
                f'<div style="background:rgba(255,255,255,0.04);border-radius:8px;padding:8px 4px;text-align:center;">'
                f'<div style="font-size:0.62rem;color:#71717a;font-weight:700;text-transform:uppercase;margin-bottom:2px;">Comments</div>'
                f'<div style="font-size:0.88rem;font-weight:700;color:#a855f7;">{v_comments:,}</div></div>'
                f'</div>'
                f'<div style="display:flex;justify-content:space-between;align-items:center;margin-bottom:8px;">'
                f'<span style="font-size:0.74rem;color:#71717a;">Engagement: <b style="color:#ff6680;">{v_eng}%</b></span>'
                f'<span style="font-size:0.72rem;color:#52525b;">{pub_date}</span>'
                f'</div>',
                unsafe_allow_html=True,
            )

            if yt_url and not vid_id.startswith("vid_"):
                st.markdown(
                    f'<a href="{yt_url}" target="_blank" style="display:block;text-align:center;'
                    f'color:#60a5fa;font-size:0.78rem;text-decoration:none;margin-bottom:8px;">'
                    f'▶ Watch on YouTube</a>',
                    unsafe_allow_html=True,
                )

            # AI Critique — cached per video_id to save tokens
            critique_key = f"video_critique_{vid_id}"
            is_cached    = critique_key in st.session_state
            btn_lbl      = "🔄 Re-run AI Critique" if is_cached else "🤖 Run Gemini AI Critique"

            if st.button(btn_lbl, type="primary", use_container_width=True, key=f"crit_{vid_id}"):
                with st.spinner("Analysing with Gemini..."):
                    try:
                        analysis = analyse_video_performance(sel_video, channel_info)
                        st.session_state[critique_key] = analysis
                        st.rerun()
                    except Exception as err:
                        st.error(format_error_one_liner(err, "Analysis failed: "))

            if is_cached:
                with st.expander("🤖 AI Analysis", expanded=True):
                    st.markdown(st.session_state[critique_key])

    # ── Competitor Benchmark ───────────────────────────────────────────────
    st.write("")
    with st.container(border=True):
        st.markdown(
            "<h4 style='font-weight:700;margin-bottom:0.3rem;'>⚔️ Competitor Benchmark</h4>",
            unsafe_allow_html=True,
        )
        st.caption("Compare your channel's volume and audience scale against any creator.")
        st.write("")

        cc1, cc2 = st.columns([3, 1])
        with cc1:
            comp_input = st.text_input(
                "Handle",
                placeholder="e.g. @mkbhd or @Fireship",
                label_visibility="collapsed",
                key="comp_input",
            )
        with cc2:
            run_comp = st.button("Compare", use_container_width=True, key="run_comp_btn")

        comp_key    = f"comp_stats_{comp_input}"
        comp_ai_key = f"comp_ai_{comp_input}"

        if run_comp and comp_input and comp_key not in st.session_state:
            with st.spinner("Fetching channel stats..."):
                try:
                    st.session_state[comp_key] = get_public_channel_stats(comp_input)
                except Exception as exc:
                    st.error(format_error_one_liner(exc, "Comparison failed: "))

        if comp_input and comp_key in st.session_state:
            comp_stats = st.session_state[comp_key]
            c_my, c_vs, c_other = st.columns([2, 1, 2])
            with c_my:
                st.markdown(f"**{channel_info['title']}** *(Your Channel)*")
                st.write(f"Subscribers: `{channel_info['subscriber_count']:,}`")
                st.write(f"Total Views: `{channel_info['view_count']:,}`")
                st.write(f"Videos: `{channel_info['video_count']:,}`")
            with c_vs:
                st.markdown(
                    "<div style='text-align:center;padding-top:0.8rem;color:#71717a;font-weight:700;font-size:1.2rem;'>VS</div>",
                    unsafe_allow_html=True,
                )
            with c_other:
                st.markdown(f"**{comp_stats['title']}** (`{comp_stats['handle']}`")
                st.write(f"Subscribers: `{comp_stats['subscriber_count']:,}`")
                st.write(f"Total Views: `{comp_stats['view_count']:,}`")
                st.write(f"Videos: `{comp_stats['video_count']:,}`")

            st.markdown("<hr style='border-color:rgba(255,255,255,0.08);'>", unsafe_allow_html=True)

            if comp_ai_key not in st.session_state:
                with st.spinner("Generating AI comparative breakdown..."):
                    try:
                        st.session_state[comp_ai_key] = compare_channels_ai(channel_info, comp_stats)
                    except Exception as exc:
                        st.error(format_error_one_liner(exc, "AI comparison failed: "))

            if comp_ai_key in st.session_state:
                st.markdown(st.session_state[comp_ai_key])


# =============================================================================
# GENERATE
# =============================================================================
elif nav_option == "Generate":
    gen_mode = st.radio(
        "Tool Mode",
        ["💡 Viral Video Ideas", "📝 Full Script Writer"],
        horizontal=True,
        label_visibility="collapsed",
    )
    st.write("")

    if gen_mode == "💡 Viral Video Ideas":
        with st.container(border=True):
            st.markdown(
                "<h4 style='font-weight:700;margin-bottom:0.3rem;'>Generate Viral Ideas</h4>",
                unsafe_allow_html=True,
            )
            st.caption("Gemini analyses your channel domain and recent topics to generate high-CTR concepts.")
            st.write("")

            c_num, c_btn = st.columns([2, 1])
            with c_num:
                n_ideas = st.slider("Number of ideas", 3, 8, 5)
            with c_btn:
                st.write("")
                run_ideas = st.button("Generate Ideas", type="primary", use_container_width=True)

            if run_ideas:
                with st.spinner("Brainstorming with Gemini..."):
                    try:
                        ideas = generate_video_ideas(channel_info, recent_videos, n_ideas=n_ideas)
                        st.session_state["generated_ideas"] = ideas
                    except Exception as err:
                        st.error(format_error_one_liner(err, "Idea generation failed: "))

        if "generated_ideas" in st.session_state:
            for idx, idea in enumerate(st.session_state["generated_ideas"]):
                with st.container(border=True):
                    st.markdown(
                        f"<h4 style='color:#ff526c;font-weight:700;'>Idea #{idx+1}: {idea.get('title')}</h4>",
                        unsafe_allow_html=True,
                    )
                    st.write(f"**Hook:** {idea.get('hook')}")
                    st.write(f"**Description:** {idea.get('description')}")
                    if idea.get("tags"):
                        st.markdown("**Tags:** " + " ".join(f"`#{t}`" for t in idea["tags"]))
                    st.write("")
                    if st.button(f"Draft Script for Idea #{idx+1}", key=f"btn_idea_{idx}"):
                        st.session_state["script_title_preset"] = idea.get("title", "")
                        st.info("Title saved! Switch to the **Full Script Writer** tool above.")

    elif gen_mode == "📝 Full Script Writer":
        with st.container(border=True):
            st.markdown(
                "<h4 style='font-weight:700;margin-bottom:0.3rem;'>Full Script Writer</h4>",
                unsafe_allow_html=True,
            )
            st.caption("Generate a complete structured script with scene cues and section timestamps.")
            st.write("")

            script_title  = st.text_input(
                "Video Title",
                value=st.session_state.get("script_title_preset", ""),
                placeholder="e.g. 5 AI Automation Tools That Save 10 Hours a Week",
            )
            channel_niche = st.text_input("Channel Topic / Niche", placeholder="e.g. Technology, AI Tools, Coding")
            duration      = st.slider("Target Duration (Minutes)", 2, 20, 5)

            st.write("")
            if st.button("Generate Complete Script", type="primary", use_container_width=True):
                if not script_title:
                    st.warning("Please enter a video title.")
                else:
                    with st.spinner("Writing your script with Gemini..."):
                        try:
                            script_text = generate_video_script(
                                video_title=script_title,
                                channel_topic=channel_niche,
                                duration_mins=duration,
                            )
                            st.session_state["last_script"] = script_text
                        except Exception as err:
                            st.error(format_error_one_liner(err, "Script generation failed: "))

        if "last_script" in st.session_state:
            st.markdown("---")
            st.markdown(st.session_state["last_script"])


# =============================================================================
# AUTOMATE
# =============================================================================
elif nav_option == "Automate":
    MIME_MAP = {
        ".mp4": "video/mp4",
        ".mov": "video/quicktime",
        ".avi": "video/x-msvideo",
        ".mkv": "video/x-matroska",
        ".mp3": "audio/mpeg",
        ".m4a": "audio/mp4",
        ".wav": "audio/wav",
    }
    if mock_mode:
        st.info("Dry-Run Mode — upload will be simulated without publishing.")

    # ── Step 1: Upload & AI Metadata Generation ───────────────────────────
    with st.container(border=True):
        st.markdown(
            "<h4 style='font-weight:700;margin-bottom:0.3rem;'>Step 1 — Upload Video</h4>",
            unsafe_allow_html=True,
        )
        st.caption("Upload your video. Gemini will transcribe it and generate the title, description, and tags.")
        st.write("")

        uploaded_file = st.file_uploader(
            "Select Video or Audio File",
            type=["mp4", "mov", "avi", "mkv", "mp3", "m4a", "wav"],
            help="Supported: MP4, MOV, AVI, MKV, MP3, M4A, WAV",
            key="automate_uploader",
        )

        if uploaded_file:
            ext  = os.path.splitext(uploaded_file.name)[1].lower()
            mime = MIME_MAP.get(ext, "video/mp4")

            col_ai, col_clear = st.columns([3, 1])
            with col_ai:
                run_ai = st.button(
                    "✨ Transcribe & Generate Metadata with Gemini",
                    type="primary",
                    use_container_width=True,
                    disabled=not bool(settings.gemini_api_key or os.environ.get("GEMINI_API_KEY")),
                )
            with col_clear:
                if st.button("Clear", use_container_width=True):
                    for k in ("ai_title", "ai_desc", "ai_tags", "ai_transcript"):
                        st.session_state.pop(k, None)
                    st.rerun()

            if run_ai:
                with st.spinner("Uploading to Gemini & transcribing — this may take 15-30 seconds..."):
                    try:
                        raw = transcribe_and_generate_metadata(
                            file_bytes=uploaded_file.getvalue(),
                            mime_type=mime,
                            file_name=uploaded_file.name,
                        )
                        st.session_state["ai_title"]      = raw.get("title", "")
                        st.session_state["ai_desc"]       = raw.get("description", "")
                        st.session_state["ai_tags"]       = ", ".join(raw.get("tags", []))
                        st.session_state["ai_transcript"] = raw.get("transcript", "")
                        st.success("Metadata generated! Review and edit below before publishing.")
                    except Exception as err:
                        st.warning(format_error_one_liner(err, "⏱️ "))
                        base_name = os.path.splitext(uploaded_file.name)[0].replace("_", " ").replace("-", " ").title()
                        st.session_state.setdefault("ai_title", base_name)
                        st.session_state.setdefault("ai_desc", f"Video upload for {base_name}")
                        st.session_state.setdefault("ai_tags", "video, youtube, creator")

            if st.session_state.get("ai_transcript"):
                with st.expander("📄 View Transcript", expanded=False):
                    st.text_area(
                        "Transcript",
                        value=st.session_state["ai_transcript"],
                        height=220,
                        disabled=True,
                        label_visibility="collapsed",
                    )

    # ── Step 2: Review & Publish ──────────────────────────────────────────
    with st.container(border=True):
        st.markdown(
            "<h4 style='font-weight:700;margin-bottom:0.3rem;'>Step 2 — Review & Publish</h4>",
            unsafe_allow_html=True,
        )
        st.write("")

        with st.form("upload_form", clear_on_submit=False):
            title_input = st.text_input(
                "Title",
                value=st.session_state.get("ai_title", ""),
                placeholder="Enter video title…",
            )
            desc_input = st.text_area(
                "Description",
                value=st.session_state.get("ai_desc", ""),
                height=120,
                placeholder="Describe your video…",
            )
            tags_raw = st.text_input(
                "Tags",
                value=st.session_state.get("ai_tags", ""),
                placeholder="python, tutorial, coding",
            )
            col1, col2 = st.columns(2)
            with col1:
                privacy = st.selectbox("Privacy", ["private", "unlisted", "public"], index=0)
            with col2:
                category_id = st.selectbox(
                    "Category",
                    list(CATEGORIES.keys()),
                    format_func=lambda x: CATEGORIES[x],
                )
            submitted = st.form_submit_button(
                "🚀 Publish to YouTube",
                type="primary",
                use_container_width=True,
                disabled=not uploaded_file,
            )

    if submitted and uploaded_file:
        tags = [t.strip() for t in tags_raw.split(",") if t.strip()]
        ext  = os.path.splitext(uploaded_file.name)[1]

        with st.spinner("Uploading video to YouTube..."):
            with tempfile.NamedTemporaryFile(delete=False, suffix=ext) as tmp:
                tmp.write(uploaded_file.read())
                tmp_path = tmp.name

            try:
                result = upload_video(
                    file_path=tmp_path,
                    title=title_input or os.path.splitext(uploaded_file.name)[0],
                    description=desc_input,
                    privacy_status=privacy,
                    category_id=category_id,
                    tags=tags,
                    mock_mode=mock_mode,
                )

                st.balloons()
                st.success("🎉 Video published successfully!")

                c1, c2 = st.columns(2)
                with c1:
                    st.metric("Status",  result["status"].upper())
                    st.metric("Privacy", result["privacy_status"])
                with c2:
                    st.metric("Video ID", result["video_id"])

                st.markdown(
                    f'<div style="padding:16px;background:rgba(66,133,244,0.1);border:1px solid rgba(66,133,244,0.3);'
                    f'border-radius:10px;text-align:center;margin-top:16px;">'
                    f'<h4 style="margin:0 0 6px;color:#60a5fa;">Watch on YouTube</h4>'
                    f'<a href="{result["youtube_url"]}" target="_blank" style="color:#93c5fd;font-weight:600;">'
                    f'{result["youtube_url"]}</a></div>',
                    unsafe_allow_html=True,
                )

            except Exception as err:
                st.error(format_error_one_liner(err, "Upload failed: "))

            finally:
                if os.path.exists(tmp_path):
                    os.remove(tmp_path)


