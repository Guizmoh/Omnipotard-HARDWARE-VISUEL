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

    fps, cadence = 30, 0

    def __init__(self, notes, transpose=0, tempo=1.0, offset=0.0):
        self.midi = np.asarray(notes, dtype=np.float64).reshape(-1, 4)
        self.midi_offset, self.midi_transpose = offset, transpose
        self.midi_force, self.midi_tempo = 1.0, tempo


def _eclat(m, t, k):
    """L'eclat de la touche k a l'instant t (0 si eteinte)."""
    return m.notes_midi(t, TOUCHES, NOTE0).get(k, 0.0)


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

    # 11. une note rejouee sur sa touche encore allumee : la touche s'eteint
    # juste avant, et sur toute une image, ou que tombe l'image
    k = 60 - NOTE0
    m = _Moteur([(1.0, 1.15, 60, 0.8), (1.176, 1.326, 60, 0.8)])
    attaque = _eclat(m, 1.1765, k)
    for avant in (0.002, 0.015, 0.030):
        ok(_eclat(m, 1.176 - avant, k) < 0.25 * attaque,
           "une note rejouee ne se detache pas : %.0f ms avant elle, la touche"
           " garde %.0f %% de son eclat" % (avant * 1000, 100 * _eclat(
               m, 1.176 - avant, k) / attaque))
    ok(_eclat(m, 1.176 - 0.10, k) > 0.5 * attaque,
       "le creux avant une note rejouee commence trop tot")
    # ... et seulement la meme hauteur : une autre note n'eteint rien
    m = _Moteur([(1.0, 1.5, 60, 0.8), (1.2, 1.5, 64, 0.8)])
    seule = _Moteur([(1.0, 1.5, 60, 0.8)])
    ok(abs(_eclat(m, 1.19, k) - _eclat(seule, 1.19, k)) < 1e-9,
       "une note qui commence ailleurs eteint le do")

    # 12. une note jouee doucement se voit quand meme
    fort = _eclat(_Moteur([(1.0, 1.3, 60, 100 / 127)]), 1.02, k)
    doux = _eclat(_Moteur([(1.0, 1.3, 60, 20 / 127)]), 1.02, k)
    ok(doux >= 0.5 * fort, "une note a 20 de velocite n'a que %.0f %% de"
       " l'eclat d'une note a 100" % (100 * doux / fort))
    ok(doux < fort, "la velocite ne change plus rien a l'eclat")

    # 13. la lecture du fichier : aucune note perdue, meme mal rangee
    fautes += _lecture()

    return fautes


def _ecrire(chemin, evenements, division=480):
    """Un fichier MIDI d'une piste, a 120 a la noire. `evenements` :
    (seconde, octets), ecrits dans l'ordre donne a instant egal."""
    def varlen(v):
        o = [v & 0x7F]
        v >>= 7
        while v:
            o.append((v & 0x7F) | 0x80)
            v >>= 7
        return bytes(reversed(o))
    corps, prec = bytearray(), 0
    for s, octets in evenements:
        tic = int(round(s * 2 * division))
        corps += varlen(tic - prec) + octets
        prec = tic
    corps += b"\x00\xff\x2f\x00"
    with open(chemin, "wb") as f:
        f.write(b"MThd" + (6).to_bytes(4, "big") + (0).to_bytes(2, "big")
                + (1).to_bytes(2, "big") + division.to_bytes(2, "big")
                + b"MTrk" + len(corps).to_bytes(4, "big") + bytes(corps))


def _lecture():
    """Les pieges d'ecriture qu'un fichier reel peut tendre au lecteur."""
    import tempfile
    on = lambda p, c=0: bytes([0x90 | c, p, 100])          # noqa: E731
    off = lambda p, c=0: bytes([0x80 | c, p, 0])           # noqa: E731
    ev = [
        # deux notes de meme hauteur qui se chevauchent (legato, pedale)
        (1.0, on(60)), (1.2, on(60)), (1.4, off(60)), (1.6, off(60)),
        # une note rejouee, le « on » ecrit avant le « off » au meme instant
        (2.0, on(62)), (2.5, on(62)), (2.5, off(62)), (3.0, off(62)),
        # la meme hauteur sur deux canaux d'une meme piste
        (3.5, on(64, 0)), (3.6, on(64, 1)), (3.8, off(64, 1)),
        (4.0, off(64, 0)),
        # une note de duree nulle, comme on ecrit souvent un coup
        (4.5, on(65)), (4.5, off(65)),
        # un meta, puis une note sans son octet de statut (hors norme)
        (5.0, on(67)), (5.1, b"\xff\x01\x03abc"), (5.2, bytes([67, 0])),
    ]
    attendu = [(1.0, 1.4, 60), (1.2, 1.6, 60), (2.0, 2.5, 62), (2.5, 3.0, 62),
               (3.5, 4.0, 64), (3.6, 3.8, 64), (4.5, 4.5, 65), (5.0, 5.2, 67)]
    fd, chemin = tempfile.mkstemp(suffix=".mid")
    os.close(fd)
    try:
        _ecrire(chemin, ev)
        lu = sorted((round(d, 3), round(f, 3), h) for d, f, h, _v in
                    M.lire_notes(chemin))
    finally:
        os.remove(chemin)
    fautes = []
    for n in attendu:
        if n not in lu:
            fautes.append("lecture : la note %s de %.2f a %.2f s est perdue"
                          % (M.nom_note(n[2]), n[0], n[1]))
    for n in lu:
        if n not in attendu:
            fautes.append("lecture : une note %s de %.2f a %.2f s apparait"
                          % (M.nom_note(n[2]), n[0], n[1]))
    return fautes


