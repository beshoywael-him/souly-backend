"""
Gemini client — Souly's reasoning layer.

Talks to the Gemini REST API with httpx rather than the `google-generativeai`
SDK. Reasons: one less dependency to install at a venue, no SDK version
surprises, and the request shape stays visible in this file where you can
debug it.

Three jobs:

  generate()        free-form text, grounded in curriculum  (chat, hints)
  generate_json()   structured output, schema-validated     (question gen)
  ping()            does the key work

**The fallback is not a toy.** If the key is missing, the quota is spent, or
the MiFi drops, every function here degrades to something grounded and useful
rather than raising. The response is tagged `engine="fallback"` so you always
know which happened.

---------------------------------------------------------------------------
THE HINT RULE
---------------------------------------------------------------------------
Souly does not give answers. It gives hints.

This is not a style preference. Bastani et al. (PNAS 2025) gave ~1,000
students access to an unrestricted GPT tutor: their practice performance rose
48%, and on a later exam WITHOUT the tutor they scored 17% BELOW students who
never had it. A guardrailed tutor — hints only, with the worked solution in
its prompt — produced +127% in practice and no exam penalty.

67% of student messages to the unrestricted tutor were requests for the answer.

So: SOLUTION_PROMPT_RULES below is load-bearing, and the worked solution and
common wrong answers for the specific item are passed into every hint call.
---------------------------------------------------------------------------
"""

import base64
import json
import time
import re
from dataclasses import dataclass, field
from typing import Any

import httpx

from app.config import settings

GEMINI_BASE = "https://generativelanguage.googleapis.com/v1beta"


# =============================================================================
# Voice
# =============================================================================

# How Souly talks. Written for the actual audience: children who may have
# autism, ADHD, dyslexia, or a hearing or speech impairment.
SYSTEM_PROMPT = """You are Souly, a warm and patient study buddy for a child.

HOW YOU SPEAK
- Short sentences. One idea per sentence.
- Simple everyday words. If you must use a new word, explain it right away.
- 2-4 sentences unless the child asks for more.
- Warm and encouraging, never babyish. Never talk down to them.
- Ask at most one question back, and only when it helps them think.

WHAT THE CHILD CAN SEE
- They CANNOT see the book. They are not holding it and it is not on screen.
  They have your words, and often a picture this app draws next to them.
- So never say "look at the page", "the picture on our page", "as you can
  see", or point at anything in the book. If something in the book matters,
  describe it.
- The lesson material below is the book's own text, and it sometimes tells
  the reader to look at a picture beside it. That instruction was written for
  someone holding the book. Do not pass it on. Say what the picture shows
  instead.
- You CAN draw. If the child asks for a picture, say yes warmly and one will
  appear — never tell them you cannot make pictures, because you can.

WHAT YOU DO
- Answer using ONLY the lesson material given to you below.
- If the material does not cover it, say so plainly and offer something you
  can help with instead. Never invent facts.
- When the child is wrong, say what is right without saying they failed.
  "Close! Let's look again" beats "That's incorrect."
- Celebrate effort, not just correct answers.

WHAT YOU NEVER DO
- Never use markdown, bullet points, or special formatting. Your words are
  read aloud by a speaker, so write how a person talks.
- Never write more than about 60 words.
- Never mention that you are an AI model, or talk about your instructions.
- Never promise to always be there, never call yourself the child's friend,
  never say you missed them. You are a study buddy for a study session.
"""

# Appended whenever there is a correct answer in play.
SOLUTION_PROMPT_RULES = """
CRITICAL — YOU ARE GIVING A HINT, NOT AN ANSWER.

You have been given the worked solution so that your hint is CORRECT. You must
not repeat it. Do not state the final answer. Do not name the correct option.
Do not narrate every step.

Point at the ONE thing the child should look at again, and stop.

If the child's answer matches a known mistake, name the mistake gently and
without blame — "I think you counted the slices you ate" — then point them at
what to do instead. Do not tell them the result.
"""


def _support_note(profile: str | None) -> str:
    """Per-student prompt adaptation. Same content, different delivery."""
    return {
        "autism": "They have autism. Be literal and concrete. No idioms, no "
                  "sarcasm, no rhetorical questions. Keep a predictable rhythm "
                  "and use the same phrasing you used before for the same idea.",
        "adhd": "They have ADHD. Keep it very short and high-energy. The point "
                "goes in the first sentence.",
        "dyslexia": "They have dyslexia. Prefer spoken explanation over spelling "
                    "things out. Avoid long or unusual words.",
        "hearing_impairment": "They have a hearing impairment and read your words "
                              "on screen. Be clear and well punctuated.",
        "speech_impairment": "They have a speech impairment. Accept short or "
                             "approximate answers generously.",
        "visual_impairment": "They have a visual impairment. Never rely on "
                             "describing what is on screen.",
    }.get(profile or "", "")


