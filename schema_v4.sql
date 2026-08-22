-- =============================================================================
-- Souly — Schema v4: who is using the tablet, and how they learn
--
-- Runs after schema_v3.sql. Re-runnable.
--
--   1. Avatars + picture passwords  — the Netflix-style sign-in
--   2. Auth tokens                  — so the app knows who it's talking to
--   3. learner_profiles             — the output of the entry activity
--   4. onboarding_responses         — the raw evidence behind the profile
-- =============================================================================

PRAGMA foreign_keys = ON;


-- =============================================================================
-- 1. SIGN-IN
-- =============================================================================

-- The picker tile.
ALTER TABLE students ADD COLUMN avatar TEXT NOT NULL DEFAULT '🙂';
ALTER TABLE students ADD COLUMN avatar_color TEXT NOT NULL DEFAULT '#7C3AED';

-- -----------------------------------------------------------------------------
-- Picture password.
--
-- The child picks three pictures in order from a grid of twelve, code.org
-- style. That is 12 x 11 x 10 = 1,320 combinations.
--
-- BE CLEAR ABOUT WHAT THIS PROTECTS AGAINST. 1,320 is trivial to brute force
-- by machine. It is not trivial for a nine-year-old classmate guessing at a
-- shared tablet, and that is the actual threat: a child opening a friend's
-- profile and messing with their stars, or reading their progress.
--
-- It is deliberately NOT good enough for the parent portal, which holds a
-- child's assessment data and keeps its own PBKDF2 access code (see
-- app/security.py). Do not reuse this mechanism there.
--
-- Stored as a PBKDF2 hash of the sequence, never the sequence itself, so the
-- database travelling on a laptop to a competition doesn't hand over every
-- child's login.
-- -----------------------------------------------------------------------------
ALTER TABLE students ADD COLUMN picture_password_hash TEXT;
ALTER TABLE students ADD COLUMN password_set_at TEXT;

-- Rate limiting. Five wrong tries locks the tile for a few minutes — enough to
-- stop a determined classmate, gentle enough that a child who mis-taps isn't
-- shut out of their lesson.
ALTER TABLE students ADD COLUMN failed_logins INTEGER NOT NULL DEFAULT 0;
ALTER TABLE students ADD COLUMN locked_until TEXT;
ALTER TABLE students ADD COLUMN last_login_at TEXT;

-- Has this student finished the getting-to-know-you activity?
ALTER TABLE students ADD COLUMN onboarded_at TEXT;


-- -----------------------------------------------------------------------------
-- auth_tokens
-- One row per signed-in device. Deleted on sign-out, expired by time.
-- -----------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS auth_tokens (
    token           TEXT    PRIMARY KEY,
    student_id      INTEGER NOT NULL REFERENCES students(id) ON DELETE CASCADE,
    created_at      TEXT    NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ','now')),
    expires_at      TEXT    NOT NULL,
    device_label    TEXT
);

CREATE INDEX IF NOT EXISTS idx_auth_tokens_student ON auth_tokens(student_id);


