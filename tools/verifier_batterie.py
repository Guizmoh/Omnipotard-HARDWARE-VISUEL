#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Verifie la reconnaissance de batterie sur un morceau dont on connait chaque coup.

Un morceau synthetique de seize mesures a 100 BPM : grosse caisse principale
et secondaire, caisse claire, clap, charley ferme et ouvert, crash, basse,
accords, toms, cloche et conga, une montee. Chaque coup est note avec sa
famille ; on compte, famille par famille, ceux que la reconnaissance retrouve
sur le bon pad (a 35 ms pres).

Les seuils sont ceux mesures au moment de la refonte, un peu en dessous :
une modification qui ferait perdre des coups se voit ici, pas sur une video.

    python3 tools/verifier_batterie.py
"""
import os
import sys

import numpy as np

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import omnipotard_intro as O                                   # noqa: E402

SR = 44100
# la famille de chaque pad, dans les classes du banc
CLASSE = {O.PAD_OF["kick"]: "K1", O.PAD_OF["kick2"]: "K2", O.PAD_OF["rim"]: "S1",
          O.PAD_OF["rim2"]: "S2", O.PAD_OF["hat"]: "HC", O.PAD_OF["hat2"]: "HO",
          O.PAD_OF["bass"]: "B", O.PAD_OF["inst"]: "I", O.PAD_OF["fx"]: "FX"}
CLASSE.update({p: "T" for p in O.PADS_TOMS})
CLASSE.update({p: "P" for p in O.PADS_PERCUS})
# coups retrouves au minimum (sur le total), et fausses alertes au maximum
SEUILS = {"K1": (30, 3), "K2": (6, 2), "S1": (22, 5), "S2": (6, 9),
          "HO": (10, 13), "B": (48, 5), "I": (13, 7), "P": (12, 7),
          "FX": (1, 1), "HC": (30, 20), "T": (2, 3)}


def morceau(bpm=100.0, mesures=16, graine=3, variante=0):
    """Le morceau (mono, -1..1) et la liste de ses coups : (instant, classe)."""
    rng = np.random.default_rng(graine)
    P = 60.0 / bpm
    st = P / 4
    dur = mesures * 4 * P + 2.0
    x = np.zeros(int(dur * SR))
    gt = []

    def pose(sig, t, g=1.0):
        i = int(round(t * SR))
        n = min(len(sig), len(x) - i)
        if n > 0:
            x[i:i + n] += g * sig[:n]

    def tt(d):
        return np.arange(int(d * SR)) / SR

    def bruit(n):
        return rng.uniform(-1, 1, n)

    def passe_haut(s, k=1):
        for _ in range(k):
            s = np.diff(s, prepend=0)
        return s

    def bande(s, lo, hi):
        X = np.fft.rfft(s)
        f = np.fft.rfftfreq(len(s), 1.0 / SR)
        X *= 1.0 / (1.0 + (lo / np.maximum(f, 1.0)) ** 4) / (1.0 + (f / hi) ** 4)
        return np.fft.irfft(X, len(s))

    def kick(f0=50, f1=140, chute=30, dec=8, clic=0.3):
        t = tt(0.45)
        f = f0 + f1 * np.exp(-t * chute)
        s = np.sin(2 * np.pi * np.cumsum(f) / SR) * np.exp(-t * dec)
        s += clic * passe_haut(bruit(len(t))) * np.exp(-t * 300)
        return s

    def snare():
        t = tt(0.25)
        return (0.9 * bande(bruit(len(t)), 1500, 9000) * np.exp(-t * 16)
                + 0.45 * np.sin(2 * np.pi * 200 * t) * np.exp(-t * 22))

    def clap():
        t = tt(0.22)
        env = np.zeros(len(t))
        for d in (0.0, 0.011, 0.022):
            env += (t >= d) * np.exp(-np.maximum(t - d, 0) * 120)
        env += (t >= 0.03) * np.exp(-np.maximum(t - 0.03, 0) * 14) * 0.6
        s = bande(bruit(len(t)), 900, 3500) * env * 2.0
        return s

    def hat(dec):
        t = tt(min(1.2, 6.0 / dec))
        return bande(bruit(len(t)), 6500, 16000) * np.exp(-t * dec) * 1.6

    def crash():
        t = tt(2.0)
        s = passe_haut(bruit(len(t)), 2) * np.exp(-t * 1.8)
        s += 0.3 * sum(np.sin(2 * np.pi * f * t) for f in (3150, 4870, 6230)) * np.exp(-t * 2.5)
        return s

    def note(f, d, kind="sine", att=0.02):
        t = tt(d)
        if kind == "sine":
            y = np.sin(2 * np.pi * f * t) + 0.15 * np.sin(4 * np.pi * f * t)
        else:
            y = sum(np.sin(2 * np.pi * f * k * t) / k for k in range(1, 8))
        env = np.minimum(1, t / att) * np.exp(-t * 2.5) * np.clip((d - t) / 0.05, 0, 1)
        return y * env

    def accord(fs, d):
        return sum(note(f, d, "saw", 0.004) for f in fs) * np.exp(-tt(d) * 2)

    def tom(f0):
        t = tt(0.5)
        f = f0 * (1 + 0.5 * np.exp(-t * 25))
        return np.sin(2 * np.pi * np.cumsum(f) / SR) * np.exp(-t * 7) + 0.05 * bruit(len(t)) * np.exp(-t * 40)

    def cloche():
        t = tt(0.35)
        return (np.sign(np.sin(2 * np.pi * 540 * t)) + np.sign(np.sin(2 * np.pi * 800 * t))) * 0.5 * np.exp(-t * 14)

    def conga():
        t = tt(0.3)
        return np.sin(2 * np.pi * 330 * t * (1 + 0.1 * np.exp(-t * 40))) * np.exp(-t * 18)

    def montee(d):
        t = tt(d)
        s = bruit(len(t))
        # un passe-bande qui monte : bruit derive puis module
        s = passe_haut(s, 1) * (t / d) ** 2
        return s

    hz = lambda p: 440.0 * 2 ** ((p - 69) / 12.0)
    nappe = [57, 60, 64]
    for m in range(mesures):
        t0 = m * 4 * P
        remplissage = (m % 4 == 3)
        # nappe tenue, discrete
        for p in nappe:
            pose(note(hz(p), 4 * P, "saw", 0.3) * np.exp(-tt(4 * P) * -2.0) * 0.02, t0)
        for k in range(16):
            t = t0 + k * st
            if k in (0, 8) and not (remplissage and k == 8 and variante):
                pose(kick(), t, 0.9); gt.append((t, "K1"))
            if k == 11 and m % 2 == 1:
                pose(kick(62, 90, 45, 14, 0.15), t, 0.45); gt.append((t, "K2"))
            if k in (4, 12) and not (remplissage and k == 12):
                pose(snare(), t, 0.5); gt.append((t, "S1"))
            if k == 14 and m % 2 == 0:
                pose(clap(), t, 0.45); gt.append((t, "S2"))
            if k % 2 == 0 and k != 6:
                pose(hat(60), t, 0.12 if k % 4 else 0.16); gt.append((t, "HC"))
            if k == 6:
                pose(hat(6), t, 0.12); gt.append((t, "HO"))
            if k == 0 and m % 4 == 0:
                pose(crash(), t, 0.22); gt.append((t, "CR"))
            if k in (3, 7, 10) or (k == 14 and m % 2):
                p = [28, 31, 33, 35][(k + m) % 4]
                pose(note(hz(p), 2 * st, "sine", 0.02), t, 0.5); gt.append((t, "B"))
            if k in (2, 10) and 4 <= m < 12:
                pose(accord([hz(69), hz(72), hz(76)], 0.45), t, 0.08); gt.append((t, "I"))
            if remplissage and k in (12, 13, 14, 15):
                pose(tom([200, 160, 125, 100][k - 12]), t, 0.5); gt.append((t, "T"))
            if 8 <= m and k == 5:
                pose(cloche(), t, 0.10); gt.append((t, "P"))
            if 8 <= m and k == 9:
                pose(conga(), t, 0.25); gt.append((t, "P"))
        if m in (6,):
            pose(montee(2 * 4 * P), t0, 0.25); gt.append((t0 + 8 * P, "FX"))
    x /= np.abs(x).max()
    gt.sort()
    return (x * 0.9).astype(np.float32), gt


CLASSES = ("K1", "K2", "S1", "S2", "HC", "HO", "CR", "B", "I", "T", "P", "FX")


def evaluer(gt, ev, pad_classe, tol=0.035):
    """Pour chaque classe : rappel (coups vus sur le bon pad) et coups en trop."""
    det = [(t, pad_classe(p)) for t, p, f, d in ev if p < 16]
    res = {}
    for c in CLASSES:
        g = [t for t, k in gt if k == c]
        d = np.array([t for t, k in det if k == c])
        vus = sum(1 for t in g if len(d) and np.min(np.abs(d - t)) <= tol)
        # en trop : detections de cette classe sans coup de cette classe pres
        gc = np.array(g)
        trop = sum(1 for t in d if not len(gc) or np.min(np.abs(gc - t)) > tol)
        res[c] = (len(g), vus, len(d), trop)
    return res


def main():
    x, gt = morceau()
    # le crash partage le pad du charley ouvert
    gt = [(t, "HO" if c == "CR" else c) for t, c in gt]
    ev = O.detect_hits(x, SR)
    res = evaluer(gt, ev, lambda p: CLASSE.get(p, "?"))
    fautes = []
    print("%-4s %6s %6s %8s" % ("", "coups", "vus", "en trop"))
    for c in CLASSES:
        if c == "CR":
            continue
        n, v, _d, trop = res[c]
        print("%-4s %6d %6d %8d" % (c, n, v, trop))
        mini, maxi = SEUILS.get(c, (0, 999))
        if v < mini:
            fautes.append("%s : %d coups retrouves sur %d (au moins %d attendus)"
                          % (c, v, n, mini))
        if trop > maxi:
            fautes.append("%s : %d fausses alertes (au plus %d)" % (c, trop, maxi))
    if fautes:
        print("\n%d defaut(s) :" % len(fautes))
        for f in fautes:
            print("  - " + f)
        return 1
    print("\ntout est en ordre.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
