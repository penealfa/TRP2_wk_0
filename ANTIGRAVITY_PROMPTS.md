# Antigravity implementation prompts

Paste these into Antigravity one at a time, in order. Each one says what to build, what
was verified and what wasn't, how it can fail, and what "done" means. Don't start the next
prompt until the acceptance checks for the current one pass. Paste Antigravity's summary
and test output back to Claude for review.

Labels used below:
- **[verified]**: checked against current official docs on 26 Nov 2026
- **[unverified]**: an assumption Antigravity must confirm by testing
- **[decision]**: an architectural choice, with the reason given

---

## P0: Onboarding (read-only, about 10 min)

```
You are working in an existing, tested Python repo: a reproducible AI film pipeline.
Do NOT rewrite working modules. Extend them.

1. Read README.md, ARCHITECTURE.md, storyboard.json, schema/storyboard.schema.json and
   pipeline/common.py. Summarise back to me, in under 15 lines: the stage list, how the
   cache key works, and how a model call gets logged.
2. Run: python3 -m pip install -r requirements.txt && make test && make offline-demo
   Report the pass/fail counts and the final "Model calls this run" line. FFmpeg must be
   on PATH (on Fedora: sudo dnf install ffmpeg-free, or ffmpeg from RPM Fusion).

House rules for every later task:
- Every provider call goes through pipeline.common.call_with_retries, which retries and
  logs to runs.jsonl. Raise RetryableError for 408/429/5xx and timeouts, FatalError for the rest.
- Every generated file is content-addressed: <item>__<stable_hash(inputs)>.<ext> via StageCache.
  Anything that changes an output must be part of its key.
- Model ids and prompts come from storyboard.json or env vars, never hardcoded in logic.
- Never commit .env, keys or kaggle.json. Never log headers.
- Add or extend pytest tests for every behaviour you add. Mock HTTP in tests; no network
  in `make test`. Keep `make test` and `make offline-demo` green.
- Don't claim something works until you have run it. Paste the command and the output.
```

---

## P1: Gemini 3.8 Flash TTS and per-speaker voices

**Why:** Peneal records his own lines. The wolf speaks through Gemini TTS in Amharic.
`storyboard.json` already declares `defaults.voices` and `narration.speaker`. The
narration stage doesn't read them yet, and its TTS client assumes an older API shape.

```
Task: make the narration stage route each line by speaker, and update the Gemini TTS client.

Facts [verified, ai.google.dev/gemini-api/docs/models/gemini-3.8-flash-tts]:
- Model id: gemini-3.8-flash-tts. Supports Amharic (auto-detects language). 8,192 input tokens.
- Unary responses are WAV WITH a RIFF header by default (audio/wav). Headerless output is
  available as audio/l16. The current code in pipeline/providers.py:gemini_tts assumes raw
  PCM and wraps it in a WAV, which would double the header. Fix that.
- Delivery style goes in a `speech_metadata` object on each part: {"speaker": ..., "style": ...}.
  Inline tags like <sigh> or <short pause> are allowed. Voices are listed by GET /v1beta/voices.
[unverified]: where the voice name goes in the generateContent request for this model, and
  whether the older generationConfig.speechConfig.voiceConfig.prebuiltVoiceConfig shape is
  still accepted. Test both and keep the one that works. Log which shape was used in params.
[unverified]: free-tier quota for this model. Read it in AI Studio and write it in COSTS.md.

Changes:
1. providers.gemini_tts(model, text, voice, style) -> bytes of a complete WAV file.
   If the response bytes start with b"RIFF", return them as-is. If they're raw PCM, wrap
   as 24 kHz, 16-bit mono. Raise RetryableError when there's no audio part.
2. narration.run: for each shot, speaker = narration.speaker; cfg = defaults.voices[speaker]
   (if voices is absent, keep today's behaviour). source "recorded" -> recordings/<shot>.<ext>,
   with TTS fallback (as today). source "tts" -> always TTS with cfg.voice and cfg.style
   (shot-level narration.style overrides). The TTS cache key must include speaker, voice and style.
3. common.load_storyboard: if defaults.voices exists, every narration.speaker must be a key
   of it and every key must be a character id. Raise StoryboardError listing each problem.
4. smoke_test: print the available voices from GET /v1beta/voices (names only).

Failure cases to handle: 429 quota (retry, then a clear message telling the user to wait
or switch HS key), a safety block (FatalError with the reason), bad Amharic pronunciation
(not a code error: the user records it in FAILURES.md).

Acceptance:
- pytest (mocked requests): RIFF bytes pass through unchanged; raw PCM gets wrapped; wolf
  lines go to TTS while peneal lines use recordings; changing voices.wolf.style regenerates
  only wolf lines (assert on runs.jsonl items); an unknown speaker fails validation.
- Live: `make smoke` shows TTS OK; `python -m pipeline.narration --only s02` writes a WAV
  that plays; ffprobe shows a sensible duration. Peneal listens and judges the Amharic.
```

