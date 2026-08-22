"""
The parents' hub.

The tests that matter most in this file are the isolation ones. Everything
else here is a convenience check; `test_a_parent_cannot_reach_another_parents_child`
and its neighbours are the ones that would let a family read another family's
child's assessment data if they ever went red.

The sibling case has its own tests because it is the one the interface was
redesigned around: Fayrouz has two sons, and a bug that silently shows one
son's week under the other's name is both plausible and invisible — the page
still renders, the numbers are still real, they are just the wrong child's.
"""

import pytest

from app.db import get_conn
from app.models import utc_now_iso
from app.security import hash_secret


# =============================================================================
# Fixtures
# =============================================================================

@pytest.fixture(scope="module")
def family(client):
    """
    Two families, three children, one of them a sibling pair.

    Mirrors the real seed: Fayrouz has two sons, Karma has one daughter. The
    shapes are what the tests are about, not the names.
    """
    with get_conn() as conn:
        ids = {}
        for ext, name, grade in [
            ("stu-p1", "Beshoy", "5"),
            ("stu-p2", "Atef", "4"),
            ("stu-p3", "Lo2lo2", "5"),
        ]:
            cur = conn.execute(
                "INSERT INTO students (external_id, full_name, display_name, "
                "grade, created_at, updated_at) VALUES (?,?,?,?,?,?)",
                (ext, name, name, grade, utc_now_iso(), utc_now_iso()),
            )
            ids[ext] = cur.lastrowid

        fay = conn.execute(
            "INSERT INTO parents (full_name, email, access_code_hash) VALUES (?,?,?)",
            ("Fayrouz", "fay-test@souly.local", hash_secret("CODE-FAYROUZ")),
        ).lastrowid
        karma = conn.execute(
            "INSERT INTO parents (full_name, email, access_code_hash) VALUES (?,?,?)",
            ("Karma", "karma-test@souly.local", hash_secret("CODE-KARMA")),
        ).lastrowid

        for pid, ext in [(fay, "stu-p1"), (fay, "stu-p2"), (karma, "stu-p3")]:
            conn.execute(
                "INSERT INTO parent_student (parent_id, student_id, relationship) "
                "VALUES (?,?,'mother')",
                (pid, ids[ext]),
            )

        teacher = conn.execute(
            "SELECT id FROM teachers WHERE email = 't@souly.local'"
        ).fetchone()["id"]

        conn.execute(
            "INSERT INTO teacher_notes (student_id, teacher_id, tone, body) "
            "VALUES (?,?,'praise','Beshoy did well today.')",
            (ids["stu-p1"], teacher),
        )
        convo = conn.execute(
            "INSERT INTO conversations (parent_id, teacher_id, student_id) VALUES (?,?,?)",
            (fay, teacher, ids["stu-p1"]),
        ).lastrowid
        conn.execute(
            "INSERT INTO conversation_messages (conversation_id, sender_role, "
            "sender_id, body) VALUES (?, 'teacher', ?, 'Hello Fayrouz.')",
            (convo, teacher),
        )

    return {"students": ids, "fayrouz": fay, "karma": karma, "conversation": convo}


def token_for(client, code):
    res = client.post("/api/parent/auth/login", json={"access_code": code})
    assert res.status_code == 200, res.text
    return res.json()["token"]


@pytest.fixture(scope="module")
def fayrouz(client, family):
    return {"X-Souly-Parent": token_for(client, "CODE-FAYROUZ")}


@pytest.fixture(scope="module")
def karma(client, family):
    return {"X-Souly-Parent": token_for(client, "CODE-KARMA")}


# =============================================================================
# Sign-in
# =============================================================================

def test_a_wrong_access_code_is_rejected(client, family):
    res = client.post("/api/parent/auth/login", json={"access_code": "CODE-NOPE"})
    assert res.status_code == 401


def test_the_rejection_does_not_say_whether_the_code_half_matched(client, family):
    """
    Both a nonsense code and a near-miss must produce the same message.

    Anything that distinguishes them ("no such account" vs "wrong code")
    turns the login into an oracle for which codes exist.
    """
    a = client.post("/api/parent/auth/login", json={"access_code": "CODE-NOPE"})
    b = client.post("/api/parent/auth/login", json={"access_code": "CODE-FAYROUX"})
    assert a.json()["detail"] == b.json()["detail"]


