"""OAuth sign-in (X and Google) for Annotated.

Flow: /auth/<p>/start redirects to the provider. The provider calls back to
/auth/<p>/callback on this server, which completes the login and renders a
short link token. The user pastes that token into the Chrome extension, which
verifies it via POST /auth/token/verify and stores the handle + token.
"""

import base64
import hashlib
import os
import secrets
import sqlite3
import time
import urllib.parse

import httpx
from fastapi import APIRouter, Request
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse

router = APIRouter(prefix="/auth")

X_CLIENT_ID = os.environ.get("X_CLIENT_ID", "")
X_CLIENT_SECRET = os.environ.get("X_CLIENT_SECRET", "")
GOOGLE_CLIENT_ID = os.environ.get("GOOGLE_CLIENT_ID", "")
GOOGLE_CLIENT_SECRET = os.environ.get("GOOGLE_CLIENT_SECRET", "")
BASE = os.environ.get("PUBLIC_BASE_URL", "https://annotated-api.onrender.com")

db: sqlite3.Connection


def init(conn: sqlite3.Connection):
    global db
    db = conn


def _now() -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())


def _pkce_pair():
    verifier = secrets.token_urlsafe(64)
    digest = hashlib.sha256(verifier.encode()).digest()
    challenge = base64.urlsafe_b64encode(digest).rstrip(b"=").decode()
    return verifier, challenge


def _link_token_page(handle: str, token: str, provider: str) -> HTMLResponse:
    label = "X" if provider == "x" else "Google"
    html = f"""<!doctype html><html><head><meta charset=utf-8>
<title>Annotated — signed in</title>
<style>body{{font-family:system-ui,sans-serif;max-width:560px;margin:60px auto;
padding:0 20px;text-align:center}}code{{font-size:28px;background:#f4f4f5;
padding:12px 20px;border-radius:10px;letter-spacing:2px}}</style></head><body>
<h2>Signed in with {label} as @{handle}</h2>
<p>Paste this code into the Annotated extension to link your account:</p>
<code>{token}</code>
<p style="color:#666">Keep this tab open until the extension confirms.</p>
</body></html>"""
    return HTMLResponse(html)


def _issue_link_token(handle: str, provider: str, provider_id: str) -> str:
    token = secrets.token_urlsafe(9)
    db.execute(
        "INSERT OR REPLACE INTO link_tokens (token, handle, provider, provider_id, created_at)"
        " VALUES (?,?,?,?,?)",
        (token, handle, provider, provider_id, _now()),
    )
    db.commit()
    return token


def _upsert_profile(handle: str, provider: str, provider_id: str):
    row = db.execute("SELECT handle FROM profiles WHERE handle=?", (handle,)).fetchone()
    if row:
        col = "x_id" if provider == "x" else "google_id"
        db.execute(f"UPDATE profiles SET {col}=? WHERE handle=?", (provider_id, handle))
    else:
        col = "x_id" if provider == "x" else "google_id"
        db.execute(
            f"INSERT INTO profiles (handle, display_name, {col}, created_at)"
            " VALUES (?,?,?,?)",
            (handle, handle, provider_id, _now()),
        )
    db.commit()


@router.get("/x/start")
def x_start():
    if not X_CLIENT_ID:
        return JSONResponse({"error": "X sign-in is not configured yet"}, status_code=503)
    verifier, challenge = _pkce_pair()
    state = secrets.token_urlsafe(24)
    db.execute(
        "INSERT INTO oauth_states (state, verifier, provider, created_at) VALUES (?,?,?,?)",
        (state, verifier, "x", _now()),
    )
    db.commit()
    params = {
        "response_type": "code",
        "client_id": X_CLIENT_ID,
        "redirect_uri": f"{BASE}/auth/x/callback",
        "scope": "tweet.read users.read",
        "state": state,
        "code_challenge": challenge,
        "code_challenge_method": "S256",
    }
    return RedirectResponse("https://twitter.com/i/oauth2/authorize?" + urllib.parse.urlencode(params))


