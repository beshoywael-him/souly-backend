"""
The shape of what a child is shown, as opposed to whether it is served.

test_learning.py proves the endpoints answer. These tests are about the two
things that made a real lesson unusable on screen:

  1. the two columns saying the same thing twice
  2. the picture prompt asking for decoration instead of explanation

Both were caught by a person looking at the screen, not by a test. These are
the tests that would have caught them first.
"""

import io

import pytest

from app.services import animation, llm, tutor


# =============================================================================
# The two columns must not say the same thing
# =============================================================================

# Verbatim from the page that prompted the fix: a science page whose step
# notes were the explanation chunks reworded, sitting side by side with them.
CHUNKS = [
    "Place three seeds in a paper towel, fold it over them, and put it on a plate.",
    "Plant the other three seeds in a cup of soil and water them.",
    "After seven days, the growth in the paper towel is similar to the growth in the soil.",
]


def test_a_note_that_repeats_the_explanation_is_dropped():
    visual = {"kind": "steps", "items": [
        {"label": "Paper towel setup",
         "note": "Place three seeds in folded towel on a plate"},
    ]}
    out = tutor._drop_echoed_notes(visual, CHUNKS)
    assert out["items"][0]["note"] == ""
    # The label survives. An unlabelled step is worse than a repeated one.
    assert out["items"][0]["label"] == "Paper towel setup"


def test_a_note_that_gives_a_reason_is_kept():
    visual = {"kind": "steps", "items": [
        {"label": "Seeds in a paper towel",
         "note": "You can watch the root come out"},
        {"label": "Measure after seven days",
         "note": "Waiting is what makes it a fair test"},
    ]}
    out = tutor._drop_echoed_notes(visual, CHUNKS)
    assert out["items"][0]["note"] == "You can watch the root come out"
    assert out["items"][1]["note"] == "Waiting is what makes it a fair test"


def test_shared_stopwords_alone_do_not_count_as_a_repeat():
    """"the", "and", "a" overlapping is not the same as saying the same thing."""
    visual = {"kind": "steps", "items": [
        {"label": "Sunlight", "note": "Nothing sprouts in the dark"},
    ]}
    out = tutor._drop_echoed_notes(visual, CHUNKS)
    assert out["items"][0]["note"] == "Nothing sprouts in the dark"


def test_labelled_parts_notes_are_checked_too():
    chunks = ["The roots take in water from the soil."]
    visual = {"kind": "labelled_parts", "items": [
        {"label": "Roots", "note": "Take in water from the soil"},
        {"label": "Leaves", "note": "Catch the sunlight"},
    ]}
    out = tutor._drop_echoed_notes(visual, chunks)
    assert out["items"][0]["note"] == ""
    assert out["items"][1]["note"] == "Catch the sunlight"


def test_no_chunks_and_no_visual_are_both_survivable():
    assert tutor._drop_echoed_notes(None, CHUNKS) is None
    visual = {"kind": "steps", "items": [{"label": "A", "note": "B"}]}
    assert tutor._drop_echoed_notes(visual, [])["items"][0]["note"] == "B"


def test_a_visual_with_no_items_passes_through():
    visual = {"kind": "number_line", "marks": [{"value": 1}]}
    assert tutor._drop_echoed_notes(visual, CHUNKS) == visual


# =============================================================================
# The picture prompt must ask for something a child can read
# =============================================================================

def test_the_illustration_style_bans_decoration():
    """
    The first version produced purple blobs, a mug with hearts on it and a
    paper towel drawn as a rounded rectangle — an adult could not tell what
    was in the picture. These clauses are what stops that, so they are worth
    a test: they are easy to soften by accident while editing the prose
    around them.
    """
    style = llm.ILLUSTRATION_STYLE.lower()
    for banned in ("blobs", "swooshes", "hearts", "sparkles",
                   "decorative", "ornamental"):
        assert banned in style, f"the style prompt no longer rules out {banned}"


def test_the_illustration_style_still_forbids_text_and_people():
    """
    Predates the rewrite and must survive it. Every number and label this app
    shows is drawn in HTML on top of the picture, because a generated "0.45"
    comes back as something that is nearly 0.45 and the child reads that as
    their own mistake.
    """
    style = llm.ILLUSTRATION_STYLE.lower()
    assert "no text" in style and "no letters" in style and "no numbers" in style
    assert "no people" in style


def test_the_illustration_style_states_the_recognition_test():
    """The goal, not just the prohibitions — the model composes better
    against something to achieve than against a list of things to avoid."""
    assert "name every object" in llm.ILLUSTRATION_STYLE.lower()


def test_the_scene_brief_asks_for_arrangement_not_a_single_noun():
    """
    "seed growing" gave the model nothing and it filled the gap with
    decoration. The instruction now asks what is in the picture and where.
    """
    text = tutor.VISUAL_INSTRUCTION.lower()
    assert "on the left" in text and "on the right" in text
    assert "no style" in text


def test_the_scene_word_budget_allows_a_described_arrangement():
    """
    Naming two objects and where each sits does not fit in ten words. The
    cleaner used to cut those scenes off, which is how "seed growing" became
    the norm.
    """
    scene = ("on the left a sprouted bean seed on a damp white paper towel on "
             "a plate, on the right the same seedling in a cup of dark soil")
    spec = tutor._clean_visual({
        "kind": "illustration", "purpose": "Compare the two.", "scene": scene,
    })
    assert spec is not None and spec.get("scene") == scene


