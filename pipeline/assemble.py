"""Stage: assembly, all in FFmpeg and all driven by storyboard.json.

    python -m pipeline.assemble radio          # narration + placeholder score on black, subtitled
    python -m pipeline.assemble film           # both 16:9 and 9:16
    python -m pipeline.assemble film --aspect 9x16

What it does, in order:
 1. Timing. Each shot lasts max(storyboard duration, offset + narration + 0.6 s),
    so the picture is cut to the voice (narration-first, as editors do).
 2. Picture. Each clip is scaled to cover the frame, centre-cropped (keyframes are
    composed with the subject centred), composited with any shot overlays (0.3 s fade),
    trimmed or held on its last frame, then concatenated with the end card.
 3. Sound. Narration lines are placed at their shot start; the score is looped or
    trimmed to length, ducked under the voice with sidechaincompress, faded out
    over the end card, and the mix is loudness-normalised (two-pass loudnorm,
    -14 LUFS, -1 dBTP).
 4. Subtitles. English SRT from narration.text_en, at most two lines per cue,
    burned in and also written next to the MP4.
Outputs land in outputs/final/. Per-shot segments are cached by content hash.
"""
from __future__ import annotations

import argparse
import json
import re
import textwrap
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

from pipeline import placeholders
from pipeline.common import (OUTPUTS, ROOT, ffmpeg, file_hash, load_storyboard, main_guard,
                             media_duration, read_manifest, say, stable_hash)

STAGE = "assemble"
FINAL = OUTPUTS / "final"
WORK = OUTPUTS / "assembly"
ASPECTS = {"16x9": (1920, 1080), "9x16": (1080, 1920)}
SUB_WRAP = {"16x9": 42, "9x16": 28}
SUB_STYLE = {
    "16x9": "FontName=DejaVu Sans,FontSize=16,Outline=1.6,Shadow=0,MarginV=22,Alignment=2",
    "9x16": "FontName=DejaVu Sans,FontSize=9,Outline=1.0,Shadow=0,MarginV=60,Alignment=2",
}
TAIL_S = 0.6
DEFAULT_OFFSET = 0.3
GOLD = (212, 175, 55)  # #D4AF37
FONTS_DIR = ROOT / "assets" / "fonts"


def _work_dir() -> Path:
    return OUTPUTS / "assembly"


def _final_dir() -> Path:
    return OUTPUTS / "final"


# ---------------------------------------------------------------------- fonts

def has_ethiopic(text: str) -> bool:
    return any(0x1200 <= ord(ch) <= 0x137F for ch in text)


def ethiopic_font(size: int):
    for p in sorted(FONTS_DIR.glob("NotoSansEthiopic-*.ttf")):
        return ImageFont.truetype(str(p), size)
    for fallback in ("C:/Windows/Fonts/nyala.ttf", "C:/Windows/Fonts/ebrima.ttf"):
        if Path(fallback).exists():
            return ImageFont.truetype(fallback, size)
    return placeholders.font(size)


def font_for_text(text: str, size: int):
    return ethiopic_font(size) if has_ethiopic(text) else placeholders.font(size)


def _split_script_runs(text: str) -> list[tuple[str, bool]]:
    """Split text into contiguous (run, is_ethiopic) chunks so mixed lines never hit missing glyphs."""
    if not text:
        return []
    runs: list[tuple[str, bool]] = []
    cur = text[0]
    cur_eth = 0x1200 <= ord(cur) <= 0x137F
    for ch in text[1:]:
        is_eth = 0x1200 <= ord(ch) <= 0x137F
        if is_eth == cur_eth or ch == " ":
            cur += ch
        else:
            runs.append((cur, cur_eth))
            cur, cur_eth = ch, is_eth
    runs.append((cur, cur_eth))
    return runs


def _measure_runs(draw: ImageDraw.ImageDraw, text: str, size: int) -> tuple[float, list[tuple[str, ImageFont.FreeTypeFont, float]]]:
    measured = []
    total = 0.0
    for run, is_eth in _split_script_runs(text):
        f = ethiopic_font(size) if is_eth else placeholders.font(size)
        w = draw.textlength(run, font=f)
        measured.append((run, f, w))
        total += w
    return total, measured


def _draw_centered_runs(draw: ImageDraw.ImageDraw, cx: float, y: float, text: str, size: int, fill) -> None:
    total, measured = _measure_runs(draw, text, size)
    x = cx - total / 2.0
    for run, f, w in measured:
        draw.text((x, y), run, font=f, fill=fill)
        x += w


