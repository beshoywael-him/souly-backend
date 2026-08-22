"""
Sign-in — the Netflix-style picker and the picture password.

Three screens' worth of API:

    GET  /api/auth/profiles          who's on this tablet
    POST /api/auth/set-password      first time only: choose your pictures
    POST /api/auth/login             tap your pictures
    POST /api/auth/logout

-----------------------------------------------------------------------------
THREAT MODEL — read this before reusing any of it
-----------------------------------------------------------------------------
A picture password is three pictures chosen in order from a grid of twelve:
12 x 11 x 10 = 1,320 combinations. A script cracks that instantly.

That is fine, because the attacker here is a nine-year-old at the next desk
who wants to open their friend's profile. 1,320 is plenty against a child
guessing, and five wrong tries locks the tile for a few minutes.

It is NOT fine for the parent portal, which holds a child's assessment data.
That keeps its own PBKDF2 access code — see app/security.py. Do not reuse this.

Only the hash is stored, never the sequence. The database travels on a laptop
to a competition; it should not carry every child's login in plaintext.
-----------------------------------------------------------------------------
"""

import secrets
import sqlite3
from datetime import datetime, timedelta, timezone

from fastapi import APIRouter, Depends, Header, HTTPException
from pydantic import BaseModel, Field

from app.db import db_dependency
from app.models import utc_now_iso
from app.security import hash_secret, verify_secret

router = APIRouter(prefix="/api/auth", tags=["auth"])

# The picture grid. Concrete, nameable objects — a child needs to be able to
# say "cat, rocket, apple" to themselves and remember it. Nothing abstract,
# nothing that looks like anything else at a glance.
PICTURE_SET = [
    {"code": "cat",     "emoji": "🐱", "label": "Cat"},
    {"code": "dog",     "emoji": "🐶", "label": "Dog"},
    {"code": "rocket",  "emoji": "🚀", "label": "Rocket"},
    {"code": "apple",   "emoji": "🍎", "label": "Apple"},
    {"code": "star",    "emoji": "⭐", "label": "Star"},
    {"code": "tree",    "emoji": "🌳", "label": "Tree"},
    {"code": "ball",    "emoji": "⚽", "label": "Ball"},
    {"code": "car",     "emoji": "🚗", "label": "Car"},
    {"code": "cake",    "emoji": "🍰", "label": "Cake"},
    {"code": "fish",    "emoji": "🐠", "label": "Fish"},
    {"code": "moon",    "emoji": "🌙", "label": "Moon"},
    {"code": "flower",  "emoji": "🌸", "label": "Flower"},
]
VALID_CODES = {p["code"] for p in PICTURE_SET}

PASSWORD_LENGTH = 3
MAX_FAILED = 5
LOCKOUT_MINUTES = 3
TOKEN_DAYS = 30


class PictureSequence(BaseModel):
    student_ext_id: str = Field(..., min_length=1, max_length=64)
    pictures: list[str] = Field(..., min_length=PASSWORD_LENGTH,
                                max_length=PASSWORD_LENGTH)


class TokenOnly(BaseModel):
    token: str


def _normalise(pictures: list[str]) -> str:
    """Sequence -> a single string to hash. Order matters."""
    cleaned = [p.strip().lower() for p in pictures]
    unknown = [p for p in cleaned if p not in VALID_CODES]
    if unknown:
        raise HTTPException(status_code=422,
                            detail=f"Unknown pictures: {', '.join(unknown)}")
    return "|".join(cleaned)


def _issue_token(conn: sqlite3.Connection, student_id: int,
                 device: str | None = None) -> str:
    token = secrets.token_urlsafe(32)
    expires = (datetime.now(timezone.utc) + timedelta(days=TOKEN_DAYS)) \
        .strftime("%Y-%m-%dT%H:%M:%SZ")
    conn.execute(
        "INSERT INTO auth_tokens (token, student_id, created_at, expires_at, device_label) "
        "VALUES (?,?,?,?,?)",
        (token, student_id, utc_now_iso(), expires, device),
    )
    # Housekeeping: drop anything expired so the table doesn't grow forever.
    conn.execute("DELETE FROM auth_tokens WHERE expires_at < ?", (utc_now_iso(),))
    return token


