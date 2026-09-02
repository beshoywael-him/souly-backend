"""
The classroom device — RFID reader, 20x4 screen, lamp.

    POST /api/device/hello    who am I, what class am I on, is a session open
    POST /api/device/tap      a card was presented
    GET  /api/device/poll     what should I be showing right now

-----------------------------------------------------------------------------
THE SCREEN IS RENDERED HERE, NOT ON THE ESP32
-----------------------------------------------------------------------------
Every endpoint returns `lines` — a list of strings already padded and truncated
to the device's column count — plus what the lamp should do. The firmware's
whole job becomes: POST, print four strings, blink N times. About 150 lines
that cannot really be wrong.

Everything that CAN be wrong lives here instead: session timing, which flag to
show, how to abbreviate a name into 20 characters, when to suppress a repeat.
All of it in Python, all of it testable, all of it changeable without a
soldering iron and a reflash. Tuning "90 seconds" to "60 seconds" during
rehearsal should be an edit and a restart, not a firmware cycle.

-----------------------------------------------------------------------------
THE DEVICE IS BOUND TO A CLASS
-----------------------------------------------------------------------------
It has no buttons, so when a teacher taps it cannot ask which of their four
classes they mean. The device carries a class_id; the tap checks that this
teacher teaches THIS class. Sarah tapping the Primary 4 unit gets "Not your
class", which is both correct and short enough to fit.
"""

import sqlite3
from datetime import datetime, timedelta, timezone

from fastapi import APIRouter, Depends, Header, HTTPException
from pydantic import BaseModel, Field

from app.db import db_dependency
from app.models import utc_now_iso

router = APIRouter(prefix="/api/device", tags=["device"])


# =============================================================================
# Display policy — the numbers worth tuning at rehearsal
# =============================================================================

# A flag older than this is never displayed. The drift is over; showing it now
# would send the teacher to a child who has gone back to work, and teach them
# the device is wrong.
FLAG_TTL_S = 15

# After showing a flag about a child, say nothing more about that child for
# this long. Beshoy has ADHD and a drift threshold of 8s — without this the
# lamp would blink every twenty seconds all lesson and the teacher would stop
# looking at it. Alarm fatigue is the most likely way this device fails in
# real use.
STUDENT_COOLDOWN_S = 90

# Distinct students flagged inside this window decide what kind of message
# this is at all.
DENSITY_WINDOW_S = 60

# Four children at once is about the lesson rather than about any one child —
# but never fewer than three, whatever the proportion says. In a class of four
# a 40% rule would trip at two, and two out of four is not "the room has gone",
# it is two children who both deserve to be named.
ROOM_LEVEL_MIN = 3
ROOM_LEVEL_CAP = 4

# How long the device holds a flag on screen, and how often it asks again.
FLAG_HOLD_MS = 3000
POLL_MS = 1000

# A tap confirmation stays up this long before the screen returns to the clock.
TAP_HOLD_MS = 2000

# Is the screen lit when nothing is happening?
#
# The original brief said no: "until that, nothing is shown on the screen". In a
# real classroom that is right — a screen glowing on a teacher's desk all lesson
# is the kind of thing that gets a device unplugged.
#
# At a competition stand it is wrong. A dark screen reads as a broken device to
# a judge walking past, and nobody taps a card to find out. Lit at the stand,
# dark in a classroom; one line, no reflash.
IDLE_BACKLIGHT = True


# What each CV flag type reads as on a 20-column screen. Plain language: the
# teacher is reading this from across a room, mid-lesson.
FLAG_COPY = {
    "gaze_away":            "Looked away",
    "head_turn":            "Turned away",
    "absent":               "Not at desk",
    "prolonged_inactivity": "Stopped working",
    "distress":             "Seems distressed",
    "repeated_error":       "Stuck on this",
    "help_requested":       "Asked for help",
}


# =============================================================================
# Helpers
# =============================================================================

# An HD44780 has a fixed character ROM: ASCII plus some Japanese. Anything
# else renders as a random glyph, so curly quotes and dashes that arrive from
# a database full of typographically correct text get flattened here rather
# than surprising us on a screen we cannot see from the laptop.
_ASCII = {
    "\u2014": "-", "\u2013": "-", "\u2018": "'", "\u2019": "'",
    "\u201c": '"', "\u201d": '"', "\u2026": "...", "\u00a0": " ",
}


