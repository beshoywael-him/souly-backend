"""
The parents' hub API.

    GET  /api/parent/children                          the switcher
    GET  /api/parent/children/{ext}/overview           Home, in ONE call
    GET  /api/parent/children/{ext}/progress           Progress
    GET  /api/parent/children/{ext}/subjects           Subjects
    GET  /api/parent/children/{ext}/subjects/{code}    one subject
    GET  /api/parent/children/{ext}/notes              Teacher Notes
    POST /api/parent/notes/{id}/read
    GET  /api/parent/children/{ext}/achievements       badges
    GET  /api/parent/children/{ext}/support            Support Profile
    PUT  /api/parent/children/{ext}/support
    GET  /api/parent/children/{ext}/teachers           who can be messaged
    GET  /api/parent/conversations                     Messages
    GET  /api/parent/conversations/{id}
    POST /api/parent/conversations/{id}/messages
    POST /api/parent/children/{ext}/conversations      start a thread

-----------------------------------------------------------------------------
TWO RULES THIS FILE FOLLOWS
-----------------------------------------------------------------------------
1. NOTHING HERE IMPORTS FROM A STUDENT ROUTER. The student app and the parents'
   hub meet at the database and at economy.py, nowhere else. That is what lets
   one squad refactor a screen the night before the competition without
   breaking the other squad's screen.

2. NOTHING HERE WRITES TO A CHILD'S TOTALS. A parent can mark a note read, send
   a message, and change accessibility settings. Stars, XP, levels and mastery
   are economy.py's alone. A parent portal that could move a score is a parent
   portal that can disagree with the student's own screen.

-----------------------------------------------------------------------------
ON EMPTY STATES — read before "fixing" a zero
-----------------------------------------------------------------------------
Aziz has never opened a lesson. Atef has six activity rows. The honest answer
to "what is his average?" for both of them is "we don't know yet", and every
payload here carries a `has_data` flag so the interface can say so rather than
printing a confident 0%.

A parent who is shown 0% for a child who simply has not started will conclude
either that their child is failing or that the system is broken. Both are
worse than an empty state that says "nothing yet — he starts this week".
-----------------------------------------------------------------------------
"""

import sqlite3
from datetime import date, datetime, timedelta

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field

from app.db import db_dependency
from app.models import utc_now_iso
from app.routers.parent_auth import (
    get_current_parent,
    get_parent_child,
    list_children,
)

router = APIRouter(prefix="/api/parent", tags=["parent"])

DAY_LABELS = ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"]

# What a row in activity_log should read as on a parent's screen. Deliberately
# plain: "Worked through a lesson page", not "lesson_step".
ACTIVITY_LABELS = {
    "lesson_step":    ("📄", "Worked through a lesson page"),
    "lesson_complete": ("✅", "Finished a lesson"),
    "quiz_answer":    ("✏️", "Answered a quiz question"),
    "quiz_complete":  ("🎯", "Completed a quiz"),
    "game_play":      ("🎮", "Played a practice game"),
    "chat_question":  ("💬", "Asked Souly a question"),
    "badge_unlock":   ("🏅", "Earned a badge"),
    "reward_unlock":  ("🎁", "Unlocked a reward"),
    "daily_challenge": ("⭐", "Finished the daily challenge"),
}

# The settings a parent is allowed to change. Everything else on
# student_settings belongs to the child or the teacher. An allowlist rather
# than a blocklist, so adding a column to the table cannot silently hand the
# parent control of it.
PARENT_EDITABLE_SETTINGS = {
    "read_aloud", "high_contrast", "larger_buttons", "reduce_motion",
    "closed_captions", "font_size", "voice_volume", "theme",
}


# =============================================================================
# Helpers
# =============================================================================

def _week_start(weeks_ago: int = 0) -> date:
    today = date.today()
    return today - timedelta(days=today.weekday()) - timedelta(weeks=weeks_ago)


def _fmt_duration(seconds: int | None) -> str:
    seconds = int(seconds or 0)
    if seconds < 60:
        return f"{seconds}s"
    hours, rem = divmod(seconds, 3600)
    minutes = rem // 60
    return f"{hours}h {minutes:02d}m" if hours else f"{minutes}m"


