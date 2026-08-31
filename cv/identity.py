"""
Which child is this face?

-----------------------------------------------------------------------------
WHY SEATS AND NOT FACE RECOGNITION
-----------------------------------------------------------------------------
The obvious answer is to recognise the children. We are not going to.

Face recognition means building and storing a biometric template of every
child in the class. That is a permanent, irrevocable identifier belonging to a
nine-year-old, kept on a laptop that travels to a competition, in a project
whose entire privacy claim is that nothing identifying leaves the classroom.
The cost of getting it wrong is enormous and the benefit over a seating plan
is small.

So a seat is a named rectangle in the camera's view, and the child sitting in
it is whoever the teacher says is sitting in it. Nothing about a child's face
is stored, computed or transmitted — the camera only ever reports "the child
in seat 3 looked away for six seconds".

The trade is honest and worth stating to anyone who asks: if two children swap
seats without telling anyone, the flags swap with them. That is a mistake a
teacher can see and fix in two seconds. A wrong biometric match is not.

-----------------------------------------------------------------------------
TWO WAYS IN
-----------------------------------------------------------------------------
    seats.json      zones drawn once with calibrate.py and reused every day
    manual assign   press A in the monitor, click a face, pick a name

The manual path exists because a camera gets bumped, a class gets rearranged,
and neither of those should end the demo. It writes back to the same file.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path

CONFIG_DIR = Path(__file__).resolve().parent / "config"
DEFAULT_SEATS = CONFIG_DIR / "seats.json"


@dataclass
class Seat:
    """One named rectangle, and the child sitting in it."""

    seat_id: str
    student_external_id: str
    display_name: str
    # Normalised 0-1 so a seat map survives a change of camera resolution.
    x1: float
    y1: float
    x2: float
    y2: float

    def contains(self, cx: float, cy: float, frame_w: int, frame_h: int) -> bool:
        return (self.x1 * frame_w <= cx <= self.x2 * frame_w
                and self.y1 * frame_h <= cy <= self.y2 * frame_h)

    def centre(self, frame_w: int, frame_h: int) -> tuple[int, int]:
        return (int((self.x1 + self.x2) / 2 * frame_w),
                int((self.y1 + self.y2) / 2 * frame_h))

    def pixels(self, frame_w: int, frame_h: int) -> tuple[int, int, int, int]:
        return (int(self.x1 * frame_w), int(self.y1 * frame_h),
                int(self.x2 * frame_w), int(self.y2 * frame_h))

    def to_dict(self) -> dict:
        return {
            "seat_id": self.seat_id,
            "student_external_id": self.student_external_id,
            "display_name": self.display_name,
            "box": [round(self.x1, 4), round(self.y1, 4),
                    round(self.x2, 4), round(self.y2, 4)],
        }

    @classmethod
    def from_dict(cls, d: dict) -> "Seat":
        box = d["box"]
        return cls(
            seat_id=d["seat_id"],
            student_external_id=d["student_external_id"],
            display_name=d.get("display_name", d["student_external_id"]),
            x1=float(box[0]), y1=float(box[1]),
            x2=float(box[2]), y2=float(box[3]),
        )


@dataclass
class SeatMap:
    """The seating plan, plus everything that reads or edits it."""

    seats: list[Seat] = field(default_factory=list)
    camera_id: str = "cam-1"
    topic_code: str | None = None
    path: Path = DEFAULT_SEATS

    # ---- persistence -----------------------------------------------------

    @classmethod
    def load(cls, path: Path | str = DEFAULT_SEATS) -> "SeatMap":
        path = Path(path)
        if not path.exists():
            raise SystemExit(
                f"\nNo seating plan at {path}.\n\n"
                "A seat map cannot be guessed: it says which child is sitting\n"
                "where, and getting it wrong means flagging the wrong child.\n"
                "Draw one first:\n\n"
                "    python cv/calibrate.py --camera 1\n"
            )
        data = json.loads(path.read_text(encoding="utf-8"))
        return cls(
            seats=[Seat.from_dict(s) for s in data.get("seats", [])],
            camera_id=data.get("camera_id", "cam-1"),
            topic_code=data.get("topic_code"),
            path=path,
        )

    def save(self, path: Path | str | None = None) -> Path:
        path = Path(path or self.path)
        path.parent.mkdir(parents=True, exist_ok=True)
        payload = {
            "_readme": [
                "Which child sits where, as seen by this camera.",
                "Boxes are normalised 0-1 so the plan survives a resolution change.",
                "Draw or redraw with:  python cv/calibrate.py",
                "topic_code is the lesson on the board; it is attached to every",
                "flag so the home robot knows what to re-teach that evening.",
            ],
            "camera_id": self.camera_id,
            "topic_code": self.topic_code,
            "seats": [s.to_dict() for s in self.seats],
        }
        path.write_text(json.dumps(payload, indent=2), encoding="utf-8")
        return path

    # ---- lookup ----------------------------------------------------------

    def seat_for_point(self, cx: float, cy: float,
                       frame_w: int, frame_h: int) -> Seat | None:
        """
        Which seat a face centre falls in.

        When boxes overlap — and hand-drawn ones do — the smallest match wins,
        because the tighter zone is the more specific statement about where
        that child sits.
        """
        matches = [s for s in self.seats if s.contains(cx, cy, frame_w, frame_h)]
        if not matches:
            return None
        return min(matches, key=lambda s: (s.x2 - s.x1) * (s.y2 - s.y1))

    def by_student(self, external_id: str) -> Seat | None:
        for s in self.seats:
            if s.student_external_id == external_id:
                return s
        return None

    def assign(self, seat_id: str, external_id: str, display_name: str) -> None:
        """Point an existing seat at a different child, or add a new seat."""
        for s in self.seats:
            if s.seat_id == seat_id:
                s.student_external_id = external_id
                s.display_name = display_name
                return
        raise KeyError(seat_id)

    def add_seat(self, box: tuple[float, float, float, float],
                 external_id: str, display_name: str) -> Seat:
        x1, y1, x2, y2 = box
        seat = Seat(
            seat_id=f"seat-{len(self.seats) + 1:02d}",
            student_external_id=external_id,
            display_name=display_name,
            x1=min(x1, x2), y1=min(y1, y2), x2=max(x1, x2), y2=max(y1, y2),
        )
        self.seats.append(seat)
        return seat

    def validate(self, roster: dict[str, str]) -> list[str]:
        """
        Problems worth knowing about before a lesson starts, not during one.

        `roster` maps external_id -> display_name, as returned by GET /students.
        """
        problems: list[str] = []
        seen: set[str] = set()

        for s in self.seats:
            if s.student_external_id not in roster:
                problems.append(
                    f"{s.seat_id} is assigned to '{s.student_external_id}', "
                    f"who is not on the class list. Flags for that seat would "
                    f"be rejected with a 404."
                )
            if s.student_external_id in seen:
                problems.append(
                    f"{s.student_external_id} is assigned to more than one "
                    f"seat. One child cannot be in two places, and the flags "
                    f"would double up."
                )
            seen.add(s.student_external_id)
            if s.x2 <= s.x1 or s.y2 <= s.y1:
                problems.append(f"{s.seat_id} has a zero-sized box.")

        unseated = [e for e in roster if e not in seen]
        if unseated:
            names = ", ".join(f"{roster[e]} ({e})" for e in unseated)
            problems.append(
                f"On the class list but not in any seat, so never watched: {names}"
            )

        return problems
