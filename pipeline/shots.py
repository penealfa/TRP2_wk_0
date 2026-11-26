"""Stage: moving shots. Wan 2.2 image-to-video runs on a Kaggle GPU; everything
else runs here.

    python -m pipeline.shots export        # bundle the shots that need a GPU run
    python -m pipeline.shots kaggle        # (optional) push bundle + notebook with the kaggle CLI
    python -m pipeline.shots import DIR    # bring finished clips + their logs back
    python -m pipeline.shots build         # resolve a clip for every shot (default)

The cache key of a clip is the hash of: keyframe file hash, motion prompt, seed,
video model, size, frames and fps. `export` only lists shots whose key has no
clip yet, so changing one keyframe or one motion prompt sends one job to Kaggle.

`build` uses the Wan clip when one exists for the current key and otherwise
makes a slow pan-and-zoom (Ken Burns) from the keyframe, so the film always
assembles even when the GPU quota is gone.
"""
from __future__ import annotations

import argparse
import json
import shutil
import subprocess
import zipfile
from pathlib import Path

from pipeline.common import (OUTPUTS, ROOT, RUNS_LOG, StageCache, ffmpeg, file_hash, load_storyboard,
                             main_guard, read_manifest, say, select_shots, stable_hash)

STAGE = "shots"
JOB_DIR = OUTPUTS / "kaggle_job"
SIZES = {"480p": (640, 640), "720p": (960, 960)}  # square so one clip serves 9:16 and 16:9 crops
SECONDS = 5.0  # Wan 2.2 TI2V-5B: 121 frames at 24 fps


def i2v_spec(sb: dict, shot: dict, keyframe: Path) -> dict:
    d = sb["defaults"]
    w, h = SIZES[d.get("video_resolution", "480p")]
    fps = d["fps"]
    return {
        "shot_id": shot["id"],
        "model": d["video_model"],
        "keyframe_hash": file_hash(keyframe),
        "prompt": shot["motion"]["prompt"],
        "negative_prompt": "morphing face, extra limbs, flicker, text, watermark, fast motion",
        "seed": shot["motion"].get("seed", shot["keyframe"]["seed"]),
        "width": w, "height": h, "fps": fps,
        "frames": int(SECONDS * 24) + 1,
        "steps": 30, "guidance": 5.0,
    }


def keyframe_for(shot_id: str) -> Path:
    entry = read_manifest("keyframes").get(shot_id)
    if not entry:
        raise SystemExit(f"No keyframe for {shot_id}. Run `python -m pipeline.keyframes` first.")
    return OUTPUTS / entry["path"]


def kenburns(src: Path, out: Path, kind: str, seconds: float, fps: int, size: int = 1080) -> None:
    frames = max(1, int(round(seconds * fps)))
    zmax = 1.12
    step = (zmax - 1) / frames
    center = "x='iw/2-(iw/zoom/2)':y='ih/2-(ih/zoom/2)'"
    if kind == "push_in":
        z, xy = f"'min(1+{step:.6f}*on,{zmax})'", center
    elif kind == "pull_out":
        z, xy = f"'max({zmax}-{step:.6f}*on,1)'", center
    elif kind in ("pan_left", "pan_right"):
        z = "'1.10'"
        travel = f"(iw-iw/zoom)*on/{frames}"
        x = f"'{travel}'" if kind == "pan_right" else f"'(iw-iw/zoom)-{travel}'"
        xy = f"x={x}:y='ih/2-(ih/zoom/2)'"
    else:  # static: a barely perceptible drift so a still never looks frozen
        z, xy = f"'1+{0.02 / frames:.6f}*on'", center
    vf = (f"scale={size * 2}:{size * 2}:force_original_aspect_ratio=increase,crop={size * 2}:{size * 2},"
          f"zoompan=z={z}:{xy}:d={frames}:s={size}x{size}:fps={fps},format=yuv420p")
    ffmpeg(["-loop", "1", "-i", str(src), "-vf", vf, "-frames:v", str(frames), "-r", str(fps),
            "-c:v", "libx264", "-preset", "veryfast", "-crf", "18", str(out)])


def build(sb: dict, only=None) -> dict[str, dict]:
    cache = StageCache(STAGE)
    result = {}
    for shot in select_shots(sb, only):
        sid = shot["id"]
        kf = keyframe_for(sid)
        if shot["motion"].get("mode", "i2v") == "i2v":
            spec = i2v_spec(sb, shot, kf)
            key = stable_hash(spec)
            clip = cache.get(sid, key, "mp4")
            if clip:
                say(STAGE, f"{sid}: Wan clip ({key})")
                cache.record(sid, key, clip, source="wan", spec=spec)
                result[sid] = cache.manifest[sid]
                continue
        seconds = shot["duration_s"] + 1.0
        fb = shot["motion"]["fallback"]
        key = stable_hash({"kf": file_hash(kf), "fallback": fb, "s": seconds, "fps": sb["defaults"]["fps"]})
        item = f"{sid}_kb"
        clip = cache.get(item, key, "mp4")
        if not clip:
            clip = cache.path_for(item, key, "mp4")
            kenburns(kf, clip, fb, seconds, sb["defaults"]["fps"])
            say(STAGE, f"{sid}: no Wan clip yet, made {fb} pan-and-zoom -> {clip.name}")
        else:
            say(STAGE, f"{sid}: cached pan-and-zoom ({key})")
        cache.record(sid, key, clip, source="kenburns", fallback=fb)
        result[sid] = cache.manifest[sid]
    return result


