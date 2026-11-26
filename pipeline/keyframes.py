"""Stage: keyframes. Per-character reference sheets first, then one still per
shot conditioned on the reference sheets of the characters in that shot.

    python -m pipeline.keyframes [--only s02] [--force] [--candidates 3]

Prompt = shot prompt + fixed description of each character in the shot + the
style block. Cache key = model + full prompt + seed + size + hashes of the
character reference sheets (up to FLUX2_MAX_REFS).
"""
from __future__ import annotations

import argparse
import os
import time
from pathlib import Path

from PIL import Image, ImageDraw

from pipeline import placeholders
from pipeline.common import (ROOT, FatalError, StageCache, call_with_retries, characters_for,
                             file_hash, load_storyboard, log_call, main_guard, offline, say,
                             select_shots, stable_hash)
from pipeline.providers import FLUX2_MAX_REFS, cloudflare_image

STAGE = "keyframes"
ASSETS_REFS = ROOT / "assets" / "refs"
REF_EXTS = ("jpg", "jpeg", "png")


def find_char_ref(char_id: str) -> Path | None:
    for ext in REF_EXTS:
        p = ASSETS_REFS / f"{char_id}.{ext}"
        if p.exists() and p.stat().st_size > 0:
            return p
    return None


def ref_sheet_prompt(sb: dict, char: dict) -> str:
    # A single figure, not a multi-panel sheet: FLUX.2 copies the *layout* of a
    # reference image, so a three-panel sheet turned shots s02/s06 into panels too
    # (FAILURES.md F4).
    # FLUX.2 also copies the reference's framing and background, so the framing is
    # chosen per character (characters[].ref_framing): a face-led character as a
    # portrait, an animal full-body against a dark night background like its scenes.
    if char.get("ref_framing") == "portrait":
        framing = (f"character reference: a single head-and-shoulders portrait of {char['name']}, "
                   f"facing the viewer, flat patterned ornamental background, one figure only, no panels")
    else:
        framing = (f"character reference: a single full-body figure of {char['name']}, three-quarter view, "
                   f"centred against a simple dark night background, one figure only, no panels, no frames")
    return f"{framing}. {char['description']}. {sb['style']['style_block']}"


def build_prompt(sb: dict, shot: dict) -> str:
    parts = [shot["keyframe"]["prompt"]]
    for c in characters_for(sb, shot):
        parts.append(f"{c['name'].split('(')[0].strip()}: {c['description']}")
    parts.append(sb["style"]["style_block"])
    if sb["style"].get("negative"):
        parts.append(f"avoid: {sb['style']['negative']}")
    return ". ".join(p.rstrip(". ") for p in parts)


FLAG_RETRIES = int(os.environ.get("HS_FLAG_RETRIES", "3"))


def _is_flagged(e: Exception) -> bool:
    msg = str(e).lower()
    return "flagged" in msg or '"code":3030' in msg.replace(" ", "")


def _generate(
    cache: StageCache,
    item_id: str,
    prompt: str,
    seed: int,
    model: str,
    w: int,
    h: int,
    refs: list[bytes],
    ref_key_part: object,
    force: bool,
) -> tuple[Path, str]:
    key = stable_hash({"model": model, "prompt": prompt, "seed": seed, "w": w, "h": h, "refs": ref_key_part})
    ext = "png"
    out = cache.get(item_id, key, ext)
    if out and not force:
        say(STAGE, f"{item_id}: cached ({key})")
        return out, key
    out = cache.path_for(item_id, key, ext)

    # Workers AI's output filter (error 3030, "output has been flagged") is
    # seed-dependent and gives false positives on painted faces. Retry with the next
    # seeds; the cache key stays on the storyboard seed, so the sequence is
    # deterministic and runs.jsonl records the seed actually used.
    def attempt(use_seed: int) -> None:
        def call():
            if offline():
                placeholders.image(out, item_id, prompt, w, h, use_seed)
            else:
                out.write_bytes(cloudflare_image(model, prompt, seed=use_seed, width=w, height=h, reference_images=refs))
            return out

        call_with_retries(
            call,
            stage=STAGE,
            provider="offline" if offline() else "cloudflare",
            model=model,
            params={"w": w, "h": h, "refs": len(refs), "prompt_chars": len(prompt), "seed_offset": use_seed - seed},
            item_id=item_id,
            seed=use_seed,
            cache_key=key,
        )

    try:
        for offset in range(FLAG_RETRIES + 1):
            try:
                attempt(seed + offset)
                break
            except FatalError as e:
                if not _is_flagged(e) or offset == FLAG_RETRIES:
                    raise
                say(STAGE, f"{item_id}: output flagged at seed {seed + offset}, trying seed {seed + offset + 1}")
    except FatalError as e:
        msg = str(e).lower()
        if refs and not offline() and not _is_flagged(e) and ("image" in msg or "input_image" in msg):
            t0 = time.monotonic()
            out.write_bytes(cloudflare_image(model, prompt, seed=seed, width=w, height=h, reference_images=[]))
            log_call(
                stage=STAGE,
                provider="cloudflare",
                model=model,
                params={"w": w, "h": h, "refs": 0, "prompt_chars": len(prompt), "fallback": "text_only"},
                latency_s=time.monotonic() - t0,
                status="degraded",
                item_id=item_id,
                seed=seed,
                cache_key=key,
                output=str(out),
                error=str(e)[:300],
            )
            say(STAGE, f"{item_id}: degraded text-only fallback -> {out.name}")
            return out, key
        raise
    say(STAGE, f"{item_id}: generated -> {out.name}")
    return out, key


