#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
MPC PERFORMANCE — visualiseur plein morceau

Charge un morceau (n'importe lequel, n'importe quelle duree), detecte sa
batterie et son tempo, et rend une video ou la MPC Live III (le meme dessin
que dans l'intro OMNIPOTARD) joue le morceau du debut a la fin : les pads
s'allument sur les coups reels, la bande de 16 pas suit le tempo reel (phase
calee sur les vraies grosses caisses), l'ecran affiche la forme d'onde, les
Q-Links et le touch strip suivent les enveloppes.

Reutilise entierement le moteur de tools/omnipotard_intro.py (geometrie,
detection audio, rendu du faisceau, colorisation) : ce script se contente de
piloter la machine image par image, sans le scenario de l'intro — pas de
clip qui s'enregistre, pas de titre, pas de zoom dans l'ecran. La machine est
deja entierement deployee des la premiere image et joue en continu ; un
simple fondu (image et son) ouvre et ferme la video.

Usage :
    python3 tools/mpc_performance.py assets/hint.mp3 -o out/mpc_performance.mp4

Pour un apercu rapide avant de lancer le morceau entier :
    python3 tools/mpc_performance.py assets/hint.mp3 --start 20 --duration 15 \
        -o out/preview.mp4
"""

import argparse
import os
import shutil
import subprocess
import sys
import tempfile
import time

import numpy as np

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from omnipotard_intro import (  # noqa: E402 -- reutilise le moteur de l'intro
    SR, PALETTES, BACKGROUNDS, Renderer, Beam, _decode, _lowpass, detect_beat,
    detect_hits, hex_to_rgb, make_backdrop, write_wav, PAD_OF, pool_context,
    fit_jobs, DECLENCHEURS, hasard_events, TRAVELLINGS,
    python_trop_petit,
    compute_spectro, PRESETS, QUALITES, APERCU, apercu_possible,
    MACHINES, NOMS_MACHINES,
)
import midi as midi_fichier          # noqa: E402 -- lecteur de fichiers MIDI


def probe_duration(path):
    out = subprocess.run(
        ["ffprobe", "-v", "error", "-show_entries", "format=duration",
         "-of", "default=nw=1:nk=1", path],
        stdout=subprocess.PIPE, check=True).stdout
    return float(out.decode().strip())


def estimate_phase(events, six):
    """Cale la grille de 16 pas sur les vraies grosses caisses du morceau :
    cherche le decalage qui les rapproche le plus d'un pas de sequenceur."""
    kicks = np.array([t for t, pad, f, d in events if pad == PAD_OF["kick"]])
    if len(kicks) == 0:
        return 0.0
    cands = np.linspace(0.0, six, 48, endpoint=False)
    best_phi, best_err = 0.0, np.inf
    for phi in cands:
        r = np.mod(kicks - phi, six)
        d = np.minimum(r, six - r)
        err = float(np.sum(d * d))
        if err < best_err:
            best_err, best_phi = err, phi
    return best_phi


def detect_drops(mono, sr, min_gap=7.0, thresh=0.60, rise=0.16):
    """Reperage des paroxysmes : les instants ou le morceau repart en force
    apres une respiration. C'est la, et seulement la, que les glitchs tombent.

    On compare l'energie a celle d'une seconde et demie plus tot : il ne
    suffit pas d'etre fort, il faut arriver fort.
    """
    w = max(1, int(sr * 0.04))                 # enveloppe a 25 Hz
    n = len(mono) // w
    if n < 8:
        return []
    e = np.abs(mono[:n * w].reshape(n, w)).max(axis=1)
    e = _lowpass(e, 12)                        # lisse sur ~0,5 s
    top = e.max()
    if top <= 0:
        return []
    e = e / top
    fps_e = sr / w
    back = max(1, int(1.5 * fps_e))
    out, last = [], -1e9
    for i in range(back, n):
        if e[i] > thresh and (e[i] - e[i - back]) > rise:
            t = i / fps_e
            if t - last >= min_gap:
                out.append(t)
                last = t
    return out


def load_full_track(path, start, duration, sr=SR):
    """Decode le morceau tel quel (pas de montage, pas de FX synthetises) et
    en extrait le tempo et les coups de batterie."""
    st = _decode(path, start, duration, sr)
    n = len(st)
    t = np.arange(n) / sr
    fade = (np.clip(t / 0.15, 0, 1) * np.clip((duration - t) / 0.4, 0, 1))[:, None]
    mix = np.clip(st * fade, -1.0, 1.0)
    mono = mix.mean(axis=1)
    beat = detect_beat(mono, sr)
    events = detect_hits(mono, sr)
    phi = estimate_phase(events, beat / 4.0)
    # Les declencheurs « hasard » sont fabriques ici plutot que dans le
    # moteur : ainsi le studio peut annoncer leur nombre sous le curseur,
    # comme pour un vrai instrument, et chaque tache de rendu les retrouve
    # sans se concerter.
    events = sorted(events + hasard_events(duration, beat, phi))
    # les paroxysmes se lisent sur le signal brut : le fondu d'ouverture
    # ressemblerait sinon a une montee en puissance et ferait un faux glitch.
    drops = detect_drops(st.mean(axis=1), sr)
    audio = {"stereo": (mix * 32767).astype("<i2"), "mono": mono.astype(np.float32),
             "events": events, "sr": sr, "beat": beat}
    return audio, phi, drops




