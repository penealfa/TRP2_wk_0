# The Heritage of Code: report

Peneal Feleke · 26 November 2026 · Code, logs and every figure below: <https://github.com/penealfa/TRP2_wk_0>

## 1. What I set out to make, and for whom

I made an 89-second film in Amharic, with English subtitles, called *የኮድ ቅርስ — The Heritage of Code*. Late at night in Addis Ababa, a young backend engineer (me, painted) is afraid that AI is making his craft worthless. An Ethiopian wolf, a voice from his own subconscious, shows him the long line of Ethiopian makers he belongs to: scribes, weavers, painters, telegraph operators. The whole film is painted in the style of Ethiopian style painting.

It is for young Ethiopian developers and CS students (20–30) who use AI tools every day and quietly worry what that means for their skills, and second for my family, who see my work as "computer stuff". They will watch it on a phone, vertical, on Instagram and in developer Telegram groups. I wanted them to end on relief and pride: our craft is not ending, it is one more link in a very old line, and the joy that made us start is still available. The audience, feeling and story beats are in `DESIGN.md`.

It is built as a pipeline, not a project of hand edits. One file, `storyboard.json`, is the contract between every stage (script → narration → keyframes → shots → music → assembly). Every stage caches its output by a hash of its inputs and logs every model call to `runs.jsonl` (83 lines in the final log). `make film` rebuilds everything, and a one-line edit regenerates only what depends on it. It cost $0: about 43 GPU minutes on Kaggle's free tier (`COSTS.md`).

## 2. What my audience said, and what changed

Two testers have reviewed it so far, one per test (the brief asks for three per test, so this is incomplete; the rest of this section is only what those two said).

| Test | Cut | Tester | What they said | What changed in the storyboard |
|---|---|---|---|---|
| 1 | Radio cut (narration + placeholder music on black) | Sebrina A. | At the end the Amharic says "ሰላም" (selam) for "hello", but it should be exactly "Hello, World", untranslated. | Shot s09's narration became `ስክሪኑ መጀመሪያ "Hello, World" ብሎ ሲመልስልህ ታስታውሳለህ?` and its subtitle `"Hello, World"`. |
| 2 | Near-final cut | Tahir A. | The on-screen text beyond the subtitles (PUNCH CARDS · 1950s, SCRIBE, WEAVER…) is clutter; remove it. | I removed every `overlays` entry from `storyboard.json` (shots s04 and s07). |

The radio cut has no picture, so only a spoken-word comment could come from test 1; the comment about on-screen text could only come from a cut with images, which is test 2. Both changes are visible in the git history (`DESIGN.md`, `storyboard.json`). The first comment was a good catch: "Hello, World" is the programmer's first program, so translating it removed the exact joke the shot depends on. Rerunning the pipeline after that edit made **one** TTS call (shot s09) and left everything else cached, which is also the evidence for section 4.

## 3. The hardest problem and how I solved it

The hardest problem was running the open video and music models on a free Kaggle T4 (15 GB, no bfloat16). Both failed, in different ways, and both are logged in `FAILURES.md` (F6, F7):

