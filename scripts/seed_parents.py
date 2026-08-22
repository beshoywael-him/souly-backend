"""
Seed the parents' hub: parents, the children they belong to, teachers,
teacher notes, and a starting conversation each.

    python scripts/seed_parents.py

Re-runnable. Everything is matched on a natural key (email for people,
external_id for children) and updated in place, so running it twice does not
create a second Fayrouz.

-----------------------------------------------------------------------------
THE ACCESS CODES ARE DEMO CODES
-----------------------------------------------------------------------------
They are short and typeable because someone has to enter one on a phone, on a
competition floor, in front of judges. That is the only reason. Only the hash
is stored either way, but a real deployment should call
security.generate_access_code() and hand the result over out-of-band --
`python scripts/seed_parents.py --real-codes` does exactly that and prints
them once.
"""

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.db import get_conn, init_db          # noqa: E402
from app.security import generate_access_code, hash_secret  # noqa: E402


# --- Who belongs to whom -----------------------------------------------------
# The children already exist (seed_students.py made them). This file only
# creates the parents and draws the lines.
PARENTS = [
    {
        "full_name": "Rancy",
        "email": "rancy@souly.local",
        "phone": "+20 100 111 2233",
        "avatar_color": "#EC4899",
        "demo_code": "SOULY-RANCY",
        "children": [("stu-03", "mother")],            # Aziz
    },
    {
        "full_name": "Karma",
        "email": "karma@souly.local",
        "phone": "+20 100 444 5566",
        "avatar_color": "#0EA5E9",
        "demo_code": "SOULY-KARMA",
        "children": [("stu-06", "mother")],            # Lo2lo2
    },
    {
        "full_name": "Fayrouz",
        "email": "fayrouz@souly.local",
        "phone": "+20 100 123 4567",
        "avatar_color": "#7C3AED",
        "demo_code": "SOULY-FAYROUZ",
        # The two-child case. Everything downstream — the switcher, the
        # per-child conversations, the unread counts — exists because of
        # this one line.
        "children": [("stu-01", "mother"), ("stu-02", "mother")],  # Beshoy, Atef
    },
]

TEACHERS = [
    {"full_name": "Sarah Ahmed",  "email": "sarah@souly.local",
     "title": "Homeroom Teacher", "subject": "MATH", "initials": "SA",
     "color": "#7C3AED", "homeroom": 1},
    {"full_name": "Noha Khaled",  "email": "noha@souly.local",
     "title": "Science Teacher",  "subject": "SCI",  "initials": "NK",
     "color": "#10B981", "homeroom": 0},
    {"full_name": "Ahmed Hassan", "email": "ahmed@souly.local",
     "title": "English Teacher",  "subject": "ENG",  "initials": "AH",
     "color": "#0EA5E9", "homeroom": 0},
    {"full_name": "Lina Saad",    "email": "lina@souly.local",
     "title": "Arabic Teacher",   "subject": "AR",   "initials": "LS",
     "color": "#F59E0B", "homeroom": 0},
]