def _seconds_between(conn, sid: int, start: date, end: date | None = None) -> int:
    """
    Total time on task. Reads BOTH activity_log and page_activity.

    activity_log.duration_s is what economy.py records against a rewarded
    event; page_activity.seconds_on_page is measured by the lesson screen and
    includes time on pages that earned nothing. A parent asking "how long was
    he working?" means the second one too, so both are counted. They cover
    different events, so this adds rather than double-counts.
    """
    end = end or (date.today() + timedelta(days=1))
    a = conn.execute(
        "SELECT COALESCE(SUM(duration_s),0) FROM activity_log "
        "WHERE student_id=? AND activity_date >= ? AND activity_date < ?",
        (sid, start.isoformat(), end.isoformat()),
    ).fetchone()[0]
    b = conn.execute(
        "SELECT COALESCE(SUM(seconds_on_page),0) FROM page_activity "
        "WHERE student_id=? AND DATE(created_at) >= ? AND DATE(created_at) < ?",
        (sid, start.isoformat(), end.isoformat()),
    ).fetchone()[0]
    return int(a) + int(b)


def _week_days(conn, sid: int, monday: date) -> list[dict]:
    rows = {
        r["activity_date"]: r
        for r in conn.execute(
            "SELECT * FROM v_daily_activity WHERE student_id=? AND activity_date >= ? "
            "AND activity_date < ?",
            (sid, monday.isoformat(), (monday + timedelta(days=7)).isoformat()),
        )
    }
    pages = {
        r["d"]: r["s"]
        for r in conn.execute(
            "SELECT DATE(created_at) AS d, SUM(seconds_on_page) AS s "
            "FROM page_activity WHERE student_id=? AND DATE(created_at) >= ? "
            "AND DATE(created_at) < ? GROUP BY 1",
            (sid, monday.isoformat(), (monday + timedelta(days=7)).isoformat()),
        )
    }

    out = []
    for i in range(7):
        day = monday + timedelta(days=i)
        key = day.isoformat()
        row = rows.get(key)
        seconds = int((row["seconds_spent"] or 0) if row else 0) + int(pages.get(key, 0))
        out.append({
            "label": DAY_LABELS[i],
            "date": key,
            "events": int(row["event_count"]) if row else 0,
            "stars": int(row["stars_earned"] or 0) if row else 0,
            "seconds": seconds,
            "minutes": round(seconds / 60),
            "is_today": day == date.today(),
            "is_future": day > date.today(),
        })
    return out


def _subjects(conn, sid: int) -> list[dict]:
    """
    Per-subject progress, plus the honest denominator.

    `progress_pct` comes from v_subject_progress, which averages mastery
    levels. `topics_started` is how many lessons the child has actually
    touched. The second number is what makes the first one meaningful: 0%
    across 12 untouched topics is not the same as 0% across 12 attempted ones,
    and the interface must be able to tell them apart.
    """
    time_by_subject = {
        r["subject_id"]: r["s"]
        for r in conn.execute(
            "SELECT subject_id, SUM(duration_s) AS s FROM activity_log "
            "WHERE student_id=? AND subject_id IS NOT NULL GROUP BY 1",
            (sid,),
        )
    }
    out = []
    for r in conn.execute(
        "SELECT * FROM v_subject_progress WHERE student_id=? ORDER BY sort_order",
        (sid,),
    ):
        seconds = int(time_by_subject.get(r["subject_id"], 0) or 0)
        out.append({
            "code": r["subject_code"],
            "name": r["subject_name"],
            "icon": r["icon"],
            "color_from": r["color_from"],
            "color_to": r["color_to"],
            "progress_pct": int(r["progress_pct"] or 0),
            "topic_count": int(r["topic_count"] or 0),
            "topics_started": int(r["topics_started"] or 0),
            "seconds": seconds,
            "time_label": _fmt_duration(seconds),
            "has_data": int(r["topics_started"] or 0) > 0,
        })
    return out


def _recent_activity(conn, sid: int, limit: int = 8) -> list[dict]:
    out = []
    for r in conn.execute(
        "SELECT a.*, s.name AS subject_name FROM activity_log a "
        "LEFT JOIN subjects s ON s.id = a.subject_id "
        "WHERE a.student_id=? ORDER BY a.occurred_at DESC LIMIT ?",
        (sid, limit),
    ):
        icon, label = ACTIVITY_LABELS.get(
            r["activity_type"], ("•", r["activity_type"].replace("_", " ").title())
        )
        out.append({
            "icon": icon,
            "label": label,
            "subject": r["subject_name"],
            "stars": int(r["stars_delta"] or 0),
            "seconds": int(r["duration_s"] or 0),
            "occurred_at": r["occurred_at"],
            "date": r["activity_date"],
        })
    return out


