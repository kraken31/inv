"""Récupère la liste des ETF cotés sur Euronext Paris et remplit la
table `etf` de inv.db.

Source : live.euronext.com, CSV officiel des ETP (MIC XPAR). Donne le
symbole Euronext (mnémo) et le nom. Pas d'appel Yahoo.

Table cible :
    CREATE TABLE etf (id TEXT, name TEXT)
- id   : symbole Euronext (ex. "B28A", "CW8", "ESE")
- name : nom tel que publié par Euronext

Le script est idempotent : à chaque run, la table est recréée à partir
du listing courant (DELETE puis INSERT de toutes les lignes).
"""

import csv
import io
import sqlite3
import urllib.request


DB_PATH = "/home/aurelien/dev/div/inv/inv.db"
EURONEXT_URL = (
    "https://live.euronext.com/en/pd_es/data/track/download"
    "?mics=XPAR"
)


def fetch_euronext_etf_listing() -> list[tuple[str, str]]:
    """Retourne la liste [(symbol, name), ...] des ETF cotés à Paris.

    Télécharge le CSV officiel d'Euronext et garde uniquement les lignes
    avec ISIN, symbole, et un marché qui mentionne Paris.
    """
    req = urllib.request.Request(
        EURONEXT_URL,
        headers={
            "User-Agent": "Mozilla/5.0",
            "Referer": (
                "https://live.euronext.com/en/markets/paris/etfs/list"
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
        # Lignes de préambule (type, date, mention légale) : 1 colonne.
        if len(row) < 4:
            continue
        if not header_seen:
            header_seen = True
            continue
        name, _isin, symbol, market = row[0], row[1], row[2], row[3]
        if not symbol or not _isin:
            continue
        if "Paris" not in market:
            continue
        if symbol in seen:
            continue
        seen.add(symbol)
        listing.append((symbol, name))
    return listing


def ensure_schema(db: sqlite3.Connection) -> None:
    db.execute(
        "CREATE TABLE IF NOT EXISTS etf (id TEXT, name TEXT)"
    )
    db.commit()


def replace_listing(
    db: sqlite3.Connection, listing: list[tuple[str, str]]
) -> None:
    db.execute("DELETE FROM etf")
    db.executemany(
        "INSERT INTO etf (id, name) VALUES (?, ?)",
        listing,
    )
    db.commit()


def main() -> None:
    print("Téléchargement de la liste ETF Euronext (XPAR)…")
    listing = fetch_euronext_etf_listing()
    print(f"{len(listing)} ETF à enregistrer")

    db = sqlite3.connect(DB_PATH)
    try:
        ensure_schema(db)
        replace_listing(db, listing)
        count = db.execute("SELECT COUNT(*) FROM etf").fetchone()[0]
        print(f"Table etf : {count} lignes")
    finally:
        db.close()


if __name__ == "__main__":
    main()
