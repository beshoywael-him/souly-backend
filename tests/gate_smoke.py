#!/usr/bin/env python3
"""
Drive sign-in and the entry activity in a real browser, and screenshot it.

The unit tests prove the endpoints behave. This proves a child can actually
get from a cold tablet to the home screen: pick a face, choose three pictures,
confirm them, be greeted by name, walk the activity, and land in the app.

    python tests/gate_smoke.py --url http://localhost:8000 --out /tmp/gate

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

PASSWORD = ["cat", "rocket", "star"]


def run(base_url: str, out_dir: Path) -> int:
    out_dir.mkdir(parents=True, exist_ok=True)
    errors: list[str] = []
    n = [0]

    with sync_playwright() as pw:
        browser = pw.chromium.launch(args=["--no-sandbox"])
        context = browser.new_context(viewport=VIEWPORT, device_scale_factor=2)
        page = context.new_page()

        page.on("console", lambda m: (
            errors.append(f"console.{m.type}: {m.text}")
            if m.type == "error" and not any(p in m.text for p in IGNORE_PATTERNS)
            else None
        ))
        page.on("pageerror", lambda e: errors.append(f"pageerror: {e}"))

        def shot(name: str) -> None:
            n[0] += 1
            page.screenshot(path=str(out_dir / f"{n[0]:02d}-{name}.png"))
            print(f"  📸 {n[0]:02d}-{name}")

        def pick_pictures() -> None:
            for code in PASSWORD:
                page.click(f'.pic-btn[data-code="{code}"]')
                page.wait_for_timeout(150)

        # --- The picker -------------------------------------------------------
        print(f"Opening {base_url}/student")
        page.goto(f"{base_url}/student", wait_until="networkidle")
        page.wait_for_selector(".profile-tile", timeout=10000)

        tiles = page.locator(".profile-tile")
        count = tiles.count()
        print(f"  profiles on the tile wall: {count}")
        assert count >= 2, "Expected the whole class on the picker"
        assert "Righty" not in page.content(), "Found 'Righty' on the sign-in screen"
        shot("picker")

        # The child we drive is whoever is first and still needs a password;
        # otherwise the first tile, and we log in instead of enrolling.
        target = None
        for i in range(count):
            tile = tiles.nth(i)
            if tile.locator(".profile-flag", has_text="Tap to set up").count():
                target = tile
                break
        enrolling = target is not None
        if target is None:
            target = tiles.nth(0)
        name = target.locator(".profile-name").inner_text().strip()
        print(f"  driving: {name}  ({'first time' if enrolling else 'returning'})")

        target.click()
        # Caught mid-transition on purpose: the password screen replaces this
        # DOM after 620ms, so the fade only exists in that window.
        page.wait_for_timeout(300)

        # Netflix behaviour: the others recede rather than vanish outright, so
        # the child can still see they picked the right face.
        assert page.locator(".profile-tile.chosen").count() == 1, \
            "Chosen tile not marked"
        assert page.locator("#profileRow.choosing").count() == 1, \
            "The other faces aren't receding"
        shot("chosen")

        # --- Picture password -------------------------------------------------
        page.wait_for_selector(".pic-btn", timeout=10000)
        grid = page.locator(".pic-btn").count()
        slots = page.locator(".pic-slot").count()
        print(f"  picture grid: {grid} pictures, {slots} slots")
        assert grid >= 9 and slots == 3
        shot("password-empty")

        pick_pictures()
        filled = page.locator(".pic-slot").evaluate_all(
            "els => els.filter(e => e.textContent.trim()).length")
        assert filled == 3, f"Only {filled} slots filled"
        shot("password-picked")

        if enrolling:
            # Confirm screen: choose the same three again.
            page.wait_for_timeout(800)
            assert "again" in page.text_content(".gate-title, .gate-hint").lower() \
                or page.locator(".pic-slot").count() == 3
            shot("password-confirm")
            pick_pictures()

        # --- Greeting ---------------------------------------------------------
        page.wait_for_selector(".gate-title", timeout=10000)
        page.wait_for_timeout(900)
        title = page.text_content(".gate-title").strip()
        print(f"  greeting: {title}")
        assert name.split()[0] in title, f"Not greeted by name: {title!r}"
        shot("greeting")

        # --- The entry activity -----------------------------------------------
        # The greeting has no button — it reads the child's name aloud and
        # advances itself, so don't click, wait.
        page.wait_for_selector(".plan-card", timeout=10000)
        cards = page.locator(".plan-card").count()
        print(f"  activity map shown up front: {cards} parts")
        assert cards >= 3, "The child must see the whole plan before starting"
        shot("activity-map")
        page.click("#gate button:has-text(\"I'm ready\")")
        page.wait_for_timeout(700)

        # Interests.
        if page.locator(".interest-btn").count():
            page.locator(".interest-btn").nth(0).click()
            page.locator(".interest-btn").nth(3).click()
            page.wait_for_timeout(200)
            shot("interests")
            page.click("#gate .gate-actions .btn-primary")
            page.wait_for_timeout(700)

        # Reasoning items. Answer the first wrong on purpose to pull a prompt
        # down — the prompt ladder is the measuring instrument, so it has to
        # render.
        saw_prompt = False
        saw_reading = False
        saw_listening = False
        saw_preference = False

        def tap(selector: str, index: int = 0) -> bool:
            """Click and tolerate the re-render that follows. Every answer
            replaces the screen, so a locator resolved a moment ago is often
            already detached — that's the app working, not a failure."""
            try:
                page.locator(selector).nth(index).click(timeout=3000)
            except Exception:
                return False
            page.wait_for_timeout(650)
            return True

        for step in range(40):
            if page.locator(".profile-summary").count():
                break

            if page.locator(".reading-passage").count():
                if not saw_reading:
                    saw_reading = True
                    shot("modality-reading")
                tap(".q-option", 0)
                continue

            if page.locator("button:has-text('Play it again')").count():
                if not saw_listening:
                    saw_listening = True
                    shot("modality-listening")
                # The passage is spoken and deliberately never shown.
                assert not page.locator(".reading-passage").count(), \
                    "The listening item printed its passage — that measures reading twice"
                tap(".q-option", 0)
                continue

            if page.locator(".pref-btn").count():
                if not saw_preference:
                    saw_preference = True
                    shot("preference")
                tap(".pref-btn", 0)
                continue

            if page.locator(".q-option").count():
                if not saw_prompt:
                    shot("reasoning-item")
                tap(".q-option", 0)
                if not saw_prompt and page.locator(".q-prompt").count():
                    saw_prompt = True
                    text = page.text_content(".q-prompt").strip()
                    print(f"  prompt rung 1: {text[:70]}…")
                    shot("prompt-rung-1")
                    # Climb one more rung, then let it resolve.
                    tap(".q-option", 1)
                    if page.locator(".q-prompt").count():
                        print("  prompt rung 2 rendered ✓")
                        shot("prompt-rung-2")
                continue

            if page.locator(".skip-link").count():
                tap(".skip-link")
                continue

            page.wait_for_timeout(500)

        assert saw_prompt, "No graduated prompt ever rendered"
        assert saw_reading and saw_listening, \
            f"Modality pair incomplete (reading={saw_reading}, listening={saw_listening})"
        assert saw_preference, "No preference question rendered"

        # --- Done -------------------------------------------------------------
        page.wait_for_selector(".profile-summary", timeout=15000)
        summary = page.text_content(".profile-summary").strip()
        print(f"  profile: {summary}")
        assert summary, "Finish screen showed no profile summary"
        shot("profile-summary")

        page.click("#gate .gate-actions .btn-primary")
        page.wait_for_timeout(1500)

        # --- Into the app -----------------------------------------------------
        page.wait_for_selector(".greeting", timeout=15000)
        greeting = page.text_content(".greeting").strip()
        print(f"  home: {greeting}")
        assert "undefined" not in greeting
        assert not page.locator("#gate.active").count(), "Gate still covering the app"
        shot("home-after-login")

        # --- Reload keeps them in ---------------------------------------------
        page.reload(wait_until="networkidle")
        page.wait_for_timeout(1500)
        assert not page.locator(".profile-tile").count(), \
            "Reload sent the child back to sign-in — the token isn't sticking"
        page.wait_for_selector(".greeting", timeout=10000)
        print("  reload: still signed in ✓")
        shot("after-reload")

        browser.close()

    print()
    if errors:
        print(f"❌ {len(errors)} console error(s):")
        for e in errors[:20]:
            print("   ", e)
        return 1
    print(f"✅ Sign-in and entry activity clean — {n[0]} screenshots in {out_dir}")
    return 0


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--url", default="http://localhost:8000")
    ap.add_argument("--out", default="/tmp/gate")
    args = ap.parse_args()
    try:
        return run(args.url.rstrip("/"), Path(args.out))
    except AssertionError as exc:
        print(f"❌ {exc}")
        return 1


if __name__ == "__main__":
    sys.exit(main())
