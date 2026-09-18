# Omnipotard — HARDWARE VISUEL

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

Trois machines sont dessinées : la **MPC Live III**, le **MiniFreak** (clavier
37 touches) et le **Digitakt II** (16 déclencheurs). Elles se jouent de la même
façon — les coups du morceau allument leurs organes — et elles peuvent se
**succéder dans une même vidéo** : le tracé de l'une se déforme jusqu'à devenir
celui de l'autre (voir *Séquenceur de machines*).

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
| `out/omnipotard_intro_1080p60_web.mp4` | version légère — partage, réseaux, prévisualisation — **non versionnée** |
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
git clone https://github.com/Guizmoh/Omnipotard-HARDWARE-VISUEL.git
cd Omnipotard-HARDWARE-VISUEL
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

### Séquenceur de machines

La carte **Machine** choisit celle du début, puis on ajoute des changements :
une ligne par instant, « à 0:32 → digitakt ». Le bouton **Répartir toutes les
N s** remplit la liste d'un coup pour tout le morceau.

Entre deux machines, le tracé se déforme : les pads glissent sur les
déclencheurs, les encodeurs sur les potards. La déformation **précède**
l'instant inscrit — à « 0:32 digitakt » avec 1,9 s de déformation, elle
commence à 0:30 et le Digitakt est bien posé à 0:32. Deux curseurs la règlent :
sa **durée** et son **ondulation** (à zéro, les traits glissent proprement ;
plus haut, ils serpentent).

Les noms des machines ne se déforment pas : celui de la première s'efface sur
place pendant que celui de la seconde se lève à la sienne. Une lettre qui se
déforme en une autre ne se lit plus — elle passe par de la bouillie.

En ligne de commande :

```bash
python3 tools/mpc_performance.py morceau.mp3 \
  --machines "0=mpc, 0:32=digitakt, 1:05=minifreak" --passage 1.9
```

Un rendu à machine unique ne paie rien : le tracé n'est refait que lorsque la
machine change.

### Mélodie : un fichier MIDI

Déposer un **.mid** dans la carte *Mélodie* fait jouer les vraies notes du
morceau sur le **clavier du MiniFreak** : c'est la touche exacte qui s'enfonce,
et elle seule — les coups de batterie cessent alors d'allumer les touches.

La MPC et le Digitakt n'ont pas de clavier, et le fichier n'y change rien.
Plaquer une mélodie sur seize pads de batterie ne donnait rien de lisible :
trois choses s'y disputaient les mêmes cellules — les coups, les pas et les
notes.

Le fichier est pris **tel quel**. Un MIDI exporté du même projet que le morceau
est déjà à l'heure : son décalage vaut zéro, et c'est ce qu'on lui laisse.

Pour le vérifier, la carte annonce l'instant de la **première note**. Comparez-le
à l'instant où la mélodie s'entend dans le morceau ; s'il y a un écart, le
curseur **avance / retard** (± 10 s, au centième) le rattrape.

Une case **chercher le décalage tout seul** existe, décochée par défaut. Elle
compare les attaques du fichier à celles du morceau. Mesuré sur le morceau
d'essai, avec des décalages connus :

| fichier | 0 s | +0,8 s | +3 s | +7,5 s | −2 s |
| --- | --- | --- | --- | --- | --- |
| notes posées sur les attaques | exact | exact | exact | exact | exact |
| **mélodie** | −17,9 | −17,1 | −14,9 | −10,4 | −17,5 |

Sur une mélodie il se trompe **à tous les coups**, et sans qu'on puisse s'en
apercevoir : ses mauvaises réponses ont des « netteté » jusqu'à 3,7, plus hautes
que certaines bonnes, qui descendent à 1,7 — aucun seuil ne les sépare. La
raison tient en une phrase : les attaques relevées dans l'audio sont surtout des
coups de batterie — six mille sept cents sur quatre minutes — là où une mélodie
ne porte que quelques centaines de notes tenues, qui ne tombent pas dessus.

À ne cocher que pour une piste de **batterie**, où il retrouve le décalage
exactement.

Une note trop grave ou trop aiguë pour les trente-sept touches y est ramenée
par octaves : la mélodie garde ses notes, elle change seulement d'octave. C'est
ce qui permet à un clavier de trois octaves de rendre une mélodie qui en
parcourt cinq.

Les formats 0 et 1 sont lus, avec leur carte des tempos (un morceau dont le
tempo change en route reste en place). Rien à installer : le lecteur tient dans
`tools/midi.py`.

```bash
python3 tools/mpc_performance.py morceau.mp3 --machine minifreak \
  --midi melodie.mid --midi-force 1.2
```

Avec `--machine mpc` ou `--machine digitakt`, `--midi` est sans effet.

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

### Sur quoi un effet se déclenche

