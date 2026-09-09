# Glitch-visualisateur — intro OMNIPOTARD

Générateur d'une intro vidéo « oscilloscope » (vert fluo sur noir) pour les clips
**Omnipotard** : un balayage d'oscillateur dessine une MPC Live III, la machine
joue un groove dub, puis fond dans la forme d'onde du morceau — d'où le titre
sort, écrit d'un seul trait continu par la courbe audio.

![affiche](out/omnipotard_poster.png)

## Le déroulé (12,8 s — 4 mesures à 75 BPM, 1920×1080 @ 60 fps)

Tout est calé sur la grille musicale : le balayage se termine **pile sur le
drop**, et le titre apparaît **pile sur la mesure 4**.

| temps | séquence | ce qui se passe |
|---|---|---|
| 0,0 – 0,9 s | **amorce** | le réticule s'allume, la trace se stabilise sur la ligne de base |
| 0,9 – 3,2 s | **balayage** | le faisceau balaie l'écran ; l'onde de l'oscillateur s'écrase et laisse derrière elle le tracé de la **MPC Live III** |
| 3,2 – 8,0 s | **groove dub** | la machine joue : pads, bande de 16 pas, Q-Links, touch strip et écran bougent **sur les évènements réels de la bande-son** |
| 8,0 – 9,6 s | **dissolution** | break : la machine fond dans la forme d'onde du morceau |
| 9,6 – 11,2 s | **titre** | impact, puis un front de lecture **balaie de gauche à droite** : l'onde s'efface derrière lui et chaque lettre s'en détache, l'une après l'autre |
| 11,2 – 12,4 s | **maintien** | le nom reste dans la courbe, vibrant avec la musique |
| 12,4 – 12,8 s | **extinction** | collapse cathodique : l'image se referme sur une ligne puis un point |

## L'image est pilotée par le son

La bande-son est synthétisée par le même script (dub ambient : sub, one-drop,
skank sur les contretemps parti dans un écho à bande, nappe et réverbe). Elle
sert ensuite de **source d'animation** — il n'y a aucune synchronisation à
refaire à la main :

- la grande courbe **est** la forme d'onde du morceau (avec calibre automatique,
  comme un vrai oscilloscope) — et c'est d'elle que le logo sort : chaque point
  d'une lettre quitte la courbe au moment où le front le dépasse ;
- chaque pad s'allume sur l'évènement qui le déclenche : grosse caisse, rimshot,
  charley, notes de basse, accords ;
- la bande de 16 pas suit le pas courant du séquenceur ;
- les Q-Links, les bandeaux, le touch strip et les vu-mètres de l'écran suivent
  les enveloppes grave / medium / aigu.

## La machine

La silhouette reprend la **MPC Live III** : bande de 16 boutons de step-séquenceur
sur l'arête haute, écran tactile 7" à gauche, quatre Q-Links surmontés de leurs
bandeaux d'affichage, molette encastrée en haut à droite, grille de 16 pads MPCe
en bas à droite, et le touch strip vertical le long des pads.

## Rendu

```bash
pip install numpy pillow          # pillow seulement pour --stills
sudo apt install ffmpeg
python3 tools/omnipotard_intro.py -o out/omnipotard_intro_1080p60.mp4
```

Le rendu est **déterministe** (tout est graine + temps) : deux exécutions
donnent le même fichier au bit près. Environ 3 min sur 4 cœurs.

### Options utiles

```bash
# 4K
python3 tools/omnipotard_intro.py -W 3840 -H 2160 -o out/intro_4k.mp4

# format vertical (shorts / reels) — la machine reste cadrée automatiquement
python3 tools/omnipotard_intro.py -W 1080 -H 1920 -o out/intro_vertical.mp4

# images clés en PNG, pour vérifier avant d'encoder
python3 tools/omnipotard_intro.py --stills out/stills --still-times 3.4,4.6,8.6,10.6,11.6

# variantes
--duration 9.6      # toute la timeline ET le tempo se remettent à l'échelle
--fps 30            # ou 24, 50…
--crf 12            # qualité d'encodage (plus bas = plus gros)
--no-audio          # image seule, mais l'animation reste pilotée par le son
--no-curve          # supprime la courbure du tube cathodique
--seed 12           # change le grain et les décalages de glitch
--jobs 8            # nombre de processus de rendu
```

## Retoucher

Tout est dans `tools/omnipotard_intro.py`, en unités « demi-hauteur d'image »
(x ∈ [-1,78 ; 1,78], y ∈ [-1 ; 1], origine au centre) :

- **le mot** : `build_title_curve("OMNIPOTARD", …)` — l'alphabet monotrait est le
  dictionnaire `GLYPHS` (ajouter une lettre = ajouter ses traits) ;
- **la place du mot dans la courbe** : `TITLE_H`, `CURVE_AMP` (amplitude de
  l'onde), `CURVE_WIN` (base de temps affichée), `MOD_OF` (à quel point chaque
  partie du tracé ondule avec la musique) ;
- **la vitesse d'apparition des lettres** : `Renderer.title_front()` — le front
  ralentit sur la largeur du mot ; élargir le palier central espace les lettres ;
- **la musique** : `KICKS`, `RIMS`, `HATS`, `PERCS`, `SKANKS`, `BASSLINE` (motif
  de 16 pas), `NOTES` (la gamme), et les voix `kick()`, `rim()`, `skank()`,
  `bass()` dans `synth_audio()` ;
- **quel pad s'allume sur quoi** : `PAD_OF`, `PAD_BASS`, `PAD_SKANK` ;
- **la machine** : `build_mpc()` — chaque organe est un `Path` étiqueté
  (`body`, `step0…step15`, `lcd`, `qlink*`, `wheel`, `strip`, `pad0…pad15`) ;
- **la couleur** : `VERT_FLUO` (#39FF14) et `VERT_HALO` ;
- **le minutage** : `Timeline.KEYS` ; **les glitchs** : `GLITCHES`.

## Fichiers produits

| fichier | usage |
|---|---|
| `out/omnipotard_intro_1080p60_web.mp4` | version légère — partage, réseaux, prévisualisation |
| `out/omnipotard_poster.png` | image fixe du titre (vignette) |
| `out/omnipotard_intro_1080p60_hq.mp4` | qualité montage : `python3 tools/omnipotard_intro.py -o out/omnipotard_intro_1080p60_hq.mp4 --crf 20` |
| `out/omnipotard_intro_1080p60.mp4` | master sans compromis : `python3 tools/omnipotard_intro.py -o out/omnipotard_intro_1080p60.mp4 --crf 16` |

Les deux masters ne sont **pas versionnés** (poids) : le rendu étant déterministe,
les commandes ci-dessus les reproduisent à l'identique en ~3 min.
