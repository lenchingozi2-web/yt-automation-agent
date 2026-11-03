"""Deterministic Pixabay-frame thumbnails for Studio (no paid image API).

YouTube custom thumbnails are 1280x720 JPEG; this routine does not copy stock images
from other services. It uses the already licensed/rendered Pixabay scene footage.
"""
from __future__ import annotations

import re
import subprocess
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont, ImageOps


def font(size: int) -> ImageFont.FreeTypeFont | ImageFont.ImageFont:
    for name in (
        "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",
        "/usr/share/fonts/truetype/liberation2/LiberationSans-Bold.ttf",
    ):
        if Path(name).exists():
            return ImageFont.truetype(name, size=size)
    return ImageFont.load_default()


def fit_headline(draw: ImageDraw.ImageDraw, headline: str, width: int = 1050) -> tuple[list[str], ImageFont.ImageFont]:
    words = re.sub(r"\s+", " ", headline).strip().upper().split()
    if not words:
        words = ["NEW", "VIDEO"]
    # 2-4 large lines rather than tiny, paragraph-sized words.
    for size in (110, 102, 94, 86, 78, 70, 62, 55):
        candidate = font(size)
        lines: list[str] = []
        current = ""
        for word in words:
            attempt = (current + " " + word).strip()
            if draw.textbbox((0, 0), attempt, font=candidate)[2] <= width:
                current = attempt
            else:
                if current:
                    lines.append(current)
                current = word
        if current:
            lines.append(current)
        if len(lines) <= 4 and all(draw.textbbox((0, 0), line, font=candidate)[2] <= width for line in lines):
            return lines, candidate
    # Avoid recursion for an unbreakable or very long title.
    smallest = font(48)
    clipped = (headline[:90].rstrip() + '...') if len(headline) > 90 else headline
    lines = []
    line = ''
    for token in clipped.split():
        candidate = (line + ' ' + token).strip()
        if draw.textbbox((0, 0), candidate, font=smallest)[2] > width and line:
            lines.append(line)
            line = token
        else:
            line = candidate
    if line:
        lines.append(line)
    return lines[:5], smallest


def create_thumbnail(source_video: Path, headline: str, output: Path, brand: str = "LENXIQ") -> Path:
    output.parent.mkdir(parents=True, exist_ok=True)
    snapshot = output.with_suffix(".frame.jpg")
    try:
        subprocess.run(
            ["ffmpeg", "-hide_banner", "-loglevel", "error", "-y", "-ss", "0.2",
             "-i", str(source_video), "-frames:v", "1", "-q:v", "3", str(snapshot)],
            timeout=60, check=True, capture_output=True,
        )
        with Image.open(snapshot) as src:
            background = ImageOps.fit(src.convert("RGB"), (1280, 720), method=Image.Resampling.LANCZOS)
    except (OSError, ValueError, subprocess.SubprocessError):
        background = Image.new("RGB", (1280, 720), "#14232b")
    finally:
        snapshot.unlink(missing_ok=True)

    # Readability-first gradient; image remains visible on the right.
    overlay = Image.new("RGBA", (1280, 720), (0, 0, 0, 0))
    pixels = overlay.load()
    for x in range(1280):
        alpha = max(38, int(218 * (1 - x / 1280) ** 1.75))
        for y in range(720):
            pixels[x, y] = (5, 12, 18, alpha)
    thumbnail = Image.alpha_composite(background.convert("RGBA"), overlay)
    draw = ImageDraw.Draw(thumbnail)
    accent = "#d3f36b"
    draw.rounded_rectangle((65, 56, 310, 112), radius=18, fill=accent)
    draw.text((85, 68), "LENXIQ  STUDIO", fill="#0b1115", font=font(22))
    headline = headline.strip() or "A NEW STORY"
    lines, text_font = fit_headline(draw, headline, width=1050)
    line_height = int(getattr(text_font, "size", 70) * 1.22)
    top = max(170, int(470 - len(lines) * line_height / 2))
    for i, line in enumerate(lines):
        y = top + i * line_height
        draw.text((70 + 3, y + 4), line, font=text_font, fill=(0, 0, 0, 155), stroke_width=3)
        draw.text((70, y), line, font=text_font, fill="white", stroke_width=1, stroke_fill="#263039")
    draw.rounded_rectangle((70, 642, 370, 650), radius=4, fill=accent)
    thumbnail.convert("RGB").save(output, "JPEG", quality=84, optimize=True, progressive=True)
    if output.stat().st_size >= 2_000_000:
        thumbnail.convert("RGB").save(output, "JPEG", quality=68, optimize=True)
    return output
