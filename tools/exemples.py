#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Les exemples du studio : sous chaque effet, ce qu'il fait, en image.

Une phrase dit ce que fait un effet ; une image le montre. Pour chacun, on rend
un court extrait d'un morceau de demonstration — 2,4 s, une mesure — avec cet
effet seul pousse fort, tout le reste au repos : une animation (WebP) que la
page joue au survol, et une image fixe, prise la ou l'effet se voit le plus
(l'image de l'extrait qui s'ecarte le plus du meme extrait sans l'effet).

Rien n'est livre avec le code : le morceau est synthetise ici meme (grosse
caisse, caisse claire, clap, charleys, basse, accords, percussions — celui du
verificateur de batterie), et l'image de fond des exemples qui en demandent
une est dessinee. Les exemples dependent du moteur : ils sont refaits a chaque
version, dans out/studio/exemples/<version>/. Le studio les lance tout seul,
en tache de fond, la premiere fois qu'il demarre sur une version nouvelle.

    python3 tools/exemples.py            # tous
    python3 tools/exemples.py split ring # quelques-uns
"""
import json
import os
import re
import sys
import time

import numpy as np

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
W, H, FPS = 384, 216, 12
DUREE = 2.4                      # une mesure a 100 BPM
DEBUT = 19.2                     # la neuvieme mesure : tout y joue — grosse
                                 # caisse, caisse claire, clap, charleys,
                                 # crash, basse, accords, percussions, et la
                                 # montee qui s'y acheve

# Les reglages qui decrivent un effet sans en etre un a montrer : nombres,
# vitesses, longueurs. Leur effet se voit dans l'exemple de l'effet lui-meme.
SANS = {"splitPx", "splitCount", "partsN", "partsSpeed", "partsLife",
        "stutLoop", "scrLen", "echoN", "echoDelay", "waveSmooth", "midiForce",
        "midiOffset", "midiTempo", "scrub", "couleurCoupsLibre", "trait",
        "bgColor", "title"}
# Les listes dont chaque choix a son exemple
OPTIONS = ("machine", "palette", "bg", "couleurCoups", "textureTouches",
           "travelMode")
# Le repos : ce qui a deja un effet par defaut est coupe, pour que chaque
# exemple ne montre que le sien
REPOS = {"glitch": "0", "split": "0", "punch": "0", "snare": "0",
         "wobble": "0", "machine": "mpc", "palette": "vert", "bg": "noir"}
# Ce qu'un exemple demande en plus de son effet pousse fort
AVEC = {
    "bgStrength": {"bg": "grille"}, "bgClear": {"bg": "grille"},
    "bgAnim": {"bg": "grille"}, "gridPulse": {"bg": "grille"},
    "bgFlash": {"fond": True}, "bdStrength": {"fond": True},
    "bdClear": {"fond": True}, "screenDim": {"fond": True},
    "bdSharp": {"fond": True}, "travel": {"fond": True},
    "travelMode": {"fond": True, "travel": "1"},
    "couleurCoups": {"machine": "mpc"}, "textureTouches": {"machine": "mpc"},
    "passage": {"plan": True}, "passageTurb": {"plan": True, "passage": "1.9"},
    "splitOn": {}, "split": {"splitOn": "tout"},
    "spectro": {"wave": "0.5"},
}
# Des valeurs choisies a la main plutot qu'aux 85 % de la course
FORT = {"taille": "0.55", "nettete": "0.4", "reflet": "1", "split": "2.2",
        "glitch": "2.5", "snare": "2", "stepDiv": "4", "eclatPads": "2.5",
        "cadence": "3", "passage": "1.9", "passageTurb": "2.5"}


def dossier(version=None):
    if version is None:
        from omnipotard_intro import VERSION as version
    return os.path.join(ROOT, "out", "studio", "exemples", version)


def controles():
    """Les controles de la page : id -> {genre, min, max, valeur, options}."""
    import studio as S
    out = {}
    for m in re.finditer(r'<(input|select)\b([^>]*)>(.*?)(?=<input|<select|$)',
                         S.PAGE, re.S):
        attrs = m.group(2)
        i = re.search(r'id="([^"]+)"', attrs)
        if not i:
            continue
        g = re.search(r'type="([^"]+)"', attrs)
        c = {"genre": g.group(1) if g else ("select" if m.group(1) == "select"
                                            else "text")}
        for b in ("min", "max", "value"):
            v = re.search(r'\b%s="([^"]*)"' % b, attrs)
            c[b] = v.group(1) if v else None
        if m.group(1) == "select":
            corps = m.group(3).split("</select>")[0]
            opts = re.findall(r'<option(?:\s+value="([^"]*)")?([^>]*)>([^<]*)', corps)
            c["options"] = [(v or t.strip()) for v, _a, t in opts]
            choisi = [(v or t.strip()) for v, a, t in opts if "selected" in a]
            c["value"] = choisi[0] if choisi else (c["options"][0] if c["options"] else "")
        out[i.group(1)] = c
    return out


def liste():
    """Les exemples a faire : (cle, reglages de la page a poser)."""
    import studio as S
    ctl = controles()
    # les listes que la page remplit elle-meme (palettes, fonds, machines)
    remplies = {"machine": list(S.NOMS_MACHINES), "palette": sorted(S.PALETTES),
                "bg": list(S.BACKGROUNDS), "travelMode": list(S.TRAVELLINGS)}
    out = []
    for ident, c in ctl.items():
        if ident in SANS or ident.startswith("midi") or ident.endswith("On"):
            continue
        if c["genre"] == "range":
            lo, hi = float(c["min"]), float(c["max"])
            fort = FORT.get(ident, "%.3f" % (lo + 0.85 * (hi - lo)))
            out.append((ident, dict(AVEC.get(ident, {}), **{ident: fort})))
        elif ident in OPTIONS:
            for v in remplies.get(ident) or c.get("options") or []:
                out.append(("%s=%s" % (ident, v),
                            dict(AVEC.get(ident, {}), **{ident: v})))
    return out


def _usine():
    """Les valeurs d'usine de la page, par identifiant."""
    return {k: (c["value"] if c["value"] is not None else "")
            for k, c in controles().items()}


