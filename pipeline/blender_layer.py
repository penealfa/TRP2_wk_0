"""Optional layer: a model writes a Blender camera move for every keyframe, Blender renders it.

This is beyond the brief's pipeline and is kept separate from it. The main film
(`make film`) never touches this module; this one writes its own clips to
outputs/blender/ and its own pair of films to outputs_blender/final/.

    python -m pipeline.blender_layer render [--only s03] [--force] [--template-only]
    python -m pipeline.blender_layer film          # assemble outputs_blender/final/*.mp4

How a shot is made:
  1. A text model (Cloudflare Workers AI by default, Gemini optional) is given the shot's motion description and a known-good Blender script (TEMPLATE)
     and returns a modified script with a camera move for that shot.
  2. The script is checked statically (allow-list of imports, no file/network/process access)
     before it is allowed anywhere near Blender. One repair round if the check or the render fails.
  3. `blender -b --factory-startup -P script.py` renders the keyframe, projected onto a
     subdivided plane displaced by its own brightness (a cheap pseudo-depth, so a camera move
     shows parallax), to a square H.264 clip. If the model's script cannot be made to work the
     built-in template move is used and the run is logged "degraded".
  4. Cache key = keyframe hash + motion text + model + template version + size + fps + length.
     Every model call and every render is a line in runs.jsonl; every script is saved in
     outputs/blender/scripts/ so what ran is reviewable.
"""
from __future__ import annotations

import argparse
import ast
import json
import os
import re
import shutil
import subprocess
import sys
import time
from pathlib import Path

from pipeline import common
from pipeline.common import (FatalError, RetryableError, StageCache, call_with_retries, file_hash, load_storyboard,
                             log_call, main_guard, media_duration, offline, read_manifest, say,
                             select_shots, stable_hash)
from pipeline.providers import cloudflare_text, gemini_text

STAGE = "blender"
TEMPLATE_VERSION = "3"
SIZE = int(os.environ.get("HS_BLENDER_SIZE", "1080"))
RENDER_TIMEOUT_S = int(os.environ.get("HS_BLENDER_TIMEOUT", "1200"))
FILM_DIR = common.ROOT / "outputs_blender"