def verifier_image():
    """A l'image : ce que le fichier MIDI decide, le son ne le rallume pas.

    Les coups releves dans le son allumaient le contour des pads — et, sur le
    clavier, de touches groupees par deux ou trois — meme quand le fichier
    MIDI decidait du reste. Mesure avant correction : un coup de grosse caisse
    ajoutait 40 % de lumiere aux touches, plus qu'une vraie note du fichier.

    On rend donc de vraies images, avec un son muet ou ne tombent que trois
    coups a 1 s, et une note du fichier a 2 s ; on mesure la lumiere du
    faisceau sur les touches, ou sur les pads.
    """
    import mpc_performance as P
    fautes = []
    sr, duree = 22050, 3.0
    for mach, genre, notes in (
            ("minifreak", "piano", [(2.0, 2.3, 60, 0.8)]),
            ("minifreak", "batterie", [(2.0, 2.1, 36, 0.8)]),
            ("mpc", "batterie", [(2.0, 2.1, 36, 0.8)]),
            ("sp404", "batterie", [(2.0, 2.1, 36, 0.8)]),
            ("digitakt", "batterie", [(2.0, 2.1, 36, 0.8)])):
        son = {"sr": sr, "mono": np.zeros(int(sr * duree), np.float32),
               "beat": 0.5,
               "events": [(1.0, 0, 1.0, 6.0), (1.0, 5, 1.0, 6.0),
                          (1.0, 11, 1.0, 6.0)]}
        r = P.make_performance_renderer(
            480, 270, 30, duree, son, 0.0, [], curve=False, machine=mach,
            midi=notes, midi_type=genre, glitch=0.0, split=0.0)
        champ = {}
        couleur = r.colorize

        def garder(f, t, *a):
            champ["f"] = f
            return couleur(f, t, *a)
        r.colorize = garder
        mach_ = O.MACHINES[mach]
        zones = [O.MF_CLAV] if genre == "piano" else list(mach_["pads"])
        lum = []
        for t in (0.5, 1.02, 2.02):
            P.frame_performance(r, t, duree)
            masque = np.zeros((r.H, r.W), bool)
            for x0, y0, x1, y1 in zones:
                px, py = r.to_px(np.array([[x0, y0], [x1, y1]]), 1.0)
                masque[int(min(py)):int(max(py)) + 1,
                       int(min(px)):int(max(px)) + 1] = True
            lum.append(float(champ["f"][masque].sum()))
        coup, note = lum[1] / lum[0] - 1.0, lum[2] / lum[0] - 1.0
        quoi = "les touches" if genre == "piano" else "les pads"
        if coup > 0.05:
            fautes.append("%s en %s : un coup du son allume %s de %+.0f %%, "
                          "alors que le fichier ne joue rien"
                          % (mach, genre, quoi, 100 * coup))
        if note < 0.08:
            fautes.append("%s en %s : une note du fichier n'allume %s que de "
                          "%+.0f %%" % (mach, genre, quoi, 100 * note))
    return fautes


def fichier(chemin):
    """Le fichier reel : aucune note perdue a la lecture, et chaque note, a son
    attaque, allume une touche de sa classe de hauteur — detachee de la
    precedente quand elle rejoue la meme. Rend (notes verifiees, fautes,
    inventaire)."""
    notes = M.lire_notes(chemin)
    inv = M.inventaire(chemin)
    fautes, vues = [], 0
    attaques = sum(p["notes"] for p in inv)
    if attaques != len(notes):
        fautes.append("%d attaques dans le fichier, mais %d notes lues"
                      % (attaques, len(notes)))
    transpo = M.transposition(notes)
    m = _Moteur(notes, transpose=transpo)
    par_hauteur = {}
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
        # rejouee sur sa touche encore allumee, elle s'en detache
        if h not in par_hauteur:
            par_hauteur[h] = _Moteur([n for n in notes if n[2] == h],
                                     transpose=transpo)
        mh = par_hauteur[h]
        avant, apres = _eclat(mh, d - 0.002, k), _eclat(mh, d + 0.002, k)
        if avant > 0.25 * apres:
            fautes.append("%s a %.3f s ne se detache pas de la note d'avant"
                          % (M.nom_note(h), d))
        vues += 1
    return vues, fautes, inv


def main():
    fautes = verifier()
    fautes += verifier_image()
    print("suivi des notes : %s" % ("tout est juste" if not fautes
                                    else "%d defaut(s)" % len(fautes)))
    for f in fautes:
        print("    " + f)
    total = len(fautes)
    for chemin in sys.argv[1:]:
        n, f, inv = fichier(chemin)
        print("%s : %d notes, %s" % (os.path.basename(chemin), n,
                                     "toutes justes" if not f else "%d defaut(s)" % len(f)))
        print("    " + M.decrire(inv))
        if M.melange(inv):
            print("    attention : la batterie (canal 10) et d'autres notes "
                  "sont dans le meme fichier, et toutes allument le clavier")
        for x in f[:10]:
            print("    " + x)
        total += len(f)
    return 1 if total else 0


if __name__ == "__main__":
    sys.exit(main())
