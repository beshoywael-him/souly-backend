-- =============================================================================
-- Souly — Unified Database Schema
-- Phase 0 deliverable. Single source of truth for the whole system.
--
-- Target: SQLite 3.35+
-- Everything (robot app, classroom CV, teacher dashboard, parent portal)
-- reads and writes THIS schema. If a screen needs a field that isn't here,
-- the field goes here first — not into a frontend-local variable.
--
-- Conventions:
--   * Timestamps are TEXT, ISO-8601 UTC ("2026-08-18T12:34:56Z").
--     SQLite has no native datetime type; strings sort correctly in ISO-8601.
--   * Booleans are INTEGER 0/1.
--   * Enum-ish columns are TEXT with a CHECK constraint, so a typo fails loudly
--     at insert time instead of silently creating a fifth flag status.
--   * Free-form extras go in a `metadata` TEXT column holding JSON.
-- =============================================================================

PRAGMA foreign_keys = ON;
PRAGMA journal_mode = WAL;


-- =============================================================================
-- PEOPLE
-- =============================================================================

-- -----------------------------------------------------------------------------
-- students
-- The centre of the schema. Everything else hangs off student_id.
-- The gamification columns exist because the student home screen shows them
-- (day streak, stars, level, weekly progress) — they are real persisted state,
-- not frontend decoration.
-- -----------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS students (
    id                  INTEGER PRIMARY KEY AUTOINCREMENT,

    -- Stable, human-readable id used by external devices (CV rig, robot tablet)
    -- so they never have to know about autoincrement primary keys.
    external_id         TEXT    NOT NULL UNIQUE,

    full_name           TEXT    NOT NULL,
    -- What the robot actually calls them out loud. Often a nickname.
    display_name        TEXT    NOT NULL,
    grade               TEXT,

    -- Accessibility profile. Drives tutoring pace, prompt style, and how
    -- aggressively the CV layer flags attention drift for this student.
    support_profile     TEXT    NOT NULL DEFAULT 'none'
                            CHECK (support_profile IN (
                                'none', 'autism', 'adhd', 'dyslexia',
                                'hearing_impairment', 'visual_impairment',
                                'speech_impairment', 'other'
                            )),
    -- Anything a human wrote that the agent should know:
    -- "responds badly to loud tones", "needs 8s of silence before re-prompting"
    support_notes       TEXT,

    -- Per-student tuning for the classroom CV. A student with autism may look
    -- away frequently as self-regulation and should NOT be flagged at 3s.
    drift_threshold_ms  INTEGER NOT NULL DEFAULT 5000,

    avatar_url          TEXT,
    preferred_voice_id  TEXT,   -- TTS voice, once TTS vendor is chosen

    -- Gamification state (shown on the student home screen)
    day_streak          INTEGER NOT NULL DEFAULT 0,
    stars               INTEGER NOT NULL DEFAULT 0,
    level               INTEGER NOT NULL DEFAULT 1,
    last_active_date    TEXT,   -- YYYY-MM-DD, used to compute streak continuity

    is_active           INTEGER NOT NULL DEFAULT 1 CHECK (is_active IN (0,1)),
    created_at          TEXT    NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ','now')),
    updated_at          TEXT    NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ','now'))
);


-- -----------------------------------------------------------------------------
-- teachers
-- Needed in Phase 1 (not Phase 3) because `flags.reviewed_by_teacher_id` is a
-- foreign key from day one. Creating the table later means an ALTER mid-project.
-- -----------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS teachers (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    full_name       TEXT    NOT NULL,
    email           TEXT    NOT NULL UNIQUE,
    password_hash   TEXT    NOT NULL,
    is_active       INTEGER NOT NULL DEFAULT 1 CHECK (is_active IN (0,1)),
    created_at      TEXT    NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ','now'))
);


