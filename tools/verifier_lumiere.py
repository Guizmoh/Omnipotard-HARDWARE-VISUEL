#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Verifie la lumiere des coups : eclat des pads, couleur, textures.

Sur la MPC, la SP-404 et le Digitakt, la batterie ne se voyait presque pas :
un pad frappe recevait cinq a vingt fois moins de lumiere qu'une touche du
MiniFreak. Il en recoit maintenant autant, a surface egale, et trois reglages
s'y ajoutent : l'eclat, la couleur des coups et la texture des touches. Ce
sont des choses qui se derangent sans bruit — une texture qui perd la moitie
de sa lumiere, une couleur qui retombe dans le trait — d'ou ce controle.

On rend de vraies images, avec un son muet ou ne tombe qu'un coup, et l'on
mesure, sur le pad frappe, la lumiere du faisceau et celle des couleurs.

    python3 tools/verifier_lumiere.py
"""
import os
import sys

import numpy as np

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import omnipotard_intro as O                                   # noqa: E402
import mpc_performance as P                                    # noqa: E402

SR, DUREE, W, H = 22050, 3.0, 480, 270
COUP = 1.0                       # l'instant du coup
VU = COUP + 1.0 / 60.0           # l'image regardee, juste apres
PADS = ("mpc", "sp404", "digitakt")


def _plein(accent, forme):
    """Les trois canaux de la lumiere des coups, remis a la taille de l'image
    (le moteur ne les rend que dans le cadre des pads allumes)."""
    if accent is None:
        return None
    y0, x0, acc = accent
    out = [np.zeros(forme, np.float32) for _ in range(3)]
    for c in range(3):
        out[c][y0:y0 + acc.shape[1], x0:x0 + acc.shape[2]] = acc[c]
    return out


def _rendu(mach, events, t=VU, **kw):
    """Une image ; rend le renderer, l'image, le champ du trait et celui des
    couleurs (None quand la lumiere des coups a la couleur du trait)."""
    son = {"sr": SR, "mono": np.zeros(int(SR * DUREE), np.float32),
           "beat": 0.5, "events": events}
    r = P.make_performance_renderer(W, H, 30, DUREE, son, 0.0, [], curve=False,
                                    machine=mach, glitch=0.0, split=0.0,
                                    punch=0.0, **kw)
    vu = {}
    couleur = r.colorize

    def garder(f, tt, *a, **k):
        vu["champ"], vu["accent"] = f, _plein(k.get("accent"), f.shape)
        return couleur(f, tt, *a, **k)
    r.colorize = garder
    img = P.frame_performance(r, t, DUREE)
    return r, img, vu["champ"], vu["accent"]


def _zone(r, rect):
    x0, y0, x1, y1 = rect
    px, py = r.to_px(np.array([[x0, y0], [x1, y1]]), 1.0)
    return (slice(int(min(py)), int(max(py)) + 1),
            slice(int(min(px)), int(max(px)) + 1))


def _ajout(mach, k, force=1.0, **kw):
    """La lumiere qu'un coup ajoute a son pad : (trait, couleurs, pixels)."""
    r, _i, f0, _a = _rendu(mach, [], **kw)
    r, _i, f1, acc = _rendu(mach, [(COUP, k, force, 6.0)], **kw)
    z = _zone(r, O.MACHINES[mach]["pads"][k])
    trait = float(f1[z].sum() - f0[z].sum())
    coul = 0.0 if acc is None else float(sum(a[z].sum() for a in acc))
    return trait, coul, f1[z].size


