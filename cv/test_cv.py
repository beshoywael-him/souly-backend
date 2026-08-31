"""
The classroom camera's decisions, tested without a camera.

    conda activate souly-cv
    pip install pytest
    pytest cv/test_cv.py

They live here rather than in tests/ because they belong to a different
environment: tests/ runs against the backend's interpreter, which has no
OpenCV and no MediaPipe, and this file needs both. `pytest` from the repo root
still only collects tests/, so the backend suite stays exactly as fast as it
was.

What is worth testing here is not "does MediaPipe find a face" — that is
Google's job — but every judgement WE make on top of it: is this child in
this seat, is this drift long enough to mention, is this reading trustworthy
enough to put in front of a teacher. All of that is arithmetic and all of it
can be checked at a desk.
"""

import math

import pytest

pytest.importorskip("cv2", reason="camera environment only (conda: souly-cv)")
pytest.importorskip("mediapipe", reason="camera environment only")

from cv.engagement import (          # noqa: E402
    DRIFT_SCORE,
    RECOVER_SCORE,
    GAZE_AWAY,
    GAZE_TOLERANCE,
    YAW_AWAY_DEG,
    YAW_TOLERANCE_DEG,
    HeadPose,
    Reading,
    SeatReference,
    _axis_score,
    confidence_for,
    eye_gaze,
    flag_type_for,
)
from cv.identity import Seat, SeatMap    # noqa: E402


# =============================================================================
# Scoring
# =============================================================================

class TestAxisScore:

    def test_inside_tolerance_is_full_marks(self):
        assert _axis_score(0.0, 18.0, 45.0) == 1.0
        assert _axis_score(18.0, 18.0, 45.0) == 1.0

    def test_at_the_away_threshold_is_zero(self):
        assert _axis_score(45.0, 18.0, 45.0) == 0.0
        assert _axis_score(90.0, 18.0, 45.0) == 0.0

    def test_it_falls_off_in_between(self):
        mid = _axis_score(31.5, 18.0, 45.0)
        assert 0.45 < mid < 0.55

    def test_a_real_head_turn_reaches_the_drift_threshold(self):
        """
        The failure that made the first version unusable: no achievable head
        angle could ever cross the line, so no flag could ever fire. A child
        turned 35 degrees off must score below DRIFT_SCORE.
        """
        assert _axis_score(35.0, YAW_TOLERANCE_DEG, YAW_AWAY_DEG) < DRIFT_SCORE

    def test_sitting_at_an_angle_is_not_a_drift(self):
        """A child on the edge of the room is turned, and is still attending."""
        assert _axis_score(15.0, YAW_TOLERANCE_DEG, YAW_AWAY_DEG) == 1.0


class TestGaze:

    def test_a_centred_iris_reads_as_a_half(self):
        pts = {33: (100, 50), 133: (140, 50), 468: (120, 50)}
        landmarks = [(0, 0)] * 500
        for i, p in pts.items():
            landmarks[i] = p
        assert eye_gaze(landmarks, 468, 33, 133) == pytest.approx(0.5, abs=0.01)

    def test_a_degenerate_eye_reads_as_neutral_not_as_maximum_deviation(self):
        landmarks = [(100, 50)] * 500
        assert eye_gaze(landmarks, 468, 33, 133) == 0.5

    def test_missing_landmarks_do_not_raise(self):
        assert eye_gaze([], 468, 33, 133) == 0.5

    def test_a_hard_sideways_look_crosses_the_gaze_threshold(self):
        assert _axis_score(GAZE_AWAY, GAZE_TOLERANCE, GAZE_AWAY) == 0.0


class TestFlagType:

    def test_a_turned_head_is_reported_as_a_head_turn(self):
        r = Reading(score=0.2, yaw_off=40, pitch_off=2, gaze_off=0.02,
                    pose=HeadPose(40, 2, 0), dominant="head")
        assert flag_type_for(r) == "head_turn"

    def test_wandering_eyes_are_reported_as_gaze_away(self):
        r = Reading(score=0.2, yaw_off=2, pitch_off=2, gaze_off=0.25,
                    pose=HeadPose(2, 2, 0), dominant="gaze")
        assert flag_type_for(r) == "gaze_away"

    def test_there_is_no_flag_type_for_a_feeling(self):
        """
        Guards a decision, not a behaviour. Whatever the camera sees, the only
        words it can put in front of a teacher are about where a child was
        looking — never about how they seemed.
        """
        from app.models import FlagType
        values = {f.value for f in FlagType}
        assert "distress" not in values
        assert {"gaze_away", "head_turn", "absent"} <= values


