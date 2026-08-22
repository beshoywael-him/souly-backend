"""
Tests for the student app's API — the learning loop and the economy.

The theme: every screen the student sees must be backed by a real state
change. These assert the change happened, not just that the endpoint
returned 200.
"""

import json

import pytest

from app import economy
from app.db import get_conn
from app.models import utc_now_iso

SID = "stu-test"

# The fixture curriculum, in one place so the tests can refer to it by name.
BOOK_CODE = "tst-book"
LESSON_LABEL = "Test Lesson"
PAGE_TEXT = [
    "A fraction is a part of a whole thing that has been cut into equal pieces.",
    "The bottom number tells you how many equal pieces there are in total.",
]


# =============================================================================
# Fixtures
# =============================================================================

@pytest.fixture(scope="module", autouse=True)
def content(client):
    """
    Minimal curriculum: one book, one lesson across two pages, three questions.

    Shaped like the real thing since schema_v5 — a topic IS a lesson in a
    book, and the page text lives on disk rather than in SQLite, so the
    fixture writes the cache files the tutor and retrieval actually read.
    """
    from app.services import curriculum as curriculum_service

    with get_conn() as conn:
        subject_id = conn.execute(
            "INSERT INTO subjects (code, name, icon, difficulty, sort_order, created_at) "
            "VALUES ('TST','Testing','🧪','Easy',1,?)",
            (utc_now_iso(),),
        ).lastrowid

        book_id = conn.execute(
            """
            INSERT INTO curriculum_books
                (code, title, subject, subject_code, grade, term, filename,
                 page_count, sha256, is_verified, source_note)
            VALUES ('tst-book','Test Book','Testing','TST','5','1',
                    'test.pdf', 2, 'testsha', 1, 'fixture')
            """
        ).lastrowid

        for page in (1, 2):
            conn.execute(
                "INSERT INTO curriculum_pages (book_id, lesson, page, lesson_order, unit) "
                "VALUES (?,?,?,0,'Test Unit')",
                (book_id, LESSON_LABEL, page),
            )

        topic_id = conn.execute(
            """
            INSERT INTO topics (code, subject, subject_id, title, grade,
                                sort_order, is_verified, book_id, lesson_label,
                                created_at)
            VALUES ('TST.ONE','TST',?,?,'5',1,1,?,?,?)
            """,
            (subject_id, LESSON_LABEL, book_id, LESSON_LABEL, utc_now_iso()),
        ).lastrowid

        for i in range(3):
            conn.execute(
                """
                INSERT INTO questions (topic_id, book_id, source_page, prompt,
                                       options_json, correct_index, explanation,
                                       hint, difficulty, engine, review_status,
                                       created_at)
                VALUES (?,?,1,?,?,?,?,?,?, 'bank', 'approved', ?)
                """,
                (topic_id, book_id, f"Test question {i}?",
                 json.dumps(["right", "wrong", "also wrong", "nope"]), 0,
                 "Because it is right.", "Pick the first one.", i + 1,
                 utc_now_iso()),
            )

        conn.execute(
            "INSERT INTO badges (code,name,description,icon,tier,criteria_type,"
            "criteria_value,sort_order) VALUES "
            "('T_FIRST','First Step','Do one lesson step','👣','bronze','lessons_completed',1,1)"
        )
        conn.execute(
            "INSERT INTO rewards (code,name,description,icon,cost_stars,category,payload,sort_order) "
            "VALUES ('T_CHEAP','Cheap Hat','A hat','🎩',10,'cosmetic','{\"hat\":1}',1)"
        )
        conn.execute(
            "INSERT INTO rewards (code,name,description,icon,cost_stars,category,payload,sort_order) "
            "VALUES ('T_PRICEY','Gold Crown','A crown','👑',999999,'cosmetic','{}',2)"
        )
        conn.execute(
            "INSERT INTO games (code,name,description,icon,difficulty,star_reward,"
            "subject_id,topic_id,engine,is_featured,sort_order) "
            "VALUES ('T_GAME','Test Game','Play','🎮','Easy',20,?,?,'math_sprint',1,1)",
            (subject_id, topic_id),
        )
        for order, (code, name) in enumerate(
            [("PROBLEM_SOLVING", "Problem Solving"), ("READING", "Reading"),
             ("CRITICAL_THINKING", "Critical Thinking"), ("CREATIVITY", "Creativity"),
             ("COMMUNICATION", "Communication")], 1):
            conn.execute(
                "INSERT INTO skills (code, name, sort_order) VALUES (?,?,?)",
                (code, name, order),
            )

        page_ids = [
            r["id"] for r in conn.execute(
                "SELECT id FROM curriculum_pages WHERE book_id = ? ORDER BY page",
                (book_id,),
            )
        ]

    # The page text is on disk, never in the database. Writing it here is what
    # makes retrieval and grounding work in tests exactly as they do in the
    # app: read from the cache beside the book.
    cache = curriculum_service.cache_dir(BOOK_CODE)
    cache.mkdir(parents=True, exist_ok=True)
    for page, body in enumerate(PAGE_TEXT, 1):
        curriculum_service.text_path(BOOK_CODE, page).write_text(body, encoding="utf-8")

    return {
        "subject_id": subject_id,
        "topic_id": topic_id,
        "lesson_id": topic_id,      # a topic IS the lesson now
        "book_id": book_id,
        "page_ids": page_ids,
        "pages": [1, 2],
    }


def api(path):
    return f"/api/students/{SID}{path}"


def stars(client):
    return client.get(api("/profile")).json()["stars"]


# =============================================================================
# Home & profile
# =============================================================================

def test_home_returns_everything_the_screen_needs(client):
    body = client.get(api("/home")).json()
    for key in ("profile", "greeting", "souly_message", "todays_lesson",
                "schedule", "daily_challenge", "weekly_progress_pct", "settings"):
        assert key in body, f"home response missing '{key}'"
    assert body["profile"]["display_name"] == "Testy"
    assert "Testy" in body["greeting"]


def test_unknown_student_is_404_everywhere(client):
    for path in ("/home", "/profile", "/subjects", "/progress", "/achievements"):
        r = client.get(f"/api/students/stu-nope{path}")
        assert r.status_code == 404, f"{path} returned {r.status_code}"


# =============================================================================
# Lessons
# =============================================================================

def test_lesson_returns_its_pages_and_a_practice_question(client, content):
    body = client.get(api(f"/lessons/{content['lesson_id']}")).json()
    assert body["total_pages"] == 2
    assert len(body["pages"]) == 2
    assert [p["page"] for p in body["pages"]] == [1, 2]
    assert body["practice_question"] is not None
    assert len(body["practice_question"]["options"]) == 4
    # The answer must not ship with the page.
    assert "correct_index" not in body["practice_question"]


def test_working_through_the_pages_awards_stars_and_finishes_the_lesson(client, content):
    lesson_id = content["lesson_id"]
    before = stars(client)

    first = client.post(api(f"/lessons/{lesson_id}/page"),
                        json={"page": 1, "duration_s": 30}).json()
    assert first["lesson_complete"] is False
    assert first["award"]["stars_delta"] > 0

    second = client.post(api(f"/lessons/{lesson_id}/page"),
                         json={"page": 2, "duration_s": 40}).json()
    assert second["lesson_complete"] is True
    assert second["award"]["stars_delta"] >= economy.STARS_PER_LESSON_COMPLETE

    assert stars(client) > before


def test_a_page_outside_the_lesson_is_404(client, content):
    """
    The page number is the book's, so it has to be checked against the map.
    Accepting any integer would let a child "complete" a lesson by posting
    page numbers that are not in it.
    """
    r = client.post(api(f"/lessons/{content['lesson_id']}/page"), json={"page": 99})
    assert r.status_code == 404


def test_replaying_a_page_does_not_inflate_progress(client, content):
    """
    Re-reading page 1 must not push the counter past the number of pages.
    Without the MAX() in the upsert, a student who taps back and forward
    repeatedly would "complete" a lesson they never finished.
    """
    lesson_id = content["lesson_id"]
    for _ in range(4):
        client.post(api(f"/lessons/{lesson_id}/page"), json={"page": 1})

    body = client.get(api(f"/lessons/{lesson_id}")).json()
    assert body["pages_completed"] <= body["total_pages"]


def test_missing_lesson_is_404(client):
    assert client.get(api("/lessons/999999")).status_code == 404


# =============================================================================
# Quiz
# =============================================================================

def test_quiz_never_leaks_the_correct_answer(client, content):
    """
    The single most important assertion in this file. If correct_index ships
    to the browser, any student can read it out of devtools and every score
    in the demo becomes meaningless.
    """
    body = client.post(api("/quiz"), json={"topic_id": content["topic_id"]}).json()
    question = body["question"]
    assert "correct_index" not in question
    assert "explanation" not in question
    assert "options" in question


def test_correct_answer_awards_stars_and_raises_mastery(client, content):
    quiz = client.post(api("/quiz"),
                       json={"topic_id": content["topic_id"], "total_questions": 3}).json()

    result = client.post(api(f"/quiz/{quiz['quiz_id']}/answer"),
                         json={"answer_index": 0, "duration_s": 10}).json()

    assert result["correct"] is True
    assert result["award"]["stars_delta"] >= economy.STARS_PER_CORRECT_ANSWER
    assert result["mastery"]["level"] > 0
    assert result["explanation"]


def test_wrong_answer_still_gives_something_and_costs_a_life(client, content):
    """Effort is always worth a little. Zero teaches a struggling child to stop trying."""
    quiz = client.post(api("/quiz"),
                       json={"topic_id": content["topic_id"], "total_questions": 3}).json()

    result = client.post(api(f"/quiz/{quiz['quiz_id']}/answer"),
                         json={"answer_index": 1}).json()

    assert result["correct"] is False
    assert result["award"]["stars_delta"] == economy.STARS_PER_WRONG_ANSWER
    assert result["lives"] == 2
    assert result["streak"] == 0
    assert result["correct_answer"] == "right"