def textures():
    """Chaque texture reste dans sa touche et porte la lumiere promise."""
    fautes = []
    for nom in PADS + ("minifreak",):
        mach = O.MACHINES[nom]
        bx, by = mach.get("pads_biseau", (0.016, 0.015))
        for genre in O.TEXTURES_TOUCHES:
            for r in mach["pads"][:3]:
                rect = (r[0] + bx, r[1] + by, r[2] - bx, r[3] - by)
                Pt, w = O.texture_touche(rect, genre)
                if not len(Pt):
                    fautes.append("%s / %s : texture vide" % (nom, genre))
                    continue
                x0, y0, x1, y1 = rect
                dehors = ((Pt[:, 0] < x0 - 1e-6) | (Pt[:, 0] > x1 + 1e-6)
                          | (Pt[:, 1] < y0 - 1e-6) | (Pt[:, 1] > y1 + 1e-6))
                if dehors.any():
                    fautes.append("%s / %s : %d points hors de la touche"
                                  % (nom, genre, int(dehors.sum())))
                m = 0.008
                aire = (x1 - x0 - 2 * m) * (y1 - y0 - 2 * m)
                attendu = O.lumiere_surface() * aire * O.GAIN_TEXTURE[genre]
                if abs(float(w.sum()) / attendu - 1.0) > 0.01:
                    fautes.append("%s / %s : %.0f %% de la lumiere promise"
                                  % (nom, genre, 100 * w.sum() / attendu))
    # sur le clavier, une blanche echancree : rien ne deborde sous les noires
    for k, (rect, contour, _f, _t) in enumerate(O.MF_CLAVIER[:12]):
        poly = np.asarray(contour, dtype=np.float64)
        for genre in O.TEXTURES_TOUCHES:
            Pt, _w = O.texture_touche(rect, genre, poly)
            if len(Pt) and not O._dans(Pt, poly).all():
                fautes.append("touche %d / %s : des points hors de sa forme"
                              % (k, genre))
    return fautes


def eclat():
    """Un pad frappe se voit autant qu'une touche du clavier, et l'eclat le
    regle sans toucher au reste."""
    fautes, lignes = [], []
    tr, _c, n = _ajout("minifreak", 4)
    ref = tr / n
    for nom in PADS:
        tr, _c, n = _ajout(nom, 5)
        rapport = (tr / n) / ref
        lignes.append("%s : un pad frappe recoit %.2f fois la lumiere d'une "
                      "touche du clavier, a surface egale" % (nom, rapport))
        if not 0.6 <= rapport <= 1.8:
            fautes.append("%s : un pad frappe recoit %.2f fois la lumiere "
                          "d'une touche (attendu de 0,6 a 1,8)" % (nom, rapport))
        for e in (0.0, 2.0):
            te, _c, _n = _ajout(nom, 5, eclat_pads=e)
            if abs(te / tr - e) > 0.06 * max(e, 1.0):
                fautes.append("%s : a l'eclat %.0f, le pad recoit %.2f fois "
                              "sa lumiere de l'eclat 1" % (nom, e, te / tr))
        # un coup qui retombe s'eteint en fondu, sans saut d'une image a
        # l'autre
        son = [(COUP, 5, 0.6, 6.0)]
        r, _i, f0, _a = _rendu(nom, [], t=0.5)
        z = _zone(r, O.MACHINES[nom]["pads"][5])
        fond = float(f0[z].sum())
        suite = [float(_rendu(nom, son, t=COUP + i / 30.0)[2][z].sum()) - fond
                 for i in range(0, 34)]
        haut = max(suite)
        sauts = [(suite[i] - suite[i + 1]) / haut for i in range(4, len(suite) - 1)]
        if max(sauts) > 0.12:
            fautes.append("%s : le pad s'eteint d'un coup (%.0f %% de sa "
                          "lumiere en une image)" % (nom, 100 * max(sauts)))
    return fautes, lignes


