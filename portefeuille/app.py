"""Application web Portefeuille.

API Flask qui lit la table `wallet` (jointure sur `stocks` via la colonne
`id`) de la base SQLite locale et expose les données au front.

Plusieurs portefeuilles coexistent, distingués par la colonne
`proprietaire` sur `wallet` / `walletDetails` et `walletETF` /
`walletETFDetails`.
"""

import math
import os
import re
import sqlite3
import subprocess
import sys
import threading
import time
from datetime import date
from pathlib import Path

from flask import Flask, jsonify, render_template, request


DB_PATH = os.environ.get(
    "PORTEFEUILLE_DB",
    "/home/aurelien/dev/div/inv/inv.db",
)

# Répertoire racine où vivent les scripts de refresh (get_pricing.py,
# get_dividends.py, …). Résolu relativement à app.py pour rester
# portable.
SCRIPTS_DIR = Path(__file__).resolve().parent.parent

# Seuil minimal de tickers pour qu'une année soit considérée
# « publiée » par yfinance et utilisable comme année de référence
# sur la page Croissance. En dessous, l'année est ignorée (typiquement
# une année récente où seuls quelques tickers ont déjà publié leur
# compte de résultat, ce qui viderait artificiellement le tableau).
# La valeur est aussi répliquée en dur dans la CTE `year_n` de
# /api/securite ci-dessous — penser à les garder synchronisés.
SECURITE_YEAR_MIN_COVERAGE = 50

# Propriétaire du portefeuille historique (lignes existantes au moment
# de l'ajout de la colonne `proprietaire`).
DEFAULT_PROPRIETAIRE = "Aurélien"
PROPRIETAIRE_MAX_LEN = 80

app = Flask(__name__)

_SCHEMA_READY = False
_SCHEMA_LOCK = threading.Lock()


# Registry des jobs de refresh exposés par /api/refresh/<job>. Pour
# ajouter un nouveau script (par ex. get_results.py), il suffit d'ajouter
# une entrée ici et de placer un bouton dans la nav.
#
# Chaque job possède un état partagé manipulé sous son propre lock :
#   - process     : subprocess.Popen en cours, ou None
#   - started_at  : epoch seconds (float) ou None
#   - finished_at : epoch seconds (float) ou None
#   - exit_code   : int (0 = succès, autre = échec) ou None tant que
#                   pas terminé
#   - log_path    : chemin du fichier de logs du dernier run
#
# Les jobs tournent indépendamment : on peut lancer get_pricing.py et
# get_dividends.py en parallèle. Chacun gère son propre rate-limit Yahoo.
def _new_state() -> dict:
    return {
        "process": None,
        "started_at": None,
        "finished_at": None,
        "exit_code": None,
        "log_path": None,
    }


_REFRESH_JOBS: dict[str, dict] = {
    "pricing": {
        "script": SCRIPTS_DIR / "get_pricing.py",
        "lock": threading.Lock(),
        "state": _new_state(),
    },
    "dividends": {
        "script": SCRIPTS_DIR / "get_dividends.py",
        "lock": threading.Lock(),
        "state": _new_state(),
    },
    "results": {
        "script": SCRIPTS_DIR / "get_results.py",
        "lock": threading.Lock(),
        "state": _new_state(),
    },
    "pricing_etf": {
        "script": SCRIPTS_DIR / "get_pricing_etf.py",
        "lock": threading.Lock(),
        "state": _new_state(),
    },
}


def get_db() -> sqlite3.Connection:
    """Connexion SQLite en lecture seule."""
    if not Path(DB_PATH).exists():
        raise FileNotFoundError(f"Base introuvable: {DB_PATH}")
    conn = sqlite3.connect(f"file:{DB_PATH}?mode=ro", uri=True)
    conn.row_factory = sqlite3.Row
    return conn


def get_db_rw() -> sqlite3.Connection:
    """Connexion SQLite en lecture/écriture (pour les rares mutations
    déclenchées depuis l'UI, p.ex. mise à jour de la liquidité)."""
    if not Path(DB_PATH).exists():
        raise FileNotFoundError(f"Base introuvable: {DB_PATH}")
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn


def json_pea(value) -> bool | None:
    """SQLite INTEGER 0/1/NULL → JSON false/true/null."""
    if value is None:
        return None
    return bool(value)


def parse_pea_filter(raw) -> int | None:
    """Filtre query `pea` : None = tous, 1 = éligible, 0 = non."""
    if raw is None:
        return None
    s = str(raw).strip().lower()
    if not s:
        return None
    if s in ("1", "true", "oui", "yes"):
        return 1
    if s in ("0", "false", "non", "no"):
        return 0
    return None


def iso_to_fr_date(date_str: str) -> str:
    """Convertit une date `YYYY-MM-DD` (telle qu'envoyée par
    `<input type="date">`) vers le format de stockage `DD/MM/YYYY`.
    Suppose que le format ISO a déjà été validé en amont.
    """
    return f"{date_str[8:10]}/{date_str[5:7]}/{date_str[0:4]}"


class ProprietaireError(ValueError):
    """Nom de propriétaire manquant ou invalide."""


def _table_columns(conn: sqlite3.Connection, table: str) -> set[str]:
    return {row[1] for row in conn.execute(f"PRAGMA table_info({table})")}


def _ensure_proprietaire_column(conn: sqlite3.Connection, table: str) -> None:
    if "proprietaire" not in _table_columns(conn, table):
        conn.execute(
            f"ALTER TABLE {table} ADD COLUMN proprietaire "
            f"TEXT NOT NULL DEFAULT '{DEFAULT_PROPRIETAIRE}'"
        )
    conn.execute(
        f"UPDATE {table} SET proprietaire = ? "
        "WHERE proprietaire IS NULL OR TRIM(proprietaire) = ''",
        (DEFAULT_PROPRIETAIRE,),
    )


def ensure_schema() -> None:
    """Ajoute `proprietaire` aux tables de portefeuille si besoin,
    rattache les lignes existantes à DEFAULT_PROPRIETAIRE, et pose
    les index d'unicité (un titre par portefeuille, un détail par
    propriétaire).
    """
    global _SCHEMA_READY
    if _SCHEMA_READY:
        return
    with _SCHEMA_LOCK:
        if _SCHEMA_READY:
            return
        with get_db_rw() as conn:
            for table in (
                "wallet",
                "walletDetails",
                "walletETF",
                "walletETFDetails",
            ):
                _ensure_proprietaire_column(conn, table)
            pricing_etf_cols = _table_columns(conn, "pricingETF")
            if pricing_etf_cols and "rsi" not in pricing_etf_cols:
                conn.execute(
                    "ALTER TABLE pricingETF ADD COLUMN rsi REAL"
                )
            etf_cols = _table_columns(conn, "etf")
            if etf_cols and "pea" not in etf_cols:
                conn.execute("ALTER TABLE etf ADD COLUMN pea INTEGER")
            conn.execute(
                "CREATE UNIQUE INDEX IF NOT EXISTS "
                "wallet_proprietaire_id "
                "ON wallet (proprietaire COLLATE NOCASE, id)"
            )
            conn.execute(
                "CREATE UNIQUE INDEX IF NOT EXISTS "
                "walletDetails_proprietaire "
                "ON walletDetails (proprietaire COLLATE NOCASE)"
            )
            conn.execute(
                "CREATE UNIQUE INDEX IF NOT EXISTS "
                "walletETF_proprietaire_id "
                "ON walletETF (proprietaire COLLATE NOCASE, id)"
            )
            conn.execute(
                "CREATE UNIQUE INDEX IF NOT EXISTS "
                "walletETFDetails_proprietaire "
                "ON walletETFDetails (proprietaire COLLATE NOCASE)"
            )
            conn.commit()
        _SCHEMA_READY = True


@app.before_request
def _migrate_schema():
    try:
        ensure_schema()
    except FileNotFoundError:
        pass


def normalize_proprietaire(raw) -> str:
    """Nettoie un nom de propriétaire. Lève ProprietaireError si
    vide ou trop long.
    """
    if raw is None:
        raise ProprietaireError("Propriétaire requis")
    name = str(raw).strip()
    if not name:
        raise ProprietaireError("Propriétaire requis")
    if len(name) > PROPRIETAIRE_MAX_LEN:
        raise ProprietaireError("Nom de propriétaire trop long")
    return name


def proprietaire_from_request(payload: dict | None = None) -> str:
    raw = None
    if payload:
        raw = payload.get("proprietaire")
    if raw is None:
        raw = request.args.get("proprietaire")
    return normalize_proprietaire(raw)


