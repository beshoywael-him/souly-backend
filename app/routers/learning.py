"""
Subjects, lessons, and quizzes — the actual learning loop.

WHAT A LESSON IS SINCE schema_v5
--------------------------------
A lesson is a lesson in a real Ministry book. `topics` holds one row per
lesson, `curriculum_pages` maps that lesson to the pages it runs across, and
the pages themselves stay in the PDF on disk. So:

    topic_id            the lesson's identity
    curriculum_pages    its pages, in book order
    page_renditions     what Souly said about each page, to THIS child

There is no `lessons` table and no `lesson_steps` table any more. The screens
below serve the plan of lessons ahead, the pages of one lesson, and the
practice built from them.

The quiz endpoints are still the heart of the demo: a student answers, mastery
moves, stars are awarded, and the next question adapts to how they're doing.
Every one of those effects is a real database write.
"""

import json
import random
import sqlite3

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import FileResponse
from pydantic import BaseModel, Field

from app import economy
from app.db import db_dependency
from app.deps import get_student
from app.services import curriculum, llm, tutor

router = APIRouter(prefix="/api/students/{student_ext_id}", tags=["learning"])


# =============================================================================
# Models
# =============================================================================

class PageComplete(BaseModel):
    page: int = Field(..., ge=1)
    duration_s: int = Field(0, ge=0)
    # Whether the child went back to re-read. A signal of difficulty that
    # costs nothing to collect and feeds the stall baseline.
    went_back: bool = False
    replayed_audio: bool = False


class GenerateRequest(BaseModel):
    """Ask Souly to write fresh practice questions from the book page."""
    count: int = Field(4, ge=1, le=8)
    page: int | None = None


class QuizStart(BaseModel):
    topic_id: int | None = None
    subject_code: str | None = None
    total_questions: int = Field(10, ge=1, le=25)


class QuizAnswer(BaseModel):
    answer_index: int = Field(..., ge=0)
    # Recorded for the parent report and stall calibration only. It has NO
    # effect on score. Autistic people are measurably slower across the board
    # (Zapparrata et al. 2023, g = .35), so scoring speed would systematically
    # penalise the exact cohort this app is for.
    duration_s: int = Field(0, ge=0)
    hints_used: int = Field(0, ge=0)


# =============================================================================
# Helpers
# =============================================================================

def _student_grade(student: sqlite3.Row) -> str:
    return str(student["grade"] or "").strip()


def _lesson_plan(conn: sqlite3.Connection, student_id: int, grade: str,
                 subject_code: str | None = None) -> list[dict]:
    """
    The plan of lessons ahead, in book order, with this child's progress on it.

    Reads `v_curriculum_lessons` — one row per lesson with its page span — and
    joins the topic that ingest_curriculum.py created for it. Grade gating
    happens here and nowhere else: a Primary 5 book must not appear for a
    Primary 6 child.
    """
    sql = """
        SELECT v.book_id, v.book_code, v.book_title, v.subject, v.grade,
               v.unit, v.lesson, v.lesson_order, v.first_page, v.last_page,
               v.page_count, v.is_verified,
               t.id AS topic_id, t.title AS topic_title, t.code AS topic_code,
               COALESCE(lp.pages_completed, 0) AS pages_completed,
               COALESCE(lp.is_complete, 0)     AS is_complete,
               COALESCE(lp.last_page, 0)       AS last_page_seen,
               COALESCE(m.level, 0.0)          AS mastery
        FROM v_curriculum_lessons v
        JOIN topics t ON t.book_id = v.book_id AND t.lesson_label = v.lesson
        LEFT JOIN lesson_progress lp ON lp.topic_id = t.id AND lp.student_id = ?
        LEFT JOIN mastery m          ON m.topic_id = t.id AND m.student_id = ?
        WHERE v.grade = ?
    """
    params: list[object] = [student_id, student_id, grade]

    if subject_code:
        sql += """ AND UPPER(COALESCE(
                       (SELECT b.subject_code FROM curriculum_books b WHERE b.id = v.book_id),
                       v.subject)) = ?"""
        params.append(subject_code.upper())

    sql += " ORDER BY v.book_id, v.lesson_order, v.first_page"

    rows = conn.execute(sql, params).fetchall()

    plan = []
    current_marked = False
    for row in rows:
        done = bool(row["is_complete"])
        # Exactly one lesson is "current": the first one not finished. Every
        # lesson after it is "ahead" — that is what makes this a plan rather
        # than a list.
        if done:
            status = "done"
        elif not current_marked:
            status = "current"
            current_marked = True
        else:
            status = "ahead"

        plan.append({
            "topic_id": row["topic_id"],
            "topic_code": row["topic_code"],
            "title": row["topic_title"],
            "lesson": row["lesson"],
            "unit": row["unit"],
            "lesson_order": row["lesson_order"],
            "book_id": row["book_id"],
            "book_code": row["book_code"],
            "book_title": row["book_title"],
            "subject": row["subject"],
            "grade": row["grade"],
            "first_page": row["first_page"],
            "last_page": row["last_page"],
            "page_count": row["page_count"],
            "pages_completed": row["pages_completed"],
            "progress_pct": round(
                (row["pages_completed"] / max(row["page_count"], 1)) * 100
            ),
            "is_complete": done,
            "mastery": round(row["mastery"], 3),
            "is_verified": bool(row["is_verified"]),
            "status": status,
        })
    return plan