- **Wan 2.2 (image-to-video).** The first run ran out of memory at denoising step 0 (peak 15.45 GB). Worse, my retry logic then tried smaller sizes, but the offloaded 10 GB transformer was still resident on the GPU, so those retries started already full and failed too. I switched to sequential CPU offload and freed the model hooks between attempts. After that the clip worked (640×640, 49 frames, peak 6.93 GB) but took 2,499 s, 41.6 GPU minutes. My budget was 20. I decided on one Wan clip (shot s02, the wolf's arrival) and kept pan-and-zoom for the other ten shots. Later I regenerated every keyframe after changing the style wording, which made that clip stale (its cache key includes the keyframe it was made from), and I chose not to spend another 42 GPU minutes on it. The final films are therefore all pan-and-zoom; the Wan clip, its log line and its cost stay in the repo as evidence. Eleven clips would have used 7.6 of the 30 weekly GPU hours with no room for retries.
- **ACE-Step 1.5 (music).** It failed three times for three different reasons: Kaggle's Python 3.13 is outside ACE-Step's supported range (fixed with a Python 3.12 environment built with `uv`), a matplotlib backend variable leaked into the subprocess, and then every candidate failed with NaN latents because a pre-Ampere GPU runs it in float16. The library's own error message told me to set `ACESTEP_DTYPE=float32`; I read the source and found that variable is only mentioned in the message, not implemented. Setting it changed nothing (the log still said float16). Patching the one `self.dtype = torch.float16` line to float32 worked: three candidates in 22 seconds each.

The lesson was the same both times: read the log, read the source, and don't trust an error message's suggested fix until a run proves it.

## 4. One suggested practice that beat my instinct, and one that didn't

**A JSON contract plus caching beat my instinct.** The easy instinct is to edit things and rerun the whole film. With the contract and hash-keyed caching, the logs show the property holding in practice: the "Hello, World" edit regenerated one narration line (`narration` s09, one call in `runs.jsonl`), and correcting my own description in the storyboard (I had written "clean-shaven, no glasses"; the photo shows glasses and a beard) regenerated only my reference sheet and the six shots that contain me, while the wolf-only shots stayed cached. Without this, correcting a character description would have meant regenerating all eleven keyframes and redoing the review.

**A reference sheet before keyframes did not work as suggested.** The advice is to generate a character sheet first and condition every shot on it. In this model (FLUX.2 klein) it conditioned the shots on the sheet's layout as well as the character. My first wolf sheet had three panels, so several shots (s02, s06) came back as three-panel images. A full-body figure on a blank ochre background turned my close-ups (s03, s10) into full-body figures on blank ochre. It took three runs to fix. The keyframe stage made 42 successful image calls for 13 final images (11 shots + 2 sheets), so about 3 generations per final image, almost all of that from this problem. The fix was a new `ref_framing` field per character (a head-and-shoulders portrait for me, a full-body figure against a night background for the wolf), so each reference matches the scenes it appears in (`FAILURES.md` F5). The practice is still worth doing, but a sheet has to be designed like a shot, not like a model sheet.

## 5. Ceiling work and what it taught me

I planned to compare my own voice with Gemini TTS using blind listeners. I changed that: I decided early to voice both characters with Gemini TTS (Charon for me, Kore for the wolf), so that comparison was not run, and I am not claiming a result for it.

The ceiling work I did instead was making Gemini TTS carry a whole film in Amharic: eleven lines, each its own file, with a style instruction per speaker. Its quota (429 after seven lines on the free tier) blocked a batch run, so I waited, re-ran, and the cache skipped the nine finished lines. I read and approved all eleven lines myself as a native Amharic speaker. The one real error came from a tester, not from me or the model: translating "Hello, World". What I learned is that TTS can sound right and still be wrong in the way that matters to a particular audience, and that the person who needs to catch it is a member of that audience, not the person who wrote the pipeline.

The loudness and mix are done in code (FFmpeg): narration is placed at each shot's start, the score is ducked under it, the mix is normalised to −14.0 LUFS (measured on both outputs), subtitles are burned in, and both 9:16 and 16:9 films have an end card saying they were made with AI.

## 6. What I would build next

1. Run the remaining shots through Wan 2.2 on a faster GPU, or only the shots where motion matters, now that the memory problem is solved and the cost per clip is known.
2. Do the planned blind listening test: my own voice against Gemini TTS, with the wolf's pronunciation checked by a second native speaker.
3. Finish the user tests: three people per test, and make storyboard changes from the other comments too.
4. Choose the score by ear, not only by measurement. I chose candidate c3 because it puts the least energy in the speech band, and I confirmed it by listening, but I only compared three candidates from one prompt.
5. Add the ten written experiments to `EXPERIMENTS.md` with the evidence the logs now provide.
