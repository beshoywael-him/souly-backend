"""
Text to speech — swappable provider.

**The vendor is not chosen yet.** That decision costs nothing to defer because
every provider hides behind `TTSProvider`, and which one runs is read from
`TTS_PROVIDER` in `.env`. Picking a vendor later means filling in two
environment variables; no Python changes.

Providers implemented:
  elevenlabs  — same account as STT, best expressiveness
  google      — Google Cloud TTS, cheap and reliable
  browser     — returns no audio; the tablet speaks with the Web Speech API.
                Zero cost, zero latency, and the only option that survives
                the MiFi dropping mid-demo.
  (unset)     — same behaviour as `browser`, so the app is never mute.

See docs/STACK.md for the comparison table.
"""

from abc import ABC, abstractmethod
from dataclasses import dataclass

import httpx

from app.config import settings


@dataclass
class Speech:
    """
    Result of a synthesis request.

    `audio` is None for the browser provider — that is a success, not a
    failure. The client checks `use_browser_tts` and speaks locally.
    """

    audio: bytes | None
    mime_type: str = "audio/mpeg"
    provider: str = "browser"
    use_browser_tts: bool = False
    latency_ms: int = 0
    error: str | None = None

    @property
    def ok(self) -> bool:
        return self.error is None


# =============================================================================
# Provider interface
# =============================================================================

class TTSProvider(ABC):
    name: str = "base"

    @abstractmethod
    def synthesize(self, text: str, *, voice_id: str | None = None) -> Speech:
        ...

    def ping(self) -> dict:
        return {"ok": True, "provider": self.name, "detail": "No check implemented"}


class BrowserTTS(TTSProvider):
    """
    No server-side synthesis. The client speaks with the Web Speech API.

    This is the default until a vendor is chosen, and it is deliberately a
    real, working path rather than a stub: the voice loop is fully testable
    today, and this doubles as the offline fallback for every other provider.
    """

    name = "browser"

    def synthesize(self, text: str, *, voice_id: str | None = None) -> Speech:
        return Speech(audio=None, provider="browser", use_browser_tts=True)

    def ping(self) -> dict:
        return {
            "ok": True,
            "provider": "browser",
            "detail": "Client-side Web Speech API — no key needed",
        }


class ElevenLabsTTS(TTSProvider):
    name = "elevenlabs"
    BASE = "https://api.elevenlabs.io/v1/text-to-speech"
    # ElevenLabs' "Rachel" — warm and clear. Override with TTS_VOICE_ID.
    DEFAULT_VOICE = "21m00Tcm4TlvDq8ikWAM"

    def synthesize(self, text: str, *, voice_id: str | None = None) -> Speech:
        key = settings.tts_api_key or settings.elevenlabs_api_key
        if not key:
            return _degrade("TTS_API_KEY is empty in .env")

        voice = voice_id or settings.tts_voice_id or self.DEFAULT_VOICE
        model = settings.tts_model or "eleven_turbo_v2_5"

        try:
            with httpx.Client(timeout=30.0) as client:
                response = client.post(
                    f"{self.BASE}/{voice}",
                    headers={"xi-api-key": key, "Content-Type": "application/json"},
                    json={
                        "text": text,
                        "model_id": model,
                        "voice_settings": {
                            # Higher stability than default: a voice that varies
                            # its delivery between sentences is harder to follow
                            # for a child with an auditory processing difference.
                            "stability": 0.6,
                            "similarity_boost": 0.75,
                            "speed": 0.95,
                        },
                    },
                )
            latency_ms = int(response.elapsed.total_seconds() * 1000)

            if response.status_code != 200:
                return _degrade(
                    f"ElevenLabs HTTP {response.status_code}: {response.text[:200]}",
                    latency_ms,
                )
            return Speech(
                audio=response.content,
                mime_type="audio/mpeg",
                provider="elevenlabs",
                latency_ms=latency_ms,
            )
        except httpx.HTTPError as exc:
            return _degrade(f"Network error: {exc}")

    def ping(self) -> dict:
        key = settings.tts_api_key or settings.elevenlabs_api_key
        if not key:
            return {"ok": False, "provider": self.name, "detail": "No key set"}
        try:
            with httpx.Client(timeout=15.0) as client:
                r = client.get("https://api.elevenlabs.io/v1/voices",
                               headers={"xi-api-key": key})
            if r.status_code == 200:
                voices = r.json().get("voices", [])
                return {
                    "ok": True,
                    "provider": self.name,
                    "voices_available": len(voices),
                    "voice_id": settings.tts_voice_id or self.DEFAULT_VOICE,
                }
            return {"ok": False, "provider": self.name,
                    "detail": f"HTTP {r.status_code}: {r.text[:200]}"}
        except httpx.HTTPError as exc:
            return {"ok": False, "provider": self.name, "detail": str(exc)}