def _ascii(text: str) -> str:
    for bad, good in _ASCII.items():
        text = text.replace(bad, good)
    return "".join(ch if 32 <= ord(ch) < 127 else "?" for ch in text)


def _pad(text: str, cols: int) -> str:
    """
    One line, exactly `cols` characters, in characters the screen can draw.

    Padding matters: an HD44780 does not clear what was there before, so a
    short line leaves the tail of the previous one behind. Doing it here means
    the firmware never has to think about it.
    """
    text = _ascii(text or "")[:cols]
    return text + " " * (cols - len(text))


def _screen(device: sqlite3.Row, *lines: str) -> list[str]:
    cols = device["lcd_cols"]
    rows = device["lcd_rows"]
    out = [_pad(l, cols) for l in lines[:rows]]
    while len(out) < rows:
        out.append(" " * cols)
    return out


def _centre(text: str, cols: int) -> str:
    text = text[:cols]
    pad = (cols - len(text)) // 2
    return " " * pad + text


def _hhmmss(seconds: int) -> str:
    seconds = max(0, int(seconds))
    return f"{seconds // 3600:02d}:{(seconds % 3600) // 60:02d}:{seconds % 60:02d}"


def _parse(ts: str | None) -> datetime | None:
    if not ts:
        return None
    try:
        return datetime.strptime(ts, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=timezone.utc)
    except ValueError:
        return None


def _elapsed_s(started_at: str) -> int:
    start = _parse(started_at)
    if start is None:
        return 0
    return int((datetime.now(timezone.utc) - start).total_seconds())


def _short_name(full: str, cols: int) -> str:
    """
    "Sarah Ahmed" fits in 20. Longer names get the surname initialised rather
    than chopped mid-word — "Abdelrahman Mostafa" reads far better as
    "Abdelrahman M." than as "Abdelrahman Mostaf".
    """
    full = (full or "").strip()
    if len(full) <= cols:
        return full
    parts = full.split()
    if len(parts) >= 2:
        short = f"{parts[0]} {parts[1][0]}."
        if len(short) <= cols:
            return short
    return full[:cols]


def _no_led() -> dict:
    return {"pattern": "none", "count": 0}


# =============================================================================
# Auth
# =============================================================================

def get_device(
    x_souly_device: str | None = Header(None),
    conn: sqlite3.Connection = Depends(db_dependency),
) -> sqlite3.Row:
    """
    Resolve the device by its key, or 401.

    Unlike a card UID, this key IS a secret — it lives only in the device's
    flash and never travels anywhere a phone could read it. It is what stops
    anything else on the MiFi opening a lesson.
    """
    if not x_souly_device:
        raise HTTPException(status_code=401, detail="No device key")

    row = conn.execute(
        "SELECT * FROM devices WHERE device_key = ? AND is_active = 1",
        (x_souly_device,),
    ).fetchone()
    if row is None:
        raise HTTPException(status_code=401, detail="Unknown device")

    conn.execute("UPDATE devices SET last_seen_at = ? WHERE id = ?",
                 (utc_now_iso(), row["id"]))
    return row


def _open_session(conn, class_id: int) -> sqlite3.Row | None:
    return conn.execute(
        "SELECT * FROM v_open_sessions WHERE class_id = ?", (class_id,)
    ).fetchone()


def _class_of(conn, device: sqlite3.Row) -> sqlite3.Row | None:
    if device["class_id"] is None:
        return None
    return conn.execute(
        "SELECT *, COALESCE(NULLIF(short_name,''), name) AS screen_name "
        "FROM classes WHERE id = ? AND is_active = 1", (device["class_id"],)
    ).fetchone()


# =============================================================================
# hello
# =============================================================================

class Hello(BaseModel):
    firmware: str | None = Field(None, max_length=32)


@router.post("/hello", summary="Device boot — config and current state")
def hello(
    payload: Hello,
    device: sqlite3.Row = Depends(get_device),
    conn: sqlite3.Connection = Depends(db_dependency),
) -> dict:
    if payload.firmware:
        conn.execute("UPDATE devices SET firmware = ? WHERE id = ?",
                     (payload.firmware, device["id"]))

    klass = _class_of(conn, device)
    session = _open_session(conn, device["class_id"]) if klass else None

    return {
        "device": device["label"],
        "class_name": klass["name"] if klass else None,
        "lcd_cols": device["lcd_cols"],
        "lcd_rows": device["lcd_rows"],
        "poll_ms": POLL_MS,
        "in_session": session is not None,
        **_render_state(conn, device, klass, session),
    }


