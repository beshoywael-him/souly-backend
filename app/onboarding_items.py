"""
The entry activity — items and their fixed prompt ladders.

-----------------------------------------------------------------------------
WHY THE PROMPTS ARE HARDCODED HERE AND NOT GENERATED
-----------------------------------------------------------------------------
Everywhere else in Souly, hints are written by the LLM. Here they must not be.

Caffrey, Fuchs & Fuchs (2008) reviewed 24 studies of dynamic assessment and
found its predictive advantage over static testing holds under specific
conditions — one of which is that the feedback is NON-CONTINGENT, meaning
standardised and identical for every child.

The score we take from this activity is "how many prompts did this child
need". If the prompts differ per child, the counts aren't comparable and the
number means nothing. So: pre-written, fixed, same words every time.

Improvising is exactly right during ordinary tutoring. It is wrong here.
-----------------------------------------------------------------------------

THE LADDER, four rungs, following Veerbeek & Vogelaar (2025):

    1. metacognitive   — "what should you look at?"
    2. metacognitive   — narrower, still about strategy
    3. task-specific   — names the actual rule
    4. model           — solves one exactly like it

prompts_used is 0 if they got it unaided, up to 4 if they needed the model.
That single number is the dynamic assessment.

CONTENT CHOICE: figural series and analogies, not school content. We want to
measure reasoning under help, not which school the child went to. Emoji render
everywhere and need no image pipeline.
"""

# =============================================================================
# Part 1 — Interests
#
# Not measurement. Gunn & Delafield-Butt (2016) reviewed 20 studies of teaching
# through a child's focused interests: ALL 20 showed motivation and engagement
# gains, 15 showed better social engagement. It is the best-evidenced
# motivation lever available for this population.
#
# Embedded in examples afterwards. Never used as a reward for compliance —
# autistic co-authors of the 2025 follow-up warn that contingent access to
# interests teaches compliance rather than learning.
# =============================================================================

INTERESTS = [
    {"code": "dinosaurs", "emoji": "🦕", "label": "Dinosaurs"},
    {"code": "space",     "emoji": "🚀", "label": "Space"},
    {"code": "animals",   "emoji": "🐘", "label": "Animals"},
    {"code": "football",  "emoji": "⚽", "label": "Football"},
    {"code": "cars",      "emoji": "🏎️", "label": "Cars"},
    {"code": "cooking",   "emoji": "🍳", "label": "Cooking"},
    {"code": "drawing",   "emoji": "🎨", "label": "Drawing"},
    {"code": "music",     "emoji": "🎵", "label": "Music"},
    {"code": "building",  "emoji": "🧱", "label": "Building things"},
    {"code": "computers", "emoji": "💻", "label": "Computers"},
    {"code": "trains",    "emoji": "🚂", "label": "Trains"},
    {"code": "sea",       "emoji": "🐙", "label": "The sea"},
]


# =============================================================================
# Part 2 — Graduated prompts core
#
# Five items. Expect wide variance: Resing's arithmetic study saw prompt counts
# from 1 to 85 across five sets, so the scoring has to tolerate floor and
# ceiling.
# =============================================================================