def test_the_access_code_is_case_insensitive(client, family):
    """A parent typing on a phone gets autocapitalisation, or does not."""
    res = client.post("/api/parent/auth/login", json={"access_code": "code-fayrouz"})
    assert res.status_code == 200


def test_login_returns_every_child_in_one_call(client, family):
    """
    The switcher has to exist before the first screen is drawn.

    If the child list arrived in a second request, the hub would render a
    single-child header and then rearrange itself — and a parent who tapped
    during that window would act on the wrong layout.
    """
    res = client.post("/api/parent/auth/login", json={"access_code": "CODE-FAYROUZ"})
    names = sorted(c["display_name"] for c in res.json()["children"])
    assert names == ["Atef", "Beshoy"]


def test_a_single_child_parent_gets_a_list_of_one(client, family):
    res = client.post("/api/parent/auth/login", json={"access_code": "CODE-KARMA"})
    assert [c["display_name"] for c in res.json()["children"]] == ["Lo2lo2"]


def test_me_resumes_the_session_with_the_same_children(client, fayrouz):
    res = client.get("/api/parent/auth/me", headers=fayrouz)
    assert res.status_code == 200
    assert len(res.json()["children"]) == 2


# =============================================================================
# Isolation — the tests that matter
# =============================================================================

def test_a_parent_cannot_reach_another_parents_child(client, karma):
    """Karma asking for Beshoy must not learn anything about Beshoy."""
    res = client.get("/api/parent/children/stu-p1/overview", headers=karma)
    assert res.status_code == 404


def test_the_refusal_is_404_and_not_403(client, karma):
    """
    403 means "this exists and you may not have it". That confirms stu-p1 is a
    real child at this school, which is the fact we are declining to publish.
    404 is the only answer that says nothing.
    """
    res = client.get("/api/parent/children/stu-p1/overview", headers=karma)
    assert res.status_code == 404
    assert "stu-p1" not in res.text


def test_no_token_is_refused(client, family):
    assert client.get("/api/parent/children/stu-p1/overview").status_code == 401


def test_a_garbage_token_is_refused(client, family):
    res = client.get("/api/parent/children/stu-p1/overview",
                     headers={"X-Souly-Parent": "not-a-real-token"})
    assert res.status_code == 401


def test_a_student_token_cannot_open_the_parent_api(client, family):
    """
    The whole reason parent_tokens is a separate table rather than a column on
    auth_tokens. A student token is a valid token — it is simply not in the
    table this endpoint reads, so it cannot be confused for a parent's however
    the query is written.
    """
    with get_conn() as conn:
        conn.execute(
            "INSERT INTO auth_tokens (token, student_id, expires_at) VALUES (?,?,?)",
            ("student-token-abc", family["students"]["stu-p1"], "2099-01-01T00:00:00Z"),
        )

    res = client.get("/api/parent/children/stu-p1/overview",
                     headers={"X-Souly-Parent": "student-token-abc"})
    assert res.status_code == 401


def test_a_parent_cannot_mark_another_parents_note_read(client, karma, family):
    with get_conn() as conn:
        note = conn.execute(
            "SELECT id FROM teacher_notes WHERE student_id = ?",
            (family["students"]["stu-p1"],),
        ).fetchone()["id"]

    assert client.post(f"/api/parent/notes/{note}/read", headers=karma).status_code == 404


def test_a_parent_cannot_read_another_parents_conversation(client, karma, family):
    res = client.get(f"/api/parent/conversations/{family['conversation']}", headers=karma)
    assert res.status_code == 404


def test_a_parent_cannot_post_into_another_parents_conversation(client, karma, family):
    res = client.post(f"/api/parent/conversations/{family['conversation']}/messages",
                      headers=karma, json={"body": "hello"})
    assert res.status_code == 404


def test_a_parent_only_sees_their_own_conversations(client, karma):
    res = client.get("/api/parent/conversations", headers=karma)
    assert res.json()["conversations"] == []


# =============================================================================
# Siblings
# =============================================================================

def test_each_sibling_returns_their_own_overview(client, fayrouz):
    """
    The failure this guards against is silent: both requests succeed, both
    render, and one son's week is shown under the other's name.
    """
    one = client.get("/api/parent/children/stu-p1/overview", headers=fayrouz).json()
    two = client.get("/api/parent/children/stu-p2/overview", headers=fayrouz).json()

    assert one["child"]["display_name"] == "Beshoy"
    assert two["child"]["display_name"] == "Atef"
    assert one["child"]["external_id"] != two["child"]["external_id"]


