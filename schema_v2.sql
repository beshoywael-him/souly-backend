-- =============================================================================
-- Souly — Schema v2: everything the student UI needs
--
-- Runs AFTER schema.sql. Every statement is IF NOT EXISTS, so applying it to
-- an existing Phase 1 database is safe and non-destructive.
--
-- The rule this file follows: if a number appears on the student's screen,
-- it has a column here. Nothing in the UI is allowed to be a hardcoded
-- literal, because a demo where the star count never changes is a demo that
-- gets caught.
-- =============================================================================

PRAGMA foreign_keys = ON;


-- =============================================================================
-- CURRICULUM STRUCTURE
-- =============================================================================

-- -----------------------------------------------------------------------------
-- subjects
-- The six cards on the Learn screen. `topics.subject` was free text in v1;
-- this promotes it to a real table so difficulty, icon and colour live in
-- the database rather than in the markup.
-- -----------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS subjects (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    code            TEXT    NOT NULL UNIQUE,     -- 'MATH'
    name            TEXT    NOT NULL,            -- 'Mathematics'
    icon            TEXT    NOT NULL DEFAULT '📚',
    difficulty      TEXT    NOT NULL DEFAULT 'Medium'
                        CHECK (difficulty IN ('Easy','Medium','Hard')),
    -- CSS gradient endpoints, so re-theming a subject never touches code.
    color_from      TEXT    NOT NULL DEFAULT '#7C3AED',
    color_to        TEXT    NOT NULL DEFAULT '#A855F7',
    sort_order      INTEGER NOT NULL DEFAULT 0,
    is_active       INTEGER NOT NULL DEFAULT 1 CHECK (is_active IN (0,1)),
    created_at      TEXT    NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ','now'))
);

-- Link the v1 topics table to subjects without breaking existing rows.
-- (SQLite can't add a FK to an existing table; the column is advisory and
-- joins go through subjects.code = topics.subject as a fallback.)
ALTER TABLE topics ADD COLUMN subject_id INTEGER REFERENCES subjects(id);


-- -----------------------------------------------------------------------------
-- lessons
-- A lesson is a sequence of steps on one topic. This is what "Today's Lesson"
-- and "Continue Learning" point at.
-- -----------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS lessons (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    topic_id        INTEGER NOT NULL REFERENCES topics(id) ON DELETE CASCADE,
    code            TEXT    NOT NULL UNIQUE,
    title           TEXT    NOT NULL,
    subtitle        TEXT,
    icon            TEXT    NOT NULL DEFAULT '📘',
    estimated_min   INTEGER NOT NULL DEFAULT 10,
    sort_order      INTEGER NOT NULL DEFAULT 0,
    is_verified     INTEGER NOT NULL DEFAULT 0 CHECK (is_verified IN (0,1)),
    created_at      TEXT    NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ','now'))
);


-- -----------------------------------------------------------------------------
-- lesson_steps
-- One card of teaching content. `body` is what the robot reads aloud and what
-- the RAG layer indexes, so it must be real curriculum prose, not a summary.
-- -----------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS lesson_steps (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    lesson_id       INTEGER NOT NULL REFERENCES lessons(id) ON DELETE CASCADE,
    step_no         INTEGER NOT NULL,
    heading         TEXT,
    body            TEXT    NOT NULL,
    visual          TEXT,   -- an emoji or image url shown alongside
    UNIQUE (lesson_id, step_no)
);


-- -----------------------------------------------------------------------------
-- lesson_progress
-- Per student, per lesson. Drives the "60% Complete" bar on the home screen.
-- -----------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS lesson_progress (
    id                  INTEGER PRIMARY KEY AUTOINCREMENT,
    student_id          INTEGER NOT NULL REFERENCES students(id) ON DELETE CASCADE,
    lesson_id           INTEGER NOT NULL REFERENCES lessons(id) ON DELETE CASCADE,
    steps_completed     INTEGER NOT NULL DEFAULT 0,
    is_complete         INTEGER NOT NULL DEFAULT 0 CHECK (is_complete IN (0,1)),
    last_step_no        INTEGER NOT NULL DEFAULT 0,
    started_at          TEXT,
    completed_at        TEXT,
    updated_at          TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ','now')),
    UNIQUE (student_id, lesson_id)
);


