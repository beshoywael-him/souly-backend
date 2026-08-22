"""
Schema-level tests.

These check the guarantees the database itself must provide, independent of
the API — most importantly the parent access rule, which is a data-layer
promise and needs to be true before any parent portal code exists.
"""

import sqlite3

import pytest

from app.db import get_conn
from app.models import utc_now_iso
from app.security import generate_access_code, hash_secret, verify_secret


def test_foreign_keys_are_enforced(client):
    """
    SQLite silently ignores foreign keys unless the pragma is on. If this
    test ever fails, flags can reference students that don't exist.
    """
    with pytest.raises(sqlite3.IntegrityError):
        with get_conn() as conn:
            conn.execute(
                "INSERT INTO flags (student_id, flag_type, detected_at) "
                "VALUES (999999, 'gaze_away', ?)",
                (utc_now_iso(),),
            )


def test_invalid_flag_status_is_rejected_by_check_constraint(client):
    """Defence in depth: even a raw SQL insert can't invent a sixth status."""
    with get_conn() as conn:
        student_id = conn.execute(
            "SELECT id FROM students WHERE external_id = 'stu-test'"
        ).fetchone()["id"]

    with pytest.raises(sqlite3.IntegrityError):
        with get_conn() as conn:
            conn.execute(
                "INSERT INTO flags (student_id, flag_type, status, detected_at) "
                "VALUES (?, 'gaze_away', 'kinda_pending', ?)",
                (student_id, utc_now_iso()),
            )


def test_mastery_level_must_be_a_fraction(client):
    with get_conn() as conn:
        student_id = conn.execute(
            "SELECT id FROM students WHERE external_id = 'stu-test'"
        ).fetchone()["id"]
        topic_id = conn.execute(
            "INSERT INTO topics (code, subject, title, created_at) "
            "VALUES ('T.TEST', 'Test', 'Test Topic', ?)",
            (utc_now_iso(),),
        ).lastrowid

    with pytest.raises(sqlite3.IntegrityError):
        with get_conn() as conn:
            conn.execute(
                "INSERT INTO mastery (student_id, topic_id, level) VALUES (?, ?, 1.4)",
                (student_id, topic_id),
            )


def test_parent_sees_only_their_own_child(client):
    """
    The core privacy guarantee. A parent linked to child A must get zero rows
    when querying child B through the parent_student join — which is why every
    parent-facing query must go through that join rather than trusting a
    student_id from the browser.
    """
    with get_conn() as conn:
        child_a = conn.execute(
            "INSERT INTO students (external_id, full_name, display_name, created_at, updated_at) "
            "VALUES ('stu-child-a', 'Child A', 'A', ?, ?)",
            (utc_now_iso(), utc_now_iso()),
        ).lastrowid
        child_b = conn.execute(
            "INSERT INTO students (external_id, full_name, display_name, created_at, updated_at) "
            "VALUES ('stu-child-b', 'Child B', 'B', ?, ?)",
            (utc_now_iso(), utc_now_iso()),
        ).lastrowid

        parent_a = conn.execute(
            "INSERT INTO parents (full_name, email, access_code_hash, created_at) "
            "VALUES ('Parent A', 'pa@souly.local', 'x', ?)",
            (utc_now_iso(),),
        ).lastrowid

        conn.execute(
            "INSERT INTO parent_student (parent_id, student_id, created_at) VALUES (?,?,?)",
            (parent_a, child_a, utc_now_iso()),
        )

    authorised_query = """
        SELECT s.id FROM students s
        JOIN parent_student ps ON ps.student_id = s.id
        WHERE ps.parent_id = ? AND s.id = ?
    """

    with get_conn() as conn:
        own = conn.execute(authorised_query, (parent_a, child_a)).fetchall()
        other = conn.execute(authorised_query, (parent_a, child_b)).fetchall()

    assert len(own) == 1, "Parent must be able to see their own child"
    assert len(other) == 0, "Parent must NOT be able to see another child"


def test_deleting_a_flag_cascades_to_its_events(client):
    with get_conn() as conn:
        student_id = conn.execute(
            "SELECT id FROM students WHERE external_id = 'stu-test'"
        ).fetchone()["id"]
        flag_id = conn.execute(
            "INSERT INTO flags (student_id, flag_type, detected_at) VALUES (?,?,?)",
            (student_id, "gaze_away", utc_now_iso()),
        ).lastrowid
        conn.execute(
            "INSERT INTO flag_events (flag_id, to_status, actor) VALUES (?,'pending','test')",
            (flag_id,),
        )

    with get_conn() as conn:
        conn.execute("DELETE FROM flags WHERE id = ?", (flag_id,))

    with get_conn() as conn:
        remaining = conn.execute(
            "SELECT COUNT(*) c FROM flag_events WHERE flag_id = ?", (flag_id,)
        ).fetchone()["c"]
    assert remaining == 0


def test_views_exist_and_are_queryable(client):
    with get_conn() as conn:
        conn.execute("SELECT * FROM v_pending_flags LIMIT 1").fetchall()
        rows = conn.execute("SELECT * FROM v_student_progress LIMIT 5").fetchall()
    assert rows, "v_student_progress should return the seeded students"


# =============================================================================
# Access codes
# =============================================================================

def test_access_code_round_trip():
    code = generate_access_code()
    stored = hash_secret(code)
    assert code not in stored, "Plaintext must never appear in the stored hash"
    assert verify_secret(code, stored) is True
    assert verify_secret(code + "X", stored) is False


def test_access_code_avoids_ambiguous_characters():
    """0/O and 1/I/L get misheard when read aloud to a parent."""
    for _ in range(20):
        body = generate_access_code().removeprefix("SOULY-")
        assert not set(body) & set("01OIL")


def test_verify_rejects_garbage_without_crashing():
    assert verify_secret("anything", "not-a-real-hash") is False
    assert verify_secret("anything", "") is False