# =============================================================================
# tap
# =============================================================================

class Tap(BaseModel):
    card_uid: str = Field(..., min_length=4, max_length=32)


@router.post("/tap", summary="A card was presented")
def tap(
    payload: Tap,
    device: sqlite3.Row = Depends(get_device),
    conn: sqlite3.Connection = Depends(db_dependency),
) -> dict:
    """
    One endpoint for both directions. The device does not decide whether this
    is a sign-in or a sign-out — it reports that a card touched the reader and
    the server, which knows whether a session is open, decides what that means.

    A device that had to track its own state would disagree with the database
    the first time it was power-cycled mid-lesson.
    """
    cols = device["lcd_cols"]
    uid = payload.card_uid.strip().upper()

    klass = _class_of(conn, device)
    if klass is None:
        return {
            "action": "denied",
            "reason": "no_class",
            "lines": _screen(device, "Device not set up", "", "No class assigned",
                             "Ask IT"),
            "led": {"pattern": "blink", "count": 3},
            "backlight": True,
            "hold_ms": 4000,
        }

    card = conn.execute(
        "SELECT c.*, t.full_name, t.id AS tid, t.is_active AS t_active "
        "FROM teacher_cards c JOIN teachers t ON t.id = c.teacher_id "
        "WHERE c.card_uid = ? AND c.is_active = 1",
        (uid,),
    ).fetchone()

    if card is None or not card["t_active"]:
        # Deliberately does not say whether the card is unknown or deactivated.
        return {
            "action": "denied",
            "reason": "unknown_card",
            "lines": _screen(device, "Card not recognised", "", "Ask the office",
                             ""),
            "led": {"pattern": "blink", "count": 3},
            "backlight": True,
            "hold_ms": 4000,
        }

    teaches = conn.execute(
        "SELECT 1 FROM teacher_classes WHERE teacher_id = ? AND class_id = ?",
        (card["tid"], klass["id"]),
    ).fetchone()

    if teaches is None:
        return {
            "action": "denied",
            "reason": "not_your_class",
            "lines": _screen(device,
                             _short_name(card["full_name"], cols),
                             "is not assigned to",
                             klass["screen_name"],
                             ""),
            "led": {"pattern": "blink", "count": 3},
            "backlight": True,
            "hold_ms": 4000,
        }

    conn.execute("UPDATE teacher_cards SET last_used_at = ? WHERE card_uid = ?",
                 (utc_now_iso(), uid))

    session = _open_session(conn, klass["id"])

    # --- no session open: start one -----------------------------------------
    if session is None:
        cur = conn.execute(
            "INSERT INTO class_sessions (class_id, teacher_id, device_id, started_at) "
            "VALUES (?,?,?,?)",
            (klass["id"], card["tid"], device["id"], utc_now_iso()),
        )
        return {
            "action": "session_started",
            "session_id": cur.lastrowid,
            "lines": _screen(device,
                             "Welcome,",
                             _short_name(card["full_name"], cols),
                             klass["screen_name"],
                             "Session started"),
            "led": {"pattern": "blink", "count": 2},
            "backlight": True,
            "hold_ms": TAP_HOLD_MS,
        }

    # --- a session is open, and it is theirs: end it -------------------------
    if session["teacher_id"] == card["tid"]:
        elapsed = _elapsed_s(session["started_at"])
        conn.execute(
            "UPDATE class_sessions SET ended_at = ?, ended_by = 'card' WHERE id = ?",
            (utc_now_iso(), session["session_id"]),
        )
        flags = conn.execute(
            "SELECT COUNT(*) FROM flags WHERE class_session_id = ?",
            (session["session_id"],),
        ).fetchone()[0]

        return {
            "action": "session_ended",
            "session_id": session["session_id"],
            "duration_s": elapsed,
            "lines": _screen(device,
                             "Session ended",
                             _short_name(card["full_name"], cols),
                             _centre(_hhmmss(elapsed), cols),
                             f"{flags} flag{'' if flags == 1 else 's'} this lesson"),
            "led": {"pattern": "blink", "count": 2},
            "backlight": True,
            "hold_ms": 5000,
        }

    # --- a session is open and it belongs to someone else --------------------
    # Do NOT silently take it over. Two teachers in a room is a real situation
    # (a lead and an assistant), and a co-teacher tapping in should not end
    # the lead's lesson and split its flags across two sessions.
    return {
        "action": "denied",
        "reason": "session_belongs_to_other",
        "lines": _screen(device,
                         "Lesson already open",
                         _short_name(session["teacher_name"], cols),
                         "started it",
                         "They must end it"),
        "led": {"pattern": "blink", "count": 3},
        "backlight": True,
        "hold_ms": 5000,
    }


