# Adding Your Own Curriculum

**Read this before the competition.** The lesson content shipped in
`scripts/seed_content.py` is factually correct and grade-appropriate, but it
is **not your school's syllabus**. Someone who knows the syllabus needs to
review or replace it.

---

## The verification rule

Righty refuses to teach from anything marked `is_verified = 0`.

```
topics.is_verified   -- is this topic approved?
lessons.is_verified  -- is this lesson's content approved?
questions.is_verified -- is this question approved?
```

Retrieval filters on these flags (`app/services/rag.py`), so unverified
material is invisible to the agent — it cannot quote it, cannot build a
question from it, and will say "I haven't learned about that yet" instead.

**Setting the flag is a human act.** Right now that act was performed by the
seed script, which means it reflects my judgement, not your teachers'. That
is fine for building and demoing. It is not fine for a competition where a
judge may ask "who checked this content?"

A test enforces the filtering behaviour:
`tests/test_learning.py::test_rag_excludes_unverified_content`.

---

## The data model

```
subjects  (Mathematics, Science, …)
   └── topics       (Introduction to Fractions)
         └── lessons      (a teachable unit)
               └── lesson_steps   ← THIS is what Righty reads and RAG indexes
               └── questions      ← the verified question bank
```

`lesson_steps.body` is the important field. It is:

- what the student reads on screen,
- what the text-to-speech engine reads aloud,
- what retrieval searches,
- what gets fed to Gemini as the only material it may teach from.

So write it as **prose a child can follow when spoken out loud**. Not bullet
points, not notes, not an outline. Full sentences.

---

## Option A — edit the seed script (easiest)

Open `scripts/seed_content.py` and edit the `CURRICULUM` list:

```python
{
    "subject": "MATH",                          # must match a SUBJECTS code
    "topic_code": "MATH.FRACTIONS.INTRO",       # unique, dotted
    "topic_title": "Introduction to Fractions",
    "lesson_code": "L.MATH.FRAC.1",             # unique
    "lesson_title": "Introduction to Fractions",
    "lesson_subtitle": "Math Lesson 3",
    "icon": "🔢",
    "steps": [
        ("Heading", "Full-sentence body text that reads well aloud.", "🍕"),
        # …3-6 steps per lesson works well
    ],
    "questions": [
        ("The question text?",
         ["correct answer", "wrong", "wrong", "wrong"],   # options
         0,                                               # index of correct
         "Explanation read aloud after answering.",
         "A hint shown if they ask.",
         2),                                              # difficulty 1-5
    ],
},
```

Then:

```bash
python scripts/seed_content.py --wipe
```

`--wipe` clears content tables and re-seeds. **It also clears student progress**
(activity log, quiz history, badges). Fine while building; don't run it an hour
before the demo.

---

## Option B — add it to the database directly

Useful when a teacher has material in a document and you want to import it.

```python
from app.db import get_conn
from app.models import utc_now_iso

with get_conn() as conn:
    subject_id = conn.execute(
        "SELECT id FROM subjects WHERE code = 'SCI'").fetchone()["id"]

    topic_id = conn.execute(
        "INSERT INTO topics (code, subject, subject_id, title, is_verified, created_at) "
        "VALUES (?,?,?,?,0,?)",                      # note is_verified = 0
        ('SCI.GRAVITY', 'SCI', subject_id, 'Gravity', utc_now_iso())
    ).lastrowid

    lesson_id = conn.execute(
        "INSERT INTO lessons (topic_id, code, title, is_verified, created_at) "
        "VALUES (?,?,?,0,?)",
        (topic_id, 'L.SCI.GRAV.1', 'What is Gravity?', utc_now_iso())
    ).lastrowid

    conn.execute(
        "INSERT INTO lesson_steps (lesson_id, step_no, heading, body, visual) "
        "VALUES (?,1,?,?,?)",
        (lesson_id, 'Gravity pulls things down',
         'Gravity is a force that pulls objects towards each other. '
         'The Earth is very big, so it pulls everything towards its centre. '
         'That is why a ball falls when you drop it.',
         '🌍')
    )
```

Insert with `is_verified = 0`, have a teacher read it, then flip the flag:

```sql
UPDATE topics  SET is_verified = 1 WHERE code = 'SCI.GRAVITY';
UPDATE lessons SET is_verified = 1 WHERE code = 'L.SCI.GRAV.1';
```

---

## Writing content that works for these students

The audience is children who may have autism, ADHD, dyslexia, or a hearing or
speech impairment. The content itself carries a lot of the accessibility load,
more than the interface does.

- **One idea per sentence.** Two clauses joined by "and" is two sentences.
- **Short, common words.** If a word is unavoidable, define it in the next
  sentence.
- **No idioms, no sarcasm, no rhetorical questions.** A literal reader will
  take "a piece of cake" literally, and a rhetorical question invites an answer
  that never comes.
- **Concrete before abstract.** Pizza slices before numerators.
- **3–6 steps per lesson.** More than six and a student with ADHD is gone
  before the end.
- **Read it out loud before you commit it.** It will be spoken by a speaker,
  so if it sounds wrong when you say it, it is wrong.

For questions specifically:

- Make wrong options *plausible*, not silly. A student learns nothing from
  eliminating "8/5" when the real confusion is between 3/8 and 5/8.
- The explanation should teach, not just confirm. "5/8 — because 8 slices
  minus the 3 she ate leaves 5" beats "Correct!"
- Keep difficulty honest. The quiz picks questions near the student's current
  mastery, so a mislabelled difficulty puts the wrong question in front of a
  struggling child.

---

## Checking your work

```bash
curl -s localhost:8000/health | python -m json.tool
```

```json
"curriculum": {
    "lesson_steps": 32,
    "lessons": 9,
    "verified_lessons": 9,     ← retrieval only sees these
    "topics": 11,
    "verified_topics": 9,
    "ready": true
}
```

If `verified_lessons` is 0, Righty will politely refuse every question — which
looks like a broken AI but is actually the safety mechanism doing its job.

Then ask Righty something from your new material on the **Ask Righty** screen
and check the reply cites the right topic underneath it.

---

## What about ChromaDB?

The roadmap lists ChromaDB + sentence-transformers for RAG. The current
implementation uses TF-IDF scoring over the same `lesson_steps` table
(`app/services/rag.py`), which needs no extra dependency, no model download,
and no network — and for a curriculum of a few hundred steps it is both fast
and completely predictable.

If you later want semantic search (matching "how do I share a pizza fairly"
to a fractions lesson that never uses those words), swap the body of
`search()`. The `Chunk` dataclass it returns is the contract the rest of the
system depends on; keep that shape and nothing else changes.