def morceau_demo(chemin):
    """Dix mesures a 100 BPM, tous les instruments du verificateur."""
    import verifier_batterie as VB
    from omnipotard_intro import write_wav
    x, _gt = VB.morceau(mesures=10)
    st = np.stack([x, x], axis=1)
    write_wav(chemin, (np.clip(st, -1.0, 1.0) * 32767).astype("<i2"), VB.SR)


def fond_demo(chemin):
    """Une image de fond dessinee : un ciel de nuit, des lumieres floues."""
    from PIL import Image, ImageDraw, ImageFilter
    w, h = 1280, 720
    y = np.linspace(0, 1, h)[:, None]
    ciel = np.zeros((h, w, 3))
    ciel[..., 0] = 0.05 + 0.25 * y
    ciel[..., 1] = 0.04 + 0.08 * y
    ciel[..., 2] = 0.18 + 0.20 * (1 - y)
    im = Image.fromarray((np.clip(ciel, 0, 1) * 255).astype(np.uint8))
    d = ImageDraw.Draw(im)
    rng = np.random.default_rng(4)
    for _ in range(60):
        x0, y0 = rng.uniform(0, w), rng.uniform(h * 0.45, h)
        r = rng.uniform(6, 40)
        col = tuple(int(v) for v in rng.choice([(255, 170, 60), (255, 90, 120),
                                                (120, 200, 255)]))
        d.ellipse((x0 - r, y0 - r, x0 + r, y0 + r), fill=col)
    im = im.filter(ImageFilter.GaussianBlur(9))
    d = ImageDraw.Draw(im)
    for k in range(14):                                  # des toits
        x0 = k * w / 14
        hh = rng.uniform(0.15, 0.45) * h
        d.rectangle((x0, h - hh, x0 + w / 14 + 2, h), fill=(8, 6, 14))
    im.save(chemin)


def _images(info, palette, kw, debut):
    import mpc_performance as MP
    r = MP._renderer(info, W, H, FPS, 7, True, palette, dict(kw))
    ts = debut + np.arange(int(round(DUREE * FPS))) / FPS
    return [MP.frame_performance(r, float(t), info["duration"]) for t in ts], r


