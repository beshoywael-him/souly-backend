"""
The entry activity — "help me learn how you like things explained".

Runs once, after first sign-in. Produces a `learner_profiles` row that the
tutor reads before deciding how to pitch an explanation.

-----------------------------------------------------------------------------
IT IS NOT A TEST, AND THE FRAMING IS PART OF THE DESIGN
-----------------------------------------------------------------------------
No score is shown. No timer. No right/wrong sound. Every item is skippable and
a skip is recorded as missing, never as failure. The child is told up front
exactly what is coming and how long it takes.

That isn't politeness. Courchesne et al. (2015) gave a standard WISC-IV to 30
minimally-verbal autistic children: ZERO completed it. Under a strength-
informed protocol — familiar setting, no time pressure, short sessions — 26 of
30 completed and most scored far higher. Almost the entire difference was
protocol, not ability.

Intolerance of uncertainty explains ~45% of sensory-sensitivity variance in
autistic children (Wigham 2015), and test anxiety significantly moderates
dynamic-testing scores (Vogelaar 2017). Demand avoidance means the request to
"do a test" is itself the aversive part, independent of content.

So: the child is helping Souly, not being examined by it.
-----------------------------------------------------------------------------
"""

import json
import sqlite3
import statistics

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field

from app import onboarding_items as items
from app.db import db_dependency
from app.deps import get_student
from app.models import utc_now_iso

router = APIRouter(prefix="/api/students/{student_ext_id}/onboarding",
                   tags=["onboarding"])


class AttemptIn(BaseModel):
    item_code: str
    answer_index: int = Field(..., ge=0)
    prompts_used: int = Field(0, ge=0, le=4)
    first_attempt_ms: int = Field(0, ge=0)
    total_ms: int = Field(0, ge=0)
    attempts: int = Field(1, ge=1)


class SkipIn(BaseModel):
    item_code: str
    total_ms: int = Field(0, ge=0)


class PromptIn(BaseModel):
    item_code: str
    tier: int = Field(..., ge=1, le=4)


class InterestsIn(BaseModel):
    interests: list[str] = Field(default_factory=list, max_length=12)


class PreferencesIn(BaseModel):
    read_aloud: bool | None = None
    reduce_motion: bool | None = None
    font_size: str | None = Field(None, pattern="^(small|medium|large)$")
    theme: str | None = Field(None, pattern="^(light|purple|dark)$")


# =============================================================================
# The plan, shown before anything starts
# =============================================================================

@router.get("", summary="The whole activity, up front")
def get_activity(
    student: sqlite3.Row = Depends(get_student),
    conn: sqlite3.Connection = Depends(db_dependency),
) -> dict:
    """
    Everything the child is about to do, before they start.

    Showing the map first is the highest-leverage single thing in this whole
    flow: unpredictability, not difficulty, is what drives anxiety in this
    population.
    """
    done = conn.execute(
        "SELECT COUNT(*) c FROM onboarding_responses WHERE student_id = ?",
        (student["id"],),
    ).fetchone()["c"]

    return {
        "display_name": student["display_name"],
        "avatar": student["avatar"],
        "already_done": student["onboarded_at"] is not None,
        "responses_so_far": done,
        "plan": items.ACTIVITY_PLAN,
        "estimated_minutes": 8,
        "max_minutes": items.MAX_MINUTES,
        "interests": items.INTERESTS,
        "reasoning_items": [items.public_item(i) for i in items.REASONING_ITEMS],
        "modality_items": [items.public_item(i) for i in items.MODALITY_ITEMS],
        "preferences": items.PREFERENCE_QUESTIONS,
        "intro": (
            f"Hi {student['display_name']}. Before we start learning, will you "
            "help me? I want to find out how you like things explained, so I "
            "can do it your way. There are no marks and nothing is saved as a "
            "score. You can skip anything you don't fancy."
        ),
    }


# =============================================================================
# Prompts — fixed, standardised, one rung at a time
# =============================================================================