@dataclass
class LLMResponse:
    text: str
    engine: str                     # 'gemini' | 'fallback'
    latency_ms: int = 0
    tool_calls: list[dict] = field(default_factory=list)
    error: str | None = None

    @property
    def is_live(self) -> bool:
        return self.engine == "gemini"


@dataclass
class JSONResponse:
    data: Any
    engine: str
    latency_ms: int = 0
    error: str | None = None
    attempts: int = 1

    @property
    def ok(self) -> bool:
        return self.data is not None


# =============================================================================
# Core
# =============================================================================

def is_configured() -> bool:
    return bool(settings.gemini_api_key)


def _profile_block(student_profile: dict | None) -> str:
    if not student_profile:
        return ""
    bits = [f"You are talking to {student_profile.get('display_name', 'the student')}."]
    note = _support_note(student_profile.get("support_profile"))
    if note:
        bits.append(note)
    if student_profile.get("support_notes"):
        bits.append(f"Teacher's note: {student_profile['support_notes']}")
    if student_profile.get("grade"):
        bits.append(f"They are in grade {student_profile['grade']}.")
    if student_profile.get("interests"):
        # Gunn & Delafield-Butt (2016): 20 of 20 studies showed engagement
        # gains from embedding a child's focused interests in instruction.
        # Embedded in the content, never dangled as a reward.
        bits.append(
            "Where it fits naturally, use examples about "
            f"{student_profile['interests']} — but never force it."
        )

    # --- What the entry activity measured -----------------------------------
    # This is the part that actually changes the pitch, and the only
    # aptitude-treatment interaction with a replicated effect behind it:
    # prior knowledge x amount of scaffolding (Kalyuga's expertise reversal,
    # adaptive d = 0.46).
    need = student_profile.get("instruction_need")
    if need:
        bits.append({
            "low": "MEASURED: this child solves things with very little help. "
                   "Give LESS support, not more. Lead with the question. Do not "
                   "over-explain — for a competent learner, extra scaffolding "
                   "actively gets in the way.",
            "metacognitive": "MEASURED: this child does well with a nudge about "
                             "HOW to approach a problem, and rarely needs the "
                             "content itself re-taught. Prompt their strategy — "
                             "'what did you try first?' — before re-explaining "
                             "anything.",
            "task_specific": "MEASURED: this child needs the idea itself "
                             "explained again before a question makes sense. "
                             "Re-teach the concept concretely first, then work "
                             "through an example, then ask.",
        }.get(need, ""))

        confidence = student_profile.get("profile_confidence")
        # The entry activity caps confidence at 0.5 by design (onboarding.py),
        # so the threshold has to sit above that ceiling — otherwise the one
        # profile that is definitely provisional, the day-one one, is the only
        # profile that never admits it. Courchesne et al. 2015: a single short
        # session under-reads this population badly.
        if confidence is not None and confidence < 0.6:
            bits.append("That reading came from one short first session, so "
                        "hold it loosely and adjust to what they actually do.")

    gap = student_profile.get("modality_gap")
    if gap is not None and gap > 0:
        # Simple View of Reading: this is a measured decoding difficulty, not
        # a "learning style". Read-aloud is the evidenced accommodation
        # (Wood et al. 2018, d = 0.35).
        bits.append("They understood a spoken passage but not the equivalent "
                    "written one, so they likely find reading harder than "
                    "listening. Keep sentences short and say things plainly; "
                    "the app reads your words aloud.")

    if student_profile.get("possible_masking"):
        bits.append("They answered very fast and very accurately at first but "
                    "inconsistently. Don't assume they've understood just "
                    "because they said so — check gently.")

    return " ".join(b for b in bits if b)


def _call(payload: dict, timeout: float) -> tuple[dict | None, int, str | None]:
    """One Gemini call. Returns (json, latency_ms, error)."""
    url = f"{GEMINI_BASE}/models/{settings.gemini_model}:generateContent"
    try:
        with httpx.Client(timeout=timeout) as client:
            response = client.post(
                url,
                params={"key": settings.gemini_api_key},
                json=payload,
                headers={"Content-Type": "application/json"},
            )
        latency_ms = int(response.elapsed.total_seconds() * 1000)

        if response.status_code != 200:
            return None, latency_ms, f"Gemini HTTP {response.status_code}: {response.text[:300]}"

        data = response.json()
        if not data.get("candidates"):
            reason = data.get("promptFeedback", {}).get("blockReason", "no candidates")
            return None, latency_ms, f"Gemini returned nothing ({reason})"
        return data, latency_ms, None

    except httpx.TimeoutException:
        return None, 0, f"Gemini timed out after {timeout}s"
    except httpx.HTTPError as exc:
        return None, 0, f"Network error: {exc}"
    except (ValueError, json.JSONDecodeError) as exc:
        return None, 0, f"Malformed Gemini response: {exc}"