def test_answer_streak_multiplies_stars(client, content):
    quiz = client.post(api("/quiz"),
                       json={"topic_id": content["topic_id"], "total_questions": 3}).json()
    qid = quiz["quiz_id"]

    deltas = []
    for _ in range(3):
        r = client.post(api(f"/quiz/{qid}/answer"), json={"answer_index": 0}).json()
        deltas.append(r["award"]["stars_delta"])
        if r["quiz_complete"]:
            break

    assert deltas[-1] > deltas[0], "A 3-answer streak should multiply the reward"


def test_out_of_range_answer_index_is_rejected(client, content):
    quiz = client.post(api("/quiz"), json={"topic_id": content["topic_id"]}).json()
    r = client.post(api(f"/quiz/{quiz['quiz_id']}/answer"), json={"answer_index": 99})
    assert r.status_code == 422


def test_finishing_a_quiz_marks_it_complete_and_ticks_the_challenge(client, content):
    quiz = client.post(api("/quiz"),
                       json={"topic_id": content["topic_id"], "total_questions": 3}).json()
    qid = quiz["quiz_id"]

    result = None
    for _ in range(5):
        result = client.post(api(f"/quiz/{qid}/answer"), json={"answer_index": 0}).json()
        if result["quiz_complete"]:
            break

    assert result["quiz_complete"] is True
    assert result["completion_award"] is not None
    assert client.get(api("/challenge")).json()["quiz_done"] is True


def test_answering_a_finished_quiz_is_rejected(client, content):
    quiz = client.post(api("/quiz"),
                       json={"topic_id": content["topic_id"], "total_questions": 3}).json()
    qid = quiz["quiz_id"]
    for _ in range(5):
        if client.post(api(f"/quiz/{qid}/answer"), json={"answer_index": 0}).json()["quiz_complete"]:
            break

    r = client.post(api(f"/quiz/{qid}/answer"), json={"answer_index": 0})
    assert r.status_code == 409


def test_quiz_with_no_questions_is_404(client):
    r = client.post(api("/quiz"), json={"topic_id": 999999})
    assert r.status_code == 404
    # The message has to say what to DO about it, not just that it failed.
    assert "approved" in r.json()["detail"].lower()


# =============================================================================
# Economy
# =============================================================================

def test_xp_and_levels_climb_together(client, content):
    before = client.get(api("/profile")).json()
    for _ in range(6):
        client.post(api(f"/lessons/{content['lesson_id']}/page"), json={"page": 1})
    after = client.get(api("/profile")).json()

    assert after["xp"] > before["xp"]
    assert after["level"] >= before["level"]


def test_level_thresholds_are_monotonic():
    """A later level must never be cheaper than an earlier one."""
    for i in range(1, len(economy.LEVEL_THRESHOLDS)):
        assert economy.LEVEL_THRESHOLDS[i] > economy.LEVEL_THRESHOLDS[i - 1]


def test_level_for_xp_boundaries():
    assert economy.level_for_xp(0) == 1
    assert economy.level_for_xp(99) == 1
    assert economy.level_for_xp(100) == 2
    assert economy.level_for_xp(250) == 3
    assert economy.level_for_xp(10_000_000) >= 20


def test_streak_resets_to_one_not_zero(client):
    """
    A student active right now has a streak of at least 1. Showing "0 day
    streak" to someone who just did a lesson is wrong and discouraging.
    """
    with get_conn() as conn:
        sid = conn.execute(
            "SELECT id FROM students WHERE external_id = ?", (SID,)
        ).fetchone()["id"]
        streak, extended = economy._update_streak(conn, sid, 12, "2020-01-01")
    assert streak == 1
    assert extended is True


def test_mastery_is_clamped_between_zero_and_one(client, content):
    with get_conn() as conn:
        sid = conn.execute(
            "SELECT id FROM students WHERE external_id = ?", (SID,)
        ).fetchone()["id"]
        for _ in range(40):
            result = economy.update_mastery(conn, sid, content["topic_id"], True)
        assert result["level"] <= 1.0
        for _ in range(60):
            result = economy.update_mastery(conn, sid, content["topic_id"], False)
        assert result["level"] >= 0.0


# =============================================================================
# Rewards
# =============================================================================

def test_unlocking_a_reward_spends_stars(client, content):
    # Earn enough to afford the cheap one.
    for _ in range(3):
        client.post(api(f"/lessons/{content['lesson_id']}/page"), json={"page": 1})

    catalogue = client.get(api("/rewards")).json()
    cheap = next(r for r in catalogue["rewards"] if r["code"] == "T_CHEAP")
    before = catalogue["stars"]

    if cheap["owned"]:
        pytest.skip("already owned in this module run")

    result = client.post(api(f"/rewards/{cheap['id']}/unlock")).json()
    assert result["unlocked"] is True
    assert result["stars_remaining"] == before - cheap["cost_stars"]
    assert stars(client) == before - cheap["cost_stars"]


def test_cannot_afford_returns_402_and_does_not_change_the_balance(client):
    catalogue = client.get(api("/rewards")).json()
    pricey = next(r for r in catalogue["rewards"] if r["code"] == "T_PRICEY")
    before = stars(client)

    r = client.post(api(f"/rewards/{pricey['id']}/unlock"))
    assert r.status_code == 402
    assert "Not enough stars" in r.json()["detail"]
    assert stars(client) == before, "A failed purchase must not deduct stars"


def test_cannot_buy_the_same_reward_twice(client, content):
    catalogue = client.get(api("/rewards")).json()
    cheap = next(r for r in catalogue["rewards"] if r["code"] == "T_CHEAP")
    if not cheap["owned"]:
        client.post(api(f"/rewards/{cheap['id']}/unlock"))

    r = client.post(api(f"/rewards/{cheap['id']}/unlock"))
    assert r.status_code == 409


def test_cannot_equip_something_you_do_not_own(client):
    catalogue = client.get(api("/rewards")).json()
    pricey = next(r for r in catalogue["rewards"] if r["code"] == "T_PRICEY")
    assert client.post(api(f"/rewards/{pricey['id']}/equip")).status_code == 403


# =============================================================================
# Badges
# =============================================================================

def test_badges_unlock_from_real_counters(client, content):
    client.post(api(f"/lessons/{content['lesson_id']}/step"), json={"step_no": 1})
    client.post(api(f"/lessons/{content['lesson_id']}/step"), json={"step_no": 2})

    body = client.get(api("/achievements")).json()
    first = next(b for b in body["badges"] if b["code"] == "T_FIRST")
    assert first["unlocked"] is True
    assert body["earned_count"] >= 1


def test_locked_badges_report_progress(client):
    body = client.get(api("/achievements")).json()
    for badge in body["badges"]:
        assert 0 <= badge["progress_pct"] <= 100
        assert badge["progress"] <= badge["target"]


# =============================================================================
# Games
# =============================================================================

def test_game_result_awards_stars_and_records_a_play(client):
    games = client.get(api("/games")).json()
    game = games["games"][0]
    before = stars(client)

    result = client.post(api(f"/games/{game['id']}/result"),
                         json={"score": 90, "max_score": 100, "duration_s": 45}).json()

    assert result["is_win"] is True
    assert result["award"]["stars_delta"] > 0
    assert stars(client) > before
    assert client.get(api("/challenge")).json()["game_done"] is True


def test_losing_a_game_still_earns_something(client):
    games = client.get(api("/games")).json()
    game = games["games"][0]

    result = client.post(api(f"/games/{game['id']}/result"),
                         json={"score": 20, "max_score": 100}).json()
    assert result["is_win"] is False
    assert result["award"]["stars_delta"] > 0, "Partial effort should still earn stars"


def test_game_questions_come_from_the_verified_bank(client):
    games = client.get(api("/games")).json()
    game = games["games"][0]
    body = client.get(api(f"/games/{game['id']}/questions?count=5")).json()
    assert body["questions"]
    for q in body["questions"]:
        assert len(q["options"]) >= 2
        assert 0 <= q["correct_index"] < len(q["options"])


# =============================================================================
# Daily challenge
# =============================================================================

def test_cannot_claim_an_unfinished_challenge(client):
    with get_conn() as conn:
        sid = conn.execute("SELECT id FROM students WHERE external_id = ?",
                           (SID,)).fetchone()["id"]
        conn.execute(
            "UPDATE daily_challenge_progress SET lesson_done=0, quiz_done=0, "
            "game_done=0, reward_claimed=0 WHERE student_id = ?",
            (sid,),
        )

    r = client.post(api("/challenge/claim"))
    assert r.status_code == 409
    assert "still need" in r.json()["detail"]


def test_claiming_a_finished_challenge_pays_out_once(client):
    with get_conn() as conn:
        sid = conn.execute("SELECT id FROM students WHERE external_id = ?",
                           (SID,)).fetchone()["id"]
        conn.execute(
            "UPDATE daily_challenge_progress SET lesson_done=1, quiz_done=1, "
            "game_done=1, reward_claimed=0 WHERE student_id = ?",
            (sid,),
        )

    before = stars(client)
    result = client.post(api("/challenge/claim")).json()
    assert result["claimed"] is True
    assert stars(client) == before + result["award"]["stars_delta"]

    assert client.post(api("/challenge/claim")).status_code == 409


# =============================================================================
# Settings
# =============================================================================