# Notes are written against the child's real support profile, because a parent
# reading a note that does not match the child in front of them is the fastest
# way to lose their trust in the whole hub.
NOTES = {
    "stu-01": [  # Beshoy — ADHD
        ("Sarah Ahmed",  "MATH", "praise",
         "Beshoy worked through the whole decimals page today without asking "
         "for a break. Two weeks ago that was three sittings. Worth telling him "
         "you noticed."),
        ("Noha Khaled",  "SCI",  "progress",
         "He follows the experiment well when the steps are read one at a time. "
         "When he reads all four at once he loses the thread by step three. "
         "We are chunking them for now."),
        ("Sarah Ahmed",  "MATH", "concern",
         "Attention drops sharply in the last ten minutes of the session. Could "
         "we try moving his practice at home earlier in the evening and see "
         "whether that changes anything?"),
    ],
    "stu-02": [  # Atef — grade 4
        ("Ahmed Hassan", "ENG",  "praise",
         "Atef read aloud to the class for the first time this week. He chose "
         "to. Please make a fuss of him about it."),
        ("Sarah Ahmed",  "MATH", "progress",
         "Multiplication facts up to 6 are solid. Sevens and eights still need "
         "counting on fingers, which is normal at this stage."),
    ],
    "stu-03": [  # Aziz — grade 6, no accommodations, has not started yet
        ("Sarah Ahmed",  "MATH", "progress",
         "Aziz has his profile set up but has not started a lesson yet. Nothing "
         "to worry about — I will settle him into the first one this week."),
    ],
    "stu-06": [  # Lo2lo2 — visual impairment
        ("Noha Khaled",  "SCI",  "praise",
         "Lo2lo2 answered every question on the plant-parts page correctly using "
         "the read-aloud. Her listening comprehension is genuinely ahead of the "
         "class average."),
        ("Sarah Ahmed",  "MATH", "progress",
         "High contrast is on and the larger buttons are helping. She still "
         "needs about a third more time on anything with a diagram, which we "
         "are giving her."),
        ("Lina Saad",    "AR",   "concern",
         "The Arabic worksheets we print are too small for her. I am moving her "
         "to the tablet version — you may see her working differently at home."),
    ],
}