class TestConfidence:

    def test_it_stays_in_range(self):
        for coverage in (0.0, 0.5, 1.0):
            for face_h in (0, 40, 400):
                for worst in (0.0, 0.3, 0.45):
                    c = confidence_for(coverage, face_h, worst, 720)
                    assert 0.0 <= c <= 1.0

    def test_a_drift_we_barely_saw_is_less_trusted(self):
        seen = confidence_for(1.0, 120, 0.1, 720)
        glimpsed = confidence_for(0.2, 120, 0.1, 720)
        assert glimpsed < seen

    def test_a_tiny_face_is_less_trusted_than_a_large_one(self):
        near = confidence_for(1.0, 200, 0.1, 720)
        far = confidence_for(1.0, 25, 0.1, 720)
        assert far < near

    def test_a_poor_reading_falls_below_the_backend_noise_floor(self):
        """
        The backend stores anything under 0.5 without queueing it. A distant,
        half-seen, marginal drift should land there by itself rather than
        reaching a teacher.
        """
        assert confidence_for(0.25, 20, 0.44, 720) < 0.5


class TestSeatReference:

    def test_it_refuses_to_finalise_on_too_few_samples(self):
        ref = SeatReference()
        for _ in range(5):
            ref.add_sample(HeadPose(10, 2, 0), 0.5, 0.5)
        assert ref.finalise() is False

    def test_it_uses_the_median_so_one_glance_away_cannot_move_it(self):
        ref = SeatReference()
        for _ in range(20):
            ref.add_sample(HeadPose(10.0, 0.0, 0.0), 0.5, 0.5)
        # Three frames where the child looked right across the room.
        for _ in range(3):
            ref.add_sample(HeadPose(70.0, 0.0, 0.0), 0.9, 0.9)
        assert ref.finalise() is True
        assert ref.yaw == pytest.approx(10.0, abs=0.5)

    def test_a_failed_pose_is_not_sampled(self):
        ref = SeatReference()
        for _ in range(20):
            ref.add_sample(HeadPose(0, 0, 0, ok=False), 0.5, 0.5)
        assert ref.samples == 0
        assert ref.finalise() is False


# =============================================================================
# Who is this child?
# =============================================================================

def _map():
    return SeatMap(seats=[
        Seat("seat-01", "stu-01", "Member1", 0.0, 0.0, 0.3, 0.5),
        Seat("seat-02", "stu-02", "Member2", 0.4, 0.0, 0.7, 0.5),
    ])


class TestSeatMap:

    def test_a_face_inside_a_zone_gets_that_child(self):
        seat = _map().seat_for_point(150, 100, 1000, 500)
        assert seat.student_external_id == "stu-01"

    def test_a_face_outside_every_zone_belongs_to_nobody(self):
        """A visitor walking past the camera is not a child we may report on."""
        assert _map().seat_for_point(800, 400, 1000, 500) is None

    def test_the_tighter_zone_wins_when_hand_drawn_boxes_overlap(self):
        m = SeatMap(seats=[
            Seat("wide", "stu-01", "Member1", 0.0, 0.0, 0.9, 0.9),
            Seat("tight", "stu-02", "Member2", 0.1, 0.1, 0.3, 0.3),
        ])
        assert m.seat_for_point(200, 200, 1000, 1000).seat_id == "tight"

    def test_boxes_are_normalised_so_the_plan_survives_a_resolution_change(self):
        m = _map()
        assert m.seat_for_point(150, 100, 1000, 500).seat_id == "seat-01"
        assert m.seat_for_point(300, 200, 2000, 1000).seat_id == "seat-01"

    def test_it_round_trips_through_json(self, tmp_path):
        original = _map()
        original.topic_code = "MATH-5-DECIMALS-L1"
        path = original.save(tmp_path / "seats.json")
        loaded = SeatMap.load(path)
        assert len(loaded.seats) == 2
        assert loaded.topic_code == "MATH-5-DECIMALS-L1"
        assert loaded.seats[0].student_external_id == "stu-01"

    def test_a_missing_plan_says_how_to_draw_one(self, tmp_path):
        with pytest.raises(SystemExit) as e:
            SeatMap.load(tmp_path / "nope.json")
        assert "calibrate.py" in str(e.value)


