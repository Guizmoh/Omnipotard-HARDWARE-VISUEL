#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
OMNIPOTARD — generateur d'intro video "oscilloscope"

Sequence (12,8 s = 4 mesures a 75 BPM, tout est cale sur la grille musicale) :
  1. Amorce      : la trace se stabilise sur la ligne de base.
  2. Balayage    : un balayage d'oscillateur (vert fluo) dessine une MPC Live III.
                   Il se termine pile sur le drop, au debut de la mesure 2.
  3. Groove dub  : la machine joue — pads, bande de 16 pas, potards et ecran
                   bougent sur les evenements reels de la bande-son.
  4. Dissolution : la machine fond dans la forme d'onde de la musique.
  5. Titre       : la courbe audio ecrit OMNIPOTARD en un seul trait continu ;
                   le nom reste dedans, vibrant avec le son.
  6. Extinction  : collapse cathodique.

L'image est pilotee par le son : la courbe est la forme d'onde reelle du
morceau, et chaque pad s'allume sur l'evenement qui le declenche.

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

SR = 48000
DUREE_REF = 12.8                 # 4 mesures a 75 BPM


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
        self.gain = gain          # normalisation d'intensite selon la definition
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
        buf = np.bincount(np.concatenate(self.idx),
                          weights=np.concatenate(self.wts),
                          minlength=self.h * self.w)
        return buf.reshape(self.h, self.w).astype(np.float32)


# ==========================================================================
#  Geometrie : chemins echantillonnes a pas constant
# ==========================================================================

STEP = 0.0016  # pas d'echantillonnage (unites) -> ~0.9 px en 1080p


def resample(pts, step=STEP, closed=False):
    """Reechantillonne une polyligne a pas d'arc constant."""
    pts = np.asarray(pts, dtype=np.float64)
    if closed and not np.allclose(pts[0], pts[-1]):
        pts = np.vstack([pts, pts[0]])
    d = np.linalg.norm(np.diff(pts, axis=0), axis=1)
    s = np.concatenate([[0.0], np.cumsum(d)])
    length = float(s[-1])
    n = max(2, int(length / step))
    t = np.linspace(0.0, length, n)
    P = np.stack([np.interp(t, s, pts[:, 0]), np.interp(t, s, pts[:, 1])], axis=1)
    return P, t, length


class Path:
    """Chemin discretise : points + abscisse curviligne + etiquette."""

    __slots__ = ("P", "s", "tag")

    def __init__(self, pts, closed=False, tag="", step=STEP):
        self.P, self.s, _ = resample(pts, step, closed)
        self.tag = tag


def circle_pts(cx, cy, r, n=240):
    a = np.linspace(0.0, 2.0 * math.pi, n)
    return np.stack([cx + r * np.cos(a), cy + r * np.sin(a)], axis=1)


def rrect_pts(x0, y0, x1, y1, r, n=9):
    r = min(r, (x1 - x0) * 0.5, (y1 - y0) * 0.5)
    out = []
    for cx, cy, a0, a1 in ((x1 - r, y0 + r, -math.pi / 2, 0.0),
                           (x1 - r, y1 - r, 0.0, math.pi / 2),
                           (x0 + r, y1 - r, math.pi / 2, math.pi),
                           (x0 + r, y0 + r, math.pi, 1.5 * math.pi)):
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
    "E": (0.56, [[(.56, 1), (0, 1), (0, 0), (.56, 0)], [(0, .50), (.44, .50)]]),
    "L": (0.54, [[(0, 1), (0, 0), (.54, 0)]]),
    "V": (0.64, [[(0, 1), (.32, 0), (.64, 1)]]),
    "S": (0.60, [[(.60, .84), (.44, 1), (.16, 1), (0, .84), (0, .66), (.16, .50), (.44, .50), (.60, .34), (.60, .16), (.44, 0), (.16, 0), (0, .16)]]),
    "U": (0.62, [[(0, 1), (0, .18), (.18, 0), (.44, 0), (.62, .18), (.62, 1)]]),
    "Y": (0.62, [[(0, 1), (.31, .55), (.62, 1)], [(.31, .55), (.31, 0)]]),
    " ": (0.30, []),
}

TRACKING = 0.145


def text_width(txt):
    return sum(GLYPHS[c][0] for c in txt) + TRACKING * max(0, len(txt) - 1)


def glyph_strokes(txt, height, x0, y0, center=True):
    """Traits du texte, positionnes : liste de (indice_lettre, points)."""
    x = x0 - text_width(txt) * height * 0.5 if center else x0
    out = []
    for gi, ch in enumerate(txt):
        gw, strokes = GLYPHS[ch]
        for st in strokes:
            out.append((gi, [(x + px * height, y0 + py * height) for px, py in st]))
        x += (gw + TRACKING) * height
    return out


def text_paths(txt, height, x0, y0, step=STEP, center=True, tag="txt"):
    return [Path(pts, tag="%s:%d" % (tag, gi), step=step)
            for gi, pts in glyph_strokes(txt, height, x0, y0, center)]


# ==========================================================================
#  La machine : MPC Live III
#  Silhouette : bande de 16 pas sur l'arete haute, ecran 7" a gauche,
#  4 Q-Links surmontes de leurs bandeaux, molette encastree en haut a droite,
#  grille 4x4 en bas a droite, touch strip vertical le long des pads.
# ==========================================================================

# Disposition (unites = demi-hauteur d'image). La machine occupe presque toute
# la largeur du cadre : les pads sont gros et biseautes, l'ecran large, la
# molette franche — c'est ce trio qui fait lire "MPC" au premier coup d'oeil.
BODY = (-1.253, -0.853, 1.253, 0.853)
BODY_IN = (-1.188, -0.788, 1.188, 0.788)
SCREEN = (-1.123, -0.022, -0.130, 0.589)          # dalle tactile 7" (16/10)
PAD_X0, PAD_Y0, PAD_SZ, PAD_GAP = 0.140, -0.756, 0.2262, 0.0303
STEP_X0, STEP_Y0, STEP_W, STEP_H, STEP_GAP = -1.123, 0.632, 0.1142, 0.086, 0.028
QLINK = [(0.108, 0.432), (0.324, 0.432), (0.540, 0.432), (0.756, 0.432)]
QLINK_R = 0.0626
QDISP_W, QDISP_Y0, QDISP_Y1 = 0.184, 0.513, 0.589
WHEEL, WHEEL_R = (1.021, 0.432), 0.135
STRIP = (-0.022, -0.756, 0.098, 0.236)            # touch strip vertical
BTN_ROWS = ((-0.173, 5, 0.140), (-0.324, 5, 0.140), (-0.486, 4, 0.189))
BTN_X0, BTN_SPAN = -1.123, 0.972