def _independence(conn, sid: int) -> dict:
    """
    How much help the child needed — the measure this hub exists to show.

    A star count says a child did the work. It does not say whether they did
    it alone. hint_requests records every time Souly was asked for a nudge, a
    simpler explanation, or a worked example, so the trend across two weeks is
    a real signal about growing independence — and it is the number a parent
    of a child with a learning difference actually wants.

    Falling is good here. The interface must say so, because a rising bar
    coloured green is the default assumption and it would be backwards.
    """
    this_monday = _week_start()
    last_monday = _week_start(1)

    def count(since: date, until: date) -> int:
        return int(conn.execute(
            "SELECT COUNT(*) FROM hint_requests WHERE student_id=? "
            "AND DATE(created_at) >= ? AND DATE(created_at) < ?",
            (sid, since.isoformat(), until.isoformat()),
        ).fetchone()[0])

    this_week = count(this_monday, this_monday + timedelta(days=7))
    last_week = count(last_monday, this_monday)

    # Did the child work at all last week? If not, "12 requests, up from 0" is
    # a lie dressed as a statistic — it says the child needed more help when
    # what actually happened is that last week did not exist. Comparisons are
    # only offered when there is something to compare against.
    worked_last_week = int(conn.execute(
        "SELECT COUNT(*) FROM activity_log WHERE student_id=? "
        "AND activity_date >= ? AND activity_date < ?",
        (sid, last_monday.isoformat(), this_monday.isoformat()),
    ).fetchone()[0]) > 0

    by_type = {
        r["help_type"]: r["n"]
        for r in conn.execute(
            "SELECT help_type, COUNT(*) AS n FROM hint_requests "
            "WHERE student_id=? GROUP BY 1 ORDER BY n DESC",
            (sid,),
        )
    }
    # Visits, not distinct pages: going back to the same page twice is two
    # chances to ask for help, so it is two rows in the denominator.
    pages = int(conn.execute(
        "SELECT COUNT(*) FROM page_activity WHERE student_id=?", (sid,)
    ).fetchone()[0])
    unaided = int(conn.execute(
        "SELECT COUNT(*) FROM page_activity WHERE student_id=? AND help_requests = 0",
        (sid,),
    ).fetchone()[0])

    return {
        "help_this_week": this_week,
        "help_last_week": last_week,
        "change": this_week - last_week,
        # Less help than last week is progress. Named so nobody has to work
        # out the sign convention at the call site — and only set when the
        # comparison means anything.
        "comparable": worked_last_week,
        "improving": (this_week < last_week) if worked_last_week else None,
        "by_type": by_type,
        "page_visits": pages,
        "pages_unaided": unaided,
        "unaided_pct": round(unaided * 100 / pages) if pages else None,
        "has_data": pages > 0 or bool(by_type),
    }


def _notes(conn, sid: int, limit: int | None = None) -> list[dict]:
    sql = (
        "SELECT n.*, t.full_name AS teacher_name, t.title AS teacher_title, "
        "       t.initials, t.avatar_color, s.name AS subject_name, s.icon AS subject_icon "
        "FROM teacher_notes n "
        "JOIN teachers t ON t.id = n.teacher_id "
        "LEFT JOIN subjects s ON s.id = n.subject_id "
        "WHERE n.student_id = ? ORDER BY n.created_at DESC"
    )
    params: tuple = (sid,)
    if limit:
        sql += " LIMIT ?"
        params = (sid, limit)

    return [
        {
            "id": r["id"],
            "tone": r["tone"],
            "body": r["body"],
            "teacher": r["teacher_name"],
            "teacher_title": r["teacher_title"],
            "initials": r["initials"],
            "avatar_color": r["avatar_color"],
            "subject": r["subject_name"],
            "subject_icon": r["subject_icon"],
            "created_at": r["created_at"],
            "read": r["read_at"] is not None,
        }
        for r in conn.execute(sql, params)
    ]


