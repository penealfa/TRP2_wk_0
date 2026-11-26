"""Call each hosted provider once and log latency to runs.jsonl, to learn early
what works with your keys today. Also lists the Gemini models your key can see
that support TTS, since free-tier model ids change.

    python -m pipeline.smoke_test
"""
from __future__ import annotations

import os

from pipeline.common import OUTPUTS, RetryableError, call_with_retries, main_guard
from pipeline.providers import (TTS_VOICE_SHAPE, cloudflare_image, gemini_list_models,
                                gemini_list_voices, gemini_text, gemini_tts)

TEXT_MODEL = os.environ.get("HS_TEXT_MODEL", "gemini-3.8-flash")


def check(name, fn):
    try:
        r = fn()
        print(f"  OK    {name} ({r.latency_s:.1f}s)")
        return True
    except Exception as e:  # report and carry on so every provider gets tested
        print(f"  FAIL  {name}: {str(e)[:200]}")
        return False


def main() -> int:
    from pipeline.common import load_storyboard
    sb = load_storyboard()
    out = OUTPUTS / "smoke"
    out.mkdir(parents=True, exist_ok=True)
    print("Gemini models visible to this key:")
    try:
        models = gemini_list_models()
        tts = [m for m in models if "tts" in m]
        print("  TTS:  ", ", ".join(tts) or "none found")
        print("  text: ", ", ".join(m for m in models if "flash" in m and "tts" not in m)[:400])
    except Exception as e:
        print("  could not list models:", e)
    try:
        voices = gemini_list_voices()
        print("  TTS voices:", ", ".join(voices) or "none found")
    except Exception as e:
        print("  could not list voices:", e)

    ok = [
        check(f"gemini text ({TEXT_MODEL})", lambda: call_with_retries(
            lambda: gemini_text(TEXT_MODEL, "Reply with the single word: ready"), stage="smoke",
            provider="gemini", model=TEXT_MODEL, params={}, attempts=2)),
    ]

    def tts():
        wav = gemini_tts(sb["defaults"]["tts_model"], "Selam. This is a test.",
                         sb["defaults"]["voice"], sb["defaults"].get("tts_style", ""))
        (out / "tts.wav").write_bytes(wav)
        return str(out / "tts.wav")
    ok.append(check(f"gemini tts ({sb['defaults']['tts_model']})", lambda: call_with_retries(
        tts, stage="smoke", provider="gemini", model=sb["defaults"]["tts_model"],
        params={"voice_shape": TTS_VOICE_SHAPE}, attempts=2)))

    model = os.environ.get("HS_KEYFRAME_MODEL", sb["defaults"]["keyframe_model"])

    def img():
        (out / "image.png").write_bytes(cloudflare_image(model, "a clay coffee pot on a wooden tray, morning light",
                                                         seed=1, width=512, height=512))
        return str(out / "image.png")
    ok.append(check(f"cloudflare image ({model})", lambda: call_with_retries(
        img, stage="smoke", provider="cloudflare", model=model, params={"w": 512, "h": 512}, seed=1, attempts=2)))

    print(f"{sum(ok)}/{len(ok)} providers OK. Kaggle (Wan 2.2, ACE-Step) is tested by running its notebook.")
    print(f"Outputs in {out}; latencies in runs.jsonl")
    return 0 if all(ok) else 1


if __name__ == "__main__":
    main_guard(main)
