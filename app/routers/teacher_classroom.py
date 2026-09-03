"""
The teacher's classroom dashboard — the class-scoped API.

    GET   /api/teacher/classes                        the switcher
    GET   /api/teacher/classes/{id}/overview          the Today screen
    GET   /api/teacher/classes/{id}/students          roster with progress
    GET   /api/teacher/students/{ext}                 one child, in full
    PATCH /api/teacher/students/{ext}                 what a teacher may change
    POST  /api/teacher/students/{ext}/reset-password  clear the picture password

    GET   /api/teacher/classes/{id}/flags             the attention queue
    GET   /api/teacher/classes/{id}/notes             notes sent home
    POST  /api/teacher/notes                          write one

    GET   /api/teacher/conversations                  threads with parents
    GET   /api/teacher/conversations/{id}             one thread
    POST  /api/teacher/conversations/{id}/messages    reply
    POST  /api/teacher/students/{ext}/conversations   open a thread

    GET   /api/teacher/curriculum                     books and what is verified
    GET   /api/teacher/review                         questions awaiting a human
    POST  /api/teacher/review/{id}                    approve or reject one
    GET   /api/teacher/classes/{id}/settings          class settings
    PUT   /api/teacher/classes/{id}/settings

-----------------------------------------------------------------------------
FOUR RULES THIS FILE FOLLOWS
-----------------------------------------------------------------------------
1. EVERY CLASS-SCOPED READ PASSES THROUGH `v_teacher_classes`.
   That view is the "this teacher teaches this class" join, written once. An
   endpoint that forgets to filter is a bug; an endpoint that selects from the
   view and still forgets is a bug you can find by grepping for the view name.
   Same discipline as v_parent_children on the parent side.

2. A CLASS THAT ISN'T YOURS RETURNS 404, NOT 403.
   403 confirms the class exists. The existence of another teacher's class,
   and of the children in it, is exactly the fact being withheld.

3. PROGRESS IS COMPUTED BY THE PARENT MODULE'S HELPERS, NOT REIMPLEMENTED.
   The imports below are deliberate. If a teacher and a parent ever disagree
   about how many minutes a child worked this week, the argument is
   unresolvable and both screens lose their credibility. One definition, two
   readers.

4. NOTHING HERE WRITES A CHILD'S TOTALS.
   Stars, XP, levels and mastery belong to economy.py. A teacher approves a
   flag, writes a note, verifies a question. A dashboard that could move a
   score is a dashboard that can disagree with the child's own screen.
"""

import json
import sqlite3
from datetime import datetime, timedelta, timezone

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field

from app.db import db_dependency
from app.models import utc_now_iso
from app.routers.teacher_auth import get_current_teacher

# Rule 3. Underscore-prefixed, and imported anyway — one definition of
# "progress" for every screen that shows it.
from app.routers.parent import (
    _fmt_duration,
    _independence,
    _recent_activity,
    _seconds_between,
    _subjects,
    _week_days,
    _week_start,
)

router = APIRouter(prefix="/api/teacher", tags=["teacher-classroom"])

RECENT_HOURS = 12


# =============================================================================
# Helpers
# =============================================================================

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


def get_teacher_class(
    class_id: int,
    teacher: sqlite3.Row = Depends(get_current_teacher),
    conn: sqlite3.Connection = Depends(db_dependency),
) -> sqlite3.Row:
    """Rule 1 and rule 2, in one dependency."""
    row = conn.execute(
        "SELECT * FROM v_teacher_classes WHERE teacher_id = ? AND class_id = ?",
        (teacher["id"], class_id),
    ).fetchone()
    if row is None:
        raise HTTPException(status_code=404, detail="No such class")
    return row


def _teachable_student(conn, teacher_id: int, ext: str) -> sqlite3.Row:
    """
    A child this teacher actually teaches — in any of their classes.

    A teacher opening a child's card from the Students screen has already been
    class-scoped; this covers the drawer, notes and messages, which are reached
    by external id and would otherwise be an open door to the whole school.
    """
    row = conn.execute(
        """
        SELECT DISTINCT s.*
        FROM students s
        JOIN class_students cs ON cs.student_id = s.id
        JOIN v_teacher_classes v ON v.class_id = cs.class_id
        WHERE v.teacher_id = ? AND s.external_id = ? AND s.is_active = 1
        """,
        (teacher_id, ext),
    ).fetchone()
    if row is None:
        raise HTTPException(status_code=404, detail="No such student")
    return row


