"""Stage: script. Ask Gemini to draft a storyboard from your story notes, then
validate it against the schema and feed any errors back for a fix (up to 3 tries).
The draft goes to storyboard.draft.json; you review it and copy it over yourself.

    python -m pipeline.script story.md
    python -m pipeline.script --translate      # fill narration.text from text_en in the film's language

Keep private family details you do not want used for training out of story.md:
free API tiers may use what you send.
"""
from __future__ import annotations

import argparse
import json
import os
from pathlib import Path

from pipeline.common import (ROOT, SCHEMA_PATH, StoryboardError, call_with_retries, load_storyboard,
                             main_guard, say)
from pipeline.providers import gemini_text

STAGE = "script"
MODEL = os.environ.get("HS_TEXT_MODEL", "gemini-3.8-flash")


def draft(notes: Path) -> int:
    schema = SCHEMA_PATH.read_text()
    example = (ROOT / "storyboard.json").read_text()
    prompt = (
        "You are a documentary editor. Turn these story notes into a storyboard JSON that validates against "
        "the JSON Schema below. 8-12 shots, 60-90 s total, about 2.3 spoken words per second, one idea and one "
        "gentle movement per shot, varied shot sizes, a stated reason for every shot, the subject centred in "
        "frame, no text inside images, motion prompts that describe only motion and camera. Write for the ear: "
        "short sentences, concrete images.\n\nSCHEMA:\n" + schema + "\n\nEXAMPLE (structure only, not content):\n"
        + example + "\n\nSTORY NOTES:\n" + notes.read_text(encoding="utf-8") + "\n\nReturn only the JSON.")
    out = ROOT / "storyboard.draft.json"
    for attempt in range(1, 4):
        text = call_with_retries(lambda: gemini_text(MODEL, prompt, json_mode=True, temperature=0.8),
                                 stage=STAGE, provider="gemini", model=MODEL,
                                 params={"attempt": attempt, "prompt_chars": len(prompt)}).value
        out.write_text(text, encoding="utf-8")
        try:
            load_storyboard(out)
            say(STAGE, f"valid draft -> {out.name}. Review it, then copy it to storyboard.json.")
            return 0
        except StoryboardError as e:
            say(STAGE, f"draft {attempt} invalid, asking for a fix")
            prompt = f"This JSON fails validation:\n{e}\n\nFix it and return only the corrected JSON:\n{text}"
    say(STAGE, f"still invalid after 3 tries; fix {out.name} by hand")
    return 1


def translate() -> int:
    sb = load_storyboard()
    lang = sb["language"]["name"]
    lines = {s["id"]: s["narration"]["text_en"] for s in sb["shots"]}
    prompt = (f"Translate each narration line into natural spoken {lang}, as a family member would say it aloud. "
              f"Keep names. Return a JSON object mapping the same keys to the translations.\n{json.dumps(lines, ensure_ascii=False)}")
    res = call_with_retries(lambda: gemini_text(MODEL, prompt, json_mode=True, temperature=0.3),
                            stage=STAGE, provider="gemini", model=MODEL, params={"task": "translate", "lang": lang}).value
    tr = json.loads(res)
    for s in sb["shots"]:
        if s["id"] in tr:
            s["narration"]["text"] = tr[s["id"]]
    out = ROOT / "storyboard.draft.json"
    out.write_text(json.dumps(sb, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    say(STAGE, f"translations -> {out.name}. Have a native speaker read them before recording.")
    return 0


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("notes", nargs="?", type=Path)
    ap.add_argument("--translate", action="store_true")
    a = ap.parse_args()
    if a.translate:
        return translate()
    if not a.notes:
        raise SystemExit("usage: python -m pipeline.script story.md  |  --translate")
    return draft(a.notes)


if __name__ == "__main__":
    main_guard(main)
