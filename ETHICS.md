# Ethics: consent, licences, labelling

## Consent

Each real person who appears, is depicted or is voiced in the film gets an entry here
**before** anything of them is generated. `storyboard.json` links each one through
`characters[].consent_ref` or `defaults.narrator`, and validation fails if a real
character has no consent reference.

### peneal

- **Person:** Peneal, the film-maker. Appears as the main character, as a painted
  likeness. His lines are spoken by a stock Gemini TTS voice (Charon), not his own voice.
- **Consent:** I consent to an AI-generated likeness of me appearing in this film, to a
  synthetic voice speaking my lines, and to publishing the film on Instagram, YouTube and GitHub.
- **Words:** I wrote or approved every line spoken by "Peneal" in `storyboard.json`.
- **Likeness source:** one photo of me (`assets/refs/peneal.jpg`, not committed) sent as a
  reference image to Cloudflare Workers AI (FLUX.2 klein, listed as zero data retention),
  plus the text description in `storyboard.json`.
- **Voice cloning:** none. My voice is not recorded, cloned or used to condition any model.
- **Date:** Nov-26-2026

### wolf (fictional)

An Ethiopian wolf standing in for Peneal's own inner voice. Voiced by Gemini TTS, not by
a real person, so no consent is needed. Labelled on the end card.

### Cultural note

The film borrows the visual language of traditional Ethiopian painting. The wolf is shown
with a faint, dreamlike glow only. Before publishing, I show the near-final cut to a family
member or friend and record their reaction here. _Outcome: …_

### _person-id_ (copy for each real person shown or described)

- **Person and relation:** _…_
- **Shown or voiced how:** _likeness generated from a description / photo reference / voice / named only_
- **What they agreed to:** _the use, the words, where it's published_
- **How consent was recorded:** _e.g. voice note saved privately on <date>, a written message, or signed in person_
- **Date:** _…_

## Private data

Free API tiers may train on what they receive. Kept out of prompts: _e.g. full names,
the village name, health details_.

## Licences

| Component | Licence / terms | Output use |
|---|---|---|
| Wan 2.2 TI2V-5B | Apache 2.0 | allowed |
| ACE-Step 1.5 | MIT License (`github.com/ace-step/ACE-Step-1.5/blob/main/LICENSE`) | allowed |
| FLUX.1 schnell | Apache 2.0 | allowed |
| FLUX.2 klein 4B | Cloudflare Workers AI (listed as zero data retention; BFL FLUX.2 [klein] 4B model terms) | allowed (personal/non-commercial; check BFL terms for commercial) |
| Gemini API / TTS | Google Generative AI terms, free tier | _…_ |
| Cloudflare Workers AI | Cloudflare terms (zero data retention for flux-2-klein-4b) | allowed |
| DejaVu Sans (subtitles, Latin overlays) | Bitstream Vera / public-domain-style licence | allowed |
| Noto Sans Ethiopic (`assets/fonts/NotoSansEthiopic-Regular.ttf`) | SIL Open Font License 1.1 (`assets/fonts/OFL.txt`) | allowed |
| My narration | mine | allowed |

## Labelling

- The end card says the film was made with AI (enforced by the schema).
- The platform's AI-content disclosure is switched on when posting.
- Provenance metadata or watermarks from generators are left intact. No stripping.
- Style prompts describe qualities (warm, grainy, hand-painted) and never name living artists.