def build_contact_sheet(cache: StageCache, items: list[tuple[str, Path]]) -> Path | None:
    if not items:
        return None
    tile = 320
    label_h = 44
    cols = min(4, len(items))
    rows = (len(items) + cols - 1) // cols
    pad = 12
    sheet = Image.new("RGB", (cols * tile + (cols + 1) * pad, rows * (tile + label_h) + (rows + 1) * pad), (18, 16, 15))
    draw = ImageDraw.Draw(sheet)
    font = placeholders.font(20)
    for idx, (label, path) in enumerate(items):
        r, c = divmod(idx, cols)
        x = pad + c * (tile + pad)
        y = pad + r * (tile + label_h + pad)
        with Image.open(path) as im:
            thumb = im.convert("RGB").resize((tile, tile))
        sheet.paste(thumb, (x, y))
        tw = draw.textlength(label, font=font)
        draw.text((x + (tile - tw) / 2, y + tile + 10), label, font=font, fill=(240, 232, 214))
    out = cache.dir / "contact_sheet.png"
    sheet.save(out)
    return out


def run(sb: dict, only=None, force: bool = False, candidates: int = 1) -> None:
    cache = StageCache(STAGE)
    d = sb["defaults"]
    model = "placeholder-image" if offline() else os.environ.get("HS_KEYFRAME_MODEL", d["keyframe_model"])
    w, h = d.get("keyframe_width", 1024), d.get("keyframe_height", 1024)
    uses_refs = "flux-2" in model

    ref_sheets: dict[str, tuple[Path, str, bytes]] = {}
    ref_tpl = sb["style"].get("reference_sheet_prompt", "")
    for char in sb["characters"]:
        cid = char["id"]
        item_id = f"ref_{cid}"
        user_ref = find_char_ref(cid)
        user_ref_hash = file_hash(user_ref) if user_ref else None
        user_ref_bytes = [user_ref.read_bytes()] if (user_ref and uses_refs) else []
        prompt = ref_sheet_prompt(sb, char)
        seed = char.get("seed", sb["style"].get("reference_seed", 0))
        ref_path, ref_key = _generate(
            cache,
            item_id,
            prompt,
            seed,
            model,
            w,
            h,
            user_ref_bytes,
            {"ref_tpl": ref_tpl, "user_ref": user_ref_hash},
            force and not only,
        )
        cache.record(item_id, ref_key, ref_path, seed=seed, user_ref=user_ref.name if user_ref else None)
        ref_sheets[cid] = (ref_path, file_hash(ref_path), ref_path.read_bytes())

    cand_items: dict[str, list[tuple[str, Path]]] = {}
    for shot in select_shots(sb, only):
        prompt = build_prompt(sb, shot)
        base_seed = shot["keyframe"]["seed"]
        shot_chars = characters_for(sb, shot)[:FLUX2_MAX_REFS]
        ref_hashes = [(c["id"], ref_sheets[c["id"]][1]) for c in shot_chars if c["id"] in ref_sheets]
        refs = [ref_sheets[c["id"]][2] for c in shot_chars if c["id"] in ref_sheets] if uses_refs else []

        for n in range(candidates):
            item = shot["id"] if n == 0 else f"{shot['id']}_cand{n}"
            out, key = _generate(cache, item, prompt, base_seed + n * 1000, model, w, h, refs, ref_hashes, force)
            if n == 0:
                cache.record(shot["id"], key, out, seed=base_seed, prompt=prompt,
                             approved=shot["keyframe"].get("approved", False))
            else:
                cand_items.setdefault(shot["id"], []).append((item, out))

    sheet_items: list[tuple[str, Path]] = []
    for s in sb["shots"]:
        p = cache.current(s["id"])
        if p:
            sheet_items.append((s["id"], p))
        sheet_items.extend(cand_items.get(s["id"], []))
    sheet = build_contact_sheet(cache, sheet_items)
    if sheet:
        say(STAGE, f"contact sheet -> {sheet.name} ({len(sheet_items)} images)")


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--storyboard", default="storyboard.json")
    ap.add_argument("--only", nargs="*")
    ap.add_argument("--force", action="store_true")
    ap.add_argument("--candidates", type=int, default=1, help="extra seeds per shot for picking (seed+1000*n)")
    a = ap.parse_args()
    run(load_storyboard(a.storyboard), a.only, a.force, a.candidates)
    return 0


if __name__ == "__main__":
    main_guard(main)
