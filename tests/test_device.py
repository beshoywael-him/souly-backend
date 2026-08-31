"""
The classroom device.

The tests that matter most here are the display-policy ones. A wrong session
duration is embarrassing; a device that blinks at a teacher every twenty
seconds until they stop looking at it is a device that fails silently in real
use, and no error message ever appears.
"""

from datetime import datetime, timedelta, timezone

import pytest

from app.db import get_conn
from app.models import utc_now_iso


DEV = {"X-Souly-Device": "dev-key-room4"}
OTHER_DEV = {"X-Souly-Device": "dev-key-room9"}


def _iso(delta_s: int = 0) -> str:
    return (datetime.now(timezone.utc) + timedelta(seconds=delta_s)) \
        .strftime("%Y-%m-%dT%H:%M:%SZ")


@pytest.fixture(scope="module")
def room(client):
    """
    One class of four, two teachers, two devices.

    Sarah leads Primary 5 Maths. Noha teaches Primary 4 and is NOT on Sarah's
    class — she is here so "not your class" has something real to refuse.
    """
    with get_conn() as conn:
        students = {}
        for ext, name, support in [
            ("stu-d1", "Beshoy", "adhd"),
            ("stu-d2", "Lo2lo2", "visual_impairment"),
            ("stu-d3", "Atef", "none"),
            ("stu-d4", "Aziz", "none"),
        ]:
            cur = conn.execute(
                "INSERT INTO students (external_id, full_name, display_name, grade, "
                "support_profile, created_at, updated_at) VALUES (?,?,?,'5',?,?,?)",
                (ext, name, name, support, utc_now_iso(), utc_now_iso()),
            )
            students[ext] = cur.lastrowid

        sarah = conn.execute(
            "INSERT INTO teachers (full_name, email, password_hash) VALUES (?,?,'x')",
            ("Sarah Ahmed", "sarah-dev@souly.local"),
        ).lastrowid
        noha = conn.execute(
            "INSERT INTO teachers (full_name, email, password_hash) VALUES (?,?,'x')",
            ("Noha Khaled", "noha-dev@souly.local"),
        ).lastrowid

        maths = conn.execute(
            "INSERT INTO classes (name, grade) VALUES ('Primary 5 Maths','5')"
        ).lastrowid
        other = conn.execute(
            "INSERT INTO classes (name, grade) VALUES ('Primary 4 Maths','4')"
        ).lastrowid

        for sid in students.values():
            conn.execute("INSERT INTO class_students (class_id, student_id) VALUES (?,?)",
                         (maths, sid))

        conn.execute("INSERT INTO teacher_classes (teacher_id, class_id, role) "
                     "VALUES (?,?,'lead')", (sarah, maths))
        conn.execute("INSERT INTO teacher_classes (teacher_id, class_id, role) "
                     "VALUES (?,?,'lead')", (noha, other))

        conn.execute("INSERT INTO teacher_cards (card_uid, teacher_id) VALUES ('AAAA1111', ?)",
                     (sarah,))
        conn.execute("INSERT INTO teacher_cards (card_uid, teacher_id) VALUES ('BBBB2222', ?)",
                     (noha,))

        conn.execute(
            "INSERT INTO devices (device_key, label, class_id, lcd_cols, lcd_rows) "
            "VALUES ('dev-key-room4','Room 4', ?, 20, 4)", (maths,))
        conn.execute(
            "INSERT INTO devices (device_key, label, class_id, lcd_cols, lcd_rows) "
            "VALUES ('dev-key-room9','Room 9', ?, 20, 4)", (other,))

    return {"students": students, "sarah": sarah, "noha": noha,
            "maths": maths, "other": other}


@pytest.fixture(autouse=True)
def _clean(room):
    """Every test starts with no open session and no flags."""
    yield
    with get_conn() as conn:
        conn.execute("DELETE FROM flags")
        conn.execute("DELETE FROM class_sessions")


def _flag(student_id, flag_type="gaze_away", confidence=0.8, age_s=0):
    with get_conn() as conn:
        cur = conn.execute(
            "INSERT INTO flags (student_id, flag_type, confidence, status, "
            "detected_at, created_at) VALUES (?,?,?, 'pending', ?, ?)",
            (student_id, flag_type, confidence, _iso(-age_s), _iso(-age_s)),
        )
        return cur.lastrowid