@router.post("/prompt", summary="Get the next fixed prompt for an item")
def get_prompt(
    payload: PromptIn,
    student: sqlite3.Row = Depends(get_student),
) -> dict:
    """
    Returns the pre-written prompt at this rung. Never generated.

    Caffrey, Fuchs & Fuchs (2008) found dynamic assessment's predictive
    advantage lives specifically in NON-CONTINGENT feedback — standardised,
    identical for every child. If the LLM improvised these, the prompt counts
    wouldn't be comparable between children and the score would be noise.
    """
    item = items.find_item(payload.item_code)
    if item is None:
        raise HTTPException(status_code=404, detail=f"No item {payload.item_code}")

    prompts = item.get("prompts") or []
    if payload.tier > len(prompts):
        raise HTTPException(status_code=422,
                            detail=f"Item has {len(prompts)} prompts")

    return {
        "item_code": payload.item_code,
        "tier": payload.tier,
        "text": prompts[payload.tier - 1],
        "is_final": payload.tier >= len(prompts),
        "next_tier": payload.tier + 1 if payload.tier < len(prompts) else None,
    }


# =============================================================================
# Recording work
# =============================================================================

@router.post("/attempt", summary="Record an answer")
def record_attempt(
    payload: AttemptIn,
    student: sqlite3.Row = Depends(get_student),
    conn: sqlite3.Connection = Depends(db_dependency),
) -> dict:
    item = items.find_item(payload.item_code)
    if item is None:
        raise HTTPException(status_code=404, detail=f"No item {payload.item_code}")

    correct = payload.answer_index == item["correct_index"]

    conn.execute(
        """
        INSERT INTO onboarding_responses (
            student_id, item_code, item_kind, prompts_used, solved, skipped,
            first_attempt_ms, total_ms, attempts, answer, created_at
        ) VALUES (?,?,?,?,?,0,?,?,?,?,?)
        """,
        (student["id"], payload.item_code, item["kind"], payload.prompts_used,
         int(correct), payload.first_attempt_ms, payload.total_ms,
         payload.attempts, str(payload.answer_index), utc_now_iso()),
    )

    # No score is returned and none is shown. The child is told the answer so
    # they aren't left hanging, warmly, and we move on.
    return {
        "recorded": True,
        "correct": correct,
        "correct_index": item["correct_index"],
        "feedback": ("Nice one." if correct
                     else "Good try — this one was tricky. Let's keep going."),
    }


@router.post("/skip", summary="Skip an item")
def skip_item(
    payload: SkipIn,
    student: sqlite3.Row = Depends(get_student),
    conn: sqlite3.Connection = Depends(db_dependency),
) -> dict:
    """
    Skipping is a first-class option and costs nothing.

    A child who can opt out is a child who doesn't have to refuse. Demand
    avoidance research is clear that being able to decline a request defuses
    it; forcing it escalates.
    """
    item = items.find_item(payload.item_code)
    kind = item["kind"] if item else "series"

    conn.execute(
        """
        INSERT INTO onboarding_responses (
            student_id, item_code, item_kind, prompts_used, solved, skipped,
            total_ms, created_at
        ) VALUES (?,?,?,0,NULL,1,?,?)
        """,
        (student["id"], payload.item_code, kind, payload.total_ms, utc_now_iso()),
    )
    return {"skipped": True}


@router.post("/interests", summary="Record what they like")
def record_interests(
    payload: InterestsIn,
    student: sqlite3.Row = Depends(get_student),
    conn: sqlite3.Connection = Depends(db_dependency),
) -> dict:
    valid = {i["code"] for i in items.INTERESTS}
    chosen = [c for c in payload.interests if c in valid]

    conn.execute(
        """
        INSERT INTO onboarding_responses (student_id, item_code, item_kind,
                                          answer, created_at)
        VALUES (?, 'INTERESTS', 'interests', ?, ?)
        """,
        (student["id"], json.dumps(chosen), utc_now_iso()),
    )
    return {"recorded": True, "interests": chosen}


@router.post("/preferences", summary="Record accessibility preferences")
def record_preferences(
    payload: PreferencesIn,
    student: sqlite3.Row = Depends(get_student),
    conn: sqlite3.Connection = Depends(db_dependency),
) -> dict:
    """
    These go straight into student_settings — they're settings, not traits.

    Kept deliberately separate from the learner profile so nobody later reads
    "prefers calm colours" as a measured cognitive characteristic.
    """
    fields = payload.model_dump(exclude_none=True)
    if fields:
        conn.execute("INSERT OR IGNORE INTO student_settings (student_id) VALUES (?)",
                     (student["id"],))
        values = [int(v) if isinstance(v, bool) else v for v in fields.values()]
        assignments = ", ".join(f"{k} = ?" for k in fields)
        conn.execute(
            f"UPDATE student_settings SET {assignments}, updated_at = ? "
            "WHERE student_id = ?",
            (*values, utc_now_iso(), student["id"]),
        )

    conn.execute(
        """
        INSERT INTO onboarding_responses (student_id, item_code, item_kind,
                                          answer, created_at)
        VALUES (?, 'PREFERENCES', 'preferences', ?, ?)
        """,
        (student["id"], json.dumps(fields), utc_now_iso()),
    )
    return {"recorded": True, "applied": fields}


