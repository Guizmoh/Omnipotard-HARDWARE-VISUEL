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
import bisect
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
    """Les evenements d'une piste, en tics absolus, dans l'ordre du fichier.

    Rend (tic, genre, a, b, canal) ou genre vaut "on", "off", "tempo" ou
    "nom" — ce dernier portant le nom de la piste dans `a`.
    """
    out = []
    i, tic, statut = 0, 0, 0
    while i < len(bloc):
        d, i = _lire_varlen(bloc, i)
        tic += d
        if i >= len(bloc):
            break
        o = bloc[i]
        # ---- meta et systeme exclusif. Ils ne deviennent pas le statut
        # courant : un fichier qui enchaine une note sans son octet de statut
        # juste apres un meta — c'est hors norme, mais il en circule — aurait
        # sinon vu cette note lue comme un meta, et le reste de la piste avec.
        if o == 0xFF:
            if i + 2 > len(bloc):
                break
            genre = bloc[i + 1]
            lg, i = _lire_varlen(bloc, i + 2)
            corps = bloc[i:i + lg]
            i += lg
            if genre == 0x51 and lg == 3:      # tempo : microsecondes / noire
                us = (corps[0] << 16) | (corps[1] << 8) | corps[2]
                out.append((tic, "tempo", us, 0, 0))
            elif genre == 0x03:                # nom de la piste
                out.append((tic, "nom",
                            bytes(corps).decode("latin-1").strip(), 0, 0))
            continue
        if o in (0xF0, 0xF7):
            lg, i = _lire_varlen(bloc, i + 1)
            i += lg
            continue
        if o & 0x80:
            statut = o
            i += 1
        elif not statut:
            raise MidiIllisible("evenement sans statut")
        # ---- voix
        haut = statut & 0xF0
        n = 1 if haut in (0xC0, 0xD0) else 2
        if i + n > len(bloc):
            break
        a = bloc[i]
        b = bloc[i + 1] if n == 2 else 0
        i += n
        if haut == 0x90 and b > 0:
            out.append((tic, "on", a, b, statut & 0x0F))
        elif haut == 0x80 or (haut == 0x90 and b == 0):
            out.append((tic, "off", a, 0, statut & 0x0F))
    return out


def _lire(chemin, pistes=None):
    """Le fichier en tics : (division, tempos, notes).

    `tempos` est la carte des tempos, [(tic, microsecondes par noire)] ;
    `notes` est [(tic de debut, tic de fin, hauteur, force)], la fin valant
    None pour une note que le fichier oublie de fermer. Les tics sont la
    seule chose que le fichier dit sans ambiguite : les secondes en
    dependent de la carte des tempos, les temps de la division.
    """
    with open(chemin, "rb") as f:
        data = f.read()
    if len(data) < 14:
        raise MidiIllisible("fichier trop court")
    _fmt, division, blocs = _pistes(data)

    tous = []
    for k, bloc in enumerate(blocs):
        garde = pistes is None or k in pistes
        for j, (tic, genre, a, b, canal) in enumerate(_evenements(bloc)):
            if genre == "nom":
                continue
            # les tempos comptent quelle que soit la piste : ils sont ecrits
            # sur la premiere et valent pour toutes
            if genre == "tempo" or garde:
                tous.append((tic, k, j, genre, a, b, canal))
    # Par instant, puis dans l'ordre du fichier. Trier « off » avant « on » a
    # tic egal, comme on le faisait, fermait une note de duree nulle avant de
    # l'ouvrir : elle restait alors ouverte, et durait une seconde.
    tous.sort(key=lambda e: e[:3])

    # Les notes ouvertes, par piste, canal et hauteur : la plus ancienne en
    # tete, et c'est elle qu'un « off » ferme. Une seule place par hauteur,
    # comme auparavant, perdait des notes sans rien dire : une note rejouee
    # avant d'avoir ete relachee — un legato, une pedale, deux canaux sur la
    # meme hauteur dans un fichier a une piste — effacait la precedente, dont
    # l'attaque disparaissait du clavier.
    notes, ouvertes, tempos = [], {}, []
    for tic, k, _j, genre, a, b, canal in tous:
        if genre == "tempo":
            tempos.append((tic, float(a) or 500000.0))
            continue
        cle = (k, canal, a)
        if genre == "on":
            ouvertes.setdefault(cle, []).append((tic, b / 127.0))
            continue
        file_ = ouvertes.get(cle)
        if not file_:
            continue                   # un « off » sans note : on l'ignore
        deb, v = file_.pop(0)
        if not file_:
            del ouvertes[cle]
        # une note de duree nulle est gardee : c'est souvent ainsi qu'un
        # logiciel ecrit un coup de batterie, et elle doit allumer son pad
        notes.append((deb, tic, int(a), float(v)))
    for (k, canal, a), file_ in ouvertes.items():
        for deb, v in file_:
            notes.append((deb, None, int(a), float(v)))
    return division, tempos, notes