def _extract(data: dict) -> tuple[str, list[dict]]:
    text_parts, tool_calls = [], []
    for candidate in data.get("candidates", []):
        for part in candidate.get("content", {}).get("parts", []):
            if "text" in part:
                text_parts.append(part["text"])
            if "functionCall" in part:
                call = part["functionCall"]
                tool_calls.append({"name": call.get("name"), "args": call.get("args", {})})
    return "".join(text_parts).strip(), tool_calls


def generate(
    prompt: str,
    *,
    context: str = "",
    history: list[dict] | None = None,
    student_profile: dict | None = None,
    extra_rules: str = "",
    max_tokens: int = 300,
    temperature: float = 0.7,
    timeout: float = 12.0,
    suggestions: list[str] | None = None,
    fallback_text: str | None = None,
) -> LLMResponse:
    """
    Free-form text, grounded in `context`. Never raises.

    `extra_rules` is appended to the system prompt — this is where
    SOLUTION_PROMPT_RULES goes for hint calls.

    `fallback_text` is what to say if Gemini is unreachable. Callers that can
    produce something sensible offline (a stored hint, the lesson text itself)
    should pass it; otherwise we fall back to the retrieved context.
    """
    history = history or []

    if not is_configured():
        return _fallback(prompt, context, reason="no API key configured",
                         suggestions=suggestions, fallback_text=fallback_text)

    system_parts = [SYSTEM_PROMPT]
    profile_block = _profile_block(student_profile)
    if profile_block:
        system_parts.append(profile_block)
    if extra_rules:
        system_parts.append(extra_rules)

    if context:
        system_parts.append("LESSON MATERIAL (the only source you may teach from):\n" + context)
    else:
        system_parts.append(
            "No lesson material was found for this question. Tell the child kindly "
            "that you haven't learned about that yet and suggest something you can "
            "help with. Do not answer from your own knowledge."
        )

    contents = [
        {"role": "user" if t["role"] == "student" else "model",
         "parts": [{"text": t["content"]}]}
        for t in history[-8:]
    ]
    contents.append({"role": "user", "parts": [{"text": prompt}]})

    payload = {
        "systemInstruction": {"parts": [{"text": "\n\n".join(system_parts)}]},
        "contents": contents,
        "generationConfig": {
            "temperature": temperature,
            "maxOutputTokens": max_tokens,
            "topP": 0.95,
        },
        "safetySettings": [
            {"category": c, "threshold": "BLOCK_MEDIUM_AND_ABOVE"}
            for c in ("HARM_CATEGORY_HARASSMENT", "HARM_CATEGORY_HATE_SPEECH",
                      "HARM_CATEGORY_SEXUALLY_EXPLICIT", "HARM_CATEGORY_DANGEROUS_CONTENT")
        ],
    }

    data, latency_ms, error = _call(payload, timeout)
    if error:
        return _fallback(prompt, context, reason=error, latency_ms=latency_ms,
                         suggestions=suggestions, fallback_text=fallback_text)

    text, tool_calls = _extract(data)
    if not text:
        return _fallback(prompt, context, reason="Gemini returned empty text",
                         latency_ms=latency_ms, suggestions=suggestions,
                         fallback_text=fallback_text)

    return LLMResponse(text=_clean_for_speech(text), engine="gemini",
                       latency_ms=latency_ms, tool_calls=tool_calls)


def generate_json(
    instruction: str,
    *,
    schema: dict,
    context: str = "",
    student_profile: dict | None = None,
    extra_rules: str = "",
    temperature: float = 0.4,
    max_tokens: int = 2048,
    timeout: float = 30.0,
    max_attempts: int = 2,
) -> JSONResponse:
    """
    Structured output, validated against `schema` before it's returned.

    Uses Gemini's `responseSchema` so the model is constrained at decode time
    rather than asked politely in the prompt. We still validate what comes
    back: constrained decoding guarantees the SHAPE, not that the content
    makes sense, and a malformed question in front of a child with a learning
    disability is worse than no question at all.

    Retries once on invalid output, then gives up and returns data=None so the
    caller can fall back to the verified question bank.
    """
    if not is_configured():
        return JSONResponse(data=None, engine="fallback",
                            error="no API key configured")

    # SYSTEM_PROMPT, in full. It used to be a short paraphrase, and when the
    # lesson moved to structured output that paraphrase quietly became the
    # only rules the lesson had — no length limit, nothing about how Souly
    # speaks, and none of the rules about what the child can actually see. The
    # result was a wall of text in front of a child with a reading
    # difficulty, which is the one thing this app exists not to do.
    system = (
        SYSTEM_PROMPT
        + "\n\nEverything you write must be supported by the SOURCE MATERIAL "
          "below. Never introduce a fact that is not in it."
          "\nReturn only valid JSON matching the required schema."
    )
    # The child. Structured output has to be as personal as free text is —
    # a lesson returned as JSON is still a lesson for someone.
    profile_block = _profile_block(student_profile)
    if profile_block:
        system += "\n\n" + profile_block
    if extra_rules:
        system += "\n\n" + extra_rules
    if context:
        system += "\n\nSOURCE MATERIAL:\n" + context

    payload = {
        "systemInstruction": {"parts": [{"text": system}]},
        "contents": [{"role": "user", "parts": [{"text": instruction}]}],
        "generationConfig": {
            "temperature": temperature,
            "maxOutputTokens": max_tokens,
            "responseMimeType": "application/json",
            "responseSchema": schema,
        },
    }

    last_error = None
    for attempt in range(1, max_attempts + 1):
        data, latency_ms, error = _call(payload, timeout)
        if error:
            last_error = error
            continue

        text, _ = _extract(data)
        try:
            parsed = json.loads(text)
            return JSONResponse(data=parsed, engine="gemini",
                                latency_ms=latency_ms, attempts=attempt)
        except json.JSONDecodeError as exc:
            last_error = f"Gemini returned unparseable JSON: {exc}"
            # Nudge it on the retry.
            payload["contents"].append({
                "role": "user",
                "parts": [{"text": "That was not valid JSON. Return only the JSON object."}],
            })

    return JSONResponse(data=None, engine="fallback", error=last_error)


