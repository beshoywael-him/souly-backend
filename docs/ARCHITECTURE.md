# How Souly Works

A complete walkthrough of the running system: what each piece does, how a
request travels through it, and — most usefully — **which file to open when
you want to change something.**

Read the first two sections and you'll have the mental model. The rest is
reference.

---

## 1. The one-paragraph version

Souly is **one Python program** (a FastAPI web server) that talks to **one
file** (a SQLite database, `data/souly.db`) and serves **one web page** (the
student app). The web page is plain HTML, CSS and JavaScript — no React, no
build step, no `npm install`. When you open `localhost:8000/student`, the
server sends the page; the page then calls back to the server for data and
draws itself. Three outside services get used when keys are present: Gemini
for reasoning, ElevenLabs for hearing the student, and a text-to-speech vendor
for Righty's voice.

That's the whole thing. Everything below is detail.

---

## 2. What runs where

```
┌───────────────────────────────────────────────────────────────────┐
│  THE TABLET / LAPTOP  (a browser)                                 │
│                                                                   │
│   localhost:8000/student                                          │
│     index.html   ← empty containers, no content                   │
│     base.css     ← YOUR design, untouched                         │
│     app.css      ← loading/error states + accessibility           │
│     api.js       ← every network call goes through here           │
│     pages.js     ← turns API data into HTML                       │
│     games.js     ← the two playable mini-games                    │
│     app.js       ← routing, actions, the voice loop               │
└──────────────────────────┬────────────────────────────────────────┘
                           │  HTTP (fetch)
                           ▼
┌───────────────────────────────────────────────────────────────────┐
│  THE SERVER  (uvicorn running app/main.py)                        │
│                                                                   │
│   routers/    ← 35 endpoints, the doors into the system           │
│   economy.py  ← the ONLY place stars/XP/levels change             │
│   services/   ← llm, rag, stt, tts, tutor                         │
│   db.py       ← opens SQLite                                      │
└──────────┬────────────────────────────────┬───────────────────────┘
           │                                │
           ▼                                ▼
   ┌───────────────┐              ┌──────────────────────┐
   │ data/souly.db │              │  OUTSIDE SERVICES    │
   │  31 tables    │              │   Gemini   (brain)   │
   │  one file     │              │   ElevenLabs (ears)  │
   └───────────────┘              │   TTS vendor (voice) │
                                  └──────────────────────┘
```

**Key point:** the browser holds no truth. It draws whatever the server sends
and immediately forgets. Refresh the page and nothing is lost, because nothing
was ever only in the browser. That's why the same student can pick up on the
robot tablet, the classroom screen, or a parent's phone and see identical
state.

---

## 3. What happens when you start it

```bash
uvicorn app.main:app --reload
```

1. Python imports `app/main.py`.
2. `app/config.py` reads your `.env` file into a `settings` object. **This is
   the only file in the project that reads environment variables** — so when
   you pick a TTS vendor, this is the only place that knows.
3. The `lifespan` function runs `init_db()`, which executes `schema.sql` then
   `schema_v2.sql`. Both are written with `IF NOT EXISTS`, so running them
   against an existing database does nothing. This is why nobody on your team
   can ever hit "no such table" for forgetting a setup step.
4. All the routers are registered — 35 endpoints.
5. `static/` is mounted so the browser can fetch the CSS and JS.
6. The server listens on port 8000.

Nothing else happens until a browser connects.

---

## 4. What happens when you open the page

This is the trace worth understanding, because every other screen works the
same way.

```
You type localhost:8000/student
   │
   ├─► main.py returns static/student/index.html
   │      (containers only — literally zero content in the file)
   │
   ├─► browser fetches base.css, app.css, api.js, pages.js, games.js, app.js
   │
   ├─► app.js fires App.init() on DOMContentLoaded
   │      ├─ GET /health           → is Gemini configured? is curriculum loaded?
   │      ├─ GET /api/me/settings  → font size, theme, accessibility
   │      │     └─ applies them as classes on <body>
   │      └─ App.go('home')
   │
   └─► App.go('home') calls renderHome()
          ├─ GET /api/students/stu-01/home     ◄── ONE request
          │      server does: profile + today's lesson + schedule
          │                 + daily challenge + pending flags + settings
          └─ Pages.home(data) returns an HTML string
                 → dropped into <div id="page-home">
```

