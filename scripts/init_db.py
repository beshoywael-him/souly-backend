#!/usr/bin/env python3
"""
Create the Souly database from schema.sql.

    python scripts/init_db.py            # create if missing, leave data alone
    python scripts/init_db.py --reset    # delete and rebuild from scratch

`--reset` destroys all data. It asks first unless you pass --yes.
"""

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.config import settings          # noqa: E402
from app.db import connect, init_db      # noqa: E402


def main() -> int:
    parser = argparse.ArgumentParser(description="Initialise the Souly database.")
    parser.add_argument("--reset", action="store_true",
                        help="Delete the existing database first. DESTRUCTIVE.")
    parser.add_argument("--yes", action="store_true",
                        help="Skip the confirmation prompt for --reset.")
    args = parser.parse_args()

    db_path = settings.db_file
    existed = db_path.exists()

    if args.reset and existed and not args.yes:
        answer = input(f"Delete {db_path} and all its data? [y/N] ").strip().lower()
        if answer != "y":
            print("Aborted.")
            return 1

    init_db(drop_existing=args.reset)

    conn = connect()
    try:
        tables = [
            r["name"] for r in conn.execute(
                "SELECT name FROM sqlite_master WHERE type='table' "
                "AND name NOT LIKE 'sqlite_%' ORDER BY name"
            )
        ]
        views = [
            r["name"] for r in conn.execute(
                "SELECT name FROM sqlite_master WHERE type='view' ORDER BY name"
            )
        ]
    finally:
        conn.close()

    action = "Reset" if args.reset else ("Verified" if existed else "Created")
    print(f"{action}: {db_path}")
    print(f"  {len(tables)} tables: {', '.join(tables)}")
    print(f"  {len(views)} views:  {', '.join(views)}")
    print("\nNext: python scripts/seed_students.py")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
