"""
Pydantic request/response models — the wire contract.

This file is what the CV team, the Interfaces squad, and the robot app all
code against. If something isn't in here, it isn't part of the API.
"""

from datetime import datetime, timezone
from enum import Enum
from typing import Any

from pydantic import BaseModel, Field, field_validator


def utc_now_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


# =============================================================================
# Enums — mirror the CHECK constraints in schema.sql exactly.
# =============================================================================

class FlagStatus(str, Enum):
    PENDING = "pending"
    APPROVED = "approved"
    DISMISSED = "dismissed"
    IN_PROGRESS = "in_progress"
    DONE = "done"


class FlagType(str, Enum):
    """
    What a flag says happened. Every value is a behaviour that can be
    observed, timed, and shown to a teacher.

    There is deliberately no value for an emotional state. The classroom
    camera can honestly measure where a child is looking and for how long; it
    cannot measure how a child feels, and a system that tells a teacher a
    child is "distressed" is making a claim about a nine-year-old's inner life
    it has no way to support. ('distress' was permitted here until schema_v8
    and was never emitted by anything; it is gone before the CV rig could
    start using it.) If a child needs help they ask for it — that is
    `help_requested`, and it comes from the child, not from a model.
    """

    # From the classroom camera.
    GAZE_AWAY = "gaze_away"                        # eyes off the board
    HEAD_TURN = "head_turn"                        # whole head turned away
    ABSENT = "absent"                              # the seat is empty
    PROLONGED_INACTIVITY = "prolonged_inactivity"  # present, but nothing changes

    # From the home robot, or from the child.
    REPEATED_ERROR = "repeated_error"
    HELP_REQUESTED = "help_requested"


class FlagSource(str, Enum):
    CLASSROOM_CV = "classroom_cv"
    ROBOT = "robot"
    TEACHER_MANUAL = "teacher_manual"
    SELF_REPORT = "self_report"


# =============================================================================
# Lifecycle
# =============================================================================

# The only legal moves. Anything not listed here is rejected with 409.
# Encoding this as data (rather than if/else chains scattered across routes)
# means Phase 3's WebSocket layer can reuse it unchanged.
ALLOWED_TRANSITIONS: dict[FlagStatus, set[FlagStatus]] = {
    FlagStatus.PENDING:     {FlagStatus.APPROVED, FlagStatus.DISMISSED},
    FlagStatus.APPROVED:    {FlagStatus.IN_PROGRESS, FlagStatus.DISMISSED},
    FlagStatus.IN_PROGRESS: {FlagStatus.DONE, FlagStatus.DISMISSED},
    FlagStatus.DISMISSED:   set(),   # terminal
    FlagStatus.DONE:        set(),   # terminal
}


def can_transition(current: FlagStatus, target: FlagStatus) -> bool:
    return target in ALLOWED_TRANSITIONS.get(current, set())


# =============================================================================
# Flags — the Phase 1 contract
# =============================================================================

