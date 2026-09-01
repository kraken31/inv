# Documentation fonctionnelle — Portefeuille

Application personnelle de suivi d’un portefeuille d’actions cotées sur **Euronext Paris** (compartiments A, B, C et Euronext Growth). Elle combine :

- un **référentiel de titres** alimenté depuis Euronext et Yahoo Finance ;
- un **portefeuille** (positions détenues, liquidité) ;
- des **écrans de screening** (PER, RSI, rendement, croissance des résultats) ;
- une **fiche titre** détaillée.

L’interface web est une application Flask locale (`portefeuille/`), branchée sur la base SQLite `inv.db`. Les données de marché ne sont pas temps réel : elles sont rafraîchies à la demande via des scripts Yahoo Finance.

---

## 1. Objectifs

| Objectif | Description |
| --- | --- |
| Suivre le portefeuille | Valoriser les positions, mesurer la performance et les dividendes perçus, tenir la liquidité. |
| Screener le marché Paris | Identifier des titres à PER bas, RSI survendu, rendement élevé ou résultats nets en croissance. |
| Consulter un titre | Afficher PER, RSI, historique de dividendes et de résultats, rendements n / n-1 / 5 ans / 10 ans. |
| Maintenir les données | Relancer cours, dividendes et résultats depuis l’UI, sans quitter le navigateur. |

Hors périmètre actuel :

- autres places (Amsterdam, Bruxelles, US, ETF hors listing actions) ;
- ordre de bourse, courtier, fiscalité ;
- multi-utilisateurs / authentification (usage local).

---

## 2. Univers de titres

Les titres gérés sont les **actions cotées à Paris**, récupérées par `get_stocks.py` :

- **XPAR** — Euronext Paris, compartiments A, B et C ;
- **ALXP** — Euronext Growth Paris.

Chaque titre est identifié par son **mnémo Euronext** (`AC`, `AI`, `AIR`…). Côté Yahoo Finance, le ticker est `<mnémo>.PA` (ex. `AC.PA`).

Le référentiel stocke :

- le nom (Yahoo, à défaut le nom Euronext) ;
- le nombre d’actions en circulation (`sharesOutstanding`), utilisé pour la capitalisation et le PER.

---

## 3. Concepts métier

### 3.1 Position

Une ligne de portefeuille = **un titre** (un mnémo), avec :

- quantité détenue ;
- date d’achat ;
- prix d’achat unitaire ;
- cumul de dividendes perçus (saisi manuellement, en euros).

Il n’y a **qu’une ligne par titre**. On ne gère pas plusieurs lots d’achat distincts.

### 3.2 Valorisation

Le **dernier cours** connu vient de la table des prix (clôture Yahoo, historique 6 mois).

| Indicateur | Formule |
| --- | --- |
| Montant achat | quantité × prix d’achat |
| Montant actuel | quantité × dernier cours |
| Perf. dividende | 100 × dividendes / montant achat |
| +/- value (ligne) | montant actuel + dividendes − montant achat |
| Perf. (ligne) | 100 × +/- value / montant achat |

Sur le **tableau de synthèse** du portefeuille, +/- value et performance **n’incluent pas les dividendes** (valorisation boursière seule). Les dividendes y figurent dans une colonne séparée.

### 3.3 PER

Le PER n’est pas celui de Yahoo : il est **recalculé** à chaque refresh des cours.

```
capitalisation = dernier cours × nombre d’actions en circulation
PER = capitalisation / résultat net de l’année la plus récente
```

Règles :

- résultat net **négatif** → PER = **-1** ;
- résultat net **nul ou inconnu**, ou quantité inconnue → PER **vide** ;
- sinon PER = capitalisation / résultat net.

Coloration « bon PER » dans l’UI (portefeuille, PER) : **0 ≤ PER ≤ 10** (vert), sinon rouge. Les écrans RSI / Rendement / fiche titre utilisent **0 < PER < 10**.

### 3.4 RSI

RSI(14) journalier, méthode de Wilder (identique à TradingView / Boursorama), calculé sur ~6 mois de clôtures.

- **< 30** : survendu (surligné) ;
- **> 70** : suracheté (surligné sur le portefeuille et la fiche titre) ;
- historique trop court ou moyenne des pertes nulle → RSI vide.