Six familles d'instruments, cela laisse vite deux effets tomber sur le même
coup. Les listes proposent donc quatre sortes de déclencheurs — **41 en tout**,
groupés dans le menu :

| groupe | ce que c'est |
| --- | --- |
| **Instruments** (7) | grosse caisse, basse, caisse claire, percussions, charley, accords, tout |
| **Bandes de fréquences** (7) | sous-basses (20-60 Hz), graves (60-160), bas médium (160-400), médium (400-1000), haut médium (1000-2500), aigus (2500-6000), très aigus (6000-15000) |
| **Hasard** (3) | rare, moyen, dense |
| **Un coup sur deux** (24) | pour chaque famille : `1 sur 2`, `l'autre sur 2`, `1 sur 3`, `1 sur 4` |

Les **bandes** écoutent une hauteur, pas un instrument. Elles ne passent par
aucune reconnaissance de batterie : ce qui monte dans la bande déclenche, que
ce soit une peau, une voix ou une nappe. C'est ce qui les rend utiles à côté
des familles plutôt que redondantes avec elles — sur le morceau de test,
`grosse caisse` et `graves` ne tombent **jamais** ensemble (0 % de recouvrement
sur douze secondes), alors que `caisse claire` et `bas médium` se recouvrent à
90 %, ce qui est normal : la caisse claire *est* dans cette bande.

Le **hasard** est tiré au sort mais posé sur la double-croche du morceau : un
vrai hasard continu tomberait à contretemps et aurait l'air d'un défaut ; calé
sur la grille, il a l'air joué. Le tirage est semé, donc deux rendus du même
morceau donnent exactement les mêmes coups — ce dont le calcul en parallèle a
besoin.

Les **parts** répondent directement au problème « si je mets plus d'effets,
tout va tomber en même temps ». Deux effets, l'un sur `caisse claire · 1 sur 2`
et l'autre sur `caisse claire · l'autre sur 2`, alternent : mesuré, 0 % de
recouvrement. Le rang se compte parmi les coups retenus, dans l'ordre du
morceau.

Tout cela passe par les mêmes événements que la batterie, avec des numéros de
pad qui n'existent pas sur la machine : rien n'y rallume un pad, et rien n'entre
dans la densité qui règle la longueur de la traînée. « tout » ne veut d'ailleurs
dire que les seize vrais pads — sinon, le régler ferait partir l'effet des
dizaines de fois par seconde.

Le coût est nul à la mesure : la détection des sept bandes prend 0,02 s sur
soixante secondes d'audio, et le nombre d'événements passant de 1 245 à 6 668
sur un morceau entier fait passer un test de déclenchement de 9 à 11
microsecondes — cinquante microsecondes par image, sur 127.

Le studio annonce sous chaque curseur combien de fois le déclencheur choisi
partira, parts comprises : `caisse claire` compte 88 coups, `caisse claire ·
l'autre sur 2` en annonce 44.

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
| miroir | l'image se replie sur elle-même | `--miroir`, `--miroir-on` |
| ondulation liquide | le balayage ondule, la machine fond | `--ondul`, `--ondul-on` |
| mosaïque | l'image tombe en gros pixels | `--mosaic`, `--mosaic-on` |
| tranches brassées | le temps est rejoué dans le désordre | `--scramble`, `--scr-len` |
| kaléidoscope | l'image répétée en grille, un carreau sur deux retourné | `--kaleido`, `--kaleido-on` |
| cisaillement | l'image penche d'un bloc | `--cisaille`, `--cisaille-on` |
| coupure franche | l'image s'absente, deux images durant | `--coupure`, `--coupure-on` |
| patinage de bande | le temps ralentit puis rattrape d'un coup | `--tapestop`, `--tapestop-on` |

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

Toutes frappent ou ne font rien : elles gardent un **plancher de 45 %**
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

### Ce que fait chaque réglage, et à quelle fréquence

Sous chaque curseur du studio, deux lignes : une phrase disant **ce que le
réglage fait**, et, en vert, **combien de fois il se déclenchera sur le morceau
chargé**.

Le second chiffre est calculé sur les vrais événements du morceau, pas estimé :
un effet posé sur le charley part souvent des centaines de fois là où la grosse
caisse en compte quelques dizaines. Sur le morceau de test :

```
charley          ~ 615 fois   (152 par minute)
basse            ~ 225 fois   ( 56 par minute)
percussions      ~ 193 fois   ( 48 par minute)
grosse caisse    ~ 125 fois   ( 31 par minute)
caisse claire    ~  88 fois   ( 22 par minute)
```

La ligne suit le sélecteur d'instrument : la changer met le compte à jour
aussitôt. Les réglages qui ne se déclenchent sur rien affichent « en continu »,
ceux laissés à zéro « éteint ». Le dédoublement annonce son nombre réel après
plafonnement par la durée, les glitchs le nombre de paroxysmes détectés, et les
tranches brassées le nombre de blocs concernés.

