"""Accounts, sessions and access control.

- Passwords: scrypt (stdlib) with a per-user salt, compared in constant time.
- Sessions: random token in an HttpOnly cookie; only its SHA-256 is stored server-side.
- CSRF: state-changing /api calls must send `X-TerraTrace: 1` (a cross-site form can't), on
  top of SameSite=Lax cookies.
- Login throttling: MAX_FAILURES per email+IP within LOCK_S locks that pair out.
- Admins are created only from the command line (tools/create_admin.py); registration always
  makes a contributor ('user').
"""
import base64
import hashlib
import hmac
import re
import secrets
import time
from datetime import datetime, timedelta, timezone

from fastapi import APIRouter, HTTPException, Request, Response
from pydantic import BaseModel, Field

from . import db

router = APIRouter()
COOKIE = "tt_session"
SESSION_DAYS = 7
MAX_FAILURES, LOCK_S = 5, 15 * 60
MIN_PASSWORD = {"user": 8, "admin": 12}
CSRF_HEADER = "x-terratrace"
_EMAIL = re.compile(r"^[^@\s]{1,64}@[^@\s]{1,255}\.[^@\s]{2,}$")
_SCRYPT = {"n": 2 ** 14, "r": 8, "p": 1}


# ---------------------------------------------------------------------- passwords
def hash_password(pw: str) -> str:
    salt = secrets.token_bytes(16)
    dk = hashlib.scrypt(pw.encode(), salt=salt, dklen=32, **_SCRYPT)
    return "scrypt$16384$8$1$" + base64.b64encode(salt).decode() + "$" + base64.b64encode(dk).decode()


def verify_password(pw: str, stored: str) -> bool:
    try:
        _, n, r, p, salt, dk = stored.split("$")
        got = hashlib.scrypt(pw.encode(), salt=base64.b64decode(salt), dklen=32, n=int(n), r=int(r), p=int(p))
        return hmac.compare_digest(got, base64.b64decode(dk))
    except (ValueError, TypeError):
        return False


_DUMMY = hash_password(secrets.token_hex(8))     # equalise timing for unknown emails


def check_password_rules(pw: str, role: str = "user"):
    if len(pw) < MIN_PASSWORD[role]:
        raise HTTPException(400, f"Password must be at least {MIN_PASSWORD[role]} characters")
    if pw.lower() == pw or pw.upper() == pw or not re.search(r"\d", pw):
        if role == "admin" or len(pw) < 12:
            raise HTTPException(400, "Use upper- and lower-case letters and a number (or 12+ characters)")


def create_user(email: str, name: str, password: str, role: str = "user") -> int:
    email = email.strip().lower()
    if not _EMAIL.match(email):
        raise HTTPException(400, "Enter a valid email address")
    name = name.strip()[:60]
    if not name:
        raise HTTPException(400, "Enter your name")
    check_password_rules(password, role)
    with db.tx() as c:
        if c.execute("SELECT 1 FROM users WHERE email = ?", (email,)).fetchone():
            raise HTTPException(409, "An account with this email already exists")
        cur = c.execute("INSERT INTO users (email, name, password_hash, role, created_at) VALUES (?, ?, ?, ?, ?)",
                        (email, name, hash_password(password), role, db.now()))
        return cur.lastrowid


