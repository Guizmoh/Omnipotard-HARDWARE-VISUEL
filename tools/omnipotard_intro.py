#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
OMNIPOTARD — generateur d'intro video "oscilloscope"

Sequence (12,8 s = 4 mesures a 75 BPM, tout est cale sur la grille musicale) :
  1. Amorce      : la trace se stabilise sur la ligne de base.
  2. Balayage    : un balayage d'oscillateur (vert fluo) dessine une MPC Live III.
                   Il se termine pile sur le drop, au debut de la mesure 2.
  3. Groove dub  : la machine joue — pads, bande de 16 pas, potards et ecran
                   bougent sur les evenements reels de la bande-son.
  4. Dissolution : la machine fond dans la forme d'onde de la musique.
  5. Titre       : la courbe audio ecrit OMNIPOTARD en un seul trait continu ;
                   le nom reste dedans, vibrant avec le son.
  6. Extinction  : collapse cathodique.

L'image est pilotee par le son : la courbe est la forme d'onde reelle du
morceau, et chaque pad s'allume sur l'evenement qui le declenche.

Rendu image par image, sans etat partage entre frames -> parallelisable.
"""

import argparse
import math
import os
import re
import subprocess
import sys
import tempfile
import unicodedata
import wave

import numpy as np

# Version du code, affichee par le studio et rappelee dans ses messages
# d'erreur. Elle ne depend pas de git : le dossier est souvent recupere en
# archive zip, sans historique, et Windows n'a pas git installe d'origine.
# Sans ce reperage, impossible de savoir si une correction est bien arrivee.
VERSION = "2026-09-18.2"

# --------------------------------------------------------------------------
# Repere : unite = demi-hauteur de l'image. y vers le haut, centre en (0, 0).
# En 16/9 la zone visible est x dans [-1.78, 1.78], y dans [-1, 1].
# --------------------------------------------------------------------------

# Palettes : coeur du trait, halo, coeur sur-expose, fond de dalle.
PALETTES = {
    "vert":      ((0.24, 1.00, 0.16), (0.10, 1.00, 0.34), (0.85, 1.00, 0.88), (0.0, 0.0, 0.0)),
    "orange":    ((1.00, 0.45, 0.07), (1.00, 0.11, 0.02), (1.00, 0.93, 0.80), (0.0, 0.0, 0.0)),
    "bleu":      ((0.22, 0.66, 1.00), (0.05, 0.26, 1.00), (0.86, 0.96, 1.00), (0.0, 0.0, 0.0)),
    "bleu-fond": ((0.62, 0.90, 1.00), (0.14, 0.48, 1.00), (0.92, 0.98, 1.00),
                  (0.022, 0.066, 0.168)),
}

# Fonds de dalle. Le faisceau est additif : un fond clair mange le contraste
# du trait. Toutes ces textures restent donc sombres, et se creusent derriere
# la machine (voir `clear`) pour qu'elle garde son relief.
BACKGROUNDS = ("noir", "uni", "grille", "points", "scan", "degrade", "bruit")


TRAVELLINGS = ("aucun", "avant", "arriere", "gauche", "droite", "haut", "bas")

# Part de l'effet qui ne depend pas de la force du coup. Une avarie d'image
# doit frapper franchement ou ne rien faire : la doser au prorata d'une force
# qui vaut 0,3 sur un mixage sage la rendrait invisible a tous les reglages.
PLANCHER_AVARIE = 0.45


# Comment encoder la video finale. Mesure sur un extrait 1080p, en comparant
# chaque encodage aux images brutes : le trait etant fin, vif et pose sur du
# noir, l'essentiel de son signal est dans la couleur — et le 4:2:0, qui n'en
# garde qu'un quart, l'abime bien plus que la quantification. Passer de CRF 20
# a CRF 8 en 4:2:0 ne gagne que 1,7 dB pour sept fois le poids ; passer en
# 4:4:4 en gagne 4,4 pour deux fois le poids. En revanche le 4:4:4 ne se lit
# ni sur un telephone, ni dans un navigateur : d'ou le choix laisse.
QUALITES = {
    "compatible": {"pix": "yuv420p", "profil": "high", "crf": 17,
                   "quoi": "lisible partout : telephones, navigateurs, reseaux"},
    "net": {"pix": "yuv444p", "profil": "high444", "crf": 16,
            "quoi": "trait bien plus net, mais VLC ou un logiciel de montage "
                    "seulement"},
    "master": {"pix": "yuv444p", "profil": "high444", "crf": 10,
               "quoi": "pour remonter la video ensuite ; fichier lourd"},
}


# L'apercu en mouvement ne sort pas du studio : il doit se lire dans le
# navigateur, tout de suite, et sa qualite n'a aucune importance. Le VP8 est
# choisi plutot que le H.264 parce qu'aucun navigateur ne le refuse — certains
# Chromium et Firefox sont construits sans H.264 — et parce qu'en mode
# « realtime » il encode plus vite que le rendu ne calcule les images.
# Le debit est fixe plutot que laisse a la qualite constante : mesure contre
# les images brutes, le VP8 « qualite constante » ne rendait que 24,1 dB — un
# trait fin et grene est cher a encoder, et le codec choisissait d'y renoncer.
# A 3 Mbit/s il rend 28,3 dB pour un fichier de 1,5 Mo par tranche de quatre
# secondes, qui ne quitte jamais la machine. Le calcul des images coute de
# toute facon dix fois plus que l'encodage.
APERCU = {
    "ext": ".webm",
    "video": ["-c:v", "libvpx", "-deadline", "realtime", "-cpu-used", "6",
              "-b:v", "3M", "-pix_fmt", "yuv420p"],
    "audio": ["-c:a", "libopus", "-b:a", "96k"],
}


def apercu_possible():
    """Vrai si ffmpeg sait encoder du VP8 et de l'Opus.

    Les versions de ffmpeg livrees sur Windows les ont toutes, mais une
    installation minimale peut en manquer : mieux vaut retomber sur le MP4
    que faire echouer l'apercu avec une ligne de commande incomprehensible.
    """
    global _APERCU_OK
    if _APERCU_OK is None:
        try:
            out = subprocess.run(["ffmpeg", "-hide_banner", "-encoders"],
                                 stdout=subprocess.PIPE,
                                 stderr=subprocess.DEVNULL).stdout.decode(
                                     "utf-8", "replace")
            _APERCU_OK = ("libvpx" in out) and ("libopus" in out)
        except Exception:                                 # noqa: BLE001
            _APERCU_OK = False
    return _APERCU_OK


_APERCU_OK = None


# ==========================================================================
#  Prereglages
#
#  Un point de depart par famille de musique, pas une verite : chaque reglage
#  reste bougeable ensuite. Ils sont ecrits avec les noms du moteur, et c'est
#  CHAMPS, plus bas, qui dit a quel curseur du studio chacun correspond.
# ==========================================================================

PRESETS = {
    "propre": {},

    "drum and bass": {
        "punch": 0.09, "punch_on": "grosse caisse",
        "shake_amp": 0.5, "shake_on": "grosse caisse",
        "ring": 1.0, "ring_on": "grosse caisse",
        "parts": 1.2, "parts_n": 600, "parts_on": "caisse claire",
        "grid_pulse": 1.2, "grid_on": "grosse caisse",
        "tranches": 0.6, "tranches_on": "caisse claire",
        "split": 1.2, "split_count": 4, "trail": 1.4, "wave_gain": 1.30,
        "glitch": 0.6, "step_div": 4,
    },

    "dub": {
        "echo": 0.60, "echo_n": 4, "echo_delay": 0.090,
        "trail": 2.0, "halo_doux": 0.5, "flottement": 0.5,
        "wave_gain": 0.90, "punch": 0.05,
        "ring": 0.8, "ring_on": "grosse caisse",
        "couleurs": 0.5, "glitch": 0.3, "split": 0.8, "step_div": 1,
    },

    "idm": {
        "spectro": 1.0, "couleurs": 1.1,
        "miroir": 1.0, "miroir_on": "percussions",
        "ondul": 0.9, "ondul_on": "basse",
        "mosaic": 0.8, "mosaic_on": "caisse claire",
        "stut": 0.18, "stut_loop": 0.05, "stut_on": "charley",
        "scramble": 0.25, "scr_len": 0.16,
        "parts": 1.0, "parts_n": 2000, "parts_on": "caisse claire",
        "split": 1.5, "glitch": 0.8,
    },

    "trip hop": {
        "cadence": 3, "halo_doux": 1.1, "poussiere": 1.2, "flottement": 1.0,
        "trail": 1.8, "wave_gain": 0.80, "punch": 0.03,
        "palette": "orange", "glitch": 0.0, "split": 0.0,
        "backdrop_sharp": 0.75, "step_div": 1,
    },

    "hip hop": {
        "punch": 0.08, "punch_on": "grosse caisse",
        "snare": 1.4, "poussiere": 0.6, "halo_doux": 0.4, "cadence": 2,
        "trail": 1.2, "ring": 0.6, "ring_on": "grosse caisse",
        "glitch": 0.2, "split": 0.6, "split_count": 2,
    },

    "breakcore": {
        "kaleido": 1.2, "kaleido_on": "caisse claire",
        "cisaille": 1.1, "cisaille_on": "percussions",
        "coupure": 1.1, "coupure_on": "grosse caisse",
        "tapestop": 0.28, "tapestop_on": "grosse caisse",
        "scramble": 0.50, "scr_len": 0.13,
        "stut": 0.14, "stut_loop": 0.05, "stut_on": "charley",
        "tranches": 1.0, "tranches_on": "caisse claire",
        "invert": 1.0, "invert_on": "grosse caisse",
        "parts": 1.1, "parts_n": 1500, "parts_on": "caisse claire",
        "split": 1.4, "glitch": 1.0, "step_div": 4,
    },

    "techno": {
        "punch": 0.07, "punch_on": "grosse caisse",
        "grid_pulse": 2.0, "grid_on": "grosse caisse",
        "ring": 1.4, "ring_on": "grosse caisse",
        "coupure": 0.8, "coupure_on": "grosse caisse",
        "parts": 0.8, "parts_n": 400, "parts_on": "charley",
        "couleurs": 0.4, "split": 1.0, "glitch": 0.5, "wave_gain": 1.10,
    },

    "ambient": {
        "echo": 0.50, "echo_n": 4, "echo_delay": 0.120,
        "halo_doux": 1.4, "trail": 2.2, "spectro": 0.8,
        "flottement": 0.4, "wave_gain": 0.60, "punch": 0.01,
        "glitch": 0.0, "split": 0.0, "wave_smooth": 110, "step_div": 1,
    },
}

# ==========================================================================
#  Ce que fait chaque reglage, et a quelle frequence
#
#  AIDE donne une phrase par curseur, affichee sous lui dans le studio.
#  COMPTE dit comment estimer le nombre de declenchements par morceau :
#    "instrument"  autant de fois que l'instrument choisi frappe
#    "continu"     tout le temps, ce n'est pas un declenchement
#    "reglage"     un reglage de forme, il ne declenche rien par lui-meme
#    "split"       les quelques dedoublements, plafonnes par la duree
#    "drops"       les paroxysmes du morceau
#    "tranche"     par blocs de temps reguliers
# ==========================================================================

AIDE = {
    "machine": "La machine dessinee. Chacune a ses organes : les pads de la "
               "MPC, les touches du MiniFreak, les declencheurs du Digitakt. "
               "Les coups les allument de la meme facon. C'est celle du debut "
               "quand le sequenceur en fait venir d'autres.",
    "passage": "Le temps que met une machine a se deformer jusqu'a devenir la "
               "suivante. La deformation precede l'instant inscrit, de sorte "
               "que la machine est bien posee quand cet instant arrive. A "
               "zero, le changement est sec.",
    "passageTurb": "L'ondulation du trace pendant la deformation. A zero les "
                   "traits glissent proprement d'une forme a l'autre ; plus "
                   "haut, ils serpentent comme un faisceau derange.",
    "midiForce": "L'eclat des touches du clavier jouees par le fichier MIDI. "
                 "A zero le fichier est charge mais rien ne s'allume. La "
                 "melodie ne se joue que sur le MiniFreak : la MPC et le "
                 "Digitakt n'ont pas de clavier, leurs pads restent a la "
                 "batterie.",
    "midiOffset": "Avance ou retarde le fichier MIDI, en secondes, par "
                  "rapport au calage trouve tout seul. A utiliser si les "
                  "touches s'allument un peu avant ou un peu apres la "
                  "melodie entendue.",
    "preset": "Repose tous les curseurs sur un point de depart. Tout reste "
              "modifiable ensuite. « Mes reglages » sont les votres, gardes "
              "d'une fois sur l'autre.",
    "palette": "La teinte du trait. « perso » ouvre un nuancier libre.",
    "trait": "La couleur du trait quand la palette est « perso ».",
    "bg": "La texture de la dalle, derriere la machine.",
    "bgColor": "La couleur de cette texture.",
    "bgStrength": "Son intensite. Le faisceau etant additif, un fond clair "
                  "mange le contraste du trait.",
    "bgClear": "Creuse la texture derriere la machine pour qu'elle s'y detache.",
    "bdStrength": "La presence de l'image ou de la video de fond.",
    "bdClear": "Creuse l'image derriere la machine, comme pour la texture.",
    "screenDim": "L'opacite de la dalle de la MPC. A zero, le fond se voit au "
                 "travers et l'ecran a l'air en verre.",
    "bdSharp": "De 0 (fondu, quart de definition) a 1 (net, pleine "
               "definition). Un fond net mange le contraste du trait.",
    "travel": "Quelle part de l'image est parcourue du debut a la fin du "
              "morceau. Vingt pour cent suffisent.",
    "travelMode": "Le sens du deplacement : on entre dans l'image, on s'en "
                  "eloigne, ou on la balaye.",
    "split": "Le trait se separe en trois copies decalees, rouge et bleu, puis "
             "se recolle. La liste dit quels coups ont le droit de le lancer.",
    "splitCount": "Combien de fois au plus dans la video. Deux dedoublements "
                  "ne peuvent pas tomber a moins de 25 secondes.",
    "splitPx": "L'ecart entre les trois copies, en pixels.",
    "wobble": "Fait onduler le trace de la machine en permanence. A zero, "
              "trait net.",
    "snare": "La caisse claire embrase le trait en jaune et enfle son halo.",
    "wave": "L'amplitude de la courbe sonore derriere la machine.",
    "wavePunch": "De combien cette courbe gonfle sur les temps forts.",
    "waveSmooth": "Lissage de la courbe : large, elle suit le grave et se "
                  "calme ; etroit, elle tremble au detail.",
    "trail": "La trainee que laisse la courbe. Elle s'allonge quand plusieurs "
             "instruments jouent ensemble.",
    "glitch": "Les rafales de tranches decalees sur les montees du morceau.",
    "title": "Le nom ecrit sur la dalle. Vide, c'est celui du fichier.",
    "punch": "L'image respire : un zoom bref sur chaque coup.",
    "shake": "L'image est bousculee d'un cran sur chaque coup.",
    "parts": "L'eclat des braises ejectees. Elles naissent des traits memes de "
             "la machine et partent perpendiculairement.",
    "partsN": "Combien de braises par coup. Leur eclat baisse a mesure "
              "qu'elles se multiplient.",
    "partsSpeed": "Jusqu'ou elles filent avant de s'eteindre.",
    "partsLife": "Combien de temps elles restent visibles.",
    "ring": "Un anneau s'ouvre depuis la machine et s'efface.",
    "gridPulse": "La grille du fond s'allume sur le coup.",
    "bgFlash": "L'image de fond est eclairee comme par un flash. Sans image de "
               "fond, rien ne se voit.",
    "tranches": "Des bandes horizontales de l'image partent de travers.",
    "blocs": "Des rectangles sont pris ailleurs dans l'image et recopies.",
    "roll": "Le tube perd sa synchro : l'image saute, avec sa barre de couture.",
    "ghost": "Une copie decalee et transparente se superpose a l'image.",
    "invert": "Le coeur du trait se replie vers le sombre en gardant ses bords "
              "lumineux.",
    "stut": "L'image decroche du son et rejoue en boucle un bout pris a "
            "l'instant du coup. Le son, lui, continue.",
    "stutLoop": "La longueur du bout rejoue. Sous une image, c'est un gel pur ; "
                "deux ou trois images donnent un sursaut repete.",
    "miroir": "L'image se replie sur elle-meme, en largeur ou en hauteur.",
    "ondul": "Le balayage ondule et la machine semble fondre.",
    "mosaic": "L'image tombe en gros pixels.",
    "kaleido": "L'image repetee en grille, un carreau sur deux retourne.",
    "cisaille": "L'image penche d'un bloc, comme cisaillee.",
    "coupure": "L'image s'absente, deux images durant.",
    "tapestop": "Le temps ralentit puis rattrape d'un coup, comme une bande "
                "qui patine.",
    "scramble": "Le temps decoupe en blocs et rejoue dans le desordre, "
                "pendant que le son continue tout droit.",
    "scrLen": "La longueur d'un bloc. Court, ca hache ; long, ca desoriente.",
    "echo": "La machine telle qu'elle etait il y a quelques centiemes, de plus "
            "en plus pale, dessinee sous l'image du moment.",
    "echoN": "Combien d'echos empiles.",
    "echoDelay": "L'ecart entre deux echos.",
    "couleurs": "Le trait prend la teinte du dernier instrument frappe : rouge "
                "la grosse caisse, jaune la caisse claire, cyan le charley, "
                "violet la basse.",
    "spectro": "Les trois dernieres secondes du morceau deroulees sur la "
               "dalle, une ligne par bande de frequences. Baisser l'amplitude "
               "de la courbe pour bien le voir.",
    "cadence": "Chaque image gardee plusieurs fois : la video avance par "
               "paliers sans rien ralentir.",
    "haloDoux": "Les noirs remontent et la lumiere s'etale, a l'oppose du "
                "contraste franc de l'oscilloscope.",
    "poussiere": "Grains, rayures verticales et cheveux de pellicule.",
    "flottement": "Lent va-et-vient de l'image, comme une cassette fatiguee.",
    "taille": "La place que prend la machine dans l'image. En la reduisant "
              "on decouvre le fond autour d'elle ; le quadrillage et le fil "
              "du morceau, eux, gardent la largeur de l'ecran.",
    "presence": "L'eclat de la machine. En la baissant elle s'efface derriere "
                "le fond sans disparaitre, comme un reflet sur une vitre.",
    "neon": "La force avec laquelle le neon eclaire ce qui l'entoure. Le "
            "trait lui-meme ne change pas : c'est la lumiere qu'il jette "
            "autour de lui qui monte ou descend.",
    "reflet": "A quelle distance se tient la surface qui renvoie cette "
              "lumiere. Collee, la lueur est serree et vive ; lointaine, elle "
              "s'etale et palit.",
    "tube": "Donne au trait l'epaisseur d'un tube de verre : les bords "
            "s'assombrissent et un reflet file le long de son arete haute.",
    "bgAnim": "Fait vivre la texture du fond : les lignes et le quadrillage "
              "descendent, le grain bout comme une pellicule. A zero, la "
              "texture est fixe. Sans effet sur « uni » et « degrade », qui "
              "n'ont rien a faire defiler.",
    "nettete": "La finesse du trait lui-meme. A 1 il est large et velours ; "
               "plus haut il se resserre, jusqu'a un cheveu de lumiere. Le "
               "gain de nettete se voit surtout en 1080p et au-dessus.",
    "stepDiv": "La vitesse a laquelle la rangee de pas, en haut de la "
               "machine, avance d'une case.",
    "quality": "Comment le fichier est encode. « compatible » menage les "
               "telephones et les navigateurs ; les deux autres gardent la "
               "couleur du trait intacte mais ne se lisent que sur "
               "ordinateur.",
    "size": "La definition de la video finale. La 4K demande beaucoup de "
            "memoire et de temps.",
    "fps": "Images par seconde. 30 suffit ; 60 adoucit les mouvements rapides.",
    "scrub": "L'instant du morceau que montre l'apercu.",
}

COMPTE = {
    "punch": "instrument", "shake": "instrument", "parts": "instrument",
    "ring": "instrument", "gridPulse": "instrument", "bgFlash": "instrument",
    "tranches": "instrument", "blocs": "instrument", "roll": "instrument",
    "ghost": "instrument", "invert": "instrument", "stut": "instrument",
    "miroir": "instrument", "ondul": "instrument", "mosaic": "instrument",
    "kaleido": "instrument", "cisaille": "instrument", "coupure": "instrument",
    "tapestop": "instrument",
    "split": "split", "glitch": "drops", "scramble": "tranche",
    # la rangee de pas n'attend aucun coup : elle avance au tempo
    "stepDiv": "sequenceur",
    # pas une frequence : ce que coute et ce que rend l'encodage choisi
    "quality": "qualite",
    # L'eclair jaune n'a pas de selecteur : il est cable sur la caisse claire
    # et les percussions. Une liste de familles dit lesquelles compter.
    "snare": ["caisse claire", "percussions"],
    "echo": "continu", "couleurs": "continu", "spectro": "continu",
    "cadence": "continu", "haloDoux": "continu", "poussiere": "continu",
    "flottement": "continu", "travel": "continu", "wobble": "continu",
    "trail": "continu", "wave": "continu",
}

# Le curseur du studio qui porte chaque reglage. Sert a poser un prereglage
# depuis la page ; un controle automatique verifie que chaque nom existe des
# deux cotes, faute de quoi un prereglage poserait des valeurs dans le vide.
CHAMPS = {
    "palette": "palette", "split": "split", "split_count": "splitCount",
    "split_on": "splitOn", "wobble": "wobble", "split_px": "splitPx",
    "snare": "snare", "wave_gain": "wave", "wave_punch": "wavePunch",
    "nettete": "nettete", "step_div": "stepDiv",
    "taille": "taille", "presence": "presence",
    "neon": "neon", "reflet": "reflet", "tube": "tube", "bg_anim": "bgAnim",
    "passage": "passage", "passage_turb": "passageTurb",
    "midi_force": "midiForce", "midi_offset": "midiOffset",
    "wave_smooth": "waveSmooth", "trail": "trail", "glitch": "glitch",
    "punch": "punch", "punch_on": "punchOn",
    "shake_amp": "shake", "shake_on": "shakeOn",
    "parts": "parts", "parts_on": "partsOn", "parts_n": "partsN",
    "parts_speed": "partsSpeed", "parts_life": "partsLife",
    "ring": "ring", "ring_on": "ringOn",
    "grid_pulse": "gridPulse", "grid_on": "gridOn",
    "bg_flash": "bgFlash", "flash_on": "flashOn",
    "travel": "travel", "travel_mode": "travelMode",
    "backdrop_sharp": "bdSharp", "screen_dim": "screenDim",
    "tranches": "tranches", "tranches_on": "tranchesOn",
    "blocs": "blocs", "blocs_on": "blocsOn",
    "roll": "roll", "roll_on": "rollOn",
    "ghost": "ghost", "ghost_on": "ghostOn",
    "invert": "invert", "invert_on": "invertOn",
    "stut": "stut", "stut_on": "stutOn", "stut_loop": "stutLoop",
    "scramble": "scramble", "scr_len": "scrLen",
    "miroir": "miroir", "miroir_on": "miroirOn",
    "ondul": "ondul", "ondul_on": "ondulOn",
    "mosaic": "mosaic", "mosaic_on": "mosaicOn",
    "kaleido": "kaleido", "kaleido_on": "kaleidoOn",
    "cisaille": "cisaille", "cisaille_on": "cisailleOn",
    "coupure": "coupure", "coupure_on": "coupureOn",
    "tapestop": "tapestop", "tapestop_on": "tapestopOn",
    "cadence": "cadence", "poussiere": "poussiere",
    "flottement": "flottement", "halo_doux": "haloDoux",
    "echo": "echo", "echo_n": "echoN", "echo_delay": "echoDelay",
    "couleurs": "couleurs", "spectro": "spectro",
}


def _sample2d(src, xs, ys):
    """Echantillonnage bilineaire aux coordonnees demandees, axe par axe.

    Le cadre n'est jamais tourne : echantillonner les lignes puis les colonnes
    donne exactement le meme resultat qu'un filtrage bilineaire complet, pour
    deux gathers au lieu de quatre.
    """
    hs, ws = src.shape[:2]
    x0 = np.clip(np.floor(xs), 0, ws - 2).astype(np.int32)
    y0 = np.clip(np.floor(ys), 0, hs - 2).astype(np.int32)
    fx = (xs - x0)[None, :, None]
    fy = (ys - y0)[:, None, None]
    lig = src[y0] * (1.0 - fy) + src[y0 + 1] * fy           # (h, ws, 3)
    return (lig[:, x0] * (1.0 - fx) + lig[:, x0 + 1] * fx).astype(np.float32)


def travel_crop(src, u, amount, mode, w, h):
    """Le cadre de l'instant u, decoupe dans une image chargee avec de la marge.

    C'est le travelling : la photo est plus grande que l'ecran, et on s'y
    promene du debut a la fin du morceau — on entre dedans, on s'en eloigne,
    ou on la balaye. Une image fixe derriere une machine qui bouge finit par
    sembler collee ; un mouvement lent, meme de quelques pour cent, suffit a
    lui rendre de la profondeur.
    """
    hs, ws = src.shape[:2]
    if amount <= 1e-4 or mode == "aucun" or (hs <= h and ws <= w):
        return src[:h, :w]
    u = min(max(float(u), 0.0), 1.0)
    mx, my = ws - w, hs - h                     # marge disponible, en pixels
    if mode in ("avant", "arriere"):
        k = u if mode == "avant" else 1.0 - u   # avant : le cadre se resserre
        cw, ch = ws - mx * k, hs - my * k
        x0, y0 = (ws - cw) * 0.5, (hs - ch) * 0.5
    else:
        cw, ch = float(w), float(h)
        x0, y0 = mx * 0.5, my * 0.5
        if mode == "droite":
            x0 = mx * u
        elif mode == "gauche":
            x0 = mx * (1.0 - u)
        elif mode == "bas":
            y0 = my * u
        elif mode == "haut":
            y0 = my * (1.0 - u)
    xs = np.linspace(x0, x0 + cw - 1.0, w, dtype=np.float32)
    ys = np.linspace(y0, y0 + ch - 1.0, h, dtype=np.float32)
    return _sample2d(src, xs, ys)


def backdrop_quality(sharp):
    """Flou et definition des vignettes, du fond fondu au fond net.

    Le faisceau etant additif, une image nette et claire derriere le trait lui
    mange son contraste : d'ou un fond volontairement flou et sous-echantillonne
    par defaut. Mais c'est un parti pris, pas une fatalite — a 1, l'image passe
    telle quelle, en pleine definition, et reste lisible.

    La valeur par defaut, 0,37, reproduit exactement l'ancien comportement.
    """
    sharp = min(max(float(sharp), 0.0), 1.0)
    blur = 4.2 * (1.0 - sharp) ** 1.4
    div = (4, 3, 2, 1)[min(3, int(sharp * 4.0))]
    return blur, div


def _backdrop_mask(w, h, strength, clear, scale, screen_dim, ecran=None):
    """Le multiplicateur applique au fond : dosage, creux derriere la machine,
    et dalle opaque. Il ne depend que du format, donc on le calcule une fois —
    y compris pour une video, ou il servira sur chaque image."""
    sc = scale if scale else min(h * 0.5, w * 0.5 / 1.30)
    yy = np.arange(h, dtype=np.float32)[:, None]
    xx = np.arange(w, dtype=np.float32)[None, :]
    m = np.full((h, w), float(strength), dtype=np.float32)
    trou = creux_machine(w, h, clear, scale)
    if trou is not None:
        m *= trou
    if screen_dim > 0:
        # la dalle est opaque : sans cela le ciel de la photo passe au travers
        # et l'ecran de la machine a l'air d'etre en verre.
        ecr = ecran if ecran else SCREEN
        px0 = w * 0.5 + ecr[0] * sc
        px1 = w * 0.5 + ecr[2] * sc
        py0 = h * 0.5 - ecr[3] * sc               # l'axe y est inverse a l'ecran
        py1 = h * 0.5 - ecr[1] * sc
        soft = max(2.0, 0.018 * sc)
        mx = np.clip(np.minimum(xx - px0, px1 - xx) / soft, 0.0, 1.0)
        my = np.clip(np.minimum(yy - py0, py1 - yy) / soft, 0.0, 1.0)
        k = mx * my
        m *= 1.0 - float(screen_dim) * (k * k * (3.0 - 2.0 * k))
    return m[..., None]


class StillBackdrop:
    """Fond fixe : la meme image sur toute la video, eventuellement parcourue.

    L'image et le masque sont gardes separes. Le masque — le creux derriere la
    machine et la dalle opaque — appartient a l'ecran, pas a la photo : si on
    le multipliait une fois pour toutes, le creux se promenerait avec l'image
    pendant le travelling.
    """

    def __init__(self, img, mask, dur=1.0, travel=0.0, travel_mode="avant"):
        self.img, self.mask = img, mask
        self.dur = max(float(dur), 1e-3)
        self.travel, self.mode = float(travel), travel_mode
        self.h, self.w = mask.shape[0], mask.shape[1]
        self._fixe = None

    def at(self, t):
        if self.travel <= 1e-4 or self.mode == "aucun":
            if self._fixe is None:
                self._fixe = np.ascontiguousarray(
                    self.img[:self.h, :self.w] * self.mask, dtype=np.float32)
            return self._fixe
        cadre = travel_crop(self.img, t / self.dur, self.travel, self.mode,
                            self.w, self.h)
        return np.ascontiguousarray(cadre * self.mask, dtype=np.float32)


def load_backdrop(path, w, h, strength=0.80, clear=0.45, scale=None, blur=2.2,
                  screen_dim=0.40, seek=0.0, travel=0.0, travel_mode="avant",
                  dur=1.0, ecran=None):
    """Charge une image de fond et la prepare pour la dalle.

    Passe par ffmpeg, donc accepte tout ce qu'il lit (jpg, png, webp, et meme
    une image extraite d'une video). L'image est recadree en « couvrant »
    le format de sortie, assombrie, un peu floutee et creusee derriere la
    machine — le faisceau etant additif, une image nette et claire derriere
    le trait lui mangerait tout son contraste.
    """
    # `seek` sert a l'apercu : sur une video de fond on n'extrait qu'une image,
    # celle de l'instant regarde, au lieu de detailler tout le fichier. On
    # replie l'instant sur la duree du fond, puisque le rendu le boucle — sans
    # quoi demander la 170e seconde d'une video qui en dure douze ne renvoie
    # rien du tout.
    #
    # Une photo, elle, n'a pas de duree : lui demander sa quarantieme seconde
    # ne renvoie rien non plus. On ne la cherche donc pas la ou elle n'est pas.
    if seek > 0:
        duree = media_duration(path)
        seek = seek % duree if duree > 0.5 else 0.0
    # Avec un travelling, on charge plus grand que l'ecran : c'est cette marge
    # qu'on parcourt. Elle est prise sur l'image d'origine, donc le cadre reste
    # net d'un bout a l'autre — l'agrandir apres coup le rendrait flou.
    aw, ah = w, h
    if travel > 1e-4 and travel_mode != "aucun":
        aw = int(round(w * (1.0 + travel)))
        ah = int(round(h * (1.0 + travel)))
    out = subprocess.run(
        ["ffmpeg", "-v", "error"]
        + (["-ss", "%.3f" % seek] if seek > 0 else [])
        + ["-i", path,
         "-vf", "scale=%d:%d:force_original_aspect_ratio=increase,crop=%d:%d"
                % (aw, ah, aw, ah),
         "-frames:v", "1", "-f", "rawvideo", "-pix_fmt", "rgb24", "-"],
        stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    if out.returncode or len(out.stdout) < aw * ah * 3:
        raise RuntimeError(_fond_illisible(path, out.stderr))
    img = (np.frombuffer(out.stdout, dtype=np.uint8)[:aw * ah * 3]
           .reshape(ah, aw, 3).astype(np.float32) / 255.0)
    if blur > 0:
        img = np.stack([gauss(img[:, :, c], blur) for c in range(3)], axis=-1)
    return StillBackdrop(np.ascontiguousarray(img, dtype=np.float32),
                         _backdrop_mask(w, h, strength, clear, scale,
                                        screen_dim, ecran),
                         dur=dur, travel=travel, travel_mode=travel_mode)


def _fond_illisible(path, err=b""):
    """Ce qu'on dit quand ffmpeg ne tire rien d'un fichier de fond.

    Il lui arrive de s'arreter net, mais aussi de finir sans erreur et sans
    rien produire — un format qu'il ne decode pas (les photos HEIC des
    telephones, typiquement) ou un fichier tronque. Dans les deux cas, mieux
    vaut une phrase lisible qu'une ligne de commande de trois cents signes ou
    un « cannot reshape array of size 0 » surgi beaucoup plus loin.
    """
    lignes = (err or b"").decode("utf-8", "replace").strip().splitlines()
    detail = (" (%s)" % lignes[-1][:120]) if lignes else ""
    return ("ffmpeg n'a pas pu lire le fond « %s »%s. Si c'est une photo prise "
            "au telephone, elle est sans doute au format HEIC : reenregistrez-la "
            "en JPEG ou en PNG." % (os.path.basename(path), detail))


def media_duration(path):
    """Duree d'un fichier en secondes, ou 0 si ce n'en est pas un (une image)."""
    try:
        out = subprocess.run(
            ["ffprobe", "-v", "error", "-show_entries", "format=duration",
             "-of", "default=nw=1:nk=1", path],
            stdout=subprocess.PIPE, check=True).stdout.decode().strip()
        return float(out)
    except Exception:                                     # noqa: BLE001
        return 0.0


def is_video(path):
    """Vrai si le fichier contient une video animee (et non une seule image)."""
    try:
        out = subprocess.run(
            ["ffprobe", "-v", "error", "-select_streams", "v:0",
             "-show_entries", "stream=duration,nb_frames,codec_name",
             "-of", "default=nw=1:nk=1", path],
            stdout=subprocess.PIPE, check=True).stdout.decode().split()
    except Exception:                                     # noqa: BLE001
        return False
    if any(c in out for c in ("mjpeg", "png", "webp", "bmp", "gif")):
        # une image fixe est parfois annoncee comme un flux video d'une image
        if not any(x.replace(".", "").isdigit() and float(x) > 1.5 for x in out):
            return False
    return any(x.replace(".", "").isdigit() and float(x) > 1.5 for x in out)


class VideoBackdrop:
    """Fond anime : une video derriere la machine.

    Le rendu calcule les images en parallele et dans un ordre quelconque : une
    lecture sequentielle de la video ne s'y prete pas. On la detaille donc une
    fois en vignettes sur le disque, que chaque tache relit par son numero.

    Les vignettes sont volontairement petites — le fond est floute de toute
    facon, et les garder en pleine definition coûterait des gigaoctets sur un
    morceau entier. Le flou et le recadrage sont faits par ffmpeg pendant
    l'extraction, si bien qu'il ne reste plus qu'une lecture et une
    multiplication par image.
    """

    DIV = 3            # les vignettes font le tiers de la definition finale

    def __init__(self, path, w, h, fps, duration, strength=0.80, clear=0.45,
                 scale=None, blur=2.2, screen_dim=0.40, cache_dir=None,
                 travel=0.0, travel_mode="avant", div=None, ecran=None):
        try:
            from PIL import Image                # noqa: F401 -- verifie tot
        except ImportError:
            raise RuntimeError(
                "un fond anime a besoin de la bibliotheque pillow. "
                "A installer une seule fois avec :  pip install pillow  "
                "(une image fixe en fond, elle, fonctionne sans)")
        self.w, self.h = w, h
        self.mask = _backdrop_mask(w, h, strength, clear, scale, screen_dim,
                                   ecran)
        self.fps = float(fps)
        self.DIV = int(div) if div else VideoBackdrop.DIV
        self.dur = max(float(duration), 1e-3)
        self.travel, self.mode = float(travel), travel_mode
        marge = (1.0 + self.travel) if (self.travel > 1e-4
                                        and travel_mode != "aucun") else 1.0
        sw = max(16, int(w * marge) // self.DIV)
        sh = max(16, int(h * marge) // self.DIV)

        key = "%s-%d-%d-%d-%d-%.2f-%.2f" % (
            os.path.basename(path), os.path.getsize(path), sw, sh,
            int(fps), duration, blur)
        key = re.sub(r"[^A-Za-z0-9._-]+", "_", key)
        root = cache_dir or os.path.join(tempfile.gettempdir(), "omnipotard-fonds")
        self.dir = os.path.join(root, key)
        done = os.path.join(self.dir, "_complet")

        if not os.path.exists(done):
            os.makedirs(self.dir, exist_ok=True)
            vf = ("scale=%d:%d:force_original_aspect_ratio=increase,crop=%d:%d"
                  % (sw, sh, sw, sh))
            if blur > 0:
                vf += ",gblur=sigma=%.2f" % max(0.4, blur / self.DIV)
            r = subprocess.run(
                ["ffmpeg", "-v", "error", "-y", "-stream_loop", "-1", "-i", path,
                 "-t", "%.3f" % (duration + 1.0 / max(fps, 1)),
                 "-vf", vf, "-r", "%.4f" % fps, "-q:v", "4",
                 os.path.join(self.dir, "%06d.jpg")], stderr=subprocess.PIPE)
            if r.returncode:
                raise RuntimeError(_fond_illisible(path, r.stderr))
            open(done, "w").close()

        self.files = sorted(f for f in os.listdir(self.dir) if f.endswith(".jpg"))
        if not self.files:
            raise RuntimeError("aucune image extraite de %s" % path)
        self._cache = (None, None)

    def at(self, t):
        from PIL import Image
        i = min(len(self.files) - 1, max(0, int(t * self.fps + 0.5)))
        if self._cache[0] == i and self.travel <= 1e-4:
            return self._cache[1]        # sans travelling, le cadre ne bouge pas
        im = Image.open(os.path.join(self.dir, self.files[i])).convert("RGB")
        # la vignette est agrandie a la taille du cadre a parcourir, puis on y
        # decoupe l'instant voulu — comme pour une photo
        cible = (int(round(self.w * (1.0 + self.travel)))
                 if self.travel > 1e-4 and self.mode != "aucun" else self.w)
        cibleh = (int(round(self.h * (1.0 + self.travel)))
                  if self.travel > 1e-4 and self.mode != "aucun" else self.h)
        if im.size != (cible, cibleh):
            im = im.resize((cible, cibleh), Image.BILINEAR)
        img = np.asarray(im, dtype=np.float32) / 255.0
        img = travel_crop(img, t / self.dur, self.travel, self.mode,
                          self.w, self.h) * self.mask
        img = np.ascontiguousarray(img, dtype=np.float32)
        self._cache = (i, img)
        return img


def make_backdrop(path, w, h, fps=30, duration=0.0, sharp=0.37, **kw):
    """Image ou video, selon ce que contient le fichier."""
    blur, div = backdrop_quality(sharp)
    if duration > 0 and is_video(path):
        return VideoBackdrop(path, w, h, fps, duration, blur=blur, div=div, **kw)
    # une photo a besoin de connaitre la duree du morceau : c'est sur elle que
    # s'etale le travelling
    return load_backdrop(path, w, h, blur=blur, dur=max(duration, 1e-3), **kw)


def hex_to_rgb(x):
    """#rrggbb (ou rrggbb, ou #rgb) -> (r, g, b) en 0..1."""
    x = str(x).strip().lstrip("#")
    if len(x) == 3:
        x = "".join(c * 2 for c in x)
    if len(x) != 6:
        raise ValueError("couleur invalide : %s" % x)
    return tuple(int(x[i:i + 2], 16) / 255.0 for i in (0, 2, 4))