def _student_card(conn, r: sqlite3.Row, cutoff: str) -> dict:
    """The row every roster and every drawer shares."""
    sid = r["id"]
    pending = int(conn.execute(
        "SELECT COUNT(*) FROM flags WHERE student_id=? AND status='pending'", (sid,)
    ).fetchone()[0])
    today = int(conn.execute(
        "SELECT COUNT(*) FROM flags WHERE student_id=? AND detected_at>=?",
        (sid, cutoff),
    ).fetchone()[0])
    last_flag = conn.execute(
        "SELECT MAX(detected_at) FROM flags WHERE student_id=? AND detected_at>=?",
        (sid, cutoff),
    ).fetchone()[0]
    seconds = _seconds_since(last_flag)

    # Three states, and none of them is a claim about how the child feels.
    if pending:
        state = "flagged"
    elif seconds is not None and seconds < 300:
        state = "drifting"
    else:
        state = "settled"

    week_seconds = _seconds_between(conn, sid, _week_start())
    subs = _subjects(conn, sid)
    active = [s for s in subs if s["has_data"]]
    overall = round(sum(s["progress_pct"] for s in active) / len(active)) if active else None

    pages = int(conn.execute(
        "SELECT COUNT(DISTINCT page_id) FROM page_activity WHERE student_id=?", (sid,)
    ).fetchone()[0])

    # The Students screen shows how much help each child needed, and who to
    # write home to. Both are per-child queries; a class here is single
    # figures, so the cost is nothing and the screen is complete.
    indep = _independence(conn, sid)
    parent = conn.execute(
        "SELECT p.full_name FROM parents p JOIN parent_student ps ON ps.parent_id = p.id "
        "WHERE ps.student_id = ? ORDER BY p.id LIMIT 1", (sid,)
    ).fetchone()

    return {
        "external_id": r["external_id"],
        "display_name": r["display_name"],
        "avatar": r["avatar"],
        "avatar_color": r["avatar_color"],
        "grade": r["grade"],
        "support_profile": r["support_profile"],
        "support_notes": r["support_notes"],
        "drift_threshold_ms": r["drift_threshold_ms"],
        "onboarded": r["onboarded_at"] is not None,
        "last_active_date": r["last_active_date"],
        "stars": r["stars"],
        "level": r["level"],
        "day_streak": r["day_streak"],
        "pending_flags": pending,
        "flags_today": today,
        "last_flag_seconds_ago": seconds,
        "state": state,
        "week_seconds": week_seconds,
        "week_time": _fmt_duration(week_seconds),
        "pages_worked": pages,
        "overall_pct": overall,
        "has_scores": overall is not None,
        "independence": indep,
        "parent_name": parent["full_name"] if parent else None,
    }


def _roster(conn, class_id: int, cutoff: str) -> list[dict]:
    rows = conn.execute(
        """
        SELECT s.* FROM students s
        JOIN class_students cs ON cs.student_id = s.id
        WHERE cs.class_id = ? AND s.is_active = 1
        ORDER BY s.display_name
        """,
        (class_id,),
    ).fetchall()
    return [_student_card(conn, r, cutoff) for r in rows]


def _class_flags(conn, class_id: int, cutoff: str, limit: int = 40) -> list[dict]:
    """
    Flags for the children in this class.

    Joined through class_students rather than flags.class_id, because a flag
    raised by the classroom camera before a lesson session was opened has no
    class_id on it. The child's membership is the reliable link.
    """
    rows = conn.execute(
        """
        SELECT f.id, f.flag_type, f.confidence, f.duration_ms, f.status,
               f.detected_at, f.reviewed_at, f.source, f.resolution_note,
               s.external_id, s.display_name, s.avatar, s.avatar_color,
               s.support_profile,
               t.title AS topic_title, t.subject AS topic_subject
        FROM flags f
        JOIN students s ON s.id = f.student_id
        JOIN class_students cs ON cs.student_id = s.id AND cs.class_id = ?
        LEFT JOIN topics t ON t.id = f.topic_id
        WHERE f.detected_at >= ?
        ORDER BY f.detected_at DESC
        LIMIT ?
        """,
        (class_id, cutoff, limit),
    ).fetchall()
    return [
        {
            "id": r["id"],
            "student_external_id": r["external_id"],
            "student_name": r["display_name"],
            "avatar": r["avatar"],
            "avatar_color": r["avatar_color"],
            "support_profile": r["support_profile"],
            "flag_type": r["flag_type"],
            "confidence": r["confidence"],
            "duration_ms": r["duration_ms"],
            "status": r["status"],
            "topic_title": r["topic_title"],
            "topic_subject": r["topic_subject"],
            "detected_at": r["detected_at"],
            "seconds_ago": _seconds_since(r["detected_at"]),
            "source": r["source"],
            "resolution_note": r["resolution_note"],
        }
        for r in rows
    ]


