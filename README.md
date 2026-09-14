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
- **les pads suivent la vraie batterie du morceau**, et chaque famille garde
  son pad pour qu'on reconnaisse l'instrument à l'endroit où il s'allume.

  Le flux par bande ne suffisait pas : dans un morceau dub, la basse occupe
  la même bande que la grosse caisse, et le pad de kick s'allumait donc sur
  chaque note de basse. Chaque famille est maintenant reconnue par ce qui la
  distingue **physiquement** :

  | famille | ce qui la reconnaît | avant → après (coups par temps, concentration sur la grille) |
  | --- | --- | --- |
  | grosse caisse | attaque grave **avec un clic** 2–6 kHz | 2,05 / 1,15 → 0,36 / **3,6** |
  | note de basse | attaque grave **sans** clic — son propre pad | — |
  | caisse claire | corps 180–450 Hz **avec du bruit** 2,5–8 kHz | 3,98 / 1,47 → 0,26 / **5,2** |
  | charley | l'aigu seul, sans corps | 4,77 / 1,60 → 1,81 / 2,6 |

  Une concentration de 1 signifie « réparti au hasard » ; au-delà de 2, les
  coups tombent vraiment sur la grille.

  Un piège trouvé en chemin : dans le grave, l'enveloppe redressée ondule à
  deux fois la fréquence du son (100 Hz pour un sub à 50 Hz). Sans un lissage
  d'au moins 45 ms, on prend cette ondulation pour des coups et on en compte
  deux fois trop — c'était la moitié du problème ;
- la bande de 16 pas suit le pas courant du séquenceur. Le tempo est affiné
  sur les attaques graves après l'autocorrélation, dont le pas vaut 5,3 ms :
  deux millisecondes d'erreur suffisent à décaler d'un quart de temps au bout
  de quatre minutes. Sur *Hint*, cela corrige 0,7040 s en 0,7058 s ;
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
pip install numpy pillow          # pillow : --stills et les fonds animés

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
- **Dedoublement du trait** (`--split`, 1 par defaut) : sur les coups graves
  vraiment appuyes — et seulement ceux-la — chaque ligne se lit en triple,
  rouge d'un cote, bleu de l'autre, vert au milieu, comme un defaut de
  convergence ; puis les copies se recollent pendant que le sub retombe.
  L'ecart se regle avec `--split-px` (11 px ramenes a 540p).

  Deux details font tout l'effet. Il est applique **avant** que le fond ne
  soit pose, donc seul le trace se dedouble, jamais l'image entiere. Et les
  trois copies sont tirees de l'**intensite** du trait, pas de ses canaux :
  dans une palette verte le rouge et le bleu sont presque vides, et decaler
  les canaux tels quels ne donnerait qu'une frange pale au lieu de trois
  lignes.

  Le declenchement ne passe pas par un seuil mais par un **classement** :
  `split_times()` trie les coups graves par force et garde les
  `--split-count` plus gros de toute la video (3 par defaut), en refusant
  deux instants trop rapproches. Un seuil aurait dependu du mixage — jamais
  declenche sur un morceau, vingt fois sur un autre. Sur *Hint* entier, les
  trois tombent a 79 s, 169 s et 223 s. `--split 0` le retire.
- **Traînée de la bande** (`--trail`, 1 par defaut) : le fil est redessine a
  quelques instants passes, de plus en plus pale. Sa longueur suit
  `density()`, le nombre de familles d'instruments qui jouent dans la
  demi-seconde : un passage depouille laisse un trait net, un passage charge
  bave derriere lui. `--trail 0` le retire.
- **Dalle** : le bandeau du haut porte le nom du morceau (`--title`, par
  defaut le nom du fichier) et, juste en dessous, une barre de progression.
  `--screen-dim` regle son opacite devant une image de fond (0,40 par
  defaut : assez pour que la dalle se lise, pas au point d'en faire un trou
  noir) — sans quoi le ciel de la photo passe au travers et l'ecran de la
  machine a l'air d'etre en verre.
