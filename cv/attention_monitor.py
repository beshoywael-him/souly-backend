#!/usr/bin/env python3
"""
The classroom camera: watch a class, and tell the backend when a child drifts.

    python cv/attention_monitor.py --camera 1
    python cv/attention_monitor.py --camera 1 --dry-run     # print, don't send
    python cv/attention_monitor.py --video sample.mp4       # no camera needed

-----------------------------------------------------------------------------
WHAT LEAVES THIS MACHINE
-----------------------------------------------------------------------------
One JSON object, about 200 bytes, when a child has been looking away for
longer than that child's own threshold:

    {"student_external_id": "stu-01", "flag_type": "gaze_away",
     "confidence": 0.71, "duration_ms": 6200, "detected_at": "...",
     "topic_code": "MATH-5-DECIMALS-L1"}

No image, no video, no face template, no landmark coordinates. There is no
code path in this file that can transmit a frame, which is the point: the
privacy promise is enforced by the shape of the program, not by a policy
somebody has to remember.

-----------------------------------------------------------------------------
HOW IT DECIDES
-----------------------------------------------------------------------------
    1. Find every face in the frame.                 (face_detection)
    2. Put each one in a seat.                       (identity.SeatMap)
    3. Read its head angle and eye position.         (face_mesh + engagement)
    4. Score it against that seat's calibrated
       "looking at the board" direction.             (engagement.score_reading)
    5. If the score stays low for longer than THAT
       CHILD's drift_threshold_ms, publish one flag. (publisher, off-thread)

Step 5 is the part that matters and the part a frame-counting version gets
wrong. Thresholds are in milliseconds because that is what the backend
contract says and because a class is not a fixed frame rate: the same "40
frames" is 1.3 seconds on a fast laptop and 4 seconds on a slow one.

-----------------------------------------------------------------------------
CONTROLS
-----------------------------------------------------------------------------
    q   quit           c   re-run calibration
    a   assign a seat by clicking a face      h   hide/show the overlay
"""

from __future__ import annotations

import argparse
import math
import sys
import time
from collections import deque
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path

import cv2
import mediapipe as mp
import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from cv.engagement import (          # noqa: E402
    DRIFT_SCORE,
    RECOVER_SCORE,
    Reading,
    SeatReference,
    confidence_for,
    eye_gaze,
    estimate_head_pose,
    flag_type_for,
    score_reading,
    LEFT_IRIS, RIGHT_IRIS, LEFT_EYE_OUTER, RIGHT_EYE_OUTER,
    LEFT_EYE_INNER, RIGHT_EYE_INNER,
)
from cv.identity import SeatMap, Seat            # noqa: E402
from cv.publisher import FlagPublisher, fetch_roster, utc_now_iso   # noqa: E402

# =============================================================================
# Tuning — every timing in one block, all in seconds or milliseconds
# =============================================================================

# How fast the per-seat score follows the raw reading. A time constant, not a
# frame window, so the behaviour is the same at 12fps and at 30fps.
SCORE_TAU_S = 0.45

# After a flag, say nothing more about that child for this long. Without it a
# child who stays turned away produces a flag every threshold period, and the
# teacher's queue becomes something to ignore.
FLAG_COOLDOWN_S = 60.0

# A seat with nobody in it for this long is reported once as 'absent'. Long
# enough to survive a child bending down for a dropped pencil.
ABSENT_AFTER_S = 25.0
ABSENT_COOLDOWN_S = 300.0

# Keep a tracked face alive through this many seconds of missed detections
# before giving up on it. Bridges blinks in the detector without inventing a
# child who has left.
TRACK_MEMORY_S = 0.6

# How close a detection must be to a tracker's last position to be treated as
# the same face, as a fraction of frame width.
MATCH_RADIUS_FRAC = 0.09

# Calibration: how long the class is asked to look at the board.
CALIBRATION_S = 6.0

