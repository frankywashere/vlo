"""Local swapMEin accounts and private, durable generation storage.

State lives under RUNTIME_ROOT, never in the public projects/static tree.
Hosted installations must provide a persistent data volume for this service.
"""
import hashlib
import hmac
import re
import secrets
import sqlite3
import time
import uuid
from pathlib import Path
from urllib.parse import urlsplit

from fastapi import APIRouter, Depends, HTTPException, Request, Response, UploadFile
from fastapi.responses import FileResponse
from fastapi.exceptions import RequestValidationError
from fastapi.routing import APIRoute
from pydantic import BaseModel, ConfigDict, Field

from config import RUNTIME_ROOT

class PrivateValidationRoute(APIRoute):
    def get_route_handler(self):
        handler = super().get_route_handler()
        async def sanitized(request: Request):
            try:
                return await handler(request)
            except RequestValidationError:
                # Validation responses must never echo submitted passwords.
                detail = "Check your email and use a password between 10 and 128 characters." if request.url.path.endswith(("/login", "/register")) else "Check the submitted generation details."
                raise HTTPException(422, detail) from None
        return sanitized


router = APIRouter(prefix="/app/account", tags=["swapmein-accounts"], route_class=PrivateValidationRoute)
ACCOUNT_ROOT = RUNTIME_ROOT / "swapmein"
COOKIE = "swapmein_session"
SESSION_SECONDS = 7 * 24 * 3600
MAX_VIDEO_BYTES = 300 * 1024 * 1024


def database():
    ACCOUNT_ROOT.mkdir(parents=True, exist_ok=True)
    # FastAPI may run a dependency, its route, and cleanup on different workers.
    # Each request still owns its connection; it is never shared across requests.
    connection = sqlite3.connect(ACCOUNT_ROOT / "accounts.sqlite3", timeout=15, check_same_thread=False)
    connection.row_factory = sqlite3.Row
    connection.execute("PRAGMA foreign_keys=ON")
    connection.executescript("""
        CREATE TABLE IF NOT EXISTS users (
            id TEXT PRIMARY KEY, email TEXT UNIQUE NOT NULL, name TEXT NOT NULL,
            salt TEXT NOT NULL, password_hash TEXT NOT NULL);
        CREATE TABLE IF NOT EXISTS sessions (
            token_hash TEXT PRIMARY KEY, user_id TEXT NOT NULL REFERENCES users(id), expires REAL NOT NULL);
        CREATE TABLE IF NOT EXISTS attempts (identity TEXT PRIMARY KEY, count INTEGER NOT NULL, reset REAL NOT NULL);
        CREATE TABLE IF NOT EXISTS credit_balances (
            user_id TEXT PRIMARY KEY REFERENCES users(id),
            balance INTEGER NOT NULL DEFAULT 0 CHECK(balance >= 0));
        CREATE TABLE IF NOT EXISTS generations (
            id TEXT NOT NULL, user_id TEXT NOT NULL REFERENCES users(id), title TEXT NOT NULL,
            provider TEXT NOT NULL, status TEXT NOT NULL, duration REAL NOT NULL,
            created REAL NOT NULL, filename TEXT, PRIMARY KEY (id, user_id));
    """)
    try:
        yield connection
    finally:
        connection.close()


def hash_token(value):
    return hashlib.sha256(value.encode()).hexdigest()


def password_hash(password, salt):
    return hashlib.scrypt(password.encode(), salt=bytes.fromhex(salt), n=16384, r=8, p=1).hex()


def same_origin(request: Request):
    # SameSite cookies plus an exact Origin check protect all state changes,
    # including login CSRF. Requests without Origin must carry our app header.
    origin = request.headers.get("origin")
    if origin:
        parsed = urlsplit(origin)
        if parsed.scheme != request.url.scheme or parsed.netloc != request.headers.get("host"):
            raise HTTPException(403, "This request must come from the app.")
    elif request.headers.get("x-swapmein-client") != "web":
        raise HTTPException(403, "This request must come from the app.")


def user(request: Request, db=Depends(database)):
    token = request.cookies.get(COOKIE, "")
    if not re.fullmatch(r"[A-Za-z0-9_-]{40,100}", token):
        raise HTTPException(401, "Sign in to continue.")
    row = db.execute("SELECT users.* FROM users JOIN sessions ON users.id=sessions.user_id "
                     "WHERE token_hash=? AND expires>?", (hash_token(token), time.time())).fetchone()
    if row is None:
        raise HTTPException(401, "Your session has expired. Sign in again.")
    return dict(row)


def public_user(row, db):
    credit = db.execute("SELECT balance FROM credit_balances WHERE user_id=?", (row["id"],)).fetchone()
    return {"id": row["id"], "email": row["email"], "name": row["name"],
            "credits": credit["balance"] if credit else 0}


