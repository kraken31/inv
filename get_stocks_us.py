"""Récupère les composants du S&P 500 et remplit la table `stocksUS`.

Source de la liste : page Wikipédia « List of S&P 500 companies »
(colonnes Symbol et Security). Le ticker Yahoo est le symbole, avec
les classes d'actions en tiret (ex. « BRK.B » -> « BRK-B »).

Le nom et le nombre d'actions en circulation viennent ensuite de
Yahoo Finance (`shortName`, `sharesOutstanding`), comme `get_stocks.py`.

Table cible :
    CREATE TABLE stocksUS (id TEXT, name TEXT, quantity INTEGER)
- id       : ticker Yahoo (ex. "AAPL", "BRK-B")
- name     : `shortName` Yahoo, à défaut le nom Wikipédia
- quantity : `sharesOutstanding`, ou NULL si indisponible

Le script est idempotent : chaque action est remplacée (DELETE puis
INSERT). La liste Wikipédia est enregistrée avant l'enrichissement
Yahoo, pour que le référentiel soit déjà consultable si le run est
interrompu.
"""

import io
import sqlite3
import threading
import time
import urllib.request
from concurrent.futures import ThreadPoolExecutor, as_completed

import pandas as pd
import yfinance as yf
from yfinance.exceptions import YFRateLimitError


DB_PATH = "/home/aurelien/dev/div/inv/inv.db"
WIKI_URL = "https://en.wikipedia.org/wiki/List_of_S%26P_500_companies"
MAX_WORKERS = 3
RATE_LIMIT_BACKOFF = (5, 15, 45)

_db_lock = threading.Lock()


def yahoo_ticker(symbol: str) -> str:
    """Symbole Wikipédia -> ticker Yahoo (classes en tiret)."""
    return symbol.strip().upper().replace(".", "-")


def fetch_sp500_listing() -> list[tuple[str, str]]:
    """Retourne [(ticker Yahoo, nom), ...] des composants du S&P 500."""
    req = urllib.request.Request(
        WIKI_URL,
        headers={"User-Agent": "Mozilla/5.0 (inv; local research)"},
    )
    with urllib.request.urlopen(req, timeout=30) as response:
        raw = response.read()

    tables = pd.read_html(io.BytesIO(raw))
    frame = None
    for table in tables:
        columns = {str(col).strip() for col in table.columns}
        if "Symbol" in columns and "Security" in columns:
            frame = table
            break
    if frame is None:
        raise RuntimeError("Table des composants S&P 500 introuvable")

    listing: list[tuple[str, str]] = []
    seen: set[str] = set()
    for _, row in frame.iterrows():
        symbol = str(row["Symbol"]).strip()
        name = str(row["Security"]).strip()
        if not symbol or symbol.lower() == "nan":
            continue
        ticker = yahoo_ticker(symbol)
        if not ticker or ticker in seen:
            continue
        seen.add(ticker)
        if not name or name.lower() == "nan":
            name = ticker
        listing.append((ticker, name))
    if len(listing) < 400:
        raise RuntimeError(
            f"Liste S&P 500 trop courte ({len(listing)} titres)"
        )
    return listing


def fetch_info(ticker: str) -> tuple[str | None, int | None]:
    """Retourne (shortName, sharesOutstanding) pour le ticker Yahoo.
    Renvoie (None, None) si Yahoo ne connaît pas le ticker.
    """
    last_exc: Exception | None = None
    attempts = len(RATE_LIMIT_BACKOFF) + 1
    for attempt in range(attempts):
        try:
            info = yf.Ticker(ticker).info or {}
        except YFRateLimitError as exc:
            last_exc = exc
            if attempt < len(RATE_LIMIT_BACKOFF):
                time.sleep(RATE_LIMIT_BACKOFF[attempt])
                continue
            raise
        except AttributeError:
            return None, None
        except Exception:
            raise
        else:
            name = info.get("shortName") or info.get("longName")
            shares_raw = info.get("sharesOutstanding")
            try:
                shares = (
                    int(shares_raw) if shares_raw is not None else None
                )
            except (TypeError, ValueError):
                shares = None
            return name, shares
    if last_exc is not None:
        raise last_exc
    return None, None


def ensure_schema(db: sqlite3.Connection) -> None:
    db.execute(
        "CREATE TABLE IF NOT EXISTS stocksUS ("
        "id TEXT PRIMARY KEY, name TEXT, quantity INTEGER)"
    )
    db.commit()


def upsert_stock(
    db: sqlite3.Connection,
    symbol: str,
    name: str | None,
    quantity: int | None,
) -> None:
    with _db_lock:
        db.execute("DELETE FROM stocksUS WHERE id = ?", (symbol,))
        db.execute(
            "INSERT INTO stocksUS (id, name, quantity) VALUES (?, ?, ?)",
            (symbol, name, quantity),
        )
        db.commit()


def seed_listing(
    db: sqlite3.Connection, listing: list[tuple[str, str]]
) -> None:
    """Enregistre le nom Wikipédia tout de suite. La quantité déjà
    connue est conservée pour ne pas la perdre si on relance le script.
    """
    with _db_lock:
        for ticker, name in listing:
            row = db.execute(
                "SELECT quantity FROM stocksUS WHERE id = ?", (ticker,)
            ).fetchone()
            quantity = row[0] if row else None
            db.execute("DELETE FROM stocksUS WHERE id = ?", (ticker,))
            db.execute(
                "INSERT INTO stocksUS (id, name, quantity) VALUES (?, ?, ?)",
                (ticker, name, quantity),
            )
        current = {ticker for ticker, _name in listing}
        stale = [
            row[0]
            for row in db.execute("SELECT id FROM stocksUS")
            if row[0] not in current
        ]
        for ticker in stale:
            db.execute("DELETE FROM stocksUS WHERE id = ?", (ticker,))
        db.commit()


def process_stock(symbol: str, fallback_name: str):
    try:
        name, shares = fetch_info(symbol)
    except Exception as exc:
        return symbol, fallback_name, None, f"{type(exc).__name__}: {exc}"
    return symbol, (name or fallback_name), shares, None


def main() -> None:
    print("Téléchargement de la liste S&P 500…")
    listing = fetch_sp500_listing()
    print(f"{len(listing)} actions à traiter")

    db = sqlite3.connect(DB_PATH, check_same_thread=False)
    try:
        ensure_schema(db)
        seed_listing(db, listing)
        print("Référentiel enregistré, enrichissement Yahoo…")

        ok = 0
        no_quantity = 0
        errors = 0
        start = time.time()

        with ThreadPoolExecutor(max_workers=MAX_WORKERS) as pool:
            futures = {
                pool.submit(process_stock, sym, nm): sym
                for sym, nm in listing
            }
            for i, fut in enumerate(as_completed(futures), 1):
                symbol, name, shares, err = fut.result()

                if err is not None:
                    errors += 1
                    print(f"[{i}/{len(listing)}] {symbol} ERROR {err}")
                    continue

                upsert_stock(db, symbol, name, shares)

                if shares is None:
                    no_quantity += 1
                    print(f"[{i}/{len(listing)}] {symbol} OK (sans quantité)")
                else:
                    ok += 1
                    print(f"[{i}/{len(listing)}] {symbol} {shares} actions")

        elapsed = time.time() - start
        print(
            f"\nTerminé en {elapsed:.1f}s : "
            f"{ok} OK, {no_quantity} sans quantité, {errors} erreurs"
        )
    finally:
        db.close()


if __name__ == "__main__":
    main()
