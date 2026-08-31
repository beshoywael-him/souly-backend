"""
Getting a flag from the camera to the backend without ever stalling the camera.

-----------------------------------------------------------------------------
THE ONE RULE
-----------------------------------------------------------------------------
The detection loop must never wait on the network. A classroom router will
drop, the backend laptop will be busy, and a `requests.post` in the middle of
the frame loop turns either of those into a camera that visibly freezes while
a judge is watching.

So publishing happens on a background thread fed by a queue. The detection
loop calls `publish()`, which appends and returns immediately — measured in
microseconds, whatever the network is doing.

-----------------------------------------------------------------------------
WHAT HAPPENS WHEN IT FAILS
-----------------------------------------------------------------------------
`docs/CV_INTEGRATION.md` sets out the contract and this implements it exactly:

    201  stored. Done.
    404  unknown student. Our seat map is wrong. Log loudly, drop it —
         retrying cannot fix a wrong id, it only hides the mistake.
    409  the child is marked inactive. Drop it, same reasoning.
    422  our payload is malformed. That is a bug in this file. Log the body,
         which names the field, and drop it.
    5xx / connection error
         their problem or the network's. Keep the flag and try again.

A dropped router therefore produces a short delay and then a burst of correct
flags with their original `detected_at` timestamps, rather than a hole in the
data. The backend records its own receipt time separately and reports the
difference as `pipeline_lag_ms`, so a late flag is visibly late rather than
quietly wrong.
"""

from __future__ import annotations

import json
import queue
import threading
import time
import urllib.error
import urllib.request
from dataclasses import dataclass, field
from datetime import datetime, timezone

DEFAULT_API = "http://localhost:8000"

# How long a single POST may take before we give up and re-queue it. Short:
# a flag that takes five seconds to send is a flag the teacher sees late.
POST_TIMEOUT_S = 4.0

# How long to wait after a network failure before trying the buffer again.
RETRY_DELAY_S = 5.0

# If the backend is unreachable for a very long time, stop growing the buffer.
# 500 flags is far more than a lesson can produce; past that we are not
# buffering, we are leaking.
MAX_BUFFER = 500


def utc_now_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


@dataclass
class PublisherStats:
    sent: int = 0
    dropped: int = 0
    buffered: int = 0
    last_error: str | None = None
    last_sent_at: str | None = None
    last_lag_ms: int | None = None