### 3.5 Rendement

```
rendement = 100 × dividende annuel / dernier cours
```

Quatre horizons :

- année civile **n** (année en cours) ;
- année **n − 1** ;
- **moyenne 5 ans** : somme des dividendes des 5 dernières années civiles / 5 ;
- **moyenne 10 ans** : idem sur 10 ans.

Un dividende **exceptionnel** (montant > 50 % du cours actuel) est **exclu** des moyennes 5 et 10 ans. Les moyennes divisent toujours par 5 ou 10, même si certaines années manquent.

### 3.6 Année de référence « n » (Croissance)

Sur l’écran Croissance, l’année **n** n’est pas l’année calendaire : c’est la **dernière année de résultats** pour laquelle au moins **50 titres** ont un résultat publié. Cela évite de basculer trop tôt sur une année où seuls quelques émetteurs ont déjà déposé leurs comptes.

---

## 4. Écrans

Navigation latérale commune : Portefeuille, Action, PER, RSI, Rendement, Croissance.

Barre supérieure commune : boutons **↻ Cours**, **↻ Dividendes**, **↻ Résultats** (voir § 6).

Recherche (sauf fiche Action) : filtre local sur **nom ou mnémo**. Les tableaux sont triables. Le nom d’un titre mène à sa fiche (`/action?id=…`).

### 4.1 Portefeuille (`/`)

Vue d’ensemble des positions.

**Synthèse** (agrégats des lignes affichées, donc après filtre) :

- valeur d’achat, date de valorisation (plus récente date de cours), valorisation, dividendes, liquidité, +/- value, perf.

**Liquidité** : cash du compte, saisie manuelle (crayon sur la cellule). Elle n’entre pas dans la +/- value.

**Tableau des lignes** : quantité, dates et prix d’achat, montant achat, dividende, date/prix/montant actuels, perf. div., +/- value, perf., PER, RSI.

**Actions** :

- **+ Ajouter** : choix parmi les titres du référentiel **absents** du portefeuille ; quantité > 0 ; date, prix, dividende ≥ 0.
- **Modifier** : quantité, date, prix, dividende (la quantité peut être 0).
- **Supprimer** : confirmation, retire la ligne du portefeuille (pas du référentiel).

### 4.2 Action (`/action`)

Fiche d’un titre du référentiel (pas seulement ceux du portefeuille).

- recherche avec **autocomplétion** (nom ou mnémo, 20 résultats max) ;
- URL bookmarkable : `/action?id=AC` ;
- KPI PER et RSI (avec date) ;
- rendements n, n−1, 5 ans, 10 ans ;
- tableaux d’historique **dividendes par année** et **résultats nets par année** (notation compacte, ex. « 40,6 M »).

### 4.3 PER (`/per`)

Screener « valorisation basse ».

Titres retenus :

- PER **strictement** entre 0 et 10 ;
- dernier PER daté d’au plus **7 jours** par rapport à la date de PER la plus récente du marché.

Tri par défaut : PER croissant. Export CSV.

### 4.4 RSI (`/rsi`)

Screener « survendu ».

Titres retenus :

- RSI **< 30** ;
- un PER renseigné (quel que soit sa valeur) ;
- même fenêtre de fraîcheur de **7 jours**.

Tri par défaut : RSI croissant. La ligne est verte si 0 < PER < 10, rouge sinon. Export CSV.

### 4.5 Rendement (`/rendement`)

Screener de rendement sur **tous les titres qui ont un PER**.

Colonnes : PER, dividende et rendement pour n, n−1, moyenne 5 ans, moyenne 10 ans.

Tri par défaut : rendement moyen 5 ans décroissant. Coloration PER : vert si 0 < PER < 10, rouge sinon. Export CSV.

### 4.6 Croissance (`/securite`)

Screener « qualité des résultats + valorisation ».

Titres retenus :

