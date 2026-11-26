"""Unit tests for P3: Ethiopic font glyphs, overlay PNGs, cache key sensitivity, and end card."""
import subprocess

from pipeline import assemble, keyframes, music, narration, shots
from pipeline.common import load_storyboard


def test_ethiopic_font_renders_every_glyph():
    font = assemble.ethiopic_font(48)
    for ch in "የኮድ ቅርስ":
        if ch.isspace():
            continue
        bbox = font.getbbox(ch)
        assert bbox is not None and (bbox[2] - bbox[0]) > 0 and (bbox[3] - bbox[1]) > 0, (
            f"missing glyph for {ch!r}"
        )


def test_overlay_png_and_cache_key_change(sandbox):
    import pipeline.common as c
    sb = load_storyboard()
    narration.run(sb, only=["s04"])
    keyframes.run(sb, only=["s04"])
    shots.build(sb, only=["s04"])
    music.build(sb)

    clip = c.OUTPUTS / assemble.read_manifest("shots")["s04"]["path"]
    ov1 = [{"text": "PUNCH CARDS · 1950s", "start_s": 1.0, "end_s": 3.5, "position": "upper_third"},
           {"text": "C · 1972", "start_s": 5.5, "end_s": 8.5, "position": "upper_third"}]  # the film itself ships without overlays (user test 1)
    seg1 = assemble.segment(clip, 9.0, "9x16", 24, overlays=ov1, shot_id="s04")
    assert seg1.exists()
    ov_pngs = list((c.OUTPUTS / "assembly" / "overlays" / "9x16").glob("s04__*.png"))
    assert len(ov_pngs) == len(ov1)

    # Unchanged overlays hit the same segment file
    seg1_again = assemble.segment(clip, 9.0, "9x16", 24, overlays=ov1, shot_id="s04")
    assert seg1_again == seg1

    # Changing one overlay's text produces a new cache key for s04 only
    ov2 = [dict(ov1[0], text="PUNCH CARDS · 1951"), *ov1[1:]]
    seg2 = assemble.segment(clip, 9.0, "9x16", 24, overlays=ov2, shot_id="s04")
    assert seg2 != seg1

    # End card builds with manuscript border and Ethiopic title
    ec = assemble.end_card(sb, "9x16", 24)
    assert ec.exists()
    assert ec.with_suffix(".png").exists()
