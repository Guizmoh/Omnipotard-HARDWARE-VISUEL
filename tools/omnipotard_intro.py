#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
OMNIPOTARD — generateur d'intro video "oscilloscope"

Sequence :
  1. Amorce      : la trace de l'oscilloscope se stabilise sur la ligne de base.
  2. Balayage    : un balayage d'oscillateur (vert fluo) dessine une MPC.
  3. Groove      : les pads s'allument sur un motif 16 pas, l'ecran affiche l'onde.
  4. Dissolution : la MPC fond en sinusoides.
  5. Titre       : les sinusoides se reorganisent en lettres -> OMNIPOTARD.
  6. Extinction  : collapse CRT (l'image se ferme sur une ligne puis un point).

Tout est trace au faisceau : chaque forme est un chemin echantillonne a pas
d'arc constant, projete dans un buffer d'intensite, puis colorise en phosphore
vert avec bloom, scanlines, grain et glitchs.

Rendu image par image, sans etat partage entre frames -> parallelisable.
"""

import argparse
import math
import os
import subprocess
import sys
import tempfile
import wave

import numpy as np

# --------------------------------------------------------------------------
# Repere : unite = demi-hauteur de l'image. y vers le haut, centre en (0, 0).
# En 16/9 la zone visible est x dans [-1.78, 1.78], y dans [-1, 1].
# --------------------------------------------------------------------------

VERT_FLUO = (0.24, 1.00, 0.16)   # #39FF14
VERT_HALO = (0.10, 1.00, 0.34)   # halo legerement plus froid


# ==========================================================================
#  Outils numeriques
# ==========================================================================

def smoothstep(a, b, x):
    t = np.clip((x - a) / max(1e-9, b - a), 0.0, 1.0)
    return t * t * (3.0 - 2.0 * t)


def ease_out(t, p=3.0):
    return 1.0 - (1.0 - np.clip(t, 0.0, 1.0)) ** p


def ease_in_out(t):
    t = np.clip(t, 0.0, 1.0)
    return t * t * (3.0 - 2.0 * t)


def gauss(a, sigma):
    """Flou gaussien separable (noyau explicite)."""
    if sigma <= 0.05:
        return a
    r = max(1, int(sigma * 2.5))
    k = np.exp(-0.5 * (np.arange(-r, r + 1) / sigma) ** 2)
    k /= k.sum()
    pad = np.pad(a, ((0, 0), (r, r)), mode="edge")
    out = np.zeros_like(a)
    for i, w in enumerate(k):
        out += w * pad[:, i:i + a.shape[1]]
    pad = np.pad(out, ((r, r), (0, 0)), mode="edge")
    res = np.zeros_like(a)
    for i, w in enumerate(k):
        res += w * pad[i:i + a.shape[0], :]
    return res


def downsample(a, f):
    h, w = a.shape
    h2, w2 = h // f, w // f
    return a[:h2 * f, :w2 * f].reshape(h2, f, w2, f).mean(axis=(1, 3))


def upsample(a, f, shape):
    b = np.repeat(np.repeat(a, f, axis=0), f, axis=1)
    out = np.zeros(shape, dtype=b.dtype)
    hh = min(shape[0], b.shape[0])
    ww = min(shape[1], b.shape[1])
    out[:hh, :ww] = b[:hh, :ww]
    if hh < shape[0]:
        out[hh:, :ww] = b[b.shape[0] - 1, :ww]
    if ww < shape[1]:
        out[:, ww:] = out[:, ww - 1:ww]
    return out


class Beam:
    """Accumulateur de faisceau : on empile les impacts, un seul bincount a la fin."""

    def __init__(self, h, w, gain=1.0):
        self.h, self.w = h, w
        self.gain = gain          # normalisation d'intensite selon la resolution
        self.idx = []
        self.wts = []

    def add(self, px, py, weight):
        h, w = self.h, self.w
        m = (px >= 1.0) & (px < w - 2.0) & (py >= 1.0) & (py < h - 2.0)
        if not np.any(m):
            return
        x = px[m]
        y = py[m]
        if isinstance(weight, np.ndarray) and weight.size == px.size:
            ww = weight[m]
        else:
            ww = np.full(x.size, float(weight))
        ww = ww * self.gain
        x0 = x.astype(np.int32)
        y0 = y.astype(np.int32)
        fx = x - x0
        fy = y - y0
        base = y0 * w + x0
        self.idx.append(np.concatenate([base, base + 1, base + w, base + w + 1]))
        self.wts.append(np.concatenate([
            ww * (1.0 - fx) * (1.0 - fy),
            ww * fx * (1.0 - fy),
            ww * (1.0 - fx) * fy,
            ww * fx * fy,
        ]))

    def render(self):
        if not self.idx:
            return np.zeros((self.h, self.w), dtype=np.float32)
        idx = np.concatenate(self.idx)
        wts = np.concatenate(self.wts)
        buf = np.bincount(idx, weights=wts, minlength=self.h * self.w)
        return buf.reshape(self.h, self.w).astype(np.float32)


# ==========================================================================
#  Geometrie : chemins echantillonnes a pas constant
# ==========================================================================

STEP = 0.0016  # pas d'echantillonnage (unites) -> ~0.9 px en 1080p


class Path:
    """Chemin discretise : points, normales, abscisse curviligne."""

    __slots__ = ("P", "N", "s", "tag")

    def __init__(self, pts, closed=False, tag="", step=STEP):
        pts = np.asarray(pts, dtype=np.float64)
        if closed and not np.allclose(pts[0], pts[-1]):
            pts = np.vstack([pts, pts[0]])
        d = np.linalg.norm(np.diff(pts, axis=0), axis=1)
        s = np.concatenate([[0.0], np.cumsum(d)])
        length = float(s[-1])
        n = max(2, int(length / step))
        t = np.linspace(0.0, length, n)
        self.P = np.stack([np.interp(t, s, pts[:, 0]), np.interp(t, s, pts[:, 1])], axis=1)
        tan = np.gradient(self.P, axis=0)
        tan /= (np.linalg.norm(tan, axis=1, keepdims=True) + 1e-12)
        self.N = np.stack([-tan[:, 1], tan[:, 0]], axis=1)
        self.s = t
        self.tag = tag


def circle_pts(cx, cy, r, n=240):
    a = np.linspace(0.0, 2.0 * math.pi, n)
    return np.stack([cx + r * np.cos(a), cy + r * np.sin(a)], axis=1)


def rrect_pts(x0, y0, x1, y1, r, n=9):
    r = min(r, (x1 - x0) * 0.5, (y1 - y0) * 0.5)
    out = []
    corners = [
        (x1 - r, y0 + r, -math.pi / 2, 0.0),
        (x1 - r, y1 - r, 0.0, math.pi / 2),
        (x0 + r, y1 - r, math.pi / 2, math.pi),
        (x0 + r, y0 + r, math.pi, 1.5 * math.pi),
    ]
    for cx, cy, a0, a1 in corners:
        a = np.linspace(a0, a1, n)
        out.append(np.stack([cx + r * np.cos(a), cy + r * np.sin(a)], axis=1))
    return np.vstack(out)


# ---- alphabet monotrait ---------------------------------------------------
# boite unitaire : x dans [0, w], y dans [0, 1] (y vers le haut)

GLYPHS = {
    "O": (0.62, [[(0, .18), (.18, 0), (.44, 0), (.62, .18), (.62, .82), (.44, 1), (.18, 1), (0, .82), (0, .18)]]),
    "M": (0.72, [[(0, 0), (0, 1), (.36, .50), (.72, 1), (.72, 0)]]),
    "N": (0.62, [[(0, 0), (0, 1), (.62, 0), (.62, 1)]]),
    "I": (0.10, [[(.05, 0), (.05, 1)]]),
    "P": (0.60, [[(0, 0), (0, 1), (.44, 1), (.60, .84), (.60, .66), (.44, .50), (0, .50)]]),
    "T": (0.62, [[(0, 1), (.62, 1)], [(.31, 1), (.31, 0)]]),
    "A": (0.66, [[(0, 0), (.33, 1), (.66, 0)], [(.115, .35), (.545, .35)]]),
    "R": (0.62, [[(0, 0), (0, 1), (.44, 1), (.60, .84), (.60, .66), (.44, .50), (0, .50)], [(.30, .50), (.62, 0)]]),
    "D": (0.62, [[(0, 0), (0, 1), (.42, 1), (.62, .80), (.62, .20), (.42, 0), (0, 0)]]),
    "C": (0.62, [[(.62, .20), (.44, 0), (.18, 0), (0, .18), (0, .82), (.18, 1), (.44, 1), (.62, .80)]]),
    "S": (0.60, [[(.60, .84), (.44, 1), (.16, 1), (0, .84), (0, .66), (.16, .50), (.44, .50), (.60, .34), (.60, .16), (.44, 0), (.16, 0), (0, .16)]]),
    "E": (0.56, [[(.56, 1), (0, 1), (0, 0), (.56, 0)], [(0, .50), (.44, .50)]]),
    "L": (0.54, [[(0, 1), (0, 0), (.54, 0)]]),
    "V": (0.64, [[(0, 1), (.32, 0), (.64, 1)]]),
    "U": (0.62, [[(0, 1), (0, .18), (.18, 0), (.44, 0), (.62, .18), (.62, 1)]]),
    " ": (0.34, []),
}

TRACKING = 0.145  # chasse entre glyphes (en hauteur de glyphe)


def text_width(txt):
    w = 0.0
    for i, ch in enumerate(txt):
        w += GLYPHS[ch][0] + (TRACKING if i < len(txt) - 1 else 0.0)
    return w


def text_paths(txt, height, x0, y0, step=STEP, center=True, tag="txt"):
    """Retourne un Path par trait, positionne dans le repere scope."""
    total = text_width(txt) * height
    x = x0 - total * 0.5 if center else x0
    out = []
    for gi, ch in enumerate(txt):
        gw, strokes = GLYPHS[ch]
        for st in strokes:
            pts = [(x + px * height, y0 + py * height) for px, py in st]
            out.append(Path(pts, tag="%s:%d" % (tag, gi), step=step))
        x += (gw + TRACKING) * height
    return out


# ---- la MPC ---------------------------------------------------------------

PAD_X0, PAD_Y0, PAD_SZ, PAD_GAP = 0.30, -0.615, 0.1575, 0.050


def pad_rect(i, j):
    """i = ligne (0 = bas), j = colonne (0 = gauche)."""
    x0 = PAD_X0 + j * (PAD_SZ + PAD_GAP)
    y0 = PAD_Y0 + i * (PAD_SZ + PAD_GAP)
    return x0, y0, x0 + PAD_SZ, y0 + PAD_SZ


def build_mpc(step=STEP):
    """Construit la machine : liste de Path, taggues par organe."""
    P = []

    # chassis
    P.append(Path(rrect_pts(-1.22, -0.73, 1.22, 0.73, 0.10), closed=True, tag="body", step=step))
    P.append(Path(rrect_pts(-1.16, -0.67, 1.16, 0.67, 0.08), closed=True, tag="body", step=step))

    # ecran LCD + cadre
    P.append(Path(rrect_pts(-1.04, 0.15, -0.34, 0.59, 0.03), closed=True, tag="lcd", step=step))
    P.append(Path(rrect_pts(-1.00, 0.19, -0.38, 0.55, 0.02), closed=True, tag="lcd", step=step))

    # molette de donnees
    P.append(Path(circle_pts(0.06, 0.37, 0.20), closed=True, tag="wheel", step=step))
    P.append(Path(circle_pts(0.06, 0.37, 0.075), closed=True, tag="wheel", step=step))

    # potentiometres
    for k in range(4):
        cx = 0.45 + k * 0.175
        P.append(Path(circle_pts(cx, 0.52, 0.058), closed=True, tag="knob%d" % k, step=step))
        P.append(Path([(cx, 0.52), (cx - 0.035, 0.52 - 0.046)], tag="knob%d" % k, step=step))

    # touches de fonction sous l'ecran
    for k in range(6):
        x0 = -1.04 + k * 0.115
        P.append(Path(rrect_pts(x0, -0.02, x0 + 0.09, 0.07, 0.015), closed=True, tag="fn%d" % k, step=step))

    # transport / touches basses
    for row in range(2):
        for k in range(5):
            x0 = -1.04 + k * 0.15
            y0 = -0.30 - row * 0.16
            P.append(Path(rrect_pts(x0, y0, x0 + 0.12, y0 + 0.10, 0.018),
                          closed=True, tag="btn%d" % (row * 5 + k), step=step))

    # grille de 16 pads
    for i in range(4):
        for j in range(4):
            x0, y0, x1, y1 = pad_rect(i, j)
            P.append(Path(rrect_pts(x0, y0, x1, y1, 0.026), closed=True,
                          tag="pad%d" % (i * 4 + j), step=step))

    # marquage
    P += text_paths("MPC", 0.105, -1.02, -0.62, step=step, center=False, tag="logo")

    return P


def pad_fill(i, j, nlines=7):
    """Traits de remplissage d'un pad (utilises quand il s'allume)."""
    x0, y0, x1, y1 = pad_rect(i, j)
    m = 0.022
    out = []
    for k in range(nlines):
        y = y0 + m + (y1 - y0 - 2 * m) * k / (nlines - 1.0)
        out.append(np.stack([np.linspace(x0 + m, x1 - m, 48), np.full(48, y)], axis=1))
    return np.vstack(out)


# ==========================================================================
#  Ondes
# ==========================================================================

WAVE_N = 2400
WAVE_X = np.linspace(-1.80, 1.80, WAVE_N)

BANDS = [(-0.42, 3.1, 0.0), (0.0, 2.3, 1.9), (0.42, 3.7, 4.1)]  # (y, k, phase)


def band_y(x, band, t, amp):
    y0, k, ph = BANDS[band]
    return y0 + amp * np.sin(k * math.pi * x + ph + t * 2.4)


def wave_points(band, t, amp):
    return np.stack([WAVE_X, band_y(WAVE_X, band, t, amp)], axis=1)


# ==========================================================================
#  Scenario
# ==========================================================================

class Timeline:
    """Bornes temporelles, mises a l'echelle si la duree change."""

    REF = 10.0
    KEYS = [
        ("boot", 0.00, 0.85),
        ("sweep", 0.85, 4.20),
        ("groove", 4.20, 5.60),
        ("melt", 5.60, 6.55),
        ("title", 6.55, 8.40),
        ("hold", 8.40, 9.52),
        ("out", 9.52, 10.00),
    ]

    def __init__(self, duration):
        f = duration / self.REF
        self.d = duration
        self.seg = {n: (a * f, b * f) for n, a, b in self.KEYS}

    def at(self, name, t):
        a, b = self.seg[name]
        return (t - a) / max(1e-9, b - a)

    def start(self, name):
        return self.seg[name][0]

    def end(self, name):
        return self.seg[name][1]


