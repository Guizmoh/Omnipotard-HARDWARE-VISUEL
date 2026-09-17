# -*- coding: utf-8 -*-
"""Lire un fichier MIDI, sans rien installer.

Le studio a besoin d'une seule chose d'un fichier MIDI : la liste des notes,
avec pour chacune l'instant ou elle commence, celui ou elle s'arrete, sa
hauteur et sa force. Le format est simple et stable depuis quarante ans, et
la seule subtilite est la carte des tempos : les instants sont ecrits en
tics, et le nombre de tics par seconde change a chaque changement de tempo.
On lit donc d'abord tous les evenements en tics, puis on les convertit.

Les fichiers de format 0 (une piste) et 1 (plusieurs pistes simultanees) sont
lus ; le format 2 (pistes independantes) est lu comme du format 1, ce qui est
faux en theorie et sans consequence ici — personne n'en produit.
"""
import struct


class MidiIllisible(Exception):
    """Le fichier n'est pas un MIDI, ou il est abime."""


def _lire_varlen(bloc, i):
    """Un entier a longueur variable : sept bits par octet, le huitieme dit
    qu'il en reste."""
    v = 0
    for _ in range(4):
        if i >= len(bloc):
            raise MidiIllisible("fichier coupe")
        o = bloc[i]
        i += 1
        v = (v << 7) | (o & 0x7F)
        if not o & 0x80:
            return v, i
    raise MidiIllisible("nombre a rallonge")


def _pistes(data):
    """Decoupe le fichier en entete + blocs de piste."""
    if data[:4] != b"MThd":
        raise MidiIllisible("ce n'est pas un fichier MIDI")
    taille = struct.unpack(">I", data[4:8])[0]
    fmt, _n, division = struct.unpack(">HHH", data[8:14])
    i = 8 + taille
    blocs = []
    while i + 8 <= len(data):
        nom, lg = data[i:i + 4], struct.unpack(">I", data[i + 4:i + 8])[0]
        i += 8
        if nom == b"MTrk":
            blocs.append(data[i:i + lg])
        i += lg
    if not blocs:
        raise MidiIllisible("aucune piste")
    return fmt, division, blocs


def _evenements(bloc):
    """Les evenements d'une piste, en tics absolus.

    Rend (tic, genre, a, b) ou genre vaut "on", "off" ou "tempo".
    """
    out = []
    i, tic, statut = 0, 0, 0
    while i < len(bloc):
        d, i = _lire_varlen(bloc, i)
        tic += d
        if i >= len(bloc):
            break
        o = bloc[i]
        if o & 0x80:
            statut = o
            i += 1
        elif not statut:
            raise MidiIllisible("evenement sans statut")
        # ---- meta et systeme exclusif
        if statut == 0xFF:
            genre = bloc[i]
            i += 1
            lg, i = _lire_varlen(bloc, i)
            corps = bloc[i:i + lg]
            i += lg
            if genre == 0x51 and lg == 3:      # tempo : microsecondes / noire
                out.append((tic, "tempo",
                            (corps[0] << 16) | (corps[1] << 8) | corps[2], 0))
            continue
        if statut in (0xF0, 0xF7):
            lg, i = _lire_varlen(bloc, i)
            i += lg
            continue
        # ---- voix
        haut = statut & 0xF0
        n = 1 if haut in (0xC0, 0xD0) else 2
        if i + n > len(bloc):
            break
        a = bloc[i]
        b = bloc[i + 1] if n == 2 else 0
        i += n
        if haut == 0x90 and b > 0:
            out.append((tic, "on", a, b))
        elif haut == 0x80 or (haut == 0x90 and b == 0):
            out.append((tic, "off", a, 0))
    return out