**Why one request instead of seven:** the app runs on a tablet over a MiFi
router shared with a camera feed. Every extra round trip is another chance to
be visibly waiting while a judge watches.

`/api/me/...` is a convenience: `api.js` rewrites `me` into the real student id
before sending. Change which student you're viewing with `?student=stu-02` in
the URL — no code change.

---

## 5. The four flows that matter

### A. Answering a quiz question

This is the densest path in the app, and the best one to trace.

```
Student taps option B
   │
app.js  App.answerQuiz(1)
   │    disables the buttons so a double-tap can't submit twice
   │
   └─► POST /api/students/stu-01/quiz/7/answer   { answer_index: 1 }
          │
          routers/learning.py  answer_question()
             1. find the current unanswered question
             2. is answer_index == correct_index?
             3. write the answer into quiz_questions
             4. update streak and lives on the quiz row
             5. economy.update_mastery()   ← mastery moves ±
             6. economy.award()            ← stars, XP, activity log,
                                             streak, badge check, level check
             7. write an attempts row (the permanent record)
             8. if no questions left → mark complete, award bonus,
                                        tick the daily challenge
             9. return everything the UI needs to react
          │
   ◄──────┘
app.js  marks the right answer green, the wrong one red
        shows the explanation
        Voice.speak(explanation)
        handleAward()  → toast "+15 ⭐", maybe confetti, maybe a badge modal
        after 1.6s → render the next question
```

**The correct answer is never sent to the browser** until after you've
answered. If it were, any student could read it out of devtools and every
score in your demo would be meaningless. There's a test asserting this:
`test_quiz_never_leaks_the_correct_answer`.

### B. Asking Righty something (text)

```
Student types "help with fractions"
   │
   └─► POST /api/me/chat   { message: "help with fractions" }
          │
          routers/tutor_api.py → services/tutor.py  answer()
             1. save the student's message to chat_messages
             2. rag.search()  ← find matching curriculum in lesson_steps
             3. rag.build_context()  ← format it for the prompt
             4. llm.generate()
                   ├─ key present → call Gemini with:
                   │     system prompt (how Righty talks)
                   │   + student profile (autism? ADHD? → changes the prompt)
                   │   + THE CURRICULUM TEXT (the only source it may use)
                   │   + last 8 turns of conversation
                   └─ key missing / API down / timeout
                         → fallback: return the retrieved curriculum text
                           itself, in Righty's voice. Grounded and correct,
                           just less conversational.
             5. save Righty's reply, tagged with which engine produced it
             6. economy.award() → +3 stars for curiosity
          │
   ◄──────┘
app.js  adds the bubble, shows the source topics underneath,
        shows an AI / OFFLINE pill, speaks the reply
```

**The fallback is the important design decision here.** A tutoring app that
returns a 500 error because an upstream API hiccupped is worse than one that
answers a little more plainly. It's also your demo insurance: "what if the
Gemini API is slow during the presentation" has a defined answer, decided in
advance rather than improvised on stage.

### C. Talking to Righty (voice)

Same as above but the whole loop is **one HTTP request**, for the same
latency reason:

```
Tap mic → MediaRecorder captures audio → tap again to stop
   │
   └─► POST /api/me/voice/ask   (the audio blob as form-data)
          │
          1. stt.transcribe()   → ElevenLabs → text
             └─ heard nothing? return early: "I didn't catch that",
                UI offers the keyboard instead
          2. tutor.answer()     → same path as text chat above
          3. tts.synthesize()   → audio, or "use the browser's voice"
          │
   ◄──────┘ { heard, reply, speech }
app.js  shows both bubbles, plays the audio
```

The API keys stay on the server. A key shipped to a tablet browser is a key on
the open internet.

### D. A classroom attention flag

This is Phase 1, independent of the student app:

```
CV rig sees a student look away
   └─► POST /flags  { student_external_id, flag_type, confidence, ... }
          routers/flags.py
             ├─ confidence below threshold? store it, but don't queue it
             └─ otherwise status = 'pending'
                 + write a flag_events row

Teacher dashboard → GET /flags/pending
Teacher approves  → PATCH /flags/7  { status: "approved" }
Robot claims it   → PATCH /flags/7  { status: "in_progress" }
Robot resolves it → PATCH /flags/7  { status: "done" }
```

