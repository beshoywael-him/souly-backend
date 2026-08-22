-- =============================================================================
-- Souly — Schema v3: the merged lesson + tutor flow
--
-- Runs after schema.sql and schema_v2.sql. Every statement is re-runnable.
--
-- Three things this adds:
--   1. The data the tutor needs to give HINTS instead of answers
--   2. Somewhere to put questions the LLM generates from lesson content
--   3. A record of every help request, so we can prove the hint ladder works
--      rather than just asserting it
-- =============================================================================

PRAGMA foreign_keys = ON;


-- =============================================================================
-- 1. HINT GUARDRAIL DATA
--
-- Bastani et al. (PNAS 2025) tested ~1,000 students. Unrestricted GPT raised
-- practice performance 48% and then left those students 17% WORSE on a later
-- exam. A guardrailed tutor left them level with controls. Two things made the
-- difference, and both are columns here:
--
--   * the tutor never gives the answer, only hints
--   * the worked solution and the common wrong answers for THAT SPECIFIC ITEM
--     are in the tutor's prompt
--
-- Without `worked_solution`, the model has to re-derive the answer every time
-- it writes a hint, and a hint built on a wrong derivation is worse than no
-- hint. Without `common_wrong_answers`, it can't recognise WHICH mistake the
-- student just made.
-- =============================================================================

-- The full solution, in steps, for the tutor's eyes only. Never sent to the
-- browser before the student has answered.
ALTER TABLE questions ADD COLUMN worked_solution TEXT;

-- JSON: [{"answer": "3/8", "why": "counted the slices eaten, not the ones left"}]
-- Lets the tutor say "ah, I think you counted the ones you ate" instead of
-- "that's wrong, try again".
ALTER TABLE questions ADD COLUMN common_wrong_answers TEXT;

-- Which lesson step this question came from. For generated questions this is
-- the provenance: the exact text the question was derived from.
ALTER TABLE questions ADD COLUMN source_step_id INTEGER REFERENCES lesson_steps(id) ON DELETE SET NULL;

-- Generation provenance. NULL for bank questions.
ALTER TABLE questions ADD COLUMN generated_at TEXT;
ALTER TABLE questions ADD COLUMN generated_model TEXT;

-- Teacher review state for generated questions.
--   pending  = the LLM made it, nobody has looked
--   approved = a human read it and it's now as good as a bank question
--   rejected = a human read it and it was wrong; kept as a training signal
ALTER TABLE questions ADD COLUMN review_status TEXT NOT NULL DEFAULT 'approved';


-- =============================================================================
-- 2. HELP AND HINTS
-- =============================================================================

-- -----------------------------------------------------------------------------
-- hint_requests
--
-- Every time the student asks for help, or Souly offers it. Append-only.
--
-- This exists because "our hint ladder helps students learn" is a claim, and
-- a claim with no measurement is marketing. With this table we can actually
-- ask: after a tier-1 nudge, how often does the next attempt succeed? Does a
-- student who used help do better on the same topic later?
--
-- It's also the evidence for the design decision that help costs nothing:
-- if help were penalised we'd see requests drop and errors rise, and this is
-- where that would show up.
-- -----------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS hint_requests (
    id                  INTEGER PRIMARY KEY AUTOINCREMENT,
    student_id          INTEGER NOT NULL REFERENCES students(id) ON DELETE CASCADE,

    -- Exactly one of these is set, depending on where help was asked for.
    --
    -- `page_id` was `lesson_step_id` until schema_v5 replaced the invented
    -- lesson_steps with pages of the real book. It carries no REFERENCES
    -- clause because this file runs before v5 on every boot, so on a database
    -- built from scratch curriculum_pages does not exist yet — and SQLite
    -- resolves a foreign key when it PREPARES a statement against the table,
    -- not when a non-NULL value is written. A dead reference here would fail
    -- every insert into this table, NULL or not.
    page_id             INTEGER,
    question_id         INTEGER REFERENCES questions(id) ON DELETE SET NULL,
    quiz_id             INTEGER REFERENCES quizzes(id) ON DELETE SET NULL,

    -- What kind of help.
    --   simpler / example / another_way  -> re-explaining a page
    --   nudge / worked / stepwise / answer -> the four hint tiers on a question
    help_type           TEXT    NOT NULL
                            CHECK (help_type IN (
                                'simpler', 'example', 'another_way',
                                'nudge', 'worked', 'stepwise', 'answer',
                                'free_question'
                            )),
    -- 1-4 for the question ladder; NULL for re-explaining a page.
    tier                INTEGER CHECK (tier IS NULL OR tier BETWEEN 1 AND 4),

    -- Did the student ask, or did Souly notice they were stuck and offer?
    -- The research says autistic students under-ask, so the ratio of
    -- 'offered' to 'requested' is a number worth watching.
    initiated_by        TEXT    NOT NULL DEFAULT 'student'
                            CHECK (initiated_by IN ('student', 'souly')),

    -- What the student had done before asking, so we can tell a considered
    -- request from a reflex one.
    attempts_before     INTEGER NOT NULL DEFAULT 0,
    seconds_before      INTEGER NOT NULL DEFAULT 0,
    student_answer      TEXT,

    response_text       TEXT,
    engine              TEXT,       -- 'gemini' | 'fallback'
    latency_ms          INTEGER,

    -- Filled in later, once we know. This is the payoff column.
    resolved_correct    INTEGER CHECK (resolved_correct IN (0, 1)),

    created_at          TEXT    NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ','now'))
);