def make_performance_renderer(w, h, fps, duration, audio, phi, drops, curve=True,
                              seed=7, palette="vert", wobble=0.0, split=1.0,
                              split_px=11.0, split_count=3,
                              split_on="grosse caisse", glitch=1.0,
                              step_div=2.0,
                              tranches=0.0, tranches_on="caisse claire",
                              roll=0.0, roll_on="grosse caisse",
                              ghost=0.0, ghost_on="caisse claire",
                              blocs=0.0, blocs_on="caisse claire",
                              invert=0.0, invert_on="grosse caisse",
                              stut=0.0, stut_on="charley", stut_loop=0.05,
                              scramble=0.0, scr_len=0.14,
                              miroir=0.0, miroir_on="caisse claire",
                              ondul=0.0, ondul_on="basse",
                              mosaic=0.0, mosaic_on="caisse claire",
                              kaleido=0.0, kaleido_on="caisse claire",
                              cisaille=0.0, cisaille_on="caisse claire",
                              coupure=0.0, coupure_on="grosse caisse",
                              tapestop=0.0, tapestop_on="grosse caisse",
                              cadence=0, poussiere=0.0, flottement=0.0,
                              halo_doux=0.0, echo=0.0, echo_n=3,
                              echo_delay=0.045, couleurs=0.0, spectro=0.0,
                              snare=1.0, wave_gain=1.10, trail=1.0, screen_title="",
                              wave_win=0.070, wave_smooth=56, wave_trig=0.0,
                              wave_passes=1, wave_punch=0.85,
                              punch=0.032, punch_on="grosse caisse",
                              shake_amp=0.0, shake_on="grosse caisse",
                              parts=0.0, parts_on="caisse claire",
                              parts_n=14, parts_speed=1.0, parts_life=0.55,
                              ring=0.0, ring_on="grosse caisse",
                              grid_pulse=0.0, grid_on="grosse caisse",
                              bg_flash=0.0, flash_on="caisse claire",
                              travel=0.0, travel_mode="avant",
                              backdrop=None, backdrop_strength=1.00,
                              backdrop_clear=0.28, screen_dim=0.40,
                              backdrop_sharp=0.37, taille=1.0, presence=1.0,
                              neon=1.0, reflet=0.5, tube=0.0,
                              midi_force=1.0, **bgkw):
    r = Renderer(w, h, fps, duration, audio, curve=curve, seed=seed,
                 palette=palette, **bgkw)
    # la taille se pose avant tout le reste : le creux de la texture et celui
    # de l'image de fond se calent dessus
    r.set_taille(taille)
    r.presence = float(presence)
    r.neon, r.reflet, r.tube = float(neon), float(reflet), float(tube)
    r.midi_force = float(midi_force)
    r.wobble, r.split, r.split_px = float(wobble), float(split), float(split_px)
    r.split_count, r.split_on = int(split_count), str(split_on)
    r.glitch = float(glitch)
    r.step_div = float(step_div)
    r.tranches, r.tranches_on = float(tranches), str(tranches_on)
    r.roll, r.roll_on = float(roll), str(roll_on)
    r.ghost, r.ghost_on = float(ghost), str(ghost_on)
    r.blocs, r.blocs_on = float(blocs), str(blocs_on)
    r.invert, r.invert_on = float(invert), str(invert_on)
    r.stut, r.stut_on = float(stut), str(stut_on)
    r.stut_loop = float(stut_loop)
    r.scramble, r.scr_len = float(scramble), float(scr_len)
    r.miroir, r.miroir_on = float(miroir), str(miroir_on)
    r.ondul, r.ondul_on = float(ondul), str(ondul_on)
    r.mosaic, r.mosaic_on = float(mosaic), str(mosaic_on)
    r.kaleido, r.kaleido_on = float(kaleido), str(kaleido_on)
    r.cisaille, r.cisaille_on = float(cisaille), str(cisaille_on)
    r.coupure, r.coupure_on = float(coupure), str(coupure_on)
    r.tapestop, r.tapestop_on = float(tapestop), str(tapestop_on)
    r.cadence = int(cadence)
    r.poussiere, r.flottement = float(poussiere), float(flottement)
    r.halo_doux = float(halo_doux)
    r.echo, r.echo_n = float(echo), int(echo_n)
    r.echo_delay, r.couleurs = float(echo_delay), float(couleurs)
    r.spectro = float(spectro)
    if r.spectro > 0.01:
        # calcule ici, une fois : les taches de rendu le recevront tout fait
        r.spec, r.spec_fps = compute_spectro(audio["mono"], audio["sr"])
    r.snare, r.wave_gain = float(snare), float(wave_gain)
    r.trail, r.screen_title = float(trail), str(screen_title or "")
    r.wave_win, r.wave_trig = float(wave_win), float(wave_trig)
    r.wave_passes, r.wave_punch = int(wave_passes), float(wave_punch)
    r.punch, r.punch_on = float(punch), str(punch_on)
    r.shake_amp, r.shake_on = float(shake_amp), str(shake_on)
    r.parts, r.parts_on = float(parts), str(parts_on)
    r.parts_n = int(parts_n)
    r.parts_speed, r.parts_life = float(parts_speed), float(parts_life)
    r.ring, r.ring_on = float(ring), str(ring_on)
    r.grid_pulse, r.grid_on = float(grid_pulse), str(grid_on)
    r.bg_flash, r.flash_on = float(bg_flash), str(flash_on)
    r.travel, r.travel_mode = float(travel), str(travel_mode)
    r.set_wave_smooth(int(wave_smooth))
    if backdrop:
        r.backdrop = make_backdrop(
            backdrop, w, h, fps, duration, strength=backdrop_strength,
            clear=backdrop_clear, scale=r.scale * r.taille,
            screen_dim=screen_dim, ecran=r.ecran,
            travel=r.travel, travel_mode=r.travel_mode, sharp=backdrop_sharp)
    # La machine est deja entierement deployee et joue en continu : on
    # neutralise tout ce qui, dans le moteur de l'intro, appartient au
    # scenario (reveal, pre-lueur, ecran qui se cache au zoom, extinction
    # cathodique programmee, glitchs d'intro etales sur toute la duree) sans
    # toucher au fichier partage — uniquement sur cette instance.
    far = (1e9, 1e9 + 1.0)
    r.tl.seg["groove"] = (-1e9, 1e9)   # pads/enveloppes actifs des t=0
    r.tl.seg["zoom"] = far             # l'ecran de la machine reste visible
    r.tl.seg["out"] = far              # pas d'extinction automatique
    r.step_phase = float(phi)                     # phase reelle du morceau
    r.drops = np.asarray(drops, dtype=np.float64)  # glitchs sur les paroxysmes
    return r


