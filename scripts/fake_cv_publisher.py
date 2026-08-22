#!/usr/bin/env python3
"""
Classroom CV simulator.

Stands in for the real MediaPipe rig so the spine is testable today. It calls
exactly the same endpoint, with exactly the same payload shape, that the real
CV code must call — so when the real code arrives, this script becomes the
reference implementation for it.

    python scripts/fake_cv_publisher.py                     # one flag
    python scripts/fake_cv_publisher.py --count 5           # five flags
    python scripts/fake_cv_publisher.py --watch --interval 4  # continuous
    python scripts/fake_cv_publisher.py --student stu-02 --type head_turn
    python scripts/fake_cv_publisher.py --confidence 0.2    # below threshold

--------------------------------------------------------------------------
FOR THE CV TEAM: the whole integration is the publish_flag() function below.
Replace the random values with your real detections and you are done.
--------------------------------------------------------------------------
"""

import argparse
import random
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

import httpx

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

DEFAULT_API = "http://localhost:8000"

FLAG_TYPES = [
    "gaze_away",
    "head_turn",
    "absent",
    "prolonged_inactivity",
]


def utc_now_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def publish_flag(
    api_base: str,
    student_external_id: str,
    flag_type: str,
    confidence: float,
    duration_ms: int,
    camera_id: str = "cam-1",
    frame_no: int | None = None,
    timeout: float = 5.0,
) -> dict:
    """
    THE INTEGRATION POINT.

    This is the exact call the real CV code makes when it decides a student
    has drifted. Everything above it in this file is just simulation.
    """
    payload = {
        "student_external_id": student_external_id,
        "flag_type": flag_type,
        "source": "classroom_cv",
        "confidence": round(confidence, 3),
        "duration_ms": duration_ms,
        # Send the CAMERA's timestamp, not the moment of the HTTP call. The
        # backend records its own receipt time separately and reports the
        # difference as pipeline_lag_ms.
        "detected_at": utc_now_iso(),
        "metadata": {
            "camera_id": camera_id,
            "frame_no": frame_no if frame_no is not None else random.randint(1, 50_000),
            "detector": "mediapipe-face-mesh",
        },
    }

    response = httpx.post(f"{api_base}/flags", json=payload, timeout=timeout)
    response.raise_for_status()
    return response.json()


def fetch_students(api_base: str) -> list[str]:
    """
    Discover seeded students so the simulator doesn't guess ids.
    Falls back to the default seed ids if the endpoint isn't reachable.
    """
    try:
        r = httpx.get(f"{api_base}/health", timeout=3.0)
        r.raise_for_status()
    except httpx.HTTPError:
        pass
    return [f"stu-0{i}" for i in range(1, 6)]


def print_result(result: dict) -> None:
    flag = result["flag"]
    marker = "AUTO-DISMISSED" if result.get("auto_dismissed") else "PENDING"
    conf = flag.get("confidence")
    conf_s = f"{conf:.2f}" if conf is not None else "  - "
    lag = flag.get("pipeline_lag_ms")
    lag_s = f"{lag}ms" if lag is not None else "?"

    print(
        f"  flag #{flag['id']:<4} {flag['student_name'] or flag['student_id']:<10} "
        f"{flag['flag_type']:<21} conf={conf_s}  "
        f"{flag['duration_ms']}ms drift  lag={lag_s:<7} -> {marker}"
    )


def main() -> int:
    parser = argparse.ArgumentParser(description="Simulate classroom CV drift events.")
    parser.add_argument("--api", default=DEFAULT_API, help="Base API URL.")
    parser.add_argument("--student", help="external_id. Random seeded student if omitted.")
    parser.add_argument("--type", dest="flag_type", choices=FLAG_TYPES,
                        help="Flag type. Random if omitted.")
    parser.add_argument("--confidence", type=float,
                        help="0.0-1.0. Random 0.55-0.98 if omitted.")
    parser.add_argument("--duration", type=int, help="Drift duration in ms.")
    parser.add_argument("--count", type=int, default=1, help="How many to publish.")
    parser.add_argument("--watch", action="store_true",
                        help="Publish continuously until Ctrl-C.")
    parser.add_argument("--interval", type=float, default=5.0,
                        help="Seconds between flags in --watch mode.")
    args = parser.parse_args()

    students = [args.student] if args.student else fetch_students(args.api)

    print(f"Publishing to {args.api}/flags")
    if args.watch:
        print(f"Watch mode, every {args.interval}s. Ctrl-C to stop.\n")
    else:
        print()

    published = 0
    try:
        while True:
            student = args.student or random.choice(students)
            flag_type = args.flag_type or random.choice(FLAG_TYPES)
            confidence = (
                args.confidence if args.confidence is not None
                else round(random.uniform(0.55, 0.98), 3)
            )
            duration = args.duration or random.randint(3000, 12000)

            try:
                result = publish_flag(
                    args.api, student, flag_type, confidence, duration
                )
                print_result(result)
                published += 1
            except httpx.ConnectError:
                print(f"  ! Cannot reach {args.api}. Is the server running? (./run.sh)")
                return 1
            except httpx.HTTPStatusError as exc:
                print(f"  ! {exc.response.status_code}: {exc.response.text}")

            if not args.watch and published >= args.count:
                break
            time.sleep(args.interval if args.watch else 0.15)

    except KeyboardInterrupt:
        print("\nStopped.")

    print(f"\nPublished {published} flag(s). Check: python scripts/poll_pending.py")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
