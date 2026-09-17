#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Studio Omnipotard v2 — la meme machine, une page plus claire.

La v1 posait ses quatre-vingt-dix reglages les uns sous les autres : tout y
etait, mais il fallait deja savoir ce qu'on cherchait. La v2 ne retire rien —
elle range.

    trois profondeurs   « simple » montre une quinzaine de reglages,
                        « regle » une cinquantaine, « tout » les quatre-vingt-dix
    six onglets         au lieu d'une colonne de douze cartes a derouler
    l'apercu fixe       il reste sous les yeux pendant qu'on regle

Les controles ne sont pas recopies : ils sont **lus dans la page de la v1** au
demarrage, avec leur libelle, leurs bornes et leur valeur d'usine. Les deux
pages ne peuvent donc pas diverger — ajouter un curseur a la v1 le fait
apparaitre ici, au niveau « tout » tant qu'on ne lui en a pas donne un autre.

    python3 tools/studio_v2.py
"""
import json
import os
import re
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import studio as S                                            # noqa: E402


# ==========================================================================
#  Lire les controles de la page de la v1
# ==========================================================================

# Les cartes qui ne sont pas des reglages d'allure : elles ont leur place
# ailleurs dans la v2 (l'en-tete, l'apercu).
HORS_ONGLETS = ("Morceau", "Prereglage", "Apercu")

ONGLETS = (
    ("Machine", ("Machine", "Trait")),
    ("Couleurs", ("Couleur du trait", "Fond", "Image ou video de fond")),
    ("Reactions", ("Reactions au son",)),
    ("Avaries", ("Avaries d'image",)),
    ("Matiere", ("Echo, couleurs, spectrogramme", "Texture trip hop, lo-fi")),
    ("Rendu", ("Rendu",)),
)

# Ce qu'on montre a chaque profondeur. Tout ce qui n'est cite nulle part
# n'apparait qu'au niveau « tout » : un reglage ajoute plus tard ne disparait
# donc jamais, il arrive seulement au fond.
SIMPLE = {
    "machine", "palette", "bg", "bgStrength", "taille", "presence", "neon",
    "split", "snare", "wave", "trail", "glitch", "punch", "punchOn", "title",
    "size", "fps", "quality", "curve", "start", "dur",
}
REGLE = SIMPLE | {
    "trait", "bgColor", "bgClear", "bgAnim", "reflet", "tube", "nettete",
    "splitOn", "splitCount", "splitPx", "wobble", "wavePunch", "stepDiv",
    "bdStrength", "bdClear", "screenDim", "bdSharp", "travel", "travelMode",
    "shake", "shakeOn", "parts", "partsOn", "partsN", "ring", "ringOn",
    "gridPulse", "gridOn", "bgFlash", "flashOn",
    "echo", "echoN", "echoDelay", "couleurs", "spectro",
    "cadence", "haloDoux", "poussiere", "flottement",
    "tranches", "tranchesOn", "stut", "stutOn", "kaleido", "kaleidoOn",
    "scramble", "scrLen",
}


def _texte(html):
    """Le libelle d'une etiquette, sans la valeur qui l'accompagne."""
    html = re.sub(r"<span.*?</span>", "", html, flags=re.S)
    html = re.sub(r"<[^>]+>", "", html)
    html = (html.replace("&mdash;", "").replace("&hellip;", "…")
                .replace("&amp;", "&").replace("&nbsp;", " "))
    return " ".join(html.split()).strip(" —-")


def extraire_controles(page=None):
    """Tous les reglages de la page de la v1, dans l'ordre ou elle les pose."""
    page = page if page is not None else S.PAGE
    carte, libelle, out = "", "", []
    motif = (r'<h2>(?P<h2>.*?)</h2>'
             r'|<label for="(?P<pour>[^"]+)">(?P<lib>.*?)</label>'
             r'|<(?P<balise>input|select)\b(?P<attrs>[^>]*)>')
    for m in re.finditer(motif, page, re.S):
        if m.group("h2"):
            carte = _texte(m.group("h2"))
            continue
        if m.group("pour"):
            libelle = _texte(m.group("lib"))
            continue
        attrs = m.group("attrs")
        ident = re.search(r'id="([^"]+)"', attrs)
        if not ident or carte in HORS_ONGLETS:
            continue
        ident = ident.group(1)
        genre = re.search(r'type="([^"]+)"', attrs)
        genre = genre.group(1) if genre else "select"
        if genre == "file":
            continue
        val = re.search(r'value="([^"]*)"', attrs)
        borne = {b: re.search(r'%s="([^"]+)"' % b, attrs) for b in
                 ("min", "max", "step")}
        out.append({
            "id": ident, "genre": genre, "carte": carte,
            "libelle": libelle if libelle else ident,
            "valeur": val.group(1) if val else "",
            "coche": "checked" in attrs,
            "niveau": 1 if ident in SIMPLE else (2 if ident in REGLE else 3),
            **{b: (v.group(1) if v else None) for b, v in borne.items()},
        })
        libelle = ""
    return out


def plan():
    """Les controles ranges par onglet, tels que la page les affichera."""
    par_carte = {}
    for c in extraire_controles():
        par_carte.setdefault(c["carte"], []).append(c)
    vus, onglets = set(), []
    for nom, cartes in ONGLETS:
        blocs = [{"titre": k, "controles": par_carte[k]}
                 for k in cartes if k in par_carte]
        for b in blocs:
            vus.add(b["titre"])
        if blocs:
            onglets.append({"nom": nom, "blocs": blocs})
    # une carte ajoutee a la v1 et oubliee ici doit apparaitre quand meme
    reste = [{"titre": k, "controles": v} for k, v in par_carte.items()
             if k not in vus]
    if reste:
        onglets.append({"nom": "Divers", "blocs": reste})
    return onglets