# A complete, working script. The model edits only the CAMERA MOVE section; the rest is the contract.
TEMPLATE = r'''
import bpy, math, sys

# args after "--": image, output file, width, height, fps, seconds
image_path, out_path = sys.argv[sys.argv.index("--") + 1], sys.argv[sys.argv.index("--") + 2]
W, H, FPS = (int(v) for v in sys.argv[sys.argv.index("--") + 3:sys.argv.index("--") + 6])
SECONDS = float(sys.argv[sys.argv.index("--") + 6])
FRAMES = max(2, int(round(SECONDS * FPS)))

bpy.ops.wm.read_factory_settings(use_empty=True)
scene = bpy.context.scene
scene.render.engine = "BLENDER_EEVEE"
scene.render.resolution_x, scene.render.resolution_y = W, H
scene.render.resolution_percentage = 100
scene.render.fps = FPS
scene.frame_start, scene.frame_end = 1, FRAMES
scene.view_settings.view_transform = "Standard"
scene.eevee.taa_render_samples = 16

# --- picture on a subdivided plane, displaced by its own brightness (pseudo-depth) ---
img = bpy.data.images.load(image_path)
bpy.ops.mesh.primitive_plane_add(size=2.45, location=(0, 0, 0), rotation=(math.radians(90), 0, 0))
plane = bpy.context.active_object
sub = plane.modifiers.new("sub", "SUBSURF")
sub.subdivision_type = "SIMPLE"
sub.levels = sub.render_levels = 7
tex = bpy.data.textures.new("depth", "IMAGE")
tex.image = img
disp = plane.modifiers.new("disp", "DISPLACE")
disp.texture = tex
disp.texture_coords = "UV"
disp.direction = "Z"          # local +Z of the rotated plane points at the camera: brighter = nearer
disp.strength = 0.12
disp.mid_level = 0.35

mat = bpy.data.materials.new("picture")
mat.use_nodes = True
nt = mat.node_tree
nt.nodes.clear()
tnode = nt.nodes.new("ShaderNodeTexImage")
tnode.image = img
emit = nt.nodes.new("ShaderNodeEmission")
outn = nt.nodes.new("ShaderNodeOutputMaterial")
nt.links.new(tnode.outputs["Color"], emit.inputs["Color"])
nt.links.new(emit.outputs["Emission"], outn.inputs["Surface"])
plane.data.materials.append(mat)

# --- camera ---
cam_data = bpy.data.cameras.new("cam")
cam_data.lens = 50
cam = bpy.data.objects.new("cam", cam_data)
scene.collection.objects.link(cam)
scene.camera = cam
cam.rotation_euler = (math.radians(90), 0, 0)

# ===== CAMERA MOVE (edit only this section) =====
# The plane is 2.45 wide and the camera at y=-2.78 frames the middle 82% of it, so keep the
# move small enough that the edge of the plane never enters the frame:
# |x| <= 0.1, |z| <= 0.1, y between -2.9 and -2.3, tilt and roll within 1.5 degrees.
# (The safety clamp below enforces these limits whatever this section does.)
START = {"x": 0.0, "y": -2.78, "z": 0.0}
END = {"x": 0.0, "y": -2.35, "z": 0.0}      # slow push in
# =================================================

# ===== SAFETY CLAMP (do not edit): keeps the edge of the picture out of the frame =====
lim_loc = cam.constraints.new("LIMIT_LOCATION")
lim_loc.owner_space = "WORLD"
lim_loc.use_min_x = lim_loc.use_max_x = lim_loc.use_min_y = lim_loc.use_max_y = True
lim_loc.use_min_z = lim_loc.use_max_z = True
lim_loc.min_x, lim_loc.max_x = -0.10, 0.10
lim_loc.min_y, lim_loc.max_y = -2.90, -2.30
lim_loc.min_z, lim_loc.max_z = -0.10, 0.10
lim_rot = cam.constraints.new("LIMIT_ROTATION")
lim_rot.owner_space = "WORLD"
lim_rot.use_limit_x = lim_rot.use_limit_y = lim_rot.use_limit_z = True
lim_rot.min_x, lim_rot.max_x = math.radians(88.5), math.radians(91.5)
lim_rot.min_y, lim_rot.max_y = math.radians(-1.5), math.radians(1.5)
lim_rot.min_z, lim_rot.max_z = math.radians(-1.5), math.radians(1.5)
# =================================================

for frame, pos in ((1, START), (FRAMES, END)):
    cam.location = (pos["x"], pos["y"], pos["z"])
    cam.keyframe_insert("location", frame=frame)

scene.render.filepath = out_path
scene.render.use_file_extension = False
scene.render.image_settings.media_type = "VIDEO"   # Blender 5: choose video before FFMPEG
scene.render.image_settings.file_format = "FFMPEG"
scene.render.ffmpeg.format = "MPEG4"
scene.render.ffmpeg.codec = "H264"
scene.render.ffmpeg.constant_rate_factor = "HIGH"
bpy.ops.render.render(animation=True)
'''

# Template camera moves, keyed by the storyboard's fallback names (also the no-model fallback).
TEMPLATE_MOVES = {
    "push_in": ({"x": 0.0, "y": -2.78, "z": 0.0}, {"x": 0.0, "y": -2.35, "z": 0.0}),
    "pull_out": ({"x": 0.0, "y": -2.35, "z": 0.0}, {"x": 0.0, "y": -2.78, "z": 0.0}),
    "pan_right": ({"x": -0.10, "y": -2.60, "z": 0.0}, {"x": 0.10, "y": -2.60, "z": 0.0}),
    "pan_left": ({"x": 0.10, "y": -2.60, "z": 0.0}, {"x": -0.10, "y": -2.60, "z": 0.0}),
    "static": ({"x": 0.0, "y": -2.78, "z": 0.0}, {"x": 0.0, "y": -2.70, "z": 0.02}),
}

_MOVE_RE = re.compile(r'START = \{.*?\}\nEND = \{.*?\}.*?\n', re.S)


def template_script(kind: str) -> str:
    start, end = TEMPLATE_MOVES.get(kind, TEMPLATE_MOVES["push_in"])
    block = f"START = {start!r}\nEND = {end!r}      # template move: {kind}\n"
    return _MOVE_RE.sub(lambda _m: block, TEMPLATE, count=1)


