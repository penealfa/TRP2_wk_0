# Experiments

Each entry: **hypothesis → change → result**, with evidence (`runs.jsonl` lines, file
names, test notes). About ten is the target. A fixed seed is used when two prompts are compared.

| # | Hypothesis | Change | Result (evidence) | Decision |
|---|---|---|---|---|
| E1 | My own recorded voice will land better with the audience than Gemini TTS | Same 3 lines, recorded vs TTS (`narration --source tts`), blind to 3 listeners | _…_ | _…_ |
| E2 | A reference sheet passed as an input image keeps the character more consistent than text description alone | FLUX.2 with ref sheet vs FLUX.1 schnell text-only, same seeds, shots s02/s05/s07 | _…_ | _…_ |
| E3 | Square 640px Wan clips cropped to both aspects look acceptable versus native 16:9 | One shot rendered both ways; compare crop sharpness and GPU minutes | _…_ | _…_ |
| E4 | 480p drafts cost less GPU time without losing the motion I care about | Same job at 480p vs 720p: `gpu_s` from runs.jsonl | _…_ | _…_ |
| E5 | bf16 on T4 avoids the black or NaN frames fp16 can give | `WAN_FP16=1` vs default on one job | _…_ | _…_ |
| E6 | Shorter motion prompts (motion + camera only) give fewer artifacts than full scene descriptions | 2 prompts × 1 keyframe, same seed | _…_ | _…_ |
| E7 | An ACE-Step caption without a strong lead melody sits better under narration | 3 candidates, listened under the radio cut at low volume | _…_ | _…_ |
| E8 | The brief's ducking settings (0.02 / 6 / 300 / 1500) are a good start for my voice | Tried a lower threshold and a faster release; recorded what I heard | _…_ | _…_ |
| E9 | | | | |
| E10 | | | | |