def frame_performance(r, t, duration):
    rng = np.random.default_rng(r.seed + int(t * r.fps + 0.5))
    # Le begaiement fige l'image sur l'instant du dernier coup : tout ce qui
    # suit est donc calcule a cet instant-la. Le tirage aleatoire, lui, reste
    # celui de l'image reelle — sans quoi le grain se figerait aussi et l'on
    # verrait une image arretee plutot qu'une image qui bute.
    # La cadence reduite se pose en premier : elle quantifie l'instant, et
    # tout ce qui suit travaille sur cet instant-la. C'est ce qui donne a la
    # video son air d'animation image par image plutot que de ralenti.
    if r.cadence > 1:
        pas = float(r.cadence) / r.fps
        t = int(t / pas) * pas
    t = r.tape_time(r.scramble_time(r.stutter_time(t)))
    # Le sequenceur de machines dit laquelle est a l'image a cet instant. On
    # le fait ici et pas seulement au trace : l'anneau de choc et les
    # etincelles partent du dessin, et partent donc du bon.
    r.poser_machine(t)
    # l'image respire sur l'instrument choisi, et peut aussi etre bousculee
    r._zoom = 1.0 + r.punch * r.hit_env(t, r.punch_on, fall=9.0)
    r._cam_z = 1.0                            # jamais de zoom dans l'ecran
    r._cam = (0.0, 0.0)
    if r.shake_amp > 0.001:
        sec = r.shake_amp * r.hit_env(t, r.shake_on, fall=16.0)
        # on ne puise dans le tirage que s'il y a vraiment une secousse :
        # sinon activer l'option deplacerait tout le hasard de l'image — le
        # grain, les tranches de glitch — sans rien secouer du tout.
        if sec > 0.002:
            r._cam = (float(rng.uniform(-1, 1) * 0.055 * sec),
                      float(rng.uniform(-1, 1) * 0.040 * sec))
    shake = r.glitch_at(t)

    beam = Beam(r.H, r.W, r.gain)
    # Le decor tient la largeur de l'image : quadrillage, coins, et le fil du
    # morceau qui entre par la gauche et ressort a droite. Il garde donc sa
    # taille quand la machine, elle, change de la sienne.
    r._ech = 1.0
    r._grid(beam, t, 0.55, 1.0)
    r._hud(beam, t, 1.0, 0.65)
    # le fil du morceau passe derriere la machine et s'allume sur les graves
    r._wave_line(beam, t, 1.0, 0.48, None, 999.0, 0.0)
    # ---- a partir d'ici, la machine et ce qui lui appartient
    #
    # Le trace est echantillonne en unites du monde : une machine retrecie
    # tasse le meme nombre de points sur moins de pixels, donc un trait plus
    # dense et plus lumineux. On corrige la luminosite d'autant, sans quoi
    # reduire la machine reviendrait a l'allumer.
    #
    # L'exposant 1,35 est mesure, pas choisi : la simple proportion (exposant
    # 1) laissait la machine passer de 149 a 212 en descendant a 0,45, parce
    # que des traits plus serres que le halo additionnent leurs halos. A 1,35
    # elle va de 149 a 167, ce qui ne se voit plus. A taille 1 le facteur vaut
    # 1 : rien ne change pour les videos deja rendues.
    r._ech = float(r.taille)
    machine = float(r.taille) ** 1.35 * float(r.presence)
    beam.mul = machine
    r._onde(beam, t, 1.0)                     # anneau, sous la machine
    # Echos : la machine telle qu'elle etait il y a quelques centiemes, de plus
    # en plus pale, dessinee sous l'image du moment. On les empile dans le meme
    # faisceau plutot que de calculer des images entieres — seul le trace est
    # refait, tout le reste (halo, textures, deformation) ne l'est qu'une fois.
    if r.echo > 0.01:
        for k in range(min(int(r.echo_n), 6), 0, -1):
            te = t - k * r.echo_delay
            if te < 0.0:
                continue
            beam.mul = machine * float(r.echo) ** k
            r._machine(beam, te, 1.0, 999.0, 0.0,
                       np.random.default_rng(r.seed + int(te * r.fps + 0.5)),
                       r.glitch_at(te))
        beam.mul = machine
    r._machine(beam, t, 1.0, 999.0, 0.0, rng, shake)   # sweep_x enorme = deployee
    r._etincelles(beam, t, 1.0)               # etincelles, par-dessus
    beam.mul, r._ech = 1.0, 1.0
    field = beam.render()
    img = r.colorize(field, t, 1.0, shake, rng)

    fade = min(1.0, t / 0.5) * min(1.0, (duration - t) / 0.6)
    if fade < 0.999:
        img = (img.astype(np.float32) * fade).astype(np.uint8)
    return img


# ==========================================================================
#  API : analyser, previsualiser, rendre
#
#  Ces trois fonctions sont ce qu'appellent la ligne de commande (plus bas)
#  et le studio (tools/studio.py). Elles ne dependent d'aucune interface.
# ==========================================================================

_R = None
_DUR = 0.0


def _init_worker(r, dur):
    """Installe le moteur dans une tache qui demarre vierge (spawn).

    Sous Unix les taches heritent de tout ce que le processus principal avait
    en memoire ; sous Windows elles demarrent d'un interpreteur neuf, et c'est
    ici qu'on leur donne de quoi travailler.
    """
    global _R, _DUR
    _R, _DUR = r, dur


def _worker(i):
    return frame_performance(_R, i / _R.fps, _DUR).tobytes()