def owner_exists(conn: sqlite3.Connection, proprietaire: str) -> bool:
    row = conn.execute(
        """
        SELECT 1
        FROM wallet
        WHERE proprietaire = ? COLLATE NOCASE
        UNION ALL
        SELECT 1
        FROM walletDetails
        WHERE proprietaire = ? COLLATE NOCASE
        LIMIT 1
        """,
        (proprietaire, proprietaire),
    ).fetchone()
    return row is not None


def etf_owner_exists(conn: sqlite3.Connection, proprietaire: str) -> bool:
    row = conn.execute(
        """
        SELECT 1
        FROM walletETF
        WHERE proprietaire = ? COLLATE NOCASE
        UNION ALL
        SELECT 1
        FROM walletETFDetails
        WHERE proprietaire = ? COLLATE NOCASE
        LIMIT 1
        """,
        (proprietaire, proprietaire),
    ).fetchone()
    return row is not None


def parse_position_payload(payload: dict) -> tuple[str, int, float, float, str]:
    """Extrait id / quantity / price / dividend / date d'une ligne
    de portefeuille. Lève ValueError avec un message affichable.
    """
    try:
        stock_id = str(payload["id"]).strip()
        quantity = int(payload["quantity"])
        price = float(payload["price"])
        dividend = float(payload["dividend"])
        date_str = str(payload["date"])
    except (KeyError, TypeError, ValueError) as exc:
        raise ValueError("Champs invalides") from exc

    if not stock_id:
        raise ValueError("Action requise")
    if quantity <= 0:
        raise ValueError("Quantité doit être > 0")
    if price < 0 or dividend < 0:
        raise ValueError("Valeurs négatives interdites")
    if not math.isfinite(price) or not math.isfinite(dividend):
        raise ValueError("Valeurs non finies")
    if not re.fullmatch(r"\d{4}-\d{2}-\d{2}", date_str):
        raise ValueError("Date invalide (YYYY-MM-DD)")
    return stock_id, quantity, price, dividend, date_str


def parse_etf_position_payload(payload: dict) -> tuple[str, float, float, str]:
    """Extrait id / quantity / price / date d'une ligne ETF.
    Lève ValueError avec un message affichable.
    """
    try:
        etf_id = str(payload["id"]).strip()
        quantity = float(payload["quantity"])
        price = float(payload["price"])
        date_str = str(payload["date"])
    except (KeyError, TypeError, ValueError) as exc:
        raise ValueError("Champs invalides") from exc

    if not etf_id:
        raise ValueError("ETF requis")
    if quantity <= 0:
        raise ValueError("Quantité doit être > 0")
    if price < 0:
        raise ValueError("Valeurs négatives interdites")
    if not math.isfinite(quantity) or not math.isfinite(price):
        raise ValueError("Valeurs non finies")
    if not re.fullmatch(r"\d{4}-\d{2}-\d{2}", date_str):
        raise ValueError("Date invalide (YYYY-MM-DD)")
    return etf_id, quantity, price, date_str


@app.route("/")
def index():
    return render_template("index.html")


@app.route("/per")
def per_page():
    return render_template("per.html")


@app.route("/rsi")
def rsi_page():
    return render_template("rsi.html")


@app.route("/rsi-etf")
def rsi_etf_page():
    return render_template("rsi_etf.html")


@app.route("/rendement")
def rendement_page():
    current_year = date.today().year
    return render_template(
        "rendement.html",
        year=current_year,
        year_prev=current_year - 1,
    )


@app.route("/action")
def action_page():
    return render_template("action.html")


@app.route("/etf")
def etf_page():
    return render_template("etf.html")


@app.route("/portefeuille-etf")
def portefeuille_etf_page():
    return render_template("portefeuille_etf.html")


@app.route("/securite")
def securite_page():
    # On dérive l'année « n » depuis la table results : c'est la
    # dernière année réellement présente, qui dépend de yfinance et
    # non du calendrier. On exige une couverture minimale
    # (SECURITE_YEAR_MIN_COVERAGE tickers) pour ignorer les années
    # « entamées » où seuls quelques émetteurs ont déjà publié — sans
    # ce garde-fou, year_n bascule trop tôt et la page se vide.
    # Fallback sur l'année précédente si la table est vide / illisible.
    try:
        with get_db() as conn:
            row = conn.execute(
                """
                SELECT MAX(year) AS n FROM (
                    SELECT year
                    FROM results
                    WHERE year IS NOT NULL
                    GROUP BY year
                    HAVING COUNT(*) >= ?
                )
                """,
                (SECURITE_YEAR_MIN_COVERAGE,),
            ).fetchone()
            year_n = (
                row["n"]
                if row and row["n"] is not None
                else date.today().year - 1
            )
    except (FileNotFoundError, sqlite3.Error):
        year_n = date.today().year - 1
    return render_template(
        "securite.html",
        year_n=year_n,
        year_n1=year_n - 1,
        year_n2=year_n - 2,
        year_n3=year_n - 3,
    )


@app.route("/api/portefeuilles")
def api_portefeuilles():
    """Liste les portefeuilles (un par propriétaire) avec une
    synthèse de valorisation.
    """
    query = """
        WITH latest_price AS (
            SELECT p.id, p.date, p.price
            FROM pricing p
            JOIN (
                SELECT id, MAX(date) AS max_date
                FROM pricing
                GROUP BY id
            ) m ON m.id = p.id AND m.max_date = p.date
        ),
        owners AS (
            SELECT proprietaire FROM wallet
            WHERE proprietaire IS NOT NULL AND TRIM(proprietaire) != ''
            UNION
            SELECT proprietaire FROM walletDetails
            WHERE proprietaire IS NOT NULL AND TRIM(proprietaire) != ''
        ),
        agg AS (
            SELECT
                w.proprietaire,
                COUNT(*) AS nb_lignes,
                SUM(w.quantity * w.price) AS purchase_amount,
                SUM(COALESCE(w.quantity * lp.price, 0)) AS current_amount,
                SUM(COALESCE(w.dividend, 0)) AS dividend,
                MAX(lp.date) AS current_date
            FROM wallet w
            LEFT JOIN latest_price lp ON lp.id = w.id
            GROUP BY w.proprietaire
        )
        SELECT
            o.proprietaire AS proprietaire,
            COALESCE(a.nb_lignes, 0) AS nb_lignes,
            COALESCE(a.purchase_amount, 0) AS purchase_amount,
            COALESCE(a.current_amount, 0) AS current_amount,
            COALESCE(a.dividend, 0) AS dividend,
            a.current_date AS current_date,
            d.liquidite AS liquidite,
            COALESCE(a.current_amount, 0)
                - COALESCE(a.purchase_amount, 0) AS plus_minus_value,
            CASE WHEN COALESCE(a.purchase_amount, 0) > 0
                 THEN 100.0 * (COALESCE(a.current_amount, 0)
                               - COALESCE(a.purchase_amount, 0))
                              / a.purchase_amount
            END AS perf
        FROM owners o
        LEFT JOIN agg a
            ON a.proprietaire = o.proprietaire COLLATE NOCASE
        LEFT JOIN walletDetails d
            ON d.proprietaire = o.proprietaire COLLATE NOCASE
        ORDER BY o.proprietaire COLLATE NOCASE
    """
    try:
        with get_db() as conn:
            rows = [dict(r) for r in conn.execute(query)]
    except FileNotFoundError as exc:
        return jsonify({"error": str(exc)}), 500
    except sqlite3.Error as exc:
        return jsonify({"error": f"Erreur SQLite: {exc}"}), 500
    return jsonify(rows)


@app.route("/api/portefeuilles", methods=["POST"])
def api_portefeuilles_create():
    """Crée un portefeuille : une ligne `walletDetails` (liquidité
    à 0) et une première position dans `wallet`. Refuse si le
    propriétaire existe déjà (409) ou si l'action est inconnue (404).
    """
    payload = request.get_json(silent=True) or {}
    try:
        proprietaire = normalize_proprietaire(payload.get("proprietaire"))
        stock_id, quantity, price, dividend, date_str = (
            parse_position_payload(payload)
        )
    except ProprietaireError as exc:
        return jsonify({"error": str(exc)}), 400
    except ValueError as exc:
        return jsonify({"error": str(exc)}), 400

    try:
        with get_db_rw() as conn:
            if owner_exists(conn, proprietaire):
                return (
                    jsonify({
                        "error": "Un portefeuille existe déjà "
                        "pour ce propriétaire",
                    }),
                    409,
                )
            if not conn.execute(
                "SELECT 1 FROM stocks WHERE id = ?", (stock_id,)
            ).fetchone():
                return jsonify({"error": "Action inconnue"}), 404
            conn.execute(
                "INSERT INTO walletDetails (liquidite, proprietaire) "
                "VALUES (?, ?)",
                (0, proprietaire),
            )
            conn.execute(
                "INSERT INTO wallet "
                "(id, quantity, date, price, dividend, proprietaire) "
                "VALUES (?, ?, ?, ?, ?, ?)",
                (
                    stock_id,
                    quantity,
                    iso_to_fr_date(date_str),
                    price,
                    dividend,
                    proprietaire,
                ),
            )
            conn.commit()
    except FileNotFoundError as exc:
        return jsonify({"error": str(exc)}), 500
    except sqlite3.IntegrityError:
        return (
            jsonify({
                "error": "Un portefeuille existe déjà pour ce propriétaire",
            }),
            409,
        )
    except sqlite3.Error as exc:
        return jsonify({"error": f"Erreur SQLite: {exc}"}), 500
    return jsonify({"created": proprietaire}), 201