def test_settings_round_trip(client):
    saved = client.put(api("/settings"),
                       json={"font_size": "large", "high_contrast": True,
                             "theme": "dark", "voice_volume": 40}).json()
    assert saved["font_size"] == "large"
    assert saved["high_contrast"] == 1
    assert saved["theme"] == "dark"
    assert saved["voice_volume"] == 40

    fetched = client.get(api("/settings")).json()
    assert fetched["font_size"] == "large"


def test_partial_settings_update_leaves_others_alone(client):
    client.put(api("/settings"), json={"font_size": "small", "read_aloud": False})
    client.put(api("/settings"), json={"theme": "purple"})
    body = client.get(api("/settings")).json()
    assert body["theme"] == "purple"
    assert body["font_size"] == "small"
    assert body["read_aloud"] == 0


def test_invalid_setting_values_are_rejected(client):
    assert client.put(api("/settings"), json={"font_size": "gigantic"}).status_code == 422
    assert client.put(api("/settings"), json={"voice_volume": 500}).status_code == 422
    assert client.put(api("/settings"), json={"theme": "neon"}).status_code == 422


# =============================================================================
# Chat
# =============================================================================

def test_chat_answers_and_is_grounded_in_curriculum(client):
    """
    With no Gemini key the fallback must still produce a real, grounded
    answer — not an error and not a canned string.
    """
    body = client.post(api("/chat"), json={"message": "what is a fraction"}).json()
    assert body["reply"]
    assert len(body["reply"]) > 30
    assert body["engine"] in ("gemini", "fallback")
    assert body["grounded"] is True
    assert body["source_refs"]


def test_chat_off_syllabus_refuses_rather_than_inventing(client):
    body = client.post(api("/chat"),
                       json={"message": "explain quantum chromodynamics"}).json()
    assert body["grounded"] is False
    assert body["reply"]


def test_chat_is_persisted_and_awards_stars(client):
    before = stars(client)
    client.post(api("/chat"), json={"message": "tell me about fractions again"})
    history = client.get(api("/chat/history")).json()
    assert history[-1]["role"] == "souly"
    assert history[-2]["role"] == "student"
    assert stars(client) > before


def test_quiz_me_switches_mode(client):
    body = client.post(api("/chat"), json={"message": "quiz me on fractions"}).json()
    assert body["suggested_mode"] == "quiz"


def test_empty_chat_message_is_rejected(client):
    assert client.post(api("/chat"), json={"message": ""}).status_code == 422


def test_a_follow_up_stays_on_the_same_material(client):
    """
    "why?" carries nothing to search on. Without an anchor, retrieval comes
    back empty, and llm.py then instructs the model to say it has not learned
    about that yet — so every follow-up got refused and the conversation felt
    like it reset on every message.
    """
    first = client.post(api("/chat"),
                        json={"message": "what is a fraction"}).json()
    assert first["grounded"] is True

    follow_up = client.post(api("/chat"), json={"message": "why?"}).json()
    assert follow_up["grounded"] is True, "a follow-up lost its anchor"
    assert follow_up["source_refs"]


def test_a_new_off_syllabus_question_is_not_anchored(client):
    """
    The anchor must not turn every question into a question about the last
    lesson. A real new question that is off-syllabus still gets refused.
    """
    client.post(api("/chat"), json={"message": "what is a fraction"})
    body = client.post(api("/chat"),
                       json={"message": "explain quantum chromodynamics"}).json()
    assert body["grounded"] is False


def test_history_stops_at_the_gap_between_sittings(client):
    """
    A conversation is one sitting. Carrying yesterday's chat into today makes
    Souly answer questions nobody asked — and taking only the last N rows
    whenever they happened did exactly that.
    """
    from app.services import tutor

    with get_conn() as conn:
        sid = conn.execute("SELECT id FROM students WHERE external_id = ?",
                           (SID,)).fetchone()["id"]
        conn.execute("DELETE FROM chat_messages WHERE student_id = ?", (sid,))
        for stamp, content in (
            ("2020-01-01T09:00:00Z", "yesterday one"),
            ("2020-01-01T09:01:00Z", "yesterday two"),
            (utc_now_iso(), "today one"),
        ):
            conn.execute(
                "INSERT INTO chat_messages (student_id, role, content, "
                "input_mode, created_at) VALUES (?, 'student', ?, 'text', ?)",
                (sid, content, stamp),
            )

    with get_conn() as conn:
        turns = tutor.load_history(conn, sid)

    contents = [t["content"] for t in turns]
    assert contents == ["today one"], contents


def test_clearing_chat_history(client):
    client.post(api("/chat"), json={"message": "hello"})
    client.delete(api("/chat/history"))
    assert client.get(api("/chat/history")).json() == []


# =============================================================================
# Progress
# =============================================================================

def test_progress_dashboard_shape(client):
    body = client.get(api("/progress")).json()
    for key in ("level", "subjects", "week", "time_spent", "skills", "goals", "message"):
        assert key in body
    assert len(body["week"]) == 7
    assert sum(1 for d in body["week"] if d["is_today"]) == 1


def test_progress_never_exceeds_100_percent(client):
    body = client.get(api("/progress")).json()
    assert 0 <= body["overall_progress_pct"] <= 100
    for subject in body["subjects"]:
        assert 0 <= subject["progress_pct"] <= 100
    for skill in body["skills"]:
        assert 0 <= skill["level_pct"] <= 100


def test_duration_formatting():
    from app.routers.progress import _fmt_duration
    assert _fmt_duration(0) == "0m"
    assert _fmt_duration(90) == "1m"
    assert _fmt_duration(3600) == "1h"
    assert _fmt_duration(4800) == "1h 20m"


# =============================================================================
# Retrieval
# =============================================================================

def test_rag_finds_relevant_content_and_handles_plurals(client):
    from app.services import rag
    with get_conn() as conn:
        for query in ("fraction", "fractions", "what are fractions"):
            hits = rag.search(conn, query)
            assert hits, f"No curriculum found for {query!r}"
            assert hits[0].score > 0


def test_rag_returns_nothing_for_off_syllabus_queries(client):
    from app.services import rag
    with get_conn() as conn:
        assert rag.search(conn, "photosynthesis in deep sea vents") == []


def test_rag_excludes_unverified_content(client):
    """
    The agent must never teach from a book nobody approved.

    `is_verified` lives on the BOOK now, not on a hand-written lesson: the
    thing a human eyeballs is the Ministry PDF, and everything mapped into it
    inherits that judgement.
    """
    from app.services import curriculum as curriculum_service
    from app.services import rag

    with get_conn() as conn:
        book_id = conn.execute(
            """
            INSERT INTO curriculum_books
                (code, title, subject, subject_code, grade, filename, is_verified)
            VALUES ('unver-book','Unchecked Book','Testing','TST','5',
                    'unver.pdf', 0)
            """
        ).lastrowid
        conn.execute(
            "INSERT INTO curriculum_pages (book_id, lesson, page, lesson_order) "
            "VALUES (?, 'Unverified Lesson', 1, 0)",
            (book_id,),
        )
        conn.execute(
            """
            INSERT INTO topics (code, subject, title, is_verified, book_id,
                                lesson_label, created_at)
            VALUES ('UNVER','TST','Unverified Lesson',0,?,'Unverified Lesson',?)
            """,
            (book_id, utc_now_iso()),
        )

    cache = curriculum_service.cache_dir("unver-book")
    cache.mkdir(parents=True, exist_ok=True)
    curriculum_service.text_path("unver-book", 1).write_text(
        "Zorblatt particles orbit the quantum meridian.", encoding="utf-8"
    )

    with get_conn() as conn:
        assert rag.search(conn, "zorblatt particles") == []
        assert any(c.text.startswith("Zorblatt")
                   for c in rag.search(conn, "zorblatt particles",
                                       verified_only=False))


# =============================================================================
# Fallback behaviour
# =============================================================================

def test_llm_fallback_uses_the_retrieved_text(client):
    from app.services import llm
    result = llm.generate("what is a fraction",
                          context="[1] Test\nA fraction is a part of a whole.")
    assert result.engine == "fallback"
    assert "fraction" in result.text.lower()
    assert result.error


def test_llm_strips_markdown_so_tts_does_not_read_asterisks():
    from app.services.llm import _clean_for_speech
    assert "*" not in _clean_for_speech("This is **very** important and *fun*")
    assert "#" not in _clean_for_speech("# Heading\nSome text")
    assert "`" not in _clean_for_speech("Use the `print` command")


def test_tts_degrades_to_browser_rather_than_failing():
    from app.services import tts
    speech = tts.synthesize("Hello there")
    assert speech.ok or speech.use_browser_tts
    assert speech.use_browser_tts is True   # no vendor configured in tests


def test_stt_without_a_key_reports_clearly():
    from app.services import stt
    result = stt.transcribe(b"x" * 5000)
    assert result.ok is False
    assert "ELEVENLABS_API_KEY" in result.error


def test_stt_rejects_silence_sized_audio():
    from app.services import stt
    assert stt.transcribe(b"tiny").ok is False


# =============================================================================
# The lesson hint layer
#
# These are the tests for the merged flow. The theme: Souly helps without
# handing over answers, and every request is anchored to something on screen.
# =============================================================================

@pytest.fixture(scope="module")
def page_and_question(client, content):
    """The first page of the test lesson, and a question with full hint data."""
    page_id = content["page_ids"][0]

    with get_conn() as conn:
        question_id = conn.execute(
            """
            INSERT INTO questions (topic_id, book_id, source_page, prompt,
                                   options_json, correct_index, explanation, hint,
                                   worked_solution, common_wrong_answers,
                                   difficulty, engine, review_status, created_at)
            VALUES (?,?,1,?,?,?,?,?,?,?,?, 'bank', 'approved', ?)
            """,
            (content["topic_id"], content["book_id"],
             "A cake has 8 slices. You eat 3. How many are left?",
             json.dumps(["5", "3", "11", "8"]), 0,
             "8 take away 3 leaves 5.",
             "Take away what you ate from the total.",
             "Whole = 8. Eaten = 3. 8 - 3 = 5.",
             json.dumps([{"answer": "3", "why": "gave the number eaten, not left"}]),
             2, utc_now_iso()),
        ).lastrowid

    return {"page_id": page_id, "question_id": question_id}