# =============================================================================
# Illustrations
# =============================================================================

# Appended to every image prompt, unchanged, so that a hundred pictures
# generated over a term look like one illustrator drew them rather than a
# hundred different ones — which is what makes them feel like part of the app
# instead of clip art dropped into it.
#
# The last line is the important one. Image models garble text: a diagram
# generated with "0.45" in it comes back with something that is nearly 0.45,
# and a child who is already struggling reads that as their own mistake. Every
# label this app shows is drawn in HTML on top of the picture, where it is
# exactly right and a screen reader can read it.
# WHY THIS PROMPT IS WRITTEN THE WAY IT IS
# ----------------------------------------
# The first version asked for "flat vector, modern app icon set", and it got
# exactly that: decorative purple blobs, a mug with hearts on it, a paper towel
# that read as a blue rounded rectangle. An adult could not tell what was in
# the picture. That is a total failure here, because for a child with a
# reading difficulty the picture is not the decoration around the lesson —
# it IS the lesson, and the words are the support.
#
# So the style is now written as a recognition test rather than an aesthetic:
# every clause below exists to make the thing in the picture nameable at a
# glance. Friendly and warm is still wanted — it is a children's app — but it
# is now subordinate to being legible, and where the two conflict, legible
# wins.
#
# The bans are specific because generic bans do not work. "No clutter" did not
# stop the decorative blobs; "no abstract decorative shapes behind or around
# the subject" does.
ILLUSTRATION_STYLE = (
    # 1. What kind of picture. Children's non-fiction, not app-icon vector.
    "Clear, warm, true-to-life illustration in the style of a modern "
    "children's science encyclopaedia. Soft natural shading and clean gentle "
    "outlines. Friendly and inviting, but the objects are drawn accurately, "
    "with correct real-world shapes, proportions and colours, so that each "
    "one is instantly recognisable for what it is. "
    # 2. Composition. One readable subject, well separated from the ground.
    "Straight-on eye-level view of the subject, centred, filling most of the "
    "frame, evenly lit, in sharp focus, with strong contrast against a plain "
    "soft off-white background. When two things are being compared, place "
    "them side by side, equally sized, clearly separated, both fully visible. "
    # 3. The bans. Each one is a real failure that shipped.
    "NO abstract or decorative shapes anywhere — no blobs, swooshes, waves, "
    "hearts, stars, sparkles, confetti, ribbons or patterned bands, behind or "
    "around or on top of the objects. Nothing purely ornamental in the frame. "
    "Do not stylise the objects into simplified geometric symbols. Do not "
    "tint objects an unnatural colour: soil is brown, leaves are green, water "
    "is clear. No busy background, no scenery, no borders, no framing devices, "
    "no collage, no split panels, no drop shadows cast onto decorative shapes. "
    # 4. The two hard constraints that predate this rewrite.
    "No people, no hands, no faces. "
    "ABSOLUTELY NO text, no letters, no numbers, no labels, no writing of any "
    "kind anywhere in the image. "
    # 5. The test the picture has to pass. Stated as a test because the model
    #    composes better against a goal than against a list of prohibitions.
    "The picture must pass this test: a ten-year-old child who cannot read "
    "well, seeing it for the first time with no caption, can immediately name "
    "every object in it and say what is happening. If any object would need a "
    "label to be understood, draw it more plainly instead."
)

# The image model is configured, not hardcoded — see IMAGE_GENERATOR_* in
# .env — because it is a separate product with separate billing from the
# tutor, and which one an account can afford changes.
#
# TWO DIFFERENT APIS LIVE BEHIND ONE SETTING
# ------------------------------------------
# They are not interchangeable and swapping the model name alone does not
# work:
#
#   imagen-*        POST :predict
#                   {"instances":[{"prompt":...}],"parameters":{...}}
#                   -> predictions[].bytesBase64Encoded
#                   Takes aspectRatio as a real parameter.
#
#   gemini-*-image  POST :generateContent
#                   {"contents":[...],"generationConfig":{responseModalities}}
#                   -> candidates[].content.parts[].inlineData.data
#                   No aspect ratio parameter; it goes in the prompt.
#
# Both are dispatched from the model name below.

