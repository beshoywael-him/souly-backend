-- =============================================================================
-- schema_v9 — classes, the classroom device, and the session clock
--
-- Renumbered from v8: the teacher-dashboard work landed as schema_v8.sql at
-- the same time. Order matters here — v8 creates teacher_tokens and adds
-- flags.topic_id; this file assumes both exist. app/db.py globs schema*.sql
-- and sorts by (len(stem), stem), so v8 runs before v9.
--
-- Three things arrive together because none of them is useful alone:
--
--   1. CLASSES.        There was no such concept anywhere. A teacher had no
--                      students, a student had no group, and a flag had no
--                      room it happened in.
--   2. THE DEVICE.     The ESP32 by the door: an RFID reader, a 20x4 screen
--                      and a lamp.
--   3. CLASS SESSIONS. The stopwatch. A teacher taps in, the clock runs, they
--                      tap out.
--
-- WHY A STUDENT CAN BE IN MANY CLASSES
-- ------------------------------------
-- Beshoy sits in Primary 5 Mathematics and Primary 5 Science. Same child, two
-- classes, possibly two teachers. That is why class_students is its own table
-- and not a class_id column on students — the column would have forced a
-- choice that does not exist in a real school.
--
-- Symmetrically, teacher_classes is its own table because Sarah teaches four
-- classes and one of them she assists rather than leads.
--
-- WHY THE DEVICE IS BOUND TO A CLASS
-- ----------------------------------
-- The device has no buttons. When Sarah taps her card it cannot ask which of
-- her four classes she means. So the device carries a class_id and the tap
-- validates that this teacher teaches THIS class. That also produces a good
-- failure line for a 20-character screen: "Not your class".
--
-- WHY card_uid IS STORED IN THE CLEAR
-- -----------------------------------
-- Deliberately, and it should be written up this way rather than quietly.
-- A MIFARE UID is broadcast unencrypted to any reader in range and clones for
-- the price of a coffee. Hashing it would look like security while providing
-- none, and would stop us doing the one lookup we need. This is
-- IDENTIFICATION -- it says which teacher is at the door -- not authentication.
-- The teacher's real credential is the password on their account.
--
-- Re-runnable like every schema file here. app/db.py hoists ADD COLUMN to the
-- top of the file and swallows "duplicate column name".
-- =============================================================================


-- -----------------------------------------------------------------------------
-- flags — where and when did this happen
-- -----------------------------------------------------------------------------
-- `session_id` already exists and points at `sessions`, which is a student's
-- one-to-one tutoring session with Souly. A classroom lesson is a different
-- thing entirely, so it gets its own column rather than overloading that one.
--
-- `shown_on_device_at` answers "was the teacher actually told?". The device
-- suppresses repeats and drops stale flags, so a flag being raised and a flag
-- being seen are genuinely different events. Without this column we could not
-- tell them apart, and "what happened to the ones you suppressed?" is the
-- first question anyone asks about the display rules.
ALTER TABLE flags ADD COLUMN class_session_id  INTEGER REFERENCES class_sessions(id) ON DELETE SET NULL;
ALTER TABLE flags ADD COLUMN class_id          INTEGER REFERENCES classes(id) ON DELETE SET NULL;
ALTER TABLE flags ADD COLUMN shown_on_device_at TEXT;


-- =============================================================================
-- classes
-- =============================================================================
CREATE TABLE IF NOT EXISTS classes (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    name            TEXT    NOT NULL,          -- "Primary 5 — Mathematics"

    -- A name that fits a 20-column screen. The full name truncates to
    -- "Primary 5 — Mathemat" mid-word, and the em dash is not in the HD44780
    -- character ROM at all — it would draw as a random glyph. The device uses
    -- short_name when set and falls back to name when it is not.
    short_name      TEXT,

    grade           TEXT    NOT NULL,
    subject_id      INTEGER REFERENCES subjects(id),
    academic_year   TEXT    NOT NULL DEFAULT '2026/2027',
    is_active       INTEGER NOT NULL DEFAULT 1 CHECK (is_active IN (0,1)),
    created_at      TEXT    NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ','now'))
);

CREATE TABLE IF NOT EXISTS class_students (
    class_id        INTEGER NOT NULL REFERENCES classes(id)  ON DELETE CASCADE,
    student_id      INTEGER NOT NULL REFERENCES students(id) ON DELETE CASCADE,
    joined_at       TEXT    NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ','now')),
    PRIMARY KEY (class_id, student_id)
);

CREATE TABLE IF NOT EXISTS teacher_classes (
    teacher_id      INTEGER NOT NULL REFERENCES teachers(id) ON DELETE CASCADE,
    class_id        INTEGER NOT NULL REFERENCES classes(id)  ON DELETE CASCADE,
    role            TEXT    NOT NULL DEFAULT 'lead'
                        CHECK (role IN ('lead','assistant')),
    PRIMARY KEY (teacher_id, class_id)
);