# ------------------------------------------------------------------ safety check
ALLOWED_IMPORTS = {"bpy", "math", "sys", "mathutils"}
FORBIDDEN_NAMES = {"open", "exec", "eval", "compile", "__import__", "input", "globals", "locals",
                   "getattr", "setattr", "delattr", "breakpoint", "exit", "quit"}
FORBIDDEN_ATTRS = {"system", "popen", "Popen", "call", "run", "urlopen", "get", "post", "socket",
                   "remove", "rmtree", "unlink", "rename", "write_text", "write_bytes", "save_as_mainfile",
                   "save_mainfile", "open_mainfile", "addon_enable", "script"}


class ScriptRejected(ValueError):
    pass


def check_script(code: str) -> None:
    """Static allow-list check for a model-written script. Raises ScriptRejected."""
    try:
        tree = ast.parse(code)
    except SyntaxError as e:
        raise ScriptRejected(f"syntax error: {e}") from e
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for a in node.names:
                if a.name.split(".")[0] not in ALLOWED_IMPORTS:
                    raise ScriptRejected(f"import not allowed: {a.name}")
        elif isinstance(node, ast.ImportFrom):
            if (node.module or "").split(".")[0] not in ALLOWED_IMPORTS:
                raise ScriptRejected(f"import not allowed: {node.module}")
        elif isinstance(node, ast.Name) and node.id in FORBIDDEN_NAMES:
            raise ScriptRejected(f"name not allowed: {node.id}")
        elif isinstance(node, ast.Attribute) and node.attr in FORBIDDEN_ATTRS:
            raise ScriptRejected(f"attribute not allowed: .{node.attr}")
    for needle in ("bpy.ops.render.render", "read_factory_settings", "sys.argv", "LIMIT_LOCATION", "LIMIT_ROTATION"):
        if needle not in code:
            raise ScriptRejected(f"missing required call: {needle}")


# ------------------------------------------------------------------ blender
def find_blender() -> str:
    cands = [os.environ.get("BLENDER_EXE"), shutil.which("blender"),
             r"E:\Blender\blender.exe", r"C:\Program Files\Blender Foundation\Blender\blender.exe"]
    for c in cands:
        if c and Path(c).exists():
            return str(c)
        if c and shutil.which(c):
            return shutil.which(c)  # type: ignore[return-value]
    raise FatalError("Blender not found: install it or set BLENDER_EXE in .env")


def run_blender(script: Path, image: Path, out: Path, seconds: float, fps: int) -> None:
    out.parent.mkdir(parents=True, exist_ok=True)
    cmd = [find_blender(), "-b", "--factory-startup", "-P", str(script), "--",
           str(image), str(out), str(SIZE), str(SIZE), str(fps), f"{seconds:.3f}"]
    r = subprocess.run(cmd, capture_output=True, text=True, timeout=RENDER_TIMEOUT_S, stdin=subprocess.DEVNULL)
    if r.returncode != 0 or not out.exists() or out.stat().st_size == 0:
        tail = "\n".join((r.stdout + "\n" + r.stderr).strip().splitlines()[-12:])
        raise FatalError(f"blender failed (exit {r.returncode}):\n{tail}")


def blender_version() -> str:
    try:
        out = subprocess.run([find_blender(), "--version"], capture_output=True, text=True, timeout=60).stdout
        return out.splitlines()[0].strip()
    except Exception:
        return "blender"


# ------------------------------------------------------------------ script-writing model
def _prompt(shot: dict, kind: str, seconds: float, feedback: str | None) -> str:
    motion = shot["motion"]["prompt"]
    p = f"""You are writing a Blender {blender_version().replace('Blender ', '')} Python script for a short painted film.
The shot is a painting; the camera moves over it. Shot description: "{shot.get('reason', '')}"
Camera move wanted (from the storyboard): "{motion}"  (fallback move name: {kind}). Length: {seconds:.1f} s.

Below is a COMPLETE, WORKING script. Return the same script with ONLY the section between
"# ===== CAMERA MOVE" and "# =====" rewritten so the camera does the move described above.
You may use several keyframes (cam.keyframe_insert) and rotate the camera slightly, but follow the
limits written in that section so the edge of the picture never shows. You may also adjust
`disp.strength` (between 0.05 and 0.25) if the scene suits more or less depth. Do not change anything else.
Rules: only `import bpy, math, sys, mathutils`; no files, network, processes, or `open`.
Return only the Python code in one ```python block.

{TEMPLATE}"""
    if feedback:
        p += f"\n\nYour previous attempt failed with:\n{feedback}\nFix it and return the complete script again."
    return p


