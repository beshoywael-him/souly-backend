#!/usr/bin/env python3
"""
Drive the real student UI in a headless browser and screenshot every screen.

Unit tests prove the API returns the right JSON. Only a browser proves the
page renders it without throwing. Any console error fails the run.

    python tests/ui_smoke.py --url http://localhost:8000 --out /tmp/shots
"""

import argparse
import sys
from pathlib import Path

from playwright.sync_api import sync_playwright

# Landscape tablet — the device the app is actually built for.
VIEWPORT = {"width": 1180, "height": 820}

IGNORE_PATTERNS = (
    "favicon",
    "Failed to load resource: the server responded with a status of 404 (Not Found)",
)


def run(base_url: str, out_dir: Path, student: str) -> int:
    out_dir.mkdir(parents=True, exist_ok=True)
    errors: list[str] = []
    shots: list[str] = []

    with sync_playwright() as pw:
        browser = pw.chromium.launch(args=["--no-sandbox"])
        context = browser.new_context(
            viewport=VIEWPORT,
            device_scale_factor=2,
            permissions=["microphone"],
        )
        page = context.new_page()

        page.on("console", lambda m: (
            errors.append(f"console.{m.type}: {m.text}")
            if m.type == "error" and not any(p in m.text for p in IGNORE_PATTERNS)
            else None
        ))
        page.on("pageerror", lambda e: errors.append(f"pageerror: {e}"))

        def shot(name: str) -> None:
            page.screenshot(path=str(out_dir / f"{name}.png"))
            shots.append(name)
            print(f"  📸 {name}")

        def dismiss() -> None:
            """Close any celebration modal before the next interaction."""
            if page.evaluate("App.overlayIsOpen()"):
                page.evaluate("App.closeOverlay()")
                page.wait_for_timeout(250)

        print(f"Opening {base_url}/student?student={student}  ({VIEWPORT['width']}x{VIEWPORT['height']} landscape)")
        page.goto(f"{base_url}/student?student={student}", wait_until="networkidle")
        page.wait_for_timeout(1200)

        # --- Home -------------------------------------------------------------
        page.wait_for_selector(".greeting", timeout=10000)
        greeting = page.text_content(".greeting")
        print(f"  greeting: {greeting.strip()}")
        assert "undefined" not in greeting, "Greeting rendered undefined"
        assert "Righty" not in page.content(), "Found 'Righty' on the home screen"

        plan = page.text_content("#planStrip")
        print(f"  plan strip: {' '.join(plan.split())}")
        assert "Lesson" in plan, "Session plan strip missing"
        shot("01-home")

        # --- Learn: subject rail + learning path -------------------------------
        dismiss()
        page.click('.rail-item[data-page="learn"]')
        page.wait_for_selector(".subject-chip", timeout=10000)
        chips = page.locator(".subject-chip").count()
        print(f"  subject chips: {chips}")
        assert chips >= 6
        shot("02-learn")

        # Open the subject that has the demo lesson.
        page.click('.subject-chip[data-code="MATH"]')
        page.wait_for_selector(".path-card", timeout=10000)
        cards = page.locator(".path-card").count()
        print(f"  path cards: {cards}")
        assert cards >= 1, "Learning path rendered no lessons"
        shot("03-learning-path")

        # --- Lesson: the two-pane screen ---------------------------------------
        dismiss()
        page.click(".path-card >> nth=0")
        page.wait_for_selector("#lessonBody", timeout=10000)
        page.wait_for_timeout(700)

        body_text = page.text_content("#lessonBody")
        print(f"  lesson step: {body_text[:60].strip()}…")
        assert len(body_text) > 40

        # The whole point of landscape: both panes visible at once.
        content_box = page.locator(".pane-content").bounding_box()
        souly_box = page.locator(".pane-souly").bounding_box()
        assert content_box and souly_box, "Two-pane layout did not render"
        assert souly_box["x"] > content_box["x"], \
            "Souly's pane is not beside the content — split-attention layout"
        print(f"  layout: content at x={int(content_box['x'])}, "
              f"Souly at x={int(souly_box['x'])} — side by side ✓")

        assert page.locator(".help-btn").count() == 3, "Expected 3 help buttons"
        shot("04-lesson")

        # --- Help: "I don't get this" ------------------------------------------
        before_help = page.text_content("#soulySay")
        page.click(".help-btn >> nth=0")
        page.wait_for_timeout(2500)
        after_help = page.text_content("#soulySay")
        print(f"  help reply: {after_help[:70].strip()}…")
        assert after_help != before_help, "Help button produced no response"
        assert len(after_help) > 20
        shot("05-lesson-help")

        # The content must NOT have moved. That's the split-attention fix.
        assert page.locator("#lessonBody").is_visible(), \
            "Lesson content disappeared when help was requested"
        print("  lesson content still on screen while Souly answers ✓")

        # --- Advance through the lesson into practice ---------------------------
        steps = page.locator(".step-dot").count()
        print(f"  stepping through {steps} steps…")
        for i in range(steps):
            dismiss()
            if page.locator("text=Finish").count():
                page.click("text=Finish")
                break
            page.click("text=Next")
            page.wait_for_timeout(1100)

        page.wait_for_timeout(3000)
        dismiss()

        # --- Practice with the hint ladder --------------------------------------
        page.wait_for_selector("#practiceOptions .quiz-option", timeout=20000)
        prompt = page.text_content("#practicePrompt")
        origin = page.text_content(".practice-origin")
        print(f"  practice question: {prompt[:60].strip()}…")
        print(f"  origin: {' '.join(origin.split())}")
        shot("06-practice")

        # Ask for a clue before answering.
        page.click("#hintMore")
        page.wait_for_selector(".hint-step", timeout=20000)
        hint_text = page.text_content(".hint-step")
        print(f"  tier 1 hint: {hint_text[:70].strip()}…")
        assert len(hint_text) > 10
        shot("07-hint-tier-1")

        # Climb the ladder.
        for tier in (2, 3):
            page.click("#hintMore")
            page.wait_for_timeout(2200)
        rungs = page.locator(".hint-step").count()
        print(f"  hint ladder rungs shown: {rungs}")
        assert rungs >= 3, "Hint ladder did not climb"
        shot("08-hint-ladder")

        # Answer it.
        page.click("#practiceOptions .quiz-option >> nth=0")
        page.wait_for_selector("#practiceFeedback .glass-card", timeout=10000)
        feedback = page.text_content("#practiceFeedback")
        print(f"  feedback: {' '.join(feedback.split())[:70]}…")
        shot("09-practice-answered")

        # --- Progress ------------------------------------------------------------
        dismiss()
        page.click('.rail-item[data-page="progress"]')
        page.wait_for_selector(".week-chart", timeout=10000)
        shot("10-progress")

        # --- Profile + accessibility ---------------------------------------------
        dismiss()
        page.click('.rail-item[data-page="profile"]')
        page.wait_for_selector(".setting-item", timeout=10000)
        shot("11-profile")

        page.evaluate("App.setSetting('font_size','large')")
        page.wait_for_timeout(700)
        page.evaluate("App.setSetting('larger_buttons', true)")
        page.wait_for_timeout(700)
        page.evaluate("App.setSetting('high_contrast', true)")
        page.wait_for_timeout(1000)
        shot("12-high-contrast")

        saved = page.evaluate(
            f"fetch('/api/students/{student}/settings').then(r=>r.json())")
        assert saved["high_contrast"] in (1, True), "high_contrast did not persist"
        assert saved["font_size"] == "large", "font_size did not persist"
        print("  accessibility settings persisted server-side ✓")

        page.evaluate("App.setSetting('high_contrast', false)")
        page.evaluate("App.setSetting('font_size','medium')")
        page.evaluate("App.setSetting('larger_buttons', false)")
        page.wait_for_timeout(800)

        # High contrast on the lesson screen is the one that matters most.
        page.evaluate("App.setSetting('high_contrast', true)")
        page.wait_for_timeout(600)
        page.evaluate("App.go('learn')")
        page.wait_for_selector(".subject-chip", timeout=10000)
        page.click('.subject-chip[data-code="MATH"]')
        page.wait_for_selector(".path-card", timeout=10000)
        page.click(".path-card >> nth=0")
        page.wait_for_selector("#lessonBody", timeout=10000)
        page.wait_for_timeout(900)
        shot("13-lesson-high-contrast")
        page.evaluate("App.setSetting('high_contrast', false)")
        page.wait_for_timeout(600)

        # --- Dark theme -----------------------------------------------------------
        page.evaluate("App.setSetting('theme','dark')")
        page.wait_for_timeout(1000)
        shot("14-lesson-dark")
        page.evaluate("App.setSetting('theme','light')")
        page.wait_for_timeout(500)

        # --- No stray old branding -------------------------------------------------
        for name, route in (("home", "home"), ("learn", "learn"), ("profile", "profile")):
            page.evaluate(f"App.go('{route}')")
            page.wait_for_timeout(900)
            assert "Righty" not in page.content(), f"'Righty' still on the {name} screen"
        print("  no 'Righty' anywhere in the UI ✓")

        browser.close()

    print(f"\n{len(shots)} screenshots -> {out_dir}")
    if errors:
        print(f"\n❌ {len(errors)} console error(s):")
        for e in errors[:25]:
            print("   " + e)
        return 1

    print("✅ No console errors. Landscape flow verified end to end.")
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description="Screenshot-test the student UI.")
    parser.add_argument("--url", default="http://localhost:8000")
    parser.add_argument("--out", default="/tmp/souly-shots")
    parser.add_argument("--student", default="stu-01")
    args = parser.parse_args()

    try:
        return run(args.url, Path(args.out), args.student)
    except AssertionError as exc:
        print(f"\n❌ Assertion failed: {exc}")
        return 1


if __name__ == "__main__":
    sys.exit(main())
