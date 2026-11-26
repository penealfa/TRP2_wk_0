# Architecture

## One contract

`storyboard.json` is the only input every stage shares. It holds the story, the
audience, the style block, the characters, and per shot: duration, shot size, the
reason the shot exists, the keyframe prompt and seed, the motion prompt and fallback,
the narration (film language + English subtitle) and the music cue. Prompts and seeds
live here, not in code, so a run is reproducible and explainable.

`schema/storyboard.schema.json` (JSON Schema 2020-12) is checked before every stage,
together with rules a schema can't express (`pipeline/common.py:load_storyboard`):

- shot ids are unique;
- every character and music cue a shot names exists;
- a character with `real_person: true` must carry a `consent_ref` into ETHICS.md;
- the end card must contain the word "AI";
- 8–12 shots, each with a stated `reason`.

A broken storyboard stops the run with a list of every problem, before any quota is spent.

## Stages

```
storyboard.json ─┬─> narration ──> outputs/narration/<shot>__<key>.wav ──────────────┐
                 ├─> keyframes ──> outputs/keyframes/<shot>__<key>.png ─┐             │
                 │                                                     ├─> shots ──> outputs/shots/<shot>__<key>.mp4
                 │                     Kaggle: Wan 2.2 ── import ──────┘             │
                 ├─> music ───────> outputs/music/<cand>__<key>.wav ─────────────────┤
                 │                     Kaggle: ACE-Step ─ import                     │
                 └──────────────────────────────────────────────────────> assemble ──> outputs/final/*.mp4 + .srt
```

Each stage is a module in `pipeline/` with a `run`/`build` function and a CLI.
`pipeline/run.py` chains them; `make film` calls it.

## Caching

Every generated file is named `<item>__<key>.<ext>`, where `key` is the first 12 hex
digits of a SHA-256 of the canonical JSON of **everything that affects that output**:

| Stage | Key inputs |
|---|---|
| narration (recorded) | hash of the recording file + cleaning-chain version |
| narration (TTS) | model, voice, delivery instruction + text, language |
| keyframes | model, full prompt (shot prompt + character descriptions + style block), seed, size, hash of the reference sheet (for models that take references) |
| shots (Wan) | keyframe file hash, motion prompt, seed, model, size, frames, fps, steps |
| shots (fallback) | keyframe file hash, fallback move, length, fps |
| music | caption, tags, lyrics, bpm, key, duration, seed |
| assembly segments | clip file hash, length, aspect, fps |

Upstream file hashes are part of downstream keys, so a new keyframe automatically
invalidates its Wan clip. Nothing is ever overwritten. An old version stays on disk
under its old key, which makes A/B comparisons free. `outputs/<stage>/manifest.json`
records the current key per item. `tests/test_caching.py` proves the property:
an unchanged storyboard makes zero calls, and one edited prompt plus one edited line
make exactly two.

## Logging

`runs.jsonl` gets one JSON line per model call, including failed attempts. Each line
records `run_id, stage, item, provider, model, seed, params, latency_s, gpu_s, cost_usd,
status, attempt, cache_key, output, error`. Kaggle notebooks write the same shape
(plus GPU name, dtype and peak VRAM) to `runs_kaggle.jsonl`, which `import` appends.
Keys never enter the log.

## Failure handling

- **Retries.** `call_with_retries` retries `RetryableError` (HTTP 408/429/5xx,
  timeouts, empty responses) with exponential backoff and jitter, three attempts by
  default (`HS_MAX_ATTEMPTS`). `FatalError` (bad key, 4xx, safety block) fails at once.
  Every attempt is logged.
- **Fallbacks keep the film assembling.**
  - Narration: a missing recording falls back to TTS.
  - Shots: a missing or failed Wan clip falls back to a pan-and-zoom of the keyframe, in
    the direction the storyboard names.
  - Music: no ACE-Step candidates falls back to a placeholder drone.
  - Assembly reports how many shots used each source.
- **GPU stages are batch and offline.** Kaggle sessions die and queues stall, so the GPU
  work is a job file plus an import step, not a live API. A half-finished notebook still
  returns the clips it made, and the next export only lists what's missing.
- **Approval gate.** Only keyframes marked `approved: true` are sent to the GPU, so quota
  isn't spent animating a face with six fingers.

## Assembly decisions

- **Cut to the voice.** Shot length is the larger of the storyboard duration and the
  offset plus narration length plus 0.6 s.
- **One clip, two aspect ratios.** Keyframes and Wan clips are square, with the subject
  centred. Each export centre-crops to 9:16 or 16:9, so one GPU render serves both. The
  trade-off is lower resolution per aspect, recorded in EXPERIMENTS.md.
- **Mix.** Narration placed at shot start plus offset. The score is looped or trimmed to
  length, dropped 6 dB, and ducked with `sidechaincompress` (threshold 0.02, ratio 6,
  attack 300 ms, release 1500 ms; tunable in `music.duck`), then faded over the end card.
  The mix is normalised in two loudnorm passes to -14 LUFS and -1 dBTP.
- **Subtitles.** English SRT generated from `text_en`, at most two lines per cue,
  wrapped at 42 characters (16:9) or 28 (9:16), then burned in.
