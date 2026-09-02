"""Récupère la liste des ETF cotés sur Euronext Paris et remplit la
table `etf` de inv.db.

Sources :
- live.euronext.com : CSV officiel des ETP (MIC XPAR) pour le mnémo,
  l'ISIN et un nom court de repli.
- yfinance (Yahoo Finance) : `longName` (à défaut `shortName`).
  Le libellé Yahoo est plus explicite que celui d'Euronext
  (ex. "iBds D28 Term E Ac" ->
  "iShares iBonds Dec 2028 Term € Corp UCITS ETF EUR (Acc)").
- justETF : TER et **catégorie** (classe d'actifs : Actions, Obligations,
  etc.) via la fiche `etf-profile.html?isin=...`. Plus fiable que Yahoo
  `netExpenseRatio` pour les UCITS européens. Yahoo reste un repli pour
  le TER si justETF n'a pas la fiche.

Le ticker Yahoo est `<symbole>.PA` (ex. "B28A" -> "B28A.PA").

Table cible :
    CREATE TABLE etf (id TEXT, name TEXT, isin TEXT, ter REAL, category TEXT)
- id       : symbole Euronext (ex. "B28A", "CW8", "ESE")
- name     : `longName` Yahoo, à défaut `shortName`, à défaut le nom Euronext
- isin     : code ISIN Euronext (ex. "IE0008UEVOE0")
- ter      : TER en pourcentage (ex. 0.12 = 0,12 %), ou NULL si inconnu
- category : classe d'actifs justETF (ex. "Obligations"), ou NULL

Le script est idempotent et reprenable : chaque ETF est mis à jour
indépendamment (DELETE puis INSERT sur son id). Les lignes absentes du
listing courant sont ensuite supprimées.
"""

import argparse
import csv
import io
import math
import re
import sqlite3
import threading
import time
import urllib.error
import urllib.request
from concurrent.futures import ThreadPoolExecutor, as_completed

import yfinance as yf
from yfinance.exceptions import YFRateLimitError


DB_PATH = "/home/aurelien/dev/div/inv/inv.db"
EURONEXT_URL = (
    "https://live.euronext.com/en/pd_es/data/track/download"
    "?mics=XPAR"
)
JUSTETF_PROFILE_URL = (
    "https://www.justetf.com/en/etf-profile.html?isin={isin}"
)
MAX_WORKERS = 3
RATE_LIMIT_BACKOFF = (5, 15, 45)
HTTP_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/128.0.0.0 Safari/537.36"
    ),
    "Accept-Language": "en-US,en;q=0.9",
}

# justETF répète souvent cette phrase en anglais sur la fiche.
_TER_PATTERNS = (
    re.compile(
        r"The ETF's TER \(total expense ratio\) amounts to\s*"
        r"([0-9]+(?:[.,][0-9]+)?)\s*%",
        re.I,
    ),
    re.compile(
        r"Total expense ratio[^0-9%]{0,400}?"
        r"([0-9]+(?:[.,][0-9]+)?)\s*%",
        re.I | re.S,
    ),
    re.compile(
        r">TER</(?:div|dt|th|span|td)>\s*<[^>]+>\s*"
        r"([0-9]+(?:[.,][0-9]+)?)\s*%",
        re.I | re.S,
    ),
)

_FOCUS_PATTERNS = (
    re.compile(
        r'data-testid="tl_etf-basics_value_investment-focus"\s*>\s*([^<]+)',
        re.I,
    ),
    re.compile(
        r"Investment focus</t[dh]>\s*<td[^>]*>\s*(?:<[^>]+>)*\s*([^<]+)",
        re.I,
    ),
)

# Première composante de « Investment focus » → libellé FR.
ASSET_CLASS_LABELS = {
    "equity": "Actions",
    "bonds": "Obligations",
    "bond": "Obligations",
    "precious metals": "Métaux précieux",
    "commodities": "Matières premières",
    "commodity": "Matières premières",
    "money market": "Marché monétaire",
    "cryptocurrencies": "Cryptomonnaies",
    "cryptocurrency": "Cryptomonnaies",
    "crypto": "Cryptomonnaies",
    "real estate": "Immobilier",
}

_db_lock = threading.Lock()


def fetch_euronext_etf_listing() -> list[tuple[str, str, str]]:
    """Retourne [(symbol, name, isin), ...] des ETF cotés à Paris."""
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

    listing: list[tuple[str, str, str]] = []
    seen: set[str] = set()
    header_seen = False
    for row in reader:
        if len(row) < 4:
            continue
        if not header_seen:
            header_seen = True
            continue
        name, isin, symbol, market = row[0], row[1], row[2], row[3]
        if not symbol or not isin:
            continue
        if "Paris" not in market:
            continue
        if symbol in seen:
            continue
        seen.add(symbol)
        listing.append((symbol, name, isin.strip().upper()))
    return listing


def _ter_from_raw(raw: object) -> float | None:
    try:
        value = float(str(raw).replace(",", "."))
    except (TypeError, ValueError):
        return None
    if not math.isfinite(value) or value <= 0:
        return None
    return value


