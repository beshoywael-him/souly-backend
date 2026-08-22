"""
Health and readiness.

Doubles as the "is everything wired?" check you run at the venue before the
demo, which is why it reports integration readiness and curriculum coverage
rather than just "ok".

Note the split: /health reports whether keys are *present* and is instant.
/api/diagnostics/services actually *calls* each vendor and is slow. Both
matter — "the key is set" and "the key works" are different failures.
"""

import sqlite3

from fastapi import APIRouter, Depends

from app.config import settings
from app.db import db_dependency
from app.models import HealthOut
from app.services import llm, rag, stt, tts

router = APIRouter(tags=["system"])


@router.get("/health", response_model=HealthOut, summary="System health")
def health(conn: sqlite3.Connection = Depends(db_dependency)) -> HealthOut:
    students = conn.execute(
        "SELECT COUNT(*) AS n FROM students WHERE is_active = 1"
    ).fetchone()["n"]
    pending = conn.execute(
        "SELECT COUNT(*) AS n FROM flags WHERE status = 'pending'"
    ).fetchone()["n"]

    return HealthOut(
        status="ok",
        env=settings.souly_env,
        database=str(settings.db_file),
        student_count=students,
        pending_flags=pending,
        integrations={
            "llm_gemini": llm.is_configured(),
            "stt_elevenlabs": stt.is_configured(),
            # False here means the browser fallback is in use — a working
            # state, not a broken one.
            "tts": tts.is_configured(),
        },
        tts_provider=settings.tts_provider or "browser",
        curriculum=rag.coverage(conn),
    )