# =============================================================================
# Classes
# =============================================================================

@router.get("/classes", summary="Every class this teacher holds")
def classes(
    teacher: sqlite3.Row = Depends(get_current_teacher),
    conn: sqlite3.Connection = Depends(db_dependency),
) -> dict:
    rows = conn.execute(
        "SELECT * FROM v_teacher_classes WHERE teacher_id = ? "
        "ORDER BY grade, class_name",
        (teacher["id"],),
    ).fetchall()

    cutoff = _iso_hours_ago(RECENT_HOURS)
    out = []
    for r in rows:
        pending = int(conn.execute(
            """
            SELECT COUNT(*) FROM flags f
            JOIN class_students cs ON cs.student_id = f.student_id
            WHERE cs.class_id = ? AND f.status = 'pending' AND f.detected_at >= ?
            """,
            (r["class_id"], cutoff),
        ).fetchone()[0])
        out.append({
            "id": r["class_id"],
            "name": r["class_name"],
            "grade": r["grade"],
            "subject": r["subject_name"],
            "subject_code": r["subject_code"],
            "icon": r["subject_icon"],
            "color": r["color_from"],
            "role": r["role"],
            "academic_year": r["academic_year"],
            "student_count": r["student_count"],
            "pending_flags": pending,
        })

    return {
        "teacher": {
            "id": teacher["id"],
            "full_name": teacher["full_name"],
            "title": teacher["title"],
            "initials": teacher["initials"],
            "avatar_color": teacher["avatar_color"],
        },
        "classes": out,
        "server_time": utc_now_iso(),
    }


@router.get("/classes/{class_id}/overview", summary="The Today screen, in one call")
def overview(
    since_hours: int = Query(RECENT_HOURS, ge=1, le=72),
    klass: sqlite3.Row = Depends(get_teacher_class),
    conn: sqlite3.Connection = Depends(db_dependency),
) -> dict:
    """
    One call per refresh.

    The dashboard polls, and it polls on the same network as the camera and
    four tablets. Three requests every refresh is three times the chance of
    being visibly half-stale while a judge is watching.
    """
    cid = klass["class_id"]
    cutoff = _iso_hours_ago(since_hours)

    roster = _roster(conn, cid, cutoff)
    flags = _class_flags(conn, cid, cutoff)

    session = conn.execute(
        "SELECT * FROM v_open_sessions WHERE class_id = ?", (cid,)
    ).fetchone()

    unread = int(conn.execute(
        """
        SELECT COUNT(*) FROM conversation_messages m
        JOIN conversations cv ON cv.id = m.conversation_id
        JOIN class_students cs ON cs.student_id = cv.student_id AND cs.class_id = ?
        WHERE cv.teacher_id = ? AND m.sender_role = 'parent' AND m.read_at IS NULL
        """,
        (cid, klass["teacher_id"]),
    ).fetchone()[0])

    return {
        "class": {
            "id": cid,
            "name": klass["class_name"],
            "grade": klass["grade"],
            "subject": klass["subject_name"],
            "icon": klass["subject_icon"],
            "color": klass["color_from"],
            "role": klass["role"],
            "student_count": klass["student_count"],
        },
        "session": None if session is None else {
            "id": session["session_id"],
            "started_at": session["started_at"],
            "elapsed_seconds": _seconds_since(session["started_at"]),
            "teacher_name": session["teacher_name"],
            "flag_count": session["flag_count"],
        },
        "roster": roster,
        "flags": flags,
        "counts": {
            "students": len(roster),
            "pending": sum(1 for f in flags if f["status"] == "pending"),
            "handled": sum(1 for f in flags if f["status"] != "pending"),
            "needing_attention": sum(1 for s in roster if s["state"] != "settled"),
            "unread_messages": unread,
        },
        "window_hours": since_hours,
        "server_time": utc_now_iso(),
    }