REASONING_ITEMS = [
    {
        "code": "SER.1",
        "kind": "series",
        "question": "What comes next?",
        "sequence": ["🔴", "🔵", "🔴", "🔵", "🔴", "❓"],
        "options": ["🔵", "🔴", "🟡", "🟢"],
        "correct_index": 0,
        "prompts": [
            "Look at the colours from the start. Do they do something again and again?",
            "Try saying them out loud, one at a time.",
            "They take turns: red, blue, red, blue. What has to come after red?",
            "It goes red, blue, red, blue, red — so the next one is blue. "
            "The two colours keep swapping.",
        ],
    },
    {
        "code": "SER.2",
        "kind": "series",
        "question": "What comes next?",
        "sequence": ["⭐", "⭐⭐", "⭐⭐⭐", "❓"],
        "options": ["⭐⭐⭐⭐", "⭐", "⭐⭐", "⭐⭐⭐⭐⭐"],
        "correct_index": 0,
        "prompts": [
            "Count the stars in each box. What do you notice?",
            "Is the number getting bigger or smaller each time?",
            "It goes one, two, three — each box gains one more star.",
            "One star, two stars, three stars, so next is four. "
            "Each step adds one.",
        ],
    },
    {
        "code": "ANA.1",
        "kind": "analogy",
        "question": "Finish the pair.",
        "analogy": {"a": "🐶", "b": "🐕‍🦺", "c": "🐱", "d": "❓"},
        "analogy_label": "small dog is to big dog as small cat is to…",
        "options": ["🐯", "🐭", "🐦", "🐟"],
        "correct_index": 0,
        "prompts": [
            "Look at the first two pictures. What changed between them?",
            "The first one got bigger. Now do the same thing to the cat.",
            "Small animal becomes a big version of the same animal.",
            "Dog becomes a big dog, so cat becomes a big cat — a tiger. "
            "The change is small to big, same kind of animal.",
        ],
    },
    {
        "code": "SER.3",
        "kind": "series",
        "question": "What comes next?",
        "sequence": ["🔺", "🔺", "🔻", "🔺", "🔺", "🔻", "🔺", "❓"],
        "options": ["🔺", "🔻", "🔵", "⬛"],
        "correct_index": 0,
        "prompts": [
            "Look for a small group of shapes that keeps repeating.",
            "Try covering the row and looking at just three shapes at a time.",
            "The pattern is up, up, down — then it starts again.",
            "Up, up, down. Up, up, down. Up… so the next one is up. "
            "The group of three keeps repeating.",
        ],
    },
    {
        "code": "ANA.2",
        "kind": "analogy",
        "question": "Finish the pair.",
        "analogy": {"a": "🌧️", "b": "☂️", "c": "☀️", "d": "❓"},
        "analogy_label": "rain is to umbrella as sun is to…",
        "options": ["🕶️", "🧤", "⛄", "🔦"],
        "correct_index": 0,
        "prompts": [
            "Think about what the first picture makes you need.",
            "When it rains you use an umbrella. What do you use when it's sunny?",
            "Each pair is weather and the thing that helps you with it.",
            "Rain goes with an umbrella, so sun goes with sunglasses. "
            "Weather, then the thing you use for it.",
        ],
    },
]


# =============================================================================
# Part 3 — Reading vs listening
#
# The Simple View of Reading: comprehension = decoding x language
# comprehension. The two dissociate, and the dissociation is measurable.
# Foorman et al. (2018) explain 68-78% of reading-comprehension variance in
# grades 1-3 from just these two factors.
#
# Two equivalent-difficulty items: one read silently, one heard. A child who
# gets the audio item and misses the written one has a DECODING difficulty,
# and read-aloud is the evidenced accommodation for that (Wood et al. 2018,
# 22 studies, d = 0.35 on comprehension).
#
# This is the single legitimate modality branch in the whole system, and it is
# NOT a learning style — it's a measured skill gap with a matching support.
#
# Silent reading, tap to answer: no speech recognition anywhere, because ASR on
# children's voices is the least reliable part of any pipeline like this.
# =============================================================================

MODALITY_ITEMS = [
    {
        "code": "READ.1",
        "kind": "reading",
        "mode": "read",
        "passage": (
            "Sam had six red marbles in a small blue bag. On the way to school "
            "the bag tipped over and two of the marbles rolled away down the "
            "hill. Sam picked up the ones that were left and put them back."
        ),
        "question": "How many marbles did Sam put back in the bag?",
        "options": ["4", "6", "2", "8"],
        "correct_index": 0,
    },
    {
        "code": "LIST.1",
        "kind": "listening",
        "mode": "listen",
        "passage": (
            "Mia had five green apples in a paper bag. On the way home from the "
            "shop the bag split and one of the apples rolled into the road. Mia "
            "carried the rest of them home in her arms."
        ),
        "question": "How many apples did Mia carry home?",
        "options": ["4", "5", "1", "6"],
        "correct_index": 0,
    },
]


# =============================================================================
# Part 4 — Preferences
#
# Honestly labelled: these are accessibility settings collected pleasantly,
# not a psychological profile. They go straight to student_settings.
# =============================================================================