def _child_card(child: sqlite3.Row) -> dict:
    return {
        "external_id": child["external_id"],
        "display_name": child["display_name"],
        "full_name": child["full_name"],
        "grade": child["grade"],
        "avatar": child["avatar"],
        "avatar_color": child["avatar_color"],
        "support_profile": child["support_profile"],
        "support_notes": child["support_notes"],
        "stars": child["stars"],
        "level": child["level"],
        "day_streak": child["day_streak"],
        "last_active_date": child["last_active_date"],
        "onboarded": child["onboarded_at"] is not None,
    }


# =============================================================================
# The switcher
# =============================================================================

@router.get("/children", summary="Every child this parent is linked to")
def children(
    parent: sqlite3.Row = Depends(get_current_parent),
    conn: sqlite3.Connection = Depends(db_dependency),
) -> dict:
    kids = list_children(conn, parent["id"])
    return {
        "children": kids,
        # The hub renders a switcher when this is true and a plain header when
        # it is false. Sending the flag rather than making the frontend do
        # `length > 1` means the rule lives in one place if it ever changes
        # (a parent with one child today may have two next year).
        "multiple": len(kids) > 1,
    }


# =============================================================================
# Home — one request
# =============================================================================

@router.get("/children/{student_ext_id}/overview", summary="Everything Home needs")
def overview(
    child: sqlite3.Row = Depends(get_parent_child),
    conn: sqlite3.Connection = Depends(db_dependency),
) -> dict:
    """
    One call, not seven — same reason as the student app's /home.

    The hub runs on a parent's phone over the same MiFi the robot and the
    camera are using. Every extra round trip is another second of spinner
    while a judge watches.
    """
    sid = child["student_id"]
    this_monday = _week_start()
    last_monday = _week_start(1)

    week_seconds = _seconds_between(conn, sid, this_monday)
    prev_seconds = _seconds_between(conn, sid, last_monday, this_monday)

    subjects = _subjects(conn, sid)
    active = [s for s in subjects if s["has_data"]]
    overall = round(sum(s["progress_pct"] for s in active) / len(active)) if active else None

    pages_worked = int(conn.execute(
        "SELECT COUNT(DISTINCT page_id) FROM page_activity WHERE student_id=?", (sid,)
    ).fetchone()[0])
    lessons_done = int(conn.execute(
        "SELECT COUNT(*) FROM activity_log WHERE student_id=? "
        "AND activity_type='lesson_complete'", (sid,)
    ).fetchone()[0])
    badges = int(conn.execute(
        "SELECT COUNT(*) FROM student_badges WHERE student_id=?", (sid,)
    ).fetchone()[0])
    questions_asked = int(conn.execute(
        "SELECT COUNT(*) FROM activity_log WHERE student_id=? "
        "AND activity_type='chat_question'", (sid,)
    ).fetchone()[0])

    return {
        "child": _child_card(child),
        "stats": {
            "overall_pct": overall,
            "has_scores": overall is not None,
            "subjects_started": len(active),
            "subjects_total": len(subjects),
            "pages_worked": pages_worked,
            "lessons_completed": lessons_done,
            "badges": badges,
            "questions_asked": questions_asked,
            "stars": child["stars"],
            "level": child["level"],
            "day_streak": child["day_streak"],
            "week_seconds": week_seconds,
            "week_time": _fmt_duration(week_seconds),
            "prev_week_seconds": prev_seconds,
            "week_change": week_seconds - prev_seconds,
            "week_change_label": ("+" if week_seconds >= prev_seconds else "−")
                                 + _fmt_duration(abs(week_seconds - prev_seconds)),
        },
        "week": _week_days(conn, sid, this_monday),
        "subjects": subjects,
        "independence": _independence(conn, sid),
        "recent": _recent_activity(conn, sid, 6),
        "notes": _notes(conn, sid, 3),
        "unread_notes": int(conn.execute(
            "SELECT COUNT(*) FROM teacher_notes WHERE student_id=? AND read_at IS NULL",
            (sid,),
        ).fetchone()[0]),
        # A child who has done literally nothing gets a different Home screen,
        # not a Home screen full of zeroes.
        "started": pages_worked > 0 or child["stars"] > 0,
    }


# =============================================================================
# Progress
# =============================================================================

