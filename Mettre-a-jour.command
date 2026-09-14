#!/bin/bash
# Met a jour le studio : recupere la derniere version depuis GitHub.
# Rien a installer, pas besoin de git : le depot est public, on telecharge
# l'archive et on remplace le code. Le dossier out (vos morceaux, vos fonds
# et vos videos) n'est jamais touche.

cd "$(dirname "$0")" || exit 1

ZIPURL="https://github.com/Guizmoh/Glitch-visualisateur/archive/refs/heads/claude/omnipotard-intro-video-5bj8zu.zip"
RACINE="Glitch-visualisateur-claude-omnipotard-intro-video-5bj8zu"

# Ce fichier doit etre DANS le dossier du projet, a cote de
# Lancer-le-studio.command. Lance ailleurs, il y deverserait tout le projet.
if [ ! -f tools/omnipotard_intro.py ]; then
    echo "Ce fichier doit etre place DANS le dossier du projet,"
    echo "a cote de Lancer-le-studio.command, puis lance de la."
    echo "Dossier actuel : $(pwd)"
    read -r -p "Appuyez sur Entree pour fermer."
    exit 1
fi

echo "Mise a jour du studio Omnipotard."
echo "Si la fenetre du studio est encore ouverte, fermez-la."
echo
echo "Version actuellement installee :"
grep '^VERSION = ' tools/omnipotard_intro.py || echo "  (inconnue)"
echo

TMP="$(mktemp -d)"
trap 'rm -rf "$TMP"' EXIT

echo "Telechargement..."
if ! curl -fsSL "$ZIPURL" -o "$TMP/maj.zip"; then
    echo "Echec du telechargement (pas de connexion ?)."
    read -r -p "Appuyez sur Entree pour fermer."
    exit 1
fi
if ! unzip -q "$TMP/maj.zip" -d "$TMP"; then
    echo "L'archive n'a pas pu etre ouverte."
    read -r -p "Appuyez sur Entree pour fermer."
    exit 1
fi

SRC="$TMP/$RACINE"
[ -d "$SRC/tools" ] || { echo "Archive inattendue."; exit 1; }

mkdir -p tools
cp -f "$SRC"/tools/* tools/
for f in README.md requirements.txt Lancer-le-studio.bat Lancer-le-studio.command \
         Mettre-a-jour.bat .gitattributes; do
    [ -f "$SRC/$f" ] && cp -f "$SRC/$f" .
done
chmod +x ./*.command 2>/dev/null

echo
echo "C'est fait. Version maintenant installee :"
grep '^VERSION = ' tools/omnipotard_intro.py
echo
read -r -p "Vous pouvez relancer le studio. Appuyez sur Entree pour fermer."