def lire_notes(chemin, pistes=None):
    """Les notes d'un fichier MIDI : liste de (debut, fin, hauteur, force).

    Les instants sont en secondes depuis le debut du fichier, la hauteur est
    le numero de note MIDI (60 = do du milieu) et la force va de 0 a 1.

    `pistes` limite la lecture a certaines pistes, numerotees a partir de 0 ;
    None les prend toutes.
    """
    with open(chemin, "rb") as f:
        data = f.read()
    if len(data) < 14:
        raise MidiIllisible("fichier trop court")
    _fmt, division, blocs = _pistes(data)

    tous = []
    for k, bloc in enumerate(blocs):
        garde = pistes is None or k in pistes
        for tic, genre, a, b in _evenements(bloc):
            # les tempos comptent quelle que soit la piste : ils sont ecrits
            # sur la premiere et valent pour toutes
            if genre == "tempo" or garde:
                tous.append((tic, genre, a, b, k))
    # « off » avant « on » a tic egal : une note repetee se ferme puis rouvre
    tous.sort(key=lambda e: (e[0], 0 if e[1] == "tempo" else
                             (1 if e[1] == "off" else 2)))

    # ---- tics vers secondes
    if division & 0x8000:
        # division en images par seconde (SMPTE) : le tempo n'entre pas en jeu
        images = 256 - ((division >> 8) & 0xFF)
        par_tic = 1.0 / (images * (division & 0xFF))
        carte = None
    else:
        par_noire = division or 480
        carte = par_noire
        par_tic = None

    notes, ouvertes = [], {}
    sec, tic_prec, us_noire = 0.0, 0, 500000.0     # 120 a la noire par defaut
    for tic, genre, a, b, k in tous:
        if par_tic is not None:
            sec = tic * par_tic
        else:
            sec += (tic - tic_prec) * (us_noire / 1e6) / carte
            tic_prec = tic
        if genre == "tempo":
            us_noire = float(a) or 500000.0
            continue
        cle = (k, a)
        if genre == "on":
            ouvertes[cle] = (sec, b / 127.0)
        else:
            deb = ouvertes.pop(cle, None)
            if deb is not None and sec > deb[0]:
                notes.append((deb[0], sec, int(a), float(deb[1])))
    # une note laissee ouverte par un fichier mal ferme dure une seconde
    for (k, a), (deb, v) in ouvertes.items():
        notes.append((deb, deb + 1.0, int(a), float(v)))
    notes.sort()
    return notes


