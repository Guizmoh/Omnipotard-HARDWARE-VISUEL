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

# Palettes : coeur du trait, halo, coeur sur-expose, fond de dalle.
PALETTES = {
    "vert":      ((0.24, 1.00, 0.16), (0.10, 1.00, 0.34), (0.85, 1.00, 0.88), (0.0, 0.0, 0.0)),
    "orange":    ((1.00, 0.45, 0.07), (1.00, 0.11, 0.02), (1.00, 0.93, 0.80), (0.0, 0.0, 0.0)),
    "bleu":      ((0.22, 0.66, 1.00), (0.05, 0.26, 1.00), (0.86, 0.96, 1.00), (0.0, 0.0, 0.0)),
    "bleu-fond": ((0.62, 0.90, 1.00), (0.14, 0.48, 1.00), (0.92, 0.98, 1.00),
                  (0.022, 0.066, 0.168)),
}

SR = 48000
DUREE_REF = 11.5                 # 15 temps + 1s de maintien sur le logo
MUSIC_PATH = "assets/hint.mp3"   # morceau utilise ; --music pour en changer
MUSIC_START = 0.0                # tout debut du morceau
# 19.8209 : l'autre point d'accroche essaye — musique, break d'une seconde,
# puis drop pile sur la barre de mesure (--music-start 19.8209).


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
    """Chemin discretise : points, normales, abscisse curviligne, etiquette."""

    __slots__ = ("P", "N", "s", "ph", "tag")

    def __init__(self, pts, closed=False, tag="", step=STEP):
        self.P, self.s, _ = resample(pts, step, closed)
        tan = np.gradient(self.P, axis=0)
        tan /= (np.linalg.norm(tan, axis=1, keepdims=True) + 1e-12)
        self.N = np.stack([-tan[:, 1], tan[:, 0]], axis=1)
        # phase stable par organe : chaque piece tremble pour son compte
        self.ph = (sum(ord(c) for c in tag) % 97) * 0.0647
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
    "H": (0.62, [[(0, 0), (0, 1)], [(.62, 0), (.62, 1)], [(0, .50), (.62, .50)]]),
    "W": (0.86, [[(0, 1), (.19, 0), (.43, .64), (.67, 0), (.86, 1)]]),
    "0": (0.46, [[(0, .18), (.14, 0), (.32, 0), (.46, .18), (.46, .82), (.32, 1), (.14, 1), (0, .82), (0, .18)]]),
    "1": (0.26, [[(0, .80), (.13, 1), (.13, 0)], [(0, 0), (.26, 0)]]),
    "S": (0.60, [[(.60, .84), (.44, 1), (.16, 1), (0, .84), (0, .66), (.16, .50), (.44, .50), (.60, .34), (.60, .16), (.44, 0), (.16, 0), (0, .16)]]),
    "U": (0.62, [[(0, 1), (0, .18), (.18, 0), (.44, 0), (.62, .18), (.62, 1)]]),
    "Y": (0.62, [[(0, 1), (.31, .55), (.62, 1)], [(.31, .55), (.31, 0)]]),
    " ": (0.30, []),
}

TRACKING = 0.145


def text_width(txt, tracking=TRACKING):
    return sum(GLYPHS[c][0] for c in txt) + tracking * max(0, len(txt) - 1)


def glyph_strokes(txt, height, x0, y0, center=True, tracking=TRACKING):
    """Traits du texte, positionnes : liste de (indice_lettre, points)."""
    x = x0 - text_width(txt, tracking) * height * 0.5 if center else x0
    out = []
    for gi, ch in enumerate(txt):
        gw, strokes = GLYPHS[ch]
        for st in strokes:
            out.append((gi, [(x + px * height, y0 + py * height) for px, py in st]))
        x += (gw + tracking) * height
    return out


def text_paths(txt, height, x0, y0, step=STEP, center=True, tag="txt", tracking=TRACKING):
    return [Path(pts, tag="%s:%d" % (tag, gi), step=step)
            for gi, pts in glyph_strokes(txt, height, x0, y0, center, tracking)]


# ==========================================================================
#  La machine : MPC Live III
#  Silhouette : bande de 16 pas sur l'arete haute, ecran 7" a gauche,
#  4 Q-Links surmontes de leurs bandeaux, molette encastree en haut a droite,
#  grille 4x4 en bas a droite, touch strip vertical le long des pads.
# ==========================================================================

# Disposition relevee sur une photo de dessus de la MPC Live III, simplifiee.
# Unites = demi-hauteur d'image. De gauche a droite : touch strip sur l'arete,
# grille 4x4, ecran 7", colonne de 4 Q-Links et molette ; bande de 16 pas et
# potard de volume en haut, grille de haut-parleur en bas.
BODY = (-1.445, -0.848, 1.445, 0.848)
BODY_IN = (-1.410, -0.813, 1.410, 0.813)
VOLUME, VOLUME_R = (-1.266, 0.669), 0.119
STEP_X0, STEP_Y0, STEP_W, STEP_H, STEP_GAP = -1.087, 0.627, 0.1183, 0.090, 0.0239
TOPBTN = ((1.198, 0.627, 1.298, 0.717), (1.318, 0.627, 1.418, 0.717))
STRIP = (-1.343, -0.269, -1.224, 0.448)
PAD_X0, PAD_Y0 = -1.116, -0.299
PAD_W, PAD_H, PAD_GX, PAD_GY = 0.2386, 0.1936, 0.0299, 0.0299
SCREEN = (0.018, -0.167, 1.086, 0.567)
QLINK = [(1.266, 0.466), (1.266, 0.257), (1.266, 0.048), (1.266, -0.161)]
QLINK_R = 0.0836
WHEEL, WHEEL_R = (1.266, -0.406), 0.155
BTN_ROWS = (-0.275, -0.382, -0.489)
BTN_X0, BTN_W, BTN_H, BTN_GAP, BTN_N = 0.018, 0.185, 0.084, 0.030, 5
GRILLE = (-1.340, -0.800, 1.340, -0.570)
MIC, MIC_R = (0.0, -0.520), 0.025

# L'ecran de la machine : c'est la que se joue la fin. Le zoom de camera et
# l'echelle de la composition sont inverses l'un de l'autre (SCR_S * CAM_Z = 1),
# si bien que le logo garde exactement la meme taille a l'image qu'avant — seul
# le cadre change : on est desormais dans la dalle de la MPC.
SCR_IN = (SCREEN[0] + 0.028, SCREEN[1] + 0.028, SCREEN[2] - 0.028, SCREEN[3] - 0.028)
SCR_C = ((SCR_IN[0] + SCR_IN[2]) * 0.5, (SCR_IN[1] + SCR_IN[3]) * 0.5)
CAM_Z = 2.60
SCR_S = 1.0 / CAM_Z
SCR_HW = (SCR_IN[2] - SCR_IN[0]) * 0.5 * CAM_Z      # decoupe, en composition
SCR_HH = (SCR_IN[3] - SCR_IN[1]) * 0.5 * CAM_Z
SCR_OY = 0.0475                                     # centrage vertical du bloc


def pad_rect(i, j):
    """i = ligne (0 = bas), j = colonne (0 = gauche)."""
    x0 = PAD_X0 + j * (PAD_W + PAD_GX)
    y0 = PAD_Y0 + i * (PAD_H + PAD_GY)
    return x0, y0, x0 + PAD_W, y0 + PAD_H


def step_rect(k):
    x0 = STEP_X0 + k * (STEP_W + STEP_GAP)
    return x0, STEP_Y0, x0 + STEP_W, STEP_Y0 + STEP_H