@router.get("/children/{student_ext_id}/progress", summary="The Progress screen")
def progress(
    child: sqlite3.Row = Depends(get_parent_child),
    conn: sqlite3.Connection = Depends(db_dependency),
) -> dict:
    sid = child["student_id"]

    # Four weeks of time-on-task. Time, not score: with mastery this sparse a
    # score trend would be four points of noise, and a parent can read
    # "he worked more this week" without a statistics lesson.
    trend = []
    for weeks_ago in range(3, -1, -1):
        monday = _week_start(weeks_ago)
        seconds = _seconds_between(conn, sid, monday, monday + timedelta(days=7))
        trend.append({
            "label": monday.strftime("%d %b"),
            "week_start": monday.isoformat(),
            "seconds": seconds,
            "minutes": round(seconds / 60),
        })

    topics = [
        {
            "title": r["title"],
            "subject": r["subject_name"],
            "icon": r["icon"],
            "level_pct": round((r["level"] or 0) * 100),
            "attempts": r["attempts"],
            "correct": r["correct"],
            "best_streak": r["best_streak"],
            "last_practiced_at": r["last_practiced_at"],
        }
        for r in conn.execute(
            "SELECT m.*, t.title, s.name AS subject_name, s.icon "
            "FROM mastery m JOIN topics t ON t.id = m.topic_id "
            "LEFT JOIN subjects s ON s.id = t.subject_id "
            "WHERE m.student_id=? ORDER BY m.updated_at DESC",
            (sid,),
        )
    ]

    return {
        "child": _child_card(child),
        "week": _week_days(conn, sid, _week_start()),
        "trend": trend,
        "subjects": _subjects(conn, sid),
        "topics": topics,
        "independence": _independence(conn, sid),
        "recent": _recent_activity(conn, sid, 15),
        "total_time": _fmt_duration(
            _seconds_between(conn, sid, date(2000, 1, 1))
        ),
    }


# =============================================================================
# Subjects
# =============================================================================

@router.get("/children/{student_ext_id}/subjects", summary="All subjects")
def subjects(
    child: sqlite3.Row = Depends(get_parent_child),
    conn: sqlite3.Connection = Depends(db_dependency),
) -> dict:
    return {"child": _child_card(child), "subjects": _subjects(conn, child["student_id"])}


@router.get("/children/{student_ext_id}/subjects/{subject_code}", summary="One subject")
def subject_detail(
    subject_code: str,
    child: sqlite3.Row = Depends(get_parent_child),
    conn: sqlite3.Connection = Depends(db_dependency),
) -> dict:
    sid = child["student_id"]
    subject = conn.execute(
        "SELECT * FROM subjects WHERE code = ? AND is_active = 1",
        (subject_code.upper(),),
    ).fetchone()
    if subject is None:
        raise HTTPException(status_code=404, detail="No such subject")

    lessons = [
        {
            "title": r["title"],
            "code": r["code"],
            "lesson_label": r["lesson_label"],
            "grade": r["grade"],
            "level_pct": round((r["level"] or 0) * 100) if r["level"] is not None else None,
            "attempts": r["attempts"] or 0,
            "correct": r["correct"] or 0,
            "last_practiced_at": r["last_practiced_at"],
            "started": r["level"] is not None,
        }
        for r in conn.execute(
            "SELECT t.*, m.level, m.attempts, m.correct, m.last_practiced_at "
            "FROM topics t LEFT JOIN mastery m "
            "  ON m.topic_id = t.id AND m.student_id = ? "
            "WHERE t.subject_id = ? ORDER BY t.sort_order, t.id",
            (sid, subject["id"]),
        )
    ]

    seconds = int(conn.execute(
        "SELECT COALESCE(SUM(duration_s),0) FROM activity_log "
        "WHERE student_id=? AND subject_id=?",
        (sid, subject["id"]),
    ).fetchone()[0])

    started = [lesson for lesson in lessons if lesson["started"]]
    notes = [n for n in _notes(conn, sid) if n["subject"] == subject["name"]]

    return {
        "child": _child_card(child),
        "subject": {
            "code": subject["code"],
            "name": subject["name"],
            "icon": subject["icon"],
            "color_from": subject["color_from"],
            "color_to": subject["color_to"],
        },
        "progress_pct": round(sum(le["level_pct"] for le in started) / len(started))
                        if started else None,
        "lessons": lessons,
        "lessons_started": len(started),
        "lessons_total": len(lessons),
        "time_label": _fmt_duration(seconds),
        "seconds": seconds,
        "notes": notes,
        "has_data": bool(started),
    }


# =============================================================================
# Teacher notes
# =============================================================================

