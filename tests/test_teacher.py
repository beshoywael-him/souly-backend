"""
The teacher's dashboard, and the class list the camera reads.

Two surfaces, added together because they are the two halves of the same
sentence: the camera reads /students to learn who it may report on, and the
teacher reads /api/teacher/board to see what it reported.

The tests that matter most here are the ones about what a teacher may NOT do.
A dashboard that can mark a flag `done` is a dashboard that can record a
lesson which never happened, and a dashboard that can name somebody else as
the reviewer is one that can forge an approval.
"""

import sqlite3

import pytest

from app.security import hash_secret

TEACHER_EMAIL = "dash@souly.local"
TEACHER_PASSWORD = "classroom-123"


# =============================================================================
# Fixtures
# =============================================================================

@pytest.fixture(scope="module")
def teacher_account(client):
    """A teacher with a password we know, created once for this module."""
    from app.db import get_conn
    from app.models import utc_now_iso

    with get_conn() as conn:
        row = conn.execute(
            "SELECT id FROM teachers WHERE email = ?", (TEACHER_EMAIL,)
        ).fetchone()
        if row is None:
            cur = conn.execute(
                "INSERT INTO teachers (full_name, email, password_hash, title, "
                "initials, avatar_color, is_homeroom, created_at) "
                "VALUES (?,?,?,?,?,?,1,?)",
                ("Sarah Ahmed", TEACHER_EMAIL, hash_secret(TEACHER_PASSWORD),
                 "Homeroom Teacher", "SA", "#7C3AED", utc_now_iso()),
            )
            return cur.lastrowid
        return row["id"]


@pytest.fixture()
def token(client, teacher_account):
    r = client.post("/api/teacher/auth/login",
                    json={"email": TEACHER_EMAIL, "password": TEACHER_PASSWORD})
    assert r.status_code == 200, r.text
    return r.json()["token"]


@pytest.fixture()
def auth(token):
    return {"X-Souly-Teacher": token}


def make_flag(client, student="stu-test", flag_type="gaze_away",
              confidence=0.8, **kw):
    r = client.post("/flags", json={
        "student_external_id": student,
        "flag_type": flag_type,
        "confidence": confidence,
        "duration_ms": 6200,
        **kw,
    })
    assert r.status_code == 201, r.text
    return r.json()["flag"]["id"]


# =============================================================================
# GET /students — what the camera reads
# =============================================================================

class TestRoster:

    def test_it_lists_the_class_with_each_childs_own_threshold(self, client):
        r = client.get("/students")
        assert r.status_code == 200
        by_id = {s["external_id"]: s for s in r.json()["students"]}
        assert "stu-test" in by_id
        # Set to 8000 in conftest: this child is given longer before anyone
        # says anything about them, and the camera must honour it.
        assert by_id["stu-test"]["drift_threshold_ms"] == 8000

    def test_inactive_children_are_left_out_by_default(self, client):
        ids = {s["external_id"] for s in client.get("/students").json()["students"]}
        assert "stu-inactive" not in ids

    def test_they_can_be_asked_for_explicitly(self, client):
        ids = {s["external_id"] for s in
               client.get("/students?active_only=false").json()["students"]}
        assert "stu-inactive" in ids

    def test_it_carries_nothing_private(self, client):
        """
        This endpoint has no login, so what it exposes is the whole argument
        for that being acceptable: a first name, a year group and a number of
        milliseconds. No progress, no mastery, no assessment, no contact
        details.
        """
        student = client.get("/students").json()["students"][0]
        allowed = {"external_id", "display_name", "grade",
                   "drift_threshold_ms", "is_active"}
        assert set(student) == allowed

    def test_topic_codes_are_available_for_the_cameras_config(self, client):
        r = client.get("/topics/codes")
        assert r.status_code == 200
        assert "topics" in r.json()


# =============================================================================
# Sign-in
# =============================================================================

