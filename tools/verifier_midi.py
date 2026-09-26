#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Verifie que chaque note d'un fichier MIDI allume la bonne touche, au bon
instant, et elle seule.

Le suivi des notes passe par quatre etapes qui peuvent chacune se tromper sans
que rien ne plante : l'ordre des touches du clavier (une noire rangee au
mauvais endroit decale toute l'octave), le repli par octaves des notes hors
clavier, la correction de derive, et la tenue plafonnee. Une erreur dans l'une
d'elles donne une video ou les touches s'allument — simplement pas les
bonnes. On ne le voit qu'en connaissant la melodie par coeur.

On appelle donc le vrai code du moteur, sur des fichiers dont on connait la
reponse, et on compare. Puis on repasse le fichier de l'utilisateur, note par
note.
"""
import os
import sys

import numpy as np

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import omnipotard_intro as O                                    # noqa: E402
import midi as M                                                # noqa: E402

NOIRES = {1, 3, 6, 8, 10}          # classes de hauteur des touches noires
NOTE0 = O.MACHINES["minifreak"]["note0"]
TOUCHES = O.MACHINES["minifreak"]["touches"]


class _Moteur:
    """Juste ce que notes_midi et notes_batterie lisent : le vrai code, sans
    construire tout le moteur ni analyser un morceau."""

    notes_midi = O.Renderer.__dict__["notes_midi"]
    notes_batterie = O.Renderer.__dict__["notes_batterie"]
    _notes_actives = O.Renderer.__dict__["_notes_actives"]

    def __init__(self, notes, transpose=0, tempo=1.0, offset=0.0):
        self.midi = np.asarray(notes, dtype=np.float64).reshape(-1, 4)
        self.midi_offset, self.midi_transpose = offset, transpose
        self.midi_force, self.midi_tempo = 1.0, tempo


def _allumees(m, t, batterie=False, n=16):
    d = m.notes_batterie(t, n) if batterie else m.notes_midi(t, TOUCHES, NOTE0)
    return {k for k, v in d.items() if v > 0.30}


def _est_noire(k):
    """Une noire s'arrete a mi-hauteur du clavier, une blanche descend
    jusqu'en bas : c'est ce qui les distingue dans la geometrie."""
    return TOUCHES[k][1] > O.MF_CLAV[1] + 0.05


def verifier():
    fautes = []
    ok = lambda cond, msg: None if cond else fautes.append(msg)

    # 1. l'ordre des touches : chaque noire doit tomber sur un demi-ton noir
    for k in range(len(TOUCHES)):
        attendu = ((NOTE0 + k) % 12) in NOIRES
        ok(_est_noire(k) == attendu,
           "touche %d (%s) : %s alors qu'elle devrait etre %s"
           % (k, M.nom_note(NOTE0 + k), "noire" if _est_noire(k) else "blanche",
              "noire" if attendu else "blanche"))

    # 2. chaque demi-ton du clavier, joue seul, allume sa touche et elle seule
    for k in range(len(TOUCHES)):
        m = _Moteur([(1.0, 1.4, NOTE0 + k, 0.8)])
        vu = _allumees(m, 1.02)
        ok(vu == {k}, "%s allume %s au lieu de la touche %d"
           % (M.nom_note(NOTE0 + k), sorted(vu), k))

    # 3. un accord allume ses trois touches ensemble
    m = _Moteur([(1.0, 1.5, 60, 0.8), (1.0, 1.5, 64, 0.8), (1.0, 1.5, 67, 0.8)])
    vu = _allumees(m, 1.05)
    ok(vu == {60 - NOTE0, 64 - NOTE0, 67 - NOTE0},
       "l'accord do-mi-sol allume %s" % sorted(vu))

    # 4. hors du clavier, la note garde sa classe de hauteur
    for h in (12, 24, 30, 90, 100, 115):
        m = _Moteur([(1.0, 1.4, h, 0.8)])
        vu = _allumees(m, 1.02)
        ok(len(vu) == 1, "%s (hors clavier) allume %d touches"
           % (M.nom_note(h), len(vu)))
        for k in vu:
            ok((NOTE0 + k) % 12 == h % 12,
               "%s replie sur %s : la note a change"
               % (M.nom_note(h), M.nom_note(NOTE0 + k)))

    # 5. l'instant : allumee a l'attaque, eteinte bien apres
    m = _Moteur([(2.0, 2.3, 60, 0.8)])
    ok(not _allumees(m, 1.98), "la touche s'allume avant sa note")
    ok(_allumees(m, 2.001) == {60 - NOTE0}, "la touche ne s'allume pas a l'attaque")
    ok(not _allumees(m, 2.9), "la touche reste allumee bien apres la fin")

    # 6. le decalage deplace la note sans la changer
    m = _Moteur([(5.0, 5.3, 60, 0.8)], offset=2.0)
    ok(_allumees(m, 3.01) == {60 - NOTE0}, "le decalage ne deplace pas la note")

    # 7. la derive : la premiere note reste en place, la suivante s'etire
    m = _Moteur([(4.0, 4.1, 60, 0.8), (14.0, 14.1, 62, 0.8)], tempo=1.01)
    ok(_allumees(m, 4.001) == {60 - NOTE0}, "la derive deplace la premiere note")
    ok(not _allumees(m, 14.02) and _allumees(m, 14.101) == {62 - NOTE0},
       "la derive n'etire pas la deuxieme note (attendue a 14,10)")

    # 8. une note tenue relache apres TENUE_MAX
    m = _Moteur([(1.0, 9.0, 60, 0.8)])
    ok(_allumees(m, 1.0 + O.TENUE_MAX - 0.05) == {60 - NOTE0},
       "la note tenue s'eteint trop tot")
    ok(not _allumees(m, 1.0 + O.TENUE_MAX + 0.40),
       "la note tenue reste allumee au-dela de %.1f s" % O.TENUE_MAX)

    # 9. batterie : un instrument, un pad ; la grosse caisse sur le premier
    kit = [36, 38, 42, 46, 49, 51]         # caisse, claire, charleys, cymbales
    notes = [(1.0 + i * 0.5, 1.1 + i * 0.5, h, 0.9) for i, h in enumerate(kit)]
    m = _Moteur(notes)
    for i, h in enumerate(kit):
        vu = _allumees(m, 1.0 + i * 0.5 + 0.002, batterie=True)
        ok(vu == {i}, "batterie : l'instrument %d allume %s au lieu du pad %d"
           % (h, sorted(vu), i))

    # 10. batterie : un instrument garde son pad, quoi qu'il joue avec lui
    m = _Moteur([(1.0, 1.1, 38, 0.9),
                 (2.0, 2.1, 36, 0.9), (2.0, 2.1, 38, 0.9)])
    seul = _allumees(m, 1.002, batterie=True)
    ensemble = _allumees(m, 2.002, batterie=True)
    ok(seul <= ensemble and len(ensemble) == 2,
       "batterie : la caisse claire change de pad selon ce qui joue avec elle")

    return fautes


def fichier(chemin):
    """Le fichier reel : chaque note, a son attaque, allume une touche de sa
    classe de hauteur. Rend (notes verifiees, fautes)."""
    notes = M.lire_notes(chemin)
    transpo = M.transposition(notes)
    m = _Moteur(notes, transpose=transpo)
    fautes, vues = [], 0
    for d, f, h, v in notes:
        attendu = (h + transpo) % 12
        seule = _Moteur([(d, f, h, v)], transpose=transpo)
        vu = _allumees(seule, d + 0.002)
        if len(vu) != 1:
            fautes.append("%s a %.3f s allume %d touches" % (M.nom_note(h), d, len(vu)))
            continue
        k = vu.pop()
        if (NOTE0 + k) % 12 != attendu:
            fautes.append("%s a %.3f s tombe sur %s" % (M.nom_note(h), d,
                                                       M.nom_note(NOTE0 + k)))
        # et dans le fichier entier, elle y est aussi
        if k not in _allumees(m, d + 0.002):
            fautes.append("%s a %.3f s disparait parmi les autres notes"
                          % (M.nom_note(h), d))
        vues += 1
    return vues, fautes


def main():
    fautes = verifier()
    print("suivi des notes : %s" % ("tout est juste" if not fautes
                                    else "%d defaut(s)" % len(fautes)))
    for f in fautes:
        print("    " + f)
    total = len(fautes)
    for chemin in sys.argv[1:]:
        n, f = fichier(chemin)
        print("%s : %d notes, %s" % (os.path.basename(chemin), n,
                                     "toutes justes" if not f else "%d defaut(s)" % len(f)))
        for x in f[:10]:
            print("    " + x)
        total += len(f)
    return 1 if total else 0


if __name__ == "__main__":
    sys.exit(main())
