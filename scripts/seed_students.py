#!/usr/bin/env python3
"""
Seed the Souly database with demo data.

    python scripts/seed_students.py            # add anything missing
    python scripts/seed_students.py --wipe     # clear seeded data first

============================== EDIT THIS FILE ==============================
Replace the five placeholder rows in TEAM_STUDENTS below with the real team
members. Only `full_name` and `display_name` matter for the demo; the rest
have sensible defaults.

`external_id` is what the CV rig and the robot use to identify a student, so
keep it short, lowercase, and stable — once the CV code has "stu-ahmed" baked
in, renaming it here breaks the pipeline.
============================================================================
"""

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.db import get_conn                                    # noqa: E402
from app.models import utc_now_iso                             # noqa: E402
from app.security import generate_access_code, hash_secret     # noqa: E402


# =============================================================================
# >>> REPLACE THESE WITH YOUR TEAM <<<
#
# support_profile must be one of:
#   none, autism, adhd, dyslexia, hearing_impairment,
#   visual_impairment, speech_impairment, other
#
# drift_threshold_ms is how long the CV should see drift before flagging.
# Raise it for students who look away as self-regulation — flagging an autistic
# student every three seconds for stimming is exactly the failure mode this
# project exists to avoid, and judges will ask about it.
# =============================================================================

TEAM_STUDENTS = [
    {
        "external_id": "stu-01", "avatar": "🦊", "avatar_color": "#F97316",
        "full_name": "TEAM MEMBER 1", "display_name": "Member1", "grade": "5",
        "support_profile": "autism",
        "support_notes": "Prefers short sentences. Needs 8s of silence before re-prompting.",
        "drift_threshold_ms": 8000,
    },
    {
        "external_id": "stu-02", "avatar": "🐼", "avatar_color": "#0EA5E9",
        "full_name": "TEAM MEMBER 2", "display_name": "Member2", "grade": "5",
        "support_profile": "adhd",
        "support_notes": "Responds well to frequent, small wins. Keep sessions under 10 min.",
        "drift_threshold_ms": 4000,
    },
    {
        "external_id": "stu-03", "avatar": "🦁", "avatar_color": "#EAB308",
        "full_name": "TEAM MEMBER 3", "display_name": "Member3", "grade": "6",
        "support_profile": "dyslexia",
        "support_notes": "Prefers spoken questions over written. Avoid dense on-screen text.",
        "drift_threshold_ms": 5000,
    },
    {
        "external_id": "stu-04", "avatar": "🐨", "avatar_color": "#10B981",
        "full_name": "TEAM MEMBER 4", "display_name": "Member4", "grade": "6",
        "support_profile": "hearing_impairment",
        "support_notes": "Always show text alongside speech. Touch input is primary.",
        "drift_threshold_ms": 6000,
    },
    {
        "external_id": "stu-05", "avatar": "🐧", "avatar_color": "#7C3AED",
        "full_name": "TEAM MEMBER 5", "display_name": "Member5", "grade": "5",
        "support_profile": "none",
        "support_notes": "No declared support needs.",
        "drift_threshold_ms": 5000,
    },
    {
        "external_id": "stu-06", "avatar": "🦉", "avatar_color": "#EC4899",
        "full_name": "TEAM MEMBER 6", "display_name": "Member6", "grade": "6",
        "support_profile": "speech_impairment",
        "support_notes": "Uses touch and typing rather than voice. Accept short answers.",
        "drift_threshold_ms": 6000,
    },
]

# NOTE: stars, streaks and levels are NOT seeded any more.
#
# They used to start at values like 1,250 stars and a 15-day streak, which made
# the screenshots look alive and the demo look dishonest — a judge asking
# "where did 1,250 come from?" deserved a real answer and there wasn't one.
# Every number now starts at zero and is earned. Use --demo-progress if you
# genuinely need a populated dashboard for a screenshot.