-- -----------------------------------------------------------------------------
-- parents
-- The "secret account". Login is by access code rather than a public signup,
-- so the stored value is a HASH of the code — never the code itself. If the
-- database leaks, the codes do not.
-- -----------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS parents (
    id                  INTEGER PRIMARY KEY AUTOINCREMENT,
    full_name           TEXT    NOT NULL,
    email               TEXT    UNIQUE,
    phone               TEXT,

    -- Hash of the private access code handed to this parent out-of-band.
    access_code_hash    TEXT    NOT NULL,
    -- Optional conventional password, if you add email login later.
    password_hash       TEXT,

    last_login_at       TEXT,
    is_active           INTEGER NOT NULL DEFAULT 1 CHECK (is_active IN (0,1)),
    created_at          TEXT    NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ','now'))
);


-- -----------------------------------------------------------------------------
-- parent_student
-- THIS TABLE IS THE ACCESS CONTROL for the parent portal.
--
-- "A parent can see only their own child" is enforced by joining through here
-- on every parent-facing query, e.g.:
--
--     SELECT s.* FROM students s
--     JOIN parent_student ps ON ps.student_id = s.id
--     WHERE ps.parent_id = :parent_id AND s.id = :requested_student_id;
--
-- If that returns zero rows, the API returns 404 (not 403 — do not confirm
-- that another student exists). Never trust a student_id that arrives from
-- the parent's browser without passing it through this join first.
-- -----------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS parent_student (
    parent_id       INTEGER NOT NULL REFERENCES parents(id)  ON DELETE CASCADE,
    student_id      INTEGER NOT NULL REFERENCES students(id) ON DELETE CASCADE,
    relationship    TEXT    NOT NULL DEFAULT 'guardian'
                        CHECK (relationship IN ('mother','father','guardian','other')),
    created_at      TEXT    NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ','now')),
    PRIMARY KEY (parent_id, student_id)
);


-- =============================================================================
-- CURRICULUM & LEARNING
-- =============================================================================

-- -----------------------------------------------------------------------------
-- topics
-- The curriculum tree. `rag_collection` links a topic to its ChromaDB
-- collection so Phase 2's RAG lookup is a column read, not a hardcoded map.
-- -----------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS topics (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    code            TEXT    NOT NULL UNIQUE,   -- 'MATH.FRACTIONS.INTRO'
    subject         TEXT    NOT NULL,          -- 'Mathematics'
    title           TEXT    NOT NULL,          -- 'Introduction to Fractions'
    description     TEXT,
    grade           TEXT,
    parent_topic_id INTEGER REFERENCES topics(id) ON DELETE SET NULL,
    sort_order      INTEGER NOT NULL DEFAULT 0,

    -- Phase 2: which ChromaDB collection holds this topic's verified content.
    rag_collection  TEXT,
    -- Set to 1 only once a human has confirmed the source material is correct.
    -- The agent must refuse to generate questions from unverified topics.
    is_verified     INTEGER NOT NULL DEFAULT 0 CHECK (is_verified IN (0,1)),

    created_at      TEXT    NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ','now'))
);


-- -----------------------------------------------------------------------------
-- sessions
-- One continuous tutoring interaction. Opened when a student starts working
-- with the robot (or is picked up after a flag), closed when they stop.
-- -----------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS sessions (
    id                  INTEGER PRIMARY KEY AUTOINCREMENT,
    student_id          INTEGER NOT NULL REFERENCES students(id) ON DELETE CASCADE,
    topic_id            INTEGER REFERENCES topics(id) ON DELETE SET NULL,

    mode                TEXT    NOT NULL DEFAULT 'home_robot'
                            CHECK (mode IN ('home_robot','classroom','practice','assessment')),
    -- Which physical thing the student was in front of.
    device              TEXT,   -- 'robot_tablet', 'smart_screen', 'laptop'

    started_at          TEXT    NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ','now')),
    ended_at            TEXT,

    questions_asked     INTEGER NOT NULL DEFAULT 0,
    questions_correct   INTEGER NOT NULL DEFAULT 0,
    stars_earned        INTEGER NOT NULL DEFAULT 0,

    -- Agent-written plain-language recap. This is what the parent portal shows,
    -- so it must be readable by a non-technical adult.
    summary             TEXT,
    metadata            TEXT    -- JSON
);


