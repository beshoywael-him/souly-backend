"""
The flag spine — Phase 1.

    camera event  ->  POST /flags  ->  row in SQLite  ->  GET /flags/pending

This is the path the whole system hangs off. Everything here is deliberately
boring and synchronous: no queues, no background tasks, no WebSockets yet.
Phase 3 adds the WebSocket push on top of these same functions.
"""

import json
import sqlite3
from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException, Query, Response, status

from app.config import settings
from app.db import db_dependency
from app.models import (
    ALLOWED_TRANSITIONS,
    FlagCreate,
    FlagCreateResponse,
    FlagEventOut,
    FlagOut,
    FlagStatus,
    FlagTransition,
    can_transition,
    utc_now_iso,
)

router = APIRouter(prefix="/flags", tags=["flags"])


# =============================================================================
# Helpers
# =============================================================================

def _parse_iso(ts: str) -> datetime:
    dt = datetime.fromisoformat(ts.replace("Z", "+00:00"))
    return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)


def _row_to_flag(row: sqlite3.Row) -> FlagOut:
    """Turn a joined flags+students row into the API shape."""
    d = dict(row)

    raw_meta = d.pop("metadata", None)
    if raw_meta:
        try:
            d["metadata"] = json.loads(raw_meta)
        except (json.JSONDecodeError, TypeError):
            # Never let malformed stored JSON take down a read.
            d["metadata"] = {"_unparsed": raw_meta}
    else:
        d["metadata"] = None

    try:
        lag = (_parse_iso(d["created_at"]) - _parse_iso(d["detected_at"])).total_seconds()
        d["pipeline_lag_ms"] = int(lag * 1000)
    except (ValueError, KeyError, TypeError):
        d["pipeline_lag_ms"] = None

    return FlagOut(**d)


# Single place that defines which columns a flag read returns.
_FLAG_SELECT = """
    SELECT
        f.id, f.student_id, f.session_id, f.source, f.flag_type,
        f.confidence, f.duration_ms, f.status,
        f.detected_at, f.created_at,
        f.reviewed_by_teacher_id, f.reviewed_at,
        f.picked_up_at, f.resolved_at, f.resolution_note, f.metadata,
        f.topic_id, t.title AS topic_title,
        s.external_id   AS student_external_id,
        s.display_name  AS student_name
    FROM flags f
    JOIN students s ON s.id = f.student_id
    LEFT JOIN topics t ON t.id = f.topic_id
"""


def _resolve_topic(conn: sqlite3.Connection, payload: FlagCreate) -> int | None:
    """
    Work out which lesson the child was drifting away from.

    An explicit id wins. A code that matches nothing returns None rather than
    raising: a typo in the CV rig's config file should cost us the topic on
    one flag, not the flag itself.
    """
    if payload.topic_id is not None:
        row = conn.execute("SELECT id FROM topics WHERE id = ?",
                           (payload.topic_id,)).fetchone()
        return row["id"] if row else None

    if payload.topic_code:
        row = conn.execute("SELECT id FROM topics WHERE code = ?",
                           (payload.topic_code,)).fetchone()
        return row["id"] if row else None

    return None


def _fetch_flag(conn: sqlite3.Connection, flag_id: int) -> FlagOut:
    row = conn.execute(_FLAG_SELECT + " WHERE f.id = ?", (flag_id,)).fetchone()
    if row is None:
        raise HTTPException(status_code=404, detail=f"Flag {flag_id} not found")
    return _row_to_flag(row)


def _log_event(
    conn: sqlite3.Connection,
    flag_id: int,
    from_status: str | None,
    to_status: str,
    actor: str,
    note: str | None = None,
) -> None:
    """Append to the audit trail. Called on every state change, including birth."""
    conn.execute(
        """
        INSERT INTO flag_events (flag_id, from_status, to_status, actor, note, created_at)
        VALUES (?, ?, ?, ?, ?, ?)
        """,
        (flag_id, from_status, to_status, actor, note, utc_now_iso()),
    )


# =============================================================================
# POST /flags  — the classroom CV publishes here
# =============================================================================