@router.get("/classes/{class_id}/students", summary="Roster with progress")
def class_students(
    klass: sqlite3.Row = Depends(get_teacher_class),
    conn: sqlite3.Connection = Depends(db_dependency),
) -> dict:
    return {
        "class_id": klass["class_id"],
        "students": _roster(conn, klass["class_id"], _iso_hours_ago(RECENT_HOURS)),
    }


@router.get("/classes/{class_id}/flags", summary="The attention queue")
def class_flags(
    since_hours: int = Query(RECENT_HOURS, ge=1, le=168),
    klass: sqlite3.Row = Depends(get_teacher_class),
    conn: sqlite3.Connection = Depends(db_dependency),
) -> dict:
    return {
        "class_id": klass["class_id"],
        "flags": _class_flags(conn, klass["class_id"], _iso_hours_ago(since_hours)),
    }


# =============================================================================
# One child
# =============================================================================

@router.get("/students/{student_ext_id}", summary="One child, in full")
def student_detail(
    student_ext_id: str,
    teacher: sqlite3.Row = Depends(get_current_teacher),
    conn: sqlite3.Connection = Depends(db_dependency),
) -> dict:
    child = _teachable_student(conn, teacher["id"], student_ext_id)
    sid = child["id"]
    cutoff = _iso_hours_ago(RECENT_HOURS)

    monday = _week_start()
    prev = _week_start(1)

    return {
        "student": _student_card(conn, child, cutoff),
        "subjects": _subjects(conn, sid),
        "week": _week_days(conn, sid, monday),
        "week_seconds": _seconds_between(conn, sid, monday),
        "prev_week_seconds": _seconds_between(conn, sid, prev, monday),
        "independence": _independence(conn, sid),
        "recent": _recent_activity(conn, sid, 8),
        "classes": [
            {"id": r["class_id"], "name": r["class_name"]}
            for r in conn.execute(
                """
                SELECT c.id AS class_id, c.name AS class_name
                FROM class_students cs JOIN classes c ON c.id = cs.class_id
                WHERE cs.student_id = ? AND c.is_active = 1
                """,
                (sid,),
            )
        ],
        "parents": [
            {"id": r["id"], "name": r["full_name"], "relationship": r["relationship"]}
            for r in conn.execute(
                """
                SELECT p.id, p.full_name, ps.relationship
                FROM parent_student ps JOIN parents p ON p.id = ps.parent_id
                WHERE ps.student_id = ?
                """,
                (sid,),
            )
        ],
        "recent_flags": _student_flags(conn, sid, cutoff),
    }


def _student_flags(conn, sid: int, cutoff: str) -> list[dict]:
    return [
        {
            "id": r["id"],
            "flag_type": r["flag_type"],
            "status": r["status"],
            "confidence": r["confidence"],
            "detected_at": r["detected_at"],
            "seconds_ago": _seconds_since(r["detected_at"]),
            "topic_title": r["topic_title"],
        }
        for r in conn.execute(
            """
            SELECT f.id, f.flag_type, f.status, f.confidence, f.detected_at,
                   t.title AS topic_title
            FROM flags f LEFT JOIN topics t ON t.id = f.topic_id
            WHERE f.student_id = ? AND f.detected_at >= ?
            ORDER BY f.detected_at DESC LIMIT 10
            """,
            (sid, cutoff),
        )
    ]


class StudentPatch(BaseModel):
    """Only the fields a teacher is allowed to move."""
    drift_threshold_ms: int | None = Field(None, ge=1000, le=60000)
    support_notes: str | None = Field(None, max_length=2000)


@router.patch("/students/{student_ext_id}", summary="What a teacher may change")
def update_student(
    student_ext_id: str,
    payload: StudentPatch,
    teacher: sqlite3.Row = Depends(get_current_teacher),
    conn: sqlite3.Connection = Depends(db_dependency),
) -> dict:
    """
    An allowlist, not a spread.

    A teacher may lengthen the attention threshold for a child who looks away
    while thinking, and may write down what helps that child. A teacher may
    not touch stars, level, streak or mastery from here — those are the
    child's own record of their own work.
    """
    child = _teachable_student(conn, teacher["id"], student_ext_id)
    sets, vals = [], []
    if payload.drift_threshold_ms is not None:
        sets.append("drift_threshold_ms = ?")
        vals.append(payload.drift_threshold_ms)
    if payload.support_notes is not None:
        sets.append("support_notes = ?")
        vals.append(payload.support_notes.strip())
    if not sets:
        raise HTTPException(status_code=400, detail="Nothing to change")

    vals.append(child["id"])
    conn.execute(f"UPDATE students SET {', '.join(sets)} WHERE id = ?", vals)
    fresh = conn.execute("SELECT * FROM students WHERE id = ?", (child["id"],)).fetchone()
    return {"student": _student_card(conn, fresh, _iso_hours_ago(RECENT_HOURS))}


