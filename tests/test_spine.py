"""
Phase 1 acceptance tests — the flag spine.

The roadmap calls this "the single most important checkpoint in the whole
project; nothing downstream matters if this isn't solid." So it's a test
suite rather than a thing someone remembers demonstrating once.

    pytest -v
"""

from datetime import datetime, timedelta, timezone


def iso(dt: datetime) -> str:
    return dt.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


# =============================================================================
# The core round-trip
# =============================================================================

def test_flag_travels_from_camera_event_to_fetchable_record(client):
    """THE Phase 1 checkpoint: publish a flag, then fetch it back."""
    detected = iso(datetime.now(timezone.utc))

    r = client.post("/flags", json={
        "student_external_id": "stu-test",
        "flag_type": "gaze_away",
        "confidence": 0.91,
        "duration_ms": 6200,
        "detected_at": detected,
        "metadata": {"camera_id": "cam-1", "frame_no": 4821},
    })
    assert r.status_code == 201, r.text

    body = r.json()
    flag = body["flag"]
    assert body["auto_dismissed"] is False
    assert flag["status"] == "pending"
    assert flag["flag_type"] == "gaze_away"
    assert flag["student_name"] == "Testy"
    assert flag["student_external_id"] == "stu-test"
    assert flag["detected_at"] == detected
    assert flag["metadata"]["camera_id"] == "cam-1"

    # Now it must be fetchable — this is the half that proves the spine.
    r = client.get("/flags/pending", params={"student_external_id": "stu-test"})
    assert r.status_code == 200
    ids = [f["id"] for f in r.json()]
    assert flag["id"] in ids


def test_pipeline_lag_is_computed(client):
    """detected_at vs created_at must produce a real lag number."""
    detected = iso(datetime.now(timezone.utc) - timedelta(seconds=2))
    r = client.post("/flags", json={
        "student_external_id": "stu-test",
        "flag_type": "head_turn",
        "confidence": 0.8,
        "detected_at": detected,
    })
    flag = r.json()["flag"]
    assert flag["pipeline_lag_ms"] is not None
    assert flag["pipeline_lag_ms"] >= 1500


def test_detected_at_defaults_to_now_when_omitted(client):
    """The CV may not send a timestamp. That must not be an error."""
    r = client.post("/flags", json={
        "student_external_id": "stu-test",
        "flag_type": "absent",
    })
    assert r.status_code == 201
    assert r.json()["flag"]["detected_at"] is not None


def test_minimal_payload_is_accepted(client):
    """
    Only student + type are required. The real MediaPipe script may not
    produce a confidence score, and that must not block the integration.
    """
    r = client.post("/flags", json={
        "student_external_id": "stu-test",
        "flag_type": "prolonged_inactivity",
    })
    assert r.status_code == 201
    flag = r.json()["flag"]
    assert flag["confidence"] is None
    assert flag["duration_ms"] is None
    # No confidence means it cannot be below threshold, so it must queue.
    assert flag["status"] == "pending"


# =============================================================================
# Rejections — the spine must fail loudly, not silently
# =============================================================================

def test_unknown_student_is_rejected(client):
    r = client.post("/flags", json={
        "student_external_id": "stu-does-not-exist",
        "flag_type": "gaze_away",
    })
    assert r.status_code == 404
    assert "stu-does-not-exist" in r.json()["detail"]


def test_inactive_student_is_rejected(client):
    r = client.post("/flags", json={
        "student_external_id": "stu-inactive",
        "flag_type": "gaze_away",
    })
    assert r.status_code == 409


def test_invalid_flag_type_is_rejected(client):
    r = client.post("/flags", json={
        "student_external_id": "stu-test",
        "flag_type": "student_is_vibing",
    })
    assert r.status_code == 422


def test_out_of_range_confidence_is_rejected(client):
    r = client.post("/flags", json={
        "student_external_id": "stu-test",
        "flag_type": "gaze_away",
        "confidence": 1.7,
    })
    assert r.status_code == 422


def test_malformed_detected_at_is_rejected(client):
    r = client.post("/flags", json={
        "student_external_id": "stu-test",
        "flag_type": "gaze_away",
        "detected_at": "yesterday afternoon",
    })
    assert r.status_code == 422


# =============================================================================
# Noise floor
# =============================================================================

def test_low_confidence_flag_is_stored_but_not_queued(client):
    """
    Below FLAG_MIN_CONFIDENCE: kept as evidence, kept out of the teacher's
    queue. Both halves matter.
    """
    r = client.post("/flags", json={
        "student_external_id": "stu-test",
        "flag_type": "gaze_away",
        "confidence": 0.2,
    })
    assert r.status_code == 201
    body = r.json()
    assert body["auto_dismissed"] is True

    flag = body["flag"]
    assert flag["status"] == "dismissed"
    assert "below threshold" in (flag["resolution_note"] or "")

    # Stored...
    assert client.get(f"/flags/{flag['id']}").status_code == 200
    # ...but absent from the queue.
    pending_ids = [f["id"] for f in client.get("/flags/pending").json()]
    assert flag["id"] not in pending_ids


# =============================================================================
# Lifecycle state machine
# =============================================================================

def _make_flag(client, flag_type="gaze_away", confidence=0.9):
    r = client.post("/flags", json={
        "student_external_id": "stu-test",
        "flag_type": flag_type,
        "confidence": confidence,
    })
    assert r.status_code == 201
    return r.json()["flag"]["id"]