# =============================================================================
# Auth
# =============================================================================

def test_no_device_key_is_refused(client, room):
    assert client.get("/api/device/poll").status_code == 401


def test_unknown_device_key_is_refused(client, room):
    res = client.get("/api/device/poll", headers={"X-Souly-Device": "nope"})
    assert res.status_code == 401


def test_hello_returns_the_screen_config(client, room):
    res = client.post("/api/device/hello", headers=DEV, json={"firmware": "v0.1"})
    assert res.status_code == 200
    body = res.json()
    assert body["lcd_cols"] == 20
    assert body["class_name"] == "Primary 5 Maths"
    assert body["in_session"] is False


# =============================================================================
# The tap
# =============================================================================

def test_an_unknown_card_is_refused(client, room):
    res = client.post("/api/device/tap", headers=DEV, json={"card_uid": "DEADBEEF"})
    assert res.json()["action"] == "denied"
    assert res.json()["reason"] == "unknown_card"


def test_a_teacher_cannot_open_a_class_they_do_not_teach(client, room):
    """Noha's card on Sarah's device. Real mistake, and it must not open."""
    res = client.post("/api/device/tap", headers=DEV, json={"card_uid": "BBBB2222"})
    assert res.json()["reason"] == "not_your_class"

    with get_conn() as conn:
        assert conn.execute("SELECT COUNT(*) FROM class_sessions").fetchone()[0] == 0


def test_tapping_in_starts_a_session(client, room):
    res = client.post("/api/device/tap", headers=DEV, json={"card_uid": "AAAA1111"})
    body = res.json()
    assert body["action"] == "session_started"
    assert "Sarah Ahmed" in "".join(body["lines"])
    assert body["led"] == {"pattern": "blink", "count": 2}


def test_the_card_uid_is_matched_case_insensitively(client, room):
    """The firmware sends uppercase, but a hand-seeded row may not be."""
    res = client.post("/api/device/tap", headers=DEV, json={"card_uid": "aaaa1111"})
    assert res.json()["action"] == "session_started"


def test_tapping_out_ends_it_and_reports_the_duration(client, room):
    client.post("/api/device/tap", headers=DEV, json={"card_uid": "AAAA1111"})
    res = client.post("/api/device/tap", headers=DEV, json={"card_uid": "AAAA1111"})
    body = res.json()
    assert body["action"] == "session_ended"
    assert body["duration_s"] >= 0
    assert "Session ended" in body["lines"][0]


def test_a_second_teacher_cannot_take_over_an_open_session(client, room):
    """
    A lead and an assistant in one room is normal. An assistant tapping in
    must not end the lead's lesson and split its flags across two sessions.
    """
    client.post("/api/device/tap", headers=DEV, json={"card_uid": "AAAA1111"})

    with get_conn() as conn:
        conn.execute("INSERT INTO teacher_classes (teacher_id, class_id, role) "
                     "VALUES (?,?,'assistant')", (room["noha"], room["maths"]))

    res = client.post("/api/device/tap", headers=DEV, json={"card_uid": "BBBB2222"})
    assert res.json()["reason"] == "session_belongs_to_other"

    with get_conn() as conn:
        open_count = conn.execute(
            "SELECT COUNT(*) FROM class_sessions WHERE ended_at IS NULL"
        ).fetchone()[0]
        assert open_count == 1
        conn.execute("DELETE FROM teacher_classes WHERE teacher_id=? AND class_id=?",
                     (room["noha"], room["maths"]))


def test_only_one_session_can_be_open_per_class(client, room):
    """Enforced by a partial unique index, not by hoping the code is careful."""
    import sqlite3
    client.post("/api/device/tap", headers=DEV, json={"card_uid": "AAAA1111"})
    with pytest.raises(sqlite3.IntegrityError):
        with get_conn() as conn:
            conn.execute(
                "INSERT INTO class_sessions (class_id, teacher_id) VALUES (?,?)",
                (room["maths"], room["sarah"]),
            )


# =============================================================================
# The idle and session screens
# =============================================================================

def test_the_screen_is_dark_when_nothing_is_happening(client, room):
    """
    The brief was: nothing on the screen until there is something to say. It
    is also what stops a lit LCD glowing on a desk for a whole lesson.
    """
    idle = client.get("/api/device/poll", headers=DEV).json()
    assert idle["state"] == "idle"
    assert idle["backlight"] is False

    client.post("/api/device/tap", headers=DEV, json={"card_uid": "AAAA1111"})
    running = client.get("/api/device/poll", headers=DEV).json()
    assert running["state"] == "session"
    assert running["backlight"] is False


