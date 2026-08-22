"""
The economy engine — stars, XP, levels, streaks, badges, mastery.

**Every reward in the system flows through `award()`.** Nothing else writes
`students.stars` or `students.level` directly. That single rule is what makes
the numbers on the student's screen trustworthy: there is one place where a
star can be created, one place where a level can change, and one audit table
recording all of it.

It also means the parent report and the weekly chart can never disagree with
the star counter, because they're all reading the same `activity_log`.
"""

import sqlite3
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta, timezone
from typing import Any

# =============================================================================
# Tuning — all reward values in one block, deliberately
# =============================================================================

STARS_PER_CORRECT_ANSWER = 10
STARS_PER_WRONG_ANSWER = 2      # never zero; effort is worth something
STARS_PER_LESSON_STEP = 5
STARS_PER_LESSON_COMPLETE = 50
STARS_PER_QUIZ_COMPLETE = 30
STARS_PER_CHAT_QUESTION = 3
STARS_DAILY_CHALLENGE = 100

XP_PER_CORRECT_ANSWER = 15
XP_PER_LESSON_STEP = 8
XP_PER_LESSON_COMPLETE = 80
XP_PER_QUIZ_COMPLETE = 50
XP_PER_GAME_WIN = 40
XP_PER_CHAT_QUESTION = 5

# A quiz streak multiplies stars: 3 in a row = 1.5x, 5 in a row = 2x.
STREAK_MULTIPLIERS = {3: 1.5, 5: 2.0, 8: 2.5}

# XP needed to reach each level. Index = level - 1.
# Gentle early curve: a child should reach level 2 in one sitting, because
# the first level-up is what teaches them the system rewards them at all.
LEVEL_THRESHOLDS = [
    0, 100, 250, 450, 700, 1000, 1400, 1900, 2500, 3200,
    4000, 4900, 5900, 7000, 8200, 9500, 11000, 12600, 14300, 16100,
]

LEVEL_TITLES = [
    "Beginner", "Learner", "Explorer", "Adventurer", "Thinker",
    "Problem Solver", "Scholar", "Expert", "Master", "Champion",
]

# Mastery moves by this much per answer. Correct answers help more than wrong
# answers hurt — for a student who already struggles, a single mistake wiping
# out visible progress is discouraging in exactly the wrong way.
MASTERY_GAIN_CORRECT = 0.12
MASTERY_LOSS_WRONG = 0.05

# Which skills each activity develops, and by how much.
SKILL_MAP = {
    "quiz_answer": {"PROBLEM_SOLVING": 0.01, "CRITICAL_THINKING": 0.008},
    "lesson_step": {"READING": 0.008, "CRITICAL_THINKING": 0.005},
    "lesson_complete": {"PROBLEM_SOLVING": 0.02, "READING": 0.015},
    "game_play": {"PROBLEM_SOLVING": 0.012, "CREATIVITY": 0.01},
    "chat_question": {"COMMUNICATION": 0.015, "CREATIVITY": 0.008},
}


def utc_now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def today_str() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%d")


# =============================================================================
# Result object
# =============================================================================

@dataclass
class AwardResult:
    """
    What an award actually did. The UI uses this to decide what to celebrate:
    a level-up gets confetti, a badge gets a modal, plain stars get a counter
    tick. Returning it explicitly means the frontend never has to diff state
    to work out that something good happened.
    """

    stars_delta: int = 0
    xp_delta: int = 0
    total_stars: int = 0
    total_xp: int = 0
    level: int = 1
    level_title: str = "Beginner"
    leveled_up: bool = False
    previous_level: int = 1
    new_badges: list[dict[str, Any]] = field(default_factory=list)
    streak_days: int = 0
    streak_extended: bool = False
    multiplier: float = 1.0

    def to_dict(self) -> dict[str, Any]:
        return {
            "stars_delta": self.stars_delta,
            "xp_delta": self.xp_delta,
            "total_stars": self.total_stars,
            "total_xp": self.total_xp,
            "level": self.level,
            "level_title": self.level_title,
            "leveled_up": self.leveled_up,
            "previous_level": self.previous_level,
            "new_badges": self.new_badges,
            "streak_days": self.streak_days,
            "streak_extended": self.streak_extended,
            "multiplier": self.multiplier,
        }


# =============================================================================
# Levels
# =============================================================================

def level_for_xp(xp: int) -> int:
    level = 1
    for i, threshold in enumerate(LEVEL_THRESHOLDS):
        if xp >= threshold:
            level = i + 1
        else:
            break
    return level


def level_title(level: int) -> str:
    return LEVEL_TITLES[min(level - 1, len(LEVEL_TITLES) - 1)]


