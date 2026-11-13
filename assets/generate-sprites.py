#!/usr/bin/env python3
"""Draw the cabinet's animated pixel-art GIF pack from code.

Every sprite here is original: drawn on a small grid with Pillow, then
scaled up with nearest-neighbour so it stays crisp on a CRT-sized panel.
Two outputs:

  assets/gifs/<name>_<tag>...gif   tagged sprites for the Pegasus mascot and
                                   sticker slots (tags = console shortnames,
                                   or "all"); indexed by build-gif-index.py
  assets/deco/*.gif                web control-plane decorations

Usage:
    pip install pillow
    python3 assets/generate-sprites.py && python3 assets/build-gif-index.py
"""
import math
import pathlib

from PIL import Image, ImageDraw

HERE = pathlib.Path(__file__).resolve().parent

# The Y2K palette the skins use: cobalt, red, lemon, white, plus support.
COBALT = (31, 79, 255)
RED = (255, 42, 61)
LEMON = (255, 212, 0)
WHITE = (255, 255, 255)
PURPLE = (124, 77, 255)
AQUA = (0, 214, 230)
LIME = (130, 230, 40)
INK = (27, 11, 46)
GREY = (176, 182, 196)
DARK = (92, 98, 116)
ORANGE = (255, 140, 20)
PINK = (255, 105, 180)
TAN = (226, 160, 92)
BROWN = (150, 92, 44)

KEY = (255, 0, 255)  # transparent key colour, never drawn


def frame(w, h):
    img = Image.new("RGB", (w, h), KEY)
    return img, ImageDraw.Draw(img)


PALETTE = [KEY, COBALT, RED, LEMON, WHITE, PURPLE, AQUA, LIME, INK, GREY,
           DARK, ORANGE, PINK, TAN, BROWN, (220, 224, 236), (232, 232, 240),
           (120, 160, 255), (255, 220, 240)]


def _palette_image():
    pal = Image.new("P", (1, 1))
    flat = [c for rgb in PALETTE for c in rgb]
    pal.putpalette(flat + flat[:3] * (256 - len(PALETTE)))
    return pal


def save(frames, path, scale=4, ms=90):
    """Upscale, map onto the fixed palette (index 0 = transparent KEY) and
    write a looping GIF. Drawing only uses palette colours, so the mapping
    is exact and the key never bleeds into a real colour."""
    pal = _palette_image()
    out = []
    for f in frames:
        big = f.resize((f.width * scale, f.height * scale), Image.NEAREST)
        out.append(big.quantize(palette=pal, dither=Image.Dither.NONE))
    path.parent.mkdir(parents=True, exist_ok=True)
    out[0].save(path, save_all=True, append_images=out[1:], duration=ms,
                loop=0, disposal=2, transparency=0, optimize=False)
    print(f"wrote {path.relative_to(HERE.parent)} ({len(frames)} frames)")


def star_points(cx, cy, r_out, r_in, n=5, rot=-math.pi / 2, squash=1.0):
    pts = []
    for i in range(n * 2):
        r = r_out if i % 2 == 0 else r_in
        a = rot + i * math.pi / n
        pts.append((cx + r * math.cos(a) * squash, cy + r * math.sin(a)))
    return pts


def sparkle(d, x, y, size, color=WHITE):
    d.line([(x - size, y), (x + size, y)], fill=color)
    d.line([(x, y - size), (x, y + size)], fill=color)


# ------------------------------------------------------------------ sprites

def star():
    frames = []
    for i in range(12):
        img, d = frame(32, 32)
        squash = abs(math.cos(i * math.pi / 12)) * 0.85 + 0.15
        d.polygon(star_points(16, 17, 13, 6, squash=squash), fill=LEMON, outline=ORANGE)
        if squash > 0.5:
            d.polygon(star_points(14, 14, 4, 2, squash=squash), fill=WHITE)
        for k, (x, y) in enumerate([(4, 5), (27, 8), (26, 27), (5, 26)]):
            if (i + k * 3) % 6 < 3:
                sparkle(d, x, y, 2, AQUA if k % 2 else WHITE)
        frames.append(img)
    save(frames, HERE / "gifs/star_all.gif")


