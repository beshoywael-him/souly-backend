"""
Teacher sign-in, and the dependency every teacher endpoint hangs off.

    POST /api/teacher/auth/login     email + password in, token out
    GET  /api/teacher/auth/me        resume a session on reload
    POST /api/teacher/auth/logout

-----------------------------------------------------------------------------
THREAT MODEL — the third realm, and the widest one
-----------------------------------------------------------------------------
There are now three ways to sign in to Souly, and they are deliberately
different sizes because they protect different things.

    student   three pictures out of twelve, 1,320 combinations.
              Enough against the nine-year-old at the next desk, which is the
              only attacker a child's own profile actually has.

    parent    a long random access code, PBKDF2, no account list published.
              It opens one child's assessment data.

    teacher   email and password, PBKDF2, per-account lockout.
              It opens EVERY child in the class at once — their flags, their
              support profiles, the lot. It is the widest door in the system
              and it gets the strongest lock.

The realistic attacker here is not remote. It is an unattended classroom
laptop with the dashboard left open, which is why tokens expire in a day
rather than the thirty a parent gets, and why five wrong passwords lock the
account for fifteen minutes.

Teachers get their own token table (schema_v8), for the same reason parents
did: a student token cannot satisfy a teacher check however the query is
written, because the lookup physically cannot return a teacher.
-----------------------------------------------------------------------------
"""

import secrets
import sqlite3
from datetime import datetime, timedelta, timezone

from fastapi import APIRouter, Depends, Header, HTTPException
from pydantic import BaseModel, Field

from app.db import db_dependency
from app.models import utc_now_iso
from app.security import verify_secret

router = APIRouter(prefix="/api/teacher/auth", tags=["teacher-auth"])

# A school day plus the walk home. Long enough that a teacher is not signing
# in between lessons; short enough that a forgotten laptop stops working
# overnight.
TOKEN_HOURS = 24

MAX_FAILED = 5
LOCKOUT_MINUTES = 15


class LoginRequest(BaseModel):
    email: str = Field(..., min_length=3, max_length=200)
    password: str = Field(..., min_length=1, max_length=200)
    device_label: str | None = Field(None, max_length=120)


class TokenOnly(BaseModel):
    token: str


def _initials(name: str) -> str:
    parts = [p for p in (name or "").split() if p]
    return "".join(p[0].upper() for p in parts[:2]) or "?"


def _locked_seconds(row: sqlite3.Row) -> int:
    """Seconds remaining on a lockout, or 0."""
    locked_until = row["locked_until"]
    if not locked_until:
        return 0
    try:
        until = datetime.fromisoformat(locked_until.replace("Z", "+00:00"))
    except ValueError:
        return 0
    remaining = (until - datetime.now(timezone.utc)).total_seconds()
    return max(0, int(remaining))


def _public(row: sqlite3.Row) -> dict:
    return {
        "id": row["id"],
        "full_name": row["full_name"],
        "email": row["email"],
        "title": row["title"],
        "initials": row["initials"] or _initials(row["full_name"]),
        "avatar_color": row["avatar_color"],
        "is_homeroom": bool(row["is_homeroom"]),
    }


def get_current_teacher(
    x_souly_teacher: str | None = Header(None, alias="X-Souly-Teacher"),
    conn: sqlite3.Connection = Depends(db_dependency),
) -> sqlite3.Row:
    """
    Resolve the header into a teacher, or 401.

    The header name is its own — not X-Souly-Token, not X-Souly-Parent. Three
    realms, three headers, three tables: a token from one can never be
    presented to another by accident.
    """
    if not x_souly_teacher:
        raise HTTPException(status_code=401, detail="Sign in to open the dashboard.")

    row = conn.execute(
        """
        SELECT t.*, k.expires_at
        FROM teacher_tokens k
        JOIN teachers t ON t.id = k.teacher_id
        WHERE k.token = ?
        """,
        (x_souly_teacher,),
    ).fetchone()

    if row is None:
        raise HTTPException(status_code=401, detail="Session expired. Sign in again.")

    if row["expires_at"] <= utc_now_iso():
        conn.execute("DELETE FROM teacher_tokens WHERE token = ?", (x_souly_teacher,))
        raise HTTPException(status_code=401, detail="Session expired. Sign in again.")

    if not row["is_active"]:
        raise HTTPException(status_code=403, detail="This account is not active.")

    return row


# =============================================================================
# Routes
# =============================================================================

@router.post("/login", summary="Sign in to the teacher dashboard")
def login(
    payload: LoginRequest,
    conn: sqlite3.Connection = Depends(db_dependency),
) -> dict:
    email = payload.email.strip().lower()

    row = conn.execute(
        "SELECT * FROM teachers WHERE LOWER(email) = ?", (email,)
    ).fetchone()

    # Unlike the parent portal there IS an account list here (a school knows
    # its own staff), so we can attribute a failure and lock the account.
    if row is not None:
        locked = _locked_seconds(row)
        if locked:
            raise HTTPException(
                status_code=429,
                detail=f"Too many tries. Try again in {locked // 60 + 1} minutes.",
            )

    if row is None or not verify_secret(payload.password, row["password_hash"]):
        if row is not None:
            failed = (row["failed_logins"] or 0) + 1
            if failed >= MAX_FAILED:
                until = (datetime.now(timezone.utc)
                         + timedelta(minutes=LOCKOUT_MINUTES)) \
                    .strftime("%Y-%m-%dT%H:%M:%SZ")
                conn.execute(
                    "UPDATE teachers SET failed_logins = 0, locked_until = ? "
                    "WHERE id = ?", (until, row["id"]),
                )
            else:
                conn.execute(
                    "UPDATE teachers SET failed_logins = ? WHERE id = ?",
                    (failed, row["id"]),
                )
        # Same message either way: a different one for "no such email" tells
        # an attacker which addresses are real staff accounts.
        raise HTTPException(status_code=401, detail="Email or password is wrong.")

    if not row["is_active"]:
        raise HTTPException(status_code=403, detail="This account is not active.")

    token = secrets.token_urlsafe(32)
    expires = (datetime.now(timezone.utc) + timedelta(hours=TOKEN_HOURS)) \
        .strftime("%Y-%m-%dT%H:%M:%SZ")

    conn.execute(
        "INSERT INTO teacher_tokens (token, teacher_id, created_at, expires_at, "
        "device_label) VALUES (?,?,?,?,?)",
        (token, row["id"], utc_now_iso(), expires, payload.device_label),
    )
    conn.execute("DELETE FROM teacher_tokens WHERE expires_at < ?", (utc_now_iso(),))
    conn.execute(
        "UPDATE teachers SET last_login_at = ?, failed_logins = 0, "
        "locked_until = NULL WHERE id = ?",
        (utc_now_iso(), row["id"]),
    )

    return {"token": token, "teacher": _public(row), "expires_at": expires}


@router.get("/me", summary="Who is this token?")
def whoami(teacher: sqlite3.Row = Depends(get_current_teacher)) -> dict:
    """Lets the dashboard resume on reload without asking again."""
    return {"teacher": _public(teacher)}


@router.post("/logout", summary="Sign out")
def logout(
    payload: TokenOnly,
    conn: sqlite3.Connection = Depends(db_dependency),
) -> dict:
    conn.execute("DELETE FROM teacher_tokens WHERE token = ?", (payload.token,))
    return {"signed_out": True}