Les phrases vivent dans le moteur, pas dans la page, et un contrôle vérifie que
chaque curseur en a une. C'est lui qui a trouvé que l'éclair jaune de la caisse
claire annonçait la fréquence de la grosse caisse : n'ayant pas de sélecteur,
il retombait sur la valeur par défaut au lieu de compter ses propres familles.

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

### Enregistrer ses propres réglages

Avec plus de quatre-vingts réglages, retrouver à la main ce qu'on avait la
semaine dernière n'est pas raisonnable. Le champ sous le préréglage garde
**tout d'un coup** : les curseurs, les listes de déclencheurs, les couleurs, le
titre, la bombe de l'écran. Pas la définition, la cadence, la durée d'aperçu ni
le morceau — ceux-là décrivent le fichier, pas l'allure, et n'ont rien à faire
dans un réglage qu'on rappelle six mois plus tard.

Ils rejoignent la même liste que les préréglages fournis, dans un groupe « Mes
réglages », et se rappellent de la même façon. Un préréglage fourni ne dit que
l'essentiel et laisse le reste revenir à l'usine ; un réglage enregistré, lui,
est une photographie complète de la page — le rappeler rend exactement ce qui a
été gardé.

Ils vivent dans `out/studio/mes-reglages.json`, lisible et modifiable à la main.
Le fichier est écrit à côté puis renommé : une coupure ne laisse pas un fichier
à moitié écrit à la place des réglages d'une soirée. Un fichier illisible
n'empêche pas le studio de démarrer — il repart d'une liste vide plutôt que de
refuser d'ouvrir.

### Netteté du fond

Le faisceau est additif : une image nette et claire derrière le trait lui mange
son contraste. D'où un fond volontairement flou et sous-échantillonné par
défaut — mais c'est un parti pris, pas une fatalité. `--backdrop-sharp` va de 0
(fondu, vignettes au quart de la définition) à 1 (net, pleine définition et
aucun flou), avec un rapport de trente entre les deux sur le détail mesuré. La
valeur par défaut, 0,37, reproduit exactement l'ancien comportement.

### Trois machines

```bash
python3 tools/mpc_performance.py morceau.mp3 --machine minifreak
python3 tools/mpc_performance.py morceau.mp3 --machine digitakt
```

| machine | ce qu'elle a |
| --- | --- |
| `mpc` | MPC Live III — seize pads, bande de seize pas, grand écran tactile |
| `minifreak` | MiniFreak — clavier 37 touches, huit potards, deux bandes tactiles |
| `digitakt` | Digitakt II — seize déclencheurs, huit encodeurs, grand écran |

Elles parlent le **même langage** : des chemins étiquetés, et des étiquettes que
le moteur sait animer. `pad<n>` s'allume sur un coup, `step<n>` sur le pas du
séquenceur, `qlink<n>` suit une enveloppe, `strip` porte un curseur tactile,
`lcd` est l'écran. Une machine n'a donc pas à ressembler à une MPC pour être
jouée comme telle : il lui suffit d'avoir des organes et de dire lesquels.

Ce que chacune en fait :

- sur le **MiniFreak**, le clavier joue des **notes**. Avec un fichier MIDI
  chargé, c'est la touche exacte de la mélodie qui s'enfonce, et elle seule ;
  sans fichier, les coups du morceau frappent les touches — les seize familles
  sont réparties sur les trente-sept touches, pour qu'une grosse caisse ne
  rallume pas seulement le bas du meuble. Le clavier n'a **pas** de rangée de
  pas : aucune machine ne fait défiler un séquenceur sur ses touches de piano,
  et cela brouillait la note jouée.

  Une touche jouée s'allume **sur toute sa surface**, pas seulement par ses
  traits : le remplissage est une grille de points espacés de trois millièmes
  d'unité, soit un pixel et demi en 1080p. C'est l'échelle du faisceau lui-même
  — à cette distance les halos se rejoignent et la surface s'éclaire. À
  l'espacement d'origine, trente millièmes, les lignes restaient séparées d'une
  douzaine de pixels et la touche avait l'air rayée. La grille étant dix-huit
  fois plus dense, le poids de chaque point baisse d'autant : c'est la lumière
  par unité de surface qui compte, et `ECLAT_TOUCHE` la fixe.

  Les blanches sont **échancrées** là où une noire s'appuie dessus, comme sur
  un vrai clavier. Tracées en rectangles pleine hauteur, elles passaient sous
  les noires et la couture entre deux blanches traversait chaque noire par le
  milieu : elle avait l'air coupée en deux. Le tracé et l'allumage sortent
  d'une seule liste, pour qu'ils ne puissent pas diverger — la blanche se
  trace échancrée, mais c'est sa partie large, celle qu'on voit, qui
  s'allume ;
