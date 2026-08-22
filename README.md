# Souly — AI Study Buddy

A study buddy for students with disabilities. Three endpoints in the real
world: the **student's robot** at home, the **classroom sensors** that flag
when focus drifts, and the **parents' portal** where a private account shows
only their own child's progress.

**Current state:** the flag spine and the complete student app are built and
working. 83 backend tests plus a browser test that drives all ten screens.

---

## Run it

```bash
pip install -r requirements.txt
cp .env.example .env                 # paste your API keys in
python scripts/init_db.py && python scripts/seed_students.py && python scripts/seed_content.py
python scripts/ingest_curriculum.py     # load the books (see below)
./run.sh
```

Then open **http://localhost:8000/student**

That is the student app — real data, real quizzes, real stars. Add
`?student=stu-02` to switch profiles.

### The parents' hub

One extra seed step, run once:

```bash
python scripts/seed_parents.py
```

It prints the access codes it created and which children each opens. Then open
**http://localhost:8000/parent** and sign in with one.

| Parent | Code | Opens |
|---|---|---|
| Rancy | `SOULY-RANCY` | Aziz |
| Karma | `SOULY-KARMA` | Lo2lo2 |
| Fayrouz | `SOULY-FAYROUZ` | Beshoy **and** Atef |

Fayrouz is the one to demo. Two children on one code is what the whole hub is
shaped around: the child switcher at the top of the sidebar, one conversation
thread per child rather than per teacher, and unread counts that are per-child
so a note about Atef never shows up as news about Beshoy.

Use `--real-codes` to generate proper random codes instead. They are printed
once and only the hash is stored, so write them down.

### The curriculum is the books

There is no lesson text in this repository and there is not meant to be. The
curriculum is the Ministry PDFs in `data/curriculum/`, and the database holds
only a map into them: which lesson lives on which page.

    canon      the PDF page.      Nobody writes it. It stays on disk.
    rendition  the explanation.   Written per child, at study time, from the page.
    path       order and pacing.  A deterministic policy over mastery.

That split is the answer to the first question a judge asks — *how do you know
it is teaching the right thing?* The content is the Ministry's, cited by page.
What the model changes is how it is said, and two children on the same page get
the same facts in different words.

To load a book:

```bash
pip install pypdfium2 pillow            # one-off, for rendering
python scripts/render_pages.py          # PDF → one image per page
# edit data/curriculum/curriculum_map.json — lesson → page, read off the book
python scripts/ingest_curriculum.py     # load it, OCR the mapped pages
python scripts/generate_practice.py --all --count 3
python scripts/generate_practice.py --review     # then --approve the good ones
```

`ingest_curriculum.py --dry-run` prints the map without writing anything, which
is the thing to read before loading a book someone else mapped.

Also available:

| URL | What it is |
|---|---|
| `/student` | The student app |
| `/docs` | Live API contract — hand this to the Interfaces squad |
| `/health` | Keys present? Curriculum loaded? |
| `/api/diagnostics/services` | Actually calls Gemini / ElevenLabs |

### Check your API keys work

```bash
python scripts/check_keys.py
```

Calls each vendor for real. Run it after adding keys, and again at the venue —
"the key is set" and "the key works" are different failures.

**The app runs fully without any keys.** Righty answers from the curriculum
using a local fallback, and the student types instead of speaking. Everything
else is identical. That is deliberate: it is your demo-day insurance.

---

## What the student app actually does

Every number on screen comes from the database, and every action writes back.

| Screen | What's real |
|---|---|
| **Home** | Greeting from the student's actual streak and last lesson, resume-where-you-left-off, live daily challenge, today's schedule |
| **Learn** | Six subjects with per-student mastery, lesson lists, recommendation based on weakest started subject |
| **Lesson** | Step-by-step content, read-aloud, progress saved per step, practice question |
| **Quiz** | Questions picked to match current mastery, streak multipliers, lives, mastery movement per answer, explanations |
| **Ask Righty** | Gemini grounded in verified curriculum, full voice loop (mic → ElevenLabs → Gemini → speech), sources cited |
| **Games** | Two playable engines fed from the verified question bank, scores persisted, personal bests |
| **Achievements** | 10 badges unlocking from real counters, progress bars on locked ones |
| **Progress** | Subject mastery, weekly activity chart, time spent, five tracked skills, weekly goals |
| **Rewards** | Stars actually spent, themes apply instantly, can't buy what you can't afford |
| **Profile** | Every setting persists server-side, including all accessibility options |

