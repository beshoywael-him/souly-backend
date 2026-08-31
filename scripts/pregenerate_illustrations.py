#!/usr/bin/env python3
"""
Draw every page's picture ahead of time, so no child waits for one.

WHY THIS SCRIPT MATTERS MORE THAN IT LOOKS
------------------------------------------
The lesson text is now held back until the picture arrives. That is right —
for a child who reads with difficulty the picture is the explanation, and
letting the words land first teaches them to skip the part built for them.
But it moves the cost of generation onto the child: on a page nobody has
opened before, a curtain and two image calls is twenty seconds of a child
sitting in front of a blank screen.

Pictures are cached by scene and shared by the whole class, so that cost is
paid exactly once per page — by whoever opens it first. This script makes
that "whoever" a laptop the night before instead of a nine-year-old in a
lesson.

    python scripts/pregenerate_illustrations.py                 # everything
    python scripts/pregenerate_illustrations.py --student stu-02
    python scripts/pregenerate_illustrations.py --topic 10
    python scripts/pregenerate_illustrations.py --dry-run       # cost first

WHAT IT COSTS
-------------
One image call per new scene, plus a second one per scene that has motion.
--dry-run reports how many of each without spending anything, which is worth
running first on a pay-as-you-go key.

Renditions have to exist before the pictures can: the scene is written by the
model from the book page, and lives in page_renditions.visual_json. So open
the lessons once (or let a child do it) before running this, and it will find
them.
"""

import argparse
import sqlite3
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.config import settings                                   # noqa: E402
from app.services import animation, curriculum, llm, tutor        # noqa: E402


def rows(conn: sqlite3.Connection, student: str | None, topic: int | None):
    sql = """
        SELECT r.visual_json, r.page_id, cp.page, cp.lesson, s.external_id
        FROM page_renditions r
        JOIN curriculum_pages cp ON cp.id = r.page_id
        JOIN students s ON s.id = r.student_id
        WHERE r.mode = 'lesson' AND r.visual_json IS NOT NULL
    """
    args: list = []
    if student:
        sql += " AND s.external_id = ?"
        args.append(student)
    if topic:
        sql += (" AND cp.lesson = (SELECT title FROM topics WHERE id = ?)")
        args.append(topic)
    sql += " ORDER BY cp.page"
    return conn.execute(sql, args).fetchall()


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--student", help="only this student's renditions")
    ap.add_argument("--topic", type=int, help="only this lesson")
    ap.add_argument("--dry-run", action="store_true",
                    help="report what would be drawn, spend nothing")
    ap.add_argument("--limit", type=int, default=0,
                    help="stop after this many new pictures")
    a = ap.parse_args()

    if not settings.image_configured:
        print("✗ No image key configured. Set IMAGE_GENERATOR_API_KEY in .env.")
        return 1

    conn = sqlite3.connect(settings.db_file)
    conn.row_factory = sqlite3.Row

    # One scene can be shared by several pages and several children — that is
    # the whole point of keying the cache on the scene. Collapse to unique
    # scenes so the count reported is the count of calls actually made.
    scenes: dict[str, dict] = {}
    for row in rows(conn, a.student, a.topic):
        visual = tutor._load_visual(row["visual_json"])
        if not visual or not visual.get("scene") or not visual.get("key"):
            continue
        scenes.setdefault(visual["key"], {
            "scene": visual["scene"],
            "motion": visual.get("motion", ""),
            "where": f"{row['lesson']} p{row['page']}",
        })

    fmt = settings.motion_format
    todo, already = [], 0
    for key, item in scenes.items():
        wants_motion = bool(item["motion"]) and settings.motion_configured
        have_animation = curriculum.existing_animation(key) is not None
        have_still = curriculum.illustration_path(key).exists()
        if have_animation or (have_still and not wants_motion):
            already += 1
            continue
        item["calls"] = 1 if have_still else (2 if wants_motion else 1)
        todo.append((key, item))

    if a.limit:
        todo = todo[: a.limit]

    moving = sum(1 for _, i in todo if i["motion"])
    calls = sum(i["calls"] for _, i in todo)
    print(f"{len(scenes)} scenes in the book, {already} already drawn.")
    print(f"{len(todo)} to draw ({moving} with motion) "
          f"= {calls} image calls, format {fmt}.")

    if a.dry_run:
        for key, item in todo[:20]:
            mark = "▶" if item["motion"] else " "
            print(f"  {mark} {item['where']:38} {item['scene'][:60]}")
        if len(todo) > 20:
            print(f"  … and {len(todo) - 20} more")
        return 0

    drawn = failed = animated = 0
    for n, (key, item) in enumerate(todo, 1):
        started = time.time()
        still = curriculum.illustration_path(key)
        print(f"[{n}/{len(todo)}] {item['where']:38} ", end="", flush=True)

        if still.exists() and still.stat().st_size > 0:
            first, second = still.read_bytes(), None
            error = None
            if item["motion"] and settings.motion_configured:
                second, _, error = llm.edit_image(first, "image/png",
                                                  item["motion"])
        elif item["motion"] and settings.motion_configured:
            first, second, _, error = llm.generate_animation(item["scene"],
                                                             item["motion"])
        else:
            first, _, error = llm.generate_image(item["scene"])
            second = None

        if not first:
            failed += 1
            print(f"✗ {error}")
            if llm.image_quota_blocked():
                print("\n  Quota is gone. Stopping — the rest would all fail. "
                      "Run this again later and it will pick up where it "
                      "left off, because everything drawn is already cached.")
                break
            continue

        still.parent.mkdir(parents=True, exist_ok=True)
        still.write_bytes(first)
        drawn += 1

        if second:
            try:
                target = curriculum.animation_path(key, fmt)
                animation.write(target, first, second, fmt=fmt)
                animated += 1
                size = target.stat().st_size / 1024
                print(f"✓ moving, {size:.0f} KB, {time.time() - started:.1f}s")
                continue
            except Exception as exc:
                print(f"✓ still only (would not assemble: {exc})")
                continue

        note = "" if not item["motion"] else f" (no motion: {error})"
        print(f"✓ still, {time.time() - started:.1f}s{note}")

    print(f"\n{drawn} drawn, {animated} of them moving, {failed} failed.")
    print(f"Cached in {curriculum.illustration_dir()}")
    if drawn:
        print("Those pages will now open instantly for every child.")
    return 0 if not failed else 1


if __name__ == "__main__":
    sys.exit(main())
