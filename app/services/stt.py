"""
Speech to text — ElevenLabs Scribe.

Confirmed vendor. (The roadmap PDF says local Whisper; that document is out
of date — see docs/STACK.md.)

The browser records audio with MediaRecorder and POSTs the blob to
`/voice/stt`; this module forwards it to ElevenLabs and returns the transcript.
Keeping the key server-side is the point — a key shipped to a tablet browser
is a key on the open internet.
"""

from dataclasses import dataclass

import httpx

from app.config import settings

ELEVENLABS_STT_URL = "https://api.elevenlabs.io/v1/speech-to-text"


@dataclass
class Transcript:
    text: str
    confidence: float | None = None
    language: str | None = None
    engine: str = "elevenlabs"
    latency_ms: int = 0
    error: str | None = None

    @property
    def ok(self) -> bool:
        return self.error is None and bool(self.text.strip())


def is_configured() -> bool:
    return bool(settings.elevenlabs_api_key)


def transcribe(
    audio_bytes: bytes,
    *,
    filename: str = "speech.webm",
    content_type: str = "audio/webm",
    language: str | None = None,
    timeout: float = 30.0,
) -> Transcript:
    """
    Send audio to ElevenLabs and return the transcript.

    Never raises. A failed transcription returns an empty Transcript with an
    error set, and the UI falls back to asking the student to type — which is
    also the accessibility fallback for a student with a speech impairment,
    so it is a path that must work regardless.
    """
    if not is_configured():
        return Transcript(text="", error="ELEVENLABS_API_KEY is empty in .env")

    if not audio_bytes:
        return Transcript(text="", error="No audio received")

    # ~1KB of webm is silence or a mis-fired recorder, not speech.
    if len(audio_bytes) < 1024:
        return Transcript(text="", error="Audio too short — nothing was recorded")

    data = {"model_id": settings.elevenlabs_stt_model}
    if language:
        data["language_code"] = language

    try:
        with httpx.Client(timeout=timeout) as client:
            response = client.post(
                ELEVENLABS_STT_URL,
                headers={"xi-api-key": settings.elevenlabs_api_key},
                files={"file": (filename, audio_bytes, content_type)},
                data=data,
            )
        latency_ms = int(response.elapsed.total_seconds() * 1000)

        if response.status_code != 200:
            return Transcript(
                text="",
                latency_ms=latency_ms,
                error=f"ElevenLabs HTTP {response.status_code}: {response.text[:200]}",
            )

        payload = response.json()
        text = (payload.get("text") or "").strip()

        # Scribe reports per-word probabilities; average them for one number
        # the UI can show and the attempts table can store.
        confidence = None
        words = payload.get("words") or []
        probs = [w["logprob"] for w in words if isinstance(w.get("logprob"), (int, float))]
        if probs:
            confidence = round(sum(probs) / len(probs), 3)
        elif isinstance(payload.get("language_probability"), (int, float)):
            confidence = round(payload["language_probability"], 3)

        if not text:
            return Transcript(
                text="", latency_ms=latency_ms,
                error="ElevenLabs heard no speech in the audio",
            )

        return Transcript(
            text=text,
            confidence=confidence,
            language=payload.get("language_code"),
            latency_ms=latency_ms,
        )

    except httpx.TimeoutException:
        return Transcript(text="", error=f"ElevenLabs STT timed out after {timeout}s")
    except httpx.HTTPError as exc:
        return Transcript(text="", error=f"Network error: {exc}")
    except (KeyError, ValueError) as exc:
        return Transcript(text="", error=f"Malformed ElevenLabs response: {exc}")


def ping() -> dict:
    """Check the key is valid by reading the account endpoint."""
    if not is_configured():
        return {"ok": False, "detail": "ELEVENLABS_API_KEY is empty in .env"}

    try:
        with httpx.Client(timeout=15.0) as client:
            response = client.get(
                "https://api.elevenlabs.io/v1/user/subscription",
                headers={"xi-api-key": settings.elevenlabs_api_key},
            )
        if response.status_code == 200:
            data = response.json()
            used = data.get("character_count")
            limit = data.get("character_limit")
            return {
                "ok": True,
                "tier": data.get("tier"),
                "characters_used": used,
                "character_limit": limit,
                "characters_remaining": (limit - used) if (used is not None and limit is not None) else None,
                "stt_model": settings.elevenlabs_stt_model,
            }
        return {"ok": False,
                "detail": f"HTTP {response.status_code}: {response.text[:200]}"}
    except httpx.HTTPError as exc:
        return {"ok": False, "detail": f"Network error: {exc}"}