PREFERENCE_QUESTIONS = [
    {
        "code": "PREF.SOUND",
        "question": "Should I read things out loud to you?",
        "options": [
            {"value": True,  "label": "Yes please", "emoji": "🔊"},
            {"value": False, "label": "No thanks",  "emoji": "🔇"},
        ],
        "setting": "read_aloud",
    },
    {
        "code": "PREF.MOTION",
        "question": "Do you like things that move and wiggle on screen?",
        "options": [
            {"value": False, "label": "Yes, they're fun", "emoji": "✨"},
            {"value": True,  "label": "No, keep it still", "emoji": "🧘"},
        ],
        "setting": "reduce_motion",
    },
    {
        "code": "PREF.TEXT",
        "question": "How big should the words be?",
        "options": [
            {"value": "small",  "label": "Small",  "emoji": "🔤"},
            {"value": "medium", "label": "Medium", "emoji": "🔠"},
            {"value": "large",  "label": "Big",    "emoji": "🅰️"},
        ],
        "setting": "font_size",
    },
    {
        "code": "PREF.COLOUR",
        "question": "Which colours feel best?",
        "options": [
            {"value": "light",  "label": "Bright", "emoji": "☀️"},
            {"value": "purple", "label": "Calm",   "emoji": "🌷"},
            {"value": "dark",   "label": "Dark",   "emoji": "🌙"},
        ],
        "setting": "theme",
    },
]


# =============================================================================
# The whole activity, in order.
#
# Shown to the child UP FRONT as a map before anything starts. Intolerance of
# uncertainty explains ~45% of sensory-sensitivity variance in autistic
# children (Wigham 2015) — knowing what's coming is the single highest-leverage
# thing we can do here, and it costs one screen.
# =============================================================================

ACTIVITY_PLAN = [
    {"key": "interests",   "label": "Things you like",  "emoji": "❤️",  "count": 1},
    {"key": "reasoning",   "label": "Some puzzles",     "emoji": "🧩",  "count": len(REASONING_ITEMS)},
    {"key": "modality",    "label": "Two short stories", "emoji": "📖", "count": len(MODALITY_ITEMS)},
    {"key": "preferences", "label": "How you like it",  "emoji": "⚙️",  "count": len(PREFERENCE_QUESTIONS)},
]

# Hard cap. A 2025 feasibility study budgeted 20-30 minutes for dynamic testing
# in a clinical sample and saw sessions run to 2.5 hours, with 22% data loss
# and participants quitting from frustration. Whatever happens, this ends.
MAX_MINUTES = 15


def _spread_answer_positions() -> None:
    """
    Move the right answer off position 1.

    Authoring every item with the correct option first is convenient and
    completely invalidates the instrument: a child who always taps the top
    button scores full marks, and prompt counts — the thing we actually
    measure — collapse to zero. Judges spot it in one run.

    The rotation is derived from the item code, so it is FIXED: the same for
    every child, on every device, on every boot. That matters as much as the
    spread does — Caffrey, Fuchs & Fuchs (2008) found dynamic assessment's
    predictive value lives in non-contingent administration, and randomising
    per session would make two children's prompt counts incomparable.
    """
    for item in REASONING_ITEMS + MODALITY_ITEMS:
        options = item["options"]
        n = len(options)
        if n < 2:
            continue
        shift = sum(ord(c) for c in item["code"]) % n
        if shift == 0:
            continue
        item["options"] = options[-shift:] + options[:-shift]
        item["correct_index"] = (item["correct_index"] + shift) % n


_spread_answer_positions()


def public_item(item: dict) -> dict:
    """
    An item as the browser sees it.

    correct_index and the prompt ladder are stripped: the client asks the
    server to check, so neither the answer nor the next hint sits in the page
    source where a curious child can read it.
    """
    out = {k: v for k, v in item.items()
           if k not in ("correct_index", "prompts")}
    out["total_prompts"] = len(item.get("prompts", []))
    return out


def find_item(code: str) -> dict | None:
    for item in REASONING_ITEMS + MODALITY_ITEMS:
        if item["code"] == code:
            return item
    return None