class GoogleTTS(TTSProvider):
    name = "google"
    URL = "https://texttospeech.googleapis.com/v1/text:synthesize"

    def synthesize(self, text: str, *, voice_id: str | None = None) -> Speech:
        import base64

        key = settings.tts_api_key
        if not key:
            return _degrade("TTS_API_KEY is empty in .env")

        voice = voice_id or settings.tts_voice_id or "en-US-Neural2-F"
        language_code = "-".join(voice.split("-")[:2]) or "en-US"

        try:
            with httpx.Client(timeout=30.0) as client:
                response = client.post(
                    self.URL,
                    params={"key": key},
                    json={
                        "input": {"text": text},
                        "voice": {"languageCode": language_code, "name": voice},
                        "audioConfig": {
                            "audioEncoding": "MP3",
                            "speakingRate": 0.95,
                        },
                    },
                )
            latency_ms = int(response.elapsed.total_seconds() * 1000)
            if response.status_code != 200:
                return _degrade(
                    f"Google TTS HTTP {response.status_code}: {response.text[:200]}",
                    latency_ms,
                )
            audio_b64 = response.json().get("audioContent")
            if not audio_b64:
                return _degrade("Google TTS returned no audio", latency_ms)
            return Speech(
                audio=base64.b64decode(audio_b64),
                provider="google",
                latency_ms=latency_ms,
            )
        except httpx.HTTPError as exc:
            return _degrade(f"Network error: {exc}")


def _degrade(reason: str, latency_ms: int = 0) -> Speech:
    """
    Any provider failure degrades to browser speech rather than silence.

    The student still hears Souly; only the voice quality drops. `error`
    carries the reason so /health and the logs can tell you what happened.
    """
    return Speech(
        audio=None,
        provider="browser",
        use_browser_tts=True,
        latency_ms=latency_ms,
        error=reason,
    )


# =============================================================================
# Registry
# =============================================================================

_PROVIDERS: dict[str, type[TTSProvider]] = {
    "": BrowserTTS,
    "browser": BrowserTTS,
    "elevenlabs": ElevenLabsTTS,
    "google": GoogleTTS,
}


def get_provider() -> TTSProvider:
    """Resolve TTS_PROVIDER from .env. Unknown values fall back to browser."""
    name = (settings.tts_provider or "").strip().lower()
    return _PROVIDERS.get(name, BrowserTTS)()


def synthesize(text: str, *, voice_id: str | None = None) -> Speech:
    if not text or not text.strip():
        return Speech(audio=None, provider="browser", use_browser_tts=True,
                      error="Nothing to say")
    return get_provider().synthesize(text.strip(), voice_id=voice_id)


def is_configured() -> bool:
    """True only for a real server-side vendor, not the browser fallback."""
    return bool(settings.tts_provider
                and settings.tts_provider.lower() not in ("", "browser")
                and settings.tts_api_key)


def ping() -> dict:
    return get_provider().ping()
