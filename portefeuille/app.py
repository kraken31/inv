"""Application web Portefeuille.

API Flask qui lit la table `wallet` (jointure sur `stocks` via la colonne
`id`) de la base SQLite locale et expose les données au front.
"""

import os
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

app = Flask(__name__)


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
}


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


@app.route("/action")
def action_page():
    return render_template("action.html")


@app.route("/securite")
def securite_page():
    # On dérive l'année « n » depuis la table results : c'est la
    # dernière année réellement présente, qui dépend de yfinance et
    # non du calendrier. Fallback sur l'année précédente si la table
    # est vide / illisible.
    try:
        with get_db() as conn:
            row = conn.execute(
                "SELECT MAX(year) AS n FROM results"
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
            SELECT MAX(year) AS n FROM results
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
