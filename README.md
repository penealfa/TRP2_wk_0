# Heritage Short

A 60–90 second family film in Amharic with English subtitles, voiced with Gemini TTS, made by a small studio in
code. One file, [`storyboard.json`](storyboard.json), is the contract between
every stage; each stage reads it, writes its outputs, and caches them by a hash
of its inputs, so changing one shot regenerates only that shot.

> **Film:** _link to 9:16 MP4_ · **16:9:** _link_ · **Walkthrough:** _link_ · **Post:** _link_
> _(fill in before submitting)_

## Run it

```bash
git clone <this repo> && cd heritage-short
make setup          # pip install -r requirements.txt, creates .env from .env.example
# fill in .env (Gemini key, Cloudflare account id + token), install FFmpeg
make film           # validate -> narration -> keyframes -> shots -> music -> both films
```

The finished films land in `outputs/final/heritage_9x16.mp4` and
`heritage_16x9.mp4`, with `.en.srt` subtitles beside them. Every model call is a
line in [`runs.jsonl`](runs.jsonl). At the end, `make film` prints how many model
calls this run made, and which ones. After a one-line edit, that should be one call.

There's no key yet? `make offline-demo` runs the whole pipeline with local placeholder
generators into `outputs_offline/`. `make test` checks the schema rules and the
"one edit, one call" caching property, also offline.

## The pipeline

| Stage | Command | Model / tool | Runs on |
|---|---|---|---|
| script | `python -m pipeline.script story.md` | Gemini Flash (draft + self-repair against the schema) | Gemini API |
| validate | `make validate` | JSON Schema + cross-reference checks | local |
| narration | `make narration` | **my own recorded voice** (`recordings/<shot>.wav`), Gemini TTS fallback | local / Gemini API |
| keyframes | `make keyframes` | FLUX on Cloudflare Workers AI, reference sheet first | Cloudflare |
| shots | `make kaggle-export` → notebook → `make kaggle-import` → `make shots` | Wan 2.2 TI2V-5B image-to-video; pan-and-zoom fallback | Kaggle 2× T4 |
| music | `make music-export` → notebook → `make music-import` → `make music` | ACE-Step 1.5, 3 candidates | Kaggle / Colab |
| assemble | `make radio` / `make film` | FFmpeg: cut to voice, duck, subtitles, loudnorm, end card, 9:16 + 16:9 | local |

See [ARCHITECTURE.md](ARCHITECTURE.md) for the contracts, the caching scheme and the failure handling.

### Regenerate one shot (the walkthrough demo)

```bash
# edit shots[3].keyframe.prompt (or re-record recordings/s04.wav) in storyboard.json
make film
# -> "[keyframes] s04: generated", every other shot "cached", and
#    "Model calls this run: 1 -> keyframes:s04"
tail -n 1 runs.jsonl
```

### Recording the narration

`make script-sheet` writes `RECORDING_SCRIPT.md`, which lists every line, the file name to
save it under, and how long it should run. Record each line as its own file in `recordings/`
(`.wav`, `.m4a` or `.mp3`). The narration stage trims silence, applies a high-pass filter and
normalises loudness. Any line not yet recorded uses Gemini TTS, so the film always
assembles.

### GPU stages on Kaggle

1. `make kaggle-export` writes `outputs/kaggle_job/` (`jobs.json` and the keyframes) for
   the shots whose clip is missing or out of date. Only keyframes marked `"approved": true`
   in the storyboard are included.
2. Upload that folder as a Kaggle dataset, then run
   [`notebooks/kaggle_wan/wan22_i2v.ipynb`](notebooks/kaggle_wan/wan22_i2v.ipynb) with
   GPU T4 x2 and Internet on. `make kaggle-shots` does both steps with the kaggle CLI.
3. Download the notebook output and run `make kaggle-import DIR=<folder>`. The clips and the
   notebook's `runs_kaggle.jsonl` lines go back into the repo.

Music works the same way with `make music-export` and
[`notebooks/kaggle_music/acestep_score.ipynb`](notebooks/kaggle_music/acestep_score.ipynb).

## What it costs

$0. Measured latencies and GPU minutes per stage, with list-price equivalents, are in [COSTS.md](COSTS.md).

## Documents

[REPORT.md](REPORT.md) · [DESIGN.md](DESIGN.md) (audience, intent, user tests) · [ARCHITECTURE.md](ARCHITECTURE.md) ·
[EXPERIMENTS.md](EXPERIMENTS.md) · [FAILURES.md](FAILURES.md) · [COSTS.md](COSTS.md) ·
[ETHICS.md](ETHICS.md) (consent, licences, labelling)

## Repository layout

```
storyboard.json   the contract          schema/      JSON Schema for it
pipeline/         one module per stage  notebooks/   Kaggle GPU jobs
recordings/       my narration takes    outputs/     every generated asset + manifests
runs.jsonl        one line per call     tests/       offline tests
```

Secrets live only in `.env`, which is git-ignored. `runs.jsonl` records parameters, never headers or keys.