def _libelles_coches(page):
    """Le texte des cases a cocher.

    Une case porte son libelle a l'interieur de son etiquette, sans « for » :
    il faut donc le chercher autour d'elle et non avant elle.
    """
    out = {}
    for m in re.finditer(r'<label\b[^>]*>(.*?)</label>', page, re.S):
        ident = re.search(r'<input[^>]*type="checkbox"[^>]*id="([^"]+)"',
                          m.group(1))
        if ident:
            out[ident.group(1)] = _texte(re.sub(r'<input[^>]*>', '', m.group(1)))
    return out


def _options(page):
    """Les choix ecrits en dur dans une liste de la v1, s'il y en a."""
    out = {}
    for m in re.finditer(r'<select\b([^>]*)>(.*?)</select>', page, re.S):
        ident = re.search(r'id="([^"]+)"', m.group(1))
        if not ident:
            continue
        choix = []
        for o in re.finditer(r'<option\b([^>]*)>(.*?)</option>', m.group(2), re.S):
            attrs, texte = o.group(1), _texte(o.group(2))
            val = re.search(r'value="([^"]*)"', attrs)
            # « <option>30</option> » n'a pas d'attribut : la valeur est alors
            # le texte lui-meme. Sans ce repli, la liste des images par seconde
            # arrivait vide dans la v2 et le rendu partait sans cadence.
            choix.append({"v": val.group(1) if val else texte, "t": texte,
                          "choisi": "selected" in attrs})
        out[ident.group(1)] = choix
    return out


def _defauts_on(page):
    """Le declencheur que la v1 choisit pour chaque effet, lu dans son code."""
    bloc = page.split("for (const [sel, def] of [", 1)
    if len(bloc) < 2:
        return {}
    return {a: b for a, b in re.findall(r"\['#(\w+)', '([^']+)'\]",
                                        bloc[1].split("]])", 1)[0])}


def rattacher_declencheurs(onglets):
    """Colle chaque liste « sur quoi ça part » au curseur qui la precede.

    Dans la page classique, ces listes suivent leur curseur sans etiquette :
    l'oeil fait le lien. Recopiees telles quelles, elles se retrouvaient
    seules, nommees par leur identifiant — « punchOn », « shakeOn ». On les
    range donc dans leur effet, qui devient un seul bloc : le curseur, ce qui
    le declenche, sa frequence, son explication.
    """
    for o in onglets:
        for b in o["blocs"]:
            gardes = []
            for c in b["controles"]:
                if (c["genre"] == "select" and c["id"].endswith("On")
                        and gardes and not gardes[-1].get("declencheur")):
                    gardes[-1]["declencheur"] = c
                else:
                    gardes.append(c)
            b["controles"] = gardes
    return onglets


def tous_les_ids(onglets):
    """Les identifiants de tous les reglages, declencheurs compris."""
    out = set()
    for o in onglets:
        for b in o["blocs"]:
            for c in b["controles"]:
                out.add(c["id"])
                if c.get("declencheur"):
                    out.add(c["declencheur"]["id"])
    return out


def donnees():
    """Tout ce que la page v2 a besoin de savoir, en une fois."""
    opts, defs = _options(S.PAGE), _defauts_on(S.PAGE)
    coches = _libelles_coches(S.PAGE)
    onglets = plan()
    for o in onglets:
        for b in o["blocs"]:
            for c in b["controles"]:
                if c["genre"] == "checkbox" and c["id"] in coches:
                    c["libelle"] = coches[c["id"]]
                if c["genre"] == "select":
                    c["options"] = opts.get(c["id"], [])
                    if c["id"] in defs:
                        c["valeur"] = defs[c["id"]]
                    elif c["options"]:
                        choisi = [o for o in c["options"] if o["choisi"]]
                        c["valeur"] = (choisi or c["options"])[0]["v"]
    return rattacher_declencheurs(onglets)


# ==========================================================================
#  La page
# ==========================================================================

