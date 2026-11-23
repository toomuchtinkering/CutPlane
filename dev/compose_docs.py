#!/usr/bin/env python3
# SPDX-License-Identifier: LGPL-2.1-or-later
"""README pictures in docs/images from the raw shots of dev/make_docs.py.

Crops, labels, the grid of placing examples and the two animations. Runs on the
host (Pillow and ffmpeg), not in FreeCAD. dev/make_docs.sh runs both steps.
"""
import json
import os
import shutil
import subprocess
import tempfile

import numpy as np
from PIL import Image, ImageDraw, ImageFont

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
RAW = os.path.join(ROOT, "dev", "out", "docs_raw")
OUT = os.path.join(ROOT, "docs", "images")
FONT = "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf"
BOLD = "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf"
meta = json.load(open(os.path.join(RAW, "meta.json")))


def load(name):
    return Image.open(os.path.join(RAW, name)).convert("RGB")


def save(im, name):
    im.save(os.path.join(OUT, name), optimize=True)
    print("  %-22s %4d x %-4d %4d kB" % (name, im.width, im.height, os.path.getsize(os.path.join(OUT, name)) // 1024))


def background(im):
    return tuple(int(v) for v in np.asarray(im)[4, 4])


def content_box(im, pad=24, tol=18):
    """Bounding box of everything that differs from the background, padded."""
    a = np.asarray(im).astype(int)
    diff = np.abs(a - np.array(background(im))).max(axis=2) > tol
    ys, xs = np.nonzero(diff)
    return [max(0, xs.min() - pad), max(0, ys.min() - pad),
            min(im.width, xs.max() + pad + 1), min(im.height, ys.max() + pad + 1)]


def fit(im, maxw, maxh=None):
    s = min(maxw / im.width, (maxh or 10 ** 6) / im.height, 1.0)
    return im if s >= 1.0 else im.resize((round(im.width * s), round(im.height * s)), Image.LANCZOS)


def font(size, bold=False):
    return ImageFont.truetype(BOLD if bold else FONT, size)


def side_by_side(left, rights, bg, gap=16):
    """The view on the left, panel pieces stacked on the right (scaled to fit its height)."""
    col_w = max(r.width for r in rights)
    col_h = sum(r.height for r in rights) + gap * (len(rights) - 1)
    s = min(1.0, (left.height - 2 * gap) / col_h)
    rights = [r.resize((round(r.width * s), round(r.height * s)), Image.LANCZOS) if s < 1 else r for r in rights]
    col_w = max(r.width for r in rights)
    im = Image.new("RGB", (left.width + col_w + 2 * gap, left.height), bg)
    im.paste(left, (0, 0))
    y = gap
    for r in rights:
        im.paste(r, (left.width + gap, y))
        y += r.height + gap
    return im


# --------------------------------------------------------------------------------------
# the mouse pointer for the animations
# --------------------------------------------------------------------------------------
ARROW = [(0, 0), (0, 21), (5, 16), (9, 25), (13, 23), (9, 15), (16, 15)]


def draw_cursor(im, x, y, pressed, scale=1.0):
    d = ImageDraw.Draw(im, "RGBA")
    if pressed:
        r = 13 * scale
        d.ellipse((x - r, y - r, x + r, y + r), fill=(255, 220, 40, 110), outline=(255, 220, 40, 230), width=2)
    pts = [(x + px * scale, y + py * scale) for px, py in ARROW]
    d.polygon(pts, fill=(255, 255, 255, 255), outline=(0, 0, 0, 255))
    d.line(pts + [pts[0]], fill=(0, 0, 0, 255), width=max(1, round(1.2 * scale)))


def animation(name, crop, width, fps, out_name, dither="bayer:bayer_scale=3", every=1):
    frames = meta[name]["frames"]
    tmp = tempfile.mkdtemp(prefix="cutplane_gif_")
    try:
        kept = 0
        for k, (cx, cy, pressed) in enumerate(frames):
            if k % every and k != len(frames) - 1:
                continue
            im = load(os.path.join(name, "%03d.png" % k)).crop(crop)
            draw_cursor(im, cx - crop[0], cy - crop[1], pressed, scale=1.25)
            im.save(os.path.join(tmp, "%03d.png" % kept))
            kept += 1
        vf = ("scale=%d:-1:flags=lanczos,split[a][b];[a]palettegen=max_colors=160:stats_mode=full[p];"
              "[b][p]paletteuse=dither=%s:diff_mode=rectangle" % (width, dither))
        subprocess.run(["ffmpeg", "-v", "error", "-y", "-framerate", str(fps), "-i", os.path.join(tmp, "%03d.png"),
                        "-vf", vf, "-loop", "0", os.path.join(OUT, out_name)], check=True)
        print("  %-22s %4d px wide, %d frames, %4d kB" % (out_name, width, kept,
                                                         os.path.getsize(os.path.join(OUT, out_name)) // 1024))
    finally:
        shutil.rmtree(tmp)


# --------------------------------------------------------------------------------------
# the labelled handle
# --------------------------------------------------------------------------------------
def labelled_handle(raw_name, out_name, labels, cursor=None, points="handle"):
    im = load(raw_name)
    P = meta[points]["points"]
    xs = [v[0] for v in P.values()]
    ys = [v[1] for v in P.values()]
    crop = [int(min(xs) - 400), int(min(ys) - 150), int(max(xs) + 400), int(max(ys) + 150)]
    im = im.crop(crop)
    if cursor is not None:
        draw_cursor(im, cursor[0] - crop[0], cursor[1] - crop[1], False, scale=1.25)
    d = ImageDraw.Draw(im, "RGBA")
    f_title, f_text = font(16, True), font(14)
    sides = {"left": [], "right": []}
    for key, side, title, text in labels:
        ax, ay = P[key][0] - crop[0], P[key][1] - crop[1]
        sides[side].append([ay, ax, title, text])
    for side, items in sides.items():
        items.sort()
        boxes = []
        for ay, ax, title, text in items:
            lines = [(title, f_title)] + [(t, f_text) for t in text.split("\n")]
            w = max(d.textlength(t, font=f) for t, f in lines) + 20
            h = sum(f.size + 6 for _, f in lines) + 10
            boxes.append([ay - h / 2, w, h, ax, ay, lines])
        for k in range(1, len(boxes)):                       # keep the boxes apart
            prev = boxes[k - 1]
            boxes[k][0] = max(boxes[k][0], prev[0] + prev[2] + 10)
        for y, w, h, ax, ay, lines in boxes:
            y = min(max(y, 8), im.height - h - 8)
            x = 14 if side == "left" else im.width - 14 - w
            ex = x + w if side == "left" else x
            d.line((ex, y + h / 2, ax, ay), fill=(255, 255, 255, 200), width=2)
            d.ellipse((ax - 4, ay - 4, ax + 4, ay + 4), fill=(255, 255, 255, 230))
            d.rounded_rectangle((x, y, x + w, y + h), 6, fill=(20, 20, 24, 225), outline=(255, 255, 255, 120))
            ty = y + 7
            for t, f in lines:
                d.text((x + 10, ty), t, font=f, fill=(255, 255, 255, 255) if f is f_title else (210, 210, 215, 255))
                ty += f.size + 6
    save(im, out_name)


def main():
    os.makedirs(OUT, exist_ok=True)
    hero = load("hero.png")
    bg = background(hero)

    hero_box = content_box(hero)
    save(fit(hero.crop(hero_box), 1200), "hero.png")

    win = load("window.png")
    save(fit(win, 1600), "window.png")
    panel = load("panel.png")
    save(panel.crop(content_box(panel, pad=10)), "panel.png")
    if os.path.exists(os.path.join(RAW, "prefs.png")):
        prefs_im = load("prefs.png")
        save(prefs_im.crop((0, 0, prefs_im.width, min(prefs_im.height, 500))), "preferences.png")

    labelled_handle("handle.png", "handle.png", [
        ("head", "left", "Grey ball", "drag: move the plane along its normal\nclick: flip which side is removed"),
        ("red+1", "left", "Red arrows", "drag: tilt the plane about one axis"),
        ("green+1", "left", "Green arrows", "drag: tilt it about the other axis"),
        ("stem", "right", "Yellow stem", "points to the side that is removed"),
        ("base", "right", "Small white ball", "drag: move the handle on the plane;\n"
                                              "snaps to vertices, edge midpoints\nand circle centres"),
    ])
    labelled_handle("handle_dial.png", "handle_dial.png", [
        ("cursor", "left", "Over the red arrows", "the green pair hides, the dial appears"),
        ("dial", "right", "Drag along the dial's scale", "5° steps; inside its inner ring 45°;\n"
                                                         "elsewhere 1°, Shift 15°, Ctrl free"),
    ], cursor=meta["handle_dial"]["cursor"], points="handle_dial")

    # tilt: a square around the plane origin
    bx, by = meta["handle"]["points"]["base"]
    R = 330
    animation("tilt", [int(bx - R), int(by - R * 0.8), int(bx + R), int(by + R * 1.2)], 560, 12, "tilt.gif",
              dither="none")
    animation("move", hero_box, 660, 8, "move.gif", dither="none", every=2)

    # placing examples, two by two
    cells = [("place_face.png", "Flat face: the plane lies on it (here dragged 20 mm in)"),
             ("place_cylinder.png", "Cylinder: through its axis, facing you"),
             ("place_circle.png", "Circle edge: through its axis (the oil hole)"),
             ("place_sketch.png", "Sketch or datum plane: that plane")]
    cw, ch, cap, gap = 620, 430, 34, 12
    grid = Image.new("RGB", (2 * cw + 3 * gap, 2 * (ch + cap) + 3 * gap), bg)
    d = ImageDraw.Draw(grid)
    for k, (name, caption) in enumerate(cells):
        im = load(name)
        im = fit(im.crop(content_box(im)), cw, ch)
        x0 = gap + (k % 2) * (cw + gap)
        y0 = gap + (k // 2) * (ch + cap + gap)
        d.text((x0 + 6, y0 + 7), caption, font=font(16, True), fill=(235, 235, 235))
        grid.paste(im, (x0 + (cw - im.width) // 2, y0 + cap + (ch - im.height) // 2))
    save(grid, "placing.png")

    two = load("two_planes.png")
    save(fit(two.crop(content_box(two)), 1000), "two_planes.png")

    keep = load("keep_whole.png")
    save(side_by_side(fit(keep.crop(content_box(keep)), 900), [load("panel_parts.png")], bg), "keep_whole.png")

    ov = load("overlap.png")
    a = np.asarray(ov).astype(int)
    red = (a[..., 0] > 200) & (a[..., 1] < 70) & (a[..., 2] < 70)
    ys, xs = np.nonzero(red)
    box = [max(0, xs.min() - 300), max(0, ys.min() - 170), min(ov.width, xs.max() + 260), min(ov.height, ys.max() + 170)]
    save(side_by_side(fit(ov.crop(box), 800), [load("panel_display.png"), load("panel_status.png")], bg),
         "overlap.png")


if __name__ == "__main__":
    main()