- sur le **Digitakt II**, les seize déclencheurs servent de pads **et** de pas
  à la fois, exactement comme sur la vraie : un coup les allume, le séquenceur
  les balaie ;
- le creux ménagé derrière la machine et la dalle opaque suivent l'écran de la
  machine choisie, pas celui de la MPC.

Un piège qui a coûté un rendu : le moteur est recopié tel quel dans chaque
tâche de rendu sous Windows, et une `lambda` ne se recopie pas. Les deux
nouvelles machines en avaient une pour leur remplissage de touche, et le rendu
s'arrêtait sur `Can't pickle <lambda>` dès qu'on quittait la MPC. Ce sont
maintenant des fonctions nommées, et le moteur ne garde que le **nom** de sa
machine — le plan, lui, vit dans le registre. Vérifié : les trois machines
rendent les mêmes images en fork, en spawn et sur un seul processus.

### Le néon : éclairage, surface qui le reflète, tube de verre

Ce qu'on voit autour du trait n'est pas le trait : c'est sa lumière renvoyée
par la surface qui le porte. Trois réglages décrivent cette scène.

```bash
python3 tools/mpc_performance.py morceau.mp3 --neon 2.2
python3 tools/mpc_performance.py morceau.mp3 --reflet 1.0      # surface collée
python3 tools/mpc_performance.py morceau.mp3 --tube 1.0        # verre et relief
```

`--neon` est la force de cet éclairage. Le trait lui-même ne bouge pas ; c'est
la lumière qu'il jette autour de lui qui monte ou descend — mesuré, la lumière
totale de l'image passe de 26 000 à 43 600 entre 1 et 2,2, et tombe à 14 000 à
0,35.

`--reflet` dit **à quelle distance se tient cette surface**. Collée au tube,
elle renvoie une lueur serrée et vive, et le noir revient entre deux traits ;
lointaine, la lueur s'étale et pâlit, et tout baigne. Techniquement, le halo
est la somme de deux flous — un serré, un large — et le réglage déplace à la
fois leur **poids** et leur **rayon**. À 0,5, les deux valent exactement ce
qu'ils valaient avant : rien ne change pour ce qui a déjà été rendu.

`--tube` donne au trait l'épaisseur d'un tube de verre. Le tracé n'a pas de
normales — c'est un champ d'intensité, pas une géométrie 3D — mais la
différence entre deux flous donne exactement l'anneau qu'il faut pour
assombrir le bord, et le cœur décalé d'un pixel ou deux fait le reflet qui file
le long de l'arête haute. Le résultat lit comme du verre, surtout en 1080p et
au-dessus.

### Textures de fond animées

`--bg-anim` fait vivre la texture : le quadrillage, les points et les lignes de
tube **descendent**, le grain **bout** comme une pellicule.

La vitesse se compte en **motifs par seconde** et non en pixels. C'est ce qui
la rend utilisable : à vitesse constante en pixels, le même réglage faisait
dériver doucement un quadrillage à grosses mailles et strober des lignes de
tube cent fois plus fines — jusqu'à les faire remonter, par le même effet qui
fait tourner les roues de diligence à l'envers au cinéma.

Deux détails qui comptent. La maille passe de « environ vingt-quatre cases » à
**vingt-cinq exactement**, pour que la hauteur de l'image soit un multiple
entier du motif : la texture défile alors en boucle sans jamais montrer de
raccord. Et le creux ménagé derrière la machine est gardé **à part** de la
texture — sinon il descendrait avec elle, et la machine se retrouverait
éclairée par le bas au bout de deux secondes.

Le grain, lui, ne défile pas : il saute d'un point à l'autre de sa propre
matière, huit fois par seconde et par cran de vitesse, et reste fixe entre deux
sauts — un grain de pellicule tient le temps d'un photogramme. Le tirage est
semé par le numéro du saut, pas pris dans le tirage partagé : y puiser
déplacerait tout le reste de l'image dès qu'on allume l'animation.

Coût mesuré : 78 à 85 ms par image en 960×540 sans animation, 81 à 89 avec.

### Taille et présence de la machine

Deux réglages pour décider de la place que la machine prend dans l'image —
utiles surtout quand il y a une photo ou une vidéo derrière elle.

```bash
python3 tools/mpc_performance.py morceau.mp3 --taille 0.62
python3 tools/mpc_performance.py morceau.mp3 --presence 0.45
python3 tools/mpc_performance.py morceau.mp3 --taille 0.55 --presence 0.60
```

`--taille` ne réduit **que la machine** : son châssis, ses pads, sa dalle, ses
étincelles et son onde de choc. Le quadrillage du fond et le fil du morceau,
eux, tiennent la largeur de l'écran et ne bougent pas — les faire rétrécir
aussi laisserait des marges noires autour d'un décor qui devrait être plein.
Le creux que la texture garde derrière la machine, et la dalle opaque qui
empêche le ciel de la photo de traverser son écran, se recalent sur la
nouvelle taille : sans cela, une machine rétrécie flotterait au milieu d'un
halo sombre plus grand qu'elle.

`--presence` baisse son éclat sans rien déplacer. Le faisceau étant additif,
elle s'efface derrière le fond comme un reflet sur une vitre, au lieu de
devenir grise.

Un détail de calcul qui se voit à l'œil. Le tracé est échantillonné en unités
du monde, pas en pixels : une machine rétrécie tasse le même nombre de points
sur moins de pixels, donc un trait plus dense — et plus lumineux. Réduire la
machine revenait donc à l'allumer. La correction est proportionnelle à la
taille **puissance 1,35**, et cet exposant est mesuré, pas choisi : à la simple
proportion, la luminosité du trait montait de 149 à 212 en descendant à 0,45,
parce que des traits plus serrés que le halo additionnent leurs halos. À 1,35
elle va de 149 à 167, ce qui ne se voit plus. À taille 1 le facteur vaut
exactement 1 : rien ne change pour ce qui a déjà été rendu.

Le curseur du studio s'arrête à 1,30. Au-delà, deux points voisins du tracé
s'écartent de plus d'un pixel et demi et le trait commence à s'égrener —
mesuré : l'ondulation le long d'un trait passe de 0,05 à 0,13 entre taille
1,25 et taille 1,50 avec une finesse de 1,7. De toute façon, à 1,25 la machine
touche déjà les bords.

### Finesse du trait, et qualité du fichier

Deux choses différentes décident de la netteté du trait dans le fichier final :
sa **largeur** au tracé, et ce que l'**encodage** en garde.

`--nettete` resserre le faisceau. À 1 — la valeur d'origine — un trait mesure
4 px à mi-hauteur en 1080p ; à 1,25 il en mesure 3, à 1,5 il en mesure 2, et
les creux entre deux traits voisins s'assombrissent d'autant (57 → 39). Au-delà
le faisceau ne peut plus se resserrer sans se mettre à grener : il est étalé sur
les pixels, et un étalement plus étroit qu'un demi-pixel donnerait un trait en
pointillés. La limite est donc atteinte vers 1,75 en 1080p, plus tard en 4K.

L'encodage, lui, pesait plus lourd que tout le reste. Mesuré sur un extrait
1080p, chaque encodage comparé aux images brutes, l'erreur séparée entre les
traits et le fond :

| encodage | fidélité | erreur sur le trait | poids |
| --- | --- | --- | --- |
| 4:2:0 CRF 20 (l'ancien réglage) | 35,1 dB | 8,16 | 1,4 Mo |
| 4:2:0 CRF 17 | 35,9 dB | 7,20 | 2,9 Mo |
| 4:2:0 CRF 14 | 36,4 dB | 6,71 | 4,6 Mo |
| 4:2:0 CRF 8 | 36,8 dB | 6,12 | 10,5 Mo |
| **4:4:4 CRF 17** | **40,3 dB** | **4,63** | **2,7 Mo** |
| 4:4:4 CRF 14 | 42,1 dB | 3,76 | 4,5 Mo |

Le coupable n'est pas la compression mais le **sous-échantillonnage de la
couleur**. Un trait fin, saturé, posé sur du noir a l'essentiel de son signal
dans la couleur ; le 4:2:0 n'en garde qu'un quart. On peut baisser le CRF
jusqu'à 8 — sept fois le poids — sans jamais rattraper ce que le 4:4:4 donne
pour moitié moins lourd. Changer de filtre de sous-échantillonnage ne change
rien non plus (36,0 contre 35,9).

Mais le 4:4:4 ne se lit ni sur un téléphone, ni dans un navigateur, ni sur la
plupart des téléviseurs. D'où trois profils, au choix, plutôt qu'un défaut
imposé :

```bash
python3 tools/mpc_performance.py morceau.mp3 --quality compatible   # defaut
python3 tools/mpc_performance.py morceau.mp3 --quality net --nettete 1.3
python3 tools/mpc_performance.py morceau.mp3 --quality master
```

| profil | encodage | pour quoi |
| --- | --- | --- |
| `compatible` | 4:2:0, CRF 17 | lisible partout : téléphones, navigateurs, réseaux |
| `net` | 4:4:4, CRF 16 | VLC, mpv, un logiciel de montage |
| `master` | 4:4:4, CRF 10 | remonter la vidéo ensuite ; fichier lourd |

Les trois partagent les mêmes réglages fins de `x264`, choisis pour ce genre
d'image : du débit donné aux zones sombres — ici tout le fond —, et un
déblocage négatif pour que le filtre anti-blocs cesse de lisser les traits fins
en croyant corriger un artefact.

### Vitesse du séquenceur

La rangée de pas, en haut de la machine, avançait d'une case par double-croche.
Sur un morceau à 85 BPM cela fait une case toutes les 176 ms, soit 340 par
minute : de loin, une course. `--step-div` divise le temps autrement — 4 pour
l'ancien pas, **2 par défaut désormais** (une case par croche, 353 ms), 1 pour
une case par temps. Le studio affiche la cadence obtenue sous le réglage, en
millisecondes et par minute, calculée sur le tempo du morceau chargé.


### Lire l'aperçu en mouvement

Une image fixe ne dit rien de ce qui bouge — bégaiement, travelling,
étincelles, spectrogramme, patinage de bande. Le bouton **Lire en mouvement**
calcule pour de bon quelques secondes à partir de l'instant regardé, avec le
son, et les joue en boucle dans la page.

C'est un vrai rendu, dans le même moteur et avec les mêmes réglages que le
fichier final — pas un diaporama d'images fixes : chacune coûte plus d'un
dixième de seconde, on n'en verrait jamais plus de huit par seconde. Le clip
est en 960×540 à 15 images par seconde, ce qui le rend à peu près en temps
réel : **2,6 s pour deux secondes, 4,1 s pour quatre, 7,7 s pour huit**,
mesuré. Le rendu final, lui, en fera 30 ou 60.

Il sort en **VP8/WebM** et non en MP4, pour une raison qui s'est vue à
l'essai : certains navigateurs sont construits sans H.264 — le Chromium qui
sert à vérifier cette page en fait partie — et le lecteur répondait alors
`DEMUXER_ERROR_NO_SUPPORTED_STREAMS` sur un fichier pourtant valide. Le VP8,
personne ne le refuse. Si ffmpeg ne sait pas l'encoder, l'aperçu redevient un
MP4 ordinaire au lieu d'échouer.

Le débit est fixé à 3 Mbit/s plutôt que laissé à la qualité constante :
mesurée contre les images brutes, la qualité constante ne rendait que 24,1 dB
— un trait fin et grené est cher à encoder, et le codec choisissait d'y
renoncer — contre 28,3 dB à débit fixe, pour 1,5 Mo par tranche de quatre
secondes. Le calcul des images coûte de toute façon dix fois plus que
l'encodage. Ces extraits sont effacés au fur et à mesure, les trois derniers
exceptés : sans ce ménage, une séance de réglage en laisserait des centaines
de mégaoctets.

Bouger n'importe quel réglage rend la main à l'image fixe.

### Déposer un morceau, une image, une vidéo

Le studio reçoit les fichiers **par blocs, écrits au fur et à mesure sur le
disque**. Il les gardait auparavant entiers en mémoire le temps de les
recopier : une vidéo de téléphone de 700 Mo demandait 700 Mo de mémoire vive
rien que pour arriver, et sur une machine modeste le studio y laissait la vie —
la page affichait alors `Failed to fetch`, le message que donne un navigateur
quand la connexion meurt sans réponse. Mesuré : le même dépôt de 719 Mo coûte
aujourd'hui **2 Mo** de mémoire au studio.

Deux autres choses le disaient mal :

- le serveur répondait en **HTTP/1.0**, donc fermait la connexion aussitôt.
  Refuser un fichier trop gros pendant que le navigateur l'envoyait encore lui
  claquait la porte au nez, et « fichier trop gros » devenait `Failed to
  fetch`. En HTTP/1.1, et en avalant la fin de l'envoi avant de répondre, le
  vrai message arrive — et le dépôt suivant fonctionne sur la même connexion.
- la page ne montrait **aucune progression**. Un dépôt de vidéo restait muet
  une minute entière. Elle affiche maintenant le poids du fichier et son
  avancement : `envoi du fond machin.mp4 (170 Mo) — 63 %`, puis « le studio
  examine… ».

Les limites : **220 Mo** pour un morceau, **2 Go** pour une image ou une vidéo
de fond. Un fichier n'est renommé à son nom définitif qu'une fois complet — un
envoi interrompu laissait sinon un fichier tronqué que le studio reprenait
ensuite pour un bon. La vidéo rendue est renvoyée elle aussi par blocs, au lieu
d'être relue entièrement en mémoire pour être recopiée.

### Mettre à jour sans casser le rendu en cours

Un rendu échouait sur `AttributeError: 'Renderer' object has no attribute
'machine'`, et la page n'offrait pas les nouvelles machines. Les deux venaient
de la même cause : **le studio avait été mis à jour pendant qu'il tournait**.

Le programme garde son code en mémoire au démarrage. Les tâches de rendu, elles,
sont sous Windows des interpréteurs neufs qui relisent les fichiers **sur le
disque**. Mettre à jour sans fermer la fenêtre laisse donc l'ancien programme
envoyer un moteur d'ancienne forme à des tâches qui attendent la nouvelle : tout
réglage ajouté depuis manque à l'appel. Et la page servie reste l'ancienne —
d'où les machines invisibles.

Reproduit en trois lignes (supprimer `machine` d'un état sérialisé puis le
relire), puis corrigé de trois façons :

- le moteur porte désormais des **valeurs de repli au niveau de la classe** :
  un état auquel manque un réglage repart sur la valeur d'origine au lieu de
  s'arrêter net ;
- le studio **compare l'heure de ses fichiers à celle de son démarrage**. S'ils
  ont changé, la page le dit en rouge et le rendu refuse de partir avec une
  phrase claire — « fermez la fenêtre noire du studio et relancez-le » — plutôt
  qu'avec un message d'erreur Python ;
- les scripts de mise à jour recopient maintenant **tous** les fichiers de la
  racine et non une liste tenue à la main (le lanceur de la v2 n'arrivait
  jamais), et ils commencent par rappeler qu'il faut fermer le studio d'abord.

Le studio affiche aussi sa version au démarrage, dans la fenêtre noire : de quoi
vérifier d'un coup d'œil qu'une mise à jour a bien pris.

### Un contrôle de cohérence

```bash
python3 tools/verifier_studio.py
```

Le studio a deux moitiés — une page qui affiche des curseurs, un moteur qui lit
des réglages — et rien n'oblige les deux à rester d'accord. Ce contrôle le
vérifie, sans navigateur et sans rien lancer :

- chaque curseur de la page part vraiment au moteur ;
- le rendu repart des mêmes réglages que l'aperçu, au lieu d'en tenir une
  seconde liste ;
- le moteur lit tout ce que la page lui envoie, et rien ne s'envoie dans le
  vide ;
- chaque curseur a sa phrase d'explication, et aucune phrase ne pend dans le
  vide ;
- chaque préréglage pose ses valeurs sur des curseurs qui existent.

Il existe parce que ce genre d'erreur ne se voit pas. Le rendu recopiait à la
main la liste des réglages de l'aperçu : les effets ajoutés ensuite —
kaléidoscope, écho, spectrogramme, avaries, travelling — s'affichaient à
l'écran et **n'arrivaient jamais dans le fichier final**, sans le moindre
message. Le contrôle a été écrit après coup, et la première chose qu'il ait
faite a été de retrouver cette liste oubliée.

## Ce qui a rendu le calcul deux fois plus rapide

Mesuré sur la même machine, la même image, à la suite — une image chargée
(tube de verre, spectrogramme, étincelles, anneau, texture de grille) :

| définition | avant | après | gain |
| --- | --- | --- | --- |
| 960×540 | 220,6 ms | **150,0 ms** | −32 % |
| 1920×1080 | 827,8 ms | **478,9 ms** | −42 % |

L'image n'a pas changé : l'écart maximal est de **1 niveau sur 255**, ce que
seule une soustraction voit. Tout ce qui suit est une réécriture, pas un
compromis de qualité.

**Le flou gaussien tournait en double précision.** Son noyau était calculé en
`float64`, et un seul coefficient `float64` multiplié par une image `float32`
suffit à faire remonter tout le calcul en double : deux fois plus d'octets à
promener à chaque passe, et une conversion à la fin. Le noyau est maintenant
ramené à la précision de l'image. Et comme il est symétrique, les deux côtés
d'un même coefficient s'additionnent avant d'être multipliés, ce qui épargne
une passe par paire. La fonction est passée de **217 à 63 ms** par image en
1080p.

**La déformation du tube lisait l'image par le chemin lent.** `image[indices]`
et `np.take(image, indices)` font la même chose, mais la seconde a une
implémentation spécialisée : mesuré sur une bande de 1080p, **19,3 ms contre
5,7**. Les quatre coins de l'interpolation bilinéaire passent par là à chaque
image.

**Trois multiplications de la taille de l'image, par image, pour rien.** Les
quatre poids de cette interpolation sont figés — ils ne dépendent que du
format — et le masque des bords y est maintenant replié. Le peigne des
scanlines et le vignettage aussi : le vignettage demandait une puissance
fractionnaire sur deux millions de pixels à chaque image. Les précalculer ne
coûte pas de mémoire de pointe, puisque cela supprime aussi les tableaux
temporaires qu'ils créaient.

**Et trois autres coins qui n'en étaient pas.** Les quatre lectures de
l'interpolation sont le même indice lu un peu plus loin : quatre vues décalées
du même tableau suffisent, là où l'on fabriquait trois tableaux d'entiers de la
taille de la bande à chaque image.

Deux pistes essayées et **abandonnées faute de gain mesurable** : remplacer
`np.zeros` par `np.empty` pour la toile (le système rend des pages déjà à zéro,
cela ne coûtait rien), et construire l'agrandissement du grain par diffusion
plutôt que par deux `repeat` (plus lent, le remodelage d'une vue diffusée
repasse par un chemin générique). Elles ne sont pas dans le code.

## STUDIO v2 — la même machine, une page plus claire

```
double-cliquer   Lancer-le-studio-v2.bat        (Windows)
                 Lancer-le-studio-v2.command    (macOS, Linux)