@app.route("/api/wallet")
def api_wallet():
    try:
        proprietaire = proprietaire_from_request()
    except ProprietaireError as exc:
        return jsonify({"error": str(exc)}), 400

    query = """
        WITH latest_price AS (
            SELECT p.id, p.date, p.price, p.per, p.rsi
            FROM pricing p
            JOIN (
                SELECT id, MAX(date) AS max_date
                FROM pricing
                GROUP BY id
            ) m ON m.id = p.id AND m.max_date = p.date
        )
        SELECT
            COALESCE(s.name, w.id)     AS name,
            w.id                       AS id,
            w.quantity                 AS quantity,
            w.date                     AS purchase_date,
            w.price                    AS purchase_price,
            (w.quantity * w.price)     AS purchase_amount,
            w.dividend                 AS dividend,
            lp.date                    AS current_date,
            lp.price                   AS current_price,
            (w.quantity * lp.price)    AS current_amount,
            lp.per                     AS per,
            lp.rsi                     AS rsi,
            CASE WHEN w.quantity * w.price > 0
                 THEN 100.0 * w.dividend / (w.quantity * w.price)
            END                        AS perf_div,
            (w.quantity * lp.price + w.dividend - w.quantity * w.price)
                                       AS plus_minus_value,
            CASE WHEN w.quantity * w.price > 0
                 THEN 100.0 * (w.quantity * lp.price + w.dividend
                               - w.quantity * w.price)
                            / (w.quantity * w.price)
            END                        AS perf
        FROM wallet w
        LEFT JOIN stocks s       ON s.id = w.id
        LEFT JOIN latest_price lp ON lp.id = w.id
        WHERE w.proprietaire = ? COLLATE NOCASE
        ORDER BY name COLLATE NOCASE
    """
    try:
        with get_db() as conn:
            rows = [dict(r) for r in conn.execute(query, (proprietaire,))]
    except FileNotFoundError as exc:
        return jsonify({"error": str(exc)}), 500
    except sqlite3.Error as exc:
        return jsonify({"error": f"Erreur SQLite: {exc}"}), 500

    return jsonify(rows)


@app.route("/api/stocks")
def api_stocks():
    """Liste toutes les actions du référentiel. Sert à peupler le
    `<select>` de la modale de création d'un portefeuille.
    """
    try:
        with get_db() as conn:
            rows = [
                dict(r)
                for r in conn.execute(
                    """
                    SELECT s.id, COALESCE(s.name, s.id) AS name
                    FROM stocks s
                    ORDER BY name COLLATE NOCASE
                    """
                )
            ]
    except FileNotFoundError as exc:
        return jsonify({"error": str(exc)}), 500
    except sqlite3.Error as exc:
        return jsonify({"error": f"Erreur SQLite: {exc}"}), 500
    return jsonify(rows)


@app.route("/api/stocks/available")
def api_stocks_available():
    """Liste les actions de `stocks` qui ne sont pas encore dans
    le portefeuille du propriétaire demandé. Sert à peupler le
    `<select>` de la modale d'ajout.
    """
    try:
        proprietaire = proprietaire_from_request()
    except ProprietaireError as exc:
        return jsonify({"error": str(exc)}), 400
    try:
        with get_db() as conn:
            rows = [
                dict(r)
                for r in conn.execute(
                    """
                    SELECT s.id, COALESCE(s.name, s.id) AS name
                    FROM stocks s
                    WHERE NOT EXISTS (
                        SELECT 1 FROM wallet w
                        WHERE w.id = s.id
                          AND w.proprietaire = ? COLLATE NOCASE
                    )
                    ORDER BY name COLLATE NOCASE
                    """,
                    (proprietaire,),
                )
            ]
    except FileNotFoundError as exc:
        return jsonify({"error": str(exc)}), 500
    except sqlite3.Error as exc:
        return jsonify({"error": f"Erreur SQLite: {exc}"}), 500
    return jsonify(rows)


@app.route("/api/wallet", methods=["POST"])
def api_wallet_create():
    """Ajoute une ligne dans `wallet`. Refuse si le portefeuille
    n'existe pas (404), si l'id n'existe pas dans `stocks` (404)
    ou si une ligne pour cet id est déjà présente chez ce
    propriétaire (409).
    """
    payload = request.get_json(silent=True) or {}
    try:
        proprietaire = proprietaire_from_request(payload)
        stock_id, quantity, price, dividend, date_str = (
            parse_position_payload(payload)
        )
    except ProprietaireError as exc:
        return jsonify({"error": str(exc)}), 400
    except ValueError as exc:
        return jsonify({"error": str(exc)}), 400

    try:
        with get_db_rw() as conn:
            if not owner_exists(conn, proprietaire):
                return jsonify({"error": "Portefeuille introuvable"}), 404
            if not conn.execute(
                "SELECT 1 FROM stocks WHERE id = ?", (stock_id,)
            ).fetchone():
                return jsonify({"error": "Action inconnue"}), 404
            if conn.execute(
                "SELECT 1 FROM wallet "
                "WHERE id = ? AND proprietaire = ? COLLATE NOCASE",
                (stock_id, proprietaire),
            ).fetchone():
                return (
                    jsonify({"error": "Action déjà dans le portefeuille"}),
                    409,
                )
            conn.execute(
                "INSERT INTO wallet "
                "(id, quantity, date, price, dividend, proprietaire) "
                "VALUES (?, ?, ?, ?, ?, ?)",
                (
                    stock_id,
                    quantity,
                    iso_to_fr_date(date_str),
                    price,
                    dividend,
                    proprietaire,
                ),
            )
            conn.commit()
    except FileNotFoundError as exc:
        return jsonify({"error": str(exc)}), 500
    except sqlite3.IntegrityError:
        return (
            jsonify({"error": "Action déjà dans le portefeuille"}),
            409,
        )
    except sqlite3.Error as exc:
        return jsonify({"error": f"Erreur SQLite: {exc}"}), 500
    return jsonify({"created": stock_id}), 201


@app.route("/api/wallet/<stock_id>", methods=["DELETE"])
def api_wallet_delete(stock_id: str):
    """Supprime une ligne de la table `wallet` identifiée par son
    `id` (mnémo) et son propriétaire. Renvoie 404 si aucune ligne
    ne correspond.
    """
    try:
        proprietaire = proprietaire_from_request()
    except ProprietaireError as exc:
        return jsonify({"error": str(exc)}), 400
    try:
        with get_db_rw() as conn:
            cur = conn.execute(
                "DELETE FROM wallet "
                "WHERE id = ? AND proprietaire = ? COLLATE NOCASE",
                (stock_id, proprietaire),
            )
            if cur.rowcount == 0:
                return jsonify({"error": "Action introuvable"}), 404
            conn.commit()
    except FileNotFoundError as exc:
        return jsonify({"error": str(exc)}), 500
    except sqlite3.Error as exc:
        return jsonify({"error": f"Erreur SQLite: {exc}"}), 500
    return jsonify({"deleted": stock_id})


@app.route("/api/wallet/<stock_id>", methods=["PUT"])
def api_wallet_update(stock_id: str):
    """Met à jour quantity / date / price / dividend pour la ligne
    `wallet` d'id et de propriétaire donnés. Tous les champs sont
    requis.
    """
    payload = request.get_json(silent=True) or {}
    try:
        proprietaire = proprietaire_from_request(payload)
        quantity = int(payload["quantity"])
        price = float(payload["price"])
        dividend = float(payload["dividend"])
        date_str = str(payload["date"])
    except ProprietaireError as exc:
        return jsonify({"error": str(exc)}), 400
    except (KeyError, TypeError, ValueError):
        return jsonify({"error": "Champs invalides"}), 400

    if quantity < 0 or price < 0 or dividend < 0:
        return jsonify({"error": "Valeurs négatives interdites"}), 400
    if not math.isfinite(price) or not math.isfinite(dividend):
        return jsonify({"error": "Valeurs non finies"}), 400
    if not re.fullmatch(r"\d{4}-\d{2}-\d{2}", date_str):
        return jsonify({"error": "Date invalide (YYYY-MM-DD)"}), 400

    try:
        with get_db_rw() as conn:
            cur = conn.execute(
                "UPDATE wallet SET quantity = ?, date = ?, "
                "price = ?, dividend = ? "
                "WHERE id = ? AND proprietaire = ? COLLATE NOCASE",
                (
                    quantity,
                    iso_to_fr_date(date_str),
                    price,
                    dividend,
                    stock_id,
                    proprietaire,
                ),
            )
            if cur.rowcount == 0:
                return jsonify({"error": "Action introuvable"}), 404
            conn.commit()
    except FileNotFoundError as exc:
        return jsonify({"error": str(exc)}), 500
    except sqlite3.Error as exc:
        return jsonify({"error": f"Erreur SQLite: {exc}"}), 500
    return jsonify({"updated": stock_id})