@router.get("/children/{student_ext_id}/notes", summary="Teacher notes")
def notes(
    child: sqlite3.Row = Depends(get_parent_child),
    conn: sqlite3.Connection = Depends(db_dependency),
) -> dict:
    items = _notes(conn, child["student_id"])
    return {
        "child": _child_card(child),
        "notes": items,
        "unread": sum(1 for n in items if not n["read"]),
        "counts": {
            tone: sum(1 for n in items if n["tone"] == tone)
            for tone in ("praise", "progress", "concern")
        },
    }


class NoteRead(BaseModel):
    note_id: int


@router.post("/notes/{note_id}/read", summary="Mark a note read")
def mark_note_read(
    note_id: int,
    parent: sqlite3.Row = Depends(get_current_parent),
    conn: sqlite3.Connection = Depends(db_dependency),
) -> dict:
    # The note is reached through the parent's own children, not by id alone.
    # Without this join, note 41 could be marked read by anyone holding any
    # parent token — and the teacher would be told the wrong parent saw it.
    row = conn.execute(
        "SELECT n.id FROM teacher_notes n "
        "JOIN v_parent_children c ON c.student_id = n.student_id "
        "WHERE n.id = ? AND c.parent_id = ?",
        (note_id, parent["id"]),
    ).fetchone()
    if row is None:
        raise HTTPException(status_code=404, detail="No such note")

    conn.execute(
        "UPDATE teacher_notes SET read_at = ? WHERE id = ? AND read_at IS NULL",
        (utc_now_iso(), note_id),
    )
    return {"read": True}


# =============================================================================
# Achievements
# =============================================================================

@router.get("/children/{student_ext_id}/achievements", summary="Badges")
def achievements(
    child: sqlite3.Row = Depends(get_parent_child),
    conn: sqlite3.Connection = Depends(db_dependency),
) -> dict:
    rows = conn.execute(
        "SELECT b.*, sb.unlocked_at FROM badges b "
        "LEFT JOIN student_badges sb ON sb.badge_id = b.id AND sb.student_id = ? "
        "ORDER BY b.sort_order, b.id",
        (child["student_id"],),
    ).fetchall()

    badges = [
        {
            "code": r["code"],
            "name": r["name"],
            "description": r["description"],
            "icon": r["icon"],
            "tier": r["tier"],
            "earned": r["unlocked_at"] is not None,
            "unlocked_at": r["unlocked_at"],
        }
        for r in rows
    ]
    return {
        "child": _child_card(child),
        "badges": badges,
        "earned": sum(1 for b in badges if b["earned"]),
        "total": len(badges),
    }


# =============================================================================
# Support profile — the tab that matters most for these children
# =============================================================================

@router.get("/children/{student_ext_id}/support", summary="Support profile")
def support(
    child: sqlite3.Row = Depends(get_parent_child),
    conn: sqlite3.Connection = Depends(db_dependency),
) -> dict:
    """
    What Souly has worked out about this child, and what the parent can change.

    `confidence` is capped at 0.5 by the entry activity, on purpose: one short
    session under-reads this population badly. It is surfaced here, unrounded
    and labelled, because a parent being shown a confident-looking profile
    built from fifteen minutes of data is being misled.
    """
    sid = child["student_id"]
    profile = conn.execute(
        "SELECT * FROM v_current_learner_profile WHERE student_id = ?", (sid,)
    ).fetchone()

    settings_row = conn.execute(
        "SELECT * FROM student_settings WHERE student_id = ?", (sid,)
    ).fetchone()
    if settings_row is None:
        conn.execute("INSERT INTO student_settings (student_id) VALUES (?)", (sid,))
        settings_row = conn.execute(
            "SELECT * FROM student_settings WHERE student_id = ?", (sid,)
        ).fetchone()

    learner = None
    if profile is not None:
        learner = {
            "instruction_need": profile["instruction_need"],
            "confidence": profile["confidence"],
            "confidence_label": (
                "Early estimate" if (profile["confidence"] or 0) < 0.6 else "Established"
            ),
            "items_attempted": profile["items_attempted"],
            "items_solved_unaided": profile["items_solved_unaided"],
            "median_first_attempt_ms": profile["median_first_attempt_ms"],
            "modality_gap": profile["modality_gap"],
            "reading_correct": profile["reading_correct"],
            "listening_correct": profile["listening_correct"],
            "possible_masking": bool(profile["possible_masking"]),
            "interests": profile["interests"],
            "incomplete": bool(profile["incomplete"]),
            "created_at": profile["created_at"],
        }

    return {
        "child": _child_card(child),
        "support_profile": child["support_profile"],
        "support_notes": child["support_notes"],
        "drift_threshold_ms": child["drift_threshold_ms"],
        "learner_profile": learner,
        "settings": {k: settings_row[k] for k in PARENT_EDITABLE_SETTINGS},
        "editable": sorted(PARENT_EDITABLE_SETTINGS),
    }


