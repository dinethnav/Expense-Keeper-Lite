"""Auth utilities — password hashing, session helpers, Google OAuth."""
import os
import urllib.parse
import bcrypt as _bcrypt
import httpx
from fastapi import Request, HTTPException
from fastapi.responses import RedirectResponse

GOOGLE_CLIENT_ID     = os.environ.get("GOOGLE_CLIENT_ID", "")
GOOGLE_CLIENT_SECRET = os.environ.get("GOOGLE_CLIENT_SECRET", "")
GOOGLE_AUTH_URL      = "https://accounts.google.com/o/oauth2/v2/auth"
GOOGLE_TOKEN_URL     = "https://oauth2.googleapis.com/token"
GOOGLE_USERINFO_URL  = "https://www.googleapis.com/oauth2/v2/userinfo"

GOOGLE_ENABLED = bool(GOOGLE_CLIENT_ID and GOOGLE_CLIENT_SECRET)


def hash_password(password: str) -> str:
    return _bcrypt.hashpw(password[:72].encode(), _bcrypt.gensalt(12)).decode()


def verify_password(plain: str, hashed: str) -> bool:
    try:
        return _bcrypt.checkpw(plain[:72].encode(), hashed.encode())
    except Exception:
        return False


def get_redirect_base(request: Request) -> str:
    """Return the public base URL for OAuth callbacks."""
    domains = os.environ.get("REPLIT_DOMAINS", "")
    if domains:
        return f"https://{domains.split(',')[0]}"
    host = request.headers.get("host", "localhost:8000")
    return f"https://{host}"


def get_session_user(request: Request):
    """Return the logged-in user dict or None."""
    from main import get_db  # imported lazily to avoid circular
    user_id = request.session.get("user_id")
    if not user_id:
        return None
    with get_db() as conn:
        row = conn.execute(
            "SELECT id, email, name, is_admin, is_active FROM users WHERE id=?", (user_id,)
        ).fetchone()
    if not row:
        return None
    user = dict(row)
    if not user["is_active"]:
        return None
    return user


def require_user(request: Request) -> dict:
    """For API routes — raise 401 if not logged in."""
    user = get_session_user(request)
    if not user:
        raise HTTPException(status_code=401, detail="Not authenticated")
    return user


def require_user_id(request: Request) -> int:
    return require_user(request)["id"]


def require_admin(request: Request) -> dict:
    user = require_user(request)
    if not user.get("is_admin"):
        raise HTTPException(status_code=403, detail="Admin only")
    return user


def redirect_if_not_logged_in(request: Request):
    """For page routes — return redirect response if not logged in, else None."""
    if not get_session_user(request):
        return RedirectResponse("/login", status_code=302)
    return None


def google_auth_url(request: Request) -> str:
    redirect_uri = get_redirect_base(request) + "/auth/google/callback"
    params = {
        "client_id": GOOGLE_CLIENT_ID,
        "redirect_uri": redirect_uri,
        "response_type": "code",
        "scope": "openid email profile",
        "access_type": "online",
        "prompt": "select_account",
    }
    return GOOGLE_AUTH_URL + "?" + urllib.parse.urlencode(params)


async def google_exchange_code(code: str, request: Request) -> dict:
    """Exchange OAuth code for user info dict."""
    redirect_uri = get_redirect_base(request) + "/auth/google/callback"
    async with httpx.AsyncClient() as client:
        tok = await client.post(GOOGLE_TOKEN_URL, data={
            "code": code,
            "client_id": GOOGLE_CLIENT_ID,
            "client_secret": GOOGLE_CLIENT_SECRET,
            "redirect_uri": redirect_uri,
            "grant_type": "authorization_code",
        })
        tok.raise_for_status()
        token = tok.json()
        info = await client.get(GOOGLE_USERINFO_URL,
            headers={"Authorization": f"Bearer {token['access_token']}"})
        info.raise_for_status()
        return info.json()