class Credentials(BaseModel):
    model_config = ConfigDict(extra="forbid")
    email: str = Field(min_length=3, max_length=254)
    password: str = Field(min_length=10, max_length=128)
    name: str = Field(default="", max_length=80)


def throttle(request, db, email):
    identities = [(hash_token("email:" + email), 12),
                  (hash_token("ip:" + (request.client.host if request.client else "local")), 60)]
    now = time.time()
    # Increment atomically so concurrent password guesses share one limit.
    with db:
        db.execute("DELETE FROM attempts WHERE reset<?", (now,))
        blocked = False
        for identity, limit in identities:
            db.execute("INSERT INTO attempts VALUES (?,1,?) ON CONFLICT(identity) DO UPDATE SET count=count+1",
                       (identity, now + 900))
            count = db.execute("SELECT count FROM attempts WHERE identity=?", (identity,)).fetchone()[0]
            blocked = blocked or count > limit
    if blocked:
        raise HTTPException(429, "Too many sign-in attempts. Try again in 15 minutes.", headers={"Retry-After": "900"})


def issue_session(request, response, db, row):
    token = secrets.token_urlsafe(32)
    with db:
        db.execute("DELETE FROM sessions WHERE expires<?", (time.time(),))
        # Rotate this browser's existing session on login/registration.
        db.execute("DELETE FROM sessions WHERE token_hash=?", (hash_token(request.cookies.get(COOKIE, "")),))
        db.execute("INSERT INTO sessions VALUES (?,?,?)", (hash_token(token), row["id"], time.time() + SESSION_SECONDS))
    response.set_cookie(COOKIE, token, max_age=SESSION_SECONDS, httponly=True,
                        secure=request.url.scheme == "https", samesite="strict", path="/app/account")
    response.headers["Cache-Control"] = "no-store"
    return public_user(row, db)


@router.post("/register", dependencies=[Depends(same_origin)])
def register(credentials: Credentials, request: Request, response: Response, db=Depends(database)):
    email = credentials.email.strip().lower()
    if not re.fullmatch(r"[^\s@]+@[^\s@]+\.[^\s@]+", email):
        raise HTTPException(422, "Enter a valid email address.")
    throttle(request, db, email)
    salt = secrets.token_hex(16)
    row = {"id": uuid.uuid4().hex, "email": email, "name": credentials.name.strip() or email.split("@")[0]}
    try:
        with db:
            db.execute("INSERT INTO users VALUES (?,?,?,?,?)",
                       (row["id"], email, row["name"], salt, password_hash(credentials.password, salt)))
    except sqlite3.IntegrityError:
        raise HTTPException(409, "Unable to create this account. Try signing in instead.") from None
    return issue_session(request, response, db, row)


@router.post("/login", dependencies=[Depends(same_origin)])
def login(credentials: Credentials, request: Request, response: Response, db=Depends(database)):
    email = credentials.email.strip().lower()
    throttle(request, db, email)
    row = db.execute("SELECT * FROM users WHERE email=?", (email,)).fetchone()
    # Always run scrypt, including for unknown email addresses.
    candidate = password_hash(credentials.password, row["salt"] if row else "00" * 16)
    if row is None or not hmac.compare_digest(candidate, row["password_hash"]):
        raise HTTPException(401, "Email or password is incorrect.")
    return issue_session(request, response, db, row)


@router.get("/me")
def me(response: Response, current=Depends(user), db=Depends(database)):
    response.headers["Cache-Control"] = "no-store"
    return public_user(current, db)


@router.post("/logout", dependencies=[Depends(same_origin)])
def logout(request: Request, response: Response, db=Depends(database)):
    with db:
        db.execute("DELETE FROM sessions WHERE token_hash=?", (hash_token(request.cookies.get(COOKIE, "")),))
    response.delete_cookie(COOKIE, path="/app/account")
    return {"ok": True}


class Generation(BaseModel):
    model_config = ConfigDict(extra="forbid")
    id: str = Field(pattern=r"^[A-Za-z0-9_-]{1,128}$")
    title: str = Field(min_length=1, max_length=200)
    provider: str = Field(pattern=r"^(reAPI|ToAPIs|fal|OpenRouter|Imported)$")
    status: str = Field(pattern=r"^(queued|preparing|ready|generating|finalizing|completed|failed|submission_unknown)$")
    duration: float = Field(default=0, ge=0, le=3600, allow_inf_nan=False)


def generation_view(row):
    return {"id": row["id"], "title": row["title"], "provider": row["provider"], "status": row["status"],
            "duration": row["duration"], "createdAt": row["created"], "saved": bool(row["filename"]),
            "videoUrl": f"/app/account/generations/{row['id']}/video" if row["filename"] else None,
            "videoExtension": Path(row["filename"]).suffix.lstrip(".") if row["filename"] else "mp4"}


