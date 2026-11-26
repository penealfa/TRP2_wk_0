"""Thin clients for the hosted providers. Each function makes exactly one HTTP
call and raises RetryableError / FatalError so callers can wrap it in
call_with_retries (which does the logging). Keys come from the environment and
never reach runs.jsonl.

Free-tier model names move often. Every model id is read from storyboard.json
or an environment variable, so swapping a model never needs a code change.
"""
from __future__ import annotations

import base64
import io
import os
import wave

import requests

from pipeline.common import FatalError, RetryableError, classify_http

GEMINI_BASE = "https://generativelanguage.googleapis.com/v1beta"
TIMEOUT = float(os.environ.get("HS_HTTP_TIMEOUT", "180"))
FLUX2_MAX_REFS = 4
TTS_VOICE_SHAPE = "speechConfig.voiceConfig.prebuiltVoiceConfig+speech_metadata"


def _post(url: str, **kw) -> requests.Response:
    try:
        resp = requests.post(url, timeout=TIMEOUT, **kw)
    except (requests.Timeout, requests.ConnectionError) as e:
        raise RetryableError(f"network: {e}") from e
    classify_http(resp)
    return resp


# ------------------------------------------------------------------------ Gemini

def _gemini_headers() -> dict:
    key = os.environ.get("GEMINI_API_KEY")
    if not key:
        raise FatalError("GEMINI_API_KEY is not set")
    return {"x-goog-api-key": key, "Content-Type": "application/json"}


def gemini_text(model: str, prompt: str, *, json_mode: bool = False, temperature: float = 0.7) -> str:
    body: dict = {
        "contents": [{"role": "user", "parts": [{"text": prompt}]}],
        "generationConfig": {"temperature": temperature},
    }
    if json_mode:
        body["generationConfig"]["responseMimeType"] = "application/json"
    resp = _post(f"{GEMINI_BASE}/models/{model}:generateContent", headers=_gemini_headers(), json=body)
    data = resp.json()
    try:
        return "".join(p.get("text", "") for p in data["candidates"][0]["content"]["parts"])
    except (KeyError, IndexError) as e:
        raise FatalError(f"unexpected Gemini response (blocked?): {str(data)[:400]}") from e


def _pcm_to_wav(pcm: bytes, *, channels: int = 1, sample_width: int = 2, rate: int = 24000) -> bytes:
    buf = io.BytesIO()
    with wave.open(buf, "wb") as w:
        w.setnchannels(channels)
        w.setsampwidth(sample_width)
        w.setframerate(rate)
        w.writeframes(pcm)
    return buf.getvalue()


def _check_gemini_safety(data: dict) -> None:
    block = (data.get("promptFeedback") or {}).get("blockReason")
    if block:
        raise FatalError(f"Gemini safety block: {block}")
    cands = data.get("candidates") or []
    if cands:
        finish = cands[0].get("finishReason")
        if finish in ("SAFETY", "PROHIBITED_CONTENT", "RECITATION", "BLOCKLIST", "SPII"):
            raise FatalError(f"Gemini safety block: {finish}")


def gemini_tts(
    model: str,
    text: str,
    voice: str,
    style: str = "",
    *,
    speaker: str | None = None,
) -> bytes:
    """Call Gemini TTS and return complete WAV bytes.

    Gemini 3.8 Flash TTS unary responses return audio/wav (starting with b'RIFF') by
    default; raw PCM (audio/l16) is wrapped as 24 kHz 16-bit mono WAV.
    """
    part: dict = {"text": text}
    meta = {k: v for k, v in (("speaker", speaker), ("style", style)) if v}
    if meta:
        part["speech_metadata"] = meta
    body = {
        "contents": [{"role": "user", "parts": [part]}],
        "generationConfig": {
            "responseModalities": ["AUDIO"],
            "speechConfig": {"voiceConfig": {"prebuiltVoiceConfig": {"voiceName": voice}}},
        },
    }
    resp = _post(f"{GEMINI_BASE}/models/{model}:generateContent", headers=_gemini_headers(), json=body)
    data = resp.json()
    _check_gemini_safety(data)
    audio_b64 = None
    try:
        for p in data["candidates"][0]["content"]["parts"]:
            inline = p.get("inlineData") or p.get("inline_data")
            if inline and inline.get("data"):
                audio_b64 = inline["data"]
                break
    except (KeyError, IndexError, TypeError):
        audio_b64 = None
    if not audio_b64:
        raise RetryableError(f"TTS returned no audio: {str(data)[:300]}")
    raw = base64.b64decode(audio_b64)
    if raw[:4] == b"RIFF":
        return raw
    return _pcm_to_wav(raw)


