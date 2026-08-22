"""
The Progress screen: subject bars, weekly activity, time spent, skills, goals.

Everything here is derived from `activity_log` and `mastery`, so it can never
disagree with the star counter on the home screen — they read the same rows.
"""

import sqlite3
from datetime import date, datetime, timedelta

from fastapi import APIRouter, Depends

from app import economy
from app.db import db_dependency
from app.deps import get_student

router = APIRouter(prefix="/api/students/{student_ext_id}", tags=["progress"])

DAY_LABELS = ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"]


def _week_start(today: date | None = None) -> date:
    today = today or date.today()
    return today - timedelta(days=today.weekday())


@router.get("/progress", summary="Full progress dashboard")
def get_progress(
    student: sqlite3.Row = Depends(get_student),
    conn: sqlite3.Connection = Depends(db_dependency),
) -> dict:
    sid = student["id"]

    # --- Per-subject mastery -------------------------------------------------
    subjects = [
        {
            "code": r["subject_code"],
            "name": r["subject_name"],
            "icon": r["icon"],
            "color_from": r["color_from"],
            "color_to": r["color_to"],
            "progress_pct": int(r["progress_pct"]),
        }
        for r in conn.execute(
            "SELECT * FROM v_subject_progress WHERE student_id = ? ORDER BY sort_order",
            (sid,),
        )
    ]

    # --- This week's activity ------------------------------------------------
    monday = _week_start()
    daily = {
        r["activity_date"]: r for r in conn.execute(
            "SELECT * FROM v_daily_activity WHERE student_id = ? AND activity_date >= ?",
            (sid, monday.isoformat()),
        )
    }

    week = []
    max_seconds = 1
    for offset in range(7):
        day = monday + timedelta(days=offset)
        row = daily.get(day.isoformat())
        seconds = (row["seconds_spent"] or 0) if row else 0
        max_seconds = max(max_seconds, seconds)
        week.append({
            "label": DAY_LABELS[offset],
            "date": day.isoformat(),
            "events": (row["event_count"] if row else 0),
            "stars": (row["stars_earned"] or 0) if row else 0,
            "seconds": seconds,
            "is_today": day == date.today(),
            "is_future": day > date.today(),
        })
    for entry in week:
        entry["height_pct"] = round((entry["seconds"] / max_seconds) * 100)

    # --- Time spent ----------------------------------------------------------
    today_s = conn.execute(
        "SELECT COALESCE(SUM(duration_s),0) AS s FROM activity_log "
        "WHERE student_id = ? AND activity_date = ?",
        (sid, date.today().isoformat()),
    ).fetchone()["s"]
    week_s = conn.execute(
        "SELECT COALESCE(SUM(duration_s),0) AS s FROM activity_log "
        "WHERE student_id = ? AND activity_date >= ?",
        (sid, monday.isoformat()),
    ).fetchone()["s"]
    month_s = conn.execute(
        "SELECT COALESCE(SUM(duration_s),0) AS s FROM activity_log "
        "WHERE student_id = ? AND activity_date >= ?",
        (sid, (date.today() - timedelta(days=30)).isoformat()),
    ).fetchone()["s"]

    # --- Skills --------------------------------------------------------------
    skills = [
        {
            "code": r["code"], "name": r["name"], "icon": r["icon"],
            "level_pct": round((r["level"] or 0) * 100),
        }
        for r in conn.execute(
            """
            SELECT s.code, s.name, s.icon, COALESCE(ss.level, 0) AS level
            FROM skills s
            LEFT JOIN student_skills ss ON ss.skill_id = s.id AND ss.student_id = ?
            ORDER BY s.sort_order
            """,
            (sid,),
        )
    ]

    # --- Weekly goals --------------------------------------------------------
    goals = [
        {
            "label": r["label"],
            "current": r["current_count"],
            "target": r["target_count"],
            "done": r["current_count"] >= r["target_count"],
            "progress_pct": min(100, round((r["current_count"] / max(r["target_count"], 1)) * 100)),
        }
        for r in conn.execute(
            "SELECT * FROM weekly_goals WHERE student_id = ? AND week_start = ? "
            "ORDER BY sort_order",
            (sid, monday.isoformat()),
        )
    ]

    xp = conn.execute(
        "SELECT COALESCE(SUM(xp_delta),0) AS xp FROM activity_log WHERE student_id = ?",
        (sid,),
    ).fetchone()["xp"]
    level = economy.level_progress(xp)

    overall = round(sum(s["progress_pct"] for s in subjects) / len(subjects)) if subjects else 0

    # --- Coaching line -------------------------------------------------------
    ranked = sorted(subjects, key=lambda s: s["progress_pct"], reverse=True)
    if len(ranked) >= 2 and ranked[0]["progress_pct"] > 0:
        strongest = ranked[0]["name"]
        weakest = ranked[-1]["name"]
        message = (
            f"You're improving every day! {strongest} is your strongest subject. "
            f"Keep practising {weakest} to reach Level {level['level'] + 1}!"
        )
    else:
        message = "Start a lesson and I'll track how you're doing!"

    return {
        "level": level,
        "stars": student["stars"],
        "day_streak": student["day_streak"],
        "overall_progress_pct": overall,
        "subjects": subjects,
        "week": week,
        "time_spent": {
            "today_seconds": today_s,
            "week_seconds": week_s,
            "month_seconds": month_s,
            "today_label": _fmt_duration(today_s),
            "week_label": _fmt_duration(week_s),
            "month_label": _fmt_duration(month_s),
        },
        "skills": skills,
        "goals": goals,
        "message": message,
    }


def _fmt_duration(seconds: int) -> str:
    """Human-friendly duration: '1h 20m', '45m', '0m'."""
    if seconds <= 0:
        return "0m"
    hours, remainder = divmod(seconds, 3600)
    minutes = remainder // 60
    if hours and minutes:
        return f"{hours}h {minutes}m"
    if hours:
        return f"{hours}h"
    return f"{minutes}m"
