"""Récupère le dernier prix connu des ETF de la table `etf` via Yahoo
Finance et met à jour la table `pricing_etf`.

Source : yfinance (`Ticker.history`). Le ticker Yahoo est construit en
suffixant le mnémo Euronext par ".PA" (ex. "B28A" -> "B28A.PA"), comme
`get_pricing.py` pour les actions.

Table cible :
    CREATE TABLE pricing_etf (
        id TEXT,
        date TEXT,
        price REAL
    )
- id    : symbole Euronext (= etf.id)
- date  : date du dernier prix connu, au format YYYY-MM-DD
- price : dernier cours de clôture connu

Le script est idempotent et reprenable : pour chaque ETF on écrase
uniquement la ligne (id, date) — les autres dates déjà présentes sont
conservées.
"""

import sqlite3
import threading
import time
from concurrent.futures import ThreadPoolExecutor, as_completed

import pandas as pd
import yfinance as yf
from yfinance.exceptions import YFRateLimitError


DB_PATH = "/home/aurelien/dev/div/inv/inv.db"
MAX_WORKERS = 3
RATE_LIMIT_BACKOFF = (5, 15, 45)
HISTORY_PERIOD = "5d"

_db_lock = threading.Lock()


def fetch_last_price(ticker: str) -> tuple[str, float] | None:
    """Retourne (date_iso, price) du dernier cours de clôture connu.
    Gère le rate-limit avec backoff. Renvoie None si Yahoo n'a pas
    d'historique. Lève l'exception sur autre erreur après les retries.
    """
    last_exc: Exception | None = None
    attempts = len(RATE_LIMIT_BACKOFF) + 1
    for attempt in range(attempts):
        try:
            df = yf.Ticker(ticker).history(period=HISTORY_PERIOD)
        except YFRateLimitError as exc:
            last_exc = exc
            if attempt < len(RATE_LIMIT_BACKOFF):
                time.sleep(RATE_LIMIT_BACKOFF[attempt])
                continue
            raise
        except AttributeError:
            return None
        except Exception:
            raise
        else:
            if df is None or df.empty or "Close" not in df.columns:
                return None

            close = df["Close"].dropna()
            if close.empty:
                return None

            ts = close.index[-1]
            value = close.iloc[-1]

            try:
                if pd.isna(value):
                    return None
            except TypeError:
                pass

            try:
                date_iso = ts.strftime("%Y-%m-%d")
            except AttributeError:
                try:
                    date_iso = pd.to_datetime(ts).strftime("%Y-%m-%d")
                except (ValueError, TypeError):
                    return None

            try:
                price = float(value)
            except (TypeError, ValueError):
                return None

            return date_iso, price

    if last_exc is not None:
        raise last_exc
    return None


def ensure_schema(db: sqlite3.Connection) -> None:
    db.execute(
        "CREATE TABLE IF NOT EXISTS pricing_etf ("
        "id TEXT, date TEXT, price REAL)"
    )
    db.commit()


def upsert_pricing(
    db: sqlite3.Connection,
    etf_id: str,
    date_iso: str,
    price: float,
) -> None:
    with _db_lock:
        db.execute(
            "DELETE FROM pricing_etf WHERE id = ? AND date = ?",
            (etf_id, date_iso),
        )
        db.execute(
            "INSERT INTO pricing_etf (id, date, price) VALUES (?, ?, ?)",
            (etf_id, date_iso, price),
        )
        db.commit()


def process_etf(etf_id: str):
    ticker = f"{etf_id}.PA"
    try:
        result = fetch_last_price(ticker)
    except Exception as exc:
        return etf_id, ticker, None, f"{type(exc).__name__}: {exc}"
    return etf_id, ticker, result, None


def main() -> None:
    db = sqlite3.connect(DB_PATH, check_same_thread=False)
    try:
        ensure_schema(db)
        rows = db.execute(
            "SELECT id FROM etf WHERE id IS NOT NULL"
        ).fetchall()
        print(f"{len(rows)} ETF à traiter")

        ok = 0
        no_price = 0
        errors = 0
        start = time.time()

        with ThreadPoolExecutor(max_workers=MAX_WORKERS) as pool:
            futures = {
                pool.submit(process_etf, sid): sid for (sid,) in rows
            }
            for i, fut in enumerate(as_completed(futures), 1):
                etf_id, ticker, result, err = fut.result()

                if err is not None:
                    errors += 1
                    print(f"[{i}/{len(rows)}] {ticker} ERROR {err}")
                    continue

                if result is None:
                    no_price += 1
                    print(f"[{i}/{len(rows)}] {ticker} sans prix")
                    continue

                date_iso, price = result
                upsert_pricing(db, etf_id, date_iso, price)
                ok += 1
                print(
                    f"[{i}/{len(rows)}] {ticker} {date_iso} "
                    f"price={price:.4f}"
                )

        elapsed = time.time() - start
        print(
            f"\nTerminé en {elapsed:.1f}s : "
            f"{ok} OK, {no_price} sans prix, {errors} erreurs"
        )
    finally:
        db.close()


if __name__ == "__main__":
    main()