def rgb_to_hex(c):
    return "#%02x%02x%02x" % tuple(max(0, min(255, int(round(v * 255)))) for v in c)


def creux_machine(w, h, clear, scale=None):
    """Le creux que l'on menage derriere la machine, en (h, w).

    La texture comme l'image de fond s'y appuient : le faisceau etant additif,
    un fond clair juste derriere le trait lui mange tout son contraste.
    """
    if clear <= 0.0:
        return None
    sc = scale if scale else min(h * 0.5, w * 0.5 / 1.30)
    nx = (np.arange(w, dtype=np.float32)[None, :] - w * 0.5) / (1.52 * sc)
    ny = (np.arange(h, dtype=np.float32)[:, None] - h * 0.5) / (1.08 * sc)
    r = np.sqrt(nx * nx + ny * ny)
    k = np.clip((r - 0.82) / 0.55, 0.0, 1.0)
    return (1.0 - float(clear) * (1.0 - k * k * (3.0 - 2.0 * k))).astype(np.float32)


def make_background(w, h, kind="noir", color=(0.0, 0.0, 0.0), strength=1.0,
                    clear=0.55, scale=None, seed=11):
    """Construit le fond, une fois pour toutes.

    Renvoie soit une couleur diffusable (1,1,3), soit une vraie image (h,w,3)
    que `colorize` additionne telle quelle sous les scanlines, le vignettage
    et le grain — la texture est donc travaillee comme le reste de la dalle.

    `clear` (0 a 1) creuse la texture derriere la machine : c'est ce qui lui
    permet de ressortir meme sur un fond colore.
    """
    col = np.float32(color) * float(strength)
    if kind in ("noir", "none") or strength <= 0.0 or not np.any(col > 0):
        return np.float32((0.0, 0.0, 0.0)).reshape(1, 1, 3)
    if kind == "uni" and clear <= 0.0:
        return col.reshape(1, 1, 3)

    yy = np.arange(h, dtype=np.float32)[:, None]
    xx = np.arange(w, dtype=np.float32)[None, :]
    # Vingt-cinq cases exactement, et non « environ vingt-quatre » : la texture
    # peut alors defiler en boucle sans montrer de raccord, puisque la hauteur
    # de l'image est un multiple entier de la maille — et des cinq mailles du
    # trait fort, et des cent lignes de tube.
    pitch = max(6.0, h / 25.0)

    if kind == "uni":
        pat = np.ones((h, w), dtype=np.float32)
    elif kind == "grille":
        # papier millimetre d'oscilloscope : trait fin, et un trait fort
        # toutes les cinq cases.
        def lines(u, period, width):
            d = np.abs((np.mod(u + period * 0.5, period) - period * 0.5))
            return np.clip(1.0 - d / width, 0.0, 1.0)
        fine = np.maximum(lines(xx, pitch, 1.1), lines(yy, pitch, 1.1))
        fort = np.maximum(lines(xx, pitch * 5, 1.5), lines(yy, pitch * 5, 1.5))
        pat = 0.22 + 0.55 * fine + 0.85 * fort
    elif kind == "points":
        def dots(u, period):
            d = np.abs(np.mod(u + period * 0.5, period) - period * 0.5)
            return np.clip(1.0 - d / 1.6, 0.0, 1.0)
        pat = 0.16 + 1.05 * (dots(xx, pitch) * dots(yy, pitch))
    elif kind == "scan":
        # lignes de tube serrees, dans l'axe des scanlines de la dalle
        pat = 0.30 + 0.80 * (0.5 + 0.5 * np.cos(yy * (2.0 * math.pi / max(3.0, pitch * 0.25)))) ** 2
        pat = pat + np.zeros((1, w), dtype=np.float32)
    elif kind == "degrade":
        # sombre au centre, colore vers les bords : le regard va au milieu
        ny = (yy / h - 0.5) * 2.0
        nx = (xx / w - 0.5) * 2.0
        r = np.sqrt(nx * nx * 0.62 + ny * ny)
        pat = np.clip(r, 0.0, 1.0) ** 1.6
        pat = 0.10 + 1.15 * pat
    elif kind == "bruit":
        # grain fixe : une matiere, pas un scintillement (il ne bouge pas
        # d'une image a l'autre, sinon il rivaliserait avec le grain anime)
        rng = np.random.default_rng(seed)
        n = rng.standard_normal((max(2, h // 3), max(2, w // 3))).astype(np.float32)
        n = upsample(gauss(n, 0.8), 3, (h, w))
        pat = np.clip(0.55 + 0.75 * n, 0.0, 2.0)
    else:
        raise ValueError("fond inconnu : %s" % kind)

    trou = creux_machine(w, h, clear, scale)
    if trou is not None:
        pat = pat * trou

    return (pat[..., None] * col.reshape(1, 1, 3)).astype(np.float32)


# Caisse claire : jaune de tube, et un halo un peu plus ambre.
SNARE_RGB = (1.00, 0.86, 0.16)
SNARE_HALO = (1.00, 0.62, 0.04)

SR = 48000
DUREE_REF = 11.5                 # 15 temps + 1s de maintien sur le logo
MUSIC_PATH = "assets/hint.mp3"   # morceau utilise ; --music pour en changer
MUSIC_START = 0.0                # tout debut du morceau
# 19.8209 : l'autre point d'accroche essaye — musique, break d'une seconde,
# puis drop pile sur la barre de mesure (--music-start 19.8209).


# ==========================================================================
#  Outils numeriques
# ==========================================================================

def smoothstep(a, b, x):
    t = np.clip((x - a) / max(1e-9, b - a), 0.0, 1.0)
    return t * t * (3.0 - 2.0 * t)


def ease_out(t, p=3.0):
    return 1.0 - (1.0 - np.clip(t, 0.0, 1.0)) ** p


def ease_in_out(t):
    t = np.clip(t, 0.0, 1.0)
    return t * t * (3.0 - 2.0 * t)


def gauss(a, sigma):
    """Flou gaussien separable (noyau explicite).

    Deux details font tout le cout de cette fonction, et c'est ici que le
    rendu passait le plus clair de son temps apres la deformation du tube.

    Le noyau est ramene a la precision de l'image. Calcule en double
    precision, chacun de ses coefficients faisait remonter tout le calcul en
    double : la meme image, deux fois plus d'octets a promener a chaque passe,
    et une conversion a la fin.

    Et le noyau est symetrique : les deux cotes d'un meme coefficient
    s'ajoutent avant d'etre multiplies, ce qui epargne une passe par paire.
    """
    if sigma <= 0.05:
        return a
    r = max(1, int(sigma * 2.5))
    k = np.exp(-0.5 * (np.arange(-r, r + 1) / sigma) ** 2)
    k /= k.sum()
    k = k.astype(a.dtype, copy=False)

    h, w = a.shape
    pad = np.pad(a, ((0, 0), (r, r)), mode="edge")
    out = pad[:, r:r + w] * k[r]
    for i in range(r):
        tmp = pad[:, i:i + w] + pad[:, 2 * r - i:2 * r - i + w]
        tmp *= k[i]
        out += tmp
    pad = np.pad(out, ((r, r), (0, 0)), mode="edge")
    res = pad[r:r + h, :] * k[r]
    for i in range(r):
        tmp = pad[i:i + h, :] + pad[2 * r - i:2 * r - i + h, :]
        tmp *= k[i]
        res += tmp
    return res


def downsample(a, f):
    h, w = a.shape
    h2, w2 = h // f, w // f
    return a[:h2 * f, :w2 * f].reshape(h2, f, w2, f).mean(axis=(1, 3))


def upsample(a, f, shape):
    b = np.repeat(np.repeat(a, f, axis=0), f, axis=1)
    out = np.zeros(shape, dtype=b.dtype)
    hh = min(shape[0], b.shape[0])
    ww = min(shape[1], b.shape[1])
    out[:hh, :ww] = b[:hh, :ww]
    if hh < shape[0]:
        out[hh:, :ww] = b[b.shape[0] - 1, :ww]
    if ww < shape[1]:
        out[:, ww:] = out[:, ww - 1:ww]
    return out


class Beam:
    """Accumulateur de faisceau : on empile les impacts, un seul bincount a la fin."""

    def __init__(self, h, w, gain=1.0):
        self.h, self.w = h, w
        self.gain = gain          # normalisation d'intensite selon la definition
        self.mul = 1.0            # attenuation passagere, pour les echos
        self.idx = []
        self.wts = []

    def add(self, px, py, weight):
        h, w = self.h, self.w
        m = (px >= 1.0) & (px < w - 2.0) & (py >= 1.0) & (py < h - 2.0)
        if not np.any(m):
            return
        x = px[m]
        y = py[m]
        if isinstance(weight, np.ndarray) and weight.size == px.size:
            ww = weight[m]
        else:
            ww = np.full(x.size, float(weight))
        ww = ww * (self.gain * self.mul)
        x0 = x.astype(np.int32)
        y0 = y.astype(np.int32)
        fx = x - x0
        fy = y - y0
        base = y0 * w + x0
        self.idx.append(np.concatenate([base, base + 1, base + w, base + w + 1]))
        self.wts.append(np.concatenate([
            ww * (1.0 - fx) * (1.0 - fy),
            ww * fx * (1.0 - fy),
            ww * (1.0 - fx) * fy,
            ww * fx * fy,
        ]))

    def render(self):
        if not self.idx:
            return np.zeros((self.h, self.w), dtype=np.float32)
        buf = np.bincount(np.concatenate(self.idx),
                          weights=np.concatenate(self.wts),
                          minlength=self.h * self.w)
        return buf.reshape(self.h, self.w).astype(np.float32)


# ==========================================================================
#  Geometrie : chemins echantillonnes a pas constant
# ==========================================================================

STEP = 0.0016  # pas d'echantillonnage (unites) -> ~0.9 px en 1080p


def resample(pts, step=STEP, closed=False):
    """Reechantillonne une polyligne a pas d'arc constant."""
    pts = np.asarray(pts, dtype=np.float64)
    if closed and not np.allclose(pts[0], pts[-1]):
        pts = np.vstack([pts, pts[0]])
    d = np.linalg.norm(np.diff(pts, axis=0), axis=1)
    s = np.concatenate([[0.0], np.cumsum(d)])
    length = float(s[-1])
    n = max(2, int(length / step))
    t = np.linspace(0.0, length, n)
    P = np.stack([np.interp(t, s, pts[:, 0]), np.interp(t, s, pts[:, 1])], axis=1)
    return P, t, length


class Path:
    """Chemin discretise : points, normales, abscisse curviligne, etiquette.

    `alpha` est la part de lumiere que le trait recoit. Elle vaut un partout,
    et rien d'autre ne la change pour l'instant : c'est la prise qui permet a
    un trait de s'effacer sans bouger — ce que demande un texte qui change,
    puisqu'une lettre qui se deforme en une autre ne se lit plus.
    """

    __slots__ = ("P", "N", "s", "ph", "tag", "alpha")

    def __init__(self, pts, closed=False, tag="", step=STEP):
        self.alpha = 1.0
        self.P, self.s, _ = resample(pts, step, closed)
        tan = np.gradient(self.P, axis=0)
        tan /= (np.linalg.norm(tan, axis=1, keepdims=True) + 1e-12)
        self.N = np.stack([-tan[:, 1], tan[:, 0]], axis=1)
        # phase stable par organe : chaque piece tremble pour son compte
        self.ph = (sum(ord(c) for c in tag) % 97) * 0.0647
        self.tag = tag


def circle_pts(cx, cy, r, n=240):
    a = np.linspace(0.0, 2.0 * math.pi, n)
    return np.stack([cx + r * np.cos(a), cy + r * np.sin(a)], axis=1)


def rrect_pts(x0, y0, x1, y1, r, n=9):
    r = min(r, (x1 - x0) * 0.5, (y1 - y0) * 0.5)
    out = []
    for cx, cy, a0, a1 in ((x1 - r, y0 + r, -math.pi / 2, 0.0),
                           (x1 - r, y1 - r, 0.0, math.pi / 2),
                           (x0 + r, y1 - r, math.pi / 2, math.pi),
                           (x0 + r, y0 + r, math.pi, 1.5 * math.pi)):
        a = np.linspace(a0, a1, n)
        out.append(np.stack([cx + r * np.cos(a), cy + r * np.sin(a)], axis=1))
    return np.vstack(out)


# ---- alphabet monotrait ---------------------------------------------------
# boite unitaire : x dans [0, w], y dans [0, 1] (y vers le haut)

GLYPHS = {
    "O": (0.62, [[(0, .18), (.18, 0), (.44, 0), (.62, .18), (.62, .82), (.44, 1), (.18, 1), (0, .82), (0, .18)]]),
    "M": (0.72, [[(0, 0), (0, 1), (.36, .50), (.72, 1), (.72, 0)]]),
    "N": (0.62, [[(0, 0), (0, 1), (.62, 0), (.62, 1)]]),
    "I": (0.10, [[(.05, 0), (.05, 1)]]),
    "P": (0.60, [[(0, 0), (0, 1), (.44, 1), (.60, .84), (.60, .66), (.44, .50), (0, .50)]]),
    "T": (0.62, [[(0, 1), (.62, 1)], [(.31, 1), (.31, 0)]]),
    "A": (0.66, [[(0, 0), (.33, 1), (.66, 0)], [(.115, .35), (.545, .35)]]),
    "R": (0.62, [[(0, 0), (0, 1), (.44, 1), (.60, .84), (.60, .66), (.44, .50), (0, .50)], [(.30, .50), (.62, 0)]]),
    "D": (0.62, [[(0, 0), (0, 1), (.42, 1), (.62, .80), (.62, .20), (.42, 0), (0, 0)]]),
    "C": (0.62, [[(.62, .20), (.44, 0), (.18, 0), (0, .18), (0, .82), (.18, 1), (.44, 1), (.62, .80)]]),
    "E": (0.56, [[(.56, 1), (0, 1), (0, 0), (.56, 0)], [(0, .50), (.44, .50)]]),
    "L": (0.54, [[(0, 1), (0, 0), (.54, 0)]]),
    "V": (0.64, [[(0, 1), (.32, 0), (.64, 1)]]),
    "H": (0.62, [[(0, 0), (0, 1)], [(.62, 0), (.62, 1)], [(0, .50), (.62, .50)]]),
    "W": (0.86, [[(0, 1), (.19, 0), (.43, .64), (.67, 0), (.86, 1)]]),
    "0": (0.46, [[(0, .18), (.14, 0), (.32, 0), (.46, .18), (.46, .82), (.32, 1), (.14, 1), (0, .82), (0, .18)]]),
    "1": (0.26, [[(0, .80), (.13, 1), (.13, 0)], [(0, 0), (.26, 0)]]),
    "S": (0.60, [[(.60, .84), (.44, 1), (.16, 1), (0, .84), (0, .66), (.16, .50), (.44, .50), (.60, .34), (.60, .16), (.44, 0), (.16, 0), (0, .16)]]),
    "U": (0.62, [[(0, 1), (0, .18), (.18, 0), (.44, 0), (.62, .18), (.62, 1)]]),
    "Y": (0.62, [[(0, 1), (.31, .55), (.62, 1)], [(.31, .55), (.31, 0)]]),
    # Le reste de l'alphabet, les chiffres et un peu de ponctuation : un titre
    # de morceau n'a aucune raison de se limiter aux lettres d'OMNIPOTARD.
    "B": (0.60, [[(0, 0), (0, 1), (.44, 1), (.60, .86), (.60, .64), (.44, .50), (0, .50)],
                 [(.44, .50), (.60, .36), (.60, .14), (.44, 0), (0, 0)]]),
    "F": (0.56, [[(.56, 1), (0, 1), (0, 0)], [(0, .52), (.44, .52)]]),
    "G": (0.64, [[(.64, .80), (.44, 1), (.18, 1), (0, .82), (0, .18), (.18, 0),
                  (.46, 0), (.64, .18), (.64, .44), (.38, .44)]]),
    "J": (0.46, [[(.46, 1), (.46, .20), (.32, 0), (.14, 0), (0, .20)]]),
    "K": (0.62, [[(0, 0), (0, 1)], [(.60, 1), (0, .42)], [(.24, .60), (.62, 0)]]),
    "Q": (0.62, [[(0, .18), (.18, 0), (.44, 0), (.62, .18), (.62, .82), (.44, 1),
                  (.18, 1), (0, .82), (0, .18)], [(.36, .26), (.66, 0)]]),
    "X": (0.62, [[(0, 1), (.62, 0)], [(0, 0), (.62, 1)]]),
    "Z": (0.60, [[(0, 1), (.60, 1), (0, 0), (.60, 0)]]),
    "2": (0.50, [[(0, .82), (.14, 1), (.36, 1), (.50, .84), (.50, .68), (0, .14),
                  (0, 0), (.50, 0)]]),
    "3": (0.48, [[(0, .86), (.14, 1), (.34, 1), (.48, .86), (.48, .68), (.32, .52),
                  (.48, .36), (.48, .14), (.34, 0), (.14, 0), (0, .14)]]),
    "4": (0.52, [[(.38, 0), (.38, 1), (0, .30), (.52, .30)]]),
    "5": (0.48, [[(.48, 1), (.04, 1), (.04, .58), (.30, .64), (.46, .50), (.46, .16),
                  (.30, 0), (.10, 0), (0, .12)]]),
    "6": (0.48, [[(.42, .94), (.28, 1), (.12, 1), (0, .82), (0, .18), (.14, 0),
                  (.32, 0), (.46, .16), (.46, .36), (.32, .52), (.14, .52), (0, .38)]]),
    "7": (0.46, [[(0, 1), (.46, 1), (.14, 0)]]),
    "8": (0.48, [[(.16, .52), (0, .66), (0, .86), (.16, 1), (.32, 1), (.48, .86),
                  (.48, .66), (.32, .52), (.16, .52), (0, .36), (0, .14), (.16, 0),
                  (.32, 0), (.48, .14), (.48, .36), (.32, .52)]]),
    "9": (0.48, [[(.06, .06), (.20, 0), (.36, 0), (.48, .18), (.48, .82), (.34, 1),
                  (.16, 1), (.02, .84), (.02, .64), (.16, .48), (.34, .48), (.48, .62)]]),
    "-": (0.40, [[(.06, .50), (.34, .50)]]),
    ".": (0.20, [[(.05, 0), (.13, 0), (.13, .08), (.05, .08), (.05, 0)]]),
    ",": (0.20, [[(.13, .10), (.03, 0)]]),
    "'": (0.18, [[(.09, .76), (.09, 1)]]),
    '"': (0.30, [[(.08, .76), (.08, 1)], [(.22, .76), (.22, 1)]]),
    "!": (0.16, [[(.08, .26), (.08, 1)], [(.08, 0), (.08, .08)]]),
    "?": (0.50, [[(0, .82), (.14, 1), (.34, 1), (.50, .84), (.50, .66), (.25, .48),
                  (.25, .28)], [(.25, 0), (.25, .08)]]),
    "(": (0.30, [[(.26, 1), (.06, .70), (.06, .30), (.26, 0)]]),
    ")": (0.30, [[(.04, 1), (.24, .70), (.24, .30), (.04, 0)]]),
    "/": (0.44, [[(0, 0), (.44, 1)]]),
    ":": (0.18, [[(.09, .14), (.09, .24)], [(.09, .56), (.09, .66)]]),
    "+": (0.44, [[(.04, .50), (.40, .50)], [(.22, .32), (.22, .68)]]),
    "=": (0.44, [[(.04, .36), (.40, .36)], [(.04, .62), (.40, .62)]]),
    "&": (0.62, [[(.62, 0), (.16, .58), (.16, .84), (.30, 1), (.44, .86), (.44, .68),
                  (0, .28), (0, .12), (.14, 0), (.34, 0), (.52, .20)]]),
    "*": (0.36, [[(.18, .54), (.18, 1)], [(.02, .64), (.34, .90)],
                 [(.02, .90), (.34, .64)]]),
    "#": (0.58, [[(.14, 0), (.22, 1)], [(.34, 0), (.42, 1)],
                 [(.02, .32), (.54, .32)], [(.04, .68), (.56, .68)]]),
    " ": (0.30, []),
}

# Ce que la police ne trace pas mais qu'un titre peut contenir : on replie
# plutot que de s'arreter. Les accents partent d'eux-memes (la decomposition
# NFD les detache de la lettre) ; restent les ligatures et la ponctuation
# typographique, que les logiciels de musique aiment glisser dans un nom.
_EQUIVALENTS = {
    "\u0152": "OE", "\u0153": "OE", "\u00c6": "AE", "\u00e6": "AE",
    "\u00d8": "O", "\u00f8": "O", "\u0110": "D", "\u0111": "D",
    "\u2019": "'", "\u2018": "'", "\u201c": '"', "\u201d": '"',
    "\u2013": "-", "\u2014": "-", "_": "-", "\u00b7": ".", "\u2026": "...",
    "\u20ac": "E", "\u00a9": "(C)", "\u2122": "TM", "@": "A",
}


def fold_text(txt):
    """Ramene un texte a ce que la police sait tracer.

    Elle ne connait que des capitales, des chiffres et un peu de ponctuation.
    Un nom de morceau, lui, arrive avec des minuscules, des accents, parfois
    un caractere qu'aucune police de trente traits ne dessinera. Tout cela se
    replie ; ce qui ne se replie pas devient une espace. Un titre exotique
    s'affiche donc de travers, ce qui est toujours mieux qu'un rendu qui
    s'arrete au milieu.
    """
    out = []
    for ch in unicodedata.normalize("NFD", str(txt)).upper():
        if unicodedata.combining(ch):          # accent detache par la NFD
            continue
        for c in _EQUIVALENTS.get(ch, ch):
            out.append(c if c in GLYPHS else " ")
    return " ".join("".join(out).split())      # pas d'espaces en trop

TRACKING = 0.145


def text_width(txt, tracking=TRACKING):
    txt = fold_text(txt)
    return sum(GLYPHS[c][0] for c in txt) + tracking * max(0, len(txt) - 1)


def glyph_strokes(txt, height, x0, y0, center=True, tracking=TRACKING):
    """Traits du texte, positionnes : liste de (indice_lettre, points)."""
    txt = fold_text(txt)
    x = x0 - text_width(txt, tracking) * height * 0.5 if center else x0
    out = []
    for gi, ch in enumerate(txt):
        gw, strokes = GLYPHS[ch]
        for st in strokes:
            out.append((gi, [(x + px * height, y0 + py * height) for px, py in st]))
        x += (gw + tracking) * height
    return out


def fit_text(txt, height, width, tracking=TRACKING, tail="..."):
    """Raccourcit un texte pour qu'il tienne dans une largeur donnee.

    Compter les caracteres ne suffit pas : un M est sept fois plus large qu'un
    I. On mesure donc, et on coupe a la lettre pres, en signalant la coupe.
    """
    txt = fold_text(txt)
    if not txt or text_width(txt, tracking) * height <= width:
        return txt
    while txt and text_width(txt + tail, tracking) * height > width:
        txt = txt[:-1]
    txt = txt.rstrip()
    return (txt + tail) if txt else ""


def text_paths(txt, height, x0, y0, step=STEP, center=True, tag="txt", tracking=TRACKING):
    return [Path(pts, tag="%s:%d" % (tag, gi), step=step)
            for gi, pts in glyph_strokes(txt, height, x0, y0, center, tracking)]


# ==========================================================================
#  La machine : MPC Live III
#  Silhouette : bande de 16 pas sur l'arete haute, ecran 7" a gauche,
#  4 Q-Links surmontes de leurs bandeaux, molette encastree en haut a droite,
#  grille 4x4 en bas a droite, touch strip vertical le long des pads.
# ==========================================================================

# Disposition relevee sur une photo de dessus de la MPC Live III, simplifiee.
# Unites = demi-hauteur d'image. De gauche a droite : touch strip sur l'arete,
# grille 4x4, ecran 7", colonne de 4 Q-Links et molette ; bande de 16 pas et
# potard de volume en haut, grille de haut-parleur en bas.
BODY = (-1.445, -0.848, 1.445, 0.848)
BODY_IN = (-1.410, -0.813, 1.410, 0.813)
VOLUME, VOLUME_R = (-1.266, 0.669), 0.119
STEP_X0, STEP_Y0, STEP_W, STEP_H, STEP_GAP = -1.087, 0.627, 0.1183, 0.090, 0.0239
TOPBTN = ((1.198, 0.627, 1.298, 0.717), (1.318, 0.627, 1.418, 0.717))
STRIP = (-1.343, -0.269, -1.224, 0.448)
STRIPBTN = (-1.343, -0.399, -1.224, -0.303)   # bouton isole sous la bande
PAD_X0, PAD_Y0 = -1.116, -0.299
PAD_W, PAD_H, PAD_GX, PAD_GY = 0.2386, 0.1936, 0.0299, 0.0299
SCREEN = (0.018, -0.167, 1.086, 0.567)
QLINK = [(1.266, 0.466), (1.266, 0.257), (1.266, 0.048), (1.266, -0.161)]
QLINK_R = 0.0836
WHEEL, WHEEL_R = (1.266, -0.406), 0.155
BTN_ROWS = (-0.275, -0.382, -0.489)
BTN_X0, BTN_W, BTN_H, BTN_GAP, BTN_N = 0.018, 0.185, 0.084, 0.030, 5
GRILLE = (-1.340, -0.800, 1.340, -0.570)
MIC, MIC_R = (0.0, -0.520), 0.025

# L'ecran de la machine : c'est la que se joue la fin. Le zoom de camera et
# l'echelle de la composition sont inverses l'un de l'autre (SCR_S * CAM_Z = 1),
# si bien que le logo garde exactement la meme taille a l'image qu'avant — seul
# le cadre change : on est desormais dans la dalle de la MPC.
SCR_IN = (SCREEN[0] + 0.028, SCREEN[1] + 0.028, SCREEN[2] - 0.028, SCREEN[3] - 0.028)
SCR_C = ((SCR_IN[0] + SCR_IN[2]) * 0.5, (SCR_IN[1] + SCR_IN[3]) * 0.5)
CAM_Z = 2.60
SCR_S = 1.0 / CAM_Z
SCR_HW = (SCR_IN[2] - SCR_IN[0]) * 0.5 * CAM_Z      # decoupe, en composition
SCR_HH = (SCR_IN[3] - SCR_IN[1]) * 0.5 * CAM_Z
SCR_OY = 0.0475                                     # centrage vertical du bloc


def pad_rect(i, j):
    """i = ligne (0 = bas), j = colonne (0 = gauche)."""
    x0 = PAD_X0 + j * (PAD_W + PAD_GX)
    y0 = PAD_Y0 + i * (PAD_H + PAD_GY)
    return x0, y0, x0 + PAD_W, y0 + PAD_H


def step_rect(k):
    x0 = STEP_X0 + k * (STEP_W + STEP_GAP)
    return x0, STEP_Y0, x0 + STEP_W, STEP_Y0 + STEP_H


def build_mpc(step=STEP):
    P = []
    add = P.append

    add(Path(rrect_pts(*BODY, r=0.072), closed=True, tag="body", step=step))
    add(Path(rrect_pts(*BODY_IN, r=0.052), closed=True, tag="body", step=step))

    # potard de volume (coin haut gauche)
    add(Path(circle_pts(*VOLUME, r=VOLUME_R), closed=True, tag="vol", step=step))
    add(Path(circle_pts(*VOLUME, r=VOLUME_R * 0.34), closed=True, tag="vol", step=step))

    # bande de 16 pas + touches du coin haut droit
    for k in range(16):
        add(Path(rrect_pts(*step_rect(k), r=0.014), closed=True, tag="step%d" % k, step=step))
    for k, r in enumerate(TOPBTN):
        add(Path(rrect_pts(*r, r=0.014), closed=True, tag="btnx%d" % k, step=step))

    # touch strip (arete gauche) + ses reperes
    add(Path(rrect_pts(*STRIP, r=0.052), closed=True, tag="strip", step=step))
    for k in range(11):
        y = STRIP[1] + 0.045 + (STRIP[3] - STRIP[1] - 0.09) * k / 10.0
        add(Path([(STRIP[0] + 0.022, y), (STRIP[2] - 0.022, y)], tag="strip", step=step))

    # bouton isole sous la bande tactile
    add(Path(rrect_pts(*STRIPBTN, r=0.022), closed=True, tag="stripbtn", step=step))
    add(Path(rrect_pts(STRIPBTN[0] + 0.021, STRIPBTN[1] + 0.018,
                       STRIPBTN[2] - 0.021, STRIPBTN[3] - 0.018, 0.014),
             closed=True, tag="stripbtn", step=step))

    # grille 4x4 : contour + biseau interieur
    for i in range(4):
        for j in range(4):
            x0, y0, x1, y1 = pad_rect(i, j)
            k = i * 4 + j
            add(Path(rrect_pts(x0, y0, x1, y1, 0.030), closed=True, tag="pad%d" % k, step=step))
            add(Path(rrect_pts(x0 + 0.022, y0 + 0.020, x1 - 0.022, y1 - 0.020, 0.020),
                     closed=True, tag="pad%d" % k, step=step))

    # ecran tactile 7"
    sx0, sy0, sx1, sy1 = SCREEN
    add(Path(rrect_pts(sx0, sy0, sx1, sy1, 0.020), closed=True, tag="lcd", step=step))
    add(Path(rrect_pts(sx0 + 0.028, sy0 + 0.028, sx1 - 0.028, sy1 - 0.028, 0.012),
             closed=True, tag="lcd", step=step))
    add(Path([(sx0 + 0.028, sy1 - 0.115), (sx1 - 0.028, sy1 - 0.115)], tag="lcd", step=step))

    # colonne de Q-Links + molette
    for k, (cx, cy) in enumerate(QLINK):
        add(Path(circle_pts(cx, cy, QLINK_R), closed=True, tag="qlink%d" % k, step=step))
        add(Path(circle_pts(cx, cy, QLINK_R * 0.30), closed=True, tag="qlink%d" % k, step=step))
    add(Path(circle_pts(WHEEL[0], WHEEL[1], WHEEL_R), closed=True, tag="wheel", step=step))
    add(Path(circle_pts(WHEEL[0], WHEEL[1], WHEEL_R * 0.68), closed=True, tag="wheel", step=step))
    add(Path(circle_pts(WHEEL[0], WHEEL[1], WHEEL_R * 0.26), closed=True, tag="wheel", step=step))

    # rangees de touches sous l'ecran
    for row, yy in enumerate(BTN_ROWS):
        for k in range(BTN_N):
            x0 = BTN_X0 + k * (BTN_W + BTN_GAP)
            add(Path(rrect_pts(x0, yy, x0 + BTN_W, yy + BTN_H, 0.016), closed=True,
                     tag="btn%d" % (row * BTN_N + k), step=step))

    # marquage + grille de haut-parleur
    P += text_paths("MPC LIVE III", 0.095, -1.070, -0.470, step=step, center=False, tag="logo")
    # plus petit et remonte : le halo du trait mordait sur la grille du
    # haut-parleur, qui commence a -0.570
    P += text_paths("OMNIPOTARD", 0.040, -1.066, -0.544, step=step, center=False,
                    tag="mark", tracking=0.52)
    add(Path(rrect_pts(*GRILLE, r=0.030), closed=True, tag="grille", step=step))
    for k in range(5):
        y = GRILLE[1] + 0.038 + (GRILLE[3] - GRILLE[1] - 0.076) * k / 4.0
        add(Path([(GRILLE[0] + 0.030, y), (GRILLE[2] - 0.030, y)], tag="grille", step=step))
    add(Path(circle_pts(*MIC, r=MIC_R), closed=True, tag="mic", step=step))
    return P


def pad_fill(k, nlines=7):
    x0, y0, x1, y1 = pad_rect(k // 4, k % 4)
    m = 0.038
    return np.vstack([np.stack([np.linspace(x0 + m, x1 - m, 56), np.full(56, y)], axis=1)
                      for y in np.linspace(y0 + m * 0.8, y1 - m * 0.8, nlines)])


# ==========================================================================
#  Deux autres machines
#
#  Elles parlent le meme langage que la MPC : des chemins etiquetes, et des
#  etiquettes que le moteur sait animer — « pad<n> » s'allume sur un coup,
#  « step<n> » sur le pas du sequenceur, « qlink<n> » suit une enveloppe,
#  « strip » le curseur tactile, « lcd » l'ecran. Une machine n'a donc pas a
#  ressembler a une MPC pour etre jouee comme telle ; il lui suffit d'avoir
#  des organes et de dire lesquels.
# ==========================================================================

# ---- Arturia MiniFreak : un clavier 37 touches, large et plat.
MF_BODY = (-1.560, -0.612, 1.560, 0.612)
MF_CLAV = (-1.512, -0.588, 1.512, -0.040)     # la zone du clavier
MF_ECRAN = (-0.040, 0.140, 0.700, 0.520)
MF_TOUCHE = ((MF_CLAV[2] - MF_CLAV[0]) / 22.0)      # 22 touches blanches
# noires : apres do, re, fa, sol, la de chaque octave
MF_NOIRES = (0, 1, 3, 4, 5)


MF_NOIRE_L = MF_TOUCHE * 0.58                       # largeur d'une noire
MF_NOIRE_Y = MF_CLAV[1] + (MF_CLAV[3] - MF_CLAV[1]) * 0.42   # ou elle s'arrete


def mf_blanche(i):
    """Le rectangle complet d'une blanche, bord a bord.

    Il sert de reperage — quelle touche est ou — mais ce n'est pas la forme
    qu'on trace : une blanche est echancree la ou une noire s'appuie dessus.
    """
    x0 = MF_CLAV[0] + i * MF_TOUCHE
    return x0 + 0.004, MF_CLAV[1] + 0.012, x0 + MF_TOUCHE - 0.004, MF_CLAV[3]


def mf_blanche_bas(i):
    """La partie large d'une blanche, sous les noires : ce qu'on en voit."""
    x0, y0, x1, _ = mf_blanche(i)
    return x0, y0, x1, MF_NOIRE_Y


def _mf_noire_a(bord):
    """Y a-t-il une noire posee sur ce bord entre deux blanches ?

    `bord` compte les intervalles depuis la premiere blanche : le bord 0 separe
    la premiere de la deuxieme.
    """
    if bord < 0 or bord >= 21:
        return False
    oct_, p = divmod(bord, 7)
    return p in MF_NOIRES and oct_ * 5 + MF_NOIRES.index(p) < 15


def mf_blanche_contour(i):
    """Le contour d'une blanche, echancre sous les noires qui l'entament.

    Tracee en rectangle plein, une blanche passe *sous* les noires, et la
    couture entre deux blanches traverse alors chaque noire par le milieu :
    elle avait l'air coupee en deux. Une blanche s'arrete donc la ou sa voisine
    noire commence, comme sur un vrai clavier.
    """
    x0, yb, x1, yt = mf_blanche(i)
    demi = MF_NOIRE_L * 0.5
    bord = MF_CLAV[0] + i * MF_TOUCHE
    gauche = _mf_noire_a(i - 1)
    droite = _mf_noire_a(i)
    xg = bord + demi + 0.004 if gauche else x0
    xd = bord + MF_TOUCHE - demi - 0.004 if droite else x1
    yn = MF_NOIRE_Y

    P = [(x0, yb), (x1, yb)]
    if droite:
        P += [(x1, yn), (xd, yn)]
    P += [(xd, yt), (xg, yt)]
    if gauche:
        P += [(xg, yn), (x0, yn)]
    P.append((x0, yb))
    return P


def _lignes(r, serre=0.030, m=0.010):
    """Un rectangle rempli de lignes horizontales, espacees de `serre`.

    Ecrit ici et non plus bas avec les autres remplissages : le clavier se
    construit au chargement du module, avant eux.
    """
    x0, y0, x1, y1 = r
    n = max(2, int(round((y1 - y0 - 2 * m) / serre)))
    return np.vstack([np.stack([np.linspace(x0 + m, x1 - m, 24),
                                np.full(24, y)], axis=1)
                      for y in np.linspace(y0 + m, y1 - m, n)])


def mf_blanche_remplir(i, serre=0.030):
    """Le remplissage d'une blanche : toute la touche, echancrure comprise.

    Ne remplir que la partie large laissait le haut de la touche eteint, et
    une note jouee n'avait l'air qu'a moitie enfoncee. On suit donc la vraie
    forme — large en bas, etroite entre les noires — avec le meme ecart entre
    les lignes de part et d'autre, pour que le passage ne se voie pas.
    """
    x0, yb, x1, yt = mf_blanche(i)
    demi = MF_NOIRE_L * 0.5
    bord = MF_CLAV[0] + i * MF_TOUCHE
    xg = bord + demi + 0.004 if _mf_noire_a(i - 1) else x0
    xd = bord + MF_TOUCHE - demi - 0.004 if _mf_noire_a(i) else x1
    yn, m = MF_NOIRE_Y, 0.010
    out = []
    for (a_, b_, ya, yb_) in ((x0, x1, yb + m, yn - m * 0.4),
                              (xg, xd, yn + m * 0.4, yt - m)):
        n = max(2, int(round((yb_ - ya) / serre)))
        for y in np.linspace(ya, yb_, n):
            out.append(np.stack([np.linspace(a_ + m, b_ - m, 24),
                                 np.full(24, y)], axis=1))
    return np.vstack(out)


def mf_noire(i):
    """La i-eme touche noire, posee a cheval sur deux blanches."""
    oct_, k = divmod(i, 5)
    blanche = oct_ * 7 + MF_NOIRES[k]
    x = MF_CLAV[0] + (blanche + 1) * MF_TOUCHE
    return (x - MF_NOIRE_L * 0.5, MF_NOIRE_Y,
            x + MF_NOIRE_L * 0.5, MF_CLAV[3])


def arrondir(P, r, n=6):
    """Arrondit les angles d'un contour ferme.

    Chaque sommet est recule le long de ses deux aretes, et les deux points
    obtenus sont relies par une courbe qui passe pres de l'ancien sommet. Les
    angles rentrants s'arrondissent comme les sortants, ce qu'il faut ici :
    une touche blanche est echancree, et ses coins rentrants doivent s'adoucir
    comme les autres.

    Le rayon est rabattu a la moitie de la plus courte arete, sans quoi deux
    angles voisins se mangeraient l'un l'autre sur une echancrure etroite.
    """
    P = np.asarray(P, dtype=np.float64)
    if len(P) > 1 and np.allclose(P[0], P[-1]):
        P = P[:-1]
    m = len(P)
    if m < 3 or r <= 0.0:
        return _boucle(P)
    t = np.linspace(0.0, 1.0, n)[:, None]
    out = []
    for i in range(m):
        v = P[i]
        a, b = P[i - 1], P[(i + 1) % m]
        da, db = a - v, b - v
        la, lb = float(np.hypot(*da)), float(np.hypot(*db))
        if la < 1e-9 or lb < 1e-9:
            out.append(v[None, :])
            continue
        d = min(r, la * 0.5, lb * 0.5)
        p1, p2 = v + da / la * d, v + db / lb * d
        # courbe de Bezier quadratique : elle part de p1, tend vers le sommet,
        # et arrive en p2 — exactement l'allure d'un coin adouci
        out.append((1 - t) ** 2 * p1 + 2 * (1 - t) * t * v + t ** 2 * p2)
    return _boucle(np.vstack(out))


def _boucle(P):
    """Un contour ferme : le dernier point rejoint le premier."""
    P = np.asarray(P, dtype=np.float64)
    return P if np.allclose(P[0], P[-1]) else np.vstack([P, P[:1]])


def mf_clavier():
    """Les 37 touches, du grave a l'aigu. Pour chacune : le rectangle qui
    s'allume, et le contour a tracer.

    Une seule liste, pour que le trace et l'allumage ne puissent pas diverger.
    Ils different : une blanche se trace echancree sous les noires, mais c'est
    sa partie large — celle qu'on voit — qui s'allume.

    Les ranger par abscisse suffit a les mettre dans l'ordre chromatique : une
    noire est posee a cheval entre deux blanches, donc son bord gauche tombe
    entre les leurs.
    """
    t = [(mf_blanche(i)[0], mf_blanche(i),
          arrondir(mf_blanche_contour(i), 0.012),
          mf_blanche_remplir(i))
         for i in range(22)]
    t += [(mf_noire(i)[0], mf_noire(i),
           _boucle(rrect_pts(*mf_noire(i), r=0.012)),
           _lignes(mf_noire(i), 0.030))
          for i in range(15)]
    t.sort(key=lambda e: e[0])
    return [(r, c, f) for _, r, c, f in t]


MF_CLAVIER = mf_clavier()
# les seize touches que les coups de batterie allument, faute de melodie :
# des blanches, reparties sur tout le clavier
MF_PADS = [[k for k, (r, _, _) in enumerate(MF_CLAVIER)
            if abs(r[0] - mf_blanche(i)[0]) < 1e-9][0]
           for i in (min(21, j + 3) for j in range(16))]


MF_KNOBS = [(-1.330 + k * 0.158, 0.352) for k in range(8)]
MF_KNOB_R = 0.062
MF_MACRO = (1.216, 0.236)
MF_MACRO_R = 0.116
MF_STRIPS = ((-1.500, 0.040, -0.900, 0.128), (-1.500, 0.184, -0.900, 0.272))
MF_BTN = [(-0.040 + k * 0.106, -0.006, 0.050 + k * 0.106, 0.070) for k in range(7)]


def build_minifreak(step=STEP):
    """Le MiniFreak : panneau de commandes en haut, clavier en bas."""
    P = []
    add = P.append
    add(Path(rrect_pts(*MF_BODY, r=0.050), closed=True, tag="body", step=step))
    add(Path(rrect_pts(MF_BODY[0] + 0.026, MF_BODY[1] + 0.026,
                       MF_BODY[2] - 0.026, MF_BODY[3] - 0.026, 0.036),
             closed=True, tag="body", step=step))
    # ligne de separation entre le panneau et le clavier
    add(Path([(MF_BODY[0] + 0.030, MF_CLAV[3] + 0.014),
              (MF_BODY[2] - 0.030, MF_CLAV[3] + 0.014)], tag="body", step=step))

    # les 37 touches, du grave a l'aigu : une seule suite de pads, etalee sur
    # tout le clavier pour qu'un coup de grosse caisse ne rallume pas seulement
    # le bas du meuble. Les contours sont deja fermes.
    for rang, (_, contour, _) in enumerate(MF_CLAVIER):
        add(Path(contour, tag="pad%d" % ((rang * 16) // len(MF_CLAVIER)),
                 step=step))

    # ecran
    add(Path(rrect_pts(*MF_ECRAN, r=0.020), closed=True, tag="lcd", step=step))
    add(Path(rrect_pts(MF_ECRAN[0] + 0.022, MF_ECRAN[1] + 0.022,
                       MF_ECRAN[2] - 0.022, MF_ECRAN[3] - 0.022, 0.012),
             closed=True, tag="lcd", step=step))

    # huit potards, et la grosse molette de droite
    for k, (cx, cy) in enumerate(MF_KNOBS):
        add(Path(circle_pts(cx, cy, MF_KNOB_R), closed=True,
                 tag="qlink%d" % k, step=step))
        add(Path([(cx, cy + MF_KNOB_R * 0.30), (cx, cy + MF_KNOB_R * 0.92)],
                 tag="qlink%d" % k, step=step))
    add(Path(circle_pts(*MF_MACRO, r=MF_MACRO_R), closed=True, tag="wheel", step=step))
    add(Path(circle_pts(*MF_MACRO, r=MF_MACRO_R * 0.42), closed=True,
             tag="wheel", step=step))

    # les deux bandes tactiles
    for r in MF_STRIPS:
        add(Path(rrect_pts(*r, r=0.034), closed=True, tag="strip", step=step))
        for j in range(7):
            x = r[0] + 0.040 + (r[2] - r[0] - 0.080) * j / 6.0
            add(Path([(x, r[1] + 0.018), (x, r[3] - 0.018)], tag="strip", step=step))

    # rangee de boutons sous l'ecran
    for k, r in enumerate(MF_BTN):
        add(Path(rrect_pts(*r, r=0.014), closed=True, tag="btn%d" % k, step=step))

    # le marquage se tient au-dessus de la grosse molette, seul endroit du
    # panneau ou il ne mord ni sur l'ecran ni sur les potards
    P += text_paths("MINIFREAK", 0.058, 0.965, 0.452, step=step, center=False,
                    tag="logo")
    P += text_paths("OMNIPOTARD", 0.028, 0.967, 0.396, step=step, center=False,
                    tag="mark", tracking=0.52)
    return P


# ---- Elektron Digitakt II : presque carre, seize touches de declenchement.
#
# La disposition suit celle de la machine : le nom au-dessus de l'ecran, une
# colonne de cinq touches le long du bord gauche a hauteur d'ecran, la grosse
# molette de niveau et ses deux touches en haut a droite, les huit encodeurs
# en deux rangees de quatre dans l'axe de l'ecran, un pave de six a leur
# droite, un amas de six en bas a gauche, et les seize declencheurs a cote de
# cet amas. La premiere version les posait au juge — ils couraient d'un bord
# a l'autre et le nom du modele leur passait dessus.
DK_BODY = (-1.065, -0.862, 1.065, 0.862)
DK_ECRAN = (-0.745, 0.250, 0.285, 0.720)
DK_WHEEL, DK_WHEEL_R = (0.760, 0.560), 0.150
# colonne de fonctions le long du bord gauche, a hauteur d'ecran
DK_GAUCHE = [(-1.000, 0.622 - k * 0.098, -0.820, 0.700 - k * 0.098)
             for k in range(5)]
# deux touches sous la molette de niveau
DK_HAUT = [(0.585, 0.255, 0.740, 0.355), (0.760, 0.255, 0.915, 0.355)]
# huit encodeurs, deux rangees de quatre, sous l'ecran et dans son axe
DK_ENC = [(-0.640 + (k % 4) * 0.295, 0.055 - (k // 4) * 0.240) for k in range(8)]
DK_ENC_R = 0.082
# pave de six touches a droite des encodeurs
DK_DROITE = [(0.520 + (k % 2) * 0.180, 0.055 - (k // 2) * 0.120,
              0.680 + (k % 2) * 0.180, 0.140 - (k // 2) * 0.120)
             for k in range(6)]
# amas de six touches en bas a gauche : fonction, fleches, transport. Sur la
# vraie machine les declencheurs ne vont pas jusqu'au bord gauche, cet amas
# leur prend la place — les poser sur toute la largeur se voyait tout de suite.
DK_BAS = [(-1.000 + (k % 2) * 0.185, -0.530 - (k // 2) * 0.130,
           -0.835 + (k % 2) * 0.185, -0.430 - (k // 2) * 0.130)
          for k in range(6)]
DK_TRIG_W, DK_TRIG_H = 0.172, 0.128
DK_TRIG_X0, DK_TRIG_GX = -0.600, 0.028
DK_TRIG_Y = (-0.760, -0.588)


def dk_trig(k):
    """k de 0 a 15 : deux rangees de huit, les huit premiers en haut."""
    ligne, col = divmod(k, 8)
    x0 = DK_TRIG_X0 + col * (DK_TRIG_W + DK_TRIG_GX)
    y0 = DK_TRIG_Y[1 - ligne]
    return x0, y0, x0 + DK_TRIG_W, y0 + DK_TRIG_H


def build_digitakt(step=STEP):
    """Le Digitakt II : grand ecran, huit encodeurs, seize declencheurs."""
    P = []
    add = P.append
    add(Path(rrect_pts(*DK_BODY, r=0.040), closed=True, tag="body", step=step))
    add(Path(rrect_pts(DK_BODY[0] + 0.024, DK_BODY[1] + 0.024,
                       DK_BODY[2] - 0.024, DK_BODY[3] - 0.024, 0.028),
             closed=True, tag="body", step=step))

    add(Path(rrect_pts(*DK_ECRAN, r=0.022), closed=True, tag="lcd", step=step))
    add(Path(rrect_pts(DK_ECRAN[0] + 0.024, DK_ECRAN[1] + 0.024,
                       DK_ECRAN[2] - 0.024, DK_ECRAN[3] - 0.024, 0.014),
             closed=True, tag="lcd", step=step))
    add(Path([(DK_ECRAN[0] + 0.024, DK_ECRAN[3] - 0.100),
              (DK_ECRAN[2] - 0.024, DK_ECRAN[3] - 0.100)], tag="lcd", step=step))

    # la molette de niveau, et les huit encodeurs
    add(Path(circle_pts(*DK_WHEEL, r=DK_WHEEL_R), closed=True, tag="wheel", step=step))
    add(Path(circle_pts(*DK_WHEEL, r=DK_WHEEL_R * 0.62), closed=True,
             tag="wheel", step=step))
    add(Path(circle_pts(*DK_WHEEL, r=DK_WHEEL_R * 0.22), closed=True,
             tag="wheel", step=step))
    for k, (cx, cy) in enumerate(DK_ENC):
        add(Path(circle_pts(cx, cy, DK_ENC_R), closed=True,
                 tag="qlink%d" % k, step=step))
        add(Path(circle_pts(cx, cy, DK_ENC_R * 0.34), closed=True,
                 tag="qlink%d" % k, step=step))

    # colonne de fonctions a gauche, deux touches sous la molette, le pave de
    # six a droite des encodeurs, l'amas de six en bas a gauche
    for k, r in enumerate(DK_GAUCHE + DK_HAUT + DK_DROITE + DK_BAS):
        add(Path(rrect_pts(*r, r=0.018), closed=True, tag="btn%d" % k, step=step))

    # seize declencheurs : ils servent de pads et de pas de sequenceur
    for k in range(16):
        x0, y0, x1, y1 = dk_trig(k)
        add(Path(rrect_pts(x0, y0, x1, y1, 0.024), closed=True,
                 tag="pad%d" % k, step=step))
        add(Path(rrect_pts(x0 + 0.018, y0 + 0.016, x1 - 0.018, y1 - 0.016, 0.014),
                 closed=True, tag="pad%d" % k, step=step))

    # le nom au-dessus de l'ecran, la marque dans la bande libre entre les
    # encodeurs et les declencheurs : les deux bandes vides de la facade
    P += text_paths("DIGITAKT II", 0.058, -1.000, 0.752, step=step,
                    center=False, tag="logo")
    P += text_paths("OMNIPOTARD", 0.030, 0.658, -0.372, step=step,
                    center=False, tag="mark", tracking=0.52)
    return P


def rect_fill(x0, y0, x1, y1, nlines=4, m=0.008):
    return np.vstack([np.stack([np.linspace(x0 + m, x1 - m, 24), np.full(24, y)], axis=1)
                      for y in np.linspace(y0 + m, y1 - m, nlines)])


def _remplir(r, nlines=5, m=0.022):
    """Le remplissage lumineux d'une touche, quelle que soit la machine."""
    return rect_fill(r[0], r[1], r[2], r[3], nlines, m)


# Ces deux-la sont des fonctions nommees et non des lambdas : le moteur est
# recopie tel quel dans chaque tache de rendu sous Windows, et une lambda ne
# se recopie pas — le rendu s'arretait sur « Can't pickle <lambda> » des qu'on
# choisissait une autre machine que la MPC.
def _remplir_mf(k):
    return MF_CLAVIER[MF_PADS[k]][2]


def _remplir_dk(k):
    return _remplir(dk_trig(k), 5, 0.030)


# Ce que le moteur a besoin de savoir d'une machine : ou est son ecran, quels
# rectangles s'allument sur un coup, lesquels suivent le sequenceur, ou sont
# ses potards et sa bande tactile. Le trace, lui, est libre : deux machines
# n'ont pas a se ressembler pour etre jouees pareil.
MACHINES = {
    "mpc": {
        "nom": "MPC Live III",
        "build": build_mpc,
        "ecran": SCREEN,
        "pads": [pad_rect(k // 4, k % 4) for k in range(16)],
        "remplir": pad_fill,
        "pas": [step_rect(k) for k in range(16)],
        "potards": [(cx, cy, QLINK_R) for cx, cy in QLINK],
        "bande": STRIP,
        "bords": (BODY[0], BODY[2]),
        "quoi": "l'originale : seize pads, bande de pas, grand ecran tactile",
    },
    "minifreak": {
        "nom": "MiniFreak",
        "build": build_minifreak,
        "ecran": MF_ECRAN,
        # Faute de melodie, les touches s'allument sur les coups : sans cela
        # le clavier resterait mort tout le morceau.
        "pads": [MF_CLAVIER[j][0] for j in MF_PADS],
        "pads_contour": [MF_CLAVIER[j][1] for j in MF_PADS],
        "remplir": _remplir_mf,
        # Pas de rangee de pas : un clavier n'a pas de sequenceur qui court le
        # long de ses touches. La premiere version y faisait defiler les seize
        # pas du morceau, ce qui n'existe sur aucune machine et brouillait les
        # notes.
        "pas": [],
        "potards": [(cx, cy, MF_KNOB_R) for cx, cy in MF_KNOBS],
        "bande": MF_STRIPS[0],
        "bords": (MF_BODY[0], MF_BODY[2]),
        # La ligne d'horizon : le fil du morceau se cale dessus plutot que de
        # passer derriere a douze pixels de la. C'est la separation entre le
        # panneau et le clavier — les deux bouts du fil, a gauche et a droite,
        # la prolongent alors au lieu de la rater de peu.
        "ligne": MF_CLAV[3] + 0.014,
        # Les 37 touches, une par demi-ton. C'est cette cle qui fait d'une
        # machine une machine melodique : ses touches sont des notes, pas des
        # pads. Des qu'un fichier MIDI est charge, elles lui appartiennent — les
        # coups de batterie cessent de les allumer, sinon on ne voit plus
        # laquelle joue.
        #
        # Les deux autres machines n'en ont pas, et c'est voulu : plaquer une
        # melodie sur seize pads de batterie ne donnait rien de lisible, trois
        # choses se disputant les memes cellules — les coups, les pas et les
        # notes.
        "touches": [r for r, _, _ in MF_CLAVIER],
        "contours": [c for _, c, _ in MF_CLAVIER],
        # le remplissage suit la vraie forme : une blanche
        # s'allume jusqu'en haut, entre les noires
        "remplis": [f for _, _, f in MF_CLAVIER],
        "note0": 36,
        "quoi": "clavier 37 touches : il joue la melodie du fichier MIDI, ou "
                "s'allume sur les coups a defaut",
    },
    "digitakt": {
        "nom": "Digitakt II",
        "build": build_digitakt,
        "ecran": DK_ECRAN,
        # les seize declencheurs servent de pads et de pas, comme sur la vraie
        "pads": [dk_trig(k) for k in range(16)],
        "remplir": _remplir_dk,
        "pas": [dk_trig(k) for k in range(16)],
        "potards": [(cx, cy, DK_ENC_R) for cx, cy in DK_ENC],
        "bande": None,
        "bords": (DK_BODY[0], DK_BODY[2]),
        "quoi": "seize declencheurs qui font pads et pas a la fois, huit "
                "encodeurs",
    },
}
NOMS_MACHINES = tuple(MACHINES)


# ==========================================================================
#  Passer d'une machine a l'autre
#
#  Le trace de la premiere se deforme jusqu'a devenir celui de la seconde.
#  Trois choses decident de la lisibilite du passage.
#
#  **Qui devient qui.** Apparier les chemins par leur seule position donnait
#  une bouillie : un potard rond se changeait en trait de clavier, un logo en
#  pave de touches. Les organes sont apparies par famille — un chassis devient
#  un chassis, un pad un declencheur, un potard un encodeur — et seulement
#  ensuite par position.
#
#  **Combien de points.** Les aligner sur le plus long tassait six cents points
#  sur un trait qui n'en demandait que trente ; le faisceau etant additif, ce
#  trait devenait une barre blanche. Le nombre de points suit donc la longueur.
#
#  **Ce qui ne se deforme pas.** Un D ne devient pas un M en passant par des
#  formes intermediaires : il passe par de la bouillie. Les textes se croisent
#  sur place, portes par la facade qui bouge sous eux. De meme un organe sans
#  equivalent se retire dans son propre centre plutot que de filer vers celui
#  de l'autre machine, ce qui tirait un trait lumineux en travers.
# ==========================================================================

# Les familles d'organes, dans l'ordre ou on les apparie.
FAMILLES_ORGANES = (
    ("corps", ("body",)),
    ("ecran", ("lcd",)),
    ("pad", ("pad", "step")),
    ("potard", ("qlink", "wheel", "vol")),
    ("bande", ("strip", "stripbtn")),
    ("bouton", ("btn", "btnx")),
    ("texte", ("logo", "mark")),
)


class Fondu:
    """Un chemin intermediaire : ce que le moteur attend d'un Path."""

    __slots__ = ("P", "N", "s", "ph", "tag", "alpha")

    def __init__(self, P, N, s, ph, tag, alpha=1.0):
        self.P, self.N, self.s, self.ph, self.tag = P, N, s, ph, tag
        self.alpha = alpha


class RemplirPads:
    """Le remplissage lumineux d'un pad intermediaire.

    Une classe et non une lambda : le moteur voyage jusqu'aux taches de rendu,
    et une lambda ne se recopie pas.
    """

    __slots__ = ("pads",)

    def __init__(self, pads):
        self.pads = pads

    def __call__(self, k):
        return _remplir(self.pads[k], 5, 0.026)


def _famille_organe(tag):
    nom = tag.rstrip("0123456789").rstrip(":")
    for f, prefixes in FAMILLES_ORGANES:
        if nom in prefixes:
            return f
    return "divers"


def _cle_organe(p):
    """Le rang d'un chemin dans sa famille : rangee, puis colonne."""
    c = p.P.mean(axis=0)
    return (-round(float(c[1]), 1), float(c[0]))


def _paires_organes(a, b):
    """Qui devient qui : par famille d'abord, par position ensuite."""
    par_famille = {}
    for cote, chemins in ((0, a), (1, b)):
        for p in chemins:
            par_famille.setdefault(_famille_organe(p.tag), ([], []))[cote].append(p)
    paires, restes = [], ([], [])
    for f in list(par_famille):
        ga, gb = par_famille[f]
        ga.sort(key=_cle_organe)
        gb.sort(key=_cle_organe)
        n = min(len(ga), len(gb))
        paires += list(zip(ga[:n], gb[:n]))
        restes[0].extend(ga[n:])
        restes[1].extend(gb[n:])
    # ce qui n'a pas trouve sa famille se rabat sur ce qui reste
    restes[0].sort(key=_cle_organe)
    restes[1].sort(key=_cle_organe)
    n = min(len(restes[0]), len(restes[1]))
    paires += list(zip(restes[0][:n], restes[1][:n]))
    return paires, restes[0][n:], restes[1][n:]


def _relire_chemin(P, n):
    """Le meme chemin, relu a n points."""
    if len(P) == n:
        return P
    u = np.linspace(0.0, 1.0, n)
    v = np.linspace(0.0, 1.0, len(P))
    return np.stack([np.interp(u, v, P[:, 0]), np.interp(u, v, P[:, 1])], axis=1)


def _cadre_trace(chemins):
    """Le rectangle qui contient une machine."""
    P = np.vstack([p.P for p in chemins])
    return (float(P[:, 0].min()), float(P[:, 1].min()),
            float(P[:, 0].max()), float(P[:, 1].max()))


def _porte_cadre(P, de, vers):
    """Le meme trace, rapporte d'un cadre a l'autre."""
    sx = (vers[2] - vers[0]) / max(1e-6, de[2] - de[0])
    sy = (vers[3] - vers[1]) / max(1e-6, de[3] - de[1])
    return np.stack([vers[0] + (P[:, 0] - de[0]) * sx,
                     vers[1] + (P[:, 1] - de[1]) * sy], axis=1)


class Passage:
    """Le passage d'une machine a une autre, prepare une fois pour toutes."""

    def __init__(self, cle_a, cle_b):
        self.ma, self.mb = MACHINES[cle_a], MACHINES[cle_b]
        a, b = self.ma["build"](), self.mb["build"]()
        # les textes sont mis de cote tout de suite : ils se croisent, ils ne
        # se deforment pas, et ils ne doivent pas non plus servir de partenaire
        # a un organe qui chercherait le sien.
        ta = [q for q in a if _famille_organe(q.tag) == "texte"]
        tb = [q for q in b if _famille_organe(q.tag) == "texte"]
        a = [q for q in a if _famille_organe(q.tag) != "texte"]
        b = [q for q in b if _famille_organe(q.tag) != "texte"]
        self.cadre_a, self.cadre_b = _cadre_trace(a), _cadre_trace(b)
        paires, seuls_a, seuls_b = _paires_organes(a, b)

        r = np.random.default_rng(5)
        self.mues = [(pa.P.astype(np.float32), pb.P.astype(np.float32),
                      pa.N, pa.s, pa.ph, pa.tag, r.uniform(2.0, 9.0, 2))
                     for pa, pb in paires]
        self.seuls = [(q.P.astype(np.float32),
                       q.P.mean(axis=0).astype(np.float32),
                       q.N, q.s, q.ph, q.tag, cote, r.uniform(2.0, 9.0, 2))
                      for cote, groupe in ((0, seuls_a), (1, seuls_b))
                      for q in groupe]
        self.textes = [(q.P.astype(np.float32), q.N, q.s, q.ph, q.tag, cote)
                       for cote, groupe in ((0, ta), (1, tb)) for q in groupe]

    def chemins(self, u, t, turbulence=1.0):
        """Le trace a l'instant u du passage (0 = machine A, 1 = machine B)."""
        e = u * u * (3.0 - 2.0 * u)
        amp = float(np.sin(math.pi * u)) ** 1.3 * 0.048 * turbulence
        # tout se chevauche au milieu du passage : trente-deux pads glissent
        # sur seize declencheurs. Le faisceau etant additif, le centre partait
        # au blanc ; la lumiere baisse donc d'un tiers au plus fort, et revient
        # a un aux deux bouts.
        creux = 1.0 - 0.30 * float(np.sin(math.pi * u)) ** 1.5
        cadre = tuple(x + (y - x) * e for x, y in zip(self.cadre_a, self.cadre_b))
        out = []

        def trouble(P, s, n, k1, k2):
            """L'ondulation du passage, le long du chemin."""
            if amp <= 1e-4:
                return P
            ss = np.linspace(0.0, float(s[-1] if len(s) else 1.0), n)
            return P + np.stack([amp * np.sin(ss * k1 + t * 3.1),
                                 amp * np.cos(ss * k2 - t * 2.6)], axis=1)

        # ---- les organes qui ont trouve leur equivalent : ils se deforment
        for Pa, Pb, N, s, ph, tag, (k1, k2) in self.mues:
            n = max(2, int(round(len(Pa) + (len(Pb) - len(Pa)) * e)))
            P = _relire_chemin(Pa, n) * (1.0 - e) + _relire_chemin(Pb, n) * e
            out.append(Fondu(trouble(P, s, n, k1, k2).astype(np.float32),
                             _relire_chemin(N, n),
                             np.linspace(0.0, float(s[-1] if len(s) else 1.0), n),
                             ph, tag, alpha=creux))

        # ---- les solitaires : ils se retirent chez eux en s'eteignant
        for P0, c, N, s, ph, tag, cote, (k1, k2) in self.seuls:
            v = (1.0 - e) if cote == 0 else e
            if v <= 0.02:
                continue
            P = c + (P0 - c) * (0.35 + 0.65 * v)
            out.append(Fondu(trouble(P, s, len(P0), k1, k2).astype(np.float32),
                             N, s, ph, tag, alpha=creux * v * v))

        # ---- les textes : celui de A s'efface sur place pendant que celui de
        # B se leve a la sienne, tous deux portes par la facade qui glisse et
        # s'etire sous eux, et tremblant d'un seul tenant avec elle.
        fa = 1.0 - smoothstep(0.08, 0.46, u)
        fb = smoothstep(0.54, 0.92, u)
        for P0, N, s, ph, tag, cote in self.textes:
            v = fa if cote == 0 else fb
            if v <= 0.02:
                continue
            P = _porte_cadre(P0, self.cadre_a if cote == 0 else self.cadre_b, cadre)
            if amp > 1e-4:
                P = P + np.array([amp * 0.55 * math.sin(ph * 3.0 + t * 3.1),
                                  amp * 0.55 * math.cos(ph * 4.0 - t * 2.6)])
            out.append(Fondu(P.astype(np.float32), N, s, ph, tag,
                             alpha=creux * v))
        return out

    def plan(self, u):
        """Le plan intermediaire : ecran, pads et pas suivent le trace."""
        e = u * u * (3.0 - 2.0 * u)

        def melange(x, y):
            return tuple(float(p + (q - p) * e) for p, q in zip(x, y))

        a, b = self.ma, self.mb
        pads = [melange(x, y) for x, y in zip(a["pads"], b["pads"])]
        pas = [melange(x, y) for x, y in zip(a["pas"], b["pas"])]
        n = min(len(a["potards"]), len(b["potards"]))
        pot = [melange(a["potards"][k], b["potards"][k]) for k in range(n)]
        bande = (melange(a["bande"], b["bande"]) if a["bande"] and b["bande"]
                 else (a["bande"] if e < 0.5 else b["bande"]))
        ligne = (a.get("ligne", 0.0)
                 + (b.get("ligne", 0.0) - a.get("ligne", 0.0)) * e)
        bords = melange(a.get("bords", (BODY[0], BODY[2])),
                        b.get("bords", (BODY[0], BODY[2])))
        return {"nom": "passage", "ecran": melange(a["ecran"], b["ecran"]),
                "ligne": ligne, "bords": bords,
                "pads": pads, "remplir": RemplirPads(pads),
                "pas": pas, "potards": pot, "bande": bande, "quoi": ""}


def lire_temps(txt):
    """Un instant, ecrit en secondes (90) ou en minutes (1:30)."""
    txt = str(txt).strip().replace(",", ".")
    if not txt:
        return None
    try:
        if ":" in txt:
            m, sec = txt.rsplit(":", 1)
            return float(int(m or 0)) * 60.0 + float(sec or 0)
        return float(txt)
    except ValueError:
        return None


def ecrire_temps(v):
    """L'ecriture inverse : 95.0 donne 1:35."""
    v = max(0.0, float(v))
    m, sec = divmod(int(round(v)), 60)
    return "%d:%02d" % (m, sec)


def lire_plan_machines(txt, defaut="mpc", duree=None):
    """Le sequenceur de machines : a partir de quel instant laquelle est a
    l'image.

    S'ecrit « 0=mpc, 0:32=digitakt, 1:05=minifreak ». Les separateurs sont
    larges a dessein : la page du studio ecrit proprement, mais un reglage
    enregistre a la main doit passer aussi.
    """
    defaut = defaut if defaut in MACHINES else "mpc"
    plan = []
    for bout in str(txt or "").replace(";", ",").replace("\n", ",").split(","):
        bout = bout.strip()
        if not bout:
            continue
        for sep in ("=", ">", "@"):
            bout = bout.replace(sep, " ")
        morceaux = bout.split()
        if len(morceaux) == 1:
            quand, nom = "0", morceaux[0]
        else:
            quand, nom = morceaux[0], morceaux[-1]
        nom = nom.strip().lower()
        quand = lire_temps(quand)
        if quand is None or nom not in MACHINES:
            continue
        if duree is not None and quand >= duree:
            continue
        plan.append((max(0.0, quand), nom))
    if not plan:
        return [(0.0, defaut)]
    plan.sort(key=lambda e: e[0])
    # deux machines au meme instant : la derniere ecrite gagne. Et une machine
    # annoncee deux fois de suite ne fait pas de passage.
    propre = []
    for quand, nom in plan:
        if propre and abs(propre[-1][0] - quand) < 1e-6:
            propre[-1] = (quand, nom)
        elif propre and propre[-1][1] == nom:
            continue
        else:
            propre.append((quand, nom))
    if propre[0][0] > 0.0:
        propre.insert(0, (0.0, defaut if defaut != propre[0][1] else propre[0][1]))
    return propre


def ecrire_plan_machines(plan):
    """L'ecriture inverse, telle que la page la relit."""
    return ", ".join("%s=%s" % (ecrire_temps(q), n) for q, n in plan)


# ==========================================================================
#  La courbe du titre : un seul trait continu, de la ligne de base au mot
# ==========================================================================

ONDE, TRAIT, TRANSIT, LIAISON = 0, 1, 2, 3


def build_title_curve(txt, height, y0, x_in=-1.88, x_out=1.88, step=STEP):
    """Un seul fil continu : la ligne d'onde traverse toute l'image et le mot
    est pose dessus.

    Chaque glyphe touche deja la ligne de base, donc le trace du logo et le fil
    ne font qu'un — aucune liaison en diagonale n'est necessaire. Seuls les
    sauts internes aux lettres a plusieurs traits restent, en faible intensite,
    comme un retour de spot.
    """
    strokes = glyph_strokes(txt, height, 0.0, y0)
    xs = [q[0] for _, st in strokes for q in st]
    wx0, wx1 = min(xs), max(xs)
    segs = [([(x_in, y0), (wx0, y0)], ONDE),
            ([(wx0, y0), (wx1, y0)], LIAISON),
            ([(wx1, y0), (x_out, y0)], ONDE)]
    prev, prev_gi = None, None
    for gi, st in strokes:
        if prev is not None and gi == prev_gi:
            segs.append(([prev, tuple(st[0])], TRANSIT))
        segs.append((st, TRAIT))
        prev, prev_gi = tuple(st[-1]), gi

    Ps, Ns, ks, ss, off = [], [], [], [], 0.0
    for pts, kind in segs:
        pts = np.asarray(pts, dtype=np.float64)
        if float(np.linalg.norm(np.diff(pts, axis=0), axis=1).sum()) < 1e-6:
            continue
        q, sq, length = resample(pts, step)
        tan = np.gradient(q, axis=0)
        tan /= (np.linalg.norm(tan, axis=1, keepdims=True) + 1e-12)
        Ps.append(q)
        Ns.append(np.stack([-tan[:, 1], tan[:, 0]], axis=1))
        ks.append(np.full(len(q), kind, dtype=np.int8))
        ss.append(sq + off)
        off += length
    return (np.vstack(Ps), np.vstack(Ns), np.concatenate(ks),
            np.concatenate(ss), off)


# ==========================================================================
#  Bande son : dub ambient, synthese additive (aucune dependance externe)
#  4 mesures : nappe -> drop et groove dub -> break -> impact et ambient.
#  Chaque evenement rythmique renvoie aussi le pad qu'il allume a l'image.
# ==========================================================================

NOTES = {"G1": 49.00, "A1": 55.00, "C2": 65.41, "D2": 73.42, "E2": 82.41, "G4": 392.0,
         "A2": 110.0, "C3": 130.8, "E3": 164.8, "G3": 196.0, "B3": 246.9,
         "A3": 220.0, "C4": 261.6, "E4": 329.6}

# motif de 16 pas, joue deux fois (dub : one drop, skank sur les contretemps)
KICKS = (0, 4, 8, 12)          # quatre au sol
RIMS = (8,)                    # accent sur le troisieme temps
HATS = (3, 7, 11, 15)          # shaker sur les doubles
PERCS = ()
SKANKS = (2, 6, 10, 14)        # l'accord des contretemps
BASSLINE = (((0, "A1", 6), (10, "C2", 4)),)

# pad allume par famille d'evenement (grille 4x4, 0 = en bas a gauche)
PAD_OF = {"kick": 0, "rim": 5, "hat": 10, "perc": 6}

# Les familles sur lesquelles chaque effet peut se caler. C'est la meme liste
# partout : une fois la batterie reconnue, pointer une reaction sur la caisse
# claire plutot que sur la grosse caisse ne demande qu'un nom.
PADS_REELS = 16           # au-dela, ce sont des declencheurs sans pad

FAMILLES = {
    "grosse caisse": (0,),
    "basse": (1, 2, 3),
    "caisse claire": (5,),
    "percussions": (6,),
    "charley": (10, 11),
    "accords": (12, 13, 14, 15),
    # « tout » ne veut dire que les vrais coups de la machine : les
    # declencheurs virtuels ci-dessous n'y entrent pas, sans quoi les regler
    # sur « tout » ferait partir l'effet des dizaines de fois par seconde.
    "tout": tuple(range(PADS_REELS)),
}
INSTRUMENTS = tuple(FAMILLES)


# ==========================================================================
#  Declencheurs
#
#  Un effet ne se cale pas forcement sur un instrument. Six familles, cela
#  laisse vite deux effets tomber sur le meme coup ; ces trois familles-ci
#  donnent de quoi les separer :
#
#  - les **bandes de frequences**, qui ecoutent une hauteur et non un
#    instrument. Elles ne dependent pas de la reconnaissance de batterie et
#    attrapent donc aussi ce qui n'est pas percussif : une nappe qui monte,
#    une voix, un souffle de cymbale.
#  - le **hasard**, tire au sort mais pose sur la grille du morceau : jamais
#    a contretemps, jamais deux fois pareil.
#  - les **parts** (« un coup sur deux », « l'autre sur deux ») : deux effets
#    poses sur la meme caisse claire alternent au lieu de tomber ensemble.
#
#  Les trois passent par les memes evenements que la batterie, avec des
#  numeros de pad qui n'existent pas sur la machine : rien de tout cela ne
#  rallume un pad ni ne compte dans la densite du morceau.
# ==========================================================================

PAD_BANDE = 100
# nom, debut et fin en Hz, lissage (s), ecart minimal entre deux coups (s)
BANDES = (
    ("sous-basses",    20,    60, 0.050, 0.16),
    ("graves",         60,   160, 0.045, 0.14),
    ("bas medium",    160,   400, 0.020, 0.10),
    ("medium",        400,  1000, 0.015, 0.09),
    ("haut medium",  1000,  2500, 0.010, 0.07),
    ("aigus",        2500,  6000, 0.008, 0.06),
    ("tres aigus",   6000, 15000, 0.008, 0.05),
)
BANDE_QUOI = {
    "sous-basses": "ce qui se sent plus qu'il ne s'entend",
    "graves": "la grosse caisse et le corps de la basse",
    "bas medium": "les toms, la caisse claire, le grave des voix",
    "medium": "le corps des voix et des accords",
    "haut medium": "l'attaque des sons, ce qui les rend clairs",
    "aigus": "les charleys, le grain, les consonnes",
    "tres aigus": "l'air, les cymbales, le souffle",
}

PAD_HASARD = 120
# nom, part des pas du sequenceur qui sont tires
HASARDS = (("hasard rare", 0.06), ("hasard moyen", 0.18),
           ("hasard dense", 0.45))

for _k, (_nom, _lo, _hi, _sm, _gap) in enumerate(BANDES):
    FAMILLES[_nom] = (PAD_BANDE + _k,)
for _k, (_nom, _p) in enumerate(HASARDS):
    FAMILLES[_nom] = (PAD_HASARD + _k,)

# Une part se note apres le nom : « caisse claire · 1 sur 2 ». Deux effets
# poses l'un sur « 1 sur 2 » et l'autre sur « l'autre sur 2 » ne peuvent
# jamais partir en meme temps.
SEPARATEUR = " \u00b7 "
PARTS = {
    "1 sur 2": (2, 0),
    "l'autre sur 2": (2, 1),
    "1 sur 3": (3, 0),
    "1 sur 4": (4, 0),
}
# Les familles auxquelles on propose les parts : les bandes et le hasard ont
# deja de quoi se separer, et la liste resterait lisible.
PARTAGEES = ("grosse caisse", "basse", "caisse claire", "percussions",
             "charley", "accords")


def decoupe_declencheur(nom):
    """« caisse claire · 1 sur 2 » -> (« caisse claire », 2, 0)."""
    nom = str(nom or "")
    if SEPARATEUR in nom:
        base, part = nom.split(SEPARATEUR, 1)
        div, reste = PARTS.get(part, (1, 0))
        return base, div, reste
    return nom, 1, 0


def groupes_declencheurs():
    """La liste complete, groupee comme la page l'affiche."""
    return [
        ("Instruments", list(INSTRUMENTS)),
        ("Bandes de frequences (une hauteur, pas un instrument)",
         [n for n, _, _, _, _ in BANDES]),
        ("Hasard, pose sur la grille du morceau", [n for n, _ in HASARDS]),
        ("Un coup sur deux, pour que deux effets ne tombent pas ensemble",
         [f + SEPARATEUR + p for f in PARTAGEES for p in PARTS]),
    ]


DECLENCHEURS = tuple(n for _, noms in groupes_declencheurs() for n in noms)


def compte_frappes(ev_pad):
    """Combien de coups chaque declencheur compte dans le morceau.

    C'est ce chiffre que le studio affiche sous chaque curseur. Il est
    calcule sur les vrais evenements, parts comprises : « un coup sur trois »
    annonce bien le tiers.
    """
    ev_pad = np.asarray(ev_pad)
    out = {}
    for nom in DECLENCHEURS:
        base, div, reste = decoupe_declencheur(nom)
        pads = FAMILLES.get(base)
        sel = (np.ones(len(ev_pad), bool) if pads is None
               else np.isin(ev_pad, pads))
        n = int(sel.sum())
        out[nom] = n if div <= 1 else len(range(reste, n, div))
    return out


def hasard_events(dur, beat, phi, seed=7):
    """Des coups tires au sort, mais poses sur la grille du morceau.

    Un vrai hasard continu tomberait a contretemps et aurait l'air d'un
    defaut ; cale sur la double-croche, il a l'air joue. Le tirage est seme,
    donc deux rendus du meme morceau donnent exactement les memes coups —
    ce dont le calcul en parallele a besoin.
    """
    rng = np.random.default_rng(int(seed) + 991)
    pas = max(0.02, float(beat) / 4.0)
    n = max(1, int(float(dur) / pas))
    out = []
    for k, (_nom, part) in enumerate(HASARDS):
        tirage = rng.random(n)
        forces = rng.uniform(0.45, 1.0, n)
        for i in np.nonzero(tirage < part)[0]:
            out.append((float(phi + i * pas), PAD_HASARD + k,
                        float(forces[i]), 10.0))
    return out

# Une teinte par famille, pour l'option « couleurs par instrument ». Elles sont
# choisies bien separees sur le cercle : le faisceau etant additif et passant
# ensuite dans un halo, deux teintes voisines se melangeraient en une bouillie.
TEINTES = {
    "grosse caisse": (1.00, 0.24, 0.20),      # rouge
    "basse":         (0.62, 0.30, 1.00),      # violet
    "caisse claire": (1.00, 0.92, 0.36),      # jaune
    "percussions":   (1.00, 0.56, 0.14),      # orange
    "charley":       (0.34, 0.95, 1.00),      # cyan
    "accords":       (0.40, 1.00, 0.52),      # vert
}
PAD_BASS = {"A1": 1, "G1": 1, "C2": 2, "D2": 2, "E2": 3}
PAD_SKANK = (12, 13, 14, 15)
DECAY_OF = {"kick": 4.5, "rim": 8.0, "hat": 14.0, "perc": 13.0,
            "bass": 3.0, "skank": 5.0}


def _lowpass(x, width):
    k = max(1, int(width))
    if k <= 1:
        return x
    c = np.cumsum(np.concatenate([[0.0], x]))
    y = (c[k:] - c[:-k]) / k
    if len(y) < len(x):
        y = np.concatenate([y, np.full(len(x) - len(y), y[-1] if len(y) else 0.0)])
    return y


def _highpass(x, width):
    return x - _lowpass(x, width)


def _tanh_limit(x, drive=1.3):
    return np.tanh(x * drive) / math.tanh(drive)


def _tape_echo(x, delay, sr, fb=0.52, taps=7, damp=9):
    """Echo a bande : chaque repetition est un peu plus sourde."""
    y = np.zeros_like(x)
    d = max(1, int(delay * sr))
    cur = x
    for k in range(1, taps + 1):
        cur = _lowpass(cur, damp)
        g = fb ** k
        if g < 0.012 or d * k >= len(x):
            break
        y[d * k:] += cur[:len(x) - d * k] * g
    return y


def _whoosh(dur, sr, rng, up=True):
    """Souffle : bruit filtre dont le centre spectral s'ouvre (ou se referme).

    Pas de composante tonale et pas de bande criarde : on veut de l'air, pas
    un effet de transition tape-a-l'oeil.
    """
    n = max(16, int(dur * sr))
    u = np.arange(n) / (n - 1.0)
    nz = rng.standard_normal(n)
    # bandes decalees vers le haut, la derniere est un vrai passe-haut :
    # on cherche de l'air, pas un grondement.
    bands = [_lowpass(nz, 150), _lowpass(nz, 48), _lowpass(nz, 16), nz - _lowpass(nz, 4)]
    bands = [b / (b.std() + 1e-9) for b in bands]
    pos = u if up else 1.0 - u
    out = np.zeros(n)
    for i, b in enumerate(bands):
        out += b * np.exp(-((pos - i / 3.0) / 0.34) ** 2) * (0.72, 0.95, 1.05, 1.00)[i]
    env = pos ** 1.4
    if up:
        env = env * (1.0 - 0.85 * np.clip((u - 0.92) / 0.08, 0, 1))
    return out * env * 0.115


def _tv_off(dur, sr, rng):
    """Extinction d'un televiseur : claquement de l'interrupteur, sifflement de
    ligne qui meurt en glissant, image qui se referme, coup de transformateur."""
    n = max(64, int(dur * sr))
    t = np.arange(n) / sr
    out = np.zeros(n)

    k = int(0.004 * sr)                                  # claquement
    cl = rng.standard_normal(k)
    out[:k] += (cl - _lowpass(cl, 6)) * np.exp(-np.arange(k) / sr * 900.0) * 0.46

    fw = 12500.0 * np.exp(-t * 3.2) + 900.0               # sifflement de ligne
    out += np.sin(2 * math.pi * np.cumsum(fw) / sr) * np.exp(-t * 20.0) * 0.11

    fc = 2600.0 * np.exp(-t * 26.0) + 70.0                # l'image se referme
    out += np.sin(2 * math.pi * np.cumsum(fc) / sr) * np.exp(-t * 15.0) * 0.13

    nz = rng.standard_normal(n)                           # souffle qui s'ecrase
    out += (nz - _lowpass(nz, 5)) * np.exp(-t * 24.0) * 0.09

    ft = 78.0 * np.exp(-t * 16.0) + 26.0                  # coup de transfo
    out += np.sin(2 * math.pi * np.cumsum(ft) / sr) * np.exp(-t * 9.0) * 0.30
    return out


def _reverb_ir(sr, dur=2.6, decay=1.15, seed=5):
    n = int(dur * sr)
    t = np.arange(n) / sr
    rng = np.random.default_rng(seed)
    ir = rng.standard_normal(n) * np.exp(-t / decay)
    ir = _lowpass(ir, 7)
    a = int(0.015 * sr)
    ir[:a] *= np.linspace(0.0, 1.0, a)
    return ir / (np.sqrt((ir ** 2).sum()) + 1e-9)


def _fft_conv(x, h):
    n = len(x) + len(h) - 1
    N = 1 << (n - 1).bit_length()
    return np.fft.irfft(np.fft.rfft(x, N) * np.fft.rfft(h, N), N)[:len(x)]


def synth_audio(duration=DUREE_REF, sr=SR, seed=3):
    """Renvoie {'stereo', 'mono', 'events', 'sr'} — dub ambient en 4 mesures."""
    tl = Timeline(duration)
    beat = duration / 20.0                # 20 temps sur toute la piece (125 BPM)
    six = beat / 4.0
    n = int(duration * sr) + 1
    rng = np.random.default_rng(seed)

    dry = np.zeros(n)      # direct
    ech = np.zeros(n)      # depart echo a bande
    rev = np.zeros(n)      # depart reverb
    events = []

    def seg(d):
        return np.arange(max(1, int(d * sr))) / sr

    def add(buf, sig, at, gain=1.0):
        i0 = max(0, int(at * sr))
        i1 = min(n, i0 + len(sig))
        if i1 > i0:
            buf[i0:i1] += sig[:i1 - i0] * gain

    def fire(at, kind, pad, force):
        if at < duration:
            events.append((float(at), int(pad), float(force), DECAY_OF[kind]))

    # ---------------------------------------------------------------- voix
    def kick(at, f=1.0):
        """Grosse caisse ronde et profonde, sans clic : le pouls dub techno."""
        t = seg(0.85)
        fr = 43.0 + 66.0 * np.exp(-t * 24.0)
        s = np.sin(2 * math.pi * np.cumsum(fr) / sr) * np.exp(-t * 4.0)
        s += _lowpass(rng.standard_normal(len(t)), 70) * np.exp(-t * 26.0) * 0.22
        add(dry, s, at, 0.90 * f)
        add(rev, s, at, 0.10 * f)
        fire(at, "kick", PAD_OF["kick"], f)

    def chord(at, k, f=1.0):
        """L'accord bref des contretemps : peu de direct, beaucoup d'echo.
        C'est lui qui fait le grain dub techno."""
        t = seg(0.60)
        env = np.minimum(t / 0.010, 1.0) * np.exp(-t * 8.5)
        s = np.zeros(len(t))
        for f0, g in ((NOTES["A3"], 1.0), (NOTES["C4"], 0.85),
                      (NOTES["E4"], 0.72), (NOTES["G4"], 0.55)):
            s += g * (np.sin(2 * math.pi * f0 * t)
                      + 0.45 * np.sin(4 * math.pi * f0 * t + 0.4)
                      + 0.18 * np.sin(6 * math.pi * f0 * t))
        s = _lowpass(s * env, 6)
        s = s - _lowpass(s, 90)                 # timbre creux, sans bas
        add(dry, s, at, 0.05 * f)
        add(ech, s, at, 0.17 * f)
        add(rev, s, at, 0.11 * f)
        fire(at, "skank", PAD_SKANK[k % 4], f)

    def shaker(at, f=1.0):
        t = seg(0.11)
        s = _highpass(rng.standard_normal(len(t)), 4) * np.exp(-t * 48)
        add(dry, s, at, 0.045 * f)
        add(rev, s, at, 0.08 * f)
        fire(at, "hat", PAD_OF["hat"], 0.5 * f)

    def rimshot(at, f=1.0):
        t = seg(0.26)
        nz = _highpass(rng.standard_normal(len(t)), 6)
        s = nz * np.exp(-t * 32) * 0.6 + np.sin(2 * math.pi * 620 * t) * np.exp(-t * 44) * 0.4
        add(dry, s, at, 0.10 * f)
        add(ech, s, at, 0.60 * f)
        add(rev, s, at, 0.38 * f)
        fire(at, "rim", PAD_OF["rim"], f)

    def bass(at, name, dur, f=1.0):
        """Sub tenu, presque sans harmonique."""
        t = seg(dur)
        f0 = NOTES[name]
        env = np.minimum(t / 0.030, 1.0) * np.exp(-t * 1.0)
        env *= np.clip((dur - t) / 0.12, 0, 1)
        s = (np.sin(2 * math.pi * f0 * t) + 0.12 * np.sin(4 * math.pi * f0 * t)) * env
        add(dry, s, at, 0.50 * f)
        fire(at, "bass", PAD_BASS[name], 0.7 * f)

    # ------------------------------- le lit : nappe, sub, souffle de bande
    t_all = np.arange(n) / sr
    swell = (np.clip(smoothstep(0.0, beat * 2.2, t_all), 0, 1)
             * (1.0 - smoothstep(duration - 0.55, duration, t_all)))
    swell = swell * (0.86 + 0.14 * np.sin(2 * math.pi * 0.19 * t_all))

    pad = np.zeros(n)
    for name, g in (("A2", 1.0), ("C3", 0.80), ("E3", 0.70),
                    ("G3", 0.55), ("B3", 0.40), ("E4", 0.22)):
        f0 = NOTES[name]
        pad += g * (np.sin(2 * math.pi * f0 * t_all + f0)
                    + 0.55 * np.sin(2 * math.pi * f0 * 1.004 * t_all))   # battement
    opening = np.clip(smoothstep(0.0, tl.start("title"), t_all), 0, 1)   # le filtre s'ouvre
    pad = _lowpass(pad, 70) * (1.0 - opening) + _lowpass(pad, 11) * opening
    dry += pad * swell * 0.032
    rev += pad * swell * 0.050

    dry += np.sin(2 * math.pi * 55.0 * t_all) * swell * 0.095            # sub tenu

    hiss = _lowpass(rng.standard_normal(n), 14)
    dry += hiss * swell * 0.032
    crackle = (rng.random(n) < 0.00028).astype(np.float64) * rng.standard_normal(n)
    dry += _lowpass(crackle, 3) * swell * 0.22                           # grain de bande

    # ------------------------------------------- 1. souffle d'ouverture
    g0, g1 = tl.start("groove"), tl.end("groove")
    add(dry, _whoosh(g0 - 0.02, sr, rng, up=True), 0.02, 0.75)
    add(rev, _whoosh(g0 - 0.02, sr, rng, up=True), 0.02, 0.30)

    # ------------------------------------------------- 2. groove dub techno
    n_steps = max(4, int(round((g1 - g0) / six)))
    ck = 0
    for i in range(n_steps):
        at = g0 + i * six
        k = i % 16
        if k in KICKS:
            kick(at, 1.0 if k == 0 else 0.92)
        if k in SKANKS:
            chord(at, ck, 0.95 if k in (2, 10) else 0.72)
            ck += 1
        if k in HATS:
            shaker(at, 0.7)
        if k in RIMS:
            rimshot(at, 0.7)
        for st, name, dur in BASSLINE[0]:
            if st == k:
                bass(at, name, dur * six)

    # --------------------------------- 3. break : la matiere part dans l'echo
    b0, t0 = tl.start("zoom"), tl.start("title")
    add(dry, _whoosh(t0 - b0, sr, rng, up=True), b0, 0.85)
    add(rev, _whoosh(t0 - b0, sr, rng, up=True), b0, 0.40)
    chord(b0, ck, 0.95)
    chord(b0 + 2 * six, ck + 1, 0.60)

    # -------------------------------------------- 4. impact puis longue traine
    ti = seg(min(3.2, duration - t0))
    fi = 30.0 + 120.0 * np.exp(-ti * 9.0)
    imp = np.sin(2 * math.pi * np.cumsum(fi) / sr) * np.exp(-ti * 1.9) * 0.95
    imp += _lowpass(rng.standard_normal(len(ti)), 12) * np.exp(-ti * 3.5) * 0.28
    add(dry, imp, t0)
    add(rev, imp * 0.45, t0)
    for k in range(3):
        chord(t0 + (4 + 6 * k) * six, ck + 2 + k, 0.34 - 0.09 * k)

    # ------------------------------------------------------ 5. sortie
    o0 = tl.start("out")
    add(dry, _whoosh(0.30, sr, rng, up=True), o0 - 0.24, 0.70)
    add(dry, _whoosh(max(0.12, duration - o0), sr, rng, up=False), o0, 0.80)
    tq = seg(min(0.35, duration - o0))
    fq = 90.0 * np.exp(-tq * 14.0) + 26.0
    add(dry, np.sin(2 * math.pi * np.cumsum(fq) / sr) * np.exp(-tq * 7.0) * 0.50, o0)

    # ------------------------------------------------------------- mixage
    mix = dry + _tape_echo(ech, beat * 0.75, sr, fb=0.64, taps=10, damp=13) * 0.60
    mix += _fft_conv(rev, _reverb_ir(sr, dur=3.4, decay=1.7)) * 0.55
    mix = _tanh_limit(mix * 0.95, 1.4)
    fade = np.clip(np.arange(n) / (0.04 * sr), 0, 1) * np.clip((n - np.arange(n)) / (0.10 * sr), 0, 1)
    mix *= fade
    mix /= (np.max(np.abs(mix)) or 1.0) / 0.94

    d = int(0.0009 * sr)
    right = np.concatenate([np.zeros(d), mix[:-d]]) * 0.96 + mix * 0.04
    st = np.stack([mix, right], axis=1)
    events.sort()
    return {"stereo": (np.clip(st, -1, 1) * 32767).astype("<i2"),
            "mono": mix.astype(np.float32), "events": events, "sr": sr,
            "beat": beat}


def write_wav(path, data, sr=SR):
    with wave.open(path, "wb") as f:
        f.setnchannels(2)
        f.setsampwidth(2)
        f.setframerate(sr)
        f.writeframes(data.tobytes())


# ==========================================================================
#  Scenario — cale sur la grille musicale (mesure = duree / 4)
# ==========================================================================

class Timeline:
    REF = DUREE_REF
    KEYS = [                        # cales sur les temps du morceau (0,704 s)
        ("boot", 0.000, 1.056),     # la piste s'enregistre, sur la musique
        ("sweep", 1.056, 3.168),    # la mue, qui deborde d'un demi-temps sur
        ("groove", 2.816, 5.632),   # le drop — une mesure pleine de groove
        ("zoom", 5.632, 6.336),     # la camera entre dans l'ecran de la machine
        ("title", 6.336, 9.152),    # quatre temps : le balayage prend son temps
        ("hold", 9.152, 11.208),    # +1s : le logo reste plus longtemps
        ("out", 11.208, 11.500),
    ]

    def __init__(self, duration):
        f = duration / self.REF
        self.d = duration
        self.seg = {n: (a * f, b * f) for n, a, b in self.KEYS}

    def at(self, name, t):
        a, b = self.seg[name]
        return (t - a) / max(1e-9, b - a)

    def start(self, name):
        return self.seg[name][0]

    def end(self, name):
        return self.seg[name][1]


GLITCHES = [(2.79, .09), (4.22, .05), (5.61, .10), (6.31, .11),
            (9.13, .06), (10.70, .05), (11.05, .06)]


# ==========================================================================
#  Rendu
# ==========================================================================

TITLE_H = 0.27
CURVE_AMP = 0.28
CURVE_WIN = 0.070          # fenetre d'analyse affichee (s) — "base de temps"
# clip audio facon station de travail (Audacity / Live)
CLIP = (-1.74, -0.50, 1.74, 0.50)
CLIP_BAR_H = 0.12
WAVE_YMAX = 0.36
DAW_COLS = 560

SUB_TXT = "HARDWARE ONLY"
SUB_H, SUB_Y, SUB_TRACK = 0.065, -0.260, 0.55   # plus bas, plus loin du logo
WEIGHT_OF = {ONDE: 0.80, TRAIT: 1.15, TRANSIT: 0.10, LIAISON: 0.80}
THICK_OF = {ONDE: 0.0030, TRAIT: 0.0052, TRANSIT: 0.0, LIAISON: 0.0030}

# pas programmes dans le motif : ils restent faiblement allumes
STEP_LIT = frozenset(KICKS + RIMS + HATS + PERCS + SKANKS)


class Renderer:
    def __init__(self, w, h, fps, duration, audio, curve=True, seed=7,
                 palette="vert", subtitle=SUB_TXT, bg=None, bg_color=None,
                 bg_strength=1.0, bg_clear=0.55, bg_anim=0.0, nettete=1.0,
                 machine="mpc", machines=None, passage=1.9, passage_turb=1.0,
                 midi=None, midi_offset=0.0, midi_transpose=0, midi_force=1.0):
        self.W, self.H = w, h
        self.fps = fps
        self.dur = duration
        self.tl = Timeline(duration)
        self.curve = curve
        self.seed = seed
        self.beat = float(audio.get("beat") or duration / 20.0)
        self.six = self.beat / 4.0           # la double-croche du morceau

        # la machine reste cadree quel que soit le format (16/9, carre, vertical)
        self.scale = min(h * 0.5, w * 0.5 / 1.30)
        # Taille et presence de la machine dans l'image. Elles ne touchent
        # qu'elle : le quadrillage du fond et le fil du morceau tiennent la
        # largeur de l'ecran et n'ont pas a retrecir avec elle.
        self.taille = 1.0
        self.presence = 1.0
        self._ech = 1.0                      # echelle du dessin en cours
        # Le neon : de quelle force il eclaire, a quelle distance se tient la
        # surface qui lui renvoie sa lumiere, et s'il a l'epaisseur d'un tube
        # de verre. Les valeurs par defaut redonnent exactement l'ancien rendu.
        self.neon = 1.0
        self.reflet = 0.5
        self.tube = 0.0
        self.set_look(palette, bg, bg_color, bg_strength, bg_clear, bg_anim)
        self._zoom = 1.0                     # respiration de l'image sur les kicks
        self._cam = (0.0, 0.0)               # camera : centre, puis dans l'ecran
        self._cam_z = 1.0
        # `nettete` resserre le faisceau : a 1 on garde le rendu d'origine, au
        # dela le trait s'affine. On ne descend pas sous 0,55 pixel — en
        # dessous, l'etalement bilineaire ne suffit plus a lisser et le trait
        # se met a monter en marches d'escalier.
        self.sigma = max(0.55, h / 1080.0 * 0.95 / max(0.5, float(nettete)))
        # un trait garde la meme luminosite quelle que soit la definition
        self.gain = (self.scale * self.sigma) / (360.0 * 0.6333)

        # ---- son : forme d'onde affichee, enveloppes, evenements
        sr = audio["sr"]
        self.sr = sr
        mono = audio["mono"].astype(np.float64)
        self._mono = mono.astype(np.float32)          # garde pour relisser
        self.set_wave_smooth(56)                      # ce que "voit" l'ecran
        low = _lowpass(mono, 26)
        self.eh = 240.0                               # resolution des enveloppes
        stepi = max(1, int(sr / self.eh))
        def env(x, width):
            e = _lowpass(np.abs(x), int(sr * width))[::stepi]
            return (e / (np.max(e) or 1.0)).astype(np.float32)
        self.e_full = env(mono, 0.030)
        self.e_low = env(low, 0.045)
        self.e_high = env(mono - low, 0.012)
        # enveloppe crete du morceau : c'est le dessin du clip
        edges = np.linspace(0, len(mono), DAW_COLS + 1).astype(np.int64)
        env = np.array([np.abs(mono[a:b]).max() if b > a else 0.0
                        for a, b in zip(edges[:-1], edges[1:])])
        self.daw = (env / (env.max() or 1.0)) ** 0.82
        self.daw_x = np.linspace(CLIP[0] + 0.02, CLIP[2] - 0.02, DAW_COLS)

        ev = audio["events"]
        self.ev_t = np.array([e[0] for e in ev], dtype=np.float64)
        self.ev_pad = np.array([e[1] for e in ev], dtype=np.int32)
        self.ev_f = np.array([e[2] for e in ev], dtype=np.float64)
        self.ev_d = np.array([e[3] for e in ev], dtype=np.float64)
        self.ev_bass = self.ev_pad <= 3          # grosse caisse et notes de basse
        # les declencheurs sans pad (bandes, hasard) ne rallument rien sur la
        # machine et ne comptent pas dans la densite du morceau
        self.ev_reel = self.ev_pad < PADS_REELS
        self._masques = {}

        # ---- geometrie
        #
        # Le sequenceur de machines : a partir de quel instant laquelle est a
        # l'image. Une seule entree — le cas ordinaire — et rien ne change de
        # tout le rendu : pas de passage a calculer, pas de trace a refaire.
        self.machine = (str(machine) if str(machine) in MACHINES else "mpc")
        self.plan_mach = lire_plan_machines(machines, self.machine, duration)
        self.machine = self.plan_mach[0][1]
        self.passage = max(0.0, float(passage))
        self.passage_turb = float(passage_turb)
        self._passages = {}                  # prepares a la demande
        self._cle_mach = (self.machine, None, 0.0)
        self._plan = None                    # plan intermediaire, en passage
        self.ecran = self.mach["ecran"]
        self.mpc = self.mach["build"]()

        # ---- le fichier MIDI, quand il y en a un : la vraie melodie du
        # morceau, note par note. Quatre colonnes — debut, fin, hauteur,
        # force — et rien d'autre : c'est tout ce qu'une touche a besoin de
        # savoir pour s'allumer au bon moment.
        self.midi = (np.asarray(midi, dtype=np.float64).reshape(-1, 4)
                     if midi is not None and len(midi) else None)
        self.midi_offset = float(midi_offset)
        self.midi_transpose = int(midi_transpose)
        self.midi_force = float(midi_force)
        (self.tP, self.tN, self.tkind,
         self.ts, self.tlen) = build_title_curve("OMNIPOTARD", TITLE_H, 0.0)
        tx = self.tP[self.tkind == TRAIT][:, 0]
        self.word_x = (float(tx.min()), float(tx.max()))
        sub = text_paths(subtitle, SUB_H, 0.0, SUB_Y, tag="sub", tracking=SUB_TRACK)
        self.subP = np.vstack([q.P for q in sub])
        subN = []
        for q in sub:
            tan = np.gradient(q.P, axis=0)
            tan /= (np.linalg.norm(tan, axis=1, keepdims=True) + 1e-12)
            subN.append(np.stack([-tan[:, 1], tan[:, 0]], axis=1))
        self.subN = np.vstack(subN)
        self.tw = np.array([WEIGHT_OF[int(k)] for k in self.tkind])
        self.tth = np.array([THICK_OF[int(k)] for k in self.tkind])
        self.is_line = ((self.tkind == TRAIT) | (self.tkind == TRANSIT)).astype(np.float64)

        if curve:
            self._build_warp()

    # -- son ---------------------------------------------------------------

    def glitch_at(self, t):
        """Quantite de glitch a l'instant t : coups ponctuels, puis rafales
        continues pendant l'extinction."""
        if self.glitch <= 0.001:
            return 0.0
        if self.drops is not None:
            # rendu « performance » : une rafale courte sur chaque paroxysme
            dt = t - self.drops
            m = (dt >= 0.0) & (dt < self.drop_dur)
            if not np.any(m):
                return 0.0
            return self.glitch * float(np.max(1.0 - dt[m] / self.drop_dur))
        f = self.dur / Timeline.REF
        g = 0.0
        for gt, gd in GLITCHES:
            gt, gd = gt * f, gd * f
            if 0.0 <= t - gt < gd:
                g = max(g, 1.0 - (t - gt) / gd)
        u = self.tl.at("out", t)
        if 0.0 <= u < 0.80:
            burst = 0.40 + 0.24 * math.sin(u * 31.0) ** 2
            g = max(g, burst * (1.0 - 0.55 * smoothstep(0.42, 0.80, u)))
        return self.glitch * g

    def set_wave_smooth(self, width, passes=None):
        """Lissage de la courbe affichee.

        Plus il est large, plus le trace est calme : on suit le mouvement du
        grave au lieu du detail du haut du spectre, qui donnait un tremblement
        illisible d'une image a l'autre.
        """
        if self._mono is None:
            raise RuntimeError("le lissage de la courbe se regle avant le rendu, "
                               "pas dans une tache de rendu")
        w = self._mono.astype(np.float64)
        # Trois passages plutot qu'un : une moyenne glissante seule ne descend
        # qu'a 6 dB par octave et laisse passer assez d'aigu pour que le trace
        # saute quand meme d'une image a l'autre. Cascadee, elle approche une
        # gaussienne et coupe franchement.
        for _ in range(max(1, int(self.wave_passes if passes is None else passes))):
            w = _lowpass(w, max(1, int(width)))
        self.wave = (w / (np.max(np.abs(w)) or 1.0)).astype(np.float32)
        self.nw = len(self.wave)

    def env_at(self, arr, t):
        i = int(np.clip(t * self.eh, 0, len(arr) - 1))
        return float(arr[i])

    def wave_mod(self, x):
        """Amplitude de l'onde le long du fil : pleine au loin, presque nulle
        sous le mot, avec une transition douce — le trace reste continu."""
        a, b = self.word_x
        inside = smoothstep(a - 0.32, a + 0.02, x) * (1.0 - smoothstep(b - 0.02, b + 0.32, x))
        return 1.0 - 0.92 * inside

    def clip_env(self, x):
        """Demi-hauteur de la forme d'onde du clip a l'abscisse x."""
        return WAVE_YMAX * np.interp(x, self.daw_x, self.daw)

    def morph_at(self, x, sweep_x):
        """0 = encore ecrase dans le clip, 1 = deploye en machine.

        La zone de transition est large : a un instant donne, une bonne partie
        de la machine est en train de s'ouvrir, ce qui donne une materialisation
        progressive plutot qu'un volet net.
        """
        m = np.clip((sweep_x - np.asarray(x) + 0.10) / 0.85, 0.0, 1.0)
        return m * m * (3.0 - 2.0 * m)

    def wave_y(self, x, t, amp=CURVE_AMP, win=None, agc=True):
        win = self.wave_win if win is None else win
        """Forme d'onde du morceau, etalee sur la largeur de l'ecran.

        Le gain suit l'inverse de l'enveloppe (comme le calibre automatique
        d'un oscilloscope) : les passages calmes restent lisibles.
        """
        amp = amp * self.wave_gain
        if agc:
            # L'amplitude SUIT le niveau : petite quand c'est calme, grande
            # quand ca pousse. C'etait l'inverse avant — un calibre automatique
            # d'oscilloscope, qui remontait les passages calmes et gardait donc
            # la courbe haute en permanence.
            amp = amp * float(np.clip(0.22 + 1.45 * self.env_at(self.e_full, t),
                                      0.14, 1.70))
            # et elle gonfle sur le temps fort : c'est ce coup-la qu'on veut
            # voir passer dans la bande.
            amp = amp * (1.0 + self.wave_punch * self.kick_hit(t))
        if self.wave_trig > 0.0:
            # Declenchement, comme sur un oscilloscope : le balayage repart au
            # debut de chaque temps et l'ecran montre exactement un temps de
            # musique. Sans cela le trace saute a chaque image — a 30 i/s on
            # echantillonne le son toutes les 33 ms, et tout ce qui depasse une
            # quinzaine de hertz a change entre deux images. Le resultat est
            # illisible, alors que la forme est ici stable pendant tout le
            # temps et ne se renouvelle qu'au suivant.
            # La periode de declenchement et la largeur de la fenetre sont
            # deux choses differentes : on repart au debut de chaque temps,
            # mais on ne montre que `win` secondes de son. Etaler un temps
            # entier sur la dalle donnerait une centaine de cycles, soit une
            # bande pleine et illisible.
            q = self.beat * self.wave_trig
            t0 = math.floor(t / q) * q
            tt = t0 + (np.asarray(x) / 1.88 + 1.0) * 0.5 * win
        else:
            tt = t + (np.asarray(x) / 1.88) * (win * 0.5)
        i = tt * self.sr
        i0 = np.floor(i).astype(np.int64)
        f = i - i0
        i0 = np.clip(i0, 0, self.nw - 2)
        return amp * (self.wave[i0] * (1.0 - f) + self.wave[i0 + 1] * f)

    def pad_flashes(self, t):
        dt = t - self.ev_t
        m = (dt >= 0.0) & (dt < 1.6) & self.ev_reel
        out = {}
        if not np.any(m):
            return out
        v = self.ev_f[m] * np.exp(-self.ev_d[m] * dt[m])
        for pad, val in zip(self.ev_pad[m], v):
            if val > 0.02:
                out[int(pad)] = max(out.get(int(pad), 0.0), float(val))
        return out

    def notes_midi(self, t, touches, note0):
        """Les touches allumees a l'instant t, avec leur intensite.

        Une note tient tant qu'elle est tenue, puis retombe en un cinquieme de
        seconde ; l'attaque est plus vive que la tenue, sans quoi une note
        longue et une note repetee se ressemblent.

        Une hauteur qui tombe hors du clavier y est ramenee par octaves : le
        dessin garde la note, il change seulement d'octave. C'est ce qui
        permet a seize pads de rendre une melodie ecrite sur cinq octaves.
        """
        if self.midi is None or not len(self.midi) or self.midi_force <= 0.001:
            return {}
        mt = t + self.midi_offset
        deb, fin, haut, force = (self.midi[:, 0], self.midi[:, 1],
                                 self.midi[:, 2], self.midi[:, 3])
        vus = (mt >= deb) & (mt < fin + 0.30)
        if not np.any(vus):
            return {}
        deb, fin, haut, force = deb[vus], fin[vus], haut[vus], force[vus]
        v = force * (0.60 + 0.55 * np.exp(-(mt - deb) * 11.0))
        v = np.where(mt < fin, v, v * np.exp(-(mt - fin) * 9.0))
        v *= self.midi_force

        n = len(touches)
        k = haut.astype(np.int64) + self.midi_transpose - int(note0)
        bas = k < 0
        if np.any(bas):
            k = np.where(bas, k + 12 * ((-k + 11) // 12), k)
        trop = k >= n
        if np.any(trop):
            k = np.where(trop, k - 12 * ((k - n) // 12 + 1), k)
        garde = (k >= 0) & (k < n) & (v > 0.02)
        out = {}
        for kk, vv in zip(k[garde], v[garde]):
            kk = int(kk)
            out[kk] = max(out.get(kk, 0.0), float(vv))
        return out

    def stutter_time(self, t):
        """L'instant reellement dessine, quand le begaiement est actif.

        Sur chaque coup retenu, l'image cesse de suivre le son : elle rejoue
        en boucle un bout tres court pris a l'instant du coup. Une boucle
        plus courte qu'une image donne un gel pur ; une boucle de deux ou
        trois images donne un sursaut repete, bien plus visible — un gel seul
        ne se remarque que si l'image bougeait beaucoup juste avant.

        C'est un decalage du temps, pas un effet applique a l'image, d'ou son
        calcul avant que quoi que ce soit ne soit dessine. Et comme il ne
        depend que de l'instant demande, chaque tache de rendu le retrouve
        seule, sans rien connaitre des images voisines.
        """
        if self.stut <= 0.001 or len(self.ev_t) == 0:
            return t
        dt = t - self.ev_t
        m = (dt >= 0.0) & self._masque(self.stut_on, "charley")
        if not np.any(m):
            return t
        dernier = float(np.min(dt[m]))          # le coup le plus recent
        if dernier >= self.stut:
            return t
        boucle = max(1e-4, float(self.stut_loop))
        return t - dernier + (dernier % boucle)

    def tape_time(self, t):
        """Le temps qui ralentit puis rattrape, comme une bande qui patine.

        Sur le coup, l'image avance de moins en moins vite pendant la fenetre,
        puis retrouve le son d'un seul coup. Le son, lui, n'a jamais ralenti :
        c'est ce decalage qui fait l'effet.
        """
        if self.tapestop <= 0.001 or len(self.ev_t) == 0:
            return t
        fen = float(self.tapestop)
        dt = t - self.ev_t
        m = (dt >= 0.0) & (dt < fen) & self._masque(self.tapestop_on)
        if not np.any(m):
            return t
        d = float(np.min(dt[m]))
        u = d / fen
        # l'avance suit 1-(1-u)^3 : rapide au depart, presque nulle a la fin
        return t - d + fen * (1.0 - (1.0 - u) ** 3) * 0.5

    def scramble_time(self, t):
        """Le temps decoupe en tranches courtes, rejouees dans le desordre.

        Le son continue tout droit, l'image saute en avant et en arriere par
        petits blocs — le montage haché des disques de breakcore. Les tranches
        sont brassees par paquets de huit, d'un tirage seme par le numero du
        paquet : chaque tache de rendu retrouve donc le meme desordre sans
        rien savoir des images voisines.
        """
        if self.scramble <= 0.001:
            return t
        L = max(0.03, float(self.scr_len))
        PAQUET = 8
        i = int(t / L)
        g, b = divmod(i, PAQUET)
        r = np.random.default_rng(31337 + g)
        if r.random() > self.scramble:
            return t                            # ce paquet-la reste en ordre
        j = g * PAQUET + int(r.permutation(PAQUET)[b])
        return min(max(t + (j - i) * L, 0.0), self.dur - 1e-3)

    @property
    def mach(self):
        """Le plan de la machine dessinee.

        Hors passage il est garde par son nom et non par son contenu : le
        dictionnaire porte des fonctions, et le moteur voyage jusqu'aux taches
        de rendu. Pendant un passage c'est un plan intermediaire, fabrique
        pour l'image en cours.
        """
        return self._plan if self._plan is not None else MACHINES[self.machine]

    def etape_machine(self, t):
        """Quelle machine est a l'image a l'instant t.

        Renvoie (nom_a, nom_b, u). Hors passage, nom_b vaut None. Pendant un
        passage, u va de 0 (encore A) a 1 (deja B) ; le passage se termine a
        l'instant inscrit au sequenceur, il le precede donc.
        """
        plan = self.plan_mach
        i = 0
        while i + 1 < len(plan) and t >= plan[i + 1][0]:
            i += 1
        a = plan[i][1]
        if i + 1 >= len(plan) or self.passage <= 1e-6:
            return a, None, 0.0
        debut = plan[i + 1][0] - self.passage
        b = plan[i + 1][1]
        if t < debut or b == a:
            return a, None, 0.0
        return a, b, min(1.0, max(0.0, (t - debut) / self.passage))

    def _le_passage(self, a, b):
        """Le passage de a vers b, prepare a la premiere image qui en a
        besoin. Chaque tache de rendu prepare les siens : ils pesent plus lourd
        que le temps de les refaire."""
        if self._passages is None:
            self._passages = {}
        p = self._passages.get((a, b))
        if p is None:
            p = self._passages[(a, b)] = Passage(a, b)
        return p

    def poser_machine(self, t):
        """Pose la machine de l'instant : son trace, son plan, son ecran.

        Appelee au debut de chaque image. Hors passage rien n'est recalcule
        tant que la machine ne change pas, donc un rendu a machine unique ne
        paie rien du tout.
        """
        a, b, u = self.etape_machine(t)
        cle = (a, b, t if b is not None else 0.0)
        if cle == self._cle_mach:
            return
        self._cle_mach = cle
        if b is None:
            if self.machine != a or self._plan is not None:
                self.machine = a
                self._plan = None
                self.mpc = MACHINES[a]["build"]()
                self.ecran = MACHINES[a]["ecran"]
                self._pool = None
            return
        p = self._le_passage(a, b)
        self._plan = p.plan(u)
        self.mpc = p.chemins(u, t, self.passage_turb)
        self.ecran = self._plan["ecran"]
        self._pool = None

    def _masque(self, nom, defaut="grosse caisse"):
        """Les evenements que ce declencheur retient, une fois pour toutes.

        Un nom dit deux choses : sur quoi se caler — une famille, une bande
        de frequences, le hasard — et quelle part en garder. Les deux se
        resolvent ici, pour que tous les effets les lisent pareil.
        """
        m = self._masques.get(nom)
        if m is not None:
            return m
        base, div, reste = decoupe_declencheur(nom)
        pads = FAMILLES.get(base, FAMILLES.get(defaut))
        m = (np.ones(len(self.ev_pad), bool) if pads is None
             else np.isin(self.ev_pad, pads))
        if div > 1:
            # le rang se compte parmi les coups retenus, dans l'ordre du
            # morceau : « l'autre sur deux » tombe donc bien entre les
            # coups de « un sur deux »
            rang = np.full(len(self.ev_pad), -1, dtype=np.int64)
            rang[m] = np.arange(int(m.sum()))
            m &= (rang % div) == reste
        self._masques[nom] = m
        return m

    def hit_env(self, t, famille, fall=9.0, win=0.55, seuil=0.0, plancher=0.0):
        """Enveloppe des coups d'une famille d'instruments a l'instant t.

        Attaque immediate sur le coup, puis retombee exponentielle : c'est la
        forme que toutes les reactions partagent, seule la vitesse de chute
        change. Renvoyer une valeur continue plutot qu'un declenchement permet
        aux effets de retomber au lieu de clignoter.

        `plancher` releve la part de la force du coup : sur un morceau au
        mixage sage, les coups pesent 0,3 et un effet proportionnel a la force
        seule reste invisible quel que soit le reglage. Les avaries d'image,
        qui doivent frapper ou ne rien faire, s'en servent ; les reactions
        douces gardent la force nue.
        """
        dt = t - self.ev_t
        m = (dt >= 0.0) & (dt < win) & self._masque(famille)
        if seuil > 0.0:
            m &= self.ev_f >= seuil
        if not np.any(m):
            return 0.0
        f = self.ev_f[m]
        if plancher > 0.0:
            f = plancher + (1.0 - plancher) * f
        return float(np.max(f * np.exp(-fall * dt[m])))

    def kick_hit(self, t):
        """Enveloppe des grosses caisses seules : sert au zoom de l'image."""
        dt = t - self.ev_t
        m = (dt >= 0.0) & (dt < 0.45) & (self.ev_pad == PAD_OF["kick"])
        if not np.any(m):
            return 0.0
        return float(np.max(self.ev_f[m] * np.exp(-9.0 * dt[m])))

    def bass_hit(self, t):
        """Enveloppe des coups graves : le fil d'onde s'allume dessus."""
        dt = t - self.ev_t
        m = (dt >= 0.0) & (dt < 1.0) & self.ev_bass
        if not np.any(m):
            return 0.0
        return float(np.max(self.ev_f[m] * np.exp(-self.ev_d[m] * 1.15 * dt[m])))

    def split_times(self):
        """Les quelques instants ou le trait se dedouble, pour toute la video."""
        if getattr(self, "_split_t", None) is None:
            self._split_t = pick_split_times(
                self.ev_t, self.ev_f, self.split_mask(), self.dur,
                self.split_count)
        return self._split_t

    def split_mask(self):
        """Les coups qui ont le droit de declencher le dedoublement.

        Par defaut la grosse caisse seule. Auparavant tout le grave etait
        admis, notes de basse comprises — or une ligne de basse tombe souvent
        sur le meme temps que la caisse claire, et l'effet avait alors l'air
        de se declencher sur elle.
        """
        return self._masque(self.split_on)

    def sub_hit(self, t):
        """Enveloppe du dedoublement : attaque immediate, longue descente.

        Pres de deux secondes de retombee — c'est ce qui laisse le temps de
        voir les trois copies se separer puis se recoller.
        """
        st = self.split_times()
        if len(st) == 0:
            return 0.0
        dt = t - st
        m = (dt >= 0.0) & (dt < 2.2)
        if not np.any(m):
            return 0.0
        return float(np.max(np.exp(-1.35 * dt[m])))

    def snare_hit(self, t, thresh=0.42):
        """Caisse claire et percussions : elles eclairent le trait en jaune.

        Volontairement breve — la bande medium est bien fournie, et sans une
        retombee rapide la machine resterait jaune en permanence au lieu d'etre
        frappee par eclairs.
        """
        dt = t - self.ev_t
        m = ((dt >= 0.0) & (dt < 0.40) & (self.ev_f >= thresh)
             & ((self.ev_pad == PAD_OF["rim"]) | (self.ev_pad == PAD_OF["perc"])))
        if not np.any(m):
            return 0.0
        return float(np.max(self.ev_f[m] * np.exp(-11.0 * dt[m])))

    def progress_at(self, t):
        """Avancement dans la video, pour la barre de la dalle."""
        return t / self.dur if self.dur > 0 else 0.0

    def density(self, t, win=0.45):
        """Combien de familles d'instruments jouent en ce moment.

        C'est ce qui regle la longueur de la trainee : un morceau depouille
        laisse un trait net, un passage charge le fait bavez derriere lui.
        """
        dt = t - self.ev_t
        m = (dt >= 0.0) & (dt < win) & self.ev_reel
        if not np.any(m):
            return 0.0
        n = len(np.unique(self.ev_pad[m]))
        return float(np.clip((n - 1) / 3.0, 0.0, 1.0))

    def step_index(self, t):
        """Le pas allume dans la bande du haut.

        `step_div` est le denominateur du temps : 4 avance d'une double-croche
        par pas, 2 d'une croche, 1 d'une noire. Plus il est petit, plus la
        bande defile lentement — a seize pas, une valeur de 4 fait deux mesures
        entieres en quatre temps, ce qui va vite.
        """
        t0 = (self.tl.start("groove") if self.step_phase is None
              else self.step_phase)
        pas = self.beat / max(0.25, float(self.step_div))
        return int((t - t0) / pas) % 16

    # -- geometrie ecran ---------------------------------------------------

    def to_px(self, P, collapse=1.0, shake=(0.0, 0.0)):
        s = self.scale * self._ech * self._zoom * self._cam_z
        cx, cy = self._cam
        return (self.W * 0.5 + (P[:, 0] - cx) * s + shake[0],
                self.H * 0.5 - (P[:, 1] - cy) * s * collapse + shake[1])

    def in_screen(self, P):
        """Composition -> monde, posee dans l'ecran de la machine.

        Renvoie aussi le masque de decoupe : une dalle n'affiche que ce qui
        tient dedans."""
        yc = P[:, 1] - SCR_OY
        m = (np.abs(P[:, 0]) <= SCR_HW) & (np.abs(yc) <= SCR_HH)
        Q = np.empty_like(P)
        Q[:, 0] = SCR_C[0] + P[:, 0] * SCR_S
        Q[:, 1] = SCR_C[1] + yc * SCR_S
        return Q, m

    # Tables de deformation cathodique : elles pesent une centaine de
    # megaoctets en 4K alors qu'elles se deduisent de la seule definition de
    # l'image. On ne les transmet donc pas aux taches de rendu (voir
    # __getstate__), qui les refont a l'arrivee en une poignee de secondes.
    _WARP = ("ww00", "ww01", "ww10", "ww11", "wi00", "_vign", "_scan")

    # Valeurs de repli, portees par la classe et non par l'instance.
    #
    # Un moteur voyage : le studio le construit, puis l'envoie tel quel aux
    # taches de rendu, qui sont sous Windows des interpreteurs neufs. Si les
    # fichiers ont change entre les deux — une mise a jour faite sans fermer
    # le studio — la tache relit la nouvelle classe et recoit l'ancien etat.
    # Tout reglage ajoute depuis manque alors a l'appel, et le rendu s'arretait
    # sur un « object has no attribute ». Avec ces replis il repart sur la
    # valeur d'origine, et la page, elle, previent qu'il faut relancer.
    machine = "mpc"
    plan_mach = ((0.0, "mpc"),)
    passage = 1.9
    passage_turb = 1.0
    _plan = None
    _cle_mach = None
    _passages = None
    midi = None
    midi_offset = 0.0
    midi_transpose = 0
    midi_force = 1.0
    ecran = SCREEN
    taille = 1.0
    presence = 1.0
    neon = 1.0
    reflet = 0.5
    tube = 0.0
    _ech = 1.0
    bg_kind = "uni"
    bg_anim = 0.0
    c_bg_pat = None
    c_bg_creux = None
    step_div = 2.0

    def __getstate__(self):
        """Ce qu'on envoie a une tache de rendu.

        Sous Windows les taches ne sont pas des copies du processus principal :
        elles demarrent vierges et recoivent le moteur serialise, chacune son
        exemplaire. Tout ce qui se recalcule vite, ou ne sert qu'ici, reste
        donc a quai — sur un morceau de quatre minutes en 1080p, cela fait
        cent cinquante megaoctets de moins par tache.
        """
        etat = {k: v for k, v in self.__dict__.items() if k not in self._WARP}
        etat["_mono"] = None          # ne sert qu'a relisser la courbe
        etat["_passages"] = {}        # chaque tache prepare les siens
        etat["_pool"] = None          # se refait en une fraction de seconde
        return etat

    def __setstate__(self, state):
        self.__dict__.update(state)
        if self.curve:
            self._build_warp()

    def forget_warp(self):
        """Relache les tables de deformation.

        Sert au processus principal quand les taches de rendu sont des
        interpreteurs neufs : elles reconstruisent chacune les leurs, et lui
        ne dessine plus rien. Deux cent trente megaoctets de moins en 4K, la
        ou ils manquaient justement.
        """
        for k in self._WARP:
            self.__dict__.pop(k, None)

    def _tables_dalle(self):
        """Le peigne des scanlines et le vignettage, calcules une seule fois.

        Ils ne dependent que du format. Les refaire a chaque image coutait une
        puissance fractionnaire sur deux millions de pixels — et trois
        tableaux temporaires de la taille de l'image, que ce cache supprime
        aussi : la memoire de pointe n'y perd donc rien.
        """
        if getattr(self, "_vign", None) is not None:
            return
        W, H = self.W, self.H
        yy = np.arange(H, dtype=np.float32)[:, None]
        periode = max(2.0, H / 360.0)
        self._scan = (0.82 + 0.18 * (0.5 + 0.5 * np.cos(
            yy * np.float32(2.0 * math.pi / periode)))).astype(np.float32)
        ny = (yy / H - 0.5) * 2.0
        nx = (np.arange(W, dtype=np.float32)[None, :] / W - 0.5) * 2.0
        self._vign = (np.clip(1.06 - 0.42 * (nx * nx * 0.55 + ny * ny),
                              0.0, 1.0) ** 1.15).astype(np.float32)

    def _build_warp(self):
        """Tables de gather de la deformation cathodique.

        Les coordonnees se deduisent d'un vecteur par axe : deployer deux
        grilles completes en double precision coutait, en 4K, pres de cinq
        cents megaoctets de tableaux temporaires — et cela dans chacune des
        taches de rendu, qui les reconstruisent toutes. On diffuse donc les
        deux vecteurs, en simple precision : le resultat final est un indice
        de pixel et une fraction, que le float32 porte tres largement.
        """
        W, H = self.W, self.H
        nx = ((np.arange(W, dtype=np.float32) / (W - 1.0)) * 2.0 - 1.0)[None, :]
        ny = ((np.arange(H, dtype=np.float32) / (H - 1.0)) * 2.0 - 1.0)[:, None]
        f = nx * nx + ny * ny
        f *= np.float32(0.055)
        f += np.float32(1.0)                    # (H, W), le seul grand tableau
        sx = nx * f
        sx *= np.float32(0.5)
        sx += np.float32(0.5)
        sx *= np.float32(W - 1.0)
        sy = ny * f
        sy *= np.float32(0.5)
        sy += np.float32(0.5)
        sy *= np.float32(H - 1.0)
        del f
        dedans = ((sx >= 0) & (sx <= W - 1.001)
                  & (sy >= 0) & (sy <= H - 1.001))
        self.wmask = dedans.astype(np.float32)[..., None]
        del dedans
        np.clip(sx, 0, W - 1.001, out=sx)
        np.clip(sy, 0, H - 1.001, out=sy)
        x0 = sx.astype(np.int32)
        y0 = sy.astype(np.int32)
        sx -= x0
        sy -= y0
        # Les quatre poids bilineaires sont figes : les recalculer a chaque
        # image coutait trois multiplications de la taille de l'image. Le
        # masque des bords y est replie, ce qui en supprime une quatrieme.
        fx, fy = sx[..., None], sy[..., None]
        un = np.float32(1.0)
        self.ww00 = ((un - fx) * (un - fy)) * self.wmask
        self.ww01 = (fx * (un - fy)) * self.wmask
        self.ww10 = ((un - fx) * fy) * self.wmask
        self.ww11 = (fx * fy) * self.wmask
        del fx, fy, self.wmask
        # Les trois autres coins se deduisent de celui-ci (+1, +W, +W+1) :
        # les garder en memoire coutait trois tableaux d'indices pour rien.
        self.wi00 = (y0 * W + x0).ravel()

    BANDE = 192          # lignes traitees d'un coup dans la deformation

    def _warp(self, img):
        """Applique la deformation cathodique, par bandes horizontales.

        Les quatre prelevements bilineaires font chacun la taille de l'image :
        les mener de front sur toute la hauteur demandait, en 4K, plus d'un
        demi-gigaoctet de tableaux temporaires par image — dans chaque tache
        de rendu, et c'est la que les rendus manquaient de memoire. Decoupee
        en bandes, la meme operation n'en garde qu'une fraction a la fois. Le
        resultat est identique au pixel pres : on ne change que l'ordre.
        """
        W, H = self.W, self.H
        flat = img.reshape(-1, 3)
        # Les trois autres coins ne sont pas d'autres indices : c'est le meme
        # indice lu un peu plus loin. Quatre vues decalees suffisent donc, la
        # ou l'on fabriquait trois tableaux d'indices entiers par bande — de
        # la taille de la bande, a chaque image.
        coins = (flat, flat[1:], flat[W:], flat[W + 1:])
        poids = (self.ww00, self.ww01, self.ww10, self.ww11)
        out = np.empty((H, W, 3), dtype=np.float32)
        for y0 in range(0, H, self.BANDE):
            y1 = min(H, y0 + self.BANDE)
            n = y1 - y0
            i00 = self.wi00[y0 * W:y1 * W]
            # np.take plutot que coins[0][i00] : la meme lecture, mais par le
            # chemin specialise de numpy — mesure trois fois plus rapide sur
            # une bande de 1080p. Et chaque terme est multiplie sur place, ce
            # qui epargne un tableau temporaire par coin.
            acc = np.take(coins[0], i00, axis=0).reshape(n, W, 3)
            acc *= poids[0][y0:y1]
            for c, p in zip(coins[1:], poids[1:]):
                tmp = np.take(c, i00, axis=0).reshape(n, W, 3)
                tmp *= p[y0:y1]
                acc += tmp
            out[y0:y1] = acc
        return out

    # -- couches -----------------------------------------------------------

    def _grid(self, beam, t, alpha, collapse):
        if self.grid_pulse > 0.01:
            alpha = alpha * (1.0 + 2.4 * self.grid_pulse
                             * self.hit_env(t, self.grid_on, fall=11.0))
        if alpha <= 0.003:
            return
        for x in np.linspace(-1.6, 1.6, 9):
            P = np.stack([np.full(320, x), np.linspace(-0.92, 0.92, 320)], axis=1)
            px, py = self.to_px(P, collapse)
            beam.add(px, py, 0.075 * alpha)
        for y in np.linspace(-0.9, 0.9, 7):
            P = np.stack([np.linspace(-1.66, 1.66, 380), np.full(380, y)], axis=1)
            px, py = self.to_px(P, collapse)
            beam.add(px, py, 0.080 * alpha)

    def _etincelles(self, beam, t, collapse):
        """Etincelles ejectees par la machine a chaque coup.

        Elles partent du bord du chassis, filent vers l'exterieur et
        s'eteignent. Chaque coup a son jet, tire d'un tirage seme par le numero
        de l'evenement : deux rendus de la meme video donnent donc exactement
        les memes etincelles, ce dont le calcul en parallele a besoin.
        """
        if self.parts <= 0.01:
            return
        vie = max(0.12, float(self.parts_life))
        dt = t - self.ev_t
        m = ((dt >= 0.0) & (dt < vie) & (self.ev_f > 0.16)
             & self._masque(self.parts_on, "caisse claire"))
        idx = np.nonzero(m)[0]
        if len(idx) == 0:
            return
        n = int(np.clip(self.parts_n, 3, 40000))
        jets = list(idx[-6:])                   # au plus six gerbes de front
        # Le faisceau pose un point a la fois : pour qu'une etincelle soit un
        # filet continu et non une file de points, on l'echantillonne a pas
        # constant, comme tout le reste du dessin. Le pas etant fixe en unites
        # du monde, la meme etincelle garde sa densite en 4K comme en 540p.
        PAS = 0.0030
        # Au-dela de quelques centaines d'etincelles, les tracer entieres
        # coute plus que toute la machine. On tient donc un budget de points
        # par image : passe un certain nombre, chaque etincelle est ecourtee
        # plutot que supprimee — et c'est ce qu'on veut, car une gerbe dense
        # se lit comme une poussiere de braises, pas comme des filets.
        BUDGET = 500000
        kmax = max(2, int(BUDGET / max(1, n * len(jets))))
        REF = 14 * 110          # points d'une gerbe au reglage d'origine
        traits, normales = self._pool_traits()
        for i in jets:
            age = float((t - self.ev_t[i]) / vie)
            force = float(self.ev_f[i])
            r = np.random.default_rng(7919 + int(i))
            # vitesses tres etalees : un tirage uniforme donne une coquille
            # reguliere, une puissance donne un panache — beaucoup de braises
            # lentes, quelques-unes qui filent loin.
            v = (0.30 + 1.55 * r.random(n) ** 2) * float(self.parts_speed)
            # Depart sur le dessin lui-meme : chaque braise nait d'un point
            # pris au hasard sur un trait de la machine, et part
            # perpendiculairement a lui — comme une gerbe sur une meule. Un
            # depart sur un contour abstrait donnait une couronne posee autour
            # de la machine, sans rapport avec ce qu'elle dessine.
            sel = r.integers(0, len(traits), n)
            ox, oy = traits[sel, 0], traits[sel, 1]
            nx, ny = normales[sel, 0], normales[sel, 1]
            # la normale pointe d'un cote ou de l'autre du trait ; on
            # privilegie l'exterieur, une braise lancee vers le centre
            # traversant toute la machine et brouillant le dessin
            vers = ox * nx + oy * ny
            flip = (vers < 0) & (r.random(n) < 0.78)
            nx = np.where(flip, -nx, nx)
            ny = np.where(flip, -ny, ny)
            b = np.arctan2(ny, nx) + 0.30 * r.standard_normal(n)
            # course mesuree : assez pour se detacher du chassis, pas assez
            # pour traverser l'ecran et devenir une rayure
            course = 1.15 * v * (0.45 + force)
            d1 = course * max(0.0, age - 0.18) ** 0.75
            d0 = course * age ** 0.75
            k = int(np.clip(float(np.max(d0 - d1)) / PAS, 2, 110))
            k = max(2, min(k, kmax))
            # L'eclat de chaque point baisse quand la gerbe s'epaissit, mais
            # en racine du nombre de points reellement poses : mille braises
            # doivent eclairer plus que dix, sans faire une tache blanche.
            # Au reglage d'origine le facteur vaut exactement 1, donc rien ne
            # change pour qui n'y touche pas.
            eclat = self.parts * math.sqrt(REF / float(max(n * k, 1)))
            s_ = np.linspace(0.0, 1.0, k)[None, :]
            dd = d1[:, None] + (d0 - d1)[:, None] * s_
            px = (ox[:, None] + np.cos(b)[:, None] * dd).ravel()
            # une retombee legere : sans elle, les jets sont trop reguliers
            py = (oy[:, None] + np.sin(b)[:, None] * dd - 0.30 * dd * dd).ravel()
            sx, sy = self.to_px(np.stack([px, py], axis=1), collapse)
            # tete vive, traine qui s'efface : une etincelle d'intensite egale
            # sur toute sa longueur ressemble a un trait tire a la regle
            profil = np.repeat((0.22 + 0.78 * s_ ** 2), n, axis=0).ravel()
            beam.add(sx, sy, profil * 1.5 * eclat * (0.30 + force)
                     * (1.0 - age) ** 2)

    def _spectro(self, beam, t, collapse, sx0, sx1, sy0, sy1):
        """Le spectrogramme du morceau, deroule sur la dalle.

        Une ligne par bande, le temps en abscisse, la force en luminosite :
        les dernieres secondes defilent de droite a gauche. Le faisceau
        acceptant un poids par point, une ligne suffit a porter toute une
        bande — inutile de dessiner des barres.
        """
        if self.spectro <= 0.01 or self.spec is None:
            return
        nb, nt = self.spec.shape
        i1 = min(nt - 1, int(t * self.spec_fps))
        i0 = max(0, i1 - int(3.2 * self.spec_fps))
        if i1 - i0 < 6:
            return
        m = 0.055
        # deux fois plus de points que de colonnes : a une colonne par point le
        # trait se detachait en pointilles sur une dalle un peu large
        n = (i1 - i0) * 2
        x = np.linspace(sx0 + m, sx1 - m, n)
        u = np.linspace(0.0, i1 - i0 - 1.0, n)
        src = np.arange(i1 - i0, dtype=np.float64)
        ybas, yhaut = sy0 + 0.055, sy1 - 0.205
        for b in range(nb):
            yy = ybas + (yhaut - ybas) * (b / max(1, nb - 1))
            w = np.interp(u, src, self.spec[b, i0:i1].astype(np.float64))
            # les creux doivent etre noirs : sans cette courbe, toutes les
            # bandes se valent et le spectrogramme n'est qu'un quadrillage
            w = (w * (1.0 / 255.0)) ** 2.1
            P = np.stack([x, np.full(n, yy)], axis=1)
            px, py = self.to_px(P, collapse)
            beam.add(px, py, w * (1.9 * self.spectro))

    def _pool_traits(self):
        """Les points du dessin de la machine, avec leur normale.

        C'est de la que partent les etincelles. Le trace est echantillonne
        tres finement — pres de soixante mille points — et en garder un sur
        trois suffit largement a tirer des departs au hasard.
        """
        if getattr(self, "_pool", None) is None:
            P = np.vstack([p.P for p in self.mpc])
            N = np.vstack([p.N for p in self.mpc])
            pas = max(1, len(P) // 20000)
            self._pool = (np.ascontiguousarray(P[::pas], dtype=np.float32),
                          np.ascontiguousarray(N[::pas], dtype=np.float32))
        return self._pool

    def _onde(self, beam, t, collapse):
        """Onde de choc : un anneau qui s'ouvre depuis la machine et s'efface."""
        if self.ring <= 0.01:
            return
        vie = 0.62
        dt = t - self.ev_t
        m = ((dt >= 0.0) & (dt < vie) & (self.ev_f > 0.22)
             & self._masque(self.ring_on))
        idx = np.nonzero(m)[0]
        if len(idx) == 0:
            return
        a = np.linspace(0.0, 2.0 * math.pi, 260)
        ca, sa = np.cos(a), np.sin(a)
        for i in idx[-3:]:
            age = float((t - self.ev_t[i]) / vie)
            # l'anneau part du bord du chassis : ne plus bas, il traverserait
            # les pads et passerait pour une rayure sur la machine
            r = 0.92 + 1.45 * age ** 0.62       # vite au depart, puis ralentit
            P = np.stack([r * 1.32 * ca, r * 0.76 * sa], axis=1)
            px, py = self.to_px(P, collapse)
            beam.add(px, py, 1.7 * self.ring * (0.30 + float(self.ev_f[i]))
                     * (1.0 - age) ** 1.9)

    def _hud(self, beam, t, collapse, alpha):
        if alpha <= 0.01:
            return
        blink = 0.45 + 0.55 * (math.sin(t * 9.0) > 0)
        for p in text_paths("OSC", 0.055, -1.66, 0.90, center=False):
            px, py = self.to_px(p.P, collapse)
            beam.add(px, py, 0.55 * alpha)
        for p in text_paths("SYNC", 0.055, 1.34, 0.90, center=False):
            px, py = self.to_px(p.P, collapse)
            beam.add(px, py, 0.55 * alpha * blink)

    def _dyn(self, beam, P, w, collapse, melt, t):
        """Couche animee de la machine. Le poids est compense par le zoom de
        camera : un trait du monde s'etale sur d'autant plus de pixels."""
        w = w * self._cam_z
        if melt >= 0.99:
            return
        if melt > 0:
            P = self._melt(P, melt, t)
            w = w * (1.0 - melt)
        px, py = self.to_px(P, collapse)
        beam.add(px, py, w)

    def _touche(self, beam, r, remplissage, w, collapse, melt, t, contour=None):
        """Une touche enfoncee : son remplissage, et son contour epaissi.

        Le halo seul ne distingue pas une touche de sa voisine — sur trente-
        sept touches serrees, une note allumee se perdait dans la rangee. Il
        faut que le trait lui-meme s'epaississe : d'ou le contour redessine, et
        un second rentre a l'interieur.

        `contour` permet de redessiner la vraie forme de la touche plutot que
        son rectangle : une blanche de clavier est echancree sous les noires,
        et la rallumer en rectangle aurait remis le trait qu'on vient d'oter.
        """
        self._dyn(beam, remplissage, 2.60 * w, collapse, melt, t)
        cont = rrect_pts(*r, r=0.012) if contour is None else contour
        self._dyn(beam, cont, 3.20 * w, collapse, melt, t)
        # le contour repasse une seconde fois, legerement decale : c'est ce qui
        # donne au trait son epaisseur, la ou un simple gain ne fait qu'elargir
        # le halo
        self._dyn(beam, cont + np.array([0.0, 0.004]), 1.90 * w,
                  collapse, melt, t)

    def _melt(self, P, u, t):
        """La machine fond dans la forme d'onde du morceau."""
        k = ease_in_out(u)
        out = P.copy()
        out[:, 1] = P[:, 1] * (1.0 - k) + self.wave_y(P[:, 0], t) * k
        out[:, 0] = P[:, 0] + 0.05 * k * np.sin(P[:, 1] * 9.0 + t * 3.0)
        return out

    def bords_machine(self):
        """Ou s'arrete le chassis de la machine a l'image, en largeur.

        Mis a l'echelle : le fil du morceau tient la largeur de l'ecran, la
        machine peut etre retrecie, et c'est bien le chassis dessine qui doit
        masquer le fil.
        """
        g, d = self.mach.get("bords", (BODY[0], BODY[2]))
        k = float(self.taille)
        return g * k, d * k

    def body_mask(self, x, sweep_x, melt):
        """1 la ou le chassis de la machine masque le fil d'onde.

        Il prenait les bords de la MPC quelle que soit la machine. Le MiniFreak
        etant plus large, le fil lui passait par-dessus le chassis sur un bon
        centimetre ; le Digitakt, plus etroit, se voyait couper le fil bien
        avant son bord.
        """
        g, d = self.bords_machine()
        inside = (smoothstep(g - 0.05, g + 0.03, x)
                  * (1.0 - smoothstep(d - 0.03, d + 0.05, x)))
        return inside * self.morph_at(x, sweep_x) * (1.0 - melt)

    def _wave_line(self, beam, t, collapse, alpha, xf=None, sweep_x=None,
                   melt=0.0, thick=True):
        """Le fil du morceau : il entre par la gauche, disparait derriere la
        machine et ressort a droite — la MPC est un morceau de la bande."""
        if alpha <= 0.01:
            return
        xs = np.linspace(-1.88, 1.88, 2600)
        a = np.full(len(xs), float(alpha))
        if xf is not None:
            a *= smoothstep(xf - 0.16, xf + 0.02, xs)
        if sweep_x is not None:
            a *= 1.0 - self.body_mask(xs, sweep_x, melt)
            a *= self.morph_at(xs, sweep_x)      # nait a mesure que le clip fond
        hit = self.bass_hit(t)
        a = a * self._cam_z
        # La machine peut offrir une ligne d'horizon : le fil s'y cale, et ses
        # deux bouts prolongent alors un trait du dessin au lieu de passer
        # derriere a cote. Sans elle, le fil reste sur l'axe de l'image.
        ligne = float(self.mach.get("ligne", 0.0) or 0.0)
        # En arrivant sur la machine, le fil s'aplatit et se pose exactement
        # sur sa ligne d'horizon : sans cela il la croisait au lieu de la
        # prolonger, et le raccord ne se voyait pas.
        k = 1.0
        if ligne:
            g, d = self.bords_machine()
            loin = np.where(xs < g, g - xs, np.where(xs > d, xs - d, 0.0))
            k = smoothstep(0.0, 0.20, loin)
        P = np.stack([xs, self.wave_y(xs, t) * k + ligne], axis=1)
        px, py = self.to_px(P, collapse)
        beam.add(px, py, 0.85 * a * (1.0 + 0.85 * hit))
        # trainee : le trait d'il y a quelques images, de plus en plus pale.
        # Sa longueur suit le nombre d'instruments qui jouent — un passage
        # charge bave derriere lui, un passage depouille reste net.
        ntr = int(round(self.trail * self.density(t) * 7))
        kp = k                                   # meme aplatissement pour la trainee
        for k in range(1, ntr + 1):
            Q = np.stack([xs, self.wave_y(xs, t - k * 0.034) * kp + ligne],
                         axis=1)
            qx, qy = self.to_px(Q, collapse)
            beam.add(qx, qy, 0.62 * a * (1.0 - k / (ntr + 1.0)) ** 1.7)
        if thick:
            for dy in (0.0035, -0.0035):
                px, py = self.to_px(P + np.array([0.0, dy]), collapse)
                beam.add(px, py, 0.35 * a * (1.0 + 0.7 * hit))
        if hit > 0.15:                            # halo sur les coups graves
            for dy in (0.012, -0.012, 0.022, -0.022):
                px, py = self.to_px(P + np.array([0.0, dy]), collapse)
                beam.add(px, py, 0.30 * a * hit)

    def _daw_clip(self, beam, t, collapse, sweep_x, rng):
        """Ouverture : une piste qui s'enregistre, facon station de travail.

        La tete d'enregistrement remplit la forme d'onde de gauche a droite,
        puis le balayage de l'oscilloscope efface le clip en devoilant la
        machine — la MPC sort litteralement du morceau enregistre.
        """
        tl = self.tl
        b0, b1 = tl.start("boot"), tl.end("boot")
        x0, y0, x1, y1 = CLIP
        rec = np.clip((t - (b0 + 0.16 * (b1 - b0))) / (0.84 * (b1 - b0)), 0.0, 1.0)
        head = x0 + (x1 - x0) * rec
        gone = sweep_x - 0.24 if t >= tl.start("sweep") else -9.0   # cadre efface
        frame_a = (smoothstep(b0, b0 + 0.16 * (b1 - b0), t)
                   * (1.0 - smoothstep(tl.start("sweep"), tl.start("sweep")
                                       + 0.30 * (tl.end("sweep") - tl.start("sweep")), t)))
        if frame_a <= 0.01 and rec >= 1.0 and gone > x1:
            return

        # --- cadre du clip, bandeau de titre, reglure temporelle
        if frame_a > 0.01:
            for pts in (rrect_pts(x0, y0, x1, y1, 0.02),
                        rrect_pts(x0, y1, x1, y1 + CLIP_BAR_H, 0.02)):
                P, _, _ = resample(np.vstack([pts, pts[:1]]))
                m = P[:, 0] > gone
                px, py = self.to_px(P[m], collapse)
                beam.add(px, py, 0.50 * frame_a)
            for q in text_paths("AUDIO 01", 0.075, x0 + 0.05, y1 + 0.025,
                                center=False, tag="clip"):
                m = q.P[:, 0] > gone
                px, py = self.to_px(q.P[m], collapse)
                beam.add(px, py, 0.60 * frame_a)
            for k in range(33):
                gx = x0 + (x1 - x0) * k / 32.0
                if gx <= gone:
                    continue
                h = 0.055 if k % 4 == 0 else 0.028
                P, _, _ = resample([(gx, y1), (gx, y1 - h)])
                px, py = self.to_px(P, collapse)
                beam.add(px, py, (0.55 if k % 4 == 0 else 0.32) * frame_a)
            # temoin d'enregistrement
            if rec < 1.0 and (math.sin(t * 22.0) > -0.2):
                P = np.vstack([circle_pts(x1 - 0.10, y1 + 0.06, r, 60)
                               for r in (0.010, 0.018, 0.028)])
                px, py = self.to_px(P, collapse)
                beam.add(px, py, 1.1 * frame_a)

        # --- ligne de zero + forme d'onde
        xs = self.daw_x
        h = WAVE_YMAX * self.daw
        keep = xs <= head
        if not np.any(keep):
            return
        left = 1.0 - self.morph_at(xs, sweep_x) if t >= tl.start("sweep") else np.ones(len(xs))
        hh = h * left                                          # la matiere passe dans la machine
        keep = keep & (left > 0.01)
        if not np.any(keep):
            return
        P0 = np.stack([xs[keep], np.zeros(int(keep.sum()))], axis=1)
        px, py = self.to_px(P0, collapse)
        beam.add(px, py, 0.42 * left[keep])

        # remplissage en colonnes (comme les traits verticaux d'un editeur)
        K = 220
        u = np.linspace(-1.0, 1.0, K)
        sel = keep & (hh > 0.004)
        if np.any(sel):
            hs = hh[sel]
            X = np.repeat(xs[sel][:, None], K, axis=1).ravel()
            Y = (hs[:, None] * u[None, :]).ravel()
            w = np.repeat(0.62 * (2.0 * hs / K) / STEP * left[sel], K)
            px, py = self.to_px(np.stack([X, Y], axis=1), collapse)
            beam.add(px, py, w)
            # contours haut et bas, plus francs
            for sgn in (1.0, -1.0):
                px, py = self.to_px(np.stack([xs[sel], sgn * hs], axis=1), collapse)
                beam.add(px, py, 0.55 * left[sel])

        # --- tete d'enregistrement
        if 0.0 < rec < 1.0:
            ys = np.linspace(y0 - 0.03, y1 + CLIP_BAR_H, 620)
            for j in range(3):
                P = np.stack([np.full(len(ys), head - j * 0.018), ys], axis=1)
                px, py = self.to_px(P, collapse)
                beam.add(px, py, (1.25 if j == 0 else 0.30) * (0.6 ** j))

    def _machine(self, beam, t, collapse, sweep_x, melt, rng, shake):
        """La machine a l'image : trace revele par le balayage, organes pilotes
        par le son.

        Elle commence par se poser : le sequenceur dit laquelle des trois est
        a l'image a cet instant, et si elle est en train de se deformer vers
        la suivante. Les echos, dessines a des instants anterieurs, montrent
        donc la machine telle qu'elle etait alors.
        """
        self.poser_machine(t)
        tl = self.tl
        live = t >= tl.start("groove") - 0.05
        flashes = self.pad_flashes(t) if live else {}
        e_low = self.env_at(self.e_low, t) if live else 0.0
        e_high = self.env_at(self.e_high, t) if live else 0.0
        e_full = self.env_at(self.e_full, t) if live else 0.0
        step = self.step_index(t) if live else -1
        pulse = 1.0 + 0.28 * e_low
        jx = shake * rng.uniform(-9, 9)
        ghost = 1.0 - smoothstep(tl.start("groove") - 0.25, tl.start("groove"), t)
        trem = (1.0 + 0.6 * e_low + 0.5 * self.bass_hit(t))  # le trait respire
        trem /= 0.45 + 0.55 * self._cam_z                    # sans enfler au zoom

        half = BODY[3]
        for p in self.mpc:
            if p.alpha <= 0.003:
                continue
            mo = self.morph_at(p.P[:, 0], sweep_x)
            if mo.max() <= 0.003:
                continue
            # le trait n'est jamais parfaitement stable : c'est un faisceau,
            # pas un dessin. Il ondule doucement le long de son parcours, un
            # peu plus fort quand le grave pousse.
            if self.wobble > 0.0:
                wob = (0.0021 * np.sin(p.s * 8.5 + t * 2.4 + p.ph)
                       + 0.0013 * np.sin(p.s * 39.0 - t * 6.8 + p.ph * 2.3))
                P = p.P + p.N * (wob * trem * self.wobble)[:, None]
            else:
                P = p.P.copy()
            # au repos, chaque point est ecrase dans l'enveloppe du clip :
            # la machine se deplie hors de la forme d'onde enregistree.
            src = self.clip_env(P[:, 0]) * (P[:, 1] / half)
            P[:, 1] = src * (1.0 - mo) + p.P[:, 1] * mo
            w = 0.50 * pulse * mo + 0.055 * ghost * (1.0 - mo)   # liseré d'annonce
            w += 1.2 * np.exp(-((mo - 0.55) / 0.30) ** 2)            # front de mue
            tag = p.tag
            boost = 0.0
            if tag.startswith("pad"):
                k = int(tag[3:])
                if k in flashes:
                    boost = 1.7 * flashes[k]
            elif tag.startswith("step"):
                k = int(tag[4:])
                if k == step:
                    boost = 1.9
                elif k in STEP_LIT:
                    boost = 0.30
            elif tag == "strip":
                boost = 0.8 * e_high
            elif tag.startswith("qlink"):
                boost = 0.45 * e_low
            elif tag == "wheel":
                boost = 0.30 * e_full
            w = w + boost * mo
            if melt > 0:
                w *= (1.0 - melt) ** 0.7
                P = self._melt(P, melt, t)
            px, py = self.to_px(P, collapse, (jx, 0.0))
            beam.add(px, py, w * (self._cam_z * p.alpha))

        # ---- organes animes
        if melt >= 0.99:
            return

        mach = self.mach
        # Sur une machine melodique — celle qui declare des touches — elles
        # appartiennent a la melodie des qu'il y en a une : seize coups de
        # batterie repartis sur trente-sept touches allumaient la moitie du
        # clavier, et la note jouee se perdait au milieu.
        melodique = mach.get("touches") is not None
        if melodique and self.midi is not None:
            flashes = {}

        # pads allumes : remplissage
        for k, v in flashes.items():
            if k >= len(mach["pads"]):
                continue
            mk = float(self.morph_at(mach["pads"][k][0], sweep_x))
            if mk > 0.4 and v > 0.05:
                if melodique:
                    # Sur un clavier, un coup se lit comme une touche
                    # enfoncee, faute de melodie pour le faire. Sans cela le
                    # clavier restait immobile tout le morceau : c'est la
                    # rangee de pas, retiree parce qu'aucun clavier n'en a,
                    # qui lui donnait jusqu'ici son mouvement.
                    #
                    # Les coups sont pour la plupart faibles — la moitie sous
                    # un dixieme, mesure sur le morceau d'essai. Une grille de
                    # pads les rend quand meme, parce que seize pads voisins
                    # s'allument ensemble et que leurs halos s'ajoutent ; une
                    # touche de clavier est seule. On redresse donc la courbe :
                    # un coup ordinaire passe de 0,08 a 0,31, et le plus fort
                    # reste sous la saturation.
                    cont = mach.get("pads_contour")
                    self._touche(beam, mach["pads"][k], mach["remplir"](k),
                                 1.05 * (v ** 0.45) * mk, collapse, melt, t,
                                 contour=cont[k] if cont else None)
                else:
                    self._dyn(beam, mach["remplir"](k), 1.05 * v * mk,
                              collapse, melt, t)

        # ---- les notes du fichier MIDI : la touche jouee s'allume, contour
        # compris. On la redessine plutot que de lui donner une etiquette a
        # elle : sur le clavier les touches sont deja groupees par seize pour
        # les coups de batterie, et il ne faut pas defaire ce groupement.
        #
        # Pendant un passage il n'y a pas de touches : les deux machines n'en
        # ont pas le meme nombre, et une note tomberait n'importe ou sur une
        # facade en train de se deformer.
        touches = mach.get("touches")
        if melodique and self.midi is not None:
            for k, v in self.notes_midi(t, touches, mach.get("note0", 36)).items():
                r = touches[k]
                mk = float(self.morph_at(r[0], sweep_x))
                if mk <= 0.4:
                    continue
                # le nombre de lignes suit la hauteur de la touche : cinq sur
                # un pad de MPC, douze sur une blanche de clavier, qui est
                # trois fois plus haute. Un nombre fixe donnait soit un pad
                # sature, soit une touche a peine teintee.
                # Le remplissage de la machine quand elle en fournit un : il
                # suit la vraie forme de la touche, echancrure comprise. Sans
                # lui, le haut d'une blanche restait eteint et la note n'avait
                # l'air qu'a moitie enfoncee.
                remplis, cont = mach.get("remplis"), mach.get("contours")
                if remplis is not None:
                    fond = remplis[k]
                else:
                    lignes = int(min(12, max(4, round((r[3] - r[1]) / 0.038))))
                    fond = _remplir(r, lignes, 0.010)
                self._touche(beam, r, fond, v * mk, collapse, melt, t,
                             contour=cont[k] if cont else None)

        # bande de pas : le pas courant s'allume
        if live and 0 <= step < len(mach["pas"]):
            x0, y0, x1, y1 = mach["pas"][step]
            mk = float(self.morph_at(x0, sweep_x))
            if mk > 0.4:
                self._dyn(beam, rect_fill(x0, y0, x1, y1, 4), 0.95 * mk, collapse, melt, t)

        # potards : index qui tourne
        for k, (cx, cy, kr) in enumerate(mach["potards"]):
            if self.morph_at(cx, sweep_x) < 0.5:
                continue
            v = np.clip(0.18 + 0.62 * (e_low if k % 2 == 0 else e_high)
                        + 0.20 * math.sin(t * 1.7 + k), 0.0, 1.0)
            a = math.radians(225.0 - 270.0 * v)
            P, _, _ = resample([(cx + kr * 0.36 * math.cos(a),
                                 cy + kr * 0.36 * math.sin(a)),
                                (cx + kr * 0.86 * math.cos(a),
                                 cy + kr * 0.86 * math.sin(a))])
            self._dyn(beam, P, 1.15, collapse, melt, t)

        # bande tactile : curseur lumineux
        bande = mach["bande"]
        if bande is not None and self.morph_at(bande[0], sweep_x) > 0.5:
            large = (bande[2] - bande[0]) > (bande[3] - bande[1])
            if large:
                # bande couchee : le curseur va de gauche a droite
                sx = bande[0] + (bande[2] - bande[0]) * np.clip(0.12 + 0.8 * e_high, 0, 1)
                r = (sx - 0.026, bande[1] + 0.010, sx + 0.026, bande[3] - 0.010)
            else:
                sy = bande[1] + (bande[3] - bande[1]) * np.clip(0.12 + 0.8 * e_high, 0, 1)
                r = (bande[0] + 0.014, sy - 0.026, bande[2] - 0.014, sy + 0.026)
            self._dyn(beam, rect_fill(*r, nlines=4), 0.85, collapse, melt, t)

        # ecran : forme d'onde du morceau + niveaux
        sx0, sy0, sx1, sy1 = self.ecran
        if self.morph_at(sx0, sweep_x) > 0.5 and t < tl.start("zoom"):
            m = 0.05
            x_hi = sx1 - m if self.morph_at(sx1, sweep_x) > 0.5 else min(sx1 - m, sweep_x)
            x_lo = sx0 + m
            if x_hi - x_lo > 0.05:
                xs = np.linspace(x_lo, x_hi, 420)
                u = (xs - (sx0 + m)) / ((sx1 - m) - (sx0 + m)) * 2.0 - 1.0
                yc = (sy0 + sy1) * 0.5 - 0.03
                ys = yc + 0.125 * self.wave_y(u * 1.88, t, amp=1.0)
                self._dyn(beam, np.stack([xs, ys], axis=1), 1.0, collapse, melt, t)
                self._spectro(beam, t, collapse, sx0, sx1, sy0, sy1)
                # bandeau du haut : nom du morceau, comme ecrit sur la dalle
                if self.screen_title:
                    # le trace est garde d'une image a l'autre, mais il doit
                    # suivre le titre : dans le studio on peut le changer sans
                    # que le moteur, lui, soit reconstruit.
                    if getattr(self, "_ttl_de", None) != self.screen_title:
                        self._ttl = text_paths(
                            fit_text(self.screen_title, 0.050,
                                     (sx1 - 0.052) - (sx0 + 0.052), tracking=0.42),
                            0.050, sx0 + 0.052, sy1 - 0.099,
                            center=False, tag="scr", tracking=0.42)
                        self._ttl_de = self.screen_title
                    for q in self._ttl:
                        self._dyn(beam, q.P, 0.80, collapse, melt, t)
                # barre de progression, sous le bandeau. Tout passe par
                # resample() : rrect_pts et rect_fill rendent des polygones
                # grossiers, qui donneraient un trait pointille sur une barre
                # aussi large que la dalle.
                py0, ph = sy1 - 0.150, 0.017
                rx0, rx1 = sx0 + 0.052, sx1 - 0.052
                rail, _, _ = resample(np.vstack([rrect_pts(rx0, py0, rx1, py0 + ph,
                                                           r=ph * 0.5),
                                                 [[rx1 - ph * 0.5, py0]]]))
                self._dyn(beam, rail, 0.32, collapse, melt, t)
                u = float(np.clip(self.progress_at(t), 0.0, 1.0))
                fx1 = rx0 + 0.004 + (rx1 - rx0 - 0.008) * u
                if fx1 - (rx0 + 0.004) > 0.004:
                    for j in range(4):
                        y = py0 + 0.004 + (ph - 0.008) * j / 3.0
                        seg, _, _ = resample([(rx0 + 0.004, y), (fx1, y)])
                        self._dyn(beam, seg, 1.00, collapse, melt, t)

                base = sy0 + 0.055
                for k in range(8):
                    v = (e_low, e_full, e_high)[k % 3] * (0.5 + 0.5 * math.sin(k * 1.7 + t * 5.0))
                    v = max(0.06, v)
                    bx = sx0 + m + (k + 0.5) * ((sx1 - m - sx0 - m) / 8.0)
                    if bx > x_hi:
                        continue
                    P, _, _ = resample([(bx, base), (bx, base + 0.16 * v)])
                    self._dyn(beam, P, 0.8, collapse, melt, t)

    def title_front(self, t):
        """Position du front qui balaie le mot, de gauche a droite.

        Il ralentit sur la largeur du mot : les lettres se detachent alors une
        par une, au rythme des doubles-croches.
        """
        u = np.clip(self.tl.at("title", t), 0.0, 1.0)
        return float(np.interp(u, (0.0, 0.14, 0.86, 1.0), (-1.42, -1.14, 1.14, 1.42)))

    def _draw_title(self, beam, t, collapse, u_out, dx=0.0):
        """Le mot nait de la frequence, sur l'ecran de la machine : le front
        passe, l'onde s'efface derriere lui et chaque lettre s'en detache."""
        xf = self.title_front(t)
        wy = self.wave_y(self.tP[:, 0], t)
        k = np.clip((xf - self.tP[:, 0] + 0.055) / 0.185, 0.0, 1.0)
        k = k * k * (3.0 - 2.0 * k)

        P = self.tP.copy()
        P[:, 1] = wy * (1.0 - k) + (self.tP[:, 1] + self.wave_mod(P[:, 0]) * wy) * k
        P[:, 0] = P[:, 0] + dx

        # le fil est deja en place avant le passage du front : seuls les traits
        # de lettres montent en intensite au fur et a mesure.
        w = self.tw * np.where(self.is_line > 0, 0.10 + 0.90 * k, 1.0)
        w = w + 2.4 * np.exp(-((k - 0.62) / 0.26) ** 2) * self.is_line * (xf < 1.42)
        if xf >= 1.42:
            w = self.tw * (1.0 + 0.10 * self.env_at(self.e_low, t)
                           + 0.45 * self.bass_hit(t))
        if u_out > 0:
            w = w * max(0.0, 1.0 - u_out * 1.35)

        th = self.tth * np.where(self.is_line > 0, k, 1.0)
        for off, ow in ((0.0, 1.0), (1.0, 0.60), (-1.0, 0.60)):
            Q, m = self.in_screen(P + self.tN * (off * th)[:, None])
            if not np.any(m):
                continue
            px, py = self.to_px(Q[m], collapse)
            beam.add(px, py, (w * ow)[m])

        # le front de lecture, borne a la hauteur de la dalle
        if -1.41 < xf < 1.41:
            ys = np.linspace(-SCR_HH * 0.94, SCR_HH * 0.94, 620)
            taper = np.exp(-(ys / (SCR_HH * 0.66)) ** 4)
            for j in range(4):
                Q, m = self.in_screen(np.stack([np.full(len(ys), xf - j * 0.022), ys], axis=1))
                if not np.any(m):
                    continue
                px, py = self.to_px(Q[m], collapse)
                beam.add(px, py, (taper * (0.95 if j == 0 else 0.26) * (0.58 ** j))[m])
            dot = np.stack([np.full(60, xf), np.linspace(-0.02, 0.02, 60)
                            + float(self.wave_y(np.array([xf]), t)[0])], axis=1)
            Q, m = self.in_screen(dot)
            if np.any(m):
                px, py = self.to_px(Q[m], collapse)
                beam.add(px, py, 2.2)

    def _draw_sub(self, beam, t, collapse, u_out, dx=0.0):
        """HARDWARE ONLY : volet lumineux qui passe juste apres le mot."""
        tl = self.tl
        span = tl.end("title") - tl.start("title")
        t0 = tl.start("title") + 0.74 * span
        u = float(np.clip((t - t0) / (0.36 * span), 0.0, 1.0))
        if u <= 0.0:
            return
        xw = -0.60 + 1.28 * ease_out(u, 2.2)
        k = np.clip((xw - self.subP[:, 0] + 0.03) / 0.10, 0.0, 1.0)
        k = k * k * (3.0 - 2.0 * k)
        P = self.subP.copy()
        P[:, 1] = P[:, 1] + 0.030 * self.wave_y(P[:, 0], t)
        P[:, 0] = P[:, 0] + dx
        w = 0.40 * k + 0.55 * np.exp(-((k - 0.60) / 0.30) ** 2) * (u < 1.0)  # plus fonce
        if u_out > 0:
            w = w * max(0.0, 1.0 - u_out * 1.35)
        th = (0.0032 * k)[:, None]
        for off, ow in ((0.0, 1.0), (1.0, 0.55), (-1.0, 0.55)):
            Q, m = self.in_screen(P + self.subN * (off * th))
            if not np.any(m):
                continue
            px, py = self.to_px(Q[m], collapse)
            beam.add(px, py, (w * ow)[m])

    # -- image -------------------------------------------------------------

    def intensity(self, t):
        tl = self.tl
        beam = Beam(self.H, self.W, self.gain)
        rng = np.random.default_rng(self.seed + int(t * self.fps + 0.5))

        # l'image respire sur chaque grosse caisse pendant que la machine joue
        self._zoom = 1.0 + 0.020 * self.kick_hit(t)
        # puis la camera entre dans l'ecran de la machine
        kz = ease_in_out(float(np.clip(tl.at("zoom", t), 0.0, 1.0)))
        self._cam = (SCR_C[0] * kz, SCR_C[1] * kz)
        self._cam_z = 1.0 + (CAM_Z - 1.0) * kz

        collapse = 1.0
        u_out = tl.at("out", t)
        if u_out > 0.30:
            collapse = max(0.028,
                           (1.0 - ease_in_out(min(1.0, (u_out - 0.30) / 0.42))) ** 1.3)

        shake = self.glitch_at(t)

        grid_a = smoothstep(0.05, 0.55, t) * (1.0 - kz)      # le reticule reste dehors
        self._grid(beam, t, grid_a * (1.0 if u_out <= 0 else max(0.0, 1 - u_out * 2)), collapse)
        self._hud(beam, t, collapse,
                  smoothstep(0.15, 0.6, t) * (1.0 - smoothstep(tl.start("zoom"),
                                                               tl.end("zoom"), t)))

        # ---- 1 et 2. le clip qui s'enregistre, puis le balayage
        u_sweep = tl.at("sweep", t)
        us = np.clip(u_sweep, 0, 1)
        sweep_x = -1.85 + 4.55 * (0.55 * us + 0.45 * ease_in_out(us)) if u_sweep > 0 else -1.85
        if t < tl.end("sweep"):
            self._daw_clip(beam, t, collapse, sweep_x, rng)

        # ---- 3. la machine
        melt = 0.0                       # la machine ne se dissout plus : on y entre
        if u_sweep > 0:
            self._machine(beam, t, collapse, sweep_x, melt, rng, shake)

        # tete de balayage
        if 0.0 < u_sweep < 1.02:
            n = 900
            ys = np.linspace(-0.95, 0.95, n)
            taper = np.exp(-(ys / 0.72) ** 4)      # bords fondus : une tete de
            for k in range(4):                     # lecture, pas un volet net
                jit = 0.004 * np.sin(ys * 60 + t * 40) if k == 0 else 0.0
                P = np.stack([np.full(n, sweep_x - k * 0.030) + jit, ys], axis=1)
                px, py = self.to_px(P, collapse)
                beam.add(px, py, taper * (0.85 if k == 0 else 0.28) * (0.62 ** k))

        # ---- 4. la forme d'onde du morceau
        a_wave = 0.0
        if t >= tl.start("groove"):
            a_wave = 0.48 * smoothstep(tl.start("groove"), tl.start("groove") + 0.4, t)
        if t >= tl.start("zoom"):
            a_wave *= 1.0 - smoothstep(tl.start("zoom"), tl.start("zoom") + 0.45, t)
        if u_out > 0:
            a_wave *= max(0.0, 1.0 - u_out * 1.6)
        self._wave_line(beam, t, collapse, a_wave, None, sweep_x, melt)

        # ---- 5. le titre, ecrit par la courbe
        if t >= tl.start("zoom") and u_out < 0.95:
            dx = shake * float(rng.uniform(-0.055, 0.055)) if u_out > 0 else 0.0
            self._draw_title(beam, t, collapse, max(0.0, u_out), dx)
            self._draw_sub(beam, t, collapse, max(0.0, u_out), dx)

        return beam.render(), collapse, shake, rng

    wobble = 0.0      # ondulation du trace de la machine (0 = trait net)
    split = 1.0       # dedoublement chromatique du trait sur les gros subs
    split_px = 11.0   # ecart des copies, en pixels ramenes a 540p
    split_count = 3   # combien de fois il se declenche dans toute la video
    split_on = "grosse caisse"   # sur quels coups il a le droit de partir
    wave_win = CURVE_WIN   # base de temps libre (s), quand wave_trig vaut 0
    wave_trig = 0.0        # balayage declenche : largeur d'ecran, en temps
    wave_passes = 1        # passages de lissage : 3 coupe franchement l'aigu
    wave_punch = 0.85      # gonflement de la courbe sur les temps forts
    snare = 1.0       # embrasement jaune sur la caisse claire
    wave_gain = 1.0   # amplitude de la courbe sonore
    trail = 1.0       # trainee de la bande, d'autant plus longue qu'il y a
                      # d'instruments qui jouent
    screen_title = ""  # nom du morceau, affiche dans le bandeau de la dalle
    # Le rendu « performance » (tools/mpc_performance.py) cale le sequenceur
    # sur le morceau et declenche les glitchs sur ses paroxysmes. Ce sont de
    # simples valeurs, pas des fonctions greffees : le moteur reste ainsi
    # transmissible tel quel aux processus de rendu, y compris sous Windows
    # ou ceux-ci ne sont pas des copies du processus principal.
    # ---- reactions au son : chacune se cale sur la famille d'instruments de
    # son choix (voir FAMILLES). Zero partout = la machine seule, comme avant.
    punch = 0.032        # zoom d'impact : l'image respire sur le coup
    punch_on = "grosse caisse"
    shake_amp = 0.0      # secousse : l'image est bousculee sur le coup
    shake_on = "grosse caisse"
    parts = 0.0          # eclat des etincelles ejectees par la machine
    parts_on = "caisse claire"
    parts_n = 14         # combien par coup : de la gerbe au nuage de braises
    parts_speed = 1.0
    parts_life = 0.55
    ring = 0.0           # onde de choc : un anneau qui s'ouvre
    ring_on = "grosse caisse"
    grid_pulse = 0.0     # la grille du fond s'allume sur le coup
    grid_on = "grosse caisse"
    bg_flash = 0.0       # le fond est eclaire par le coup
    flash_on = "caisse claire"
    travel = 0.0         # travelling sur le fond (part de l'image parcourue)
    travel_mode = "avant"
    # ---- reactions franchement glitchy, sur le coup plutot que sur un
    # paroxysme : la difference avec `glitch` est la, il se declenche sur ce
    # qu'on joue et non sur les montees du morceau.
    tranches = 0.0       # bandes horizontales decalees
    tranches_on = "caisse claire"
    roll = 0.0           # decrochage vertical, comme un tube desynchronise
    roll_on = "grosse caisse"
    ghost = 0.0          # image fantome, decalee et attardee
    ghost_on = "caisse claire"
    blocs = 0.0          # blocs recopies ailleurs, facon flux abime
    blocs_on = "caisse claire"
    invert = 0.0         # negatif bref
    invert_on = "grosse caisse"
    stut = 0.0           # begaiement : duree totale, en secondes
    stut_on = "charley"
    stut_loop = 0.05     # longueur du bout d'image rejoue en boucle
    scramble = 0.0       # tranches de temps rejouees dans le desordre
    scr_len = 0.14       # longueur d'une tranche, en secondes
    miroir = 0.0         # l'image se replie sur elle-meme
    miroir_on = "caisse claire"
    ondul = 0.0          # ondulation liquide du balayage
    ondul_on = "basse"
    mosaic = 0.0         # pixelisation brutale
    mosaic_on = "caisse claire"
    # ---- breakcore
    kaleido = 0.0        # l'image repetee en grille
    kaleido_on = "caisse claire"
    cisaille = 0.0       # cisaillement diagonal
    cisaille_on = "caisse claire"
    coupure = 0.0        # l'image disparait, une image ou deux
    coupure_on = "grosse caisse"
    tapestop = 0.0       # le temps ralentit puis rattrape, comme une bande
    tapestop_on = "grosse caisse"
    # ---- trip hop, lo-fi : des textures continues, pas des impacts
    cadence = 0          # images tenues (2 = 15 i/s, 3 = 10 i/s)
    poussiere = 0.0      # poussiere et rayures de pellicule
    flottement = 0.0     # la bande flotte : lent va-et-vient de l'image
    halo_doux = 0.0      # halo laiteux et noirs releves
    echo = 0.0           # images fantomes du passe, en tramee derriere
    echo_n = 3           # combien d'echos
    echo_delay = 0.045   # ecart entre deux echos, en secondes
    couleurs = 0.0       # le trait prend la teinte de l'instrument frappe
    spectro = 0.0        # spectrogramme deroulant sur la dalle
    spec = None          # le spectrogramme lui-meme (bandes x temps, en octets)
    spec_fps = 60.0

    step_div = 2.0      # pas du sequenceur : 4 = double-croche, 2 = croche
    step_phase = None   # instant du premier pas du sequenceur (None = intro)
    drops = None        # instants des paroxysmes du morceau (None = intro)
    drop_dur = 0.22     # duree d'une rafale de glitch, en secondes
    glitch = 1.0        # dosage des glitchs sur les paroxysmes (0 = aucun)

    def set_look(self, palette="vert", bg=None, bg_color=None,
                 bg_strength=1.0, bg_clear=0.55, bg_anim=0.0):
        """Change la couleur et le fond sans rien recalculer d'autre.

        Rien de tout cela ne depend du son : on peut donc changer d'allure
        sur un moteur deja construit, ce dont le studio se sert pour
        reafficher une image instantanement quand on bouge un curseur.

        `palette` : un nom du catalogue, ou directement les quatre couleurs
        (coeur, halo, coeur sur-expose, fond) pour une teinte sur mesure.
        """
        fluo, halo, hotc, pbg = (PALETTES[palette] if isinstance(palette, str)
                                 else tuple(palette))
        self.c_fluo, self.c_halo = tuple(fluo), tuple(halo)
        self.c_hot = np.float32(hotc)
        # le fond de la palette reste la valeur par defaut ; `bg` le remplace
        self.backdrop = None      # image de fond, ajoutee sous la texture
        # On retient de quoi refaire cette texture : son creux derriere la
        # machine depend de la taille de celle-ci, qui se regle apres coup.
        self._look = (palette, bg, bg_color, bg_strength, bg_clear, bg_anim)
        self.bg_kind = bg or "uni"
        self.bg_anim = float(bg_anim)
        creux = (creux_machine(self.W, self.H, bg_clear, self.scale * self.taille)
                 if bg else None)
        # La texture est construite sans son creux : c'est elle qui defile, et
        # le creux, lui, doit rester derriere la machine. Tant que rien ne
        # bouge on les multiplie une fois pour toutes — garder les deux plans
        # separes couterait le double de memoire a chaque tache de rendu.
        pat = make_background(
            self.W, self.H, kind=self.bg_kind,
            color=pbg if bg_color is None else bg_color,
            strength=bg_strength, clear=0.0,
            scale=self.scale * self.taille, seed=self.seed)
        # La vitesse se compte en motifs par seconde et non en pixels : sinon
        # le meme reglage ferait deriver doucement un quadrillage a grosses
        # mailles et strober des lignes de tube cent fois plus fines.
        maille = max(6.0, self.H / 25.0)
        self.bg_periode = maille * (0.25 if self.bg_kind == "scan" else 1.0)
        anime = (self.bg_anim > 1e-4 and pat.shape[0] > 1
                 and self.bg_kind not in ("uni", "degrade"))
        if anime:
            self.c_bg, self.c_bg_pat, self.c_bg_creux = None, pat, creux
        else:
            self.c_bg = pat if creux is None else pat * creux[..., None]
            self.c_bg_pat = self.c_bg_creux = None

    def fond_texture(self, t):
        """La texture du fond a l'instant t.

        Les lignes de tube et le quadrillage descendent ; le grain, lui, saute
        d'un point a l'autre de sa propre matiere, ce qui le fait bouillir
        comme un grain de pellicule. Le saut est tire d'un hasard seme par le
        numero de l'image, et non du tirage partage : piocher dedans
        deplacerait tout le reste de l'image — le grain, les tranches de
        glitch — des qu'on allume l'animation.
        """
        if self.c_bg is not None:
            return self.c_bg
        pat = self.c_bg_pat
        h, w = pat.shape[0], pat.shape[1]
        if self.bg_kind == "bruit":
            # le grain saute huit fois par seconde et par cran de vitesse :
            # entre deux sauts il reste fixe, comme un grain de pellicule qui
            # tient le temps d'une photogramme
            saut = int(t * self.bg_anim * 8.0)
            r = np.random.default_rng(self.seed + 7717 + saut)
            pat = np.roll(pat, (int(r.integers(0, h)), int(r.integers(0, w))),
                          axis=(0, 1))
        else:
            pat = np.roll(pat, int(t * self.bg_anim * self.bg_periode) % h,
                          axis=0)
        return pat if self.c_bg_creux is None else pat * self.c_bg_creux[..., None]

    def set_taille(self, taille):
        """Change la taille de la machine, creux du fond compris.

        La texture de la dalle est creusee derriere la machine : si le creux
        gardait l'ancienne taille, une machine retrecie flotterait au milieu
        d'un halo sombre plus grand qu'elle.
        """
        taille = float(taille)
        if abs(taille - self.taille) < 1e-6:
            return
        self.taille = taille
        if getattr(self, "_look", None):
            self.set_look(*self._look)

    @staticmethod
    def _shift(a, dx, dy):
        """Decale un plan de (dx, dy) pixels, en noircissant ce qui entre.

        Un np.roll ferait reapparaitre de l'autre cote ce qui sort du cadre :
        sur un gros decalage, le trait se retrouverait recopie au bord oppose.
        """
        out = np.zeros_like(a)
        h, w = a.shape
        x0s, x1s = max(0, -dx), min(w, w - dx)
        x0d, x1d = max(0, dx), min(w, w + dx)
        y0s, y1s = max(0, -dy), min(h, h - dy)
        y0d, y1d = max(0, dy), min(h, h + dy)
        if x1s > x0s and y1s > y0s:
            out[y0d:y1d, x0d:x1d] = a[y0s:y1s, x0s:x1s]
        return out

    def _reactions_glitch(self, img, t, rng):
        """Les avaries d'image declenchees par la batterie.

        Elles s'appliquent a l'image finie, juste avant la deformation du
        tube — c'est la que se logent deja les glitchs de paroxysme, et c'est
        ce qui leur donne cet air de panne de signal plutot que d'effet
        dessine. Le tirage est celui de l'image, seme par son numero : deux
        rendus de la meme video donnent les memes avaries.
        """
        H, W = self.H, self.W

        # ---- bandes horizontales arrachees
        a = self.tranches * self.hit_env(t, self.tranches_on, fall=16.0,
                                 plancher=PLANCHER_AVARIE)
        if a > 0.02:
            for _ in range(int(2 + 14 * a)):
                y0 = int(rng.integers(0, max(1, H - 4)))
                y1 = min(H, y0 + int(rng.integers(3, max(6, int(H * 0.09 * a) + 5))))
                off = int(rng.integers(-int(W * 0.09 * a) - 2, int(W * 0.09 * a) + 3))
                img[y0:y1] = np.roll(img[y0:y1], off, axis=1)

        # ---- blocs recopies d'ailleurs, comme un flux video abime
        a = self.blocs * self.hit_env(t, self.blocs_on, fall=15.0,
                              plancher=PLANCHER_AVARIE)
        if a > 0.02:
            cote = max(8, int(H * 0.055))
            for _ in range(int(2 + 16 * a)):
                bh = int(rng.integers(cote // 2, cote * 2))
                bw = int(rng.integers(cote, cote * 3))
                y0 = int(rng.integers(0, max(1, H - bh)))
                x0 = int(rng.integers(0, max(1, W - bw)))
                ys = int(rng.integers(0, max(1, H - bh)))
                xs = int(rng.integers(0, max(1, W - bw)))
                img[y0:y0 + bh, x0:x0 + bw] = img[ys:ys + bh, xs:xs + bw]

        # ---- decrochage vertical : le tube perd sa synchro
        a = self.roll * self.hit_env(t, self.roll_on, fall=14.0,
                             plancher=PLANCHER_AVARIE)
        if a > 0.02:
            k = int(round(H * 0.16 * a * float(rng.uniform(0.5, 1.0))))
            if k:
                img[:] = np.roll(img, k, axis=0)
                # la couture laisse une barre claire, comme sur un vrai tube
                b = max(1, int(H * 0.004))
                img[k:k + b] += 0.22 * a

        # ---- image fantome : une copie decalee et attardee
        a = self.ghost * self.hit_env(t, self.ghost_on, fall=10.0,
                              plancher=PLANCHER_AVARIE)
        if a > 0.02:
            dx = int(round(W * 0.035 * a))
            dy = int(round(H * 0.012 * a))
            if dx or dy:
                fant = np.zeros_like(img)
                x0s, x1s = max(0, -dx), min(W, W - dx)
                y0s, y1s = max(0, -dy), min(H, H - dy)
                fant[max(0, dy):min(H, H + dy), max(0, dx):min(W, W + dx)] = \
                    img[y0s:y1s, x0s:x1s]
                img += fant * (0.55 * a)

        # ---- miroir : l'image se replie sur elle-meme
        a = self.miroir * self.hit_env(t, self.miroir_on, fall=15.0,
                                       plancher=PLANCHER_AVARIE)
        if a > 0.30:
            # tout ou rien : un miroir a moitie applique n'existe pas
            if int(rng.integers(0, 4)) == 0:
                d = H // 2                      # une fois sur quatre, en haut
                img[H - d:] = img[:d][::-1]
            else:
                d = W // 2
                img[:, W - d:] = img[:, :d][:, ::-1]

        # ---- ondulation liquide du balayage
        a = self.ondul * self.hit_env(t, self.ondul_on, fall=9.0,
                                      plancher=PLANCHER_AVARIE)
        if a > 0.02:
            amp = W * 0.055 * a
            y = np.arange(H, dtype=np.float32)
            dx = (amp * np.sin(y * (2.0 * math.pi / max(8.0, H * 0.17))
                               + t * 26.0)).astype(np.int32)
            # Les lignes se regroupent par decalage : quelques dizaines de
            # valeurs distinctes seulement, donc quelques dizaines de np.roll
            # au lieu d'un par ligne — et aucun grand tableau d'indices.
            for d in np.unique(dx):
                if d:
                    sel = np.nonzero(dx == d)[0]
                    img[sel] = np.roll(img[sel], int(d), axis=1)

        # ---- mosaique : l'image tombe en gros pixels
        a = self.mosaic * self.hit_env(t, self.mosaic_on, fall=16.0,
                                       plancher=PLANCHER_AVARIE)
        if a > 0.02:
            k = int(2 + 46 * a * (H / 1080.0))
            h2, w2 = H // k, W // k
            if h2 > 0 and w2 > 0:
                vue = img[:h2 * k, :w2 * k].reshape(h2, k, w2, k, 3)
                # la moyenne du bloc est reecrite par diffusion : pas de copie
                # de l'image, seul le petit tableau des blocs est alloue
                vue[...] = vue.mean(axis=(1, 3))[:, None, :, None, :]

        # ---- kaleidoscope : l'image repetee en grille
        a = self.kaleido * self.hit_env(t, self.kaleido_on, fall=13.0,
                                        plancher=PLANCHER_AVARIE)
        if a > 0.30:
            nb = 3 if a > 0.85 else 2
            h2, w2 = H // nb, W // nb
            if h2 > 1 and w2 > 1:
                # copie du sous-echantillonnage : on ne peut pas lire l'image
                # et y ecrire en meme temps sans la brouiller
                petit = np.ascontiguousarray(img[:h2 * nb:nb, :w2 * nb:nb])
                for i in range(nb):
                    for j in range(nb):
                        bloc = img[i * h2:(i + 1) * h2, j * w2:(j + 1) * w2]
                        src = petit[:bloc.shape[0], :bloc.shape[1]]
                        # un carreau sur deux est retourne : c'est ce qui fait
                        # le kaleidoscope plutot qu'une simple mosaique
                        bloc[...] = src[::-1, ::-1] if (i + j) % 2 else src

        # ---- cisaillement : l'image penche d'un bloc
        a = self.cisaille * self.hit_env(t, self.cisaille_on, fall=14.0,
                                         plancher=PLANCHER_AVARIE)
        if a > 0.02:
            pente = W * 0.16 * a * (1.0 if int(rng.integers(0, 2)) else -1.0)
            dx = (np.linspace(-0.5, 0.5, H) * pente).astype(np.int32)
            for d in np.unique(dx):
                if d:
                    sel = np.nonzero(dx == d)[0]
                    img[sel] = np.roll(img[sel], int(d), axis=1)

        # ---- coupure franche : l'image s'absente
        # retombee calee sur la cadence : a 1, la coupure dure deux images
        a = self.coupure * self.hit_env(t, self.coupure_on, fall=11.0,
                                        plancher=PLANCHER_AVARIE)
        if a > 0.30:
            img *= max(0.0, 1.0 - 1.35 * a)

        # ---- negatif du trait (solarisation)
        a = self.invert * self.hit_env(t, self.invert_on, fall=18.0,
                                       plancher=PLANCHER_AVARIE)
        if a > 0.02:
            # Un negatif franc — 1 moins l'image — passe par un gris uniforme
            # a mi-chemin : au lieu d'un eclair on obtient un voile, et le
            # fond noir devient blanc. On replie donc seulement ce qui est
            # au-dessus d'un seuil : le coeur du trait vire au sombre en
            # gardant ses bords lumineux, et le fond reste noir. Par bandes,
            # pour ne pas dupliquer l'image entiere.
            seuil = np.float32(1.0 - 0.72 * min(1.0, a))
            for y0 in range(0, self.H, self.BANDE):
                b = img[y0:y0 + self.BANDE]
                np.minimum(b, 2.0 * seuil - b, out=b)
        self._texture_lofi(img, t, rng)
        return img

    def _texture_lofi(self, img, t, rng):
        """Les textures continues : celles qui ne frappent pas, mais vieillissent.

        Contrairement aux avaries, elles ne se declenchent sur aucun coup —
        elles sont la du debut a la fin. C'est ce qui fait la difference entre
        un accident et une matiere : un grain de pellicule qui n'apparaitrait
        que sur la caisse claire ne ressemblerait a rien.
        """
        H, W = self.H, self.W

        # ---- la bande flotte : lent va-et-vient, comme une cassette fatiguee
        if self.flottement > 0.01:
            k = self.flottement
            dx = int(round(W * 0.012 * k * math.sin(t * 0.83 + 1.1)
                           + W * 0.005 * k * math.sin(t * 2.37)))
            dy = int(round(H * 0.008 * k * math.sin(t * 0.61)))
            if dx:
                img[:] = np.roll(img, dx, axis=1)
            if dy:
                img[:] = np.roll(img, dy, axis=0)

        # ---- halo laiteux et noirs releves
        if self.halo_doux > 0.01:
            k = self.halo_doux
            # Flou calcule en definition reduite puis redeploye, comme le halo
            # du faisceau : un flou large n'a aucun detail a perdre, et le
            # faire en pleine definition doublait le temps de calcul d'une
            # image 1080p a lui seul.
            petit = downsample(img.mean(axis=2), 4)
            flou = upsample(gauss(petit, max(1.5, H / 600.0)), 4, (H, W))
            img *= (1.0 - 0.10 * k)
            img += flou[..., None] * (0.42 * k)
            img += 0.035 * k                    # les noirs ne sont plus noirs

        # ---- poussiere et rayures de pellicule
        if self.poussiere > 0.01:
            k = self.poussiere
            for _ in range(int(14 * k) + 2):    # grains clairs
                y = int(rng.integers(0, H)); x = int(rng.integers(0, W))
                r = int(rng.integers(1, max(2, int(H * 0.004)) + 1))
                img[max(0, y - r):y + r, max(0, x - r):x + r] += 0.45 * k
            if rng.random() < 0.35 * k:          # une rayure verticale
                x = int(rng.integers(0, W))
                w = max(1, int(W * 0.0012))
                y0 = int(rng.integers(0, H // 2))
                y1 = int(rng.integers(y0 + H // 4, H))
                img[y0:y1, x:x + w] += float(rng.uniform(0.10, 0.40)) * k
            if rng.random() < 0.18 * k:          # un cheveu, une poussiere longue
                y = int(rng.integers(0, H))
                x0 = int(rng.integers(0, W // 2))
                img[y:y + 1, x0:x0 + int(W * rng.uniform(0.05, 0.25))] += 0.22 * k

    def _split(self, img, amount):
        """Dedoublement chromatique du trait sur les gros coups de sub.

        Les trois couches se separent lateralement : le rouge part d'un cote,
        le bleu de l'autre, le vert reste en place — chaque ligne se lit donc
        en triple, comme un defaut de convergence. Puis elles se recollent
        pendant que le coup retombe.

        C'est applique avant que le fond ne soit pose, donc seul ce qui est
        dessine se dedouble : le fond, lui, ne bouge pas.
        """
        dx = int(round(self.split_px * (self.H / 540.0) * amount))
        if dx < 1:
            return img
        dy = int(round(dx * 0.22))
        # On ne decale pas les canaux tels quels : dans une palette verte, le
        # rouge et le bleu du trait sont presque vides, et les copies decalees
        # seraient a peine visibles. On repart donc de l'intensite du trait et
        # on en tire trois copies de meme force, une par couleur primaire —
        # c'est ce qui donne vraiment trois lignes au lieu d'une frange.
        lum = img.max(axis=2)
        trip = np.stack([self._shift(lum, dx, -dy), lum,
                         self._shift(lum, -dx, dy)], axis=-1)
        a = amount * 0.90
        return img * (1.0 - a) + trip * a

    def colorize(self, field, t, collapse, shake, rng):
        W, H = self.W, self.H
        core = gauss(field, self.sigma)

        # Un neon n'eclaire pas dans le vide : ce qu'on voit autour de lui est
        # sa lumiere renvoyee par la surface qui le porte. Plus cette surface
        # est proche, plus la lueur est serree et vive ; plus elle est loin,
        # plus elle s'etale et palit. On deplace donc a la fois le poids et le
        # rayon des deux flous. A 0,5 on retrouve exactement l'ancien rendu.
        k = float(np.clip(self.reflet, 0.0, 1.0))
        ecart = 1.45 - 0.9 * k
        pres, loin = 1.0 + 1.2 * (k - 0.5), 1.0 - 1.2 * (k - 0.5)
        g4 = gauss(downsample(core, 4), 2.6 * ecart)
        g8 = gauss(downsample(core, 8), 4.5 * ecart)
        glow = (upsample(g4, 4, (H, W)) * 2.6 * pres
                + upsample(g8, 8, (H, W)) * 3.4 * loin)

        inten = core * 1.15
        hot = np.clip(inten - 0.72, 0, None)

        img = np.zeros((H, W, 3), dtype=np.float32)
        base = np.clip(inten, 0, 1.6)
        # Effet de tube : le verre assombrit les bords du trait, et un reflet
        # file le long de son arete haute. Le trace n'a pas de normales — c'est
        # un champ d'intensite — mais la difference entre deux flous donne
        # exactement l'anneau qu'il faut pour le bord, et le coeur decale d'un
        # pixel ou deux fait le reflet.
        luisant = None
        if self.tube > 0.01:
            kt = float(self.tube)
            bord = np.clip(gauss(field, self.sigma * 1.9) * 1.9 - core, 0.0, None)
            base = np.clip(base - kt * 0.45 * bord, 0.0, None)
            d = max(1, int(round(self.sigma)))
            luisant = self._shift(gauss(field, self.sigma * 0.5), -d, -d) * (kt * 0.60)
        # la caisse claire embrase le trait : il vire au jaune et le halo enfle
        fluo, halo = self.c_fluo, self.c_halo
        sn = self.snare * self.snare_hit(t)
        gmul = 0.55
        if sn > 0.01:
            k = min(0.78, sn * 0.82)
            fluo = tuple(f * (1.0 - k) + y * k for f, y in zip(fluo, SNARE_RGB))
            halo = tuple(h * (1.0 - k) + y * k for h, y in zip(halo, SNARE_HALO))
            gmul = 0.55 * (1.0 + 1.25 * sn)
        if self.couleurs > 0.01:
            # Le trait prend la teinte du dernier instrument frappe. On garde
            # le plus fort du moment plutot que de melanger les familles : deux
            # teintes moyennees donnent un gris, et l'oreille, elle, entend
            # bien un instrument a la fois sur l'attaque.
            # On classe sur la fraicheur du coup, presque pas sur sa force :
            # c'est le dernier instrument frappe qui doit donner la couleur.
            # Classees a la force, les familles denses — le charley — gagnaient
            # jusque sur la grosse caisse, et sa teinte ne sortait jamais.
            meilleur, teinte = 0.0, None
            for fam, col in TEINTES.items():
                e = self.hit_env(t, fam, fall=14.0, plancher=0.75)
                if e > meilleur:
                    meilleur, teinte = e, col
            if teinte is not None:
                k = min(0.92, self.couleurs * meilleur)
                fluo = tuple(f * (1.0 - k) + c * k for f, c in zip(fluo, teinte))
                halo = tuple(h * (1.0 - k) + c * k for h, c in zip(halo, teinte))
        gc = np.clip(glow, 0, 3.0)
        # le halo est le meme pour les trois couches : le multiplier une fois
        # plutot que trois epargne deux passes de la taille de l'image
        gc *= np.float32(gmul * float(self.neon))
        for c in range(3):
            img[:, :, c] = fluo[c] * base + halo[c] * gc
        # x**1.25 = x * sqrt(sqrt(x)) : deux racines carrees coutent bien
        # moins qu'une puissance fractionnaire, pour le meme resultat
        np.clip(hot * 1.25, 0, 1.0, out=hot)
        quart = np.sqrt(hot)
        np.sqrt(quart, out=quart)
        hot *= quart
        img += hot[..., None] * self.c_hot
        if luisant is not None:
            img += np.clip(luisant, 0.0, 1.4)[..., None]
        sp = self.split * self.sub_hit(t)
        if sp > 0.01:
            img = self._split(img, sp)
        # le fond passe sous les textures : scanlines, vignettage et grain
        # le travaillent comme le reste de la dalle.
        img += self.fond_texture(t)
        if self.backdrop is not None:
            fond = self.backdrop.at(t)
            if self.bg_flash > 0.01:
                # le fond est eclaire par le coup, comme par un flash de studio
                fond = fond * (1.0 + 1.8 * self.bg_flash
                               * self.hit_env(t, self.flash_on, fall=13.0))
            img += fond

        # Scanlines, ondulation lente et vignettage : trois multiplications de
        # la taille de l'image, ramenees a une seule. Le peigne et le
        # vignettage sont figes, seule l'ondulation suit le temps.
        self._tables_dalle()
        yy = np.arange(H, dtype=np.float32)[:, None]
        roll = 1.0 + 0.05 * np.cos((yy / H + t * 0.16) * 2.0 * math.pi)
        img *= ((self._scan * roll).astype(np.float32) * self._vign)[..., None]

        img += upsample(rng.standard_normal((H // 4, W // 4)).astype(np.float32),
                        4, (H, W))[..., None] * 0.011

        self._reactions_glitch(img, t, rng)

        gl = self.glitch_at(t)
        if gl > 0.02:
            # tranches decalees
            for _ in range(int(3 + 18 * gl)):
                y0 = int(rng.integers(0, H - 4))
                y1 = min(H, y0 + int(rng.integers(3, max(6, int(H * 0.10 * gl) + 5))))
                off = int(rng.integers(-int(W * 0.10 * gl) - 2, int(W * 0.10 * gl) + 3))
                img[y0:y1] = np.roll(img[y0:y1], off, axis=1)
            # tranches recopiees ailleurs (datamosh)
            if gl > 0.55:
                for _ in range(int(5 * gl)):
                    h = int(rng.integers(4, max(8, int(H * 0.09))))
                    y0 = int(rng.integers(0, H - h))
                    ys = int(rng.integers(0, H - h))
                    img[y0:y0 + h] = img[ys:ys + h]
            # pertes de signal
            if gl > 0.45:
                for _ in range(int(4 * gl)):
                    h = int(rng.integers(2, max(5, int(H * 0.05))))
                    y0 = int(rng.integers(0, H - h))
                    img[y0:y0 + h] *= float(rng.uniform(0.0, 0.30))
            sh = max(1, int(14 * gl))
            img[:, :, 0] = np.roll(img[:, :, 0], sh, axis=1)
            img[:, :, 2] = np.roll(img[:, :, 2], -sh, axis=1)

        if self.curve:
            img = self._warp(img)

        u_out = self.tl.at("out", t)
        if u_out > 0:
            f = 1.0 if u_out <= 0.72 else max(0.0, 1.0 - (u_out - 0.72) / 0.20)
            img *= f
            if 0.66 < u_out < 0.80:
                cy, cx = H // 2, W // 2
                r = max(2, int(H * 0.006))
                img[cy - r:cy + r, cx - int(r * 2.5):cx + int(r * 2.5)] += 0.9

        np.clip(img, 0.0, 1.0, out=img)
        return ((img ** (1.0 / 1.06)) * 255.0 + 0.5).astype(np.uint8)

    def frame(self, i):
        t = i / self.fps
        field, collapse, shake, rng = self.intensity(t)
        return self.colorize(field, t, collapse, shake, rng)


# ==========================================================================
#  Pipeline
# ==========================================================================

def available_memory():
    """Memoire vive encore libre, en megaoctets (0 si on ne sait pas).

    C'est la memoire *disponible* qu'on veut, pas celle qui est installee :
    sur une machine ou le systeme et le navigateur occupent deja la moitie de
    la barrette, elle seule dit combien de taches de rendu tiendront. Chaque
    systeme a son guichet, et aucun ne demande de bibliotheque en plus.
    """
    try:
        if sys.platform.startswith("win"):
            import ctypes

            class _Etat(ctypes.Structure):
                _fields_ = [("dwLength", ctypes.c_ulong),
                            ("dwMemoryLoad", ctypes.c_ulong),
                            ("ullTotalPhys", ctypes.c_ulonglong),
                            ("ullAvailPhys", ctypes.c_ulonglong),
                            ("ullTotalPageFile", ctypes.c_ulonglong),
                            ("ullAvailPageFile", ctypes.c_ulonglong),
                            ("ullTotalVirtual", ctypes.c_ulonglong),
                            ("ullAvailVirtual", ctypes.c_ulonglong),
                            ("ullAvailExtendedVirtual", ctypes.c_ulonglong)]

            e = _Etat()
            e.dwLength = ctypes.sizeof(_Etat)
            if ctypes.windll.kernel32.GlobalMemoryStatusEx(ctypes.byref(e)):
                return e.ullAvailPhys / 1e6
            return 0.0
        with open("/proc/meminfo") as f:                  # Linux
            for ligne in f:
                if ligne.startswith("MemAvailable:"):
                    return int(ligne.split()[1]) / 1024.0
    except Exception:                                     # noqa: BLE001
        pass
    try:                                                  # macOS, BSD
        return os.sysconf("SC_PAGE_SIZE") * os.sysconf("SC_AVPHYS_PAGES") / 1e6
    except Exception:                                     # noqa: BLE001
        return 0.0


def python_trop_petit(w, h):
    """Message si l'interpreteur ne peut pas tenir une image de cette taille.

    Un Python 32 bits plafonne vers deux gigaoctets par processus, quelle que
    soit la memoire installee — et c'est la version que propose parfois
    l'installateur Windows. Aucun reglage ne rattrape cela : autant le dire.
    """
    if sys.maxsize > 2 ** 32 or w * h <= 2.3e6:
        return None
    return ("Python 32 bits : chaque processus plafonne vers 2 Go, ce qui ne "
            "suffit pas pour du %dx%d. Reinstallez Python en 64 bits depuis "
            "python.org (choisir « Windows installer (64-bit) »), ou restez "
            "en 1920x1080." % (w, h))


def fit_jobs(jobs, w, h, duration, method="fork"):
    """Ramene le nombre de taches de rendu a ce que la memoire supporte.

    Une tache coute d'autant plus cher que l'image est grande et le morceau
    long. Sous Unix elles sont des copies du processus principal et se
    partagent l'essentiel ; sous Windows chacune emporte son exemplaire
    complet, et en lancer une par coeur sur un portable epuise la memoire
    avant la fin du premier plan — le rendu s'arrete alors sur un MemoryError,
    parfois apres une heure de calcul. Mieux vaut quelques taches de moins.

    Les deux estimations viennent de mesures : a definition egale, une tache
    « spawn » pese environ trois fois une tache « fork ».
    """
    mpx = w * h / 1e6
    if method == "spawn":
        besoin = 40.0 + 0.20 * duration + 95.0 * mpx      # tout est en double
    else:
        besoin = 35.0 + 47.0 * mpx                        # le reste est partage
    # Une estimation trop juste ne se paie pas en lenteur mais en rendu perdu,
    # parfois apres une heure de calcul : on garde donc une reserve, d'autant
    # plus large que l'image est grande, car c'est la que l'erreur coute cher.
    besoin *= 1.30
    marge = 700.0 + 90.0 * mpx                            # ffmpeg, et le systeme
    libre = available_memory()
    if libre <= 0.0:
        return jobs                        # systeme inconnu : on ne decide rien
    return max(1, min(jobs, int((libre - marge) / besoin)))


def pool_context():
    """Comment demarrer les taches de rendu.

    `fork` duplique le processus en cours : instantane, et la memoire reste
    partagee tant que personne n'y ecrit. C'est le choix naturel sous Linux et
    macOS — mais il n'existe pas sous Windows, ou il faut lancer des
    interpreteurs neufs (`spawn`) et leur transmettre le moteur. La variable
    d'environnement OMNIPOTARD_MP force l'un ou l'autre, ce qui sert a
    eprouver le chemin Windows depuis une autre machine.
    """
    import multiprocessing as mp
    forced = os.environ.get("OMNIPOTARD_MP", "").strip().lower()
    for name in ([forced] if forced else []) + ["fork", "spawn"]:
        try:
            return mp.get_context(name)
        except ValueError:
            continue
    return mp.get_context()


_R = None


def _init_worker(r):
    """Installe le moteur dans une tache qui demarre vierge (spawn)."""
    global _R
    _R = r


def _worker(i):
    return _R.frame(i).tobytes()


def main():
    ap = argparse.ArgumentParser(description="Intro video OMNIPOTARD (oscilloscope vert fluo)")
    ap.add_argument("-o", "--out", default="omnipotard_intro.mp4")
    ap.add_argument("-W", "--width", type=int, default=1920)
    ap.add_argument("-H", "--height", type=int, default=1080)
    ap.add_argument("--fps", type=int, default=60)
    ap.add_argument("--duration", type=float, default=DUREE_REF)
    ap.add_argument("--crf", type=int, default=16)
    ap.add_argument("--jobs", type=int, default=os.cpu_count() or 2)
    ap.add_argument("--seed", type=int, default=7)
    ap.add_argument("--music", default=MUSIC_PATH,
                    help="morceau a utiliser ; vide ou --synth pour la musique de synthese")
    ap.add_argument("--music-start", type=float, default=MUSIC_START,
                    help="debut de l'extrait dans le morceau (s)")
    ap.add_argument("--synth", action="store_true", help="force la bande-son de synthese")
    ap.add_argument("--palette", default="vert", choices=sorted(PALETTES),
                    help="couleur du trace (et fond de dalle pour bleu-fond)")
    ap.add_argument("--subtitle", default=SUB_TXT, help="ligne sous le logo")
    ap.add_argument("--bg", default=None, choices=BACKGROUNDS,
                    help="fond de dalle (defaut : celui de la palette)")
    ap.add_argument("--bg-color", default=None, help="couleur du fond, ex. #101828")
    ap.add_argument("--bg-strength", type=float, default=1.0,
                    help="intensite du fond (0 = noir)")
    ap.add_argument("--bg-clear", type=float, default=0.55,
                    help="0 a 1 : creuse le fond derriere la machine")
    ap.add_argument("--wobble", type=float, default=0.0,
                    help="ondulation du trace de la machine (0 = trait net)")
    ap.add_argument("--split", type=float, default=1.0,
                    help="dedoublement chromatique du trait sur les gros subs")
    ap.add_argument("--split-px", type=float, default=11.0,
                    help="ecart des copies, en pixels ramenes a 540p")
    ap.add_argument("--split-count", type=int, default=3,
                    help="nombre de declenchements dans toute la video")
    ap.add_argument("--snare", type=float, default=1.0,
                    help="embrasement jaune sur la caisse claire (0 = aucun)")
    ap.add_argument("--wave", type=float, default=1.0,
                    help="amplitude de la courbe sonore")
    ap.add_argument("--wave-win", type=float, default=0.07,
                    help="base de temps de la courbe (s) : large = mouvement lent")
    ap.add_argument("--wave-smooth", type=int, default=56,
                    help="lissage de la courbe : large = trace plus calme")
    ap.add_argument("--wave-trig", type=float, default=0.0,
                    help="balayage declenche : largeur d'ecran en temps (0 = libre)")
    ap.add_argument("--wave-passes", type=int, default=1,
                    help="passages de lissage (3 = trace nettement plus calme)")
    ap.add_argument("--wave-punch", type=float, default=0.85,
                    help="gonflement de la courbe sur les temps forts")
    ap.add_argument("--trail", type=float, default=0.0,
                    help="trainee de la bande (0 = trait net)")
    ap.add_argument("--backdrop", default=None, help="image de fond")
    ap.add_argument("--backdrop-strength", type=float, default=1.00)
    ap.add_argument("--backdrop-clear", type=float, default=0.28)
    ap.add_argument("--screen-dim", type=float, default=0.40,
                    help="opacite de la dalle devant l'image de fond")
    ap.add_argument("--no-curve", action="store_true", help="desactive la courbure CRT")
    ap.add_argument("--no-audio", action="store_true", help="video muette (l'image reste pilotee par le son)")
    ap.add_argument("--stills", default="", help="dossier ou exporter des images cles PNG")
    ap.add_argument("--still-times", default="0.9,1.7,2.1,2.6,4.2,6.2,7.6,8.4,9.1,10.0,10.8")
    args = ap.parse_args()

    if args.synth or not args.music or not os.path.exists(args.music):
        if args.music and not args.synth and not os.path.exists(args.music):
            print("morceau introuvable (%s) -> bande-son de synthese" % args.music, flush=True)
        audio = synth_audio(args.duration, seed=args.seed)
    else:
        audio = load_music(args.music, args.duration, args.music_start, seed=args.seed)
        print("morceau %s  extrait a %.2f s  battement %.4f s (%.1f BPM)  %d coups detectes"
              % (args.music, args.music_start, audio["beat"], 60.0 / audio["beat"],
                 len(audio["events"])), flush=True)

    global _R
    _R = Renderer(args.width, args.height, args.fps, args.duration, audio,
                  curve=not args.no_curve, seed=args.seed,
                  palette=args.palette, subtitle=args.subtitle.upper(),
                  bg=args.bg, bg_strength=args.bg_strength,
                  bg_clear=args.bg_clear,
                  bg_color=hex_to_rgb(args.bg_color) if args.bg_color else None)
    _R.wobble, _R.split, _R.split_px = args.wobble, args.split, args.split_px
    _R.split_count = args.split_count
    _R.snare, _R.wave_gain = args.snare, args.wave
    _R.trail = args.trail
    _R.wave_win, _R.wave_trig = args.wave_win, args.wave_trig
    _R.wave_passes, _R.wave_punch = args.wave_passes, args.wave_punch
    _R.set_wave_smooth(args.wave_smooth)
    if args.backdrop:
        _R.backdrop = make_backdrop(
            args.backdrop, args.width, args.height, args.fps, args.duration,
            strength=args.backdrop_strength, clear=args.backdrop_clear,
            scale=_R.scale, screen_dim=args.screen_dim)

    if args.stills:
        from PIL import Image
        os.makedirs(args.stills, exist_ok=True)
        for ts in [float(x) for x in args.still_times.split(",") if x.strip()]:
            Image.fromarray(_R.frame(int(round(ts * args.fps)))).save(
                os.path.join(args.stills, "t%05.2f.png" % ts))
            print("still %.2fs" % ts, flush=True)
        return

    nframes = int(round(args.duration * args.fps))
    tmpdir = tempfile.mkdtemp(prefix="omnipotard_")
    wav = None
    if not args.no_audio:
        wav = os.path.join(tmpdir, "omnipotard.wav")
        write_wav(wav, audio["stereo"], audio["sr"])
        print("audio -> %s" % wav, flush=True)

    cmd = ["ffmpeg", "-y", "-hide_banner", "-loglevel", "error",
           "-f", "rawvideo", "-pix_fmt", "rgb24",
           "-s", "%dx%d" % (args.width, args.height), "-r", str(args.fps), "-i", "-"]
    if wav:
        cmd += ["-i", wav]
    cmd += ["-c:v", "libx264", "-preset", "slow", "-crf", str(args.crf),
            "-pix_fmt", "yuv420p", "-profile:v", "high", "-movflags", "+faststart",
            "-x264-params", "keyint=%d" % (args.fps * 2)]
    if wav:
        cmd += ["-c:a", "aac", "-b:a", "192k", "-ac", "2", "-shortest"]
    cmd += [args.out]

    import time
    proc = subprocess.Popen(cmd, stdin=subprocess.PIPE)
    t0 = time.time()
    try:
        ctx = pool_context()
        if args.jobs > 1:
            demande = args.jobs
            args.jobs = fit_jobs(args.jobs, args.width, args.height,
                                 args.duration, ctx.get_start_method())
            if args.jobs < demande:
                print("  memoire disponible limitee : %d taches de rendu au lieu "
                      "de %d" % (args.jobs, demande), flush=True)
        if args.jobs > 1:
            chunk = max(args.jobs, 24)
            with ctx.Pool(args.jobs, initializer=_init_worker,
                          initargs=(_R,)) as pool:
                for start in range(0, nframes, chunk):
                    for buf in pool.map(_worker, range(start, min(nframes, start + chunk)), 1):
                        proc.stdin.write(buf)
                    done = min(nframes, start + chunk)
                    el = time.time() - t0
                    print("\r  %d/%d frames  %.0fs  (eta %.0fs)"
                          % (done, nframes, el, el / done * (nframes - done)), end="", flush=True)
        else:
            for i in range(nframes):
                proc.stdin.write(_worker(i))
    finally:
        proc.stdin.close()
        proc.wait()
    print("\n%s  (%.1f s, %dx%d @ %dfps)" % (args.out, args.duration, args.width,
                                             args.height, args.fps))



# ==========================================================================
#  Morceau existant : chargement, analyse de la batterie, montage
#  L'image reste pilotee par le son — les pads suivent donc les vrais coups.
# ==========================================================================

def _decode(path, start, duration, sr=SR):
    """Decode un extrait en stereo flottant (recherche precise a l'echantillon)."""
    out = subprocess.run(
        ["ffmpeg", "-v", "error", "-i", path, "-ss", "%.6f" % start,
         "-t", "%.6f" % duration, "-ac", "2", "-ar", str(sr), "-f", "f32le", "-"],
        stdout=subprocess.PIPE, check=True).stdout
    st = np.frombuffer(out, dtype="<f4").astype(np.float64).reshape(-1, 2)
    n = int(duration * sr) + 1
    if len(st) < n:
        st = np.vstack([st, np.zeros((n - len(st), 2))])
    return st[:n]


def _frames(x, sr, hop=256, win=1024):
    n = max(1, (len(x) - win) // hop)
    idx = np.arange(n)[:, None] * hop + np.arange(win)[None, :]
    S = np.abs(np.fft.rfft(x[idx] * np.hanning(win), axis=1))
    return S, np.fft.rfftfreq(win, 1.0 / sr), sr / hop


def _band_flux(S, freqs, lo, hi):
    e = S[:, (freqs >= lo) & (freqs < hi)].sum(axis=1)
    return np.maximum(np.diff(e, prepend=e[0]), 0.0)


def _pick(flux, fps, thresh=2.0, gap=0.055):
    """Sommets d'une courbe d'attaque -> (instant, force)."""
    f = _lowpass(flux / (flux.std() + 1e-9), 3)
    cand = np.where((f[1:-1] > thresh) & (f[1:-1] >= f[:-2]) & (f[1:-1] >= f[2:]))[0] + 1
    out, last = [], -9.0
    for i in cand:
        t = i / fps
        if t - last >= gap:
            out.append((t, float(min(1.0, f[i] / (thresh * 2.6)))))
            last = t
    return out


def detect_beat(mono, sr):
    """Periode du battement, par autocorrelation de la courbe d'attaque."""
    S, freqs, fps = _frames(mono, sr)
    onset = sum(_band_flux(S, freqs, lo, hi) / (_band_flux(S, freqs, lo, hi).std() + 1e-9)
                for lo, hi in ((30, 140), (160, 1200), (4000, 10000)))
    o = onset - onset.mean()
    ac = np.correlate(o, o, mode="full")[len(o) - 1:]
    lags = np.arange(len(ac)) / fps
    sel = (lags > 0.30) & (lags < 1.10)
    # ponderation : un extrait court fait ressortir la demi-periode, alors on
    # privilegie les tempos plausibles (autour de 100 BPM) avant de choisir.
    pref = np.zeros_like(ac)
    pref[sel] = np.exp(-0.5 * (np.log(lags[sel] / 0.62) / 0.55) ** 2)
    best = int(np.argmax(ac * pref))
    for _ in range(2):                    # et on remonte encore d'une octave
        dbl = best * 2                    # si le double tient presque aussi bien
        if dbl < len(ac) and lags[dbl] < 1.10 and ac[dbl] > 0.62 * ac[best]:
            best = dbl
    return _refine_beat(S, freqs, fps, float(lags[best]))


def compute_spectro(mono, sr, bandes=30, fps=60.0, f0=55.0, f1=12000.0):
    """Un spectrogramme compact, destine a la dalle de la machine.

    Trente bandes espacees en octaves — c'est ainsi qu'on entend, et une
    echelle lineaire tasserait tout le grave sur deux lignes — et soixante
    colonnes par seconde, gardees en octets. Un morceau de quatre minutes tient
    dans un demi-megaoctet, ce qui se transmet sans peine aux taches de rendu.
    """
    S, freqs, sfps = _frames(mono, sr)
    bords = np.geomspace(f0, min(f1, float(freqs[-1])), bandes + 1)
    B = np.zeros((bandes, S.shape[0]), dtype=np.float32)
    for i in range(bandes):
        m = (freqs >= bords[i]) & (freqs < bords[i + 1])
        if np.any(m):
            B[i] = S[:, m].sum(axis=1)
    B = np.log1p(B * 12.0)                    # l'oreille est logarithmique
    B /= (float(B.max()) or 1.0)
    n = max(2, int(B.shape[1] * fps / sfps))
    xi = np.linspace(0.0, B.shape[1] - 1.0, n)
    src = np.arange(B.shape[1], dtype=np.float64)
    out = np.empty((bandes, n), dtype=np.uint8)
    for i in range(bandes):
        out[i] = np.clip(np.interp(xi, src, B[i]) * 255.0, 0, 255)
    return out, fps


def _refine_beat(S, freqs, fps, coarse):
    """Affine la periode sur les attaques graves.

    Le pas d'autocorrelation vaut 5,3 ms. A 85 BPM, deux millisecondes
    d'erreur suffisent a decaler d'un quart de temps au bout de quatre
    minutes : la grille du sequenceur part alors en vrille, et les coups ne
    tombent plus ou il faut. On cherche donc, autour du sommet, la periode
    qui range le mieux les attaques graves sur la grille.

    Sur Hint, cela corrige 0,7040 s en 0,7060 s — et la concentration des
    grosses caisses sur la grille passe de 1,4 a 3,8.
    """
    low = _env(S, freqs, 35, 110, smooth=max(2, int(fps * 0.045)))
    idx = _attacks(low, fps, 0.16, 2.2)
    if len(idx) < 12:
        return coarse
    t = idx / fps
    best, score = coarse, -1.0
    for b in np.linspace(coarse * 0.985, coarse * 1.015, 81):
        h = np.histogram(np.mod(t, b) / b, bins=8, range=(0, 1))[0]
        c = h.max() / max(h.mean(), 1e-9)
        if c > score:
            best, score = float(b), c
    return best


def _env(S, freqs, lo, hi, smooth=1):
    """Enveloppe d'une bande, lissee sur `smooth` trames.

    Le lissage n'est pas cosmetique : dans le grave, l'enveloppe redressee
    ondule a deux fois la frequence du son (100 Hz pour un sub a 50 Hz). Sans
    l'effacer, on prend cette ondulation pour des coups et on en compte deux
    fois trop.
    """
    e = S[:, (freqs >= lo) & (freqs < hi)].sum(axis=1)
    return _lowpass(e, smooth) if smooth > 1 else e


def _attacks(e, fps, gap, rel):
    """Instants ou une enveloppe monte nettement plus que d'ordinaire."""
    d = np.maximum(np.diff(e, prepend=e[0]), 0.0)
    base = _lowpass(d, max(1, int(fps * 1.5))) + 0.4 * d.std() + 1e-12
    r = d / base
    step, out, i, n = max(1, int(fps * gap)), [], 1, len(e)
    while i < n - 1:
        if r[i] > rel and r[i] >= r[i - 1] and r[i] >= r[i + 1]:
            out.append(i)
            i += step
        else:
            i += 1
    return np.array(out, dtype=np.int64)


def _salience(e, fps, idx, ahead=0.02):
    """Combien une bande ressort a ces instants, rapportee a son ordinaire."""
    if len(idx) == 0:
        return np.zeros(0)
    base = _lowpass(e, max(1, int(fps * 1.5))) + 1e-12
    w = max(1, int(fps * ahead))
    return np.array([e[max(0, i - 1):i + w].max() / base[i] for i in idx])


ECART_SPLIT = 25.0        # secondes minimum entre deux dedoublements


def pick_split_times(ev_t, ev_f, eligibles, dur, count):
    """Les instants ou le trait se dedouble, pour toute une video.

    Un seuil ne conviendrait pas : selon le mixage il ne se declencherait
    jamais, ou vingt fois. On classe donc les coups retenus par force et on
    garde les plus gros, en refusant deux instants trop rapproches — l'effet
    doit rester un evenement, pas une ponctuation.

    Deux garde-fous, et non un seul. L'ecart minimum est d'abord une vraie
    duree en secondes, pas seulement une fraction du morceau : sur un extrait
    de vingt secondes, une fraction laissait passer trois declenchements en
    dix-sept secondes. Le nombre demande est ensuite plafonne par ce que la
    duree peut contenir a cet ecart-la — « trois fois par video » ne veut rien
    dire si la video dure quinze secondes.
    """
    gap = max(ECART_SPLIT, 0.14 * dur)
    count = min(int(count), 1 + int(dur / max(gap, 1.0)))
    out = []
    for i in np.argsort(-np.asarray(ev_f)):
        if not eligibles[i]:
            continue
        t = float(ev_t[i])
        if any(abs(t - u) < gap for u in out):
            continue
        out.append(t)
        if len(out) >= max(0, int(count)):
            break
    return np.array(sorted(out), dtype=np.float64)


def detect_hits(mono, sr):
    """Coups de batterie -> evenements de pads.

    Le flux par bande ne suffisait pas a distinguer les instruments : dans un
    morceau dub la basse occupe la meme bande que la grosse caisse, et le pad
    de kick s'allumait donc sur chaque note de basse. Chaque famille est ici
    reconnue par ce qui la distingue physiquement.

    - **Grosse caisse** : une attaque dans le grave *accompagnee d'un clic*
      entre 2 et 6 kHz. Une note de basse n'a pas ce clic. Mesure sur Hint :
      sans ce test, 2,05 attaques graves par temps reparties au hasard
      (concentration 1,15 sur la grille) ; avec, 0,6 par temps nettement
      calees (concentration 3,3).
    - **Caisse claire** : un corps entre 180 et 450 Hz accompagne de bruit
      entre 2,5 et 8 kHz. Le bruit elimine les notes tenues et les accords,
      qui sont harmoniques. De 3,98 coups par temps a 1,34, concentration
      1,47 -> 3,0.
    - **Charley** : l'aigu seul, sans corps. Il reste dense, et c'est normal.
    - Une attaque grave **sans** clic est une note de basse : elle a son
      propre pad, plus discret, au lieu de se faire passer pour un kick.
    """
    S, freqs, fps = _frames(mono, sr)
    lag = 0.025          # la detection voit l'attaque au debut de sa fenetre
    nyq = sr * 0.5

    low = _env(S, freqs, 35, 110, smooth=max(2, int(fps * 0.045)))
    body = _env(S, freqs, 180, 450, smooth=2)
    click = _env(S, freqs, 2000, min(6000, nyq))
    noise = _env(S, freqs, 2500, min(8000, nyq))
    air = _env(S, freqs, min(8000, nyq * 0.8), min(15000, nyq))

    ev = []

    # ---- grave : grosse caisse si ca claque, note de basse sinon
    idx = _attacks(low, fps, 0.16, 2.2)
    cl = _salience(click, fps, idx)
    st = _salience(low, fps, idx, 0.04)
    for i, c, v in zip(idx, cl, st):
        t = i / fps + lag
        f = float(np.clip(v / 6.0, 0.30, 1.0))
        if c > 1.6:
            ev.append((t, PAD_OF["kick"], f, DECAY_OF["kick"]))
        else:
            ev.append((t, 1, f * 0.7, DECAY_OF["bass"]))

    # ---- medium : caisse claire, si le coup est bruite et pas un grave
    idx = _attacks(body, fps, 0.11, 2.2)
    nz = _salience(noise, fps, idx)
    lo = _salience(low, fps, idx, 0.03)
    st = _salience(body, fps, idx)
    for i, n_, l_, v in zip(idx, nz, lo, st):
        if n_ <= 1.8 or l_ >= 2.0:
            continue
        f = float(np.clip(v / 6.0, 0.30, 1.0))
        # le partage se fait sur la force : les coups appuyes du contretemps
        # vont a la caisse claire, les petites frappes aux percussions.
        pad = PAD_OF["rim"] if f > 0.42 else PAD_OF["perc"]
        ev.append((i / fps + lag, pad, f, DECAY_OF["rim"]))

    # ---- aigu : charleston, s'il n'a pas de corps
    idx = _attacks(air, fps, 0.05, 2.0)
    bd = _salience(body, fps, idx)
    st = _salience(air, fps, idx)
    k = 0
    for i, b_, v in zip(idx, bd, st):
        if b_ >= 1.8:
            continue
        f = float(np.clip(v / 5.0, 0.30, 1.0))
        ev.append((i / fps + lag, (10, 11)[k % 2], f, DECAY_OF["hat"]))
        k += 1

    # ---- bandes de frequences : des declencheurs qui ecoutent une hauteur
    #
    # Ils ne passent par aucune reconnaissance d'instrument : ce qui monte
    # dans la bande part, que ce soit une peau, une voix ou une nappe. C'est
    # ce qui les rend utiles a cote de la batterie, pas redondants avec elle.
    for b, (nom, blo, bhi, sm, gap) in enumerate(BANDES):
        if blo >= nyq:
            continue
        e = _env(S, freqs, blo, min(bhi, nyq), smooth=max(2, int(fps * sm)))
        if not e.any():
            continue
        idx = _attacks(e, fps, gap, 2.0)
        for i, v in zip(idx, _salience(e, fps, idx)):
            f = float(np.clip(v / 5.0, 0.30, 1.0))
            ev.append((i / fps + lag, PAD_BANDE + b, f, 11.0))

    ev.sort()
    return ev


def _ramp(t, pts):
    return np.interp(t, [p[0] for p in pts], [p[1] for p in pts])


def load_music(path=MUSIC_PATH, duration=DUREE_REF, start=MUSIC_START, sr=SR, seed=3):
    """Monte un extrait du morceau sur la scenographie, y ajoute les FX de
    synthese (souffles, impact, sortie) et en extrait la batterie."""
    tl = Timeline(duration)
    st = _decode(path, start, duration, sr)
    n = len(st)
    mono_src = st.mean(axis=1)
    beat = detect_beat(mono_src, sr)
    events = detect_hits(mono_src, sr)

    t = np.arange(n) / sr
    g0 = tl.start("groove")
    sw0, sw1 = tl.start("sweep"), tl.end("sweep")
    m0, t0, o0 = tl.start("zoom"), tl.start("title"), tl.start("out")

    # --- montage : le morceau est mat avant le drop, evide pendant le break
    dull = np.stack([_lowpass(st[:, c], 42) for c in range(2)], axis=1)
    thin = st - np.stack([_lowpass(st[:, c], 30) for c in range(2)], axis=1)
    # le morceau reste audible des le debut : juste mat et un peu en retrait,
    # il s'ouvre progressivement au lieu de sauter au drop.
    a_dull = _ramp(t, [(0, .58), (g0 - 0.60, .50), (g0 - 0.05, .12), (g0, 0)])[:, None]
    a_thin = _ramp(t, [(0, 0), (m0 - 0.02, 0), (m0 + 0.10, .85), (t0 - 0.12, .85),
                       (t0, 0)])[:, None]
    gain = _ramp(t, [(0, .72), (g0 - 0.60, .80), (g0 - 0.02, .90), (g0, 1.0),
                     (m0, 1.0), (m0 + 0.10, .66),
                     (t0, 1.0), (o0, 1.0), (o0 + 0.16, 0.0)])[:, None]
    mix = (st * (1.0 - a_dull - a_thin) + dull * a_dull + thin * a_thin) * gain

    # --- les FX, conserves tels quels
    rng = np.random.default_rng(seed)
    fx = np.zeros(n)

    def add(sig, at, g=1.0):
        i0 = max(0, int(at * sr))
        i1 = min(n, i0 + len(sig))
        if i1 > i0:
            fx[i0:i1] += sig[:i1 - i0] * g

    add(_whoosh(sw1 - sw0, sr, rng, up=True), sw0, 0.55)     # souffle pendant
    #                                       que la machine apparait (fenetre sweep)
    add(_whoosh(max(0.2, t0 - m0), sr, rng, up=True), m0, 0.17)         # entree dans
    #                                          l'ecran : le souffle passe a 15 %
    ti = np.arange(int(min(3.0, duration - t0) * sr)) / sr              # impact du titre
    fi = 30.0 + 120.0 * np.exp(-ti * 9.0)                                   # (bass tres attenuee)
    imp = np.sin(2 * math.pi * np.cumsum(fi) / sr) * np.exp(-ti * 1.9) * 0.22
    imp += _lowpass(rng.standard_normal(len(ti)), 12) * np.exp(-ti * 3.5) * 0.07
    add(imp, t0)
    add(_whoosh(0.55, sr, rng, up=True), t0 - 0.55, 0.13)  # tres leger souffle qui
    #                                             atterrit pile quand le titre arrive
    add(_tv_off(max(0.20, duration - o0), sr, rng), o0, 1.0)            # extinction
    fx *= _ramp(t, [(0, 1), (duration - 0.04, 1), (duration, 0)])

    # un peu de reverbe sur les FX seuls : c'est ce qui les rend aeriens
    fx = fx + _fft_conv(fx, _reverb_ir(sr, dur=2.0, decay=1.0)) * 0.28
    mix += fx[:, None] * 0.74
    mix = _tanh_limit(mix * 0.92, 1.35)
    fade = (np.clip(t / 0.03, 0, 1) * np.clip((duration - t) / 0.10, 0, 1))[:, None]
    mix *= fade
    mix /= (np.max(np.abs(mix)) or 1.0) / 0.94

    m = mix.mean(axis=1)
    return {"stereo": (np.clip(mix, -1, 1) * 32767).astype("<i2"),
            "mono": m.astype(np.float32), "events": events, "sr": sr, "beat": beat}

if __name__ == "__main__":
    main()