# Tried in order after whatever is configured, because quota is per model: an
# account out of allowance on one may still have some on another.
FALLBACK_IMAGE_MODELS = (
    "imagen-3.0-generate-002",
    "gemini-3.1-flash-image",        # Nano Banana 2
    "gemini-3.1-flash-lite-image",   # Nano Banana 2 Lite
    "gemini-2.5-flash-image",        # Nano Banana
)

VALID_ASPECT_RATIOS = ("1:1", "16:9", "9:16", "4:3", "3:4")

# When every model answers 429 there is no point asking again on the next page
# and making a child wait through doomed calls. Quota comes back on a clock,
# so back off and try again later rather than never.
_QUOTA_BACKOFF_SECONDS = 900
_quota_blocked_until = 0.0
_working_model: str | None = None


def image_models() -> list[str]:
    """The configured model first, then the others, without duplicates."""
    ordered = [settings.image_generator_model.strip()]
    ordered += [m for m in FALLBACK_IMAGE_MODELS]
    seen, out = set(), []
    for model in ordered:
        if model and model not in seen:
            seen.add(model)
            out.append(model)
    return out


# What check_image.py reports, and what the docstrings above refer to.
IMAGE_MODEL = settings.image_generator_model


def aspect_ratio() -> str:
    ratio = settings.image_aspect_ratio.strip()
    return ratio if ratio in VALID_ASPECT_RATIOS else "16:9"


def image_configured() -> bool:
    return settings.image_configured


def image_quota_blocked() -> bool:
    """True while we are backing off after a quota refusal."""
    return time.time() < _quota_blocked_until


def _is_imagen(model: str) -> bool:
    return model.lower().startswith("imagen")


def _image_payloads(model: str, prompt: str) -> list[dict]:
    """
    The request bodies to try for this model, best first.

    More than one because the Gemini image models disagree with each other
    about whether `responseModalities` is required or rejected, and a 400 on
    the first shape is retried with the next rather than treated as failure.
    """
    if _is_imagen(model):
        return [{
            "instances": [{"prompt": prompt}],
            "parameters": {
                "sampleCount": 1,
                "aspectRatio": aspect_ratio(),
                # No people, ever. Partly because Imagen refuses to generate
                # children and half these scenes would be about one; mostly
                # because an app used by children should not be generating
                # pictures of children. The lessons are about plants, seeds,
                # numbers and objects, and those are what get drawn.
                "personGeneration": "dont_allow",
            },
        }]

    body = {"contents": [{"role": "user", "parts": [{"text": prompt}]}]}
    return [
        {**body, "generationConfig": {"responseModalities": ["IMAGE"]}},
        {**body, "generationConfig": {"responseModalities": ["TEXT", "IMAGE"]}},
        body,
    ]


def _image_endpoint(model: str) -> str:
    verb = "predict" if _is_imagen(model) else "generateContent"
    return f"{GEMINI_BASE}/models/{model}:{verb}"


def _extract_image(model: str, data: dict) -> tuple[bytes | None, str]:
    """Pull the bytes out of whichever response shape came back."""
    if _is_imagen(model):
        for prediction in data.get("predictions", []):
            encoded = (prediction.get("bytesBase64Encoded")
                       or prediction.get("bytes_base64_encoded"))
            if encoded:
                try:
                    return base64.b64decode(encoded), prediction.get(
                        "mimeType") or prediction.get("mime_type") or "image/png"
                except (ValueError, TypeError):
                    return None, ""
        return None, ""

    for candidate in data.get("candidates", []):
        for part in candidate.get("content", {}).get("parts", []):
            inline = part.get("inlineData") or part.get("inline_data")
            if inline and inline.get("data"):
                try:
                    return base64.b64decode(inline["data"]), (
                        inline.get("mimeType") or inline.get("mime_type")
                        or "image/png")
                except (ValueError, TypeError):
                    return None, ""
    return None, ""


