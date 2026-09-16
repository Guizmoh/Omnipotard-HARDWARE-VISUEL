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
    compute_spectro,
)
from omnipotard_intro import (  # noqa: E402
    BACKGROUNDS, PALETTES, hex_to_rgb, rgb_to_hex, load_backdrop, is_video,
    VERSION, INSTRUMENTS, DECLENCHEURS, groupes_declencheurs,
    compte_frappes, TRAVELLINGS, FAMILLES, apercu_possible,
    backdrop_quality, PRESETS, CHAMPS, AIDE, COMPTE, QUALITES, pick_split_times,
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
OUTDIR = WORKDIR
MO = 1024 * 1024
MAX_UPLOAD = 220 * MO                   # un morceau, pas une discotheque
# Une video de telephone pese des centaines de megaoctets, et une video de
# fond est rarement courte : la limite des morceaux n'a pas de sens pour elle.
MAX_FOND = 2048 * MO
BLOC = 1 * MO                           # on lit et on ecrit par blocs


# --------------------------------------------------------------------------
#  Petits utilitaires
# --------------------------------------------------------------------------

def png_bytes(img):
    """Encode un tableau (h, w, 3) uint8 en PNG, sans dependance."""
    h, w, _ = img.shape
    rows = np.hstack([np.zeros((h, 1), np.uint8), img.reshape(h, w * 3)])
    def chunk(tag, data):
        c = tag + data
        return struct.pack(">I", len(data)) + c + struct.pack(">I", zlib.crc32(c))
    return (b"\x89PNG\r\n\x1a\n"
            + chunk(b"IHDR", struct.pack(">IIBBBBB", w, h, 8, 2, 0, 0, 0))
            + chunk(b"IDAT", zlib.compress(rows.tobytes(), 6))
            + chunk(b"IEND", b""))


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


def _dans(valeur, permises, defaut):
    """Un nom venu de la page, ramene a ceux que le moteur connait.

    La page peut etre ouverte dans un onglet reste sur une version plus
    ancienne : mieux vaut retomber sur la valeur par defaut que lever une
    erreur au milieu d'un rendu.
    """
    return valeur if valeur in permises else defaut


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
        "wobble": float(q.get("wobble", 0.0)),
        "split": float(q.get("split", 1.0)),
        "split_count": int(float(q.get("splitCount", 3))),
        "split_on": _dans(q.get("splitOn"), DECLENCHEURS, "grosse caisse"),
        "glitch": float(q.get("glitch", 1.0)),
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
        "backdrop": backdrop_path(q.get("backdrop")),
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
        raise ValueError("reglages illisibles")
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


def backdrop_path(name):
    """Chemin du fond depose, ou None. Le nom vient de la page, donc on le
    ramene a un simple nom de fichier dans le dossier prevu."""
    if not name:
        return None
    p = os.path.join(FONDS, safe_name(name))
    return p if os.path.exists(p) else None


# --------------------------------------------------------------------------
#  Etat : analyses et rendus en cours
# --------------------------------------------------------------------------

class Depasse(Exception):
    """Un apercu demande puis remplace par un autre avant d'etre dessine."""


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
            self.tracks[tid] = {"path": path, "name": name, "info": info}
        return tid, info

    def track(self, tid):
        with self.lock:
            t = self.tracks.get(tid)
        if not t:
            raise KeyError("morceau inconnu (relancez l'envoi)")
        return t

    # ---- apercu
    def still(self, tid, t, q, w, h):
        """Une image, avec les reglages du moment.

        Le moteur est garde par (morceau, definition, bombe) : c'est lui qui
        coute cher a construire, parce qu'il depouille tout le son. La
        couleur et le fond, eux, n'en dependent pas — on les repose sur le
        moteur existant, et bouger un curseur redevient instantane.
        """
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
        key = (tid, w, h, bool(q.get("curve", True)), kw["nettete"])
        with self.lock:
            r = self.renderers.get(key)
        if r is None:
            r = _renderer(tr["info"], w, h, 30, 7, bool(q.get("curve", True)),
                          palette, kw)
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
                "invert", "invert_on", "stut", "stut_on", "stut_loop",
                "scramble", "scr_len", "miroir", "miroir_on",
                "ondul", "ondul_on", "mosaic", "mosaic_on",
                "kaleido", "kaleido_on", "cisaille", "cisaille_on",
                "coupure", "coupure_on", "tapestop", "tapestop_on",
                "cadence", "poussiere", "flottement", "halo_doux",
                "echo", "echo_n", "echo_delay", "couleurs", "step_div",
                "presence")
        APART = POSE + ("wave_smooth", "backdrop", "backdrop_strength",
                        "backdrop_clear", "screen_dim", "travel", "travel_mode",
                        "backdrop_sharp", "spectro", "nettete", "taille")
        # La taille se pose avant l'allure : c'est elle qui decide du creux
        # que la texture garde derriere la machine, et set_look le recalcule.
        r.taille = float(kw["taille"])
        r.set_look(palette, **{k: v for k, v in kw.items() if k not in APART})
        for k in POSE:
            setattr(r, k, kw[k])
        # Le spectrogramme est calcule a partir du son, pas repose comme une
        # couleur : on ne le refait que lorsqu'on l'allume pour la premiere fois.
        if kw["spectro"] > 0.01 and getattr(r, "spec", None) is None:
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
        stamp = (bd, w, h, kw["backdrop_strength"], kw["backdrop_clear"],
                 kw["screen_dim"], round(float(t), 1),
                 kw["travel"], kw["travel_mode"], kw["backdrop_sharp"],
                 kw["taille"])
        # set_look, plus haut, remet le fond a zero — il fait partie de
        # l'allure. On le repose donc ici a chaque fois, en ne le rechargeant
        # que si un de ses reglages a bouge : sans cela, tout apercu qui ne
        # rechargeait pas le fond le perdait purement et simplement.
        if bd:
            if getattr(r, "_bd_stamp", None) != stamp:
                # le travelling s'etale sur tout le morceau : l'apercu montre
                # le cadre de l'instant regarde, pas celui du debut
                r._bd = load_backdrop(
                    bd, w, h, kw["backdrop_strength"], kw["backdrop_clear"],
                    scale=r.scale * r.taille, screen_dim=kw["screen_dim"],
                    seek=float(t),
                    travel=kw["travel"], travel_mode=kw["travel_mode"],
                    dur=max(tr["info"]["duration"], 1e-3),
                    blur=backdrop_quality(kw["backdrop_sharp"])[0])
                r._bd_stamp = stamp
            r.backdrop = r._bd
        else:
            r.backdrop, r._bd, r._bd_stamp = None, None, None
        dur = tr["info"]["duration"]
        # l'apercu montre le morceau tel qu'il joue, sans les fondus des bords
        t = max(0.6, min(float(t), dur - 0.8))
        return frame_performance(r, t, dur + 10.0)

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
               "apercu": apercu}
        with self.lock:
            self.jobs[jid] = job
        threading.Thread(target=self._run, args=(job, tr, q, start, dur),
                         daemon=True).start()
        return job

    def _run(self, job, tr, q, start, dur):
        with self.render_lock:       # le moteur utilise des globales : un a la fois
            try:
                # Un moteur d'apercu pese quelques centaines de megaoctets ;
                # les garder pendant le rendu, c'est autant de place en moins
                # pour les taches de calcul. On les relache, quitte a
                # recalculer une image au retour.
                with self.lock:
                    self.renderers.clear()
                job["state"] = "analyse"
                palette, kw = look_from(q)
                full = tr["info"]["duration"]
                info = (tr["info"] if start <= 0.01 and (not dur or dur >= full - 0.01)
                        else analyze(tr["path"], start, dur))
                job["total"] = int(round(info["duration"] * int(q.get("fps", 30))))
                job["state"] = "rendu"

                def prog(done, total, el):
                    job["done"], job["total"] = done, total
                    job["eta"] = el / max(done, 1) * (total - done)

                # Sans consigne, c'est la qualite choisie qui fixe la
                # compression : imposer un CRF ici annulerait le reglage.
                crf = q.get("crf")
                render_video(tr["path"], job["out"], info=info,
                             width=int(q.get("width", 1920)),
                             height=int(q.get("height", 1080)),
                             fps=int(q.get("fps", 30)),
                             crf=int(crf) if crf else None,
                             quality=("apercu" if job["apercu"] else
                                      _dans(q.get("quality"), QUALITES,
                                            "compatible")),
                             curve=bool(q.get("curve", True)),
                             palette=palette, progress=prog, **kw)
                job["size"] = os.path.getsize(job["out"])
                job["state"] = "fini"
            except Exception as e:                       # noqa: BLE001
                job["error"] = "%s: %s" % (type(e).__name__, e)
                job["state"] = "erreur"
                traceback.print_exc()


