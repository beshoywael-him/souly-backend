"""
Turn each page of a curriculum PDF into an image on disk.

    python scripts/render_pages.py
    python scripts/render_pages.py --width 1000 --quality 72
    python scripts/render_pages.py --book "5th primary math.pdf" --pages 1-40

WHY THIS EXISTS
---------------
The Ministry books are scans. `pdftotext` returns nothing for them, and more
importantly a child working through a maths page should be looking at the
actual page — the diagram, the number line, the worked example as printed —
not at a text extraction of it.

So every mapped page becomes an image once, here, and the app serves that.
The same images are what gets OCR'd to build the text the tutor is grounded
in. Rendering is the slow part and it happens exactly once per book.

WHAT IT WRITES

    data/curriculum/.cache/<book-code>/p0001.jpg
    data/curriculum/.cache/<book-code>/manifest.json

Everything under `.cache/` is derived. Delete it and re-run this script and
you get it all back from the PDFs, which stay untouched and stay the only
copy of the content.

DEPENDENCIES
------------
One of, in order of preference:

    pip install pypdfium2 pillow   # two plain wheels, no system dependencies
    pip install pymupdf
    poppler's pdftoppm on PATH

The first line is the one to use on Windows. pypdfium2 rasterises the page and
pillow writes the JPEG — pypdfium2 alone renders into a buffer it cannot save,
so both are needed.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import shutil
import subprocess
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

DEFAULT_DIR = PROJECT_ROOT / "data" / "curriculum"

# 1000px wide is legible on a tablet and lands around 120KB per page as JPEG.
# Wider than this mostly buys file size: the source is a scan, so there is no
# extra detail to recover past the scanner's own resolution.
DEFAULT_WIDTH = 1000
DEFAULT_QUALITY = 72


def book_code(filename: str) -> str:
    """
    '5th primary math.pdf' -> '5th-primary-math'

    Stable and derived from the filename, so re-running after adding a book
    does not renumber anything that already exists.
    """
    stem = Path(filename).stem.strip().lower()
    stem = re.sub(r"[^a-z0-9]+", "-", stem).strip("-")
    return stem or "book"


def file_sha256(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as fh:
        for block in iter(lambda: fh.read(1 << 20), b""):
            h.update(block)
    return h.hexdigest()


def parse_pages(spec: str | None, total: int) -> list[int]:
    """'1-40', '3', '1-10,25,30-32' -> a sorted list of page numbers."""
    if not spec:
        return list(range(1, total + 1))
    wanted: set[int] = set()
    for part in spec.split(","):
        part = part.strip()
        if not part:
            continue
        if "-" in part:
            lo, _, hi = part.partition("-")
            wanted.update(range(int(lo), int(hi) + 1))
        else:
            wanted.add(int(part))
    return sorted(p for p in wanted if 1 <= p <= total)


# =============================================================================
# Renderers — first one that imports wins
# =============================================================================

class Renderer:
    name = "none"

    def page_count(self, pdf: Path) -> int:
        raise NotImplementedError

    def render(self, pdf: Path, page: int, dest: Path, width: int, quality: int) -> bool:
        raise NotImplementedError

    def close(self) -> None:
        pass


class PdfiumRenderer(Renderer):
    name = "pypdfium2"

    def __init__(self) -> None:
        # Pillow is imported here, not at the point of use, so a missing
        # install is caught before any rendering starts. pypdfium2 hands back
        # a raw bitmap and Pillow is what writes it to a JPEG.
        import PIL.Image  # noqa: F401
        import pypdfium2  # noqa: F401

        self._pypdfium2 = pypdfium2
        self._doc = None
        self._path: Path | None = None

    def _open(self, pdf: Path):
        if self._path != pdf:
            if self._doc is not None:
                self._doc.close()
            self._doc = self._pypdfium2.PdfDocument(str(pdf))
            self._path = pdf
        return self._doc

    def page_count(self, pdf: Path) -> int:
        return len(self._open(pdf))

    def render(self, pdf: Path, page: int, dest: Path, width: int, quality: int) -> bool:
        doc = self._open(pdf)
        pdf_page = doc[page - 1]
        scale = width / max(pdf_page.get_width(), 1)
        image = pdf_page.render(scale=scale).to_pil()
        if image.mode not in ("RGB", "L"):
            image = image.convert("RGB")
        image.save(dest, quality=quality, optimize=True)
        return dest.exists()

    def close(self) -> None:
        if self._doc is not None:
            self._doc.close()


class MuPdfRenderer(Renderer):
    name = "pymupdf"

    def __init__(self) -> None:
        import fitz  # noqa: F401

        self._fitz = fitz

    def page_count(self, pdf: Path) -> int:
        with self._fitz.open(str(pdf)) as doc:
            return doc.page_count

    def render(self, pdf: Path, page: int, dest: Path, width: int, quality: int) -> bool:
        with self._fitz.open(str(pdf)) as doc:
            pg = doc[page - 1]
            scale = width / max(pg.rect.width, 1)
            pix = pg.get_pixmap(matrix=self._fitz.Matrix(scale, scale))
            pix.save(dest, jpg_quality=quality)
        return dest.exists()


class PopplerRenderer(Renderer):
    name = "pdftoppm"

    def page_count(self, pdf: Path) -> int:
        out = subprocess.run(["pdfinfo", str(pdf)], capture_output=True,
                             text=True, timeout=120).stdout
        for line in out.splitlines():
            if line.lower().startswith("pages:"):
                return int(line.split(":", 1)[1].strip())
        return 0

    def render(self, pdf: Path, page: int, dest: Path, width: int, quality: int) -> bool:
        stem = dest.with_suffix("")
        subprocess.run(
            ["pdftoppm", "-f", str(page), "-l", str(page), "-jpeg",
             "-jpegopt", f"quality={quality}",
             "-scale-to-x", str(width), "-scale-to-y", "-1",
             "-singlefile", str(pdf), str(stem)],
            capture_output=True, timeout=300, check=True,
        )
        return dest.exists()


def pick_renderer() -> Renderer:
    for factory in (PdfiumRenderer, MuPdfRenderer):
        try:
            return factory()
        except ImportError:
            continue
    if shutil.which("pdftoppm") and shutil.which("pdfinfo"):
        return PopplerRenderer()

    print(
        "No usable PDF renderer.\n\n"
        "Install these two — on Windows they are the ones to use, both are\n"
        "plain wheels with no compiler and no system libraries:\n\n"
        "    pip install pypdfium2 pillow\n\n"
        "pypdfium2 rasterises the page; pillow writes the JPEG. Having one\n"
        "without the other is the common case and it is why this check runs\n"
        "before any page is touched rather than failing per page.\n",
        file=sys.stderr,
    )
    raise SystemExit(2)


# =============================================================================
# Main
# =============================================================================

def render_book(renderer: Renderer, pdf: Path, cache_root: Path, *,
                width: int, quality: int, pages_spec: str | None,
                force: bool) -> dict:
    code = book_code(pdf.name)
    out_dir = cache_root / code
    out_dir.mkdir(parents=True, exist_ok=True)

    total = renderer.page_count(pdf)
    pages = parse_pages(pages_spec, total)

    print(f"\n{pdf.name}")
    print(f"  code       {code}")
    print(f"  pages      {total} in the file, {len(pages)} to render")
    print(f"  output     {out_dir}")

    written, skipped = 0, 0
    for page in pages:
        dest = out_dir / f"p{page:04d}.jpg"
        if dest.exists() and not force:
            skipped += 1
            continue
        try:
            if renderer.render(pdf, page, dest, width, quality):
                written += 1
        except Exception as exc:                      # noqa: BLE001
            print(f"  page {page}: FAILED — {exc}")
        if written and written % 10 == 0:
            print(f"  ... {written} rendered", flush=True)

    manifest = {
        "code": code,
        "filename": pdf.name,
        "page_count": total,
        "sha256": file_sha256(pdf),
        "rendered_pages": sorted(
            int(p.stem[1:]) for p in out_dir.glob("p*.jpg")
        ),
        "width": width,
        "quality": quality,
        "renderer": renderer.name,
    }
    (out_dir / "manifest.json").write_text(
        json.dumps(manifest, indent=2), encoding="utf-8"
    )

    size_mb = sum(p.stat().st_size for p in out_dir.glob("p*.jpg")) / (1024 * 1024)
    print(f"  rendered   {written} new, {skipped} already there")
    print(f"  total      {len(manifest['rendered_pages'])} images, {size_mb:.1f} MB")
    return manifest


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--dir", default=str(DEFAULT_DIR),
                    help="directory holding the PDFs (default: data/curriculum)")
    ap.add_argument("--book", action="append",
                    help="only this filename; repeatable")
    ap.add_argument("--pages", help="page range, e.g. 1-40 or 1-10,25")
    ap.add_argument("--width", type=int, default=DEFAULT_WIDTH)
    ap.add_argument("--quality", type=int, default=DEFAULT_QUALITY)
    ap.add_argument("--force", action="store_true",
                    help="re-render pages that already exist")
    args = ap.parse_args()

    root = Path(args.dir)
    if not root.is_absolute():
        root = PROJECT_ROOT / root
    if not root.exists():
        print(f"No such directory: {root}", file=sys.stderr)
        return 1

    pdfs = sorted(root.glob("*.pdf"))
    if args.book:
        wanted = {b.lower() for b in args.book}
        pdfs = [p for p in pdfs if p.name.lower() in wanted]
    if not pdfs:
        print(f"No PDFs found in {root}", file=sys.stderr)
        return 1

    renderer = pick_renderer()
    print(f"Renderer: {renderer.name}")

    try:
        for pdf in pdfs:
            render_book(renderer, pdf, root / ".cache",
                        width=args.width, quality=args.quality,
                        pages_spec=args.pages, force=args.force)
    finally:
        renderer.close()

    print("\nDone. The images are under data/curriculum/.cache/")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
