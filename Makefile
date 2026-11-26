# One command: `make film`. Every stage is cached by content hash, so re-running
# after a storyboard edit regenerates only what changed.
PY ?= python3
.PHONY: help setup validate smoke script-sheet narration keyframes shots music radio film film-blender \
        kaggle-export kaggle-shots kaggle-import music-export music-import test offline-demo clean-cache costs

help:
	@grep -E '^[a-z-]+:.*##' Makefile | sed 's/:.*##/ -/'

setup: ## install Python deps (needs FFmpeg on PATH)
	$(PY) -m pip install -r requirements.txt
	@test -f .env || (cp .env.example .env && echo "created .env: fill in your keys")

validate: ## check storyboard.json against schema/
	$(PY) -m pipeline.validate

smoke: ## call each provider once, log latency
	$(PY) -m pipeline.smoke_test

script-sheet: ## write RECORDING_SCRIPT.md for the narrator
	$(PY) -m pipeline.script_sheet

narration: validate ## recordings/ -> cleaned WAVs (TTS for missing lines)
	$(PY) -m pipeline.narration

keyframes: validate ## reference sheet + one still per shot (Cloudflare FLUX)
	$(PY) -m pipeline.keyframes

shots: validate ## Wan clip if imported, else pan-and-zoom from the keyframe
	$(PY) -m pipeline.shots build

music: validate ## chosen ACE-Step candidate, else placeholder score
	$(PY) -m pipeline.music build

radio: ## Phase 1 checkpoint: narration + placeholder score on black
	$(PY) -m pipeline.run --radio

film-blender: ## whole pipeline with Blender motion (a Cloudflare model writes camera moves) -> outputs_blender/final/
	$(PY) -m pipeline.run --motion blender

film: ## whole pipeline, both aspect ratios -> outputs/final/
	$(PY) -m pipeline.run

kaggle-export: ## bundle shots needing a GPU run -> outputs/kaggle_job/
	$(PY) -m pipeline.shots export

kaggle-shots: ## export + push dataset and notebook with the kaggle CLI
	$(PY) -m pipeline.shots kaggle

kaggle-import: ## make kaggle-import DIR=outputs/kaggle_out
	$(PY) -m pipeline.shots import $(DIR)

music-export: ## write ACE-Step candidate jobs
	$(PY) -m pipeline.music export

music-import: ## make music-import DIR=path/to/music
	$(PY) -m pipeline.music import $(DIR)

test: ## unit tests (offline, no keys)
	$(PY) -m pytest -q

offline-demo: ## whole pipeline with placeholder generators, no keys needed
	HS_OFFLINE=1 HS_OUTPUTS=outputs_offline HS_RUNS_LOG=outputs_offline/runs.jsonl $(PY) -m pipeline.run

clean-cache: ## remove assembly intermediates only (generated assets are kept)
	rm -rf outputs/assembly

costs: ## per-stage latency / GPU minutes table from runs.jsonl
	$(PY) -m pipeline.costs
