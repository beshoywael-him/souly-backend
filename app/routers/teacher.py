"""
The teacher's dashboard API.

    GET   /api/teacher/board          everything the screen needs, in one call
    PATCH /api/teacher/flags/{id}     approve or dismiss, with the teacher named
    GET   /api/teacher/flags/{id}/events   one flag's audit trail

-----------------------------------------------------------------------------
THREE RULES THIS FILE FOLLOWS
-----------------------------------------------------------------------------
1. NOTHING HERE WRITES TO A CHILD'S TOTALS. A teacher approves or dismisses a
   flag. Stars, XP, levels and mastery belong to economy.py, exactly as they
   do for the parents' hub. A dashboard that could move a score is a dashboard
   that can disagree with the child's own screen.

2. THE STATE MACHINE IS NOT REIMPLEMENTED HERE. Moving a flag goes through the
   same `can_transition` table in app/models.py that routers/flags.py uses, so
   there is one definition of a legal move in the system and this screen
   cannot invent a shortcut past the teacher's own approval.

3. ONE CALL PER REFRESH. The dashboard polls, and it polls in a classroom on
   the same router as the camera. `GET /board` returns the queue, the roster
   and the counts together, because three polls every two seconds is three
   times the chance of being visibly stale while a judge is watching.

-----------------------------------------------------------------------------
WHAT THIS SCREEN DELIBERATELY DOES NOT SHOW
-----------------------------------------------------------------------------
It is the teacher's own screen and nobody else's. It is not the smart screen
at the front of the room: no display a class can see ever carries one child's
name next to a difficulty they are having. That was settled early and it is
the reason this router is behind a login while /students is not — the class
list is not a secret, but who is struggling with what certainly is.
"""

import sqlite3
from datetime import datetime, timedelta, timezone

from fastapi import APIRouter, Depends, HTTPException, Query

from app.db import db_dependency
from app.models import (
    ALLOWED_TRANSITIONS,
    FlagStatus,
    FlagTransition,
    can_transition,
    utc_now_iso,
)
from app.routers.teacher_auth import get_current_teacher

router = APIRouter(prefix="/api/teacher", tags=["teacher"])

# How far back "today" reaches for the activity counts. A lesson is an hour;
# a school day is six. Twelve keeps the morning visible in the afternoon.
RECENT_HOURS = 12


def _iso_hours_ago(hours: int) -> str:
    return (datetime.now(timezone.utc) - timedelta(hours=hours)) \
        .strftime("%Y-%m-%dT%H:%M:%SZ")


def _seconds_since(ts: str | None) -> int | None:
    if not ts:
        return None
    try:
        then = datetime.fromisoformat(ts.replace("Z", "+00:00"))
    except ValueError:
        return None
    return max(0, int((datetime.now(timezone.utc) - then).total_seconds()))


# =============================================================================
# GET /board — one read per refresh
# =============================================================================