@app.route("/api/portefeuilles-etf")
def api_portefeuilles_etf():
    """Liste les portefeuilles ETF (un par propriétaire) avec une
    synthèse de valorisation.
    """
    query = """
        WITH latest_price AS (
            SELECT p.id, p.date, p.price
            FROM pricingETF p
            JOIN (
                SELECT id, MAX(date) AS max_date
                FROM pricingETF
                GROUP BY id
            ) m ON m.id = p.id AND m.max_date = p.date
        ),
        owners AS (
            SELECT proprietaire FROM walletETF
            WHERE proprietaire IS NOT NULL AND TRIM(proprietaire) != ''
            UNION
            SELECT proprietaire FROM walletETFDetails
            WHERE proprietaire IS NOT NULL AND TRIM(proprietaire) != ''
        ),
        agg AS (
            SELECT
                w.proprietaire,
                COUNT(*) AS nb_lignes,
                SUM(w.quantity * w.price) AS purchase_amount,
                SUM(COALESCE(w.quantity * lp.price, 0)) AS current_amount,
                MAX(lp.date) AS current_date
            FROM walletETF w
            LEFT JOIN latest_price lp ON lp.id = w.id
            GROUP BY w.proprietaire
        )
        SELECT
            o.proprietaire AS proprietaire,
            COALESCE(a.nb_lignes, 0) AS nb_lignes,
            COALESCE(a.purchase_amount, 0) AS purchase_amount,
            COALESCE(a.current_amount, 0) AS current_amount,
            a.current_date AS current_date,
            d.liquidite AS liquidite,
            COALESCE(a.current_amount, 0)
                - COALESCE(a.purchase_amount, 0) AS plus_minus_value,
            CASE WHEN COALESCE(a.purchase_amount, 0) > 0
                 THEN 100.0 * (COALESCE(a.current_amount, 0)
                               - COALESCE(a.purchase_amount, 0))
                              / a.purchase_amount
            END AS perf
        FROM owners o
        LEFT JOIN agg a
            ON a.proprietaire = o.proprietaire COLLATE NOCASE
        LEFT JOIN walletETFDetails d
            ON d.proprietaire = o.proprietaire COLLATE NOCASE
        ORDER BY o.proprietaire COLLATE NOCASE
    """
    try:
        with get_db() as conn:
            rows = [dict(r) for r in conn.execute(query)]
    except FileNotFoundError as exc:
        return jsonify({"error": str(exc)}), 500
    except sqlite3.Error as exc:
        return jsonify({"error": f"Erreur SQLite: {exc}"}), 500
    return jsonify(rows)


@app.route("/api/portefeuilles-etf", methods=["POST"])
def api_portefeuilles_etf_create():
    """Crée un portefeuille ETF : une ligne `walletETFDetails`
    (liquidité à 0) et une première position dans `walletETF`.
    """
    payload = request.get_json(silent=True) or {}
    try:
        proprietaire = normalize_proprietaire(payload.get("proprietaire"))
        etf_id, quantity, price, date_str = parse_etf_position_payload(
            payload
        )
    except ProprietaireError as exc:
        return jsonify({"error": str(exc)}), 400
    except ValueError as exc:
        return jsonify({"error": str(exc)}), 400

    try:
        with get_db_rw() as conn:
            if etf_owner_exists(conn, proprietaire):
                return (
                    jsonify({
                        "error": "Un portefeuille ETF existe déjà "
                        "pour ce propriétaire",
                    }),
                    409,
                )
            if not conn.execute(
                "SELECT 1 FROM etf WHERE id = ?", (etf_id,)
            ).fetchone():
                return jsonify({"error": "ETF inconnu"}), 404
            conn.execute(
                "INSERT INTO walletETFDetails (liquidite, proprietaire) "
                "VALUES (?, ?)",
                (0, proprietaire),
            )
            conn.execute(
                "INSERT INTO walletETF "
                "(id, quantity, date, price, proprietaire) "
                "VALUES (?, ?, ?, ?, ?)",
                (
                    etf_id,
                    quantity,
                    iso_to_fr_date(date_str),
                    price,
                    proprietaire,
                ),
            )
            conn.commit()
    except FileNotFoundError as exc:
        return jsonify({"error": str(exc)}), 500
    except sqlite3.IntegrityError:
        return (
            jsonify({
                "error": "Un portefeuille ETF existe déjà "
                "pour ce propriétaire",
            }),
            409,
        )
    except sqlite3.Error as exc:
        return jsonify({"error": f"Erreur SQLite: {exc}"}), 500
    return jsonify({"created": proprietaire}), 201


@app.route("/api/wallet-etf")
def api_wallet_etf():
    try:
        proprietaire = proprietaire_from_request()
    except ProprietaireError as exc:
        return jsonify({"error": str(exc)}), 400

    query = """
        WITH latest_price AS (
            SELECT p.id, p.date, p.price, p.rsi
            FROM pricingETF p
            JOIN (
                SELECT id, MAX(date) AS max_date
                FROM pricingETF
                GROUP BY id
            ) m ON m.id = p.id AND m.max_date = p.date
        )
        SELECT
            COALESCE(e.name, w.id)     AS name,
            w.id                       AS id,
            w.quantity                 AS quantity,
            w.date                     AS purchase_date,
            w.price                    AS purchase_price,
            (w.quantity * w.price)     AS purchase_amount,
            lp.date                    AS current_date,
            lp.price                   AS current_price,
            (w.quantity * lp.price)    AS current_amount,
            lp.rsi                     AS rsi,
            (w.quantity * lp.price - w.quantity * w.price)
                                       AS plus_minus_value,
            CASE WHEN w.quantity * w.price > 0
                 THEN 100.0 * (w.quantity * lp.price
                               - w.quantity * w.price)
                            / (w.quantity * w.price)
            END                        AS perf
        FROM walletETF w
        LEFT JOIN etf e            ON e.id = w.id
        LEFT JOIN latest_price lp  ON lp.id = w.id
        WHERE w.proprietaire = ? COLLATE NOCASE
        ORDER BY name COLLATE NOCASE
    """
    try:
        with get_db() as conn:
            rows = [dict(r) for r in conn.execute(query, (proprietaire,))]
    except FileNotFoundError as exc:
        return jsonify({"error": str(exc)}), 500
    except sqlite3.Error as exc:
        return jsonify({"error": f"Erreur SQLite: {exc}"}), 500

    return jsonify(rows)


@app.route("/api/etfs")
def api_etfs():
    """Liste tous les ETF du référentiel. Sert à peupler le
    `<select>` de la modale de création d'un portefeuille ETF.
    """
    try:
        with get_db() as conn:
            rows = [
                dict(r)
                for r in conn.execute(
                    """
                    SELECT e.id, COALESCE(e.name, e.id) AS name
                    FROM etf e
                    ORDER BY name COLLATE NOCASE
                    """
                )
            ]
    except FileNotFoundError as exc:
        return jsonify({"error": str(exc)}), 500
    except sqlite3.Error as exc:
        return jsonify({"error": f"Erreur SQLite: {exc}"}), 500
    return jsonify(rows)


@app.route("/api/etfs/available")
def api_etfs_available():
    """Liste les ETF de `etf` qui ne sont pas encore dans le
    portefeuille ETF du propriétaire demandé.
    """
    try:
        proprietaire = proprietaire_from_request()
    except ProprietaireError as exc:
        return jsonify({"error": str(exc)}), 400
    try:
        with get_db() as conn:
            rows = [
                dict(r)
                for r in conn.execute(
                    """
                    SELECT e.id, COALESCE(e.name, e.id) AS name
                    FROM etf e
                    WHERE NOT EXISTS (
                        SELECT 1 FROM walletETF w
                        WHERE w.id = e.id
                          AND w.proprietaire = ? COLLATE NOCASE
                    )
                    ORDER BY name COLLATE NOCASE
                    """,
                    (proprietaire,),
                )
            ]
    except FileNotFoundError as exc:
        return jsonify({"error": str(exc)}), 500
    except sqlite3.Error as exc:
        return jsonify({"error": f"Erreur SQLite: {exc}"}), 500
    return jsonify(rows)


