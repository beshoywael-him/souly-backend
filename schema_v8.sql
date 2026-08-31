-- =============================================================================
-- schema_v8 — the teacher's dashboard, and a topic on the flag
--
-- Runs after schema_v7.sql. Re-runnable, like every file before it.
--
-- WHY TEACHERS GET A THIRD TOKEN TABLE
-- ------------------------------------
-- schema_v4 gave students `auth_tokens`; schema_v7 gave parents
-- `parent_tokens`, and wrote down why: two tables cannot be confused for each
-- other, so a student token can never satisfy a parent check however the
-- query is written. A teacher sees EVERY child in the class — strictly more
-- than a parent sees — so the same reasoning applies with more force. This is
-- the third realm, not a role column on somebody else's table.
--
-- WHY A FLAG NEEDS A TOPIC
-- ------------------------
-- Until now a flag said `gaze_away` and named a child, and that was all. The
-- home robot could learn that a child had struggled but not what they had
-- struggled with, so the loop this project exists to close stopped one step
-- short. `topic_id` is what the CV rig fills in from the lesson the teacher
-- has open, and it is what the student app reads to decide what to re-teach
-- that evening.
--
-- It is nullable on purpose. A detection with no topic is still a real
-- detection worth showing a teacher; it just cannot start a lesson by itself.
--
-- A NOTE ON 'distress'
-- --------------------
-- `flags.flag_type` still permits 'distress' at the database level, because
-- SQLite cannot drop a CHECK constraint without rebuilding the table, and
-- `flag_events` holds a foreign key into `flags` that a rebuild would put at
-- risk for no real gain. The value is removed where it actually matters — the
-- `FlagType` enum in app/models.py, which is the contract the CV rig, the
-- robot and the dashboard all code against. Nothing can create one any more.
-- We settled early that this system reports engagement and never infers
-- emotion, and an API that cannot express a claim cannot make it.
-- =============================================================================

PRAGMA foreign_keys = ON;


-- =============================================================================
-- 1. TEACHER SESSIONS
-- =============================================================================

CREATE TABLE IF NOT EXISTS teacher_tokens (
    token           TEXT    PRIMARY KEY,
    teacher_id      INTEGER NOT NULL REFERENCES teachers(id) ON DELETE CASCADE,
    created_at      TEXT    NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ','now')),
    expires_at      TEXT    NOT NULL,
    device_label    TEXT
);

CREATE INDEX IF NOT EXISTS idx_teacher_tokens_teacher
    ON teacher_tokens(teacher_id);

-- The dashboard shows every child's flags, so the realistic threat is an
-- unattended classroom laptop, not a remote attacker. Same lockout shape as
-- the student picker.
ALTER TABLE teachers ADD COLUMN failed_logins  INTEGER NOT NULL DEFAULT 0;
ALTER TABLE teachers ADD COLUMN locked_until   TEXT;
ALTER TABLE teachers ADD COLUMN last_login_at  TEXT;


-- =============================================================================
-- 2. WHAT THE CHILD WAS DRIFTING AWAY FROM
-- =============================================================================

ALTER TABLE flags ADD COLUMN topic_id INTEGER REFERENCES topics(id) ON DELETE SET NULL;

CREATE INDEX IF NOT EXISTS idx_flags_topic ON flags(topic_id);


-- =============================================================================
-- 3. THE QUEUE THE DASHBOARD READS
-- =============================================================================

-- Replaced rather than added to: the original view predates both the topic
-- and the per-student threshold, and the dashboard needs all three in one
-- read so a queue refresh is a single query.
DROP VIEW IF EXISTS v_pending_flags;

CREATE VIEW v_pending_flags AS
SELECT
    f.id, f.student_id, s.display_name AS student_name, s.external_id,
    s.avatar, s.avatar_color, s.support_profile, s.drift_threshold_ms,
    f.flag_type, f.confidence, f.duration_ms, f.source,
    f.topic_id, t.title AS topic_title, t.subject AS topic_subject,
    f.detected_at, f.created_at, f.status
FROM flags f
JOIN students s ON s.id = f.student_id
LEFT JOIN topics t ON t.id = f.topic_id
WHERE f.status = 'pending'
ORDER BY f.detected_at DESC;