- **Courbe sonore** : deux choses la rendent lisible.

  Son amplitude **suit le niveau** (`--wave`) : petite quand c'est calme,
  grande quand ca pousse. C'etait l'inverse avant — un calibre automatique
  d'oscilloscope remontait les passages calmes, et la courbe restait donc
  haute en permanence.

  Elle **gonfle sur le temps fort** (`--wave-punch`) : plus du double de sa
  hauteur sur chaque grosse caisse. C'est ce coup-la qu'on veut voir passer
  dans la bande.

  ### Pourquoi le trace ne peut pas etre « fluide »

  A 30 images par seconde on echantillonne le son toutes les 33 ms : tout ce
  qui depasse une quinzaine de hertz a deja change d'une image a l'autre. Le
  saut moyen entre deux images vaut 1,7 fois la hauteur de la courbe, et
  aucun lissage n'y change rien — une moyenne glissante seule ne descend qu'a
  6 dB par octave. Cascadee trois fois (`--wave-passes 3`) on tombe a 0,73,
  mais il faut ecraser le signal au point que ce n'est plus une forme d'onde.

  Deux echappatoires, toutes deux avec un cout :

  - `--wave-trig` (balayage declenche sur la grille musicale) descend le saut
    a 0,13 — dix fois plus stable. Mais le trace se fige pendant tout le
    temps : la bande ne vit plus.
  - `--wave-passes 3` avec une large `--wave-win` donne un mouvement plus
    coulant, au prix d'une courbe qui ressemble a une enveloppe et non plus a
    un signal.

  Par defaut, ni l'un ni l'autre : balayage libre, fenetre de 70 ms, un seul
  passage de lissage. Le mouvement reste vif, et c'est l'amplitude — basse
  dans les creux, doublee sur le temps fort — qui porte la lecture.
- **Fond en image ou en video** (`--backdrop`) : n'importe quel fichier
  lisible par ffmpeg, un par morceau. Le type est reconnu tout seul.

  Le fond est recadre en « couvrant », assombri, legerement floute et creuse
  derriere la machine — le faisceau etant additif, une image nette et claire
  lui mangerait tout son contraste. `--backdrop-strength`, `--backdrop-clear`
  et `--screen-dim` reglent le dosage.

  Une **video** est detaillee une fois en vignettes sur le disque, que chaque
  tache de rendu relit par son numero. C'est necessaire : le rendu calcule
  les images en parallele et dans un ordre quelconque, ce a quoi une lecture
  sequentielle ne se prete pas. Les vignettes font le tiers de la definition
  finale — le fond est floute de toute facon, et les garder en pleine
  definition coûterait des gigaoctets sur un morceau entier (17 Mo pour 12 s
  ici). Le recadrage et le flou sont delegues a ffmpeg pendant l'extraction,
  si bien qu'il ne reste qu'une lecture et une multiplication par image. Le
  resultat est mis en cache dans `/tmp/omnipotard-fonds/`, donc un deuxieme
  rendu ne re-extrait rien. Une video plus courte que le morceau **boucle**.
- **Ondulation du trace** (`--wobble`, 0 par defaut) : le leger tremblement
  des contours de la machine. Il est desormais desactive ; `--wobble 1` le
  retablit. A ne pas confondre avec la bombe de la dalle, qui est
  `--no-curve`.
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

**Sans toucher au terminal**, il y a un raccourci à double-cliquer à la
racine du projet :

| système | fichier |
| --- | --- |
| macOS, Linux | `Lancer-le-studio.command` |
| Windows | `Lancer-le-studio.bat` |

### Mettre à jour

Double-cliquer sur `Mettre-a-jour.bat` (Windows) ou `Mettre-a-jour.command`
(macOS, Linux). Le script télécharge la dernière version depuis GitHub et
remplace le code — **rien à installer, pas besoin de git**. Vos morceaux, vos
fonds et vos vidéos (dossier `out/`) ainsi que ffmpeg (`bin/`) ne sont pas
touchés. Fermez la fenêtre du studio avant, et relancez-la après : Python lit
les modules au démarrage, un studio resté ouvert continue de servir l'ancien
moteur.

Le studio affiche en bas de page la version qu'il exécute réellement, et la
rappelle dans ses messages d'erreur — c'est ce qui permet de dire en un coup
d'œil si une correction est bien arrivée jusqu'à la machine.

