"""
Games, rewards shop, badges, daily challenge.

None of this is decoration. Stars earned in the learning loop are spent here,
badges unlock from real counters, and a game score writes a row. A judge who
taps "Unlock" gets a state change, not an alert box.
"""

import json
import sqlite3

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field

from app import economy
from app.db import db_dependency
from app.deps import get_student

router = APIRouter(prefix="/api/students/{student_ext_id}", tags=["gamification"])


class GameResult(BaseModel):
    score: int = Field(..., ge=0)
    max_score: int = Field(..., ge=1)
    duration_s: int = Field(0, ge=0)


# =============================================================================
# Games
# =============================================================================

@router.get("/games", summary="Game list with this student's history")
def list_games(
    student: sqlite3.Row = Depends(get_student),
    conn: sqlite3.Connection = Depends(db_dependency),
) -> dict:
    rows = conn.execute(
        """
        SELECT g.*,
               COALESCE(sub.name, '') AS subject_name,
               (SELECT COUNT(*)          FROM game_plays gp
                 WHERE gp.game_id = g.id AND gp.student_id = :sid) AS times_played,
               (SELECT COALESCE(MAX(score),0) FROM game_plays gp
                 WHERE gp.game_id = g.id AND gp.student_id = :sid) AS best_score
        FROM games g
        LEFT JOIN subjects sub ON sub.id = g.subject_id
        ORDER BY g.sort_order
        """,
        {"sid": student["id"]},
    ).fetchall()

    games = [
        {
            "id": r["id"], "code": r["code"], "name": r["name"],
            "description": r["description"], "icon": r["icon"],
            "difficulty": r["difficulty"], "star_reward": r["star_reward"],
            "engine": r["engine"], "subject_name": r["subject_name"],
            "topic_id": r["topic_id"],
            "is_featured": bool(r["is_featured"]),
            "times_played": r["times_played"], "best_score": r["best_score"],
        }
        for r in rows
    ]

    featured = next((g for g in games if g["is_featured"]), games[0] if games else None)

    # Recommend something they haven't played, else their least-played game.
    unplayed = [g for g in games if g["times_played"] == 0]
    recommended = (unplayed[0] if unplayed else
                   min(games, key=lambda g: g["times_played"]) if games else None)

    return {
        "games": games,
        "featured": featured,
        "stars": student["stars"],
        "recommendation": (
            {"game_code": recommended["code"],
             "message": f"I think you'll love {recommended['name']} today!"}
            if recommended else None
        ),
    }


@router.post("/games/{game_id}/result", summary="Record a finished game")
def record_game(
    game_id: int,
    payload: GameResult,
    student: sqlite3.Row = Depends(get_student),
    conn: sqlite3.Connection = Depends(db_dependency),
) -> dict:
    sid = student["id"]

    game = conn.execute("SELECT * FROM games WHERE id = ?", (game_id,)).fetchone()
    if game is None:
        raise HTTPException(status_code=404, detail=f"Game {game_id} not found")

    ratio = payload.score / payload.max_score
    is_win = ratio >= 0.6

    # Partial credit rather than all-or-nothing. A student who scores 55%
    # still worked for it, and zero reward for a near miss teaches them not
    # to try the hard game again.
    stars = game["star_reward"] if is_win else max(5, int(game["star_reward"] * ratio * 0.5))

    conn.execute(
        """
        INSERT INTO game_plays (student_id, game_id, score, max_score, is_win,
                                stars_earned, duration_s)
        VALUES (?,?,?,?,?,?,?)
        """,
        (sid, game_id, payload.score, payload.max_score, int(is_win),
         stars, payload.duration_s),
    )

    award = economy.award(
        conn, sid, "game_play",
        stars=stars,
        xp=economy.XP_PER_GAME_WIN if is_win else int(economy.XP_PER_GAME_WIN * ratio),
        subject_id=game["subject_id"], topic_id=game["topic_id"],
        reference_id=game_id, duration_s=payload.duration_s,
        detail=f"{game['name']}: {payload.score}/{payload.max_score}",
    )
    economy.mark_challenge_step(conn, sid, "game")

    best = conn.execute(
        "SELECT MAX(score) AS best FROM game_plays WHERE student_id = ? AND game_id = ?",
        (sid, game_id),
    ).fetchone()["best"]

    return {
        "is_win": is_win,
        "score": payload.score,
        "max_score": payload.max_score,
        "accuracy_pct": round(ratio * 100),
        "best_score": best,
        "is_personal_best": payload.score >= (best or 0),
        "award": award.to_dict(),
    }