### The design

`static/student/base.css` is your original CSS, **extracted verbatim and never
edited**. All additions live in `app.css`. The pages are rendered by
`pages.js` from live API data, so no screen holds a hardcoded number.

---

## Accessibility

This is the product's whole reason to exist, so it isn't a settings page
afterthought.

- **Settings persist server-side, not in browser storage.** A student who
  needs larger buttons needs them on every device, including a shared
  classroom screen they've never opened.
- **High contrast** is a genuine AAA black-on-white pass — cards, gradients,
  toggles, progress bars, chat bubbles, the lot. Correct and wrong answers are
  distinguished by border weight and style as well as colour, because colour
  blindness and high-contrast need often occur together.
- **Reduce motion** honours both the setting and the OS `prefers-reduced-motion`.
  The design has a lot of movement, and vestibular sensitivity is common
  alongside autism.
- **Everything is keyboard reachable**, with a visible focus ring — the
  original design's tap-highlight reset had removed it.
- **Per-student CV drift thresholds.** A student with autism may look away
  frequently as self-regulation; flagging them every three seconds for
  stimming is exactly the failure this project exists to prevent.
- **Righty adapts its voice per student.** The prompt changes for autism
  (literal, concrete, predictable), ADHD (short, high-energy), dyslexia
  (spoken over written), and hearing impairment (clear, well-punctuated).
- **The typed path is always available.** It's the fallback when speech
  recognition fails, and the primary path for a student with a speech
  impairment — so it can never be second-class.

---

## Testing

```bash
pytest                        # 83 backend tests
python tests/ui_smoke.py      # drives all 10 screens in a real browser
```

The browser test is the one that matters for the UI: it clicks through every
screen, answers a quiz, buys a reward, checks the stars were really deducted,
toggles accessibility settings and confirms they persisted to the server, then
screenshots everything. Any console error fails the run.

Tests use a temporary database — `pytest` never touches your seeded data. A
session-scoped guard aborts the run if that ever stops being true.

---

## Project layout

```
souly-backend/
├── schema.sql               # Phase 1: students, flags, mastery, sessions
├── schema_v2.sql            # Phase 2: quizzes, badges, rewards…
├── schema_v5.sql            # the real curriculum: books + a lesson→page map
├── .env.example             # every secret, all documented
├── app/
│   ├── main.py              # FastAPI app, serves /student
│   ├── config.py            # the only file that reads env vars
│   ├── economy.py           # stars, XP, levels, streaks, badges, mastery
│   ├── security.py          # PBKDF2 hashing, parent access codes
│   ├── services/
│   │   ├── llm.py           # Gemini + grounded offline fallback
│   │   ├── curriculum.py    # reads the actual book pages off disk
│   │   ├── rag.py           # curriculum retrieval, over the book
│   │   ├── stt.py           # ElevenLabs
│   │   ├── tts.py           # swappable provider
│   │   └── tutor.py         # one path for text and voice tutoring
│   └── routers/             # flags, student, learning, gamification, tutor, progress
├── static/student/
│   ├── base.css             # YOUR design, untouched
│   ├── app.css              # states + accessibility
│   ├── api.js  pages.js  games.js  app.js
├── data/curriculum/         # THE BOOKS. The only copy of the content.
│   ├── *.pdf                # the Ministry PDFs
│   ├── curriculum_map.json  # lesson → page, human-approved
│   └── .cache/              # rendered pages + their text. All regenerable.
├── scripts/
│   ├── init_db.py  seed_students.py  seed_content.py
│   ├── render_pages.py      # PDF → one image per page
│   ├── ingest_curriculum.py # books + page map → the database
│   ├── generate_practice.py # write practice from a page, then approve it
│   ├── check_keys.py        # verify vendors respond
│   ├── fake_cv_publisher.py # classroom CV simulator
│   ├── poll_pending.py  demo_spine.py
└── docs/
    ├── STACK.md             # source of truth
    ├── CURRICULUM.md        # how to add your syllabus
    └── CV_INTEGRATION.md    # hand to the CV owner
```

