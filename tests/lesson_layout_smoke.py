#!/usr/bin/env python3
"""
Measure the lesson screen. Not "does it render" — does it render in a shape a
struggling child can use.

The layout this replaced stacked a 460px picture, a 460px step list and a
full-width block of text: three widths, three alignments, and tall enough that
the child had to zoom the browser out to see any of it. Screenshots prove a
page draws; only measuring the boxes proves the geometry is right, and the
geometry is the part that was wrong.

Five assertions, all from the child's side of the screen:

  1. the picture sits ABOVE both columns, not beside or between them
  2. the two columns are side by side, not stacked
  3. they are the same width — mismatched widths are what made it read as
     three unrelated things
  4. their left and right edges line up with the picture's
  5. nothing overflows its pane sideways

    python tests/lesson_layout_smoke.py --url http://localhost:8000 \
        --token <token> --student stu-02 --topic 10 --page 1

Any console error fails the run.
"""

import argparse
import sys
from pathlib import Path

from playwright.sync_api import sync_playwright

# Landscape tablet — the device the app is built for.
VIEWPORT = {"width": 1180, "height": 820}

IGNORE_PATTERNS = (
    "favicon",
    "Failed to load resource: the server responded with a status of 404 (Not Found)",
    # A page whose picture has not been generated yet answers 503, the client
    # removes the frame, and the lesson carries on — that is the designed
    # behaviour with no image quota, not a fault. The layout assertions below
    # still run, because the caption keeps the band in place.
    "503 (Service Unavailable)",
)

# Boxes are measured in CSS pixels and sub-pixel rounding is normal, so
# "equal" means equal to within a pixel, not bit-identical.
SLOP = 1.5


