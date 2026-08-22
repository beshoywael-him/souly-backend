#!/usr/bin/env python3
"""
The bare polling client from the roadmap: fetch pending flags and print them.

Stands in for the teacher dashboard until Phase 3 replaces polling with a
WebSocket push. Deliberately dumb — its job is to prove the flag is fetchable,
nothing more.

    python scripts/poll_pending.py                      # print once
    python scripts/poll_pending.py --watch              # poll every 3s
    python scripts/poll_pending.py --student stu-01     # one student
"""

import argparse
import sys
import time
from pathlib import Path

import httpx

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

DEFAULT_API = "http://localhost:8000"


def fetch_pending(api_base: str, student_external_id: str | None = None) -> list[dict]:
    params = {}
    if student_external_id:
        params["student_external_id"] = student_external_id
    r = httpx.get(f"{api_base}/flags/pending", params=params, timeout=5.0)
    r.raise_for_status()
    return r.json()


def render(flags: list[dict]) -> None:
    if not flags:
        print("  (no pending flags)")
        return

    header = (
        f"  {'ID':<5} {'STUDENT':<12} {'TYPE':<22} "
        f"{'CONF':<6} {'DRIFT':<8} {'LAG':<8} DETECTED"
    )
    print(header)
    print("  " + "-" * (len(header) - 2))

    for f in flags:
        conf = f.get("confidence")
        conf_s = f"{conf:.2f}" if conf is not None else "-"
        drift = f.get("duration_ms")
        drift_s = f"{drift}ms" if drift is not None else "-"
        lag = f.get("pipeline_lag_ms")
        lag_s = f"{lag}ms" if lag is not None else "-"
        print(
            f"  {f['id']:<5} {(f.get('student_name') or '?'):<12} "
            f"{f['flag_type']:<22} {conf_s:<6} {drift_s:<8} {lag_s:<8} "
            f"{f['detected_at']}"
        )


def main() -> int:
    parser = argparse.ArgumentParser(description="Poll pending flags.")
    parser.add_argument("--api", default=DEFAULT_API)
    parser.add_argument("--student", help="Filter by student external_id.")
    parser.add_argument("--watch", action="store_true", help="Poll continuously.")
    parser.add_argument("--interval", type=float, default=3.0)
    args = parser.parse_args()

    try:
        while True:
            try:
                flags = fetch_pending(args.api, args.student)
            except httpx.ConnectError:
                print(f"! Cannot reach {args.api}. Is the server running? (./run.sh)")
                return 1

            if args.watch:
                # Clear screen so the queue reads as live rather than scrolling.
                print("\033[2J\033[H", end="")
            print(f"Pending flags @ {args.api}  ({len(flags)})\n")
            render(flags)

            if not args.watch:
                break
            time.sleep(args.interval)

    except KeyboardInterrupt:
        print("\nStopped.")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
