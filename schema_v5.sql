-- =============================================================================
-- schema_v5 — the real curriculum
--
-- This migration deletes the invented curriculum and replaces it with a map
-- into the actual Ministry PDFs.
--
-- The thing being removed is `lesson_steps`: a fixed sequence of pre-written
-- explanation screens, identical for every child. That was the objection —
-- a sequence every child walks the same way is not worth presenting. It is
-- also the wrong place to put content now that the books are real: the
-- explanation is written per child, at study time, from the page.
--
-- What replaces it is deliberately thin:
--
--     curriculum_books   which PDF, what subject, which grade
--     curriculum_pages   lesson -> page.  No content. Ever.
--
-- `curriculum_pages` holds no text on purpose. The book is the source of
-- truth and it stays a PDF on disk; copying its prose into SQLite would
-- create a second version that silently drifts. At study time the page is
-- read from the PDF, handed to the model as grounding, and the explanation
-- is generated for THIS child. Two children on the same page get the same
-- facts and different lessons.
--
-- Re-runnable, like every schema file here.
-- =============================================================================


-- -----------------------------------------------------------------------------
-- 1. Remove the invented curriculum
--
-- Order matters: children before parents, or the foreign keys complain.
--
-- `topics` and `subjects` SURVIVE. They carry no content — just names — and
-- mastery, attempts, sessions, games, activity_log, weekly_goals and two
-- views all hang off topics.id. Dropping them would mean rewriting the whole
-- progress spine to delete some rows of invented text. The topics table is
-- instead re-pointed at the real book: one topic per lesson in the PDF,
-- created by scripts/ingest_curriculum.py.
-- -----------------------------------------------------------------------------

DROP VIEW  IF EXISTS v_questions_for_review;

DROP TABLE IF EXISTS step_activity;
DROP TABLE IF EXISTS step_explanations;
DROP TABLE IF EXISTS quiz_questions;
DROP TABLE IF EXISTS lesson_progress;
DROP TABLE IF EXISTS lesson_steps;
DROP TABLE IF EXISTS lessons;

-- Everything the invented content seeded. Questions are generated from the
-- page now, so the table is rebuilt below without its lesson_steps ancestry.
DROP TABLE IF EXISTS questions;


-- -----------------------------------------------------------------------------
-- 2. The books
-- -----------------------------------------------------------------------------

CREATE TABLE IF NOT EXISTS curriculum_books (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    code            TEXT    NOT NULL UNIQUE,   -- 'math-p5-t1'
    title           TEXT    NOT NULL,
    subject         TEXT    NOT NULL,          -- 'Mathematics'
    subject_code    TEXT,                      -- links to subjects.code when one exists

    -- Grade gating. A Primary 5 book must not appear for a Primary 6 child,
    -- and right now Primary 6 has no books at all — that has to read as an
    -- honest empty state, not as a broken screen.
    grade           TEXT    NOT NULL,          -- '5'
    term            TEXT,                      -- '1'
    language        TEXT    NOT NULL DEFAULT 'en',

    -- Where the PDF actually lives, relative to the curriculum directory.
    -- Nothing is copied into the database.
    filename        TEXT    NOT NULL,
    page_count      INTEGER,
    sha256          TEXT,                      -- detects a swapped-out file

    -- Only Ministry material a human has eyeballed gets taught from. The
    -- agent refuses unverified sources; this is the same gate as before.
    is_verified     INTEGER NOT NULL DEFAULT 0,
    source_note     TEXT,

    added_at        TEXT    NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ','now')),
    updated_at      TEXT    NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ','now'))
);

CREATE INDEX IF NOT EXISTS idx_books_grade
    ON curriculum_books(grade, subject);


-- -----------------------------------------------------------------------------
-- 3. The page map
--
-- Exactly two columns of substance: which lesson, which page. One row per
-- page. A lesson spanning pages 12-15 is four rows.
--
-- One row per page rather than a first/last range because real books do not
-- behave: a lesson can be interrupted by a full-page illustration, an
-- activity spread, or a revision section, and a range would swallow them.
-- Listing pages individually lets a lesson be non-contiguous without any
-- special case.
-- -----------------------------------------------------------------------------

