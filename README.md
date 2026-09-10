# Speckles-Acquisition

Outils d'**imagerie de contraste de speckle laser (LSCI)** pour caractériser la dynamique d'un échantillon à partir de son motif de speckle. Le code pilote une caméra **FLIR Blackfly S** (via Aravis/GenICam) depuis Python, capture des images de speckle et mesure le **contraste de speckle** en fonction du **temps d'exposition** de la caméra.

> Vous cherchez un guide destiné à un agent IA (Claude, Codex…) ? Voir [`AGENTS.md`](AGENTS.md). La physique détaillée est dans [`LSCI_THEORY.md`](LSCI_THEORY.md). Le journal des séances d'expérience est dans [`INSTALL-Old mac intel.md`](INSTALL-Old%20mac%20intel.md).

---

## À quoi ça sert

Quand une lumière laser cohérente éclaire un milieu diffusant, on observe un motif granuleux : le **speckle**. Sur une image, on quantifie son « piqué » par le **contraste** :

```
K = σ / ⟨I⟩          (écart-type / moyenne de l'intensité dans une région)
```

- Un échantillon **statique** garde un speckle net → contraste élevé.
- Un échantillon **en mouvement** (flux sanguin, mouvement brownien, particules en suspension…) fait scintiller le speckle. Pendant une exposition, le motif se brouille et le contraste **baisse**.

En mesurant **K en fonction du temps d'exposition T**, on obtient une **courbe de décorrélation**. Sa forme et son temps caractéristique τ renseignent sur la vitesse de la dynamique dans l'échantillon. C'est l'objet de ce dépôt : **acquérir cette courbe, proprement, puis l'ajuster à un modèle physique.**

---

## Installation / prérequis

Le matériel de référence est une **FLIR Blackfly S BFS-U3-63S4M** (USB3), mais n'importe quelle caméra compatible **Aravis 0.8 / GenICam** devrait fonctionner.

Il faut :

- **Aravis 0.8 + PyGObject** (bindings caméra). Sur macOS : bibliothèques natives via Homebrew (`/opt/homebrew` sur Apple Silicon, `/usr/local` sur Intel), PyGObject dans le venv.
- **Python 3** avec **numpy, Pillow, scipy, matplotlib**.
- **`mytk`** (bibliothèque GUI [`DCC-Lab/myTk`]) — uniquement pour l'interface graphique live, pas pour les scripts de mesure.

### Environnement virtuel

Depuis la racine du dépôt :

```bash
python3 -m venv .venv
source .venv/bin/activate
python -m pip install -r requirements.txt
```

Pour les sessions suivantes, lancez simplement `source .venv/bin/activate`.
Pour quitter l'environnement : `deactivate`. Le dossier `.venv/` est ignoré par Git.
`requirements.txt` inclut les dépendances d'analyse, `DCC-Lab/myTk` et PyGObject.
Avant l'installation, PyGObject nécessite un compilateur C fonctionnel,
pkg-config et les bibliothèques de développement Cairo et GLib/GObject
introspection. Sur macOS, les outils de développement Xcode doivent être
installés et configurés. Aravis 0.8 et son typelib restent des dépendances
natives à installer séparément ; pip ne les fournit pas.

Avec Homebrew installé, préparez les bibliothèques natives avant pip :

```bash
brew install aravis gobject-introspection cairo pkgconf
source .venv/bin/activate
python -m pip install -r requirements.txt
```

### Note macOS (important)

Sur macOS, la pile scientifique (numpy…) vit dans le Python de python.org, alors qu'Aravis vient de Homebrew. Les deux ne se voient pas sans quelques variables d'environnement. **Bonne nouvelle : c'est géré automatiquement.** Les scripts qui parlent à la caméra se « bootstrapent » eux-mêmes ; il suffit de lancer :

```bash
python3 speckle_viewer.py
```

Pour vérifier que tout est en place :

```bash
python3 -c "import numpy, PIL, scipy, matplotlib; print('OK')"
python3 -c "import mytk; print('mytk OK')"
arv-tool-0.8          # liste les caméras GenICam détectées
```

---

## L'interface graphique (GUI) : `speckle_viewer.py`

Un visualiseur **temps réel** pour cadrer l'expérience, régler la caméra et voir le contraste en direct **avant** de lancer une mesure.

```bash
python3 speckle_viewer.py
```

À gauche : l'image live, avec un **carré de région d'intérêt (ROI)** superposé. **Cliquez** dans l'image pour déplacer la ROI ; c'est sur cette zone que tout est calculé. À droite, les contrôles :

| Contrôle | Rôle |
|---|---|
| **Stop / Start** | Met en pause ou relance l'acquisition. |
| **Center ROI** | Recentre la région d'intérêt. |
| **Save frame…** | Enregistre l'image courante sur disque. |
| **ROI size (px)** | Taille du carré de mesure. |
| **Hardware ROI** | Réduit la lecture au niveau du **capteur** (et non juste à l'affichage) → beaucoup plus d'images/seconde. Indispensable pour la haute vitesse. |
| **Exposure (µs)** | Temps d'exposition. |
| **Gain (dB)** | Gain caméra (à laisser au minimum pour des mesures propres). |
| **Frame rate (fps)** | Cadence d'acquisition. |
| **Pixel format** | `Mono8` (rapide) ou `Mono16` (12 bits, plus de dynamique). |
| **Auto-stretch display** | Étire l'affichage pour la lisibilité (n'affecte **pas** la mesure). |
| **Capture background / Subtract background** | Capture une image de fond (lumière bloquée) et la soustrait en direct. |

