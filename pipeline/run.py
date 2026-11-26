"""Run the whole pipeline from storyboard.json, in order. Every stage is cached,
so this is also the command to re-run after editing one shot.

    python -m pipeline.run                 # validate -> narration -> keyframes -> shots -> music -> film
    python -m pipeline.run --radio         # validate -> narration -> music -> radio cut
    python -m pipeline.run --only s04      # regenerate one shot's assets, then re-assemble
"""
from __future__ import annotations

import argparse
import json
import time

from pipeline import assemble, keyframes, music, narration, shots
from pipeline.common import RUN_ID, RUNS_LOG, load_storyboard, main_guard, say


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--storyboard", default="storyboard.json")
    ap.add_argument("--radio", action="store_true")
    ap.add_argument("--only", nargs="*")
    ap.add_argument("--aspect", choices=["16x9", "9x16"], help="render one aspect only (faster iteration)")
    a = ap.parse_args()
    t0 = time.monotonic()
    sb = load_storyboard(a.storyboard)
    say("run", f"run_id {RUN_ID}: storyboard valid ({len(sb['shots'])} shots)")
    narration.run(sb, a.only)
    if not a.radio:
        keyframes.run(sb, a.only)
        shots.build(sb, a.only)
    music.build(sb)
    if a.radio:
        assemble.render(sb, "radio", "16x9")
    else:
        for asp in ([a.aspect] if a.aspect else ["16x9", "9x16"]):
            assemble.render(sb, "film", asp)
    calls = []
    if RUNS_LOG.exists():
        calls = [json.loads(l) for l in RUNS_LOG.read_text(encoding="utf-8").splitlines() if l.strip()]
        calls = [c for c in calls if c.get("run_id") == RUN_ID]
    summary = ", ".join(f"{c['stage']}:{c['item']}" for c in calls) or "none (everything was cached)"
    say("run", f"done in {time.monotonic() - t0:.0f}s. Model calls this run: {len(calls)} -> {summary}")
    return 0


if __name__ == "__main__":
    main_guard(main)
