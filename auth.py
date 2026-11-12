"""
HTTP Basic Authentication middleware for the Simple Todo application.

Provides optional htpasswd-based authentication with session cookie persistence.
On first visit (or after session expiry), users see a login form instead of a
browser-native Basic Auth dialog. After successful login, a signed cookie keeps
them authenticated for SESSION_EXPIRY_DAYS (configurable in config.py).
"""
import os
import base64
import binascii
import hashlib
import hmac
import json
import secrets
import time
from urllib.parse import urlencode

from fastapi import Request
from fastapi.responses import JSONResponse, RedirectResponse
from starlette.middleware.base import BaseHTTPMiddleware
from starlette._utils import get_route_path
from storage import data_path, external_data_enabled, session_secret

# Optional. Only required if Basic Auth is enabled and your htpasswd uses
# hashed formats like $apr1$ (Apache MD5) or bcrypt.
try:
    from passlib.hash import apr_md5_crypt, bcrypt
except Exception:
    apr_md5_crypt = None
    bcrypt = None


# ---------------------------------------------------------------------------
# Configuration helpers
# ---------------------------------------------------------------------------

_HTPASSWD_CACHE = {
    "path": None,
    "mtime": None,
    "users": {},
}

# Session signing secret. Read from env var so sessions survive restarts.
# If not set, falls back to a random per-process key (sessions lost on restart).
_SESSION_SECRET = os.getenv("SIMPLE_TODO_SESSION_SECRET") or secrets.token_hex(32)

# Session cookie defaults
SESSION_COOKIE_NAME = "simple_todo_session"
_SESSION_EXPIRY_DAYS = 30  # fallback; overridden by config.SESSION_EXPIRY_DAYS


def _get_session_expiry_days() -> int:
    """Get session expiry from config, with safe fallback."""
    try:
        from config import SESSION_EXPIRY_DAYS
        return int(SESSION_EXPIRY_DAYS)
    except Exception:
        return _SESSION_EXPIRY_DAYS


def _basic_auth_enabled() -> bool:
    """Check if basic authentication is enabled via environment variable."""
    v = (os.getenv("SIMPLE_TODO_BASIC_AUTH") or "0").strip().lower()
    return v in ("1", "true", "yes", "on")


def _scoped_api_key_user(request: Request, scope: str) -> str | None:
    """Authenticate a bearer key for one configured API scope."""
    header = request.headers.get("authorization", "")
    scheme, _, token = header.partition(" ")
    expected = os.environ.get(f"SIMPLE_TODO_{scope}_API_KEY_SHA256", "").strip().lower()
    user = os.environ.get(f"SIMPLE_TODO_{scope}_API_USER", "").strip()
    if scheme.lower() != "bearer" or not token or not user or len(expected) != 64:
        return None
    if not all(c in "0123456789abcdef" for c in expected):
        return None
    actual = hashlib.sha256(token.encode("utf-8")).hexdigest()
    return user if hmac.compare_digest(actual, expected) else None


def _htpasswd_path() -> str:
    """Get the path to the htpasswd file from environment variable."""
    filename = (os.getenv("SIMPLE_TODO_HTPASSWD_PATH") or ".htpasswd").strip()
    return str(data_path(filename))


def _load_htpasswd_users(path: str) -> dict[str, str]:
    """Parse .htpasswd into {username: stored_hash}."""
    try:
        st = os.stat(path)
    except FileNotFoundError:
        return {}

    if _HTPASSWD_CACHE["path"] == path and _HTPASSWD_CACHE["mtime"] == st.st_mtime:
        return _HTPASSWD_CACHE["users"]

    users: dict[str, str] = {}
    with open(path, "r", encoding="utf-8", errors="replace") as f:
        for raw in f:
            line = raw.strip()
            if not line or line.startswith("#"):
                continue
            if ":" not in line:
                continue
            user, stored = line.split(":", 1)
            user = user.strip()
            stored = stored.strip()
            if user:
                users[user] = stored

    _HTPASSWD_CACHE["path"] = path
    _HTPASSWD_CACHE["mtime"] = st.st_mtime
    _HTPASSWD_CACHE["users"] = users
    return users


# ---------------------------------------------------------------------------
# Password verification (htpasswd formats)
# ---------------------------------------------------------------------------

