#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Verifie qu'aucun organe d'une machine n'en chevauche un autre.

A l'oeil, sur une image de 960 pixels de large ou le trait fait deux pixels,
un texte pose sur un bouton ou un potard qui mord sur un pad ne se voient pas
— ou se voient une fois sur trois, selon l'instant rendu. Ils se voient en
revanche tres bien sur la video finale en 1080p, et il est alors trop tard.

On rasterise donc chaque organe separement, sur une grille assez fine pour
qu'une cellule vaille environ deux pixels en 1080p, et on croise les masques
deux a deux. Ce qui se touche est signale, avec l'endroit.

Trois familles de defauts sont cherchees :

  * **chevauchement** : deux organes dont les traits se croisent ;
  * **debordement** : un organe qui sort du chassis ;
  * **texte perdu** : un titre ou une marque qui sort du chassis, ce qui
    donne a l'image un mot coupe en plein vide.

Le chassis lui-meme est exclu des croisements : tout est dedans, c'est son
role. Les organes d'une meme famille non plus — les deux traits d'un pad se
doublent volontairement.
"""
import sys
import os

import numpy as np

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import omnipotard_intro as O                                   # noqa: E402

# une cellule vaut environ deux pixels en 1080p, ou une machine large de trois
# unites de monde occupe un peu moins de 1900 pixels
PAS = 0.0035
EPAISSEUR = 1          # cellules ajoutees de part et d'autre du trait


def _famille(tag):
    """Le nom de l'organe, sans son numero ni son detail de texte."""
    t = str(tag or "")
    if ":" in t:                        # « logo:O », « mark:3 »
        t = t.split(":")[0]
    return t.rstrip("0123456789") or t


def _masques(paths, boite):
    """Un masque booleen par organe, sur une grille commune."""
    x0, y0, x1, y1 = boite
    nx = max(4, int((x1 - x0) / PAS) + 1)
    ny = max(4, int((y1 - y0) / PAS) + 1)
    out = {}
    for p in paths:
        P = np.asarray(p.P, dtype=np.float64)
        if len(P) < 1:
            continue
        # on echantillonne le long du trait, pas seulement ses sommets : deux
        # points distants laisseraient le segment invisible au croisement
        seg = []
        for a, b in zip(P[:-1], P[1:]):
            d = float(np.hypot(*(b - a)))
            n = max(2, int(d / (PAS * 0.5)) + 1)
            seg.append(a + (b - a) * np.linspace(0.0, 1.0, n)[:, None])
        Q = np.vstack(seg) if seg else P
        ix = np.clip(((Q[:, 0] - x0) / PAS).astype(int), 0, nx - 1)
        iy = np.clip(((Q[:, 1] - y0) / PAS).astype(int), 0, ny - 1)
        m = out.get(_famille(p.tag))
        if m is None:
            m = out[_famille(p.tag)] = np.zeros((ny, nx), dtype=bool)
        m[iy, ix] = True
    if EPAISSEUR:
        for k, m in out.items():
            g = m.copy()
            for dy in range(-EPAISSEUR, EPAISSEUR + 1):
                for dx in range(-EPAISSEUR, EPAISSEUR + 1):
                    g |= np.roll(np.roll(m, dy, axis=0), dx, axis=1)
            out[k] = g
    return out, (nx, ny)


def verifier(nom):
    """Les defauts d'une machine, en clair. Liste vide = rien a signaler."""
    mach = O.MACHINES[nom]
    paths = mach["build"]()
    pts = np.vstack([np.asarray(p.P, dtype=np.float64) for p in paths])
    boite = (float(pts[:, 0].min()) - 0.05, float(pts[:, 1].min()) - 0.05,
             float(pts[:, 0].max()) + 0.05, float(pts[:, 1].max()) + 0.05)
    masques, _ = _masques(paths, boite)

    # le chassis : sa plus grande boite englobante
    corps = [p for p in paths if _famille(p.tag) == "body"]
    if not corps:
        return ["pas de chassis (aucun chemin marque « body »)"]
    cp = np.vstack([np.asarray(p.P, dtype=np.float64) for p in corps])
    bx0, by0 = float(cp[:, 0].min()), float(cp[:, 1].min())
    bx1, by1 = float(cp[:, 0].max()), float(cp[:, 1].max())

    fautes = []
    # --- debordements
    for p in paths:
        f = _famille(p.tag)
        if f == "body":
            continue
        P = np.asarray(p.P, dtype=np.float64)
        dehors = ((P[:, 0] < bx0) | (P[:, 0] > bx1)
                  | (P[:, 1] < by0) | (P[:, 1] > by1))
        if dehors.any():
            quoi = "texte" if f in ("logo", "mark") else "organe"
            fautes.append("%s « %s » sort du chassis (%d points dehors)"
                          % (quoi, p.tag, int(dehors.sum())))

    # --- le titre du morceau tient-il dans la dalle ?
    #
    # Il est raccourci a la lettre pres par fit_text, mais encore faut-il que
    # la dalle soit assez large pour en montrer quelque chose : sur un ecran
    # trop etroit le titre se reduit aux points de suspension, et l'on croit
    # a un defaut d'affichage.
    ecran = mach.get("ecran")
    if ecran is not None:
        large = (ecran[2] - 0.052) - (ecran[0] + 0.052)
        essai = "BOMBE ATOMIQUE N2 REMIX"
        # la meme hauteur que le moteur choisira : elle suit la largeur
        h = float(np.clip(large / (O.text_width(essai, 0.42) or 1.0),
                          0.030, 0.050))
        coupe = O.fit_text(essai, h, large, tracking=0.42)
        if O.text_width(coupe, 0.42) * h > large + 1e-6:
            fautes.append("le titre deborde de la dalle (%.3f pour %.3f)"
                          % (O.text_width(coupe, 0.42) * h, large))
        garde = len(coupe.rstrip(".").strip())
        if garde < 6:
            fautes.append("dalle trop etroite pour un titre : « %s » sur "
                          "%.2f de large, il n'en reste que %d lettres"
                          % (essai, large, garde))

    # --- chevauchements, chaque paire une fois
    noms = sorted(k for k in masques if k != "body")
    for i, a in enumerate(noms):
        for b in noms[i + 1:]:
            inter = masques[a] & masques[b]
            n = int(inter.sum())
            if n == 0:
                continue
            iy, ix = np.nonzero(inter)
            x = boite[0] + float(ix.mean()) * PAS
            y = boite[1] + float(iy.mean()) * PAS
            fautes.append("« %s » et « %s » se touchent sur %d cellules, "
                          "vers (%.2f, %.2f)" % (a, b, n, x, y))
    return fautes


def main():
    mauvais = 0
    for nom in O.NOMS_MACHINES:
        fautes = verifier(nom)
        etat = "rien a signaler" if not fautes else "%d defaut(s)" % len(fautes)
        print("%-11s %s" % (nom, etat))
        for f in fautes:
            print("    %s" % f)
        mauvais += len(fautes)
    print()
    print("tout est propre." if not mauvais
          else "%d defaut(s) au total." % mauvais)
    return 1 if mauvais else 0


if __name__ == "__main__":
    sys.exit(main())