def pad_rect(i, j):
    """i = ligne (0 = bas), j = colonne (0 = gauche)."""
    x0 = PAD_X0 + j * (PAD_SZ + PAD_GAP)
    y0 = PAD_Y0 + i * (PAD_SZ + PAD_GAP)
    return x0, y0, x0 + PAD_SZ, y0 + PAD_SZ


def step_rect(k):
    x0 = STEP_X0 + k * (STEP_W + STEP_GAP)
    return x0, STEP_Y0, x0 + STEP_W, STEP_Y0 + STEP_H


def build_mpc(step=STEP):
    """MPC Live III : bande de 16 pas en haut, ecran 7" a gauche, Q-Links et
    molette a droite, grille 4x4 biseautee en bas a droite, touch strip."""
    P = []
    add = P.append

    add(Path(rrect_pts(*BODY, r=0.081), closed=True, tag="body", step=step))
    add(Path(rrect_pts(*BODY_IN, r=0.059), closed=True, tag="body", step=step))

    # bande de 16 boutons de step-sequenceur (arete haute)
    for k in range(16):
        add(Path(rrect_pts(*step_rect(k), r=0.014), closed=True, tag="step%d" % k, step=step))

    # ecran + cadre
    sx0, sy0, sx1, sy1 = SCREEN
    add(Path(rrect_pts(sx0, sy0, sx1, sy1, 0.020), closed=True, tag="lcd", step=step))
    add(Path(rrect_pts(sx0 + 0.030, sy0 + 0.030, sx1 - 0.030, sy1 - 0.030, 0.012),
             closed=True, tag="lcd", step=step))
    add(Path([(sx0 + 0.030, sy1 - 0.128), (sx1 - 0.030, sy1 - 0.128)], tag="lcd", step=step))

    # Q-Links et leurs bandeaux
    for k, (cx, cy) in enumerate(QLINK):
        add(Path(circle_pts(cx, cy, QLINK_R), closed=True, tag="qlink%d" % k, step=step))
        add(Path(circle_pts(cx, cy, QLINK_R * 0.30), closed=True, tag="qlink%d" % k, step=step))
        add(Path(rrect_pts(cx - QDISP_W * 0.5, QDISP_Y0, cx + QDISP_W * 0.5, QDISP_Y1, 0.011),
                 closed=True, tag="qdisp%d" % k, step=step))

    # molette encastree
    add(Path(circle_pts(WHEEL[0], WHEEL[1], WHEEL_R), closed=True, tag="wheel", step=step))
    add(Path(circle_pts(WHEEL[0], WHEEL[1], WHEEL_R * 0.72), closed=True, tag="wheel", step=step))
    add(Path(circle_pts(WHEEL[0], WHEEL[1], WHEEL_R * 0.30), closed=True, tag="wheel", step=step))

    # touch strip
    add(Path(rrect_pts(*STRIP, r=0.048), closed=True, tag="strip", step=step))

    # 16 pads : contour + biseau interieur (c'est ce relief qui fait la MPC)
    for i in range(4):
        for j in range(4):
            x0, y0, x1, y1 = pad_rect(i, j)
            k = i * 4 + j
            add(Path(rrect_pts(x0, y0, x1, y1, 0.034), closed=True, tag="pad%d" % k, step=step))
            add(Path(rrect_pts(x0 + 0.024, y0 + 0.024, x1 - 0.024, y1 - 0.024, 0.024),
                     closed=True, tag="pad%d" % k, step=step))

    # touches et transport sous l'ecran
    for row, (yy, nb, w) in enumerate(BTN_ROWS):
        gap = (BTN_SPAN - nb * w) / (nb - 1.0)
        for k in range(nb):
            x0 = BTN_X0 + k * (w + gap)
            add(Path(rrect_pts(x0, yy, x0 + w, yy + (0.086 if row < 2 else 0.096), 0.016),
                     closed=True, tag="btn%d" % (row * 5 + k), step=step))

    P += text_paths("MPC LIVE", 0.097, BTN_X0, -0.713, step=step, center=False, tag="logo")
    return P