def _lesson_or_404(conn: sqlite3.Connection, topic_id: int) -> sqlite3.Row:
    lesson = conn.execute(
        """
        SELECT t.id, t.code, t.title, t.summary, t.is_verified, t.subject_id,
               t.lesson_label, t.book_id,
               b.code AS book_code, b.title AS book_title, b.subject AS subject,
               b.grade AS grade, b.term AS term, b.sha256 AS book_sha,
               b.is_verified AS book_verified,
               COALESCE(sub.name, t.subject) AS subject_name,
               sub.code AS subject_code
        FROM topics t
        LEFT JOIN curriculum_books b ON b.id = t.book_id
        LEFT JOIN subjects sub       ON sub.id = t.subject_id
        WHERE t.id = ?
        """,
        (topic_id,),
    ).fetchone()
    if lesson is None:
        raise HTTPException(status_code=404, detail=f"Lesson {topic_id} not found")
    return lesson


def _practice_question(conn: sqlite3.Connection, topic_id: int,
                       student_id: int) -> dict | None:
    """
    The lesson's "Try It Yourself" card.

    `correct_index` is deliberately NOT returned — the client asks the server
    to check, so the answer never sits in the page source where a student can
    read it.

    Questions written for another child are excluded: a generated item is
    pitched at the mastery of the student it was written for.
    """
    row = conn.execute(
        """
        SELECT id, prompt, options_json, hint, source_page
        FROM questions
        WHERE topic_id = ?
          AND review_status != 'rejected'
          AND (student_id IS NULL OR student_id = ?)
        ORDER BY (student_id IS NULL), difficulty
        LIMIT 1
        """,
        (topic_id, student_id),
    ).fetchone()
    if row is None:
        return None
    return {
        "id": row["id"],
        "prompt": row["prompt"],
        "options": json.loads(row["options_json"]),
        "hint": row["hint"],
        "source_page": row["source_page"],
    }


# =============================================================================
# Subjects
# =============================================================================

@router.get("/subjects", summary="Subject cards with this student's progress")
def list_subjects(
    student: sqlite3.Row = Depends(get_student),
    conn: sqlite3.Connection = Depends(db_dependency),
) -> dict:
    grade = _student_grade(student)

    rows = conn.execute(
        "SELECT * FROM v_subject_progress WHERE student_id = ? ORDER BY sort_order",
        (student["id"],),
    ).fetchall()

    # How many lessons each subject actually has FOR THIS CHILD'S GRADE. The
    # subject cards themselves are grade-agnostic; the content is not.
    lessons_by_subject: dict[str, int] = {}
    for row in conn.execute(
        """
        SELECT UPPER(COALESCE(b.subject_code, b.subject)) AS code,
               COUNT(DISTINCT b.id || '|' || p.lesson) AS lessons
        FROM curriculum_pages p
        JOIN curriculum_books b ON b.id = p.book_id
        WHERE b.grade = ? AND b.is_verified = 1
        GROUP BY UPPER(COALESCE(b.subject_code, b.subject))
        """,
        (grade,),
    ):
        lessons_by_subject[row["code"]] = row["lessons"]

    subjects = []
    for r in rows:
        lesson_count = lessons_by_subject.get((r["subject_code"] or "").upper(), 0)
        subjects.append({
            "id": r["subject_id"],
            "code": r["subject_code"],
            "name": r["subject_name"],
            "icon": r["icon"],
            "difficulty": r["difficulty"],
            "color_from": r["color_from"],
            "color_to": r["color_to"],
            "progress_pct": int(r["progress_pct"]),
            "topic_count": lesson_count,
            "lesson_count": lesson_count,
            "topics_started": r["topics_started"],
            "has_content": lesson_count > 0,
        })

    completed = sum(1 for s in subjects if s["progress_pct"] >= 80)
    xp = conn.execute(
        "SELECT COALESCE(SUM(xp_delta),0) AS xp FROM activity_log WHERE student_id = ?",
        (student["id"],),
    ).fetchone()["xp"]
    progress = economy.level_progress(xp)

    # Only subjects that actually have lessons for this grade can be
    # recommended. Suggesting an empty subject sends the student to a dead end
    # — and reads as broken rather than as "no content loaded yet".
    teachable = [s for s in subjects if s["has_content"]]

    # Prefer continuing something already started; momentum beats a fresh
    # start. Otherwise point at the first subject that has any content.
    started = [s for s in teachable if s["topics_started"] > 0 and s["progress_pct"] < 100]
    if started:
        recommended = min(started, key=lambda s: s["progress_pct"])
        message = (f"I recommend continuing {recommended['name']} today. "
                   f"You're at {recommended['progress_pct']}% — keep going!")
    elif teachable:
        recommended = teachable[0]
        message = f"Let's start with {recommended['name']} today!"
    else:
        recommended = None
        message = None

    # The empty state has to be honest and specific. Right now Primary 6 has
    # no books at all, and "nothing for your grade yet" is a true sentence a
    # child can act on; a spinner or a 500 is not.
    any_book = conn.execute(
        "SELECT COUNT(*) c FROM curriculum_books WHERE is_verified = 1"
    ).fetchone()["c"]

    if teachable:
        empty_state = None
    elif any_book:
        empty_state = {
            "reason": "no_books_for_grade",
            "title": f"Nothing for grade {grade} yet" if grade else "Nothing for your grade yet",
            "message": ("Your books haven't been added yet. Other years are "
                        "loaded, so this isn't broken — ask your teacher to "
                        "add yours."),
        }
    else:
        empty_state = {
            "reason": "no_books",
            "title": "No books loaded yet",
            "message": "Ask your teacher to add the curriculum books.",
        }

    return {
        "subjects": subjects,
        "grade": grade,
        "stats": {
            "subjects_done": completed,
            "subjects_total": len(subjects),
            "total_xp": xp,
            "level": progress["level"],
            "level_title": progress["title"],
        },
        "has_content": bool(teachable),
        "empty_state": empty_state,
        "recommendation": (
            {"subject_code": recommended["code"], "message": message}
            if recommended else None
        ),
    }