PAGE = r"""<!doctype html>
<html lang="fr"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>Studio Omnipotard v2</title>
<style>
  :root{
    --fond:#06080a; --carte:#0d1115; --carte2:#11161b; --ligne:#1b242c;
    --ligne2:#27333c; --ink:#dfe9e4; --dim:#839690;
    --faible:#5f7069; --acc:#3dff72; --acc-mat:#1f8f45; --mal:#ff6b5e;
    --mono:ui-monospace,SFMono-Regular,Menlo,Consolas,monospace;
  }
  *{box-sizing:border-box}
  html{-webkit-text-size-adjust:100%}
  body{margin:0;background:var(--fond);color:var(--ink);font-family:var(--mono);
    font-size:13px;line-height:1.55;
    background-image:radial-gradient(1200px 600px at 70% -10%,#0b1a13 0%,transparent 70%)}

  /* ---------- en-tete ---------- */
  header{position:sticky;top:0;z-index:20;display:flex;align-items:center;
    gap:16px;flex-wrap:wrap;padding:11px 20px;
    background:rgba(6,8,10,.92);backdrop-filter:blur(8px);
    border-bottom:1px solid var(--ligne)}
  h1{margin:0;font-size:13px;letter-spacing:.2em;text-transform:uppercase;
    color:var(--acc);font-weight:600}
  h1 span{color:var(--ink);opacity:.8}
  #ver{color:var(--faible);font-size:10.5px;letter-spacing:.04em}
  #etat{color:var(--dim);flex:1 1 240px;min-width:0;font-size:12px;
    white-space:nowrap;overflow:hidden;text-overflow:ellipsis}
  #etat.mal{color:var(--mal)}
  header a{color:var(--faible);font-size:11px;text-decoration:none;
    border-bottom:1px solid transparent}
  header a:hover{color:var(--acc);border-color:var(--acc)}

  /* ---------- structure ---------- */
  main{display:grid;grid-template-columns:minmax(0,1fr) minmax(0,1.22fr);
    gap:20px;padding:20px;align-items:start;max-width:1720px;margin:0 auto}
  @media (max-width:1040px){main{grid-template-columns:1fr;gap:16px;padding:14px}}
  .bloc{background:linear-gradient(180deg,var(--carte2),var(--carte));
    border:1px solid var(--ligne);border-radius:10px;padding:16px 16px 6px;
    margin-bottom:14px}
  .bloc.serre{padding:14px 16px}
  .bloc h2{margin:0 0 12px;font-size:10px;letter-spacing:.2em;color:var(--faible);
    text-transform:uppercase;font-weight:600}

  /* ---------- un reglage ---------- */
  .champ{padding:7px 0 9px;border-bottom:1px solid transparent}
  .champ + .champ{border-top:1px solid rgba(255,255,255,.035)}
  .tete{display:flex;align-items:baseline;gap:10px;margin-bottom:6px}
  .tete label{flex:1;margin:0;color:var(--dim);font-size:12px;letter-spacing:.01em;
    text-transform:none;cursor:pointer}
  .champ:hover .tete label,.champ:focus-within .tete label{color:var(--ink)}
  .val{color:var(--acc);font-size:12px;font-variant-numeric:tabular-nums;
    white-space:nowrap}
  .freq{display:block;font-size:10.5px;color:var(--acc-mat);margin-top:5px;
    letter-spacing:.01em}
  /* le declencheur est second : il se lit, il ne se crie pas */
  .sur{margin-top:7px;display:flex;align-items:center;gap:8px}
  .sur em{font-style:normal;font-size:10.5px;color:var(--faible);
    letter-spacing:.12em;text-transform:uppercase;flex:0 0 auto}
  .sur select{font-size:11.5px;color:var(--dim);padding:5px 8px;
    background:transparent;border-color:var(--ligne)}
  .sur select:hover{color:var(--ink);border-color:var(--ligne2)}
  .aide{font-size:11px;line-height:1.5;color:var(--faible);margin:6px 0 0;
    display:none}
  .champ:hover .aide,.champ:focus-within .aide,body.aides .aide{display:block}

  /* ---------- curseurs ---------- */
  input[type=range]{-webkit-appearance:none;appearance:none;width:100%;
    height:18px;background:transparent;margin:0;display:block}
  input[type=range]::-webkit-slider-runnable-track{height:4px;border-radius:2px;
    background:linear-gradient(90deg,var(--acc) 0 var(--p,50%),
      #1c262d var(--p,50%) 100%)}
  input[type=range]::-moz-range-track{height:4px;border-radius:2px;background:#1c262d}
  input[type=range]::-moz-range-progress{height:4px;border-radius:2px;background:var(--acc)}
  input[type=range]::-webkit-slider-thumb{-webkit-appearance:none;appearance:none;
    width:14px;height:14px;border-radius:50%;background:var(--acc);
    margin-top:-5px;border:0;box-shadow:0 0 0 3px rgba(61,255,114,.14);
    transition:box-shadow .12s}
  input[type=range]::-moz-range-thumb{width:14px;height:14px;border-radius:50%;
    background:var(--acc);border:0;box-shadow:0 0 0 3px rgba(61,255,114,.14)}
  input[type=range]:hover::-webkit-slider-thumb{box-shadow:0 0 0 5px rgba(61,255,114,.22)}
  input[type=range]:focus{outline:none}
  input[type=range]:focus::-webkit-slider-thumb{box-shadow:0 0 0 5px rgba(61,255,114,.35)}

  /* ---------- listes, champs, boutons ---------- */
  select,input[type=text],input[type=number],input[type=color]{
    width:100%;background:#090d10;color:var(--ink);border:1px solid var(--ligne2);
    border-radius:7px;padding:8px 9px;font:inherit;font-size:12.5px;
    transition:border-color .12s}
  select:hover,input[type=text]:hover,input[type=number]:hover{border-color:#36454f}
  select:focus,input[type=text]:focus,input[type=number]:focus{outline:none;
    border-color:var(--acc)}
  input[type=color]{height:36px;padding:3px;cursor:pointer}
  .coche{display:flex;align-items:center;gap:9px;color:var(--dim);font-size:12px;
    cursor:pointer}
  .coche input{width:16px;height:16px;accent-color:var(--acc);margin:0}
  .coche:hover{color:var(--ink)}
  button{background:#131a20;color:var(--ink);border:1px solid var(--ligne2);
    border-radius:7px;padding:9px 13px;font:inherit;font-size:12.5px;
    cursor:pointer;transition:border-color .12s,color .12s,background .12s}
  button:hover:not(:disabled){border-color:var(--acc);color:var(--acc)}
  button.fort{background:var(--acc);color:#04120a;border-color:var(--acc);
    font-weight:700;letter-spacing:.12em;text-transform:uppercase;font-size:12px;
    padding:12px 14px}
  button.fort:hover:not(:disabled){background:#63ff8d;color:#04120a}
  button:disabled{opacity:.38;cursor:default}
  .rang{display:flex;gap:8px;flex-wrap:wrap;align-items:center}
  .rang > *{flex:1 1 130px}
  .rang > .etroit{flex:0 0 92px}

  /* ---------- onglets et profondeur ---------- */
  .onglets{display:flex;gap:6px;flex-wrap:wrap;margin:0 0 12px}
  .onglets button{padding:7px 12px;font-size:12px;border-radius:999px;
    color:var(--dim)}
  .onglets button.on{border-color:var(--acc);color:var(--acc);
    background:rgba(61,255,114,.08)}
  .niveaux{display:flex;border:1px solid var(--ligne2);border-radius:999px;
    overflow:hidden}
  .niveaux button{border:0;border-radius:0;padding:6px 13px;font-size:11.5px;
    color:var(--dim);letter-spacing:.04em}
  .niveaux button:hover:not(.on){color:var(--ink);background:#141b21}
  .niveaux button.on{background:var(--acc);color:#04120a;font-weight:700}
  .cache{display:none}

  /* ---------- depots ---------- */
  .drop{border:1px dashed var(--ligne2);border-radius:9px;padding:14px;
    text-align:center;color:var(--faible);cursor:pointer;font-size:12px;
    transition:border-color .12s,color .12s,background .12s}
  .drop:hover{border-color:#3c4d57;color:var(--dim)}
  .drop.sur{border-color:var(--acc);color:var(--acc);background:rgba(61,255,114,.06)}
  .drop b{display:block;color:var(--ink);font-size:13px;margin-bottom:2px}

  /* ---------- apercu ---------- */
  #vue{position:sticky;top:64px}
  .ecran{position:relative;border-radius:10px;overflow:hidden;
    border:1px solid var(--ligne);background:#000;
    box-shadow:0 0 0 1px rgba(61,255,114,.05),0 18px 50px -24px rgba(0,0,0,.9)}
  #image,#clip{width:100%;display:block;background:#000;aspect-ratio:16/9;
    object-fit:contain;transition:opacity .15s}
  #image[hidden],#clip[hidden]{display:none}
  .jauge{height:3px;background:#121a1f;border-radius:2px;overflow:hidden;margin:9px 0 5px}
  .jauge i{display:block;height:100%;background:var(--acc);width:0;
    transition:width .2s}
  .meta{display:flex;gap:18px;flex-wrap:wrap;color:var(--faible);font-size:11px;
    letter-spacing:.04em;margin-top:12px}
  .meta b{color:var(--ink);font-weight:600}
  .note{font-size:11px;color:var(--faible);margin:6px 0 0;min-height:1em}
  a.dl[hidden]{display:none}
  a.dl{display:inline-block;margin-top:10px;background:var(--acc);color:#04120a;
    padding:10px 14px;border-radius:7px;text-decoration:none;font-weight:700;
    letter-spacing:.12em;text-transform:uppercase;font-size:12px}
  a.dl:hover{background:#63ff8d}
</style></head><body>

<header>
  <h1>Studio Omnipotard <span style="color:var(--ink)">v2</span></h1>
  <span id="ver"></span>
  <span id="etat">deposez un morceau pour commencer</span>
  <div class="niveaux" id="niveaux">
    <button data-n="1">simple</button>
    <button data-n="2" class="on">regle</button>
    <button data-n="3">tout</button>
  </div>
  <button id="aides" title="garder toutes les explications ouvertes">aide</button>
  <a href="/">page classique</a>
</header>

<main>
 <div>
  <div class="bloc serre">
    <div class="drop" id="drop"><b id="nomMorceau">Deposer un morceau</b>
      mp3, wav, flac, m4a&hellip; ou cliquer</div>
    <input type="file" id="fichier" accept="audio/*" hidden>
    <div class="meta" id="infos" hidden>
      <span>duree <b id="i-duree">-</b></span>
      <span>tempo <b id="i-bpm">-</b></span>
      <span>coups <b id="i-coups">-</b></span>
      <span>paroxysmes <b id="i-drops">-</b></span>
    </div>
  </div>

  <div class="bloc serre">
    <h2>Prereglage</h2>
    <select id="preset"></select>
    <div class="rang" style="margin-top:9px">
      <input type="text" id="presetNom" maxlength="40"
             placeholder="nom de votre reglage">
      <button id="presetSave">Enregistrer</button>
      <button id="presetDel" class="etroit" disabled>Effacer</button>
    </div>
  </div>

  <div class="onglets" id="onglets"></div>
  <div id="panneaux"></div>
 </div>

 <div id="vue">
  <div class="ecran">
    <img id="image" alt="apercu">
    <video id="clip" hidden loop controls playsinline></video>
  </div>
  <div class="jauge" id="jauge" hidden><i id="jaugeBar"></i></div>
  <p class="note" id="jaugeTexte"></p>
  <div class="tete" style="margin-top:6px">
    <label for="scrub">instant du morceau</label>
    <span class="val" id="v-scrub">0.0 s</span>
  </div>
  <input type="range" id="scrub" min="0" max="100" step="0.1" value="0" disabled>
  <div class="rang" style="margin-top:10px">
    <button id="versDrop">prochain paroxysme</button>
    <button id="versSplit">prochain dedoublement</button>
    <button id="lire" disabled>Lire en mouvement</button>
    <select id="clipDur" class="etroit">
      <option value="2">2 s</option><option value="4" selected>4 s</option>
      <option value="8">8 s</option>
    </select>
  </div>
  <div class="bloc serre" style="margin-top:16px">
    <h2>Image ou video de fond</h2>
    <div class="drop" id="dropFond"><b id="nomFond">Deposer une image ou une video</b>
      jpg, png, mp4, mov&hellip; ou cliquer</div>
    <input type="file" id="fichierFond" accept="image/*,video/*" hidden>
    <button id="retirerFond" style="margin-top:9px" hidden>retirer le fond</button>
  </div>
  <div class="bloc serre">
    <h2>Rendu</h2>
    <button class="fort" id="rendre" disabled style="width:100%">Lancer le rendu</button>
    <div class="jauge" id="prog" hidden><i id="progBar"></i></div>
    <p class="note" id="progTexte"></p>
    <a class="dl" id="dl" hidden>Telecharger</a>
  </div>
 </div>
</main>

<script>
const CTRL = __CTRL__;
const $ = s => document.querySelector(s);
let morceau = null, duree = 0, drops = [], AIDE = {}, COMPTE = {}, QUALITES = {},
    PRESETS = {}, MES = {}, USINE = {}, FRAPPES = null, fond = '',
    attente = null, seq = 0, minuteur = null, minuteurClip = null;

function etat(txt, mal) {
  $('#etat').textContent = txt;
  $('#etat').classList.toggle('mal', !!mal);
}
function echap(v) {
  return String(v).replace(/&/g, '&amp;').replace(/</g, '&lt;')
                  .replace(/"/g, '&quot;');
}

/* ---------- construction des reglages ----------
   Rien n'est ecrit en dur ici : la liste vient du serveur, qui la lit dans la
   page classique. Un curseur ajoute la-bas apparait ici sans qu'on y touche. */
function bati() {
  const onglets = $('#onglets'), panneaux = $('#panneaux');
  CTRL.forEach((o, i) => {
    const b = document.createElement('button');
    b.textContent = o.nom;
    b.onclick = () => montrerOnglet(i);
    onglets.appendChild(b);
    const p = document.createElement('div');
    p.dataset.onglet = i;
    p.innerHTML = o.blocs.map(bl =>
      '<div class="bloc" data-carte="1"><h2>' + echap(bl.titre) + '</h2>'
      + bl.controles.map(champ).join('') + '</div>').join('');
    panneaux.appendChild(p);
  });
  montrerOnglet(0);
}

/* Un reglage tient en trois lignes : son nom et sa valeur sur la meme ligne,
   le curseur, puis — discretement — la frequence a laquelle il partira.
   L'explication, elle, ne se montre qu'au survol : quatre-vingt-dix phrases
   affichees en meme temps, c'est un mur, pas une aide. */
function champ(c) {
  const id = echap(c.id), lib = echap(c.libelle);
  let h = '<div class="champ" data-champ="' + id + '" data-niveau="'
        + c.niveau + '">';
  if (c.genre === 'checkbox') {
    h += '<label class="coche"><input type="checkbox" id="' + id + '"'
       + (c.coche ? ' checked' : '') + '>' + lib + '</label>';
  } else {
    h += '<div class="tete"><label for="' + id + '">' + lib + '</label>'
       + (c.genre === 'range' ? '<span class="val" id="v-' + id + '"></span>' : '')
       + '</div>';
    if (c.genre === 'select') {
      h += liste(c);
    } else if (c.genre === 'range') {
      h += '<input type="range" id="' + id + '" min="' + c.min + '" max="'
         + c.max + '" step="' + c.step + '" value="' + c.valeur + '">';
    } else {
      h += '<input type="' + c.genre + '" id="' + id + '" value="'
         + echap(c.valeur) + '"'
         + (c.genre === 'number' ? ' step="0.1"' : ' maxlength="40"') + '>';
    }
  }
  if (c.declencheur)
    h += '<div class="sur"><em>sur</em>' + liste(c.declencheur) + '</div>';
  return h + '<span class="freq" id="f-' + id + '"></span>'
           + '<p class="aide" id="a-' + id + '"></p></div>';
}
function liste(c) {
  return '<select id="' + echap(c.id) + '">' + (c.options || []).map(
    o => '<option value="' + echap(o.v) + '"'
         + (o.v === c.valeur ? ' selected' : '') + '>' + echap(o.t)
         + '</option>').join('') + '</select>';
}

/* Tous les reglages a plat : les curseurs, les listes, et les declencheurs
   ranges dans leur effet. */
function tousLesControles() {
  const out = [];
  for (const o of CTRL) for (const b of o.blocs) for (const c of b.controles) {
    out.push(c);
    if (c.declencheur) out.push(c.declencheur);
  }
  return out;
}

function montrerOnglet(i) {
  document.querySelectorAll('#onglets button').forEach(
    (b, k) => b.classList.toggle('on', k === i));
  document.querySelectorAll('#panneaux > div').forEach(
    p => p.classList.toggle('cache', +p.dataset.onglet !== i));
}

/* La profondeur ne retire aucun reglage : elle en cache. Un bloc dont tous
   les reglages sont caches disparait aussi, sinon la page se remplit de
   cartes vides. */
let niveau = 2;
function majNiveau() {
  document.querySelectorAll('[data-champ]').forEach(
    d => d.classList.toggle('cache', +d.dataset.niveau > niveau));
  document.querySelectorAll('[data-carte]').forEach(d => {
    const reste = [...d.querySelectorAll('[data-champ]')].some(
      c => !c.classList.contains('cache'));
    d.classList.toggle('cache', !reste);
  });
}
$('#niveaux').onclick = e => {
  if (!e.target.dataset.n) return;
  niveau = +e.target.dataset.n;
  document.querySelectorAll('#niveaux button').forEach(
    b => b.classList.toggle('on', +b.dataset.n === niveau));
  majNiveau();
};
</script>
</body></html>
"""