# motif 16 pas : (pad, force)
PATTERN = [
    (0, 1.0), (10, .45), (5, .55), (0, .40),
    (7, 1.0), (10, .45), (0, .70), (13, .50),
    (0, 1.0), (10, .45), (5, .55), (2, .60),
    (7, 1.0), (14, .55), (11, .65), (15, .80),
]

GLITCHES = [(4.62, .08), (5.18, .06), (5.58, .13), (6.52, .10), (8.42, .07)]


# ==========================================================================
#  Rendu
# ==========================================================================

class Renderer:
    def __init__(self, w, h, fps, duration, curve=True, seed=7):
        self.W, self.H = w, h
        self.fps = fps
        self.dur = duration
        self.tl = Timeline(duration)
        self.curve = curve
        self.seed = seed
        # echelle : la machine (largeur 2.44 unites) reste toujours cadree,
        # ce qui permet aussi les formats carre et vertical (shorts/reels).
        self.scale = min(h * 0.5, w * 0.5 / 1.36)
        self.px_step = STEP * self.scale
        # Un trait garde la meme luminosite quelle que soit la definition :
        # l'energie deposee par unite de longueur est repartie sur scale*sigma pixels.
        self.sigma = max(0.60, h / 1080.0 * 0.95)
        self.gain = (self.scale * self.sigma) / (360.0 * 0.6333)

        self.mpc = build_mpc()
        self.mpc_pts = np.vstack([p.P for p in self.mpc])
        self.mpc_x = self.mpc_pts[:, 0]
        self.mpc_band = np.where(self.mpc_pts[:, 1] > 0.22, 2,
                                 np.where(self.mpc_pts[:, 1] < -0.22, 0, 1))

        self.title = text_paths("OMNIPOTARD", 0.300, 0.0, -0.150, tag="ttl")
        self.title_P = np.vstack([p.P for p in self.title])
        self.title_N = np.vstack([p.N for p in self.title])
        self.title_s = np.concatenate([p.s for p in self.title])
        gi = np.concatenate([np.full(len(p.P), int(p.tag.split(":")[1])) for p in self.title])
        self.title_g = gi
        self.title_band = gi % 3
        # position d'origine : le texte est "deroule" sur toute la largeur des
        # trois sinusoides, puis se replie lettre par lettre pour former le mot.
        self.title_srcx = np.zeros(len(self.title_P))
        for b in range(3):
            m = self.title_band == b
            c = int(m.sum())
            if c:
                self.title_srcx[m] = -1.74 + 3.48 * (np.arange(c) / max(1, c - 1))

        if curve:
            self._build_warp()

    # -- geometrie ecran ---------------------------------------------------

    def to_px(self, P, collapse=1.0, shake=(0.0, 0.0)):
        s = self.scale
        x = self.W * 0.5 + P[:, 0] * s + shake[0]
        y = self.H * 0.5 - P[:, 1] * s * collapse + shake[1]
        return x, y

    def _build_warp(self):
        """Table de distorsion barillet (tube cathodique), bilineaire."""
        W, H = self.W, self.H
        yy, xx = np.mgrid[0:H, 0:W]
        nx = (xx / (W - 1.0)) * 2.0 - 1.0
        ny = (yy / (H - 1.0)) * 2.0 - 1.0
        r2 = nx * nx + ny * ny
        k = 0.055
        f = 1.0 + k * r2
        sx = (nx * f * 0.5 + 0.5) * (W - 1.0)
        sy = (ny * f * 0.5 + 0.5) * (H - 1.0)
        inside = (sx >= 0) & (sx <= W - 1.001) & (sy >= 0) & (sy <= H - 1.001)
        sx = np.clip(sx, 0, W - 1.001)
        sy = np.clip(sy, 0, H - 1.001)
        x0 = sx.astype(np.int32)
        y0 = sy.astype(np.int32)
        self.wfx = (sx - x0).astype(np.float32)[..., None]
        self.wfy = (sy - y0).astype(np.float32)[..., None]
        self.wi00 = (y0 * W + x0).ravel()
        self.wi01 = self.wi00 + 1
        self.wi10 = self.wi00 + W
        self.wi11 = self.wi10 + 1
        self.wmask = inside.astype(np.float32)[..., None]

    def _warp(self, img):
        W, H = self.W, self.H
        flat = img.reshape(-1, 3)
        a = flat[self.wi00].reshape(H, W, 3)
        b = flat[self.wi01].reshape(H, W, 3)
        c = flat[self.wi10].reshape(H, W, 3)
        d = flat[self.wi11].reshape(H, W, 3)
        fx, fy = self.wfx, self.wfy
        top = a * (1 - fx) + b * fx
        bot = c * (1 - fx) + d * fx
        return (top * (1 - fy) + bot * fy) * self.wmask

    # -- elements ----------------------------------------------------------

    def _grid(self, beam, t, alpha, collapse):
        """Reticule de l'oscilloscope."""
        if alpha <= 0.002:
            return
        xs = np.linspace(-1.6, 1.6, 9)
        ys = np.linspace(-0.9, 0.9, 7)
        for x in xs:
            P = np.stack([np.full(320, x), np.linspace(-0.92, 0.92, 320)], axis=1)
            px, py = self.to_px(P, collapse)
            beam.add(px, py, 0.075 * alpha)
        for y in ys:
            P = np.stack([np.linspace(-1.66, 1.66, 380), np.full(380, y)], axis=1)
            px, py = self.to_px(P, collapse)
            beam.add(px, py, 0.080 * alpha)
        # axes plus marques
        for P in (np.stack([np.linspace(-1.68, 1.68, 900), np.zeros(900)], axis=1),
                  np.stack([np.zeros(560), np.linspace(-0.95, 0.95, 560)], axis=1)):
            px, py = self.to_px(P, collapse)
            beam.add(px, py, 0.15 * alpha)

    def _trace(self, beam, pts_fn, t, weight, collapse, trail=7, dt=None, decay=0.62):
        """Trace un element mobile avec sa remanence (sous-pas temporels)."""
        dt = dt if dt is not None else 1.0 / self.fps
        w = weight
        for k in range(trail):
            P = pts_fn(t - k * dt * 0.85)
            if P is None or len(P) == 0:
                continue
            px, py = self.to_px(P, collapse)
            beam.add(px, py, w * (decay ** k))

    def _hud(self, beam, t, collapse):
        """Petits marquages type appareil de mesure."""
        a = smoothstep(0.15, 0.6, t) * (1.0 - smoothstep(self.tl.start("melt"), self.tl.end("melt"), t))
        if a <= 0.01:
            return
        blink = 0.45 + 0.55 * (math.sin(t * 9.0) > 0)
        for p in text_paths("OSC", 0.055, -1.62, 0.90, center=False, tag="hud"):
            px, py = self.to_px(p.P, collapse)
            beam.add(px, py, 0.55 * a)
        for p in text_paths("SCAN", 0.055, 1.30, 0.90, center=False, tag="hud"):
            px, py = self.to_px(p.P, collapse)
            beam.add(px, py, 0.55 * a * blink)

    # -- frame -------------------------------------------------------------

    def intensity(self, t):
        """Champ d'intensite du faisceau pour l'instant t."""
        tl = self.tl
        beam = Beam(self.H, self.W, self.gain)
        rng = np.random.default_rng(self.seed + int(t * self.fps + 0.5))

        # collapse CRT en fin de sequence
        collapse = 1.0
        u_out = tl.at("out", t)
        if u_out > 0:
            u = np.clip(u_out, 0, 1)
            collapse = max(0.006, (1.0 - ease_in_out(min(1.0, u / 0.62))) ** 1.6)

        # secousses sur les glitchs
        shake = 0.0
        for gt, gd in GLITCHES:
            gt *= self.dur / Timeline.REF
            if 0 <= t - gt < gd:
                shake = max(shake, 1.0 - (t - gt) / gd)

        # --------------------------------------------------- grille + HUD
        grid_a = (smoothstep(0.05, 0.55, t)
                  * (1.0 - 0.55 * smoothstep(tl.start("melt"), tl.end("title"), t)))
        self._grid(beam, t, grid_a * (1.0 if u_out <= 0 else max(0.0, 1 - u_out * 2)), collapse)
        if u_out <= 0:
            self._hud(beam, t, collapse)

        # --------------------------------------------------- 1. amorce
        if t < tl.end("sweep"):
            a = (1.0 - smoothstep(tl.start("sweep"), tl.start("sweep") + 0.35, t))
            if a > 0.01:
                n = 1600
                x = np.linspace(-1.75, 1.75, n)
                jitter = (rng.standard_normal(n) * 0.004
                          + 0.012 * np.sin(x * 40 + t * 30))
                base = smoothstep(0.0, 0.45, t)
                y = jitter * (1.0 + 6.0 * (1.0 - base))
                P = np.stack([x, y], axis=1)
                px, py = self.to_px(P, collapse)
                beam.add(px, py, 1.15 * a)

        # --------------------------------------------------- 2. balayage
        u_sweep = tl.at("sweep", t)
        sweep_x = -1.85 + 3.75 * ease_in_out(np.clip(u_sweep, 0, 1)) if u_sweep > 0 else -1.85
        drawn = 0.0
        if t >= tl.start("sweep"):
            drawn = np.clip(u_sweep, 0, 1)

        # signal de l'oscillateur : amplitude qui s'ecrase a mesure du trace
        if tl.start("boot") < t < tl.start("melt"):
            amp = 0.52 * (1.0 - ease_out(np.clip(u_sweep, 0, 1), 1.6)) + 0.028
            if t > tl.start("groove"):
                ug = tl.at("groove", t)
                amp = 0.028 + 0.10 * math.exp(-6.0 * (ug * 16 % 1.0))
            freq = 2.4 + 5.0 * np.clip(u_sweep, 0, 1)

            def osc(tt):
                y = amp * np.sin(freq * math.pi * WAVE_X + tt * 7.0)
                y += amp * 0.32 * np.sin(freq * 2.7 * math.pi * WAVE_X - tt * 4.0)
                return np.stack([WAVE_X, y * 0.85], axis=1)

            self._trace(beam, osc, t, 0.46 * (1.0 - 0.62 * smoothstep(tl.start("groove") - 0.25,
                                                                       tl.start("groove") + 0.35, t)),
                        collapse, trail=6, decay=0.55)

        # geometrie revelee par le balayage
        if drawn > 0:
            near = 0.075
            flash_pads = {}
            if t >= tl.start("groove"):
                ug = tl.at("groove", t)
                step_f = ug * len(PATTERN)
                for si in range(len(PATTERN)):
                    dt_step = (step_f - si) / len(PATTERN) * (tl.end("groove") - tl.start("groove"))
                    if 0 <= dt_step < 0.42:
                        pad, force = PATTERN[si]
                        v = force * math.exp(-7.0 * dt_step)
                        flash_pads[pad] = max(flash_pads.get(pad, 0.0), v)

            melt = np.clip(tl.at("melt", t), 0, 1)
            for p in self.mpc:
                mask = p.P[:, 0] <= sweep_x
                if not mask.any():
                    continue
                P = p.P[mask]
                w = np.full(len(P), 0.50)
                # front de trace : surbrillance juste derriere le balayage
                d = sweep_x - P[:, 0]
                w += 1.5 * np.exp(-(d / near) ** 2)
                if p.tag.startswith("pad"):
                    k = int(p.tag[3:])
                    if k in flash_pads:
                        w += 1.4 * flash_pads[k]
                if melt > 0:
                    w *= (1.0 - melt) ** 0.7
                if melt > 0:
                    P = self._melt(P, melt, t)
                px, py = self.to_px(P, collapse, (shake * rng.uniform(-9, 9), 0))
                beam.add(px, py, w)

            # pads allumes : remplissage
            if t >= tl.start("groove") and tl.at("melt", t) < 0.35:
                melt = np.clip(tl.at("melt", t), 0, 1)
                for k, v in flash_pads.items():
                    if v < 0.05:
                        continue
                    P = pad_fill(k // 4, k % 4)
                    if melt > 0:
                        P = self._melt(P, melt, t)
                    px, py = self.to_px(P, collapse)
                    beam.add(px, py, 0.9 * v * (1.0 - melt))

            # onde dans l'ecran LCD
            if t >= tl.start("sweep") + 1.4 and tl.at("melt", t) < 0.4:
                melt = np.clip(tl.at("melt", t), 0, 1)
                n = 260
                xs = np.linspace(-0.97, -0.41, n)
                env = 0.9
                if t >= tl.start("groove"):
                    ug = tl.at("groove", t) * len(PATTERN)
                    env = 0.5 + 0.9 * math.exp(-5.0 * (ug % 1.0))
                ys = 0.37 + 0.13 * env * np.sin(xs * 26 + t * 16) * np.sin(xs * 7 - t * 3)
                P = np.stack([xs, ys], axis=1)
                if melt > 0:
                    P = self._melt(P, melt, t)
                px, py = self.to_px(P, collapse)
                beam.add(px, py, 1.05 * (1.0 - melt))

        # tete de balayage
        if 0.0 < u_sweep < 1.02:
            n = 900
            ys = np.linspace(-0.97, 0.97, n)
            for k in range(5):
                xx = sweep_x - k * 0.030
                w = (0.42 if k else 1.35) * (0.62 ** k)
                jit = 0.004 * np.sin(ys * 60 + t * 40) if k == 0 else 0.0
                P = np.stack([np.full(n, xx) + jit, ys], axis=1)
                px, py = self.to_px(P, collapse)
                beam.add(px, py, w)
            # passes fantomes (retour de balayage)
            for gx, gw in ((sweep_x - 0.55, 0.16), (sweep_x - 1.05, 0.07)):
                if gx > -1.8:
                    P = np.stack([np.full(400, gx), np.linspace(-0.95, 0.95, 400)], axis=1)
                    px, py = self.to_px(P, collapse)
                    beam.add(px, py, gw)

        # --------------------------------------------------- 3. sinusoides
        wave_a = 0.0
        if t >= tl.start("melt"):
            wave_a = (smoothstep(tl.start("melt"), tl.start("melt") + 0.45, t)
                      * (1.0 - smoothstep(tl.start("title") + 0.35, tl.end("title"), t)))
        if wave_a > 0.01:
            amp = 0.16 * (0.7 + 0.3 * math.sin(t * 3.0))
            for b in range(3):
                self._trace(beam, lambda tt, b=b: wave_points(b, tt, amp), t,
                            0.55 * wave_a, collapse, trail=5, decay=0.5)

        # --------------------------------------------------- 4. titre
        u_ttl = tl.at("title", t)
        if u_ttl > -0.02 and u_out < 0.9:
            self._draw_title(beam, t, collapse, rng)

        return beam.render(), collapse, shake, rng

    def _melt(self, P, u, t):
        """Fait fondre des points de la MPC vers les sinusoides."""
        band = np.where(P[:, 1] > 0.22, 2, np.where(P[:, 1] < -0.22, 0, 1))
        amp = 0.16
        ty = np.zeros(len(P))
        for b in range(3):
            m = band == b
            if m.any():
                ty[m] = band_y(P[m, 0], b, t, amp)
        k = ease_in_out(u)
        out = P.copy()
        out[:, 1] = P[:, 1] * (1 - k) + ty * k
        out[:, 0] = P[:, 0] + 0.06 * k * np.sin(P[:, 1] * 9.0 + t * 3.0)
        return out

    def _draw_title(self, beam, t, collapse, rng):
        tl = self.tl
        u = np.clip(tl.at("title", t), 0, 1)
        g = self.title_g.astype(np.float64)
        k = ease_out(np.clip((u - g / 10.0 * 0.55) / 0.40, 0, 1), 3.0)   # decalage par lettre

        amp = 0.17
        sx = self.title_srcx
        sy = np.zeros(len(sx))
        for b in range(3):
            m = self.title_band == b
            sy[m] = band_y(sx[m], b, t, amp)

        P = np.empty_like(self.title_P)
        P[:, 0] = sx * (1 - k) + self.title_P[:, 0] * k
        P[:, 1] = sy * (1 - k) + self.title_P[:, 1] * k

        # les traits sont eux-memes faits d'une sinusoide (ondulation perpendiculaire)
        hold = max(0.0, t - tl.start("hold"))
        wob = 0.017 * (1 - k) * np.sin(self.title_s * 105.0 + t * 13.0 + g * 1.7)
        res = (0.0030 * math.exp(-0.9 * hold) + 0.0016) * (0.62 + 0.38 * math.sin(t * 2.1))
        wob += res * k * np.sin(self.title_s * 27.0 - t * 6.5 + g * 0.9)
        P = P + self.title_N * wob[:, None]

        w = 0.40 + 0.90 * k
        w = w + 1.7 * np.exp(-((k - 0.84) / 0.15) ** 2) * (u < 0.995)   # eclat de verrouillage
        if t > tl.start("hold"):
            sc = (t - tl.start("hold")) / 0.9
            if sc < 1.5:                                                # passage de lecture
                w = w * (1.0 + 1.2 * np.exp(-((P[:, 1] - (0.30 - 0.66 * sc)) / 0.042) ** 2))
            w = w * (1.0 + 0.05 * math.sin(t * 4.0))
        u_out = tl.at("out", t)
        if u_out > 0:
            w = w * max(0.0, 1.0 - u_out * 1.35)

        # epaisseur : trois passes decalees sur la normale -> trait "tube neon"
        th = (0.0054 * k)[:, None]
        for off, ow in ((0.0, 1.0), (1.0, 0.62), (-1.0, 0.62)):
            px, py = self.to_px(P + self.title_N * (off * th), collapse)
            beam.add(px, py, w * ow)

        # soulignement sinusoidal
        a = smoothstep(tl.start("title") + 0.9, tl.start("hold") + 0.25, t)
        if a > 0.01 and u_out <= 0.2:
            n = 1400
            xs = np.linspace(-1.10, 1.10, n)
            prog = smoothstep(tl.start("title") + 0.9, tl.start("hold") + 0.5, t)
            m = xs <= -1.10 + 2.20 * prog
            ys = -0.315 + 0.022 * np.sin(xs * 14.0 - t * 6.0)
            px, py = self.to_px(np.stack([xs[m], ys[m]], axis=1), collapse)
            beam.add(px, py, 0.75 * a)

    # -- colorisation + post ----------------------------------------------

    def colorize(self, field, t, collapse, shake, rng):
        W, H = self.W, self.H
        core = gauss(field, self.sigma)

        d4 = downsample(core, 4)
        g4 = gauss(d4, 2.6)
        d8 = downsample(core, 8)
        g8 = gauss(d8, 4.5)
        glow = upsample(g4, 4, (H, W)) * 2.6 + upsample(g8, 8, (H, W)) * 3.4

        inten = core * 1.15
        hot = np.clip(inten - 0.72, 0, None)

        img = np.zeros((H, W, 3), dtype=np.float32)
        base = np.clip(inten, 0, 1.6)
        for c in range(3):
            img[:, :, c] = VERT_FLUO[c] * base + VERT_HALO[c] * np.clip(glow, 0, 3.0) * 0.55
        white = np.clip(hot * 1.25, 0, 1.0) ** 1.25
        img += white[..., None] * np.float32([0.85, 1.0, 0.88])

        # scanlines + battement vertical lent
        yy = np.arange(H, dtype=np.float32)[:, None]
        period = max(2.0, H / 360.0)            # ~3 px en 1080p
        sl = 0.82 + 0.18 * (0.5 + 0.5 * np.cos(yy * (2.0 * math.pi / period)))
        roll = 1.0 + 0.05 * np.cos((yy / H + t * 0.16) * 2.0 * math.pi)
        img *= (sl * roll).astype(np.float32)[..., None]

        # vignette
        ny = (np.arange(H, dtype=np.float32)[:, None] / H - 0.5) * 2.0
        nx = (np.arange(W, dtype=np.float32)[None, :] / W - 0.5) * 2.0
        vig = np.clip(1.06 - 0.42 * (nx * nx * 0.55 + ny * ny), 0.0, 1.0) ** 1.15
        img *= vig[..., None]

        # grain
        gr = rng.standard_normal((H // 4, W // 4)).astype(np.float32)
        img += upsample(gr, 4, (H, W))[..., None] * 0.011

        # glitchs : tranches decalees + separation chromatique
        gl = 0.0
        for gt, gd in GLITCHES:
            gt *= self.dur / Timeline.REF
            if 0 <= t - gt < gd:
                gl = max(gl, 1.0 - (t - gt) / gd)
        if gl > 0.02:
            nb = int(3 + 9 * gl)
            for _ in range(nb):
                y0 = int(rng.integers(0, H - 4))
                hgt = int(rng.integers(3, max(6, int(H * 0.06))))
                y1 = min(H, y0 + hgt)
                off = int(rng.integers(-int(W * 0.05 * gl) - 2, int(W * 0.05 * gl) + 3))
                img[y0:y1] = np.roll(img[y0:y1], off, axis=1)
            sh = max(1, int(6 * gl))
            img[:, :, 0] = np.roll(img[:, :, 0], sh, axis=1)
            img[:, :, 2] = np.roll(img[:, :, 2], -sh, axis=1)

        if self.curve:
            img = self._warp(img)

        # fondu final
        u_out = self.tl.at("out", t)
        if u_out > 0:
            f = 1.0
            if u_out > 0.62:
                f = max(0.0, 1.0 - (u_out - 0.62) / 0.26)
            img *= f
            if 0.55 < u_out < 0.72:  # eclair du point central
                cy, cx = H // 2, W // 2
                r = max(2, int(H * 0.006))
                img[cy - r:cy + r, cx - int(r * 2.5):cx + int(r * 2.5)] += 1.4

        np.clip(img, 0.0, 1.0, out=img)
        img = img ** (1.0 / 1.06)
        return (img * 255.0 + 0.5).astype(np.uint8)

    def frame(self, i):
        t = i / self.fps
        field, collapse, shake, rng = self.intensity(t)
        return self.colorize(field, t, collapse, shake, rng)


# ==========================================================================
#  Bande son (synthese additive, sans dependance externe)
# ==========================================================================

def _tanh_limit(x, drive=1.25):
    return np.tanh(x * drive) / math.tanh(drive)


def _lowpass(x, width):
    k = max(1, int(width))
    if k <= 1:
        return x
    c = np.cumsum(np.concatenate([[0.0], x]))
    y = (c[k:] - c[:-k]) / k
    if len(y) < len(x):
        y = np.concatenate([y, np.full(len(x) - len(y), y[-1] if len(y) else 0.0)])
    return y


def synth_audio(duration, sr=48000, seed=3):
    """Sweep d'oscillateur -> groove MPC -> riser -> impact -> nappe."""
    tl = Timeline(duration)
    n = int(duration * sr) + 1
    out = np.zeros(n)
    rng = np.random.default_rng(seed)

    def add(sig, at, gain=1.0):
        i0 = max(0, int(at * sr))
        i1 = min(n, i0 + len(sig))
        if i1 > i0:
            out[i0:i1] += sig[:i1 - i0] * gain

    def seg(dur):
        return np.arange(max(1, int(dur * sr))) / sr

    # --- nappe grave continue
    t = np.arange(n) / sr
    bed_env = np.clip(smoothstep(0.0, 0.7, t), 0, 1) * (1.0 - smoothstep(tl.start("out"), duration, t))
    out += (0.10 * np.sin(2 * math.pi * 55 * t) + 0.05 * np.sin(2 * math.pi * 110 * t + 0.6)) * bed_env
    out += 0.012 * _lowpass(rng.standard_normal(n), 40) * bed_env

    # --- balayage de l'oscillateur
    s0, s1 = tl.start("sweep"), tl.end("sweep")
    ts = seg(s1 - s0)
    u = ts / max(1e-6, ts[-1])
    f = 70.0 * (2600.0 / 70.0) ** (u ** 1.35)
    ph = 2 * math.pi * np.cumsum(f) / sr
    swp = (np.sin(ph) + 0.30 * np.sin(2 * ph + 0.4) + 0.16 * np.sin(3 * ph))
    swp *= 0.20 * (0.35 + 0.65 * np.sin(math.pi * np.clip(u, 0, 1)) ** 0.6)
    swp *= (1.0 + 0.25 * np.sin(2 * math.pi * 5.5 * ts))
    add(swp, s0)

    # ticks de passage
    for k, at in enumerate((s0 + 0.02, s0 + (s1 - s0) * 0.42, s0 + (s1 - s0) * 0.78)):
        tb = seg(0.10)
        add(np.sin(2 * math.pi * (2100 - 400 * k) * tb) * np.exp(-tb * 46) * 0.16, at)

    # --- groove 16 pas
    g0, g1 = tl.start("groove"), tl.end("groove")
    stepd = (g1 - g0) / len(PATTERN)
    for i, (pad, force) in enumerate(PATTERN):
        at = g0 + i * stepd
        if pad in (0, 7):                                  # grosse caisse
            tb = seg(0.42)
            fk = 46 + 95 * np.exp(-tb * 24)
            k = np.sin(2 * math.pi * np.cumsum(fk) / sr) * np.exp(-tb * 7.5)
            k[:int(0.004 * sr)] += rng.standard_normal(int(0.004 * sr)) * 0.5
            add(k, at, 0.62 * force)
        elif pad in (5, 11, 14, 15):                       # caisse claire
            tb = seg(0.26)
            nz = rng.standard_normal(len(tb))
            body = nz - _lowpass(nz, 26)
            sn = body * np.exp(-tb * 17) + np.sin(2 * math.pi * 195 * tb) * np.exp(-tb * 26) * 0.5
            add(sn, at, 0.30 * force)
        else:                                              # charleston / perc
            tb = seg(0.09)
            nz = rng.standard_normal(len(tb))
            hh = (nz - _lowpass(nz, 7)) * np.exp(-tb * 62)
            add(hh, at, 0.18 * force)
        if i % 2 == 0:                                     # basse pulsee
            tb = seg(stepd * 1.8)
            add(np.sin(2 * math.pi * 82.4 * tb) * np.exp(-tb * 6.0), at, 0.16)

    # --- riser sur la dissolution
    m0, m1 = tl.start("melt"), tl.start("title") + 0.12
    tm = seg(m1 - m0)
    um = tm / max(1e-6, tm[-1])
    fr = 180.0 * (2400.0 / 180.0) ** um
    ris = np.sin(2 * math.pi * np.cumsum(fr) / sr) * 0.20 * um ** 1.4
    ris += np.sin(2 * math.pi * np.cumsum(fr * 0.5) / sr) * 0.12 * um ** 1.2
    nz = rng.standard_normal(len(tm))
    ris += (nz - _lowpass(nz, 30)) * 0.26 * um ** 2.0
    ris *= 1.0 - 0.55 * np.clip((um - 0.93) / 0.07, 0, 1)   # respiration avant l'impact
    add(ris, m0)

    # --- impact d'apparition du titre
    ti = seg(2.4)
    fi = 38 + 150 * np.exp(-ti * 13)
    imp = np.sin(2 * math.pi * np.cumsum(fi) / sr) * np.exp(-ti * 2.6) * 0.75
    nz = rng.standard_normal(len(ti))
    imp += _lowpass(nz, 12) * np.exp(-ti * 5.5) * 0.30
    add(imp, tl.start("title"))

    # --- nappe du titre (quinte)
    tp = seg(max(0.4, duration - tl.start("title") - 0.15))
    envp = np.clip(smoothstep(0.0, 0.55, tp), 0, 1) * np.exp(-tp * 0.55)
    pad_sig = (0.14 * np.sin(2 * math.pi * 110 * tp)
               + 0.10 * np.sin(2 * math.pi * 164.8 * tp + 1.1)
               + 0.06 * np.sin(2 * math.pi * 220 * tp + 2.0)
               + 0.03 * np.sin(2 * math.pi * 329.6 * tp))
    pad_sig *= envp * (1.0 + 0.10 * np.sin(2 * math.pi * 4.3 * tp))
    add(pad_sig, tl.start("title"))

    # --- extinction
    to = seg(0.55)
    off = (np.sin(2 * math.pi * (900 * np.exp(-to * 9) + 60) * to) * np.exp(-to * 12) * 0.35)
    nz = rng.standard_normal(len(to))
    off += _lowpass(nz, 8) * np.exp(-to * 16) * 0.25
    add(off, tl.start("out"))

    # --- mixage
    out = _tanh_limit(out * 0.92, 1.35)
    fade = np.clip(np.arange(n) / (0.05 * sr), 0, 1) * np.clip((n - np.arange(n)) / (0.10 * sr), 0, 1)
    out *= fade
    peak = float(np.max(np.abs(out))) or 1.0
    out *= 0.94 / peak

    # leger elargissement stereo
    d = int(0.0007 * sr)
    left = out.copy()
    right = np.concatenate([np.zeros(d), out[:-d]]) * 0.97 + out * 0.03
    st = np.stack([left, right], axis=1)
    return (np.clip(st, -1, 1) * 32767).astype("<i2")


def write_wav(path, data, sr=48000):
    with wave.open(path, "wb") as f:
        f.setnchannels(2)
        f.setsampwidth(2)
        f.setframerate(sr)
        f.writeframes(data.tobytes())


# ==========================================================================
#  Pipeline de rendu
# ==========================================================================

_R = None


def _worker(i):
    return _R.frame(i).tobytes()


def main():
    ap = argparse.ArgumentParser(description="Intro video OMNIPOTARD (oscilloscope vert fluo)")
    ap.add_argument("-o", "--out", default="omnipotard_intro.mp4")
    ap.add_argument("-W", "--width", type=int, default=1920)
    ap.add_argument("-H", "--height", type=int, default=1080)
    ap.add_argument("--fps", type=int, default=60)
    ap.add_argument("--duration", type=float, default=10.0)
    ap.add_argument("--crf", type=int, default=15)
    ap.add_argument("--jobs", type=int, default=os.cpu_count() or 2)
    ap.add_argument("--seed", type=int, default=7)
    ap.add_argument("--no-curve", action="store_true", help="desactive la courbure CRT")
    ap.add_argument("--no-audio", action="store_true")
    ap.add_argument("--stills", default="", help="dossier ou exporter des images cles PNG")
    ap.add_argument("--still-times", default="0.4,2.1,3.6,4.9,6.0,7.2,8.8,9.7")
    args = ap.parse_args()

    global _R
    _R = Renderer(args.width, args.height, args.fps, args.duration,
                  curve=not args.no_curve, seed=args.seed)

    if args.stills:
        from PIL import Image
        os.makedirs(args.stills, exist_ok=True)
        for ts in [float(x) for x in args.still_times.split(",") if x.strip()]:
            i = int(round(ts * args.fps))
            Image.fromarray(_R.frame(i)).save(os.path.join(args.stills, "t%05.2f.png" % ts))
            print("still %.2fs" % ts, flush=True)
        return

    nframes = int(round(args.duration * args.fps))
    tmpdir = tempfile.mkdtemp(prefix="omnipotard_")
    wav = None
    if not args.no_audio:
        wav = os.path.join(tmpdir, "omnipotard.wav")
        write_wav(wav, synth_audio(args.duration, seed=args.seed))
        print("audio -> %s" % wav, flush=True)

    cmd = ["ffmpeg", "-y", "-hide_banner", "-loglevel", "error",
           "-f", "rawvideo", "-pix_fmt", "rgb24",
           "-s", "%dx%d" % (args.width, args.height), "-r", str(args.fps), "-i", "-"]
    if wav:
        cmd += ["-i", wav]
    cmd += ["-c:v", "libx264", "-preset", "slow", "-crf", str(args.crf),
            "-pix_fmt", "yuv420p", "-profile:v", "high", "-movflags", "+faststart",
            "-x264-params", "keyint=%d" % (args.fps * 2)]
    if wav:
        cmd += ["-c:a", "aac", "-b:a", "192k", "-ac", "2", "-shortest"]
    cmd += [args.out]

    proc = subprocess.Popen(cmd, stdin=subprocess.PIPE)
    import time
    t0 = time.time()
    try:
        if args.jobs > 1:
            import multiprocessing as mp
            chunk = max(args.jobs, 24)
            with mp.get_context("fork").Pool(args.jobs) as pool:
                for start in range(0, nframes, chunk):
                    idx = range(start, min(nframes, start + chunk))
                    for buf in pool.map(_worker, idx, chunksize=1):
                        proc.stdin.write(buf)
                    done = min(nframes, start + chunk)
                    el = time.time() - t0
                    print("\r  %d/%d frames  %.0fs  (eta %.0fs)"
                          % (done, nframes, el, el / done * (nframes - done)),
                          end="", flush=True)
        else:
            for i in range(nframes):
                proc.stdin.write(_worker(i))
                if i % 10 == 0:
                    print("\r  %d/%d" % (i, nframes), end="", flush=True)
    finally:
        proc.stdin.close()
        proc.wait()
    print("\n%s  (%.1f s, %dx%d @ %dfps)" % (args.out, args.duration, args.width, args.height, args.fps))


if __name__ == "__main__":
    main()