def bubbles():
    frames = []
    seeds = [(6, 0, 4, AQUA), (18, 9, 3, COBALT), (26, 4, 5, PURPLE), (12, 15, 2, AQUA)]
    for i in range(16):
        img, d = frame(32, 40)
        for x, off, r, col in seeds:
            y = 40 + r - ((i * 3 + off * 3) % (40 + 2 * r))
            x2 = x + round(math.sin((i + off) / 2.5) * 2)
            d.ellipse([x2 - r, y - r, x2 + r, y + r], outline=col, fill=None)
            d.point((x2 - r // 2, y - r // 2), fill=WHITE)
        frames.append(img)
    save(frames, HERE / "gifs/bubbles_all.gif")


def heart():
    shape = ["..XX...XX..",
             ".XXXX.XXXX.",
             "XXXXXXXXXXX",
             "XXXXXXXXXXX",
             ".XXXXXXXXX.",
             "..XXXXXXX..",
             "...XXXXX...",
             "....XXX....",
             ".....X....."]
    frames = []
    for i in range(10):
        img, d = frame(32, 32)
        grow = i % 5 in (1, 2)
        cell = 3 if grow else 2
        w, h = len(shape[0]) * cell, len(shape) * cell
        ox, oy = (32 - w) // 2, (32 - h) // 2
        for r, row in enumerate(shape):
            for c, ch in enumerate(row):
                if ch == "X":
                    d.rectangle([ox + c * cell, oy + r * cell,
                                 ox + c * cell + cell - 1, oy + r * cell + cell - 1], fill=RED)
        d.rectangle([ox + cell * 2, oy + cell, ox + cell * 3 - 1, oy + cell * 2 - 1], fill=WHITE)
        if grow:
            sparkle(d, 4, 4, 2, PINK)
            sparkle(d, 28, 6, 2, PINK)
        frames.append(img)
    save(frames, HERE / "gifs/heart_all.gif", ms=110)


def coin():
    frames = []
    for i in range(12):
        img, d = frame(32, 32)
        w = max(1, round(abs(math.cos(i * math.pi / 12)) * 11))
        d.ellipse([16 - w, 5, 16 + w, 27], fill=LEMON, outline=ORANGE)
        if w > 4:
            d.rectangle([15, 10, 17, 22], fill=ORANGE)
            d.point((16 - w // 2, 9), fill=WHITE)
        frames.append(img)
    save(frames, HERE / "gifs/coin_arcade.gif", ms=80)


def joystick():
    frames = []
    for i in range(12):
        img, d = frame(40, 32)
        d.rounded_rectangle([2, 20, 37, 30], radius=3, fill=INK)
        d.rounded_rectangle([3, 21, 36, 28], radius=2, fill=COBALT)
        tilt = round(math.sin(i * math.pi / 3) * 4)
        d.line([(12, 22), (12 + tilt, 9)], fill=GREY, width=2)
        d.ellipse([8 + tilt, 4, 16 + tilt, 12], fill=RED)
        d.point((10 + tilt, 6), fill=WHITE)
        for k, (x, col) in enumerate([(24, LEMON), (31, LIME)]):
            on = (i // 2 + k) % 3 == 0
            d.ellipse([x - 3, 21 if on else 20, x + 3, 26 if on else 25],
                      fill=WHITE if on else col, outline=INK)
        frames.append(img)
    save(frames, HERE / "gifs/joystick_arcade.gif")


def cartridge():
    frames = []
    for i in range(12):
        img, d = frame(32, 36)
        dy = round(math.sin(i * math.pi / 6) * 2)
        d.rectangle([5, 6 + dy, 26, 31 + dy], fill=GREY, outline=DARK)
        d.rectangle([8, 3 + dy, 23, 6 + dy], fill=GREY, outline=DARK)
        d.rectangle([8, 10 + dy, 23, 22 + dy], fill=COBALT)
        d.rectangle([10, 12 + dy, 21, 15 + dy], fill=RED)
        d.rectangle([10, 17 + dy, 17, 18 + dy], fill=WHITE)
        for x in range(8, 24, 3):
            d.line([(x, 26 + dy), (x, 29 + dy)], fill=DARK)
        if i % 4 < 2:
            sparkle(d, 27, 7 + dy, 2, LEMON)
        frames.append(img)
    save(frames, HERE / "gifs/cartridge_nes_snes_gen_gb_gbc_gba_n64.gif")


def disc():
    frames = []
    sheen = [AQUA, LIME, LEMON, PINK, PURPLE, COBALT]
    for i in range(12):
        img, d = frame(32, 32)
        d.ellipse([2, 2, 29, 29], fill=(220, 224, 236), outline=DARK)
        a = i * math.pi / 6
        for k, col in enumerate(sheen):
            ang = a + k * 0.18
            x1, y1 = 16 + 6 * math.cos(ang), 16 + 6 * math.sin(ang)
            x2, y2 = 16 + 13 * math.cos(ang), 16 + 13 * math.sin(ang)
            d.line([(x1, y1), (x2, y2)], fill=col)
        d.ellipse([11, 11, 20, 20], fill=GREY)
        d.ellipse([14, 14, 17, 17], fill=KEY)
        frames.append(img)
    save(frames, HERE / "gifs/disc_ps1_ps2_dc_sat.gif", ms=70)


def controller():
    frames = []
    for i in range(12):
        img, d = frame(40, 28)
        d.rounded_rectangle([3, 7, 36, 22], radius=7, fill=(232, 232, 240), outline=DARK)
        d.rectangle([9, 12, 15, 14], fill=INK)
        d.rectangle([11, 10, 13, 16], fill=INK)
        cols = [RED, LEMON, LIME, COBALT]
        pos = [(29, 10), (32, 13), (29, 16), (26, 13)]
        for k, ((x, y), col) in enumerate(zip(pos, cols)):
            lit = (i // 2) % 4 == k
            d.ellipse([x - 1, y - 1, x + 1, y + 1], fill=WHITE if lit else col)
        d.rectangle([18, 15, 21, 16], fill=DARK)
        frames.append(img)
    save(frames, HERE / "gifs/controller_all.gif", ms=100)


def tabby():
    """The cabinet's mascot cat, as an 8-bit sprite: blinks and wags."""
    frames = []
    for i in range(16):
        img, d = frame(36, 32)
        d.ellipse([9, 15, 29, 29], fill=TAN, outline=BROWN)            # body
        for x in (14, 19, 24):
            d.line([(x, 17), (x - 1, 22)], fill=BROWN)                  # stripes
        wag = round(math.sin(i * math.pi / 4) * 3)
        d.line([(28, 24), (33, 18 + wag)], fill=BROWN, width=2)        # tail
        d.ellipse([3, 6, 19, 20], fill=TAN, outline=BROWN)             # head
        d.polygon([(4, 9), (6, 2), (10, 7)], fill=TAN, outline=BROWN)  # ears
        d.polygon([(12, 7), (17, 2), (18, 9)], fill=TAN, outline=BROWN)
        d.line([(9, 7), (11, 9)], fill=BROWN)
        if i in (6, 7):
            d.line([(6, 13), (8, 13)], fill=INK)
            d.line([(13, 13), (15, 13)], fill=INK)
        else:
            d.rectangle([6, 12, 8, 14], fill=LIME)
            d.rectangle([13, 12, 15, 14], fill=LIME)
            d.point((7, 13), fill=INK)
            d.point((14, 13), fill=INK)
        d.point((10, 16), fill=PINK)
        frames.append(img)
    save(frames, HERE / "gifs/tabby_all.gif", ms=110)


def rocket():
    frames = []
    for i in range(8):
        img, d = frame(64, 24)
        for k in range(6):
            x = 4 + k * 5 - (i % 2)
            d.rectangle([x, 9, x + 3, 14], fill=[RED, ORANGE, LEMON, LIME, AQUA, PURPLE][k])
        d.polygon([(34, 6), (52, 6), (60, 12), (52, 18), (34, 18)], fill=WHITE, outline=DARK)
        d.ellipse([44, 9, 49, 14], fill=COBALT, outline=DARK)
        d.polygon([(34, 6), (30, 2), (38, 6)], fill=RED)
        d.polygon([(34, 18), (30, 22), (38, 18)], fill=RED)
        if i % 2:
            sparkle(d, 2, 4, 1, LEMON)
            sparkle(d, 20, 20, 1, WHITE)
        else:
            sparkle(d, 12, 3, 1, WHITE)
            sparkle(d, 26, 21, 1, LEMON)
        frames.append(img)
    save(frames, HERE / "gifs/rocket_all.gif", ms=80)


# ------------------------------------------------------------------ deco

def flame():
    frames = []
    for i in range(8):
        img, d = frame(24, 24)
        for layer, (col, w, h) in enumerate([(RED, 9, 18), (ORANGE, 6, 13), (LEMON, 3, 8)]):
            j = round(math.sin(i * math.pi / 4 + layer) * 1.5)
            d.polygon([(12 - w, 22), (12 + j, 22 - h), (12 + w, 22)], fill=col)
            d.ellipse([12 - w, 22 - h // 2, 12 + w, 23], fill=col)
        if i % 3 == 0:
            d.point((7 + i % 5, 4), fill=LEMON)
        frames.append(img)
    save(frames, HERE / "deco/flame.gif", scale=3, ms=90)


def glitter():
    frames = []
    spots = [(5, 6), (20, 4), (33, 10), (10, 20), (27, 23), (40, 30),
             (4, 36), (18, 40), (36, 44), (24, 32), (12, 50), (38, 54)]
    cols = [WHITE, LEMON, PINK, AQUA]
    for i in range(10):
        img, d = frame(48, 60)
        d.rectangle([0, 0, 47, 59], fill=(255, 220, 240))
        for k, (x, y) in enumerate(spots):
            phase = (i + k * 3) % 5
            if phase < 3:
                sparkle(d, x, y, phase + 1, cols[k % 4])
        d.polygon(star_points(24, 30, 9, 4), fill=PINK, outline=RED)
        frames.append(img)
    save(frames, HERE / "deco/glitter.gif", scale=3, ms=100)


def badge():
    frames = []
    for i in range(8):
        img, d = frame(56, 20)
        d.rounded_rectangle([1, 1, 54, 18], radius=6, fill=COBALT, outline=INK)
        d.rounded_rectangle([3, 3, 52, 9], radius=3, fill=(120, 160, 255))
        # pixel "P1" glyphs
        for x, y in [(0, 0), (0, 1), (0, 2), (0, 3), (0, 4), (1, 0), (2, 0), (2, 1), (1, 2), (2, 2)]:
            d.rectangle([20 + x * 2, 5 + y * 2, 21 + x * 2, 6 + y * 2], fill=LEMON if i % 4 < 2 else WHITE)
        for x, y in [(1, 0), (0, 1), (1, 1), (1, 2), (1, 3), (0, 4), (1, 4), (2, 4)]:
            d.rectangle([29 + x * 2, 5 + y * 2, 30 + x * 2, 6 + y * 2], fill=LEMON if i % 4 < 2 else WHITE)
        sparkle(d, 8 + (i % 4) * 11, 4, 1, WHITE)
        frames.append(img)
    save(frames, HERE / "deco/badge.gif", scale=3, ms=120)


if __name__ == "__main__":
    for draw in (star, bubbles, heart, coin, joystick, cartridge, disc,
                 controller, tabby, rocket, flame, glitter, badge):
        draw()