@app.route("/api/wallet-etf", methods=["POST"])
def api_wallet_etf_create():
    """Ajoute une ligne dans `walletETF`. Refuse si le portefeuille
    n'existe pas (404), si l'id n'existe pas dans `etf` (404) ou si
    une ligne pour cet id est déjà présente chez ce propriétaire
    (409).
    """
    payload = request.get_json(silent=True) or {}
    try:
        proprietaire = proprietaire_from_request(payload)
        etf_id, quantity, price, date_str = parse_etf_position_payload(
            payload
        )
    except ProprietaireError as exc:
        return jsonify({"error": str(exc)}), 400
    except ValueError as exc:
        return jsonify({"error": str(exc)}), 400

    try:
        with get_db_rw() as conn:
            if not etf_owner_exists(conn, proprietaire):
                return jsonify({"error": "Portefeuille introuvable"}), 404
            if not conn.execute(
                "SELECT 1 FROM etf WHERE id = ?", (etf_id,)
            ).fetchone():
                return jsonify({"error": "ETF inconnu"}), 404
            if conn.execute(
                "SELECT 1 FROM walletETF "
                "WHERE id = ? AND proprietaire = ? COLLATE NOCASE",
                (etf_id, proprietaire),
            ).fetchone():
                return (
                    jsonify({"error": "ETF déjà dans le portefeuille"}),
                    409,
                )
            conn.execute(
                "INSERT INTO walletETF "
                "(id, quantity, date, price, proprietaire) "
                "VALUES (?, ?, ?, ?, ?)",
                (
                    etf_id,
                    quantity,
                    iso_to_fr_date(date_str),
                    price,
                    proprietaire,
                ),
            )
            conn.commit()
    except FileNotFoundError as exc:
        return jsonify({"error": str(exc)}), 500
    except sqlite3.IntegrityError:
        return (
            jsonify({"error": "ETF déjà dans le portefeuille"}),
            409,
        )
    except sqlite3.Error as exc:
        return jsonify({"error": f"Erreur SQLite: {exc}"}), 500
    return jsonify({"created": etf_id}), 201


@app.route("/api/wallet-etf/<etf_id>", methods=["DELETE"])
def api_wallet_etf_delete(etf_id: str):
    """Supprime une ligne de `walletETF` identifiée par son id et
    son propriétaire. Renvoie 404 si aucune ligne ne correspond.
    """
    try:
        proprietaire = proprietaire_from_request()
    except ProprietaireError as exc:
        return jsonify({"error": str(exc)}), 400
    try:
        with get_db_rw() as conn:
            cur = conn.execute(
                "DELETE FROM walletETF "
                "WHERE id = ? AND proprietaire = ? COLLATE NOCASE",
                (etf_id, proprietaire),
            )
            if cur.rowcount == 0:
                return jsonify({"error": "ETF introuvable"}), 404
            conn.commit()
    except FileNotFoundError as exc:
        return jsonify({"error": str(exc)}), 500
    except sqlite3.Error as exc:
        return jsonify({"error": f"Erreur SQLite: {exc}"}), 500
    return jsonify({"deleted": etf_id})


@app.route("/api/wallet-etf/<etf_id>", methods=["PUT"])
def api_wallet_etf_update(etf_id: str):
    """Met à jour quantity / date / price pour la ligne `walletETF`
    d'id et de propriétaire donnés. Tous les champs sont requis.
    """
    payload = request.get_json(silent=True) or {}
    try:
        proprietaire = proprietaire_from_request(payload)
        quantity = float(payload["quantity"])
        price = float(payload["price"])
        date_str = str(payload["date"])
    except ProprietaireError as exc:
        return jsonify({"error": str(exc)}), 400
    except (KeyError, TypeError, ValueError):
        return jsonify({"error": "Champs invalides"}), 400

    if quantity < 0 or price < 0:
        return jsonify({"error": "Valeurs négatives interdites"}), 400
    if not math.isfinite(quantity) or not math.isfinite(price):
        return jsonify({"error": "Valeurs non finies"}), 400
    if not re.fullmatch(r"\d{4}-\d{2}-\d{2}", date_str):
        return jsonify({"error": "Date invalide (YYYY-MM-DD)"}), 400

    try:
        with get_db_rw() as conn:
            cur = conn.execute(
                "UPDATE walletETF SET quantity = ?, date = ?, "
                "price = ? "
                "WHERE id = ? AND proprietaire = ? COLLATE NOCASE",
                (
                    quantity,
                    iso_to_fr_date(date_str),
                    price,
                    etf_id,
                    proprietaire,
                ),
            )
            if cur.rowcount == 0:
                return jsonify({"error": "ETF introuvable"}), 404
            conn.commit()
    except FileNotFoundError as exc:
        return jsonify({"error": str(exc)}), 500
    except sqlite3.Error as exc:
        return jsonify({"error": f"Erreur SQLite: {exc}"}), 500
    return jsonify({"updated": etf_id})


@app.route("/api/per")
def api_per():
    query = """
        WITH latest_per AS (
            SELECT p.id, p.date, p.per
            FROM pricing p
            JOIN (
                SELECT id, MAX(date) AS max_date
                FROM pricing
                WHERE per IS NOT NULL
                GROUP BY id
            ) m ON m.id = p.id AND m.max_date = p.date
        ),
        overall AS (
            SELECT MAX(date) AS max_date FROM latest_per
        )
        SELECT
            COALESCE(s.name, lp.id)   AS name,
            lp.id                     AS id,
            lp.date                   AS date,
            lp.per                    AS per
        FROM latest_per lp
        CROSS JOIN overall o
        LEFT JOIN stocks s ON s.id = lp.id
        WHERE lp.per > 0
          AND lp.per < 10
          AND lp.date >= date(o.max_date, '-7 days')
        ORDER BY lp.per ASC
    """
    try:
        with get_db() as conn:
            rows = [dict(r) for r in conn.execute(query)]
    except FileNotFoundError as exc:
        return jsonify({"error": str(exc)}), 500
    except sqlite3.Error as exc:
        return jsonify({"error": f"Erreur SQLite: {exc}"}), 500

    return jsonify(rows)


@app.route("/api/rsi")
def api_rsi():
    query = """
        WITH latest_pricing AS (
            SELECT p.id, p.date, p.per, p.rsi
            FROM pricing p
            JOIN (
                SELECT id, MAX(date) AS max_date
                FROM pricing
                WHERE rsi IS NOT NULL
                GROUP BY id
            ) m ON m.id = p.id AND m.max_date = p.date
        ),
        overall AS (
            SELECT MAX(date) AS max_date FROM latest_pricing
        )
        SELECT
            COALESCE(s.name, lp.id)   AS name,
            lp.id                     AS id,
            lp.date                   AS date,
            lp.per                    AS per,
            lp.rsi                    AS rsi
        FROM latest_pricing lp
        CROSS JOIN overall o
        LEFT JOIN stocks s ON s.id = lp.id
        WHERE lp.rsi < 30
          AND lp.per IS NOT NULL
          AND lp.date >= date(o.max_date, '-7 days')
        ORDER BY lp.rsi ASC
    """
    try:
        with get_db() as conn:
            rows = [dict(r) for r in conn.execute(query)]
    except FileNotFoundError as exc:
        return jsonify({"error": str(exc)}), 500
    except sqlite3.Error as exc:
        return jsonify({"error": f"Erreur SQLite: {exc}"}), 500

    return jsonify(rows)


@app.route("/api/rsi-etf")
def api_rsi_etf():
    """ETF dont le dernier RSI connu est < 30, dans la même fenêtre
    de fraîcheur de 7 jours que `/api/rsi`. Filtres optionnels
    `category` et `pea` (1 / 0), comme `/api/etf/list`.
    """
    category = (request.args.get("category") or "").strip()
    pea = parse_pea_filter(request.args.get("pea"))
    query = """
        WITH latest_pricing AS (
            SELECT p.id, p.date, p.rsi
            FROM pricingETF p
            JOIN (
                SELECT id, MAX(date) AS max_date
                FROM pricingETF
                WHERE rsi IS NOT NULL
                GROUP BY id
            ) m ON m.id = p.id AND m.max_date = p.date
        ),
        overall AS (
            SELECT MAX(date) AS max_date FROM latest_pricing
        )
        SELECT
            COALESCE(e.name, lp.id)   AS name,
            lp.id                     AS id,
            lp.date                   AS date,
            e.ter                     AS ter,
            e.category                AS category,
            e.pea                     AS pea,
            lp.rsi                    AS rsi
        FROM latest_pricing lp
        CROSS JOIN overall o
        LEFT JOIN etf e ON e.id = lp.id
        WHERE lp.rsi < 30
          AND lp.date >= date(o.max_date, '-7 days')
    """
    params = []
    if category:
        query += " AND e.category = ?"
        params.append(category)
    if pea is not None:
        query += " AND e.pea = ?"
        params.append(pea)
    query += " ORDER BY lp.rsi ASC"
    try:
        with get_db() as conn:
            rows = [dict(r) for r in conn.execute(query, params)]
    except FileNotFoundError as exc:
        return jsonify({"error": str(exc)}), 500
    except sqlite3.Error as exc:
        return jsonify({"error": f"Erreur SQLite: {exc}"}), 500
    for row in rows:
        row["pea"] = json_pea(row.get("pea"))
    return jsonify(rows)