-- =============================================================================
-- 2. THE LEARNER PROFILE
--
-- What the entry activity produces, and what the tutor reads before deciding
-- how to pitch an explanation.
--
-- WHAT THIS IS NOT: a learning style. There is no visual/auditory/kinaesthetic
-- field here and there must never be one. Pashler et al. (2008) went looking
-- for studies that could validate style-matching and found essentially none;
-- the most favourable recent meta-analysis still concludes the effect is "too
-- small and too infrequent to warrant widespread adoption". 89% of teachers
-- believe it anyway, so the temptation to add the column will recur. Don't.
--
-- WHAT IT IS: a measurement of how much help this child needed, taken by
-- watching them work. That is Dynamic Assessment by graduated prompts
-- (Resing/Vogelaar/Veerbeek at Leiden), and the one aptitude-treatment
-- interaction that has actually replicated is prior knowledge x amount of
-- scaffolding (Kalyuga's expertise reversal effect, adaptive d = 0.46).
-- =============================================================================

CREATE TABLE IF NOT EXISTS learner_profiles (
    id                      INTEGER PRIMARY KEY AUTOINCREMENT,
    student_id              INTEGER NOT NULL REFERENCES students(id) ON DELETE CASCADE,

    -- ---- The headline: how much scaffolding does this child need? ----------
    -- Veerbeek & Vogelaar (2025) sorted children into exactly these three
    -- buckets from a 15-20 minute graduated-prompts session, and the
    -- task_specific group scored significantly lower on standardised maths
    -- and reading — the profile tracks something real.
    --
    --   low            solves with few or no prompts. Give LESS support:
    --                  extra scaffolding actively harms competent learners.
    --   metacognitive  needs "what's changing?" nudges, rarely needs the
    --                  content re-taught. Scaffold strategy, not facts.
    --   task_specific  needs the concept itself re-explained before the
    --                  question makes sense. Start at worked examples.
    instruction_need        TEXT    NOT NULL DEFAULT 'metacognitive'
                                CHECK (instruction_need IN
                                       ('low', 'metacognitive', 'task_specific')),

    -- How much to trust the above. Deliberately low after one session.
    --
    -- Courchesne et al. (2015) tested 30 minimally-verbal autistic children:
    -- ZERO completed a standard WISC-IV. Under a strength-informed protocol
    -- averaging 3.8 short sessions, 26 of 30 completed and most scored far
    -- higher. Day-one numbers underestimate this population badly, so the
    -- profile starts provisional and is corrected from live tutoring.
    confidence              REAL    NOT NULL DEFAULT 0.3
                                CHECK (confidence >= 0.0 AND confidence <= 1.0),

    -- ---- Evidence behind the headline --------------------------------------
    mean_prompts_needed     REAL,       -- 0 = solved unaided, 4 = needed modelling
    rung1_sufficient_rate   REAL,       -- how often the gentlest nudge was enough
    items_attempted         INTEGER NOT NULL DEFAULT 0,
    items_solved_unaided    INTEGER NOT NULL DEFAULT 0,

    -- ---- Pacing ------------------------------------------------------------
    -- Seeds the stall detector on day one instead of guessing for four steps.
    -- Autistic people are measurably slower across the board (Zapparrata 2023,
    -- g = .35), so this must be per child, never a global constant.
    median_first_attempt_ms INTEGER,
    latency_variability     REAL,       -- SD/median; high = inconsistent
    gives_up_early          INTEGER NOT NULL DEFAULT 0 CHECK (gives_up_early IN (0,1)),

    -- ---- Reading vs listening ----------------------------------------------
    -- The Simple View of Reading: comprehension = decoding x language
    -- comprehension. The dissociation is real and large (Foorman 2018 explains
    -- 68-78% of variance in grades 1-3). A child who understands the audio
    -- item and not the written one has a DECODING problem, and read-aloud is
    -- the evidenced accommodation (Wood 2018, d = 0.35) — not a "learning
    -- style". Positive = listening beat reading.
    modality_gap            REAL,
    reading_correct         INTEGER,
    listening_correct       INTEGER,
    reading_time_ms         INTEGER,

    -- ---- Interests ----------------------------------------------------------
    -- JSON array. Embedding a child's focused interest in instruction is the
    -- best-evidenced motivation lever for this group: Gunn & Delafield-Butt
    -- (2016) reviewed 20 studies and ALL 20 showed engagement gains.
    -- Embedded in content, never used as a reward for compliance.
    interests               TEXT,

    -- ---- Flags ---------------------------------------------------------------
    -- Fast, flawless, but hesitant. Day-one error runs in both directions:
    -- anxiety pushes scores down, masking pushes them up. A child flagged here
    -- should not immediately be pitched at the hardest level.
    possible_masking        INTEGER NOT NULL DEFAULT 0 CHECK (possible_masking IN (0,1)),
    -- Bailed out of the activity. Score as missing, never as failure.
    incomplete              INTEGER NOT NULL DEFAULT 0 CHECK (incomplete IN (0,1)),

    source                  TEXT    NOT NULL DEFAULT 'onboarding'
                                CHECK (source IN ('onboarding', 'live', 'manual')),
    notes                   TEXT,
    created_at              TEXT    NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ','now'))
);

CREATE INDEX IF NOT EXISTS idx_learner_profiles_student
    ON learner_profiles(student_id, created_at DESC);


-- -----------------------------------------------------------------------------
-- onboarding_responses
-- Raw per-item evidence. Append-only.
--
-- Kept separate from the profile so the profile can be recomputed later with a
-- better scoring rule without re-testing any child.
-- -----------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS onboarding_responses (
    id                  INTEGER PRIMARY KEY AUTOINCREMENT,
    student_id          INTEGER NOT NULL REFERENCES students(id) ON DELETE CASCADE,

    item_code           TEXT    NOT NULL,
    item_kind           TEXT    NOT NULL
                            CHECK (item_kind IN ('series', 'analogy', 'reading',
                                                 'listening', 'interests',
                                                 'preferences')),

    -- 0 = solved with no help, 4 = needed the full worked model.
    -- This IS the dynamic assessment score.
    prompts_used        INTEGER NOT NULL DEFAULT 0
                            CHECK (prompts_used BETWEEN 0 AND 4),
    solved              INTEGER CHECK (solved IN (0,1)),
    skipped             INTEGER NOT NULL DEFAULT 0 CHECK (skipped IN (0,1)),

    first_attempt_ms    INTEGER,
    total_ms            INTEGER,
    attempts            INTEGER NOT NULL DEFAULT 0,
    answer              TEXT,

    created_at          TEXT    NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ','now'))
);

CREATE INDEX IF NOT EXISTS idx_onboarding_student
    ON onboarding_responses(student_id, created_at);


-- =============================================================================
-- 3. VIEW — the profile the tutor actually reads
-- =============================================================================

CREATE VIEW IF NOT EXISTS v_current_learner_profile AS
SELECT lp.*
FROM learner_profiles lp
JOIN (
    SELECT student_id, MAX(id) AS max_id
    FROM learner_profiles
    GROUP BY student_id
) latest ON latest.max_id = lp.id;


-- =============================================================================
-- 4. BACKFILL — give the existing class distinct faces
--
-- Students created before this migration inherit the DEFAULT '🙂' above, which
-- on a sign-in screen whose entire premise is "tap your own picture" means a
-- wall of identical tiles. That defeats the point for exactly the children who
-- can't fall back on reading the name underneath.
--
-- Assigned from each row's POSITION in the table, not its id and not a
-- hardcoded list of external_ids: ids have gaps once anyone is deleted, and
-- `id % 10` then hands two children the same animal. Guarded on avatar = '🙂'
-- so it only ever touches a face nobody has chosen — re-running is a no-op.
--
-- A child can still pick a different face later. This is only the starting
-- state, so no two tiles look alike on day one.
-- =============================================================================

UPDATE students
SET avatar = CASE (SELECT COUNT(*) FROM students s2 WHERE s2.id < students.id) % 10
        WHEN 0 THEN '🦊'
        WHEN 1 THEN '🐼'
        WHEN 2 THEN '🦁'
        WHEN 3 THEN '🐨'
        WHEN 4 THEN '🐧'
        WHEN 5 THEN '🦉'
        WHEN 6 THEN '🐸'
        WHEN 7 THEN '🐙'
        WHEN 8 THEN '🦄'
        ELSE '🐝'
    END,
    avatar_color = CASE (SELECT COUNT(*) FROM students s2 WHERE s2.id < students.id) % 10
        WHEN 0 THEN '#F97316'
        WHEN 1 THEN '#0EA5E9'
        WHEN 2 THEN '#EAB308'
        WHEN 3 THEN '#10B981'
        WHEN 4 THEN '#7C3AED'
        WHEN 5 THEN '#EC4899'
        WHEN 6 THEN '#14B8A6'
        WHEN 7 THEN '#EF4444'
        WHEN 8 THEN '#8B5CF6'
        ELSE '#F59E0B'
    END
WHERE avatar = '🙂';
