"""Channel banner for Happy Shapes Baby (2560x1440, YouTube's recommended size).

Everything important sits inside the 1546x423 centre area that phones show; the dancing friends
spread out to the edges that only desktop and TV show.

    python make_banner.py   -> branding/banner.png + branding/banner_preview.png (safe areas marked)
"""
import math
import random
from pathlib import Path

import cairo

from make_video import PALETTE, SHAPES, TAU, character, hexrgb

ROOT = Path(__file__).resolve().parent
W, H = 2560, 1440
SAFE = (507, 508, 1546, 423)          # x, y, w, h visible on every device
FONT = "Segoe UI Black"


def text(ctx, s, x, y, size, fill, outline=None, ow=0):
    ctx.select_font_face(FONT, cairo.FONT_SLANT_NORMAL, cairo.FONT_WEIGHT_BOLD)
    ctx.set_font_size(size)
    e = ctx.text_extents(s)
    ctx.move_to(x - e.width / 2 - e.x_bearing, y - e.height / 2 - e.y_bearing)
    ctx.text_path(s)
    if outline:
        ctx.set_source_rgb(*hexrgb(outline))
        ctx.set_line_width(ow)
        ctx.set_line_join(cairo.LINE_JOIN_ROUND)
        ctx.stroke_preserve()
    ctx.set_source_rgb(*hexrgb(fill))
    ctx.fill()


def main():
    out = ROOT / "branding"
    out.mkdir(exist_ok=True)
    r = random.Random(11)
    surf = cairo.ImageSurface(cairo.FORMAT_ARGB32, W, H)
    ctx = cairo.Context(surf)
    g = cairo.RadialGradient(W / 2, H / 2, 0, W / 2, H / 2, W * 0.6)
    g.add_color_stop_rgb(0, *hexrgb("#6a5cff"))
    g.add_color_stop_rgb(1, *hexrgb("#24106e"))
    ctx.set_source(g)
    ctx.paint()
    for _ in range(140):
        ctx.set_source_rgba(*hexrgb(r.choice(PALETTE)), r.uniform(0.35, 0.7))
        ctx.arc(r.random() * W, r.random() * H, r.uniform(4, 13), 0, TAU)
        ctx.fill()
    # big rainbow behind the title
    cx, cy = W / 2, H / 2 + 330
    for k, col in enumerate(["#ff3b30", "#ff9f0a", "#ffd60a", "#34c759", "#0a84ff", "#bf5af2"]):
        ctx.set_source_rgba(*hexrgb(col), 0.85)
        ctx.set_line_width(34)
        ctx.arc(cx, cy, 640 - k * 34, math.pi * 1.04, math.pi * 1.96)
        ctx.stroke()
    # friends: two inside the safe area (next to the title), more out to the sides for desktop/TV
    sx, sy, sw, sh = SAFE
    mid = sy + sh / 2
    # desktop band (full width, y 508-931): friends left and right of the phone-safe centre
    left = [("star", "#ffd60a", 400, -0.15), ("circle", "#34c759", 175, 0.1)]
    right = [("heart", "#ff6ac1", W - 400, 0.15), ("square", "#5ac8fa", W - 175, -0.1)]
    for i, (kind, col, x, rot) in enumerate(left + right):
        character(ctx, kind, x, mid + (25 if i % 2 else -15), 105 if i % 2 == 0 else 85, col, 1.0, i, rot,
                  look=((0.5 if x < W / 2 else -0.5), 0))
    # TV-only corners
    for i, (kind, col, x, y) in enumerate([("triangle", "#ff3b30", 260, 260), ("hexagon", "#ff9f0a", W - 260, 260),
                                           ("star", "#bf5af2", 330, H - 230), ("circle", "#0a84ff", W - 330, H - 230)]):
        character(ctx, kind, x, y, 80, col, 1.0, i + 4, 0.15 * (1 if i % 2 else -1))
    text(ctx, "Happy Shapes Baby", W / 2, mid - 50, 140, "#ffffff", "#24106e", 26)
    text(ctx, "A new sensory video every day", W / 2, mid + 105, 60, "#ffe28a", "#24106e", 14)
    surf.write_to_png(str(out / "banner.png"))

    # preview with the device areas outlined (not for upload)
    prev = cairo.ImageSurface(cairo.FORMAT_ARGB32, W // 4, H // 4)
    p = cairo.Context(prev)
    p.scale(0.25, 0.25)
    p.set_source_surface(surf, 0, 0)
    p.paint()
    p.set_line_width(8)
    for (x, y, w, h), col in (((0, 0, W, H), (1, 1, 1)), ((0, 508, W, 423), (0, 1, 1)), (SAFE, (1, 0, 0))):
        p.set_source_rgb(*col)
        p.rectangle(x, y, w, h)
        p.stroke()
    prev.write_to_png(str(out / "banner_preview.png"))
    print("->", out / "banner.png")


if __name__ == "__main__":
    main()