def build_mpc(step=STEP):
    P = []
    add = P.append

    add(Path(rrect_pts(*BODY, r=0.072), closed=True, tag="body", step=step))
    add(Path(rrect_pts(*BODY_IN, r=0.052), closed=True, tag="body", step=step))

    # potard de volume (coin haut gauche)
    add(Path(circle_pts(*VOLUME, r=VOLUME_R), closed=True, tag="vol", step=step))
    add(Path(circle_pts(*VOLUME, r=VOLUME_R * 0.34), closed=True, tag="vol", step=step))

    # bande de 16 pas + touches du coin haut droit
    for k in range(16):
        add(Path(rrect_pts(*step_rect(k), r=0.014), closed=True, tag="step%d" % k, step=step))
    for k, r in enumerate(TOPBTN):
        add(Path(rrect_pts(*r, r=0.014), closed=True, tag="btnx%d" % k, step=step))

    # touch strip (arete gauche) + ses reperes
    add(Path(rrect_pts(*STRIP, r=0.052), closed=True, tag="strip", step=step))
    for k in range(11):
        y = STRIP[1] + 0.045 + (STRIP[3] - STRIP[1] - 0.09) * k / 10.0
        add(Path([(STRIP[0] + 0.022, y), (STRIP[2] - 0.022, y)], tag="strip", step=step))

    # grille 4x4 : contour + biseau interieur
    for i in range(4):
        for j in range(4):
            x0, y0, x1, y1 = pad_rect(i, j)
            k = i * 4 + j
            add(Path(rrect_pts(x0, y0, x1, y1, 0.030), closed=True, tag="pad%d" % k, step=step))
            add(Path(rrect_pts(x0 + 0.022, y0 + 0.020, x1 - 0.022, y1 - 0.020, 0.020),
                     closed=True, tag="pad%d" % k, step=step))

    # ecran tactile 7"
    sx0, sy0, sx1, sy1 = SCREEN
    add(Path(rrect_pts(sx0, sy0, sx1, sy1, 0.020), closed=True, tag="lcd", step=step))
    add(Path(rrect_pts(sx0 + 0.028, sy0 + 0.028, sx1 - 0.028, sy1 - 0.028, 0.012),
             closed=True, tag="lcd", step=step))
    add(Path([(sx0 + 0.028, sy1 - 0.115), (sx1 - 0.028, sy1 - 0.115)], tag="lcd", step=step))

    # colonne de Q-Links + molette
    for k, (cx, cy) in enumerate(QLINK):
        add(Path(circle_pts(cx, cy, QLINK_R), closed=True, tag="qlink%d" % k, step=step))
        add(Path(circle_pts(cx, cy, QLINK_R * 0.30), closed=True, tag="qlink%d" % k, step=step))
    add(Path(circle_pts(WHEEL[0], WHEEL[1], WHEEL_R), closed=True, tag="wheel", step=step))
    add(Path(circle_pts(WHEEL[0], WHEEL[1], WHEEL_R * 0.68), closed=True, tag="wheel", step=step))
    add(Path(circle_pts(WHEEL[0], WHEEL[1], WHEEL_R * 0.26), closed=True, tag="wheel", step=step))

    # rangees de touches sous l'ecran
    for row, yy in enumerate(BTN_ROWS):
        for k in range(BTN_N):
            x0 = BTN_X0 + k * (BTN_W + BTN_GAP)
            add(Path(rrect_pts(x0, yy, x0 + BTN_W, yy + BTN_H, 0.016), closed=True,
                     tag="btn%d" % (row * BTN_N + k), step=step))

    # marquage + grille de haut-parleur
    P += text_paths("MPC LIVE III", 0.095, -1.070, -0.470, step=step, center=False, tag="logo")
    add(Path(rrect_pts(*GRILLE, r=0.030), closed=True, tag="grille", step=step))
    for k in range(5):
        y = GRILLE[1] + 0.038 + (GRILLE[3] - GRILLE[1] - 0.076) * k / 4.0
        add(Path([(GRILLE[0] + 0.030, y), (GRILLE[2] - 0.030, y)], tag="grille", step=step))
    add(Path(circle_pts(*MIC, r=MIC_R), closed=True, tag="mic", step=step))
    return P


