"""
Souly — the tutor endpoints: help, hints, question generation, voice.

The voice path is:

    browser mic -> webm blob -> POST /voice/ask -> ElevenLabs STT
      -> RAG -> Gemini -> TTS -> audio back to the browser

`/voice/ask` does all of that in one request. One round trip instead of three
matters over a MiFi router: each hop is another chance for latency to make
Souly feel unresponsive, and a tutor that pauses awkwardly loses a child's
attention — which is the exact problem this project exists to solve.
"""

import base64
import sqlite3

from fastapi import APIRouter, Depends, File, Form, HTTPException, UploadFile
from fastapi.responses import Response
from pydantic import BaseModel, Field

from app.db import db_dependency
from app.deps import get_student
from app.services import llm, stt, tts, tutor

router = APIRouter(prefix="/api/students/{student_ext_id}", tags=["tutor"])

# Cap on uploaded audio. A 30-second answer from a child is well under 1MB;
# anything larger is a stuck recorder, not speech.
MAX_AUDIO_BYTES = 8 * 1024 * 1024


class ChatRequest(BaseModel):
    message: str = Field(..., min_length=1, max_length=1000)
    topic_id: int | None = None
    session_id: int | None = None
    # What the child is looking at right now: a page of the book. Sent by the
    # lesson screen so Souly answers about the page in front of them rather
    # than whatever retrieval happens to match.
    page_id: int | None = None
    speak: bool = Field(False, description="Also return synthesized audio.")


class ExplainRequest(BaseModel):
    """The three help buttons under Souly on the lesson screen."""
    mode: str = Field(..., pattern="^(simpler|example|another_way)$")
    seconds_on_page: int = Field(0, ge=0)
    initiated_by: str = Field("student", pattern="^(student|souly)$")
    speak: bool = True


class HintRequest(BaseModel):
    """One rung of the ladder. Tier 4 is the answer and arrives last."""
    tier: int = Field(1, ge=1, le=4)
    student_answer: str | None = None
    quiz_id: int | None = None
    attempts_before: int = Field(0, ge=0)
    seconds_before: int = Field(0, ge=0)
    initiated_by: str = Field("student", pattern="^(student|souly)$")
    speak: bool = True


class StallCheck(BaseModel):
    """Polled by the lesson screen so Souly can speak first."""
    seconds_on_page: int = Field(0, ge=0)
    wrong_attempts: int = Field(0, ge=0)
    already_offered: bool = False


class SpeakRequest(BaseModel):
    text: str = Field(..., min_length=1, max_length=2000)
    voice_id: str | None = None


def _speech_payload(text: str, voice_id: str | None = None) -> dict:
    """Synthesize and package audio as base64 for a JSON response."""
    speech = tts.synthesize(text, voice_id=voice_id)
    return {
        "provider": speech.provider,
        "use_browser_tts": speech.use_browser_tts,
        "audio_base64": (
            base64.b64encode(speech.audio).decode() if speech.audio else None
        ),
        "mime_type": speech.mime_type,
        "latency_ms": speech.latency_ms,
        "error": speech.error,
    }


# =============================================================================
# Text chat
# =============================================================================

@router.post("/chat", summary="Ask Souly a question (text)")
def chat(
    payload: ChatRequest,
    student: sqlite3.Row = Depends(get_student),
    conn: sqlite3.Connection = Depends(db_dependency),
) -> dict:
    reply = tutor.answer(
        conn, student["id"], payload.message,
        input_mode="text",
        session_id=payload.session_id,
        topic_id=payload.topic_id,
        page_id=payload.page_id,
    )

    body = {
        "reply": reply.text,
        "engine": reply.engine,
        "grounded": reply.grounded,
        "latency_ms": reply.latency_ms,
        "source_refs": reply.source_refs,
        "suggested_mode": reply.suggested_mode,
        "suggested_topic_id": reply.suggested_topic_id,
        "award": reply.award,
        "warning": reply.error,
        # Set when the child asked to be shown something. The picture itself
        # is fetched separately, so the sentence appears immediately and the
        # drawing arrives after it.
        "illustration_url": (
            f"/api/students/{student['external_id']}"
            f"/curriculum/pages/{payload.page_id}/illustration"
            if (reply.visual or {}).get("scene") else None
        ),
    }
    if payload.speak:
        body["speech"] = _speech_payload(reply.text)
    return body