def xp_for_level(level: int) -> int:
    if level - 1 < len(LEVEL_THRESHOLDS):
        return LEVEL_THRESHOLDS[level - 1]
    # Past the table, keep a steady climb rather than an unreachable wall.
    return LEVEL_THRESHOLDS[-1] + (level - len(LEVEL_THRESHOLDS)) * 2000


def level_progress(xp: int) -> dict[str, Any]:
    """How far through the current level, for the progress ring."""
    level = level_for_xp(xp)
    floor_xp = xp_for_level(level)
    ceil_xp = xp_for_level(level + 1)
    span = max(ceil_xp - floor_xp, 1)
    pct = round(((xp - floor_xp) / span) * 100)
    return {
        "level": level,
        "title": level_title(level),
        "xp": xp,
        "level_floor_xp": floor_xp,
        "next_level_xp": ceil_xp,
        "xp_to_next": max(ceil_xp - xp, 0),
        "progress_pct": max(0, min(100, pct)),
    }


def streak_multiplier(streak: int) -> float:
    best = 1.0
    for threshold, mult in sorted(STREAK_MULTIPLIERS.items()):
        if streak >= threshold:
            best = mult
    return best


# =============================================================================
# The one entry point
# =============================================================================

def award(
    conn: sqlite3.Connection,
    student_id: int,
    activity_type: str,
    *,
    stars: int = 0,
    xp: int = 0,
    subject_id: int | None = None,
    topic_id: int | None = None,
    reference_id: int | None = None,
    duration_s: int = 0,
    detail: str | None = None,
    multiplier: float = 1.0,
) -> AwardResult:
    """
    Grant stars and XP, log the activity, update the streak, check for badges
    and level-ups. The only function permitted to change a student's totals.

    Caller supplies an open transaction; this does not commit.
    """
    stars_delta = int(round(stars * multiplier))
    xp_delta = int(round(xp * multiplier))

    row = conn.execute(
        "SELECT stars, level, day_streak, last_active_date FROM students WHERE id = ?",
        (student_id,),
    ).fetchone()
    if row is None:
        raise ValueError(f"No student {student_id}")

    previous_level = row["level"]

    # XP is the sum of the log rather than a column, so it can never drift
    # out of sync with the history that produced it.
    current_xp = conn.execute(
        "SELECT COALESCE(SUM(xp_delta), 0) AS xp FROM activity_log WHERE student_id = ?",
        (student_id,),
    ).fetchone()["xp"]

    conn.execute(
        """
        INSERT INTO activity_log (
            student_id, activity_type, subject_id, topic_id, reference_id,
            stars_delta, xp_delta, duration_s, detail, occurred_at, activity_date
        ) VALUES (?,?,?,?,?,?,?,?,?,?,?)
        """,
        (student_id, activity_type, subject_id, topic_id, reference_id,
         stars_delta, xp_delta, duration_s, detail, utc_now(), today_str()),
    )

    new_total_stars = max(0, row["stars"] + stars_delta)
    new_total_xp = current_xp + xp_delta
    new_level = level_for_xp(new_total_xp)

    streak_days, streak_extended = _update_streak(
        conn, student_id, row["day_streak"], row["last_active_date"]
    )

    conn.execute(
        "UPDATE students SET stars = ?, level = ?, day_streak = ?, "
        "last_active_date = ?, updated_at = ? WHERE id = ?",
        (new_total_stars, new_level, streak_days, today_str(), utc_now(), student_id),
    )

    _bump_skills(conn, student_id, activity_type)
    new_badges = check_badges(conn, student_id)

    return AwardResult(
        stars_delta=stars_delta,
        xp_delta=xp_delta,
        total_stars=new_total_stars,
        total_xp=new_total_xp,
        level=new_level,
        level_title=level_title(new_level),
        leveled_up=new_level > previous_level,
        previous_level=previous_level,
        new_badges=new_badges,
        streak_days=streak_days,
        streak_extended=streak_extended,
        multiplier=multiplier,
    )


def spend_stars(conn: sqlite3.Connection, student_id: int, amount: int,
                detail: str) -> int:
    """
    Deduct stars for a shop purchase. Raises if the student can't afford it,
    rather than letting the balance go negative.
    """
    row = conn.execute(
        "SELECT stars FROM students WHERE id = ?", (student_id,)
    ).fetchone()
    if row is None:
        raise ValueError(f"No student {student_id}")
    if row["stars"] < amount:
        raise ValueError(f"Not enough stars: have {row['stars']}, need {amount}")

    new_total = row["stars"] - amount
    conn.execute(
        "UPDATE students SET stars = ?, updated_at = ? WHERE id = ?",
        (new_total, utc_now(), student_id),
    )
    conn.execute(
        """
        INSERT INTO activity_log (student_id, activity_type, stars_delta,
                                  detail, occurred_at, activity_date)
        VALUES (?, 'reward_unlock', ?, ?, ?, ?)
        """,
        (student_id, -amount, detail, utc_now(), today_str()),
    )
    return new_total


