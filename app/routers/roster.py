"""
The class list, for the classroom camera.

    GET /students

One endpoint, and it exists for one caller: the CV rig reads it once at
startup so it knows which children it may publish flags for and how long each
of them is allowed to drift before it says anything.

-----------------------------------------------------------------------------
WHY THIS IS NOT BEHIND A LOGIN
-----------------------------------------------------------------------------
The camera runs headless on a machine in the corner of a classroom. Giving it
an account means putting a credential in a config file on a machine nobody is
watching, which is worse security than what this endpoint actually exposes:
first names, a year group, and a number of milliseconds. No progress, no
mastery, no assessment data, no contact details, nothing a parent or a teacher
would consider private.

It is also only reachable on the classroom's own router, which is where the
real boundary is. `docs/CV_INTEGRATION.md` promised this endpoint from the
start and told the CV owner to read `drift_threshold_ms` from it.

-----------------------------------------------------------------------------
WHY drift_threshold_ms IS PER CHILD AND NOT A CONSTANT
-----------------------------------------------------------------------------
This is the whole reason the endpoint returns a number instead of the CV rig
hardcoding one. A child with autism may look away from a speaker constantly as
self-regulation; flagging them every three seconds for stimming is precisely
the harm this project exists to avoid. Their threshold is set longer, by a
human who knows them, and the camera obeys it.
"""

import sqlite3

from fastapi import APIRouter, Depends, Query

from app.db import db_dependency

router = APIRouter(tags=["roster"])


@router.get("/students", summary="The class list the classroom camera reads")
def list_students(
    active_only: bool = Query(True, description="Skip children marked inactive."),
    conn: sqlite3.Connection = Depends(db_dependency),
) -> dict:
    """
    Every child the camera may publish a flag for.

    Read this once at startup and cache it. Nothing here changes during a
    lesson, and re-reading it every frame would put an HTTP call inside the
    detection loop, which is the one place it must never be.
    """
    sql = (
        "SELECT external_id, display_name, grade, drift_threshold_ms, is_active "
        "FROM students"
    )
    if active_only:
        sql += " WHERE is_active = 1"
    sql += " ORDER BY display_name"

    rows = conn.execute(sql).fetchall()

    return {
        "students": [
            {
                "external_id": r["external_id"],
                "display_name": r["display_name"],
                "grade": r["grade"],
                # How long this particular child may drift before the camera
                # says anything. Set by a person, not by the model.
                "drift_threshold_ms": r["drift_threshold_ms"],
                "is_active": bool(r["is_active"]),
            }
            for r in rows
        ],
        "count": len(rows),
    }


@router.get("/topics/codes", summary="Lesson codes, for the camera's config")
def list_topic_codes(
    conn: sqlite3.Connection = Depends(db_dependency),
) -> dict:
    """
    Every lesson code a flag may name.

    The CV rig puts one of these in its config so a flag records what the
    child was drifting away from. Without it the home robot learns that a
    child struggled and not what with, and the loop stops one step short.
    """
    rows = conn.execute(
        """
        SELECT t.code, t.title, t.subject, t.grade
        FROM topics t
        WHERE t.code IS NOT NULL
        ORDER BY t.subject, t.sort_order
        """
    ).fetchall()

    return {
        "topics": [
            {
                "code": r["code"],
                "title": r["title"],
                "subject": r["subject"],
                "grade": r["grade"],
            }
            for r in rows
        ],
        "count": len(rows),
    }
