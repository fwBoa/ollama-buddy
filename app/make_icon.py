#!/usr/bin/env python3
"""Genere AppIcon.icns pour Ollama Buddy (style macOS : coins arrondis, marge).

Trois barres de longueurs decroissantes, aux teintes de la palette du
tableau de bord - la meme lecture que la vue « Repartition par modele ».
"""

import shutil
import subprocess
import sys
from pathlib import Path

from PIL import Image, ImageDraw, ImageFilter

SIZE = 1024
# Style macOS : l'icone n'occupe pas tout le carre, elle flotte avec une marge.
MARGIN = 100
RADIUS = 230

BG_TOP = (42, 42, 40)
BG_BOTTOM = (18, 18, 17)
RING = (255, 255, 255, 26)
# Piste des barres : une couleur OPAQUE, un ton au-dessus du fond. La dessiner
# en blanc translucide percerait le fond au lieu de l'eclaircir.
TRACK = (58, 58, 56)

# Bleu, aqua, orange - slots 1, 3, 2 de la palette validee.
BARS = [
    (0.94, (57, 135, 229)),
    (0.62, (25, 158, 112)),
    (0.34, (217, 89, 38)),
]
BAR_H = 86
GAP = 60


def rounded_mask(size: int, radius: int) -> Image.Image:
    mask = Image.new("L", (size, size), 0)
    ImageDraw.Draw(mask).rounded_rectangle((0, 0, size - 1, size - 1), radius, fill=255)
    return mask


def build() -> Image.Image:
    canvas = Image.new("RGBA", (SIZE, SIZE), (0, 0, 0, 0))

    # Fond : degrade vertical doux.
    inner = SIZE - MARGIN * 2
    plate = Image.new("RGBA", (inner, inner))
    draw = ImageDraw.Draw(plate)
    for y in range(inner):
        t = y / (inner - 1)
        draw.line(
            [(0, y), (inner, y)],
            fill=tuple(round(BG_TOP[i] + (BG_BOTTOM[i] - BG_TOP[i]) * t) for i in range(3)) + (255,),
        )

    mask = rounded_mask(inner, RADIUS)
    plate.putalpha(mask)

    # Ombre portee : les icones macOS flottent, elles ne sont pas posees a plat.
    shadow = Image.new("RGBA", (SIZE, SIZE), (0, 0, 0, 0))
    shadow.paste((0, 0, 0, 90), (MARGIN, MARGIN + 14), mask)
    shadow = shadow.filter(ImageFilter.GaussianBlur(22))
    canvas = Image.alpha_composite(canvas, shadow)

    canvas.paste(plate, (MARGIN, MARGIN), plate)

    # Anneau interieur : le lisere clair des icones macOS.
    ring = Image.new("RGBA", (SIZE, SIZE), (0, 0, 0, 0))
    ImageDraw.Draw(ring).rounded_rectangle(
        (MARGIN, MARGIN, SIZE - MARGIN - 1, SIZE - MARGIN - 1),
        RADIUS, outline=RING, width=3,
    )
    canvas = Image.alpha_composite(canvas, ring)

    # Barres : longueurs decroissantes, comme une repartition.
    draw = ImageDraw.Draw(canvas)
    track_left = MARGIN + 118
    track_w = inner - 236
    total_h = len(BARS) * BAR_H + (len(BARS) - 1) * GAP
    top = MARGIN + (inner - total_h) // 2

    for i, (share, color) in enumerate(BARS):
        y = top + i * (BAR_H + GAP)
        draw.rounded_rectangle(
            (track_left, y, track_left + track_w, y + BAR_H),
            BAR_H // 2, fill=TRACK + (255,),
        )
        draw.rounded_rectangle(
            (track_left, y, track_left + round(track_w * share), y + BAR_H),
            BAR_H // 2, fill=color + (255,),
        )

    return canvas


ICONSET = [
    ("icon_16x16.png", 16), ("icon_16x16@2x.png", 32),
    ("icon_32x32.png", 32), ("icon_32x32@2x.png", 64),
    ("icon_128x128.png", 128), ("icon_128x128@2x.png", 256),
    ("icon_256x256.png", 256), ("icon_256x256@2x.png", 512),
    ("icon_512x512.png", 512), ("icon_512x512@2x.png", 1024),
]


def main() -> int:
    out = Path(sys.argv[1] if len(sys.argv) > 1 else "AppIcon.icns").resolve()
    iconset = out.parent / "AppIcon.iconset"
    if iconset.exists():
        shutil.rmtree(iconset)
    iconset.mkdir(parents=True)

    master = build()
    for name, px in ICONSET:
        master.resize((px, px), Image.LANCZOS).save(iconset / name)

    subprocess.run(["iconutil", "-c", "icns", str(iconset), "-o", str(out)], check=True)
    shutil.rmtree(iconset)
    print(f"icone -> {out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