-- -----------------------------------------------------------------------------
-- step_explanations
--
-- Cache of alternative explanations the LLM has produced for a lesson step.
--
-- Two reasons to store rather than regenerate:
--   * Predictability. If a child taps "say it another way" twice, getting a
--     different answer each time is unsettling for exactly the users this app
--     is for. Cached means the same step gives the same alternative.
--   * Cost and latency. A cache hit is instant and free, which matters on a
--     MiFi at a competition.
-- -----------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS step_explanations (
    id                  INTEGER PRIMARY KEY AUTOINCREMENT,
    lesson_step_id      INTEGER NOT NULL REFERENCES lesson_steps(id) ON DELETE CASCADE,
    mode                TEXT    NOT NULL
                            CHECK (mode IN ('simpler', 'example', 'another_way')),
    -- Explanations are tuned per support profile: an autistic student gets a
    -- more literal rewrite than a student with ADHD, who gets a shorter one.
    support_profile     TEXT    NOT NULL DEFAULT 'none',
    body                TEXT    NOT NULL,
    engine              TEXT,
    created_at          TEXT    NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ','now')),
    UNIQUE (lesson_step_id, mode, support_profile)
);


-- =============================================================================
-- 3. THE MERGED FLOW
-- =============================================================================

-- Ties a chat message to the exact page that was on screen when it was sent.
-- This is what makes the tutor a hint layer rather than a chatbot: it always
-- knows what the child is looking at.
--
-- This was `lesson_step_id`, pointing at schema_v2's invented lesson_steps.
-- It is `page_id` since schema_v5, pointing at a page of the real book. The
-- rename has to happen HERE rather than in v5 because the chat_messages
-- rebuild further down this file runs on every boot and would otherwise drop
-- a column v5 had added on the previous one.
--
-- No REFERENCES clause: on a database built from scratch this file runs
-- before v5, so curriculum_pages does not exist yet. The column below in
-- chat_messages_new carries the foreign key, which is legal to declare
-- against a table that arrives later.
ALTER TABLE chat_messages ADD COLUMN page_id INTEGER;
ALTER TABLE chat_messages ADD COLUMN help_type TEXT;


-- -----------------------------------------------------------------------------
-- step_activity
--
-- How long a student spent on each step, and how they left it. Feeds stall
-- detection.
--
-- Per-student baselines matter here. Zapparrata et al. (2023) meta-analysed
-- 44 studies and found autistic people are slower across the board (g = .35),
-- so a global "12 seconds means stuck" constant would mislabel this cohort as
-- stuck constantly. We compare a student against their own median instead.
-- -----------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS step_activity (
    id                  INTEGER PRIMARY KEY AUTOINCREMENT,
    student_id          INTEGER NOT NULL REFERENCES students(id) ON DELETE CASCADE,
    lesson_step_id      INTEGER NOT NULL REFERENCES lesson_steps(id) ON DELETE CASCADE,
    seconds_on_step     INTEGER NOT NULL DEFAULT 0,
    help_requests       INTEGER NOT NULL DEFAULT 0,
    replayed_audio      INTEGER NOT NULL DEFAULT 0,
    went_back           INTEGER NOT NULL DEFAULT 0,
    created_at          TEXT    NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ','now'))
);


-- -----------------------------------------------------------------------------
-- Extra fields on lesson_steps for richer visuals.
--
-- "Topics only feature icons and it's a very narrow scale" — these give a step
-- something to show beyond an emoji.
-- -----------------------------------------------------------------------------
ALTER TABLE lesson_steps ADD COLUMN image_url TEXT;
-- Inline SVG or a diagram spec, for content that needs a real picture.
ALTER TABLE lesson_steps ADD COLUMN diagram TEXT;
-- Key terms to surface beside the text when a diagram is on screen. Adesope &
-- Nesbit (2012): narration plus FULL text alongside a picture gives no benefit
-- (g=0.06, ns), but narration plus short labels keeps the channels from
-- competing. JSON array of strings.
ALTER TABLE lesson_steps ADD COLUMN key_terms TEXT;


-- Topics get real artwork too, not just an emoji.
ALTER TABLE topics ADD COLUMN image_url TEXT;
ALTER TABLE topics ADD COLUMN color_from TEXT;
ALTER TABLE topics ADD COLUMN color_to TEXT;
ALTER TABLE topics ADD COLUMN summary TEXT;


-- =============================================================================
-- 4. INDEXES
-- =============================================================================

