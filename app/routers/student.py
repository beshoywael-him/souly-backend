"""
Student profile, home dashboard, settings.

`GET /api/students/{id}/home` is the single call the home screen makes. One
round trip instead of seven matters here: the app runs on a tablet over a
MiFi router shared with a camera feed, and every extra request is another
chance to be waiting when a judge is watching.
"""

import sqlite3
from datetime import date, datetime, timezone

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field

from app import economy
from app.db import db_dependency
from app.deps import ensure_settings, get_student
from app.services import tutor

router = APIRouter(prefix="/api/students/{student_ext_id}", tags=["student"])


# =============================================================================
# Models
# =============================================================================

class SettingsUpdate(BaseModel):
    language: str | None = Field(None, pattern="^(en|ar|fr)$")
    voice_volume: int | None = Field(None, ge=0, le=100)
    theme: str | None = Field(None, pattern="^(light|purple|dark)$")
    font_size: str | None = Field(None, pattern="^(small|medium|large)$")

    read_aloud: bool | None = None
    high_contrast: bool | None = None
    larger_buttons: bool | None = None
    voice_commands: bool | None = None
    closed_captions: bool | None = None
    reduce_motion: bool | None = None

    mic_enabled: bool | None = None
    camera_enabled: bool | None = None
    speaker_enabled: bool | None = None
    led_enabled: bool | None = None
    face_expressions: bool | None = None


# =============================================================================
# Helpers
# =============================================================================

def _total_xp(conn: sqlite3.Connection, student_id: int) -> int:
    return conn.execute(
        "SELECT COALESCE(SUM(xp_delta),0) AS xp FROM activity_log WHERE student_id = ?",
        (student_id,),
    ).fetchone()["xp"]


def _weekly_progress_pct(conn: sqlite3.Connection, student_id: int) -> int:
    """
    Average mastery across every topic the student has touched.

    Deliberately mastery-based rather than "activities this week": a student
    who practises hard on a difficult topic should not see the number fall
    because they got answers wrong.
    """
    row = conn.execute(
        "SELECT COALESCE(AVG(level),0) AS avg_level FROM mastery WHERE student_id = ?",
        (student_id,),
    ).fetchone()
    return round((row["avg_level"] or 0) * 100)


def build_profile(conn: sqlite3.Connection, student: sqlite3.Row) -> dict:
    xp = _total_xp(conn, student["id"])
    progress = economy.level_progress(xp)

    counts = conn.execute(
        """
        SELECT
            (SELECT COUNT(*) FROM student_badges WHERE student_id = :sid)  AS badges,
            (SELECT COUNT(*) FROM lesson_progress
              WHERE student_id = :sid AND is_complete = 1)                 AS lessons,
            (SELECT COUNT(*) FROM quizzes
              WHERE student_id = :sid AND status = 'complete')             AS quizzes,
            (SELECT COUNT(*) FROM game_plays WHERE student_id = :sid)      AS games,
            (SELECT COUNT(*) FROM chat_messages
              WHERE student_id = :sid AND role = 'student')                AS questions
        """,
        {"sid": student["id"]},
    ).fetchone()

    return {
        "external_id": student["external_id"],
        "full_name": student["full_name"],
        "display_name": student["display_name"],
        "grade": student["grade"],
        "support_profile": student["support_profile"],
        "avatar_url": student["avatar_url"],
        "stars": student["stars"],
        "day_streak": student["day_streak"],
        "xp": xp,
        "level": progress["level"],
        "level_title": progress["title"],
        "level_progress_pct": progress["progress_pct"],
        "xp_to_next_level": progress["xp_to_next"],
        "next_level_xp": progress["next_level_xp"],
        "badges_earned": counts["badges"],
        "lessons_completed": counts["lessons"],
        "quizzes_completed": counts["quizzes"],
        "games_played": counts["games"],
        "questions_asked": counts["questions"],
        "overall_progress_pct": _weekly_progress_pct(conn, student["id"]),
    }


def _time_greeting() -> str:
    hour = datetime.now().hour
    if hour < 12:
        return "Good Morning"
    if hour < 17:
        return "Good Afternoon"
    return "Good Evening"


# =============================================================================
# Routes
# =============================================================================

@router.get("/profile", summary="Student profile and totals")
def get_profile(
    student: sqlite3.Row = Depends(get_student),
    conn: sqlite3.Connection = Depends(db_dependency),
) -> dict:
    return build_profile(conn, student)