def parse_yahoo_ter(info: dict) -> float | None:
    """Extrait le TER en % depuis `netExpenseRatio`. Yahoo envoie déjà
    un pourcentage (0.38 = 0,38 %). 0 est traité comme inconnu.
    """
    return _ter_from_raw(info.get("netExpenseRatio"))


def parse_justetf_ter(html: str) -> float | None:
    """Extrait le TER en % depuis le HTML (ou le texte) d'une fiche justETF."""
    if not html:
        return None
    for pattern in _TER_PATTERNS:
        match = pattern.search(html)
        if match:
            value = _ter_from_raw(match.group(1))
            if value is not None:
                return value
    return None


def fetch_yahoo_info(symbol: str) -> tuple[str | None, float | None]:
    """Retourne (longName, ter_pct) pour `<symbol>.PA`."""
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
            return None, None
        except Exception:
            raise
        else:
            name = info.get("longName") or info.get("shortName")
            if isinstance(name, str):
                name = name.strip() or None
            return name, parse_yahoo_ter(info)
    if last_exc is not None:
        raise last_exc
    return None, None


def parse_justetf_category(html: str) -> str | None:
    """Classe d'actifs FR depuis le champ justETF « Investment focus »."""
    if not html:
        return None
    raw = None
    for pattern in _FOCUS_PATTERNS:
        match = pattern.search(html)
        if match:
            raw = match.group(1).strip()
            break
    if not raw:
        return None
    first = raw.split(",")[0].strip()
    first = re.sub(r"\s+", " ", first)
    if not first:
        return None
    return ASSET_CLASS_LABELS.get(first.lower(), first)


def fetch_justetf_profile(
    isin: str,
) -> tuple[float | None, str | None]:
    """Retourne (ter, category) justETF, ou (None, None) si absent."""
    url = JUSTETF_PROFILE_URL.format(isin=isin)
    last_exc: Exception | None = None
    attempts = len(RATE_LIMIT_BACKOFF) + 1
    for attempt in range(attempts):
        try:
            req = urllib.request.Request(url, headers=HTTP_HEADERS)
            with urllib.request.urlopen(req, timeout=30) as response:
                html = response.read().decode("utf-8", errors="replace")
            return parse_justetf_ter(html), parse_justetf_category(html)
        except urllib.error.HTTPError as exc:
            if exc.code == 404:
                return None, None
            last_exc = exc
            if exc.code in (403, 429, 503) and attempt < len(RATE_LIMIT_BACKOFF):
                time.sleep(RATE_LIMIT_BACKOFF[attempt])
                continue
            raise
        except (TimeoutError, urllib.error.URLError) as exc:
            last_exc = exc
            if attempt < len(RATE_LIMIT_BACKOFF):
                time.sleep(RATE_LIMIT_BACKOFF[attempt])
                continue
            raise
    if last_exc is not None:
        raise last_exc
    return None, None


def ensure_schema(db: sqlite3.Connection) -> None:
    db.execute(
        "CREATE TABLE IF NOT EXISTS etf "
        "(id TEXT, name TEXT, isin TEXT, ter REAL, category TEXT)"
    )
    cols = {row[1] for row in db.execute("PRAGMA table_info(etf)")}
    if "ter" not in cols:
        db.execute("ALTER TABLE etf ADD COLUMN ter REAL")
    if "isin" not in cols:
        db.execute("ALTER TABLE etf ADD COLUMN isin TEXT")
    if "category" not in cols:
        db.execute("ALTER TABLE etf ADD COLUMN category TEXT")
    db.commit()


def upsert_etf(
    db: sqlite3.Connection,
    symbol: str,
    name: str,
    isin: str,
    ter: float | None,
    category: str | None,
) -> None:
    with _db_lock:
        db.execute("DELETE FROM etf WHERE id = ?", (symbol,))
        db.execute(
            "INSERT INTO etf (id, name, isin, ter, category) "
            "VALUES (?, ?, ?, ?, ?)",
            (symbol, name, isin, ter, category),
        )
        db.commit()


def process_etf(symbol: str, fallback_name: str, isin: str):
    yahoo_name: str | None = None
    yahoo_ter: float | None = None
    err: str | None = None
    try:
        yahoo_name, yahoo_ter = fetch_yahoo_info(symbol)
    except Exception as exc:
        err = f"{type(exc).__name__}: {exc}"

    justetf_ter: float | None = None
    category: str | None = None
    try:
        justetf_ter, category = fetch_justetf_profile(isin)
    except Exception:
        justetf_ter, category = None, None

    if justetf_ter is not None:
        ter, ter_source = justetf_ter, "justetf"
    elif yahoo_ter is not None:
        ter, ter_source = yahoo_ter, "yahoo"
    else:
        ter, ter_source = None, None

    return (
        symbol,
        (yahoo_name or fallback_name),
        isin,
        ter,
        category,
        yahoo_name is not None,
        ter_source,
        err,
    )