CREATE TABLE IF NOT EXISTS curriculum_pages (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    book_id     INTEGER NOT NULL REFERENCES curriculum_books(id) ON DELETE CASCADE,

    lesson      TEXT    NOT NULL,   -- 'Lesson 3: Comparing Fractions'
    page        INTEGER NOT NULL,   -- 1-based, as printed in the PDF

    -- Ordering hint for the lessons themselves, so the plan strip can show
    -- them in book order rather than alphabetically.
    lesson_order INTEGER NOT NULL DEFAULT 0,

    -- Optional grouping above the lesson, when the book has one.
    unit        TEXT,

    UNIQUE (book_id, page)
);

CREATE INDEX IF NOT EXISTS idx_pages_lesson
    ON curriculum_pages(book_id, lesson_order, page);


-- -----------------------------------------------------------------------------
-- 4. Questions, rebuilt
--
-- Same job as before, minus the ancestry: a question no longer belongs to an
-- invented lesson_step. It belongs to a topic and, when the model wrote it,
-- to the page it was grounded in.
--
-- The guardrail columns stay. Bastani et al. (PNAS 2025) measured an AI tutor
-- that answered freely: +48% during practice, -17% against control on a later
-- unaided exam. The worked solution and the common wrong answers live here so
-- the hint prompt can be built without ever handing over the answer.
-- -----------------------------------------------------------------------------

CREATE TABLE IF NOT EXISTS questions (
    id                  INTEGER PRIMARY KEY AUTOINCREMENT,
    topic_id            INTEGER REFERENCES topics(id) ON DELETE CASCADE,

    -- Where it came from, so a wrong question can be traced to a page.
    book_id             INTEGER REFERENCES curriculum_books(id) ON DELETE SET NULL,
    source_page         INTEGER,

    prompt              TEXT    NOT NULL,
    options_json        TEXT    NOT NULL,
    correct_index       INTEGER NOT NULL,
    explanation         TEXT,

    -- `hint` is not in the handover's draft of this table and its absence was
    -- an oversight: tiers 1-3 of the hint ladder fall back to it whenever the
    -- model is unreachable, which on a competition-day MiFi is not a rare
    -- case. Without it a child who taps "I'm stuck" offline gets nothing.
    hint                TEXT,

    worked_solution     TEXT,
    common_wrong_answers TEXT,

    difficulty          INTEGER NOT NULL DEFAULT 2,
    engine              TEXT    NOT NULL DEFAULT 'fallback',

    -- Generated questions are not shown until they pass validation.
    review_status       TEXT    NOT NULL DEFAULT 'pending'
                        CHECK (review_status IN ('pending','approved','rejected')),

    -- Per-child questions: a question written for one student is not reused
    -- for another. NULL means it is general to the topic.
    student_id          INTEGER REFERENCES students(id) ON DELETE CASCADE,

    created_at          TEXT    NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ','now'))
);

CREATE INDEX IF NOT EXISTS idx_questions_topic
    ON questions(topic_id, review_status);
CREATE INDEX IF NOT EXISTS idx_questions_student
    ON questions(student_id, created_at);


-- -----------------------------------------------------------------------------
-- 5. Link topics to the book
--
-- A topic is now a lesson in a real book, not something invented. These
-- columns let ingest_curriculum.py keep them in step, and let the tutor find
-- the pages it must read before explaining anything.
-- -----------------------------------------------------------------------------

ALTER TABLE topics ADD COLUMN book_id INTEGER REFERENCES curriculum_books(id);
ALTER TABLE topics ADD COLUMN lesson_label TEXT;


-- -----------------------------------------------------------------------------
-- 6. The lesson plan the interface reads
--
-- "the plan of lessons ahead" — one row per lesson, with its page span and
-- how many pages it runs to, in book order.
-- -----------------------------------------------------------------------------