class TestTeacherAuth:

    def test_a_good_password_returns_a_token(self, client, teacher_account):
        r = client.post("/api/teacher/auth/login",
                        json={"email": TEACHER_EMAIL,
                              "password": TEACHER_PASSWORD})
        assert r.status_code == 200
        assert r.json()["token"]
        assert r.json()["teacher"]["full_name"] == "Sarah Ahmed"

    def test_a_wrong_password_is_refused(self, client, teacher_account):
        r = client.post("/api/teacher/auth/login",
                        json={"email": TEACHER_EMAIL, "password": "nope"})
        assert r.status_code == 401

    def test_an_unknown_email_gives_the_same_answer_as_a_wrong_password(
            self, client, teacher_account):
        """
        A different message would tell an attacker which addresses are real
        staff accounts, which is a list a school should not publish.
        """
        wrong_pw = client.post("/api/teacher/auth/login",
                               json={"email": TEACHER_EMAIL, "password": "nope"})
        no_such = client.post("/api/teacher/auth/login",
                              json={"email": "ghost@souly.local", "password": "nope"})
        assert wrong_pw.status_code == no_such.status_code == 401
        assert wrong_pw.json()["detail"] == no_such.json()["detail"]

    def test_the_board_needs_a_token(self, client):
        assert client.get("/api/teacher/board").status_code == 401

    def test_a_junk_token_is_refused(self, client):
        r = client.get("/api/teacher/board",
                       headers={"X-Souly-Teacher": "not-a-real-token"})
        assert r.status_code == 401

    def test_a_student_token_cannot_open_the_dashboard(self, client, auth):
        """
        The reason teachers got their own table in schema_v8. A token from one
        realm presented to another must not resolve, whatever the query says.
        """
        from app.db import get_conn

        with get_conn() as conn:
            student_token = conn.execute(
                "SELECT token FROM auth_tokens LIMIT 1"
            ).fetchone()

        if student_token is None:      # no student has signed in during this run
            pytest.skip("no student token in this database")

        r = client.get("/api/teacher/board",
                       headers={"X-Souly-Teacher": student_token["token"]})
        assert r.status_code == 401

    def test_me_resumes_a_session(self, client, auth):
        r = client.get("/api/teacher/auth/me", headers=auth)
        assert r.status_code == 200
        assert r.json()["teacher"]["email"] == TEACHER_EMAIL

    def test_logout_invalidates_the_token(self, client, token):
        headers = {"X-Souly-Teacher": token}
        assert client.get("/api/teacher/board", headers=headers).status_code == 200
        client.post("/api/teacher/auth/logout", json={"token": token})
        assert client.get("/api/teacher/board", headers=headers).status_code == 401


# =============================================================================
# The board
# =============================================================================

class TestBoard:

    def test_it_returns_the_queue_the_roster_and_the_counts_in_one_call(
            self, client, auth):
        make_flag(client)
        body = client.get("/api/teacher/board", headers=auth).json()
        assert {"queue", "roster", "handled", "counts", "teacher"} <= set(body)
        assert body["counts"]["pending"] >= 1

    def test_a_queued_flag_names_the_child_and_how_long_they_drifted(
            self, client, auth):
        fid = make_flag(client, duration_ms=7300)
        queue = client.get("/api/teacher/board", headers=auth).json()["queue"]
        # Find it by id. Timestamps are second-resolution, so several flags
        # raised in the same second have no defined order between them and
        # "the first one in the list" is not this one.
        item = next(i for i in queue if i["id"] == fid)
        assert item["student_name"]
        assert item["duration_ms"] == 7300
        assert item["seconds_ago"] is not None

    def test_a_low_confidence_flag_never_reaches_the_queue(self, client, auth):
        """
        The noise floor, seen from the teacher's side. A camera that publishes
        everything honestly still must not fill this screen with guesses.
        """
        before = len(client.get("/api/teacher/board", headers=auth).json()["queue"])
        make_flag(client, confidence=0.2)
        after = client.get("/api/teacher/board", headers=auth).json()["queue"]
        assert len(after) == before

    def test_the_roster_carries_a_state_but_never_a_feeling(self, client, auth):
        roster = client.get("/api/teacher/board", headers=auth).json()["roster"]
        assert roster
        allowed_states = {"attending", "drifting", "flagged", "settled"}
        for child in roster:
            assert child["state"] in allowed_states

    def test_it_reports_which_lesson_the_child_drifted_away_from(
            self, client, auth):
        from app.db import get_conn

        with get_conn() as conn:
            conn.execute(
                "INSERT OR IGNORE INTO topics (code, subject, title, grade) "
                "VALUES ('T-CV-1', 'Mathematics', 'Decimals', '5')"
            )

        make_flag(client, topic_code="T-CV-1")
        queue = client.get("/api/teacher/board", headers=auth).json()["queue"]
        assert any(i["topic_title"] == "Decimals" for i in queue)

    def test_an_unknown_lesson_code_costs_the_topic_not_the_flag(self, client):
        """
        A typo in the camera's config file must not lose a real detection.
        """
        r = client.post("/flags", json={
            "student_external_id": "stu-test",
            "flag_type": "gaze_away",
            "confidence": 0.9,
            "topic_code": "NO-SUCH-LESSON",
        })
        assert r.status_code == 201
        assert r.json()["flag"]["topic_id"] is None


# =============================================================================
# The approval gate
# =============================================================================

