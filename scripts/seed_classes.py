"""
Seed classes, the classroom device, and teacher cards.

    python scripts/seed_classes.py

Re-runnable. Everything is matched on a natural key and updated in place.

Run this AFTER seed_parents.py — it reuses the teachers that file creates
rather than inventing a second Sarah Ahmed.

-----------------------------------------------------------------------------
THE CARD UIDs ARE PLACEHOLDERS
-----------------------------------------------------------------------------
Real MIFARE UIDs are burned in at the factory and cannot be chosen. Tap each
card on the reader, copy the UID it prints, and run:

    python scripts/seed_classes.py --card sarah=A3F21C08 --card noha=7B22019C

The names on the left are the first names of the teachers seeded below.
"""

import argparse
import secrets
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.db import get_conn, init_db      # noqa: E402


# The four classes from the teacher interface. Beshoy and Lo2lo2 appear in two
# of them — which is the whole reason class_students is its own table.
CLASSES = [
    {"name": "Primary 5 — Mathematics", "short": "P5 Mathematics", "grade": "5", "subject": "MATH",
     "students": ["stu-01", "stu-06"], "teachers": [("Sarah Ahmed", "lead")]},
    {"name": "Primary 5 — Science", "short": "P5 Science", "grade": "5", "subject": "SCI",
     "students": ["stu-01", "stu-06"], "teachers": [("Noha Khaled", "lead")]},
    {"name": "Primary 4 — Mathematics", "short": "P4 Mathematics", "grade": "4", "subject": "MATH",
     "students": ["stu-02"], "teachers": [("Sarah Ahmed", "lead")]},
    {"name": "Primary 6 — Mathematics", "short": "P6 Mathematics", "grade": "6", "subject": "MATH",
     "students": ["stu-03"], "teachers": [("Sarah Ahmed", "assistant")]},
]

# The device by the door. It is bound to ONE class, because with no buttons it
# cannot ask a teacher which of theirs they mean.
DEVICE_LABEL = "Room 4 — door unit"
DEVICE_CLASS = "Primary 5 — Mathematics"

PLACEHOLDER_CARDS = {
    "Sarah Ahmed": "AAAA1111",
    "Noha Khaled": "BBBB2222",
}


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--card", action="append", default=[], metavar="NAME=UID",
                    help="Real card UID, e.g. --card sarah=A3F21C08")
    ap.add_argument("--new-device-key", action="store_true",
                    help="Rotate the device key (you must reflash the ESP32)")
    args = ap.parse_args()

    overrides = {}
    for item in args.card:
        if "=" not in item:
            sys.exit(f"--card needs NAME=UID, got {item!r}")
        name, uid = item.split("=", 1)
        overrides[name.strip().lower()] = uid.strip().upper()

    init_db()

    with get_conn() as conn:
        subjects = {r["code"]: r["id"] for r in conn.execute("SELECT id, code FROM subjects")}
        teachers = {r["full_name"]: r["id"]
                    for r in conn.execute("SELECT id, full_name FROM teachers")}

        missing = {n for c in CLASSES for n, _ in c["teachers"]} - set(teachers)
        if missing:
            sys.exit("These teachers do not exist yet: " + ", ".join(sorted(missing))
                     + "\nRun  python scripts/seed_parents.py  first.")

        class_ids = {}
        for c in CLASSES:
            row = conn.execute("SELECT id FROM classes WHERE name = ?", (c["name"],)).fetchone()
            if row:
                cid = row["id"]
                conn.execute("UPDATE classes SET grade=?, subject_id=?, short_name=?, "
                             "is_active=1 WHERE id=?",
                             (c["grade"], subjects.get(c["subject"]), c["short"], cid))
            else:
                cid = conn.execute(
                    "INSERT INTO classes (name, grade, subject_id, short_name) "
                    "VALUES (?,?,?,?)",
                    (c["name"], c["grade"], subjects.get(c["subject"]), c["short"]),
                ).lastrowid
            class_ids[c["name"]] = cid

            conn.execute("DELETE FROM class_students WHERE class_id = ?", (cid,))
            for ext in c["students"]:
                s = conn.execute("SELECT id FROM students WHERE external_id = ?",
                                 (ext,)).fetchone()
                if s is None:
                    print(f"  !! no student {ext!r} — skipped")
                    continue
                conn.execute("INSERT INTO class_students (class_id, student_id) VALUES (?,?)",
                             (cid, s["id"]))

            conn.execute("DELETE FROM teacher_classes WHERE class_id = ?", (cid,))
            for name, role in c["teachers"]:
                conn.execute(
                    "INSERT INTO teacher_classes (teacher_id, class_id, role) VALUES (?,?,?)",
                    (teachers[name], cid, role),
                )

        # --- cards ----------------------------------------------------------
        issued = []
        for name, placeholder in PLACEHOLDER_CARDS.items():
            uid = overrides.get(name.split()[0].lower(), placeholder)
            real = uid != placeholder
            conn.execute("DELETE FROM teacher_cards WHERE teacher_id = ?", (teachers[name],))
            conn.execute(
                "INSERT INTO teacher_cards (card_uid, teacher_id, label) VALUES (?,?,?)",
                (uid, teachers[name], "lanyard"),
            )
            issued.append((name, uid, real))

        # --- device ---------------------------------------------------------
        dev = conn.execute("SELECT * FROM devices WHERE label = ?", (DEVICE_LABEL,)).fetchone()
        if dev and not args.new_device_key:
            key = dev["device_key"]
            conn.execute("UPDATE devices SET class_id=?, lcd_cols=20, lcd_rows=4, "
                         "is_active=1 WHERE id=?", (class_ids[DEVICE_CLASS], dev["id"]))
        else:
            key = "dev_" + secrets.token_urlsafe(24)
            if dev:
                conn.execute("UPDATE devices SET device_key=?, class_id=? WHERE id=?",
                             (key, class_ids[DEVICE_CLASS], dev["id"]))
            else:
                conn.execute(
                    "INSERT INTO devices (device_key, label, class_id, lcd_cols, lcd_rows) "
                    "VALUES (?,?,?,20,4)", (key, DEVICE_LABEL, class_ids[DEVICE_CLASS]))

    print()
    print("  Classes seeded")
    print("  " + "-" * 62)
    for c in CLASSES:
        who = ", ".join(f"{n} ({r})" for n, r in c["teachers"])
        print(f"  {c['name']:<26} {len(c['students'])} student(s)   {who}")
    print("  " + "-" * 62)
    print()
    print("  Teacher cards")
    for name, uid, real in issued:
        tag = "" if real else "   <- PLACEHOLDER, replace with the real UID"
        print(f"    {name:<14} {uid}{tag}")
    print()
    print(f"  Device: {DEVICE_LABEL}  ->  {DEVICE_CLASS}")
    print(f"  DEVICE KEY (put this in the firmware):")
    print(f"    {key}")
    print()
    print("  Once you have real UIDs:")
    print("    python scripts/seed_classes.py --card sarah=XXXXXXXX --card noha=YYYYYYYY")
    print()


if __name__ == "__main__":
    main()
