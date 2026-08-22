"""
Load the real curriculum: the books, the page map, and one topic per lesson.

    python scripts/ingest_curriculum.py                 # load + OCR anything missing
    python scripts/ingest_curriculum.py --dry-run       # show what would change
    python scripts/ingest_curriculum.py --no-ocr        # skip the text extraction
    python scripts/ingest_curriculum.py --reocr         # rebuild the text cache
    python scripts/ingest_curriculum.py --unverified    # load, but do not allow teaching yet

WHAT THIS WRITES
----------------
    curriculum_books   which PDF, what subject, which grade, its sha256
    curriculum_pages   lesson -> page. One row per page. No text, ever.
    topics             one row per lesson, re-pointed at the book

And on disk, beside the PDFs:

    data/curriculum/.cache/<book>/pNNNN.txt    the page's text, for grounding

THE MAPPING THAT MAKES THE REST WORK
------------------------------------
`topics` is the lesson. It already carries mastery, attempts, sessions,
games, activity_log and weekly_goals, so re-pointing it at the book — rather
than dropping it and rebuilding the whole progress spine — is what let
schema_v5 replace the invented curriculum without touching any of that.

    topics.book_id       which book
    topics.lesson_label  which lesson in it, matching curriculum_pages.lesson

Those two columns are the join. Keep them in step or the tutor cannot find
the pages it must read before explaining anything.

WHY THE TEXT IS ON DISK AND NOT IN THE DATABASE
-----------------------------------------------
The book is the source of truth and it stays a PDF. `curriculum_pages` holds
no text on purpose: a copy in SQLite would be a second version that silently
drifts from the book. The `.cache/` text is derived, regenerable, and keyed
to the book's sha256 — swap the PDF and it is detectable rather than quietly
wrong.

IDEMPOTENT
----------
Run it as often as you like. Pages are upserted on (book_id, page), pages
that have left the map are deleted, and topics are upserted on their code —
so a child's progress survives a re-ingest.
"""

from __future__ import annotations

import argparse
import json
import re
import sqlite3
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from app.config import settings          # noqa: E402
from app.db import connect, init_db      # noqa: E402
from app.services import curriculum      # noqa: E402

MAP_FILE = "curriculum_map.json"


# =============================================================================
# Reading the map
# =============================================================================

def load_map(path: Path) -> dict:
    if not path.exists():
        raise SystemExit(
            f"No lesson map at {path}.\n\n"
            f"That file is the lesson-to-page mapping and it cannot be guessed:\n"
            f"it has to be read off the books and approved by a human before\n"
            f"anything is taught from it."
        )
    data = json.loads(path.read_text(encoding="utf-8"))
    books = data.get("books") or []
    if not books:
        raise SystemExit(f"{path} contains no books.")
    return data


def validate_book(book: dict, pdf_pages: int | None) -> list[str]:
    """
    Catch the mistakes that would otherwise show up as a blank lesson screen.

    A page mapped twice, or mapped past the end of the file, is the kind of
    error that survives all the way to a child staring at nothing.
    """
    problems: list[str] = []
    seen: dict[int, str] = {}

    for lesson in book.get("lessons", []):
        label = lesson.get("lesson", "?")
        pages = lesson.get("pages") or []
        if not pages:
            problems.append(f"{label}: no pages")
        for page in pages:
            if not isinstance(page, int) or page < 1:
                problems.append(f"{label}: page {page!r} is not a page number")
                continue
            if pdf_pages and page > pdf_pages:
                problems.append(
                    f"{label}: page {page} is past the end of the file "
                    f"({pdf_pages} pages)"
                )
            if page in seen:
                problems.append(
                    f"page {page} is in both '{seen[page]}' and '{label}'"
                )
            else:
                seen[page] = label
    return problems


