#!/usr/bin/env python3
"""
Fabricate a believable two weeks of learning for one student.

    python scripts/seed_demo_progress.py --student stu-01

Every star, every XP point and every badge here is created by economy.award(),
the same single writer the real app uses, so the home counter, the weekly
chart, the progress screen and the parent report cannot disagree with each
other. Only the timestamps are rewritten afterwards, to spread the activity
across past days.

This is demo data. It is for screenshots and rehearsal, not for a real child.
"""

from __future__ import annotations

import argparse
import random
import sys
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from app import economy                    # noqa: E402
from app.db import get_conn                # noqa: E402


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--student", default="stu-01")
    ap.add_argument("--seed", type=int, default=7)
    args = ap.parse_args()
    random.seed(args.seed)

    with get_conn() as conn:
        srow = conn.execute(
            "SELECT id, display_name FROM students WHERE external_id = ?",
            (args.student,),
        ).fetchone()
        if srow is None:
            print(f"No student {args.student}")
            return 1
        sid = srow["id"]

        # Start from a clean slate for this one child.
        for table in ("activity_log", "student_badges", "mastery",
                      "student_skills", "page_renditions", "page_activity",
                      "lesson_progress", "game_plays", "student_rewards",
                      "daily_challenge_progress"):
            try:
                conn.execute(f"DELETE FROM {table} WHERE student_id = ?", (sid,))
            except Exception:
                pass
        conn.execute(
            "UPDATE students SET stars = 0, level = 1, day_streak = 0, "
            "last_active_date = NULL WHERE id = ?", (sid,)
        )

        topics = conn.execute(
            """
            SELECT t.id, t.title, b.subject_code
            FROM topics t JOIN curriculum_books b ON b.id = t.book_id
            ORDER BY b.id, t.sort_order
            """
        ).fetchall()
        if not topics:
            print("No curriculum ingested — run scripts/ingest_curriculum.py first.")
            return 1

        subjects = {
            r["code"]: r["id"]
            for r in conn.execute("SELECT id, code FROM subjects")
        }

        # ---- the history -----------------------------------------------------
        # (days_ago, how many of each kind of thing happened that day)
        plan = [
            (10, dict(steps=3, answers=4, correct=2, quizzes=1, games=1, chats=2)),
            (8,  dict(steps=2, answers=4, correct=3, quizzes=0, games=1, chats=2)),
            (6,  dict(steps=3, answers=5, correct=4, quizzes=1, games=1, chats=1)),
            (5,  dict(steps=2, answers=4, correct=3, quizzes=0, games=1, chats=2)),
            (4,  dict(steps=3, answers=5, correct=4, quizzes=1, games=0, chats=1)),
            (3,  dict(steps=2, answers=4, correct=4, quizzes=0, games=1, chats=2)),
            (2,  dict(steps=3, answers=5, correct=4, quizzes=1, games=1, chats=1)),
            (1,  dict(steps=3, answers=5, correct=5, quizzes=1, games=0, chats=2)),
            (0,  dict(steps=2, answers=3, correct=3, quizzes=0, games=0, chats=1)),
        ]

        lessons_done = 0
        made = 0

        for days_ago, d in plan:
            when = datetime.now(timezone.utc) - timedelta(days=days_ago)
            stamp = when.strftime("%Y-%m-%dT%H:%M:%SZ")
            day = when.strftime("%Y-%m-%d")
            topic = topics[(days_ago * 3) % len(topics)]
            subject_id = subjects.get(topic["subject_code"])

            def log(kind, stars, xp, *, mult=1.0, secs=0, ref=None):
                nonlocal made
                economy.award(
                    conn, sid, kind, stars=stars, xp=xp,
                    subject_id=subject_id, topic_id=topic["id"],
                    reference_id=ref, duration_s=secs, multiplier=mult,
                )
                made += 1

            for _ in range(d["steps"]):
                log("lesson_step", economy.STARS_PER_LESSON_STEP,
                    economy.XP_PER_LESSON_STEP, secs=random.randint(45, 130))

            streak = 0
            for i in range(d["answers"]):
                correct = i < d["correct"]
                streak = streak + 1 if correct else 0
                log("quiz_answer",
                    economy.STARS_PER_CORRECT_ANSWER if correct
                    else economy.STARS_PER_WRONG_ANSWER,
                    economy.XP_PER_CORRECT_ANSWER if correct else 0,
                    mult=economy.streak_multiplier(streak) if correct else 1.0,
                    secs=random.randint(12, 40))
                economy.update_mastery(conn, sid, topic["id"], correct)

            for _ in range(d["quizzes"]):
                log("quiz_complete", economy.STARS_PER_QUIZ_COMPLETE,
                    economy.XP_PER_QUIZ_COMPLETE, secs=random.randint(180, 340))

            for _ in range(d["games"]):
                log("game_play", 20, economy.XP_PER_GAME_WIN,
                    secs=random.randint(90, 200))

            for _ in range(d["chats"]):
                log("chat_question", economy.STARS_PER_CHAT_QUESTION,
                    economy.XP_PER_CHAT_QUESTION, secs=random.randint(20, 60))

            if days_ago in (8, 4, 1):
                lessons_done += 1
                log("lesson_complete", economy.STARS_PER_LESSON_COMPLETE,
                    economy.XP_PER_LESSON_COMPLETE, secs=random.randint(400, 700))

            # Move everything written this round back to the right day.
            conn.execute(
                "UPDATE activity_log SET occurred_at = ?, activity_date = ? "
                "WHERE student_id = ? AND activity_date = ?",
                (stamp, day, sid, date.today().isoformat()),
            )

        # The streak field is computed per-award against 'today', so set it to
        # match the history we just backdated.
        conn.execute(
            "UPDATE students SET day_streak = ?, last_active_date = ? WHERE id = ?",
            (9, date.today().isoformat(), sid),
        )

        # Badge criteria count real rows, not the activity log, so write the
        # records those activities would have produced.
        now = economy.utc_now()

        # Completed lessons are taken from the SECOND book, so the first
        # lesson of the first book is still ahead of the child — which is what
        # the home screen offers as "starting today".
        done = [t for t in topics if t["subject_code"] != topics[0]["subject_code"]][:3]
        for t in done:
            conn.execute(
                "INSERT OR REPLACE INTO lesson_progress (student_id, topic_id, "
                "pages_completed, last_page, is_complete, started_at, completed_at) "
                "VALUES (?,?,?,?,1,?,?)",
                (sid, t["id"], 8, 8, now, now),
            )

        for i in range(5):
            conn.execute(
                "INSERT INTO quizzes (student_id, topic_id, total_questions, "
                "current_index, score, correct_count, status, started_at, completed_at) "
                "VALUES (?,?,?,?,?,?,'complete',?,?)",
                (sid, topics[i % len(topics)]["id"], 5, 5, 4, 4, now, now),
            )

        game_ids = [r["id"] for r in conn.execute("SELECT id FROM games ORDER BY id")]
        for i in range(6):
            conn.execute(
                "INSERT INTO game_plays (student_id, game_id, score, max_score, "
                "is_win, stars_earned, duration_s, played_at) VALUES (?,?,?,?,1,?,?,?)",
                (sid, game_ids[i % len(game_ids)], 8 + i, 10, 20,
                 120 + i * 15, now),
            )

        asked = [
            "What does the digit 8 mean in 3,249.578?",
            "Can you say the thousandths grid another way?",
            "Why is 0.087 smaller than 0.476?",
            "How do I write 84.005 in words?",
            "What is a decimal point for?",
            "Show me an example with tenths.",
            "I don't get hundredths yet.",
            "What does a plant need to grow?",
            "Why do leaves need sunlight?",
            "Can you say that more slowly?",
            "What is photosynthesis?",
            "How do seeds travel?",
        ]
        for q in asked:
            conn.execute(
                "INSERT INTO chat_messages (student_id, role, content, input_mode, "
                "created_at) VALUES (?, 'student', ?, 'text', ?)",
                (sid, q, now),
            )

        # Now re-check, against the rows the criteria actually read.
        conn.execute("DELETE FROM student_badges WHERE student_id = ?", (sid,))
        earned = economy.check_badges(conn, sid)

        # Pages of lesson one, worked through, so 'resume where you left off'
        # has somewhere to resume to.
        first = topics[0]
        pages = conn.execute(
            "SELECT p.id FROM curriculum_pages p JOIN topics t ON t.book_id = p.book_id "
            "AND t.lesson_label = p.lesson WHERE t.id = ? ORDER BY p.page LIMIT 3",
            (first["id"],),
        ).fetchall()
        for p in pages:
            try:
                conn.execute(
                    "INSERT INTO page_activity (student_id, page_id, "
                    "seconds_on_page, created_at) VALUES (?,?,?,?)",
                    (sid, p["id"], 95, economy.utc_now()),
                )
            except Exception:
                pass

        totals = conn.execute(
            "SELECT stars, level, day_streak FROM students WHERE id = ?", (sid,)
        ).fetchone()

    print(f"\n  Demo history written for {srow['display_name']} ({args.student})")
    print(f"    {made} activities across {len(plan)} days")
    print(f"    {totals['stars']} stars · level {totals['level']} "
          f"· {totals['day_streak']}-day streak")
    print(f"    {len(earned)} badges earned · {lessons_done} lessons completed")
    print("\n  This is demo data. Re-run scripts/seed_students.py --wipe to clear it.\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
