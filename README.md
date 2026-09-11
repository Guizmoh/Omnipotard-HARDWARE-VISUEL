# Glitch-visualisateur — intro OMNIPOTARD

Générateur d'une intro vidéo « oscilloscope » (vert fluo sur noir) pour les clips
**Omnipotard** : un balayage d'oscillateur dessine une MPC Live III, la machine
joue un groove dub, puis fond dans la forme d'onde du morceau — d'où le titre
sort, écrit d'un seul trait continu par la courbe audio.

![affiche](out/omnipotard_poster.png)

## Le déroulé (11,5 s — 15 temps + 1 s de maintien, calé sur le morceau)

Tout est calé sur la grille musicale : le balayage se termine **pile sur le
drop**, et le titre apparaît **pile sur l'impact**.

| temps | séquence | ce qui se passe |
|---|---|---|
| 0,0 – 1,06 s | **enregistrement** | une piste s'enregistre comme dans une station de travail : cadre de clip, bandeau « AUDIO 01 », règle temporelle, témoin REC, et la **forme d'onde du morceau** qui se remplit derrière la tête d'enregistrement. Le morceau joue, mat et un peu en retrait |
| 1,06 – 3,17 s | **transformation** | le clip **se déplie en MPC Live III** — chaque point de la machine part écrasé dans l'enveloppe de la forme d'onde et s'ouvre à sa place |
| 2,82 – 5,63 s | **groove** | le morceau s'ouvre en grand, avant la fin de la mue : la machine joue déjà pendant que son flanc droit finit de se déployer |
| 5,63 – 6,34 s | **entrée dans l'écran** | la caméra **plonge dans la dalle 7" de la MPC**. La machine ne se dissout plus : on y entre. Le morceau est évidé de ses graves, un souffle monte |
| 6,34 – 9,15 s | **titre** | **sur l'écran de la machine** : impact, puis un front de lecture balaie de gauche à droite pendant quatre temps — une lettre par double-croche — et les lettres se détachent du fil d'onde une par une, puis la ligne de bas de casse passe sous le nom |
| 9,15 – 11,21 s | **maintien** | le logo repose sur le fil, dans la dalle, encadré par le boîtier — pads à gauche, Q-Links à droite (+1 s par rapport aux versions précédentes) |
| 11,21 – 11,50 s | **extinction** | rafales de glitch adoucies, souffle de sortie, puis collapse cathodique — moins abrupte que la version précédente |

Le zoom de caméra et l'échelle de la composition sont inverses l'un de l'autre
(`SCR_S × CAM_Z = 1`) : le logo garde exactement la même taille à l'image
qu'avant, seul le cadre change. Et comme un trait du monde s'étale sur d'autant
plus de pixels une fois la caméra avancée, son intensité est compensée par le
zoom — sinon la machine s'assombrissait en approchant.

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
- **les pads suivent la vraie batterie du morceau** : le script détecte les
  attaques par bande (grave → grosse caisse, medium → caisse claire et
  percussions, aigu → charley) et chaque famille garde son pad, pour qu'on
  reconnaisse l'instrument à l'endroit où il s'allume ;
- la bande de 16 pas suit le pas courant du séquenceur ;
- le fil d'onde **s'allume légèrement à chaque coup grave** (grosse caisse et
  notes de basse), avec un halo qui s'ajoute au trait ;
- **l'image respire sur chaque grosse caisse** pendant que la machine joue :
  un zoom d'environ 1,5 % qui se relâche en un quart de seconde ;
- **le tracé de la machine tremble** en permanence, comme une trace
  d'oscilloscope : une ondulation lente le long du parcours plus une ride fine,
  chaque organe avec sa propre phase, amplifiée quand le grave pousse ;
- pendant le groove, le fil **passe derrière la machine** : il entre par le bord
  gauche, disparaît sous le châssis et ressort à droite — la MPC est un morceau
  de la bande ;
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

# ffmpeg, selon la machine :
brew install ffmpeg               # macOS
winget install Gyan.FFmpeg        # Windows
sudo apt install ffmpeg           # Linux

# placer le morceau dans assets/hint.mp3 (non versionné), puis :
python3 tools/omnipotard_intro.py -o out/omnipotard_intro_1080p60.mp4
```

Le morceau n'est **pas versionné** : il faut le déposer dans `assets/`. Sans
lui, le script bascule tout seul sur la bande-son de synthèse.

Le rendu est **déterministe** (tout est graine + temps) : deux exécutions
donnent le même fichier au bit près. Environ 2 min sur 4 cœurs.

### Options utiles

```bash
# 4K (le rendu est plus long, mais la meme luminosite qu'en 1080p)
python3 tools/omnipotard_intro.py -W 3840 -H 2160 -o out/intro_4k.mp4