@app.route("/api/rendement")
def api_rendement():
    query = """
        WITH latest_price AS (
            SELECT p.id, p.date, p.price, p.per
            FROM pricing p
            JOIN (
                SELECT id, MAX(date) AS max_date
                FROM pricing
                GROUP BY id
            ) m ON m.id = p.id AND m.max_date = p.date
        ),
        current_div AS (
            SELECT id, dividend
            FROM dividends
            WHERE year = CAST(strftime('%Y', 'now') AS INTEGER)
        ),
        prev_div AS (
            SELECT id, dividend
            FROM dividends
            WHERE year = CAST(strftime('%Y', 'now') AS INTEGER) - 1
        ),
        avg5_div AS (
            SELECT d.id, SUM(d.dividend) / 5.0 AS dividend
            FROM dividends d
            JOIN latest_price lp ON lp.id = d.id
            WHERE d.year BETWEEN
                CAST(strftime('%Y', 'now') AS INTEGER) - 4
                AND CAST(strftime('%Y', 'now') AS INTEGER)
              AND lp.price > 0
              AND d.dividend <= 0.5 * lp.price
            GROUP BY d.id
        ),
        avg10_div AS (
            SELECT d.id, SUM(d.dividend) / 10.0 AS dividend
            FROM dividends d
            JOIN latest_price lp ON lp.id = d.id
            WHERE d.year BETWEEN
                CAST(strftime('%Y', 'now') AS INTEGER) - 9
                AND CAST(strftime('%Y', 'now') AS INTEGER)
              AND lp.price > 0
              AND d.dividend <= 0.5 * lp.price
            GROUP BY d.id
        )
        SELECT
            COALESCE(s.name, s.id)    AS name,
            s.id                      AS id,
            lp.per                    AS per,
            d.dividend                AS dividend,
            dp.dividend               AS dividend_prev,
            d5.dividend               AS dividend_avg5,
            d10.dividend              AS dividend_avg10,
            lp.price                  AS price,
            CASE WHEN lp.price > 0 AND d.dividend IS NOT NULL
                 THEN (d.dividend * 100.0) / lp.price
            END                       AS rendement,
            CASE WHEN lp.price > 0 AND dp.dividend IS NOT NULL
                 THEN (dp.dividend * 100.0) / lp.price
            END                       AS rendement_prev,
            CASE WHEN lp.price > 0 AND d5.dividend IS NOT NULL
                 THEN (d5.dividend * 100.0) / lp.price
            END                       AS rendement_avg5,
            CASE WHEN lp.price > 0 AND d10.dividend IS NOT NULL
                 THEN (d10.dividend * 100.0) / lp.price
            END                       AS rendement_avg10
        FROM stocks s
        LEFT JOIN current_div d   ON d.id = s.id
        LEFT JOIN prev_div dp     ON dp.id = s.id
        LEFT JOIN avg5_div d5     ON d5.id = s.id
        LEFT JOIN avg10_div d10   ON d10.id = s.id
        LEFT JOIN latest_price lp ON lp.id = s.id
        WHERE lp.per IS NOT NULL
        ORDER BY rendement DESC
    """
    try:
        with get_db() as conn:
            rows = [dict(r) for r in conn.execute(query)]
    except FileNotFoundError as exc:
        return jsonify({"error": str(exc)}), 500
    except sqlite3.Error as exc:
        return jsonify({"error": f"Erreur SQLite: {exc}"}), 500

    return jsonify(rows)


@app.route("/api/securite")
def api_securite():
    # « Année n » = dernière année présente dans `results` (déterminée
    # par yfinance, pas par le calendrier). On pivote les 4 dernières
    # années par action puis on garde uniquement celles qui ont les 4
    # exercices renseignés ET tous > 0, et dont le PER courant est
    # dans ]0, 10[ (même fenêtre de fraîcheur que /api/per).
    query = """
        WITH latest_per AS (
            SELECT p.id, p.date, p.per
            FROM pricing p
            JOIN (
                SELECT id, MAX(date) AS max_date
                FROM pricing
                WHERE per IS NOT NULL
                GROUP BY id
            ) m ON m.id = p.id AND m.max_date = p.date
        ),
        overall AS (
            SELECT MAX(date) AS max_date FROM latest_per
        ),
        year_n AS (
            -- Même logique que securite_page() : on retient la
            -- dernière année avec une couverture suffisante
            -- (>= SECURITE_YEAR_MIN_COVERAGE tickers). La valeur
            -- 50 est dupliquée ici car SQLite ne permet pas
            -- d'injecter un paramètre Python dans une CTE simple
            -- sans complexifier la requête.
            SELECT MAX(year) AS n FROM (
                SELECT year
                FROM results
                WHERE year IS NOT NULL
                GROUP BY year
                HAVING COUNT(*) >= 50
            )
        ),
        results_pivot AS (
            SELECT
                r.id,
                MAX(CASE WHEN r.year = y.n - 3 THEN r.result END) AS r_n3,
                MAX(CASE WHEN r.year = y.n - 2 THEN r.result END) AS r_n2,
                MAX(CASE WHEN r.year = y.n - 1 THEN r.result END) AS r_n1,
                MAX(CASE WHEN r.year = y.n     THEN r.result END) AS r_n
            FROM results r
            CROSS JOIN year_n y
            WHERE r.year BETWEEN y.n - 3 AND y.n
            GROUP BY r.id
        )
        SELECT
            COALESCE(s.name, lp.id)   AS name,
            lp.id                     AS id,
            rp.r_n3                   AS result_n3,
            rp.r_n2                   AS result_n2,
            rp.r_n1                   AS result_n1,
            rp.r_n                    AS result_n,
            lp.per                    AS per,
            y.n                       AS year_n
        FROM latest_per lp
        CROSS JOIN overall o
        CROSS JOIN year_n y
        INNER JOIN results_pivot rp ON rp.id = lp.id
        LEFT JOIN stocks s ON s.id = lp.id
        WHERE lp.per > 0
          AND lp.per < 10
          AND lp.date >= date(o.max_date, '-7 days')
          AND rp.r_n3 IS NOT NULL AND rp.r_n3 > 0
          AND rp.r_n2 IS NOT NULL AND rp.r_n2 > 0
          AND rp.r_n1 IS NOT NULL AND rp.r_n1 > 0
          AND rp.r_n  IS NOT NULL AND rp.r_n  > 0
        ORDER BY lp.per ASC
    """
    try:
        with get_db() as conn:
            rows = [dict(r) for r in conn.execute(query)]
    except FileNotFoundError as exc:
        return jsonify({"error": str(exc)}), 500
    except sqlite3.Error as exc:
        return jsonify({"error": f"Erreur SQLite: {exc}"}), 500

    return jsonify(rows)


@app.route("/api/action/search")
def api_action_search():
    """Autocomplete pour la page Action : renvoie au plus 20 actions
    de la table `stocks` dont l'id (ISIN) ou le nom contient `q`.
    """
    q = (request.args.get("q") or "").strip()
    if not q:
        return jsonify([])
    like = f"%{q}%"
    try:
        with get_db() as conn:
            rows = [
                dict(r)
                for r in conn.execute(
                    """
                    SELECT id, COALESCE(name, id) AS name
                    FROM stocks
                    WHERE id LIKE ? OR name LIKE ?
                    ORDER BY name COLLATE NOCASE
                    LIMIT 20
                    """,
                    (like, like),
                )
            ]
    except FileNotFoundError as exc:
        return jsonify({"error": str(exc)}), 500
    except sqlite3.Error as exc:
        return jsonify({"error": f"Erreur SQLite: {exc}"}), 500
    return jsonify(rows)