def test_full_lifecycle_pending_to_done(client):
    flag_id = _make_flag(client)

    r = client.patch(f"/flags/{flag_id}",
                     json={"status": "approved", "actor": "teacher:1", "teacher_id": 1})
    assert r.status_code == 200
    assert r.json()["status"] == "approved"
    assert r.json()["reviewed_at"] is not None
    assert r.json()["reviewed_by_teacher_id"] == 1

    r = client.patch(f"/flags/{flag_id}", json={"status": "in_progress", "actor": "robot"})
    assert r.status_code == 200
    assert r.json()["picked_up_at"] is not None

    r = client.patch(f"/flags/{flag_id}",
                     json={"status": "done", "actor": "robot", "note": "Re-engaged"})
    assert r.status_code == 200
    assert r.json()["status"] == "done"
    assert r.json()["resolved_at"] is not None

    # Once done, it must leave the pending queue.
    assert flag_id not in [f["id"] for f in client.get("/flags/pending").json()]


def test_illegal_transition_is_refused(client):
    """pending -> done skips teacher review and must be impossible."""
    flag_id = _make_flag(client)
    r = client.patch(f"/flags/{flag_id}", json={"status": "done", "actor": "robot"})
    assert r.status_code == 409
    assert "Cannot move" in r.json()["detail"]

    # And the flag must be untouched.
    assert client.get(f"/flags/{flag_id}").json()["status"] == "pending"


def test_terminal_state_cannot_be_reopened(client):
    flag_id = _make_flag(client)
    client.patch(f"/flags/{flag_id}", json={"status": "dismissed", "actor": "teacher:1"})
    r = client.patch(f"/flags/{flag_id}", json={"status": "approved", "actor": "teacher:1"})
    assert r.status_code == 409


def test_transition_to_same_status_is_idempotent(client):
    """A double-clicked Approve button must not 409."""
    flag_id = _make_flag(client)
    client.patch(f"/flags/{flag_id}", json={"status": "approved", "actor": "teacher:1"})
    r = client.patch(f"/flags/{flag_id}", json={"status": "approved", "actor": "teacher:1"})
    assert r.status_code == 200
    assert r.json()["status"] == "approved"


def test_transition_on_missing_flag_is_404(client):
    r = client.patch("/flags/999999", json={"status": "approved", "actor": "teacher:1"})
    assert r.status_code == 404


# =============================================================================
# Audit trail
# =============================================================================

def test_every_transition_is_recorded(client):
    flag_id = _make_flag(client)
    client.patch(f"/flags/{flag_id}", json={"status": "approved", "actor": "teacher:1"})
    client.patch(f"/flags/{flag_id}", json={"status": "in_progress", "actor": "robot"})
    client.patch(f"/flags/{flag_id}", json={"status": "done", "actor": "robot"})

    events = client.get(f"/flags/{flag_id}/events").json()
    assert [e["to_status"] for e in events] == [
        "pending", "approved", "in_progress", "done"
    ]
    # Creation has no previous state.
    assert events[0]["from_status"] is None
    assert events[0]["actor"] == "classroom_cv"
    assert events[1]["actor"] == "teacher:1"
    assert events[3]["actor"] == "robot"


def test_refused_transition_leaves_no_audit_entry(client):
    """A rejected move must not pollute the trail."""
    flag_id = _make_flag(client)
    before = len(client.get(f"/flags/{flag_id}/events").json())
    client.patch(f"/flags/{flag_id}", json={"status": "done", "actor": "robot"})
    after = len(client.get(f"/flags/{flag_id}/events").json())
    assert before == after


# =============================================================================
# Queue behaviour
# =============================================================================

def test_pending_queue_is_newest_first(client):
    now = datetime.now(timezone.utc)
    older = client.post("/flags", json={
        "student_external_id": "stu-test", "flag_type": "gaze_away",
        "confidence": 0.9, "detected_at": iso(now - timedelta(minutes=10)),
    }).json()["flag"]["id"]
    newer = client.post("/flags", json={
        "student_external_id": "stu-test", "flag_type": "gaze_away",
        "confidence": 0.9, "detected_at": iso(now),
    }).json()["flag"]["id"]

    ids = [f["id"] for f in client.get("/flags/pending").json()]
    assert ids.index(newer) < ids.index(older)


def test_pending_filter_by_student_excludes_others(client):
    r = client.get("/flags/pending", params={"student_external_id": "stu-test"})
    assert all(f["student_external_id"] == "stu-test" for f in r.json())


def test_list_flags_by_status(client):
    r = client.get("/flags", params={"status": "done"})
    assert r.status_code == 200
    assert all(f["status"] == "done" for f in r.json())


def test_get_missing_flag_is_404(client):
    assert client.get("/flags/999999").status_code == 404


# =============================================================================
# System
# =============================================================================

def test_health_reports_integrations_as_unconfigured(client):
    """Phase 2 keys are blank by design — health must say so without erroring."""
    body = client.get("/health").json()
    assert body["status"] == "ok"
    assert body["student_count"] >= 1
    assert body["integrations"]["llm_gemini"] is False
    assert body["integrations"]["stt_elevenlabs"] is False
    assert body["integrations"]["tts"] is False


def test_api_root_endpoint(client):
    body = client.get("/api").json()
    assert body["service"] == "Souly API"
    assert body["student_app"] == "/student"


def test_root_serves_the_student_app(client):
    """`/` redirects to the student UI — the thing you actually want to see."""
    r = client.get("/", follow_redirects=False)
    assert r.status_code in (307, 308)
    assert r.headers["location"] == "/student"