---

## P2: Reference sheets and keyframes with FLUX.2 klein

**Why:** keeping Peneal and the wolf consistent across 11 shots is the hardest visual
problem. FLUX.2 klein accepts reference images, which works much better than repeating
a text description.

```
Task: per-character reference sheets, then keyframes conditioned on the sheets of the
characters in each shot.

Facts [verified, developers.cloudflare.com/workers-ai/models/flux-2-klein-4b]:
- Model id @cf/black-forest-labs/flux-2-klein-4b (a 9b variant also exists). Input is
  multipart form data. Output JSON has `image` as base64. Listed as zero data retention.
[unverified]: the exact multipart field names (current code guesses prompt, seed, width,
  height, steps, input_image_0..3) and the maximum number of references. Fetch the model's
  input JSON schema linked from that page, correct providers.cloudflare_image, and cite
  the schema in a code comment. Also find the per-image neuron cost and the daily free
  allowance on the Workers AI pricing page, and put both in COSTS.md.

Changes:
1. keyframes: generate one sheet per character: item "ref_<char_id>". The prompt is
   style.reference_sheet_prompt adapted to that character, plus the character description
   and the style block. If assets/refs/<char_id>.(jpg|png) exists (Peneal may add a photo
   of himself), pass it as a reference image for that sheet. Its file hash goes in the key.
2. For each shot, pass the sheets of shot.characters (up to the model limit) as reference
   images. The key includes those sheet hashes, so changing the wolf's description
   regenerates the wolf sheet and only the shots that contain the wolf.
3. Write outputs/keyframes/contact_sheet.png: a grid of every current keyframe with its
   shot id drawn underneath (Pillow), for fast review.
4. Keep the --candidates N option (extra seeds). Write candidates into the contact sheet too.
5. If the model returns an error mentioning the image fields, fall back to text-only and
   log status "degraded".

Acceptance:
- pytest (mocked): a shot with ["peneal","wolf"] sends 2 reference images; a shot with
  none sends 0; editing the wolf description regenerates ref_wolf plus s02, s04, s06, s08
  and s11 only.
- Live: `make keyframes` completes; the contact sheet exists; record neurons and latency.
  The human check (faces, hands, the wolf looks like a wolf) happens before setting "approved": true in storyboard.json.
```

---

## P3: Text overlays, Ethiopic fonts, assembly polish

**Why:** text inside generated images comes out garbled, so labels like
"PUNCH CARDS · 1950s" are drawn in code. **Known bug:** the end card uses DejaVu Sans,
which has no Ethiopic glyphs, so the Amharic title will render as boxes.

```
Task:
1. Add assets/fonts/NotoSansEthiopic-*.ttf (SIL Open Font License; add the licence file and
   a line in ETHICS.md). Use it for any text containing Ethiopic characters (U+1200-U+137F),
   on the end card and in overlays. Keep DejaVu Sans for Latin.
2. Render shots[].overlays in assemble.segment: draw each overlay as a transparent PNG at
   the target aspect ratio (safe area: 8% margins; in 9:16, keep clear of the bottom 20%,
   where subtitles go). Composite with FFmpeg overlay, using enable='between(t,start,end)'
   and a 0.3 s alpha fade in and out. The overlay text and timing must be part of the
   segment cache key.
3. End card: render the title line in the Ethiopic font, gold (#D4AF37) on near-black, with a
   thin ornamental border in the style of a manuscript frame (drawn with Pillow, no images).

Acceptance:
- pytest: rendering "የኮድ ቅርስ" with the chosen font gives a non-empty glyph bbox for every
  character (catches tofu boxes); an overlay PNG exists for s04; changing an overlay's text
  changes only that segment's cache key.
- `make offline-demo` passes. A frame grabbed at s04 + 2 s shows the label. Paste the frame.
```