def clamp_span(music, start, duration):
    """Ramene (depart, duree) a ce que le morceau contient reellement."""
    total = probe_duration(music)
    start = max(0.0, min(float(start), max(0.0, total - 0.5)))
    avail = max(0.0, total - start)
    dur = min(float(duration), avail) if duration else avail
    return total, start, dur


def analyze(music, start=0.0, duration=None):
    """Tempo, coups de batterie et paroxysmes, sans rien rendre."""
    total, start, dur = clamp_span(music, start, duration)
    if dur <= 0.5:
        raise ValueError("rien a traiter : depart (%.2fs) au-dela du morceau "
                         "(%.2fs)" % (start, total))
    audio, phi, drops = load_full_track(music, start, dur)
    return {"total": total, "start": start, "duration": dur,
            "beat": audio["beat"], "bpm": 60.0 / audio["beat"],
            "hits": len(audio["events"]), "drops": drops,
            "_audio": audio, "_phi": phi}


def preparer_midi(info, reglages):
    """Lit le fichier MIDI et le cale sur le morceau.

    Le calage se fait ici, une seule fois, dans le processus principal : les
    taches de rendu recoivent les notes deja placees et toutes le meme
    decalage. Le faire chacune de son cote serait plus lent et, si le calage
    hesitait entre deux instants, elles pourraient ne pas choisir le meme.

    Renvoie le dictionnaire de reglages, et y ajoute `midi_resume` : de quoi
    dire a la page ce qui a ete lu et de combien on a cale.
    """
    reglages = dict(reglages)
    chemin = reglages.pop("midi", None) or None
    # Le calage automatique est desactive par defaut : il se trompe a tous les
    # coups sur un fichier melodique (voir midi.caler). Un fichier exporte du
    # meme projet est de toute facon deja a l'heure.
    cale = reglages.pop("midi_cale", False)
    ecart = float(reglages.pop("midi_offset", 0.0) or 0.0)
    transpo = reglages.pop("midi_transpose", None)
    if not chemin or not os.path.exists(chemin):
        reglages.pop("midi_force", None)
        return reglages, None
    notes = midi_fichier.lire_notes(chemin)
    infos = midi_fichier.resume(notes)
    if not notes:
        reglages.pop("midi_force", None)
        return reglages, infos
    # Les instants d'attaque du morceau sont ceux que l'analyse a releves, et
    # ils sont comptes depuis le debut de l'extrait. Le fichier MIDI, lui, part
    # du debut du morceau : on remet donc les attaques dans le temps du morceau
    # avant de chercher, sinon le vrai decalage tombe hors de la fenetre des
    # qu'on rend un extrait pris au milieu.
    debut = float(info.get("start") or 0.0)
    auto, nettete = (midi_fichier.caler(
        notes, [e[0] + debut for e in info["_audio"]["events"]])
        if cale else (0.0, 0.0))
    # sous 1.3 de nettete il n'y a pas de correspondance : mieux vaut poser le
    # fichier au debut de l'extrait que de decaler la melodie au hasard
    if nettete < 1.3:
        auto = 0.0
    auto += debut
    if transpo is None or transpo == "":
        transpo = midi_fichier.transposition(notes)
    infos.update({"cale": auto, "nettete": nettete, "transpose": int(transpo),
                  "offset": auto + ecart})
    reglages["midi"] = [(a, b, c, d) for a, b, c, d in notes]
    reglages["midi_offset"] = auto + ecart
    reglages["midi_transpose"] = int(transpo)
    return reglages, infos


def _renderer(info, width, height, fps, seed, curve, palette, bgkw):
    # Un prereglage peut fixer la palette ; elle arrive alors parmi les autres
    # reglages et non par son argument, d'ou ce rattrapage. On copie plutot que
    # de retirer la cle : le dictionnaire appartient a l'appelant.
    bgkw = dict(bgkw)
    palette = bgkw.pop("palette", palette)
    bgkw, info["midi"] = preparer_midi(info, bgkw)
    return make_performance_renderer(
        width, height, fps, info["duration"], info["_audio"], info["_phi"],
        info["drops"], curve=curve, seed=seed, palette=palette, **bgkw)


def render_still(music, t, width=960, height=540, fps=30, start=0.0,
                 duration=None, seed=7, curve=True, palette="vert",
                 info=None, **bgkw):
    """Une seule image, pour juger d'une couleur ou d'un fond sans attendre
    un rendu complet."""
    info = info or analyze(music, start, duration)
    r = _renderer(info, width, height, fps, seed, curve, palette, bgkw)
    t = max(0.0, min(float(t), info["duration"] - 1.0 / fps))
    return frame_performance(r, t, info["duration"])