@app.route("/api/action/<stock_id>")
def api_action_detail(stock_id: str):
    """Synthèse d'une action : PER, RSI, dividendes par année,
    résultats par année, et rendements (n, n-1, moyenne 5 ans,
    moyenne 10 ans). Les rendements et les moyennes de dividendes
    suivent la même logique que /api/rendement : filtrage des
    dividendes exceptionnels (> 50% du prix), division par n
    (et non par le nombre d'années réellement disponibles).
    """
    try:
        with get_db() as conn:
            stock = conn.execute(
                "SELECT id, COALESCE(name, id) AS name "
                "FROM stocks WHERE id = ?",
                (stock_id,),
            ).fetchone()
            if stock is None:
                return (
                    jsonify({"error": f"Action introuvable: {stock_id}"}),
                    404,
                )

            # Dernier PER / RSI / prix non-NULL (chacun indépendamment :
            # le calcul peut être absent un jour donné).
            latest_per = conn.execute(
                "SELECT date, per FROM pricing "
                "WHERE id = ? AND per IS NOT NULL "
                "ORDER BY date DESC LIMIT 1",
                (stock_id,),
            ).fetchone()
            latest_rsi = conn.execute(
                "SELECT date, rsi FROM pricing "
                "WHERE id = ? AND rsi IS NOT NULL "
                "ORDER BY date DESC LIMIT 1",
                (stock_id,),
            ).fetchone()
            latest_price = conn.execute(
                "SELECT date, price FROM pricing "
                "WHERE id = ? AND price IS NOT NULL "
                "ORDER BY date DESC LIMIT 1",
                (stock_id,),
            ).fetchone()

            dividends = [
                dict(r)
                for r in conn.execute(
                    "SELECT year, dividend FROM dividends "
                    "WHERE id = ? AND year IS NOT NULL "
                    "ORDER BY year DESC",
                    (stock_id,),
                )
            ]
            results = [
                dict(r)
                for r in conn.execute(
                    "SELECT year, result FROM results "
                    "WHERE id = ? AND year IS NOT NULL "
                    "ORDER BY year DESC",
                    (stock_id,),
                )
            ]
    except FileNotFoundError as exc:
        return jsonify({"error": str(exc)}), 500
    except sqlite3.Error as exc:
        return jsonify({"error": f"Erreur SQLite: {exc}"}), 500

    current_year = date.today().year
    price = latest_price["price"] if latest_price else None

    div_by_year = {
        d["year"]: d["dividend"]
        for d in dividends
        if d["year"] is not None and d["dividend"] is not None
    }

    def avg_dividend(span: int) -> float | None:
        # Somme des dividendes des `span` dernières années (en
        # excluant les valeurs exceptionnelles > 50% du prix
        # courant, comme dans /api/rendement), divisée par `span`.
        if not price or price <= 0:
            return None
        total = 0.0
        any_value = False
        for year in range(current_year - span + 1, current_year + 1):
            div = div_by_year.get(year)
            if div is None:
                continue
            if div > 0.5 * price:
                continue
            total += div
            any_value = True
        if not any_value:
            return None
        return total / span

    def rendement(div: float | None) -> float | None:
        if price and price > 0 and div is not None:
            return div * 100.0 / price
        return None

    div_n = div_by_year.get(current_year)
    div_n1 = div_by_year.get(current_year - 1)
    div_avg5 = avg_dividend(5)
    div_avg10 = avg_dividend(10)

    return jsonify({
        "id": stock["id"],
        "name": stock["name"],
        "per": latest_per["per"] if latest_per else None,
        "per_date": latest_per["date"] if latest_per else None,
        "rsi": latest_rsi["rsi"] if latest_rsi else None,
        "rsi_date": latest_rsi["date"] if latest_rsi else None,
        "price": price,
        "price_date": latest_price["date"] if latest_price else None,
        "year_n": current_year,
        "year_n1": current_year - 1,
        "dividend_n": div_n,
        "dividend_n1": div_n1,
        "dividend_avg5": div_avg5,
        "dividend_avg10": div_avg10,
        "rendement_n": rendement(div_n),
        "rendement_n1": rendement(div_n1),
        "rendement_avg5": rendement(div_avg5),
        "rendement_avg10": rendement(div_avg10),
        "dividends": dividends,
        "results": results,
    })


@app.route("/api/etf/search")
def api_etf_search():
    """Autocomplete pour la page ETF : au plus 20 ETF de la table
    `etf` dont l'id (ticker) ou le nom contient `q`. Les filtres
    optionnels `category` et `pea` (1 / 0) restreignent le résultat.
    """
    q = (request.args.get("q") or "").strip()
    if not q:
        return jsonify([])
    like = f"%{q}%"
    category = (request.args.get("category") or "").strip()
    pea = parse_pea_filter(request.args.get("pea"))
    sql = """
        SELECT id, COALESCE(name, id) AS name
        FROM etf
        WHERE (id LIKE ? OR name LIKE ?)
    """
    params = [like, like]
    if category:
        sql += " AND category = ?"
        params.append(category)
    if pea is not None:
        sql += " AND pea = ?"
        params.append(pea)
    sql += " ORDER BY name COLLATE NOCASE LIMIT 20"
    try:
        with get_db() as conn:
            rows = [dict(r) for r in conn.execute(sql, params)]
    except FileNotFoundError as exc:
        return jsonify({"error": str(exc)}), 500
    except sqlite3.Error as exc:
        return jsonify({"error": f"Erreur SQLite: {exc}"}), 500
    return jsonify(rows)


@app.route("/api/etf/categories")
def api_etf_categories():
    """Liste distincte des catégories justETF renseignées."""
    try:
        with get_db() as conn:
            rows = [
                r["category"]
                for r in conn.execute(
                    """
                    SELECT DISTINCT category
                    FROM etf
                    WHERE category IS NOT NULL
                      AND TRIM(category) != ''
                    ORDER BY category COLLATE NOCASE
                    """
                )
            ]
    except FileNotFoundError as exc:
        return jsonify({"error": str(exc)}), 500
    except sqlite3.Error as exc:
        return jsonify({"error": f"Erreur SQLite: {exc}"}), 500
    return jsonify(rows)


@app.route("/api/etf/list")
def api_etf_list():
    """ETF avec dernier cours `pricingETF`. Filtres optionnels
    `category` (classe d'actifs) et `pea` (1 = éligible, 0 = non).
    Sans filtre : tout le référentiel.
    """
    category = (request.args.get("category") or "").strip()
    pea = parse_pea_filter(request.args.get("pea"))
    query = """
        WITH latest_price AS (
            SELECT p.id, p.date, p.price, p.rsi
            FROM pricingETF p
            JOIN (
                SELECT id, MAX(date) AS max_date
                FROM pricingETF
                GROUP BY id
            ) m ON m.id = p.id AND m.max_date = p.date
        )
        SELECT
            e.id                       AS id,
            COALESCE(e.name, e.id)     AS name,
            e.ter                      AS ter,
            e.category                 AS category,
            e.pea                      AS pea,
            lp.date                    AS price_date,
            lp.price                   AS price,
            lp.rsi                     AS rsi
        FROM etf e
        LEFT JOIN latest_price lp ON lp.id = e.id
    """
    filters = []
    params = []
    if category:
        filters.append("e.category = ?")
        params.append(category)
    if pea is not None:
        filters.append("e.pea = ?")
        params.append(pea)
    if filters:
        query += " WHERE " + " AND ".join(filters)
    query += " ORDER BY name COLLATE NOCASE"
    try:
        with get_db() as conn:
            rows = [dict(r) for r in conn.execute(query, params)]
    except FileNotFoundError as exc:
        return jsonify({"error": str(exc)}), 500
    except sqlite3.Error as exc:
        return jsonify({"error": f"Erreur SQLite: {exc}"}), 500
    for row in rows:
        row["pea"] = json_pea(row.get("pea"))
    return jsonify(rows)


@app.route("/api/etf/<etf_id>")
def api_etf_detail(etf_id: str):
    """Fiche d'un ETF : nom, ticker, catégorie, TER, éligibilité PEA,
    dernier cours et RSI (table `pricingETF`).
    """
    try:
        with get_db() as conn:
            etf = conn.execute(
                "SELECT id, COALESCE(name, id) AS name, ter, category, pea "
                "FROM etf WHERE id = ?",
                (etf_id,),
            ).fetchone()
            if etf is None:
                return (
                    jsonify({"error": f"ETF introuvable: {etf_id}"}),
                    404,
                )
            latest = conn.execute(
                "SELECT date, price FROM pricingETF "
                "WHERE id = ? AND price IS NOT NULL "
                "ORDER BY date DESC LIMIT 1",
                (etf_id,),
            ).fetchone()
            latest_rsi = conn.execute(
                "SELECT date, rsi FROM pricingETF "
                "WHERE id = ? AND rsi IS NOT NULL "
                "ORDER BY date DESC LIMIT 1",
                (etf_id,),
            ).fetchone()
    except FileNotFoundError as exc:
        return jsonify({"error": str(exc)}), 500
    except sqlite3.Error as exc:
        return jsonify({"error": f"Erreur SQLite: {exc}"}), 500

    return jsonify({
        "id": etf["id"],
        "name": etf["name"],
        "ter": etf["ter"],
        "category": etf["category"],
        "pea": json_pea(etf["pea"]),
        "price": latest["price"] if latest else None,
        "price_date": latest["date"] if latest else None,
        "rsi": latest_rsi["rsi"] if latest_rsi else None,
        "rsi_date": latest_rsi["date"] if latest_rsi else None,
    })


