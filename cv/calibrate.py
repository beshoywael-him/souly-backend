#!/usr/bin/env python3
"""
Draw the seating plan: which child sits where, as this camera sees it.

    python cv/calibrate.py --camera 0
    python cv/calibrate.py --image classroom.jpg      # from a still photo

Point the camera at the class, drag a box around each child, pick their name
from the list the backend gives you, and save. That file is then the only
thing that connects a face in the frame to a child in the database — no
biometrics, nothing stored about anyone's face.

    drag        draw a box
    u           undo the last box
    s           save and quit
    q / ESC     quit without saving
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

import cv2

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from cv.identity import SeatMap                 # noqa: E402
from cv.publisher import fetch_roster           # noqa: E402


def choose_student(roster: dict[str, dict], used: set[str]) -> tuple[str, str] | None:
    """Print the class list and let the operator pick one. Console, not GUI."""
    available = [(k, v["display_name"]) for k, v in roster.items()]
    if not available:
        print("The class list is empty. Seed the backend first.")
        return None

    print("\n  Who is sitting here?")
    for i, (ext, name) in enumerate(available, 1):
        mark = "  (already seated)" if ext in used else ""
        thr = roster[ext]["drift_threshold_ms"]
        print(f"    {i:>2}. {name:<16} {ext:<10} {thr:>5}ms{mark}")
    print("     0. cancel this box")

    while True:
        raw = input("  Number: ").strip()
        if raw in ("0", ""):
            return None
        if raw.isdigit() and 1 <= int(raw) <= len(available):
            return available[int(raw) - 1]
        print("  Not one of the numbers above.")


def main() -> int:
    p = argparse.ArgumentParser(description="Draw the Souly seating plan.")
    p.add_argument("--camera", type=int, default=0)
    p.add_argument("--image", help="Use a still image instead of a live camera.")
    p.add_argument("--api", default="http://localhost:8000")
    p.add_argument("--out", default=None, help="Where to write seats.json")
    p.add_argument("--camera-id", default="cam-1")
    p.add_argument("--topic", default=None,
                   help="Lesson code to attach to flags, e.g. MATH-5-DECIMALS-L1")
    p.add_argument("--width", type=int, default=1280)
    p.add_argument("--height", type=int, default=720)
    args = p.parse_args()

    out_path = Path(args.out) if args.out else \
        Path(__file__).resolve().parent / "config" / "seats.json"

    try:
        roster = fetch_roster(args.api)
    except Exception as exc:
        print(f"\nCould not read the class list from {args.api}/students")
        print(f"  {type(exc).__name__}: {exc}")
        print("\nStart the backend first — the seating plan has to name real\n"
              "children, and this is where those names come from.\n")
        return 1

    print(f"Class list: {len(roster)} children.")

    # --- get one frame to draw on -----------------------------------------
    if args.image:
        frame = cv2.imread(args.image)
        if frame is None:
            print(f"Could not read {args.image}")
            return 1
    else:
        cap = cv2.VideoCapture(args.camera)
        cap.set(cv2.CAP_PROP_FRAME_WIDTH, args.width)
        cap.set(cv2.CAP_PROP_FRAME_HEIGHT, args.height)
        if not cap.isOpened():
            print(f"Camera {args.camera} did not open. Try --camera 1.")
            return 1
        print("\nGet the class settled and in shot, then press SPACE to freeze "
              "the frame you will draw on. (q to quit)")
        frame = None
        while True:
            ok, live = cap.read()
            if not ok:
                print("The camera stopped returning frames.")
                cap.release()
                return 1
            preview = live.copy()
            cv2.putText(preview, "SPACE to freeze this frame  ·  q to quit",
                        (14, 30), cv2.FONT_HERSHEY_SIMPLEX, 0.7,
                        (255, 255, 255), 2)
            cv2.imshow("Souly — draw the seating plan", preview)
            key = cv2.waitKey(1) & 0xFF
            if key == ord(" "):
                frame = live.copy()
                break
            if key in (ord("q"), 27):
                cap.release()
                cv2.destroyAllWindows()
                return 0
        cap.release()

    ih, iw = frame.shape[:2]
    seat_map = SeatMap(camera_id=args.camera_id, topic_code=args.topic,
                       path=out_path)

    drag = {"start": None, "current": None, "active": False}
    pending: list[tuple[int, int, int, int]] = []

    def on_mouse(event, x, y, flags, param):
        if event == cv2.EVENT_LBUTTONDOWN:
            drag["start"] = (x, y)
            drag["current"] = (x, y)
            drag["active"] = True
        elif event == cv2.EVENT_MOUSEMOVE and drag["active"]:
            drag["current"] = (x, y)
        elif event == cv2.EVENT_LBUTTONUP and drag["active"]:
            drag["active"] = False
            x1, y1 = drag["start"]
            if abs(x - x1) > 15 and abs(y - y1) > 15:
                pending.append((min(x1, x), min(y1, y), max(x1, x), max(y1, y)))

    window = "Souly — draw the seating plan"
    cv2.namedWindow(window)
    cv2.setMouseCallback(window, on_mouse)

    print("\nDrag a box around each child.  u undo  ·  s save  ·  q quit\n")

    while True:
        canvas = frame.copy()

        for seat in seat_map.seats:
            sx1, sy1, sx2, sy2 = seat.pixels(iw, ih)
            cv2.rectangle(canvas, (sx1, sy1), (sx2, sy2), (110, 200, 110), 2)
            cv2.putText(canvas, seat.display_name, (sx1 + 5, sy1 + 22),
                        cv2.FONT_HERSHEY_SIMPLEX, 0.55, (110, 200, 110), 2)

        if drag["active"] and drag["start"] and drag["current"]:
            cv2.rectangle(canvas, drag["start"], drag["current"],
                          (240, 200, 90), 2)

        cv2.rectangle(canvas, (0, ih - 32), (iw, ih), (30, 28, 38), -1)
        cv2.putText(canvas, f"{len(seat_map.seats)} seats  ·  drag to add  ·  "
                            f"u undo  ·  s save  ·  q quit",
                    (12, ih - 11), cv2.FONT_HERSHEY_SIMPLEX, 0.55,
                    (230, 230, 240), 1)
        cv2.imshow(window, canvas)

        # A finished drag waits here until the operator names the child.
        if pending:
            x1, y1, x2, y2 = pending.pop(0)
            used = {s.student_external_id for s in seat_map.seats}
            picked = choose_student(roster, used)
            if picked:
                ext, name = picked
                existing = seat_map.by_student(ext)
                if existing:
                    seat_map.seats.remove(existing)
                    print(f"  (moved {name} from {existing.seat_id})")
                seat = seat_map.add_seat(
                    (x1 / iw, y1 / ih, x2 / iw, y2 / ih), ext, name
                )
                print(f"  {seat.seat_id}: {name}\n")

        key = cv2.waitKey(20) & 0xFF
        if key == ord("u") and seat_map.seats:
            gone = seat_map.seats.pop()
            print(f"  removed {gone.seat_id} ({gone.display_name})")
        elif key == ord("s"):
            if not seat_map.seats:
                print("  Nothing to save yet.")
                continue
            problems = seat_map.validate(
                {k: v["display_name"] for k, v in roster.items()}
            )
            path = seat_map.save(out_path)
            print(f"\nSaved {len(seat_map.seats)} seats to {path}")
            if problems:
                print("\nWorth knowing before the lesson:")
                for pr in problems:
                    print(f"  ! {pr}")
            print("\nNow run:  python cv/attention_monitor.py "
                  f"--camera {args.camera}\n")
            break
        elif key in (ord("q"), 27):
            print("\nQuit without saving.")
            break

    cv2.destroyAllWindows()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
