"""Application web Portefeuille.

API Flask qui lit la table `wallet` (jointure sur `stocks` via la colonne
`isin`) de la base SQLite locale et expose les données au front.
"""

import os
import sqlite3
from pathlib import Path

from flask import Flask, jsonify, render_template


DB_PATH = os.environ.get(
    "PORTEFEUILLE_DB",
    "/home/aurelien/dev/div/db/per_analysis.db",
)

app = Flask(__name__)


def get_db() -> sqlite3.Connection:
    """Connexion SQLite en lecture seule."""
    if not Path(DB_PATH).exists():
        raise FileNotFoundError(f"Base introuvable: {DB_PATH}")
    conn = sqlite3.connect(f"file:{DB_PATH}?mode=ro", uri=True)
    conn.row_factory = sqlite3.Row
    return conn


@app.route("/")
def index():
    return render_template("index.html")


@app.route("/api/wallet")
def api_wallet():
    query = """
        WITH latest_price AS (
            SELECT p.isin, p.date, p.price, p.per
            FROM pricing p
            JOIN (
                SELECT isin, MAX(date) AS max_date
                FROM pricing
                GROUP BY isin
            ) m ON m.isin = p.isin AND m.max_date = p.date
        )
        SELECT
            COALESCE(s.name, w.isin)   AS name,
            w.isin                     AS isin,
            w.quantity                 AS quantity,
            w.date                     AS purchase_date,
            w.price                    AS purchase_price,
            (w.quantity * w.price)     AS purchase_amount,
            w.dividend                 AS dividend,
            lp.date                    AS current_date,
            lp.price                   AS current_price,
            (w.quantity * lp.price)    AS current_amount,
            lp.per                     AS per,
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
        LEFT JOIN stocks s       ON s.isin = w.isin
        LEFT JOIN latest_price lp ON lp.isin = w.isin
        ORDER BY name COLLATE NOCASE
    """
    try:
        with get_db() as conn:
            rows = [dict(r) for r in conn.execute(query)]
    except FileNotFoundError as exc:
        return jsonify({"error": str(exc)}), 500
    except sqlite3.Error as exc:
        return jsonify({"error": f"Erreur SQLite: {exc}"}), 500

    return jsonify(rows)


@app.route("/api/liquidite")
def api_liquidite():
    try:
        with get_db() as conn:
            row = conn.execute(
                "SELECT liquidite FROM walletDetails LIMIT 1"
            ).fetchone()
    except FileNotFoundError as exc:
        return jsonify({"error": str(exc)}), 500
    except sqlite3.Error as exc:
        return jsonify({"error": f"Erreur SQLite: {exc}"}), 500

    return jsonify({"liquidite": row["liquidite"] if row else None})


if __name__ == "__main__":
    port = int(os.environ.get("PORT", "5001"))
    app.run(host="127.0.0.1", port=port, debug=True)
