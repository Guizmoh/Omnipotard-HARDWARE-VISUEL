#!/usr/bin/env python3
"""Verifie que la page du studio et le moteur parlent bien de la meme chose.

Rien a installer, rien a lancer : ce controle lit la page telle qu'elle est
servie et le moteur tel qu'il est ecrit, et signale les endroits ou les deux
ne se correspondent plus.

Il existe parce qu'une erreur de ce genre ne se voit pas : le rendu recopiait
a la main les reglages de l'apercu, si bien que tout effet ajoute ensuite
restait visible a l'ecran mais absent du fichier final, sans le moindre
message. Chaque controle ci-dessous ferme une de ces portes.

    python3 tools/verifier_studio.py
"""
import os
import re
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from omnipotard_intro import AIDE, CHAMPS, COMPTE, PRESETS   # noqa: E402
import studio as S                                           # noqa: E402


# Ce qui ne fait pas partie de l'allure : le fichier de sortie, le morceau,
# l'instant regarde. Ces reglages-la ne passent pas par look_from.
HORS_ALLURE = {"size", "fps", "quality", "start", "dur", "preset", "scrub",
               "file", "bdfile",
               # la duree de l'apercu en mouvement : elle ne decrit rien de
               # l'image, elle dit seulement combien de secondes calculer
               "clipDur"}
# Les champs qui aident a regler sans etre des reglages : le decalage en
# millisecondes ecrit dans le curseur du decalage, le nom d'un reglage a
# enregistrer, l'ecart du sequenceur automatique.
AIDES = {"midiMs", "presetNom", "seqChaque"}
# Ce que la page envoie en plus des reglages d'allure.
META = {"track", "t", "w", "h", "curve", "width", "height", "fps", "quality",
        "start", "duration", "crf",
        # le titre du fichier, quand le champ du titre est laisse vide
        "fallbackTitle",
        # le nom du fond depose, que la page retient sans curseur
        "backdrop"}


def ids_de_la_page():
    """Les identifiants des curseurs, des listes et des autres champs (cases,
    nombres, couleurs, texte), tels que la page les pose."""
    curseurs, listes, champs, tous = set(), set(), set(), set()
    for m in re.finditer(r'<(input|select)\b([^>]*)>', S.PAGE):
        balise, attrs = m.group(1), m.group(2)
        ident = re.search(r'id="([^"]+)"', attrs)
        if not ident:
            continue
        nom = ident.group(1)
        tous.add(nom)
        if balise == "select":
            listes.add(nom)
        elif 'type="range"' in attrs:
            curseurs.add(nom)
        elif re.search(r'type="(number|checkbox|color|text)"', attrs):
            champs.add(nom)
    return curseurs, listes, champs, tous


def envoyes_par_la_page():
    """Les reglages que `params()` transmet, lus dans le corps de la fonction."""
    corps = S.PAGE.split("function params() {", 1)[1].split("\n  return p;", 1)[0]
    # « nom: valeur », et aussi « nom, » quand la variable porte deja le bon nom
    nommes = re.findall(r'[,{]\s*([A-Za-z][A-Za-z0-9]*)\s*:', corps)
    courts = re.findall(r'[,{]\s*([A-Za-z][A-Za-z0-9]*)\s*(?=[,}])', corps)
    return set(nommes) | set(courts)


class Espion(dict):
    """Un dictionnaire qui retient ce qu'on lui a demande."""

    def __init__(self, *a, **k):
        super().__init__(*a, **k)
        self.vus = set()

    def get(self, cle, defaut=None):
        self.vus.add(cle)
        return super().get(cle, defaut)

    def __getitem__(self, cle):
        self.vus.add(cle)
        return super().__getitem__(cle)


