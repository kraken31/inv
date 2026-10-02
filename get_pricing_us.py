"""Récupère le dernier cours des actions `stocksUS` (S&P 500) via Yahoo
Finance et met à jour `pricingUS`.

Même calcul que `get_pricing.py` (clôture, capitalisation, PER, RSI 14
de Wilder). Le ticker Yahoo est `stocksUS.id` tel quel (ex. "AAPL",
"BRK-B"), sans suffixe de place.

Table cible :
    CREATE TABLE pricingUS (
        id TEXT, date TEXT, price REAL,
        capitalisation REAL, per REAL, rsi REAL
    )
"""

import sqlite3
import threading
import time
from concurrent.futures import ThreadPoolExecutor, as_completed

from get_pricing import compute_per, fetch_last_price


DB_PATH = "/home/aurelien/dev/div/inv/inv.db"
MAX_WORKERS = 3

_db_lock = threading.Lock()


def ensure_schema(db: sqlite3.Connection) -> None:
    db.execute(
        "CREATE TABLE IF NOT EXISTS pricingUS ("
        "id TEXT, date TEXT, price REAL, "
        "capitalisation REAL, per REAL, rsi REAL)"
    )
    db.execute(
        "CREATE TABLE IF NOT EXISTS resultsUS ("
        "id TEXT, year INTEGER, result INTEGER)"
    )
    db.commit()


def latest_net_income(db: sqlite3.Connection, stock_id: str) -> int | None:
    with _db_lock:
        row = db.execute(
            "SELECT result FROM resultsUS "
            "WHERE id = ? AND result IS NOT NULL "
            "ORDER BY year DESC LIMIT 1",
            (stock_id,),
        ).fetchone()
    if row is None:
        return None
    try:
        return int(row[0])
    except (TypeError, ValueError):
        return None


def upsert_pricing(
    db: sqlite3.Connection,
    stock_id: str,
    date_iso: str,
    price: float,
    capitalisation: float | None,
    per: float | None,
    rsi: float | None,
) -> None:
    with _db_lock:
        db.execute(
            "DELETE FROM pricingUS WHERE id = ? AND date = ?",
            (stock_id, date_iso),
        )
        db.execute(
            "INSERT INTO pricingUS "
            "(id, date, price, capitalisation, per, rsi) "
            "VALUES (?, ?, ?, ?, ?, ?)",
            (stock_id, date_iso, price, capitalisation, per, rsi),
        )
        db.commit()


def process_stock(stock_id: str, quantity: int | None):
    try:
        result = fetch_last_price(stock_id)
    except Exception as exc:
        return (
            stock_id,
            quantity,
            None,
            f"{type(exc).__name__}: {exc}",
        )
    return stock_id, quantity, result, None


def main() -> None:
    db = sqlite3.connect(DB_PATH, check_same_thread=False)
    try:
        ensure_schema(db)
        rows = db.execute(
            "SELECT id, quantity FROM stocksUS WHERE id IS NOT NULL"
        ).fetchall()
        print(f"{len(rows)} actions à traiter")

        ok = 0
        no_price = 0
        errors = 0
        start = time.time()

        with ThreadPoolExecutor(max_workers=MAX_WORKERS) as pool:
            futures = {
                pool.submit(process_stock, sid, qty): sid
                for sid, qty in rows
            }
            for i, fut in enumerate(as_completed(futures), 1):
                stock_id, quantity, result, err = fut.result()

                if err is not None:
                    errors += 1
                    print(f"[{i}/{len(rows)}] {stock_id} ERROR {err}")
                    continue

                if result is None:
                    no_price += 1
                    print(f"[{i}/{len(rows)}] {stock_id} sans prix")
                    continue

                date_iso, price, rsi = result
                capitalisation = (
                    price * quantity if quantity is not None else None
                )
                net_income = latest_net_income(db, stock_id)
                per = compute_per(capitalisation, net_income)

                upsert_pricing(
                    db,
                    stock_id,
                    date_iso,
                    price,
                    capitalisation,
                    per,
                    rsi,
                )

                ok += 1
                print(
                    f"[{i}/{len(rows)}] {stock_id} {date_iso} "
                    f"price={price:.4f} "
                    f"cap={capitalisation if capitalisation is None else f'{capitalisation:.0f}'} "
                    f"per={per if per is None else f'{per:.2f}'} "
                    f"rsi={rsi if rsi is None else f'{rsi:.1f}'}"
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