@router.get("/x/callback")
def x_callback(code: str = "", state: str = "", error: str = ""):
    if error:
        return HTMLResponse(f"<h2>X sign-in failed: {error}</h2>", status_code=400)
    row = db.execute(
        "SELECT verifier FROM oauth_states WHERE state=? AND provider='x'", (state,)
    ).fetchone()
    if not row:
        return HTMLResponse("<h2>Invalid or expired login session. Try again.</h2>", status_code=400)
    db.execute("DELETE FROM oauth_states WHERE state=?", (state,))
    db.commit()
    try:
        r = httpx.post(
            "https://api.twitter.com/2/oauth2/token",
            auth=(X_CLIENT_ID, X_CLIENT_SECRET),
            data={
                "grant_type": "authorization_code",
                "code": code,
                "redirect_uri": f"{BASE}/auth/x/callback",
                "code_verifier": row[0],
            },
            timeout=20,
        )
        r.raise_for_status()
        access = r.json()["access_token"]
        me = httpx.get(
            "https://api.twitter.com/2/users/me",
            headers={"Authorization": f"Bearer {access}"},
            timeout=20,
        )
        me.raise_for_status()
        data = me.json()["data"]
        handle = data["username"]
        _upsert_profile(handle, "x", data["id"])
        token = _issue_link_token(handle, "x", data["id"])
        return _link_token_page(handle, token, "x")
    except Exception as e:
        return HTMLResponse(f"<h2>X sign-in failed.</h2><p>{e}</p>", status_code=500)


@router.get("/google/start")
def google_start():
    if not GOOGLE_CLIENT_ID:
        return JSONResponse({"error": "Google sign-in is not configured yet"}, status_code=503)
    state = secrets.token_urlsafe(24)
    db.execute(
        "INSERT INTO oauth_states (state, verifier, provider, created_at) VALUES (?,?,?,?)",
        (state, "", "google", _now()),
    )
    db.commit()
    params = {
        "response_type": "code",
        "client_id": GOOGLE_CLIENT_ID,
        "redirect_uri": f"{BASE}/auth/google/callback",
        "scope": "openid email profile",
        "state": state,
        "access_type": "online",
    }
    return RedirectResponse(
        "https://accounts.google.com/o/oauth2/v2/auth?" + urllib.parse.urlencode(params)
    )


@router.get("/google/callback")
def google_callback(code: str = "", state: str = "", error: str = ""):
    if error:
        return HTMLResponse(f"<h2>Google sign-in failed: {error}</h2>", status_code=400)
    row = db.execute(
        "SELECT state FROM oauth_states WHERE state=? AND provider='google'", (state,)
    ).fetchone()
    if not row:
        return HTMLResponse("<h2>Invalid or expired login session. Try again.</h2>", status_code=400)
    db.execute("DELETE FROM oauth_states WHERE state=?", (state,))
    db.commit()
    try:
        r = httpx.post(
            "https://oauth2.googleapis.com/token",
            data={
                "grant_type": "authorization_code",
                "code": code,
                "client_id": GOOGLE_CLIENT_ID,
                "client_secret": GOOGLE_CLIENT_SECRET,
                "redirect_uri": f"{BASE}/auth/google/callback",
            },
            timeout=20,
        )
        r.raise_for_status()
        access = r.json()["access_token"]
        me = httpx.get(
            "https://www.googleapis.com/oauth2/v3/userinfo",
            headers={"Authorization": f"Bearer {access}"},
            timeout=20,
        )
        me.raise_for_status()
        data = me.json()
        handle = (data.get("email") or f"user{data['sub'][:6]}").split("@")[0].lower()
        handle = "".join(c for c in handle if c.isalnum() or c in "_-")[:24] or f"g{data['sub'][:8]}"
        _upsert_profile(handle, "google", data["sub"])
        token = _issue_link_token(handle, "google", data["sub"])
        return _link_token_page(handle, token, "google")
    except Exception as e:
        return HTMLResponse(f"<h2>Google sign-in failed.</h2><p>{e}</p>", status_code=500)


@router.post("/token/verify")
async def token_verify(req: Request):
    body = await req.json()
    token = (body.get("token") or "").strip()
    row = db.execute(
        "SELECT handle, provider FROM link_tokens WHERE token=?", (token,)
    ).fetchone()
    if not row:
        return JSONResponse({"error": "Unknown or expired code"}, status_code=404)
    return {"handle": row[0], "provider": row[1], "token": token}
