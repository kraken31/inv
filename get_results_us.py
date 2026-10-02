"""Récupère le résultat net annuel des actions `stocksUS` via Yahoo
Finance et met à jour `resultsUS`.

Même logique que `get_results.py` (`income_stmt`, ligne Net Income).
Le ticker Yahoo est `stocksUS.id`. Les montants sont en dollars.
"""

import sqlite3
import threading
import time
from concurrent.futures import ThreadPoolExecutor, as_completed

from get_results import fetch_net_income_by_year


DB_PATH = "/home/aurelien/dev/div/inv/inv.db"
MAX_WORKERS = 3

_db_lock = threading.Lock()


def ensure_schema(db: sqlite3.Connection) -> None:
    db.execute(
        "CREATE TABLE IF NOT EXISTS resultsUS ("
        "id TEXT, year INTEGER, result INTEGER)"
    )
    db.commit()


def upsert_results(
    db: sqlite3.Connection, stock_id: str, totals: dict[int, int]
) -> None:
    if not totals:
        return
    with _db_lock:
        for year, result in sorted(totals.items()):
            db.execute(
                "DELETE FROM resultsUS WHERE id = ? AND year = ?",
                (stock_id, year),
            )
            db.execute(
                "INSERT INTO resultsUS (id, year, result) "
                "VALUES (?, ?, ?)",
                (stock_id, year, result),
            )
        db.commit()


def process_stock(stock_id: str):
    try:
        totals = fetch_net_income_by_year(stock_id)
        return stock_id, totals, None
    except Exception as exc:
        return stock_id, None, f"{type(exc).__name__}: {exc}"


def main() -> None:
    db = sqlite3.connect(DB_PATH, check_same_thread=False)
    try:
        ensure_schema(db)
        rows = db.execute(
            "SELECT id FROM stocksUS WHERE id IS NOT NULL"
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
                stock_id, totals, err = fut.result()

                if err is not None:
                    errors += 1
                    print(f"[{i}/{len(ids)}] {stock_id} ERROR {err}")
                    continue

                upsert_results(db, stock_id, totals or {})

                if not totals:
                    empty += 1
                    print(f"[{i}/{len(ids)}] {stock_id} 0 année")
                else:
                    ok += 1
                    print(f"[{i}/{len(ids)}] {stock_id} {len(totals)} années")

        elapsed = time.time() - start
        print(
            f"\nTerminé en {elapsed:.1f}s : "
            f"{ok} OK, {empty} sans résultat, {errors} erreurs"
        )
    finally:
        db.close()


if __name__ == "__main__":
    main()
