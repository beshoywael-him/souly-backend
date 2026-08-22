#!/usr/bin/env python3
"""
Remove all curriculum content from an existing database.

    python scripts/clear_curriculum.py           # show what would go, then ask
    python scripts/clear_curriculum.py --yes     # no prompt

Use this when the database already contains lessons you no longer want —
for example the placeholder curriculum that shipped with the first build.

WHAT IT DELETES
    curriculum_books, curriculum_pages, topics, questions
    and everything that only makes sense alongside them:
    lesson_progress, page_activity, page_renditions,
    quizzes, quiz_questions, mastery, attempts

WHAT IT DOES NOT TOUCH
    The PDFs. They are the canon and they stay on disk, along with the
    rendered pages and text under data/curriculum/.cache/. Clearing the
    database and re-running scripts/ingest_curriculum.py puts everything
    back, which is the point of keeping no content in SQLite.

WHAT IT KEEPS
    students, teachers, parents and their links
    subjects, badges, rewards, games, skills
    student settings, schedules, weekly goals
    stars, XP, streaks and activity history
    flags and flag events

So your team members stay, their accounts stay, their stars stay — only the
teaching material and the records that point at it are removed.
"""

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.config import settings   # noqa: E402
from app.db import get_conn       # noqa: E402

# Order matters: children before parents, or foreign keys reject the delete.
TABLES = [
    ("quiz_questions", "answered quiz questions"),
    ("quizzes", "quiz runs"),
    ("attempts", "answer records"),
    ("page_renditions", "cached per-child explanations"),
    ("page_activity", "time-on-page records"),
    ("lesson_progress", "per-student lesson progress"),
    ("mastery", "per-topic mastery scores"),
    ("questions", "question bank entries"),
    ("curriculum_pages", "lesson-to-page map rows"),
    ("curriculum_books", "books"),
    ("topics", "lessons"),
]


def counts(conn) -> dict[str, int]:
    out = {}
    for table, _ in TABLES:
        try:
            out[table] = conn.execute(f"SELECT COUNT(*) c FROM {table}").fetchone()["c"]
        except Exception:
            out[table] = 0
    return out


def main() -> int:
    parser = argparse.ArgumentParser(description="Delete all curriculum content.")
    parser.add_argument("--yes", action="store_true", help="Skip the confirmation.")
    args = parser.parse_args()

    if not settings.db_file.exists():
        print(f"No database at {settings.db_file} — nothing to clear.")
        return 0

    with get_conn() as conn:
        before = counts(conn)

    total = sum(before.values())
    print(f"\nDatabase: {settings.db_file}\n")

    if total == 0:
        print("  No curriculum content found. Nothing to do.\n")
        return 0

    print("  Will delete:")
    for table, label in TABLES:
        if before[table]:
            print(f"    {before[table]:>6}  {label}")

    with get_conn() as conn:
        students = conn.execute(
            "SELECT COUNT(*) c FROM students").fetchone()["c"]
        stars = conn.execute(
            "SELECT COALESCE(SUM(stars),0) c FROM students").fetchone()["c"]
    print(f"\n  Will keep: {students} students, {stars:,} stars, "
          "all accounts, badges, rewards and settings.\n")

    if not args.yes:
        answer = input("  Proceed? [y/N] ").strip().lower()
        if answer != "y":
            print("  Aborted.\n")
            return 1

    with get_conn() as conn:
        for table, _ in TABLES:
            conn.execute(f"DELETE FROM {table}")
        # Reset autoincrement so new content starts at id 1 rather than
        # continuing from the deleted rows' numbering.
        for table, _ in TABLES:
            conn.execute("DELETE FROM sqlite_sequence WHERE name = ?", (table,))

    with get_conn() as conn:
        after = counts(conn)

    remaining = sum(after.values())
    print(f"\n  Deleted {total} rows. {remaining} remaining.\n")
    print("  The app will now show empty states and Souly will decline every")
    print("  question — that is retrieval having nothing verified to draw on,")
    print("  not a fault. The books themselves are untouched on disk, so:\n")
    print("      python scripts/ingest_curriculum.py\n")
    print("  puts the whole curriculum back from data/curriculum/.\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