STATE_COLOURS = {
    "attending": (110, 200, 110),
    "drifting": (60, 170, 240),
    "flagged": (70, 90, 235),
    "absent": (150, 150, 150),
}


def now_s() -> float:
    return time.monotonic()


# =============================================================================
# Per-seat state — this is where a drift becomes a flag
# =============================================================================

@dataclass
class SeatState:
    """
    Everything we know about one seat right now.

    One of these per seat, created at startup and never destroyed. That is a
    deliberate difference from tracking by detection id: a child who is missed
    for a second comes back to the same state, the same reference direction
    and the same running drift clock, instead of becoming a brand-new stranger
    with a fresh baseline.
    """

    seat: Seat
    threshold_ms: int
    reference: SeatReference = field(default_factory=SeatReference)
    calibrated: bool = False

    score: float = 1.0
    last_reading: Reading | None = None

    # Tracking
    box: tuple[int, int, int, int] | None = None
    smooth_box: list[int] = field(default_factory=lambda: [0, 0, 0, 0])
    last_seen_at: float = 0.0
    last_mesh_at: float = 0.0

    # The drift clock
    drift_started_at: float | None = None
    drift_frames: int = 0
    drift_mesh_frames: int = 0
    worst_score: float = 1.0
    flagged_this_drift: bool = False
    last_flag_at: float = -1e9

    # Absence
    absent_since: float | None = None
    last_absent_flag_at: float = -1e9

    # For the on-screen sparkline only. Never transmitted.
    history: deque = field(default_factory=lambda: deque(maxlen=90))

    @property
    def name(self) -> str:
        return self.seat.display_name

    def update_box(self, box: tuple[int, int, int, int]) -> None:
        self.box = box
        if self.smooth_box == [0, 0, 0, 0]:
            self.smooth_box = list(box)
        else:
            # Exponential smoothing purely so the drawn rectangle stops
            # shivering. It has no effect on any score.
            a = 0.25
            for i in range(4):
                self.smooth_box[i] = int(a * box[i] + (1 - a) * self.smooth_box[i])

    def blend_score(self, raw: float, dt: float) -> None:
        alpha = 1.0 - math.exp(-max(dt, 1e-3) / SCORE_TAU_S)
        self.score += alpha * (raw - self.score)
        self.history.append(self.score)

    def state_name(self, t: float) -> str:
        if self.absent_since is not None and (t - self.absent_since) > ABSENT_AFTER_S:
            return "absent"
        if self.flagged_this_drift:
            return "flagged"
        if self.drift_started_at is not None:
            return "drifting"
        return "attending"

    def drift_ms(self, t: float) -> int:
        if self.drift_started_at is None:
            return 0
        return int((t - self.drift_started_at) * 1000)


# =============================================================================
# The monitor
# =============================================================================

