#!/usr/bin/env python3
"""
Seed the scaffolding: subjects, badges, rewards, games, skills and settings.

    python scripts/seed_content.py            # add anything missing
    python scripts/seed_content.py --wipe     # clear content first

Run AFTER scripts/seed_students.py.

------------------------------------------------------------------------------
NOTE — there is no curriculum in this file, and there cannot be
------------------------------------------------------------------------------
This script used to carry a `CURRICULUM` list of hand-written lessons. Since
schema_v5 the curriculum is the Ministry PDFs themselves, and the only thing
that loads it is:

    python scripts/ingest_curriculum.py

which reads data/curriculum/curriculum_map.json — a human-approved map of
which lesson lives on which page — and writes curriculum_books,
curriculum_pages and one topic per lesson. No prose is copied into SQLite at
any point, because a copy is a second version that silently drifts from the
book.

What is left here is the structure around the teaching: the subject cards, the
badge and reward criteria, the mini-games, and each student's settings,
schedule and weekly goals. None of it teaches anything.

If no book has been ingested, the app still runs: the Learn screen shows its
empty state and Souly declines every question rather than inventing an answer.
That is the safety mechanism working, not a failure.
"""

import argparse
import json
import sys
from datetime import date, timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.db import get_conn          # noqa: E402
from app.models import utc_now_iso   # noqa: E402


# =============================================================================
# SUBJECTS — the six cards on the Learn screen
# =============================================================================

SUBJECTS = [
    # code,   name,             icon, difficulty, from,      to
    ("MATH",  "Mathematics",    "➗", "Medium", "#7C3AED", "#A855F7"),
    ("ENG",   "English",        "📖", "Easy",   "#0EA5E9", "#38BDF8"),
    ("SCI",   "Science",        "🔬", "Medium", "#10B981", "#34D399"),
    ("SOC",   "Social Studies", "🌍", "Easy",   "#F59E0B", "#FBBF24"),
    ("CODE",  "Coding",         "💻", "Hard",   "#EC4899", "#F472B6"),
    ("AR",    "Arabic",         "🕌", "Easy",   "#8B5CF6", "#C084FC"),
]


# =============================================================================
# BADGES — criteria are data, so adding one is an INSERT
# =============================================================================

BADGES = [
    ("FIRST_STEPS",   "First Steps",       "Complete your first lesson",  "👣", "bronze", "lessons_completed", 1,   1),
    ("MATH_MASTER",   "Math Master",       "Complete 10 lessons",         "🧮", "gold",   "lessons_completed", 10,  2),
    ("QUIZ_ROOKIE",   "Quiz Rookie",       "Finish your first quiz",      "📝", "bronze", "quizzes_completed", 1,   3),
    ("SHARP_MIND",    "Sharp Mind",        "Answer 25 questions correctly", "🎯", "silver", "questions_correct", 25, 4),
    ("GAME_CHAMPION", "Game Champion",     "Win 5 games",                 "🎮", "silver", "games_won",         5,   5),
    ("CURIOUS_MIND",  "Curious Mind",      "Ask Souly 10 questions",     "🤖", "bronze", "chat_questions",    10,  6),
    ("AI_EXPERT",     "AI Expert",         "Ask Souly 50 questions",     "🧠", "gold",   "chat_questions",    50,  7),
    ("WEEK_WARRIOR",  "Week Warrior",      "Learn 7 days in a row",       "🔥", "silver", "streak_days",       7,   8),
    ("STREAK_LEGEND", "30-Day Streak",     "Learn every day for a month", "⚡", "gold",   "streak_days",       30,  9),
    ("STAR_COLLECTOR","Star Collector",    "Earn 500 stars",              "⭐", "silver", "stars_earned",      500, 10),
]


# =============================================================================
# REWARDS — what stars are actually for
# =============================================================================

REWARDS = [
    ("THEME_PURPLE",  "Purple Robot Theme",  "Give the app a deep purple look.",       "🟣", 250, "theme",    {"theme": "purple"},        1),
    ("SOUNDS_FUN",    "Fun Greeting Sounds", "Souly greets you with cheerful sounds.", "🔊", 180, "sound",    {"greeting_sound": "fun"},  2),
    ("EXPR_NEW",      "New Expressions",     "Unlock more robot faces for Souly.",     "😃", 200, "cosmetic", {"expressions": "extended"},3),
    ("STORY_PACK",    "Story Collection",    "A set of short stories to read.",         "📚", 220, "content",  {"stories": "pack1"},       4),
    ("DANCE_CELEB",   "Celebration Dance",   "Souly dances when you get things right.", "💃", 350, "cosmetic", {"celebration": "dance"},   5),
    ("GAME_BONUS",    "Bonus Mini Game",     "Unlock an extra game to play.",           "🕹️", 300, "game",     {"game": "bonus"},          6),
    ("OUTFIT_HERO",   "Super Souly Outfit",  "Give Souly a cool superhero look.",      "🦸", 500, "cosmetic", {"outfit": "superhero"},    7),
]


