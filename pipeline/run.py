"""Run the whole pipeline from storyboard.json, in order. Every stage is cached,
so this is also the command to re-run after editing one shot.

    python -m pipeline.run                 # validate -> narration -> keyframes -> shots -> music -> film
    python -m pipeline.run --radio         # validate -> narration -> music -> radio cut
    python -m pipeline.run --only s04      # regenerate one shot's assets, then re-assemble
    python -m pipeline.run --motion blender  # motion from Blender (a Cloudflare model writes each camera
                                             # move, Blender renders locally) -> outputs_blender/final/

The motion backend is --motion, else HS_MOTION, else defaults.motion_backend in storyboard.json,
else "kaggle" (Wan 2.2 on a Kaggle GPU, pan-and-zoom where no clip exists). The two backends write
to separate output folders, so one never overwrites the other's films.
"""
from __future__ import annotations

import argparse
import json
import time

import os

from pipeline import assemble, blender_layer, keyframes, music, narration, shots
from pipeline.common import RUN_ID, RUNS_LOG, load_storyboard, main_guard, say


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--storyboard", default="storyboard.json")
    ap.add_argument("--radio", action="store_true")
    ap.add_argument("--only", nargs="*")
    ap.add_argument("--motion", choices=["kaggle", "blender"], help="how shots get their motion")
    ap.add_argument("--aspect", choices=["16x9", "9x16"], help="render one aspect only (faster iteration)")
    a = ap.parse_args()
    t0 = time.monotonic()
    sb = load_storyboard(a.storyboard)
    say("run", f"run_id {RUN_ID}: storyboard valid ({len(sb['shots'])} shots)")
    motion = a.motion or os.environ.get("HS_MOTION") or sb["defaults"].get("motion_backend", "kaggle")
    narration.run(sb, a.only)
    if not a.radio:
        keyframes.run(sb, a.only)
        if motion == "blender":
            say("run", "motion backend: blender")
            blender_layer.run(sb, a.only)
        else:
            shots.build(sb, a.only)
    music.build(sb)
    if a.radio:
        assemble.render(sb, "radio", "16x9")
    elif motion == "blender":
        blender_layer.build_film(sb, a.aspect)
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