STUDIO = Studio()


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
            self.send_header("Content-Type", ctype)
            self.send_header("Content-Length", str(len(body)))
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
            if u.path in ("/", "/index.html"):
                return self._send(200, "text/html; charset=utf-8", PAGE.encode("utf-8"))
            if u.path == "/config":
                return self._json({
                    "version": version(),
                    "palettes": {k: {"trait": rgb_to_hex(v[0]),
                                     "fond": rgb_to_hex(v[3])}
                                 for k, v in sorted(PALETTES.items())},
                    "fonds": list(BACKGROUNDS),
                    "instruments": list(INSTRUMENTS),
                    "declencheurs": [{"titre": t, "noms": n}
                                     for t, n in groupes_declencheurs()],
                    "travellings": list(TRAVELLINGS),
                    "qualites": {k: v["quoi"] for k, v in QUALITES.items()},
                    "presets": {k: {CHAMPS[a]: b for a, b in v.items()}
                                for k, v in PRESETS.items()},
                    "mes": lire_mes_reglages(),
                    "aide": AIDE, "compte": COMPTE,
                })
            if u.path == "/still":
                q["curve"] = q.get("curve", "1") == "1"
                img = STUDIO.still(q["track"], float(q.get("t", 0.0)), q,
                                   int(q.get("w", 640)), int(q.get("h", 360)))
                return self._send(200, "image/png", png_bytes(img))
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

            if u.path == "/job":
                with STUDIO.lock:
                    job = dict(STUDIO.jobs.get(q.get("id"), {}))
                job.pop("out", None)
                return self._json(job or {"error": "rendu inconnu"})
            if u.path == "/download":
                with STUDIO.lock:
                    job = STUDIO.jobs.get(q.get("id"))
                if not job or job["state"] != "fini":
                    return self._fail("rendu non termine", 404)
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
            # l'apercu suivant est deja en route : celui-ci n'a plus lieu d'etre
            self._send(409, "text/plain; charset=utf-8", b"apercu depasse")
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
                return self._json({
                    "track": tid, "name": name,
                    "duration": info["total"], "bpm": info["bpm"],
                    "hits": info["hits"], "drops": info["drops"],
                    "frappes": STUDIO.frappes(info),
                    "duree": info["duration"]})

            if u.path == "/backdrop":
                os.makedirs(FONDS, exist_ok=True)
                name = safe_name(self.headers.get("X-Filename"))
                path = os.path.join(FONDS, name)
                if not self._recevoir(path, MAX_FOND):
                    return self._fail("fond trop gros (%d Mo au plus). Une "
                                      "video plus courte, ou exportee moins "
                                      "lourde, fera le meme effet."
                                      % (MAX_FOND // MO), 413)
                try:                        # ffmpeg doit savoir le lire
                    load_backdrop(path, 64, 36)
                except Exception as e:      # noqa: BLE001
                    os.remove(path)
                    # on repasse le vrai motif : il nomme le format en cause,
                    # ce qu'un « ffmpeg ne sait pas lire ce fichier » taisait
                    return self._fail(e)
                return self._json({"name": name, "video": is_video(path)})

            if u.path == "/reglages":
                corps = self._corps()
                if corps is None:
                    return self._fail("reglages illisibles", 413)
                d = json.loads(corps or b"{}")
                nom = " ".join(str(d.get("nom") or "").split())[:40]
                tout = lire_mes_reglages()
                if d.get("action") == "supprimer":
                    tout.pop(nom, None)
                else:
                    if not nom:
                        return self._fail("donnez un nom a ce reglage")
                    if nom not in tout and len(tout) >= MAX_REGLAGES:
                        return self._fail("deja %d reglages enregistres : "
                                          "effacez-en un" % MAX_REGLAGES)
                    tout[nom] = propre(d.get("valeurs"))
                ecrire_mes_reglages(tout)
                return self._json({"mes": tout, "nom": nom})

            if u.path == "/render":
                corps = self._corps()
                if corps is None:
                    return self._fail("reglages illisibles", 413)
                return self._json(STUDIO.start_job(json.loads(corps or b"{}")))

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

PAGE = r"""<!doctype html>
<html lang="fr"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>Studio Omnipotard</title>
<style>
  :root{
    --bg:#07090b; --panel:#0e1216; --line:#1d262e; --ink:#d6e2dc;
    --dim:#7d8c88; --acc:#3dff72; --bad:#ff6b5e;
    font-family:ui-monospace,SFMono-Regular,Menlo,Consolas,monospace;
  }
  *{box-sizing:border-box}
  body{margin:0;background:var(--bg);color:var(--ink);font-size:13px;line-height:1.5}
  header{padding:16px 20px;border-bottom:1px solid var(--line);display:flex;
    align-items:baseline;gap:12px;flex-wrap:wrap}
  h1{margin:0;font-size:15px;letter-spacing:.16em;text-transform:uppercase;
    color:var(--acc);font-weight:600}
  header span{color:var(--dim)}
  main{display:grid;grid-template-columns:330px minmax(0,1fr);gap:20px;
    padding:20px;align-items:start}
  @media (max-width:860px){main{grid-template-columns:1fr}}
  .card{background:var(--panel);border:1px solid var(--line);border-radius:8px;
    padding:14px;margin-bottom:14px}
  .card h2{margin:0 0 10px;font-size:11px;letter-spacing:.14em;color:var(--dim);
    text-transform:uppercase;font-weight:600}
  label{display:block;margin:9px 0 3px;color:var(--dim);font-size:11px;
    letter-spacing:.06em;text-transform:uppercase}
  input,select,button{font:inherit;color:var(--ink);background:#141a20;
    border:1px solid var(--line);border-radius:5px;padding:6px 8px;width:100%}
  input[type=color]{padding:2px;height:32px;cursor:pointer}
  input[type=range]{padding:0;background:none;border:none;accent-color:var(--acc)}
  button{background:var(--acc);color:#04180c;border:none;font-weight:700;
    cursor:pointer;letter-spacing:.1em;text-transform:uppercase;padding:10px}
  button:disabled{background:#24302a;color:var(--dim);cursor:default}
  button.ghost{background:#141a20;color:var(--ink);font-weight:400;
    border:1px solid var(--line);letter-spacing:0;text-transform:none}
  .row{display:grid;grid-template-columns:1fr 1fr;gap:8px}
  .drop{border:1px dashed #2c3a44;border-radius:8px;padding:22px 12px;
    text-align:center;color:var(--dim);cursor:pointer;transition:.15s}
  .drop:hover,.drop.over{border-color:var(--acc);color:var(--ink)}
  .drop b{color:var(--acc);display:block;margin-bottom:4px;letter-spacing:.08em}
  #shot.calcul{opacity:.35;transition:opacity .2s}
  select.inst{margin:2px 0 4px;font-size:11px;color:var(--dim)}
  .aide{font-size:11px;line-height:1.5;color:#7c8f88;margin:2px 0 12px}
  /* le style en ligne aurait ecrase l'attribut « hidden », qui ne passe que
     par la feuille de style du navigateur */
  #clip{width:100%;display:block;border-radius:6px;background:#000}
  /* « hidden » ne coupe rien tout seul des qu'une regle donne un display :
     il faut le redire pour chacun des deux apercus */
  #clip[hidden], #shot[hidden]{display:none}
  .aide b.freq{display:block;color:var(--acc);font-weight:600;margin-top:2px;
    letter-spacing:.03em}
  #shoterr{display:none;margin-top:8px;padding:8px 10px;border-radius:6px;
    font-size:12px;line-height:1.45;background:#2a1416;border:1px solid #6b2b30;
    color:#ffb4b4}
  #shoterr.on{display:block}
#shot{width:100%;border-radius:8px;border:1px solid var(--line);display:block;
    background:#000;aspect-ratio:16/9;object-fit:contain}
  .meta{display:flex;gap:18px;flex-wrap:wrap;color:var(--dim);margin-top:10px;
    font-size:11px;letter-spacing:.06em}
  .meta b{color:var(--ink);font-weight:600}
  .bar{height:5px;background:#141a20;border-radius:3px;overflow:hidden;margin:8px 0}
  .bar i{display:block;height:100%;background:var(--acc);width:0;transition:.3s}
  .err{color:var(--bad)}
  .hint{color:var(--dim);font-size:11px;margin-top:8px}
  a.dl{display:block;text-align:center;background:var(--acc);color:#04180c;
    padding:10px;border-radius:5px;text-decoration:none;font-weight:700;
    letter-spacing:.1em;text-transform:uppercase}
</style></head><body>

<header>
  <h1>Studio Omnipotard</h1>
  <span id="ver" style="color:#5a6b64;font-size:11px"></span>
  <span id="status">deposez un morceau pour commencer</span>
</header>

<main>
 <div>
  <div class="card">
    <h2>Morceau</h2>
    <div class="drop" id="drop">
      <b>Deposer un fichier</b>mp3, wav, flac, m4a&hellip;<br>ou cliquer pour choisir
    </div>
    <input type="file" id="file" accept="audio/*" hidden>
    <div class="meta" id="trackmeta" hidden>
      <span>duree <b id="m-dur">-</b></span>
      <span>tempo <b id="m-bpm">-</b></span>
      <span>coups <b id="m-hits">-</b></span>
      <span>paroxysmes <b id="m-drops">-</b></span>
    </div>
  </div>

  <div class="card">
    <h2>Prereglage</h2>
    <select id="preset"></select>
    <div class="row" style="margin-top:8px">
      <input type="text" id="presetNom" maxlength="40"
             placeholder="nom de votre reglage">
      <button class="ghost" id="presetSave">Enregistrer</button>
    </div>
    <button class="ghost" id="presetDel" style="margin-top:6px" disabled>
      Effacer ce reglage</button>
    <p class="hint">Ceux qu'un prereglage ne mentionne pas reviennent a leur
      valeur d'usine : deux prereglages enchaines ne se melangent donc pas.<br>
      <b>Enregistrer</b> garde d'un coup tous les curseurs, toutes les listes,
      les couleurs et le titre — tout sauf la definition, la cadence et le
      morceau. Ils sont ecrits dans <code>out/studio/mes-reglages.json</code>
      et vous les retrouverez a la prochaine ouverture.</p>
  </div>

  <div class="card">
    <h2>Couleur du trait</h2>
    <select id="palette">
      <option value="vert">vert (par defaut)</option>
      <option value="orange">orange</option>
      <option value="bleu">bleu</option>
      <option value="bleu-fond">bleu sur fond bleu</option>
      <option value="perso">couleur libre&hellip;</option>
    </select>
    <div id="traitwrap" hidden>
      <label for="trait">teinte</label>
      <input type="color" id="trait" value="#3dff72">
    </div>
  </div>

  <div class="card">
    <h2>Fond</h2>
    <label for="bg">texture</label>
    <select id="bg">
      <option value="noir">noir (aucun)</option>
      <option value="uni">couleur unie</option>
      <option value="grille">grille d'oscilloscope</option>
      <option value="points">trame de points</option>
      <option value="scan">lignes de tube</option>
      <option value="degrade">degrade (sombre au centre)</option>
      <option value="bruit">grain</option>
    </select>
    <div id="bgopts" hidden>
      <label for="bgColor">couleur du fond</label>
      <input type="color" id="bgColor" value="#123a5c">
      <label for="bgStrength">intensite &mdash; <span id="v-str">1.00</span></label>
      <input type="range" id="bgStrength" min="0" max="2" step="0.05" value="1">
      <label for="bgClear">degagement derriere la machine &mdash; <span id="v-clr">0.55</span></label>
      <input type="range" id="bgClear" min="0" max="1" step="0.05" value="0.55">
      <p class="hint">Le trait est additif : un fond clair mange son contraste.
        Le degagement creuse la texture derriere la machine pour qu'elle
        ressorte quand meme.</p>
    </div>
  </div>

  <div class="card">
    <h2>Image ou video de fond</h2>
    <div class="drop" id="bdrop">
      <b id="bdname">Deposer une image ou une video</b>
      jpg, png, mp4, mov&hellip; ou cliquer
    </div>
    <input type="file" id="bdfile" accept="image/*,video/*" hidden>
    <div id="bdopts" hidden>
      <label for="bdStrength">presence du fond &mdash; <span id="v-bds">0.78</span></label>
      <input type="range" id="bdStrength" min="0" max="1.6" step="0.02" value="0.78">
      <label for="bdClear">degagement derriere la machine &mdash; <span id="v-bdc">0.40</span></label>
      <input type="range" id="bdClear" min="0" max="1" step="0.05" value="0.40">
      <label for="screenDim">opacite de la dalle &mdash; <span id="v-sd">0.40</span></label>
      <input type="range" id="screenDim" min="0" max="1" step="0.05" value="0.40">
      <label for="bdSharp">nettete du fond &mdash; <span id="v-bdq">0.37</span></label>
      <input type="range" id="bdSharp" min="0" max="1" step="0.01" value="0.37">
      <label for="travel">travelling &mdash; <span id="v-tv">0 %</span> de l'image parcourue</label>
      <input type="range" id="travel" min="0" max="0.5" step="0.01" value="0">
      <label for="travelMode">sens du travelling</label>
      <select id="travelMode"></select>
      <button class="ghost" id="bdclear" style="margin-top:8px">retirer le fond</button>
      <p class="hint">Sur une video, l'apercu montre l'image de l'instant
        regarde ; le rendu, lui, la joue en entier (et la boucle si elle est
        plus courte que le morceau).<br>
        Le travelling s'etale sur tout le morceau : l'image est chargee plus
        grande que l'ecran et on s'y deplace lentement. Quelques pour cent
        suffisent a lui oter son air de decor colle derriere la machine.</p>
    </div>
  </div>

  <div class="card">
    <h2>Trait</h2>
    <label for="taille">taille de la machine &mdash; <span id="v-ta">1.00</span></label>
    <input type="range" id="taille" min="0.35" max="1.3" step="0.01" value="1">
    <label for="presence">presence de la machine &mdash; <span id="v-pr">1.00</span></label>
    <input type="range" id="presence" min="0.1" max="1.6" step="0.02" value="1">
    <label for="nettete">finesse du trait &mdash; <span id="v-net">1.00</span></label>
    <input type="range" id="nettete" min="0.6" max="1.7" step="0.05" value="1">
    <label for="split">dedoublement du trait sur les gros subs &mdash; <span id="v-split">1.00</span></label>
    <input type="range" id="split" min="0" max="2.5" step="0.05" value="1">
    <select id="splitOn" class="inst"></select>
    <label for="splitCount">dedoublements dans la video &mdash; au plus <span id="v-sc">3</span></label>
    <input type="range" id="splitCount" min="0" max="12" step="1" value="3">
    <label for="wobble">ondulation du trace &mdash; <span id="v-wob">0.00</span></label>
    <input type="range" id="wobble" min="0" max="1.5" step="0.05" value="0">
    <label for="splitPx">ecart des copies &mdash; <span id="v-spx">11</span> px</label>
    <input type="range" id="splitPx" min="0" max="30" step="1" value="11">
    <label for="snare">eclair jaune sur la caisse claire &mdash; <span id="v-sn">1.00</span></label>
    <input type="range" id="snare" min="0" max="2" step="0.05" value="1">
    <label for="wave">amplitude de la courbe &mdash; <span id="v-wv">1.10</span></label>
    <input type="range" id="wave" min="0" max="3" step="0.05" value="1.10">
    <label for="wavePunch">gonflement sur le temps fort &mdash; <span id="v-wp">0.85</span></label>
    <input type="range" id="wavePunch" min="0" max="2.5" step="0.05" value="0.85">
    <label for="waveSmooth">lissage de la courbe &mdash; <span id="v-ws">56</span></label>
    <input type="range" id="waveSmooth" min="8" max="240" step="4" value="56">
    <label for="trail">trainee de la bande &mdash; <span id="v-trail">1.00</span></label>
    <input type="range" id="trail" min="0" max="2.5" step="0.05" value="1">
    <label for="glitch">glitchs sur les paroxysmes &mdash; <span id="v-gl">1.00</span></label>
    <input type="range" id="glitch" min="0" max="2" step="0.05" value="1">
    <label for="stepDiv">vitesse des pas du sequenceur</label>
    <select id="stepDiv">
      <option value="1">lente &mdash; une case par temps</option>
      <option value="2" selected>moyenne &mdash; une case par demi-temps</option>
      <option value="4">rapide &mdash; une case par quart de temps</option>
    </select>
    <label for="title">titre affiche sur la dalle</label>
    <input type="text" id="title" maxlength="22" placeholder="nom du fichier">
    <p class="hint">Sur les coups vraiment appuyes — et seulement ceux-la —
      les trois couches de couleur du trait se separent, puis se recollent
      quand le coup retombe. Le fond, lui, ne bouge pas.<br>
      Le nombre est un plafond, pas une consigne : deux dedoublements ne
      peuvent pas tomber a moins de 25 secondes l'un de l'autre, et une video
      courte en recoit donc moins. Par defaut ils ne partent que sur la grosse
      caisse — la basse, souvent posee sur le meme temps que la caisse claire,
      donnait l'impression qu'ils se declenchaient sur elle.</p>
  </div>

  <div class="card">
    <h2>Reactions au son</h2>
    <p class="hint" style="margin-top:0">Chaque reaction se cale sur ce que
      vous voulez. A zero, elle est eteinte.<br>
      Les listes proposent quatre sortes de declencheurs.
      <b>Instruments</b> : la batterie est reconnue a l'analyse, « caisse
      claire » veut donc vraiment dire caisse claire.
      <b>Bandes de frequences</b> : une hauteur et non un instrument — elles
      attrapent aussi ce qui n'est pas percussif, une nappe qui monte, une
      voix, un souffle de cymbale.
      <b>Hasard</b> : tire au sort, mais pose sur la grille du morceau, donc
      jamais a contretemps.
      <b>Un coup sur deux</b> : deux effets poses l'un sur « 1 sur 2 » et
      l'autre sur « l'autre sur 2 » ne peuvent jamais partir ensemble — c'est
      la reponse quand tout tombe en meme temps.</p>

    <label for="punch">zoom d'impact &mdash; <span id="v-pu">0.03</span></label>
    <input type="range" id="punch" min="0" max="0.25" step="0.005" value="0.032">
    <select id="punchOn" class="inst"></select>

    <label for="shake">secousse de l'image &mdash; <span id="v-sh">0.00</span></label>
    <input type="range" id="shake" min="0" max="2" step="0.05" value="0">
    <select id="shakeOn" class="inst"></select>

    <label for="parts">etincelles ejectees &mdash; <span id="v-pa">0.00</span></label>
    <input type="range" id="parts" min="0" max="3" step="0.05" value="0">
    <select id="partsOn" class="inst"></select>
    <label for="partsN">nombre par coup &mdash; <span id="v-pan">14</span></label>
    <input type="range" id="partsN" min="2" max="180" step="1" value="4">
    <div class="row">
      <div>
        <label for="partsSpeed">vitesse &mdash; <span id="v-pas">1.00</span></label>
        <input type="range" id="partsSpeed" min="0.2" max="2.5" step="0.05" value="1">
      </div>
      <div>
        <label for="partsLife">duree &mdash; <span id="v-pal">0.55</span> s</label>
        <input type="range" id="partsLife" min="0.15" max="1.5" step="0.05" value="0.55">
      </div>
    </div>

    <label for="ring">onde de choc &mdash; <span id="v-ri">0.00</span></label>
    <input type="range" id="ring" min="0" max="3" step="0.05" value="0">
    <select id="ringOn" class="inst"></select>

    <label for="gridPulse">pulsation de la grille &mdash; <span id="v-gp">0.00</span></label>
    <input type="range" id="gridPulse" min="0" max="3" step="0.05" value="0">
    <select id="gridOn" class="inst"></select>

    <label for="bgFlash">eclat du fond &mdash; <span id="v-bf">0.00</span></label>
    <input type="range" id="bgFlash" min="0" max="2" step="0.05" value="0">
    <select id="flashOn" class="inst"></select>
    <p class="hint">Pour une vraie explosion d'etincelles, monter le nombre,
      la vitesse et la duree ensemble. Au-dela de quelques centaines de
      braises, le trace de chacune est ecourte pour tenir un budget de points
      par image : c'est ce qui permet d'en lancer des dizaines de milliers
      sans que le rendu s'effondre.</p>
  </div>

  <div class="card">
    <h2>Avaries d'image</h2>
    <p class="hint" style="margin-top:0">Les memes pannes que sur les
      paroxysmes, mais declenchees par ce qui est joue. Elles s'appliquent a
      l'image finie, juste avant la deformation du tube : d'ou leur air de
      signal casse plutot que d'effet dessine.</p>

    <label for="tranches">bandes arrachees &mdash; <span id="v-tr">0.00</span></label>
    <input type="range" id="tranches" min="0" max="2.5" step="0.05" value="0">
    <select id="tranchesOn" class="inst"></select>

    <label for="blocs">blocs recopies &mdash; <span id="v-bl">0.00</span></label>
    <input type="range" id="blocs" min="0" max="2.5" step="0.05" value="0">
    <select id="blocsOn" class="inst"></select>

    <label for="roll">decrochage vertical &mdash; <span id="v-ro">0.00</span></label>
    <input type="range" id="roll" min="0" max="2" step="0.05" value="0">
    <select id="rollOn" class="inst"></select>

    <label for="ghost">image fantome &mdash; <span id="v-gh">0.00</span></label>
    <input type="range" id="ghost" min="0" max="2.5" step="0.05" value="0">
    <select id="ghostOn" class="inst"></select>

    <label for="invert">negatif du trait &mdash; <span id="v-in">0.00</span></label>
    <input type="range" id="invert" min="0" max="2.5" step="0.05" value="0">
    <select id="invertOn" class="inst"></select>

    <label for="stut">begaiement &mdash; pendant <span id="v-st">0.00</span> s</label>
    <input type="range" id="stut" min="0" max="0.6" step="0.01" value="0">
    <select id="stutOn" class="inst"></select>
    <label for="stutLoop">boucle rejouee &mdash; <span id="v-sl">0.05</span> s</label>
    <input type="range" id="stutLoop" min="0.01" max="0.3" step="0.01" value="0.05">

    <label for="miroir">miroir &mdash; <span id="v-mi">0.00</span></label>
    <input type="range" id="miroir" min="0" max="2" step="0.05" value="0">
    <select id="miroirOn" class="inst"></select>

    <label for="ondul">ondulation liquide &mdash; <span id="v-on">0.00</span></label>
    <input type="range" id="ondul" min="0" max="2.5" step="0.05" value="0">
    <select id="ondulOn" class="inst"></select>

    <label for="mosaic">mosaique &mdash; <span id="v-mo">0.00</span></label>
    <input type="range" id="mosaic" min="0" max="2" step="0.05" value="0">
    <select id="mosaicOn" class="inst"></select>

    <label for="kaleido">kaleidoscope &mdash; <span id="v-ka">0.00</span></label>
    <input type="range" id="kaleido" min="0" max="2" step="0.05" value="0">
    <select id="kaleidoOn" class="inst"></select>

    <label for="cisaille">cisaillement &mdash; <span id="v-ci">0.00</span></label>
    <input type="range" id="cisaille" min="0" max="2.5" step="0.05" value="0">
    <select id="cisailleOn" class="inst"></select>

    <label for="coupure">coupure franche &mdash; <span id="v-co">0.00</span></label>
    <input type="range" id="coupure" min="0" max="2.5" step="0.05" value="0">
    <select id="coupureOn" class="inst"></select>

    <label for="tapestop">patinage de bande &mdash; <span id="v-ta">0.00</span> s</label>
    <input type="range" id="tapestop" min="0" max="0.8" step="0.02" value="0">
    <select id="tapestopOn" class="inst"></select>

    <label for="scramble">tranches de temps brassees &mdash; <span id="v-sc2">0.00</span></label>
    <input type="range" id="scramble" min="0" max="1" step="0.05" value="0">
    <label for="scrLen">longueur d'une tranche &mdash; <span id="v-scl">0.14</span> s</label>
    <input type="range" id="scrLen" min="0.04" max="0.6" step="0.01" value="0.14">
    <p class="hint">Le <b>begaiement</b> decroche l'image du son : elle rejoue
      en boucle un bout tres court pris a l'instant du coup. Une boucle plus
      courte qu'une image donne un gel pur ; deux ou trois images donnent un
      sursaut repete, bien plus visible.<br>
      Les <b>tranches brassees</b> ne dependent d'aucun instrument : elles
      decoupent le temps en blocs reguliers et les rejouent dans le desordre,
      pendant que le son continue tout droit. Des tranches courtes hachent,
      des longues desorientent.</p>
  </div>

  <div class="card">
    <h2>Echo, couleurs, spectrogramme</h2>
    <label for="echo">echo d'images &mdash; <span id="v-ec">0.00</span></label>
    <input type="range" id="echo" min="0" max="0.85" step="0.05" value="0">
    <div class="row">
      <div>
        <label for="echoN">nombre &mdash; <span id="v-ecn">3</span></label>
        <input type="range" id="echoN" min="1" max="6" step="1" value="3">
      </div>
      <div>
        <label for="echoDelay">ecart &mdash; <span id="v-ecd">0.045</span> s</label>
        <input type="range" id="echoDelay" min="0.02" max="0.2" step="0.005" value="0.045">
      </div>
    </div>

    <label for="couleurs">couleurs par instrument &mdash; <span id="v-cl">0.00</span></label>
    <input type="range" id="couleurs" min="0" max="2.5" step="0.05" value="0">

    <label for="spectro">spectrogramme sur la dalle &mdash; <span id="v-sp">0.00</span></label>
    <input type="range" id="spectro" min="0" max="2" step="0.05" value="0">
    <p class="hint">L'<b>echo</b> redessine la machine telle qu'elle etait il y a
      quelques centiemes, de plus en plus pale. Les <b>couleurs par instrument</b>
      donnent au trait la teinte du dernier coup : rouge la grosse caisse, jaune
      la caisse claire, cyan le charley, violet la basse. Le <b>spectrogramme</b>
      deroule les trois dernieres secondes du morceau sur la dalle, une ligne
      par bande de frequences — baissez l'amplitude de la courbe pour bien le
      voir.</p>
  </div>

  <div class="card">
    <h2>Texture &mdash; trip hop, lo-fi</h2>
    <p class="hint" style="margin-top:0">Celles-ci ne frappent sur rien : elles
      sont la du debut a la fin. C'est ce qui separe un accident d'une matiere
      — un grain de pellicule qui n'apparaitrait que sur la caisse claire ne
      ressemblerait a rien.</p>

    <label for="cadence">cadence tenue &mdash; <span id="v-ca">fluide</span></label>
    <input type="range" id="cadence" min="0" max="5" step="1" value="0">

    <label for="haloDoux">halo laiteux &mdash; <span id="v-hd">0.00</span></label>
    <input type="range" id="haloDoux" min="0" max="2" step="0.05" value="0">

    <label for="poussiere">poussiere et rayures &mdash; <span id="v-po">0.00</span></label>
    <input type="range" id="poussiere" min="0" max="2.5" step="0.05" value="0">

    <label for="flottement">flottement de bande &mdash; <span id="v-fl">0.00</span></label>
    <input type="range" id="flottement" min="0" max="2.5" step="0.05" value="0">
    <p class="hint">La <b>cadence tenue</b> garde chaque image deux, trois ou
      quatre fois : la video passe a 15, 10 ou 7 images par seconde sans rien
      ralentir. C'est le geste qui donne son air d'animation a un clip lo-fi.
      Le <b>halo laiteux</b> releve les noirs et etale la lumiere, a l'oppose du
      contraste franc de l'oscilloscope.</p>
  </div>

  <div class="card">
    <h2>Rendu</h2>
    <div class="row">
      <div><label for="start">depart (s)</label><input type="number" id="start" value="0" min="0" step="0.1"></div>
      <div><label for="dur">duree (s)</label><input type="number" id="dur" placeholder="tout" min="1" step="1"></div>
    </div>
    <div class="row">
      <div><label for="size">definition</label>
        <select id="size">
          <option value="1920x1080">1080p</option>
          <option value="3840x2160">4K</option>
          <option value="1280x720">720p</option>
          <option value="1080x1080">carre 1080</option>
          <option value="1080x1920">vertical 1080</option>
        </select></div>
      <div><label for="fps">images/s</label>
        <select id="fps"><option>30</option><option>60</option><option>24</option></select></div>
    </div>
    <label for="quality">qualite du fichier</label>
    <select id="quality"></select>
    <label><input type="checkbox" id="curve" checked style="width:auto;margin-right:6px">
      bombe de l'ecran cathodique</label>
    <button id="go" disabled style="margin-top:12px">Lancer le rendu</button>
    <div id="prog" hidden>
      <div class="bar"><i id="pbar"></i></div>
      <div class="hint" id="ptext"></div>
    </div>
    <div id="done" hidden style="margin-top:10px">
      <a class="dl" id="dl">Telecharger</a>
      <p class="hint" id="donepath"></p>
    </div>
  </div>
 </div>

 <div>
  <div class="card">
    <h2>Apercu</h2>
    <img id="shot" alt="apercu">
    <video id="clip" hidden loop controls playsinline></video>
    <div id="shoterr"></div>
    <label for="scrub">instant du morceau &mdash; <span id="v-t">0.0 s</span></label>
    <input type="range" id="scrub" min="0" max="100" step="0.1" value="0" disabled>
    <div class="row" style="margin-top:8px">
      <button class="ghost" id="toSplit">aller au prochain dedoublement</button>
      <button class="ghost" id="toDrop">aller au prochain paroxysme</button>
      <button class="ghost" id="hi">chercher un kick</button>
    </div>
    <div class="row" style="margin-top:8px">
      <button id="lire" disabled>Lire en mouvement</button>
      <div><label for="clipDur">duree</label>
        <select id="clipDur">
          <option value="2">2 s</option>
          <option value="4" selected>4 s</option>
          <option value="8">8 s</option>
        </select></div>
    </div>
    <div id="clipprog" hidden>
      <div class="bar"><i id="cbar"></i></div>
      <div class="hint" id="ctext"></div>
    </div>
    <p class="hint">L'apercu est une vraie image du rendu, calculee avec vos
      reglages : ce que vous voyez ici est ce que vous obtiendrez.<br>
      <b>Lire en mouvement</b> calcule pour de bon quelques secondes a partir
      de l'instant regarde, avec le son, et les joue en boucle. C'est la seule
      facon de juger ce qui bouge — begaiement, travelling, etincelles,
      spectrogramme. La lecture est en 15 images par seconde pour ne pas faire
      attendre : le rendu final, lui, en fera 30 ou 60.</p>
  </div>
 </div>
</main>

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
  try {
    const j = await deposer('/upload', f, 'du morceau');
    track = j.track; drops = j.drops || []; duration = j.duration;
    // les frappes du morceau : c'est d'elles que sortent les frequences
    FRAPPES = {frappes: j.frappes || {}, drops: j.drops || [],
               duree: j.duree || j.duration || 0, bpm: j.bpm || 0};
    majFrequences();
    $('#m-dur').textContent = fmt(j.duration);
    $('#m-bpm').textContent = j.bpm.toFixed(1) + ' BPM';
    $('#m-hits').textContent = j.hits;
    $('#m-drops').textContent = drops.length;
    $('#trackmeta').hidden = false;
    drop.innerHTML = '<b>' + j.name + '</b>cliquer pour changer de morceau';
    $('#title').placeholder = j.name.replace(/\.[^.]+$/, '');
    $('#scrub').max = Math.max(1, duration - 1); $('#scrub').disabled = false;
    // on ouvre sur un moment ordinaire, pas sur un paroxysme : l'image
    // glitchee ne dit rien des couleurs. Le bouton dedie y emmene.
    $('#scrub').value = (duration * 0.35).toFixed(2);
    $('#v-t').textContent = (duration * 0.35).toFixed(1) + ' s';
    $('#dur').placeholder = 'tout';
    $('#go').disabled = false;
    $('#lire').disabled = false;
    setStatus(j.name + ' — ' + j.bpm.toFixed(1) + ' BPM, ' + drops.length +
      ' paroxysme(s) : les glitchs tomberont la.');
    shot();
  } catch (e) { setStatus('echec : ' + e.message, true); }
}

/* ---------- apercu ---------- */
function params() {
  const p = new URLSearchParams({
    track, t: $('#scrub').value,
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
    wavePunch: $('#wavePunch').value, backdrop,
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
    echo: $('#echo').value, echoN: $('#echoN').value,
    echoDelay: $('#echoDelay').value, couleurs: $('#couleurs').value,
    spectro: $('#spectro').value,
    cadence: $('#cadence').value, poussiere: $('#poussiere').value,
    flottement: $('#flottement').value, haloDoux: $('#haloDoux').value,
    bdSharp: $('#bdSharp').value,
    nettete: $('#nettete').value, stepDiv: $('#stepDiv').value,
    taille: $('#taille').value, presence: $('#presence').value,
    curve: $('#curve').checked ? '1' : '0', w: 960, h: 540,
  });
  return p;
}
let pending = null, PRESETS = {}, USINE = {}, AIDE = {}, COMPTE = {};
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
    if (eteint) {
      txt = 'eteint';
    } else if (!FRAPPES) {
      txt = 'deposez un morceau pour connaitre la frequence';
    } else if (Array.isArray(genre)) {
      // un effet cable sur des familles fixes, sans selecteur
      const n = genre.reduce((a, f) => a + ((FRAPPES.frappes || {})[f] || 0), 0);
      txt = '~ ' + n + ' fois dans le morceau' + parMinute(n, duree)
          + '  (' + genre.join(' et ') + ')';
    } else if (genre === 'instrument') {
      const sel = $('#' + id + 'On');
      const fam = sel ? sel.value : 'grosse caisse';
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
      txt = '~ ' + n + ' fois dans le morceau  (les montees du morceau)';
    } else if (genre === 'tranche') {
      const blocs = Math.floor(duree / Math.max(0.04, +$('#scrLen').value));
      const part = +$('#scramble').value;
      txt = '~ ' + Math.round(blocs * part) + ' blocs brasses sur ' + blocs;
    } else {
      txt = 'en continu, du debut a la fin';
    }
    cible.textContent = txt;
  }
}
function parMinute(n, duree) {
  if (!duree || n < 2) return '';
  return ', soit ' + (n / (duree / 60)).toFixed(0) + ' par minute';
}
function shot() {
  if (!track) return;
  rendreLImage();
  clearTimeout(pending);
  pending = setTimeout(() => {           // on ne recalcule pas a chaque pixel
    const n = ++shotSeq;
    // la premiere image d'un morceau demande quelques secondes (le moteur
    // depouille tout le son) : on le montre, sinon l'apercu a l'air casse.
    $('#shot').classList.add('calcul');
    // On passe par fetch plutot que par img.src : quand le serveur refuse,
    // une balise <img> ne donne qu'une image cassee, sans dire pourquoi.
    fetch('/still?' + params().toString()).then(async r => {
      if (n !== shotSeq) return;             // un reglage a bouge entre-temps
      if (r.status === 409) return;          // apercu abandonne, un autre arrive
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
      $('#shot').classList.remove('calcul');
      $('#shoterr').classList.remove('on');
    }).catch(e => {
      if (n !== shotSeq) return;
      $('#shot').classList.remove('calcul');
      // la version est rappelee ici : un message rapporte sans elle ne dit pas
      // si la correction correspondante est deja installee ou non
      $('#shoterr').textContent = "L'apercu n'a pas pu etre calcule : "
        + e.message + "  [" + ($('#ver').textContent || "version inconnue") + "]";
      $('#shoterr').classList.add('on');
    });
  }, 90);
}

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
bind('#mosaic','#v-mo',2); bind('#scramble','#v-sc2',2); bind('#scrLen','#v-scl',2);
bind('#bdSharp','#v-bdq',2); bind('#nettete','#v-net',2);
bind('#taille','#v-ta',2); bind('#presence','#v-pr',2);
bind('#kaleido','#v-ka',2); bind('#cisaille','#v-ci',2);
bind('#coupure','#v-co',2); bind('#tapestop','#v-ta',2);
bind('#haloDoux','#v-hd',2); bind('#poussiere','#v-po',2);
bind('#flottement','#v-fl',2);
bind('#echo','#v-ec',2); bind('#echoN','#v-ecn',0);
bind('#echoDelay','#v-ecd',3); bind('#couleurs','#v-cl',2);
bind('#spectro','#v-sp',2);
$('#cadence').oninput = e => {
  const n = +e.target.value;
  $('#v-ca').textContent = n < 2 ? 'fluide' : Math.round(30 / n) + ' i/s';
  shot();
};
$('#travel').oninput = e => {
  $('#v-tv').textContent = Math.round(+e.target.value * 100) + ' %'; shot(); };
for (const id of ['#travelMode','#punchOn','#shakeOn','#partsOn','#ringOn',
                  '#gridOn','#flashOn','#splitOn','#tranchesOn','#blocsOn',
                  '#rollOn','#ghostOn','#invertOn','#stutOn','#miroirOn',
                  '#ondulOn','#mosaicOn','#kaleidoOn','#cisailleOn',
                  '#coupureOn','#tapestopOn','#stepDiv'])
  $(id).onchange = () => { majFrequences(); shot(); };
// la qualite ne change rien a l'apercu : elle ne touche que l'encodage
$('#quality').onchange = majFrequences;

/* ---- fond : image ou video ---- */
let backdrop = '';
const bdrop = $('#bdrop'), bdfile = $('#bdfile');
bdrop.onclick = () => bdfile.click();
bdrop.ondragover = e => { e.preventDefault(); bdrop.classList.add('over'); };
bdrop.ondragleave = () => bdrop.classList.remove('over');
bdrop.ondrop = e => { e.preventDefault(); bdrop.classList.remove('over');
                      if (e.dataTransfer.files[0]) sendBackdrop(e.dataTransfer.files[0]); };
bdfile.onchange = () => bdfile.files[0] && sendBackdrop(bdfile.files[0]);
$('#bdclear').onclick = () => { backdrop = ''; $('#bdopts').hidden = true;
  $('#bdname').textContent = 'Deposer une image ou une video'; shot(); };

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
      else if (!j) mauvais(new Error('le studio a repondu ' + x.status
                                     + ' sans explication'));
      else bon(j);
    };
    x.onerror = () => mauvais(new Error(
      'le studio n\'a pas repondu pendant l\'envoi (' + poids(f.size) + '). '
      + 'Le message exact est ecrit dans la fenetre noire du studio.'));
    x.onabort = () => mauvais(new Error('envoi interrompu'));
    x.send(f);
  });
}

async function sendBackdrop(f) {
  try {
    const j = await deposer('/backdrop', f, 'du fond');
    backdrop = j.name;
    $('#bdname').textContent = j.name + (j.video ? ' (video)' : '');
    $('#bdopts').hidden = false;
    setStatus('fond en place');
    shot();
  } catch (e) { setStatus('fond refuse : ' + e.message, true); }
}
$('#title').oninput  = shot;
$('#bgStrength').oninput = e => { $('#v-str').textContent = (+e.target.value).toFixed(2); shot(); };
$('#bgClear').oninput   = e => { $('#v-clr').textContent = (+e.target.value).toFixed(2); shot(); };
$('#scrub').oninput     = e => { $('#v-t').textContent = (+e.target.value).toFixed(1) + ' s'; shot(); };

/* Le dedoublement ne tombe que sur les 2 ou 3 plus gros coups de tout le
   morceau : sans ce bouton on peut chercher longtemps avant d'en voir un. */
$('#toSplit').onclick = async () => {
  if (!track) return;
  const j = await (await fetch('/splits?track=' + track +
                               '&count=' + $('#splitCount').value +
                               '&on=' + encodeURIComponent($('#splitOn').value))).json();
  const ts = j.times || [];
  if (!ts.length) return setStatus('aucun dedoublement sur ce morceau');
  const t = +$('#scrub').value;
  const next = ts.find(d => d > t + 0.2) ?? ts[0];
  $('#scrub').value = next + 0.08;
  $('#v-t').textContent = (next + 0.08).toFixed(1) + ' s';
  setStatus('dedoublement a ' + next.toFixed(1) + ' s  (tous : ' +
            ts.map(x => x.toFixed(1) + ' s').join(', ') + ')');
  shot();
};

$('#toDrop').onclick = () => {
  if (!drops.length) return setStatus('aucun paroxysme detecte sur ce morceau');
  const t = +$('#scrub').value;
  const next = drops.find(d => d > t + 0.2) ?? drops[0];
  $('#scrub').value = next + 0.05;      // juste apres, dans la rafale
  $('#v-t').textContent = (next + 0.05).toFixed(1) + ' s';
  setStatus('paroxysme a ' + next.toFixed(1) + ' s');
  shot();
};
$('#hi').onclick = () => {
  $('#scrub').value = (Math.random() * Math.max(1, duration - 2)).toFixed(2);
  $('#v-t').textContent = (+$('#scrub').value).toFixed(1) + ' s';
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
  const body = Object.fromEntries(params());
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
  clearTimeout(clipTimer);
  // on rend la main a l'image fixe pendant le calcul : laisser l'ancien
  // extrait tourner ferait croire que rien ne se passe
  rendreLImage();
  $('#lire').disabled = true;
  $('#clipprog').hidden = false;
  $('#cbar').style.width = '0%';
  $('#ctext').textContent = 'preparation\u2026';
  try {
    const r = await fetch('/render', {method: 'POST',
                                      body: JSON.stringify(reglagesDuClip())});
    const j = await r.json();
    if (j.error) throw new Error(j.error);
    suivreClip(j.id);
  } catch (e) {
    setStatus('lecture impossible : ' + e.message, true);
    $('#lire').disabled = false; $('#clipprog').hidden = true;
  }
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
      return;
    }
    if (j.state === 'fini') {
      const v = $('#clip');
      v.src = '/download?inline=1&id=' + id;
      v.hidden = false; $('#shot').hidden = true;
      $('#clipprog').hidden = true; $('#lire').disabled = false;
      // le son demande parfois un geste de l'utilisateur : a defaut on joue
      // sans, plutot que de laisser une image arretee
      v.play().catch(() => { v.muted = true; v.play().catch(() => {}); });
      setStatus('lecture en boucle — bougez un reglage pour revenir a l\'image');
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
  $('#pbar').style.width = '0%'; $('#ptext').textContent = 'preparation…';
  const r = await fetch('/render', {method:'POST', body: JSON.stringify(body)});
  const j = await r.json();
  if (j.error) { setStatus('echec : ' + j.error, true); $('#go').disabled = false; return; }
  watch(j.id);
};

function watch(id) {
  clearInterval(jobTimer);
  jobTimer = setInterval(async () => {
    const j = await (await fetch('/job?id=' + id)).json();
    if (j.state === 'erreur') {
      clearInterval(jobTimer); $('#prog').hidden = true;
      setStatus('echec du rendu : ' + j.error, true); $('#go').disabled = false;
      return;
    }
    if (j.state === 'fini') {
      clearInterval(jobTimer);
      $('#pbar').style.width = '100%';
      $('#ptext').textContent = 'termine';
      $('#dl').href = '/download?id=' + id;
      $('#dl').setAttribute('download', j.name);
      $('#donepath').textContent = 'ecrit dans out/studio/' + j.name +
        ' (' + (j.size / 1048576).toFixed(1) + ' Mo)';
      $('#done').hidden = false; $('#go').disabled = false;
      setStatus('rendu termine');
      return;
    }
    const pc = j.total ? j.done / j.total * 100 : 0;
    $('#pbar').style.width = pc.toFixed(1) + '%';
    $('#ptext').textContent = j.state === 'rendu'
      ? j.done + '/' + j.total + ' images — encore ' + fmt(j.eta)
      : j.state + '…';
  }, 700);
}

/* La version du code effectivement charge : un studio laisse ouvert continue
   de servir l'ancien moteur apres un git pull, et on cherche longtemps
   pourquoi une nouveaute « n'est pas la ». */
fetch('/config').then(r => r.json())
  .then(c => {
    if (c.version) $('#ver').textContent = c.version;
    // Les instruments et les sens de travelling viennent du moteur : la page
    // n'en garde pas sa propre copie, qui finirait par diverger.
    const remplir = (sel, liste, choisi) => {
      $(sel).innerHTML = liste.map(
        v => '<option value="' + v + '"' + (v === choisi ? ' selected' : '')
             + '>' + (sel === '#travelMode' ? v : 'sur : ' + v) + '</option>'
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
               + (v === choisi ? ' selected' : '') + '>sur : ' + echap(v)
               + '</option>').join('') + '</optgroup>').join('');
    };
    for (const [sel, def] of [['#splitOn', 'grosse caisse'],
                              ['#punchOn', 'grosse caisse'],
                              ['#shakeOn', 'grosse caisse'],
                              ['#partsOn', 'caisse claire'],
                              ['#ringOn', 'grosse caisse'],
                              ['#gridOn', 'grosse caisse'],
                              ['#flashOn', 'caisse claire'],
                              ['#tranchesOn', 'caisse claire'],
                              ['#blocsOn', 'caisse claire'],
                              ['#rollOn', 'grosse caisse'],
                              ['#ghostOn', 'caisse claire'],
                              ['#invertOn', 'grosse caisse'],
                              ['#stutOn', 'charley'],
                              ['#miroirOn', 'caisse claire'],
                              ['#ondulOn', 'basse'],
                              ['#mosaicOn', 'caisse claire'],
                              ['#kaleidoOn', 'caisse claire'],
                              ['#cisailleOn', 'caisse claire'],
                              ['#coupureOn', 'grosse caisse'],
                              ['#tapestopOn', 'grosse caisse']])
        remplirGroupes(sel, c.declencheurs || [], def);
    remplir('#travelMode', c.travellings || [], 'avant');
    // chaque qualite dit en clair ce qu'elle coute et ce qu'elle rend
    QUALITES = c.qualites || {};
    $('#quality').innerHTML = Object.keys(QUALITES).map(
      k => '<option value="' + k + '">' + k + '</option>').join('');

    /* ---- une phrase sous chaque reglage, et sa frequence ---- */
    AIDE = c.aide || {}; COMPTE = c.compte || {};
    for (const [id, phrase] of Object.entries(AIDE)) {
      const el = $('#' + id);
      if (!el) continue;
      // Le texte se pose apres le selecteur d'instrument quand celui-ci suit
      // immediatement le curseur, pour que le bloc « effet + instrument +
      // explication » reste solidaire. Exiger le voisinage direct evite de
      // rattacher un selecteur qui se trouve plus bas dans la meme carte.
      // deux selecteurs ne portent pas le nom de leur curseur suivi de « On »
      const AUTRE = {gridPulse: 'gridOn', bgFlash: 'flashOn'};
      const inst = $('#' + (AUTRE[id] || id + 'On'));
      const apres = (inst && el.nextElementSibling === inst) ? inst : el;
      const d = document.createElement('div');
      d.className = 'aide';
      d.innerHTML = phrase + '<b class="freq" id="f-' + id + '"></b>';
      apres.parentNode.insertBefore(d, apres.nextSibling);
    }
    majFrequences();

    /* ---- prereglages : ils reposent tous les curseurs d'un coup ---- */
    PRESETS = c.presets || {};
    MES = c.mes || {};
    USINE = {};                       // les valeurs d'usine, pour y revenir
    for (const el of document.querySelectorAll('input[type=range], select'))
      if (el.id) USINE[el.id] = el.value;
    listeDesPrereglages();
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
                         + groupe("Mes reglages", Object.keys(MES).sort());
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
  if (!nom) { setStatus('donnez un nom a ce reglage', true); return; }
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
  } catch (e) { setStatus('pas efface : ' + e.message, true); }
};

/* ---------- divers ---------- */
function fmt(s) {
  s = Math.max(0, Math.round(s));
  return s >= 60 ? Math.floor(s / 60) + ' min ' + String(s % 60).padStart(2,'0') + ' s'
                 : s + ' s';
}
function setStatus(t, bad) {
  const el = $('#status'); el.textContent = t; el.className = bad ? 'err' : '';
}
</script>
</body></html>
"""


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


def main():
    ap = argparse.ArgumentParser(description="Studio local Omnipotard")
    ap.add_argument("--port", type=int, default=8765)
    ap.add_argument("--host", default="127.0.0.1",
                    help="127.0.0.1 par defaut : rien n'est expose au reseau")
    ap.add_argument("--no-browser", action="store_true")
    ap.add_argument("track", nargs="?", help="morceau a charger au demarrage")
    args = ap.parse_args()

    check_deps()
    os.makedirs(UPLOADS, exist_ok=True)
    if args.track:
        tid, info = STUDIO.add_track(os.path.abspath(args.track),
                                     safe_name(args.track))
        print("morceau pre-charge : %s (%.1f BPM, %d paroxysmes)"
              % (args.track, info["bpm"], len(info["drops"])))

    srv = ThreadingHTTPServer((args.host, args.port), Handler)
    url = "http://%s:%d" % (args.host, args.port)
    print("Studio Omnipotard  ->  %s" % url)
    print("Ctrl-C pour arreter. Les videos sont ecrites dans out/studio/.")
    if not args.no_browser:
        threading.Timer(0.6, lambda: webbrowser.open(url)).start()
    try:
        srv.serve_forever()
    except KeyboardInterrupt:
        print("\narret.")


if __name__ == "__main__":
    main()