@router.get("/generations")
def library(response: Response, current=Depends(user), db=Depends(database)):
    response.headers["Cache-Control"] = "no-store"
    return [generation_view(row) for row in db.execute(
        "SELECT * FROM generations WHERE user_id=? ORDER BY created DESC", (current["id"],))]


@router.put("/generations/{generation_id}", dependencies=[Depends(same_origin)])
def record(generation_id: str, data: Generation, current=Depends(user), db=Depends(database)):
    if generation_id != data.id:
        raise HTTPException(422, "Invalid generation.")
    with db:
        db.execute("""INSERT INTO generations VALUES (?,?,?,?,?,?,?,NULL)
            ON CONFLICT(id,user_id) DO UPDATE SET title=excluded.title, provider=excluded.provider,
            status=excluded.status, duration=excluded.duration""",
                   (data.id, current["id"], data.title, data.provider, data.status, data.duration, time.time()))
    return generation_view(db.execute("SELECT * FROM generations WHERE id=? AND user_id=?", (data.id, current["id"])).fetchone())


@router.post("/generations/{generation_id}/video", dependencies=[Depends(same_origin)])
def save_video(generation_id: str, video: UploadFile, current=Depends(user), db=Depends(database)):
    row = db.execute("SELECT * FROM generations WHERE id=? AND user_id=?", (generation_id, current["id"])).fetchone()
    if row is None:
        raise HTTPException(404, "Generation not found.")
    folder = ACCOUNT_ROOT / "videos" / current["id"]
    folder.mkdir(parents=True, exist_ok=True)
    filename = uuid.uuid4().hex + ".mp4"
    target = folder / filename
    total = 0
    try:
        with target.open("xb") as output:
            while chunk := video.file.read(1024 * 1024):
                total += len(chunk)
                if total > MAX_VIDEO_BYTES:
                    raise HTTPException(413, "Videos must be smaller than 300 MiB.")
                output.write(chunk)
        import av
        try:
            with av.open(str(target), options={"protocol_whitelist": "file", "format_whitelist": "mov,matroska,webm"}) as container:
                if not container.streams.video or container.format.name not in {"mov,mp4,m4a,3gp,3g2,mj2", "matroska,webm"}:
                    raise ValueError
                duration = (container.duration or 0) / av.time_base
                webm = container.format.name == "matroska,webm"
        except Exception:
            raise HTTPException(422, "Upload a valid MP4, MOV or WebM video.") from None
        if webm:
            filename = Path(filename).with_suffix(".webm").name
            renamed = folder / filename
            target.rename(renamed)
            target = renamed
        with db:
            db.execute("UPDATE generations SET filename=?,status='completed',duration=? WHERE id=? AND user_id=?",
                       (filename, duration or row["duration"], generation_id, current["id"]))
    except Exception:
        target.unlink(missing_ok=True)
        raise
    finally:
        video.file.close()
    if row["filename"]:
        (folder / row["filename"]).unlink(missing_ok=True)
    return {"saved": True}


@router.get("/generations/{generation_id}/video")
def play_video(generation_id: str, current=Depends(user), db=Depends(database)):
    row = db.execute("SELECT * FROM generations WHERE id=? AND user_id=?", (generation_id, current["id"])).fetchone()
    if row is None or not row["filename"]:
        raise HTTPException(404, "Saved video not found.")
    target = ACCOUNT_ROOT / "videos" / current["id"] / row["filename"]
    if not target.is_file():
        raise HTTPException(404, "Saved video not found.")
    # Container detection avoids labelling imported WebM files as MP4.
    import av
    with av.open(str(target), options={"protocol_whitelist": "file", "format_whitelist": "mov,matroska,webm"}) as container:
        media_type = "video/webm" if container.format.name == "matroska,webm" else "video/mp4"
    return FileResponse(target, media_type=media_type, headers={"Cache-Control": "private, no-store"})


@router.delete("/generations/{generation_id}", dependencies=[Depends(same_origin)])
def delete_generation(generation_id: str, current=Depends(user), db=Depends(database)):
    row = db.execute("SELECT * FROM generations WHERE id=? AND user_id=?", (generation_id, current["id"])).fetchone()
    if row is None:
        raise HTTPException(404, "Generation not found.")
    with db:
        db.execute("DELETE FROM generations WHERE id=? AND user_id=?", (generation_id, current["id"]))
    if row["filename"]:
        (ACCOUNT_ROOT / "videos" / current["id"] / row["filename"]).unlink(missing_ok=True)
    return {"ok": True}