def _verify_htpasswd_password(password: str, stored: str) -> bool:
    """Verify user password against an htpasswd hash.

    Supported:
      - {SHA}base64(sha1(password))  (no deps)
      - $apr1$... (Apache MD5)       (requires passlib)
      - $2a$/$2b$/$2y$... (bcrypt)   (requires passlib + bcrypt)
      - plaintext fallback
    """
    if stored.startswith("{SHA}"):
        try:
            want = stored[len("{SHA}"):]
            got = base64.b64encode(hashlib.sha1(password.encode("utf-8")).digest()).decode("ascii")
            return hmac.compare_digest(got, want)
        except Exception:
            return False

    if stored.startswith("$apr1$"):
        if apr_md5_crypt is None:
            return False
        try:
            return apr_md5_crypt.verify(password, stored)
        except Exception:
            return False

    if stored.startswith("$2a$") or stored.startswith("$2b$") or stored.startswith("$2y$"):
        if bcrypt is None:
            return False
        try:
            return bcrypt.verify(password, stored)
        except Exception:
            return False

    if not stored.startswith("$") and not stored.startswith("{"):
        return hmac.compare_digest(password, stored)

    return False


# ---------------------------------------------------------------------------
# HTTP Basic Auth header check (kept for API/sync clients)
# ---------------------------------------------------------------------------

def _authenticate_basic(request: Request) -> bool:
    """Authenticate a request using HTTP Basic Auth."""
    header = request.headers.get("authorization") or ""
    if not header.lower().startswith("basic "):
        return False

    token = header.split(" ", 1)[1].strip()
    try:
        decoded = base64.b64decode(token).decode("utf-8")
    except (binascii.Error, UnicodeDecodeError):
        return False

    if ":" not in decoded:
        return False

    username, password = decoded.split(":", 1)
    users = _load_htpasswd_users(_htpasswd_path())
    stored = users.get(username)
    if not stored:
        return False
    return _verify_htpasswd_password(password, stored)


def _get_basic_auth_user(request: Request) -> str | None:
    """Extract username from Basic Auth header, or None."""
    header = request.headers.get("authorization") or ""
    if not header.lower().startswith("basic "):
        return None
    try:
        token = header.split(" ", 1)[1].strip()
        decoded = base64.b64decode(token).decode("utf-8")
        username, _ = decoded.split(":", 1)
        return username
    except Exception:
        return None


# ---------------------------------------------------------------------------
# Signed session cookie
# ---------------------------------------------------------------------------

def _signing_key() -> bytes:
    """Derive a signing key from the session secret.

    External-directory mode persists a private key in that directory unless
    SIMPLE_TODO_SESSION_SECRET is supplied. Legacy mode keeps its old fallback.
    """
    secret = session_secret() if external_data_enabled() else _SESSION_SECRET
    return hashlib.sha256(secret.encode("utf-8")).digest()


def _sign_session(username: str) -> str:
    """Create a signed session cookie value: base64(json) . signature."""
    payload = json.dumps({
        "u": username,
        "t": int(time.time()),
    }, separators=(",", ":"))
    payload_b64 = base64.urlsafe_b64encode(payload.encode("utf-8")).decode("ascii")
    sig = hmac.new(_signing_key(), payload_b64.encode("ascii"), hashlib.sha256).hexdigest()
    return f"{payload_b64}.{sig}"


def _verify_session(cookie_value: str) -> str | None:
    """Verify a session cookie. Returns username if valid, else None."""
    if not cookie_value or "." not in cookie_value:
        return None

    parts = cookie_value.rsplit(".", 1)
    if len(parts) != 2:
        return None

    payload_b64, sig = parts

    # Verify signature
    expected_sig = hmac.new(_signing_key(), payload_b64.encode("ascii"), hashlib.sha256).hexdigest()
    if not hmac.compare_digest(sig, expected_sig):
        return None

    # Decode payload
    try:
        payload = json.loads(base64.urlsafe_b64decode(payload_b64).decode("utf-8"))
    except Exception:
        return None

    username = payload.get("u")
    created = payload.get("t", 0)
    if not username:
        return None

    # Check expiry
    expiry_seconds = _get_session_expiry_days() * 86400
    if time.time() - created > expiry_seconds:
        return None

    # Check user still exists in htpasswd
    users = _load_htpasswd_users(_htpasswd_path())
    if username not in users:
        return None

    return username


