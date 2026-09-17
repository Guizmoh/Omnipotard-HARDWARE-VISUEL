#!/bin/bash
# Raccourci macOS / Linux : double-cliquer ce fichier lance le studio v2
# (moins de reglages a l'ecran, ranges par onglets ; tout reste la).
# (Sous macOS, le Finder ouvre un Terminal tout seul pour un .command.)

cd "$(dirname "$0")" || exit 1

PY=""
for c in python3 python; do
    command -v "$c" >/dev/null 2>&1 && { PY="$c"; break; }
done
if [ -z "$PY" ]; then
    echo "Python n'est pas installe."
    echo "A telecharger sur https://www.python.org/downloads/"
    echo
    read -r -p "Appuyez sur Entree pour fermer."
    exit 1
fi

# Deux dependances a poser, une seule fois : numpy pour tout le calcul
# d'image, pillow pour les fonds animes (les vignettes de la video).
if ! "$PY" -c "import numpy, PIL" >/dev/null 2>&1; then
    echo "Installation de numpy et pillow (une seule fois)..."
    "$PY" -m pip install --quiet numpy pillow || {
        echo "L'installation a echoue. Essayez :  $PY -m pip install numpy pillow"
        read -r -p "Appuyez sur Entree pour fermer."
        exit 1
    }
fi

echo "Demarrage du studio v2 — laissez cette fenetre ouverte."
echo "Pour arreter : Ctrl-C, ou fermez cette fenetre."
echo
"$PY" tools/studio.py --v2

# la fenetre reste ouverte si quelque chose s'est mal passe
echo
read -r -p "Le studio s'est arrete. Appuyez sur Entree pour fermer."
