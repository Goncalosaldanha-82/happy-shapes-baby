"""Baby Sensory - one original sensory video per day for babies and toddlers (0-3).

Each run = one episode:
  * a long video (1920x1080, ~20 min) of friendly high-contrast shapes that dance to calm music,
  * a vertical Short (1080x1920, ~40 s) cut from the same episode,
  * a thumbnail (1280x720) and metadata.json.
The theme rotates every day (dance party, bubbles, black & white, parade, kaleidoscope, orbit, night).

Everything is generated from code: shapes drawn with cairo, music synthesised with numpy
(sine-based music box / marimba / kalimba / pad). No AI, no samples, no third-party assets, no text,
so it works for any language.

Baby-safe by design: no flashing or strobing, no full-screen brightness jumps (scene changes fade
through black over 0.6 s), slow movement, soft volume, no sudden loud sounds.

Usage:
    python make_video.py                 # next episode
    python make_video.py --if-needed     # only if nothing is waiting and nothing was published today
    python make_video.py --episode 3     # (re)render episode 3
    python make_video.py --preview       # quick low-res 1 minute test
    python make_video.py --minutes 30    # longer episode
"""
import argparse
import datetime
import json
import math
import random
import shutil
import subprocess
import sys
import wave
from pathlib import Path

import cairo
import imageio_ffmpeg
import numpy as np

for _s in (sys.stdout, sys.stderr):
    try:
        _s.reconfigure(encoding="utf-8")
    except AttributeError:
        pass

ROOT = Path(__file__).resolve().parent
OUT = ROOT / "output"
FPS = 30
SR = 44100
TAU = 2 * math.pi
FADE = 0.6          # seconds of fade through black between scenes
MIN_FREE_GB = 1.5   # one episode needs ~0.4 GB (wav + video) while rendering; keep a margin for Windows

PALETTE = ["#ff3b30", "#ffd60a", "#34c759", "#0a84ff", "#bf5af2", "#ff9f0a", "#ff6ac1", "#5ac8fa"]
CONTRAST = ["#ffffff", "#ff2d2d", "#ffd60a"]
SHAPES = ["circle", "square", "triangle", "star", "heart", "hexagon"]

# (key, title, short title, emoji, bpm range, scene mix)
THEMES = [
    ("dance", "Happy Shapes Dance Party", "Dancing Shapes", "🎉", (100, 110), ["dance", "orbit", "bubbles", "kaleido"]),
    ("bubbles", "Bubble Party", "Happy Bubbles", "🫧", (96, 106), ["bubbles", "dance", "orbit", "parade"]),
    ("contrast", "Black & White High Contrast", "High Contrast", "⚫⚪", (86, 94), ["contrast", "contrast", "orbit", "dance"]),
    ("parade", "Shape Parade", "Shape Parade", "🚂", (100, 110), ["parade", "dance", "bubbles", "kaleido"]),
    ("kaleido", "Rainbow Kaleidoscope", "Rainbow Kaleidoscope", "🌈", (94, 104), ["kaleido", "orbit", "dance", "bubbles"]),
    ("orbit", "Spinning Friends", "Spinning Friends", "💫", (98, 108), ["orbit", "kaleido", "dance", "parade"]),
    ("night", "Starry Night Calm Lullaby", "Sleepy Stars", "🌙", (72, 80), ["night", "night", "bubbles", "orbit"]),
]


def hexrgb(h):
    h = h.lstrip("#")
    return tuple(int(h[i:i + 2], 16) / 255 for i in (0, 2, 4))


def ease(x):
    x = min(1.0, max(0.0, x))
    return x * x * (3 - 2 * x)


# ----------------------------------------------------------------------------- music

SCALE = [0, 2, 4, 5, 7, 9, 11]
PROGRESSIONS = [[0, 4, 5, 3], [0, 3, 4, 0], [0, 5, 3, 4], [0, 3, 0, 4], [5, 3, 0, 4], [0, 4, 3, 4]]
RHYTHMS = [
    [1, 0, 1, 0, 1, 0, 1, 0, 1, 0, 0, 0, 1, 0, 0, 0],
    [1, 0, 0, 1, 0, 0, 1, 0, 1, 0, 1, 0, 1, 0, 0, 0],
    [1, 0, 1, 1, 1, 0, 0, 0, 1, 0, 1, 1, 1, 0, 0, 0],
    [1, 0, 0, 0, 1, 0, 1, 0, 1, 0, 0, 0, 0, 0, 0, 0],
    [1, 1, 1, 0, 1, 0, 0, 0, 1, 1, 1, 0, 1, 0, 0, 0],
]


def hz(m):
    return 440.0 * 2 ** ((m - 69) / 12)