# =============================================================================
# GAMES
# =============================================================================

GAMES = [
    ("SPACE_MATH",  "Space Math Adventure", "Practice adding and subtracting while exploring space!", "🚀", "Medium", 40, "MATH", "math_sprint",  1, 1),
    ("MEMORY",      "Memory Match",         "Match the pairs and train your memory.",                 "🧩", "Easy",   25, None,   "memory_match", 0, 2),
    ("WORD_BUILD",  "Word Builder",         "Build words letter by letter.",                          "🔤", "Easy",   30, "ENG",  "word_builder", 0, 3),
    ("SCI_LAB",     "Science Lab",          "Answer science questions to run experiments.",           "🔬", "Medium", 50, "SCI",  "math_sprint",  0, 4),
    ("GEO_EXPLORE", "Geography Explorer",   "Travel the world and learn about places.",               "🌍", "Medium", 45, "SOC",  "math_sprint",  0, 5),
    ("QUICK_QUIZ",  "Quick Fire Quiz",      "How many can you get right before time runs out?",       "⚡", "Easy",   35, None,   "math_sprint",  0, 6),
]


# =============================================================================
# SKILLS
# =============================================================================

SKILLS = [
    ("PROBLEM_SOLVING",   "Problem Solving",   "🧩", 1),
    ("READING",           "Reading",           "📖", 2),
    ("CRITICAL_THINKING", "Critical Thinking", "🤔", 3),
    ("CREATIVITY",        "Creativity",        "🎨", 4),
    ("COMMUNICATION",     "Communication",     "💬", 5),
]


# =============================================================================
# Seeding
# =============================================================================

def wipe(conn) -> None:
    # Deliberately NOT in this list: curriculum_books, curriculum_pages and
    # topics. Those are the ingested book, and this script has no business
    # deleting them — scripts/clear_curriculum.py does that, on purpose, with
    # a confirmation prompt.
    for table in ("quiz_questions", "quizzes", "questions", "lesson_progress",
                  "page_activity", "page_renditions",
                  "student_badges", "badges",
                  "student_rewards", "rewards", "game_plays", "games",
                  "student_skills", "skills", "weekly_goals", "schedule_items",
                  "daily_challenge_progress", "activity_log", "chat_messages",
                  "subjects"):
        conn.execute(f"DELETE FROM {table}")
    print("  Wiped the scaffolding tables. The ingested books were left alone.")


def seed_subjects(conn) -> int:
    added = 0
    for order, (code, name, icon, difficulty, c_from, c_to) in enumerate(SUBJECTS, 1):
        if conn.execute("SELECT 1 FROM subjects WHERE code = ?", (code,)).fetchone():
            continue
        conn.execute(
            "INSERT INTO subjects (code, name, icon, difficulty, color_from, "
            "color_to, sort_order, created_at) VALUES (?,?,?,?,?,?,?,?)",
            (code, name, icon, difficulty, c_from, c_to, order, utc_now_iso()),
        )
        added += 1
    return added


def seed_badges(conn) -> int:
    added = 0
    for code, name, desc, icon, tier, c_type, c_value, order in BADGES:
        if conn.execute("SELECT 1 FROM badges WHERE code = ?", (code,)).fetchone():
            continue
        conn.execute(
            "INSERT INTO badges (code, name, description, icon, tier, "
            "criteria_type, criteria_value, sort_order) VALUES (?,?,?,?,?,?,?,?)",
            (code, name, desc, icon, tier, c_type, c_value, order),
        )
        added += 1
    return added


def seed_rewards(conn) -> int:
    added = 0
    for code, name, desc, icon, cost, category, payload, order in REWARDS:
        if conn.execute("SELECT 1 FROM rewards WHERE code = ?", (code,)).fetchone():
            continue
        conn.execute(
            "INSERT INTO rewards (code, name, description, icon, cost_stars, "
            "category, payload, sort_order) VALUES (?,?,?,?,?,?,?,?)",
            (code, name, desc, icon, cost, category, json.dumps(payload), order),
        )
        added += 1
    return added