def _extract_code(text: str) -> str:
    m = re.search(r"```(?:python)?\s*\n(.*?)```", text, re.S)
    return (m.group(1) if m else text).strip() + "\n"


# Script-writing model. Cloudflare Workers AI is the default (free allowance, same account as the
# keyframes); HS_BLENDER_LLM=gemini switches to Gemini. Each provider has a short model chain: when
# a model is overloaded after its retries, the next one is asked. Every call is a line in runs.jsonl.
LLM_PROVIDER = os.environ.get("HS_BLENDER_LLM", "cloudflare").lower()
MODEL_CHAINS = {
    "cloudflare": ["@cf/qwen/qwen2.5-coder-32b-instruct", "@cf/meta/llama-3.3-70b-instruct-fp8-fast",
                   "@cf/openai/gpt-oss-120b"],
    "gemini": ["gemini-3.8-flash", "gemini-3.7-flash", "gemini-3.5-flash", "gemini-flash-latest"],
}


def llm_chain() -> list[str]:
    chain = list(MODEL_CHAINS[LLM_PROVIDER])
    override = os.environ.get("HS_BLENDER_MODEL")
    return [override] + [m for m in chain if m != override] if override else chain


def llm_text(model: str, prompt: str) -> str:
    if LLM_PROVIDER == "gemini":
        return gemini_text(model, prompt, temperature=0.4)
    return cloudflare_text(model, prompt, temperature=0.4)


def script_from_llm(chain: list[str], shot: dict, kind: str, seconds: float, feedback: str | None, key: str) -> str:
    last: Exception | None = None
    for m in chain:
        def call(m=m):
            return llm_text(m, _prompt(shot, kind, seconds, feedback))

        try:
            res = call_with_retries(call, stage=STAGE + "_script", provider=LLM_PROVIDER, model=m,
                                    params={"shot": shot["id"], "repair": bool(feedback)},
                                    item_id=shot["id"], cache_key=key,
                                    attempts=int(os.environ.get("HS_LLM_ATTEMPTS", "2")), base_delay=5.0)
        except RetryableError as e:
            last = e
            say(STAGE, f"{shot['id']}: {m} unavailable, trying the next model")
            continue
        code = _extract_code(res.value)
        check_script(code)
        return code
    raise last or RetryableError("no script-writing model available")


# ------------------------------------------------------------------ stage
def narration_length(shot: dict) -> float:
    entry = read_manifest("narration").get(shot["id"])
    n = shot.get("narration") or {}
    return float(entry["duration_s"]) + float(n.get("offset_s", 0) or 0) + 0.6 if entry else 0.0


def clip_seconds(shot: dict) -> float:
    return round(max(float(shot["duration_s"]), narration_length(shot)) + 0.5, 2)