-- -----------------------------------------------------------------------------
-- questions
-- The question bank. Used by quizzes and by the lesson "Try It Yourself" card.
--
-- Why a bank at all, when Phase 2 has an LLM that can generate questions:
-- a generated question can be wrong, and a wrong question in front of a child
-- with a learning disability is worse than a boring one. The bank is the
-- verified floor; the LLM adds variety on top of it and its output is checked
-- against the same schema.
-- -----------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS questions (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    topic_id        INTEGER NOT NULL REFERENCES topics(id) ON DELETE CASCADE,
    lesson_id       INTEGER REFERENCES lessons(id) ON DELETE SET NULL,

    prompt          TEXT    NOT NULL,
    -- JSON array of option strings: ["5/8","3/8","6/8","8/5"]
    options         TEXT    NOT NULL,
    correct_index   INTEGER NOT NULL,
    explanation     TEXT,               -- read aloud after answering
    hint            TEXT,               -- the Hint button

    difficulty      INTEGER NOT NULL DEFAULT 2 CHECK (difficulty BETWEEN 1 AND 5),
    -- 'bank' = human-verified. 'generated' = came from the LLM this session.
    origin          TEXT    NOT NULL DEFAULT 'bank'
                        CHECK (origin IN ('bank','generated')),
    is_verified     INTEGER NOT NULL DEFAULT 1 CHECK (is_verified IN (0,1)),
    created_at      TEXT    NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ','now'))
);


-- -----------------------------------------------------------------------------
-- quizzes / quiz_questions
-- A quiz is a materialised run: which questions, in which order, for whom.
-- Storing the order means a refresh mid-quiz resumes instead of restarting,
-- which matters when a student's tablet sleeps.
-- -----------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS quizzes (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    student_id      INTEGER NOT NULL REFERENCES students(id) ON DELETE CASCADE,
    topic_id        INTEGER REFERENCES topics(id) ON DELETE SET NULL,
    session_id      INTEGER REFERENCES sessions(id) ON DELETE SET NULL,

    total_questions INTEGER NOT NULL DEFAULT 10,
    current_index   INTEGER NOT NULL DEFAULT 0,
    score           INTEGER NOT NULL DEFAULT 0,
    correct_count   INTEGER NOT NULL DEFAULT 0,
    lives           INTEGER NOT NULL DEFAULT 3,
    streak          INTEGER NOT NULL DEFAULT 0,

    status          TEXT    NOT NULL DEFAULT 'active'
                        CHECK (status IN ('active','complete','abandoned')),
    started_at      TEXT    NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ','now')),
    completed_at    TEXT
);

CREATE TABLE IF NOT EXISTS quiz_questions (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    quiz_id         INTEGER NOT NULL REFERENCES quizzes(id) ON DELETE CASCADE,
    question_id     INTEGER NOT NULL REFERENCES questions(id) ON DELETE CASCADE,
    position        INTEGER NOT NULL,
    answered_index  INTEGER,
    is_correct      INTEGER CHECK (is_correct IN (0,1)),
    answered_at     TEXT,
    UNIQUE (quiz_id, position)
);


-- =============================================================================
-- GAMIFICATION — all of it real, none of it decoration
-- =============================================================================

-- -----------------------------------------------------------------------------
-- badges
-- `criteria_type` + `criteria_value` make badges data, not code. Adding a new
-- badge is an INSERT; the unlock engine already knows how to evaluate it.
-- -----------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS badges (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    code            TEXT    NOT NULL UNIQUE,
    name            TEXT    NOT NULL,
    description     TEXT    NOT NULL,
    icon            TEXT    NOT NULL DEFAULT '🏅',
    tier            TEXT    NOT NULL DEFAULT 'bronze'
                        CHECK (tier IN ('bronze','silver','gold')),

    criteria_type   TEXT    NOT NULL
                        CHECK (criteria_type IN (
                            'lessons_completed','quizzes_completed',
                            'questions_correct','games_won',
                            'chat_questions','streak_days','stars_earned'
                        )),
    criteria_value  INTEGER NOT NULL,
    sort_order      INTEGER NOT NULL DEFAULT 0
);

CREATE TABLE IF NOT EXISTS student_badges (
    student_id      INTEGER NOT NULL REFERENCES students(id) ON DELETE CASCADE,
    badge_id        INTEGER NOT NULL REFERENCES badges(id)   ON DELETE CASCADE,
    unlocked_at     TEXT    NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ','now')),
    PRIMARY KEY (student_id, badge_id)
);


-- -----------------------------------------------------------------------------
-- rewards / student_rewards
-- The shop. Stars are spent here, which is what makes earning them mean
-- something — a currency with nothing to buy is just a score.
-- -----------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS rewards (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    code            TEXT    NOT NULL UNIQUE,
    name            TEXT    NOT NULL,
    description     TEXT,
    icon            TEXT    NOT NULL DEFAULT '🎁',
    cost_stars      INTEGER NOT NULL,
    category        TEXT    NOT NULL DEFAULT 'cosmetic'
                        CHECK (category IN ('cosmetic','theme','sound','content','game')),
    -- What unlocking actually does, e.g. {"theme":"purple"} — the UI applies it.
    payload         TEXT,
    sort_order      INTEGER NOT NULL DEFAULT 0
);