@router.get("/chat/history", summary="Recent chat messages")
def chat_history(
    limit: int = 30,
    student: sqlite3.Row = Depends(get_student),
    conn: sqlite3.Connection = Depends(db_dependency),
) -> list[dict]:
    rows = conn.execute(
        "SELECT role, content, input_mode, engine, created_at FROM chat_messages "
        "WHERE student_id = ? ORDER BY id DESC LIMIT ?",
        (student["id"], min(limit, 100)),
    ).fetchall()
    return [dict(r) for r in reversed(rows)]


@router.delete("/chat/history", summary="Clear chat history")
def clear_history(
    student: sqlite3.Row = Depends(get_student),
    conn: sqlite3.Connection = Depends(db_dependency),
) -> dict:
    conn.execute("DELETE FROM chat_messages WHERE student_id = ?", (student["id"],))
    return {"cleared": True}


@router.get("/chat/greeting", summary="Souly's opening line")
def greeting(
    student: sqlite3.Row = Depends(get_student),
    conn: sqlite3.Connection = Depends(db_dependency),
) -> dict:
    return {"message": tutor.greeting(conn, student["id"])}



# =============================================================================
# The lesson hint layer
#
# These are the endpoints the merged flow runs on. They exist so that help
# never means leaving the lesson: the child stays on the step, and Souly
# answers about the thing still on screen.
# =============================================================================

@router.post("/pages/{page_id}/explain", summary="Re-explain this page")
def explain_step(
    page_id: int,
    payload: ExplainRequest,
    student: sqlite3.Row = Depends(get_student),
    conn: sqlite3.Connection = Depends(db_dependency),
) -> dict:
    """
    Backs the three buttons under Souly:
      simpler      -> "I don't get this"
      example      -> "Show me an example"
      another_way  -> "Say it another way"

    Fixed buttons rather than a blank text box on purpose. A blank box asks the
    child to formulate an unscripted question, which is exactly the barrier
    that stops autistic students seeking help in the first place.
    """
    try:
        reply = tutor.explain(
            conn, student["id"], page_id, payload.mode,
            initiated_by=payload.initiated_by,
            seconds_before=payload.seconds_on_page,
        )
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc

    body = {
        "text": reply.text,
        "mode": payload.mode,
        "engine": reply.engine,
        "cached": reply.cached,
        "adapted_for": reply.adapted_for,
        "latency_ms": reply.latency_ms,
        "warning": reply.error,
    }
    if payload.speak:
        body["speech"] = _speech_payload(reply.text)
    return body


@router.post("/questions/{question_id}/hint", summary="One rung of the hint ladder")
def question_hint(
    question_id: int,
    payload: HintRequest,
    student: sqlite3.Row = Depends(get_student),
    conn: sqlite3.Connection = Depends(db_dependency),
) -> dict:
    """
    nudge -> worked example -> step-through -> answer.

    Souly cannot skip to the answer. Bastani et al. (PNAS 2025): students given
    an AI that answered on request scored 17% BELOW students with no AI at all
    on a later unaided exam. The ladder is the guardrail that study identified.

    Help never costs stars and never breaks a streak. Penalising help is how
    you teach a struggling child to stop asking.
    """
    try:
        reply = tutor.hint(
            conn, student["id"], question_id, payload.tier,
            student_answer=payload.student_answer,
            quiz_id=payload.quiz_id,
            attempts_before=payload.attempts_before,
            seconds_before=payload.seconds_before,
            initiated_by=payload.initiated_by,
        )
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc

    body = {
        "text": reply.text,
        "tier": reply.tier,
        "tier_name": tutor.HINT_TIERS[reply.tier],
        "next_tier": reply.next_tier,
        "is_answer": reply.tier == 4,
        "engine": reply.engine,
        "latency_ms": reply.latency_ms,
        "warning": reply.error,
    }
    if payload.speak:
        body["speech"] = _speech_payload(reply.text)
    return body


@router.post("/stall-check", summary="Should Souly offer help right now?")
def stall_check(
    payload: StallCheck,
    student: sqlite3.Row = Depends(get_student),
    conn: sqlite3.Connection = Depends(db_dependency),
) -> dict:
    """
    Polled by the lesson screen. Triggers on lack of PROGRESS, never on gaze.

    Autistic children look away MORE as a task gets harder — it reduces
    cognitive load so they can think (Doherty-Sneddon et al. 2012). An
    attention-based trigger would interrupt them at the worst possible moment.
    Two wrong attempts, or idle past this child's own median step time, is a
    signal worth acting on. Looking out of the window is not.
    """
    return tutor.should_offer_help(
        conn, student["id"],
        seconds_on_step=payload.seconds_on_page,
        wrong_attempts=payload.wrong_attempts,
        already_offered=payload.already_offered,
    )