def generer(sortie=None, seulement=None, journal=print):
    """Fait les exemples manquants. Rend le nombre d'exemples faits."""
    import mpc_performance as MP
    import studio as S
    from PIL import Image
    sortie = sortie or dossier()
    os.makedirs(sortie, exist_ok=True)
    wav = os.path.join(sortie, "demo.wav")
    if not os.path.exists(wav):
        morceau_demo(wav)
    fond = os.path.join(sortie, "fond.png")
    if not os.path.exists(fond):
        fond_demo(fond)
    info = MP.analyze(wav, 0.0, None)
    # les paroxysmes de la demo : un au debut de la mesure montree, pour que
    # le glitch, qui ne part que sur eux, ait de quoi partir
    info["drops"] = [DEBUT + 0.35]
    usine = _usine()
    repos = dict(usine, **REPOS)
    neutres = {}
    faits = 0
    a_faire = [e for e in liste() if not seulement or e[0] in seulement
               or e[0].split("=")[0] in seulement]
    t0 = time.time()
    for k, (cle, pose) in enumerate(a_faire):
        fichier = os.path.join(sortie, _nom(cle))
        if os.path.exists(fichier + ".webp") and os.path.exists(fichier + ".jpg"):
            continue
        ident = cle.split("=")[0]
        pose = dict(pose)
        avec_fond = pose.pop("fond", False)
        avec_plan = pose.pop("plan", False)
        q = dict(repos, **pose)
        debut = DEBUT
        if avec_plan:
            # la deformation precede l'instant inscrit : elle se deroule dans
            # la mesure montree
            q["machines"] = "0=mpc, %.2f=digitakt" % (DEBUT + 2.1)
        palette, kw = S.look_from(q)
        kw["midi"] = ""
        if avec_fond:
            kw["backdrop"] = fond
        if ident == "split":
            r0 = MP._renderer(info, W, H, FPS, 7, True, palette, dict(kw))
            st = r0.split_times()
            if len(st):
                debut = max(0.0, float(st[0]) - 0.25)
        imgs, _r = _images(info, palette, kw, debut)
        # l'image fixe : celle qui s'ecarte le plus du meme instant sans
        # l'effet — pour une liste, l'instant d'une grosse caisse suffit
        if "=" in cle:
            fixe = imgs[min(len(imgs) - 1, int(0.3 * FPS))]
        else:
            q0 = dict(q, **{ident: usine.get(ident, "")})
            cle0 = (debut, tuple(sorted(q0.items())), avec_fond)
            if cle0 not in neutres:
                p0, kw0 = S.look_from(q0)
                kw0["midi"] = ""
                if avec_fond:
                    kw0["backdrop"] = fond
                neutres[cle0] = _images(info, p0, kw0, debut)[0]
            ecarts = [float(np.abs(a.astype(np.int16) - b.astype(np.int16)).mean())
                      for a, b in zip(imgs, neutres[cle0])]
            fixe = imgs[int(np.argmax(ecarts))]
        pil = [Image.fromarray(a) for a in imgs]
        pil[0].save(fichier + ".webp", "WEBP", save_all=True,
                    append_images=pil[1:], duration=int(1000 / FPS), loop=0,
                    quality=62, method=4)
        Image.fromarray(fixe).save(fichier + ".jpg", "JPEG", quality=82)
        faits += 1
        journal("exemple %d/%d : %s (%.0f s)" % (k + 1, len(a_faire), cle,
                                                time.time() - t0))
    with open(os.path.join(sortie, "index.json"), "w") as f:
        json.dump(sorted(c for c, _p in liste()
                         if os.path.exists(os.path.join(sortie, _nom(c) + ".jpg"))), f)
    return faits


def _nom(cle):
    """Un nom de fichier sur pour une cle « palette=bleu-fond »."""
    return re.sub(r"[^A-Za-z0-9_.=-]", "_", cle).replace("=", "--")


def main():
    try:
        os.nice(10)                  # en tache de fond : le studio d'abord
    except (AttributeError, OSError):
        pass
    n = generer(seulement=set(sys.argv[1:]) or None)
    print("%d exemple(s) faits dans %s" % (n, dossier()))


if __name__ == "__main__":
    main()
