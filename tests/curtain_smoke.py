#!/usr/bin/env python3
"""
The curtain: no words, and no voice, until the picture is on screen.

For a child who reads with difficulty the picture is the explanation and the
sentences are the support. If the words arrive first, they learn to skip the
part that was built for them — and a child who has already read the text has
no reason to look up when the picture finally lands. So the lesson is held
back until the picture is there.

Held back is not the same as absent. Three things have to be true, and the
last one is the one that matters most:

  1. while the picture is loading, the words are not visible and nothing is
     being read aloud
  2. when it arrives, they appear
  3. when it FAILS, they appear anyway — a child must never be stranded in
     front of a blank screen because an image service is down

    python tests/curtain_smoke.py --url http://localhost:8000 \
        --token <token> --student stu-02 --topic 10 --page 12
"""

import argparse
import sys

from playwright.sync_api import sync_playwright

VIEWPORT = {"width": 1180, "height": 820}


def run(base_url: str, token: str, student: str, topic: int, page_no: int) -> int:
    failures: list[str] = []

    with sync_playwright() as pw:
        browser = pw.chromium.launch(args=["--no-sandbox"])
        context = browser.new_context(viewport=VIEWPORT)

        def open_lesson(page, hold_ms: int = 0, fail: bool = False):
            """Open the page with the illustration request delayed or broken."""
            def handler(route):
                if fail:
                    route.fulfill(status=503, body="no")
                    return
                if hold_ms:
                    page.wait_for_timeout(hold_ms)
                route.continue_()

            page.route("**/illustration*", handler)
            page.goto(f"{base_url}/student", wait_until="domcontentloaded")
            page.evaluate(
                """([t, s]) => {
                     localStorage.setItem('souly.token', t);
                     localStorage.setItem('souly.student', s);
                   }""", [token, student])
            page.evaluate("h => { location.hash = h; }",
                          f"#lesson/{topic}/{page_no}")
            page.reload(wait_until="domcontentloaded")

        def words_visible(page) -> bool:
            return page.evaluate(
                """() => {
                     const b = document.querySelector('.lesson-body');
                     if (!b) return false;
                     const s = getComputedStyle(b);
                     return s.visibility !== 'hidden' && Number(s.opacity) > 0.1
                            && b.getBoundingClientRect().height > 0;
                   }""")

        # --- 1 & 2: held while drawing, shown when drawn ---------------------
        print("slow picture…")
        page = context.new_page()
        spoken: list[str] = []
        page.expose_function("__spoke", lambda t: spoken.append(t))
        page.add_init_script(
            """window.addEventListener('DOMContentLoaded', () => {
                 const s = window.speechSynthesis;
                 if (s) { const f = s.speak.bind(s);
                          s.speak = u => { window.__spoke?.(u.text || ''); return f(u); }; }
               });""")
        open_lesson(page, hold_ms=2500)

        page.wait_for_selector(".lesson-stage", timeout=15000)
        page.wait_for_timeout(700)                 # picture still in flight

        if words_visible(page):
            failures.append("The words were on screen before the picture")
        else:
            print("  ✅ words held back while the picture is being drawn")
        if spoken:
            failures.append(f"Read aloud started before the picture: {spoken!r}")
        else:
            print("  ✅ nothing read aloud yet")

        page.wait_for_selector(".lesson-stage:not(.curtained)", timeout=20000)
        page.wait_for_timeout(500)
        if not words_visible(page):
            failures.append("The words never appeared after the picture loaded")
        else:
            print("  ✅ words appear once the picture is there")
        page.close()

        # --- 3: the picture fails, the lesson does not -----------------------
        print("picture fails…")
        page = context.new_page()
        open_lesson(page, fail=True)
        page.wait_for_selector(".lesson-stage", timeout=15000)
        try:
            page.wait_for_selector(".lesson-stage:not(.curtained)", timeout=8000)
            visible = words_visible(page)
        except Exception:
            visible = False
        if not visible:
            failures.append(
                "The curtain stayed shut when the picture failed — a child "
                "would be stranded in front of a blank screen")
        else:
            print("  ✅ lesson opens anyway when no picture can be drawn")
        page.close()

        browser.close()

    if failures:
        print("\n❌")
        for f in failures:
            print("   ", f)
        return 1
    print("\n✅ curtain smoke passed")
    return 0


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--url", default="http://localhost:8000")
    ap.add_argument("--token", required=True)
    ap.add_argument("--student", default="stu-02")
    ap.add_argument("--topic", type=int, default=10)
    ap.add_argument("--page", type=int, default=12)
    a = ap.parse_args()
    return run(a.url, a.token, a.student, a.topic, a.page)


if __name__ == "__main__":
    sys.exit(main())
