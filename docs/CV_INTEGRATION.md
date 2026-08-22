# CV Integration Guide

**For:** whoever owns the MediaPipe classroom detection code.
**Goal:** your code publishes a flag; the backend does the rest.

The entire integration is one HTTP POST. Everything below is detail around
that one call.

---

## The call

```python
import httpx
from datetime import datetime, timezone

httpx.post("http://<backend-ip>:8000/flags", json={
    "student_external_id": "stu-01",       # REQUIRED
    "flag_type": "gaze_away",              # REQUIRED
    "source": "classroom_cv",
    "confidence": 0.87,                    # optional
    "duration_ms": 6200,                   # optional
    "detected_at": datetime.now(timezone.utc)
                     .strftime("%Y-%m-%dT%H:%M:%SZ"),   # optional
    "metadata": {                          # optional, free-form
        "camera_id": "cam-1",
        "frame_no": 4821,
        "detector": "mediapipe-face-mesh"
    }
}, timeout=5.0)
```

A working reference implementation is `scripts/fake_cv_publisher.py` — the
`publish_flag()` function in that file is exactly what your code needs to do.

---

## Fields

| Field | Required | Notes |
|---|---|---|
| `student_external_id` | **Yes** | The `external_id` from the `students` table, e.g. `"stu-01"`. Not the numeric primary key. Unknown id → **404**. |
| `flag_type` | **Yes** | One of the seven values below. Anything else → **422**. |
| `source` | No | Defaults to `"classroom_cv"`. |
| `confidence` | No | `0.0`–`1.0`. **Omit it entirely if your model doesn't produce one** — that's supported, don't invent a fake `1.0`. |
| `duration_ms` | No | How long the drift lasted before you decided to publish. |
| `detected_at` | No | ISO-8601 UTC. Defaults to arrival time. See "Send your own timestamp" below. |
| `session_id` | No | Only if the student had a tutoring session open. Classroom detections normally leave this out. |
| `metadata` | No | Any JSON. Camera id, bbox, frame number — anything you want visible later. |

### `flag_type` values

`gaze_away` · `head_turn` · `absent` · `prolonged_inactivity` · `distress` ·
`repeated_error` · `help_requested`

The first four are the CV's. `repeated_error` and `help_requested` come from
the robot; `distress` may come from either.

---

## Send your own timestamp

Set `detected_at` to when **the camera saw it**, not when you make the HTTP
call. The backend records its own receipt time separately and reports the
difference as `pipeline_lag_ms` on every read.

That number is how you prove the system is responsive, and it's the kind of
thing judges ask about. If you let `detected_at` default, the lag always
reads as 0ms and you've thrown away the measurement.

---

## Confidence and the noise floor

Flags arriving below `FLAG_MIN_CONFIDENCE` (default `0.5`, set in `.env`) are
**stored but never queued**. The response tells you:

```json
{ "flag": { "id": 6, "status": "dismissed", ... }, "auto_dismissed": true }
```

This is deliberate. The teacher's queue stays trustworthy, and you keep every
filtered detection in the database as evidence that the noise floor is being
handled on purpose rather than by luck.

**Your side of this:** don't pre-filter in the CV. Publish everything with an
honest confidence and let the threshold do the filtering — that way you can
tune it from a config file at the venue instead of editing detection code.

---

## Per-student thresholds

Each student row has a `drift_threshold_ms`. It's how long that particular
student should be drifting before you flag them.

This matters more than it looks. A student with autism may look away
frequently as self-regulation; flagging them every three seconds for stimming
is precisely the failure mode this project exists to avoid, and it is a
question you should expect to be asked about.

Read it once at startup:

```python
students = httpx.get(f"{API}/students").json()   # endpoint lands in Phase 3
# For now: read it from the database, or hardcode from scripts/seed_students.py
```

---

## Error handling

| Status | Meaning | What your code should do |
|---|---|---|
| `201` | Stored. | Continue. |
| `404` | Unknown `student_external_id`. | Log loudly. Someone's ids are out of sync. Don't retry. |
| `409` | Student is marked inactive. | Skip this student. Don't retry. |
| `422` | Bad payload. | A bug in your code. Log the response body — it names the field. |
| Connection error | Backend down or MiFi dropped. | **Don't crash and don't block detection.** Buffer to a local list and retry; see below. |

### Suggested resilience

```python
_pending = []

def publish(payload):
    _pending.append(payload)
    for item in list(_pending):
        try:
            httpx.post(f"{API}/flags", json=item, timeout=3.0).raise_for_status()
            _pending.remove(item)
        except httpx.HTTPStatusError as e:
            if 400 <= e.response.status_code < 500:
                _pending.remove(item)   # our bug — retrying won't fix it
        except httpx.HTTPError:
            break                        # network — keep it buffered, try later
```

The CV loop must never stop because the network hiccupped. Buffering also
means a MiFi dropout during the demo produces a short delay rather than a
hole in the data.

---

## Testing without the backend team

```bash
./run.sh                                    # terminal 1
python scripts/poll_pending.py --watch      # terminal 2 — live queue
# terminal 3: run your CV code, watch flags appear
```

If you want to check the queue behaves before your code is ready:

```bash
python scripts/fake_cv_publisher.py --watch --interval 3
```

---

## What we still need from you

1. **Which `flag_type` values can your code actually produce?** If it only
   does `gaze_away`, say so — we'd rather trim the enum than carry types
   nothing emits.
2. **Does it produce a confidence score?** If not, we drop the noise-floor
   filter and find another way to handle false positives.
3. **Does it produce a drift duration**, or just a moment-in-time detection?
4. **How does it identify students** — face recognition, seat position,
   manual assignment at session start? This determines whether
   `student_external_id` is something you know reliably or something we need
   to solve together.
