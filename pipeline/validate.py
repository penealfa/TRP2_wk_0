"""Validate storyboard.json against schema/ and print a timing summary.

    python -m pipeline.validate [storyboard.json]
"""
from __future__ import annotations

import sys

from pipeline.common import DEFAULT_STORYBOARD, load_storyboard, main_guard

WORDS_PER_SECOND = 2.3  # rough guide from the brief; measure your own TTS


def main() -> int:
    path = sys.argv[1] if len(sys.argv) > 1 else DEFAULT_STORYBOARD
    sb = load_storyboard(path)
    total = sum(s["duration_s"] for s in sb["shots"])
    words = sum(len(s["narration"]["text"].split()) for s in sb["shots"])
    print(f"OK  {path}: {len(sb['shots'])} shots, planned {total:.0f}s + {sb['end_card']['duration_s']}s end card")
    print(f"    narration {words} words, about {words / WORDS_PER_SECOND:.0f}s at {WORDS_PER_SECOND} words/s")
    film = total + sb["end_card"]["duration_s"]
    if not 60 <= film <= 90:
        print(f"WARN planned length {film:.0f}s is outside the 60-90s brief (assembly may stretch shots to fit narration)")
    for s in sb["shots"]:
        need = len(s["narration"]["text"].split()) / WORDS_PER_SECOND + s["narration"].get("offset_s", 0.3)
        flag = "  <- narration may run long" if need > s["duration_s"] else ""
        print(f"    {s['id']:>4} {s['duration_s']:>4}s {s['shot_size']:<17} {s['reason'][:60]}{flag}")
    for c in sb["characters"]:
        if c["real_person"]:
            print(f"    consent: {c['name']} -> ETHICS.md#{c['consent_ref']}")
    return 0


if __name__ == "__main__":
    main_guard(main)
