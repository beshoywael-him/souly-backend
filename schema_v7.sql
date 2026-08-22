-- =============================================================================
-- schema_v7 — the parents' hub
--
-- Everything the parent portal needs that did not already exist. Three ideas:
--
--   1. Parents get their OWN token table, not a column on auth_tokens.
--   2. Teacher notes are a first-class record, not a chat message.
--   3. Parent<->teacher conversation is scoped to ONE CHILD.
--
-- WHY A SEPARATE parent_tokens TABLE
-- ----------------------------------
-- The obvious move is to add an `audience` column to auth_tokens and let one
-- table serve students, parents and teachers. It is also how you end up with a
-- student token that accidentally satisfies a parent check because somebody
-- forgot a WHERE clause. Two tables cannot be confused for each other: the
-- parent lookup physically cannot return a student, no matter what the query
-- says. The student sign-in path is not touched by this file at all.
--
-- WHY CONVERSATIONS ARE PER-CHILD
-- -------------------------------
-- Fayrouz has two sons. She and Ms. Sarah may need to discuss Beshoy's
-- attention and Atef's reading in the same week. One thread per (parent,
-- teacher) pair would mix them, and a parent reading back through a mixed
-- thread cannot tell which child a message was about. The child is part of
-- the conversation's identity, so `student_id` is in the unique key.
--
-- Re-runnable like every schema file here. app/db.py hoists ADD COLUMN to the
-- top and swallows "duplicate column name".
-- =============================================================================


-- -----------------------------------------------------------------------------
-- parents — lockout, and the human details the hub displays
-- -----------------------------------------------------------------------------
-- Same five-tries-then-pause rule as the student picker. A parent access code
-- is 31^12 combinations so this is not what makes it safe; it is here so that
-- someone typing a code into a phone on a noisy competition floor gets a
-- readable pause instead of an unbounded guessing surface.
ALTER TABLE parents ADD COLUMN failed_logins   INTEGER NOT NULL DEFAULT 0;
ALTER TABLE parents ADD COLUMN locked_until    TEXT;
ALTER TABLE parents ADD COLUMN avatar_color    TEXT NOT NULL DEFAULT '#7C3AED';
ALTER TABLE parents ADD COLUMN preferred_language TEXT NOT NULL DEFAULT 'en';

-- -----------------------------------------------------------------------------
-- teachers — who they are to a parent
-- -----------------------------------------------------------------------------
-- `title` is what the hub prints under the name ("Homeroom Teacher"). It is a
-- label, not a role: nothing branches on it. Authorisation, when the teacher
-- interface lands, will come from the teachers table itself.
ALTER TABLE teachers ADD COLUMN title       TEXT;
ALTER TABLE teachers ADD COLUMN subject_id  INTEGER REFERENCES subjects(id);
ALTER TABLE teachers ADD COLUMN initials    TEXT;
ALTER TABLE teachers ADD COLUMN avatar_color TEXT NOT NULL DEFAULT '#7C3AED';
ALTER TABLE teachers ADD COLUMN is_homeroom INTEGER NOT NULL DEFAULT 0;


-- =============================================================================
-- parent_tokens
-- =============================================================================
CREATE TABLE IF NOT EXISTS parent_tokens (
    token           TEXT    PRIMARY KEY,
    parent_id       INTEGER NOT NULL REFERENCES parents(id) ON DELETE CASCADE,
    created_at      TEXT    NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ','now')),
    expires_at      TEXT    NOT NULL,
    device_label    TEXT
);

CREATE INDEX IF NOT EXISTS idx_parent_tokens_parent ON parent_tokens(parent_id);
CREATE INDEX IF NOT EXISTS idx_parent_tokens_expiry ON parent_tokens(expires_at);


-- =============================================================================
-- teacher_notes
-- The "comments made by the teachers" the hub is built to surface.
-- =============================================================================
-- Deliberately NOT a message. A note is addressed to the record, not to a
-- person: it stays true after the conversation about it has ended, it belongs
-- to a subject, and a parent should be able to read a term's worth in one
-- scroll. Messages are for back-and-forth; notes are for what the school
-- wants on file. Conflating them means a parent has to reconstruct their
-- child's term by scrolling a chat log.
CREATE TABLE IF NOT EXISTS teacher_notes (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    student_id      INTEGER NOT NULL REFERENCES students(id) ON DELETE CASCADE,
    teacher_id      INTEGER NOT NULL REFERENCES teachers(id) ON DELETE CASCADE,
    subject_id      INTEGER REFERENCES subjects(id),

    -- 'praise'   — something went well, no action wanted
    -- 'progress' — a neutral observation about how the work is going
    -- 'concern'  — the school would like the parent to do something
    tone            TEXT    NOT NULL DEFAULT 'progress'
                        CHECK (tone IN ('praise','progress','concern')),
    body            TEXT    NOT NULL,

    -- Set the first time the parent opens it. Drives the unread badge, and
    -- lets a teacher see whether a concern was actually read.
    read_at         TEXT,
    created_at      TEXT    NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ','now'))
);

CREATE INDEX IF NOT EXISTS idx_notes_student ON teacher_notes(student_id, created_at DESC);
CREATE INDEX IF NOT EXISTS idx_notes_unread  ON teacher_notes(student_id, read_at);


-- =============================================================================
-- conversations  +  conversation_messages
-- =============================================================================
CREATE TABLE IF NOT EXISTS conversations (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    parent_id       INTEGER NOT NULL REFERENCES parents(id)  ON DELETE CASCADE,
    teacher_id      INTEGER NOT NULL REFERENCES teachers(id) ON DELETE CASCADE,
    student_id      INTEGER NOT NULL REFERENCES students(id) ON DELETE CASCADE,
    created_at      TEXT    NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ','now')),
    last_message_at TEXT,
    UNIQUE (parent_id, teacher_id, student_id)
);

CREATE INDEX IF NOT EXISTS idx_conv_parent ON conversations(parent_id, last_message_at DESC);

CREATE TABLE IF NOT EXISTS conversation_messages (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    conversation_id INTEGER NOT NULL REFERENCES conversations(id) ON DELETE CASCADE,

    -- Who wrote it. sender_id points at parents.id or teachers.id depending
    -- on the role — resolved in the query, never assumed.
    sender_role     TEXT    NOT NULL CHECK (sender_role IN ('parent','teacher')),
    sender_id       INTEGER NOT NULL,

    body            TEXT    NOT NULL,
    read_at         TEXT,
    created_at      TEXT    NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ','now'))
);

CREATE INDEX IF NOT EXISTS idx_msg_conv ON conversation_messages(conversation_id, created_at);


-- =============================================================================
-- v_parent_children
-- The join every parent-facing query has to pass through, written once.
-- =============================================================================
-- The access rule from schema.sql lives here now instead of being retyped in
-- twenty endpoints. A parent endpoint that forgets to filter on parent_id is
-- a bug; a parent endpoint that selects from this view and forgets is still a
-- bug, but it is one you can find by grepping for the view name.
DROP VIEW IF EXISTS v_parent_children;
CREATE VIEW v_parent_children AS
SELECT
    ps.parent_id,
    ps.relationship,
    s.id                AS student_id,
    s.external_id,
    s.full_name,
    s.display_name,
    s.grade,
    s.avatar,
    s.avatar_color,
    s.support_profile,
    s.support_notes,
    s.drift_threshold_ms,
    s.day_streak,
    s.stars,
    s.level,
    s.last_active_date,
    s.onboarded_at,
    s.is_active
FROM parent_student ps
JOIN students s ON s.id = ps.student_id
WHERE s.is_active = 1;