# =============================================================================
# Voice
# =============================================================================

@router.post("/voice/transcribe", summary="Audio in, text out")
async def transcribe(
    audio: UploadFile = File(...),
    language: str | None = Form(None),
    student: sqlite3.Row = Depends(get_student),
) -> dict:
    raw = await audio.read()
    if len(raw) > MAX_AUDIO_BYTES:
        raise HTTPException(status_code=413,
                            detail=f"Audio too large ({len(raw)} bytes)")

    result = stt.transcribe(
        raw,
        filename=audio.filename or "speech.webm",
        content_type=audio.content_type or "audio/webm",
        language=language,
    )
    return {
        "text": result.text,
        "confidence": result.confidence,
        "language": result.language,
        "latency_ms": result.latency_ms,
        "ok": result.ok,
        "error": result.error,
    }


@router.post("/voice/ask", summary="Full voice loop: speak in, hear back")
async def voice_ask(
    audio: UploadFile = File(...),
    topic_id: int | None = Form(None),
    session_id: int | None = Form(None),
    speak: bool = Form(True),
    student: sqlite3.Row = Depends(get_student),
    conn: sqlite3.Connection = Depends(db_dependency),
) -> dict:
    """
    Mic to answer in a single request: STT, retrieval, reasoning, speech.

    Returns partial results rather than failing outright when a stage breaks —
    if STT can't hear the child, the client shows "I didn't catch that" and
    offers the keyboard, which is the same path a student with a speech
    impairment uses by default.
    """
    raw = await audio.read()
    if len(raw) > MAX_AUDIO_BYTES:
        raise HTTPException(status_code=413,
                            detail=f"Audio too large ({len(raw)} bytes)")

    transcript = stt.transcribe(
        raw,
        filename=audio.filename or "speech.webm",
        content_type=audio.content_type or "audio/webm",
    )

    if not transcript.ok:
        message = "I didn't quite catch that. Can you say it again?"
        body = {
            "heard": "",
            "stt_ok": False,
            "stt_error": transcript.error,
            "reply": message,
            "engine": "none",
            "grounded": False,
        }
        if speak:
            body["speech"] = _speech_payload(message)
        return body

    reply = tutor.answer(
        conn, student["id"], transcript.text,
        input_mode="voice",
        stt_confidence=transcript.confidence,
        session_id=session_id,
        topic_id=topic_id,
    )

    body = {
        "heard": transcript.text,
        "stt_ok": True,
        "stt_confidence": transcript.confidence,
        "stt_latency_ms": transcript.latency_ms,
        "reply": reply.text,
        "engine": reply.engine,
        "grounded": reply.grounded,
        "latency_ms": reply.latency_ms,
        "source_refs": reply.source_refs,
        "suggested_mode": reply.suggested_mode,
        "award": reply.award,
        "warning": reply.error,
    }
    if speak:
        body["speech"] = _speech_payload(reply.text)
    return body


@router.post("/voice/speak", summary="Text to speech")
def speak(
    payload: SpeakRequest,
    student: sqlite3.Row = Depends(get_student),
) -> dict:
    """
    Used for reading lesson text and questions aloud — the "Read Text Aloud"
    accessibility setting.
    """
    return _speech_payload(payload.text, payload.voice_id)


@router.post("/voice/speak.mp3", summary="Text to speech as raw audio")
def speak_raw(
    payload: SpeakRequest,
    student: sqlite3.Row = Depends(get_student),
) -> Response:
    """Binary variant, for feeding an <audio src> directly."""
    speech = tts.synthesize(payload.text, voice_id=payload.voice_id)
    if speech.audio is None:
        raise HTTPException(
            status_code=503,
            detail=(speech.error or
                    "No server-side TTS provider configured; use browser speech"),
        )
    return Response(content=speech.audio, media_type=speech.mime_type)


# =============================================================================
# Diagnostics
# =============================================================================

diag_router = APIRouter(prefix="/api/diagnostics", tags=["system"])


@diag_router.get("/services", summary="Live check of Gemini, ElevenLabs and TTS")
def check_services() -> dict:
    """
    Actually calls each vendor. Slower than /health, which only reports
    whether keys are present — this tells you whether they *work*.

    Run it once at the venue before the demo.
    """
    return {
        "llm_gemini": llm.ping(),
        "stt_elevenlabs": stt.ping(),
        "tts": tts.ping(),
    }