@router.get("/subjects/{subject_code}/lessons",
            summary="The plan of lessons ahead, in book order")
def list_lessons(
    subject_code: str,
    student: sqlite3.Row = Depends(get_student),
    conn: sqlite3.Connection = Depends(db_dependency),
) -> list[dict]:
    grade = _student_grade(student)
    plan = _lesson_plan(conn, student["id"], grade, subject_code)

    if not plan:
        # 404 rather than an empty list: the client distinguishes "this
        # subject has nothing for you" from "here are zero lessons", and the
        # first one gets the honest empty state rather than a blank screen.
        raise HTTPException(
            status_code=404,
            detail=(f"No grade {grade} lessons for subject '{subject_code}'. "
                    f"Load a book with scripts/ingest_curriculum.py."),
        )
    return plan


@router.get("/plan", summary="Every lesson ahead, across subjects")
def full_plan(
    student: sqlite3.Row = Depends(get_student),
    conn: sqlite3.Connection = Depends(db_dependency),
) -> dict:
    grade = _student_grade(student)
    plan = _lesson_plan(conn, student["id"], grade)
    current = next((p for p in plan if p["status"] == "current"), None)
    return {
        "grade": grade,
        "lessons": plan,
        "current": current,
        "done": sum(1 for p in plan if p["is_complete"]),
        "total": len(plan),
    }


# =============================================================================
# Lessons
# =============================================================================

@router.get("/lessons/{topic_id}", summary="A lesson with all its pages")
def get_lesson(
    topic_id: int,
    student: sqlite3.Row = Depends(get_student),
    conn: sqlite3.Connection = Depends(db_dependency),
) -> dict:
    lesson = _lesson_or_404(conn, topic_id)
    pages = curriculum.lesson_pages(conn, topic_id)

    progress = conn.execute(
        "SELECT * FROM lesson_progress WHERE student_id = ? AND topic_id = ?",
        (student["id"], topic_id),
    ).fetchone()
    pages_done = progress["pages_completed"] if progress else 0

    page_list = []
    for index, page in enumerate(pages):
        page_list.append({
            "page_id": page["id"],
            "page": page["page"],
            "ordinal": index + 1,
            "image_url": (
                f"/api/students/{student['external_id']}"
                f"/curriculum/pages/{page['id']}/image"
            ),
            "has_text": bool(curriculum.page_text(page["book_code"], page["page"])),
            "completed": index < pages_done,
        })

    return {
        "id": lesson["id"],
        "code": lesson["code"],
        "title": lesson["title"],
        "lesson": lesson["lesson_label"],
        "summary": lesson["summary"],
        "book_id": lesson["book_id"],
        "book_title": lesson["book_title"],
        "subject_name": lesson["subject_name"],
        "subject_code": lesson["subject_code"],
        "grade": lesson["grade"],
        "term": lesson["term"],
        # Verified means a human has eyeballed the book. Both gates have to be
        # open before anything here is taught from.
        "is_verified": bool(lesson["is_verified"] and lesson["book_verified"]),
        "pages": page_list,
        "total_pages": len(page_list),
        "pages_completed": pages_done,
        "last_page": progress["last_page"] if progress else 0,
        "is_complete": bool(progress["is_complete"]) if progress else False,
        "practice_question": _practice_question(conn, topic_id, student["id"]),
    }


@router.get("/curriculum/pages/{page_id}/image", summary="The page, as printed")
def page_image(
    page_id: int,
    student: sqlite3.Row = Depends(get_student),
    conn: sqlite3.Connection = Depends(db_dependency),
):
    """
    The rendered page from the PDF.

    The books are scans, so this is the canon in the most literal sense: the
    child looks at the Ministry's own page, and what Souly says about it is
    the layer on top. Rendered once by scripts/render_pages.py.
    """
    page = curriculum.page_row(conn, page_id)
    if page is None:
        raise HTTPException(status_code=404, detail=f"Page {page_id} not found")

    path = curriculum.image_path(page["book_code"], page["page"])
    if path is None:
        raise HTTPException(
            status_code=404,
            detail=(f"Page {page['page']} of {page['book_title']} has not been "
                    f"rendered. Run scripts/render_pages.py."),
        )
    return FileResponse(path, media_type="image/jpeg")


@router.get("/curriculum/pages/{page_id}/illustration",
            summary="The generated picture for this page, for this child")