def test_explain_returns_something_and_is_logged(client, page_and_question):
    """The 'I don't get this' button."""
    r = client.post(api(f"/pages/{page_and_question['page_id']}/explain"),
                    json={"mode": "simpler", "seconds_on_page": 30, "speak": False})
    assert r.status_code == 200
    body = r.json()
    assert body["text"]
    assert body["mode"] == "simpler"

    with get_conn() as conn:
        row = conn.execute(
            "SELECT * FROM hint_requests WHERE page_id = ? AND help_type = 'simpler'",
            (page_and_question["page_id"],),
        ).fetchone()
    assert row is not None, "Help request was not logged"
    assert row["seconds_before"] == 30


def test_all_three_help_modes_work(client, page_and_question):
    for mode in ("simpler", "example", "another_way"):
        r = client.post(api(f"/pages/{page_and_question['page_id']}/explain"),
                        json={"mode": mode, "speak": False})
        assert r.status_code == 200, f"{mode} failed"
        assert r.json()["text"]


def test_invalid_explain_mode_is_rejected(client, page_and_question):
    r = client.post(api(f"/pages/{page_and_question['page_id']}/explain"),
                    json={"mode": "just_tell_me", "speak": False})
    assert r.status_code == 422


def test_explain_on_missing_page_is_404(client):
    r = client.post(api("/pages/999999/explain"),
                    json={"mode": "simpler", "speak": False})
    assert r.status_code == 404


def test_hint_ladder_climbs_one_tier_at_a_time(client, page_and_question):
    qid = page_and_question["question_id"]
    for tier in (1, 2, 3):
        r = client.post(api(f"/questions/{qid}/hint"),
                        json={"tier": tier, "speak": False})
        assert r.status_code == 200
        body = r.json()
        assert body["tier"] == tier
        assert body["next_tier"] == tier + 1
        assert body["is_answer"] is False

    r = client.post(api(f"/questions/{qid}/hint"), json={"tier": 4, "speak": False})
    body = r.json()
    assert body["is_answer"] is True
    assert body["next_tier"] is None


def test_hint_tier_is_clamped(client, page_and_question):
    """Tier 9 must not be a way round the ladder."""
    r = client.post(api(f"/questions/{page_and_question['question_id']}/hint"),
                    json={"tier": 9, "speak": False})
    assert r.status_code == 422


def test_early_hints_do_not_contain_the_answer(client, page_and_question):
    """
    The point of the whole guardrail. Tiers 1-3 must not state the answer.

    Offline this is checked against the stored hint; with a live key it is
    checked against whatever Gemini produced under SOLUTION_PROMPT_RULES.
    """
    qid = page_and_question["question_id"]
    for tier in (1, 2, 3):
        text = client.post(api(f"/questions/{qid}/hint"),
                           json={"tier": tier, "speak": False}).json()["text"]
        # "5" is the answer; it must not be handed over at these tiers.
        assert "the answer is 5" not in text.lower(), f"tier {tier} gave the answer"


def test_hints_are_logged_with_tier_and_initiator(client, page_and_question):
    qid = page_and_question["question_id"]
    client.post(api(f"/questions/{qid}/hint"),
                json={"tier": 1, "speak": False, "initiated_by": "souly",
                      "attempts_before": 2})
    with get_conn() as conn:
        row = conn.execute(
            "SELECT * FROM hint_requests WHERE question_id = ? AND initiated_by = 'souly' "
            "ORDER BY id DESC LIMIT 1",
            (qid,),
        ).fetchone()
    assert row["tier"] == 1
    assert row["attempts_before"] == 2


def test_answering_resolves_the_hint_log(client, page_and_question):
    """
    hint_requests only becomes evidence if outcomes get written back.
    Without this, "did the ladder help?" is unanswerable.
    """
    qid = page_and_question["question_id"]
    client.post(api(f"/questions/{qid}/hint"), json={"tier": 1, "speak": False})
    client.post(api(f"/questions/{qid}/check"), json={"answer_index": 0})

    with get_conn() as conn:
        unresolved = conn.execute(
            "SELECT COUNT(*) c FROM hint_requests "
            "WHERE question_id = ? AND resolved_correct IS NULL",
            (qid,),
        ).fetchone()["c"]
    assert unresolved == 0


def test_check_answer_grades_without_leaking_beforehand(client, page_and_question):
    qid = page_and_question["question_id"]

    right = client.post(api(f"/questions/{qid}/check"), json={"answer_index": 0}).json()
    assert right["correct"] is True
    assert right["offer_hint"] is False

    wrong = client.post(api(f"/questions/{qid}/check"), json={"answer_index": 1}).json()
    assert wrong["correct"] is False
    assert wrong["offer_hint"] is True
    assert wrong["next_tier"] == 1

    # The answer is NOT in the response. A child who gets it wrong is routed
    # into the hint ladder — a nudge, then a worked example with different
    # numbers, then a step-by-step — and the answer only arrives at tier 4.
    # "Wrong, it was 5" teaches nothing except that they were wrong.
    assert wrong["correct_answer"] is None
    assert wrong["correct_index"] is None
    assert wrong["explanation"] is None
    assert wrong["can_retry"] is True


def test_the_hint_ladder_gets_more_concrete_with_each_wrong_answer(client,
                                                                   page_and_question):
    """
    Second wrong attempt starts a rung higher than the first. A child who has
    already had the nudge and still cannot do it needs the worked example, not
    the same nudge again.
    """
    qid = page_and_question["question_id"]
    first = client.post(api(f"/questions/{qid}/check"),
                        json={"answer_index": 1, "attempts_before": 0}).json()
    second = client.post(api(f"/questions/{qid}/check"),
                         json={"answer_index": 1, "attempts_before": 1}).json()
    assert second["next_tier"] > first["next_tier"]


def test_nobody_is_trapped_on_one_question(client, page_and_question):
    """
    Withholding the answer is right until it becomes its own harm. After
    MAX_PRACTICE_ATTEMPTS the answer is shown, warmly, and the child moves on.
    """
    from app.routers.learning import MAX_PRACTICE_ATTEMPTS

    qid = page_and_question["question_id"]
    body = client.post(
        api(f"/questions/{qid}/check"),
        json={"answer_index": 1, "attempts_before": MAX_PRACTICE_ATTEMPTS - 1},
    ).json()
    assert body["correct"] is False
    assert body["can_retry"] is False
    assert body["correct_answer"] == "5"


def test_lesson_payload_never_ships_the_practice_answer(client, content):
    """
    The lesson screen gets the question but not the key. If correct_index were
    in the page source, any student could read it in devtools.
    """
    body = client.get(api(f"/lessons/{content['lesson_id']}")).json()
    q = body["practice_question"]
    assert q is not None
    assert "correct_index" not in q
    assert "explanation" not in q
    assert "worked_solution" not in q


def test_lesson_pages_expose_their_id_and_image(client, content):
    """
    Help is anchored to a page, so the client needs the page id — and the
    books are scans, so it needs somewhere to fetch the printed page from.
    """
    body = client.get(api(f"/lessons/{content['lesson_id']}")).json()
    assert all(isinstance(p["page_id"], int) for p in body["pages"])
    assert all(p["image_url"].endswith("/image") for p in body["pages"])


# =============================================================================
# Stall detection — progress, never gaze
# =============================================================================

def test_no_offer_while_making_progress(client):
    r = client.post(api("/stall-check"),
                    json={"seconds_on_page": 3, "wrong_attempts": 0}).json()
    assert r["offer"] is False


def test_two_wrong_attempts_triggers_an_offer(client):
    r = client.post(api("/stall-check"),
                    json={"seconds_on_page": 5, "wrong_attempts": 2}).json()
    assert r["offer"] is True
    assert "wrong attempts" in r["reason"]


def test_long_idle_triggers_an_offer(client):
    r = client.post(api("/stall-check"),
                    json={"seconds_on_page": 600, "wrong_attempts": 0}).json()
    assert r["offer"] is True


def test_offer_is_not_repeated_on_the_same_step(client):
    """A child who said 'I'm fine' should be believed for the rest of the step."""
    r = client.post(api("/stall-check"),
                    json={"seconds_on_page": 600, "wrong_attempts": 3,
                          "already_offered": True}).json()
    assert r["offer"] is False


def test_stall_threshold_is_generous_before_we_know_the_student(client):
    """
    A brand-new student must not be nagged. Autistic people are measurably
    slower (g = .35), so the default has to sit well above a neurotypical
    median rather than at it.
    """
    from app.services import tutor
    with get_conn() as conn:
        sid = conn.execute("SELECT id FROM students WHERE external_id = ?",
                           (SID,)).fetchone()["id"]
        threshold = tutor.stall_threshold(conn, sid)
    assert threshold >= tutor.STALL_FLOOR_SECONDS


def test_stall_threshold_is_bounded(client):
    from app.services import tutor
    with get_conn() as conn:
        sid = conn.execute("SELECT id FROM students WHERE external_id = ?",
                           (SID,)).fetchone()["id"]
        # A student who takes ages on every page.
        page_id = conn.execute(
            "SELECT id FROM curriculum_pages LIMIT 1").fetchone()["id"]
        for _ in range(8):
            conn.execute(
                "INSERT INTO page_activity (student_id, page_id, seconds_on_page) "
                "VALUES (?,?,9999)", (sid, page_id))
    with get_conn() as conn:
        threshold = tutor.stall_threshold(conn, sid)
    assert threshold <= tutor.STALL_CEILING_SECONDS


