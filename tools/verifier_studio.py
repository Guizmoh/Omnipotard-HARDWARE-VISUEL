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
# Ce que la page envoie en plus des reglages d'allure.
META = {"track", "t", "w", "h", "curve", "width", "height", "fps", "quality",
        "start", "duration", "crf",
        # le titre du fichier, quand le champ du titre est laisse vide
        "fallbackTitle",
        # le nom du fond depose, que la page retient sans curseur
        "backdrop"}


def ids_de_la_page():
    """Les identifiants des curseurs et des listes, tels que la page les pose."""
    curseurs, listes, tous = set(), set(), set()
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
    return curseurs, listes, tous


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
    curseurs, listes, tous = ids_de_la_page()
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

    # 3. le moteur lit bien tout ce que la page lui envoie
    espion = Espion({k: "0" for k in envoi})
    for couleur in ("trait", "bgColor"):
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

    # 5. la v2 propose exactement les memes reglages que la v1
    import studio_v2 as V2
    v2 = {c["id"] for o in V2.donnees() for b in o["blocs"]
          for c in b["controles"]}
    gronder("reglages de la v1 absents de la v2",
            (curseurs | listes) - v2 - {"preset", "clipDur", "scrub"})
    gronder("reglages inventes par la v2", v2 - tous)
    sans_niveau = {c["id"] for o in V2.donnees() for b in o["blocs"]
                   for c in b["controles"] if c["niveau"] < 1 or c["niveau"] > 3}
    gronder("reglages de la v2 sans profondeur", sans_niveau)

    # 6. le rendu part des memes reglages que l'apercu, sans les recopier
    corps = S.PAGE.split("$('#go').onclick", 1)[1][:900]
    if "Object.fromEntries(params())" not in corps:
        soucis.append("le rendu ne repart pas de params() : deux listes de "
                      "reglages a tenir a jour, donc une qui prendra du retard")

    # 7. les prereglages posent des valeurs sur des curseurs qui existent
    gronder("prereglages : noms inconnus du moteur",
            {n for v in PRESETS.values() for n in v} - set(CHAMPS))
    gronder("prereglages : curseurs inconnus de la page",
            {CHAMPS[n] for v in PRESETS.values() for n in v if n in CHAMPS}
            - tous)

    print("page : %d curseurs, %d listes ; %d reglages envoyes au moteur"
          % (len(curseurs), len(listes), len(envoi)))
    if soucis:
        for s in soucis:
            print("  MANQUE  " + s)
        return 1
    print("tout se correspond.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
