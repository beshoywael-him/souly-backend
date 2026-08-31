#!/usr/bin/env python3
"""
Give a teacher a password so they can open the classroom dashboard.

    python scripts/set_teacher_password.py --list
    python scripts/set_teacher_password.py sarah@souly.local
    python scripts/set_teacher_password.py sarah@souly.local --password souly-2026

`seed_parents.py` creates the teaching staff but deliberately stores a
placeholder they cannot sign in with, because a seed script that writes a real
password writes the SAME real password into every copy of this project. This
is where a human chooses one.

The password is hashed with PBKDF2 before it is stored (app/security.py) and
the plaintext is never written anywhere — not to the database, not to a log.
"""

from __future__ import annotations

import argparse
import getpass
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from app.db import get_conn                 # noqa: E402
from app.security import hash_secret        # noqa: E402

MIN_LENGTH = 8


def main() -> int:
    ap = argparse.ArgumentParser(description="Set a teacher's dashboard password.")
    ap.add_argument("email", nargs="?", help="Which teacher.")
    ap.add_argument("--password", help="Set it non-interactively. Shell history "
                                       "will remember this; prefer the prompt.")
    ap.add_argument("--list", action="store_true", help="Show the staff list.")
    args = ap.parse_args()

    with get_conn() as conn:
        teachers = conn.execute(
            "SELECT id, full_name, email, title, is_active, last_login_at "
            "FROM teachers ORDER BY is_homeroom DESC, full_name"
        ).fetchall()

        if args.list or not args.email:
            if not teachers:
                print("\nNo teachers yet. Run:  python scripts/seed_parents.py\n")
                return 1
            print("\n  Teaching staff\n")
            for t in teachers:
                signed_in = t["last_login_at"] or "never signed in"
                state = "" if t["is_active"] else "   (inactive)"
                print(f"    {t['email']:<26} {t['full_name']:<16} "
                      f"{t['title'] or '':<18} {signed_in}{state}")
            print("\n  python scripts/set_teacher_password.py <email>\n")
            return 0 if args.list else 1

        row = conn.execute(
            "SELECT id, full_name FROM teachers WHERE LOWER(email) = ?",
            (args.email.strip().lower(),),
        ).fetchone()

        if row is None:
            print(f"\nNo teacher with the email {args.email!r}.")
            print("Run with --list to see who exists.\n")
            return 1

        password = args.password
        if not password:
            password = getpass.getpass(f"New password for {row['full_name']}: ")
            again = getpass.getpass("Type it again: ")
            if password != again:
                print("\nThose did not match. Nothing changed.\n")
                return 1

        if len(password) < MIN_LENGTH:
            print(f"\nToo short — at least {MIN_LENGTH} characters. This account "
                  f"opens every child in the class at once.\n")
            return 1

        conn.execute(
            "UPDATE teachers SET password_hash = ?, failed_logins = 0, "
            "locked_until = NULL WHERE id = ?",
            (hash_secret(password), row["id"]),
        )

    print(f"\n  Password set for {row['full_name']} ({args.email}).")
    print("  Sign in at  http://localhost:8000/teacher\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