@app.route("/api/liquidite", methods=["GET"])
def api_liquidite():
    try:
        proprietaire = proprietaire_from_request()
    except ProprietaireError as exc:
        return jsonify({"error": str(exc)}), 400
    try:
        with get_db() as conn:
            row = conn.execute(
                "SELECT liquidite FROM walletDetails "
                "WHERE proprietaire = ? COLLATE NOCASE",
                (proprietaire,),
            ).fetchone()
    except FileNotFoundError as exc:
        return jsonify({"error": str(exc)}), 500
    except sqlite3.Error as exc:
        return jsonify({"error": f"Erreur SQLite: {exc}"}), 500

    return jsonify({"liquidite": row["liquidite"] if row else None})


@app.route("/api/liquidite", methods=["POST"])
def api_liquidite_update():
    """Met à jour (ou insère) la liquidité du portefeuille du
    propriétaire donné dans `walletDetails`.
    """
    payload = request.get_json(silent=True) or {}
    try:
        proprietaire = proprietaire_from_request(payload)
    except ProprietaireError as exc:
        return jsonify({"error": str(exc)}), 400
    raw = payload.get("liquidite")
    try:
        value = float(raw)
    except (TypeError, ValueError):
        return jsonify({"error": "Champ 'liquidite' invalide"}), 400
    if not math.isfinite(value) or value < 0:
        return jsonify({"error": "Champ 'liquidite' invalide"}), 400

    try:
        with get_db_rw() as conn:
            if not owner_exists(conn, proprietaire):
                return jsonify({"error": "Portefeuille introuvable"}), 404
            cur = conn.execute(
                "UPDATE walletDetails SET liquidite = ? "
                "WHERE proprietaire = ? COLLATE NOCASE",
                (value, proprietaire),
            )
            if cur.rowcount == 0:
                conn.execute(
                    "INSERT INTO walletDetails (liquidite, proprietaire) "
                    "VALUES (?, ?)",
                    (value, proprietaire),
                )
            conn.commit()
    except FileNotFoundError as exc:
        return jsonify({"error": str(exc)}), 500
    except sqlite3.Error as exc:
        return jsonify({"error": f"Erreur SQLite: {exc}"}), 500

    return jsonify({"liquidite": value})


@app.route("/api/liquidite-etf", methods=["GET"])
def api_liquidite_etf():
    try:
        proprietaire = proprietaire_from_request()
    except ProprietaireError as exc:
        return jsonify({"error": str(exc)}), 400
    try:
        with get_db() as conn:
            row = conn.execute(
                "SELECT liquidite FROM walletETFDetails "
                "WHERE proprietaire = ? COLLATE NOCASE",
                (proprietaire,),
            ).fetchone()
    except FileNotFoundError as exc:
        return jsonify({"error": str(exc)}), 500
    except sqlite3.Error as exc:
        return jsonify({"error": f"Erreur SQLite: {exc}"}), 500

    return jsonify({"liquidite": row["liquidite"] if row else None})


@app.route("/api/liquidite-etf", methods=["POST"])
def api_liquidite_etf_update():
    """Met à jour (ou insère) la liquidité du portefeuille ETF du
    propriétaire donné dans `walletETFDetails`.
    """
    payload = request.get_json(silent=True) or {}
    try:
        proprietaire = proprietaire_from_request(payload)
    except ProprietaireError as exc:
        return jsonify({"error": str(exc)}), 400
    raw = payload.get("liquidite")
    try:
        value = float(raw)
    except (TypeError, ValueError):
        return jsonify({"error": "Champ 'liquidite' invalide"}), 400
    if not math.isfinite(value) or value < 0:
        return jsonify({"error": "Champ 'liquidite' invalide"}), 400

    try:
        with get_db_rw() as conn:
            if not etf_owner_exists(conn, proprietaire):
                return jsonify({"error": "Portefeuille introuvable"}), 404
            cur = conn.execute(
                "UPDATE walletETFDetails SET liquidite = ? "
                "WHERE proprietaire = ? COLLATE NOCASE",
                (value, proprietaire),
            )
            if cur.rowcount == 0:
                conn.execute(
                    "INSERT INTO walletETFDetails "
                    "(liquidite, proprietaire) VALUES (?, ?)",
                    (value, proprietaire),
                )
            conn.commit()
    except FileNotFoundError as exc:
        return jsonify({"error": str(exc)}), 500
    except sqlite3.Error as exc:
        return jsonify({"error": f"Erreur SQLite: {exc}"}), 500

    return jsonify({"liquidite": value})


def _last_log_line(path: str | None) -> str | None:
    """Retourne la dernière ligne non vide du fichier de log, ou None
    si pas de log lisible / fichier vide. Lecture intégrale (le fichier
    fait au plus quelques centaines de Ko sur un run get_pricing.py
    complet, c'est négligeable).
    """
    if not path:
        return None
    try:
        with open(path) as f:
            content = f.read()
    except OSError:
        return None
    content = content.rstrip()
    if not content:
        return None
    return content.rsplit("\n", 1)[-1]


def _job_status_dict(job_name: str) -> dict:
    """Renvoie un dict JSON-sérialisable décrivant l'état du dernier
    refresh du job `job_name`. Doit être appelé sous le lock du job.
    Met à jour exit_code / finished_at si le subprocess vient juste
    de se terminer. Inclut `last_log` (dernière ligne du log) tant que
    le subprocess tourne.
    """
    state = _REFRESH_JOBS[job_name]["state"]
    proc = state["process"]
    running = False
    if proc is not None:
        rc = proc.poll()
        if rc is None:
            running = True
        elif state["exit_code"] is None:
            # Premier check après terminaison : on enregistre le code
            # et l'heure de fin une seule fois.
            state["exit_code"] = rc
            state["finished_at"] = time.time()
    return {
        "job": job_name,
        "running": running,
        "started_at": state["started_at"],
        "finished_at": state["finished_at"],
        "exit_code": state["exit_code"],
        "log_path": state["log_path"],
        "last_log": (
            _last_log_line(state["log_path"]) if running else None
        ),
    }


@app.route("/api/refresh/<job>", methods=["GET"])
def api_refresh_status(job: str):
    """Retourne l'état du dernier refresh du job demandé."""
    cfg = _REFRESH_JOBS.get(job)
    if cfg is None:
        return jsonify({"error": f"Job inconnu: {job}"}), 404
    with cfg["lock"]:
        return jsonify(_job_status_dict(job))


@app.route("/api/refresh/<job>", methods=["POST"])
def api_refresh_start(job: str):
    """Lance le script associé au job en sous-process si aucun refresh
    de ce même job n'est déjà en cours. Ne bloque pas. Renvoie 202 +
    état si démarré, 409 + état si déjà en cours, 404 si job inconnu,
    500 si le script est introuvable.
    """
    cfg = _REFRESH_JOBS.get(job)
    if cfg is None:
        return jsonify({"error": f"Job inconnu: {job}"}), 404

    script: Path = cfg["script"]
    if not script.exists():
        return jsonify({"error": f"Script introuvable: {script}"}), 500

    with cfg["lock"]:
        status = _job_status_dict(job)
        if status["running"]:
            return (
                jsonify({"error": "Refresh déjà en cours", **status}),
                409,
            )

        log_path = (
            f"/tmp/{script.stem}-{time.strftime('%Y%m%dT%H%M%S')}.log"
        )
        # Ouvre le log en parent ; le child hérite du fd, on peut
        # refermer côté parent immédiatement après le spawn.
        log_file = open(log_path, "w")
        try:
            proc = subprocess.Popen(
                [sys.executable, str(script)],
                stdout=log_file,
                stderr=subprocess.STDOUT,
                cwd=str(script.parent),
            )
        finally:
            log_file.close()

        state = cfg["state"]
        state["process"] = proc
        state["started_at"] = time.time()
        state["finished_at"] = None
        state["exit_code"] = None
        state["log_path"] = log_path

        return jsonify(_job_status_dict(job)), 202


if __name__ == "__main__":
    port = int(os.environ.get("PORT", "5001"))
    app.run(host="127.0.0.1", port=port, debug=True)
