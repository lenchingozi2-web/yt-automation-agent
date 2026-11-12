"""A medically careful, readable Prader–Willi video thumbnail; free, offline artwork."""
from __future__ import annotations

import math
from pathlib import Path
from PIL import Image, ImageDraw, ImageFont, ImageFilter

W, H = 1280, 720
LIME = (232, 247, 73)
WHITE = (250, 252, 255)

def face(size: int):
    for path in ("/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",
                 "/usr/share/fonts/truetype/liberation2/LiberationSans-Bold.ttf"):
        if Path(path).exists():
            return ImageFont.truetype(path, size)
    return ImageFont.load_default()

def make_prader_willi_thumbnail(output: Path) -> Path:
    """Explain chromosome 15 involvement without falsely suggesting deletion is universal."""
    output = Path(output)
    output.parent.mkdir(parents=True, exist_ok=True)
    im = Image.new("RGB", (W, H))
    pixels = im.load()
    for y in range(H):
        for x in range(W):
            halo = max(0, 1 - math.hypot((x - 1050) / 850, (y - 325) / 610))
            pixels[x, y] = (int(4 + halo * 12), int(13 + halo * 20), int(33 + halo * 42))
    draw = ImageDraw.Draw(im)
    for x in range(26, W, 42):
        for y in range(34, H - 8, 42):
            if x > 745:
                draw.ellipse((x-1, y-1, x+1, y+1), fill=(32, 55, 81))
    glow = Image.new("RGBA", (W, H), (0, 0, 0, 0))
    gd = ImageDraw.Draw(glow)
    gd.ellipse((810, 20, 1350, 690), fill=(69, 49, 190, 100))
    gd.ellipse((890, 125, 1205, 575), fill=(31, 126, 215, 95))
    im = Image.alpha_composite(im.convert("RGBA"), glow.filter(ImageFilter.GaussianBlur(70)))
    d = ImageDraw.Draw(im)
    def chromosome(cx, cy, scale, stroke, color, accent):
        paths = [((-58,-162),(-6,0),(-65,153)), ((58,-162),(5,0),(65,153))]
        for start, mid, end in paths:
            points = []
            for j in range(101):
                t = j/100
                x = (1-t)**2*start[0]+2*(1-t)*t*mid[0]+t*t*end[0]
                y = (1-t)**2*start[1]+2*(1-t)*t*mid[1]+t*t*end[1]
                points.append((int(cx+x*scale),int(cy+y*scale)))
            d.line(points, fill=(33,44,102,240), width=stroke+24, joint="curve")
            d.line(points, fill=color, width=stroke, joint="curve")
            d.line([(x-7,y-8) for x,y in points], fill=accent, width=max(4,stroke//3), joint="curve")
        d.ellipse((cx-26*scale,cy-18*scale,cx+26*scale,cy+18*scale),
                  fill=(243,79,132,255),outline=(255,183,210,255),width=3)
    chromosome(995, 326, 1.12, 66, (120,82,239,255), (202,188,255,255))
    chromosome(1145, 324, .83, 49, (36,139,243,255), (150,228,255,255))
    for angle in range(205,335,4):
        rad = math.radians(angle)
        x,y = 1042+290*math.cos(rad), 330+255*math.sin(rad)
        if 0 <= x < W and 0 <= y < H:
            d.ellipse((x-3,y-3,x+3,y+3),fill=(65,199,255,200))
    shade = Image.new("RGBA",(W,H),(0,0,0,0))
    sd = ImageDraw.Draw(shade)
    for x in range(770):
        sd.line((x,0,x,H),fill=(0,4,12,int(178*(1-x/870)**0.7)))
    im = Image.alpha_composite(im, shade)
    d = ImageDraw.Draw(im)
    d.rounded_rectangle((64,51,341,105), radius=15, fill=LIME)
    d.text((82,65),"LENXIQ  /  HEALTH",font=face(24),fill=(4,13,33))
    d.rounded_rectangle((993,49,1216,103), radius=16,
                        fill=(20,36,70,245),outline=(76,114,187,255),width=2)
    d.text((1016,64),"CHROMOSOME 15",font=face(20),fill=WHITE)
    d.text((65,191),"PRADER–WILLI",font=face(79),fill=WHITE,
           stroke_width=1,stroke_fill=(18,29,53))
    d.text((61,294),"SYNDROME",font=face(111),fill=LIME)
    d.rounded_rectangle((66,458,537,517),radius=17,
                        fill=(18,46,78,245),outline=(56,155,214,255),width=2)
    d.text((88,474),"A RARE GENETIC CONDITION",font=face(25),fill=WHITE)
    d.rectangle((66,605,575,611),fill=(91,165,217,255))
    d.text((66,633),"GENES  •  BRAIN  •  APPETITE",font=face(27),fill=WHITE)
    d.rounded_rectangle((8,8,W-9,H-9),radius=30,outline=(25,55,87,255),width=3)
    result=im.convert("RGB")
    result.save(output,format="JPEG",quality=91,optimize=True,progressive=True)
    if output.stat().st_size >= 1_950_000:
        result.save(output,format="JPEG",quality=79,optimize=True)
    return output
