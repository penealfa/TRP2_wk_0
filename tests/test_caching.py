"""The property the walkthrough demonstrates: change one line, only that shot is regenerated."""
import copy
import json
import subprocess

from pipeline import keyframes, narration, shots
from pipeline.common import load_storyboard


def calls(log):
    return [json.loads(l) for l in log.read_text().splitlines()] if log.exists() else []


def test_one_edit_one_call(sandbox):
    import pipeline.common as c
    sb = load_storyboard()
    narration.run(sb)
    keyframes.run(sb)
    first = len(calls(c.RUNS_LOG))
    assert first == len(sb["shots"]) * 2 + len(sb["characters"])  # narration + keyframes + per-char ref sheets

    narration.run(sb); keyframes.run(sb)
    assert len(calls(c.RUNS_LOG)) == first, "unchanged storyboard must not call any model"

    sb2 = copy.deepcopy(sb)
    sb2["shots"][3]["keyframe"]["prompt"] += ", a cat asleep in the corner"
    sb2["shots"][5]["narration"]["text"] = "A new line for this shot."
    narration.run(sb2); keyframes.run(sb2)
    new = calls(c.RUNS_LOG)[first:]
    assert sorted((r["stage"], r["item"]) for r in new) == [("keyframes", "s04"), ("narration", "s06")]


def test_recording_preferred_and_rerecord_rebuilds_one(sandbox):
    import pipeline.common as c
    sb = load_storyboard()
    rec = sandbox / "recordings" / "s02.wav"
    subprocess.run(["ffmpeg", "-nostdin", "-loglevel", "error", "-f", "lavfi", "-i",
                    "anullsrc=r=48000:cl=mono:d=0.5", "-f", "lavfi", "-i", "sine=f=200:d=2",
                    "-filter_complex", "[0][1]concat=n=2:v=0:a=1", str(rec)], check=True)
    narration.run(sb, source="recorded")
    m = json.loads((c.OUTPUTS / "narration" / "manifest.json").read_text())
    assert m["s02"]["source"] == "recorded" and m["s01"]["source"] == "tts"
    assert m["s02"]["duration_s"] < 2.4  # leading silence trimmed
    n = len(calls(c.RUNS_LOG))
    subprocess.run(["ffmpeg", "-nostdin", "-y", "-loglevel", "error", "-f", "lavfi", "-i", "sine=f=250:d=3", str(rec)], check=True)
    narration.run(sb, source="recorded")
    new = calls(c.RUNS_LOG)[n:]
    assert [(r["item"], r["provider"]) for r in new] == [("s02", "human")]


def test_shots_fall_back_to_pan_and_zoom(sandbox):
    sb = load_storyboard()
    keyframes.run(sb, only=["s01"])
    res = shots.build(sb, only=["s01"])
    assert res["s01"]["source"] == "kenburns"