class FlagCreate(BaseModel):
    """
    Body of POST /flags. This is what the classroom CV publishes.

    Only `student_external_id`, `flag_type`, and `detected_at` are required —
    everything else the CV can supply if it has it. That keeps the contract
    achievable for a MediaPipe script that may not compute a confidence score.
    """

    # The CV rig identifies students by their human-readable external id
    # ("stu-ahmed"), not by database primary key.
    student_external_id: str = Field(..., min_length=1, max_length=64,
                                     examples=["stu-ahmed"])

    flag_type: FlagType = Field(..., examples=[FlagType.GAZE_AWAY])
    source: FlagSource = FlagSource.CLASSROOM_CV

    confidence: float | None = Field(
        None, ge=0.0, le=1.0,
        description="Model certainty 0.0-1.0. Omit if the CV doesn't produce one.",
    )
    duration_ms: int | None = Field(
        None, ge=0,
        description="How long the drift lasted before publishing.",
    )

    detected_at: str | None = Field(
        None,
        description="ISO-8601 UTC time the CAMERA saw it. Defaults to now if omitted.",
        examples=["2026-08-18T09:14:22Z"],
    )

    session_id: int | None = Field(
        None, description="Set only if the student had a tutoring session open."
    )

    # What the child was drifting away FROM. Without it the home robot learns
    # that a child struggled but not with what, and the loop stops one step
    # short of closing. The CV rig reads it from the lesson the teacher has
    # open and sends the topic's code; an unknown code is stored as null
    # rather than rejected, because losing a real detection over a typo in a
    # config file is the worse failure.
    topic_code: str | None = Field(
        None, max_length=64,
        description="Code of the lesson on the board, e.g. 'MATH-5-DECIMALS-L1'.",
        examples=["MATH-5-DECIMALS-L1"],
    )
    topic_id: int | None = Field(
        None, description="Topic primary key, if the caller already knows it. "
                          "Takes precedence over topic_code.",
    )

    metadata: dict[str, Any] | None = Field(
        None,
        description="Free-form CV context: camera id, bbox, frame number.",
        examples=[{"camera_id": "cam-1", "frame_no": 1423}],
    )

    @field_validator("detected_at")
    @classmethod
    def _validate_detected_at(cls, v: str | None) -> str | None:
        if v is None:
            return None
        try:
            # Accept both "...Z" and "+00:00" forms, normalise to "...Z".
            parsed = datetime.fromisoformat(v.replace("Z", "+00:00"))
        except ValueError as exc:
            raise ValueError(
                "detected_at must be ISO-8601, e.g. 2026-08-18T09:14:22Z"
            ) from exc
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=timezone.utc)
        return parsed.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


class FlagOut(BaseModel):
    """A flag as returned by the API."""

    id: int
    student_id: int
    student_external_id: str | None = None
    student_name: str | None = None
    session_id: int | None = None

    source: str
    flag_type: str
    confidence: float | None = None
    duration_ms: int | None = None

    topic_id: int | None = None
    topic_title: str | None = None

    status: str

    detected_at: str
    created_at: str
    reviewed_by_teacher_id: int | None = None
    reviewed_at: str | None = None
    picked_up_at: str | None = None
    resolved_at: str | None = None
    resolution_note: str | None = None

    metadata: dict[str, Any] | None = None

    # Milliseconds between the camera seeing it and the backend storing it.
    # Surfaced deliberately: it's the number that proves the spine is fast.
    pipeline_lag_ms: int | None = None


class FlagCreateResponse(BaseModel):
    """
    POST /flags response.

    `auto_dismissed` is True when the flag arrived below FLAG_MIN_CONFIDENCE.
    The flag is still stored (so you can show judges what was filtered and
    why) but it never reaches the teacher's queue.
    """

    flag: FlagOut
    auto_dismissed: bool = False


class FlagTransition(BaseModel):
    """Body of PATCH /flags/{id}."""

    status: FlagStatus
    actor: str = Field(
        "system",
        description="Who is making the change: 'teacher:3', 'robot', 'system'.",
    )
    teacher_id: int | None = None
    note: str | None = None


class FlagEventOut(BaseModel):
    """One entry in a flag's audit trail."""

    id: int
    flag_id: int
    from_status: str | None
    to_status: str
    actor: str
    note: str | None
    created_at: str


# =============================================================================
# Health
# =============================================================================

class HealthOut(BaseModel):
    status: str
    env: str
    database: str
    student_count: int
    pending_flags: int
    # Whether each vendor key is present. False for tts means the browser
    # fallback is active, which is a working state.
    integrations: dict[str, bool]
    tts_provider: str = "browser"
    # How much verified curriculum exists. "RAG returns nothing" and "RAG is
    # broken" look identical from the UI; this distinguishes them.
    curriculum: dict[str, Any] = Field(default_factory=dict)