# =============================================================================
# Streaks
# =============================================================================

def _update_streak(
    conn: sqlite3.Connection,
    student_id: int,
    current_streak: int,
    last_active: str | None,
) -> tuple[int, bool]:
    """
    Extend the streak if the last activity was yesterday, keep it if it was
    today, reset to 1 otherwise.

    Note the deliberate asymmetry: a streak resets to 1, not 0, because the
    student is active right now. Showing "0 day streak" to someone who just
    did a lesson is both wrong and demoralising.
    """
    today = date.today()
    if last_active is None:
        return 1, True

    try:
        last = datetime.strptime(last_active, "%Y-%m-%d").date()
    except ValueError:
        return 1, True

    if last == today:
        return max(current_streak, 1), False
    if last == today - timedelta(days=1):
        return current_streak + 1, True
    return 1, True


# =============================================================================
# Skills
# =============================================================================

def _bump_skills(conn: sqlite3.Connection, student_id: int,
                 activity_type: str) -> None:
    gains = SKILL_MAP.get(activity_type)
    if not gains:
        return

    for skill_code, gain in gains.items():
        skill = conn.execute(
            "SELECT id FROM skills WHERE code = ?", (skill_code,)
        ).fetchone()
        if skill is None:
            continue
        conn.execute(
            """
            INSERT INTO student_skills (student_id, skill_id, level, updated_at)
            VALUES (?, ?, ?, ?)
            ON CONFLICT(student_id, skill_id) DO UPDATE SET
                level = MIN(1.0, student_skills.level + ?),
                updated_at = excluded.updated_at
            """,
            (student_id, skill["id"], min(1.0, gain), utc_now(), gain),
        )


# =============================================================================
# Mastery
# =============================================================================

def update_mastery(
    conn: sqlite3.Connection,
    student_id: int,
    topic_id: int,
    is_correct: bool,
) -> dict[str, Any]:
    """
    Move a student's mastery of one topic after an answer. This is the
    `update_mastery` tool from the roadmap's agent tool list.
    """
    row = conn.execute(
        "SELECT * FROM mastery WHERE student_id = ? AND topic_id = ?",
        (student_id, topic_id),
    ).fetchone()

    if row is None:
        conn.execute(
            """
            INSERT INTO mastery (student_id, topic_id, level, attempts, correct,
                                 current_streak, best_streak, last_practiced_at, updated_at)
            VALUES (?,?,?,?,?,?,?,?,?)
            """,
            (student_id, topic_id,
             MASTERY_GAIN_CORRECT if is_correct else 0.0,
             1, 1 if is_correct else 0,
             1 if is_correct else 0, 1 if is_correct else 0,
             utc_now(), utc_now()),
        )
        return {
            "level": MASTERY_GAIN_CORRECT if is_correct else 0.0,
            "attempts": 1,
            "correct": 1 if is_correct else 0,
            "current_streak": 1 if is_correct else 0,
        }

    delta = MASTERY_GAIN_CORRECT if is_correct else -MASTERY_LOSS_WRONG
    new_level = max(0.0, min(1.0, row["level"] + delta))
    new_streak = row["current_streak"] + 1 if is_correct else 0
    best_streak = max(row["best_streak"], new_streak)

    conn.execute(
        """
        UPDATE mastery SET level = ?, attempts = ?, correct = ?,
                           current_streak = ?, best_streak = ?,
                           last_practiced_at = ?, updated_at = ?
        WHERE student_id = ? AND topic_id = ?
        """,
        (new_level, row["attempts"] + 1, row["correct"] + (1 if is_correct else 0),
         new_streak, best_streak, utc_now(), utc_now(), student_id, topic_id),
    )
    return {
        "level": new_level,
        "attempts": row["attempts"] + 1,
        "correct": row["correct"] + (1 if is_correct else 0),
        "current_streak": new_streak,
    }


# =============================================================================
# Badges
# =============================================================================

def _criteria_progress(conn: sqlite3.Connection, student_id: int,
                       criteria_type: str) -> int:
    """Current value of whatever a badge measures."""
    q = {
        "lessons_completed":
            "SELECT COUNT(*) n FROM lesson_progress WHERE student_id=? AND is_complete=1",
        "quizzes_completed":
            "SELECT COUNT(*) n FROM quizzes WHERE student_id=? AND status='complete'",
        "questions_correct":
            "SELECT COUNT(*) n FROM quiz_questions qq JOIN quizzes q ON q.id=qq.quiz_id "
            "WHERE q.student_id=? AND qq.is_correct=1",
        "games_won":
            "SELECT COUNT(*) n FROM game_plays WHERE student_id=? AND is_win=1",
        "chat_questions":
            "SELECT COUNT(*) n FROM chat_messages WHERE student_id=? AND role='student'",
        "streak_days":
            "SELECT day_streak n FROM students WHERE id=?",
        "stars_earned":
            "SELECT COALESCE(SUM(stars_delta),0) n FROM activity_log "
            "WHERE student_id=? AND stars_delta>0",
    }.get(criteria_type)

    if q is None:
        return 0
    row = conn.execute(q, (student_id,)).fetchone()
    return row["n"] if row else 0