def pad_fill(k, nlines=8):
    x0, y0, x1, y1 = pad_rect(k // 4, k % 4)
    m = 0.040
    return np.vstack([np.stack([np.linspace(x0 + m, x1 - m, 56), np.full(56, y)], axis=1)
                      for y in np.linspace(y0 + m, y1 - m, nlines)])


def rect_fill(x0, y0, x1, y1, nlines=4, m=0.008):
    return np.vstack([np.stack([np.linspace(x0 + m, x1 - m, 24), np.full(24, y)], axis=1)
                      for y in np.linspace(y0 + m, y1 - m, nlines)])


# ==========================================================================
#  La courbe du titre : un seul trait continu, de la ligne de base au mot
# ==========================================================================

ONDE, TRAIT, TRANSIT = 0, 1, 2


def build_title_curve(txt, height, y0, x_in=-1.88, x_out=1.88, step=STEP):
    """Ligne de base -> lettres tracees d'un seul trait -> ligne de base.

    Renvoie (P, kind, s, longueur). `kind` distingue l'onde, les traits de
    lettres et les sauts de faisceau (traces en faible intensite, comme le
    retour de spot d'un oscilloscope).
    """
    strokes = glyph_strokes(txt, height, 0.0, y0)
    first = strokes[0][1][0]
    last = strokes[-1][1][-1]
    segs = [([(x_in, 0.0), (first[0] - 0.10, 0.0)], ONDE),
            ([(first[0] - 0.10, 0.0), first], TRANSIT)]
    prev = None
    for _, pts in strokes:
        if prev is not None:
            segs.append(([prev, pts[0]], TRANSIT))
        segs.append((pts, TRAIT))
        prev = pts[-1]
    segs.append(([last, (last[0] + 0.10, 0.0)], TRANSIT))
    segs.append(([(last[0] + 0.10, 0.0), (x_out, 0.0)], ONDE))

    Ps, Ns, ks, ss, off = [], [], [], [], 0.0
    for pts, kind in segs:
        p, s, length = resample(pts, step)
        tan = np.gradient(p, axis=0)
        tan /= (np.linalg.norm(tan, axis=1, keepdims=True) + 1e-12)
        Ps.append(p)
        Ns.append(np.stack([-tan[:, 1], tan[:, 0]], axis=1))
        ks.append(np.full(len(p), kind, dtype=np.int8))
        ss.append(s + off)
        off += length
    return (np.vstack(Ps), np.vstack(Ns), np.concatenate(ks),
            np.concatenate(ss), off)


# ==========================================================================
#  Bande son : dub ambient, synthese additive (aucune dependance externe)
#  4 mesures : nappe -> drop et groove dub -> break -> impact et ambient.
#  Chaque evenement rythmique renvoie aussi le pad qu'il allume a l'image.
# ==========================================================================

NOTES = {"G1": 49.00, "A1": 55.00, "C2": 65.41, "D2": 73.42, "E2": 82.41,
         "A2": 110.0, "C3": 130.8, "E3": 164.8, "G3": 196.0, "B3": 246.9,
         "A3": 220.0, "C4": 261.6, "E4": 329.6}

# motif de 16 pas, joue deux fois (dub : one drop, skank sur les contretemps)
KICKS = (0, 8)
RIMS = (8,)
HATS = (4, 12)
PERCS = (11,)
SKANKS = (2, 6, 10, 14)
BASSLINE = (((0, "A1", 3), (6, "A1", 2), (10, "C2", 2), (13, "E2", 3)),
            ((0, "A1", 3), (6, "G1", 2), (10, "A1", 2), (14, "C2", 2)))

# pad allume par famille d'evenement (grille 4x4, 0 = en bas a gauche)
PAD_OF = {"kick": 0, "rim": 5, "hat": 10, "perc": 6}
PAD_BASS = {"A1": 1, "G1": 1, "C2": 2, "D2": 2, "E2": 3}
PAD_SKANK = (12, 13, 14, 15)
DECAY_OF = {"kick": 5.5, "rim": 9.0, "hat": 15.0, "perc": 13.0,
            "bass": 4.5, "skank": 6.5}


def _lowpass(x, width):
    k = max(1, int(width))
    if k <= 1:
        return x
    c = np.cumsum(np.concatenate([[0.0], x]))
    y = (c[k:] - c[:-k]) / k
    if len(y) < len(x):
        y = np.concatenate([y, np.full(len(x) - len(y), y[-1] if len(y) else 0.0)])
    return y


def _highpass(x, width):
    return x - _lowpass(x, width)


def _tanh_limit(x, drive=1.3):
    return np.tanh(x * drive) / math.tanh(drive)


def _tape_echo(x, delay, sr, fb=0.52, taps=7, damp=9):
    """Echo a bande : chaque repetition est un peu plus sourde."""
    y = np.zeros_like(x)
    d = max(1, int(delay * sr))
    cur = x
    for k in range(1, taps + 1):
        cur = _lowpass(cur, damp)
        g = fb ** k
        if g < 0.012 or d * k >= len(x):
            break
        y[d * k:] += cur[:len(x) - d * k] * g
    return y


def _reverb_ir(sr, dur=2.6, decay=1.15, seed=5):
    n = int(dur * sr)
    t = np.arange(n) / sr
    rng = np.random.default_rng(seed)
    ir = rng.standard_normal(n) * np.exp(-t / decay)
    ir = _lowpass(ir, 7)
    a = int(0.015 * sr)
    ir[:a] *= np.linspace(0.0, 1.0, a)
    return ir / (np.sqrt((ir ** 2).sum()) + 1e-9)


def _fft_conv(x, h):
    n = len(x) + len(h) - 1
    N = 1 << (n - 1).bit_length()
    return np.fft.irfft(np.fft.rfft(x, N) * np.fft.rfft(h, N), N)[:len(x)]


def synth_audio(duration=DUREE_REF, sr=SR, seed=3):
    """Renvoie {'stereo', 'mono', 'events', 'sr'} — dub ambient en 4 mesures."""
    bar = duration / 4.0
    beat = bar / 4.0
    six = beat / 4.0
    n = int(duration * sr) + 1
    rng = np.random.default_rng(seed)

    dry = np.zeros(n)      # direct
    ech = np.zeros(n)      # depart echo a bande
    rev = np.zeros(n)      # depart reverb
    events = []

    def seg(d):
        return np.arange(max(1, int(d * sr))) / sr

    def add(buf, sig, at, gain=1.0):
        i0 = max(0, int(at * sr))
        i1 = min(n, i0 + len(sig))
        if i1 > i0:
            buf[i0:i1] += sig[:i1 - i0] * gain

    def fire(at, kind, pad, force):
        if at < duration:
            events.append((float(at), int(pad), float(force), DECAY_OF[kind]))

    # ---------------------------------------------------------------- voix
    def kick(at, f=1.0):
        t = seg(0.60)
        fr = 42 + 95 * np.exp(-t * 20)
        s = np.sin(2 * math.pi * np.cumsum(fr) / sr) * np.exp(-t * 5.0)
        s[:int(0.004 * sr)] += rng.standard_normal(int(0.004 * sr)) * 0.35
        add(dry, s, at, 0.80 * f)
        add(rev, s, at, 0.10 * f)
        fire(at, "kick", PAD_OF["kick"], f)

    def rim(at, f=1.0):
        t = seg(0.20)
        nz = _highpass(rng.standard_normal(len(t)), 5)
        s = (nz * np.exp(-t * 42) * 0.7
             + np.sin(2 * math.pi * 880 * t) * np.exp(-t * 55) * 0.5
             + np.sin(2 * math.pi * 1720 * t) * np.exp(-t * 70) * 0.25)
        add(dry, s, at, 0.34 * f)
        add(ech, s, at, 0.85 * f)     # le rim part dans l'echo : signature dub
        add(rev, s, at, 0.45 * f)
        fire(at, "rim", PAD_OF["rim"], f)

    def hat(at, f=1.0):
        t = seg(0.10)
        s = _highpass(rng.standard_normal(len(t)), 3) * np.exp(-t * 58)
        add(dry, s, at, 0.13 * f)
        add(rev, s, at, 0.10 * f)
        fire(at, "hat", PAD_OF["hat"], f)

    def perc(at, f=1.0):
        t = seg(0.16)
        s = _highpass(rng.standard_normal(len(t)), 9) * np.exp(-t * 26)
        add(dry, s, at, 0.10 * f)
        add(ech, s, at, 0.35 * f)
        fire(at, "perc", PAD_OF["perc"], f)

    def skank(at, k, f=1.0):
        """Accord bref sur le contretemps, envoye dans l'echo."""
        t = seg(0.30)
        env = np.exp(-t * 16) * np.minimum(t / 0.004, 1.0)
        s = np.zeros(len(t))
        for h, g in ((1.0, 1.0), (2.0, 0.42), (3.0, 0.20), (4.0, 0.10)):
            for f0, gg in ((NOTES["A3"], 1.0), (NOTES["C4"], 0.85), (NOTES["E4"], 0.7)):
                s += np.sin(2 * math.pi * f0 * h * t) * g * gg
        s = _lowpass(s * env, 4) * 0.09
        add(dry, s, at, 0.55 * f)
        add(ech, s, at, 1.0 * f)
        add(rev, s, at, 0.35 * f)
        fire(at, "skank", PAD_SKANK[k % 4], f)

    def bass(at, name, dur, f=1.0):
        t = seg(dur)
        f0 = NOTES[name]
        env = np.minimum(t / 0.012, 1.0) * np.exp(-t * 2.2)
        env *= np.clip((dur - t) / 0.08, 0, 1)
        s = (np.sin(2 * math.pi * f0 * t) + 0.22 * np.sin(4 * math.pi * f0 * t)) * env
        add(dry, _tanh_limit(s * 0.8, 1.6), at, 0.55 * f)
        fire(at, "bass", PAD_BASS[name], 0.75 * f)

    # ------------------------------------------------- mesure 1 : ouverture
    t_all = np.arange(n) / sr
    drone = (0.085 * np.sin(2 * math.pi * 55 * t_all)
             + 0.045 * np.sin(2 * math.pi * 110 * t_all + 0.7)
             + 0.020 * np.sin(2 * math.pi * 82.41 * t_all + 1.9))
    drone *= np.clip(smoothstep(0.0, 1.2, t_all), 0, 1) * (1.0 - smoothstep(duration - 0.9, duration, t_all))
    dry += drone

    # souffle du balayage : montee filtree qui se resout sur le drop
    sw = seg(bar - 0.15)
    us = sw / sw[-1]
    fr = 90.0 * (1900.0 / 90.0) ** (us ** 1.5)
    swp = np.sin(2 * math.pi * np.cumsum(fr) / sr) * 0.11 * us ** 1.2
    swp += _highpass(rng.standard_normal(len(sw)), 40) * 0.05 * us ** 2.4
    swp *= 1.0 - 0.7 * np.clip((us - 0.9) / 0.1, 0, 1)
    add(dry, swp, 0.15)
    add(rev, swp * 0.5, 0.15)

    # ------------------------------------------- mesures 2-3 : groove dub
    g0 = bar                       # le drop tombe pile a la fin du balayage
    n_steps = 24                   # 6 temps de groove
    sk = 0
    for s_i in range(n_steps):
        at = g0 + s_i * six
        k = s_i % 16
        b = (s_i // 16) % 2
        if k in KICKS:
            kick(at, 1.0 if k == 0 else 0.85)
        if k in RIMS:
            rim(at, 0.95)
        if k in HATS:
            hat(at, 0.6)
        if k in PERCS or (b == 1 and k == 3):
            perc(at, 0.55)
        if k in SKANKS:
            skank(at, sk, 0.9 if k in (2, 10) else 0.7)
            sk += 1
        for st, name, dur in BASSLINE[b]:
            if st == k:
                bass(at, name, dur * six * 0.95)

    # ------------------------------------------------ mesure 3.5 : break
    b0 = g0 + n_steps * six        # tout se retire, il ne reste que les echos
    tb = seg(2.0 * beat)
    ub = tb / tb[-1]
    ris = np.sin(2 * math.pi * np.cumsum(150 * (2400 / 150.0) ** ub) / sr) * 0.10 * ub ** 1.8
    ris += _highpass(rng.standard_normal(len(tb)), 25) * 0.14 * ub ** 2.4
    ris *= 1.0 - 0.75 * np.clip((ub - 0.9) / 0.1, 0, 1)
    add(dry, ris, b0)
    add(rev, ris * 0.6, b0)
    skank(b0, sk, 0.8)             # dernier skank, jete dans l'echo
    rim(b0 + 2 * six, 0.7)

    # -------------------------------------- mesure 4 : impact puis ambient
    t0 = 3.0 * bar                 # debut de la mesure 4 = apparition du titre
    ti = seg(3.0)
    fi = 34 + 130 * np.exp(-ti * 11)
    imp = np.sin(2 * math.pi * np.cumsum(fi) / sr) * np.exp(-ti * 2.2) * 0.85
    imp += _lowpass(rng.standard_normal(len(ti)), 10) * np.exp(-ti * 4.5) * 0.25
    add(dry, imp, t0)
    add(rev, imp * 0.35, t0)

    tp = seg(max(0.5, duration - t0))
    envp = np.clip(smoothstep(0.0, 0.9, tp), 0, 1) * np.exp(-tp * 0.30)
    envp *= 1.0 - smoothstep(duration - t0 - 0.55, duration - t0, tp)
    chord = np.zeros(len(tp))
    for name, g in (("A2", 1.0), ("C3", 0.75), ("E3", 0.62), ("G3", 0.45), ("B3", 0.30)):
        f0 = NOTES[name]
        chord += g * (np.sin(2 * math.pi * f0 * tp + f0)
                      + 0.25 * np.sin(2 * math.pi * f0 * 2 * tp))
    chord *= envp * 0.055 * (1.0 + 0.12 * np.sin(2 * math.pi * 0.35 * tp))
    add(dry, chord, t0, 0.9)
    add(rev, chord, t0, 1.1)
    add(dry, np.sin(2 * math.pi * 55 * tp) * envp * 0.10, t0)

    # echos residuels du skank pendant l'ambient
    for k in range(3):
        skank(t0 + (2 + 3 * k) * six, sk + k, 0.30 - 0.07 * k)

    # extinction
    to = seg(0.6)
    off = np.sin(2 * math.pi * (760 * np.exp(-to * 10) + 55) * to) * np.exp(-to * 11) * 0.30
    off += _lowpass(rng.standard_normal(len(to)), 9) * np.exp(-to * 15) * 0.22
    add(dry, off, duration - 0.42)

    # ------------------------------------------------------------- mixage
    mix = dry + _tape_echo(ech, beat * 0.75, sr, fb=0.54, taps=8) * 0.55
    mix += _fft_conv(rev, _reverb_ir(sr)) * 0.42
    mix = _tanh_limit(mix * 0.95, 1.4)
    fade = np.clip(np.arange(n) / (0.04 * sr), 0, 1) * np.clip((n - np.arange(n)) / (0.10 * sr), 0, 1)
    mix *= fade
    mix /= (np.max(np.abs(mix)) or 1.0) / 0.94

    d = int(0.0009 * sr)
    right = np.concatenate([np.zeros(d), mix[:-d]]) * 0.96 + mix * 0.04
    st = np.stack([mix, right], axis=1)
    events.sort()
    return {"stereo": (np.clip(st, -1, 1) * 32767).astype("<i2"),
            "mono": mix.astype(np.float32), "events": events, "sr": sr}


def write_wav(path, data, sr=SR):
    with wave.open(path, "wb") as f:
        f.setnchannels(2)
        f.setsampwidth(2)
        f.setframerate(sr)
        f.writeframes(data.tobytes())


# ==========================================================================
#  Scenario — cale sur la grille musicale (mesure = duree / 4)
# ==========================================================================

class Timeline:
    REF = DUREE_REF
    KEYS = [
        ("boot", 0.00, 0.90),
        ("sweep", 0.90, 3.20),     # se termine sur le drop (mesure 2)
        ("groove", 3.20, 8.00),
        ("melt", 8.00, 9.60),
        ("title", 9.60, 11.20),    # l'impact tombe sur la mesure 4
        ("hold", 11.20, 12.40),
        ("out", 12.40, 12.80),
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


GLITCHES = [(3.18, .10), (5.60, .06), (7.98, .12), (9.58, .11), (11.22, .06)]


# ==========================================================================
#  Rendu
# ==========================================================================

TITLE_H = 0.27
CURVE_AMP = 0.28
CURVE_WIN = 0.070          # fenetre d'analyse affichee (s) — "base de temps"
MOD_OF = {ONDE: 1.0, TRAIT: 0.055, TRANSIT: 0.30}
WEIGHT_OF = {ONDE: 0.80, TRAIT: 1.15, TRANSIT: 0.07}
THICK_OF = {ONDE: 0.0028, TRAIT: 0.0052, TRANSIT: 0.0}

# pas programmes dans le motif : ils restent faiblement allumes
STEP_LIT = frozenset(KICKS + RIMS + HATS + PERCS + SKANKS)


class Renderer:
    def __init__(self, w, h, fps, duration, audio, curve=True, seed=7):
        self.W, self.H = w, h
        self.fps = fps
        self.dur = duration
        self.tl = Timeline(duration)
        self.curve = curve
        self.seed = seed
        self.bar = duration / 4.0
        self.six = self.bar / 16.0

        # la machine reste cadree quel que soit le format (16/9, carre, vertical)
        self.scale = min(h * 0.5, w * 0.5 / 1.30)
        self.sigma = max(0.60, h / 1080.0 * 0.95)
        # un trait garde la meme luminosite quelle que soit la definition
        self.gain = (self.scale * self.sigma) / (360.0 * 0.6333)

        # ---- son : forme d'onde affichee, enveloppes, evenements
        sr = audio["sr"]
        self.sr = sr
        mono = audio["mono"].astype(np.float64)
        wave = _lowpass(mono, 56)                     # ce que "voit" l'ecran
        self.wave = (wave / (np.max(np.abs(wave)) or 1.0)).astype(np.float32)
        self.nw = len(self.wave)
        low = _lowpass(mono, 26)
        self.eh = 240.0                               # resolution des enveloppes
        stepi = max(1, int(sr / self.eh))
        def env(x, width):
            e = _lowpass(np.abs(x), int(sr * width))[::stepi]
            return (e / (np.max(e) or 1.0)).astype(np.float32)
        self.e_full = env(mono, 0.030)
        self.e_low = env(low, 0.045)
        self.e_high = env(mono - low, 0.012)
        ev = audio["events"]
        self.ev_t = np.array([e[0] for e in ev], dtype=np.float64)
        self.ev_pad = np.array([e[1] for e in ev], dtype=np.int32)
        self.ev_f = np.array([e[2] for e in ev], dtype=np.float64)
        self.ev_d = np.array([e[3] for e in ev], dtype=np.float64)

        # ---- geometrie
        self.mpc = build_mpc()
        (self.tP, self.tN, self.tkind,
         self.ts, self.tlen) = build_title_curve("OMNIPOTARD", TITLE_H, -TITLE_H * 0.5)
        self.tmod = np.array([MOD_OF[int(k)] for k in self.tkind])
        self.tw = np.array([WEIGHT_OF[int(k)] for k in self.tkind])
        self.tth = np.array([THICK_OF[int(k)] for k in self.tkind])

        if curve:
            self._build_warp()

    # -- son ---------------------------------------------------------------

    def env_at(self, arr, t):
        i = int(np.clip(t * self.eh, 0, len(arr) - 1))
        return float(arr[i])

    def wave_y(self, x, t, amp=CURVE_AMP, win=CURVE_WIN, agc=True):
        """Forme d'onde du morceau, etalee sur la largeur de l'ecran.

        Le gain suit l'inverse de l'enveloppe (comme le calibre automatique
        d'un oscilloscope) : les passages calmes restent lisibles.
        """
        if agc:
            amp = amp * float(np.clip(0.55 / (0.20 + self.env_at(self.e_full, t)), 0.80, 1.60))
        tt = t + (np.asarray(x) / 1.88) * (win * 0.5)
        i = tt * self.sr
        i0 = np.floor(i).astype(np.int64)
        f = i - i0
        i0 = np.clip(i0, 0, self.nw - 2)
        return amp * (self.wave[i0] * (1.0 - f) + self.wave[i0 + 1] * f)

    def pad_flashes(self, t):
        dt = t - self.ev_t
        m = (dt >= 0.0) & (dt < 1.6)
        out = {}
        if not np.any(m):
            return out
        v = self.ev_f[m] * np.exp(-self.ev_d[m] * dt[m])
        for pad, val in zip(self.ev_pad[m], v):
            if val > 0.02:
                out[int(pad)] = max(out.get(int(pad), 0.0), float(val))
        return out

    def step_index(self, t):
        return int((t - self.tl.start("groove")) / self.six) % 16

    # -- geometrie ecran ---------------------------------------------------

    def to_px(self, P, collapse=1.0, shake=(0.0, 0.0)):
        s = self.scale
        return (self.W * 0.5 + P[:, 0] * s + shake[0],
                self.H * 0.5 - P[:, 1] * s * collapse + shake[1])

    def _build_warp(self):
        W, H = self.W, self.H
        yy, xx = np.mgrid[0:H, 0:W]
        nx = (xx / (W - 1.0)) * 2.0 - 1.0
        ny = (yy / (H - 1.0)) * 2.0 - 1.0
        f = 1.0 + 0.055 * (nx * nx + ny * ny)
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
        fx, fy = self.wfx, self.wfy
        top = flat[self.wi00].reshape(H, W, 3) * (1 - fx) + flat[self.wi01].reshape(H, W, 3) * fx
        bot = flat[self.wi10].reshape(H, W, 3) * (1 - fx) + flat[self.wi11].reshape(H, W, 3) * fx
        return (top * (1 - fy) + bot * fy) * self.wmask

    # -- couches -----------------------------------------------------------

    def _grid(self, beam, t, alpha, collapse):
        if alpha <= 0.003:
            return
        for x in np.linspace(-1.6, 1.6, 9):
            P = np.stack([np.full(320, x), np.linspace(-0.92, 0.92, 320)], axis=1)
            px, py = self.to_px(P, collapse)
            beam.add(px, py, 0.075 * alpha)
        for y in np.linspace(-0.9, 0.9, 7):
            P = np.stack([np.linspace(-1.66, 1.66, 380), np.full(380, y)], axis=1)
            px, py = self.to_px(P, collapse)
            beam.add(px, py, 0.080 * alpha)
        for P in (np.stack([np.linspace(-1.68, 1.68, 900), np.zeros(900)], axis=1),
                  np.stack([np.zeros(560), np.linspace(-0.95, 0.95, 560)], axis=1)):
            px, py = self.to_px(P, collapse)
            beam.add(px, py, 0.15 * alpha)

    def _hud(self, beam, t, collapse, alpha):
        if alpha <= 0.01:
            return
        blink = 0.45 + 0.55 * (math.sin(t * 9.0) > 0)
        for p in text_paths("OSC", 0.055, -1.66, 0.90, center=False):
            px, py = self.to_px(p.P, collapse)
            beam.add(px, py, 0.55 * alpha)
        for p in text_paths("SYNC", 0.055, 1.34, 0.90, center=False):
            px, py = self.to_px(p.P, collapse)
            beam.add(px, py, 0.55 * alpha * blink)

    def _dyn(self, beam, P, w, collapse, melt, t):
        """Couche animee (organes qui bougent), soumise a la dissolution."""
        if melt >= 0.99:
            return
        if melt > 0:
            P = self._melt(P, melt, t)
            w = w * (1.0 - melt)
        px, py = self.to_px(P, collapse)
        beam.add(px, py, w)

    def _melt(self, P, u, t):
        """La machine fond dans la forme d'onde du morceau."""
        k = ease_in_out(u)
        out = P.copy()
        out[:, 1] = P[:, 1] * (1.0 - k) + self.wave_y(P[:, 0], t) * k
        out[:, 0] = P[:, 0] + 0.05 * k * np.sin(P[:, 1] * 9.0 + t * 3.0)
        return out

    def _wave_line(self, beam, t, collapse, alpha, xf=None, thick=True):
        """La courbe du morceau. Passe le front `xf` : elle s'efface derriere."""
        if alpha <= 0.01:
            return
        xs = np.linspace(-1.88, 1.88, 2600)
        a = np.full(len(xs), float(alpha))
        if xf is not None:
            a *= smoothstep(xf - 0.16, xf + 0.02, xs)
        P = np.stack([xs, self.wave_y(xs, t)], axis=1)
        px, py = self.to_px(P, collapse)
        beam.add(px, py, 0.85 * a)
        if thick:
            for dy in (0.0035, -0.0035):
                px, py = self.to_px(P + np.array([0.0, dy]), collapse)
                beam.add(px, py, 0.35 * a)

    def _machine(self, beam, t, collapse, sweep_x, melt, rng, shake):
        """La MPC Live III : trace revele par le balayage, organes pilotes par le son."""
        tl = self.tl
        live = t >= tl.start("groove") - 0.05
        flashes = self.pad_flashes(t) if live else {}
        e_low = self.env_at(self.e_low, t) if live else 0.0
        e_high = self.env_at(self.e_high, t) if live else 0.0
        e_full = self.env_at(self.e_full, t) if live else 0.0
        step = self.step_index(t) if live else -1
        pulse = 1.0 + 0.28 * e_low
        jx = shake * rng.uniform(-9, 9)

        for p in self.mpc:
            m = p.P[:, 0] <= sweep_x
            if not m.any():
                continue
            P = p.P[m]
            w = np.full(len(P), 0.50 * pulse)
            w += 1.6 * np.exp(-((sweep_x - P[:, 0]) / 0.075) ** 2)   # front de trace
            tag = p.tag
            if tag.startswith("pad"):
                k = int(tag[3:])
                if k in flashes:
                    w += 1.7 * flashes[k]
            elif tag.startswith("step"):
                k = int(tag[4:])
                if k == step:
                    w += 1.9
                elif k in STEP_LIT:
                    w += 0.30
            elif tag == "strip":
                w += 0.8 * e_high
            elif tag.startswith("qlink"):
                w += 0.45 * e_low
            elif tag == "wheel":
                w += 0.30 * e_full
            if melt > 0:
                w *= (1.0 - melt) ** 0.7
                P = self._melt(P, melt, t)
            px, py = self.to_px(P, collapse, (jx, 0.0))
            beam.add(px, py, w)

        # ---- organes animes
        if melt >= 0.99:
            return

        # pads allumes : remplissage
        for k, v in flashes.items():
            x0, _, _, _ = pad_rect(k // 4, k % 4)
            if x0 <= sweep_x and v > 0.05:
                self._dyn(beam, pad_fill(k), 1.05 * v, collapse, melt, t)

        # bande de 16 pas : le pas courant s'allume
        if live and 0 <= step < 16:
            x0, y0, x1, y1 = step_rect(step)
            if x0 <= sweep_x:
                self._dyn(beam, rect_fill(x0, y0, x1, y1, 4), 0.95, collapse, melt, t)

        # Q-Links : index qui tourne + bandeau qui se remplit
        for k, (cx, cy) in enumerate(QLINK):
            if cx > sweep_x:
                continue
            v = np.clip(0.18 + 0.62 * (e_low if k % 2 == 0 else e_high)
                        + 0.20 * math.sin(t * 1.7 + k), 0.0, 1.0)
            a = math.radians(225.0 - 270.0 * v)
            P, _, _ = resample([(cx + QLINK_R * 0.36 * math.cos(a),
                                 cy + QLINK_R * 0.36 * math.sin(a)),
                                (cx + QLINK_R * 0.86 * math.cos(a),
                                 cy + QLINK_R * 0.86 * math.sin(a))])
            self._dyn(beam, P, 1.15, collapse, melt, t)
            bx0 = cx - QDISP_W * 0.5 + 0.008
            self._dyn(beam, rect_fill(bx0, QDISP_Y0 + 0.008,
                                      bx0 + (QDISP_W - 0.016) * v, QDISP_Y1 - 0.008, 3),
                      0.75, collapse, melt, t)

        # touch strip : curseur lumineux
        if STRIP[0] <= sweep_x:
            sy = STRIP[1] + (STRIP[3] - STRIP[1]) * np.clip(0.12 + 0.8 * e_high, 0, 1)
            self._dyn(beam, rect_fill(STRIP[0] + 0.014, sy - 0.026,
                                      STRIP[2] - 0.014, sy + 0.026, 4),
                      0.85, collapse, melt, t)

        # ecran : forme d'onde du morceau + niveaux
        sx0, sy0, sx1, sy1 = SCREEN
        if sx0 <= sweep_x:
            m = 0.05
            x_lo, x_hi = sx0 + m, min(sx1 - m, sweep_x)
            if x_hi - x_lo > 0.05:
                xs = np.linspace(x_lo, x_hi, 320)
                u = (xs - (sx0 + m)) / ((sx1 - m) - (sx0 + m)) * 2.0 - 1.0
                yc = (sy0 + sy1) * 0.5 - 0.03
                ys = yc + 0.125 * self.wave_y(u * 1.88, t, amp=1.0)
                self._dyn(beam, np.stack([xs, ys], axis=1), 1.0, collapse, melt, t)
                base = sy0 + 0.055
                for k in range(8):
                    v = (e_low, e_full, e_high)[k % 3] * (0.5 + 0.5 * math.sin(k * 1.7 + t * 5.0))
                    v = max(0.06, v)
                    bx = sx0 + m + (k + 0.5) * ((sx1 - m - sx0 - m) / 8.0)
                    if bx > sweep_x:
                        continue
                    P, _, _ = resample([(bx, base), (bx, base + 0.16 * v)])
                    self._dyn(beam, P, 0.8, collapse, melt, t)

    def title_front(self, t):
        """Position du front qui balaie le mot, de gauche a droite.

        Il ralentit sur la largeur du mot : les lettres se detachent alors une
        par une, au rythme des doubles-croches.
        """
        u = np.clip(self.tl.at("title", t), 0.0, 1.0)
        return float(np.interp(u, (0.0, 0.19, 0.81, 1.0), (-1.95, -1.12, 1.12, 1.95)))

    def _draw_title(self, beam, t, collapse, u_out):
        """Le mot nait de la frequence : le front passe, l'onde s'efface
        derriere lui et chaque lettre se detache de la courbe."""
        xf = self.title_front(t)
        wy = self.wave_y(self.tP[:, 0], t)

        # chaque point quitte l'onde quand le front le depasse
        k = np.clip((xf - self.tP[:, 0] + 0.055) / 0.185, 0.0, 1.0)
        k = k * k * (3.0 - 2.0 * k)

        P = self.tP.copy()
        P[:, 1] = wy * (1.0 - k) + (self.tP[:, 1] + self.tmod * wy) * k

        w = self.tw * (0.12 + 0.88 * k)
        w = w + 2.4 * np.exp(-((k - 0.62) / 0.26) ** 2) * (xf < 1.9)   # eclat de detachement
        if xf >= 1.9:
            w = self.tw * (1.0 + 0.10 * self.env_at(self.e_low, t))
        if u_out > 0:
            w = w * max(0.0, 1.0 - u_out * 1.35)

        th = self.tth * k
        for off, ow in ((0.0, 1.0), (1.0, 0.60), (-1.0, 0.60)):
            px, py = self.to_px(P + self.tN * (off * th)[:, None], collapse)
            beam.add(px, py, w * ow)

        # le front lui-meme : trait vertical + point chaud sur la courbe
        if -1.94 < xf < 1.94:
            ys = np.linspace(-0.52, 0.52, 620)
            taper = np.exp(-(ys / 0.34) ** 4)          # plat au centre, fondu aux bords
            for j in range(4):
                Q = np.stack([np.full(len(ys), xf - j * 0.022), ys], axis=1)
                px, py = self.to_px(Q, collapse)
                beam.add(px, py, taper * (0.95 if j == 0 else 0.26) * (0.58 ** j))
            dot = np.stack([np.full(60, xf), np.linspace(-0.02, 0.02, 60)
                            + float(self.wave_y(np.array([xf]), t)[0])], axis=1)
            px, py = self.to_px(dot, collapse)
            beam.add(px, py, 2.2)

    # -- image -------------------------------------------------------------

    def intensity(self, t):
        tl = self.tl
        beam = Beam(self.H, self.W, self.gain)
        rng = np.random.default_rng(self.seed + int(t * self.fps + 0.5))

        collapse = 1.0
        u_out = tl.at("out", t)
        if u_out > 0:
            collapse = max(0.006, (1.0 - ease_in_out(min(1.0, u_out / 0.62))) ** 1.6)

        shake = 0.0
        for gt, gd in GLITCHES:
            gt *= self.dur / Timeline.REF
            if 0 <= t - gt < gd:
                shake = max(shake, 1.0 - (t - gt) / gd)

        grid_a = smoothstep(0.05, 0.55, t) * (1.0 - 0.55 * smoothstep(tl.start("melt"),
                                                                     tl.end("title"), t))
        self._grid(beam, t, grid_a * (1.0 if u_out <= 0 else max(0.0, 1 - u_out * 2)), collapse)
        self._hud(beam, t, collapse,
                  smoothstep(0.15, 0.6, t) * (1.0 - smoothstep(tl.start("melt"),
                                                               tl.end("melt"), t)))

        # ---- 1. amorce : la trace se stabilise
        if t < tl.start("sweep") + 0.4:
            a = 1.0 - smoothstep(tl.start("sweep"), tl.start("sweep") + 0.35, t)
            if a > 0.01:
                n = 1600
                x = np.linspace(-1.75, 1.75, n)
                base = smoothstep(0.0, 0.45, t)
                y = (rng.standard_normal(n) * 0.004 + 0.012 * np.sin(x * 40 + t * 30))
                y *= 1.0 + 6.0 * (1.0 - base)
                px, py = self.to_px(np.stack([x, y], axis=1), collapse)
                beam.add(px, py, 1.15 * a)

        # ---- 2. balayage de l'oscillateur
        u_sweep = tl.at("sweep", t)
        sweep_x = -1.95 + 3.95 * ease_in_out(np.clip(u_sweep, 0, 1)) if u_sweep > 0 else -1.95

        if tl.start("boot") < t < tl.start("groove"):
            amp = 0.50 * (1.0 - ease_out(np.clip(u_sweep, 0, 1), 1.5)) + 0.02
            freq = 2.4 + 5.2 * np.clip(u_sweep, 0, 1)
            xs = np.linspace(-1.88, 1.88, 2400)

            def osc(tt):
                y = amp * (np.sin(freq * math.pi * xs + tt * 7.0)
                           + 0.32 * np.sin(freq * 2.7 * math.pi * xs - tt * 4.0))
                return np.stack([xs, y * 0.85], axis=1)

            dt = 1.0 / self.fps
            for k in range(6):
                px, py = self.to_px(osc(t - k * dt * 0.85), collapse)
                beam.add(px, py, 0.46 * (0.55 ** k))

        # ---- 3. la machine
        melt = float(np.clip(tl.at("melt", t), 0, 1))
        if u_sweep > 0 and melt < 0.995:
            self._machine(beam, t, collapse, sweep_x, melt, rng, shake)

        # tete de balayage
        if 0.0 < u_sweep < 1.02:
            n = 900
            ys = np.linspace(-0.97, 0.97, n)
            for k in range(5):
                jit = 0.004 * np.sin(ys * 60 + t * 40) if k == 0 else 0.0
                P = np.stack([np.full(n, sweep_x - k * 0.030) + jit, ys], axis=1)
                px, py = self.to_px(P, collapse)
                beam.add(px, py, (1.35 if k == 0 else 0.42) * (0.62 ** k))
            for gx, gw in ((sweep_x - 0.55, 0.16), (sweep_x - 1.05, 0.07)):
                if gx > -1.9:
                    P = np.stack([np.full(400, gx), np.linspace(-0.95, 0.95, 400)], axis=1)
                    px, py = self.to_px(P, collapse)
                    beam.add(px, py, gw)

        # ---- 4. la forme d'onde du morceau
        a_wave = 0.0
        if t >= tl.start("groove"):
            a_wave = 0.24 * smoothstep(tl.start("groove"), tl.start("groove") + 0.5, t)
        if t >= tl.start("melt"):
            a_wave = 0.24 + 0.76 * smoothstep(tl.start("melt"), tl.start("melt") + 0.55, t)
        xf = self.title_front(t) if t >= tl.start("title") else None
        if u_out > 0:
            a_wave *= max(0.0, 1.0 - u_out * 1.6)
        self._wave_line(beam, t, collapse, a_wave, xf)

        # ---- 5. le titre, ecrit par la courbe
        if t >= tl.start("title") and u_out < 0.95:
            self._draw_title(beam, t, collapse, max(0.0, u_out))

        return beam.render(), collapse, shake, rng

    def colorize(self, field, t, collapse, shake, rng):
        W, H = self.W, self.H
        core = gauss(field, self.sigma)

        g4 = gauss(downsample(core, 4), 2.6)
        g8 = gauss(downsample(core, 8), 4.5)
        glow = upsample(g4, 4, (H, W)) * 2.6 + upsample(g8, 8, (H, W)) * 3.4

        inten = core * 1.15
        hot = np.clip(inten - 0.72, 0, None)

        img = np.zeros((H, W, 3), dtype=np.float32)
        base = np.clip(inten, 0, 1.6)
        for c in range(3):
            img[:, :, c] = VERT_FLUO[c] * base + VERT_HALO[c] * np.clip(glow, 0, 3.0) * 0.55
        img += (np.clip(hot * 1.25, 0, 1.0) ** 1.25)[..., None] * np.float32([0.85, 1.0, 0.88])

        yy = np.arange(H, dtype=np.float32)[:, None]
        period = max(2.0, H / 360.0)
        sl = 0.82 + 0.18 * (0.5 + 0.5 * np.cos(yy * (2.0 * math.pi / period)))
        roll = 1.0 + 0.05 * np.cos((yy / H + t * 0.16) * 2.0 * math.pi)
        img *= (sl * roll).astype(np.float32)[..., None]

        ny = (np.arange(H, dtype=np.float32)[:, None] / H - 0.5) * 2.0
        nx = (np.arange(W, dtype=np.float32)[None, :] / W - 0.5) * 2.0
        img *= (np.clip(1.06 - 0.42 * (nx * nx * 0.55 + ny * ny), 0.0, 1.0) ** 1.15)[..., None]

        img += upsample(rng.standard_normal((H // 4, W // 4)).astype(np.float32),
                        4, (H, W))[..., None] * 0.011

        gl = 0.0
        for gt, gd in GLITCHES:
            gt *= self.dur / Timeline.REF
            if 0 <= t - gt < gd:
                gl = max(gl, 1.0 - (t - gt) / gd)
        if gl > 0.02:
            for _ in range(int(3 + 9 * gl)):
                y0 = int(rng.integers(0, H - 4))
                y1 = min(H, y0 + int(rng.integers(3, max(6, int(H * 0.06)))))
                off = int(rng.integers(-int(W * 0.05 * gl) - 2, int(W * 0.05 * gl) + 3))
                img[y0:y1] = np.roll(img[y0:y1], off, axis=1)
            sh = max(1, int(6 * gl))
            img[:, :, 0] = np.roll(img[:, :, 0], sh, axis=1)
            img[:, :, 2] = np.roll(img[:, :, 2], -sh, axis=1)

        if self.curve:
            img = self._warp(img)

        u_out = self.tl.at("out", t)
        if u_out > 0:
            f = 1.0 if u_out <= 0.62 else max(0.0, 1.0 - (u_out - 0.62) / 0.26)
            img *= f
            if 0.55 < u_out < 0.72:
                cy, cx = H // 2, W // 2
                r = max(2, int(H * 0.006))
                img[cy - r:cy + r, cx - int(r * 2.5):cx + int(r * 2.5)] += 1.4

        np.clip(img, 0.0, 1.0, out=img)
        return ((img ** (1.0 / 1.06)) * 255.0 + 0.5).astype(np.uint8)

    def frame(self, i):
        t = i / self.fps
        field, collapse, shake, rng = self.intensity(t)
        return self.colorize(field, t, collapse, shake, rng)


# ==========================================================================
#  Pipeline
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
    ap.add_argument("--duration", type=float, default=DUREE_REF)
    ap.add_argument("--crf", type=int, default=16)
    ap.add_argument("--jobs", type=int, default=os.cpu_count() or 2)
    ap.add_argument("--seed", type=int, default=7)
    ap.add_argument("--no-curve", action="store_true", help="desactive la courbure CRT")
    ap.add_argument("--no-audio", action="store_true", help="video muette (l'image reste pilotee par le son)")
    ap.add_argument("--stills", default="", help="dossier ou exporter des images cles PNG")
    ap.add_argument("--still-times", default="0.4,2.2,3.4,4.6,6.2,8.6,9.9,10.6,11.6,12.6")
    args = ap.parse_args()

    audio = synth_audio(args.duration, seed=args.seed)

    global _R
    _R = Renderer(args.width, args.height, args.fps, args.duration, audio,
                  curve=not args.no_curve, seed=args.seed)

    if args.stills:
        from PIL import Image
        os.makedirs(args.stills, exist_ok=True)
        for ts in [float(x) for x in args.still_times.split(",") if x.strip()]:
            Image.fromarray(_R.frame(int(round(ts * args.fps)))).save(
                os.path.join(args.stills, "t%05.2f.png" % ts))
            print("still %.2fs" % ts, flush=True)
        return

    nframes = int(round(args.duration * args.fps))
    tmpdir = tempfile.mkdtemp(prefix="omnipotard_")
    wav = None
    if not args.no_audio:
        wav = os.path.join(tmpdir, "omnipotard.wav")
        write_wav(wav, audio["stereo"], audio["sr"])
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

    import time
    proc = subprocess.Popen(cmd, stdin=subprocess.PIPE)
    t0 = time.time()
    try:
        if args.jobs > 1:
            import multiprocessing as mp
            chunk = max(args.jobs, 24)
            with mp.get_context("fork").Pool(args.jobs) as pool:
                for start in range(0, nframes, chunk):
                    for buf in pool.map(_worker, range(start, min(nframes, start + chunk)), 1):
                        proc.stdin.write(buf)
                    done = min(nframes, start + chunk)
                    el = time.time() - t0
                    print("\r  %d/%d frames  %.0fs  (eta %.0fs)"
                          % (done, nframes, el, el / done * (nframes - done)), end="", flush=True)
        else:
            for i in range(nframes):
                proc.stdin.write(_worker(i))
    finally:
        proc.stdin.close()
        proc.wait()
    print("\n%s  (%.1f s, %dx%d @ %dfps)" % (args.out, args.duration, args.width,
                                             args.height, args.fps))


if __name__ == "__main__":
    main()
