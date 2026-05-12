"""Application web Portefeuille.

API Flask qui lit la table `wallet` (jointure sur `stocks` via la colonne
`id`) de la base SQLite locale et expose les données au front.
"""

import os
import sqlite3
from datetime import date
from pathlib import Path

from flask import Flask, jsonify, render_template


DB_PATH = os.environ.get(
    "PORTEFEUILLE_DB",
    "/home/aurelien/dev/div/inv/inv.db",
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


@app.route("/per")
def per_page():
    return render_template("per.html")


@app.route("/rsi")
def rsi_page():
    return render_template("rsi.html")


@app.route("/rendement")
def rendement_page():
    current_year = date.today().year
    return render_template(
        "rendement.html",
        year=current_year,
        year_prev=current_year - 1,
    )


@app.route("/api/wallet")
def api_wallet():
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