def page_illustration(
    page_id: int,
    student: sqlite3.Row = Depends(get_student),
    conn: sqlite3.Connection = Depends(db_dependency),
):
    """
    Generated on first request, then served from disk for everyone after.

    The prompt is NOT taken from the request. It is read from the rendition
    this child already has for this page, which the model wrote from the book.
    A client that could pass its own prompt would be an open image generator
    wearing a lesson's clothes.

    Slow the first time — several seconds — which is exactly why it is a
    separate request: the child is reading the explanation while this arrives,
    rather than staring at a spinner waiting for both.
    """
    row = conn.execute(
        "SELECT visual_json FROM page_renditions "
        "WHERE student_id = ? AND page_id = ? AND mode = 'lesson'",
        (student["id"], page_id),
    ).fetchone()

    visual = tutor._load_visual(row["visual_json"]) if row else None
    if not visual or not visual.get("scene") or not visual.get("key"):
        raise HTTPException(status_code=404,
                            detail="This page has no illustration.")

    path = curriculum.illustration_path(visual["key"])
    if path.exists() and path.stat().st_size > 0:
        return FileResponse(path, media_type="image/png")

    image, mime, error = llm.generate_image(visual["scene"])
    if not image:
        # A lesson whose picture failed is still a lesson. The client hides
        # the frame and the child reads on.
        raise HTTPException(status_code=503,
                            detail=f"Could not draw that yet: {error}")

    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(image)
    return FileResponse(path, media_type=mime or "image/png")


@router.get("/lessons/{topic_id}/pages/{page_no}",
            summary="One page, explained for this child")
def get_page(
    topic_id: int,
    page_no: int,
    student: sqlite3.Row = Depends(get_student),
    conn: sqlite3.Connection = Depends(db_dependency),
) -> dict:
    """
    The rendition: this page, written for this child, grounded in the page.

    Cached in `page_renditions`, so coming back to a page shows the same
    lesson rather than a fresh one — and so it still works when the network
    doesn't.
    """
    lesson = _lesson_or_404(conn, topic_id)
    pages = curriculum.lesson_pages(conn, topic_id)
    match = next((p for p in pages if p["page"] == page_no), None)
    if match is None:
        raise HTTPException(
            status_code=404,
            detail=f"Page {page_no} is not part of lesson {topic_id}",
        )

    if not (lesson["is_verified"] and lesson["book_verified"]):
        raise HTTPException(
            status_code=409,
            detail=("This book has not been verified, so Souly will not teach "
                    "from it. Mark it verified once a human has checked it."),
        )

    reply = tutor.rendition(conn, student["id"], match["id"])
    ordinal = pages.index(match) + 1

    return {
        "topic_id": topic_id,
        "title": lesson["title"],
        "page_id": match["id"],
        "page": match["page"],
        "ordinal": ordinal,
        "total_pages": len(pages),
        "book_title": lesson["book_title"],
        # The picture that goes with the lesson. `visual` is a spec the app
        # draws; `illustration_url` is only set when the spec calls for a
        # generated picture, and it is fetched separately so the words are on
        # screen while it is still being drawn.
        "visual": reply.visual,
        "illustration_url": (
            f"/api/students/{student['external_id']}"
            f"/curriculum/pages/{match['id']}/illustration"
            if (reply.visual or {}).get("scene") else None
        ),
        "explanation": reply.text,
        "engine": reply.engine,
        "cached": reply.cached,
        "grounded": reply.grounded,
        "source_refs": reply.source_refs,
        # What was changed for this child. Shown on screen, so "it adapts to
        # the learner" is something a teacher can check against the lesson in
        # front of them rather than a claim in a slide.
        "adapted_for": reply.adapted_for,
        "error": reply.error,
    }