class SupportUpdate(BaseModel):
    settings: dict = Field(default_factory=dict)


@router.put("/children/{student_ext_id}/support", summary="Update what a parent may change")
def update_support(
    payload: SupportUpdate,
    child: sqlite3.Row = Depends(get_parent_child),
    conn: sqlite3.Connection = Depends(db_dependency),
) -> dict:
    sid = child["student_id"]
    changes = {k: v for k, v in payload.settings.items() if k in PARENT_EDITABLE_SETTINGS}
    if not changes:
        raise HTTPException(status_code=422, detail="No editable settings in request")

    conn.execute("INSERT OR IGNORE INTO student_settings (student_id) VALUES (?)", (sid,))
    assignments = ", ".join(f"{k} = ?" for k in changes)
    conn.execute(
        f"UPDATE student_settings SET {assignments}, updated_at = ? WHERE student_id = ?",
        (*changes.values(), utc_now_iso(), sid),
    )
    # These are server-side settings, so the change follows the child to the
    # robot tablet and the classroom screen without anyone re-entering it.
    return {"updated": sorted(changes), "applies_everywhere": True}


# =============================================================================
# Messages
# =============================================================================

@router.get("/children/{student_ext_id}/teachers", summary="Teachers to message")
def teachers(
    child: sqlite3.Row = Depends(get_parent_child),
    conn: sqlite3.Connection = Depends(db_dependency),
) -> dict:
    return {
        "teachers": [
            {
                "id": r["id"],
                "full_name": r["full_name"],
                "title": r["title"],
                "initials": r["initials"],
                "avatar_color": r["avatar_color"],
                "subject": r["subject_name"],
                "is_homeroom": bool(r["is_homeroom"]),
            }
            for r in conn.execute(
                "SELECT t.*, s.name AS subject_name FROM teachers t "
                "LEFT JOIN subjects s ON s.id = t.subject_id "
                "WHERE t.is_active = 1 ORDER BY t.is_homeroom DESC, t.full_name"
            )
        ]
    }