def topic_code(book: dict, index: int) -> str:
    """
    Stable identifier for a lesson, derived from the book and its position.

    Stable matters: it is the key the upsert uses, so a child's mastery of
    lesson 3 stays attached to lesson 3 across a re-ingest.
    """
    subject = (book.get("subject_code") or book.get("subject") or "GEN").upper()
    slug = re.sub(r"[^A-Z0-9]+", "-", book["code"].upper()).strip("-")
    return f"{subject}.{slug}.L{index:02d}"


# =============================================================================
# Writing
# =============================================================================

def ensure_subject(conn: sqlite3.Connection, book: dict) -> int | None:
    """Find the subject card this book belongs to, creating it if it is new."""
    code = (book.get("subject_code") or "").upper()
    if not code:
        return None

    row = conn.execute("SELECT id FROM subjects WHERE code = ?", (code,)).fetchone()
    if row:
        return row["id"]

    conn.execute(
        "INSERT INTO subjects (code, name, sort_order) VALUES (?,?,?)",
        (code, book.get("subject") or code, 99),
    )
    return conn.execute(
        "SELECT id FROM subjects WHERE code = ?", (code,)
    ).fetchone()["id"]


def upsert_book(conn: sqlite3.Connection, book: dict, *,
                page_count: int | None, sha: str | None,
                verified: bool) -> int:
    conn.execute(
        """
        INSERT INTO curriculum_books
            (code, title, subject, subject_code, grade, term, language,
             filename, page_count, sha256, is_verified, source_note, updated_at)
        VALUES (?,?,?,?,?,?,?,?,?,?,?,?, strftime('%Y-%m-%dT%H:%M:%SZ','now'))
        ON CONFLICT(code) DO UPDATE SET
            title        = excluded.title,
            subject      = excluded.subject,
            subject_code = excluded.subject_code,
            grade        = excluded.grade,
            term         = excluded.term,
            language     = excluded.language,
            filename     = excluded.filename,
            page_count   = excluded.page_count,
            sha256       = excluded.sha256,
            is_verified  = excluded.is_verified,
            source_note  = excluded.source_note,
            updated_at   = strftime('%Y-%m-%dT%H:%M:%SZ','now')
        """,
        (book["code"], book["title"], book.get("subject") or "",
         (book.get("subject_code") or "").upper() or None,
         str(book["grade"]), book.get("term"), book.get("language") or "en",
         book["filename"], page_count, sha, int(verified),
         book.get("source_note")),
    )
    return conn.execute(
        "SELECT id FROM curriculum_books WHERE code = ?", (book["code"],)
    ).fetchone()["id"]


def upsert_pages(conn: sqlite3.Connection, book_id: int, book: dict) -> tuple[int, int]:
    """Write the page map. Returns (written, removed)."""
    wanted: set[int] = set()
    written = 0

    for order, lesson in enumerate(book.get("lessons", [])):
        for page in lesson["pages"]:
            wanted.add(page)
            conn.execute(
                """
                INSERT INTO curriculum_pages (book_id, lesson, page, lesson_order, unit)
                VALUES (?,?,?,?,?)
                ON CONFLICT(book_id, page) DO UPDATE SET
                    lesson       = excluded.lesson,
                    lesson_order = excluded.lesson_order,
                    unit         = excluded.unit
                """,
                (book_id, lesson["lesson"], page, order, lesson.get("unit")),
            )
            written += 1

    existing = {
        r["page"] for r in conn.execute(
            "SELECT page FROM curriculum_pages WHERE book_id = ?", (book_id,)
        )
    }
    stale = existing - wanted
    for page in stale:
        conn.execute(
            "DELETE FROM curriculum_pages WHERE book_id = ? AND page = ?",
            (book_id, page),
        )
    return written, len(stale)