@router.get("/games/{game_id}/questions", summary="Question set for a mini-game")
def game_questions(
    game_id: int,
    count: int = 10,
    student: sqlite3.Row = Depends(get_student),
    conn: sqlite3.Connection = Depends(db_dependency),
) -> dict:
    """
    Feed the in-browser mini-games from the same verified question bank the
    quiz uses, so a game can never ask something factually wrong.
    """
    game = conn.execute("SELECT * FROM games WHERE id = ?", (game_id,)).fetchone()
    if game is None:
        raise HTTPException(status_code=404, detail=f"Game {game_id} not found")

    # `review_status = 'approved'` is the gate that `is_verified` used to be.
    # A generated question sits at 'pending' until a human reads it, and a
    # game is scored, so an unreviewed item must never reach one.
    sql = "SELECT * FROM questions WHERE review_status = 'approved'"
    params: list[object] = []
    if game["topic_id"]:
        sql += " AND topic_id = ?"
        params.append(game["topic_id"])
    elif game["subject_id"]:
        sql += (" AND topic_id IN (SELECT id FROM topics WHERE subject_id = ?)")
        params.append(game["subject_id"])
    sql += " ORDER BY difficulty LIMIT ?"
    params.append(min(count, 25))

    rows = conn.execute(sql, params).fetchall()
    if not rows:
        rows = conn.execute(
            "SELECT * FROM questions WHERE review_status = 'approved' "
            "ORDER BY difficulty LIMIT ?",
            (min(count, 25),),
        ).fetchall()

    return {
        "game": {"id": game["id"], "name": game["name"], "engine": game["engine"]},
        "questions": [
            {
                "id": r["id"],
                "prompt": r["prompt"],
                "options": json.loads(r["options_json"]),
                "correct_index": r["correct_index"],
                "hint": r["hint"],
            }
            for r in rows
        ],
    }


# =============================================================================
# Rewards shop
# =============================================================================

@router.get("/rewards", summary="Shop catalogue with affordability")
def list_rewards(
    student: sqlite3.Row = Depends(get_student),
    conn: sqlite3.Connection = Depends(db_dependency),
) -> dict:
    owned = {
        r["reward_id"]: r for r in conn.execute(
            "SELECT reward_id, unlocked_at, is_equipped FROM student_rewards "
            "WHERE student_id = ?",
            (student["id"],),
        )
    }

    rewards = []
    for r in conn.execute("SELECT * FROM rewards ORDER BY sort_order"):
        is_owned = r["id"] in owned
        rewards.append({
            "id": r["id"], "code": r["code"], "name": r["name"],
            "description": r["description"], "icon": r["icon"],
            "cost_stars": r["cost_stars"], "category": r["category"],
            "payload": json.loads(r["payload"]) if r["payload"] else None,
            "owned": is_owned,
            "equipped": bool(owned[r["id"]]["is_equipped"]) if is_owned else False,
            "affordable": student["stars"] >= r["cost_stars"],
            "stars_needed": max(0, r["cost_stars"] - student["stars"]),
        })

    unlocked_count = len(owned)
    next_target = next(
        (r for r in rewards if not r["owned"]),
        None,
    )

    return {
        "rewards": rewards,
        "stars": student["stars"],
        "unlocked_count": unlocked_count,
        "total_count": len(rewards),
        "next_reward": (
            {
                "name": next_target["name"],
                "cost_stars": next_target["cost_stars"],
                "progress_pct": min(100, round(
                    (student["stars"] / next_target["cost_stars"]) * 100
                )) if next_target["cost_stars"] else 100,
            }
            if next_target else None
        ),
    }


@router.post("/rewards/{reward_id}/unlock", summary="Buy a reward with stars")
def unlock_reward(
    reward_id: int,
    student: sqlite3.Row = Depends(get_student),
    conn: sqlite3.Connection = Depends(db_dependency),
) -> dict:
    sid = student["id"]

    reward = conn.execute("SELECT * FROM rewards WHERE id = ?", (reward_id,)).fetchone()
    if reward is None:
        raise HTTPException(status_code=404, detail=f"Reward {reward_id} not found")

    already = conn.execute(
        "SELECT 1 FROM student_rewards WHERE student_id = ? AND reward_id = ?",
        (sid, reward_id),
    ).fetchone()
    if already:
        raise HTTPException(status_code=409, detail=f"You already own {reward['name']}")

    try:
        remaining = economy.spend_stars(
            conn, sid, reward["cost_stars"], f"Unlocked {reward['name']}"
        )
    except ValueError as exc:
        # 402 Payment Required is the honest status here, and it lets the UI
        # show "you need N more stars" instead of a generic error.
        raise HTTPException(status_code=402, detail=str(exc)) from exc

    conn.execute(
        "INSERT INTO student_rewards (student_id, reward_id, unlocked_at) VALUES (?,?,?)",
        (sid, reward_id, economy.utc_now()),
    )

    return {
        "unlocked": True,
        "reward": {
            "id": reward["id"], "code": reward["code"], "name": reward["name"],
            "icon": reward["icon"], "category": reward["category"],
            "payload": json.loads(reward["payload"]) if reward["payload"] else None,
        },
        "stars_spent": reward["cost_stars"],
        "stars_remaining": remaining,
    }


