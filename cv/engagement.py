"""
Turning a face mesh into one number: is this child attending?

-----------------------------------------------------------------------------
WHY NOT IRIS POSITION ALONE
-----------------------------------------------------------------------------
The obvious signal is where the iris sits inside the eye. It is also, on its
own, the wrong one. A child who turns their whole head to the window keeps
their iris centred in the socket, so an iris-only score reads that child as
perfectly focused — and turning to look at something else is the single most
common way attention actually drifts in a classroom.

So the primary signal here is head pose, recovered with `cv2.solvePnP` from
six stable points on the mesh, and iris offset is the secondary signal that
catches the opposite case: a child facing forward whose eyes are on something
in their lap.

-----------------------------------------------------------------------------
WHY THE REFERENCE IS PER SEAT AND CAPTURED DELIBERATELY
-----------------------------------------------------------------------------
"Facing the board" is not the same direction for every child. A child on the
far left of the room is turned maybe twenty degrees right when they are
looking straight at the teacher, and a system that treats zero degrees as
"attending" would flag that child all lesson for sitting where they sit.

So each seat has its own reference direction, captured during a short
calibration at the start of the session while the class is asked to look at
the board. That is a deliberate act by a person, once, and it does not move
afterwards.

It is emphatically NOT a per-child baseline taken from whatever the child
happened to be doing on the first frame they were seen. That version resets
every time tracking blinks, and it silently redefines "attending" as whatever
the child was doing at an arbitrary moment.

-----------------------------------------------------------------------------
WHAT THIS FILE WILL NOT COMPUTE
-----------------------------------------------------------------------------
Anything about how a child feels. Head angle and eye position are geometry.
Emotion is not, and inferring it from a face is contested enough that no
teacher should be handed the result as fact. See app/models.py, FlagType.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field

import cv2
import numpy as np

# =============================================================================
# Tuning — every threshold in one block, deliberately
# =============================================================================

# Head yaw: left/right rotation, in degrees away from this seat's reference.
# Inside TOLERANCE the child is attending. At AWAY they are plainly looking
# somewhere else. Between the two the score falls off linearly.
YAW_TOLERANCE_DEG = 18.0
YAW_AWAY_DEG = 45.0

# Head pitch: up/down. Wider tolerance than yaw because writing in an exercise
# book is looking down, and looking down at your own work is attending.
PITCH_TOLERANCE_DEG = 16.0
PITCH_AWAY_DEG = 38.0

# Iris offset inside the eye, normalised by eye width. Only meaningful while
# the head is roughly forward; past a real head turn the eye geometry stops
# being readable and head pose carries the score on its own.
GAZE_TOLERANCE = 0.07
GAZE_AWAY = 0.22

# Below this score the child counts as drifting; they must climb back above
# RECOVER to count as attending again. The gap is hysteresis — without it a
# child hovering on the line generates a flag, a recovery and another flag
# every few seconds.
DRIFT_SCORE = 0.45
RECOVER_SCORE = 0.62

# Landmarks used for pose. Chosen because they barely move with expression:
# the corners of the eyes and mouth and the tip of the nose stay put whether
# a child is smiling, talking or still.
NOSE_TIP = 1
CHIN = 152
LEFT_EYE_OUTER = 33
RIGHT_EYE_OUTER = 263
LEFT_MOUTH = 61
RIGHT_MOUTH = 291

# A generic adult head in millimetres, origin at the nose tip. Absolute scale
# does not matter — only the ratios do, and they are close enough on a child's
# face that the recovered angles stay usable. We use the angles as a relative
# measure against a calibrated reference anyway, so a constant bias cancels.
_MODEL_POINTS = np.array([
    (0.0,    0.0,    0.0),      # nose tip
    (0.0,  -63.6,  -12.5),      # chin
    (-43.3,  32.7,  -26.0),     # left eye, outer corner
    (43.3,  32.7,  -26.0),      # right eye, outer corner
    (-28.9, -28.9,  -24.1),     # left mouth corner
    (28.9, -28.9,  -24.1),      # right mouth corner
], dtype=np.float64)

_POSE_INDICES = (NOSE_TIP, CHIN, LEFT_EYE_OUTER, RIGHT_EYE_OUTER,
                 LEFT_MOUTH, RIGHT_MOUTH)

# Iris centres, available because face_mesh runs with refine_landmarks=True.
LEFT_IRIS = 468
RIGHT_IRIS = 473
LEFT_EYE_INNER = 133
RIGHT_EYE_INNER = 362


@dataclass
class HeadPose:
    """Where the head is pointing, in degrees."""

    yaw: float        # + is the child's left
    pitch: float      # + is chin up
    roll: float
    ok: bool = True   # False when solvePnP could not converge


@dataclass
class Reading:
    """One frame's worth of attention evidence for one child."""

    score: float                 # 0.0 (fully away) to 1.0 (attending)
    yaw_off: float               # degrees from this seat's reference
    pitch_off: float
    gaze_off: float              # normalised iris deviation
    pose: HeadPose
    dominant: str                # 'head' or 'gaze' — which drove the score
    face_height_px: int = 0      # how big the face was; feeds confidence