# =============================================================================
# poll
# =============================================================================

@router.get("/poll", summary="What should the device be showing right now")
def poll(
    device: sqlite3.Row = Depends(get_device),
    conn: sqlite3.Connection = Depends(db_dependency),
) -> dict:
    klass = _class_of(conn, device)
    session = _open_session(conn, device["class_id"]) if klass else None
    return {
        "poll_ms": POLL_MS,
        **_render_state(conn, device, klass, session),
    }


def _render_state(conn, device, klass, session) -> dict:
    """
    The whole display decision, in one place.

    Backlight is OFF unless there is something to say. That is not a power
    saving trick — it is the brief: "until that, nothing is shown on the
    screen". A screen glowing on a teacher's desk for a whole lesson is the
    kind of thing that gets a device unplugged.
    """
    cols = device["lcd_cols"]

    if klass is None:
        return {
            "state": "unconfigured",
            "lines": _screen(device, "Souly", "No class assigned", "", ""),
            "led": _no_led(), "backlight": IDLE_BACKLIGHT, "hold_ms": 0,
        }

    if session is None:
        return {
            "state": "idle",
            "lines": _screen(device, "Souly  Classroom", klass["screen_name"], "",
                             "Tap card to start"),
            "led": _no_led(), "backlight": IDLE_BACKLIGHT, "hold_ms": 0,
        }

    # --- in a session: is there anything to say? ----------------------------
    display = _pick_flags(conn, klass["id"], session["session_id"])

    if display["kind"] == "none":
        return {
            "state": "session",
            "lines": _screen(device,
                             _short_name(session["teacher_name"], cols),
                             klass["screen_name"],
                             _centre(_hhmmss(_elapsed_s(session["started_at"])), cols),
                             "Tap card to end"),
            "led": _no_led(), "backlight": IDLE_BACKLIGHT, "hold_ms": 0,
        }

    if display["kind"] == "room":
        n = display["count"]
        total = display["class_size"]
        return {
            "state": "flag_room",
            "lines": _screen(device,
                             "CLASS DRIFTING",
                             f"{n} of {total} students",
                             "in the last minute",
                             "Consider a break"),
            # A slow pulse, not blinks. Eight blinks in a row is noise; a
            # different rhythm says "something else is happening" without the
            # teacher having to read anything.
            "led": {"pattern": "pulse", "count": 1},
            "backlight": True,
            "hold_ms": FLAG_HOLD_MS + 1000,
            "flag_ids": display["flag_ids"],
        }

    f = display["flag"]
    position = ""
    if display["total"] > 1:
        position = f"{display['index']}/{display['total']}"

    return {
        "state": "flag",
        "lines": _screen(device,
                         _short_name(f["display_name"], cols - 5).ljust(cols - 5)
                         + (f"{int((f['confidence'] or 0) * 100):>4}%" if f["confidence"] else ""),
                         FLAG_COPY.get(f["flag_type"], f["flag_type"].replace("_", " ")),
                         f["support_line"],
                         f"#{f['id']}".ljust(cols - len(position)) + position),
        "led": {"pattern": "blink", "count": 2},
        "backlight": True,
        "hold_ms": FLAG_HOLD_MS,
        "flag_ids": [f["id"]],
    }