# =============================================================================
# Scoring
# =============================================================================

@router.post("/finish", summary="Score the activity into a learner profile")
def finish(
    student: sqlite3.Row = Depends(get_student),
    conn: sqlite3.Connection = Depends(db_dependency),
) -> dict:
    profile = _score(conn, student["id"])
    conn.execute("UPDATE students SET onboarded_at = ? WHERE id = ?",
                 (utc_now_iso(), student["id"]))
    return profile


def _score(conn: sqlite3.Connection, student_id: int) -> dict:
    """
    Turn the raw responses into one learner profile row.

    The headline is `instruction_need`, following the three buckets Veerbeek &
    Vogelaar (2025) derived from a training-only graduated-prompts session:
    low / metacognitive / task_specific. Their task_specific group scored
    significantly lower on standardised maths and reading, so the split tracks
    something real.

    Everything here is stored with LOW confidence on purpose. Day-one numbers
    for this population are unreliable in both directions: anxiety and novelty
    push them down, masking pushes them up, and performance takes roughly four
    sessions to stabilise. This is a prior to be corrected, not a verdict.
    """
    rows = conn.execute(
        "SELECT * FROM onboarding_responses WHERE student_id = ? ORDER BY id",
        (student_id,),
    ).fetchall()

    reasoning = [r for r in rows
                 if r["item_kind"] in ("series", "analogy") and not r["skipped"]]
    skipped = [r for r in rows if r["skipped"]]

    # ---- The dynamic assessment score --------------------------------------
    prompts = [r["prompts_used"] for r in reasoning]
    mean_prompts = round(statistics.mean(prompts), 2) if prompts else None
    unaided = sum(1 for p in prompts if p == 0)
    rung1_rate = (round(sum(1 for p in prompts if p == 1) / len(prompts), 2)
                  if prompts else None)

    # Prompt rungs 1-2 are metacognitive ("what should you look at?"), 3-4 are
    # task-specific ("here is the actual rule"). Which half a child lives in is
    # the whole signal.
    task_specific_hits = sum(1 for p in prompts if p >= 3)
    metacognitive_hits = sum(1 for p in prompts if 1 <= p <= 2)

    if not prompts:
        instruction_need = "metacognitive"     # no evidence; middle default
    elif task_specific_hits >= max(2, len(prompts) // 2):
        instruction_need = "task_specific"
    elif mean_prompts is not None and mean_prompts < 0.7:
        instruction_need = "low"
    else:
        instruction_need = "metacognitive"

    # ---- Pacing -------------------------------------------------------------
    latencies = [r["first_attempt_ms"] for r in reasoning
                 if r["first_attempt_ms"] and r["first_attempt_ms"] > 300]
    median_latency = int(statistics.median(latencies)) if latencies else None
    variability = None
    if len(latencies) >= 3 and median_latency:
        variability = round(statistics.pstdev(latencies) / median_latency, 2)

    # Reaching the final rung repeatedly, fast, without much trying.
    gives_up = bool(prompts and sum(1 for p in prompts if p == 4) >= 2
                    and (median_latency or 9999) < 4000)

    # ---- Reading vs listening ------------------------------------------------
    reading = next((r for r in rows if r["item_kind"] == "reading"
                    and not r["skipped"]), None)
    listening = next((r for r in rows if r["item_kind"] == "listening"
                      and not r["skipped"]), None)

    modality_gap = None
    if reading is not None and listening is not None:
        # Simple and honest with two items: +1 listening only, -1 reading only,
        # 0 both or neither. Two items cannot support anything finer, and
        # pretending otherwise would be false precision.
        modality_gap = float((listening["solved"] or 0) - (reading["solved"] or 0))

    # ---- Interests -----------------------------------------------------------
    interests_row = next((r for r in rows if r["item_kind"] == "interests"), None)
    interests = []
    if interests_row and interests_row["answer"]:
        try:
            interests = json.loads(interests_row["answer"])
        except (json.JSONDecodeError, TypeError):
            interests = []

    # ---- Masking flag ---------------------------------------------------------
    # Fast and flawless, but inconsistent underneath. Don't immediately pitch
    # this child at the hardest level — day-one performance can be a
    # performance.
    possible_masking = bool(
        unaided >= 4
        and median_latency is not None and median_latency < 2500
        and variability is not None and variability > 0.8
    )

    # ---- Confidence ----------------------------------------------------------
    # Caps at 0.5. One short session, on an unfamiliar system, on day one is
    # never worth more than that.
    confidence = 0.15 + 0.07 * len(reasoning)
    if skipped:
        confidence -= 0.05 * len(skipped)
    if modality_gap is not None:
        confidence += 0.05
    confidence = round(max(0.1, min(0.5, confidence)), 2)

    conn.execute(
        """
        INSERT INTO learner_profiles (
            student_id, instruction_need, confidence,
            mean_prompts_needed, rung1_sufficient_rate,
            items_attempted, items_solved_unaided,
            median_first_attempt_ms, latency_variability, gives_up_early,
            modality_gap, reading_correct, listening_correct, reading_time_ms,
            interests, possible_masking, incomplete, source, created_at
        ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?, 'onboarding', ?)
        """,
        (student_id, instruction_need, confidence,
         mean_prompts, rung1_rate, len(reasoning), unaided,
         median_latency, variability, int(gives_up),
         modality_gap,
         reading["solved"] if reading else None,
         listening["solved"] if listening else None,
         reading["total_ms"] if reading else None,
         json.dumps(interests), int(possible_masking),
         int(len(skipped) > 2), utc_now_iso()),
    )

    return {
        "instruction_need": instruction_need,
        "confidence": confidence,
        "mean_prompts_needed": mean_prompts,
        "items_attempted": len(reasoning),
        "items_solved_unaided": unaided,
        "median_first_attempt_ms": median_latency,
        "modality_gap": modality_gap,
        "interests": interests,
        "possible_masking": possible_masking,
        "skipped": len(skipped),
        "summary": _plain_summary(instruction_need, modality_gap, interests),
    }


def _plain_summary(instruction_need: str, modality_gap: float | None,
                   interests: list[str]) -> str:
    """
    One sentence a teacher or parent could read.

    Worth having: a profile nobody can interpret is a profile nobody will
    challenge, and this one should be challengeable.
    """
    pitch = {
        "low": "works well with very little help — give the question first "
               "and stay out of the way",
        "metacognitive": "does well with a nudge about strategy rather than "
                         "being re-taught the content",
        "task_specific": "needs the idea itself explained again before "
                         "questions make sense",
    }[instruction_need]

    parts = [f"Learns best when Souly {pitch}."]

    if modality_gap is not None and modality_gap > 0:
        parts.append("Understood the spoken story but not the written one, so "
                     "reading aloud is on by default.")
    elif modality_gap is not None and modality_gap < 0:
        parts.append("Read comfortably; audio is optional.")

    if interests:
        parts.append(f"Likes: {', '.join(interests[:3])}.")

    parts.append("Based on one short session, so treat it as a starting guess.")
    return " ".join(parts)


# =============================================================================
# Reading back
# =============================================================================

@router.get("/profile", summary="This student's current learner profile")
def get_profile(
    student: sqlite3.Row = Depends(get_student),
    conn: sqlite3.Connection = Depends(db_dependency),
) -> dict:
    row = conn.execute(
        "SELECT * FROM v_current_learner_profile WHERE student_id = ?",
        (student["id"],),
    ).fetchone()
    if row is None:
        return {"has_profile": False,
                "message": "This student hasn't done the entry activity yet."}

    profile = dict(row)
    try:
        profile["interests"] = json.loads(profile["interests"] or "[]")
    except (json.JSONDecodeError, TypeError):
        profile["interests"] = []
    profile["has_profile"] = True
    profile["summary"] = _plain_summary(
        profile["instruction_need"], profile["modality_gap"], profile["interests"]
    )
    return profile


@router.delete("", summary="Clear the activity so it can be retaken")
def reset(
    student: sqlite3.Row = Depends(get_student),
    conn: sqlite3.Connection = Depends(db_dependency),
) -> dict:
    conn.execute("DELETE FROM onboarding_responses WHERE student_id = ?",
                 (student["id"],))
    conn.execute("DELETE FROM learner_profiles WHERE student_id = ? "
                 "AND source = 'onboarding'", (student["id"],))
    conn.execute("UPDATE students SET onboarded_at = NULL WHERE id = ?",
                 (student["id"],))
    return {"reset": True}
