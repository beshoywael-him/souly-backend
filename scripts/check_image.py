#!/usr/bin/env python3
"""
Ask Gemini for one picture and print exactly what comes back.

    python scripts/check_image.py
    python scripts/check_image.py "a bean seed sprouting in a cup of soil"
    python scripts/check_image.py --raw          # dump the whole JSON response

Exists because the lesson screen can only ever tell you "no picture", and the
reason could be the key, the model name, the account's access to image models,
the request shape, or a stale cache. This prints the actual answer instead of
guessing, and writes the image to disk if one arrives so you can look at it.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from app.config import settings          # noqa: E402
from app.services import curriculum, llm  # noqa: E402

DEFAULT_SCENE = "a bean seed sprouting in a cup of soil"


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("scene", nargs="?", default=DEFAULT_SCENE)
    ap.add_argument("--raw", action="store_true",
                    help="print the whole HTTP response, not just the verdict")
    args = ap.parse_args()

    key = settings.image_key
    print(f"provider   {settings.image_generator_provider}")
    print(f"image key  {'set (' + key[:6] + '…)' if key else 'MISSING'}"
          + ("  [same as GEMINI_API_KEY]"
             if key and not settings.image_generator_api_key else ""))
    print(f"model      {settings.image_generator_model}")
    print(f"aspect     {llm.aspect_ratio()}")
    print(f"fallbacks  {', '.join(llm.image_models()[1:])}")
    print(f"text model {settings.gemini_model}")
    print(f"scene      {args.scene!r}")
    print()

    if args.raw:
        import httpx

        model = settings.image_generator_model
        url = llm._image_endpoint(model)
        prompt = f"{args.scene}.\n\n{llm.ILLUSTRATION_STYLE}"
        payload = llm._image_payloads(model, prompt)[0]
        print(f"POST {url}")
        with httpx.Client(timeout=90) as client:
            response = client.post(url, params={"key": settings.image_key},
                                   json=payload)
        print(f"HTTP {response.status_code}")
        try:
            body = response.json()
        except ValueError:
            print(response.text[:4000])
            return 1
        # Truncate the base64 so the output stays readable.
        for candidate in body.get("candidates", []):
            for part in candidate.get("content", {}).get("parts", []):
                inline = part.get("inlineData") or part.get("inline_data")
                if inline and inline.get("data"):
                    inline["data"] = f"<{len(inline['data'])} chars of base64>"
        for prediction in body.get("predictions", []):
            if prediction.get("bytesBase64Encoded"):
                prediction["bytesBase64Encoded"] = (
                    f"<{len(prediction['bytesBase64Encoded'])} chars of base64>")
        print(json.dumps(body, indent=2)[:4000])
        return 0 if response.status_code == 200 else 1

    image, mime, error = llm.generate_image(args.scene)

    if not image:
        print(f"NO IMAGE: {error}")
        print()
        print("What that usually means:")
        print("  429 / quota exceeded     the account has no image allowance.")
        print("                           This is NOT a bug in the app. Lessons")
        print("                           fall back to the drawn diagram, which")
        print("                           needs no quota and works offline.")
        print("                           To turn pictures on, enable billing on")
        print("                           the Google AI Studio project for this")
        print("                           key — Nano Banana is a few cents an")
        print("                           image and every page is drawn once and")
        print("                           shared by every child.")
        print("  403 / PERMISSION_DENIED  the key cannot reach image models")
        print("  404 / NOT_FOUND          wrong model name for this account —")
        print("                           check `curl .../v1beta/models` for one")
        print("                           whose name contains 'image'")
        print("  no image returned        the model replied with words instead;")
        print("                           run again with --raw to see them")
        return 1

    out = curriculum.illustration_dir() / "_check.png"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_bytes(image)
    print(f"OK — {len(image):,} bytes, {mime}")
    print(f"written to {out}")
    print("\nOpen it. If it has words or numbers drawn into it, the style rule")
    print("is not holding and that needs fixing before a child sees one.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
