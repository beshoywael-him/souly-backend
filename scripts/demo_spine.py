#!/usr/bin/env python3
"""
The Phase 1 checkpoint, as one narrated command.

    python scripts/demo_spine.py

Walks a single flag through its entire life — camera event, database row,
fetchable by the dashboard, teacher approval, robot pickup, resolution — and
prints the audit trail at the end. This is the thing you run in front of
people to show the spine works.

Requires the server to be running (./run.sh).
"""

import argparse
import sys
import time
from pathlib import Path

import httpx

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from scripts.fake_cv_publisher import publish_flag  # noqa: E402

DEFAULT_API = "http://localhost:8000"

BOLD = "\033[1m"
DIM = "\033[2m"
GREEN = "\033[32m"
RESET = "\033[0m"


def step(n: int, title: str) -> None:
    print(f"\n{BOLD}[{n}] {title}{RESET}")


def ok(msg: str) -> None:
    print(f"    {GREEN}✓{RESET} {msg}")


def detail(msg: str) -> None:
    print(f"    {DIM}{msg}{RESET}")


def main() -> int:
    parser = argparse.ArgumentParser(description="Narrated end-to-end spine demo.")
    parser.add_argument("--api", default=DEFAULT_API)
    parser.add_argument("--student", default="stu-01")
    parser.add_argument("--pause", type=float, default=0.8,
                        help="Seconds between steps. Use 0 for fast CI runs.")
    args = parser.parse_args()

    api = args.api
    pause = args.pause

    print(f"{BOLD}Souly — Phase 1 spine demo{RESET}")
    print(f"{DIM}camera event -> database -> fetchable -> teacher -> robot -> done{RESET}")

    # -- 0. Server reachable? -------------------------------------------------
    step(0, "Check the backend is up")
    try:
        health = httpx.get(f"{api}/health", timeout=5.0).json()
    except httpx.ConnectError:
        print(f"    ! Cannot reach {api}. Start the server first: ./run.sh")
        return 1
    ok(f"API healthy — {health['student_count']} students, "
       f"{health['pending_flags']} flags already pending")
    detail(f"database: {health['database']}")
    time.sleep(pause)

    # -- 1. The camera sees drift --------------------------------------------
    step(1, "Classroom CV detects attention drift")
    result = publish_flag(
        api_base=api,
        student_external_id=args.student,
        flag_type="gaze_away",
        confidence=0.87,
        duration_ms=6200,
        camera_id="cam-1",
        frame_no=4821,
    )
    flag = result["flag"]
    flag_id = flag["id"]
    ok(f"POST /flags -> 201, flag #{flag_id} created")
    detail(f"student: {flag['student_name']} ({flag['student_external_id']})")
    detail(f"type: {flag['flag_type']}  confidence: {flag['confidence']}  "
           f"drift: {flag['duration_ms']}ms")
    detail(f"camera saw it: {flag['detected_at']}")
    detail(f"backend stored it: {flag['created_at']}")
    detail(f"pipeline lag: {flag['pipeline_lag_ms']}ms")
    time.sleep(pause)

    # -- 2. It is fetchable ---------------------------------------------------
    step(2, "Teacher dashboard polls the queue")
    pending = httpx.get(
        f"{api}/flags/pending",
        params={"student_external_id": args.student},
        timeout=5.0,
    ).json()
    found = any(f["id"] == flag_id for f in pending)
    if not found:
        print(f"    ! Flag #{flag_id} did NOT appear in the pending queue.")
        return 1
    ok(f"GET /flags/pending -> flag #{flag_id} is in the queue "
       f"({len(pending)} pending for this student)")
    time.sleep(pause)

    # -- 3. Teacher approves --------------------------------------------------
    step(3, "Teacher approves the flag")
    r = httpx.patch(
        f"{api}/flags/{flag_id}",
        json={"status": "approved", "actor": "teacher:1", "teacher_id": 1,
              "note": "Confirmed — send the robot over."},
        timeout=5.0,
    )
    r.raise_for_status()
    ok(f"PATCH /flags/{flag_id} -> approved at {r.json()['reviewed_at']}")
    time.sleep(pause)

    # -- 4. Robot picks it up -------------------------------------------------
    step(4, "Robot claims the flag")
    r = httpx.patch(
        f"{api}/flags/{flag_id}",
        json={"status": "in_progress", "actor": "robot"},
        timeout=5.0,
    )
    r.raise_for_status()
    ok(f"PATCH /flags/{flag_id} -> in_progress at {r.json()['picked_up_at']}")
    detail("(Phase 2 replaces this with the agent actually starting a session)")
    time.sleep(pause)

    # -- 5. Resolved ----------------------------------------------------------
    step(5, "Robot re-engages the student and resolves")
    r = httpx.patch(
        f"{api}/flags/{flag_id}",
        json={"status": "done", "actor": "robot",
              "note": "Student re-engaged, resumed fractions."},
        timeout=5.0,
    )
    r.raise_for_status()
    ok(f"PATCH /flags/{flag_id} -> done at {r.json()['resolved_at']}")
    time.sleep(pause)

    # -- 6. Illegal move is refused ------------------------------------------
    step(6, "Confirm the state machine rejects an illegal move")
    r = httpx.patch(
        f"{api}/flags/{flag_id}", json={"status": "pending", "actor": "system"},
        timeout=5.0,
    )
    if r.status_code != 409:
        print(f"    ! Expected 409, got {r.status_code}. State machine is not enforcing.")
        return 1
    ok("done -> pending correctly refused with 409")
    time.sleep(pause)

    # -- 7. The audit trail ---------------------------------------------------
    step(7, "Full audit trail")
    events = httpx.get(f"{api}/flags/{flag_id}/events", timeout=5.0).json()
    for e in events:
        arrow = f"{e['from_status'] or 'created'} -> {e['to_status']}"
        print(f"    {e['created_at']}  {arrow:<28} by {e['actor']}")
        if e["note"]:
            detail(f"  \"{e['note']}\"")

    print(f"\n{GREEN}{BOLD}Spine verified.{RESET} "
          f"Flag #{flag_id} travelled camera -> database -> teacher -> robot -> done "
          f"in {len(events)} recorded transitions.\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