class TestSeatMapValidation:

    def test_a_seat_naming_an_unknown_child_is_caught_before_the_lesson(self):
        m = SeatMap(seats=[Seat("s1", "stu-99", "Ghost", 0, 0, 0.2, 0.2)])
        problems = m.validate({"stu-01": "Member1"})
        assert any("not on the class list" in p for p in problems)

    def test_one_child_in_two_seats_is_caught(self):
        m = SeatMap(seats=[
            Seat("s1", "stu-01", "Member1", 0.0, 0.0, 0.2, 0.2),
            Seat("s2", "stu-01", "Member1", 0.3, 0.0, 0.5, 0.2),
        ])
        problems = m.validate({"stu-01": "Member1"})
        assert any("more than one" in p for p in problems)

    def test_a_child_with_no_seat_is_reported_as_never_watched(self):
        m = SeatMap(seats=[Seat("s1", "stu-01", "Member1", 0, 0, 0.2, 0.2)])
        problems = m.validate({"stu-01": "Member1", "stu-02": "Member2"})
        assert any("never watched" in p for p in problems)

    def test_a_correct_plan_has_nothing_to_say(self):
        m = _map()
        assert m.validate({"stu-01": "Member1", "stu-02": "Member2"}) == []


# =============================================================================
# Drift becomes a flag
# =============================================================================

class TestDriftClock:
    """
    The rule the whole integration hangs on: a low score is not a flag. A low
    score that PERSISTS for longer than this child's own threshold is.
    """

    def _state(self, threshold_ms=5000):
        from cv.attention_monitor import SeatState
        return SeatState(seat=Seat("s1", "stu-01", "Member1", 0, 0, 1, 1),
                         threshold_ms=threshold_ms)

    def test_the_score_follows_the_reading_over_time_not_per_frame(self):
        s = self._state()
        for _ in range(30):
            s.blend_score(0.0, dt=0.05)
        assert s.score < 0.1

    def test_smoothing_is_time_based_so_frame_rate_does_not_change_it(self):
        """
        A frame-counted window means the same setting behaves differently on a
        fast laptop and a slow one. Half a second of looking away should read
        the same at 10fps and at 30fps.
        """
        slow = self._state()
        for _ in range(5):                     # 10 fps for 0.5s
            slow.blend_score(0.0, dt=0.1)
        fast = self._state()
        for _ in range(15):                    # 30 fps for 0.5s
            fast.blend_score(0.0, dt=1 / 30)
        assert abs(slow.score - fast.score) < 0.05

    def test_a_glance_away_is_not_long_enough_to_report(self):
        s = self._state(threshold_ms=5000)
        t = 100.0
        s.drift_started_at = t
        assert s.drift_ms(t + 1.5) < s.threshold_ms

    def test_a_sustained_drift_crosses_the_threshold(self):
        s = self._state(threshold_ms=5000)
        t = 100.0
        s.drift_started_at = t
        assert s.drift_ms(t + 6.0) >= s.threshold_ms

    def test_each_child_gets_their_own_threshold(self):
        """
        The reason the camera reads GET /students instead of hardcoding a
        number. A child who looks away frequently as self-regulation has a
        longer threshold, set by somebody who knows them.
        """
        patient = self._state(threshold_ms=8000)
        brisk = self._state(threshold_ms=4000)
        t = 100.0
        patient.drift_started_at = brisk.drift_started_at = t
        at = t + 5.0
        assert brisk.drift_ms(at) >= brisk.threshold_ms
        assert patient.drift_ms(at) < patient.threshold_ms

    def test_hysteresis_leaves_a_gap_so_a_child_on_the_line_cannot_flap(self):
        assert RECOVER_SCORE > DRIFT_SCORE

    def test_state_names_never_describe_a_feeling(self):
        s = self._state()
        assert s.state_name(0.0) == "attending"
        s.drift_started_at = 1.0
        assert s.state_name(2.0) == "drifting"
        s.flagged_this_drift = True
        assert s.state_name(2.0) == "flagged"
