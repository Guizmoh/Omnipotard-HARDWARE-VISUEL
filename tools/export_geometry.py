#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Exporte la geometrie de la MPC vers le studio web (tools/web_studio.py).

La machine est un dessin fixe : 113 chemins deja reechantillonnes a pas d'arc
constant. Plutot que de la redessiner en JavaScript — ou elle finirait par
deriver du moteur Python a la premiere retouche — on l'exporte telle quelle et
le navigateur se contente de la tracer. Le trait est donc identique au rendu
hors-ligne, au bruit de quantification pres.

Les points sont quantifies en entiers 16 bits (echelle 16000, soit une
precision de 6e-5 unite : 0,07 pixel en 4K) puis encodes en base64.

    python3 tools/export_geometry.py -o out/geometrie.json
"""

import argparse
import base64
import json
import os
import sys

import numpy as np

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from omnipotard_intro import (  # noqa: E402
    BODY, SCREEN, PALETTES, PAD_OF, DECAY_OF, STEP_LIT, QLINK, QLINK_R, STRIP,
    WHEEL, WHEEL_R, PAD_X0, PAD_Y0, PAD_W, PAD_H, PAD_GX, PAD_GY,
    STEP_X0, STEP_Y0, STEP_W, STEP_H, STEP_GAP, CURVE_WIN, STEP,
    build_mpc, pad_fill, step_rect, rect_fill, text_paths,
)

SCALE = 16000.0


def pack(P):
    """Polyligne -> entiers 16 bits en base64."""
    q = np.clip(np.round(np.asarray(P, dtype=np.float64) * SCALE),
                -32768, 32767).astype("<i2")
    return base64.b64encode(q.tobytes()).decode("ascii")


def main():
    ap = argparse.ArgumentParser(description="Geometrie de la MPC -> JSON")
    ap.add_argument("-o", "--out", default="out/geometrie.json")
    args = ap.parse_args()

    paths = []
    for p in build_mpc():
        # s est une graduation reguliere : on ne transporte que sa longueur
        paths.append({"tag": p.tag, "ph": round(p.ph, 6),
                      "len": round(float(p.s[-1]), 6), "xy": pack(p.P)})

    # remplissages des organes animes, calcules une fois pour toutes
    fills = {
        "pad": [pack(pad_fill(k)) for k in range(16)],
        "step": [pack(rect_fill(*step_rect(k), 4)) for k in range(16)],
    }

    hud = {name: [pack(q.P) for q in text_paths(name, 0.055, x, 0.90, center=False)]
           for name, x in (("OSC", -1.66), ("SYNC", 1.34))}

    data = {
        "scale": SCALE,
        "step": STEP,
        "paths": paths,
        "fills": fills,
        "hud": hud,
        "const": {
            "BODY": list(BODY), "SCREEN": list(SCREEN), "STRIP": list(STRIP),
            "QLINK": [list(q) for q in QLINK], "QLINK_R": QLINK_R,
            "WHEEL": list(WHEEL), "WHEEL_R": WHEEL_R,
            "PAD": [PAD_X0, PAD_Y0, PAD_W, PAD_H, PAD_GX, PAD_GY],
            "STEPB": [STEP_X0, STEP_Y0, STEP_W, STEP_H, STEP_GAP],
            "STEP_LIT": sorted(STEP_LIT),
            "PAD_OF": PAD_OF, "DECAY_OF": DECAY_OF,
            "CURVE_WIN": CURVE_WIN,
            "PALETTES": {k: [list(c) for c in v] for k, v in PALETTES.items()},
        },
    }

    os.makedirs(os.path.dirname(os.path.abspath(args.out)), exist_ok=True)
    with open(args.out, "w", encoding="utf-8") as f:
        json.dump(data, f, separators=(",", ":"))
    n = sum(len(base64.b64decode(p["xy"])) // 4 for p in paths)
    print("%s  %d chemins, %d points, %.0f Ko"
          % (args.out, len(paths), n, os.path.getsize(args.out) / 1024))


if __name__ == "__main__":
    main()
