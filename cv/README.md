# The classroom camera

Watches a class, and tells the backend when a child has been looking away for
longer than *that child's own* threshold. Nothing else leaves this machine.

```
camera ──► face detection ──► seat map ──► head pose + gaze ──► drift clock
                                                                    │
                                              POST /flags ◄─────────┘
                                                   │
                                        teacher's screen at /teacher
```

---

## What is sent, and what is not

One JSON object, about 200 bytes, when a drift outlasts the threshold:

```json
{ "student_external_id": "stu-01", "flag_type": "gaze_away",
  "confidence": 0.74, "duration_ms": 8300,
  "detected_at": "2026-09-06T09:14:22Z",
  "topic_code": "MATH.5TH-PRIMARY-MATH.L01",
  "metadata": { "camera_id": "cam-1", "seat_id": "seat-01",
                "yaw_off_deg": 31.2, "mesh_coverage": 0.91 } }
```

**No image, no video, no face template, no landmark coordinates.** There is no
code path in this folder that can transmit a frame. The privacy promise is
enforced by the shape of the program, not by a policy someone has to remember.

Children are identified by **seat**, not by face. We store nothing biometric
about anybody. The trade is honest and worth saying out loud: if two children
swap seats without telling anyone, the flags swap with them — a mistake a
teacher can see and fix in seconds. A wrong face match is not.

---

## Setup, once

```bash
conda env create -f cv/environment.yml
conda activate souly-cv
```

Python 3.11 is a hard ceiling, not a preference: `mediapipe 0.10.9` publishes
wheels for cp38–cp311 and nothing newer. The backend runs on its own
interpreter and is unaffected — the two talk over HTTP.

---

## Setup, per classroom

**1. Start the backend.** The camera reads the class list from it and refuses
to start without one, because a camera that does not know who it is watching
should not be running.

**2. Draw the seating plan.**

```bash
python cv/calibrate.py --camera 0 --topic MATH.5TH-PRIMARY-MATH.L01
```

Point the camera at the class, press SPACE to freeze a frame, drag a box
around each child, pick their name from the list. `s` saves to
`cv/config/seats.json`.

Get the lesson codes from `http://localhost:8000/topics/codes`. The topic is
attached to every flag so the home robot knows *what* to re-teach; without it
a flag says a child struggled but not with what, and the loop stops one step
short of closing.

**3. Run it.**

```bash
python cv/attention_monitor.py --camera 0
```

The first six seconds are calibration: ask the class to look at the board.
That captures what "attending" means *for each seat*, which is not the same
direction for a child on the left of the room as for one in the middle.

```
q  quit          c  recalibrate
a  assign a seat by clicking a face      h  hide the overlay
```

### Rehearsing without a classroom

```bash
python cv/attention_monitor.py --dry-run              # print flags, send nothing
python cv/attention_monitor.py --dry-run --offline    # and do not contact the backend
python cv/attention_monitor.py --video sample.mp4     # no camera needed
```

---

## How a drift becomes a flag

1. **Head pose** — yaw and pitch, from `cv2.solvePnP` over six stable mesh
   points. This is the primary signal, because a child who turns their whole
   head keeps their iris centred and an eyes-only system reads them as
   perfectly focused.
2. **Gaze** — iris offset inside the eye. The secondary signal, and the one
   that catches a child facing forward whose eyes are on their lap.
3. **Score** — each axis is 1.0 inside its tolerance and falls to 0.0 at its
   "away" angle. The three are combined with `min`, not an average: the worst
   axis is the honest answer, and averaging lets a good gaze score hide a
   plain head turn.
4. **Drift clock** — a low score is not a flag. A low score that *persists*
   for longer than that child's `drift_threshold_ms` is. Recovery needs a
   higher score than dropping did, so a child hovering on the line cannot
   flap.
5. **Publish** — once, then sixty seconds of silence about that child, so a
   turned-away child does not fill a teacher's queue on their own.

Every threshold is a named constant in one block at the top of
`engagement.py`. Timings are in **milliseconds, never frames**: a frame
window means one thing on a fast laptop and another on a slow one, and the
backend contract is written in time.

### Confidence is a real number

```
0.50 × how much of the drift we could actually see a face for
0.30 × how large that face was in the frame
0.20 × how far below the threshold the score sat
```

The backend stores anything below `FLAG_MIN_CONFIDENCE` (0.5) without putting
it in the teacher's queue. So a distant, half-seen, marginal drift filters
itself out — and stays in the database as evidence that the noise floor is
handled deliberately rather than by luck.

---

## Files

| File | What it does |
|---|---|
| `attention_monitor.py` | The loop: capture, detect, seat, score, drift clock, publish. |
| `engagement.py` | Head pose, gaze, scoring, confidence. Every threshold in one block. |
| `identity.py` | The seat map: which child sits where, and what is wrong with the plan. |
| `publisher.py` | Background sender. Buffers and retries; never blocks the camera. |
| `calibrate.py` | Draw the seating plan. |
| `config/seats.json` | The plan itself. Not in git — it is per classroom. |
| `test_cv.py` | 36 tests for every judgement we make on top of MediaPipe. |

```bash
pytest cv/test_cv.py        # in the souly-cv environment
```

They live here rather than in `tests/` because they need a different
environment. `pytest` from the repo root still only collects `tests/`.

---

## When it goes wrong

| What you see | What it means |
|---|---|
| `No seating plan at …` | Run `calibrate.py`. A seat map cannot be guessed. |
| `Could not read the class list` | The backend is not running, or `--api` is wrong. |
| `DROPPED … HTTP 404` | The seat map names a child the backend does not have. Compare `seats.json` with `GET /students`. |
| `stored but NOT queued` | Working as intended — that reading was below the noise floor. |
| `Not enough samples for: …` | Those children were not visible during calibration. Press `c` and try again. |
| Camera does not open | It tries 0–3 before giving up. Close anything else using the webcam. |
| Everything reads as attending | Recalibrate (`c`). The reference was probably captured while the class was not looking at the board. |

---

## What this does not do

It does not infer emotion, and it never will. `FlagType` in `app/models.py`
has no value for a feeling, so the API cannot express one — head angle and
eye position are geometry, and how a child *feels* is not something a camera
can be trusted to tell a teacher.

It also does not decide anything. A flag is a suggestion to an adult: nothing
reaches a child until a teacher approves it on their own screen, and no
display a class can see ever carries one child's name next to a difficulty.