---

## The economy, in one place

`app/economy.py` is the **only** module permitted to change a student's stars,
XP, or level. Everything flows through `award()`, which also writes
`activity_log`. That single rule is why the weekly chart, the parent report and
the star counter can never disagree — they all read the same rows.

| Action | Stars | XP |
|---|---|---|
| Correct answer | 10 (×1.5 at 3 streak, ×2 at 5) | 15 |
| Wrong answer | 2 | 0 |
| Lesson step | 5 | 8 |
| Lesson complete | 50 | 80 |
| Quiz complete | 30 | 50 |
| Ask Righty | 3 | 5 |
| Daily challenge | 100 | 100 |

A wrong answer is never worth zero. For a student who already struggles, zero
reward for effort teaches them to stop trying.

---

## The flag spine (Phase 1)

```
camera event → POST /flags → SQLite → GET /flags/pending → teacher → robot
```

```bash
python scripts/demo_spine.py     # narrated end-to-end walkthrough
```

Lifecycle `pending → approved → in_progress → done`, illegal moves rejected
with 409, every transition recorded in `flag_events`. Hand
`docs/CV_INTEGRATION.md` to whoever owns the MediaPipe code.

---

## Configuration

```bash
GEMINI_API_KEY=              # AI brain
ELEVENLABS_API_KEY=          # speech to text (confirmed vendor)

TTS_PROVIDER=                # VENDOR NOT YET CHOSEN — see docs/STACK.md
TTS_API_KEY=
TTS_VOICE_ID=

FLAG_MIN_CONFIDENCE=0.5      # CV flags below this are stored but not queued
```

`app/config.py` is the only file that reads the environment. When you pick a
TTS vendor, `.env` is the only file that changes.

> **Note:** the roadmap PDF has TTS and STT the wrong way round. STT is
> ElevenLabs (confirmed); TTS is undecided. See `docs/STACK.md`.

---

## Running on the MiFi

`run.sh` binds `0.0.0.0`, so the tablet, the CV rig and the other screens all
reach the API at `http://<backend-machine-ip>:8000`. Find the IP with
`ip addr` (Linux/macOS) or `ipconfig` (Windows).

**One process, three screens.** `/student`, `/parent` and (later) `/teacher`
are served by the same uvicorn against the same `souly.db`. Do not start a
second server for the parents' hub: SQLite serialises writers, and two
processes on one file is a mid-demo stall you cannot debug on stage.

They stay untangled by structure rather than by separation:

| | Student | Parent |
|---|---|---|
| Static files | `static/student/` | `static/parent/` |
| Routers | `student.py`, `learning.py`, … | `parent.py`, `parent_auth.py` |
| Token header | `X-Souly-Token` | `X-Souly-Parent` |
| Token table | `auth_tokens` | `parent_tokens` |

Nothing in `app/routers/parent*.py` imports from a student router, and no
frontend file is shared. Two token tables rather than one table with an
`audience` column means a student token cannot satisfy a parent check however
the query is written — there is a test for it.

Nothing in the parents' hub is loaded from the internet: no CDN, no
web font, no framework. It is plain CSS and plain JS, like the student app, so
it renders identically with the MiFi's upstream unplugged.

---

## What's left

**Before the competition**

- [ ] **Have a teacher check the two books' lesson map** in
      `data/curriculum/curriculum_map.json`, then re-run
      `python scripts/ingest_curriculum.py`. The content itself is the
      Ministry's and is not editable here by design — what a human approves is
      which lesson lives on which page.
- [ ] **Generate and approve practice** — `python scripts/generate_practice.py
      --all` then `--review`. Quizzes and games only ever draw on approved
      questions, so an empty bank means an empty quiz.
- [ ] Replace the `TEAM MEMBER` placeholders in `scripts/seed_students.py`
- [ ] Choose a TTS vendor (`docs/STACK.md` has the comparison)
- [ ] Send the CV code — and answer the four questions in `docs/CV_INTEGRATION.md`
- [ ] Stress-test the MiFi with all devices at once

**Next phases**

- [ ] Parent portal (schema, auth and the access-control join are already built)
- [ ] Teacher dashboard with live WebSocket flag queue
- [ ] Smart screen (class-wide content)