CREATE INDEX IF NOT EXISTS idx_hint_requests_student   ON hint_requests(student_id, created_at DESC);
CREATE INDEX IF NOT EXISTS idx_hint_requests_question  ON hint_requests(question_id, tier);
CREATE INDEX IF NOT EXISTS idx_step_expl_lookup        ON step_explanations(lesson_step_id, mode, support_profile);
CREATE INDEX IF NOT EXISTS idx_step_activity_student   ON step_activity(student_id, lesson_step_id);

-- Two indexes were removed here when schema_v5 went in. They covered
-- `questions.origin` and `questions.source_step_id`, both of which v5 removes
-- when it rebuilds the table around the real book.
--
-- This file runs BEFORE v5 on every boot (app/db.py globs schema*.sql in
-- filename order), so leaving them in place means the second boot after v5
-- dies with:
--     sqlite3.OperationalError: no such column: origin
-- v5 section 4 creates the indexes the new shape needs.


-- =============================================================================
-- 5. VIEWS
-- =============================================================================

-- Did the hint ladder actually help? Per tier: how often the student's next
-- attempt was correct.
CREATE VIEW IF NOT EXISTS v_hint_effectiveness AS
SELECT
    help_type,
    tier,
    initiated_by,
    COUNT(*)                                          AS requests,
    SUM(CASE WHEN resolved_correct = 1 THEN 1 ELSE 0 END) AS resolved_correct,
    ROUND(
        100.0 * SUM(CASE WHEN resolved_correct = 1 THEN 1 ELSE 0 END)
        / NULLIF(SUM(CASE WHEN resolved_correct IS NOT NULL THEN 1 ELSE 0 END), 0),
        1
    )                                                 AS success_pct
FROM hint_requests
GROUP BY help_type, tier, initiated_by;


-- Generated questions waiting for a human to read them.
--
-- The view that used to live here joined `lessons` and `lesson_steps` and
-- selected `questions.origin` — all three gone in schema_v5. It is recreated
-- in v5 section 7 against the new shape, where a generated question is
-- grounded in a page of a real book.
--
-- It had to move rather than stay: this file runs before v5 on every boot, so
-- a view here would be rebuilt against tables v5 has already dropped.
-- `app/routers/learning.py` queries `v_questions_for_review` directly, so the
-- two edits are a pair — removing this without v5 present leaves the next
-- database built from scratch with no such view and that endpoint 500s.


-- =============================================================================
-- 6. RENAME: righty -> souly
--
-- `chat_messages.role` was created with CHECK (role IN ('student','righty',
-- 'system')). The tutor now writes 'souly', which that constraint rejects —
-- every reply would fail to insert.
--
-- SQLite cannot ALTER a CHECK constraint, so the table has to be rebuilt. The
-- sequence below is deliberately re-runnable: on a second pass the "_new"
-- table is recreated empty, the (already-migrated) rows are copied into it,
-- and the swap happens again harmlessly.
-- =============================================================================

CREATE TABLE IF NOT EXISTS chat_messages_new (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    student_id      INTEGER NOT NULL REFERENCES students(id) ON DELETE CASCADE,
    session_id      INTEGER REFERENCES sessions(id) ON DELETE SET NULL,
    -- No REFERENCES clause. This file runs before schema_v5 on every boot, so
    -- on a database built from scratch curriculum_pages does not exist yet,
    -- and SQLite resolves a foreign key when it PREPARES a statement against
    -- the table — not when a non-NULL value is written. The insert below
    -- would fail with "no such table: main.curriculum_pages".
    page_id         INTEGER,
    role            TEXT    NOT NULL CHECK (role IN ('student','souly','system')),
    content         TEXT    NOT NULL,
    input_mode      TEXT    NOT NULL DEFAULT 'text'
                        CHECK (input_mode IN ('text','voice','quick_action','help_button')),
    help_type       TEXT,
    engine          TEXT,
    stt_confidence  REAL,
    latency_ms      INTEGER,
    source_refs     TEXT,
    created_at      TEXT    NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ','now'))
);

INSERT INTO chat_messages_new (
    id, student_id, session_id, page_id, role, content,
    input_mode, help_type, engine, stt_confidence, latency_ms,
    source_refs, created_at
)
SELECT
    id, student_id, session_id, page_id,
    -- the actual rename
    CASE role WHEN 'righty' THEN 'souly' ELSE role END,
    content,
    CASE WHEN input_mode IN ('text','voice','quick_action','help_button')
         THEN input_mode ELSE 'text' END,
    help_type, engine, stt_confidence, latency_ms, source_refs, created_at
FROM chat_messages;

DROP TABLE chat_messages;
ALTER TABLE chat_messages_new RENAME TO chat_messages;

CREATE INDEX IF NOT EXISTS idx_chat_student ON chat_messages(student_id, created_at DESC);
CREATE INDEX IF NOT EXISTS idx_chat_step    ON chat_messages(page_id);


-- Badge copy referenced the old name.
UPDATE badges SET description = REPLACE(description, 'Righty', 'Souly')
WHERE description LIKE '%Righty%';
UPDATE rewards SET name        = REPLACE(name, 'Righty', 'Souly'),
                   description = REPLACE(description, 'Righty', 'Souly')
WHERE name LIKE '%Righty%' OR description LIKE '%Righty%';