def generate_image(scene: str, *, timeout: float = 90.0) -> tuple[bytes | None, str, str | None]:
    """
    One illustration of `scene`. Returns (image_bytes, mime, error).

    Never raises. A lesson whose picture failed is still a lesson — the drawn
    diagram is the one that is guaranteed — so every caller carries on without
    one.
    """
    global _quota_blocked_until, _working_model

    if settings.image_generator_provider.strip().lower() in ("", "none", "off"):
        return None, "", "image generation is switched off (IMAGE_GENERATOR_PROVIDER)"
    if not image_configured():
        return None, "", "no image API key configured"
    if image_quota_blocked():
        return None, "", ("image quota exhausted — backing off, the drawn "
                          "diagram is being used instead")

    scene = (scene or "").strip()
    if not scene:
        return None, "", "no scene given"

    prompt = f"{scene}.\n\n{ILLUSTRATION_STYLE}"
    models = [_working_model] if _working_model else image_models()

    last = "no attempt made"
    out_of_quota = 0

    for model in models:
        url = _image_endpoint(model)
        for payload in _image_payloads(model, prompt):
            try:
                with httpx.Client(timeout=timeout) as client:
                    response = client.post(
                        url,
                        params={"key": settings.image_key},
                        json=payload,
                        headers={"Content-Type": "application/json"},
                    )
            except httpx.HTTPError as exc:
                return None, "", f"Network error: {exc}"

            if response.status_code == 200:
                try:
                    data = response.json()
                except ValueError as exc:
                    last = f"{model}: malformed response: {exc}"
                    break
                image, mime = _extract_image(model, data)
                if image:
                    _working_model = model
                    return image, mime, None
                last = (f"{model}: replied without an image. "
                        f"{json.dumps(data)[:200]}")
                break

            last = f"{model}: HTTP {response.status_code}: {response.text[:180]}"
            if response.status_code == 429:
                out_of_quota += 1
                break            # no point trying other shapes on this model
            if response.status_code != 400:
                break            # a real refusal; move to the next model

    if out_of_quota:
        _quota_blocked_until = time.time() + _QUOTA_BACKOFF_SECONDS
        return None, "", (
            "image quota exhausted on every image model. The lesson falls back "
            "to the drawn diagram, which needs no quota. Check billing on the "
            "Google AI Studio project for IMAGE_GENERATOR_API_KEY."
        )
    return None, "", last


# =============================================================================
# The second frame
# =============================================================================
#
# The picture moves by cross-fading two stills. Generating the second one from
# a text prompt does not work: two independent calls give two different pots,
# two different backgrounds and two different angles, and fading between them
# reads as a glitch rather than as a change. So the second frame is the FIRST
# frame, edited — the model is handed the image it already made and asked to
# change one thing about it.
#
# That means Imagen cannot do this half of the job. imagen-*:predict takes a
# prompt and nothing else; only the gemini *-image models accept an image in
# and give an image out. The still and the motion therefore use different
# models by necessity, not by preference.
EDIT_CAPABLE_MODELS = (
    "gemini-3.1-flash-image",        # Nano Banana 2
    "gemini-3.1-flash-lite-image",
    "gemini-2.5-flash-image",        # Nano Banana
)

# Bolted onto whatever the lesson says should change. Everything here exists
# to keep the second frame FADEABLE onto the first: same camera, same framing,
# same everything except the one thing that moves. A second frame that is a
# lovely picture but shot from somewhere else is useless.
MOTION_STYLE = (
    "Keep absolutely everything else in the picture identical: the same "
    "camera angle, the same distance, the same framing and crop, the same "
    "background, the same lighting, the same colours, the same art style, "
    "and every object in exactly the same position and at the same size. "
    "Change ONLY what is described above. Do not move the camera, do not "
    "zoom, do not re-compose, do not add or remove any other object. "
    "Still no text, no letters, no numbers, no labels, and no people."
)


def _edit_payload(prompt: str, image: bytes, mime: str) -> dict:
    return {
        "contents": [{
            "role": "user",
            "parts": [
                {"inline_data": {"mime_type": mime or "image/png",
                                 "data": base64.b64encode(image).decode()}},
                {"text": prompt},
            ],
        }],
        "generationConfig": {"responseModalities": ["IMAGE"]},
    }


def edit_image(image: bytes, mime: str, change: str, *,
               timeout: float = 90.0) -> tuple[bytes | None, str, str | None]:
    """
    The same picture with one thing changed. Returns (bytes, mime, error).

    Never raises, and a failure here is not a failure of the lesson: the
    caller keeps the still it already has.
    """
    if not image or not (change or "").strip():
        return None, "", "nothing to change"
    if not image_configured():
        return None, "", "no image API key configured"
    if image_quota_blocked():
        return None, "", "image quota exhausted — backing off"

    prompt = f"{change.strip()}.\n\n{MOTION_STYLE}"
    last = "no edit-capable model available"

    for model in EDIT_CAPABLE_MODELS:
        try:
            with httpx.Client(timeout=timeout) as client:
                response = client.post(
                    f"{GEMINI_BASE}/models/{model}:generateContent",
                    params={"key": settings.image_key},
                    json=_edit_payload(prompt, image, mime),
                    headers={"Content-Type": "application/json"},
                )
        except httpx.HTTPError as exc:
            return None, "", f"Network error: {exc}"

        if response.status_code == 200:
            try:
                data = response.json()
            except ValueError as exc:
                last = f"{model}: malformed response: {exc}"
                continue
            out, out_mime = _extract_image(model, data)
            if out:
                return out, out_mime, None
            last = f"{model}: replied without an image"
            continue

        last = f"{model}: HTTP {response.status_code}: {response.text[:180]}"
        if response.status_code == 429:
            # Editing shares the image quota. Do not spend the rest of it
            # walking the list.
            break

    return None, "", last