def test_every_line_is_exactly_the_screen_width(client, room):
    """
    An HD44780 does not clear what was there before. A short line leaves the
    tail of the previous one on screen, so the server pads every line and the
    firmware never has to think about it.
    """
    for res in (client.get("/api/device/poll", headers=DEV).json(),
                client.post("/api/device/tap", headers=DEV,
                            json={"card_uid": "AAAA1111"}).json()):
        assert len(res["lines"]) == 4
        assert all(len(line) == 20 for line in res["lines"])


def test_the_session_screen_shows_a_running_clock(client, room):
    client.post("/api/device/tap", headers=DEV, json={"card_uid": "AAAA1111"})
    body = client.get("/api/device/poll", headers=DEV).json()
    assert "00:00:" in "".join(body["lines"])


# =============================================================================
# Flags on the screen
# =============================================================================

def test_a_flag_wakes_the_screen(client, room):
    client.post("/api/device/tap", headers=DEV, json={"card_uid": "AAAA1111"})
    _flag(room["students"]["stu-d1"])

    body = client.get("/api/device/poll", headers=DEV).json()
    assert body["state"] == "flag"
    assert body["backlight"] is True
    assert body["led"] == {"pattern": "blink", "count": 2}
    joined = "".join(body["lines"])
    assert "Beshoy" in joined
    assert "Looked away" in joined


def test_the_flag_names_the_support_profile(client, room):
    """
    A teacher deciding whether to walk over needs to know this is a child
    whose looking away may be self-regulation rather than distraction.
    """
    client.post("/api/device/tap", headers=DEV, json={"card_uid": "AAAA1111"})
    _flag(room["students"]["stu-d1"])
    body = client.get("/api/device/poll", headers=DEV).json()
    assert "ADHD" in "".join(body["lines"])


def test_a_stale_flag_is_never_shown(client, room):
    """
    The drift is over. Sending the teacher to a child who went back to work
    two minutes ago teaches them the device is wrong.
    """
    client.post("/api/device/tap", headers=DEV, json={"card_uid": "AAAA1111"})
    _flag(room["students"]["stu-d1"], age_s=120)
    assert client.get("/api/device/poll", headers=DEV).json()["state"] == "session"


def test_confirming_display_marks_the_flag(client, room):
    client.post("/api/device/tap", headers=DEV, json={"card_uid": "AAAA1111"})
    fid = _flag(room["students"]["stu-d1"])

    body = client.get("/api/device/poll", headers=DEV).json()
    assert body["flag_ids"] == [fid]

    res = client.post("/api/device/shown", headers=DEV, json={"flag_ids": [fid]})
    assert res.json()["marked"] == 1

    with get_conn() as conn:
        assert conn.execute("SELECT shown_on_device_at FROM flags WHERE id=?",
                            (fid,)).fetchone()[0] is not None


def test_a_flag_is_not_marked_shown_until_the_device_says_so(client, room):
    """
    Marking in /poll would let a lost packet silently swallow a flag. The
    device confirms what it actually put on screen.
    """
    client.post("/api/device/tap", headers=DEV, json={"card_uid": "AAAA1111"})
    fid = _flag(room["students"]["stu-d1"])
    client.get("/api/device/poll", headers=DEV)

    with get_conn() as conn:
        assert conn.execute("SELECT shown_on_device_at FROM flags WHERE id=?",
                            (fid,)).fetchone()[0] is None


def test_the_same_child_is_not_reported_twice_in_quick_succession(client, room):
    """
    THE most important test in this file.

    Beshoy has ADHD and an 8-second drift threshold. He may legitimately
    generate a flag every twenty seconds for a whole lesson. Without a
    cooldown the lamp blinks continuously, the teacher stops looking at it,
    and the device becomes decoration. Nothing errors; it just quietly stops
    working, which is why it needs a test.
    """
    client.post("/api/device/tap", headers=DEV, json={"card_uid": "AAAA1111"})
    first = _flag(room["students"]["stu-d1"])
    client.get("/api/device/poll", headers=DEV)
    client.post("/api/device/shown", headers=DEV, json={"flag_ids": [first]})

    _flag(room["students"]["stu-d1"])          # he drifts again, moments later
    assert client.get("/api/device/poll", headers=DEV).json()["state"] == "session"