- PER dans **]0, 10[** et frais (7 jours, comme PER) ;
- **quatre exercices consécutifs** n−3 … n avec un résultat net **renseigné et strictement positif**.

Coloration selon la **croissance stricte consécutive** des résultats, en partant du plus récent :

| Croissance | Couleur |
| --- | --- |
| 4 ans (n−3 < n−2 < n−1 < n) | vert |
| 3 ans (n−2 < n−1 < n) | jaune |
| 2 ans (n−1 < n) | orange |
| sinon | rouge |

Les résultats sont affichés en notation compacte. Export CSV.

---

## 5. Modèle de données (vue fonctionnelle)

Base : `inv.db` (surcharge possible via `PORTEFEUILLE_DB`).

| Entité | Rôle |
| --- | --- |
| **stocks** | Référentiel : mnémo (`id`), nom, nombre d’actions. |
| **wallet** | Positions détenues : quantité, date d’achat (`JJ/MM/AAAA`), prix, cumul de dividendes. |
| **walletDetails** | Une seule ligne : liquidité. |
| **pricing** | Historique quotidien : cours, capitalisation, PER, RSI. Une ligne par (titre, date). |
| **dividends** | Dividendes agrégés par année civile (somme Yahoo). |
| **results** | Résultat net annuel (Yahoo `income_stmt`). |

Les lectures métier se font en **lecture seule**. Les écritures UI concernent uniquement `wallet` et `walletDetails`. Les tables de marché sont mises à jour par les scripts de refresh.

---

## 6. Mise à jour des données

Trois jobs indépendants, lançables en parallèle depuis n’importe quel écran. Un même job ne peut pas être lancé deux fois à la fois. L’UI affiche l’avancement (`[i/n]`) puis le succès ou l’échec.

| Bouton | Script | Effet |
| --- | --- | --- |
| ↻ Cours | `get_pricing.py` | Dernier cours, capitalisation, PER, RSI pour **tous** les titres du référentiel. |
| ↻ Dividendes | `get_dividends.py` | Historique de dividendes par année. |
| ↻ Résultats | `get_results.py` | Historique de résultat net par année. |

Caractéristiques communes :

- source **Yahoo Finance** (`yfinance`) ;
- ticker `\<id\>.PA` ;
- 3 workers, backoff en cas de rate-limit (5 / 15 / 45 s) ;
- **idempotents** : on écrase seulement les (id, date) ou (id, année) renvoyés ; le reste de l’historique est conservé.

Le référentiel lui-même (`get_stocks.py`) n’est **pas** exposé dans l’UI : il se lance en ligne de commande. Il télécharge le CSV officiel Euronext (`mics=XPAR,ALXP`), ne garde que les lignes dont le marché mentionne « Paris », puis enrichit nom et flottant via Yahoo.

---

## 7. Parcours utilisateur types

1. **Tenir le portefeuille** — Ajouter / modifier une ligne, saisir la liquidité, lancer ↻ Cours, relire synthèse et +/- value.
2. **Chercher une idée d’achat** — PER (pas cher) ∩ RSI (survendu) ∩ Croissance (bénéfices croissants), puis ouvrir la fiche Action.
3. **Comparer les rendements** — écran Rendement, tri 5 ans, croiser avec le PER coloré.
4. **Analyser un titre** — fiche Action depuis n’importe quel tableau, ou recherche directe.

---

## 8. Contraintes et limites fonctionnelles

- **Marché unique** : Paris uniquement ; le suffixe `.PA` est imposé partout.
- **Pas de lots multiples** : une position = un titre.
- **Dividendes du portefeuille** : saisie manuelle (cumul), distincte de l’historique Yahoo utilisé pour le screening.
- **Cours différés** : clôture Yahoo, pas le carnet d’ordres Euronext.
- **Couverture Yahoo** : titres délistés, illiquides ou mal mappés peuvent n’avoir ni cours, ni PER, ni dividendes.
- **Rate-limit Yahoo** : un refresh complet du référentiel (~600 titres) peut durer plusieurs minutes.
- **Application locale** : `127.0.0.1`, port 5001 par défaut (`PORT` surchargeable). Aucune auth.

---

## 9. Lancement (rappel)

```bash
cd /home/aurelien/dev/div/inv
source bin/activate
cd portefeuille
python app.py
```

Ouvrir <http://127.0.0.1:5001/>.

Scripts de données (venv activé, à la racine du projet) :

```bash
python get_stocks.py      # référentiel Euronext Paris
python get_pricing.py     # cours / PER / RSI
python get_dividends.py   # dividendes
python get_results.py     # résultats nets
```