# One opening exchange per child, so Messages is not an empty room on first
# open. The parent side is fully wired; these are the teacher's half, which
# the teacher interface will write for real later.
CONVERSATIONS = {
    "stu-01": ("Sarah Ahmed", [
        ("teacher", "Hello Fayrouz. Beshoy had a good week — he is holding "
                    "focus for longer stretches than he was in July."),
        ("parent",  "That is lovely to hear. He has been going to bed earlier, "
                    "I wonder if that is part of it."),
        ("teacher", "It may well be. Could you keep that going for another "
                    "fortnight and I will watch the sessions?"),
    ]),
    "stu-02": ("Ahmed Hassan", [
        ("teacher", "Atef volunteered to read aloud today. I wanted you to "
                    "hear it from me before he tells you himself."),
    ]),
    "stu-03": ("Sarah Ahmed", [
        ("teacher", "Hello Rancy. Aziz is all set up on Souly — I will start "
                    "him on his first lesson this week."),
    ]),
    "stu-06": ("Noha Khaled", [
        ("teacher", "Hello Karma. Lo2lo2 is doing beautifully with the "
                    "read-aloud in Science."),
        ("parent",  "Thank you. Does she need anything from us at home?"),
        ("teacher", "Only more time on the diagrams. She gets there, it just "
                    "takes her longer, and that is fine."),
    ]),
}


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--real-codes", action="store_true",
                    help="Generate random access codes instead of demo ones. "
                         "They are printed once and never recoverable.")
    args = ap.parse_args()

    init_db()
    issued = []

    with get_conn() as conn:
        subjects = {r["code"]: r["id"] for r in conn.execute("SELECT id, code FROM subjects")}

        # --- Teachers --------------------------------------------------------
        teacher_ids = {}
        for t in TEACHERS:
            existing = conn.execute(
                "SELECT id FROM teachers WHERE email = ?", (t["email"],)
            ).fetchone()
            if existing:
                tid = existing["id"]
                conn.execute(
                    "UPDATE teachers SET full_name=?, title=?, subject_id=?, "
                    "initials=?, avatar_color=?, is_homeroom=?, is_active=1 WHERE id=?",
                    (t["full_name"], t["title"], subjects.get(t["subject"]),
                     t["initials"], t["color"], t["homeroom"], tid),
                )
            else:
                cur = conn.execute(
                    "INSERT INTO teachers (full_name, email, password_hash, title, "
                    "subject_id, initials, avatar_color, is_homeroom) "
                    "VALUES (?,?,?,?,?,?,?,?)",
                    (t["full_name"], t["email"], hash_secret("teacher-not-set-yet"),
                     t["title"], subjects.get(t["subject"]), t["initials"],
                     t["color"], t["homeroom"]),
                )
                tid = cur.lastrowid
            teacher_ids[t["full_name"]] = tid

        # The placeholder rows seed_students.py left behind. Nothing points at
        # them — parent_student was empty — so they are safe to remove, and
        # leaving them means two accounts in the picker that open nothing.
        conn.execute("DELETE FROM parents WHERE email IN "
                     "('parent1@souly.local','parent2@souly.local')")
        conn.execute("DELETE FROM teachers WHERE email = 'teacher@souly.local'")

        # --- Parents and the links -------------------------------------------
        for p in PARENTS:
            code = generate_access_code() if args.real_codes else p["demo_code"]

            existing = conn.execute(
                "SELECT id FROM parents WHERE email = ?", (p["email"],)
            ).fetchone()
            if existing:
                pid = existing["id"]
                conn.execute(
                    "UPDATE parents SET full_name=?, phone=?, access_code_hash=?, "
                    "avatar_color=?, failed_logins=0, locked_until=NULL, "
                    "is_active=1 WHERE id=?",
                    (p["full_name"], p["phone"], hash_secret(code),
                     p["avatar_color"], pid),
                )
            else:
                cur = conn.execute(
                    "INSERT INTO parents (full_name, email, phone, "
                    "access_code_hash, avatar_color) VALUES (?,?,?,?,?)",
                    (p["full_name"], p["email"], p["phone"],
                     hash_secret(code), p["avatar_color"]),
                )
                pid = cur.lastrowid

            child_names = []
            for ext_id, relationship in p["children"]:
                child = conn.execute(
                    "SELECT id, display_name FROM students WHERE external_id = ?",
                    (ext_id,),
                ).fetchone()
                if child is None:
                    print(f"  !! no student '{ext_id}' — skipped")
                    continue
                conn.execute(
                    "INSERT OR REPLACE INTO parent_student "
                    "(parent_id, student_id, relationship) VALUES (?,?,?)",
                    (pid, child["id"], relationship),
                )
                child_names.append(child["display_name"])

                # --- Notes for this child ---------------------------------
                conn.execute("DELETE FROM teacher_notes WHERE student_id = ?",
                             (child["id"],))
                for teacher_name, subj, tone, body in NOTES.get(ext_id, []):
                    conn.execute(
                        "INSERT INTO teacher_notes (student_id, teacher_id, "
                        "subject_id, tone, body) VALUES (?,?,?,?,?)",
                        (child["id"], teacher_ids[teacher_name],
                         subjects.get(subj), tone, body),
                    )

                # --- One conversation for this child ------------------------
                convo = CONVERSATIONS.get(ext_id)
                if convo:
                    teacher_name, turns = convo
                    tid = teacher_ids[teacher_name]
                    conn.execute(
                        "DELETE FROM conversations WHERE parent_id=? AND student_id=?",
                        (pid, child["id"]),
                    )
                    cur = conn.execute(
                        "INSERT INTO conversations (parent_id, teacher_id, student_id) "
                        "VALUES (?,?,?)",
                        (pid, tid, child["id"]),
                    )
                    cid = cur.lastrowid
                    for role, body in turns:
                        conn.execute(
                            "INSERT INTO conversation_messages (conversation_id, "
                            "sender_role, sender_id, body) VALUES (?,?,?,?)",
                            (cid, role, pid if role == "parent" else tid, body),
                        )
                    conn.execute(
                        "UPDATE conversations SET last_message_at = "
                        "(SELECT MAX(created_at) FROM conversation_messages "
                        " WHERE conversation_id = ?) WHERE id = ?",
                        (cid, cid),
                    )

            issued.append((p["full_name"], code, child_names))

    print()
    print("  Parents' hub seeded.  Sign in at  /parent")
    print("  " + "-" * 58)
    for name, code, children in issued:
        kids = " and ".join(children) if children else "(no children linked!)"
        print(f"  {name:<10} {code:<16} -> {kids}")
    print("  " + "-" * 58)
    if not args.real_codes:
        print("  Demo codes. Use --real-codes for generated ones.")
    else:
        print("  Written down? They are hashed — this is the only time you see them.")
    print()


if __name__ == "__main__":
    main()