@dataclass
class SeatReference:
    """
    What "looking at the board" means for one seat.

    Collected over a few seconds during calibration and then frozen. Stored as
    the median rather than the mean because a child who glances away mid
    calibration should not drag the reference with them.
    """

    yaw: float = 0.0
    pitch: float = 0.0
    gaze_l: float = 0.5
    gaze_r: float = 0.5
    samples: int = 0
    _yaws: list = field(default_factory=list)
    _pitches: list = field(default_factory=list)
    _gl: list = field(default_factory=list)
    _gr: list = field(default_factory=list)

    def add_sample(self, pose: HeadPose, gaze_l: float, gaze_r: float) -> None:
        if not pose.ok:
            return
        self._yaws.append(pose.yaw)
        self._pitches.append(pose.pitch)
        self._gl.append(gaze_l)
        self._gr.append(gaze_r)
        self.samples += 1

    def finalise(self) -> bool:
        """Freeze the reference. False if too few samples to trust it."""
        if self.samples < 10:
            return False
        self.yaw = float(np.median(self._yaws))
        self.pitch = float(np.median(self._pitches))
        self.gaze_l = float(np.median(self._gl))
        self.gaze_r = float(np.median(self._gr))
        return True

    def to_dict(self) -> dict:
        return {"yaw": round(self.yaw, 2), "pitch": round(self.pitch, 2),
                "gaze_l": round(self.gaze_l, 4), "gaze_r": round(self.gaze_r, 4),
                "samples": self.samples}

    @classmethod
    def from_dict(cls, d: dict) -> "SeatReference":
        ref = cls()
        ref.yaw = float(d.get("yaw", 0.0))
        ref.pitch = float(d.get("pitch", 0.0))
        ref.gaze_l = float(d.get("gaze_l", 0.5))
        ref.gaze_r = float(d.get("gaze_r", 0.5))
        ref.samples = int(d.get("samples", 0))
        return ref


# =============================================================================
# Head pose
# =============================================================================

def estimate_head_pose(landmarks_px: list[tuple[int, int]],
                       frame_w: int, frame_h: int) -> HeadPose:
    """
    Recover yaw, pitch and roll from six mesh points.

    The camera matrix is the usual pinhole approximation: focal length equal
    to the frame width, principal point at the centre. It is not a calibrated
    camera and it does not need to be — every angle here is compared against a
    reference captured through the same lens, so the approximation cancels.
    """
    try:
        image_points = np.array(
            [landmarks_px[i] for i in _POSE_INDICES], dtype=np.float64
        )
    except (IndexError, TypeError):
        return HeadPose(0.0, 0.0, 0.0, ok=False)

    focal = float(frame_w)
    camera_matrix = np.array([
        [focal, 0, frame_w / 2.0],
        [0, focal, frame_h / 2.0],
        [0, 0, 1],
    ], dtype=np.float64)
    dist_coeffs = np.zeros((4, 1))

    ok, rotation_vec, _ = cv2.solvePnP(
        _MODEL_POINTS, image_points, camera_matrix, dist_coeffs,
        flags=cv2.SOLVEPNP_ITERATIVE,
    )
    if not ok:
        return HeadPose(0.0, 0.0, 0.0, ok=False)

    rotation_mat, _ = cv2.Rodrigues(rotation_vec)
    # RQDecomp3x3 returns the Euler angles directly and handles the gimbal
    # bookkeeping that doing this by hand gets wrong near ±90°.
    angles = cv2.RQDecomp3x3(rotation_mat)[0]
    pitch, yaw, roll = float(angles[0]), float(angles[1]), float(angles[2])

    # Pitch comes out near ±180 for a forward-facing head; fold it to 0.
    if pitch > 90:
        pitch -= 180
    elif pitch < -90:
        pitch += 180

    return HeadPose(yaw=yaw, pitch=pitch, roll=roll, ok=True)


# =============================================================================
# Gaze
# =============================================================================