# =============================================================================
# Question generation
# =============================================================================

def test_generation_falls_back_to_the_bank_without_a_key(client, content):
    """
    No Gemini key in tests, so generation is unavailable. The student must
    still get practice — silently returning nothing would be worse than
    returning bank questions.
    """
    r = client.post(api(f"/lessons/{content['lesson_id']}/generate-practice"),
                    json={"count": 3})
    assert r.status_code == 200
    body = r.json()
    assert body["fell_back_to_bank"] is True
    assert body["questions"], "Should have fallen back to bank questions"
    assert all(q["origin"] == "bank" for q in body["questions"])


def test_generated_questions_never_ship_the_answer(client, content):
    body = client.post(api(f"/lessons/{content['lesson_id']}/generate-practice"),
                       json={"count": 3}).json()
    for q in body["questions"]:
        assert "correct_index" not in q
        assert "worked_solution" not in q


# =============================================================================
# Differentiation
#
# "The lesson adapts to the child" is the claim the whole project rests on. It
# was true in the sense that the profile reached the prompt, and false in the
# sense that the prompt did the same thing with it every time — which nobody
# noticed for a month because nothing made it visible or testable.
# =============================================================================

def test_two_profiles_produce_different_teaching_instructions(client):
    """
    Not a test of the model's prose — a test that we ASK for something
    different. A child who needs the idea re-taught gets worked-example-first;
    a child who does not gets the question first.
    """
    from app.services import tutor

    needs_reteaching = {"instruction_need": "task_specific",
                        "support_profile": "autism"}
    needs_little = {"instruction_need": "low", "support_profile": "adhd"}

    a, a_labels = tutor._lesson_instruction(needs_reteaching, 0.0)
    b, b_labels = tutor._lesson_instruction(needs_little, 0.0)

    assert a != b
    assert a_labels != b_labels
    assert "worked example first" in a_labels
    assert "straight to the point" in b_labels
    assert "literal and predictable" in a_labels
    assert "short chunks" in b_labels


def test_a_visually_impaired_child_is_never_told_to_look(client):
    """
    The lesson is read aloud to them. An instruction that says "look at the
    diagram" is not a lesson, it is a locked door.
    """
    from app.services import tutor

    instruction, labels = tutor._lesson_instruction(
        {"support_profile": "visual_impairment"}, 0.0)
    assert "described in words" in labels
    assert "never say 'look at'" in instruction.lower()

    # And nothing else in the instruction may quietly contradict it. A model
    # handed "end by telling them what to look at" AND "never say look at"
    # follows the first one.
    before_delivery = instruction.split("DELIVERY:")[0].lower()
    assert "look at" not in before_delivery


def test_interests_reach_the_example_not_a_reward(client):
    from app.services import tutor

    instruction, labels = tutor._lesson_instruction(
        {"interests": "football, space"}, 0.0)
    assert "uses football" in labels
    assert "football" in instruction
    assert "never as a bribe" in instruction


def test_a_confident_child_gets_a_recap_not_a_re_teach(client):
    from app.services import tutor

    _, low = tutor._lesson_instruction({"instruction_need": "low"}, 0.1)
    _, high = tutor._lesson_instruction({"instruction_need": "low"}, 0.9)
    assert "recap, not re-teach" not in low
    assert "recap, not re-teach" in high


def test_a_changed_profile_invalidates_the_cached_lesson(client, content):
    """
    The cache is keyed per child, which is right. But it also froze the FIRST
    lesson a child ever saw — written before the entry activity had measured
    anything — and kept serving it after the profile changed.
    """
    from app.services import tutor

    page_id = content["page_ids"][0]

    with get_conn() as conn:
        sid = conn.execute("SELECT id FROM students WHERE external_id = ?",
                           (SID,)).fetchone()["id"]
        conn.execute(
            "DELETE FROM page_renditions WHERE student_id = ? AND page_id = ?",
            (sid, page_id),
        )
        conn.execute(
            """
            INSERT INTO page_renditions (student_id, page_id, mode, body,
                                         engine, source_sha, profile_sig)
            VALUES (?,?, 'lesson', 'stale lesson', 'gemini', 'testsha',
                    'a-profile-that-no-longer-applies')
            """,
            (sid, page_id),
        )

    with get_conn() as conn:
        reply = tutor.rendition(conn, sid, page_id)

    assert reply.text != "stale lesson"
    assert reply.cached is False


# =============================================================================
# The visual
#
# The lesson screen used to show the scanned book page, which was the source
# rather than the teaching and unreadable at pane size. What replaced it is a
# picture with a job — and these are the checks that stop a broken or a
# decorative one reaching a child.
# =============================================================================

def test_ocr_noise_is_never_shown_to_a_child(client):
    """
    A page that is mostly diagram OCRs into character soup. That is harmless
    as grounding — the model is told not to invent and simply has little to
    work with — but it must never reach the screen as though it were the
    lesson.
    """
    from app.services import tutor

    noise = ("Unit 1 | Concept 1 MM HOOUGGAOOO OO 0000000000000 VEE GOOLE "
             "HET ERAT PRQNTAELEEEECEEECEECCCC OCTET FUTTNTOUEEEEEEEEEEEE")
    assert tutor._looks_like_prose(noise) is False
    assert tutor._looks_like_prose(PAGE_TEXT[0] + " " + PAGE_TEXT[1] +
                                   " Every part is the same size. "
                                   "The top number counts the parts you have.") is True

    page = {"page": 4, "book_title": "Test Book"}
    said = tutor._offline_rendition(page, noise, "lesson")
    assert "HOOUGGAOOO" not in said
    assert "can't read" in said


def test_a_lesson_can_never_be_a_wall_of_text(client, content, monkeypatch):
    """
    The regression that mattered most. When the lesson moved to structured
    output it stopped inheriting SYSTEM_PROMPT — including the length limit —
    and a child with a reading difficulty was served thirteen unbroken lines.

    So the limit is enforced here, in code, and not left to the prompt.
    """
    from app.services import llm, tutor

    wall = ("Place three seeds in the top half of the paper towel. Fold the "
            "bottom half of the towel up to cover the seeds. Place the paper "
            "towel inside the plastic plate. Plant the other three seeds in "
            "the cup that contains soil, and water them. Place the plate and "
            "the cup where they can get sunlight. Check the growth over the "
            "next several days. Wet the paper towel and water the soil as "
            "needed. Measure the growth of each seed using the metric ruler.")

    def fake_json(instruction, **kwargs):
        return llm.JSONResponse(
            data={"chunks": [wall], "visual": {"kind": "none", "purpose": "x"}},
            engine="gemini", latency_ms=1)

    monkeypatch.setattr(llm, "generate_json", fake_json)

    with get_conn() as conn:
        sid = conn.execute("SELECT id FROM students WHERE external_id = ?",
                           (SID,)).fetchone()["id"]
        reply = tutor.rendition(conn, sid, content["page_ids"][0],
                                regenerate=True)

    words = len(reply.text.split())
    assert words <= tutor.MAX_CHUNK_WORDS * tutor.MAX_LESSON_CHUNKS, (
        f"{words} words reached a child")
    # And it ends on a full stop rather than mid-sentence.
    assert reply.text.rstrip()[-1] in ".!?"


def test_the_lesson_is_chunked_not_one_block(client, content, monkeypatch):
    from app.services import llm, tutor

    def fake_json(instruction, **kwargs):
        return llm.JSONResponse(
            data={"chunks": ["Plants need water.", "They need air too.",
                             "Sunlight makes their food.",
                             "A fourth chunk too many."],
                  "visual": {"kind": "none", "purpose": "x"}},
            engine="gemini", latency_ms=1)

    monkeypatch.setattr(llm, "generate_json", fake_json)

    with get_conn() as conn:
        sid = conn.execute("SELECT id FROM students WHERE external_id = ?",
                           (SID,)).fetchone()["id"]
        reply = tutor.rendition(conn, sid, content["page_ids"][0],
                                regenerate=True)

    parts = [p for p in reply.text.split("\n\n") if p.strip()]
    assert len(parts) == tutor.MAX_LESSON_CHUNKS
    assert "A fourth chunk" not in reply.text


def test_a_missing_picture_gets_a_second_attempt(client, content, monkeypatch):
    """
    For these children the picture carries more than the sentences do, so one
    combined call quietly returning nothing is not an acceptable outcome.
    """
    from app.services import llm, tutor

    calls = []

    def fake_json(instruction, **kwargs):
        calls.append(instruction)
        if len(calls) == 1:
            # The combined call: lesson fine, no picture.
            return llm.JSONResponse(
                data={"chunks": ["Plants need water and light."],
                      "visual": {"kind": "none", "purpose": "nothing to show"}},
                engine="gemini", latency_ms=1)
        # The second, narrower attempt.
        return llm.JSONResponse(
            data={"kind": "illustration", "purpose": "shows a growing seed",
                  "scene": "a bean seed sprouting in a cup of soil"},
            engine="gemini", latency_ms=1)

    monkeypatch.setattr(llm, "generate_json", fake_json)

    with get_conn() as conn:
        sid = conn.execute("SELECT id FROM students WHERE external_id = ?",
                           (SID,)).fetchone()["id"]
        reply = tutor.rendition(conn, sid, content["page_ids"][0],
                                regenerate=True)

    assert len(calls) == 2, "no second attempt was made"
    assert reply.visual is not None
    assert reply.visual["kind"] == "illustration"