def _vers_secondes(division, tempos):
    """La fonction tic -> seconde du fichier, carte des tempos comprise."""
    if division & 0x8000:
        # division en images par seconde (SMPTE) : le tempo n'entre pas en jeu
        images = 256 - ((division >> 8) & 0xFF)
        par_tic = 1.0 / (images * (division & 0xFF))
        return lambda tic: tic * par_tic
    par_noire = division or 480
    # les segments de la carte : a partir de tel tic, telle seconde, tel tempo
    segs, sec, tic_prec, us = [], 0.0, 0, 500000.0   # 120 a la noire par defaut
    for tic, us_noire in tempos:
        sec += (tic - tic_prec) * (us / 1e6) / par_noire
        tic_prec, us = tic, us_noire
        segs.append((tic, sec, us))
    debuts = [s[0] for s in segs]

    def conv(tic):
        i = bisect.bisect_right(debuts, tic) - 1
        if i < 0:
            return tic * 0.5 / par_noire
        t0, s0, u = segs[i]
        return s0 + (tic - t0) * (u / 1e6) / par_noire
    return conv


def lire_notes(chemin, pistes=None):
    """Les notes d'un fichier MIDI : liste de (debut, fin, hauteur, force).

    Les instants sont en secondes depuis le debut du fichier, la hauteur est
    le numero de note MIDI (60 = do du milieu) et la force va de 0 a 1.

    `pistes` limite la lecture a certaines pistes, numerotees a partir de 0 ;
    None les prend toutes.
    """
    division, tempos, brutes = _lire(chemin, pistes)
    sec = _vers_secondes(division, tempos)
    notes = []
    for deb, fin, a, v in brutes:
        d = sec(deb)
        # une note laissee ouverte par un fichier mal ferme dure une seconde
        notes.append((d, sec(fin) if fin is not None else d + 1.0, a, v))
    notes.sort()
    return notes


def lire_temps(chemin, pistes=None):
    """Les memes notes, en temps et non en secondes : (debut, fin, hauteur,
    force), un temps valant une noire.

    C'est ce que le fichier dit de la musique, independamment du tempo qu'il
    annonce — ou qu'il n'annonce pas : un fichier sans tempo est lu a 120, et
    une melodie ecrite a 85 y defile alors presque une fois et demie trop vite.
    Pose ensuite au tempo du morceau, chaque note retombe exactement sur son
    temps. Rend None pour un fichier date en images (SMPTE), qui n'a pas de
    temps.
    """
    division, tempos, brutes = _lire(chemin, pistes)
    if division & 0x8000:
        return None
    par_noire = float(division or 480)
    sec = _vers_secondes(division, tempos)
    notes = []
    for deb, fin, a, v in brutes:
        if fin is None:
            # comme en secondes : une seconde, comptee ici au tempo du fichier
            d = sec(deb)
            fin_t = deb
            while sec(fin_t) < d + 1.0:
                fin_t += par_noire / 8.0
            fin = fin_t
        notes.append((deb / par_noire, fin / par_noire, a, v))
    notes.sort()
    return notes


def tempo_fichier(chemin):
    """Ce que le fichier annonce de son tempo : le premier, s'il y en a, et
    combien de fois il en change."""
    division, tempos, _ = _lire(chemin)
    valeurs = [60e6 / us for _tic, us in tempos]
    distincts = []
    for b in valeurs:
        if not distincts or abs(b - distincts[-1]) > 1e-6:
            distincts.append(b)
    return {"bpm": valeurs[0] if valeurs else 120.0,
            "annonce": bool(valeurs),
            "changements": max(0, len(distincts) - 1),
            "smpte": bool(division & 0x8000)}