CREATE TABLE IF NOT EXISTS student_rewards (
    student_id      INTEGER NOT NULL REFERENCES students(id) ON DELETE CASCADE,
    reward_id       INTEGER NOT NULL REFERENCES rewards(id)  ON DELETE CASCADE,
    unlocked_at     TEXT    NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ','now')),
    is_equipped     INTEGER NOT NULL DEFAULT 0 CHECK (is_equipped IN (0,1)),
    PRIMARY KEY (student_id, reward_id)
);


-- -----------------------------------------------------------------------------
-- games / game_plays
-- -----------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS games (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    code            TEXT    NOT NULL UNIQUE,
    name            TEXT    NOT NULL,
    description     TEXT,
    icon            TEXT    NOT NULL DEFAULT '🎮',
    difficulty      TEXT    NOT NULL DEFAULT 'Easy'
                        CHECK (difficulty IN ('Easy','Medium','Hard')),
    star_reward     INTEGER NOT NULL DEFAULT 25,
    subject_id      INTEGER REFERENCES subjects(id) ON DELETE SET NULL,
    topic_id        INTEGER REFERENCES topics(id) ON DELETE SET NULL,
    -- Which built-in mini-game engine renders it: 'math_sprint', 'memory_match', 'word_builder'
    engine          TEXT    NOT NULL DEFAULT 'math_sprint',
    is_featured     INTEGER NOT NULL DEFAULT 0 CHECK (is_featured IN (0,1)),
    sort_order      INTEGER NOT NULL DEFAULT 0
);

CREATE TABLE IF NOT EXISTS game_plays (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    student_id      INTEGER NOT NULL REFERENCES students(id) ON DELETE CASCADE,
    game_id         INTEGER NOT NULL REFERENCES games(id)    ON DELETE CASCADE,
    score           INTEGER NOT NULL DEFAULT 0,
    max_score       INTEGER NOT NULL DEFAULT 0,
    is_win          INTEGER NOT NULL DEFAULT 0 CHECK (is_win IN (0,1)),
    stars_earned    INTEGER NOT NULL DEFAULT 0,
    duration_s      INTEGER,
    played_at       TEXT    NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ','now'))
);


-- -----------------------------------------------------------------------------
-- daily_challenge_progress
-- "One lesson, one quiz, one game" — one row per student per day.
-- -----------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS daily_challenge_progress (
    id                  INTEGER PRIMARY KEY AUTOINCREMENT,
    student_id          INTEGER NOT NULL REFERENCES students(id) ON DELETE CASCADE,
    challenge_date      TEXT    NOT NULL,            -- YYYY-MM-DD
    lesson_done         INTEGER NOT NULL DEFAULT 0 CHECK (lesson_done IN (0,1)),
    quiz_done           INTEGER NOT NULL DEFAULT 0 CHECK (quiz_done IN (0,1)),
    game_done           INTEGER NOT NULL DEFAULT 0 CHECK (game_done IN (0,1)),
    reward_claimed      INTEGER NOT NULL DEFAULT 0 CHECK (reward_claimed IN (0,1)),
    reward_stars        INTEGER NOT NULL DEFAULT 100,
    UNIQUE (student_id, challenge_date)
);


-- =============================================================================
-- PROGRESS & ACTIVITY
-- =============================================================================

-- -----------------------------------------------------------------------------
-- activity_log
-- Every meaningful thing a student does, with its star/XP value and how long
-- it took. This one table powers the weekly bar chart, "Time Spent Learning",
-- the streak calculation, and the parent report in the next phase.
-- -----------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS activity_log (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    student_id      INTEGER NOT NULL REFERENCES students(id) ON DELETE CASCADE,
    activity_type   TEXT    NOT NULL
                        CHECK (activity_type IN (
                            'lesson_step','lesson_complete','quiz_answer',
                            'quiz_complete','game_play','chat_question',
                            'reward_unlock','badge_unlock','daily_challenge',
                            'flag_resolved','login'
                        )),
    subject_id      INTEGER REFERENCES subjects(id) ON DELETE SET NULL,
    topic_id        INTEGER REFERENCES topics(id)   ON DELETE SET NULL,
    reference_id    INTEGER,                -- id of the lesson/quiz/game involved
    stars_delta     INTEGER NOT NULL DEFAULT 0,
    xp_delta        INTEGER NOT NULL DEFAULT 0,
    duration_s      INTEGER NOT NULL DEFAULT 0,
    detail          TEXT,
    occurred_at     TEXT    NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ','now')),
    activity_date   TEXT    NOT NULL DEFAULT (strftime('%Y-%m-%d','now'))
);