@router.post("/students/{student_ext_id}/reset-password",
             summary="Let a child choose new pictures")
def reset_picture_password(
    student_ext_id: str,
    teacher: sqlite3.Row = Depends(get_current_teacher),
    conn: sqlite3.Connection = Depends(db_dependency),
) -> dict:
    """
    Clears the hash so the child sets a new sequence at their next sign-in.

    The old sequence is not shown to anybody, because it is not stored — only
    its hash ever was, and this deletes that.
    """
    child = _teachable_student(conn, teacher["id"], student_ext_id)
    conn.execute(
        "UPDATE students SET picture_password_hash = NULL, password_set_at = NULL, "
        "failed_logins = 0, locked_until = NULL WHERE id = ?",
        (child["id"],),
    )
    conn.execute("DELETE FROM auth_tokens WHERE student_id = ?", (child["id"],))
    return {"reset": True, "student": child["display_name"]}


# =============================================================================
# Notes home
# =============================================================================

@router.get("/classes/{class_id}/notes", summary="Notes this teacher sent home")
def class_notes(
    klass: sqlite3.Row = Depends(get_teacher_class),
    conn: sqlite3.Connection = Depends(db_dependency),
) -> dict:
    rows = conn.execute(
        """
        SELECT n.*, s.external_id, s.display_name, s.avatar, s.avatar_color
        FROM teacher_notes n
        JOIN students s ON s.id = n.student_id
        JOIN class_students cs ON cs.student_id = s.id AND cs.class_id = ?
        WHERE n.teacher_id = ?
        ORDER BY n.created_at DESC LIMIT 50
        """,
        (klass["class_id"], klass["teacher_id"]),
    ).fetchall()
    return {
        "notes": [
            {
                "id": r["id"],
                "student_external_id": r["external_id"],
                "student_name": r["display_name"],
                "avatar": r["avatar"],
                "avatar_color": r["avatar_color"],
                "tone": r["tone"],
                "body": r["body"],
                "read": r["read_at"] is not None,
                "read_at": r["read_at"],
                "created_at": r["created_at"],
                "seconds_ago": _seconds_since(r["created_at"]),
            }
            for r in rows
        ]
    }


class NoteIn(BaseModel):
    student_external_id: str = Field(..., min_length=1, max_length=64)
    tone: str = Field("progress", pattern="^(praise|progress|concern)$")
    body: str = Field(..., min_length=1, max_length=2000)


@router.post("/notes", status_code=201, summary="Write a note home")
def create_note(
    payload: NoteIn,
    teacher: sqlite3.Row = Depends(get_current_teacher),
    conn: sqlite3.Connection = Depends(db_dependency),
) -> dict:
    """Lands in the parents' hub unread, which is what drives their badge."""
    child = _teachable_student(conn, teacher["id"], payload.student_external_id)
    cur = conn.execute(
        "INSERT INTO teacher_notes (student_id, teacher_id, tone, body, created_at) "
        "VALUES (?,?,?,?,?)",
        (child["id"], teacher["id"], payload.tone, payload.body.strip(), utc_now_iso()),
    )
    return {
        "id": cur.lastrowid,
        "student_name": child["display_name"],
        "tone": payload.tone,
        "created_at": utc_now_iso(),
    }


# =============================================================================
# Messages with parents — the teacher half of the parents' hub
# =============================================================================

