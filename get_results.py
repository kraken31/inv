"""Récupère le résultat net annuel des actions de la table `stocks` via
Yahoo Finance et met à jour la table `results`.

Source : yfinance (`Ticker.income_stmt`, ligne « Net Income »). Pas de
scraping HTML ni de Selenium.

Le ticker Yahoo est construit en suffixant l'identifiant Euronext par
".PA" (ex. "AC" -> "AC.PA"), exactement comme `get_stocks.py`.

Table cible :
    CREATE TABLE results (id TEXT, year INTEGER, result INTEGER)
- id     : symbole Euronext (= stocks.id)
- year   : année fiscale du résultat net (extrait de la colonne du
           DataFrame `income_stmt`, qui est la date de clôture)
- result : résultat net en euros, arrondi à l'entier (peut être négatif
           en cas de perte)

Le script est idempotent et reprenable : pour chaque action on fait
DELETE puis INSERT sur son id, donc on peut le relancer sans perdre les
données déjà récupérées.
"""

import sqlite3
import threading
import time
from collections import defaultdict
from concurrent.futures import ThreadPoolExecutor, as_completed

import pandas as pd
import yfinance as yf
from yfinance.exceptions import YFRateLimitError


DB_PATH = "/home/aurelien/dev/div/inv/inv.db"
MAX_WORKERS = 3
RATE_LIMIT_BACKOFF = (5, 15, 45)  # secondes entre tentatives sur rate-limit

# Clés possibles pour le résultat net dans le DataFrame `income_stmt`
# renvoyé par yfinance. On prend la première ligne disponible.
NET_INCOME_KEYS = (
    "Net Income",
    "Net Income Common Stockholders",
    "Net Income From Continuing Operation Net Minority Interest",
    "Net Income Continuous Operations",
    "Net Income Including Noncontrolling Interests",
)

_db_lock = threading.Lock()


def fetch_net_income_by_year(ticker: str) -> dict[int, int]:
    """Retourne {année: résultat_net_int} pour le ticker Yahoo donné.
    Gère le rate-limit avec backoff. Renvoie un dict vide si Yahoo n'a
    pas de compte de résultat pour ce ticker. Lève l'exception sur
    autre erreur après les retries.
    """
    last_exc: Exception | None = None
    attempts = len(RATE_LIMIT_BACKOFF) + 1
    for attempt in range(attempts):
        try:
            df = yf.Ticker(ticker).income_stmt
        except YFRateLimitError as exc:
            last_exc = exc
            if attempt < len(RATE_LIMIT_BACKOFF):
                time.sleep(RATE_LIMIT_BACKOFF[attempt])
                continue
            raise
        except AttributeError:
            # Même garde-fou que get_div.py : yfinance plante en interne
            # pour certains tickers délistés ou inconnus.
            return {}
        except Exception:
            raise
        else:
            if df is None or df.empty:
                return {}

            row = None
            for key in NET_INCOME_KEYS:
                if key in df.index:
                    row = df.loc[key]
                    break
            if row is None:
                return {}

            totals: dict[int, int] = {}
            # Plusieurs exercices peuvent partager une année civile (rare
            # mais possible lors d'un changement de date de clôture). On
            # prend la somme dans ce cas.
            buckets: dict[int, float] = defaultdict(float)
            for ts, value in row.items():
                if value is None:
                    continue
                try:
                    if pd.isna(value):
                        continue
                except TypeError:
                    pass
                try:
                    year = ts.year
                except AttributeError:
                    try:
                        year = pd.to_datetime(ts).year
                    except (ValueError, TypeError):
                        continue
                try:
                    buckets[year] += float(value)
                except (TypeError, ValueError):
                    continue
            for year, val in buckets.items():
                totals[year] = int(round(val))
            return totals
    if last_exc is not None:
        raise last_exc
    return {}


def upsert_results(
    db: sqlite3.Connection, stock_id: str, totals: dict[int, int]
) -> None:
    """Remplace les résultats existants pour cette action par les nouveaux."""
    with _db_lock:
        db.execute("DELETE FROM results WHERE id = ?", (stock_id,))
        if totals:
            db.executemany(
                "INSERT INTO results (id, year, result) VALUES (?, ?, ?)",
                [(stock_id, y, r) for y, r in sorted(totals.items())],
            )
        db.commit()


def process_stock(stock_id: str):
    ticker = f"{stock_id}.PA"
    try:
        totals = fetch_net_income_by_year(ticker)
        return stock_id, ticker, totals, None
    except Exception as exc:
        return stock_id, ticker, None, f"{type(exc).__name__}: {exc}"


def main() -> None:
    db = sqlite3.connect(DB_PATH, check_same_thread=False)
    try:
        rows = db.execute(
            "SELECT id FROM stocks WHERE id IS NOT NULL"
        ).fetchall()
        ids = [r[0] for r in rows]
        print(f"{len(ids)} actions à traiter")

        ok = 0
        empty = 0
        errors = 0
        start = time.time()

        with ThreadPoolExecutor(max_workers=MAX_WORKERS) as pool:
            futures = {pool.submit(process_stock, sid): sid for sid in ids}
            for i, fut in enumerate(as_completed(futures), 1):
                stock_id, ticker, totals, err = fut.result()

                if err is not None:
                    errors += 1
                    print(f"[{i}/{len(ids)}] {ticker} ERROR {err}")
                    continue

                upsert_results(db, stock_id, totals or {})

                if not totals:
                    empty += 1
                    print(f"[{i}/{len(ids)}] {ticker} 0 année")
                else:
                    ok += 1
                    print(f"[{i}/{len(ids)}] {ticker} {len(totals)} années")

        elapsed = time.time() - start
        print(
            f"\nTerminé en {elapsed:.1f}s : "
            f"{ok} OK, {empty} sans résultat, {errors} erreurs"
        )
    finally:
        db.close()


if __name__ == "__main__":
    main()