def test_the_lesson_inherits_the_rules_about_how_souly_speaks(client):
    """
    Structured output used to run on a four-line paraphrase of SYSTEM_PROMPT,
    which is how the length limit and the rules about what the child can see
    got silently dropped from every lesson.
    """
    import inspect
    from app.services import llm

    source = inspect.getsource(llm.generate_json)
    assert "SYSTEM_PROMPT" in source


def test_souly_knows_it_can_draw(client):
    """
    A child asked for a picture and was told "I cannot make pictures for you."
    That was false — the app draws — and a child told no stops asking.
    """
    from app.services import llm, tutor

    assert tutor._wants_a_picture("generate an image") is True
    assert tutor._wants_a_picture("can you draw it") is True
    assert tutor._wants_a_picture("show me what it looks like") is True
    assert tutor._wants_a_picture("what is a fraction") is False

    assert "you can" in llm.SYSTEM_PROMPT.lower()
    assert "never tell them you cannot make pictures" in llm.SYSTEM_PROMPT.lower()


def test_souly_never_points_at_a_page_the_child_cannot_see(client):
    """
    The book is not on screen. It said "we can look at the picture on our page
    instead" — pointing a child at something that is not there.

    The book's own text makes this worse: pages say "look at the opposite
    picture", and that instruction was written for someone holding the book.
    """
    from app.services import llm, tutor

    prompt = llm.SYSTEM_PROMPT.lower()
    assert "cannot see the book" in prompt
    assert "look at the page" in prompt          # named, in order to forbid it
    assert "look at the opposite picture" in tutor.CANON_RULES.lower()


def test_every_page_gets_a_picture(client):
    """
    The picture is the part these children look at. Leaving "should there be
    one" to the model's judgement produced lesson screens with nothing above
    the words, so a scene is now required whatever else the page gets.
    """
    from app.services import tutor

    # A picture on its own is a complete visual.
    only = tutor._clean_visual({"kind": "none", "purpose": "p",
                                "scene": "a bean seed sprouting in soil"})
    assert only is not None and only["scene"]

    # No scene means no visual at all — there is nothing to show.
    assert tutor._clean_visual({"kind": "none", "purpose": "x"}) is None
    assert tutor._clean_visual(None) is None


def test_a_broken_diagram_does_not_take_the_picture_with_it(client):
    """
    A wrong diagram is worse than none — the child believes the diagram. But
    dropping the whole visual because the diagram was malformed leaves the
    screen bare, which is the failure this all started from.
    """
    from app.services import tutor

    spec = tutor._clean_visual({"kind": "hundredths_grid", "purpose": "p",
                                "scene": "a chocolate bar in equal pieces",
                                "total": 100, "shaded": 900})
    assert spec is not None
    assert spec["kind"] == "none"          # the bad diagram is gone
    assert spec["scene"]                   # the picture survives


def test_a_page_still_gets_a_visual_with_no_image_quota(client, content, monkeypatch):
    """
    The account ran out of image quota, and a picture that depends on a paid
    allowance is not a picture you can promise a child every page.

    So the drawn diagram is the guaranteed one: generated by the text model,
    drawn by the app, no quota and no network needed to render it.
    """
    from app.services import llm, tutor

    def fake_json(instruction, **kwargs):
        if "Do NOT answer" in instruction:
            return llm.JSONResponse(
                data={"kind": "labelled_parts", "purpose": "names the parts",
                      "items": [{"label": "Roots", "note": "take in water"},
                                {"label": "Stem", "note": "carries it up"},
                                {"label": "Leaves", "note": "make food"}]},
                engine="gemini", latency_ms=1)
        return llm.JSONResponse(
            data={"chunks": ["Roots take in water."],
                  "visual": {"kind": "none", "purpose": "p"}},
            engine="gemini", latency_ms=1)

    monkeypatch.setattr(llm, "generate_json", fake_json)

    with get_conn() as conn:
        sid = conn.execute("SELECT id FROM students WHERE external_id = ?",
                           (SID,)).fetchone()["id"]
        reply = tutor.rendition(conn, sid, content["page_ids"][0],
                                regenerate=True)

    assert reply.visual is not None, "nothing above the words"
    assert reply.visual["kind"] == "labelled_parts"
    assert len(reply.visual["items"]) == 3


def test_quota_refusal_backs_off_instead_of_retrying_every_page(client):
    """
    Three doomed image calls per lesson page is a child waiting for nothing.
    """
    from app.services import llm

    import time

    from app.config import settings

    assert llm.image_quota_blocked() is False

    was_blocked = llm._quota_blocked_until
    was_key = settings.gemini_api_key
    try:
        # A key, so the call gets past the "not configured" gate and reaches
        # the backoff. No request is made — that is the point.
        settings.gemini_api_key = "test-key"
        llm._quota_blocked_until = time.time() + 60
        assert llm.image_quota_blocked() is True

        image, _, error = llm.generate_image("a plant")
        assert image is None
        assert "quota" in error.lower()
    finally:
        llm._quota_blocked_until = was_blocked
        settings.gemini_api_key = was_key


def test_the_image_call_asks_for_an_image(client):
    """
    Souly said "I will draw the bean seeds for you now" and nothing appeared.
    The call was succeeding and returning a paragraph ABOUT the picture,
    because nothing told the model to reply with an image.
    """
    import inspect
    from app.services import llm

    assert "responseModalities" in inspect.getsource(llm.generate_image)


def test_a_malformed_diagram_is_dropped_rather_than_drawn_wrong(client):
    """
    A diagram that disagrees with the page is worse than no diagram, because
    the child will believe the diagram.
    """
    from app.services import tutor

    # More shaded than there are squares.
    assert tutor._clean_visual({"kind": "hundredths_grid", "purpose": "p",
                                "total": 100, "shaded": 140}) is None
    # A number line that goes nowhere.
    assert tutor._clean_visual({"kind": "number_line", "purpose": "p",
                                "min": 5, "max": 5,
                                "marks": [{"value": 5}]}) is None
    # A number line with nothing marked on it.
    assert tutor._clean_visual({"kind": "number_line", "purpose": "p",
                                "min": 0, "max": 1, "marks": []}) is None


def test_a_good_grid_survives(client):
    from app.services import tutor
    spec = tutor._clean_visual({"kind": "hundredths_grid",
                                "purpose": "shows what one hundredth is",
                                "total": 100, "shaded": 45})
    assert spec["kind"] == "hundredths_grid"
    assert spec["shaded"] == 45
    assert spec["purpose"]


def test_an_illustration_is_never_asked_to_draw_numbers(client):
    """
    Image models garble digits, and a wrong number in a picture reads to a
    struggling child as their own mistake. Every label is drawn by the app on
    top of the image instead, so a scene containing digits is rejected
    outright rather than sent.
    """
    from app.services import tutor

    assert tutor._clean_visual({"kind": "illustration", "purpose": "p",
                                "scene": "a chart showing 0.45 shaded"}) is None

    ok = tutor._clean_visual({"kind": "illustration", "purpose": "p",
                              "scene": "a bean seed sprouting in soil"})
    assert ok["scene"] == "a bean seed sprouting in soil"
    # Keyed on the scene, so two children shown the same thing share one
    # generated picture instead of generating it twice.
    same = tutor._clean_visual({"kind": "illustration", "purpose": "other",
                                "scene": "A Bean Seed Sprouting In Soil"})
    assert ok["key"] == same["key"]


def test_the_lesson_screen_no_longer_serves_the_book_page(client, content):
    """
    A whole scanned textbook page shrunk into half a pane is unreadable, and
    it is the source rather than the teaching. The endpoint still exists for
    teacher review; the child's lesson must not point at it.
    """
    lesson_id = content["lesson_id"]
    body = client.get(api(f"/lessons/{lesson_id}/pages/1")).json()
    assert "image_url" not in body
    assert "visual" in body


def test_the_illustration_prompt_never_comes_from_the_client(client, content):
    """
    The scene is read from the rendition this child already has, written by
    the model from the book. A client that could pass its own prompt would be
    an open image generator wearing a lesson's clothes.
    """
    page_id = content["page_ids"][0]

    with get_conn() as conn:
        conn.execute(
            "UPDATE page_renditions SET visual_json = NULL WHERE page_id = ?",
            (page_id,),
        )

    # No illustration in this child's rendition -> 404. Not "draw me anything".
    r = client.get(api(f"/curriculum/pages/{page_id}/illustration"))
    assert r.status_code == 404

    # And a scene passed in by the caller is ignored rather than drawn.
    r = client.get(api(f"/curriculum/pages/{page_id}/illustration"),
                   params={"scene": "a photograph of a real person"})
    assert r.status_code == 404


def test_generation_refuses_unverified_books(client):
    """The verification rule holds for generation too."""
    from app.services import curriculum as curriculum_service
    from app.services import tutor

    with get_conn() as conn:
        book_id = conn.execute(
            """
            INSERT INTO curriculum_books
                (code, title, subject, subject_code, grade, filename, is_verified)
            VALUES ('gen-unver','Unchecked','Testing','TST','5','gu.pdf', 0)
            """
        ).lastrowid
        conn.execute(
            "INSERT INTO curriculum_pages (book_id, lesson, page, lesson_order) "
            "VALUES (?, 'Unverified', 1, 0)",
            (book_id,),
        )
        topic_id = conn.execute(
            """
            INSERT INTO topics (code, subject, title, is_verified, book_id,
                                lesson_label, created_at)
            VALUES ('GEN.UNVER','TST','Unverified',0,?,'Unverified',?)
            """,
            (book_id, utc_now_iso()),
        ).lastrowid

    cache = curriculum_service.cache_dir("gen-unver")
    cache.mkdir(parents=True, exist_ok=True)
    curriculum_service.text_path("gen-unver", 1).write_text(
        "Some unapproved text.", encoding="utf-8")

    with get_conn() as conn:
        result = tutor.generate_questions(conn, topic_id, count=2)
    assert result["questions"] == []
    assert "not verified" in result["reasons"][0].lower()


