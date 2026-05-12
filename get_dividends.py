"""Récupère l'historique des dividendes des actions de la table `stocks`
via Yahoo Finance et met à jour la table `dividends` (agrégation par
année).

Source : yfinance (`Ticker.dividends`). Pas de scraping HTML, pas de
Selenium. Le ticker Yahoo est construit en suffixant l'identifiant
Euronext par ".PA" (ex. "AC" -> "AC.PA"), comme `get_stocks.py` /
`get_pricing.py` / `get_results.py`.

Table cible :
    CREATE TABLE dividends (
        id TEXT,
        year INTEGER,
        dividend REAL
    )
- id       : symbole Euronext (= stocks.id)
- year     : année du dividende
- dividend : somme des dividendes versés cette année-là (en devise
             d'origine renvoyée par Yahoo)

Le script est idempotent et reprenable : chaque action est mise à jour
indépendamment (DELETE puis INSERT sur son id), donc on peut le relancer
sans perdre les données déjà récupérées.
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

_db_lock = threading.Lock()


def fetch_dividends_by_year(ticker: str) -> dict[int, float]:
    """Retourne {année: total_dividendes} pour le ticker Yahoo donné.
    Gère le rate-limit avec backoff. Renvoie un dict vide si pas de
    dividende. Lève l'exception sur autre erreur après les retries.
    """
    last_exc: Exception | None = None
    attempts = len(RATE_LIMIT_BACKOFF) + 1
    for attempt in range(attempts):
        try:
            series = yf.Ticker(ticker).dividends
        except YFRateLimitError as exc:
            last_exc = exc
            if attempt < len(RATE_LIMIT_BACKOFF):
                time.sleep(RATE_LIMIT_BACKOFF[attempt])
                continue
            raise
        except AttributeError:
            # Bug yfinance pour certains tickers sans historique (souvent
            # inconnus ou délistés) : 'PriceHistory' object has no
            # attribute '_dividends'. On traite comme "pas de données".
            return {}
        except Exception:
            raise
        else:
            totals: dict[int, float] = defaultdict(float)
            for ts, value in series.items():
                # Certains tickers renvoient un index str au lieu d'un
                # Timestamp -> on convertit défensivement.
                try:
                    year = ts.year
                except AttributeError:
                    try:
                        year = pd.to_datetime(ts).year
                    except (ValueError, TypeError):
                        continue
                try:
                    totals[year] += float(value)
                except (TypeError, ValueError):
                    continue
            return dict(totals)
    if last_exc is not None:
        raise last_exc
    return {}


def upsert_dividends(
    db: sqlite3.Connection, stock_id: str, totals: dict[int, float]
) -> None:
    """Remplace les dividendes existants pour cette action par les
    nouveaux."""
    with _db_lock:
        db.execute("DELETE FROM dividends WHERE id = ?", (stock_id,))
        if totals:
            db.executemany(
                "INSERT INTO dividends (id, year, dividend) "
                "VALUES (?, ?, ?)",
                [
                    (stock_id, y, round(d, 6))
                    for y, d in sorted(totals.items())
                ],
            )
        db.commit()


def process_stock(stock_id: str):
    ticker = f"{stock_id}.PA"
    try:
        totals = fetch_dividends_by_year(ticker)
        return stock_id, ticker, totals, None
    except Exception as exc:
        return stock_id, ticker, None, f"{type(exc).__name__}: {exc}"


def main() -> None:
    db = sqlite3.connect(DB_PATH, check_same_thread=False)
    try:
        rows = db.execute(
            "SELECT id FROM stocks WHERE id IS NOT NULL"
        ).fetchall()
        print(f"{len(rows)} actions à traiter")

        ok = 0
        empty = 0
        errors = 0
        start = time.time()

        with ThreadPoolExecutor(max_workers=MAX_WORKERS) as pool:
            futures = {
                pool.submit(process_stock, sid): sid for (sid,) in rows
            }
            for i, fut in enumerate(as_completed(futures), 1):
                stock_id, ticker, totals, err = fut.result()

                if err is not None:
                    errors += 1
                    print(f"[{i}/{len(rows)}] {ticker} ERROR {err}")
                    continue

                upsert_dividends(db, stock_id, totals or {})

                if not totals:
                    empty += 1
                    print(f"[{i}/{len(rows)}] {ticker} 0 année")
                else:
                    ok += 1
                    print(
                        f"[{i}/{len(rows)}] {ticker} {len(totals)} années"
                    )

        elapsed = time.time() - start
        print(
            f"\nTerminé en {elapsed:.1f}s : "
            f"{ok} OK, {empty} sans dividende, {errors} erreurs"
        )
    finally:
        db.close()


if __name__ == "__main__":
    main()
