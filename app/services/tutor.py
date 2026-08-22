"""
Souly's tutoring brain — the lesson's hint layer.

This is not a chatbot module. Every function here is anchored to something the
student is currently looking at: a page of the book, or a question they just
got wrong. That anchoring is the whole design.

Six jobs:

    rendition()        write THIS page as a lesson, for THIS child
    explain()          re-explain the page a different way
    hint()             the four-tier ladder on a question
    generate_questions()  build practice items from the page
    answer()           free-form question, grounded, still hint-shaped
    should_offer_help()   has this student stalled?

---------------------------------------------------------------------------
CANON, RENDITION, PATH
---------------------------------------------------------------------------
Since schema_v5 the content is a Ministry PDF and nothing here paraphrases it
into the database. Three layers:

    canon      the PDF page.     Nobody writes it. It stays on disk.
    rendition  the explanation.  This module writes it, per child, from the
                                 page — cached in `page_renditions`.
    path       order and pacing. A deterministic policy over mastery.

The canon is frozen because a hallucinated step in a maths procedure is
invisible to a child who is already struggling: they assume they are the one
who is wrong. Every function below that talks to the model is handed the
page's text as grounding and told it may not go beyond it. When there is no
text for a page — never ingested, or the OCR came back empty — the answer is
to say so, not to let the model fill the silence.

---------------------------------------------------------------------------
WHY HELP IS OFFERED, NOT WAITED FOR
---------------------------------------------------------------------------
Grainger, Williams & Lind (2016) found autistic children's confidence
judgements are significantly less accurate, and that they "used monitoring to
influence control processes significantly less than neurotypical children."

Plainly: the link between not understanding and doing something about it is
weaker. A button saying "tap if you're stuck" gets pressed by the students who
need it least.

So `should_offer_help()` exists, and the UI calls it. Souly speaks first.
---------------------------------------------------------------------------
"""

import hashlib
import json
import re
import sqlite3
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import Any

from app import economy
from app.services import curriculum, llm, rag

# The four tiers. A student can stop at any of them; Souly may never skip
# ahead. Tier 4 exists so a child is never trapped — but it arrives last.
HINT_TIERS = {
    1: "nudge",
    2: "worked",
    3: "stepwise",
    4: "answer",
}

TIER_INSTRUCTIONS = {
    1: ("Give the smallest possible nudge. Point at ONE thing to look at again. "
        "One or two short sentences. Do not explain the method. Do not give the answer."),
    2: ("Show a similar worked example with DIFFERENT numbers or details, solved "
        "all the way through. Then say 'now try yours'. Never solve their actual "
        "question."),
    3: ("Walk through the method one step at a time for THEIR question, but stop "
        "before the final step and ask them to finish it. Do not state the answer."),
    4: ("Give the answer and explain clearly why it is right, in a way that helps "
        "next time. Be warm about it — they worked for this."),
}

# How long a child must be idle before Souly offers help, as a multiple of
# that child's own median step time.
#
# Per-child rather than a global constant on purpose: Zapparrata et al. (2023)
# meta-analysed 44 studies and found autistic people are slower across the
# board (g = .35). A fixed "15 seconds means stuck" threshold would mark this
# entire cohort as permanently stuck.
STALL_MULTIPLIER = 2.5
STALL_FLOOR_SECONDS = 25
STALL_CEILING_SECONDS = 180

# Readability limits on generated practice, enforced in _validate_question().
#
# The children this is for have learning disabilities and are working in
# English. A long question measures their reading rather than the maths, and
# "all of the above" measures whether they have met that convention before.
# The generation prompt asks for short and plain; these are the check, because
# models drift long.
# The lesson a child actually reads. Hard limits, enforced in code, because
# the instruction asked for short and got thirteen lines of unbroken prose in
# front of a child with a reading difficulty.
#
# Three chunks of about twenty words is roughly what fits on a tablet without
# scrolling, and roughly what this cohort will read before giving up. A
# lesson that is not read is not a lesson.
# Bump this and every cached rendition is rewritten on next view.
RENDITION_VERSION = "v3-picture-required"

MAX_LESSON_CHUNKS = 3
MAX_CHUNK_WORDS = 28

MAX_PROMPT_WORDS = 18
MAX_OPTION_WORDS = 8
BANNED_PHRASES = (
    "all of the above",
    "none of the above",
    "which of the following",
    "both a and b",
)


@dataclass
class TutorReply:
    text: str
    engine: str
    latency_ms: int
    source_refs: list[dict] = field(default_factory=list)
    grounded: bool = False
    award: dict[str, Any] | None = None
    suggested_mode: str | None = None
    suggested_topic_id: int | None = None
    tier: int | None = None
    next_tier: int | None = None
    cached: bool = False
    error: str | None = None
    # The picture that goes with the lesson, as a spec the app draws. See
    # llm.LESSON_SCHEMA for why it is a spec and not an image or raw SVG.
    visual: dict | None = None
    # What was changed for THIS child, in words a person can check against
    # the lesson in front of them. Without this, "it adapts to the learner" is
    # an assertion nobody can test — which is how it went a month without
    # anyone noticing it had stopped being true.
    adapted_for: list[str] = field(default_factory=list)


# =============================================================================
# Student context
# =============================================================================

def load_profile(conn: sqlite3.Connection, student_id: int) -> dict:
    """
    Everything the LLM should know about this child before it writes a word.

    Two sources, deliberately distinct:

      * `students`          — the declared support profile (autism, ADHD…),
                              set by an adult
      * `learner_profiles`  — what the entry activity MEASURED: how much
                              scaffolding they needed, whether they understood
                              the spoken story but not the written one, what
                              they're interested in

    The second is the one that changes the pitch, and it carries a confidence
    number so the prompt can say "provisional" rather than asserting it.
    """
    row = conn.execute(
        "SELECT display_name, full_name, grade, support_profile, support_notes "
        "FROM students WHERE id = ?",
        (student_id,),
    ).fetchone()
    profile = dict(row) if row else {}

    learner = conn.execute(
        "SELECT * FROM v_current_learner_profile WHERE student_id = ?",
        (student_id,),
    ).fetchone()

    if learner:
        profile["instruction_need"] = learner["instruction_need"]
        profile["profile_confidence"] = learner["confidence"]
        profile["modality_gap"] = learner["modality_gap"]
        profile["possible_masking"] = bool(learner["possible_masking"])
        try:
            profile["interests"] = ", ".join(json.loads(learner["interests"] or "[]"))
        except (json.JSONDecodeError, TypeError):
            profile["interests"] = ""

    return profile


def available_topics(conn: sqlite3.Connection, limit: int = 4) -> list[str]:
    """
    Titles of lessons Souly can actually teach right now.

    Used when a question has no match, so "try asking me about X" names
    something that genuinely exists. Hardcoding examples here is how you end up
    suggesting a lesson that was deleted three weeks ago.

    A topic IS a lesson in a real book since schema_v5, so this asks the only
    question that matters: which lessons have pages in a verified book?
    """
    rows = conn.execute(
        """
        SELECT DISTINCT t.title FROM topics t
        JOIN curriculum_books b ON b.id = t.book_id
        JOIN curriculum_pages p ON p.book_id = b.id AND p.lesson = t.lesson_label
        WHERE t.is_verified = 1 AND b.is_verified = 1
        ORDER BY t.sort_order LIMIT ?
        """,
        (limit,),
    ).fetchall()
    return [r["title"] for r in rows]


# A conversation is what happened in one sitting. Two messages further apart
# than this belong to different sittings, and carrying yesterday's chat into
# today's lesson makes Souly answer questions nobody asked.
SESSION_GAP_MINUTES = 45

# How many turns the model sees. Deep enough that a child can say "and the
# other one?" three exchanges later and still be understood.
HISTORY_TURNS = 12


def load_history(conn: sqlite3.Connection, student_id: int,
                 limit: int = HISTORY_TURNS) -> list[dict]:
    """
    The current sitting's turns, oldest first.

    Bounded by TIME rather than by count alone. The old version took the last
    eight rows whenever they happened, so a child returning the next morning
    got a model still mid-conversation about yesterday — and a child three
    questions into today lost the start of it.
    """
    rows = conn.execute(
        "SELECT role, content, created_at FROM chat_messages "
        "WHERE student_id = ? AND role IN ('student','souly') "
        "ORDER BY id DESC LIMIT ?",
        (student_id, max(limit * 2, 24)),
    ).fetchall()

    turns: list[dict] = []
    previous: datetime | None = None

    for row in rows:                       # newest first
        stamp = _parse_stamp(row["created_at"])
        if previous is not None and stamp is not None:
            if (previous - stamp) > timedelta(minutes=SESSION_GAP_MINUTES):
                break                      # a gap this long ends the sitting
        previous = stamp or previous
        turns.append({"role": row["role"], "content": row["content"]})
        if len(turns) >= limit:
            break

    return list(reversed(turns))