@router.post("/lessons/{topic_id}/page", summary="Mark a page worked through")
def complete_page(
    topic_id: int,
    payload: PageComplete,
    student: sqlite3.Row = Depends(get_student),
    conn: sqlite3.Connection = Depends(db_dependency),
) -> dict:
    sid = student["id"]
    lesson = _lesson_or_404(conn, topic_id)

    pages = curriculum.lesson_pages(conn, topic_id)
    if not pages:
        raise HTTPException(
            status_code=409,
            detail=f"Lesson {topic_id} has no pages mapped to it",
        )

    match = next((p for p in pages if p["page"] == payload.page), None)
    if match is None:
        raise HTTPException(
            status_code=404,
            detail=f"Page {payload.page} is not part of lesson {topic_id}",
        )

    total_pages = len(pages)
    # Position within the lesson, not the printed page number: a lesson that
    # starts on page 7 is one page in, not seven.
    ordinal = pages.index(match) + 1

    conn.execute(
        """
        INSERT INTO lesson_progress (student_id, topic_id, pages_completed,
                                     last_page, started_at, updated_at)
        VALUES (?, ?, ?, ?, ?, ?)
        ON CONFLICT(student_id, topic_id) DO UPDATE SET
            -- MAX, not +1: re-reading a page must not inflate progress.
            pages_completed = MAX(lesson_progress.pages_completed, excluded.pages_completed),
            last_page       = excluded.last_page,
            updated_at      = excluded.updated_at
        """,
        (sid, topic_id, ordinal, payload.page,
         economy.utc_now(), economy.utc_now()),
    )

    # Record how long this page took. Feeds the per-child stall threshold — a
    # global "15 seconds means stuck" constant would mislabel this cohort,
    # since autistic people are measurably slower across the board.
    conn.execute(
        """
        INSERT INTO page_activity (student_id, page_id, seconds_on_page,
                                   replayed_audio, went_back)
        VALUES (?,?,?,?,?)
        """,
        (sid, match["id"], payload.duration_s,
         int(payload.replayed_audio), int(payload.went_back)),
    )

    progress = conn.execute(
        "SELECT * FROM lesson_progress WHERE student_id = ? AND topic_id = ?",
        (sid, topic_id),
    ).fetchone()

    just_completed = False
    if progress["pages_completed"] >= total_pages and not progress["is_complete"]:
        conn.execute(
            "UPDATE lesson_progress SET is_complete = 1, completed_at = ? "
            "WHERE student_id = ? AND topic_id = ?",
            (economy.utc_now(), sid, topic_id),
        )
        just_completed = True

    if just_completed:
        award = economy.award(
            conn, sid, "lesson_complete",
            stars=economy.STARS_PER_LESSON_COMPLETE,
            xp=economy.XP_PER_LESSON_COMPLETE,
            topic_id=topic_id, subject_id=lesson["subject_id"],
            reference_id=topic_id, duration_s=payload.duration_s,
        )
        economy.mark_challenge_step(conn, sid, "lesson")
    else:
        award = economy.award(
            conn, sid, "lesson_step",
            stars=economy.STARS_PER_LESSON_STEP,
            xp=economy.XP_PER_LESSON_STEP,
            topic_id=topic_id, subject_id=lesson["subject_id"],
            reference_id=topic_id, duration_s=payload.duration_s,
        )

    return {
        "pages_completed": progress["pages_completed"],
        "total_pages": total_pages,
        "lesson_complete": just_completed,
        "award": award.to_dict(),
    }


# =============================================================================
# Checking a single question, and generating new ones
# =============================================================================

class SingleAnswer(BaseModel):
    answer_index: int = Field(..., ge=0)
    seconds_taken: int = Field(0, ge=0)
    attempts_before: int = Field(0, ge=0)


# How many wrong attempts before lesson practice gives up and shows the answer.
#
# A wrong answer routes into the hint ladder instead of the answer, and the
# ladder's own tier 4 is where the answer lives — so a child gets a nudge, a
# worked example with different numbers, then a step-by-step, and only then
# the answer. Being told "wrong, it was B" teaches nothing except that you got
# it wrong.
#
# But a child must never be trapped either. After this many attempts the
# answer is shown regardless, warmly, because grinding a struggling child
# against the same item is its own harm.
MAX_PRACTICE_ATTEMPTS = 4


@router.post("/questions/{question_id}/check", summary="Check one answer")
def check_single_answer(
    question_id: int,
    payload: SingleAnswer,
    student: sqlite3.Row = Depends(get_student),
    conn: sqlite3.Connection = Depends(db_dependency),
) -> dict:
    """
    Grade the lesson's practice question.

    The client never receives `correct_index` up front, so grading has to
    happen here. That is the point: if the answer shipped with the page, any
    student could read it out of devtools and every score in the demo would
    be meaningless.

    Note what this does NOT do:

    * Award stars for a right answer or deduct for a wrong one. Lesson
      practice is for thinking, not scoring. Stars come from finishing the
      page either way.

    * Hand back the answer when the child gets it wrong. A wrong answer
      returns `offer_hint` and the tier to start at, and the answer itself
      only arrives from the hint ladder's tier 4, after a nudge, a worked
      example with different numbers, and a step-by-step. Telling a child
      "wrong — it was B" teaches nothing except that they were wrong, and for
      a child who is already struggling that is the whole lesson they take
      away.

      The exception is `MAX_PRACTICE_ATTEMPTS`: nobody gets trapped on one
      question.
    """
    question = conn.execute(
        "SELECT * FROM questions WHERE id = ?", (question_id,)
    ).fetchone()
    if question is None:
        raise HTTPException(status_code=404, detail=f"Question {question_id} not found")

    options = json.loads(question["options_json"])
    if payload.answer_index >= len(options):
        raise HTTPException(
            status_code=422,
            detail=f"answer_index {payload.answer_index} out of range "
                   f"(question has {len(options)} options)",
        )

    is_correct = payload.answer_index == question["correct_index"]

    # Close the loop on any hints used for this question, so we can measure
    # whether the ladder actually helped.
    tutor.resolve_hints(conn, student["id"], question_id, is_correct)

    if question["topic_id"]:
        economy.update_mastery(conn, student["id"], question["topic_id"], is_correct)

    conn.execute(
        """
        INSERT INTO attempts (student_id, topic_id, question_text, expected_answer,
                              student_answer, input_mode, is_correct,
                              asked_at, answered_at)
        VALUES (?,?,?,?,?, 'touch', ?,?,?)
        """,
        (student["id"], question["topic_id"], question["prompt"],
         options[question["correct_index"]], options[payload.answer_index],
         int(is_correct), economy.utc_now(), economy.utc_now()),
    )

    attempts = payload.attempts_before + 1
    out_of_tries = attempts >= MAX_PRACTICE_ATTEMPTS

    # The answer is only in this response when the child got it right, or when
    # they have run out of tries. Otherwise it routes into the hint ladder.
    reveal = is_correct or out_of_tries

    return {
        "correct": is_correct,
        "correct_index": question["correct_index"] if reveal else None,
        "correct_answer": options[question["correct_index"]] if reveal else None,
        "explanation": question["explanation"] if reveal else None,
        "attempts": attempts,
        "attempts_left": max(0, MAX_PRACTICE_ATTEMPTS - attempts),
        "can_retry": not is_correct and not out_of_tries,
        # What the client should offer next. A wrong answer routes into the
        # hint ladder rather than just saying "no".
        "offer_hint": not is_correct,
        # Climb one rung per wrong attempt, so the help gets more concrete as
        # the child keeps missing it — and never skips ahead to the answer.
        "next_tier": min(attempts, 4) if not is_correct else None,
        "message": (
            None if is_correct
            else ("Not quite — and that's fine. Let me give you a hand."
                  if not out_of_tries else
                  "That one was tricky. Here's how it works.")
        ),
    }