def test_siblings_in_different_grades_keep_their_own_grade(client, fayrouz):
    one = client.get("/api/parent/children/stu-p1/overview", headers=fayrouz).json()
    two = client.get("/api/parent/children/stu-p2/overview", headers=fayrouz).json()
    assert one["child"]["grade"] == "5"
    assert two["child"]["grade"] == "4"


def test_a_note_about_one_sibling_does_not_appear_under_the_other(client, fayrouz):
    beshoy = client.get("/api/parent/children/stu-p1/notes", headers=fayrouz).json()
    atef = client.get("/api/parent/children/stu-p2/notes", headers=fayrouz).json()
    assert len(beshoy["notes"]) == 1
    assert atef["notes"] == []


def test_a_conversation_row_names_the_child_it_is_about(client, fayrouz):
    """
    Fayrouz can have two threads with the same teacher, one per son. Without
    the child on the row they are indistinguishable in the list.
    """
    row = client.get("/api/parent/conversations", headers=fayrouz).json()["conversations"][0]
    assert row["child"] == "Beshoy"
    assert row["child_ext_id"] == "stu-p1"


def test_the_children_list_flags_that_there_is_more_than_one(client, fayrouz, karma):
    assert client.get("/api/parent/children", headers=fayrouz).json()["multiple"] is True
    assert client.get("/api/parent/children", headers=karma).json()["multiple"] is False


# =============================================================================
# Empty states
# =============================================================================

def test_a_child_who_has_done_nothing_reports_no_scores_rather_than_zero(client, karma):
    """
    Aziz has never opened a lesson. `overall_pct` must be null, not 0.

    A 0 renders as a full-width empty bar and a parent reads it as failure.
    The interface can only tell the difference if the API does.
    """
    data = client.get("/api/parent/children/stu-p3/overview", headers=karma).json()
    assert data["stats"]["overall_pct"] is None
    assert data["stats"]["has_scores"] is False
    assert data["started"] is False


def test_a_subject_with_no_work_is_flagged_rather_than_scored(client, karma):
    data = client.get("/api/parent/children/stu-p3/subjects", headers=karma).json()
    assert all(s["has_data"] is False for s in data["subjects"])


def test_independence_declines_to_compare_against_a_week_with_no_activity(client, karma):
    """
    "0 requests last week, 4 this week" is not a child needing more help; it
    is a child who was not using Souly last week. `comparable` is what stops
    the interface drawing that conclusion.
    """
    data = client.get("/api/parent/children/stu-p3/overview", headers=karma).json()
    assert data["independence"]["comparable"] is False
    assert data["independence"]["improving"] is None


# =============================================================================
# Notes and messages
# =============================================================================

def test_opening_a_note_marks_it_read(client, fayrouz, family):
    before = client.get("/api/parent/children/stu-p1/notes", headers=fayrouz).json()
    assert before["unread"] == 1
    note_id = before["notes"][0]["id"]

    assert client.post(f"/api/parent/notes/{note_id}/read", headers=fayrouz).status_code == 200

    after = client.get("/api/parent/children/stu-p1/notes", headers=fayrouz).json()
    assert after["unread"] == 0
    assert after["notes"][0]["read"] is True


def test_sending_a_message_appends_to_the_thread(client, fayrouz, family):
    cid = family["conversation"]
    before = len(client.get(f"/api/parent/conversations/{cid}", headers=fayrouz).json()["messages"])

    res = client.post(f"/api/parent/conversations/{cid}/messages",
                      headers=fayrouz, json={"body": "Thank you."})
    assert res.status_code == 200
    assert res.json()["from"] == "parent"

    after = client.get(f"/api/parent/conversations/{cid}", headers=fayrouz).json()["messages"]
    assert len(after) == before + 1
    assert after[-1]["body"] == "Thank you."


def test_an_empty_message_is_rejected(client, fayrouz, family):
    res = client.post(f"/api/parent/conversations/{family['conversation']}/messages",
                      headers=fayrouz, json={"body": "   "})
    assert res.status_code == 422