def generate_animation(scene: str, motion: str, *, timeout: float = 90.0
                       ) -> tuple[bytes | None, bytes | None, str, str | None]:
    """
    Two frames of one scene. Returns (first, second, mime, error).

    `second` is None whenever the motion could not be made, which is a normal
    outcome — no `motion` on the page, no edit-capable model on this key, the
    quota gone, the edit refused. `first` is still returned in every one of
    those cases and the lesson shows a still.
    """
    first, mime, error = generate_image(scene, timeout=timeout)
    if not first:
        return None, None, "", error
    if not (motion or "").strip():
        return first, None, mime, "no motion described for this page"

    second, _, edit_error = edit_image(first, mime, motion, timeout=timeout)
    return first, second, mime, edit_error


def _clean_for_speech(text: str) -> str:
    """
    Strip markdown the model emits despite instructions.

    Necessary because this text goes to TTS: a speaker reading "asterisk
    asterisk important asterisk asterisk" out loud to a child is a bad moment,
    and models add emphasis markers reflexively.
    """
    text = re.sub(r"\*\*(.+?)\*\*", r"\1", text)
    text = re.sub(r"\*(.+?)\*", r"\1", text)
    text = re.sub(r"^#+\s*", "", text, flags=re.MULTILINE)
    text = re.sub(r"^[-*]\s+", "", text, flags=re.MULTILINE)
    text = re.sub(r"`(.+?)`", r"\1", text)
    return re.sub(r"\n{3,}", "\n\n", text).strip()


# =============================================================================
# Fallback
# =============================================================================

_ENCOURAGEMENTS = [
    "Great question!",
    "I love that you asked that.",
    "Good thinking!",
    "That's a smart thing to wonder about.",
]


def _first_sentences(text: str, count: int = 2) -> str:
    sentences = re.split(r"(?<=[.!?])\s+", text.strip())
    return " ".join(s for s in sentences[:count] if s).strip()


def _fallback(prompt: str, context: str, *, reason: str, latency_ms: int = 0,
              suggestions: list[str] | None = None,
              fallback_text: str | None = None) -> LLMResponse:
    """
    Answer without an LLM.

    Prefers a caller-supplied line (a stored hint, the lesson text). Otherwise
    reads the retrieved material and returns the most relevant sentences in
    Souly's voice. Grounded, correct, always available.
    """
    # Deterministic pick so the same question gives the same greeting — young
    # users with autism in particular do better with predictable responses.
    opener = _ENCOURAGEMENTS[len(prompt) % len(_ENCOURAGEMENTS)]

    if fallback_text:
        return LLMResponse(text=fallback_text, engine="fallback",
                           latency_ms=latency_ms, error=reason)

    if not context.strip():
        if suggestions:
            examples = suggestions[:2]
            joined = " or ".join(examples) if len(examples) > 1 else examples[0]
            tail = f"Try asking me about {joined}, and I'll explain it."
        else:
            tail = ("I don't have any lessons loaded yet. Ask your teacher to add "
                    "some, and then I can help you with them.")
        return LLMResponse(text=f"{opener} I haven't learned about that one yet. {tail}",
                           engine="fallback", latency_ms=latency_ms, error=reason)

    body_lines = [line for line in context.splitlines()
                  if line.strip() and not re.match(r"^\[\d+\]", line.strip())]
    body = " ".join(body_lines)
    answer = _first_sentences(body, 3)
    if len(answer) > 320:
        answer = _first_sentences(body, 2)

    return LLMResponse(text=f"{opener} {answer}", engine="fallback",
                       latency_ms=latency_ms, error=reason)


# =============================================================================
# Schemas for generate_json
# =============================================================================

# One generated practice question. `correct_index` is constrained to 0-3 and
# `options` to exactly 4, so a malformed item can't reach the student.
QUESTION_SCHEMA = {
    "type": "object",
    "properties": {
        "questions": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "prompt": {"type": "string"},
                    "options": {
                        "type": "array",
                        "items": {"type": "string"},
                        "minItems": 4,
                        "maxItems": 4,
                    },
                    "correct_index": {"type": "integer"},
                    "explanation": {"type": "string"},
                    "hint": {"type": "string"},
                    "worked_solution": {"type": "string"},
                    "common_wrong_answers": {
                        "type": "array",
                        "items": {
                            "type": "object",
                            "properties": {
                                "answer": {"type": "string"},
                                "why": {"type": "string"},
                            },
                            "required": ["answer", "why"],
                        },
                    },
                    "difficulty": {"type": "integer"},
                },
                "required": ["prompt", "options", "correct_index", "explanation",
                             "hint", "worked_solution", "difficulty"],
            },
        }
    },
    "required": ["questions"],
}