def process_justetf_row(etf_id: str, isin: str):
    try:
        ter, category = fetch_justetf_profile(isin)
    except Exception as exc:
        return etf_id, isin, None, None, f"{type(exc).__name__}: {exc}"
    return etf_id, isin, ter, category, None


def refresh_justetf_fields(db: sqlite3.Connection) -> None:
    """Met à jour TER et catégorie justETF sans retélécharger les noms Yahoo."""
    rows = db.execute(
        "SELECT id, isin FROM etf "
        "WHERE isin IS NOT NULL AND TRIM(isin) != ''"
    ).fetchall()
    print(f"{len(rows)} ETF à enrichir (TER + catégorie justETF)")
    with_ter = 0
    with_cat = 0
    errors = 0
    start = time.time()

    with ThreadPoolExecutor(max_workers=MAX_WORKERS) as pool:
        futures = {
            pool.submit(process_justetf_row, eid, isin): eid
            for eid, isin in rows
        }
        for i, fut in enumerate(as_completed(futures), 1):
            etf_id, isin, ter, category, err = fut.result()
            if err is not None:
                errors += 1
                print(f"[{i}/{len(rows)}] {etf_id} ERROR {err}")
                continue
            with _db_lock:
                if ter is not None:
                    db.execute(
                        "UPDATE etf SET ter = ? WHERE id = ?",
                        (ter, etf_id),
                    )
                if category is not None:
                    db.execute(
                        "UPDATE etf SET category = ? WHERE id = ?",
                        (category, etf_id),
                    )
                db.commit()
            if ter is not None:
                with_ter += 1
            if category is not None:
                with_cat += 1
            ter_txt = f" ter={ter:.4f}%" if ter is not None else " ter=—"
            cat_txt = f" {category}" if category else " cat=—"
            print(f"[{i}/{len(rows)}] {etf_id} {isin}{ter_txt}{cat_txt}")

    elapsed = time.time() - start
    print(
        f"\nTerminé en {elapsed:.1f}s : "
        f"{with_ter} TER, {with_cat} catégories, {errors} erreurs"
    )


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--justetf",
        action="store_true",
        help="Ne rafraîchit que TER et catégorie justETF (sans Yahoo).",
    )
    args = parser.parse_args()

    db = sqlite3.connect(DB_PATH, check_same_thread=False)
    try:
        ensure_schema(db)
        if args.justetf:
            refresh_justetf_fields(db)
            return

        print("Téléchargement de la liste ETF Euronext (XPAR)…")
        listing = fetch_euronext_etf_listing()
        print(f"{len(listing)} ETF à traiter (noms Yahoo + TER/catégorie justETF)")

        ok = 0
        fallback = 0
        with_ter = 0
        with_cat = 0
        from_justetf = 0
        from_yahoo_ter = 0
        errors = 0
        start = time.time()

        with ThreadPoolExecutor(max_workers=MAX_WORKERS) as pool:
            futures = {
                pool.submit(process_etf, sym, nm, isin): sym
                for sym, nm, isin in listing
            }
            for i, fut in enumerate(as_completed(futures), 1):
                (
                    symbol,
                    name,
                    isin,
                    ter,
                    category,
                    from_yahoo,
                    ter_source,
                    err,
                ) = fut.result()
                upsert_etf(db, symbol, name, isin, ter, category)

                if err is not None:
                    errors += 1
                    print(f"[{i}/{len(listing)}] {symbol} ERROR {err}")
                    continue
                if ter is not None:
                    with_ter += 1
                    if ter_source == "justetf":
                        from_justetf += 1
                    elif ter_source == "yahoo":
                        from_yahoo_ter += 1
                if category:
                    with_cat += 1
                src = f" {ter_source}" if ter_source else ""
                ter_txt = (
                    f" ter={ter:.4f}%{src}" if ter is not None else " ter=—"
                )
                cat_txt = f" {category}" if category else ""
                if from_yahoo:
                    ok += 1
                    print(
                        f"[{i}/{len(listing)}] {symbol} {name}{ter_txt}{cat_txt}"
                    )
                else:
                    fallback += 1
                    print(
                        f"[{i}/{len(listing)}] {symbol} "
                        f"(nom Euronext) {name}{ter_txt}{cat_txt}"
                    )

        listed_ids = [sym for sym, _, _ in listing]
        placeholders = ",".join("?" * len(listed_ids))
        if listed_ids:
            db.execute(
                f"DELETE FROM etf WHERE id NOT IN ({placeholders})",
                listed_ids,
            )
            db.commit()

        elapsed = time.time() - start
        print(
            f"\nTerminé en {elapsed:.1f}s : "
            f"{ok} Yahoo, {fallback} repli Euronext, "
            f"{with_ter} TER ({from_justetf} justETF, "
            f"{from_yahoo_ter} Yahoo), {with_cat} catégories, "
            f"{errors} erreurs"
        )
    finally:
        db.close()


if __name__ == "__main__":
    main()