-- -----------------------------------------------------------------------------
-- skills / student_skills
-- The "Skills You've Improved" panel: Problem Solving, Reading, etc.
-- Distinct from mastery, which is per curriculum topic.
-- -----------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS skills (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    code            TEXT    NOT NULL UNIQUE,
    name            TEXT    NOT NULL,
    icon            TEXT    NOT NULL DEFAULT '💡',
    sort_order      INTEGER NOT NULL DEFAULT 0
);

CREATE TABLE IF NOT EXISTS student_skills (
    student_id      INTEGER NOT NULL REFERENCES students(id) ON DELETE CASCADE,
    skill_id        INTEGER NOT NULL REFERENCES skills(id)   ON DELETE CASCADE,
    level           REAL    NOT NULL DEFAULT 0.0
                        CHECK (level >= 0.0 AND level <= 1.0),
    updated_at      TEXT    NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ','now')),
    PRIMARY KEY (student_id, skill_id)
);


-- -----------------------------------------------------------------------------
-- weekly_goals
-- The checklist on the Progress screen.
-- -----------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS weekly_goals (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    student_id      INTEGER NOT NULL REFERENCES students(id) ON DELETE CASCADE,
    week_start      TEXT    NOT NULL,            -- YYYY-MM-DD, Monday
    label           TEXT    NOT NULL,
    target_count    INTEGER NOT NULL DEFAULT 1,
    current_count   INTEGER NOT NULL DEFAULT 0,
    goal_type       TEXT    NOT NULL DEFAULT 'lesson'
                        CHECK (goal_type IN ('lesson','quiz','game','chat','story')),
    topic_id        INTEGER REFERENCES topics(id) ON DELETE SET NULL,
    sort_order      INTEGER NOT NULL DEFAULT 0
);


-- -----------------------------------------------------------------------------
-- schedule_items
-- "Today's Schedule" on the home screen.
-- -----------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS schedule_items (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    student_id      INTEGER NOT NULL REFERENCES students(id) ON DELETE CASCADE,
    day_of_week     INTEGER NOT NULL CHECK (day_of_week BETWEEN 0 AND 6),  -- 0=Mon
    start_time      TEXT    NOT NULL,            -- 'HH:MM' 24h
    label           TEXT    NOT NULL,
    icon            TEXT    NOT NULL DEFAULT '🕐',
    subject_id      INTEGER REFERENCES subjects(id) ON DELETE SET NULL,
    sort_order      INTEGER NOT NULL DEFAULT 0
);


-- =============================================================================
-- SETTINGS & TUTOR
-- =============================================================================

-- -----------------------------------------------------------------------------
-- student_settings
-- The accessibility toggles are the reason this table matters more than it
-- looks. For this user group they are not preferences, they are whether the
-- app is usable at all — so they persist server-side and follow the student
-- to any device, rather than living in one browser's storage.
-- -----------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS student_settings (
    student_id          INTEGER PRIMARY KEY REFERENCES students(id) ON DELETE CASCADE,

    language            TEXT    NOT NULL DEFAULT 'en'
                            CHECK (language IN ('en','ar','fr')),
    voice_volume        INTEGER NOT NULL DEFAULT 70 CHECK (voice_volume BETWEEN 0 AND 100),
    theme               TEXT    NOT NULL DEFAULT 'light'
                            CHECK (theme IN ('light','purple','dark')),
    font_size           TEXT    NOT NULL DEFAULT 'medium'
                            CHECK (font_size IN ('small','medium','large')),

    -- Accessibility
    read_aloud          INTEGER NOT NULL DEFAULT 1 CHECK (read_aloud IN (0,1)),
    high_contrast       INTEGER NOT NULL DEFAULT 0 CHECK (high_contrast IN (0,1)),
    larger_buttons      INTEGER NOT NULL DEFAULT 0 CHECK (larger_buttons IN (0,1)),
    voice_commands      INTEGER NOT NULL DEFAULT 1 CHECK (voice_commands IN (0,1)),
    closed_captions     INTEGER NOT NULL DEFAULT 1 CHECK (closed_captions IN (0,1)),
    reduce_motion       INTEGER NOT NULL DEFAULT 0 CHECK (reduce_motion IN (0,1)),

    -- Robot hardware toggles from the Profile screen
    mic_enabled         INTEGER NOT NULL DEFAULT 1 CHECK (mic_enabled IN (0,1)),
    camera_enabled      INTEGER NOT NULL DEFAULT 1 CHECK (camera_enabled IN (0,1)),
    speaker_enabled     INTEGER NOT NULL DEFAULT 1 CHECK (speaker_enabled IN (0,1)),
    led_enabled         INTEGER NOT NULL DEFAULT 1 CHECK (led_enabled IN (0,1)),
    face_expressions    INTEGER NOT NULL DEFAULT 1 CHECK (face_expressions IN (0,1)),

    updated_at          TEXT    NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ','now'))
);