@router.get("/conversations", summary="Threads with parents")
def conversations(
    class_id: int | None = Query(None),
    teacher: sqlite3.Row = Depends(get_current_teacher),
    conn: sqlite3.Connection = Depends(db_dependency),
) -> dict:
    """
    Mirror of the parent side, from the other end.

    `child` is on every row for the same reason it is there for parents:
    Fayrouz has two sons, so one teacher can hold two live threads with the
    same parent. Without the child on the row the list is unusable.
    """
    sql = """
        SELECT cv.id, cv.last_message_at, cv.created_at,
               p.full_name AS parent_name, p.avatar_color AS parent_color,
               s.display_name AS child_name, s.external_id AS child_ext_id,
               s.avatar AS child_avatar,
               (SELECT body FROM conversation_messages m
                 WHERE m.conversation_id = cv.id
                 ORDER BY m.created_at DESC LIMIT 1)          AS last_body,
               (SELECT COUNT(*) FROM conversation_messages m
                 WHERE m.conversation_id = cv.id
                   AND m.sender_role = 'parent'
                   AND m.read_at IS NULL)                      AS unread
        FROM conversations cv
        JOIN parents  p ON p.id = cv.parent_id
        JOIN students s ON s.id = cv.student_id
        WHERE cv.teacher_id = ?
    """
    params: list = [teacher["id"]]
    if class_id is not None:
        # Rule 1: the class must be one of this teacher's.
        get_teacher_class(class_id, teacher, conn)
        sql += (" AND EXISTS (SELECT 1 FROM class_students cs "
                "WHERE cs.student_id = cv.student_id AND cs.class_id = ?)")
        params.append(class_id)
    sql += " ORDER BY COALESCE(cv.last_message_at, cv.created_at) DESC"

    rows = conn.execute(sql, params).fetchall()
    return {
        "conversations": [
            {
                "id": r["id"],
                "parent": r["parent_name"],
                "parent_color": r["parent_color"],
                "child": r["child_name"],
                "child_ext_id": r["child_ext_id"],
                "child_avatar": r["child_avatar"],
                "last_body": r["last_body"],
                "last_message_at": r["last_message_at"],
                "unread": int(r["unread"] or 0),
            }
            for r in rows
        ],
        "unread_total": sum(int(r["unread"] or 0) for r in rows),
    }


def _own_thread(conn, teacher_id: int, conversation_id: int) -> sqlite3.Row:
    row = conn.execute(
        """
        SELECT cv.*, p.full_name AS parent_name, p.avatar_color AS parent_color,
               s.display_name AS child_name, s.external_id AS child_ext_id,
               s.avatar AS child_avatar
        FROM conversations cv
        JOIN parents  p ON p.id = cv.parent_id
        JOIN students s ON s.id = cv.student_id
        WHERE cv.id = ? AND cv.teacher_id = ?
        """,
        (conversation_id, teacher_id),
    ).fetchone()
    if row is None:
        raise HTTPException(status_code=404, detail="No such conversation")
    return row


@router.get("/conversations/{conversation_id}", summary="One thread")
def conversation(
    conversation_id: int,
    teacher: sqlite3.Row = Depends(get_current_teacher),
    conn: sqlite3.Connection = Depends(db_dependency),
) -> dict:
    """Opening a thread marks the parent's messages read, as the hub does."""
    convo = _own_thread(conn, teacher["id"], conversation_id)

    conn.execute(
        "UPDATE conversation_messages SET read_at = ? "
        "WHERE conversation_id = ? AND sender_role = 'parent' AND read_at IS NULL",
        (utc_now_iso(), conversation_id),
    )

    msgs = conn.execute(
        "SELECT * FROM conversation_messages WHERE conversation_id = ? "
        "ORDER BY created_at",
        (conversation_id,),
    ).fetchall()

    return {
        "id": convo["id"],
        "parent": convo["parent_name"],
        "parent_color": convo["parent_color"],
        "child": convo["child_name"],
        "child_ext_id": convo["child_ext_id"],
        "child_avatar": convo["child_avatar"],
        "messages": [
            {
                "id": m["id"],
                "from": m["sender_role"],
                "mine": m["sender_role"] == "teacher",
                "body": m["body"],
                "created_at": m["created_at"],
                "read": m["read_at"] is not None,
            }
            for m in msgs
        ],
    }


class MessageIn(BaseModel):
    body: str = Field(..., min_length=1, max_length=4000)


@router.post("/conversations/{conversation_id}/messages", status_code=201,
             summary="Reply to a parent")
def send_message(
    conversation_id: int,
    payload: MessageIn,
    teacher: sqlite3.Row = Depends(get_current_teacher),
    conn: sqlite3.Connection = Depends(db_dependency),
) -> dict:
    convo = _own_thread(conn, teacher["id"], conversation_id)
    now = utc_now_iso()
    cur = conn.execute(
        "INSERT INTO conversation_messages "
        "(conversation_id, sender_role, sender_id, body, created_at) "
        "VALUES (?,'teacher',?,?,?)",
        (convo["id"], teacher["id"], payload.body.strip(), now),
    )
    conn.execute("UPDATE conversations SET last_message_at = ? WHERE id = ?",
                 (now, convo["id"]))
    return {"id": cur.lastrowid, "created_at": now, "body": payload.body.strip()}