class AttentionMonitor:

    def __init__(self, args: argparse.Namespace) -> None:
        self.args = args
        self.seat_map = SeatMap.load(args.seats)
        self.show_overlay = True

        # --- who are we allowed to talk about? -----------------------------
        if args.dry_run and args.offline:
            roster = {s.student_external_id: {"display_name": s.display_name,
                                              "drift_threshold_ms": args.default_threshold}
                      for s in self.seat_map.seats}
            print("Offline dry run: using the seat map's own names and "
                  f"a {args.default_threshold}ms threshold for everybody.")
        else:
            try:
                roster = fetch_roster(args.api)
            except Exception as exc:
                raise SystemExit(
                    f"\nCould not read the class list from {args.api}/students\n"
                    f"  {type(exc).__name__}: {exc}\n\n"
                    "The camera needs it to know which children it may report on\n"
                    "and how long each of them may drift. Start the backend first:\n"
                    "    run.bat        (Windows)\n"
                    "    ./run.sh       (Linux/macOS)\n\n"
                    "Or rehearse without it:\n"
                    "    python cv/attention_monitor.py --dry-run --offline\n"
                )

        problems = self.seat_map.validate(
            {k: v["display_name"] for k, v in roster.items()}
        )
        if problems:
            print("\nSeat map warnings:")
            for p in problems:
                print(f"  ! {p}")
            print()

        # --- one state per seat, for the whole session ---------------------
        self.seats: dict[str, SeatState] = {}
        for seat in self.seat_map.seats:
            entry = roster.get(seat.student_external_id, {})
            self.seats[seat.seat_id] = SeatState(
                seat=seat,
                threshold_ms=int(entry.get("drift_threshold_ms",
                                           args.default_threshold)),
            )

        self.publisher = FlagPublisher(
            api_base=args.api,
            camera_id=self.seat_map.camera_id,
            dry_run=args.dry_run,
        ).start()

        self.topic_code = args.topic or self.seat_map.topic_code
        if not self.topic_code:
            print("No topic_code set, so flags will not say WHICH lesson the\n"
                  "child drifted away from. Set one in cv/config/seats.json or\n"
                  "pass --topic. The home robot needs it to know what to\n"
                  "re-teach.\n")

        self.frame_times: deque = deque(maxlen=30)
        self.calibrating_until: float | None = None

    # ---------------------------------------------------------------------
    # Camera
    # ---------------------------------------------------------------------

    def open_capture(self) -> cv2.VideoCapture:
        if self.args.video:
            cap = cv2.VideoCapture(self.args.video)
            if not cap.isOpened():
                raise SystemExit(f"Could not open video file {self.args.video}")
            return cap

        cap = cv2.VideoCapture(self.args.camera)
        if not cap.isOpened():
            # Try the neighbours before giving up. Which index a USB camera
            # lands on changes between machines and between reboots, and
            # "camera 1 is not there" is a bad first experience.
            for idx in (0, 1, 2, 3):
                if idx == self.args.camera:
                    continue
                probe = cv2.VideoCapture(idx)
                if probe.isOpened():
                    print(f"Camera {self.args.camera} did not open; using "
                          f"camera {idx} instead.")
                    return probe
                probe.release()
            raise SystemExit(
                f"\nNo camera opened (tried {self.args.camera}, then 0-3).\n"
                "Close anything else using the webcam and try --camera N.\n"
            )

        cap.set(cv2.CAP_PROP_FRAME_WIDTH, self.args.width)
        cap.set(cv2.CAP_PROP_FRAME_HEIGHT, self.args.height)
        return cap

    # ---------------------------------------------------------------------
    # The loop
    # ---------------------------------------------------------------------

    def run(self) -> int:
        cap = self.open_capture()
        mp_fd = mp.solutions.face_detection
        mp_fm = mp.solutions.face_mesh

        print(f"\nWatching {len(self.seats)} seats. "
              f"Backend: {self.args.api}{'  (dry run)' if self.args.dry_run else ''}")
        print("q quit  ·  c recalibrate  ·  a assign a seat  ·  h hide overlay\n")

        self.start_calibration()
        last_t = now_s()
        consecutive_read_failures = 0

        with mp_fd.FaceDetection(model_selection=1,
                                 min_detection_confidence=0.35) as detector, \
             mp_fm.FaceMesh(max_num_faces=1, refine_landmarks=True,
                            min_detection_confidence=0.3,
                            min_tracking_confidence=0.5) as mesh:

            while True:
                ok, frame = cap.read()
                if not ok:
                    # The original version spun here forever at 100% CPU when
                    # a camera was unplugged. Fail after a second of nothing.
                    consecutive_read_failures += 1
                    if consecutive_read_failures > 30:
                        print("\nThe camera stopped returning frames. Exiting.")
                        break
                    time.sleep(0.03)
                    continue
                consecutive_read_failures = 0

                t = now_s()
                dt = t - last_t
                last_t = t
                self.frame_times.append(dt)

                ih, iw = frame.shape[:2]
                rgb = cv2.cvtColor(frame, cv2.COLOR_BGR2RGB)

                detections = self.detect_faces(detector, rgb, iw, ih)
                self.assign_to_seats(detections, iw, ih, t)
                self.read_and_score(mesh, rgb, iw, ih, t, dt)

                if self.calibrating_until and t >= self.calibrating_until:
                    self.finish_calibration()

                if not self.calibrating_until:
                    self.check_for_flags(t)

                if self.show_overlay:
                    self.draw(frame, iw, ih, t)

                cv2.imshow("Souly — classroom attention", frame)
                key = cv2.waitKey(1) & 0xFF
                if key == ord("q"):
                    break
                if key == ord("c"):
                    self.start_calibration()
                if key == ord("h"):
                    self.show_overlay = not self.show_overlay
                if key == ord("a"):
                    self.assign_seat_interactively(frame, iw, ih)

        cap.release()
        cv2.destroyAllWindows()
        print("\nFlushing anything still queued...")
        self.publisher.close()
        s = self.publisher.stats
        print(f"Sent {s.sent}, dropped {s.dropped}, still buffered {s.buffered}")
        if s.last_error:
            print(f"Last error: {s.last_error}")
        return 0

    # ---------------------------------------------------------------------
    # Detection and seating
    # ---------------------------------------------------------------------

    def detect_faces(self, detector, rgb, iw: int, ih: int) -> list[dict]:
        results = detector.process(rgb)
        out = []
        if not results.detections:
            return out
        for det in results.detections:
            b = det.location_data.relative_bounding_box
            x1 = max(0, int(b.xmin * iw))
            y1 = max(0, int(b.ymin * ih))
            x2 = min(iw, x1 + int(b.width * iw))
            y2 = min(ih, y1 + int(b.height * ih))
            if x2 <= x1 or y2 <= y1:
                continue
            out.append({
                "box": (x1, y1, x2, y2),
                "centre": ((x1 + x2) // 2, (y1 + y2) // 2),
                "score": float(det.score[0]) if det.score else 0.0,
            })
        return out

    def assign_to_seats(self, detections: list[dict],
                        iw: int, ih: int, t: float) -> None:
        """
        Put each detected face in a seat.

        A face is matched to the seat whose zone contains it. If no zone does,
        it belongs to nobody we are watching and is ignored entirely — a
        visitor walking past the camera is not a child we may report on.
        """
        taken: set[str] = set()

        for det in detections:
            cx, cy = det["centre"]
            seat = self.seat_map.seat_for_point(cx, cy, iw, ih)
            if seat is None or seat.seat_id in taken:
                continue
            state = self.seats.get(seat.seat_id)
            if state is None:
                continue
            state.update_box(det["box"])
            state.last_seen_at = t
            state.absent_since = None
            taken.add(seat.seat_id)

        # Anybody not matched this frame starts, or continues, being away.
        for state in self.seats.values():
            if state.seat.seat_id in taken:
                continue
            if state.last_seen_at and (t - state.last_seen_at) <= TRACK_MEMORY_S:
                continue                      # a blink in the detector, not an exit
            if state.absent_since is None:
                state.absent_since = t

    def read_and_score(self, mesh, rgb, iw: int, ih: int,
                       t: float, dt: float) -> None:
        """Run the mesh on each occupied seat and update its score."""
        for state in self.seats.values():
            if state.box is None or state.absent_since is not None:
                continue
            if state.last_seen_at and (t - state.last_seen_at) > TRACK_MEMORY_S:
                continue

            x1, y1, x2, y2 = state.box
            w, h = x2 - x1, y2 - y1
            pad_w, pad_h = int(w * 0.4), int(h * 0.4)
            cx1, cy1 = max(0, x1 - pad_w), max(0, y1 - pad_h)
            cx2, cy2 = min(iw, x2 + pad_w), min(ih, y2 + pad_h)
            if cx2 - cx1 < 20 or cy2 - cy1 < 20:
                continue

            crop = np.ascontiguousarray(rgb[cy1:cy2, cx1:cx2])
            result = mesh.process(crop)
            if not result.multi_face_landmarks:
                # The mesh dropped. Do NOT hold the last score: a child who
                # has turned far enough that the landmarks fail is a child who
                # is not attending, and freezing the number here is how the
                # original version stopped measuring exactly when it mattered.
                state.blend_score(0.0, dt)
                if state.drift_started_at is not None:
                    state.drift_frames += 1
                continue

            cw, chh = cx2 - cx1, cy2 - cy1
            landmarks = [
                (cx1 + int(lm.x * cw), cy1 + int(lm.y * chh))
                for lm in result.multi_face_landmarks[0].landmark
            ]

            xs = [p[0] for p in landmarks]
            ys = [p[1] for p in landmarks]
            state.update_box((min(xs), min(ys), max(xs), max(ys)))
            state.last_mesh_at = t
            face_h = max(ys) - min(ys)

            if self.calibrating_until:
                pose = estimate_head_pose(landmarks, iw, ih)
                gl = eye_gaze(landmarks, LEFT_IRIS, LEFT_EYE_OUTER, LEFT_EYE_INNER)
                gr = eye_gaze(landmarks, RIGHT_IRIS, RIGHT_EYE_OUTER, RIGHT_EYE_INNER)
                state.reference.add_sample(pose, gl, gr)
                state.score = 1.0
                continue

            reading = score_reading(landmarks, state.reference, iw, ih, face_h)
            state.last_reading = reading
            state.blend_score(reading.score, dt)

            if state.drift_started_at is not None:
                state.drift_frames += 1
                state.drift_mesh_frames += 1
                state.worst_score = min(state.worst_score, state.score)

    # ---------------------------------------------------------------------
    # Calibration
    # ---------------------------------------------------------------------

    def start_calibration(self) -> None:
        print(f"Calibrating for {CALIBRATION_S:.0f}s — ask the class to look "
              f"at the board.")
        for state in self.seats.values():
            state.reference = SeatReference()
            state.calibrated = False
        self.calibrating_until = now_s() + CALIBRATION_S

    def finish_calibration(self) -> None:
        self.calibrating_until = None
        good, bad = [], []
        for state in self.seats.values():
            if state.reference.finalise():
                state.calibrated = True
                good.append(state.name)
            else:
                bad.append(state.name)
        print(f"Calibrated {len(good)} seat(s): {', '.join(good) or '—'}")
        if bad:
            print(f"  Not enough samples for: {', '.join(bad)}. "
                  f"They will be scored against a straight-ahead default, "
                  f"which is less accurate. Press 'c' to try again.")
        print()

    # ---------------------------------------------------------------------
    # Drift -> flag
    # ---------------------------------------------------------------------

    def check_for_flags(self, t: float) -> None:
        for state in self.seats.values():

            # --- absence ---------------------------------------------------
            if (state.absent_since is not None
                    and (t - state.absent_since) > ABSENT_AFTER_S
                    and (t - state.last_absent_flag_at) > ABSENT_COOLDOWN_S):
                state.last_absent_flag_at = t
                self.publisher.publish(
                    student_external_id=state.seat.student_external_id,
                    flag_type="absent",
                    confidence=0.9,     # an empty seat is not a subtle reading
                    duration_ms=int((t - state.absent_since) * 1000),
                    detected_at=utc_now_iso(),
                    topic_code=self.topic_code,
                    metadata={"seat_id": state.seat.seat_id},
                )
                continue

            if state.absent_since is not None:
                continue

            # --- the drift clock -------------------------------------------
            if state.score < DRIFT_SCORE:
                if state.drift_started_at is None:
                    state.drift_started_at = t
                    state.drift_frames = 0
                    state.drift_mesh_frames = 0
                    state.worst_score = state.score
                    state.flagged_this_drift = False

            elif state.score >= RECOVER_SCORE:
                # Recovered. Hysteresis between DRIFT and RECOVER is what
                # stops a child hovering on the line from flapping.
                state.drift_started_at = None
                state.flagged_this_drift = False

            if state.drift_started_at is None or state.flagged_this_drift:
                continue

            elapsed_ms = state.drift_ms(t)
            if elapsed_ms < state.threshold_ms:
                continue
            if (t - state.last_flag_at) < FLAG_COOLDOWN_S:
                continue

            reading = state.last_reading
            coverage = (state.drift_mesh_frames / state.drift_frames
                        if state.drift_frames else 0.0)
            face_h = reading.face_height_px if reading else 0
            conf = confidence_for(coverage, face_h, state.worst_score,
                                  self.args.height)
            kind = flag_type_for(reading) if reading else "gaze_away"

            state.flagged_this_drift = True
            state.last_flag_at = t

            self.publisher.publish(
                student_external_id=state.seat.student_external_id,
                flag_type=kind,
                confidence=conf,
                duration_ms=elapsed_ms,
                detected_at=utc_now_iso(),
                topic_code=self.topic_code,
                metadata={
                    "seat_id": state.seat.seat_id,
                    # Enough to explain the decision later, and nothing that
                    # could reconstruct a face.
                    "yaw_off_deg": round(reading.yaw_off, 1) if reading else None,
                    "pitch_off_deg": round(reading.pitch_off, 1) if reading else None,
                    "gaze_off": round(reading.gaze_off, 3) if reading else None,
                    "mesh_coverage": round(coverage, 2),
                    "threshold_ms": state.threshold_ms,
                },
            )

    # ---------------------------------------------------------------------
    # Drawing
    # ---------------------------------------------------------------------

    def draw(self, frame, iw: int, ih: int, t: float) -> None:
        # Seat zones, so it is obvious who is being watched and who is not.
        for state in self.seats.values():
            sx1, sy1, sx2, sy2 = state.seat.pixels(iw, ih)
            cv2.rectangle(frame, (sx1, sy1), (sx2, sy2), (70, 70, 70), 1)

        for state in self.seats.values():
            name = state.state_name(t)
            colour = STATE_COLOURS[name]

            if state.box is None:
                sx1, sy1, sx2, sy2 = state.seat.pixels(iw, ih)
                cv2.putText(frame, f"{state.name}: {name}", (sx1 + 6, sy1 + 22),
                            cv2.FONT_HERSHEY_SIMPLEX, 0.5, colour, 1)
                continue

            bx1, by1, bx2, by2 = state.smooth_box
            cv2.rectangle(frame, (bx1, by1), (bx2, by2), colour, 2)

            label_y = by1 - 10 if by1 > 30 else by2 + 20
            pct = int(round(state.score * 100))
            cv2.putText(frame, f"{state.name}  {pct}%", (bx1, label_y - 16),
                        cv2.FONT_HERSHEY_SIMPLEX, 0.55, colour, 2)

            if state.drift_started_at is not None:
                secs = state.drift_ms(t) / 1000.0
                limit = state.threshold_ms / 1000.0
                cv2.putText(frame, f"drifting {secs:.1f}s / {limit:.0f}s",
                            (bx1, label_y), cv2.FONT_HERSHEY_SIMPLEX,
                            0.45, colour, 1)
            elif state.last_reading:
                r = state.last_reading
                cv2.putText(frame, f"yaw {r.yaw_off:+.0f} pitch {r.pitch_off:+.0f}",
                            (bx1, label_y), cv2.FONT_HERSHEY_SIMPLEX,
                            0.4, (170, 170, 170), 1)

        # Status bar
        fps = 1.0 / (sum(self.frame_times) / len(self.frame_times)) \
            if self.frame_times else 0.0
        s = self.publisher.stats
        bar = f"{fps:4.1f} fps   sent {s.sent}   buffered {s.buffered}"
        if s.dropped:
            bar += f"   DROPPED {s.dropped}"
        if self.calibrating_until:
            left = self.calibrating_until - now_s()
            bar = f"CALIBRATING {left:.1f}s — everyone look at the board"
        cv2.rectangle(frame, (0, ih - 34), (iw, ih), (30, 28, 38), -1)
        cv2.putText(frame, bar, (12, ih - 12), cv2.FONT_HERSHEY_SIMPLEX,
                    0.55, (230, 230, 240), 1)

    # ---------------------------------------------------------------------
    # Manual seat assignment
    # ---------------------------------------------------------------------

    def assign_seat_interactively(self, frame, iw: int, ih: int) -> None:
        """
        Click a face, choose a child, done.

        Exists because a camera gets bumped and a class gets rearranged, and
        neither should mean editing JSON by hand thirty seconds before a demo.
        """
        print("\nClick on a face to re-assign its seat "
              "(or press ESC in the window to cancel).")
        clicked: list[tuple[int, int]] = []

        def on_click(event, x, y, flags, param):
            if event == cv2.EVENT_LBUTTONDOWN:
                clicked.append((x, y))

        window = "Souly — classroom attention"
        cv2.setMouseCallback(window, on_click)
        while not clicked:
            if cv2.waitKey(30) & 0xFF == 27:
                cv2.setMouseCallback(window, lambda *a: None)
                print("Cancelled.\n")
                return
        cv2.setMouseCallback(window, lambda *a: None)

        cx, cy = clicked[0]
        seat = self.seat_map.seat_for_point(cx, cy, iw, ih)
        if seat is None:
            print("That point is not inside any seat zone. Redraw the map with "
                  "cv/calibrate.py if the camera has moved.\n")
            return

        print(f"\nSeat {seat.seat_id} currently holds "
              f"{seat.display_name} ({seat.student_external_id}).")
        new_id = input("New student_external_id (blank to cancel): ").strip()
        if not new_id:
            print("Cancelled.\n")
            return
        new_name = input("Display name: ").strip() or new_id

        self.seat_map.assign(seat.seat_id, new_id, new_name)
        self.seats[seat.seat_id].seat = seat
        path = self.seat_map.save()
        print(f"Saved to {path}. Press 'c' to recalibrate this seat.\n")


# =============================================================================

def main() -> int:
    p = argparse.ArgumentParser(
        description="Souly classroom attention monitor.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    p.add_argument("--camera", type=int, default=0,
                   help="Camera index. Try 0, 1 or 2. Default 0.")
    p.add_argument("--video", help="Read from a video file instead of a camera.")
    p.add_argument("--api", default="http://localhost:8000",
                   help="Backend base URL.")
    p.add_argument("--seats", default=None,
                   help="Path to the seating plan. Default cv/config/seats.json")
    p.add_argument("--topic", default=None,
                   help="Lesson code on the board, e.g. MATH-5-DECIMALS-L1.")
    p.add_argument("--width", type=int, default=1280)
    p.add_argument("--height", type=int, default=720)
    p.add_argument("--default-threshold", type=int, default=5000,
                   help="Drift threshold in ms for a child the backend "
                        "does not list. Default 5000.")
    p.add_argument("--dry-run", action="store_true",
                   help="Print flags instead of sending them.")
    p.add_argument("--offline", action="store_true",
                   help="With --dry-run: do not contact the backend at all.")
    args = p.parse_args()

    if args.seats is None:
        args.seats = Path(__file__).resolve().parent / "config" / "seats.json"

    try:
        return AttentionMonitor(args).run()
    except KeyboardInterrupt:
        print("\nStopped.")
        return 0


if __name__ == "__main__":
    raise SystemExit(main())