def export(sb: dict, only=None, force: bool = False) -> Path:
    cache = StageCache(STAGE)
    if JOB_DIR.exists():
        shutil.rmtree(JOB_DIR)
    (JOB_DIR / "keyframes").mkdir(parents=True)
    jobs = []
    for shot in select_shots(sb, only):
        if shot["motion"].get("mode", "i2v") != "i2v":
            continue
        if not shot["keyframe"].get("approved", False) and not force:
            say(STAGE, f"{shot['id']}: keyframe not approved in storyboard.json, skipped (use --force)")
            continue
        kf = keyframe_for(shot["id"])
        spec = i2v_spec(sb, shot, kf)
        key = stable_hash(spec)
        if cache.get(shot["id"], key, "mp4"):
            continue
        img_name = f"{shot['id']}{kf.suffix}"
        shutil.copy(kf, JOB_DIR / "keyframes" / img_name)
        jobs.append({**spec, "key": key, "image": f"keyframes/{img_name}", "output": f"{shot['id']}__{key}.mp4"})
    (JOB_DIR / "jobs.json").write_text(json.dumps({"jobs": jobs}, indent=2, ensure_ascii=False))
    meta = {"title": "heritage-short-jobs", "id": "KAGGLE_USERNAME/heritage-short-jobs", "licenses": [{"name": "CC0-1.0"}]}
    (JOB_DIR / "dataset-metadata.json").write_text(json.dumps(meta, indent=2))
    say(STAGE, f"{len(jobs)} job(s) need the GPU -> {JOB_DIR}/jobs.json")
    for j in jobs:
        say(STAGE, f"  {j['shot_id']}: {j['prompt'][:70]}")
    return JOB_DIR


def import_clips(src: Path) -> None:
    cache = StageCache(STAGE)
    tmp = None
    if src.suffix == ".zip":
        tmp = OUTPUTS / "_import"
        shutil.rmtree(tmp, ignore_errors=True)
        with zipfile.ZipFile(src) as z:
            z.extractall(tmp)
        src = tmp
    n = 0
    for clip in src.rglob("*.mp4"):
        if "__" not in clip.stem:
            continue
        shutil.copy(clip, cache.dir / clip.name)
        n += 1
    logs = 0
    for log in src.rglob("runs_kaggle.jsonl"):
        with open(RUNS_LOG, "a", encoding="utf-8") as out:
            for line in log.read_text(encoding="utf-8").splitlines():
                if line.strip():
                    out.write(line.rstrip() + "\n")
                    logs += 1
    if tmp:
        shutil.rmtree(tmp, ignore_errors=True)
    say(STAGE, f"imported {n} clip(s) and {logs} log line(s) into runs.jsonl. Now run `build`.")


def kaggle_push(sb: dict) -> None:
    """Best-effort automation with the official kaggle CLI (pip install kaggle,
    ~/.kaggle/kaggle.json). Uploading by hand through the Kaggle web UI works too."""
    if not shutil.which("kaggle"):
        raise SystemExit("kaggle CLI not found: pip install kaggle, then put kaggle.json in ~/.kaggle/")
    user = subprocess.run(["kaggle", "config", "view"], capture_output=True, text=True).stdout
    say(STAGE, "uploading job bundle as a Kaggle dataset (new version if it exists)")
    meta_path = JOB_DIR / "dataset-metadata.json"
    import os
    uname = os.environ.get("KAGGLE_USERNAME") or next(
        (l.split(":", 1)[1].strip() for l in user.splitlines() if "username" in l), "")
    if not uname:
        raise SystemExit("Set KAGGLE_USERNAME in .env")
    meta = json.loads(meta_path.read_text()); meta["id"] = f"{uname}/heritage-short-jobs"
    meta_path.write_text(json.dumps(meta, indent=2))
    r = subprocess.run(["kaggle", "datasets", "version", "-p", str(JOB_DIR), "-m", "jobs", "-r", "zip"])
    if r.returncode != 0:
        subprocess.run(["kaggle", "datasets", "create", "-p", str(JOB_DIR), "-r", "zip"], check=True)
    kdir = ROOT / "notebooks" / "kaggle_wan"
    km = json.loads((kdir / "kernel-metadata.json").read_text())
    km["id"] = f"{uname}/heritage-short-wan22"; km["dataset_sources"] = [f"{uname}/heritage-short-jobs"]
    (kdir / "kernel-metadata.json").write_text(json.dumps(km, indent=2))
    subprocess.run(["kaggle", "kernels", "push", "-p", str(kdir)], check=True)
    say(STAGE, f"pushed. Check: kaggle kernels status {uname}/heritage-short-wan22 ; then "
               f"kaggle kernels output {uname}/heritage-short-wan22 -p outputs/kaggle_out ; "
               "python -m pipeline.shots import outputs/kaggle_out")


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("cmd", nargs="?", default="build", choices=["build", "export", "import", "kaggle"])
    ap.add_argument("path", nargs="?", help="for import: folder or zip of Kaggle outputs")
    ap.add_argument("--storyboard", default="storyboard.json")
    ap.add_argument("--only", nargs="*")
    ap.add_argument("--force", action="store_true", help="export unapproved keyframes too")
    a = ap.parse_args()
    sb = load_storyboard(a.storyboard)
    if a.cmd == "build":
        build(sb, a.only)
    elif a.cmd == "export":
        export(sb, a.only, a.force)
    elif a.cmd == "import":
        if not a.path:
            raise SystemExit("usage: python -m pipeline.shots import <folder-or-zip>")
        import_clips(Path(a.path))
    else:
        export(sb, a.only, a.force)
        kaggle_push(sb)
    return 0


if __name__ == "__main__":
    main_guard(main)
