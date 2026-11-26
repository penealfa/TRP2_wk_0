"""Local stand-ins used when HS_OFFLINE=1 (and by the tests). They let the whole
pipeline run end to end with no keys, so assembly and caching can be developed
before any quota is spent. Output is clearly marked as placeholder."""
from __future__ import annotations

import textwrap
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

from pipeline.common import ffmpeg, stable_hash

FONT_CANDIDATES = [
    "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
    "/Library/Fonts/Arial.ttf",
    "C:/Windows/Fonts/arial.ttf",
]


def font(size: int):
    for p in FONT_CANDIDATES:
        if Path(p).exists():
            return ImageFont.truetype(p, size)
    return ImageFont.load_default()


def image(path: Path, title: str, body: str, width: int, height: int, seed: int) -> None:
    h = int(stable_hash([title, seed], 6), 16)
    c1 = (60 + h % 120, 40 + (h >> 8) % 90, 30 + (h >> 16) % 60)
    c2 = (20, 18, 16)
    img = Image.new("RGB", (width, height), c2)
    draw = ImageDraw.Draw(img)
    for y in range(height):
        t = y / height
        draw.line([(0, y), (width, y)], fill=tuple(int(c1[i] * (1 - t) + c2[i] * t) for i in range(3)))
    draw.text((width * 0.08, height * 0.08), "PLACEHOLDER " + title, font=font(width // 18), fill=(255, 240, 210))
    wrapped = "\n".join(textwrap.wrap(body, 34)[:12])
    draw.multiline_text((width * 0.08, height * 0.25), wrapped, font=font(width // 30), fill=(240, 230, 210), spacing=8)
    img.save(path)


def speech(path: Path, text: str, wps: float = 2.3) -> None:
    """A soft tone as long as the line would take to speak, so timing and ducking work."""
    dur = max(1.0, len(text.split()) / wps)
    ffmpeg(["-f", "lavfi", "-i", f"sine=frequency=330:duration={dur:.2f}",
            "-af", "volume=0.25,afade=t=in:d=0.1,afade=t=out:st=%.2f:d=0.2" % max(0, dur - 0.2),
            "-ar", "24000", "-ac", "1", str(path)])


def music(path: Path, duration: float, seed: int) -> None:
    """A quiet three-note drone pad."""
    base = 110 + seed % 30
    ffmpeg(["-f", "lavfi", "-i", f"sine=frequency={base}:duration={duration}",
            "-f", "lavfi", "-i", f"sine=frequency={base * 1.5:.1f}:duration={duration}",
            "-f", "lavfi", "-i", f"sine=frequency={base * 2.4:.1f}:duration={duration}",
            "-filter_complex", "amix=inputs=3,volume=0.6,tremolo=f=0.2:d=0.4,afade=t=in:d=2",
            "-ar", "44100", "-ac", "2", str(path)])
