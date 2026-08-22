#!/usr/bin/env python3
"""
Write practice questions from the book, and let a human approve them.

    python scripts/generate_practice.py --list
    python scripts/generate_practice.py --lesson 3 --count 4
    python scripts/generate_practice.py --all --count 3
    python scripts/generate_practice.py --review
    python scripts/generate_practice.py --approve 12 14 15
    python scripts/generate_practice.py --approve-all          # read them first

WHY A SCRIPT AND NOT JUST THE APP
---------------------------------
The app generates practice live, from the page, while the child works — that
is the point of hybrid generation. But a quiz is SCORED, and a scored question
nobody has read is a question that can be wrong in front of a child who will
assume the fault is theirs. So quizzes and mini-games only ever draw on
`review_status = 'approved'`, and this is where a human moves items there.

Running it before a demo also means the quiz has a bank to fall back on when
the network does not cooperate.

WHAT THE MODEL IS AND IS NOT ALLOWED TO DO
------------------------------------------
It sees one lesson's pages and nothing else. Every question must be answerable
from those pages alone. Everything it returns is validated — shape, duplicate
options, index range, reading length, and whether the prompt or the hint leaks
the answer — before it is stored, and it is stored as 'pending' with the page
it came from, so the review below shows the item next to its source.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from app.db import get_conn                    # noqa: E402
from app.services import curriculum, tutor     # noqa: E402


def list_lessons(conn) -> int:
    rows = conn.execute(
        """
        SELECT t.id, t.title, t.is_verified,
               b.subject, b.grade, b.is_verified AS book_verified,
               (SELECT COUNT(*) FROM curriculum_pages p
                 WHERE p.book_id = t.book_id AND p.lesson = t.lesson_label) AS pages,
               (SELECT COUNT(*) FROM questions q
                 WHERE q.topic_id = t.id AND q.review_status = 'approved') AS approved,
               (SELECT COUNT(*) FROM questions q
                 WHERE q.topic_id = t.id AND q.review_status = 'pending') AS pending
        FROM topics t
        JOIN curriculum_books b ON b.id = t.book_id
        ORDER BY b.id, t.sort_order
        """
    ).fetchall()

    if not rows:
        print("No lessons. Run scripts/ingest_curriculum.py first.")
        return 1

    print(f"{'id':>4}  {'subject':<8} {'g':<2} {'pages':>5} {'ok':>4} {'pend':>5}  lesson")
    for r in rows:
        gate = "" if (r["is_verified"] and r["book_verified"]) else "  [UNVERIFIED]"
        print(f"{r['id']:>4}  {r['subject'][:8]:<8} {r['grade']:<2} "
              f"{r['pages']:>5} {r['approved']:>4} {r['pending']:>5}  "
              f"{r['title'][:58]}{gate}")
    return 0


def generate(conn, topic_id: int, count: int) -> int:
    topic = conn.execute("SELECT title FROM topics WHERE id = ?", (topic_id,)).fetchone()
    if topic is None:
        print(f"No lesson {topic_id}.")
        return 1

    print(f"\n[{topic_id}] {topic['title']}")
    if not curriculum.has_text(conn, topic_id):
        print("  no ingested text — skipped")
        return 1

    result = tutor.generate_questions(conn, topic_id, count=count)
    conn.commit()

    accepted = result.get("accepted", 0)
    print(f"  engine {result.get('engine')}  "
          f"generated {result.get('generated', 0)}  "
          f"accepted {accepted}  rejected {result.get('rejected', 0)}")
    for reason in result.get("reasons", []):
        print(f"    rejected: {reason}")
    return 0 if accepted else 1


def review(conn, limit: int) -> int:
    rows = conn.execute(
        "SELECT * FROM v_questions_for_review LIMIT ?", (limit,)
    ).fetchall()
    if not rows:
        print("Nothing pending review.")
        return 0

    for r in rows:
        options = json.loads(r["options_json"])
        print(f"\n--- id {r['id']} — {r['subject']} / {r['topic_title']}")
        print(f"    source: {r['book_title']}, page {r['source_page']}")
        print(f"    {r['prompt']}")
        for i, opt in enumerate(options):
            print(f"      {'>' if i == r['correct_index'] else ' '} {i}. {opt}")
        if r["explanation"]:
            print(f"    why: {r['explanation']}")
        if r["worked_solution"]:
            print(f"    working: {r['worked_solution'][:200]}")
    print(f"\n{len(rows)} pending. Approve with:\n"
          f"    python scripts/generate_practice.py --approve "
          f"{' '.join(str(r['id']) for r in rows[:5])}")
    return 0


def set_status(conn, ids: list[int], status: str) -> int:
    changed = 0
    for qid in ids:
        cur = conn.execute(
            "UPDATE questions SET review_status = ? WHERE id = ? "
            "AND review_status = 'pending'",
            (status, qid),
        )
        changed += cur.rowcount
    conn.commit()
    print(f"{changed} question(s) -> {status}")
    return 0


def main() -> int:
    ap = argparse.ArgumentParser(
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    ap.add_argument("--list", action="store_true", help="show every lesson")
    ap.add_argument("--lesson", type=int, help="generate for this lesson id")
    ap.add_argument("--all", action="store_true",
                    help="generate for every verified lesson that has text")
    ap.add_argument("--count", type=int, default=4, help="questions per lesson")
    ap.add_argument("--review", action="store_true", help="print what is pending")
    ap.add_argument("--limit", type=int, default=25, help="how many to review")
    ap.add_argument("--approve", type=int, nargs="+", metavar="ID")
    ap.add_argument("--reject", type=int, nargs="+", metavar="ID")
    ap.add_argument("--approve-all", action="store_true",
                    help="approve everything pending — read it first")
    args = ap.parse_args()

    with get_conn() as conn:
        if args.list:
            return list_lessons(conn)
        if args.review:
            return review(conn, args.limit)
        if args.approve:
            return set_status(conn, args.approve, "approved")
        if args.reject:
            return set_status(conn, args.reject, "rejected")
        if args.approve_all:
            ids = [r["id"] for r in conn.execute(
                "SELECT id FROM questions WHERE review_status = 'pending'")]
            return set_status(conn, ids, "approved")

        if args.lesson:
            return generate(conn, args.lesson, args.count)

        if args.all:
            rows = conn.execute(
                """
                SELECT t.id FROM topics t
                JOIN curriculum_books b ON b.id = t.book_id
                WHERE t.is_verified = 1 AND b.is_verified = 1
                ORDER BY b.id, t.sort_order
                """
            ).fetchall()
            for r in rows:
                generate(conn, r["id"], args.count)
            print("\nNow read them before anything is scored on them:\n"
                  "    python scripts/generate_practice.py --review")
            return 0

    ap.print_help()
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