def render_video(music, out, start=0.0, duration=None, width=1920, height=1080,
                 fps=30, crf=None, jobs=None, seed=7, curve=True, palette="vert",
                 info=None, progress=None, quality="compatible", **bgkw):
    """Rend la video complete et y remet le son.

    `progress(done, total, elapsed)` est appele au fil de l'eau ; renvoie le
    dictionnaire d'analyse.
    """
    global _R, _DUR
    info = info or analyze(music, start, duration)
    dur = info["duration"]
    jobs = jobs or os.cpu_count() or 2

    souci = python_trop_petit(width, height)
    if souci:
        raise RuntimeError(souci)

    _DUR = dur
    _R = _renderer(info, width, height, fps, seed, curve, palette, bgkw)

    nframes = int(round(dur * fps))
    tmpdir = tempfile.mkdtemp(prefix="mpcperf_")
    wav = os.path.join(tmpdir, "track.wav")
    write_wav(wav, info["_audio"]["stereo"], info["_audio"]["sr"])

    outdir = os.path.dirname(os.path.abspath(out))
    if outdir:
        os.makedirs(outdir, exist_ok=True)

    entree = ["ffmpeg", "-y", "-hide_banner", "-loglevel", "error",
              "-f", "rawvideo", "-pix_fmt", "rgb24",
              "-s", "%dx%d" % (width, height), "-r", str(fps), "-i", "-",
              "-i", wav]
    if quality == "apercu":
        cmd = entree + APERCU["video"] + APERCU["audio"] + ["-shortest", out]
    else:
        q = QUALITES.get(quality, QUALITES["compatible"])
        cmd = entree + [
            "-c:v", "libx264", "-preset", "slow",
            "-crf", str(int(crf) if crf is not None else q["crf"]),
            "-pix_fmt", q["pix"], "-profile:v", q["profil"],
            "-movflags", "+faststart",
            # aq-mode 3 donne du debit aux zones sombres — ici tout le fond —
            # et un deblocage negatif evite que le filtre anti-blocs ne lisse
            # les traits fins en croyant corriger un artefact.
            "-x264-params", "keyint=%d:aq-mode=3:aq-strength=0.9:"
                            "psy-rd=1.2,0.2:deblock=-2,-2" % (fps * 2),
            "-colorspace", "bt709", "-color_primaries", "bt709",
            "-color_trc", "bt709",
            "-c:a", "aac", "-b:a", "192k", "-ac", "2", "-shortest", out]

    proc = subprocess.Popen(cmd, stdin=subprocess.PIPE)
    t0 = time.time()
    try:
        if jobs > 1:
            # Le moteur est construit : la memoire qu'il reste est celle dont
            # les taches disposeront vraiment.
            ctx = pool_context()
            demande, jobs = jobs, fit_jobs(jobs, width, height, dur,
                                           ctx.get_start_method())
            info["jobs"] = jobs
            if jobs < demande:
                # Ecrit dans la fenetre du studio, qui reste ouverte : un rendu
                # plus lent que prevu s'explique alors tout seul.
                print("  memoire disponible limitee : %d taches de rendu au lieu "
                      "de %d (fermez quelques fenetres pour aller plus vite)"
                      % (jobs, demande), flush=True)
        if jobs > 1:
            if ctx.get_start_method() == "spawn":
                # Les taches reconstruisent chacune leurs tables de
                # deformation, qu'on ne leur transmet pas ; le processus
                # principal ne dessine plus rien et n'a donc plus besoin des
                # siennes. Elles pesent 230 Mo en 4K.
                _R.forget_warp()
            chunk = max(jobs, 24)
            with ctx.Pool(jobs, initializer=_init_worker,
                          initargs=(_R, _DUR)) as pool:
                for s0 in range(0, nframes, chunk):
                    idx = range(s0, min(nframes, s0 + chunk))
                    for buf in pool.map(_worker, idx, chunksize=1):
                        proc.stdin.write(buf)
                    if progress:
                        progress(min(nframes, s0 + chunk), nframes, time.time() - t0)
        else:
            for i in range(nframes):
                proc.stdin.write(_worker(i))
                if progress and i % 12 == 0:
                    progress(i + 1, nframes, time.time() - t0)
    finally:
        proc.stdin.close()
        proc.wait()
    if proc.returncode:
        raise RuntimeError("ffmpeg a echoue (code %d)" % proc.returncode)
    if progress:
        progress(nframes, nframes, time.time() - t0)
    shutil.rmtree(tmpdir, ignore_errors=True)
    return info


# ==========================================================================
#  Ligne de commande
# ==========================================================================