def _is_locked(row: sqlite3.Row) -> int:
    """Seconds remaining on a lockout, or 0."""
    if not row["locked_until"]:
        return 0
    try:
        until = datetime.strptime(row["locked_until"], "%Y-%m-%dT%H:%M:%SZ") \
            .replace(tzinfo=timezone.utc)
    except ValueError:
        return 0
    remaining = (until - datetime.now(timezone.utc)).total_seconds()
    return max(0, int(remaining))


# =============================================================================
# Who's on this tablet
# =============================================================================

@router.get("/profiles", summary="The sign-in picker")
def list_profiles(conn: sqlite3.Connection = Depends(db_dependency)) -> dict:
    """
    Every active student, as tiles.

    Deliberately public and deliberately thin: name, avatar, colour, and
    whether they've set a password yet. No progress, no stars, no support
    profile — a child shouldn't be able to read anything about a classmate
    from the sign-in screen.
    """
    rows = conn.execute(
        "SELECT external_id, display_name, avatar, avatar_color, "
        "       picture_password_hash, onboarded_at, locked_until "
        "FROM students WHERE is_active = 1 ORDER BY id"
    ).fetchall()

    return {
        "profiles": [
            {
                "external_id": r["external_id"],
                "display_name": r["display_name"],
                "avatar": r["avatar"],
                "avatar_color": r["avatar_color"],
                "needs_password": r["picture_password_hash"] is None,
                "needs_onboarding": r["onboarded_at"] is None,
                "locked_seconds": _is_locked(r),
            }
            for r in rows
        ],
        "pictures": PICTURE_SET,
        "password_length": PASSWORD_LENGTH,
    }


# =============================================================================
# First time: choose your pictures
# =============================================================================

@router.post("/set-password", summary="Choose a picture password (first time only)")
def set_password(
    payload: PictureSequence,
    conn: sqlite3.Connection = Depends(db_dependency),
) -> dict:
    """
    Only works while the student has no password.

    Changing an existing one has to go through a teacher — otherwise the first
    child to reach the tablet could lock a classmate out of their own profile,
    which is exactly the mischief the password exists to prevent.
    """
    student = conn.execute(
        "SELECT id, display_name, picture_password_hash, onboarded_at "
        "FROM students WHERE external_id = ? AND is_active = 1",
        (payload.student_ext_id,),
    ).fetchone()
    if student is None:
        raise HTTPException(status_code=404, detail="No such student")

    if student["picture_password_hash"] is not None:
        raise HTTPException(
            status_code=409,
            detail="This student already has a picture password. "
                   "Ask a teacher to reset it.",
        )

    sequence = _normalise(payload.pictures)

    # Three of the same picture is a sequence a classmate guesses first.
    if len(set(payload.pictures)) < PASSWORD_LENGTH:
        raise HTTPException(
            status_code=422,
            detail="Pick three different pictures.",
        )

    conn.execute(
        "UPDATE students SET picture_password_hash = ?, password_set_at = ?, "
        "failed_logins = 0, locked_until = NULL WHERE id = ?",
        (hash_secret(sequence), utc_now_iso(), student["id"]),
    )

    token = _issue_token(conn, student["id"])
    conn.execute("UPDATE students SET last_login_at = ? WHERE id = ?",
                 (utc_now_iso(), student["id"]))

    return {
        "token": token,
        "student_ext_id": payload.student_ext_id,
        "display_name": student["display_name"],
        "needs_onboarding": student["onboarded_at"] is None,
    }


# =============================================================================
# Sign in
# =============================================================================

