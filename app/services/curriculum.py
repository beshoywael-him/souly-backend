"""
Reading the actual book.

This is the layer between `curriculum_pages` — which knows only *which lesson
is on which page* — and the PDF on disk, which is the only place the content
lives.

The split is deliberate and it is the answer to the first question a judge
asks: *how do you know it is teaching the right thing?* The content is the
Ministry's, cited by page. Nothing in this codebase paraphrases the book into
SQLite, so there is no second version to drift.

Three layers, and this module serves the first one:

    canon      the PDF page.        Nobody writes it. It stays on disk.
    rendition  the explanation.     The model writes it, per child, from here.
    path       order and pacing.    A deterministic policy over mastery.

WHY THERE IS A CACHE
--------------------
The books are scanned page images — `pdftotext` returns nothing for them — so
getting text out means OCR, and OCR takes seconds per page. Doing that while a
child waits is not an option, so `scripts/ingest_curriculum.py` OCRs every
mapped page once and writes the result beside the PDF under `.cache/`.

The cache is derived, never authoritative. Delete the whole directory and
re-running the ingest rebuilds it from the books. The book's sha256 is stored
on `curriculum_books` so a swapped PDF is detectable rather than silently
teaching from a page that moved.
"""

from __future__ import annotations

import hashlib
import json
import shutil
import sqlite3
import subprocess
from functools import lru_cache
from pathlib import Path

from app.config import settings

# Rendered at this width. Big enough to read a maths diagram on a tablet,
# small enough that a page is well under a megabyte.
PAGE_IMAGE_WIDTH = 1200


# =============================================================================
# Where things are
# =============================================================================

def book_file(book: sqlite3.Row | dict) -> Path:
    """The PDF itself. `filename` is relative to the curriculum directory."""
    return settings.curriculum_dir / book["filename"]


def cache_dir(book_code: str) -> Path:
    return settings.curriculum_cache_dir / book_code


def text_path(book_code: str, page: int) -> Path:
    return cache_dir(book_code) / f"p{page:04d}.txt"


def image_path(book_code: str, page: int) -> Path | None:
    """
    The rendered page image, or None if it was never rendered.

    JPEG first: `scripts/render_pages.py` writes JPEGs because a scanned page
    at 1000px is around 120KB as JPEG and close to a megabyte as PNG, and the
    source is a scan — there is no extra detail for PNG to preserve. PNG is
    still accepted so an older cache keeps working.
    """
    folder = cache_dir(book_code)
    for suffix in (".jpg", ".jpeg", ".png"):
        candidate = folder / f"p{page:04d}{suffix}"
        if candidate.exists():
            return candidate
    return None


def illustration_dir() -> Path:
    """
    Generated illustrations, shared across children.

    Keyed on the scene rather than on the child: two students who are both
    shown "a bean seed sprouting in soil" get the same picture, and it is
    generated once for the whole class rather than once per child per page.
    The lesson TEXT is per child; the photograph of a bean is not.
    """
    return settings.curriculum_cache_dir / "illustrations"


def illustration_path(key: str) -> Path:
    return illustration_dir() / f"{key}.png"