def deg_midi(d, base):
    return base + 12 * (d // 7) + SCALE[d % 7]


def tone(f, dur, partials, decay, attack=0.004, vib=0.0):
    n = int(dur * SR)
    t = np.arange(n, dtype=np.float32) / SR
    ph = TAU * f * t
    if vib:
        ph = ph + vib * np.sin(TAU * 5.0 * t)
    x = np.zeros(n, np.float32)
    for ratio, amp, dk in partials:
        x += amp * np.sin(ph * ratio) * np.exp(-t / (decay * dk))
    a = max(1, int(attack * SR))
    x[:a] *= np.linspace(0, 1, a, dtype=np.float32)
    x[-256:] *= np.linspace(1, 0, 256, dtype=np.float32)
    return x


INSTR = {
    "musicbox": lambda m: tone(hz(m), 1.8, [(1, 1, 1), (2, 0.3, 0.6), (4, 0.14, 0.3), (6, 0.05, 0.2)], 0.9),
    "marimba": lambda m: tone(hz(m), 1.0, [(1, 1, 1), (4, 0.3, 0.15), (10, 0.05, 0.05)], 0.4),
    "kalimba": lambda m: tone(hz(m), 1.4, [(1, 1, 1), (2, 0.12, 0.5), (5.4, 0.2, 0.12)], 0.6),
    "whistle": lambda m: tone(hz(m), 0.55, [(1, 1, 3), (2, 0.08, 2)], 0.6, attack=0.04, vib=0.012),
}


def pad(midis, dur):
    n = int(dur * SR)
    t = np.arange(n, dtype=np.float32) / SR
    x = np.zeros(n, np.float32)
    for m in midis:
        f = hz(m)
        x += np.sin(TAU * f * 0.997 * t) + np.sin(TAU * f * 1.003 * t) + 0.25 * np.sin(TAU * 2 * f * t)
    env = np.minimum(1, t / 0.5) * np.minimum(1, (dur - t) / 0.5).clip(0, 1)
    return x * env / len(midis)


def softkick():
    n = int(0.35 * SR)
    t = np.arange(n, dtype=np.float32) / SR
    f = 50 + 70 * np.exp(-t / 0.04)
    return np.sin(TAU * np.cumsum(f) / SR) * np.exp(-t / 0.12)


def shaker(rng):
    n = int(0.09 * SR)
    x = rng.standard_normal(n).astype(np.float32)
    x = np.diff(x, prepend=0) * 0.5
    return x * np.exp(-np.arange(n, dtype=np.float32) / SR / 0.025)


class Song:
    """One episode's music: fixed key and tempo, a new progression and melody every scene."""

    def __init__(self, seed, bpm, calm):
        self.rng = np.random.default_rng(seed)
        self.r = random.Random(seed)
        self.bpm = bpm
        self.calm = calm
        self.root = 60 + self.r.choice([0, 2, 5, 7, -3])      # C, D, F, G, A major
        self.beat = 60.0 / bpm
        self.bars = 16
        self.scene_len = self.bars * 4 * self.beat
        self.kick = softkick()

    def motif(self):
        rh = self.r.choice(RHYTHMS)
        notes, cur = [], self.r.choice([0, 2, 4])
        for i, on in enumerate(rh):
            if not on:
                notes.append(None)
                continue
            if i % 4 == 0:
                cur = self.r.choice([0, 2, 4, 7])                # chord tones on strong slots
            else:
                cur = max(-1, min(9, cur + self.r.choice([-2, -1, 1, 2])))
            notes.append(cur)
        return notes

    def scene(self, idx):
        """Mono float32 audio for scene idx (+2 s tail that rings into the next scene)."""
        prog = self.r.choice(PROGRESSIONS)
        lead = ["musicbox", "kalimba", "marimba", "whistle"][idx % 4] if not self.calm else \
            ["musicbox", "kalimba"][idx % 2]
        m1, m2 = self.motif(), self.motif()
        n = int((self.scene_len + 2.0) * SR)
        x = np.zeros(n, np.float32)
        e8 = self.beat / 2

        def put(sig, t0, g):
            i = int(t0 * SR)
            j = min(n, i + len(sig))
            if i < n:
                x[i:j] += g * sig[:j - i]

        for bar in range(self.bars):
            t_bar = bar * 4 * self.beat
            croot = prog[bar % 4]
            chord = [deg_midi(croot + k, self.root - 12) for k in (0, 2, 4)]
            put(pad(chord, 4 * self.beat + 0.4), t_bar, 0.10)
            bass = deg_midi(croot, self.root - 24)
            for b in (0, 2):
                put(tone(hz(bass), 2 * self.beat, [(1, 1, 1), (2, 0.3, 0.5)], 0.7, attack=0.01), t_bar + b * self.beat, 0.30)
            # gentle arpeggio all the time (quiet), the lead from bar 4
            for k in range(8):
                arp = deg_midi(croot + [0, 2, 4, 7][k % 4], self.root)
                put(INSTR["marimba"](arp), t_bar + k * e8, 0.07 if self.calm else 0.09)
            if bar >= 4:
                mot = (m1 if (bar // 2) % 2 == 0 else m2)[(bar % 2) * 8:(bar % 2) * 8 + 8]
                up = 12 if bar >= 12 and not self.calm else 0
                for k, d in enumerate(mot):
                    if d is not None:
                        put(INSTR[lead](deg_midi(croot + d, self.root + 12) + up - (12 if lead == "whistle" else 0)),
                            t_bar + k * e8, 0.22)
            if not self.calm and bar >= 4:
                for b in (0, 2):
                    put(self.kick, t_bar + b * self.beat, 0.35)
                if bar >= 8:
                    for b in range(4):
                        put(shaker(self.rng), t_bar + (b + 0.5) * self.beat, 0.05)
        return x


def write_audio(song, n_scenes, path):
    """Renders scene by scene (the 2 s tails carry over) straight into a 16-bit stereo wav."""
    carry = np.zeros(0, np.float32)
    seg = int(song.scene_len * SR)
    with wave.open(str(path), "wb") as w:
        w.setnchannels(2)
        w.setsampwidth(2)
        w.setframerate(SR)
        for i in range(n_scenes):
            x = song.scene(i)
            x[:len(carry)] += carry
            out, carry = x[:seg], x[seg:].copy()
            if i == 0:
                out[:int(0.05 * SR)] *= np.linspace(0, 1, int(0.05 * SR), dtype=np.float32)
            if i == n_scenes - 1:
                fo = int(2.5 * SR)
                out[-fo:] *= np.linspace(1, 0, fo, dtype=np.float32)
            y = np.tanh(out * 0.9) * 0.8                       # soft limiter: never harsh, never loud
            pcm = (y * 32767).astype(np.int16)
            w.writeframes(np.repeat(pcm[:, None], 2, axis=1).tobytes())


# ----------------------------------------------------------------------------- drawing

def shape_path(ctx, kind, s):
    if kind == "circle":
        ctx.arc(0, 0, s, 0, TAU)
    elif kind == "square":
        r = s * 0.3
        ctx.new_sub_path()
        for cx, cy, a in ((s - r, -s + r, -math.pi / 2), (s - r, s - r, 0), (-s + r, s - r, math.pi / 2), (-s + r, -s + r, math.pi)):
            ctx.arc(cx, cy, r, a, a + math.pi / 2)
        ctx.close_path()
    elif kind == "heart":
        ctx.move_to(0, s * 0.95)
        ctx.curve_to(-s * 1.5, -s * 0.1, -s * 0.65, -s * 1.15, 0, -s * 0.45)
        ctx.curve_to(s * 0.65, -s * 1.15, s * 1.5, -s * 0.1, 0, s * 0.95)
        ctx.close_path()
    else:
        pts = {"triangle": [(1.2, a) for a in (-90, 30, 150)],
               "hexagon": [(1.05, a) for a in range(-90, 270, 60)],
               "star": [((1.2 if k % 2 == 0 else 0.58), -90 + 36 * k) for k in range(10)]}[kind]
        for k, (rr, a) in enumerate(pts):
            p = (s * rr * math.cos(math.radians(a)), s * rr * math.sin(math.radians(a)))
            (ctx.move_to if k == 0 else ctx.line_to)(*p)
        ctx.close_path()


def character(ctx, kind, x, y, s, color, t, phase=0.0, rot=0.0, sx=1.0, sy=1.0, alpha=1.0, sleepy=False, look=(0, 0)):
    """A friendly shape with a face. Blinks now and then."""
    ctx.save()
    ctx.translate(x, y)
    ctx.rotate(rot)
    ctx.scale(sx, sy)
    r, g, b = hexrgb(color)
    shape_path(ctx, kind, s)
    ctx.set_source_rgba(r, g, b, alpha)
    ctx.fill_preserve()
    ctx.set_line_join(cairo.LINE_JOIN_ROUND)
    ctx.set_line_width(s * 0.16)
    ctx.stroke()
    fy = s * 0.1 if kind in ("triangle", "heart") else 0.0
    ex, ey, er = s * 0.33, fy - s * 0.12, s * 0.18
    blink = sleepy or ((t + phase * 3.1) % 4.3) < 0.13
    for side in (-1, 1):
        if blink:
            ctx.set_source_rgba(0, 0, 0, alpha)
            ctx.set_line_width(s * 0.06)
            ctx.set_line_cap(cairo.LINE_CAP_ROUND)
            ctx.arc(side * ex, ey - er * 0.2, er * 0.8, 0.15 * math.pi, 0.85 * math.pi)
            ctx.stroke()
            continue
        ctx.arc(side * ex, ey, er, 0, TAU)
        ctx.set_source_rgba(1, 1, 1, alpha)
        ctx.fill_preserve()
        ctx.set_source_rgba(0, 0, 0, alpha)
        ctx.set_line_width(s * 0.035)
        ctx.stroke()
        ctx.arc(side * ex + look[0] * er * 0.35, ey + look[1] * er * 0.35, er * 0.5, 0, TAU)
        ctx.fill()
        ctx.arc(side * ex + look[0] * er * 0.35 + er * 0.18, ey + look[1] * er * 0.35 - er * 0.2, er * 0.14, 0, TAU)
        ctx.set_source_rgba(1, 1, 1, alpha)
        ctx.fill()
    ctx.set_source_rgba(1, 0.45, 0.6, 0.55 * alpha)
    for side in (-1, 1):
        ctx.arc(side * s * 0.52, fy + s * 0.18, s * 0.11, 0, TAU)
        ctx.fill()
    ctx.set_source_rgba(0, 0, 0, alpha)
    ctx.set_line_width(s * 0.07)
    ctx.set_line_cap(cairo.LINE_CAP_ROUND)
    ctx.arc(0, fy + s * 0.12, s * 0.26, 0.18 * math.pi, 0.82 * math.pi)
    ctx.stroke()
    ctx.restore()


def bounce(beat, phase=0.0):
    return abs(math.sin(math.pi * (beat + phase)))


def fill_bg(ctx, w, h, rgb=(0, 0, 0)):
    ctx.set_source_rgb(*rgb)
    ctx.rectangle(0, 0, w, h)
    ctx.fill()


def twinkles(ctx, w, h, dots, t, base=0.25):
    for (x, y, r, col, ph) in dots:
        cr, cg, cb = hexrgb(col)
        ctx.set_source_rgba(cr, cg, cb, base + 0.18 * math.sin(t * 1.3 + ph * 6))
        ctx.arc(x * w, y * h, r * min(w, h), 0, TAU)
        ctx.fill()


def sc_dance(ctx, w, h, tl, beat, p):
    fill_bg(ctx, w, h, p.get("bg", (0, 0, 0)))
    twinkles(ctx, w, h, p["dots"], tl)
    chars = p["chars"]
    n = len(chars)
    rows = 2 if h > w else 1
    per = math.ceil(n / rows)
    s = min(w / (per + 1) * 0.36, h / rows * 0.17)
    bar = int(beat // 4)
    for i, (kind, col) in enumerate(chars):
        row, col_i = divmod(i, per)
        cnt = min(per, n - row * per)
        x = w * (col_i + 1) / (cnt + 1)
        ground = h * (0.62 if rows == 1 else 0.40 + 0.30 * row)
        mode = bar % 4
        ph = 0.0 if mode < 2 else (0.5 * (i % 2) if mode == 2 else 0.22 * i)
        bz = bounce(beat, ph)
        y = ground - bz * s * 0.7
        squash = 1 - bz
        sx, sy = 1 + 0.14 * squash ** 4, 1 - 0.12 * squash ** 4
        rot = 0.14 * math.sin(math.pi * beat / 2 + i)
        if beat % 32 < 1:
            rot += TAU * ease(beat % 32)
        pop = ease((tl - 0.2 - i * 0.18) / 0.45)
        ctx.set_source_rgba(1, 1, 1, 0.08)
        ctx.save()
        ctx.translate(x, ground + s * 1.15)
        ctx.scale(1, 0.22)
        ctx.arc(0, 0, s * (0.9 - 0.3 * bz), 0, TAU)
        ctx.restore()
        ctx.fill()
        if pop > 0:
            character(ctx, kind, x, y, s * pop, col, tl, i, rot, sx, sy, look=(math.sin(tl * 0.7 + i), 0.2))


def sc_bubbles(ctx, w, h, tl, beat, p):
    fill_bg(ctx, w, h, p.get("bg", (0.02, 0.03, 0.08)))
    m = min(w, h)
    for (x0, spd, r, col, ph, face, kind) in p["bubbles"]:
        rr = r * m * (1 + 0.05 * bounce(beat, ph))
        span = h + 4 * rr
        y = h + 2 * rr - ((tl * spd * m + ph * span) % span)
        x = x0 * w + math.sin(tl * 0.8 + ph * 7) * m * 0.04
        if face:
            character(ctx, kind, x, y, rr, col, tl, ph, 0.2 * math.sin(tl + ph * 5), alpha=0.95)
            continue
        cr, cg, cb = hexrgb(col)
        ctx.set_source_rgba(cr, cg, cb, 0.75)
        ctx.arc(x, y, rr, 0, TAU)
        ctx.fill()
        ctx.set_source_rgba(1, 1, 1, 0.55)
        ctx.set_line_width(rr * 0.12)
        ctx.arc(x, y, rr * 0.68, 1.1 * math.pi, 1.45 * math.pi)
        ctx.stroke()


def sc_orbit(ctx, w, h, tl, beat, p):
    fill_bg(ctx, w, h, p.get("bg", (0, 0, 0)))
    twinkles(ctx, w, h, p["dots"], tl, 0.18)
    cx, cy, m = w / 2, h / 2, min(w, h)
    kind, col = p["center"]
    cr, cg, cb = hexrgb(col)
    g = cairo.RadialGradient(cx, cy, 0, cx, cy, m * 0.55)
    g.add_color_stop_rgba(0, cr, cg, cb, 0.22)
    g.add_color_stop_rgba(1, cr, cg, cb, 0)
    ctx.set_source(g)
    ctx.paint()
    sats = p["sats"]
    for i, (k, c) in enumerate(sats):
        a = TAU * i / len(sats) + tl * 0.35 * p["dir"]
        x = cx + math.cos(a) * w * 0.34 if w > h else cx + math.cos(a) * w * 0.36
        y = cy + math.sin(a) * h * 0.36 if w > h else cy + math.sin(a) * h * 0.3
        character(ctx, k, x, y - bounce(beat, i * 0.25) * m * 0.03, m * 0.065, c, tl, i, a * 0.3,
                  look=(math.cos(a + math.pi) * 0.6, math.sin(a + math.pi) * 0.6))
    s = m * 0.19 * (1 + 0.07 * bounce(beat))
    character(ctx, kind, cx, cy, s, col, tl, 0.3, 0.1 * math.sin(math.pi * beat / 2), look=(math.sin(tl * 0.9), 0))


def sc_contrast(ctx, w, h, tl, beat, p):
    cx, cy, m = w / 2, h / 2, min(w, h)
    dark = p["dark"]
    fg, bg = ((1, 1, 1), (0, 0, 0)) if dark else ((0, 0, 0), (1, 1, 1))
    fill_bg(ctx, w, h, bg)
    ctx.set_source_rgb(*fg)
    if p["pattern"] == "rings":
        step = m * 0.09
        off = (tl * m * 0.025) % (2 * step)                      # slow outward drift
        r = off
        while r < math.hypot(w, h) / 2 + 2 * step:
            ctx.arc(cx, cy, r + step * 0.5, 0, TAU)
            ctx.set_line_width(step)
            ctx.stroke()
            r += 2 * step
    elif p["pattern"] == "pinwheel":
        k = 12
        rot = tl * 0.35 * p["dir"]
        R = math.hypot(w, h)
        for i in range(0, k, 2):
            ctx.move_to(cx, cy)
            ctx.arc(cx, cy, R, rot + TAU * i / k, rot + TAU * (i + 1) / k)
            ctx.close_path()
            ctx.fill()
    else:  # checker dots
        step = m * 0.16
        sh = (tl * m * 0.02) % step
        for j in range(-1, int(h / step) + 2):
            for i in range(-1, int(w / step) + 2):
                if (i + j) % 2 == 0:
                    ctx.arc(i * step + sh, j * step, step * 0.3, 0, TAU)
                    ctx.fill()
    # framed friend in the middle: plain disc behind it keeps the face readable
    s = m * 0.17 * (1 + 0.06 * bounce(beat))
    ctx.set_source_rgb(*bg)
    ctx.arc(cx, cy, s * 1.55, 0, TAU)
    ctx.fill()
    ctx.set_source_rgb(*fg)
    ctx.set_line_width(s * 0.12)
    ctx.arc(cx, cy, s * 1.55, 0, TAU)
    ctx.stroke()
    kind, col = p["center"]
    character(ctx, kind, cx, cy, s, col, tl, 0.7, 0.12 * math.sin(math.pi * beat / 2), look=(math.sin(tl * 0.6), 0))


def sc_parade(ctx, w, h, tl, beat, p):
    fill_bg(ctx, w, h, (0, 0, 0))
    twinkles(ctx, w, h, p["dots"], tl, 0.2)
    m = min(w, h)
    base = h * 0.78
    for k, col in enumerate(("#1f7a3a", "#2fa84f")):
        ctx.set_source_rgb(*hexrgb(col))
        ctx.move_to(0, h)
        for xi in range(0, w + 41, 40):
            ctx.line_to(xi, base + k * m * 0.06 + math.sin(xi / w * TAU * (1.5 + k) + tl * 0.3 * (k + 1)) * m * 0.03)
        ctx.line_to(w, h)
        ctx.close_path()
        ctx.fill()
    chars = p["chars"]
    s = m * 0.085
    gap = s * 3.0
    total = len(chars) * gap + w * 0.6
    for i, (kind, col) in enumerate(chars):
        x = ((tl * m * 0.12 + i * gap) % total) - s * 2
        bz = bounce(beat, 0.15 * i)
        y = base - s * 1.1 - bz * s * 0.8
        character(ctx, kind, x, y, s, col, tl, i, 0.12 * math.sin(math.pi * beat + i), look=(0.6, 0))


def sc_kaleido(ctx, w, h, tl, beat, p):
    fill_bg(ctx, w, h, (0, 0, 0))
    cx, cy, m = w / 2, h / 2, min(w, h)
    for ring in range(3, 0, -1):
        cnt = 4 + 4 * ring
        rad = m * 0.15 * ring
        rot = tl * 0.12 * (1 if ring % 2 else -1)
        s = m * 0.045 * (1 + 0.15 * bounce(beat, 0.33 * ring)) * (1 + 0.15 * ring)
        for i in range(cnt):
            a = rot + TAU * i / cnt
            col = p["colors"][(i + ring) % len(p["colors"])]
            ctx.save()
            ctx.translate(cx + math.cos(a) * rad, cy + math.sin(a) * rad)
            ctx.rotate(a + math.pi / 2)
            shape_path(ctx, p["kinds"][ring - 1], s)
            ctx.set_source_rgb(*hexrgb(col))
            ctx.fill()
            ctx.restore()
    kind, col = p["center"]
    character(ctx, kind, cx, cy, m * 0.1 * (1 + 0.06 * bounce(beat)), col, tl, 0.1, 0.15 * math.sin(math.pi * beat / 2))


def sc_night(ctx, w, h, tl, beat, p):
    g = cairo.LinearGradient(0, 0, 0, h)
    g.add_color_stop_rgb(0, 0.02, 0.03, 0.12)
    g.add_color_stop_rgb(1, 0.07, 0.06, 0.25)
    ctx.set_source(g)
    ctx.paint()
    m = min(w, h)
    for (x, y, r, col, ph) in p["dots"]:
        ctx.save()
        ctx.translate(x * w, y * h)
        ctx.rotate(tl * 0.1 + ph)
        shape_path(ctx, "star", r * m * 1.6)
        ctx.set_source_rgba(1, 0.92, 0.55, 0.45 + 0.35 * math.sin(tl * 0.9 + ph * 6))
        ctx.fill()
        ctx.restore()
    for k, (cy, spd, sc) in enumerate(p["clouds"]):
        x = ((tl * spd * m + k * w * 0.45) % (w + m * 0.8)) - m * 0.4
        ctx.set_source_rgba(1, 1, 1, 0.10)
        for dx, dy, rr in ((0, 0, 0.09), (0.08, 0.02, 0.07), (-0.08, 0.02, 0.07)):
            ctx.arc(x + dx * m * sc, cy * h + dy * m * sc, rr * m * sc, 0, TAU)
            ctx.fill()
    mx, my = w * 0.5, h * 0.45 + math.sin(tl * 0.6) * m * 0.02
    character(ctx, "circle", mx, my, m * 0.17, "#ffe28a", tl, 0, 0.08 * math.sin(tl * 0.5), sleepy=True)
    for i, (kind, col) in enumerate(p["chars"]):
        a = TAU * i / len(p["chars"]) + tl * 0.08
        character(ctx, kind, mx + math.cos(a) * m * 0.38, my + math.sin(a) * m * 0.3, m * 0.05, col, tl, i,
                  sleepy=(i + int(tl / 6)) % 3 == 0)


SCENES = {"dance": sc_dance, "bubbles": sc_bubbles, "orbit": sc_orbit, "contrast": sc_contrast,
          "parade": sc_parade, "kaleido": sc_kaleido, "night": sc_night}


def scene_params(kind, r, theme):
    pal = CONTRAST if theme == "contrast" else PALETTE
    dots = [(r.random(), r.random(), r.uniform(0.003, 0.008), r.choice(PALETTE), r.random()) for _ in range(40)]
    pick = lambda: (r.choice(SHAPES), r.choice(pal))
    p = {"type": kind, "dots": dots}
    if kind == "dance":
        cols = r.sample(PALETTE, 5)
        p["chars"] = [(r.choice(SHAPES), cols[i]) for i in range(r.choice([3, 4, 5]))]
    elif kind == "bubbles":
        p["bubbles"] = [(r.random(), r.uniform(0.08, 0.16), r.uniform(0.03, 0.07), r.choice(PALETTE), r.random(),
                         r.random() < 0.25, r.choice(SHAPES)) for _ in range(22)]
    elif kind == "orbit":
        p["center"] = (r.choice(SHAPES), r.choice(PALETTE))
        p["sats"] = [pick() for _ in range(r.choice([6, 8]))]
        p["dir"] = r.choice([-1, 1])
    elif kind == "contrast":
        p["dark"] = r.random() < 0.5
        p["pattern"] = r.choice(["rings", "pinwheel", "dots"])
        p["dir"] = r.choice([-1, 1])
        p["center"] = (r.choice(SHAPES), r.choice(["#ff2d2d", "#ffd60a"]))
    elif kind == "parade":
        p["chars"] = [pick() for _ in range(8)]
    elif kind == "kaleido":
        p["colors"] = r.sample(PALETTE, 4)
        p["kinds"] = [r.choice(SHAPES) for _ in range(3)]
        p["center"] = pick()
    elif kind == "night":
        p["clouds"] = [(r.uniform(0.1, 0.8), r.uniform(0.01, 0.025), r.uniform(0.8, 1.4)) for _ in range(3)]
        p["chars"] = [pick() for _ in range(5)]
    return p


# ----------------------------------------------------------------------------- episode

def theme_for(ep):
    return THEMES[(ep - 1) % len(THEMES)]


def plan(ep, minutes):
    key, title, short, emoji, (lo, hi), mix = theme_for(ep)
    r = random.Random(ep * 7919 + 17)
    bpm = r.randint(lo, hi)
    song = Song(ep * 104729 + 3, bpm, calm=(key == "night"))
    n = max(1, int(minutes * 60 // song.scene_len))     # never longer than asked (15 min cap on unverified channels)
    kinds, last = [key], key
    while len(kinds) < n:
        k = key if r.random() < 0.45 else r.choice(mix)
        if k == last and r.random() < 0.7:
            continue
        kinds.append(k)
        last = k
    scenes = [scene_params(k, r, key) for k in kinds]
    return song, scenes


def render_video(path, w, h, song, scenes, audio, duration, fade_in=True):
    surf = cairo.ImageSurface(cairo.FORMAT_ARGB32, w, h)
    ctx = cairo.Context(surf)
    cmd = [imageio_ffmpeg.get_ffmpeg_exe(), "-y", "-loglevel", "error",
           "-f", "rawvideo", "-pix_fmt", "bgra", "-s", f"{w}x{h}", "-r", str(FPS), "-i", "-",
           "-i", str(audio), "-t", f"{duration:.3f}",
           "-c:v", "libx264", "-preset", "veryfast", "-crf", "21", "-pix_fmt", "yuv420p",
           "-c:a", "aac", "-b:a", "160k", "-movflags", "+faststart", str(path)]
    proc = subprocess.Popen(cmd, stdin=subprocess.PIPE)
    frames = int(duration * FPS)
    L = song.scene_len
    for f in range(frames):
        t = f / FPS
        si = min(len(scenes) - 1, int(t // L))
        tl = t - si * L
        beat = t * song.bpm / 60.0
        p = scenes[si]
        ctx.save()
        SCENES[p["type"]](ctx, w, h, tl, beat, p)
        ctx.restore()
        a = 0.0
        if fade_in or si > 0:
            a = max(a, 1 - tl / FADE)
        a = max(a, 1 - (min(L, duration - si * L) - tl) / FADE)
        if a > 0:
            ctx.set_source_rgba(0, 0, 0, min(1.0, a))
            ctx.paint()
        surf.flush()
        proc.stdin.write(surf.get_data())
        if f % (FPS * 60) == 0:
            print(f"  {path.name}: {t / 60:.0f}/{duration / 60:.0f} min", flush=True)
    proc.stdin.close()
    if proc.wait() != 0:
        raise SystemExit(f"ffmpeg failed for {path}")


def thumbnail(path, ep, theme_key):
    w, h = 1280, 720
    surf = cairo.ImageSurface(cairo.FORMAT_ARGB32, w, h)
    ctx = cairo.Context(surf)
    r = random.Random(ep)
    bgc = {"night": (0.06, 0.05, 0.22), "contrast": (0, 0, 0)}.get(theme_key, hexrgb(r.choice(["#1b1464", "#0b3d91", "#4a148c"])))
    g = cairo.RadialGradient(w / 2, h / 2, 0, w / 2, h / 2, w * 0.7)
    g.add_color_stop_rgb(0, *[min(1, c * 2.2 + 0.08) for c in bgc])
    g.add_color_stop_rgb(1, *bgc)
    ctx.set_source(g)
    ctx.paint()
    for _ in range(60):
        ctx.set_source_rgba(*hexrgb(r.choice(PALETTE)), 0.5)
        ctx.arc(r.random() * w, r.random() * h, r.uniform(3, 9), 0, TAU)
        ctx.fill()
    cols = r.sample(PALETTE, 4)
    kinds = r.sample(SHAPES, 4)
    for i in range(4):
        x = w * (i + 1) / 5
        y = h * 0.52 + (-1 if i % 2 else 1) * h * 0.06
        character(ctx, kinds[i], x, y, h * 0.16, cols[i], 1.0, i, (i - 1.5) * 0.12, look=(0, 0.3))
    surf.write_to_png(str(path))


def metadata(ep, minutes, theme):
    key, title, short, emoji, _, _ = theme
    long_title = f"{title} {emoji} Baby Sensory | High Contrast Animation & Music for Babies"
    desc = "\n".join([
        f"{title} {emoji} — {minutes} minutes of friendly, colourful shapes dancing to calm original music.",
        "Made for babies and toddlers (0–3 years).",
        "",
        "• Bright, high-contrast shapes that are easy for little eyes to follow",
        "• Smooth, gentle movement with no flashing lights",
        "• Soft original music: music box, marimba and kalimba",
        "",
        "Watch together with your little one: name the colours and shapes, clap to the beat and wave hello! 👋",
        "A new video every day. 🌈",
        "",
        "100% original animation and music, made for this channel.",
        f"Episode {ep}",
        "#babysensory #sensoryvideo #babyvideo",
    ])
    tags = ["baby sensory", "sensory video", "baby video", "high contrast", "baby sensory video",
            "toddler video", "babies", "infant visual stimulation", "baby music", "calm music for babies",
            "colors for babies", "shapes for kids", title.lower()]
    return {"episode": ep, "theme": key, "minutes": minutes, "madeForKids": True, "categoryId": "27",
            "title": long_title[:100], "description": desc, "tags": tags,
            "short_title": f"{short} {emoji} Baby Sensory #shorts"[:100],
            "date": datetime.date.today().isoformat()}


def episodes():
    OUT.mkdir(exist_ok=True)
    return sorted(OUT.glob("ep_*"))


def waiting_or_published_today():
    today = datetime.date.today().isoformat()
    for d in episodes():
        up = d / "uploaded.json"
        if (d / "video.mp4").exists() and not up.exists():
            return f"{d.name} is waiting to be uploaded"
        if up.exists():
            info = json.loads(up.read_text(encoding="utf-8"))
            if str(info.get("uploaded", "")).startswith(today):
                return f"already published today ({d.name})"
    return None


def render(ep, minutes, preview=False):
    theme = theme_for(ep)
    song, scenes = plan(ep, minutes)
    folder = OUT / (f"preview_{ep:03d}" if preview else f"ep_{ep:03d}")
    folder.mkdir(parents=True, exist_ok=True)
    audio = folder / "audio.wav"
    write_audio(song, len(scenes), audio)
    dur = len(scenes) * song.scene_len
    print(f"episode {ep} [{theme[0]}] {song.bpm} BPM, {len(scenes)} scenes, {dur / 60:.1f} min", flush=True)
    if preview:
        render_video(folder / "video.mp4", 640, 360, song, scenes, audio, min(dur, 60))
        render_video(folder / "short.mp4", 360, 640, song, scenes[:1], audio, song.scene_len, fade_in=False)
    else:
        render_video(folder / "video.mp4", 1920, 1080, song, scenes, audio, dur)
        render_video(folder / "short.mp4", 1080, 1920, song, scenes[:1], audio, song.scene_len, fade_in=False)
    thumbnail(folder / "thumbnail.png", ep, theme[0])
    meta = metadata(ep, round(dur / 60), theme)
    meta.update({"bpm": song.bpm, "scenes": [s["type"] for s in scenes]})
    (folder / "metadata.json").write_text(json.dumps(meta, indent=2, ensure_ascii=False), encoding="utf-8")
    audio.unlink()
    print(f"done -> {folder}")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--episode", type=int)
    # YouTube rejects videos over 15 min until the channel is phone-verified (youtube.com/verify).
    ap.add_argument("--minutes", type=float, default=14.5)
    ap.add_argument("--preview", action="store_true")
    ap.add_argument("--if-needed", action="store_true")
    ap.add_argument("--ahead", type=int, metavar="N",
                    help="render new episodes until N are ready and not yet uploaded (daily run)")
    a = ap.parse_args()
    if a.ahead:
        ready = [d.name for d in episodes() if (d / "video.mp4").exists() and not (d / "uploaded.json").exists()]
        need = a.ahead - len(ready)
        print(f"ready and waiting: {', '.join(ready) or 'none'} -> rendering {max(0, need)}")
        for _ in range(need):
            free = shutil.disk_usage(ROOT).free / 1e9
            if free < MIN_FREE_GB:
                raise SystemExit(f"DISK FULL: only {free:.1f} GB free on the disk, need {MIN_FREE_GB} GB to render. "
                                 "Free some space; the episodes already scheduled keep going out.")
            nums = [int(d.name[3:]) for d in episodes() if (d / "metadata.json").exists()]
            ep = max(nums) + 1 if nums else 1
            try:
                render(ep, a.minutes)
            except BaseException:
                shutil.rmtree(OUT / f"ep_{ep:03d}", ignore_errors=True)   # never leave a half episode behind
                raise
        return
    if a.if_needed:
        why = waiting_or_published_today()
        if why:
            print(f"not rendering: {why}")
            return
    nums = [int(d.name[3:]) for d in episodes()]
    ep = a.episode or (max(nums) + 1 if nums else 1)
    render(ep, 1.5 if a.preview else a.minutes, a.preview)


if __name__ == "__main__":
    main()