# General MIDI : le canal 10 est celui de la batterie
CANAL_BATTERIE = 10


def _batterie(hauteurs):
    """Des notes qui ressemblent a une batterie : presque toutes dans la plage
    du kit General MIDI — grosse caisse 35, caisse claire 38, charleys 42 a 46,
    toms, cymbales jusqu'a 59 — et sur une poignee de hauteurs.

    Le canal seul ne suffit pas : une MPC range volontiers une partie de piano
    sur le canal 10 — mesure sur un fichier reel, 950 notes d'arpeges de piano
    sur 1 350, de mi1 a sol5, sur 25 hauteurs dont 39 % seulement dans la
    plage du kit. Le nombre de hauteurs ne suffit pas non plus : un kit complet,
    toms et cymbales compris, en joue une quinzaine.
    """
    if not hauteurs:
        return False
    dans_le_kit = sum(1 for h in hauteurs if 35 <= h <= 59) / float(len(hauteurs))
    return dans_le_kit >= 0.8 and len(set(hauteurs)) <= 18


def inventaire(chemin):
    """Ce que contient le fichier, piste par piste : d'ou viennent les notes.

    Toutes les notes du fichier allument le clavier, de toutes les pistes et
    de tous les canaux. Un export qui a emporte la batterie avec la melodie
    allume donc des touches que l'on n'attendait pas, et rien a l'image ne dit
    pourquoi : cet inventaire le dit.

    Rend une entree par piste qui porte des notes : {"piste", "nom",
    "canaux": {canal: attaques}, "notes", "batterie": [canaux]}. Pistes et
    canaux sont numerotes comme les logiciels les affichent, a partir de 1 ;
    `batterie` liste ceux dont les notes ressemblent a une batterie.
    """
    with open(chemin, "rb") as f:
        data = f.read()
    _fmt, _div, blocs = _pistes(data)
    out = []
    for k, bloc in enumerate(blocs):
        nom, canaux, hauteurs = "", {}, {}
        for _tic, genre, a, _b, canal in _evenements(bloc):
            if genre == "nom" and not nom:
                nom = a
            elif genre == "on":
                canaux[canal + 1] = canaux.get(canal + 1, 0) + 1
                hauteurs.setdefault(canal + 1, []).append(a)
        if canaux:
            out.append({"piste": k + 1, "nom": nom.strip("\x00 "),
                        "canaux": canaux, "notes": sum(canaux.values()),
                        "batterie": sorted(c for c in canaux
                                           if c == CANAL_BATTERIE
                                           and _batterie(hauteurs[c]))})
    return out


def decrire(inv):
    """L'inventaire en une ligne : piste 2 « Lead » : 170 notes, canal 1."""
    parts = []
    for p in inv:
        canaux = ", ".join(
            "canal %d%s" % (c, " (batterie)" if c in p.get("batterie", ())
                            else "")
            for c in sorted(p["canaux"]))
        nom = " \u00ab %s \u00bb" % p["nom"] if p["nom"] else ""
        parts.append("piste %d%s : %d notes, %s"
                     % (p["piste"], nom, p["notes"], canaux))
    return " \u00b7 ".join(parts)


def melange(inv):
    """Une batterie et autre chose dans le meme fichier : tout s'allumera."""
    canaux, batterie = set(), set()
    for p in inv:
        canaux |= set(p["canaux"])
        batterie |= set(p.get("batterie", ()))
    return bool(batterie) and bool(canaux - batterie)


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


def _reponse(t, periodes, poids=None):
    """La reponse d'une suite d'instants a chaque periode de grille :
    |somme des exp(2i.pi.t/p)|, ponderee si on le demande.

    Par paquets : la matrice entiere ferait deux gigaoctets sur un morceau de
    quatre minutes, ou l'on compte pres de sept mille attaques.
    """
    import numpy as np
    t = np.asarray(t, dtype=np.float64)
    w = np.ones(len(t)) if poids is None else np.asarray(poids, dtype=np.float64)
    g = np.asarray(periodes, dtype=np.float64)
    out = np.empty(len(g))
    for i in range(0, len(g), 256):
        bloc = g[i:i + 256]
        out[i:i + 256] = np.abs(
            (w[:, None] * np.exp(2j * np.pi * t[:, None] / bloc[None, :])).sum(axis=0))
    return out