-- -----------------------------------------------------------------------------
-- chat_messages
-- Ask Souly history. Kept because it feeds the "AI Expert — asked 100
-- questions" badge, gives the parent report something qualitative, and lets
-- the agent remember earlier turns within a session.
-- -----------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS chat_messages (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    student_id      INTEGER NOT NULL REFERENCES students(id) ON DELETE CASCADE,
    session_id      INTEGER REFERENCES sessions(id) ON DELETE SET NULL,
    role            TEXT    NOT NULL CHECK (role IN ('student','righty','system')),
    content         TEXT    NOT NULL,

    input_mode      TEXT    NOT NULL DEFAULT 'text'
                        CHECK (input_mode IN ('text','voice','quick_action')),
    -- Which engine produced a 'righty' message: 'gemini' | 'fallback' | 'canned'.
    -- Recorded so you can tell at a glance whether a demo answer was real.
    engine          TEXT,
    stt_confidence  REAL,
    latency_ms      INTEGER,
    source_refs     TEXT,   -- JSON array of grounding chunks
    created_at      TEXT    NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ','now'))
);


-- =============================================================================
-- INDEXES
-- =============================================================================

CREATE INDEX IF NOT EXISTS idx_lessons_topic          ON lessons(topic_id, sort_order);
CREATE INDEX IF NOT EXISTS idx_lesson_steps_lesson    ON lesson_steps(lesson_id, step_no);
CREATE INDEX IF NOT EXISTS idx_lesson_progress_student ON lesson_progress(student_id);
CREATE INDEX IF NOT EXISTS idx_questions_topic        ON questions(topic_id, difficulty);
CREATE INDEX IF NOT EXISTS idx_quizzes_student        ON quizzes(student_id, status);
CREATE INDEX IF NOT EXISTS idx_quiz_questions_quiz    ON quiz_questions(quiz_id, position);
CREATE INDEX IF NOT EXISTS idx_activity_student_date  ON activity_log(student_id, activity_date);
CREATE INDEX IF NOT EXISTS idx_activity_student_time  ON activity_log(student_id, occurred_at DESC);
CREATE INDEX IF NOT EXISTS idx_game_plays_student     ON game_plays(student_id, played_at DESC);
CREATE INDEX IF NOT EXISTS idx_chat_student           ON chat_messages(student_id, created_at DESC);
CREATE INDEX IF NOT EXISTS idx_student_badges_student ON student_badges(student_id);
CREATE INDEX IF NOT EXISTS idx_topics_subject_id      ON topics(subject_id);


-- =============================================================================
-- VIEWS
-- =============================================================================

-- Per-subject mastery for the Learn screen and the Progress bars.
CREATE VIEW IF NOT EXISTS v_subject_progress AS
SELECT
    st.id                                       AS student_id,
    sub.id                                      AS subject_id,
    sub.code                                    AS subject_code,
    sub.name                                    AS subject_name,
    sub.icon,
    sub.difficulty,
    sub.color_from,
    sub.color_to,
    sub.sort_order,
    COALESCE(ROUND(AVG(m.level) * 100), 0)      AS progress_pct,
    COUNT(DISTINCT t.id)                        AS topic_count,
    COUNT(DISTINCT m.topic_id)                  AS topics_started
FROM students st
CROSS JOIN subjects sub
LEFT JOIN topics  t ON t.subject_id = sub.id
LEFT JOIN mastery m ON m.topic_id = t.id AND m.student_id = st.id
WHERE sub.is_active = 1
GROUP BY st.id, sub.id;


-- Daily activity totals — the weekly bar chart and "Time Spent Learning".
CREATE VIEW IF NOT EXISTS v_daily_activity AS
SELECT
    student_id,
    activity_date,
    COUNT(*)                AS event_count,
    SUM(stars_delta)        AS stars_earned,
    SUM(xp_delta)           AS xp_earned,
    SUM(duration_s)         AS seconds_spent
FROM activity_log
GROUP BY student_id, activity_date;