Il trouve Python, installe `numpy` et `pillow` s'ils manquent, **télécharge
ffmpeg** s'il n'est pas là, démarre le studio et **laisse la fenêtre ouverte**
si quelque chose se passe mal — sans quoi le message d'erreur disparaît avant
d'être lu.

Sous Windows, ffmpeg est posé dans le sous-dossier `bin/` du projet plutôt
que dans le système : pas de droits administrateur, pas de `PATH` à modifier,
et il suffit de supprimer le dossier pour tout enlever.

En ligne de commande, c'est :

```bash
git clone https://github.com/Guizmoh/Glitch-visualisateur.git
cd Glitch-visualisateur
pip install numpy pillow          # ffmpeg : voir « Rendu » plus haut
python3 tools/studio.py
# le navigateur s'ouvre sur http://127.0.0.1:8765
```

**Après un `git pull`, il faut relancer le studio.** Python lit les modules au
démarrage : un studio laissé ouvert continue de servir l'ancien moteur, et on
cherche longtemps pourquoi une nouveauté « n'est pas là ». La version
réellement chargée est affichée en haut de la page, à côté du titre — c'est
elle qui fait foi.

L'adresse est **locale** : elle ne marche que sur la machine qui fait tourner
la commande. Il n'y a pas de version en ligne, et c'est voulu — le studio
décode l'audio avec ffmpeg, calcule chaque image avec numpy puis encode en
x264, tout cela sur vos fichiers. Un navigateur seul ne sait pas faire ça, et
il faudrait de toute façon envoyer vos morceaux sur un serveur.

On y dépose un morceau (mp3, wav, flac, m4a…) et, si on veut, une **image ou
une vidéo de fond**. Tous les réglages du moteur sont là : couleur, fond de
dalle, dédoublement du trait (nombre et écart), éclair de caisse claire,
amplitude et gonflement de la courbe, traînée, titre affiché sur la dalle.
L'**aperçu se recalcule à chaque réglage** — c'est une vraie
image du rendu, pas une simulation : ce qu'on voit est ce qu'on obtient. Le
bouton *aller au prochain paroxysme* saute là où tomberont les glitchs, pour
les juger avant de lancer quoi que ce soit.

Sur une vidéo de fond, l'aperçu n'extrait que l'image de l'instant regardé
(une demi-seconde) plutôt que de détailler tout le fichier ; c'est le rendu
qui la joue en entier, et la boucle si elle est plus courte que le morceau.