def _sommet(t, autour, largeur, poids=None, pas=1e-6):
    """La periode de grille la plus nette a +-`largeur` autour de `autour` :
    (periode, nettete). La nettete compare le sommet a la reponse moyenne.

    En deux temps : un balayage large au centieme de milliseconde, puis un
    affinage autour du sommet. Balayer tout au millionieme coutait quatre
    secondes et n'apprenait rien de plus.
    """
    import numpy as np
    g = np.arange(autour * (1.0 - largeur), autour * (1.0 + largeur), 1e-5)
    s = _reponse(t, g, poids)
    i = int(s.argmax())
    net = float(s[i] / (s.mean() or 1e-9))
    fin = np.arange(g[i] - 2e-5, g[i] + 2e-5, pas)
    sf = _reponse(t, fin, poids)
    return float(fin[int(sf.argmax())]), net


def deriver(notes, instants, battement, largeur=0.06, pas=1e-6):
    """De combien etirer le fichier pour qu'il tienne le tempo du morceau.

    Un fichier dont la grille n'a pas tout a fait le tempo du morceau se cale
    au debut puis s'en ecarte peu a peu : c'est une derive, et aucun decalage
    ne la rattrape. On mesure donc les deux grilles et on rend leur rapport.

    **Contrairement a `caler`, cette mesure-ci est fiable sur une melodie**, et
    la raison est nette : chercher un decalage revient a choisir *laquelle* des
    mesures du morceau est la bonne, ce qu'un motif repetitif ne permet pas ;
    chercher un tempo ne demande que l'ecart *entre* les attaques, que le meme
    motif repetitif donne au contraire tres bien. Mesure sur le fichier
    d'essai, un motif de doubles-croches sur une seule note :

        morceau entier   85,0006 BPM (nettete 32,8)  melodie 85,1633 (9,7)
        90 s d'extrait   85,0064 BPM (nettete 16,7)  melodie 85,1633 (9,7)
        45 s d'extrait   85,0330 BPM (nettete  9,4)  melodie 85,1633 (9,7)

    Le morceau converge vers 85,000 — un tempo rond, comme presque toujours —
    et la melodie ne bouge pas d'un millieme. Le rapport cherche vaut +0,19 %,
    soit sept centiemes de seconde au bout de trente-cinq : de quoi voir la
    touche s'allumer a cote de la note. Plus l'extrait est long, plus la
    mesure est juste : c'est un ecart entre attaques, il se moyenne.

    On somme `exp(2i.pi.t/p)` plutot que de compter ce qui tombe sur une
    grille : la somme complexe ne depend pas de la phase, la ou une grille
    posee a zero aurait rate le vrai sommet — mesure, elle donnait 0,1765 s au
    lieu de 0,1765 (0,2 % d'erreur, soit tout ce qu'on cherche a corriger).

    `battement` sert seulement a savoir ou chercher : on balaie +-`largeur`
    autour du quart de battement. Sans ce garde-fou la mesure partirait une
    fois sur deux sur la moitie ou le double du vrai pas.

    Renvoie (rapport, pas du morceau, pas du fichier, nettete). Le rapport
    vaut 1 quand rien n'est mesurable — le fichier est alors pris tel quel.
    """
    import numpy as np
    m = np.asarray([n[0] for n in notes], dtype=np.float64)
    a = np.asarray(instants, dtype=np.float64)
    autour = float(battement) / 4.0
    if len(m) < 8 or len(a) < 8 or autour <= 0.0:
        return 1.0, autour, autour, 0.0

    def sommet(t):
        return _sommet(t, autour, largeur, pas=pas)

    pa, na = sommet(a)
    pm, nm = sommet(m)
    # sous 3 de nettete la grille ne ressort pas : mieux vaut ne rien etirer
    # que d'etirer au hasard
    if min(na, nm) < 3.0 or pm <= 0.0:
        return 1.0, pa, pm, min(na, nm)
    return pa / pm, pa, pm, min(na, nm)