@router.get("/board", summary="Everything the dashboard shows, in one call")
def get_board(
    since_hours: int = Query(RECENT_HOURS, ge=1, le=72),
    teacher: sqlite3.Row = Depends(get_current_teacher),
    conn: sqlite3.Connection = Depends(db_dependency),
) -> dict:
    cutoff = _iso_hours_ago(since_hours)

    # --- the queue --------------------------------------------------------
    # v_pending_flags already joins the child and the lesson, so a refresh is
    # one query rather than one per row.
    pending = [
        {
            "id": r["id"],
            "student_external_id": r["external_id"],
            "student_name": r["student_name"],
            "avatar": r["avatar"],
            "avatar_color": r["avatar_color"],
            "support_profile": r["support_profile"],
            "flag_type": r["flag_type"],
            "confidence": r["confidence"],
            "duration_ms": r["duration_ms"],
            "topic_title": r["topic_title"],
            "topic_subject": r["topic_subject"],
            "detected_at": r["detected_at"],
            "seconds_ago": _seconds_since(r["detected_at"]),
            "source": r["source"],
        }
        for r in conn.execute("SELECT * FROM v_pending_flags")
    ]

    # --- the class, and how each child is doing right now ------------------
    roster_rows = conn.execute(
        """
        SELECT s.id, s.external_id, s.display_name, s.avatar, s.avatar_color,
               s.grade, s.support_profile, s.drift_threshold_ms,
               (SELECT COUNT(*) FROM flags f
                 WHERE f.student_id = s.id AND f.status = 'pending')  AS pending_flags,
               (SELECT COUNT(*) FROM flags f
                 WHERE f.student_id = s.id AND f.detected_at >= ?)    AS flags_today,
               (SELECT MAX(f.detected_at) FROM flags f
                 WHERE f.student_id = s.id AND f.detected_at >= ?)    AS last_flag_at
        FROM students s
        WHERE s.is_active = 1
        ORDER BY s.display_name
        """,
        (cutoff, cutoff),
    ).fetchall()

    roster = []
    for r in roster_rows:
        seconds = _seconds_since(r["last_flag_at"])
        # Three states, and none of them is a claim about how the child feels.
        # "flagged" means a detection is waiting for this teacher to look at
        # it; "drifting" means one was raised recently and already handled.
        if r["pending_flags"]:
            state = "flagged"
        elif seconds is not None and seconds < 300:
            state = "drifting"
        else:
            state = "settled"

        roster.append({
            "external_id": r["external_id"],
            "display_name": r["display_name"],
            "avatar": r["avatar"],
            "avatar_color": r["avatar_color"],
            "grade": r["grade"],
            "support_profile": r["support_profile"],
            "drift_threshold_ms": r["drift_threshold_ms"],
            "pending_flags": r["pending_flags"],
            "flags_today": r["flags_today"],
            "last_flag_seconds_ago": seconds,
            "state": state,
        })

    # --- what has already been dealt with ---------------------------------
    handled = [
        {
            "id": r["id"],
            "student_name": r["display_name"],
            "flag_type": r["flag_type"],
            "status": r["status"],
            "topic_title": r["topic_title"],
            "reviewed_at": r["reviewed_at"],
            "seconds_ago": _seconds_since(r["reviewed_at"] or r["detected_at"]),
        }
        for r in conn.execute(
            """
            SELECT f.id, f.flag_type, f.status, f.reviewed_at, f.detected_at,
                   s.display_name, t.title AS topic_title
            FROM flags f
            JOIN students s ON s.id = f.student_id
            LEFT JOIN topics t ON t.id = f.topic_id
            WHERE f.status IN ('approved','dismissed','in_progress','done')
              AND f.detected_at >= ?
            ORDER BY COALESCE(f.reviewed_at, f.detected_at) DESC
            LIMIT 12
            """,
            (cutoff,),
        )
    ]

    counts = conn.execute(
        """
        SELECT
          SUM(CASE WHEN status = 'pending'     THEN 1 ELSE 0 END) AS pending,
          SUM(CASE WHEN status = 'approved'    THEN 1 ELSE 0 END) AS approved,
          SUM(CASE WHEN status = 'dismissed'   THEN 1 ELSE 0 END) AS dismissed,
          SUM(CASE WHEN status = 'in_progress' THEN 1 ELSE 0 END) AS in_progress,
          SUM(CASE WHEN status = 'done'        THEN 1 ELSE 0 END) AS done,
          COUNT(*)                                               AS total
        FROM flags WHERE detected_at >= ?
        """,
        (cutoff,),
    ).fetchone()

    return {
        "teacher": {
            "full_name": teacher["full_name"],
            "title": teacher["title"],
            "initials": teacher["initials"],
            "avatar_color": teacher["avatar_color"],
        },
        "queue": pending,
        "roster": roster,
        "handled": handled,
        "counts": {k: (counts[k] or 0) for k in
                   ("pending", "approved", "dismissed", "in_progress", "done", "total")},
        "window_hours": since_hours,
        "server_time": utc_now_iso(),
    }