def _pick_flags(conn, class_id: int, session_id: int) -> dict:
    """
    Which flags, if any, the device should show — and whether this is about a
    child or about the room.

    Three students drifting at once is not three events, it is one different
    event. One child losing focus is about that child and the teacher walks
    over. Half the class losing focus is about the lesson, and handing the
    teacher a list of names is the wrong response.
    """
    now = datetime.now(timezone.utc)
    ttl_cutoff = (now - timedelta(seconds=FLAG_TTL_S)).strftime("%Y-%m-%dT%H:%M:%SZ")
    cooldown_cutoff = (now - timedelta(seconds=STUDENT_COOLDOWN_S)) \
        .strftime("%Y-%m-%dT%H:%M:%SZ")
    window_cutoff = (now - timedelta(seconds=DENSITY_WINDOW_S)) \
        .strftime("%Y-%m-%dT%H:%M:%SZ")

    class_size = conn.execute(
        "SELECT COUNT(*) FROM class_students WHERE class_id = ?", (class_id,)
    ).fetchone()[0] or 1

    rows = conn.execute(
        """
        SELECT f.id, f.flag_type, f.confidence, f.student_id,
               s.display_name, s.support_profile, s.drift_threshold_ms
        FROM flags f
        JOIN students s ON s.id = f.student_id
        JOIN class_students cs ON cs.student_id = f.student_id AND cs.class_id = ?
        WHERE f.status = 'pending'
          AND f.shown_on_device_at IS NULL
          AND f.created_at >= ?
          AND NOT EXISTS (
                SELECT 1 FROM flags prev
                 WHERE prev.student_id = f.student_id
                   AND prev.shown_on_device_at IS NOT NULL
                   AND prev.shown_on_device_at >= ?
          )
        ORDER BY f.confidence DESC, f.created_at ASC
        """,
        (class_id, ttl_cutoff, cooldown_cutoff),
    ).fetchall()

    if not rows:
        return {"kind": "none"}

    # Distinct students flagged in the wider window — including ones already
    # shown, because the question "how much of the room has gone?" is about
    # the room, not about what we happen to have displayed.
    distinct_recent = conn.execute(
        """
        SELECT COUNT(DISTINCT f.student_id)
        FROM flags f
        JOIN class_students cs ON cs.student_id = f.student_id AND cs.class_id = ?
        WHERE f.created_at >= ?
        """,
        (class_id, window_cutoff),
    ).fetchone()[0]

    # Effectively: 3 in a very small class, 4 in anything bigger.
    room_threshold = max(ROOM_LEVEL_MIN,
                         min(ROOM_LEVEL_CAP, round(class_size * 0.4)))
    if distinct_recent >= room_threshold:
        return {
            "kind": "room",
            "count": distinct_recent,
            "class_size": class_size,
            "flag_ids": [r["id"] for r in rows],
        }

    # One student per screen, highest confidence first.
    seen: set[int] = set()
    unique = []
    for r in rows:
        if r["student_id"] in seen:
            continue
        seen.add(r["student_id"])
        unique.append(r)

    top = unique[0]
    support = ""
    if top["support_profile"] and top["support_profile"] != "none":
        # Naming the profile on the flag is not decoration. A teacher deciding
        # whether to walk over needs to know this is a child whose looking
        # away may be self-regulation rather than distraction.
        support = {
            "adhd": "ADHD",
            "autism": "Autistic",
            "dyslexia": "Dyslexia",
            "visual_impairment": "Visual impairment",
            "hearing_impairment": "Hearing impairment",
        }.get(top["support_profile"], top["support_profile"])

    return {
        "kind": "one",
        "flag": {
            "id": top["id"],
            "flag_type": top["flag_type"],
            "confidence": top["confidence"],
            "display_name": top["display_name"],
            "support_line": support,
        },
        "index": 1,
        "total": len(unique),
    }


class Shown(BaseModel):
    flag_ids: list[int] = Field(default_factory=list)


@router.post("/shown", summary="Device confirms it displayed these flags")
def shown(
    payload: Shown,
    device: sqlite3.Row = Depends(get_device),
    conn: sqlite3.Connection = Depends(db_dependency),
) -> dict:
    """
    The device tells us what it actually put on screen.

    Marking them here rather than in /poll means a flag dropped by a lost
    packet gets shown on the next poll instead of being silently swallowed —
    and it makes "was the teacher told?" a fact rather than an assumption.
    """
    if not payload.flag_ids:
        return {"marked": 0}

    session = _open_session(conn, device["class_id"])
    now = utc_now_iso()
    marked = 0

    for fid in payload.flag_ids[:20]:
        cur = conn.execute(
            "UPDATE flags SET shown_on_device_at = ?, class_session_id = ?, "
            "class_id = ? WHERE id = ? AND shown_on_device_at IS NULL",
            (now, session["session_id"] if session else None,
             device["class_id"], fid),
        )
        marked += cur.rowcount

    if session and marked:
        conn.execute(
            "UPDATE class_sessions SET flag_count = flag_count + ? WHERE id = ?",
            (marked, session["session_id"]),
        )

    return {"marked": marked}
