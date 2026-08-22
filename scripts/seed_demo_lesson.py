#!/usr/bin/env python3
"""
SUPERSEDED — this script no longer does anything. The file is safe to delete.

It used to seed one hand-written demo lesson ("DEMO.FRACTIONS") so the lesson
screens had something in them while the real curriculum was empty. The real
curriculum is not empty any more: the Ministry books are ingested, and the two
tables this script wrote to — `lessons` and `lesson_steps` — were removed by
schema_v5.

What you probably want instead:

    python scripts/render_pages.py          turn the PDFs into page images
    python scripts/ingest_curriculum.py     load the books and the page map
    python scripts/generate_practice.py     write practice from a real page

Left in place rather than deleted only so nobody wonders where it went.
"""

import sys


def main() -> int:
    print(__doc__.strip(), file=sys.stderr)
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