_SUITE = r"""
/* ---------- ce que la page envoie au moteur ----------
   Une seule liste, construite des reglages eux-memes : impossible qu'un
   reglage visible ne parte pas au rendu. */
function reglages() {
  const p = {};
  for (const c of tousLesControles()) {
    const el = $('#' + c.id);
    if (!el) continue;
    p[c.id] = (c.genre === 'checkbox') ? el.checked : el.value;
  }
  // le nombre d'etincelles est porte par la racine, comme dans la v1
  p.partsN = String(Math.round($('#partsN').value * $('#partsN').value));
  p.fallbackTitle = $('#title').placeholder || '';
  p.backdrop = fond;
  return p;
}
function params() {
  const p = new URLSearchParams(reglages());
  p.set('track', morceau); p.set('t', $('#scrub').value);
  p.set('w', 960); p.set('h', 540);
  p.set('curve', $('#curve').checked ? '1' : '0');
  p.delete('size'); p.delete('fps'); p.delete('start'); p.delete('dur');
  return p;
}

/* ---------- apercu ---------- */
function apercu() {
  if (!morceau) return;
  rendreImage();
  clearTimeout(minuteur);
  minuteur = setTimeout(() => {
    const n = ++seq;
    $('#image').style.opacity = .4;
    fetch('/still?' + params().toString()).then(async r => {
      if (n !== seq || r.status === 409) return;
      if (!r.ok) {
        let m = 'erreur ' + r.status;
        try { m = (await r.json()).error || m; } catch (e) {}
        throw new Error(m);
      }
      const url = URL.createObjectURL(await r.blob());
      const vieux = $('#image').dataset.blob;
      $('#image').dataset.blob = url;
      $('#image').src = url;
      $('#image').style.opacity = 1;
      if (vieux) URL.revokeObjectURL(vieux);
    }).catch(e => { if (n === seq) etat('apercu : ' + e.message, true); });
  }, 220);
}
function rendreImage() {
  const v = $('#clip');
  if (!v.hidden) { v.pause(); v.hidden = true; v.removeAttribute('src'); }
  $('#image').hidden = false;
}

/* ---------- depots, avec leur progression ---------- */
function poids(n) { return (n / 1048576).toFixed(n > 10485760 ? 0 : 1) + ' Mo'; }
function deposer(url, f, quoi) {
  return new Promise((bon, mauvais) => {
    const x = new XMLHttpRequest();
    x.open('POST', url);
    x.setRequestHeader('X-Filename', f.name);
    x.upload.onprogress = e => etat('envoi ' + quoi + ' ' + f.name + ' ('
      + poids(f.size) + ') — '
      + (e.lengthComputable ? Math.round(e.loaded / e.total * 100) : 0) + ' %');
    x.upload.onload = () => etat('le studio examine ' + f.name + '…');
    x.onload = () => {
      let j = null;
      try { j = JSON.parse(x.responseText); } catch (e) { j = null; }
      if (j && j.error) mauvais(new Error(j.error));
      else if (!j) mauvais(new Error('le studio a repondu ' + x.status));
      else bon(j);
    };
    x.onerror = () => mauvais(new Error("le studio n'a pas repondu pendant "
      + "l'envoi (" + poids(f.size) + "). Le message exact est dans la fenetre "
      + 'noire du studio.'));
    x.send(f);
  });
}

function relie(zone, entree, quoi) {
  zone.onclick = () => entree.click();
  zone.ondragover = e => { e.preventDefault(); zone.classList.add('sur'); };
  zone.ondragleave = () => zone.classList.remove('sur');
  zone.ondrop = e => { e.preventDefault(); zone.classList.remove('sur');
    if (e.dataTransfer.files[0]) quoi(e.dataTransfer.files[0]); };
  entree.onchange = () => entree.files[0] && quoi(entree.files[0]);
}
relie($('#drop'), $('#fichier'), envoyerMorceau);
relie($('#dropFond'), $('#fichierFond'), envoyerFond);

async function envoyerMorceau(f) {
  $('#rendre').disabled = true; $('#lire').disabled = true;
  try {
    const j = await deposer('/upload', f, 'du morceau');
    morceau = j.track; duree = j.duration; drops = j.drops || [];
    FRAPPES = {frappes: j.frappes || {}, drops: drops,
               duree: j.duree || j.duration || 0, bpm: j.bpm || 0};
    $('#nomMorceau').textContent = j.name;
    $('#infos').hidden = false;
    $('#i-duree').textContent = fmt(j.duration);
    $('#i-bpm').textContent = j.bpm.toFixed(1) + ' BPM';
    $('#i-coups').textContent = j.hits;
    $('#i-drops').textContent = drops.length;
    $('#title').placeholder = j.name.replace(/\.[^.]+$/, '');
    const s = $('#scrub');
    s.max = Math.max(1, duree - 1); s.value = Math.min(40, duree * 0.35);
    s.disabled = false;
    allerA(+s.value);
    $('#rendre').disabled = false; $('#lire').disabled = false;
    etat(j.name + ' — ' + j.bpm.toFixed(1) + ' BPM, ' + drops.length
         + ' paroxysme(s)');
    frequences();
    apercu();
  } catch (e) { etat('echec : ' + e.message, true); }
}

async function envoyerFond(f) {
  try {
    const j = await deposer('/backdrop', f, 'du fond');
    fond = j.name;
    $('#nomFond').textContent = j.name + (j.video ? ' (video)' : '');
    $('#retirerFond').hidden = false;
    etat('fond en place');
    apercu();
  } catch (e) { etat('fond refuse : ' + e.message, true); }
}
$('#retirerFond').onclick = () => {
  fond = ''; $('#retirerFond').hidden = true;
  $('#nomFond').textContent = 'Deposer une image ou une video';
  apercu();
};

/* ---------- combien de fois chaque effet partira ---------- */
function parMinute(n, d) {
  return (!d || n < 2) ? '' : ', soit ' + (n / (d / 60)).toFixed(0) + ' par minute';
}
function frequences() {
  for (const [id, genre] of Object.entries(COMPTE)) {
    const cible = $('#f-' + id);
    if (!cible) continue;
    const el = $('#' + id);
    if (genre === 'qualite') { cible.textContent = QUALITES[$('#quality').value] || ''; continue; }
    const d = FRAPPES ? FRAPPES.duree : 0;
    let txt = '';
    if (el && Math.abs(+el.value) < 1e-9) txt = 'eteint';
    else if (!FRAPPES) txt = 'deposez un morceau pour connaitre la frequence';
    else if (Array.isArray(genre)) {
      const n = genre.reduce((a, f) => a + ((FRAPPES.frappes || {})[f] || 0), 0);
      txt = '~ ' + n + ' fois' + parMinute(n, d) + '  (' + genre.join(' et ') + ')';
    } else if (genre === 'instrument') {
      const sel = $('#' + id + 'On') || $('#' + ({gridPulse:'gridOn', bgFlash:'flashOn'}[id] || ''));
      const fam = sel ? sel.value : 'grosse caisse';
      const n = (FRAPPES.frappes || {})[fam] || 0;
      txt = '~ ' + n + ' fois' + parMinute(n, d) + '  (' + fam + ')';
    } else if (genre === 'sequenceur') {
      const pas = 60 / Math.max(1, FRAPPES.bpm) / +$('#stepDiv').value;
      txt = 'une case toutes les ' + Math.round(pas * 1000) + ' ms, soit '
          + Math.round(60 / pas) + ' par minute';
    } else if (genre === 'split') {
      txt = '~ ' + Math.min(+$('#splitCount').value,
            1 + Math.floor(d / Math.max(25, 0.14 * d))) + ' fois dans la video';
    } else if (genre === 'drops') {
      txt = '~ ' + (FRAPPES.drops || []).length + ' fois  (les montees du morceau)';
    } else if (genre === 'tranche') {
      const blocs = Math.floor(d / Math.max(0.04, +$('#scrLen').value));
      txt = '~ ' + Math.round(blocs * +$('#scramble').value) + ' blocs brasses sur ' + blocs;
    } else txt = 'en continu, du debut a la fin';
    cible.textContent = txt;
  }
}

/* ---------- prereglages ---------- */
function listePrereglages() {
  const gr = (titre, noms) => !noms.length ? '' :
    '<optgroup label="' + titre + '">' + noms.map(
      k => '<option value="' + echap(k) + '">' + echap(k) + '</option>').join('')
    + '</optgroup>';
  const avant = $('#preset').value;
  $('#preset').innerHTML = gr('Fournis', Object.keys(PRESETS))
                         + gr('Mes reglages', Object.keys(MES).sort());
  if (avant) $('#preset').value = avant;
  $('#presetDel').disabled = !(($('#preset').value || '') in MES);
}
function appliquer(nom) {
  const p = PRESETS[nom] || MES[nom] || {};
  for (const [id, v] of Object.entries(USINE)) {
    if (['preset', 'bg', 'size', 'fps', 'quality', 'clipDur'].includes(id)) continue;
    const el = $('#' + id);
    if (el) { el.value = v; el.dispatchEvent(new Event('input')); }
  }
  for (const [id, v] of Object.entries(p)) {
    const el = $('#' + id);
    if (!el) continue;
    if (el.type === 'checkbox') el.checked = !!v && v !== 'false';
    else el.value = (id === 'partsN') ? Math.round(Math.sqrt(+v)) : v;
    el.dispatchEvent(new Event(el.tagName === 'SELECT' ? 'change' : 'input'));
  }
  $('#presetDel').disabled = !(nom in MES);
}
$('#preset').onchange = () => { appliquer($('#preset').value); frequences(); apercu(); };
async function ecrire(corps) {
  const r = await fetch('/reglages', {method: 'POST', body: JSON.stringify(corps)});
  const j = await r.json();
  if (j.error) throw new Error(j.error);
  MES = j.mes || {}; listePrereglages();
}
$('#presetSave').onclick = async () => {
  const nom = ($('#presetNom').value || '').trim();
  if (!nom) return etat('donnez un nom a ce reglage', true);
  try {
    const v = reglages();
    delete v.fallbackTitle; delete v.backdrop;
    await ecrire({nom: nom, valeurs: v});
    $('#preset').value = nom; $('#presetDel').disabled = false;
    $('#presetNom').value = '';
    etat('« ' + nom + ' » enregistre');
  } catch (e) { etat('pas enregistre : ' + e.message, true); }
};
$('#presetDel').onclick = async () => {
  const nom = $('#preset').value;
  if (!(nom in MES)) return;
  try { await ecrire({action: 'supprimer', nom: nom}); etat('« ' + nom + ' » efface'); }
  catch (e) { etat('pas efface : ' + e.message, true); }
};

/* ---------- apercu en mouvement ---------- */
function corpsDuRendu(plus) {
  const [w, h] = $('#size').value.split('x').map(Number);
  const b = reglages();
  delete b.size;
  return Object.assign(b, {
    track: morceau, width: w, height: h, fps: +$('#fps').value,
    start: +$('#start').value || 0,
    duration: $('#dur').value ? +$('#dur').value : null,
    quality: $('#quality').value, curve: $('#curve').checked,
  }, plus || {});
}
$('#lire').onclick = async () => {
  if (!morceau) return;
  clearTimeout(minuteurClip); rendreImage();
  $('#lire').disabled = true; $('#jauge').hidden = false;
  $('#jaugeBar').style.width = '0%'; $('#jaugeTexte').textContent = 'preparation…';
  const sec = +$('#clipDur').value;
  const depart = Math.max(0, Math.min(+$('#scrub').value, Math.max(0, duree - sec)));
  try {
    const r = await fetch('/render', {method: 'POST', body: JSON.stringify(
      corpsDuRendu({start: depart, duration: sec, width: 960, height: 540,
                    fps: 15, apercu: true}))});
    const j = await r.json();
    if (j.error) throw new Error(j.error);
    suivreClip(j.id);
  } catch (e) {
    etat('lecture impossible : ' + e.message, true);
    $('#lire').disabled = false; $('#jauge').hidden = true;
  }
};
function suivreClip(id) {
  minuteurClip = setTimeout(async () => {
    let j; try { j = await (await fetch('/job?id=' + id)).json(); }
    catch (e) { return suivreClip(id); }
    if (j.state === 'erreur') {
      etat('lecture impossible : ' + j.error, true);
      $('#lire').disabled = false; $('#jauge').hidden = true; return;
    }
    if (j.state === 'fini') {
      const v = $('#clip');
      v.src = '/download?inline=1&id=' + id;
      v.hidden = false; $('#image').hidden = true;
      $('#jauge').hidden = true; $('#jaugeTexte').textContent = '';
      $('#lire').disabled = false;
      v.play().catch(() => { v.muted = true; v.play().catch(() => {}); });
      etat('lecture en boucle — bougez un reglage pour revenir a l\'image');
      return;
    }
    $('#jaugeBar').style.width = (j.total ? j.done / j.total * 100 : 0) + '%';
    $('#jaugeTexte').textContent = j.state === 'rendu'
      ? j.done + '/' + j.total + ' images' : j.state + '…';
    suivreClip(id);
  }, 500);
}

/* ---------- rendu ---------- */
$('#rendre').onclick = async () => {
  $('#rendre').disabled = true; $('#dl').hidden = true; $('#prog').hidden = false;
  $('#progBar').style.width = '0%'; $('#progTexte').textContent = 'preparation…';
  try {
    const r = await fetch('/render', {method: 'POST',
                                      body: JSON.stringify(corpsDuRendu())});
    const j = await r.json();
    if (j.error) throw new Error(j.error);
    suivreRendu(j.id);
  } catch (e) {
    etat('echec : ' + e.message, true); $('#rendre').disabled = false;
  }
};
function suivreRendu(id) {
  setTimeout(async () => {
    let j; try { j = await (await fetch('/job?id=' + id)).json(); }
    catch (e) { return suivreRendu(id); }
    if (j.state === 'erreur') {
      etat('echec du rendu : ' + j.error, true);
      $('#rendre').disabled = false; $('#prog').hidden = true; return;
    }
    if (j.state === 'fini') {
      $('#progBar').style.width = '100%';
      $('#progTexte').textContent = 'ecrit dans out/studio/' + j.name
        + ' (' + (j.size / 1048576).toFixed(1) + ' Mo)';
      $('#dl').href = '/download?id=' + id;
      $('#dl').setAttribute('download', j.name);
      $('#dl').hidden = false; $('#rendre').disabled = false;
      etat('rendu termine'); return;
    }
    $('#progBar').style.width = (j.total ? j.done / j.total * 100 : 0) + '%';
    $('#progTexte').textContent = j.state === 'rendu'
      ? j.done + '/' + j.total + ' images — encore ' + fmt(j.eta) : j.state + '…';
    suivreRendu(id);
  }, 700);
}
function fmt(s) {
  s = Math.max(0, Math.round(s));
  return s >= 60 ? Math.floor(s / 60) + ' min ' + String(s % 60).padStart(2, '0') + ' s'
                 : s + ' s';
}

/* ---------- reperes dans le morceau ---------- */
function allerA(t) {
  const s = $('#scrub');
  s.value = t;
  $('#v-scrub').textContent = (+t).toFixed(1) + ' s';
  s.style.setProperty('--p', (s.max > 0 ? t / +s.max * 100 : 0).toFixed(1) + '%');
  apercu();
}
$('#scrub').oninput = e => allerA(+e.target.value);
$('#versDrop').onclick = () => {
  if (!drops.length) return etat('aucun paroxysme detecte', true);
  const t = +$('#scrub').value;
  allerA((drops.find(d => d > t + 0.2) ?? drops[0]) + 0.05);
};
$('#versSplit').onclick = async () => {
  if (!morceau) return;
  const q = new URLSearchParams({track: morceau, on: $('#splitOn').value,
                                 n: $('#splitCount').value});
  try {
    const j = await (await fetch('/splits?' + q)).json();
    if (!j.times || !j.times.length) return etat('aucun dedoublement prevu', true);
    const t = +$('#scrub').value;
    allerA((j.times.find(x => x > t + 0.2) ?? j.times[0]) + 0.08);
  } catch (e) { etat('impossible : ' + e.message, true); }
};

/* ---------- mise en place ---------- */
bati();
fetch('/config').then(r => r.json()).then(c => {
  if (c.version) $('#ver').textContent = c.version;
  AIDE = c.aide || {}; COMPTE = c.compte || {}; QUALITES = c.qualites || {};
  PRESETS = c.presets || {}; MES = c.mes || {};
  const groupes = (sel, gs, def) => {
    if (!$(sel)) return;
    $(sel).innerHTML = gs.map(g => '<optgroup label="' + echap(g.titre) + '">'
      + g.noms.map(v => '<option value="' + echap(v) + '"'
        + (v === def ? ' selected' : '') + '>' + echap(v) + '</option>').join('')
      + '</optgroup>').join('');
  };
  const plat = (sel, liste, def) => {
    if (!$(sel)) return;
    $(sel).innerHTML = liste.map(v => '<option value="' + echap(v.v || v) + '"'
      + ((v.v || v) === def ? ' selected' : '') + '>' + echap(v.t || v)
      + '</option>').join('');
  };
  for (const ctl of tousLesControles()) {
    if (ctl.genre !== 'select' || (ctl.options || []).length) continue;
    if (ctl.id === 'machine')
      plat('#machine', (c.machines || []).map(
        m => ({v: m.cle, t: m.nom + ' — ' + m.quoi})), 'mpc');
    else if (ctl.id === 'travelMode') plat('#travelMode', c.travellings || [], 'avant');
    else if (ctl.id === 'quality')
      plat('#quality', Object.keys(QUALITES).map(k => ({v: k, t: k})), 'compatible');
    else groupes('#' + ctl.id, c.declencheurs || [], ctl.valeur);
  }
  // une phrase sous chaque reglage, et sa frequence
  for (const ctl of tousLesControles()) {
    const zone = $('#a-' + ctl.id);
    if (zone) zone.textContent = AIDE[ctl.id] || '';
  }
  USINE = {};
  for (const ctl of tousLesControles()) {
    const el = $('#' + ctl.id);
    if (el && el.type !== 'checkbox') USINE[ctl.id] = el.value;
  }
  listePrereglages();
  majNiveau();
  frequences();
}).catch(() => etat('le studio ne repond pas', true));

/* chaque reglage redessine, et tient son chiffre a jour */
document.querySelectorAll('#panneaux input, #panneaux select').forEach(el => {
  const maj = () => {
    const v = $('#v-' + el.id);
    if (v) {
      const n = +el.value;
      v.textContent = Number.isFinite(n)
        ? (Math.abs(n) >= 100 || Number.isInteger(n) ? el.value : n.toFixed(2))
        : el.value;
    }
    // la part remplie du curseur : le navigateur ne sait pas la dessiner
    // seul, on la lui donne
    if (el.type === 'range') {
      const a = +el.min, b = +el.max;
      el.style.setProperty('--p', (b > a ? (el.value - a) / (b - a) * 100 : 0)
                                  .toFixed(1) + '%');
    }
    frequences();
    if (['size', 'fps', 'quality', 'start', 'dur'].includes(el.id)) return;
    apercu();
  };
  el.addEventListener('input', maj);
  el.addEventListener('change', maj);
  maj();
});

/* Les explications sont la au survol ; ce bouton les laisse toutes ouvertes,
   pour qui decouvre la page. */
$('#aides').onclick = () => {
  const on = document.body.classList.toggle('aides');
  $('#aides').classList.toggle('on', on);
};
"""

PAGE = PAGE.replace("</script>\n</body></html>",
                    _SUITE + "\n</script>\n</body></html>")


def page():
    """La page v2, avec ses controles injectes."""
    return PAGE.replace("__CTRL__", json.dumps(donnees(), ensure_ascii=False))


def main():
    """Lance le studio et ouvre la v2."""
    sys.argv.append("--v2")
    S.main()


if __name__ == "__main__":
    main()