DROP VIEW IF EXISTS v_curriculum_lessons;
CREATE VIEW v_curriculum_lessons AS
SELECT
    b.id            AS book_id,
    b.code          AS book_code,
    b.title         AS book_title,
    b.subject       AS subject,
    b.grade         AS grade,
    b.term          AS term,
    b.is_verified   AS is_verified,
    p.unit          AS unit,
    p.lesson        AS lesson,
    MIN(p.lesson_order) AS lesson_order,
    MIN(p.page)     AS first_page,
    MAX(p.page)     AS last_page,
    COUNT(*)        AS page_count
FROM curriculum_pages p
JOIN curriculum_books b ON b.id = p.book_id
GROUP BY b.id, p.unit, p.lesson
ORDER BY b.id, MIN(p.lesson_order), MIN(p.page);


-- -----------------------------------------------------------------------------
-- 7. Generated questions waiting for a human
--
-- Replaces the schema_v3 view of the same name, which joined `lessons` and
-- `lesson_steps`. A generated question is now grounded in a page of a real
-- book, so that is what the reviewer needs to see next to it.
-- -----------------------------------------------------------------------------

DROP VIEW IF EXISTS v_questions_for_review;
CREATE VIEW v_questions_for_review AS
SELECT
    q.id, q.prompt, q.options_json, q.correct_index, q.explanation,
    q.worked_solution, q.difficulty, q.engine, q.created_at,
    q.source_page,
    t.title AS topic_title,
    b.title AS book_title,
    b.subject AS subject,
    b.grade AS grade,
    s.display_name AS written_for
FROM questions q
LEFT JOIN topics t            ON t.id = q.topic_id
LEFT JOIN curriculum_books b  ON b.id = q.book_id
LEFT JOIN students s          ON s.id = q.student_id
WHERE q.review_status = 'pending'
ORDER BY q.created_at DESC;


-- =============================================================================
-- 8. The progress spine, re-pointed at pages
--
-- Sections 1-7 above are the curriculum. This section is the consequence of
-- them: section 1 drops four tables that the rest of the app writes to on
-- every lesson screen, and an app that boots and 500s is not a migration.
--
-- Each one comes back with the same job and a page-shaped key:
--
--     quiz_questions   unchanged — it only died because it FKs `questions`
--     lesson_progress  keyed on topics.id, because a topic IS a lesson now
--     page_activity    was step_activity; a step is a page
--     page_renditions  was step_explanations; a rendition is per CHILD
--
-- The mapping that makes this work: ingest_curriculum.py writes one `topics`
-- row per lesson in the book, so `topic_id` is the lesson's identity and
-- `curriculum_pages` rows are its steps. Nothing else in the progress spine
-- — mastery, attempts, sessions, games, activity_log, weekly_goals — has to
-- change at all, because all of it already hangs off topics.id.
-- =============================================================================


-- -----------------------------------------------------------------------------
-- 8a. The quiz's question list
--
-- Identical to the schema_v2 table. It was dropped in section 1 only because
-- it holds a foreign key into `questions`, which is rebuilt above.
-- -----------------------------------------------------------------------------

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


-- -----------------------------------------------------------------------------
-- 8b. How far through a lesson a child is
--
-- Keyed on topic_id rather than the old lessons.id. Pages, not steps: a
-- lesson of four pages is complete when the child has worked all four.
--
-- `last_page` is the printed page number, not an ordinal, so "carry on where
-- you left off" survives the lesson map being re-ingested with an extra page
-- in the middle.
-- -----------------------------------------------------------------------------