def run(base_url: str, token: str, student: str, topic: int, page_no: int,
        out_dir: Path | None) -> int:
    errors: list[str] = []
    if out_dir:
        out_dir.mkdir(parents=True, exist_ok=True)

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

        page.goto(f"{base_url}/student", wait_until="domcontentloaded")
        page.evaluate(
            """([t, s]) => {
                 localStorage.setItem('souly.token', t);
                 localStorage.setItem('souly.student', s);
               }""",
            [token, student],
        )
        # Set the hash and RELOAD. goto() to the same URL with a different
        # fragment does not reload the document, so the app would never start
        # up holding the session that was just written.
        page.evaluate("h => { location.hash = h; }", f"#lesson/{topic}/{page_no}")
        page.reload(wait_until="networkidle")
        page.wait_for_selector(".lesson-stage", timeout=15000)
        page.wait_for_timeout(1200)          # let the picture land

        def box(sel: str):
            b = page.locator(sel).first.bounding_box()
            if b is None:
                raise AssertionError(f"{sel} is not on the page")
            return b

        pic = box(".lesson-picture")

        # A wide diagram — the place-value chart with a thousandths column —
        # is laid out full width on purpose, because half a pane clips the
        # very digit the page is about. Check that shape on its own terms
        # rather than failing it for not being two columns.
        wide = page.locator(".lesson-stage > .lesson-col-diagram")
        if wide.count():
            w = wide.first.bounding_box()
            assert abs(w["x"] - pic["x"]) <= SLOP and \
                   abs(w["width"] - pic["width"]) <= SLOP, \
                "The full-width diagram does not line up with the picture"
            clipped = page.evaluate(
                """() => {
                     const t = document.querySelector('.pv-scroll');
                     return t ? t.scrollWidth - t.clientWidth : 0;
                   }"""
            )
            assert clipped <= 2, \
                f"The chart is clipped by {clipped}px — a digit is hidden"
            print("  ✅ wide chart laid out full width, nothing clipped")
            if out_dir:
                page.screenshot(path=str(out_dir / "lesson-layout-wide.png"))
            browser.close()
            return 0 if not errors else 1

        cols = page.locator(".lesson-duo > .lesson-col")
        n = cols.count()
        assert n == 2, f"Expected two columns, found {n}"
        left = cols.nth(0).bounding_box()
        right = cols.nth(1).bounding_box()
        pane = box(".pane-content")

        print(f"  picture  x={pic['x']:.0f} w={pic['width']:.0f} "
              f"h={pic['height']:.0f}")
        print(f"  left     x={left['x']:.0f} w={left['width']:.0f} "
              f"h={left['height']:.0f}")
        print(f"  right    x={right['x']:.0f} w={right['width']:.0f} "
              f"h={right['height']:.0f}")

        # 1. The picture is above both columns. The child looks at it first,
        #    which only works if it is first.
        assert pic["y"] + pic["height"] <= left["y"] + SLOP, \
            "The picture is not above the left column"
        assert pic["y"] + pic["height"] <= right["y"] + SLOP, \
            "The picture is not above the right column"

        # 2. Side by side, on the same line.
        assert right["x"] > left["x"] + left["width"] - SLOP, \
            "The columns are stacked, not side by side"
        assert abs(left["y"] - right["y"]) <= SLOP, \
            "The columns do not start at the same height"

        # 3. Equal width. Two different widths read as two unrelated things.
        assert abs(left["width"] - right["width"]) <= SLOP, \
            (f"Columns differ in width: {left['width']:.1f} vs "
             f"{right['width']:.1f}")

        # 4. One shared grid: the picture starts where the left column starts
        #    and ends where the right column ends.
        assert abs(pic["x"] - left["x"]) <= SLOP, \
            "The picture and the left column do not share a left edge"
        assert abs((pic["x"] + pic["width"])
                   - (right["x"] + right["width"])) <= SLOP, \
            "The picture and the right column do not share a right edge"

        # 5. Nothing sticks out sideways. Horizontal scrolling on a tablet is
        #    how a child loses half the lesson without knowing it is there.
        overflow = page.evaluate(
            """() => {
                 const p = document.querySelector('.pane-content');
                 return p.scrollWidth - p.clientWidth;
               }"""
        )
        assert overflow <= 2, f"The content pane scrolls sideways by {overflow}px"

        print("  ✅ picture on top, two equal columns, one shared grid")

        # And the point of all of it: the whole lesson visible without
        # scrolling on the device it is built for.
        scroll = page.evaluate(
            """() => {
                 const p = document.querySelector('.pane-content');
                 return p.scrollHeight - p.clientHeight;
               }"""
        )
        print(f"  vertical overflow in the content pane: {scroll:.0f}px")

        if out_dir:
            page.screenshot(path=str(out_dir / "lesson-layout.png"))
            print(f"  📸 {out_dir / 'lesson-layout.png'}")

        # --- The narrow case --------------------------------------------------
        # A smaller tablet cannot hold two readable columns, and two columns
        # four words wide would be worse than the stack it replaced. Below the
        # threshold it must fall back cleanly rather than crush them.
        print("narrow screen…")
        page.set_viewport_size({"width": 860, "height": 700})
        page.wait_for_timeout(600)
        n_left = page.locator(".lesson-duo > .lesson-col").nth(0).bounding_box()
        n_right = page.locator(".lesson-duo > .lesson-col").nth(1).bounding_box()
        stacked = n_right["y"] > n_left["y"] + n_left["height"] - SLOP
        side_by_side = n_right["x"] > n_left["x"] + n_left["width"] - SLOP
        assert stacked or side_by_side, \
            "On a narrow screen the columns neither stack nor sit side by side"
        print(f"  columns {'stack' if stacked else 'stay side by side'} "
              f"at 860px — width {n_left['width']:.0f}px")
        assert n_left["width"] >= 240, \
            f"Columns crushed to {n_left['width']:.0f}px — unreadable"

        overflow = page.evaluate(
            """() => {
                 const p = document.querySelector('.pane-content');
                 return p.scrollWidth - p.clientWidth;
               }"""
        )
        assert overflow <= 2, \
            f"The content pane scrolls sideways by {overflow}px on a narrow screen"
        print("  ✅ degrades cleanly, nothing crushed, no sideways scroll")
        if out_dir:
            page.screenshot(path=str(out_dir / "lesson-layout-narrow.png"))

        browser.close()

    if errors:
        print("\n❌ console errors:")
        for e in errors[:20]:
            print("   ", e)
        return 1
    print("\n✅ lesson layout smoke passed")
    return 0


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--url", default="http://localhost:8000")
    ap.add_argument("--token", required=True)
    ap.add_argument("--student", default="stu-02")
    ap.add_argument("--topic", type=int, default=10)
    ap.add_argument("--page", type=int, default=12)   # the book page number, not an ordinal
    ap.add_argument("--out", default="")
    a = ap.parse_args()
    return run(a.url, a.token, a.student, a.topic, a.page,
               Path(a.out) if a.out else None)


if __name__ == "__main__":
    sys.exit(main())
