"""
Turning two generated stills into one looping picture.

WHY THIS EXISTS
---------------
A still picture of a seed and a still picture of a sprout are two facts. The
change between them is the idea — that the seed became the sprout — and a
child who reads poorly gets that idea from watching it happen far more
cheaply than from a sentence saying so. Motion is not decoration here; it is
the part of the explanation that does not require reading.

WHY IT IS A CROSS-FADE AND NOT A VIDEO
--------------------------------------
A video model would give real motion. It would also cost a great deal more
per page and take one to three minutes to answer, and the lesson text is now
held back until the picture arrives — so a minute of generation is a minute
of a child sitting in front of a blank screen. Two stills and a dissolve cost
two image calls, land in seconds, cache like any other file, and carry a
before-and-after perfectly well. "Simple motion is enough" was the brief.

WHY IT IS SLOW AND SOFT
-----------------------
Every timing in here is deliberately gentle. Flashing and rapid alternation
between high-contrast frames is a seizure risk — WCAG 2.3.1 draws the line at
three flashes per second, and this loop runs nowhere near it: each frame is
held for over a second and the change between them is a slow dissolve of many
small steps. It is also the right choice for the children this is built for,
who mostly do not need something else on the screen demanding attention.
"""

from __future__ import annotations

import io
from pathlib import Path

# --- The loop, in numbers ----------------------------------------------------
# One cycle is: hold A, dissolve to B, hold B, dissolve back to A. Symmetric,
# so it loops seamlessly with no jump at the end.
HOLD_MS = 1100          # long enough to actually look at a frame
FADE_MS = 640           # slow enough to read as a change, not a cut
FADE_STEPS = 8          # 80ms a step: smooth, and few frames to encode

# The picture is displayed about 616px wide on the tablet this is built for.
# Encoding it much larger buys nothing a child can see and costs a download
# over a MiFi router, which is the connection this has to work on.
MAX_WIDTH = 720

# GIF is what was asked for and what this returns by default. It is also a
# 1987 format with a 256-colour palette, and a soft dissolve is exactly the
# content it is worst at: the file is several times larger than the same loop
# as WebP and the gradients band. Both are built by the same code path, so
# ILLUSTRATION_MOTION_FORMAT can be flipped in .env without touching this.
FORMATS = ("gif", "webp")


def _pil():
    """Imported lazily so a missing Pillow degrades to stills, not a 500."""
    from PIL import Image  # noqa: PLC0415
    return Image


def _load(data: bytes):
    Image = _pil()
    img = Image.open(io.BytesIO(data))
    img.load()
    return img.convert("RGB")


def _fit(img, other):
    """
    Both frames to one size.

    The second frame comes back from an image-editing model, and those do not
    reliably preserve dimensions. Cross-fading two different sizes raises; a
    quiet resize is the only sane response, and the frames are the same scene
    so nothing meaningful moves.
    """
    Image = _pil()
    if img.size != other.size:
        img = img.resize(other.size, Image.LANCZOS)
    return img


def _downscale(img):
    Image = _pil()
    if img.width <= MAX_WIDTH:
        return img
    height = round(img.height * MAX_WIDTH / img.width)
    return img.resize((MAX_WIDTH, height), Image.LANCZOS)


def cross_fade_frames(first: bytes, second: bytes) -> tuple[list, list[int]]:
    """
    The frame sequence and the duration of each, in milliseconds.

    Returned separately from the encoding so the timings can be asserted in a
    test without decoding a GIF.
    """
    Image = _pil()

    a = _downscale(_load(first))
    b = _fit(_downscale(_load(second)), a)

    frames = [a]
    durations = [HOLD_MS]

    def dissolve(start, end):
        # 1..FADE_STEPS-1: the endpoints are the held frames themselves, so
        # including them would show the same picture twice in a row.
        for i in range(1, FADE_STEPS):
            frames.append(Image.blend(start, end, i / FADE_STEPS))
            durations.append(FADE_MS // FADE_STEPS)

    dissolve(a, b)
    frames.append(b)
    durations.append(HOLD_MS)
    dissolve(b, a)

    return frames, durations


def build(first: bytes, second: bytes, *, fmt: str = "gif") -> tuple[bytes, str]:
    """
    One looping animation from two stills. Returns (bytes, mime).

    Raises only if Pillow is missing or the bytes are not images — callers
    treat that as "no animation" and fall back to the first still, because a
    lesson with a still picture is a lesson and a lesson with an error is not.
    """
    fmt = fmt.strip().lower()
    if fmt not in FORMATS:
        fmt = "gif"

    frames, durations = cross_fade_frames(first, second)
    buf = io.BytesIO()

    if fmt == "webp":
        frames[0].save(
            buf, format="WEBP", save_all=True, append_images=frames[1:],
            duration=durations, loop=0, quality=82, method=4,
        )
        return buf.getvalue(), "image/webp"

    # GIF. One shared adaptive palette across every frame, because a per-frame
    # palette makes the dissolve shimmer as the colours re-quantise underneath
    # it. Dithering is off: on a smooth gradient it adds noise the eye reads
    # as movement, which is the one thing this picture must not have going on
    # in the background.
    Image = _pil()
    palette = frames[0].quantize(colors=128, method=Image.MEDIANCUT)
    quantised = [f.quantize(palette=palette, dither=Image.NONE) for f in frames]
    quantised[0].save(
        buf, format="GIF", save_all=True, append_images=quantised[1:],
        duration=durations, loop=0, optimize=True, disposal=1,
    )
    return buf.getvalue(), "image/gif"


def write(path: Path, first: bytes, second: bytes, *, fmt: str = "gif") -> str:
    """Build and save in one step. Returns the mime type."""
    data, mime = build(first, second, fmt=fmt)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(data)
    return mime