def add_look_args(ap):
    """Options de rendu partagees avec le studio."""
    ap.add_argument("--preset", default=None, choices=sorted(PRESETS),
                    help="point de depart par famille de musique ; les autres "
                         "options passees restent prioritaires")
    ap.add_argument("--palette", default="vert", choices=sorted(PALETTES),
                    help="teinte du trait")
    ap.add_argument("--bg", default=None, choices=BACKGROUNDS,
                    help="fond de dalle (defaut : celui de la palette)")
    ap.add_argument("--bg-color", default=None,
                    help="couleur du fond en hexa, ex. #101828")
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
                    help="nombre de declenchements dans toute la video "
                         "(plafonne par la duree : un au plus toutes les 25 s)")
    ap.add_argument("--split-on", default="grosse caisse", choices=DECLENCHEURS, metavar="QUOI",
                    help="coups autorises a declencher le dedoublement")
    ap.add_argument("--machine", default="mpc", choices=sorted(MACHINES),
                    help="la machine dessinee : mpc, minifreak ou digitakt")
    ap.add_argument("--machines", default="", metavar="PLAN",
                    help="sequenceur de machines : a partir de quel instant "
                         "laquelle est a l'image, par exemple "
                         "\"0=mpc, 0:32=digitakt, 1:05=minifreak\". Vide, "
                         "c'est --machine du debut a la fin")
    ap.add_argument("--passage", type=float, default=1.9, metavar="S",
                    help="duree de la deformation d'une machine a l'autre, "
                         "en secondes (0 = changement sec)")
    ap.add_argument("--passage-turb", type=float, default=1.0, metavar="X",
                    help="ondulation du trace pendant le passage "
                         "(0 = deformation lisse)")
    ap.add_argument("--midi", default="", metavar="FICHIER",
                    help="fichier MIDI de la melodie : les touches du clavier "
                         "s'allument sur les vraies notes du morceau")
    ap.add_argument("--midi-offset", type=float, default=0.0, metavar="S",
                    help="decalage du fichier MIDI, en secondes, ajoute au "
                         "calage automatique (negatif = plus tot)")
    ap.add_argument("--midi-cale", type=int, default=0, choices=(0, 1),
                    help="1 : cherche le decalage du fichier MIDI en le "
                         "comparant aux attaques du morceau. Ne vaut que pour "
                         "un fichier percussif : sur une melodie il se trompe "
                         "a tous les coups. Par defaut le fichier est pris tel "
                         "quel, ce qui est juste pour un export du meme projet")
    ap.add_argument("--midi-transpose", type=int, default=None, metavar="N",
                    help="transposition en demi-tons ; par defaut, celle qui "
                         "met la melodie au milieu du clavier")
    ap.add_argument("--midi-force", type=float, default=1.0, metavar="X",
                    help="eclat des touches jouees (0 = aucune)")
    ap.add_argument("--neon", type=float, default=1.0,
                    help="force de l'eclairage du neon : 1 = d'origine, "
                         "2 = deux fois plus de lumiere autour du trait")
    ap.add_argument("--reflet", type=float, default=0.5,
                    help="proximite de la surface qui renvoie la lumiere : "
                         "0 = lointaine (lueur large et douce), 1 = collee "
                         "(lueur serree et vive)")
    ap.add_argument("--tube", type=float, default=0.0,
                    help="effet de tube de verre : bords assombris et reflet "
                         "le long du trait")
    ap.add_argument("--bg-anim", type=float, default=0.0,
                    help="animation de la texture de fond, en motifs par "
                         "seconde : les lignes descendent, le grain bout "
                         "(0 = fixe)")
    ap.add_argument("--taille", type=float, default=1.0,
                    help="taille de la machine dans l'image : 1 = d'origine, "
                         "0.6 = plus petite, 1.3 = plus grande")
    ap.add_argument("--presence", type=float, default=1.0,
                    help="presence de la machine : 1 = d'origine, 0.4 = "
                         "effacee derriere le fond")
    ap.add_argument("--nettete", type=float, default=1.0,
                    help="finesse du trait : 1 = d'origine, 1.5 = deux fois "
                         "plus fin")
    ap.add_argument("--step-div", type=float, default=2.0,
                    help="vitesse du sequenceur : 4 = double-croche (ancien), "
                         "2 = croche, 1 = noire")
    ap.add_argument("--glitch", type=float, default=1.0,
                    help="dosage des glitchs sur les paroxysmes (0 = aucun)")
    # ---- avaries d'image declenchees par la batterie
    ap.add_argument("--tranches", type=float, default=0.0,
                    help="bandes horizontales arrachees sur le coup")
    ap.add_argument("--tranches-on", default="caisse claire", choices=DECLENCHEURS, metavar="QUOI")
    ap.add_argument("--roll", type=float, default=0.0,
                    help="decrochage vertical du tube sur le coup")
    ap.add_argument("--roll-on", default="grosse caisse", choices=DECLENCHEURS, metavar="QUOI")
    ap.add_argument("--ghost", type=float, default=0.0,
                    help="image fantome decalee sur le coup")
    ap.add_argument("--ghost-on", default="caisse claire", choices=DECLENCHEURS, metavar="QUOI")
    ap.add_argument("--blocs", type=float, default=0.0,
                    help="blocs recopies ailleurs, facon flux abime")
    ap.add_argument("--blocs-on", default="caisse claire", choices=DECLENCHEURS, metavar="QUOI")
    ap.add_argument("--invert", type=float, default=0.0,
                    help="negatif bref sur le coup")
    ap.add_argument("--invert-on", default="grosse caisse", choices=DECLENCHEURS, metavar="QUOI")
    ap.add_argument("--stut", type=float, default=0.0,
                    help="begaiement : duree du gel de l'image, en secondes")
    ap.add_argument("--stut-on", default="charley", choices=DECLENCHEURS, metavar="QUOI")
    ap.add_argument("--stut-loop", type=float, default=0.05,
                    help="longueur du bout rejoue en boucle (s)")
    ap.add_argument("--scramble", type=float, default=0.0,
                    help="part des tranches de temps rejouees dans le desordre")
    ap.add_argument("--scr-len", type=float, default=0.14,
                    help="longueur d'une tranche de temps (s)")
    ap.add_argument("--miroir", type=float, default=0.0,
                    help="l'image se replie sur elle-meme sur le coup")
    ap.add_argument("--miroir-on", default="caisse claire", choices=DECLENCHEURS, metavar="QUOI")
    ap.add_argument("--ondul", type=float, default=0.0,
                    help="ondulation liquide du balayage")
    ap.add_argument("--ondul-on", default="basse", choices=DECLENCHEURS, metavar="QUOI")
    ap.add_argument("--mosaic", type=float, default=0.0,
                    help="pixelisation brutale sur le coup")
    ap.add_argument("--mosaic-on", default="caisse claire", choices=DECLENCHEURS, metavar="QUOI")
    ap.add_argument("--kaleido", type=float, default=0.0,
                    help="l'image repetee en grille sur le coup")
    ap.add_argument("--kaleido-on", default="caisse claire", choices=DECLENCHEURS, metavar="QUOI")
    ap.add_argument("--cisaille", type=float, default=0.0,
                    help="cisaillement diagonal sur le coup")
    ap.add_argument("--cisaille-on", default="caisse claire", choices=DECLENCHEURS, metavar="QUOI")
    ap.add_argument("--coupure", type=float, default=0.0,
                    help="l'image s'absente une image ou deux sur le coup")
    ap.add_argument("--coupure-on", default="grosse caisse", choices=DECLENCHEURS, metavar="QUOI")
    ap.add_argument("--tapestop", type=float, default=0.0,
                    help="duree du patinage de bande sur le coup (s)")
    ap.add_argument("--tapestop-on", default="grosse caisse", choices=DECLENCHEURS, metavar="QUOI")
    # ---- textures continues
    ap.add_argument("--cadence", type=int, default=0,
                    help="images tenues : 2 = 15 i/s, 3 = 10 i/s (0 = fluide)")
    ap.add_argument("--poussiere", type=float, default=0.0,
                    help="poussiere et rayures de pellicule")
    ap.add_argument("--flottement", type=float, default=0.0,
                    help="la bande flotte : lent va-et-vient de l'image")
    ap.add_argument("--halo-doux", type=float, default=0.0,
                    help="halo laiteux et noirs releves")
    ap.add_argument("--echo", type=float, default=0.0,
                    help="echos de la machine : force du premier (0 = aucun)")
    ap.add_argument("--echo-n", type=int, default=3, help="nombre d'echos")
    ap.add_argument("--echo-delay", type=float, default=0.045,
                    help="ecart entre deux echos, en secondes")
    ap.add_argument("--couleurs", type=float, default=0.0,
                    help="le trait prend la teinte de l'instrument frappe")
    ap.add_argument("--spectro", type=float, default=0.0,
                    help="spectrogramme deroulant sur la dalle (0 = aucun)")
    ap.add_argument("--snare", type=float, default=1.0,
                    help="embrasement jaune sur la caisse claire (0 = aucun)")
    ap.add_argument("--wave", type=float, default=1.10,
                    help="amplitude de la courbe sonore")
    ap.add_argument("--wave-win", type=float, default=0.070,
                    help="base de temps de la courbe (s) : large = mouvement lent")
    ap.add_argument("--wave-smooth", type=int, default=56,
                    help="lissage de la courbe : large = trace plus calme")
    ap.add_argument("--wave-trig", type=float, default=0.0,
                    help="balayage declenche : largeur d'ecran en temps (0 = libre)")
    ap.add_argument("--wave-passes", type=int, default=1,
                    help="passages de lissage (3 = trace nettement plus calme)")
    ap.add_argument("--wave-punch", type=float, default=0.85,
                    help="gonflement de la courbe sur les temps forts")
    ap.add_argument("--trail", type=float, default=1.0,
                    help="trainee de la bande (0 = trait net)")
    ap.add_argument("--title", default=None,
                    help="titre affiche sur la dalle (defaut : nom du fichier)")
    ap.add_argument("--backdrop", default=None,
                    help="image de fond (jpg, png, webp...)")
    ap.add_argument("--backdrop-strength", type=float, default=1.00)
    ap.add_argument("--backdrop-clear", type=float, default=0.28)
    ap.add_argument("--backdrop-sharp", type=float, default=0.37,
                    help="nettete du fond : 0 = fondu, 1 = net et pleine definition")
    ap.add_argument("--screen-dim", type=float, default=0.40,
                    help="opacite de la dalle devant l'image de fond")
    ap.add_argument("--travel", type=float, default=0.0,
                    help="travelling sur le fond : part de l'image parcourue "
                         "du debut a la fin (0.20 = 20%%)")
    ap.add_argument("--travel-mode", default="avant", choices=TRAVELLINGS,
                    help="sens du travelling")
    # ---- reactions au son : chacune se cale sur l'instrument de son choix
    ap.add_argument("--punch", type=float, default=0.032,
                    help="zoom d'impact sur chaque coup")
    ap.add_argument("--punch-on", default="grosse caisse", choices=DECLENCHEURS, metavar="QUOI")
    ap.add_argument("--shake", type=float, default=0.0,
                    help="secousse de l'image sur chaque coup")
    ap.add_argument("--shake-on", default="grosse caisse", choices=DECLENCHEURS, metavar="QUOI")
    ap.add_argument("--parts", type=float, default=0.0,
                    help="etincelles ejectees a chaque coup")
    ap.add_argument("--parts-on", default="caisse claire", choices=DECLENCHEURS, metavar="QUOI")
    ap.add_argument("--parts-n", type=int, default=14,
                    help="nombre d'etincelles par coup (jusqu'a 40000)")
    ap.add_argument("--parts-speed", type=float, default=1.0)
    ap.add_argument("--parts-life", type=float, default=0.55,
                    help="duree de vie d'une etincelle, en secondes")
    ap.add_argument("--ring", type=float, default=0.0,
                    help="onde de choc : un anneau qui s'ouvre sur le coup")
    ap.add_argument("--ring-on", default="grosse caisse", choices=DECLENCHEURS, metavar="QUOI")
    ap.add_argument("--grid-pulse", type=float, default=0.0,
                    help="la grille du fond s'allume sur le coup")
    ap.add_argument("--grid-on", default="grosse caisse", choices=DECLENCHEURS, metavar="QUOI")
    ap.add_argument("--bg-flash", type=float, default=0.0,
                    help="l'image de fond est eclairee par le coup")
    ap.add_argument("--flash-on", default="caisse claire", choices=DECLENCHEURS, metavar="QUOI")