CREATE INDEX IF NOT EXISTS idx_class_students_student ON class_students(student_id);
CREATE INDEX IF NOT EXISTS idx_teacher_classes_class  ON teacher_classes(class_id);


-- =============================================================================
-- teacher_cards
-- =============================================================================
CREATE TABLE IF NOT EXISTS teacher_cards (
    card_uid        TEXT    PRIMARY KEY,       -- uppercase hex, no separators
    teacher_id      INTEGER NOT NULL REFERENCES teachers(id) ON DELETE CASCADE,
    label           TEXT,                      -- "lanyard", "spare fob"
    is_active       INTEGER NOT NULL DEFAULT 1 CHECK (is_active IN (0,1)),
    issued_at       TEXT    NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ','now')),
    last_used_at    TEXT
);

CREATE INDEX IF NOT EXISTS idx_teacher_cards_teacher ON teacher_cards(teacher_id);


-- =============================================================================
-- devices
-- =============================================================================
-- api_key is a real secret, unlike the card UID: it never leaves the device's
-- flash and it is what stops anything else on the MiFi from opening sessions.
-- One key per device so a compromised unit can be revoked alone.
--
-- lcd_cols is here rather than hardcoded because the screen turned out to be
-- 20 columns, not 16, and the server renders the lines. A second unit with a
-- different display should not need a code change.
CREATE TABLE IF NOT EXISTS devices (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    device_key      TEXT    NOT NULL UNIQUE,   -- what the firmware sends
    label           TEXT    NOT NULL,          -- "Room 4 door unit"
    class_id        INTEGER REFERENCES classes(id) ON DELETE SET NULL,
    lcd_cols        INTEGER NOT NULL DEFAULT 20,
    lcd_rows        INTEGER NOT NULL DEFAULT 4,
    is_active       INTEGER NOT NULL DEFAULT 1 CHECK (is_active IN (0,1)),
    last_seen_at    TEXT,
    firmware        TEXT,
    created_at      TEXT    NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ','now'))
);


-- =============================================================================
-- class_sessions — the stopwatch
-- =============================================================================
-- `ended_at IS NULL` means running. A partial unique index enforces one open
-- session per class, so a second tap on another device cannot silently open a
-- parallel lesson that splits the flags between two sessions.
CREATE TABLE IF NOT EXISTS class_sessions (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    class_id        INTEGER NOT NULL REFERENCES classes(id)  ON DELETE CASCADE,
    teacher_id      INTEGER NOT NULL REFERENCES teachers(id) ON DELETE CASCADE,
    device_id       INTEGER REFERENCES devices(id) ON DELETE SET NULL,

    started_at      TEXT    NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ','now')),
    ended_at        TEXT,
    -- 'card'  — a teacher tapped out, the normal case
    -- 'auto'  — the server closed a session left running overnight
    -- 'manual'— closed from the teacher web view
    ended_by        TEXT CHECK (ended_by IS NULL OR ended_by IN ('card','auto','manual')),

    flag_count      INTEGER NOT NULL DEFAULT 0
);

CREATE UNIQUE INDEX IF NOT EXISTS idx_one_open_session_per_class
    ON class_sessions(class_id) WHERE ended_at IS NULL;

CREATE INDEX IF NOT EXISTS idx_sessions_class ON class_sessions(class_id, started_at DESC);


-- =============================================================================
-- teacher_tokens — created in schema_v8.sql, not here
-- =============================================================================
-- Both halves of the team reached for a third token realm in the same week,
-- which is a good sign the reasoning holds. schema_v8 creates the table; this
-- file deliberately does not, so there is exactly one place that owns it.


-- =============================================================================
-- v_teacher_classes
-- The join every teacher-facing query passes through, written once — the same
-- discipline as v_parent_children.
-- =============================================================================
DROP VIEW IF EXISTS v_teacher_classes;
CREATE VIEW v_teacher_classes AS
SELECT
    tc.teacher_id,
    tc.role,
    c.id                AS class_id,
    c.name              AS class_name,
    c.grade,
    c.academic_year,
    c.subject_id,
    s.code              AS subject_code,
    s.name              AS subject_name,
    s.icon              AS subject_icon,
    s.color_from,
    (SELECT COUNT(*) FROM class_students cs WHERE cs.class_id = c.id) AS student_count
FROM teacher_classes tc
JOIN classes c   ON c.id = tc.class_id
LEFT JOIN subjects s ON s.id = c.subject_id
WHERE c.is_active = 1;


-- =============================================================================
-- v_open_sessions
-- =============================================================================
DROP VIEW IF EXISTS v_open_sessions;
CREATE VIEW v_open_sessions AS
SELECT
    cs.id           AS session_id,
    cs.class_id,
    cs.teacher_id,
    cs.device_id,
    cs.started_at,
    cs.flag_count,
    c.name          AS class_name,
    t.full_name     AS teacher_name
FROM class_sessions cs
JOIN classes  c ON c.id = cs.class_id
JOIN teachers t ON t.id = cs.teacher_id
WHERE cs.ended_at IS NULL;