**Lecture live (encadré « ROI »)** : contraste, moyenne et max du signal, fraction de pixels saturés, taille de grain de speckle, cadence réelle et Δt entre images. Un **graphe déroulant** trace le contraste dans le temps (que l'on peut désactiver pour garder l'image fluide).

Le contraste est calculé **par tuiles** (la ROI est découpée en N×N tuiles, et K est la moyenne des std/moyenne de chaque tuile) : cela élimine les gradients d'éclairage à l'échelle de la ROI.

À retenir : **surveillez la moyenne du signal**. À très courte exposition, le signal réel peut être à peine au-dessus du piédestal de la caméra ; le bruit de fond a alors son propre contraste qui peut se faire passer pour de la décorrélation. Utilisez la soustraction de fond et, au besoin, `characterize_background.py` (voir ci-dessous).

---

## Le backend : scripts de mesure et d'analyse

Deux familles : ceux qui **parlent à la caméra** (acquisition) et ceux qui **traitent des données déjà enregistrées** (analyse). La plupart se configurent en **éditant le bloc `CONFIG` en haut du fichier**.

### Deux façons de construire la courbe

1. **Balayage d'exposition réel** (extrémité courte, ~8 µs à ~850 µs) : on change réellement l'exposition de la caméra et on mesure K à chaque valeur. Exact, mais les longues expositions saturent.
2. **Sommation d'images / exposition synthétique** (extrémité longue, ~1 ms à ~1 s) : on acquiert des milliers d'images consécutives à haute cadence, puis on en **somme N** pour simuler une exposition N fois plus longue.

Comme les vibrations décalent tout le motif de speckle d'une image à l'autre (ce qui imiterait une fausse décorrélation lors de la sommation), les images sont **recalées** par corrélation de phase (FFT) avant d'être sommées.

### Acquisition (caméra requise)

| Script | Ce qu'il fait |
|---|---|
| **`measure_full_curve.py`** | **Le point d'entrée recommandé.** Acquiert la courbe **complète** (extrémités courte + longue) en **une seule session**, donc même éclairage partout et raccord sans triche. Produit 2 CSV + 1 figure. |
| `measure_contrast_short_exposure.py` | L'extrémité courte seule (balayage d'exposition réel). |
| `measure_contrast_vs_exposure.py` | L'extrémité longue seule (sommation d'images). |
| `find_max_fps_exposure.py` | Détermine le point de fonctionnement haute vitesse (cadence max sous 1000 fps avec la plus longue exposition qui la tient). |
| `take_exposure_sweep.py` | Capture brute : une liste d'expositions, plusieurs images chacune, enregistrées en PNG. À analyser ensuite avec `analyze_sweep.py`. |
| `characterize_background.py` | **À lancer objectif bouché / lumière coupée.** Mesure le fond (offset, courant d'obscurité, bruit spatial) vs exposition, pour distinguer le vrai signal du bruit. |

### Analyse et tracé (aucune caméra requise)

| Script | Ce qu'il fait |
|---|---|
| `analyze_sweep.py` | Transforme un dossier de PNG capturés en `contrast.csv` (ROI fixe pour que les runs soient comparables). |
| `fit_decorrelation.py` | Ajuste la courbe K(T) à deux modèles physiques (exponentielle simple / DWS en racine) et sort β, τ, K0. |
| `stitch_runs.py` | Raccorde un balayage « clair » et un « sombre » (même montage, atténuation différente) en une seule courbe propre. |
| `plot_full_curve.py` | Superpose les deux moitiés (CSV courte + longue) sur un axe log. |
| `compare_decorrelation.py` | Compare plusieurs jeux de données sauvegardés (`*_decorrelation_data/`). |
| `register.py` | Le recalage FFT sous-pixel (utilisé par la sommation d'images). |

---

## Déroulé type d'une expérience

1. **Cadrer et régler** avec `speckle_viewer.py` : positionner la ROI, régler l'exposition/gain, vérifier que le signal n'est ni saturé ni trop faible.
2. **(Optionnel) Mesurer le fond** objectif bouché : `characterize_background.py`.
3. **Acquérir la courbe** en gardant l'éclairage fixe : `python3 measure_full_curve.py`.
4. **Ajuster** : `python3 fit_decorrelation.py` (lit les CSV les plus récents) → temps de décorrélation τ et figures.

---

## Données et bonnes pratiques

- Le contraste **dépend de la région** (échantillonnage du speckle, éclairage). Toute comparaison entre runs doit utiliser **la même ROI**. Comparez les *formes* de courbes plus que les valeurs absolues de K.
- Pour des mesures fiables : sortie **linéaire** (gamma désactivé — fait par le code), **gain minimal**, et attention à la **saturation** (les pixels saturés biaisent le contraste vers le bas).
- **Les images capturées ne sont pas dans le dépôt Git.** Les dossiers `captures/` et `captures_*/` (plusieurs Go de PNG) sont volontairement ignorés ; seuls le code, la doc et les petits CSV de résultats sont versionnés.

---

## Où trouver quoi

- **`LSCI_THEORY.md`** — la physique : modèles de contraste, décorrélation, ajustements.
- **`INSTALL-Old mac intel.md`** — le carnet de laboratoire (réglages et résultats des séances passées). À consulter en premier pour reprendre un travail.
- **`AGENTS.md`** — guide technique pour un agent IA travaillant sur ce code.
- **`*_decorrelation_data/`, `paper_*_data/`, `milk_*_data/`** — jeux de données sauvegardés (CSV + figures + petit README).