def check_badges(conn: sqlite3.Connection, student_id: int) -> list[dict[str, Any]]:
    """
    Unlock any badge whose criteria the student now meets. Returns only the
    newly unlocked ones so the UI knows what to celebrate.
    """
    unlocked_ids = {
        r["badge_id"] for r in conn.execute(
            "SELECT badge_id FROM student_badges WHERE student_id = ?", (student_id,)
        )
    }

    newly: list[dict[str, Any]] = []
    for badge in conn.execute("SELECT * FROM badges ORDER BY sort_order"):
        if badge["id"] in unlocked_ids:
            continue
        if _criteria_progress(conn, student_id, badge["criteria_type"]) >= badge["criteria_value"]:
            conn.execute(
                "INSERT OR IGNORE INTO student_badges (student_id, badge_id, unlocked_at) "
                "VALUES (?,?,?)",
                (student_id, badge["id"], utc_now()),
            )
            conn.execute(
                """
                INSERT INTO activity_log (student_id, activity_type, reference_id,
                                          detail, occurred_at, activity_date)
                VALUES (?, 'badge_unlock', ?, ?, ?, ?)
                """,
                (student_id, badge["id"], badge["name"], utc_now(), today_str()),
            )
            newly.append({
                "code": badge["code"],
                "name": badge["name"],
                "description": badge["description"],
                "icon": badge["icon"],
                "tier": badge["tier"],
            })
    return newly


def badge_status(conn: sqlite3.Connection, student_id: int) -> list[dict[str, Any]]:
    """Every badge with unlock state and progress — the Achievements screen."""
    unlocked = {
        r["badge_id"]: r["unlocked_at"] for r in conn.execute(
            "SELECT badge_id, unlocked_at FROM student_badges WHERE student_id = ?",
            (student_id,),
        )
    }

    out = []
    for badge in conn.execute("SELECT * FROM badges ORDER BY sort_order"):
        current = _criteria_progress(conn, student_id, badge["criteria_type"])
        target = badge["criteria_value"]
        is_unlocked = badge["id"] in unlocked
        out.append({
            "code": badge["code"],
            "name": badge["name"],
            "description": badge["description"],
            "icon": badge["icon"],
            "tier": badge["tier"],
            "unlocked": is_unlocked,
            "unlocked_at": unlocked.get(badge["id"]),
            "progress": min(current, target),
            "target": target,
            "progress_pct": min(100, round((current / target) * 100)) if target else 0,
        })
    return out


# =============================================================================
# Daily challenge
# =============================================================================

def get_or_create_challenge(conn: sqlite3.Connection, student_id: int) -> sqlite3.Row:
    today = today_str()
    row = conn.execute(
        "SELECT * FROM daily_challenge_progress WHERE student_id = ? AND challenge_date = ?",
        (student_id, today),
    ).fetchone()
    if row is None:
        conn.execute(
            "INSERT INTO daily_challenge_progress (student_id, challenge_date, reward_stars) "
            "VALUES (?,?,?)",
            (student_id, today, STARS_DAILY_CHALLENGE),
        )
        row = conn.execute(
            "SELECT * FROM daily_challenge_progress WHERE student_id = ? AND challenge_date = ?",
            (student_id, today),
        ).fetchone()
    return row


def mark_challenge_step(conn: sqlite3.Connection, student_id: int,
                        step: str) -> dict[str, Any]:
    """step is one of: lesson, quiz, game."""
    column = {"lesson": "lesson_done", "quiz": "quiz_done", "game": "game_done"}.get(step)
    if column is None:
        raise ValueError(f"Unknown challenge step: {step}")

    get_or_create_challenge(conn, student_id)
    conn.execute(
        f"UPDATE daily_challenge_progress SET {column} = 1 "
        "WHERE student_id = ? AND challenge_date = ?",
        (student_id, today_str()),
    )
    row = get_or_create_challenge(conn, student_id)
    return {
        "lesson_done": bool(row["lesson_done"]),
        "quiz_done": bool(row["quiz_done"]),
        "game_done": bool(row["game_done"]),
        "all_done": bool(row["lesson_done"] and row["quiz_done"] and row["game_done"]),
        "reward_claimed": bool(row["reward_claimed"]),
        "reward_stars": row["reward_stars"],
    }
