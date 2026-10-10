#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
STUDIO — l'atelier local

Un petit outil a lancer sur sa machine : on depose un morceau, on choisit la
couleur du trait et le fond, on voit le resultat immediatement sur une image
fixe, et on lance le rendu quand ca plait.

    python3 tools/studio.py

Le navigateur s'ouvre sur http://127.0.0.1:8765. Rien ne sort de la machine :
le serveur n'ecoute que sur l'interface locale, et tout ce qu'il fabrique
(morceaux deposes et videos) reste dans out/studio/.

Dependances : les memes que le reste du projet (numpy et ffmpeg). Pas de
bibliotheque web, pas de CDN — la page est servie telle quelle.
"""

import argparse
import base64
import glob
import json
import os
import re
import struct
import subprocess
import sys
import threading
import time
import traceback
import webbrowser
import zlib
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import urlparse, parse_qs

import numpy as np

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from mpc_performance import (  # noqa: E402
    analyze, frame_performance, probe_duration, render_video, _renderer,
    compute_spectro, preparer_midi, grille_du_morceau, RenduArrete,
    lire_effets, poser_effets, retablir_effets, EFFETS_PLACABLES, FONDU_EFFET,
)
import midi                                                   # noqa: E402
from omnipotard_intro import (  # noqa: E402
    BACKGROUNDS, PALETTES, hex_to_rgb, rgb_to_hex, load_backdrop, is_video,
    menage_fonds, apercu_fonds, genre_fond, BOUCLES_FOND, plan_fonds,
    _durees_fonds, _image_fond, LecteurFond,
    VERSION, INSTRUMENTS, DECLENCHEURS, groupes_declencheurs, MACHINES,
    NOMS_MACHINES, COULEURS_COUPS, TEXTURES_TOUCHES, MODES_TRAIT, INVERSIONS,
    compte_frappes, TRAVELLINGS, FAMILLES, apercu_possible,
    lire_plan_machines,
    backdrop_quality, PRESETS, STYLES, CHAMPS, AIDE, COMPTE, QUALITES, pick_split_times,
)

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def version():
    """Version du code effectivement charge.

    Python lit les modules au demarrage : un studio laisse ouvert continue de
    servir l'ancien moteur meme apres une mise a jour. Et le dossier est
    souvent recupere en archive zip, sans historique — sur une machine ou git
    n'est meme pas installe. D'ou un numero ecrit dans le code lui-meme, que
    rien ne peut perdre ; l'historique, quand il est la, vient en plus.
    """
    try:
        out = subprocess.run(["git", "-C", ROOT, "log", "-1",
                              "--format=%h %ad", "--date=short"],
                             stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
                             check=True).stdout.decode().strip()
    except Exception:                                     # noqa: BLE001
        out = ""
    return ("version %s" % VERSION) + (" (%s)" % out[:40] if out else "")
# tout ce que le studio fabrique tient dans un seul dossier de travail
WORKDIR = os.path.join(ROOT, "out", "studio")
UPLOADS = os.path.join(WORKDIR, "morceaux")
FONDS = os.path.join(WORKDIR, "fonds")        # images et videos de fond
MELODIES = os.path.join(WORKDIR, "melodies")  # fichiers MIDI
OUTDIR = WORKDIR
# les polices de la page (Inter, JetBrains Mono), livrees avec le studio
POLICES = os.path.join(os.path.dirname(os.path.abspath(__file__)), "polices")
MO = 1024 * 1024
MAX_UPLOAD = 220 * MO                   # un morceau, pas une discotheque
# Une video de telephone pese des centaines de megaoctets, et une video de
# fond est rarement courte : la limite des morceaux n'a pas de sens pour elle.
MAX_FOND = 2048 * MO
# un fichier MIDI est minuscule : le plus gros qu'on croise fait quelques
# centaines de kilo-octets. Au-dela, ce n'en est pas un.
MAX_MIDI = 16 * MO
BLOC = 1 * MO                           # on lit et on ecrit par blocs


# --------------------------------------------------------------------------
#  Petits utilitaires
# --------------------------------------------------------------------------

def png_bytes(img):
    """Encode un tableau (h, w, 3) uint8 en PNG, sans dependance.

    Compression au plus rapide : l'image ne quitte pas la machine, sa taille
    ne compte pas, et le niveau 6 coutait autant que le dessin lui-meme."""
    h, w, _ = img.shape
    rows = np.hstack([np.zeros((h, 1), np.uint8), img.reshape(h, w * 3)])
    def chunk(tag, data):
        c = tag + data
        return struct.pack(">I", len(data)) + c + struct.pack(">I", zlib.crc32(c))
    return (b"\x89PNG\r\n\x1a\n"
            + chunk(b"IHDR", struct.pack(">IIBBBBB", w, h, 8, 2, 0, 0, 0))
            + chunk(b"IDAT", zlib.compress(rows.tobytes(), 1))
            + chunk(b"IEND", b""))


def jpeg_bytes(img, qualite=82):
    """Une image en JPEG, pour l'ecoute en direct : cinq fois plus vite
    encodee qu'en PNG. Sans pillow, on retombe sur le PNG."""
    try:
        import io
        from PIL import Image
    except ImportError:
        return png_bytes(img), "image/png"
    tampon = io.BytesIO()
    Image.fromarray(img).save(tampon, "JPEG", quality=qualite)
    return tampon.getvalue(), "image/jpeg"


# le son du morceau, tel que le navigateur le lit
GENRES_AUDIO = {".mp3": "audio/mpeg", ".wav": "audio/wav", ".flac": "audio/flac",
                ".m4a": "audio/mp4", ".mp4": "audio/mp4", ".aac": "audio/aac",
                ".ogg": "audio/ogg", ".oga": "audio/ogg", ".opus": "audio/ogg",
                ".webm": "audio/webm"}


def wav_du_morceau(tr):
    """Le morceau decode, en WAV, pour un navigateur qui ne lit pas le
    fichier d'origine (un aiff, un wma) : ecrit une fois, a cote des morceaux."""
    a = tr["info"]["_audio"]
    chemin = os.path.join(WORKDIR, "ecoute", "%s.wav" % tr["id"])
    if not os.path.exists(chemin):
        os.makedirs(os.path.dirname(chemin), exist_ok=True)
        st = np.asarray(a["stereo"], dtype="<i2")
        donnees = st.tobytes()
        canaux = st.shape[1] if st.ndim == 2 else 1
        sr = int(a["sr"])
        tete = (b"RIFF" + struct.pack("<I", 36 + len(donnees)) + b"WAVEfmt "
                + struct.pack("<IHHIIHH", 16, 1, canaux, sr, sr * canaux * 2,
                              canaux * 2, 16)
                + b"data" + struct.pack("<I", len(donnees)))
        provisoire = chemin + ".part"
        with open(provisoire, "wb") as f:
            f.write(tete)
            f.write(donnees)
        os.replace(provisoire, chemin)
    return chemin


def derive_palette(hexcol):
    """Fabrique les quatre couleurs du moteur a partir d'une seule.

    Le coeur du trait est la couleur choisie ramenee a sa pleine intensite,
    le halo la meme en plus saturee, et le coeur sur-expose sa version
    presque blanche — c'est ce triplet qui donne au trait son allure de
    phosphore plutot que de ligne peinte.
    """
    c = np.array(hex_to_rgb(hexcol), dtype=np.float64)
    m = float(c.max()) or 1.0
    base = c / m
    fluo = tuple(float(x) for x in base)
    halo = tuple(float(x) for x in np.clip(base ** 2.2, 0.0, 1.0))
    hot = tuple(float(x) for x in np.clip(0.70 + 0.30 * base, 0.0, 1.0))
    return (fluo, halo, hot, (0.0, 0.0, 0.0))


def safe_name(name):
    name = os.path.basename(str(name or "morceau"))
    name = re.sub(r"[^A-Za-z0-9._-]+", "_", name).strip("._") or "morceau"
    return name[:120]


def _inversion(v):
    """Ce que le negatif touche : « non », « tout », « fond » ou « trait ».
    Une ancienne case cochee (« 1 », « true ») voulait dire toute l'image."""
    v = str(v if v is not None else "").strip().lower()
    if v in INVERSIONS:
        return v
    return "tout" if v in ("1", "true", "on", "oui") else "non"


def _coche(valeur, defaut=False):
    """Une case a cocher, telle que la page l'envoie.

    La page ecrit « 1 » ou « 0 ». Passer la chaine a bool() rend vrai dans les
    deux cas — « 0 » n'est pas une chaine vide — et la case reste cochee quoi
    qu'on fasse. C'est ce qui arrivait a la courbe du titre dans l'apercu : le
    rendu, lui, envoie un vrai booleen et obeissait, si bien que l'image
    regardee et le fichier produit ne disaient pas la meme chose.
    """
    if valeur is None or valeur == "":
        return defaut
    return str(valeur).strip().lower() not in ("0", "false", "off", "non")


def _melodie(nom):
    """Le chemin d'une melodie deposee, ou rien.

    Le nom vient de la page : on le repasse par `safe_name` et on ne sort pas
    du dossier des melodies, pour qu'un nom tordu ne puisse pas designer un
    fichier ailleurs sur le disque.
    """
    nom = str(nom or "").strip()
    if not nom:
        return ""
    chemin = os.path.join(MELODIES, safe_name(nom))
    return chemin if os.path.exists(chemin) else ""


def _dans(valeur, permises, defaut):
    """Un nom venu de la page, ramene a ceux que le moteur connait.

    La page peut etre ouverte dans un onglet reste sur une version plus
    ancienne : mieux vaut retomber sur la valeur par defaut que lever une
    erreur au milieu d'un rendu.
    """
    return valeur if valeur in permises else defaut


def _couleur(valeur, defaut):
    """Une couleur #rrggbb de la page ; illisible, celle par defaut."""
    try:
        return hex_to_rgb(valeur or defaut)
    except ValueError:
        return hex_to_rgb(defaut)


def look_from(q):
    """Traduit les reglages de la page en arguments du moteur."""
    pal = q.get("palette", "vert")
    palette = derive_palette(q["trait"]) if pal == "perso" else pal
    bg = q.get("bg") or "noir"
    return palette, {
        "bg": bg,
        "bg_color": hex_to_rgb(q.get("bgColor") or "#000000"),
        "bg_strength": float(q.get("bgStrength", 1.0)),
        "bg_clear": float(q.get("bgClear", 0.55)),
        "bg_anim": float(q.get("bgAnim", 0.0)),
        "neon": float(q.get("neon", 1.0)),
        "reflet": float(q.get("reflet", 0.5)),
        "tube": float(q.get("tube", 0.0)),
        "wobble": float(q.get("wobble", 0.0)),
        "split": float(q.get("split", 1.0)),
        "split_count": int(float(q.get("splitCount", 3))),
        "split_on": _dans(q.get("splitOn"), DECLENCHEURS, "grosse caisse"),
        "glitch": float(q.get("glitch", 1.0)),
        "machine": _dans(q.get("machine"), NOMS_MACHINES, "mpc"),
        # le sequenceur de machines, et la deformation qui mene de l'une a
        # l'autre. Le plan est relu par le moteur, qui jette ce qu'il ne
        # comprend pas : la page peut donc l'ecrire comme elle veut.
        "machines": str(q.get("machines", "") or ""),
        "passage": float(q.get("passage", 1.9)),
        "passage_turb": float(q.get("passageTurb", 1.0)),
        # la melodie : un nom de fichier depose dans out/studio/melodies
        "midi": _melodie(q.get("midi")),
        "midi_force": float(q.get("midiForce", 1.0)),
        # ---- la lumiere des coups : pads et touches frappes
        "eclat_pads": float(q.get("eclatPads", 1.0)),
        "couleur_coups": _dans(q.get("couleurCoups"), COULEURS_COUPS, "trait"),
        "couleur_coups_libre": _couleur(q.get("couleurCoupsLibre"), "#ff7a1f"),
        "texture_touches": _dans(q.get("textureTouches"), TEXTURES_TOUCHES,
                                 "nappe"),
        # ---- la nature du trait : neon, encre (fonds clairs) ou selon le fond
        "mode_trait": _dans(q.get("modeTrait"), MODES_TRAIT, "neon"),
        "encre": _couleur(q.get("encre"), "#0a1210"),
        "detourage": float(q.get("detourage", 0.0)),
        # le fond derriere la machine, eclairci en papier sous l'encre
        "papier": float(q.get("papier", 0.0)),
        # ce que le negatif touche ; l'ancienne case cochee voulait dire tout
        "inverser": _inversion(q.get("inverser")),
        "vignettage": float(q.get("vignettage", 1.0)),
        "scanlines": float(q.get("scanlines", 1.0)),
        "aberration": float(q.get("aberration", 0.0)),
        "midi_offset": float(q.get("midiOffset", 0.0)),
        # le tempo du morceau, tape ou propose : les notes se posent sur sa
        # grille (voir midi.placer) ; 0 = le tempo detecte
        "midi_bpm": float(q.get("midiBpm") or 0.0),
        "midi_tel_quel": _coche(q.get("midiTelQuel")),
        # la page le donne en pourcent — un rapport a six decimales ne se lit
        # pas sur un curseur — et le moteur veut un rapport
        "midi_tempo": 1.0 + float(q.get("midiTempo", 0.0)) / 100.0,
        "midi_type": "batterie" if q.get("midiType") == "batterie" else "piano",
        "midi_cale": _coche(q.get("midiCale")),
        # les effets places sur la frise : des blocs de temps, en JSON
        "effets": lire_effets(q.get("effets"), DECLENCHEURS),
        "nettete": float(q.get("nettete", 1.0)),
        "taille": float(q.get("taille", 1.0)),
        "presence": float(q.get("presence", 1.0)),
        "step_div": float(q.get("stepDiv", 2.0)),
        # ---- avaries d'image, declenchees par la batterie
        "tranches": float(q.get("tranches", 0.0)),
        "tranches_on": _dans(q.get("tranchesOn"), DECLENCHEURS, "caisse claire"),
        "roll": float(q.get("roll", 0.0)),
        "roll_on": _dans(q.get("rollOn"), DECLENCHEURS, "grosse caisse"),
        "ghost": float(q.get("ghost", 0.0)),
        "ghost_on": _dans(q.get("ghostOn"), DECLENCHEURS, "caisse claire"),
        "blocs": float(q.get("blocs", 0.0)),
        "blocs_on": _dans(q.get("blocsOn"), DECLENCHEURS, "caisse claire"),
        "invert": float(q.get("invert", 0.0)),
        "invert_on": _dans(q.get("invertOn"), DECLENCHEURS, "grosse caisse"),
        # le flash d'inversion : toute l'image en negatif
        "inversion": float(q.get("inversion", 0.0)),
        "inversion_on": _dans(q.get("inversionOn"), DECLENCHEURS, "grosse caisse"),
        "stut": float(q.get("stut", 0.0)),
        "stut_on": _dans(q.get("stutOn"), DECLENCHEURS, "charley"),
        "stut_loop": float(q.get("stutLoop", 0.05)),
        "scramble": float(q.get("scramble", 0.0)),
        "scr_len": float(q.get("scrLen", 0.14)),
        "miroir": float(q.get("miroir", 0.0)),
        "miroir_on": _dans(q.get("miroirOn"), DECLENCHEURS, "caisse claire"),
        "ondul": float(q.get("ondul", 0.0)),
        "ondul_on": _dans(q.get("ondulOn"), DECLENCHEURS, "basse"),
        "mosaic": float(q.get("mosaic", 0.0)),
        "mosaic_on": _dans(q.get("mosaicOn"), DECLENCHEURS, "caisse claire"),
        "kaleido": float(q.get("kaleido", 0.0)),
        "kaleido_on": _dans(q.get("kaleidoOn"), DECLENCHEURS, "caisse claire"),
        "cisaille": float(q.get("cisaille", 0.0)),
        "cisaille_on": _dans(q.get("cisailleOn"), DECLENCHEURS, "caisse claire"),
        "coupure": float(q.get("coupure", 0.0)),
        "coupure_on": _dans(q.get("coupureOn"), DECLENCHEURS, "grosse caisse"),
        "tapestop": float(q.get("tapestop", 0.0)),
        "tapestop_on": _dans(q.get("tapestopOn"), DECLENCHEURS, "grosse caisse"),
        # ---- glitchs avances
        "tri": float(q.get("tri", 0.0)),
        "tri_on": _dans(q.get("triOn"), DECLENCHEURS, "caisse claire"),
        "rvb": float(q.get("rvb", 0.0)),
        "rvb_on": _dans(q.get("rvbOn"), DECLENCHEURS, "caisse claire"),
        "retro": float(q.get("retro", 0.0)),
        "retro_on": _dans(q.get("retroOn"), DECLENCHEURS, "grosse caisse"),
        "macro": float(q.get("macro", 0.0)),
        "macro_on": _dans(q.get("macroOn"), DECLENCHEURS, "caisse claire"),
        "tourbillon": float(q.get("tourbillon", 0.0)),
        "tourbillon_on": _dans(q.get("tourbillonOn"), DECLENCHEURS, "basse"),
        "bits": float(q.get("bits", 0.0)),
        "bits_on": _dans(q.get("bitsOn"), DECLENCHEURS, "grosse caisse"),
        "tracking": float(q.get("tracking", 0.0)),
        "tracking_on": _dans(q.get("trackingOn"), DECLENCHEURS, "grosse caisse"),
        "cadence": int(float(q.get("cadence", 0))),
        "poussiere": float(q.get("poussiere", 0.0)),
        "flottement": float(q.get("flottement", 0.0)),
        "halo_doux": float(q.get("haloDoux", 0.0)),
        "echo": float(q.get("echo", 0.0)),
        "echo_n": int(float(q.get("echoN", 3))),
        "echo_delay": float(q.get("echoDelay", 0.045)),
        "couleurs": float(q.get("couleurs", 0.0)),
        "spectro": float(q.get("spectro", 0.0)),
        "snare": float(q.get("snare", 1.0)),
        "wave_gain": float(q.get("wave", 1.10)),
        "wave_win": float(q.get("waveWin", 0.070)),
        "wave_trig": float(q.get("waveTrig", 0.0)),
        "wave_passes": int(float(q.get("wavePasses", 1))),
        "wave_punch": float(q.get("wavePunch", 0.85)),
        "wave_smooth": int(float(q.get("waveSmooth", 56))),
        "split_px": float(q.get("splitPx", 11.0)),
        "trail": float(q.get("trail", 1.0)),
        # a defaut de titre saisi, celui du fichier : un champ vide laissait la
        # dalle sans nom, alors que la page affichait le nom en invite.
        "screen_title": str(q.get("title") or q.get("fallbackTitle") or ""),
        "backdrop": backdrop_paths(q.get("backdrop")),
        # la vitesse arrive en puissance de deux : le milieu du curseur vaut 1
        "fond_vitesse": 2.0 ** min(3.0, max(-3.0, float(q.get("fondVitesse", 0.0)))),
        "fond_boucle": _dans(q.get("fondBoucle"), BOUCLES_FOND, "boucle"),
        "fond_fondu": float(q.get("fondFondu", 0.0)),
        "fond_photo": float(q.get("fondPhoto", 6.0)),
        "backdrop_strength": float(q.get("bdStrength", 0.78)),
        "backdrop_clear": float(q.get("bdClear", 0.40)),
        "screen_dim": float(q.get("screenDim", 0.40)),
        "backdrop_sharp": float(q.get("bdSharp", 0.37)),
        "travel": float(q.get("travel", 0.0)),
        "travel_mode": _dans(q.get("travelMode"), TRAVELLINGS, "avant"),
        # ---- reactions au son
        "punch": float(q.get("punch", 0.032)),
        "punch_on": _dans(q.get("punchOn"), DECLENCHEURS, "grosse caisse"),
        "shake_amp": float(q.get("shake", 0.0)),
        "shake_on": _dans(q.get("shakeOn"), DECLENCHEURS, "grosse caisse"),
        "parts": float(q.get("parts", 0.0)),
        "parts_on": _dans(q.get("partsOn"), DECLENCHEURS, "caisse claire"),
        "parts_n": int(float(q.get("partsN", 14))),
        "parts_speed": float(q.get("partsSpeed", 1.0)),
        "parts_life": float(q.get("partsLife", 0.55)),
        "ring": float(q.get("ring", 0.0)),
        "ring_on": _dans(q.get("ringOn"), DECLENCHEURS, "grosse caisse"),
        "grid_pulse": float(q.get("gridPulse", 0.0)),
        "grid_on": _dans(q.get("gridOn"), DECLENCHEURS, "grosse caisse"),
        "bg_flash": float(q.get("bgFlash", 0.0)),
        "flash_on": _dans(q.get("flashOn"), DECLENCHEURS, "caisse claire"),
    }


MES_REGLAGES = os.path.join(WORKDIR, "mes-reglages.json")
MAX_REGLAGES = 200


def lire_mes_reglages():
    """Les reglages enregistres par l'utilisateur.

    Un fichier illisible ne doit pas empecher le studio de demarrer : on
    repart d'une liste vide plutot que de refuser d'ouvrir.
    """
    try:
        with open(MES_REGLAGES, encoding="utf-8") as f:
            tout = json.load(f)
        return tout if isinstance(tout, dict) else {}
    except (OSError, ValueError):
        return {}


def ecrire_mes_reglages(tout):
    """Ecrit a cote puis renomme : une coupure ne laisse pas un fichier a
    moitie ecrit a la place de tous les reglages d'une soiree."""
    os.makedirs(WORKDIR, exist_ok=True)
    moitie = MES_REGLAGES + ".en-cours"
    with open(moitie, "w", encoding="utf-8") as f:
        json.dump(tout, f, ensure_ascii=False, indent=1, sort_keys=True)
    os.replace(moitie, MES_REGLAGES)


def propre(valeurs):
    """Ce qui vient de la page, ramene a des cles et des valeurs simples."""
    if not isinstance(valeurs, dict):
        raise ValueError("réglages illisibles")
    out = {}
    for cle, v in list(valeurs.items())[:400]:
        cle = re.sub(r"[^A-Za-z0-9_-]", "", str(cle))[:40]
        if not cle:
            continue
        out[cle] = v if isinstance(v, (bool, int, float)) else str(v)[:120]
    return out


def purger_apercus(garder=3):
    """Efface les extraits d'apercu passes.

    Regler, c'est en demander des dizaines : sans ce menage, une seance de
    travail laisse des centaines de megaoctets dans le dossier de sortie.
    On garde les derniers, au cas ou le navigateur en relise encore un.
    """
    try:
        vus = sorted(glob.glob(os.path.join(OUTDIR, "apercu_*.webm")),
                     key=os.path.getmtime, reverse=True)
    except OSError:
        return
    for vieux in vus[garder:]:
        try:
            os.remove(vieux)
        except OSError:
            pass


def code_modifie():
    """L'instant de la derniere modification des fichiers du studio.

    Une mise a jour faite sans fermer le studio laisse le programme tourner
    sur l'ancien code, tandis que les taches de rendu — sous Windows, des
    interpreteurs neufs — relisent le nouveau. Les deux ne se comprennent
    plus, et le rendu s'arretait sur un message incomprehensible. On compare
    donc l'heure des fichiers a celle du demarrage.
    """
    ici = os.path.dirname(os.path.abspath(__file__))
    t = 0.0
    for nom in os.listdir(ici):
        if nom.endswith(".py"):
            try:
                t = max(t, os.path.getmtime(os.path.join(ici, nom)))
            except OSError:
                pass
    return t


CODE_AU_DEMARRAGE = code_modifie()
A_RELANCER = ("Le studio a ete mis a jour pendant qu'il tournait : il fait "
              "encore tourner l'ancienne version. Fermez la fenetre noire du "
              "studio et relancez-le, puis rechargez cette page.")


def perime():
    """Vrai si les fichiers ont change depuis le demarrage."""
    return code_modifie() > CODE_AU_DEMARRAGE + 1.0


def _entier(v, defaut):
    """Un nombre venu de la page, ou la valeur par defaut.

    Une liste restee vide envoie une chaine vide ou un null : mieux vaut un
    rendu a trente images par seconde qu'un « int() argument must be... »
    au milieu d'un rendu.
    """
    try:
        return int(float(v))
    except (TypeError, ValueError):
        return int(defaut)


def backdrop_paths(noms):
    """Les chemins des fonds deposes, dans l'ordre de la page, ou None. Les
    noms viennent de la page, separes par « | » : chacun est ramene a un
    simple nom de fichier dans le dossier prevu."""
    if not noms:
        return None
    chemins = []
    for nom in str(noms).split("|"):
        if nom.strip():
            p = os.path.join(FONDS, safe_name(nom))
            if os.path.exists(p):
                chemins.append(p)
    return chemins or None


# --------------------------------------------------------------------------
#  La frise : ce que la page dessine sous l'apercu
# --------------------------------------------------------------------------

# Colonnes par seconde de la forme d'onde. A 25, une frise zoomee sur vingt
# secondes garde encore une colonne tous les deux ou trois pixels ; le
# morceau entier pese 30 Ko.
ONDE_PAR_S = 25
# Les trois bandes que la frise superpose : la grosse caisse et la basse, le
# corps des voix et des instruments, le brillant des cymbales.
ONDE_BANDES = ((20.0, 160.0), (160.0, 2500.0), (2500.0, 16000.0))


def onde_du_son(mono, sr, par_s=ONDE_PAR_S):
    """L'energie du son dans trois bandes de frequences, de 0 a 255.

    Une colonne par 1/par_s seconde, mesuree sur une fenetre de deux pas :
    une attaque tombee entre deux colonnes compte quand meme. Chaque bande
    est ramenee a son propre maximum — sinon les aigus, cent fois moins
    energiques que la basse, ne se verraient pas —, mais sans etre gonflee
    plus de cinq fois : un morceau sans basse ne s'en invente pas une. La
    page, qui loge souvent dix colonnes dans un pixel, en prend la moyenne
    quadratique : le maximum, lui, ne gardait que les coups de charley.
    """
    x = np.asarray(mono, np.float32)
    sr = int(sr)
    pas = max(1, int(round(sr / float(par_s))))
    n = max(1, len(x) // pas)
    N = 1
    while N < 2 * pas:
        N *= 2
    fenetre = np.hanning(N).astype(np.float32)
    f = np.fft.rfftfreq(N, 1.0 / sr)
    masques = [(f >= lo) & (f < hi) for lo, hi in ONDE_BANDES]
    xp = np.pad(x, (N // 2, N // 2 + pas))
    vues = np.lib.stride_tricks.sliding_window_view(xp, N)[::pas][:n]
    e = np.zeros((len(ONDE_BANDES), n), np.float32)
    for i0 in range(0, n, 256):
        p = np.abs(np.fft.rfft(vues[i0:i0 + 256] * fenetre, axis=1)) ** 2
        for b, m in enumerate(masques):
            e[b, i0:i0 + len(p)] = np.sqrt(p[:, m].sum(axis=1))
    ref = np.percentile(e, 99.0, axis=1)
    ref = np.maximum(np.maximum(ref, 0.2 * float(ref.max())), 1e-9)
    # lineaire : une courbe qui releve les faibles niveaux faisait de tout
    # le morceau un mur, ou l'on ne voyait plus ni l'intro ni le creux
    v = np.clip(e / ref[:, None], 0.0, 1.0)
    return {"par_s": float(sr) / pas, "n": int(n),
            "bandes": [base64.b64encode((b * 255.0 + 0.5).astype(np.uint8)
                                        .tobytes()).decode("ascii")
                       for b in v]}


def plan_des_fonds(chemins, vitesse, boucle, fondu, photo, total):
    """Ce que la suite de fonds montre au fil du morceau, par tranches.

    Le meme plan_fonds que le rendu, lu tous les quelques dixiemes : la
    frise montre donc exactement ce que la video montrera. Rend les blocs
    [debut, fin, element, sens] — sens -1 quand l'element se joue a l'envers,
    en aller-retour — et les fondus [debut, fin].
    """
    genres = [genre_fond(p) for p in chemins]
    durees = _durees_fonds(genres, vitesse, photo)
    total = max(0.0, float(total))
    if not durees or total <= 0.0:
        return [], []
    pas = max(0.05, total / 4000.0)
    blocs, fondus = [], []
    for i in range(int(np.ceil(total / pas))):
        t = i * pas
        res = plan_fonds(durees, t, boucle, fondu)
        k, u, _w = max(res, key=lambda r: r[2])
        # le sens se lit sur l'instant dans l'element, un pas plus loin
        suite = [r for r in plan_fonds(durees, t + pas, boucle, fondu)
                 if r[0] == k]
        sens = -1 if suite and suite[0][1] < u - 1e-9 else 1
        if genres[k][0] != "video":
            sens = 1                    # une photo n'a pas d'envers
        fin = min(total, t + pas)
        if blocs and blocs[-1][2] == k and blocs[-1][3] == sens:
            blocs[-1][1] = fin
        else:
            blocs.append([t, fin, k, sens])
        if len(res) > 1:
            if fondus and abs(fondus[-1][1] - t) < 1e-6:
                fondus[-1][1] = fin
            else:
                fondus.append([t, fin])
    def arrondi(liste):
        return [[round(x, 3) if isinstance(x, float) else x for x in b]
                for b in liste]
    return arrondi(blocs), arrondi(fondus)


# --------------------------------------------------------------------------
#  Etat : analyses et rendus en cours
# --------------------------------------------------------------------------

class Depasse(Exception):
    """Un apercu demande puis remplace par un autre avant d'etre dessine."""


class FondsDirect:
    """Les fonds de l'ecoute en direct : une video se lit a la suite, par un
    lecteur qui reste ouvert (LecteurFond), une photo se garde telle quelle.
    Sans cela chaque image du direct relancait ffmpeg sur le fond, et la
    video de fond divisait la cadence par trois. Un lecteur qui n'a pas servi
    depuis quelques secondes est ferme."""

    OUBLI = 4.0

    def __init__(self):
        self.lecteurs = {}            # (chemin, l, h, flou) -> [lecteur, vu]
        self.photos = {}              # (chemin, l, h, flou) -> [image, vu]

    def lire(self, path, aw, ah, seek, blur):
        cle = (path, aw, ah, round(float(blur), 3))
        maintenant = time.time()
        g = genre_fond(path)
        if g[0] != "video":
            if cle not in self.photos:
                self.photos[cle] = [_image_fond(path, aw, ah, 0.0, blur), 0.0]
            self.photos[cle][1] = maintenant
            return self.photos[cle][0].copy()
        if cle not in self.lecteurs:
            self.lecteurs[cle] = [LecteurFond(path, aw, ah, blur, g[1], g[2]), 0.0]
        self.lecteurs[cle][1] = maintenant
        return self.lecteurs[cle][0].image(seek)

    def menage(self, tout=False):
        limite = float("inf") if tout else time.time() - self.OUBLI
        for cle, (lect, vu) in list(self.lecteurs.items()):
            if vu < limite:
                lect.fermer()
                del self.lecteurs[cle]
        for cle, (_, vu) in list(self.photos.items()):
            if vu < limite:
                del self.photos[cle]


class Studio:
    """Garde en memoire ce qui coute cher a recalculer.

    L'analyse d'un morceau (tempo, coups, paroxysmes) prend quelques secondes
    et le moteur de rendu quelques centaines de millisecondes a construire :
    on les garde sous la main pour que deplacer un curseur de couleur
    reaffiche l'image instantanement.
    """

    def __init__(self):
        self.lock = threading.Lock()
        self.tracks = {}          # id -> {path, name, info}
        self.renderers = {}       # (id, w, h, curve) -> Renderer
        self.jobs = {}            # id -> etat d'un rendu
        self.render_lock = threading.Lock()
        # Le moteur garde en memoire est repose avant chaque apercu (couleur,
        # fond, reglages). Deux apercus menes de front se marcheraient donc
        # dessus : on n'en dessine qu'un a la fois.
        self.draw = threading.Lock()
        self.shot_seq = 0         # numero du dernier apercu demande
        self.direct = FondsDirect()   # les fonds de l'ecoute en direct

    @staticmethod
    def frappes(info):
        """Combien de fois chaque famille d'instruments frappe dans le morceau.

        C'est ce qui permet au studio d'annoncer, sous chaque curseur, a quelle
        frequence l'effet partira : un effet pose sur le charley se declenche
        souvent des milliers de fois, la ou la grosse caisse en compte
        quelques centaines. Sans ce chiffre, on regle a l'aveugle.
        """
        return compte_frappes([e[1] for e in info["_audio"]["events"]])

    # ---- morceaux
    def add_track(self, path, name):
        tid = "%x" % (abs(hash((path, os.path.getsize(path)))) & 0xFFFFFFFF)
        info = analyze(path, 0.0, None)
        with self.lock:
            self.tracks[tid] = {"id": tid, "path": path, "name": name,
                                "info": info}
        return tid, info

    def track(self, tid):
        with self.lock:
            t = self.tracks.get(tid)
        if not t:
            raise KeyError("morceau inconnu (relancez l'envoi)")
        return t

    def grille(self, tr, bpm=0.0):
        """La grille du morceau entier pour ce tempo : mesuree une fois.

        Mesuree sur le morceau entier et non sur l'extrait rendu : l'apercu et
        le rendu posent ainsi les notes au meme endroit, et la mesure est
        d'autant plus juste qu'elle porte sur plus de temps.
        """
        cle = round(float(bpm or 0.0), 3)
        with self.lock:
            g = tr.setdefault("grilles", {}).get(cle)
        if g is None:
            g = grille_du_morceau(tr["info"], cle or None)
            with self.lock:
                tr["grilles"][cle] = g
        return g

    def calage(self, tr, chemin, bpm=0.0, tel_quel=False, cale=False):
        """Les notes du fichier posees sur le morceau, et le compte rendu.

        Le meme calcul sert a l'apercu, au rendu et a la page : c'est ce qui
        garantit que la touche s'allume dans la video la ou on l'a vue.
        """
        reg = {"midi": chemin, "midi_cale": cale, "midi_tel_quel": tel_quel}
        if not tel_quel:
            g = self.grille(tr, bpm)
            reg["midi_bpm"] = g["bpm"]
            reg["midi_phase"] = g["phase"] if g.get("phase_sure") else None
        return preparer_midi(tr["info"], reg)

    def onde(self, tr):
        """La forme d'onde du morceau pour la frise, calculee une fois."""
        with self.lock:
            o = tr.get("onde")
        if o is None:
            a = tr["info"]["_audio"]
            o = onde_du_son(a["mono"], a["sr"])
            with self.lock:
                tr["onde"] = o
        return o

    def info_courante(self):
        """L'analyse du dernier morceau depose, s'il y en a un.

        Sert au depot d'une melodie : le calage se cherche sur les attaques du
        morceau, donc il faut un morceau. Sans lui on accepte quand meme le
        fichier — on ne peut simplement pas encore dire de combien caler.
        """
        with self.lock:
            if not self.tracks:
                return None
            return list(self.tracks.values())[-1]["info"]

    # ---- apercu
    def still(self, tid, t, q, w, h):
        """Une image, avec les reglages du moment.

        Le moteur est garde par (morceau, definition, bombe) : c'est lui qui
        coute cher a construire, parce qu'il depouille tout le son. La
        couleur et le fond, eux, n'en dependent pas — on les repose sur le
        moteur existant, et bouger un curseur redevient instantane.
        """
        # Les vignettes des styles se demandent a six d'affilee : elles
        # s'annuleraient l'une l'autre, et surtout annuleraient l'apercu
        # principal. Elles passent donc a cote de la regle du dernier arrive,
        # tout en attendant leur tour pour dessiner.
        if q.get("vignette") == "1":
            with self.draw:
                return self._still(tid, t, q, w, h)
        with self.lock:
            self.shot_seq += 1
            mine = self.shot_seq
        with self.draw:
            with self.lock:
                if mine != self.shot_seq:
                    # Un reglage a bouge pendant qu'on attendait : cette image
                    # ne sera jamais regardee. La calculer ferait patienter
                    # celle qui compte, sur une machine modeste d'autant plus.
                    raise Depasse()
            return self._still(tid, t, q, w, h)

    def _still(self, tid, t, q, w, h):
        tr = self.track(tid)
        palette, kw = look_from(q)
        # La finesse du trait est fixee a la construction du moteur (elle
        # decide de l'etalement du faisceau) : la changer demande donc un
        # moteur neuf, contrairement a la couleur ou au fond qu'on repose.
        # La machine decide de toute la geometrie : en changer demande un
        # moteur neuf, comme la finesse du trait.
        key = (tid, w, h, _coche(q.get("curve"), True), kw["nettete"],
               kw["machine"])
        with self.lock:
            r = self.renderers.get(key)
        if r is None:
            # la melodie est posee plus bas, une fois pour toutes : la lire
            # et la caler a chaque apercu couterait une seconde par curseur
            # deplace
            r = _renderer(tr["info"], w, h, 30, 7, _coche(q.get("curve"), True),
                          palette, dict(kw, midi=""))
            with self.lock:
                if len(self.renderers) > 2:        # ne pas garder tout l'historique
                    self.renderers.pop(next(iter(self.renderers)))
                self.renderers[key] = r
        # set_look ne connait que la couleur et le fond ; les deux autres
        # reglages se posent directement sur l'instance
        # wave_smooth passe par une methode : il faut relisser la courbe
        POSE = ("wobble", "split", "split_px", "split_count", "split_on", "snare",
                "wave_gain", "wave_win", "wave_trig", "wave_passes",
                "wave_punch", "trail", "screen_title", "glitch",
                "punch", "punch_on", "shake_amp", "shake_on",
                "parts", "parts_on", "parts_n", "parts_speed", "parts_life",
                "ring", "ring_on", "grid_pulse", "grid_on",
                "bg_flash", "flash_on",
                "tranches", "tranches_on", "roll", "roll_on",
                "ghost", "ghost_on", "blocs", "blocs_on",
                "invert", "invert_on", "inversion", "inversion_on",
                "stut", "stut_on", "stut_loop",
                "scramble", "scr_len", "miroir", "miroir_on",
                "ondul", "ondul_on", "mosaic", "mosaic_on",
                "kaleido", "kaleido_on", "cisaille", "cisaille_on",
                "coupure", "coupure_on", "tapestop", "tapestop_on",
                "tri", "tri_on", "rvb", "rvb_on", "retro", "retro_on",
                "macro", "macro_on", "tourbillon", "tourbillon_on",
                "bits", "bits_on", "tracking", "tracking_on",
                "cadence", "poussiere", "flottement", "halo_doux",
                "echo", "echo_n", "echo_delay", "couleurs", "step_div",
                "presence", "neon", "reflet", "tube",
                "passage", "passage_turb", "midi_force",
                "vignettage", "scanlines", "aberration",
                "eclat_pads", "couleur_coups", "couleur_coups_libre",
                "texture_touches", "mode_trait", "encre", "detourage",
                "papier", "inverser")
        APART = POSE + ("wave_smooth", "backdrop", "backdrop_strength",
                        "fond_vitesse", "fond_boucle", "fond_fondu",
                        "fond_photo",
                        "backdrop_clear", "screen_dim", "travel", "travel_mode",
                        "backdrop_sharp", "spectro", "nettete", "taille",
                        "machine",
                        # le plan de machines et la melodie se posent a la
                        # main : l'un se relit, l'autre se lit dans un fichier
                        "machines", "midi", "midi_offset", "midi_cale",
                        "midi_tempo", "midi_type", "midi_bpm",
                        "midi_tel_quel",
                        # les blocs de la frise se posent apres tout le reste
                        "effets")
        # La taille se pose avant l'allure : c'est elle qui decide du creux
        # que la texture garde derriere la machine, et set_look le recalcule.
        r.taille = float(kw["taille"])
        r.set_look(palette, **{k: v for k, v in kw.items() if k not in APART})
        for k in POSE:
            setattr(r, k, kw[k])
        # Le sequenceur de machines : le plan se relit a chaque apercu, il ne
        # coute rien. On oublie ensuite la machine posee, pour que le moteur
        # la repose selon le nouveau plan a l'instant regarde.
        r.plan_mach = lire_plan_machines(kw["machines"], kw["machine"],
                                         tr["info"]["duration"])
        r._cle_mach = None
        r.poser_machine(float(t))

        # La melodie : lue et calee une seule fois par fichier. Le decalage de
        # la page s'ajoute a celui trouve tout seul.
        # La case « chercher le decalage » fait partie de la cle : la cocher
        # change le decalage calcule, il faut donc relire.
        # Le tempo et la lecture « telle quelle » en font partie : les changer
        # repose les notes.
        chemin = (kw["midi"], bool(kw["midi_cale"]), bool(kw["midi_tel_quel"]),
                  round(float(kw["midi_bpm"]), 3))
        if getattr(r, "_midi_de", None) != chemin:
            r._midi_de = chemin
            r.midi, r._midi_auto, r._midi_grille = None, 0.0, False
            if chemin[0]:
                reg, lu = self.calage(tr, chemin[0], kw["midi_bpm"],
                                      kw["midi_tel_quel"], chemin[1])
                if lu and lu.get("notes"):
                    r.midi = np.asarray(reg["midi"],
                                        dtype=np.float64).reshape(-1, 4)
                    r._midi_auto = float(reg["midi_offset"])
                    r.midi_transpose = int(reg["midi_transpose"])
                    r._midi_grille = not kw["midi_tel_quel"]
        r.midi_offset = r._midi_auto + float(kw["midi_offset"])
        # posees sur la grille, les notes ont deja le tempo du morceau : la
        # derive ne s'applique qu'a un fichier lu tel quel
        r.midi_tempo = 1.0 if r._midi_grille else float(kw["midi_tempo"])
        r.midi_type = kw["midi_type"]

        # Le spectrogramme est calcule a partir du son, pas repose comme une
        # couleur : on ne le refait que lorsqu'on l'allume pour la premiere fois.
        if (kw["spectro"] > 0.01 or any(e["e"] == "spectro" and e["v"] > 0.01
                                        for e in kw["effets"])) \
                and getattr(r, "spec", None) is None:
            r.spec, r.spec_fps = compute_spectro(
                tr["info"]["_audio"]["mono"], tr["info"]["_audio"]["sr"])
        r.spectro = kw["spectro"]
        if getattr(r, "_smooth_at", None) != kw["wave_smooth"]:
            r.set_wave_smooth(kw["wave_smooth"])
            r._smooth_at = kw["wave_smooth"]
        # Le fond de l'apercu est une image fixe, meme quand c'est une video :
        # on extrait la seule image de l'instant regarde (une demi-seconde)
        # plutot que de detailler tout le fichier, ce que le rendu fera.
        r._split_t = None            # le classement depend de split_count
        bd = kw["backdrop"]
        # L'ecoute en direct demande des images a la suite : ses videos de
        # fond se lisent d'un trait, et suivent au centieme de seconde
        direct = q.get("rapide") == "1"
        stamp = (tuple(bd or ()), w, h, kw["backdrop_strength"],
                 kw["backdrop_clear"], kw["screen_dim"],
                 round(float(t), 2 if direct else 1),
                 kw["travel"], kw["travel_mode"], kw["backdrop_sharp"],
                 kw["taille"], kw["machine"], kw["fond_vitesse"],
                 kw["fond_boucle"], kw["fond_fondu"], kw["fond_photo"])
        # set_look, plus haut, remet le fond a zero — il fait partie de
        # l'allure. On le repose donc ici a chaque fois, en ne le rechargeant
        # que si un de ses reglages a bouge : sans cela, tout apercu qui ne
        # rechargeait pas le fond le perdait purement et simplement.
        if bd:
            if getattr(r, "_bd_stamp", None) != stamp:
                # le travelling s'etale sur tout le morceau : l'apercu montre
                # le cadre de l'instant regarde, pas celui du debut
                # le meme plan que le rendu : quelle video, a quel instant,
                # ou quel fondu entre deux, a la vitesse choisie
                r._bd = apercu_fonds(
                    bd, w, h, float(t), max(tr["info"]["duration"], 1e-3),
                    vitesse=kw["fond_vitesse"], boucle=kw["fond_boucle"],
                    fondu=kw["fond_fondu"], photo=kw["fond_photo"],
                    blur=backdrop_quality(kw["backdrop_sharp"])[0],
                    strength=kw["backdrop_strength"],
                    clear=kw["backdrop_clear"], scale=r.scale * r.taille,
                    screen_dim=kw["screen_dim"], ecran=r.ecran,
                    travel=kw["travel"], travel_mode=kw["travel_mode"],
                    lire=self.direct.lire if direct else None)
                r._bd_stamp = stamp
            r.backdrop = r._bd
        else:
            r.backdrop, r._bd, r._bd_stamp = None, None, None
        # les lecteurs que l'ecoute n'a plus demandes depuis un moment
        self.direct.menage()
        dur = tr["info"]["duration"]
        # l'apercu montre le morceau tel qu'il joue, sans les fondus des bords
        t = max(0.6, min(float(t), dur - 0.8))
        # les effets places sur la frise, une fois tous les reglages poses :
        # le moteur est garde d'un apercu a l'autre, on le rend tel qu'on l'a
        # trouve
        poser_effets(r, kw["effets"])
        try:
            return frame_performance(r, t, dur + 10.0)
        finally:
            retablir_effets(r)

    # ---- rendu
    def start_job(self, q):
        tr = self.track(q["track"])
        jid = "%x" % int(time.time() * 1000)
        start = float(q.get("start", 0.0))
        dur = q.get("duration")
        dur = float(dur) if dur else None
        # L'apercu en mouvement se lit dans la page : il part en VP8, qu'aucun
        # navigateur ne refuse. Si ffmpeg ne sait pas l'encoder, il redevient
        # un MP4 ordinaire plutot que d'echouer.
        apercu = bool(q.get("apercu")) and apercu_possible()
        ext = ".webm" if apercu else ".mp4"
        if apercu:
            purger_apercus()
        out = os.path.join(OUTDIR, "%s%s_%s%s"
                           % ("apercu_" if apercu else "",
                              os.path.splitext(tr["name"])[0][:60], jid, ext))
        job = {"id": jid, "state": "attente", "done": 0, "total": 0, "eta": 0.0,
               "out": out, "name": os.path.basename(out), "error": None,
               "apercu": apercu, "_arret": threading.Event()}
        with self.lock:
            self.jobs[jid] = job
        threading.Thread(target=self._run, args=(job, tr, q, start, dur),
                         daemon=True).start()
        return job

    def arreter(self, jid):
        """Demande l'arret d'un rendu : il s'arrete au prochain paquet
        d'images, ou avant meme de commencer s'il attendait son tour."""
        with self.lock:
            job = self.jobs.get(jid)
        if job is None:
            raise ValueError("rendu inconnu")
        if job["state"] not in ("fini", "erreur", "arrete"):
            job["_arret"].set()
            job["state"] = "arret"
        return job

    def _run(self, job, tr, q, start, dur):
        with self.render_lock:       # le moteur utilise des globales : un a la fois
            if job["_arret"].is_set():
                job["state"] = "arrete"
                return
            try:
                # Un moteur d'apercu pese quelques centaines de megaoctets ;
                # les garder pendant le rendu, c'est autant de place en moins
                # pour les taches de calcul. On les relache, quitte a
                # recalculer une image au retour.
                with self.lock:
                    self.renderers.clear()
                if perime():
                    raise RuntimeError(A_RELANCER)
                job["state"] = "analyse"
                palette, kw = look_from(q)
                if kw.get("midi") and not kw.get("midi_tel_quel"):
                    g = self.grille(tr, kw.get("midi_bpm"))
                    kw["midi_bpm"] = g["bpm"]
                    kw["midi_phase"] = (g["phase"] if g.get("phase_sure")
                                        else None)
                full = tr["info"]["duration"]
                info = (tr["info"] if start <= 0.01 and (not dur or dur >= full - 0.01)
                        else analyze(tr["path"], start, dur))
                job["total"] = int(round(info["duration"] * _entier(q.get("fps"), 30)))
                job["state"] = "rendu"

                def prog(done, total, el):
                    job["done"], job["total"] = done, total
                    job["eta"] = el / max(done, 1) * (total - done)

                # Sans consigne, c'est la qualite choisie qui fixe la
                # compression : imposer un CRF ici annulerait le reglage.
                crf = q.get("crf")
                render_video(tr["path"], job["out"], info=info,
                             width=_entier(q.get("width"), 1920),
                             height=_entier(q.get("height"), 1080),
                             fps=_entier(q.get("fps"), 30),
                             crf=int(crf) if crf else None,
                             quality=("apercu" if job["apercu"] else
                                      _dans(q.get("quality"), QUALITES,
                                            "compatible")),
                             curve=_coche(q.get("curve"), True),
                             palette=palette, progress=prog,
                             arret=job["_arret"].is_set, **kw)
                job["size"] = os.path.getsize(job["out"])
                job["state"] = "fini"
            except RenduArrete:
                job["state"] = "arrete"
            except Exception as e:                       # noqa: BLE001
                # nos propres messages se suffisent ; les autres ont besoin
                # de leur nom pour etre rapportables
                job["error"] = (str(e) if isinstance(e, (ValueError, RuntimeError))
                                else "%s: %s" % (type(e).__name__, e))
                job["state"] = "erreur"
                traceback.print_exc()


STUDIO = Studio()


# --------------------------------------------------------------------------
#  Exemples : sous chaque effet, ce qu'il fait (voir tools/exemples.py)
# --------------------------------------------------------------------------

EXEMPLES = {"proc": None}


def dossier_exemples():
    return os.path.join(WORKDIR, "exemples", VERSION)


def lancer_exemples():
    """Fabrique les exemples de cette version s'ils manquent, a cote.

    Dans un processus a part, de priorite basse : quelques minutes de calcul
    la premiere fois, qui ne doivent ni bloquer la page ni ralentir les
    apercus. Les exemples deja faits restent : seuls les manquants sont
    calcules.
    """
    if os.path.exists(os.path.join(dossier_exemples(), "index.json")):
        return
    if EXEMPLES["proc"] is not None and EXEMPLES["proc"].poll() is None:
        return
    try:
        EXEMPLES["proc"] = subprocess.Popen(
            [sys.executable, os.path.join(ROOT, "tools", "exemples.py")],
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, cwd=ROOT)
    except OSError:
        EXEMPLES["proc"] = None


def exemples_prets():
    """Les exemples deja faits, et si la fabrication continue."""
    d = dossier_exemples()
    try:
        noms = os.listdir(d)
    except OSError:
        noms = []
    prets = sorted(n[:-4].replace("--", "=") for n in noms
                   if n.endswith(".jpg") and n[:-4] + ".webp" in noms)
    p = EXEMPLES["proc"]
    return {"prets": prets,
            "en_cours": bool(p is not None and p.poll() is None)}


# --------------------------------------------------------------------------
#  Serveur
# --------------------------------------------------------------------------

class Handler(BaseHTTPRequestHandler):
    server_version = "OmnipotardStudio/1.0"

    def log_message(self, fmt, *a):                      # silence les logs HTTP
        pass

    # HTTP/1.1 plutot que 1.0 : une reponse envoyee alors que le navigateur
    # depose encore son fichier lui parvient au lieu de lui claquer la
    # connexion au nez. C'est ce qui transformait « fichier trop gros » en
    # « Failed to fetch », un message qui ne dit rien a personne.
    protocol_version = "HTTP/1.1"
    # Chaque reponse part tout de suite. Sinon le systeme retient la fin
    # d'une image le temps que le navigateur accuse reception du debut, ce
    # qu'il fait avec 40 ms de retard : l'ecoute en direct perdait ainsi la
    # moitie de ses images.
    disable_nagle_algorithm = True

    def handle_one_request(self):
        self._begun = False          # une connexion peut servir plusieurs fois
        self._reste = 0              # ce qui n'a pas encore ete lu du corps
        try:
            return BaseHTTPRequestHandler.handle_one_request(self)
        finally:
            self._vider()

    _reste = 0

    def _vider(self):
        """Avale la fin du corps quand on n'en a pas voulu.

        La connexion resservira pour la requete suivante : y laisser la fin
        d'un envoi ferait lire ces octets-la comme une nouvelle requete.
        """
        try:
            while self._reste > 0:
                bloc = self.rfile.read(min(BLOC, self._reste))
                if not bloc:
                    break
                self._reste -= len(bloc)
        except OSError:
            self._reste = 0

    def _recevoir(self, path, limite):
        """Ecrit le fichier depose sur le disque, bloc par bloc.

        Tout garder en memoire le temps de l'ecrire demandait autant de
        memoire que le fichier pesait : une video de telephone suffisait a
        mettre a genoux une machine modeste, et le studio mourait au milieu
        de l'envoi — cote page, un « Failed to fetch » sans explication.

        Renvoie faux si le fichier depasse la limite. Dans ce cas on avale
        quand meme tout ce que le navigateur envoie avant de repondre :
        repondre au milieu d'un envoi coupe la connexion, et le message
        n'arrive jamais jusqu'a la page.
        """
        self._reste = int(self.headers.get("Content-Length") or 0)
        trop = self._reste > limite
        # On ecrit a cote, et on ne donne son nom au fichier qu'une fois
        # complet : un envoi coupe en chemin laissait sinon un fichier
        # tronque que le studio reprenait ensuite pour un bon.
        moitie = path + ".en-cours"
        f = None if trop else open(moitie, "wb")
        souci = None
        try:
            while self._reste > 0:
                bloc = self.rfile.read(min(BLOC, self._reste))
                if not bloc:
                    raise RuntimeError("l'envoi s'est interrompu en chemin")
                self._reste -= len(bloc)
                if f and souci is None:
                    try:
                        f.write(bloc)
                    except OSError as e:      # disque plein, par exemple
                        souci = e
        finally:
            if f:
                f.close()
                if souci is None and self._reste == 0:
                    os.replace(moitie, path)
                elif os.path.exists(moitie):
                    os.remove(moitie)
        if souci is not None:
            raise souci
        return not trop

    def _corps(self):
        """Le corps d'une requete de reglages — court, donc lu d'un coup."""
        self._reste = int(self.headers.get("Content-Length") or 0)
        if self._reste > 4 * MO:
            return None
        corps = self.rfile.read(self._reste)
        self._reste = 0
        return corps

    # ---- reponses
    #
    # Une reponse part en une seule fois, et une seule. Si l'envoi echoue a
    # mi-parcours — l'onglet a coupe la connexion parce qu'un reglage a bouge
    # et que l'apercu precedent ne l'interesse plus — il ne faut surtout pas
    # en poster une deuxieme derriere : le navigateur recollerait l'image a
    # moitie envoyee et l'en-tete de la suivante, et afficherait une image
    # coupee en deux, comme un fichier abime. D'ou ce drapeau.
    _begun = False

    def _send(self, code, ctype, body, extra=None):
        if self._begun:
            return
        self._begun = True
        try:
            self.send_response(code)
            # une reponse vide (204) n'annonce ni genre ni longueur
            if code != 204:
                self.send_header("Content-Type", ctype)
                self.send_header("Content-Length", str(len(body)))
            if "Cache-Control" not in (extra or {}):
                self.send_header("Cache-Control", "no-store")
            for k, v in (extra or {}).items():
                self.send_header(k, v)
            self.end_headers()
            self.wfile.write(body)
        except OSError:
            # Connexion coupee par le navigateur. Unix appelle cela un tube
            # brise ou une remise a zero, Windows un abandon (WinError 10053) :
            # trois exceptions differentes, une seule situation, parfaitement
            # normale. On ratisse donc large plutot que de nommer chaque cas.
            pass

    def _tranche(self, taille):
        """Le morceau de fichier demande par un en-tete Range, ou None.

        Une balise video ne se contente pas de telecharger : elle demande des
        tranches, pour demarrer sans tout avoir et pour se deplacer dedans.
        Sans cette reponse-la, l'apercu en mouvement restait noir.
        """
        brut = self.headers.get("Range") or ""
        if not brut.startswith("bytes=") or "," in brut:
            return None
        deb, _, fin = brut[6:].partition("-")
        try:
            if deb == "":                       # « les N derniers octets »
                n = int(fin)
                return (max(0, taille - n), taille - 1) if n > 0 else None
            a = int(deb)
            b = int(fin) if fin else taille - 1
        except ValueError:
            return None
        b = min(b, taille - 1)
        return (a, b) if 0 <= a <= b else None

    def _fichier(self, path, ctype, extra=None):
        """Envoie un fichier par blocs, entier ou par tranches.

        Une video rendue pese des centaines de megaoctets : la charger
        entierement pour la remettre au navigateur demandait cette memoire
        une deuxieme fois, juste pour la recopier.
        """
        if self._begun:
            return
        self._begun = True
        try:
            taille = os.path.getsize(path)
            tranche = self._tranche(taille)
            a, b = tranche if tranche else (0, taille - 1)
            self.send_response(206 if tranche else 200)
            self.send_header("Content-Type", ctype)
            self.send_header("Accept-Ranges", "bytes")
            self.send_header("Content-Length", str(b - a + 1))
            if tranche:
                self.send_header("Content-Range",
                                 "bytes %d-%d/%d" % (a, b, taille))
            self.send_header("Cache-Control", "no-store")
            for k, v in (extra or {}).items():
                self.send_header(k, v)
            self.end_headers()
            reste = b - a + 1
            with open(path, "rb") as f:
                f.seek(a)
                while reste > 0:
                    bloc = f.read(min(BLOC, reste))
                    if not bloc:
                        break
                    reste -= len(bloc)
                    self.wfile.write(bloc)
        except OSError:
            pass

    def _json(self, obj, code=200):
        self._send(code, "application/json; charset=utf-8",
                   json.dumps(obj).encode("utf-8"))

    def _fail(self, e, code=400):
        """Le message que la page affichera.

        `str()` sur une KeyError rend la cle entre guillemets, et certaines
        exceptions n'ont pas de message du tout : sans le nom de la classe,
        la page afficherait une ligne vide, impossible a rapporter.
        """
        if isinstance(e, str):
            msg = e
        else:
            texte = (str(e.args[0]) if isinstance(e, KeyError) and e.args
                     else str(e))
            msg = texte or e.__class__.__name__
            if not isinstance(e, (ValueError, RuntimeError)):
                msg = "%s : %s" % (e.__class__.__name__, msg)
        self._json({"error": msg}, code)

    # ---- GET
    def do_GET(self):
        u = urlparse(self.path)
        q = {k: v[0] for k, v in parse_qs(u.query).items()}
        try:
            if u.path == "/favicon.ico":
                # Le navigateur la reclame de lui-meme. Sans reponse, chaque
                # page ouverte laissait une erreur 404 dans la console — sans
                # consequence, mais elle noyait les vraies.
                return self._send(204, "image/x-icon", b"")
            if u.path in ("/", "/index.html"):
                return self._send(200, "text/html; charset=utf-8", PAGE.encode("utf-8"))
            if u.path in ("/v1", "/v2"):
                # Il y a eu deux pages ; il n'y en a plus qu'une. Un ancien
                # favori ou un ancien lanceur y menent encore.
                self.send_response(302)
                self.send_header("Location", "/")
                self.end_headers()
                return
            if u.path == "/config":
                return self._json({
                    "version": version(),
                    "perime": perime(),
                    "palettes": {k: {"trait": rgb_to_hex(v[0]),
                                     "fond": rgb_to_hex(v[3])}
                                 for k, v in sorted(PALETTES.items())},
                    "fonds": list(BACKGROUNDS),
                    "instruments": list(INSTRUMENTS),
                    "declencheurs": [{"titre": t, "noms": n}
                                     for t, n in groupes_declencheurs()],
                    "travellings": list(TRAVELLINGS),
                    "qualites": {k: v["quoi"] for k, v in QUALITES.items()},
                    "machines": [{"cle": k, "nom": v["nom"], "quoi": v["quoi"]}
                                 for k, v in MACHINES.items()],
                    "presets": {k: {CHAMPS[a]: b for a, b in v.items()}
                                for k, v in PRESETS.items()},
                    # les styles, dans les noms des curseurs de la page
                    "styles": {k: {"quoi": v["quoi"],
                                   "reglages": {CHAMPS[a]: b for a, b
                                                in v["reglages"].items()}}
                               for k, v in STYLES.items()},
                    "mes": lire_mes_reglages(),
                    "aide": AIDE, "compte": COMPTE,
                    # les effets qu'on peut limiter a un passage de la frise :
                    # s'ils partent sur un instrument, s'ils vont par crans
                    "placables": {k: {"inst": bool(v[1]), "entier": bool(v[2])}
                                  for k, v in EFFETS_PLACABLES.items()},
                    "fondu_effet": FONDU_EFFET,
                })
            if u.path == "/still":
                q["curve"] = q.get("curve", "1") == "1"
                img = STUDIO.still(q["track"], float(q.get("t", 0.0)), q,
                                   int(q.get("w", 640)), int(q.get("h", 360)))
                # l'ecoute en direct demande des images a la chaine : en JPEG,
                # cinq fois plus vite encodees
                if q.get("rapide") == "1":
                    corps, genre = jpeg_bytes(img)
                    return self._send(200, genre, corps)
                return self._send(200, "image/png", png_bytes(img))
            if u.path == "/instant_vignette":
                # L'instant ou prendre les vignettes des styles : un coup de
                # grosse caisse ordinaire, le plus proche de l'instant regarde.
                #
                # Ordinaire, et c'est tout le point. Les coups qui declenchent
                # le dedoublement du trait superposent trois copies rouge,
                # vert et bleu : mesure sur la MPC, 46 % de l'image vire au
                # blanc sur un tel coup, contre 0,4 % sur un coup ordinaire.
                # Prendre les plus gros coups — l'idee de depart — donnait
                # donc cinq vignettes sur six cramees. On ecarte ceux-la, avec
                # les reglages de dedoublement de la page, puisque ce sont eux
                # qui decident lesquels partent.
                tr = STUDIO.track(q["track"])
                info = tr["info"]
                ev = info["_audio"]["events"]
                dur = float(info["duration"])
                t0 = float(q.get("t", 0.0))
                tt = np.array([e[0] for e in ev])
                ff = np.array([e[2] for e in ev])
                cible = FAMILLES.get(_dans(q.get("splitOn"), INSTRUMENTS,
                                           "grosse caisse"))
                ok = (np.ones(len(ev), bool) if cible is None
                      else np.array([e[1] in cible for e in ev]))
                splits = pick_split_times(tt, ff, ok, dur,
                                          int(float(q.get("splitCount", 3))))
                caisse = FAMILLES["grosse caisse"]
                # hors du fondu d'ouverture et de fermeture, qui assombrissent
                coups = [e[0] for e in ev if e[1] in caisse
                         and 0.6 < e[0] < dur - 0.8
                         and all(abs(e[0] - x) > 0.35 for x in splits)]
                if not coups:
                    return self._json({"t": t0, "coup": False})
                t = min(coups, key=lambda x: abs(x - t0))
                # juste apres l'attaque : l'anneau et la poussee sont partis
                return self._json({"t": t + 0.08, "coup": True})
            if u.path == "/calage":
                # Ce que deviennent les notes du fichier sur ce morceau : la
                # page le dit en clair, et en tire l'instant de la premiere
                # note et la duree d'un temps pour ses boutons.
                tr = STUDIO.track(q["track"])
                chemin = _melodie(q.get("midi"))
                if not chemin or not os.path.exists(chemin):
                    return self._fail("aucune mélodie chargée")
                tel_quel = _coche(q.get("telQuel"))
                reg, lu = STUDIO.calage(tr, chemin, float(q.get("bpm") or 0.0),
                                        tel_quel)
                if not lu or not lu.get("notes"):
                    return self._fail("ce fichier MIDI ne contient aucune note")
                g = STUDIO.grille(tr, float(q.get("bpm") or 0.0))
                return self._json({
                    "mode": lu.get("mode", "tel quel"),
                    "raison": lu.get("raison") or
                    "le fichier est lu tel quel, en secondes, sans le poser "
                    "sur la grille du morceau",
                    "bpm": g["bpm"], "temps": g["temps"],
                    "debut": float(lu.get("debut", 0.0)),
                    "notes": int(lu.get("notes", 0)),
                })
            if u.path == "/derive":
                # De combien le fichier MIDI derive par rapport au morceau.
                # Mesure a la demande et non a l'envoi : elle demande le
                # morceau analyse, et coute un tiers de seconde.
                tr = STUDIO.track(q["track"])
                chemin = _melodie(q.get("midi"))
                if not chemin or not os.path.exists(chemin):
                    return self._fail("aucune mélodie chargée")
                notes = midi.lire_notes(chemin)
                r, pa, pm, net = midi.deriver(
                    notes, [e[0] for e in tr["info"]["_audio"]["events"]],
                    tr["info"]["_audio"]["beat"])
                return self._json({
                    "pourcent": (r - 1.0) * 100.0,
                    "bpm_morceau": 60.0 / (pa * 4.0) if pa > 0 else 0.0,
                    "bpm_melodie": 60.0 / (pm * 4.0) if pm > 0 else 0.0,
                    "nettete": net,
                    "duree": tr["info"]["duration"],
                })
            if u.path == "/splits":
                tr = STUDIO.track(q["track"])
                ev = tr["info"]["_audio"]["events"]
                t = np.array([e[0] for e in ev])
                f = np.array([e[2] for e in ev])
                pads = FAMILLES.get(_dans(q.get("on"), INSTRUMENTS,
                                          "grosse caisse"))
                ok = (np.ones(len(ev), bool) if pads is None
                      else np.array([e[1] in pads for e in ev]))
                st = pick_split_times(t, f, ok, tr["info"]["duration"],
                                      int(float(q.get("count", 3))))
                return self._json({"times": [round(float(x), 2) for x in st]})

            # ---- la frise sous l'apercu
            if u.path == "/onde":
                tr = STUDIO.track(q["track"])
                g = STUDIO.grille(tr)
                return self._json(dict(
                    STUDIO.onde(tr), duree=float(tr["info"]["total"]),
                    # les temps du morceau : les blocs d'effets s'y aimantent
                    grille={"temps": float(g["temps"]),
                            "phase": float(g["phase"]),
                            "sure": bool(g.get("phase_sure"))}))
            if u.path == "/audio":
                # Le son du morceau, pour l'ecouter dans la page : le fichier
                # depose, par tranches (le lecteur saute ou on clique), ou sa
                # version decodee si le navigateur ne lit pas l'original.
                tr = STUDIO.track(q["track"])
                ext = os.path.splitext(tr["path"])[1].lower()
                if q.get("wav") == "1" or ext not in GENRES_AUDIO:
                    return self._fichier(wav_du_morceau(tr), "audio/wav")
                return self._fichier(tr["path"], GENRES_AUDIO[ext])
            if u.path == "/notes":
                # Les notes telles que le moteur les jouera : posees sur la
                # grille du morceau par le meme calcul que l'apercu. La page
                # n'a plus qu'a y ajouter le decalage de son curseur.
                tr = STUDIO.track(q["track"])
                chemin = _melodie(q.get("midi"))
                if not chemin or not os.path.exists(chemin):
                    return self._fail("aucune mélodie chargée")
                tel_quel = _coche(q.get("telQuel"))
                reg, _lu = STUDIO.calage(tr, chemin, float(q.get("bpm") or 0.0),
                                         tel_quel, _coche(q.get("cale")))
                notes = reg.get("midi") or []
                return self._json({
                    "notes": [[round(float(a), 3), round(float(b), 3), int(c),
                               round(float(d), 2)] for a, b, c, d in notes],
                    # le decalage trouve tout seul, quand la case est cochee
                    "auto": float(reg.get("midi_offset", 0.0)),
                    "grille": not tel_quel})
            if u.path == "/plan_fonds":
                chemins = backdrop_paths(q.get("noms"))
                if not chemins:
                    return self._json({"blocs": [], "fondus": []})
                blocs, fondus = plan_des_fonds(
                    chemins, min(4.0, max(0.25, float(q.get("vitesse") or 1.0))),
                    _dans(q.get("boucle"), BOUCLES_FOND, "boucle"),
                    float(q.get("fondu") or 0.0), float(q.get("photo") or 6.0),
                    float(q.get("total") or 0.0))
                return self._json({"blocs": blocs, "fondus": fondus})
            if u.path.startswith("/polices/"):
                # Les polices de la page, servies d'ici : le studio ne
                # telecharge rien et tourne sans reseau.
                nom = u.path[len("/polices/"):]
                f = os.path.join(POLICES, nom)
                if not re.fullmatch(r"[a-z0-9-]{1,60}\.woff2", nom) \
                        or not os.path.exists(f):
                    return self._fail("police inconnue", 404)
                with open(f, "rb") as fh:
                    corps = fh.read()
                return self._send(200, "font/woff2", corps,
                                  {"Cache-Control": "max-age=604800"})

            if u.path == "/job":
                with STUDIO.lock:
                    job = {k: v for k, v in STUDIO.jobs.get(q.get("id"), {}).items()
                           if not k.startswith("_")}
                job.pop("out", None)
                return self._json(job or {"error": "rendu inconnu"})
            if u.path == "/stop":
                job = STUDIO.arreter(q.get("id"))
                return self._json({"id": job["id"], "state": job["state"]})
            if u.path == "/exemples":
                return self._json(exemples_prets())
            if u.path == "/exemple":
                cle = str(q.get("cle", ""))
                if not re.fullmatch(r"[A-Za-z0-9_.=-]{1,60}", cle):
                    return self._fail("exemple inconnu", 404)
                ext = ".webp" if q.get("anim") == "1" else ".jpg"
                f = os.path.join(dossier_exemples(), cle.replace("=", "--") + ext)
                if not os.path.exists(f):
                    return self._fail("exemple pas encore fait", 404)
                with open(f, "rb") as fh:
                    corps = fh.read()
                # un exemple ne change pas tant que la version ne change pas
                return self._send(200, "image/webp" if ext == ".webp" else "image/jpeg",
                                  corps, {"Cache-Control": "max-age=86400"})
            if u.path == "/download":
                with STUDIO.lock:
                    job = STUDIO.jobs.get(q.get("id"))
                if not job or job["state"] != "fini":
                    return self._fail("rendu non terminé", 404)
                # « inline » sert l'apercu anime, qui se joue dans la page
                # au lieu d'etre telecharge
                entete = ({} if q.get("inline") == "1" else
                          {"Content-Disposition":
                           'attachment; filename="%s"' % job["name"]})
                genre = ("video/webm" if job["out"].endswith(".webm")
                         else "video/mp4")
                return self._fichier(job["out"], genre, entete)
            self._fail("page inconnue", 404)
        except Depasse:
            # L'apercu suivant est deja en route : celui-ci n'a plus lieu
            # d'etre. Une reponse vide plutot qu'une erreur (409) : le
            # navigateur notait chacune dans sa console, et l'ecoute en
            # direct en laisse passer a chaque arret.
            self._send(204, "text/plain; charset=utf-8", b"")
        except Exception as e:                            # noqa: BLE001
            traceback.print_exc()
            self._fail(e, 500)

    # ---- POST
    def do_POST(self):
        u = urlparse(self.path)
        try:
            if u.path == "/upload":
                os.makedirs(UPLOADS, exist_ok=True)
                name = safe_name(self.headers.get("X-Filename"))
                path = os.path.join(UPLOADS, name)
                if not self._recevoir(path, MAX_UPLOAD):
                    return self._fail("morceau trop gros (%d Mo au plus)"
                                      % (MAX_UPLOAD // MO), 413)
                try:
                    probe_duration(path)
                except Exception:                         # noqa: BLE001
                    os.remove(path)
                    return self._fail("ffmpeg ne sait pas lire ce fichier")
                tid, info = STUDIO.add_track(path, name)
                # le tempo exact, mesure sur la grille du morceau : c'est lui
                # que la page propose pour poser une melodie
                g = STUDIO.grille(STUDIO.track(tid))
                return self._json({
                    "track": tid, "name": name,
                    "duration": info["total"], "bpm": g["bpm"],
                    "bpm_detecte": info["bpm"],
                    "hits": info["hits"], "drops": info["drops"],
                    "frappes": STUDIO.frappes(info),
                    "duree": info["duration"]})

            if u.path == "/backdrop":
                os.makedirs(FONDS, exist_ok=True)
                name = safe_name(self.headers.get("X-Filename"))
                path = os.path.join(FONDS, name)
                if not self._recevoir(path, MAX_FOND):
                    return self._fail("fond trop gros (%d Mo au plus). Une "
                                      "vidéo plus courte, ou exportée moins "
                                      "lourde, fera le même effet."
                                      % (MAX_FOND // MO), 413)
                try:                        # ffmpeg doit savoir le lire
                    load_backdrop(path, 64, 36)
                except Exception as e:      # noqa: BLE001
                    os.remove(path)
                    # on repasse le vrai motif : il nomme le format en cause,
                    # ce qu'un « ffmpeg ne sait pas lire ce fichier » taisait
                    return self._fail(e)
                g = genre_fond(path)
                return self._json({"name": name, "video": g[0] == "video",
                                   "duree": round(g[1], 2)})

            if u.path == "/midi":
                os.makedirs(MELODIES, exist_ok=True)
                name = safe_name(self.headers.get("X-Filename"))
                path = os.path.join(MELODIES, name)
                if not self._recevoir(path, MAX_MIDI):
                    return self._fail("ce fichier est trop gros pour un MIDI "
                                      "(%d Mo au plus)" % (MAX_MIDI // MO), 413)
                try:
                    notes = midi.lire_notes(path)
                except Exception as e:                    # noqa: BLE001
                    os.remove(path)
                    return self._fail("fichier MIDI illisible : %s" % e)
                if not notes:
                    os.remove(path)
                    return self._fail("ce fichier MIDI ne contient aucune note")
                rep = dict(midi.resume(notes), name=name)
                rep["grave"] = midi.nom_note(rep["grave"])
                rep["aigu"] = midi.nom_note(rep["aigu"])
                # d'ou viennent les notes : ce sont elles, et elles seules, qui
                # allument le clavier — la page doit pouvoir le montrer
                inv = midi.inventaire(path)
                rep["pistes"] = midi.decrire(inv)
                rep["melange"] = midi.melange(inv)
                # On ne cherche plus de calage a l'envoi : il se trompe a tous
                # les coups sur une melodie (voir midi.caler). On rend l'instant
                # de la premiere note, qui se verifie a l'oreille.
                return self._json(rep)

            if u.path == "/reglages":
                corps = self._corps()
                if corps is None:
                    return self._fail("réglages illisibles", 413)
                d = json.loads(corps or b"{}")
                nom = " ".join(str(d.get("nom") or "").split())[:40]
                tout = lire_mes_reglages()
                if d.get("action") == "supprimer":
                    tout.pop(nom, None)
                else:
                    if not nom:
                        return self._fail("donnez un nom à ce réglage")
                    if nom not in tout and len(tout) >= MAX_REGLAGES:
                        return self._fail("déjà %d réglages enregistrés : "
                                          "effacez-en un" % MAX_REGLAGES)
                    tout[nom] = propre(d.get("valeurs"))
                ecrire_mes_reglages(tout)
                return self._json({"mes": tout, "nom": nom})

            if u.path == "/render":
                corps = self._corps()
                if corps is None:
                    return self._fail("réglages illisibles", 413)
                job = STUDIO.start_job(json.loads(corps or b"{}"))
                return self._json({k: v for k, v in job.items()
                                   if not k.startswith("_") and k != "out"})

            self._fail("page inconnue", 404)
        except Exception as e:                            # noqa: BLE001
            traceback.print_exc()
            # le corps restant est avale avant la reponse, sans quoi elle
            # n'arriverait pas jusqu'a la page
            self._vider()
            self._fail(e, 500)


# --------------------------------------------------------------------------
#  La page
# --------------------------------------------------------------------------

# Le sequenceur de machines et le depot de melodie, ecrits a part : il y a eu
# deux pages, et deux copies auraient fini par diverger. La page fournit
# `_redessine`, `_etat`, `_duree` et `_memoriser` (un pas de retour en
# arriere, commun avec la frise).
JS_SEQ_MIDI = r"""
/* ---------- sequenceur de machines ----------

   Le plan s'ecrit « 0:32=digitakt, 1:05=minifreak/3.5 » dans un champ cache,
   que params() envoie comme n'importe quel reglage ; « /3.5 » donne a ce
   changement sa propre duree de deformation. Les lignes ci-dessous ne sont
   qu'une facon commode de l'ecrire : elles n'ont pas d'identifiant a elles,
   pour que la page n'ait qu'un seul reglage a tenir. Chaque changement est
   {t, m, d} : l'instant, la machine, et la duree (null : celle du curseur). */
let SEQ = [];

function seqMachines() {
  return [...$('#machine').options].map(o => o.value);
}

// un instant au centieme — un changement pose sur un temps du morceau ne
// tombe pas sur une seconde ronde : « 1:31.81 », et « 0:32 » quand il l'est
function seqTemps(v) {
  v = Math.max(0, Math.round(v * 100) / 100);
  const m = Math.floor(v / 60), cs = Math.round((v - m * 60) * 100);
  const sec = Math.floor(cs / 100), reste = cs % 100;
  return m + ':' + String(sec).padStart(2, '0')
    + (reste ? '.' + String(reste).padStart(2, '0').replace(/0$/, '') : '');
}

function seqLire(txt) {
  const noms = seqMachines();
  return String(txt || '').split(',').map(b => {
    // la duree propre du changement, apres la barre : « digitakt/2.5 »
    let d = null;
    const k = b.lastIndexOf('/');
    if (k >= 0) {
      const v = parseFloat(b.slice(k + 1));
      if (Number.isFinite(v) && v >= 0) d = v;
      b = b.slice(0, k);
    }
    const m = b.trim().split('=');
    if (m.length < 2) return null;
    const t = m[0].trim().split(':');
    const s = t.length > 1 ? (+t[0] || 0) * 60 + (+t[1] || 0) : (+t[0] || 0);
    return noms.includes(m[1].trim()) ? {t: s, m: m[1].trim(), d} : null;
  }).filter(Boolean);
}

function seqEcrire() {
  SEQ.sort((a, b) => a.t - b.t);
  $('#machines').value = SEQ.length
    ? '0:00=' + $('#machine').value + ', '
      + SEQ.map(e => seqTemps(e.t) + '=' + e.m
                     + (e.d != null ? '/' + Math.round(e.d * 100) / 100 : '')).join(', ')
    : '';
  midiAvis();
}

/* La melodie ne se joue que sur un clavier. Si le plan n'en contient aucun,
   le fichier est bien charge mais rien ne s'allumera : autant le dire tout de
   suite plutot que de laisser chercher. */
function midiAvis() {
  const a = $('#midiAvis');
  if (!a) return;
  const plan = ($('#machines').value || '') + ' ' + $('#machine').value;
  // une batterie joue sur les pads de toutes les machines : pas besoin de
  // clavier pour elle
  const bat = $('#midiType') && $('#midiType').value === 'batterie';
  a.hidden = !$('#midi').value || bat || plan.indexOf('minifreak') >= 0;
}

/* Le nom d'une machine tel que la liste du debut l'affiche, sans sa
   description : « MiniFreak » plutot que « minifreak ». */
function seqNom(n) {
  const o = [...$('#machine').options].find(x => x.value === n);
  return o ? o.textContent.split(' — ')[0] : n;
}

function seqDessine() {
  const noms = seqMachines(), l = $('#seqListe');
  // la duree du curseur, en grise dans les cases laissees vides
  const defaut = (+$('#passage').value || 0).toFixed(1);
  l.innerHTML = '';
  SEQ.forEach((e, i) => {
    const d = document.createElement('div');
    d.className = 'row seqrow';
    d.innerHTML = '<span class="unite">à</span>'
      + '<input type="text" class="seqt" value="' + seqTemps(e.t) + '"'
      + ' aria-label="instant du changement">'
      + '<select class="seqm" aria-label="machine">'
      + noms.map(n => '<option value="' + n + '"'
                 + (n === e.m ? ' selected' : '') + '>' + seqNom(n) + '</option>').join('')
      + '</select>'
      + '<input type="number" class="seqd" min="0" max="60" step="0.1"'
      + ' placeholder="' + defaut + '" value="' + (e.d != null ? e.d : '') + '"'
      + ' title="durée de la déformation vers cette machine, en secondes ; vide,'
      + ' c\'est celle du curseur plus bas" aria-label="durée de la déformation">'
      + '<span class="unite">s</span>'
      + '<button class="ghost seqx" title="retirer ce changement">&times;</button>';
    d.querySelector('.seqt').onchange = ev => {
      const t = ev.target.value.trim().split(':');
      _memoriser();
      SEQ[i].t = t.length > 1 ? (+t[0] || 0) * 60 + (+t[1] || 0) : (+t[0] || 0);
      seqEcrire(); seqDessine(); _redessine();
    };
    d.querySelector('.seqm').onchange = ev => {
      _memoriser();
      SEQ[i].m = ev.target.value; seqEcrire(); _redessine();
    };
    d.querySelector('.seqd').onchange = ev => {
      const v = ev.target.value.trim();
      _memoriser();
      SEQ[i].d = v === '' || !Number.isFinite(+v) ? null
               : Math.round(Math.min(60, Math.max(0, +v)) * 100) / 100;
      seqEcrire(); seqDessine(); _redessine();
    };
    d.querySelector('.seqx').onclick = () => {
      _memoriser();
      SEQ.splice(i, 1); seqEcrire(); seqDessine(); _redessine();
    };
    l.appendChild(d);
  });
  if (!SEQ.length) {
    l.innerHTML = '<p class="hint" style="margin:2px 0 6px">Une seule machine '
      + 'du début à la fin. Ajoutez un changement pour qu\'elle se déforme '
      + 'en une autre.</p>';
  } else {
    const h = document.createElement('p');
    h.className = 'hint';
    h.textContent = 'La durée de chaque déformation s\'étire aussi à la souris, '
      + 'sur la frise : par le bord gauche de la déformation. Laissée vide, '
      + 'c\'est celle du curseur plus bas.';
    l.appendChild(h);
  }
}

function seqPose(txt) {
  SEQ = seqLire(txt).filter((e, i, a) => e.t > 0 || i > 0);
  // la premiere entree du plan est la machine du debut : elle a son propre
  // choix, elle ne prend pas une ligne de plus
  const p = seqLire(txt);
  if (p.length && p[0].t <= 0) { $('#machine').value = p[0].m; SEQ = p.slice(1); }
  seqEcrire(); seqDessine();
}

/* Le branchement, appele une fois les elements de la page en place. Une
   propriete posee sur un element absent coupait la fin du script sans le
   moindre message : d'ou la verification en tete. */
function seqBrancher() {
  const plus = $('#seqPlus'), mdrop = $('#midiDrop'), mfile = $('#midifile');
  if (!plus || !mdrop || !mfile) return;

  plus.onclick = () => {
  const dernier = SEQ.length ? SEQ[SEQ.length - 1].t : 0;
  const noms = seqMachines();
  const prec = SEQ.length ? SEQ[SEQ.length - 1].m : $('#machine').value;
  const suiv = noms[(noms.indexOf(prec) + 1) % noms.length];
  _memoriser();
  SEQ.push({t: Math.round(dernier + (_duree() ? Math.max(8, _duree() / 6) : 30)),
            m: suiv, d: null});
  seqEcrire(); seqDessine(); _redessine();
};

  $('#seqAuto').onclick = () => {
  const chaque = Math.max(4, +$('#seqChaque').value || 30);
  const noms = seqMachines(), fin = _duree() || chaque * 4;
  _memoriser();
  SEQ = [];
  let i = noms.indexOf($('#machine').value);
  for (let t = chaque; t < fin - 1; t += chaque) {
    i = (i + 1) % noms.length;
    SEQ.push({t: Math.round(t), m: noms[i], d: null});
  }
  seqEcrire(); seqDessine(); _redessine();
};

/* ---------- melodie : le fichier MIDI ---------- */
  mdrop.onclick = () => mfile.click();
  mdrop.ondragover = e => { e.preventDefault(); mdrop.classList.add('over'); };
  mdrop.ondragleave = () => mdrop.classList.remove('over');
  mdrop.ondrop = e => { e.preventDefault(); mdrop.classList.remove('over');
                        if (e.dataTransfer.files[0]) sendMidi(e.dataTransfer.files[0]); };
  mfile.onchange = () => mfile.files[0] && sendMidi(mfile.files[0]);
  $('#midiOte').onclick = () => { midiOte(); _redessine(); };
  seqPose('');
}

/* L'instant de la premiere note du fichier MIDI, dans le temps du morceau.
   null tant qu'aucun fichier n'est charge. */
let MIDI_DEBUT = null;

/* Un instant, ecrit au millieme : c'est ce qui permet de comparer la premiere
   note du fichier a ce qu'on entend. Arrondi a la seconde, « 5 s » ne disait
   pas si le fichier tombait a 5,00 ou a 5,49 ; au centieme, un decalage regle
   a la milliseconde ne s'y voyait pas bouger. */
function instant(s) {
  s = Math.max(0, +s || 0);
  const m = Math.floor(s / 60), r = s - m * 60;
  return m + ':' + (r < 10 ? '0' : '') + r.toFixed(3);
}

function midiOte() {
  $('#midi').value = '';
  MIDI_DEBUT = null;
  midiAvis();
  $('#midimeta').hidden = true;
  if ($('#mi-p')) $('#mi-p').textContent = '';
  if (typeof majCalage === 'function') majCalage();
  $('#midiReglages').hidden = true;
  $('#midiVide').hidden = false;
  $('#midiActions').hidden = true;
  $('#midiDrop').classList.remove('charge');
  $('#midiDrop').title = '';
  $('#midiDrop').innerHTML = '<b>Déposer un fichier MIDI</b>.mid, .midi'
    + ' \u2014 ou cliquer pour choisir';
  majPoints();
}

async function sendMidi(f) {
  try {
    const j = await deposer('/midi', f, 'de la mélodie');
    $('#midi').value = j.name;
    $('#mi-n').textContent = j.notes;
    $('#mi-e').textContent = j.grave + ' \u2192 ' + j.aigu;
    $('#mi-c').textContent = instant(j.debut);
    // d'ou viennent les notes, piste par piste : ce sont elles seules qui
    // allument le clavier
    const origine = $('#mi-p');
    if (origine) {
      origine.textContent = j.pistes ? 'dans le fichier : ' + j.pistes : '';
      if (j.melange) origine.textContent += ' — attention : la batterie'
        + ' (canal 10) et les autres notes s\'allument ensemble. Pour un rendu'
        + ' net, exportez la mélodie seule.';
    }
    MIDI_DEBUT = +j.debut || 0;
    if (typeof majOffset === 'function') majOffset();
    // la melodie se pose sur la grille du morceau : la page dit ce qu'elle a fait
    if (typeof majCalage === 'function') majCalage();
    $('#midimeta').hidden = false;
    $('#midiReglages').hidden = false;
    $('#midiVide').hidden = true;
    $('#midiActions').hidden = false;
    puceFichier($('#midiDrop'), 'melodie', j.name, 'remplacer');
    majPoints();
    midiAvis();
    _etat(j.notes + ' notes lues dans ' + j.name);
    _redessine();
  } catch (e) { _etat('mélodie refusée : ' + e.message, true); }
}

"""


PAGE = r"""<!doctype html>
<html lang="fr"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>Studio Omnipotard</title>
<style>
  /* Les polices sont livrees avec le studio (tools/polices) : rien ne se
     telecharge, la page tourne sans reseau. Inter pour le texte, JetBrains
     Mono pour les nombres, qui gardent ainsi leur chasse. */
  @font-face{font-family:"Inter";font-weight:400;font-display:swap;src:url(/polices/inter-400.woff2) format("woff2")}
  @font-face{font-family:"Inter";font-weight:500;font-display:swap;src:url(/polices/inter-500.woff2) format("woff2")}
  @font-face{font-family:"Inter";font-weight:600;font-display:swap;src:url(/polices/inter-600.woff2) format("woff2")}
  @font-face{font-family:"Inter";font-weight:700;font-display:swap;src:url(/polices/inter-700.woff2) format("woff2")}
  @font-face{font-family:"JetBrains Mono";font-weight:400;font-display:swap;src:url(/polices/jetbrains-mono-400.woff2) format("woff2")}
  @font-face{font-family:"JetBrains Mono";font-weight:500;font-display:swap;src:url(/polices/jetbrains-mono-500.woff2) format("woff2")}
  :root{
    --f:"Inter",system-ui,-apple-system,"Segoe UI",Roboto,"Helvetica Neue",Arial,sans-serif;
    --m:"JetBrains Mono",ui-monospace,"Cascadia Mono","Segoe UI Mono",Menlo,Consolas,monospace;
    /* les fonds, du plus profond au plus clair, et les filets */
    --bg:#0a0c0f; --bg2:#0e1115; --pan:#12161b; --pan2:#171c22; --pan3:#1d232b;
    --lig:#222932; --lig2:#2c343e; --lig3:#3a4350;
    /* le texte : principal, secondaire, discret */
    --tx:#e9ecf0; --tx2:#a4adb8; --tx3:#6d7783;
    /* l'accent bleu, et ce qui va avec */
    --acc:#38bdf8; --acc2:#7dd3fc; --acc-f:rgba(56,189,248,.14); --acc-tx:#04121c;
    --ok:#3ddc97; --err:#ff5c7a;
    /* la frise : paroxysmes, dedoublements, notes, fonds */
    --parox:#ff6b8a; --dedo:#fbbf24; --note:#86efac; --fond:#c4b5fd;
    --r:10px; --r2:7px; --r3:5px;
    --ombre:0 10px 30px rgba(0,0,0,.45),0 2px 6px rgba(0,0,0,.35);
    color-scheme:dark;
  }
  *{box-sizing:border-box}
  /* « hidden » doit toujours l'emporter : une regle qui donne un display
     l'annulait sans bruit, et il fallait le redire element par element */
  [hidden]{display:none!important}
  html,body{height:100%}
  body{margin:0;display:flex;flex-direction:column;overflow:hidden;
    background:var(--bg);color:var(--tx);font:13px/1.45 var(--f);
    -webkit-font-smoothing:antialiased;font-feature-settings:"cv11","ss01"}
  *{scrollbar-width:thin;scrollbar-color:var(--lig2) transparent}
  b{font-weight:600}
  code{font:12px var(--m);color:var(--tx2)}

  /* ---- les champs ---- */
  button,input,select{font:inherit;color:inherit}
  button{cursor:pointer;border:none;border-radius:var(--r2);padding:8px 14px;
    font-size:12px;font-weight:600;line-height:1.2;white-space:nowrap;
    background:var(--acc);color:var(--acc-tx);
    box-shadow:0 1px 0 rgba(255,255,255,.2) inset,0 4px 14px rgba(56,189,248,.16);
    transition:background .12s,border-color .12s,color .12s}
  button:hover{background:var(--acc2)}
  button:disabled{background:var(--pan3);color:var(--tx3);box-shadow:none;cursor:default}
  button.ghost{background:var(--pan3);color:var(--tx);border:1px solid var(--lig2);
    font-weight:500;box-shadow:none}
  button.ghost:hover{background:#222a33;border-color:var(--lig3)}
  button.ghost:disabled{background:var(--pan2);color:var(--tx3);border-color:var(--lig)}
  button:focus-visible,select:focus-visible,input:focus-visible,
  #frise:focus-visible{outline:2px solid var(--acc);outline-offset:1px}
  select,input[type=number],input[type=text]{width:100%;height:30px;
    padding:5px 9px;border-radius:var(--r3);background-color:var(--pan3);
    border:1px solid var(--lig2);color:var(--tx);font-size:12px;min-width:0}
  select{appearance:none;-webkit-appearance:none;padding-right:26px;cursor:pointer;
    background-image:url("data:image/svg+xml,%3Csvg xmlns='http://www.w3.org/2000/svg' width='10' height='6' viewBox='0 0 10 6'%3E%3Cpath d='M1 1l4 4 4-4' stroke='%23a4adb8' stroke-width='1.5' fill='none' stroke-linecap='round'/%3E%3C/svg%3E");
    background-repeat:no-repeat;background-position:right 9px center;
    text-overflow:ellipsis}
  select option,select optgroup{background:#1a1f26;color:var(--tx)}
  select:hover,input[type=number]:hover,input[type=text]:hover{border-color:var(--lig3)}
  select:focus,input[type=number]:focus,input[type=text]:focus{outline:none;
    border-color:var(--acc);box-shadow:0 0 0 3px var(--acc-f)}
  input::placeholder{color:var(--tx3)}
  input[type=color]{width:64px;height:30px;padding:2px;border-radius:var(--r3);
    background:var(--pan3);border:1px solid var(--lig2);cursor:pointer}
  /* un curseur : la part deja parcourue est peinte en bleu (--p) */
  input[type=range]{-webkit-appearance:none;appearance:none;width:100%;height:18px;
    margin:0;padding:0;background:transparent;cursor:pointer;min-width:0}
  input[type=range]::-webkit-slider-runnable-track{height:4px;border-radius:2px;
    background:linear-gradient(to right,var(--acc) 0,var(--acc) var(--p,50%),var(--lig2) var(--p,50%))}
  input[type=range]::-webkit-slider-thumb{-webkit-appearance:none;width:13px;height:13px;
    margin-top:-4.5px;border-radius:50%;background:#f4f6f8;border:none;
    box-shadow:0 0 0 3px rgba(0,0,0,.45),0 1px 3px rgba(0,0,0,.5)}
  input[type=range]:hover::-webkit-slider-thumb{box-shadow:0 0 0 4px var(--acc-f),0 1px 3px rgba(0,0,0,.5)}
  input[type=range]::-moz-range-track{height:4px;border-radius:2px;background:var(--lig2)}
  input[type=range]::-moz-range-progress{height:4px;border-radius:2px;background:var(--acc)}
  input[type=range]::-moz-range-thumb{width:13px;height:13px;border-radius:50%;
    background:#f4f6f8;border:none;box-shadow:0 0 0 3px rgba(0,0,0,.45)}
  /* une case : un interrupteur */
  input[type=checkbox]{-webkit-appearance:none;appearance:none;flex:none;margin:0;
    width:30px;height:17px;border-radius:9px;background:var(--lig2);position:relative;
    cursor:pointer;transition:background .15s}
  input[type=checkbox]::after{content:"";position:absolute;top:2.5px;left:3px;
    width:12px;height:12px;border-radius:50%;background:#cfd6de;transition:left .15s}
  input[type=checkbox]:checked{background:var(--acc)}
  input[type=checkbox]:checked::after{left:15px;background:#fff}
  .ic{display:inline-grid;place-items:center;font-style:normal;flex:none}
  .ic svg{width:100%;height:100%}

  /* ---- l'en-tete ---- */
  header{flex:none;height:52px;display:flex;align-items:center;gap:14px;
    padding:0 16px;border-bottom:1px solid var(--lig);
    background:linear-gradient(#0f1217,#0c0f13)}
  .logo{display:flex;align-items:center;gap:10px;font-weight:600;font-size:14px;
    white-space:nowrap;letter-spacing:.01em}
  .logo .ic{width:26px;height:26px;padding:4px;border-radius:7px;color:var(--acc);
    background:radial-gradient(circle at 50% 40%,rgba(56,189,248,.35),transparent 70%),#0a1218;
    border:1px solid rgba(56,189,248,.45);box-shadow:0 0 14px rgba(56,189,248,.25)}
  .logo span{color:var(--tx3);font-weight:500}
  #vues{display:flex;gap:2px;padding:3px;border-radius:8px;background:var(--pan2);
    border:1px solid var(--lig)}
  #vues button{background:none;border:none;box-shadow:none;color:var(--tx2);
    font-weight:500;padding:5px 12px;border-radius:6px}
  #vues button:hover{color:var(--tx);background:none}
  #vues button.on{background:var(--pan3);color:var(--tx);box-shadow:inset 0 0 0 1px var(--lig2)}
  #puce{display:flex;align-items:center;gap:8px;height:30px;padding:0 12px;
    border-radius:8px;background:var(--pan2);border:1px solid var(--lig);box-shadow:none;
    color:var(--tx2);font-weight:400;max-width:360px;min-width:0}
  #puce:hover{border-color:var(--lig3);background:var(--pan3)}
  #puce .ic{width:14px;height:14px;color:var(--acc)}
  #puce b{color:var(--tx);font-weight:500;overflow:hidden;text-overflow:ellipsis}
  #puce .pt{width:6px;height:6px;border-radius:50%;background:var(--ok);flex:none;
    box-shadow:0 0 8px var(--ok)}
  #puce.vide .pt{background:var(--tx3);box-shadow:none}
  #puce.vide b{color:var(--tx2);font-weight:400}
  #status{flex:1;min-width:0;font-size:12px;color:var(--tx2);white-space:nowrap;
    overflow:hidden;text-overflow:ellipsis}
  #status.err{color:var(--err)}
  #ver{font:400 11px var(--m);color:var(--tx3);white-space:nowrap}
  #exporter{display:flex;align-items:center;gap:7px;height:32px;padding:0 14px}
  #exporter .ic{width:15px;height:15px}

  /* ---- trois colonnes : les sections, le reglage, la scene ----
     A gauche les sections, une seule ouverte a la fois ; au milieu ses
     reglages, qui defilent ; a droite l'apercu, fixe, la frise du morceau
     et ce qui entre et sort. La poignee entre les reglages et la scene
     elargit le panneau ; un double-clic le remet a sa largeur. */
  main{flex:1;min-height:0;display:grid;
    grid-template-columns:82px var(--panneau,clamp(360px,28vw,460px)) 7px minmax(0,1fr)}
  #rail{min-height:0;overflow-y:auto;display:flex;flex-direction:column;gap:2px;
    padding:10px 8px;background:var(--bg2);border-right:1px solid var(--lig)}
  #rail button{position:relative;display:flex;flex-direction:column;align-items:center;
    gap:5px;width:100%;padding:9px 2px 8px;background:transparent;border:none;
    box-shadow:none;color:var(--tx3);font-size:10px;font-weight:500;border-radius:9px;
    white-space:normal;text-align:center}
  #rail button .ic{width:19px;height:19px}
  #rail button:hover{background:var(--pan2);color:var(--tx)}
  #rail button.actif{background:var(--pan3);color:var(--tx);box-shadow:inset 2px 0 0 var(--acc)}
  #rail button.actif .ic{color:var(--acc)}
  /* un point : la section s'ecarte des valeurs d'usine */
  #rail button.modif::after{content:"";position:absolute;top:7px;right:14px;width:6px;
    height:6px;border-radius:50%;background:var(--acc);box-shadow:0 0 6px rgba(56,189,248,.6)}
  #rail hr{flex:none;border:none;height:1px;background:var(--lig);margin:6px}
  /* « 0 » : une action, pas une section — un rond plutot qu'une icone */
  #rail button .ic.zero{width:21px;height:21px;border:1.6px solid currentColor;
    border-radius:50%;font:600 11.5px/1 var(--m)}
  #rail button.zero:hover .ic.zero{color:var(--acc)}
  #panneau{min-height:0;overflow-y:auto;background:var(--pan)}
  #poignee{cursor:col-resize;position:relative;touch-action:none;background:var(--pan);
    border-right:1px solid var(--lig)}
  #poignee::after{content:"";position:absolute;top:50%;left:2px;width:2px;height:36px;
    margin-top:-18px;border-radius:1px;background:var(--lig2);transition:background .15s}
  #poignee:hover::after,#poignee.tire::after{background:var(--acc)}

  /* ---- une section du panneau ---- */
  .section{display:none;padding:18px 22px 64px}
  .section.actif{display:block}
  .section>.tete{display:flex;align-items:center;gap:10px}
  .section>.tete .ic{width:19px;height:19px;color:var(--acc)}
  .section>.tete h2{margin:0;font-size:16px;font-weight:600;line-height:1.3}
  .q{flex:none;margin-left:auto;width:22px;height:22px;padding:0;border-radius:50%;
    background:transparent;border:1px solid var(--lig2);box-shadow:none;
    color:var(--tx3);font-size:11px;font-weight:600}
  .q:hover,.q.ouvert{background:transparent;border-color:var(--acc);color:var(--acc)}
  .desc{margin:4px 0 14px 29px;color:var(--tx3);font-size:12px;line-height:1.5}
  .explications{margin:0 0 14px;padding:12px 14px;border-radius:var(--r2);
    background:var(--bg2);border:1px solid var(--lig);color:var(--tx2);font-size:12px;
    line-height:1.6}
  .explications p{margin:0 0 8px}
  .explications p:last-child{margin:0}
  .explications b{color:var(--tx)}
  .groupe{margin:22px 0 2px;font-size:10.5px;font-weight:600;letter-spacing:.08em;
    text-transform:uppercase;color:var(--tx3)}
  .section .row{display:grid;grid-template-columns:minmax(0,1fr) auto;gap:8px;margin-top:8px}
  #presetDel{margin-top:8px;width:100%}
  .info{margin:6px 0 4px;color:var(--tx3);font-size:11.5px;line-height:1.5}
  .info b{color:var(--tx);font-weight:500}
  .hint{color:var(--tx3);font-size:11.5px;line-height:1.5}
  .err{color:var(--err)}
  .boutons{display:flex;flex-wrap:wrap;gap:6px;margin:8px 0 2px}
  .boutons button{padding:6px 10px;font-size:11.5px}
  .section>button.ghost,#midiReglages>button.ghost{margin-top:10px}

  /* ---- un reglage : nom | curseur | valeur | explication ---- */
  .ctl{position:relative;display:grid;align-items:center;column-gap:12px;
    grid-template-columns:minmax(0,36%) minmax(0,1fr) 64px 18px;
    min-height:38px;padding:6px 0;border-bottom:1px solid rgba(255,255,255,.045)}
  .ctl>label{grid-column:1;grid-row:1;margin:0;color:var(--tx2);font-size:12px;
    line-height:1.3;min-width:0}
  .ctl:hover>label{color:var(--tx)}
  .ctl>input[type=range]{grid-column:2;grid-row:1}
  .ctl>output{grid-column:3;grid-row:1;text-align:right;white-space:nowrap;
    overflow:hidden;text-overflow:ellipsis;font:500 11.5px var(--m);color:var(--tx);
    font-variant-numeric:tabular-nums}
  .ctl>.ctl-i{grid-column:4;grid-row:1}
  /* l'instrument qui declenche l'effet, sous son curseur */
  .ctl>select.inst{grid-column:2/5;grid-row:2;justify-self:start;width:auto;
    max-width:100%;height:24px;margin-top:5px;padding:2px 24px 2px 8px;font-size:11px;
    color:var(--tx2);background-color:transparent;border-color:var(--lig);
    background-position:right 8px center}
  .ctl.liste>select,.ctl.texte>input,.ctl.nombre>input{grid-column:2/4;grid-row:1}
  .ctl.couleur>input{grid-column:2;grid-row:1}
  .ctl.coche>label{grid-column:1/4;display:flex;align-items:center;gap:10px;cursor:pointer}
  .ctl.large{grid-template-columns:minmax(0,30%) minmax(0,1fr) 96px 18px}
  .ctl .aide,.ctl figure.ex{display:none}
  .ctl-i{width:18px;height:18px;padding:0;border-radius:50%;display:grid;place-items:center;
    background:transparent;border:1px solid var(--lig2);box-shadow:none;color:var(--tx3);
    font:600 10px/1 var(--f);cursor:help}
  .ctl-i:hover,.ctl-i.ouvert{background:transparent;border-color:var(--acc);color:var(--acc)}

  /* ---- le sequenceur de machines ---- */
  .seq{margin:12px 0 4px;padding:10px 12px 12px;border-radius:var(--r2);
    background:var(--bg2);border:1px solid var(--lig)}
  .seq .titre{font-size:12px;color:var(--tx2)}
  .seq .row{display:grid;gap:6px;align-items:center;margin-top:8px}
  .seq .seqplus{grid-template-columns:1fr}
  .seq .seqauto{grid-template-columns:minmax(0,1fr) 64px auto}
  .seq .seqrow{grid-template-columns:auto 72px minmax(0,1fr) 54px auto 30px;margin-top:6px}
  .seq .seqrow .seqd{padding:4px 6px;font:400 11.5px var(--m)}
  .seq .seqrow .seqd::placeholder{color:var(--tx3)}
  .seq .seqrow input,.seq .seqrow select{height:28px}
  .seq .seqx{width:30px;height:28px;padding:0}
  .seq .unite{color:var(--tx3);font-size:11.5px}
  .seq .hint{margin:6px 0 2px}

  /* ---- les depots de fichiers ---- */
  .drop{display:block;border:1px dashed var(--lig3);border-radius:var(--r2);
    background:rgba(255,255,255,.015);color:var(--tx3);font-size:12px;padding:14px;
    text-align:center;cursor:pointer;transition:border-color .15s,color .15s}
  .drop:hover,.drop.over{border-color:var(--acc);color:var(--tx2)}
  .drop b{display:block;color:var(--tx);font-weight:500;margin-bottom:2px}
  /* un fichier charge : sa puce, au lieu de la zone de depot */
  .drop.charge{display:grid;grid-template-columns:auto minmax(0,1fr) auto;gap:10px;
    align-items:center;text-align:left;padding:9px 10px;border-style:solid;
    border-color:var(--lig);background:var(--pan2)}
  .drop.charge .ic{width:16px;height:16px;color:var(--acc)}
  .drop.charge b{margin:0;white-space:nowrap;overflow:hidden;text-overflow:ellipsis}
  .drop.charge .rempl{font-size:11px;color:var(--tx3)}
  .drop.charge:hover .rempl{color:var(--acc)}
  .vide{border:1px dashed var(--lig3);border-radius:var(--r2);padding:16px;
    text-align:center;color:var(--tx3);font-size:12px}
  .vide b{display:block;color:var(--tx2);font-weight:500;margin-bottom:2px}
  .vide button{margin-top:10px}
  /* la suite de fonds : un fichier par ligne, dans l'ordre de passage */
  #bdliste{list-style:none;counter-reset:fond;margin:10px 0 4px;padding:0}
  #bdliste li{counter-increment:fond;display:grid;align-items:center;gap:4px;
    grid-template-columns:minmax(0,1fr) auto auto auto;padding:6px 0;
    border-bottom:1px solid rgba(255,255,255,.045);font-size:12px}
  #bdliste li span{overflow:hidden;text-overflow:ellipsis;white-space:nowrap}
  #bdliste li span::before{content:counter(fond) ". ";color:var(--tx3);font-family:var(--m)}
  #bdliste li small{margin-left:6px;color:var(--tx3);font:11px var(--m)}
  #bdliste button{width:26px;height:24px;padding:0;font-size:12px}

  /* ---- la scene : l'apercu, la frise, l'export et les sources ----
     Sur un ecran large et bas — un portable, un 1080p dans un navigateur —
     l'export et les sources passent a droite de l'apercu : il y gagne la
     moitie de sa taille. Sinon ils vont dessous. */
  #scene{min-width:0;min-height:0;overflow-y:auto;container:scene/size;
    background:radial-gradient(1200px 500px at 50% -10%,#121a24,transparent)}
  #sceneGrille{display:grid;min-height:100%;padding:16px 20px 18px;gap:12px;
    grid-template-columns:minmax(0,1fr);
    grid-template-rows:minmax(auto,1fr) auto auto;
    grid-template-areas:"apercu" "chrono" "bas"}
  #carteApercu{grid-area:apercu;position:relative;min-height:max(220px,45cqh);
    display:flex;align-items:center;justify-content:center;container-type:size}
  .ecran{position:relative;aspect-ratio:16/9;overflow:hidden;background:#000;
    width:min(100%,calc((100vh - 470px) * 16 / 9));
    width:min(100cqw,calc(100cqh * 16 / 9));
    border-radius:var(--r);border:1px solid var(--lig);
    box-shadow:0 0 0 1px rgba(0,0,0,.6),0 18px 50px rgba(0,0,0,.55)}
  #ecran.vide{cursor:pointer}
  .ecran.over{border-color:var(--acc);box-shadow:0 0 0 3px var(--acc-f)}
  .ecran #shot,.ecran #clip{display:block;width:100%;height:100%;object-fit:contain;
    background:#000;border:0}
  #shot.calcul{opacity:.35;transition:opacity .2s}
  /* sans image, une balise <img> dessine un cadre : on la tait */
  #ecran.vide #shot{visibility:hidden}
  #ecranVide{position:absolute;inset:0;display:flex;flex-direction:column;
    align-items:center;justify-content:center;gap:4px;padding:16px;text-align:center;
    color:var(--tx3);font-size:12px;pointer-events:none}
  #ecranVide .ic{width:34px;height:34px;color:var(--acc);margin-bottom:8px}
  #ecranVide b{color:var(--tx);font-size:14px;font-weight:500}
  #shoterr{display:none;position:absolute;left:12px;right:12px;bottom:12px;z-index:2;
    padding:8px 10px;border-radius:var(--r2);font-size:12px;line-height:1.45;
    background:#2a1416;border:1px solid #6b2b30;color:#ffb4b4}
  #shoterr.on{display:block}

  /* la barre de lecture et la frise, d'un seul tenant */
  #chrono{grid-area:chrono;min-width:0;background:var(--pan);border:1px solid var(--lig);
    border-radius:var(--r);overflow:hidden}
  #lecture{display:flex;align-items:center;flex-wrap:wrap;gap:8px;padding:8px 10px;
    border-bottom:1px solid var(--lig)}
  /* ecouter : le bouton rond, le seul en bleu de la barre */
  #ecoute{width:34px;height:34px;padding:0;border-radius:50%;display:grid;place-items:center}
  #ecoute .ic{width:13px;height:13px}
  #ecoute.on{background:var(--ok);box-shadow:0 0 0 3px rgba(61,220,151,.18)}
  #lire{display:flex;align-items:center;gap:8px;height:32px;padding:0 12px}
  #lire .ic{width:14px;height:14px}
  #clipDur{width:70px;height:32px}
  #clipprog{display:flex;align-items:center;gap:8px}
  #clipprog .bar{width:110px;margin:0}
  #ctext{font:400 11px var(--m);color:var(--tx3);white-space:nowrap}
  #clipStop{height:28px;padding:0 10px}
  .temps{padding:0 6px;font:500 12.5px var(--m);color:var(--tx);white-space:nowrap;
    font-variant-numeric:tabular-nums}
  .temps em{font-style:normal;color:var(--tx3)}
  .outils{display:flex;align-items:center;gap:4px}
  .ib{width:30px;height:30px;padding:0;display:grid;place-items:center}
  .ib .ic{width:15px;height:15px}
  .ib.texte{width:auto;padding:0 10px;display:flex;gap:6px;font-size:11.5px}
  .flex{flex:1}
  #zoomTxt{min-width:38px;text-align:center;font:400 11px var(--m);color:var(--tx3)}
  #frise{position:relative;height:96px;outline:none;cursor:crosshair;
    background:var(--bg2);user-select:none;-webkit-user-select:none;touch-action:pan-y}
  #frise canvas{position:absolute;inset:0;width:100%;height:100%;display:block}
  #friseInfo{position:absolute;top:3px;z-index:2;pointer-events:none;padding:2px 7px;
    border-radius:5px;background:rgba(10,12,15,.94);border:1px solid var(--lig2);
    font:500 11px/1.5 var(--m);color:var(--tx);white-space:nowrap}
  #friseVide{position:absolute;inset:0;margin:0;display:flex;align-items:center;
    justify-content:center;padding:0 20px;text-align:center;color:var(--tx3);
    font-size:12px;pointer-events:none}
  #effetsVider{position:absolute;left:7px;z-index:2;height:18px;padding:0 5px;
    border-radius:4px;background:transparent;border:1px solid transparent;box-shadow:none;
    color:var(--tx3);font-size:10px;font-weight:500}
  #effetsVider:hover{background:transparent;color:var(--err);border-color:var(--lig2)}

  /* l'apercu en direct, ses masques et ce qu'ils disent */
  #ecranDirect{position:absolute;inset:0;width:100%;height:100%;display:block;background:#000}
  #masques{position:absolute;top:8px;right:8px;z-index:3;display:flex;gap:4px;
    opacity:.55;transition:opacity .15s}
  .ecran:hover #masques,#masques:focus-within,#masques.actif{opacity:1}
  #ecran.vide #masques{display:none}
  #masques button{display:flex;align-items:center;gap:5px;height:24px;padding:0 8px;
    border-radius:6px;background:rgba(10,12,15,.8);border:1px solid var(--lig2);
    box-shadow:none;color:var(--tx2);font-size:11px;font-weight:500}
  #masques button .ic{width:13px;height:13px}
  #masques button:hover{color:var(--tx);border-color:var(--lig3)}
  #masques button.on{background:var(--acc);border-color:var(--acc);color:var(--acc-tx)}
  #masques button:disabled{opacity:.45;color:var(--tx3);cursor:default}
  #direct,#masqueAvis{position:absolute;left:8px;z-index:3;display:flex;align-items:center;
    gap:6px;height:24px;padding:0 9px;border-radius:6px;background:rgba(10,12,15,.8);
    border:1px solid var(--lig2);color:var(--tx2);pointer-events:none;white-space:nowrap}
  #direct{top:8px;font:500 11px var(--m)}
  #direct::before{content:"";width:7px;height:7px;border-radius:50%;background:var(--ok);
    box-shadow:0 0 8px var(--ok)}
  #masqueAvis{bottom:8px;font-size:11px;max-width:calc(100% - 16px);overflow:hidden;
    text-overflow:ellipsis}

  /* la poignee d'un effet : on l'attrape pour le poser sur la frise */
  .ctl-glisse{position:absolute;left:-16px;top:9px;width:14px;height:20px;padding:0;
    display:grid;place-items:center;border-radius:4px;background:transparent;border:none;
    box-shadow:none;color:var(--tx3);opacity:.4;cursor:grab;touch-action:none}
  .ctl-glisse .ic{width:12px;height:12px}
  .ctl:hover .ctl-glisse,.ctl-glisse:focus-visible{opacity:1}
  .ctl-glisse:hover{background:var(--pan3);color:var(--acc)}
  /* l'effet est deja pose quelque part sur la frise */
  .ctl-glisse.pose{color:var(--acc);opacity:.85}
  body.glisse,body.glisse *{cursor:grabbing!important;user-select:none}
  #glisse{position:fixed;z-index:120;pointer-events:none;display:flex;align-items:center;
    gap:8px;padding:5px 10px;border-radius:7px;background:#1a2028;border:1px solid var(--lig3);
    box-shadow:var(--ombre);font-size:12px;color:var(--tx);white-space:nowrap;
    transform:translate(14px,-50%)}
  #glisse i{width:9px;height:9px;border-radius:3px;flex:none}
  #glisse b{font-weight:500}
  #glisse span{font:400 11px var(--m);color:var(--tx3)}
  #glisse.sur{border-color:var(--acc)}
  #glisse.sur span{color:var(--acc)}

  /* le bloc choisi sur la frise : son intensite, son instrument, ses bornes */
  #blocInsp{position:fixed;z-index:95;width:316px;padding:12px 12px 10px;
    border-radius:var(--r);background:#1a2028;border:1px solid var(--lig2);
    box-shadow:var(--ombre);font-size:12px;color:var(--tx2)}
  #blocInsp::after{content:"";position:absolute;left:var(--fl,50%);bottom:-6px;width:10px;
    height:10px;margin-left:-5px;background:#1a2028;border:1px solid var(--lig2);
    border-top:none;border-left:none;transform:rotate(45deg)}
  #blocInsp.dessous::after{bottom:auto;top:-6px;transform:rotate(-135deg)}
  #blocInsp .tete{display:flex;align-items:center;gap:8px}
  #blocInsp .tete i{width:9px;height:9px;border-radius:3px;flex:none}
  #blocInsp .tete b{min-width:0;overflow:hidden;text-overflow:ellipsis;white-space:nowrap;
    color:var(--tx);font-weight:600}
  #blocInsp .ferme{flex:none;margin-left:auto;width:22px;height:22px;padding:0;
    border-radius:50%;background:transparent;border:1px solid var(--lig2);box-shadow:none;
    color:var(--tx3);font-size:12px;line-height:1}
  #blocInsp .ferme:hover{border-color:var(--acc);color:var(--acc)}
  #blocInsp .quand{margin:3px 0 4px 17px;font:400 11px var(--m);color:var(--tx3)}
  #blocInsp .ctl{grid-template-columns:minmax(0,32%) minmax(0,1fr) 58px;min-height:34px}
  #blocInsp .ctl.liste>select{grid-column:2/4}
  #blocInsp .bornes{display:grid;grid-template-columns:auto minmax(0,1fr) auto minmax(0,1fr) auto;
    gap:6px;align-items:center;margin-top:8px;font-size:11.5px;color:var(--tx3)}
  #blocInsp .bornes input{height:26px;font:400 11.5px var(--m)}
  #blocInsp .boutons{margin-top:10px}
  #blocInsp .boutons button{flex:1}
  #blocInsp button.danger:hover{border-color:var(--err);color:var(--err)}
  #blocInsp .note{margin:8px 0 0;font-size:11px;line-height:1.45;color:var(--tx3)}

  /* l'export et les sources */
  #bas{grid-area:bas;display:grid;gap:12px;align-items:start;
    grid-template-columns:minmax(0,1.1fr) minmax(0,2fr)}
  .card{min-width:0;background:var(--pan);border:1px solid var(--lig);
    border-radius:var(--r);padding:14px}
  .card .tete{display:flex;align-items:center;gap:9px;margin-bottom:12px}
  .card .tete .ic{width:16px;height:16px;color:var(--acc)}
  .card .tete h2{margin:0;font-size:12.5px;font-weight:600}
  #go{margin-left:auto;height:32px;padding:0 14px}
  #carteRendu{container-type:inline-size}
  #carteRendu .champs{display:grid;grid-template-columns:repeat(2,minmax(0,1fr));gap:10px}
  #carteRendu .champs label{display:block;margin:0 0 4px;font-size:11px;color:var(--tx3)}
  #carteRendu label.coche{grid-column:1/-1;display:flex;align-items:center;gap:10px;
    margin:2px 0 0;font-size:12px;line-height:1.3;color:var(--tx2);cursor:pointer}
  @container (min-width:290px){
    #carteRendu .champs{grid-template-columns:repeat(3,minmax(0,1fr))}
  }
  #carteRendu .aide{margin:10px 0 0;font-size:11px;color:var(--tx3)}
  #carteRendu .aide b.freq{font:400 11.5px/1.45 var(--f);color:var(--tx3)}
  #prog,#done{margin-bottom:12px}
  #stop{width:100%;margin-top:6px}
  .bar{height:4px;margin:8px 0;border-radius:2px;background:var(--lig2);overflow:hidden}
  .bar i{display:block;height:100%;width:0;background:var(--acc);transition:width .3s}
  #ptext{font-family:var(--m);font-size:11px}
  a.dl{display:flex;align-items:center;justify-content:center;height:34px;
    border-radius:var(--r2);background:var(--acc);color:var(--acc-tx);font-weight:600;
    text-decoration:none}
  a.dl:hover{background:var(--acc2)}
  #donepath{margin:6px 0 0;font:400 11px var(--m);word-break:break-all}
  #carteSources{display:grid;grid-template-columns:repeat(2,minmax(0,1fr));gap:14px}
  .source{min-width:0;container-type:inline-size}
  .source .tete{margin-bottom:10px}
  .meta{display:grid;grid-template-columns:repeat(2,minmax(0,1fr));gap:6px 12px;
    margin-top:10px}
  @container (min-width:300px){
    #trackmeta{grid-template-columns:repeat(4,minmax(0,1fr))}
    #midimeta{grid-template-columns:repeat(3,minmax(0,1fr))}
  }
  .meta span{font-size:11px;color:var(--tx3)}
  .meta b{display:block;font:500 12px var(--m);color:var(--tx);white-space:nowrap;
    overflow:hidden;text-overflow:ellipsis}
  #midiAvis{margin:10px 0 0}
  .actions{display:flex;gap:6px;margin-top:10px}
  .actions button{flex:1;padding:7px 8px}

  /* une scene etroite : les deux cartes cote a cote, les sources l'une
     sous l'autre, les boutons de saut sans leur texte */
  @container scene (max-width:999px){
    #bas{grid-template-columns:repeat(2,minmax(0,1fr))}
    #carteSources{grid-template-columns:minmax(0,1fr)}
    .ib.texte{width:30px;padding:0;display:grid}
    .ib.texte span{display:none}
  }
  @container scene (min-aspect-ratio: 13 / 10){
    /* une hauteur fixe : la premiere rangee prend ce que laisse la frise */
    #sceneGrille{height:100%;min-height:0;
      grid-template-columns:minmax(0,1fr) clamp(290px,25%,360px);
      grid-template-rows:minmax(0,1fr) auto;
      grid-template-areas:"apercu bas" "chrono chrono"}
    #carteApercu{min-height:220px}
    #bas{display:flex;flex-direction:column;align-items:stretch;align-self:stretch;
      min-height:0;overflow-y:auto;margin-right:-6px;padding-right:6px}
    #carteSources{grid-template-columns:minmax(0,1fr)}
  }

  /* ---- la bulle d'explication : la phrase, la frequence, l'exemple ---- */
  #bulle{position:fixed;z-index:100;width:320px;padding:12px;border-radius:var(--r);
    background:#1a2028;border:1px solid var(--lig2);box-shadow:var(--ombre);
    color:var(--tx2);font-size:12px;line-height:1.5;pointer-events:none}
  #bulle.fige{pointer-events:auto}
  #bulle .titre{margin-bottom:4px;color:var(--tx);font-weight:600}
  #bulle b.freq{display:block;margin-top:6px;color:var(--acc);font:500 11px/1.45 var(--m)}
  #bulle b{color:var(--tx)}
  #bulle p{margin:0 0 6px}
  #bulle p:last-child{margin:0}
  #bulle img{display:block;width:100%;aspect-ratio:16/9;object-fit:cover;margin-top:10px;
    border-radius:6px;border:1px solid var(--lig);background:#000}

  /* ---- l'onglet des styles : il prend la place du studio ---- */
  #styles{flex:1;min-height:0;overflow-y:auto;display:flex;justify-content:center;
    padding:24px}
  #styles .fen{align-self:flex-start;width:100%;max-width:1180px;padding:22px 24px;
    border-radius:12px;background:var(--pan);border:1px solid var(--lig)}
  #styles .tete{display:flex;justify-content:space-between;align-items:flex-start;gap:16px}
  #styles .tete>div{flex:1;min-width:0}
  #styles h2{margin:0;font-size:18px;font-weight:600}
  #styles .grille{display:grid;gap:14px;margin-top:18px;
    grid-template-columns:repeat(auto-fill,minmax(280px,1fr))}
  #styles .st{display:flex;flex-direction:column;overflow:hidden;border-radius:var(--r);
    background:var(--bg2);border:1px solid var(--lig);transition:border-color .15s}
  #styles .st:hover{border-color:var(--lig3)}
  #styles .st img,#styles .st .vide{display:block;width:100%;aspect-ratio:16/9;
    object-fit:cover;background:#000;border:none;border-radius:0}
  #styles .st .vide{display:flex;align-items:center;justify-content:center;padding:0}
  #styles .st .txt{flex:1;padding:12px 14px 10px}
  #styles .st b{display:block;margin-bottom:4px;color:var(--tx)}
  #styles .st b::first-letter{text-transform:uppercase}
  #styles .st p{margin:0;color:var(--tx3);font-size:12px;line-height:1.5}
  #styles .st .act{display:flex;gap:8px;padding:0 14px 14px}
  #styles .st .act button{flex:1}

  /* ---- les ecrans plus petits ---- */
  @media (max-width:1180px){
    main{grid-template-columns:66px var(--panneau,clamp(330px,31vw,380px)) 7px minmax(0,1fr)}
    #rail{padding:8px 5px}
    #rail button{font-size:9.5px;padding:8px 0 7px}
    #rail button.modif::after{right:8px}
    .section{padding:16px 16px 56px}
    #ver{display:none}
  }
  /* un ecran etroit : une seule colonne, l'apercu d'abord */
  @media (max-width:860px){
    body{display:block;overflow:auto;height:auto}
    header{position:sticky;top:0;z-index:5;flex-wrap:nowrap;overflow-x:auto}
    #status{display:none}
    main{display:flex;flex-direction:column}
    #scene{order:1;container-type:inline-size;overflow:visible}
    #sceneGrille{min-height:0;padding:12px}
    #carteApercu{container-type:normal;min-height:0}
    .ecran{width:100%}
    #rail{order:2;flex-direction:row;overflow-x:auto;padding:6px;border-right:none;
      border-bottom:1px solid var(--lig)}
    #rail button{flex:0 0 68px}
    #rail hr{width:1px;height:auto;margin:6px 2px}
    #panneau{order:3;overflow:visible}
    #poignee{display:none}
    #bas{grid-template-columns:minmax(0,1fr)}
  }
</style></head><body>

<header>
  <div class="logo"><i class="ic" data-ic="logo"></i>Omnipotard <span>Studio</span></div>
  <nav id="vues">
    <button class="on" data-vue="studio">Studio</button>
    <button data-vue="styles">Styles</button>
  </nav>
  <button type="button" id="puce" class="vide" title="choisir un morceau">
    <span class="pt"></span><i class="ic" data-ic="morceau"></i><b id="puceNom">aucun morceau</b><span id="puceDet"></span></button>
  <span id="status">déposez un morceau pour commencer</span>
  <span id="ver"></span>
  <button type="button" id="exporter" disabled title="lancer le rendu avec les réglages de la carte Export"><i class="ic" data-ic="rendu"></i><span>Exporter</span></button>
</header>

<main>
<nav id="rail" aria-label="sections des réglages">
  <button type="button" id="toutZero" class="zero" title="tous les effets à zéro — réactions, avaries, écho, texture, éclair, glitchs, dédoublement — avant de personnaliser. La couleur, la machine, le fond et la frise ne bougent pas (Ctrl + Z pour revenir)"><i class="ic zero" aria-hidden="true">0</i><span>Effets à 0</span></button>
  <hr>
  <button type="button" data-section="prereglage"><i class="ic" data-ic="prereglage"></i><span>Préréglage</span></button>
  <hr>
  <button type="button" data-section="machine"><i class="ic" data-ic="machine"></i><span>Machine</span></button>
  <button type="button" data-section="lumiere"><i class="ic" data-ic="lumiere"></i><span>Lumière</span></button>
  <button type="button" data-section="couleur"><i class="ic" data-ic="couleur"></i><span>Couleur</span></button>
  <button type="button" data-section="dalle"><i class="ic" data-ic="dalle"></i><span>Dalle</span></button>
  <button type="button" data-section="fonds"><i class="ic" data-ic="fonds"></i><span>Fonds</span></button>
  <button type="button" data-section="trait"><i class="ic" data-ic="trait"></i><span>Trait</span></button>
  <hr>
  <button type="button" data-section="reactions"><i class="ic" data-ic="reactions"></i><span>Réactions</span></button>
  <button type="button" data-section="avaries"><i class="ic" data-ic="avaries"></i><span>Avaries</span></button>
  <button type="button" data-section="echo"><i class="ic" data-ic="echo"></i><span>Écho</span></button>
  <button type="button" data-section="texture"><i class="ic" data-ic="texture"></i><span>Texture</span></button>
  <hr>
  <button type="button" data-section="melodie"><i class="ic" data-ic="melodie"></i><span>Mélodie</span></button>
</nav>

<section id="panneau">
  <div class="section" data-section="prereglage">
    <div class="tete"><i class="ic" data-ic="prereglage"></i><h2>Préréglage</h2>
      <button type="button" class="q" title="explications">?</button></div>
    <p class="desc">Un point de départ : il repose tous les curseurs d'un coup. Vos propres réglages s'enregistrent ici.</p>
    <div class="explications" hidden>
      <p>Ceux qu'un préréglage ne mentionne pas reviennent à leur valeur d'usine : deux préréglages enchaînés ne se mélangent donc pas.</p>
      <p><b>Enregistrer</b> garde d'un coup tous les curseurs, toutes les listes, les couleurs et le titre — tout sauf la définition, la cadence et le morceau. Ils sont écrits dans <code>out/studio/mes-reglages.json</code> et vous les retrouverez à la prochaine ouverture.</p>
    </div>
    <div class="ctl liste"><label for="preset">préréglage</label>
      <select id="preset"></select></div>
    <div class="row">
      <input type="text" id="presetNom" maxlength="40"
             placeholder="nom de votre réglage">
      <button class="ghost" id="presetSave">Enregistrer</button>
    </div>
    <button class="ghost" id="presetDel" disabled>Effacer ce réglage</button>
  </div>

  <div class="section" data-section="machine">
    <div class="tete"><i class="ic" data-ic="machine"></i><h2>Machine</h2>
      <button type="button" class="q" title="explications">?</button></div>
    <p class="desc">La machine dessinée, et celles qui lui succèdent au fil du morceau.</p>
    <div class="explications" hidden>
      <p>Le tracé d'une machine se déforme jusqu'à devenir celui de la suivante : les pads glissent sur les déclencheurs, les encodeurs sur les potards. Les noms, eux, ne se déforment pas — ils se croisent sur place, parce qu'une lettre qui se déforme en une autre ne se lit plus.</p>
      <p>La déformation <b>précède</b> l'instant inscrit : à « 0:32 digitakt » avec 1,9 s de déformation, elle commence à 0:30 et le Digitakt est bien posé à 0:32.</p>
    </div>
    <div class="ctl liste"><label for="machine">la machine du début</label>
      <select id="machine"></select></div>
    <div class="seq" id="seqBloc">
      <div class="titre">puis, en cours de morceau</div>
      <div id="seqListe"></div>
      <div class="row seqplus">
        <button class="ghost" id="seqPlus">Ajouter un changement</button>
      </div>
      <div class="row seqauto">
        <button class="ghost" id="seqAuto">Répartir toutes les</button>
        <input type="number" id="seqChaque" min="4" max="600" step="1" value="30">
        <span class="unite">s</span>
      </div>
      <input type="hidden" id="machines">
    </div>
    <div class="groupe">D'une machine à l'autre</div>
    <div class="ctl"><label for="passage">durée de la déformation</label>
      <input type="range" id="passage" min="0" max="6" step="0.1" value="1.9"><output><span id="v-psg">1.90 s</span></output></div>
    <div class="ctl"><label for="passageTurb">ondulation pendant la déformation</label>
      <input type="range" id="passageTurb" min="0" max="2.5" step="0.05" value="1"><output><span id="v-psgt">1.00</span></output></div>
  </div>

  <div class="section" data-section="lumiere">
    <div class="tete"><i class="ic" data-ic="lumiere"></i><h2>Lumière des coups</h2>
      <button type="button" class="q" title="explications">?</button></div>
    <p class="desc">Comment s'allument les pads et les touches frappés : éclat, couleur, texture.</p>
    <div class="explications" hidden>
      <p>Ce qui s'allume quand un coup tombe : les pads de la MPC, de la SP-404 et du Digitakt, les touches du MiniFreak. L'<b>éclat</b> monte ou baisse leur lumière sans toucher au reste du tracé. La <b>couleur</b> peut quitter celle du trait : une seule, choisie, ou une par instrument — rouge la grosse caisse, jaune la caisse claire, cyan le charley, et sur le clavier une teinte par note de la gamme. La <b>texture</b> dessine l'intérieur de la touche allumée.</p>
    </div>
    <div class="ctl"><label for="eclatPads">éclat des pads frappés</label>
      <input type="range" id="eclatPads" min="0" max="3" step="0.05" value="1"><output><span id="v-ep">1.00</span></output></div>
    <div class="ctl liste"><label for="couleurCoups">couleur de la lumière</label>
      <select id="couleurCoups">
        <option value="trait" selected>celle du trait</option>
        <option value="libre">une couleur au choix</option>
        <option value="instrument">une par instrument</option>
        <option value="arc-en-ciel">une au hasard à chaque coup</option>
      </select></div>
    <div id="libreBloc" hidden>
      <div class="ctl couleur"><label for="couleurCoupsLibre">la couleur choisie</label>
        <input type="color" id="couleurCoupsLibre" value="#ff7a1f"></div>
    </div>
    <div class="ctl liste"><label for="textureTouches">texture des touches allumées</label>
      <select id="textureTouches">
        <option value="nappe" selected>nappe pleine</option>
        <option value="lignes">lignes</option>
        <option value="hachures">hachures</option>
        <option value="quadrillage">quadrillage</option>
        <option value="points">points</option>
        <option value="cadres">cadres emboîtés</option>
        <option value="eclat">éclat au centre</option>
        <option value="contour">contour épais</option>
      </select></div>
  </div>

  <div class="section" data-section="couleur">
    <div class="tete"><i class="ic" data-ic="couleur"></i><h2>Couleur du trait</h2>
      <button type="button" class="q" title="explications">?</button></div>
    <p class="desc">La teinte du trait et sa nature : néon, encre pour les fonds clairs, ou les deux.</p>
    <div class="explications" hidden>
      <p>Sur un <b>fond clair</b> — un ciel, une vidéo de nuages — un néon se perd : sa lumière s'ajoute au blanc. Trois réponses : <b>encre</b> peint la machine en trait foncé par-dessus le fond (les coups gardent leur couleur) ; <b>auto</b> choisit point par point, néon sur le sombre et encre sur le clair, ce qui suit un ciel qui change ; le <b>détourage</b> pose un liseré autour du trait, sombre en néon, clair en encre. En encre, le fond reste tel qu'il est derrière la machine ; le curseur <b>papier derrière la machine</b> l'éclaircit en halo blanc, pour détacher le trait d'un fond chargé.</p>
      <p><b>Inverser</b> met en négatif toute l'image, ou seulement le fond (le trait ne bouge pas), ou seulement le trait, qui prend alors sa couleur complémentaire sur un fond intact.</p>
      <p>Les réglages du fond sont faits pour le néon : ils assombrissent la photo. Pour un ciel lumineux, montez la <b>présence du fond</b> et baissez le <b>dégagement</b> — en auto, ce qui reste sombre (l'écran de la machine, le creux) garde le néon, le reste passe à l'encre.</p>
    </div>
    <div class="ctl liste"><label for="palette">couleur</label>
      <select id="palette">
        <option value="vert">vert (par défaut)</option>
        <option value="orange">orange</option>
        <option value="bleu">bleu</option>
        <option value="bleu-fond">bleu sur fond bleu</option>
        <option value="perso">couleur libre…</option>
      </select></div>
    <div id="traitwrap" hidden>
      <div class="ctl couleur"><label for="trait">teinte</label>
        <input type="color" id="trait" value="#3dff72"></div>
    </div>
    <div class="ctl liste"><label for="modeTrait">nature du trait</label>
      <select id="modeTrait">
        <option value="neon" selected>néon : une lumière (fonds sombres)</option>
        <option value="encre">encre : un trait foncé (fonds clairs)</option>
        <option value="auto">auto : selon le fond derrière chaque trait</option>
      </select></div>
    <div id="encreBloc" hidden>
      <div class="ctl couleur"><label for="encre">couleur de l'encre</label>
        <input type="color" id="encre" value="#0a1210"></div>
      <div class="ctl"><label for="papier">papier derrière la machine</label>
        <input type="range" id="papier" min="0" max="1" step="0.05" value="0"><output><span id="v-pap">0.00</span></output></div>
    </div>
    <div class="ctl"><label for="detourage">détourage</label>
      <input type="range" id="detourage" min="0" max="2" step="0.05" value="0"><output><span id="v-det">0.00</span></output></div>
    <div class="ctl liste"><label for="inverser">inverser (négatif)</label>
      <select id="inverser">
        <option value="non" selected>non</option>
        <option value="tout">toute l'image</option>
        <option value="fond">le fond seulement</option>
        <option value="trait">le trait seulement</option>
      </select></div>
  </div>

  <div class="section" data-section="dalle">
    <div class="tete"><i class="ic" data-ic="dalle"></i><h2>Texture de la dalle</h2>
      <button type="button" class="q" title="explications">?</button></div>
    <p class="desc">La texture qui tapisse l'écran derrière la machine — d'un simple quadrillage à un tunnel ou un champ d'étoiles qui vivent avec l'animation —, et l'allure du tube lui-même.</p>
    <div class="explications" hidden>
      <p>Le trait est additif : un fond clair mange son contraste. Le <b>dégagement</b> creuse la texture derrière la machine pour qu'elle ressorte quand même.</p>
      <p>Les coins assombris, les lignes de tube et la frange d'objectif valent pour toutes les textures, même le noir : c'est l'écran lui-même.</p>
    </div>
    <div class="ctl liste"><label for="bg">texture</label>
      <select id="bg">
        <option value="noir">noir (aucune)</option>
        <option value="uni">couleur unie</option>
        <option value="grille">grille d'oscilloscope</option>
        <option value="points">trame de points</option>
        <option value="scan">lignes de tube</option>
        <option value="degrade">dégradé (sombre au centre)</option>
        <option value="bruit">grain</option>
        <option value="plasma">plasma (ondes qui se croisent)</option>
        <option value="horizon">horizon quadrillé (sol qui file)</option>
        <option value="etoiles">champ d'étoiles (hyperespace)</option>
        <option value="ondes">ondes croisées (moiré)</option>
        <option value="tunnel">tunnel (cadres qui viennent)</option>
      </select></div>
    <div id="bgopts" hidden>
      <div class="ctl couleur"><label for="bgColor">couleur du fond</label>
        <input type="color" id="bgColor" value="#123a5c"></div>
      <div class="ctl"><label for="bgStrength">intensité</label>
        <input type="range" id="bgStrength" min="0" max="2" step="0.05" value="1"><output><span id="v-str">1.00</span></output></div>
      <div class="ctl"><label for="bgClear">dégagement derrière la machine</label>
        <input type="range" id="bgClear" min="0" max="1" step="0.05" value="0.55"><output><span id="v-clr">0.55</span></output></div>
      <div class="ctl"><label for="bgAnim">animation de la texture</label>
        <input type="range" id="bgAnim" min="0" max="6" step="0.1" value="0"><output><span id="v-ba">0.00</span></output></div>
    </div>
    <div class="groupe">L'écran</div>
    <div class="ctl"><label for="vignettage">coins assombris</label>
      <input type="range" id="vignettage" min="0" max="2" step="0.05" value="1"><output><span id="v-vig">1.00</span></output></div>
    <div class="ctl"><label for="scanlines">lignes de tube</label>
      <input type="range" id="scanlines" min="0" max="2" step="0.05" value="1"><output><span id="v-scl">1.00</span></output></div>
    <div class="ctl"><label for="aberration">frange d'objectif</label>
      <input type="range" id="aberration" min="0" max="1.5" step="0.05" value="0"><output><span id="v-abr">0.00</span></output></div>
  </div>

  <div class="section" data-section="fonds">
    <div class="tete"><i class="ic" data-ic="fonds"></i><h2>Fonds : images et vidéos</h2>
      <button type="button" class="q" title="explications">?</button></div>
    <p class="desc">Une image, une vidéo, ou plusieurs à la suite, avec leur vitesse et leurs fondus.</p>
    <div class="explications" hidden>
      <p>Sur une vidéo, l'aperçu montre l'image de l'instant regardé ; le rendu, lui, la joue en entier, et la reprend quand elle est plus courte que le morceau.</p>
      <p><b>Plusieurs fichiers</b> se jouent dans l'ordre de la liste, chacun jusqu'au bout pour une vidéo, le temps choisi pour une photo ; le <b>fondu enchaîné</b> passe de l'un à l'autre, et du dernier au premier quand la suite reprend. En <b>aller-retour</b>, la suite se rejoue à l'envers au lieu de repartir du début : pas de saut, même sans fondu.</p>
      <p>Le <b>travelling</b> s'étale sur tout le morceau : l'image est chargée plus grande que l'écran et on s'y déplace lentement. Quelques pour cent suffisent à lui ôter son air de décor collé derrière la machine.</p>
    </div>
    <div class="drop" id="bdrop">
      <b id="bdname">Déposer une ou plusieurs images ou vidéos</b>
      jpg, png, mp4, mov… ou cliquer. Plusieurs fichiers se jouent à la suite.
    </div>
    <input type="file" id="bdfile" accept="image/*,video/*" multiple hidden>
    <ol id="bdliste" hidden></ol>
    <div id="bdopts" hidden>
      <div id="bdjeu" hidden>
        <div class="groupe">La suite</div>
        <div class="ctl"><label for="fondVitesse">vitesse des vidéos</label>
          <input type="range" id="fondVitesse" min="-2" max="2" step="0.05" value="0"><output><span id="v-fv">1.00 ×</span></output></div>
        <div class="ctl liste"><label for="fondBoucle">au bout de la suite</label>
          <select id="fondBoucle">
            <option value="boucle">reprendre du début (boucle)</option>
            <option value="allerretour">repartir à l'envers (aller-retour)</option>
          </select></div>
        <div class="ctl"><label for="fondFondu">fondu enchaîné</label>
          <input type="range" id="fondFondu" min="0" max="4" step="0.1" value="0"><output><span id="v-ff">0.0 s</span></output></div>
        <div id="bdphoto" hidden>
          <div class="ctl"><label for="fondPhoto">durée d'une photo</label>
            <input type="range" id="fondPhoto" min="1" max="30" step="0.5" value="6"><output><span id="v-fp">6.0 s</span></output></div>
        </div>
      </div>
      <div class="groupe">L'image</div>
      <div class="ctl"><label for="bdStrength">présence du fond</label>
        <input type="range" id="bdStrength" min="0" max="1.6" step="0.02" value="0.78"><output><span id="v-bds">0.78</span></output></div>
      <div class="ctl"><label for="bdClear">dégagement derrière la machine</label>
        <input type="range" id="bdClear" min="0" max="1" step="0.05" value="0.40"><output><span id="v-bdc">0.40</span></output></div>
      <div class="ctl"><label for="screenDim">opacité de la dalle</label>
        <input type="range" id="screenDim" min="0" max="1" step="0.05" value="0.40"><output><span id="v-sd">0.40</span></output></div>
      <div class="ctl"><label for="bdSharp">netteté du fond</label>
        <input type="range" id="bdSharp" min="0" max="1" step="0.01" value="0.37"><output><span id="v-bdq">0.37</span></output></div>
      <div class="ctl"><label for="travel">travelling</label>
        <input type="range" id="travel" min="0" max="0.5" step="0.01" value="0"><output><span id="v-tv">0 %</span></output></div>
      <div class="ctl liste"><label for="travelMode">sens du travelling</label>
        <select id="travelMode"></select></div>
      <button class="ghost" id="bdclear">Retirer tous les fonds</button>
    </div>
  </div>

  <div class="section" data-section="trait">
    <div class="tete"><i class="ic" data-ic="trait"></i><h2>Trait</h2>
      <button type="button" class="q" title="explications">?</button></div>
    <p class="desc">Taille, finesse et lumière du tracé, et la courbe du son sur l'écran de la machine.</p>
    <div class="explications" hidden>
      <p>Sur les coups vraiment appuyés — et seulement ceux-là — les trois couches de couleur du trait se séparent, puis se recollent quand le coup retombe. Le fond, lui, ne bouge pas.</p>
      <p>Le nombre de dédoublements est un plafond, pas une consigne : deux dédoublements ne peuvent pas tomber à moins de 25 secondes l'un de l'autre, et une vidéo courte en reçoit donc moins. Par défaut ils ne partent que sur la grosse caisse — la basse, souvent posée sur le même temps que la caisse claire, donnait l'impression qu'ils se déclenchaient sur elle.</p>
    </div>
    <div class="groupe">Le tracé</div>
    <div class="ctl"><label for="taille">taille de la machine</label>
      <input type="range" id="taille" min="0.35" max="1.3" step="0.01" value="1"><output><span id="v-ta">1.00</span></output></div>
    <div class="ctl"><label for="presence">présence de la machine</label>
      <input type="range" id="presence" min="0.1" max="1.6" step="0.02" value="1"><output><span id="v-pr">1.00</span></output></div>
    <div class="ctl"><label for="neon">éclat du néon</label>
      <input type="range" id="neon" min="0.2" max="3" step="0.05" value="1"><output><span id="v-ne">1.00</span></output></div>
    <div class="ctl"><label for="reflet">surface qui renvoie la lumière</label>
      <input type="range" id="reflet" min="0" max="1" step="0.02" value="0.5"><output><span id="v-re">0.50</span></output></div>
    <div class="ctl"><label for="tube">tube de verre (relief)</label>
      <input type="range" id="tube" min="0" max="1.5" step="0.05" value="0"><output><span id="v-tu">0.00</span></output></div>
    <div class="ctl"><label for="nettete">finesse du trait</label>
      <input type="range" id="nettete" min="0.6" max="1.7" step="0.05" value="1"><output><span id="v-net">1.00</span></output></div>
    <div class="ctl"><label for="wobble">ondulation du tracé</label>
      <input type="range" id="wobble" min="0" max="1.5" step="0.05" value="0"><output><span id="v-wob">0.00</span></output></div>
    <div class="groupe">Dédoublement sur les gros coups</div>
    <div class="ctl"><label for="split">dédoublement du trait</label>
      <input type="range" id="split" min="0" max="2.5" step="0.05" value="0"><output><span id="v-split">0.00</span></output>
      <select id="splitOn" class="inst"></select></div>
    <div class="ctl"><label for="splitCount">dédoublements dans la vidéo, au plus</label>
      <input type="range" id="splitCount" min="0" max="12" step="1" value="3"><output><span id="v-sc">3</span></output></div>
    <div class="ctl"><label for="splitPx">écart des copies</label>
      <input type="range" id="splitPx" min="0" max="30" step="1" value="11"><output><span id="v-spx">11</span> px</output></div>
    <div class="groupe">La courbe du son</div>
    <div class="ctl"><label for="wave">amplitude de la courbe</label>
      <input type="range" id="wave" min="0" max="3" step="0.05" value="1.10"><output><span id="v-wv">1.10</span></output></div>
    <div class="ctl"><label for="wavePunch">gonflement sur le temps fort</label>
      <input type="range" id="wavePunch" min="0" max="2.5" step="0.05" value="0.85"><output><span id="v-wp">0.85</span></output></div>
    <div class="ctl"><label for="waveSmooth">lissage de la courbe</label>
      <input type="range" id="waveSmooth" min="8" max="240" step="4" value="56"><output><span id="v-ws">56</span></output></div>
    <div class="ctl"><label for="trail">traînée de la bande</label>
      <input type="range" id="trail" min="0" max="2.5" step="0.05" value="1"><output><span id="v-trail">1.00</span></output></div>
    <div class="groupe">Coups et paroxysmes</div>
    <div class="ctl"><label for="snare">éclair jaune sur la caisse claire</label>
      <input type="range" id="snare" min="0" max="2" step="0.05" value="0"><output><span id="v-sn">0.00</span></output></div>
    <div class="ctl"><label for="glitch">glitchs sur les paroxysmes</label>
      <input type="range" id="glitch" min="0" max="2" step="0.05" value="0"><output><span id="v-gl">0.00</span></output></div>
    <div class="groupe">La dalle de la machine</div>
    <div class="ctl liste"><label for="stepDiv">vitesse des pas du séquenceur</label>
      <select id="stepDiv">
        <option value="1">lente — une case par temps</option>
        <option value="2" selected>moyenne — une case par demi-temps</option>
        <option value="4">rapide — une case par quart de temps</option>
      </select></div>
    <div class="ctl texte"><label for="title">titre affiché sur la dalle</label>
      <input type="text" id="title" maxlength="22" placeholder="nom du fichier"></div>
  </div>

  <div class="section" data-section="reactions">
    <div class="tete"><i class="ic" data-ic="reactions"></i><h2>Réactions au son</h2>
      <button type="button" class="q" title="explications">?</button></div>
    <p class="desc">Ce qui bouge au rythme. Par défaut, une réaction ne part sur rien : elle est là tant que son curseur est monté, ou dans ses blocs de la frise. Calez-la sur un instrument, une bande de fréquences ou le hasard si vous le voulez ; à zéro, elle est éteinte.</p>
    <div class="explications" hidden>
      <p><b>Aucun</b>, le choix par défaut : l'effet ne part sur aucun coup. Il est là, en continu, tant que son curseur est monté — ou seulement dans ses blocs posés sur la frise, ce qui permet de le programmer passage par passage. Les effets qui partent par jets (étincelles, onde de choc, bégaiement, patinage) en lancent un tous les quarts de seconde.</p>
      <p>Les listes proposent aussi quatre sortes de déclencheurs. <b>Instruments</b> : la batterie est reconnue à l'analyse, « caisse claire » veut donc vraiment dire caisse claire. <b>Bandes de fréquences</b> : une hauteur et non un instrument — elles attrapent aussi ce qui n'est pas percussif, une nappe qui monte, une voix, un souffle de cymbale. <b>Hasard</b> : tiré au sort, mais posé sur la grille du morceau, donc jamais à contretemps. <b>Un coup sur deux</b> : deux effets posés l'un sur « 1 sur 2 » et l'autre sur « l'autre sur 2 » ne peuvent jamais partir ensemble — c'est la réponse quand tout tombe en même temps.</p>
      <p>Pour une vraie explosion d'étincelles, montez le nombre, la vitesse et la durée ensemble. Au-delà de quelques centaines de braises, le tracé de chacune est écourté pour tenir un budget de points par image : c'est ce qui permet d'en lancer des dizaines de milliers sans que le rendu s'effondre.</p>
      <p>Chacun peut aussi n'agir que sur un passage : attrapez-le par sa poignée ⠿, à gauche de son nom, et lâchez-le sur la frise sous l'aperçu. Le curseur ci-dessous vaut alors pour le reste du morceau — à zéro, l'effet n'existe que dans ses blocs.</p>
    </div>
    <div class="groupe">L'image</div>
    <div class="ctl"><label for="punch">zoom d'impact</label>
      <input type="range" id="punch" min="0" max="0.25" step="0.005" value="0"><output><span id="v-pu">0.000</span></output>
      <select id="punchOn" class="inst"></select></div>
    <div class="ctl"><label for="shake">secousse de l'image</label>
      <input type="range" id="shake" min="0" max="2" step="0.05" value="0"><output><span id="v-sh">0.00</span></output>
      <select id="shakeOn" class="inst"></select></div>
    <div class="groupe">Étincelles</div>
    <div class="ctl"><label for="parts">étincelles éjectées</label>
      <input type="range" id="parts" min="0" max="3" step="0.05" value="0"><output><span id="v-pa">0.00</span></output>
      <select id="partsOn" class="inst"></select></div>
    <div class="ctl"><label for="partsN">nombre par coup</label>
      <input type="range" id="partsN" min="2" max="180" step="1" value="4"><output><span id="v-pan">16</span></output></div>
    <div class="ctl"><label for="partsSpeed">vitesse des étincelles</label>
      <input type="range" id="partsSpeed" min="0.2" max="2.5" step="0.05" value="1"><output><span id="v-pas">1.00</span></output></div>
    <div class="ctl"><label for="partsLife">durée de vie</label>
      <input type="range" id="partsLife" min="0.15" max="1.5" step="0.05" value="0.55"><output><span id="v-pal">0.55</span> s</output></div>
    <div class="groupe">Lumière</div>
    <div class="ctl"><label for="ring">onde de choc</label>
      <input type="range" id="ring" min="0" max="3" step="0.05" value="0"><output><span id="v-ri">0.00</span></output>
      <select id="ringOn" class="inst"></select></div>
    <div class="ctl"><label for="gridPulse">pulsation de la grille</label>
      <input type="range" id="gridPulse" min="0" max="3" step="0.05" value="0"><output><span id="v-gp">0.00</span></output>
      <select id="gridOn" class="inst"></select></div>
    <div class="ctl"><label for="bgFlash">éclat du fond</label>
      <input type="range" id="bgFlash" min="0" max="2" step="0.05" value="0"><output><span id="v-bf">0.00</span></output>
      <select id="flashOn" class="inst"></select></div>
  </div>

  <div class="section" data-section="avaries">
    <div class="tete"><i class="ic" data-ic="avaries"></i><h2>Avaries d'image</h2>
      <button type="button" class="q" title="explications">?</button></div>
    <p class="desc">Les pannes d'une vieille image, déclenchées par ce qui est joué : coupures, décalages, blocs, négatif — et des glitchs d'art vidéo : tri de pixels, rétroaction, tourbillon, 8 bits, tracking.</p>
    <div class="explications" hidden>
      <p>Les mêmes pannes que sur les paroxysmes, mais déclenchées par ce qui est joué. Elles s'appliquent à l'image finie, juste avant la déformation du tube : d'où leur air de signal cassé plutôt que d'effet dessiné.</p>
      <p>Le <b>bégaiement</b> décroche l'image du son : elle rejoue en boucle un bout très court pris à l'instant du coup. Une boucle plus courte qu'une image donne un gel pur ; deux ou trois images donnent un sursaut répété, bien plus visible.</p>
      <p>Les <b>glitchs avancés</b> viennent de l'art vidéo plutôt que de la panne : le <b>tri de pixels</b> range chaque ligne par clarté, la <b>déchirure RVB</b> sépare les trois couleurs, la <b>rétroaction</b> filme l'écran dans l'écran, la <b>compression cassée</b> fait baver des macroblocs, le <b>tourbillon</b> aspire l'image en spirale, l'<b>écran 8 bits</b> la réduit à quelques teintes tramées, et la <b>bande de tracking</b> monte comme sur une cassette mal calée. Sur « aucun », ils sont là en continu ; sur un instrument, chaque coup les lance.</p>
      <p>Les <b>tranches brassées</b> ne dépendent d'aucun instrument : elles découpent le temps en blocs réguliers et les rejouent dans le désordre, pendant que le son continue tout droit. Des tranches courtes hachent, des longues désorientent.</p>
      <p>Chacun peut aussi n'agir que sur un passage : attrapez-le par sa poignée ⠿, à gauche de son nom, et lâchez-le sur la frise sous l'aperçu. Le curseur ci-dessous vaut alors pour le reste du morceau — à zéro, l'effet n'existe que dans ses blocs.</p>
    </div>
    <div class="groupe">Coupures et décalages</div>
    <div class="ctl"><label for="tranches">bandes arrachées</label>
      <input type="range" id="tranches" min="0" max="2.5" step="0.05" value="0"><output><span id="v-tr">0.00</span></output>
      <select id="tranchesOn" class="inst"></select></div>
    <div class="ctl"><label for="blocs">blocs recopiés</label>
      <input type="range" id="blocs" min="0" max="2.5" step="0.05" value="0"><output><span id="v-bl">0.00</span></output>
      <select id="blocsOn" class="inst"></select></div>
    <div class="ctl"><label for="roll">décrochage vertical</label>
      <input type="range" id="roll" min="0" max="2" step="0.05" value="0"><output><span id="v-ro">0.00</span></output>
      <select id="rollOn" class="inst"></select></div>
    <div class="ctl"><label for="cisaille">cisaillement</label>
      <input type="range" id="cisaille" min="0" max="2.5" step="0.05" value="0"><output><span id="v-ci">0.00</span></output>
      <select id="cisailleOn" class="inst"></select></div>
    <div class="ctl"><label for="coupure">coupure franche</label>
      <input type="range" id="coupure" min="0" max="2.5" step="0.05" value="0"><output><span id="v-co">0.00</span></output>
      <select id="coupureOn" class="inst"></select></div>
    <div class="groupe">Déformations</div>
    <div class="ctl"><label for="ghost">image fantôme</label>
      <input type="range" id="ghost" min="0" max="2.5" step="0.05" value="0"><output><span id="v-gh">0.00</span></output>
      <select id="ghostOn" class="inst"></select></div>
    <div class="ctl"><label for="invert">négatif du trait</label>
      <input type="range" id="invert" min="0" max="2.5" step="0.05" value="0"><output><span id="v-in">0.00</span></output>
      <select id="invertOn" class="inst"></select></div>
    <div class="ctl"><label for="inversion">flash d'inversion (toute l'image)</label>
      <input type="range" id="inversion" min="0" max="2" step="0.05" value="0"><output><span id="v-inv">0.00</span></output>
      <select id="inversionOn" class="inst"></select></div>
    <div class="ctl"><label for="miroir">miroir</label>
      <input type="range" id="miroir" min="0" max="2" step="0.05" value="0"><output><span id="v-mi">0.00</span></output>
      <select id="miroirOn" class="inst"></select></div>
    <div class="ctl"><label for="ondul">ondulation liquide</label>
      <input type="range" id="ondul" min="0" max="2.5" step="0.05" value="0"><output><span id="v-on">0.00</span></output>
      <select id="ondulOn" class="inst"></select></div>
    <div class="ctl"><label for="mosaic">mosaïque</label>
      <input type="range" id="mosaic" min="0" max="2" step="0.05" value="0"><output><span id="v-mo">0.00</span></output>
      <select id="mosaicOn" class="inst"></select></div>
    <div class="ctl"><label for="kaleido">kaléidoscope</label>
      <input type="range" id="kaleido" min="0" max="2" step="0.05" value="0"><output><span id="v-ka">0.00</span></output>
      <select id="kaleidoOn" class="inst"></select></div>
    <div class="groupe">Glitchs avancés</div>
    <div class="ctl"><label for="tri">tri de pixels</label>
      <input type="range" id="tri" min="0" max="2.5" step="0.05" value="0"><output><span id="v-tri">0.00</span></output>
      <select id="triOn" class="inst"></select></div>
    <div class="ctl"><label for="rvb">déchirure RVB</label>
      <input type="range" id="rvb" min="0" max="2.5" step="0.05" value="0"><output><span id="v-rvb">0.00</span></output>
      <select id="rvbOn" class="inst"></select></div>
    <div class="ctl"><label for="retro">rétroaction vidéo</label>
      <input type="range" id="retro" min="0" max="2" step="0.05" value="0"><output><span id="v-ret">0.00</span></output>
      <select id="retroOn" class="inst"></select></div>
    <div class="ctl"><label for="macro">compression cassée</label>
      <input type="range" id="macro" min="0" max="2.5" step="0.05" value="0"><output><span id="v-mac">0.00</span></output>
      <select id="macroOn" class="inst"></select></div>
    <div class="ctl"><label for="tourbillon">tourbillon</label>
      <input type="range" id="tourbillon" min="0" max="2" step="0.05" value="0"><output><span id="v-tou">0.00</span></output>
      <select id="tourbillonOn" class="inst"></select></div>
    <div class="ctl"><label for="bits">écran 8 bits</label>
      <input type="range" id="bits" min="0" max="2" step="0.05" value="0"><output><span id="v-bit">0.00</span></output>
      <select id="bitsOn" class="inst"></select></div>
    <div class="ctl"><label for="tracking">bande de tracking VHS</label>
      <input type="range" id="tracking" min="0" max="2" step="0.05" value="0"><output><span id="v-trk">0.00</span></output>
      <select id="trackingOn" class="inst"></select></div>
    <div class="groupe">Le temps</div>
    <div class="ctl"><label for="stut">bégaiement</label>
      <input type="range" id="stut" min="0" max="0.6" step="0.01" value="0"><output><span id="v-st">0.00</span> s</output>
      <select id="stutOn" class="inst"></select></div>
    <div class="ctl"><label for="stutLoop">boucle rejouée</label>
      <input type="range" id="stutLoop" min="0.01" max="0.3" step="0.01" value="0.05"><output><span id="v-sl">0.05</span> s</output></div>
    <div class="ctl"><label for="tapestop">patinage de bande</label>
      <input type="range" id="tapestop" min="0" max="0.8" step="0.02" value="0"><output><span id="v-tp">0.00</span> s</output>
      <select id="tapestopOn" class="inst"></select></div>
    <div class="ctl"><label for="scramble">tranches de temps brassées</label>
      <input type="range" id="scramble" min="0" max="1" step="0.05" value="0"><output><span id="v-sc2">0.00</span></output></div>
    <div class="ctl"><label for="scrLen">longueur d'une tranche</label>
      <input type="range" id="scrLen" min="0.04" max="0.6" step="0.01" value="0.14"><output><span id="v-srl">0.14</span> s</output></div>
  </div>

  <div class="section" data-section="echo">
    <div class="tete"><i class="ic" data-ic="echo"></i><h2>Écho, couleurs, spectrogramme</h2>
      <button type="button" class="q" title="explications">?</button></div>
    <p class="desc">Échos d'images, teinte par instrument, spectrogramme sur la dalle.</p>
    <div class="explications" hidden>
      <p>L'<b>écho</b> redessine la machine telle qu'elle était il y a quelques centièmes, de plus en plus pâle. Les <b>couleurs par instrument</b> donnent au trait la teinte du dernier coup : rouge la grosse caisse, jaune la caisse claire, cyan le charley, violet la basse. Le <b>spectrogramme</b> déroule les trois dernières secondes du morceau sur la dalle, une ligne par bande de fréquences — baissez l'amplitude de la courbe pour bien le voir.</p>
      <p>Chacun peut aussi n'agir que sur un passage : attrapez-le par sa poignée ⠿, à gauche de son nom, et lâchez-le sur la frise sous l'aperçu. Le curseur ci-dessous vaut alors pour le reste du morceau — à zéro, l'effet n'existe que dans ses blocs.</p>
    </div>
    <div class="ctl"><label for="echo">écho d'images</label>
      <input type="range" id="echo" min="0" max="0.85" step="0.05" value="0"><output><span id="v-ec">0.00</span></output></div>
    <div class="ctl"><label for="echoN">nombre d'échos</label>
      <input type="range" id="echoN" min="1" max="6" step="1" value="3"><output><span id="v-ecn">3</span></output></div>
    <div class="ctl"><label for="echoDelay">écart entre les échos</label>
      <input type="range" id="echoDelay" min="0.02" max="0.2" step="0.005" value="0.045"><output><span id="v-ecd">0.045</span> s</output></div>
    <div class="ctl"><label for="couleurs">couleurs par instrument</label>
      <input type="range" id="couleurs" min="0" max="2.5" step="0.05" value="0"><output><span id="v-cl">0.00</span></output></div>
    <div class="ctl"><label for="spectro">spectrogramme sur la dalle</label>
      <input type="range" id="spectro" min="0" max="2" step="0.05" value="0"><output><span id="v-sp">0.00</span></output></div>
  </div>

  <div class="section" data-section="texture">
    <div class="tete"><i class="ic" data-ic="texture"></i><h2>Texture — trip hop, lo-fi</h2>
      <button type="button" class="q" title="explications">?</button></div>
    <p class="desc">Le grain d'une image fatiguée, du début à la fin : cadence tenue, halo laiteux, poussière.</p>
    <div class="explications" hidden>
      <p>Celles-ci ne frappent sur rien : elles sont là du début à la fin. C'est ce qui sépare un accident d'une matière — un grain de pellicule qui n'apparaîtrait que sur la caisse claire ne ressemblerait à rien.</p>
      <p>La <b>cadence tenue</b> garde chaque image deux, trois ou quatre fois : la vidéo passe à 15, 10 ou 7 images par seconde sans rien ralentir. C'est le geste qui donne son air d'animation à un clip lo-fi. Le <b>halo laiteux</b> relève les noirs et étale la lumière, à l'opposé du contraste franc de l'oscilloscope.</p>
      <p>Chacun peut aussi n'agir que sur un passage : attrapez-le par sa poignée ⠿, à gauche de son nom, et lâchez-le sur la frise sous l'aperçu. Le curseur ci-dessous vaut alors pour le reste du morceau — à zéro, l'effet n'existe que dans ses blocs.</p>
    </div>
    <div class="ctl"><label for="cadence">cadence tenue</label>
      <input type="range" id="cadence" min="0" max="5" step="1" value="0"><output><span id="v-ca">fluide</span></output></div>
    <div class="ctl"><label for="haloDoux">halo laiteux</label>
      <input type="range" id="haloDoux" min="0" max="2" step="0.05" value="0"><output><span id="v-hd">0.00</span></output></div>
    <div class="ctl"><label for="poussiere">poussière et rayures</label>
      <input type="range" id="poussiere" min="0" max="2.5" step="0.05" value="0"><output><span id="v-po">0.00</span></output></div>
    <div class="ctl"><label for="flottement">flottement de bande</label>
      <input type="range" id="flottement" min="0" max="2.5" step="0.05" value="0"><output><span id="v-fl">0.00</span></output></div>
  </div>

  <div class="section" data-section="melodie">
    <div class="tete"><i class="ic" data-ic="melodie"></i><h2>Mélodie MIDI</h2>
      <button type="button" class="q" title="explications">?</button></div>
    <p class="desc">Les vraies notes du morceau, une par une, sur le clavier du MiniFreak : c'est ici qu'on les cale.</p>
    <div class="explications" hidden>
      <p>C'est la touche exacte qui s'enfonce. La MPC et le Digitakt n'ont pas de clavier — leurs pads restent à la batterie, et le fichier n'y change rien.</p>
      <p>Le fichier est pris <b>tel quel</b> : un MIDI exporté du même projet que le morceau est déjà à l'heure, son décalage vaut zéro. La ligne sous les boutons dit à quel instant de la vidéo tombe la première note : lancez l'aperçu là, et regardez si la touche s'allume avec le son. La frise, sous l'aperçu, montre les notes là où elles tomberont.</p>
      <p><b>Si c'est décalé d'un bout à l'autre</b>, servez-vous des boutons plutôt que du curseur. Un fichier exporté d'un projet tombe déjà sur la grille du morceau : ce qui lui manque n'est pas un réglage fin, c'est un nombre entier de temps. Les boutons décalent d'exactement un temps ou une mesure du morceau : on clique jusqu'à ce que ça tombe juste, sans jamais sortir de la grille. Le curseur ne sert qu'à rattraper un fichier qui, lui, n'est pas sur la grille du tout.</p>
      <p><b>Si c'est calé au début et faux à la fin</b>, ce n'est plus un décalage mais une <b>dérive</b> : la grille du fichier n'a pas tout à fait le tempo du morceau, et aucun décalage ne la rattrape. Le bouton <b>mesurer la dérive</b> compare les deux grilles et pose le curseur. Mesuré sur le fichier d'essai : mélodie 85,163 BPM, morceau 85,000 — 0,19 % d'écart, soit sept centièmes de seconde au bout de trente-cinq. L'étirement part de la première note, donc le calage déjà trouvé ne bouge pas.</p>
      <p><b>Chercher le décalage tout seul</b> compare les attaques du fichier à celles du morceau. Mesuré : sur un fichier percussif il retrouve le décalage exactement ; sur une mélodie il se trompe à tous les coups, et sans qu'on puisse s'en apercevoir — un motif de doubles-croches répétitif ressemble à lui-même partout dans le morceau, et les décalages candidats se tiennent alors à 3 % les uns des autres. À ne cocher que pour une piste de batterie.</p>
      <p>Une note <b>tenue</b> n'allume pas sa touche indéfiniment : au bout de 1,2 s la touche relâche, même si le son continue. Sans cela une nappe gardait la moitié du clavier allumée et on ne voyait plus quelle note venait d'être jouée. Une note trop grave ou trop aiguë pour le clavier y est ramenée par octaves : la mélodie garde ses notes, elle change seulement d'octave.</p>
    </div>
    <div class="vide" id="midiVide"><b>Aucune mélodie chargée</b>Déposez un fichier .mid sur l'aperçu ou dans la carte Mélodie, sous la frise.
      <br><button class="ghost" id="midiChoisir">Choisir un fichier MIDI…</button></div>
    <div id="midiReglages" hidden>
      <p class="info" id="mi-p"></p>
      <div class="ctl liste"><label for="midiType">ce que contient le fichier</label>
        <select id="midiType">
          <option value="piano">une mélodie (touches du clavier)</option>
          <option value="batterie">une batterie (pads de toutes les machines)</option>
        </select></div>
      <div class="ctl nombre"><label for="midiBpm">BPM du morceau</label>
        <input type="number" id="midiBpm" min="20" max="300" step="0.01" value=""></div>
      <p class="info" id="midiGrille">&nbsp;</p>
      <div class="ctl"><label for="midiForce">éclat des touches jouées</label>
        <input type="range" id="midiForce" min="0" max="2.5" step="0.05" value="1"><output><span id="v-mif">1.00</span></output></div>
      <div class="groupe">Le décalage</div>
      <div class="ctl large"><label for="midiOffset">avance / retard</label>
        <input type="range" id="midiOffset" min="-60" max="60" step="0.001" value="0"><output><span id="v-mio">0 ms</span></output></div>
      <p class="info" id="midiSens">&nbsp;</p>
      <div class="ctl nombre"><label for="midiMs">décalage exact, en ms</label>
        <input type="number" id="midiMs" step="1" value="0"></div>
      <div class="boutons" id="midiFin">
        <button class="ghost" data-img="-1">−1 image</button>
        <button class="ghost" data-img="1">+1 image</button>
        <button class="ghost" data-ms="-10">−10 ms</button>
        <button class="ghost" data-ms="10">+10 ms</button>
      </div>
      <div class="boutons" id="midiPas">
        <button class="ghost" data-pas="-4">−1 mesure</button>
        <button class="ghost" data-pas="-1">−1 temps</button>
        <button class="ghost" data-pas="-0.25">−1/4</button>
        <button class="ghost" data-pas="0.25">+1/4</button>
        <button class="ghost" data-pas="1">+1 temps</button>
        <button class="ghost" data-pas="4">+1 mesure</button>
      </div>
      <p class="info" id="midiOu">&nbsp;</p>
      <p class="info" id="midiImage">&nbsp;</p>
      <div class="groupe">La dérive</div>
      <div class="ctl large"><label for="midiTempo">dérive</label>
        <input type="range" id="midiTempo" min="-1" max="1" step="0.005" value="0"><output><span id="v-mit">0.000 %</span></output></div>
      <button class="ghost" id="midiMesure">Mesurer la dérive</button>
      <p class="info" id="midiDerive">&nbsp;</p>
      <div class="groupe">Options</div>
      <div class="ctl coche"><label class="coche"><input type="checkbox" id="midiCale"><span>chercher le décalage tout seul</span></label></div>
      <div class="ctl coche"><label class="coche"><input type="checkbox" id="midiTelQuel"><span>lire le fichier tel quel, sans le poser sur la grille du morceau</span></label></div>
    </div>
  </div>
</section>

<div id="poignee" title="tirer pour élargir ou rétrécir les réglages ; double-clic : largeur d'origine"></div>

<section id="scene"><div id="sceneGrille">
  <div id="carteApercu">
    <div class="ecran vide" id="ecran">
      <img id="shot" alt="">
      <video id="clip" hidden loop controls playsinline></video>
      <canvas id="ecranDirect" width="480" height="270" hidden></canvas>
      <div id="ecranVide"><i class="ic" data-ic="morceau"></i><b>Déposez un morceau ici</b>ou cliquez pour le choisir. Un MIDI, une image, une vidéo déposés ici vont aussi à leur place.</div>
      <div id="masques">
        <button type="button" id="masqueFond" aria-pressed="false"><i class="ic" data-ic="fonds"></i><span>fond</span></button>
        <button type="button" id="masqueMachine" aria-pressed="false"><i class="ic" data-ic="machine"></i><span>machine</span></button>
      </div>
      <div id="direct" hidden>direct</div>
      <div id="masqueAvis" hidden></div>
    </div>
    <div id="shoterr"></div>
    <audio id="son" preload="auto"></audio>
  </div>

  <div id="chrono">
    <div id="lecture">
      <button id="ecoute" disabled title="écouter le morceau : l'aperçu suit le son (barre d'espace)" aria-label="écouter le morceau"><i class="ic" data-ic="play"></i></button>
      <button class="ghost" id="lire" disabled title="calculer pour de bon quelques secondes, avec le son, et les jouer en boucle"><i class="ic" data-ic="film"></i><span>Lire en mouvement</span></button>
      <select id="clipDur" title="durée de l'aperçu animé"
        aria-label="durée de l'aperçu animé">
        <option value="2">2 s</option>
        <option value="4" selected>4 s</option>
        <option value="8">8 s</option>
        <option value="12">12 s</option>
        <option value="16">16 s</option>
      </select>
      <div id="clipprog" hidden>
        <div class="bar"><i id="cbar"></i></div>
        <span id="ctext"></span>
        <button class="ghost" id="clipStop">Arrêter</button>
      </div>
      <span class="temps"><span id="v-t">0:00.0</span> <em>/ <span id="v-fin">0:00.0</span></em></span>
      <div class="outils">
        <button class="ghost ib" id="toPrev" disabled title="paroxysme précédent"><i class="ic" data-ic="precedent"></i></button>
        <button class="ghost ib" id="toDrop" disabled title="prochain paroxysme"><i class="ic" data-ic="suivant"></i></button>
        <button class="ghost ib texte" id="toSplit" disabled title="prochain dédoublement du trait"><i class="ic" data-ic="dedoublement"></i><span>dédoublement</span></button>
        <button class="ghost ib texte" id="hi" disabled title="un coup de grosse caisse, pris au hasard"><i class="ic" data-ic="hasard"></i><span>un kick</span></button>
      </div>
      <span class="flex"></span>
      <div class="outils">
        <button class="ghost ib" id="zMoins" title="voir plus large (Ctrl + molette sur la frise)">−</button>
        <span id="zoomTxt">tout</span>
        <button class="ghost ib" id="zPlus" title="zoomer autour de l'instant regardé (Ctrl + molette sur la frise)">+</button>
      </div>
      <button type="button" class="q" id="apercuAide" title="comment lire l'aperçu et la frise">?</button>
    </div>
    <div id="frise" tabindex="0" role="slider" aria-label="instant du morceau"
         aria-valuemin="0" aria-valuemax="0" aria-valuenow="0">
      <canvas id="friseToile"></canvas>
      <div id="friseInfo" hidden></div>
      <button type="button" id="effetsVider" hidden title="retirer de la frise tous les effets placés (Ctrl + Z les remet)">tout retirer</button>
      <p id="friseVide">La frise du morceau apparaîtra ici : le son en trois bandes, les paroxysmes, les effets placés, les machines, la mélodie et les fonds. Un clic y place l'aperçu.</p>
    </div>
    <!-- les effets places sur la frise, tels que le moteur les lit -->
    <input type="hidden" id="effets" value="[]">
  </div>
  <input type="range" id="scrub" min="0" max="100" step="0.1" value="0" disabled hidden>
  <div id="aideApercu" hidden>
    <p class="titre">L'aperçu</p>
    <p>L'aperçu est une vraie image du rendu, calculée avec vos réglages : ce que vous voyez ici est ce que vous obtiendrez.</p>
    <p><b>Écouter</b> (barre d'espace) joue le morceau dans la page, et l'aperçu le suit en plus petit, aussi vite que l'ordinateur le permet. Une vidéo de fond est ce qui coûte le plus : les boutons en haut à droite de l'aperçu masquent le fond et la machine le temps de régler. Ils ne touchent que l'aperçu, jamais l'export.</p>
    <p><b>Lire en mouvement</b> calcule pour de bon quelques secondes à partir de l'instant regardé, avec le son, et les joue en boucle : c'est la vidéo exacte, en 15 images par seconde. Le rendu final, lui, en fera 30 ou 60.</p>
    <p><b>Placer un effet</b> : attrapez-le dans le panneau par sa poignée ⠿, à gauche de son nom, et lâchez-le sur la frise. Il n'agit alors que sur la durée de son bloc ; ailleurs, c'est le curseur du panneau qui compte. Un bloc s'aimante aux temps du morceau (Alt : librement), se déplace, s'étire par ses bords ; un clic l'ouvre pour régler son intensité et son instrument. Suppr l'efface, Ctrl + Z annule.</p>
    <p><b>La frise</b> montre tout le morceau : le son en trois bandes (graves, médiums, aigus), les paroxysmes en rose, les dédoublements en jaune, les effets placés, le plan des machines, les notes de la mélodie et la suite des fonds. Un clic ou un glisser y place l'aperçu ; Ctrl + molette zoome, Maj + molette fait défiler ; les flèches du clavier avancent d'une seconde. Sur la piste Machines, le bord gauche d'une déformation se tire pour l'allonger ou la raccourcir ; prise par son milieu, elle se déplace avec son changement.</p>
  </div>

  <div id="bas">
    <div class="card" id="carteRendu">
      <div class="tete"><i class="ic" data-ic="rendu"></i><h2>Export</h2>
        <button id="go" disabled>Lancer le rendu</button></div>
      <div id="prog" hidden>
        <div class="bar"><i id="pbar"></i></div>
        <div class="hint" id="ptext"></div>
        <button class="ghost" id="stop">Arrêter le rendu</button>
      </div>
      <div id="done" hidden>
        <a class="dl" id="dl">Télécharger</a>
        <p class="hint" id="donepath"></p>
      </div>
      <div class="champs">
        <div><label for="size">définition</label>
          <select id="size">
            <option value="1920x1080">1080p</option>
            <option value="3840x2160">4K</option>
            <option value="1280x720">720p</option>
            <option value="1080x1080">carré 1080</option>
            <option value="1080x1920">vertical 1080</option>
            <option value="568x320">320p — essai rapide</option>
            <option value="320x568">320p vertical — essai rapide</option>
          </select></div>
        <div><label for="fps">images/s</label>
          <select id="fps"><option>30</option><option>60</option><option>24</option>
            <option>12</option></select></div>
        <div><label for="quality">qualité du fichier</label>
          <select id="quality"></select></div>
        <div><label for="start">départ (s)</label><input type="number" id="start" value="0" min="0" step="0.1"></div>
        <div><label for="dur">durée (s)</label><input type="number" id="dur" placeholder="tout" min="1" step="1"></div>
        <label class="coche bombe"><input type="checkbox" id="curve" checked><span>bombé de l'écran cathodique</span></label>
      </div>
    </div>
    <div class="card" id="carteSources">
      <div class="source">
        <div class="tete"><i class="ic" data-ic="morceau"></i><h2>Morceau</h2></div>
        <div class="drop" id="drop">
          <b>Déposer un fichier</b>mp3, wav, flac, m4a… ou cliquer pour choisir
        </div>
        <input type="file" id="file" accept="audio/*" hidden>
        <div class="meta" id="trackmeta" hidden>
          <span>durée <b id="m-dur">-</b></span>
          <span>tempo <b id="m-bpm">-</b></span>
          <span>coups <b id="m-hits">-</b></span>
          <span>paroxysmes <b id="m-drops">-</b></span>
        </div>
      </div>
      <div class="source">
        <div class="tete"><i class="ic" data-ic="melodie"></i><h2>Mélodie MIDI</h2></div>
        <div class="drop" id="midiDrop">
          <b>Déposer un fichier MIDI</b>.mid, .midi — ou cliquer pour choisir
        </div>
        <input type="file" id="midifile" accept=".mid,.midi,audio/midi" hidden>
        <input type="hidden" id="midi">
        <div class="meta" id="midimeta" hidden>
          <span>notes <b id="mi-n">-</b></span>
          <span>étendue <b id="mi-e">-</b></span>
          <span>première note <b id="mi-c">-</b></span>
        </div>
        <p class="hint err" id="midiAvis" hidden>Aucun MiniFreak dans le plan des machines : la mélodie ne sera jouée nulle part. Choisissez-le comme machine du début, ou ajoutez-le au séquenceur.</p>
        <div class="actions" id="midiActions" hidden>
          <button class="ghost" id="midiCaler">Caler la mélodie…</button>
          <button class="ghost" id="midiOte">Retirer</button>
        </div>
      </div>
    </div>
  </div>
</div></section>
</main>

<div id="styles" hidden>
  <div class="fen" role="dialog" aria-labelledby="stylesTitre">
    <div class="tete">
      <div>
        <h2 id="stylesTitre">Styles</h2>
        <p class="hint" style="margin:6px 0 0">Chaque style se pose
          <b>par-dessus</b> tes réglages : il change la couleur, la lumière et
          la matière de la dalle, mais garde tes réactions, ta machine et ta
          mélodie. Les vignettes sont prises sur un coup de grosse caisse, là
          où les styles réactifs se montrent.</p>
      </div>
      <button class="ghost ferme" id="stylesFerme">Retour au studio</button>
    </div>
    <div class="grille" id="stylesGrille"></div>
  </div>
</div>

<div id="bulle" role="tooltip" hidden></div>

<!-- l'effet qu'on emporte du panneau vers la frise -->
<div id="glisse" hidden></div>

<!-- le bloc d'effet choisi sur la frise : pas d'identifiant sur ses champs,
     les prereglages et les reglages enregistres ne doivent pas les voir -->
<div id="blocInsp" role="dialog" aria-label="effet placé sur la frise" hidden>
  <div class="tete"><i class="bi-pt"></i><b class="bi-nom"></b>
    <button type="button" class="ferme bi-ferme" aria-label="fermer">✕</button></div>
  <p class="quand bi-quand"></p>
  <div class="ctl"><label>intensité</label>
    <input type="range" class="bi-v" aria-label="intensité sur ce passage"><output class="bi-vo"></output></div>
  <div class="ctl liste bi-inst"><label>déclenché par</label>
    <select class="bi-on" aria-label="instrument qui déclenche l'effet sur ce passage"></select></div>
  <div class="bornes">
    <span>de</span><input type="number" class="bi-a" min="0" step="0.1" aria-label="début du bloc, en secondes">
    <span>à</span><input type="number" class="bi-b" min="0" step="0.1" aria-label="fin du bloc, en secondes">
    <span>s</span></div>
  <p class="note bi-note" hidden>À zéro, l'effet est coupé sur ce passage, même si son curseur du panneau est monté.</p>
  <div class="boutons">
    <button type="button" class="ghost bi-aller">Aller au début</button>
    <button type="button" class="ghost bi-dup">Dupliquer</button>
    <button type="button" class="ghost danger bi-sup">Supprimer</button>
  </div>
</div>

<script>
const $ = s => document.querySelector(s);
let track = null, drops = [], duration = 0, jobTimer = null, shotSeq = 0;

/* ---------- envoi du morceau ---------- */
const drop = $('#drop'), file = $('#file');
drop.onclick = () => file.click();
drop.ondragover = e => { e.preventDefault(); drop.classList.add('over'); };
drop.ondragleave = () => drop.classList.remove('over');
drop.ondrop = e => {
  e.preventDefault(); drop.classList.remove('over');
  if (e.dataTransfer.files[0]) upload(e.dataTransfer.files[0]);
};
file.onchange = () => file.files[0] && upload(file.files[0]);

async function upload(f) {
  $('#go').disabled = true;
  // l'ecoute du morceau d'avant s'arrete la
  ECOUTE.arreter(false);
  try {
    const j = await deposer('/upload', f, 'du morceau');
    track = j.track; drops = j.drops || []; duration = j.duration;
    // les frappes du morceau : c'est d'elles que sortent les frequences
    FRAPPES = {frappes: j.frappes || {}, drops: j.drops || [],
               duree: j.duree || j.duration || 0, bpm: j.bpm || 0};
    majFrequences();
    // court : la case est etroite quand les sources passent a droite
    const sec = Math.round(j.duration);
    $('#m-dur').textContent = Math.floor(sec / 60) + ':' + String(sec % 60).padStart(2, '0');
    $('#m-bpm').textContent = j.bpm.toFixed(1) + ' BPM';
    $('#m-hits').textContent = j.hits;
    $('#m-drops').textContent = drops.length;
    $('#trackmeta').hidden = false;
    puceFichier(drop, 'morceau', j.name, 'remplacer');
    majPuce(j);
    $('#title').placeholder = j.name.replace(/\.[^.]+$/, '');
    $('#scrub').max = Math.max(1, duration - 1); $('#scrub').disabled = false;
    // on ouvre sur un moment ordinaire, pas sur un paroxysme : l'image
    // glitchee ne dit rien des couleurs. Le bouton dedie y emmene.
    $('#scrub').value = (duration * 0.35).toFixed(2);
    $('#dur').placeholder = 'tout';
    $('#go').disabled = false;
    $('#lire').disabled = false;
    for (const id of ['#toPrev', '#toDrop', '#toSplit', '#hi']) $(id).disabled = false;
    // les effets places sur ce morceau la derniere fois, et son ecoute
    // (par nom et par duree : deux « mix.wav » differents ne se melangent pas)
    const retrouves = EFFETS.morceau(j.name + '|' + Math.round(j.duration));
    ECOUTE.morceau();
    FRISE.morceau();
    majTemps();
    proposerBpm(j.bpm);
    majCalage();
    majOffset();          // le tempo et la longueur viennent d'arriver
    setStatus(j.name + ' — ' + j.bpm.toFixed(1) + ' BPM, ' + drops.length +
      ' paroxysme(s) : les glitchs tomberont là.' + (retrouves > 1
        ? ' Les ' + retrouves + ' effets placés la dernière fois sur ce morceau sont revenus.'
        : retrouves ? " L'effet placé la dernière fois sur ce morceau est revenu." : ''));
    shot();
  } catch (e) { setStatus('echec : ' + e.message, true); }
}

/* ---------- apercu ---------- */
function params() {
  const p = new URLSearchParams({
    track, t: $('#scrub').value,
    machine: $('#machine').value,
    machines: $('#machines').value,
    passage: $('#passage').value, passageTurb: $('#passageTurb').value,
    midi: $('#midi').value,
    midiForce: $('#midiForce').value, midiOffset: $('#midiOffset').value,
    eclatPads: $('#eclatPads').value, couleurCoups: $('#couleurCoups').value,
    couleurCoupsLibre: $('#couleurCoupsLibre').value,
    textureTouches: $('#textureTouches').value,
    modeTrait: $('#modeTrait').value, encre: $('#encre').value,
    detourage: $('#detourage').value,
    inverser: $('#inverser').value, papier: $('#papier').value,
    midiTempo: $('#midiTempo').value, midiType: $('#midiType').value,
    midiBpm: $('#midiBpm').value,
    midiTelQuel: $('#midiTelQuel').checked ? '1' : '0',
    vignettage: $('#vignettage').value, scanlines: $('#scanlines').value,
    aberration: $('#aberration').value,
    midiCale: $('#midiCale').checked ? '1' : '0',
    palette: $('#palette').value, trait: $('#trait').value,
    bg: $('#bg').value, bgColor: $('#bgColor').value,
    bgStrength: $('#bgStrength').value, bgClear: $('#bgClear').value,
    split: $('#split').value, wobble: $('#wobble').value,
    trail: $('#trail').value, title: $('#title').value,
    fallbackTitle: ($('#title').placeholder || ''),
    splitCount: $('#splitCount').value, splitPx: $('#splitPx').value,
    splitOn: $('#splitOn').value, glitch: $('#glitch').value,
    snare: $('#snare').value, wave: $('#wave').value,
    waveSmooth: $('#waveSmooth').value,
    wavePunch: $('#wavePunch').value,
    backdrop: fonds.map(f => f.name).join('|'),
    fondVitesse: $('#fondVitesse').value, fondBoucle: $('#fondBoucle').value,
    fondFondu: $('#fondFondu').value, fondPhoto: $('#fondPhoto').value,
    bdStrength: $('#bdStrength').value, bdClear: $('#bdClear').value,
    screenDim: $('#screenDim').value,
    travel: $('#travel').value, travelMode: $('#travelMode').value,
    punch: $('#punch').value, punchOn: $('#punchOn').value,
    shake: $('#shake').value, shakeOn: $('#shakeOn').value,
    parts: $('#parts').value, partsOn: $('#partsOn').value,
    partsN: String(Math.round($('#partsN').value * $('#partsN').value)),
    partsSpeed: $('#partsSpeed').value, partsLife: $('#partsLife').value,
    ring: $('#ring').value, ringOn: $('#ringOn').value,
    gridPulse: $('#gridPulse').value, gridOn: $('#gridOn').value,
    bgFlash: $('#bgFlash').value, flashOn: $('#flashOn').value,
    tranches: $('#tranches').value, tranchesOn: $('#tranchesOn').value,
    blocs: $('#blocs').value, blocsOn: $('#blocsOn').value,
    roll: $('#roll').value, rollOn: $('#rollOn').value,
    ghost: $('#ghost').value, ghostOn: $('#ghostOn').value,
    invert: $('#invert').value, invertOn: $('#invertOn').value,
    inversion: $('#inversion').value, inversionOn: $('#inversionOn').value,
    stut: $('#stut').value, stutOn: $('#stutOn').value,
    stutLoop: $('#stutLoop').value,
    scramble: $('#scramble').value, scrLen: $('#scrLen').value,
    miroir: $('#miroir').value, miroirOn: $('#miroirOn').value,
    ondul: $('#ondul').value, ondulOn: $('#ondulOn').value,
    mosaic: $('#mosaic').value, mosaicOn: $('#mosaicOn').value,
    kaleido: $('#kaleido').value, kaleidoOn: $('#kaleidoOn').value,
    cisaille: $('#cisaille').value, cisailleOn: $('#cisailleOn').value,
    coupure: $('#coupure').value, coupureOn: $('#coupureOn').value,
    tapestop: $('#tapestop').value, tapestopOn: $('#tapestopOn').value,
    tri: $('#tri').value, triOn: $('#triOn').value,
    rvb: $('#rvb').value, rvbOn: $('#rvbOn').value,
    retro: $('#retro').value, retroOn: $('#retroOn').value,
    macro: $('#macro').value, macroOn: $('#macroOn').value,
    tourbillon: $('#tourbillon').value, tourbillonOn: $('#tourbillonOn').value,
    bits: $('#bits').value, bitsOn: $('#bitsOn').value,
    tracking: $('#tracking').value, trackingOn: $('#trackingOn').value,
    echo: $('#echo').value, echoN: $('#echoN').value,
    echoDelay: $('#echoDelay').value, couleurs: $('#couleurs').value,
    spectro: $('#spectro').value,
    cadence: $('#cadence').value, poussiere: $('#poussiere').value,
    flottement: $('#flottement').value, haloDoux: $('#haloDoux').value,
    bdSharp: $('#bdSharp').value,
    nettete: $('#nettete').value, stepDiv: $('#stepDiv').value,
    taille: $('#taille').value, presence: $('#presence').value,
    neon: $('#neon').value, reflet: $('#reflet').value,
    tube: $('#tube').value, bgAnim: $('#bgAnim').value,
    // les effets places sur la frise, en blocs de temps
    effets: $('#effets').value,
    curve: $('#curve').checked ? '1' : '0', w: 960, h: 540,
  });
  return p;
}
let pending = null, PRESETS = {}, STYLES = {}, USINE = {}, AIDE = {}, COMPTE = {};
let QUALITES = {}, FRAPPES = null;

/* Combien de fois chaque effet partira sur ce morceau. C'est le chiffre qui
   manque le plus quand on regle : un effet pose sur le charley se declenche
   des milliers de fois, la ou la grosse caisse en compte quelques centaines. */
function majFrequences() {
  const duree = FRAPPES ? FRAPPES.duree : 0;
  for (const [id, genre] of Object.entries(COMPTE)) {
    const cible = $('#f-' + id);
    if (!cible) continue;
    const el = $('#' + id);
    const eteint = el && Math.abs(+el.value) < 1e-9;
    if (genre === 'qualite') {
      cible.textContent = QUALITES[$('#quality').value] || '';
      continue;
    }
    let txt = '';
    // la liste « sur : » de l'effet ; deux ne portent pas son nom suivi de On
    const AUTRE = {gridPulse: 'gridOn', bgFlash: 'flashOn'};
    const choix = genre === 'instrument' ? $('#' + (AUTRE[id] || id + 'On')) : null;
    if (eteint) {
      txt = 'éteint';
    } else if (choix && choix.value === 'continu') {
      txt = 'en continu, sur aucun instrument : tant que le curseur est monté'
          + ' — ou seulement dans ses blocs posés sur la frise';
    } else if (!FRAPPES) {
      txt = 'déposez un morceau pour connaître la fréquence';
    } else if (Array.isArray(genre)) {
      // un effet cable sur des familles fixes, sans selecteur
      const n = genre.reduce((a, f) => a + ((FRAPPES.frappes || {})[f] || 0), 0);
      txt = '~ ' + n + ' fois dans le morceau' + parMinute(n, duree)
          + '  (' + genre.join(' et ') + ')';
    } else if (genre === 'instrument') {
      const fam = choix ? choix.value : 'grosse caisse';
      const n = (FRAPPES.frappes || {})[fam] || 0;
      txt = '~ ' + n + ' fois dans le morceau' + parMinute(n, duree)
          + '  (' + fam + ')';
    } else if (genre === 'split') {
      const n = Math.min(+$('#splitCount').value,
                         1 + Math.floor(duree / Math.max(25, 0.14 * duree)));
      txt = '~ ' + n + ' fois dans le morceau';
    } else if (genre === 'qualite') {
      txt = QUALITES[$('#quality').value] || '';
    } else if (genre === 'sequenceur') {
      const pas = 60 / Math.max(1, FRAPPES.bpm) / +$('#stepDiv').value;
      txt = 'une case toutes les ' + Math.round(pas * 1000) + ' ms, soit '
          + Math.round(60 / pas) + ' par minute';
    } else if (genre === 'drops') {
      const n = (FRAPPES.drops || []).length;
      txt = '~ ' + n + ' fois dans le morceau  (les montées du morceau)';
    } else if (genre === 'tranche') {
      const blocs = Math.floor(duree / Math.max(0.04, +$('#scrLen').value));
      const part = +$('#scramble').value;
      txt = '~ ' + Math.round(blocs * part) + ' blocs brassés sur ' + blocs;
    } else {
      txt = 'en continu, du début à la fin';
    }
    cible.textContent = txt;
  }
}
function parMinute(n, duree) {
  if (!duree || n < 2) return '';
  return ', soit ' + (n / (duree / 60)).toFixed(0) + ' par minute';
}
function shot() {
  remplirCurseurs();
  majPoints();
  if (FRISE) FRISE.dessiner();
  if (LISTES_EX && EXEMPLES.size) for (const id of LISTES_EX) majExemple(id);
  // la couleur au choix ne sert qu'a « une couleur au choix »
  if ($('#libreBloc')) $('#libreBloc').hidden = $('#couleurCoups').value !== 'libre';
  // la couleur de l'encre ne sert qu'hors du neon
  if ($('#encreBloc')) $('#encreBloc').hidden = $('#modeTrait').value === 'neon';
  if (!track) return;
  // pendant l'ecoute, chaque image demandee relit les reglages du moment :
  // une image fixe en plus ne ferait que lui disputer le moteur
  if (ECOUTE && ECOUTE.actif()) return;
  rendreLImage();
  apercuARefaire = true;
  if (!apercuEnVol) { clearTimeout(pending); pending = setTimeout(calculerApercu, 25); }
}

/* L'apercu suit le reglage pendant qu'on le bouge : une seule image en calcul
   a la fois, et des qu'elle arrive, la suivante part avec les reglages du
   moment. Avant, chaque mouvement du curseur relancait l'attente : tant qu'on
   le faisait glisser, l'image ne changeait pas. Une image coute ~0,13 s en
   960x540 : on en voit ainsi six ou sept par seconde pendant le geste. */
let apercuEnVol = false, apercuARefaire = false, minuteurCalcul = null;
function calculerApercu() {
  if (!apercuARefaire || !track) return;
  apercuARefaire = false;
  apercuEnVol = true;
  const n = ++shotSeq;
  // l'image ne s'assombrit que si le calcul traine — la premiere d'un
  // morceau, ou un reglage qui reconstruit le moteur — : a chaque geste,
  // elle clignoterait
  clearTimeout(minuteurCalcul);
  minuteurCalcul = setTimeout(() => $('#shot').classList.add('calcul'), 450);
  // On passe par fetch plutot que par img.src : quand le serveur refuse,
  // une balise <img> ne donne qu'une image cassee, sans dire pourquoi.
  // les masques de l'apercu : la video de fond, la machine
  fetch('/still?' + MASQUES.appliquer(params()).toString()).then(async r => {
    if (r.status === 204) return;          // apercu abandonne, un autre arrive
    if (!r.ok) {
      let m = 'erreur ' + r.status;
      try { m = (await r.json()).error || m; } catch (e) { /* pas du JSON */ }
      throw new Error(m);
    }
    const url = URL.createObjectURL(await r.blob());
    const vieux = $('#shot').dataset.blob;
    if (vieux) URL.revokeObjectURL(vieux);
    $('#shot').dataset.blob = url;
    $('#shot').src = url;
    // la derniere image de l'ecoute restait affichee jusqu'a celle-ci
    if (!ECOUTE.actif()) ECOUTE.cacher();
    $('#ecranVide').hidden = true;
    $('#ecran').classList.remove('vide');
    $('#shoterr').classList.remove('on');
  }).catch(e => {
    if (n !== shotSeq) return;
    // la version est rappelee ici : un message rapporte sans elle ne dit pas
    // si la correction correspondante est deja installee ou non
    $('#shoterr').textContent = "L'aperçu n'a pas pu être calculé : "
      + e.message + "  [" + ($('#ver').textContent || "version inconnue") + "]";
    $('#shoterr').classList.add('on');
  }).finally(() => {
    clearTimeout(minuteurCalcul);
    $('#shot').classList.remove('calcul');
    apercuEnVol = false;
    if (apercuARefaire) calculerApercu();
  });
}

/* Tout ce qu'on depose sur l'apercu va a sa place : un morceau, une melodie,
   une image ou une video de fond. */
(function () {
  const ecran = $('#ecran');
  ecran.ondragover = e => { e.preventDefault(); ecran.classList.add('over'); };
  ecran.ondragleave = () => ecran.classList.remove('over');
  ecran.ondrop = async e => {
    e.preventDefault(); ecran.classList.remove('over');
    for (const f of Array.from(e.dataTransfer.files || [])) {
      const ext = (f.name.split('.').pop() || '').toLowerCase();
      if (['mid', 'midi'].includes(ext)) await sendMidi(f);
      else if (['jpg', 'jpeg', 'png', 'webp', 'gif', 'bmp', 'heic', 'mp4',
                'mov', 'webm', 'mkv', 'avi', 'm4v'].includes(ext))
        await sendBackdrop(f);
      else await upload(f);
    }
  };
  // tant qu'il n'y a pas de morceau, un clic sur l'ecran le fait choisir
  ecran.onclick = () => { if (ecran.classList.contains('vide')) $('#file').click(); };
})();

/* La poignee entre les reglages et la scene : on la tire pour elargir ou
   retrecir le panneau des reglages, un double-clic le remet a sa largeur.
   La largeur choisie est gardee pour la prochaine ouverture. */
(function () {
  const p = $('#poignee'), m = document.querySelector('main');
  if (!p) return;
  const CLE = 'omnipotard.largeurPanneau';
  // la scene garde toujours de quoi montrer l'apercu
  const borne = w => Math.max(320, Math.min(w, 760, window.innerWidth - 82 - 480));
  const poser = w => m.style.setProperty('--panneau', Math.round(borne(w)) + 'px');
  try { const w = +localStorage.getItem(CLE); if (w > 0) poser(w); } catch (e) {}
  let x0 = null, w0 = 0;
  p.addEventListener('pointerdown', e => {
    x0 = e.clientX; w0 = $('#panneau').getBoundingClientRect().width;
    p.setPointerCapture(e.pointerId); p.classList.add('tire'); e.preventDefault();
  });
  p.addEventListener('pointermove', e => { if (x0 !== null) poser(w0 + e.clientX - x0); });
  const lacher = () => {
    if (x0 === null) return;
    x0 = null; p.classList.remove('tire');
    try { localStorage.setItem(CLE, Math.round($('#panneau').getBoundingClientRect().width)); }
    catch (e) {}
  };
  p.addEventListener('pointerup', lacher);
  p.addEventListener('pointercancel', lacher);
  p.addEventListener('dblclick', () => {
    m.style.removeProperty('--panneau');
    try { localStorage.removeItem(CLE); } catch (e) {}
  });
  window.addEventListener('resize', () => {
    const v = parseFloat(m.style.getPropertyValue('--panneau'));
    if (v) poser(v);
  });
})();

/* ---------- reglages ---------- */
$('#palette').onchange = e => {
  $('#traitwrap').hidden = e.target.value !== 'perso';
  shot();
};
$('#bg').onchange = e => { $('#bgopts').hidden = e.target.value === 'noir'; shot(); };
for (const id of ['#trait','#bgColor','#curve']) $(id).oninput = shot;
$('#split').oninput  = e => { $('#v-split').textContent = (+e.target.value).toFixed(2); majFrequences(); shot(); };
$('#wobble').oninput = e => { $('#v-wob').textContent  = (+e.target.value).toFixed(2); majFrequences(); shot(); };
$('#trail').oninput  = e => { $('#v-trail').textContent= (+e.target.value).toFixed(2); majFrequences(); shot(); };
const bind = (id, out, dec) => { $(id).oninput = e => {
  $(out).textContent = dec ? (+e.target.value).toFixed(dec) : e.target.value;
  majFrequences(); shot(); }; };
bind('#splitCount','#v-sc',0); bind('#splitPx','#v-spx',0);
bind('#snare','#v-sn',2); bind('#wave','#v-wv',2); bind('#wavePunch','#v-wp',2);
bind('#waveSmooth','#v-ws',0);
bind('#bdStrength','#v-bds',2); bind('#bdClear','#v-bdc',2); bind('#screenDim','#v-sd',2);
bind('#glitch','#v-gl',2);
bind('#punch','#v-pu',3); bind('#shake','#v-sh',2); bind('#parts','#v-pa',2);
bind('#partsSpeed','#v-pas',2); bind('#partsLife','#v-pal',2);
// Le curseur porte la racine du nombre : lineaire, il aurait passe de 2000 a
// 30000 sur son dernier tiers et aurait ete inutilisable dans le bas.
$('#partsN').oninput = e => {
  const n = Math.round(e.target.value * e.target.value);
  $('#v-pan').textContent = n >= 1000 ? (n / 1000).toFixed(1) + ' k' : n;
  shot();
};
bind('#ring','#v-ri',2); bind('#gridPulse','#v-gp',2); bind('#bgFlash','#v-bf',2);
bind('#tranches','#v-tr',2); bind('#blocs','#v-bl',2); bind('#roll','#v-ro',2);
bind('#ghost','#v-gh',2); bind('#invert','#v-in',2); bind('#stut','#v-st',2);
bind('#stutLoop','#v-sl',2); bind('#miroir','#v-mi',2); bind('#ondul','#v-on',2);
bind('#mosaic','#v-mo',2); bind('#scramble','#v-sc2',2); bind('#scrLen','#v-srl',2);
bind('#bdSharp','#v-bdq',2); bind('#nettete','#v-net',2);
bind('#taille','#v-ta',2); bind('#presence','#v-pr',2);
bind('#neon','#v-ne',2); bind('#reflet','#v-re',2); bind('#tube','#v-tu',2);
bind('#bgAnim','#v-ba',2);
bind('#kaleido','#v-ka',2); bind('#cisaille','#v-ci',2);
bind('#coupure','#v-co',2); bind('#tapestop','#v-tp',2);
bind('#haloDoux','#v-hd',2); bind('#poussiere','#v-po',2);
bind('#flottement','#v-fl',2);
bind('#echo','#v-ec',2); bind('#echoN','#v-ecn',0);
bind('#echoDelay','#v-ecd',3); bind('#couleurs','#v-cl',2);
bind('#eclatPads','#v-ep',2); bind('#detourage','#v-det',2);
bind('#spectro','#v-sp',2);
bind('#inversion','#v-inv',2); bind('#papier','#v-pap',2);
bind('#tri','#v-tri',2); bind('#rvb','#v-rvb',2); bind('#retro','#v-ret',2);
bind('#macro','#v-mac',2); bind('#tourbillon','#v-tou',2); bind('#bits','#v-bit',2);
bind('#tracking','#v-trk',2);
$('#cadence').oninput = e => {
  const n = +e.target.value;
  $('#v-ca').textContent = n < 2 ? 'fluide' : Math.round(30 / n) + ' i/s';
  shot();
};
$('#travel').oninput = e => {
  $('#v-tv').textContent = Math.round(+e.target.value * 100) + ' %'; shot(); };
for (const id of ['#travelMode','#punchOn','#shakeOn','#partsOn','#ringOn',
                  '#gridOn','#flashOn','#splitOn','#tranchesOn','#blocsOn',
                  '#rollOn','#ghostOn','#invertOn','#inversionOn','#stutOn','#miroirOn',
                  '#ondulOn','#mosaicOn','#kaleidoOn','#cisailleOn',
                  '#coupureOn','#tapestopOn','#triOn','#rvbOn','#retroOn',
                  '#macroOn','#tourbillonOn','#bitsOn','#trackingOn','#stepDiv'])
  $(id).onchange = () => { majFrequences(); shot(); };
// la qualite ne change rien a l'apercu : elle ne touche que l'encodage
$('#quality').onchange = majFrequences;
// la lumiere des coups : sa couleur et la texture des touches
$('#couleurCoups').onchange = () => shot();
$('#textureTouches').onchange = () => shot();
$('#couleurCoupsLibre').oninput = () => shot();
// la nature du trait
$('#modeTrait').onchange = () => shot();
$('#encre').oninput = () => shot();
$('#inverser').onchange = () => shot();

/* ---- fonds : une image, une video, ou plusieurs a la suite ----
   Chaque fichier depose s'ajoute au bout de la liste ; l'ordre se change
   avec les fleches. Le moteur recoit les noms separes par « | ». */
let fonds = [];
const bdrop = $('#bdrop'), bdfile = $('#bdfile');
bdrop.onclick = () => bdfile.click();
bdrop.ondragover = e => { e.preventDefault(); bdrop.classList.add('over'); };
bdrop.ondragleave = () => bdrop.classList.remove('over');
bdrop.ondrop = e => { e.preventDefault(); bdrop.classList.remove('over');
                      deposerFonds(e.dataTransfer.files); };
bdfile.onchange = () => { deposerFonds(bdfile.files); bdfile.value = ''; };
$('#bdclear').onclick = () => { fonds = []; majFonds(); shot(); };
async function deposerFonds(liste) {
  for (const f of Array.from(liste || [])) await sendBackdrop(f);
}
function dureeTexte(d) {
  return d >= 60 ? Math.floor(d / 60) + ' min ' + Math.round(d % 60) + ' s'
                 : d.toFixed(d < 10 ? 1 : 0) + ' s';
}
function majFonds() {
  const ol = $('#bdliste');
  ol.innerHTML = '';
  fonds.forEach((f, i) => {
    const li = document.createElement('li');
    const nom = document.createElement('span');
    nom.textContent = f.name + ' ';
    const q = document.createElement('small');
    q.textContent = f.video ? 'vidéo ' + dureeTexte(f.duree || 0) : 'photo';
    nom.appendChild(q);
    li.appendChild(nom);
    for (const [txt, titre, act] of [['\u2191', 'passer avant', -1],
                                    ['\u2193', 'passer après', 1],
                                    ['\u2715', 'retirer de la liste', 0]]) {
      const b = document.createElement('button');
      b.className = 'ghost'; b.textContent = txt; b.title = titre;
      b.onclick = () => {
        if (act === 0) fonds.splice(i, 1);
        else {
          const j = i + act;
          if (j < 0 || j >= fonds.length) return;
          [fonds[i], fonds[j]] = [fonds[j], fonds[i]];
        }
        majFonds(); shot();
      };
      li.appendChild(b);
    }
    ol.appendChild(li);
  });
  const n = fonds.length, videos = fonds.some(f => f.video);
  ol.hidden = n === 0;
  $('#bdopts').hidden = n === 0;
  // la vitesse, la boucle et le fondu n'ont de sens qu'avec une video ou
  // plusieurs fichiers ; la duree d'une photo, qu'avec une suite
  $('#bdjeu').hidden = !(videos || n > 1);
  $('#bdphoto').hidden = !(n > 1 && fonds.some(f => !f.video));
  $('#bdname').textContent = n ? 'Ajouter une image ou une vidéo'
                               : 'Déposer une ou plusieurs images ou vidéos';
  FRISE.chargerFonds();
  majPoints();
  // sans fond, le bouton qui le masque n'a rien a masquer
  MASQUES.maj();
}
function vitesseFond() { return Math.pow(2, +$('#fondVitesse').value); }
$('#fondVitesse').oninput = () => {
  const v = vitesseFond();
  // le ralenti et l'accelere se disent au survol : la case est etroite
  $('#v-fv').textContent = v.toFixed(2) + ' \u00d7';
  $('#v-fv').parentNode.title = v < 0.97 ? 'ralenti' : v > 1.03 ? 'accéléré' : 'vitesse normale';
  FRISE.chargerFonds();
  shot();
};
$('#fondBoucle').onchange = () => { FRISE.chargerFonds(); shot(); };
$('#fondFondu').oninput = e => { $('#v-ff').textContent = (+e.target.value).toFixed(1) + ' s'; FRISE.chargerFonds(); shot(); };
$('#fondPhoto').oninput = e => { $('#v-fp').textContent = (+e.target.value).toFixed(1) + ' s'; FRISE.chargerFonds(); shot(); };

/* Un depot de fichier, avec sa progression.

   fetch() ne sait pas dire ou en est un envoi : sur une video de telephone,
   qui pese des centaines de megaoctets, la page restait muette une minute
   entiere puis affichait « Failed to fetch » sans rien expliquer. XHR, lui,
   rend compte de l'envoi au fur et a mesure, et distingue une reponse du
   studio d'une connexion perdue. */
function poids(n) { return (n / 1048576).toFixed(n > 10485760 ? 0 : 1) + ' Mo'; }

function deposer(url, f, quoi) {
  return new Promise((bon, mauvais) => {
    const x = new XMLHttpRequest();
    x.open('POST', url);
    x.setRequestHeader('X-Filename', f.name);
    x.upload.onprogress = e => {
      const pc = e.lengthComputable ? Math.round(e.loaded / e.total * 100) : 0;
      setStatus('envoi ' + quoi + ' ' + f.name + ' (' + poids(f.size) + ') — '
                + pc + ' %');
    };
    x.upload.onload = () => setStatus('le studio examine ' + f.name + '\u2026');
    x.onload = () => {
      let j = null;
      try { j = JSON.parse(x.responseText); } catch (e) { j = null; }
      if (j && j.error) mauvais(new Error(j.error));
      else if (!j) mauvais(new Error('le studio a répondu ' + x.status
                                     + ' sans explication'));
      else bon(j);
    };
    x.onerror = () => mauvais(new Error(
      'le studio n\'a pas répondu pendant l\'envoi (' + poids(f.size) + '). '
      + 'Le message exact est écrit dans la fenêtre noire du studio.'));
    x.onabort = () => mauvais(new Error('envoi interrompu'));
    x.send(f);
  });
}

/* le bloc commun nomme ainsi l'apercu, le bandeau et la duree du morceau */
const _redessine = () => shot();
const _etat = (m, e) => setStatus(m, e);
const _duree = () => duration;
// un pas de retour en arriere (Ctrl + Z), le meme que celui de la frise
const _memoriser = () => EFFETS.memoriser();
/*__SEQ_MIDI__*/

async function sendBackdrop(f) {
  try {
    const j = await deposer('/backdrop', f, 'du fond');
    fonds.push({name: j.name, video: !!j.video, duree: j.duree || 0});
    majFonds();
    setStatus(fonds.length > 1 ? 'fond ajouté à la suite (' + fonds.length
                                 + ' fichiers)' : 'fond en place');
    shot();
  } catch (e) { setStatus('fond refuse : ' + e.message, true); }
}
// la duree par defaut : les cases vides du sequenceur l'affichent en grise
$('#passage').oninput = e => {
  $('#v-psg').textContent = (+e.target.value).toFixed(2) + ' s';
  seqDessine();
  shot();
};
$('#passageTurb').oninput = e => { $('#v-psgt').textContent = (+e.target.value).toFixed(2); shot(); };
$('#midiForce').oninput = e => { $('#v-mif').textContent = (+e.target.value).toFixed(2); shot(); };
for (const [id, sp] of [['vignettage','v-vig'], ['scanlines','v-scl'],
                        ['aberration','v-abr']])
  $('#' + id).oninput = e => { $('#'+sp).textContent = (+e.target.value).toFixed(2); shot(); };
/* Ou tombe la premiere note dans la video, une fois le decalage applique.
   C'est le seul chiffre verifiable a l'oeil : on lance l'apercu a cet
   instant-la et on regarde si la touche s'allume avec le son. Le moteur
   calcule mt = t + depart + decalage, donc la note ecrite a `debut` dans le
   fichier tombe a `debut - depart - decalage` dans la video. */
function enSecMs(x, signe) {
  const neg = x < 0;
  let ms = Math.round(Math.abs(x) * 1000);
  const sec = Math.floor(ms / 1000);
  ms -= sec * 1000;
  const corps = !sec ? ms + ' ms' : (ms ? sec + ' s ' + ms + ' ms' : sec + ' s');
  if (!signe || (!sec && !ms)) return corps;
  return (neg ? '\u2212' : '+') + corps;
}
// la cadence de l'apercu anime : elle fixe la plus petite erreur de calage
// qu'on puisse y voir
const FPS_APERCU = 15;
/* Le decalage se lit en secondes et millisecondes : c'est dans cette unite
   qu'on constate un ecart — « la touche s'allume un poil apres le son ». Au
   centieme, 85 ms s'affichaient « 0.09 », et 5 ms de plus ne changeaient rien
   au chiffre. Le moteur calcule mt = t + decalage : un decalage positif
   allume donc les touches plus TOT, et la phrase le dit. */
function majOffset() {
  const d = +$('#midiOffset').value || 0;
  $('#v-mio').textContent = enSecMs(d, true);
  const sens = $('#midiSens');
  if (sens) sens.innerHTML = !Math.round(d * 1000)
    ? "les touches s'allument à l'heure du fichier"
    : "les touches s'allument <b>" + enSecMs(Math.abs(d)) + '</b> plus '
      + (d > 0 ? 'tôt' : 'tard') + ' que ne le dit le fichier';
  const ms = $('#midiMs');
  if (ms && document.activeElement !== ms) ms.value = Math.round(d * 1000);
  majImage();
  const ou = $('#midiOu');
  if (!ou) return;
  if (MIDI_DEBUT === null) { ou.innerHTML = '&nbsp;'; majDerive(); return; }
  const depart = +$('#start').value || 0;
  const t = MIDI_DEBUT - depart - d;
  // la longueur rendue, pas celle du morceau : le champ est vide quand on
  // rend tout, et le plan s'arrete alors a la fin du morceau
  const plan = +$('#dur').value || Math.max(0, (duration || 0) - depart);
  if (t < 0)
    ou.innerHTML = 'première note <b>' + instant(-t)
      + ' avant le début du plan</b> : on ne la verra pas';
  else if (plan > 0 && t > plan)
    ou.innerHTML = 'première note à <b>' + instant(t)
      + '</b>, soit après la fin du plan : on ne la verra pas';
  else
    ou.innerHTML = 'première note à <b>' + instant(t) + '</b> dans la vidéo';
  // la portee de la derive se compte depuis la premiere note : bouger le
  // calage la change
  majDerive();
}
$('#midiOffset').oninput = () => { majOffset(); shot(); };
/* Un ecart plus petit qu'une image ne se voit pas, et le chercher fait
   tourner en rond. L'apercu anime tourne a quinze images par seconde, la ou
   la video finale en a souvent trente. */
function majImage() {
  const z = $('#midiImage');
  if (!z) return;
  const fps = +$('#fps').value || 30;
  z.innerHTML = '1 image = <b>' + Math.round(1000 / fps) + ' ms</b> dans la vidéo ('
    + fps + ' i/s) et <b>' + Math.round(1000 / FPS_APERCU)
    + " ms</b> dans l'aperçu animé (" + FPS_APERCU
    + " i/s) : un écart plus petit ne s'y voit pas";
}
function reglerDecalage(v) {
  const el = $('#midiOffset');
  v = Math.min(+el.max, Math.max(+el.min, v));
  // au millieme : arrondir plus gros ferait deriver une suite de clics
  el.value = (Math.round(v * 1000) / 1000).toFixed(3);
  majOffset();
  shot();
}
$('#midiMs').oninput = () => {
  const ms = $('#midiMs');
  if (ms.value === '' || ms.value === '-') return;      // en cours de frappe
  const v = +ms.value;
  if (Number.isFinite(v)) reglerDecalage(v / 1000);
};
// en quittant le champ, il reprend la valeur retenue, bornee et arrondie
$('#midiMs').onchange = () => { $('#midiMs').blur(); majOffset(); };
$('#fps').addEventListener('change', majImage);
for (const b of document.querySelectorAll('#midiFin [data-ms]'))
  b.onclick = () => reglerDecalage((+$('#midiOffset').value || 0) + (+b.dataset.ms) / 1000);
for (const b of document.querySelectorAll('#midiFin [data-img]'))
  b.onclick = () => reglerDecalage((+$('#midiOffset').value || 0)
                                   + (+b.dataset.img) / (+$('#fps').value || 30));

/* ---------- la grille du morceau ----------
   La page demande au serveur ce que deviennent les notes du fichier sur ce
   morceau, et le dit en une phrase. Elle en garde l'instant de la premiere
   note, tel qu'il sera joue, et la duree exacte d'un temps, pour ses
   boutons. */
let TEMPS_MIDI = 0, BPM_PROPOSE = '', minuteurCalage = null;
function majCalage() {
  clearTimeout(minuteurCalage);
  minuteurCalage = setTimeout(async () => {
    FRISE.chargerNotes();
    const z = $('#midiGrille');
    if (!track || !$('#midi').value) {
      z.innerHTML = track ? '&nbsp;' : 'déposez le morceau : la mélodie se pose sur sa grille';
      TEMPS_MIDI = 0;
      return;
    }
    const q = new URLSearchParams({
      track, midi: $('#midi').value, bpm: $('#midiBpm').value,
      telQuel: $('#midiTelQuel').checked ? '1' : '0'});
    try {
      const j = await (await fetch('/calage?' + q)).json();
      if (j.error) { z.textContent = j.error; return; }
      z.textContent = j.raison + '.';
      TEMPS_MIDI = +j.temps || 0;
      MIDI_DEBUT = +j.debut;
      // la premiere note la ou elle sera jouee, et non la ou le fichier
      // l'ecrit : relu a un autre tempo, le fichier la mettait ailleurs
      $('#mi-c').textContent = instant(MIDI_DEBUT);
      majOffset();
    } catch (e) { z.innerHTML = '&nbsp;'; }
  }, 250);
}
/* Le tempo propose a l'arrivee d'un morceau : celui de sa grille. Il ne
   remplace pas un tempo tape a la main — seulement celui qu'on avait propose
   pour le morceau d'avant. */
function proposerBpm(bpm) {
  const b = $('#midiBpm');
  if (!(bpm > 0)) return;
  if (!b.value || b.value === BPM_PROPOSE) {
    b.value = (+bpm).toFixed(2);
    BPM_PROPOSE = b.value;
  }
}
for (const id of ['#midiBpm', '#midiTelQuel']) {
  $(id).addEventListener('change', () => { majCalage(); shot(); });
}
$('#midiBpm').addEventListener('input', majCalage);
// chercher le decalage tout seul change le calage : l'apercu et la frise suivent
$('#midiCale').onchange = () => { FRISE.chargerNotes(); shot(); };
/* La derive, dite en secondes plutot qu'en pourcent : c'est sous cette forme
   qu'on la constate — la melodie est calee au debut du plan et fausse a la
   fin. Le pourcent reste affiche parce que lui ne change pas quand on change
   la longueur du plan. */
function majDerive() {
  const el = $('#midiTempo');
  if (!el) return;
  const p = +el.value || 0;
  $('#v-mit').textContent = p.toFixed(3) + ' %';
  const ou = $('#midiDerive');
  if (!ou) return;
  if (MIDI_DEBUT === null || !p) { ou.innerHTML = '&nbsp;'; return; }
  // l'etirement part de la premiere note : c'est de la qu'on compte
  const depart = +$('#start').value || 0;
  const plan = +$('#dur').value || Math.max(0, (duration || 0) - depart);
  const t0 = MIDI_DEBUT - depart - (+$('#midiOffset').value || 0);
  const portee = Math.max(0, plan - Math.max(0, t0));
  const d = portee * p / 100;
  ou.innerHTML = 'la mélodie ' + (p > 0 ? 'retarde' : 'avance') + ' de <b>'
    + Math.abs(d).toFixed(3) + ' s</b> à la fin du plan';
}
$('#midiTempo').oninput = () => { majDerive(); shot(); };
$('#midiType').onchange = () => { midiAvis(); shot(); };
/* Mesurer la derive : on compare le pas de la grille du fichier a celui du
   morceau. Contrairement au calage, cette mesure-la est fiable sur une
   melodie — un motif repetitif dit tres bien l'ecart *entre* ses attaques,
   c'est seulement *laquelle* des mesures est la bonne qu'il ne dit pas. */
$('#midiMesure').onclick = async () => {
  if (!track) return setStatus('chargez d abord un morceau', true);
  if (!$('#midi').value) return setStatus('chargez d abord une mélodie', true);
  setStatus('mesure de la dérive...');
  try {
    const j = await (await fetch('/derive?track=' + track + '&midi='
                     + encodeURIComponent($('#midi').value))).json();
    if (j.error) return setStatus(j.error, true);
    const el = $('#midiTempo');
    // le curseur va par pas de 0,005 % : l'arrondi laisse au pire 0,0025 %,
    // soit six millisecondes sur quatre minutes
    const v = Math.min(+el.max, Math.max(+el.min,
              Math.round(j.pourcent / +el.step) * +el.step));
    el.value = v;
    majDerive();
    shot();
    setStatus('melodie ' + j.bpm_melodie.toFixed(2) + ' BPM, morceau '
      + j.bpm_morceau.toFixed(2) + ' BPM  ->  derive '
      + (j.pourcent >= 0 ? '+' : '') + j.pourcent.toFixed(3) + ' %'
      + (Math.abs(j.pourcent - v) > 0.0005
         ? ' (curseur posé à ' + v.toFixed(3) + ' %)' : '')
      + (j.nettete < 5 ? '  — mesure peu nette, vérifiez à l oreille' : '')
      // la mesure est un ecart entre attaques : elle se moyenne, donc elle
      // vaut ce que vaut la longueur analysee
      + (j.duree < 60 ? '  — extrait court (' + Math.round(j.duree)
         + ' s) : la mesure sera plus juste sur un plan plus long' : ''));
  } catch (e) { setStatus('mesure impossible : ' + e.message, true); }
};
/* Le depart et la duree deplacent la fenetre rendue, donc l'instant ou la
   premiere note y tombe : le rappel se refait. */
$('#start').oninput = () => { majOffset(); majDerive(); FRISE.dessiner(); };
$('#dur').oninput = () => { majOffset(); majDerive(); FRISE.dessiner(); };
// les dedoublements se placent selon leur nombre et leur instrument
$('#splitCount').addEventListener('change', () => FRISE.chargerDedo());
$('#splitOn').addEventListener('change', () => FRISE.chargerDedo());
/* Les boutons decalent d'un nombre entier de temps du morceau. La phase du
   fichier est presque toujours deja bonne — un MIDI exporte du meme projet
   tombe sur la grille — et ce qui manque est le nombre de temps. Bouger par
   temps entiers explore exactement cette inconnue sans jamais sortir de la
   grille, ce qu'un curseur au centieme de seconde ne sait pas faire. */
for (const b of document.querySelectorAll('#midiPas button')) {
  b.onclick = () => {
    // le temps exact du morceau, tel que la grille l'a mesure
    const bpm = +$('#midiBpm').value || (FRAPPES && FRAPPES.bpm) || 120;
    const temps = TEMPS_MIDI || 60 / Math.max(1, bpm);
    const el = $('#midiOffset');
    const v = (+el.value || 0) + (+b.dataset.pas) * temps;
    // pas d'arrondi ici : le curseur va au millieme, et arrondir au centieme
    // ajoutait cinq millisecondes d'erreur par clic — de quoi sortir de la
    // grille au bout d'une quinzaine
    el.value = Math.min(+el.max, Math.max(+el.min, v));
    majOffset();
    shot();
  };
}
$('#title').oninput  = shot;
$('#bgStrength').oninput = e => { $('#v-str').textContent = (+e.target.value).toFixed(2); shot(); };
$('#bgClear').oninput   = e => { $('#v-clr').textContent = (+e.target.value).toFixed(2); shot(); };
// pendant l'ecoute, le son saute avec : un clic sur la frise y mene aussi
$('#scrub').oninput     = () => { majTemps(); ECOUTE.chercher(+$('#scrub').value); shot(); };

/* Le dedoublement ne tombe que sur les 2 ou 3 plus gros coups de tout le
   morceau : sans ce bouton on peut chercher longtemps avant d'en voir un. */
$('#toSplit').onclick = async () => {
  if (!track) return;
  const j = await (await fetch('/splits?track=' + track +
                               '&count=' + $('#splitCount').value +
                               '&on=' + encodeURIComponent($('#splitOn').value))).json();
  const ts = j.times || [];
  if (!ts.length) return setStatus('aucun dédoublement sur ce morceau');
  const t = +$('#scrub').value;
  const next = ts.find(d => d > t + 0.2) ?? ts[0];
  allerA(next + 0.08);
  setStatus('dedoublement a ' + next.toFixed(1) + ' s  (tous : ' +
            ts.map(x => x.toFixed(1) + ' s').join(', ') + ')');
  shot();
};

$('#toDrop').onclick = () => {
  if (!drops.length) return setStatus('aucun paroxysme détecté sur ce morceau');
  const t = +$('#scrub').value;
  const next = drops.find(d => d > t + 0.2) ?? drops[0];
  allerA(next + 0.05);                  // juste apres, dans la rafale
  setStatus('paroxysme a ' + next.toFixed(1) + ' s');
  shot();
};
$('#toPrev').onclick = () => {
  if (!drops.length) return setStatus('aucun paroxysme détecté sur ce morceau');
  const t = +$('#scrub').value;
  const avant = drops.filter(d => d < t - 0.2);
  const prec = avant.length ? avant[avant.length - 1] : drops[drops.length - 1];
  allerA(prec + 0.05);
  setStatus('paroxysme a ' + prec.toFixed(1) + ' s');
  shot();
};
/* Un coup de grosse caisse pris au hasard : un instant tire au sort, puis le
   coup le plus proche. Le serveur ecarte ceux qui dedoublent le trait, qui
   blanchissent toute l'image. */
$('#hi').onclick = async () => {
  if (!track) return;
  const t0 = Math.random() * Math.max(1, duration - 2);
  let t = t0;
  try {
    const j = await (await fetch('/instant_vignette?track=' + track + '&t=' + t0.toFixed(2)
      + '&splitOn=' + encodeURIComponent($('#splitOn').value)
      + '&splitCount=' + $('#splitCount').value)).json();
    if (typeof j.t === 'number') t = j.t;
  } catch (e) { /* l'instant tire au sort fera l'affaire */ }
  allerA(t);
  shot();
};

/* ---------- apercu en mouvement ----------

   Une image fixe ne dit rien de ce qui bouge. Plutot que de rejouer des
   images une par une — chacune coute plus d'un dixieme de seconde, on ne
   verrait qu'un diaporama — on calcule pour de bon un court extrait, dans le
   meme moteur et avec les memes reglages que le rendu final, et on le joue
   en boucle. */
let clipTimer = null;

function reglagesDuClip() {
  const secondes = +$('#clipDur').value;
  const depart = Math.max(0, Math.min(+$('#scrub').value,
                                      Math.max(0, duration - secondes)));
  // les masques valent aussi pour l'extrait : c'est un apercu
  const body = Object.fromEntries(MASQUES.appliquer(params()));
  delete body.t; delete body.w; delete body.h;
  Object.assign(body, {
    track, start: depart, duration: secondes,
    // assez grand pour juger, assez petit pour ne pas faire attendre
    width: 960, height: 540, fps: 15, apercu: true,
    curve: $('#curve').checked,
  });
  return body;
}

$('#lire').onclick = async () => {
  if (!track) return;
  // l'extrait a son propre son : l'ecoute s'efface devant lui
  ECOUTE.arreter(false);
  clearTimeout(clipTimer);
  // on rend la main a l'image fixe pendant le calcul : laisser l'ancien
  // extrait tourner ferait croire que rien ne se passe
  rendreLImage();
  $('#lire').disabled = true;
  $('#clipprog').hidden = false;
  $('#cbar').style.width = '0%';
  $('#ctext').textContent = 'préparation\u2026';
  const extrait = reglagesDuClip();
  FRISE.clip(extrait.start, extrait.duration);
  try {
    const r = await fetch('/render', {method: 'POST',
                                      body: JSON.stringify(extrait)});
    const j = await r.json();
    if (j.error) throw new Error(j.error);
    clipId = j.id;
    $('#clipStop').disabled = false;
    suivreClip(j.id);
  } catch (e) {
    setStatus('lecture impossible : ' + e.message, true);
    $('#lire').disabled = false; $('#clipprog').hidden = true;
    FRISE.clip(null);
  }
};

let clipId = null;
$('#clipStop').onclick = async () => {
  if (!clipId) return;
  $('#clipStop').disabled = true;
  try { await fetch('/stop?id=' + clipId); } catch (e) {}
};
function suivreClip(id) {
  clipTimer = setTimeout(async () => {
    let j;
    try {
      j = await (await fetch('/job?id=' + id)).json();
    } catch (e) { return suivreClip(id); }
    if (j.state === 'erreur') {
      setStatus('lecture impossible : ' + j.error, true);
      $('#lire').disabled = false; $('#clipprog').hidden = true;
      FRISE.clip(null);
      return;
    }
    if (j.state === 'arrete') {
      setStatus('aperçu arrêté');
      $('#lire').disabled = false; $('#clipprog').hidden = true;
      FRISE.clip(null);
      return;
    }
    if (j.state === 'fini') {
      const v = $('#clip');
      v.src = '/download?inline=1&id=' + id;
      v.hidden = false; $('#shot').hidden = true;
      ECOUTE.cacher();
      $('#clipprog').hidden = true; $('#lire').disabled = false;
      // le son demande parfois un geste de l'utilisateur : a defaut on joue
      // sans, plutot que de laisser une image arretee
      v.play().catch(() => { v.muted = true; v.play().catch(() => {}); });
      FRISE.suivre(v);
      setStatus('lecture en boucle — bougez un réglage pour revenir à l\'image');
      return;
    }
    const pc = j.total ? j.done / j.total * 100 : 0;
    $('#cbar').style.width = pc.toFixed(1) + '%';
    $('#ctext').textContent = j.state === 'rendu'
      ? j.done + '/' + j.total + ' images' : j.state + '\u2026';
    suivreClip(id);
  }, 500);
}

/* Toute nouvelle image fixe reprend la place de la video : sans cela on
   croirait regler dans le vide, la video restant affichee telle quelle. */
function rendreLImage() {
  const v = $('#clip');
  if (!v.hidden) { v.pause(); v.hidden = true; v.removeAttribute('src'); }
  $('#shot').hidden = false;
  if (FRISE) {
    FRISE.suivre(null);
    // l'extrait encore en calcul reste marque sur la frise
    if ($('#clipprog').hidden) FRISE.clip(null);
  }
}

/* ---------- exemples ----------
   Sous chaque effet, une image du moteur, prise la ou l'effet se voit le
   plus ; elle s'anime au survol (au toucher sur un telephone). Pour une
   liste — palette, fond, machine, couleur des coups, texture —, l'exemple
   suit le choix. Les exemples sont fabriques en tache de fond la premiere
   fois qu'une version demarre : la page les pose au fur et a mesure. */
// « var » : l'apercu peut etre demande avant que ces lignes ne soient lues
var EXEMPLES = new Set(), minuteurEx = null;
var LISTES_EX = ['machine', 'palette', 'bg', 'couleurCoups',
                 'textureTouches', 'travelMode', 'modeTrait', 'inverser'];
function cleExemple(id) {
  return LISTES_EX.includes(id) ? id + '=' + $('#' + id).value : id;
}
function figureExemple(id) {
  let f = document.querySelector('figure.ex[data-id="' + id + '"]');
  if (f) return f;
  const el = $('#' + id);
  if (!el) return null;
  // sous l'explication du reglage, apres la liste « sur quoi » s'il en a une
  // (une case a cocher est dans son libelle : on part du libelle)
  let a = el.type === 'checkbox' ? (el.closest('label') || el) : el;
  if (a.nextElementSibling && a.nextElementSibling.matches('select.inst'))
    a = a.nextElementSibling;
  if (a.nextElementSibling && a.nextElementSibling.classList.contains('aide'))
    a = a.nextElementSibling;
  f = document.createElement('figure');
  f.className = 'ex';
  f.dataset.id = id;
  f.hidden = true;
  f.innerHTML = '<img alt="exemple" loading="lazy">'
    + '<figcaption>survoler pour voir bouger</figcaption>';
  // dans la ligne du reglage : la bulle du « i » le montre
  const ctl = el.closest('.ctl');
  if (ctl) ctl.appendChild(f); else a.insertAdjacentElement('afterend', f);
  const img = f.querySelector('img');
  const jouer = oui => {
    f.classList.toggle('joue', oui);
    img.src = '/exemple?' + (oui ? 'anim=1&' : '') + 'cle='
      + encodeURIComponent(f.dataset.cle);
  };
  f.onmouseenter = () => jouer(true);
  f.onmouseleave = () => jouer(false);
  f.onclick = () => jouer(!f.classList.contains('joue'));
  return f;
}
function majExemple(id) {
  const cle = cleExemple(id);
  const f = figureExemple(id);
  if (!f || f.dataset.cle === cle) return;
  f.dataset.cle = cle;
  f.classList.remove('joue');
  f.hidden = !EXEMPLES.has(cle);
  if (!f.hidden) f.querySelector('img').src = '/exemple?cle=' + encodeURIComponent(cle);
}
async function majExemples() {
  try {
    const j = await (await fetch('/exemples')).json();
    EXEMPLES = new Set(j.prets);
    const ids = new Set(j.prets.map(c => c.split('=')[0]));
    for (const id of ids) {
      const f = figureExemple(id);
      if (f) f.dataset.cle = '';      // a reposer : il vient peut-etre d'arriver
      majExemple(id);
    }
    clearTimeout(minuteurEx);
    if (j.en_cours) minuteurEx = setTimeout(majExemples, 6000);
  } catch (e) {}
}
for (const id of LISTES_EX) {
  const el = $('#' + id);
  if (el) el.addEventListener('change', () => majExemple(id));
}

/* ---------- rendu ---------- */
$('#go').onclick = async () => {
  const [w, h] = $('#size').value.split('x').map(Number);
  // Le rendu part exactement des reglages de l'apercu. Les recopier a la main
  // laissait dehors, sans rien dire, tout effet ajoute depuis : on voyait une
  // chose a l'ecran et on en recevait une autre dans le fichier.
  const body = Object.fromEntries(params());
  delete body.t; delete body.w; delete body.h;
  Object.assign(body, {
    track, start: +$('#start').value || 0,
    duration: $('#dur').value ? +$('#dur').value : null,
    width: w, height: h, fps: +$('#fps').value,
    quality: $('#quality').value,
    curve: $('#curve').checked,      // '0' serait vrai cote python
  });
  $('#go').disabled = true; $('#done').hidden = true; $('#prog').hidden = false;
  $('#pbar').style.width = '0%'; $('#ptext').textContent = 'préparation…';
  const r = await fetch('/render', {method:'POST', body: JSON.stringify(body)});
  const j = await r.json();
  if (j.error) { setStatus('echec : ' + j.error, true); $('#go').disabled = false; return; }
  renduId = j.id;
  $('#stop').disabled = false;
  watch(j.id);
};
/* Arreter : le moteur s'arrete au prochain paquet d'images, une ou deux
   secondes au plus, et efface le fichier commence. */
let renduId = null;
$('#stop').onclick = async () => {
  if (!renduId) return;
  $('#stop').disabled = true;
  $('#ptext').textContent = 'arrêt en cours\u2026';
  try { await fetch('/stop?id=' + renduId); }
  catch (e) { $('#stop').disabled = false; }
};

/* L'avancement du rendu se lit aussi dans le titre de l'onglet : on le suit
   depuis une autre fenetre, ou la colonne de droite defilee plus bas. */
const TITRE = document.title;
function titreRendu(t) {
  document.title = t ? t + ' \u2014 ' + TITRE : TITRE;
  const e = $('#exporter span');
  if (e) e.textContent = !t || /termin/.test(t) ? 'Exporter'
                                                : t.charAt(0).toUpperCase() + t.slice(1);
}
function watch(id) {
  clearInterval(jobTimer);
  jobTimer = setInterval(async () => {
    const j = await (await fetch('/job?id=' + id)).json();
    if (j.state === 'erreur') {
      clearInterval(jobTimer); $('#prog').hidden = true; titreRendu('');
      setStatus('échec du rendu : ' + j.error, true); $('#go').disabled = false;
      return;
    }
    if (j.state === 'fini') {
      // la barre et « Arreter » s'effacent : il ne reste que le fichier
      clearInterval(jobTimer); $('#prog').hidden = true;
      $('#dl').href = '/download?id=' + id;
      $('#dl').setAttribute('download', j.name);
      $('#donepath').textContent = 'écrit dans out/studio/' + j.name +
        ' (' + (j.size / 1048576).toFixed(1) + ' Mo)';
      $('#done').hidden = false; $('#go').disabled = false;
      titreRendu('rendu terminé');
      setStatus('rendu terminé — d\'autres allures à essayer dans l\'onglet Styles');
      return;
    }
    if (j.state === 'arrete') {
      clearInterval(jobTimer); $('#prog').hidden = true; $('#go').disabled = false;
      titreRendu('');
      setStatus('rendu arrêté : le fichier commencé a été effacé');
      return;
    }
    const pc = j.total ? j.done / j.total * 100 : 0;
    $('#pbar').style.width = pc.toFixed(1) + '%';
    titreRendu(j.state === 'rendu' ? 'rendu ' + Math.floor(pc) + ' %' : 'rendu');
    $('#ptext').textContent = j.state === 'rendu'
      ? j.done + '/' + j.total + ' images — encore ' + fmt(j.eta)
      : j.state === 'arret' ? 'arrêt en cours\u2026' : j.state + '…';
  }, 700);
}

/* La version du code effectivement charge : un studio laisse ouvert continue
   de servir l'ancien moteur apres un git pull, et on cherche longtemps
   pourquoi une nouveaute « n'est pas la ». */
fetch('/config').then(r => r.json())
  .then(c => {
    if (c.version) $('#ver').textContent = c.version;
    if (c.perime) setStatus('mise à jour installée : fermez la fenêtre noire '
      + 'du studio, relancez-le, puis rechargez cette page', true);
    // Les instruments et les sens de travelling viennent du moteur : la page
    // n'en garde pas sa propre copie, qui finirait par diverger.
    // Les noms que le moteur compare restent sans accent ; la page les
    // affiche accentues.
    const AFFICHE = {'arriere': 'arrière', 'medium': 'médium',
                     'bas medium': 'bas médium', 'haut medium': 'haut médium',
                     'tres aigus': 'très aigus', 'continu': 'aucun (en continu)'};
    const affiche = v => AFFICHE[v] || v;
    const remplir = (sel, liste, choisi) => {
      $(sel).innerHTML = liste.map(
        v => '<option value="' + v + '"' + (v === choisi ? ' selected' : '')
             + '>' + (sel === '#travelMode' ? affiche(v) : 'sur : ' + affiche(v))
             + '</option>'
      ).join('');
    };
    /* Les declencheurs arrivent groupes : instruments, bandes de frequences,
       hasard, parts. Sans ces groupes la liste ferait quarante lignes a plat
       et plus personne n'y trouverait rien. */
    const echap = v => v.replace(/&/g, '&amp;').replace(/</g, '&lt;')
                        .replace(/"/g, '&quot;');
    const remplirGroupes = (sel, groupes, choisi) => {
      $(sel).innerHTML = groupes.map(g =>
        '<optgroup label="' + echap(g.titre) + '">' + g.noms.map(
          v => '<option value="' + echap(v) + '"'
               + (v === choisi ? ' selected' : '') + '>sur : ' + echap(affiche(v))
               + '</option>').join('') + '</optgroup>').join('');
    };
    /* Par defaut, aucun effet ne part sur la batterie : la liste s'ouvre sur
       « aucun » — l'effet est la tant qu'il est monte, ou seulement dans ses
       blocs de la frise —, et l'on choisit un instrument si on le veut. Le
       dedoublement seul garde la grosse caisse : il se choisit parmi les
       plus gros coups, il lui faut des coups. */
    const groupes = c.declencheurs || [];
    remplirGroupes('#splitOn', groupes.filter(g => !g.noms.includes('continu')),
                   'grosse caisse');
    for (const sel of ['#punchOn', '#shakeOn', '#partsOn', '#ringOn', '#gridOn',
                       '#flashOn', '#tranchesOn', '#blocsOn', '#rollOn',
                       '#ghostOn', '#invertOn', '#inversionOn', '#stutOn',
                       '#miroirOn', '#ondulOn', '#mosaicOn', '#kaleidoOn',
                       '#cisailleOn', '#coupureOn', '#tapestopOn',
                       '#triOn', '#rvbOn', '#retroOn', '#macroOn',
                       '#tourbillonOn', '#bitsOn', '#trackingOn'])
        remplirGroupes(sel, groupes, 'continu');
    remplir('#travelMode', c.travellings || [], 'avant');
    // chaque qualite dit en clair ce qu'elle coute et ce qu'elle rend
    $('#machine').innerHTML = (c.machines || []).map(
      m => '<option value="' + m.cle + '">' + m.nom + ' \u2014 ' + m.quoi
           + '</option>').join('');
    $('#machine').onchange = () => { seqEcrire(); seqDessine(); shot(); };
    seqDessine();
    QUALITES = c.qualites || {};
    $('#quality').innerHTML = Object.keys(QUALITES).map(
      k => '<option value="' + k + '">' + k + '</option>').join('');

    /* ---- une phrase sous chaque reglage, et sa frequence ---- */
    AIDE = c.aide || {}; COMPTE = c.compte || {};
    // Sous l'apercu la place est comptee : l'explication du rendu et du
    // curseur d'instant passe en bulle, au survol. La qualite garde sa ligne,
    // qui dit ce que donne celle qui est choisie.
    const EN_BULLE = ['scrub', 'size', 'fps', 'quality'];
    for (const [id, phrase] of Object.entries(AIDE)) {
      const el = $('#' + id);
      if (!el) continue;
      if (EN_BULLE.includes(id)) {
        const bulle = phrase.replace(/<[^>]*>/g, '');
        el.title = bulle;
        const lab = document.querySelector('label[for="' + id + '"]');
        if (lab) lab.title = bulle;
        if (!(id in COMPTE)) continue;
        const d = document.createElement('div');
        d.className = 'aide';
        d.innerHTML = '<b class="freq" id="f-' + id + '"></b>';
        // la ligne de la qualite tient toute la largeur de la carte
        if (id === 'quality') $('#carteRendu').appendChild(d);
        else el.parentNode.insertBefore(d, el.nextSibling);
        continue;
      }
      // Le texte se pose apres le selecteur d'instrument quand celui-ci suit
      // immediatement le curseur, pour que le bloc « effet + instrument +
      // explication » reste solidaire. Exiger le voisinage direct evite de
      // rattacher un selecteur qui se trouve plus bas dans la meme carte.
      // deux selecteurs ne portent pas le nom de leur curseur suivi de « On »
      const AUTRE = {gridPulse: 'gridOn', bgFlash: 'flashOn'};
      const inst = $('#' + (AUTRE[id] || id + 'On'));
      // une case a cocher est dans son libelle : l'explication se pose apres
      // lui, sans quoi elle en prenait les capitales et cassait la ligne
      const apres = (inst && el.nextElementSibling === inst) ? inst
                  : (el.type === 'checkbox' && el.closest('label')) || el;
      const d = document.createElement('div');
      d.className = 'aide';
      d.innerHTML = phrase + '<b class="freq" id="f-' + id + '"></b>';
      // dans la ligne du reglage, derriere son « i »
      const ctl = el.closest('.ctl');
      if (ctl) { ctl.appendChild(d); boutonAide(ctl); continue; }
      apres.parentNode.insertBefore(d, apres.nextSibling);
    }
    majFrequences();
    majExemples();
    FRISE.noms(Object.fromEntries((c.machines || []).map(m => [m.cle, m.nom])));
    // les effets que le moteur sait limiter a un passage : chacun sa poignee
    EFFETS.placables(c.placables || {}, c.fondu_effet);

    /* ---- prereglages : ils reposent tous les curseurs d'un coup ---- */
    PRESETS = c.presets || {};
    STYLES = c.styles || {};
    MES = c.mes || {};
    USINE = {};                       // les valeurs d'usine, pour y revenir
    for (const el of document.querySelectorAll('input[type=range], select'))
      if (el.id) USINE[el.id] = el.value;
    listeDesPrereglages();
    // les points du rail se comptent depuis ces valeurs-la
    ORIGINE = valeursPage();
    majPoints();
    $('#preset').onchange = () => {
      appliquerPrereglage($('#preset').value);
      majBoutonsPreset();
      majFrequences();
      shot();
    };
  })
  .catch(() => {});

/* ---------- prereglages, ceux d'usine et les votres ----------

   Les deux passent par la meme table : des identifiants de curseurs et leurs
   valeurs. Un prereglage d'usine ne dit que l'essentiel et laisse le reste
   revenir a l'usine ; un reglage enregistre, lui, est une photographie
   complete de la page. */
let MES = {};

function listeDesPrereglages() {
  const groupe = (titre, noms) => !noms.length ? '' :
    '<optgroup label="' + titre + '">' + noms.map(
      k => '<option value="' + echapHtml(k) + '">' + echapHtml(k)
           + '</option>').join('') + '</optgroup>';
  const choisi = $('#preset').value;
  $('#preset').innerHTML = groupe("Fournis", Object.keys(PRESETS))
                         + groupe("Mes réglages", Object.keys(MES).sort());
  if (choisi) $('#preset').value = choisi;
  majBoutonsPreset();
}
function echapHtml(v) {
  return String(v).replace(/&/g, '&amp;').replace(/</g, '&lt;')
                  .replace(/"/g, '&quot;');
}
function majBoutonsPreset() {
  $('#presetDel').disabled = !(($('#preset').value || '') in MES);
}

function appliquerPrereglage(nom) {
  const p = PRESETS[nom] || MES[nom] || {};
  for (const [id, v] of Object.entries(USINE)) {
    // un prereglage d'usine ne dit pas tout : ce qu'il tait revient a
    // l'usine, sinon deux prereglages enchaines se melangeraient
    // le fichier de sortie n'est pas une affaire de style : un
    // prereglage n'a pas a rabaisser une 4K choisie en 1080p
    if (['preset', 'bg', 'backdrop', 'size', 'fps', 'quality', 'clipDur']
        .includes(id)) continue;
    const el = $('#' + id);
    if (el) { el.value = v; el.dispatchEvent(new Event('input')); }
  }
  for (const [id, v] of Object.entries(p)) {
    const el = $('#' + id);
    if (!el) { console.warn('prereglage : curseur inconnu', id); continue; }
    if (el.type === 'checkbox') {
      el.checked = !!v && v !== 'false';
    } else {
      // le nombre d'etincelles est porte par sa racine
      el.value = (id === 'partsN') ? Math.round(Math.sqrt(+v)) : v;
    }
    el.dispatchEvent(new Event(el.tagName === 'SELECT' ? 'change' : 'input'));
  }
}

/* ---------- styles ----------

   Un style ne passe pas par appliquerPrereglage : celui-ci remet d'abord tout
   a l'usine, ce qui effacerait les reactions, la machine et la melodie qu'on
   vient de regler. Un style ne pose que ce qu'il dit. */
function appliquerStyle(nom) {
  const st = STYLES[nom];
  if (!st) return;
  for (const [id, v] of Object.entries(st.reglages)) {
    const el = $('#' + id);
    if (!el) { console.warn('style : curseur inconnu', id); continue; }
    el.value = v;
    el.dispatchEvent(new Event(el.tagName === 'SELECT' ? 'change' : 'input'));
  }
  majFrequences();
  shot();
  setStatus('style « ' + nom + ' » posé par-dessus tes réglages');
}

/* L'instant des vignettes, choisi par le serveur : un coup de grosse caisse
   ordinaire. Sur un instant quelconque les styles reactifs ne montraient rien
   (5 % de l'image changee) ; sur les plus gros coups, le dedoublement du
   trait blanchissait tout (46 % de pixels blancs). Un coup ordinaire montre
   les deux : la reaction, sans l'eblouissement. */
async function instantDesVignettes() {
  const t0 = +$('#scrub').value || 0;
  try {
    const j = await (await fetch('/instant_vignette?track=' + track + '&t=' + t0
      + '&splitOn=' + encodeURIComponent($('#splitOn').value)
      + '&splitCount=' + $('#splitCount').value)).json();
    if (typeof j.t === 'number') return j.t;
  } catch (e) { /* on retombe sur l'instant regarde */ }
  return t0;
}

let stylesGen = 0;
function montrerVue(v) {
  for (const b of document.querySelectorAll('#vues button'))
    b.classList.toggle('on', b.dataset.vue === v);
  document.querySelector('main').hidden = v !== 'studio';
  $('#styles').hidden = v !== 'styles';
  window.scrollTo(0, 0);
}
async function ouvrirStyles() {
  montrerVue('styles');
  const gen = ++stylesGen;
  const g = $('#stylesGrille');
  for (const im of g.querySelectorAll('img'))
    if (im.dataset.blob) URL.revokeObjectURL(im.dataset.blob);
  g.innerHTML = '';
  if (!track || !Object.keys(STYLES).length) {
    g.innerHTML = '<p class="hint">Déposez d\'abord un morceau dans l\'onglet '
      + 'Studio : les vignettes des styles sont prises sur lui.</p>';
    return;
  }
  const cartes = {};
  for (const [nom, st] of Object.entries(STYLES)) {
    const c = document.createElement('div');
    c.className = 'st';
    c.innerHTML = '<div class="vide">calcul de la vignette…</div>'
      + '<div class="txt"><b></b><p></p></div>'
      + '<div class="act"><button class="ghost">Appliquer</button>'
      + '<button>Rendre avec</button></div>';
    c.querySelector('b').textContent = nom;
    c.querySelector('p').textContent = st.quoi;
    const [app, ren] = c.querySelectorAll('button');
    app.onclick = () => { appliquerStyle(nom); fermerStyles(); };
    ren.onclick = () => { appliquerStyle(nom); fermerStyles(); $('#go').click(); };
    g.appendChild(c);
    cartes[nom] = c;
  }
  const t = await instantDesVignettes();
  // une par une : le moteur dessine une image a la fois, et les lancer
  // ensemble ne ferait que les mettre en file
  for (const [nom, st] of Object.entries(STYLES)) {
    if (gen !== stylesGen || $('#styles').hidden) return;
    const p = params();
    for (const [id, v] of Object.entries(st.reglages)) p.set(id, v);
    p.set('t', t); p.set('w', 480); p.set('h', 270); p.set('vignette', '1');
    const place = cartes[nom].querySelector('.vide');
    try {
      const r = await fetch('/still?' + p.toString());
      if (!r.ok) throw new Error('erreur ' + r.status);
      const url = URL.createObjectURL(await r.blob());
      if (gen !== stylesGen) { URL.revokeObjectURL(url); return; }
      const im = document.createElement('img');
      im.src = url; im.dataset.blob = url; im.alt = 'style ' + nom;
      place.replaceWith(im);
    } catch (e) {
      place.textContent = 'vignette impossible : ' + e.message;
    }
  }
}
function fermerStyles() { montrerVue('studio'); stylesGen++; }
$('#stylesFerme').onclick = fermerStyles;
for (const b of document.querySelectorAll('#vues button'))
  b.onclick = () => b.dataset.vue === 'styles' ? ouvrirStyles() : fermerStyles();
document.addEventListener('keydown', e => {
  if (e.key === 'Escape' && !$('#styles').hidden) fermerStyles();
});

/* Tout ce qui decrit l'allure, et rien de ce qui decrit le fichier : la
   definition, la cadence, la duree d'apercu et le morceau n'ont rien a faire
   dans un reglage qu'on rappelle six mois plus tard. */
function reglagesActuels() {
  const sauf = new Set(['preset', 'presetNom', 'size', 'fps', 'quality',
                        'clipDur', 'scrub', 'start', 'dur']);
  const out = {};
  for (const el of document.querySelectorAll(
         'input[type=range], input[type=color], input[type=text], select')) {
    if (!el.id || sauf.has(el.id)) continue;
    out[el.id] = el.value;
  }
  // meme convention que les prereglages d'usine : le nombre d'etincelles,
  // pas la racine que porte le curseur
  out.partsN = String(Math.round($('#partsN').value * $('#partsN').value));
  out.curve = $('#curve').checked;
  return out;
}

async function ecrireReglages(corps) {
  const r = await fetch('/reglages', {method: 'POST',
                                      body: JSON.stringify(corps)});
  const j = await r.json();
  if (j.error) throw new Error(j.error);
  MES = j.mes || {};
  listeDesPrereglages();
  return j;
}

$('#presetSave').onclick = async () => {
  const nom = ($('#presetNom').value || '').trim();
  if (!nom) { setStatus('donnez un nom à ce réglage', true); return; }
  try {
    await ecrireReglages({nom: nom, valeurs: reglagesActuels()});
    $('#preset').value = nom;
    majBoutonsPreset();
    $('#presetNom').value = '';
    setStatus('« ' + nom + ' » enregistre');
  } catch (e) { setStatus('pas enregistre : ' + e.message, true); }
};

$('#presetDel').onclick = async () => {
  const nom = $('#preset').value;
  if (!(nom in MES)) return;
  try {
    await ecrireReglages({action: 'supprimer', nom: nom});
    setStatus('« ' + nom + ' » efface');
  } catch (e) { setStatus('pas effacé : ' + e.message, true); }
};

/* ---------- divers ---------- */
function fmt(s) {
  s = Math.max(0, Math.round(s));
  return s >= 60 ? Math.floor(s / 60) + ' min ' + String(s % 60).padStart(2,'0') + ' s'
                 : s + ' s';
}
function setStatus(t, bad) {
  const el = $('#status'); el.textContent = t; el.className = bad ? 'err' : '';
  el.title = t;
}

/* ---------- l'habillage : icones, sections, bulles, puces ---------- */

/* Les icones de la page, dessinees au trait : des chemins SVG plutot que des
   fichiers, la page reste d'un seul tenant. */
var ICONES = {
  logo: '<path d="M2 12h3l2-5 3 10 3-13 3 10 2-4h4"/>',
  prereglage: '<path d="M6 3h12v18l-6-4-6 4z"/>',
  machine: '<rect x="3" y="6" width="18" height="12" rx="2"/><circle cx="8" cy="12" r="2"/><path d="M13 10h5M13 14h5"/>',
  lumiere: '<path d="M13 2 4 14h7l-1 8 9-12h-7z"/>',
  couleur: '<path d="M12 3c4 5 6 8 6 11a6 6 0 0 1-12 0c0-3 2-6 6-11z"/>',
  dalle: '<rect x="3" y="3" width="18" height="18" rx="2"/><path d="M3 9h18M3 15h18M9 3v18M15 3v18"/>',
  fonds: '<rect x="3" y="5" width="18" height="14" rx="2"/><circle cx="9" cy="10" r="1.6"/><path d="m21 16-5-5-8 8"/>',
  trait: '<path d="M3 17c4-9 7-9 9-3s5 7 9-5"/>',
  reactions: '<path d="M2 12h4l2-6 4 12 3-9 2 3h5"/>',
  avaries: '<path d="M4 6h9M9 10h11M4 14h7M11 18h9"/>',
  echo: '<path d="m12 3 9 5-9 5-9-5z"/><path d="m3 13 9 5 9-5"/>',
  texture: '<path d="M5 5h.01M10 5h.01M15 5h.01M19 5h.01M7 9h.01M12 9h.01M17 9h.01M5 13h.01M10 13h.01M15 13h.01M19 13h.01M7 17h.01M12 17h.01M17 17h.01M5 20h.01M15 20h.01" stroke-width="2.6"/>',
  rendu: '<path d="M12 3v12M7 10l5 5 5-5M4 20h16"/>',
  morceau: '<path d="M9 18V5l11-2v13"/><circle cx="6" cy="18" r="3"/><circle cx="17" cy="16" r="3"/>',
  melodie: '<rect x="3" y="4" width="18" height="16" rx="2"/><path d="M8 4v9M12 4v16M16 4v9"/>',
  play: '<path d="M7 4.5v15l12-7.5z" fill="currentColor" stroke="none"/>',
  pause: '<rect x="6" y="4.5" width="4" height="15" rx="1" fill="currentColor" stroke="none"/><rect x="14" y="4.5" width="4" height="15" rx="1" fill="currentColor" stroke="none"/>',
  film: '<rect x="3" y="5" width="18" height="14" rx="2"/><path d="M7.5 5v14M16.5 5v14M3 9.7h4.5M3 14.3h4.5M16.5 9.7H21M16.5 14.3H21"/>',
  poignee: '<g fill="currentColor" stroke="none"><circle cx="9" cy="5.5" r="1.8"/><circle cx="15" cy="5.5" r="1.8"/><circle cx="9" cy="12" r="1.8"/><circle cx="15" cy="12" r="1.8"/><circle cx="9" cy="18.5" r="1.8"/><circle cx="15" cy="18.5" r="1.8"/></g>',
  precedent: '<path d="M19 5 9 12l10 7zM5 5v14"/>',
  suivant: '<path d="m5 5 10 7-10 7zM19 5v14"/>',
  dedoublement: '<path d="M3 13c3-6 6-6 8-2s5 4 8-2"/><path d="M5 18c3-6 6-6 8-2s5 4 8-2" opacity=".55"/>',
  hasard: '<rect x="4" y="4" width="16" height="16" rx="3"/><circle cx="9" cy="9" r="1.2" fill="currentColor"/><circle cx="15" cy="9" r="1.2" fill="currentColor"/><circle cx="9" cy="15" r="1.2" fill="currentColor"/><circle cx="15" cy="15" r="1.2" fill="currentColor"/>',
};
function icone(k) {
  return '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.7"'
    + ' stroke-linecap="round" stroke-linejoin="round" aria-hidden="true">'
    + (ICONES[k] || '') + '</svg>';
}
for (const i of document.querySelectorAll('i.ic[data-ic]')) i.innerHTML = icone(i.dataset.ic);

/* ---- les sections : une seule ouverte a la fois, retrouvee a la
   prochaine ouverture ---- */
var CLE_SECTION = 'omnipotard.section';
function montrerSection(k) {
  if (!document.querySelector('#panneau .section[data-section="' + k + '"]'))
    k = 'prereglage';
  for (const s of document.querySelectorAll('#panneau .section'))
    s.classList.toggle('actif', s.dataset.section === k);
  for (const b of document.querySelectorAll('#rail button'))
    b.classList.toggle('actif', b.dataset.section === k);
  $('#panneau').scrollTop = 0;
  BULLE.fermer();
  try { localStorage.setItem(CLE_SECTION, k); } catch (e) {}
}
for (const b of document.querySelectorAll('#rail button[data-section]'))
  b.onclick = () => montrerSection(b.dataset.section);
$('#toutZero').onclick = () => EFFETS.toutAZero();
// le « ? » d'une section deplie ses explications
for (const q of document.querySelectorAll('.section .q')) {
  q.onclick = () => {
    const ex = q.closest('.section').querySelector('.explications');
    if (!ex) return;
    ex.hidden = !ex.hidden;
    q.classList.toggle('ouvert', !ex.hidden);
  };
}

/* ---- les curseurs : la part deja parcourue, peinte en bleu ---- */
function remplir(inp) {
  const lo = +inp.min || 0, hi = inp.max === '' ? 100 : +inp.max;
  const p = Math.max(0, Math.min(1, (+inp.value - lo) / ((hi - lo) || 1)));
  inp.style.setProperty('--p', (p * 100).toFixed(2) + '%');
}
function remplirCurseurs() {
  for (const inp of document.querySelectorAll('input[type=range]')) remplir(inp);
}
// en capture : les evenements que posent les prereglages ne remontent pas
document.addEventListener('input', e => {
  if (e.target.type === 'range') remplir(e.target);
}, true);
remplirCurseurs();

/* ---- un point sur les sections qui s'ecartent de l'usine ----
   On voit d'un coup d'oeil ou il se passe quelque chose : une avarie
   allumee, un fond, une melodie. */
var ORIGINE = null, minuteurPoints = 0;
function valeursPage() {
  const v = {};
  for (const el of document.querySelectorAll('#panneau input, #panneau select'))
    if (el.id) v[el.id] = el.type === 'checkbox' ? el.checked : el.value;
  return v;
}
function majPoints() {
  if (minuteurPoints) return;
  minuteurPoints = requestAnimationFrame(() => {
    minuteurPoints = 0;
    for (const s of document.querySelectorAll('#panneau .section')) {
      const k = s.dataset.section;
      let mod = false;
      if (k === 'fonds') mod = fonds.length > 0;
      else if (k === 'melodie') mod = !!$('#midi').value;
      else if (ORIGINE) {
        for (const el of s.querySelectorAll('input, select')) {
          if (!el.id || !(el.id in ORIGINE) || el.type === 'text'
              || el.type === 'number' || el.type === 'file') continue;
          const v = el.type === 'checkbox' ? el.checked : el.value;
          mod = el.type === 'range' ? Math.abs(+v - +ORIGINE[el.id]) > 1e-9
                                    : v !== ORIGINE[el.id];
          if (mod) break;
        }
      }
      const b = document.querySelector('#rail button[data-section="' + k + '"]');
      if (b) b.classList.toggle('modif', mod);
    }
  });
}

/* ---- la bulle d'explication ----
   Une seule pour toute la page, posee au-dessus de tout : rangee dans le
   panneau, elle y aurait ete coupee par son defilement. Elle s'ouvre au
   survol du « i » et reste ouverte au clic (au doigt sur un ecran tactile). */
var BULLE = (() => {
  const b = $('#bulle');
  let ancre = null, fige = false;
  function placer() {
    if (!ancre || b.hidden) return;
    const r = ancre.getBoundingClientRect(), W = b.offsetWidth, H = b.offsetHeight;
    let x = r.right + 12, y = r.top - 12;
    if (x + W > innerWidth - 8) x = r.left - W - 12;
    if (x < 8) { x = Math.max(8, Math.min(r.left, innerWidth - W - 8)); y = r.bottom + 8; }
    y = Math.max(8, Math.min(y, innerHeight - H - 8));
    b.style.left = Math.round(x) + 'px';
    b.style.top = Math.round(y) + 'px';
  }
  function ouvrir(el, html, image, figer) {
    if (ancre && ancre !== el) ancre.classList.remove('ouvert');
    ancre = el; fige = !!figer;
    b.innerHTML = html + (image ? '<img alt="exemple" src="' + image + '">' : '');
    b.classList.toggle('fige', fige);
    b.hidden = false;
    el.classList.add('ouvert');
    placer();
    const im = b.querySelector('img');
    if (im) im.onload = placer;
  }
  function fermer() {
    if (ancre) ancre.classList.remove('ouvert');
    ancre = null; fige = false;
    b.hidden = true; b.innerHTML = '';
  }
  function attacher(el, contenu) {
    const montrer = figer => {
      const c = contenu();
      if (c) ouvrir(el, c[0], c[1], figer);
    };
    el.addEventListener('mouseenter', () => { if (!fige) montrer(false); });
    el.addEventListener('mouseleave', () => { if (!fige && ancre === el) fermer(); });
    el.addEventListener('focus', () => { if (!fige) montrer(false); });
    el.addEventListener('blur', () => { if (!fige && ancre === el) fermer(); });
    el.addEventListener('click', e => {
      e.stopPropagation();
      if (fige && ancre === el) fermer(); else montrer(true);
    });
  }
  document.addEventListener('click', e => { if (fige && !b.contains(e.target)) fermer(); });
  document.addEventListener('keydown', e => { if (e.key === 'Escape' && !b.hidden) fermer(); });
  window.addEventListener('resize', placer);
  $('#panneau').addEventListener('scroll', () => { if (fige) placer(); else fermer(); });
  return {ouvrir, fermer, attacher};
})();

/* Le « i » d'un reglage : sa phrase, sa frequence sur ce morceau, et
   l'exemple anime quand il est pret. */
function boutonAide(ctl) {
  if (ctl.querySelector('.ctl-i')) return;
  const b = document.createElement('button');
  b.type = 'button'; b.className = 'ctl-i'; b.textContent = 'i';
  b.setAttribute('aria-label', 'explication');
  ctl.appendChild(b);
  BULLE.attacher(b, () => {
    const aide = ctl.querySelector('.aide');
    if (!aide) return null;
    const lab = ctl.querySelector('label');
    const f = ctl.querySelector('figure.ex');
    const img = f && !f.hidden && f.dataset.cle
      ? '/exemple?anim=1&cle=' + encodeURIComponent(f.dataset.cle) : '';
    return ['<p class="titre">' + echapHtml(lab ? lab.textContent.trim() : '')
            + '</p>' + aide.innerHTML, img];
  });
}
BULLE.attacher($('#apercuAide'), () => [$('#aideApercu').innerHTML, '']);

/* ---- les fichiers charges : une puce a la place de la zone de depot ---- */
function puceFichier(el, ic, nom, action) {
  el.classList.add('charge');
  el.innerHTML = '<i class="ic">' + icone(ic) + '</i><b></b><span class="rempl"></span>';
  el.querySelector('b').textContent = nom;
  el.querySelector('.rempl').textContent = action;
  el.title = nom;
}
function majPuce(j) {
  $('#puce').classList.remove('vide');
  $('#puce').title = 'changer de morceau';
  $('#puceNom').textContent = j.name;
  $('#puceDet').textContent = j.bpm.toFixed(1) + ' BPM · ' + fmt(j.duration);
}
$('#puce').onclick = () => $('#file').click();
$('#midiCaler').onclick = () => montrerSection('melodie');
$('#midiChoisir').onclick = () => $('#midifile').click();

/* ---- l'export, depuis l'en-tete : le meme bouton que la carte ---- */
$('#exporter').onclick = () => $('#go').click();
new MutationObserver(() => { $('#exporter').disabled = $('#go').disabled; })
  .observe($('#go'), {attributes: true, attributeFilter: ['disabled']});

/* ---- l'instant regarde ---- */
function tc(s) {
  const d = Math.max(0, Math.round((+s || 0) * 10));
  const m = Math.floor(d / 600), r = (d - m * 600) / 10;
  return m + ':' + r.toFixed(1).padStart(4, '0');
}
function majTemps() {
  const t = +$('#scrub').value || 0;
  $('#v-t').textContent = tc(t);
  $('#v-fin').textContent = tc(duration);
  $('#frise').setAttribute('aria-valuenow', String(Math.round(t)));
  $('#frise').setAttribute('aria-valuetext', tc(t));
  FRISE.tetes();
}
// un saut vers un instant : la frise le garde en vue, et le son y saute
// pendant l'ecoute
function allerA(t) {
  const s = $('#scrub');
  s.value = Math.max(0, Math.min(+s.max, t));
  majTemps();
  FRISE.montrer(+s.value);
  ECOUTE.chercher(+s.value);
}

/* ---------- la frise : le morceau d'un coup d'oeil ----------

   Une seule toile sous l'apercu : la regle, le son en trois bandes de
   frequences avec les paroxysmes et les dedoublements, le plan des
   machines, les notes de la melodie la ou le moteur les jouera, et la suite
   des fonds. Un clic ou un glisser y place l'apercu. Ctrl + molette zoome
   autour du pointeur, Maj + molette fait defiler ; au clavier, les fleches
   avancent d'une seconde (Maj : dix), + et - zooment.

   Le decor (pistes, son, notes) est peint une fois dans une toile cachee ;
   seules les tetes de lecture et le survol se repeignent a chaque image,
   ce qui permet de suivre la lecture de l'apercu anime sans rien couter. */
var FRISE = (() => {
  const zone = $('#frise'), toile = $('#friseToile'), info = $('#friseInfo');
  const ctx = toile.getContext('2d');
  const couche = document.createElement('canvas'), cctx = couche.getContext('2d');
  const ETI = 88, MARGE = 10, VIDE = 96;
  const HAUT = {regle: 22, son: 58, machines: 26, melodie: 40, fonds: 26};
  const css = getComputedStyle(document.documentElement);
  const C = k => css.getPropertyValue(k).trim();
  const COUL = {
    fond: C('--bg2'), eti: '#0b0e12', lig: C('--lig'), tx: C('--tx'), tx2: C('--tx2'),
    tx3: C('--tx3'), acc: C('--acc'), parox: C('--parox'), dedo: C('--dedo'),
    note: C('--note'), fondC: C('--fond'),
    // les trois bandes du son, des graves aux aigus
    bandes: [['rgba(37,99,235,.9)', 1.0], ['rgba(56,189,248,.85)', .78],
             ['rgba(224,242,254,.85)', .5]],
  };
  // chaque machine sa teinte : fond, bord, texte
  const MACH = {mpc: ['#16324a', '#2b5878', '#dbeafe'],
                minifreak: ['#143425', '#2a6a4a', '#d1fae5'],
                sp404: ['#2a1f45', '#5b3f8a', '#ede9fe'],
                digitakt: ['#3a2a12', '#7a5a22', '#fef3c7']};
  const NOTES = ['do', 'do♯', 'ré', 'ré♯', 'mi', 'fa', 'fa♯', 'sol', 'sol♯', 'la', 'la♯', 'si'];
  let onde = null, dedo = [], notes = null, noteAuto = 0, noteGrille = true, plan = null;
  let noms = {}, v0 = 0, v1 = 1, largeur = 0, haut = VIDE, dpr = 1, pistes = [];
  let prise = false, survol = null, clip = null, video = null, rafVideo = 0;
  let attente = 0, sale = true;
  // la grille des temps (les blocs d'effets s'y aimantent), le bloc fantome
  // d'un effet qu'on apporte du panneau, le bloc qu'on deplace ou etire
  let grille = null, fantome = null, geste = null;
  // le changement de machine qu'on etire ou deplace, celui sous la souris
  let gesteM = null, survolM = null;
  let mDedo = 0, mNotes = 0, mFonds = 0, nNotes = 0, nFonds = 0;

  const total = () => duration || 0;
  const utile = () => Math.max(1, largeur - ETI - MARGE);
  const x = t => ETI + (t - v0) / ((v1 - v0) || 1) * utile();
  const t = px => v0 + (px - ETI) / utile() * (v1 - v0);
  const piste = cle => pistes.find(p => p.cle === cle);
  const nomNote = h => NOTES[((h % 12) + 12) % 12] + (Math.floor(h / 12) - 1);

  function dessiner() { sale = true; demander(); }
  // la vue a bouge sous la souris immobile : la bulle de la frise suit
  function resurvoler() {
    if (survol) { survol.t = Math.max(0, Math.min(total(), t(survol.px))); majInfo(); }
  }
  function tetes() { demander(); }
  function demander() { if (!attente) attente = requestAnimationFrame(peindre); }

  // la piste des effets places : une rangee par chevauchement ; seule, une
  // rangee prend toute la hauteur, et y ecrit deux lignes
  function hautEffets() {
    const n = EFFETS.rangs();
    return n <= 2 ? 44 : Math.min(6 + n * 19, 120);
  }
  // les pistes du moment, et la taille de la toile
  function mesurer() {
    pistes = [{cle: 'regle', h: HAUT.regle}];
    if (track) {
      pistes.push({cle: 'son', nom: 'Son', h: HAUT.son});
      pistes.push({cle: 'effets', nom: 'Effets', h: hautEffets()});
      pistes.push({cle: 'machines', nom: 'Machines', h: HAUT.machines});
      if ($('#midi').value && notes && notes.length)
        pistes.push({cle: 'melodie', nom: 'Mélodie', h: HAUT.melodie});
      if (fonds.length && plan && plan.blocs.length)
        pistes.push({cle: 'fonds', nom: 'Fonds', h: HAUT.fonds});
    }
    let y = 0;
    for (const p of pistes) { p.y = y; y += p.h; }
    const h = track ? y : VIDE;
    if (h !== haut) { haut = h; zone.style.height = h + 'px'; }
    const w = zone.clientWidth, d = window.devicePixelRatio || 1;
    if (w !== largeur || d !== dpr || toile.height !== Math.round(h * d)) {
      largeur = w; dpr = d;
      toile.width = couche.width = Math.max(1, Math.round(w * d));
      toile.height = couche.height = Math.max(1, Math.round(h * d));
      sale = true;
    }
    const T = total();
    if (T > 0 && (v1 <= v0 || v1 - v0 > T)) { v0 = 0; v1 = T; }
  }

  function peindre() {
    attente = 0;
    mesurer();
    $('#friseVide').hidden = !!track;
    if (sale) { peindreDecor(cctx); sale = false; }
    ctx.setTransform(1, 0, 0, 1, 0, 0);
    ctx.clearRect(0, 0, toile.width, toile.height);
    ctx.drawImage(couche, 0, 0);
    ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
    if (track && total()) peindreTetes(ctx);
    // « tout retirer », sous le nom de la piste des effets
    const pe = piste('effets'), vider = $('#effetsVider');
    vider.hidden = !pe || !EFFETS.liste().length;
    if (pe) vider.style.top = (pe.y + 23) + 'px';
    // le panneau du bloc choisi suit son bloc quand la vue bouge
    EFFETS.placerInsp();
  }

  // ---- le decor : tout ce qui ne bouge pas avec la tete de lecture
  function peindreDecor(c) {
    c.setTransform(1, 0, 0, 1, 0, 0);
    c.clearRect(0, 0, couche.width, couche.height);
    c.setTransform(dpr, 0, 0, dpr, 0, 0);
    if (!track || !total()) return;
    c.fillStyle = COUL.eti;
    c.fillRect(0, 0, ETI, haut);
    c.fillStyle = COUL.lig;
    c.fillRect(ETI - 1, 0, 1, haut);
    for (const p of pistes) {
      c.fillStyle = p.cle === 'regle' ? COUL.lig : 'rgba(255,255,255,.045)';
      c.fillRect(0, p.y + p.h - 1, largeur, 1);
    }
    c.save();
    c.beginPath(); c.rect(ETI, 0, largeur - ETI, haut); c.clip();
    peindreRegle(c, piste('regle'));
    peindreSon(c, piste('son'));
    peindreFondEffets(c, piste('effets'));
    peindreMachines(c, piste('machines'));
    if (piste('melodie')) peindreNotes(c, piste('melodie'));
    if (piste('fonds')) peindreFonds(c, piste('fonds'));
    peindreExport(c);
    c.restore();
    // les noms des pistes, et la legende des trois bandes
    c.font = '500 10.5px ' + C('--f');
    c.textBaseline = 'middle';
    for (const p of pistes) {
      if (!p.nom) continue;
      c.fillStyle = COUL.tx2;
      c.fillText(p.nom, 12, p.cle === 'son' || p.cle === 'effets' ? p.y + 13 : p.y + p.h / 2);
    }
    const son = piste('son');
    if (son) {
      c.font = '400 9.5px ' + C('--f');
      ['graves', 'médiums', 'aigus'].forEach((n, i) => {
        const y = son.y + 27 + i * 11;
        c.fillStyle = COUL.bandes[i][0];
        c.fillRect(12, y - 3, 6, 6);
        c.fillStyle = COUL.tx3;
        c.fillText(n, 23, y);
      });
    }
  }

  function pasDeRegle() {
    const parS = utile() / ((v1 - v0) || 1);
    for (const p of [0.5, 1, 2, 5, 10, 15, 30, 60, 120, 300, 600])
      if (p * parS >= 64) return p;
    return 1200;
  }
  function etiquette(s, p) {
    const m = Math.floor(s / 60), r = s - 60 * m;
    return m + ':' + (p < 1 ? r.toFixed(1).padStart(4, '0') : String(Math.round(r)).padStart(2, '0'));
  }
  function peindreRegle(c, p) {
    const pas = pasDeRegle(), fin = Math.min(total(), v1);
    c.font = '400 10px ' + C('--m');
    c.textBaseline = 'middle';
    const fin2 = fin + 1e-6;
    for (let s = Math.ceil(v0 / (pas / 5)) * (pas / 5); s <= fin2; s += pas / 5) {
      const majeur = Math.abs(s / pas - Math.round(s / pas)) < 1e-6;
      const xx = Math.round(x(s)) + 0.5;
      c.fillStyle = majeur ? COUL.lig : 'rgba(255,255,255,.06)';
      c.fillRect(xx, p.y + (majeur ? 12 : 16), 1, majeur ? p.h - 12 : p.h - 16);
      if (majeur) {
        c.fillStyle = COUL.tx3;
        c.fillText(etiquette(Math.round(s * 10) / 10, pas), xx + 4, p.y + 8);
      }
    }
  }

  function peindreSon(c, p) {
    const mid = p.y + p.h / 2, demi = p.h / 2 - 5;
    if (!onde) {
      c.fillStyle = COUL.tx3; c.font = '400 11px ' + C('--f');
      c.fillText('lecture du son…', ETI + 12, mid);
      return;
    }
    const n = onde.n, ps = onde.par_s, x0 = ETI, x1 = Math.min(largeur - MARGE, x(total()));
    // la ligne du silence
    c.fillStyle = 'rgba(56,189,248,.25)';
    c.fillRect(x0, mid, x1 - x0, 1);
    for (let b = 0; b < 3; b++) {
      const v = onde.bandes[b], [coul, ech] = COUL.bandes[b], h = [];
      for (let px = x0; px < x1; px++) {
        const i0 = Math.max(0, Math.floor(t(px) * ps));
        const i1 = Math.min(n, Math.max(i0 + 1, Math.floor(t(px + 1) * ps)));
        // la moyenne quadratique des colonnes du pixel : le maximum ne
        // gardait que les coups de charley, et tout le morceau faisait un mur
        let m = 0;
        for (let i = i0; i < i1; i++) m += v[i] * v[i];
        h.push(Math.sqrt(m / Math.max(1, i1 - i0)) / 255 * demi * ech);
      }
      c.beginPath();
      c.moveTo(x0, mid);
      h.forEach((a, k) => c.lineTo(x0 + k + 0.5, mid - a));
      for (let k = h.length - 1; k >= 0; k--) c.lineTo(x0 + k + 0.5, mid + h[k]);
      c.closePath();
      c.fillStyle = coul;
      c.fill();
    }
    // les paroxysmes : la ou tombent les glitchs
    c.fillStyle = COUL.parox;
    for (const d of drops) {
      if (d < v0 - 1 || d > v1 + 1) continue;
      const xx = Math.round(x(d));
      c.globalAlpha = .9;
      c.fillRect(xx - 1, p.y + 3, 2, p.h - 6);
      c.globalAlpha = 1;
      c.beginPath(); c.moveTo(xx - 4, p.y + 2); c.lineTo(xx + 4, p.y + 2); c.lineTo(xx, p.y + 7);
      c.fill();
    }
    // les dedoublements du trait : un losange en haut
    c.fillStyle = COUL.dedo;
    for (const s of dedo) {
      if (s < v0 - 1 || s > v1 + 1) continue;
      const xx = x(s), yy = p.y + p.h - 7;
      c.beginPath(); c.moveTo(xx, yy - 5); c.lineTo(xx + 4, yy); c.lineTo(xx, yy + 5);
      c.lineTo(xx - 4, yy); c.closePath(); c.fill();
    }
  }

  // Le plan des machines : celle du debut, puis les changements du
  // sequenceur — les objets memes, que les gestes de la frise modifient. Lu
  // comme le moteur le lit : deux changements au meme instant, le dernier
  // l'emporte ; la meme machine deux fois de suite ne change rien.
  function planMachines() {
    const p = [{t: 0, m: $('#machine').value, d: null}];
    for (const e of SEQ) {
      const der = p[p.length - 1];
      if (Math.abs(e.t - der.t) < 1e-6) p[p.length - 1] = e;
      else if (e.m !== der.m) p.push(e);
    }
    return p;
  }
  // la deformation qui mene au changement i : sa duree a lui, sinon celle du
  // curseur — jamais plus que l'intervalle depuis le changement d'avant,
  // comme dans le moteur
  function dureeChangement(pl, i) {
    const d = pl[i].d != null ? pl[i].d : (+$('#passage').value || 0);
    return Math.max(0, Math.min(d, pl[i].t - pl[i - 1].t));
  }
  function bloc(c, a, b, y, h, f, bord) {
    const xa = Math.max(ETI - 4, x(a)) + 1, xb = Math.min(largeur + 4, x(b)) - 1;
    if (xb - xa < 1) return null;
    c.beginPath();
    if (c.roundRect) c.roundRect(xa, y, xb - xa, h, 5); else c.rect(xa, y, xb - xa, h);
    c.fillStyle = f; c.fill();
    c.strokeStyle = bord; c.lineWidth = 1; c.stroke();
    return [xa, xb];
  }
  function texte(c, s, xa, xb, y, coul) {
    if (xb - xa < 24) return;
    c.save();
    c.beginPath(); c.rect(xa + 6, y - 8, xb - xa - 12, 16); c.clip();
    c.fillStyle = coul; c.fillText(s, Math.max(xa, ETI) + 8, y);
    c.restore();
  }
  function peindreMachines(c, p) {
    const plan = planMachines(), T = total();
    c.font = '500 11px ' + C('--f');
    c.textBaseline = 'middle';
    plan.forEach((e, i) => {
      const a = e.t, b = i + 1 < plan.length ? plan[i + 1].t : T;
      const m = MACH[e.m] || MACH.mpc;
      const r = bloc(c, a, b, p.y + 4, p.h - 8, m[0], m[1]);
      if (r) texte(c, noms[e.m] || e.m, r[0], r[1], p.y + p.h / 2, m[2]);
    });
    // la deformation d'une machine a l'autre precede l'instant inscrit ; son
    // bord gauche est une poignee (bleue quand le changement a sa propre
    // duree) : on l'etire a la souris
    for (let i = 1; i < plan.length; i++) {
      const b = plan[i].t, a = b - dureeChangement(plan, i);
      const xa = x(a), xb = x(b);
      if (xb - xa >= 1) {
        const g = c.createLinearGradient(xa, 0, xb, 0);
        g.addColorStop(0, 'rgba(255,255,255,0)');
        g.addColorStop(1, 'rgba(255,255,255,.22)');
        c.fillStyle = g;
        c.fillRect(xa, p.y + 4, xb - xa, p.h - 8);
      }
      c.fillStyle = plan[i].d != null ? COUL.acc : 'rgba(255,255,255,.5)';
      c.fillRect(Math.round(xa) - 1, p.y + 6, 2, p.h - 12);
    }
  }

  // les notes telles que le moteur les jouera : posees par le serveur, puis
  // decalees et etirees comme les curseurs de la page le demandent
  function notesPosees() {
    if (!notes || !notes.length) return [];
    const off = noteAuto + (+$('#midiOffset').value || 0);
    const tempo = noteGrille ? 1 : 1 + (+$('#midiTempo').value || 0) / 100;
    let d0 = Infinity;
    for (const n of notes) if (n[0] < d0) d0 = n[0];
    return notes.map(n => {
      const deb = d0 + (n[0] - d0) * tempo, fin = d0 + (n[1] - d0) * tempo;
      return [deb - off, Math.min(fin, deb + 1.2) - off, n[2], n[3]];
    });
  }
  function peindreNotes(c, p) {
    const ns = notesPosees();
    let lo = 127, hi = 0;
    for (const n of ns) { if (n[2] < lo) lo = n[2]; if (n[2] > hi) hi = n[2]; }
    const ecart = Math.max(1, hi - lo), h = p.h - 10;
    c.fillStyle = COUL.note;
    for (const n of ns) {
      if (n[1] < v0 || n[0] > v1) continue;
      const xa = x(n[0]), xb = Math.max(xa + 1.5, x(n[1]));
      const yy = p.y + 4 + (1 - (n[2] - lo) / ecart) * h;
      c.globalAlpha = .4 + .6 * Math.max(0, Math.min(1, n[3]));
      c.fillRect(xa, yy, xb - xa, 2);
    }
    c.globalAlpha = 1;
    p.notes = ns; p.lo = lo; p.ecart = ecart;
  }

  function peindreFonds(c, p) {
    c.font = '500 11px ' + C('--f');
    c.textBaseline = 'middle';
    for (const [a, b, k, sens] of plan.blocs) {
      const r = bloc(c, a, b, p.y + 4, p.h - 8, k % 2 ? '#2a2246' : '#231d3b', '#4c3f7a');
      const f = fonds[k];
      if (r && f) texte(c, (sens < 0 ? '◂ ' : '') + f.name, r[0], r[1], p.y + p.h / 2, '#e9e3ff');
    }
    for (const [a, b] of plan.fondus) {
      const xa = x(a), xb = x(b);
      if (xb - xa < 1) continue;
      const g = c.createLinearGradient(xa, 0, xb, 0);
      g.addColorStop(0, 'rgba(196,181,253,.05)');
      g.addColorStop(.5, 'rgba(196,181,253,.35)');
      g.addColorStop(1, 'rgba(196,181,253,.05)');
      c.fillStyle = g;
      c.fillRect(xa, p.y + 4, xb - xa, p.h - 8);
    }
  }

  // ---- la piste des effets places
  // l'unite d'aimantation : le temps, ou une fraction de temps quand on a
  // zoome (90 px au plus entre deux crans), ou plusieurs temps — une mesure,
  // deux — quand on voit tout le morceau (6 px au moins)
  function unite() {
    const parS = utile() / ((v1 - v0) || 1);
    let u = grille.temps;
    while (u * parS > 90 && u > grille.temps / 4 + 1e-9) u /= 2;
    while (u * parS < 6 && u < grille.temps * 64) u *= 2;
    return u;
  }
  // Un instant pose sur la grille du morceau ; sans grille sure, au dixieme.
  // Alt (libre) le laisse ou il est, au centieme ; `auTemps` le pose sur le
  // temps le plus proche, quel que soit le zoom.
  function aimanter(tt, libre, auTemps) {
    if (libre) return Math.round(tt * 100) / 100;
    if (!grille || !grille.sure || !(grille.temps > 0.05)) return Math.round(tt * 10) / 10;
    const u = auTemps ? grille.temps : unite();
    return grille.phase + Math.round((tt - grille.phase) / u) * u;
  }
  // le decor de la piste : un fond a peine plus clair, la grille des temps
  // (une barre plus marquee toutes les quatre), et l'invite quand elle est vide
  function peindreFondEffets(c, p) {
    if (!p) return;
    c.fillStyle = 'rgba(255,255,255,.018)';
    c.fillRect(ETI, p.y, largeur - ETI, p.h - 1);
    if (grille && grille.sure && grille.temps > 0.05) {
      const u = unite(), fin = Math.min(v1, total());
      const k0 = Math.ceil((v0 - grille.phase) / u), k1 = Math.floor((fin - grille.phase) / u);
      for (let k = k0; k <= k1 && k - k0 < 4000; k++) {
        const s = grille.phase + k * u, temps = Math.round((s - grille.phase) / grille.temps);
        c.fillStyle = temps % 4 === 0 ? 'rgba(255,255,255,.075)' : 'rgba(255,255,255,.03)';
        c.fillRect(Math.round(x(s)), p.y, 1, p.h - 1);
      }
    }
    if (!EFFETS.liste().length) {
      c.font = '400 11px ' + C('--f');
      c.textBaseline = 'middle';
      c.fillStyle = COUL.tx3;
      c.fillText('glissez ici un effet du panneau par sa poignée ⠿ : il n\'agira que sur ce passage',
                 ETI + 12, p.y + p.h / 2);
    }
  }
  // les blocs, peints avec les tetes : ils bougent sous la souris, et ceux que
  // la tete de lecture traverse s'allument
  function peindreEffets(c) {
    const p = piste('effets');
    if (!p) return;
    const liste = EFFETS.liste(), sel = EFFETS.choisi(), ts = +$('#scrub').value || 0;
    const hr = (p.h - 6) / EFFETS.rangs(), fondu = EFFETS.fondu();
    c.save();
    c.beginPath(); c.rect(ETI, p.y, largeur - ETI, p.h - 1); c.clip();
    c.textBaseline = 'middle';
    for (const b of liste) {
      const xa = x(b.a), xb = x(b.b);
      if (xb < ETI - 2 || xa > largeur + 2) continue;
      const y = p.y + 3 + b.rang * hr, h = hr - 2;
      const [f, bord, coul] = EFFETS.teinte(b.e);
      const actif = ts >= b.a && ts < b.b, choisi = b.id === sel;
      const xg = Math.max(ETI - 4, xa) + .5, w = Math.max(2, Math.min(largeur + 4, xb) - xg - .5);
      c.beginPath();
      if (c.roundRect) c.roundRect(xg, y, w, h, 4); else c.rect(xg, y, w, h);
      c.globalAlpha = b.v > 0 ? 1 : .5;
      c.fillStyle = f; c.fill();
      if (actif) { c.fillStyle = 'rgba(255,255,255,.1)'; c.fill(); }
      c.globalAlpha = 1;
      // un effet continu entre et sort en fondu : on le montre
      const lf = x(b.a + fondu) - xa;
      if (!EFFETS.net(b.e) && lf > 3 && xb - xa > 2 * lf) {
        for (const [g0, g1] of [[xa, xa + lf], [xb, xb - lf]]) {
          const g = c.createLinearGradient(g0, 0, g1, 0);
          g.addColorStop(0, 'rgba(0,0,0,.5)'); g.addColorStop(1, 'rgba(0,0,0,0)');
          c.fillStyle = g;
          c.fillRect(Math.min(g0, g1), y + 1, Math.abs(g1 - g0), h - 2);
        }
      }
      c.beginPath();
      if (c.roundRect) c.roundRect(xg, y, w, h, 4); else c.rect(xg, y, w, h);
      c.setLineDash(b.v > 0 ? [] : [3, 3]);
      c.lineWidth = choisi ? 1.6 : 1;
      c.strokeStyle = choisi ? '#ffffff' : bord;
      if (actif && !choisi) { c.shadowColor = bord; c.shadowBlur = 9; }
      c.stroke();
      c.shadowBlur = 0; c.setLineDash([]);
      // le nom, puis la valeur et l'instrument : sur deux lignes s'il y a la
      // place, sinon a la suite
      if (w > 28) {
        c.save();
        c.beginPath(); c.rect(xg + 5, y, w - 10, h); c.clip();
        const x0 = Math.max(xg, ETI) + 7, det = EFFETS.detail(b);
        c.fillStyle = coul;
        if (h >= 30) {
          c.font = '600 11px ' + C('--f');
          c.fillText(EFFETS.nom(b.e), x0, y + h / 2 - 6.5);
          c.font = '400 10px ' + C('--m');
          c.globalAlpha = .8;
          c.fillText(det, x0, y + h / 2 + 7);
          c.globalAlpha = 1;
        } else {
          c.font = '500 10.5px ' + C('--f');
          c.fillText(EFFETS.nom(b.e) + '  ·  ' + det, x0, y + h / 2 + .5);
        }
        c.restore();
      }
      // les poignees du bloc choisi : c'est par ses bords qu'on l'etire
      if (choisi && w > 16) {
        c.fillStyle = '#ffffff';
        c.fillRect(xg + 2.5, y + h / 2 - 5, 2, 10);
        c.fillRect(xg + w - 4.5, y + h / 2 - 5, 2, 10);
      }
    }
    if (fantome) {
      const xa = x(fantome.a), xb = x(fantome.b);
      c.setLineDash([4, 3]);
      c.fillStyle = 'rgba(56,189,248,.16)';
      c.strokeStyle = COUL.acc;
      c.beginPath(); c.rect(xa + .5, p.y + 3.5, Math.max(2, xb - xa - 1), p.h - 8);
      c.fill(); c.stroke();
      c.setLineDash([]);
    }
    c.restore();
    // le debut du bloc qu'on apporte, sur toute la hauteur : on voit sur quel
    // coup du son il tombe
    if (fantome) {
      c.fillStyle = 'rgba(56,189,248,.55)';
      c.fillRect(Math.round(x(fantome.a)), HAUT.regle, 1, haut - HAUT.regle);
    }
  }

  // la prise d'un changement de machine, survolee ou tenue : le bord gauche
  // de la deformation, ou toute la deformation jusqu'a l'instant inscrit
  function peindrePoigneeMachine(c) {
    const h = gesteM || survolM, p = piste('machines');
    if (!h || !p) return;
    const pl = planMachines(), i = pl.indexOf(h.e);
    if (i < 1) return;
    const xa = x(h.e.t - dureeChangement(pl, i)), xb = x(h.e.t);
    c.save();
    c.beginPath(); c.rect(ETI, p.y, largeur - ETI, p.h); c.clip();
    c.fillStyle = COUL.acc;
    if (h.ou === 'duree') {
      c.fillRect(Math.round(xa) - 1.5, p.y + 2, 3, p.h - 4);
      // deux petites fleches : on tire vers la gauche ou vers la droite
      for (const s of [-1, 1]) {
        c.beginPath();
        c.moveTo(xa + s * 4, p.y + p.h / 2 - 3.5);
        c.lineTo(xa + s * 8, p.y + p.h / 2);
        c.lineTo(xa + s * 4, p.y + p.h / 2 + 3.5);
        c.closePath(); c.fill();
      }
    } else {
      c.strokeStyle = COUL.acc; c.lineWidth = 1.5;
      c.strokeRect(Math.round(xa) + .5, p.y + 3.5, Math.max(2, xb - xa - 1), p.h - 7);
      c.fillRect(Math.round(xb) - 1, p.y + 2, 2, p.h - 4);
    }
    c.restore();
  }

  // le bloc sous le pointeur, et la partie prise : le corps, ou un bord
  function blocSous(px, py) {
    const p = piste('effets');
    if (!p || py < p.y || py >= p.y + p.h) return null;
    const hr = (p.h - 6) / EFFETS.rangs(), liste = EFFETS.liste();
    for (let i = liste.length - 1; i >= 0; i--) {
      const b = liste[i], y = p.y + 3 + b.rang * hr;
      if (py < y - 1 || py > y + hr - 1) continue;
      const xa = x(b.a), xb = x(b.b), w = xb - xa;
      if (px < xa - 3 || px > xb + 3) continue;
      let ou = 'corps';
      if (w >= 16) {
        if (px - xa < 6) ou = 'debut';
        else if (xb - px < 6) ou = 'fin';
      } else if (w >= 6) {
        if (px < xa) ou = 'debut';
        else if (px > xb) ou = 'fin';
      }
      return {b, ou};
    }
    return null;
  }
  // Le changement de machine sous le pointeur, et la prise : le bord gauche
  // de sa deformation (« duree » : on l'etire, l'arrivee ne bouge pas) ou le
  // reste, jusqu'a l'instant inscrit (« instant » : on deplace le changement).
  // Une deformation trop etroite pour deux prises se partage en son milieu.
  function changementSous(px, py) {
    const p = piste('machines');
    if (!p || py < p.y || py >= p.y + p.h) return null;
    const pl = planMachines();
    let best = null, bd = Infinity;
    for (let i = 1; i < pl.length; i++) {
      const xa = x(pl[i].t - dureeChangement(pl, i)), xb = x(pl[i].t), w = xb - xa;
      const coupe = w >= 12 ? xa + 6 : xa + w / 2;
      let ou = null, dist = Infinity;
      if (px >= xa - 6 && px <= coupe) { ou = 'duree'; dist = Math.abs(px - xa); }
      else if (px > coupe && px <= xb + 6) { ou = 'instant'; dist = Math.max(0, px - xb); }
      if (ou && dist < bd) { bd = dist; best = {e: pl[i], ou}; }
    }
    return best;
  }
  // etirer la deformation, ou deplacer le changement, aimante sur la grille
  function bougerChangement(px, libre) {
    const g = gesteM;
    if (!g.bouge && Math.abs(px - g.x0) < 3) return;
    if (!g.bouge) { g.bouge = true; EFFETS.memoriser(); }
    const dt = (px - g.x0) / utile() * (v1 - v0);
    if (g.ou === 'duree') {
      const debut = aimanter(g.t0 - g.d0 + dt, libre);
      g.e.d = Math.round(Math.max(0, Math.min(g.t0 - debut, g.t0 - g.prec, 60)) * 100) / 100;
    } else {
      g.e.t = Math.round(Math.max(g.prec + 0.1, Math.min(aimanter(g.t0 + dt, libre),
                                                         g.suiv - 0.1)) * 100) / 100;
    }
    seqEcrire();
    seqDessine();
    const pl = planMachines(), i = pl.indexOf(g.e), d = i > 0 ? dureeChangement(pl, i) : 0;
    info.textContent = 'déformation ' + d.toFixed(1) + ' s → ' + (noms[g.e.m] || g.e.m)
      + ' à ' + tc(g.e.t);
    info.hidden = false;
    const w = info.offsetWidth;
    info.style.left = Math.round(Math.max(ETI, Math.min(x(g.e.t) - w / 2, largeur - w - 4))) + 'px';
    dessiner();
    shot();
  }

  // deplacer ou etirer le bloc pris, aimante sur la grille
  function bougerBloc(px, libre) {
    const g = geste;
    if (!g.bouge && Math.abs(px - g.x0) < 3) return;
    if (!g.bouge) { g.bouge = true; EFFETS.memoriser(); }
    const T = total(), dt = (px - g.x0) / utile() * (v1 - v0), MIN = 0.1;
    let a = g.a0, b = g.b0;
    if (g.ou === 'corps') {
      const L = g.b0 - g.a0;
      a = Math.max(0, Math.min(aimanter(g.a0 + dt, libre), T - L));
      b = a + L;
    } else if (g.ou === 'debut') {
      a = Math.max(0, Math.min(aimanter(g.a0 + dt, libre), g.b0 - MIN));
    } else {
      b = Math.min(T, Math.max(aimanter(g.b0 + dt, libre), g.a0 + MIN));
    }
    EFFETS.deplacer(g.id, a, b);
    info.textContent = tc(a) + ' → ' + tc(b) + '  (' + (b - a).toFixed(1) + ' s)';
    info.hidden = false;
    const w = info.offsetWidth, xm = (x(a) + x(b)) / 2;
    info.style.left = Math.round(Math.max(ETI, Math.min(xm - w / 2, largeur - w - 4))) + 'px';
    tetes();
  }

  // la part du morceau que le rendu couvrira, quand elle n'est pas le tout
  function fenetreExport() {
    const dep = Math.max(0, +$('#start').value || 0), d = +$('#dur').value || 0;
    if (dep <= 0 && d <= 0) return null;
    return [dep, d > 0 ? Math.min(total(), dep + d) : total()];
  }
  function peindreExport(c) {
    const f = fenetreExport();
    if (!f) return;
    const y = HAUT.regle, h = haut - y, xa = x(f[0]), xb = x(f[1]);
    c.fillStyle = 'rgba(5,7,9,.62)';
    if (xa > ETI) c.fillRect(ETI, y, xa - ETI, h);
    if (xb < largeur) c.fillRect(xb, y, largeur - xb, h);
    c.fillStyle = COUL.acc;
    c.globalAlpha = .55;
    c.fillRect(xa, 0, Math.max(1, xb - xa), 3);
    c.globalAlpha = 1;
  }

  // ---- ce qui bouge : la tete de lecture, l'apercu anime, le survol
  function peindreTetes(c) {
    if (clip) {
      const xa = x(clip[0]), xb = x(clip[1]);
      c.fillStyle = 'rgba(56,189,248,.22)';
      c.fillRect(xa, 0, xb - xa, HAUT.regle - 1);
      c.fillStyle = 'rgba(56,189,248,.06)';
      c.fillRect(xa, HAUT.regle, xb - xa, haut - HAUT.regle);
    }
    peindreEffets(c);
    peindrePoigneeMachine(c);
    if (survol !== null && !prise && !geste && !gesteM) {
      c.fillStyle = 'rgba(255,255,255,.28)';
      c.fillRect(Math.round(x(survol.t)), 0, 1, haut);
    }
    if (video && clip && !video.paused) {
      const xv = Math.round(x(clip[0] + video.currentTime));
      c.fillStyle = COUL.acc;
      c.fillRect(xv - 1, 0, 2, haut);
    }
    const ts = +$('#scrub').value || 0, xs = Math.round(x(ts));
    if (xs >= ETI - 2 && xs <= largeur) {
      c.fillStyle = '#fff';
      c.shadowColor = 'rgba(255,255,255,.5)'; c.shadowBlur = 6;
      c.fillRect(xs - 1, HAUT.regle - 6, 2, haut - HAUT.regle + 6);
      c.shadowBlur = 0;
      c.beginPath(); c.moveTo(xs - 6, HAUT.regle - 8); c.lineTo(xs + 6, HAUT.regle - 8);
      c.lineTo(xs, HAUT.regle - 1); c.closePath(); c.fill();
    }
  }

  // ---- ce que dit la bulle de la frise, au survol
  function majInfo() {
    if (!survol || !track) { info.hidden = true; return; }
    const tt = survol.t, p = pistes.find(q => survol.py >= q.y && survol.py < q.y + q.h);
    let txt = tc(tt);
    const pres = (liste, px) => liste.find(s => Math.abs(x(s) - survol.px) <= px);
    if (p && p.cle === 'son') {
      const d = pres(drops, 5), s = pres(dedo, 6);
      if (d !== undefined) txt = 'paroxysme · ' + tc(d);
      else if (s !== undefined) txt = 'dédoublement · ' + tc(s);
    } else if (p && p.cle === 'effets') {
      const h = blocSous(survol.px, survol.py);
      txt = h ? EFFETS.decrire(h.b)
              : EFFETS.liste().length ? tc(tt)
              : 'glissez un effet du panneau jusqu\'ici · ' + tc(tt);
    } else if (p && p.cle === 'machines') {
      const pl = planMachines(), hm = changementSous(survol.px, survol.py);
      let i = 0;
      while (i + 1 < pl.length && pl[i + 1].t <= tt) i++;
      const suiv = pl[i + 1];
      txt = (noms[pl[i].m] || pl[i].m) + ' · ' + tc(tt);
      if (suiv && tt >= suiv.t - dureeChangement(pl, i + 1))
        txt = 'déformation → ' + (noms[suiv.m] || suiv.m);
      if (hm) {
        const nm = noms[hm.e.m] || hm.e.m, d = dureeChangement(pl, pl.indexOf(hm.e));
        txt = hm.ou === 'duree'
          ? 'déformation → ' + nm + ' · ' + d.toFixed(1) + ' s · glisser pour l\'allonger ou la raccourcir'
          : nm + ' à ' + tc(hm.e.t) + ' · glisser pour déplacer le changement';
      }
    } else if (p && p.cle === 'melodie' && p.notes) {
      const yv = q => p.y + 4 + (1 - (q[2] - p.lo) / p.ecart) * (p.h - 10);
      let best = null, bd = 6;
      for (const n of p.notes) {
        if (tt < n[0] - (v1 - v0) / utile() * 3 || tt > n[1] + (v1 - v0) / utile() * 3) continue;
        const d = Math.abs(yv(n) + 1 - survol.py);
        if (d < bd) { bd = d; best = n; }
      }
      if (best) txt = nomNote(best[2]) + ' · ' + tc(best[0]);
    } else if (p && p.cle === 'fonds' && plan) {
      const fd = plan.fondus.find(f => tt >= f[0] && tt < f[1]);
      const b = plan.blocs.find(q => tt >= q[0] && tt < q[1]);
      if (fd) txt = 'fondu enchaîné · ' + tc(tt);
      else if (b && fonds[b[2]]) txt = fonds[b[2]].name + (b[3] < 0 ? ' (à l\'envers)' : '');
    }
    info.textContent = txt;
    info.hidden = false;
    const w = info.offsetWidth;
    info.style.left = Math.round(Math.max(ETI, Math.min(survol.px - w / 2, largeur - w - 4))) + 'px';
  }

  // ---- la vue : tout le morceau, ou un zoom
  function borner() {
    const T = total(), s = Math.min(v1 - v0, T);
    if (v0 < 0) { v0 = 0; v1 = s; }
    if (v1 > T) { v1 = T; v0 = Math.max(0, T - s); }
  }
  function majZoom() {
    const T = total(), z = T ? T / ((v1 - v0) || T) : 1;
    $('#zoomTxt').textContent = z < 1.05 ? 'tout' : '×' + (z < 10 ? z.toFixed(1) : Math.round(z));
  }
  function zoomer(f, centre) {
    const T = total();
    if (!T) return;
    const s = Math.max(Math.min(6, T), Math.min(T, (v1 - v0) * f));
    const k = (centre - v0) / ((v1 - v0) || 1);
    v0 = centre - k * s; v1 = v0 + s;
    borner(); majZoom(); resurvoler(); dessiner();
  }
  function montrer(tt) {
    if (tt >= v0 && tt <= v1) return;
    const s = v1 - v0;
    v0 = tt - s / 2; v1 = v0 + s;
    borner(); dessiner();
  }
  function chercher(tt) {
    const s = $('#scrub');
    if (s.disabled) return;
    s.value = Math.max(0, Math.min(+s.max, tt)).toFixed(2);
    s.dispatchEvent(new Event('input'));
  }

  // ---- les gestes
  const pos = e => { const r = zone.getBoundingClientRect(); return [e.clientX - r.left, e.clientY - r.top]; };
  zone.addEventListener('pointerdown', e => {
    if (!track || e.button !== 0) return;
    const [px, py] = pos(e);
    if (px < ETI) return;
    zone.setPointerCapture(e.pointerId);
    zone.focus({preventScroll: true});
    e.preventDefault();
    // un bloc d'effet : on le choisit, et on peut le deplacer ou l'etirer.
    // La tete de lecture ne bouge pas.
    const h = blocSous(px, py);
    if (h) {
      EFFETS.choisir(h.b.id);
      geste = {id: h.b.id, ou: h.ou, x0: px, a0: h.b.a, b0: h.b.b, bouge: false};
      zone.style.cursor = h.ou === 'corps' ? 'grabbing' : 'ew-resize';
      tetes();
      return;
    }
    // un changement de machine : on etire sa deformation ou on le deplace ;
    // un clic sans glisser, lui, reste un clic : il y place l'apercu
    const hm = changementSous(px, py);
    if (hm) {
      const pl = planMachines(), i = pl.indexOf(hm.e);
      gesteM = {e: hm.e, ou: hm.ou, x0: px, t0: hm.e.t, d0: dureeChangement(pl, i),
                prec: pl[i - 1].t, suiv: i + 1 < pl.length ? pl[i + 1].t : total(),
                bouge: false};
      zone.style.cursor = hm.ou === 'duree' ? 'ew-resize' : 'grabbing';
      tetes();
      return;
    }
    // ailleurs dans la piste des effets : on lache le bloc choisi
    const p = pistes.find(q => py >= q.y && py < q.y + q.h);
    if (p && p.cle === 'effets') EFFETS.choisir(null);
    prise = true;
    chercher(t(px));
  });
  zone.addEventListener('pointermove', e => {
    const [px, py] = pos(e);
    if (geste) return bougerBloc(px, e.altKey);
    if (gesteM) return bougerChangement(px, e.altKey);
    if (prise) chercher(t(Math.max(ETI, Math.min(largeur - MARGE, px))));
    survol = track && px >= ETI ? {t: Math.max(0, Math.min(total(), t(px))), px, py} : null;
    // ce que la souris prendrait : un bloc a deplacer, un bord a etirer, la
    // deformation d'un changement de machine, ou ce changement lui-meme
    if (!prise) {
      const h = survol ? blocSous(px, py) : null;
      survolM = survol && !h ? changementSous(px, py) : null;
      zone.style.cursor = h ? (h.ou === 'corps' ? 'grab' : 'ew-resize')
                        : survolM ? (survolM.ou === 'duree' ? 'ew-resize' : 'grab') : '';
    }
    majInfo(); tetes();
  });
  const lacher = e => {
    if (geste) {
      const g = geste;
      geste = null;
      zone.style.cursor = '';
      if (g.bouge) EFFETS.fini();
      majInfo();
    }
    if (gesteM) {
      const g = gesteM;
      gesteM = null;
      zone.style.cursor = '';
      if (!g.bouge) {
        // un simple clic : comme partout sur la frise, il y place l'apercu
        if (e && e.type === 'pointerup') chercher(t(Math.max(ETI, pos(e)[0])));
      } else {
        const pl = planMachines(), i = pl.indexOf(g.e);
        setStatus(g.ou === 'duree'
          ? 'déformation vers ' + (noms[g.e.m] || g.e.m) + ' : '
            + (i > 0 ? dureeChangement(pl, i) : 0).toFixed(2) + ' s (Ctrl + Z pour revenir)'
          : (noms[g.e.m] || g.e.m) + ' arrive à ' + tc(g.e.t) + ' (Ctrl + Z pour revenir)');
        _redessine();
      }
      majInfo();
    }
    prise = false; tetes();
  };
  zone.addEventListener('pointerup', lacher);
  zone.addEventListener('pointercancel', lacher);
  zone.addEventListener('pointerleave', () => {
    survol = null; survolM = null; info.hidden = true; tetes();
  });
  zone.addEventListener('dblclick', e => {
    if (pos(e)[1] < HAUT.regle && track) { v0 = 0; v1 = total(); majZoom(); dessiner(); }
  });
  zone.addEventListener('wheel', e => {
    if (!track) return;
    const [px] = pos(e), T = total(), s = v1 - v0;
    if (e.ctrlKey || e.metaKey) {
      e.preventDefault();
      zoomer(Math.exp(e.deltaY * 0.0025), px >= ETI ? t(px) : (v0 + v1) / 2);
    } else if (s < T - 1e-6 && (e.shiftKey || Math.abs(e.deltaX) > Math.abs(e.deltaY))) {
      e.preventDefault();
      const d = Math.abs(e.deltaX) > Math.abs(e.deltaY) ? e.deltaX : e.deltaY;
      v0 += d / utile() * s; v1 += d / utile() * s;
      borner(); resurvoler(); dessiner();
    }
  }, {passive: false});
  zone.addEventListener('keydown', e => {
    if (!track) return;
    const s = +$('#scrub').value || 0, pas = e.shiftKey ? 10 : 1;
    let n = null;
    if (e.key === 'ArrowRight') n = s + pas;
    else if (e.key === 'ArrowLeft') n = s - pas;
    else if (e.key === 'Home') n = 0;
    else if (e.key === 'End') n = total();
    else if (e.key === '+' || e.key === '=') { e.preventDefault(); return zoomer(0.5, s); }
    else if (e.key === '-' || e.key === '_') { e.preventDefault(); return zoomer(2, s); }
    if (n === null) return;
    e.preventDefault();
    chercher(n);
    montrer(+$('#scrub').value);
  });
  $('#zPlus').onclick = () => zoomer(0.5, +$('#scrub').value || 0);
  $('#zMoins').onclick = () => zoomer(2, (v0 + v1) / 2);
  new ResizeObserver(() => dessiner()).observe(zone);

  // ---- ce que la frise demande au studio
  async function morceau() {
    const tid = track;
    onde = null; dedo = []; notes = null; plan = null; grille = null;
    v0 = 0; v1 = total();
    zone.setAttribute('aria-valuemax', String(Math.round(total())));
    majZoom(); dessiner();
    try {
      const j = await (await fetch('/onde?track=' + tid)).json();
      if (j.error) throw new Error(j.error);
      if (tid !== track) return;
      onde = {n: j.n, par_s: j.par_s, bandes: j.bandes.map(
        b => Uint8Array.from(atob(b), ch => ch.charCodeAt(0)))};
      grille = j.grille || null;
    } catch (e) { onde = null; }
    dessiner();
    chargerDedo(); chargerNotes(); chargerFonds();
  }
  function chargerDedo() {
    clearTimeout(mDedo);
    mDedo = setTimeout(async () => {
      const tid = track;
      if (!tid) return;
      try {
        const j = await (await fetch('/splits?track=' + tid + '&count='
          + $('#splitCount').value + '&on=' + encodeURIComponent($('#splitOn').value))).json();
        if (tid === track) { dedo = j.times || []; dessiner(); }
      } catch (e) { /* la frise s'en passe */ }
    }, 250);
  }
  function chargerNotes() {
    clearTimeout(mNotes);
    mNotes = setTimeout(async () => {
      const n = ++nNotes;
      if (!track || !$('#midi').value) { notes = null; dessiner(); return; }
      const q = new URLSearchParams({
        track, midi: $('#midi').value, bpm: $('#midiBpm').value,
        telQuel: $('#midiTelQuel').checked ? '1' : '0',
        cale: $('#midiCale').checked ? '1' : '0'});
      let j = null;
      try { j = await (await fetch('/notes?' + q)).json(); } catch (e) { j = null; }
      if (n !== nNotes) return;
      if (j && !j.error) { notes = j.notes; noteAuto = +j.auto || 0; noteGrille = !!j.grille; }
      else notes = null;
      dessiner();
    }, 200);
  }
  function chargerFonds() {
    clearTimeout(mFonds);
    mFonds = setTimeout(async () => {
      const n = ++nFonds;
      if (!track || !fonds.length) { plan = null; dessiner(); return; }
      const q = new URLSearchParams({
        noms: fonds.map(f => f.name).join('|'), vitesse: vitesseFond(),
        boucle: $('#fondBoucle').value, fondu: $('#fondFondu').value,
        photo: $('#fondPhoto').value, total: duration});
      let j = null;
      try { j = await (await fetch('/plan_fonds?' + q)).json(); } catch (e) { j = null; }
      if (n !== nFonds) return;
      plan = j && !j.error ? j : null;
      dessiner();
    }, 250);
  }
  function poserClip(debut, duree) {
    clip = debut === null || debut === undefined ? null : [debut, debut + duree];
    tetes();
  }
  function suivre(v) {
    video = v;
    cancelAnimationFrame(rafVideo);
    const pas = () => { if (!video) return; tetes(); rafVideo = requestAnimationFrame(pas); };
    if (v) rafVideo = requestAnimationFrame(pas); else tetes();
  }
  // l'instant sous un point de l'ecran, s'il tombe sur la frise (un peu
  // au-dessus ou au-dessous compte encore : on lache vite), ou null
  function sous(cx, cy) {
    if (!track || !total()) return null;
    const r = zone.getBoundingClientRect();
    if (cx < r.left + ETI - 4 || cx > r.right || cy < r.top - 16 || cy > r.bottom + 16) return null;
    return Math.max(0, Math.min(total(), t(Math.max(ETI, cx - r.left))));
  }
  // ou est un bloc a l'ecran, pour y accrocher son panneau
  function rectBloc(id) {
    const p = piste('effets'), b = EFFETS.trouver(id);
    if (!p || !b) return null;
    const r = zone.getBoundingClientRect(), hr = (p.h - 6) / EFFETS.rangs();
    const xa = Math.max(ETI, Math.min(largeur, x(b.a))), xb = Math.max(ETI, Math.min(largeur, x(b.b)));
    const y = r.top + p.y + 3 + b.rang * hr;
    return {left: r.left + xa, right: r.left + xb, top: y, bottom: y + hr - 2,
            haut: r.top, bas: r.bottom};
  }
  return {dessiner, tetes, morceau, chargerDedo, chargerNotes, chargerFonds,
          montrer, zoomer, clip: poserClip, suivre,
          noms: m => { noms = m || {}; dessiner(); },
          enLecture: () => !!clip,
          grille: () => grille, aimanter, sous, rectBloc,
          fantome: f => { fantome = f || null; tetes(); },
          // ou tombe un instant, et une piste, a l'ecran
          ecran: tt => zone.getBoundingClientRect().left + x(tt),
          piste: cle => {
            const q = piste(cle), r = zone.getBoundingClientRect();
            return q ? {haut: r.top + q.y, h: q.h} : null;
          }};
})();

/* ---------- les effets places sur la frise ----------

   Un effet du panneau, glisse sur la frise par sa poignee, n'agit que sur la
   duree de son bloc : sa valeur y remplace celle du curseur, et son
   instrument celui de la liste. Hors des blocs, c'est le curseur qui compte
   — a zero, l'effet n'existe que dans ses blocs. Le moteur recoit les blocs
   en JSON (le champ cache #effets) et les relit a chaque image.

   Les blocs sont gardes dans ce navigateur, par nom de morceau : on retrouve
   les siens en redeposant le meme fichier. */
var EFFETS = (() => {
  const MAX = 300;
  // une teinte par famille, comme les sections du panneau : fond, bord, texte
  const TEINTES = {
    reactions: ['#3b2b0d', '#c58b25', '#fde68a'],
    avaries: ['#3d1727', '#c4587d', '#fecdd3'],
    echo: ['#0f3431', '#31a597', '#a7f3d0'],
    matiere: ['#1e2734', '#6d7c92', '#e2e8f0'],
  };
  // deux listes ne portent pas le nom de leur curseur suivi de « On »
  const AUTRE_INST = {gridPulse: 'gridOn', bgFlash: 'flashOn'};
  const insp = $('#blocInsp'), q = s => insp.querySelector(s);
  let PLAC = {}, FONDU = 0.25;
  let blocs = [], choisi = null, histo = [], refait = [], nId = 1, nRangs = 1, cle = '';
  let prise = null;           // l'effet qu'on emporte du panneau vers la frise
  const noms = {};

  // ---- ce qu'on sait d'un effet
  const curseur = e => $('#' + e);
  function nom(e) {
    if (!(e in noms)) {
      const l = document.querySelector('label[for="' + e + '"]');
      noms[e] = l ? l.textContent.trim() : e;
    }
    return noms[e];
  }
  function teinte(e) {
    const s = curseur(e) && curseur(e).closest('.section');
    return TEINTES[s ? s.dataset.section : ''] || TEINTES.matiere;
  }
  // la liste « sur : ... » de l'effet, s'il part sur un instrument
  function liste(e) {
    return PLAC[e] && PLAC[e].inst ? $('#' + (AUTRE_INST[e] || e + 'On')) : null;
  }
  // un instrument ou une cadence basculent net ; le reste entre en fondu
  const net = e => !!(PLAC[e] && (PLAC[e].inst || PLAC[e].entier));
  function texte(e, v) {
    if (e === 'cadence') return v < 2 ? 'fluide' : Math.round(30 / v) + ' i/s';
    const st = +(curseur(e) || {}).step || 0.01;
    return (+v).toFixed(st < 0.01 ? 3 : st >= 1 ? 0 : 2);
  }
  function detail(b) {
    if (!(b.v > 0)) return 'coupé';
    // « aucun instrument » est l'ordinaire : on ne le redit pas sur le bloc
    return texte(b.e, b.v) + (b.on && b.on !== 'continu' ? ' · ' + b.on : '');
  }
  function decrire(b) {
    return nom(b.e) + ' · ' + detail(b) + ' · ' + tc(b.a) + ' → ' + tc(b.b);
  }
  // La valeur d'un bloc neuf : celle du curseur s'il est monte, sinon un peu
  // moins de la moitie de sa course — assez pour se voir.
  function valeurNeuve(e) {
    const el = curseur(e), v = +el.value;
    if (v > 0) return v;
    if (e === 'cadence') return 3;
    const lo = +el.min || 0, hi = +el.max || 1, st = +el.step || 0.01;
    return +(Math.round((lo + 0.4 * (hi - lo)) / st) * st).toFixed(4);
  }

  // ---- les blocs
  const trouver = id => blocs.find(b => b.id === id) || null;
  // les rangees : un bloc va dans la premiere ou il ne chevauche personne
  function ranger() {
    const fins = [];
    for (const b of [...blocs].sort((p, r) => p.a - r.a || r.b - p.b)) {
      let k = fins.findIndex(f => f <= b.a + 1e-6);
      if (k < 0) { k = fins.length; fins.push(0); }
      fins[k] = b.b;
      b.rang = k;
    }
    nRangs = Math.max(1, fins.length);
  }
  // le champ que lit le moteur, et la copie gardee pour ce morceau
  function ecrire(garder) {
    $('#effets').value = JSON.stringify(blocs.map(b => {
      const o = {e: b.e, a: +b.a.toFixed(3), b: +b.b.toFixed(3), v: +(+b.v).toPrecision(4)};
      if (b.on) o.on = b.on;
      return o;
    }));
    if (garder && cle) {
      try {
        if (blocs.length) localStorage.setItem(cle, $('#effets').value);
        else localStorage.removeItem(cle);
      } catch (e) { /* la page s'en passe */ }
    }
    // la poignee d'un effet deja pose se voit dans le panneau
    for (const p of document.querySelectorAll('.ctl-glisse'))
      p.classList.toggle('pose', blocs.some(b => b.e === p.dataset.e));
  }
  // Un pas de retour en arriere : les blocs, et le plan des machines — la
  // frise deplace l'un comme l'autre, Ctrl + Z defait l'un comme l'autre.
  function etat(avecCurseurs) {
    const e = {b: blocs, m: $('#machines').value, d: $('#machine').value};
    if (avecCurseurs) {
      e.z = {};
      for (const id of curseursEffets()) e.z[id] = $('#' + id).value;
    }
    return JSON.stringify(e);
  }
  function memoriser(avecCurseurs) {
    histo.push(etat(avecCurseurs === true));
    if (histo.length > 100) histo.shift();
    refait = [];
  }
  // les curseurs que « 0 » remet a zero : chaque effet placable, et le
  // dedoublement du trait
  function curseursEffets() {
    return Object.keys(PLAC).concat(['split']).filter(id => $('#' + id));
  }
  // « 0 » : tous les effets a zero, avant de personnaliser. L'allure — la
  // couleur, la machine, le fond — et la frise ne bougent pas.
  function toutAZero() {
    const ids = curseursEffets().filter(id => +$('#' + id).value !== 0);
    if (!ids.length) return setStatus('tous les effets sont déjà à zéro');
    memoriser(true);
    for (const id of ids) {
      const el = $('#' + id);
      el.value = 0;
      el.dispatchEvent(new Event('input'));
    }
    majFrequences();
    shot();
    setStatus(ids.length + ' effet' + (ids.length > 1 ? 's' : '') + ' remis à zéro : '
              + 'montez ceux que vous voulez, ou posez-les sur la frise (Ctrl + Z pour revenir)');
  }
  // apres chaque changement : les rangees, le champ, la frise et l'apercu
  function changer() {
    ranger();
    ecrire(true);
    FRISE.dessiner();
    majInsp();
    shot();
  }
  function ajouter(e, a, b) {
    if (!PLAC[e] || !track) return null;
    if (blocs.length >= MAX) {
      setStatus('au plus ' + MAX + ' effets placés sur un morceau', true);
      return null;
    }
    memoriser();
    const s = liste(e);
    const bl = {id: nId++, e, a, b, v: valeurNeuve(e), on: s ? s.value : null, rang: 0};
    blocs.push(bl);
    choisi = bl.id;
    changer();
    ouvrirInsp();
    setStatus('« ' + nom(e) + ' » posé de ' + tc(a) + ' à ' + tc(b)
              + ' — son intensité se règle dans la bulle, Suppr l\'enlève');
    return bl;
  }
  // pendant un geste : ni rangees ni copie ; la frise et l'apercu suivent
  function deplacer(id, a, b) {
    const bl = trouver(id);
    if (!bl) return;
    bl.a = a; bl.b = b;
    ecrire(false);
    majInsp();
    placerInsp();
    shot();
  }
  function modifier(id, champs) {
    const bl = trouver(id);
    if (!bl) return;
    Object.assign(bl, champs);
    changer();
  }
  function supprimer(id) {
    const bl = trouver(id);
    if (!bl) return;
    memoriser();
    blocs = blocs.filter(b => b !== bl);
    if (choisi === id) { choisi = null; fermerInsp(); }
    changer();
    setStatus('« ' + nom(bl.e) + ' » retiré de la frise — Ctrl + Z le remet');
  }
  function dupliquer(id) {
    const bl = trouver(id), T = duration;
    if (!bl) return;
    const L = bl.b - bl.a;
    // juste apres lui ; au bout du morceau, juste avant
    let a = bl.b;
    if (a + 0.1 > T) a = Math.max(0, bl.a - L);
    memoriser();
    const n = Object.assign({}, bl, {id: nId++, a, b: Math.min(T, a + L)});
    blocs.push(n);
    choisi = n.id;
    changer();
    ouvrirInsp();
  }
  function vider() {
    if (!blocs.length) return;
    memoriser();
    const n = blocs.length;
    blocs = [];
    choisi = null; fermerInsp();
    changer();
    setStatus(n > 1 ? n + ' effets retirés de la frise — Ctrl + Z les remet'
                    : 'effet retiré de la frise — Ctrl + Z le remet');
  }
  function retablir(json) {
    const e = JSON.parse(json);
    blocs = e.b;
    // les curseurs, quand le pas en arriere est celui de « 0 »
    for (const [id, v] of Object.entries(e.z || {})) {
      const el = $('#' + id);
      if (el && el.value !== v) { el.value = v; el.dispatchEvent(new Event('input')); }
    }
    if (e.m !== $('#machines').value || e.d !== $('#machine').value) {
      $('#machine').value = e.d;
      seqPose(e.m);
    }
    if (!trouver(choisi)) { choisi = null; fermerInsp(); }
    changer();
  }
  function annuler() {
    if (!histo.length) return setStatus('rien à annuler');
    const h = histo.pop();
    refait.push(etat(!!JSON.parse(h).z));
    retablir(h);
    majFrequences();
    setStatus('retour en arrière (Ctrl + Maj + Z pour refaire)');
  }
  function refaire() {
    if (!refait.length) return;
    const r = refait.pop();
    histo.push(etat(!!JSON.parse(r).z));
    retablir(r);
    majFrequences();
  }
  // un nouveau morceau : ses blocs de la derniere fois, s'il y en a
  function morceau(n) {
    cle = 'omnipotard.effets.' + n;
    blocs = []; histo = []; refait = [];
    choisi = null; fermerInsp();
    try {
      const l = JSON.parse(localStorage.getItem(cle) || '[]');
      for (const o of Array.isArray(l) ? l : []) {
        if (!o || (Object.keys(PLAC).length && !PLAC[o.e])) continue;
        const a = Math.max(0, +o.a), b = Math.min(duration, +o.b);
        if (!(b - a > 0.05) || !Number.isFinite(+o.v)) continue;
        blocs.push({id: nId++, e: o.e, a, b, v: +o.v, on: o.on || null, rang: 0});
      }
    } catch (e) { blocs = []; }
    ranger();
    ecrire(false);
    FRISE.dessiner();
    return blocs.length;
  }

  // ---- la bulle du bloc choisi
  function choisir(id) {
    choisi = trouver(id) ? id : null;
    if (choisi) ouvrirInsp(); else fermerInsp();
    FRISE.tetes();
  }
  function ouvrirInsp() {
    const bl = trouver(choisi);
    if (!bl) return fermerInsp();
    const el = curseur(bl.e), r = q('.bi-v'), s = liste(bl.e);
    q('.bi-nom').textContent = nom(bl.e);
    q('.bi-pt').style.background = teinte(bl.e)[1];
    r.min = el.min; r.max = el.max; r.step = el.step;
    q('.bi-inst').hidden = !s;
    if (s) q('.bi-on').innerHTML = s.innerHTML;
    BULLE.fermer();
    insp.hidden = false;
    majInsp();
    placerInsp();
  }
  function fermerInsp() { insp.hidden = true; }
  function majInsp() {
    const bl = trouver(choisi);
    if (!bl || insp.hidden) return;
    const r = q('.bi-v');
    r.value = bl.v;
    remplir(r);
    q('.bi-vo').textContent = bl.v > 0 ? texte(bl.e, bl.v) : 'coupé';
    q('.bi-note').hidden = bl.v > 0;
    if (liste(bl.e)) q('.bi-on').value = bl.on || liste(bl.e).value;
    let quand = tc(bl.a) + ' → ' + tc(bl.b) + '  ·  ' + (bl.b - bl.a).toFixed(1) + ' s';
    // en temps du morceau, quand le bloc en fait un compte rond
    const g = FRISE.grille();
    if (g && g.sure && g.temps > 0.05) {
      const n = (bl.b - bl.a) / g.temps;
      if (Math.abs(n - Math.round(n)) < 0.02 && Math.round(n) > 0)
        quand += '  ·  ' + Math.round(n) + ' temps';
    }
    q('.bi-quand').textContent = quand;
    for (const [k, v] of [['.bi-a', bl.a], ['.bi-b', bl.b]])
      if (document.activeElement !== q(k)) q(k).value = v.toFixed(2);
  }
  // Au-dessus de la barre de lecture, a l'aplomb du bloc : elle cache le bas
  // de l'apercu, pas la frise ni les boutons de lecture.
  function placerInsp() {
    if (insp.hidden || !choisi) return;
    const r = FRISE.rectBloc(choisi);
    if (!r) return;
    const W = insp.offsetWidth, H = insp.offsetHeight, cx = (r.left + r.right) / 2;
    const x = Math.max(8, Math.min(cx - W / 2, innerWidth - W - 8));
    let y = $('#chrono').getBoundingClientRect().top - H - 10, dessous = false;
    if (y < 8) { y = Math.min(r.bas + 10, innerHeight - H - 8); dessous = true; }
    insp.style.left = Math.round(x) + 'px';
    insp.style.top = Math.round(y) + 'px';
    insp.style.setProperty('--fl', Math.round(Math.max(14, Math.min(W - 14, cx - x))) + 'px');
    insp.classList.toggle('dessous', dessous);
  }
  // l'intensite : un pas de retour en arriere par geste, pas par cran
  q('.bi-v').addEventListener('pointerdown', () => memoriser());
  q('.bi-v').addEventListener('keydown', e => {
    if (!e.repeat && /^(Arrow|Page|Home|End)/.test(e.key)) memoriser();
  });
  q('.bi-v').addEventListener('input', () => {
    const bl = trouver(choisi);
    if (!bl) return;
    bl.v = +q('.bi-v').value;
    ecrire(true);
    majInsp();
    FRISE.tetes();
    shot();
  });
  q('.bi-on').addEventListener('change', () => {
    memoriser();
    modifier(choisi, {on: q('.bi-on').value});
  });
  for (const k of ['.bi-a', '.bi-b']) {
    q(k).addEventListener('change', () => {
      const bl = trouver(choisi);
      if (!bl) return;
      let a = +q('.bi-a').value, b = +q('.bi-b').value;
      if (!Number.isFinite(a) || !Number.isFinite(b)) return majInsp();
      a = Math.max(0, Math.min(a, duration));
      b = Math.max(0, Math.min(b, duration));
      if (b - a < 0.1) {
        if (k === '.bi-a') a = Math.max(0, b - 0.1); else b = Math.min(duration, a + 0.1);
      }
      memoriser();
      modifier(choisi, {a, b});
    });
  }
  q('.bi-ferme').onclick = () => choisir(null);
  q('.bi-sup').onclick = () => supprimer(choisi);
  q('.bi-dup').onclick = () => dupliquer(choisi);
  q('.bi-aller').onclick = () => {
    const bl = trouver(choisi);
    if (bl) { allerA(bl.a + 0.05); shot(); }
  };
  $('#effetsVider').onclick = vider;
  // un clic ailleurs referme la bulle ; la frise, la barre de lecture et les
  // masques la laissent ouverte : on ecoute et on regle en meme temps
  document.addEventListener('pointerdown', e => {
    if (insp.hidden || !e.target.closest) return;
    if (e.target.closest('#blocInsp, #frise, #lecture, #masques, .ctl-glisse')) return;
    choisir(null);
  }, true);
  window.addEventListener('resize', placerInsp);
  document.addEventListener('scroll', placerInsp, true);
  // Suppr, Echap, Ctrl + Z — sauf dans un champ de saisie, ou ces touches
  // appartiennent au texte
  document.addEventListener('keydown', e => {
    if (!$('#styles').hidden) return;
    const c = e.target;
    if (prise && e.key === 'Escape') { finirGlisse(); prise = null; return; }
    if (c.matches && c.matches('input[type=text], input[type=number], textarea, select')) return;
    const dedans = c === document.body
             || (c.closest && c.closest('#frise, #blocInsp, #chrono, #carteApercu, '
                                        + '.ctl-glisse, #seqBloc, #rail'));
    if ((e.ctrlKey || e.metaKey) && !e.altKey && /^[zZyY]$/.test(e.key)) {
      if (!dedans) return;
      e.preventDefault();
      if (/^[yY]$/.test(e.key) || e.shiftKey) refaire(); else annuler();
      return;
    }
    if (!choisi) return;
    if ((e.key === 'Delete' || e.key === 'Backspace') && dedans) {
      e.preventDefault();
      supprimer(choisi);
    } else if (e.key === 'Escape') choisir(null);
  });

  // ---- les poignees du panneau : on emporte un effet vers la frise
  // le bloc qu'un depot donnerait : il commence la ou l'on lache, et dure
  // deux mesures (huit temps), ou quatre secondes sans grille
  // le flash d'inversion est un eclair : son bloc ne dure qu'un temps
  function bornesDepot(tt, libre, auTemps, e) {
    const T = duration, g = FRISE.grille();
    const temps = g && g.sure && g.temps > 0.05 ? g.temps : 0;
    const L = Math.min(T, e === 'inversion' ? (temps || 0.5) : temps ? 8 * temps : 4);
    const a = Math.max(0, Math.min(FRISE.aimanter(tt, libre, auTemps), T - L));
    return [a, a + L];
  }
  function commencer(e) {
    const g = $('#glisse');
    g.innerHTML = '<i></i><b></b><span></span>';
    g.querySelector('i').style.background = teinte(e)[1];
    g.querySelector('b').textContent = nom(e);
    g.hidden = false;
    document.body.classList.add('glisse');
    BULLE.fermer();
  }
  function suivreGlisse(cx, cy, libre) {
    const g = $('#glisse');
    g.style.left = cx + 'px';
    g.style.top = cy + 'px';
    const tt = FRISE.sous(cx, cy);
    g.classList.toggle('sur', tt !== null);
    if (tt === null) {
      FRISE.fantome(null);
      g.querySelector('span').textContent = 'lâchez-le sur la frise';
      return;
    }
    const [a, b] = bornesDepot(tt, libre, false, prise && prise.e);
    FRISE.fantome({a, b});
    g.querySelector('span').textContent = tc(a) + ' → ' + tc(b);
  }
  function finirGlisse() {
    $('#glisse').hidden = true;
    document.body.classList.remove('glisse');
    FRISE.fantome(null);
  }
  function deposer(e, a, b) {
    const bl = ajouter(e, a, b);
    // on va voir : la tete de lecture se pose au debut du bloc — sauf pendant
    // l'ecoute, qui continue sans sauter
    if (bl && !ECOUTE.actif()) { allerA(a + 0.05); shot(); }
  }
  // sans glisser (un clic, ou Entree au clavier) : sur le temps le plus
  // proche de l'instant regarde
  function ici(e) {
    if (!track) return;
    const [a, b] = bornesDepot(+$('#scrub').value || 0, false, true, e);
    deposer(e, a, b);
  }
  function poignee(e, ctl) {
    const b = document.createElement('button');
    b.type = 'button';
    b.className = 'ctl-glisse';
    b.dataset.e = e;
    b.innerHTML = '<i class="ic">' + icone('poignee') + '</i>';
    b.title = 'glisser sur la frise : l\'effet n\'agira que sur ce passage'
            + ' (un clic le pose à l\'instant regardé)';
    b.setAttribute('aria-label', 'placer « ' + nom(e) + ' » sur la frise');
    ctl.appendChild(b);
    b.addEventListener('pointerdown', ev => {
      if (ev.button !== 0) return;
      ev.preventDefault();
      if (!track) {
        setStatus('déposez d\'abord un morceau : les effets se placent sur sa frise', true);
        return;
      }
      prise = {e, x: ev.clientX, y: ev.clientY, glisse: false};
      b.setPointerCapture(ev.pointerId);
    });
    b.addEventListener('pointermove', ev => {
      if (!prise || prise.e !== e) return;
      if (!prise.glisse) {
        if (Math.hypot(ev.clientX - prise.x, ev.clientY - prise.y) < 5) return;
        prise.glisse = true;
        commencer(e);
      }
      suivreGlisse(ev.clientX, ev.clientY, ev.altKey);
    });
    b.addEventListener('pointerup', ev => {
      if (!prise || prise.e !== e) return;
      const p = prise;
      prise = null;
      if (!p.glisse) return ici(e);
      const tt = FRISE.sous(ev.clientX, ev.clientY);
      finirGlisse();
      if (tt !== null) {
        const [a, c] = bornesDepot(tt, ev.altKey, false, e);
        deposer(e, a, c);
      }
    });
    b.addEventListener('pointercancel', () => {
      if (prise && prise.glisse) finirGlisse();
      prise = null;
    });
    b.addEventListener('click', ev => { if (ev.detail === 0) ici(e); });
  }
  // la liste vient du moteur : chaque effet qu'il sait limiter a un bloc
  function placables(p, fondu) {
    PLAC = p || {};
    if (fondu > 0) FONDU = fondu;
    for (const e of Object.keys(PLAC)) {
      const el = curseur(e), ctl = el && el.closest('.ctl');
      if (ctl && !ctl.querySelector('.ctl-glisse')) poignee(e, ctl);
    }
    ecrire(false);
  }
  return {placables, morceau, toutAZero, liste: () => blocs, rangs: () => nRangs,
          choisi: () => choisi, trouver, choisir, memoriser, deplacer,
          fini: changer, teinte, nom, net, detail, decrire,
          fondu: () => FONDU, placerInsp};
})();

/* ---------- les masques de l'apercu ----------

   La video de fond est ce qui coute le plus a chaque image (~180 ms, contre
   ~35 sans elle, en 480 x 270) : la masquer le temps de regler les effets et
   d'ecouter rend l'apercu fluide. La machine ne coute presque rien ; la
   masquer degage la vue sur ce qui se passe autour. Ni l'un ni l'autre ne
   touche l'export : les masques ne passent que par les apercus. */
var MASQUES = (() => {
  const CLE = 'omnipotard.masques';
  const m = {fond: false, machine: false};
  try { Object.assign(m, JSON.parse(localStorage.getItem(CLE) || '{}')); } catch (e) {}
  const bf = $('#masqueFond'), bm = $('#masqueMachine');
  const fond = () => !!m.fond && fonds.length > 0;
  function maj() {
    bf.disabled = !fonds.length;
    bf.classList.toggle('on', fond());
    bm.classList.toggle('on', !!m.machine);
    bf.setAttribute('aria-pressed', String(fond()));
    bm.setAttribute('aria-pressed', String(!!m.machine));
    bf.querySelector('span').textContent = fond() ? 'fond masqué' : 'fond';
    bm.querySelector('span').textContent = m.machine ? 'machine masquée' : 'machine';
    bf.title = !fonds.length ? 'pas de vidéo ni de photo de fond à masquer'
      : fond() ? 'remontrer le fond dans l\'aperçu'
      : 'masquer la vidéo ou la photo de fond dans l\'aperçu, pour qu\'il suive mieux le son (l\'export la garde)';
    bm.title = m.machine ? 'remontrer la machine dans l\'aperçu'
      : 'masquer la machine dans l\'aperçu (l\'export la garde)';
    $('#masques').classList.toggle('actif', fond() || !!m.machine);
    const avis = $('#masqueAvis');
    avis.hidden = !(fond() || m.machine);
    avis.textContent = fond() && m.machine
      ? 'fond et machine masqués dans l\'aperçu — l\'export les garde'
      : fond() ? 'fond masqué dans l\'aperçu — l\'export le garde'
      : 'machine masquée dans l\'aperçu — l\'export la garde';
  }
  for (const [b, k] of [[bf, 'fond'], [bm, 'machine']]) {
    // l'ecran prend les clics (choisir un morceau) : pas ceux-ci
    b.addEventListener('pointerdown', e => e.stopPropagation());
    b.addEventListener('click', e => {
      e.stopPropagation();
      m[k] = !m[k];
      try { localStorage.setItem(CLE, JSON.stringify(m)); } catch (e2) {}
      maj();
      shot();
    });
  }
  // les reglages d'un apercu, masques
  function appliquer(p) {
    if (fond()) p.set('backdrop', '');
    if (m.machine) p.set('presence', '0');
    return p;
  }
  maj();
  return {appliquer, maj};
})();

/* ---------- ecouter : le son dans la page, l'apercu qui suit ----------

   Le navigateur joue le morceau. L'apercu demande au studio des images plus
   petites (480 x 270, en JPEG), une a la fois, chacune pour l'instant que le
   son aura atteint quand elle arrivera : le temps d'une image se mesure au
   fil de l'ecoute. La barre d'espace lance et arrete ; la frise et les sauts
   deplacent le son. */
var ECOUTE = (() => {
  const son = $('#son'), bouton = $('#ecoute'), toile = $('#ecranDirect');
  const badge = $('#direct'), dessin = toile.getContext('2d');
  const W = 480, H = 270;
  let actif = false, tour = 0, raf = 0, avance = 0.15, wav = false, vues = [];
  // deux images en route a la fois : le studio en dessine une pendant que
  // l'autre voyage. Une image partie avant la derniere montree est jetee.
  let demandees = 0, montree = 0;
  function bascule(ic, titre) {
    bouton.innerHTML = '<i class="ic">' + icone(ic) + '</i>';
    bouton.title = titre;
  }
  function source() {
    son.src = '/audio?track=' + encodeURIComponent(track) + (wav ? '&wav=1' : '');
    son.load();
  }
  function morceau() {
    arreter(false);
    cacher();
    wav = false;
    source();
    bouton.disabled = false;
  }
  // un format que le navigateur ne lit pas (aiff, wma...) : le studio le
  // lui decode une fois en WAV
  son.addEventListener('error', () => {
    if (!track || wav) return;
    wav = true;
    source();
  });
  async function demarrer() {
    if (!track || actif) return;
    // un seul son a la fois : l'extrait anime se tait, son calcul s'arrete
    if (!$('#clipprog').hidden && !$('#clipStop').disabled) $('#clipStop').click();
    rendreLImage();
    const t = Math.max(0, Math.min(+$('#scrub').value || 0, duration - 0.5));
    for (let essai = 0; ; essai++) {
      try {
        son.currentTime = t;
        await son.play();
        break;
      } catch (e) {
        if (essai === 0 && !wav && e.name === 'NotSupportedError') { wav = true; source(); continue; }
        setStatus('le son ne part pas : ' + e.message, true);
        return;
      }
    }
    actif = true;
    vues = [];
    bouton.classList.add('on');
    bascule('pause', 'arrêter l\'écoute (barre d\'espace)');
    badge.textContent = 'direct';
    badge.hidden = false;
    suivre();
    const n = ++tour;
    image(n);
    setTimeout(() => image(n), 25);
  }
  function arreter(rendre = true) {
    if (!actif) return;
    actif = false;
    tour++;
    son.pause();
    cancelAnimationFrame(raf);
    bouton.classList.remove('on');
    bascule('play', 'écouter le morceau : l\'aperçu suit le son (barre d\'espace)');
    badge.hidden = true;
    FRISE.tetes();
    // l'image entiere, la ou l'on s'est arrete ; la derniere de l'ecoute
    // reste en place jusqu'a ce qu'elle arrive
    if (rendre) shot();
  }
  function cacher() { toile.hidden = true; }
  // la tete de lecture suit le son
  function suivre() {
    if (!actif) return;
    const s = $('#scrub'), t = son.currentTime;
    s.value = Math.min(+s.max, t).toFixed(2);
    majTemps();
    FRISE.montrer(t);
    raf = requestAnimationFrame(suivre);
  }
  async function image(n) {
    if (!actif || n !== tour) return;
    const p = MASQUES.appliquer(params());
    const t = Math.max(0, Math.min(son.currentTime + (son.paused ? 0 : avance), duration - 0.9));
    p.set('t', t.toFixed(3)); p.set('w', W); p.set('h', H); p.set('rapide', '1');
    const t0 = performance.now(), numero = ++demandees;
    let pause = 0;
    try {
      const r = await fetch('/still?' + p.toString());
      if (r.status === 204) {
        // depassee par une autre demande : rien a montrer
      } else if (r.ok) {
        const im = await createImageBitmap(await r.blob());
        // le temps d'une image, lisse : c'est l'avance a prendre sur le son
        avance = Math.min(1.5, 0.7 * avance + 0.3 * (performance.now() - t0) / 1000);
        if (actif && n === tour && numero > montree) {
          montree = numero;
          if (toile.width !== im.width) toile.width = im.width;
          if (toile.height !== im.height) toile.height = im.height;
          dessin.drawImage(im, 0, 0);
          toile.hidden = false;
          $('#ecranVide').hidden = true;
          $('#ecran').classList.remove('vide');
          $('#shoterr').classList.remove('on');
          compter();
        }
        if (im.close) im.close();
      } else {
        let m = 'erreur ' + r.status;
        try { m = (await r.json()).error || m; } catch (e) { /* pas du JSON */ }
        $('#shoterr').textContent = "L'aperçu en direct n'a pas pu être calculé : " + m;
        $('#shoterr').classList.add('on');
        pause = 1000;
      }
    } catch (e) { pause = 500; }        // le studio ne repond plus : on reessaie
    if (actif && n === tour) setTimeout(() => image(n), pause);
  }
  // combien d'images sont arrivees dans la derniere seconde
  function compter() {
    const m = performance.now();
    vues.push(m);
    while (vues.length && m - vues[0] > 1000) vues.shift();
    badge.textContent = 'direct · ' + vues.length + ' i/s';
  }
  son.addEventListener('ended', () => arreter(true));
  bouton.onclick = () => (actif ? arreter(true) : demarrer());
  // La barre d'espace lance et arrete, d'ou que l'on soit — sauf dans un
  // champ de saisie, et sur un bouton atteint au clavier, qui la garde.
  document.addEventListener('keydown', e => {
    if (e.key !== ' ' || e.ctrlKey || e.metaKey || e.altKey) return;
    const c = e.target;
    if (c.matches && c.matches('input[type=text], input[type=number], textarea, select')) return;
    if (c.matches && c.matches('button, input[type=checkbox]') && c.matches(':focus-visible')) return;
    if (!track || bouton.disabled || !$('#styles').hidden) return;
    e.preventDefault();
    if (e.repeat) return;
    // un bouton clique a la souris garde le focus : sans cela, la barre
    // d'espace le recliquerait en plus
    if (c !== document.body && c.blur) c.blur();
    if (actif) arreter(true); else demarrer();
  }, true);
  return {morceau, demarrer, arreter, cacher, actif: () => actif,
          chercher: t => { if (actif) son.currentTime = t; }};
})();

(function () {
  let k = null;
  try { k = localStorage.getItem(CLE_SECTION); } catch (e) {}
  montrerSection(k || 'prereglage');
})();
majTemps();
</script>
</body></html>
"""

# Le sequenceur et le depot de melodie sont poses a leur place dans la page.
PAGE = PAGE.replace("/*__SEQ_MIDI__*/", JS_SEQ_MIDI + "\nseqBrancher();\n")



def check_deps():
    """Verifie ffmpeg avant d'ouvrir la page.

    Le studio est fait pour etre lance sur sa propre machine, souvent sans
    rien y avoir installe : autant le dire clairement tout de suite plutot
    que de laisser le premier rendu echouer.
    """
    import shutil as sh
    if sh.which("ffmpeg") and sh.which("ffprobe"):
        return
    aide = {
        "darwin": "brew install ffmpeg",
        "win32": "winget install Gyan.FFmpeg   (ou https://ffmpeg.org/download.html)",
    }.get(sys.platform, "sudo apt install ffmpeg")
    sys.exit("ffmpeg est introuvable — le studio en a besoin pour lire vos "
             "morceaux et encoder les videos.\n  A installer avec :  %s" % aide)


# Ce que les versions passees ont laisse et qui n'existe plus. La mise a jour
# remplace et ajoute des fichiers, elle n'en retire aucun — et elle ne se
# remplace pas elle-meme : c'est donc le studio qui fait le menage.
OBSOLETES = ("tools/studio_v2.py",)
LANCEURS_OBSOLETES = ("Lancer-le-studio-v2.bat", "Lancer-le-studio-v2.command")


def retirer_obsoletes(par_v2=False):
    """Retire les fichiers des versions passees qui n'ont plus d'usage.

    Un lanceur ne s'efface pas pendant qu'il tourne : Windows et bash lisent
    leur script au fur et a mesure, et le retirer sous leurs pieds finirait
    sur une erreur. Lance par l'un d'eux, le studio le garde pour la fois
    suivante et dit lequel ouvrir.
    """
    partis = []
    for rel in OBSOLETES + (() if par_v2 else LANCEURS_OBSOLETES):
        p = os.path.join(ROOT, *rel.split("/"))
        if os.path.isfile(p):
            try:
                os.remove(p)
                partis.append(rel)
            except OSError:
                pass
    cache = os.path.join(ROOT, "tools", "__pycache__")
    if os.path.isdir(cache):
        for n in os.listdir(cache):
            if n.startswith("studio_v2."):
                try:
                    os.remove(os.path.join(cache, n))
                except OSError:
                    pass
    if partis:
        print("anciens fichiers retires (la v2 n'existe plus) : %s"
              % ", ".join(partis))
    if par_v2:
        print("Ce lanceur « v2 » n'est plus utile : la prochaine fois, ouvrez "
              "Lancer-le-studio. Il sera retire a ce moment-la.")
    return partis


def main():
    ap = argparse.ArgumentParser(description="Studio local Omnipotard")
    ap.add_argument("--port", type=int, default=8765)
    ap.add_argument("--host", default="127.0.0.1",
                    help="127.0.0.1 par defaut : rien n'est expose au reseau")
    ap.add_argument("--no-browser", action="store_true")
    # il n'y a plus qu'une page ; l'option reste acceptee pour que les
    # anciens lanceurs et raccourcis continuent de marcher
    ap.add_argument("--v2", action="store_true", help=argparse.SUPPRESS)
    ap.add_argument("track", nargs="?", help="morceau a charger au demarrage")
    args = ap.parse_args()

    check_deps()
    retirer_obsoletes(par_v2=args.v2)
    # les vignettes de fond laissees par les rendus d'avant : les versions
    # precedentes n'en effacaient aucune, et elles finissaient par remplir le
    # disque (voir SuiteDeFonds)
    libere = menage_fonds()
    if libere > 50e6:
        print("  menage : %.1f Go de vignettes de fond laissees par d'anciens "
              "rendus ont ete effaces" % (libere / 1e9), flush=True)
    lancer_exemples()
    os.makedirs(UPLOADS, exist_ok=True)
    if args.track:
        tid, info = STUDIO.add_track(os.path.abspath(args.track),
                                     safe_name(args.track))
        print("morceau pre-charge : %s (%.1f BPM, %d paroxysmes)"
              % (args.track, info["bpm"], len(info["drops"])))

    srv = ThreadingHTTPServer((args.host, args.port), Handler)
    url = "http://%s:%d" % (args.host, args.port)
    print("Studio Omnipotard %s" % version())
    print("  ->  %s" % url)
    print("Ctrl-C pour arreter. Les videos sont ecrites dans out/studio/.")
    if not args.no_browser:
        threading.Timer(0.6, lambda: webbrowser.open(url)).start()
    try:
        srv.serve_forever()
    except KeyboardInterrupt:
        print("\narret.")


if __name__ == "__main__":
    main()