def upsert_topics(conn: sqlite3.Connection, book_id: int, book: dict, *,
                  subject_id: int | None, verified: bool) -> int:
    """
    One topic per lesson.

    `is_verified` is the gate the tutor checks before generating anything, and
    it is set here because this material IS the Ministry's — a human has read
    the book and approved the map. `--unverified` loads without opening it.
    """
    count = 0
    for index, lesson in enumerate(book.get("lessons", []), start=1):
        code = topic_code(book, index)
        conn.execute(
            """
            INSERT INTO topics
                (code, subject, subject_id, title, grade, sort_order,
                 is_verified, book_id, lesson_label)
            VALUES (?,?,?,?,?,?,?,?,?)
            ON CONFLICT(code) DO UPDATE SET
                subject      = excluded.subject,
                subject_id   = excluded.subject_id,
                title        = excluded.title,
                grade        = excluded.grade,
                sort_order   = excluded.sort_order,
                is_verified  = excluded.is_verified,
                book_id      = excluded.book_id,
                lesson_label = excluded.lesson_label
            """,
            (code, book.get("subject") or "", subject_id, lesson["lesson"],
             str(book["grade"]), index, int(verified), book_id,
             lesson["lesson"]),
        )
        count += 1
    return count


# =============================================================================
# Text extraction
# =============================================================================

def extract_text(book: dict, pages: list[int], *, reocr: bool,
                 quiet: bool = False) -> tuple[int, int, int]:
    """
    Fill the text cache for every mapped page.

    Four sources, cheapest first:

      1. the loose pNNNN.txt file, if it is already there
      2. text.json, the bundle — the cache moved between machines as one file
      3. the PDF's own embedded text — instant, and nothing for a scan
      4. OCR — slow, and needs tesseract, which is not on a Windows laptop

    Step 2 is the one that was missing, and its absence was alarming: a
    machine that had the bundle but no loose files and no tesseract reported
    "no text — the tutor will refuse to teach from it" for every page in the
    book, while the app itself was reading the bundle quite happily and
    teaching fine. A hundred lines of false alarm.

    Anything found in the bundle is also written out as a loose file, so the
    two forms converge and a human can open p0023.txt and fix an OCR mistake.

    Returns (from_pdf, from_ocr, empty).
    """
    code = book["code"]
    pdf = settings.curriculum_dir / book["filename"]
    out_dir = curriculum.cache_dir(code)
    out_dir.mkdir(parents=True, exist_ok=True)

    from_pdf = from_ocr = from_bundle = empty = 0
    missing: list[int] = []

    for page in pages:
        dest = curriculum.text_path(code, page)
        if dest.exists() and dest.stat().st_size > 0 and not reocr:
            continue

        # The bundle. `page_text` consults it, so this is exactly what the
        # running app would find.
        if not reocr:
            bundled = curriculum.page_text(code, page)
            if bundled:
                dest.write_text(bundled, encoding="utf-8")
                from_bundle += 1
                continue

        text = ""
        if pdf.exists():
            text = curriculum.extract_embedded_text(pdf, page)
            if text:
                from_pdf += 1

        if not text:
            text = curriculum.ocr_page(pdf if pdf.exists() else None, page,
                                       book_code=code)
            if text:
                from_ocr += 1

        if not text:
            empty += 1
            missing.append(page)
            continue

        dest.write_text(text, encoding="utf-8")

    # One line, not one per page. A book with no text at all is a single
    # problem with a single cause, and printing it sixty times buries the
    # cause under the symptom.
    if missing and not quiet:
        shown = ", ".join(str(p) for p in missing[:12])
        more = f" and {len(missing) - 12} more" if len(missing) > 12 else ""
        print(f"    no text for {len(missing)} page(s): {shown}{more}")
        if not curriculum.have_tool("tesseract"):
            print("    tesseract is not installed, so pages cannot be OCR'd "
                  "here. Either install it, or copy the .cache/<book>/text.json "
                  "from a machine that has it.")

    return from_pdf, from_ocr, empty


# =============================================================================
# Main
# =============================================================================

