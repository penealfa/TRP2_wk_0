"""E8: how much does the score duck under the voice, for three sidechain settings?

Re-runs the mix's own sidechain filter (pipeline/assemble.py: mix_audio) on the real score and the real
narration of the 16:9 film, once per setting, and measures the RMS level of the *ducked score alone*
inside speech windows and inside gaps, against the same score with no ducking.

    python experiments/e8_ducking.py        # needs FFmpeg on PATH and a built film (outputs/assembly/heritage_16x9)
Writes experiments/e8_ducking.json.
"""
import json
import subprocess
import sys
import tempfile
import wave
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
from pipeline.common import OUTPUTS, load_storyboard  # noqa: E402

VARIANTS = {
    "A brief default (thr 0.02, ratio 6, attack 300 ms, release 1500 ms)": dict(threshold=0.02, ratio=6, attack=300, release=1500),
    "B lower threshold, faster release (thr 0.01, ratio 6, attack 150 ms, release 800 ms)": dict(threshold=0.01, ratio=6, attack=150, release=800),
    "C stronger ratio (thr 0.02, ratio 12, attack 300 ms, release 1500 ms)": dict(threshold=0.02, ratio=12, attack=300, release=1500),
}
SR = 48000


def rms_db(x: np.ndarray) -> float:
    return float(20 * np.log10(np.sqrt(np.mean(x.astype(np.float64) ** 2)) + 1e-12)) if x.size else float("nan")


def read_wav(path: Path) -> np.ndarray:
    with wave.open(str(path)) as w:
        a = np.frombuffer(w.readframes(w.getnframes()), dtype=np.int16).astype(np.float32) / 32768
        return a.reshape(-1, w.getnchannels()).mean(axis=1)


def main() -> None:
    sb = load_storyboard()
    tl = json.loads((OUTPUTS / "assembly" / "heritage_16x9" / "timeline.json").read_text(encoding="utf-8"))
    total, rows = tl["total_s"], [r for r in tl["rows"] if r["narration"]]
    score = OUTPUTS / "music" / tl["score"]
    gain = sb["music"].get("duck", {}).get("music_gain_db", -6)
    end_card = sb["end_card"]["duration_s"]
    windows = []
    for r in rows:
        with wave.open(r["narration"], "rb") as w:
            windows.append((r["narr_start"], r["narr_start"] + w.getnframes() / w.getframerate()))
    speech = np.zeros(int(total * SR), dtype=bool)
    for a, b in windows:
        speech[int(a * SR):int(b * SR)] = True
    valid = np.zeros_like(speech)
    valid[int(2 * SR):int((total - end_card - 1) * SR)] = True      # skip the fade in and the end-card fade out
    out = {"score": tl["score"], "total_s": total, "speech_s": round(float(speech.sum()) / SR, 1), "variants": {}}
    with tempfile.TemporaryDirectory() as tmp:
        for name, p in VARIANTS.items():
            inputs, labels, filt = ["-i", str(score)], [], []
            for k, r in enumerate(rows, start=1):
                inputs += ["-i", r["narration"]]
                filt.append(f"[{k}:a]aresample=48000,aformat=channel_layouts=mono,adelay={int(r['narr_start'] * 1000)}:all=1[n{k}]")
                labels.append(f"[n{k}]")
            filt.append(f"{''.join(labels)}amix=inputs={len(labels)}:normalize=0,apad=whole_dur={total:.3f},atrim=0:{total:.3f},"
                        f"aformat=channel_layouts=stereo[sc]")
            filt.append(f"[0:a]aresample=48000,aformat=channel_layouts=stereo,aloop=loop=-1:size=2e9,atrim=0:{total:.3f},"
                        f"volume={gain}dB,asplit=2[mus][mus2]")
            filt.append(f"[mus][sc]sidechaincompress=threshold={p['threshold']}:ratio={p['ratio']}:"
                        f"attack={p['attack']}:release={p['release']}[ducked]")
            d, u = Path(tmp) / "ducked.wav", Path(tmp) / "plain.wav"
            subprocess.run(["ffmpeg", "-nostdin", "-v", "error", "-y", *inputs, "-filter_complex", ";".join(filt),
                            "-map", "[ducked]", "-ar", str(SR), "-ac", "1", str(d),
                            "-map", "[mus2]", "-ar", str(SR), "-ac", "1", str(u)], check=True)
            dk, pl = read_wav(d), read_wav(u)
            n = min(len(dk), len(pl), len(speech))
            sp, gp, v = speech[:n] & valid[:n], ~speech[:n] & valid[:n], valid[:n]
            res = {
                "music_under_speech_vs_undipped_db": round(rms_db(dk[:n][sp]) - rms_db(pl[:n][sp]), 2),
                "music_in_gaps_vs_undipped_db": round(rms_db(dk[:n][gp]) - rms_db(pl[:n][gp]), 2),
                "music_rms_under_speech_db": round(rms_db(dk[:n][sp]), 2),
                "music_rms_in_gaps_db": round(rms_db(dk[:n][gp]), 2),
            }
            out["variants"][name] = res
            print(name, res)
    (ROOT / "experiments" / "e8_ducking.json").write_text(json.dumps(out, indent=2, ensure_ascii=False), encoding="utf-8")


if __name__ == "__main__":
    main()
