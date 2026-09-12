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
    detect_hits, hex_to_rgb, load_backdrop, write_wav, PAD_OF,
)


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


def make_glitch_fn(drops, dur=0.22):
    """Meme langage visuel que l'intro : une rafale courte qui retombe."""
    d = np.asarray(drops, dtype=np.float64)

    def glitch_at(t):
        if len(d) == 0:
            return 0.0
        dt = t - d
        m = (dt >= 0.0) & (dt < dur)
        if not np.any(m):
            return 0.0
        return float(np.max(1.0 - dt[m] / dur))

    return glitch_at


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
    # les paroxysmes se lisent sur le signal brut : le fondu d'ouverture
    # ressemblerait sinon a une montee en puissance et ferait un faux glitch.
    drops = detect_drops(st.mean(axis=1), sr)
    audio = {"stereo": (mix * 32767).astype("<i2"), "mono": mono.astype(np.float32),
             "events": events, "sr": sr, "beat": beat}
    return audio, estimate_phase(events, beat / 4.0), drops




def make_performance_renderer(w, h, fps, duration, audio, phi, drops, curve=True,
                              seed=7, palette="vert", wobble=0.0, iris=1.0,
                              snare=1.0, wave_gain=2.6, trail=1.0, screen_title="",
                              backdrop=None, backdrop_strength=0.55,
                              backdrop_clear=0.45, **bgkw):
    r = Renderer(w, h, fps, duration, audio, curve=curve, seed=seed,
                 palette=palette, **bgkw)
    r.wobble, r.iris = float(wobble), float(iris)
    r.snare, r.wave_gain = float(snare), float(wave_gain)
    r.trail, r.screen_title = float(trail), str(screen_title or "")
    if backdrop:
        r.backdrop = load_backdrop(backdrop, w, h, backdrop_strength,
                                   backdrop_clear, scale=r.scale)
    # La machine est deja entierement deployee et joue en continu : on
    # neutralise tout ce qui, dans le moteur de l'intro, appartient au
    # scenario (reveal, pre-lueur, ecran qui se cache au zoom, extinction
    # cathodique programmee, glitchs d'intro etales sur toute la duree) sans
    # toucher au fichier partage — uniquement sur cette instance.
    far = (1e9, 1e9 + 1.0)
    r.tl.seg["groove"] = (-1e9, 1e9)   # pads/enveloppes actifs des t=0
    r.tl.seg["zoom"] = far             # l'ecran de la machine reste visible
    r.tl.seg["out"] = far              # pas d'extinction automatique
    r.step_index = lambda t: int((t - phi) / r.six) % 16   # phase reelle
    r.glitch_at = make_glitch_fn(drops)   # glitchs sur les paroxysmes du morceau
    return r


def frame_performance(r, t, duration):
    rng = np.random.default_rng(r.seed + int(t * r.fps + 0.5))
    r._zoom = 1.0 + 0.032 * r.kick_hit(t)     # respire sur chaque kick
    r._cam, r._cam_z = (0.0, 0.0), 1.0        # jamais de zoom dans l'ecran
    shake = r.glitch_at(t)

    beam = Beam(r.H, r.W, r.gain)
    r._grid(beam, t, 0.55, 1.0)
    r._hud(beam, t, 1.0, 0.65)
    # le fil du morceau passe derriere la machine et s'allume sur les graves
    r._wave_line(beam, t, 1.0, 0.48, None, 999.0, 0.0)
    r._machine(beam, t, 1.0, 999.0, 0.0, rng, shake)   # sweep_x enorme = deployee
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


def _renderer(info, width, height, fps, seed, curve, palette, bgkw):
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
                 fps=30, crf=20, jobs=None, seed=7, curve=True, palette="vert",
                 info=None, progress=None, **bgkw):
    """Rend la video complete et y remet le son.

    `progress(done, total, elapsed)` est appele au fil de l'eau ; renvoie le
    dictionnaire d'analyse.
    """
    global _R, _DUR
    info = info or analyze(music, start, duration)
    dur = info["duration"]
    jobs = jobs or os.cpu_count() or 2

    _DUR = dur
    _R = _renderer(info, width, height, fps, seed, curve, palette, bgkw)

    nframes = int(round(dur * fps))
    tmpdir = tempfile.mkdtemp(prefix="mpcperf_")
    wav = os.path.join(tmpdir, "track.wav")
    write_wav(wav, info["_audio"]["stereo"], info["_audio"]["sr"])

    outdir = os.path.dirname(os.path.abspath(out))
    if outdir:
        os.makedirs(outdir, exist_ok=True)

    cmd = ["ffmpeg", "-y", "-hide_banner", "-loglevel", "error",
           "-f", "rawvideo", "-pix_fmt", "rgb24",
           "-s", "%dx%d" % (width, height), "-r", str(fps), "-i", "-",
           "-i", wav,
           "-c:v", "libx264", "-preset", "slow", "-crf", str(crf),
           "-pix_fmt", "yuv420p", "-profile:v", "high", "-movflags", "+faststart",
           "-x264-params", "keyint=%d" % (fps * 2),
           "-c:a", "aac", "-b:a", "192k", "-ac", "2", "-shortest", out]

    proc = subprocess.Popen(cmd, stdin=subprocess.PIPE)
    t0 = time.time()
    try:
        if jobs > 1:
            import multiprocessing as mp
            chunk = max(jobs, 24)
            with mp.get_context("fork").Pool(jobs) as pool:
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
    ap.add_argument("--iris", type=float, default=1.0,
                    help="irisation sur les plus gros coups de sub (0 = aucune)")
    ap.add_argument("--snare", type=float, default=1.0,
                    help="embrasement jaune sur la caisse claire (0 = aucun)")
    ap.add_argument("--wave", type=float, default=2.6,
                    help="amplitude de la courbe sonore")
    ap.add_argument("--trail", type=float, default=1.0,
                    help="trainee de la bande (0 = trait net)")
    ap.add_argument("--title", default=None,
                    help="titre affiche sur la dalle (defaut : nom du fichier)")
    ap.add_argument("--backdrop", default=None,
                    help="image de fond (jpg, png, webp...)")
    ap.add_argument("--backdrop-strength", type=float, default=0.55)
    ap.add_argument("--backdrop-clear", type=float, default=0.45)


def look_kwargs(args):
    return {"bg": args.bg,
            "bg_color": hex_to_rgb(args.bg_color) if args.bg_color else None,
            "bg_strength": args.bg_strength,
            "bg_clear": args.bg_clear,
            "wobble": args.wobble, "iris": args.iris,
            "snare": args.snare, "wave_gain": args.wave, "trail": args.trail,
            "screen_title": (args.title if args.title is not None
                             else os.path.splitext(os.path.basename(args.music))[0]),
            "backdrop": args.backdrop,
            "backdrop_strength": args.backdrop_strength,
            "backdrop_clear": args.backdrop_clear}


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
    ap.add_argument("--crf", type=int, default=20)
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

    render_video(args.music, args.out, crf=args.crf, jobs=args.jobs,
                 progress=show, **common)
    print("\n%s  (%.1f s, %dx%d @ %dfps)"
          % (args.out, info["duration"], args.width, args.height, args.fps))


if __name__ == "__main__":
    main()