CREATE TABLE IF NOT EXISTS lesson_progress (
    id                  INTEGER PRIMARY KEY AUTOINCREMENT,
    student_id          INTEGER NOT NULL REFERENCES students(id) ON DELETE CASCADE,
    topic_id            INTEGER NOT NULL REFERENCES topics(id) ON DELETE CASCADE,

    pages_completed     INTEGER NOT NULL DEFAULT 0,
    last_page           INTEGER NOT NULL DEFAULT 0,
    is_complete         INTEGER NOT NULL DEFAULT 0 CHECK (is_complete IN (0,1)),

    started_at          TEXT,
    completed_at        TEXT,
    updated_at          TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ','now')),

    UNIQUE (student_id, topic_id)
);

CREATE INDEX IF NOT EXISTS idx_lesson_progress_student
    ON lesson_progress(student_id, updated_at DESC);


-- -----------------------------------------------------------------------------
-- 8c. Time spent on a page
--
-- Was `step_activity`. This is the table `tutor.stall_threshold()` reads to
-- work out what "stuck" means for THIS child rather than applying a global
-- constant — which matters because Zapparrata et al. (2023) found autistic
-- people are slower across the board (g = .35), so a fixed threshold would
-- mark the entire target cohort as permanently stalled.
-- -----------------------------------------------------------------------------

CREATE TABLE IF NOT EXISTS page_activity (
    id                  INTEGER PRIMARY KEY AUTOINCREMENT,
    student_id          INTEGER NOT NULL REFERENCES students(id) ON DELETE CASCADE,
    page_id             INTEGER NOT NULL REFERENCES curriculum_pages(id) ON DELETE CASCADE,
    seconds_on_page     INTEGER NOT NULL DEFAULT 0,
    help_requests       INTEGER NOT NULL DEFAULT 0,
    replayed_audio      INTEGER NOT NULL DEFAULT 0,
    went_back           INTEGER NOT NULL DEFAULT 0,
    created_at          TEXT    NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ','now'))
);

CREATE INDEX IF NOT EXISTS idx_page_activity_student
    ON page_activity(student_id, page_id);


-- -----------------------------------------------------------------------------
-- 8d. The rendition cache
--
-- Was `step_explanations`, and the key is the difference. The old table
-- cached per (step, mode, support_profile): every autistic child in the class
-- read the identical rewrite. A rendition is per CHILD — that is the whole
-- point of the layer — so the cache is keyed on student_id.
--
-- It is a cache and nothing more. The canon is the PDF page; delete every row
-- here and the app regenerates them from the book. That is also why the
-- source hash is stored: swap the PDF and the stale renditions are detectable
-- rather than silently wrong.
--
-- `mode = 'lesson'` is the main explanation the child reads. The other three
-- are the "I don't get this" buttons.
-- -----------------------------------------------------------------------------

CREATE TABLE IF NOT EXISTS page_renditions (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    student_id      INTEGER NOT NULL REFERENCES students(id) ON DELETE CASCADE,
    page_id         INTEGER NOT NULL REFERENCES curriculum_pages(id) ON DELETE CASCADE,
    mode            TEXT    NOT NULL DEFAULT 'lesson'
                        CHECK (mode IN ('lesson','simpler','example','another_way')),

    body            TEXT    NOT NULL,
    engine          TEXT,
    -- The book's sha256 at the time this was written, so a swapped PDF
    -- invalidates the cache instead of teaching from a page that moved.
    source_sha      TEXT,

    created_at      TEXT    NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ','now')),

    UNIQUE (student_id, page_id, mode)
);

CREATE INDEX IF NOT EXISTS idx_renditions_lookup
    ON page_renditions(student_id, page_id, mode);


-- -----------------------------------------------------------------------------
-- 8e. Anchor help requests to a page
--
-- `hint_requests` was created by schema_v3 with `lesson_step_id INTEGER
-- REFERENCES lesson_steps(id)`. Section 1 above drops lesson_steps, and a
-- dead foreign key is NOT harmless: SQLite resolves every foreign key on a
-- table when it PREPARES a statement against it, so with the parent table
-- gone, EVERY insert into hint_requests fails with
--
--     sqlite3.OperationalError: no such table: main.lesson_steps
--
-- — including inserts that leave the column NULL. Which is every insert, in
-- the rewritten tutor. So the table has to be rebuilt rather than altered.
--
-- schema_v3 now creates the new shape directly, so a database built from
-- scratch never has the dead column. This block is what fixes the databases
-- that already exist. It is re-runnable in the same way schema_v3's
-- chat_messages rebuild is: on a second pass the "_new" table is created
-- empty, the already-migrated rows are copied into it, and the swap happens
-- again harmlessly.
-- -----------------------------------------------------------------------------