def eye_gaze(landmarks_px: list[tuple[int, int]],
             iris_idx: int, outer_idx: int, inner_idx: int) -> float:
    """
    Where the iris sits across the eye, 0.0 at one corner and 1.0 at the other.

    Roughly 0.5 when looking straight ahead. Returns 0.5 for a degenerate eye
    so a bad frame reads as neutral rather than as maximum deviation.
    """
    try:
        iris_x = landmarks_px[iris_idx][0]
        outer_x = landmarks_px[outer_idx][0]
        inner_x = landmarks_px[inner_idx][0]
    except (IndexError, TypeError):
        return 0.5

    eye_width = abs(inner_x - outer_x)
    if eye_width < 2:
        return 0.5
    return (iris_x - min(outer_x, inner_x)) / eye_width


# =============================================================================
# Scoring
# =============================================================================

def _axis_score(offset: float, tolerance: float, away: float) -> float:
    """1.0 inside tolerance, 0.0 at `away`, straight line between."""
    if offset <= tolerance:
        return 1.0
    if offset >= away:
        return 0.0
    return 1.0 - (offset - tolerance) / (away - tolerance)


def score_reading(landmarks_px: list[tuple[int, int]],
                  reference: SeatReference,
                  frame_w: int, frame_h: int,
                  face_height_px: int = 0) -> Reading:
    """
    Score one frame against this seat's reference direction.

    The three axes are combined with `min`, not an average. If a child has
    turned their head forty degrees it does not matter where their eyes are
    pointing inside that turned head — the worst axis is the honest answer,
    and averaging would let a good gaze score hide a plain head turn.
    """
    pose = estimate_head_pose(landmarks_px, frame_w, frame_h)

    gl = eye_gaze(landmarks_px, LEFT_IRIS, LEFT_EYE_OUTER, LEFT_EYE_INNER)
    gr = eye_gaze(landmarks_px, RIGHT_IRIS, RIGHT_EYE_OUTER, RIGHT_EYE_INNER)
    gaze_off = (abs(gl - reference.gaze_l) + abs(gr - reference.gaze_r)) / 2.0

    if pose.ok:
        yaw_off = abs(pose.yaw - reference.yaw)
        pitch_off = abs(pose.pitch - reference.pitch)
        yaw_score = _axis_score(yaw_off, YAW_TOLERANCE_DEG, YAW_AWAY_DEG)
        pitch_score = _axis_score(pitch_off, PITCH_TOLERANCE_DEG, PITCH_AWAY_DEG)
        head_score = min(yaw_score, pitch_score)
    else:
        # Pose failed. Fall back to gaze alone rather than inventing a number,
        # and let the caller see ok=False so confidence drops accordingly.
        yaw_off = pitch_off = 0.0
        head_score = 1.0

    gaze_score = _axis_score(gaze_off, GAZE_TOLERANCE, GAZE_AWAY)

    score = min(head_score, gaze_score)
    dominant = "head" if head_score <= gaze_score else "gaze"

    return Reading(
        score=float(score),
        yaw_off=float(yaw_off),
        pitch_off=float(pitch_off),
        gaze_off=float(gaze_off),
        pose=pose,
        dominant=dominant,
        face_height_px=face_height_px,
    )


def flag_type_for(reading: Reading) -> str:
    """
    Which of our flag types this drift actually is.

    A turned head and eyes that have wandered are different events and the
    teacher's queue should say which happened. Both are geometry; neither is a
    claim about the child.
    """
    return "head_turn" if reading.dominant == "head" else "gaze_away"


def confidence_for(mesh_coverage: float, face_height_px: int,
                   worst_score: float, frame_h: int) -> float:
    """
    How much we trust this detection, as a real number between 0 and 1.

    `docs/CV_INTEGRATION.md` is explicit that we must not invent a fake 1.0,
    and the backend stores anything below FLAG_MIN_CONFIDENCE without queueing
    it, so this number decides what a teacher is asked to look at. Three
    honest inputs:

      mesh_coverage  what fraction of the drift window we could actually read
                     a face for. A drift we only half saw is a weaker claim.
      face size      a face 30 pixels tall gives noisy landmarks; one 150
                     pixels tall gives good ones.
      depth          how far below the drift threshold the score sat. Barely
                     over the line is a weaker claim than plainly away.
    """
    coverage = max(0.0, min(1.0, mesh_coverage))
    size = max(0.0, min(1.0, face_height_px / max(1.0, frame_h * 0.12)))
    depth = max(0.0, min(1.0, (DRIFT_SCORE - worst_score) / max(1e-6, DRIFT_SCORE)))

    conf = 0.50 * coverage + 0.30 * size + 0.20 * depth
    return round(max(0.0, min(1.0, conf)), 3)