def test_a_scene_with_digits_in_it_is_still_refused():
    """Unchanged and load-bearing: the image model renders digits, and it
    renders them wrong."""
    spec = tutor._clean_visual({
        "kind": "illustration", "purpose": "Show it.",
        "scene": "a ruler showing 3 centimetres beside a seedling",
    })
    assert spec is None or not spec.get("scene")


# =============================================================================
# The picture that moves
# =============================================================================

def _still(shoot: bool) -> bytes:
    """A stand-in frame. Two of these differ in one thing, like the real pair."""
    Image = pytest.importorskip("PIL.Image")
    from PIL import ImageDraw
    im = Image.new("RGB", (1024, 576), "#f6f3ec")
    d = ImageDraw.Draw(im)
    d.rounded_rectangle((600, 300, 860, 500), 30, fill="#b07a4e")
    if shoot:
        d.rectangle((722, 180, 738, 330), fill="#5d8c3a")
    buf = io.BytesIO()
    im.save(buf, "PNG")
    return buf.getvalue()


def test_the_loop_holds_each_frame_and_dissolves_between_them():
    """
    The timings are a safety property, not a preference. Rapid alternation
    between two high-contrast frames is a seizure risk — WCAG 2.3.1 draws the
    line at three flashes a second. Each frame here is held for over a second
    and the change is spread over many small steps, which is nowhere near it.
    """
    frames, durations = animation.cross_fade_frames(_still(False), _still(True))
    assert len(frames) == len(durations)
    assert durations[0] >= 1000, "the first frame is not held long enough to read"
    assert max(durations) >= 1000 and min(durations) <= 120
    # Two holds and two dissolves — symmetric, so the loop does not jump.
    assert sum(1 for d in durations if d >= 1000) == 2
    assert sum(durations) > 2500, "the whole loop is faster than a glance"


def test_the_loop_is_built_and_repeats_forever():
    Image = pytest.importorskip("PIL.Image")
    for fmt, expected in (("gif", "image/gif"), ("webp", "image/webp")):
        data, mime = animation.build(_still(False), _still(True), fmt=fmt)
        assert mime == expected
        img = Image.open(io.BytesIO(data))
        assert getattr(img, "is_animated", False), f"{fmt} came out static"
        assert img.n_frames == len(
            animation.cross_fade_frames(_still(False), _still(True))[0])
        assert img.info.get("loop") == 0, f"{fmt} does not loop forever"


def test_frames_of_different_sizes_are_reconciled():
    """
    The second frame comes back from an image-editing model, and those do not
    reliably return the size they were given. Raising on that would cost the
    child the picture over something invisible to them.
    """
    Image = pytest.importorskip("PIL.Image")
    from PIL import ImageDraw
    small = Image.new("RGB", (640, 360), "#eee")
    ImageDraw.Draw(small).rectangle((10, 10, 100, 100), fill="#333")
    buf = io.BytesIO()
    small.save(buf, "PNG")
    data, _ = animation.build(_still(False), buf.getvalue(), fmt="webp")
    assert len(data) > 0


def test_decorative_motion_is_refused():
    """
    "Make it move" reads to a language model as "make it lively", and the
    result is an ambient wobble: a second image call spent on a distraction,
    on a screen used by children who cannot easily look away from one.
    """
    for wobble in ("the leaves sway gently in the breeze",
                   "the picture shimmers softly",
                   "a gentle glow pulses around the pot",
                   "the camera slowly zooms in"):
        spec = tutor._clean_visual({
            "kind": "illustration", "purpose": "Show it.",
            "scene": "a green seedling in a brown clay pot of dark soil",
            "motion": wobble,
        })
        assert not spec.get("motion"), f"accepted decorative motion: {wobble!r}"


def test_meaningful_motion_is_kept():
    spec = tutor._clean_visual({
        "kind": "illustration", "purpose": "Watch it grow.",
        "scene": "a bean seed in a brown clay cup of dark soil",
        "motion": "the seed has split open and a green shoot has risen from it",
    })
    assert spec["motion"].startswith("the seed has split open")


def test_the_cache_key_covers_the_motion_as_well_as_the_scene():
    """
    Two pages can want the same picture and different changes in it. Keying
    on the scene alone would serve the first page's animation to the second.
    """
    scene = "a bean seed in a brown clay cup of dark soil"
    a = tutor._clean_visual({"kind": "illustration", "purpose": "p",
                             "scene": scene,
                             "motion": "a green shoot has risen from the seed"})
    b = tutor._clean_visual({"kind": "illustration", "purpose": "p",
                             "scene": scene,
                             "motion": "the soil has turned dark with water"})
    still = tutor._clean_visual({"kind": "illustration", "purpose": "p",
                                 "scene": scene})
    assert a["key"] != b["key"] != still["key"] != a["key"]


def test_motion_survives_a_diagram_that_would_not_validate():
    """
    A malformed diagram falls back to the picture alone. The picture must keep
    its motion on the way through, or every page with a bad diagram silently
    loses its animation.
    """
    spec = tutor._clean_visual({
        "kind": "bar_compare", "purpose": "Compare them.",
        "scene": "two clay pots of dark soil side by side on a table",
        "motion": "a green shoot has risen from the pot on the left",
        "bars": [{"label": "only one", "value": 1}],     # too few, invalid
    })
    assert spec["kind"] == "none"
    assert spec["motion"].startswith("a green shoot")
