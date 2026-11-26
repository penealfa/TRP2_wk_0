# Costs

Measured from `runs.jsonl`: `make costs` prints the per-stage table below from the log
(add the list-price column by hand). Free-tier limits are as I found them on the date shown.

| Stage | Provider / model | Calls | Median latency | GPU minutes | Cost paid | List-price equivalent |
|---|---|---|---|---|---|---|
| script | Gemini Flash | | | – | $0 | |
| narration | my voice + Gemini TTS fallback | | | – | $0 | |
| keyframes | Cloudflare FLUX | | | – | $0 | |
| shots | Wan 2.2 TI2V-5B, Kaggle 2× T4 | | | | $0 | _e.g. T4 cloud rate × GPU hours_ |
| music | ACE-Step 1.5, Kaggle | | | | $0 | |
| assemble | FFmpeg, local | | | – | $0 | – |
| **Total** | | | | | **$0** | |

## Free-tier limits as found

| Service | Limit found | Date checked |
|---|---|---|
| Gemini API (text, TTS) | Free tier available for `gemini-3.8-flash-tts` (8,192 input tokens); check exact RPM/RPD in AI Studio (`aistudio.google.com`) under your project quota | 2026-11-26 |
| Cloudflare Workers AI | 10,000 free neurons/day (`$0.011 / 1,000 neurons` paid). `@cf/black-forest-labs/flux-2-klein-4b`: ~5.37 neurons per 512x512 input tile (`$0.000059`), ~26.05 neurons per 512x512 output tile (`$0.000287`) | 2026-11-26 |
| Kaggle GPU | 30 GPU hours/week (2x T4 16 GB or P100 16 GB) | 2026-11-26 |

## Frontier comparison

_If an org key is shared: same shot on an open vs frontier model, with cost and latency._
