"""
Souly backend — application entry point.

    ./run.sh          or      uvicorn app.main:app --reload

Three things live here:
  /                 -> redirects to the student app
  /student          -> the student UI (the thing you actually look at)
  /docs             -> live API contract for the Interfaces squad
"""

from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles

from app.config import settings
from app.db import init_db
from app.routers import (
    auth,
    flags,
    gamification,
    health,
    learning,
    onboarding,
    parent,
    parent_auth,
    progress,
    student,
    tutor_api,
)


@asynccontextmanager
async def lifespan(app: FastAPI):
    # Both schema files are idempotent, so this is safe on every boot and
    # means no teammate ever hits "no such table" for forgetting a step.
    init_db()
    yield


app = FastAPI(
    title="Souly API",
    description=(
        "Backend for Souly — an AI study buddy for students with disabilities.\n\n"
        "**The student app is at [/student](/student).**\n\n"
        "Three real-world endpoints: the student's robot at home, the classroom "
        "sensors that flag when focus drifts, and the parents' portal."
    ),
    version="0.2.0",
    lifespan=lifespan,
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.cors_origin_list,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# --- API ---------------------------------------------------------------------
app.include_router(health.router)
app.include_router(auth.router)
app.include_router(onboarding.router)
app.include_router(flags.router)
app.include_router(student.router)
app.include_router(learning.router)
app.include_router(gamification.router)
app.include_router(tutor_api.router)
app.include_router(tutor_api.diag_router)
app.include_router(progress.router)

# The parents' hub. Its own token realm and its own router file — it shares
# the database with the student app and nothing else.
app.include_router(parent_auth.router)
app.include_router(parent.router)

# --- Static UI ---------------------------------------------------------------
if settings.static_dir.exists():
    app.mount("/static", StaticFiles(directory=settings.static_dir), name="static")


@app.get("/student", include_in_schema=False)
def student_app():
    """The student UI. This is the screen mounted in the robot's shell."""
    index = settings.static_dir / "student" / "index.html"
    if not index.exists():
        return RedirectResponse("/docs")
    return FileResponse(index)


@app.get("/parent", include_in_schema=False)
def parent_app():
    """The parents' hub. Same server, same database, separate everything else."""
    index = settings.static_dir / "parent" / "index.html"
    if not index.exists():
        return RedirectResponse("/docs")
    return FileResponse(index)


@app.get("/", include_in_schema=False)
def root():
    return RedirectResponse("/student")


@app.get("/api", tags=["system"])
def api_root() -> dict[str, str]:
    return {
        "service": "Souly API",
        "version": "0.2.0",
        "phase": "2 — student app fully wired",
        "student_app": "/student",
        "parent_hub": "/parent",
        "docs": "/docs",
    }