def main():
    curseurs, listes, champs, tous = ids_de_la_page()
    envoi = envoyes_par_la_page()
    soucis = []

    def gronder(quoi, manquants):
        if manquants:
            soucis.append("%s : %s" % (quoi, ", ".join(sorted(manquants))))

    # 1. la page n'envoie rien qui n'existe pas chez elle
    gronder("reglages envoyes mais absents de la page", envoi - tous - META)

    # 2. tout curseur d'allure part vraiment au moteur — c'est le controle qui
    #    aurait vu les effets rester a l'ecran sans jamais atteindre le fichier
    gronder("curseurs de la page jamais envoyes",
            (curseurs | listes) - envoi - HORS_ALLURE)
    # les cases, nombres et couleurs aussi : le tempo du morceau et la lecture
    # « telle quelle » de la melodie sont restes des semaines sans partir,
    # parce que seuls curseurs et listes etaient controles
    gronder("champs de la page jamais envoyes",
            champs - envoi - HORS_ALLURE - AIDES)

    # 3. le moteur lit bien tout ce que la page lui envoie
    espion = Espion({k: "0" for k in envoi})
    for couleur in ("trait", "bgColor", "couleurCoupsLibre"):
        espion[couleur] = "#00ff00"
    # « perso » est le seul cas ou la couleur libre du trait est lue
    espion["palette"] = "perso"
    S.look_from(espion)
    gronder("reglages envoyes que le moteur ne lit pas",
            envoi - espion.vus - META)

    # 4. une phrase d'aide sous chaque reglage, et pas de phrase orpheline
    gronder("phrases d'aide sans reglage correspondant", set(AIDE) - tous)
    gronder("curseurs sans phrase d'aide", curseurs - set(AIDE))
    gronder("frequences annoncees pour un reglage inexistant", set(COMPTE) - tous)

    # 5. le rendu part des memes reglages que l'apercu, sans les recopier
    corps = S.PAGE.split("$('#go').onclick", 1)[1][:900]
    if "Object.fromEntries(params())" not in corps:
        soucis.append("le rendu ne repart pas de params() : deux listes de "
                      "reglages a tenir a jour, donc une qui prendra du retard")

    # 6. les prereglages posent des valeurs sur des curseurs qui existent
    gronder("prereglages : noms inconnus du moteur",
            {n for v in PRESETS.values() for n in v} - set(CHAMPS))
    gronder("prereglages : curseurs inconnus de la page",
            {CHAMPS[n] for v in PRESETS.values() for n in v if n in CHAMPS}
            - tous)

    # 7. les effets places sur la frise : la valeur du bloc dedans, celle du
    #    panneau dehors, l'instrument du bloc, et le moteur rendu tel quel
    import mpc_performance as MP
    gronder("effets placables inconnus de la page",
            set(MP.EFFETS_PLACABLES) - tous)

    class Moteur:
        pass
    m = Moteur()
    for attr, attr_on, entier in MP.EFFETS_PLACABLES.values():
        setattr(m, attr, 0 if entier else 0.25)
        if attr_on:
            setattr(m, attr_on, "grosse caisse")
    blocs = MP.lire_effets(
        '[{"e": "mosaic", "a": 10, "b": 20, "v": 2, "on": "caisse claire"},'
        ' {"e": "haloDoux", "a": 10, "b": 20, "v": 1.5},'
        ' {"e": "cadence", "a": 5, "b": 6, "v": 3},'
        ' {"e": "inconnu", "a": 1, "b": 2, "v": 1},'
        ' {"e": "shake", "a": 9, "b": 3, "v": 1}]', S.DECLENCHEURS)
    MP.poser_effets(m, blocs)
    attendu = [
        (15.0, lambda: m.mosaic == 2.0 and m.mosaic_on == "caisse claire"
         and m.halo_doux == 1.5, "au milieu du bloc"),
        (10.05, lambda: 0.25 < m.halo_doux < 1.5 and m.mosaic == 2.0,
         "a l'entree du bloc (fondu, sauf sur instrument)"),
        (25.0, lambda: m.mosaic == 0.25 and m.mosaic_on == "grosse caisse"
         and m.halo_doux == 0.25, "hors du bloc"),
        (5.5, lambda: m.cadence == 3 and isinstance(m.cadence, int),
         "cadence entiere"),
    ]
    if len(blocs) != 3:
        soucis.append("effets places : %d blocs lus au lieu de 3" % len(blocs))
    for t, ok, quoi in attendu:
        MP.appliquer_effets(m, t)
        if not ok():
            soucis.append("effets places : faux %s (t = %s)" % (quoi, t))
    MP.appliquer_effets(m, 15.0)
    MP.retablir_effets(m)
    if not (m.mosaic == 0.25 and m.halo_doux == 0.25 and m.cadence == 0
            and m.mosaic_on == "grosse caisse"):
        soucis.append("effets places : le moteur n'est pas remis apres l'image")
    decale = MP.decaler_effets(blocs, -12.0)
    if [round(e["a"], 3) for e in decale] != [-2.0, -2.0] \
            or len(decale) != 2:
        soucis.append("effets places : mauvais decalage pour un extrait")

    # 8. l'ecoute en direct et les masques de l'apercu
    if not getattr(S.Handler, "disable_nagle_algorithm", False):
        soucis.append("le studio retient ses reponses (Nagle) : chaque image "
                      "de l'ecoute attendrait 40 ms de plus")
    rendu = S.PAGE.split("$('#go').onclick", 1)[1].split("\n};", 1)[0]
    if "MASQUES" in rendu:
        soucis.append("les masques de l'apercu touchent l'export")
    for quoi, debut in (("l'image fixe", "function calculerApercu()"),
                        ("l'extrait anime", "function reglagesDuClip()"),
                        ("l'ecoute", "async function image(n)")):
        morceau = S.PAGE.split(debut, 1)[1][:1500] if debut in S.PAGE else ""
        if "MASQUES.appliquer(params())" not in morceau:
            soucis.append("les masques ne s'appliquent pas a %s" % quoi)
    import numpy as np
    _, genre = S.jpeg_bytes(np.zeros((8, 8, 3), np.uint8))
    if genre not in ("image/jpeg", "image/png"):
        soucis.append("ecoute : image du direct de type %s" % genre)
    soucis += lecteur_de_fond()

    print("page : %d curseurs, %d listes ; %d reglages envoyes au moteur"
          % (len(curseurs), len(listes), len(envoi)))
    if soucis:
        for s in soucis:
            print("  MANQUE  " + s)
        return 1
    print("tout se correspond.")
    return 0