-- -----------------------------------------------------------------------------
-- mastery
-- Per-student, per-topic competence. One row per (student, topic).
-- `level` is 0.0–1.0 so the UI can render it as a percentage directly.
-- -----------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS mastery (
    id                  INTEGER PRIMARY KEY AUTOINCREMENT,
    student_id          INTEGER NOT NULL REFERENCES students(id) ON DELETE CASCADE,
    topic_id            INTEGER NOT NULL REFERENCES topics(id)   ON DELETE CASCADE,

    level               REAL    NOT NULL DEFAULT 0.0
                            CHECK (level >= 0.0 AND level <= 1.0),
    attempts            INTEGER NOT NULL DEFAULT 0,
    correct             INTEGER NOT NULL DEFAULT 0,
    -- Consecutive correct answers. Drives "they've got it, move on".
    current_streak      INTEGER NOT NULL DEFAULT 0,
    best_streak         INTEGER NOT NULL DEFAULT 0,

    last_practiced_at   TEXT,
    updated_at          TEXT    NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ','now')),

    UNIQUE (student_id, topic_id)
);


-- -----------------------------------------------------------------------------
-- attempts
-- One question asked and (maybe) answered. The raw material for mastery,
-- the parent report, and the demo's "watch it adapt" moment.
-- -----------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS attempts (
    id                  INTEGER PRIMARY KEY AUTOINCREMENT,
    session_id          INTEGER REFERENCES sessions(id) ON DELETE CASCADE,
    student_id          INTEGER NOT NULL REFERENCES students(id) ON DELETE CASCADE,
    topic_id            INTEGER REFERENCES topics(id) ON DELETE SET NULL,

    question_text       TEXT    NOT NULL,
    expected_answer     TEXT,
    student_answer      TEXT,

    -- How the answer arrived. 'voice' means it went through STT; keeping the
    -- raw transcript lets you show judges where STT helped or hurt.
    input_mode          TEXT    NOT NULL DEFAULT 'voice'
                            CHECK (input_mode IN ('voice','touch','text','none')),
    stt_transcript      TEXT,
    stt_confidence      REAL,

    is_correct          INTEGER CHECK (is_correct IN (0,1)),  -- NULL = unanswered
    -- Which RAG chunks grounded this question. Provenance for the "no
    -- hallucinated curriculum" claim.
    source_refs         TEXT,   -- JSON array

    asked_at            TEXT    NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ','now')),
    answered_at         TEXT,
    latency_ms          INTEGER,
    metadata            TEXT    -- JSON
);


-- =============================================================================
-- THE FLAG SPINE  (Phase 1 — the single most important path in the system)
-- =============================================================================

