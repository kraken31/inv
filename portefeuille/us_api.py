"""Routes du marché Actions US (S&P 500).

Mêmes écrans que les actions Paris, sur les tables `stocksUS`,
`pricingUS`, `dividendsUS`, `resultsUS`, `walletUS` et
`walletUSDetails`. Les montants Yahoo sont en dollars.
"""

from datetime import date


def register(flask_app) -> None:
    import app as application

    jsonify = application.jsonify
    request = application.request

    stocks = "stocksUS"
    pricing = "pricingUS"
    dividends = "dividendsUS"
    results = "resultsUS"
    wallet = "walletUS"
    details = "walletUSDetails"

    def owner_exists(conn, proprietaire: str) -> bool:
        row = conn.execute(
            f"""
            SELECT 1 FROM {wallet}
            WHERE proprietaire = ? COLLATE NOCASE
            UNION ALL
            SELECT 1 FROM {details}
            WHERE proprietaire = ? COLLATE NOCASE
            LIMIT 1
            """,
            (proprietaire, proprietaire),
        ).fetchone()
        return row is not None

    @flask_app.route("/api/us/portefeuilles")
    def api_us_portefeuilles():
        query = f"""
            WITH latest_price AS (
                SELECT p.id, p.date, p.price
                FROM {pricing} p
                JOIN (
                    SELECT id, MAX(date) AS max_date
                    FROM {pricing}
                    GROUP BY id
                ) m ON m.id = p.id AND m.max_date = p.date
            ),
            owners AS (
                SELECT proprietaire FROM {wallet}
                WHERE proprietaire IS NOT NULL AND TRIM(proprietaire) != ''
                UNION
                SELECT proprietaire FROM {details}
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
                FROM {wallet} w
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
            LEFT JOIN {details} d
                ON d.proprietaire = o.proprietaire COLLATE NOCASE
            ORDER BY o.proprietaire COLLATE NOCASE
        """
        try:
            with application.get_db() as conn:
                rows = [dict(r) for r in conn.execute(query)]
        except FileNotFoundError as exc:
            return jsonify({"error": str(exc)}), 500
        except application.sqlite3.Error as exc:
            return jsonify({"error": f"Erreur SQLite: {exc}"}), 500
        return jsonify(rows)

    @flask_app.route("/api/us/portefeuilles", methods=["POST"])
    def api_us_portefeuilles_create():
        payload = request.get_json(silent=True) or {}
        try:
            proprietaire = application.normalize_proprietaire(
                payload.get("proprietaire")
            )
            stock_id, quantity, price, dividend, date_str = (
                application.parse_position_payload(payload)
            )
        except application.ProprietaireError as exc:
            return jsonify({"error": str(exc)}), 400
        except ValueError as exc:
            return jsonify({"error": str(exc)}), 400

        try:
            with application.get_db_rw() as conn:
                if owner_exists(conn, proprietaire):
                    return (
                        jsonify({
                            "error": "Un portefeuille existe déjà "
                            "pour ce propriétaire",
                        }),
                        409,
                    )
                if not conn.execute(
                    f"SELECT 1 FROM {stocks} WHERE id = ?", (stock_id,)
                ).fetchone():
                    return jsonify({"error": "Action inconnue"}), 404
                conn.execute(
                    f"INSERT INTO {details} (liquidite, proprietaire) "
                    "VALUES (?, ?)",
                    (0, proprietaire),
                )
                conn.execute(
                    f"INSERT INTO {wallet} "
                    "(id, quantity, date, price, dividend, proprietaire) "
                    "VALUES (?, ?, ?, ?, ?, ?)",
                    (
                        stock_id,
                        quantity,
                        application.iso_to_fr_date(date_str),
                        price,
                        dividend,
                        proprietaire,
                    ),
                )
                conn.commit()
        except FileNotFoundError as exc:
            return jsonify({"error": str(exc)}), 500
        except application.sqlite3.IntegrityError:
            return (
                jsonify({
                    "error": "Un portefeuille existe déjà "
                    "pour ce propriétaire",
                }),
                409,
            )
        except application.sqlite3.Error as exc:
            return jsonify({"error": f"Erreur SQLite: {exc}"}), 500
        return jsonify({"created": proprietaire}), 201

    @flask_app.route("/api/us/wallet")
    def api_us_wallet():
        try:
            proprietaire = application.proprietaire_from_request()
        except application.ProprietaireError as exc:
            return jsonify({"error": str(exc)}), 400

        query = f"""
            WITH latest_price AS (
                SELECT p.id, p.date, p.price, p.per, p.rsi
                FROM {pricing} p
                JOIN (
                    SELECT id, MAX(date) AS max_date
                    FROM {pricing}
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
                (w.quantity * lp.price + w.dividend
                    - w.quantity * w.price) AS plus_minus_value,
                CASE WHEN w.quantity * w.price > 0
                     THEN 100.0 * (w.quantity * lp.price + w.dividend
                                   - w.quantity * w.price)
                                / (w.quantity * w.price)
                END                        AS perf
            FROM {wallet} w
            LEFT JOIN {stocks} s ON s.id = w.id
            LEFT JOIN latest_price lp ON lp.id = w.id
            WHERE w.proprietaire = ? COLLATE NOCASE
            ORDER BY name COLLATE NOCASE
        """
        try:
            with application.get_db() as conn:
                rows = [
                    dict(r) for r in conn.execute(query, (proprietaire,))
                ]
        except FileNotFoundError as exc:
            return jsonify({"error": str(exc)}), 500
        except application.sqlite3.Error as exc:
            return jsonify({"error": f"Erreur SQLite: {exc}"}), 500
        return jsonify(rows)

    @flask_app.route("/api/us/stocks")
    def api_us_stocks():
        try:
            with application.get_db() as conn:
                rows = [
                    dict(r)
                    for r in conn.execute(
                        f"""
                        SELECT s.id, COALESCE(s.name, s.id) AS name
                        FROM {stocks} s
                        ORDER BY name COLLATE NOCASE
                        """
                    )
                ]
        except FileNotFoundError as exc:
            return jsonify({"error": str(exc)}), 500
        except application.sqlite3.Error as exc:
            return jsonify({"error": f"Erreur SQLite: {exc}"}), 500
        return jsonify(rows)

    @flask_app.route("/api/us/stocks/available")
    def api_us_stocks_available():
        try:
            proprietaire = application.proprietaire_from_request()
        except application.ProprietaireError as exc:
            return jsonify({"error": str(exc)}), 400
        try:
            with application.get_db() as conn:
                rows = [
                    dict(r)
                    for r in conn.execute(
                        f"""
                        SELECT s.id, COALESCE(s.name, s.id) AS name
                        FROM {stocks} s
                        WHERE NOT EXISTS (
                            SELECT 1 FROM {wallet} w
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
        except application.sqlite3.Error as exc:
            return jsonify({"error": f"Erreur SQLite: {exc}"}), 500
        return jsonify(rows)

    @flask_app.route("/api/us/wallet", methods=["POST"])
    def api_us_wallet_create():
        payload = request.get_json(silent=True) or {}
        try:
            proprietaire = application.proprietaire_from_request(payload)
            stock_id, quantity, price, dividend, date_str = (
                application.parse_position_payload(payload)
            )
        except application.ProprietaireError as exc:
            return jsonify({"error": str(exc)}), 400
        except ValueError as exc:
            return jsonify({"error": str(exc)}), 400

        try:
            with application.get_db_rw() as conn:
                if not owner_exists(conn, proprietaire):
                    return jsonify({"error": "Portefeuille introuvable"}), 404
                if not conn.execute(
                    f"SELECT 1 FROM {stocks} WHERE id = ?", (stock_id,)
                ).fetchone():
                    return jsonify({"error": "Action inconnue"}), 404
                if conn.execute(
                    f"SELECT 1 FROM {wallet} "
                    "WHERE id = ? AND proprietaire = ? COLLATE NOCASE",
                    (stock_id, proprietaire),
                ).fetchone():
                    return (
                        jsonify({"error": "Action déjà dans le portefeuille"}),
                        409,
                    )
                conn.execute(
                    f"INSERT INTO {wallet} "
                    "(id, quantity, date, price, dividend, proprietaire) "
                    "VALUES (?, ?, ?, ?, ?, ?)",
                    (
                        stock_id,
                        quantity,
                        application.iso_to_fr_date(date_str),
                        price,
                        dividend,
                        proprietaire,
                    ),
                )
                conn.commit()
        except FileNotFoundError as exc:
            return jsonify({"error": str(exc)}), 500
        except application.sqlite3.IntegrityError:
            return (
                jsonify({"error": "Action déjà dans le portefeuille"}),
                409,
            )
        except application.sqlite3.Error as exc:
            return jsonify({"error": f"Erreur SQLite: {exc}"}), 500
        return jsonify({"created": stock_id}), 201

    @flask_app.route("/api/us/wallet/<stock_id>", methods=["DELETE"])
    def api_us_wallet_delete(stock_id: str):
        try:
            proprietaire = application.proprietaire_from_request()
        except application.ProprietaireError as exc:
            return jsonify({"error": str(exc)}), 400
        try:
            with application.get_db_rw() as conn:
                cur = conn.execute(
                    f"DELETE FROM {wallet} "
                    "WHERE id = ? AND proprietaire = ? COLLATE NOCASE",
                    (stock_id, proprietaire),
                )
                if cur.rowcount == 0:
                    return jsonify({"error": "Action introuvable"}), 404
                conn.commit()
        except FileNotFoundError as exc:
            return jsonify({"error": str(exc)}), 500
        except application.sqlite3.Error as exc:
            return jsonify({"error": f"Erreur SQLite: {exc}"}), 500
        return jsonify({"deleted": stock_id})

    @flask_app.route("/api/us/wallet/<stock_id>", methods=["PUT"])
    def api_us_wallet_update(stock_id: str):
        payload = request.get_json(silent=True) or {}
        try:
            proprietaire = application.proprietaire_from_request(payload)
            quantity = int(payload["quantity"])
            price = float(payload["price"])
            dividend = float(payload["dividend"])
            date_str = str(payload["date"])
        except application.ProprietaireError as exc:
            return jsonify({"error": str(exc)}), 400
        except (KeyError, TypeError, ValueError):
            return jsonify({"error": "Champs invalides"}), 400

        if quantity < 0 or price < 0 or dividend < 0:
            return jsonify({"error": "Valeurs négatives interdites"}), 400
        if (
            not application.math.isfinite(price)
            or not application.math.isfinite(dividend)
        ):
            return jsonify({"error": "Valeurs non finies"}), 400
        if not application.re.fullmatch(r"\d{4}-\d{2}-\d{2}", date_str):
            return jsonify({"error": "Date invalide (YYYY-MM-DD)"}), 400

        try:
            with application.get_db_rw() as conn:
                cur = conn.execute(
                    f"UPDATE {wallet} SET quantity = ?, date = ?, "
                    "price = ?, dividend = ? "
                    "WHERE id = ? AND proprietaire = ? COLLATE NOCASE",
                    (
                        quantity,
                        application.iso_to_fr_date(date_str),
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
        except application.sqlite3.Error as exc:
            return jsonify({"error": f"Erreur SQLite: {exc}"}), 500
        return jsonify({"updated": stock_id})

    @flask_app.route("/api/us/liquidite", methods=["GET"])
    def api_us_liquidite():
        try:
            proprietaire = application.proprietaire_from_request()
        except application.ProprietaireError as exc:
            return jsonify({"error": str(exc)}), 400
        try:
            with application.get_db() as conn:
                row = conn.execute(
                    f"SELECT liquidite FROM {details} "
                    "WHERE proprietaire = ? COLLATE NOCASE",
                    (proprietaire,),
                ).fetchone()
        except FileNotFoundError as exc:
            return jsonify({"error": str(exc)}), 500
        except application.sqlite3.Error as exc:
            return jsonify({"error": f"Erreur SQLite: {exc}"}), 500
        return jsonify({"liquidite": row["liquidite"] if row else None})

    @flask_app.route("/api/us/liquidite", methods=["POST"])
    def api_us_liquidite_update():
        payload = request.get_json(silent=True) or {}
        try:
            proprietaire = application.proprietaire_from_request(payload)
        except application.ProprietaireError as exc:
            return jsonify({"error": str(exc)}), 400
        raw = payload.get("liquidite")
        try:
            value = float(raw)
        except (TypeError, ValueError):
            return jsonify({"error": "Champ 'liquidite' invalide"}), 400
        if not application.math.isfinite(value) or value < 0:
            return jsonify({"error": "Champ 'liquidite' invalide"}), 400

        try:
            with application.get_db_rw() as conn:
                if not owner_exists(conn, proprietaire):
                    return jsonify({"error": "Portefeuille introuvable"}), 404
                cur = conn.execute(
                    f"UPDATE {details} SET liquidite = ? "
                    "WHERE proprietaire = ? COLLATE NOCASE",
                    (value, proprietaire),
                )
                if cur.rowcount == 0:
                    conn.execute(
                        f"INSERT INTO {details} (liquidite, proprietaire) "
                        "VALUES (?, ?)",
                        (value, proprietaire),
                    )
                conn.commit()
        except FileNotFoundError as exc:
            return jsonify({"error": str(exc)}), 500
        except application.sqlite3.Error as exc:
            return jsonify({"error": f"Erreur SQLite: {exc}"}), 500
        return jsonify({"liquidite": value})

    @flask_app.route("/api/us/per")
    def api_us_per():
        query = f"""
            WITH latest_per AS (
                SELECT p.id, p.date, p.per
                FROM {pricing} p
                JOIN (
                    SELECT id, MAX(date) AS max_date
                    FROM {pricing}
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
            LEFT JOIN {stocks} s ON s.id = lp.id
            WHERE lp.per > 0
              AND lp.per < 10
              AND lp.date >= date(o.max_date, '-7 days')
            ORDER BY lp.per ASC
        """
        try:
            with application.get_db() as conn:
                rows = [dict(r) for r in conn.execute(query)]
        except FileNotFoundError as exc:
            return jsonify({"error": str(exc)}), 500
        except application.sqlite3.Error as exc:
            return jsonify({"error": f"Erreur SQLite: {exc}"}), 500
        return jsonify(rows)

    @flask_app.route("/api/us/rsi")
    def api_us_rsi():
        query = f"""
            WITH latest_pricing AS (
                SELECT p.id, p.date, p.per, p.rsi
                FROM {pricing} p
                JOIN (
                    SELECT id, MAX(date) AS max_date
                    FROM {pricing}
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
            LEFT JOIN {stocks} s ON s.id = lp.id
            WHERE lp.rsi < 30
              AND lp.per IS NOT NULL
              AND lp.date >= date(o.max_date, '-7 days')
            ORDER BY lp.rsi ASC
        """
        try:
            with application.get_db() as conn:
                rows = [dict(r) for r in conn.execute(query)]
        except FileNotFoundError as exc:
            return jsonify({"error": str(exc)}), 500
        except application.sqlite3.Error as exc:
            return jsonify({"error": f"Erreur SQLite: {exc}"}), 500
        return jsonify(rows)

    @flask_app.route("/api/us/rendement")
    def api_us_rendement():
        query = f"""
            WITH latest_price AS (
                SELECT p.id, p.date, p.price, p.per
                FROM {pricing} p
                JOIN (
                    SELECT id, MAX(date) AS max_date
                    FROM {pricing}
                    GROUP BY id
                ) m ON m.id = p.id AND m.max_date = p.date
            ),
            current_div AS (
                SELECT id, dividend
                FROM {dividends}
                WHERE year = CAST(strftime('%Y', 'now') AS INTEGER)
            ),
            prev_div AS (
                SELECT id, dividend
                FROM {dividends}
                WHERE year = CAST(strftime('%Y', 'now') AS INTEGER) - 1
            ),
            avg5_div AS (
                SELECT d.id, SUM(d.dividend) / 5.0 AS dividend
                FROM {dividends} d
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
                FROM {dividends} d
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
            FROM {stocks} s
            LEFT JOIN current_div d   ON d.id = s.id
            LEFT JOIN prev_div dp     ON dp.id = s.id
            LEFT JOIN avg5_div d5     ON d5.id = s.id
            LEFT JOIN avg10_div d10   ON d10.id = s.id
            LEFT JOIN latest_price lp ON lp.id = s.id
            WHERE lp.per IS NOT NULL
            ORDER BY rendement DESC
        """
        try:
            with application.get_db() as conn:
                rows = [dict(r) for r in conn.execute(query)]
        except FileNotFoundError as exc:
            return jsonify({"error": str(exc)}), 500
        except application.sqlite3.Error as exc:
            return jsonify({"error": f"Erreur SQLite: {exc}"}), 500
        return jsonify(rows)

    @flask_app.route("/api/us/securite")
    def api_us_securite():
        query = f"""
            WITH latest_per AS (
                SELECT p.id, p.date, p.per
                FROM {pricing} p
                JOIN (
                    SELECT id, MAX(date) AS max_date
                    FROM {pricing}
                    WHERE per IS NOT NULL
                    GROUP BY id
                ) m ON m.id = p.id AND m.max_date = p.date
            ),
            overall AS (
                SELECT MAX(date) AS max_date FROM latest_per
            ),
            year_n AS (
                SELECT MAX(year) AS n FROM (
                    SELECT year
                    FROM {results}
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
                FROM {results} r
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
            LEFT JOIN {stocks} s ON s.id = lp.id
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
            with application.get_db() as conn:
                rows = [dict(r) for r in conn.execute(query)]
        except FileNotFoundError as exc:
            return jsonify({"error": str(exc)}), 500
        except application.sqlite3.Error as exc:
            return jsonify({"error": f"Erreur SQLite: {exc}"}), 500
        return jsonify(rows)

    @flask_app.route("/api/us/action/search")
    def api_us_action_search():
        q = (request.args.get("q") or "").strip()
        if not q:
            return jsonify([])
        like = f"%{q}%"
        try:
            with application.get_db() as conn:
                rows = [
                    dict(r)
                    for r in conn.execute(
                        f"""
                        SELECT id, COALESCE(name, id) AS name
                        FROM {stocks}
                        WHERE id LIKE ? OR name LIKE ?
                        ORDER BY name COLLATE NOCASE
                        LIMIT 20
                        """,
                        (like, like),
                    )
                ]
        except FileNotFoundError as exc:
            return jsonify({"error": str(exc)}), 500
        except application.sqlite3.Error as exc:
            return jsonify({"error": f"Erreur SQLite: {exc}"}), 500
        return jsonify(rows)

    @flask_app.route("/api/us/action/<stock_id>")
    def api_us_action_detail(stock_id: str):
        try:
            with application.get_db() as conn:
                stock = conn.execute(
                    f"SELECT id, COALESCE(name, id) AS name "
                    f"FROM {stocks} WHERE id = ?",
                    (stock_id,),
                ).fetchone()
                if stock is None:
                    return (
                        jsonify({"error": f"Action introuvable: {stock_id}"}),
                        404,
                    )
                latest_per = conn.execute(
                    f"SELECT date, per FROM {pricing} "
                    "WHERE id = ? AND per IS NOT NULL "
                    "ORDER BY date DESC LIMIT 1",
                    (stock_id,),
                ).fetchone()
                latest_rsi = conn.execute(
                    f"SELECT date, rsi FROM {pricing} "
                    "WHERE id = ? AND rsi IS NOT NULL "
                    "ORDER BY date DESC LIMIT 1",
                    (stock_id,),
                ).fetchone()
                latest_price = conn.execute(
                    f"SELECT date, price FROM {pricing} "
                    "WHERE id = ? AND price IS NOT NULL "
                    "ORDER BY date DESC LIMIT 1",
                    (stock_id,),
                ).fetchone()
                dividend_rows = [
                    dict(r)
                    for r in conn.execute(
                        f"SELECT year, dividend FROM {dividends} "
                        "WHERE id = ? AND year IS NOT NULL "
                        "ORDER BY year DESC",
                        (stock_id,),
                    )
                ]
                result_rows = [
                    dict(r)
                    for r in conn.execute(
                        f"SELECT year, result FROM {results} "
                        "WHERE id = ? AND year IS NOT NULL "
                        "ORDER BY year DESC",
                        (stock_id,),
                    )
                ]
        except FileNotFoundError as exc:
            return jsonify({"error": str(exc)}), 500
        except application.sqlite3.Error as exc:
            return jsonify({"error": f"Erreur SQLite: {exc}"}), 500

        current_year = date.today().year
        price = latest_price["price"] if latest_price else None
        div_by_year = {
            d["year"]: d["dividend"]
            for d in dividend_rows
            if d["year"] is not None and d["dividend"] is not None
        }

        def avg_dividend(span: int) -> float | None:
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
            "dividends": dividend_rows,
            "results": result_rows,
        })
