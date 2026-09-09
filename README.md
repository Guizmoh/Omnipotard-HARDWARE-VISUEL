# Glitch-visualisateur — intro OMNIPOTARD

Générateur d'une intro vidéo « oscilloscope » (vert fluo sur noir) pour les clips
**Omnipotard** : un balayage d'oscillateur dessine une MPC Live III, la machine
joue un groove dub, puis fond dans la forme d'onde du morceau — d'où le titre
sort, écrit d'un seul trait continu par la courbe audio.

![affiche](out/omnipotard_poster.png)

## Le déroulé (11 s — 16 temps à ~87 BPM, 1920×1080 @ 60 fps)

Tout est calé sur la grille musicale : le balayage se termine **pile sur le
drop**, et le titre apparaît **pile sur l'impact**.

| temps | séquence | ce qui se passe |
|---|---|---|
| 0,0 – 1,03 s | **enregistrement** | une piste s'enregistre comme dans une station de travail : cadre de clip, bandeau « AUDIO 01 », règle temporelle, témoin REC, et la **forme d'onde du morceau** qui se remplit de gauche à droite derrière la tête d'enregistrement |
| 1,03 – 2,75 s | **transformation** | le clip **se déplie en MPC Live III** : chaque point de la machine part écrasé dans l'enveloppe de la forme d'onde et s'ouvre à sa place, de gauche à droite, pendant que le clip s'efface d'autant. La zone de mue est large — à chaque instant une bonne partie de la machine est en train de s'ouvrir — et un liseré annonce les organes avant qu'ils ne se déploient |
| 2,75 – 5,5 s | **groove dub** | drop : la machine joue, pads, bande de 16 pas, Q-Links, touch strip et écran bougent **sur les évènements réels de la bande-son** |
| 5,5 – 6,88 s | **break** | souffle ; la machine fond dans la forme d'onde du morceau |
| 6,88 – 9,63 s | **titre** | impact, puis un front de lecture **balaie lentement de gauche à droite** : les lettres se détachent du fil d'onde une par une (une par double-croche), puis **HARDWARE ONLY** passe sous le nom |
| 9,63 – 10,65 s | **maintien** | le logo repose sur le fil, qui continue de vibrer avec la musique |
| 10,65 – 11,0 s | **extinction** | **rafales de glitch** (déchirures, datamosh, pertes de signal, décalage RVB), souffle de sortie, puis collapse cathodique |

## L'image est pilotée par le son

La bande-son est synthétisée par le même script (dub ambient : sub, one-drop,
skank sur les contretemps parti dans un écho à bande, nappe et réverbe, plus
les risers woosh d'entrée, de break et de sortie). Elle
sert ensuite de **source d'animation** — il n'y a aucune synchronisation à
refaire à la main :

- la grande courbe **est** la forme d'onde du morceau (avec calibre automatique,
  comme un vrai oscilloscope) — et c'est d'elle que le logo sort : chaque point
  d'une lettre quitte la courbe au moment où le front le dépasse. Le fil traverse
  toute l'image et le mot est **posé dessus** : logo et onde ne font qu'un trait ;
- le clip d'ouverture affiche l'enveloppe crête **du morceau lui-même** : on y
  voit le drop, le groove, le break, l'impact et la traîne ;
- chaque pad s'allume sur l'évènement qui le déclenche : grosse caisse, rimshot,
  charley, notes de basse, accords ;
- la bande de 16 pas suit le pas courant du séquenceur ;
- les Q-Links, les bandeaux, le touch strip et les vu-mètres de l'écran suivent
  les enveloppes grave / medium / aigu.

## La machine

Disposition relevée sur une photo de dessus de la **MPC Live III**, simplifiée :
potard de volume au coin haut gauche, bande de 16 boutons de step-séquenceur sur
l'arête haute, touch strip vertical sur l'arête gauche, grille de 16 pads MPCe
biseautés au centre gauche, écran tactile 7" à droite, colonne de quatre Q-Links
sur l'arête droite, molette en bas à droite, rangées de touches sous l'écran,
marquage et grille de haut-parleur en bas.

## Rendu

```bash
pip install numpy pillow          # pillow seulement pour --stills
sudo apt install ffmpeg
python3 tools/omnipotard_intro.py -o out/omnipotard_intro_1080p60.mp4
```

Le rendu est **déterministe** (tout est graine + temps) : deux exécutions
donnent le même fichier au bit près. Environ 2 min 15 sur 4 cœurs.

### Options utiles

```bash
# 4K
python3 tools/omnipotard_intro.py -W 3840 -H 2160 -o out/intro_4k.mp4

# format vertical (shorts / reels) — la machine reste cadrée automatiquement
python3 tools/omnipotard_intro.py -W 1080 -H 1920 -o out/intro_vertical.mp4

# images clés en PNG, pour vérifier avant d'encoder
python3 tools/omnipotard_intro.py --stills out/stills --still-times 0.9,2.1,4.2,8.4,10.0

# variantes
--duration 8        # toute la timeline se remet à l'échelle (le tempo reste dans les 80-90 BPM)
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
  dictionnaire `GLYPHS` (ajouter une lettre = ajouter ses traits) ; chaque glyphe
  doit toucher `y = 0` pour rester accroché au fil ;
- **l'amortissement de l'onde sous le mot** : `Renderer.wave_mod()` ;
- **la mue du clip en machine** : `Renderer.morph_at()` (élargir le dénominateur ralentit l'ouverture de chaque organe) et `clip_env()` ;
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
- **le sous-titre** : `SUB_TXT`, `SUB_H`, `SUB_TRACK` ;
- **le minutage** : `Timeline.KEYS` ; **les glitchs** : `GLITCHES` pour les coups
  ponctuels, `Renderer.glitch_at()` pour les rafales de l'extinction ;
- **le clip d'ouverture** : `CLIP`, `WAVE_YMAX`, `DAW_COLS` et
  `Renderer._daw_clip()` ;
- **les souffles** : `_whoosh()` (montant ou descendant) — bruit filtré seul,
  sans composante tonale.

## Fichiers produits

| fichier | usage |
|---|---|
| `out/omnipotard_intro_1080p60_web.mp4` | version légère — partage, réseaux, prévisualisation |
| `out/omnipotard_poster.png` | image fixe du titre (vignette) |
| `out/omnipotard_intro_1080p60.mp4` | master CRF 19 (~25 Mo) pour le montage — **non versionné** : `python3 tools/omnipotard_intro.py -o out/omnipotard_intro_1080p60.mp4 --crf 19` |

Le rendu étant déterministe, cette commande reproduit le master à l'identique.