Illegal jumps (`pending → done`, skipping review) return 409. Every transition
appends to `flag_events`, so you can replay a flag's whole journey — which is
what makes it demoable rather than invisible.

Run `python scripts/demo_spine.py` to watch this narrated end to end.

---

## 6. The rule that holds the numbers together

**`app/economy.py` is the only module allowed to change a student's stars, XP,
or level.** Every reward goes through one function:

```python
economy.award(conn, student_id, "quiz_answer", stars=10, xp=15, ...)
```

That function does all of it in one place: writes the `activity_log` row,
updates the totals, recalculates the level, extends or resets the streak,
checks every badge, and returns an object describing exactly what happened so
the UI knows whether to show a toast, confetti, or a badge modal.

Why this matters: the weekly chart, the time-spent panel, the parent report and
the star counter in the header all read from `activity_log`. Because there is
exactly one writer, they can never disagree. If stars were updated in five
different route handlers, they would drift apart within a week and you'd spend
the night before the competition hunting the discrepancy.

**If you want to retune the game economy, `economy.py` lines 20–60 is the
whole thing** — every star value, XP value, level threshold and streak
multiplier is a constant in one block at the top.

---

## 7. The database

One file: `data/souly.db`. Delete it and run the three seed scripts to start
fresh. 31 tables, but they group into five ideas:

| Group | Tables | What it's for |
|---|---|---|
| **People** | `students`, `teachers`, `parents`, `parent_student` | Who's who. `parent_student` is the access-control table — see below. |
| **Curriculum** | `subjects`, `topics`, `lessons`, `lesson_steps`, `questions` | What gets taught. `lesson_steps.body` is the text Righty reads and RAG searches. |
| **Doing** | `sessions`, `attempts`, `quizzes`, `quiz_questions`, `lesson_progress`, `game_plays`, `chat_messages` | What the student actually did. |
| **Scoring** | `mastery`, `activity_log`, `student_skills`, `badges`, `student_badges`, `rewards`, `student_rewards`, `daily_challenge_progress`, `weekly_goals` | Progress and rewards. |
| **Flags** | `flags`, `flag_events` | The classroom attention pipeline. |

Plus `student_settings` (accessibility, persisted server-side so it follows the
student to any device) and `schedule_items`.

**The parent access rule.** "A parent sees only their own child" is enforced by
joining through `parent_student` on every parent-facing query:

```sql
SELECT s.* FROM students s
JOIN parent_student ps ON ps.student_id = s.id
WHERE ps.parent_id = ? AND s.id = ?
```

Zero rows → 404. Never trust a `student_id` that arrived from a parent's
browser. There's a test for this: `test_parent_sees_only_their_own_child`.

**Two safety flags worth knowing:**

- `is_verified` on topics, lessons and questions. Retrieval filters on it, so
  Righty physically cannot teach from unapproved material. Setting it is meant
  to be a human act.
- `drift_threshold_ms` on students. How long the CV should see drift before
  flagging *that particular student*. A student with autism may look away
  constantly as self-regulation.

---

## 8. File map — where to go to change something

### Backend

| File | Lines | What it does |
|---|---|---|
| `app/main.py` | 97 | Wires everything together, serves `/student` |
| `app/config.py` | 100 | **The only file that reads `.env`** |
| `app/db.py` | 117 | Opens SQLite, runs the schema files |
| `app/models.py` | 215 | The flag API's request/response shapes |
| `app/deps.py` | 47 | Resolves `stu-01` → a student row, or 404 |
| `app/economy.py` | 557 | **All stars, XP, levels, streaks, badges, mastery** |
| `app/security.py` | 58 | Password/access-code hashing |
| `app/services/llm.py` | 438 | Gemini + the offline fallback + Righty's personality |
| `app/services/rag.py` | 229 | Curriculum search |
| `app/services/stt.py` | 147 | ElevenLabs speech-to-text |
| `app/services/tts.py` | 251 | Swappable text-to-speech |
| `app/services/tutor.py` | 187 | One tutoring path for both text and voice |
| `app/routers/flags.py` | 375 | The classroom flag spine |
| `app/routers/student.py` | 302 | Profile, home screen, settings |
| `app/routers/learning.py` | 613 | Subjects, lessons, quizzes |
| `app/routers/gamification.py` | 419 | Games, rewards, badges, daily challenge |
| `app/routers/tutor_api.py` | 271 | Chat and voice endpoints |
| `app/routers/progress.py` | 179 | The progress dashboard |
| `app/routers/health.py` | 49 | Health check |