def test_a_different_child_is_still_reported_during_that_cooldown(client, room):
    """The cooldown is per child, not a global mute."""
    client.post("/api/device/tap", headers=DEV, json={"card_uid": "AAAA1111"})
    first = _flag(room["students"]["stu-d1"])
    client.get("/api/device/poll", headers=DEV)
    client.post("/api/device/shown", headers=DEV, json={"flag_ids": [first]})

    _flag(room["students"]["stu-d2"])
    body = client.get("/api/device/poll", headers=DEV).json()
    assert body["state"] == "flag"
    assert "Lo2lo2" in "".join(body["lines"])


def test_two_children_show_one_at_a_time_with_a_counter(client, room):
    client.post("/api/device/tap", headers=DEV, json={"card_uid": "AAAA1111"})
    _flag(room["students"]["stu-d1"], confidence=0.9)
    _flag(room["students"]["stu-d2"], confidence=0.6)

    body = client.get("/api/device/poll", headers=DEV).json()
    assert body["state"] == "flag"
    assert "1/2" in "".join(body["lines"])
    assert "Beshoy" in "".join(body["lines"])      # higher confidence first


def test_two_of_four_is_still_about_the_children_not_the_room(client, room):
    """
    The floor exists because of exactly this. A bare 40% rule would call two
    children out of four a room-level event, when what the teacher needs is
    their names.
    """
    client.post("/api/device/tap", headers=DEV, json={"card_uid": "AAAA1111"})
    _flag(room["students"]["stu-d1"])
    _flag(room["students"]["stu-d2"])
    assert client.get("/api/device/poll", headers=DEV).json()["state"] == "flag"


def test_half_the_room_drifting_becomes_one_message_about_the_room(client, room):
    """
    Three students drifting at once is not three events, it is one different
    event. One child is about that child; half the class is about the lesson,
    and a list of names is the wrong response.
    """
    client.post("/api/device/tap", headers=DEV, json={"card_uid": "AAAA1111"})
    for ext in ("stu-d1", "stu-d2", "stu-d3", "stu-d4"):
        _flag(room["students"][ext])

    body = client.get("/api/device/poll", headers=DEV).json()
    assert body["state"] == "flag_room"
    joined = "".join(body["lines"])
    assert "CLASS DRIFTING" in joined
    assert "4 of 4" in joined
    # A slow pulse rather than blinks — eight blinks in a row is noise.
    assert body["led"]["pattern"] == "pulse"


def test_flags_are_scoped_to_the_device_class(client, room):
    """A child in another class must never appear on this device."""
    with get_conn() as conn:
        outsider = conn.execute(
            "INSERT INTO students (external_id, full_name, display_name, "
            "created_at, updated_at) VALUES ('stu-out','Outsider','Outsider',?,?)",
            (utc_now_iso(), utc_now_iso()),
        ).lastrowid

    client.post("/api/device/tap", headers=DEV, json={"card_uid": "AAAA1111"})
    _flag(outsider)
    assert client.get("/api/device/poll", headers=DEV).json()["state"] == "session"


def test_no_flags_are_shown_when_no_session_is_running(client, room):
    """No lesson, no audience. The screen stays dark."""
    _flag(room["students"]["stu-d1"])
    body = client.get("/api/device/poll", headers=DEV).json()
    assert body["state"] == "idle"
    assert body["backlight"] is False


def test_showing_a_flag_counts_it_against_the_session(client, room):
    tap = client.post("/api/device/tap", headers=DEV, json={"card_uid": "AAAA1111"}).json()
    fid = _flag(room["students"]["stu-d1"])
    client.get("/api/device/poll", headers=DEV)
    client.post("/api/device/shown", headers=DEV, json={"flag_ids": [fid]})

    end = client.post("/api/device/tap", headers=DEV, json={"card_uid": "AAAA1111"}).json()
    assert "1 flag" in "".join(end["lines"])

    with get_conn() as conn:
        row = conn.execute("SELECT class_session_id, class_id FROM flags WHERE id=?",
                           (fid,)).fetchone()
        assert row["class_session_id"] == tap["session_id"]
        assert row["class_id"] == room["maths"]