-- On a database that already exists, schema_v3's CREATE TABLE IF NOT EXISTS
-- does nothing, so hint_requests is still the OLD shape and has no page_id at
-- all — the copy below would fail with "no such column: page_id". This ALTER
-- gives it one. app/db.py hoists every ADD COLUMN to the top of the file, so
-- it lands before the rebuild; on a fresh database schema_v3 has already
-- created the column and the duplicate-column error is swallowed.
--
-- The old lesson_step_id values are deliberately not carried across. They
-- point at rows of lesson_steps, which section 1 deleted.
ALTER TABLE hint_requests ADD COLUMN page_id INTEGER;

-- `v_hint_effectiveness` (schema_v3) reads hint_requests. ALTER TABLE ...
-- RENAME re-parses the whole schema, views included, so a view left standing
-- over a table that is mid-rebuild aborts the migration with
--     error in view v_hint_effectiveness: no such table: main.hint_requests
-- Drop it, rebuild, put it back.
DROP VIEW IF EXISTS v_hint_effectiveness;

CREATE TABLE IF NOT EXISTS hint_requests_new (
    id                  INTEGER PRIMARY KEY AUTOINCREMENT,
    student_id          INTEGER NOT NULL REFERENCES students(id) ON DELETE CASCADE,

    page_id             INTEGER,
    question_id         INTEGER REFERENCES questions(id) ON DELETE SET NULL,
    quiz_id             INTEGER REFERENCES quizzes(id) ON DELETE SET NULL,

    help_type           TEXT    NOT NULL
                            CHECK (help_type IN (
                                'simpler', 'example', 'another_way',
                                'nudge', 'worked', 'stepwise', 'answer',
                                'free_question'
                            )),
    tier                INTEGER CHECK (tier IS NULL OR tier BETWEEN 1 AND 4),

    initiated_by        TEXT    NOT NULL DEFAULT 'student'
                            CHECK (initiated_by IN ('student', 'souly')),

    attempts_before     INTEGER NOT NULL DEFAULT 0,
    seconds_before      INTEGER NOT NULL DEFAULT 0,
    student_answer      TEXT,

    response_text       TEXT,
    engine              TEXT,
    latency_ms          INTEGER,

    resolved_correct    INTEGER CHECK (resolved_correct IN (0, 1)),

    created_at          TEXT    NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ','now'))
);

INSERT INTO hint_requests_new (
    id, student_id, page_id, question_id, quiz_id, help_type, tier,
    initiated_by, attempts_before, seconds_before, student_answer,
    response_text, engine, latency_ms, resolved_correct, created_at
)
SELECT
    id, student_id, page_id, question_id, quiz_id, help_type, tier,
    initiated_by, attempts_before, seconds_before, student_answer,
    response_text, engine, latency_ms, resolved_correct, created_at
FROM hint_requests;

DROP TABLE hint_requests;
ALTER TABLE hint_requests_new RENAME TO hint_requests;

CREATE INDEX IF NOT EXISTS idx_hint_requests_student  ON hint_requests(student_id, created_at DESC);
CREATE INDEX IF NOT EXISTS idx_hint_requests_question ON hint_requests(question_id, tier);
CREATE INDEX IF NOT EXISTS idx_hint_requests_page     ON hint_requests(page_id);

-- Did the hint ladder actually help? Per tier: how often the student's next
-- attempt was correct. Same view schema_v3 defines; recreated here because
-- the rebuild above had to drop it.
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