def couleurs():
    """La couleur choisie part dans la lumiere des coups, et y reste."""
    fautes = []
    for nom in PADS:
        tr, _c, _n = _ajout(nom, 5)
        # rouge pur : tout quitte le trait pour la couleur
        t2, c2, _n = _ajout(nom, 5, couleur_coups="libre",
                            couleur_coups_libre=(1.0, 0.0, 0.0))
        if abs(t2) > 0.05 * tr:
            fautes.append("%s : en couleur au choix, %.0f %% de la lumiere du "
                          "coup reste dans le trait" % (nom, 100 * t2 / tr))
        if abs(c2 / tr - 1.0) > 0.05:
            fautes.append("%s : en couleur au choix, la lumiere du coup vaut "
                          "%.2f fois celle du trait" % (nom, c2 / tr))
        # orange : il ne doit ni jaunir ni blanchir, meme frappe a fond
        r, img, _f, _a = _rendu(nom, [(COUP, 5, 1.0, 6.0)],
                                couleur_coups="libre",
                                couleur_coups_libre=(1.0, 0.48, 0.12))
        x0, y0, x1, y1 = O.MACHINES[nom]["pads"][5]
        cx, cy, dx, dy = (x0 + x1) / 2, (y0 + y1) / 2, (x1 - x0) / 4, (y1 - y0) / 4
        z = _zone(r, (cx - dx, cy - dy, cx + dx, cy + dy))
        px = img[z].reshape(-1, 3).astype(np.float64)
        px = px[px[:, 0] > 60]
        if not len(px):
            fautes.append("%s : le pad frappe en orange reste sombre" % nom)
        else:
            g = float(np.median(px[:, 1] / px[:, 0]))
            b = float(np.median(px[:, 2] / px[:, 0]))
            if not (0.36 <= g <= 0.65 and b <= 0.30):
                fautes.append("%s : l'orange frappe a fond devient (1, %.2f, "
                              "%.2f) — jauni ou blanchi" % (nom, g, b))
    # par instrument : la grosse caisse rouge, le charley cyan
    r, img, _f, acc = _rendu("mpc", [(COUP, 0, 1.0, 6.0), (COUP, 4, 1.0, 6.0)],
                             couleur_coups="instrument")
    for pad in (0, 4):
        fam = O.PAD_FAMILLE.get(pad)
        z = _zone(r, O.MACHINES["mpc"]["pads"][pad])
        vu = np.array([float(a[z].sum()) for a in acc])
        att = np.array(O.TEINTES[fam])
        if np.abs(vu / vu.max() - att / att.max()).max() > 0.03:
            fautes.append("mpc, pad %d (%s) : teinte %s au lieu de %s"
                          % (pad, fam, np.round(vu / vu.max(), 2),
                             np.round(att / att.max(), 2)))
    # au hasard : deux coups simultanes ne tirent pas la meme
    r, _i, _f, acc = _rendu("mpc", [(COUP, 2, 1.0, 6.0), (COUP, 7, 1.0, 6.0)],
                            couleur_coups="arc-en-ciel")
    tons = []
    for pad in (2, 7):
        z = _zone(r, O.MACHINES["mpc"]["pads"][pad])
        v = np.array([float(a[z].sum()) for a in acc])
        tons.append(v / v.max())
    if np.abs(tons[0] - tons[1]).max() < 0.1:
        fautes.append("au hasard : deux coups simultanes ont la meme couleur")
    # la batterie d'un fichier MIDI se colore par sa note General MIDI
    r, _i, _f, acc = _rendu("mpc", [], t=2.0 + 1.0 / 60.0,
                            midi=[(2.0, 2.1, 36, 0.9)], midi_type="batterie",
                            couleur_coups="instrument")
    if acc is None:
        fautes.append("batterie MIDI : la note ne part pas dans la couleur")
    else:
        v = np.array([float(a.sum()) for a in acc])
        att = np.array(O.TEINTES["grosse caisse"])
        if np.abs(v / v.max() - att / att.max()).max() > 0.03:
            fautes.append("batterie MIDI : la grosse caisse (36) n'est pas "
                          "rouge : %s" % np.round(v / v.max(), 2))
    return fautes


def main():
    fautes = textures()
    print("textures : %d genres sur 4 machines" % len(O.TEXTURES_TOUCHES))
    f, lignes = eclat()
    fautes += f
    for l in lignes:
        print(l)
    fautes += couleurs()
    if fautes:
        print("\n%d defaut(s) :" % len(fautes))
        for x in fautes:
            print("  - " + x)
        return 1
    print("\ntout est en ordre.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
