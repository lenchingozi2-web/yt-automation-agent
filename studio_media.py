"""Build legible, format-matched thumbnails from a production's own footage."""
from __future__ import annotations

from io import BytesIO
from functools import lru_cache
import math
import re
import subprocess
from pathlib import Path

from PIL import Image, ImageDraw, ImageFilter, ImageFont, ImageOps, ImageStat


THUMBNAIL_MAX_BYTES = 2_000_000

VISUAL_STOP_WORDS = frozenset("""
    about above after again against along also always among another any around because before being below between both
    but can cannot could did does doing down each enough even every first for from further get getting give going had
    has have having here how into its just keep last let like look made make many may might more most much must near
    need next now off often once one only onto other our out over own really same say see should show some something
    still such take tell than that the their them then there these they thing think this those though through together
    too toward under until upon use used using very want was way well were what when where which while who why will
    with within without would you your footage illustration scene video background context reveal study researchers
    result research finding example group difference actual common data model system comparison method process approach
""".split())

# Each alternative names the visible subject or a concrete part of the same procedure.
VISUAL_SUBJECTS = (
    (r"\bocean waves?\b", ("ocean waves", "sea waves", "surf")),
    (r"\b(?:kidney stones?|renal calculi|nephrolithiasis)\b", ("kidney stone", "renal calculus")),
    (r"\b(?:urine tests?|urine samples?|urinalysis|urine collections?)\b", ("urine sample", "urine test", "specimen cup")),
    (r"\b(?:blood\s?spots?|heel pricks?)\b", ("newborn heel prick", "blood sample", "heel blood test")),
    (r"\b(?:newborns?|infants?|bab(?:y|ies))\b", ("newborn", "infant", "baby")),
    (r"\b(?:genetic tests?|methylation|dna)\b", ("dna", "genetic test")),
    (r"\b(?:microscopes?|microscopy)\b", ("microscope", "microscopy")),
    (r"\b(?:classrooms?|pupils?|students?|teachers?|teaching)\b", ("classroom", "teacher", "student", "pupil", "lesson")),
    (r"\b(?:wearables?|smart\s?watches?)\b", ("smartwatch", "fitness tracker", "wearable")),
)


def visual_words(text: str) -> list[str]:
    words = re.findall(r"[a-z][a-z-]{2,}", text.casefold())
    return [word[:-3] + "y" if word.endswith("ies") else
            word[:-1] if len(word) > 3 and word.endswith("s") and not word.endswith(("ss", "is", "us")) else word
            for word in words if word not in VISUAL_STOP_WORDS]


def footage_intent(spoken: str, narration: str, queries: list[str], override: bool = False) -> tuple[list[str], list, set]:
    local = set(visual_words(spoken))
    scene = set(visual_words(narration))
    subjects = [] if override else [alternatives for pattern, alternatives in VISUAL_SUBJECTS if re.search(pattern, spoken, re.I)]
    planned = [(query, set(visual_words(query))) for query in queries if visual_words(query)]
    if override and planned:
        return [query for query, _ in planned][:5], [], set.union(*(words for _, words in planned))
    focus = spoken
    if len(local) < 2 and not subjects and not (local & set().union(*(words for _, words in planned))):
        focus = narration
        local = scene
        subjects = [alternatives for pattern, alternatives in VISUAL_SUBJECTS if re.search(pattern, narration, re.I)]
    primary = [alternatives[0] for alternatives in subjects]
    ranked = sorted(planned, key=lambda pair: len(pair[1] & local), reverse=True)
    related = [query for query, words in ranked if words & local or any(query in group for group in subjects)]
    words = list(dict.fromkeys(visual_words(focus)))
    derived = [" ".join(words[index:index + 3]) for index in (0, 3) if len(words[index:index + 3]) >= 2]
    alternate = [alternatives[1] for alternatives in subjects if len(alternatives) > 1]
    return list(dict.fromkeys(primary + related + alternate + derived))[:4], subjects, local


def footage_relevance(tags: str, subjects: list, local: set, query: str, override: bool = False) -> int:
    if re.search(r"green.?screen|chroma|subscribe|like button|countdown|logo reveal|\b(?:intro|outro)\b", tags, re.I):
        return 0
    words = set(visual_words(tags))
    matches = sum(any(set(visual_words(term)) <= words for term in group) for group in subjects)
    overlap = len(words & local)
    requested = set(visual_words(query))
    hit = len(words & requested)
    if (subjects and not matches) or (not subjects and (not overlap or overlap < min(2, len(local)))):
        return 0
    if override and (not hit or hit < min(2, len(requested))):
        return 0
    return matches * 20 + overlap * 3 + hit


CONTEXT_FOOTAGE = (
    (r"\b(?:kidney|renal|urine|blood\s?spot|blood sample|microscop\w*|genetic\w*|specimen|biomarker\w*|laborator\w*)\b",
     ("laboratory equipment", "laboratory scientist")),
    (r"\b(?:health\w*|clinic\w*|doctor\w*|hospital\w*|patient\w*|diagnos\w*|treatment\w*|medic\w*|nurs\w*)\b",
     ("hospital corridor", "medical office")),
    (r"\b(?:learn\w*|educat\w*|student\w*|pupil\w*|teach\w*|school\w*|classroom\w*|lesson\w*|recall)\b",
     ("student notebook", "school classroom")),
    (r"\b(?:artificial intelligence|ai|machine learning|comput\w*|software|algorithm\w*|robot\w*|neural network\w*|programming)\b",
     ("computer workstation", "typing keyboard")),
    (r"\b(?:ocean\w*|marine|sea|beach\w*|coast\w*)\b", ("ocean beach", "sea coast")),
    (r"\b(?:forest\w*|woodland\w*|trek\w*|wildlife|ecolog\w*)\b", ("forest path", "woodland trees")),
)


