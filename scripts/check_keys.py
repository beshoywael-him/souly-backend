#!/usr/bin/env python3
"""
Verify the API keys in your .env actually work.

    python scripts/check_keys.py

Calls each vendor for real. Run it once after adding keys, and again at the
venue before the demo — "the key is set" and "the key works" are different
things, and the gap between them shows up at the worst moment.

No server needs to be running.
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.config import settings                # noqa: E402
from app.services import llm, stt, tts         # noqa: E402

GREEN, YELLOW, RED, BOLD, RESET = (
    "\033[32m", "\033[33m", "\033[31m", "\033[1m", "\033[0m"
)


def line(ok: bool, label: str, detail: str, warn: bool = False) -> None:
    mark = f"{GREEN}✓{RESET}" if ok else (f"{YELLOW}!{RESET}" if warn else f"{RED}✗{RESET}")
    print(f"  {mark} {label:<22} {detail}")


def main() -> int:
    print(f"\n{BOLD}Souly — API key check{RESET}")
    print(f"  .env: {Path(settings.schema_file).parent / '.env'}\n")

    failures = 0

    # --- Gemini --------------------------------------------------------------
    print(f"{BOLD}AI brain — Gemini{RESET}")
    if not settings.gemini_api_key:
        line(False, "GEMINI_API_KEY", "not set — Souly will use offline fallback", warn=True)
    else:
        result = llm.ping()
        if result["ok"]:
            line(True, settings.gemini_model,
                 f"replied \"{result['reply'].strip()}\" in {result['latency_ms']}ms")
        else:
            line(False, "Gemini", result["detail"])
            failures += 1

    # --- ElevenLabs STT ------------------------------------------------------
    print(f"\n{BOLD}Speech to text — ElevenLabs{RESET}")
    if not settings.elevenlabs_api_key:
        line(False, "ELEVENLABS_API_KEY", "not set — the microphone button won't work", warn=True)
    else:
        result = stt.ping()
        if result["ok"]:
            remaining = result.get("characters_remaining")
            detail = f"tier {result.get('tier')}, model {result.get('stt_model')}"
            if remaining is not None:
                detail += f", {remaining:,} characters left"
            line(True, "ElevenLabs", detail)
            if remaining is not None and remaining < 10000:
                print(f"    {YELLOW}Low character budget — top up before the competition.{RESET}")
        else:
            line(False, "ElevenLabs", result["detail"])
            failures += 1

    # --- TTS -----------------------------------------------------------------
    print(f"\n{BOLD}Text to speech{RESET}")
    provider = settings.tts_provider or "(not chosen)"
    if not tts.is_configured():
        line(True, provider, "using the browser's built-in voice — works, lower quality",
             warn=False)
        print(f"    {YELLOW}No TTS vendor chosen yet. See docs/STACK.md for the comparison.{RESET}")
    else:
        result = tts.ping()
        if result["ok"]:
            line(True, result["provider"],
                 ", ".join(f"{k}={v}" for k, v in result.items()
                           if k not in ("ok", "provider")))
        else:
            line(False, result["provider"], result["detail"])
            failures += 1

    # --- Verdict -------------------------------------------------------------
    print()
    if failures:
        print(f"{RED}{BOLD}{failures} vendor check(s) failed.{RESET} "
              "Fix the keys in .env before the demo.\n")
        return 1

    if not settings.gemini_api_key or not settings.elevenlabs_api_key:
        print(f"{YELLOW}{BOLD}Running in reduced mode.{RESET} The app works — Souly "
              "answers from the curriculum and\nthe student types instead of speaking "
              "— but the AI and voice features are off.\n")
        return 0

    print(f"{GREEN}{BOLD}All configured services are live.{RESET}\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