# =============================================================================
# PATCH /flags/{id} — the approval gate itself
# =============================================================================

@router.patch("/flags/{flag_id}", summary="Approve or dismiss a flag")
def review_flag(
    flag_id: int,
    payload: FlagTransition,
    teacher: sqlite3.Row = Depends(get_current_teacher),
    conn: sqlite3.Connection = Depends(db_dependency),
) -> dict:
    """
    A teacher may only approve or dismiss. `in_progress` belongs to the robot
    and `done` belongs to the child finishing the work — a teacher marking a
    flag done from this screen would be recording a lesson that never
    happened.

    The teacher id comes from the token, never from the request body. A
    dashboard that can name somebody else as the reviewer is a dashboard that
    can forge an approval.
    """
    if payload.status not in (FlagStatus.APPROVED, FlagStatus.DISMISSED):
        raise HTTPException(
            status_code=422,
            detail=("A teacher can approve or dismiss a flag. "
                    "'in_progress' is set by the robot when it picks the flag "
                    "up, and 'done' when the child finishes the work."),
        )

    row = conn.execute(
        "SELECT status FROM flags WHERE id = ?", (flag_id,)
    ).fetchone()
    if row is None:
        raise HTTPException(status_code=404, detail=f"Flag {flag_id} not found")

    current = FlagStatus(row["status"])
    if current == payload.status:
        # A double-tap, or a retry after a dropped connection. Not an error.
        return {"flag_id": flag_id, "status": current.value, "changed": False}

    if not can_transition(current, payload.status):
        allowed = sorted(s.value for s in ALLOWED_TRANSITIONS.get(current, set()))
        raise HTTPException(
            status_code=409,
            detail=(f"Flag {flag_id} is '{current.value}'. Allowed from there: "
                    f"{allowed or 'nothing (terminal state)'}."),
        )

    now = utc_now_iso()
    conn.execute(
        "UPDATE flags SET status = ?, reviewed_at = ?, "
        "reviewed_by_teacher_id = ?, resolution_note = COALESCE(?, resolution_note) "
        "WHERE id = ?",
        (payload.status.value, now, teacher["id"], payload.note, flag_id),
    )
    if payload.status == FlagStatus.DISMISSED:
        conn.execute("UPDATE flags SET resolved_at = ? WHERE id = ?", (now, flag_id))

    conn.execute(
        "INSERT INTO flag_events (flag_id, from_status, to_status, actor, note, "
        "created_at) VALUES (?,?,?,?,?,?)",
        (flag_id, current.value, payload.status.value,
         f"teacher:{teacher['id']}", payload.note, now),
    )

    return {
        "flag_id": flag_id,
        "status": payload.status.value,
        "changed": True,
        "reviewed_by": teacher["full_name"],
        "reviewed_at": now,
    }


@router.get("/flags/{flag_id}/events", summary="One flag's audit trail")
def flag_events(
    flag_id: int,
    teacher: sqlite3.Row = Depends(get_current_teacher),
    conn: sqlite3.Connection = Depends(db_dependency),
) -> dict:
    """
    Every state this flag has been in, and who moved it.

    Worth having on the teacher's own screen rather than only in the database:
    "why did this child get a nudge" should be answerable by the person who
    approved it, without anybody opening a SQL client.
    """
    if conn.execute("SELECT 1 FROM flags WHERE id = ?", (flag_id,)).fetchone() is None:
        raise HTTPException(status_code=404, detail=f"Flag {flag_id} not found")

    rows = conn.execute(
        "SELECT id, from_status, to_status, actor, note, created_at "
        "FROM flag_events WHERE flag_id = ? ORDER BY id",
        (flag_id,),
    ).fetchall()

    return {"flag_id": flag_id, "events": [dict(r) for r in rows]}
