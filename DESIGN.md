# Design

> Written **before** generating anything. Fill in each section, then keep it honest as
> the tests change your mind.

## Audience and intent

- **Who watches:** young Ethiopian developers and CS students (20–30) who use AI tools every day and quietly worry what that means for their skills. A second audience is my family, who see my work as "computer stuff".
- **Where:** Instagram Reels and the developer Telegram groups I am in, on a phone, vertical, usually with sound on.
- **The one feeling at the end:** relief and pride. Our craft is not ending; it is one more link in a very old Ethiopian line of makers, and the joy that made us start is still there.
- **What they should understand:** making things easier has always been the history of programming (punch cards, assembly, C), and the people worth being are the ones who still want to know how it works. Scribes, weavers and painters feared the new thing too.
- **Logline:** late at night in Addis Ababa, a young backend engineer who fears AI is making his craft worthless is visited by a softly glowing Ethiopian wolf, a voice from his own subconscious, who shows him the long line of Ethiopian makers he belongs to.
- **Why me, why now:** I am that engineer. "Everyone is a programmer now" is something I hear weekly, and I wanted to answer it in my own language and in the visual language of Ethiopian church painting, not in English tech-talk.

Inspiration: the theme of a reference reel (craft versus ease in programming). I take the question, not the lines: the script is original, nothing is translated or copied from it, and the look (church painting, a wolf as inner voice) is mine.

Honest note on timing: this file was filled in after the first generation pass, not before it. The audience text above is the same as `storyboard.json` → `audience`, which was written first; the git history of both files is the evidence.

## Story in five beats

1. **The fear (s01–s03):** Peneal at his desk at night. "Everyone is a programmer now." He spent years learning what happens underneath, and he is afraid of losing it.
2. **The visitor (s02, s04):** the wolf, his own subconscious, shows the history of programming as painted panels: every step was someone trying to say what they meant more easily.
3. **The objection (s05–s06):** "If it's easy, who will know how it works when it breaks?" The wolf: "Someone always does. The one who wants to."
4. **The line of makers (s07–s08):** scribes, weavers, painters and telegraph operators, who feared the new thing too. Peneal stands at the end of that line.
5. **The joy (s09–s11):** the first time the screen said "Hello, World" back. He laughs, the wolf fades, and he types again: same desk, a different man.

## Shot reasons

Every shot's reason lives in `storyboard.json` (`shots[].reason`). `make validate` prints them.

## User test 1: the radio cut

- **Date / build:** _date, `outputs/final/radio_cut.mp4`, storyboard commit `abc123`_
- **How:** _in person / Slack; what I asked them ("what did you feel at the end?", "where did you drift?")_

| # | Who (relation to audience) | What they said | What I'll change |
|---|---|---|---|
| 1 | Sebrina A. (tester, target audience) | At the end the Amharic says "ሰላም" (selam) for "hello", but it should be exactly "Hello, World", untranslated, because that is the programmer's first program | s09 narration now reads `ስክሪኑ መጀመሪያ "Hello, World" ብሎ ሲመልስልህ ታስታውሳለህ?` and the subtitle says `"Hello, World"`. One edit regenerated one TTS call (s09) |
| 2 | | | |
| 3 | | | |

**Changes made to the storyboard because of this test:** s09 now says "Hello, World" literally (narration and subtitle) instead of the Amharic "ሰላም".

## User test 2: the near-final cut

- **Date / build:** _…_

| # | Who | What they said | What I'll change |
|---|---|---|---|
| 1 | Tahir A. (tester, target audience) | The on-screen labels (PUNCH CARDS · 1950s, SCRIBE, WEAVER…) beyond the subtitles are clutter; remove them | Removed every `overlays` entry from `storyboard.json` (s04, s07). The overlay renderer stays in `pipeline/assemble.py`, covered by a test that supplies its own overlays |
| 2 | | | |
| 3 | | | |

**Changes made:** removed all `overlays` from `storyboard.json`.