# Curriculum topics are NOT seeded here.
#
# They used to be, with seven placeholder topics. Those have been removed along
# with the rest of the placeholder curriculum. Topics are now created only by
# scripts/seed_content.py, from the CURRICULUM list your team fills in — so
# there is exactly one place content comes from, and no invented topic can
# reach the database by a side door.
TOPICS: list[tuple] = []


TEACHERS = [
    ("Demo Teacher", "teacher@souly.local", "teacher123"),
]

# Each parent is linked to exactly one student, which is the point: the
# parent_student join is what makes "only their child" true at the data layer.
PARENTS = [
    ("Parent of Member1", "parent1@souly.local", "stu-01", "mother"),
    ("Parent of Member2", "parent2@souly.local", "stu-02", "father"),
]


def wipe(conn) -> None:
    """Clear seeded data. Order matters — children before parents."""
    for table in ("flag_events", "flags", "attempts", "mastery",
                  "sessions", "parent_student", "parents",
                  "teachers", "topics", "students"):
        conn.execute(f"DELETE FROM {table}")
    conn.execute("DELETE FROM sqlite_sequence")
    print("  Wiped all seeded tables.")


def seed_students(conn) -> int:
    added = 0
    for s in TEAM_STUDENTS:
        exists = conn.execute(
            "SELECT 1 FROM students WHERE external_id = ?", (s["external_id"],)
        ).fetchone()
        if exists:
            # The six students predate schema_v4, so they came out of the
            # migration with the default face — six identical 🙂 tiles on a
            # sign-in screen whose whole point is picking your own picture.
            # Backfill, but only where nobody has chosen something else.
            conn.execute(
                """
                UPDATE students SET avatar = ?, avatar_color = ?, updated_at = ?
                WHERE external_id = ? AND (avatar = '🙂' OR avatar IS NULL)
                """,
                (s["avatar"], s["avatar_color"], utc_now_iso(), s["external_id"]),
            )
            continue
        conn.execute(
            """
            INSERT INTO students (
                external_id, full_name, display_name, grade,
                support_profile, support_notes, drift_threshold_ms,
                avatar, avatar_color, created_at, updated_at
            ) VALUES (?,?,?,?,?,?,?,?,?,?,?)
            """,
            (
                s["external_id"], s["full_name"], s["display_name"], s["grade"],
                s["support_profile"], s["support_notes"], s["drift_threshold_ms"],
                s["avatar"], s["avatar_color"], utc_now_iso(), utc_now_iso(),
            ),
        )
        added += 1
    return added


def seed_topics(conn) -> int:
    added = 0
    for code, subject, title, grade, order in TOPICS:
        exists = conn.execute("SELECT 1 FROM topics WHERE code = ?", (code,)).fetchone()
        if exists:
            continue
        conn.execute(
            """
            INSERT INTO topics (code, subject, title, grade, sort_order,
                                rag_collection, is_verified, created_at)
            VALUES (?,?,?,?,?,?,0,?)
            """,
            (code, subject, title, grade, order,
             code.lower().replace(".", "_"), utc_now_iso()),
        )
        added += 1
    return added


def seed_mastery(conn) -> int:
    """
    Fabricate partial progress so the dashboards have something to render.

    OFF by default — pass --demo-progress to enable.

    It used to run unconditionally, which meant the progress rings showed
    numbers no student had earned. That is fine for a screenshot and bad for
    a demo: a judge who asks "where does 78% come from?" deserves a real
    answer. Now you have to ask for it.
    """
    added = 0
    students = conn.execute("SELECT id, external_id FROM students").fetchall()
    topics = conn.execute("SELECT id FROM topics ORDER BY sort_order").fetchall()
    if not students or not topics:
        return 0

    # Deterministic spread — reproducible screenshots, no random surprises.
    levels = [0.85, 0.60, 0.35, 0.15, 0.0]

    for idx, student in enumerate(students):
        for t_idx, topic in enumerate(topics[:4]):
            exists = conn.execute(
                "SELECT 1 FROM mastery WHERE student_id = ? AND topic_id = ?",
                (student["id"], topic["id"]),
            ).fetchone()
            if exists:
                continue
            level = levels[(idx + t_idx) % len(levels)]
            attempts = 4 + t_idx * 2
            conn.execute(
                """
                INSERT INTO mastery (student_id, topic_id, level, attempts,
                                     correct, current_streak, best_streak,
                                     last_practiced_at, updated_at)
                VALUES (?,?,?,?,?,?,?,?,?)
                """,
                (
                    student["id"], topic["id"], level, attempts,
                    round(attempts * level), int(level * 3), int(level * 5),
                    utc_now_iso(), utc_now_iso(),
                ),
            )
            added += 1
    return added