# ---------------------------------------------------------------------- sessions
def _hash_token(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()


def _secure(request: Request) -> bool:
    return request.url.scheme in ("https", "wss")


def start_session(request: Request, response: Response, user_id: int):
    token = secrets.token_urlsafe(32)
    exp = datetime.now(timezone.utc) + timedelta(days=SESSION_DAYS)
    with db.tx() as c:
        c.execute("DELETE FROM sessions WHERE expires_at < ?", (db.now(),))
        c.execute("INSERT INTO sessions (token_hash, user_id, created_at, expires_at, ip, user_agent) VALUES (?, ?, ?, ?, ?, ?)",
                  (_hash_token(token), user_id, db.now(), exp.isoformat(timespec="seconds"),
                   request.client.host if request.client else None, (request.headers.get("user-agent") or "")[:200]))
    response.set_cookie(COOKIE, token, max_age=SESSION_DAYS * 86400, httponly=True, samesite="lax",
                        secure=_secure(request), path="/")


def user_from_token(token: str | None) -> dict | None:
    if not token:
        return None
    row = db.one("SELECT u.id, u.email, u.name, u.role, u.disabled, u.created_at FROM sessions s "
                 "JOIN users u ON u.id = s.user_id WHERE s.token_hash = ? AND s.expires_at > ?",
                 (_hash_token(token), db.now()))
    if row is None or row["disabled"]:
        return None
    return {k: row[k] for k in ("id", "email", "name", "role", "created_at")}


def current_user(request: Request) -> dict | None:
    """Cached per request by the middleware."""
    if not hasattr(request.state, "user"):
        request.state.user = user_from_token(request.cookies.get(COOKIE))
    return request.state.user


def require_user(request: Request) -> dict:
    u = current_user(request)
    if u is None:
        raise HTTPException(401, "Please log in")
    return u


def require_admin(request: Request) -> dict:
    u = require_user(request)
    if u["role"] != "admin":
        raise HTTPException(403, "Admins only")
    return u


# ---------------------------------------------------------------------- throttling
def _throttle_key(request: Request, email: str) -> str:
    return f"{email.strip().lower()}|{request.client.host if request.client else '?'}"


def _locked(key: str) -> bool:
    with db.tx() as c:
        c.execute("DELETE FROM login_failures WHERE ts < ?", (time.time() - LOCK_S,))
        n = c.execute("SELECT COUNT(*) FROM login_failures WHERE key = ?", (key,)).fetchone()[0]
    return n >= MAX_FAILURES


# ---------------------------------------------------------------------- API
class Register(BaseModel):
    email: str = Field(max_length=320)
    name: str = Field(max_length=60)
    password: str = Field(max_length=200)


class Login(BaseModel):
    email: str = Field(max_length=320)
    password: str = Field(max_length=200)


class PasswordChange(BaseModel):
    current: str = Field(max_length=200)
    new: str = Field(max_length=200)


def _landing(user: dict) -> str:
    return "/" if user["role"] == "admin" else "/app/"


@router.post("/api/auth/register")
def register(body: Register, request: Request, response: Response):
    uid = create_user(body.email, body.name, body.password, "user")
    start_session(request, response, uid)
    return {"ok": True, "redirect": "/app/"}


@router.post("/api/auth/login")
def login(body: Login, request: Request, response: Response):
    key = _throttle_key(request, body.email)
    if _locked(key):
        raise HTTPException(429, "Too many failed attempts. Try again in 15 minutes.")
    row = db.one("SELECT * FROM users WHERE email = ?", (body.email.strip().lower(),))
    ok = verify_password(body.password, row["password_hash"] if row else _DUMMY) and row is not None
    if not ok or row["disabled"]:
        with db.tx() as c:
            c.execute("INSERT INTO login_failures (key, ts) VALUES (?, ?)", (key, time.time()))
        raise HTTPException(401, "Invalid email or password" if not (row and ok) else "This account is disabled")
    with db.tx() as c:
        c.execute("DELETE FROM login_failures WHERE key = ?", (key,))
    start_session(request, response, row["id"])
    return {"ok": True, "redirect": _landing(dict(row))}


@router.post("/api/auth/logout")
def logout(request: Request, response: Response):
    token = request.cookies.get(COOKIE)
    if token:
        with db.tx() as c:
            c.execute("DELETE FROM sessions WHERE token_hash = ?", (_hash_token(token),))
    response.delete_cookie(COOKIE, path="/")
    return {"ok": True, "redirect": "/login.html"}


@router.get("/api/auth/me")
def me(request: Request):
    return require_user(request)


@router.post("/api/auth/password")
def change_password(body: PasswordChange, request: Request):
    u = require_user(request)
    row = db.one("SELECT password_hash FROM users WHERE id = ?", (u["id"],))
    if not verify_password(body.current, row["password_hash"]):
        raise HTTPException(400, "Current password is wrong")
    check_password_rules(body.new, u["role"])
    with db.tx() as c:
        c.execute("UPDATE users SET password_hash = ? WHERE id = ?", (hash_password(body.new), u["id"]))
        # sign out every other device
        c.execute("DELETE FROM sessions WHERE user_id = ? AND token_hash != ?",
                  (u["id"], _hash_token(request.cookies.get(COOKIE, ""))))
    return {"ok": True}


# ---------------------------------------------------------------------- access rules
# live.html/live.js are static; a paired phone authenticates with the session key (server/live.py)
PUBLIC_PREFIXES = ("/login.html", "/register.html", "/api/auth/", "/css/", "/img/", "/favicon", "/fonts/",
                   "/live.html", "/live.js", "/api/pair/")
USER_PREFIXES = ("/app/", "/live.html", "/live.js", "/api/me/", "/api/map/", "/report.html", "/js/core.js", "/js/map.js")
OWNED_RUN = re.compile(r"^/(?:runs|api/runs)/([\w\-]+)(?:/|$)")


def access(request: Request) -> tuple[str, str | None]:
    """('ok'|'login'|'forbidden', run_id-to-check) for an HTTP path. Runs owned by a contributor
    are checked by the caller (needs the drives table)."""
    path = request.url.path
    if path.startswith(PUBLIC_PREFIXES):
        return "ok", None
    user = current_user(request)
    if user is None:
        return "login", None
    if user["role"] == "admin":
        return "ok", None
    if path.startswith(USER_PREFIXES) or path in ("/app", "/api/weather"):
        return "ok", None
    m = OWNED_RUN.match(path)
    if m:
        return "owner", m.group(1)
    return "forbidden", None