def pad_fill(k, nlines=7):
    x0, y0, x1, y1 = pad_rect(k // 4, k % 4)
    m = 0.038
    return np.vstack([np.stack([np.linspace(x0 + m, x1 - m, 56), np.full(56, y)], axis=1)
                      for y in np.linspace(y0 + m * 0.8, y1 - m * 0.8, nlines)])


def rect_fill(x0, y0, x1, y1, nlines=4, m=0.008):
    return np.vstack([np.stack([np.linspace(x0 + m, x1 - m, 24), np.full(24, y)], axis=1)
                      for y in np.linspace(y0 + m, y1 - m, nlines)])


# ==========================================================================
#  La courbe du titre : un seul trait continu, de la ligne de base au mot
# ==========================================================================

ONDE, TRAIT, TRANSIT, LIAISON = 0, 1, 2, 3


def build_title_curve(txt, height, y0, x_in=-1.88, x_out=1.88, step=STEP):
    """Un seul fil continu : la ligne d'onde traverse toute l'image et le mot
    est pose dessus.

    Chaque glyphe touche deja la ligne de base, donc le trace du logo et le fil
    ne font qu'un — aucune liaison en diagonale n'est necessaire. Seuls les
    sauts internes aux lettres a plusieurs traits restent, en faible intensite,
    comme un retour de spot.
    """
    strokes = glyph_strokes(txt, height, 0.0, y0)
    xs = [q[0] for _, st in strokes for q in st]
    wx0, wx1 = min(xs), max(xs)
    segs = [([(x_in, y0), (wx0, y0)], ONDE),
            ([(wx0, y0), (wx1, y0)], LIAISON),
            ([(wx1, y0), (x_out, y0)], ONDE)]
    prev, prev_gi = None, None
    for gi, st in strokes:
        if prev is not None and gi == prev_gi:
            segs.append(([prev, tuple(st[0])], TRANSIT))
        segs.append((st, TRAIT))
        prev, prev_gi = tuple(st[-1]), gi

    Ps, Ns, ks, ss, off = [], [], [], [], 0.0
    for pts, kind in segs:
        pts = np.asarray(pts, dtype=np.float64)
        if float(np.linalg.norm(np.diff(pts, axis=0), axis=1).sum()) < 1e-6:
            continue
        q, sq, length = resample(pts, step)
        tan = np.gradient(q, axis=0)
        tan /= (np.linalg.norm(tan, axis=1, keepdims=True) + 1e-12)
        Ps.append(q)
        Ns.append(np.stack([-tan[:, 1], tan[:, 0]], axis=1))
        ks.append(np.full(len(q), kind, dtype=np.int8))
        ss.append(sq + off)
        off += length
    return (np.vstack(Ps), np.vstack(Ns), np.concatenate(ks),
            np.concatenate(ss), off)


# ==========================================================================
#  Bande son : dub ambient, synthese additive (aucune dependance externe)
#  4 mesures : nappe -> drop et groove dub -> break -> impact et ambient.
#  Chaque evenement rythmique renvoie aussi le pad qu'il allume a l'image.
# ==========================================================================

NOTES = {"G1": 49.00, "A1": 55.00, "C2": 65.41, "D2": 73.42, "E2": 82.41, "G4": 392.0,
         "A2": 110.0, "C3": 130.8, "E3": 164.8, "G3": 196.0, "B3": 246.9,
         "A3": 220.0, "C4": 261.6, "E4": 329.6}

# motif de 16 pas, joue deux fois (dub : one drop, skank sur les contretemps)
KICKS = (0, 4, 8, 12)          # quatre au sol
RIMS = (8,)                    # accent sur le troisieme temps
HATS = (3, 7, 11, 15)          # shaker sur les doubles
PERCS = ()
SKANKS = (2, 6, 10, 14)        # l'accord des contretemps
BASSLINE = (((0, "A1", 6), (10, "C2", 4)),)

# pad allume par famille d'evenement (grille 4x4, 0 = en bas a gauche)
PAD_OF = {"kick": 0, "rim": 5, "hat": 10, "perc": 6}
PAD_BASS = {"A1": 1, "G1": 1, "C2": 2, "D2": 2, "E2": 3}
PAD_SKANK = (12, 13, 14, 15)
DECAY_OF = {"kick": 4.5, "rim": 8.0, "hat": 14.0, "perc": 13.0,
            "bass": 3.0, "skank": 5.0}


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


def _whoosh(dur, sr, rng, up=True):
    """Souffle : bruit filtre dont le centre spectral s'ouvre (ou se referme).

    Pas de composante tonale et pas de bande criarde : on veut de l'air, pas
    un effet de transition tape-a-l'oeil.
    """
    n = max(16, int(dur * sr))
    u = np.arange(n) / (n - 1.0)
    nz = rng.standard_normal(n)
    # bandes decalees vers le haut, la derniere est un vrai passe-haut :
    # on cherche de l'air, pas un grondement.
    bands = [_lowpass(nz, 150), _lowpass(nz, 48), _lowpass(nz, 16), nz - _lowpass(nz, 4)]
    bands = [b / (b.std() + 1e-9) for b in bands]
    pos = u if up else 1.0 - u
    out = np.zeros(n)
    for i, b in enumerate(bands):
        out += b * np.exp(-((pos - i / 3.0) / 0.34) ** 2) * (0.72, 0.95, 1.05, 1.00)[i]
    env = pos ** 1.4
    if up:
        env = env * (1.0 - 0.85 * np.clip((u - 0.92) / 0.08, 0, 1))
    return out * env * 0.115


def _tv_off(dur, sr, rng):
    """Extinction d'un televiseur : claquement de l'interrupteur, sifflement de
    ligne qui meurt en glissant, image qui se referme, coup de transformateur."""
    n = max(64, int(dur * sr))
    t = np.arange(n) / sr
    out = np.zeros(n)

    k = int(0.004 * sr)                                  # claquement
    cl = rng.standard_normal(k)
    out[:k] += (cl - _lowpass(cl, 6)) * np.exp(-np.arange(k) / sr * 900.0) * 0.46

    fw = 12500.0 * np.exp(-t * 3.2) + 900.0               # sifflement de ligne
    out += np.sin(2 * math.pi * np.cumsum(fw) / sr) * np.exp(-t * 20.0) * 0.11

    fc = 2600.0 * np.exp(-t * 26.0) + 70.0                # l'image se referme
    out += np.sin(2 * math.pi * np.cumsum(fc) / sr) * np.exp(-t * 15.0) * 0.13

    nz = rng.standard_normal(n)                           # souffle qui s'ecrase
    out += (nz - _lowpass(nz, 5)) * np.exp(-t * 24.0) * 0.09

    ft = 78.0 * np.exp(-t * 16.0) + 26.0                  # coup de transfo
    out += np.sin(2 * math.pi * np.cumsum(ft) / sr) * np.exp(-t * 9.0) * 0.30
    return out


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
    tl = Timeline(duration)
    beat = duration / 20.0                # 20 temps sur toute la piece (125 BPM)
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
        """Grosse caisse ronde et profonde, sans clic : le pouls dub techno."""
        t = seg(0.85)
        fr = 43.0 + 66.0 * np.exp(-t * 24.0)
        s = np.sin(2 * math.pi * np.cumsum(fr) / sr) * np.exp(-t * 4.0)
        s += _lowpass(rng.standard_normal(len(t)), 70) * np.exp(-t * 26.0) * 0.22
        add(dry, s, at, 0.90 * f)
        add(rev, s, at, 0.10 * f)
        fire(at, "kick", PAD_OF["kick"], f)

    def chord(at, k, f=1.0):
        """L'accord bref des contretemps : peu de direct, beaucoup d'echo.
        C'est lui qui fait le grain dub techno."""
        t = seg(0.60)
        env = np.minimum(t / 0.010, 1.0) * np.exp(-t * 8.5)
        s = np.zeros(len(t))
        for f0, g in ((NOTES["A3"], 1.0), (NOTES["C4"], 0.85),
                      (NOTES["E4"], 0.72), (NOTES["G4"], 0.55)):
            s += g * (np.sin(2 * math.pi * f0 * t)
                      + 0.45 * np.sin(4 * math.pi * f0 * t + 0.4)
                      + 0.18 * np.sin(6 * math.pi * f0 * t))
        s = _lowpass(s * env, 6)
        s = s - _lowpass(s, 90)                 # timbre creux, sans bas
        add(dry, s, at, 0.05 * f)
        add(ech, s, at, 0.17 * f)
        add(rev, s, at, 0.11 * f)
        fire(at, "skank", PAD_SKANK[k % 4], f)

    def shaker(at, f=1.0):
        t = seg(0.11)
        s = _highpass(rng.standard_normal(len(t)), 4) * np.exp(-t * 48)
        add(dry, s, at, 0.045 * f)
        add(rev, s, at, 0.08 * f)
        fire(at, "hat", PAD_OF["hat"], 0.5 * f)

    def rimshot(at, f=1.0):
        t = seg(0.26)
        nz = _highpass(rng.standard_normal(len(t)), 6)
        s = nz * np.exp(-t * 32) * 0.6 + np.sin(2 * math.pi * 620 * t) * np.exp(-t * 44) * 0.4
        add(dry, s, at, 0.10 * f)
        add(ech, s, at, 0.60 * f)
        add(rev, s, at, 0.38 * f)
        fire(at, "rim", PAD_OF["rim"], f)

    def bass(at, name, dur, f=1.0):
        """Sub tenu, presque sans harmonique."""
        t = seg(dur)
        f0 = NOTES[name]
        env = np.minimum(t / 0.030, 1.0) * np.exp(-t * 1.0)
        env *= np.clip((dur - t) / 0.12, 0, 1)
        s = (np.sin(2 * math.pi * f0 * t) + 0.12 * np.sin(4 * math.pi * f0 * t)) * env
        add(dry, s, at, 0.50 * f)
        fire(at, "bass", PAD_BASS[name], 0.7 * f)

    # ------------------------------- le lit : nappe, sub, souffle de bande
    t_all = np.arange(n) / sr
    swell = (np.clip(smoothstep(0.0, beat * 2.2, t_all), 0, 1)
             * (1.0 - smoothstep(duration - 0.55, duration, t_all)))
    swell = swell * (0.86 + 0.14 * np.sin(2 * math.pi * 0.19 * t_all))

    pad = np.zeros(n)
    for name, g in (("A2", 1.0), ("C3", 0.80), ("E3", 0.70),
                    ("G3", 0.55), ("B3", 0.40), ("E4", 0.22)):
        f0 = NOTES[name]
        pad += g * (np.sin(2 * math.pi * f0 * t_all + f0)
                    + 0.55 * np.sin(2 * math.pi * f0 * 1.004 * t_all))   # battement
    opening = np.clip(smoothstep(0.0, tl.start("title"), t_all), 0, 1)   # le filtre s'ouvre
    pad = _lowpass(pad, 70) * (1.0 - opening) + _lowpass(pad, 11) * opening
    dry += pad * swell * 0.032
    rev += pad * swell * 0.050

    dry += np.sin(2 * math.pi * 55.0 * t_all) * swell * 0.095            # sub tenu

    hiss = _lowpass(rng.standard_normal(n), 14)
    dry += hiss * swell * 0.032
    crackle = (rng.random(n) < 0.00028).astype(np.float64) * rng.standard_normal(n)
    dry += _lowpass(crackle, 3) * swell * 0.22                           # grain de bande

    # ------------------------------------------- 1. souffle d'ouverture
    g0, g1 = tl.start("groove"), tl.end("groove")
    add(dry, _whoosh(g0 - 0.02, sr, rng, up=True), 0.02, 0.75)
    add(rev, _whoosh(g0 - 0.02, sr, rng, up=True), 0.02, 0.30)

    # ------------------------------------------------- 2. groove dub techno
    n_steps = max(4, int(round((g1 - g0) / six)))
    ck = 0
    for i in range(n_steps):
        at = g0 + i * six
        k = i % 16
        if k in KICKS:
            kick(at, 1.0 if k == 0 else 0.92)
        if k in SKANKS:
            chord(at, ck, 0.95 if k in (2, 10) else 0.72)
            ck += 1
        if k in HATS:
            shaker(at, 0.7)
        if k in RIMS:
            rimshot(at, 0.7)
        for st, name, dur in BASSLINE[0]:
            if st == k:
                bass(at, name, dur * six)

    # --------------------------------- 3. break : la matiere part dans l'echo
    b0, t0 = tl.start("zoom"), tl.start("title")
    add(dry, _whoosh(t0 - b0, sr, rng, up=True), b0, 0.85)
    add(rev, _whoosh(t0 - b0, sr, rng, up=True), b0, 0.40)
    chord(b0, ck, 0.95)
    chord(b0 + 2 * six, ck + 1, 0.60)

    # -------------------------------------------- 4. impact puis longue traine
    ti = seg(min(3.2, duration - t0))
    fi = 30.0 + 120.0 * np.exp(-ti * 9.0)
    imp = np.sin(2 * math.pi * np.cumsum(fi) / sr) * np.exp(-ti * 1.9) * 0.95
    imp += _lowpass(rng.standard_normal(len(ti)), 12) * np.exp(-ti * 3.5) * 0.28
    add(dry, imp, t0)
    add(rev, imp * 0.45, t0)
    for k in range(3):
        chord(t0 + (4 + 6 * k) * six, ck + 2 + k, 0.34 - 0.09 * k)

    # ------------------------------------------------------ 5. sortie
    o0 = tl.start("out")
    add(dry, _whoosh(0.30, sr, rng, up=True), o0 - 0.24, 0.70)
    add(dry, _whoosh(max(0.12, duration - o0), sr, rng, up=False), o0, 0.80)
    tq = seg(min(0.35, duration - o0))
    fq = 90.0 * np.exp(-tq * 14.0) + 26.0
    add(dry, np.sin(2 * math.pi * np.cumsum(fq) / sr) * np.exp(-tq * 7.0) * 0.50, o0)

    # ------------------------------------------------------------- mixage
    mix = dry + _tape_echo(ech, beat * 0.75, sr, fb=0.64, taps=10, damp=13) * 0.60
    mix += _fft_conv(rev, _reverb_ir(sr, dur=3.4, decay=1.7)) * 0.55
    mix = _tanh_limit(mix * 0.95, 1.4)
    fade = np.clip(np.arange(n) / (0.04 * sr), 0, 1) * np.clip((n - np.arange(n)) / (0.10 * sr), 0, 1)
    mix *= fade
    mix /= (np.max(np.abs(mix)) or 1.0) / 0.94

    d = int(0.0009 * sr)
    right = np.concatenate([np.zeros(d), mix[:-d]]) * 0.96 + mix * 0.04
    st = np.stack([mix, right], axis=1)
    events.sort()
    return {"stereo": (np.clip(st, -1, 1) * 32767).astype("<i2"),
            "mono": mix.astype(np.float32), "events": events, "sr": sr,
            "beat": beat}


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
    KEYS = [                        # cales sur les temps du morceau (0,704 s)
        ("boot", 0.000, 1.056),     # la piste s'enregistre, sur la musique
        ("sweep", 1.056, 3.168),    # la mue, qui deborde d'un demi-temps sur
        ("groove", 2.816, 5.632),   # le drop — une mesure pleine de groove
        ("zoom", 5.632, 6.336),     # la camera entre dans l'ecran de la machine
        ("title", 6.336, 9.152),    # quatre temps : le balayage prend son temps
        ("hold", 9.152, 11.208),    # +1s : le logo reste plus longtemps
        ("out", 11.208, 11.500),
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


GLITCHES = [(2.79, .09), (4.22, .05), (5.61, .10), (6.31, .11),
            (9.13, .06), (10.70, .05), (11.05, .06)]


# ==========================================================================
#  Rendu
# ==========================================================================

TITLE_H = 0.27
CURVE_AMP = 0.28
CURVE_WIN = 0.070          # fenetre d'analyse affichee (s) — "base de temps"
# clip audio facon station de travail (Audacity / Live)
CLIP = (-1.74, -0.50, 1.74, 0.50)
CLIP_BAR_H = 0.12
WAVE_YMAX = 0.36
DAW_COLS = 560

SUB_TXT = "HARDWARE ONLY"
SUB_H, SUB_Y, SUB_TRACK = 0.065, -0.175, 0.55
WEIGHT_OF = {ONDE: 0.80, TRAIT: 1.15, TRANSIT: 0.10, LIAISON: 0.80}
THICK_OF = {ONDE: 0.0030, TRAIT: 0.0052, TRANSIT: 0.0, LIAISON: 0.0030}

# pas programmes dans le motif : ils restent faiblement allumes
STEP_LIT = frozenset(KICKS + RIMS + HATS + PERCS + SKANKS)


class Renderer:
    def __init__(self, w, h, fps, duration, audio, curve=True, seed=7,
                 palette="vert", subtitle=SUB_TXT):
        self.W, self.H = w, h
        self.fps = fps
        self.dur = duration
        self.tl = Timeline(duration)
        self.curve = curve
        self.seed = seed
        self.beat = float(audio.get("beat") or duration / 20.0)
        self.six = self.beat / 4.0           # la double-croche du morceau

        # la machine reste cadree quel que soit le format (16/9, carre, vertical)
        self.scale = min(h * 0.5, w * 0.5 / 1.30)
        fluo, halo, hotc, bg = PALETTES[palette]
        self.c_fluo, self.c_halo = fluo, halo
        self.c_hot = np.float32(hotc)
        self.c_bg = np.float32(bg)
        self._zoom = 1.0                     # respiration de l'image sur les kicks
        self._cam = (0.0, 0.0)               # camera : centre, puis dans l'ecran
        self._cam_z = 1.0
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
        # enveloppe crete du morceau : c'est le dessin du clip
        edges = np.linspace(0, len(mono), DAW_COLS + 1).astype(np.int64)
        env = np.array([np.abs(mono[a:b]).max() if b > a else 0.0
                        for a, b in zip(edges[:-1], edges[1:])])
        self.daw = (env / (env.max() or 1.0)) ** 0.82
        self.daw_x = np.linspace(CLIP[0] + 0.02, CLIP[2] - 0.02, DAW_COLS)

        ev = audio["events"]
        self.ev_t = np.array([e[0] for e in ev], dtype=np.float64)
        self.ev_pad = np.array([e[1] for e in ev], dtype=np.int32)
        self.ev_f = np.array([e[2] for e in ev], dtype=np.float64)
        self.ev_d = np.array([e[3] for e in ev], dtype=np.float64)
        self.ev_bass = self.ev_pad <= 3          # grosse caisse et notes de basse

        # ---- geometrie
        self.mpc = build_mpc()
        (self.tP, self.tN, self.tkind,
         self.ts, self.tlen) = build_title_curve("OMNIPOTARD", TITLE_H, 0.0)
        tx = self.tP[self.tkind == TRAIT][:, 0]
        self.word_x = (float(tx.min()), float(tx.max()))
        sub = text_paths(subtitle, SUB_H, 0.0, SUB_Y, tag="sub", tracking=SUB_TRACK)
        self.subP = np.vstack([q.P for q in sub])
        subN = []
        for q in sub:
            tan = np.gradient(q.P, axis=0)
            tan /= (np.linalg.norm(tan, axis=1, keepdims=True) + 1e-12)
            subN.append(np.stack([-tan[:, 1], tan[:, 0]], axis=1))
        self.subN = np.vstack(subN)
        self.tw = np.array([WEIGHT_OF[int(k)] for k in self.tkind])
        self.tth = np.array([THICK_OF[int(k)] for k in self.tkind])
        self.is_line = ((self.tkind == TRAIT) | (self.tkind == TRANSIT)).astype(np.float64)

        if curve:
            self._build_warp()

    # -- son ---------------------------------------------------------------

    def glitch_at(self, t):
        """Quantite de glitch a l'instant t : coups ponctuels, puis rafales
        continues pendant l'extinction."""
        f = self.dur / Timeline.REF
        g = 0.0
        for gt, gd in GLITCHES:
            gt, gd = gt * f, gd * f
            if 0.0 <= t - gt < gd:
                g = max(g, 1.0 - (t - gt) / gd)
        u = self.tl.at("out", t)
        if 0.0 <= u < 0.80:
            burst = 0.40 + 0.24 * math.sin(u * 31.0) ** 2
            g = max(g, burst * (1.0 - 0.55 * smoothstep(0.42, 0.80, u)))
        return g

    def env_at(self, arr, t):
        i = int(np.clip(t * self.eh, 0, len(arr) - 1))
        return float(arr[i])

    def wave_mod(self, x):
        """Amplitude de l'onde le long du fil : pleine au loin, presque nulle
        sous le mot, avec une transition douce — le trace reste continu."""
        a, b = self.word_x
        inside = smoothstep(a - 0.32, a + 0.02, x) * (1.0 - smoothstep(b - 0.02, b + 0.32, x))
        return 1.0 - 0.92 * inside

    def clip_env(self, x):
        """Demi-hauteur de la forme d'onde du clip a l'abscisse x."""
        return WAVE_YMAX * np.interp(x, self.daw_x, self.daw)

    def morph_at(self, x, sweep_x):
        """0 = encore ecrase dans le clip, 1 = deploye en machine.

        La zone de transition est large : a un instant donne, une bonne partie
        de la machine est en train de s'ouvrir, ce qui donne une materialisation
        progressive plutot qu'un volet net.
        """
        m = np.clip((sweep_x - np.asarray(x) + 0.10) / 0.85, 0.0, 1.0)
        return m * m * (3.0 - 2.0 * m)

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

    def kick_hit(self, t):
        """Enveloppe des grosses caisses seules : sert au zoom de l'image."""
        dt = t - self.ev_t
        m = (dt >= 0.0) & (dt < 0.45) & (self.ev_pad == PAD_OF["kick"])
        if not np.any(m):
            return 0.0
        return float(np.max(self.ev_f[m] * np.exp(-9.0 * dt[m])))

    def bass_hit(self, t):
        """Enveloppe des coups graves : le fil d'onde s'allume dessus."""
        dt = t - self.ev_t
        m = (dt >= 0.0) & (dt < 1.0) & self.ev_bass
        if not np.any(m):
            return 0.0
        return float(np.max(self.ev_f[m] * np.exp(-self.ev_d[m] * 1.15 * dt[m])))

    def step_index(self, t):
        return int((t - self.tl.start("groove")) / self.six) % 16

    # -- geometrie ecran ---------------------------------------------------

    def to_px(self, P, collapse=1.0, shake=(0.0, 0.0)):
        s = self.scale * self._zoom * self._cam_z
        cx, cy = self._cam
        return (self.W * 0.5 + (P[:, 0] - cx) * s + shake[0],
                self.H * 0.5 - (P[:, 1] - cy) * s * collapse + shake[1])

    def in_screen(self, P):
        """Composition -> monde, posee dans l'ecran de la machine.

        Renvoie aussi le masque de decoupe : une dalle n'affiche que ce qui
        tient dedans."""
        yc = P[:, 1] - SCR_OY
        m = (np.abs(P[:, 0]) <= SCR_HW) & (np.abs(yc) <= SCR_HH)
        Q = np.empty_like(P)
        Q[:, 0] = SCR_C[0] + P[:, 0] * SCR_S
        Q[:, 1] = SCR_C[1] + yc * SCR_S
        return Q, m

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
        """Couche animee de la machine. Le poids est compense par le zoom de
        camera : un trait du monde s'etale sur d'autant plus de pixels."""
        w = w * self._cam_z
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

    def body_mask(self, x, sweep_x, melt):
        """1 la ou le chassis de la machine masque le fil d'onde."""
        inside = (smoothstep(BODY[0] - 0.05, BODY[0] + 0.03, x)
                  * (1.0 - smoothstep(BODY[2] - 0.03, BODY[2] + 0.05, x)))
        return inside * self.morph_at(x, sweep_x) * (1.0 - melt)

    def _wave_line(self, beam, t, collapse, alpha, xf=None, sweep_x=None,
                   melt=0.0, thick=True):
        """Le fil du morceau : il entre par la gauche, disparait derriere la
        machine et ressort a droite — la MPC est un morceau de la bande."""
        if alpha <= 0.01:
            return
        xs = np.linspace(-1.88, 1.88, 2600)
        a = np.full(len(xs), float(alpha))
        if xf is not None:
            a *= smoothstep(xf - 0.16, xf + 0.02, xs)
        if sweep_x is not None:
            a *= 1.0 - self.body_mask(xs, sweep_x, melt)
            a *= self.morph_at(xs, sweep_x)      # nait a mesure que le clip fond
        hit = self.bass_hit(t)
        a = a * self._cam_z
        P = np.stack([xs, self.wave_y(xs, t)], axis=1)
        px, py = self.to_px(P, collapse)
        beam.add(px, py, 0.85 * a * (1.0 + 0.85 * hit))
        if thick:
            for dy in (0.0035, -0.0035):
                px, py = self.to_px(P + np.array([0.0, dy]), collapse)
                beam.add(px, py, 0.35 * a * (1.0 + 0.7 * hit))
        if hit > 0.15:                            # halo sur les coups graves
            for dy in (0.012, -0.012, 0.022, -0.022):
                px, py = self.to_px(P + np.array([0.0, dy]), collapse)
                beam.add(px, py, 0.30 * a * hit)

    def _daw_clip(self, beam, t, collapse, sweep_x, rng):
        """Ouverture : une piste qui s'enregistre, facon station de travail.

        La tete d'enregistrement remplit la forme d'onde de gauche a droite,
        puis le balayage de l'oscilloscope efface le clip en devoilant la
        machine — la MPC sort litteralement du morceau enregistre.
        """
        tl = self.tl
        b0, b1 = tl.start("boot"), tl.end("boot")
        x0, y0, x1, y1 = CLIP
        rec = np.clip((t - (b0 + 0.16 * (b1 - b0))) / (0.84 * (b1 - b0)), 0.0, 1.0)
        head = x0 + (x1 - x0) * rec
        gone = sweep_x - 0.24 if t >= tl.start("sweep") else -9.0   # cadre efface
        frame_a = (smoothstep(b0, b0 + 0.16 * (b1 - b0), t)
                   * (1.0 - smoothstep(tl.start("sweep"), tl.start("sweep")
                                       + 0.30 * (tl.end("sweep") - tl.start("sweep")), t)))
        if frame_a <= 0.01 and rec >= 1.0 and gone > x1:
            return

        # --- cadre du clip, bandeau de titre, reglure temporelle
        if frame_a > 0.01:
            for pts in (rrect_pts(x0, y0, x1, y1, 0.02),
                        rrect_pts(x0, y1, x1, y1 + CLIP_BAR_H, 0.02)):
                P, _, _ = resample(np.vstack([pts, pts[:1]]))
                m = P[:, 0] > gone
                px, py = self.to_px(P[m], collapse)
                beam.add(px, py, 0.50 * frame_a)
            for q in text_paths("AUDIO 01", 0.075, x0 + 0.05, y1 + 0.025,
                                center=False, tag="clip"):
                m = q.P[:, 0] > gone
                px, py = self.to_px(q.P[m], collapse)
                beam.add(px, py, 0.60 * frame_a)
            for k in range(33):
                gx = x0 + (x1 - x0) * k / 32.0
                if gx <= gone:
                    continue
                h = 0.055 if k % 4 == 0 else 0.028
                P, _, _ = resample([(gx, y1), (gx, y1 - h)])
                px, py = self.to_px(P, collapse)
                beam.add(px, py, (0.55 if k % 4 == 0 else 0.32) * frame_a)
            # temoin d'enregistrement
            if rec < 1.0 and (math.sin(t * 22.0) > -0.2):
                P = np.vstack([circle_pts(x1 - 0.10, y1 + 0.06, r, 60)
                               for r in (0.010, 0.018, 0.028)])
                px, py = self.to_px(P, collapse)
                beam.add(px, py, 1.1 * frame_a)

        # --- ligne de zero + forme d'onde
        xs = self.daw_x
        h = WAVE_YMAX * self.daw
        keep = xs <= head
        if not np.any(keep):
            return
        left = 1.0 - self.morph_at(xs, sweep_x) if t >= tl.start("sweep") else np.ones(len(xs))
        hh = h * left                                          # la matiere passe dans la machine
        keep = keep & (left > 0.01)
        if not np.any(keep):
            return
        P0 = np.stack([xs[keep], np.zeros(int(keep.sum()))], axis=1)
        px, py = self.to_px(P0, collapse)
        beam.add(px, py, 0.42 * left[keep])

        # remplissage en colonnes (comme les traits verticaux d'un editeur)
        K = 220
        u = np.linspace(-1.0, 1.0, K)
        sel = keep & (hh > 0.004)
        if np.any(sel):
            hs = hh[sel]
            X = np.repeat(xs[sel][:, None], K, axis=1).ravel()
            Y = (hs[:, None] * u[None, :]).ravel()
            w = np.repeat(0.62 * (2.0 * hs / K) / STEP * left[sel], K)
            px, py = self.to_px(np.stack([X, Y], axis=1), collapse)
            beam.add(px, py, w)
            # contours haut et bas, plus francs
            for sgn in (1.0, -1.0):
                px, py = self.to_px(np.stack([xs[sel], sgn * hs], axis=1), collapse)
                beam.add(px, py, 0.55 * left[sel])

        # --- tete d'enregistrement
        if 0.0 < rec < 1.0:
            ys = np.linspace(y0 - 0.03, y1 + CLIP_BAR_H, 620)
            for j in range(3):
                P = np.stack([np.full(len(ys), head - j * 0.018), ys], axis=1)
                px, py = self.to_px(P, collapse)
                beam.add(px, py, (1.25 if j == 0 else 0.30) * (0.6 ** j))

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
        ghost = 1.0 - smoothstep(tl.start("groove") - 0.25, tl.start("groove"), t)
        trem = (1.0 + 0.6 * e_low + 0.5 * self.bass_hit(t))  # le trait respire
        trem /= 0.45 + 0.55 * self._cam_z                    # sans enfler au zoom

        half = BODY[3]
        for p in self.mpc:
            mo = self.morph_at(p.P[:, 0], sweep_x)
            if mo.max() <= 0.003:
                continue
            # le trait n'est jamais parfaitement stable : c'est un faisceau,
            # pas un dessin. Il ondule doucement le long de son parcours, un
            # peu plus fort quand le grave pousse.
            wob = (0.0021 * np.sin(p.s * 8.5 + t * 2.4 + p.ph)
                   + 0.0013 * np.sin(p.s * 39.0 - t * 6.8 + p.ph * 2.3)) * trem
            P = p.P + p.N * wob[:, None]
            # au repos, chaque point est ecrase dans l'enveloppe du clip :
            # la machine se deplie hors de la forme d'onde enregistree.
            src = self.clip_env(P[:, 0]) * (P[:, 1] / half)
            P[:, 1] = src * (1.0 - mo) + p.P[:, 1] * mo
            w = 0.50 * pulse * mo + 0.055 * ghost * (1.0 - mo)   # liseré d'annonce
            w += 1.2 * np.exp(-((mo - 0.55) / 0.30) ** 2)            # front de mue
            tag = p.tag
            boost = 0.0
            if tag.startswith("pad"):
                k = int(tag[3:])
                if k in flashes:
                    boost = 1.7 * flashes[k]
            elif tag.startswith("step"):
                k = int(tag[4:])
                if k == step:
                    boost = 1.9
                elif k in STEP_LIT:
                    boost = 0.30
            elif tag == "strip":
                boost = 0.8 * e_high
            elif tag.startswith("qlink"):
                boost = 0.45 * e_low
            elif tag == "wheel":
                boost = 0.30 * e_full
            w = w + boost * mo
            if melt > 0:
                w *= (1.0 - melt) ** 0.7
                P = self._melt(P, melt, t)
            px, py = self.to_px(P, collapse, (jx, 0.0))
            beam.add(px, py, w * self._cam_z)

        # ---- organes animes
        if melt >= 0.99:
            return

        # pads allumes : remplissage
        for k, v in flashes.items():
            x0, _, _, _ = pad_rect(k // 4, k % 4)
            mk = float(self.morph_at(x0, sweep_x))
            if mk > 0.4 and v > 0.05:
                self._dyn(beam, pad_fill(k), 1.05 * v * mk, collapse, melt, t)

        # bande de 16 pas : le pas courant s'allume
        if live and 0 <= step < 16:
            x0, y0, x1, y1 = step_rect(step)
            mk = float(self.morph_at(x0, sweep_x))
            if mk > 0.4:
                self._dyn(beam, rect_fill(x0, y0, x1, y1, 4), 0.95 * mk, collapse, melt, t)

        # Q-Links : index qui tourne + bandeau qui se remplit
        for k, (cx, cy) in enumerate(QLINK):
            if self.morph_at(cx, sweep_x) < 0.5:
                continue
            v = np.clip(0.18 + 0.62 * (e_low if k % 2 == 0 else e_high)
                        + 0.20 * math.sin(t * 1.7 + k), 0.0, 1.0)
            a = math.radians(225.0 - 270.0 * v)
            P, _, _ = resample([(cx + QLINK_R * 0.36 * math.cos(a),
                                 cy + QLINK_R * 0.36 * math.sin(a)),
                                (cx + QLINK_R * 0.86 * math.cos(a),
                                 cy + QLINK_R * 0.86 * math.sin(a))])
            self._dyn(beam, P, 1.15, collapse, melt, t)

        # touch strip : curseur lumineux
        if self.morph_at(STRIP[0], sweep_x) > 0.5:
            sy = STRIP[1] + (STRIP[3] - STRIP[1]) * np.clip(0.12 + 0.8 * e_high, 0, 1)
            self._dyn(beam, rect_fill(STRIP[0] + 0.014, sy - 0.026,
                                      STRIP[2] - 0.014, sy + 0.026, 4),
                      0.85, collapse, melt, t)

        # ecran : forme d'onde du morceau + niveaux
        sx0, sy0, sx1, sy1 = SCREEN
        if self.morph_at(sx0, sweep_x) > 0.5 and t < tl.start("zoom"):
            m = 0.05
            x_hi = sx1 - m if self.morph_at(sx1, sweep_x) > 0.5 else min(sx1 - m, sweep_x)
            x_lo = sx0 + m
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
                    if bx > x_hi:
                        continue
                    P, _, _ = resample([(bx, base), (bx, base + 0.16 * v)])
                    self._dyn(beam, P, 0.8, collapse, melt, t)

    def title_front(self, t):
        """Position du front qui balaie le mot, de gauche a droite.

        Il ralentit sur la largeur du mot : les lettres se detachent alors une
        par une, au rythme des doubles-croches.
        """
        u = np.clip(self.tl.at("title", t), 0.0, 1.0)
        return float(np.interp(u, (0.0, 0.14, 0.86, 1.0), (-1.42, -1.14, 1.14, 1.42)))

    def _draw_title(self, beam, t, collapse, u_out, dx=0.0):
        """Le mot nait de la frequence, sur l'ecran de la machine : le front
        passe, l'onde s'efface derriere lui et chaque lettre s'en detache."""
        xf = self.title_front(t)
        wy = self.wave_y(self.tP[:, 0], t)
        k = np.clip((xf - self.tP[:, 0] + 0.055) / 0.185, 0.0, 1.0)
        k = k * k * (3.0 - 2.0 * k)

        P = self.tP.copy()
        P[:, 1] = wy * (1.0 - k) + (self.tP[:, 1] + self.wave_mod(P[:, 0]) * wy) * k
        P[:, 0] = P[:, 0] + dx

        # le fil est deja en place avant le passage du front : seuls les traits
        # de lettres montent en intensite au fur et a mesure.
        w = self.tw * np.where(self.is_line > 0, 0.10 + 0.90 * k, 1.0)
        w = w + 2.4 * np.exp(-((k - 0.62) / 0.26) ** 2) * self.is_line * (xf < 1.42)
        if xf >= 1.42:
            w = self.tw * (1.0 + 0.10 * self.env_at(self.e_low, t)
                           + 0.45 * self.bass_hit(t))
        if u_out > 0:
            w = w * max(0.0, 1.0 - u_out * 1.35)

        th = self.tth * np.where(self.is_line > 0, k, 1.0)
        for off, ow in ((0.0, 1.0), (1.0, 0.60), (-1.0, 0.60)):
            Q, m = self.in_screen(P + self.tN * (off * th)[:, None])
            if not np.any(m):
                continue
            px, py = self.to_px(Q[m], collapse)
            beam.add(px, py, (w * ow)[m])

        # le front de lecture, borne a la hauteur de la dalle
        if -1.41 < xf < 1.41:
            ys = np.linspace(-SCR_HH * 0.94, SCR_HH * 0.94, 620)
            taper = np.exp(-(ys / (SCR_HH * 0.66)) ** 4)
            for j in range(4):
                Q, m = self.in_screen(np.stack([np.full(len(ys), xf - j * 0.022), ys], axis=1))
                if not np.any(m):
                    continue
                px, py = self.to_px(Q[m], collapse)
                beam.add(px, py, (taper * (0.95 if j == 0 else 0.26) * (0.58 ** j))[m])
            dot = np.stack([np.full(60, xf), np.linspace(-0.02, 0.02, 60)
                            + float(self.wave_y(np.array([xf]), t)[0])], axis=1)
            Q, m = self.in_screen(dot)
            if np.any(m):
                px, py = self.to_px(Q[m], collapse)
                beam.add(px, py, 2.2)

    def _draw_sub(self, beam, t, collapse, u_out, dx=0.0):
        """HARDWARE ONLY : volet lumineux qui passe juste apres le mot."""
        tl = self.tl
        span = tl.end("title") - tl.start("title")
        t0 = tl.start("title") + 0.74 * span
        u = float(np.clip((t - t0) / (0.36 * span), 0.0, 1.0))
        if u <= 0.0:
            return
        xw = -0.60 + 1.28 * ease_out(u, 2.2)
        k = np.clip((xw - self.subP[:, 0] + 0.03) / 0.10, 0.0, 1.0)
        k = k * k * (3.0 - 2.0 * k)
        P = self.subP.copy()
        P[:, 1] = P[:, 1] + 0.030 * self.wave_y(P[:, 0], t)
        P[:, 0] = P[:, 0] + dx
        w = 0.78 * k + 1.30 * np.exp(-((k - 0.60) / 0.30) ** 2) * (u < 1.0)
        if u_out > 0:
            w = w * max(0.0, 1.0 - u_out * 1.35)
        th = (0.0032 * k)[:, None]
        for off, ow in ((0.0, 1.0), (1.0, 0.55), (-1.0, 0.55)):
            Q, m = self.in_screen(P + self.subN * (off * th))
            if not np.any(m):
                continue
            px, py = self.to_px(Q[m], collapse)
            beam.add(px, py, (w * ow)[m])

    # -- image -------------------------------------------------------------

    def intensity(self, t):
        tl = self.tl
        beam = Beam(self.H, self.W, self.gain)
        rng = np.random.default_rng(self.seed + int(t * self.fps + 0.5))

        # l'image respire sur chaque grosse caisse pendant que la machine joue
        self._zoom = 1.0 + 0.020 * self.kick_hit(t)
        # puis la camera entre dans l'ecran de la machine
        kz = ease_in_out(float(np.clip(tl.at("zoom", t), 0.0, 1.0)))
        self._cam = (SCR_C[0] * kz, SCR_C[1] * kz)
        self._cam_z = 1.0 + (CAM_Z - 1.0) * kz

        collapse = 1.0
        u_out = tl.at("out", t)
        if u_out > 0.30:
            collapse = max(0.028,
                           (1.0 - ease_in_out(min(1.0, (u_out - 0.30) / 0.42))) ** 1.3)

        shake = self.glitch_at(t)

        grid_a = smoothstep(0.05, 0.55, t) * (1.0 - kz)      # le reticule reste dehors
        self._grid(beam, t, grid_a * (1.0 if u_out <= 0 else max(0.0, 1 - u_out * 2)), collapse)
        self._hud(beam, t, collapse,
                  smoothstep(0.15, 0.6, t) * (1.0 - smoothstep(tl.start("zoom"),
                                                               tl.end("zoom"), t)))

        # ---- 1 et 2. le clip qui s'enregistre, puis le balayage
        u_sweep = tl.at("sweep", t)
        us = np.clip(u_sweep, 0, 1)
        sweep_x = -1.85 + 4.55 * (0.55 * us + 0.45 * ease_in_out(us)) if u_sweep > 0 else -1.85
        if t < tl.end("sweep"):
            self._daw_clip(beam, t, collapse, sweep_x, rng)

        # ---- 3. la machine
        melt = 0.0                       # la machine ne se dissout plus : on y entre
        if u_sweep > 0:
            self._machine(beam, t, collapse, sweep_x, melt, rng, shake)

        # tete de balayage
        if 0.0 < u_sweep < 1.02:
            n = 900
            ys = np.linspace(-0.95, 0.95, n)
            taper = np.exp(-(ys / 0.72) ** 4)      # bords fondus : une tete de
            for k in range(4):                     # lecture, pas un volet net
                jit = 0.004 * np.sin(ys * 60 + t * 40) if k == 0 else 0.0
                P = np.stack([np.full(n, sweep_x - k * 0.030) + jit, ys], axis=1)
                px, py = self.to_px(P, collapse)
                beam.add(px, py, taper * (0.85 if k == 0 else 0.28) * (0.62 ** k))

        # ---- 4. la forme d'onde du morceau
        a_wave = 0.0
        if t >= tl.start("groove"):
            a_wave = 0.48 * smoothstep(tl.start("groove"), tl.start("groove") + 0.4, t)
        if t >= tl.start("zoom"):
            a_wave *= 1.0 - smoothstep(tl.start("zoom"), tl.start("zoom") + 0.45, t)
        if u_out > 0:
            a_wave *= max(0.0, 1.0 - u_out * 1.6)
        self._wave_line(beam, t, collapse, a_wave, None, sweep_x, melt)

        # ---- 5. le titre, ecrit par la courbe
        if t >= tl.start("zoom") and u_out < 0.95:
            dx = shake * float(rng.uniform(-0.055, 0.055)) if u_out > 0 else 0.0
            self._draw_title(beam, t, collapse, max(0.0, u_out), dx)
            self._draw_sub(beam, t, collapse, max(0.0, u_out), dx)

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
            img[:, :, c] = self.c_fluo[c] * base + self.c_halo[c] * np.clip(glow, 0, 3.0) * 0.55
        img += (np.clip(hot * 1.25, 0, 1.0) ** 1.25)[..., None] * self.c_hot
        # le fond passe sous les textures : scanlines, vignettage et grain
        # le travaillent comme le reste de la dalle.
        img += self.c_bg

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

        gl = self.glitch_at(t)
        if gl > 0.02:
            # tranches decalees
            for _ in range(int(3 + 18 * gl)):
                y0 = int(rng.integers(0, H - 4))
                y1 = min(H, y0 + int(rng.integers(3, max(6, int(H * 0.10 * gl) + 5))))
                off = int(rng.integers(-int(W * 0.10 * gl) - 2, int(W * 0.10 * gl) + 3))
                img[y0:y1] = np.roll(img[y0:y1], off, axis=1)
            # tranches recopiees ailleurs (datamosh)
            if gl > 0.55:
                for _ in range(int(5 * gl)):
                    h = int(rng.integers(4, max(8, int(H * 0.09))))
                    y0 = int(rng.integers(0, H - h))
                    ys = int(rng.integers(0, H - h))
                    img[y0:y0 + h] = img[ys:ys + h]
            # pertes de signal
            if gl > 0.45:
                for _ in range(int(4 * gl)):
                    h = int(rng.integers(2, max(5, int(H * 0.05))))
                    y0 = int(rng.integers(0, H - h))
                    img[y0:y0 + h] *= float(rng.uniform(0.0, 0.30))
            sh = max(1, int(14 * gl))
            img[:, :, 0] = np.roll(img[:, :, 0], sh, axis=1)
            img[:, :, 2] = np.roll(img[:, :, 2], -sh, axis=1)

        if self.curve:
            img = self._warp(img)

        u_out = self.tl.at("out", t)
        if u_out > 0:
            f = 1.0 if u_out <= 0.72 else max(0.0, 1.0 - (u_out - 0.72) / 0.20)
            img *= f
            if 0.66 < u_out < 0.80:
                cy, cx = H // 2, W // 2
                r = max(2, int(H * 0.006))
                img[cy - r:cy + r, cx - int(r * 2.5):cx + int(r * 2.5)] += 0.9

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
    ap.add_argument("--music", default=MUSIC_PATH,
                    help="morceau a utiliser ; vide ou --synth pour la musique de synthese")
    ap.add_argument("--music-start", type=float, default=MUSIC_START,
                    help="debut de l'extrait dans le morceau (s)")
    ap.add_argument("--synth", action="store_true", help="force la bande-son de synthese")
    ap.add_argument("--palette", default="vert", choices=sorted(PALETTES),
                    help="couleur du trace (et fond de dalle pour bleu-fond)")
    ap.add_argument("--subtitle", default=SUB_TXT, help="ligne sous le logo")
    ap.add_argument("--no-curve", action="store_true", help="desactive la courbure CRT")
    ap.add_argument("--no-audio", action="store_true", help="video muette (l'image reste pilotee par le son)")
    ap.add_argument("--stills", default="", help="dossier ou exporter des images cles PNG")
    ap.add_argument("--still-times", default="0.9,1.7,2.1,2.6,4.2,6.2,7.6,8.4,9.1,10.0,10.8")
    args = ap.parse_args()

    if args.synth or not args.music or not os.path.exists(args.music):
        if args.music and not args.synth and not os.path.exists(args.music):
            print("morceau introuvable (%s) -> bande-son de synthese" % args.music, flush=True)
        audio = synth_audio(args.duration, seed=args.seed)
    else:
        audio = load_music(args.music, args.duration, args.music_start, seed=args.seed)
        print("morceau %s  extrait a %.2f s  battement %.4f s (%.1f BPM)  %d coups detectes"
              % (args.music, args.music_start, audio["beat"], 60.0 / audio["beat"],
                 len(audio["events"])), flush=True)

    global _R
    _R = Renderer(args.width, args.height, args.fps, args.duration, audio,
                  curve=not args.no_curve, seed=args.seed,
                  palette=args.palette, subtitle=args.subtitle.upper())

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



# ==========================================================================
#  Morceau existant : chargement, analyse de la batterie, montage
#  L'image reste pilotee par le son — les pads suivent donc les vrais coups.
# ==========================================================================

def _decode(path, start, duration, sr=SR):
    """Decode un extrait en stereo flottant (recherche precise a l'echantillon)."""
    out = subprocess.run(
        ["ffmpeg", "-v", "error", "-i", path, "-ss", "%.6f" % start,
         "-t", "%.6f" % duration, "-ac", "2", "-ar", str(sr), "-f", "f32le", "-"],
        stdout=subprocess.PIPE, check=True).stdout
    st = np.frombuffer(out, dtype="<f4").astype(np.float64).reshape(-1, 2)
    n = int(duration * sr) + 1
    if len(st) < n:
        st = np.vstack([st, np.zeros((n - len(st), 2))])
    return st[:n]


def _frames(x, sr, hop=256, win=1024):
    n = max(1, (len(x) - win) // hop)
    idx = np.arange(n)[:, None] * hop + np.arange(win)[None, :]
    S = np.abs(np.fft.rfft(x[idx] * np.hanning(win), axis=1))
    return S, np.fft.rfftfreq(win, 1.0 / sr), sr / hop


def _band_flux(S, freqs, lo, hi):
    e = S[:, (freqs >= lo) & (freqs < hi)].sum(axis=1)
    return np.maximum(np.diff(e, prepend=e[0]), 0.0)


def _pick(flux, fps, thresh=2.0, gap=0.055):
    """Sommets d'une courbe d'attaque -> (instant, force)."""
    f = _lowpass(flux / (flux.std() + 1e-9), 3)
    cand = np.where((f[1:-1] > thresh) & (f[1:-1] >= f[:-2]) & (f[1:-1] >= f[2:]))[0] + 1
    out, last = [], -9.0
    for i in cand:
        t = i / fps
        if t - last >= gap:
            out.append((t, float(min(1.0, f[i] / (thresh * 2.6)))))
            last = t
    return out


def detect_beat(mono, sr):
    """Periode du battement, par autocorrelation de la courbe d'attaque."""
    S, freqs, fps = _frames(mono, sr)
    onset = sum(_band_flux(S, freqs, lo, hi) / (_band_flux(S, freqs, lo, hi).std() + 1e-9)
                for lo, hi in ((30, 140), (160, 1200), (4000, 10000)))
    o = onset - onset.mean()
    ac = np.correlate(o, o, mode="full")[len(o) - 1:]
    lags = np.arange(len(ac)) / fps
    sel = (lags > 0.30) & (lags < 1.10)
    # ponderation : un extrait court fait ressortir la demi-periode, alors on
    # privilegie les tempos plausibles (autour de 100 BPM) avant de choisir.
    pref = np.zeros_like(ac)
    pref[sel] = np.exp(-0.5 * (np.log(lags[sel] / 0.62) / 0.55) ** 2)
    best = int(np.argmax(ac * pref))
    for _ in range(2):                    # et on remonte encore d'une octave
        dbl = best * 2                    # si le double tient presque aussi bien
        if dbl < len(ac) and lags[dbl] < 1.10 and ac[dbl] > 0.62 * ac[best]:
            best = dbl
    return float(lags[best])


def detect_hits(mono, sr):
    """Coups de batterie par bande -> evenements de pads.

    grave -> grosse caisse, medium -> caisse claire et percussions,
    aigu -> charleston. Chaque famille garde le meme pad, pour qu'on
    reconnaisse l'instrument a l'endroit ou il s'allume.
    """
    S, freqs, fps = _frames(mono, sr)
    lag = 0.025          # la detection voit l'attaque au debut de sa fenetre
    ev = []
    for (lo, hi), kind, thresh, gap in (((30, 140), "kick", 2.1, 0.14),
                                        ((160, 1200), "rim", 2.3, 0.10),
                                        ((4000, 10000), "hat", 2.0, 0.055)):
        for i, (t, f) in enumerate(_pick(_band_flux(S, freqs, lo, hi), fps, thresh, gap)):
            if kind == "kick":
                pads = (PAD_OF["kick"],) if f > 0.45 else (1,)
            elif kind == "rim":
                pads = (PAD_OF["rim"],) if f > 0.55 else (PAD_OF["perc"],)
            else:
                pads = (10, 11)[i % 2],
            for pad in pads:
                ev.append((t + lag, pad, max(0.30, f), DECAY_OF[kind]))
    ev.sort()
    return ev


def _ramp(t, pts):
    return np.interp(t, [p[0] for p in pts], [p[1] for p in pts])


def load_music(path=MUSIC_PATH, duration=DUREE_REF, start=MUSIC_START, sr=SR, seed=3):
    """Monte un extrait du morceau sur la scenographie, y ajoute les FX de
    synthese (souffles, impact, sortie) et en extrait la batterie."""
    tl = Timeline(duration)
    st = _decode(path, start, duration, sr)
    n = len(st)
    mono_src = st.mean(axis=1)
    beat = detect_beat(mono_src, sr)
    events = detect_hits(mono_src, sr)

    t = np.arange(n) / sr
    g0 = tl.start("groove")
    sw0, sw1 = tl.start("sweep"), tl.end("sweep")
    m0, t0, o0 = tl.start("zoom"), tl.start("title"), tl.start("out")

    # --- montage : le morceau est mat avant le drop, evide pendant le break
    dull = np.stack([_lowpass(st[:, c], 42) for c in range(2)], axis=1)
    thin = st - np.stack([_lowpass(st[:, c], 30) for c in range(2)], axis=1)
    # le morceau reste audible des le debut : juste mat et un peu en retrait,
    # il s'ouvre progressivement au lieu de sauter au drop.
    a_dull = _ramp(t, [(0, .58), (g0 - 0.60, .50), (g0 - 0.05, .12), (g0, 0)])[:, None]
    a_thin = _ramp(t, [(0, 0), (m0 - 0.02, 0), (m0 + 0.10, .85), (t0 - 0.12, .85),
                       (t0, 0)])[:, None]
    gain = _ramp(t, [(0, .72), (g0 - 0.60, .80), (g0 - 0.02, .90), (g0, 1.0),
                     (m0, 1.0), (m0 + 0.10, .66),
                     (t0, 1.0), (o0, 1.0), (o0 + 0.16, 0.0)])[:, None]
    mix = (st * (1.0 - a_dull - a_thin) + dull * a_dull + thin * a_thin) * gain

    # --- les FX, conserves tels quels
    rng = np.random.default_rng(seed)
    fx = np.zeros(n)

    def add(sig, at, g=1.0):
        i0 = max(0, int(at * sr))
        i1 = min(n, i0 + len(sig))
        if i1 > i0:
            fx[i0:i1] += sig[:i1 - i0] * g

    add(_whoosh(sw1 - sw0, sr, rng, up=True), sw0, 0.55)     # souffle pendant
    #                                       que la machine apparait (fenetre sweep)
    add(_whoosh(max(0.2, t0 - m0), sr, rng, up=True), m0, 0.17)         # entree dans
    #                                          l'ecran : le souffle passe a 15 %
    ti = np.arange(int(min(3.0, duration - t0) * sr)) / sr              # impact du titre
    fi = 30.0 + 120.0 * np.exp(-ti * 9.0)                                   # (bass tres attenuee)
    imp = np.sin(2 * math.pi * np.cumsum(fi) / sr) * np.exp(-ti * 1.9) * 0.22
    imp += _lowpass(rng.standard_normal(len(ti)), 12) * np.exp(-ti * 3.5) * 0.07
    add(imp, t0)
    add(_whoosh(0.55, sr, rng, up=True), t0 - 0.55, 0.13)  # tres leger souffle qui
    #                                             atterrit pile quand le titre arrive
    add(_tv_off(max(0.20, duration - o0), sr, rng), o0, 1.0)            # extinction
    fx *= _ramp(t, [(0, 1), (duration - 0.04, 1), (duration, 0)])

    # un peu de reverbe sur les FX seuls : c'est ce qui les rend aeriens
    fx = fx + _fft_conv(fx, _reverb_ir(sr, dur=2.0, decay=1.0)) * 0.28
    mix += fx[:, None] * 0.74
    mix = _tanh_limit(mix * 0.92, 1.35)
    fade = (np.clip(t / 0.03, 0, 1) * np.clip((duration - t) / 0.10, 0, 1))[:, None]
    mix *= fade
    mix /= (np.max(np.abs(mix)) or 1.0) / 0.94

    m = mix.mean(axis=1)
    return {"stereo": (np.clip(mix, -1, 1) * 32767).astype("<i2"),
            "mono": m.astype(np.float32), "events": events, "sr": sr, "beat": beat}

if __name__ == "__main__":
    main()