Le rendu se lance depuis la même page, en 1080p, 4K, 720p, carré ou vertical,
avec une barre de progression et un bouton de téléchargement. Tout ce que le studio fabrique (morceaux déposés et
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

### Travelling sur l'image de fond

Une photo immobile derrière une machine qui bouge finit par ressembler à un
décor collé. Le **travelling** l'anime : l'image est chargée plus grande que
l'écran, et on s'y déplace lentement du début à la fin du morceau. Le curseur
dit **quelle part de l'image est parcourue** — 20 % suffisent largement — et
le sens se choisit parmi `avant`, `arriere`, `gauche`, `droite`, `haut`, `bas`
(ou `aucun`).

La marge est prise sur l'image d'origine, jamais sur l'image finale : le cadre
reste net d'un bout à l'autre, là où un agrandissement après coup l'aurait
rendu flou. Le creux derrière la machine et l'opacité de la dalle, eux, ne
bougent pas — ils appartiennent à l'écran, pas à la photo.

```bash
python3 tools/mpc_performance.py morceau.mp3 --backdrop photo.jpg \
    --travel 0.20 --travel-mode avant
```

### Réactions au son

Sept réglages qui font répondre l'image à la batterie. Chacun se **cale sur
l'instrument de son choix** : la batterie ayant été reconnue à l'analyse
(grosse caisse contre note de basse, caisse claire contre charley), « caisse
claire » veut vraiment dire caisse claire. À zéro, la réaction est éteinte.

| réglage | ce que ça fait | option |
| --- | --- | --- |
| zoom d'impact | l'image respire sur le coup | `--punch`, `--punch-on` |
| secousse | l'image est bousculée | `--shake`, `--shake-on` |
| étincelles | des braises jaillissent du châssis et retombent | `--parts`, `--parts-on`, `--parts-n`, `--parts-speed`, `--parts-life` |
| onde de choc | un anneau s'ouvre depuis la machine | `--ring`, `--ring-on` |
| pulsation de la grille | la grille du fond s'allume | `--grid-pulse`, `--grid-on` |
| éclat du fond | la photo est éclairée comme par un flash | `--bg-flash`, `--flash-on` |

Les instruments disponibles : `grosse caisse`, `basse`, `caisse claire`,
`percussions`, `charley`, `accords`, `tout`.

Les **étincelles** naissent du dessin lui-même : chaque braise part d'un point
pris au hasard sur un trait de la machine, perpendiculairement à lui, comme une
gerbe sur une meule. La normale pointant des deux côtés du trait, on privilégie
l'extérieur — une braise lancée vers le centre traverse toute la machine et
brouille le dessin. Elles partaient auparavant d'un contour abstrait, ce qui
faisait une couronne posée autour de la machine, sans rapport avec ce qu'elle
trace.

Leur nombre (`--parts-n`) va de quelques-unes à plus de trente
mille. Deux mécanismes rendent cette échelle tenable. L'éclat de chaque braise
baisse en racine du nombre de points réellement posés — mille braises éclairent
plus que dix, sans faire une tache blanche — et au-delà de quelques centaines,
le tracé de chacune est **écourté** plutôt que supprimé, pour tenir un budget
de points par image. Le coût reste donc borné : en 1080p, passer de 14 à 10 000
braises ajoute 112 ms par image, et 32 000 n'en coûtent pas davantage. Pour une
vraie explosion, monter aussi `--parts-speed` et `--parts-life`.

Le **dédoublement chromatique** se règle de la même façon (`--split-on`,
défaut : la grosse caisse). Son nombre est un **plafond, pas une consigne** :
deux dédoublements ne peuvent pas tomber à moins de 25 secondes l'un de
l'autre, si bien qu'une vidéo courte en reçoit moins qu'un morceau entier.
Auparavant le réglage valait « trois fois, quelle que soit la durée » — sur un
extrait de vingt secondes, les trois se tassaient et couvraient un tiers de la
vidéo au lieu d'un quarantième.

Tout est dessiné **au faisceau**, comme la machine : les étincelles sont
échantillonnées à pas constant en unités du monde, donc elles gardent la même
densité en 4K qu'en 540p, et elles passent par le même halo et les mêmes
scanlines que le reste. Et tout reste **déterministe** — chaque jet est tiré
d'un hasard semé par le numéro du coup, sans quoi le calcul en parallèle
donnerait des étincelles différentes d'une image à l'autre.

```bash
python3 tools/mpc_performance.py morceau.mp3 \
    --parts 1.3 --parts-on "caisse claire" \
    --ring 1.0 --ring-on "grosse caisse" \
    --grid-pulse 1.1 --punch 0.06 --shake 0.35
```

Le coût est faible : toutes réactions allumées, une image 1080p passe de
503 ms à 529 ms, soit 5 % de plus.

Un réglage, `--glitch`, dose les **glitchs sur les paroxysmes** — ces rafales
de tranches décalées qui tombent sur les montées du morceau. À 0 ils
disparaissent complètement, ce qui est utile pour juger le reste : ils sont
assez violents pour masquer tout ce qu'on cherche à régler.

### Avaries d'image

Les mêmes pannes, mais déclenchées par **ce qui est joué** plutôt que par les
montées du morceau — et chacune, là encore, calable sur l'instrument de son
choix.

| réglage | ce que ça fait | option |
| --- | --- | --- |
| bandes arrachées | des tranches horizontales partent de travers | `--tranches`, `--tranches-on` |
| blocs recopiés | des rectangles sont pris ailleurs dans l'image | `--blocs`, `--blocs-on` |
| décrochage vertical | le tube perd sa synchro, l'image saute | `--roll`, `--roll-on` |
| image fantôme | une copie décalée et transparente se superpose | `--ghost`, `--ghost-on` |
| négatif du trait | le cœur du trait se replie vers le sombre | `--invert`, `--invert-on` |
| bégaiement | l'image gèle pendant que le son continue | `--stut`, `--stut-on` |

Le **bégaiement** demande un mot. Sur chaque coup retenu, l'image se fige sur
l'instant de ce coup pendant la durée réglée ; le son, lui, ne s'arrête pas.
Avec un gel de 0,14 s à 30 images par seconde, cela donne :

```
image à 3,227 s  →  dessine 3,227 s
image à 3,260 s  →  dessine 3,257 s   (gelée — un coup vient de tomber)
image à 3,293 s  →  dessine 3,257 s
image à 3,326 s  →  dessine 3,257 s
image à 3,359 s  →  dessine 3,257 s
image à 3,392 s  →  dessine 3,257 s
image à 3,425 s  →  dessine 3,417 s   (coup suivant, l'image repart)
```

Cinq images identiques, puis la vidéo rattrape son retard d'un coup. Ce n'est
donc pas un ralenti : le temps saute pour revenir au bon endroit.

Un gel pur ne se remarque que si l'image bougeait beaucoup juste avant. D'où
`--stut-loop` : au lieu de figer, l'image **rejoue en boucle** un bout très
court pris à l'instant du coup. Avec une boucle de deux ou trois images, on
obtient un sursaut répété, bien plus visible qu'un arrêt.

### Texture — trip hop, lo-fi

Quatre réglages d'une autre nature : ils ne frappent sur rien, ils sont là du
début à la fin. C'est ce qui sépare un accident d'une matière — un grain de
pellicule qui n'apparaîtrait que sur la caisse claire ne ressemblerait à rien.

| réglage | ce que ça fait | option |
| --- | --- | --- |
| cadence tenue | chaque image gardée 2, 3 ou 4 fois : 15, 10 ou 7 i/s | `--cadence` |
| halo laiteux | les noirs remontent, la lumière s'étale | `--halo-doux` |
| poussière et rayures | grains, rayures verticales, cheveux de pellicule | `--poussiere` |
| flottement de bande | lent va-et-vient de l'image, comme une cassette fatiguée | `--flottement` |

La **cadence tenue** ne ralentit rien : elle quantifie l'instant demandé, si
bien que la vidéo garde sa durée mais avance par paliers. C'est le geste qui
donne son air d'animation à un clip lo-fi.

Le **halo laiteux** est calculé en définition réduite puis redéployé, comme le
halo du faisceau : un flou large n'a aucun détail à perdre, et le faire en
pleine définition doublait à lui seul le temps de calcul d'une image 1080p.
Les quatre ensemble coûtent 18 % — 622 ms par image contre 527.

### Écho, couleurs, spectrogramme

**Écho d'images** : la machine telle qu'elle était il y a quelques centièmes,
de plus en plus pâle, dessinée sous l'image du moment. Les échos s'empilent
dans le **même faisceau** plutôt que de recalculer des images entières — seul
le tracé est refait, le halo, les textures et la déformation ne le sont qu'une
fois. Trois échos coûtent 48 % de plus, là où trois images entières en
coûteraient 300.

**Couleurs par instrument** : le trait prend la teinte du dernier coup — rouge
la grosse caisse, jaune la caisse claire, cyan le charley, violet la basse,
orange les percussions, vert les accords. Le classement se fait sur la
*fraîcheur* du coup et presque pas sur sa force : classées à la force, les
familles denses comme le charley gagnaient jusque sur la grosse caisse, dont la
teinte ne sortait jamais.

**Spectrogramme** : les trois dernières secondes du morceau déroulées sur la
dalle, une ligne par bande de fréquences, la force en luminosité. Trente bandes
espacées en octaves — une échelle linéaire tasserait tout le grave sur deux
lignes — et soixante colonnes par seconde gardées en octets : un morceau de
quatre minutes tient dans un demi-mégaoctet, ce qui se transmet sans peine aux
tâches de rendu. Baisser l'amplitude de la courbe sonore pour bien le voir.

### Préréglages

Un point de départ par famille de musique, pas une vérité : tout reste
bougeable ensuite.

`propre` · `drum and bass` · `dub` · `idm` · `trip hop` · `hip hop` ·
`breakcore` · `techno` · `ambient`

```bash
python3 tools/mpc_performance.py morceau.mp3 --preset idm
python3 tools/mpc_performance.py morceau.mp3 --preset "trip hop" --palette bleu
```

Le préréglage n'est qu'un socle : une option donnée explicitement l'emporte sur
lui, ce qui permet de partir d'`idm` et de ne changer qu'une chose. Dans le
studio, choisir un préréglage repose **tous** les curseurs — ceux qu'il ne
mentionne pas reviennent à leur valeur d'usine, sans quoi deux préréglages
enchaînés se mélangeraient.

Les préréglages sont écrits avec les noms du moteur, et une table dit à quel
curseur du studio chacun correspond. Un contrôle vérifie que chaque nom existe
des deux côtés : sans lui, un préréglage poserait des valeurs dans le vide sans
que rien ne le signale.

### Netteté du fond

Le faisceau est additif : une image nette et claire derrière le trait lui mange
son contraste. D'où un fond volontairement flou et sous-échantillonné par
défaut — mais c'est un parti pris, pas une fatalité. `--backdrop-sharp` va de 0
(fondu, vignettes au quart de la définition) à 1 (net, pleine définition et
aucun flou), avec un rapport de trente entre les deux sur le détail mesuré. La
valeur par défaut, 0,37, reproduit exactement l'ancien comportement.

| miroir | l'image se replie sur elle-même | `--miroir`, `--miroir-on` |
| ondulation liquide | le balayage ondule, la machine fond | `--ondul`, `--ondul-on` |
| mosaïque | l'image tombe en gros pixels | `--mosaic`, `--mosaic-on` |
| tranches brassées | le temps est rejoué dans le désordre | `--scramble`, `--scr-len` |
| kaléidoscope | l'image répétée en grille, un carreau sur deux retourné | `--kaleido`, `--kaleido-on` |
| cisaillement | l'image penche d'un bloc | `--cisaille`, `--cisaille-on` |
| coupure franche | l'image s'absente, deux images durant | `--coupure`, `--coupure-on` |
| patinage de bande | le temps ralentit puis rattrape d'un coup | `--tapestop`, `--tapestop-on` |

Elles s'appliquent à l'image finie, juste avant la déformation du tube — au
même endroit que les glitchs de paroxysme, ce qui leur donne cet air de signal
cassé plutôt que d'effet dessiné.

Les **tranches brassées** ne dépendent d'aucun instrument : elles découpent le
temps en blocs réguliers et les rejouent dans le désordre, par paquets de huit,
pendant que le son continue tout droit — le montage haché des disques de
breakcore. Le tirage est semé par le numéro du paquet, si bien que chaque tâche
de rendu retrouve le même désordre sans rien savoir des images voisines.

Deux détails qui comptent. Le **négatif** n'est pas un vrai négatif : inverser
franchement l'image passerait par un gris uniforme à mi-chemin, ce qui donne un
voile au lieu d'un éclair, et rendrait le fond noir tout blanc. On replie donc
seulement ce qui dépasse un seuil — le cœur du trait vire au sombre en gardant
ses bords lumineux, et le fond reste noir. Le **bégaiement**, lui, n'est pas un
effet appliqué à l'image mais un décalage du temps : la tâche de rendu calcule
quel instant dessiner, ce qu'elle déduit seule, sans rien savoir des images
voisines — c'est ce qui permet de le calculer en parallèle.

Ces six-là frappent ou ne font rien : elles gardent un **plancher de 45 %**
indépendant de la force du coup. Un morceau au mixage sage donne des coups qui
pèsent 0,3, et un effet strictement proportionnel y resterait invisible quel
que soit le réglage.

```bash
python3 tools/mpc_performance.py morceau.mp3 --glitch 0 \
    --tranches 1.5 --tranches-on "caisse claire" \
    --invert 1.8 --invert-on "grosse caisse" \
    --stut 0.10 --stut-on charley
```

Leur coût est nul à la mesure : 1080p, toutes allumées, 529 ms par image contre
544 sans.

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
