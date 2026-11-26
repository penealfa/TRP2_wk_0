# Costs

Measured from `runs.jsonl`: `make costs` prints the per-stage table below from the log
(add the list-price column by hand). Free-tier limits are as I found them on the date shown.

| Stage | Provider / model | Calls (ok / failed) | Median latency | GPU minutes | Cost paid | List-price equivalent |
|---|---|---|---|---|---|---|
| script | none (script written by hand) | – | – | – | $0 | – |
| narration | Gemini TTS `gemini-3.8-flash-tts` | 11 / 6 (all 6 were 429 quota) | 4.6 s | – | $0 | not measured (free tier) |
| keyframes | Cloudflare FLUX.2 klein 4B | 42 / 3 | 8.4 s | – | $0 | not measured (free neurons) |
| shots | Wan 2.2 TI2V-5B, Kaggle T4 | 2 / 1 (the 1 failure was the OOM that led to the fix, F6) | 2,499 s and 2,258 s per clip | 79.2 for 2 clips (+3.8 model load each) | $0 | about $0.46 at an assumed $0.35/GPU-hour for a T4 |
| motion (optional Blender layer) | Cloudflare `qwen2.5-coder-32b-instruct` writes scripts; Blender renders locally | 23+ model calls, 11 renders | about 1 s per script; 70-210 s per render | – (local GPU) | $0 | not measured (free neurons) |
| music | ACE-Step 1.5 turbo, Kaggle T4 | 3 / 6 (6 failed candidates across two earlier runs, F7) | 22.1 s per candidate | 1.1 | $0 | about $0.01 at the same assumed rate |
| assemble | FFmpeg, local | 2 films | about 45-106 s for both | – | $0 | – |
| **Total** | | | | **about 81** | **$0** | **about $0.50** (the T4 rate is my assumption, not a quoted price) |

Failed calls are in `runs.jsonl` for the API stages; the failed Kaggle attempts are in the Kaggle kernel logs (not imported), and described in [FAILURES.md](FAILURES.md).

## Free-tier limits as found

| Service | Limit found | Date checked |
|---|---|---|
| Gemini API (text, TTS) | Free tier available for `gemini-3.8-flash-tts` (8,192 input tokens); check exact RPM/RPD in AI Studio (`aistudio.google.com`) under your project quota | 2026-11-26 |
| Cloudflare Workers AI | 10,000 free neurons/day (`$0.011 / 1,000 neurons` paid). `@cf/black-forest-labs/flux-2-klein-4b`: ~5.37 neurons per 512x512 input tile (`$0.000059`), ~26.05 neurons per 512x512 output tile (`$0.000287`) | 2026-11-26 |
| Kaggle GPU | 30 GPU hours/week (2x T4 16 GB or P100 16 GB) | 2026-11-26 |

## Frontier comparison

_If an org key is shared: same shot on an open vs frontier model, with cost and latency._
