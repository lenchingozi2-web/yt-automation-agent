"""Clinical illustration for the original Prader–Willi upload."""
from __future__ import annotations

from pathlib import Path

from PIL import Image, ImageDraw, ImageFilter

from studio_media import fit_headline, save_thumbnail, thumbnail_size


def make_prader_willi_thumbnail(output: Path, *, ratio: str = "16:9") -> Path:
    width, height = thumbnail_size(ratio)
    portrait = ratio == "9:16"
    canvas = Image.new("RGBA", (width, height), "#050d22")
    glow = Image.new("RGBA", canvas.size)
    center_x, center_y = (width * .50, height * .37) if portrait else (width * .79, height * .48)
    radius = 440 if portrait else 330
    ImageDraw.Draw(glow).ellipse(
        (center_x - radius, center_y - radius, center_x + radius, center_y + radius),
        fill=(53, 79, 204, 190),
    )
    canvas = Image.alpha_composite(canvas, glow.filter(ImageFilter.GaussianBlur(95)))
    draw = ImageDraw.Draw(canvas)

    def chromosome(cx: float, cy: float, scale: float, color: str, shine: str):
        stroke = round(62 * scale)
        for side in (-1, 1):
            points = [
                (cx + side * scale * (4 + 56 * (2 * step / 100 - 1) ** 2),
                 cy + scale * (-150 + 300 * step / 100))
                for step in range(101)
            ]
            draw.line(points, fill="#22316a", width=stroke + 20, joint="curve")
            draw.line(points, fill=color, width=stroke, joint="curve")
            draw.line([(x - 7 * scale, y - 8 * scale) for x, y in points],
                      fill=shine, width=max(4, stroke // 4), joint="curve")
        draw.ellipse((cx - 25 * scale, cy - 17 * scale, cx + 25 * scale, cy + 17 * scale),
                     fill="#f34f84", outline="#ffc0d6", width=3)

    scale = 2.15 if portrait else 1.10
    spacing = 166 if portrait else 94
    chromosome(center_x - spacing, center_y, scale, "#7852ef", "#cabcff")
    chromosome(center_x + spacing, center_y, scale * .78, "#248bf3", "#96e4ff")

    lines, typeface = fit_headline(draw, "PRADER–WILLI SYNDROME",
                                   900 if portrait else 670, 128 if portrait else 96, 2)
    line_height = round(typeface.size * 1.15)
    top = round(height * .69) if portrait else round(height * .49 - len(lines) * line_height / 2)
    for i, line in enumerate(lines):
        left = (width - draw.textlength(line, font=typeface)) / 2 if portrait else 62
        draw.text((left, top + i * line_height), line, font=typeface,
                  fill="#e8f749" if i == len(lines) - 1 else "#fafcff")
    return save_thumbnail(canvas, output, ratio)