# ------------------------------------------------------------------- timeline

def timeline(sb: dict) -> list[dict]:
    narr = read_manifest("narration")
    t, rows = 0.0, []
    for s in sb["shots"]:
        n = narr.get(s["id"])
        offset = s["narration"].get("offset_s", DEFAULT_OFFSET)
        nd = n["duration_s"] if n else 0.0
        length = max(s["duration_s"], offset + nd + TAIL_S if n else 0)
        rows.append({
            "id": s["id"],
            "start": round(t, 3),
            "length": round(length, 3),
            "narration": (OUTPUTS / n["path"]) if n else None,
            "narr_start": round(t + offset, 3),
            "narr_dur": nd,
            "text_en": s["narration"]["text_en"],
        })
        t += length
    return rows


# ------------------------------------------------------------------ subtitles

def _ts(x: float) -> str:
    ms = int(round(x * 1000))
    h, ms = divmod(ms, 3_600_000)
    m, ms = divmod(ms, 60_000)
    s, ms = divmod(ms, 1000)
    return f"{h:02}:{m:02}:{s:02},{ms:03}"


def write_srt(rows: list[dict], path: Path, wrap: int) -> None:
    cues, i = [], 1
    for r in rows:
        if not r["narration"] or not r["text_en"].strip():
            continue
        lines = textwrap.wrap(r["text_en"], wrap)
        chunks = ["\n".join(lines[k:k + 2]) for k in range(0, len(lines), 2)]
        total_chars = sum(len(c) for c in chunks)
        t = r["narr_start"]
        for c in chunks:
            d = r["narr_dur"] * len(c) / total_chars
            cues.append(f"{i}\n{_ts(t)} --> {_ts(t + d)}\n{c}\n")
            t += d
            i += 1
    path.write_text("\n".join(cues), encoding="utf-8")


# ------------------------------------------------------------------- overlays