-- -----------------------------------------------------------------------------
-- flags
-- A detected attention/engagement event travelling from the classroom CV
-- through teacher review to the robot.
--
-- Lifecycle:
--
--     pending ──approve──> approved ──pickup──> in_progress ──> done
--        │
--        └──dismiss──> dismissed
--
-- Every transition is also appended to flag_events, so the demo can render
-- the flag's whole journey live rather than just its final state.
-- -----------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS flags (
    id                      INTEGER PRIMARY KEY AUTOINCREMENT,
    student_id              INTEGER NOT NULL REFERENCES students(id) ON DELETE CASCADE,
    -- NULL when the CV flags a student who has no session open — the normal
    -- classroom case. Set when the drift happens mid-tutoring.
    session_id              INTEGER REFERENCES sessions(id) ON DELETE SET NULL,

    source                  TEXT    NOT NULL DEFAULT 'classroom_cv'
                                CHECK (source IN ('classroom_cv','robot','teacher_manual','self_report')),

    flag_type               TEXT    NOT NULL
                                CHECK (flag_type IN (
                                    'gaze_away',            -- eyes off task
                                    'head_turn',            -- turned away
                                    'absent',               -- not in frame
                                    'prolonged_inactivity', -- present but idle
                                    'distress',             -- agitation cues
                                    'repeated_error',       -- wrong repeatedly
                                    'help_requested'        -- explicit ask
                                )),

    -- 0.0–1.0 from the CV model. Lets the teacher dashboard sort by certainty
    -- and lets you set an auto-dismiss floor for noisy detections.
    confidence              REAL    CHECK (confidence IS NULL OR (confidence >= 0.0 AND confidence <= 1.0)),
    -- How long the drift lasted before the CV decided to publish.
    duration_ms             INTEGER,

    status                  TEXT    NOT NULL DEFAULT 'pending'
                                CHECK (status IN ('pending','approved','dismissed','in_progress','done')),

    -- When the CAMERA saw it (from the CV rig's clock).
    detected_at             TEXT    NOT NULL,
    -- When the BACKEND received it. Difference between the two = pipeline lag,
    -- which is a number the judges will ask about.
    created_at              TEXT    NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ','now')),

    reviewed_by_teacher_id  INTEGER REFERENCES teachers(id) ON DELETE SET NULL,
    reviewed_at             TEXT,
    picked_up_at            TEXT,   -- robot claimed it
    resolved_at             TEXT,
    resolution_note         TEXT,

    -- Anything CV-specific that doesn't deserve a column:
    -- {"camera_id":"cam-1","bbox":[...],"frame_no":1423}
    metadata                TEXT
);


-- -----------------------------------------------------------------------------
-- flag_events
-- Append-only audit log of every flag state transition. Never updated,
-- never deleted. This is what powers a live "here is the process happening"
-- visualisation in the presentation.
-- -----------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS flag_events (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    flag_id         INTEGER NOT NULL REFERENCES flags(id) ON DELETE CASCADE,
    from_status     TEXT,           -- NULL on creation
    to_status       TEXT    NOT NULL,
    -- Who or what caused it: 'classroom_cv', 'teacher:3', 'robot', 'system'
    actor           TEXT    NOT NULL,
    note            TEXT,
    created_at      TEXT    NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ','now'))
);


-- =============================================================================
-- INDEXES
-- Chosen for the queries the system actually runs every few seconds:
-- "pending flags for this student", "pending flags for the dashboard",
-- "this student's mastery", "this student's recent sessions".
-- =============================================================================

CREATE INDEX IF NOT EXISTS idx_flags_student_status  ON flags(student_id, status);
CREATE INDEX IF NOT EXISTS idx_flags_status_detected ON flags(status, detected_at DESC);
CREATE INDEX IF NOT EXISTS idx_flags_session         ON flags(session_id);
CREATE INDEX IF NOT EXISTS idx_flag_events_flag      ON flag_events(flag_id, id);

CREATE INDEX IF NOT EXISTS idx_sessions_student      ON sessions(student_id, started_at DESC);
CREATE INDEX IF NOT EXISTS idx_mastery_student       ON mastery(student_id);
CREATE INDEX IF NOT EXISTS idx_attempts_session      ON attempts(session_id, asked_at);
CREATE INDEX IF NOT EXISTS idx_attempts_student      ON attempts(student_id, asked_at DESC);
CREATE INDEX IF NOT EXISTS idx_parent_student_parent ON parent_student(parent_id);
CREATE INDEX IF NOT EXISTS idx_topics_subject        ON topics(subject, sort_order);


-- =============================================================================
-- VIEWS — convenience reads for the dashboards
-- =============================================================================

-- Teacher dashboard: the live queue, newest first, with student names attached.
CREATE VIEW IF NOT EXISTS v_pending_flags AS
SELECT
    f.id, f.student_id, s.display_name AS student_name, s.external_id,
    f.flag_type, f.confidence, f.duration_ms, f.source,
    f.detected_at, f.created_at, f.status
FROM flags f
JOIN students s ON s.id = f.student_id
WHERE f.status = 'pending'
ORDER BY f.detected_at DESC;

-- Parent portal / student home: overall progress across all topics.
CREATE VIEW IF NOT EXISTS v_student_progress AS
SELECT
    s.id AS student_id,
    s.display_name,
    s.day_streak,
    s.stars,
    s.level,
    COUNT(m.id)                                   AS topics_started,
    COALESCE(ROUND(AVG(m.level) * 100, 1), 0.0)   AS overall_progress_pct,
    COALESCE(SUM(m.attempts), 0)                  AS total_attempts,
    COALESCE(SUM(m.correct), 0)                   AS total_correct
FROM students s
LEFT JOIN mastery m ON m.student_id = s.id
GROUP BY s.id;
