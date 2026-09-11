#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Assemble le studio web : le gabarit + la geometrie exportee de la MPC.

Le resultat est un seul fichier HTML autonome — aucune dependance, aucun
serveur. Ouvert depuis le disque, il decode le morceau, l'analyse et dessine
la machine entierement dans le navigateur.

    python3 tools/build_web_studio.py -o out/studio-omnipotard.html
"""

import argparse
import json
import os
import subprocess
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
MARK = "/*__GEO__*/"


def main():
    ap = argparse.ArgumentParser(description="Construit le studio web autonome")
    ap.add_argument("-o", "--out", default="out/studio-omnipotard.html")
    ap.add_argument("--template", default=os.path.join(HERE, "web_studio_template.html"))
    args = ap.parse_args()

    with tempfile.TemporaryDirectory() as tmp:
        geo_path = os.path.join(tmp, "geo.json")
        subprocess.run([sys.executable, os.path.join(HERE, "export_geometry.py"),
                        "-o", geo_path], check=True)
        with open(geo_path, encoding="utf-8") as f:
            geo = f.read()
    json.loads(geo)                      # garde-fou : le gabarit l'inline tel quel

    with open(args.template, encoding="utf-8") as f:
        html = f.read()
    a = html.index(MARK)
    b = html.index(MARK, a + len(MARK)) + len(MARK)
    html = html[:a] + geo + html[b:]

    os.makedirs(os.path.dirname(os.path.abspath(args.out)), exist_ok=True)
    with open(args.out, "w", encoding="utf-8") as f:
        f.write(html)
    print("%s  (%.0f Ko)" % (args.out, os.path.getsize(args.out) / 1024))


if __name__ == "__main__":
    main()
