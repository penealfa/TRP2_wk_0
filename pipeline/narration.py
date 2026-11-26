"""Stage: narration. One WAV per shot.

    python -m pipeline.narration [--only s03 s07] [--force] [--source tts]

Two sources, chosen by defaults.narration_source in storyboard.json:

  recorded  The narrator's own takes, recordings/<shot_id>.<wav|m4a|mp3|ogg|flac>.
            Each is cleaned (high-pass, leading/trailing silence trimmed,
            loudness-normalised) into outputs/narration/. A line with no
            recording falls back to Gemini TTS, so the film always assembles.
  tts       Gemini TTS for every line.

Cache key = (recording file hash | TTS model + voice + style + text) + language.
Re-record one line, or edit one line of text, and only that shot is redone.
"""
from __future__ import annotations

import argparse
import time
from pathlib import Path

from pipeline import placeholders
from pipeline.common import (ROOT, StageCache, call_with_retries, ffmpeg, file_hash, load_storyboard,
                             log_call, main_guard, media_duration, offline, say, select_shots,
                             stable_hash, RetryableError)
from pipeline.providers import TTS_VOICE_SHAPE, gemini_tts

STAGE = "narration"
RECORDINGS = ROOT / "recordings"
AUDIO_EXTS = ("wav", "m4a", "mp3", "ogg", "flac", "aac", "opus")
TRIM_CHAIN = ("highpass=f=80,"
              "silenceremove=start_periods=1:start_threshold=-45dB:start_silence=0.15,"
              "areverse,silenceremove=start_periods=1:start_threshold=-45dB:start_silence=0.25,areverse")
# loudnorm runs as a separate pass: chained after areverse in one graph, FFmpeg 6.1 can hang
# on takes with no silence at all (see FAILURES.md F0)
LEVEL_CHAIN = "loudnorm=I=-16:TP=-1.5:LRA=11"
CLEAN_VERSION = "v2"  # bump when CLEAN_CHAIN changes so every take is reprocessed


def find_recording(shot_id: str) -> Path | None:
    for ext in AUDIO_EXTS:
        p = RECORDINGS / f"{shot_id}.{ext}"
        if p.exists() and p.stat().st_size > 0:
            return p
    return None


def process_recording(src: Path, out: Path, sid: str, key: str) -> None:
    t0 = time.monotonic()
    trimmed = out.with_name(out.stem + "_trim.wav")
    ffmpeg(["-i", str(src), "-af", TRIM_CHAIN, "-ar", "48000", "-ac", "1", str(trimmed)])
    ffmpeg(["-i", str(trimmed), "-af", LEVEL_CHAIN, "-ar", "48000", "-ac", "1", str(out)])
    trimmed.unlink(missing_ok=True)
    log_call(stage=STAGE, provider="human", model="recorded-voice", item_id=sid, cache_key=key,
             params={"source": src.name, "clean": CLEAN_VERSION}, latency_s=time.monotonic() - t0,
             status="ok", output=str(out.name))


def speaker_config(sb: dict, shot: dict, source_override: str | None = None) -> tuple[str | None, str, str, str]:
    """Resolve (speaker, source, voice, style) for one shot using defaults.voices if present."""
    d = sb["defaults"]
    voices = d.get("voices")
    speaker = shot["narration"].get("speaker")
    if voices and speaker in voices:
        cfg = voices[speaker]
        src = source_override or cfg.get("source") or d.get("narration_source", "tts")
        voice = cfg.get("voice") or d["voice"]
        style = shot["narration"].get("style") or cfg.get("style") or d.get("tts_style", "")
    else:
        src = source_override or d.get("narration_source", "tts")
        voice = d["voice"]
        style = shot["narration"].get("style") or d.get("tts_style", "")
    return speaker, src, voice, style


def run(sb: dict, only=None, force: bool = False, source: str | None = None) -> dict[str, float]:
    cache = StageCache(STAGE)
    model = "placeholder-tone" if offline() else sb["defaults"]["tts_model"]
    durations: dict[str, float] = {}
    missing: list[str] = []
    for shot in select_shots(sb, only):
        sid, text = shot["id"], shot["narration"]["text"].strip()
        if not text:
            say(STAGE, f"{sid}: no narration")
            continue
        speaker, eff_source, voice, style = speaker_config(sb, shot, source)
        rec = find_recording(sid) if eff_source == "recorded" else None
        if rec:
            key = stable_hash({"rec": file_hash(rec), "clean": CLEAN_VERSION})
            out = cache.get(sid, key, "wav")
            if out and not force:
                say(STAGE, f"{sid}: cached recording ({key})")
            else:
                out = cache.path_for(sid, key, "wav")
                process_recording(rec, out, sid, key)
                say(STAGE, f"{sid}: processed {rec.name} -> {out.name}")
            durations[sid] = media_duration(out)
            cache.record(sid, key, out, duration_s=round(durations[sid], 3), text=text,
                         speaker=speaker, source="recorded")
            continue
        if eff_source == "recorded":
            missing.append(sid)
        key = stable_hash({
            "model": model,
            "speaker": speaker,
            "voice": voice,
            "style": style,
            "text": text,
            "lang": sb["language"]["code"],
        })
        out = cache.get(sid, key, "wav")
        if out and not force:
            say(STAGE, f"{sid}: cached ({key})")
        else:
            out = cache.path_for(sid, key, "wav")
            def call():
                if offline():
                    placeholders.speech(out, text)
                    return out
                wav_bytes = gemini_tts(model, text, voice, style, speaker=speaker)
                out.write_bytes(wav_bytes)
                return out
            try:
                call_with_retries(
                    call,
                    stage=STAGE,
                    provider="offline" if offline() else "gemini",
                    model=model,
                    params={
                        "speaker": speaker,
                        "voice": voice,
                        "style": style,
                        "chars": len(text),
                        "lang": sb["language"]["code"],
                        "voice_shape": TTS_VOICE_SHAPE,
                    },
                    item_id=sid,
                    cache_key=key,
                )
            except RetryableError as e:
                if "429" in str(e):
                    raise RetryableError(
                        f"Gemini TTS quota exceeded (429) on {sid}; wait a moment and re-run or switch GEMINI_API_KEY in .env ({e})"
                    ) from e
                raise
            say(STAGE, f"{sid}: generated -> {out.name}")
        durations[sid] = media_duration(out)
        cache.record(sid, key, out, duration_s=round(durations[sid], 3), text=text,
                     speaker=speaker, voice=voice, source="tts")
    if missing:
        say(STAGE, f"no recording yet for {', '.join(missing)}: used TTS as a stand-in")
    return durations


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--storyboard", default="storyboard.json")
    ap.add_argument("--only", nargs="*")
    ap.add_argument("--force", action="store_true")
    ap.add_argument("--source", choices=["recorded", "tts"], help="override defaults.narration_source")
    a = ap.parse_args()
    d = run(load_storyboard(a.storyboard), a.only, a.force, a.source)
    say(STAGE, f"total narration {sum(d.values()):.1f}s over {len(d)} lines")
    return 0


if __name__ == "__main__":
    main_guard(main)