# format vertical (shorts / reels) — la machine reste cadrée automatiquement
python3 tools/omnipotard_intro.py -W 1080 -H 1920 -o out/intro_vertical.mp4

# images clés en PNG, pour vérifier avant d'encoder
python3 tools/omnipotard_intro.py --stills out/stills --still-times 0.6,1.9,2.85,4.3,8.9

# variantes
--duration 8        # toute la timeline se remet à l'échelle (le tempo reste dans les 80-90 BPM)
--fps 30            # ou 24, 50…
--crf 12            # qualité d'encodage (plus bas = plus gros)
--palette bleu      # vert (défaut), orange (rouge/orange), bleu, bleu-fond
--subtitle "DAWLESS MUSIC"   # la ligne sous le logo
--music autre.mp3   # un autre morceau (le tempo et la batterie sont redétectés)
--music-start 41.2  # où commencer l'extrait, à caler sur une barre de mesure
--synth             # revenir à la bande-son entièrement synthétisée
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
- **la mue du clip en machine** : `Renderer.morph_at()` (élargir le dénominateur
  ralentit l'ouverture de chaque organe) et `clip_env()` ;
- **le fil masqué par la machine** : `Renderer.body_mask()` ;
- **le halo sur les graves** : `Renderer.bass_hit()` ;
- **le zoom sur les kicks** : `Renderer.kick_hit()` et le facteur `_zoom`
  appliqué dans `to_px()` ;
- **le tremblement du trait** : la variable `wob` dans `Renderer._machine()`
  (les deux amplitudes) et le facteur `trem` qui la module ;
- **le recouvrement groove / mue** : les bornes `sweep` et `groove` de
  `Timeline.KEYS` se chevauchent volontairement ;
- **la place du mot dans la courbe** : `TITLE_H`, `CURVE_AMP` (amplitude de
  l'onde), `CURVE_WIN` (base de temps affichée), `MOD_OF` (à quel point chaque
  partie du tracé ondule avec la musique) ;
- **la vitesse d'apparition des lettres** : `Renderer.title_front()` — le front
  ralentit sur la largeur du mot ; élargir le palier central espace les lettres ;
- **le montage du morceau** : `load_music()` — les courbes `a_dull`, `a_thin` et
  `gain` sont l'automation (étouffé, évidé, plein) ; `MUSIC_START` choisit
  l'extrait ;
- **la détection de la batterie** : `detect_hits()` (seuils et écart minimum par
  bande) et `detect_beat()` ;
- **la musique de synthèse** (`--synth`) : `KICKS`, `RIMS`, `HATS`, `SKANKS`,
  `BASSLINE`, `NOTES` et les voix de `synth_audio()` ;
- **quel pad s'allume sur quoi** : `PAD_OF`, `PAD_BASS`, `PAD_SKANK` ;
- **la machine** : `build_mpc()` — chaque organe est un `Path` étiqueté
  (`body`, `step0…step15`, `lcd`, `qlink*`, `wheel`, `strip`, `pad0…pad15`) ;
- **les couleurs** : le dictionnaire `PALETTES` — cœur du trait, halo, cœur
  sur-exposé, fond de dalle. Le fond passe **sous** les textures, donc les
  scanlines, le vignettage et le grain le travaillent comme le reste de l'image ;
- **le sous-titre** : `--subtitle`, et `SUB_H` / `SUB_TRACK` pour sa taille et sa chasse ;
- **l'entrée dans l'écran** : `SCR_IN`, `CAM_Z`, `Renderer.in_screen()` (la
  découpe de la dalle) et la caméra dans `to_px()` ;
- **le minutage** : `Timeline.KEYS` ; **les glitchs** : `GLITCHES` pour les coups
  ponctuels, `Renderer.glitch_at()` pour les rafales de l'extinction ;
- **le clip d'ouverture** : `CLIP`, `WAVE_YMAX`, `DAW_COLS` et
  `Renderer._daw_clip()` ;
- **les souffles** : `_whoosh()` — quatre bandes de bruit dont la dernière est
  un vrai passe-haut, sans composante tonale ; une réverbe leur est appliquée à
  part (dosée bas, pour rester discrète) — c'est elle qui les rend aériens ;
  le souffle qui accompagne la matérialisation de la machine ne joue que
  pendant la fenêtre `sweep` ;
- **l'impact du titre** : très en retrait, avec un souffle léger qui atterrit
  à sa place dans `load_music()` ;
- **l'extinction finale** : `_tv_off()` (audio) et le burst de `glitch_at()` +
  la formule de `collapse` dans `intensity()` (visuel) ;

## Versions livrées

| fichier | ce qui change |
|---|---|
| `omnipotard_intro_1080p60.mp4` | la référence : vert, « HARDWARE ONLY » |
| `omnipotard_dawless.mp4` | « DAWLESS MUSIC » sous le logo |
| `omnipotard_bleu_fond.mp4` | fond bleu travaillé par la texture cathodique |
| `omnipotard_rouge_orange.mp4` | tracé orange, halo rouge |
| `omnipotard_bleu.mp4` | tracé bleu — la version demandée en dernier |

Les options se combinent : `--palette orange --subtitle "DAWLESS MUSIC"`.

## Fichiers produits

| fichier | usage |
|---|---|
| `out/omnipotard_intro_1080p60_web.mp4` | version légère — partage, réseaux, prévisualisation |
| `out/omnipotard_poster.png` | image fixe du titre (vignette) |
| `out/omnipotard_intro_1080p60.mp4` | master CRF 19 (~22 Mo) pour le montage — **non versionné** : `python3 tools/omnipotard_intro.py -o out/omnipotard_intro_1080p60.mp4 --crf 19` |

Le rendu étant déterministe, cette commande reproduit le master à l'identique.

## MPC PERFORMANCE — la machine joue le morceau en entier

`tools/mpc_performance.py` reutilise le meme moteur (geometrie, detection de
batterie, rendu du faisceau) pour un usage different de l'intro : donner un
morceau — n'importe lequel, n'importe quelle duree — et obtenir une video ou
la MPC Live III le joue du debut a la fin. Pas de scenario (pas de clip qui
s'enregistre, pas de titre, pas de zoom) : la machine est deployee des la
premiere image, un simple fondu ouvre et ferme la video.

```bash
python3 tools/mpc_performance.py assets/hint.mp3 -o out/mpc_performance.mp4

# apercu rapide avant de lancer le morceau entier
python3 tools/mpc_performance.py assets/hint.mp3 --start 20 --duration 15 -o out/preview.mp4
```

- Le tempo et la phase de la bande de 16 pas sont recales sur les vraies
  grosses caisses du morceau (`detect_beat`, `estimate_phase`).
- Les pads s'allument sur les coups reels (`detect_hits`, deja utilise dans
  l'intro) : grave -> grosse caisse, medium -> caisse claire/percu, aigu ->
  charley.
- L'image respire a chaque grosse caisse (zoom +3,2 %) et le fil d'onde
  passe derriere la machine, accroche de chaque cote, avec son halo sur les
  graves — exactement comme dans l'intro.
- Les glitchs ne tombent pas au hasard : `detect_drops` repere les
  paroxysmes du morceau — les instants ou il repart en force apres une
  respiration — en comparant l'energie a celle d'une seconde et demie plus
  tot. Il ne suffit pas d'etre fort, il faut arriver fort. Sur *Hint*, cela
  donne une vingtaine de rafales sur 4 minutes. Les seuils se reglent dans
  `detect_drops` (`thresh`, `rise`, `min_gap`) ; les instants retenus sont
  affiches au lancement.
- `--palette` (vert/orange/bleu/bleu-fond), `--fps`, `-W/-H` fonctionnent
  comme dans l'intro. Par defaut 30 fps (un morceau entier est long a
  rendre ; 60 fps double le temps de calcul pour un gain surtout sensible
  sur les mouvements rapides de l'intro).
- Rendu non temps reel : c'est un pipeline hors-ligne (image par image, puis
  encodage), pas un instrument qui reagirait en direct a un micro ou une
  entree ligne — ce dernier est un projet different (capture audio et
  affichage en direct sur votre machine), que ce script ne couvre pas.

## STUDIO — l'atelier local

`tools/studio.py` est la version « on charge son morceau et on voit » des deux
scripts ci-dessus : pas de ligne de commande, une page dans le navigateur.

```bash
git clone https://github.com/Guizmoh/Glitch-visualisateur.git
cd Glitch-visualisateur
pip install numpy                 # ffmpeg : voir « Rendu » plus haut
python3 tools/studio.py
# le navigateur s'ouvre sur http://127.0.0.1:8765
```

L'adresse est **locale** : elle ne marche que sur la machine qui fait tourner
la commande. Il n'y a pas de version en ligne, et c'est voulu — le studio
décode l'audio avec ffmpeg, calcule chaque image avec numpy puis encode en
x264, tout cela sur vos fichiers. Un navigateur seul ne sait pas faire ça, et
il faudrait de toute façon envoyer vos morceaux sur un serveur.

On y dépose un morceau (mp3, wav, flac, m4a…), on choisit la couleur du trait
et le fond, et **l'aperçu se recalcule à chaque réglage** — c'est une vraie
image du rendu, pas une simulation : ce qu'on voit est ce qu'on obtient. Le
bouton *aller au prochain paroxysme* saute là où tomberont les glitchs, pour
les juger avant de lancer quoi que ce soit.

Le rendu se lance depuis la même page, avec une barre de progression et un
bouton de téléchargement. Tout ce que le studio fabrique (morceaux déposés et
vidéos) reste dans `out/studio/`.

Rien ne sort de la machine : le serveur n'écoute que sur `127.0.0.1`, il n'y a
ni bibliothèque web ni CDN — la page est servie telle quelle, et les seules
dépendances sont celles du reste du projet (numpy et ffmpeg).

### Couleur

Les quatre palettes du catalogue sont là, plus un mode **couleur libre** : on
choisit une teinte, et le moteur en dérive les trois couleurs dont il a besoin
— le cœur du trait, son halo, et sa version sur-exposée. C'est ce triplet qui
donne au trait son allure de phosphore plutôt que de ligne peinte.

### Fond

Le faisceau est **additif** : un fond clair mange le contraste du trait. Les
six textures restent donc sombres, et surtout elles se **creusent derrière la
machine** — c'est le curseur *dégagement*, qui va de 0 (texture uniforme) à 1
(plus rien derrière la machine). C'est ce qui permet de mettre un fond coloré
sans que la machine s'y noie.

| fond | ce que c'est |
| --- | --- |
| `noir` | rien, comme avant |
| `uni` | une couleur pleine |
| `grille` | papier millimétré d'oscilloscope, trait fort toutes les 5 cases |
| `points` | trame de points aux intersections |
| `scan` | lignes de tube serrées |
| `degrade` | sombre au centre, coloré vers les bords — le regard va au milieu |
| `bruit` | un grain fixe, une matière |

Les mêmes réglages existent en ligne de commande sur les deux autres scripts :
`--bg`, `--bg-color`, `--bg-strength`, `--bg-clear`.

```bash
python3 tools/omnipotard_intro.py --palette bleu --bg grille --bg-color '#123a5c'
python3 tools/mpc_performance.py assets/hint.mp3 --bg degrade --bg-color '#2a0d3f'
```

## STUDIO WEB — une page HTML, rien a installer

`tools/build_web_studio.py` fabrique **un seul fichier HTML autonome** : on
l'ouvre (double-clic depuis le disque, ou en ligne), on depose un morceau, et
la MPC le joue. Pas de Python, pas de ffmpeg, pas de serveur — tout se passe
dans le navigateur, et le morceau ne quitte pas la machine.

```bash
python3 tools/build_web_studio.py -o out/studio-omnipotard.html
```

La page s'ouvre sur une **boucle de demonstration** deja en train de jouer, pour
qu'on voie ce que fait l'outil avant meme d'avoir depose un fichier.

### Ce qu'elle fait elle-meme

Tout ce que fait `mpc_performance.py` est porte en JavaScript : STFT (hop 256,
fenetre 1024), flux spectral par bande, detection des coups, tempo par
autocorrelation ponderee, calage de phase sur les grosses caisses, paroxysmes,
enveloppes — puis le faisceau, le bloom a deux echelles, la colorisation, les
scanlines, le vignettage, le grain, la bombe cathodique et les glitchs.

Sur *Hint*, le portage retrouve les memes paroxysmes que Python a 0,1 s pres
(8,76 / 19,76 / 30,92 s contre 8,8 / 19,8 / 31,0) et une image dont la
luminosite moyenne differe de 1,4 %.

### Le trace n'est pas redessine

C'est le point important : la geometrie de la machine est **exportee du moteur
Python** (`tools/export_geometry.py` : 113 chemins, 58 000 points deja
reechantillonnes, quantifies en entiers 16 bits) et embarquee dans la page. Le
navigateur ne fait que la tracer. Une retouche de la machine se propage donc au
studio web par une simple reconstruction, sans risque de voir les deux dessins
diverger.

### Ses limites, face a `tools/studio.py`

| | studio web | studio local (Python) |
| --- | --- | --- |
| installation | aucune | Python + numpy + ffmpeg |
| enregistrement | temps reel, webm/mp4 | hors-ligne, mp4 x264 |
| definition | 360p a 720p | jusqu'a la 4K |
| duree | celle du morceau, en temps reel | illimitee |

Le studio web sert a essayer, choisir une couleur, sortir vite un extrait. Pour
un master propre, en 4K ou en lot, c'est `tools/studio.py` qui travaille.