@router.get("/conversations", summary="Message threads")
def conversations(
    parent: sqlite3.Row = Depends(get_current_parent),
    conn: sqlite3.Connection = Depends(db_dependency),
) -> dict:
    """
    Every thread, each tagged with which child it is about.

    Fayrouz may be talking to Ms. Sarah about Beshoy's attention and about
    Atef's reading in the same week. Both threads are with the same teacher.
    Without the child on the row, she cannot tell them apart in the list — so
    `child` is not decoration here, it is what makes the list usable.
    """
    rows = conn.execute(
        """
        SELECT cv.id, cv.student_id, cv.last_message_at,
               t.full_name AS teacher_name, t.title AS teacher_title,
               t.initials, t.avatar_color,
               s.display_name AS child_name, s.external_id AS child_ext_id,
               s.avatar AS child_avatar,
               (SELECT body FROM conversation_messages m
                 WHERE m.conversation_id = cv.id
                 ORDER BY m.created_at DESC LIMIT 1)            AS last_body,
               (SELECT COUNT(*) FROM conversation_messages m
                 WHERE m.conversation_id = cv.id
                   AND m.sender_role = 'teacher'
                   AND m.read_at IS NULL)                        AS unread
        FROM conversations cv
        JOIN teachers t ON t.id = cv.teacher_id
        JOIN students s ON s.id = cv.student_id
        WHERE cv.parent_id = ?
        ORDER BY COALESCE(cv.last_message_at, cv.created_at) DESC
        """,
        (parent["id"],),
    ).fetchall()

    return {
        "conversations": [
            {
                "id": r["id"],
                "teacher": r["teacher_name"],
                "teacher_title": r["teacher_title"],
                "initials": r["initials"],
                "avatar_color": r["avatar_color"],
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


def _own_conversation(conn, parent_id: int, conversation_id: int) -> sqlite3.Row:
    row = conn.execute(
        "SELECT cv.*, t.full_name AS teacher_name, t.title AS teacher_title, "
        "       t.initials, t.avatar_color, s.display_name AS child_name, "
        "       s.external_id AS child_ext_id, s.avatar AS child_avatar "
        "FROM conversations cv "
        "JOIN teachers t ON t.id = cv.teacher_id "
        "JOIN students s ON s.id = cv.student_id "
        "WHERE cv.id = ? AND cv.parent_id = ?",
        (conversation_id, parent_id),
    ).fetchone()
    if row is None:
        raise HTTPException(status_code=404, detail="No such conversation")
    return row


@router.get("/conversations/{conversation_id}", summary="One thread")
def conversation(
    conversation_id: int,
    parent: sqlite3.Row = Depends(get_current_parent),
    conn: sqlite3.Connection = Depends(db_dependency),
) -> dict:
    convo = _own_conversation(conn, parent["id"], conversation_id)

    messages = [
        {
            "id": r["id"],
            "from": r["sender_role"],
            "body": r["body"],
            "created_at": r["created_at"],
            "read": r["read_at"] is not None,
        }
        for r in conn.execute(
            "SELECT * FROM conversation_messages WHERE conversation_id = ? "
            "ORDER BY created_at, id",
            (conversation_id,),
        )
    ]

    # Opening the thread is what marks the teacher's side read.
    conn.execute(
        "UPDATE conversation_messages SET read_at = ? "
        "WHERE conversation_id = ? AND sender_role = 'teacher' AND read_at IS NULL",
        (utc_now_iso(), conversation_id),
    )

    return {
        "id": convo["id"],
        "teacher": convo["teacher_name"],
        "teacher_title": convo["teacher_title"],
        "initials": convo["initials"],
        "avatar_color": convo["avatar_color"],
        "child": convo["child_name"],
        "child_ext_id": convo["child_ext_id"],
        "child_avatar": convo["child_avatar"],
        "messages": messages,
    }


class NewMessage(BaseModel):
    body: str = Field(..., min_length=1, max_length=4000)


@router.post("/conversations/{conversation_id}/messages", summary="Send a message")
def send_message(
    conversation_id: int,
    payload: NewMessage,
    parent: sqlite3.Row = Depends(get_current_parent),
    conn: sqlite3.Connection = Depends(db_dependency),
) -> dict:
    _own_conversation(conn, parent["id"], conversation_id)

    body = payload.body.strip()
    if not body:
        raise HTTPException(status_code=422, detail="Empty message")

    now = utc_now_iso()
    cur = conn.execute(
        "INSERT INTO conversation_messages (conversation_id, sender_role, "
        "sender_id, body, created_at) VALUES (?, 'parent', ?, ?, ?)",
        (conversation_id, parent["id"], body, now),
    )
    conn.execute(
        "UPDATE conversations SET last_message_at = ? WHERE id = ?",
        (now, conversation_id),
    )
    return {"id": cur.lastrowid, "from": "parent", "body": body,
            "created_at": now, "read": False}


class NewConversation(BaseModel):
    teacher_id: int


@router.post("/children/{student_ext_id}/conversations", summary="Start a thread")
def start_conversation(
    payload: NewConversation,
    child: sqlite3.Row = Depends(get_parent_child),
    parent: sqlite3.Row = Depends(get_current_parent),
    conn: sqlite3.Connection = Depends(db_dependency),
) -> dict:
    teacher = conn.execute(
        "SELECT id FROM teachers WHERE id = ? AND is_active = 1", (payload.teacher_id,)
    ).fetchone()
    if teacher is None:
        raise HTTPException(status_code=404, detail="No such teacher")

    existing = conn.execute(
        "SELECT id FROM conversations WHERE parent_id=? AND teacher_id=? AND student_id=?",
        (parent["id"], payload.teacher_id, child["student_id"]),
    ).fetchone()
    if existing:
        return {"id": existing["id"], "created": False}

    cur = conn.execute(
        "INSERT INTO conversations (parent_id, teacher_id, student_id) VALUES (?,?,?)",
        (parent["id"], payload.teacher_id, child["student_id"]),
    )
    return {"id": cur.lastrowid, "created": True}
