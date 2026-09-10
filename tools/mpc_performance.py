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
import subprocess
import sys
import tempfile
import time

import numpy as np

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from omnipotard_intro import (  # noqa: E402 -- reutilise le moteur de l'intro
    SR, PALETTES, Renderer, Beam, _decode, detect_beat, detect_hits,
    write_wav, PAD_OF,
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
    audio = {"stereo": (mix * 32767).astype("<i2"), "mono": mono.astype(np.float32),
             "events": events, "sr": sr, "beat": beat}
    return audio, estimate_phase(events, beat / 4.0)


def make_performance_renderer(w, h, fps, duration, audio, phi, curve=True,
                               seed=7, palette="vert"):
    r = Renderer(w, h, fps, duration, audio, curve=curve, seed=seed, palette=palette)
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
    r.glitch_at = lambda t: 0.0        # pas de glitchs d'intro etires
    return r


def frame_performance(r, t, duration):
    rng = np.random.default_rng(r.seed + int(t * r.fps + 0.5))
    r._zoom = 1.0 + 0.020 * r.kick_hit(t)     # respire sur chaque kick
    r._cam, r._cam_z = (0.0, 0.0), 1.0        # jamais de zoom dans l'ecran

    beam = Beam(r.H, r.W, r.gain)
    r._grid(beam, t, 0.55, 1.0)
    r._hud(beam, t, 1.0, 0.65)
    r._machine(beam, t, 1.0, 999.0, 0.0, rng, 0.0)   # sweep_x enorme = deployee
    field = beam.render()
    img = r.colorize(field, t, 1.0, 0.0, rng)

    fade = min(1.0, t / 0.5) * min(1.0, (duration - t) / 0.6)
    if fade < 0.999:
        img = (img.astype(np.float32) * fade).astype(np.uint8)
    return img


_R = None
_DUR = 0.0


def _worker(i):
    return frame_performance(_R, i / _R.fps, _DUR).tobytes()


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
    ap.add_argument("--palette", default="vert", choices=sorted(PALETTES))
    ap.add_argument("--stills", default="")
    ap.add_argument("--still-times", default="")
    args = ap.parse_args()

    total = probe_duration(args.music)
    avail = max(0.0, total - args.start)
    duration = min(args.duration, avail) if args.duration else avail
    if duration <= 0.5:
        sys.exit("rien a traiter : depart (%.2fs) au-dela du morceau (%.2fs)"
                 % (args.start, total))

    t0 = time.time()
    audio, phi = load_full_track(args.music, args.start, duration)
    print("morceau %s  [%.2f -> %.2f s / %.2f s]  battement %.4f s (%.1f BPM)"
          "  %d coups  phase %.3f s  (analyse en %.1fs)"
          % (args.music, args.start, args.start + duration, total, audio["beat"],
             60.0 / audio["beat"], len(audio["events"]), phi, time.time() - t0),
          flush=True)

    global _R, _DUR
    _DUR = duration
    _R = make_performance_renderer(args.width, args.height, args.fps, duration,
                                   audio, phi, curve=not args.no_curve,
                                   seed=args.seed, palette=args.palette)

    if args.stills:
        from PIL import Image
        os.makedirs(args.stills, exist_ok=True)
        for ts in [float(x) for x in args.still_times.split(",") if x.strip()]:
            Image.fromarray(frame_performance(_R, ts, duration)).save(
                os.path.join(args.stills, "t%06.2f.png" % ts))
            print("still %.2fs" % ts, flush=True)
        return

    nframes = int(round(duration * args.fps))
    tmpdir = tempfile.mkdtemp(prefix="mpcperf_")
    wav = os.path.join(tmpdir, "track.wav")
    write_wav(wav, audio["stereo"], audio["sr"])

    cmd = ["ffmpeg", "-y", "-hide_banner", "-loglevel", "error",
           "-f", "rawvideo", "-pix_fmt", "rgb24",
           "-s", "%dx%d" % (args.width, args.height), "-r", str(args.fps), "-i", "-",
           "-i", wav,
           "-c:v", "libx264", "-preset", "slow", "-crf", str(args.crf),
           "-pix_fmt", "yuv420p", "-profile:v", "high", "-movflags", "+faststart",
           "-x264-params", "keyint=%d" % (args.fps * 2),
           "-c:a", "aac", "-b:a", "192k", "-ac", "2", "-shortest", args.out]

    proc = subprocess.Popen(cmd, stdin=subprocess.PIPE)
    t0 = time.time()
    try:
        if args.jobs > 1:
            import multiprocessing as mp
            chunk = max(args.jobs, 24)
            with mp.get_context("fork").Pool(args.jobs) as pool:
                for start in range(0, nframes, chunk):
                    idx = range(start, min(nframes, start + chunk))
                    for buf in pool.map(_worker, idx, chunksize=1):
                        proc.stdin.write(buf)
                    done = min(nframes, start + chunk)
                    el = time.time() - t0
                    print("\r  %d/%d frames  %.0fs  (eta %.0fs)"
                          % (done, nframes, el, el / done * (nframes - done)),
                          end="", flush=True)
        else:
            for i in range(nframes):
                proc.stdin.write(_worker(i))
    finally:
        proc.stdin.close()
        proc.wait()
    print("\n%s  (%.1f s, %dx%d @ %dfps)"
          % (args.out, duration, args.width, args.height, args.fps))


if __name__ == "__main__":
    main()