@router.post("/login", summary="Sign in with your pictures")
def login(
    payload: PictureSequence,
    conn: sqlite3.Connection = Depends(db_dependency),
) -> dict:
    student = conn.execute(
        "SELECT * FROM students WHERE external_id = ? AND is_active = 1",
        (payload.student_ext_id,),
    ).fetchone()
    if student is None:
        raise HTTPException(status_code=404, detail="No such student")

    locked = _is_locked(student)
    if locked:
        raise HTTPException(
            status_code=429,
            detail=f"Too many tries. Try again in {locked} seconds.",
        )

    if student["picture_password_hash"] is None:
        raise HTTPException(
            status_code=409,
            detail="This student hasn't chosen their pictures yet.",
        )

    sequence = _normalise(payload.pictures)

    if not verify_secret(sequence, student["picture_password_hash"]):
        failed = student["failed_logins"] + 1
        lock_until = None
        if failed >= MAX_FAILED:
            lock_until = (datetime.now(timezone.utc)
                          + timedelta(minutes=LOCKOUT_MINUTES)) \
                .strftime("%Y-%m-%dT%H:%M:%SZ")
            failed = 0

        conn.execute(
            "UPDATE students SET failed_logins = ?, locked_until = ? WHERE id = ?",
            (failed, lock_until, student["id"]),
        )
        # Commit before raising. db_dependency rolls back on exception, and an
        # HTTPException is an exception — without this the counter resets on
        # every wrong try and the lockout can never fire.
        conn.commit()

        if lock_until:
            raise HTTPException(
                status_code=429,
                detail=f"Too many tries. Try again in {LOCKOUT_MINUTES} minutes.",
            )
        # Deliberately vague and gentle — no "wrong on the second picture".
        raise HTTPException(
            status_code=401,
            detail="That's not quite right. Have another go.",
        )

    conn.execute(
        "UPDATE students SET failed_logins = 0, locked_until = NULL, "
        "last_login_at = ? WHERE id = ?",
        (utc_now_iso(), student["id"]),
    )
    token = _issue_token(conn, student["id"])

    return {
        "token": token,
        "student_ext_id": student["external_id"],
        "display_name": student["display_name"],
        "avatar": student["avatar"],
        "needs_onboarding": student["onboarded_at"] is None,
    }


@router.post("/logout", summary="Sign out")
def logout(
    payload: TokenOnly,
    conn: sqlite3.Connection = Depends(db_dependency),
) -> dict:
    conn.execute("DELETE FROM auth_tokens WHERE token = ?", (payload.token,))
    return {"signed_out": True}


@router.get("/me", summary="Who is this token?")
def whoami(
    x_souly_token: str | None = Header(None),
    conn: sqlite3.Connection = Depends(db_dependency),
) -> dict:
    """Lets the app resume a session on reload without asking again."""
    if not x_souly_token:
        raise HTTPException(status_code=401, detail="No token")

    row = conn.execute(
        """
        SELECT s.external_id, s.display_name, s.avatar, s.avatar_color,
               s.onboarded_at, t.expires_at
        FROM auth_tokens t JOIN students s ON s.id = t.student_id
        WHERE t.token = ? AND t.expires_at > ?
        """,
        (x_souly_token, utc_now_iso()),
    ).fetchone()
    if row is None:
        raise HTTPException(status_code=401, detail="Token expired or unknown")

    return {
        "student_ext_id": row["external_id"],
        "display_name": row["display_name"],
        "avatar": row["avatar"],
        "avatar_color": row["avatar_color"],
        "needs_onboarding": row["onboarded_at"] is None,
    }


# =============================================================================
# Teacher reset
# =============================================================================

@router.post("/reset-password/{student_ext_id}",
             summary="Teacher: clear a picture password")
def reset_password(
    student_ext_id: str,
    conn: sqlite3.Connection = Depends(db_dependency),
) -> dict:
    """
    Clears the password so the child can choose again on next sign-in.

    Unauthenticated for now, because there is no teacher login yet. That is a
    real gap and it is listed in the README — when the teacher dashboard lands
    in Phase 3, this endpoint must move behind it.
    """
    student = conn.execute(
        "SELECT id, display_name FROM students WHERE external_id = ?",
        (student_ext_id,),
    ).fetchone()
    if student is None:
        raise HTTPException(status_code=404, detail="No such student")

    conn.execute(
        "UPDATE students SET picture_password_hash = NULL, password_set_at = NULL, "
        "failed_logins = 0, locked_until = NULL WHERE id = ?",
        (student["id"],),
    )
    conn.execute("DELETE FROM auth_tokens WHERE student_id = ?", (student["id"],))

    return {"reset": True, "display_name": student["display_name"]}