def main() -> int:
    ap = argparse.ArgumentParser(
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    ap.add_argument("--map", default=None,
                    help=f"path to the lesson map (default: "
                         f"data/curriculum/{MAP_FILE})")
    ap.add_argument("--dry-run", action="store_true",
                    help="validate and report, write nothing")
    ap.add_argument("--no-ocr", action="store_true",
                    help="skip text extraction entirely")
    ap.add_argument("--reocr", action="store_true",
                    help="rebuild the text cache even where it exists")
    ap.add_argument("--prune-orphan-topics", action="store_true",
                    help="delete topics that belong to no book — the invented "
                         "curriculum from before schema_v5. This also deletes "
                         "the mastery and attempts attached to them.")
    ap.add_argument("--bundle-only", action="store_true",
                    help="just rebuild text.json from the loose page files")
    ap.add_argument("--unverified", action="store_true",
                    help="load the books but leave is_verified = 0, so nothing "
                         "is taught from them yet")
    args = ap.parse_args()

    map_path = Path(args.map) if args.map else settings.curriculum_dir / MAP_FILE
    if not map_path.is_absolute():
        map_path = PROJECT_ROOT / map_path

    data = load_map(map_path)
    verified = not args.unverified

    if args.bundle_only:
        for book in data["books"]:
            n = curriculum.write_bundle(book["code"])
            print(f"{book['code']}: bundled {n} pages")
        return 0

    print(f"Map        {map_path}")
    print(f"Curriculum {settings.curriculum_dir}")
    print(f"Database   {settings.db_file}")
    print(f"Verified   {'yes — these lessons can be taught' if verified else 'NO — loaded but not teachable'}")

    # Make sure the schema is there before writing into it.
    if not args.dry_run:
        init_db()

    conn = connect()
    problems_total = 0

    try:
        for book in data["books"]:
            pdf = settings.curriculum_dir / book["filename"]
            manifest = curriculum.cache_dir(book["code"]) / "manifest.json"

            page_count = None
            sha = None
            if pdf.exists():
                page_count = curriculum.pdf_page_count(pdf) or None
                sha = curriculum.file_sha256(pdf)
                if page_count is None and manifest.exists():
                    # The file is there and readable — the sha proves it — but
                    # nothing here could open it as a PDF. The renderer already
                    # counted its pages once; trust that rather than losing the
                    # bounds check on the lesson map.
                    try:
                        page_count = json.loads(
                            manifest.read_text(encoding="utf-8")
                        ).get("page_count")
                    except (OSError, json.JSONDecodeError):
                        page_count = None
            elif manifest.exists():
                # The PDF is not here but its images are. That is a legitimate
                # state — the renderer ran on another machine — so trust the
                # manifest it left behind rather than refusing to load.
                info = json.loads(manifest.read_text(encoding="utf-8"))
                page_count = info.get("page_count")
                sha = info.get("sha256")

            print(f"\n{book['code']}  ({book['filename']})")
            if pdf.exists():
                print(f"  pdf        {page_count} pages, sha {(sha or '')[:12]}")
            elif sha:
                print(f"  pdf        NOT PRESENT — using the render manifest "
                      f"({page_count} pages, sha {sha[:12]})")
            else:
                print("  pdf        NOT PRESENT and no manifest — pages cannot "
                      "be verified against the file")

            problems = validate_book(book, page_count)
            if problems:
                problems_total += len(problems)
                print(f"  PROBLEMS   {len(problems)}")
                for p in problems:
                    print(f"    - {p}")
                continue

            pages = sorted(
                {p for lesson in book["lessons"] for p in lesson["pages"]}
            )
            print(f"  lessons    {len(book['lessons'])}")
            print(f"  pages      {len(pages)} mapped, "
                  f"{len(book.get('skipped_pages') or {})} deliberately skipped")

            if args.dry_run:
                for index, lesson in enumerate(book["lessons"], start=1):
                    lp = lesson["pages"]
                    print(f"    {index:>2}. {lesson['lesson'][:64]:<64} "
                          f"p{min(lp)}-{max(lp)} ({len(lp)})")
                continue

            subject_id = ensure_subject(conn, book)
            book_id = upsert_book(conn, book, page_count=page_count, sha=sha,
                                  verified=verified)
            written, removed = upsert_pages(conn, book_id, book)
            topics = upsert_topics(conn, book_id, book,
                                   subject_id=subject_id, verified=verified)
            conn.commit()

            print(f"  written    {written} page rows"
                  + (f", {removed} removed" if removed else ""))
            print(f"  topics     {topics} lessons")

            if not args.no_ocr:
                print("  text       extracting…", flush=True)
                from_pdf, from_ocr, empty = extract_text(book, pages,
                                                         reocr=args.reocr)
                cached = sum(
                    1 for p in pages
                    if curriculum.page_text(book["code"], p)
                )
                print(f"  text       {cached}/{len(pages)} pages have text "
                      f"({from_pdf} from the PDF, {from_ocr} OCR'd, "
                      f"{empty} came back empty)")

                # One file holding every page's text, so the cache can be
                # moved between machines without carrying a hundred loose
                # files. The loose files stay authoritative — a page someone
                # hand-corrected must not be overwritten by a stale bundle.
                bundled = curriculum.write_bundle(book["code"])
                if bundled:
                    print(f"  bundle     {bundled} pages -> "
                          f"{curriculum.bundle_path(book['code']).name}")

        if args.dry_run:
            print("\nDry run — nothing written.")
            return 1 if problems_total else 0

        if problems_total:
            print(f"\n{problems_total} problem(s) — those books were skipped.")
            return 1

        # What the interface will actually read.
        print("\nThe plan of lessons ahead:")
        for row in conn.execute(
            "SELECT subject, grade, lesson, first_page, last_page, page_count "
            "FROM v_curriculum_lessons"
        ):
            print(f"  [{row['subject'][:4]:<4} g{row['grade']}] "
                  f"{row['lesson'][:62]:<62} "
                  f"p{row['first_page']}-{row['last_page']} "
                  f"({row['page_count']})")

        # Topics left over from the invented curriculum. They belong to no
        # book, so they never appear in the plan — but they still count in
        # `v_subject_progress`, and they still carry a child's mastery rows.
        # Reported rather than deleted: throwing away a child's progress is
        # not a decision a migration script should make on its own.
        orphans = conn.execute(
            """
            SELECT t.id, t.code, t.title,
                   (SELECT COUNT(*) FROM mastery m WHERE m.topic_id = t.id) AS mastery,
                   (SELECT COUNT(*) FROM attempts a WHERE a.topic_id = t.id) AS attempts
            FROM topics t WHERE t.book_id IS NULL
            ORDER BY t.id
            """
        ).fetchall()

        if orphans:
            with_progress = sum(1 for o in orphans if o["mastery"] or o["attempts"])
            if args.prune_orphan_topics:
                for o in orphans:
                    conn.execute("DELETE FROM topics WHERE id = ?", (o["id"],))
                conn.commit()
                print(f"\nPruned {len(orphans)} topic(s) that belonged to no book"
                      + (f", including {with_progress} carrying progress."
                         if with_progress else "."))
            else:
                print(f"\n{len(orphans)} topic(s) belong to no book — left over "
                      f"from the curriculum that was in the database before:")
                for o in orphans[:10]:
                    tail = (f"  ({o['mastery']} mastery, {o['attempts']} attempts)"
                            if (o["mastery"] or o["attempts"]) else "")
                    print(f"    {o['code']}: {o['title'][:50]}{tail}")
                if len(orphans) > 10:
                    print(f"    ... and {len(orphans) - 10} more")
                print("  They never appear in the plan of lessons. To delete "
                      "them and the progress attached to them:")
                print("      python scripts/ingest_curriculum.py --prune-orphan-topics")

        cov = curriculum.coverage(conn)
        print(f"\n{cov['books']} books, {cov['lessons']} lessons, "
              f"{cov['pages']} pages, {cov['pages_with_text']} with text.")
        if not cov["ready"]:
            print("NOT READY: no verified book with extracted text. The tutor "
                  "will refuse to teach until there is one.")
        return 0

    finally:
        conn.close()


if __name__ == "__main__":
    raise SystemExit(main())