@dataclass
class FlagPublisher:
    """
    Background sender for classroom flags.

    Start it once, call `publish()` from the frame loop, call `close()` on the
    way out so anything still buffered gets a last chance to leave.
    """

    api_base: str = DEFAULT_API
    camera_id: str = "cam-1"
    dry_run: bool = False           # print instead of sending; for rehearsal

    _q: "queue.Queue[dict]" = field(default_factory=queue.Queue, init=False)
    _thread: threading.Thread | None = field(default=None, init=False)
    _stop: threading.Event = field(default_factory=threading.Event, init=False)
    stats: PublisherStats = field(default_factory=PublisherStats, init=False)

    # ---- lifecycle -------------------------------------------------------

    def start(self) -> "FlagPublisher":
        self._thread = threading.Thread(
            target=self._run, name="flag-publisher", daemon=True
        )
        self._thread.start()
        return self

    def close(self, drain_seconds: float = 6.0) -> None:
        """Let the buffer empty, then stop. Called on the way out."""
        deadline = time.monotonic() + drain_seconds
        while not self._q.empty() and time.monotonic() < deadline:
            time.sleep(0.2)
        self._stop.set()
        if self._thread:
            self._thread.join(timeout=2.0)

    # ---- the call the frame loop makes -----------------------------------

    def publish(self, *, student_external_id: str, flag_type: str,
                confidence: float | None = None,
                duration_ms: int | None = None,
                detected_at: str | None = None,
                topic_code: str | None = None,
                metadata: dict | None = None) -> None:
        """
        Queue one flag. Returns immediately; never raises; never blocks.

        `detected_at` is when the CAMERA saw it, not when this call happens.
        Letting it default would make every measured lag read as zero and
        throw away the one number that proves the pipeline is fast.
        """
        if self._q.qsize() >= MAX_BUFFER:
            self.stats.dropped += 1
            self.stats.last_error = "buffer full — backend unreachable too long"
            return

        payload = {
            "student_external_id": student_external_id,
            "flag_type": flag_type,
            "source": "classroom_cv",
            "detected_at": detected_at or utc_now_iso(),
        }
        if confidence is not None:
            payload["confidence"] = round(float(confidence), 3)
        if duration_ms is not None:
            payload["duration_ms"] = int(duration_ms)
        if topic_code:
            payload["topic_code"] = topic_code

        meta = {"camera_id": self.camera_id, "detector": "mediapipe-face-mesh"}
        if metadata:
            meta.update(metadata)
        payload["metadata"] = meta

        self._q.put(payload)
        self.stats.buffered = self._q.qsize()

    # ---- the background thread -------------------------------------------

    def _run(self) -> None:
        pending: list[dict] = []
        while not self._stop.is_set():
            try:
                pending.append(self._q.get(timeout=0.5))
            except queue.Empty:
                pass

            # Drain whatever else arrived while we were waiting.
            while True:
                try:
                    pending.append(self._q.get_nowait())
                except queue.Empty:
                    break

            if not pending:
                continue

            still_pending = []
            for item in pending:
                keep = self._send_one(item)
                if keep:
                    still_pending.append(item)
                    # Network is down. Stop trying the rest this round rather
                    # than burning four seconds per flag against a dead host.
                    break

            # Anything after the one that failed goes back on the pile.
            idx = pending.index(still_pending[0]) if still_pending else len(pending)
            pending = pending[idx:] if still_pending else []
            self.stats.buffered = len(pending) + self._q.qsize()

            if pending:
                self._stop.wait(RETRY_DELAY_S)

    def _send_one(self, payload: dict) -> bool:
        """Send one flag. Returns True if it should be retried later."""
        if self.dry_run:
            print(f"  [dry-run] would POST /flags  {json.dumps(payload)}")
            self.stats.sent += 1
            self.stats.last_sent_at = utc_now_iso()
            return False

        body = json.dumps(payload).encode("utf-8")
        req = urllib.request.Request(
            f"{self.api_base.rstrip('/')}/flags",
            data=body,
            headers={"Content-Type": "application/json"},
            method="POST",
        )

        try:
            with urllib.request.urlopen(req, timeout=POST_TIMEOUT_S) as resp:
                raw = resp.read().decode("utf-8", "replace")
            data = json.loads(raw)
            flag = data.get("flag", {})
            self.stats.sent += 1
            self.stats.last_sent_at = utc_now_iso()
            self.stats.last_lag_ms = flag.get("pipeline_lag_ms")
            self.stats.last_error = None

            if data.get("auto_dismissed"):
                print(f"  flag {flag.get('id')} stored but NOT queued — "
                      f"confidence {payload.get('confidence')} is below the "
                      f"backend's noise floor")
            else:
                print(f"  flag {flag.get('id')} -> {payload['student_external_id']} "
                      f"{payload['flag_type']} "
                      f"({payload.get('duration_ms')}ms, "
                      f"conf {payload.get('confidence')}, "
                      f"lag {flag.get('pipeline_lag_ms')}ms)")
            return False

        except urllib.error.HTTPError as e:
            detail = e.read().decode("utf-8", "replace")[:300]
            if e.code in (404, 409, 422):
                # Our fault, and retrying will not change the answer.
                self.stats.dropped += 1
                self.stats.last_error = f"HTTP {e.code}: {detail}"
                print(f"  DROPPED {payload['student_external_id']} "
                      f"{payload['flag_type']} — HTTP {e.code}: {detail}")
                if e.code == 404:
                    print("     the seat map names a child the backend does not "
                          "have. Check cv/config/seats.json against GET /students.")
                return False
            self.stats.last_error = f"HTTP {e.code}: {detail}"
            return True

        except (urllib.error.URLError, TimeoutError, OSError) as e:
            self.stats.last_error = f"{type(e).__name__}: {e}"
            return True

        except json.JSONDecodeError as e:
            # A 200 that is not JSON means something is in front of the API —
            # a captive portal, usually. Worth keeping and retrying.
            self.stats.last_error = f"bad response: {e}"
            return True


# =============================================================================
# Reading the class list
# =============================================================================

def fetch_roster(api_base: str = DEFAULT_API,
                 timeout: float = 5.0) -> dict[str, dict]:
    """
    Read GET /students once at startup.

    Returns external_id -> {display_name, drift_threshold_ms, grade}.

    This is the call that makes per-child thresholds real: a child with autism
    who looks away frequently as self-regulation has a longer threshold set by
    somebody who knows them, and the camera obeys it instead of applying one
    number to everybody.
    """
    url = f"{api_base.rstrip('/')}/students"
    with urllib.request.urlopen(url, timeout=timeout) as resp:
        data = json.loads(resp.read().decode("utf-8"))

    return {
        s["external_id"]: {
            "display_name": s["display_name"],
            "drift_threshold_ms": s["drift_threshold_ms"],
            "grade": s.get("grade"),
        }
        for s in data.get("students", [])
        if s.get("is_active", True)
    }
