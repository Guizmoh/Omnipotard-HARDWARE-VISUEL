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
from omnipotard_intro import BACKGROUNDS, PALETTES, hex_to_rgb, rgb_to_hex  # noqa: E402

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
# tout ce que le studio fabrique tient dans un seul dossier de travail
WORKDIR = os.path.join(ROOT, "out", "studio")
UPLOADS = os.path.join(WORKDIR, "morceaux")
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
        "snare": float(q.get("snare", 1.0)),
        "wave_gain": float(q.get("wave", 2.6)),
        "trail": float(q.get("trail", 1.0)),
        "screen_title": str(q.get("title") or ""),
    }


# --------------------------------------------------------------------------
#  Etat : analyses et rendus en cours
# --------------------------------------------------------------------------

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
        tr = self.track(tid)
        palette, kw = look_from(q)
        key = (tid, w, h, bool(q.get("curve", True)))
        with self.lock:
            r = self.renderers.get(key)
        if r is None:
            r = _renderer(tr["info"], w, h, 30, 7, bool(q.get("curve", True)),
                          palette, kw)
            with self.lock:
                if len(self.renderers) > 4:        # ne pas garder tout l'historique
                    self.renderers.pop(next(iter(self.renderers)))
                self.renderers[key] = r
        # set_look ne connait que la couleur et le fond ; les deux autres
        # reglages se posent directement sur l'instance
        POSE = ("wobble", "split", "snare", "wave_gain", "trail", "screen_title")
        r.set_look(palette, **{k: v for k, v in kw.items() if k not in POSE})
        for k in POSE:
            setattr(r, k, kw[k])
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

    # ---- reponses
    def _send(self, code, ctype, body, extra=None):
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        for k, v in (extra or {}).items():
            self.send_header(k, v)
        self.end_headers()
        try:
            self.wfile.write(body)
        except (BrokenPipeError, ConnectionResetError):
            pass                                          # onglet ferme en cours de route

    def _json(self, obj, code=200):
        self._send(code, "application/json; charset=utf-8",
                   json.dumps(obj).encode("utf-8"))

    def _fail(self, e, code=400):
        self._json({"error": str(e)}, code)

    # ---- GET
    def do_GET(self):
        u = urlparse(self.path)
        q = {k: v[0] for k, v in parse_qs(u.query).items()}
        try:
            if u.path in ("/", "/index.html"):
                return self._send(200, "text/html; charset=utf-8", PAGE.encode("utf-8"))
            if u.path == "/config":
                return self._json({
                    "palettes": {k: {"trait": rgb_to_hex(v[0]),
                                     "fond": rgb_to_hex(v[3])}
                                 for k, v in sorted(PALETTES.items())},
                    "fonds": list(BACKGROUNDS),
                })
            if u.path == "/still":
                q["curve"] = q.get("curve", "1") == "1"
                img = STUDIO.still(q["track"], float(q.get("t", 0.0)), q,
                                   int(q.get("w", 640)), int(q.get("h", 360)))
                return self._send(200, "image/png", png_bytes(img))
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
    <h2>Trait</h2>
    <label for="split">dedoublement du trait sur les gros subs &mdash; <span id="v-split">1.00</span></label>
    <input type="range" id="split" min="0" max="2.5" step="0.05" value="1">
    <label for="wobble">ondulation du trace &mdash; <span id="v-wob">0.00</span></label>
    <input type="range" id="wobble" min="0" max="1.5" step="0.05" value="0">
    <label for="trail">trainee de la bande &mdash; <span id="v-trail">1.00</span></label>
    <input type="range" id="trail" min="0" max="2.5" step="0.05" value="1">
    <label for="title">titre affiche sur la dalle</label>
    <input type="text" id="title" maxlength="22" placeholder="nom du fichier">
    <p class="hint">Sur les coups graves vraiment appuyes — et seulement
      ceux-la — les trois couches de couleur du trait se separent, puis se
      recollent quand le coup retombe. Le fond, lui, ne bouge pas.</p>
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
    <label for="scrub">instant du morceau &mdash; <span id="v-t">0.0 s</span></label>
    <input type="range" id="scrub" min="0" max="100" step="0.1" value="0" disabled>
    <div class="row" style="margin-top:8px">
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
    const img = new Image();
    img.onload = () => { if (n === shotSeq) $('#shot').src = img.src; };
    img.src = '/still?' + params().toString();
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
$('#title').oninput  = shot;
$('#bgStrength').oninput = e => { $('#v-str').textContent = (+e.target.value).toFixed(2); shot(); };
$('#bgClear').oninput   = e => { $('#v-clr').textContent = (+e.target.value).toFixed(2); shot(); };
$('#scrub').oninput     = e => { $('#v-t').textContent = (+e.target.value).toFixed(1) + ' s'; shot(); };

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
