"""
Parent sign-in, and the two dependencies every parent endpoint hangs off.

    POST /api/parent/auth/login     access code in, token out
    GET  /api/parent/auth/me        resume a session on reload
    POST /api/parent/auth/logout

-----------------------------------------------------------------------------
THREAT MODEL — this is not the student picker
-----------------------------------------------------------------------------
The student sign-in is three pictures out of twelve: 1,320 combinations,
which is plenty against a nine-year-old at the next desk and useless against
anything else. That is the right trade for a child who has to sign in ten
times a day.

This is not that. A parent account opens one child's assessment data, their
support profile, and what their teachers have written about them. It uses the
PBKDF2 access code that has been in schema.sql since day one — 31^12 for the
generated form — and only the hash is ever stored.

TWO DELIBERATE CHOICES, both about not leaking who exists:

1. THERE IS NO PARENT PICKER. The student app shows every face on the tablet
   because a class list is not a secret and a child cannot type. Showing every
   parent's name on an open portal publishes the school's family list to
   anyone who loads the page. So: code only, no list.

2. BECAUSE THERE IS NO PICKER, WE CANNOT ATTRIBUTE A FAILED ATTEMPT.
   A wrong code matches no account, so there is no row on which to increment
   failed_logins — the student lockout design does not transfer. The throttle
   here is per-client instead, held in memory. It resets when the server
   restarts, which is an acceptable weakness for a portal that lives on one
   laptop for one afternoon, and a real one to fix if this is ever hosted.

A wrong code returns 401 with the same message whether the code is malformed,
unknown, or belongs to a deactivated parent. Never confirm that a code
half-exists.
-----------------------------------------------------------------------------
"""

import secrets
import sqlite3
import time
from datetime import datetime, timedelta, timezone

from fastapi import APIRouter, Depends, Header, HTTPException, Path, Request
from pydantic import BaseModel, Field

from app.db import db_dependency
from app.models import utc_now_iso
from app.security import verify_secret

router = APIRouter(prefix="/api/parent/auth", tags=["parent-auth"])

TOKEN_DAYS = 30
MAX_ATTEMPTS = 6
THROTTLE_WINDOW_S = 300      # 5 minutes

# client-ip -> [timestamps of failed attempts]. See choice 2 above.
_recent_failures: dict[str, list[float]] = {}


class LoginRequest(BaseModel):
    access_code: str = Field(..., min_length=4, max_length=64)
    device_label: str | None = Field(None, max_length=64)


class TokenOnly(BaseModel):
    token: str


def _initials(full_name: str) -> str:
    """
    Monogram for the avatar circle.

    "Fayrouz" is one word, and one letter in a circle reads as a mistake
    rather than a monogram — so a single-word name uses its first two letters.
    Egyptian parents in this school are as likely to give one name as two.
    """
    parts = [w for w in full_name.split() if w]
    if not parts:
        return "?"
    if len(parts) == 1:
        return parts[0][:2].upper()
    return (parts[0][0] + parts[1][0]).upper()


def _throttle_check(client: str) -> None:
    cutoff = time.time() - THROTTLE_WINDOW_S
    hits = [t for t in _recent_failures.get(client, []) if t > cutoff]
    _recent_failures[client] = hits
    if len(hits) >= MAX_ATTEMPTS:
        wait = int(hits[0] + THROTTLE_WINDOW_S - time.time())
        raise HTTPException(
            status_code=429,
            detail=f"Too many attempts. Please wait {max(1, wait // 60)} minute(s).",
        )


def _record_failure(client: str) -> None:
    _recent_failures.setdefault(client, []).append(time.time())


# =============================================================================
# The dependencies. Every parent-facing route uses one of these two.
# =============================================================================

def get_current_parent(
    x_souly_parent: str | None = Header(None),
    conn: sqlite3.Connection = Depends(db_dependency),
) -> sqlite3.Row:
    """
    Resolve the parent token, or 401.

    The header is X-Souly-Parent, not X-Souly-Token, and the table is
    parent_tokens, not auth_tokens. Both differences are on purpose: a student
    token sent to a parent endpoint cannot accidentally satisfy this lookup,
    because the row it would need does not exist in this table at all.
    """
    if not x_souly_parent:
        raise HTTPException(status_code=401, detail="Not signed in")

    row = conn.execute(
        """
        SELECT p.*
        FROM parent_tokens t
        JOIN parents p ON p.id = t.parent_id
        WHERE t.token = ? AND t.expires_at > ? AND p.is_active = 1
        """,
        (x_souly_parent, utc_now_iso()),
    ).fetchone()
    if row is None:
        raise HTTPException(status_code=401, detail="Session expired. Please sign in again.")
    return row


def get_parent_child(
    student_ext_id: str = Path(..., description="Child external_id, e.g. 'stu-01'"),
    parent: sqlite3.Row = Depends(get_current_parent),
    conn: sqlite3.Connection = Depends(db_dependency),
) -> sqlite3.Row:
    """
    Resolve a child from the URL — but only if this parent is linked to them.

    THIS IS THE ACCESS CONTROL. Every endpoint that takes a child in the path
    depends on this rather than on get_student, so "a parent sees only their
    own child" is enforced in one place instead of being retyped, correctly,
    in fifteen. v_parent_children is the join; there is no way to reach a
    student row through this function without passing through parent_student.

    404, not 403. A 403 confirms that stu-04 exists and belongs to somebody
    else, which is exactly the fact we are trying not to publish.
    """
    row = conn.execute(
        "SELECT * FROM v_parent_children WHERE parent_id = ? AND external_id = ?",
        (parent["id"], student_ext_id),
    ).fetchone()
    if row is None:
        raise HTTPException(status_code=404, detail="No such child")
    return row


