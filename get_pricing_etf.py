"""Récupère le dernier prix connu des ETF de la table `etf` via Yahoo
Finance et met à jour la table `pricingETF`.

Source : yfinance (`Ticker.history`). Le ticker Yahoo est construit en
suffixant le mnémo Euronext par ".PA" (ex. "B28A" -> "B28A.PA"), comme
`get_pricing.py` pour les actions.

Table cible :
    CREATE TABLE pricingETF (
        id TEXT,
        date TEXT,
        price REAL,
        rsi REAL
    )
- id    : symbole Euronext (= etf.id)
- date  : date du dernier prix connu, au format YYYY-MM-DD
- price : dernier cours de clôture connu
- rsi   : RSI(14) journalier calculé sur les clôtures de l'historique
          récent, méthode de Wilder (identique à get_pricing.py).
          NULL si moins de 15 clôtures ou si la moyenne des pertes
          vaut 0.

La colonne `rsi` est ajoutée automatiquement à la table existante via
ALTER TABLE si elle est absente (idempotent).

Hors séance, Yahoo laisse souvent la dernière ligne d'historique avec
un Close vide. On complète alors avec le dernier cours coté
(`fast_info.last_price`), typiquement la clôture de la veille.

Le script est idempotent et reprenable : pour chaque ETF on écrase
uniquement la ligne (id, date) — les autres dates déjà présentes sont
conservées.
"""

import sqlite3
import threading
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import date, timedelta

import numpy as np
import pandas as pd
import yfinance as yf
from yfinance.exceptions import YFRateLimitError


DB_PATH = "/home/aurelien/dev/div/inv/inv.db"
MAX_WORKERS = 3
RATE_LIMIT_BACKOFF = (5, 15, 45)

# 6 mois pour stabiliser le RSI(14), comme get_pricing.py.
HISTORY_PERIOD = "6mo"
HISTORY_FALLBACK_DAYS = 200
RSI_PERIOD = 14

_db_lock = threading.Lock()


def compute_rsi(close: "pd.Series", period: int = RSI_PERIOD) -> float | None:
    """Dernier RSI(`period`) journalier (Wilder), ou None si
    l'historique est trop court ou si le calcul est dégénéré.
    """
    if close is None or len(close) < period + 1:
        return None

    delta = close.diff().to_numpy()[1:]
    if len(delta) < period:
        return None

    gain = np.where(delta > 0, delta, 0.0)
    loss = np.where(delta < 0, -delta, 0.0)

    avg_gain = float(gain[:period].mean())
    avg_loss = float(loss[:period].mean())

    for i in range(period, len(gain)):
        avg_gain = (avg_gain * (period - 1) + gain[i]) / period
        avg_loss = (avg_loss * (period - 1) + loss[i]) / period

    if avg_loss == 0:
        return None

    rs = avg_gain / avg_loss
    try:
        return float(100 - 100 / (1 + rs))
    except (TypeError, ValueError, ZeroDivisionError):
        return None


def _as_price(value) -> float | None:
    try:
        if value is None or pd.isna(value):
            return None
        price = float(value)
    except (TypeError, ValueError):
        return None
    if price <= 0:
        return None
    return price


def _index_to_iso(ts) -> str | None:
    try:
        return ts.strftime("%Y-%m-%d")
    except AttributeError:
        try:
            return pd.to_datetime(ts).strftime("%Y-%m-%d")
        except (ValueError, TypeError):
            return None


def _inferred_last_session_date() -> str:
    d = date.today() - timedelta(days=1)
    while d.weekday() >= 5:
        d -= timedelta(days=1)
    return d.isoformat()


def _quote_last_price(yticker: yf.Ticker) -> float | None:
    try:
        info = yticker.fast_info
    except Exception:
        return None
    for attr in (
        "last_price",
        "regular_market_previous_close",
        "previous_close",
    ):
        try:
            price = _as_price(getattr(info, attr, None))
        except Exception:
            continue
        if price is not None:
            return price
    return None


def _download_history(yticker: yf.Ticker) -> pd.DataFrame | None:
    df = yticker.history(period=HISTORY_PERIOD)
    if df is not None and not df.empty and "Close" in df.columns:
        return df
    end = date.today() + timedelta(days=1)
    start = date.today() - timedelta(days=HISTORY_FALLBACK_DAYS)
    df = yticker.history(start=start.isoformat(), end=end.isoformat())
    if df is None or df.empty or "Close" not in df.columns:
        return None
    return df


def fetch_last_price(
    ticker: str,
) -> tuple[str, float, float | None] | None:
    """Retourne (date_iso, price, rsi) du dernier cours de clôture
    connu. Le RSI peut être None si l'historique est trop court.
    Gère le rate-limit avec backoff. Renvoie None si Yahoo n'a ni
    historique ni cotation. Lève l'exception sur autre erreur après
    les retries.

    Hors séance, complète une dernière bougie Close=NaN avec le
    dernier cours coté (typiquement la veille).
    """
    last_exc: Exception | None = None
    attempts = len(RATE_LIMIT_BACKOFF) + 1
    yticker = yf.Ticker(ticker)
    for attempt in range(attempts):
        try:
            df = _download_history(yticker)
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
            quote_price: float | None = None

            if df is not None and not df.empty:
                last_close = df["Close"].iloc[-1]
                if pd.isna(last_close):
                    quote_price = _quote_last_price(yticker)
                    if quote_price is not None:
                        df = df.copy()
                        df.loc[df.index[-1], "Close"] = quote_price

                close = df["Close"].dropna()
                if not close.empty:
                    date_iso = _index_to_iso(close.index[-1])
                    price = _as_price(close.iloc[-1])
                    if date_iso is not None and price is not None:
                        return date_iso, price, compute_rsi(close)

            if quote_price is None:
                quote_price = _quote_last_price(yticker)
            if quote_price is None:
                return None

            if df is not None and not df.empty:
                date_iso = _index_to_iso(df.index[-1])
                if date_iso is not None:
                    return date_iso, quote_price, None
            return _inferred_last_session_date(), quote_price, None

    if last_exc is not None:
        raise last_exc
    return None


def ensure_schema(db: sqlite3.Connection) -> None:
    tables = {
        row[0]
        for row in db.execute(
            "SELECT name FROM sqlite_master WHERE type = 'table'"
        )
    }
    if "pricing_etf" in tables and "pricingETF" not in tables:
        db.execute("ALTER TABLE pricing_etf RENAME TO pricingETF")
    db.execute(
        "CREATE TABLE IF NOT EXISTS pricingETF ("
        "id TEXT, date TEXT, price REAL, rsi REAL)"
    )
    cols = {row[1] for row in db.execute("PRAGMA table_info(pricingETF)")}
    if "rsi" not in cols:
        db.execute("ALTER TABLE pricingETF ADD COLUMN rsi REAL")
    db.commit()


def upsert_pricing(
    db: sqlite3.Connection,
    etf_id: str,
    date_iso: str,
    price: float,
    rsi: float | None,
) -> None:
    with _db_lock:
        db.execute(
            "DELETE FROM pricingETF WHERE id = ? AND date = ?",
            (etf_id, date_iso),
        )
        db.execute(
            "INSERT INTO pricingETF (id, date, price, rsi) "
            "VALUES (?, ?, ?, ?)",
            (etf_id, date_iso, price, rsi),
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

                date_iso, price, rsi = result
                upsert_pricing(db, etf_id, date_iso, price, rsi)
                ok += 1
                print(
                    f"[{i}/{len(rows)}] {ticker} {date_iso} "
                    f"price={price:.4f} "
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
