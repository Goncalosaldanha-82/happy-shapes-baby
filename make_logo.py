"""Channel logo for Happy Shapes Baby: three of the show's characters on a bright round badge.

    python make_logo.py   -> branding/logo.png (800x800, YouTube crops it to a circle)
"""
import math
from pathlib import Path

import cairo

from make_video import TAU, character, hexrgb

ROOT = Path(__file__).resolve().parent
S = 800


def main():
    out = ROOT / "branding"
    out.mkdir(exist_ok=True)
    surf = cairo.ImageSurface(cairo.FORMAT_ARGB32, S, S)
    ctx = cairo.Context(surf)
    c = S / 2
    g = cairo.RadialGradient(c, c * 0.8, 0, c, c, S * 0.62)
    g.add_color_stop_rgb(0, *hexrgb("#6a5cff"))
    g.add_color_stop_rgb(1, *hexrgb("#2a1580"))
    ctx.set_source(g)
    ctx.paint()
    # soft rainbow arc behind the friends (kept inside the circle crop)
    for k, col in enumerate(["#ff3b30", "#ff9f0a", "#ffd60a", "#34c759", "#0a84ff"]):
        ctx.set_source_rgba(*hexrgb(col), 0.9)
        ctx.set_line_width(26)
        ctx.arc(c, c + 140, 300 - k * 26, math.pi * 1.08, math.pi * 1.92)
        ctx.stroke()
    # twinkle dots
    for x, y, r in ((170, 250, 9), (630, 230, 11), (600, 600, 8), (200, 590, 7), (400, 120, 8)):
        ctx.set_source_rgba(1, 1, 1, 0.75)
        ctx.arc(x, y, r, 0, TAU)
        ctx.fill()
    character(ctx, "star", 205, 500, 105, "#ffd60a", 1.0, 0, -0.18, look=(0.4, 0))
    character(ctx, "heart", 595, 500, 105, "#ff6ac1", 1.0, 1, 0.15, look=(-0.4, 0))
    character(ctx, "circle", 400, 400, 125, "#34c759", 1.0, 2, 0, look=(0, 0.3))
    surf.write_to_png(str(out / "logo.png"))
    # preview as YouTube shows it: circle, also tiny
    prev = cairo.ImageSurface(cairo.FORMAT_ARGB32, 420, 260)
    p = cairo.Context(prev)
    p.set_source_rgb(1, 1, 1)
    p.paint()
    for x, size in ((20, 220), (270, 88), (380, 32)):
        p.save()
        p.translate(x, 20)
        p.arc(size / 2, size / 2, size / 2, 0, TAU)
        p.clip()
        p.scale(size / S, size / S)
        p.set_source_surface(surf, 0, 0)
        p.paint()
        p.restore()
    prev.write_to_png(str(out / "logo_preview.png"))
    print("->", out / "logo.png")


if __name__ == "__main__":
    main()