def list_children(conn: sqlite3.Connection, parent_id: int) -> list[dict]:
    """
    Every child this parent is linked to, newest activity first.

    Returned by /login and /me so the hub knows on its very first call whether
    it is drawing a switcher or not. Fayrouz has two sons; Rancy and Karma
    have one each. The frontend must not have to guess, and must not have to
    make a second request to find out.
    """
    rows = conn.execute(
        """
        SELECT c.*,
               (SELECT COUNT(*) FROM teacher_notes n
                 WHERE n.student_id = c.student_id AND n.read_at IS NULL)
                                                            AS unread_notes,
               (SELECT COUNT(*) FROM conversation_messages m
                  JOIN conversations cv ON cv.id = m.conversation_id
                 WHERE cv.student_id = c.student_id
                   AND cv.parent_id  = c.parent_id
                   AND m.sender_role = 'teacher'
                   AND m.read_at IS NULL)                   AS unread_messages
        FROM v_parent_children c
        WHERE c.parent_id = ?
        ORDER BY c.grade, c.display_name
        """,
        (parent_id,),
    ).fetchall()

    return [
        {
            "external_id": r["external_id"],
            "display_name": r["display_name"],
            "full_name": r["full_name"],
            "grade": r["grade"],
            "avatar": r["avatar"],
            "avatar_color": r["avatar_color"],
            "relationship": r["relationship"],
            "support_profile": r["support_profile"],
            "stars": r["stars"],
            "level": r["level"],
            "day_streak": r["day_streak"],
            "last_active_date": r["last_active_date"],
            "onboarded": r["onboarded_at"] is not None,
            "unread_notes": r["unread_notes"],
            "unread_messages": r["unread_messages"],
            "unread_total": r["unread_notes"] + r["unread_messages"],
        }
        for r in rows
    ]


# =============================================================================
# Routes
# =============================================================================

@router.post("/login", summary="Sign in with an access code")
def login(
    payload: LoginRequest,
    request: Request,
    conn: sqlite3.Connection = Depends(db_dependency),
) -> dict:
    client = request.client.host if request.client else "unknown"
    _throttle_check(client)

    code = payload.access_code.strip().upper()

    # No index can help here: the hash is salted per row, so finding the
    # matching parent means verifying against each one. That is O(parents)
    # PBKDF2 rounds per login — about 60ms each, for a school-sized table.
    # If this ever holds thousands of families, add a lookup column
    # (a plain SHA-256 of the code, unhashed-but-unsalted) to narrow the
    # candidate set first; do not weaken the stored hash to fix it.
    matched = None
    for row in conn.execute("SELECT * FROM parents WHERE is_active = 1"):
        if verify_secret(code, row["access_code_hash"]):
            matched = row
            break

    if matched is None:
        _record_failure(client)
        conn.commit()   # nothing to persist, but keep the pattern explicit
        raise HTTPException(
            status_code=401,
            detail="That code doesn't match an account. Check it and try again.",
        )

    token = secrets.token_urlsafe(32)
    expires = (datetime.now(timezone.utc) + timedelta(days=TOKEN_DAYS)) \
        .strftime("%Y-%m-%dT%H:%M:%SZ")
    conn.execute(
        "INSERT INTO parent_tokens (token, parent_id, created_at, expires_at, "
        "device_label) VALUES (?,?,?,?,?)",
        (token, matched["id"], utc_now_iso(), expires, payload.device_label),
    )
    conn.execute("DELETE FROM parent_tokens WHERE expires_at < ?", (utc_now_iso(),))
    conn.execute(
        "UPDATE parents SET last_login_at = ?, failed_logins = 0, "
        "locked_until = NULL WHERE id = ?",
        (utc_now_iso(), matched["id"]),
    )
    _recent_failures.pop(client, None)

    children = list_children(conn, matched["id"])
    return {
        "token": token,
        "parent": {
            "full_name": matched["full_name"],
            "email": matched["email"],
            "phone": matched["phone"],
            "avatar_color": matched["avatar_color"],
            "initials": _initials(matched["full_name"]),
        },
        "children": children,
    }


@router.get("/me", summary="Who is this token?")
def whoami(
    parent: sqlite3.Row = Depends(get_current_parent),
    conn: sqlite3.Connection = Depends(db_dependency),
) -> dict:
    """Lets the hub resume on reload without asking for the code again."""
    return {
        "parent": {
            "full_name": parent["full_name"],
            "email": parent["email"],
            "phone": parent["phone"],
            "avatar_color": parent["avatar_color"],
            "initials": _initials(parent["full_name"]),
        },
        "children": list_children(conn, parent["id"]),
    }


@router.post("/logout", summary="Sign out")
def logout(
    payload: TokenOnly,
    conn: sqlite3.Connection = Depends(db_dependency),
) -> dict:
    conn.execute("DELETE FROM parent_tokens WHERE token = ?", (payload.token,))
    return {"signed_out": True}