def contextual_searches(spoken: str, narration: str, title: str = "") -> list[str]:
    for text in (spoken, narration, title):
        for pattern, searches in CONTEXT_FOOTAGE:
            if re.search(pattern, text, re.I):
                return list(searches)
    return ["desk notebook", "abstract light"]


@lru_cache(maxsize=4)
def motion_background(size: tuple[int, int]) -> Image.Image:
    width, height = size
    gradient = Image.new("RGB", (80, 80))
    gradient.putdata([(round(10 + 14 * glow), round(22 + 44 * glow), round(30 + 40 * glow))
                      for y in range(80) for x in range(80)
                      for glow in [max(0, 1 - math.hypot((x - 66) / 75, (y - 16) / 60))]])
    image = gradient.resize(size, Image.Resampling.BICUBIC)
    draw = ImageDraw.Draw(image, "RGBA")
    step = max(12, width // 26)
    for y in range(step, height, step):
        for x in range(step, width, step):
            draw.ellipse((x, y, x + 2, y + 2), fill=(159, 228, 199, 40))
    stroke = max(1, width // 750)
    for ring in range(4):
        radius = width * (.18 + ring * .065)
        x, y = width * .88, height * .18
        draw.arc((x - radius, y - radius, x + radius, y + radius), 30, 300,
                 fill=(138, 226, 203, 56 - ring * 9), width=stroke)
    return image


def narration_motion(text: str, path: Path, size: tuple[int, int], frames: int) -> Path:
    """Render staggered words and a moving accent on an editorial background."""
    # Composite at a smaller resolution and let the regular scene renderer upscale it.
    scale = min(1, 900 / max(size))
    width, height = (max(2, round(side * scale / 2) * 2) for side in size)
    background = motion_background((width, height))
    margin = round(width * (.12 if height > width else .15))
    tokens = text.split()
    words = tokens[:20] or ["…"]
    if len(tokens) > 20:
        words[-1] = words[-1].rstrip(".,;:!?") + "…"
    available = width - 2 * margin
    font_size = round(width * (.067 if height > width else .053))
    face = font(max(14, font_size))
    while face.size > 14:
        line_width, lines = 0, 1
        gap = face.getlength(" ")
        for word in words:
            advance = face.getlength(word)
            if line_width and line_width + gap + advance > available:
                lines += 1
                line_width = advance
            else:
                line_width += advance + (gap if line_width else 0)
        if lines * face.size * 1.22 < height * .51:
            break
        face = font(max(14, face.size - 2))
    space = round(face.getlength(" "))
    spacing = round(face.size * 1.22)
    layout = []
    x, row = margin, 0
    for word in words:
        if face.getlength(word) > available:
            while word and face.getlength(word + "…") > available:
                word = word[:-1]
            word += "…"
        advance = round(face.getlength(word))
        if x + advance > width - margin and x > margin:
            x, row = margin, row + 1
        layout.append((word, x, row))
        x += advance + space
    top = round(height * .45) - row * spacing // 2
    first_line = top - spacing // 2
    reveal = min(24, max(1, round(frames * .58)))
    settle = max(1, min(8, frames // 5))
    temporary = path.with_suffix(".part.mp4")
    command = ["ffmpeg", "-y", "-v", "error", "-f", "rawvideo", "-pixel_format", "rgb24", "-video_size",
               f"{width}x{height}", "-framerate", "30", "-i", "-", "-an", "-c:v", "libx264", "-preset", "ultrafast",
               "-crf", "17", "-threads", "2", "-bf", "0", "-pix_fmt", "yuv420p", str(temporary)]
    try:
        with subprocess.Popen(command, stdin=subprocess.PIPE) as encoder:
            assert encoder.stdin is not None
            stream = encoder.stdin
            try:
                settled_frame = b""
                for frame in range(frames):
                    if settled_frame:
                        stream.write(settled_frame)
                        continue
                    image = background.copy()
                    draw = ImageDraw.Draw(image, "RGBA")
                    line_width = max(1, round(width * (.035 + .09 * min(1, frame / reveal))))
                    draw.rounded_rectangle((margin, first_line, margin + line_width, first_line + max(3, width // 150)),
                                           radius=2, fill="#e7c782")
                    for index, (word, x, row) in enumerate(layout):
                        start = index * reveal / len(layout)
                        if frame < start:
                            continue
                        progress = min(1, (frame - start + 1) / settle)
                        ease = 1 - (1 - progress) ** 3
                        y = top + row * spacing + round((1 - ease) * face.size * .65)
                        color = (235, 209, 149) if row == 0 else (248, 244, 232)
                        draw.text((x, y), word, font=face, fill=(*color, round(255 * ease)), anchor="lt")
                    pixels = image.tobytes()
                    stream.write(pixels)
                    if frame >= reveal + settle:
                        settled_frame = pixels
                stream.close()
                if encoder.wait(timeout=30):
                    raise RuntimeError("FFmpeg could not encode the narration animation")
            except (BrokenPipeError, subprocess.TimeoutExpired):
                encoder.kill()
                raise
        temporary.replace(path)
    finally:
        temporary.unlink(missing_ok=True)
    return path


def prepare_stock_image(data: bytes, path: Path, size: tuple[int, int]) -> None:
    with Image.open(BytesIO(data)) as source:
        if source.width * source.height > 16_000_000 or min(source.size) < 320:
            raise ValueError("Unsuitable stock photo dimensions")
        picture = ImageOps.exif_transpose(source).convert("RGB")
        ImageOps.fit(picture, size, method=Image.Resampling.LANCZOS).save(path, "PNG")


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
        for timestamp in ((0,) if source.suffix.lower() == ".png" else timestamps):
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
