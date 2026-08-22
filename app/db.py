"""
SQLite access layer.

Plain `sqlite3` on purpose — no ORM. The schema is small, the queries are
readable SQL, and every team member can follow what happens without learning
SQLAlchemy on top of everything else they're learning this month.

Two things worth knowing:

  * Foreign keys are OFF by default in SQLite. We turn them on per-connection.
    Without this, `flags.student_id` accepts any integer at all.
  * WAL mode lets the CV rig write flags while the teacher dashboard reads
    them, without one blocking the other.
"""

import re
import sqlite3
from contextlib import contextmanager
from typing import Any, Iterator

from app.config import settings


def connect() -> sqlite3.Connection:
    """Open a configured connection. Caller is responsible for closing it."""
    conn = sqlite3.connect(
        settings.db_file,
        # FastAPI may hand the connection to a different worker thread.
        check_same_thread=False,
        # Wait rather than immediately raising "database is locked" if the CV
        # rig happens to be mid-write.
        timeout=10.0,
    )
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    conn.execute("PRAGMA journal_mode = WAL")
    return conn


@contextmanager
def get_conn() -> Iterator[sqlite3.Connection]:
    """
    Transactional connection. Commits on success, rolls back on exception.

        with get_conn() as conn:
            conn.execute(...)
    """
    conn = connect()
    try:
        yield conn
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def db_dependency() -> Iterator[sqlite3.Connection]:
    """FastAPI dependency. Use with `Depends(db_dependency)`."""
    with get_conn() as conn:
        yield conn


def init_db(drop_existing: bool = False) -> None:
    """
    Create the database from schema.sql, then apply schema_v2.sql.

    Both files are idempotent, so running this against an existing database is
    safe. The one exception is `ALTER TABLE ... ADD COLUMN`, which SQLite has
    no IF NOT EXISTS form for — those are executed separately and their
    "duplicate column name" error is swallowed, since it just means the
    migration already ran.
    """
    if drop_existing and settings.db_file.exists():
        settings.db_file.unlink()
        for suffix in ("-wal", "-shm"):
            sidecar = settings.db_file.with_name(settings.db_file.name + suffix)
            if sidecar.exists():
                sidecar.unlink()

    conn = connect()
    try:
        # Every schema*.sql in the project root, in filename order:
        # schema.sql, schema_v2.sql, schema_v3.sql, ...
        # Globbing rather than listing means adding a migration is one new
        # file and no code change — which is the point, since we're going to
        # add several as the design settles.
        schema_files = sorted(
            settings.schema_file.parent.glob("schema*.sql"),
            key=lambda p: (len(p.stem), p.stem),
        )

        for path in schema_files:
            if not path.exists():
                continue
            sql = path.read_text(encoding="utf-8")

            # Pull ADD COLUMN statements out of the script so one
            # already-applied migration doesn't abort everything after it.
            #
            # Only ADD COLUMN — deliberately not every ALTER. A
            # `RENAME TO` in a table-rebuild sequence must stay in place, or
            # it gets hoisted above the CREATE that makes its table exist.
            alters = re.findall(
                r"^ALTER TABLE\s+\S+\s+ADD COLUMN\b.*?;", sql,
                flags=re.MULTILINE | re.IGNORECASE,
            )
            for alter in alters:
                sql = sql.replace(alter, "")

            # Apply the column additions BEFORE the rest of the script. Views
            # later in the file select the new columns, and SQLite validates
            # column references at CREATE VIEW time — so the order matters.
            for alter in alters:
                try:
                    conn.execute(alter)
                except sqlite3.OperationalError as exc:
                    if "duplicate column name" not in str(exc).lower():
                        raise

            conn.executescript(sql)

        conn.commit()
    finally:
        conn.close()


def row_to_dict(row: sqlite3.Row | None) -> dict[str, Any] | None:
    return dict(row) if row is not None else None


def rows_to_dicts(rows: list[sqlite3.Row]) -> list[dict[str, Any]]:
    return [dict(r) for r in rows]
