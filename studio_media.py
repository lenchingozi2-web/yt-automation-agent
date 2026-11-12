"""Build legible, format-matched thumbnails from a production's own footage."""
from __future__ import annotations

import math
import re
import subprocess
from pathlib import Path

from PIL import Image, ImageDraw, ImageFilter, ImageFont, ImageOps, ImageStat


THUMBNAIL_MAX_BYTES = 2_000_000


def thumbnail_size(ratio: str) -> tuple[int, int]:
    return (1080, 1920) if ratio == "9:16" else (1280, 720)


def font(size: int) -> ImageFont.FreeTypeFont | ImageFont.ImageFont:
    for name in (
        "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",
        "/usr/share/fonts/truetype/liberation2/LiberationSans-Bold.ttf",
    ):
        if Path(name).exists():
            return ImageFont.truetype(name, size=size)
    return ImageFont.load_default(size=size)


def fit_headline(draw: ImageDraw.ImageDraw, headline: str, width: int, max_size: int, max_lines: int):
    words = re.sub(r"\s+", " ", headline).strip().upper().split() or ["NEW", "VIDEO"]
    for size in range(max_size, 31, -4):
        candidate = font(size)
        lines = [""]
        for word in words:
            line = (lines[-1] + " " + word).strip()
            if draw.textlength(line, font=candidate) <= width or not lines[-1]:
                lines[-1] = line
            else:
                lines.append(word)
        if len(lines) <= max_lines and all(draw.textlength(line, font=candidate) <= width for line in lines):
            return lines, candidate
    # A manually pasted headline may have no spaces. Keep it inside the title-safe area.
    candidate = font(32)
    line = " ".join(words)
    while draw.textlength(line + "…", font=candidate) > width and line:
        line = line[:-1]
    return [line.rstrip() + "…"], candidate


def verify_thumbnail(path: Path, ratio: str | None = None) -> None:
    with Image.open(path) as image:
        if image.format != "JPEG" or (ratio and image.size != thumbnail_size(ratio)):
            raise ValueError("Thumbnail does not match the required JPEG format and dimensions.")
        image.verify()
    if path.stat().st_size >= THUMBNAIL_MAX_BYTES:
        raise ValueError("Thumbnail exceeds the Studio JPEG size limit.")


def save_thumbnail(image: Image.Image, output: Path, ratio: str) -> Path:
    output.parent.mkdir(parents=True, exist_ok=True)
    for quality in (90, 82, 72, 62):
        image.convert("RGB").save(output, "JPEG", quality=quality, optimize=True, progressive=True)
        if output.stat().st_size < THUMBNAIL_MAX_BYTES:
            verify_thumbnail(output, ratio)
            return output
    raise ValueError("Thumbnail could not fit the Studio JPEG size limit.")


def best_frame(sources: list[Path], output: Path, size: tuple[int, int], timestamps: tuple[float, ...]) -> Image.Image:
    snapshot = output.with_suffix(".frame.jpg")
    best = None
    best_score = -math.inf
    for source in sources[:3]:
        for timestamp in timestamps:
            snapshot.unlink(missing_ok=True)
            try:
                subprocess.run(
                    ["ffmpeg", "-hide_banner", "-loglevel", "error", "-y", "-ss", str(timestamp),
                     "-i", str(source), "-frames:v", "1", "-q:v", "3", str(snapshot)],
                    timeout=25, check=True, capture_output=True,
                )
                with Image.open(snapshot) as image:
                    candidate = ImageOps.fit(image.convert("RGB"), size, method=Image.Resampling.LANCZOS)
                sample = ImageOps.grayscale(candidate.resize((160, 160)))
                stats = ImageStat.Stat(sample)
                edges = ImageStat.Stat(sample.filter(ImageFilter.FIND_EDGES).crop((2, 2, 158, 158))).mean[0]
                exposure_penalty = max(0, 65 - stats.mean[0], stats.mean[0] - 195)
                score = stats.stddev[0] + 1.5 * edges - exposure_penalty
                if score > best_score:
                    best, best_score = candidate, score
            except (OSError, ValueError, subprocess.SubprocessError):
                continue
            finally:
                snapshot.unlink(missing_ok=True)
    if best is None:
        raise ValueError("No usable frame was available for the thumbnail.")
    return best


def create_thumbnail(source_video: Path | list[Path], headline: str, output: Path, *,
                     ratio: str = "16:9", timestamps: tuple[float, ...] = (.2, 1.4)) -> Path:
    output.parent.mkdir(parents=True, exist_ok=True)
    width, height = thumbnail_size(ratio)
    portrait = ratio == "9:16"
    sources = source_video if isinstance(source_video, list) else [source_video]
    background = best_frame(sources, output, (width, height), timestamps)
    # Keep the photographic subject visible and darken only the title region.
    gradient_size = (1, height) if portrait else (width, 1)
    gradient = Image.new("L", gradient_size)
    steps = height if portrait else width
    gradient.putdata([
        int(24 + 212 * (i / (steps - 1)) ** 2.2) if portrait
        else int(25 + 204 * (1 - i / (steps - 1)) ** 1.6)
        for i in range(steps)
    ])
    dark = Image.new("RGB", (width, height), "#071119")
    result = Image.composite(dark, background, gradient.resize((width, height)))
    draw = ImageDraw.Draw(result)
    text_width = 872 if portrait else 820
    lines, title_font = fit_headline(draw, headline, text_width, 128 if portrait else 100, 4)
    line_height = round(title_font.size * 1.12)
    top = (round(height * .67) - len(lines) * line_height // 2 if portrait
           else round(height * .53) - len(lines) * line_height // 2)
    for index, line in enumerate(lines):
        x = round((width - draw.textlength(line, font=title_font)) / 2) if portrait else 64
        draw.text((x, top + index * line_height), line, font=title_font,
                  fill="#dff478" if index == len(lines) - 1 else "white",
                  stroke_width=2, stroke_fill="#071119")
    return save_thumbnail(result, output, ratio)