def test_generation_refuses_a_lesson_with_no_ingested_text(client):
    """
    A page that was never ingested, or whose OCR came back empty, has no
    grounding — and a model asked to teach from nothing will fill the silence.
    Refusing is the only safe answer.
    """
    from app.services import tutor

    with get_conn() as conn:
        book_id = conn.execute(
            """
            INSERT INTO curriculum_books
                (code, title, subject, subject_code, grade, filename, is_verified)
            VALUES ('no-text','Not Ingested','Testing','TST','5','nt.pdf', 1)
            """
        ).lastrowid
        conn.execute(
            "INSERT INTO curriculum_pages (book_id, lesson, page, lesson_order) "
            "VALUES (?, 'No Text', 1, 0)",
            (book_id,),
        )
        topic_id = conn.execute(
            """
            INSERT INTO topics (code, subject, title, is_verified, book_id,
                                lesson_label, created_at)
            VALUES ('NO.TEXT','TST','No Text',1,?,'No Text',?)
            """,
            (book_id, utc_now_iso()),
        ).lastrowid

    with get_conn() as conn:
        result = tutor.generate_questions(conn, topic_id, count=2)
    assert result["questions"] == []
    assert "no ingested text" in result["reasons"][0].lower()


# =============================================================================
# The generated-question validator
#
# This is the gate between the model and a child. Every case here is one that
# would otherwise put a broken question on screen.
# =============================================================================

def _good_question(**overrides):
    base = {
        "prompt": "What is two add two?",
        "options": ["4", "3", "5", "22"],
        "correct_index": 0,
        "explanation": "Two and two more makes four.",
        "hint": "Count on from the first number.",
        "worked_solution": "2 + 2 = 4",
        "difficulty": 1,
    }
    base.update(overrides)
    return base


def test_validator_accepts_a_good_question():
    from app.services.tutor import _validate_question
    assert _validate_question(_good_question()) is None


def test_validator_rejects_wrong_option_count():
    from app.services.tutor import _validate_question
    assert _validate_question(_good_question(options=["4", "3"])) is not None


def test_validator_rejects_duplicate_options():
    from app.services.tutor import _validate_question
    problem = _validate_question(_good_question(options=["4", "4", "5", "6"]))
    assert problem and "duplicate" in problem.lower()


def test_validator_rejects_out_of_range_index():
    from app.services.tutor import _validate_question
    assert _validate_question(_good_question(correct_index=7)) is not None
    assert _validate_question(_good_question(correct_index=-1)) is not None


def test_validator_rejects_a_prompt_that_leaks_the_answer():
    from app.services.tutor import _validate_question
    problem = _validate_question(_good_question(
        prompt="Which one is fifteen, the correct answer?",
        options=["fifteen", "twelve", "twenty", "thirty"],
        correct_index=0,
    ))
    assert problem and "leak" in problem.lower()


def test_validator_rejects_a_hint_that_leaks_the_answer():
    from app.services.tutor import _validate_question
    problem = _validate_question(_good_question(
        options=["fifteen", "twelve", "twenty", "thirty"],
        correct_index=0,
        hint="The answer is fifteen.",
    ))
    assert problem and "leak" in problem.lower()


def test_validator_requires_a_worked_solution():
    """No worked solution means hints get built on a re-derivation, which can
    be wrong. Reject rather than risk it."""
    from app.services.tutor import _validate_question
    assert _validate_question(_good_question(worked_solution="")) is not None


def test_validator_rejects_blank_options():
    from app.services.tutor import _validate_question
    assert _validate_question(_good_question(options=["4", "", "5", "6"])) is not None


# =============================================================================
# Voice and naming
# =============================================================================

def test_chat_is_anchored_to_the_page_on_screen(client, page_and_question):
    """
    The child is almost always asking about what's in front of them. Retrieval
    alone can drift to a different lesson sharing vocabulary.
    """
    r = client.post(api("/chat"), json={
        "message": "what does this mean",
        "page_id": page_and_question["page_id"],
    })
    assert r.status_code == 200
    body = r.json()
    assert body["grounded"] is True
    assert any(ref.get("on_screen") for ref in body["source_refs"])


def test_souly_never_promises_permanence(client):
    """
    Moxie bricked every unit when its company folded, and the children who had
    been told it was their best friend took it hardest. Souly is a study buddy
    for a study session, and the greeting must not say otherwise.
    """
    from app.services import tutor
    with get_conn() as conn:
        sid = conn.execute("SELECT id FROM students WHERE external_id = ?",
                           (SID,)).fetchone()["id"]
        text = tutor.greeting(conn, sid).lower()
    for phrase in ("always be here", "best friend", "missed you", "forever"):
        assert phrase not in text


def test_system_prompt_forbids_attachment_language():
    from app.services.llm import SYSTEM_PROMPT
    lowered = SYSTEM_PROMPT.lower()
    assert "never promise to always be there" in lowered
    assert "souly" in lowered
    assert "righty" not in lowered


# =============================================================================
# Sign-in — the picker and the picture password
# =============================================================================

def test_profile_picker_leaks_nothing_about_classmates(client):
    """
    The sign-in screen is the one place every child sees every other child.
    It must show a face and a name and nothing else — no stars, no support
    profile, no progress.
    """
    body = client.get("/api/auth/profiles").json()
    assert body["profiles"]
    for p in body["profiles"]:
        assert set(p) == {
            "external_id", "display_name", "avatar", "avatar_color",
            "needs_password", "needs_onboarding", "locked_seconds",
        }
    assert len(body["pictures"]) >= 8
    assert body["password_length"] == 3


def test_first_login_sets_a_picture_password(client):
    r = client.post("/api/auth/set-password", json={
        "student_ext_id": SID, "pictures": ["cat", "rocket", "star"]})
    assert r.status_code == 200
    body = r.json()
    assert body["token"]
    assert body["display_name"] == "Testy"


def test_password_cannot_be_silently_changed(client):
    """
    Otherwise the first child to reach the tablet could lock a classmate out
    of their own profile — exactly the mischief the password prevents.
    """
    r = client.post("/api/auth/set-password", json={
        "student_ext_id": SID, "pictures": ["dog", "moon", "cake"]})
    assert r.status_code == 409
    assert "teacher" in r.json()["detail"].lower()


def test_login_with_the_right_pictures(client):
    r = client.post("/api/auth/login", json={
        "student_ext_id": SID, "pictures": ["cat", "rocket", "star"]})
    assert r.status_code == 200
    assert r.json()["token"]


def test_order_matters(client):
    r = client.post("/api/auth/login", json={
        "student_ext_id": SID, "pictures": ["star", "rocket", "cat"]})
    assert r.status_code == 401


def test_wrong_pictures_give_a_gentle_vague_message(client):
    """
    Never "wrong on the second picture" — that halves the search space and
    tells a guessing classmate exactly how close they are.
    """
    r = client.post("/api/auth/login", json={
        "student_ext_id": SID, "pictures": ["dog", "dog", "dog"]})
    assert r.status_code == 401
    detail = r.json()["detail"].lower()
    assert "second" not in detail and "first" not in detail
    # Invites another attempt without saying how close they got.
    assert "again" in detail or "another go" in detail


def test_unknown_picture_is_rejected(client):
    r = client.post("/api/auth/login", json={
        "student_ext_id": SID, "pictures": ["cat", "banana", "star"]})
    assert r.status_code == 422


def test_password_hash_is_never_the_sequence(client):
    """The database travels to a competition on a laptop."""
    with get_conn() as conn:
        stored = conn.execute(
            "SELECT picture_password_hash FROM students WHERE external_id = ?",
            (SID,)).fetchone()["picture_password_hash"]
    assert stored
    assert "cat" not in stored
    assert stored.startswith("pbkdf2_sha256$")


def test_repeated_failures_lock_the_tile(client):
    """Five wrong tries buys a few minutes — enough to stop a determined
    classmate, gentle enough that a mis-tap doesn't end the lesson."""
    from app.routers.auth import MAX_FAILED

    with get_conn() as conn:
        conn.execute("UPDATE students SET failed_logins = 0, locked_until = NULL "
                     "WHERE external_id = ?", (SID,))

    last = None
    for _ in range(MAX_FAILED):
        last = client.post("/api/auth/login", json={
            "student_ext_id": SID, "pictures": ["dog", "moon", "cake"]})
    assert last.status_code == 429

    # And the right password is refused while locked.
    r = client.post("/api/auth/login", json={
        "student_ext_id": SID, "pictures": ["cat", "rocket", "star"]})
    assert r.status_code == 429

    with get_conn() as conn:
        conn.execute("UPDATE students SET failed_logins = 0, locked_until = NULL "
                     "WHERE external_id = ?", (SID,))


def test_token_identifies_the_student(client):
    token = client.post("/api/auth/login", json={
        "student_ext_id": SID, "pictures": ["cat", "rocket", "star"]
    }).json()["token"]

    me = client.get("/api/auth/me", headers={"X-Souly-Token": token})
    assert me.status_code == 200
    assert me.json()["student_ext_id"] == SID

    assert client.get("/api/auth/me").status_code == 401
    assert client.get("/api/auth/me",
                      headers={"X-Souly-Token": "nonsense"}).status_code == 401


def test_teacher_reset_lets_the_child_choose_again(client):
    client.post(f"/api/auth/reset-password/{SID}")
    body = client.get("/api/auth/profiles").json()
    me = next(p for p in body["profiles"] if p["external_id"] == SID)
    assert me["needs_password"] is True


