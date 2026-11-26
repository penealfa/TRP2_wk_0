"""Unit tests for P1: per-speaker routing and Gemini 3.8 Flash TTS client."""
import base64
import copy
import io
import json
import subprocess
import wave

import pytest

from pipeline import narration, providers
from pipeline.common import FatalError, RetryableError, StoryboardError, load_storyboard


def _make_wav_bytes(freq: int = 440, frames: int = 2400) -> bytes:
    buf = io.BytesIO()
    with wave.open(buf, "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(24000)
        w.writeframes(b"\x01\x00" * frames)
    return buf.getvalue()


class _FakeResp:
    def __init__(self, status_code: int, payload: dict):
        self.status_code = status_code
        self._payload = payload
        self.text = json.dumps(payload)

    def json(self):
        return self._payload


def test_gemini_tts_riff_passthrough_and_pcm_wrapping(monkeypatch):
    monkeypatch.setenv("GEMINI_API_KEY", "test-key")
    riff = _make_wav_bytes()
    b64_riff = base64.b64encode(riff).decode("ascii")
    sent_bodies = []

    def fake_post(url, headers=None, json=None, timeout=None):
        sent_bodies.append(json)
        return _FakeResp(200, {
            "candidates": [{"content": {"parts": [{"inlineData": {"mimeType": "audio/wav", "data": b64_riff}}]}}]
        })

    monkeypatch.setattr(providers.requests, "post", fake_post)
    out_riff = providers.gemini_tts("gemini-3.8-flash-tts", "ሰላም", "Kore", "warmly", speaker="wolf")
    assert out_riff == riff, "RIFF bytes must pass through unchanged without double WAV header"
    part = sent_bodies[0]["contents"][0]["parts"][0]
    assert part["speech_metadata"] == {"speaker": "wolf", "style": "warmly"}

    raw_pcm = b"\x02\x00" * 2400
    b64_pcm = base64.b64encode(raw_pcm).decode("ascii")

    def fake_post_pcm(url, headers=None, json=None, timeout=None):
        return _FakeResp(200, {
            "candidates": [{"content": {"parts": [{"inlineData": {"mimeType": "audio/l16", "data": b64_pcm}}]}}]
        })

    monkeypatch.setattr(providers.requests, "post", fake_post_pcm)
    out_wav = providers.gemini_tts("gemini-3.8-flash-tts", "ሰላም", "Kore", "warmly")
    assert out_wav[:4] == b"RIFF"
    with wave.open(io.BytesIO(out_wav), "rb") as w:
        assert (w.getnchannels(), w.getsampwidth(), w.getframerate()) == (1, 2, 24000)
        assert w.readframes(w.getnframes()) == raw_pcm


def test_gemini_tts_errors(monkeypatch):
    monkeypatch.setenv("GEMINI_API_KEY", "test-key")
    monkeypatch.setattr(
        providers.requests, "post",
        lambda *a, **kw: _FakeResp(200, {"candidates": [{"content": {"parts": [{"text": "oops"}]}}]}),
    )
    with pytest.raises(RetryableError):
        providers.gemini_tts("gemini-3.8-flash-tts", "hello", "Kore", "calm")

    monkeypatch.setattr(
        providers.requests, "post",
        lambda *a, **kw: _FakeResp(200, {"promptFeedback": {"blockReason": "SAFETY"}}),
    )
    with pytest.raises(FatalError, match="SAFETY"):
        providers.gemini_tts("gemini-3.8-flash-tts", "hello", "Kore", "calm")


def test_per_speaker_routing_and_style_invalidation(sandbox, monkeypatch):
    import pipeline.common as c
    monkeypatch.setenv("HS_OFFLINE", "0")
    monkeypatch.setenv("GEMINI_API_KEY", "test-key")
    wav_bytes = _make_wav_bytes()
    monkeypatch.setattr(narration, "gemini_tts", lambda model, text, voice, style="", speaker=None: wav_bytes)

    sb = load_storyboard()
    sb["defaults"]["voices"]["peneal"] = {"source": "recorded"}

    for sid in ("s01", "s03", "s05", "s10", "s11"):
        rec = sandbox / "recordings" / f"{sid}.wav"
        subprocess.run(["ffmpeg", "-nostdin", "-y", "-loglevel", "error", "-f", "lavfi",
                        "-i", "sine=f=220:d=1.2", str(rec)], check=True)

    narration.run(sb)
    manifest = json.loads((c.OUTPUTS / "narration" / "manifest.json").read_text())
    assert manifest["s01"]["source"] == "recorded"
    assert manifest["s02"]["source"] == "tts"

    runs1 = [json.loads(l) for l in c.RUNS_LOG.read_text().splitlines()]
    assert len(runs1) == len(sb["shots"])

    # Changing voices.wolf.style must regenerate ONLY wolf lines
    sb2 = copy.deepcopy(sb)
    sb2["defaults"]["voices"]["wolf"]["style"] = "Speak slowly with a faint echo:"
    narration.run(sb2)
    new_runs = [json.loads(l) for l in c.RUNS_LOG.read_text().splitlines()][len(runs1):]
    wolf_shots = sorted(s["id"] for s in sb["shots"] if s["narration"].get("speaker") == "wolf")
    assert sorted(r["item"] for r in new_runs) == wolf_shots


def test_unknown_speaker_fails_validation(tmp_path):
    d = load_storyboard()
    d["shots"][0]["narration"]["speaker"] = "ghost"
    p = tmp_path / "sb.json"
    p.write_text(json.dumps(d), encoding="utf-8")
    with pytest.raises(StoryboardError, match="ghost"):
        load_storyboard(p)

    d2 = load_storyboard()
    d2["defaults"]["voices"]["nobody"] = {"source": "tts"}
    p2 = tmp_path / "sb2.json"
    p2.write_text(json.dumps(d2), encoding="utf-8")
    with pytest.raises(StoryboardError, match="nobody"):
        load_storyboard(p2)