# ==========================================================================
#  Le calage par le tempo du morceau
#
#  Un fichier MIDI dit deux choses de ses notes : leur place dans la musique
#  — tel temps de telle mesure, en tics — et, par sa carte des tempos, a
#  quelle seconde cela tombe. La premiere est sure ; la seconde ne l'est que
#  si le tempo annonce est celui du morceau. Un fichier qui n'en annonce pas
#  est lu a 120 ; un tempo arrondi (85 pour 84,6) ou une horloge de machine
#  un rien differente de celle de la carte son font deriver la melodie de
#  quelques millisecondes par mesure, jusqu'a ce que tout soit a cote.
#
#  On garde donc la place des notes dans la musique, et on prend le tempo et
#  la grille dans le morceau lui-meme : mesures sur ses coups de batterie, au
#  millieme de BPM, autour du tempo que l'on donne. Rien ne peut plus
#  deriver : chaque temps du fichier tombe sur un temps du morceau.
# ==========================================================================

# Les grilles sur lesquelles une note peut tomber, en fractions de temps :
# noire, croche, double croche, et les deux triolets. Une melodie en triolets
# n'est pas sur la grille des doubles croches, mais elle est sur la sienne.
SUBDIVISIONS = (1.0, 0.5, 0.25, 1.0 / 3.0, 1.0 / 6.0)


def coherence(instants, periode):
    """A quel point des instants tombent sur une grille de cette periode, quelle
    qu'en soit la phase : 1 = tous dessus, ~0 = au hasard."""
    import numpy as np
    t = np.asarray(instants, dtype=np.float64)
    if not len(t) or periode <= 0:
        return 0.0
    return float(abs(np.exp(2j * np.pi * t / periode).mean()))


def sur_la_grille(instants, temps, origine=0.0):
    """A quel point des instants tombent sur les traits d'une grille qui part
    de `origine` : sur ses temps ou l'une de leurs subdivisions. 1 = tous
    dessus ; 0 ou moins = a cote. Contrairement a `coherence`, la phase compte :
    des notes regulieres mais posees entre les traits n'y sont pas."""
    import numpy as np
    t = np.asarray(instants, dtype=np.float64) - float(origine)
    if not len(t) or temps <= 0:
        return 0.0
    return max(float(np.cos(2 * np.pi * t / (temps * f)).mean())
               for f in SUBDIVISIONS)


def grille_morceau(instants, bpm, poids=None, accents=None, largeur=0.03):
    """Le tempo exact du morceau et l'instant de ses temps.

    `bpm` dit ou chercher : le tempo est mesure a +-`largeur` autour, au
    millieme, sur tous les coups ; s'il ne ressort pas nettement, c'est `bpm`
    lui-meme qui sert. La grille fine — ou tombent les doubles croches — se lit
    sur tous les coups, ponderes par `poids`. Reste a savoir laquelle des
    quatre doubles croches porte le temps : c'est la que tombent la grosse
    caisse, la caisse claire et la basse, d'ou les `accents`, un poids par
    coup qui les favorise. Le charley, lui, tombe partout et ne le dit pas.

    Rend {"bpm", "temps", "phase", "nettete", "mesure", "phase_sure"} :
    `phase` est l'instant du premier temps, entre 0 et la duree d'un temps ;
    `mesure` dit si le tempo a ete mesure plutot que pris tel quel, et
    `phase_sure` si les coups tombent assez sur une grille pour qu'on sache ou
    sont les temps — ce qui ne demande pas d'en avoir beaucoup : seize clics
    disent mal le tempo au millieme, mais tres bien ou ils tombent.
    """
    import numpy as np
    t = np.asarray(instants, dtype=np.float64)
    w = np.ones(len(t)) if poids is None else np.asarray(poids, dtype=np.float64)
    acc = w if accents is None else np.asarray(accents, dtype=np.float64)
    p16 = 60.0 / float(bpm) / 4.0
    net = 0.0
    if len(t) >= 8:
        p, net = _sommet(t, p16, largeur, w)
        if net >= 3.0:
            p16 = p
    temps = 4.0 * p16
    if not len(t):
        return {"bpm": 60.0 / temps, "temps": temps, "phase": 0.0,
                "nettete": net, "mesure": False, "phase_sure": False}
    # la grille fine, sur tous les coups ...
    z16 = (w * np.exp(2j * np.pi * t / p16)).sum()
    ph16 = (np.angle(z16) / (2 * np.pi) * p16) % p16
    # ... dont la longueur dit si les coups tombent sur une grille : 1 s'ils y
    # sont tous, de l'ordre de 1/racine(n) s'ils tombent au hasard
    tenue = float(abs(z16) / (w.sum() or 1e-9))
    # ... et la double croche qui porte le temps : celle ou tombent les accents
    cands = [(ph16 + j * p16) % temps for j in range(4)]
    phase = max(cands, key=lambda c: float(
        (acc * np.cos(2 * np.pi * (t - c) / temps)).sum()))
    return {"bpm": 60.0 / temps, "temps": temps, "phase": float(phase),
            "nettete": net, "mesure": net >= 3.0,
            "phase_sure": tenue >= 0.25 and len(t) >= 4}


