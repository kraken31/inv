# Portefeuille

Petite application web (Flask + HTML/JS vanilla) qui affiche le contenu de la
table `wallet` de la base SQLite `/home/aurelien/dev/div/db/per_analysis.db`,
enrichi du nom de l'action (jointure sur la table `stocks` via la colonne
`isin`).

## Colonnes affichées

| Colonne          | Source                                 |
| ---------------- | -------------------------------------- |
| Nom              | `stocks.name` (jointure sur `isin`)    |
| Quantité         | `wallet.quantity`                      |
| Date achat       | `wallet.date`                          |
| Prix achat       | `wallet.price`                         |
| Montant achat    | `wallet.quantity * wallet.price`       |
| Dividende        | `wallet.dividend`                      |

La base est ouverte en **lecture seule** (`mode=ro`).

## Installation

Depuis un venv (par exemple celui déjà présent dans `inv/`) :

```bash
cd /home/aurelien/dev/div/inv
source bin/activate
pip install -r portefeuille/requirements.txt
```

## Lancement

```bash
cd /home/aurelien/dev/div/inv/portefeuille
python app.py
```

Puis ouvrir <http://127.0.0.1:5001/>.

Variables d'environnement optionnelles :

- `PORTEFEUILLE_DB` : chemin de la base SQLite
  (défaut `/home/aurelien/dev/div/db/per_analysis.db`)
- `PORT` : port HTTP (défaut `5001`)

## API

- `GET /api/wallet` — renvoie un tableau JSON :

  ```json
  [
    {
      "name": "ACCOR",
      "isin": "FR0000120404 AC/EURONEXT PARIS DONNÉES TEMPS RÉEL",
      "quantity": 37,
      "purchase_date": "26/04/2019",
      "purchase_price": 40.235,
      "purchase_amount": 1488.695,
      "dividend": 555.0
    }
  ]
  ```

## Structure

```
portefeuille/
├── app.py
├── requirements.txt
├── templates/
│   └── index.html
└── static/
    ├── styles.css
    └── app.js
```