def test_opening_a_thread_marks_the_teachers_messages_read(client, fayrouz, family):
    cid = family["conversation"]
    client.get(f"/api/parent/conversations/{cid}", headers=fayrouz)
    rows = client.get("/api/parent/conversations", headers=fayrouz).json()["conversations"]
    assert next(r for r in rows if r["id"] == cid)["unread"] == 0


def test_starting_a_thread_twice_returns_the_same_one(client, fayrouz, family):
    with get_conn() as conn:
        teacher = conn.execute(
            "SELECT id FROM teachers WHERE email = 't@souly.local'"
        ).fetchone()["id"]

    first = client.post("/api/parent/children/stu-p2/conversations",
                        headers=fayrouz, json={"teacher_id": teacher}).json()
    second = client.post("/api/parent/children/stu-p2/conversations",
                         headers=fayrouz, json={"teacher_id": teacher}).json()

    assert first["created"] is True
    assert second["created"] is False
    assert first["id"] == second["id"]


def test_the_same_teacher_gets_a_separate_thread_per_child(client, fayrouz, family):
    """
    The reason student_id is in the conversation's unique key. One thread per
    (parent, teacher) would mix Beshoy's attention and Atef's reading into a
    single scroll that neither parent nor teacher can follow.
    """
    rows = client.get("/api/parent/conversations", headers=fayrouz).json()["conversations"]
    children = {r["child"] for r in rows}
    assert {"Beshoy", "Atef"} <= children


# =============================================================================
# Settings
# =============================================================================

def test_a_parent_can_change_an_accessibility_setting(client, fayrouz):
    res = client.put("/api/parent/children/stu-p1/support",
                     headers=fayrouz, json={"settings": {"high_contrast": 1}})
    assert res.status_code == 200

    data = client.get("/api/parent/children/stu-p1/support", headers=fayrouz).json()
    assert data["settings"]["high_contrast"] == 1


def test_a_parent_cannot_change_a_setting_outside_the_allowlist(client, fayrouz):
    """
    `camera_enabled` is on the same table but is not the parent's to set — it
    governs the classroom CV rig. The allowlist is why adding a column to
    student_settings cannot silently hand the parent control of it.
    """
    res = client.put("/api/parent/children/stu-p1/support",
                     headers=fayrouz, json={"settings": {"camera_enabled": 0}})
    assert res.status_code == 422


def test_a_parent_cannot_change_settings_for_another_parents_child(client, karma):
    res = client.put("/api/parent/children/stu-p1/support",
                     headers=karma, json={"settings": {"high_contrast": 1}})
    assert res.status_code == 404


def test_the_parent_api_never_moves_a_childs_stars(client, fayrouz, family):
    """
    economy.py is the only writer of stars, XP and levels. Reading the hub
    must not move them — if it could, the parent's screen and the child's own
    screen would start disagreeing, and the child would notice first.
    """
    sid = family["students"]["stu-p1"]
    with get_conn() as conn:
        before = conn.execute("SELECT stars, level FROM students WHERE id=?", (sid,)).fetchone()

    for path in ("overview", "progress", "subjects", "notes", "achievements", "support"):
        client.get(f"/api/parent/children/stu-p1/{path}", headers=fayrouz)
    client.put("/api/parent/children/stu-p1/support",
               headers=fayrouz, json={"settings": {"reduce_motion": 1}})

    with get_conn() as conn:
        after = conn.execute("SELECT stars, level FROM students WHERE id=?", (sid,)).fetchone()

    assert (before["stars"], before["level"]) == (after["stars"], after["level"])


# =============================================================================
# Schema
# =============================================================================

def test_the_access_code_is_never_stored_in_the_clear(client, family):
    """
    The database travels to a competition on a laptop. If it leaks, the codes
    must not leak with it.
    """
    with get_conn() as conn:
        stored = conn.execute(
            "SELECT access_code_hash FROM parents WHERE email = 'fay-test@souly.local'"
        ).fetchone()["access_code_hash"]

    assert "CODE-FAYROUZ" not in stored
    assert stored.startswith("pbkdf2_sha256$")


def test_the_parent_children_view_only_returns_linked_children(client, family):
    with get_conn() as conn:
        rows = conn.execute(
            "SELECT external_id FROM v_parent_children WHERE parent_id = ?",
            (family["karma"],),
        ).fetchall()
    assert [r["external_id"] for r in rows] == ["stu-p3"]