# =============================================================================
# The entry activity
# =============================================================================

def test_activity_shows_the_whole_plan_up_front(client):
    """
    Unpredictability, not difficulty, is what drives anxiety in this
    population. The child sees the map before anything starts.
    """
    body = client.get(api("/onboarding")).json()
    assert body["plan"]
    assert body["estimated_minutes"] <= 10
    assert body["reasoning_items"]
    assert body["interests"]
    # Framed as helping, not being tested. Word-boundary match: the seeded
    # child is called "Testy", and substring matching would flag her name.
    import re
    assert "help me" in body["intro"].lower()
    assert not re.search(r"\btest(s|ed|ing)?\b", body["intro"], re.I)
    assert not re.search(r"\bquiz(zes)?\b", body["intro"], re.I)


def test_items_never_ship_the_answer_or_the_prompts(client):
    """
    The prompt ladder is the measuring instrument. If it's in the page source
    a child can read ahead, and the prompt count stops meaning anything.
    """
    body = client.get(api("/onboarding")).json()
    for item in body["reasoning_items"] + body["modality_items"]:
        assert "correct_index" not in item
        assert "prompts" not in item
        assert item["total_prompts"] >= 0


def test_the_answer_is_not_always_the_first_option(client):
    """
    Caught by a browser run: every item had been authored with the correct
    option first. A child who taps the top button scores full marks, prompt
    counts collapse to zero, and the instrument measures nothing.
    """
    from app.onboarding_items import MODALITY_ITEMS, REASONING_ITEMS

    items = REASONING_ITEMS + MODALITY_ITEMS
    positions = {i["correct_index"] for i in items}
    assert len(positions) >= 3, f"Answers clustered at positions {positions}"
    first = sum(1 for i in items if i["correct_index"] == 0)
    assert first <= len(items) // 3, "Too many answers sit at position 1"
    for item in items:
        assert 0 <= item["correct_index"] < len(item["options"])


def test_answer_positions_are_the_same_on_every_boot(client):
    """
    Spread, but not shuffled. Randomising per session would make two
    children's prompt counts incomparable — Caffrey, Fuchs & Fuchs (2008):
    the predictive value lives in non-contingent administration.
    """
    import importlib

    from app import onboarding_items

    before = [(i["code"], i["correct_index"], tuple(i["options"]))
              for i in onboarding_items.REASONING_ITEMS]
    reloaded = importlib.reload(onboarding_items)
    after = [(i["code"], i["correct_index"], tuple(i["options"]))
             for i in reloaded.REASONING_ITEMS]
    assert before == after


def test_prompts_are_fixed_and_identical_every_time(client):
    """
    Caffrey, Fuchs & Fuchs (2008): dynamic assessment's predictive advantage
    lives in NON-CONTINGENT feedback. If these varied per child the prompt
    counts wouldn't be comparable.
    """
    first = client.post(api("/onboarding/prompt"),
                        json={"item_code": "SER.1", "tier": 1}).json()
    second = client.post(api("/onboarding/prompt"),
                         json={"item_code": "SER.1", "tier": 1}).json()
    assert first["text"] == second["text"]
    assert first["text"]


def test_prompt_ladder_climbs_to_a_worked_model(client):
    for tier in (1, 2, 3):
        body = client.post(api("/onboarding/prompt"),
                           json={"item_code": "SER.1", "tier": tier}).json()
        assert body["is_final"] is False
        assert body["next_tier"] == tier + 1

    final = client.post(api("/onboarding/prompt"),
                        json={"item_code": "SER.1", "tier": 4}).json()
    assert final["is_final"] is True
    assert final["next_tier"] is None


def test_prompt_tiers_1_and_2_are_metacognitive_not_answers(client):
    """
    Rungs 1-2 should ask the child what to look at. Rung 4 may state the rule.
    That split is what makes the prompt count mean 'needs strategy help' vs
    'needs the content re-taught'.
    """
    from app.onboarding_items import find_item
    item = find_item("SER.1")
    early = " ".join(item["prompts"][:2]).lower()
    # The gentle rungs must not simply give the pattern away.
    assert "blue" not in early or "?" in early


def test_scoring_produces_a_low_instruction_profile(client):
    """A child who needs no prompts should be pitched with less support."""
    client.delete(api("/onboarding"))
    from app.onboarding_items import REASONING_ITEMS, find_item

    for item in REASONING_ITEMS:
        client.post(api("/onboarding/attempt"), json={
            "item_code": item["code"],
            "answer_index": find_item(item["code"])["correct_index"],
            "prompts_used": 0, "first_attempt_ms": 6000,
            "total_ms": 8000, "attempts": 1})

    profile = client.post(api("/onboarding/finish")).json()
    assert profile["instruction_need"] == "low"
    assert profile["items_solved_unaided"] == len(REASONING_ITEMS)


def test_scoring_produces_a_task_specific_profile(client):
    """A child who needed the rule spelled out should get content re-taught."""
    client.delete(api("/onboarding"))
    from app.onboarding_items import REASONING_ITEMS, find_item

    for item in REASONING_ITEMS:
        client.post(api("/onboarding/attempt"), json={
            "item_code": item["code"],
            "answer_index": find_item(item["code"])["correct_index"],
            "prompts_used": 4, "first_attempt_ms": 12000,
            "total_ms": 40000, "attempts": 3})

    profile = client.post(api("/onboarding/finish")).json()
    assert profile["instruction_need"] == "task_specific"


def test_confidence_stays_low_after_one_session(client):
    """
    Courchesne (2015): autistic children's measured performance took ~4 short
    sessions to stabilise, and zero of thirty completed a standard test on the
    first go. A day-one profile must never be presented as settled.
    """
    profile = client.get(api("/onboarding/profile")).json()
    assert profile["has_profile"] is True
    assert profile["confidence"] <= 0.5


def test_skipping_is_free_and_recorded_as_missing(client):
    client.delete(api("/onboarding"))
    r = client.post(api("/onboarding/skip"),
                    json={"item_code": "SER.1", "total_ms": 2000})
    assert r.status_code == 200

    with get_conn() as conn:
        sid = conn.execute("SELECT id FROM students WHERE external_id = ?",
                           (SID,)).fetchone()["id"]
        row = conn.execute(
            "SELECT * FROM onboarding_responses WHERE student_id = ? "
            "AND item_code = 'SER.1' ORDER BY id DESC LIMIT 1",
            (sid,)).fetchone()
    assert row["skipped"] == 1
    assert row["solved"] is None      # missing, never failure


def test_latency_baseline_reaches_the_stall_detector(client):
    """
    The whole reason we time the first attempt: on day one the stall detector
    has no live data and would otherwise guess. A fixed threshold would mark
    this cohort as permanently stuck.
    """
    client.delete(api("/onboarding"))
    from app.onboarding_items import REASONING_ITEMS, find_item
    from app.services import tutor

    for item in REASONING_ITEMS:
        client.post(api("/onboarding/attempt"), json={
            "item_code": item["code"],
            "answer_index": find_item(item["code"])["correct_index"],
            "prompts_used": 1, "first_attempt_ms": 20000,
            "total_ms": 25000, "attempts": 1})
    client.post(api("/onboarding/finish"))

    with get_conn() as conn:
        sid = conn.execute("SELECT id FROM students WHERE external_id = ?",
                           (SID,)).fetchone()["id"]
        conn.execute("DELETE FROM page_activity WHERE student_id = ?", (sid,))

    with get_conn() as conn:
        threshold = tutor.stall_threshold(conn, sid)

    # A slow child gets a generous threshold rather than the default.
    assert threshold > tutor.STALL_FLOOR_SECONDS * 2


def test_profile_reaches_the_llm_prompt(client):
    """
    A profile nothing reads is a profile that does nothing. This asserts the
    measured instruction need actually changes the words sent to Gemini.
    """
    from app.services import tutor
    from app.services.llm import _profile_block

    with get_conn() as conn:
        sid = conn.execute("SELECT id FROM students WHERE external_id = ?",
                           (SID,)).fetchone()["id"]
        profile = tutor.load_profile(conn, sid)

    assert "instruction_need" in profile
    block = _profile_block(profile).lower()
    assert "measured" in block
    # Low confidence must be admitted, not asserted away.
    assert "hold it loosely" in block


def test_no_learning_style_field_exists_anywhere(client):
    """
    A guard, not a test of behaviour.

    89% of teachers believe in matching instruction to a learning style, so
    the pressure to add this column will recur. Pashler et al. (2008) found
    essentially no study that could validate it. If someone adds
    visual/auditory/kinaesthetic to the profile, this fails.
    """
    with get_conn() as conn:
        cols = [r[1].lower() for r in
                conn.execute("PRAGMA table_info(learner_profiles)")]
    banned = ("learning_style", "visual", "auditory", "kinaesthetic",
              "kinesthetic", "vark", "style")
    for col in cols:
        for word in banned:
            assert word not in col, f"learner_profiles.{col} looks like a learning style"


def test_interests_are_recorded_for_use_in_content(client):
    r = client.post(api("/onboarding/interests"),
                    json={"interests": ["dinosaurs", "space", "not-a-real-one"]})
    assert r.status_code == 200
    # Unknown codes are dropped rather than stored.
    assert set(r.json()["interests"]) == {"dinosaurs", "space"}


def test_preferences_land_in_settings_not_the_learner_profile(client):
    """
    They're accessibility settings collected pleasantly, not measured traits.
    Keeping them apart stops "prefers calm colours" being read later as a
    cognitive characteristic.
    """
    client.post(api("/onboarding/preferences"),
                json={"font_size": "large", "reduce_motion": True})
    settings = client.get(api("/settings")).json()
    assert settings["font_size"] == "large"
    assert settings["reduce_motion"] == 1