en ligne         python3 tools/studio_v2.py
l'adresse        http://127.0.0.1:8765/v2   (la page classique reste sur /)
```

La v1 pose ses quatre-vingt-dix réglages les uns sous les autres. Tout y est,
mais il faut déjà savoir ce qu'on cherche. La v2 **ne retire rien — elle
range**.

**Trois profondeurs**, dans l'en-tête. `simple` montre **21** réglages, `réglé`
**68**, `tout` les **92**. Rien n'est supprimé : ce qui est caché est à un clic.
Et un réglage ajouté plus tard n'a pas besoin qu'on pense à lui : il apparaît
au niveau `tout` tant qu'on ne lui a pas donné de place plus haut.

**Six onglets** — Machine, Couleurs, Réactions, Avaries, Matière, Rendu — au
lieu d'une colonne de douze cartes à dérouler.

**L'aperçu reste sous les yeux** pendant qu'on règle, collé en haut de sa
colonne, avec le dépôt du fond, la lecture en mouvement et le rendu juste en
dessous.

Le reste est identique : mêmes réglages, mêmes phrases d'explication, mêmes
fréquences annoncées, mêmes préréglages (les fournis et les vôtres), même
moteur. Les deux pages parlent au même serveur et rendent le même fichier.

### Ce que la page montre, et ce qu'elle garde pour plus tard

Quatre-vingt-dix explications affichées en même temps, ce n'est pas de l'aide,
c'est un mur. Chaque phrase **ne se montre qu'au survol** de son réglage — et
le bouton `aide`, dans l'en-tête, les laisse toutes ouvertes pour qui découvre
la page.

Un réglage tient en trois lignes : son **nom et sa valeur sur la même ligne**
(la valeur alignée à droite, en chiffres à chasse fixe), le **curseur**, puis —
discrètement, en vert sombre — la **fréquence** à laquelle il partira.

Et les listes « sur quoi ça part » sont **rangées dans l'effet qu'elles
déclenchent**. Dans la page classique elles suivent leur curseur sans
étiquette : l'œil fait le lien. Recopiées telles quelles, elles se retrouvaient
seules, nommées par leur identifiant — `punchOn`, `shakeOn`, `partsOn`. Chaque
effet est maintenant un seul bloc : le curseur, ce qui le déclenche, sa
fréquence, son explication. La colonne compte **72 blocs pour 92 réglages**.

Le reste est du soin : curseurs dessinés (piste fine, part remplie en vert,
pastille qui s'allume au survol), libellés en bas de casse plutôt qu'en
capitales, cartes en léger dégradé, en-tête collante, et l'aperçu qui reste en
place pendant qu'on règle.

### Deux polices, deux usages

Tout était en chasse fixe. C'est joli pour une console, fatigant pour lire
quatre-vingt-dix libellés et autant d'explications. Le **texte** passe donc en
caractères proportionnels — Segoe UI sous Windows, San Francisco sous macOS, ce
que le système propose ailleurs — et les **nombres** gardent la chasse fixe :
une valeur qui passe de `1` à `0.85` ne doit pas faire sauter toute la ligne.

Restent en chasse fixe : les valeurs des curseurs, les chiffres du morceau
(durée, tempo, nombre de coups), le numéro de version, les noms de fichiers et
l'avancement du rendu. Le titre de la page aussi, parce que c'est une enseigne.

Aucune police n'est téléchargée. Le studio tourne en local, souvent sans
réseau : une police distante ferait attendre la page pour rien, et la ferait
apparaître nue si elle n'arrivait pas.

Les deux pages ont reçu le même traitement.

### Pourquoi les deux pages ne peuvent pas diverger

La v2 **ne recopie pas** la liste des réglages : elle la **lit dans la page de
la v1** au démarrage — l'identifiant, le libellé, les bornes, le pas, la valeur
d'usine, les choix des listes, et jusqu'au déclencheur choisi par défaut pour
chaque effet. Ajouter un curseur à la v1 le fait apparaître dans la v2 sans y
toucher.

`tools/verifier_studio.py` le vérifie : tout réglage de la v1 doit se retrouver
dans la v2, la v2 ne doit rien inventer, et chaque réglage doit avoir une
profondeur.

Deux défauts que cette lecture a fait apparaître tout de suite, et qui
n'existaient que dans la v2 :

- la liste des **images par seconde** s'écrit `<option>30</option>` dans la v1,
  sans attribut `value`. L'extraction ne la voyait pas et la v2 lançait le
  rendu **sans cadence** — le serveur s'arrêtait sur `int() argument must be…`
  au milieu du travail. La valeur est maintenant le texte lui-même à défaut
  d'attribut, et le serveur retombe sur trente images par seconde plutôt que
  d'échouer.

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
