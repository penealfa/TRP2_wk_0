"""Shared helpers for every stage: storyboard loading and validation, content-hash
caching, retries, and the runs.jsonl model-call log.

Nothing in here talks to a model provider. Stages import these helpers so that
caching, logging and failure handling behave the same way everywhere.
"""
from __future__ import annotations

import hashlib
import json
import os
import random
import shutil
import subprocess
import sys
import time
import uuid
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Iterable

import jsonschema

ROOT = Path(__file__).resolve().parent.parent
SCHEMA_PATH = ROOT / "schema" / "storyboard.schema.json"
DEFAULT_STORYBOARD = ROOT / "storyboard.json"
OUTPUTS = Path(os.environ.get("HS_OUTPUTS", ROOT / "outputs"))
RUNS_LOG = Path(os.environ.get("HS_RUNS_LOG", ROOT / "runs.jsonl"))


# --------------------------------------------------------------------------- env

def load_env(path: Path = ROOT / ".env") -> None:
    """Minimal .env loader (KEY=VALUE lines). Real environment variables win."""
    if not path.exists():
        return
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        os.environ.setdefault(key.strip(), value.strip().strip('"').strip("'"))


def offline() -> bool:
    """HS_OFFLINE=1 swaps every provider for a local placeholder generator, so the
    whole pipeline (and the tests) run with no keys and no network."""
    return os.environ.get("HS_OFFLINE", "0") not in ("", "0", "false", "False")


def require_env(*names: str) -> dict[str, str]:
    missing = [n for n in names if not os.environ.get(n)]
    if missing:
        raise SystemExit(
            f"Missing {', '.join(missing)}. Copy .env.example to .env and fill it in, "
            "or run with HS_OFFLINE=1 for placeholder output."
        )
    return {n: os.environ[n] for n in names}


# -------------------------------------------------------------------- storyboard

class StoryboardError(ValueError):
    pass


def load_storyboard(path: Path | str = DEFAULT_STORYBOARD) -> dict:
    """Load storyboard.json and validate it against the JSON Schema plus the
    cross-reference rules a schema cannot express. Raises StoryboardError."""
    path = Path(path)
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as e:
        raise StoryboardError(f"{path}: not valid JSON: {e}") from e
    schema = json.loads(SCHEMA_PATH.read_text(encoding="utf-8"))
    validator = jsonschema.Draft202012Validator(schema)
    errors = sorted(validator.iter_errors(data), key=lambda e: list(e.absolute_path))
    if errors:
        lines = [f"  - {'/'.join(map(str, e.absolute_path)) or '<root>'}: {e.message}" for e in errors]
        raise StoryboardError(f"{path} fails the schema:\n" + "\n".join(lines))

    problems: list[str] = []
    shot_ids = [s["id"] for s in data["shots"]]
    dupes = {i for i in shot_ids if shot_ids.count(i) > 1}
    if dupes:
        problems.append(f"duplicate shot ids: {sorted(dupes)}")
    char_ids = {c["id"] for c in data["characters"]}
    cue_ids = {c["id"] for c in data["music"]["cues"]}
    for s in data["shots"]:
        for c in s.get("characters", []):
            if c not in char_ids:
                problems.append(f"shot {s['id']} uses unknown character '{c}'")
        if "music_cue" in s and s["music_cue"] not in cue_ids:
            problems.append(f"shot {s['id']} uses unknown music cue '{s['music_cue']}'")
    for cue in data["music"]["cues"]:
        if cue["starts_at_shot"] not in shot_ids:
            problems.append(f"music cue {cue['id']} starts at unknown shot '{cue['starts_at_shot']}'")
    voices = data.get("defaults", {}).get("voices")
    if voices is not None:
        for vid in voices:
            if vid not in char_ids:
                problems.append(f"defaults.voices key '{vid}' is not a character id")
        for s in data["shots"]:
            spk = s["narration"].get("speaker")
            if spk is not None and spk not in voices:
                problems.append(f"shot {s['id']} narration.speaker '{spk}' is not in defaults.voices")
    if problems:
        raise StoryboardError(f"{path} has broken references:\n" + "\n".join(f"  - {p}" for p in problems))
    return data