def placer(chemin, grille, pistes=None):
    """Les notes du fichier, posees sur la grille du morceau.

    Deux lectures sont possibles, et le fichier dit lui-meme laquelle est la
    bonne :

    - **en temps** : si ses notes tombent sur les traits de sa propre grille —
      un fichier ecrit dans un sequenceur, une boite a rythmes, un projet —
      chaque note garde sa place dans la musique, et c'est la grille du
      morceau qui dit a quelle seconde elle tombe. Le temps 0 du fichier va
      sur le temps du morceau le plus proche du debut. C'est ce qui rattrape
      un fichier qui annonce un autre tempo que le morceau, ou aucun, ou dont
      la premiere mesure n'est pas au debut du son. Un fichier deja au tempo
      exact du morceau et deja a sa place n'est pas touche : le reposer ne
      ferait qu'ajouter l'imprecision de la mesure.
    - **en secondes**, tel quel, si ses notes n'ont pas de grille a elles mais
      tombent sur celle du morceau : un fichier tire du son par un logiciel,
      une partie jouee sans clic. Si sa grille s'ecarte d'un rien de celle du
      morceau — moins de 0,6 %, une horloge ou un tempo arrondi —, il est
      etire d'autant. Au-dela, ce n'est plus une derive mais un autre tempo.

    Si ni l'une ni l'autre ne tombe sur une grille, le fichier est pris tel
    quel.

    Rend (notes, compte rendu) : les notes en secondes du morceau, et de quoi
    dire a la page ce qui a ete fait.
    """
    import numpy as np
    temps, phase = float(grille["temps"]), float(grille["phase"])
    sure = bool(grille.get("phase_sure", grille.get("mesure", True)))
    if not sure:
        # sans grille lisible dans le son, on suppose ce que fait un export :
        # le premier temps au debut du fichier
        phase = 0.0
    # le temps du morceau le plus proche du debut : c'est la qu'un fichier
    # exporte depuis le debut du projet pose sa premiere mesure
    phase0 = phase if phase <= temps / 2.0 else phase - temps
    secondes = lire_notes(chemin, pistes)
    en_temps = lire_temps(chemin, pistes)
    annonce = tempo_fichier(chemin)
    cr = {"bpm_morceau": grille["bpm"], "tempo_mesure": sure,
          "bpm_fichier": annonce["bpm"], "tempo_annonce": annonce["annonce"],
          "changements": annonce["changements"], "phase": phase0,
          "etirement": 1.0, "coherence_temps": 0.0,
          "coherence_secondes": 0.0}
    if not secondes:
        cr.update(mode="secondes", debut=0.0)
        return [], cr

    # ---- en temps : les notes sur les traits de leur propre grille ?
    c_temps = (sur_la_grille([n[0] for n in en_temps], 1.0)
               if en_temps else -1.0)
    # ---- en secondes : sur ceux du morceau, a une petite derive pres ?
    t = np.asarray([n[0] for n in secondes], dtype=np.float64)
    etir = 1.0
    if len(t) >= 8:
        # cherche large pour que le sommet ressorte, n'accepte que petit
        pm, net = _sommet(t, temps / 4.0, 0.06)
        if net >= 3.0 and pm > 0 and abs((temps / 4.0) / pm - 1.0) <= 0.006:
            etir = (temps / 4.0) / pm
    d0 = float(t.min())
    t2 = d0 + (t - d0) * etir
    c_sec = sur_la_grille(t2, temps, phase0) if sure else -1.0
    cr.update(coherence_temps=max(0.0, c_temps),
              coherence_secondes=max(0.0, c_sec))

    mode, recale = "tel quel", 0.0
    if c_temps >= 0.7:
        mode = "temps"
        deja = (annonce["annonce"] and not annonce["changements"]
                and abs(annonce["bpm"] / grille["bpm"] - 1.0) < 0.0005
                and abs(phase0) < 0.030)
        if deja:
            mode, etir = "secondes", 1.0
    elif c_sec >= 0.5:
        mode = "secondes"
    elif sure:
        # Reguliere mais posee entre les traits : la melodie a bien la grille
        # du morceau, decalee d'une fraction de case — ce que laisse un
        # logiciel qui tire les notes d'un son, ou un fichier cale a l'oeil.
        # On la remet sur le trait le plus proche, a moins d'une demi-case :
        # au-dela, on ne saurait plus lequel est le bon.
        f = max(SUBDIVISIONS, key=lambda f: coherence(t2 - phase0, temps * f))
        case = temps * f
        if coherence(t2 - phase0, case) >= 0.6:
            z = np.exp(2j * np.pi * (t2 - phase0) / case).mean()
            recale = -float(np.angle(z) / (2 * np.pi) * case)
            mode = "secondes"
    if mode == "temps":
        notes = [(phase0 + d * temps, phase0 + f * temps, h, v)
                 for d, f, h, v in en_temps]
    else:
        if mode == "tel quel":
            etir = 1.0
        notes = [(d0 + (d - d0) * etir + recale, d0 + (f - d0) * etir + recale,
                  h, v) for d, f, h, v in secondes]
        cr["etirement"] = etir
    cr["recale"] = recale
    cr["mode"] = mode
    notes.sort()
    cr["debut"] = notes[0][0]
    return notes, cr