def render_overlay_png(ov: dict, aspect: str, out_path: Path) -> Path:
    """Render one overlay as a transparent RGBA PNG obeying safe-area margins."""
    w, h = ASPECTS[aspect]
    out_path.parent.mkdir(parents=True, exist_ok=True)
    img = Image.new("RGBA", (w, h), (0, 0, 0, 0))
    draw = ImageDraw.Draw(img)

    text = ov["text"]
    size = max(24, int(min(w, h) * 0.042))
    margin_x = int(w * 0.08)
    margin_y = int(h * 0.08)
    bottom_limit = int(h * (0.80 if aspect == "9x16" else 0.92))
    max_text_w = w - 2 * margin_x - 36

    # Wrap only if wider than safe width
    words = text.split()
    lines: list[str] = []
    cur = ""
    for word in words:
        cand = f"{cur} {word}".strip()
        cw, _ = _measure_runs(draw, cand, size)
        if cw <= max_text_w or not cur:
            cur = cand
        else:
            lines.append(cur)
            cur = word
    if cur:
        lines.append(cur)

    line_widths = [_measure_runs(draw, ln, size)[0] for ln in lines]
    block_w = max(line_widths) if line_widths else 0.0
    lh = int(size * 1.35)
    block_h = lh * len(lines)
    pad_x, pad_y = 26, 14
    box_w = min(w - 2 * margin_x, int(block_w + pad_x * 2))
    box_h = block_h + pad_y * 2

    pos = ov.get("position", "upper_third")
    anchor_y = {
        "top": int(h * 0.12),
        "upper_third": int(h * 0.24),
        "center": int(h * 0.50),
        "lower_third": int(h * (0.68 if aspect == "9x16" else 0.78)),
    }.get(pos, int(h * 0.24))

    box_top = max(margin_y, min(bottom_limit - box_h, anchor_y - box_h // 2))
    box_left = (w - box_w) // 2
    draw.rounded_rectangle(
        [box_left, box_top, box_left + box_w, box_top + box_h],
        radius=10,
        fill=(12, 10, 9, 178),
        outline=(*GOLD, 220),
        width=2,
    )
    y = box_top + pad_y
    for ln in lines:
        _draw_centered_runs(draw, w / 2.0, y, ln, size, (245, 235, 215, 255))
        y += lh

    img.save(out_path)
    return out_path


# -------------------------------------------------------------------- picture

def segment(
    clip: Path,
    length: float,
    aspect: str,
    fps: int,
    overlays: list[dict] | None = None,
    shot_id: str | None = None,
) -> Path:
    w, h = ASPECTS[aspect]
    ov_list = overlays or []
    sid = shot_id or clip.stem.split("__")[0]
    key = stable_hash({
        "clip": file_hash(clip),
        "len": length,
        "aspect": aspect,
        "fps": fps,
        "overlays": ov_list,
        "v": 3,
    })
    work = _work_dir()
    out = work / "segments" / aspect / f"{sid}__{key}.mp4"
    if out.exists():
        return out
    out.parent.mkdir(parents=True, exist_ok=True)
    src_len = media_duration(clip)
    trim_head = 0.25 if src_len > length + 0.5 else 0.0  # weak first frames, trim when we can afford it
    hold = max(0.0, length - (src_len - trim_head))
    base_vf = (
        f"[0:v]trim=start={trim_head},setpts=PTS-STARTPTS,fps={fps},"
        f"scale={w}:{h}:force_original_aspect_ratio=increase,crop={w}:{h},setsar=1,"
        f"tpad=stop_mode=clone:stop_duration={hold + 0.1:.3f},trim=duration={length:.3f}"
    )

    if not ov_list:
        vf = (
            f"trim=start={trim_head},setpts=PTS-STARTPTS,fps={fps},"
            f"scale={w}:{h}:force_original_aspect_ratio=increase,crop={w}:{h},setsar=1,"
            f"tpad=stop_mode=clone:stop_duration={hold + 0.1:.3f},trim=duration={length:.3f},format=yuv420p"
        )
        ffmpeg(["-i", str(clip), "-vf", vf, "-an", "-c:v", "libx264", "-preset", "veryfast", "-crf", "18",
                "-r", str(fps), str(out)])
        return out

    inputs = ["-i", str(clip)]
    filters = [f"{base_vf}[v0]"]
    for idx, ov in enumerate(ov_list, start=1):
        png = render_overlay_png(ov, aspect, work / "overlays" / aspect / f"{sid}__{key}_{idx - 1}.png")
        inputs += ["-loop", "1", "-t", f"{length:.3f}", "-i", str(png)]
        st = max(0.0, min(length, float(ov.get("start_s", 0.0))))
        en = max(st, min(length, float(ov.get("end_s", length))))
        fade_d = min(0.3, max(0.01, (en - st) / 2.0))
        fade_out_st = max(st, en - fade_d)
        filters.append(
            f"[{idx}:v]format=rgba,"
            f"fade=t=in:st={st:.3f}:d={fade_d:.3f}:alpha=1,"
            f"fade=t=out:st={fade_out_st:.3f}:d={fade_d:.3f}:alpha=1[ov{idx}]"
        )
        filters.append(
            f"[v{idx - 1}][ov{idx}]overlay=0:0:enable='between(t,{st:.3f},{en:.3f})':shortest=1[v{idx}]"
        )
    filters.append(f"[v{len(ov_list)}]format=yuv420p[vout]")
    ffmpeg(inputs + ["-filter_complex", ";".join(filters), "-map", "[vout]",
                     "-an", "-c:v", "libx264", "-preset", "veryfast", "-crf", "18", "-r", str(fps), str(out)])
    return out


def _draw_manuscript_border(draw: ImageDraw.ImageDraw, w: int, h: int) -> None:
    """Thin ornamental border in Ethiopian manuscript style (gold + crimson rules and corner motifs)."""
    m = int(min(w, h) * 0.045)
    gap = max(8, int(min(w, h) * 0.014))
    crimson = (138, 34, 34)
    lw = max(2, int(min(w, h) * 0.003))
    draw.rectangle([m, m, w - m, h - m], outline=GOLD, width=lw)
    m2 = m + gap
    draw.rectangle([m2, m2, w - m2, h - m2], outline=crimson, width=max(1, lw - 1))
    m3 = m2 + max(5, gap // 2)
    draw.rectangle([m3, m3, w - m3, h - m3], outline=GOLD, width=1)
    # Corner diamond/cross flourishes
    r = max(6, gap // 2)
    for cx, cy in ((m2, m2), (w - m2, m2), (m2, h - m2), (w - m2, h - m2)):
        draw.polygon([(cx, cy - r), (cx + r, cy), (cx, cy + r), (cx - r, cy)], fill=GOLD, outline=crimson)


def end_card(sb: dict, aspect: str, fps: int) -> Path:
    w, h = ASPECTS[aspect]
    ec = sb["end_card"]
    key = stable_hash({"ec": ec, "aspect": aspect, "fps": fps, "v": 2})
    out = _work_dir() / "segments" / aspect / f"endcard__{key}.mp4"
    if out.exists():
        return out
    out.parent.mkdir(parents=True, exist_ok=True)
    img = Image.new("RGB", (w, h), (12, 10, 9))
    d = ImageDraw.Draw(img)
    _draw_manuscript_border(d, w, h)

    size = int(min(w, h) * 0.042)
    wrap = 28 if aspect == "9x16" else 46
    blocks = [textwrap.wrap(l, wrap) for l in ec["lines"]]
    lines = [(ln, bi) for bi, b in enumerate(blocks) for ln in b]
    lh = int(size * 1.55)
    y = h // 2 - (len(lines) * lh + (len(blocks) - 1) * lh // 2) // 2
    last = None
    for ln, bi in lines:
        if last is not None and bi != last:
            y += lh // 2
        fsize = int(size * 1.25) if bi == 0 else size
        color = GOLD if bi == 0 else (218, 208, 192)
        _draw_centered_runs(d, w / 2.0, y, ln, fsize, color)
        y += lh
        last = bi
    png = out.with_suffix(".png")
    img.save(png)
    dur = ec["duration_s"]
    ffmpeg(["-loop", "1", "-i", str(png), "-vf", f"fps={fps},format=yuv420p,fade=t=in:d=0.5",
            "-t", f"{dur}", "-c:v", "libx264", "-preset", "veryfast", "-crf", "18", str(out)])
    return out


def black(seconds: float, aspect: str, fps: int) -> Path:
    w, h = ASPECTS[aspect]
    out = _work_dir() / f"black_{aspect}_{seconds:.2f}.mp4"
    if not out.exists():
        out.parent.mkdir(parents=True, exist_ok=True)
        ffmpeg(["-f", "lavfi", "-i", f"color=c=black:s={w}x{h}:r={fps}:d={seconds:.3f}",
                "-c:v", "libx264", "-preset", "veryfast", "-pix_fmt", "yuv420p", str(out)])
    return out


def concat(parts: list[Path], out: Path) -> None:
    lst = out.with_suffix(".txt")
    lst.write_text("".join(f"file '{p.resolve()}'\n" for p in parts))
    ffmpeg(["-f", "concat", "-safe", "0", "-i", str(lst), "-c", "copy", str(out)])


# ---------------------------------------------------------------------- sound

def mix_audio(sb: dict, rows: list[dict], total: float, score: Path, out: Path) -> dict:
    duck = {"threshold": 0.02, "ratio": 6, "attack_ms": 300, "release_ms": 1500, "music_gain_db": -6,
            **sb["music"].get("duck", {})}
    inputs, labels, filters = ["-i", str(score)], [], []
    for k, r in enumerate([r for r in rows if r["narration"]], start=1):
        inputs += ["-i", str(r["narration"])]
        ms = int(r["narr_start"] * 1000)
        filters.append(f"[{k}:a]aresample=48000,aformat=channel_layouts=mono,adelay={ms}:all=1[n{k}]")
        labels.append(f"[n{k}]")
    fade_st = max(0.0, total - sb["end_card"]["duration_s"] - 0.5)
    if labels:
        filters.append(f"{''.join(labels)}amix=inputs={len(labels)}:normalize=0,"
                       f"apad=whole_dur={total:.3f},atrim=0:{total:.3f},aformat=channel_layouts=stereo,"
                       f"asplit=2[voice][sc]")
    filters.append(f"[0:a]aresample=48000,aformat=channel_layouts=stereo,aloop=loop=-1:size=2e9,"
                   f"atrim=0:{total:.3f},volume={duck['music_gain_db']}dB,afade=t=in:d=1.5,"
                   f"afade=t=out:st={fade_st:.3f}:d={total - fade_st:.3f}[mus]")
    if labels:
        filters.append(f"[mus][sc]sidechaincompress=threshold={duck['threshold']}:ratio={duck['ratio']}:"
                       f"attack={duck['attack_ms']}:release={duck['release_ms']}[ducked]")
        filters.append("[ducked][voice]amix=inputs=2:normalize=0[mix]")
    else:
        filters.append("[mus]anull[mix]")
    pre = out.with_name(out.stem + "_premaster.wav")
    ffmpeg(inputs + ["-filter_complex", ";".join(filters), "-map", "[mix]", "-ar", "48000", str(pre)])

    # two-pass loudness normalisation to -14 LUFS / -1 dBTP
    import subprocess
    p = subprocess.run(["ffmpeg", "-nostdin", "-hide_banner", "-i", str(pre), "-af",
                        "loudnorm=I=-14:TP=-1:LRA=11:print_format=json", "-f", "null", "-"],
                       capture_output=True, text=True)
    m = json.loads(re.search(r"\{[^{}]*\"input_i\"[^{}]*\}", p.stderr, re.S).group(0))
    ln = (f"loudnorm=I=-14:TP=-1:LRA=11:measured_I={m['input_i']}:measured_TP={m['input_tp']}:"
          f"measured_LRA={m['input_lra']}:measured_thresh={m['input_thresh']}:offset={m['target_offset']}:linear=true")
    ffmpeg(["-i", str(pre), "-af", ln, "-ar", "48000", str(out)])
    return {"input_lufs": m["input_i"], "duck": duck}


# ----------------------------------------------------------------------- main

def render(sb: dict, mode: str, aspect: str) -> Path:
    fps = sb["defaults"]["fps"]
    rows = timeline(sb)
    body = sum(r["length"] for r in rows)
    total = body + sb["end_card"]["duration_s"]
    tag = "radio_cut" if mode == "radio" else f"heritage_{aspect}"
    work = _work_dir() / tag
    final_dir = _final_dir()
    work.mkdir(parents=True, exist_ok=True)
    final_dir.mkdir(parents=True, exist_ok=True)

    score_entry = read_manifest("music").get("score")
    if not score_entry:
        raise SystemExit("No score resolved. Run `python -m pipeline.music build` first.")
    score = OUTPUTS / score_entry["path"]

    if mode == "radio":
        parts = [black(body, aspect, fps), end_card(sb, aspect, fps)]
    else:
        shots_manifest = read_manifest("shots")
        by_id = {s["id"]: s for s in sb["shots"]}
        parts = []
        for r in rows:
            if r["id"] not in shots_manifest:
                raise SystemExit(f"No clip for {r['id']}. Run `python -m pipeline.shots build` first.")
            parts.append(segment(
                OUTPUTS / shots_manifest[r["id"]]["path"],
                r["length"],
                aspect,
                fps,
                overlays=by_id[r["id"]].get("overlays"),
                shot_id=r["id"],
            ))
        parts.append(end_card(sb, aspect, fps))
    video = work / "picture.mp4"
    concat(parts, video)

    audio = work / "mix.wav"
    info = mix_audio(sb, rows, total, score, audio)

    srt = final_dir / f"{tag}.en.srt"
    write_srt(rows, srt, SUB_WRAP[aspect])
    out = final_dir / f"{tag}.mp4"
    srt_esc = str(srt.resolve()).replace("\\", "/").replace(":", "\\:").replace("'", "\\'")
    vf = f"subtitles='{srt_esc}':force_style='{SUB_STYLE[aspect]}',fade=t=in:d=0.6"
    ffmpeg(["-i", str(video), "-i", str(audio), "-vf", vf, "-map", "0:v", "-map", "1:a",
            "-c:v", "libx264", "-preset", "medium", "-crf", "20", "-pix_fmt", "yuv420p", "-r", str(fps),
            "-c:a", "aac", "-b:a", "192k", "-movflags", "+faststart", "-t", f"{total:.3f}", str(out)])
    (work / "timeline.json").write_text(json.dumps(
        {"total_s": total, "rows": [{**r, "narration": str(r["narration"]) if r["narration"] else None} for r in rows],
         "score": score.name, "mix": info}, indent=2))
    sources = {}
    if mode != "radio":
        for v in read_manifest("shots").values():
            sources[v.get("source", "?")] = sources.get(v.get("source", "?"), 0) + 1
    say(STAGE, f"{out.name}: {media_duration(out):.1f}s, score {score.name}"
               + (f", shots {sources}" if sources else ""))
    if not 60 <= total <= 90:
        say(STAGE, f"WARNING: {total:.1f}s is outside the 60-90 s brief")
    return out


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("mode", choices=["radio", "film"])
    ap.add_argument("--aspect", choices=["16x9", "9x16", "both"], default=None)
    ap.add_argument("--storyboard", default="storyboard.json")
    a = ap.parse_args()
    sb = load_storyboard(a.storyboard)
    aspects = {"radio": ["16x9"], "film": ["16x9", "9x16"]}[a.mode] if a.aspect in (None, "both") else [a.aspect]
    for asp in aspects:
        render(sb, a.mode, asp)
    return 0


if __name__ == "__main__":
    main_guard(main)