@router.get("/home", summary="Everything the home screen needs, in one call")
def get_home(
    student: sqlite3.Row = Depends(get_student),
    conn: sqlite3.Connection = Depends(db_dependency),
) -> dict:
    sid = student["id"]
    profile = build_profile(conn, student)

    # --- Today's lesson: resume what's started, else start what's next -------
    #
    # Both queries are gated on the child's grade. A Primary 6 child must not
    # be handed a Primary 5 lesson to "carry on with" — and right now Primary
    # 6 has no books at all, so `todays_lesson` is None and the home screen
    # shows the honest empty state.
    grade = str(student["grade"] or "").strip()

    current = conn.execute(
        """
        SELECT t.id, t.title, t.code, t.image_url AS icon,
               COALESCE(sub.name, b.subject) AS subject_name,
               lp.pages_completed, lp.last_page,
               (SELECT COUNT(*) FROM curriculum_pages p
                 WHERE p.book_id = t.book_id AND p.lesson = t.lesson_label) AS total_pages
        FROM lesson_progress lp
        JOIN topics t            ON t.id = lp.topic_id
        JOIN curriculum_books b  ON b.id = t.book_id
        LEFT JOIN subjects sub   ON sub.id = t.subject_id
        WHERE lp.student_id = ? AND lp.is_complete = 0 AND lp.pages_completed > 0
          AND b.grade = ? AND b.is_verified = 1
        ORDER BY lp.updated_at DESC LIMIT 1
        """,
        (sid, grade),
    ).fetchone()

    if current is None:
        current = conn.execute(
            """
            SELECT t.id, t.title, t.code, t.image_url AS icon,
                   COALESCE(sub.name, b.subject) AS subject_name,
                   0 AS pages_completed, 0 AS last_page,
                   (SELECT COUNT(*) FROM curriculum_pages p
                     WHERE p.book_id = t.book_id AND p.lesson = t.lesson_label) AS total_pages
            FROM topics t
            JOIN curriculum_books b ON b.id = t.book_id
            LEFT JOIN subjects sub  ON sub.id = t.subject_id
            LEFT JOIN lesson_progress lp
                   ON lp.topic_id = t.id AND lp.student_id = ?
            WHERE COALESCE(lp.is_complete, 0) = 0
              AND b.grade = ? AND b.is_verified = 1 AND t.is_verified = 1
            ORDER BY b.id, t.sort_order LIMIT 1
            """,
            (sid, grade),
        ).fetchone()

    todays_lesson = None
    if current and current["total_pages"]:
        total = current["total_pages"]
        todays_lesson = {
            "lesson_id": current["id"],
            "topic_id": current["id"],
            "code": current["code"],
            "title": current["title"],
            "icon": current["icon"],
            "topic": current["title"],
            "subject": current["subject_name"],
            "pages_completed": current["pages_completed"],
            "total_pages": total,
            # The printed page to open at, not an ordinal — so "carry on where
            # you left off" survives the map being re-ingested.
            "resume_page": current["last_page"] or None,
            "progress_pct": round((current["pages_completed"] / total) * 100),
        }

    # --- Today's schedule ----------------------------------------------------
    weekday = date.today().weekday()
    schedule = [
        {
            "time": r["start_time"],
            "label": r["label"],
            "icon": r["icon"],
            "subject_id": r["subject_id"],
        }
        for r in conn.execute(
            "SELECT * FROM schedule_items WHERE student_id = ? AND day_of_week = ? "
            "ORDER BY start_time",
            (sid, weekday),
        )
    ]

    # --- Daily challenge -----------------------------------------------------
    challenge_row = economy.get_or_create_challenge(conn, sid)
    challenge = {
        "lesson_done": bool(challenge_row["lesson_done"]),
        "quiz_done": bool(challenge_row["quiz_done"]),
        "game_done": bool(challenge_row["game_done"]),
        "all_done": bool(challenge_row["lesson_done"] and challenge_row["quiz_done"]
                         and challenge_row["game_done"]),
        "reward_claimed": bool(challenge_row["reward_claimed"]),
        "reward_stars": challenge_row["reward_stars"],
    }

    # --- Pending attention flags --------------------------------------------
    # The robot screen reacts to these: the classroom camera noticed the
    # student drifting, so Souly opens with a gentle check-in rather than
    # ploughing on with the lesson.
    pending_flags = conn.execute(
        "SELECT COUNT(*) AS n FROM flags WHERE student_id = ? AND status IN "
        "('pending','approved')",
        (sid,),
    ).fetchone()["n"]

    return {
        "profile": profile,
        "greeting": f"{_time_greeting()}, {student['display_name']}!",
        "greeting_sub": "Ready to learn something new today?",
        "souly_message": tutor.greeting(conn, sid),
        "todays_lesson": todays_lesson,
        "schedule": schedule,
        "daily_challenge": challenge,
        "weekly_progress_pct": profile["overall_progress_pct"],
        "next_level": profile["level"] + 1,
        "pending_flags": pending_flags,
        "settings": dict(ensure_settings(conn, sid)),
    }


@router.get("/settings", summary="Read settings")
def get_settings(
    student: sqlite3.Row = Depends(get_student),
    conn: sqlite3.Connection = Depends(db_dependency),
) -> dict:
    return dict(ensure_settings(conn, student["id"]))


@router.put("/settings", summary="Update settings")
def update_settings(
    payload: SettingsUpdate,
    student: sqlite3.Row = Depends(get_student),
    conn: sqlite3.Connection = Depends(db_dependency),
) -> dict:
    """
    Partial update — only the fields you send change.

    Settings live server-side rather than in browser storage because for this
    user group they are accessibility requirements, not preferences: a student
    who needs larger buttons needs them on every device, including the shared
    classroom screen they've never opened before.
    """
    ensure_settings(conn, student["id"])

    fields = payload.model_dump(exclude_none=True)
    if not fields:
        return dict(ensure_settings(conn, student["id"]))

    values = [int(v) if isinstance(v, bool) else v for v in fields.values()]
    assignments = ", ".join(f"{k} = ?" for k in fields)

    conn.execute(
        f"UPDATE student_settings SET {assignments}, updated_at = ? WHERE student_id = ?",
        (*values, economy.utc_now(), student["id"]),
    )
    return dict(ensure_settings(conn, student["id"]))


@router.get("/activity", summary="Recent activity feed")
def get_activity(
    limit: int = 20,
    student: sqlite3.Row = Depends(get_student),
    conn: sqlite3.Connection = Depends(db_dependency),
) -> list[dict]:
    rows = conn.execute(
        """
        SELECT activity_type, stars_delta, xp_delta, detail, occurred_at
        FROM activity_log WHERE student_id = ?
        ORDER BY id DESC LIMIT ?
        """,
        (student["id"], min(limit, 100)),
    ).fetchall()
    return [dict(r) for r in rows]
