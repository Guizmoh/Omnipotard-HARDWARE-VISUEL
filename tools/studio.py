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
)
from omnipotard_intro import (  # noqa: E402
    BACKGROUNDS, PALETTES, hex_to_rgb, rgb_to_hex, load_backdrop, is_video,
    pick_split_times, VERSION, INSTRUMENTS, TRAVELLINGS, FAMILLES,
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
MAX_UPLOAD = 220 * 1024 * 1024          # un morceau, pas une discotheque


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
        "split_on": _dans(q.get("splitOn"), INSTRUMENTS, "grosse caisse"),
        "glitch": float(q.get("glitch", 1.0)),
        # ---- avaries d'image, declenchees par la batterie
        "tranches": float(q.get("tranches", 0.0)),
        "tranches_on": _dans(q.get("tranchesOn"), INSTRUMENTS, "caisse claire"),
        "roll": float(q.get("roll", 0.0)),
        "roll_on": _dans(q.get("rollOn"), INSTRUMENTS, "grosse caisse"),
        "ghost": float(q.get("ghost", 0.0)),
        "ghost_on": _dans(q.get("ghostOn"), INSTRUMENTS, "caisse claire"),
        "blocs": float(q.get("blocs", 0.0)),
        "blocs_on": _dans(q.get("blocsOn"), INSTRUMENTS, "caisse claire"),
        "invert": float(q.get("invert", 0.0)),
        "invert_on": _dans(q.get("invertOn"), INSTRUMENTS, "grosse caisse"),
        "stut": float(q.get("stut", 0.0)),
        "stut_on": _dans(q.get("stutOn"), INSTRUMENTS, "charley"),
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
        "travel": float(q.get("travel", 0.0)),
        "travel_mode": _dans(q.get("travelMode"), TRAVELLINGS, "avant"),
        # ---- reactions au son
        "punch": float(q.get("punch", 0.032)),
        "punch_on": _dans(q.get("punchOn"), INSTRUMENTS, "grosse caisse"),
        "shake_amp": float(q.get("shake", 0.0)),
        "shake_on": _dans(q.get("shakeOn"), INSTRUMENTS, "grosse caisse"),
        "parts": float(q.get("parts", 0.0)),
        "parts_on": _dans(q.get("partsOn"), INSTRUMENTS, "caisse claire"),
        "parts_speed": float(q.get("partsSpeed", 1.0)),
        "parts_life": float(q.get("partsLife", 0.55)),
        "ring": float(q.get("ring", 0.0)),
        "ring_on": _dans(q.get("ringOn"), INSTRUMENTS, "grosse caisse"),
        "grid_pulse": float(q.get("gridPulse", 0.0)),
        "grid_on": _dans(q.get("gridOn"), INSTRUMENTS, "grosse caisse"),
        "bg_flash": float(q.get("bgFlash", 0.0)),
        "flash_on": _dans(q.get("flashOn"), INSTRUMENTS, "caisse claire"),
    }


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
        key = (tid, w, h, bool(q.get("curve", True)))
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
                "parts", "parts_on", "parts_speed", "parts_life",
                "ring", "ring_on", "grid_pulse", "grid_on",
                "bg_flash", "flash_on",
                "tranches", "tranches_on", "roll", "roll_on",
                "ghost", "ghost_on", "blocs", "blocs_on",
                "invert", "invert_on", "stut", "stut_on")
        APART = POSE + ("wave_smooth", "backdrop", "backdrop_strength",
                        "backdrop_clear", "screen_dim", "travel", "travel_mode")
        r.set_look(palette, **{k: v for k, v in kw.items() if k not in APART})
        for k in POSE:
            setattr(r, k, kw[k])
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
                 kw["travel"], kw["travel_mode"])
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
                    scale=r.scale, screen_dim=kw["screen_dim"], seek=float(t),
                    travel=kw["travel"], travel_mode=kw["travel_mode"],
                    dur=max(tr["info"]["duration"], 1e-3))
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
        out = os.path.join(OUTDIR, "%s_%s.mp4"
                           % (os.path.splitext(tr["name"])[0][:60], jid))
        job = {"id": jid, "state": "attente", "done": 0, "total": 0, "eta": 0.0,
               "out": out, "name": os.path.basename(out), "error": None}
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

                render_video(tr["path"], job["out"], info=info,
                             width=int(q.get("width", 1920)),
                             height=int(q.get("height", 1080)),
                             fps=int(q.get("fps", 30)),
                             crf=int(q.get("crf", 20)),
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

    def handle_one_request(self):
        self._begun = False          # une connexion peut servir plusieurs fois
        return BaseHTTPRequestHandler.handle_one_request(self)

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
                    "travellings": list(TRAVELLINGS),
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
                with open(job["out"], "rb") as f:
                    body = f.read()
                return self._send(200, "video/mp4", body, {
                    "Content-Disposition": 'attachment; filename="%s"' % job["name"]})
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
            n = int(self.headers.get("Content-Length") or 0)
            if n > MAX_UPLOAD:
                return self._fail("fichier trop gros (%d Mo max)"
                                  % (MAX_UPLOAD // (1024 * 1024)), 413)
            body = self.rfile.read(n)

            if u.path == "/upload":
                os.makedirs(UPLOADS, exist_ok=True)
                name = safe_name(self.headers.get("X-Filename"))
                path = os.path.join(UPLOADS, name)
                with open(path, "wb") as f:
                    f.write(body)
                try:
                    probe_duration(path)
                except Exception:                         # noqa: BLE001
                    os.remove(path)
                    return self._fail("ffmpeg ne sait pas lire ce fichier")
                tid, info = STUDIO.add_track(path, name)
                return self._json({
                    "track": tid, "name": name,
                    "duration": info["total"], "bpm": info["bpm"],
                    "hits": info["hits"], "drops": info["drops"]})

            if u.path == "/backdrop":
                os.makedirs(FONDS, exist_ok=True)
                name = safe_name(self.headers.get("X-Filename"))
                path = os.path.join(FONDS, name)
                with open(path, "wb") as f:
                    f.write(body)
                try:                        # ffmpeg doit savoir le lire
                    load_backdrop(path, 64, 36)
                except Exception as e:      # noqa: BLE001
                    os.remove(path)
                    # on repasse le vrai motif : il nomme le format en cause,
                    # ce qu'un « ffmpeg ne sait pas lire ce fichier » taisait
                    return self._fail(e)
                return self._json({"name": name, "video": is_video(path)})

            if u.path == "/render":
                return self._json(STUDIO.start_job(json.loads(body or b"{}")))

            self._fail("page inconnue", 404)
        except Exception as e:                            # noqa: BLE001
            traceback.print_exc()
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
  select.inst{margin:2px 0 10px;font-size:11px;color:var(--dim)}
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
    <label for="split">dedoublement du trait sur les gros subs &mdash; <span id="v-split">1.00</span></label>
    <input type="range" id="split" min="0" max="2.5" step="0.05" value="1">
    <label for="wobble">ondulation du trace &mdash; <span id="v-wob">0.00</span></label>
    <input type="range" id="wobble" min="0" max="1.5" step="0.05" value="0">
    <label for="splitCount">dedoublements dans la video &mdash; au plus <span id="v-sc">3</span></label>
    <input type="range" id="splitCount" min="0" max="12" step="1" value="3">
    <select id="splitOn" class="inst"></select>
    <label for="splitPx">ecart des copies &mdash; <span id="v-spx">11</span> px</label>
    <input type="range" id="splitPx" min="0" max="30" step="1" value="11">
    <label for="snare">eclair jaune sur la caisse claire &mdash; <span id="v-sn">1.00</span></label>
    <input type="range" id="snare" min="0" max="2" step="0.05" value="1">
    <label for="wave">amplitude de la courbe &mdash; <span id="v-wv">1.10</span></label>
    <input type="range" id="wave" min="0" max="3" step="0.05" value="1.10">
    <label for="wavePunch">gonflement sur le temps fort &mdash; <span id="v-wp">0.85</span></label>
    <input type="range" id="wavePunch" min="0" max="2.5" step="0.05" value="0.85">
    <label for="trail">trainee de la bande &mdash; <span id="v-trail">1.00</span></label>
    <input type="range" id="trail" min="0" max="2.5" step="0.05" value="1">
    <label for="glitch">glitchs sur les paroxysmes &mdash; <span id="v-gl">1.00</span></label>
    <input type="range" id="glitch" min="0" max="2" step="0.05" value="1">
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
    <p class="hint" style="margin-top:0">Chaque reaction se cale sur
      l'instrument de votre choix : la batterie est reconnue a l'analyse, donc
      « caisse claire » veut vraiment dire caisse claire. A zero, la reaction
      est eteinte.</p>

    <label for="punch">zoom d'impact &mdash; <span id="v-pu">0.03</span></label>
    <input type="range" id="punch" min="0" max="0.25" step="0.005" value="0.032">
    <select id="punchOn" class="inst"></select>

    <label for="shake">secousse de l'image &mdash; <span id="v-sh">0.00</span></label>
    <input type="range" id="shake" min="0" max="2" step="0.05" value="0">
    <select id="shakeOn" class="inst"></select>

    <label for="parts">etincelles ejectees &mdash; <span id="v-pa">0.00</span></label>
    <input type="range" id="parts" min="0" max="3" step="0.05" value="0">
    <select id="partsOn" class="inst"></select>
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
    <p class="hint">L'eclat du fond ne se voit que s'il y a une image ou une
      video derriere la machine.</p>
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

    <label for="stut">begaiement &mdash; l'image gele <span id="v-st">0.00</span> s</label>
    <input type="range" id="stut" min="0" max="0.3" step="0.01" value="0">
    <select id="stutOn" class="inst"></select>
    <p class="hint">Le begaiement fige l'image sur l'instant du coup ; le son,
      lui, continue. Sur le charley il donne un rythme sacade, sur la grosse
      caisse un arret net. Au-dela d'un dixieme de seconde la video parait
      ralentie plutot qu'abimee.</p>
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
    <div id="shoterr"></div>
    <label for="scrub">instant du morceau &mdash; <span id="v-t">0.0 s</span></label>
    <input type="range" id="scrub" min="0" max="100" step="0.1" value="0" disabled>
    <div class="row" style="margin-top:8px">
      <button class="ghost" id="toSplit">aller au prochain dedoublement</button>
      <button class="ghost" id="toDrop">aller au prochain paroxysme</button>
      <button class="ghost" id="hi">chercher un kick</button>
    </div>
    <p class="hint">L'apercu est une vraie image du rendu, calculee avec vos
      reglages : ce que vous voyez ici est ce que vous obtiendrez.</p>
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
  setStatus('lecture et analyse de ' + f.name + '…');
  $('#go').disabled = true;
  try {
    const r = await fetch('/upload', {method:'POST', body:f,
      headers:{'X-Filename': f.name}});
    const j = await r.json();
    if (j.error) throw new Error(j.error);
    track = j.track; drops = j.drops || []; duration = j.duration;
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
    wavePunch: $('#wavePunch').value, backdrop,
    bdStrength: $('#bdStrength').value, bdClear: $('#bdClear').value,
    screenDim: $('#screenDim').value,
    travel: $('#travel').value, travelMode: $('#travelMode').value,
    punch: $('#punch').value, punchOn: $('#punchOn').value,
    shake: $('#shake').value, shakeOn: $('#shakeOn').value,
    parts: $('#parts').value, partsOn: $('#partsOn').value,
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
    curve: $('#curve').checked ? '1' : '0', w: 960, h: 540,
  });
  return p;
}
let pending = null;
function shot() {
  if (!track) return;
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
$('#split').oninput  = e => { $('#v-split').textContent = (+e.target.value).toFixed(2); shot(); };
$('#wobble').oninput = e => { $('#v-wob').textContent  = (+e.target.value).toFixed(2); shot(); };
$('#trail').oninput  = e => { $('#v-trail').textContent= (+e.target.value).toFixed(2); shot(); };
const bind = (id, out, dec) => { $(id).oninput = e => {
  $(out).textContent = dec ? (+e.target.value).toFixed(dec) : e.target.value; shot(); }; };
bind('#splitCount','#v-sc',0); bind('#splitPx','#v-spx',0);
bind('#snare','#v-sn',2); bind('#wave','#v-wv',2); bind('#wavePunch','#v-wp',2);
bind('#bdStrength','#v-bds',2); bind('#bdClear','#v-bdc',2); bind('#screenDim','#v-sd',2);
bind('#glitch','#v-gl',2);
bind('#punch','#v-pu',3); bind('#shake','#v-sh',2); bind('#parts','#v-pa',2);
bind('#partsSpeed','#v-pas',2); bind('#partsLife','#v-pal',2);
bind('#ring','#v-ri',2); bind('#gridPulse','#v-gp',2); bind('#bgFlash','#v-bf',2);
bind('#tranches','#v-tr',2); bind('#blocs','#v-bl',2); bind('#roll','#v-ro',2);
bind('#ghost','#v-gh',2); bind('#invert','#v-in',2); bind('#stut','#v-st',2);
$('#travel').oninput = e => {
  $('#v-tv').textContent = Math.round(+e.target.value * 100) + ' %'; shot(); };
for (const id of ['#travelMode','#punchOn','#shakeOn','#partsOn','#ringOn',
                  '#gridOn','#flashOn','#splitOn','#tranchesOn','#blocsOn',
                  '#rollOn','#ghostOn','#invertOn','#stutOn']) $(id).onchange = shot;

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

async function sendBackdrop(f) {
  setStatus('envoi du fond ' + f.name + '\u2026');
  try {
    const r = await fetch('/backdrop', {method:'POST', body:f,
                                        headers:{'X-Filename': f.name}});
    const j = await r.json();
    if (j.error) throw new Error(j.error);
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

/* ---------- rendu ---------- */
$('#go').onclick = async () => {
  const [w, h] = $('#size').value.split('x').map(Number);
  const body = {
    track, start: +$('#start').value || 0,
    duration: $('#dur').value ? +$('#dur').value : null,
    width: w, height: h, fps: +$('#fps').value,
    palette: $('#palette').value, trait: $('#trait').value,
    bg: $('#bg').value, bgColor: $('#bgColor').value,
    bgStrength: +$('#bgStrength').value, bgClear: +$('#bgClear').value,
    split: +$('#split').value, wobble: +$('#wobble').value,
    trail: +$('#trail').value, title: $('#title').value,
    fallbackTitle: ($('#title').placeholder || ''),
    splitCount: +$('#splitCount').value, splitPx: +$('#splitPx').value,
    snare: +$('#snare').value, wave: +$('#wave').value,
    wavePunch: +$('#wavePunch').value, backdrop,
    bdStrength: +$('#bdStrength').value, bdClear: +$('#bdClear').value,
    screenDim: +$('#screenDim').value,
    curve: $('#curve').checked,
  };
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
                              ['#stutOn', 'charley']])
      remplir(sel, c.instruments || [], def);
    remplir('#travelMode', c.travellings || [], 'avant');
  })
  .catch(() => {});

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