def _parse_stamp(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        return datetime.strptime(value, "%Y-%m-%dT%H:%M:%SZ")
    except (ValueError, TypeError):
        return None


def _log_help(conn: sqlite3.Connection, student_id: int, help_type: str, *,
              tier: int | None = None, page_id: int | None = None,
              question_id: int | None = None, quiz_id: int | None = None,
              initiated_by: str = "student", attempts_before: int = 0,
              seconds_before: int = 0, student_answer: str | None = None,
              response_text: str = "", engine: str = "", latency_ms: int = 0) -> int:
    """
    Append to hint_requests. Returns the row id so it can be resolved later.

    `page_id` replaces schema_v3's `lesson_step_id`, which pointed at the
    invented lesson_steps table. The old column is still on hint_requests and
    is left NULL forever — see schema_v5 section 8e.
    """
    return conn.execute(
        """
        INSERT INTO hint_requests (
            student_id, page_id, question_id, quiz_id, help_type, tier,
            initiated_by, attempts_before, seconds_before, student_answer,
            response_text, engine, latency_ms, created_at
        ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)
        """,
        (student_id, page_id, question_id, quiz_id, help_type, tier,
         initiated_by, attempts_before, seconds_before, student_answer,
         response_text, engine, latency_ms, economy.utc_now()),
    ).lastrowid


# =============================================================================
# 1. Renditions — turning a page of the book into a lesson for one child
# =============================================================================

MODE_INSTRUCTIONS = {
    # Deliberately does NOT end with "look at the page". The child is not
    # holding the book — they have your words and the picture beside them —
    # and for a child with a visual impairment that instruction is a locked
    # door. It also contradicted the delivery rule further down, and a model
    # given two contradicting instructions follows the first one.
    "lesson": ("Teach this page to the child. Walk through what it covers in "
               "order, in your own words, as if you were sitting next to them. "
               "Every fact must come from the page — you may change HOW it is "
               "said, never WHAT it says. If the page shows a worked example, "
               "walk through that example. End with ONE short question that "
               "checks they followed."),
    "simpler": ("The child did not understand this page. Say the SAME idea again "
                "using easier words and shorter sentences. Do not add new "
                "information. Do not skip ahead."),
    "example": ("Give ONE concrete everyday example of the idea on this page. "
                "Something a child would recognise — food, toys, family, school. "
                "Do not teach anything the page does not cover."),
    "another_way": ("Explain the SAME idea from a different angle. If the page used "
                    "objects, try actions. If it used numbers, try a picture in "
                    "words. Same content, different door in."),
}


# =============================================================================
# Differentiation — the part that makes two children get two lessons
#
# The profile was already being handed to the model, in llm.py's system block.
# It described the child accurately and changed almost nothing, because a
# description is not an instruction: told "this child needs less scaffolding"
# and then told "teach this page", the model teaches the page and adjusts a
# few adjectives. Two children came out with the same lesson in different
# words.
#
# What follows turns the profile into a different SHAPE of lesson — a
# different order of moves — and reports which moves it picked, so "it adapts
# to the child" is checkable rather than asserted.
# =============================================================================

# The order of moves, by what the entry activity measured about how much
# scaffolding this child needed. Veerbeek & Vogelaar (2025): the
# task_specific group scored significantly lower on standardised maths and
# reading, so the split tracks something real — and the intervention that
# follows from it is the ORDER you do things in, not the tone.
STRUCTURE_BY_NEED = {
    "task_specific": (
        "STRUCTURE — follow this order exactly:\n"
        "1. Say what the idea is, in one short sentence.\n"
        "2. Work through ONE example from the page, showing every step and "
        "saying out loud why each step happens.\n"
        "3. Then, and only then, ask them one question about it.\n"
        "Do not open with a question. This child needs the idea itself "
        "re-taught before a question means anything to them.",
        "worked example first",
    ),
    "metacognitive": (
        "STRUCTURE — follow this order exactly:\n"
        "1. Ask them what they would look at first on this page.\n"
        "2. Name the strategy that works here, in one sentence.\n"
        "3. Then show it working on the page's example.\n"
        "This child rarely needs the content re-taught — they need a nudge "
        "about HOW to approach it.",
        "strategy first",
    ),
    "low": (
        "STRUCTURE — follow this order exactly:\n"
        "1. Open with the question the page is really asking.\n"
        "2. Give the shortest explanation that answers it.\n"
        "3. Stop. Do not add a second example unless the page has one.\n"
        "This child solves things with very little help, and for a competent "
        "learner extra scaffolding actively gets in the way "
        "(Kalyuga's expertise reversal). Say LESS.",
        "straight to the point",
    ),
}

# How it is delivered, by the support profile an adult set. Same content,
# different shape of sentence.
DELIVERY_BY_SUPPORT = {
    "autism": (
        "DELIVERY: Be literal and concrete. No idioms, no sarcasm, no "
        "rhetorical questions. Use the SAME words for the same idea every "
        "time — if you called it 'the bottom number' once, never switch to "
        "'the denominator' later. Say what is coming before you say it.",
        "literal and predictable",
    ),
    "adhd": (
        "DELIVERY: Three short chunks, no more. The point goes in the FIRST "
        "sentence, before any setup. Put a one-line question between chunks "
        "so there is something to do, not just something to read. Nothing "
        "over two sentences without a break.",
        "short chunks",
    ),
    "dyslexia": (
        "DELIVERY: This is read aloud to them. Short common words, short "
        "sentences, and never ask them to notice how a word is spelled. "
        "Prefer saying a number to writing it in words.",
        "made for listening",
    ),
    "hearing_impairment": (
        "DELIVERY: They read your words on screen rather than hearing them. "
        "Punctuate clearly and keep each sentence self-contained.",
        "clear on screen",
    ),
    "speech_impairment": (
        "DELIVERY: Any question you ask must be answerable in one or two "
        "words. Never require a spoken explanation.",
        "short answers only",
    ),
    "visual_impairment": (
        "DELIVERY: Never say 'look at', 'as you can see', or refer to where "
        "something sits on the page. Describe every diagram fully in words, "
        "including what it shows and why. The explanation has to stand on its "
        "own with the screen switched off.",
        "described in words",
    ),
}


def _profile_signature(profile: dict, mastery: float) -> str:
    """
    A short fingerprint of everything that shaped this lesson.

    Stored on the cached rendition so the cache invalidates itself when what
    we know about the child changes — otherwise the first lesson a child ever
    saw, written before they had done the entry activity, is the one they keep
    getting forever.
    """
    parts = [
        # Bumped whenever the SHAPE of a rendition changes, so every cached
        # lesson regenerates once. Without this, a child keeps being served a
        # lesson written before the picture was mandatory — the cache is
        # working exactly as designed and the fix never reaches them.
        RENDITION_VERSION,
        str(profile.get("instruction_need") or "-"),
        str(profile.get("support_profile") or "-"),
        str(profile.get("interests") or "-"),
        "gap" if (profile.get("modality_gap") or 0) > 0 else "-",
        "mask" if profile.get("possible_masking") else "-",
        # Banded, not raw: a lesson should not regenerate because mastery
        # moved by a hundredth.
        "m2" if mastery > 0.7 else ("m1" if mastery > 0.35 else "m0"),
    ]
    return "|".join(parts)


def _lesson_instruction(profile: dict, mastery: float) -> tuple[str, list[str]]:
    """
    Build the teaching instruction for THIS child.

    Returns the instruction and a short list of what was adapted, in words a
    person can check against the lesson they are looking at.
    """
    blocks = [MODE_INSTRUCTIONS["lesson"]]
    adapted: list[str] = []

    structure, label = STRUCTURE_BY_NEED.get(
        profile.get("instruction_need") or "",
        (
            "STRUCTURE — follow this order exactly:\n"
            "1. Say what the idea is.\n"
            "2. Work through the page's example.\n"
            "3. Ask them one question about it.",
            "worked example first",
        ),
    )
    blocks.append(structure)
    adapted.append(label)

    delivery = DELIVERY_BY_SUPPORT.get(profile.get("support_profile") or "")
    if delivery:
        blocks.append(delivery[0])
        adapted.append(delivery[1])

    interests = (profile.get("interests") or "").strip()
    if interests:
        first = interests.split(",")[0].strip()
        # Gunn & Delafield-Butt (2016): 20 of 20 studies showed engagement
        # gains from embedding a child's focused interests in instruction.
        # In the content, never dangled as a reward — so it goes in the
        # example, which is the part a child actually notices is theirs.
        blocks.append(
            f"EXAMPLE: when you need an everyday example of your own, build it "
            f"out of something this child is into — {interests}. Use it in the "
            f"example itself, never as a bribe or an aside. If the maths does "
            f"not fit it, use a plain example instead of forcing it."
        )
        adapted.append(f"uses {first}")

    if (profile.get("modality_gap") or 0) > 0:
        blocks.append(
            "READING: they understood a spoken passage but not the written "
            "one, so reading is the harder channel. Keep every sentence short "
            "enough to hold in your head after hearing it once."
        )
        adapted.append("easier to listen to")

    if profile.get("possible_masking"):
        blocks.append(
            "CHECKING: they answer fast and say they understand even when "
            "they do not. End with a question that would be hard to answer by "
            "guessing, rather than asking 'does that make sense?'"
        )
        adapted.append("checks understanding")

    if mastery > 0.7:
        blocks.append(
            "DEPTH: they already do well on this topic. This is a reminder, "
            "not a first teaching. Keep it to the key idea and one example."
        )
        adapted.append("recap, not re-teach")
    elif mastery > 0.35:
        blocks.append("DEPTH: they have met this before but it is not solid yet. "
                      "Re-teach it properly, briskly.")

    confidence = profile.get("profile_confidence")
    if confidence is not None and confidence < 0.6:
        blocks.append(
            "All of the above came from one short first session, so hold it "
            "loosely and follow what the child actually does."
        )

    return "\n\n".join(blocks), adapted


NO_PAGE_TEXT = (
    "I can't read this page yet, so I'm not going to guess at what it says. "
    "Have a look at the page itself and ask me about any bit of it."
)

# Handed to the model on top of the usual style rules, every time it is shown
# a page. The canon is frozen: what the model changes is how it is said.
CANON_RULES = (
    "GROUNDING RULES — these override everything else:\n"
    "- Everything you say must come from the page above. You may change the "
    "wording, the order of explanation, and the everyday examples used to "
    "illustrate it. You may not add facts, methods, or steps the page does "
    "not contain.\n"
    "- If the page does not answer something, say you do not know and point "
    "them at their teacher. Do not fill the gap.\n"
    "- Never contradict the page, even if you believe it is wrong.\n"
    "- The child cannot see the book. Never tell them to look at the page, at "
    "a picture on it, or at anything printed. The page text sometimes says "
    "'look at the opposite picture' — that was written for someone holding "
    "the book. Describe what matters instead of passing the instruction on.\n"
)


# =============================================================================
# The picture
# =============================================================================

VISUAL_INSTRUCTION = """
EVERY PAGE GETS A PICTURE. Say what it is FOR in `purpose`.

The child cannot see the book. The picture sits above your words and it is the
part they look at first, so `scene` is never empty — not on an exercise page,
not on a page of instructions, not ever.

`scene`: the concrete thing this page is about, in plain words, ten words or
fewer. A bean seed sprouting in a cup of soil. A plant with its roots in the
ground. A child measuring a seedling with a ruler. No style words. NEVER any
numbers, letters or labels — the app writes those on top afterwards, so ask
only for the thing itself.

`kind`: ALSO choose a drawn diagram. This one always appears — it needs no
picture service and works with the network down — so choose the best fit
rather than "none":

  steps          any procedure, experiment or method. Almost any page that
                 tells the child to do something in an order.
  labelled_parts any thing with named parts. Almost any science page.
  cycle          anything that goes round.
  hundredths_grid  a quantity out of 10 or 100.
  place_value    a number in its columns.
  number_line    where numbers sit relative to each other.
  bar_compare    two or more amounts.

Use "none" only when you would have to invent a number or a part name to fill
one in. A wrong diagram is worse than no diagram, because the child believes
the diagram. Everything in it must come from this page.

Pick the kind that does the job:

  hundredths_grid  a quantity out of 10 or 100. Set `total` and `shaded`.
                   This is the picture for tenths and hundredths.
  place_value      a number in its place-value columns. Set `columns` as
                   [{place, digit, highlight}] and `decimal_after` = how many
                   columns come before the decimal point.
  number_line      where numbers sit relative to each other. Set `min`, `max`,
                   `step`, and `marks` as [{value, label, highlight}]. This is
                   the picture for rounding and for comparing.
  bar_compare      two or more amounts side by side. Set `bars`.
  steps            a procedure with an order. Set `items` as [{label, note}].
  labelled_parts   a thing and the names of its parts. Set `items`.
  cycle            something that goes round: photosynthesis, water, a life
                   cycle. Set `items`.
  illustration     a real picture of a concrete thing — a plant, a seed being
                   carried by the wind, a child measuring something. This is
                   usually the right choice for a science page about a living
                   thing or an object. Set
                   `scene` to WHAT IS IN THE PICTURE, in plain words, ten
                   words or fewer. No style words. Never put numbers,
                   letters or labels in `scene`: everything written is added
                   afterwards by the app, so ask only for the thing itself.

Every number you put in the picture must come from this page. A picture that
disagrees with the page is worse than no picture, because the child will
believe the picture.
"""


def _clean_chunks(raw) -> list[str]:
    """
    Trim the lesson to something a struggling reader will actually finish.

    Enforced rather than requested. The prompt asks for three short chunks and
    the model mostly complies, but "mostly" put a wall of text in front of a
    child once already, and the cost of that is not a slightly worse lesson —
    it is a child who stops reading.
    """
    if isinstance(raw, str):
        raw = [raw]
    if not isinstance(raw, list):
        return []

    out: list[str] = []
    for item in raw[:MAX_LESSON_CHUNKS]:
        text = llm._clean_for_speech(str(item or "")).strip()
        if not text:
            continue

        words = text.split()
        if len(words) > MAX_CHUNK_WORDS:
            # Cut at the last sentence that fits, so it ends on a full stop
            # rather than mid-thought.
            kept, count = [], 0
            for sentence in re.split(r"(?<=[.!?])\s+", text):
                n = len(sentence.split())
                if count + n > MAX_CHUNK_WORDS and kept:
                    break
                kept.append(sentence)
                count += n
            text = " ".join(kept) if kept else " ".join(words[:MAX_CHUNK_WORDS])

        out.append(text.strip())

    return out


def _clean_visual(raw) -> dict | None:
    """
    Keep a visual spec only if it is usable. Returns None otherwise.

    The model is constrained to the schema, so this checks the content rather
    than the shape: a grid with nothing shaded, a number line with no marks
    and a `scene` full of digits are all well-formed and all useless.

    Every page gets a PICTURE — `scene` — whatever else it gets. A diagram is
    an extra on top of that, not an alternative to it. Leaving the choice to
    the model produced lesson screens with nothing above the words, and for
    these children the picture is the part that carries.
    """
    if not isinstance(raw, dict):
        return None

    kind = str(raw.get("kind") or "none").strip()
    if kind not in llm.VISUAL_KINDS:
        kind = "none"

    spec: dict = {
        "kind": kind,
        "purpose": str(raw.get("purpose") or "").strip(),
        "title": str(raw.get("title") or "").strip(),
    }

    # The picture. Digits in the scene are the failure mode — the image model
    # will render them, and it will render them wrong — so a scene containing
    # any is stripped back to nothing rather than sent.
    scene = str(raw.get("scene") or "").strip()
    if scene and len(scene.split()) <= 16 and not re.search(r"\d", scene):
        spec["scene"] = scene
        spec["key"] = hashlib.sha1(scene.lower().encode()).hexdigest()[:16]

    if kind == "none":
        # No diagram. Then the picture has to carry it alone.
        return spec if spec.get("scene") else None

    if kind == "hundredths_grid":
        total = int(raw.get("total") or 100)
        shaded = int(raw.get("shaded") or 0)
        if total not in (10, 100) or not (0 <= shaded <= total):
            return _diagram_or_picture(spec)
        spec.update(total=total, shaded=shaded)

    elif kind == "place_value":
        columns = [c for c in (raw.get("columns") or [])
                   if isinstance(c, dict) and c.get("place")]
        if not 2 <= len(columns) <= 12:
            return _diagram_or_picture(spec)
        spec["columns"] = [{
            "place": str(c["place"])[:14],
            "digit": str(c.get("digit") or "")[:2],
            "highlight": bool(c.get("highlight")),
        } for c in columns]
        spec["decimal_after"] = int(raw.get("decimal_after") or 0)

    elif kind == "number_line":
        try:
            low, high = float(raw["min"]), float(raw["max"])
        except (KeyError, TypeError, ValueError):
            return _diagram_or_picture(spec)
        if not high > low:
            return _diagram_or_picture(spec)
        marks = [m for m in (raw.get("marks") or []) if isinstance(m, dict)]
        if not marks:
            return _diagram_or_picture(spec)
        spec.update(min=low, max=high, step=float(raw.get("step") or 0) or None)
        spec["marks"] = [{
            "value": float(m.get("value", low)),
            "label": str(m.get("label") or "")[:12],
            "highlight": bool(m.get("highlight")),
        } for m in marks[:12]]

    elif kind == "bar_compare":
        bars = [b for b in (raw.get("bars") or [])
                if isinstance(b, dict) and b.get("label") is not None]
        if not 2 <= len(bars) <= 6:
            return _diagram_or_picture(spec)
        spec["bars"] = [{"label": str(b["label"])[:24],
                         "value": float(b.get("value") or 0)} for b in bars]

    elif kind in ("steps", "labelled_parts", "cycle"):
        items = [i for i in (raw.get("items") or [])
                 if isinstance(i, dict) and i.get("label")]
        if not 2 <= len(items) <= 8:
            return _diagram_or_picture(spec)
        spec["items"] = [{"label": str(i["label"])[:40],
                          "note": str(i.get("note") or "")[:90]} for i in items]

    elif kind == "illustration":
        # The picture is the whole visual here; it was validated above.
        if not spec.get("scene"):
            return None

    return spec


def _second_attempt_at_a_picture(context: str, profile: dict) -> dict | None:
    """
    Ask for the picture on its own, having asked for it alongside the lesson
    and been given nothing usable.

    The combined call has two jobs and drops one of them often enough to
    matter. This one has a single job and a schema containing only the
    picture, and "none" is explicitly not on the table — by the time we are
    here we already know the page has teachable content in it, because the
    lesson came back fine.
    """
    result = llm.generate_json(
        "Choose the visual for this page. It is the only thing the child has "
        "to look at, so it matters more than the words.\n\n"
        + VISUAL_INSTRUCTION
        + "\n\nDo NOT answer \"none\" for `kind` here. The page has "
          "something in it worth drawing — find it. `steps` fits any "
          "procedure and `labelled_parts` fits almost any science page, so "
          "one of those is nearly always available. Everything in it must "
          "come from the page.",
        schema=llm.VISUAL_ONLY_SCHEMA,
        context=context,
        student_profile=profile,
        temperature=0.5,
        max_tokens=400,
    )
    if not (result.ok and isinstance(result.data, dict)):
        return None
    return _clean_visual(result.data)


def _diagram_or_picture(spec: dict) -> dict | None:
    """
    A diagram that did not validate falls back to just the picture.

    A wrong diagram is worse than none — the child believes the diagram — but
    dropping the whole visual because the diagram was malformed leaves the
    lesson screen bare, and that is the failure we started from.
    """
    if not spec.get("scene"):
        return None
    return {"kind": "none", "purpose": spec.get("purpose", ""),
            "title": spec.get("title", ""),
            "scene": spec["scene"], "key": spec["key"]}


def _load_visual(raw: str | None) -> dict | None:
    if not raw:
        return None
    try:
        value = json.loads(raw)
    except (json.JSONDecodeError, TypeError):
        return None
    return value if isinstance(value, dict) else None


def _topic_mastery(conn: sqlite3.Connection, student_id: int,
                   topic_id: int | None) -> float:
    """How well this child already does on this lesson. 0.0 if never seen."""
    if not topic_id:
        return 0.0
    row = conn.execute(
        "SELECT level FROM mastery WHERE student_id = ? AND topic_id = ?",
        (student_id, topic_id),
    ).fetchone()
    return float(row["level"]) if row else 0.0


def _page_ref(page) -> dict:
    return {
        "page_id": page["id"],
        "page": page["page"],
        "lesson": page["lesson"],
        "book": page["book_title"],
        "subject": page["subject"],
        "on_screen": True,
    }


def rendition(
    conn: sqlite3.Connection,
    student_id: int,
    page_id: int,
    mode: str = "lesson",
    *,
    regenerate: bool = False,
) -> TutorReply:
    """
    The page, taught to THIS child.

    Cached per (student, page, mode) in `page_renditions`. Two reasons: a child
    who comes back to a page and finds a different lesson there is unsettled by
    it, which matters more for exactly the users this app is for; and a cache
    hit is instant on a congested MiFi, which is what generation being
    "hybrid" means — explanations cached so they survive a dead connection,
    practice questions written live.

    The cache is keyed on `student_id`, not on the support profile. The old
    `step_explanations` table cached per profile, which meant every autistic
    child in the class read the identical rewrite — the opposite of the point.

    Raises ValueError if the page does not exist. Returns an honest reply,
    never an invented one, when the page has no ingested text.
    """
    if mode not in MODE_INSTRUCTIONS:
        raise ValueError(f"Unknown rendition mode: {mode}")

    page = curriculum.page_row(conn, page_id)
    if page is None:
        raise ValueError(f"No curriculum page {page_id}")

    profile = load_profile(conn, student_id)
    mastery = _topic_mastery(conn, student_id, page["topic_id"])
    signature = _profile_signature(profile, mastery)

    if mode == "lesson":
        instruction, adapted_for = _lesson_instruction(profile, mastery)
    else:
        instruction, adapted_for = MODE_INSTRUCTIONS[mode], []

    if not regenerate:
        cached = conn.execute(
            "SELECT body, engine, source_sha, profile_sig, visual_json "
            "FROM page_renditions "
            "WHERE student_id = ? AND page_id = ? AND mode = ?",
            (student_id, page_id, mode),
        ).fetchone()
        # Two things invalidate a cached lesson: a swapped PDF, so nobody is
        # left reading an explanation of a page that has since moved, and a
        # changed profile, so the un-personalised lesson a child saw before
        # they had done the entry activity does not follow them forever.
        fresh = (
            cached
            and (cached["source_sha"] or "") == (page["book_sha"] or "")
            and (cached["profile_sig"] or "") == signature
        )
        if fresh:
            return TutorReply(
                text=cached["body"], engine=cached["engine"] or "cache",
                latency_ms=0, grounded=True, cached=True,
                suggested_topic_id=page["topic_id"],
                source_refs=[_page_ref(page)],
                adapted_for=adapted_for,
                visual=_load_visual(cached["visual_json"]),
            )

    body = curriculum.page_text(page["book_code"], page["page"])
    if not body:
        return TutorReply(
            text=NO_PAGE_TEXT, engine="none", latency_ms=0, grounded=False,
            suggested_topic_id=page["topic_id"], source_refs=[_page_ref(page)],
            error="Page has no ingested text",
        )

    context = (
        f"BOOK: {page['book_title']} ({page['subject']}, grade {page['grade']})\n"
        f"LESSON: {page['lesson']}\n"
        f"PAGE {page['page']}, exactly as printed:\n\n{body}"
    )

    visual: dict | None = None

    if mode == "lesson":
        # One call for the lesson and the picture together. Two calls would
        # double the wait and let the picture describe something the words
        # never mentioned.
        structured = llm.generate_json(
            instruction + "\n\n" + VISUAL_INSTRUCTION,
            schema=llm.LESSON_SCHEMA,
            context=context,
            student_profile=profile,
            extra_rules=CANON_RULES,
            temperature=0.6,
            # Deliberately tight. Room to ramble is room to write a wall of
            # text, and the ceiling is a cheap second line of defence behind
            # the chunk limits.
            max_tokens=700,
        )

        chunks: list[str] = []
        if structured.ok and isinstance(structured.data, dict):
            chunks = _clean_chunks(structured.data.get("chunks"))
            visual = _clean_visual(structured.data.get("visual"))

        if chunks:
            result = llm.LLMResponse(text="\n\n".join(chunks),
                                     engine=structured.engine,
                                     latency_ms=structured.latency_ms)
        else:
            result = llm.LLMResponse(
                text=_offline_rendition(page, body, mode),
                engine="fallback", latency_ms=structured.latency_ms,
                error=structured.error,
            )

        # The picture matters more than the words for these children, so one
        # combined call quietly returning nothing is not good enough. Ask
        # again, for the picture alone — one job, one schema, and it lands far
        # more often than hoping a single call gets both right.
        # A page with nothing above the words is the failure to avoid. Retry
        # when there is no visual at all, and also when the visual is a
        # picture with no diagram behind it — because the picture needs image
        # quota and the diagram does not, so a picture alone is one 429 away
        # from a bare screen.
        needs_retry = chunks and (
            visual is None or visual.get("kind") == "none"
        )
        if needs_retry:
            better = _second_attempt_at_a_picture(context, profile)
            if better is not None and better.get("kind") != "none":
                # Keep the scene from the first pass if the second lost it.
                if visual and visual.get("scene") and not better.get("scene"):
                    better["scene"] = visual["scene"]
                    better["key"] = visual["key"]
                visual = better
            elif visual is None:
                visual = better
    else:
        result = llm.generate(
            instruction,
            context=context,
            student_profile=profile,
            extra_rules=CANON_RULES,
            max_tokens=220,
            temperature=0.6,
            fallback_text=_offline_rendition(page, body, mode),
        )

    if result.is_live:
        conn.execute(
            """
            INSERT INTO page_renditions
                (student_id, page_id, mode, body, engine, source_sha,
                 profile_sig, visual_json, created_at)
            VALUES (?,?,?,?,?,?,?,?,?)
            ON CONFLICT(student_id, page_id, mode) DO UPDATE SET
                body        = excluded.body,
                engine      = excluded.engine,
                source_sha  = excluded.source_sha,
                profile_sig = excluded.profile_sig,
                visual_json = excluded.visual_json,
                created_at  = excluded.created_at
            """,
            (student_id, page_id, mode, result.text, result.engine,
             page["book_sha"], signature,
             json.dumps(visual) if visual else None, economy.utc_now()),
        )

    return TutorReply(
        text=result.text, engine=result.engine, latency_ms=result.latency_ms,
        grounded=True, suggested_topic_id=page["topic_id"],
        source_refs=[_page_ref(page)], error=result.error,
        adapted_for=adapted_for, visual=visual,
    )


def explain(
    conn: sqlite3.Connection,
    student_id: int,
    page_id: int,
    mode: str,
    *,
    initiated_by: str = "student",
    seconds_before: int = 0,
) -> TutorReply:
    """
    Re-explain the page a different way. This is the "I don't get this" button.

    Same machinery as `rendition()` — it IS a rendition, in one of the three
    help modes — plus the two things that make it evidence rather than a
    feature: the request goes into hint_requests, and the page's help counter
    goes up so stall detection knows this child asked.
    """
    if mode == "lesson":
        raise ValueError("'lesson' is the rendition itself, not a help mode")

    reply = rendition(conn, student_id, page_id, mode)

    _log_help(conn, student_id, mode, page_id=page_id,
              initiated_by=initiated_by, seconds_before=seconds_before,
              response_text=reply.text,
              engine="cache" if reply.cached else reply.engine,
              latency_ms=reply.latency_ms)
    _bump_page_help(conn, student_id, page_id)
    return reply


def _looks_like_prose(text: str) -> bool:
    """
    Is this OCR output fit to show a child?

    A page that is mostly a diagram OCRs into things like
    "HOOUGGAOOO OO 0000000000000 VEE GOOLE" — harmless as grounding, because
    the model is told not to invent and simply has little to work with, but
    unacceptable to put on screen as though it were the lesson.

    The tell is repeated characters: English almost never runs the same letter
    three times, and scanner noise does it constantly.
    """
    tokens = [t for t in re.split(r"\s+", text.strip()) if len(t) > 1][:60]
    if len(tokens) < 8:
        return False

    noisy = sum(1 for t in tokens if re.search(r"(.)\1\1", t))
    wordy = sum(1 for t in tokens if re.fullmatch(r"[A-Za-z][a-z']+", t))

    return noisy / len(tokens) < 0.15 and wordy / len(tokens) > 0.45


def _offline_rendition(page, body: str, mode: str) -> str:
    """
    What Souly says when the LLM is unreachable.

    An earlier version returned the source text unchanged, which is technically
    grounded and useless: the child taps "I don't get this" and receives the
    identical sentence back, which reads as being ignored.

    So: say plainly that the clever explanation is not available, then give the
    most useful thing we do have offline — the page one sentence at a time,
    which is a real simplification and something we can do without a model.
    Honest, and still worth reading.
    """
    sentences = [s.strip() for s in re.split(r"(?<=[.!?])\s+", body) if s.strip()]
    where = f"page {page['page']} of {page['book_title']}"

    if mode == "lesson":
        # Cannot say "read the page" any more: the child is not shown the
        # page. So give them the page's own words, one line at a time, and be
        # honest that this is the book talking rather than Souly.
        if sentences and _looks_like_prose(body):
            numbered = " ".join(f"{i}. {t}" for i, t in enumerate(sentences[:6], 1))
            return (f"I can't write my own explanation right now, so here is "
                    f"what {where} says, one bit at a time. {numbered}")
        return (f"I can't write my own explanation right now, and I can't read "
                f"{where} either. Ask me again in a moment, or ask your "
                f"teacher about this one.")

    if mode == "simpler":
        # One sentence at a time is a real simplification: it's the segmenting
        # principle, and it's something we can do without a model.
        if len(sentences) > 1 and _looks_like_prose(body):
            numbered = " ".join(f"{i}. {s}" for i, s in enumerate(sentences[:8], 1))
            return "Let's take it one bit at a time. " + numbered
        return "Here it is again, slowly. " + body[:600]

    if mode == "example":
        return ("I can't reach my examples right now. Tell me which bit is "
                "the tricky one and I'll have another go when I'm back.")

    # another_way
    if len(sentences) > 1 and _looks_like_prose(body):
        return ("I can't rewrite it right now, so here's the last part on its "
                "own — that's usually the bit that matters most. " + sentences[-1])
    return "I can't rewrite it right now. Here it is once more. " + body[:600]


def _bump_page_help(conn: sqlite3.Connection, student_id: int, page_id: int) -> None:
    conn.execute(
        "INSERT INTO page_activity (student_id, page_id, help_requests) "
        "VALUES (?, ?, 1)",
        (student_id, page_id),
    )


# =============================================================================
# 2. The hint ladder
# =============================================================================

def hint(
    conn: sqlite3.Connection,
    student_id: int,
    question_id: int,
    tier: int,
    *,
    student_answer: str | None = None,
    quiz_id: int | None = None,
    attempts_before: int = 0,
    seconds_before: int = 0,
    initiated_by: str = "student",
) -> TutorReply:
    """
    One rung of the hint ladder for one question.

    The worked solution and the known wrong answers go into the prompt so the
    hint is CORRECT, and SOLUTION_PROMPT_RULES stops the model repeating them.
    That pairing is the Bastani guardrail — see llm.py.
    """
    tier = max(1, min(4, tier))

    question = conn.execute(
        """
        SELECT q.*, t.title AS topic_title, b.code AS book_code
        FROM questions q
        LEFT JOIN topics t           ON t.id = q.topic_id
        LEFT JOIN curriculum_books b ON b.id = q.book_id
        WHERE q.id = ?
        """,
        (question_id,),
    ).fetchone()
    if question is None:
        raise ValueError(f"No question {question_id}")

    options = json.loads(question["options_json"])
    correct = options[question["correct_index"]]

    # The page this question was written from. A hint that points back at the
    # book is checkable; one that points at the model's memory of it is not.
    source_text = ""
    if question["book_code"] and question["source_page"]:
        source_text = curriculum.page_text(question["book_code"],
                                           question["source_page"])

    # Everything the model needs to be right, none of it for the child's eyes.
    solution_block = [f"QUESTION: {question['prompt']}",
                      f"OPTIONS: {', '.join(options)}",
                      f"CORRECT ANSWER: {correct}"]
    if question["worked_solution"]:
        solution_block.append(f"WORKED SOLUTION: {question['worked_solution']}")
    elif question["explanation"]:
        solution_block.append(f"WHY: {question['explanation']}")

    if question["common_wrong_answers"]:
        try:
            wrongs = json.loads(question["common_wrong_answers"])
            lines = [f"  - {w['answer']}: {w['why']}" for w in wrongs]
            solution_block.append("COMMON MISTAKES:\n" + "\n".join(lines))
        except (json.JSONDecodeError, KeyError, TypeError):
            pass

    if source_text:
        solution_block.append(
            f"THE BOOK PAGE THIS CAME FROM (page {question['source_page']}):\n"
            f"{source_text}"
        )

    if student_answer:
        solution_block.append(f"THE CHILD ANSWERED: {student_answer}")

    instruction = TIER_INSTRUCTIONS[tier]

    # Offline, tier 1 can still use the stored hint and tier 4 the stored
    # explanation. Tiers 2 and 3 degrade to the stored hint too — worse, but
    # never nothing.
    offline = {
        1: question["hint"] or "Have another look at the question.",
        2: question["hint"] or "Let's look at a similar one together.",
        3: question["hint"] or "Let's take it one step at a time.",
        4: f"The answer is {correct}. {question['explanation'] or ''}".strip(),
    }[tier]

    result = llm.generate(
        instruction,
        context="\n".join(solution_block),
        student_profile=load_profile(conn, student_id),
        extra_rules="" if tier == 4 else llm.SOLUTION_PROMPT_RULES,
        max_tokens=260,
        temperature=0.5,
        fallback_text=offline,
    )

    _log_help(conn, student_id, HINT_TIERS[tier], tier=tier,
              question_id=question_id, quiz_id=quiz_id,
              initiated_by=initiated_by, attempts_before=attempts_before,
              seconds_before=seconds_before, student_answer=student_answer,
              response_text=result.text, engine=result.engine,
              latency_ms=result.latency_ms)

    return TutorReply(
        text=result.text, engine=result.engine, latency_ms=result.latency_ms,
        grounded=True, tier=tier,
        next_tier=tier + 1 if tier < 4 else None,
        error=result.error,
    )


def resolve_hints(conn: sqlite3.Connection, student_id: int, question_id: int,
                  was_correct: bool) -> None:
    """
    Mark the outcome of any unresolved hints on this question.

    This is what turns hint_requests from a log into evidence: it lets us ask
    "after a tier-1 nudge, how often did the next attempt succeed?"
    """
    conn.execute(
        "UPDATE hint_requests SET resolved_correct = ? "
        "WHERE student_id = ? AND question_id = ? AND resolved_correct IS NULL",
        (int(was_correct), student_id, question_id),
    )


# =============================================================================
# 3. Question generation
# =============================================================================

def generate_questions(
    conn: sqlite3.Connection,
    topic_id: int,
    *,
    count: int = 4,
    student_id: int | None = None,
    page: int | None = None,
    persist: bool = True,
) -> dict:
    """
    Build practice questions from the book page itself.

    This is the live half of hybrid generation: explanations are cached so
    they survive a dead connection, practice is written fresh from the page so
    a child who repeats a lesson does not see the same four questions forever.

    `topic_id` is the lesson — since schema_v5 a topic IS a lesson in a real
    book. Pass `page` to generate from one page rather than the whole lesson.

    Four safeguards, because a wrong question in front of a child with a
    learning disability is worse than a boring one:

      1. Only verified books. The gate is the same one as before, moved to
         where the content now lives.
      2. The model sees the page text and is told every question must be
         answerable from it alone.
      3. Everything it returns goes through `_validate_question()` — shape,
         duplicate options, index range, answer-leaking prompts.
      4. Stored with review_status='pending' and the page it came from, so a
         teacher can read what the machine wrote next to its source.

    Returns {"questions": [...], "engine": ..., "rejected": n, "reasons": [...]}
    """
    topic = conn.execute(
        """
        SELECT t.id, t.title, t.is_verified, t.book_id, t.lesson_label,
               b.title AS book_title, b.subject AS subject,
               b.is_verified AS book_verified
        FROM topics t
        LEFT JOIN curriculum_books b ON b.id = t.book_id
        WHERE t.id = ?
        """,
        (topic_id,),
    ).fetchone()
    if topic is None:
        raise ValueError(f"No lesson {topic_id}")

    if topic["book_id"] is None:
        return {"questions": [], "engine": "none", "rejected": 0,
                "reasons": ["Lesson is not backed by a book"]}

    # The verification rule holds here too: no generating from unapproved
    # material. A human has to have eyeballed the book first.
    if not (topic["is_verified"] and topic["book_verified"]):
        return {"questions": [], "engine": "none", "rejected": 0,
                "reasons": ["Book is not verified — cannot generate from it"]}

    source = curriculum.source_text(conn, topic_id, page=page)
    if not source:
        return {"questions": [], "engine": "none", "rejected": 0,
                "reasons": ["No ingested text for this lesson — run "
                            "scripts/ingest_curriculum.py"]}

    pages = curriculum.lesson_pages(conn, topic_id)
    source_page = page if page is not None else (pages[0]["page"] if pages else None)

    # Aim the difficulty at where this student actually is — and start low.
    # These are children with learning disabilities working in English, which
    # for most of them is not the language they think in. A question they
    # cannot parse measures their reading, not their maths.
    difficulty_hint = "easy — difficulty 1 or 2"
    if student_id:
        row = conn.execute(
            "SELECT level FROM mastery WHERE student_id = ? AND topic_id = ?",
            (student_id, topic_id),
        ).fetchone()
        mastery = row["level"] if row else 0.0
        if mastery > 0.7:
            difficulty_hint = "a little harder — difficulty 3, never above"
        elif mastery > 0.4:
            difficulty_hint = "medium — difficulty 2 or 3"

    instruction = f"""Write {count} multiple-choice practice questions about the lesson
"{topic['title']}" from {topic['book_title']}.

WHO THIS IS FOR
These are children with learning disabilities, working in English. Keep every
question SIMPLE. A question they cannot read is a question that measures their
reading, not their understanding.

- ONE idea per question. Never two steps, never "and then".
- Ask about the page's core idea, not an incidental detail from it.
- Question text: at most 15 words, one sentence, no sub-clauses.
- Options: at most 6 words each, and all four about the same length — a
  conspicuously longer option gives the answer away.
- Plain words. No idioms, no metaphors, no rhetorical questions, no double
  negatives, no "which of the following", no "all of the above".
- Use concrete numbers and objects from the page rather than abstractions.
- Present tense. Say things directly.

GROUNDING
- Every question must be answerable using ONLY the book page above. Do not use
  outside knowledge and do not test anything the page does not cover.

SHAPE
- Exactly 4 options each. Exactly one is correct.
- Wrong options must be PLAUSIBLE mistakes a child could really make — not
  silly. A child learns nothing from ruling out an obviously absurd option.
- `explanation` teaches why the answer is right, in one or two short sentences.
- `hint` points at what to look at, WITHOUT giving the answer away. It is the
  first thing the child sees when they get it wrong, so it must be a nudge —
  never the answer in other words.
- `worked_solution` shows the full reasoning. The tutor reads this so its hints
  are correct; the child never sees it.
- `common_wrong_answers` lists the tempting wrong options and the specific
  misunderstanding behind each one. This is what lets the tutor say "ah, I
  think you counted the ones you ate" instead of "that's wrong, try again".
- Difficulty: {difficulty_hint}.
"""

    result = llm.generate_json(instruction, schema=llm.QUESTION_SCHEMA,
                               context=source, temperature=0.7)

    if not result.ok:
        return {"questions": [], "engine": "fallback", "rejected": 0,
                "reasons": [result.error or "Generation unavailable"]}

    raw = result.data.get("questions", []) if isinstance(result.data, dict) else []
    accepted, reasons = [], []

    for item in raw:
        problem = _validate_question(item)
        if problem:
            reasons.append(problem)
            continue
        accepted.append(item)

    stored = []
    if persist and accepted:
        for item in accepted:
            question_id = _store_generated(
                conn, topic, item,
                source_page=source_page, student_id=student_id,
                engine=result.engine,
            )
            stored.append({**item, "id": question_id})

    return {
        "questions": stored if persist else accepted,
        "engine": result.engine,
        "generated": len(raw),
        "accepted": len(accepted),
        "rejected": len(raw) - len(accepted),
        "reasons": reasons,
        "latency_ms": result.latency_ms,
    }


def _validate_question(item: dict) -> str | None:
    """
    Reject anything malformed. Returns a reason string, or None if it's fine.

    Constrained decoding guarantees the shape; this checks the content is
    usable. The answer-leak check matters most: a model asked for a hint-free
    prompt will still sometimes write "Which is correct: 5/8 (the answer)?"
    """
    prompt = (item.get("prompt") or "").strip()
    options = item.get("options") or []
    index = item.get("correct_index")

    if len(prompt) < 8:
        return f"Prompt too short: {prompt!r}"
    if len(options) != 4:
        return f"Expected 4 options, got {len(options)}: {prompt[:40]}"
    if any(not str(o).strip() for o in options):
        return f"Blank option in: {prompt[:40]}"
    if len({str(o).strip().lower() for o in options}) != 4:
        return f"Duplicate options in: {prompt[:40]}"

    # Readability, enforced rather than requested. The prompt asks for short
    # questions; models drift long, and a 30-word question in front of a child
    # with a reading difficulty is a question about reading.
    words = len(prompt.split())
    if words > MAX_PROMPT_WORDS:
        return f"Prompt too long ({words} words): {prompt[:40]}"

    lengths = [len(str(o).split()) for o in options]
    if max(lengths) > MAX_OPTION_WORDS:
        return f"Option too long ({max(lengths)} words): {prompt[:40]}"

    # A conspicuously longer option is a tell. Children spot it without
    # understanding anything, and then the item measures nothing.
    if max(lengths) >= 3 and max(lengths) > 2 * min(lengths) + 2:
        return f"One option far longer than the others: {prompt[:40]}"

    lowered = prompt.lower()
    for phrase in BANNED_PHRASES:
        if phrase in lowered:
            return f"Prompt uses {phrase!r}: {prompt[:40]}"
    if not isinstance(index, int) or not (0 <= index < 4):
        return f"correct_index {index} out of range: {prompt[:40]}"
    if not (item.get("explanation") or "").strip():
        return f"No explanation: {prompt[:40]}"
    if not (item.get("worked_solution") or "").strip():
        return f"No worked solution: {prompt[:40]}"

    # The prompt must not contain the correct option verbatim — that's a
    # giveaway, and models do it more often than you'd expect.
    correct = str(options[index]).strip().lower()
    if len(correct) > 3 and correct in prompt.lower():
        return f"Prompt leaks the answer: {prompt[:40]}"

    # Nor may the hint.
    hint_text = (item.get("hint") or "").strip().lower()
    if len(correct) > 3 and correct in hint_text:
        return f"Hint leaks the answer: {prompt[:40]}"

    return None


def _store_generated(conn: sqlite3.Connection, topic: sqlite3.Row, item: dict, *,
                     source_page: int | None, student_id: int | None,
                     engine: str) -> int:
    """
    Save one generated question with its provenance.

    `review_status='pending'` and `student_id` are the two columns that make
    this auditable: a teacher can pull up every item the model wrote, see the
    page it came from, and see which child it was written for. A question
    written for one student is never reused for another.
    """
    difficulty = item.get("difficulty", 2)
    if not isinstance(difficulty, int) or not (1 <= difficulty <= 5):
        difficulty = 2

    return conn.execute(
        """
        INSERT INTO questions (
            topic_id, book_id, source_page, prompt, options_json, correct_index,
            explanation, hint, worked_solution, common_wrong_answers,
            difficulty, engine, review_status, student_id, created_at
        ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?, 'pending', ?,?)
        """,
        (
            topic["id"], topic["book_id"], source_page,
            item["prompt"], json.dumps(item["options"]), item["correct_index"],
            item.get("explanation"), item.get("hint"),
            item.get("worked_solution"),
            json.dumps(item.get("common_wrong_answers") or []),
            difficulty, engine, student_id, economy.utc_now(),
        ),
    ).lastrowid


# =============================================================================
# 4. Stall detection
# =============================================================================

def stall_threshold(conn: sqlite3.Connection, student_id: int) -> int:
    """
    How many seconds of no progress means this student is stuck.

    Calibrated to the student's own median step time, not a global constant.
    Autistic people are measurably slower across the board (Zapparrata 2023,
    g = .35), so a fixed threshold would mark the entire target cohort as
    permanently stalled — and then interrupt them, which is the specific harm
    the research warns about.
    """
    rows = conn.execute(
        "SELECT seconds_on_page FROM page_activity "
        "WHERE student_id = ? AND seconds_on_page > 0 "
        "ORDER BY id DESC LIMIT 20",
        (student_id,),
    ).fetchall()

    if len(rows) < 4:
        # Not enough live data yet. The entry activity measured how long this
        # child takes to make a first attempt, so use that instead of guessing
        # — it's the whole reason we collect it on day one.
        measured = conn.execute(
            "SELECT median_first_attempt_ms FROM v_current_learner_profile "
            "WHERE student_id = ?",
            (student_id,),
        ).fetchone()
        if measured and measured["median_first_attempt_ms"]:
            baseline = measured["median_first_attempt_ms"] / 1000
            return int(max(STALL_FLOOR_SECONDS,
                           min(STALL_CEILING_SECONDS,
                               baseline * STALL_MULTIPLIER * 2)))
        return STALL_FLOOR_SECONDS * 2   # generous until we know them

    values = sorted(r["seconds_on_page"] for r in rows)
    median = values[len(values) // 2]
    return int(max(STALL_FLOOR_SECONDS,
                   min(STALL_CEILING_SECONDS, median * STALL_MULTIPLIER)))


def should_offer_help(
    conn: sqlite3.Connection,
    student_id: int,
    *,
    seconds_on_step: int,
    wrong_attempts: int = 0,
    already_offered: bool = False,
) -> dict:
    """
    Should Souly speak first?

    Two triggers, both about PROGRESS rather than attention:
      * two wrong attempts on the same item
      * idle for longer than this student's own stall threshold

    Deliberately NOT triggered by looking away. Autistic children avert their
    gaze MORE as a task gets harder (Doherty-Sneddon 2012) — it reduces
    cognitive load so they can think. Offering help then interrupts them at
    the worst possible moment.
    """
    if already_offered:
        return {"offer": False, "reason": "already offered on this step"}

    if wrong_attempts >= 2:
        return {"offer": True, "reason": "two wrong attempts", "tier": 1}

    threshold = stall_threshold(conn, student_id)
    if seconds_on_step >= threshold:
        return {"offer": True, "reason": f"idle {seconds_on_step}s (threshold {threshold}s)",
                "tier": 1, "threshold": threshold}

    return {"offer": False, "reason": "making progress", "threshold": threshold}


# =============================================================================
# 5. Free-form questions
# =============================================================================

PICTURE_WORDS = (
    "picture", "image", "photo", "draw", "drawing", "show me what",
    "what does it look like", "see it", "diagram", "illustration",
)


def _wants_a_picture(text: str) -> bool:
    """
    Did the child just ask to be shown something?

    They asked, Souly said "I cannot make pictures for you", and that was
    false — the app draws. A child who asks for a picture and is told no
    stops asking.
    """
    lowered = text.lower()
    return any(word in lowered for word in PICTURE_WORDS)


def _detect_mode_intent(text: str) -> str | None:
    """Cheap local intent check, so obvious requests don't wait on a round trip."""
    lowered = text.lower().strip()
    if any(w in lowered for w in ("quiz me", "test me", "give me a quiz",
                                  "make a quiz", "create a quiz")):
        return "quiz"
    if any(w in lowered for w in ("give me a game", "play a game", "let's play",
                                  "i want a game")):
        return "game"
    return None


DRAW_SCHEMA = {
    "type": "object",
    "properties": {
        "scene": {"type": "string"},
        "purpose": {"type": "string"},
        "say": {"type": "string"},
    },
    "required": ["scene", "purpose", "say"],
}


def draw_for_page(conn: sqlite3.Connection, student_id: int,
                  page_id: int) -> tuple[dict | None, str]:
    """
    Draw a picture of what this page is about, because the child asked.

    Returns (visual spec, what Souly says). The spec is written into this
    child's rendition for the page, so the lesson screen picks up the same
    picture rather than the two disagreeing.

    Grounded like everything else: the scene comes from the page, not from the
    child's request, so "draw me a dragon" gets a picture of the lesson.
    """
    page = curriculum.page_row(conn, page_id)
    if page is None:
        return None, "I'm not sure which page we're on."

    body = curriculum.page_text(page["book_code"], page["page"])
    if not body:
        return None, ("I can't read this page well enough to draw it. "
                      "Tell me what you'd like a picture of and I'll try.")

    result = llm.generate_json(
        "The child asked to see a picture of what this page is about.\n\n"
        "`scene`: what should be IN the picture, in plain words, ten words or "
        "fewer. Only things the page actually talks about. No style words. "
        "Never any numbers, letters or labels — the app writes those on top "
        "afterwards, so ask only for the thing itself.\n"
        "`purpose`: one short sentence saying what the picture helps them see.\n"
        "`say`: one warm sentence to the child, telling them the picture is "
        "coming and what to notice in it. Under 25 words.",
        schema=DRAW_SCHEMA,
        context=f"PAGE {page['page']} of {page['book_title']}:\n\n{body}",
        temperature=0.5,
        max_tokens=300,
    )

    if not (result.ok and isinstance(result.data, dict)):
        return None, ("I can't draw that one just now. Ask me again in a "
                      "moment.")

    spec = _clean_visual({
        "kind": "illustration",
        "scene": result.data.get("scene"),
        "purpose": result.data.get("purpose"),
    })
    if spec is None:
        return None, ("I couldn't think of a good picture for this one. "
                      "What part would you like me to explain instead?")

    # Save it onto the lesson rendition so the picture beside the lesson and
    # the picture in the chat are the same picture.
    conn.execute(
        """
        UPDATE page_renditions SET visual_json = ?
        WHERE student_id = ? AND page_id = ? AND mode = 'lesson'
        """,
        (json.dumps(spec), student_id, page_id),
    )

    return spec, str(result.data.get("say") or "Here you go.")


# Openers that mean "still about the last thing": the child is continuing,
# not starting again.
FOLLOW_UP_OPENERS = (
    "why", "how come", "and", "so", "then", "but", "what about", "how about",
    "again", "more", "explain more", "tell me more", "what do you mean",
    "i don't get", "i dont get", "i still", "which one", "the second",
    "the first", "ok but", "okay but", "what if",
)
FOLLOW_UP_MAX_WORDS = 8


def _is_follow_up(message: str) -> bool:
    """
    Is this a continuation of the last exchange, or a new question?

    It matters because a follow-up gets anchored to what the conversation was
    already about, and a NEW question must not be — "explain quantum
    chromodynamics" is off-syllabus and has to be refused, not answered as
    though it were about decimals because decimals is what came before.

    Two ways in: the message carries nothing to search on at all ("why?"), or
    it is short and opens with a word that continues a sentence ("and the
    other one?").
    """
    lowered = message.lower().strip().rstrip("?!.")
    if not lowered:
        return True

    # `rag.tokenize` drops stopwords, so what is left is what retrieval could
    # actually have matched on. Nothing left means nothing to search for.
    if len(rag.tokenize(lowered)) <= 1:
        return True

    if len(lowered.split()) <= FOLLOW_UP_MAX_WORDS:
        return any(lowered.startswith(opener) for opener in FOLLOW_UP_OPENERS)

    return False


def _search_query(message: str, history: list[dict]) -> str:
    """
    What to actually search the curriculum for.

    A follow-up on its own retrieves nothing. Folding in what the child said
    last turn is what lets "why?" find the page they were just reading.
    """
    if not _is_follow_up(message):
        return message

    previous = next(
        (t["content"] for t in reversed(history) if t["role"] == "student"), ""
    )
    return f"{previous} {message}".strip() if previous else message


def _last_anchor(conn: sqlite3.Connection,
                 student_id: int) -> tuple[int | None, int | None]:
    """
    The page and topic the last exchange was about.

    Read off the previous reply's stored `source_refs`, which is why they are
    written there in the first place. Bounded to the current sitting: an
    anchor from yesterday is not what this child is looking at.
    """
    row = conn.execute(
        """
        SELECT page_id, source_refs, created_at FROM chat_messages
        WHERE student_id = ? AND role = 'souly'
        ORDER BY id DESC LIMIT 1
        """,
        (student_id,),
    ).fetchone()
    if row is None:
        return None, None

    stamp = _parse_stamp(row["created_at"])
    if stamp is not None:
        age = datetime.utcnow() - stamp
        if age > timedelta(minutes=SESSION_GAP_MINUTES):
            return None, None

    if row["page_id"]:
        return row["page_id"], None

    try:
        refs = json.loads(row["source_refs"] or "[]")
    except (json.JSONDecodeError, TypeError):
        return None, None

    for ref in refs:
        if ref.get("page_id"):
            return ref["page_id"], ref.get("topic_id")
    for ref in refs:
        if ref.get("topic_id"):
            return None, ref["topic_id"]
    return None, None


def answer(
    conn: sqlite3.Connection,
    student_id: int,
    message: str,
    *,
    input_mode: str = "text",
    stt_confidence: float | None = None,
    session_id: int | None = None,
    topic_id: int | None = None,
    page_id: int | None = None,
    award_stars: bool = True,
) -> TutorReply:
    """
    A free-form question, still anchored to the page when there is one.

    If `page_id` is set, that page's text is put first in the context — the
    child is almost always asking about the thing in front of them, and
    retrieval alone can drift to a different lesson that shares vocabulary.
    """
    message = (message or "").strip()
    if not message:
        return TutorReply(text="I didn't catch that. Can you say it again?",
                          engine="none", latency_ms=0, error="Empty message")

    conn.execute(
        """
        INSERT INTO chat_messages (student_id, session_id, role, content,
                                   input_mode, stt_confidence, page_id)
        VALUES (?,?,'student',?,?,?,?)
        """,
        (student_id, session_id, message, input_mode, stt_confidence, page_id),
    )

    suggested_mode = _detect_mode_intent(message)
    history = load_history(conn, student_id)

    # "Draw me a picture" is a thing Souly can actually do, so do it rather
    # than answering a question about it.
    if page_id and _wants_a_picture(message):
        spec, say = draw_for_page(conn, student_id, page_id)
        conn.execute(
            """
            INSERT INTO chat_messages (student_id, session_id, role, content,
                                       input_mode, engine, page_id, help_type)
            VALUES (?,?,'souly',?, 'text', 'gemini', ?, 'free_question')
            """,
            (student_id, session_id, say, page_id),
        )
        page_row = curriculum.page_row(conn, page_id)
        return TutorReply(
            text=say, engine="gemini", latency_ms=0, grounded=bool(spec),
            visual=spec, suggested_topic_id=(page_row["topic_id"] if page_row else None),
            source_refs=[_page_ref(page_row)] if page_row else [],
        )

    context_parts, refs = [], []

    # Where the conversation already was. A child who has just been told about
    # decimals and then types "why?" is still asking about decimals — but the
    # question itself contains nothing to retrieve on, so without this the
    # search comes back empty and the model is instructed to say it hasn't
    # learned about that yet. That is the single thing that made this feel
    # like every message opened a new conversation.
    if page_id is None and topic_id is None and _is_follow_up(message):
        page_id, topic_id = _last_anchor(conn, student_id)

    if page_id:
        page = curriculum.page_row(conn, page_id)
        if page:
            body = curriculum.page_text(page["book_code"], page["page"])
            if body:
                context_parts.append(
                    f"[ON SCREEN RIGHT NOW] {page['subject']} > {page['lesson']}, "
                    f"{page['book_title']} page {page['page']}:\n{body}"
                )
            refs.append(_page_ref(page))
            topic_id = topic_id or page["topic_id"]

    chunks = rag.search(conn, _search_query(message, history), limit=3,
                        topic_id=topic_id)

    if not chunks and topic_id and not context_parts:
        # Retrieval found nothing, but we know which lesson this conversation
        # is about. Hand over the lesson itself rather than nothing — "nothing"
        # makes llm.py tell the child it has not learned about that yet, which
        # is a lie when the lesson is right there.
        lesson_text = curriculum.source_text(conn, topic_id, max_chars=2000)
        if lesson_text:
            context_parts.append(lesson_text)

    if chunks:
        context_parts.append(rag.build_context(chunks))
        refs.extend(c.to_ref() for c in chunks)

    context = "\n\n".join(context_parts)

    result = llm.generate(
        message,
        context=context,
        history=history,
        student_profile=load_profile(conn, student_id),
        suggestions=available_topics(conn) if not context else None,
    )

    conn.execute(
        """
        INSERT INTO chat_messages (student_id, session_id, role, content,
                                   input_mode, engine, latency_ms, source_refs,
                                   page_id, help_type)
        VALUES (?,?,'souly',?,'text',?,?,?,?, 'free_question')
        """,
        (student_id, session_id, result.text, result.engine, result.latency_ms,
         json.dumps(refs) if refs else None, page_id),
    )

    _log_help(conn, student_id, "free_question", page_id=page_id,
              response_text=result.text, engine=result.engine,
              latency_ms=result.latency_ms, student_answer=message)

    award_dict = None
    if award_stars:
        award_dict = economy.award(
            conn, student_id, "chat_question",
            stars=economy.STARS_PER_CHAT_QUESTION,
            xp=economy.XP_PER_CHAT_QUESTION,
            topic_id=topic_id, duration_s=5, detail=message[:120],
        ).to_dict()

    return TutorReply(
        text=result.text, engine=result.engine, latency_ms=result.latency_ms,
        source_refs=refs, grounded=bool(context), award=award_dict,
        suggested_mode=suggested_mode, suggested_topic_id=topic_id,
        error=result.error,
    )


# =============================================================================
# 6. Greeting
# =============================================================================

def greeting(conn: sqlite3.Connection, student_id: int) -> str:
    """
    Souly's opening line, built from real state.

    Note what it never says: nothing about always being here, nothing about
    friendship, nothing about having missed them. Moxie — a companion robot
    sold for autistic children — bricked every unit when its company folded,
    and the children who had been told it was their best friend were the ones
    who took it hardest. Souly is a study buddy for a study session.
    """
    profile = load_profile(conn, student_id)
    name = profile.get("display_name", "friend")

    in_progress = conn.execute(
        """
        SELECT t.title FROM lesson_progress lp
        JOIN topics t ON t.id = lp.topic_id
        WHERE lp.student_id = ? AND lp.is_complete = 0 AND lp.pages_completed > 0
        ORDER BY lp.updated_at DESC LIMIT 1
        """,
        (student_id,),
    ).fetchone()

    if in_progress:
        return f"Hi {name}. You were on {in_progress['title']}. Want to carry on?"

    # Grade-aware, because "no lessons are loaded" and "no lessons for YOUR
    # grade" are different sentences and only one of them is true for a
    # Primary 6 child right now.
    grade = str(profile.get("grade") or "").strip()
    if grade:
        for_grade = conn.execute(
            "SELECT COUNT(*) c FROM curriculum_books WHERE is_verified = 1 AND grade = ?",
            (grade,),
        ).fetchone()["c"]
        if not for_grade:
            any_book = conn.execute(
                "SELECT COUNT(*) c FROM curriculum_books WHERE is_verified = 1"
            ).fetchone()["c"]
            if any_book:
                return (f"Hi {name}. I don't have any grade {grade} books yet — "
                        f"only other years so far. Ask your teacher to add yours.")
            return f"Hi {name}. No books are loaded yet — ask your teacher to add some."
    else:
        has_content = conn.execute(
            "SELECT COUNT(*) c FROM curriculum_books WHERE is_verified = 1"
        ).fetchone()["c"]
        if not has_content:
            return f"Hi {name}. No books are loaded yet — ask your teacher to add some."

    return f"Hi {name}. I'm Souly. Ready when you are."