def look_kwargs(args):
    """Les reglages d'allure, prereglage compris.

    Le prereglage n'est qu'un socle : une option donnee explicitement sur la
    ligne de commande passe apres lui et l'emporte. C'est ce qui permet de
    partir d'« idm » et de ne changer qu'une chose.
    """
    socle = dict(PRESETS.get(getattr(args, "preset", None) or "", {}))
    donnes = {a.lstrip("-").replace("-", "_")
              for a in sys.argv[1:] if a.startswith("--")}
    reglages = {"bg": args.bg,
            "bg_color": hex_to_rgb(args.bg_color) if args.bg_color else None,
            "bg_strength": args.bg_strength,
            "bg_clear": args.bg_clear,
            "wobble": args.wobble, "split": args.split, "split_px": args.split_px,
            "split_count": args.split_count, "split_on": args.split_on,
            "glitch": args.glitch, "step_div": args.step_div,
            "machine": args.machine,
            "machines": args.machines,
            "midi": args.midi,
            "midi_offset": args.midi_offset,
            "midi_cale": bool(args.midi_cale),
            "midi_transpose": args.midi_transpose,
            "midi_force": args.midi_force,
            "passage": args.passage,
            "passage_turb": args.passage_turb,
            "nettete": args.nettete, "taille": args.taille,
            "presence": args.presence, "neon": args.neon,
            "reflet": args.reflet, "tube": args.tube,
            "bg_anim": args.bg_anim,
            "tranches": args.tranches, "tranches_on": args.tranches_on,
            "roll": args.roll, "roll_on": args.roll_on,
            "ghost": args.ghost, "ghost_on": args.ghost_on,
            "blocs": args.blocs, "blocs_on": args.blocs_on,
            "invert": args.invert, "invert_on": args.invert_on,
            "stut": args.stut, "stut_on": args.stut_on,
            "stut_loop": args.stut_loop,
            "scramble": args.scramble, "scr_len": args.scr_len,
            "miroir": args.miroir, "miroir_on": args.miroir_on,
            "ondul": args.ondul, "ondul_on": args.ondul_on,
            "mosaic": args.mosaic, "mosaic_on": args.mosaic_on,
            "kaleido": args.kaleido, "kaleido_on": args.kaleido_on,
            "cisaille": args.cisaille, "cisaille_on": args.cisaille_on,
            "coupure": args.coupure, "coupure_on": args.coupure_on,
            "tapestop": args.tapestop, "tapestop_on": args.tapestop_on,
            "cadence": args.cadence, "poussiere": args.poussiere,
            "flottement": args.flottement, "halo_doux": args.halo_doux,
            "echo": args.echo, "echo_n": args.echo_n,
            "echo_delay": args.echo_delay, "couleurs": args.couleurs,
            "spectro": args.spectro,
            "snare": args.snare, "wave_gain": args.wave, "trail": args.trail,
            "wave_win": args.wave_win, "wave_smooth": args.wave_smooth,
            "wave_trig": args.wave_trig, "wave_passes": args.wave_passes,
            "wave_punch": args.wave_punch,
            "screen_dim": args.screen_dim,
            "backdrop_sharp": args.backdrop_sharp,
            "travel": args.travel, "travel_mode": args.travel_mode,
            "punch": args.punch, "punch_on": args.punch_on,
            "shake_amp": args.shake, "shake_on": args.shake_on,
            "parts": args.parts, "parts_on": args.parts_on,
            "parts_n": args.parts_n,
            "parts_speed": args.parts_speed, "parts_life": args.parts_life,
            "ring": args.ring, "ring_on": args.ring_on,
            "grid_pulse": args.grid_pulse, "grid_on": args.grid_on,
            "bg_flash": args.bg_flash, "flash_on": args.flash_on,
            "screen_title": (args.title if args.title is not None
                             else os.path.splitext(os.path.basename(args.music))[0]),
            "backdrop": args.backdrop,
            "backdrop_strength": args.backdrop_strength,
            "backdrop_clear": args.backdrop_clear}
    # ce que l'utilisateur n'a pas nomme, le prereglage le decide
    for k, v in socle.items():
        if k not in donnes:
            reglages[k] = v
    # La palette ne voyage pas avec les autres reglages : elle a son propre
    # argument, que la ligne de commande passe a part. Un prereglage qui la
    # fixe la depose donc la, sinon elle arriverait deux fois a destination.
    if "palette" in reglages:
        if "palette" not in donnes:
            args.palette = reglages["palette"]
        del reglages["palette"]
    return reglages