def gemini_list_models() -> list[str]:
    try:
        resp = requests.get(f"{GEMINI_BASE}/models?pageSize=200", headers=_gemini_headers(), timeout=TIMEOUT)
    except (requests.Timeout, requests.ConnectionError) as e:
        raise RetryableError(str(e)) from e
    classify_http(resp)
    return [m["name"].removeprefix("models/") for m in resp.json().get("models", [])]


def gemini_list_voices() -> list[str]:
    """List available Gemini TTS voice names from GET /v1beta/voices."""
    try:
        resp = requests.get(f"{GEMINI_BASE}/voices", headers=_gemini_headers(), timeout=TIMEOUT)
    except (requests.Timeout, requests.ConnectionError) as e:
        raise RetryableError(str(e)) from e
    classify_http(resp)
    names = []
    for v in resp.json().get("voices", []):
        name = v.get("name") or v.get("voiceId") or v.get("id") or ""
        if name:
            names.append(name.removeprefix("voices/"))
    return names


# ------------------------------------------------------------ Cloudflare Workers AI

def _cf() -> tuple[str, dict]:
    acct, tok = os.environ.get("CLOUDFLARE_ACCOUNT_ID"), os.environ.get("CLOUDFLARE_API_TOKEN")
    if not acct or not tok:
        raise FatalError("CLOUDFLARE_ACCOUNT_ID / CLOUDFLARE_API_TOKEN not set")
    return f"https://api.cloudflare.com/client/v4/accounts/{acct}/ai/run/", {"Authorization": f"Bearer {tok}"}


def cloudflare_text(model: str, prompt: str, *, temperature: float = 0.4, max_tokens: int = 4096) -> str:
    """One chat completion from a Workers AI text model (free daily neuron allowance).
    Response shapes differ by model family, so both the legacy `response` field and the
    OpenAI-style `choices` are read."""
    url, headers = _cf()
    body = {"messages": [{"role": "user", "content": prompt}], "temperature": temperature,
            "max_tokens": max_tokens}
    resp = _post(url + model, headers={**headers, "Content-Type": "application/json"}, json=body)
    data = resp.json()
    if data.get("success") is False:
        raise FatalError(f"Cloudflare text error: {str(data.get('errors'))[:300]}")
    res = data.get("result") or {}
    text = res.get("response")
    if text is None and res.get("choices"):
        text = (res["choices"][0].get("message") or {}).get("content")
    if not isinstance(text, str) or not text.strip():
        raise FatalError(f"empty Cloudflare text response: {str(data)[:300]}")
    return text


def cloudflare_image(model: str, prompt: str, *, seed: int, width: int, height: int,
                     steps: int | None = None, reference_images: list[bytes] | None = None) -> bytes:
    """Generate one image. FLUX.1 schnell takes JSON (prompt, steps<=8, seed).

    FLUX.2 klein schema verified against:
    https://developers.cloudflare.com/workers-ai/models/flux-2-klein-4b/
    - Content-Type: multipart/form-data
    - Form fields: prompt, width, height, seed, and up to 4 reference images
      (input_image_0 .. input_image_3). `steps` is fixed at 4 on flux-2-klein
      and cannot be overridden via the API schema.
    - Response JSON: {"success": true, "result": {"image": "<base64>"}}.
    """
    base, headers = _cf()
    url = base + model
    if "flux-2" in model:
        form = {"prompt": (None, prompt), "seed": (None, str(seed)),
                "width": (None, str(width)), "height": (None, str(height))}
        if steps and "klein" not in model:
            form["steps"] = (None, str(steps))
        for i, img in enumerate((reference_images or [])[:FLUX2_MAX_REFS]):
            form[f"input_image_{i}"] = (f"ref{i}.png", img, "image/png")
        resp = _post(url, headers=headers, files=form)
    else:
        body = {"prompt": prompt, "seed": seed, "steps": min(steps or 4, 8)}
        resp = _post(url, headers=headers, json=body)
    ctype = resp.headers.get("content-type", "")
    if ctype.startswith("image/"):
        return resp.content
    data = resp.json()
    if not data.get("success", True):
        raise FatalError(f"Workers AI error: {data.get('errors')}")
    img = (data.get("result") or {}).get("image")
    if not img:
        raise RetryableError(f"Workers AI returned no image: {str(data)[:300]}")
    return base64.b64decode(img)