def raconter(cr):
    """Le compte rendu du calage, en une phrase pour la page."""
    def nb(x, f="%.2f"):
        return (f % x).replace(".", ",")
    bpm = nb(cr["bpm_morceau"])
    if cr["mode"] == "temps":
        ecart = abs(cr["bpm_fichier"] - cr["bpm_morceau"]) / cr["bpm_morceau"]
        if not cr["tempo_annonce"]:
            debut = ("le fichier n'annonce pas de tempo : ses notes sont "
                     "relues à celui du morceau (%s BPM)" % bpm)
        elif ecart > 0.0005:
            debut = ("le fichier annonce %s BPM, le morceau en fait %s : ses "
                     "notes sont relues au tempo du morceau"
                     % (nb(cr["bpm_fichier"]), bpm))
        else:
            debut = ("le fichier est au tempo du morceau (%s BPM) mais pas à "
                     "sa place : ses notes sont" % bpm)
            return debut + (" posées sur sa grille, premier temps à %s s"
                            % nb(cr["phase"], "%.3f"))
        return debut + (" et posées sur sa grille, premier temps à %s s"
                        % nb(cr["phase"], "%.3f"))
    if cr["mode"] == "tel quel":
        return ("ses notes ne tombent sur aucune grille : le fichier est pris "
                "tel quel — vérifiez le BPM du morceau")
    if abs(cr.get("recale", 0.0)) > 1e-4:
        txt = ("le fichier est daté en secondes, à côté de la grille du "
               "morceau (%s BPM) : il est remis sur ses traits, décalé de %s ms"
               % (bpm, nb(cr["recale"] * 1000.0, "%+.0f")))
    else:
        txt = ("le fichier tombe déjà sur la grille du morceau (%s BPM) : il "
               "est pris tel quel" % bpm)
    if abs(cr["etirement"] - 1.0) > 1e-5:
        txt += (", et étiré de %s %% pour en suivre le tempo exact"
                % nb((cr["etirement"] - 1.0) * 100.0, "%+.3f"))
    return txt