@router.post("/lessons/{topic_id}/generate-practice",
             summary="Have Souly write fresh questions from the page")
def generate_practice(
    topic_id: int,
    payload: GenerateRequest,
    student: sqlite3.Row = Depends(get_student),
    conn: sqlite3.Connection = Depends(db_dependency),
) -> dict:
    """
    The LLM reading the book page and writing new practice items from it.

    This is the live half of hybrid generation, and the thing a static bank
    cannot do: a child who repeats a lesson gets different questions rather
    than the same four forever.

    Three safeguards, because a wrong question in front of a child with a
    learning disability is worse than a boring one:
      * generation only from a book a human marked verified
      * every item validated — shape, duplicate options, index range, and
        whether the prompt or hint leaks the answer
      * stored review_status='pending' with the page it came from, so a
        teacher sees exactly what the machine wrote and where it got it

    Falls back to the approved bank when generation is unavailable, so the
    student always gets practice.
    """
    try:
        result = tutor.generate_questions(
            conn, topic_id, count=payload.count,
            student_id=student["id"], page=payload.page,
        )
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc

    questions = [
        {
            "id": q.get("id"),
            "prompt": q["prompt"],
            "options": q["options"],
            "hint": q.get("hint"),
            "origin": "generated",
        }
        for q in result["questions"]
    ]

    fell_back = False
    if not questions:
        # Generation unavailable or everything got rejected. Use the bank.
        rows = conn.execute(
            "SELECT id, prompt, options_json, hint FROM questions "
            "WHERE topic_id = ? AND review_status = 'approved' "
            "AND student_id IS NULL ORDER BY difficulty LIMIT ?",
            (topic_id, payload.count),
        ).fetchall()
        questions = [
            {"id": r["id"], "prompt": r["prompt"],
             "options": json.loads(r["options_json"]), "hint": r["hint"],
             "origin": "bank"}
            for r in rows
        ]
        fell_back = True

    return {
        "questions": questions,
        "engine": result.get("engine"),
        "generated": result.get("generated", 0),
        "accepted": result.get("accepted", 0),
        "rejected": result.get("rejected", 0),
        "rejection_reasons": result.get("reasons", []),
        "fell_back_to_bank": fell_back,
        "latency_ms": result.get("latency_ms", 0),
    }


@router.get("/questions/review", summary="Generated questions awaiting review")
def questions_for_review(
    limit: int = 50,
    student: sqlite3.Row = Depends(get_student),
    conn: sqlite3.Connection = Depends(db_dependency),
) -> list[dict]:
    """
    Everything the LLM wrote that no human has checked.

    Exists so "the AI generates questions" is an auditable claim rather than a
    hopeful one — a teacher can read every generated item next to the page of
    the book it came from, and see which child it was written for.
    """
    rows = conn.execute(
        "SELECT * FROM v_questions_for_review LIMIT ?", (min(limit, 200),)
    ).fetchall()
    out = []
    for r in rows:
        item = dict(r)
        try:
            item["options"] = json.loads(item.pop("options_json"))
        except (json.JSONDecodeError, TypeError, KeyError):
            item["options"] = []
        out.append(item)
    return out


# =============================================================================
# Quiz
# =============================================================================

def _pick_questions(
    conn: sqlite3.Connection,
    student_id: int,
    topic_id: int | None,
    subject_code: str | None,
    count: int,
) -> list[sqlite3.Row]:
    """
    Choose questions, easiest first, biased toward the student's current level.

    A student at 20% mastery gets difficulty 1-2; at 80% they get 3-5. Opening
    a quiz with a question they can't do is how you lose a child's engagement
    in the first ten seconds.

    Only approved questions. A generated item sits at 'pending' until a human
    reads it, and a quiz is scored — so it is the one place an unreviewed
    question must never appear.
    """
    sql = """
        SELECT q.* FROM questions q
        JOIN topics t ON t.id = q.topic_id
        LEFT JOIN subjects sub ON sub.id = t.subject_id
        WHERE q.review_status = 'approved'
          AND (q.student_id IS NULL OR q.student_id = ?)
    """
    params: list[object] = [student_id]

    if topic_id is not None:
        sql += " AND q.topic_id = ?"
        params.append(topic_id)
    if subject_code is not None:
        sql += " AND sub.code = ?"
        params.append(subject_code.upper())

    candidates = conn.execute(sql, params).fetchall()
    if not candidates:
        return []

    if topic_id is not None:
        row = conn.execute(
            "SELECT level FROM mastery WHERE student_id = ? AND topic_id = ?",
            (student_id, topic_id),
        ).fetchone()
        mastery = row["level"] if row else 0.0
    else:
        row = conn.execute(
            "SELECT COALESCE(AVG(level),0) AS lvl FROM mastery WHERE student_id = ?",
            (student_id,),
        ).fetchone()
        mastery = row["lvl"] or 0.0

    target = 1 + mastery * 4  # 1.0 at zero mastery, 5.0 at full mastery

    # Deterministic ordering by distance-from-target, with a small jitter so
    # two consecutive quizzes aren't identical.
    rng = random.Random(student_id * 1000 + len(candidates))
    ranked = sorted(
        candidates,
        key=lambda q: (abs(q["difficulty"] - target), rng.random()),
    )
    chosen = ranked[:count]
    chosen.sort(key=lambda q: q["difficulty"])
    return chosen