class ThreadIn(BaseModel):
    parent_id: int | None = None


@router.post("/students/{student_ext_id}/conversations", status_code=201,
             summary="Open a thread about a child")
def start_conversation(
    student_ext_id: str,
    payload: ThreadIn,
    teacher: sqlite3.Row = Depends(get_current_teacher),
    conn: sqlite3.Connection = Depends(db_dependency),
) -> dict:
    """
    Idempotent. `UNIQUE (parent_id, teacher_id, student_id)` means a second
    call returns the thread that already exists rather than a duplicate — and
    that uniqueness is per child, so Fayrouz's two sons get two threads with
    the same teacher, which is correct.
    """
    child = _teachable_student(conn, teacher["id"], student_ext_id)

    if payload.parent_id is not None:
        parent = conn.execute(
            "SELECT p.* FROM parents p JOIN parent_student ps ON ps.parent_id = p.id "
            "WHERE p.id = ? AND ps.student_id = ?",
            (payload.parent_id, child["id"]),
        ).fetchone()
    else:
        parent = conn.execute(
            "SELECT p.* FROM parents p JOIN parent_student ps ON ps.parent_id = p.id "
            "WHERE ps.student_id = ? ORDER BY p.id LIMIT 1",
            (child["id"],),
        ).fetchone()

    if parent is None:
        raise HTTPException(status_code=404,
                            detail="No parent is linked to this child")

    existing = conn.execute(
        "SELECT * FROM conversations WHERE parent_id=? AND teacher_id=? AND student_id=?",
        (parent["id"], teacher["id"], child["id"]),
    ).fetchone()
    if existing:
        return {"id": existing["id"], "created": False,
                "parent": parent["full_name"], "child": child["display_name"]}

    cur = conn.execute(
        "INSERT INTO conversations (parent_id, teacher_id, student_id, created_at) "
        "VALUES (?,?,?,?)",
        (parent["id"], teacher["id"], child["id"], utc_now_iso()),
    )
    return {"id": cur.lastrowid, "created": True,
            "parent": parent["full_name"], "child": child["display_name"]}


# =============================================================================
# Curriculum
# =============================================================================

@router.get("/curriculum", summary="Books, and how much is verified")
def curriculum(
    teacher: sqlite3.Row = Depends(get_current_teacher),
    conn: sqlite3.Connection = Depends(db_dependency),
) -> dict:
    """
    `is_verified` is the gate the whole tutor rests on: retrieval refuses
    anything a human has not ticked, so this screen is where that human is.
    """
    rows = conn.execute(
        "SELECT * FROM curriculum_books ORDER BY grade, subject, title"
    ).fetchall()
    out = []
    for b in rows:
        lessons = int(conn.execute(
            "SELECT COUNT(*) FROM topics WHERE book_id = ?", (b["id"],)
        ).fetchone()[0])
        verified = int(conn.execute(
            "SELECT COUNT(*) FROM topics WHERE book_id = ? AND is_verified = 1",
            (b["id"],),
        ).fetchone()[0])
        out.append({
            "id": b["id"],
            "code": b["code"],
            "title": b["title"],
            "subject": b["subject"],
            "grade": b["grade"],
            "term": b["term"],
            "pages": b["page_count"],
            "lessons": lessons,
            "verified_lessons": verified,
            "book_verified": bool(b["is_verified"]),
        })
    return {"books": out}


# =============================================================================
# Question review
# =============================================================================