---

## P4: ACE-Step 1.5 score notebook

```
Task: rewrite notebooks/kaggle_music/acestep_score.ipynb against the current ACE-Step 1.5 API.

Facts [verified, github.com/ace-step/ACE-Step-1.5 docs/en/INFERENCE.md]:
  from acestep.handler import AceStepHandler
  from acestep.llm_inference import LLMHandler
  from acestep.inference import GenerationParams, GenerationConfig, generate_music
  dit = AceStepHandler(); dit.initialize_service(project_root=..., config_path="acestep-v15-turbo", device="cuda")
  llm = LLMHandler(); llm.initialize(checkpoint_dir=..., lm_model_path="acestep-5Hz-lm-0.6B", backend="vllm", device="cuda")
  params = GenerationParams(caption=..., lyrics="[Instrumental]", instrumental=True, bpm=66,
                            keyscale="D minor", duration=90, seed=..., shift=3.0)
  config = GenerationConfig(batch_size=1, use_random_seed=False, audio_format="flac")
  result = generate_music(dit, llm, params, config, save_dir=...)  # result.audios[i]["path"]
  - Caption max 512 chars. For GPUs under 20 GB, the docs recommend offload_to_cpu=True and INT8.
[unverified]: the install command; whether vllm installs on Kaggle (if not, find the
  non-vllm backend in the docs, or skip the LM since bpm and key are set explicitly); the
  licence of 1.5 (check the repo LICENSE file and put it in ETHICS.md).

Changes: read jobs.json from `python -m pipeline.music export`. For each job, generate one
FLAC and rename it to the job's "output" name with a .flac extension. Append a runs_kaggle.jsonl line
(stage, item, model "ACE-Step-1.5-turbo", seed, params, latency_s, gpu_s, gpu name,
status). Update pipeline/music.py export so "output" ends in .flac.

Acceptance: on Kaggle (GPU T4 or P100), 3 candidates finish. `make music-import DIR=...`
then `make music` picks c1, or the candidate set in storyboard.json music.chosen.
Paste the notebook log.
```

---

## P5: Wan 2.2 image-to-video on Kaggle: prove one shot first

```
Task: make notebooks/kaggle_wan/wan22_i2v.ipynb actually run, starting with ONE job.

Facts [verified, huggingface.co/Wan-AI/Wan2.2-TI2V-5B-Diffusers]: Apache 2.0. The diffusers
  integration needs diffusers installed from the main branch. The official CLI says it
  needs at least 24 GB VRAM (with offload). A Kaggle T4 has 16 GB.
[unverified]: whether diffusers' WanImageToVideoPipeline loads TI2V-5B (the model card
  only shows WanPipeline for text-to-video). Check the diffusers docs. If i2v needs a
  different call, use it. Otherwise fall back to the repo's generate.py --task ti2v-5B
  --image ... --offload_model True --convert_model_dtype --t5_cpu.

Plan:
1. pip install git+https://github.com/huggingface/diffusers. Run a single job (s02) at a
   square size divisible by 32 (640x640), 49 frames first, 30 steps. Use bf16, cpu offload
   and vae tiling.
2. Log gpu_s, peak VRAM and status. If it runs out of memory: drop to 512x512, then 33 frames.
   If a 49-frame clip takes more than 20 GPU minutes, stop. The other shots use the
   pan-and-zoom fallback, and the reason goes in FAILURES.md. That's a valid,
   documented outcome.
3. Only then queue the remaining approved hero shots (s02, s06, s09, s11 recommended:
   slow, simple motion), both GPUs in parallel.

Acceptance: at least one Wan clip imported with `make kaggle-import`, its runs.jsonl
line showing gpu_s and peak VRAM, and `make film` showing shots {'wan': n, 'kenburns': m}.
```

---

## Not today: Blender and Blender MCP

**[decision]** Leave Blender out of the critical path for this deadline. The church-painting
style is flat by design, so 3D adds little that a pan-and-zoom can't. The scripted Blender
setup and an MCP bridge would cost hours we don't have. If there's time after submission
prep, the one good use is a parallax camera move through the s07 procession: split the
keyframe into 3 depth layers, place them as planes in Blender via a Python script
(`blender -b -P pipeline/blender_parallax.py`), and render 10 s. Log it as an experiment.