def seed_games(conn) -> int:
    added = 0
    for code, name, desc, icon, difficulty, reward, subj_code, engine, featured, order in GAMES:
        if conn.execute("SELECT 1 FROM games WHERE code = ?", (code,)).fetchone():
            continue
        subject_id = None
        topic_id = None
        if subj_code:
            row = conn.execute(
                "SELECT id FROM subjects WHERE code = ?", (subj_code,)
            ).fetchone()
            subject_id = row["id"] if row else None
            if subject_id:
                topic = conn.execute(
                    "SELECT id FROM topics WHERE subject_id = ? ORDER BY sort_order LIMIT 1",
                    (subject_id,),
                ).fetchone()
                topic_id = topic["id"] if topic else None

        conn.execute(
            "INSERT INTO games (code, name, description, icon, difficulty, "
            "star_reward, subject_id, topic_id, engine, is_featured, sort_order) "
            "VALUES (?,?,?,?,?,?,?,?,?,?,?)",
            (code, name, desc, icon, difficulty, reward, subject_id, topic_id,
             engine, featured, order),
        )
        added += 1
    return added


def seed_skills(conn) -> int:
    added = 0
    for code, name, icon, order in SKILLS:
        if conn.execute("SELECT 1 FROM skills WHERE code = ?", (code,)).fetchone():
            continue
        conn.execute(
            "INSERT INTO skills (code, name, icon, sort_order) VALUES (?,?,?,?)",
            (code, name, icon, order),
        )
        added += 1
    return added


def seed_student_extras(conn) -> dict[str, int]:
    """Per-student rows: settings, schedule, weekly goals."""
    counts = {"settings": 0, "schedule": 0, "goals": 0}
    monday = (date.today() - timedelta(days=date.today().weekday())).isoformat()

    students = conn.execute("SELECT id FROM students WHERE is_active = 1").fetchall()
    subjects = {
        r["code"]: r["id"] for r in conn.execute("SELECT code, id FROM subjects")
    }

    for student in students:
        sid = student["id"]

        if not conn.execute(
            "SELECT 1 FROM student_settings WHERE student_id = ?", (sid,)
        ).fetchone():
            conn.execute("INSERT INTO student_settings (student_id) VALUES (?)", (sid,))
            counts["settings"] += 1

        if not conn.execute(
            "SELECT 1 FROM schedule_items WHERE student_id = ?", (sid,)
        ).fetchone():
            for day in range(5):  # Mon-Fri
                for order, (time_s, label, icon, subj) in enumerate([
                    ("09:00", "Mathematics", "🕐", "MATH"),
                    ("11:00", "Science", "🕐", "SCI"),
                    ("16:00", "Story Time", "📖", "ENG"),
                ], 1):
                    conn.execute(
                        "INSERT INTO schedule_items (student_id, day_of_week, "
                        "start_time, label, icon, subject_id, sort_order) "
                        "VALUES (?,?,?,?,?,?,?)",
                        (sid, day, time_s, label, icon, subjects.get(subj), order),
                    )
                    counts["schedule"] += 1

        if not conn.execute(
            "SELECT 1 FROM weekly_goals WHERE student_id = ? AND week_start = ?",
            (sid, monday),
        ).fetchone():
            for order, (label, target, goal_type) in enumerate([
                ("Complete 3 Math Lessons", 3, "lesson"),
                ("Finish 2 Quizzes", 2, "quiz"),
                ("Play Science Lab", 1, "game"),
                ("Ask Souly 5 Questions", 5, "chat"),
            ], 1):
                conn.execute(
                    "INSERT INTO weekly_goals (student_id, week_start, label, "
                    "target_count, goal_type, sort_order) VALUES (?,?,?,?,?,?)",
                    (sid, monday, label, target, goal_type, order),
                )
                counts["goals"] += 1

    return counts


def main() -> int:
    parser = argparse.ArgumentParser(description="Seed Souly curriculum and content.")
    parser.add_argument("--wipe", action="store_true",
                        help="Clear content tables first. DESTRUCTIVE.")
    args = parser.parse_args()

    with get_conn() as conn:
        if not conn.execute("SELECT 1 FROM students LIMIT 1").fetchone():
            print("! No students found. Run scripts/seed_students.py first.")
            return 1

        if args.wipe:
            wipe(conn)

        n_subjects = seed_subjects(conn)
        n_badges = seed_badges(conn)
        n_rewards = seed_rewards(conn)
        n_games = seed_games(conn)
        n_skills = seed_skills(conn)
        extras = seed_student_extras(conn)

    print("\nContent seeded.")
    print(f"  subjects  : +{n_subjects}")
    print(f"  badges    : +{n_badges}")
    print(f"  rewards   : +{n_rewards}")
    print(f"  games     : +{n_games}")
    print(f"  skills    : +{n_skills}")
    print(f"  settings  : +{extras['settings']}   schedule: +{extras['schedule']}   "
          f"goals: +{extras['goals']}")
    print("\n  This seeds the scaffolding only. The curriculum comes from the")
    print("  books:\n")
    print("      python scripts/ingest_curriculum.py\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