class TestReview:

    def test_a_teacher_can_approve(self, client, auth):
        fid = make_flag(client)
        r = client.patch(f"/api/teacher/flags/{fid}",
                         json={"status": "approved"}, headers=auth)
        assert r.status_code == 200
        assert r.json()["status"] == "approved"

    def test_a_teacher_can_dismiss(self, client, auth):
        fid = make_flag(client)
        r = client.patch(f"/api/teacher/flags/{fid}",
                         json={"status": "dismissed"}, headers=auth)
        assert r.status_code == 200

    def test_a_teacher_cannot_mark_a_flag_done(self, client, auth):
        """
        'done' means a child sat with the robot and worked through the topic.
        A teacher ticking it from this screen would be recording a lesson that
        never happened.
        """
        fid = make_flag(client)
        r = client.patch(f"/api/teacher/flags/{fid}",
                         json={"status": "done"}, headers=auth)
        assert r.status_code == 422

    def test_a_teacher_cannot_claim_a_flag_for_the_robot(self, client, auth):
        fid = make_flag(client)
        r = client.patch(f"/api/teacher/flags/{fid}",
                         json={"status": "in_progress"}, headers=auth)
        assert r.status_code == 422

    def test_the_reviewer_comes_from_the_token_not_the_request(
            self, client, auth, teacher_account):
        """
        The body may claim any teacher_id it likes; the recorded reviewer is
        whoever actually signed in.
        """
        fid = make_flag(client)
        client.patch(f"/api/teacher/flags/{fid}",
                     json={"status": "approved", "teacher_id": 99999},
                     headers=auth)

        from app.db import get_conn
        with get_conn() as conn:
            row = conn.execute(
                "SELECT reviewed_by_teacher_id FROM flags WHERE id = ?", (fid,)
            ).fetchone()
        assert row["reviewed_by_teacher_id"] == teacher_account

    def test_reviewing_the_same_flag_twice_is_not_an_error(self, client, auth):
        """A double-tap, or a retry after the router blinked."""
        fid = make_flag(client)
        first = client.patch(f"/api/teacher/flags/{fid}",
                             json={"status": "approved"}, headers=auth)
        second = client.patch(f"/api/teacher/flags/{fid}",
                              json={"status": "approved"}, headers=auth)
        assert first.json()["changed"] is True
        assert second.status_code == 200
        assert second.json()["changed"] is False

    def test_a_resolved_flag_cannot_be_reopened(self, client, auth):
        fid = make_flag(client)
        client.patch(f"/api/teacher/flags/{fid}",
                     json={"status": "dismissed"}, headers=auth)
        r = client.patch(f"/api/teacher/flags/{fid}",
                         json={"status": "approved"}, headers=auth)
        assert r.status_code == 409

    def test_a_missing_flag_is_404(self, client, auth):
        r = client.patch("/api/teacher/flags/999999",
                         json={"status": "approved"}, headers=auth)
        assert r.status_code == 404

    def test_every_review_is_recorded_in_the_audit_trail(self, client, auth):
        fid = make_flag(client)
        client.patch(f"/api/teacher/flags/{fid}",
                     json={"status": "approved", "note": "quiet word after"},
                     headers=auth)
        events = client.get(f"/api/teacher/flags/{fid}/events",
                            headers=auth).json()["events"]
        moves = [(e["from_status"], e["to_status"]) for e in events]
        assert (None, "pending") in moves
        assert ("pending", "approved") in moves
        assert any(e["actor"].startswith("teacher:") for e in events)

    def test_the_review_endpoint_needs_a_token(self, client):
        r = client.patch("/api/teacher/flags/1", json={"status": "approved"})
        assert r.status_code == 401


# =============================================================================
# The whole loop
# =============================================================================

def test_camera_to_teacher_to_robot(client, auth):
    """
    The path the project exists to demonstrate, end to end:
    the camera publishes, the teacher approves, the robot claims it, the
    child finishes, and every step is recorded.
    """
    fid = make_flag(client, confidence=0.74, duration_ms=6200)

    queue = client.get("/api/teacher/board", headers=auth).json()["queue"]
    assert any(i["id"] == fid for i in queue), "the flag never reached the teacher"

    assert client.patch(f"/api/teacher/flags/{fid}",
                        json={"status": "approved"},
                        headers=auth).status_code == 200

    # The robot picks it up and the child works through it. Those two moves
    # belong to the robot, not to this screen, so they go through /flags.
    assert client.patch(f"/flags/{fid}",
                        json={"status": "in_progress",
                              "actor": "robot"}).status_code == 200
    assert client.patch(f"/flags/{fid}",
                        json={"status": "done",
                              "actor": "robot"}).status_code == 200

    events = client.get(f"/api/teacher/flags/{fid}/events",
                        headers=auth).json()["events"]
    assert [e["to_status"] for e in events] == \
        ["pending", "approved", "in_progress", "done"]