def verify_credentials(username: str, password: str) -> bool:
    """Verify username/password against htpasswd file."""
    users = _load_htpasswd_users(_htpasswd_path())
    stored = users.get(username)
    if not stored:
        return False
    return _verify_htpasswd_password(password, stored)


# ---------------------------------------------------------------------------
# Public helpers used by main.py
# ---------------------------------------------------------------------------

def get_authenticated_user(request: Request) -> str:
    """Extract the authenticated username from the request, or 'default' if not authenticated."""
    key_user = getattr(request.state, "scoped_api_user", None)
    if key_user:
        return key_user
    user = "default"
    if _basic_auth_enabled():
        # Check session cookie first
        cookie = request.cookies.get(SESSION_COOKIE_NAME)
        session_user = _verify_session(cookie)
        if session_user:
            return session_user

        # Fall back to Basic Auth header (for API clients)
        header = request.headers.get("authorization") or ""
        if header.lower().startswith("basic "):
            try:
                token = header.split(" ", 1)[1].strip()
                decoded = base64.b64decode(token).decode("utf-8")
                user, _ = decoded.split(":", 1)
            except Exception:
                pass
    return user


# ---------------------------------------------------------------------------
# Middleware
# ---------------------------------------------------------------------------

class BasicAuthMiddleware(BaseHTTPMiddleware):
    """Optional HTTP Basic Auth with session cookie persistence.

    Authentication check order:
    1. Skip public paths (static, favicon, etc.)
    2. Check for valid session cookie → allow through
    3. Check for Basic Auth header → allow through (for API/sync clients)
    4. Redirect to /login page
    """

    async def dispatch(self, request: Request, call_next):
        path = get_route_path(request.scope) or "/"
        # A bearer credential is valid only for this read-only endpoint. Handle
        # it before cookies so it always selects the configured user's data.
        scopes = {"/api/bills": "BILLS", "/api/calendar/events": "CALENDAR"}
        if path in scopes and request.method == "GET":
            authorization = request.headers.get("authorization", "")
            if authorization.split(" ", 1)[0].lower() == "bearer":
                user = _scoped_api_key_user(request, scopes[path])
                if not user:
                    return JSONResponse({"error": "Unauthorized"}, status_code=401,
                                        headers={"WWW-Authenticate": "Bearer"})
                request.state.scoped_api_user = user
                return await call_next(request)

        if not _basic_auth_enabled():
            return await call_next(request)

        # Match backend routes whether the ASGI server includes the public prefix
        # in scope['path'] or Caddy has already stripped it.

        # Public routes that should never trigger an auth challenge.
        public_paths = {
            "/favicon.ico",
            "/apple-touch-icon.png",
            "/apple-touch-icon-precomposed.png",
            "/robots.txt",
            "/manifest.json",
            "/site.webmanifest",
            "/sw.js",
            "/offline",
            "/login",
        }
        public_prefixes = (
            "/static/",
        )
        if path in public_paths or any(path.startswith(pfx) for pfx in public_prefixes):
            return await call_next(request)

        # 1. Session cookie (works for all routes)
        cookie = request.cookies.get(SESSION_COOKIE_NAME)
        if _verify_session(cookie):
            return await call_next(request)

        # 2. API/sync endpoints: accept Basic Auth header (for curl, sync clients)
        #    and return 401 JSON on failure (not an HTML redirect).
        is_api = path.startswith("/api/") or path.startswith("/sync/")
        if is_api:
            if _authenticate_basic(request):
                return await call_next(request)
            return JSONResponse(
                {"error": "Unauthorized"},
                status_code=401,
                headers={"WWW-Authenticate": 'Basic realm="simple_todo"'},
            )

        # 3. Browser page requests without a session cookie → redirect to login.
        #    We intentionally do NOT check the Basic Auth header here, because
        #    browsers cache and re-send it automatically, which would bypass the
        #    login page and defeat the purpose of session cookies.
        next_url = path + (f"?{request.url.query}" if request.url.query else "")
        login_url = "/login?" + urlencode({"next": next_url})
        return RedirectResponse(url=login_url, status_code=302)