def main():
    ap = argparse.ArgumentParser(
        description="La MPC Live III joue un morceau, du debut a la fin")
    ap.add_argument("music", help="fichier audio a traiter")
    ap.add_argument("-o", "--out", default="mpc_performance.mp4")
    ap.add_argument("-W", "--width", type=int, default=1920)
    ap.add_argument("-H", "--height", type=int, default=1080)
    ap.add_argument("--fps", type=int, default=30)
    ap.add_argument("--start", type=float, default=0.0, help="depart dans le morceau (s)")
    ap.add_argument("--duration", type=float, default=None,
                    help="duree a traiter (par defaut : le morceau entier)")
    ap.add_argument("--crf", type=int, default=None,
                    help="qualite fine de l'encodage ; par defaut celle du "
                         "profil choisi")
    ap.add_argument("--quality", default="compatible", choices=sorted(QUALITES),
                    help="compatible (lit partout), net (trait plus fin, VLC "
                         "et montage), master (pour retravailler)")
    ap.add_argument("--jobs", type=int, default=os.cpu_count() or 2)
    ap.add_argument("--seed", type=int, default=7)
    ap.add_argument("--no-curve", action="store_true")
    ap.add_argument("--stills", default="")
    ap.add_argument("--still-times", default="")
    add_look_args(ap)
    args = ap.parse_args()

    try:
        info = analyze(args.music, args.start, args.duration)
    except ValueError as e:
        sys.exit(str(e))

    print("morceau %s  [%.2f -> %.2f s / %.2f s]  battement %.4f s (%.1f BPM)"
          "  %d coups  phase %.3f s  %d paroxysmes"
          % (args.music, info["start"], info["start"] + info["duration"],
             info["total"], info["beat"], info["bpm"], info["hits"],
             info["_phi"], len(info["drops"])), flush=True)
    if info["drops"]:
        print("  glitchs a : %s"
              % ", ".join("%.1fs" % d for d in info["drops"]), flush=True)

    look = look_kwargs(args)
    common = dict(width=args.width, height=args.height, fps=args.fps,
                  seed=args.seed, curve=not args.no_curve,
                  palette=args.palette, info=info, **look)

    if args.stills:
        from PIL import Image
        os.makedirs(args.stills, exist_ok=True)
        for ts in [float(x) for x in args.still_times.split(",") if x.strip()]:
            Image.fromarray(render_still(args.music, ts, **common)).save(
                os.path.join(args.stills, "t%06.2f.png" % ts))
            print("still %.2fs" % ts, flush=True)
        return

    def show(done, total, el):
        print("\r  %d/%d frames  %.0fs  (eta %.0fs)"
              % (done, total, el, el / max(done, 1) * (total - done)),
              end="", flush=True)

    render_video(args.music, args.out, crf=args.crf, quality=args.quality,
                 jobs=args.jobs,
                 progress=show, **common)
    print("\n%s  (%.1f s, %dx%d @ %dfps)"
          % (args.out, info["duration"], args.width, args.height, args.fps))


if __name__ == "__main__":
    main()