def seed_teachers(conn) -> list[tuple[str, str]]:
    creds = []
    for name, email, password in TEACHERS:
        exists = conn.execute(
            "SELECT 1 FROM teachers WHERE email = ?", (email,)
        ).fetchone()
        if exists:
            continue
        conn.execute(
            "INSERT INTO teachers (full_name, email, password_hash, created_at) "
            "VALUES (?,?,?,?)",
            (name, email, hash_secret(password), utc_now_iso()),
        )
        creds.append((email, password))
    return creds


def seed_parents(conn) -> list[tuple[str, str, str]]:
    """Returns (parent_name, email, plaintext_access_code) for printing ONCE."""
    creds = []
    for name, email, student_ext_id, relationship in PARENTS:
        exists = conn.execute(
            "SELECT 1 FROM parents WHERE email = ?", (email,)
        ).fetchone()
        if exists:
            continue

        student = conn.execute(
            "SELECT id FROM students WHERE external_id = ?", (student_ext_id,)
        ).fetchone()
        if student is None:
            print(f"  ! Skipping parent {name}: no student '{student_ext_id}'")
            continue

        # Generated here and printed once. Only the hash reaches the database,
        # so if you lose the code you regenerate it — you cannot read it back.
        code = generate_access_code()
        cursor = conn.execute(
            "INSERT INTO parents (full_name, email, access_code_hash, created_at) "
            "VALUES (?,?,?,?)",
            (name, email, hash_secret(code), utc_now_iso()),
        )
        conn.execute(
            "INSERT INTO parent_student (parent_id, student_id, relationship, created_at) "
            "VALUES (?,?,?,?)",
            (cursor.lastrowid, student["id"], relationship, utc_now_iso()),
        )
        creds.append((name, email, code))
    return creds


def main() -> int:
    parser = argparse.ArgumentParser(description="Seed the Souly database.")
    parser.add_argument("--wipe", action="store_true",
                        help="Clear all seeded data first. DESTRUCTIVE.")
    parser.add_argument("--demo-progress", action="store_true",
                        help="Fabricate partial mastery so dashboards aren't at 0%%. "
                             "Off by default: these are numbers nobody earned.")
    args = parser.parse_args()

    with get_conn() as conn:
        if args.wipe:
            wipe(conn)

        n_students = seed_students(conn)
        n_topics = seed_topics(conn)
        n_mastery = seed_mastery(conn) if args.demo_progress else 0
        teacher_creds = seed_teachers(conn)
        parent_creds = seed_parents(conn)

        total_students = conn.execute("SELECT COUNT(*) c FROM students").fetchone()["c"]

    print("\nSeed complete.")
    print(f"  students : +{n_students}  (total {total_students})")
    print(f"  topics   : +{n_topics}")
    print(f"  mastery  : +{n_mastery} rows"
          + ("" if args.demo_progress else "   (use --demo-progress to fabricate some)"))

    if teacher_creds:
        print("\n  Teacher login:")
        for email, password in teacher_creds:
            print(f"    {email}  /  {password}")

    if parent_creds:
        print("\n  Parent access codes — SAVE THESE, they are not recoverable:")
        for name, email, code in parent_creds:
            print(f"    {name:24s} {email:24s} {code}")

    print("\n  Remember to replace the TEAM MEMBER placeholders in this file")
    print("  with real names, then re-run with --wipe.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