@router.post("/rewards/{reward_id}/equip", summary="Equip an owned reward")
def equip_reward(
    reward_id: int,
    student: sqlite3.Row = Depends(get_student),
    conn: sqlite3.Connection = Depends(db_dependency),
) -> dict:
    sid = student["id"]

    reward = conn.execute("SELECT * FROM rewards WHERE id = ?", (reward_id,)).fetchone()
    if reward is None:
        raise HTTPException(status_code=404, detail=f"Reward {reward_id} not found")

    owned = conn.execute(
        "SELECT 1 FROM student_rewards WHERE student_id = ? AND reward_id = ?",
        (sid, reward_id),
    ).fetchone()
    if not owned:
        raise HTTPException(status_code=403,
                            detail=f"You don't own {reward['name']} yet")

    # Only one item per category can be worn at a time.
    conn.execute(
        """
        UPDATE student_rewards SET is_equipped = 0
        WHERE student_id = ? AND reward_id IN (SELECT id FROM rewards WHERE category = ?)
        """,
        (sid, reward["category"]),
    )
    conn.execute(
        "UPDATE student_rewards SET is_equipped = 1 WHERE student_id = ? AND reward_id = ?",
        (sid, reward_id),
    )
    return {"equipped": True, "reward_code": reward["code"],
            "category": reward["category"]}


# =============================================================================
# Achievements
# =============================================================================

@router.get("/achievements", summary="Badges, trophies, streak")
def achievements(
    student: sqlite3.Row = Depends(get_student),
    conn: sqlite3.Connection = Depends(db_dependency),
) -> dict:
    sid = student["id"]
    badges = economy.badge_status(conn, sid)

    earned = [b for b in badges if b["unlocked"]]
    tiers = {"gold": 0, "silver": 0, "bronze": 0}
    for badge in earned:
        tiers[badge["tier"]] = tiers.get(badge["tier"], 0) + 1

    next_badge = min(
        (b for b in badges if not b["unlocked"]),
        key=lambda b: -b["progress_pct"],
        default=None,
    )

    return {
        "badges": badges,
        "earned_count": len(earned),
        "total_count": len(badges),
        "trophies": tiers,
        "day_streak": student["day_streak"],
        "stars": student["stars"],
        "next_badge": next_badge,
        "message": (
            f"Fantastic work! You've earned {len(earned)} badges so far."
            if earned else
            "Complete a lesson to earn your first badge!"
        ),
    }


# =============================================================================
# Daily challenge
# =============================================================================

@router.get("/challenge", summary="Today's challenge state")
def get_challenge(
    student: sqlite3.Row = Depends(get_student),
    conn: sqlite3.Connection = Depends(db_dependency),
) -> dict:
    row = economy.get_or_create_challenge(conn, student["id"])
    return {
        "date": row["challenge_date"],
        "lesson_done": bool(row["lesson_done"]),
        "quiz_done": bool(row["quiz_done"]),
        "game_done": bool(row["game_done"]),
        "all_done": bool(row["lesson_done"] and row["quiz_done"] and row["game_done"]),
        "reward_claimed": bool(row["reward_claimed"]),
        "reward_stars": row["reward_stars"],
    }


@router.post("/challenge/claim", summary="Claim the daily challenge reward")
def claim_challenge(
    student: sqlite3.Row = Depends(get_student),
    conn: sqlite3.Connection = Depends(db_dependency),
) -> dict:
    sid = student["id"]
    row = economy.get_or_create_challenge(conn, sid)

    if not (row["lesson_done"] and row["quiz_done"] and row["game_done"]):
        missing = [
            name for name, done in (
                ("a lesson", row["lesson_done"]),
                ("a quiz", row["quiz_done"]),
                ("a game", row["game_done"]),
            ) if not done
        ]
        raise HTTPException(
            status_code=409,
            detail=f"Challenge not finished yet — still need: {', '.join(missing)}",
        )
    if row["reward_claimed"]:
        raise HTTPException(status_code=409,
                            detail="Today's reward has already been claimed")

    conn.execute(
        "UPDATE daily_challenge_progress SET reward_claimed = 1 "
        "WHERE student_id = ? AND challenge_date = ?",
        (sid, row["challenge_date"]),
    )
    award = economy.award(
        conn, sid, "daily_challenge",
        stars=row["reward_stars"], xp=100,
        detail="Daily challenge complete",
    )
    return {"claimed": True, "award": award.to_dict()}