def select_shots(sb: dict, only: Iterable[str] | None) -> list[dict]:
    if not only:
        return list(sb["shots"])
    wanted = set(only)
    unknown = wanted - {s["id"] for s in sb["shots"]}
    if unknown:
        raise SystemExit(f"Unknown shot ids: {sorted(unknown)}")
    return [s for s in sb["shots"] if s["id"] in wanted]


def characters_for(sb: dict, shot: dict) -> list[dict]:
    by_id = {c["id"]: c for c in sb["characters"]}
    return [by_id[c] for c in shot.get("characters", [])]


# ----------------------------------------------------------------------- caching

def stable_hash(obj: Any, length: int = 12) -> str:
    """Hash of the canonical JSON form of obj. Same inputs -> same key, always."""
    blob = json.dumps(obj, sort_keys=True, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
    return hashlib.sha256(blob).hexdigest()[:length]


def file_hash(path: Path, length: int = 12) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()[:length]


class StageCache:
    """Content-addressed outputs for one stage.

    Each item lives at outputs/<stage>/<item_id>__<key>.<ext>, where key is the
    hash of everything that affects the output (prompt, seed, model, params and
    the hashes of upstream files). A re-run with unchanged inputs finds the file
    and skips the model call; change one prompt and only that item's key moves.
    manifest.json records which key is current for each item.
    """

    def __init__(self, stage: str):
        self.stage = stage
        self.dir = OUTPUTS / stage
        self.dir.mkdir(parents=True, exist_ok=True)
        self.manifest_path = self.dir / "manifest.json"
        self.manifest: dict[str, dict] = (
            json.loads(self.manifest_path.read_text()) if self.manifest_path.exists() else {}
        )

    def path_for(self, item_id: str, key: str, ext: str) -> Path:
        return self.dir / f"{item_id}__{key}.{ext.lstrip('.')}"

    def get(self, item_id: str, key: str, ext: str) -> Path | None:
        p = self.path_for(item_id, key, ext)
        return p if p.exists() and p.stat().st_size > 0 else None

    def record(self, item_id: str, key: str, path: Path, **meta: Any) -> None:
        self.manifest[item_id] = {"key": key, "path": str(path.relative_to(OUTPUTS)), **meta}
        tmp = self.manifest_path.with_suffix(".tmp")
        tmp.write_text(json.dumps(self.manifest, indent=2, ensure_ascii=False))
        tmp.replace(self.manifest_path)

    def current(self, item_id: str) -> Path | None:
        entry = self.manifest.get(item_id)
        if not entry:
            return None
        p = OUTPUTS / entry["path"]
        return p if p.exists() else None


def read_manifest(stage: str) -> dict[str, dict]:
    p = OUTPUTS / stage / "manifest.json"
    return json.loads(p.read_text()) if p.exists() else {}


# ----------------------------------------------------------------------- logging

RUN_ID = os.environ.get("HS_RUN_ID") or datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ") + "-" + uuid.uuid4().hex[:6]


def log_call(
    *,
    stage: str,
    provider: str,
    model: str,
    params: dict,
    latency_s: float,
    status: str,
    item_id: str | None = None,
    seed: int | None = None,
    cache_key: str | None = None,
    output: str | None = None,
    cost_usd: float = 0.0,
    list_price_usd: float | None = None,
    gpu_s: float | None = None,
    attempt: int = 1,
    error: str | None = None,
    extra: dict | None = None,
) -> dict:
    """Append one line to runs.jsonl for every model call (success or failure).
    Secrets never go in here: callers pass params, not headers."""
    rec = {
        "ts": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "run_id": RUN_ID,
        "stage": stage,
        "item": item_id,
        "provider": provider,
        "model": model,
        "seed": seed,
        "params": params,
        "latency_s": round(latency_s, 3),
        "gpu_s": gpu_s,
        "cost_usd": cost_usd,
        "list_price_usd": list_price_usd,
        "status": status,
        "attempt": attempt,
        "cache_key": cache_key,
        "output": output,
        "error": error,
    }
    if extra:
        rec.update(extra)
    RUNS_LOG.parent.mkdir(parents=True, exist_ok=True)
    with open(RUNS_LOG, "a", encoding="utf-8") as f:
        f.write(json.dumps(rec, ensure_ascii=False) + "\n")
    return rec


def say(stage: str, msg: str) -> None:
    print(f"[{stage}] {msg}", flush=True)


# ----------------------------------------------------------------------- retries

class RetryableError(RuntimeError):
    """Raise for failures worth retrying: 429, 5xx, timeouts, flaky GPU queues."""


class FatalError(RuntimeError):
    """Raise for failures a retry cannot fix: bad key, bad request, safety block."""


@dataclass
class CallResult:
    value: Any
    latency_s: float
    attempts: int


def call_with_retries(
    fn: Callable[[], Any],
    *,
    stage: str,
    provider: str,
    model: str,
    params: dict,
    item_id: str | None = None,
    seed: int | None = None,
    cache_key: str | None = None,
    attempts: int = int(os.environ.get("HS_MAX_ATTEMPTS", "3")),
    base_delay: float = float(os.environ.get("HS_RETRY_BASE_DELAY", "4")),
    list_price_usd: float | None = None,
) -> CallResult:
    """Run fn with exponential backoff and jitter on RetryableError. Every attempt,
    including failures, is logged to runs.jsonl. Re-raises after the last attempt."""
    last: Exception | None = None
    for attempt in range(1, attempts + 1):
        t0 = time.monotonic()
        try:
            value = fn()
            dt = time.monotonic() - t0
            log_call(stage=stage, provider=provider, model=model, params=params, latency_s=dt,
                     status="ok", item_id=item_id, seed=seed, cache_key=cache_key,
                     attempt=attempt, list_price_usd=list_price_usd,
                     output=str(value) if isinstance(value, (str, Path)) else None)
            return CallResult(value, dt, attempt)
        except RetryableError as e:
            dt = time.monotonic() - t0
            last = e
            log_call(stage=stage, provider=provider, model=model, params=params, latency_s=dt,
                     status="retryable_error", item_id=item_id, seed=seed, cache_key=cache_key,
                     attempt=attempt, error=str(e)[:500])
            if attempt < attempts:
                delay = base_delay * (2 ** (attempt - 1)) * (0.75 + random.random() / 2)
                say(stage, f"{item_id or ''} attempt {attempt} failed ({e}); retrying in {delay:.0f}s")
                time.sleep(delay)
        except Exception as e:  # FatalError or anything unexpected: log, do not retry
            dt = time.monotonic() - t0
            log_call(stage=stage, provider=provider, model=model, params=params, latency_s=dt,
                     status="fatal_error", item_id=item_id, seed=seed, cache_key=cache_key,
                     attempt=attempt, error=f"{type(e).__name__}: {e}"[:500])
            raise
    assert last is not None
    raise last


def classify_http(resp) -> None:
    """Turn a requests.Response into RetryableError / FatalError when not 2xx."""
    if 200 <= resp.status_code < 300:
        return
    body = resp.text[:400]
    if resp.status_code in (408, 409, 425, 429) or resp.status_code >= 500:
        raise RetryableError(f"HTTP {resp.status_code}: {body}")
    raise FatalError(f"HTTP {resp.status_code}: {body}")


# ------------------------------------------------------------------------ ffmpeg

def need_ffmpeg() -> None:
    for tool in ("ffmpeg", "ffprobe"):
        if not shutil.which(tool):
            raise SystemExit(f"{tool} not found on PATH. Install FFmpeg (https://ffmpeg.org/download.html).")


def ffmpeg(args: list[str], quiet: bool = True) -> None:
    need_ffmpeg()
    cmd = ["ffmpeg", "-nostdin", "-y", "-hide_banner"] + (["-loglevel", "error"] if quiet else []) + args
    proc = subprocess.run(cmd, capture_output=True, text=True, stdin=subprocess.DEVNULL)
    if proc.returncode != 0:
        raise RuntimeError("ffmpeg failed:\n" + " ".join(cmd) + "\n" + proc.stderr[-2000:])


def media_duration(path: Path) -> float:
    need_ffmpeg()
    out = subprocess.run(
        ["ffprobe", "-v", "error", "-show_entries", "format=duration", "-of", "default=nw=1:nk=1", str(path)],
        capture_output=True, text=True, check=True,
    ).stdout.strip()
    return float(out)


def main_guard(fn: Callable[[], int | None]) -> None:
    """Common entry point: load .env, turn StoryboardError into a clean exit."""
    load_env()
    try:
        sys.exit(fn() or 0)
    except StoryboardError as e:
        print(f"Storyboard invalid:\n{e}", file=sys.stderr)
        sys.exit(2)
