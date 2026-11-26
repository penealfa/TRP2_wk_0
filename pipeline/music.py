"""Stage: music. ACE-Step 1.5 runs on a Kaggle (or Colab) GPU; candidates come
back here and one is chosen in storyboard.json (music.chosen + chosen_reason).

    python -m pipeline.music export        # write the candidate jobs for the notebook
    python -m pipeline.music import DIR    # bring candidate tracks + logs back
    python -m pipeline.music build         # resolve the score used by assembly (default)

Candidate n uses seed + n. The key covers caption, tags, lyrics, bpm, key,
duration and seed, so editing the caption makes a new set of candidates while
the old ones stay on disk for comparison. With no candidates yet, `build` makes
a quiet placeholder drone: that is the radio-cut score.
"""
from __future__ import annotations

import argparse
import json
import shutil
import zipfile
from pathlib import Path

from pipeline import placeholders
from pipeline.common import (OUTPUTS, RUNS_LOG, StageCache, load_storyboard, main_guard, say,
                             stable_hash)

STAGE = "music"
JOB_DIR = OUTPUTS / "kaggle_music_job"


def candidate_specs(sb: dict) -> list[dict]:
    m = sb["music"]
    specs = []
    for n in range(1, m.get("candidates", 3) + 1):
        spec = {"model": "ACE-Step-1.5", "caption": m["caption"], "tags": m.get("tags", []),
                "lyrics": m.get("lyrics", "[instrumental]"), "bpm": m.get("bpm"), "keyscale": m.get("key"),
                "duration_s": m["duration_s"], "seed": m.get("seed", 0) + n}
        specs.append({"id": f"c{n}", "key": stable_hash(spec), **spec})
    return specs


def export(sb: dict) -> None:
    cache = StageCache(STAGE)
    todo = [
        s for s in candidate_specs(sb)
        if not any(cache.get(s["id"], s["key"], ext) for ext in ("flac", "wav", "mp3"))
    ]
    JOB_DIR.mkdir(parents=True, exist_ok=True)
    for s in todo:
        s["output"] = f"{s['id']}__{s['key']}.flac"
    (JOB_DIR / "jobs.json").write_text(json.dumps({"jobs": todo}, indent=2, ensure_ascii=False))
    say(STAGE, f"{len(todo)} candidate(s) to generate -> {JOB_DIR}/jobs.json")


def import_tracks(src: Path) -> None:
    cache = StageCache(STAGE)
    tmp = None
    if src.suffix == ".zip":
        tmp = OUTPUTS / "_import_music"
        with zipfile.ZipFile(src) as z:
            z.extractall(tmp)
        src = tmp
    n = 0
    for f in list(src.rglob("*.wav")) + list(src.rglob("*.mp3")) + list(src.rglob("*.flac")):
        if "__" in f.stem:
            shutil.copy(f, cache.dir / f.name)
            n += 1
    for log in src.rglob("runs_kaggle.jsonl"):
        with open(RUNS_LOG, "a", encoding="utf-8") as out:
            out.writelines(l.rstrip() + "\n" for l in log.read_text(encoding="utf-8").splitlines() if l.strip())
    if tmp:
        shutil.rmtree(tmp, ignore_errors=True)
    say(STAGE, f"imported {n} track(s)")


def build(sb: dict) -> Path:
    cache = StageCache(STAGE)
    specs = candidate_specs(sb)
    found = {}
    for s in specs:
        for ext in ("wav", "mp3", "flac"):
            p = cache.get(s["id"], s["key"], ext)
            if p:
                found[s["id"]] = (s, p)
                break
    chosen = sb["music"].get("chosen")
    if chosen and chosen in found:
        s, p = found[chosen]
    elif found:
        cid = sorted(found)[0]
        s, p = found[cid]
        say(STAGE, f"music.chosen not set; using {cid}. Listen to all candidates and record your choice and reason.")
    else:
        key = stable_hash({"placeholder": True, "d": sb["music"]["duration_s"], "seed": sb["music"].get("seed", 0)})
        p = cache.get("placeholder", key, "wav")
        if not p:
            p = cache.path_for("placeholder", key, "wav")
            placeholders.music(p, sb["music"]["duration_s"], sb["music"].get("seed", 0))
        say(STAGE, "no ACE-Step candidates yet: using the placeholder drone (fine for the radio cut)")
        cache.record("score", key, p, source="placeholder")
        return p
    cache.record("score", s["key"], p, source="ace-step", candidate=s["id"],
                 reason=sb["music"].get("chosen_reason", ""))
    say(STAGE, f"score = {p.name}")
    return p


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("cmd", nargs="?", default="build", choices=["build", "export", "import"])
    ap.add_argument("path", nargs="?")
    ap.add_argument("--storyboard", default="storyboard.json")
    a = ap.parse_args()
    sb = load_storyboard(a.storyboard)
    if a.cmd == "export":
        export(sb)
    elif a.cmd == "import":
        if not a.path:
            raise SystemExit("usage: python -m pipeline.music import <folder-or-zip>")
        import_tracks(Path(a.path))
    else:
        build(sb)
    return 0


if __name__ == "__main__":
    main_guard(main)