def file_sha256(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as fh:
        for block in iter(lambda: fh.read(1 << 20), b""):
            h.update(block)
    return h.hexdigest()


# =============================================================================
# Extraction — used by the ingest script, not at study time
# =============================================================================

def have_tool(name: str) -> bool:
    return shutil.which(name) is not None


def pdf_page_count(pdf: Path) -> int:
    """Page count via pdfinfo, falling back to pypdfium2, then 0."""
    if have_tool("pdfinfo"):
        try:
            out = subprocess.run(["pdfinfo", str(pdf)], capture_output=True,
                                 text=True, timeout=60).stdout
            for line in out.splitlines():
                if line.lower().startswith("pages:"):
                    return int(line.split(":", 1)[1].strip())
        except (subprocess.SubprocessError, ValueError):
            pass
    try:
        import pypdfium2  # type: ignore

        doc = pypdfium2.PdfDocument(str(pdf))
        try:
            return len(doc)
        finally:
            doc.close()
    except Exception:
        return 0


def extract_embedded_text(pdf: Path, page: int) -> str:
    """
    Whatever text the PDF already carries for this page.

    Returns "" for a scanned book, which is the case here and the whole reason
    `ocr_page` exists. Trying this first is still worth it: it is instant, and
    if a future book is a real digital PDF the OCR step is skipped entirely.
    """
    if not have_tool("pdftotext"):
        return ""
    try:
        out = subprocess.run(
            ["pdftotext", "-f", str(page), "-l", str(page), "-layout",
             str(pdf), "-"],
            capture_output=True, text=True, timeout=120,
        )
        return (out.stdout or "").strip()
    except subprocess.SubprocessError:
        return ""


def render_page(pdf: Path, page: int, dest: Path, *, width: int = PAGE_IMAGE_WIDTH) -> bool:
    """Rasterise one page to PNG. Returns False if no renderer is available."""
    dest.parent.mkdir(parents=True, exist_ok=True)
    if have_tool("pdftoppm"):
        stem = dest.with_suffix("")
        try:
            subprocess.run(
                ["pdftoppm", "-f", str(page), "-l", str(page), "-png",
                 "-scale-to-x", str(width), "-scale-to-y", "-1",
                 "-singlefile", str(pdf), str(stem)],
                capture_output=True, timeout=300, check=True,
            )
            return dest.exists()
        except (subprocess.SubprocessError, OSError):
            return False

    try:
        import pypdfium2  # type: ignore

        doc = pypdfium2.PdfDocument(str(pdf))
        try:
            pdf_page = doc[page - 1]
            scale = width / max(pdf_page.get_width(), 1)
            pdf_page.render(scale=scale).to_pil().save(dest)
            return True
        finally:
            doc.close()
    except Exception:
        return False


def ocr_image(image: Path, *, lang: str = "eng", psm: int = 6) -> str:
    """Run tesseract over one already-rendered page image."""
    if not have_tool("tesseract"):
        return ""
    try:
        out = subprocess.run(
            ["tesseract", str(image), "stdout", "-l", lang, "--psm", str(psm)],
            capture_output=True, text=True, timeout=300,
        )
        return (out.stdout or "").strip()
    except subprocess.SubprocessError:
        return ""


def ocr_page(pdf: Path | None, page: int, *, book_code: str | None = None,
             lang: str = "eng") -> str:
    """
    OCR one page.

    Uses the image `scripts/render_pages.py` already wrote when there is one —
    rendering is the expensive half and doing it twice is pure waste. Falls
    back to rendering into a temporary file from the PDF.

    Slow either way, seconds per page, which is exactly why it happens at
    ingest time and never while a child is waiting.
    """
    if not have_tool("tesseract"):
        return ""

    if book_code:
        cached = image_path(book_code, page)
        if cached is not None:
            return ocr_image(cached, lang=lang)

    if pdf is None or not pdf.exists():
        return ""

    import tempfile

    with tempfile.TemporaryDirectory() as tmp:
        png = Path(tmp) / "page.png"
        if not render_page(pdf, page, png, width=2000):
            return ""
        return ocr_image(png, lang=lang)


# =============================================================================
# Reading — used at study time
# =============================================================================

def bundle_path(book_code: str) -> Path:
    """
    All of a book's page text in one JSON file.

    Two forms of the same cache exist because they serve different people. The
    per-page `.txt` files are for a human: OCR of a scanned maths page gets
    things wrong, and fixing page 23 should mean opening p0023.txt and editing
    the line, not hunting through a blob. The bundle is for moving the cache
    between machines in one file instead of a hundred.

    `page_text()` prefers the loose file, so a hand-corrected page always wins
    over whatever the bundle says.
    """
    return cache_dir(book_code) / "text.json"


@lru_cache(maxsize=8)
def _load_bundle(book_code: str, mtime_ns: int) -> dict[str, str]:
    """Parsed once per file version — `mtime_ns` is the cache key, not a arg."""
    path = bundle_path(book_code)
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}


def page_text(book_code: str, page: int) -> str:
    """
    The text of one page, from the cache the ingest wrote.

    Returns "" when the page was never ingested. Callers must treat that as
    "no grounding available" and refuse to teach from it, rather than letting
    the model fill the silence — an invented step in a maths procedure is
    invisible to a child who is already struggling, because they assume they
    are the one who is wrong.
    """
    path = text_path(book_code, page)
    if path.exists():
        return path.read_text(encoding="utf-8", errors="replace").strip()

    bundle = bundle_path(book_code)
    if bundle.exists():
        data = _load_bundle(book_code, bundle.stat().st_mtime_ns)
        return (data.get(str(page)) or "").strip()

    return ""


def write_bundle(book_code: str) -> int:
    """
    Collect the loose page files into text.json. Returns the page count.

    Merges rather than replaces. The loose files only exist for pages the
    lesson map currently uses, so replacing would quietly drop the text for
    every page the map happens not to cover today — and get it back only by
    re-OCRing the book.
    """
    folder = cache_dir(book_code)
    if not folder.exists():
        return 0

    data: dict[str, str] = {}
    existing = bundle_path(book_code)
    if existing.exists():
        try:
            loaded = json.loads(existing.read_text(encoding="utf-8"))
            if isinstance(loaded, dict):
                data.update({str(k): str(v) for k, v in loaded.items()})
        except (OSError, json.JSONDecodeError):
            pass
    for path in sorted(folder.glob("p*.txt")):
        try:
            page = int(path.stem[1:])
        except ValueError:
            continue
        body = path.read_text(encoding="utf-8", errors="replace").strip()
        if body:
            data[str(page)] = body
    if data:
        bundle_path(book_code).write_text(
            json.dumps(data, ensure_ascii=False, indent=1), encoding="utf-8"
        )
    return len(data)