def resume(notes):
    """De quoi renseigner la page : combien de notes, quelle etendue, et
    surtout **a quel instant tombe la premiere**.

    C'est ce dernier chiffre qui permet de verifier le calage a l'oreille :
    si la premiere note est annoncee a 0:12 et que la melodie s'entend bien a
    0:12 dans le morceau, le fichier est a l'heure. Aucun calcul ne le dit
    mieux que cette comparaison-la.
    """
    if not notes:
        return {"notes": 0, "duree": 0.0, "debut": 0.0,
                "grave": 0, "aigu": 0, "median": 60}
    hauteurs = sorted(n[2] for n in notes)
    return {"notes": len(notes),
            "duree": max(n[1] for n in notes),
            "debut": min(n[0] for n in notes),
            "grave": hauteurs[0], "aigu": hauteurs[-1],
            "median": hauteurs[len(hauteurs) // 2]}


NOMS = ("do", "do#", "re", "re#", "mi", "fa", "fa#", "sol", "sol#", "la",
        "la#", "si")


def nom_note(p):
    """La hauteur, ecrite comme on la lit : do3, fa#4."""
    return "%s%d" % (NOMS[int(p) % 12], int(p) // 12 - 1)


def caler(notes, instants, fenetre=20.0, pas=0.010):
    """Le decalage qui met le fichier MIDI en face du morceau.

    On ne compare pas des sons mais des instants d'attaque : ceux du fichier
    MIDI d'un cote, ceux releves dans l'audio de l'autre. On les compte dans
    des cases de dix millisecondes, on floute un peu — deux attaques a trente
    millisecondes l'une de l'autre sont la meme — et on cherche le glissement
    qui en fait coincider le plus.

    **A n'employer que sur un fichier percussif, et jamais par defaut.**
    Mesure sur le morceau d'essai, en comparant a des decalages connus :

        fichier dont les notes tombent sur les attaques
            0 / +0,8 / +3 / +7,5 / -2 s  ->  retrouve exactement, cinq fois
        fichier melodique
            0 / +0,8 / +3 / +7,5 / -2 s  ->  -17,9 / -17,1 / -14,9 / -10,4
                                             / -17,5 s, faux cinq fois

    La raison tient en une phrase : les attaques relevees dans l'audio sont
    surtout des coups de batterie — six mille sept cents sur quatre minutes —
    la ou une melodie ne porte que quelques centaines de notes tenues, qui ne
    tombent pas dessus. Le glissement qui fait le plus coincider n'est alors
    pas le bon, et rien ne permet de s'en apercevoir : les mauvaises reponses
    ci-dessus ont des nettetes allant jusqu'a 3,7, plus hautes que certaines
    bonnes, qui descendent a 1,7. Aucun seuil ne les separe.

    Un fichier MIDI exporte du meme projet que le morceau est deja a l'heure :
    son decalage vaut zero, et c'est ce qu'on lui laisse.

    Les deux listes doivent etre dans la meme base de temps : celle du
    morceau entier. Un extrait qui commence a la quarantieme seconde doit donc
    presenter ses instants decales d'autant, sinon le vrai decalage tombe hors
    de la fenetre cherchee.

    Renvoie (decalage en secondes, nettete). La nettete est le rapport entre
    le meilleur accord et l'accord moyen. Elle vaut zero si le meilleur accord
    touche le bord de la fenetre — le vrai est alors plus loin, et ce qu'on a
    trouve ne veut rien dire.
    """
    import numpy as np
    if not len(notes) or not len(instants):
        return 0.0, 0.0
    m = np.asarray([n[0] for n in notes], dtype=np.float64)
    a = np.asarray(instants, dtype=np.float64)
    n = int(max(float(m.max()), float(a.max())) / pas) + 2
    hm, ha = np.zeros(n), np.zeros(n)
    # ce qui tombe hors du compte est jete, pas rabattu sur le bord : une
    # poignee d'instants tasses dans la premiere case ferait un pic de
    # coincidence qui n'existe pas, et le calage partirait dessus
    for h, v in ((hm, m), (ha, a)):
        i = (v / pas).astype(np.int64)
        np.add.at(h, i[(i >= 0) & (i < n)], 1.0)
    flou = np.exp(-0.5 * (np.arange(-6, 7) / 2.5) ** 2)
    hm = np.convolve(hm, flou, mode="same")
    ha = np.convolve(ha, flou, mode="same")

    L = min(int(round(fenetre / pas)), n - 1)
    scores = np.empty(2 * L + 1)
    for i, d in enumerate(range(-L, L + 1)):
        scores[i] = (np.dot(hm[d:], ha[:n - d]) if d >= 0
                     else np.dot(hm[:n + d], ha[-d:]))
    moyen = float(scores.mean()) or 1e-9
    i = int(scores.argmax())
    if i == 0 or i == len(scores) - 1:
        return 0.0, 0.0
    # Le sommet tombe rarement pile sur une case. On fait passer une parabole
    # par la case gagnante et ses deux voisines : le vrai sommet se lit entre
    # les deux, et le calage gagne un ordre de grandeur sans rien couter.
    fin = 0.0
    if 0 < i < len(scores) - 1:
        g, c, d = scores[i - 1], scores[i], scores[i + 1]
        bas = 2.0 * c - g - d
        if bas > 1e-12:
            fin = float(np.clip(0.5 * (g - d) / bas, -0.5, 0.5))
    return (i - L + fin) * pas, float(scores[i]) / moyen


def transposition(notes, note0=36, touches=37):
    """De combien d'octaves decaler la melodie pour qu'elle tienne au milieu
    du clavier. On ne bouge que par octaves : la melodie garde ses notes."""
    if not len(notes):
        return 0
    hauteurs = sorted(n[2] for n in notes)
    median = hauteurs[len(hauteurs) // 2]
    cible = note0 + touches // 2
    return int(12 * round((cible - median) / 12.0))
