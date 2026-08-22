"""
Test fixtures.

IMPORTANT — read before editing the top of this file.

The environment variables below are set at conftest IMPORT time, not inside a
fixture. That ordering is load-bearing. `app/config.py` builds a module-level
`settings` object the moment it is first imported, and `app/db.py` binds to
that object. Test modules import `app.db` at their own module level, which
happens during pytest collection — before any fixture body runs.

So if we set SOULY_DB_PATH inside a fixture, the config object already exists,
still points at ./data/souly.db, and the suite runs `init_db(drop_existing=True)`
against the real development database and deletes everyone's seed data.

Setting it here, before the first `import app.*` anywhere, is what prevents
that. There is also a belt-and-braces assertion in the fixture below.
"""

import os
import sys
import tempfile
from pathlib import Path

import pytest

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

# --- Must happen before any `import app.*` -----------------------------------
_TMPDIR = tempfile.mkdtemp(prefix="souly-test-")
_TEST_DB = Path(_TMPDIR) / "test.db"
_TEST_CURRICULUM = Path(_TMPDIR) / "curriculum"
_TEST_CURRICULUM.mkdir(parents=True, exist_ok=True)

os.environ["SOULY_DB_PATH"] = str(_TEST_DB)
# Same reasoning as the database, one layer out. The curriculum directory
# holds the real Ministry PDFs and the page cache derived from them; a test
# that wrote into it would be editing the actual books.
os.environ["SOULY_CURRICULUM_DIR"] = str(_TEST_CURRICULUM)
os.environ["SOULY_ENV"] = "test"
os.environ["FLAG_MIN_CONFIDENCE"] = "0.5"
# The real .env must not leak into tests — a developer with a Gemini key set
# would otherwise see different results from CI.
os.environ["GEMINI_API_KEY"] = ""
os.environ["ELEVENLABS_API_KEY"] = ""
os.environ["TTS_PROVIDER"] = ""
os.environ["TTS_API_KEY"] = ""
# -----------------------------------------------------------------------------


@pytest.fixture(scope="session", autouse=True)
def _guard_against_clobbering_the_dev_database():
    """
    Hard stop if the config somehow resolved to a non-temp database.

    Without this, a future refactor that moves an import earlier silently
    turns `pytest` into `rm -rf your seed data`.
    """
    from app.config import settings

    resolved = str(settings.db_file)
    assert resolved == str(_TEST_DB), (
        f"Tests are pointed at {resolved!r}, not the temporary database "
        f"{str(_TEST_DB)!r}. Refusing to run — this would destroy real data. "
        "Something imported app.config before conftest set SOULY_DB_PATH."
    )
    assert str(settings.curriculum_dir) == str(_TEST_CURRICULUM), (
        f"Tests are pointed at curriculum {settings.curriculum_dir!r}, not "
        f"{str(_TEST_CURRICULUM)!r}. Refusing to run — this would write into "
        "the real books' cache."
    )
    yield


@pytest.fixture(scope="session")
def curriculum_dir(_guard_against_clobbering_the_dev_database) -> Path:
    """The temporary stand-in for data/curriculum."""
    return _TEST_CURRICULUM


@pytest.fixture(scope="session")
def client(_guard_against_clobbering_the_dev_database):
    """FastAPI TestClient backed by an isolated temporary database."""
    from fastapi.testclient import TestClient

    from app.db import get_conn, init_db
    from app.main import app
    from app.models import utc_now_iso

    init_db(drop_existing=True)

    with get_conn() as conn:
        conn.execute(
            """
            INSERT INTO students (external_id, full_name, display_name, grade,
                                  support_profile, drift_threshold_ms,
                                  created_at, updated_at)
            VALUES ('stu-test', 'Test Student', 'Testy', '5', 'autism', 8000, ?, ?)
            """,
            (utc_now_iso(), utc_now_iso()),
        )
        conn.execute(
            """
            INSERT INTO students (external_id, full_name, display_name,
                                  is_active, created_at, updated_at)
            VALUES ('stu-inactive', 'Gone Student', 'Gone', 0, ?, ?)
            """,
            (utc_now_iso(), utc_now_iso()),
        )
        conn.execute(
            "INSERT INTO teachers (full_name, email, password_hash, created_at) "
            "VALUES ('T', 't@souly.local', 'x', ?)",
            (utc_now_iso(),),
        )

    with TestClient(app) as c:
        yield c