### Frontend

| File | Lines | What it does |
|---|---|---|
| `static/student/index.html` | 90 | Shell — ten empty containers and the nav bar |
| `static/student/base.css` | 516 | **Your original design, extracted verbatim** |
| `static/student/app.css` | 525 | Loading/error states, high contrast, dark theme, font sizes |
| `static/student/api.js` | 282 | Every network call, retry logic, shared `State` |
| `static/student/pages.js` | 822 | **One function per screen — the HTML lives here** |
| `static/student/games.js` | 270 | Quick-fire and memory-match engines |
| `static/student/app.js` | 852 | Routing, all user actions, the voice loop |

### Common changes

| I want to… | Open this |
|---|---|
| Change how a screen looks | `pages.js` — find the function named after the screen |
| Change colours, spacing, fonts | `base.css` (your design) or `app.css` (my additions) |
| Change star/XP values or level thresholds | `economy.py`, the constants block at the top |
| Change Righty's personality or tone | `services/llm.py`, `SYSTEM_PROMPT` |
| Add or edit lesson content | `scripts/seed_content.py`, the `CURRICULUM` list. See `docs/CURRICULUM.md`. |
| Add a badge | `scripts/seed_content.py`, the `BADGES` list — criteria are data, no code needed |
| Add a shop item | `scripts/seed_content.py`, the `REWARDS` list |
| Add a new API endpoint | The matching `routers/*.py`, then call it from `api.js` |
| Add a database table | `schema_v2.sql` (it's re-runnable) |
| Change what happens on a tap | `app.js` — the function named in the `onclick` |
| Swap the TTS vendor | `.env` only. Nothing else. |
| Change the quiz difficulty curve | `learning.py`, `_pick_questions()` |

---

## 9. Testing

```bash
pytest                     # 83 backend tests, ~2 seconds
python tests/ui_smoke.py   # drives all 10 screens in a real browser
```

The browser test is the one that catches real bugs. It clicks through every
screen, answers a quiz, buys a reward and asserts the stars actually left the
balance, toggles accessibility settings and re-fetches them from the server to
confirm they persisted, then screenshots everything. Any JavaScript console
error fails the run.

Tests use a temporary database and never touch your seeded data. There's a
guard that aborts the whole run if that ever stops being true — because the
first version of the fixtures silently wiped the dev database, and I'd rather
that fail loudly than quietly.

---

## 10. Things that will surprise you

**The HTML file has no content in it.** All ten pages are empty `<div>`s. If
you open `index.html` looking for the home screen, you won't find it — it's
`Pages.home()` in `pages.js`. This was deliberate: a hardcoded number in the
markup is a number that can silently disagree with the database.

**There's no build step.** No webpack, no npm, no compilation. Edit a `.js`
file, refresh the browser, done. This was chosen so any team member can
contribute without learning a toolchain in the two weeks you have.

**The app works with no API keys at all.** Righty answers from the curriculum,
the student types instead of speaking, everything else is identical. Check
`/health` to see which vendors are live.

**`/api/me/...` isn't a real path.** `api.js` rewrites `me` into the actual
student id. The server only ever sees `/api/students/stu-01/...`.

**Every screen re-fetches on navigation.** There's no client-side cache. Slower
in theory; in practice it means you can never look at a stale number, which
matters more when four screens are showing the same student at once.

---

## 11. If you're rewriting large parts

Two things are worth keeping even if everything around them changes:

1. **`schema.sql` + `schema_v2.sql`.** The data model is the part that's
   expensive to get wrong and cheap to keep. Rewriting the UI against the same
   schema is a day's work; changing the schema after four people have built
   against it is a week.

2. **`economy.py`'s single-writer rule.** Whatever the app looks like, keep one
   function as the only thing that changes a student's totals. This is the
   discipline that keeps four screens agreeing with each other.

Everything else — the routers, the pages, the games — is replaceable without
touching those two.
