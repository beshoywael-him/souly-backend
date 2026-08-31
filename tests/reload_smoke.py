#!/usr/bin/env python3
"""
The returning child: what a reload does.

This exists because of a real regression. Profile stopped being a tab and
became the child's own face in the top-right corner — which makes that button
the *only* door into Profile. But the code that shows it, App.afterLogin(),
only ran on the fresh sign-in path. Gate.resume() — the path every returning
child takes, and the path a page refresh takes — hid the sign-in screen and
returned early. So the normal case was: no avatar, no way to reach Profile,
and always dumped back at Home no matter where the child had been reading.

Two assertions, both from the child's point of view after a reload:

  1. the avatar button is visible, because it is the way to Profile
  2. the saved #lesson/{topic}/{page} route reopens on that page

    python tests/reload_smoke.py --url http://localhost:8000 \
        --token <token> --student stu-02

Any console error fails the run.
"""

import argparse
import sys
from pathlib import Path

from playwright.sync_api import sync_playwright

VIEWPORT = {"width": 1180, "height": 820}

IGNORE_PATTERNS = (
    "favicon",
    "Failed to load resource: the server responded with a status of 404 (Not Found)",
)


def run(base_url: str, token: str, student: str, out_dir: Path | None) -> int:
    errors: list[str] = []
    if out_dir:
        out_dir.mkdir(parents=True, exist_ok=True)

    with sync_playwright() as pw:
        browser = pw.chromium.launch(args=["--no-sandbox"])
        context = browser.new_context(viewport=VIEWPORT)
        page = context.new_page()

        page.on("console", lambda m: (
            errors.append(f"console.{m.type}: {m.text}")
            if m.type == "error" and not any(p in m.text for p in IGNORE_PATTERNS)
            else None
        ))
        page.on("pageerror", lambda e: errors.append(f"pageerror: {e}"))

        def shot(name: str) -> None:
            if out_dir:
                page.screenshot(path=str(out_dir / f"{name}.png"))

        # Put a valid session on the tablet the way signing in would, then
        # reload into it. This is the returning child, not a first sign-in.
        page.goto(f"{base_url}/student", wait_until="domcontentloaded")
        page.evaluate(
            """([t, s]) => {
                 localStorage.setItem('souly.token', t);
                 localStorage.setItem('souly.student', s);
               }""",
            [token, student],
        )

        # --- 1. The door to Profile -----------------------------------------
        print("reload with a live session…")
        page.goto(f"{base_url}/student", wait_until="networkidle")
        page.wait_for_selector("#gate", state="hidden", timeout=10000)
        page.wait_for_selector("#avatarBtn:not([hidden])", timeout=10000)
        print("  ✅ avatar button visible")
        shot("01-resumed")

        page.click("#avatarBtn")
        page.wait_for_timeout(600)
        assert page.locator("#page-profile.active").count() == 1, \
            "The avatar did not open Profile"
        print("  ✅ it opens Profile")
        shot("02-profile")

        # --- 2. Coming back to the page they were reading --------------------
        # Find a lesson with pages, park on page 2 of it, reload, and expect to
        # land back there. Starting over from Home every refresh is the thing
        # the child notices.
        lessons = page.evaluate(
            """async () => {
                 const s = localStorage.getItem('souly.student');
                 const r = await fetch(`/api/students/${s}/plan`,
                   {headers: {Authorization: 'Bearer ' +
                     localStorage.getItem('souly.token')}});
                 if (!r.ok) return [];
                 const j = await r.json();
                 return (j.lessons || j.plan || j.items || []).slice(0, 60);
               }"""
        )
        topic_id = None
        for t in lessons:
            if not isinstance(t, dict):
                continue
            tid = t.get("topic_id") or t.get("id")
            if tid and (t.get("page_count") or t.get("pages") or 0):
                topic_id = tid
                break
        if topic_id is None and lessons:
            topic_id = lessons[0].get("topic_id") or lessons[0].get("id")

        if topic_id is None:
            print("  ⚠️  no topics seeded — route restore not exercised")
        else:
            # Set the hash, then reload for real. goto() to the same URL with
            # a different fragment does not reload the document, and the whole
            # point of this check is what a refresh does.
            page.evaluate("h => { location.hash = h; }",
                          f"#lesson/{topic_id}/2")
            page.reload(wait_until="networkidle")
            page.wait_for_selector("#gate", state="hidden", timeout=10000)
            page.wait_for_timeout(1500)
            active = page.evaluate(
                "() => document.querySelector('.page.active')?.id || ''")
            assert active == "page-lesson", \
                f"Reload landed on {active!r}, not back in the lesson"
            print(f"  ✅ reload returns to the lesson (topic {topic_id})")
            shot("03-route-restored")

        browser.close()

    if errors:
        print("\n❌ console errors:")
        for e in errors[:20]:
            print("   ", e)
        return 1
    print("\n✅ reload smoke passed")
    return 0


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--url", default="http://localhost:8000")
    ap.add_argument("--token", required=True)
    ap.add_argument("--student", default="stu-02")
    ap.add_argument("--out", default="")
    a = ap.parse_args()
    return run(a.url, a.token, a.student, Path(a.out) if a.out else None)


if __name__ == "__main__":
    sys.exit(main())