@router.get("/review", summary="Generated questions awaiting a human")
def review_queue(
    limit: int = Query(25, ge=1, le=100),
    teacher: sqlite3.Row = Depends(get_current_teacher),
    conn: sqlite3.Connection = Depends(db_dependency),
) -> dict:
    """Nothing here has reached a child. `review_status` gates retrieval."""
    rows = conn.execute(
        """
        SELECT q.*, t.title AS topic_title, t.subject AS topic_subject,
               t.lesson_label
        FROM questions q
        LEFT JOIN topics t ON t.id = q.topic_id
        WHERE q.review_status = 'pending'
        ORDER BY q.created_at DESC LIMIT ?
        """,
        (limit,),
    ).fetchall()

    out = []
    for q in rows:
        try:
            options = json.loads(q["options_json"])
        except (TypeError, ValueError):
            options = []
        out.append({
            "id": q["id"],
            "lesson": q["lesson_label"] or q["topic_title"] or "Unassigned",
            "subject": q["topic_subject"],
            "stem": q["prompt"],
            "options": options,
            "correct": q["correct_index"],
            "explanation": q["explanation"],
            "hint": q["hint"],
            "difficulty": q["difficulty"],
            "engine": q["engine"],
            "source_page": q["source_page"],
            "created_at": q["created_at"],
        })
    return {"questions": out, "total": len(out)}


class ReviewIn(BaseModel):
    decision: str = Field(..., pattern="^(approve|reject)$")


@router.post("/review/{question_id}", summary="Approve or reject one question")
def review_question(
    question_id: int,
    payload: ReviewIn,
    teacher: sqlite3.Row = Depends(get_current_teacher),
    conn: sqlite3.Connection = Depends(db_dependency),
) -> dict:
    row = conn.execute("SELECT * FROM questions WHERE id = ?", (question_id,)).fetchone()
    if row is None:
        raise HTTPException(status_code=404, detail="No such question")
    if row["review_status"] != "pending":
        raise HTTPException(status_code=409,
                            detail=f"Already {row['review_status']}")

    status = "approved" if payload.decision == "approve" else "rejected"
    conn.execute("UPDATE questions SET review_status = ? WHERE id = ?",
                 (status, question_id))
    return {"id": question_id, "review_status": status,
            "reviewed_by": teacher["full_name"]}


# =============================================================================
# Class settings
# =============================================================================

@router.get("/classes/{class_id}/settings", summary="Class settings")
def get_settings(
    klass: sqlite3.Row = Depends(get_teacher_class),
    conn: sqlite3.Connection = Depends(db_dependency),
) -> dict:
    row = conn.execute("SELECT * FROM classes WHERE id = ?",
                       (klass["class_id"],)).fetchone()
    device = conn.execute(
        "SELECT label, last_seen_at, lcd_cols, lcd_rows, is_active "
        "FROM devices WHERE class_id = ?", (klass["class_id"],)
    ).fetchone()

    return {
        "class": {
            "id": row["id"],
            "name": row["name"],
            "short_name": row["short_name"],
            "grade": row["grade"],
            "academic_year": row["academic_year"],
            "is_active": bool(row["is_active"]),
        },
        "device": None if device is None else {
            "label": device["label"],
            "last_seen_at": device["last_seen_at"],
            "seconds_since_seen": _seconds_since(device["last_seen_at"]),
            "screen": f"{device['lcd_cols']}x{device['lcd_rows']}",
            "is_active": bool(device["is_active"]),
        },
        "students": [
            {
                "external_id": r["external_id"],
                "display_name": r["display_name"],
                "avatar": r["avatar"],
                "avatar_color": r["avatar_color"],
                "support_profile": r["support_profile"],
                "support_notes": r["support_notes"],
                "drift_threshold_ms": r["drift_threshold_ms"],
                "has_password": r["picture_password_hash"] is not None,
            }
            for r in conn.execute(
                """
                SELECT s.* FROM students s
                JOIN class_students cs ON cs.student_id = s.id
                WHERE cs.class_id = ? AND s.is_active = 1
                ORDER BY s.display_name
                """,
                (klass["class_id"],),
            )
        ],
    }


class ClassPatch(BaseModel):
    short_name: str | None = Field(None, max_length=20)


@router.put("/classes/{class_id}/settings", summary="Update class settings")
def put_settings(
    payload: ClassPatch,
    klass: sqlite3.Row = Depends(get_teacher_class),
    conn: sqlite3.Connection = Depends(db_dependency),
) -> dict:
    """
    `short_name` is what the classroom device shows. It is capped at 20
    characters because that is the width of the screen it has to fit on, and a
    name that overflows there is silently truncated mid-word.
    """
    if payload.short_name is None:
        raise HTTPException(status_code=400, detail="Nothing to change")
    conn.execute("UPDATE classes SET short_name = ? WHERE id = ?",
                 (payload.short_name.strip(), klass["class_id"]))
    return {"class_id": klass["class_id"], "short_name": payload.short_name.strip()}