@router.post("/quiz", summary="Start a quiz")
def start_quiz(
    payload: QuizStart,
    student: sqlite3.Row = Depends(get_student),
    conn: sqlite3.Connection = Depends(db_dependency),
) -> dict:
    sid = student["id"]

    questions = _pick_questions(
        conn, sid, payload.topic_id, payload.subject_code, payload.total_questions
    )
    if not questions:
        raise HTTPException(
            status_code=404,
            detail=("No approved questions available for that selection. "
                    "Load a book (scripts/ingest_curriculum.py), then generate "
                    "and approve practice for its lessons."),
        )

    # Abandon any quiz left open, so "current quiz" is never ambiguous.
    conn.execute(
        "UPDATE quizzes SET status = 'abandoned' WHERE student_id = ? AND status = 'active'",
        (sid,),
    )

    topic_id = payload.topic_id or questions[0]["topic_id"]
    quiz_id = conn.execute(
        "INSERT INTO quizzes (student_id, topic_id, total_questions) VALUES (?,?,?)",
        (sid, topic_id, len(questions)),
    ).lastrowid

    for position, question in enumerate(questions):
        conn.execute(
            "INSERT INTO quiz_questions (quiz_id, question_id, position) VALUES (?,?,?)",
            (quiz_id, question["id"], position),
        )

    return _quiz_state(conn, quiz_id)


def _quiz_state(conn: sqlite3.Connection, quiz_id: int) -> dict:
    """Current quiz plus the question the student should see now."""
    quiz = conn.execute("SELECT * FROM quizzes WHERE id = ?", (quiz_id,)).fetchone()
    if quiz is None:
        raise HTTPException(status_code=404, detail=f"Quiz {quiz_id} not found")

    topic = conn.execute(
        "SELECT t.title, COALESCE(sub.name, t.subject) AS subject_name "
        "FROM topics t LEFT JOIN subjects sub ON sub.id = t.subject_id WHERE t.id = ?",
        (quiz["topic_id"],),
    ).fetchone()

    current = conn.execute(
        """
        SELECT qq.position, q.id, q.prompt, q.options_json, q.hint, q.difficulty
        FROM quiz_questions qq
        JOIN questions q ON q.id = qq.question_id
        WHERE qq.quiz_id = ? AND qq.answered_index IS NULL
        ORDER BY qq.position LIMIT 1
        """,
        (quiz_id,),
    ).fetchone()

    answered = conn.execute(
        "SELECT COUNT(*) AS n FROM quiz_questions WHERE quiz_id = ? AND answered_index IS NOT NULL",
        (quiz_id,),
    ).fetchone()["n"]

    accuracy = round((quiz["correct_count"] / answered) * 100) if answered else 0

    return {
        "quiz_id": quiz_id,
        "status": quiz["status"],
        "topic_title": topic["title"] if topic else None,
        "subject_name": topic["subject_name"] if topic else None,
        "total_questions": quiz["total_questions"],
        "answered": answered,
        "score": quiz["score"],
        "correct_count": quiz["correct_count"],
        "lives": quiz["lives"],
        "streak": quiz["streak"],
        "accuracy_pct": accuracy,
        "question": (
            {
                "id": current["id"],
                "position": current["position"],
                "number": current["position"] + 1,
                "prompt": current["prompt"],
                # correct_index deliberately withheld — the client must not be
                # able to read the answer out of the network response.
                "options": json.loads(current["options_json"]),
                "hint": current["hint"],
                "difficulty": current["difficulty"],
            }
            if current else None
        ),
    }


@router.get("/quiz/current", summary="Resume the active quiz")
def current_quiz(
    student: sqlite3.Row = Depends(get_student),
    conn: sqlite3.Connection = Depends(db_dependency),
) -> dict:
    quiz = conn.execute(
        "SELECT id FROM quizzes WHERE student_id = ? AND status = 'active' "
        "ORDER BY id DESC LIMIT 1",
        (student["id"],),
    ).fetchone()
    if quiz is None:
        return {"quiz_id": None, "status": "none", "question": None}
    return _quiz_state(conn, quiz["id"])