@router.post(
    "",
    response_model=FlagCreateResponse,
    status_code=status.HTTP_201_CREATED,
    summary="Publish an attention/engagement flag",
)
def create_flag(
    payload: FlagCreate,
    conn: sqlite3.Connection = Depends(db_dependency),
) -> FlagCreateResponse:
    """
    Called by the classroom CV when it detects a student's focus drifting.

    Resolves the student by `external_id` and returns 404 if unknown — a
    silent insert against a nonexistent student is the kind of bug that only
    surfaces during a live demo.
    """
    student = conn.execute(
        "SELECT id, external_id, display_name, drift_threshold_ms, is_active "
        "FROM students WHERE external_id = ?",
        (payload.student_external_id,),
    ).fetchone()

    if student is None:
        raise HTTPException(
            status_code=404,
            detail=(
                f"No student with external_id '{payload.student_external_id}'. "
                "Seed the student first (scripts/seed_students.py)."
            ),
        )
    if not student["is_active"]:
        raise HTTPException(
            status_code=409,
            detail=f"Student '{payload.student_external_id}' is not active.",
        )

    detected_at = payload.detected_at or utc_now_iso()
    created_at = utc_now_iso()

    # Low-confidence detections are stored but never queued. Two reasons:
    # the teacher's queue stays trustworthy, and you keep the filtered rows
    # as evidence that the noise floor is being handled deliberately.
    auto_dismissed = (
        payload.confidence is not None
        and payload.confidence < settings.flag_min_confidence
    )
    initial_status = (
        FlagStatus.DISMISSED.value if auto_dismissed else FlagStatus.PENDING.value
    )

    topic_id = _resolve_topic(conn, payload)

    cursor = conn.execute(
        """
        INSERT INTO flags (
            student_id, session_id, source, flag_type,
            confidence, duration_ms, status, topic_id,
            detected_at, created_at, resolution_note, metadata
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            student["id"],
            payload.session_id,
            payload.source.value,
            payload.flag_type.value,
            payload.confidence,
            payload.duration_ms,
            initial_status,
            topic_id,
            detected_at,
            created_at,
            (
                f"Auto-dismissed: confidence {payload.confidence} below "
                f"threshold {settings.flag_min_confidence}"
                if auto_dismissed else None
            ),
            json.dumps(payload.metadata) if payload.metadata else None,
        ),
    )
    flag_id = cursor.lastrowid

    _log_event(
        conn, flag_id, None, initial_status,
        actor=payload.source.value,
        note="Flag created" + (" and auto-dismissed" if auto_dismissed else ""),
    )

    return FlagCreateResponse(
        flag=_fetch_flag(conn, flag_id),
        auto_dismissed=auto_dismissed,
    )


# =============================================================================
# GET /flags/pending  — the teacher dashboard and robot poll here
# =============================================================================

@router.get(
    "/pending",
    response_model=list[FlagOut],
    summary="Fetch flags awaiting teacher review",
)
def get_pending_flags(
    student_id: int | None = Query(
        None, description="Filter by student database id."
    ),
    student_external_id: str | None = Query(
        None, description="Filter by external id, e.g. 'stu-ahmed'."
    ),
    limit: int = Query(50, ge=1, le=500),
    conn: sqlite3.Connection = Depends(db_dependency),
) -> list[FlagOut]:
    """
    Newest first. Either filter is optional; with neither, you get the whole
    class queue, which is exactly what the teacher dashboard wants.
    """
    sql = _FLAG_SELECT + " WHERE f.status = 'pending'"
    params: list[object] = []

    if student_id is not None:
        sql += " AND f.student_id = ?"
        params.append(student_id)
    if student_external_id is not None:
        sql += " AND s.external_id = ?"
        params.append(student_external_id)

    sql += " ORDER BY f.detected_at DESC, f.id DESC LIMIT ?"
    params.append(limit)

    return [_row_to_flag(r) for r in conn.execute(sql, params).fetchall()]


# =============================================================================
# GET /flags  — general query, useful for the dashboard and for debugging
# =============================================================================

@router.get("", response_model=list[FlagOut], summary="Query flags by status")
def list_flags(
    status_filter: FlagStatus | None = Query(None, alias="status"),
    student_external_id: str | None = Query(None),
    limit: int = Query(50, ge=1, le=500),
    conn: sqlite3.Connection = Depends(db_dependency),
) -> list[FlagOut]:
    sql = _FLAG_SELECT + " WHERE 1=1"
    params: list[object] = []

    if status_filter is not None:
        sql += " AND f.status = ?"
        params.append(status_filter.value)
    if student_external_id is not None:
        sql += " AND s.external_id = ?"
        params.append(student_external_id)

    sql += " ORDER BY f.detected_at DESC, f.id DESC LIMIT ?"
    params.append(limit)

    return [_row_to_flag(r) for r in conn.execute(sql, params).fetchall()]


@router.get("/{flag_id}", response_model=FlagOut, summary="Fetch one flag")
def get_flag(
    flag_id: int,
    conn: sqlite3.Connection = Depends(db_dependency),
) -> FlagOut:
    return _fetch_flag(conn, flag_id)


# =============================================================================
# PATCH /flags/{id}  — lifecycle transitions
#
# Phase 1 only strictly needs create + read. This is here because the
# lifecycle enum exists in the schema from day one, and having the transition
# logic validated now means Phase 3 wires a WebSocket to a working function
# instead of writing the state machine under deadline pressure.
# =============================================================================

@router.patch(
    "/{flag_id}",
    response_model=FlagOut,
    summary="Move a flag through its lifecycle",
)
def transition_flag(
    flag_id: int,
    payload: FlagTransition,
    conn: sqlite3.Connection = Depends(db_dependency),
) -> FlagOut:
    """
    pending -> approved | dismissed
    approved -> in_progress | dismissed
    in_progress -> done | dismissed

    Illegal moves return 409 rather than silently succeeding, so a buggy
    frontend can't teleport a flag from pending straight to done.
    """
    row = conn.execute("SELECT status FROM flags WHERE id = ?", (flag_id,)).fetchone()
    if row is None:
        raise HTTPException(status_code=404, detail=f"Flag {flag_id} not found")

    current = FlagStatus(row["status"])
    target = payload.status

    if current == target:
        return _fetch_flag(conn, flag_id)

    if not can_transition(current, target):
        allowed = sorted(s.value for s in ALLOWED_TRANSITIONS.get(current, set()))
        raise HTTPException(
            status_code=409,
            detail=(
                f"Cannot move flag {flag_id} from '{current.value}' to "
                f"'{target.value}'. Allowed from '{current.value}': "
                f"{allowed or 'nothing (terminal state)'}."
            ),
        )

    now = utc_now_iso()
    fields: dict[str, object] = {"status": target.value}

    # Stamp the timestamp that corresponds to this particular move.
    if target in (FlagStatus.APPROVED, FlagStatus.DISMISSED):
        fields["reviewed_at"] = now
        if payload.teacher_id is not None:
            fields["reviewed_by_teacher_id"] = payload.teacher_id
    if target == FlagStatus.IN_PROGRESS:
        fields["picked_up_at"] = now
    if target in (FlagStatus.DONE, FlagStatus.DISMISSED):
        fields["resolved_at"] = now
    if payload.note:
        fields["resolution_note"] = payload.note

    assignments = ", ".join(f"{k} = ?" for k in fields)
    conn.execute(
        f"UPDATE flags SET {assignments} WHERE id = ?",
        (*fields.values(), flag_id),
    )

    _log_event(conn, flag_id, current.value, target.value, payload.actor, payload.note)

    return _fetch_flag(conn, flag_id)


# =============================================================================
# GET /flags/{id}/events  — the audit trail
# =============================================================================

@router.get(
    "/{flag_id}/events",
    response_model=list[FlagEventOut],
    summary="Full lifecycle history of a flag",
)
def get_flag_events(
    flag_id: int,
    conn: sqlite3.Connection = Depends(db_dependency),
) -> list[FlagEventOut]:
    """
    Oldest first — reads as a story, which is what the presentation needs
    when showing "here is one flag's journey through the system".
    """
    exists = conn.execute("SELECT 1 FROM flags WHERE id = ?", (flag_id,)).fetchone()
    if exists is None:
        raise HTTPException(status_code=404, detail=f"Flag {flag_id} not found")

    rows = conn.execute(
        "SELECT id, flag_id, from_status, to_status, actor, note, created_at "
        "FROM flag_events WHERE flag_id = ? ORDER BY id ASC",
        (flag_id,),
    ).fetchall()
    return [FlagEventOut(**dict(r)) for r in rows]