def lesson_pages(conn: sqlite3.Connection, topic_id: int) -> list[sqlite3.Row]:
    """
    Every page of one lesson, in book order.

    A topic IS a lesson since schema_v5: ingest_curriculum.py writes one topic
    per lesson in the book and keeps `topics.book_id` and `topics.lesson_label`
    in step with `curriculum_pages`.
    """
    return conn.execute(
        """
        SELECT p.id, p.page, p.lesson, p.unit, p.lesson_order,
               b.id AS book_id, b.code AS book_code, b.title AS book_title,
               b.subject AS subject, b.grade AS grade, b.sha256 AS book_sha,
               b.is_verified AS book_verified
        FROM curriculum_pages p
        JOIN curriculum_books b ON b.id = p.book_id
        JOIN topics t ON t.book_id = b.id AND t.lesson_label = p.lesson
        WHERE t.id = ?
        ORDER BY p.page
        """,
        (topic_id,),
    ).fetchall()


def page_row(conn: sqlite3.Connection, page_id: int) -> sqlite3.Row | None:
    return conn.execute(
        """
        SELECT p.id, p.page, p.lesson, p.unit, p.lesson_order,
               b.id AS book_id, b.code AS book_code, b.title AS book_title,
               b.subject AS subject, b.grade AS grade, b.sha256 AS book_sha,
               b.is_verified AS book_verified,
               t.id AS topic_id, t.title AS topic_title
        FROM curriculum_pages p
        JOIN curriculum_books b ON b.id = p.book_id
        LEFT JOIN topics t ON t.book_id = b.id AND t.lesson_label = p.lesson
        WHERE p.id = ?
        """,
        (page_id,),
    ).fetchone()


def source_text(conn: sqlite3.Connection, topic_id: int,
                *, page: int | None = None, max_chars: int = 6000) -> str:
    """
    The grounding block handed to the model before it explains anything.

    Every page is labelled with its printed page number so the model — and
    anyone auditing what it said — can point at where a claim came from.
    """
    rows = lesson_pages(conn, topic_id)
    if page is not None:
        rows = [r for r in rows if r["page"] == page]
    if not rows:
        return ""

    parts, total = [], 0
    for row in rows:
        body = page_text(row["book_code"], row["page"])
        if not body:
            continue
        block = f"[{row['book_title']}, page {row['page']}]\n{body}"
        if total + len(block) > max_chars:
            break
        parts.append(block)
        total += len(block)
    return "\n\n".join(parts)


def has_text(conn: sqlite3.Connection, topic_id: int) -> bool:
    """Is there any ingested text behind this lesson at all?"""
    return bool(source_text(conn, topic_id, max_chars=400))


def coverage(conn: sqlite3.Connection) -> dict:
    """
    How much real curriculum is loaded.

    Surfaced on /health because "the tutor has nothing to say" and "the tutor
    is broken" look identical from the UI, and the first one is usually the
    real answer.
    """
    row = conn.execute(
        """
        SELECT
            (SELECT COUNT(*) FROM curriculum_books)                     AS books,
            (SELECT COUNT(*) FROM curriculum_books WHERE is_verified=1) AS verified_books,
            (SELECT COUNT(*) FROM curriculum_pages)                     AS pages,
            (SELECT COUNT(DISTINCT book_id || '|' || lesson)
               FROM curriculum_pages)                                   AS lessons,
            (SELECT COUNT(*) FROM topics WHERE book_id IS NOT NULL)     AS book_topics
        """
    ).fetchone()

    # Mapped pages that actually have text — not every file in the cache. A
    # page nobody teaches from is not coverage.
    cached = 0
    for page in conn.execute(
        "SELECT b.code AS book_code, p.page FROM curriculum_pages p "
        "JOIN curriculum_books b ON b.id = p.book_id"
    ):
        path = text_path(page["book_code"], page["page"])
        if path.exists() and path.stat().st_size > 0:
            cached += 1

    return {
        "books": row["books"],
        "verified_books": row["verified_books"],
        "pages": row["pages"],
        "lessons": row["lessons"],
        "topics_from_books": row["book_topics"],
        "pages_with_text": cached,
        "ready": row["verified_books"] > 0 and cached > 0,
    }