@router.post("/quiz/{quiz_id}/answer", summary="Submit an answer")
def answer_question(
    quiz_id: int,
    payload: QuizAnswer,
    student: sqlite3.Row = Depends(get_student),
    conn: sqlite3.Connection = Depends(db_dependency),
) -> dict:
    """
    The `check_answer` + `update_mastery` tools, as one atomic step.

    Grades the answer, moves mastery, awards stars with a streak multiplier,
    and returns the explanation for Souly to read aloud.
    """
    sid = student["id"]

    quiz = conn.execute(
        "SELECT * FROM quizzes WHERE id = ? AND student_id = ?", (quiz_id, sid)
    ).fetchone()
    if quiz is None:
        raise HTTPException(status_code=404, detail=f"Quiz {quiz_id} not found")
    if quiz["status"] != "active":
        raise HTTPException(status_code=409,
                            detail=f"Quiz {quiz_id} is already {quiz['status']}")

    current = conn.execute(
        """
        SELECT qq.id AS qq_id, qq.position, q.*
        FROM quiz_questions qq
        JOIN questions q ON q.id = qq.question_id
        WHERE qq.quiz_id = ? AND qq.answered_index IS NULL
        ORDER BY qq.position LIMIT 1
        """,
        (quiz_id,),
    ).fetchone()
    if current is None:
        raise HTTPException(status_code=409, detail="No unanswered questions left")

    options = json.loads(current["options_json"])
    if payload.answer_index >= len(options):
        raise HTTPException(
            status_code=422,
            detail=f"answer_index {payload.answer_index} out of range "
                   f"(question has {len(options)} options)",
        )

    is_correct = payload.answer_index == current["correct_index"]

    conn.execute(
        "UPDATE quiz_questions SET answered_index = ?, is_correct = ?, answered_at = ? "
        "WHERE id = ?",
        (payload.answer_index, int(is_correct), economy.utc_now(), current["qq_id"]),
    )

    new_streak = quiz["streak"] + 1 if is_correct else 0
    new_lives = quiz["lives"] if is_correct else max(0, quiz["lives"] - 1)
    multiplier = economy.streak_multiplier(new_streak) if is_correct else 1.0

    base_stars = (economy.STARS_PER_CORRECT_ANSWER if is_correct
                  else economy.STARS_PER_WRONG_ANSWER)
    base_xp = economy.XP_PER_CORRECT_ANSWER if is_correct else 0

    conn.execute(
        "UPDATE quizzes SET score = score + ?, correct_count = correct_count + ?, "
        "streak = ?, lives = ?, current_index = current_index + 1 WHERE id = ?",
        (int(base_stars * multiplier), int(is_correct), new_streak, new_lives, quiz_id),
    )

    mastery = economy.update_mastery(conn, sid, current["topic_id"], is_correct)

    # Close the loop on any hints used for this question.
    tutor.resolve_hints(conn, sid, current["id"], is_correct)

    subject_row = conn.execute(
        "SELECT subject_id FROM topics WHERE id = ?", (current["topic_id"],)
    ).fetchone()

    award = economy.award(
        conn, sid, "quiz_answer",
        stars=base_stars, xp=base_xp,
        topic_id=current["topic_id"],
        subject_id=subject_row["subject_id"] if subject_row else None,
        reference_id=quiz_id, duration_s=payload.duration_s,
        multiplier=multiplier,
        detail=f"{'Correct' if is_correct else 'Wrong'}: {current['prompt'][:80]}",
    )

    conn.execute(
        """
        INSERT INTO attempts (session_id, student_id, topic_id, question_text,
                              expected_answer, student_answer, input_mode,
                              is_correct, asked_at, answered_at)
        VALUES (NULL,?,?,?,?,?,'touch',?,?,?)
        """,
        (sid, current["topic_id"], current["prompt"],
         options[current["correct_index"]], options[payload.answer_index],
         int(is_correct), economy.utc_now(), economy.utc_now()),
    )

    remaining = conn.execute(
        "SELECT COUNT(*) AS n FROM quiz_questions WHERE quiz_id = ? AND answered_index IS NULL",
        (quiz_id,),
    ).fetchone()["n"]

    quiz_complete = remaining == 0 or new_lives == 0
    completion_award = None
    if quiz_complete:
        conn.execute(
            "UPDATE quizzes SET status = 'complete', completed_at = ? WHERE id = ?",
            (economy.utc_now(), quiz_id),
        )
        completion_award = economy.award(
            conn, sid, "quiz_complete",
            stars=economy.STARS_PER_QUIZ_COMPLETE,
            xp=economy.XP_PER_QUIZ_COMPLETE,
            topic_id=current["topic_id"], reference_id=quiz_id,
        ).to_dict()
        economy.mark_challenge_step(conn, sid, "quiz")

    return {
        "correct": is_correct,
        "correct_index": current["correct_index"],
        "correct_answer": options[current["correct_index"]],
        "explanation": current["explanation"],
        "question_id": current["id"],
        # A wrong answer routes into the hint ladder rather than just being
        # marked wrong and moved past.
        "offer_hint": not is_correct,
        "streak": new_streak,
        "lives": new_lives,
        "multiplier": multiplier,
        "mastery": mastery,
        "award": award.to_dict(),
        "quiz_complete": quiz_complete,
        "completion_award": completion_award,
        "state": _quiz_state(conn, quiz_id),
    }


@router.get("/quiz/{quiz_id}", summary="Quiz state")
def get_quiz(
    quiz_id: int,
    student: sqlite3.Row = Depends(get_student),
    conn: sqlite3.Connection = Depends(db_dependency),
) -> dict:
    return _quiz_state(conn, quiz_id)
