"""Récupère la liste des actions cotées sur Euronext Paris (compartiments
A, B, C) et Euronext Growth Paris, et remplit la table `stocks` de
inv.db.

Sources :
- live.euronext.com : téléchargement CSV officiel listant tous les titres
  des MIC XPAR (Euronext Paris -> compartiments A, B, C) et ALXP
  (Euronext Growth Paris). Donne le symbole Euronext (mnémo), l'ISIN, le
  nom et le marché.
- yfinance (Yahoo Finance) : nom complet et nombre d'actions en
  circulation (`sharesOutstanding`) par ticker.

Le ticker Yahoo est construit en ajoutant le suffixe ".PA" au symbole
Euronext (ex. "AC" -> "AC.PA").

Table cible :
    CREATE TABLE stocks (id TEXT, name TEXT, quantity INTEGER)
- id       : symbole Euronext (ex. "AC", "AI", "AIR", "AL2SI")
- name     : `shortName` renvoyé par yfinance (à défaut `longName`,
             puis le nom Euronext)
- quantity : `sharesOutstanding` renvoyé par yfinance, ou NULL si
             indisponible

Le script est idempotent et reprenable : chaque action est mise à jour
indépendamment (DELETE puis INSERT sur son id), donc on peut le relancer
sans perdre les données déjà récupérées.
"""

import csv
import io
import sqlite3
import threading
import time
import urllib.request
from concurrent.futures import ThreadPoolExecutor, as_completed

import yfinance as yf
from yfinance.exceptions import YFRateLimitError


DB_PATH = "/home/aurelien/dev/div/inv/inv.db"
EURONEXT_URL = (
    "https://live.euronext.com/en/pd_es/data/stocks/download"
    "?mics=ALXP%2CXPAR"
)
MAX_WORKERS = 3
RATE_LIMIT_BACKOFF = (5, 15, 45)  # secondes entre tentatives sur rate-limit

_db_lock = threading.Lock()


def fetch_euronext_listing() -> list[tuple[str, str]]:
    """Retourne la liste [(symbol, name), ...] des actions XPAR + ALXP.

    Télécharge le CSV officiel d'Euronext et garde uniquement les lignes
    représentant une action listée à Paris (avec ISIN et symbole).
    """
    req = urllib.request.Request(
        EURONEXT_URL,
        headers={
            "User-Agent": "Mozilla/5.0",
            "Referer": (
                "https://live.euronext.com/en/products/equities/list"
            ),
        },
    )
    with urllib.request.urlopen(req, timeout=30) as response:
        raw = response.read()

    text = raw.decode("utf-8-sig", errors="replace")
    reader = csv.reader(io.StringIO(text), delimiter=";")

    listing: list[tuple[str, str]] = []
    seen: set[str] = set()
    header_seen = False
    for row in reader:
        # Les lignes "European Equities", date, mention légale n'ont
        # qu'une seule colonne -> on les ignore.
        if len(row) < 4:
            continue
        if not header_seen:
            # Première ligne à 15 colonnes = en-tête CSV.
            header_seen = True
            continue
        name, _isin, symbol, market = row[0], row[1], row[2], row[3]
        if not symbol or not _isin:
            continue
        # Sécurité : on garde uniquement les cotations qui mentionnent
        # Paris (le mics=XPAR,ALXP devrait déjà s'en assurer).
        if "Paris" not in market:
            continue
        if symbol in seen:
            continue
        seen.add(symbol)
        listing.append((symbol, name))
    return listing


def fetch_info(symbol: str) -> tuple[str | None, int | None]:
    """Retourne (longName, sharesOutstanding) pour le ticker Yahoo
    `<symbol>.PA`. Gère le rate-limit avec backoff. Renvoie (None, None)
    si Yahoo ne connaît pas le ticker.
    """
    ticker = f"{symbol}.PA"
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
            # Même garde-fou que get_div.py pour les tickers délistés :
            # certains plantent en interne dans yfinance.
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


def upsert_stock(
    db: sqlite3.Connection,
    symbol: str,
    name: str | None,
    quantity: int | None,
) -> None:
    """Remplace la ligne existante (id = symbol) de `stocks` par les
    nouvelles valeurs."""
    with _db_lock:
        db.execute("DELETE FROM stocks WHERE id = ?", (symbol,))
        db.execute(
            "INSERT INTO stocks (id, name, quantity) VALUES (?, ?, ?)",
            (symbol, name, quantity),
        )
        db.commit()


def process_stock(symbol: str, fallback_name: str):
    try:
        name, shares = fetch_info(symbol)
    except Exception as exc:
        return symbol, fallback_name, None, f"{type(exc).__name__}: {exc}"
    return symbol, (name or fallback_name), shares, None


def main() -> None:
    print("Téléchargement de la liste Euronext (XPAR + ALXP)…")
    listing = fetch_euronext_listing()
    print(f"{len(listing)} actions à traiter")

    db = sqlite3.connect(DB_PATH, check_same_thread=False)
    try:
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
                    # On enregistre quand même le nom Euronext pour ne
                    # pas perdre la ligne ; quantity reste NULL.
                    upsert_stock(db, symbol, name, None)
                    continue

                upsert_stock(db, symbol, name, shares)

                if shares is None:
                    no_quantity += 1
                    print(
                        f"[{i}/{len(listing)}] {symbol} OK (sans quantité)"
                    )
                else:
                    ok += 1
                    print(
                        f"[{i}/{len(listing)}] {symbol} {shares} actions"
                    )

        elapsed = time.time() - start
        print(
            f"\nTerminé en {elapsed:.1f}s : "
            f"{ok} OK, {no_quantity} sans quantité, {errors} erreurs"
        )
    finally:
        db.close()


if __name__ == "__main__":
    main()
