"""Shared FastAPI dependencies."""

import sqlite3

from fastapi import Depends, HTTPException, Path

from app.db import db_dependency


def get_student(
    student_ext_id: str = Path(..., description="Student external_id, e.g. 'stu-01'"),
    conn: sqlite3.Connection = Depends(db_dependency),
) -> sqlite3.Row:
    """
    Resolve a student from the URL, or 404.

    Every student-scoped route depends on this, so an unknown or deactivated
    student fails identically everywhere instead of each endpoint inventing
    its own behaviour.
    """
    row = conn.execute(
        "SELECT * FROM students WHERE external_id = ?", (student_ext_id,)
    ).fetchone()
    if row is None:
        raise HTTPException(
            status_code=404,
            detail=f"No student with external_id '{student_ext_id}'",
        )
    if not row["is_active"]:
        raise HTTPException(status_code=409,
                            detail=f"Student '{student_ext_id}' is not active")
    return row


def ensure_settings(conn: sqlite3.Connection, student_id: int) -> sqlite3.Row:
    """Fetch a student's settings, creating the default row on first access."""
    row = conn.execute(
        "SELECT * FROM student_settings WHERE student_id = ?", (student_id,)
    ).fetchone()
    if row is None:
        conn.execute(
            "INSERT INTO student_settings (student_id) VALUES (?)", (student_id,)
        )
        row = conn.execute(
            "SELECT * FROM student_settings WHERE student_id = ?", (student_id,)
        ).fetchone()
    return row
