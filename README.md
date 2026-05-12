# inv — Récupération des URLs dividendes Investing.com

Script Selenium qui parcourt les ISIN (`isin2`) de la table `stocks` d'une base SQLite locale et affiche pour chacun l'URL de la page dividendes correspondante sur [fr.investing.com](https://fr.investing.com/).

Exemple de sortie :

```
FR0010340141 https://fr.investing.com/equities/aeroports-paris-dividends
```

## Prérequis système

- Linux (testé sous WSL Ubuntu)
- Python 3.10+
- Chromium ou Google Chrome
- chromedriver (le script attend `/usr/bin/chromedriver` en dur, cf. `get_div.py` ligne 22)
- Une base SQLite à `/home/aurelien/dev/div/inv/inv.db` contenant la table `stocks` avec une colonne `isin2`

Installation des dépendances système (Debian/Ubuntu) :

```bash
sudo apt update
sudo apt install -y chromium-browser chromium-chromedriver python3-venv
```

Vérifier que les versions de Chromium et chromedriver sont compatibles :

```bash
chromium --version
chromedriver --version
```

## Installation du projet

```bash
cd /home/aurelien/dev/div/inv

# Création du venv (si pas déjà présent)
python3 -m venv .

# Activation
source bin/activate

# Installation des dépendances Python
pip install -r requirements.txt
```

Le venv est créé directement à la racine du dossier (`bin/`, `lib/`, `include/`, `pyvenv.cfg`), pas dans un sous-dossier `venv/`. Le `.gitignore` est configuré pour ignorer ces dossiers.

## Lancement

Avec le venv activé :

```bash
python get_div.py
```

Le script :

1. Ouvre la base SQLite en read-only et récupère tous les `isin2` non vides de la table `stocks` (~1 338 lignes).
2. Lance Chrome en mode headless.
3. Pour chaque ISIN, va sur `https://fr.investing.com/search/?q=<isin>&tab=quotes`, récupère le premier résultat et affiche `<isin> <url>-dividends`.
4. En cas d'échec (timeout, ISIN introuvable, blocage anti-bot), affiche `<isin> NOT_FOUND` et passe au suivant.

Durée estimée : 1 à 2 heures pour les 1 338 ISINs.

Pour rediriger la sortie vers un fichier :

```bash
python get_div.py | tee dividends_urls.txt
```

Pour sortir du venv en fin de session :

```bash
deactivate
```

## Dépannage

### `error: externally-managed-environment` lors du `pip install`

Le venv n'est pas activé dans le shell courant. Vérifier avec `which pip` — il doit pointer vers `.../inv/bin/pip`. Sinon, refaire `source bin/activate`.

### Beaucoup de `NOT_FOUND` en sortie

Plusieurs causes possibles :

- Le sélecteur CSS `a.js-inner-all-results-quote-item` a changé sur investing.com → inspecter la page de recherche manuellement et adapter le sélecteur dans `get_div.py`.
- Cloudflare bloque les requêtes headless → envisager [`undetected-chromedriver`](https://github.com/ultrafunkamsterdam/undetected-chromedriver) ou basculer sur l'API publique `https://api.investing.com/api/financialdata/search/?q=<isin>`.

### Erreurs Chrome / DevTools sous WSL

Le script utilise déjà `--headless` et `--no-sandbox`. Si le driver crashe au démarrage, ajouter `--disable-dev-shm-usage` aux options Chrome dans `get_div.py`.

### Chemin du chromedriver

Le chemin `/usr/bin/chromedriver` est codé en dur. Sur une autre machine, soit adapter cette ligne, soit utiliser `webdriver-manager` (déjà dans `requirements.txt`) :

```python
from webdriver_manager.chrome import ChromeDriverManager
service = Service(ChromeDriverManager().install())
```

## Structure

```
inv/
├── get_div.py              # script principal (ce README)
├── stock_screener_paris.py # autre script (screener Boursorama)
├── requirements.txt
├── .gitignore
├── portefeuille/           # application web Portefeuille (cf. ci-dessous)
└── README.md
```

La base SQLite n'est pas dans ce dossier ; elle est attendue à `/home/aurelien/dev/div/inv/inv.db`.

---

## Application web Portefeuille

Petite application Flask (+ HTML/CSS/JS vanilla) servie depuis le sous-dossier
[`portefeuille/`](portefeuille/). Elle affiche le contenu de la table `wallet`
enrichi de :

- le nom de l'action (jointure sur `stocks.isin`),
- le dernier cours connu et sa date (table `pricing`, `MAX(date)` par ISIN),
- la valorisation courante, la +/- value, la performance et le PER,
- la liquidité (table `walletDetails`),
- un tableau de synthèse (Valeur achat, Date valorisation, Valorisation,
  Dividendes, Liquidité, +/- Value, Perf).

Coloration des lignes en fonction du PER courant :

- vert si `0 ≤ PER ≤ 10`
- rouge si `PER > 10` ou `PER < 0`

La base SQLite est ouverte en **lecture seule** (`mode=ro`).

### Installation

Depuis le venv déjà présent dans `inv/` :

```bash
cd /home/aurelien/dev/div/inv
source bin/activate
pip install -r portefeuille/requirements.txt
```

### Lancement

```bash
cd /home/aurelien/dev/div/inv/portefeuille
python app.py
```

Puis ouvrir <http://127.0.0.1:5001/>.

Variables d'environnement optionnelles :

- `PORTEFEUILLE_DB` — chemin de la base SQLite
  (défaut : `/home/aurelien/dev/div/inv/inv.db`)
- `PORT` — port HTTP (défaut : `5001`)

Exemple avec un autre port :

```bash
PORT=5057 python app.py
```

### Endpoints API

- `GET /` — page HTML (écran « Portefeuille »).
- `GET /api/wallet` — tableau JSON des lignes du portefeuille (nom, ISIN,
  quantité, dates et prix d'achat, dividendes, dernier cours, +/- value,
  perf, PER, etc.).
- `GET /api/liquidite` — `{"liquidite": <valeur>}` (table `walletDetails`).

### Détail des fichiers

```
portefeuille/
├── app.py                  # API Flask + service de la page
├── requirements.txt        # Flask>=3.0
├── README.md               # README dédié (plus détaillé)
├── templates/
│   └── index.html          # page « Portefeuille »
└── static/
    ├── styles.css
    └── app.js              # tri, filtre, tableau de synthèse
```