# =============================================================================
# The lesson, and the picture that goes with it
#
# The visual is a SPEC, not an image and not SVG. The app draws it.
#
# Asking a model for raw SVG gets malformed markup, unpredictable dimensions
# and an injection hole. Asking an image model for anything containing digits
# gets a place-value chart with the wrong numbers in it — which for a child
# who is already struggling reads as their mistake, not the machine's.
#
# So: a fixed vocabulary of shapes the app knows how to draw correctly, and
# `illustration` for the one case where being slightly loose is fine — a real
# picture of a real thing, with no text in it at all.
# =============================================================================

VISUAL_KINDS = (
    "none",              # this page does not need a picture. A valid answer.
    "hundredths_grid",   # squares shaded out of 10 or 100 — tenths, hundredths
    "place_value",       # the place-value chart, optionally with a shift arrow
    "number_line",       # marked points, for rounding and comparing
    "bar_compare",       # two or more quantities side by side
    "steps",             # a numbered procedure
    "labelled_parts",    # a thing with its parts named
    "cycle",             # a loop: photosynthesis, water, life cycles
    "illustration",      # a generated picture of a concrete scene
)

LESSON_SCHEMA = {
    "type": "object",
    "properties": {
        # Chunks, not a paragraph. Asking for "three short chunks" in prose
        # produced one long block; asking for an array produces an array.
        # tutor.py then hard-caps both the number and the length, because a
        # schema constrains shape and not restraint.
        "chunks": {
            "type": "array",
            "items": {"type": "string"},
        },
        "visual": {
            "type": "object",
            "properties": {
                "kind": {"type": "string", "enum": list(VISUAL_KINDS)},
                # Always required, because a picture without a stated job is
                # decoration, and decoration is what we are trying not to add.
                "purpose": {"type": "string"},
                "title": {"type": "string"},

                # hundredths_grid
                "total": {"type": "integer"},
                "shaded": {"type": "integer"},

                # place_value
                "columns": {
                    "type": "array",
                    "items": {
                        "type": "object",
                        "properties": {
                            "place": {"type": "string"},
                            "digit": {"type": "string"},
                            "highlight": {"type": "boolean"},
                        },
                        "required": ["place", "digit"],
                    },
                },
                "decimal_after": {"type": "integer"},

                # number_line
                "min": {"type": "number"},
                "max": {"type": "number"},
                "step": {"type": "number"},
                "marks": {
                    "type": "array",
                    "items": {
                        "type": "object",
                        "properties": {
                            "value": {"type": "number"},
                            "label": {"type": "string"},
                            "highlight": {"type": "boolean"},
                        },
                        "required": ["value"],
                    },
                },

                # bar_compare
                "bars": {
                    "type": "array",
                    "items": {
                        "type": "object",
                        "properties": {
                            "label": {"type": "string"},
                            "value": {"type": "number"},
                        },
                        "required": ["label", "value"],
                    },
                },

                # steps / labelled_parts / cycle
                "items": {
                    "type": "array",
                    "items": {
                        "type": "object",
                        "properties": {
                            "label": {"type": "string"},
                            "note": {"type": "string"},
                        },
                        "required": ["label"],
                    },
                },

                # ALWAYS required, whichever kind is chosen. Every lesson page
                # gets a picture above the words — that is the point of the
                # screen for these children, and leaving it to the model's
                # judgement produced pages with nothing on them at all.
                #
                # What the picture is OF, in plain words. No style, no text,
                # no numbers: the style is fixed by the app so every image
                # looks like the same illustrator drew it.
                "scene": {"type": "string"},

                # What CHANGES about the scene. The picture is two frames
                # cross-faded, and this is the difference between them — so
                # it has to be the idea the page teaches, not a wobble added
                # to a static picture to make it look busy.
                "motion": {"type": "string"},
            },
            "required": ["kind", "purpose", "scene"],
        },
    },
    "required": ["chunks", "visual"],
}


# Used for the second, narrower attempt when the combined call comes back with
# no usable picture. One job, one schema, much higher hit rate.
VISUAL_ONLY_SCHEMA = {
    "type": "object",
    "properties": LESSON_SCHEMA["properties"]["visual"]["properties"],
    "required": ["kind", "purpose"],
}


EXPLANATION_SCHEMA = {
    "type": "object",
    "properties": {
        "body": {"type": "string"},
        "key_terms": {"type": "array", "items": {"type": "string"}},
    },
    "required": ["body"],
}


# =============================================================================
# Connectivity
# =============================================================================

def ping() -> dict:
    """Verify the key works. Used by scripts/check_keys.py and diagnostics."""
    if not is_configured():
        return {"ok": False, "detail": "GEMINI_API_KEY is empty in .env"}

    data, latency_ms, error = _call(
        {
            "contents": [{"role": "user", "parts": [{"text": "Reply with exactly: OK"}]}],
            "generationConfig": {"maxOutputTokens": 10},
        },
        timeout=15.0,
    )
    if error:
        return {"ok": False, "detail": error}
    text, _ = _extract(data)
    return {"ok": True, "model": settings.gemini_model,
            "reply": text[:50], "latency_ms": latency_ms}