def lecteur_de_fond():
    """Le lecteur continu des videos de fond (l'ecoute) rend les memes images
    que l'extraction une a une, a une image pres, en avancant comme en
    revenant en arriere. Il faut ffmpeg : sans lui, rien a verifier."""
    import shutil
    import subprocess
    import tempfile
    import numpy as np
    from omnipotard_intro import LecteurFond, _image_fond, genre_fond
    if not shutil.which("ffmpeg"):
        return []
    soucis = []
    with tempfile.TemporaryDirectory() as dossier:
        video = os.path.join(dossier, "compte.mp4")
        subprocess.run(["ffmpeg", "-v", "error", "-y", "-f", "lavfi", "-i",
                        "testsrc=size=320x180:rate=25:duration=4",
                        "-pix_fmt", "yuv420p", video], check=True)
        g = genre_fond(video)
        lect = LecteurFond(video, 160, 90, 0.0, g[1], g[2])
        try:
            for t in (0.5, 0.58, 1.3, 3.1, 1.0, 5.2):
                a = lect.image(t)
                pres = min(float(np.abs(a - _image_fond(video, 160, 90,
                                                        max(1e-3, t % g[1] + d),
                                                        0.0)).mean())
                           for d in (-0.04, 0.0, 0.04))
                if pres > 1e-3:
                    soucis.append("lecteur de fond : image fausse a %.2f s" % t)
        finally:
            lect.fermer()
    return soucis


if __name__ == "__main__":
    sys.exit(main())