def render_shot(sb: dict, shot: dict, cache: StageCache, *, model: str, force: bool, template_only: bool) -> dict:
    sid = shot["id"]
    kf_entry = read_manifest("keyframes").get(sid)
    if not kf_entry:
        raise SystemExit(f"No keyframe for {sid}. Run `python -m pipeline.keyframes` first.")
    kf = common.OUTPUTS / kf_entry["path"]
    kind = shot["motion"]["fallback"]
    fps = sb["defaults"]["fps"]
    seconds = clip_seconds(shot)
    use_llm = not (offline() or template_only)
    key = stable_hash({"kf": file_hash(kf), "motion": shot["motion"]["prompt"], "kind": kind,
                       "model": f"{LLM_PROVIDER}:{model}" if use_llm else "template", "tv": TEMPLATE_VERSION,
                       "size": SIZE, "fps": fps, "s": seconds})
    clip = cache.get(sid, key, "mp4")
    prev = cache.manifest.get(sid, {})
    if clip and use_llm and prev.get("script_origin") == "template" and not force:
        say(STAGE, f"{sid}: last time no model was available (template move), trying again")
        clip = None
    if clip and not force:
        say(STAGE, f"{sid}: cached ({key})")
        cache.record(sid, key, clip, source="blender", script=f"{sid}__{key}.py",
                     script_origin=prev.get("script_origin", "?"), seconds=seconds)
        return cache.manifest[sid]

    scripts = cache.dir / "scripts"
    scripts.mkdir(exist_ok=True)
    script_path = scripts / f"{sid}__{key}.py"
    clip = cache.path_for(sid, key, "mp4")
    origin, feedback, status = "template", None, "ok"
    attempts = (["llm", "llm"] if use_llm else []) + ["template"]
    t0 = time.monotonic()
    for i, who in enumerate(attempts):
        try:
            if who == "llm":
                code = script_from_llm([model] + [m for m in llm_chain() if m != model], shot, kind, seconds, feedback, key)
            else:
                code = template_script(kind)
            script_path.write_text(code, encoding="utf-8")
            run_blender(script_path, kf, clip, seconds, fps)
            origin = who
            if who == "template" and use_llm:
                status = "degraded"
            break
        except (ScriptRejected, FatalError, RetryableError) as e:
            feedback = str(e)[:900]
            say(STAGE, f"{sid}: {who} attempt failed: {feedback.splitlines()[0][:120]}")
            if i == len(attempts) - 1:
                log_call(stage=STAGE, provider="blender-local", model=blender_version(), params={"kind": kind},
                         latency_s=time.monotonic() - t0, status="fatal_error", item_id=sid, cache_key=key,
                         error=feedback[:500])
                raise
    dt = time.monotonic() - t0
    log_call(stage=STAGE, provider="blender-local", model=blender_version(),
             params={"size": SIZE, "fps": fps, "seconds": seconds, "script_origin": origin, "kind": kind,
                     "script": script_path.name},
             latency_s=dt, status=status, item_id=sid, cache_key=key, output=str(clip))
    say(STAGE, f"{sid}: {origin} script -> {clip.name} ({dt:.0f}s)")
    cache.record(sid, key, clip, source="blender", script=script_path.name, script_origin=origin,
                 seconds=seconds)
    return cache.manifest[sid]


def run(sb: dict, only=None, force=False, template_only=False) -> dict:
    cache = StageCache(STAGE)
    model = llm_chain()[0]
    out = {}
    for shot in select_shots(sb, only):
        out[shot["id"]] = render_shot(sb, shot, cache, model=model, force=force, template_only=template_only)
    return out


# ------------------------------------------------------------------ film
def build_film(sb: dict, aspect: str | None = None) -> None:
    """Assemble the Blender-layer films into outputs_blender/final/ with the same narration,
    score, subtitles and end card as the main film; only the picture differs."""
    blend = read_manifest(STAGE)
    missing = [s["id"] for s in sb["shots"] if s["id"] not in blend]
    if missing:
        raise SystemExit(f"No Blender clip for {', '.join(missing)}; run `render` first.")
    for stage in ("narration", "music"):
        src, dst = common.OUTPUTS / stage, FILM_DIR / stage
        if dst.exists():
            shutil.rmtree(dst)
        shutil.copytree(src, dst)
    shots_dir = FILM_DIR / "shots"
    if shots_dir.exists():
        shutil.rmtree(shots_dir)
    shots_dir.mkdir(parents=True)
    manifest = {}
    for sid, e in blend.items():
        src = common.OUTPUTS / e["path"]
        shutil.copy(src, shots_dir / src.name)
        manifest[sid] = {"key": e["key"], "path": f"shots/{src.name}", "source": "blender"}
    (shots_dir / "manifest.json").write_text(json.dumps(manifest, indent=2))
    env = {**os.environ, "HS_OUTPUTS": str(FILM_DIR), "HS_RUNS_LOG": str(FILM_DIR / "runs.jsonl"), "PYTHONUTF8": "1"}
    cmd = [sys.executable, "-m", "pipeline.assemble", "film"] + (["--aspect", aspect] if aspect else [])
    subprocess.run(cmd, env=env, check=True, cwd=common.ROOT)


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("cmd", choices=["render", "film"])
    ap.add_argument("--storyboard", default="storyboard.json")
    ap.add_argument("--only", nargs="*")
    ap.add_argument("--force", action="store_true")
    ap.add_argument("--template-only", action="store_true", help="skip the model, use the built-in moves")
    a = ap.parse_args()
    sb = load_storyboard(a.storyboard)
    if a.cmd == "render":
        run(sb, a.only, a.force, a.template_only)
    else:
        build_film(sb)
    return 0


if __name__ == "__main__":
    main_guard(main)
